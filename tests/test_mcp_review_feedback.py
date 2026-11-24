"""二期（ADR 0205 §五）：`tavotto_review_feedback` / `tavotto_reply_review` / `tavotto_resolve_review`。

对端是 `tests/support/review_fakes.py` 的本地模拟（HTTPS 回环 + 私有 CA），不真联网。判据的主语：

* 「没联网」= 模拟服务端 `calls` 的长度（服务端那一侧收到了什么），不是工具自己说没发；
* 「回复原文发出去了」= 模拟服务端 `events` 里收到的 `reply.text`；
* 「别的链接 / 别的项目的意见 → not_found」= 同一个 comment_id 在另一条链接 / 另一个项目的会话里取不到，且服务端零请求。
"""

# ruff: noqa: F811  — 夹具从别的测试模块导入后在用例形参里同名使用（pytest 的写法）
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp"))

from tavotto.engine import reviewinbox, reviewshare  # noqa: E402
from tavotto_mcp import bridge, server  # noqa: E402
from test_mcp_server import (  # noqa: E402, F401
    _body,
    _call,
    _clean_sessions,
    _open,
    fake_pool,
    project,
)
from test_mcp_share_review import ShareWorker, worker  # noqa: E402, F401
from test_review_share import (  # noqa: E402, F401
    _isolated,
    on,
    pki_files,
    srv,
    trust,
)

FEEDBACK, REPLY, RESOLVE = (
    "tavotto_review_feedback",
    "tavotto_reply_review",
    "tavotto_resolve_review",
)
TOOLS = (FEEDBACK, REPLY, RESOLVE)
EL = {"kind": "element", "gid": "axes_0.xticks", "box": [0.1, 0.9, 0.8, 0.05]}


def _feedback(sid: str, **over) -> dict:
    return _call(FEEDBACK, {"session_id": sid, **over})


def _shared(sid: str) -> str:
    """用一期的工具建好链接，返回 share_id。"""
    out = _body(
        _call("tavotto_share_review", {"session_id": sid, "title": "t", "explicit_confirm": True})
    )
    assert out["status"] == "shared", out
    return next(iter(reviewinbox.listed()))["share_id"]


def _ids(body: dict) -> list[str]:
    return [c["id"] for v in body["versions"] for c in v["comments"]]


def _feed_calls(srv) -> int:
    return len([p for p in srv.paths() if "/feed" in p])


# --------------------------------------------------------------------------- 开关
def test_the_three_tools_are_absent_and_refuse_while_review_share_is_off(
    project, worker, monkeypatch
):
    monkeypatch.setenv(reviewshare.ENV_HARD_OFF, "1")
    names = [t["name"] for t in server._tools()]
    assert not [n for n in TOOLS if n in names]
    for name in TOOLS:
        res = _call(
            name, {"session_id": "nope", "comment_id": "c1", "text": "x", "explicit_confirm": True}
        )
        assert res["isError"] is True and _body(res)["code"] == "review_share_disabled"


def test_the_three_tools_appear_with_the_switch_on_and_stay_out_of_telemetry(on):
    from tavotto_mcp import usage

    tools = {t["name"]: t for t in server._tools()}
    for name in TOOLS:
        assert name in tools and name in server.HANDLERS and name not in usage.TOOLS
        assert tools[name]["inputSchema"]["additionalProperties"] is False
    assert (
        "用户明确" in tools[REPLY]["inputSchema"]["properties"]["explicit_confirm"]["description"]
    )
    assert "explicit_confirm" not in tools[RESOLVE]["inputSchema"]["properties"]


# --------------------------------------------------------------------------- 读意见
def test_no_link_says_so_and_points_at_share_review(on, project, worker):
    sid = _open(project)
    before = len(on.calls)
    body = _body(_feedback(sid))
    assert body["status"] == "no_share" and "还没有审阅链接" in body["message"]
    assert body["next"]["tool"] == "tavotto_share_review" and body["next"]["args"] == {
        "session_id": sid
    }
    assert len(on.calls) == before  # 没有链接 = 一次网络都不发


