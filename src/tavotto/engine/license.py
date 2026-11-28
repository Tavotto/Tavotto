"""高级版授权的**引擎侧客户端**（ADR 0193 §四 / §五 / §七）：激活码换一枚带签名的授权令牌，本机验签后 `has(feature)` 才为真。

服务端是 `Tavotto/infra` 的 `serverless/license/`（CloudBase）；线上格式的唯一出处是 ADR 0193 与那边的 README / 固定签名向量
（`tests/test_license.py` 用同一个向量对拍）。形状照抄 `reviewshare.py`（同一套出站笼子），这里只写差异：

* **付费只拦新功能**（ADR 0193 §一）：本模块唯一对外的判断入口是 `has(feature)`，功能键是闭集 `FEATURES`。凡随正式版免费发布过的能力，
  代码里都不许出现权益判断；`tests/test_license_callsites.py` 用 AST 列出全仓库对 `license.has(` 的调用点，只许出现在登记表里的模块。
  本地判断只管界面怎么显示（仓库是 AGPL，本地怎么改都行；真正的价值在服务端每次请求的判断，ADR 0193 §六）。
* **没有令牌时一次网络都不发**；联网只有用户点「激活 / 付款 / 解绑 / 刷新」，或本机已有令牌且过了 `refresh_after`
  （`maybe_refresh`：每次启动最多一次、之后每天最多一次）。任何失败都静默回到免费版，不报错卡住。
* **目的地由代码钉死**：`LICENSE_HOSTS = {license.tavotto.cn}`；`TAVOTTO_LICENSE_ENDPOINT` 设了就必须合规（或回环开发模式
  `TAVOTTO_DIAG_DEV_LOOPBACK=1`），不合规整个功能关闭、**不回退内置端点**；`TAVOTTO_NO_LICENSE=1` 整个功能不出现、不联网、`has()` 恒假；
  `PRODUCTION_LIVE = False` 时没设端点 = 入口不出现、零网络。出站只经 `diagsend._send`。
* **设备签名**（ADR 0193 §五）：每台设备一把 Ed25519 密钥（PyNaCl，延后 import；32 字节种子 0600 存 `data_dir()/license/device.key`，
  **与外部审阅的设备密钥是两把**）。请求体含 `op`（闭集）、`device_pub`（标准 base64）、`ts`（毫秒整数），对「去掉 `sig` 的规范化 JSON」
  按 `"<ts>\\n<sha256 hex>"` 签名；规则以服务端 README 与固定向量为准。
* **令牌**：`{"payload": base64(JSON), "sig": base64(Ed25519(payload 字节)), "kid"}`（标准 base64）。公钥按 `kid` 内置在 `PUBLIC_KEYS`
  （正式 kid `live-2026-11`）；测试密钥只经 `TAVOTTO_LICENSE_TEST_PUBKEY` + 合规的 `TAVOTTO_LICENSE_ENDPOINT` 认，
  正式安装永远不认测试服务端签的令牌。
* **秘密不落地**：令牌原文、激活码、设备密钥只在 `data_dir()/license/` 的 0600 文件与内存里；`status()` / 日志里没有。日志模板是字面量，
  参数只有闭集里的操作名与结果码。`license/` 目录整个不进诊断包，激活码与令牌形状在脱敏规则里（`diagnostictext`）。

纯标准库，Flask 父进程 import 链上的模块（`nacl` 只在签名 / 验签时延后 import）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import atomicio, config, diagsend, ed25519util

LOG = logging.getLogger("tavotto.license")

# ---------------------------------------------------------------------------
# 开关与端点
# ---------------------------------------------------------------------------
ENV_HARD_OFF = "TAVOTTO_NO_LICENSE"
ENV_ENDPOINT = "TAVOTTO_LICENSE_ENDPOINT"
ENV_TEST_PUBKEY = "TAVOTTO_LICENSE_TEST_PUBKEY"
LICENSE_HOSTS: frozenset[str] = frozenset({"license.tavotto.cn"})
PRODUCTION_ENDPOINT = "https://license.tavotto.cn"
#: 线上服务是否已在正式模式部署并验通（ADR 0193 §七）。False 时只有显式设了 `TAVOTTO_LICENSE_ENDPOINT`
#: （开发 / 验收）才可用，正式安装里入口不出现、零网络。改成 True 要另开 PR、维护者本人点头。
PRODUCTION_LIVE = False

#: 功能键闭集（ADR 0193 §一）；加键要同步改 ADR §一。
FEATURES: frozenset[str] = frozenset(
    {
        "hosted-ai",
        "review-plus",
        "quick-layout",
        "change-plan-rerun",
        "submission-pack",
        "mechanism-diagram",
    }
)
#: 令牌公钥表 `kid -> 32 字节公钥`。换密钥 = 发版加新 kid，旧 kid 保留到它签出的令牌全部过期（ADR 0193 §七）。
#: live-2026-11：2026-11-23 生成，私钥只在正式函数 license-api 的环境变量里。
PUBLIC_KEYS: dict[str, bytes] = {
    "live-2026-11": base64.b64decode("xOJ+iCfgW9ZaYCQ/Phdt+pP+kj/47pHXuJN74eiFcFY="),
}

#: 失败码闭集（ADR 0193 §三；与前端 `license.failure.*` 对拍）。值 = 用户能不能再点一次。
FAILURES: dict[str, bool] = {
    "invalid_code": False,
    "device_limit": False,
    "revoked": False,
    "expired": False,
    "price_unavailable": False,
    "rate_limited": True,
    "network": True,
    "server": True,
    "client_error": False,
}
#: 失败码 → 本机 HTTP 状态（`/api/license/*` 用）
HTTP_STATUS: dict[str, int] = {
    "invalid_code": 400,
    "device_limit": 409,
    "revoked": 403,
    "expired": 403,
    "price_unavailable": 422,
    "rate_limited": 429,
    "network": 502,
    "server": 502,
    "client_error": 400,
}
#: 设备签名的 op 闭集（服务端核对与端点一致）
OPS = frozenset({"activate", "refresh", "release", "renew", "pro_manifest", "me", "ai_usage"})
LOG_RESULTS = frozenset(FAILURES) | {"ok"}

API_TIMEOUT_S = 15
API_MAX_BYTES = 64 * 1024
REFRESH_MIN_S = 60
#: 后台刷新：启动后等一会儿再试，之后每天最多一次
STARTUP_DELAY_S = 20
DAY_S = 86_400

DIR_NAME = "license"
KEY_FILE = "device.key"
TOKEN_FILE = "token.json"

_UA = "Tavotto-license-client/1"
_LOOKUP_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_LOCALE = re.compile(r"^[a-zA-Z]{2}(-[A-Za-z]{2,4})?$")
_CODE_MAX = 40
_STATES = frozenset({"ok", "revoked", "expired"})

_Failure = diagsend._Failure


class LicenseError(Exception):
    """对 Flask 层抛的同步错：`code` 稳定（`FAILURES` 的键或 `disabled`）、`status` 是本机 HTTP 状态。"""

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status if status is not None else HTTP_STATUS.get(code, 400)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


_DEV_HOSTS = frozenset({"127.0.0.1", "localhost"})


@dataclass(frozen=True)
class _DevTarget(diagsend.Target):
    """**仅回环开发模式**的端点（可以是 `http://`，本机服务端常没有 TLS）。生产 `Target` 永远是 https。"""

    scheme: str = "https"

    @property
    def base(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return f"{self.scheme}://{self.host}" + ("" if self.port == default else f":{self.port}")


def _dev_endpoint(raw: str) -> diagsend.Target | None:
    """回环开发模式下的端点：`http(s)://127.0.0.1|localhost[:端口]`，无用户信息 / 路径 / 查询串。"""
    try:
        parts = urllib.parse.urlsplit(raw.strip())
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or host not in _DEV_HOSTS:
        return None
    if "@" in parts.netloc or parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    return _DevTarget(host, port or (443 if parts.scheme == "https" else 80), True, parts.scheme)


def _endpoint_override() -> str | None:
    raw = os.environ.get(ENV_ENDPOINT)
    return raw if raw is not None and raw.strip() else None


def resolve_target() -> diagsend.Target | None:
    """此刻允许连的端点；None = 功能关闭（入口不出现、不联网）。设了 `TAVOTTO_LICENSE_ENDPOINT` 但不合规也是 None，不回退。"""
    if _flag(ENV_HARD_OFF):
        return None
    raw = _endpoint_override()
    if raw is not None:
        dev = _flag(diagsend.ENV_DEV_LOOPBACK)
        got = diagsend.parse_endpoint(raw, dev_loopback=dev, hosts=LICENSE_HOSTS)
        if got is not None and got.loopback:
            return _dev_endpoint(raw)  # 回环只有开发模式才走到这里
        if got is None and dev:
            return _dev_endpoint(raw)
        return got
    if not PRODUCTION_LIVE:
        return None
    return diagsend.parse_endpoint(PRODUCTION_ENDPOINT, dev_loopback=False, hosts=LICENSE_HOSTS)


def enabled() -> bool:
    return resolve_target() is not None


def capability() -> dict:
    """给界面的能力说明（本机判断，**不联网**，不含端点地址）。"""
    return {"enabled": enabled()}


# ---------------------------------------------------------------------------
# 规范化 JSON 与设备签名（规则：serverless/license/README.md「设备签名」）
# ---------------------------------------------------------------------------
_SAFE_INT = 2**53 - 1


def canonicalize(value: object) -> str:
    """对象键按 UTF-16 码元序递归排序，紧凑 JSON，非 ASCII 不转义；只许字符串 / 安全整数 / 布尔 / null / 对象 / 数组（**不许浮点**）。"""
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        if abs(value) > _SAFE_INT:
            raise ValueError("integer out of safe range")
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=lambda k: str(k).encode("utf-16-be"))
        return (
            "{"
            + ",".join(
                json.dumps(str(k), ensure_ascii=False) + ":" + canonicalize(value[k]) for k in keys
            )
            + "}"
        )
    raise ValueError("unsupported value in signed body")


def signed_bytes(body: dict) -> bytes:
    """被签名字节串 = UTF-8(`"<ts>\\n<规范化 JSON（去掉顶层 sig）的 UTF-8 字节的 sha256 小写十六进制>"`)。"""
    rest = {k: v for k, v in body.items() if k != "sig"}
    ts = rest.get("ts")
    if not isinstance(ts, int) or isinstance(ts, bool):
        raise ValueError("bad ts")
    digest = hashlib.sha256(canonicalize(rest).encode("utf-8")).hexdigest()
    return f"{ts}\n{digest}".encode()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: object) -> bytes:
    if not isinstance(text, str):
        raise ValueError("not str")
    return base64.b64decode(text.encode("ascii"), validate=True)


_time = time.time
_STORE_LOCK = threading.RLock()  # 文件读写（密钥生成、令牌落盘）
_OP_LOCK = threading.RLock()  # 一次联网操作从头到尾；status / has 不拿它，不会被网络拖住


def _now() -> float:
    return float(_time())


def _key_path():
    return config.data_path(DIR_NAME, KEY_FILE)


def _token_path():
    return config.data_path(DIR_NAME, TOKEN_FILE)


def _signing_key(*, create: bool):
    """本机设备密钥。`create=False`（`has` / `status` 用）只读、不存在返回 None；`create=True` 首次使用时生成（32 字节种子，0600，目录 0700）。
    PyNaCl 缺失 / 写不下去 → `client_error`（不用一次性密钥：那等于每次新设备）。"""
    try:
        from nacl.signing import SigningKey
    except ImportError:
        if create:
            raise _Failure("client_error") from None
        return None
    p = _key_path()
    with _STORE_LOCK:
        try:
            seed = p.read_bytes()
        except OSError:
            seed = b""
        if len(seed) == 32:
            return SigningKey(seed)
        if not create:
            return None
        key = SigningKey.generate()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.parent.chmod(0o700)
            except OSError:
                pass
            atomicio.write_bytes(p, bytes(key), mode=0o600)
        except OSError:
            LOG.warning("高级版授权: 设备密钥写入失败")
            raise _Failure("client_error") from None
        return key


def _device_hash() -> str | None:
    """本机设备公钥的 sha256 十六进制（令牌里 `device` 字段的取值）；没有设备密钥 = None（只读，不生成）。"""
    key = _signing_key(create=False)
    return hashlib.sha256(bytes(key.verify_key)).hexdigest() if key is not None else None


def sign_body(body: dict) -> dict:
    """给请求体补上 `device_pub` 与 `sig`（`body` 里已有 `op`、`ts`）；返回新字典。"""
    if body.get("op") not in OPS:
        raise _Failure("client_error")
    key = _signing_key(create=True)
    out = dict(body)
    out["device_pub"] = _b64(bytes(key.verify_key))
    try:
        out["sig"] = _b64(key.sign(signed_bytes(out)).signature)
    except ValueError:
        raise _Failure("client_error") from None
    return out


def _signed(op: str, fields: dict) -> dict:
    return sign_body({**fields, "op": op, "ts": int(_now() * 1000)})


# ---------------------------------------------------------------------------
# 令牌：解包、验签、落盘
# ---------------------------------------------------------------------------
def _public_key(kid: object) -> bytes | None:
    if not isinstance(kid, str):
        return None
    known = PUBLIC_KEYS.get(kid)
    if known is not None:
        return known
    # 测试密钥：只在「设了合规的开发 / 验收端点」时认，正式安装永远不认测试服务端签的令牌
    if _endpoint_override() is not None and resolve_target() is not None:
        raw = os.environ.get(ENV_TEST_PUBKEY, "").strip()
        if raw:
            try:
                pub = _b64decode(raw)
            except (ValueError, binascii.Error):
                return None
            return pub if len(pub) == 32 else None
    return None


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _open_token(env: object) -> dict | None:
    """信封 → payload 字典；签名无效 / kid 未知 / 形状不对 = None（不看时间与设备）。永不抛。"""
    try:
        if not isinstance(env, dict):
            return None
        pub = _public_key(env.get("kid"))
        if pub is None:
            return None
        payload_raw = _b64decode(env.get("payload"))
        sig = _b64decode(env.get("sig"))
        if not ed25519util.verify(pub, payload_raw, sig):
            return None
        payload = json.loads(payload_raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        feats = payload.get("features")
        if not isinstance(feats, list) or not all(isinstance(f, str) for f in feats):
            return None
        for k in ("exp", "iat", "refresh_after", "offline_until"):
            if not _is_int(payload.get(k)):
                return None
        if not isinstance(payload.get("lid"), str) or not isinstance(payload.get("device"), str):
            return None
        return payload
    except Exception:  # noqa: BLE001 — 令牌来自磁盘 / 网络，任何畸形都当无效，不抛
        return None


def _load() -> tuple[dict | None, str]:
    """本机令牌记录 → `(信封, 状态)`；没有 / 损坏 = `(None, "ok")`。"""
    with _STORE_LOCK:
        try:
            rec = json.loads(_token_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "ok"
    if not isinstance(rec, dict) or not isinstance(rec.get("token"), dict):
        return None, "ok"
    state = rec.get("state")
    return rec["token"], state if state in _STATES else "ok"


def _save(env: dict, state: str = "ok") -> None:
    p = _token_path()
    with _STORE_LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.parent.chmod(0o700)
            except OSError:
                pass
            atomicio.write_bytes(
                p,
                json.dumps({"v": 1, "token": env, "state": state}, separators=(",", ":")).encode(
                    "utf-8"
                ),
                mode=0o600,
            )
        except OSError:
            LOG.warning("高级版授权: 令牌写入失败")
            raise _Failure("server") from None


def _clear() -> None:
    with _STORE_LOCK:
        try:
            _token_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            LOG.warning("高级版授权: 令牌删除失败")


def _mark(state: str) -> None:
    """服务端说 revoked / expired：令牌原样留着（刷新时可看），但标记失效，`has` 为假；设备密钥不动。"""
    env, _ = _load()
    if env is not None:
        try:
            _save(env, state)
        except _Failure:
            pass


def _usable(payload: dict, state: str, now: float) -> bool:
    mine = _device_hash()
    return (
        state == "ok"
        and mine is not None
        and payload["device"] == mine
        and now < payload["offline_until"]
        and now < payload["exp"]
    )


def has(feature: str) -> bool:
    """**唯一的权益判断入口**（ADR 0193 §一：只许新功能模块调用，`test_license_callsites` 看护）。
    签名有效、kid 已知、`device` 是本机、`now < offline_until`、`now < exp`、功能键在闭集且在令牌里。任何异常返回 False，绝不抛。"""
    try:
        if feature not in FEATURES or not enabled():
            return False
        env, state = _load()
        if env is None:
            return False
        payload = _open_token(env)
        if payload is None or not _usable(payload, state, _now()):
            return False
        return feature in payload["features"]
    except Exception:  # noqa: BLE001 — 授权判断永远不许把调用方带下水
        return False


#: 创始席位号上限：服务端只发 1–200；客户端放宽到 999（形状校验，不替服务端判额度）
FOUNDING_NO_MAX = 999


def _founding_of(payload: dict) -> dict | None:
    """令牌里的创始会员标记（`founding_no` 席位号、`founding_since` 首次以创始价付款的 unix 秒）；两个都合规才算，永不抛。"""
    try:
        no, since = payload.get("founding_no"), payload.get("founding_since")
        if not _is_int(no) or not 1 <= no <= FOUNDING_NO_MAX:
            return None
        if not _is_int(since) or since <= 0:
            return None
        return {"no": no, "since": since}
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 铭牌刻字（只存本机）
# ---------------------------------------------------------------------------
PLATE_FILE = "plate.json"
PLATE_NAME_MAX = 24


def _plate_path():
    # 放在 `license/` 里：这个目录整个不进诊断包 / 遥测，名字绝不出机
    return config.data_path(DIR_NAME, PLATE_FILE)


def _clean_plate_name(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    text = "".join(ch for ch in raw if ch.isprintable() or ch == " ")
    return " ".join(text.split())[:PLATE_NAME_MAX]


def plate_name() -> str:
    """用户刻在铭牌上的名字；没有 / 文件坏了 = 空串。永不抛。"""
    with _STORE_LOCK:
        try:
            rec = json.loads(_plate_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
    return _clean_plate_name(rec.get("name")) if isinstance(rec, dict) else ""


def set_plate_name(name: object) -> dict:
    """刻名字（空串 = 清掉，回到默认）。只写本机文件，不联网；返回新的 `status()`。"""
    _target()  # 入口没开：404
    if not isinstance(name, str):
        raise LicenseError("client_error")
    clean = _clean_plate_name(name)
    p = _plate_path()
    with _STORE_LOCK:
        try:
            if clean:
                p.parent.mkdir(parents=True, exist_ok=True)
                atomicio.write_bytes(
                    p,
                    json.dumps({"v": 1, "name": clean}, ensure_ascii=False).encode("utf-8"),
                    mode=0o600,
                )
            else:
                p.unlink(missing_ok=True)
        except OSError:
            LOG.warning("高级版授权: 铭牌名字写入失败")
            raise LicenseError("client_error") from None
    return {"ok": True, **status()}


def status() -> dict:
    """给前端的摘要（**不联网**）；不含令牌原文与设备密钥。`state`：none / active / expired / revoked / lapsed（过了断网宽限或
    令牌对不上，需要联网刷新）。创始会员另带 `founding: {no, since}`（吊销时没有）。"""
    if not enabled():
        return {"enabled": False}
    out = {
        "enabled": True,
        "licensed": False,
        "state": "none",
        "features": [],
        "exp": None,
        "email_hint": "",
        "needs_refresh": False,
    }
    try:
        env, state = _load()
        payload = _open_token(env) if env is not None else None
        if payload is None:
            return out
        now = _now()
        out["exp"] = payload["exp"]
        out["email_hint"] = str(payload.get("email_hint") or "")[:64]
        # 创始身份是永久的：active / expired / lapsed 都给，吊销（state 记为 revoked）不给；形状不对当没有
        founding = _founding_of(payload) if state != "revoked" else None
        if founding is not None:
            out["founding"] = {**founding, "name": plate_name()}
        if state != "ok":
            out["state"] = state
        elif now >= payload["exp"]:
            out["state"] = "expired"
        elif not _usable(payload, state, now):
            out["state"] = "lapsed"
            out["needs_refresh"] = True
        else:
            out["state"] = "active"
            out["licensed"] = True
            out["features"] = sorted(f for f in payload["features"] if f in FEATURES)
        if state == "ok" and now >= payload["refresh_after"]:
            out["needs_refresh"] = True
    except Exception:  # noqa: BLE001 — 摘要出错就当没有授权
        pass
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _log(op: str, result: str) -> None:
    op = op if op in OPS | {"checkout", "claim"} else "other"
    result = result if result in LOG_RESULTS else "client_error"
    LOG.info("高级版授权: %s %s", op, result)


def _server_code(resp: diagsend._Response) -> str:
    """错误体：`{"error": {"code", "message"}}`；别的形状不认。"""
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    err = obj.get("error") if isinstance(obj, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code[:64] if isinstance(code, str) else ""


def classify(status: int, server_code: str) -> str:
    """服务端错误 → `FAILURES` 的键。服务端 code 在闭集里直通（`network` 是客户端自己判的，服务端发了也不认）。"""
    if server_code in FAILURES and server_code != "network":
        return server_code
    if status == 429:
        return "rate_limited"
    if status >= 500 or status == 404:
        return "server"  # 裸 404 多半是网关 / 路径出了问题，可再试
    return "client_error"


def _call(target: diagsend.Target, path: str, payload: dict) -> dict:
    """一次 POST；2xx 回响应对象，其余抛 `_Failure(闭集码)`。请求体字节只构造一次，签的就是发的。"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — 地址由 Target 钉死为白名单主机的 https
        target.base + path,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
        },
    )
    try:
        resp = diagsend._send(req, API_TIMEOUT_S, API_MAX_BYTES)
    except _Failure as fail:
        raise _Failure(
            "network" if fail.code in ("network", "timeout", "tls") else fail.code
        ) from None
    if not 200 <= resp.status < 300:
        raise _Failure(classify(resp.status, _server_code(resp)), diagsend._retry_after(resp))
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _Failure("server") from None
    if not isinstance(obj, dict):
        raise _Failure("server")
    return obj


