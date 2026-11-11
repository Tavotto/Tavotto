"""v2 source facts over MCP: faithful pagination, privacy and explicit failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import journalfacts_remote as facts, journalremote, journals  # noqa: E402
from tavotto_mcp import bridge, server  # noqa: E402
from test_journalfacts_remote import fact_bundle, index_for, signed  # noqa: E402

pytest_plugins = ["test_journalfacts_remote"]


def test_mcp_exposes_facts_with_sources_without_telemetry(isolated, monkeypatch):
    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _name: None)
    obj = fact_bundle()
    raw = json.dumps(obj).encode()
    index = index_for(raw, 2)
    facts._save_index(signed(key, index), index)
    monkeypatch.setattr(facts, "_request", lambda *args: (200, raw))
    events = []
    monkeypatch.setattr(bridge, "note_tool_call", lambda event: events.append(event))
    assert "tavotto_get_journal_facts" in {t["name"] for t in server._tools()}
    result = server.Server(conn=object())._call_tool_counted(
        "tavotto_get_journal_facts", {"journal_id": "fx-journal", "limit": 1}
    )
    out = result["structuredContent"]
    assert out["journal_id"] == "fx-journal" and out["total"] == 1
    assert out["facts"][0]["conditions"] == {"figure_kind": "line", "fine_line": True}
    assert out["sources"]["s"]["url"] == "https://example.invalid/guide"
    assert json.loads(result["content"][0]["text"]) == out
    assert events == []


def test_mcp_facts_does_not_substitute_v1_or_seed(isolated, monkeypatch):
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _name: None)
    result = server.call_tool("tavotto_get_journal_facts", {"journal_id": "fx-journal"})
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "journal_offline"
    assert "facts" not in result["structuredContent"]


def test_mcp_facts_respects_global_switch_and_old_engine(monkeypatch):
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", False)
    assert "tavotto_get_journal_facts" not in {t["name"] for t in server._tools()}
    result = server.call_tool("tavotto_get_journal_facts", {"query": "fixture"})
    assert result["isError"] and result["structuredContent"]["code"] == "journal_catalog_disabled"
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    real = bridge._optional_engine
    monkeypatch.setattr(
        bridge,
        "_optional_engine",
        lambda name: None if name == "journalfacts_remote" else real(name),
    )
    assert "tavotto_get_journal_facts" not in {t["name"] for t in server._tools()}
    result = server.call_tool("tavotto_get_journal_facts", {"query": "fixture"})
    assert result["isError"] and result["structuredContent"]["code"] == "engine_capability_missing"


def test_mcp_rejects_ambiguous_or_invalid_paging():
    from tavotto_mcp.rpc import RpcError

    for args in ({"query": "x", "journal_id": "fx-journal"}, {"limit": True}, {"unexpected": 1}):
        with pytest.raises(RpcError):
            server.call_tool("tavotto_get_journal_facts", args)


def test_frozen_spec_includes_dynamically_loaded_v2_client():
    import ast

    tree = ast.parse((ROOT / "packaging" / "tavotto.spec").read_text())
    analyses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    ]
    assert len(analyses) == 1
    hidden = next(
        keyword.value for keyword in analyses[0].keywords if keyword.arg == "hiddenimports"
    )
    assert isinstance(hidden, ast.List)
    literals = {
        node.value
        for node in hidden.elts
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {
        "tavotto.engine.journalfacts_remote",
        "tavotto.engine.journalfacts_contract",
    } <= literals


def test_mcp_offline_cached_index_missing_journal_is_unknown_not_absent(isolated, monkeypatch):
    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _name: None)
    raw = json.dumps(fact_bundle()).encode()
    idx = index_for(raw)
    idx["journals"] = {}
    facts._save_index(signed(key, idx), idx)
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    result = server.call_tool("tavotto_get_journal_facts", {"journal_id": "new-journal"})
    out = result["structuredContent"]
    assert result["isError"] is True and out["code"] == "journal_offline"
    assert out["sync_state"] == "disabled" and out["sync_code"] == "disabled"
    assert "没有这本刊" not in out["error"]

    monkeypatch.setattr(journalremote, "disabled", lambda: False)
    monkeypatch.setattr(facts, "_request", lambda *args: (200, signed(key, idx)))
    result = server.call_tool("tavotto_get_journal_facts", {"journal_id": "new-journal"})
    assert result["structuredContent"]["code"] == "journal_unknown"