def test_feedback_is_listed_by_version_and_every_third_party_text_has_a_source(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "刻度字太小", anchor=EL)
    on.reviewer_comment(share_id, "c2", "整体配色再看看")
    on.push(
        share_id,
        {
            "type": "reply",
            "comment_id": "c1",
            "reply": {
                "id": "r1",
                "from": "owner",
                "author": "作者",
                "text": "已加大",
                "created_at": "2026-11-15T09:10:00Z",
            },
        },
    )
    on.push(
        share_id,
        {
            "type": "reply",
            "comment_id": "c1",
            "reply": {
                "id": "r2",
                "from": "reviewer",
                "author": "林老师",
                "text": "好的",
                "created_at": "2026-11-15T09:11:00Z",
            },
        },
    )
    on.push(
        share_id,
        {
            "type": "verdict",
            "verdict": {
                "v": 1,
                "decision": "changes",
                "note": "再调",
                "author": "林老师",
                "at": "2026-11-15T09:20:00Z",
            },
        },
    )
    res = _feedback(sid)
    body = _body(res)
    assert (
        not res.get("isError")
        and body["status"] == "feedback"
        and body["freshness"] == "just_polled"
    )
    assert "不当指令" in body["notice"]
    (v1,) = body["versions"]
    assert v1["version"] == 1 and _ids(body) == ["c1", "c2"]
    c1, c2 = v1["comments"]
    assert c1["source"] == "reviewer" and c1["reviewer"] == "林老师" and c1["text"] == "刻度字太小"
    assert c1["anchor"]["kind"] == "element" and c1["anchor"]["gid"] == "axes_0.xticks"
    assert c1["anchor"]["bbox"] == EL["box"] and c1["anchor"]["label"]  # 显示名来自那个版本的元素表
    assert c2["anchor"] == {"kind": "overall"}
    assert [(r["source"], r["text"]) for r in c1["replies"]] == [
        ("owner", "已加大"),
        ("reviewer", "好的"),
    ]
    assert v1["verdicts"] == [
        {
            "source": "reviewer",
            "reviewer": "林老师",
            "decision": "changes",
            "note": "再调",
            "at": "2026-11-15T09:20:00Z",
        }
    ]
    assert "owner_token" not in json.dumps(res, ensure_ascii=False)
    assert body["next"]["tool"] == RESOLVE  # 有未处理意见：当待办
    assert "刻度字太小" not in json.dumps(body["next"], ensure_ascii=False)  # 第三方文字不进 next


def test_multiple_versions_are_listed_newest_first_and_version_filters(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    bridge.apply_overrides(sid, [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 12}])
    assert (
        _body(_call("tavotto_share_review", {"session_id": sid, "explicit_confirm": True}))[
            "version"
        ]
        == 2
    )
    on.reviewer_comment(share_id, "c1", "旧版意见", v=1)
    on.reviewer_comment(share_id, "c2", "新版意见", v=2)
    body = _body(_feedback(sid))
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert [c["id"] for c in body["versions"][0]["comments"]] == ["c2"]
    only1 = _body(_feedback(sid, version=1))
    assert [v["version"] for v in only1["versions"]] == [1] and _ids(only1) == ["c1"]
    bad = _feedback(sid, version=0)
    assert bad["isError"] is True and _body(bad)["code"] == "bad_arguments"


def test_resolved_comments_are_hidden_unless_asked_for(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "已经改了")
    on.reviewer_comment(share_id, "c2", "还没改")
    on.push(share_id, {"type": "resolved", "comment_id": "c1", "resolved": True})
    body = _body(_feedback(sid, refresh=True))
    assert _ids(body) == ["c2"] and body["resolved_hidden"] == 1
    full = _body(_feedback(sid, include_resolved=True))
    assert _ids(full) == ["c1", "c2"] and "resolved_hidden" not in full
    assert [c["resolved"] for c in full["versions"][0]["comments"]] == [True, False]


