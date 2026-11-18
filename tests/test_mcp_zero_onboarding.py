"""ADR 0192 §4「开场零开销」：首包 engine 摘要、期刊参数宽松解析、规则进工具描述。

* 第一次成功的 `tavotto_open_figure` 回包带 `engine`，之后不再带；失败的不算「第一次」；
* `journal_id` 接受 id / 全名 / 缩写（大小写、空格、标点不敏感），回包写出 `journal_resolved`；
  多个命中 = `journal_ambiguous`，没命中 = `journal_unknown` + 候选；精确 id 的老行为不变；
  走真的期刊种子目录（引擎 `search_journals`），不在这里抄期刊表；
* `initialize.instructions` / `tools/list` 的 description 不再要求「开工前先调 health / guide」，
  并写进关键规则（结构化解析 JSON-RPC 结果，不对源码做子串断言）。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto_mcp import bridge, rpc, server  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TAVOTTO_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(server, "_ENGINE_REPORTED", False)


def _srv():
    return server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))


# --------------------------------------------------------------- 首包 engine 摘要
def _fake_open(monkeypatch, outcomes):
    """把 open 的真干活那一层换成按序回放：只测首包摘要这一层的接线。"""
    queue = list(outcomes)

    def fake(args):
        out = queue.pop(0)
        if isinstance(out, Exception):
            raise out
        return {"content": [{"type": "text", "text": "已打开"}], "structuredContent": dict(out)}

    monkeypatch.setattr(server, "_open_figure_result", fake)


def test_first_successful_open_carries_engine_then_never_again(monkeypatch):
    monkeypatch.setattr(server, "_version", lambda: "9.9.9")
    _fake_open(monkeypatch, [{"session_id": "s1"}, {"session_id": "s2"}, {"session_id": "s3"}])
    first = server._call_open({})["structuredContent"]
    assert first["engine"] == {"version": "9.9.9"}
    assert "engine" not in server._call_open({})["structuredContent"]
    assert "engine" not in server._call_open({})["structuredContent"]


def test_a_failed_open_does_not_use_up_the_first_report(monkeypatch):
    _fake_open(
        monkeypatch,
        [bridge.BridgeError("没有这张图", code="stem_not_found"), {"session_id": "s1"}],
    )
    with pytest.raises(bridge.BridgeError):
        server._call_open({})
    assert "engine" in server._call_open({})["structuredContent"]


def test_a_failed_batch_does_not_use_up_the_first_report(monkeypatch):
    _fake_open(monkeypatch, [{"status": "failed"}, {"status": "done"}])
    assert "engine" not in server._call_open({})["structuredContent"]
    assert "engine" in server._call_open({})["structuredContent"]


def test_open_engine_digest_reuses_the_health_update_notice(monkeypatch):
    """`update` 的数据与话术只有一份：open 首包与 `tavotto_health` 拿到同一个对象。"""
    notice = {"latest": "99.0.0", "message": "有新版 99.0.0"}
    monkeypatch.setattr(server.updatenotice, "notice", lambda mine: notice)
    _fake_open(monkeypatch, [{"session_id": "s1"}])
    res = server._call_open({})
    assert res["structuredContent"]["engine"]["update"] == notice
    assert "有新版 99.0.0" in res["content"][0]["text"]
    health = server._call_health({})["structuredContent"]
    assert health["update"] == notice


def test_health_is_unchanged_and_still_listed():
    health = server._call_health({})["structuredContent"]
    assert health["ok"] is True and health["engine"]["available"] is True
    names = {t["name"] for t in _srv().dispatch("tools/list", {})["tools"]}
    assert {"tavotto_health", "tavotto_guide", "tavotto_list_journals"} <= names


# --------------------------------------------------------------- 期刊宽松解析（真种子目录）
def _catalog():
    return {j["id"]: j for j in bridge.list_journals()["journals"]}


def test_seed_catalog_has_what_these_cases_need():
    cat = _catalog()
    assert cat["nature"]["label"] == "Nature"
    assert "Nat Commun" in cat["nature-communications"]["aliases"]


@pytest.mark.parametrize(
    "raw",
    ["Nature", "NATURE", "  nature  ", "Nature (London)", "nature!"],
)
def test_loose_names_resolve_to_the_unique_journal(raw):
    rid, info = bridge.resolve_journal_id(raw)
    assert rid == "nature"
    assert info == {"from": raw, "id": "nature"}


@pytest.mark.parametrize(
    "raw",
    ["Nature Communications", "nature_communications", "Nat. Commun.", "nat commun", "NatCommun"],
)
def test_full_name_alias_and_punctuation_variants(raw):
    assert bridge.resolve_journal_id(raw) == (
        "nature-communications",
        {"from": raw, "id": "nature-communications"},
    )


def test_exact_id_is_passed_through_untouched_without_resolved_marker():
    for jid in _catalog():
        assert bridge.resolve_journal_id(jid) == (jid, None)


def test_an_exact_name_beats_longer_names_that_contain_it():
    """「Nature」是 Nature Chemistry / Nature Energy… 的子串，但整串相等的那一本优先。"""
    assert bridge.resolve_journal_id("Nature")[0] == "nature"


def test_ambiguous_name_returns_candidates_not_a_guess():
    with pytest.raises(bridge.BridgeError) as err:
        bridge.resolve_journal_id("nature c")
    payload = err.value.payload()
    assert payload["code"] == "journal_ambiguous"
    ids = {c["id"] for c in payload["candidates"]}
    assert {"nature-chemistry", "nature-communications"} <= ids
    assert 2 <= len(payload["candidates"]) <= bridge.JOURNAL_CANDIDATE_LIMIT
    assert payload["journal"] == "nature c"


def test_ambiguity_candidates_are_capped(monkeypatch):
    many = [
        {"id": f"zeta-{i}", "label": f"Zeta Letters {i}", "publisher": "P", "aliases": []}
        for i in range(12)
    ]
    monkeypatch.setattr(bridge, "list_journals", lambda query=None: {"journals": many})
    with pytest.raises(bridge.BridgeError) as err:
        bridge.resolve_journal_id("zeta letters")
    assert err.value.code == "journal_ambiguous"
    assert len(err.value.payload()["candidates"]) == bridge.JOURNAL_CANDIDATE_LIMIT


def test_unknown_name_returns_closest_candidates():
    with pytest.raises(bridge.BridgeError) as err:
        bridge.resolve_journal_id("Natrue Communcations")
    payload = err.value.payload()
    assert payload["code"] == "journal_unknown"
    assert "nature-communications" in {c["id"] for c in payload["candidates"]}
    assert len(payload["candidates"]) <= bridge.JOURNAL_CANDIDATE_LIMIT


def test_unknown_with_nothing_close_still_unknown_with_empty_candidates():
    with pytest.raises(bridge.BridgeError) as err:
        bridge.resolve_journal_id("zzzzqqqq")
    assert err.value.code == "journal_unknown"
    assert err.value.payload()["candidates"] == []


def test_non_strings_and_blank_pass_through_for_the_original_errors():
    for raw in (None, "", "   ", 5):
        assert bridge.resolve_journal_id(raw) == (raw, None)


def test_catalog_switched_off_passes_through(monkeypatch):
    monkeypatch.setattr(bridge, "_journal_agent_module", lambda: None)
    assert bridge.resolve_journal_id("Nature") == ("Nature", None)


# 分派层：解析发生在处理器之前，处理器只见精确 id，回包带 journal_resolved
@pytest.fixture
def spy_handler(monkeypatch):
    seen = {}

    def handler(args):
        seen.update(args)
        return {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"ok": True}}

    monkeypatch.setitem(server.HANDLERS, "tavotto_preflight", handler)
    return seen


def test_dispatch_resolves_before_the_handler_and_reports_it(spy_handler):
    res = server.call_tool("tavotto_preflight", {"session_id": "s", "journal_id": "Nat Commun"})
    assert spy_handler["journal_id"] == "nature-communications"
    assert res["structuredContent"]["journal_resolved"] == {
        "from": "Nat Commun",
        "id": "nature-communications",
    }
    assert "nature-communications" in res["content"][0]["text"]


def test_dispatch_exact_id_has_no_resolved_marker(spy_handler):
    res = server.call_tool("tavotto_preflight", {"session_id": "s", "journal_id": "nature"})
    assert spy_handler["journal_id"] == "nature"
    assert "journal_resolved" not in res["structuredContent"]


def test_dispatch_ambiguous_is_an_error_result_with_candidates(spy_handler):
    res = server.call_tool("tavotto_preflight", {"session_id": "s", "journal_id": "nature c"})
    assert res["isError"] is True
    assert res["structuredContent"]["code"] == "journal_ambiguous"
    assert res["structuredContent"]["candidates"]
    assert spy_handler == {}  # 处理器没跑


def test_dispatch_does_not_resolve_when_a_manual_profile_is_also_given(spy_handler):
    """与手写 profile_id / journal 同给：不替用户挑，原样交给引擎报 journal_id_conflict。"""
    server.call_tool(
        "tavotto_preflight", {"session_id": "s", "journal_id": "Nature", "profile_id": "x"}
    )
    assert spy_handler["journal_id"] == "Nature"


def test_every_journal_id_tool_is_covered_by_the_resolver():
    tools = {t["name"]: t for t in _srv().dispatch("tools/list", {})["tools"]}
    having = {n for n, t in tools.items() if "journal_id" in t["inputSchema"]["properties"]}
    having.discard("tavotto_get_journal_facts")  # 事实目录另有 id 空间，不在宽松解析范围
    assert having == set(server.JOURNAL_RESOLVE_TOOLS)


# --------------------------------------------------------------- instructions / description
def _instructions():
    return _srv().dispatch("initialize", {"protocolVersion": "2025-06-18"})["instructions"]


def _tool_descriptions():
    return {t["name"]: t["description"] for t in _srv().dispatch("tools/list", {})["tools"]}


def _sentences(text):
    return [s for s in text.replace("。", "。\n").replace("；", "；\n").splitlines() if s.strip()]


def test_instructions_no_longer_require_health_or_guide_first():
    for sentence in _sentences(_instructions()):
        for tool in ("tavotto_health", "tavotto_guide"):
            if tool in sentence:
                assert "先调" not in sentence.replace("不必先调", ""), sentence
                assert "开工前" not in sentence.replace("不必开工前", ""), sentence
    text = _instructions()
    assert "不必先调 tavotto_health 或 tavotto_guide" in text
    assert "工具缺失" in text and "引擎错误" in text


def test_instructions_carry_the_key_rules():
    text = _instructions()
    assert "patches 永远发全量列表" in text
    assert "必须回代码改" in text
    assert "导出之后再关会话" in text
    assert "journal_id 接受 id、全名、常见缩写" in text


def test_tool_descriptions_state_the_rules_where_hosts_without_skills_see_them():
    desc = _tool_descriptions()
    assert "不必开工前先调" in desc["tavotto_health"]
    assert "不必开工前先调" in desc["tavotto_guide"]
    assert "不必先 tavotto_health" in desc["tavotto_open_figure"]
    assert "engine" in desc["tavotto_open_figure"]
    assert "全量列表" in desc["tavotto_apply_overrides"]
    assert "必须回代码改" in desc["tavotto_apply_overrides"]
    assert "先导出再关会话" in desc["tavotto_export"]
    assert "先 tavotto_export 再关" in desc["tavotto_close_session"]
    for name in ("tavotto_open_figure", "tavotto_preflight", "tavotto_export"):
        props = _journal_id_description(name)
        assert "全名" in props and "journal_ambiguous" in props


def _journal_id_description(name):
    tool = next(t for t in _srv().dispatch("tools/list", {})["tools"] if t["name"] == name)
    return tool["inputSchema"]["properties"]["journal_id"]["description"]


def test_no_description_tells_the_agent_to_call_health_first():
    for name, text in _tool_descriptions().items():
        assert "先调它" not in text, name
        assert "开工前先调" not in text.replace("不必开工前先调", ""), name


def test_descriptions_stay_short():
    """规则进了描述，但别把描述写成长文：每个不超过 1200 字符，整张 tools/list 的描述总量有上限。"""
    desc = _tool_descriptions()
    assert max(len(t) for t in desc.values()) <= 1200
    assert sum(len(t) for t in desc.values()) <= 9000
