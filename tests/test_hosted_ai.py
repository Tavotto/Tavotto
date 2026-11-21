"""内置 AI「Tavotto Agent」的引擎侧客户端（ADR 0200 §四 / §八 / §十一）：开 / 关任务、设备签名、失败码映射、虚拟连接、限制下发、秘密不落地。

出站全部打桩 `diagsend._send`（没有任何真网络）；「网关」是本文件里的 `FakeGateway`：按 ADR 0200 §四之一 A 的契约核设备签名（用 `license.py` 的签名器对拍），
回任务通行证。授权令牌由测试密钥签发（同 `tests/test_license.py`）。四条功能路径（改图 / 转换 / 修复 / 数据出图）怎么开 / 关任务见本文件后半，
改图的整条 `ai_bridge.run` 与修复端点各有一条贯穿用例。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time

import pytest

from tavotto.engine import (
    ai_connections,
    ai_pricing,
    ai_providers,
    ai_structured,
    config,
    diagsend,
    exectrace,
    fixsession,
    hosted_ai,
    license as lic,
)
from tavotto.engine.agent_runner import loop, wires

nacl_signing = pytest.importorskip("nacl.signing")

GATEWAY = "http://127.0.0.1:18200"
LICENSE_ENDPOINT = "https://license.tavotto.cn"
KID = "test-1"
TASK_TOKEN = "tvk_task_" + "CANARY" + "abcdefghijklmnopqrstuvwxyz0123456789"
TOMORROW_MS = int(time.time() * 1000) + 86_400_000
LIMITS = {"max_turns": 24, "max_total_tokens": 200_000, "max_output_tokens": 8192}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class FakeGateway:
    """按契约回应 `/v1/task` 与 `/v1/task/close`；`script` 里排队的 `(status, 体, 头)` 优先于正常逻辑。记录每一次请求。"""

    def __init__(self, license_signing):
        self.license_signing = license_signing
        self.calls: list[dict] = []
        self.script: list[tuple] = []
        self.limits = dict(LIMITS)

    def __call__(self, req, timeout, limit=0):
        path = req.full_url.removeprefix(GATEWAY)
        assert req.full_url.startswith(GATEWAY), req.full_url  # 目的地只来自解析后的 Target
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append(
            {
                "path": path,
                "body": body,
                "auth": req.get_header("Authorization"),
                "timeout": timeout,
            }
        )
        assert req.get_method() == "POST"
        if self.script:
            status, payload, headers = self.script.pop(0)
            if isinstance(status, Exception):
                raise status
            return diagsend._Response(status, headers or {}, json.dumps(payload).encode())
        if path == "/v1/task":
            self._verify_device_signature(body)
            return diagsend._Response(
                200,
                {},
                json.dumps(
                    {
                        "task_id": "tsk_abc123",
                        "task_token": TASK_TOKEN,
                        "expires_idle_ms": 1_800_000,
                        "ceiling_micro": 100_000,
                        "limits": self.limits,
                        "model": "tavotto-auto",
                        "remaining": 0.63,
                    }
                ).encode(),
            )
        if path == "/v1/task/close":
            assert req.get_header("Authorization") == f"Bearer {TASK_TOKEN}"
            return diagsend._Response(200, {}, b'{"task_id":"tsk_abc123","spent_micro":0}')
        raise AssertionError(path)

    def _verify_device_signature(self, body: dict) -> None:
        assert body["op"] == "ai_task", "op 必须是 ai_task（防止别的端点的签名被重放过来）"
        assert body["kind"] in hosted_ai.KINDS
        pub = base64.b64decode(body["device_pub"])
        # 用 license.py 的 signed_bytes 对拍：被签名字节串的定义只有一份
        nacl_signing.VerifyKey(pub).verify(lic.signed_bytes(body), base64.b64decode(body["sig"]))
        assert abs(time.time() * 1000 - body["ts"]) < 600_000
        # 授权令牌（信封对象）原样带上，网关能用公钥验它
        tok = body["token"]
        payload_raw = base64.b64decode(tok["payload"])
        self.license_signing.verify_key.verify(payload_raw, base64.b64decode(tok["sig"]))
        payload = json.loads(payload_raw)
        assert "hosted-ai" in payload["features"]
        assert payload["device"] == hashlib.sha256(pub).hexdigest()

    def kinds(self) -> list[str]:
        return [c["body"]["kind"] for c in self.calls if c["path"] == "/v1/task"]

    def paths(self) -> list[str]:
        return [c["path"] for c in self.calls]


def mint_token(signing, device_pub_b64: str, features: list[str]) -> dict:
    iat = int(time.time())
    exp = iat + 30 * 86400
    payload = {
        "v": 1,
        "lid": "lic_abcdefghijklmn",
        "features": features,
        "exp": exp,
        "iat": iat,
        "device": hashlib.sha256(base64.b64decode(device_pub_b64)).hexdigest(),
        "refresh_after": iat + 86400,
        "offline_until": exp,
        "email_hint": "m***@gmail.com",
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return {"payload": _b64(raw), "sig": _b64(signing.sign(raw).signature), "kid": KID}


@pytest.fixture
def gw(monkeypatch, tmp_path):
    """已激活 `hosted-ai` 授权的本机 + 指向回环假网关的开发端点。"""
    for name in (hosted_ai.ENV_HARD_OFF, lic.ENV_HARD_OFF):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    monkeypatch.setenv(lic.ENV_ENDPOINT, LICENSE_ENDPOINT)
    monkeypatch.setenv(hosted_ai.ENV_ENDPOINT, GATEWAY)
    signing = nacl_signing.SigningKey.generate()
    monkeypatch.setenv(lic.ENV_TEST_PUBKEY, _b64(bytes(signing.verify_key)))
    lic.reset_for_tests()
    key = lic._signing_key(create=True)
    lic._save(mint_token(signing, _b64(bytes(key.verify_key)), ["hosted-ai"]))
    fake = FakeGateway(signing)
    monkeypatch.setattr(diagsend, "_send", fake)
    assert hosted_ai.available()
    yield fake
    lic.reset_for_tests()


def err(status: int, code: str, **extra):
    return (status, {"error": {"code": code, "message": "x", **extra}}, {})


# ---------------------------------------------------------------------------
# 开关：没开 / 没授权 = 零网络、没有连接
# ---------------------------------------------------------------------------
def _boom(*a, **k):
    raise AssertionError("network touched")


def test_production_not_live_and_no_dev_endpoint_means_no_network_and_no_connection(
    monkeypatch, tmp_path
):
    monkeypatch.delenv(hosted_ai.ENV_HARD_OFF, raising=False)
    monkeypatch.delenv(hosted_ai.ENV_ENDPOINT, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(diagsend, "_send", _boom)
    assert hosted_ai.PRODUCTION_LIVE is False
    assert hosted_ai.resolve_target() is None
    assert hosted_ai.available() is False
    assert hosted_ai.virtual_record() is None
    assert ai_connections._hosted_connection() is None
    assert ai_connections.vendors({}) == [] or all(
        v["id"] != hosted_ai.PROVIDER_ID for v in ai_connections.vendors({})
    )
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("figure_edit")
    assert e.value.code == "disabled"
    lease = hosted_ai.lease("fix")
    assert not lease.ok and lease.error.code == "disabled"
    lease.close()  # 没有任务：关也是零网络


def test_production_live_pins_the_production_host(monkeypatch):
    monkeypatch.delenv(hosted_ai.ENV_HARD_OFF, raising=False)
    monkeypatch.delenv(hosted_ai.ENV_ENDPOINT, raising=False)
    monkeypatch.setattr(hosted_ai, "PRODUCTION_LIVE", True)
    t = hosted_ai.resolve_target()
    assert t is not None and t.host == "ai.tavotto.cn" and t.base == "https://ai.tavotto.cn"
    assert hosted_ai.HOSTED_HOSTS == frozenset({"ai.tavotto.cn"})


@pytest.mark.parametrize(
    "raw",
    [
        "https://evil.example",
        "https://ai.tavotto.cn.evil.example",
        "http://ai.tavotto.cn",
        "https://ai.tavotto.cn/v1",
        "https://user@ai.tavotto.cn",
        "https://license.tavotto.cn",  # 别的高级版服务的主机不是网关
        "http://127.0.0.1:18200",  # 回环没开发模式不认
    ],
)
def test_a_bad_explicit_endpoint_turns_the_feature_off_and_never_falls_back(monkeypatch, raw):
    monkeypatch.delenv(hosted_ai.ENV_HARD_OFF, raising=False)
    monkeypatch.delenv(diagsend.ENV_DEV_LOOPBACK, raising=False)
    monkeypatch.setattr(hosted_ai, "PRODUCTION_LIVE", True)  # 即使正式开关开着也不回退内置端点
    monkeypatch.setenv(hosted_ai.ENV_ENDPOINT, raw)
    assert hosted_ai.resolve_target() is None


def test_hard_off_switch(gw, monkeypatch):
    monkeypatch.setenv(hosted_ai.ENV_HARD_OFF, "1")
    assert hosted_ai.available() is False and hosted_ai.resolve_target() is None
    assert hosted_ai.virtual_record() is None
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("fix")
    assert e.value.code == "disabled" and gw.calls == []


def test_without_a_valid_license_there_is_no_connection_and_no_request(gw, monkeypatch):
    lic._clear()
    assert hosted_ai.available() is False
    assert ai_connections._hosted_connection() is None
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("figure_edit")
    assert e.value.code == "not_entitled" and gw.calls == []


def test_a_license_without_the_feature_does_not_unlock_it(gw, monkeypatch):
    signing = gw.license_signing
    key = lic._signing_key(create=True)
    lic._save(mint_token(signing, _b64(bytes(key.verify_key)), ["review-plus"]))
    assert hosted_ai.available() is False
    assert ai_connections._hosted_connection() is None


# ---------------------------------------------------------------------------
# 开任务：设备签名、授权令牌、回包
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", hosted_ai.KINDS)
def test_open_task_signs_the_request_with_the_device_key(gw, kind):
    g = hosted_ai.open_task(kind)
    (call,) = gw.calls
    assert call["path"] == "/v1/task" and call["body"]["kind"] == kind
    assert set(call["body"]) == {"op", "token", "device_pub", "kind", "client", "ts", "sig"}
    assert g.task_id == "tsk_abc123" and g.task_token == TASK_TOKEN
    assert g.ceiling_micro == 100_000 and g.remaining == 0.63
    assert g.limits.as_dict() == LIMITS
    assert g.base_url == GATEWAY and g.model == "tavotto-auto"


def test_a_wrong_signature_would_be_caught_by_the_fake_gateway(gw):
    """反证：假网关的验签不是摆设（篡改签名的请求会被它拒）。"""
    body = lic.sign_op("ai_task", {"token": {"a": "b"}, "kind": "fix", "client": "x"})
    body["kind"] = "figure_edit"  # 签名之后改了内容
    with pytest.raises(Exception):  # noqa: B017 — nacl.exceptions.BadSignatureError
        gw._verify_device_signature(body)


def test_unknown_kind_is_a_client_error_with_no_network(gw):
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("everything")
    assert e.value.code == "client_error" and gw.calls == []


@pytest.mark.parametrize(
    ("status", "code", "extra", "expect", "retry"),
    [
        (402, "quota_exhausted", {"retry_at": TOMORROW_MS}, "quota_exhausted", TOMORROW_MS),
        (402, "daily_limit", {"retry_at": TOMORROW_MS}, "daily_limit", TOMORROW_MS),
        (403, "not_entitled", {}, "not_entitled", None),
        (403, "kind_disabled", {}, "kind_disabled", None),
        (503, "paused", {}, "paused", None),
        (400, "client_error", {}, "client_error", None),
        (401, "client_error", {}, "client_error", None),
        (429, "rate_limited", {}, "rate_limited", None),
        (500, "server", {}, "server", None),
        (502, "server", {}, "server", None),
        # 不认识的码：按状态码兜底，永远落进闭集
        (503, "something_new", {}, "paused", None),
        (500, "something_new", {}, "server", None),
        (404, "", {}, "server", None),
        (418, "teapot", {}, "client_error", None),
        (402, "", {}, "client_error", None),
        # 客户端自己判的码，服务端发了也不认
        (500, "network", {}, "server", None),
        (500, "disabled", {}, "server", None),
    ],
)
def test_every_closed_error_code_maps_to_a_typed_failure(gw, status, code, extra, expect, retry):
    gw.script.append(err(status, code, **extra))
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("figure_edit")
    assert e.value.code == expect and e.value.code in hosted_ai.FAILURES
    assert e.value.retry_at == retry
    assert e.value.public() == {"reason": expect, "retry_at": retry}


def test_retry_after_header_becomes_retry_at_and_a_past_retry_at_is_dropped(gw):
    before = int(time.time() * 1000)
    gw.script.append((429, {"error": {"code": "rate_limited"}}, {"retry-after": "30"}))
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("fix")
    assert before + 29_000 < e.value.retry_at < before + 40_000
    gw.script.append(err(402, "quota_exhausted", retry_at=1000))  # 过去的时刻：不信
    with pytest.raises(hosted_ai.HostedError) as e2:
        hosted_ai.open_task("fix")
    assert e2.value.retry_at is None


def test_network_failures_are_typed_not_raised_raw(gw):
    for fail in ("network", "timeout", "tls"):
        gw.script.append((diagsend._Failure(fail), None, None))
        with pytest.raises(hosted_ai.HostedError) as e:
            hosted_ai.open_task("figure_edit")
        assert e.value.code == "network"
    gw.script.append((RuntimeError("boom"), None, None))  # 意外异常也不外泄
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("figure_edit")
    assert e.value.code == "client_error"


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"task_id": "t", "task_token": "short", "ceiling_micro": 1, "limits": LIMITS},
        {"task_id": "t", "task_token": TASK_TOKEN, "ceiling_micro": -1, "limits": LIMITS},
        {"task_id": "t", "task_token": TASK_TOKEN, "ceiling_micro": 1, "limits": {"max_turns": 1}},
        {
            "task_id": "t",
            "task_token": TASK_TOKEN,
            "ceiling_micro": 1,
            "limits": {**LIMITS, "max_turns": 0},
        },
        {
            "task_id": "t",
            "task_token": TASK_TOKEN + " x",  # 通行证里不许有空白（会进 HTTP 头）
            "ceiling_micro": 1,
            "limits": LIMITS,
        },
    ],
)
def test_a_malformed_grant_is_a_server_failure_not_a_guess(gw, bad):
    gw.script.append((200, bad, {}))
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("figure_edit")
    assert e.value.code == "server"


def test_non_json_ok_response_is_a_server_failure(gw):
    gw.script.append((200, "x", {}))  # 体是 JSON 但不是对象
    with pytest.raises(hosted_ai.HostedError) as e:
        hosted_ai.open_task("fix")
    assert e.value.code == "server"


def test_limits_from_the_gateway_are_clamped_to_sane_caps(gw):
    gw.limits = {
        "max_turns": 10**9,
        "max_total_tokens": 10**12,
        "max_output_tokens": 10**9,
    }
    g = hosted_ai.open_task("convert")
    assert g.limits.max_turns <= 200
    assert g.limits.max_total_tokens <= 5_000_000
    assert g.limits.max_output_tokens <= 65_536


# ---------------------------------------------------------------------------
# 关任务与 Lease
# ---------------------------------------------------------------------------
def test_close_task_posts_with_the_bearer_pass_once_and_is_idempotent(gw):
    lease = hosted_ai.lease("figure_edit")
    assert lease.ok
    lease.close()
    lease.close()
    lease.close()
    assert gw.paths() == ["/v1/task", "/v1/task/close"]
    assert gw.calls[1]["auth"] == f"Bearer {TASK_TOKEN}"
    assert gw.calls[1]["body"] is None  # 空体


def test_close_failure_is_swallowed(gw):
    lease = hosted_ai.lease("fix")
    gw.script.append((diagsend._Failure("network"), None, None))
    lease.close()  # 不抛
    gw.script.append((500, {"error": {"code": "server"}}, {}))
    hosted_ai.close_task(lease.grant)  # 不抛


def test_a_failed_lease_has_nothing_to_close(gw):
    gw.script.append(err(402, "quota_exhausted"))
    lease = hosted_ai.lease("fix")
    assert not lease.ok and lease.record() is None and lease.conn() is None
    lease.close()
    assert gw.paths() == ["/v1/task"]


def test_task_tokens_are_never_logged_or_repr_ed(gw, caplog):
    caplog.set_level(logging.DEBUG)
    lease = hosted_ai.lease("figure_edit")
    gw.script.append(err(403, "not_entitled"))
    hosted_ai.lease("fix")
    lease.close()
    everything = caplog.text + repr(lease.grant) + repr(lease) + str(lease.error)
    assert TASK_TOKEN not in everything and "CANARY" not in everything
    # 日志模板是字面量：只有任务类型与闭集结果码
    assert any("内置 AI: 开任务 figure_edit ok" in r.getMessage() for r in caplog.records)
    assert any("内置 AI: 开任务 fix not_entitled" in r.getMessage() for r in caplog.records)
    # 万一进了文本（日志 / 诊断包 / 报错）：按形状抹掉
    from tavotto.engine import diagnostictext

    assert TASK_TOKEN not in diagnostictext.redact_text(f"x {TASK_TOKEN} y")
    # 令牌不写盘
    data = config.data_dir()
    leaked = [p for p in data.rglob("*") if p.is_file() and TASK_TOKEN.encode() in p.read_bytes()]
    assert leaked == []


# ---------------------------------------------------------------------------
# 虚拟连接：只在可用时出现、不落盘、没有用户 Key、选中后用内置循环
# ---------------------------------------------------------------------------
def test_the_connection_appears_when_available_and_is_never_persisted(gw):
    conns = ai_connections.connections({})
    mine = [c for c in conns if c["id"] == hosted_ai.PROVIDER_ID]
    assert len(mine) == 1
    c = mine[0]
    assert c["route"] == "builtin" and c["state"] == "ready" and c["hosted"] is True
    assert c["models"] == ["tavotto-auto"] and c["efforts"] == []
    assert c["label"] == f"{__import__('tavotto.engine.brand', fromlist=['x']).PRODUCT_NAME} Agent"
    assert "api_key" not in c and "has_key" not in c
    vendors = [v for v in ai_connections.vendors({}) if v["id"] == hosted_ai.PROVIDER_ID]
    assert len(vendors) == 1
    assert vendors[0]["methods"][0]["kind"] == "hosted"
    assert vendors[0]["methods"][0]["connection_id"] == hosted_ai.PROVIDER_ID
    # 从不写进 config.json：列表、解析、开 / 关任务之后配置里都没有它
    ai_providers.lookup(hosted_ai.PROVIDER_ID)
    hosted_ai.lease("figure_edit").close()
    cfg = config.config_dir() / "config.json"
    assert not cfg.exists() or hosted_ai.PROVIDER_ID not in cfg.read_text(encoding="utf-8")
    assert ai_providers.list_providers() == []
    assert ai_providers.get(hosted_ai.PROVIDER_ID) is None  # 写入路径永远看不见它


def test_the_provider_id_is_reserved_for_user_connections(gw):
    rec = ai_providers.create(
        {
            "id": hosted_ai.PROVIDER_ID,
            "label": "x",
            "agent": "claude",
            "base_url": "https://api.example.com",
            "api_key": "sk-test-1234567890",
            "default_model": "m",
        }
    )
    assert rec["id"] != hosted_ai.PROVIDER_ID
    assert (
        ai_providers.lookup(hosted_ai.PROVIDER_ID)["hosted"] is True
    )  # 保留 id 解析到虚拟连接，不是用户的那条


def test_forced_builtin_off_hides_it(gw, monkeypatch):
    monkeypatch.setenv(ai_connections.ENV_BUILTIN, "0")
    assert ai_connections._hosted_connection() is None
    rec = hosted_ai.virtual_record()
    assert ai_connections.route_for(rec, {}) == (None, "builtin_unavailable")


def test_resolve_connection_routes_it_to_the_builtin_runner_never_a_cli(gw):
    agent, rec = ai_connections.resolve_connection(hosted_ai.PROVIDER_ID, {})
    assert agent == "builtin" and rec["id"] == hosted_ai.PROVIDER_ID
    # 即使「首选 CLI」的偏好打开、CLI 可用，也不会路由到 CLI（通行证 CLI 注入不了）
    assert ai_connections.route_for(
        rec, {"claude": {"usable": True, "installed": True, "enabled": True}}
    ) == (
        "builtin",
        None,
    )


def test_resolve_connection_refuses_when_unavailable(gw, monkeypatch):
    lic._clear()
    with pytest.raises(ai_connections.ConnectionRefused) as e:
        ai_connections.resolve_connection(hosted_ai.PROVIDER_ID, {})
    assert e.value.code == "connection_not_found"


def test_builtin_usable_with_only_the_hosted_connection(gw):
    assert ai_connections.first_key_connection() is None  # 没有任何自己的 Key
    assert ai_connections.builtin_usable() is True
    lic._clear()
    assert ai_connections.builtin_usable() is False


def test_first_key_connection_never_picks_the_hosted_one_it_is_the_callers_first_choice(gw):
    """`first_key_connection` 只管自己的 Key；内置 AI 在隐式路径（修复 / 数据出图）里由调用方放在最前，开不成时退到这里。"""
    assert ai_connections.first_key_connection(refresh=True) is None
    ai_providers.save(
        {
            "id": "mine",
            "label": "mine",
            "agent": "claude",
            "base_url": "https://api.example.com",
            "api_key": "sk-test-1234567890",
            "default_model": "m1",
        }
    )
    conn = ai_connections.first_key_connection()
    assert conn is not None and conn["provider_id"] == "mine" and "hosted" not in conn
    # 隐式路径里「Tavotto Agent」排第一（维护者 2026-11-21）：占位连接，真发请求前开任务；开不成静默退到自己的 Key
    conn2, why, _ = ai_structured.resolve_connection(0.0)
    assert hosted_ai.needs_task(conn2) and why == ""
    assert ai_structured.fallback_connection()["provider_id"] == "mine"


# ---------------------------------------------------------------------------
# 限制下发与注入：通行证只进环境变量，argv 里只有地址 / 型号 / 上限
# ---------------------------------------------------------------------------
def test_grant_limits_and_pass_are_injected_for_the_builtin_runner(gw):
    lease = hosted_ai.lease("figure_edit")
    rec = lease.record()
    args, env = ai_providers.spawn_overrides("builtin", rec, "some-other-model", None)
    assert args[:6] == ["--wire", "anthropic", "--base-url", GATEWAY, "--model", "tavotto-auto"]
    flags = dict(zip(args[6::2], args[7::2], strict=False))
    assert flags["--max-turns"] == "24"
    assert flags["--max-total-tokens"] == "200000"
    assert flags["--max-output-tokens"] == "8192"
    assert "--effort" not in args and "--thinking" not in args  # 第三方网关一律没有强度字段
    assert env == {"TAVOTTO_AGENT_API_KEY": TASK_TOKEN}
    assert TASK_TOKEN not in " ".join(args)
    # 虚拟（未开任务）记录只是占位：不带通行证、不带上限
    conn = ai_providers.builtin_conn(hosted_ai.virtual_record())
    assert conn["api_key"] == "" and hosted_ai.needs_task(conn) and "limits" not in conn


def test_the_runner_cli_accepts_the_limit_flags():
    from tavotto.engine.agent_runner import runner_cli

    ns = runner_cli._parser().parse_args(
        ["--cwd", ".", "--target", "f.py", "--wire", "anthropic", "--max-output-tokens", "123"]
    )
    assert ns.max_output_tokens == 123


# ---------------------------------------------------------------------------
# 402 task_budget_exhausted → 现有的预算用完结局；别的 402 仍是额度
# ---------------------------------------------------------------------------
def test_task_budget_exhausted_ends_through_the_existing_budget_outcome():
    f = loop.classify_status(402, b'{"error":{"code":"task_budget_exhausted","message":"x"}}')
    assert (f.reason, f.detail) == ("budget", "tokens")
    for body in (
        b"",
        b"{}",
        b'{"error":{"code":"quota_exhausted"}}',
        b"not json",
        b'{"error":"x"}',
    ):
        g = loop.classify_status(402, body)
        assert (g.reason, g.detail) == ("quota", "http_402")
    # 401 task_expired = 通行证没了：沿用鉴权失败（界面对这条连接给「改用我自己的 key」）
    assert loop.classify_status(401, b'{"error":{"code":"task_expired"}}').reason == "auth"
    # 503 paused = 可重试的网络类
    assert loop.classify_status(503, b'{"error":{"code":"paused"}}').retryable is True


def test_structured_calls_map_it_to_over_budget():
    assert ai_structured.map_failure(wires.RunnerFailure("budget", "tokens")) == (
        "over_budget",
        "tokens",
    )


def _anthropic_body(text: str = "ok") -> bytes:
    return json.dumps(
        {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 50},
        }
    ).encode()


def _model(post):
    return loop.HttpModel(
        wires.AnthropicWire(),
        base_url=GATEWAY,
        api_key=TASK_TOKEN,
        model="tavotto-auto",
        post=post,
        max_attempts=1,
        retry_base=0.0,
    )


def _complete(model):
    import threading

    return model.complete(
        system="s",
        tools=[],
        history=[wires.Msg("user", "hi")],
        max_output_tokens=100,
        cancel=threading.Event(),
        deadline=time.monotonic() + 30,
    )


def test_a_402_from_the_gateway_ends_the_runner_as_budget_not_a_new_error():
    def post(url, headers, body, *, timeout, max_bytes=None):
        return 402, {}, b'{"error":{"code":"task_budget_exhausted","message":"x"}}'

    with pytest.raises(wires.RunnerFailure) as e:
        _complete(_model(post))
    assert (e.value.reason, e.value.detail) == ("budget", "tokens")


# ---------------------------------------------------------------------------
# X-Tavotto-Charged：权威成本（人民币微元）
# ---------------------------------------------------------------------------
def test_charged_header_is_read_into_the_turn_usage_and_the_usage_event_shape():
    seen = {}

    def post(url, headers, body, *, timeout, max_bytes=None):
        seen["url"], seen["headers"] = url, headers
        return 200, {"x-tavotto-charged": "1234", "x-tavotto-remaining": "0.5"}, _anthropic_body()

    turn = _complete(_model(post))
    assert turn.usage.charged_micro == 1234
    assert seen["url"] == GATEWAY + "/v1/messages"
    assert seen["headers"]["x-api-key"] == TASK_TOKEN  # 通行证当 API key
    assert loop._charged_field(turn.usage) == {"charged_micro": 1234}


@pytest.mark.parametrize("raw", ["", "abc", "-5", "1.5", "9" * 20, "12 34"])
def test_a_garbage_charged_header_is_ignored(raw):
    def post(url, headers, body, *, timeout, max_bytes=None):
        return 200, {"x-tavotto-charged": raw}, _anthropic_body()

    assert _complete(_model(post)).usage.charged_micro is None


def test_gateway_cost_is_authoritative_cny_only_for_the_hosted_connection():
    event = {
        "type": "usage",
        "input_tokens": 100,
        "output_tokens": 10,
        "cached_input_tokens": 50,
        "reported": True,
        "charged_micro": 1234,
    }
    hosted_rec = {"id": hosted_ai.PROVIDER_ID, "hosted": True}
    u = ai_pricing.usage_with_cost(event, hosted_rec, "tavotto-auto")
    assert u["cost"] == {
        "amount": 0.001234,
        "currency": "CNY",
        "pricing_version": "gateway",
        "authoritative": True,
    }
    projected = exectrace.project_usage(u)
    assert projected["cost"] == {
        "amount": 0.001234,
        "currency": "CNY",
        "pricing_version": "gateway",
        "estimated": False,
    }
    assert projected["source"] == "reported" and projected["input_tokens"] == 100
    # 第三方端点自己发这个头（或事件里带这个字段）一律不算数：没有价格表就是 None，不是 0
    other = {"id": "x", "base_url": "https://api.example.com"}
    assert "cost" not in ai_pricing.usage_with_cost(event, other, "m")
    assert "cost" not in ai_pricing.usage_with_cost(event, None, None)
    # 价格表估算的仍是 estimated=True（不被权威口径污染）
    est = ai_pricing.usage_with_cost(
        {**event, "charged_micro": None},
        {"base_url": "https://api.anthropic.com"},
        "claude-sonnet-5-5",
    )
    assert exectrace.project_usage(est)["cost"]["estimated"] is True


def test_gateway_cost_without_reported_usage_still_counts_and_attributes_are_closed():
    ev = {
        "type": "usage",
        "input_tokens": 5,
        "output_tokens": 5,
        "reported": False,
        "charged_micro": 7,
    }
    u = ai_pricing.usage_with_cost(ev, {"id": hosted_ai.PROVIDER_ID, "hosted": True}, None)
    assert u["source"] == "estimated" and u["cost"]["amount"] == 0.000007
    attrs, rejected = exectrace.project_attributes(
        ai_pricing.model_attributes({"id": hosted_ai.PROVIDER_ID, "hosted": True}, "tavotto-auto")
    )
    assert attrs == {"provider": "tavotto-agent", "model": "tavotto-auto"} and rejected == 0
    assert {"USD", "CNY"} == exectrace.vocabulary("currency")


# ---------------------------------------------------------------------------
# 路径：数据出图（ai_structured）——开任务 / 通行证 / 上限 / 关任务
# ---------------------------------------------------------------------------
class FakePost:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers, body, *, timeout, max_bytes=None):
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.replies.pop(0)


def _ok_reply(text='{"answer": 1}', charged="900"):
    return 200, {"x-tavotto-charged": charged}, _anthropic_body(text)


def _validate(obj):
    if obj.get("answer") != 1:
        raise ValueError
    return obj


def test_data_roles_opens_one_task_uses_the_pass_clamps_output_and_closes(gw):
    post = FakePost([_ok_reply()])
    res = ai_structured.call_structured(system="S", user="U", validate=_validate, post=post)
    assert res.ok and res.value == {"answer": 1}
    assert gw.kinds() == ["data_roles"] and gw.paths() == ["/v1/task", "/v1/task/close"]
    (c,) = post.calls
    assert c["url"] == GATEWAY + "/v1/messages"
    assert c["headers"]["x-api-key"] == TASK_TOKEN and c["body"]["model"] == "tavotto-auto"
    assert c["body"]["max_tokens"] <= LIMITS["max_output_tokens"]
    assert res.cost_usd is None  # 成本以网关的人民币结算为准（执行追踪另记），不拿美元价目表估算


def test_data_roles_failure_to_open_is_a_quiet_closed_reason_with_no_model_request(gw):
    post = FakePost([])
    for status, code, reason in (
        (402, "quota_exhausted", "quota"),
        (402, "daily_limit", "quota"),
        (403, "not_entitled", "auth"),
        (503, "paused", "transport"),
    ):
        gw.script.append(err(status, code))
        res = ai_structured.call_structured(system="S", user="U", validate=_validate, post=post)
        assert not res.ok and res.reason == reason and res.reason in ai_structured.REASONS
    gw.script.append((diagsend._Failure("network"), None, None))
    res = ai_structured.call_structured(system="S", user="U", validate=_validate, post=post)
    assert not res.ok and res.reason == "transport"
    assert post.calls == []
    assert "/v1/task/close" not in gw.paths()  # 没开成就没有什么要关


def test_data_roles_closes_the_task_on_failure_too(gw):
    post = FakePost([(500, {}, b"{}")])
    res = ai_structured.call_structured(system="S", user="U", validate=_validate, post=post)
    assert not res.ok
    assert gw.paths()[-1] == "/v1/task/close"


def test_data_roles_task_budget_exhausted_is_over_budget(gw):
    post = FakePost([(402, {}, b'{"error":{"code":"task_budget_exhausted"}}')])
    res = ai_structured.call_structured(system="S", user="U", validate=_validate, post=post)
    assert not res.ok and res.reason == "over_budget"


def test_a_judge_shares_one_task_between_its_two_requests(gw, monkeypatch):
    """表头那问 + 列角色那问共用一个任务（一个放行、一次结算），结束就关；注入的 `call` 拿到的是已换上通行证的连接。"""
    from tavotto.engine import ai_data_roles

    convs = []

    def fake_call(**kw):
        convs.append(kw.get("conn"))
        return ai_structured.StructuredResult(False, reason="invalid_output", detail="x")

    monkeypatch.setattr(ai_data_roles, "build_request", lambda *a, **k: ("S", "U"))
    monkeypatch.setattr(ai_structured, "call_structured", fake_call)
    out = ai_data_roles.judge(object(), object(), None, data_path=None)
    assert out == ("failed", "invalid_output")
    assert gw.kinds() == ["data_roles"] and gw.paths()[-1] == "/v1/task/close"
    assert [c["api_key"] for c in convs] == [TASK_TOKEN] and convs[0]["limits"] == LIMITS


def test_a_judge_with_a_failed_task_never_calls_the_model(gw, monkeypatch):
    from tavotto.engine import ai_data_roles

    def fake_call(**kw):
        raise AssertionError("model touched")

    monkeypatch.setattr(ai_structured, "call_structured", fake_call)
    gw.script.append(err(402, "quota_exhausted"))
    assert ai_data_roles.judge(object(), object(), None, data_path=None) == ("failed", "quota")


# ---------------------------------------------------------------------------
# 路径：AI 修复（fixsession 进程内 HttpModel）
# ---------------------------------------------------------------------------
def test_fix_limits_follow_the_grant_but_never_exceed_the_default_budget(gw):
    lease = hosted_ai.lease("fix")
    lim = fixsession.hosted_limits(lease)
    assert lim.max_turns == min(fixsession.FIX_LIMITS.max_turns, LIMITS["max_turns"])
    assert lim.max_total_tokens == min(
        fixsession.FIX_LIMITS.max_total_tokens, LIMITS["max_total_tokens"]
    )
    assert lim.max_output_tokens == min(
        fixsession.FIX_LIMITS.max_output_tokens, LIMITS["max_output_tokens"]
    )
    assert lim.max_seconds == fixsession.FIX_LIMITS.max_seconds
    gw.limits = {"max_turns": 5, "max_total_tokens": 1000, "max_output_tokens": 100}
    tight = fixsession.hosted_limits(hosted_ai.lease("fix"))
    assert (tight.max_turns, tight.max_total_tokens, tight.max_output_tokens) == (5, 1000, 100)
    conn = lease.conn()
    model, redactor = fixsession.build_model(conn)
    assert model.api_key if hasattr(model, "api_key") else True
    assert TASK_TOKEN not in repr(model)
    assert redactor.redact(f"x {TASK_TOKEN} y") != f"x {TASK_TOKEN} y"  # 通行证也在脱敏范围内


def test_fix_available_counts_the_hosted_connection_as_the_last_resort(gw, monkeypatch):
    monkeypatch.setattr(fixsession, "cli_agent", lambda: None)
    assert fixsession.connection() is None
    assert fixsession.available() is True
    lic._clear()
    assert fixsession.available() is False


def test_fix_session_runs_the_close_hook_on_every_ending(tmp_path, monkeypatch):
    """收尾钩子在任何结局（完成 / 取消 / 失败）都只调一次；钩子自己抛也不影响收尾。"""
    from tavotto.engine.agent_runner.tools import Limits  # noqa: F401

    fixsession.SESSIONS.reset_for_tests()
    calls: list[str] = []

    class Quit:
        def complete(self, **_kw):
            return wires.Turn(text="done", stop="end")

    s = fixsession.SESSIONS.start(
        scope="p1",
        prep_session_id="ps1",
        script="fig.py",
        project_root=tmp_path,
        model=Quit(),
        background=False,
        on_finish=lambda: calls.append("closed"),
    )
    assert s.state == fixsession.STATE_FINISHED and calls == ["closed"]
    fixsession.SESSIONS._settle(s, "again")  # 再收尾一次不再调
    assert calls == ["closed"]
    fixsession.SESSIONS.reset_for_tests()

    def bad():
        raise RuntimeError("boom")

    s2 = fixsession.SESSIONS.start(
        scope="p1",
        prep_session_id="ps2",
        script="fig.py",
        project_root=tmp_path,
        model=Quit(),
        background=False,
        on_finish=bad,
    )
    assert s2.state == fixsession.STATE_FINISHED
    fixsession.SESSIONS.reset_for_tests()


# ---------------------------------------------------------------------------
# 路径：改图 / 转换（ai_bridge.run → 内置执行器子进程）
# ---------------------------------------------------------------------------
class FakeProc:
    """顶替内置执行器子进程：读一行提示词、吐一个 `done`，记下它被怎么启动。"""

    def __init__(self, cmd, env, lines):
        self.cmd, self.env = cmd, dict(env)
        self.pid = None  # 绝不是 0：`_terminate` 会 `os.killpg(pid)`，0 = 杀自己的进程组；非 int = 只走 kill()
        self.returncode = 0
        import io

        self.stdout = io.StringIO("".join(json.dumps(x) + "\n" for x in lines))

        class _In:
            def write(self, *_a):
                return 0

            def flush(self):
                pass

            def close(self):
                pass

        self.stdin = _In()

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass

    terminate = kill


@pytest.fixture
def run_env(gw, monkeypatch, tmp_path):
    from tavotto.engine import ai_bridge

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig.py").write_text(
        "import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('fig.pdf')\n"
    )
    procs: list[FakeProc] = []
    script = {"lines": [{"type": "done", "ok": True, "reason": "completed"}]}

    def fake_popen(cmd, cwd, env, stderr_log, stdin_text=None, keep_stdin_open=False):
        p = FakeProc(cmd, env, script["lines"])
        procs.append(p)
        return p

    monkeypatch.setattr(ai_bridge, "_popen", fake_popen)
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snaps")
    return figs, procs, script


def _wait_closed(gw, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if "/v1/task/close" in gw.paths():
            return True
        time.sleep(0.02)
    return False


def test_figure_edit_opens_a_task_injects_the_pass_and_limits_and_closes_when_done(gw, run_env):
    from tavotto.engine import ai_bridge

    figs, procs, _ = run_env
    done = []
    sid = ai_bridge.run(
        "builtin",
        "fig.py",
        "make it blue",
        str(figs),
        on_event=lambda name, data: done.append(name),
        endpoint_id=hosted_ai.PROVIDER_ID,
        model="tavotto-auto",
        client_request_id="rid-hosted-0001",
    )
    assert sid and _wait_closed(gw)
    assert gw.kinds() == ["figure_edit"]
    (p,) = procs
    assert p.env["TAVOTTO_AGENT_API_KEY"] == TASK_TOKEN  # 通行证只在子进程环境变量里
    assert TASK_TOKEN not in " ".join(p.cmd)
    assert "--max-turns" in p.cmd and p.cmd[p.cmd.index("--max-total-tokens") + 1] == "200000"
    assert p.cmd[p.cmd.index("--max-output-tokens") + 1] == "8192"
    assert p.cmd[p.cmd.index("--base-url") + 1] == GATEWAY
    assert p.cmd[p.cmd.index("--model") + 1] == "tavotto-auto"
    assert gw.paths() == ["/v1/task", "/v1/task/close"]


def test_convert_opens_a_convert_task_with_its_own_limits_and_closes_it(gw, run_env):
    from tavotto.engine import ai_bridge

    figs, procs, _ = run_env
    gw.limits = {"max_turns": 32, "max_total_tokens": 600_000, "max_output_tokens": 16_384}
    ai_bridge.run(
        "builtin",
        "fig.py",
        "convert",
        str(figs),
        endpoint_id=hosted_ai.PROVIDER_ID,
        convert={"library": "plotly", "original_rel": None, "source_language": "python"},
        client_request_id="rid-hosted-0002",
    )
    assert _wait_closed(gw)
    assert gw.kinds() == ["convert"]
    p = procs[0]
    assert p.cmd[p.cmd.index("--task") + 1] == "convert_to_editable"
    assert p.cmd[p.cmd.index("--max-output-tokens") + 1] == "16384"
    # 校验没过会有「有界修正」再起一次 runner：它复用同一个任务（同一张通行证 / 同一组上限），不会再开第二个
    assert {q.env["TAVOTTO_AGENT_API_KEY"] for q in procs} == {TASK_TOKEN}
    assert gw.paths() == ["/v1/task", "/v1/task/close"]


def test_a_run_that_cannot_start_closes_the_task(gw, run_env):
    from tavotto.engine import ai_bridge

    figs, _procs, _ = run_env
    with pytest.raises(RuntimeError):  # 脚本不存在：脚本锁里的前置检查失败
        ai_bridge.run(
            "builtin",
            "missing.py",
            "x",
            str(figs),
            endpoint_id=hosted_ai.PROVIDER_ID,
            client_request_id="rid-hosted-0003",
        )
    assert gw.paths() == ["/v1/task", "/v1/task/close"]


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (402, "quota_exhausted", "quota_exhausted"),
        (402, "daily_limit", "daily_limit"),
        (403, "not_entitled", "not_entitled"),
        (503, "paused", "paused"),
    ],
)
def test_open_failure_is_a_quiet_typed_refusal_before_any_spawn(gw, run_env, status, code, reason):
    from tavotto.engine import ai_bridge

    figs, procs, _ = run_env
    gw.script.append(err(status, code, retry_at=TOMORROW_MS))
    with pytest.raises(ai_bridge.AgentError) as e:
        ai_bridge.run(
            "builtin",
            "fig.py",
            "x",
            str(figs),
            endpoint_id=hosted_ai.PROVIDER_ID,
            client_request_id=f"rid-hosted-{code[:4]}",
        )
    assert e.value.code == "ai_hosted_unavailable"
    assert e.value.params["reason"] == reason
    assert procs == [] and "/v1/task/close" not in gw.paths()


def test_a_resend_of_an_accepted_request_opens_no_second_task(gw, run_env):
    from tavotto.engine import ai_bridge

    figs, procs, _ = run_env
    kw = {"endpoint_id": hosted_ai.PROVIDER_ID, "client_request_id": "rid-hosted-dup1"}
    ai_bridge.run("builtin", "fig.py", "x", str(figs), **kw)
    assert _wait_closed(gw)
    n = gw.kinds()
    with pytest.raises(ai_bridge.DuplicateRequest):
        ai_bridge.run("builtin", "fig.py", "x", str(figs), **kw)
    assert gw.kinds() == n and len(procs) == 1


def test_own_key_and_subscription_runs_never_touch_the_gateway(gw, run_env, monkeypatch):
    from tavotto.engine import ai_bridge

    figs, procs, _ = run_env
    ai_providers.save(
        {
            "id": "mine",
            "label": "mine",
            "agent": "claude",
            "base_url": "https://api.example.com",
            "api_key": "sk-test-1234567890",
            "default_model": "m1",
        }
    )
    ai_bridge.run(
        "builtin", "fig.py", "x", str(figs), endpoint_id="mine", client_request_id="rid-own-00001"
    )
    time.sleep(0.5)
    assert gw.calls == []
    (p,) = procs
    assert p.env["TAVOTTO_AGENT_API_KEY"] == "sk-test-1234567890"
    assert "--max-turns" not in p.cmd  # 自带 Key 的上限完全照旧


def test_license_changes_refresh_the_capabilities_cache(gw, monkeypatch):
    """激活 / 刷新 / 解绑之后「Tavotto Agent」这条连接立刻出现或消失（能力表缓存被作废）。"""
    from tavotto import app as m
    from tavotto.engine import ai_bridge

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    ai_bridge.invalidate_capabilities()
    ids = [c["id"] for c in client.get("/api/ai/capabilities?refresh=1").get_json()["connections"]]
    assert hosted_ai.PROVIDER_ID in ids
    lic._clear()  # 令牌没了，但能力表还在缓存里
    cached = [c["id"] for c in client.get("/api/ai/capabilities").get_json()["connections"]]
    assert hosted_ai.PROVIDER_ID in cached
    client.post(
        "/api/license/release"
    )  # 没有令牌的解绑是零网络的空操作，但授权状态相关的入口都会作废缓存
    fresh = [c["id"] for c in client.get("/api/ai/capabilities").get_json()["connections"]]
    assert hosted_ai.PROVIDER_ID not in fresh
    ai_bridge.invalidate_capabilities()


def _own_key():
    ai_providers.save(
        {
            "id": "mine",
            "label": "mine",
            "agent": "claude",
            "base_url": "https://api.example.com",
            "api_key": "sk-test-1234567890",
            "default_model": "m1",
        }
    )


def test_data_roles_prefers_hosted_over_an_own_key_and_uses_the_pass(gw):
    _own_key()
    post = FakePost([_ok_reply()])
    res = ai_structured.call_structured(system="S", user="U", validate=_validate, post=post)
    assert res.ok and post.calls[0]["headers"]["x-api-key"] == TASK_TOKEN
    assert gw.kinds() == ["data_roles"]


@pytest.mark.parametrize("code", ["quota_exhausted", "daily_limit", "not_entitled", "paused"])
def test_data_roles_falls_back_silently_to_the_own_key_when_the_task_cannot_open(gw, code):
    _own_key()
    gw.script.append(err(402 if "quota" in code or "daily" in code else 403, code))
    post = FakePost([_ok_reply()])
    res = ai_structured.call_structured(system="S", user="U", validate=_validate, post=post)
    assert res.ok and post.calls[0]["headers"]["x-api-key"] == "sk-test-1234567890"
    assert "/v1/task/close" not in gw.paths()


def test_a_judge_falls_back_to_the_own_key_when_the_task_cannot_open(gw, monkeypatch):
    from tavotto.engine import ai_data_roles

    convs = []

    def fake_call(**kw):
        convs.append(kw.get("conn"))
        return ai_structured.StructuredResult(False, reason="invalid_output", detail="x")

    _own_key()
    monkeypatch.setattr(ai_data_roles, "build_request", lambda *a, **k: ("S", "U"))
    monkeypatch.setattr(ai_structured, "call_structured", fake_call)
    gw.script.append(err(402, "quota_exhausted"))
    ai_data_roles.judge(object(), object(), None, data_path=None)
    assert [c["provider_id"] for c in convs] == ["mine"]
