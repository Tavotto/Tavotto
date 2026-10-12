"""`/api/diagnostics/send*`（本机 HTTP 面，ADR 0118）：功能关闭时不存在、确认前零远程请求、打开后端到端。

引擎侧的状态机 / 契约对账在 `test_diag_send.py`；这里只验 Flask 这一层的形状与门。
"""

from __future__ import annotations

import hashlib
import ssl
import threading
import time

import pytest

from support.diag_fakes import FakeDiagServer, Forced
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


def _open_two(client, tmp_path):
    ids = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        body = client.post("/api/projects/open", json={"path": str(root), "default": name == "a"})
        ids.append(body.get_json()["id"])
    return ids


def _prep(client, pj):
    return client.post(
        "/api/diagnostics/send/prepare",
        json={"frontend_state": None, "interaction_trace": []},
        headers={"X-Tavotto-Project": pj},
    ).get_json()


class TestPerProjectSessions:
    """#927：会话注册表按项目分键——两个窗口、两个项目互不使对方的对话框失效。"""

    def test_preparing_in_b_does_not_discard_a(self, client, on, tmp_path):
        a, b = _open_two(client, tmp_path)
        pa, pb = _prep(client, a), _prep(client, b)
        assert pa["id"] != pb["id"]
        ra = client.get(f"/api/diagnostics/send/{pa['id']}", headers={"X-Tavotto-Project": a})
        assert ra.status_code == 200 and ra.get_json()["state"] == "prepared"
        rb = client.get(f"/api/diagnostics/send/{pb['id']}", headers={"X-Tavotto-Project": b})
        assert rb.status_code == 200
        # 各自再发，互不影响
        for pj, prep in ((a, pa), (b, pb)):
            r = client.post(
                f"/api/diagnostics/send/{prep['id']}/send",
                json={"confirm": True},
                headers={"X-Tavotto-Project": pj},
            )
            assert r.status_code == 202, r.get_json()
        for pj, prep in ((a, pa), (b, pb)):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                st = client.get(
                    f"/api/diagnostics/send/{prep['id']}", headers={"X-Tavotto-Project": pj}
                ).get_json()
                if st["state"] == "done":
                    break
                time.sleep(0.02)
            assert st["state"] == "done"
        assert len(on.reports) == 2

    def test_a_sending_in_a_does_not_block_preparing_in_b(self, client, on, tmp_path):
        a, b = _open_two(client, tmp_path)
        gate = threading.Event()
        on.force("init", Forced("hold", event=gate))
        pa = _prep(client, a)
        client.post(
            f"/api/diagnostics/send/{pa['id']}/send",
            json={"confirm": True},
            headers={"X-Tavotto-Project": a},
        )
        r = client.post(
            "/api/diagnostics/send/prepare",
            json={"frontend_state": None, "interaction_trace": []},
            headers={"X-Tavotto-Project": b},
        )
        try:
            assert r.status_code == 200, r.get_json()
        finally:
            gate.set()

    def test_a_session_id_is_not_reachable_from_another_project(self, client, on, tmp_path):
        a, b = _open_two(client, tmp_path)
        pa = _prep(client, a)
        for method, suffix in (("get", ""), ("get", "/bundle"), ("post", "/cancel")):
            r = getattr(client, method)(
                f"/api/diagnostics/send/{pa['id']}{suffix}", headers={"X-Tavotto-Project": b}
            )
            assert r.status_code == 404, (suffix, r.status_code)
        client.post(f"/api/diagnostics/send/{pa['id']}/discard", headers={"X-Tavotto-Project": b})
        r = client.get(f"/api/diagnostics/send/{pa['id']}", headers={"X-Tavotto-Project": a})
        assert r.status_code == 200, "B 的 discard 不能丢掉 A 的包"

    def test_explicit_pj_query_wins_over_a_conflicting_header(self, client, on, tmp_path):
        """Codex #955：前端在切项目途中清理 A 的会话，头已是 B、`?pj=` 显式写 A——以显式 pj 为准，A 的会话必须被丢弃。"""
        a, b = _open_two(client, tmp_path)
        pa = _prep(client, a)
        client.post(
            f"/api/diagnostics/send/{pa['id']}/discard?pj={a}", headers={"X-Tavotto-Project": b}
        )
        r = client.get(f"/api/diagnostics/send/{pa['id']}", headers={"X-Tavotto-Project": a})
        assert r.status_code == 404, "A 的会话应已被丢弃"