def _fail(op: str, fail: _Failure) -> LicenseError:
    code = fail.code if fail.code in FAILURES else "client_error"
    _log(op, code)
    return LicenseError(code)


def _target() -> diagsend.Target:
    target = resolve_target()
    if target is None:
        raise LicenseError("disabled", 404)
    return target


def _accept_token(raw: object, *, lid: str | None) -> dict:
    """服务端给的令牌：验签、`device` 是本机、`lid` 对得上（刷新时）、没过期，才落盘。不合格 = `server`（契约漂移 / 被篡改）。"""
    payload = _open_token(raw)
    mine = _device_hash()
    if (
        payload is None
        or mine is None
        or payload["device"] != mine
        or (lid is not None and payload["lid"] != lid)
        or _now() >= payload["exp"]
    ):
        raise _Failure("server")
    assert isinstance(raw, dict)
    env = {k: raw[k] for k in ("payload", "sig", "kid")}
    _save(env, "ok")
    return payload


def _clean_code(code: object) -> str:
    if not isinstance(code, str):
        raise LicenseError("invalid_code")
    clean = code.strip().upper()
    if not clean or len(clean) > _CODE_MAX or not clean.isascii():
        raise LicenseError("invalid_code")
    return clean


def activate(code: object) -> dict:
    """用户点「激活」：激活码换令牌，绑定这台设备。成功回 `status()`。"""
    target = _target()
    clean = _clean_code(code)
    with _OP_LOCK:
        try:
            body = _signed("activate", {"code": clean})
            got = _call(target, "/v1/activate", body)
            _accept_token(got.get("token"), lid=None)
        except _Failure as fail:
            raise _fail("activate", fail) from None
        _LAST["attempt"] = _now()
        _log("activate", "ok")
    _notify_after_license()  # 激活成功后立刻查一次高级版组件（后台线程，不阻塞响应）
    return {"ok": True, **status()}


