"""Import Origin Resolver PR4（#888）：builtin 名单、扩展模块后缀取**目标**解释器；扫描截断时标不完整。

判据的主语：谁的 `sys.builtin_module_names` / `EXTENSION_SUFFIXES`——是**会跑脚本的那个解释器**的，不是 Flask 宿主的。
目标事实来自 `depplan.target_facts`（依赖门 / 授权检查已经在起的那一个子进程）；量不到（替身事实、`facts=None`）就是
「不知道」，扫描走保守口径（ambiguous / unverified / 搜索不完整），不拿宿主的名单冒充。
"""

from __future__ import annotations

import importlib.machinery
import sys
from pathlib import Path

import pytest

from tavotto.engine import depplan, importscan

SCRIPT = "s.py"
BARE = importscan.Entry(profile=importscan.PROFILE_BARE)
#: 一个 Linux / CPython 3.11 的目标
LINUX_SUFFIXES = (".cpython-311-x86_64-linux-gnu.so", ".abi3.so", ".so")
#: 一个 Windows / CPython 3.13 free-threaded 的目标
WINDOWS_SUFFIXES = (".cp313t-win_amd64.pyd", ".pyd")


def _tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
    return root


def _by(res: importscan.ScanResult) -> dict[str, importscan.ImportClass]:
    return {c.module: c for c in res.classes}


# ---------------------------------------------------------------------------
# builtin：posix / nt 按平台二选一
# ---------------------------------------------------------------------------
class TestBuiltinNamesComeFromTheTarget:
    FILES = {
        SCRIPT: "import nt\nimport posix\n",
        "nt.py": "import from_nt\n",
        "posix.py": "import from_posix\n",
    }

    def test_the_universal_builtin_table_no_longer_claims_a_platform_module(self):
        assert not {"posix", "nt"} & importscan.BUILTIN_NAMES
        assert {"posix", "nt"} <= importscan.HOST_BUILTIN_EXTRA  # 不论宿主是哪个平台

    def test_a_linux_target_loads_a_project_nt_py_and_follows_its_imports(self, tmp_path):
        _tree(tmp_path, self.FILES)
        res = importscan.scan(
            tmp_path, SCRIPT, entry=BARE, builtin=frozenset({"posix", "sys", "time"})
        )
        got = _by(res)
        # Linux 上 `nt` 不是内建：项目里的 nt.py 是个普通模块，里面的 import 是真依赖
        assert (got["nt"].bucket, got["nt"].resolution_status) == ("local", "resolved")
        assert "from_nt" in got
        # `posix` 是内建：本地同名文件到不了
        assert (got["posix"].bucket, got["posix"].origin_kind) == ("stdlib", "built-in")
        assert "from_posix" not in got

    def test_a_windows_target_is_the_mirror_image(self, tmp_path):
        _tree(tmp_path, self.FILES)
        res = importscan.scan(tmp_path, SCRIPT, entry=BARE, builtin=frozenset({"nt", "sys"}))
        got = _by(res)
        assert (got["nt"].bucket, got["nt"].origin_kind) == ("stdlib", "built-in")
        assert (got["posix"].bucket, got["posix"].resolution_status) == ("local", "resolved")
        assert "from_posix" in got and "from_nt" not in got

    def test_without_target_facts_the_answer_is_ambiguous_not_the_hosts(self, tmp_path):
        _tree(tmp_path, self.FILES)
        got = _by(importscan.scan(tmp_path, SCRIPT, entry=BARE))
        for name in ("nt", "posix"):
            assert (got[name].bucket, got[name].resolution_status) == ("local", "ambiguous")
            assert "may_shadow_builtin" in got[name].warnings

    def test_depplan_passes_the_target_interpreters_builtin_names(self, tmp_path):
        proj = _tree(tmp_path / "proj", {"plot.py": "import nt\n", "nt.py": "import yaml\n"})
        linux = depplan.TargetFacts(
            python="/fake/python",
            marker_env={"python_version": "3.11", "sys_platform": "linux"},
            stdlib=importscan.HOST_STDLIB,
            installed={},
            builtin=frozenset({"posix", "sys"}),
            ext_suffixes=LINUX_SUFFIXES,
        )
        plan = depplan.plan(proj, "plot.py", facts=linux, target_kind="project_venv")
        nt = next(c for c in plan.scan["classes"] if c["module"] == "nt")
        # Linux 上 `nt` 不是内建：项目里的 nt.py 是个本地文件（wrapper 下与标准库同名一律 ambiguous、不冒充内建）
        assert (nt["bucket"], nt["origin_kind"], nt["resolution_status"]) == (
            "local",
            "project-local",
            "ambiguous",
        )
        windows = depplan.TargetFacts(
            python="/fake/python",
            marker_env={"python_version": "3.13", "sys_platform": "win32"},
            stdlib=importscan.HOST_STDLIB,
            installed={},
            builtin=frozenset({"nt", "sys"}),
            ext_suffixes=WINDOWS_SUFFIXES,
        )
        plan = depplan.plan(proj, "plot.py", facts=windows, target_kind="project_venv")
        nt = next(c for c in plan.scan["classes"] if c["module"] == "nt")
        # Windows 上 nt 是内建：项目里的 nt.py 永远不被 import，答案确定
        assert (nt["bucket"], nt["origin_kind"], nt["resolution_status"]) == (
            "stdlib",
            "built-in",
            "resolved",
        )
        assert plan.missing == ()


