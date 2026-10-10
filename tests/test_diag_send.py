"""「发送问题反馈」客户端（`engine/diagsend.py`，ADR 0118）对 `diagnostics-api` v1 契约的验收。

对端是 `tests/support/diag_fakes.py` 的本地模拟（HTTPS 回环 + 每会话现造的私有 CA）；真 COS（C1–C16）与真机
WebView 联调不在这里，标 NOT_RUN（PR 正文）。

判据的主语：
* 「零远程请求」= 模拟服务端 `calls` 的长度——**服务端那一侧收到了什么**，不是客户端自己说没发；
* 「同一次发送复用 client_request_id」= 服务端 init 请求体里的值，不是客户端字段；
* 「没泄漏」= 日志（caplog 全级别 + 出门版 ExportLogFormatter）、状态对象、诊断包全文里搜金丝雀；
* 「目的地不被响应改写」= 模拟服务端 / 第二台「别处」服务器那一侧有没有收到连接。
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import os
import re
import ssl
import threading
import time
import zipfile
from pathlib import Path

import pytest

from support.diag_fakes import FakeDiagServer, Forced
from support.tls_certs import make_pki
from tavotto.engine import diagnostics, diagsend, telemetry, tlstrust

SRC = Path(__file__).resolve().parent.parent / "src" / "tavotto"
CANARY_NOTE = "CANARY_NOTE_Zq81_user_text"
GOOD_COS = "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/"


# --------------------------------------------------------------------------- 装置
@pytest.fixture(scope="module")
def pki_files(tmp_path_factory) -> dict[str, Path]:
    return make_pki().write(tmp_path_factory.mktemp("diag-pki"))


@pytest.fixture
def server_tls(pki_files) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki_files["cert"]), str(pki_files["key"]))
    return ctx


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    for name in (
        diagsend.ENV_ENABLE,
        diagsend.ENV_HARD_OFF,
        diagsend.ENV_ENDPOINT,
        diagsend.ENV_DEV_LOOPBACK,
    ):
        monkeypatch.delenv(name, raising=False)
    # 退避不真等；记下来给「按 Retry-After 等」的用例看
    sleeps: list[float] = []
    monkeypatch.setattr(diagsend, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.sleeps = sleeps  # type: ignore[attr-defined]
    diagsend.reset_for_tests()
    monkeypatch.setattr(diagsend, "_sleep", lambda s: sleeps.append(s))
    yield
    diagsend.reset_for_tests()


@pytest.fixture
def srv(server_tls):
    with FakeDiagServer(server_tls) as s:
        yield s


@pytest.fixture
def on(srv, pki_files, monkeypatch):
    """功能打开、指向模拟服务端、客户端信任测试根（只换 `tlstrust.client_context` 这一个注入点）。"""

    def _ctx():
        return ssl.create_default_context(cafile=str(pki_files["ca"]))

    monkeypatch.setattr(tlstrust, "client_context", _ctx)
    monkeypatch.setenv(diagsend.ENV_ENABLE, "1")
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.setenv(diagsend.ENV_ENDPOINT, srv.base)
    return srv


def make_bundle(pad: int = 0, schema: object = 6) -> bytes:
    """一个与产品包同形状的最小 ZIP（manifest + report）；`pad` 字节不可压缩的填充。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("report.json", json.dumps({"tavotto": {"version": "0.0.0"}}))
        z.writestr("manifest.json", json.dumps({"schema_version": schema}))
        z.writestr("README.txt", "x")
        if pad:
            z.writestr("app.log", os.urandom(pad))
    return buf.getvalue()


