"""`/api/diagnostics/send*`（本机 HTTP 面，ADR 0118）：功能关闭时不存在、确认前零远程请求、打开后端到端。

引擎侧的状态机 / 契约对账在 `test_diag_send.py`；这里只验 Flask 这一层的形状与门。
"""

from __future__ import annotations

import hashlib
import ssl
import time

import pytest

from support.diag_fakes import FakeDiagServer
from support.tls_certs import make_pki
from tavotto import app as m
from tavotto.engine import diagsend, tlstrust

CANARY = "CANARY_STATE_Qp77"


@pytest.fixture(scope="module")
def pki_files(tmp_path_factory):
    return make_pki().write(tmp_path_factory.mktemp("diag-api-pki"))


@pytest.fixture
def client(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for name in (
        diagsend.ENV_ENABLE,
        diagsend.ENV_HARD_OFF,
        diagsend.ENV_ENDPOINT,
        diagsend.ENV_DEV_LOOPBACK,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(diagsend, "_sleep", lambda s: None)
    m.app.config["TESTING"] = True
    m.reset_projects()
    diagsend.reset_for_tests()
    yield m.app.test_client()
    diagsend.reset_for_tests()
    m.reset_projects()


@pytest.fixture
def on(client, pki_files, monkeypatch):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    with FakeDiagServer(ctx) as srv:
        monkeypatch.setattr(
            tlstrust,
            "client_context",
            lambda: ssl.create_default_context(cafile=str(pki_files["ca"])),
        )
        monkeypatch.setenv(diagsend.ENV_ENABLE, "1")
        monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
        monkeypatch.setenv(diagsend.ENV_ENDPOINT, srv.base)
        yield srv


def _poll(client, pid, states, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = client.get(f"/api/diagnostics/send/{pid}").get_json()
        if st["state"] in states:
            return st
        time.sleep(0.02)
    raise AssertionError(st)


def _prepare(client, state_text=CANARY):
    body = {"frontend_state": None, "interaction_trace": []}
    return client.post("/api/diagnostics/send/prepare", json=body)


class TestDisabledByDefault:
    def test_capability_says_disabled_and_everything_else_is_404(self, client):
        assert client.get("/api/diagnostics/send").get_json() == {"enabled": False}
        r = _prepare(client)
        assert r.status_code == 404 and r.get_json()["code"] == "diag_send_disabled"
        for method, path in [
            ("get", "/api/diagnostics/send/x"),
            ("get", "/api/diagnostics/send/x/bundle"),
            ("post", "/api/diagnostics/send/x/send"),
            ("post", "/api/diagnostics/send/x/cancel"),
        ]:
            assert getattr(client, method)(path, json={"confirm": True}).status_code == 404

    def test_existing_export_endpoint_is_untouched(self, client):
        r = client.get("/api/diagnostics/bundle")
        assert r.status_code == 200 and r.mimetype == "application/zip"

    def test_responses_are_not_cacheable(self, client):
        assert client.get("/api/diagnostics/send").headers["Cache-Control"] == "no-store"


class TestEnabledFlow:
    def test_prepare_is_offline_and_bundle_is_the_exact_payload_that_will_be_sent(self, client, on):
        assert client.get("/api/diagnostics/send").get_json()["enabled"] is True
        prep = _prepare(client).get_json()
        assert prep["id"] and prep["too_large"] is False and prep["schema"] >= 1
        kinds = {e["name"]: e["kind"] for e in prep["entries"]}
        assert kinds["report.json"] == "environment" and kinds["manifest.json"] == "manifest"
        saved = client.get(f"/api/diagnostics/send/{prep['id']}/bundle")
        assert saved.mimetype == "application/zip"
        assert hashlib.sha256(saved.data).hexdigest() == prep["sha256"]
        assert on.calls == [] and on.connections == 0, "备包、查看都不联网"

    def test_send_requires_an_explicit_confirm(self, client, on):
        pid = _prepare(client).get_json()["id"]
        for body in ({}, {"confirm": False}, {"confirm": "true"}, {"category": "other"}):
            r = client.post(f"/api/diagnostics/send/{pid}/send", json=body)
            assert r.status_code == 400 and r.get_json()["code"] == "diag_not_confirmed"
        r = client.post(
            f"/api/diagnostics/send/{pid}/send", data="not json", content_type="text/plain"
        )
        assert r.status_code == 400
        assert on.connections == 0

    def test_confirmed_send_reaches_done_with_a_copyable_report_id(self, client, on):
        prep = _prepare(client).get_json()
        r = client.post(
            f"/api/diagnostics/send/{prep['id']}/send",
            json={"confirm": True, "category": "export_failure", "note": "export looks wrong"},
        )
        assert r.status_code == 202 and r.get_json()["state"] == "sending"
        st = _poll(client, prep["id"], {"done", "failed"})
        assert st["state"] == "done" and st["report_id"].startswith("TVD-")
        rep = next(iter(on.reports.values()))
        assert hashlib.sha256(rep.uploaded).hexdigest() == prep["sha256"], (
            "上传的就是用户能存下来的那份"
        )
        assert on.reports and rep.note == "export looks wrong"

    def test_discard_after_close_means_nothing_is_ever_sent(self, client, on):
        pid = _prepare(client).get_json()["id"]
        assert client.post(f"/api/diagnostics/send/{pid}/discard").get_json() == {"ok": True}
        assert (
            client.post(f"/api/diagnostics/send/{pid}/send", json={"confirm": True}).status_code
            == 404
        )
        assert client.post(f"/api/diagnostics/send/{pid}/discard").status_code == 200  # 幂等
        assert on.connections == 0

    def test_cancel_endpoint_on_a_prepared_bundle(self, client, on):
        pid = _prepare(client).get_json()["id"]
        r = client.post(f"/api/diagnostics/send/{pid}/cancel").get_json()
        assert r["state"] == "cancelled" and on.connections == 0

    def test_status_never_exposes_token_url_or_fields(self, client, on):
        prep = _prepare(client).get_json()
        client.post(f"/api/diagnostics/send/{prep['id']}/send", json={"confirm": True, "note": "n"})
        st = _poll(client, prep["id"], {"done", "failed"})
        text = str(st)
        rep = next(iter(on.reports.values()))
        for secret in (rep.token, on.base, rep.key, "policy", "signature"):
            assert secret not in text

    def test_unknown_id_is_404_with_a_stable_code(self, client, on):
        r = client.get("/api/diagnostics/send/nope")
        assert r.status_code == 404 and r.get_json()["code"] == "diag_send_not_found"

    def test_oversized_prepare_returns_too_large_without_keeping_anything(
        self, client, on, monkeypatch
    ):
        monkeypatch.setattr(diagsend, "MAX_BYTES", 10)
        body = _prepare(client).get_json()
        assert body["too_large"] is True and body["id"] is None
        assert on.connections == 0


class TestActionOrdering:
    def test_a_send_that_arrives_after_its_cancel_is_refused_and_nothing_is_uploaded(
        self, client, on
    ):
        prep = _prepare(client).get_json()
        assert prep["gen"] == 0
        assert (
            client.post(f"/api/diagnostics/send/{prep['id']}/cancel").get_json()["state"]
            == "cancelled"
        )
        r = client.post(
            f"/api/diagnostics/send/{prep['id']}/send", json={"confirm": True, "gen": prep["gen"]}
        )
        assert r.status_code == 409 and r.get_json()["code"] in (
            "diag_session_cancelled",
            "diag_stale_action",
        )
        r2 = client.post(f"/api/diagnostics/send/{prep['id']}/send", json={"confirm": True})
        assert r2.status_code == 409 and r2.get_json()["code"] == "diag_session_cancelled"
        assert on.connections == 0

    def test_status_carries_the_generation_and_send_accepts_it(self, client, on):
        prep = _prepare(client).get_json()
        gen = client.get(f"/api/diagnostics/send/{prep['id']}").get_json()["gen"]
        r = client.post(
            f"/api/diagnostics/send/{prep['id']}/send", json={"confirm": True, "gen": gen}
        )
        assert r.status_code == 202
        assert _poll(client, prep["id"], {"done", "failed"})["state"] == "done"
