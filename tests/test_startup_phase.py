"""桌面 sidecar 的启动阶段标记（TVD-DT76）。闭集与标记格式同 `tests/golden/desktop_startup_phase.json`（壳那侧另读同一份）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tavotto.engine import startupphase

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "desktop_startup_phase.json").read_text("utf-8")
)


def test_phases_and_marker_are_the_golden_pair():
    assert startupphase.MARKER_PREFIX == GOLDEN["marker_prefix"]
    assert list(startupphase.PHASES) == GOLDEN["phases"]
    assert GOLDEN["none_value"] not in startupphase.PHASES, (
        "none 是壳的『没有标记』，sidecar 不写它"
    )


def test_mark_writes_one_line_of_its_own_to_the_fd(tmp_path):
    log = tmp_path / "sidecar.log"
    with open(log, "ab", buffering=0) as f:
        f.write(b"half a line without newline")
        for phase in startupphase.PHASES:
            startupphase.mark(phase, fd=f.fileno())
    lines = log.read_text("ascii").splitlines()
    assert lines[0] == "half a line without newline", "前一条日志不粘连标记"
    marks = [ln for ln in lines if ln.startswith(GOLDEN["marker_prefix"])]
    assert marks == [GOLDEN["marker_prefix"] + p for p in GOLDEN["phases"]]


def test_mark_ignores_unknown_phases_and_write_failures(tmp_path):
    log = tmp_path / "x.log"
    with open(log, "ab", buffering=0) as f:
        startupphase.mark("not_a_phase", fd=f.fileno())
    assert log.read_bytes() == b""
    r, w = os.pipe()
    os.close(w)
    os.close(r)
    startupphase.mark("serving", fd=r)  # 已关闭的 fd：吞掉 OSError，不影响启动


def test_enabled_only_for_the_desktop_sidecar():
    assert startupphase.enabled(["--desktop-sidecar"])
    assert startupphase.enabled(["--figures", "/x", "--desktop-sidecar"])
    assert not startupphase.enabled(["--figures", "/x"])
    assert not startupphase.enabled([])


def test_the_frozen_entry_marks_interpreter_before_any_heavy_import():
    """打包入口在 import 重模块之前写 `interpreter`：判『解释器有没有跑起来』的唯一依据。"""
    import ast

    entry = Path(__file__).parent.parent / "packaging" / "entry.py"
    tree = ast.parse(entry.read_text("utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    mark_line = next(
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "mark"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "interpreter"
    )
    heavy = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.ImportFrom)
        and n.module
        and n.module.startswith("tavotto")
        and n.module != "tavotto.engine"
        or isinstance(n, ast.ImportFrom)
        and n.module == "tavotto.engine"
        and any(a.name != "startupphase" for a in n.names)
    ]
    assert heavy and mark_line < min(heavy)