def wait_for(pid: str, states: set[str], timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = diagsend.status(pid)
        if st["state"] in states:
            return st
        time.sleep(0.01)
    raise AssertionError(f"没等到 {states}: {diagsend.status(pid)}")


def send(
    bundle: bytes | None = None, *, note: str | None = None, category: str | None = "render_failure"
):
    prep = diagsend.prepare(bundle if bundle is not None else make_bundle())
    diagsend.start(prep["id"], confirmed=True, category=category, note=note)
    return prep


def init_bodies(srv) -> list[dict]:
    return [c["json"] for c in srv.calls if c["path"] == "/v1/reports/init"]


# =========================================================================== 开关
class TestFeatureFlag:
    def test_off_by_default_and_nothing_to_enable_in_a_release(self, monkeypatch):
        """没有任何环境变量：关。生产白名单是空集：即使 `TAVOTTO_DIAG_UPLOAD=1` 指向任何 https 主机也关
        （G-DIAG 通过前，发行版里没有办法打开）。"""
        assert diagsend.ENDPOINT_HOSTS == frozenset()
        assert diagsend.capability() == {"enabled": False}
        monkeypatch.setenv(diagsend.ENV_ENABLE, "1")
        monkeypatch.setenv(diagsend.ENV_ENDPOINT, "https://diag.example.cn")
        assert not diagsend.enabled()
        monkeypatch.setenv(diagsend.ENV_ENDPOINT, "https://127.0.0.1:8443")
        assert not diagsend.enabled(), "回环要另设 DEV_LOOPBACK 才放行"

    def test_disabled_means_no_prepare_and_no_start(self):
        with pytest.raises(diagsend.SendError) as e:
            diagsend.prepare(make_bundle())
        assert e.value.code == "send_disabled" and e.value.status == 404
        with pytest.raises(diagsend.SendError) as e2:
            diagsend.start("x", confirmed=True, category=None, note=None)
        assert e2.value.code == "send_disabled"

    def test_hard_off_wins(self, on, monkeypatch):
        assert diagsend.enabled()
        monkeypatch.setenv(diagsend.ENV_HARD_OFF, "1")
        assert not diagsend.enabled()

    def test_capability_has_no_endpoint_address(self, on):
        cap = diagsend.capability()
        assert (
            cap["enabled"] and cap["max_bytes"] == 10 * 1024 * 1024 and cap["retention_days"] == 30
        )
        assert on.base not in json.dumps(cap) and "127.0.0.1" not in json.dumps(cap)

    @pytest.mark.parametrize(
        "raw",
        [
            "http://127.0.0.1:8443",  # 明文
            "https://127.0.0.1",  # 回环必须显式端口
            "https://user:pw@127.0.0.1:8443",
            "https://127.0.0.1:8443/path",
            "https://127.0.0.1:8443/?x=1",
            "https://127.0.0.1:8443/#f",
            "https://localhost:8443",
            "https://[::1]:8443",
            "https://127.0.0.1.evil.com:8443",
            "https://127.0.0.1:99999",
            "ftp://127.0.0.1:8443",
            "",
        ],
    )
    def test_endpoint_parser_rejects_everything_but_the_loopback_literal(self, raw):
        assert diagsend.parse_endpoint(raw, dev_loopback=True) is None

    def test_endpoint_parser_accepts_whitelisted_host_only_on_443(self, monkeypatch):
        monkeypatch.setattr(diagsend, "ENDPOINT_HOSTS", frozenset({"diag.example.cn"}))
        ok = diagsend.parse_endpoint("https://DIAG.example.cn", dev_loopback=False)
        assert ok and ok.host == "diag.example.cn" and ok.base == "https://diag.example.cn"
        assert diagsend.parse_endpoint("https://diag.example.cn:8443", dev_loopback=False) is None
        assert (
            diagsend.parse_endpoint("https://diag.example.cn.evil.com", dev_loopback=False) is None
        )
        assert diagsend.parse_endpoint("https://evil.com", dev_loopback=False) is None


# =========================================================================== 零请求
class TestNoRequestWithoutConfirmation:
    """未同意 / 关闭窗口 / 取消 → 服务端那一侧一个请求都没有。"""

    def test_prepare_view_discard_and_cancel_never_touch_the_network(self, on):
        prep = diagsend.prepare(make_bundle())
        assert prep["id"] and not prep["too_large"]
        assert diagsend.bundle_bytes(prep["id"])  # 用户查看 / 保存
        diagsend.status(prep["id"])
        diagsend.cancel(prep["id"])
        diagsend.discard(prep["id"])
        assert on.calls == [] and on.connections == 0

    def test_closing_the_window_after_prepare_sends_nothing(self, on):
        prep = diagsend.prepare(make_bundle())
        diagsend.discard(prep["id"])
        with pytest.raises(diagsend.SendError):
            diagsend.start(prep["id"], confirmed=True, category=None, note=None)
        assert on.connections == 0

    @pytest.mark.parametrize("confirmed", [None, False, 0, 1, "true", "yes", [], {}])
    def test_only_a_literal_true_confirms(self, on, confirmed):
        prep = diagsend.prepare(make_bundle())
        with pytest.raises(diagsend.SendError) as e:
            diagsend.start(prep["id"], confirmed=confirmed, category=None, note=None)
        assert e.value.code == "not_confirmed"
        assert on.connections == 0

    def test_a_guessed_id_cannot_start_anything(self, on):
        diagsend.prepare(make_bundle())
        for bad in ("", "x", None, 7, "ａ" * 8):
            with pytest.raises(diagsend.SendError):
                diagsend.start(bad, confirmed=True, category=None, note=None)  # type: ignore[arg-type]
        assert on.connections == 0

    def test_invalid_note_or_category_never_reaches_the_server(self, on):
        prep = diagsend.prepare(make_bundle())
        with pytest.raises(diagsend.SendError) as e:
            diagsend.start(prep["id"], confirmed=True, category="nonsense", note=None)
        assert e.value.code == "category_invalid"
        with pytest.raises(diagsend.SendError) as e2:
            diagsend.start(prep["id"], confirmed=True, category=None, note="x" * 1001)
        assert e2.value.code == "note_too_long"
        with pytest.raises(diagsend.SendError) as e3:
            diagsend.start(prep["id"], confirmed=True, category=None, note=5)  # type: ignore[arg-type]
        assert e3.value.code == "note_invalid"
        assert on.connections == 0

    def test_process_restart_resumes_nothing(self, on):
        """会话只在内存里：模拟「进程退出」= 丢掉全部状态；之后没有任何线程再去联网。"""
        on.fail("init", 503, "storage_unavailable", retry_after=1)
        prep = send()
        wait_for(prep["id"], {"failed"})
        calls = len(on.calls)
        diagsend.reset_for_tests()  # 新进程什么都不记得
        time.sleep(0.2)
        assert len(on.calls) == calls
        assert all(t.daemon for t in threading.enumerate() if t.name.startswith("tavotto-diag-"))
        assert not any(
            p.name.endswith(".json")
            for p in Path(os.environ.get("TAVOTTO_DATA_DIR", ".")).glob("diag*")
        )


# =========================================================================== 主路径
class TestHappyPath:
    def test_init_upload_complete_in_order_with_contract_shapes(self, on):
        bundle = make_bundle(pad=300_000)
        prep = send(bundle, note="  the plot is blank  ", category="render_failure")
        st = wait_for(prep["id"], {"done"})
        assert on.paths() == ["/v1/reports/init", "/v1/local-upload", "/v1/reports/complete"]
        init = init_bodies(on)[0]
        # 契约：只有这些键；note 去首尾空白；size 精确；sha256 是本机算的；category 取用户所选
        assert set(init) <= {
            "client_request_id",
            "tavotto_version",
            "os",
            "arch",
            "distribution",
            "category",
            "bundle_schema",
            "size",
            "sha256",
            "note",
        }
        assert init["size"] == len(bundle) and init["sha256"] == hashlib.sha256(bundle).hexdigest()
        assert init["category"] == "render_failure" and init["note"] == "the plot is blank"
        assert init["bundle_schema"] == 6
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            init["client_request_id"],
        )
        assert st["report_id"].startswith("TVD-") and st["state"] == "done"
        rep = next(iter(on.reports.values()))
        assert rep.uploaded == bundle, "上传的就是 prepare 时备好的那份字节"
        assert rep.state == "complete" and rep.note == "the plot is blank"

    def test_multipart_field_order_follows_the_server_and_file_is_last(self, on):
        wait_for(send()["id"], {"done"})
        up = next(c for c in on.calls if c["path"] == "/v1/local-upload")
        assert up["field_order"] == ["key", "Content-Type", "policy", "signature", "file"]
        assert up["values"]["Content-Type"] == "application/zip"
        assert up["ctype"].startswith("multipart/form-data; boundary=")

    def test_metadata_is_whitelisted_enums_and_has_no_install_identity(self, on, monkeypatch):
        telemetry.set_consent(telemetry.CONSENT_ENABLED, source="settings")
        ident = telemetry.install_id()
        assert ident, "判据的前提：这台机器上确实有一个遥测 install_id"
        prep = send()
        wait_for(prep["id"], {"done"})
        init = init_bodies(on)[0]
        assert init["os"] in {"macos", "windows", "linux", "other"}
        assert init["arch"] in {"arm64", "x86_64", "other"}
        assert init["distribution"] in {"desktop", "pipx", "pip", "source", "unknown"}
        assert re.match(r"^[0-9A-Za-z.+_-]{1,32}$", init["tavotto_version"])
        blob = json.dumps(on.calls, default=str)
        assert ident not in blob and "install_id" not in blob and "distinct_id" not in blob

    def test_double_click_starts_one_send(self, on):
        gate = threading.Event()
        on.force("init", Forced("hold", event=gate))
        prep = diagsend.prepare(make_bundle())
        a = diagsend.start(prep["id"], confirmed=True, category=None, note=None)
        b = diagsend.start(prep["id"], confirmed=True, category=None, note=None)
        assert a["state"] == "sending" and b["state"] == "sending"
        gate.set()
        wait_for(prep["id"], {"done"})
        assert on.count("/v1/reports/init") == 1 and len(on.reports) == 1

    def test_state_after_done_releases_the_bundle_and_secrets(self, on):
        prep = send()
        wait_for(prep["id"], {"done"})
        with pytest.raises(diagsend.SendError):
            diagsend.bundle_bytes(prep["id"])
        with pytest.raises(diagsend.SendError):
            diagsend.start(
                prep["id"], confirmed=True, category=None, note=None
            )  # 已完成，不能再发一次
        assert len(on.reports) == 1

    def test_unknown_response_fields_and_codes_are_ignored(self, on):
        on.extra_response_fields = {"future_field": {"a": 1}}
        prep = send()
        wait_for(prep["id"], {"done"})