# ---------------------------------------------------------------------------
# 扩展模块后缀
# ---------------------------------------------------------------------------
class TestExtensionSuffixesComeFromTheTarget:
    def _files(self, ext_name: str) -> dict:
        return {SCRIPT: "import fast\n", "fast.py": "import right_one\n", ext_name: b"\x00"}

    def test_a_stale_binary_from_another_platform_is_not_the_provider(self, tmp_path):
        _tree(tmp_path, self._files("fast.cpython-313-darwin.so"))
        res = importscan.scan(tmp_path, SCRIPT, ext_suffixes=LINUX_SUFFIXES)
        c = _by(res)["fast"]
        # Linux 3.11 的 FileFinder 不认它：加载的是 fast.py，它的 import 是真依赖
        assert (c.origin_kind, c.local_path, c.resolution_status) == (
            "project-local",
            "fast.py",
            "resolved",
        )
        assert "right_one" in _by(res)

    def test_the_targets_own_suffix_is_an_extension(self, tmp_path):
        _tree(tmp_path, self._files("fast.cpython-311-x86_64-linux-gnu.so"))
        res = importscan.scan(tmp_path, SCRIPT, ext_suffixes=LINUX_SUFFIXES)
        c = _by(res)["fast"]
        assert (c.origin_kind, c.resolution_status) == ("extension", "resolved")
        assert "right_one" not in _by(res)
        assert res.search_complete is True

    def test_free_threaded_windows_suffixes_are_matched_verbatim(self, tmp_path):
        _tree(tmp_path, self._files("fast.cp313t-win_amd64.pyd"))
        assert (
            _by(importscan.scan(tmp_path, SCRIPT, ext_suffixes=WINDOWS_SUFFIXES))[
                "fast"
            ].origin_kind
            == "extension"
        )
        # 同一个文件在 Linux 目标上不是扩展
        assert (
            _by(importscan.scan(tmp_path, SCRIPT, ext_suffixes=LINUX_SUFFIXES))["fast"].origin_kind
            == "project-local"
        )

    def test_a_non_identifier_stem_is_never_a_module(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import fast\n", "fast-x.so": b"\x00"})
        c = _by(importscan.scan(tmp_path, SCRIPT, ext_suffixes=LINUX_SUFFIXES))["fast"]
        assert c.bucket == "unknown"

    def test_without_target_suffixes_an_extension_is_unconfirmed_and_the_search_incomplete(
        self, tmp_path
    ):
        _tree(tmp_path, self._files("fast.cpython-313-darwin.so"))
        res = importscan.scan(tmp_path, SCRIPT)
        c = _by(res)["fast"]
        assert (c.origin_kind, c.resolution_status) == ("extension", "unverified")
        assert "extension_suffix_not_confirmed" in c.warnings
        assert res.search_complete is False

    def test_no_extension_means_no_penalty_when_suffixes_are_unknown(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import util\n", "util.py": ""})
        res = importscan.scan(tmp_path, SCRIPT)
        assert res.search_complete is True

    def test_depplan_hands_the_target_suffixes_to_the_scan(self, tmp_path):
        proj = _tree(
            tmp_path / "proj",
            {
                "plot.py": "import fast\n",
                "fast.py": "import yaml\n",
                "fast.cpython-313-darwin.so": b"\x00",
            },
        )
        facts = depplan.TargetFacts(
            python="/fake/python",
            marker_env={"python_version": "3.11", "sys_platform": "linux"},
            stdlib=importscan.HOST_STDLIB,
            installed={},
            builtin=frozenset({"posix", "sys"}),
            ext_suffixes=LINUX_SUFFIXES,
        )
        plan = depplan.plan(proj, "plot.py", facts=facts, target_kind="project_venv")
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"]
        assert plan.scan["search_complete"] is True


# ---------------------------------------------------------------------------
# 截断 → 不完整
# ---------------------------------------------------------------------------
class TestTruncatedScansAreNotComplete:
    def _chain(self, root: Path, length: int) -> None:
        files = {SCRIPT: "import m0\n"}
        for i in range(length):
            files[f"m{i}.py"] = f"import m{i + 1}\n"
        files[f"m{length}.py"] = "import numpy\n"
        _tree(root, files)

    def test_a_chain_beyond_max_depth_is_truncated_and_incomplete(self, tmp_path):
        self._chain(tmp_path, importscan.MAX_DEPTH + 2)
        res = importscan.scan(tmp_path, SCRIPT)
        assert res.truncated is True
        assert res.search_complete is False
        assert "numpy" not in _by(res)  # 那个 import 确实没读到——所以不能自称完整

    def test_too_many_local_modules_is_truncated_and_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.setattr(importscan, "MAX_LOCAL_MODULES", 2)
        _tree(
            tmp_path,
            {SCRIPT: "import a\nimport b\nimport c\n", "a.py": "", "b.py": "", "c.py": ""},
        )
        res = importscan.scan(tmp_path, SCRIPT)
        assert res.truncated is True and res.search_complete is False

    def test_a_short_chain_is_complete(self, tmp_path):
        self._chain(tmp_path, 1)
        res = importscan.scan(tmp_path, SCRIPT)
        assert res.truncated is False and res.search_complete is True

    def test_the_plan_payload_says_so(self, tmp_path):
        proj = tmp_path / "proj"
        self._chain(proj, importscan.MAX_DEPTH + 2)
        (proj / "plot.py").write_text("import m0\n", encoding="utf-8")
        plan = depplan.plan(proj, "plot.py", facts=None, target_kind="project_venv")
        assert plan.scan["truncated"] is True and plan.scan["search_complete"] is False


# ---------------------------------------------------------------------------
# 目标事实的量取
# ---------------------------------------------------------------------------
def test_target_facts_measure_the_interpreters_own_builtin_names_and_suffixes():
    depplan.reset_cache()
    facts = depplan.target_facts(sys.executable, use_cache=False)
    assert facts is not None
    assert facts.builtin == frozenset(sys.builtin_module_names)
    assert facts.ext_suffixes == tuple(importlib.machinery.EXTENSION_SUFFIXES)
    # 与计划的输入无关：不改 digest / payload
    assert "builtin" not in facts.to_payload() and "ext_suffixes" not in facts.to_payload()


def test_unmeasured_facts_default_to_unknown_not_to_the_host():
    facts = depplan.TargetFacts(python="", marker_env={}, stdlib=frozenset(), installed={})
    assert facts.builtin == frozenset() and facts.ext_suffixes == ()


def test_the_facts_digest_ignores_builtin_and_suffixes():
    base = dict(python="p", marker_env={"python_version": "3.12"}, stdlib=frozenset(), installed={})
    a = depplan.TargetFacts(**base)
    b = depplan.TargetFacts(**base, builtin=frozenset({"nt"}), ext_suffixes=(".pyd",))
    assert a.digest() == b.digest()


@pytest.mark.parametrize("name", ["posix", "nt"])
def test_platform_modules_are_in_the_ambiguous_set_for_every_host(name):
    assert name in importscan.HOST_BUILTIN_EXTRA