def _lid_of_local_token() -> str | None:
    """本机令牌的授权号（只要签名有效；过期 / 作废也行——续费、解绑恰恰发生在那时）。"""
    env, _ = _load()
    payload = _open_token(env) if env is not None else None
    return payload["lid"] if payload is not None else None


_LAST: dict[str, float] = {"attempt": 0.0}


def refresh(*, force: bool = False) -> dict:
    """刷新令牌。**只在有令牌、没被作废且过了 `refresh_after` 时联网**（`force` 只跳过 `refresh_after`）；
    服务端回 `revoked` / `expired` 时本地令牌标记失效（`has` 变假），设备密钥不动。网络 / 服务端失败令牌原样留着。
    标记为 `expired` 的令牌**仍然可以刷新**（不看 `refresh_after`）：续费正是在过期之后发生的，付款后刷新才拿得到新令牌；
    拿到新令牌之前 `has` 照旧为假。`revoked` 只能重新激活恢复。"""
    target = _target()
    with _OP_LOCK:
        env, state = _load()
        payload = _open_token(env) if env is not None else None
        if payload is None or state not in ("ok", "expired"):
            return {"ok": True, "refreshed": False, **status()}
        now = _now()
        due = state == "expired" or force or now >= payload["refresh_after"]
        if not due or now - _LAST["attempt"] < REFRESH_MIN_S:
            return {"ok": True, "refreshed": False, **status()}
        _LAST["attempt"] = now
        try:
            body = _signed("refresh", {"license_id": payload["lid"]})
            got = _call(target, "/v1/refresh", body)
            verdict = got.get("state")
            if verdict in ("revoked", "expired"):
                _mark(verdict)
                _log("refresh", verdict)
                return {"ok": True, "refreshed": False, **status()}
            _accept_token(got.get("token"), lid=payload["lid"])
        except _Failure as fail:
            if fail.code in ("revoked", "expired"):
                _mark(fail.code)
            raise _fail("refresh", fail) from None
        _log("refresh", "ok")
    _notify_after_license()  # 拿到新令牌后查一次高级版组件（后台线程，不阻塞响应）
    return {"ok": True, "refreshed": True, **status()}