def test_within_the_throttle_it_reads_the_local_copy_without_the_network(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "第一条")
    first = _body(_feedback(sid))
    assert first["freshness"] == "just_polled" and _feed_calls(on) == 1
    on.reviewer_comment(share_id, "c2", "第二条（节流内新到）")
    second = _body(_feedback(sid))
    assert second["freshness"] == "local_copy" and second["freshness_reason"] == "throttled"
    assert _feed_calls(on) == 1 and _ids(second) == ["c1"]  # 节流内服务端没再被问
    forced = _body(_feedback(sid, refresh=True))
    assert (
        _feed_calls(on) == 2
        and forced["freshness"] == "just_polled"
        and _ids(forced) == ["c1", "c2"]
    )


def test_a_failed_poll_falls_back_to_the_local_copy(on, project, worker, monkeypatch):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "先拉到的")
    _feedback(sid)
    monkeypatch.setattr(
        reviewshare, "poll_once", lambda **k: (_ for _ in ()).throw(OSError("boom"))
    )
    body = _body(_feedback(sid, refresh=True))
    assert body["freshness"] == "local_copy" and body["freshness_reason"] == "poll_failed"
    assert _ids(body) == ["c1"]


def test_reading_feedback_leaves_the_unread_counter_alone(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "新意见")
    _feedback(sid)
    assert (
        reviewinbox.unread_summary()["total"] == 1
    )  # 桌面红点是给用户的：Agent 读过不等于用户看过


