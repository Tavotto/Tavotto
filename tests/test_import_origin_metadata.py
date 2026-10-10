"""Import Origin Resolver PR2：发行包元数据映射（`distmeta`）——O-02 / O-10 / O-11 / O-19 / O-20 与 P7.2 的元数据半边。

判据的主语：**给定一个环境前缀（tmp_path 里造的真实 dist-info 目录树），不起解释器，静态读出的 import 名 →
发行包结论**。每条都是真文件树 + 真读取；零执行用「会在被执行时留下痕迹」的 `.pth` / finder / `sitecustomize` /
包 `__init__`，读完核痕迹文件不存在，再把起进程 / 联网 / `importlib.metadata` / `find_spec` / `__import__`
（针对用户模块名）换成调用即失败的桩。供应链半边：已安装元数据不产出任何安装授权。
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import depresolve, distmeta, importscan, scanbudget

SP_REL = "lib/python3.12/site-packages"
USER_NAMES = {"evilpkg", "sitecustomize", "usercustomize", "__editable___evil_finder", "mylab"}


# ---------------------------------------------------------------- 造环境


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _env(tmp_path: Path, name: str = "venv") -> tuple[Path, Path]:
    prefix = tmp_path / name
    site = prefix / SP_REL
    site.mkdir(parents=True)
    _write(prefix / "pyvenv.cfg", "home = /usr/bin\nversion = 3.12.1\n")
    return prefix, site


def _dist(
    site: Path,
    name: str,
    version: str,
    *,
    top: str | None = None,
    record: list[str] | None = None,
    direct_url: dict | None = None,
    installer: str | None = None,
    metadata: bool = True,
    summary: str = "A package",
) -> Path:
    """一个标准 wheel 安装产生的 `<name>.dist-info`。`top` / `record` 为 None 就不写那个文件（O-19）。"""
    d = site / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True)
    if metadata:
        _write(
            d / "METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nSummary: {summary}\n\nLONG BODY\n",
        )
    if top is not None:
        _write(d / "top_level.txt", top)
    if record is not None:
        rows = [f"{p},sha256=abc,10" for p in record] + [f"{d.name}/METADATA,,"]
        _write(d / "RECORD", "\n".join(rows) + "\n")
    if direct_url is not None:
        _write(d / "direct_url.json", json.dumps(direct_url))
    if installer is not None:
        _write(d / "INSTALLER", installer + "\n")
    return d


def _index(prefix: Path, **kw) -> distmeta.Index:
    return distmeta.index_environment(prefix, **kw)


def _scan(
    tmp_path: Path, imports: str, index, *, declared=None
) -> dict[str, importscan.ImportClass]:
    proj = tmp_path / "proj"
    _write(proj / "s.py", imports)
    res = importscan.scan(proj, "s.py", declared=declared or {}, dists=index)
    return {c.module: c for c in res.classes}


# ---------------------------------------------------------------- 布局


class TestLayout:
    def test_posix_venv_layout_is_derived_from_the_prefix_without_an_interpreter(self, tmp_path):
        prefix, site = _env(tmp_path)
        roots = distmeta.site_packages(prefix)
        assert [(r.rel, r.layer) for r in roots] == [(SP_REL, "env")]
        assert Path(roots[0].path) == site

    def test_windows_layout(self, tmp_path):
        prefix = tmp_path / "w"
        (prefix / "Lib" / "site-packages").mkdir(parents=True)
        roots = distmeta.site_packages(prefix)
        assert [r.rel for r in roots] == ["Lib/site-packages"]

    def test_only_the_python_dir_named_by_pyvenv_cfg_is_a_root(self, tmp_path):
        prefix, _ = _env(tmp_path)
        (prefix / "lib" / "python3.13t" / "site-packages").mkdir(parents=True)
        (prefix / "lib" / "python3.11" / "site-packages").mkdir(parents=True)
        (prefix / "lib" / "notpython" / "site-packages").mkdir(parents=True)
        assert [r.rel for r in distmeta.site_packages(prefix)] == [SP_REL]  # version = 3.12.1

    def test_free_threaded_dir_of_the_declared_version_is_accepted(self, tmp_path):
        prefix, _ = _env(tmp_path)
        (prefix / "lib" / "python3.12t" / "site-packages").mkdir(parents=True)
        rels = {r.rel for r in distmeta.site_packages(prefix)}
        assert rels == {SP_REL, "lib/python3.12t/site-packages"}

    def test_a_leftover_python_dir_of_another_version_is_not_installed_evidence(self, tmp_path):
        prefix, _ = _env(tmp_path)  # pyvenv.cfg: 3.12.1
        stale = prefix / "lib" / "python3.11" / "site-packages"
        _dist(stale, "oldpkg", "1.0", top="oldpkg\n", record=["oldpkg/__init__.py"])
        got = _scan(tmp_path, "import oldpkg\n", _index(prefix))["oldpkg"]
        assert got.distribution_status != "installed_confirmed"
        assert got.distribution_candidates == ()

    def test_unknown_python_version_with_several_candidate_dirs_is_incomplete(self, tmp_path):
        prefix, _ = _env(tmp_path)
        _write(prefix / "pyvenv.cfg", "home = /usr/bin\n")  # 没有 version
        stale = prefix / "lib" / "python3.11" / "site-packages"
        _dist(stale, "oldpkg", "1.0", top="oldpkg\n", record=["oldpkg/__init__.py"])
        idx = _index(prefix)
        assert not idx.complete
        got = _scan(tmp_path, "import oldpkg\n", idx)["oldpkg"]
        assert got.distribution_status == "environment_not_checked"

    def test_unknown_python_version_with_one_candidate_dir_is_still_read(self, tmp_path):
        prefix, site = _env(tmp_path)
        _write(prefix / "pyvenv.cfg", "home = /usr/bin\n")
        _dist(site, "pkg", "1.0", top="pkg\n", record=["pkg/__init__.py"])
        got = _scan(tmp_path, "import pkg\n", _index(prefix))["pkg"]
        assert got.distribution_status == "installed_confirmed"

    def test_a_prefix_without_site_packages_was_not_checked_rather_than_empty(self, tmp_path):
        (tmp_path / "bare").mkdir()
        idx = _index(tmp_path / "bare")
        assert idx.checked is False and idx.complete is False
        got = _scan(tmp_path, "import docx\n", idx)["docx"]
        assert got.distribution_status == "environment_not_checked"
        assert "metadata_scan_incomplete" in got.compatibility

    def test_base_layer_only_when_asked_and_declared_by_pyvenv_cfg(self, tmp_path):
        base = tmp_path / "base"
        (base / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, _ = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg",
            f"home = {base / 'bin'}\ninclude-system-site-packages = true\nversion = 3.12.1\n",
        )
        assert [r.layer for r in distmeta.site_packages(prefix)] == ["env"]
        roots = distmeta.site_packages(prefix, include_base=True)
        assert [r.layer for r in roots] == ["env", "base"]
        assert all(str(tmp_path) not in r.rel for r in roots)  # 公开相对路径里没有绝对路径
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = false\n"
        )
        assert [r.layer for r in distmeta.site_packages(prefix, include_base=True)] == ["env"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
    def test_a_project_env_behind_a_symlink_is_refused_before_anything_is_touched(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        outside, _ = _env(tmp_path, "outside")
        (proj / ".venv").symlink_to(outside, target_is_directory=True)
        idx = _index(proj / ".venv", project_root=proj)
        assert idx.checked is False
        assert {
            "code": "symlinked_dir",
            "severity": "partial",
            "scope": "env",
            "path": ".venv",
            "count": 1,
        } in list(idx.issues)


# ---------------------------------------------------------------- O-02 及改名包


class TestInstalledEvidenceMapsImportNames:
    @pytest.mark.parametrize(
        ("imp", "dist", "version", "top", "record"),
        [
            ("docx", "python-docx", "1.1.0", "docx\n", None),  # O-02：top_level.txt 证明
            (
                "docx",
                "python-docx",
                "1.1.0",
                None,
                ["docx/__init__.py", "docx/api.py"],
            ),  # RECORD 证明
            (
                "PIL",
                "Pillow",
                "10.4.0",
                None,
                ["PIL/__init__.py", "PIL/Image.py", "PIL/_imaging.cpython-312-darwin.so"],
            ),
            ("yaml", "PyYAML", "6.0.2", "_yaml\nyaml\n", None),
            ("sklearn", "scikit-learn", "1.5.1", "sklearn\n", ["sklearn/__init__.py"]),
            ("skimage", "scikit-image", "0.24.0", None, ["skimage/__init__.py"]),
        ],
    )
    def test_the_distribution_is_proved_by_files_and_the_evidence_file_is_named(
        self, tmp_path, imp, dist, version, top, record
    ):
        prefix, site = _env(tmp_path)
        d = _dist(site, dist, version, top=top, record=record)
        got = _scan(tmp_path, f"import {imp}\n", _index(prefix))[imp]
        assert got.selected_distribution == dist
        assert got.observed_distribution == dist
        assert got.observed_version == version
        assert got.distribution_status == "installed_confirmed"
        assert (got.origin_kind, got.resolution_status) == ("site-packages", "resolved")
        assert got.distribution_provenance == "index"
        [cand] = got.distribution_candidates
        assert cand["source"] == "installed" and cand["reproducible"] is True
        assert cand["evidence_file"] == f"{SP_REL}/{d.name}"
        assert set(cand["evidence"]) <= distmeta.EVIDENCE_CODES
        assert ("installed_top_level_txt" in cand["evidence"]) == (top is not None)
        assert ("installed_record_files" in cand["evidence"]) == (record is not None)
        # 只观测：bucket / needed / distribution 口径与没有索引时完全一样
        base = _scan(tmp_path, f"import {imp}\n", None)[imp]
        assert (got.bucket, got.needed, got.distribution) == (
            base.bucket,
            base.needed,
            base.distribution,
        )

    def test_installed_evidence_wins_over_the_curated_table(self, tmp_path):
        """cv2 的 curated 是 opencv-python，环境里实际装的是 headless：以已装为准，并报告与 curated 不同。"""
        prefix, site = _env(tmp_path)
        _dist(site, "opencv-python-headless", "4.10.0", top="cv2\n")
        got = _scan(tmp_path, "import cv2\n", _index(prefix))["cv2"]
        assert got.observed_distribution == "opencv-python-headless"
        assert got.selected_distribution == "opencv-python-headless"
        assert got.distribution == "opencv-python"  # 旧字段不动（PR4 才决定消费）
        assert "curated_dist_differs_from_observed" in got.compatibility
        assert got.distribution_status == "installed_confirmed"

    def test_installed_evidence_maps_a_name_no_table_knows_but_never_changes_the_bucket(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "weird-dist-name", "2.0", top="weirdmod\n")
        got = _scan(tmp_path, "import weirdmod\n", _index(prefix))["weirdmod"]
        assert got.observed_distribution == "weird-dist-name"
        assert got.bucket == "unknown"  # 观测期：不因为元数据改变桶 / needed
        assert got.needed is False
        assert got.distribution == ""

    def test_top_level_names_match_exactly_case_included(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "Pillow", "10.4.0", top="PIL\n")
        got = _scan(tmp_path, "import pil\n", _index(prefix))["pil"]
        assert got.distribution_candidates == ()
        assert got.distribution_status == "not_installed"

    def test_record_paths_outside_site_packages_and_data_dirs_are_not_import_names(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "tool",
            "1.0",
            record=[
                "../../../bin/tool",
                "tool-1.0.data/scripts/x",
                "__pycache__/a.pyc",
                "real/__init__.py",
                "mod.py",
            ],
        )
        idx = _index(prefix)
        assert [c.distribution for c in idx.lookup("real").candidates] == ["tool"]
        assert [c.distribution for c in idx.lookup("mod").candidates] == ["tool"]
        for junk in ("bin", "tool", "__pycache__", "scripts", "..", ""):
            assert idx.lookup(junk).candidates == ()


# ---------------------------------------------------------------- O-10 editable 与各种不可重现来源


class TestEditableAndNonReproducibleSources:
    def test_o10_direct_url_editable(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "mylab",
            "0.1.0",
            top="mylab\n",
            direct_url={"url": "file:///somewhere/mylab", "dir_info": {"editable": True}},
        )
        got = _scan(tmp_path, "import mylab\n", _index(prefix))["mylab"]
        assert got.distribution_status == "editable_dependency_not_reproducible"
        assert (got.origin_kind, got.resolution_status) == ("editable", "unsupported")
        assert got.distribution_provenance == "editable"
        assert got.observed_distribution == "mylab" and got.observed_version == "0.1.0"
        assert got.selected_distribution == ""  # 绝不作为可重现的 requirement 来源
        assert [c["reproducible"] for c in got.distribution_candidates] == [False]
        assert got.distribution == ""  # 本地 editable 包没有被映射成同名 PyPI 包

    def test_a_local_editable_fork_of_a_curated_name_is_still_not_that_pypi_package(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "python-docx",
            "9.9.9",
            top="docx\n",
            direct_url={"url": "file:///home/me/my-docx-fork", "dir_info": {"editable": True}},
        )
        got = _scan(tmp_path, "import docx\n", _index(prefix))["docx"]
        assert got.distribution_status == "editable_dependency_not_reproducible"
        assert got.selected_distribution == ""
        assert all(c["reproducible"] is False for c in got.distribution_candidates)
        # curated 的 PyPI 名与已装的 editable 是同一个键：只出一行，来源是 installed，不是 curated
        assert [(c["distribution"], c["source"]) for c in got.distribution_candidates] == [
            ("python-docx", "installed")
        ]

    def test_o10_setuptools_editable_finder_pth_maps_modules_without_running_anything(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        mark = tmp_path / "SIDE_EFFECT_finder"
        _write(
            site / "__editable__.mylab-0.1.0.pth",
            "import __editable___mylab_0_1_0_finder; __editable___mylab_0_1_0_finder.install()\n",
        )
        _write(
            site / "__editable___mylab_0_1_0_finder.py",
            f"open({str(mark)!r}, 'w').write('x')\n"
            "MAPPING: dict[str, str] = {'mylab': '/work/mylab/src/mylab', 'mylab_extra': '/work/x'}\n"
            "NAMESPACES = {}\n",
        )
        _dist(
            site,
            "mylab",
            "0.1.0",
            direct_url={"url": "file:///work/mylab", "dir_info": {"editable": True}},
        )
        idx = _index(prefix)
        got = _scan(tmp_path, "import mylab\nimport mylab_extra\n", idx)
        for name in ("mylab", "mylab_extra"):
            assert got[name].distribution_status == "editable_dependency_not_reproducible"
            assert got[name].distribution_candidates[0]["evidence"] == ["installed_editable_finder"]
        assert not mark.exists()  # finder 源码只被 ast 解析，没被 import / exec
        assert idx.uncovered_paths == 0

    def test_a_finder_pth_without_any_dist_info_is_still_recognised_as_editable(self, tmp_path):
        prefix, site = _env(tmp_path)
        _write(
            site / "__editable__.solo-2.0.pth",
            "import __editable___solo_2_0_finder; __editable___solo_2_0_finder.install()\n",
        )
        _write(site / "__editable___solo_2_0_finder.py", "MAPPING = {'solo': '/w/solo'}\n")
        got = _scan(tmp_path, "import solo\n", _index(prefix))["solo"]
        assert got.distribution_status == "editable_dependency_not_reproducible"
        assert got.observed_distribution == "solo" and got.selected_distribution == ""

    def test_path_style_pth_lines_are_counted_never_followed_and_make_not_found_honest(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        outside = tmp_path / "elsewhere" / "src"
        outside.mkdir(parents=True)
        _write(site / "legacy.pth", f"{outside}\n# comment\n")
        idx = _index(prefix)
        assert idx.uncovered_paths == 1
        got = _scan(tmp_path, "import zzz\n", idx)["zzz"]
        # 路径行指向的目录没读：不能宣称「没装」
        assert got.distribution_status == "environment_not_checked"
        assert "path_entries_not_followed" in got.compatibility

    def test_egg_link_is_editable_by_filename_alone(self, tmp_path):
        prefix, site = _env(tmp_path)
        _write(site / "oldlab.egg-link", "/some/where/oldlab\n.\n")
        got = _scan(tmp_path, "import oldlab\n", _index(prefix))["oldlab"]
        assert got.distribution_status == "editable_dependency_not_reproducible"
        assert got.selected_distribution == ""

    @pytest.mark.parametrize(
        ("direct_url", "provenance"),
        [
            ({"url": "file:///home/me/pkg", "dir_info": {}}, "local_path"),
            (
                {
                    "url": "file:///home/me/pkg-1.0-py3-none-any.whl",
                    "archive_info": {"hash": "sha256=ab"},
                },
                "local_archive",
            ),
            ({"url": "https://example.invalid/pkg.zip", "archive_info": {}}, "url"),
            (
                {
                    "url": "https://example.invalid/pkg.git",
                    "vcs_info": {"vcs": "git", "commit_id": "abc"},
                },
                "vcs",
            ),
        ],
    )
    def test_local_path_wheel_vcs_and_url_installs_are_evidence_not_a_requirement(
        self, tmp_path, direct_url, provenance
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "labtool", "1.2", top="labtool\n", direct_url=direct_url)
        got = _scan(tmp_path, "import labtool\n", _index(prefix))["labtool"]
        assert got.distribution_provenance == provenance
        assert got.distribution_status == "installed_source_not_reproducible"
        assert got.resolution_status == "unsupported"
        assert got.selected_distribution == ""
        assert got.observed_distribution == "labtool"
        assert [c["reproducible"] for c in got.distribution_candidates] == [False]

    def test_an_unreadable_direct_url_is_treated_as_not_reproducible(self, tmp_path):
        prefix, site = _env(tmp_path)
        d = _dist(site, "labtool", "1.2", top="labtool\n")
        _write(d / "direct_url.json", "{ not json")
        got = _scan(tmp_path, "import labtool\n", _index(prefix))["labtool"]
        assert got.distribution_provenance == "url"
        assert got.selected_distribution == ""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink / chmod")
    @pytest.mark.parametrize("how", ["symlink", "oversize", "placeholder", "unreadable", "fifo"])
    def test_a_direct_url_that_exists_but_cannot_be_read_is_never_taken_for_an_index_install(
        self, tmp_path, monkeypatch, how
    ):
        if how == "fifo" and not hasattr(os, "mkfifo"):
            pytest.skip("needs mkfifo")
        prefix, site = _env(tmp_path)
        d = _dist(site, "labtool", "1.2", top="labtool\n")
        du = d / "direct_url.json"
        payload = json.dumps({"url": "file:///x", "dir_info": {"editable": True}})
        if how == "symlink":
            real = _write(tmp_path / "real.json", payload)
            du.symlink_to(real)
        elif how == "oversize":
            _write(du, payload + " " * (distmeta.MAX_SMALL_BYTES + 10))
        elif how == "placeholder":
            _write(du, payload)
            real_is_ph = scanbudget.is_placeholder
            monkeypatch.setattr(
                scanbudget,
                "is_placeholder",
                lambda st: real_is_ph(st) or st.st_size == len(payload),
            )
        elif how == "unreadable":
            _write(du, payload)
            du.chmod(0)
            if os.access(du, os.R_OK):
                pytest.skip("running as a user that ignores file modes")
        else:
            os.mkfifo(du)
        try:
            idx = _index(prefix)
        finally:
            if how == "unreadable":
                du.chmod(0o644)
        got = _scan(tmp_path, "import labtool\n", idx)["labtool"]
        assert got.distribution_provenance != "index"
        assert [c["reproducible"] for c in got.distribution_candidates] == [False]
        assert got.selected_distribution == ""
        assert got.distribution_status == "installed_source_not_reproducible"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink / chmod")
    @pytest.mark.parametrize("how", ["symlink", "oversize", "placeholder", "unreadable"])
    def test_a_pth_the_scan_could_not_read_makes_the_environment_not_checked_not_not_installed(
        self, tmp_path, monkeypatch, how
    ):
        prefix, site = _env(tmp_path)
        outside = tmp_path / "elsewhere"
        _write(outside / "viapth.py", "X = 1\n")
        line = f"{outside}\n"
        pth = site / "extra.pth"
        if how == "symlink":
            real = _write(tmp_path / "real.pth", line)
            pth.symlink_to(real)
        elif how == "oversize":
            _write(pth, line + "#" * (distmeta.MAX_SMALL_BYTES + 10) + "\n")
        elif how == "placeholder":
            _write(pth, line)
            real_is_ph = scanbudget.is_placeholder
            monkeypatch.setattr(
                scanbudget,
                "is_placeholder",
                lambda st: real_is_ph(st) or st.st_size == len(line),
            )
        else:
            _write(pth, line)
            pth.chmod(0)
            if os.access(pth, os.R_OK):
                pytest.skip("running as a user that ignores file modes")
        try:
            idx = _index(prefix)
        finally:
            if how == "unreadable":
                pth.chmod(0o644)
        if how != "unreadable":
            # 真解释器（站点目录用 addsitedir 处理）能经这个 `.pth` 导入 `viapth`——所以「没装」是错的
            code = "import site, sys; site.addsitedir(sys.argv[1]); import viapth"
            subprocess.run(
                [sys.executable, "-I", "-S", "-c", code, str(site)], check=True, capture_output=True
            )
        assert not idx.complete
        got = _scan(tmp_path, "import viapth\n", idx)["viapth"]
        assert got.distribution_status == "environment_not_checked"
        assert "metadata_scan_incomplete" in got.compatibility

    def test_a_readable_pth_alone_does_not_make_the_scan_incomplete(self, tmp_path):
        prefix, site = _env(tmp_path)
        _write(site / "ok.pth", "import sys\n")
        idx = _index(prefix)
        assert idx.complete
        assert idx.uncovered_paths == 1  # 没建模的 import 行：计数，不假装看过

    @pytest.mark.parametrize(
        "line",
        [
            "import sys; sys.path.insert(0, {outside!r})",
            "import mypkg_hook",
            "import __editable___x_finder; import sys; sys.path.insert(0, {outside!r})",
            "import __editable___x_finder; __editable___x_finder.install(); import sys",
        ],
    )
    def test_an_unmodelled_import_line_in_a_pth_makes_unfound_modules_not_checked(
        self, tmp_path, line
    ):
        prefix, site = _env(tmp_path)
        outside = tmp_path / "elsewhere"
        _write(outside / "viahook.py", "X = 1\n")
        _write(site / "hook.pth", line.format(outside=str(outside)) + "\n")
        if line.startswith("import sys; sys.path"):
            # 真解释器：Python 执行这一行，目录外的模块因此可导入
            code = "import site, sys; site.addsitedir(sys.argv[1]); import viahook"
            subprocess.run(
                [sys.executable, "-I", "-S", "-c", code, str(site)], check=True, capture_output=True
            )
        idx = _index(prefix)
        assert idx.uncovered_paths == 1
        got = _scan(tmp_path, "import viahook\n", idx)["viahook"]
        assert got.distribution_status == "environment_not_checked"
        assert "path_entries_not_followed" in got.compatibility

    def test_the_modelled_editable_finder_line_is_still_covered(self, tmp_path):
        prefix, site = _env(tmp_path)
        _write(
            site / "__editable__.solo-2.0.pth",
            "import __editable___solo_2_0_finder; __editable___solo_2_0_finder.install()\n",
        )
        _write(site / "__editable___solo_2_0_finder.py", "MAPPING = {'solomod': '/x/solomod'}\n")
        idx = _index(prefix)
        assert idx.uncovered_paths == 0
        got = _scan(tmp_path, "import solomod\nimport nothere\n", idx)
        assert got["solomod"].distribution_status == "editable_dependency_not_reproducible"
        assert got["nothere"].distribution_status == "not_installed"

    def test_a_direct_url_that_does_not_exist_is_still_an_index_install(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "plain", "1.0", top="plain\n")
        got = _scan(tmp_path, "import plain\n", _index(prefix))["plain"]
        assert got.distribution_provenance == "index" and got.selected_distribution == "plain"


# ---------------------------------------------------------------- Conda


class TestCondaSpecific:
    def _conda(self, tmp_path):
        prefix, site = _env(tmp_path, "conda")
        meta = prefix / "conda-meta"
        _write(
            meta / "condaonly-1.0-h1_0.json",
            json.dumps(
                {
                    "name": "condaonly",
                    "version": "1.0",
                    "files": [
                        f"{SP_REL}/condamod/__init__.py",
                        f"{SP_REL}/condamod/core.py",
                        "bin/condatool",
                    ],
                }
            ),
        )
        return prefix, site

    def test_a_conda_only_package_is_evidence_and_never_a_pypi_name(self, tmp_path):
        prefix, _ = self._conda(tmp_path)
        got = _scan(tmp_path, "import condamod\n", _index(prefix))["condamod"]
        assert got.distribution_status == "conda_package_not_pypi"
        assert got.resolution_status == "unsupported"
        assert got.selected_distribution == "" and got.observed_distribution == ""
        [cand] = got.distribution_candidates
        assert (cand["ecosystem"], cand["provenance"], cand["reproducible"]) == (
            "conda",
            "conda",
            False,
        )
        assert "installed_conda_meta_files" in cand["evidence"]

    def test_conda_evidence_file_is_relative_to_the_prefix_not_to_site_packages(self, tmp_path):
        prefix, _ = self._conda(tmp_path)
        got = _scan(tmp_path, "import condamod\n", _index(prefix))["condamod"]
        [cand] = got.distribution_candidates
        assert cand["evidence_file"] == "conda-meta/condaonly-1.0-h1_0.json"
        assert str(tmp_path) not in cand["evidence_file"]

    def test_conda_meta_defers_to_a_pip_visible_dist_info_for_the_same_module(self, tmp_path):
        prefix, site = self._conda(tmp_path)
        _dist(site, "CondaMod", "1.0", top="condamod\n", installer="conda")
        got = _scan(tmp_path, "import condamod\n", _index(prefix))["condamod"]
        assert len(got.distribution_candidates) == 1
        assert got.distribution_provenance == "conda"
        assert got.selected_distribution == ""  # conda 装的：不许 pip 再装一份
        assert got.observed_distribution == "CondaMod"
        assert "installed_by_conda_not_pip" in got.compatibility

    def test_oversized_conda_meta_is_noted_not_read(self, tmp_path):
        prefix, _ = self._conda(tmp_path)
        idx = distmeta.index_environment(
            prefix, budget=scanbudget.Budget(limits=scanbudget.Limits(max_file_bytes=20))
        )
        assert any(i["code"] == "file_too_large" for i in idx.issues)


# ---------------------------------------------------------------- 拒读传播（读不了 ≠ 没有）


_EDITABLE_URL = {"url": "file:///src/x", "dir_info": {"editable": True}}


class TestRefusalNeverBecomesAbsence:
    """`distmeta` 里每一处「在那儿但没读成」都经 `_Reader.read_state` / `_Reader.refuse` 把环境记为没读全；
    这里一处一条用例（清单见 PR 正文「拒读传播全量排查」）。"""

    @staticmethod
    def _incomplete(tmp_path, idx, module):
        assert not idx.complete
        got = _scan(tmp_path, f"import {module}\n", idx)[module]
        assert got.distribution_status == "environment_not_checked"

    def _with_base(self, tmp_path, cfg_how):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        _dist(base_site, "basedist", "1.0", top="basemod\n", record=["basemod/__init__.py"])
        prefix, _ = _env(tmp_path)
        text = f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        cfg = prefix / "pyvenv.cfg"
        cfg.unlink()
        if cfg_how == "oversize":
            _write(cfg, text + "#" * (distmeta.MAX_SMALL_BYTES + 10) + "\n")
        elif cfg_how == "symlink":
            real = _write(tmp_path / "real.cfg", text)
            cfg.symlink_to(real)
        elif cfg_how == "dir":
            cfg.mkdir()
        return prefix

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
    @pytest.mark.parametrize("how", ["oversize", "symlink", "dir"])
    def test_unreadable_pyvenv_cfg_with_include_base_is_not_an_empty_config(self, tmp_path, how):
        prefix = self._with_base(tmp_path, how)
        self._incomplete(tmp_path, _index(prefix, include_base=True), "basemod")

    def test_include_base_that_finds_no_base_site_packages_is_incomplete(self, tmp_path):
        prefix, _ = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg",
            f"home = {tmp_path / 'gone' / 'bin'}\ninclude-system-site-packages = true\n",
        )
        self._incomplete(tmp_path, _index(prefix, include_base=True), "basemod")

    def test_a_readable_cfg_with_a_real_base_stays_complete(self, tmp_path):
        base = tmp_path / "base"
        (base / SP_REL).mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, _ = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg",
            f"home = {base / 'bin'}\ninclude-system-site-packages = true\n",
        )
        assert _index(prefix, include_base=True).complete

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
    def test_a_python_dir_that_is_a_symlink_is_not_skipped_silently(self, tmp_path):
        prefix, _ = _env(tmp_path)
        real = tmp_path / "realpy" / "site-packages"
        real.mkdir(parents=True)
        shutil.rmtree(prefix / "lib" / "python3.12")  # pyvenv.cfg 声明的版本目录换成链接
        (prefix / "lib" / "python3.12").symlink_to(tmp_path / "realpy", target_is_directory=True)
        self._incomplete(tmp_path, _index(prefix), "anything")

    def test_an_unlistable_lib_dir_next_to_a_good_one_is_incomplete(self, tmp_path, monkeypatch):
        prefix = tmp_path / "mixed"
        (prefix / "Lib" / "site-packages").mkdir(parents=True)
        # 大小写敏感的文件系统（Linux CI）上 Lib 和 lib 是两个目录：_layout_dirs 只对小写 lib 调 os.listdir，
        # 所以显式建出它；大小写不敏感的（macOS / Windows）上它就是 Lib 本身，exist_ok 即可。两种都真走到被拒的列目录。
        (prefix / "lib").mkdir(exist_ok=True)
        # 对照：不拦截时这个环境是读全的，红只能来自下面被拒的 listing
        assert _index(prefix).complete
        real_listdir = os.listdir
        refused: list[str] = []

        def listdir(path="."):
            if os.path.basename(os.fspath(path)).lower() == "lib":
                refused.append(os.fspath(path))
                raise PermissionError("denied")
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", listdir)
        idx = _index(prefix)
        assert refused, "monkeypatch 的 os.listdir 没被调用：用例是空对照"
        assert idx.checked and not idx.complete

    @pytest.mark.parametrize("how", ["oversize", "placeholder", "badjson", "notdict", "nofiles"])
    def test_a_conda_meta_record_that_was_not_read_makes_the_environment_incomplete(
        self, tmp_path, monkeypatch, how
    ):
        prefix, _ = _env(tmp_path, "conda")
        meta = prefix / "conda-meta"
        f = meta / "pkg-1.0-h1_0.json"
        body = json.dumps({"name": "pkg", "version": "1.0", "files": [f"{SP_REL}/pkgmod/a.py"]})
        if how == "oversize":
            _write(f, body + " " * (distmeta.MAX_CONDA_META_BYTES + 10))
        elif how == "placeholder":
            _write(f, body)
            real_is_ph = scanbudget.is_placeholder
            monkeypatch.setattr(
                scanbudget, "is_placeholder", lambda st: real_is_ph(st) or st.st_size == len(body)
            )
        elif how == "badjson":
            _write(f, "{not json")
        elif how == "notdict":
            _write(f, "[1, 2]")
        else:
            _write(f, json.dumps({"name": "pkg", "version": "1.0"}))
        self._incomplete(tmp_path, _index(prefix), "pkgmod")

    def test_a_good_conda_meta_alone_stays_complete(self, tmp_path):
        prefix, _ = _env(tmp_path, "conda")
        _write(
            prefix / "conda-meta" / "pkg-1.0-h1_0.json",
            json.dumps({"name": "pkg", "version": "1.0", "files": [f"{SP_REL}/pkgmod/a.py"]}),
        )
        assert _index(prefix).complete

    def test_conda_meta_beyond_the_file_cap_is_incomplete(self, tmp_path, monkeypatch):
        prefix, _ = _env(tmp_path, "conda")
        for i in range(3):
            _write(
                prefix / "conda-meta" / f"p{i}-1.0-h1_0.json",
                json.dumps({"name": f"p{i}", "version": "1.0", "files": []}),
            )
        monkeypatch.setattr(distmeta, "MAX_CONDA_META_FILES", 2)
        assert not _index(prefix).complete

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
    def test_a_conda_meta_directory_that_is_a_symlink_is_incomplete(self, tmp_path):
        prefix, _ = _env(tmp_path, "conda")
        real = tmp_path / "real-meta"
        real.mkdir()
        (prefix / "conda-meta").symlink_to(real, target_is_directory=True)
        assert not _index(prefix).complete

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
    @pytest.mark.parametrize("what", ["egg-link", "package-dir", "module-file"])
    def test_a_symlinked_entry_python_would_follow_is_not_read_as_absent(self, tmp_path, what):
        prefix, site = _env(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        _write(elsewhere / "linkedmod" / "__init__.py", "X = 1\n")
        _write(elsewhere / "linkedmod.py", "X = 1\n")
        _write(elsewhere / "x.egg-link", "/somewhere\n")
        if what == "egg-link":
            (site / "x.egg-link").symlink_to(elsewhere / "x.egg-link")
        elif what == "package-dir":
            (site / "linkedmod").symlink_to(elsewhere / "linkedmod", target_is_directory=True)
        else:
            (site / "linkedmod.py").symlink_to(elsewhere / "linkedmod.py")
        self._incomplete(tmp_path, _index(prefix), "linkedmod")

    @pytest.mark.parametrize("which", ["top_level.txt", "RECORD", "INSTALLER", "direct_url.json"])
    def test_an_oversized_per_dist_file_makes_the_environment_incomplete(self, tmp_path, which):
        prefix, site = _env(tmp_path)
        d = _dist(
            site,
            "bigdist",
            "1.0",
            top="bigmod\n",
            record=["bigmod/__init__.py"],
            direct_url={"url": "https://x"},
            installer="pip",
        )
        limit = {
            "RECORD": distmeta.MAX_RECORD_BYTES,
            "top_level.txt": distmeta.MAX_SMALL_BYTES,
            "INSTALLER": 1024,
            "direct_url.json": distmeta.MAX_SMALL_BYTES,
        }[which]
        _write(d / which, "x" * (limit + 10))
        self._incomplete(tmp_path, _index(prefix), "otherthing")

    def test_a_record_that_hits_the_row_cap_is_incomplete(self, tmp_path, monkeypatch):
        prefix, site = _env(tmp_path)
        _dist(site, "rows", "1.0", record=["a/__init__.py", "b/__init__.py", "c/__init__.py"])
        monkeypatch.setattr(distmeta, "MAX_RECORD_ROWS", 2)
        assert not _index(prefix).complete

    def test_an_oversized_metadata_file_makes_the_environment_incomplete(self, tmp_path):
        prefix, site = _env(tmp_path)
        d = _dist(site, "meta", "1.0", top="metamod\n")
        _write(d / "METADATA", "Name: meta\n" + "x" * (distmeta.MAX_METADATA_BYTES + 10))
        assert not _index(prefix).complete


class TestSameDistributionInTwoLayers:
    def test_upper_index_v1_and_lower_editable_v2_is_ambiguous_not_one_confirmed(self, tmp_path):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        _dist(site, "nsdist", "1.0", record=["nsmod/up.py"])
        _dist(base_site, "nsdist", "2.0", record=["nsmod/low.py"], direct_url=_EDITABLE_URL)
        idx = _index(prefix, include_base=True)
        assert [c.shadowed for c in idx.lookup("nsmod").candidates] == [False, False]
        got = _scan(tmp_path, "import nsmod\n", idx)["nsmod"]
        assert got.distribution_status == "module_origin_ambiguous"
        assert got.selected_distribution == ""
        assert "layers_disagree_on_same_distribution" in got.compatibility

    def test_same_version_but_different_provenance_is_also_ambiguous(self, tmp_path):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        _dist(site, "nsdist", "1.0", record=["nsmod/up.py"])
        _dist(base_site, "nsdist", "1.0", record=["nsmod/low.py"], direct_url=_EDITABLE_URL)
        got = _scan(tmp_path, "import nsmod\n", _index(prefix, include_base=True))["nsmod"]
        assert got.distribution_status == "module_origin_ambiguous"

    def test_identical_versions_and_provenance_across_layers_stay_one_confirmed(self, tmp_path):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        _dist(site, "nsdist", "1.0", record=["nsmod/up.py"])
        _dist(base_site, "nsdist", "1.0", record=["nsmod/low.py"])
        got = _scan(tmp_path, "import nsmod\n", _index(prefix, include_base=True))["nsmod"]
        assert got.distribution_status != "module_origin_ambiguous"


# ---------------------------------------------------------------- O-11 多发行包 / namespace / 多层


class TestSeveralProviders:
    def test_o11_two_installed_cv2_distributions_are_both_kept_and_none_is_picked(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "opencv-python", "4.10.0", top="cv2\n", record=["cv2/__init__.py"])
        _dist(site, "opencv-contrib-python", "4.10.0", top="cv2\n", record=["cv2/__init__.py"])
        got = _scan(tmp_path, "import cv2\n", _index(prefix))["cv2"]
        assert got.distribution_status == "module_origin_ambiguous"
        assert got.resolution_status == "ambiguous"
        assert got.selected_distribution == "" and got.observed_distribution == ""
        installed = [c for c in got.distribution_candidates if c["source"] == "installed"]
        assert sorted(c["distribution"] for c in installed) == [
            "opencv-contrib-python",
            "opencv-python",
        ]
        # 其余已知的替代发行包也留在候选里（来源 curated，未安装）——一个都不丢
        assert {c["distribution"] for c in got.distribution_candidates} == set(
            depresolve.distribution_alternatives("cv2")
        )
        assert "multiple_distribution_candidates" in got.evidence

    def test_o11_declared_name_matching_one_candidate_does_not_pick_it(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "opencv-python", "4.10.0", top="cv2\n")
        _dist(site, "opencv-python-headless", "4.10.0", top="cv2\n")
        got = _scan(
            tmp_path, "import cv2\n", _index(prefix), declared={"opencv-python-headless": ">=4"}
        )["cv2"]
        assert got.resolution_status == "ambiguous" and got.selected_distribution == ""
        assert got.declared_requirement == "opencv-python-headless"
        assert "declared_matches_installed_candidate" in got.compatibility

    def test_o11_nothing_installed_keeps_all_four_alternatives_and_is_ambiguous_even_without_an_index(
        self, tmp_path
    ):
        for idx in (None, _index(_env(tmp_path)[0])):
            got = _scan(tmp_path, "import cv2\n", idx)["cv2"]
            assert got.resolution_status == "ambiguous"
            assert [c["distribution"] for c in got.distribution_candidates] == [
                "opencv-python",
                "opencv-python-headless",
                "opencv-contrib-python",
                "opencv-contrib-python-headless",
            ]
            assert {c["source"] for c in got.distribution_candidates} == {"curated"}
            assert got.selected_distribution == ""
            # 安装路径上的口径一点没变：仍是 curated 的那一个
            assert (got.bucket, got.distribution, got.resolution_source, got.needed) == (
                "third_party",
                "opencv-python",
                "curated",
                True,
            )

    def test_namespace_style_multi_provider_is_ambiguous_and_flagged_namespace(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "google-auth", "2.0", record=["google/auth/__init__.py"])
        _dist(site, "protobuf", "5.0", record=["google/protobuf/__init__.py"])
        idx = _index(prefix)
        look = idx.lookup("google")
        assert {c.distribution for c in look.candidates} == {"google-auth", "protobuf"}
        assert all(c.namespace for c in look.candidates)
        got = _scan(tmp_path, "import google\n", idx)["google"]
        assert got.distribution_status == "module_origin_ambiguous"

    def test_regular_package_provided_twice_is_not_called_a_namespace(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "a-dist", "1", record=["shared/__init__.py"])
        _dist(site, "b-dist", "1", record=["shared/__init__.py"])
        assert not any(c.namespace for c in _index(prefix).lookup("shared").candidates)

    def test_a_higher_priority_root_shadows_the_lower_layer(self, tmp_path):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        _dist(site, "numpy", "2.0.0", top="numpy\n")
        _dist(base_site, "numpy", "1.26.0", top="numpy\n")
        _write(
            site / "numpy" / "__init__.py"
        )  # 只有 top_level.txt 时形状看磁盘：真实安装里包目录当然在
        _write(base_site / "numpy" / "__init__.py")
        idx = _index(prefix, include_base=True)
        got = _scan(tmp_path, "import numpy\n", idx)["numpy"]
        assert got.observed_version == "2.0.0" and got.selected_distribution == "numpy"
        assert "shadowed_by_higher_priority_layer" in got.compatibility
        assert [c["shadowed"] for c in got.distribution_candidates] == [False, True]

    # ---- 跨 site-packages 层的遮蔽：与真解释器的 PathFinder 对拍 ----

    @staticmethod
    def _layer(site: Path, kind: str, tag: str) -> None:
        """在一层 site-packages 里装 `lyr` 的一个提供者：ns = namespace portion（目录无 `__init__`）/ pkg = 常规包 / mod = 单文件。"""
        rec = {
            "ns": [f"lyr/part_{tag}/__init__.py"],
            "pkg": ["lyr/__init__.py"],
            "mod": ["lyr.py"],
        }[kind]
        for r in rec:
            _write(site / r, "")
        _dist(site, f"dist-{tag}", "1", record=rec)

    @staticmethod
    def _real_providers(sites: list[Path]) -> set[int]:
        """真解释器（隔离模式、sys.path 只有这两层）导入 `lyr` 后，实际落在哪几层（下标）。"""
        code = (
            "import sys, json; sys.path[:] = json.loads(sys.argv[1]); import lyr; "
            "print(json.dumps(list(lyr.__path__) if hasattr(lyr, '__path__') and "
            "getattr(lyr, '__file__', None) is None else [lyr.__file__]))"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code, json.dumps([str(x) for x in sites])],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
        got = json.loads(out)
        return {i for i, site in enumerate(sites) if any(g.startswith(str(site)) for g in got)}

    @pytest.mark.parametrize("upper", ["ns", "pkg", "mod"])
    @pytest.mark.parametrize("lower", ["ns", "pkg", "mod"])
    def test_cross_layer_providers_match_the_real_interpreters_path_finder(
        self, tmp_path, upper, lower
    ):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        self._layer(site, upper, "u")
        self._layer(base_site, lower, "l")
        idx = _index(prefix, include_base=True)
        cands = idx.lookup("lyr").candidates
        assert [c.order for c in cands] == [0, 1]
        ours = {c.order for c in cands if not c.shadowed}
        assert ours == self._real_providers([site, base_site])

    def test_two_layers_of_namespace_portions_are_both_providers_and_ambiguous(self, tmp_path):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        self._layer(site, "ns", "u")
        self._layer(base_site, "ns", "l")
        got = _scan(tmp_path, "import lyr\n", _index(prefix, include_base=True))["lyr"]
        assert got.distribution_status == "module_origin_ambiguous"
        assert [c["shadowed"] for c in got.distribution_candidates] == [False, False]

    # ---- 只有 top_level.txt（没有可读 RECORD）的提供者跨层：形状看磁盘，推不出就不替 Python 挑 ----

    @staticmethod
    def _top_only_layer(site: Path, kind: str, tag: str) -> None:
        """同 `_layer`，但元数据只有 `top_level.txt`。pkg / mod / ns / ext（扩展）/ none（磁盘上没有这个名字）。"""
        files = {
            "ns": ["lyr/part_{t}/__init__.py"],
            "pkg": ["lyr/__init__.py"],
            "mod": ["lyr.py"],
            "ext": ["lyr.cpython-312-darwin.so"],
            "none": [],
        }[kind]
        for f in files:
            _write(site / f.format(t=tag), "")
        _dist(site, f"dist-{tag}", "1", top="lyr\n")

    @pytest.mark.parametrize("upper", ["ns", "pkg", "mod", "ext"])
    @pytest.mark.parametrize("lower", ["ns", "pkg", "mod", "ext"])
    def test_top_level_only_cross_layer_providers_match_the_real_interpreters_path_finder(
        self, tmp_path, upper, lower
    ):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        self._top_only_layer(site, upper, "u")
        self._top_only_layer(base_site, lower, "l")
        idx = _index(prefix, include_base=True)
        look = idx.lookup("lyr")
        assert not look.order_unresolved
        ours = {c.order for c in look.candidates if not c.shadowed}
        if upper == "ext" or lower == "ext":
            # 假扩展不是真共享库，真解释器加载会失败——这里只核我们的判定与「ns 不遮蔽 / 常规遮蔽下层」一致
            assert ours == ({0, 1} if upper == lower == "ns" else {0} if upper != "ns" else {1})
        else:
            assert ours == self._real_providers([site, base_site])

    def test_two_top_level_only_namespace_portions_are_both_providers_not_one_confirmed(
        self, tmp_path
    ):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        self._top_only_layer(site, "ns", "u")
        self._top_only_layer(base_site, "ns", "l")
        got = _scan(tmp_path, "import lyr\n", _index(prefix, include_base=True))["lyr"]
        assert got.distribution_status == "module_origin_ambiguous"
        assert [c["shadowed"] for c in got.distribution_candidates] == [False, False]

    @pytest.mark.parametrize("upper", ["none", "ns", "pkg"])
    def test_a_top_level_only_provider_whose_shape_cannot_be_read_is_ambiguous_never_unique(
        self, tmp_path, monkeypatch, upper
    ):
        base = tmp_path / "base"
        base_site = base / SP_REL
        base_site.mkdir(parents=True)
        (base / "bin").mkdir()
        prefix, site = _env(tmp_path)
        _write(
            prefix / "pyvenv.cfg", f"home = {base / 'bin'}\ninclude-system-site-packages = true\n"
        )
        # 上层元数据说有 lyr、磁盘上却没有（或形状读不了）；下层是常规包
        self._top_only_layer(site, "none", "u")
        if upper == "pkg":
            # 目录链接：拒绝跟进 = 形状推不出
            os.symlink(tmp_path, site / "lyr", target_is_directory=True)
        elif upper == "ns":
            # 目录在、清单读不了。不用 chmod 0：Windows 上 chmod 只管只读位，挡不住 scandir
            (site / "lyr").mkdir()
            real_scandir = os.scandir
            blocked = str(site / "lyr")

            def scandir(path=".", *a, **k):
                if os.fspath(path) == blocked:
                    raise PermissionError(13, "denied", blocked)
                return real_scandir(path, *a, **k)

            monkeypatch.setattr(os, "scandir", scandir)
        self._top_only_layer(base_site, "pkg", "l")
        idx = _index(prefix, include_base=True)
        look = idx.lookup("lyr")
        assert look.order_unresolved
        assert not any(c.shadowed for c in look.candidates)
        got = _scan(tmp_path, "import lyr\n", idx)["lyr"]
        assert got.distribution_status == "module_origin_ambiguous"
        assert "layer_order_unresolved" in got.compatibility

    def test_a_single_top_level_only_provider_with_an_unknown_shape_is_still_the_one_candidate(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        self._top_only_layer(site, "none", "u")
        look = _index(prefix).lookup("lyr")
        assert not look.order_unresolved and len(look.candidates) == 1

    def test_two_versions_of_one_distribution_in_one_site_packages_are_reported_not_resolved(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "1.26.0", top="numpy\n")
        _dist(site, "numpy", "2.0.0", top="numpy\n")
        got = _scan(tmp_path, "import numpy\n", _index(prefix))["numpy"]
        assert got.observed_version == "" and got.observed_distribution == "numpy"
        assert "multiple_versions_installed" in got.compatibility


# ---------------------------------------------------------------- O-19 / O-20 与声明冲突


class TestIncompleteMetadataAndNothingInstalled:
    def test_o19_a_dist_with_neither_top_level_nor_record_is_unverified_not_a_mapping(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        (site / "foo").mkdir()
        _dist(site, "foo", "1.0")  # METADATA 只有 Name / Version
        got = _scan(tmp_path, "import foo\n", _index(prefix))["foo"]
        assert got.distribution_status == "unverified"
        assert got.resolution_status == "unverified"
        assert got.selected_distribution == "" and got.observed_distribution == ""
        assert got.distribution_candidates[0]["evidence"] == ["installed_metadata_name_only"]

    def test_o19_a_module_with_no_claiming_distribution_is_an_orphan_not_a_guess(self, tmp_path):
        prefix, site = _env(tmp_path)
        _write(site / "orphan" / "__init__.py")
        _dist(site, "unrelated", "1.0")
        got = _scan(tmp_path, "import orphan\n", _index(prefix))["orphan"]
        assert got.distribution_status == "unverified"
        assert got.distribution_candidates == ()
        assert got.origin_kind == "site-packages"
        assert "module_present_no_metadata" in got.evidence

    def test_o19_a_name_matching_dist_whose_module_is_not_on_disk_is_not_a_candidate(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "foo", "1.0")
        got = _scan(tmp_path, "import foo\n", _index(prefix))["foo"]
        assert got.distribution_candidates == ()
        assert got.distribution_status == "not_installed"

    def test_o19_a_dist_info_without_metadata_falls_back_to_its_directory_name_and_says_so(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "half-dist", "3.1", top="halfmod\n", metadata=False)
        got = _scan(tmp_path, "import halfmod\n", _index(prefix))["halfmod"]
        assert got.observed_distribution == "half_dist" and got.observed_version == "3.1"
        assert "metadata_incomplete" in got.compatibility

    def test_o20_an_uninstalled_unknown_module_is_never_given_a_pypi_name(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.0", top="numpy\n")
        got = _scan(tmp_path, "import totally_unknown_thing\n", _index(prefix))[
            "totally_unknown_thing"
        ]
        assert got.bucket == "unknown" and got.distribution == ""
        assert got.distribution_candidates == ()
        assert (got.selected_distribution, got.observed_distribution) == ("", "")
        assert got.distribution_status == "not_installed"
        assert got.resolution_status == "unresolved"

    def test_a_curated_but_uninstalled_name_stays_a_candidate_not_an_installed_fact(self, tmp_path):
        prefix, _ = _env(tmp_path)
        got = _scan(tmp_path, "import docx\n", _index(prefix))["docx"]
        assert got.distribution_status == "not_installed"
        assert [(c["distribution"], c["source"]) for c in got.distribution_candidates] == [
            ("python-docx", "curated")
        ]
        assert got.selected_distribution == "" and got.observed_distribution == ""
        assert got.resolution_status == "unverified"

    def test_without_an_index_nothing_is_claimed_about_installation(self, tmp_path):
        got = _scan(tmp_path, "import docx\n", None)["docx"]
        assert got.distribution_status == "" and got.distribution_candidates == ()


class TestDeclaredVersusInstalled:
    def test_declared_constraint_not_met_is_reported_and_neither_side_is_overwritten(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "1.26.4", top="numpy\n")
        got = _scan(tmp_path, "import numpy\n", _index(prefix), declared={"numpy": ">=2.0"})[
            "numpy"
        ]
        assert got.distribution_status == "distribution_version_conflict"
        assert (got.declared_requirement, got.declared_constraint) == ("numpy", ">=2.0")
        assert (got.observed_distribution, got.observed_version) == ("numpy", "1.26.4")
        assert "declared_version_conflict" in got.compatibility
        assert got.distribution_provenance == "index"

    def test_declared_constraint_met(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.1.0", top="numpy\n")
        got = _scan(tmp_path, "import numpy\n", _index(prefix), declared={"numpy": ">=2.0,<3"})[
            "numpy"
        ]
        assert got.distribution_status == "installed_confirmed"
        assert "declared_constraint_satisfied" in got.compatibility

    def test_an_unreadable_constraint_is_unchecked_not_a_conflict(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.1.0", top="numpy\n")
        got = _scan(tmp_path, "import numpy\n", _index(prefix), declared={"numpy": "garbage!"})[
            "numpy"
        ]
        assert got.distribution_status == "installed_confirmed"
        assert "declared_constraint_unchecked" in got.compatibility

    def test_declared_distribution_that_differs_from_the_installed_provider_is_reported(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "opencv-python-headless", "4.10.0", top="cv2\n")
        got = _scan(tmp_path, "import cv2\n", _index(prefix), declared={"opencv-python": ">=4"})[
            "cv2"
        ]
        assert got.declared_requirement == "opencv-python"
        assert got.observed_distribution == "opencv-python-headless"
        assert "declared_dist_differs_from_observed" in got.compatibility

    def test_the_closed_sets_hold_for_everything_emitted(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "1.0", top="numpy\n")
        _dist(site, "opencv-python", "4", top="cv2\n")
        _dist(site, "opencv-python-headless", "4", top="cv2\n")
        _dist(
            site,
            "mylab",
            "1",
            top="mylab\n",
            direct_url={"url": "file:///x", "dir_info": {"editable": True}},
        )
        got = _scan(
            tmp_path,
            "import numpy, cv2, mylab, docx, nothere\n",
            _index(prefix),
            declared={"numpy": ">=2"},
        )
        for c in got.values():
            assert c.distribution_status in importscan.DISTRIBUTION_STATUSES
            assert set(c.compatibility) <= importscan.COMPATIBILITY_CODES
            assert set(c.evidence) <= importscan.EVIDENCE_CODES
            assert c.origin_kind in importscan.ORIGIN_KINDS
            assert c.resolution_status in importscan.STATUSES
            assert c.distribution_provenance in ("", *distmeta.PROVENANCES)


class TestIssue889:
    """#882 第五轮 P2 三条（接入安装决策前必须修）：看不全不确认、读不出映射不当没有、冲突不分来源都报。"""

    # ① 有未建模的 `.pth` 钩子 = 别的提供者可能被插到 base site-packages 之前
    @pytest.mark.parametrize(
        "pth_line",
        ["/opt/shared-libs", "import os; os.environ.setdefault('X', '1')"],
        ids=["path_line_outside_site", "unmodelled_import_hook"],
    )
    def test_a_hook_that_may_put_another_provider_first_blocks_the_confirmation(
        self, tmp_path, pth_line
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "1.26.4", top="numpy\n")
        control = _scan(tmp_path, "import numpy\n", _index(prefix))["numpy"]
        assert control.distribution_status == "installed_confirmed"
        assert control.selected_distribution == "numpy"

        _write(site / "hook.pth", pth_line + "\n")
        idx = _index(prefix)
        assert idx.uncovered_paths == 1
        got = _scan(tmp_path, "import numpy\n", idx)["numpy"]
        assert got.distribution_status == "unverified"
        assert got.resolution_status == "unverified"
        assert got.selected_distribution == ""  # 不确认唯一提供者，就不给可重现的包名
        assert (got.observed_distribution, got.observed_version) == ("numpy", "1.26.4")
        assert "path_entries_not_followed" in got.compatibility

    # ① 的补漏（#906 r4236524004）：provenance 专属分支（editable / 本地 / VCS / URL）的提前返回不许绕过「看不全不确认」
    @pytest.mark.parametrize(
        "direct_url",
        [
            {"url": "file:///w/lab", "dir_info": {"editable": True}},
            {
                "url": "file:///home/me/lab-1.0-py3-none-any.whl",
                "archive_info": {"hash": "sha256=ab"},
            },
            {"url": "file:///home/me/lab", "dir_info": {}},
            {
                "url": "https://example.invalid/lab.git",
                "vcs_info": {"vcs": "git", "commit_id": "abc"},
            },
            {"url": "https://example.invalid/lab.zip", "archive_info": {}},
        ],
        ids=["editable", "local_archive", "local_path", "vcs", "url"],
    )
    @pytest.mark.parametrize("pth_line", ["/opt/shared-libs", "import os; os.environ.get('X')"])
    def test_an_uncovered_hook_beats_every_provenance_specific_verdict(
        self, tmp_path, direct_url, pth_line
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "lab", "1.0", top="lab\n", direct_url=direct_url)
        idx_before = _index(prefix)
        assert idx_before.uncovered_paths == 0
        control = _scan(tmp_path, "import lab\n", idx_before)["lab"]
        assert control.distribution_status in (
            "editable_dependency_not_reproducible",
            "installed_source_not_reproducible",
        )

        _write(site / "hook.pth", pth_line + "\n")
        idx = _index(prefix)
        assert idx.uncovered_paths == 1
        got = _scan(tmp_path, "import lab\n", idx)["lab"]
        assert got.distribution_status == "unverified"
        assert got.resolution_status == "unverified"
        assert got.selected_distribution == ""
        assert (got.observed_distribution, got.observed_version) == ("lab", "1.0")
        assert "path_entries_not_followed" in got.compatibility

    def test_the_declared_conflict_is_still_reported_when_an_uncovered_hook_makes_it_unverified(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "lab",
            "1.0",
            top="lab\n",
            direct_url={"url": "file:///w/lab", "dir_info": {"editable": True}},
        )
        _write(site / "hook.pth", "/opt/shared-libs\n")
        got = _scan(tmp_path, "import lab\n", _index(prefix), declared={"lab": ">=2"})["lab"]
        assert got.distribution_status == "unverified"
        assert "declared_version_conflict" in got.compatibility
        assert "path_entries_not_followed" in got.compatibility

    def test_a_modelled_editable_finder_line_does_not_count_as_a_hook(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "1.26.4", top="numpy\n")
        _write(
            site / "__editable__.mylab-0.1.pth",
            "import __editable___mylab_0_1_finder; __editable___mylab_0_1_finder.install()\n",
        )
        _write(site / "__editable___mylab_0_1_finder.py", "MAPPING = {'mylab': '/w/mylab'}\n")
        idx = _index(prefix)
        assert idx.uncovered_paths == 0
        got = _scan(tmp_path, "import numpy\n", idx)["numpy"]
        assert got.distribution_status == "installed_confirmed"

    # ② editable finder 读得到、映射读不出 = 没读全
    @pytest.mark.parametrize(
        "finder_body",
        [
            "MAPPING = dict(mylab='/w/mylab')\n",
            "MAPPING = {k: '/w' for k in ('mylab',)}\n",
            "_m = {'mylab': '/w'}\nMAPPING = _m\n",
            "MAPPING = {'mylab': '/w'\n",  # 语法错误（宿主解析器读不了）
            "NAMESPACES = {}\n",  # 根本没有 MAPPING
        ],
        ids=["dict_call", "comprehension", "name_reference", "syntax_error", "no_mapping"],
    )
    def test_an_editable_finder_whose_mapping_cannot_be_read_marks_the_index_incomplete(
        self, tmp_path, finder_body
    ):
        prefix, site = _env(tmp_path)
        _write(
            site / "__editable__.mylab-0.1.pth",
            "import __editable___mylab_0_1_finder; __editable___mylab_0_1_finder.install()\n",
        )
        _write(site / "__editable___mylab_0_1_finder.py", finder_body)
        idx = _index(prefix)
        assert idx.complete is False
        assert any(i["path"].endswith("__editable___mylab_0_1_finder.py") for i in idx.issues)
        got = _scan(tmp_path, "import nothere\n", idx)["nothere"]
        # finder 可能提供它而我们没读出来：没查到 ≠ 没装，不是 not_installed，而是环境没看全
        assert got.distribution_status == "environment_not_checked"
        assert "metadata_scan_incomplete" in got.compatibility

    @pytest.mark.parametrize("body", ["MAPPING = {'mylab': '/w'}\n", "MAPPING = {}\n"])
    def test_a_literal_mapping_including_an_empty_one_is_a_complete_read(self, tmp_path, body):
        prefix, site = _env(tmp_path)
        _write(
            site / "__editable__.mylab-0.1.pth",
            "import __editable___mylab_0_1_finder; __editable___mylab_0_1_finder.install()\n",
        )
        _write(site / "__editable___mylab_0_1_finder.py", body)
        idx = _index(prefix)
        assert idx.complete is True
        got = _scan(tmp_path, "import nothere\n", idx)["nothere"]
        assert got.distribution_status == "not_installed"

    # ③ 声明与已装版本的冲突，不分来源都报
    @pytest.mark.parametrize(
        "direct_url, status, provenance",
        [
            (
                {"url": "file:///dl/lab-1.0.tar.gz", "archive_info": {}},
                "installed_source_not_reproducible",
                "local_archive",
            ),
            (
                {"url": "file:///w/lab", "dir_info": {}},
                "installed_source_not_reproducible",
                "local_path",
            ),
            (
                {"url": "https://x.example/lab.git", "vcs_info": {"vcs": "git"}},
                "installed_source_not_reproducible",
                "vcs",
            ),
            (
                {"url": "https://x.example/lab-1.0.tar.gz", "archive_info": {}},
                "installed_source_not_reproducible",
                "url",
            ),
            (
                {"url": "file:///w/lab", "dir_info": {"editable": True}},
                "editable_dependency_not_reproducible",
                "editable",
            ),
        ],
        ids=["local_archive", "local_path", "vcs", "url", "editable"],
    )
    def test_a_declared_constraint_the_installed_version_breaks_is_reported_for_every_provenance(
        self, tmp_path, direct_url, status, provenance
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "lab", "1.0", top="lab\n", direct_url=direct_url)
        idx = _index(prefix)
        broken = _scan(tmp_path, "import lab\n", idx, declared={"lab": ">=2"})["lab"]
        assert broken.distribution_provenance == provenance
        assert broken.distribution_status == status  # 来源本身的结论不被冲突盖掉
        assert "declared_version_conflict" in broken.compatibility
        assert (broken.declared_requirement, broken.declared_constraint) == ("lab", ">=2")
        assert (broken.observed_distribution, broken.observed_version) == ("lab", "1.0")

        fine = _scan(tmp_path, "import lab\n", idx, declared={"lab": ">=0.5"})["lab"]
        assert "declared_constraint_satisfied" in fine.compatibility
        assert "declared_version_conflict" not in fine.compatibility
        unreadable = _scan(tmp_path, "import lab\n", idx, declared={"lab": "garbage!"})["lab"]
        assert "declared_constraint_unchecked" in unreadable.compatibility

    def test_a_conda_dist_info_provenance_also_reports_the_conflict(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "1.26.4", top="numpy\n", installer="conda")
        got = _scan(tmp_path, "import numpy\n", _index(prefix), declared={"numpy": ">=2"})["numpy"]
        assert "declared_version_conflict" in got.compatibility
        assert got.distribution_status == "distribution_version_conflict"


