"""内置 AI「Tavotto Agent」的**引擎侧客户端**（ADR 0200 §四 / §八 / §十一，功能键 `hosted-ai`）：买了高级版的用户不用自己申请模型 Key，
AI 请求经我们的网关（`ai.tavotto.cn`）转给上游模型。网关与账本在 `Tavotto/infra`；契约唯一出处是 ADR 0200 §四之一 A，本模块只做客户端那半：

* **开任务**：`open_task(kind)` 带授权令牌 + 设备签名（`op = "ai_task"`，签名规则同 `license.py`，复用它的签名器）POST `/v1/task`，
  换回一张**任务通行证**（`tvk_task_…`，30 分钟无请求失效、只能花这一个任务的 `ceiling`）与这个任务的上限（轮数 / 总 token / 单次输出）。
  之后这个任务里的每个模型请求，内置执行器照常发（Anthropic Messages 线路，`wires.post_json` 不改），通行证当 API key、模型名固定 `tavotto-auto`。
* **关任务**：任务结束时 `close_task`（`Lease.close()`，幂等、尽力而为）；不调则网关 30 分钟空闲后自动关闭。
* **一条虚拟连接**：`PROVIDER_ID = "tavotto-agent"`。只在 `available()` 时出现在连接列表，**永不写进 config.json、没有用户 Key**；
  功能代码里没有任何 `has()` 判断（ADR 0200 §一）——判断只在这里（`available()`）。选了自己的 Key / 订阅登录的人完全不经过本模块。
* **失败不卡人**（§八）：额度用完 / 每日上限 / 未授权 / 熔断 / 网络不通，一律是一个 `HostedError`（闭集 `FAILURES` + 可选 `retry_at`），
  由调用方落成「改用我自己的 key」/「X 号再试」两条路的安静结果；任务中途超上限走现有的预算用完结局（`agent_runner.loop`，不是新错误）。

目的地由代码钉死（同 `license.py`）：`HOSTED_HOSTS = {ai.tavotto.cn}`；`TAVOTTO_HOSTED_AI_ENDPOINT` 设了就必须合规（或回环开发模式
`TAVOTTO_DIAG_DEV_LOOPBACK=1`），不合规整个功能关闭、**不回退内置端点**；`TAVOTTO_NO_HOSTED_AI=1` 整个功能不出现、不联网；
`PRODUCTION_LIVE = False` 时没设端点 = 入口不出现、零网络。控制面出站（开 / 关任务）只经 `diagsend._send`；网关地址只取自解析后的 `Target`。

**秘密不落地**：任务通行证只在内存里（`TaskGrant.task_token` 不进 `repr`），不写盘、不进日志 / 事件 / 诊断包；日志模板是字面量，参数只有闭集里的任务类型与结果码。

纯标准库，Flask 父进程 import 链上的模块。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.request
from dataclasses import dataclass, field

from . import brand, diagsend, license

LOG = logging.getLogger("tavotto.hosted_ai")

# ---------------------------------------------------------------------------
# 开关与端点
# ---------------------------------------------------------------------------
ENV_HARD_OFF = "TAVOTTO_NO_HOSTED_AI"
ENV_ENDPOINT = "TAVOTTO_HOSTED_AI_ENDPOINT"
HOSTED_HOSTS: frozenset[str] = frozenset({"ai.tavotto.cn"})
PRODUCTION_ENDPOINT = "https://ai.tavotto.cn"
#: 网关是否已在正式模式部署并验通（ADR 0200 §十一）。False 时只有显式设了 `TAVOTTO_HOSTED_AI_ENDPOINT`（开发 / 验收）才可用，
#: 正式安装里「Tavotto Agent」连接不出现、零网络。改成 True 要另开 PR、维护者本人点头。
PRODUCTION_LIVE = False

FEATURE = "hosted-ai"
#: 虚拟连接的 provider id（保留：`ai_providers._clean` 不许用户的连接占它）与网关认的固定型号名
PROVIDER_ID = "tavotto-agent"
MODEL = "tavotto-auto"
#: 任务类型闭集（ADR 0200 §一 / §四之一 A1）
KINDS: tuple[str, ...] = ("figure_edit", "fix", "data_roles", "convert")

#: 失败码闭集：网关的错误码（§四之一 A1）+ 客户端自己判的 `network`（连不上）/ `disabled`（功能没开）。
#: 值 = 用户**稍后**再点一次有没有可能成功（false = 要换办法：用自己的 Key）。
FAILURES: dict[str, bool] = {
    "quota_exhausted": True,
    "daily_limit": True,
    "not_entitled": False,
    "kind_disabled": True,
    "paused": True,
    "rate_limited": True,
    "network": True,
    "server": True,
    "client_error": False,
    "disabled": False,
}
LOG_RESULTS = frozenset(FAILURES) | {"ok"}

API_TIMEOUT_S = 15
CLOSE_TIMEOUT_S = 5
API_MAX_BYTES = 64 * 1024
#: 网关回的数不信任：夹在合理范围里（防坏回复把引擎的上限撑成无限）
_LIMIT_CAPS = {"max_turns": 200, "max_total_tokens": 5_000_000, "max_output_tokens": 65_536}
_TOKEN_RE = re.compile(
    r"[\x21-\x7e]{8,512}"
)  # 通行证：可打印 ASCII、无空白（会进 HTTP 头与环境变量）
_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_UA = "Tavotto-hosted-ai-client/1"
_MAX_RETRY_AHEAD_MS = 400 * 86_400_000


class HostedError(Exception):
    """开任务失败：`code` 是 `FAILURES` 的键；`retry_at` = 额度恢复 / 可再试的时刻（UTC 毫秒），网关没给 = None。"""

    def __init__(self, code: str, retry_at: int | None = None) -> None:
        super().__init__(code)
        self.code = code if code in FAILURES else "client_error"
        self.retry_at = retry_at

    def public(self) -> dict:
        """给界面的形状（只有闭集码与时刻，没有任何文字）。"""
        return {"reason": self.code, "retry_at": self.retry_at}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def _endpoint_override() -> str | None:
    raw = os.environ.get(ENV_ENDPOINT)
    return raw if raw is not None and raw.strip() else None


def resolve_target() -> diagsend.Target | None:
    """此刻允许连的网关；None = 功能关闭（入口不出现、不联网）。设了 `TAVOTTO_HOSTED_AI_ENDPOINT` 但不合规也是 None，不回退。"""
    if _flag(ENV_HARD_OFF):
        return None
    raw = _endpoint_override()
    if raw is not None:
        return license.parse_service_endpoint(raw, HOSTED_HOSTS)
    if not PRODUCTION_LIVE:
        return None
    return diagsend.parse_endpoint(PRODUCTION_ENDPOINT, dev_loopback=False, hosts=HOSTED_HOSTS)


def available() -> bool:
    """「Tavotto Agent」连接此刻出不出现：功能没被硬关、有可连的网关（正式开关 / 开发端点）、本机授权令牌有效且带 `hosted-ai`。
    本机判断、**不联网**（真正的权益判断在网关每次开任务时）。永不抛。"""
    try:
        return resolve_target() is not None and license.has(FEATURE)
    except Exception:  # noqa: BLE001 — 探测失败 = 没有这条连接
        return False


def label() -> str:
    """连接的显示名：由品牌常量拼出，不手写产品名（ADR 0200 §一）。"""
    return f"{brand.PRODUCT_NAME} Agent"


# ---------------------------------------------------------------------------
# 虚拟连接（provider 记录的形状；只在内存里）
# ---------------------------------------------------------------------------
def is_hosted(rec: object) -> bool:
    """这条 provider 记录是不是 Tavotto Agent 的虚拟连接（含已换上任务通行证的那份）。"""
    return isinstance(rec, dict) and rec.get("hosted") is True and rec.get("id") == PROVIDER_ID


def virtual_record() -> dict | None:
    """尚未开任务的虚拟连接记录（`ai_providers.resolve` / 连接视图用）；不可用 = None。**没有密钥**：真正的通行证在开任务之后才有（`Lease.record()`）。"""
    target = resolve_target() if available() else None
    if target is None:
        return None
    return {
        "id": PROVIDER_ID,
        "label": label(),
        "agent": "claude",
        "base_url": target.base,
        "api_key": "",
        # 型号清单与默认型号都留空：界面不出现内部型号名 `tavotto-auto`（连接只显示产品名）；真正发给网关的型号由 `conn_of` 固定
        "models": [],
        "default_model": "",
        "protocol": "anthropic",
        "hosted": True,
    }


def conn_of(rec: dict) -> dict | None:
    """虚拟连接记录 → `builtin_conn` 形状（`ai_providers.builtin_conn` 在认出它时调用）。未开任务的（没有通行证）= 占位：
    调用方真要发请求前必须先 `lease()`（`api_key` 为空，发出去只会得到 `auth`）。地址不合规 = None。"""
    if not is_hosted(rec):
        return None
    from .agent_runner.wires import check_base_url

    base = str(rec.get("base_url") or "")
    if check_base_url(base) is not None:
        return None
    conn = {
        "wire": "anthropic",
        "base_url": base,
        "api_key": str(rec.get("api_key") or ""),
        "model": MODEL,  # 型号固定：网关按配置换成上游模型，请求里写别的也没有意义
        "auth": "key",
        "provider_id": PROVIDER_ID,
        "hosted": True,
    }
    lim = rec.get("hosted_limits")
    if isinstance(lim, dict):
        conn["limits"] = {k: int(lim[k]) for k in _LIMIT_CAPS if isinstance(lim.get(k), int)}
    return conn


def needs_task(conn: object) -> bool:
    """这条连接是不是「还没开任务」的占位（没有通行证的 Tavotto Agent 连接）。"""
    return isinstance(conn, dict) and conn.get("hosted") is True and not conn.get("api_key")


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaskLimits:
    max_turns: int
    max_total_tokens: int
    max_output_tokens: int

    def as_dict(self) -> dict:
        return {
            "max_turns": self.max_turns,
            "max_total_tokens": self.max_total_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True)
class TaskGrant:
    """网关放行的一个任务。`task_token` 不进 `repr`（日志 / 断言失败信息里不会出现）。"""

    task_id: str
    task_token: str = field(repr=False)
    ceiling_micro: int
    limits: TaskLimits
    remaining: float | None
    base_url: str
    expires_idle_ms: int = 1_800_000
    model: str = MODEL


def _now_ms() -> int:
    return int(license._now() * 1000)


def _log(kind: str, result: str) -> None:
    kind = kind if kind in KINDS else "other"
    result = result if result in LOG_RESULTS else "client_error"
    LOG.info("内置 AI: 开任务 %s %s", kind, result)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _error_fields(body: bytes) -> tuple[str, int | None]:
    """错误体 `{"error": {"code", "message", "retry_at"?}}` → `(code, retry_at)`；别的形状不认。"""
    try:
        obj = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "", None
    err = obj.get("error") if isinstance(obj, dict) else None
    if not isinstance(err, dict):
        return "", None
    code = err.get("code")
    retry = err.get("retry_at")
    return (code[:64] if isinstance(code, str) else ""), (retry if _is_int(retry) else None)


def _sane_retry_at(ms: int | None, retry_after_s: int | None) -> int | None:
    now = _now_ms()
    if ms is None and retry_after_s is not None:
        ms = now + retry_after_s * 1000
    return ms if ms is not None and now < ms < now + _MAX_RETRY_AHEAD_MS else None


def _classify(status: int, code: str, retry_at: int | None) -> HostedError:
    if code in FAILURES and code not in ("network", "disabled"):
        return HostedError(code, retry_at)
    if status == 429:
        return HostedError("rate_limited", retry_at)
    if status == 503:
        return HostedError("paused", retry_at)
    if status >= 500 or status == 404:
        return HostedError("server")
    return HostedError("client_error")


def _request(target: diagsend.Target, path: str, data: bytes, headers: dict, timeout: float):
    req = urllib.request.Request(  # noqa: S310 — 地址由 Target 钉死：白名单主机的 https（开发模式才是回环）
        target.base + path,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
            **headers,
        },
    )
    return diagsend._send(req, timeout, API_MAX_BYTES)


def _client_version() -> str:
    return diagsend._version()


def _parse_grant(resp, base_url: str) -> TaskGrant:
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HostedError("server") from None
    if not isinstance(obj, dict):
        raise HostedError("server")
    task_id, token = obj.get("task_id"), obj.get("task_token")
    lim = obj.get("limits")
    ceiling = obj.get("ceiling_micro")
    if (
        not (isinstance(task_id, str) and _ID_RE.fullmatch(task_id))
        or not (isinstance(token, str) and _TOKEN_RE.fullmatch(token))
        or not isinstance(lim, dict)
        or not _is_int(ceiling)
        or ceiling < 0
    ):
        raise HostedError("server")  # 契约漂移：不猜，当网关出了问题
    limits = {}
    for key, cap in _LIMIT_CAPS.items():
        v = lim.get(key)
        if not _is_int(v) or v < 1:
            raise HostedError("server")
        limits[key] = min(v, cap)
    remaining = obj.get("remaining")
    rem = (
        float(remaining)
        if isinstance(remaining, int | float)
        and not isinstance(remaining, bool)
        and 0 <= remaining <= 1
        else None
    )
    idle = obj.get("expires_idle_ms")
    return TaskGrant(
        task_id=task_id,
        task_token=token,
        ceiling_micro=ceiling,
        limits=TaskLimits(**limits),
        remaining=rem,
        base_url=base_url,
        expires_idle_ms=idle if _is_int(idle) and idle > 0 else 1_800_000,
    )


def open_task(kind: str) -> TaskGrant:
    """开一个任务（设备签名的一次 POST `/v1/task`）。成功回 `TaskGrant`；其余一律抛 `HostedError`（闭集码，永远不是别的异常）。
    功能没开 / 本机没有有效授权 = 零网络。**调用方负责在任务结束时 `close_task`（或用 `lease()` 拿带关闭的包装）。**"""
    try:
        return _open_task(kind)
    except HostedError as err:
        _log(kind, err.code)
        raise
    except Exception:  # noqa: BLE001 — 契约：只抛 HostedError；原因里不放异常消息（可能含地址）
        _log(kind, "client_error")
        raise HostedError("client_error") from None


def _open_task(kind: str) -> TaskGrant:
    if kind not in KINDS:
        raise HostedError("client_error")
    target = resolve_target()
    if target is None:
        raise HostedError("disabled")
    env = license.token_envelope()
    if env is None:
        raise HostedError("not_entitled")  # 本机没有有效授权：网关也只会这么答，不必联网
    try:
        body = license.sign_op("ai_task", {"token": env, "kind": kind, "client": _client_version()})
    except diagsend._Failure:
        raise HostedError("client_error") from None
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        resp = _request(target, "/v1/task", data, {}, API_TIMEOUT_S)
    except diagsend._Failure:
        raise HostedError("network") from None
    if not 200 <= resp.status < 300:
        code, retry_at = _error_fields(resp.body)
        retry_after = diagsend._retry_after(resp)
        raise _classify(resp.status, code, _sane_retry_at(retry_at, retry_after))
    grant = _parse_grant(resp, target.base)
    _log(kind, "ok")
    return grant


def close_task(grant: TaskGrant) -> None:
    """通知网关这个任务结束（它立即记账）。尽力而为：任何失败都吞掉（网关 30 分钟空闲后也会自己关）。永不抛。"""
    try:
        target = resolve_target()
        if target is None or target.base != grant.base_url:
            return
        _request(
            target,
            "/v1/task/close",
            b"",
            {"Authorization": f"Bearer {grant.task_token}"},
            CLOSE_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        LOG.debug("内置 AI: 关任务失败（忽略）")


class Lease:
    """一次内置 AI 任务的持有者：成功 = 有 `grant`，失败 = 有 `error`（二者必有其一）。`close()` 幂等、线程安全、永不抛。
    调用方在任务**结束的每一条路**上都要关（成功 / 失败 / 取消 / 超时 / 没起来就退出）。"""

    def __init__(self, kind: str, grant: TaskGrant | None = None, error: HostedError | None = None):
        self.kind = kind
        self.grant = grant
        self.error = error
        self._closed = False
        self._lock = threading.Lock()

    @property
    def ok(self) -> bool:
        return self.grant is not None

    def limits(self) -> TaskLimits | None:
        return self.grant.limits if self.grant else None

    def record(self) -> dict | None:
        """换上任务通行证的 provider 记录（普通 Key 连接的形状 + `hosted` 标记与上限）；开任务失败 = None。**含通行证，只给 spawn 注入用。**"""
        g = self.grant
        if g is None:
            return None
        return {
            "id": PROVIDER_ID,
            "label": label(),
            "agent": "claude",
            "base_url": g.base_url,
            "api_key": g.task_token,
            "models": [],
            "default_model": "",
            "protocol": "anthropic",
            "hosted": True,
            "hosted_limits": g.limits.as_dict(),
        }

    def conn(self) -> dict | None:
        """`builtin_conn` 形状的连接（带通行证与上限，`HttpModel` / `fixsession.build_model` 用）。"""
        rec = self.record()
        return conn_of(rec) if rec else None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self.grant is not None:
            close_task(self.grant)


def lease(kind: str) -> Lease:
    """开任务并包成 `Lease`；**永不抛**（失败落在 `lease.error`）。"""
    try:
        return Lease(kind, grant=open_task(kind))
    except HostedError as err:
        return Lease(kind, error=err)


def structured_reason(err: HostedError | None) -> str:
    """开任务失败码 → 单轮结构化调用（`ai_structured.REASONS`）的结局码：额度类 `quota`、未授权 / 客户端错 `auth`、其余（熔断 / 网络 / 网关故障）`transport`。
    调用方（数据出图）本来就静默退回规则结果，这里只是把原因归进已有的闭集。"""
    if err is None:
        return "transport"
    if err.code in ("quota_exhausted", "daily_limit", "kind_disabled"):
        return "quota"
    if err.code in ("not_entitled", "client_error", "disabled"):
        return "auth"
    return "transport"
