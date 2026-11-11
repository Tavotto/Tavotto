"""The session-only view cannot promote publisher labels to semantic review."""

import json

import pytest

from tavotto.engine import journalfacts_remote as facts, journalremote, journals
from tavotto_mcp import bridge, server
from test_journalfacts_remote import fact_bundle, index_for, signed

pytest_plugins = ["test_journalfacts_remote"]


def test_signed_source_view_retains_context_and_never_asserts_compliance(isolated, monkeypatch):
    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _: None)
    bundle = fact_bundle()
    fact = bundle["facts"][0]
    fact.update(
        context=[{"source": "s", "quote": "A qualifying heading"}], condition_text="if suitable"
    )
    raw = json.dumps(bundle).encode()
    idx = index_for(raw, 7)
    envelope = signed(key, idx)
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, headers: (
            (200, envelope) if url.endswith("index.signed.json") else (200, raw)
        ),
    )
    result = server.call_tool(
        "tavotto_get_journal_facts",
        {
            "journal_id": "fx-journal",
            "inspection": {
                "object_label": "Figure 1",
                "figure_kind": "line_art",
                "stage": "submission",
            },
        },
    )
    out = result["structuredContent"]
    assert out["catalog_rev"] == 7 and out["catalog_completeness"] == "unknown"
    assert out["facts"][0] == fact and out["sources"]["s"]["url"] == "https://example.invalid/guide"
    assert out["inspection"]["semantic_review"] == "not_authenticated"
    assert out["inspection"]["facts"][0]["structured_conditions"]["status"] == "unknown"
    assert out["inspection"]["facts"][0]["status"] == "not_verifiable"
    assert json.loads(result["content"][0]["text"]) == out


def test_selection_defaults_and_pending_never_become_a_verdict():
    for review in ("pending", "verified", "rejected"):
        page = {"facts": [{"id": "x", "review": {"status": review}}]}
        result = facts.inspect_facts_page(page, {"object_label": "one"})
        view = result["inspection"]
        assert (view["figure_kind"], view["stage"]) == ("unknown", "unknown")
        assert view["facts"][0]["status"] == "not_verifiable"
        assert view["facts"][0]["structured_conditions"]["status"] == "unknown"
        assert "inspection" not in page


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"object_label": "  "},
        {"object_label": "x", "figure_kind": "line"},
        {"object_label": "x", "stage": "any"},
        {"object_label": "x", "measured_dpi": 1200},
        {"object_label": "x", "reviewed": True},
    ],
)
def test_invalid_selection_fails_before_network(selection, monkeypatch):
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _: None)
    monkeypatch.setattr(facts, "get_facts", lambda *a, **kw: pytest.fail("network must not run"))
    out = server.call_tool(
        "tavotto_get_journal_facts", {"journal_id": "fx-journal", "inspection": selection}
    )
    assert out["isError"] and out["structuredContent"]["code"] == "bad_request"


def test_guarded_paging_for_inspection():
    from tavotto_mcp.rpc import RpcError

    with pytest.raises(RpcError):
        server.call_tool(
            "tavotto_get_journal_facts",
            {"journal_id": "fx-journal", "offset": 1, "inspection": {"object_label": "one"}},
        )


def test_invalid_signature_cannot_return_a_source_or_inspection(isolated, monkeypatch):
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _: None)
    monkeypatch.setattr(facts, "_request", lambda *a: (200, b"{}"))
    result = server.call_tool(
        "tavotto_get_journal_facts",
        {"journal_id": "fx-journal", "inspection": {"object_label": "one"}},
    )
    assert (
        result["isError"] and result["structuredContent"]["code"] == "journal_verification_failed"
    )
    assert "facts" not in result["structuredContent"]
    assert "inspection" not in result["structuredContent"]