# ---------------------------------------------------------------- 供应链：已安装元数据不是安装授权


class TestInstalledMetadataIsNotAnInstallAuthorization:
    def test_the_installable_sources_are_exactly_the_three_existing_ones(self):
        assert depresolve.INSTALLABLE_SOURCES == (
            depresolve.SOURCE_PROJECT_DECLARED,
            depresolve.SOURCE_CURATED,
            depresolve.SOURCE_USER_SPECIFIED,
        )
        assert set(vars(depresolve)) >= {"ALTERNATIVE_DISTRIBUTIONS"}  # 观测表存在……
        # ……但安装路径完全不读它：cv2 的解析仍是 curated 的单个包
        import inspect

        src = inspect.getsource(depresolve.resolve) + inspect.getsource(
            depresolve.curated_distribution
        )
        assert "ALTERNATIVE_DISTRIBUTIONS" not in src and "distribution_alternatives" not in src

    def test_an_installed_unknown_module_still_resolves_to_nothing_installable(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "weird-dist-name", "2.0", top="weirdmod\n")
        idx = _index(prefix)
        assert idx.lookup("weirdmod").candidates  # 元数据读到了……
        proj = tmp_path / "proj"
        proj.mkdir()
        assert depresolve.resolve(proj, "weirdmod") is None  # ……但 depresolve 仍判不可安装
        assert depresolve.resolve(proj, "docx").resolution_source == "curated"  # 旧路径照旧

    def test_no_editable_or_local_provider_can_produce_a_requirement_source(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "mylab",
            "1",
            top="mylab\n",
            direct_url={"url": "file:///x", "dir_info": {"editable": True}},
        )
        _dist(
            site,
            "labtool",
            "1",
            top="labtool\n",
            direct_url={"url": "file:///y.whl", "archive_info": {}},
        )
        _dist(
            site,
            "vcsdist",
            "1",
            top="vcsdist\n",
            direct_url={"url": "https://h/r.git", "vcs_info": {"vcs": "git"}},
        )
        got = _scan(tmp_path, "import mylab, labtool, vcsdist\n", _index(prefix))
        for c in got.values():
            assert c.selected_distribution == ""
            assert not any(row["reproducible"] for row in c.distribution_candidates)

    def test_the_planner_and_repair_modules_do_not_read_the_new_observation(self):
        root = Path(importscan.__file__).parent
        for mod in ("depplan.py", "deprepair.py", "depresolve.py"):
            names = {
                n.id if isinstance(n, ast.Name) else n.attr
                for n in ast.walk(ast.parse((root / mod).read_text(encoding="utf-8")))
                if isinstance(n, (ast.Name, ast.Attribute))
            }
            assert "distmeta" not in names, mod
            assert not names & {
                "distribution_candidates",
                "selected_distribution",
                "distribution_status",
            }, mod

    def test_distmeta_neither_imports_the_installer_nor_builds_a_dependency_requirement(self):
        tree = ast.parse(Path(distmeta.__file__).read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        assert not names & {
            "DependencyRequirement",
            "from_user_input",
            "INSTALLABLE_SOURCES",
            "resolve",
        }


# ---------------------------------------------------------------- 零执行（P7.2 元数据半边）


def _boom(what: str):
    def fail(*a, **kw):
        raise AssertionError(f"metadata read reached {what}")

    return fail


@contextlib.contextmanager
def _armed(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _boom("subprocess.Popen"))
    monkeypatch.setattr(os, "system", _boom("os.system"))
    for name in (
        "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
        "posix_spawn", "posix_spawnp", "fork", "forkpty", "startfile",
    ):  # fmt: skip
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom(f"os.{name}"))
    monkeypatch.setattr(socket, "socket", _boom("socket.socket"))
    monkeypatch.setattr(socket, "create_connection", _boom("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", _boom("socket.getaddrinfo"))
    monkeypatch.setattr(importlib.util, "find_spec", _boom("importlib.util.find_spec"))
    for attr in (
        "distributions",
        "distribution",
        "packages_distributions",
        "version",
        "metadata",
        "files",
    ):
        monkeypatch.setattr(importlib.metadata, attr, _boom(f"importlib.metadata.{attr}"))
    real_import_module = importlib.import_module

    def guarded_import_module(name, package=None):
        if name.split(".", 1)[0] in USER_NAMES:
            raise AssertionError(f"read imported user module {name!r} via import_module")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", guarded_import_module)
    real_import = builtins.__import__

    def guarded_import(name, *a, **kw):
        if name.split(".", 1)[0] in USER_NAMES:
            raise AssertionError(f"read imported user module {name!r}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield


@contextlib.contextmanager
def _state_unchanged():
    path, environ, cwd = list(sys.path), dict(os.environ), os.getcwd()
    before = set(sys.modules)
    yield
    assert sys.path == path and dict(os.environ) == environ and os.getcwd() == cwd
    assert not [m for m in set(sys.modules) - before if m.split(".", 1)[0] in USER_NAMES]


@contextlib.contextmanager
def _spy_paths(monkeypatch, under: Path):
    """记录 `under` 之下被 open / lstat / stat / scandir / listdir 碰过的路径。"""
    seen: list[str] = []
    prefix = os.path.normpath(str(under))

    def record(p):
        try:
            s = os.path.normpath(os.fspath(p))
        except TypeError:
            return
        if s.startswith(prefix):
            seen.append(s)

    for name in ("open", "lstat", "stat", "scandir", "listdir"):
        real = getattr(os, name)

        def make(real=real):
            def spy(path, *a, **kw):
                record(path)
                return real(path, *a, **kw)

            return spy

        monkeypatch.setattr(os, name, make())
    yield seen


class TestReadingRunsNothing:
    def _hostile(self, tmp_path: Path):
        prefix, site = _env(tmp_path)
        marks = {
            k: tmp_path / f"SIDE_EFFECT_{k}"
            for k in ("pth", "sitecustomize", "usercustomize", "evilpkg", "finder", "initpkg")
        }

        def writer(k: str) -> str:
            return f"open({str(marks[k])!r}, 'w').write('x')\n"

        _write(site / "evil.pth", f"import os; open({str(marks['pth'])!r}, 'w').write('x')\n")
        _write(site / "sitecustomize.py", writer("sitecustomize"))
        _write(site / "usercustomize.py", writer("usercustomize"))
        _write(site / "evilpkg" / "__init__.py", writer("evilpkg") + "import scipy\n")
        _write(
            site / "__editable__.evil-1.0.pth",
            "import __editable___evil_finder; __editable___evil_finder.install()\n",
        )
        _write(
            site / "__editable___evil_finder.py",
            writer("finder") + "MAPPING = {'evilpkg2': '/x'}\n",
        )
        _dist(site, "evilpkg", "1.0", top="evilpkg\n", record=["evilpkg/__init__.py"])
        return prefix, site, marks

    def test_reading_and_scanning_execute_no_pth_sitecustomize_package_or_finder(
        self, tmp_path, monkeypatch
    ):
        prefix, site, marks = self._hostile(tmp_path)
        with _armed(monkeypatch), _state_unchanged():
            idx = _index(prefix)
            got = _scan(tmp_path, "import evilpkg\nimport evilpkg2\nimport sitecustomize\n", idx)
        assert not [k for k, p in marks.items() if p.exists()]
        # 这个环境里有一行没建模的 `import …` 的 `.pth`（Python 会执行它）：看不全，所以 evilpkg 只是「观测到」而不是
        # 「确认是唯一提供者」（#889 ①）；零执行的结论不变——痕迹文件一个都没出现
        assert got["evilpkg"].distribution_status == "unverified"
        assert got["evilpkg"].observed_distribution == "evilpkg"
        assert "path_entries_not_followed" in got["evilpkg"].compatibility
        # editable 的那个也一样：这个环境看不全，来源专属的结论不能抢在「看不全不确认」前面（#906 r4236524004）
        assert got["evilpkg2"].distribution_status == "unverified"
        assert got["evilpkg2"].distribution_provenance == "editable"

    def test_the_armed_traps_are_real(self, monkeypatch):
        with _armed(monkeypatch):
            with pytest.raises(AssertionError, match="Popen"):
                subprocess.Popen(["true"])
            with pytest.raises(AssertionError, match="importlib.metadata.distributions"):
                importlib.metadata.distributions()
            with pytest.raises(AssertionError, match="packages_distributions"):
                importlib.metadata.packages_distributions()
            with pytest.raises(AssertionError, match="find_spec"):
                importlib.util.find_spec("json")
            with pytest.raises(AssertionError, match="user module"):
                __import__("evilpkg")
            with pytest.raises(AssertionError, match="socket"):
                socket.socket()
            __import__("json")

    def test_only_files_inside_the_environment_prefix_are_touched(self, tmp_path, monkeypatch):
        prefix, site = _env(tmp_path)
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        _write(outside / "sentinel.txt")
        _write(site / "pathy.pth", f"{outside}\n")
        _write(
            site / "__editable__.p-1.pth",
            "import __editable___p_1_finder; __editable___p_1_finder.install()\n",
        )
        _write(site / "__editable___p_1_finder.py", f"MAPPING = {{'p': {str(outside)!r}}}\n")
        _dist(
            site,
            "p",
            "1",
            top="p\n",
            direct_url={"url": f"file://{outside}", "dir_info": {"editable": True}},
        )
        with _spy_paths(monkeypatch, tmp_path) as seen:
            _index(prefix)
            assert not [s for s in seen if s.startswith(str(prefix)) is False], seen
            os.lstat(outside)  # 反向钉：间谍确实会记下 prefix 之外的访问
            assert any(s.startswith(str(outside)) for s in seen)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
    def test_a_symlinked_dist_info_is_noted_and_its_target_is_never_opened(
        self, tmp_path, monkeypatch
    ):
        prefix, site = _env(tmp_path)
        target = tmp_path / "decoy" / "evil-1.0.dist-info"
        _write(target / "METADATA", "Name: evil\nVersion: 1\n\n")
        _write(target / "top_level.txt", "evil\n")
        (site / "evil-1.0.dist-info").symlink_to(target, target_is_directory=True)
        with _spy_paths(monkeypatch, tmp_path / "decoy") as seen:
            idx = _index(prefix)
        assert seen == []
        assert idx.lookup("evil").candidates == ()
        assert any(
            i["code"] == "symlinked_dir" and i["path"].endswith("evil-1.0.dist-info")
            for i in idx.issues
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink")
    def test_a_symlinked_metadata_file_is_refused(self, tmp_path):
        prefix, site = _env(tmp_path)
        d = _dist(site, "lnk", "1", metadata=False)
        secret = _write(tmp_path / "secret.txt", "Name: leaked\nVersion: 9\n\n")
        (d / "METADATA").symlink_to(secret)
        idx = _index(prefix)
        assert [c.distribution for c in idx.lookup("lnk").candidates] == []
        assert any(i["code"] == "symlinked_dir" for i in idx.issues)
        # 名字退回目录名，没读到链接目标的内容
        assert idx._dists[0].name == "lnk" and idx._dists[0].metadata_ok is False

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
    def test_a_fifo_named_like_a_metadata_file_does_not_block(self, tmp_path):
        prefix, site = _env(tmp_path)
        d = _dist(site, "fifo", "1", metadata=False)
        os.mkfifo(d / "METADATA")
        idx = _index(prefix)  # 阻塞就是用例超时
        assert any(i["code"] == "unreadable_file" for i in idx.issues)

    def test_placeholder_files_are_not_read(self, tmp_path, monkeypatch):
        prefix, site = _env(tmp_path)
        _dist(site, "cloudy", "1", top="cloudy\n")
        monkeypatch.setattr(scanbudget, "is_placeholder", lambda st: True)
        idx = _index(prefix)
        assert idx.lookup("cloudy").candidates == ()
        assert any(i["code"] == "placeholder_file" for i in idx.issues)


class TestBudgetsLeaveATrace:
    def _many(self, tmp_path, n=30):
        prefix, site = _env(tmp_path)
        for i in range(n):
            _dist(site, f"pkg{i}", "1.0", top=f"mod{i}\n")
        return prefix

    def test_entry_budget_stops_the_listing_and_not_installed_is_no_longer_claimed(self, tmp_path):
        prefix = self._many(tmp_path)
        budget = scanbudget.Budget(limits=scanbudget.Limits(max_entries=5))
        idx = distmeta.index_environment(prefix, budget=budget)
        assert idx.complete is False
        assert any(i["code"] == "entry_budget" for i in idx.issues)
        got = _scan(tmp_path, "import mod29\n", idx)["mod29"]
        assert got.distribution_status == "environment_not_checked"
        assert "metadata_scan_incomplete" in got.compatibility

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod")
    def test_an_unlistable_site_packages_is_not_checked_rather_than_empty(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.0", top="numpy\n")
        site.chmod(0)
        try:
            if os.access(site, os.R_OK):
                pytest.skip("running as a user that ignores directory modes")
            idx = _index(prefix)
        finally:
            site.chmod(0o755)
        assert idx.complete is False
        got = _scan(tmp_path, "import numpy\n", idx)["numpy"]
        assert got.distribution_status == "environment_not_checked"

    def test_scandir_failure_is_not_checked_not_not_installed(self, tmp_path, monkeypatch):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.0", top="numpy\n")
        real = os.scandir

        def boom(path="."):
            if str(path) == str(site):
                raise PermissionError(13, "denied")
            return real(path)

        monkeypatch.setattr(os, "scandir", boom)
        idx = _index(prefix)
        assert idx.complete is False
        assert (
            _scan(tmp_path, "import numpy\n", idx)["numpy"].distribution_status
            == "environment_not_checked"
        )

    def test_an_entry_whose_stat_fails_makes_the_environment_incomplete(
        self, tmp_path, monkeypatch
    ):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.0", top="numpy\n")
        _dist(site, "scipy", "1.0", top="scipy\n")

        class Wrapped:
            def __init__(self, e):
                self._e = e
                self.name = e.name

            def stat(self, **kw):
                if self.name.startswith("scipy"):
                    raise PermissionError(13, "denied")
                return self._e.stat(**kw)

            def __getattr__(self, k):
                return getattr(self._e, k)

        class It:
            def __init__(self, it):
                self._it = it

            def __enter__(self):
                self._it.__enter__()
                return self

            def __exit__(self, *a):
                return self._it.__exit__(*a)

            def __iter__(self):
                return (Wrapped(e) for e in self._it)

        real_scandir = os.scandir
        monkeypatch.setattr(
            os,
            "scandir",
            lambda p=".": It(real_scandir(p)) if str(p) == str(site) else real_scandir(p),
        )
        idx = _index(prefix)
        assert idx.complete is False
        assert any(i["code"] == "unreadable_file" for i in idx.issues)
        got = _scan(tmp_path, "import scipy\n", idx)["scipy"]
        assert got.distribution_status == "environment_not_checked"

    def test_byte_budget_stops_reading_and_is_recorded(self, tmp_path):
        prefix = self._many(tmp_path)
        budget = scanbudget.Budget(
            limits=scanbudget.Limits(max_source_bytes=600, max_file_bytes=64 * 1024)
        )
        idx = distmeta.index_environment(prefix, budget=budget)
        assert idx.complete is False
        assert any(i["code"] == "source_byte_budget" for i in idx.issues)

    def test_wall_clock_budget(self, tmp_path):
        prefix = self._many(tmp_path)
        ticks = iter(range(0, 10_000, 100))
        budget = scanbudget.Budget(
            limits=scanbudget.Limits(max_seconds=1.0), clock=lambda: next(ticks)
        )
        idx = distmeta.index_environment(prefix, budget=budget)
        assert idx.complete is False
        assert any(i["code"] == "time_budget" for i in idx.issues)

    def test_cancellation(self, tmp_path):
        prefix = self._many(tmp_path)
        idx = distmeta.index_environment(prefix, budget=scanbudget.Budget(cancel=lambda: True))
        assert idx.complete is False
        assert any(i["code"] == "cancelled" for i in idx.issues)

    def test_an_oversized_metadata_file_falls_back_to_the_directory_name(self, tmp_path):
        prefix, site = _env(tmp_path)
        d = _dist(site, "bigdoc", "1.0", top="bigdoc\n")
        _write(
            d / "METADATA",
            "Name: bigdoc\nVersion: 1.0\n\n" + "x" * (distmeta.MAX_METADATA_BYTES + 10),
        )
        idx = _index(prefix)
        assert any(i["code"] == "file_too_large" for i in idx.issues)
        got = _scan(tmp_path, "import bigdoc\n", idx)["bigdoc"]
        assert got.observed_distribution == "bigdoc"
        assert "metadata_incomplete" in got.compatibility

    def test_garbage_in_names_and_versions_is_not_trusted(self, tmp_path):
        prefix, site = _env(tmp_path)
        d = _dist(site, "okname", "1.0", top="okmod\n")
        _write(d / "METADATA", "Name: evil name; rm -rf /\nVersion: 1.0 && x\n\n")
        got = _scan(tmp_path, "import okmod\n", _index(prefix))["okmod"]
        assert got.observed_distribution == "okname"  # 退回目录名
        assert got.observed_version == "1.0"
        assert "metadata_incomplete" in got.compatibility


class TestPrivacy:
    def test_payloads_carry_no_absolute_path_and_no_metadata_body(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.0.0", top="numpy\n", summary="SECRET-SUMMARY-TEXT")
        _dist(
            site,
            "mylab",
            "1",
            top="mylab\n",
            direct_url={"url": "file:///home/secret/user/mylab", "dir_info": {"editable": True}},
        )
        _write(site / "legacy.pth", "/home/secret/user/src\n")
        idx = _index(prefix)
        proj = tmp_path / "proj"
        _write(proj / "s.py", "import numpy\nimport mylab\n")
        res = importscan.scan(proj, "s.py", dists=idx)
        blob = json.dumps({"scan": res.to_payload(), "index": idx.to_payload()})
        for leak in (str(tmp_path), "SECRET-SUMMARY-TEXT", "/home/secret", "LONG BODY", "sha256="):
            assert leak not in blob, leak
        assert idx.to_payload()["distributions"] == 2


class TestPerformanceShape:
    def test_a_few_hundred_distributions_index_with_a_bounded_number_of_reads(
        self, tmp_path, monkeypatch
    ):
        prefix, site = _env(tmp_path)
        n = 300
        for i in range(n):
            _dist(
                site,
                f"pkg{i}",
                "1.0",
                top=f"mod{i}\n",
                record=[f"mod{i}/__init__.py"],
                installer="pip",
            )
        opened: list[str] = []
        real_open = os.open
        monkeypatch.setattr(
            os, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1]
        )
        idx = _index(prefix)
        assert idx.complete and idx.distribution_count == n
        # 每个发行包最多读五个小文件，没有别的
        assert len(opened) <= 5 * n + 1
        assert idx.lookup("mod299").candidates[0].distribution == "pkg299"


# ---------------------------------------------------------------- 结构门禁


_FORBIDDEN_NAMES = {
    "subprocess", "socket", "importlib", "runpy", "exec", "eval", "find_spec",
    "__import__", "import_module", "Popen", "system", "urllib", "requests", "pip", "metadata",
    "packages_distributions", "distributions", "pkgutil", "zipimport",
}  # fmt: skip
_ALLOWED_IMPORTS = {
    "ast",
    "csv",
    "io",
    "json",
    "os",
    "re",
    "stat",
    "collections",
    "dataclasses",
    "pathlib",
    "__future__",
}
_ALLOWED_SIBLINGS = {"depresolve", "scanbudget"}
_ALLOWED_ATTRS = {
    "depresolve": {"normalize_distribution", "version_satisfies"},
    "scanbudget": {
        "Budget",
        "Limits",
        "ISSUE_SYMLINK_DIR",
        "ISSUE_UNREADABLE_FILE",
        "ISSUE_UNREADABLE_DIR",
        "ISSUE_PLACEHOLDER",
        "ISSUE_TOO_LARGE",
        "ISSUE_PARSE_FAILED",
        "is_redirect",
        "is_placeholder",
        "read_regular_text",
        "redirected_component",
    },
}


class TestStructuralGate:
    TREE = ast.parse(Path(distmeta.__file__).read_text(encoding="utf-8"))

    def test_every_literal_distmeta_emits_is_in_its_closed_set(self):
        """结构性：AST 扫 distmeta.py，所有发出的 compatibility 码 / 状态 / 结论种类 / 证据码 / issue 码的字面量
        都在各自的闭集里；无法静态判定的写法直接红（别靠「某次扫描恰好没发出来」的运行时断言）。"""
        tree = ast.parse(Path(distmeta.__file__).read_text(encoding="utf-8"))
        closed = {
            "compat": set(distmeta.COMPAT_CODES),
            "status": set(distmeta.STATUSES) | {distmeta.ST_NOT_CHECKED},
            "kind": {v for k, v in vars(distmeta).items() if k.startswith("KIND_")},
            "evidence": set(distmeta.EVIDENCE_CODES),
            "issue": set(scanbudget.ISSUE_CODES),
        }
        seen: dict[str, list[tuple[int, set[str]]]] = {k: [] for k in closed}

        def scan(fn: ast.AST) -> None:
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}  # type: ignore[attr-defined]
            # `refused` 来自 `Budget.charge_source`：它只回 scanbudget 的账本码
            assigned: dict[str, set[str]] = {"refused": set(scanbudget.ISSUE_CODES)}

            def lits(node) -> set[str] | None:
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    return {node.value}
                if isinstance(node, ast.IfExp):
                    x, y = lits(node.body), lits(node.orelse)
                    return None if x is None or y is None else x | y
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id == "scanbudget" and hasattr(scanbudget, node.attr):
                        return {getattr(scanbudget, node.attr)}
                if isinstance(node, ast.Name):
                    if node.id in assigned:
                        return assigned[node.id]
                    v = getattr(distmeta, node.id, None)
                    if isinstance(v, str) and node.id.isupper():
                        return {v}
                return None

            def set_lits(node) -> set[str] | None:
                if isinstance(node, ast.Set):
                    out: set[str] = set()
                    for e in node.elts:
                        v = lits(e)
                        if v is None:
                            return None
                        out |= v
                    return out
                return lits(node)

            def bind(target, value) -> None:
                if isinstance(target, ast.Name):
                    v = lits(value)
                    if v is not None:
                        assigned.setdefault(target.id, set()).update(v)
                elif isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
                    for t, e in zip(target.elts, value.elts, strict=True):
                        bind(t, e)

            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1:
                    bind(n.targets[0], n.value)

            def record(kind: str, node, line: int) -> None:
                v = lits(node)
                assert v is not None, f"line {line}: {kind} code not statically checkable"
                seen[kind].append((line, v))

            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.args:
                    f, recv = n.func.attr, n.func.value
                    if f == "append" and isinstance(recv, ast.Name) and recv.id == "compat":
                        record("compat", n.args[0], n.lineno)
                    elif f == "append" and isinstance(recv, ast.Name) and recv.id == "evidence":
                        record("evidence", n.args[0], n.lineno)
                    elif f == "add" and isinstance(recv, ast.Call):
                        owner = getattr(
                            recv.func, "value", None
                        )  # `x.modules.setdefault(m, set()).add(EV)`
                        if getattr(recv.func, "attr", "") == "setdefault" and (
                            getattr(owner, "attr", "") == "modules"
                        ):
                            record("evidence", n.args[0], n.lineno)
                    elif f in ("note", "refuse"):
                        if isinstance(n.args[0], ast.Name) and n.args[0].id in params:
                            continue  # 转发包装（`_Reader.note/refuse`）：调用方各自被检查
                        record("issue", n.args[0], n.lineno)
                if isinstance(n, ast.Assign) and len(n.targets) == 1:
                    t = n.targets[0]
                    if (
                        isinstance(t, ast.Name)
                        and t.id == "evidence"
                        and isinstance(n.value, ast.Set)
                    ):
                        v = set_lits(n.value)
                        assert v is not None, f"line {n.lineno}: evidence not checkable"
                        seen["evidence"].append((n.lineno, v))
                if isinstance(n, ast.Dict):
                    for k, val in zip(n.keys, n.values, strict=True):
                        if isinstance(k, ast.Constant) and k.value in ("status", "kind"):
                            record(k.value, val, val.lineno)

        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scan(fn)
        for kind in closed:
            assert seen[kind], f"{kind}: the scan found nothing (gate is hollow)"
            for line, v in seen[kind]:
                assert v <= closed[kind], (kind, line, sorted(v - closed[kind]))

    def test_imports_are_the_allowed_leaf_set(self):
        seen: set[str] = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Import):
                seen |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level == 1:
                    seen |= {f"sibling:{a.name}" for a in node.names}
                else:
                    seen.add((node.module or "").split(".")[0])
        assert {s.split(":", 1)[1] for s in seen if s.startswith("sibling:")} == _ALLOWED_SIBLINGS
        assert {s for s in seen if not s.startswith("sibling:")} <= _ALLOWED_IMPORTS

    def test_no_process_network_dynamic_execution_or_importlib_metadata_name_appears(self):
        found = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
                found.add(node.attr)
        assert found == set()
        # 内建 compile()（`re.compile` 是属性，不算）
        assert not [n for n in ast.walk(self.TREE) if isinstance(n, ast.Name) and n.id == "compile"]

    def test_sibling_modules_are_used_through_a_whitelist_of_attributes(self):
        used: dict[str, set[str]] = {}
        for node in ast.walk(self.TREE):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in _ALLOWED_ATTRS
            ):
                used.setdefault(node.value.id, set()).add(node.attr)
        for mod, attrs in used.items():
            assert attrs <= _ALLOWED_ATTRS[mod], f"{mod}: {sorted(attrs - _ALLOWED_ATTRS[mod])}"
        assert set(used) == set(_ALLOWED_ATTRS)  # 前提：门禁真的看到了两个兄弟

    def test_every_file_is_read_through_scanbudget_and_nothing_opens_a_path_directly(self):
        calls = {
            n.func.id if isinstance(n.func, ast.Name) else n.func.attr
            for n in ast.walk(self.TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
        }
        assert not calls & {"open", "read_text", "read_bytes", "fopen"}
        assert "read_regular_text" in calls