#: 授权成功（激活 / 刷新拿到新令牌）或每日后台刷新之后要跑的回调（ADR 0198 §五：检查高级版组件更新）。注册而不是直接 import：
#: `proinstall` 要 import 本模块，本模块不能反过来 import 它。回调在**后台守护线程**里跑，不阻塞激活 / 刷新的响应；回调自己负责「没有授权就不联网」。
_AFTER_LICENSE: list = []


def register_after_license(fn) -> None:
    """登记一个授权后回调（幂等）；回调的异常一律吞掉、只记一行日志。"""
    if fn not in _AFTER_LICENSE:
        _AFTER_LICENSE.append(fn)


def _notify_after_license() -> None:
    """起一个守护线程依次跑已登记的回调（没有登记 = 什么都不做）。永不抛、不等回调。"""
    fns = list(_AFTER_LICENSE)
    if not fns:
        return

    def run() -> None:
        for fn in fns:
            try:
                fn()
            except Exception:  # noqa: BLE001 — 后台回调不允许把主进程带下水
                LOG.warning("高级版授权: 授权后回调失败")

    threading.Thread(target=run, name="tavotto-license-after", daemon=True).start()


def pro_manifest(platform: str, pro_api: list[int]) -> dict:
    """向服务端要高级版组件的发布清单与限时下载地址（ADR 0198 §五，`POST /v1/pro/manifest`，设备签名同 `refresh`）。
    回 `{"release", "url", "app_update_needed"}` 或 `{"state": "none" | "revoked" | "expired"}`；**不验清单签名**（那是 `proinstall.verify_release` 的事）。
    本机没有令牌 = `client_error`、零网络。"""
    target = _target()
    if (
        not isinstance(platform, str)
        or not _LOOKUP_KEY.match(platform)
        or not isinstance(pro_api, list)
        or not pro_api
        or not all(_is_int(v) for v in pro_api)
    ):
        raise LicenseError("client_error")
    with _OP_LOCK:
        lid = _lid_of_local_token()
        if lid is None:
            raise LicenseError("client_error")
        try:
            body = _signed(
                "pro_manifest",
                {"license_id": lid, "platform": platform, "pro_api": sorted(set(pro_api))},
            )
            got = _call(target, "/v1/pro/manifest", body)
            state = got.get("state")
            if state is not None:
                if state not in ("none", "revoked", "expired"):
                    raise _Failure("server")
                _log("pro_manifest", "ok")
                return {"state": state}
            release, url = got.get("release"), got.get("url")
            if not isinstance(release, dict) or not isinstance(url, str):
                raise _Failure("server")
            _log("pro_manifest", "ok")
            return {
                "release": release,
                "url": url,
                "app_update_needed": got.get("app_update_needed") is True,
            }
        except _Failure as fail:
            raise _fail("pro_manifest", fail) from None


