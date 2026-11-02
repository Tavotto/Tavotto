"""发送问题反馈的「回信通道」（ADR 0118 / 0145 修订 2026-11-02）：`diagsend._init` 带 `want_reply`，
服务端回关联来信 `reply`，送达后登记进「联系作者」的收件记录（`contactinbox`，kind=report）。

判据的主语：
* 「带没带 want_reply」= 模拟服务端那一侧收到的 init 请求体；
* 「登记了没有」= 数据目录里 `contact_messages.json` 的内容（经 `contactinbox`），不是会话自己说登记了；
* 「没泄漏」= 状态对象 `public()` 与日志（caplog）里搜令牌。
对端是 `tests/support/diag_fakes.py` 的本地模拟：默认是未部署的旧服务端（extra=forbid）。
"""

from __future__ import annotations

import io
import json
import logging
import re
import ssl
import time
import zipfile
from pathlib import Path

import pytest

from support.diag_fakes import FakeDiagServer, Forced
from support.tls_certs import make_pki
from tavotto.engine import contactinbox, contactsend, diagsend, taskdiag, tlstrust

PJ = "pj-reply"
NOTE = "CANARY_NOTE_附言"


@pytest.fixture(scope="module")
def pki_files(tmp_path_factory) -> dict[str, Path]:
    return make_pki().write(tmp_path_factory.mktemp("diag-reply-pki"))


@pytest.fixture
def srv(pki_files):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    with FakeDiagServer(ctx) as s:
        yield s


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, srv, pki_files, tmp_path):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    for name in (diagsend.ENV_ENABLE, diagsend.ENV_HARD_OFF, contactsend.ENV_HARD_OFF):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        tlstrust, "client_context", lambda: ssl.create_default_context(cafile=str(pki_files["ca"]))
    )
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.setenv(diagsend.ENV_ENDPOINT, srv.base)
    monkeypatch.setenv(contactsend.ENV_ENDPOINT, srv.base)
    monkeypatch.setattr(diagsend, "_sleep", lambda s: None)
    diagsend.reset_for_tests()
    taskdiag.reset_for_tests()
    contactinbox.reset_for_tests()
    yield
    diagsend.reset_for_tests()
    taskdiag.reset_for_tests()
    contactinbox.reset_for_tests()


def _bundle() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("report.json", json.dumps({"tavotto": {"version": "0.0.0"}}))
        z.writestr("manifest.json", json.dumps({"schema_version": 6}))
        z.writestr("README.txt", "x")
    return buf.getvalue()


def _wait(pid: str, states: set[str]) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        st = diagsend.status(pid, scope=PJ)
        if st["state"] in states:
            return st
        time.sleep(0.01)
    raise AssertionError(diagsend.status(pid, scope=PJ))


def _send(pid: str | None = None, *, note: str | None = NOTE) -> str:
    if pid is None:
        pid = diagsend.prepare(_bundle(), scope=PJ)["id"]
    diagsend.start(pid, confirmed=True, category="render_failure", note=note, scope=PJ)
    return pid


def _inits(srv) -> list[dict]:
    return [c["json"] for c in srv.calls if c["path"] == "/v1/reports/init"]


def _records() -> list[dict]:
    return contactinbox._load_locked()["messages"]


def test_old_server_refuses_want_reply_then_report_is_sent_without_it(srv):
    srv.accepts_want_reply = False
    st = _wait(_send(), {"done", "failed"})
    assert st["state"] == "done" and "reply_inbox" not in st
    inits = _inits(srv)
    assert len(inits) == 2
    assert inits[0]["want_reply"] is True and "want_reply" not in inits[1]
    assert inits[0]["client_request_id"] == inits[1]["client_request_id"]
    assert _records() == []


def test_after_a_refusal_the_session_stops_asking(srv):
    srv.accepts_want_reply = False
    pid = _send()
    _wait(pid, {"done"})
    # 同会话再来一次（失败后重试的形状）：不再带
    srv.accepts_want_reply = True
    sess = diagsend._get(pid, PJ)
    assert sess.reply_refused


def test_new_server_opens_the_linked_letter_and_it_is_registered(srv):
    srv.accepts_want_reply = True
    st = _wait(_send(), {"done", "failed"})
    assert st["state"] == "done" and st["reply_inbox"] is True
    inits = _inits(srv)
    assert len(inits) == 1 and inits[0]["want_reply"] is True
    rep = srv.reports[st["report_id"]]
    (rec,) = _records()
    assert rec["kind"] == "report" and rec["report_id"] == st["report_id"]
    assert rec["message_id"] == rep.reply["message_id"]
    assert rec["reply_token"] == rep.reply["reply_token"]
    assert rec["body"] == NOTE and rec["subject"] == ""
    assert rec["route"] == "global" and rec["delivered"] is True
    (pub,) = contactinbox.public_messages()
    assert pub["kind"] == "report" and pub["report_id"] == st["report_id"]
    assert "reply_token" not in json.dumps(pub)


def test_no_note_registers_an_empty_body(srv):
    srv.accepts_want_reply = True
    st = _wait(_send(note=None), {"done"})
    assert st["reply_inbox"] is True and _records()[0]["body"] == ""


