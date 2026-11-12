"""远程期刊目录：索引整份 + 刊包按需，Ed25519 验签 → 校验 → 本地缓存 → 离线回退（ADR 0155 §4b / §10）。

取用顺序是 服务端（验签、校验通过）→ 本地缓存 → 内置种子；本模块负责前两档，种子档在 `engine/journals.py`
（前端 `journalCatalogStore` 的最后一道兜底）。**任何一步不通过，这次拉取整个作废**，缓存原样不动——
不做部分采信，不弹错误。

两层数据（用户 2026-11-04 拍板：按需下载 + 服务端留读取记录）：

* **索引**（`index.signed.json`，整份下载、不计数）：出版社名、每本刊的名称 / ISSN / 别名 / id，以及每本刊「刊包」的
  sha256 与字节数。索引是**签名信封**：`{alg:"ed25519", key_id, payload:<b64>, sig:<b64>}`，签名覆盖 `payload` 解码后的
  原始字节（不做规范化）。验签通过才解析；`catalog_rev` 不得低于历史最高与内置种子。搜索 / 浏览只靠索引，离线可用。
* **刊包**（`journals/<id>?rev=<n>`，选中某本刊时才拉）：该刊完整对象 + 所属出版社完整对象（含 sources / rules）。
  **每本刊的标准由签名索引给出的 sha256 担保**（不另签每个刊包）：哈希或大小对不上整包作废。
  请求带设备令牌（`X-Tavotto-Device`）；服务端只数该令牌对该刊的**首次**读取（重复读、`catalog_rev` 更新重拉、种子里的刊都不计数）。
  超限回 429 → 本模块回报 `quota`（含 `retry_after_s`），界面按「不按期刊」处理。
* **快照**（`snapshots/<sha256>.webp`，内容寻址）：出处卡要看时才拉，哈希必须等于文件名。

设备令牌：服务端签发的随机串，`POST device` 首次需要时取，存 `data_dir()/journal-catalog/device.json`（0600）。
与遥测的 install_id 无关、互不派生。请求里没有项目 / 路径 / 脚本 / 所选期刊以外的任何东西，UA 只有 `Tavotto/<版本>`。
只认专用开关 `TAVOTTO_NO_JOURNAL_SYNC=1`（`TAVOTTO_NO_TELEMETRY` 只管遥测，与 `updater` 同一先例）。

验签用 PyNaCl（libsodium；用户 2026-11-05 拍板：用成熟库，不手写加密；不用 cryptography 是 Intel Mac 轮子与告警），
**只在 `_ed25519_verify` 里延后 import**
（与 `tlstrust` 的 truststore 同一写法；Flask 父进程启动不加载它，`tests/test_journal_remote.py` 看护）。
import 不了、长度不对、任何异常一律当验签失败（fail closed，目录保持种子）。

看护：`tests/test_journal_remote.py`。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlparse

from . import atomicio, config, journals, tlstrust

LOG = logging.getLogger(__name__)

#: 内置信任的公钥（key_id → 32 字节公钥的 base64）。验签只认这里。**轮换 = 先发含新旧两把的客户端版本，
#: 再切签名，最后下一个版本去掉旧钥**（ADR 0155 §10）。
#:
#: 生产代码里**只有** `prod-1`（私钥在维护者本机，不进仓库）。测试钥只在测试里注入
#: （`tests/journal_catalog_signing.py`），`test_production_code_does_not_trust_the_test_key` 看护。
TRUSTED_KEYS: dict[str, str] = {"prod-1": "fYO5adiBp/EH0ug9cRcsC3bhij5l1V4Em4rcfYGLW5Q="}

#: 两个入口（**一个后端、计数只有一份**）：`(中国站, 海外)`。中国站 `tavotto.cn` 是后端本体（sqlite 计数在那里），
#: 海外 `tavotto.com` 是 Vercel 上的薄反代，转到中国站（`services/journal_catalog/vercel_proxy/`）。
#: 先后顺序与更新检查、联系作者**同一个**「大陆环境」判据（`contactsend.region_is_cn`）：大陆先 .cn 后 .com，其余反过来。
CATALOG_URLS: tuple[str, ...] = (
    "https://tavotto.cn/v1/journal-catalog/",
    "https://tavotto.com/v1/journal-catalog/",
)
URL_ENV = "TAVOTTO_JOURNAL_CATALOG_URL"

SYNC_INTERVAL_S = 24 * 3600
RETRY_AFTER_FAILURE_S = 3600
SNAPSHOT_RETRY_S = 600
NETWORK_TIMEOUT_S = 15
MAX_INDEX_BYTES = 16 << 20
MAX_BUNDLE_BYTES = 4 << 20
MAX_SNAPSHOT_BYTES = 4 << 20

#: 客户端类型头 `X-Tavotto-Client`：桌面 / 浏览器版恒为 `app`；Agent 宿主写 `agent-<宿主名>`。服务端只记录用于统计，
#: **限额不按它分桶**（IP 级限额是 app 与 Agent 合计；数字以服务端为准、可调）。
CLIENT_TYPE = "app"
CLIENT_ENV = "TAVOTTO_JOURNAL_CLIENT"
_CLIENT_RE = re.compile(r"^(app|agent-[a-z0-9][a-z0-9-]{0,31})$")
LOCK_STALE_S = 30

INDEX_NAME = "index.signed.json"
INDEX_KIND = "tavotto-journal-catalog-index"
ASSET_ROUTE = "/api/journal-catalog/assets/"

#: 失败码闭集（`code` 字段；界面不显示，日志与测试用）
CODES = (
    "disabled",
    "network",
    "signature_missing",
    "signature_invalid",
    "key_unknown",
    "index_invalid",
    "rev_rollback",
    "bundle_invalid",
    "catalog_invalid",
    "device_unknown",
    "unknown_journal",
    "stale_rev",
    "quota_hour",
    "quota_day",
    "ip_day",
    "ip_tokens",
    "server_error",
)
QUOTA_CODES = ("quota_hour", "quota_day", "ip_day", "ip_tokens")

_JID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")
_PID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SNAPSHOT_RE = re.compile(r"^snapshots/([0-9a-f]{64})\.webp$")

_LOCK = threading.RLock()
_SNAPSHOT_FAILED: dict[str, float] = {}


class RemoteCatalogError(Exception):
    def __init__(self, code: str, message: str = "", *, retry_after_s: int | None = None):
        super().__init__(message or code)
        assert code in CODES
        self.code = code
        self.retry_after_s = retry_after_s


# ---------------------------------------------------------------------------
# 开关与路径
# ---------------------------------------------------------------------------
def disabled() -> bool:
    """`TAVOTTO_NO_JOURNAL_SYNC=1`。只管这一条通道：与 `updater` 的先例一致，`TAVOTTO_NO_TELEMETRY` 只管遥测，谁都不代管对方。"""
    raw = (os.environ.get("TAVOTTO_NO_JOURNAL_SYNC") or "").strip().lower()
    return raw not in ("", "0", "false", "no")


def cache_dir() -> Path:
    return config.data_dir() / "journal-catalog"


def base_urls(ui_locale: str | None = None) -> tuple[str, ...]:
    override = (os.environ.get(URL_ENV) or "").strip()
    if not override:
        from . import contactsend  # 延后：「大陆环境」判据只有这一处（ADR 0121 判据 3）

        loc = ui_locale or _read_json(_state_path()).get("locale")
        cn_first = contactsend.region_is_cn(loc if isinstance(loc, str) else None)
        return CATALOG_URLS if cn_first else tuple(reversed(CATALOG_URLS))
    if urlparse(override).scheme != "https":
        return ()  # 只认 HTTPS（出站传输只经 tlstrust，见 tests/test_outbound_https_trust.py）
    return (override if override.endswith("/") else override + "/",)


# ---------------------------------------------------------------------------
# 验签与索引
# ---------------------------------------------------------------------------
def _b64decode(text: object) -> bytes:
    if not isinstance(text, str):
        raise ValueError("not str")
    return base64.b64decode(text.encode("ascii"), validate=True)


def _ed25519_verify(public_key: object, message: object, signature: object) -> bool:
    """Ed25519 验签；任何异常（含 `nacl` 缺失）都是 False，永不抛。公钥 32 字节、签名 64 字节。"""
    if not (
        isinstance(public_key, bytes | bytearray | memoryview)
        and isinstance(message, bytes | bytearray | memoryview)
        and isinstance(signature, bytes | bytearray | memoryview)
    ):
        return False
    pk, msg, sig = bytes(public_key), bytes(message), bytes(signature)
    if len(pk) != 32 or len(sig) != 64:
        return False
    try:
        # 延后：Flask 父进程启动不为原生轮子付代价；只在拉索引验签时才加载
        from nacl.signing import VerifyKey

        # libsodium 的 crypto_sign_verify_detached：拒 S >= L 的可塑性签名与小阶 / 非规范公钥点
        VerifyKey(pk).verify(msg, sig)
    except Exception:  # noqa: BLE001 - BadSignatureError / ValueError / TypeError / CryptoError / ImportError 等一律验签失败
        return False
    return True


def verify_envelope(raw: bytes) -> dict:
    """验签并返回索引（已按结构校验）。失败抛 `RemoteCatalogError`（签名缺失 / 无效 / 钥未知 / 索引畸形）。"""
    try:
        env = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteCatalogError("index_invalid", "索引信封不是 JSON") from exc
    if not isinstance(env, dict):
        raise RemoteCatalogError("index_invalid", "索引信封不是对象")
    if not env.get("sig") or not env.get("payload"):
        raise RemoteCatalogError("signature_missing", "索引没有签名")
    if env.get("alg") != "ed25519":
        raise RemoteCatalogError("signature_invalid", "不支持的签名算法")
    key_b64 = TRUSTED_KEYS.get(env["key_id"]) if isinstance(env.get("key_id"), str) else None
    if key_b64 is None:
        raise RemoteCatalogError("key_unknown", "签名钥不在内置公钥里")
    try:
        payload, sig, key = _b64decode(env["payload"]), _b64decode(env["sig"]), _b64decode(key_b64)
    except (ValueError, binascii.Error) as exc:
        raise RemoteCatalogError("signature_invalid", "签名或载荷不是合法 base64") from exc
    if not _ed25519_verify(key, payload, sig):
        raise RemoteCatalogError("signature_invalid", "签名不匹配")
    try:
        index = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteCatalogError("index_invalid", "索引载荷不是 JSON") from exc
    return _check_index(index)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _bad(msg: str) -> RemoteCatalogError:
    return RemoteCatalogError("index_invalid", msg)


def _check_index(m: object) -> dict:
    if not isinstance(m, dict) or m.get("schema") != 1 or m.get("kind") != INDEX_KIND:
        raise _bad("索引 schema / kind 不对")
    if not _is_int(m.get("catalog_rev")) or m["catalog_rev"] < 1:
        raise _bad("catalog_rev 必须是正整数")
    if not isinstance(m.get("issued"), str) or not _DATE_RE.fullmatch(m["issued"]):
        raise _bad("issued 不是日期")
    pubs, jrs = m.get("publishers"), m.get("journals")
    if not isinstance(pubs, dict) or not isinstance(jrs, dict):
        raise _bad("缺 publishers / journals")
    for pid, p in pubs.items():
        if (
            not _PID_RE.fullmatch(pid)
            or not isinstance(p, dict)
            or set(p) != {"label"}
            or not isinstance(p["label"], str)
        ):
            raise _bad("出版社条目不合法")
    for jid, j in jrs.items():
        b = j.get("bundle") if isinstance(j, dict) else None
        if (
            not _JID_RE.fullmatch(jid)
            or not isinstance(b, dict)
            or set(b) != {"sha256", "bytes"}
            or not isinstance(b["sha256"], str)
            or not _SHA_RE.fullmatch(b["sha256"])
            or not _is_int(b["bytes"])
            or not 0 < b["bytes"] <= MAX_BUNDLE_BYTES
            or j.get("publisher") not in pubs
            or not isinstance(j.get("label"), str)
        ):
            raise _bad("期刊条目不合法")
    return m


def check_bundle(index: dict, jid: str, raw: bytes) -> dict:
    """刊包字节对索引给出的哈希与大小；通过后解析并检查形状。"""
    entry = index["journals"].get(jid)
    if entry is None:
        raise RemoteCatalogError("unknown_journal", "索引里没有这本刊")
    want = entry["bundle"]
    if len(raw) != want["bytes"] or hashlib.sha256(raw).hexdigest() != want["sha256"]:
        raise RemoteCatalogError("bundle_invalid", "刊包大小或哈希与签名索引不符")
    try:
        b = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteCatalogError("bundle_invalid", "刊包不是 JSON") from exc
    if (
        not isinstance(b, dict)
        or set(b) != {"journal", "journal_id", "publisher", "publisher_id"}
        or b["journal_id"] != jid
        or b["publisher_id"] != entry["publisher"]
        or not isinstance(b["journal"], dict)
        or not isinstance(b["publisher"], dict)
        or b["journal"].get("publisher") != b["publisher_id"]
    ):
        raise RemoteCatalogError("bundle_invalid", "刊包形状不对")
    return b


def seed_catalog() -> dict:
    try:
        return journals.load_catalog()
    except Exception:  # noqa: BLE001 — 种子读不到不该挡住远程目录
        return {"publishers": {}, "journals": {}}


def assemble(index: dict, bundles: dict[str, dict]) -> dict:
    """种子 + 索引骨架（只有名称 / ISSN / 别名的刊，没有任何规则）+ 已下载刊包 → 完整目录，整份校验。

    种子里有的刊以种子为准（不需要下载、不计数）；已下载的刊包覆盖骨架。校验不过抛 `catalog_invalid`。"""
    seed = seed_catalog()
    pubs: dict = {pid: dict(p) for pid, p in index["publishers"].items()}
    jrs: dict = {}
    for jid, e in index["journals"].items():
        j = {"label": e["label"], "publisher": e["publisher"]}
        for k in ("issn", "aliases"):
            if k in e:
                j[k] = e[k]
        jrs[jid] = j
    pubs.update(seed.get("publishers", {}))
    jrs.update(seed.get("journals", {}))
    for jid, b in bundles.items():
        pubs[b["publisher_id"]] = b["publisher"]
        jrs[jid] = b["journal"]
    catalog = {
        "schema": journals.SCHEMA,
        "catalog_rev": index["catalog_rev"],
        "publishers": pubs,
        "journals": jrs,
    }
    try:
        return journals.validate_catalog(catalog)
    except journals.JournalCatalogError as exc:
        raise RemoteCatalogError("catalog_invalid", f"{exc.code}: {exc}") from exc


# ---------------------------------------------------------------------------
# 本地缓存（验证过的才落盘；读出来时再验一遍，坏了就当没有）
# ---------------------------------------------------------------------------
def _state_path() -> Path:
    return cache_dir() / "state.json"


def _read_json(path: Path) -> dict:
    try:
        v = json.loads(path.read_text("utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _dir_lock(timeout_s: float = 10.0):
    """跨进程互斥（app 与 Agent 宿主共用同一份缓存目录）：`O_EXCL` 建锁文件，持锁超过 `LOCK_STALE_S` 视为遗弃可抢。
    拿不到也**不阻塞业务**：超时后照常往下走（写入本身都是原子替换，最坏是多领一枚令牌 / 状态晚一拍）。"""
    path = cache_dir() / "lock"
    deadline = time.monotonic() + timeout_s
    held = False
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        while True:
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                held = True
                break
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > LOCK_STALE_S:
                        path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
    except OSError:
        pass
    try:
        yield
    finally:
        if held:
            path.unlink(missing_ok=True)


def _write_state(patch: dict) -> None:
    try:
        with _dir_lock():
            atomicio.write_json(_state_path(), {**_read_json(_state_path()), **patch})
    except OSError as exc:
        LOG.info("期刊目录状态写不下: %s", type(exc).__name__)


def seed_rev() -> int:
    try:
        return journals.catalog_rev()
    except Exception:  # noqa: BLE001
        return 0


def rev_floor() -> int:
    """接受的最低 `catalog_rev`：历史上接受过的最高值与内置种子的版本取大——低于它就是倒退。"""
    seen = _read_json(_state_path()).get("max_rev")
    return max(seed_rev(), seen if _is_int(seen) else 0)


class Cached:
    def __init__(self, index: dict, bundles: dict[str, dict]):
        self.index, self.bundles = index, bundles
        self.catalog = assemble(index, bundles)

    @property
    def rev(self) -> int:
        return self.index["catalog_rev"]

    def loaded_ids(self) -> list[str]:
        """已经能直接用的刊（种子里的 + 已下载的）。其余在选择器里可见，选中时才下载。"""
        seed = set(seed_catalog().get("journals", {}))
        return sorted(seed | set(self.bundles))


def load_cached() -> Cached | None:
    """读缓存：索引重新验签、刊包重算哈希、整份重新校验；索引坏了当没有缓存，单个刊包坏了只丢那一本。"""
    try:
        index = verify_envelope((cache_dir() / INDEX_NAME).read_bytes())
    except (OSError, RemoteCatalogError):
        return None
    bundles = bundles_on_disk(index)
    try:
        return Cached(index, bundles)
    except RemoteCatalogError:
        # 单个刊包与种子 / 骨架拼不起来：退回只有骨架
        try:
            return Cached(index, {})
        except RemoteCatalogError:
            return None


def bundles_on_disk(index: dict) -> dict[str, dict]:
    """本地已有、且与这份索引给出的哈希一致的刊包（哈希对不上的一律当没有）。"""
    out: dict[str, dict] = {}
    for jid, e in index["journals"].items():
        try:
            raw = (cache_dir() / "bundles" / f"{e['bundle']['sha256']}.json").read_bytes()
            out[jid] = check_bundle(index, jid, raw)
        except (OSError, RemoteCatalogError):
            continue
    return out


def _prune(index: dict) -> None:
    keep = {f"{e['bundle']['sha256']}.json" for e in index["journals"].values()}
    for old in (cache_dir() / "bundles").glob("*.json"):
        if old.name not in keep:
            old.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def _user_agent() -> str:
    from . import updater  # 延后：updater 的 import 链更长

    return f"Tavotto/{updater.current_version()}"


def _http(method: str, url: str, cap: int, headers: dict[str, str]) -> tuple[int, bytes]:
    """一次请求 → (状态码, 至多 cap 字节)。HTTPS 经 `tlstrust`，不带 cookie / 查询串以外的东西；传输失败抛 `network`。"""
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": _user_agent(),
            "Accept": "*/*",
            **headers,
        },
        data=b"" if method == "POST" else None,
    )
    opener = urllib.request.build_opener(tlstrust.https_handler(tlstrust.client_context()))
    try:
        with opener.open(req, timeout=NETWORK_TIMEOUT_S) as resp:
            status, data = resp.status, resp.read(cap + 1)
    except urllib.error.HTTPError as exc:
        status, data = exc.code, exc.read(65536)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RemoteCatalogError("network", type(exc).__name__) from exc
    if status == 200 and len(data) > cap:
        raise RemoteCatalogError("bundle_invalid", "响应超过体积上限")
    return status, data


#: 测试钩子：`(method, url, cap, headers) -> (status, body)`
Requester = Callable[[str, str, int, dict[str, str]], tuple[int, bytes]]
_request: Requester = _http


_CLIENT_OVERRIDE: str | None = None


def set_client_type(value: str | None) -> None:
    """宿主进程（MCP 桥）自报客户端类型 `agent-<宿主名>`；不合法的忽略。环境变量 `TAVOTTO_JOURNAL_CLIENT` 优先于它。"""
    global _CLIENT_OVERRIDE
    _CLIENT_OVERRIDE = value if isinstance(value, str) and _CLIENT_RE.fullmatch(value) else None


def client_type() -> str:
    """`app`，或 Agent 宿主自报的 `agent-<宿主名>`（环境变量 `TAVOTTO_JOURNAL_CLIENT` 优先，其次 `set_client_type`）；
    不合法一律按 `app`。"""
    raw = (os.environ.get(CLIENT_ENV) or "").strip().lower()
    if _CLIENT_RE.fullmatch(raw):
        return raw
    return _CLIENT_OVERRIDE or CLIENT_TYPE


def _call(method: str, url: str, cap: int, headers: dict[str, str]) -> tuple[int, bytes]:
    """所有出站请求的唯一入口：补上客户端类型头（只用于服务端统计，不是身份）。"""
    return _request(method, url, cap, {"X-Tavotto-Client": client_type(), **headers})


def _error_of(status: int, body: bytes) -> RemoteCatalogError:
    """把服务端的错误体（`{"code", "retry_after_s"?}`）翻成闭集错误；形状不对一律 `server_error`。"""
    try:
        doc = json.loads(body.decode("utf-8"))
        code, retry = doc.get("code"), doc.get("retry_after_s")
    except (UnicodeDecodeError, ValueError, AttributeError):
        code, retry = None, None
    if status == 429 and code in QUOTA_CODES:
        return RemoteCatalogError(
            code, retry_after_s=retry if _is_int(retry) and retry >= 0 else None
        )
    if status == 401 and code == "device_unknown":
        return RemoteCatalogError("device_unknown")
    if status == 404 and code in ("stale_rev", "no_such_journal"):
        return RemoteCatalogError("stale_rev" if code == "stale_rev" else "unknown_journal")
    return RemoteCatalogError("server_error", f"HTTP {status}")


# ---------------------------------------------------------------------------
# 设备令牌
# ---------------------------------------------------------------------------
def _device_path() -> Path:
    return cache_dir() / "device.json"


def _stored_token() -> str | None:
    tok = _read_json(_device_path()).get("token")
    return tok if isinstance(tok, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", tok) else None


def _issue_token(base: str) -> str:
    status, body = _call("POST", base + "device", 4096, {})
    if status != 200:
        raise _error_of(status, body)
    try:
        tok = json.loads(body.decode("utf-8")).get("token")
    except (UnicodeDecodeError, ValueError, AttributeError):
        tok = None
    if not isinstance(tok, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", tok):
        raise RemoteCatalogError("server_error", "令牌形状不对")
    cache_dir().mkdir(parents=True, exist_ok=True)
    atomicio.write_bytes(_device_path(), json.dumps({"token": tok}).encode("utf-8"), mode=0o600)
    return tok


def _token(base: str, *, renew: bool = False, stale: str | None = None) -> str:
    """共享令牌：谁先用到谁领，后来的一方（app / Agent）直接读盘复用。领取在目录锁里**先重读再签发**，
    两个进程同时需要时只有一个去签发。`renew` 时若盘上的已不是被服务端拒掉的 `stale`，说明对方刚换过，直接用新的。"""
    tok = _stored_token()
    if tok and not renew:
        return tok
    with _dir_lock():
        tok = _stored_token()
        if tok and tok != stale and (not renew or stale is not None):
            return tok
        return _issue_token(base)


# ---------------------------------------------------------------------------
# 索引同步（整份、不计数）
# ---------------------------------------------------------------------------
def _result(fetch: str, have: int, code: str | None = None, *, cache: Cached | None = None) -> dict:
    cache = cache if cache is not None else load_cached()
    rev = cache.rev if cache else None
    return {
        "fetch": fetch,
        "code": code,
        "rev": rev,
        # 目录本体在缓存比调用方手里的新时随回复发出（前端空缓存 have=0 时取回缓存）；缓存里有已下载的刊包时也带——
        # 刊包是 Agent / 另一个标签页随时可能下载的，`catalog_rev` 不变而内容变了，调用方凭 `have` 看不出来，
        # 而 `loaded` 只许报调用方已拿到内容的刊（否则前端会把骨架当成可用的标准）
        "catalog": cache.catalog if cache and (rev > have or cache.bundles) else None,
        "loaded": cache.loaded_ids() if cache else [],
    }


def _due(now: float) -> bool:
    st = _read_json(_state_path())
    last, ok = st.get("last_attempt"), st.get("last_ok")
    if not isinstance(last, (int, float)):
        return True
    return now - last >= (SYNC_INTERVAL_S if ok == last else RETRY_AFTER_FAILURE_S)


def remember_locale(ui_locale: object) -> None:
    """记下界面语言（只存 `zh-CN` / `en-US` 这类短码），让没有语言信息的调用（快照取图、Agent）也按同一判据选先后。"""
    if isinstance(ui_locale, str) and re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", ui_locale):
        if _read_json(_state_path()).get("locale") != ui_locale:
            _write_state({"locale": ui_locale})


def sync(
    *, have: int = 0, force: bool = False, now: float | None = None, ui_locale: str | None = None
) -> dict:
    """拉一次索引并回报：`{fetch, code, rev, catalog, loaded}`。

    `fetch` ∈ updated / unchanged / offline / rejected / disabled / throttled。**永不抛**；
    不论结果如何，`catalog` 都是此刻能用的最好一份（新拉到的 → 缓存），没有则 None（前端退回种子）。
    """
    now = time.time() if now is None else now
    with _LOCK:
        if disabled():
            return _result("disabled", have, "disabled")
        if not force and not _due(now):
            return _result("throttled", have)
        last_err: RemoteCatalogError | None = None
        remember_locale(ui_locale)
        for base in base_urls(ui_locale):
            try:
                out = _sync_index(base, have)
            except RemoteCatalogError as exc:
                last_err = exc
                continue
            _write_state({"last_attempt": now, "last_ok": now})
            return out
        _write_state({"last_attempt": now, "last_ok": None})
        code = last_err.code if last_err else "network"
        LOG.info("期刊索引同步未生效（%s）", code)
        return _result("offline" if code in ("network", "server_error") else "rejected", have, code)


def _fetch_index(base: str) -> tuple[bytes, dict]:
    status, raw = _call("GET", base + INDEX_NAME, MAX_INDEX_BYTES, {})
    if status != 200:
        raise _error_of(status, raw)
    index = verify_envelope(raw)
    if index["catalog_rev"] < rev_floor():
        raise RemoteCatalogError(
            "rev_rollback", f"catalog_rev {index['catalog_rev']} < {rev_floor()}"
        )
    return raw, index


def _sync_index(base: str, have: int) -> dict:
    raw, index = _fetch_index(base)
    cached = load_cached()
    if cached is not None and cached.rev == index["catalog_rev"]:
        return _result("unchanged", have, cache=cached)
    # 新版本：哈希仍然匹配的已下载刊包原样保留（不必重拉）；整份拼好校验通过才落盘
    try:
        candidate = Cached(index, bundles_on_disk(index))
    except RemoteCatalogError:
        candidate = Cached(index, {})
    cache_dir().mkdir(parents=True, exist_ok=True)
    atomicio.write_bytes(cache_dir() / INDEX_NAME, raw)
    _write_state({"max_rev": max(rev_floor(), index["catalog_rev"])})
    _prune(index)
    return _result("updated", have, cache=candidate)


# ---------------------------------------------------------------------------
# 刊包按需下载（计数发生在服务端；这里只带 id、令牌、版本号）
# ---------------------------------------------------------------------------
def ensure_journal(jid: str, *, now: float | None = None, ui_locale: str | None = None) -> dict:
    """保证 `jid` 的标准在本地可用。永不抛。

    `{state, code, retry_after_s, bundle}`：state ∈ ready（`bundle` 给前端并入目录；已在本地的也是 ready）/
    quota（今天的额度用完，`retry_after_s` 是建议等待秒数）/ offline（没网又没缓存，或开关关着）/
    unknown（索引里没有这本刊）/ rejected（签名、哈希或校验不过）。
    """
    if not isinstance(jid, str) or not _JID_RE.fullmatch(jid):
        return _ensure_out("unknown", "unknown_journal")
    with _LOCK:
        cached = load_cached()
        if cached is not None and jid in cached.bundles:
            return _ensure_out("ready", bundle=cached.bundles[jid])
        if jid in seed_catalog().get("journals", {}):
            return _ensure_out("ready")  # 种子里的刊：随包带着，不下载、不计数
        if cached is None or jid not in cached.index["journals"]:
            if disabled():
                # 同步关着 = 能用的目录只有内置种子，而它里面没有这个 id：就是「不认识」，不是「没网」
                return _ensure_out("unknown", "unknown_journal")
            if cached is None:
                return _ensure_out("offline", "network")
            return _ensure_out("unknown", "unknown_journal")
        if disabled():
            return _ensure_out("offline", "disabled")
        last: RemoteCatalogError | None = None
        remember_locale(ui_locale)
        for base in base_urls(ui_locale):
            try:
                bundle = _download_bundle(base, cached.index, jid)
                return _ensure_out("ready", bundle=bundle)
            except RemoteCatalogError as exc:
                last = exc
                if exc.code in QUOTA_CODES:
                    break
        code = last.code if last else "network"
        retry = last.retry_after_s if last else None
        if code in QUOTA_CODES:
            return _ensure_out("quota", code, retry)
        if code in ("network", "server_error"):
            return _ensure_out("offline", code)
        return _ensure_out("rejected", code)


def _ensure_out(
    state: str, code: str | None = None, retry: int | None = None, *, bundle: dict | None = None
) -> dict:
    return {"state": state, "code": code, "retry_after_s": retry, "bundle": bundle}


def _download_bundle(base: str, index: dict, jid: str) -> dict:
    rev = index["catalog_rev"]
    entry = index["journals"][jid]["bundle"]
    url = f"{base}journals/{quote(jid, safe='')}?rev={rev}"
    renewed = False
    resynced = False
    fresh_index_raw: bytes | None = None
    while True:
        tok = _token(base)
        status, body = _call("GET", url, entry["bytes"], {"X-Tavotto-Device": tok})
        if status == 200:
            break
        err = _error_of(status, body)
        if err.code == "device_unknown" and not renewed:
            renewed = True
            _token(
                base, renew=True, stale=tok
            )  # 服务端不认旧令牌：换一枚新的再试一次（对方已换过则直接用）
            continue
        if err.code == "stale_rev" and not resynced:
            # 服务端已换了版本：重取索引（不计数），刊包哈希可能变了，用新索引再试一次。
            # 新索引只暂存：整份候选目录（含这次的刊包）校验通过后才替换旧缓存，不过则旧缓存原样
            resynced = True
            raw, index = _fetch_index(base)
            fresh_index_raw = raw
            rev, entry = index["catalog_rev"], index["journals"].get(jid, {}).get("bundle")
            if entry is None:
                raise RemoteCatalogError("unknown_journal") from err
            url = f"{base}journals/{quote(jid, safe='')}?rev={rev}"
            continue
        raise err
    bundle = check_bundle(index, jid, body)
    assemble(
        index, {**bundles_on_disk(index), jid: bundle}
    )  # 拼进整份目录再校验一遍；不过则整包作废、不落盘
    if fresh_index_raw is not None:
        # 候选目录校验已过：现在才替换旧索引（并清掉新索引不再引用的旧刊包）
        cache_dir().mkdir(parents=True, exist_ok=True)
        atomicio.write_bytes(cache_dir() / INDEX_NAME, fresh_index_raw)
        _prune(index)
    d = cache_dir() / "bundles"
    d.mkdir(parents=True, exist_ok=True)
    atomicio.write_bytes(d / f"{entry['sha256']}.json", body)
    _write_state({"max_rev": max(rev_floor(), rev)})
    return bundle


# ---------------------------------------------------------------------------
# Agent 入口（MCP / 宿主插件）：与 app 同一份令牌、缓存与限额（ADR 0155 §10.1）
# ---------------------------------------------------------------------------
def agent_catalog(*, ui_locale: str | None = None) -> dict | None:
    """列期刊用：先同步索引（节流、不计数；关着 / 离线都静默），再回缓存里「种子 + 索引骨架 + 已下载刊包」的完整目录；
    没有缓存返回 None（调用方用内置种子）。读目录不计数、离线可用。"""
    if not disabled():
        sync(ui_locale=ui_locale)
    cached = load_cached()
    return cached.catalog if cached else None


def loaded_ids() -> list[str]:
    """已经能直接用（种子里的 + 已下载的）的刊 id；没有缓存时为空。"""
    cached = load_cached()
    return cached.loaded_ids() if cached else []


def agent_resolve(jid: str, *, ui_locale: str | None = None) -> dict:
    """Agent 要用某本刊：保证它的标准在本地，回 `{state, code, retry_after_s, catalog}`。

    `state` 同 `ensure_journal`（ready / quota / offline / unknown / rejected）；`catalog` 是此刻能用的完整目录
    （含刚下载的这本），没有缓存时为 None（调用方用种子）。本地已有的刊不发请求、服务端不计数。
    永不抛；调用方必须把 quota / offline / rejected 当**明确的错误**报给 Agent，不许静默退回别的数据。"""
    if not disabled() and load_cached() is None:
        sync(ui_locale=ui_locale)  # 第一次用：先取索引（不计数）
    out = ensure_journal(jid, ui_locale=ui_locale)
    cached = load_cached()
    return {**out, "catalog": cached.catalog if cached else None}


# ---------------------------------------------------------------------------
# 快照（出处卡的高亮短图；内容寻址，哈希必须等于文件名）
# ---------------------------------------------------------------------------
def snapshot_path(file: str) -> Path | None:
    """本地快照文件路径；没有就带令牌拉一次（离线 / 被拒 / 哈希不符 = None，出处卡退回摘录框）。"""
    m = _SNAPSHOT_RE.fullmatch(file) if isinstance(file, str) else None
    if m is None:
        return None
    path = cache_dir() / file
    if path.is_file():
        return path
    if disabled() or load_cached() is None:
        return None
    with _LOCK:
        now = time.time()
        if now - _SNAPSHOT_FAILED.get(file, 0.0) < SNAPSHOT_RETRY_S:
            return None
        for base in base_urls():
            try:
                tok = _token(base)
                status, data = _call(
                    "GET", f"{base}{file}", MAX_SNAPSHOT_BYTES, {"X-Tavotto-Device": tok}
                )
                if status == 200 and hashlib.sha256(data).hexdigest() == m.group(1):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomicio.write_bytes(path, data)
                    return path
            except RemoteCatalogError as exc:
                LOG.info("快照未取到（%s）", exc.code)
        _SNAPSHOT_FAILED[file] = now
        return None