def maybe_refresh() -> None:
    """后台刷新入口（启动后调用）：每次启动最多一次、之后每天最多一次；没有令牌 / 功能关闭 / 没到 `refresh_after` 一次网络都不发。永不抛。"""
    try:
        if not enabled():
            return
        env, state = _load()
        if env is None or state not in ("ok", "expired"):
            return
        if _now() - _LAST["attempt"] < DAY_S and _LAST["attempt"] > 0:
            return
        refresh()
    except Exception:  # noqa: BLE001 — 后台刷新不允许把主进程带下水
        pass


_started = threading.Event()
_wake = threading.Event()


def start_background_refresh() -> None:
    """启动时一次 + 之后每天一次的后台刷新（守护线程；功能关闭 / 没有令牌时什么网络都不发）。幂等。"""
    if _started.is_set() or _flag(ENV_HARD_OFF):
        return
    _started.set()

    def loop() -> None:
        _wake.wait(STARTUP_DELAY_S)
        while True:
            _wake.clear()
            before = _LAST["attempt"]
            maybe_refresh()
            if _LAST["attempt"] == before:
                # 这轮没有刷新（还没到 refresh_after / 没有令牌）：令牌有效时也查一次高级版组件
                # （启动后一次、之后每天最多一次，ADR 0198 §五）；刷新成功的那轮已在 `refresh()` 里触发过
                _notify_after_license()
            _wake.wait(DAY_S)

    threading.Thread(target=loop, name="tavotto-license-refresh", daemon=True).start()


