"""导出作业的阶段名 ↔ 前端进度文案（统一实施包 U08 第三切片；RC-088「UI 状态、错误码、双语同步」）。

`exportjob.run` 每换一个阶段就经 SSE `export.progress` 把 `progress.phase` 推给前端，前端按
`dialogs:export.phase.<phase>` 找文案——没有那条键时 i18next 会把键名原样画在进度条上
（「phase.inspecting」）。B 加了 `inspecting` 这一档，这里把「后端会发出的每个阶段名都有两种
语言的文案」钉成判据：后端源码用 AST 抽 `job.phase = "<字面量>"`，前端读两份 `dialogs.json`。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXPORTJOB = ROOT / "src" / "tavotto" / "engine" / "exportjob.py"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

pytestmark = pytest.mark.skipif(
    not (LOCALES / "zh-CN" / "dialogs.json").is_file(),
    reason="没有 web/（wheel/sdist 里不含前端源码）",
)


def _backend_phases() -> set[str]:
    """`job.phase = "..."` 的字面量 + 经 STATUS_* 常量赋的终局名。"""
    tree = ast.parse(EXPORTJOB.read_text(encoding="utf-8"))
    consts: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if (
                isinstance(t, ast.Name)
                and t.id.startswith("STATUS_")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                consts[t.id] = node.value.value
    phases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Attribute) and t.attr == "phase":
                v = node.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    phases.add(v.value)
                elif isinstance(v, ast.Name) and v.id in consts:
                    phases.add(consts[v.id])
                elif isinstance(v, ast.Name) and v.id == "terminal":
                    # `job.phase = terminal`：终局取自 STATUS_DONE / PARTIAL / FAILED / CANCELLED
                    phases |= {
                        consts[k]
                        for k in (
                            "STATUS_DONE",
                            "STATUS_PARTIAL",
                            "STATUS_FAILED",
                            "STATUS_CANCELLED",
                        )
                        if k in consts
                    }
    assert "inspecting" in phases and "writing" in phases, sorted(phases)
    return phases


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_backend_phase_has_progress_text(locale: str):
    table = json.loads((LOCALES / locale / "dialogs.json").read_text(encoding="utf-8"))["export"][
        "phase"
    ]
    missing = sorted(_backend_phases() - set(table))
    assert not missing, f"{locale} 的 dialogs.json export.phase 缺这些阶段的文案：{missing}"
    empty = sorted(k for k, v in table.items() if not str(v).strip())
    assert not empty, empty
