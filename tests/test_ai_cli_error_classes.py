"""Codex / Claude CLI 失败归类（结构化字段 → RUNNER_ERRORS）与前后端「错误码 → 主按钮」同源对。

向量在 tests/golden/ai_error_action_vectors.json；前端那一侧是 web/src/components/ai/transcriptModel.test.ts。
"""

import json
from pathlib import Path

import pytest

from tavotto.engine import ai_agents, ai_bridge

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "ai_error_action_vectors.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("v", GOLDEN["cli_vectors"], ids=lambda v: v["name"])
def test_cli_failure_event_is_classified_from_structured_fields(v):
    st: dict = {}
    ai_bridge._classify(v["agent"], json.dumps(v["event"]), st)
    assert st.get("runner_error") == v["expect"]
    if v["expect"]:
        assert v["expect"] in ai_agents.RUNNER_ERRORS


def test_free_text_never_decides():
    # 自由文本里写满关键词也不判；也不保留原文
    st: dict = {}
    line = json.dumps(
        {"type": "turn.failed", "error": {"message": "401 unauthorized 429 quota network down"}}
    )
    ai_bridge._classify("codex", line, st)
    assert "runner_error" not in st


def test_action_table_covers_every_code_the_failure_card_can_show():
    actions = GOLDEN["actions"]
    assert set(actions.values()) <= {"check_connection", "retry"}
    shown = (ai_agents.RUNNER_ERRORS - {"no_done_event"}) | {"agent_exit_nonzero", "timeout"}
    assert shown <= set(actions), shown - set(actions)