@pytest.mark.parametrize("hard_off", ["TAVOTTO_NO_CONTACT"])
def test_contact_switch_off_neither_asks_nor_registers(srv, monkeypatch, hard_off):
    srv.accepts_want_reply = True
    monkeypatch.setenv(hard_off, "1")
    st = _wait(_send(), {"done"})
    assert "want_reply" not in _inits(srv)[0]
    assert "reply_inbox" not in st and _records() == []


def test_switch_turned_off_after_init_does_not_register(srv, monkeypatch):
    srv.accepts_want_reply = True
    sess_id = diagsend.prepare(_bundle(), scope=PJ)["id"]
    real = diagsend._complete

    def complete_then_switch_off(sess, target):
        real(sess, target)
        monkeypatch.setenv("TAVOTTO_NO_CONTACT", "1")

    monkeypatch.setattr(diagsend, "_complete", complete_then_switch_off)
    st = _wait(_send(sess_id), {"done"})
    assert "reply_inbox" not in st and _records() == []


@pytest.mark.parametrize(
    "bad",
    [
        "x",
        [],
        {},
        {"message_id": "TVM-NOPE", "reply_token": "a" * 64},
        {"message_id": "TVM-ABCD-EFGH-JKMN-PQRS", "reply_token": "A" * 64},
        {"message_id": "TVM-ABCD-EFGH-JKMN-PQRS", "reply_token": "a" * 63},
        {"message_id": "TVD-ABCD-EFGH-JKMN-PQRS", "reply_token": "a" * 64},
        {"message_id": 7, "reply_token": "a" * 64},
    ],
)
def test_malformed_reply_is_dropped_and_the_report_still_succeeds(srv, bad):
    srv.accepts_want_reply = True
    srv.reply_override = bad
    st = _wait(_send(), {"done", "failed"})
    assert st["state"] == "done" and "reply_inbox" not in st
    assert _records() == []


def test_replay_of_a_complete_report_registers_the_reply(srv):
    """complete 响应全丢、用户再点发送：init 重放回 state=complete + reply，仍登记（同附言复用 id）。"""
    srv.accepts_want_reply = True
    srv.force("complete", Forced("drop_after"), times=diagsend.COMPLETE_ATTEMPTS)
    pid = _send()
    _wait(pid, {"failed"})
    assert _records() == [], "失败态不登记"
    sess = diagsend._get(pid, PJ)
    sess.token, sess.spec = (
        "",
        None,
    )  # 令牌不在了（如重启后重开会话）：重新 init，服务端按同一 id 重放 complete
    sess.uploaded = True
    diagsend.start(pid, confirmed=True, category="render_failure", note=NOTE, scope=PJ)
    st = _wait(pid, {"done"})
    assert st["reply_inbox"] is True
    (rec,) = _records()
    assert rec["report_id"] == st["report_id"]
    assert rec["message_id"] == srv.reports[st["report_id"]].reply["message_id"]
    assert len(_inits(srv)) == 2 and len(srv.reports) == 1


def test_unknown_reconciled_as_delivered_registers_the_old_reply(srv):
    """改附言重试时对账出上一次其实已送达：登记的是首份报告的关联来信，不是新的。"""
    srv.accepts_want_reply = True
    srv.force("complete", Forced("drop_after"), times=diagsend.COMPLETE_ATTEMPTS)
    pid = _send()
    _wait(pid, {"failed"})
    first = diagsend._get(pid, PJ).report_id
    diagsend.start(pid, confirmed=True, category="crash", note=NOTE + "2", scope=PJ)
    st = _wait(pid, {"done", "unknown"})
    assert st["state"] == "done" and st["report_id"] == first and st["reply_inbox"] is True
    assert len(srv.reports) == 1 and len(_inits(srv)) == 1
    (rec,) = _records()
    assert rec["report_id"] == first
    assert rec["message_id"] == srv.reports[first].reply["message_id"]


def test_token_never_shows_in_public_state_or_logs(srv, caplog):
    srv.accepts_want_reply = True
    caplog.set_level(logging.DEBUG)
    pid = _send()
    st = _wait(pid, {"done"})
    token = srv.reports[st["report_id"]].reply["reply_token"]
    mid = srv.reports[st["report_id"]].reply["message_id"]
    assert token not in json.dumps(diagsend.status(pid, scope=PJ))
    assert token not in repr(diagsend._get(pid, PJ))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in text and mid not in text and NOTE not in text


def test_changing_the_switch_between_attempts_uses_a_new_request_id(srv, monkeypatch):
    srv.accepts_want_reply = True
    srv.fail("init", 503, "unavailable")
    pid = _send()
    _wait(pid, {"failed"})
    monkeypatch.setenv("TAVOTTO_NO_CONTACT", "1")
    diagsend.start(pid, confirmed=True, category="render_failure", note=NOTE, scope=PJ)
    _wait(pid, {"done"})
    inits = _inits(srv)
    assert re.match(r".+", inits[-1]["client_request_id"])
    assert inits[0]["client_request_id"] != inits[-1]["client_request_id"]
    assert "want_reply" not in inits[-1]
