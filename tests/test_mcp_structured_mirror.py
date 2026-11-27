"""插件工具结果：`structuredContent` 里下一步要用的值，`content` 文字里也要有（MCP 规范；评测实测）。

只把 `content` 交给模型的宿主（opencode 一类）拿不到 `base_revision` / `echo_digest` / 对象 id / `errors`，
`tavotto_layout_edit_plan → _apply` 就走不通。`server.call_tool` 统一在 `content` 末尾追加一个只给助手看的文字块（精简副本）；
这里盯：每个工具（含失败结果）都带得出关键字段、体积有上限、大字段与预览图不进文字、原有文字块一字不动。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto_mcp import bridge, server  # noqa: E402

NEXT_STEP = {
    "base_revision": "rev-1",
    "echo_digest": "dig-1",
    "undo_token": "undo-1",
    "session_id": "s-1",
    "vector_objects": [{"id": "t_note", "type": "text"}, {"id": "a1", "type": "arrow"}],
    "errors": [{"code": "bad_item", "item": "frame", "detail": "fit"}],
}
HUGE = {
    "svg": "<svg>" + "x" * 50000,
    "manifest": {"elements": [{"gid": f"g{i}"} for i in range(5000)]},
}


def _mirrored(result: dict) -> dict:
    blocks = [b for b in result["content"] if b.get("text", "").startswith(server.MIRROR_LABEL)]
    assert len(blocks) == 1, result["content"]
    assert blocks[0]["annotations"] == {"audience": ["assistant"]}
    return json.loads(blocks[0]["text"][len(server.MIRROR_LABEL) :])


@pytest.mark.parametrize("name", sorted(set(server.HANDLERS) - set(server.MIRROR_SKIP_TOOLS)))
def test_every_tool_result_carries_the_next_step_fields_in_content(name, monkeypatch):
    shown = {
        "content": [{"type": "text", "text": "人话一行"}],
        "structuredContent": dict(NEXT_STEP, **HUGE),
    }
    monkeypatch.setitem(server.HANDLERS, name, lambda _args: json.loads(json.dumps(shown)))
    out = server.call_tool(name, {})
    mirror = _mirrored(out)
    for k, v in NEXT_STEP.items():
        assert mirror[k] == v, (name, k)
    assert "svg" not in mirror and "manifest" not in mirror  # 大字段不进文字
    assert out["content"][0] == {"type": "text", "text": "人话一行"}  # 原文字块一字不动
    assert out["structuredContent"]["svg"] == HUGE["svg"]  # structuredContent 本身不动
    assert sum(len(b.get("text", "")) for b in out["content"]) <= server.MIRROR_MAX_CHARS + 2000


@pytest.mark.parametrize("name", sorted(server.MIRROR_SKIP_TOOLS))
def test_the_canvas_tools_keep_their_own_receipt_budget(name, monkeypatch):
    result = {
        "content": [{"type": "text", "text": "x"}],
        "structuredContent": {"session_id": "s-1"},
    }
    assert server._mirror_structured(name, result)["content"] == [{"type": "text", "text": "x"}]  # noqa: SLF001


def test_failures_are_not_mirrored_the_code_stays_out_of_the_text(monkeypatch):
    def boom(_args):
        raise bridge.BridgeError("排版被改过", code="layout_changed", revision="rev-9")

    monkeypatch.setitem(server.HANDLERS, "tavotto_layout_edit_plan", boom)
    out = server.call_tool("tavotto_layout_edit_plan", {})
    assert out["isError"] is True and len(out["content"]) == 1
    assert "layout_changed" not in json.dumps(out["content"], ensure_ascii=False)


def test_images_stay_out_of_the_text_and_text_is_capped():
    big = {
        "vector_objects": [{"id": f"o{i}", "blob": "y" * 300} for i in range(500)],
        "preview_png_base64": "AAAA" * 9999,
    }
    out = server._mirror_structured(  # noqa: SLF001
        "tavotto_layout_scene",
        {
            "content": [{"type": "image", "data": "AAAA", "mimeType": "image/png"}],
            "structuredContent": dict(big, base_revision="rev-2"),
        },
    )
    text = out["content"][-1]["text"]
    assert len(text) <= server.MIRROR_MAX_CHARS + len(server.MIRROR_LABEL)
    assert (
        "AAAA" not in text
        and json.loads(text[len(server.MIRROR_LABEL) :])["base_revision"] == "rev-2"
    )
    assert out["content"][0]["type"] == "image"


def test_a_result_without_structured_content_is_untouched():
    result = {"content": [{"type": "text", "text": "x"}]}
    assert server._mirror_structured("tavotto_health", dict(result)) == result  # noqa: SLF001
