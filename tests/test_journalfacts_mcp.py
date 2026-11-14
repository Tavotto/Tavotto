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


def test_mcp_paging_rejects_revision_changes_and_gives_recovery(isolated, monkeypatch):
    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _name: None)
    bundle = fact_bundle()
    duplicate = dict(bundle["facts"][0], id="second")
    bundle["facts"].append(duplicate)
    raw = json.dumps(bundle).encode()
    idx1, idx2 = index_for(raw, 1), index_for(raw, 2)
    current = {"rev": 1}

    def request(method, url, cap, headers):
        if url.endswith("index.signed.json"):
            return 200, signed(key, idx1 if current["rev"] == 1 else idx2)
        return 200, raw

    monkeypatch.setattr(facts, "_request", request)
    first = server.call_tool("tavotto_get_journal_facts", {"journal_id": "fx-journal", "limit": 1})
    page = first["structuredContent"]
    assert page["catalog_rev"] == 1 and page["next_offset"] == 1
    current["rev"] = 2
    response = server.call_tool(
        "tavotto_get_journal_facts",
        {"journal_id": "fx-journal", "offset": 1, "limit": 1, "expected_catalog_rev": 1},
    )
    out = response["structuredContent"]
    assert response["isError"] and out["code"] == "journal_catalog_changed"
    assert out["expected_catalog_rev"] == 1 and out["current_catalog_rev"] == 2
    assert "第一页重新读取" in out["error"] and "facts" not in out


@pytest.mark.parametrize("mode", ["search", "journal"])
def test_mcp_followup_requires_signed_catalog_revision_and_rejects_changes(
    isolated, monkeypatch, mode
):
    from tavotto_mcp.rpc import RpcError

    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _name: None)
    bundle = fact_bundle()
    bundle["facts"].append(dict(bundle["facts"][0], id="second"))
    raw = json.dumps(bundle).encode()
    idx1, idx2 = index_for(raw, 1), index_for(raw, 2)
    if mode == "search":
        template = idx1["journals"]["fx-journal"]
        idx1["journals"] = {"j1": dict(template), "j2": dict(template)}
        idx2["journals"] = {"j0": dict(template), **idx1["journals"]}
    current = {"index": idx1, "requests": 0}

    def request(method, url, cap, headers):
        current["requests"] += 1
        return (
            (200, signed(key, current["index"]))
            if url.endswith("index.signed.json")
            else (200, raw)
        )

    monkeypatch.setattr(facts, "_request", request)
    args = {"limit": 1, **({"journal_id": "fx-journal"} if mode == "journal" else {})}
    first = server.call_tool("tavotto_get_journal_facts", args)["structuredContent"]
    items = "facts" if mode == "journal" else "journals"
    assert first["catalog_rev"] == 1 and first["next_offset"] == 1
    assert first[items][0]["id"] == ("line" if mode == "journal" else "j1")

    requests = current["requests"]
    with pytest.raises(RpcError, match="catalog_rev"):
        server.call_tool("tavotto_get_journal_facts", {**args, "offset": 1})
    assert current["requests"] == requests
    same = server.call_tool(
        "tavotto_get_journal_facts", {**args, "offset": 1, "expected_catalog_rev": 1}
    )["structuredContent"]
    assert same[items][0]["id"] == ("second" if mode == "journal" else "j2")

    current["index"] = idx2
    changed = server.call_tool(
        "tavotto_get_journal_facts", {**args, "offset": 1, "expected_catalog_rev": 1}
    )
    assert changed["isError"] and changed["structuredContent"]["code"] == "journal_catalog_changed"
    assert changed["structuredContent"]["current_catalog_rev"] == 2
    assert items not in changed["structuredContent"]


def test_guarded_paging_requires_engine_support_but_legacy_calls_still_work(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _name: None)
    legacy = SimpleNamespace(
        FactsError=facts.FactsError,
        search=lambda query, *, offset, limit: {"catalog_rev": 1, "journals": []},
        get_facts=lambda jid, *, offset, limit: {"catalog_rev": 1, "facts": []},
    )
    real = bridge._optional_engine
    monkeypatch.setattr(
        bridge,
        "_optional_engine",
        lambda name: legacy if name == "journalfacts_remote" else real(name),
    )
    ok = server.call_tool("tavotto_get_journal_facts", {"journal_id": "fx-journal"})
    assert ok["structuredContent"]["catalog_rev"] == 1
    guarded = server.call_tool(
        "tavotto_get_journal_facts", {"journal_id": "fx-journal", "expected_catalog_rev": 1}
    )
    assert (
        guarded["isError"] and guarded["structuredContent"]["code"] == "engine_capability_missing"
    )


def test_mcp_concurrent_journal_removal_returns_revision_changed(isolated, monkeypatch):
    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _name: None)
    raw = json.dumps(fact_bundle()).encode()
    idx1, idx2 = index_for(raw, 1), index_for(raw, 2)
    idx2["journals"] = {}
    facts._save_index(signed(key, idx1), idx1)
    monkeypatch.setattr(facts, "_request", lambda *args: (200, signed(key, idx1)))
    original = facts._cached_journal

    def concurrent_removal(jid):
        facts._save_index(signed(key, idx2), idx2)
        return original(jid)

    monkeypatch.setattr(facts, "_cached_journal", concurrent_removal)
    result = server.call_tool(
        "tavotto_get_journal_facts",
        {"journal_id": "fx-journal", "offset": 1, "expected_catalog_rev": 1},
    )
    assert result["isError"] and result["structuredContent"]["code"] == "journal_catalog_changed"
    assert result["structuredContent"]["current_catalog_rev"] == 2