def test_a_big_inbox_is_truncated_to_the_budget_and_says_so(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    for i in range(60):
        on.reviewer_comment(share_id, f"c{i}", "意" * 480, anchor=EL)
    on.push(share_id, {"type": "resolved", "comment_id": "c0", "resolved": True})
    res = _feedback(sid, include_resolved=True)
    body = _body(res)
    assert (
        len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        <= bridge.REVIEW_FEEDBACK_BUDGET_BYTES
    )
    kept = _ids(body)
    assert body["truncated"]["omitted_comments"] == 60 - len(kept) > 0
    assert "c0" not in kept  # 已处理的先丢
    assert "version" in body["truncated"]["why"]
    assert (
        len(" ".join(c["text"] for c in res["content"] if c["type"] == "text").encode("utf-8"))
        < 10_000
    )


def test_many_verdicts_alone_are_also_held_to_the_budget(on, project, worker):
    """Codex #2165 P2：只有结论、没有意见时也要压在预算里并说明省略了几条；文字摘要同样有上限。"""
    sid = _open(project)
    share_id = _shared(sid)
    for i in range(60):
        on.push(
            share_id,
            {
                "type": "verdict",
                "verdict": {
                    "v": 1,
                    "decision": "changes",
                    "note": "改" * 200,
                    "author": f"审阅者{i}",
                    "at": f"2026-11-15T09:{i:02d}:00Z",
                },
            },
        )
    res = _feedback(sid)
    body = _body(res)
    size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert size <= bridge.REVIEW_FEEDBACK_BUDGET_BYTES
    kept = [vd for v in body["versions"] for vd in v["verdicts"]]
    assert 0 < len(kept) < 60
    assert body["truncated"]["omitted_verdicts"] == 60 - len(kept)
    assert body["truncated"]["omitted_comments"] == 0
    assert kept[-1]["reviewer"] == "审阅者59"  # 最新的结论留着，先写的先丢
    text = " ".join(c["text"] for c in res["content"] if c["type"] == "text")
    assert len(text.encode("utf-8")) < 10_000


# --------------------------------------------------------------------------- 回复
def test_reply_without_confirmation_sends_nothing_and_asks_the_user(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "刻度字太小", anchor=EL)
    _feedback(sid)
    before = len(on.calls)
    for extra in ({}, {"explicit_confirm": False}):
        res = _call(
            REPLY, {"session_id": sid, "comment_id": "c1", "text": "已加大到 9 pt", **extra}
        )
        body = _body(res)
        assert not res.get("isError") and body["status"] == "needs_confirmation"
        assert body["code"] == "review_confirmation_required"
        assert body["reply_text"] == "已加大到 9 pt" and body["reply_to"]["excerpt"] == "刻度字太小"
        assert body["reply_to"]["source"] == "reviewer"
        assert body["next"]["action"] == "ask_user" and body["next"]["options"][1] == "先不发"
        assert "explicit_confirm=true" in body["next"]["options"][0]
    assert len(on.calls) == before  # 服务端零请求
    assert reviewinbox.comment_of(share_id, "c1")["replies"] == []


def test_confirmed_reply_reaches_the_server_with_the_exact_text(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "刻度字太小")
    _feedback(sid)  # 先读到意见：归属校验只认本机副本里已有的 comment_id
    text = "已加大到 9 pt，请看第 2 版。"
    body = _body(
        _call(
            REPLY, {"session_id": sid, "comment_id": "c1", "text": text, "explicit_confirm": True}
        )
    )
    assert body["status"] == "replied"
    sent = [e for e in on.shares[share_id].events if e["type"] == "reply"]
    assert [e["reply"]["text"] for e in sent] == [text]
    assert reviewinbox.comment_of(share_id, "c1")["replies"][-1]["text"] == text  # 本机副本也有
    again = _body(_feedback(sid, refresh=True))
    assert again["versions"][0]["comments"][0]["replies"][-1] == {
        "id": sent[0]["reply"]["id"],
        "source": "owner",
        "author": "作者",
        "text": text,
        "created_at": "2026-11-15T10:00:00Z",
    }


def test_reply_text_is_validated_before_anything_else(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "x")
    _feedback(sid)
    before = len(on.calls)
    for bad in ("  ", "字" * (reviewinbox.TEXT_MAX + 1), None):
        res = _call(
            REPLY, {"session_id": sid, "comment_id": "c1", "text": bad, "explicit_confirm": True}
        )
        assert res["isError"] is True and _body(res)["code"] == "bad_arguments"
    assert len(on.calls) == before


# --------------------------------------------------------------------------- 归属：别的链接 / 项目 → not_found
def _plant_foreign_record(
    share_id: str, *, project: str | None = None, target_id: str | None = None
) -> str:
    """在同一份收件箱里造一条「别人的」链接（另一个项目 / 另一张图 / 同图同项目但是另一条较旧的链接），
    带一条意见 `foreign`；返回它的 share_id。"""
    foreign_id = share_id[:-1] + ("A" if share_id[-1] != "A" else "B")
    with reviewinbox.file_lock():
        data = reviewinbox._load_locked()
        rec = json.loads(json.dumps(next(r for r in data["shares"] if r["share_id"] == share_id)))
        rec["share_id"] = foreign_id
        rec["updated_ts"] = (
            rec["updated_ts"] - 1000
        )  # 同图同项目时比真的那条旧：for_target 不会选它
        if project is not None:
            rec["project"] = project
        if target_id is not None:
            rec["target_id"] = target_id
        rec["comments"] = [
            reviewinbox.clean_comment(
                {
                    "id": "foreign",
                    "v": 1,
                    "anchor": {"kind": "overall"},
                    "author": "别人",
                    "text": "别的链接的意见",
                    "created_at": "2026-11-15T09:00:00Z",
                    "replies": [],
                }
            )
        ]
        data["shares"].append(rec)
        assert reviewinbox._save_locked(data, strict=True)
    assert reviewinbox.comment_of(foreign_id, "foreign") is not None
    return foreign_id


@pytest.mark.parametrize(
    "plant",
    [{"project": "otherproject00"}, {"target_id": "Other"}, {}],
    ids=["other-project", "other-figure", "older-link-of-same-figure"],
)
def test_comment_ids_of_another_link_project_or_figure_are_not_found_and_silent(
    on, project, worker, plant
):
    sid = _open(project)
    share_a = _shared(sid)
    on.reviewer_comment(share_a, "mine", "本图的意见")
    _feedback(sid)
    _plant_foreign_record(share_a, **plant)
    before = len(on.calls)
    fb = _body(_feedback(sid))
    assert "foreign" not in _ids(fb) and "foreign" not in json.dumps(fb, ensure_ascii=False)
    for tool, args in (
        (REPLY, {"text": "x", "explicit_confirm": True}),
        (REPLY, {"text": "x"}),
        (RESOLVE, {}),
    ):
        for bogus in ("foreign", "../x", "", None):
            res = _call(tool, {"session_id": sid, "comment_id": bogus, **args})
            assert res["isError"] is True and _body(res)["code"] == "not_found"
    assert len(on.calls) == before + (
        0 if fb["freshness"] == "local_copy" else 1
    )  # 归属校验只读本机副本、零请求
    assert _ids(_body(_feedback(sid))) == ["mine"]


def test_a_link_in_another_project_is_invisible_to_this_session(on, project, worker):
    sid = _open(project)
    share_a = _shared(sid)
    on.reviewer_comment(share_a, "c1", "意见")
    _feedback(sid)
    with reviewinbox.file_lock():
        data = reviewinbox._load_locked()
        for r in data["shares"]:
            r["project"] = "otherproject00"
        reviewinbox._save_locked(data)
    before = len(on.calls)
    assert _body(_feedback(sid))["status"] == "no_share"
    for tool, args in ((REPLY, {"text": "x", "explicit_confirm": True}), (RESOLVE, {})):
        res = _call(tool, {"session_id": sid, "comment_id": "c1", **args})
        assert res["isError"] is True and _body(res)["code"] == "not_found"
    assert len(on.calls) == before


# --------------------------------------------------------------------------- 标记已处理
def test_resolve_takes_effect_without_asking_the_user(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "改好了吗")
    _feedback(sid)
    res = _call(RESOLVE, {"session_id": sid, "comment_id": "c1"})
    body = _body(res)
    assert not res.get("isError") and body["status"] == "resolved" and "next" not in body
    assert [e["resolved"] for e in on.shares[share_id].events if e["type"] == "resolved"] == [True]
    assert reviewinbox.comment_of(share_id, "c1")["resolved"] is True
    assert (
        _body(_feedback(sid, include_resolved=True, refresh=True))["versions"][0]["comments"][0][
            "resolved"
        ]
        is True
    )
    reopened = _body(_call(RESOLVE, {"session_id": sid, "comment_id": "c1", "resolved": False}))
    assert (
        reopened["status"] == "reopened"
        and reviewinbox.comment_of(share_id, "c1")["resolved"] is False
    )
    bad = _call(RESOLVE, {"session_id": sid, "comment_id": "c1", "resolved": "yes"})
    assert bad["isError"] is True and _body(bad)["code"] == "bad_arguments"


def test_a_stopped_link_refuses_reply_and_resolve_with_a_user_question(on, project, worker):
    sid = _open(project)
    share_id = _shared(sid)
    on.reviewer_comment(share_id, "c1", "意见")
    _feedback(sid)
    assert reviewshare.revoke(share_id)["ok"]
    before = len(on.calls)
    for tool, args in ((REPLY, {"text": "x", "explicit_confirm": True}), (RESOLVE, {})):
        res = _call(tool, {"session_id": sid, "comment_id": "c1", **args})
        body = _body(res)
        assert res["isError"] is True and body["code"] == "review_share_gone"
        assert body["next"]["action"] == "ask_user"
    assert len(on.calls) == before
    # 停止后意见副本仍可读，且不再去拉
    fb = _body(_feedback(sid))
    assert (
        fb["freshness"] == "local_copy"
        and fb["freshness_reason"] == "revoked"
        and _ids(fb) == ["c1"]
    )