def test_older_client_cannot_fabricate_inspection(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _: None)
    legacy = SimpleNamespace(
        FactsError=facts.FactsError,
        search=lambda *a, **kw: {},
        get_facts=lambda *a, **kw: pytest.fail("old engine must not be called"),
    )
    real = bridge._optional_engine
    monkeypatch.setattr(
        bridge,
        "_optional_engine",
        lambda name: legacy if name == "journalfacts_remote" else real(name),
    )
    result = server.call_tool(
        "tavotto_get_journal_facts",
        {"journal_id": "fx-journal", "inspection": {"object_label": "one"}},
    )
    assert result["isError"] and result["structuredContent"]["code"] == "engine_capability_missing"


@pytest.mark.parametrize(
    ("fact", "selection", "expected"),
    [
        (
            {"stage": "submission", "conditions": {"figure_kind": "line_art"}},
            {"figure_kind": "line_art", "stage": "submission"},
            "match",
        ),
        (
            {"stage": "submission", "conditions": {"figure_kind": "halftone"}},
            {"figure_kind": "line_art", "stage": "submission"},
            "mismatch",
        ),
        (
            {"stage": "revision", "conditions": {"figure_kind": "line_art"}},
            {"figure_kind": "line_art", "stage": "submission"},
            "mismatch",
        ),
        (
            {
                "stage": "any",
                "conditions": {
                    "figure_kind": {"relation": "in", "value": ["line_art", "halftone"]}
                },
            },
            {"figure_kind": "line_art", "stage": "submission"},
            "match",
        ),
        (
            {
                "stage": "submission",
                "conditions": {"figure_kind": {"relation": "in", "value": ["line_art", "line"]}},
            },
            {"figure_kind": "line_art", "stage": "submission"},
            "unknown",
        ),
        (
            {"stage": "submission", "conditions": {"figure_kind": "line"}},
            {"figure_kind": "line_art", "stage": "submission"},
            "unknown",
        ),
        (
            {"stage": "submission", "conditions": {"figure_kind": "halftone", "fine_line": True}},
            {"figure_kind": "line_art", "stage": "submission"},
            "unknown",
        ),
        (
            {
                "stage": "submission",
                "conditions": {"figure_kind": "halftone"},
                "condition_text": "unless...",
            },
            {"figure_kind": "line_art", "stage": "submission"},
            "unknown",
        ),
        (
            {
                "stage": "submission",
                "conditions": {"figure_kind": "halftone"},
                "context": [{"source": "s", "quote": "heading"}],
            },
            {"figure_kind": "line_art", "stage": "submission"},
            "unknown",
        ),
        (
            {"stage": "unknown", "conditions": {}},
            {"figure_kind": "line_art", "stage": "submission"},
            "unknown",
        ),
        ({"stage": "any", "conditions": {}}, {"figure_kind": "line_art"}, "unknown"),
        ({"stage": "submission", "conditions": {}}, {"stage": "submission"}, "unknown"),
    ],
)
def test_only_explicit_structured_fields_are_compared(fact, selection, expected):
    record = {"id": "sample", **fact}
    view = facts.inspect_facts_page({"facts": [record]}, {"object_label": "one", **selection})
    item = view["inspection"]["facts"][0]
    assert item["status"] == "not_verifiable"
    assert item["structured_conditions"]["status"] == expected
    assert view["inspection"]["semantic_review"] == "not_authenticated"