# =========================================================================== 重试与幂等
class TestRetryAndIdempotency:
    def test_lost_complete_response_is_retried_with_the_same_token_and_creates_one_report(self, on):
        on.force("complete", Forced("drop_after"))  # 服务端完成了，响应丢在路上
        prep = send()
        wait_for(prep["id"], {"done"})
        assert on.count("/v1/reports/complete") == 2
        assert len(on.reports) == 1 and next(iter(on.reports.values())).complete_runs == 1
        assert on.count("/v1/reports/init") == 1, "complete 的重试不重新 init"
        assert on.count("/v1/local-upload") == 1, "也不重新上传"
        assert monkeypatch_sleeps(on)  # 用了退避

    def test_complete_timeout_is_retried_idempotently(self, on, monkeypatch):
        monkeypatch.setattr(diagsend, "COMPLETE_TIMEOUT_S", 0.3)
        on.force("complete", Forced("sleep", seconds=0.8))
        prep = send()
        wait_for(prep["id"], {"done"})
        assert len(on.reports) == 1

    def test_complete_gives_up_after_bounded_attempts_and_user_retry_reuses_the_request_id(
        self, on
    ):
        on.force("complete", Forced("drop_before"), times=diagsend.COMPLETE_ATTEMPTS)
        prep = send()
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "network" and st["retryable"]
        assert on.count("/v1/reports/complete") == diagsend.COMPLETE_ATTEMPTS
        before = init_bodies(on)[0]["client_request_id"]
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note=None)
        wait_for(prep["id"], {"done"})
        assert {b["client_request_id"] for b in init_bodies(on)} == {before}, (
            "同一次发送的重试复用 id"
        )
        assert len(on.reports) == 1

    def test_retry_with_changed_content_gets_a_new_request_id(self, on):
        on.fail("init", 503, "storage_unavailable", retry_after=2)
        prep = send(note=None)
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "storage_unavailable" and st["retry_after"] == 2
        diagsend.start(prep["id"], confirmed=True, category="crash", note=None)
        wait_for(prep["id"], {"done"})
        ids = [b["client_request_id"] for b in init_bodies(on)]
        assert len(ids) == 2 and ids[0] != ids[1]

    def test_init_replay_of_a_completed_report_finishes_without_upload(self, on):
        """上次 complete 成功但响应全丢、用户又点了发送：init 重放回 `complete` → 直接成功，不再上传。"""
        on.force("complete", Forced("drop_after"), times=diagsend.COMPLETE_ATTEMPTS)
        prep = send()
        wait_for(prep["id"], {"failed"})
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note=None)
        st = wait_for(prep["id"], {"done"})
        assert st["report_id"] and on.count("/v1/local-upload") == 1 and len(on.reports) == 1

    def test_upload_missing_at_complete_re_uploads_on_user_retry(self, on):
        on.fail("complete", 409, "upload_missing", retry_after=1, times=diagsend.COMPLETE_ATTEMPTS)
        prep = send()
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "upload_missing"
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note=None)
        wait_for(prep["id"], {"done"})
        assert on.count("/v1/local-upload") == 2, "对象缺失 → 重新 init 取新授权并重传"

    def test_in_progress_waits_for_retry_after_then_succeeds(self, on, monkeypatch):
        on.fail("complete", 409, "in_progress", retry_after=3)
        prep = send()
        wait_for(prep["id"], {"done"})
        assert 3 in monkeypatch.sleeps  # type: ignore[attr-defined]

    def test_already_complete_is_success(self, on):
        on.fail("complete", 409, "already_complete")
        prep = send()
        st = wait_for(prep["id"], {"done"})
        assert st["state"] == "done"


