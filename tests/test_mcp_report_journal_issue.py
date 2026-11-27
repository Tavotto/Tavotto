"""`tavotto_report_journal_issue`（ADR 0213 三期）：Agent 替用户提交期刊线索，先问再发。

判据的主语：

* 「没联网 / 没发」= `contactsend.start` 被调用的次数、以及 `diagsend._send`（出站唯一入口）的调用次数，
  不是工具自己说没发；
* 「current / catalog 由本机取」= 回包里的值等于夹具目录里的值，且 Agent 传入的同名字段不存在于 schema；
* 「遥测不进闭集」= `usage.TOOLS` 里没有它、`usage.properties` 把它归到 `other`。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import contactsend, diagsend, journalremote, journals  # noqa: E402
from tavotto_mcp import bridge, nextstep, server, usage  # noqa: E402

TOOL = "tavotto_report_journal_issue"
VECTORS = json.loads((ROOT / "tests" / "golden" / "journal_vectors.json").read_text("utf-8"))
URL = "https://example.invalid/author-guide"


class _Cached:
    def __init__(self, catalog):
        self.catalog = catalog


class FakeContact:
    """替换 `contactsend` 的 start / status / dismiss：记下每次发送的 meta，状态由用例推进。"""

    def __init__(self):
        self.starts: list[dict] = []
        self.state: dict = {"state": "idle"}
        self.finish_after = 0  # status() 被问几次后才结束
        self.polls = 0
        self.dismissed = 0

    def start(self, meta, files):
        self.starts.append(copy.deepcopy(meta))
        self.polls = 0
        self.state = {"state": "sending", "gen": len(self.starts)}
        return dict(self.state)

    def status(self):
        if self.state.get("state") == "sending" and self.polls >= self.finish_after:
            self.state = {
                "state": "done",
                "message_id": "TVM-ABCD",
                "route": "cn",
                "inbox_saved": True,
                "gen": len(self.starts),
            }
        self.polls += 1
        return dict(self.state)

    def dismiss(self):
        self.dismissed += 1
        self.state = {"state": "idle"}


@pytest.fixture
def env(monkeypatch):
    for name in (contactsend.ENV_HARD_OFF, "TAVOTTO_NO_JOURNAL_SYNC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(contactsend.ENV_ENDPOINT, raising=False)
    cat = copy.deepcopy(VECTORS["catalog"])
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(journalremote, "load_cached", lambda: _Cached(cat))
    sent: list[tuple] = []

    def boom(*a, **k):
        sent.append((a, k))
        raise AssertionError("预览 / 未确认的调用不许出站")

    monkeypatch.setattr(diagsend, "_send", boom)
    fake = FakeContact()
    monkeypatch.setattr(contactsend, "start", fake.start)
    monkeypatch.setattr(contactsend, "status", fake.status)
    monkeypatch.setattr(contactsend, "dismiss", fake.dismiss)
    monkeypatch.setattr(bridge, "_JOURNAL_REPORT_PENDING", None)
    monkeypatch.setattr(bridge, "_JOURNAL_REPORT_SENT", {})
    monkeypatch.setattr(bridge, "_journal_report_sleep", lambda s: None)
    fake.sent = sent
    fake.catalog = cat
    return fake


def _call(args: dict) -> dict:
    return server.call_tool(TOOL, args)


def _body(res: dict) -> dict:
    return res["structuredContent"]


WRONG = {
    "type": "wrong",
    "journal_id": "fx-alpha",
    "rule_key": "min_raster_dpi",
    "issue": "wrong_value",
    "url": URL,
    "body": "编辑部退稿信写的是 300 dpi 以上即可",
}


# --------------------------------------------------------------------------- 开关
def test_tool_is_listed_only_when_all_three_switches_are_on(env, monkeypatch):
    names = lambda: [t["name"] for t in server._tools()]  # noqa: E731
    assert TOOL in names()
    monkeypatch.setenv(contactsend.ENV_HARD_OFF, "1")
    assert TOOL not in names()
    monkeypatch.delenv(contactsend.ENV_HARD_OFF)
    monkeypatch.setenv("TAVOTTO_NO_JOURNAL_SYNC", "1")
    assert TOOL not in names()
    monkeypatch.delenv("TAVOTTO_NO_JOURNAL_SYNC")
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", False)
    assert TOOL not in names()


def test_disabled_call_refuses_with_a_stable_code_and_sends_nothing(env, monkeypatch):
    monkeypatch.setenv("TAVOTTO_NO_JOURNAL_SYNC", "1")
    res = _call({**WRONG, "explicit_confirm": True, "send_token": "x"})
    assert res["isError"] is True and _body(res)["code"] == "journal_report_disabled"
    assert env.starts == [] and env.sent == []


def test_tool_is_not_in_the_telemetry_closed_set_and_is_bucketed_as_other():
    assert TOOL not in usage.TOOLS
    assert usage.properties(TOOL, "ok", seconds=0.1, client_name=None)["tool"] == "other"
    assert TOOL in server.HANDLERS
    assert "journal_report_confirmation_required" in usage._NEEDS_INPUT


# --------------------------------------------------------------------------- 两步确认
def test_first_call_is_a_preview_with_zero_network_and_asks_the_user(env):
    res = _call(WRONG)
    out = _body(res)
    assert (
        out["status"] == "needs_confirmation"
        and out["code"] == "journal_report_confirmation_required"
    )
    assert env.starts == [] and env.sent == []
    w = out["will_send"]
    assert (
        w["type"] == "wrong" and w["journal_id"] == "fx-alpha" and w["rule_key"] == "min_raster_dpi"
    )
    assert w["current"] == "≥ 600" and w["source_id"] == "s2" and w["url"] == URL
    assert w["issue"] == "wrong_value" and w["catalog"] == {"v1": env.catalog["catalog_rev"]}
    assert w["body_preview"].startswith("编辑部退稿信") and w["body_chars"] == len(WRONG["body"])
    assert out["next"]["action"] == "ask_user"
    assert out["send_token"] in " ".join(out["next"]["options"])
    assert "没有任何内容离开本机" in res["content"][0]["text"]


def test_confirm_flag_without_the_token_does_not_send(env):
    out = _body(_call({**WRONG, "explicit_confirm": True}))
    assert out["status"] == "needs_confirmation" and env.starts == []


def test_confirmed_call_sends_once_with_the_local_head_and_returns_the_id(env):
    token = _body(_call(WRONG))["send_token"]
    res = _call({**WRONG, "explicit_confirm": True, "send_token": token})
    out = _body(res)
    assert out["status"] == "sent" and out["message_id"] == "TVM-ABCD"
    assert "回信" in out["note"] and "核对出版社原文" in out["note"]
    assert len(env.starts) == 1
    head = env.starts[0]["journal"]
    assert head["current"] == "≥ 600" and head["source_id"] == "s2"
    assert head["catalog"] == {"v1": env.catalog["catalog_rev"]}
    assert env.starts[0]["body"] == WRONG["body"]
    # 同一份再确认一次：回放结论，不再发第二封
    again = _body(_call({**WRONG, "explicit_confirm": True, "send_token": token}))
    assert again["already_sent"] is True and len(env.starts) == 1


def test_changed_content_invalidates_the_token(env):
    token = _body(_call(WRONG))["send_token"]
    out = _body(
        _call({**WRONG, "body": "换了一句话", "explicit_confirm": True, "send_token": token})
    )
    assert out["status"] == "needs_confirmation" and out["token_stale"] is True
    assert out["send_token"] != token and env.starts == []


def test_failure_returns_the_contact_failure_code_without_retry(env):
    token = _body(_call(WRONG))["send_token"]
    env.start_orig = env.start

    def fail_start(meta, files):
        env.starts.append(meta)
        env.state = {
            "state": "failed",
            "code": "rate_limited",
            "retryable": True,
            "retry_after": 30,
        }
        return dict(env.state)

    contactsend.start = fail_start  # monkeypatch 的 teardown 会还原 env 里装的那个
    try:
        res = _call({**WRONG, "explicit_confirm": True, "send_token": token})
    finally:
        contactsend.start = env.start_orig
    body = _body(res)
    assert res["isError"] is True and body["code"] == "journal_report_failed"
    assert (
        body["failure"] == "rate_limited"
        and body["retryable"] is True
        and body["retry_after"] == 30
    )
    assert len(env.starts) == 1  # 不重试


def test_slow_send_returns_in_progress_and_the_retry_does_not_resend(env, monkeypatch):
    monkeypatch.setattr(bridge, "JOURNAL_REPORT_WAIT_S", -1.0)
    env.finish_after = 1
    token = _body(_call(WRONG))["send_token"]
    first = _body(_call({**WRONG, "explicit_confirm": True, "send_token": token}))
    assert first["status"] == "in_progress" and len(env.starts) == 1
    monkeypatch.setattr(bridge, "JOURNAL_REPORT_WAIT_S", 5.0)
    second = _body(_call({**WRONG, "explicit_confirm": True, "send_token": token}))
    assert second["status"] == "sent" and len(env.starts) == 1


# --------------------------------------------------------------------------- 参数校验
@pytest.mark.parametrize(
    "args",
    [
        {"type": "bogus"},
        {"type": "wrong", "rule_key": "min_raster_dpi"},  # 缺 journal_id
        {"type": "wrong", "journal_id": "fx-alpha"},  # 缺 rule_key
        {"type": "editor", "journal_id": "fx-alpha"},  # 缺原话
        {"type": "missing"},  # query / url 都没有
        {"type": "missing", "query": "Some Journal", "issue": "other"},  # issue 只随 wrong
        {"type": "missing", "query": "X", "journal_id": "fx-alpha"},
        {"type": "editor", "journal_id": "fx-alpha", "body": "x", "rule_key": "min_raster_dpi"},
        {**WRONG, "issue": "made_up"},
        {**WRONG, "url": "http://example.invalid/a"},  # 只收 https
        {**WRONG, "url": "https://user:pw@example.invalid/a"},
        {**WRONG, "body": 5},
    ],
)
def test_bad_arguments_are_refused_before_anything_is_sent(env, args):
    res = _call(args)
    assert res["isError"] is True and _body(res)["code"] == "bad_arguments"
    assert env.starts == [] and env.sent == []


def test_schema_accepts_no_numeric_value_or_agent_supplied_current(env):
    schema = next(t for t in server._tools() if t["name"] == TOOL)["inputSchema"]
    assert schema["additionalProperties"] is False
    props = set(schema["properties"])
    assert not props & {"value", "current", "catalog", "catalog_rev", "new_value"}
    assert set(schema["properties"]["issue"]["enum"]) == set(contactsend.JOURNAL_ISSUES)
    assert set(schema["properties"]["type"]["enum"]) == set(contactsend.JOURNAL_TYPES)


def test_unknown_journal_and_unknown_rule_are_refused_with_known_keys(env):
    res = _call({**WRONG, "journal_id": "nope"})
    assert _body(res)["code"] == "journal_unknown"
    res = _call({**WRONG, "rule_key": "made_up_key"})
    body = _body(res)
    assert body["code"] == "journal_rule_unknown" and "min_raster_dpi" in body["known_rule_keys"]
    assert env.starts == []


def test_source_id_must_match_the_local_catalog_and_v1_default_is_taken_from_it(env):
    assert _body(_call({**WRONG, "source_id": "s1"}))["code"] == "journal_rule_unknown"
    assert _body(_call({**WRONG, "source_id": "v2:nope:journal"}))["code"] == "journal_rule_unknown"
    out = _body(_call({**WRONG, "source_id": "s2"}))
    assert out["will_send"]["source_id"] == "s2"


def test_agent_cannot_override_current_via_extra_arguments(env):
    # 多余参数在 schema 里是 additionalProperties=false；桥的函数签名也没有 current / catalog
    with pytest.raises(TypeError):
        bridge.report_journal_issue(type="wrong", current="0.1", journal_id="fx-alpha")  # type: ignore[call-arg]


def test_v2_source_uses_the_cached_bundle_and_its_catalog_rev(env, monkeypatch):
    from tavotto.engine import journalfacts_fold as fold, journalfacts_remote as facts

    monkeypatch.setattr(facts, "cached_bundle", lambda jid: (42, {"bundle": jid}))
    monkeypatch.setattr(
        fold,
        "fold",
        lambda b: {
            "rules": {
                "min_line_width_pt": {
                    "value": 0.25,
                    "qualifier": "min",
                    "src": "v2:src1:journal",
                    "fact_id": "fact-9",  # 挂在规则上（与 journalfacts_fold 一致）
                }
            },
            "sources": {"v2:src1:journal": {}},
            "confirm": [],
        },
    )
    args = {
        "type": "wrong",
        "journal_id": "fx-alpha",
        "rule_key": "min_line_width_pt",
        "source_id": "v2:fact-9",
        "body": "原文写 0.5 pt",
    }
    w = _body(_call(args))["will_send"]
    # 线缆上 v2 出处 id 是 `v2:<事实 id>`（与界面同一规则），不是带第二个冒号的折叠 src
    assert w["current"] == "≥ 0.25" and w["source_id"] == "v2:fact-9"
    # 不给 source_id 时取同一条
    del args["source_id"]
    assert _body(_call(args))["will_send"]["source_id"] == "v2:fact-9"
    args["source_id"] = "v2:src1:journal"  # 旧写法（折叠 src）对不上
    assert _body(_call(args))["code"] == "journal_rule_unknown"
    assert w["catalog"] == {"v1": env.catalog["catalog_rev"], "v2": 42}


def test_v2_only_journal_is_known_from_the_local_v2_cache(env, monkeypatch):
    """Codex #2332：只在 v2 里的刊不在 v1 目录；本机 v2 缓存里有它就认（刊名取 bundle），都没有才 journal_unknown。"""
    from tavotto.engine import journalfacts_fold as fold, journalfacts_remote as facts

    bundles = {"v2-only": (7, {"journal": {"label": "V2 Only Letters"}})}
    monkeypatch.setattr(facts, "cached_bundle", lambda jid: bundles.get(jid))
    monkeypatch.setattr(
        fold,
        "fold",
        lambda b: {
            "rules": {
                "min_raster_dpi": {
                    "value": 300,
                    "qualifier": "min",
                    "src": "v2:g:journal",
                    "fact_id": "f-1",
                }
            },
            "sources": {"v2:g:journal": {}},
            "confirm": [],
        },
    )
    e = _body(_call({"type": "editor", "journal_id": "v2-only", "body": "编辑部要 600 dpi"}))
    assert e["will_send"]["journal_label"] == "V2 Only Letters"
    w = _body(
        _call({"type": "wrong", "journal_id": "v2-only", "rule_key": "min_raster_dpi", "body": "x"})
    )
    assert w["will_send"]["source_id"] == "v2:f-1" and w["will_send"]["catalog"]["v2"] == 7
    assert (
        _body(_call({"type": "editor", "journal_id": "nowhere", "body": "x"}))["code"]
        == "journal_unknown"
    )


def test_missing_and_editor_shapes_are_accepted(env):
    m = _body(_call({"type": "missing", "query": "Journal of Nothing", "url": URL}))
    assert m["will_send"]["query"] == "Journal of Nothing" and "journal_id" not in m["will_send"]
    e = _body(_call({"type": "editor", "journal_id": "fx-alpha", "body": "请把线宽改成 0.5 pt"}))
    assert e["will_send"]["journal_label"] and "rule_key" not in e["will_send"]


def test_next_step_text_never_sends_on_its_own(env):
    out = _body(_call(WRONG))
    step = nextstep.journal_report_confirm(out, WRONG)
    assert step["action"] == "ask_user" and step["options"][-1] == "先不发"