def test_final_result_budget_counts_utf8_and_both_copies(monkeypatch):
    page = {
        "catalog_rev": 8,
        "next_offset": None,
        "facts": [{"id": "f", "quote": "字体中文"}],
        "inspection": {"status": "not_verifiable"},
    }
    monkeypatch.setattr(bridge, "journal_facts", lambda *args: page)
    expected = {
        "content": server._text(json.dumps(page, ensure_ascii=False)),
        "structuredContent": page,
    }
    actual_bytes = len(json.dumps(expected, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    assert actual_bytes > len(json.dumps(expected, ensure_ascii=False, allow_nan=False))
    monkeypatch.setattr(bridge, "journal_facts_inspection_budget", lambda: actual_bytes)
    args = {"journal_id": "fx-journal", "inspection": {"object_label": "图一"}}
    assert server.call_tool("tavotto_get_journal_facts", args) == expected
    monkeypatch.setattr(bridge, "journal_facts_inspection_budget", lambda: actual_bytes - 1)
    failed = server.call_tool("tavotto_get_journal_facts", args)
    assert (
        failed["isError"] and failed["structuredContent"]["code"] == "journal_facts_page_too_large"
    )
    assert "facts" not in failed["structuredContent"]
    # Same response without inspection preserves legacy behavior and never invokes the new budget.
    legacy = server.call_tool("tavotto_get_journal_facts", {"journal_id": "fx-journal"})
    assert legacy == expected


@pytest.mark.parametrize("request_id", [3, "中文请求号", "界" * 1200])
def test_real_rpc_frame_uses_actual_request_id(monkeypatch, request_id):
    import io

    from tavotto_mcp.rpc import StdioConnection

    payload = {
        "content": server._text("中文"),
        "structuredContent": {"inspection": {"status": "not_verifiable"}},
    }
    raw = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "result": payload}, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    assert len(raw) + 1 == server._inspection_result_size(payload, request_id)
    out = io.BytesIO()
    instance = server.Server(conn=StdioConnection(stdin=io.BytesIO(), stdout=out))
    monkeypatch.setattr(instance, "_refresh_protocol_roots", lambda: None)
    monkeypatch.setattr(instance, "dispatch", lambda *args: payload)
    monkeypatch.setattr(bridge, "journal_facts_inspection_budget", lambda: len(raw) + 1)
    msg = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": "tavotto_get_journal_facts",
            "arguments": {"inspection": {"object_label": "一"}},
        },
    }
    instance.handle(msg)
    assert out.getvalue() == raw + b"\n"
    out.seek(0)
    out.truncate()
    monkeypatch.setattr(bridge, "journal_facts_inspection_budget", lambda: len(raw))
    instance.handle(msg)
    response = json.loads(out.getvalue())
    assert response["id"] == request_id
    assert response["result"]["isError"]
    assert response["result"]["structuredContent"]["code"] == "journal_facts_page_too_large"


def test_unrelated_tool_response_with_inspection_is_not_guarded(monkeypatch):
    import io

    from tavotto_mcp.rpc import StdioConnection

    out = io.BytesIO()
    instance = server.Server(conn=StdioConnection(stdin=io.BytesIO(), stdout=out))
    monkeypatch.setattr(instance, "_refresh_protocol_roots", lambda: None)
    payload = {"structuredContent": {"inspection": "other tool"}}
    monkeypatch.setattr(instance, "dispatch", lambda *args: payload)
    monkeypatch.setattr(
        bridge, "journal_facts_inspection_budget", lambda: pytest.fail("unrelated tool")
    )
    instance.handle({"id": 1, "method": "tools/call", "params": {"name": "other", "arguments": {}}})
    assert json.loads(out.getvalue())["result"] == payload


def test_large_signed_page_fails_closed_then_resumes_same_revision(isolated, monkeypatch):
    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _: None)
    bundle = fact_bundle()
    for i in range(1, 4):
        item = dict(bundle["facts"][0], id=f"line-{i}", quote="图" * 250)
        bundle["facts"].append(item)
    bundle["facts"][0]["quote"] = "图" * 250
    raw = json.dumps(bundle, ensure_ascii=False).encode()
    idx = index_for(raw, 9)
    envelope = signed(key, idx)
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, headers: (
            (200, envelope) if url.endswith("index.signed.json") else (200, raw)
        ),
    )
    monkeypatch.setattr(facts, "MAX_PAGE_BYTES", 5000)
    selection = {"object_label": "图一", "figure_kind": "line_art", "stage": "submission"}
    original = server.call_tool(
        "tavotto_get_journal_facts", {"journal_id": "fx-journal", "limit": 4}
    )
    assert not original.get("isError") and len(original["structuredContent"]["facts"]) >= 2
    assert original["structuredContent"]["next_offset"] is not None
    inspection = server.call_tool(
        "tavotto_get_journal_facts",
        {"journal_id": "fx-journal", "limit": 4, "inspection": selection},
    )
    assert (
        inspection["isError"]
        and inspection["structuredContent"]["code"] == "journal_facts_page_too_large"
    )
    seen = []
    offset = 0
    while offset is not None:
        args = {"journal_id": "fx-journal", "limit": 1, "offset": offset, "inspection": selection}
        if offset:
            args["expected_catalog_rev"] = 9
        result = server.call_tool("tavotto_get_journal_facts", args)
        assert not result.get("isError")
        page = result["structuredContent"]
        assert page["catalog_rev"] == 9
        seen.extend(item["id"] for item in page["facts"])
        offset = page["next_offset"]
    assert seen == ["line", "line-1", "line-2", "line-3"]


