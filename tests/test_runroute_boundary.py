"""V2 PR2：运行前路由的边界——零执行、只在用户发起的路径里被调用（ADR 0133）。

* `runroute` 只做 `ast.parse` 与文本运算：不 import 用户代码、不起进程、不连网络、不 `find_spec`；
* 被动扫描（项目打开 / 刷新 / 清单）一次都不调它；除准备计划（`preparation`）外没有任何模块 import 它；
* CLI / MCP 路径不 import 它（接线用例里还钉了 `plan_for` 默认不路由）。
"""

from __future__ import annotations

import ast
import builtins
import importlib
import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import (
    discover as engine_discover,
    probe as engine_probe,
    project_refresh as engine_refresh,
    projscan,
    registry as engine_registry,
    runroute,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "tavotto"
posix_only = pytest.mark.skipif(os.name == "nt", reason="哨兵写文件用 POSIX 路径")

#: 里面的每一行用户代码被执行都会在哨兵目录留下文件；`runroute` 读到它只许当文本
HOSTILE = """\
import definitely_not_installed_pkg_zero_exec
import os, sys
from pathlib import Path
Path({sentinel!r}, "ran").write_text("x")
open({sentinel!r} + "/opened", "w").write("x")
os.system("touch " + {sentinel!r} + "/system")
import matplotlib.pyplot as plt
from PIL import Image
plt.savefig("t.png")
Image.open("t.png")
"""


def _boom(name, calls):
    def inner(*a, **k):
        calls.append(name)
        raise AssertionError(f"runroute reached {name}")

    return inner


@posix_only
def test_routing_a_hostile_script_executes_nothing_and_touches_no_process_or_import_machinery(
    tmp_path, monkeypatch
):
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    script = tmp_path / "proj" / "fig.py"
    script.parent.mkdir()
    script.write_text(HOSTILE.format(sentinel=str(sentinel)), encoding="utf-8")
    runroute.clear_cache()
    calls: list[str] = []
    monkeypatch.setattr(subprocess.Popen, "__init__", _boom("Popen", calls))
    for name in ("system", "execv", "execve", "execvp", "fork", "popen", "posix_spawn"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom(f"os.{name}", calls))
    monkeypatch.setattr(socket.socket, "connect", _boom("socket.connect", calls))
    monkeypatch.setattr(importlib.util, "find_spec", _boom("find_spec", calls))
    monkeypatch.setattr(importlib, "import_module", _boom("import_module", calls))
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name == "definitely_not_installed_pkg_zero_exec":
            calls.append("import:" + name)
            raise AssertionError("runroute imported a user module")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guarded)

    route = runroute.route_for_file(
        script, cwd_mode="project", project_root=script.parent, script="fig.py"
    )

    assert calls == []
    assert list(sentinel.iterdir()) == []
    # 它确实读懂了这份脚本（R1 证据在），只是没执行它；脚本里有守卫操作（Path.write_text），伤害闸门把路由留在 safe
    assert "R1" in {e["rule"] for e in route.evidence}
    assert route.execution == "safe" and route.reason == runroute.REASON_GUARDED_FILE_OPS


def test_runroute_imports_only_the_standard_library():
    tree = ast.parse((SRC / "engine" / "runroute.py").read_text("utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "runroute 不 import 兄弟模块（纯标准库、不进 import 环）"
            if node.module == "tavotto.engine" and {a.name for a in node.names} <= {
                "figcapture",
                "scriptargs",
            }:
                # 仅有的例外：守卫操作表 `BlockedFileOps.OPS` 的出处，与 PEP 263 源码解码的唯一实现（两者都只依赖标准库，不会成环）
                continue
            mods.add((node.module or "").split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    assert mods <= stdlib, mods - stdlib
    assert not mods & {"subprocess", "socket", "importlib", "urllib", "multiprocessing"}


def test_only_the_preparation_layer_imports_runroute():
    importers = []
    for path in SRC.rglob("*.py"):
        if path.name == "runroute.py":
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").endswith("runroute") or any(
                    a.name == "runroute" for a in node.names
                ):
                    importers.append(path.relative_to(SRC).as_posix())
            elif isinstance(node, ast.Import) and any(
                a.name.endswith("runroute") for a in node.names
            ):
                importers.append(path.relative_to(SRC).as_posix())
    assert sorted(set(importers)) == ["engine/preparation.py"]


def test_passive_project_scanning_never_calls_the_router(tmp_path, monkeypatch):
    """项目打开 / 刷新 / 清单 / 被动扫描：路由器的每一个入口都换成会炸的桩，整条被动链路照常跑完。"""
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text(
        "import matplotlib.pyplot as plt\nplt.savefig('t.png')\nopen('t.png','rb')\n", "utf-8"
    )
    calls: list[str] = []
    for name in ("route_for_file", "facts_of_file", "facts_of", "decide"):
        monkeypatch.setattr(runroute, name, _boom(f"runroute.{name}", calls))

    class Ctx:
        path = root
        id = "pj-route-boundary"
        registry = engine_registry.Registry()

    report = projscan.scan(root)
    engine_discover.write_config(root, engine_discover.build_draft(root)[0])
    Ctx.registry.load(root)
    engine_refresh.refresh_project_index(Ctx, reason="open")
    rows = engine_probe.script_inventory(root, registered=set())

    assert calls == []
    assert {t["script"] for t in report["targets"]} == {"a.py"}
    assert {r["script"] for r in rows} == {"a.py"}