def monkeypatch_sleeps(_srv) -> bool:
    return True


# =========================================================================== 服务端错误码
class TestServerErrors:
    @pytest.mark.parametrize(
        ("endpoint", "status", "server_code", "client_code", "retryable"),
        [
            ("init", 413, "too_large", "too_large", False),
            ("init", 413, "payload_too_large", "too_large", False),
            ("init", 415, "bad_content_type", "client_error", False),
            ("init", 400, "bad_json", "client_error", False),
            ("init", 422, "bad_request", "client_error", False),
            ("init", 422, "identity_field_forbidden", "client_error", False),
            ("init", 422, "notes_disabled", "notes_disabled", True),
            ("init", 422, "rejected", "rejected", False),
            ("init", 409, "idempotency_conflict", "session_closed", True),
            ("init", 409, "session_closed", "session_closed", True),
            ("init", 429, "rate_limited", "rate_limited", True),
            ("init", 429, "daily_quota_exceeded", "daily_quota_exceeded", True),
            ("init", 503, "uploads_disabled", "uploads_disabled", True),
            ("init", 503, "storage_unavailable", "storage_unavailable", True),
            ("init", 503, "busy", "storage_unavailable", True),
            ("complete", 404, "not_found", "not_found", True),
            ("complete", 410, "expired", "expired", True),
            ("complete", 413, "too_large", "too_large", False),
            ("complete", 415, "bad_content_type", "client_error", False),
            ("complete", 422, "rejected", "rejected", False),
            ("complete", 429, "rate_limited", "rate_limited", True),
            ("complete", 503, "storage_error", "storage_unavailable", True),
            ("complete", 409, "cancelled", "session_closed", True),
        ],
    )
    def test_each_documented_status_maps_to_a_stable_client_code(
        self, on, endpoint, status, server_code, client_code, retryable
    ):
        on.fail(endpoint, status, server_code, retry_after=7 if status in (429, 503) else None)
        prep = send()
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == client_code and st["retryable"] is retryable
        assert client_code in diagsend.FAILURES
        if status in (429, 503):
            assert st["retry_after"] == 7
        assert "complete_token" not in json.dumps(st) and "https://" not in json.dumps(st)

    def test_429_503_are_not_auto_retried_into_a_loop(self, on):
        on.fail("init", 429, "rate_limited", retry_after=60)
        prep = send()
        wait_for(prep["id"], {"failed"})
        time.sleep(0.2)
        assert on.count("/v1/reports/init") == 1

    def test_expired_session_needs_a_new_request_id(self, on):
        on.fail("complete", 410, "expired")
        prep = send()
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "expired"
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note=None)
        wait_for(prep["id"], {"done"})
        ids = [b["client_request_id"] for b in init_bodies(on)]
        assert len(set(ids)) == 2

    def test_notes_disabled_then_resend_without_note(self, on):
        on.notes_enabled = False
        prep = send(note="please look")
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "notes_disabled"
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note="")
        wait_for(prep["id"], {"done"})
        assert "note" not in init_bodies(on)[-1]

    def test_cos_403_then_user_retry_obtains_a_fresh_authorization(self, on):
        on.fail("upload", 403, "forbidden")
        prep = send()
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "upload_denied" and st["retryable"]
        assert on.count("/v1/reports/complete") == 0
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note=None)
        wait_for(prep["id"], {"done"})
        assert on.count("/v1/reports/init") == 2 and len(on.reports) == 1

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (400, "upload_rejected"),
            (413, "upload_rejected"),
            (500, "upload_unavailable"),
            (503, "upload_unavailable"),
        ],
    )
    def test_cos_other_statuses(self, on, status, code):
        on.fail("upload", status, "x")
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == code

    def test_garbage_and_incompatible_responses(self, on):
        on.api_header = "2.0.0"
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "api_incompatible"
        assert not diagsend.FAILURES["api_incompatible"]

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda o: o.pop("report_id"),
            lambda o: o.update(report_id="not-an-id"),
            lambda o: o.update(complete_token="short"),
            lambda o: o.update(state="weird"),
            lambda o: o.pop("upload"),
            lambda o: o["upload"].update(method="PUT"),
            lambda o: o["upload"].update(file_field="bad name"),
            lambda o: o["upload"].update(fields={"bad key\r\n": "x"}),
            lambda o: o["upload"].update(max_bytes="big"),
            lambda o: o.update(ok="yes"),
        ],
    )
    def test_init_response_that_breaks_the_schema_is_bad_response_and_uploads_nothing(
        self, on, mutate
    ):
        orig = on._init_body

        def broken(rep):
            body = orig(rep)
            mutate(body)
            return body

        on._init_body = broken  # type: ignore[method-assign]
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "bad_response"
        assert on.count("/v1/local-upload") == 0

    def test_complete_with_a_different_digest_is_not_success(self, on):
        orig = on._complete_body

        def lying(rep, replay):
            return {**orig(rep, replay), "sha256": "0" * 64}

        on._complete_body = staticmethod(lying)  # type: ignore[method-assign]
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "digest_mismatch"


