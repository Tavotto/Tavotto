"""Import Origin Resolver PR1：静态来源解析的零执行边界（计划 P7.2 的静态半边）。

判据的主语：**`importscan.scan()` 这一次调用期间**，本进程有没有执行 / 初始化用户代码，有没有起进程、
开网络、改解释器状态。不是「某个用例里没抛」——每条都先把会被碰到的入口换成「调用即失败」的桩，再扫
一棵**会在被执行时留下痕迹**的真实文件树，最后核痕迹文件不存在。

* 用户包 `__init__` 与子模块里写了 `SIDE_EFFECT` 文件：`from dangerous_package import submodule` 扫完它们
  不存在；
* 假 site-packages 里的 `.pth` / `sitecustomize.py` / 包：同理，扫描不读 site-packages、更不执行；
* 换桩的入口：`subprocess.Popen` / `os.system` / `os.exec*` / `os.posix_spawn*` / `os.fork` /
  `socket.socket` / `importlib.util.find_spec` / `importlib.import_module` / `builtins.__import__`（针对用户
  模块名）；
* 解释器状态不变：`sys.path` / `sys.modules`（无用户模块名）/ `os.environ` / cwd；
* 结构：`importscan.py` 的 AST 里没有 `subprocess` / `socket` / `importlib` / `runpy` / `exec` / `eval` /
  `find_spec` / `__import__`，对 `projectenv` / `figcapture` / `depresolve` 只用白名单里的属性。
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import importlib
import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto.engine import importscan, scanbudget

USER_NAMES = {"dangerous_package", "sitecustomize", "usercustomize", "evilpkg", "helper_mod"}


def _write(root: Path, rel: str, text: str = "") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _boom(what: str):
    def fail(*a, **kw):
        raise AssertionError(f"scan reached {what}")

    return fail


@contextlib.contextmanager
def _armed(monkeypatch):
    """扫描期间：起进程 / 联网 / find_spec / 针对用户模块名的 import 一律调用即失败。"""
    monkeypatch.setattr(subprocess, "Popen", _boom("subprocess.Popen"))
    monkeypatch.setattr(os, "system", _boom("os.system"))
    for name in (
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
        "startfile",
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom(f"os.{name}"))
    monkeypatch.setattr(socket, "socket", _boom("socket.socket"))
    monkeypatch.setattr(socket, "create_connection", _boom("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", _boom("socket.getaddrinfo"))
    monkeypatch.setattr(importlib.util, "find_spec", _boom("importlib.util.find_spec"))
    real_import_module = importlib.import_module

    def guarded_import_module(name, package=None):
        if name.split(".", 1)[0] in USER_NAMES:
            raise AssertionError(f"scan imported user module {name!r} via import_module")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", guarded_import_module)
    real_import = builtins.__import__

    def guarded_import(name, *a, **kw):
        if name.split(".", 1)[0] in USER_NAMES:
            raise AssertionError(f"scan imported user module {name!r}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield


@contextlib.contextmanager
def _state_unchanged():
    path, environ, cwd = list(sys.path), dict(os.environ), os.getcwd()
    before = set(sys.modules)
    yield
    assert sys.path == path, "scan changed sys.path"
    assert dict(os.environ) == environ, "scan changed os.environ"
    assert os.getcwd() == cwd, "scan changed the cwd"
    assert not [m for m in set(sys.modules) - before if m.split(".", 1)[0] in USER_NAMES], (
        "scan left a user module in sys.modules"
    )


def _world(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    """一个会在被执行时留下痕迹的项目：用户包、子模块、假 venv 里的 .pth / sitecustomize / 包。"""
    proj = tmp_path / "proj"
    marks = {
        name: tmp_path / f"SIDE_EFFECT_{name}"
        for name in (
            "pkg_init",
            "submodule",
            "pth",
            "sitecustomize",
            "evilpkg",
            "usercustomize",
            "helper",
        )
    }

    def writer(key: str) -> str:
        return f"open({str(marks[key])!r}, 'w').write('x')\n"

    _write(proj, "dangerous_package/__init__.py", writer("pkg_init") + "import scipy\n")
    _write(proj, "dangerous_package/submodule.py", writer("submodule") + "import h5py\n")
    site = proj / ".venv" / "lib" / "python3.13" / "site-packages"
    _write(site, "evil.pth", f"import os; open({str(marks['pth'])!r}, 'w').write('x')\n")
    _write(site, "sitecustomize.py", writer("sitecustomize"))
    _write(site, "evilpkg/__init__.py", writer("evilpkg"))
    _write(proj, "usercustomize.py", writer("usercustomize"))
    _write(proj, "helper_mod.py", writer("helper") + "import torch\n")
    _write(
        proj,
        "s.py",
        "from dangerous_package import submodule\n"
        "import sitecustomize\nimport usercustomize\nimport evilpkg\nimport helper_mod\n",
    )
    return proj, marks


class TestScanRunsNothing:
    def test_from_dangerous_package_import_submodule_executes_neither_file(
        self, tmp_path, monkeypatch
    ):
        proj, marks = _world(tmp_path)
        with _armed(monkeypatch), _state_unchanged():
            res = importscan.scan(proj, "s.py")
        # 扫描是真的走了这些文件（静态读源码），所以第三方依赖看得见——但一行都没执行
        got = {c.module: c for c in res.classes}
        assert got["scipy"].via == ("dangerous_package/__init__.py",)
        assert got["h5py"].via == ("dangerous_package/submodule.py",)
        assert got["torch"].via == ("helper_mod.py",)
        assert [m for m, p in marks.items() if p.exists()] == []

    def test_no_follow_mode_runs_nothing_either(self, tmp_path, monkeypatch):
        proj, marks = _world(tmp_path)
        with _armed(monkeypatch), _state_unchanged():
            importscan.scan(proj, "s.py", no_follow=True)
        assert [m for m, p in marks.items() if p.exists()] == []

    def test_pth_and_sitecustomize_in_a_fake_site_packages_are_never_read_or_run(
        self, tmp_path, monkeypatch
    ):
        proj, marks = _world(tmp_path)
        site = proj / ".venv" / "lib" / "python3.13" / "site-packages"
        opened: list[str] = []
        real_open = os.open

        def spy_open(path, *a, **kw):
            opened.append(os.fspath(path))
            return real_open(path, *a, **kw)

        monkeypatch.setattr(os, "open", spy_open)
        with _armed(monkeypatch), _state_unchanged():
            importscan.scan(proj, "s.py")
        assert not [p for p in opened if str(site) in p], "scan opened files in site-packages"
        assert not (
            marks["pth"].exists() or marks["sitecustomize"].exists() or marks["evilpkg"].exists()
        )

    def test_a_python_m_entry_runs_nothing_either(self, tmp_path, monkeypatch):
        proj, marks = _world(tmp_path)
        _write(proj, "dangerous_package/__main__.py", "from . import submodule\n")
        entry = importscan.Entry(kind="module", module="dangerous_package", cwd_mode="project_root")
        with _armed(monkeypatch), _state_unchanged():
            res = importscan.scan(proj, "", entry=entry)
        assert "dangerous_package/__main__.py" in res.files
        assert [m for m, p in marks.items() if p.exists()] == []

    def test_the_armed_traps_are_real(self, tmp_path, monkeypatch):
        """反向钉：桩确实会在被碰到时失败——否则上面几条全绿也什么都证明不了。"""
        with _armed(monkeypatch):
            with pytest.raises(AssertionError, match="Popen"):
                subprocess.Popen(["true"])
            with pytest.raises(AssertionError, match="find_spec"):
                importlib.util.find_spec("json")
            with pytest.raises(AssertionError, match="user module"):
                __import__("dangerous_package")
            with pytest.raises(AssertionError, match="socket"):
                socket.socket()
            with pytest.raises(AssertionError, match="user module"):
                importlib.import_module("evilpkg")
            __import__("json")  # 非用户模块名照常


_FORBIDDEN_NAMES = {
    "subprocess",
    "socket",
    "importlib",
    "runpy",
    "exec",
    "eval",
    "find_spec",
    "__import__",
    "Popen",
    "system",
    "urllib",
    "requests",
    "pip",
}
_ALLOWED_IMPORTS = {
    "ast",
    "dataclasses",
    "os",
    "re",
    "sys",
    "pathlib",
    "__future__",
}
_ALLOWED_SIBLINGS = {"depresolve", "execspec", "figcapture", "projectenv", "scanbudget"}
#: 对兄弟模块只许用这些属性（projectenv 里有起解释器的 `probe_environment`，不许碰）。
_ALLOWED_ATTRS = {
    "projectenv": {"within"},
    "figcapture": {"PROFILE_SAFE", "unused_imports", "reaches_main"},
    "execspec": {
        "TARGET_SCRIPT",
        "TARGET_MODULE",
        "TARGET_KINDS",
        "PROFILES",
        "CWD_MODES",
        "CWD_PROJECT_ROOT",
        "CWD_SANDBOX",
    },
    "depresolve": {
        "valid_import_name",
        "curated_distribution",
        "normalize_distribution",
        "SOURCE_PROJECT_DECLARED",
        "SOURCE_CURATED",
    },
    "scanbudget": {
        "Budget",
        "ISSUE_UNREADABLE_DIR",
        "ISSUE_PARSE_FAILED",
        "ISSUE_PLACEHOLDER",
        "ISSUE_TOO_LARGE",
        "ISSUE_SYMLINK_DIR",
        "is_placeholder",
        "read_regular_text",
        "redirected_component",
    },
}


class TestStructuralGate:
    TREE = ast.parse(Path(importscan.__file__).read_text(encoding="utf-8"))

    def test_the_module_imports_only_the_allowed_modules(self):
        seen: set[str] = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Import):
                seen |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level == 1:
                    seen |= {f"sibling:{a.name}" for a in node.names}
                else:
                    seen.add((node.module or "").split(".")[0])
        siblings = {s.split(":", 1)[1] for s in seen if s.startswith("sibling:")}
        assert siblings == _ALLOWED_SIBLINGS
        assert {s for s in seen if not s.startswith("sibling:")} <= _ALLOWED_IMPORTS

    def test_no_process_network_or_dynamic_execution_name_appears_in_the_code(self):
        found = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
                found.add(node.attr)
        assert found == set()

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
        assert used["projectenv"] == {"within"}  # 前提：这个门禁真的在看 projectenv


class TestCostScalesWithTheImportGraph:
    """性能 fixture 的静态半边（计划 P7.7：5 / 30 / 100 个脚本）。扫一个脚本只读它的导入闭包，不是整棵树。"""

    @pytest.mark.parametrize("scripts", [5, 30, 100])
    def test_one_scan_reads_only_its_import_closure(self, tmp_path, monkeypatch, scripts):
        proj = tmp_path / "proj"
        for i in range(scripts):
            _write(proj, f"scripts/fig{i:03d}.py", "import numpy\nimport shared\nimport lab.util\n")
        _write(proj, "scripts/shared.py", "import scipy\n")
        _write(proj, "scripts/lab/__init__.py", "")
        _write(proj, "scripts/lab/util.py", "import h5py\n")
        for i in range(40):  # 与脚本无关的模块：不能被读
            _write(proj, f"scripts/unrelated{i}.py", "import torch\n" * 20)
        t0 = time.perf_counter()
        total_files = 0
        with _armed(monkeypatch):
            for i in range(scripts):
                budget = scanbudget.Budget()
                res = importscan.scan(proj, f"scripts/fig{i:03d}.py", budget=budget)
                closure = {
                    f"scripts/fig{i:03d}.py",
                    "scripts/shared.py",
                    "scripts/lab/__init__.py",
                    "scripts/lab/util.py",
                }
                assert set(res.files) == closure
                read = sum((proj / f).stat().st_size for f in res.files)
                assert budget.snapshot()["source_bytes"] == read  # 读的恰好是闭包，一个字节不多
                total_files += len(res.files)
                assert {"scipy", "h5py", "numpy"} <= {c.module for c in res.classes}
        elapsed = time.perf_counter() - t0
        assert total_files == scripts * 4
        assert elapsed < 30, f"{scripts} scripts took {elapsed:.2f}s"
