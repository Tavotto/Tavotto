"""Tavotto wrapper 先于用户路径预加载的标准库模块（Codex 第一轮 P1-a）。

主语：**执行脚本的那个进程是 Tavotto 的 wrapper**（safe：`engine/worker.py`；native：
`engine/bridge_runner.py`），不是裸的 `python script.py`。wrapper 在把用户目录插进 `sys.path` 之前
已 `import json` 等，所以用户脚本里的 `import json` 命中 `sys.modules` 缓存，项目里的 `json.py` 永远到不了，
它里面的 import 也不会被执行。`importscan.WRAPPER_PRELOADED` 是这件事的单一出处；这里用 AST 重算它，
wrapper 改了 import 而表没同步就红。

裸 `python script.py` 的对拍（另一个进程）留在 `test_import_origin_static.py::TestRealPythonParity`，
那边把这张表清空再比。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import figcapture, importscan

ENGINE = Path(importscan.__file__).resolve().parent
STDLIB = set(sys.stdlib_module_names) | {"__future__"}


def _module_level_imports(path: Path) -> set[str]:
    """模块层（含模块层的 if / try 分支，不含函数体）import 的顶层模块名。"""
    out: set[str] = set()

    def walk(body):
        for node in body:
            if isinstance(node, ast.Import):
                out.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                out.add(node.module.split(".")[0])
            elif isinstance(node, (ast.If, ast.Try)):
                walk(node.body)
                walk(node.orelse)
                walk(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    walk(handler.body)

    walk(ast.parse(path.read_text(encoding="utf-8")).body)
    return out


def _tuple_constant(path: Path, name: str) -> list[str]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return [e.value for e in node.value.elts]
    raise AssertionError(f"{path.name} 里找不到 {name}")


def _closure(paths: list[Path], engine_modules: list[str]) -> set[str]:
    mods = set()
    for p in paths:
        mods |= _module_level_imports(p)
    for m in engine_modules:
        mods |= _module_level_imports(ENGINE / f"{m}.py")
    return mods & STDLIB


class TestTableMatchesTheWrappers:
    def test_safe_table_is_the_worker_py_closure(self):
        worker = ENGINE / "worker.py"
        want = _closure([worker], _tuple_constant(worker, "_ENGINE_MODULES"))
        assert importscan.WRAPPER_PRELOADED[figcapture.PROFILE_SAFE] == want

    def test_native_table_is_the_bridge_runner_py_closure(self):
        runner = ENGINE / "bridge_runner.py"
        want = _closure([runner, ENGINE / "bridgeboot.py"], _tuple_constant(runner, "_PHASE1"))
        assert importscan.WRAPPER_PRELOADED[figcapture.PROFILE_NATIVE] == want

    def test_the_tables_contain_json_which_both_wrappers_import_first(self):
        for profile in (figcapture.PROFILE_SAFE, figcapture.PROFILE_NATIVE):
            assert "json" in importscan.WRAPPER_PRELOADED[profile]

    def test_phase2_engine_modules_are_not_counted_for_native(self):
        """native 的第二阶段在用户 import 之后才装：它们的 import 不算预加载。"""
        runner = ENGINE / "bridge_runner.py"
        phase1 = set(_tuple_constant(runner, "_PHASE1"))
        phase2 = [m for m in _tuple_constant(runner, "_PHASE2") if m not in phase1]
        assert phase2  # 前提：确有第二阶段模块
        only_phase2 = _closure([], phase2) - _closure(
            [runner, ENGINE / "bridgeboot.py"], list(phase1)
        )
        assert not (only_phase2 & importscan.WRAPPER_PRELOADED[figcapture.PROFILE_NATIVE])


@pytest.mark.parametrize("profile", [figcapture.PROFILE_SAFE, figcapture.PROFILE_NATIVE])
class TestWrapperSemantics:
    def test_a_local_json_py_is_ignored_and_its_imports_are_not_followed(self, tmp_path, profile):
        (tmp_path / "s.py").write_text("import json\nimport numpy\n", encoding="utf-8")
        (tmp_path / "json.py").write_text("import requests\n", encoding="utf-8")
        res = importscan.scan(
            tmp_path, "s.py", declared={"numpy": ""}, entry=importscan.Entry(profile=profile)
        )
        got = {c.module: c for c in res.classes}
        assert (got["json"].bucket, got["json"].origin_kind) == ("stdlib", "stdlib")
        assert got["json"].shadowing == "stdlib"
        assert "local_file_never_imported" in got["json"].warnings
        assert got["json"].evidence == ("preloaded_stdlib",)
        assert "requests" not in got and not any(
            c.needed for c in got.values() if c.module != "numpy"
        )
        assert res.files == ("s.py",)

    def test_a_name_the_wrapper_never_imported_is_still_shadowable(self, tmp_path, profile):
        (tmp_path / "s.py").write_text("import colorsys\n", encoding="utf-8")
        (tmp_path / "colorsys.py").write_text("import requests\n", encoding="utf-8")
        res = importscan.scan(tmp_path, "s.py", entry=importscan.Entry(profile=profile))
        got = {c.module: c for c in res.classes}
        assert got["colorsys"].bucket == "local" and "requests" in got


def test_a_real_interpreter_with_the_wrapper_order_agrees(tmp_path):
    """另一个进程：先 import json 再把项目目录插到 sys.path 最前（wrapper 的顺序）——拿到的是 stdlib 的 json。"""
    (tmp_path / "json.py").write_text("raise SystemExit('local json.py executed')\n", "utf-8")
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "import json as again\n"
        "print(again is json, 'json.py' in (again.__file__ or '').rsplit('/', 1)[-1:])\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["True", "False"]