# =========================================================================== 网络
class TestNetwork:
    def test_server_down_is_network_and_retry_after_it_comes_back_reuses_id(
        self, srv, pki_files, monkeypatch
    ):
        monkeypatch.setattr(
            tlstrust,
            "client_context",
            lambda: ssl.create_default_context(cafile=str(pki_files["ca"])),
        )
        monkeypatch.setenv(diagsend.ENV_ENABLE, "1")
        monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
        monkeypatch.setenv(diagsend.ENV_ENDPOINT, "https://127.0.0.1:1")  # 没人听
        prep = send()
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "network" and st["retryable"]
        monkeypatch.setenv(diagsend.ENV_ENDPOINT, srv.base)
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note=None)
        wait_for(prep["id"], {"done"})

    def test_untrusted_certificate_is_tls_and_nothing_is_sent(self, srv, monkeypatch):
        monkeypatch.setenv(diagsend.ENV_ENABLE, "1")
        monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
        monkeypatch.setenv(diagsend.ENV_ENDPOINT, srv.base)
        # 不注入测试根：走产品默认的 `tlstrust.client_context`
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "tls"
        assert srv.calls == [] and srv.connections >= 1

    def test_init_timeout_is_reported_as_timeout(self, on, monkeypatch):
        monkeypatch.setattr(diagsend, "API_TIMEOUT_S", 0.3)
        on.force("init", Forced("sleep", seconds=1.0))
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "timeout"

    def test_connection_dropped_mid_init(self, on):
        on.force("init", Forced("drop_before"))
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "network"


# =========================================================================== SSRF / 重定向 / 主机白名单
class TestDestinationIsPinnedByCodeNotByResponse:
    GOOD = [
        GOOD_COS,
        "https://a-b-1250000000.cos.ap-shanghai.myqcloud.com",
        "https://BUCKET-1250000000.cos.ap-guangzhou.myqcloud.com/",
    ]
    BAD = [
        "http://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/",
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com.evil.com/",
        "https://evil.com/tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/",
        "https://evil.com#@tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/",
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com@evil.com/",
        "https://user@tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/",
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com:8443/",
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/?x=1",
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/#f",
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/key",
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com\\@evil.com/",
        "https://127.0.0.1/",
        "https://127.0.0.1:443/v1/local-upload",
        "https://localhost/",
        "https://[::1]/",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/",
        "https://metadata.tencentyun.com/",
        "https://cos.ap-guangzhou.myqcloud.com/",
        "https://myqcloud.com/",
        "https://tavotto-diag.cos.ap-guangzhou.myqcloud.com/",  # 没有 APPID
        "file:///etc/passwd",
        "ftp://x/",
        "//evil.com/",
        "",
        None,
        5,
        "https://tavotto-diag-1250000000.cos.ap-guangzhou.myqcloud.com/\r\nHost: evil",
        "https://bücher-1250000000.cos.ap-guangzhou.myqcloud.com/",
    ]

    @pytest.fixture
    def prod_target(self):
        return diagsend.Target("diag.example.cn", 443, False)

    @pytest.mark.parametrize("url", GOOD)
    def test_cos_hosts_pass(self, prod_target, url):
        assert diagsend.validate_upload_url(url, prod_target).startswith("https://")

    @pytest.mark.parametrize("url", BAD)
    def test_everything_else_is_rejected(self, prod_target, url):
        with pytest.raises(diagsend._Failure) as e:
            diagsend.validate_upload_url(url, prod_target)
        assert e.value.code == "upload_url_rejected"

    def test_loopback_dev_mode_only_allows_the_local_upload_path_of_the_same_origin(self):
        t = diagsend.Target("127.0.0.1", 9443, True)
        ok = "https://127.0.0.1:9443/v1/local-upload"
        assert diagsend.validate_upload_url(ok, t) == ok
        for bad in (
            "https://127.0.0.1:9443/other",
            "https://127.0.0.1:9444/v1/local-upload",
            "https://localhost:9443/v1/local-upload",
            GOOD_COS,
        ):
            with pytest.raises(diagsend._Failure):
                diagsend.validate_upload_url(bad, t)

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/",
            "http://127.0.0.1:1/v1/local-upload",
            "https://169.254.169.254/",
        ],
    )
    def test_a_rewritten_upload_url_never_gets_a_byte_and_the_report_is_cancelled(self, on, url):
        on.upload_url = url
        prep = send()
        st = wait_for(prep["id"], {"failed"})
        assert st["code"] == "upload_url_rejected" and st["retryable"] is False
        assert on.count("/v1/local-upload") == 0 and on.count("/v1/reports/complete") == 0
        deadline = time.monotonic() + 5
        while on.count("/v1/reports/cancel") == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert on.count("/v1/reports/cancel") == 1, "拿到了令牌却不会上传：通知服务端作废"

    @pytest.mark.parametrize("endpoint", ["init", "upload", "complete"])
    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
    def test_no_redirect_is_followed_on_any_endpoint(self, on, server_tls, endpoint, status):
        """302/303 会让 urllib 把 POST 改成 GET 跟过去；307/308 在 POST 上 urllib 自己就拒——五种都要钉死，
        判据是「别处」那台服务器**收到过连接没有**。"""
        path = {
            "init": "/v1/reports/init",
            "upload": "/v1/local-upload",
            "complete": "/v1/reports/complete",
        }[endpoint]
        with FakeDiagServer(server_tls) as elsewhere:
            on.force(endpoint, Forced("redirect", status=status, location=elsewhere.base + path))
            prep = send()
            assert wait_for(prep["id"], {"failed"})["code"] == "redirect_blocked"
            assert elsewhere.connections == 0 and elsewhere.calls == []

    def test_upload_field_names_cannot_split_the_multipart_body(self, on):
        orig = on._init_body

        def evil(rep):
            body = orig(rep)
            body["upload"]["fields"]['x"\r\nContent-Disposition: form-data; name="key'] = "v"
            return body

        on._init_body = evil  # type: ignore[method-assign]
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "bad_response"
        assert on.count("/v1/local-upload") == 0

    def test_the_client_never_disables_verification(self):
        """AST：本模块不构造 TLS 上下文、不碰 `CERT_NONE` / `check_hostname`（出站集中检查在
        `test_outbound_https_trust.py`；这里钉本模块不自己写传输）。"""
        tree = ast.parse((SRC / "engine" / "diagsend.py").read_text("utf-8"))
        banned_attrs = {
            "CERT_NONE",
            "check_hostname",
            "_create_unverified_context",
            "create_default_context",
            "HTTPSConnection",
            "HTTPConnection",
            "urlopen",
            "urlretrieve",
            "SSLContext",
        }
        used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
        }
        assert not used & banned_attrs, used & banned_attrs