def test_single_record_can_exceed_mcp_budget_without_truncation(isolated, monkeypatch):
    key, _ = isolated
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _: None)
    bundle = fact_bundle()
    bundle["facts"][0]["quote"] = "图" * 500
    bundle["facts"][0]["context"] = [{"source": "s", "quote": "线" * 500}]
    raw = json.dumps(bundle, ensure_ascii=False).encode()
    idx = index_for(raw, 9)
    envelope = signed(key, idx)
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, headers: (
            (200, envelope) if url.endswith("index.signed.json") else (200, raw)
        ),
    )
    monkeypatch.setattr(facts, "MAX_PAGE_BYTES", 5000)
    legacy = server.call_tool("tavotto_get_journal_facts", {"journal_id": "fx-journal", "limit": 1})
    assert (
        not legacy.get("isError") and legacy["structuredContent"]["facts"][0]["quote"] == "图" * 500
    )
    result = server.call_tool(
        "tavotto_get_journal_facts",
        {"journal_id": "fx-journal", "limit": 1, "inspection": {"object_label": "图"}},
    )
    assert (
        result["isError"] and result["structuredContent"]["code"] == "journal_facts_page_too_large"
    )
    assert "facts" not in result["structuredContent"]
    assert "limit 为 1" in result["content"][0]["text"]


def test_oversized_request_id_can_itself_exceed_the_error_budget(monkeypatch):
    import io

    from tavotto_mcp.rpc import StdioConnection

    out = io.BytesIO()
    instance = server.Server(conn=StdioConnection(stdin=io.BytesIO(), stdout=out))
    monkeypatch.setattr(instance, "_refresh_protocol_roots", lambda: None)
    monkeypatch.setattr(
        instance, "dispatch", lambda *args: {"structuredContent": {"inspection": {}}}
    )
    monkeypatch.setattr(bridge, "journal_facts_inspection_budget", lambda: 1000)
    request_id = "界" * 1000
    instance.handle(
        {
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "tavotto_get_journal_facts",
                "arguments": {"inspection": {"object_label": "一"}},
            },
        }
    )
    raw = out.getvalue()
    assert len(raw) > 1000
    result = json.loads(raw)
    assert result["id"] == request_id and result["result"]["isError"]
    assert "inspection" not in result["result"].get("structuredContent", {})


def test_base_single_record_too_large_reports_explicitly(monkeypatch):
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "set_client_type", lambda _: None)

    def too_large(*args, **kwargs):
        raise facts.FactsError("result_too_large")

    monkeypatch.setattr(facts, "get_facts", too_large)
    result = server.call_tool(
        "tavotto_get_journal_facts",
        {"journal_id": "fx-journal", "limit": 1, "inspection": {"object_label": "一"}},
    )
    assert (
        result["isError"] and result["structuredContent"]["code"] == "journal_facts_page_too_large"
    )
    legacy = server.call_tool("tavotto_get_journal_facts", {"journal_id": "fx-journal", "limit": 1})
    assert legacy["isError"] and legacy["structuredContent"]["code"] == "journal_facts_unavailable"
