"""期刊目录服务的全部业务逻辑（线协议 v1），与平台无关。

输入是 (方法, 路径, 查询串, 请求头, 对端地址)，输出是 (状态码, 头, 字节)。
时间由 `clock` 注入；日志里不写令牌、IP、刊 id（本模块根本不打日志）。
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import parse_qs

from .facts import FactValidationError, decode_bundle
from .store import RULE_NAMES, Limits, Store

RULES_TTL_S = 10  # 规则表读缓存：改了规则最多这么久生效
PREFIX = "/v1/journal-catalog"
PREFIX_V2 = "/v2/journal-catalog"
DEVICE_HEADER = "x-tavotto-device"
JOURNAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")
REV_RE = re.compile(r"^[1-9][0-9]{0,9}$")
CLIENT_RE = re.compile(r"^(app|agent-[a-z0-9][a-z0-9-]{0,31})$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

Response = tuple[int, list[tuple[str, str]], bytes]


class ConfigError(RuntimeError):
    """配置缺失或非法：拒绝启动。"""


@dataclass(frozen=True)
class Config:
    data_dir: Path
    db_path: Path
    ip_secret: bytes
    limits: Limits = field(default_factory=Limits)
    # "direct" = 直连 REMOTE_ADDR；"xff_rightmost" = 受信任代理的 X-Forwarded-For 最右一段
    ip_mode: str = "direct"
    # 海外入口（tavotto.com 上的 Vercel 反代）与本服务共用密钥：请求带正确的 `X-Tavotto-Proxy-Secret` 才采信
    # `X-Tavotto-Client-IP` 里的真实客户端 IP；密钥为空 = 不接受任何反代声明的 IP
    proxy_secret: bytes = b""

    def __post_init__(self) -> None:
        if not self.ip_secret:
            raise ConfigError("缺少 IP 哈希密钥")
        if self.ip_mode not in ("direct", "xff_rightmost"):
            raise ConfigError("ip_mode 只能是 direct 或 xff_rightmost")


def config_from_env(env: Mapping[str, str]) -> Config:
    secret = env.get("TAVOTTO_JC_IP_SECRET", "")
    if not secret:
        raise ConfigError("缺少环境变量 TAVOTTO_JC_IP_SECRET，拒绝启动")
    data = env.get("TAVOTTO_JC_DATA", "")
    db = env.get("TAVOTTO_JC_DB", "")
    if not data or not db:
        raise ConfigError("缺少环境变量 TAVOTTO_JC_DATA / TAVOTTO_JC_DB，拒绝启动")
    d = Limits()
    try:
        limits = Limits(
            ip_tokens_per_day=int(env.get("TAVOTTO_JC_IP_TOKENS_PER_DAY") or d.ip_tokens_per_day),
            reads_per_hour=int(env.get("TAVOTTO_JC_READS_PER_HOUR") or d.reads_per_hour),
            reads_per_day=int(env.get("TAVOTTO_JC_READS_PER_DAY") or d.reads_per_day),
            ip_reads_per_day=int(env.get("TAVOTTO_JC_IP_READS_PER_DAY") or d.ip_reads_per_day),
        )
    except ValueError as exc:
        raise ConfigError("限额配置必须是整数") from exc
    return Config(
        Path(data),
        Path(db),
        secret.encode("utf-8"),
        limits,
        env.get("TAVOTTO_JC_IP_MODE") or "direct",
        env.get("TAVOTTO_JC_PROXY_SECRET", "").encode("utf-8"),
    )


def ip_key(secret: bytes, ip: str | None) -> str:
    """HMAC-SHA256(密钥, IPv4 全址 / IPv6 的 /64 前缀)。无法解析的地址归到同一个桶。"""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if isinstance(addr, ipaddress.IPv6Address):
            norm = str(ipaddress.ip_network(f"{addr}/64", strict=False))
        else:
            norm = str(addr)
    except ValueError:
        norm = "unparseable"
    return hmac.new(secret, norm.encode("ascii"), hashlib.sha256).hexdigest()


def _normalise_ip(ip: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


@dataclass(frozen=True)
class Rules:
    """某一刻的规则快照（来自规则表）。"""

    defaults: dict
    kill_switch: bool
    # (网段, 完全豁免, 令牌上限 | None, 读取上限 | None)
    cidrs: tuple

    def limits_for(self, base: Limits, plain_ip: str | None) -> Limits:
        """有效限额。**用明文 IP 在内存里比对网段**（在 HMAC 之前；明文 IP 不落库、不进日志）。
        顺序：规则表默认值覆盖环境变量 / 内置默认；紧急开关 → IP 维度全不拦；命中网段（最长前缀优先）→ 豁免或放宽。"""
        eff = replace(base, **{k: v for k, v in self.defaults.items() if k in RULE_NAMES})
        if self.kill_switch:
            return replace(eff, ip_tokens_per_day=None, ip_reads_per_day=None)
        addr = _normalise_ip(plain_ip)
        if addr is None:
            return eff
        best = None
        for net, exempt, tokens, reads in self.cidrs:
            if net.version == addr.version and addr in net:
                if best is None or net.prefixlen > best[0].prefixlen:
                    best = (net, exempt, tokens, reads)
        if best is None:
            return eff
        _, exempt, tokens, reads = best
        if exempt:
            return replace(eff, ip_tokens_per_day=None, ip_reads_per_day=None)
        return replace(
            eff,
            ip_tokens_per_day=tokens if tokens is not None else eff.ip_tokens_per_day,
            ip_reads_per_day=reads if reads is not None else eff.ip_reads_per_day,
        )


def load_rules(store: Store) -> Rules:
    raw = store.load_rules()
    nets = []
    for cidr, exempt, tokens, reads in raw["cidrs"]:
        try:
            nets.append((ipaddress.ip_network(cidr, strict=False), exempt, tokens, reads))
        except ValueError:
            continue  # 表里的脏行不影响其余规则
    return Rules(raw["defaults"], raw["kill_switch"], tuple(nets))


def parse_client(value: str | None) -> str:
    """X-Tavotto-Client：缺省 app；不合法记 unknown，不报错。只用于统计，限额不分桶。"""
    if value is None or value == "":
        return "app"
    return value if CLIENT_RE.fullmatch(value) else "unknown"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json(status: int, body: dict) -> Response:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    return status, [("Content-Type", "application/json"), ("Cache-Control", "no-store")], payload


def _err(status: int, code: str, retry_after_s: int | None = None) -> Response:
    body: dict = {"code": code}
    if retry_after_s is not None:
        body["retry_after_s"] = retry_after_s
    status, headers, payload = _json(status, body)
    if retry_after_s is not None:
        headers.append(("Retry-After", str(retry_after_s)))
    return status, headers, payload


class Service:
    def __init__(
        self,
        config: Config,
        store: Store | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.store = store or Store(config.db_path)
        self.clock = clock
        self._data_root = config.data_dir.resolve()
        self._rules: Rules | None = None
        self._rules_at = 0.0
        self._rules_lock = threading.Lock()

    def rules(self, now: float | None = None) -> Rules:
        """规则快照，短 TTL 缓存（`RULES_TTL_S`）。读失败（库暂时锁住等）沿用旧快照，没有旧的就当空规则。"""
        t = self.clock() if now is None else now
        cur = self._rules
        if cur is not None and 0 <= t - self._rules_at < RULES_TTL_S:
            return cur
        with self._rules_lock:
            try:
                self._rules = load_rules(self.store)
            except Exception:  # noqa: BLE001 - 规则读不到不能让整个服务 500
                if self._rules is None:
                    self._rules = Rules({}, False, ())
            self._rules_at = t
            return self._rules

    def _limits_for(self, plain_ip: str | None) -> Limits:
        return self.rules().limits_for(self.config.limits, plain_ip)

    def _dev_tag(self, token_hash: str) -> str:
        return hmac.new(
            self.config.ip_secret, b"dev:" + token_hash.encode("ascii"), hashlib.sha256
        ).hexdigest()[:16]

    # ---- 工具 ----
    def client_ip(self, remote_addr: str | None, h: Mapping[str, str | None]) -> str | None:
        """限额按哪个 IP 算。顺序：① 带**共享密钥头**的反代请求，用它声明的真实客户端 IP
        （密钥错 / 缺 / IP 头不是合法地址 → 忽略这个声明，**不是**报错，按下面算）；② `xff_rightmost` 模式取
        X-Forwarded-For 最右一段；③ 连接地址。没有密钥的客户端伪造 `X-Tavotto-Client-IP` 一律无效。"""
        secret = self.config.proxy_secret
        if secret:
            given = (h.get("x-tavotto-proxy-secret") or "").encode("utf-8")
            if hmac.compare_digest(given, secret):
                claimed = (h.get("x-tavotto-client-ip") or "").strip()
                try:
                    ipaddress.ip_address(claimed)
                    return claimed
                except ValueError:
                    pass
        xff = h.get("x-forwarded-for")
        if self.config.ip_mode == "xff_rightmost" and xff:
            return xff.split(",")[-1].strip() or remote_addr
        return remote_addr

    def _inside_data(self, path: Path) -> Path | None:
        """断言 resolve 后仍在 DATA 下；否则当作不存在。"""
        try:
            real = path.resolve()
        except OSError:
            return None
        if self._data_root != real and self._data_root not in real.parents:
            return None
        return real

    def purge(self, now: float | None = None) -> dict[str, int]:
        return self.store.purge(int(self.clock() if now is None else now))

    # ---- 入口 ----
    def handle(
        self,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str | None],
        remote_addr: str | None,
    ) -> Response:
        if path == PREFIX or path.startswith(PREFIX + "/"):
            prefix, data_dir, facts_v2 = PREFIX, self.config.data_dir, False
        elif path == PREFIX_V2 or path.startswith(PREFIX_V2 + "/"):
            prefix, data_dir, facts_v2 = PREFIX_V2, self.config.data_dir / "v2", True
        else:
            return _err(404, "not_found")
        route = path[len(prefix) :]
        h = {k.lower(): v for k, v in headers.items()}
        now = int(self.clock())

        if route == "/index.signed.json":
            if method != "GET":
                return _err(405, "method_not_allowed")
            return self._file(
                data_dir / "index.signed.json",
                "application/json",
                "public, max-age=300",
                missing=(503, "index_unavailable"),
            )
        if route == "/device":
            if method != "POST":
                return _err(405, "method_not_allowed")
            plain = self.client_ip(remote_addr, h)
            key = ip_key(self.config.ip_secret, plain)
            token = secrets.token_urlsafe(24)
            ok, retry = self.store.issue_device(
                _hash_token(token),
                key,
                now,
                self._limits_for(plain),
                parse_client(h.get("x-tavotto-client")),
                self._dev_tag(_hash_token(token)),
            )
            if not ok:
                return _err(429, "ip_tokens", retry)
            return _json(200, {"token": token})
        if route.startswith("/journals/"):
            if method != "GET":
                return _err(405, "method_not_allowed")
            return self._journal(
                route[len("/journals/") :], query, h, remote_addr, now, data_dir, facts_v2
            )
        if route.startswith("/snapshots/"):
            if method != "GET":
                return _err(405, "method_not_allowed")
            return self._snapshot(route[len("/snapshots/") :], h, data_dir, facts_v2)
        return _err(404, "not_found")

    def _device_ok(self, h: Mapping[str, str | None]) -> str | None:
        token = h.get(DEVICE_HEADER) or ""
        if not token or len(token) > 256:
            return None
        th = _hash_token(token)
        return th if self.store.has_device(th) else None

    def _journal(
        self, jid: str, query: str, h, remote_addr, now: int, data_dir: Path, facts_v2: bool
    ) -> Response:
        revs = parse_qs(query, keep_blank_values=True).get("rev", [])
        if not JOURNAL_ID_RE.fullmatch(jid) or len(revs) != 1 or not REV_RE.fullmatch(revs[0]):
            return _err(400, "bad_request")
        th = self._device_ok(h)
        if th is None:
            return _err(401, "device_unknown")
        rev_dir = self._inside_data(data_dir / f"r{int(revs[0])}" / "journals")
        if rev_dir is None or not rev_dir.is_dir():
            return _err(404, "stale_rev")
        target = self._inside_data(rev_dir / f"{jid}.json")
        if target is None or not target.is_file():
            return _err(404, "no_such_journal")
        try:
            body = target.read_bytes()
        except OSError:
            return _err(404, "no_such_journal")
        if facts_v2:
            try:
                fact_bundle = decode_bundle(body, require_verified=True)
                if fact_bundle["journal_id"] != jid:
                    return _err(503, "catalog_invalid")
            except FactValidationError:
                return _err(503, "catalog_invalid")
        plain = self.client_ip(remote_addr, h)
        key = ip_key(self.config.ip_secret, plain)
        result, retry = self.store.first_read(
            th,
            jid,
            key,
            now,
            self._limits_for(plain),
            parse_client(h.get("x-tavotto-client")),
            self._dev_tag(th),
        )
        if result == "unknown":
            return _err(401, "device_unknown")
        if result in ("quota_hour", "quota_day", "ip_day"):
            return _err(429, result, retry)
        return (
            200,
            [("Content-Type", "application/json"), ("Cache-Control", "private, no-store")],
            body,
        )

    def _snapshot(self, name: str, h, data_dir: Path, facts_v2: bool) -> Response:
        if not name.endswith(".webp") or not SHA_RE.fullmatch(name[: -len(".webp")]):
            return _err(400, "bad_request")
        if self._device_ok(h) is None:
            return _err(401, "device_unknown")
        response = self._file(
            data_dir / "snapshots" / name,
            "image/webp",
            "public, max-age=31536000, immutable",
            missing=(404, "no_such_snapshot"),
        )
        if facts_v2 and response[0] == 200 and hashlib.sha256(response[2]).hexdigest() != name[:-5]:
            return _err(503, "catalog_invalid")
        return response

    def _file(self, path: Path, ctype: str, cache: str, missing: tuple[int, str]) -> Response:
        real = self._inside_data(path)
        try:
            if real is None or not real.is_file():
                return _err(*missing)
            body = real.read_bytes()
        except OSError:
            return _err(*missing)
        return 200, [("Content-Type", ctype), ("Cache-Control", cache)], body