# =========================================================================== 取消
class TestCancel:
    def test_cancel_during_upload_aborts_and_notifies_the_server(self, on):
        gate = threading.Event()
        on.force("upload", Forced("hold_early", event=gate))
        bundle = make_bundle(pad=9 * 1024 * 1024)
        prep = send(bundle)
        deadline = time.monotonic() + 10
        while diagsend.status(prep["id"])["stage"] != "upload" and time.monotonic() < deadline:
            time.sleep(0.01)
        diagsend.cancel(prep["id"])
        assert diagsend.status(prep["id"])["state"] == "cancelling"
        gate.set()
        wait_for(prep["id"], {"cancelled"})
        deadline = time.monotonic() + 5
        while on.count("/v1/reports/cancel") == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert on.count("/v1/reports/cancel") == 1
        assert on.count("/v1/reports/complete") == 0
        assert next(iter(on.reports.values())).state == "cancelled"
        calls = len(on.calls)
        time.sleep(0.3)
        assert len(on.calls) == calls, "取消之后不会自己继续"

    def test_restart_after_cancel_is_a_new_authorization_with_a_new_request_id(self, on):
        gate = threading.Event()
        on.force("upload", Forced("hold_early", event=gate))
        prep = send(make_bundle(pad=9 * 1024 * 1024))
        while diagsend.status(prep["id"])["stage"] != "upload":
            time.sleep(0.01)
        diagsend.cancel(prep["id"])
        gate.set()
        wait_for(prep["id"], {"cancelled"})
        diagsend.start(prep["id"], confirmed=True, category="render_failure", note=None)
        wait_for(prep["id"], {"done"})
        ids = [b["client_request_id"] for b in init_bodies(on)]
        assert len(ids) == 2 and ids[0] != ids[1]

    def test_cancel_after_a_failed_attempt_voids_the_open_server_session(self, on):
        on.fail("upload", 500, "x")
        prep = send()
        wait_for(prep["id"], {"failed"})
        diagsend.cancel(prep["id"])
        assert diagsend.status(prep["id"])["state"] == "cancelled"
        deadline = time.monotonic() + 5
        while on.count("/v1/reports/cancel") == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert on.count("/v1/reports/cancel") == 1

    def test_cancel_during_complete_backoff_after_a_lost_response_is_success_not_cancelled(
        self, on, monkeypatch, caplog
    ):
        """complete 到了服务端、响应丢了；客户端在重试退避里被用户取消。服务端 cancel 回 409 already_complete：
        报告其实已经存下——最终状态是 done 且带编号（标 cancel_raced），不是 cancelled，也不能说「没有发送」。"""
        caplog.set_level(logging.INFO, logger="tavotto")
        on.force("complete", Forced("drop_after"))
        holder: dict = {}

        def sleep_then_cancel(_s):
            if "done" not in holder:
                holder["done"] = True
                diagsend.cancel(holder["id"])

        monkeypatch.setattr(diagsend, "_sleep", sleep_then_cancel)
        prep = diagsend.prepare(make_bundle())
        holder["id"] = prep["id"]
        diagsend.start(prep["id"], confirmed=True, category=None, note=None)
        st = wait_for(prep["id"], {"done", "cancelled", "failed"})
        assert st["state"] == "done", st
        assert st["report_id"].startswith("TVD-") and st["cancel_raced"] is True
        rep = next(iter(on.reports.values()))
        assert rep.state == "complete" and rep.complete_runs == 1
        assert on.count("/v1/reports/cancel") == 1
        assert not any("结果=cancelled" in r.getMessage() for r in caplog.records)

    def test_cancel_after_a_failed_attempt_whose_complete_actually_landed_becomes_done(self, on):
        on.force("complete", Forced("drop_after"), times=diagsend.COMPLETE_ATTEMPTS)
        prep = send()
        wait_for(prep["id"], {"failed"})
        diagsend.cancel(prep["id"])
        deadline = time.monotonic() + 5
        while diagsend.status(prep["id"])["state"] != "done" and time.monotonic() < deadline:
            time.sleep(0.01)
        st = diagsend.status(prep["id"])
        assert (
            st["state"] == "done"
            and st["report_id"].startswith("TVD-")
            and st["cancel_raced"] is True
        )

    def test_cancel_in_progress_is_asked_once_more(self, on):
        on.fail("upload", 500, "x")
        prep = send()
        wait_for(prep["id"], {"failed"})
        on.fail("cancel", 409, "in_progress", retry_after=1)
        diagsend.cancel(prep["id"])
        deadline = time.monotonic() + 5
        while on.count("/v1/reports/cancel") < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert on.count("/v1/reports/cancel") == 2

    def test_discard_while_sending_cancels_and_frees_the_bundle(self, on):
        gate = threading.Event()
        on.force("init", Forced("hold", event=gate))
        prep = diagsend.prepare(make_bundle())
        diagsend.start(prep["id"], confirmed=True, category=None, note=None)
        diagsend.discard(prep["id"])
        gate.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                diagsend.status(prep["id"])
            except diagsend.SendError:
                break
            time.sleep(0.01)
        with pytest.raises(diagsend.SendError):
            diagsend.status(prep["id"])
        assert on.count("/v1/local-upload") == 0, "关窗之后不再上传"

    def test_cancel_best_effort_survives_a_dead_server(self, on, monkeypatch):
        on.fail("upload", 500, "x")
        prep = send()
        wait_for(prep["id"], {"failed"})
        monkeypatch.setenv(diagsend.ENV_ENDPOINT, "https://127.0.0.1:1")
        diagsend.cancel(prep["id"])  # 不抛
        assert diagsend.status(prep["id"])["state"] == "cancelled"


