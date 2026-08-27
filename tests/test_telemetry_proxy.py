"""遥测代理（services/telemetry_proxy）：schema、认证、脱敏与跨侧契约对拍。

**没有一个用例碰真实的 PostHog**：`posthog.send` 一律被替换成收集器。
代理不进 wheel/sdist，也不属于 Tavotto 的运行时依赖；它在这里被测，是因为
「客户端发的事件代理认不认识」是一条跨侧契约，跨侧契约必须有硬门禁。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

PROXY_ROOT = Path(__file__).resolve().parent.parent / "services" / "telemetry_proxy"
if not PROXY_ROOT.is_dir():
    # 代理不进 wheel/sdist（它是独立部署的服务）。从源码树以外跑 pytest 时
    # 整个模块跳过，而不是在 import 阶段炸掉收集。
    pytest.skip("没有 services/telemetry_proxy（wheel/sdist 里不含代理服务）",
                allow_module_level=True)
sys.path.insert(0, str(PROXY_ROOT))

from tavotto_telemetry_proxy import (  # noqa: E402
    contract as proxy_contract,  # noqa: E402
    core,
    posthog,
)

TOKEN = "s3cret-metrics-token-0123456789"
JSON_HEADERS = {"content-type": "application/json"}


@pytest.fixture
def upstream(monkeypatch):
    """拦下上游投递，返回收到的批次（列表的列表）。"""
    monkeypatch.setenv("POSTHOG_PROJECT_KEY", "phc_test_key")
    monkeypatch.setenv("POSTHOG_INGEST_URL", "https://us.i.posthog.com/batch/")
    monkeypatch.setenv("TAVOTTO_METRICS_TOKEN", TOKEN)
    monkeypatch.delenv("POSTHOG_PERSON_PROFILES", raising=False)
    batches: list[list[dict]] = []
    monkeypatch.setattr(posthog, "send", batches.append)
    return batches


def _post(path: str, body, headers=None, raw: bytes | None = None):
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    return core.handle("POST", path, {**JSON_HEADERS, **(headers or {})}, data)


def _event(**over) -> dict:
    return {
        "schema_version": 1,
        "distinct_id": str(uuid.uuid4()),
        "event": "export_completed",
        "properties": {"app_version": "0.8.0", "platform": "macos",
                       "arch": "arm64", "distribution": "desktop",
                       "pdf": True, "png": False, "with_proof": False,
                       "panel_count": 4},
        **over,
    }


# ---------------------------------------------------------------------------
# 健康检查与路由
# ---------------------------------------------------------------------------
def test_healthz(upstream):
    status, body = core.handle("GET", "/healthz", {}, b"")
    assert status == 200 and body["ok"] is True


def test_unknown_path_is_404(upstream):
    assert _post("/v1/anything", _event())[0] == 404


def test_events_is_post_only(upstream):
    assert core.handle("GET", "/v1/events", JSON_HEADERS, b"")[0] == 405


def test_non_json_content_type_rejected(upstream):
    status, body = _post("/v1/events", _event(),
                         headers={"content-type": "text/plain"})
    assert status == 415 and body["code"] == "bad_content_type"


# ---------------------------------------------------------------------------
# /v1/events 的 schema
# ---------------------------------------------------------------------------
def test_valid_event_is_accepted_and_forwarded(upstream):
    status, body = _post("/v1/events", _event())
    assert status == 200 and body["ok"] is True
    (batch,) = upstream
    (sent,) = batch
    assert sent["event"] == "export_completed"
    assert sent["properties"]["panel_count"] == 4
    assert sent["properties"]["schema_version"] == 1


def test_unknown_event_rejected(upstream):
    status, body = _post("/v1/events", _event(event="figure_uploaded"))
    assert status == 400 and body["code"] == "unknown_event"
    assert upstream == []


def test_unknown_property_rejected(upstream):
    ev = _event()
    ev["properties"]["region"] = "cn-north"
    status, body = _post("/v1/events", ev)
    assert status == 400 and body["code"] == "unknown_property"
    assert upstream == []


@pytest.mark.parametrize("key,value", [
    ("stem", "Fig1_kinetics"),
    ("filename", "论文数据.pdf"),
    ("path", "/Users/me/figures"),
    ("script", "fig1.py"),
    ("prompt", "把第三条曲线改成红色"),
    ("source_code", "import numpy as np"),
    ("axis_label", "Wavenumber / cm-1"),
])
def test_content_bearing_properties_are_structurally_impossible(upstream, key, value):
    """文件名 / 路径 / 源码 / 提示词 / 图内文字：在 schema 层就进不来。"""
    ev = _event()
    ev["properties"][key] = value
    status, body = _post("/v1/events", ev)
    assert status == 400 and body["code"] == "unknown_property"
    # 拒绝的响应里也不能把它回声出去
    assert value not in json.dumps(body, ensure_ascii=False)
    assert upstream == []


@pytest.mark.parametrize("value", [
    {"nested": "object"},
    ["a", "list"],
    {"a": {"b": {"c": 1}}},
])
def test_nested_containers_rejected(upstream, value):
    ev = _event()
    ev["properties"]["panel_count"] = value
    assert _post("/v1/events", ev)[1]["code"] == "bad_property"
    assert upstream == []


@pytest.mark.parametrize("distinct_id", [
    "not-a-uuid",
    "me@example.com",
    "MacBook-Pro.local",
    "00000000-0000-0000-0000-000000000000",     # 不是 v4
    "550e8400-e29b-11d4-a716-446655440000",     # v1（含 MAC/时间戳）
    "",
    12345,
])
def test_invalid_distinct_id_rejected(upstream, distinct_id):
    status, body = _post("/v1/events", _event(distinct_id=distinct_id))
    assert status == 400 and body["code"] == "bad_distinct_id"
    assert upstream == []


def test_oversized_request_rejected(upstream):
    raw = b'{"schema_version":1,"pad":"' + b"x" * (core.MAX_EVENT_BODY + 10) + b'"}'
    status, body = _post("/v1/events", None, raw=raw)
    assert status == 413 and body["code"] == "payload_too_large"
    assert upstream == []


def test_malformed_json_rejected(upstream):
    status, body = _post("/v1/events", None, raw=b"{not json")
    assert status == 400 and body["code"] == "bad_json"
    assert _post("/v1/events", None, raw=b"[1,2,3]")[1]["code"] == "bad_json"
    assert upstream == []


def test_wrong_schema_version_rejected(upstream):
    status, body = _post("/v1/events", _event(schema_version=2))
    assert status == 400 and body["code"] == "bad_schema_version"
    assert upstream == []


# ---------------------------------------------------------------------------
# 提供商属性与隐私
# ---------------------------------------------------------------------------
def test_geoip_is_disabled_on_every_forwarded_event(upstream):
    _post("/v1/events", _event())
    assert upstream[0][0]["properties"]["$geoip_disable"] is True


def test_client_ip_and_headers_are_never_forwarded(upstream):
    """代理收到的请求头一个都不能变成事件属性。"""
    status, _ = _post("/v1/events", _event(), headers={
        "x-forwarded-for": "203.0.113.7",
        "user-agent": "Tavotto/0.8.0 (macOS 15.3; MacBook-Pro.local)",
        "cookie": "session=abc123",
        "referer": "https://example.com/secret",
    })
    assert status == 200
    blob = json.dumps(upstream[0], ensure_ascii=False)
    for leaked in ("203.0.113.7", "MacBook-Pro.local", "session=abc123",
                   "example.com", "x-forwarded-for", "user-agent"):
        assert leaked not in blob


def test_dollar_properties_from_the_client_are_rejected(upstream):
    """`$` 属性归代理所有：客户端塞进来的一律拒，不是覆盖。"""
    ev = _event()
    ev["properties"]["$geoip_disable"] = False
    assert _post("/v1/events", ev)[1]["code"] == "unknown_property"
    ev = _event()
    ev["properties"]["$ip"] = "203.0.113.7"
    assert _post("/v1/events", ev)[1]["code"] == "unknown_property"
    assert upstream == []


def test_person_profile_mode_is_configurable_and_defaults_to_identified(
        upstream, monkeypatch):
    _post("/v1/events", _event())
    assert "$process_person_profile" not in upstream[0][0]["properties"]
    upstream.clear()
    monkeypatch.setenv("POSTHOG_PERSON_PROFILES", "anonymous")
    _post("/v1/events", _event())
    assert upstream[0][0]["properties"]["$process_person_profile"] is False


# ---------------------------------------------------------------------------
# 上游故障
# ---------------------------------------------------------------------------
def test_upstream_outage_returns_502_without_leaking_secrets(monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_KEY", "phc_super_secret_key")
    monkeypatch.setenv("TAVOTTO_METRICS_TOKEN", TOKEN)

    def down(_batch):
        raise posthog.UpstreamError("analytics backend unreachable")
    monkeypatch.setattr(posthog, "send", down)
    status, body = _post("/v1/events", _event())
    assert status == 502 and body["code"] == "upstream_error"
    text = json.dumps(body)
    assert "phc_super_secret_key" not in text and TOKEN not in text


def test_missing_project_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_KEY", raising=False)
    status, body = _post("/v1/events", _event())
    assert status == 502
    assert "phc" not in json.dumps(body)


def test_upstream_http_error_does_not_echo_the_response_body(monkeypatch):
    import urllib.error
    monkeypatch.setenv("POSTHOG_PROJECT_KEY", "phc_test_key")

    class FakeResp:
        def read(self, *_a):
            return b'{"echo": "phc_test_key and the whole payload"}'

        def close(self):
            """`HTTPError` 会把 fp 交给 `addinfourl` 的 closer，GC 时调 close()。

            少了它，回收期抛 `AttributeError: 'FakeResp' object has no
            attribute 'close'`，被 pytest 记成 PytestUnraisableExceptionWarning
            ——用例照绿，但 Windows CI 的日志里常年多一段假 traceback，读日志的人
            要先排除它才能看见真问题。
            """

    def raise_http(*_a, **_kw):
        raise urllib.error.HTTPError("https://us.i.posthog.com/batch/", 401,
                                     "Unauthorized", {}, FakeResp())
    monkeypatch.setattr(posthog.urllib.request, "urlopen", raise_http)
    with pytest.raises(posthog.UpstreamError) as exc:
        posthog.send([{"event": "app_started"}])
    assert "phc_test_key" not in str(exc.value)
    assert "401" in str(exc.value)


# ---------------------------------------------------------------------------
# /v1/metrics
# ---------------------------------------------------------------------------
def _snapshot(**over) -> dict:
    return {
        "event": "github_release_asset_snapshot",
        "properties": {
            "release_id": 111, "release_tag": "v0.8.0", "asset_id": 222,
            "asset_role": "installer", "platform": "macos",
            "download_count_total": 42, "observed_date": "2026-08-20",
            "snapshot_key": "gh-asset:222:2026-08-20",
            **over.pop("properties", {}),
        },
        **over,
    }


def _metrics(body, token: str | None = TOKEN):
    headers = {"authorization": f"Bearer {token}"} if token is not None else {}
    return _post("/v1/metrics", body, headers=headers)


def test_metrics_requires_a_bearer_token(upstream):
    status, body = _metrics({"schema_version": 1, "events": [_snapshot()]},
                            token=None)
    assert status == 401 and body["code"] == "unauthorized"
    assert upstream == []


def test_metrics_rejects_a_wrong_token(upstream):
    status, body = _metrics({"schema_version": 1, "events": [_snapshot()]},
                            token="not-the-token")
    assert status == 401
    assert upstream == []


def test_metrics_accepts_the_right_token(upstream):
    status, body = _metrics({"schema_version": 1, "events": [_snapshot()]})
    assert status == 200 and body["accepted"] == 1
    (batch,) = upstream
    assert batch[0]["distinct_id"] == proxy_contract.METRICS_DISTINCT_ID
    # 发行量快照永远匿名：它们不对应任何一个人
    assert batch[0]["properties"]["$process_person_profile"] is False


def test_metrics_token_never_appears_in_any_response(upstream):
    for token in (None, "wrong", TOKEN):
        _status, body = _metrics({"schema_version": 1, "events": [_snapshot()]},
                                 token=token)
        assert TOKEN not in json.dumps(body)


def test_metrics_endpoint_is_closed_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv("TAVOTTO_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("POSTHOG_PROJECT_KEY", "phc_test_key")
    assert _metrics({"schema_version": 1, "events": [_snapshot()]})[0] == 401


def test_metrics_rejects_product_events_and_vice_versa(upstream):
    assert _metrics({"schema_version": 1,
                     "events": [{"event": "export_completed",
                                 "properties": {}}]})[1]["code"] == "unknown_event"
    assert _post("/v1/events", _event(
        event="github_release_asset_snapshot"))[1]["code"] == "unknown_event"
    assert upstream == []


def test_metrics_snapshot_uuid_is_deterministic(upstream):
    """同一个 snapshot_key 每次推出同一个事件 uuid——手动重跑采集器时上游
    **有机会**去重。看板仍然按 snapshot_key 去重（上游没承诺幂等）。"""
    _metrics({"schema_version": 1, "events": [_snapshot()]})
    _metrics({"schema_version": 1, "events": [_snapshot()]})
    assert upstream[0][0]["uuid"] == upstream[1][0]["uuid"]
    assert upstream[0][0]["properties"]["snapshot_key"] == "gh-asset:222:2026-08-20"


def test_metrics_batch_is_bounded(upstream):
    body = {"schema_version": 1,
            "events": [_snapshot() for _ in range(core.MAX_METRICS_BATCH + 1)]}
    assert _metrics(body)[0] == 413
    assert upstream == []


# ---------------------------------------------------------------------------
# 跨侧契约
# ---------------------------------------------------------------------------
def test_client_and_proxy_contracts_match():
    """客户端与代理的白名单必须逐条一致。

    漂开的症状是最难查的一种：客户端发了新事件、代理不认识，于是
    「新版本发出去之后那个指标一直是 0」，而两侧的测试各自全绿。
    """
    from tavotto.engine import telemetry as client

    assert client.SCHEMA_VERSION == proxy_contract.SCHEMA_VERSION
    assert set(client.EVENTS) == set(proxy_contract.EVENTS)

    def shape(table: dict) -> dict:
        return {name: {prop: (spec["kind"], tuple(spec.get("values", ())),
                              spec.get("max"))
                       for prop, spec in props.items()}
                for name, props in table.items()}

    assert shape(client.EVENTS) == shape(proxy_contract.EVENTS)
    assert shape({"auto": client.AUTO_PROPS}) == shape({"auto": proxy_contract.AUTO_PROPS})
    # 指标事件是代理独有的：客户端**不该**认识它们（桌面应用发不出发行量快照）
    assert not (set(proxy_contract.METRICS_EVENTS) & set(client.EVENTS))


def test_every_client_event_is_accepted_end_to_end(upstream):
    """逐条把客户端会发的事件喂给代理，确认一条都不会被拒。"""
    from tavotto.engine import telemetry as client

    samples = {
        "telemetry_enabled": {"source": "settings"},
        "app_started": {"app_mode": "desktop"},
        "figure_opened": {"asset_kind": "pdf", "editable": True},
        "figure_edit_completed": {"edit_kind": "layout", "patch_count": 2},
        "canvas_created": {"creation_kind": "blank"},
        "preflight_completed": {"errors": 0, "warnings": 1, "not_verifiable": 2,
                                "suggestions": 3, "passed": True},
        "export_completed": {"pdf": True, "png": True, "with_proof": False,
                             "panel_count": 6},
        "ai_assistant_invoked": {"agent": "claude"},
        "update_completed": {"update_kind": "pipx", "target_version": "0.9.0"},
    }
    assert set(samples) == set(client.EVENTS), "新增事件要在这里补一条样例"
    auto = {"app_version": "0.8.0", "platform": "linux", "arch": "x86_64",
            "distribution": "pip"}
    for name, props in samples.items():
        status, body = _post("/v1/events", _event(event=name,
                                                  properties={**auto, **props}))
        assert status == 200, (name, body)


# ---------------------------------------------------------------------------
# 真实入口层（WSGI）
#
# 这一组是 2026-08-20 首次部署那个 bug 的看护，而它的教训**不是**「换个部署
# 方案」，是：**只测 `core.handle` 看不出入口层坏没坏**。当时入口靠
# `self.path` 路由，而 Vercel 的 rewrite 会把函数收到的路径换成 destination
# （`/api/index`）——上面那 40 多条用例全绿，一部署整站 404。
#
# 现在对外只有一个入口 `wsgi.application`，本地 / 测试 / Vercel 跑的是同一个，
# 下面这几条直接打在它身上。
# ---------------------------------------------------------------------------
PUBLIC_ROUTES = ["/healthz", "/v1/events", "/v1/metrics"]


def _wsgi(method: str, path: str, body: bytes = b"", headers: dict | None = None):
    """像真实服务器那样调 `application`，返回 (状态行, 头, 响应体)。"""
    import io

    from tavotto_telemetry_proxy.wsgi import application

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)) if body else "",
        "CONTENT_TYPE": (headers or {}).get("content-type", "application/json"),
        "wsgi.input": io.BytesIO(body),
    }
    if (headers or {}).get("authorization"):
        environ["HTTP_AUTHORIZATION"] = headers["authorization"]
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = response_headers

    chunks = application(environ, start_response)
    return captured["status"], dict(captured["headers"]), b"".join(chunks)


@pytest.mark.parametrize("route", PUBLIC_ROUTES)
def test_wsgi_serves_every_public_route(upstream, route):
    """每个对外路径经**真实入口**都必须被认出来——404 就说明部署出去是死的。"""
    method = "GET" if route == "/healthz" else "POST"
    status, _headers, body = _wsgi(method, route, b"{}")
    assert json.loads(body).get("code") != "not_found", f"{route} 在 WSGI 层是 404"
    assert not status.startswith("404")


def test_wsgi_routes_on_the_real_request_path(upstream):
    """它按 PATH_INFO 路由：未知路径才是 404，已知路径不许是。"""
    status, _h, body = _wsgi("GET", "/nope")
    assert status.startswith("404") and json.loads(body)["code"] == "not_found"


def test_wsgi_status_line_has_a_reason_phrase(upstream):
    """WSGI 规范要 `"200 OK"` 而不是光一个数字——有的服务器直接拒，
    而那种失败只会在部署之后出现。"""
    for method, route in [("GET", "/healthz"), ("GET", "/nope")]:
        status, _h, _b = _wsgi(method, route)
        assert re.fullmatch(r"\d{3} [A-Za-z][A-Za-z ']*", status), repr(status)


def test_wsgi_rejects_negative_content_length(upstream):
    """负 Content-Length 必须当场 400，且**一个字节都不读**。

    不拦的话 `min(length, MAX+1)` 还是负数，某些 WSGI 服务器把 `read(-1)`
    当「读到 EOF」——keep-alive 连接上这一读会挂到对端超时，一个畸形请求
    占死一个线程（PR #21 评审指出的输入边界）。
    """
    import io

    from tavotto_telemetry_proxy.wsgi import application

    class MustNotRead(io.BytesIO):
        def read(self, *a):  # pragma: no cover - 被调用即失败
            raise AssertionError("负 Content-Length 不该触发任何 read")

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/v1/events",
        "CONTENT_LENGTH": "-1",
        "CONTENT_TYPE": "application/json",
        "wsgi.input": MustNotRead(),
    }
    captured = {}
    chunks = application(environ, lambda s, h: captured.update(status=s))
    assert captured["status"].startswith("400")
    assert b"content-length" in b"".join(chunks)


def test_wsgi_rejects_content_bearing_properties_end_to_end(upstream):
    """夹带文件名的事件，走完整入口也必须被拒。"""
    ev = _event()
    ev["properties"]["stem"] = "Fig1_保密数据"
    status, _h, body = _wsgi("POST", "/v1/events",
                             json.dumps(ev).encode("utf-8"))
    assert status.startswith("400")
    assert json.loads(body)["code"] == "unknown_property"
    assert "保密数据" not in body.decode("utf-8")
    assert upstream == []


def test_wsgi_metrics_still_needs_the_token(upstream):
    status, _h, body = _wsgi("POST", "/v1/metrics",
                             json.dumps({"schema_version": 1, "events": []}).encode())
    assert status.startswith("401")


def test_vercel_entrypoint_points_at_the_wsgi_app():
    """`pyproject.toml` 里配的 entrypoint 必须真的存在且可调用。

    配错了的表现是构建期报「No python entrypoint found」——发布链上才发现。
    """
    text = (PROXY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'entrypoint\s*=\s*"([^"]+)"', text)
    assert m, "pyproject.toml 里没有 [tool.vercel] entrypoint"
    module_path, _, attr = m.group(1).partition(":")
    import importlib

    module = importlib.import_module(module_path)
    assert callable(getattr(module, attr, None)), f"{m.group(1)} 不可调用"


def test_no_second_entrypoint_layer_creeps_back():
    """对外只能有一个入口。`api/` 文件路由那条路会重新引入
    「本地全绿、部署 404」的失败模式——别加回来。"""
    assert not (PROXY_ROOT / "api").exists(), \
        "services/telemetry_proxy/api/ 又出现了：见本节开头"
    conf = json.loads((PROXY_ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert "rewrites" not in conf, \
        "rewrites 会用重写后的 destination 路由，正是当初那个 bug"


def test_self_hosted_server_never_logs_remote_addresses(upstream, capfd):
    """自己起 server 时**不许**打访问日志——那里面有客户端 IP。

    `wsgiref` 的默认 handler 会把 `<IP> - - [时间] "GET /x" 200` 打到 stderr。
    在 FaaS（腾讯云 SCF 的 Web 函数）或自托管上，那会直接进云日志服务——
    等于我们**自己主动**记了一份带来源地址的访问日志，而 docs/privacy.md
    承诺的是「不刻意记录来源地址」。托管方自身的日志不归我们控制（政策里
    如实写着），但我们不能再往上叠一份。
    """
    import http.client
    import threading
    from wsgiref.simple_server import make_server

    from tavotto_telemetry_proxy.wsgi import _QuietHandler, _ThreadingWSGIServer, application

    srv = make_server("127.0.0.1", 0, application,
                      server_class=_ThreadingWSGIServer,
                      handler_class=_QuietHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz")
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()

    out, err = capfd.readouterr()
    blob = out + err
    assert "127.0.0.1" not in blob, f"访问日志里出现了来源地址：{blob!r}"
    assert "GET /healthz" not in blob, f"打了访问日志：{blob!r}"


def test_scf_bootstrap_is_executable_and_binds_the_required_port():
    """SCF 的 Web 函数契约：可执行的 `scf_bootstrap` + 监听 0.0.0.0:9000。

    权限少了函数起不来；端口不对平台探活失败——两者都只在部署之后才暴露。

    **查的是 git 索引里的 mode，不是 `os.stat().st_mode`。** Windows 上普通
    文件的 st_mode 恒为 `0o100666`，`& 0o111` 永远是 0——用它判可执行位的话
    这条用例在 Windows 上必然红（2026-08-20 真的红了一次）。而且 git 记录的
    `100755` 才是**真正决定** Linux/macOS 上 checkout 出来有没有 +x 的东西，
    zip 打包时取的也是它。跨平台一致，且判的是正确的东西。
    """
    boot = PROXY_ROOT / "scf_bootstrap"
    if not boot.exists():
        pytest.skip("这份部署里没有 scf_bootstrap")
    rel = boot.relative_to(PROXY_ROOT.parent.parent).as_posix()
    entry = subprocess.run(
        ["git", "ls-files", "-s", "--", rel],
        cwd=PROXY_ROOT.parent.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout.split()
    assert entry and entry[0] == "100755", (
        f"git 里 {rel} 的 mode 是 {entry[0] if entry else '(未追踪)'}，不是 100755"
        "——Linux/macOS 上 checkout 出来就没有可执行位，SCF 函数起不来"
    )
    text = boot.read_text(encoding="utf-8")
    assert "HOST=0.0.0.0" in text and "PORT=9000" in text
    assert "-u" in text, "不加 -u 的话日志要等缓冲区满才出现，排障时像是没日志"


def test_public_listen_requires_an_explicit_opt_in():
    """默认只听回环。少了这条默认值，本地调试会把一个无鉴权的公开端点
    暴露到局域网里。"""
    src = (PROXY_ROOT / "tavotto_telemetry_proxy" / "wsgi.py").read_text(encoding="utf-8")
    assert 'os.environ.get("HOST") or "127.0.0.1"' in src

