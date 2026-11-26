"""AI 修复的任务清单进度（用户 2026-11-25 拍板「照视频的设计」）：「找原因 → 动手修 → 运行验证 → 收尾」四步。

判据的主语：**会话对外的公开状态**里的 `stage`（`SESSIONS.get(...)["stage"]`），以及给它定步骤的纯函数
`fixtools.next_stage` 与归步表 `STAGE_OF_TOOL`。步骤按工具**所在的组**归，不靠模型列计划；
注册表里每个工具要么归一步、要么明确登记为不推进——新加工具漏归类在这里红。
"""

# ruff: noqa: F811 — 夹具从 test_fixmcp 导入复用
from __future__ import annotations

import threading

import pytest

from tavotto.engine import fixtools
from tavotto.engine.fixtools import (
    FIX_STAGES,
    REGISTRY,
    STAGE_NEUTRAL_TOOLS,
    STAGE_OF_TOOL,
    FixTool,
    next_stage,
)
from tavotto.engine.fixtools.base import obj, ok
from test_fixmcp import box, wait_finished  # noqa: F401
from test_fixstall import Scripted, public, start_builtin


def test_fix_stage_of_tool_covers_registry():
    """每个注册的工具要么归到四步之一，要么在不推进名单里；两张表不重叠、不多出注册表外的名字。"""
    assert set(STAGE_OF_TOOL) | STAGE_NEUTRAL_TOOLS == set(REGISTRY)
    assert not set(STAGE_OF_TOOL) & STAGE_NEUTRAL_TOOLS
    assert set(STAGE_OF_TOOL.values()) <= set(FIX_STAGES)


def test_tool_groups_map_to_the_steps_the_user_sees():
    assert STAGE_OF_TOOL["read_run_report"] == "diagnose"
    assert STAGE_OF_TOOL["lookup_package"] == "diagnose"
    for t in (
        "install_packages",
        "change_python",
        "link_data_file",
        "ignore_import_folder",
        "run_command",
        "rollback",
        "propose_fix",
    ):
        assert STAGE_OF_TOOL[t] == "fix", t
    for t in ("try_import", "run_snippet", "try_run"):
        assert STAGE_OF_TOOL[t] == "verify", t
    assert STAGE_OF_TOOL["finish"] == "finish"


def _walk(*tools: str) -> list[tuple[str | None, int]]:
    cur, rnd, out = None, 1, []
    for t in tools:
        cur, rnd = next_stage(cur, rnd, t)
        out.append((cur, rnd))
    return out


def test_steps_only_move_forward_and_a_return_to_fixing_after_verifying_is_a_new_round():
    assert _walk(
        "read_run_report",  # 找原因
        "ask_user",  # 不推进
        "install_packages",  # 动手修
        "read_file",  # 修着修着又看一眼：停在动手修
        "try_run",  # 运行验证
        "read_logs",  # 验证完读日志：停在验证
        "set_run_arguments",  # 又回去修：第 2 轮
        "try_run",
        "finish",
    ) == [
        ("diagnose", 1),
        ("diagnose", 1),
        ("fix", 1),
        ("fix", 1),
        ("verify", 1),
        ("verify", 1),
        ("fix", 2),
        ("verify", 2),
        ("finish", 2),
    ]


def test_unknown_or_neutral_first_tool_leaves_stage_empty_and_skipping_ahead_is_allowed():
    assert _walk("pick_file", "not_a_tool") == [(None, 1), (None, 1)]
    # 一上来就试运行：直接到验证（前面的步骤由界面算作做过）
    assert _walk("try_run") == [("verify", 1)]


def test_stage_is_in_the_public_state_and_follows_tool_starts(box, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(_c, _a):
        entered.set()
        release.wait(20)
        return ok("x")

    # 一个归在「运行验证」的慢工具：开始的那一刻公开状态就到了验证
    monkeypatch.setitem(REGISTRY, "slow_trial", FixTool("slow_trial", "d", obj({}), slow))
    monkeypatch.setitem(fixtools.STAGE_OF_TOOL, "slow_trial", "verify")
    s = start_builtin(
        box,
        Scripted(
            [
                ("read_run_report", {}),
                ("slow_trial", {}),
                ("finish", {"outcome": "fixed", "summary": "ok"}),
            ]
        ),
    )
    assert entered.wait(10)
    assert public(s)["stage"] == {"step": "verify", "round": 1}
    release.set()
    wait_finished(s, 10)
    assert public(s)["stage"] == {"step": "finish", "round": 1}


@pytest.mark.parametrize("tool", sorted(STAGE_NEUTRAL_TOOLS))
def test_neutral_tools_never_set_a_stage(tool):
    assert next_stage(None, 1, tool) == (None, 1)
    assert next_stage("fix", 2, tool) == ("fix", 2)