# ---------------------------------------------------------------------------
# Pro 页：账户详情与内置 AI 用量（只读；失败一律静默降级，不报错）
# ---------------------------------------------------------------------------
#: 两个读接口各缓存这么久（同一份授权）：打开 Pro 页才取，不轮询
READ_TTL_S = 60
_READ_CACHE: dict[str, tuple[str, float, dict]] = {}


def _tier_key(lookup_key: object) -> str:
    """Stripe Price 的 lookup_key → 界面认的档名：year / season / sprint / other（创始年度也归 year，创始身份看令牌）。"""
    k = lookup_key if isinstance(lookup_key, str) else ""
    if k.startswith(("founding_year_", "year_")):
        return "year"
    if k.startswith("season_90_"):
        return "season"
    if k.startswith("sprint_30_"):
        return "sprint"
    return "other"


def _read_token() -> tuple[dict | None, str]:
    env, state = _load()
    payload = _open_token(env) if env is not None else None
    return payload, state


def account() -> dict:
    """`GET /api/license/account`：包一层 infra `POST /v1/me`（设备签名 `op = "me"`）。
    成功 `{ok: true, tier: {key, term_days}, devices: {used, limit}, paid_at, exp}`；没有令牌 / 已作废 / 网络或服务端出错 /
    形状不对一律 `{ok: false}`（HTTP 200，不报错）。没有令牌时零网络；服务端回 `revoked` 时照 `refresh` 的做法把本机令牌标为失效。"""
    target = _target()
    with _OP_LOCK:
        payload, state = _read_token()
        if payload is None or state not in ("ok", "expired"):
            return {"ok": False}
        hit = _READ_CACHE.get("account")
        if hit and hit[0] == payload["lid"] and _now() - hit[1] < READ_TTL_S:
            return hit[2]
        try:
            got = _call(target, "/v1/me", _signed("me", {"license_id": payload["lid"]}))
        except _Failure as fail:
            _log("me", fail.code if fail.code in FAILURES else "client_error")
            return {"ok": False}
        if got.get("state") == "revoked":
            _mark("revoked")
            _log("me", "revoked")
            return {"ok": False}
        dev = got.get("devices")
        ok = (
            got.get("state") in ("active", "expired")
            and isinstance(got.get("tier"), str)
            and _is_int(got.get("term_days"))
            and got["term_days"] > 0
            and _is_int(got.get("paid_at"))
            and _is_int(got.get("exp"))
            and isinstance(dev, dict)
            and _is_int(dev.get("used"))
            and _is_int(dev.get("limit"))
            and dev["used"] >= 0
            and dev["limit"] >= 0
        )
        if not ok:
            _log("me", "server")
            return {"ok": False}
        out = {
            "ok": True,
            "tier": {"key": _tier_key(got["tier"]), "term_days": got["term_days"]},
            "devices": {"used": dev["used"], "limit": dev["limit"]},
            "paid_at": got["paid_at"],
            "exp": got["exp"],
        }
        _READ_CACHE["account"] = (payload["lid"], _now(), out)
        _log("me", "ok")
        return out


