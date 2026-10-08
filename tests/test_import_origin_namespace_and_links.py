"""命名空间目录的真实判据 + 目录链接先守卫再列清单（Codex 第一轮 P1-b / P1-c）。

P1-b：CPython `FileFinder.find_spec` 里，同名、无 `__init__` 的**任何**目录（空的、只有数据文件的）都是
命名空间部分；同一搜索根内优先级 常规包 > 扩展 > .py > 命名空间，命名空间只在**所有**搜索根都找不到常规
模块时才成立（PathFinder 逐项收集 portion）。
P1-c：命中的目录项如果是符号链接 / junction，在 `scandir` 它之前就要先不跟随地 `lstat`——目标可能在项目外，
Windows 上可能是 UNC / WebDAV，光 `scandir` 就会联网。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import importscan, scanbudget


def _by(res):
    return {c.module: c for c in res.classes}


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class TestNamespaceIsAnyDirectory:
    @pytest.mark.parametrize("files", [{"foo/.keep": ""}, {"foo/data.csv": "a,b\n"}])
    def test_empty_or_data_only_directory_is_a_local_namespace_not_third_party(
        self, tmp_path, files
    ):
        _tree(tmp_path, {"s.py": "import foo\n", **files})
        res = importscan.scan(tmp_path, "s.py", declared={"foo": ">=1"})
        c = _by(res)["foo"]
        assert c.bucket == "local" and c.origin_kind == "namespace"
        assert "namespace_may_be_overridden" in c.warnings
        assert not c.needed

    def test_a_truly_empty_directory_too(self, tmp_path):
        (tmp_path / "foo").mkdir()
        _tree(tmp_path, {"s.py": "import foo\n"})
        c = _by(importscan.scan(tmp_path, "s.py", declared={"foo": ">=1"}))["foo"]
        assert c.bucket == "local" and c.origin_kind == "namespace"

    def test_real_interpreter_agrees(self, tmp_path):
        (tmp_path / "foo").mkdir()
        (tmp_path / "foo" / "data.csv").write_text("a\n", encoding="utf-8")
        _tree(tmp_path, {"s.py": "import foo\nprint(foo.__file__, list(foo.__path__)[0] != '')\n"})
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        proc = subprocess.run(
            [sys.executable, "s.py"], cwd=tmp_path, env=env, capture_output=True, text=True
        )
        assert proc.returncode == 0 and proc.stdout.split()[0] == "None", proc.stderr

    def test_a_regular_module_in_a_later_root_beats_an_earlier_namespace_dir(self, tmp_path):
        """script_dir 里是命名空间目录、项目根里是常规 `foo.py`：真实 PathFinder 取常规模块。"""
        _tree(tmp_path, {"sub/s.py": "import foo\n", "sub/foo/data.csv": "", "foo.py": "X = 1\n"})
        c = _by(importscan.scan(tmp_path, "sub/s.py"))["foo"]
        assert c.origin_kind != "namespace" and c.local_path == "foo.py"
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env["PYTHONPATH"] = str(tmp_path)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'sub'); import foo; print(foo.__file__)",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.stdout.strip().endswith("foo.py"), proc.stderr

    def test_a_package_beats_an_extension_beats_a_py_beats_a_namespace_in_one_root(self, tmp_path):
        _tree(tmp_path, {"s.py": "import a, b\n", "a/__init__.py": "", "b.py": "", "b/x.csv": ""})
        got = _by(importscan.scan(tmp_path, "s.py"))
        assert got["a"].local_path == "a/__init__.py"
        assert got["b"].local_path == "b.py" and got["b"].origin_kind != "namespace"

    def test_a_stdlib_regular_package_still_beats_a_local_namespace_dir(self, tmp_path):
        _tree(tmp_path, {"s.py": "import statistics\n", "statistics/x.csv": ""})
        c = _by(importscan.scan(tmp_path, "s.py"))["statistics"]
        assert c.bucket == "stdlib"


class _ScandirSpy:
    def __init__(self, monkeypatch):
        self.paths: list[str] = []
        real = os.scandir

        def spy(path="."):
            self.paths.append(os.path.abspath(os.fsdecode(os.fspath(path))))
            return real(path)

        monkeypatch.setattr(os, "scandir", spy)

    def touched(self, target: Path) -> bool:
        t = {os.path.abspath(target), os.path.realpath(target)}
        return any(p == x or p.startswith(x + os.sep) for p in self.paths for x in t)


def _symlink_dir(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"no symlinks: {exc}")


class TestDirectoryLinksAreGuardedBeforeListing:
    def _world(self, tmp_path):
        proj = tmp_path / "proj"
        outside = tmp_path / "outside"
        proj.mkdir()
        _tree(outside, {"ns/mod.py": "import secret\n"})
        _tree(proj, {"s.py": "import linked\n"})
        return proj, outside

    def test_symlinked_dir_to_outside_is_never_scanned_in_follow_mode(self, tmp_path, monkeypatch):
        proj, outside = self._world(tmp_path)
        _symlink_dir(proj / "linked", outside / "ns")
        spy = _ScandirSpy(monkeypatch)
        res = importscan.scan(proj, "s.py")
        assert not spy.touched(outside)
        c = _by(res)["linked"]
        assert c.resolution_status == "unverified" and "link_outside_project" in c.warnings
        assert res.search_complete is False

    def test_symlinked_dir_is_not_descended_under_no_follow_even_inside_the_project(
        self, tmp_path, monkeypatch
    ):
        _tree(tmp_path, {"s.py": "import linked\n", "real/ns/mod.py": "import numpy\n"})
        _symlink_dir(tmp_path / "linked", tmp_path / "real" / "ns")
        spy = _ScandirSpy(monkeypatch)
        res = importscan.scan(tmp_path, "s.py", no_follow=True)
        assert not any(p.endswith(os.sep + "linked") for p in spy.paths)
        assert "numpy" not in _by(res)
        assert res.search_complete is False
        assert any(i["code"] == scanbudget.ISSUE_SYMLINK_DIR for i in res.issues)

    def test_a_windows_junction_is_never_listed_in_either_mode(self, tmp_path, monkeypatch):
        """非 Windows 上用假的 lstat 结果模拟 junction（reparse 位 + name-surrogate tag，不是 S_ISLNK）。"""
        proj, outside = self._world(tmp_path)
        (proj / "linked").mkdir()  # 占位目录；lstat 被换成 junction 的样子
        (proj / "linked" / "mod.py").write_text("import secret\n", encoding="utf-8")
        real_lstat = os.lstat
        target = os.path.abspath(proj / "linked")

        class Fake:
            def __init__(self, st):
                self.st_mode = st.st_mode
                self.st_size = 0
                self.st_file_attributes = 0x400 | 0x10
                self.st_reparse_tag = 0xA0000003

        def fake_lstat(path, *a, **kw):
            st = real_lstat(path, *a, **kw)
            if os.path.abspath(os.fspath(path)) == target:
                return Fake(st)
            return st

        monkeypatch.setattr(os, "lstat", fake_lstat)
        for no_follow in (False, True):
            spy = _ScandirSpy(monkeypatch)
            res = importscan.scan(proj, "s.py", no_follow=no_follow)
            assert not spy.touched(proj / "linked")
            assert "link_not_followed" in _by(res)["linked"].warnings
            assert res.search_complete is False

    def test_a_symlink_inside_the_project_is_still_followed_by_default(self, tmp_path):
        _tree(tmp_path, {"s.py": "import linked\n", "real/ns/mod.py": "import numpy\n"})
        (tmp_path / "real" / "ns" / "__init__.py").write_text("", encoding="utf-8")
        _symlink_dir(tmp_path / "linked", tmp_path / "real" / "ns")
        res = importscan.scan(tmp_path, "s.py")
        assert _by(res)["linked"].bucket == "local"
