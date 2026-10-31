"""`/api/contact*`（本机 HTTP 面，ADR 0145）：关闭时不存在、打开后端到端、状态对象里没有令牌 / 邮箱 / 正文。

引擎侧的契约对账在 `test_contact_send.py`；会话认证对每条路由的默认拒绝由 `test_browser_auth.py` 从 url_map 枚举
（这里再钉“这些路由确实在 url_map 里”，免得枚举悄悄漏掉它们）。
"""

from __future__ import annotations

import io
import json
import ssl
import time

import pytest

from support.contact_fakes import FakeContactServer
from support.tls_certs import make_pki
from tavotto import app as m
from tavotto.engine import contactinbox, contactsend, diagsend, tlstrust

PNG = b"\x89PNG\r\n\x1a\n" + b"0123456789abcdef" * 8
CANARY = "CANARY_API_BODY_Hq52"
MAIL = "api.canary@example.org"


@pytest.fixture(scope="module")
def pki_files(tmp_path_factory):
    return make_pki().write(tmp_path_factory.mktemp("contact-api-pki"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for name in (
        contactsend.ENV_HARD_OFF,
        contactsend.ENV_ENDPOINT,
        contactsend.ENV_REGION,
        diagsend.ENV_DEV_LOOPBACK,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(contactsend, "_sleep", lambda s: None)
    m.app.config["TESTING"] = True
    m.reset_projects()
    contactsend.reset_for_tests()
    yield m.app.test_client()
    contactsend.reset_for_tests()
    m.reset_projects()


@pytest.fixture
def on(client, pki_files, monkeypatch):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    with FakeContactServer(ctx) as srv:
        monkeypatch.setattr(
            tlstrust,
            "client_context",
            lambda: ssl.create_default_context(cafile=str(pki_files["ca"])),
        )
        monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
        monkeypatch.setenv(contactsend.ENV_ENDPOINT, srv.base)
        yield srv


def _send(client, meta=None, files=()):
    data = {
        "meta": json.dumps({"locale": "en-US", "body": CANARY, "reply_email": MAIL, **(meta or {})})
    }
    if files:
        data["file"] = [(io.BytesIO(b), n) for n, b in files]
    return client.post("/api/contact/send", data=data, content_type="multipart/form-data")


def _wait(client, states, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = client.get("/api/contact/status").get_json()
        if st["state"] in states:
            return st
        time.sleep(0.02)
    raise AssertionError(st)


def test_the_routes_exist_in_the_url_map_so_the_default_deny_sweep_sees_them():
    rules = {
        (r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in m.app.url_map.iter_rules()
    }
    expected = {
        ("/api/contact", ("GET",)),
        ("/api/contact/status", ("GET",)),
        ("/api/contact/send", ("POST",)),
        ("/api/contact/cancel", ("POST",)),
        ("/api/contact/dismiss", ("POST",)),
        ("/api/contact/messages", ("GET",)),
        ("/api/contact/refresh", ("POST",)),
        ("/api/contact/messages/<mid>/read", ("POST",)),
        ("/api/contact/messages/<mid>", ("DELETE",)),
        ("/api/contact/replies/<mid>/<rid>/doc", ("GET",)),
    }
    assert expected <= rules


def test_disabled_means_capability_false_and_everything_else_404(client, monkeypatch):
    monkeypatch.setenv(contactsend.ENV_HARD_OFF, "1")
    assert client.get("/api/contact").get_json() == {"enabled": False}
    for method, url in (
        ("get", "/api/contact/status"),
        ("get", "/api/contact/messages"),
        ("get", "/api/contact/replies/TVM-ABCD-EFGH-JKMN-PQRS/r_A/doc"),
        ("post", "/api/contact/refresh"),
        ("post", "/api/contact/send"),
    ):
        resp = getattr(client, method)(url)
        assert resp.status_code == 404, url
        assert resp.get_json()["code"] == "contact_disabled"


def test_capability_has_limits_and_no_endpoint(on, client):
    cap = client.get("/api/contact").get_json()
    assert cap["enabled"] is True and cap["recipient"] == "feedback@tavotto.com"
    assert (cap["max_attachments"], cap["max_total_bytes"]) == (5, 20 * 1024 * 1024)
    assert (cap["subject_max"], cap["body_max"]) == (120, 5000)
    assert cap["unread"] == 0
    assert "127.0.0.1" not in json.dumps(cap) and "tavotto.cn" not in json.dumps(cap)


def test_end_to_end_through_http_and_no_secret_in_any_response(on, client):
    resp = _send(client, files=[("shot.png", PNG)])
    assert resp.status_code == 202 and resp.get_json()["state"] == "sending"
    st = _wait(client, {"done", "failed"})
    assert st["state"] == "done"
    msg = on.only_message()
    listing = client.get("/api/contact/messages").get_json()
    assert [x["message_id"] for x in listing["messages"]] == [msg.mid]
    seen = json.dumps([resp.get_json(), st, listing, client.get("/api/contact").get_json()])
    for secret in (msg.reply_token, msg.complete_token, MAIL):
        assert secret not in seen
    # 正文只在「我的来信」列表里（本机存的那一份，第二期）；发送状态与能力说明里没有
    assert listing["messages"][0]["body"] == CANARY
    assert CANARY not in json.dumps([resp.get_json(), st, client.get("/api/contact").get_json()])
    assert resp.headers["Cache-Control"] == "no-store"

    on.reply(msg.mid, "thanks")
    out = client.post("/api/contact/refresh").get_json()
    assert out["polled"] == 1 and out["unread"] == 1
    assert client.get("/api/contact").get_json()["unread"] == 1
    assert client.post(f"/api/contact/messages/{msg.mid}/read").get_json() == {"unread": 0}
    assert client.delete(f"/api/contact/messages/{msg.mid}").get_json() == {"unread": 0}
    assert client.get("/api/contact/messages").get_json()["messages"] == []
    assert client.post("/api/contact/dismiss").get_json()["state"] == "idle"


def test_validation_errors_are_stable_codes_with_no_network(on, client):
    resp = _send(client, {"body": ""})
    assert resp.status_code == 422 and resp.get_json()["code"] == "contact_body_required"
    resp = _send(client, {"reply_email": "nope"})
    assert resp.get_json()["code"] == "contact_email_invalid"
    resp = _send(client, files=[("evil.exe", PNG)])
    assert resp.get_json()["code"] == "contact_attachment_type"
    resp = client.post(
        "/api/contact/send", data={"meta": "{not json"}, content_type="multipart/form-data"
    )
    assert resp.status_code == 400 and resp.get_json()["code"] == "contact_meta_invalid"
    assert on.calls == []


def test_oversized_upload_is_refused_without_reading_it_into_a_session(on, client):
    big = PNG + b"\0" * (contactsend.MAX_TOTAL_BYTES + 10)
    resp = _send(client, files=[("big.png", big)])
    assert resp.status_code in (413, 422)
    assert on.calls == []


def test_failure_state_exposes_a_closed_code_only(on, client):
    on.fail("init", 422, "invalid_email")
    _send(client)
    st = _wait(client, {"failed"})
    assert st["code"] == "invalid_email" and st["retryable"] is False
    assert set(st) <= {
        "state",
        "stage",
        "sent",
        "total",
        "gen",
        "code",
        "retryable",
        "retry_after",
        "message_id",
    }


def test_cancel_endpoint_when_idle_is_harmless(on, client):
    assert client.post("/api/contact/cancel").get_json()["state"] == "idle"


def test_inbox_endpoints_do_not_touch_the_network(on, client):
    contactinbox.add("TVM-AAAA-BBBB-CCCC-DDDD", "c" * 64, "cn", delivered=True)
    assert client.get("/api/contact/messages").get_json()["messages"][0]["unread"] == 0
    assert on.calls == []


def test_large_attachment_never_touches_a_disk_spool_and_arrives_intact(on, client, monkeypatch):
    """Werkzeug 默认 500 KiB 以上会把附件滚进磁盘临时文件；本端点必须全程在内存里解析（ADR 0145）。"""
    import tempfile

    def boom(*_a, **_k):
        raise AssertionError("附件不许落到磁盘临时文件")

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", boom)
    monkeypatch.setattr(tempfile, "TemporaryFile", boom)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", boom)
    big = PNG + b"\x00" * (700 * 1024)
    resp = _send(client, files=[("big.png", big)])
    assert resp.status_code == 202
    assert _wait(client, {"done", "failed"})["state"] == "done"
    assert list(on.only_message().uploads.values()) == [big]


def test_request_without_content_length_is_refused(on, client):
    """合法完整的 multipart（有 Content-Length 时会被接受）去掉 CONTENT_LENGTH、改成分块传输：必须在解析前 413。"""
    from werkzeug.test import EnvironBuilder

    data = {"meta": json.dumps({"locale": "en-US", "body": CANARY, "reply_email": MAIL})}
    data["file"] = (io.BytesIO(PNG), "a.png")
    builder = EnvironBuilder("/api/contact/send", method="POST", data=data)
    environ = builder.get_environ()
    assert environ["CONTENT_LENGTH"]  # 对照：带头时是个完整请求
    del environ["CONTENT_LENGTH"]
    environ["HTTP_TRANSFER_ENCODING"] = "chunked"
    resp = client.open(environ)
    assert resp.status_code == 413
    assert resp.get_json()["code"] == "contact_attachments_too_large"
    assert on.calls == []


def test_inbox_write_failure_still_delivers_but_says_inbox_not_saved(on, client, monkeypatch):
    from tavotto.engine import atomicio

    def deny(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(atomicio, "write_bytes", deny)
    assert _send(client).status_code == 202
    st = _wait(client, {"done", "failed"})
    assert st["state"] == "done" and st["inbox_saved"] is False
    assert on.only_message() is not None  # 信确实送达了
    assert client.get("/api/contact/messages").get_json()["messages"] == []


def test_inbox_saved_is_true_on_the_normal_path(on, client):
    assert _send(client).status_code == 202
    assert _wait(client, {"done", "failed"})["inbox_saved"] is True


def test_reply_doc_endpoint_serves_the_sandbox_document_with_inlined_images(on, client):
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "contact" / "reply_sanitized.html").read_text(
        encoding="utf-8"
    )
    assert _send(client).status_code == 202
    _wait(client, {"done"})
    mid = on.only_message().mid
    rid = on.reply(mid, "plain", html=html, assets=[("image/png", PNG)] * 4)
    plain = on.reply(mid, "只有文字")
    client.post("/api/contact/refresh")
    listing = client.get("/api/contact/messages").get_json()["messages"][0]["replies"]
    assert {r["id"]: r["has_html"] for r in listing} == {rid: True, plain: False}

    resp = client.get(f"/api/contact/replies/{mid}/{rid}/doc")
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    doc = resp.get_json()["doc"]
    assert "Content-Security-Policy" in doc and "img-src data:" in doc
    assert "asset:" not in doc and doc.count("data:image/png;base64,") == 4
    # 没有 HTML 的回信 / 不存在的回信：404（纯文字直接用列表里的 body）
    assert client.get(f"/api/contact/replies/{mid}/{plain}/doc").status_code == 404
    assert client.get(f"/api/contact/replies/{mid}/r_NOPE/doc").status_code == 404
    assert client.get(f"/api/contact/replies/{mid}/..%2Fx/doc").status_code == 404


def test_reply_doc_endpoint_is_session_gated():
    """会话认证是全局 before_request（ADR 0008）：这条新路由必须在 url_map 里，由 test_browser_auth 的枚举兜住。"""
    rules = {r.rule for r in m.app.url_map.iter_rules()}
    assert "/api/contact/replies/<mid>/<rid>/doc" in rules