def ai_usage() -> dict:
    """`GET /api/license/usage`：包一层 infra `POST /v1/ai/usage`（设备签名 `op = "ai_usage"`）。
    成功 `{agent_used: 0..1}`（= round(1 − remaining, 2)）；额度算不准（`remaining: null`）、没有 `hosted-ai`、没有令牌、
    403 / 网络 / 服务端出错 / 形状不对一律 `{agent_used: null}`（HTTP 200，不报错）。只给比例，不给 token 与金额。"""
    target = _target()
    none = {"agent_used": None}
    with _OP_LOCK:
        payload, state = _read_token()
        if payload is None or state != "ok" or "hosted-ai" not in payload["features"]:
            return none
        hit = _READ_CACHE.get("usage")
        if hit and hit[0] == payload["lid"] and _now() - hit[1] < READ_TTL_S:
            return hit[2]
        try:
            got = _call(target, "/v1/ai/usage", _signed("ai_usage", {"license_id": payload["lid"]}))
        except _Failure as fail:
            _log("ai_usage", fail.code if fail.code in FAILURES else "client_error")
            return none
        rem = got.get("remaining")
        if rem is None:
            _log("ai_usage", "ok")
            return none
        if isinstance(rem, bool) or not isinstance(rem, (int, float)) or rem != rem:
            _log("ai_usage", "server")
            return none
        out = {"agent_used": round(1 - min(1.0, max(0.0, float(rem))), 2)}
        _READ_CACHE["usage"] = (payload["lid"], _now(), out)
        _log("ai_usage", "ok")
        return out


