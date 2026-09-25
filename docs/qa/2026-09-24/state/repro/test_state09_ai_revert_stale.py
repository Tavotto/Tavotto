"""STATE-09 复现（**预期红 = 产品缺陷**，不进测试集）：AI 会话的「回滚」是一次**不带版本的写**。

`ai_bridge.revert(sid)` 把会话开始前的快照无条件原子写回脚本——不核对脚本此刻是不是还是
这次 AI 改完的那一版。AI 改完之后人工又改了脚本（或者第二个 AI 会话又改了一遍），再从
历史列表里点这次会话的「回滚」，后来的改动被静默覆盖，接口回 `{"ok": true}`，界面报
「已回滚」（`AiPanel` 历史条目上的按钮没有确认框）。

QA 规范 §2 STATE-09 的验收：所有更改绑定项目与版本；过期写入被冲突拒绝或按明确规则协调，
**不静默覆盖新状态**。

跑法（从 worktree 根目录）：
    docs/qa/2026-09-24/state/repro/run.sh pytest docs/qa/2026-09-24/state/repro/test_state09_ai_revert_stale.py
"""

from __future__ import annotations

import pytest

from tavotto.engine import ai_bridge

BEFORE_AI = b"import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4])\n"
AFTER_AI = b"import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4], color='red')\n"
HUMAN_LATER = (
    b"import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4], color='red')\nplt.title('mine')\n"
)


@pytest.fixture
def ai_session(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    script = project / "fig1.py"
    script.write_bytes(AFTER_AI)  # AI 会话结束时磁盘上的样子
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    snap = snaps / "s1__fig1.py"
    snap.write_bytes(BEFORE_AI)  # 会话开始前的快照
    sid = "s1"
    ai_bridge.SESSIONS[sid] = {
        "id": sid,
        "script": "fig1.py",
        "script_path": str(script),
        "project": str(project.resolve()),
        "snapshot": str(snap),
        "status": "done",
    }
    monkeypatch.setattr(ai_bridge.ai_history, "update_status", lambda *a, **k: None)
    try:
        yield sid, script
    finally:
        ai_bridge.SESSIONS.pop(sid, None)


def test_revert_after_a_later_human_edit_must_not_silently_discard_it(ai_session):
    sid, script = ai_session
    script.write_bytes(HUMAN_LATER)  # AI 之后人工又改了一版

    try:
        result = ai_bridge.revert(sid)
    except Exception as exc:  # 明确拒绝（冲突）是可接受的合同
        assert script.read_bytes() == HUMAN_LATER, f"拒绝了却改了文件：{exc}"
        return
    # 没拒绝：那就必须没有把人工那一版覆盖掉
    assert script.read_bytes() == HUMAN_LATER, (
        f"revert 返回 {result}，人工在 AI 之后的改动被快照静默覆盖"
    )


def test_control_revert_right_after_the_ai_edit_restores_the_snapshot(ai_session):
    """对照组：脚本仍是 AI 改完那一版时，回滚本来就该恢复快照（这条应当绿）。"""
    sid, script = ai_session
    assert ai_bridge.revert(sid) == {"ok": True, "script": "fig1.py"}
    assert script.read_bytes() == BEFORE_AI