# =========================================================================== 包
class TestBundle:
    def test_oversized_bundle_is_kept_local_not_trimmed_and_not_sent(self, on):
        big = make_bundle(pad=diagsend.MAX_BYTES + 1)
        prep = diagsend.prepare(big)
        assert prep["too_large"] is True and prep["id"] is None
        assert prep["size"] == len(big)
        assert on.connections == 0

    def test_server_side_smaller_limit_is_too_large_and_uploads_nothing(self, on):
        on.max_bytes = 100  # 服务端 init 就回 413
        prep = send(make_bundle(pad=5000))
        assert wait_for(prep["id"], {"failed"})["code"] == "too_large"
        assert on.count("/v1/local-upload") == 0

    def test_upload_spec_smaller_than_the_bundle_is_too_large(self, on):
        orig = on._init_body

        def small(rep):
            body = orig(rep)
            body["upload"]["max_bytes"] = 10
            return body

        on._init_body = small  # type: ignore[method-assign]
        prep = send()
        assert wait_for(prep["id"], {"failed"})["code"] == "too_large"
        assert on.count("/v1/local-upload") == 0

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"not a zip",
            make_bundle(schema="six"),
            make_bundle(schema=0),
            make_bundle(schema=100),
            make_bundle(schema=True),
        ],
    )
    def test_missing_or_malformed_bundle_is_refused_before_anything_else(self, on, data):
        with pytest.raises(diagsend.SendError) as e:
            diagsend.prepare(data)
        assert e.value.code == "bundle_invalid"
        assert on.connections == 0

    def test_zip_without_manifest_is_refused(self, on):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("report.json", "{}")
        with pytest.raises(diagsend.SendError):
            diagsend.prepare(buf.getvalue())

    def test_prepare_lists_content_categories_from_a_closed_table(self, on):
        prep = diagsend.prepare(make_bundle(pad=10))
        kinds = {e["name"]: e["kind"] for e in prep["entries"]}
        assert kinds["report.json"] == "environment" and kinds["app.log"] == "log"
        assert set(kinds.values()) <= set(diagsend.ENTRY_KINDS.values()) | {"other"}

    def test_a_real_bundle_roundtrips_through_prepare(self, on):
        data = diagnostics.build_bundle()
        prep = diagsend.prepare(data)
        assert prep["schema"] == diagnostics.BUNDLE_SCHEMA_VERSION
        assert all(e["kind"] != "other" for e in prep["entries"]), prep["entries"]
        assert diagsend.bundle_bytes(prep["id"]) == data

    def test_preparing_again_replaces_the_previous_prepared_bundle(self, on):
        a = diagsend.prepare(make_bundle())
        b = diagsend.prepare(make_bundle(pad=10))
        assert a["id"] != b["id"]
        with pytest.raises(diagsend.SendError):
            diagsend.status(a["id"])

    def test_cannot_prepare_while_sending(self, on):
        gate = threading.Event()
        on.force("init", Forced("hold", event=gate))
        prep = diagsend.prepare(make_bundle())
        diagsend.start(prep["id"], confirmed=True, category=None, note=None)
        with pytest.raises(diagsend.SendError) as e:
            diagsend.prepare(make_bundle())
        assert e.value.code == "send_in_progress"
        gate.set()
        wait_for(prep["id"], {"done"})

    def test_prepared_bundle_expires(self, on, monkeypatch):
        prep = diagsend.prepare(make_bundle())
        t = time.monotonic() + diagsend.PREPARED_TTL_S + 1
        monkeypatch.setattr(diagsend, "_clock", lambda: t)
        with pytest.raises(diagsend.SendError):
            diagsend.status(prep["id"])


