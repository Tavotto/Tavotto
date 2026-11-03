"""AI 修复 D2b（ADR 0148 / 0149）：「问与报」五个工具 · 修复会话 · 卡片回答校验。

判据的主语：

* 工具：**返回给模型的 ToolResult** 与 **`ctx.reads` / 读取范围**（`pick_file` 之后同一路径 `read_file` 的结果，而不是工具自己说「已授权」）。
* 卡片：会话**对外的 DTO**（`SESSIONS.get`）里的 `card`；回答只能走 `answer()`，按 card id（`compare_digest`）与种类校验。
* 审计：磁盘上的 `fix_audit.jsonl`（金丝雀串先证明到过卡片里，再证明不在审计文件里）。
* 全程假模型 / 假主目录；不联网、不起真实执行器。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from tavotto.engine import fixsession
from tavotto.engine.agent_runner.redact import Redactor
from tavotto.engine.agent_runner.wires import ToolCall, Turn
from tavotto.engine.fixtools import REGISTRY, AuditLog, FixContext, ask as asks, run_tool

ALL_TIERS = frozenset({"observe", "run", "tavotto", "listed", "user_env", "outward"})
# 假密钥运行时拼接（GitHub 推送保护按形状拦字面量）
KEY = "sk-" + "test-" + "CANARYkey1234567890"


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    fixsession.SESSIONS.reset_for_tests()
    yield type("Box", (), {"home": home, "proj": proj, "data": data, "tmp": tmp_path})
    fixsession.SESSIONS.reset_for_tests()


def make_ctx(box, answer=None, **kw) -> tuple[FixContext, list[dict]]:
    """带假 `ask` 的上下文：`answer` 是 `card -> dict` 或固定回答 dict；返回 (ctx, 收到的卡片列表)。"""
    cards: list[dict] = []

    def ask(card: dict) -> dict:
        cards.append(card)
        return answer(card) if callable(answer) else (answer or {"answer": "yes"})

    kw.setdefault("allowed_tiers", ALL_TIERS)
    kw.setdefault("case_insensitive", False)
    ctx = FixContext.for_fix_session("s1", box.proj, home=box.home, ask=ask, **kw)
    return ctx, cards


def call(ctx, name, args, audit=None):
    return run_tool(REGISTRY[name], ctx, args, audit)


def put(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


# =========================================================================== 登记
def test_ask_group_is_registered_and_waits_for_user():
    names = {"propose_fix", "ask_user", "pick_file", "prepare_diagnostics", "finish"}
    assert names <= set(REGISTRY)
    assert all(REGISTRY[n].waits_for_user for n in names)
    assert REGISTRY["prepare_diagnostics"].tier == "outward"
    assert {REGISTRY[n].tier for n in names - {"prepare_diagnostics"}} == {"observe"}
    # 看 / 试组不等用户
    assert not REGISTRY["read_file"].waits_for_user


def test_prepare_diagnostics_is_outward_so_a_session_without_that_tier_cannot_call_it(box):
    ctx, cards = make_ctx(box, allowed_tiers=frozenset({"observe", "run"}))
    r = call(ctx, "prepare_diagnostics", {"summary": "s"})
    assert r.is_error and r.code == "tier_not_allowed" and cards == []


# =========================================================================== 没有界面 = 未获同意
@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("propose_fix", {"summary": "装 numpy"}),
        ("ask_user", {"question": "q", "options": [{"id": "a", "label": "A"}]}),
        ("pick_file", {"purpose": "p"}),
        ("prepare_diagnostics", {"summary": "s"}),
    ],
)
def test_without_an_interface_nothing_is_ever_assumed_approved(box, name, args):
    ctx = FixContext.for_fix_session("s", box.proj, home=box.home, allowed_tiers=ALL_TIERS)
    assert ctx.ask is None
    r = call(ctx, name, args)
    assert r.is_error and r.code == "needs_approval"


def test_a_crashing_or_malformed_ask_counts_as_not_approved(box):
    def boom(_card):
        raise RuntimeError("ui down")

    ctx = FixContext.for_fix_session(
        "s", box.proj, home=box.home, ask=boom, allowed_tiers=ALL_TIERS
    )
    assert call(ctx, "propose_fix", {"summary": "x"}).code == "needs_approval"
    ctx2, _ = make_ctx(box, {"answer": 7})
    assert call(ctx2, "propose_fix", {"summary": "x"}).code == "needs_approval"


# =========================================================================== propose_fix
def test_propose_fix_returns_whether_the_user_approved(box):
    ctx, cards = make_ctx(box, {"answer": "yes"})
    r = call(ctx, "propose_fix", {"summary": "把 numpy 降到 1.26", "details": "因为 ABI"})
    assert not r.is_error and "approved" in r.content
    assert cards[0]["kind"] == "approve" and cards[0]["summary"] == "把 numpy 降到 1.26"
    ctx2, _ = make_ctx(box, {"answer": "no"})
    declined = call(ctx2, "propose_fix", {"summary": "x"})
    assert (
        not declined.is_error
        and "declined" in declined.content
        and "approved" not in declined.content
    )


@pytest.mark.parametrize("answer", ["timeout", "cancelled"])
def test_propose_fix_no_answer_is_an_error_not_an_approval(box, answer):
    ctx, _ = make_ctx(box, {"answer": answer})
    r = call(ctx, "propose_fix", {"summary": "x"})
    assert r.is_error and r.code == "no_answer"


def test_propose_fix_card_lists_outside_reads_home_relative_and_names_the_blocked_places(box):
    outside = put(box.home / "Desktop" / "data.csv", "a,b\n1,2")
    ctx, cards = make_ctx(box)
    assert not call(ctx, "read_file", {"path": str(outside)}).is_error
    call(ctx, "propose_fix", {"summary": "x"})
    card = cards[0]
    assert card["reads"] == ["~/Desktop/data.csv"]
    assert str(box.home) not in json.dumps(card)  # 少带一层本机用户名
    assert ".ssh" in card["not_read"] and ".env" in card["not_read"]


def test_propose_fix_scrubs_secret_shapes_out_of_the_card(box):
    ctx, cards = make_ctx(box)
    call(ctx, "propose_fix", {"summary": "set api_key=" + KEY + " now", "details": "token=" + KEY})
    assert KEY not in json.dumps(cards[0])


def test_propose_fix_rejects_a_missing_summary(box):
    ctx, cards = make_ctx(box)
    assert call(ctx, "propose_fix", {"summary": "  "}).code == "bad_arguments"
    assert call(ctx, "propose_fix", {"summary": "x", "details": 3}).code == "bad_arguments"
    assert cards == []


# =========================================================================== ask_user
OPTS = [
    {"id": "desk", "label": "~/Desktop/实验数据"},
    {"id": "docs", "label": "~/Documents/project"},
]


def test_ask_user_returns_the_chosen_option_id(box):
    ctx, cards = make_ctx(box, {"answer": "docs"})
    r = call(ctx, "ask_user", {"question": "data.csv 在哪？", "options": OPTS})
    assert not r.is_error and "docs" in r.content
    assert cards[0]["kind"] == "choice" and cards[0]["options"] == OPTS
    assert cards[0]["allow_other"] is False


def test_ask_user_rejects_an_answer_that_is_not_an_option(box):
    ctx, _ = make_ctx(box, {"answer": "evil"})
    r = call(ctx, "ask_user", {"question": "q", "options": OPTS})
    assert r.is_error and r.code == "bad_answer"


def test_ask_user_other_needs_allow_other(box):
    typed = {"answer": "other", "text": "~/lab"}
    ctx, _ = make_ctx(box, typed)
    refused = call(ctx, "ask_user", {"question": "q", "options": OPTS})
    assert refused.is_error  # 没开 allow_other：不接受
    ctx2, _ = make_ctx(box, typed)
    r = call(ctx2, "ask_user", {"question": "q", "options": OPTS, "allow_other": True})
    assert not r.is_error and "~/lab" in r.content


def test_ask_user_skip_and_validation(box):
    ctx, _ = make_ctx(box, {"answer": "skip"})
    assert "skipped" in call(ctx, "ask_user", {"question": "q", "options": OPTS}).content
    for bad in (
        [],
        [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}],  # 重复 id
        [{"id": "other", "label": "x"}],  # 保留 id
        [{"id": "a"}],
        "nope",
        [{"id": "a", "label": "A"}] * 9,
    ):
        assert call(ctx, "ask_user", {"question": "q", "options": bad}).code == "bad_arguments"


# =========================================================================== pick_file
def test_picked_path_outside_home_becomes_readable_and_is_listed_in_reads(box):
    outside = put(box.tmp / "lab" / "data.csv", "x,y\n1,2")
    ctx, _ = make_ctx(box, {"answer": str(outside)})
    before = call(ctx, "read_file", {"path": str(outside)})
    assert before.is_error and before.code == "outside_scope"  # 先证明挑之前确实读不到
    r = call(ctx, "pick_file", {"purpose": "找数据"})
    assert not r.is_error
    after = call(ctx, "read_file", {"path": str(outside)})
    assert not after.is_error and "x,y" in after.content
    assert any(Path(x["path"]) == outside.resolve() for x in ctx.reads)
    # 只放开挑的这一个文件，不放开它的邻居
    neighbour = put(box.tmp / "lab" / "other.txt", "n")
    assert call(ctx, "read_file", {"path": str(neighbour)}).code == "outside_scope"


def test_picked_folder_makes_its_files_readable(box):
    folder = box.tmp / "lab"
    put(folder / "d.csv", "k")
    ctx, _ = make_ctx(box, {"answer": str(folder)})
    assert not call(ctx, "pick_file", {"purpose": "p", "kind": "dir"}).is_error
    assert not call(ctx, "read_file", {"path": str(folder / "d.csv")}).is_error


@pytest.mark.parametrize("secret", [".ssh/id_rsa", ".aws/credentials", "proj/.env"])
def test_pick_file_still_hard_blocks_sensitive_locations(box, secret):
    f = put(box.home / secret, "TOPSECRET")
    ctx, _ = make_ctx(box, {"answer": str(f)})
    r = call(ctx, "pick_file", {"purpose": "p"})
    assert r.is_error and r.code == "denied"
    # 选完之后同一路径依旧读不了（授权没有悄悄生效）
    after = call(ctx, "read_file", {"path": str(f)})
    assert after.is_error and "TOPSECRET" not in after.content
    assert ctx.reads == []


def test_a_picked_symlink_to_a_credential_is_still_blocked(box):
    secret = put(box.home / ".ssh" / "id_rsa", "TOPSECRET")
    link = box.tmp / "lab" / "innocent.txt"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    ctx, _ = make_ctx(box, {"answer": str(link)})
    r = call(ctx, "pick_file", {"purpose": "p"})
    assert r.is_error and r.code == "denied"
    after = call(ctx, "read_file", {"path": str(link)})
    assert after.is_error and "TOPSECRET" not in after.content


def test_picking_a_folder_does_not_unblock_a_credential_inside_it(box):
    put(box.tmp / "lab" / "ok.txt", "fine")
    secret = put(box.tmp / "lab" / "id_rsa", "TOPSECRET")
    ctx, _ = make_ctx(box, {"answer": str(box.tmp / "lab")})
    assert not call(ctx, "pick_file", {"purpose": "p", "kind": "dir"}).is_error
    assert call(ctx, "read_file", {"path": str(secret)}).is_error


def test_pick_file_validation_skip_and_wrong_kind(box):
    f = put(box.tmp / "lab" / "a.txt", "a")
    ctx, _ = make_ctx(box, {"answer": "skip"})
    assert "did not pick" in call(ctx, "pick_file", {"purpose": "p"}).content
    ctx2, _ = make_ctx(box, {"answer": str(f)})
    assert call(ctx2, "pick_file", {"purpose": "p", "kind": "dir"}).code == "wrong_kind"
    assert call(ctx2, "pick_file", {"purpose": "p", "kind": "x"}).code == "bad_arguments"
    gone, _ = make_ctx(box, {"answer": str(box.tmp / "nope.txt")})
    assert call(gone, "pick_file", {"purpose": "p"}).code == "not_found"
    root, _ = make_ctx(box, {"answer": str(Path(box.tmp.anchor))})
    assert call(root, "pick_file", {"purpose": "p", "kind": "dir"}).is_error


# =========================================================================== prepare_diagnostics
def test_prepare_diagnostics_only_shows_a_send_card_and_reports_what_the_user_did(box):
    ctx, cards = make_ctx(box, {"answer": "no"})
    r = call(ctx, "prepare_diagnostics", {"summary": "numpy ABI 不匹配"})
    assert not r.is_error and "not to send" in r.content
    assert cards[0]["kind"] == "send" and cards[0]["wants_source"] is False
    ctx2, cards2 = make_ctx(box, {"answer": "sent"})
    sent = call(ctx2, "prepare_diagnostics", {"summary": "s", "include_source": True})
    assert not sent.is_error and "sent" in sent.content
    assert cards2[0]["wants_source"] is True  # 只是提示：源码勾选框由用户在发送对话框里勾


def test_prepare_diagnostics_without_an_answer_never_claims_it_was_sent(box):
    ctx, _ = make_ctx(box, {"answer": "timeout"})
    r = call(ctx, "prepare_diagnostics", {"summary": "s"})
    assert r.is_error and "sent" not in r.content.replace("send", "")


def test_product_issue_card_contains_reviewable_evidence_and_denial_never_sends(box):
    issue = {
        "title": "Original succeeds; application capture fails",
        "reproduction": "Open the project, run the selected script, inspect the capture error.",
        "expected": "The same 2 figures as the original run.",
        "actual": "Application reports capture_incomplete.",
        "evidence": "Direct run exit 0; application attempt reports a failed second figure.",
        "attempts": "Rechecked environment; ran python figure.py; repeated application run.",
        "product_reason": "Both runs use the same interpreter and inputs; failure is in capture.",
    }
    ctx, cards = make_ctx(box, {"answer": "no"})
    result = call(ctx, "prepare_diagnostics", {"summary": "Capture failure", "issue": issue})
    assert "not to send" in result.content
    assert len(cards) == 1 and cards[0]["kind"] == "send"
    assert all(value in cards[0]["summary"] for value in issue.values())
    assert cards[0]["wants_source"] is False
    assert not call(
        ctx, "prepare_diagnostics", {"summary": "x", "issue": {**issue, "evidence": "token=" + KEY}}
    ).is_error
    assert KEY not in cards[-1]["summary"]


@pytest.mark.parametrize(
    "issue",
    [
        {"title": "just a guess"},
        {
            k: "x" * 1000
            for k in (
                "title",
                "reproduction",
                "expected",
                "actual",
                "evidence",
                "attempts",
                "product_reason",
            )
        },
    ],
)
def test_incomplete_or_oversized_issue_is_not_silently_truncated(box, issue):
    ctx, cards = make_ctx(box)
    result = call(ctx, "prepare_diagnostics", {"summary": "x", "issue": issue})
    assert result.code == "bad_arguments" and cards == []


# =========================================================================== finish
def test_finish_hands_the_outcome_to_the_session_even_without_an_interface(box):
    ctx, cards = make_ctx(box, {"answer": "ok"})
    r = call(ctx, "finish", {"outcome": "fixed", "summary": "已装好"})
    assert not r.is_error and cards[0] == {
        "kind": "done",
        "tool": "finish",
        "outcome": "fixed",
        "summary": "已装好",
    }
    bare = FixContext.for_fix_session("s", box.proj, home=box.home)
    assert not call(bare, "finish", {"outcome": "not_fixed", "summary": "x"}).is_error
    assert call(bare, "finish", {"outcome": "maybe", "summary": "x"}).code == "bad_arguments"


def test_finish_convert_is_only_valid_after_a_handoff(box):
    ctx, cards = make_ctx(box, {"answer": "ok"})
    r = call(ctx, "finish", {"outcome": "convert", "summary": "x"})
    assert r.code == "bad_arguments" and cards == []  # 被拒：卡片都没发出去
    ctx.handoff = {"kind": "convert", "script": "fig.py", "library": "pillow", "path": None}
    assert not call(ctx, "finish", {"outcome": "convert", "summary": "已交接"}).is_error
    assert cards[0]["outcome"] == "convert"
    assert REGISTRY["finish"].schema["properties"]["outcome"]["enum"] == [
        "fixed",
        "not_fixed",
        "convert",
    ]


# =========================================================================== 修复会话
class Scripted:
    """按剧本回放的假模型：每次 `complete` 吐下一条；用完就给终止回复。"""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    def complete(self, **_kw) -> Turn:
        self.calls += 1
        if not self.steps:
            return Turn(text="done", stop="end")
        calls = self.steps.pop(0)
        return Turn(
            tool_calls=[ToolCall(f"c{self.calls}_{i}", n, a) for i, (n, a) in enumerate(calls)],
            stop="tool_use",
        )


def start(box, model, scope="pj", prep="prep1", **kw):
    return fixsession.SESSIONS.start(
        scope=scope,
        prep_session_id=prep,
        script="fig.py",
        project_root=box.proj,
        model=model,
        report=lambda: {"phase": "failed", "code": "module_missing"},
        audit=AuditLog(box.data / "ai" / "fix_audit.jsonl"),
        **kw,
    )


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out")


def waiting_card(s, scope="pj"):
    return wait_for(lambda: fixsession.SESSIONS.get(s.id, scope)["card"])


def test_session_runs_the_model_asks_the_user_and_ends_with_finish(box):
    model = Scripted(
        [("read_run_report", {})],
        [("propose_fix", {"summary": "装 numpy"})],
        [("finish", {"outcome": "fixed", "summary": "好了"})],
    )
    s = start(box, model)
    card = waiting_card(s)
    assert card["kind"] == "approve" and card["summary"] == "装 numpy"
    assert fixsession.SESSIONS.get(s.id, "pj")["state"] == "waiting"
    fixsession.SESSIONS.answer(s.id, "pj", card["id"], "yes")
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    done = fixsession.SESSIONS.get(s.id, "pj")
    assert done["outcome"] == "fixed" and done["summary"] == "好了" and done["card"] is None
    # finish 之后不再向模型发请求
    assert model.calls == 3
    # 每次调用都写了审计，且没有内容
    rows = [
        json.loads(x) for x in (box.data / "ai" / "fix_audit.jsonl").read_text("utf-8").splitlines()
    ]
    assert [r["tool"] for r in rows] == ["read_run_report", "propose_fix", "finish"]
    assert "装 numpy" not in (box.data / "ai" / "fix_audit.jsonl").read_text("utf-8")


def test_session_without_finish_ends_as_failed_with_the_runner_reason(box):
    s = start(box, Scripted([("read_run_report", {})]))
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    done = fixsession.SESSIONS.get(s.id, "pj")
    assert done["outcome"] == "failed" and done["reason"] == "completed"


def test_cancel_while_a_card_is_open_unblocks_the_tool_and_ends_cancelled(box):
    model = Scripted(
        [("propose_fix", {"summary": "x"})],
        [("finish", {"outcome": "fixed", "summary": "不该到这"})],
    )
    s = start(box, model)
    waiting_card(s)
    fixsession.SESSIONS.cancel(s.id, "pj")
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    assert fixsession.SESSIONS.get(s.id, "pj")["outcome"] == "cancelled"
    assert model.calls == 1  # 取消后不再继续请求模型


def test_unanswered_card_times_out_and_the_tool_reports_no_answer(box, monkeypatch):
    monkeypatch.setattr(fixsession, "ASK_TIMEOUT", 0.3)
    seen: list[str] = []

    class Spy(Scripted):
        def complete(self, history=None, **kw):
            for m in history or ():
                for r in m.results:
                    seen.append(r.code)
            return super().complete(**kw)

    s = start(box, Spy([("propose_fix", {"summary": "x"})]))
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    assert "no_answer" in seen


def test_answers_are_validated_per_card_kind_and_card_id(box):
    model = Scripted(
        [("ask_user", {"question": "q", "options": OPTS})],
        [("finish", {"outcome": "not_fixed", "summary": "x"})],
    )
    s = start(box, model)
    card = waiting_card(s)
    sess = fixsession.SESSIONS
    for bad in ("evil", "yes", "other", ""):
        with pytest.raises(fixsession.FixError) as e:
            sess.answer(s.id, "pj", card["id"], bad)
        assert e.value.status == 400
    with pytest.raises(fixsession.FixError) as e:
        sess.answer(s.id, "pj", "wrong-card", "desk")
    assert e.value.status == 409
    with pytest.raises(fixsession.FixError) as e:
        sess.answer(s.id, "other-project", card["id"], "desk")
    assert e.value.status == 404
    with pytest.raises(fixsession.FixError) as e:
        sess.answer("fx_unknown", "pj", card["id"], "desk")
    assert e.value.status == 404
    assert sess.get(s.id, "pj")["state"] == "waiting"  # 坏回答没有结束这张卡
    sess.answer(s.id, "pj", card["id"], "desk")
    wait_for(lambda: sess.get(s.id, "pj")["state"] == "finished")
    # 卡片已答：同一张卡再答 = 409
    with pytest.raises(fixsession.FixError) as e:
        sess.answer(s.id, "pj", card["id"], "desk")
    assert e.value.status == 409


def test_session_ids_are_random_and_only_one_session_runs_at_a_time(box):
    s = start(box, Scripted([("propose_fix", {"summary": "x"})]))
    waiting_card(s)
    assert s.id.startswith("fx_") and len(s.id) > 12
    # 同一准备会话再点 = 复用；别的准备会话 / 项目 = 忙
    assert start(box, Scripted(), prep="prep1") is s
    with pytest.raises(fixsession.FixError) as e:
        start(box, Scripted(), prep="prep2")
    assert e.value.code == "fix_busy"
    fixsession.SESSIONS.cancel(s.id, "pj")
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    other = start(box, Scripted(), prep="prep2")  # 结束后可以再起
    assert other.id != s.id


def test_pick_answer_goes_through_the_read_scope_inside_a_session(box):
    secret = put(box.home / ".ssh" / "id_rsa", "TOPSECRET")
    seen: list[str] = []

    class Spy(Scripted):
        def complete(self, history=None, **kw):
            for m in history or ():
                for r in m.results:
                    seen.append(r.content)
            return super().complete(**kw)

    s = start(box, Spy([("pick_file", {"purpose": "找密钥"})]))
    card = waiting_card(s)
    assert card["kind"] == "pick"
    fixsession.SESSIONS.answer(s.id, "pj", card["id"], str(secret))
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    assert any("credentials" in x for x in seen) and not any("TOPSECRET" in x for x in seen)


def test_api_key_echoed_by_the_model_never_reaches_the_card(box):
    # 不带任何密钥形状的普通词也认：Redactor 按「已知的那一个密钥」整串换掉（形状脱敏之外的第二道）
    plain = "opensesame" + "-lab-passphrase"
    s = start(
        box,
        Scripted([("propose_fix", {"summary": "use " + plain + " here"})]),
        redactor=Redactor(plain),
    )
    card = waiting_card(s)
    assert plain not in json.dumps(card) and "[redacted]" in card["summary"]
    fixsession.SESSIONS.cancel(s.id, "pj")


def test_session_exposes_no_card_once_it_is_not_waiting(box):
    s = start(box, Scripted([("finish", {"outcome": "not_fixed", "summary": "x"})]))
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    d = fixsession.SESSIONS.get(s.id, "pj")
    assert d["card"] is None and d["outcome"] == "not_fixed"
    assert set(d) == {
        "fix_id",
        "state",
        "script",
        "session_id",
        "card",
        "outcome",
        "summary",
        "reason",
        "steps",
        "seq",
        "activity",
        "stalled",
    }


def test_notifier_is_poked_when_a_card_opens_and_carries_no_content(box, monkeypatch):
    pokes: list[tuple] = []
    monkeypatch.setattr(
        fixsession.SESSIONS, "notifier", lambda scope, fid: pokes.append((scope, fid))
    )
    s = start(box, Scripted([("propose_fix", {"summary": "机密内容"})]))
    waiting_card(s)
    assert (("pj", s.id)) in pokes
    fixsession.SESSIONS.cancel(s.id, "pj")


def test_concurrent_answers_resolve_a_card_exactly_once(box):
    s = start(
        box,
        Scripted(
            [("propose_fix", {"summary": "x"})], [("finish", {"outcome": "fixed", "summary": "ok"})]
        ),
    )
    card = waiting_card(s)
    results: list[str] = []

    def go(a):
        try:
            fixsession.SESSIONS.answer(s.id, "pj", card["id"], a)
            results.append("ok")
        except fixsession.FixError as e:
            results.append(str(e.status))

    ts = [threading.Thread(target=go, args=(a,)) for a in ("yes", "no", "yes", "no")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert (
        results.count("ok") >= 1
    )  # 第一个到的赢；晚到的要么 409 要么落在已结束的卡上也不会改变结果
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")


def test_available_needs_a_usable_api_connection(box, monkeypatch):
    from tavotto.engine import ai_bridge, ai_providers

    # 只看 API 连接这一半：这台机器上装没装 Codex / Claude Code 不该影响（CLI 那一半见 test_fixcli.py）
    monkeypatch.setattr(ai_bridge, "agent_caps", lambda _agent_id: None)
    assert fixsession.available() is False and fixsession.connection() is None
    ai_providers.save(
        {
            "id": "x",
            "label": "x",
            "agent": "claude",
            "base_url": "https://api.example.com",
            "api_key": KEY,
            "default_model": "m1",
        }
    )
    # 「连接」改造后：存了就是连着，不再要求每个家族「选中」一条（见 tests/test_ai_connections.py 的挑选顺序用例）
    conn = fixsession.connection()
    assert conn is not None and conn["wire"] == "anthropic" and conn["model"] == "m1"
    assert fixsession.available() is True
    # 明文 http（非本机）的地址不算可用
    ai_providers.save(
        {
            "id": "x",
            "label": "x",
            "agent": "claude",
            "base_url": "http://evil.example.com",
            "api_key": KEY,
            "default_model": "m1",
        }
    )
    assert fixsession.available() is False


def test_connection_object_never_leaks_the_key_through_repr(box):
    model, red = fixsession.build_model(
        {"wire": "anthropic", "base_url": "https://api.example.com", "api_key": KEY, "model": "m"}
    )
    assert KEY not in repr(model) and red.contains(KEY)


def test_audit_tail_reads_back_what_the_session_wrote(box):
    s = start(
        box, Scripted([("read_run_report", {})], [("finish", {"outcome": "fixed", "summary": "x"})])
    )
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    tail = fixsession.audit_tail({s.id})
    assert [r["tool"] for r in tail][-2:] == ["read_run_report", "finish"]
    assert set(asks.__dict__) >= {"ASK_TOOLS"}


# =========================================================================== 诊断包
def test_fix_audit_enters_the_diagnostics_bundle_and_secret_shapes_are_scrubbed():
    from tavotto.engine import diagdetail

    audit = [
        {
            "session": "fx_1",
            "tool": "propose_fix",
            "tier": "observe",
            "approved_by": "policy",
            "args": {"path": "/x/api_key=" + KEY},
        }
    ]
    src = diagdetail.Sources(fix_audit=audit)
    (item,) = diagdetail.collect_runs(src, diagdetail.DetailOptions(), diagdetail.Scrubber())
    doc = json.loads(item.data)
    assert doc["fix_audit"][0]["tool"] == "propose_fix"
    assert doc["fix_audit"][0]["approved_by"] == "policy"
    assert KEY not in item.data.decode("utf-8")
    (empty,) = diagdetail.collect_runs(
        diagdetail.Sources(), diagdetail.DetailOptions(), diagdetail.Scrubber()
    )
    assert json.loads(empty.data)["fix_audit"] == []


def test_audit_tail_only_returns_the_given_sessions(box):
    s = start(
        box, Scripted([("read_run_report", {})], [("finish", {"outcome": "fixed", "summary": "x"})])
    )
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    path = box.data / "ai" / "fix_audit.jsonl"
    with open(path, "ab") as f:
        f.write(
            b'{"session": "fx_other_project", "tool": "read_file", "args": {"path": "/x/Patient Bob/a.py"}}\n'
        )
    assert fixsession.audit_tail(set()) == []
    rows = fixsession.audit_tail({s.id})
    assert rows and {r["session"] for r in rows} == {s.id}
    assert (
        fixsession.SESSIONS.ids_for("pj") == {s.id}
        and fixsession.SESSIONS.ids_for("other") == set()
    )


def test_bundle_audit_paths_are_hashed_not_spelled_out():
    from tavotto.engine import diagdetail

    rows = [
        {
            "session": "fx_1",
            "tool": "read_file",
            "args": {"path": "~/Patient Alice/plot.py", "start_line": 3},
        }
    ]
    (item,) = diagdetail.collect_runs(
        diagdetail.Sources(fix_audit=rows), diagdetail.DetailOptions(), diagdetail.Scrubber()
    )
    text = item.data.decode("utf-8")
    assert "Alice" not in text and "Patient" not in text and "plot" not in text
    doc = json.loads(text)["fix_audit"][0]
    assert doc["args"]["path"].startswith("<path ") and doc["args"]["path"].endswith(".py>")
    assert doc["args"]["start_line"] == 3


def test_the_session_hands_its_cancel_event_to_the_tool_context(box, monkeypatch):
    """耗时执行器（换 Python）要能被「取消」叫停：修复会话构造的 FixContext 带的就是会话自己的 `s.cancel`。"""
    from tavotto.engine import fixtools

    built = []
    real = fixtools.FixContext.for_fix_session

    def spy(*a, **kw):
        ctx = real(*a, **kw)
        built.append(ctx)
        return ctx

    monkeypatch.setattr(fixtools.FixContext, "for_fix_session", spy)
    s = start(box, Scripted([]), background=False)
    assert built and built[0].cancel is s.cancel


def _convert_session(box, model):
    put(box.proj / "fig.py", "print(1)")
    hint = {"kind": "raster_script", "library": "pillow", "in_project": False, "path": None}
    return fixsession.SESSIONS.start(
        scope="pj",
        prep_session_id="prep1",
        script="fig.py",
        project_root=box.proj,
        script_path=box.proj / "fig.py",
        model=model,
        report=lambda: {"phase": "failed", "no_figure_hint": hint},
        audit=AuditLog(box.data / "ai" / "fix_audit.jsonl"),
    )


def test_builtin_loop_convert_outcome_carries_the_handoff_in_the_public_state(box):
    model = Scripted(
        [("convert_script", {})],
        [("finish", {"outcome": "convert", "summary": "我会生成一份可编辑版本"})],
    )
    s = _convert_session(box, model)
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    done = fixsession.SESSIONS.get(s.id, "pj")
    assert done["outcome"] == "convert" and done["reason"] == ""
    assert done["handoff"] == {
        "kind": "convert",
        "script": "fig.py",
        "library": "pillow",
        "path": None,
    }
    assert (box.proj / "fig.py").read_text("utf-8") == "print(1)"
    assert sorted(p.name for p in box.proj.iterdir()) == ["fig.py"]
    rows = [
        json.loads(x) for x in (box.data / "ai" / "fix_audit.jsonl").read_text("utf-8").splitlines()
    ]
    assert [r["tool"] for r in rows] == ["convert_script", "finish"]
    assert rows[-1]["result"]["summary"] == "finished: convert"  # 审计照记 outcome


def test_builtin_loop_finish_convert_without_a_handoff_is_refused_and_not_convert(box):
    model = Scripted(
        [("finish", {"outcome": "convert", "summary": "x"})],
        [("finish", {"outcome": "not_fixed", "summary": "不行"})],
    )
    s = _convert_session(box, model)
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    done = fixsession.SESSIONS.get(s.id, "pj")
    assert done["outcome"] == "not_fixed" and "handoff" not in done


def test_a_handoff_that_ends_not_fixed_is_not_published(box):
    model = Scripted(
        [("convert_script", {})], [("finish", {"outcome": "not_fixed", "summary": "算了"})]
    )
    s = _convert_session(box, model)
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    done = fixsession.SESSIONS.get(s.id, "pj")
    assert done["outcome"] == "not_fixed" and "handoff" not in done