def release() -> dict:
    """本机解绑：腾出名额，本地令牌删除（设备密钥保留）。没有令牌 = 什么都不做、零网络。"""
    target = _target()
    with _OP_LOCK:
        lid = _lid_of_local_token()
        if lid is None:
            return {"ok": True, **status()}
        try:
            _call(target, "/v1/devices/release", _signed("release", {"license_id": lid}))
        except _Failure as fail:
            raise _fail("release", fail) from None
        _clear()
        _log("release", "ok")
        return {"ok": True, **status()}


def _checkout_url_ok(url: object, target: diagsend.Target) -> bool:
    """付款页地址：https、Stripe 域名、无用户信息；回环开发模式放行回环。"""
    if not isinstance(url, str) or not url or len(url) > 2048 or not url.isascii():
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 — 非法端口在这里抛 ValueError
    except ValueError:
        return False
    # 片段（#…）放行：Stripe 真实的 Checkout 地址末尾就带一段 `#fid…`（11-23 测试环境实测），片段不会发给服务器
    if parts.username is not None or "@" in parts.netloc:
        return False
    if isinstance(target, _DevTarget) and host in _DEV_HOSTS and parts.scheme in ("http", "https"):
        return True
    return parts.scheme == "https" and (host == "stripe.com" or host.endswith(".stripe.com"))


def checkout(lookup_key: object, locale: object = "zh", renew: bool = False) -> dict:
    """建付款页，回地址（由前端在系统浏览器打开）。`renew` = 给本机这一份续费：带签名的 `renew` 对象（`op = "renew"`，数量固定 1）；
    本机没有令牌却要续费 = `client_error`（零网络）。不带 `renew` 的购买一律是新的份数。"""
    target = _target()
    if not isinstance(lookup_key, str) or not _LOOKUP_KEY.match(lookup_key):
        raise LicenseError("client_error")
    loc = locale if isinstance(locale, str) and _LOCALE.match(locale) else "zh"
    with _OP_LOCK:
        try:
            body: dict = {"lookup_key": lookup_key, "locale": loc}
            if renew:
                lid = _lid_of_local_token()
                if lid is None:
                    raise _Failure("client_error")
                body["renew"] = _signed("renew", {"license_id": lid})
            got = _call(target, "/v1/checkout", body)
            url = got.get("url")
            if not _checkout_url_ok(url, target):
                raise _Failure("server")
        except _Failure as fail:
            raise _fail("checkout", fail) from None
        _log("checkout", "ok")
        return {"ok": True, "url": url}


def reset_for_tests() -> None:
    _READ_CACHE.clear()
    _LAST["attempt"] = 0.0
    _started.clear()
    _wake.clear()