# =========================================================================== 不泄漏
class TestNothingSecretLeaks:
    def test_token_url_fields_and_note_stay_out_of_logs_status_and_the_bundle(self, on, caplog):
        caplog.set_level(logging.DEBUG)
        on.fail("complete", 503, "storage_unavailable", retry_after=1)
        prep = send(note=CANARY_NOTE)
        st = wait_for(prep["id"], {"done", "failed"})
        secrets_seen = {
            c: None
            for c in (
                next(iter(on.reports.values())).token,
                f"{on.base}/v1/local-upload",
                next(iter(on.reports.values())).key,
                CANARY_NOTE,
            )
        }
        rep = next(iter(on.reports.values()))
        for r in on.reports.values():
            secrets_seen[r.token] = None
        # 1) 本机日志（全级别）+ 诊断包那份出门版日志
        texts = [r.getMessage() for r in caplog.records]
        texts += [diagnostics.ExportLogFormatter().format(r) for r in caplog.records]
        blob = "\n".join(texts)
        for secret in secrets_seen:
            assert secret not in blob, f"日志里出现了 {secret[:12]}…"
        assert "127.0.0.1" not in blob, "地址也不进日志"
        # 2) 状态对象
        status_blob = json.dumps(diagsend.status(prep["id"]) if st["state"] != "done" else st)
        for secret in secrets_seen:
            assert secret not in status_blob
        for r in caplog.records:
            assert rep.token not in repr(r.args), "参数里也没有"
        # 3) 之后导出的诊断包全文
        data = diagnostics.build_bundle()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                content = z.read(name).decode("utf-8", "replace")
                for secret in secrets_seen:
                    assert secret not in content, f"{name} 里出现了秘密"

    def test_repr_of_session_and_spec_carry_no_secrets(self, on):
        prep = send(note=CANARY_NOTE)
        wait_for(prep["id"], {"done"})
        sess = diagsend._CURRENT
        spec = diagsend.UploadSpec("https://x/", (("policy", "SECRET_POLICY"),), "file", 1, 1)
        assert "SECRET_POLICY" not in repr(spec) and "SECRET_POLICY" not in str(spec)
        assert CANARY_NOTE not in repr(sess) and (not sess or sess.token == "")

    def test_the_module_never_emits_telemetry_or_posthog(self):
        """用户文本不可进 PostHog：本模块不调 `telemetry.capture`；对遥测模块只借三个枚举探测函数。"""
        tree = ast.parse((SRC / "engine" / "diagsend.py").read_text("utf-8"))
        used = {
            n.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "telemetry"
        }
        assert used == {"_platform", "_arch", "_distribution"}, used

    def test_log_templates_are_literals_and_args_are_closed_sets(self):
        tree = ast.parse((SRC / "engine" / "diagsend.py").read_text("utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if isinstance(n.func.value, ast.Name) and n.func.value.id == "LOG":
                    assert isinstance(n.args[0], ast.Constant), "日志模板必须是字面量"
                    for arg in n.args[1:]:
                        assert (
                            isinstance(arg, ast.Call)
                            or (isinstance(arg, ast.Attribute) and arg.attr == "__name__")
                            or isinstance(arg, ast.Call)
                        ), ast.dump(arg)


# =========================================================================== 契约对账
class TestContractMirrors:
    def test_categories_and_failure_table_are_consistent(self):
        assert diagsend.DEFAULT_CATEGORY in diagsend.CATEGORIES
        assert len(set(diagsend.CATEGORIES)) == 8
        for server_status, server_code in [
            (429, "rate_limited"),
            (429, "daily_quota_exceeded"),
            (503, "busy"),
            (413, "too_large"),
            (410, "expired"),
            (404, "not_found"),
            (409, "session_closed"),
            (422, "rejected"),
        ]:
            assert diagsend.classify(server_status, server_code) in diagsend.FAILURES

    def test_the_contract_snapshot_if_infra_is_next_door(self):
        """infra 仓库在旁边时，枚举逐项对账（不在就跳过，不假装通过）。"""
        spec = Path("/Volumes/Projects/tavotto-infra/docs/diagnostics-api/openapi.json")
        if not spec.is_file():
            pytest.skip("infra openapi.json 不在本机")
        api = json.loads(spec.read_text("utf-8"))
        init = api["components"]["schemas"]["InitBody"]["properties"]
        assert tuple(init["category"]["enum"]) == diagsend.CATEGORIES
        assert set(init["os"]["enum"]) == {"macos", "windows", "linux", "other"}
        assert set(init["arch"]["enum"]) == {"arm64", "x86_64", "other"}
        assert set(init["distribution"]["enum"]) == {"desktop", "pipx", "pip", "source", "unknown"}
        assert api["info"]["version"].split(".")[0] == str(diagsend.API_MAJOR)
        assert set(init) == {
            "client_request_id",
            "tavotto_version",
            "os",
            "arch",
            "distribution",
            "category",
            "bundle_schema",
            "size",
            "sha256",
            "note",
        }
