"""带凭据的明文 http 请求能发往哪里：**唯一判据**（用户裁决 2026-10-25：实验室常有自己部署的模型服务器，放行局域网）。

* https 不归这里管（证书校验在 `tlstrust`）。明文 http 只放**本机回环与私网**：
  `127.0.0.1` / `::1`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`100.64.0.0/10`（CGNAT / Tailscale）、`fc00::/7`；
  其余一律拒绝——公网地址、链路本地（`169.254.0.0/16` 含云元数据 `169.254.169.254`、`fe80::/10`）、`0.0.0.0/8`、组播、保留段。
  `127.0.0.2` 这类整个 `127/8` 也不放（只认 `127.0.0.1`）。
* IP 字面量直接判。**主机名在判定时解析，所有解析结果都属于放行段才放行**（混了公网 / 链路本地 / 解析失败都拒）。
  字面量 `localhost` 不经 DNS，直接当 `127.0.0.1`。
* **连接时把解析出的 IP 钉住直连**（`pinned_connection`，`Host` 头保留原主机名）：判定与连接用同一次解析的结果，
  DNS 重绑定（判定时私网、连接时公网）到不了别处。
* `check_base_url`（内置执行器）与 `ai_catalog.url_refusal`（保存连接 / 取型号清单）都问这里，不各写一份。

纯标准库。`resolve_host` 是唯一做 DNS 的函数（测试在此替换）。
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import threading
import time
import urllib.parse

#: 明文 http 放行的网段
ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)
_LOOPBACK_IPS = (ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1"))

#: 判定结果的缓存秒数（只给「判定」用，省得每次读 capabilities 都做 DNS；连接时永远重新解析）
VERDICT_TTL_S = 30.0
_CACHE: dict[str, tuple[float, tuple[str, ...] | None]] = {}
_CACHE_LOCK = threading.Lock()


#: 云厂商的实例元数据 / 内部服务地址：**先于放行段判定、一律拒**（有的恰好落在放行段里：`fc00::/7` 内的 IPv6 元数据端点、
#: `100.64.0.0/10` 内的阿里云）。实验室网关不会在这些地址上。出处（官方文档 URL；标「未核对」的是凭记忆写的，排除方向安全所以保留）：
#: * AWS EC2 IMDS `169.254.169.254` / IPv6 `fd00:ec2::254`：https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-retrieval.html
#: * AWS ECS 任务元数据 / 凭据 `169.254.170.2`：https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html
#: * Amazon Time Sync `169.254.169.123`：https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/set-time.html （未核对）
#: * GCP `169.254.169.254` / 主机名 `metadata.google.internal` / IPv6-only 实例 `fd20:ce::254`：
#:   https://docs.cloud.google.com/compute/docs/metadata/querying-metadata
#: * Azure IMDS `169.254.169.254`：https://learn.microsoft.com/azure/virtual-machines/instance-metadata-service ；
#:   WireServer / 虚拟公网 IP `168.63.129.16`：https://learn.microsoft.com/azure/virtual-network/what-is-ip-address-168-63-129-16
#: * 阿里云 ECS `100.100.100.200`：https://www.alibabacloud.com/help/en/ecs/user-guide/view-instance-metadata
#: * Oracle OCI `169.254.169.254`：https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/gettingmetadata.htm ；
#:   `192.0.0.192` 旧版地址（未核对）；OCI 没有查到 IPv6 元数据地址的出处，不加
#: * 腾讯云 CVM 主机名 `metadata.tencentyun.com` / `169.254.0.23`（未核对）
#: * Akamai / Linode IPv6 `fd00:a9fe:a9fe::1`：https://techdocs.akamai.com/cloud-computing/docs/metadata-service-api
#: * DigitalOcean https://docs.digitalocean.com/reference/api/metadata/ ；Huawei / OpenStack `169.254.169.254`（未核对）
METADATA_IPS = frozenset(
    ipaddress.ip_address(a)
    for a in (
        "169.254.169.254",
        "169.254.170.2",
        "169.254.169.123",
        "169.254.0.23",
        "fd00:ec2::254",
        "fd20:ce::254",
        "fd00:a9fe:a9fe::1",
        "100.100.100.200",
        "168.63.129.16",
        "192.0.0.192",
    )
)
METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "metadata.tencentyun.com",
        "instance-data",
        "instance-data.ec2.internal",
    }
)


def ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped  # ::ffff:10.0.0.1 按它背后的 IPv4 判
    if ip in METADATA_IPS:
        return False
    return ip in _LOOPBACK_IPS or any(ip in net for net in ALLOWED_NETWORKS)


def resolve_host(host: str) -> list[str]:
    """主机名 → 它当前解析出的全部 IP（去重，IPv4 在前）；解析失败 = `[]`。**全进程唯一的 DNS 入口。**"""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    seen: list[str] = []
    for family, _t, _p, _c, sockaddr in infos:
        ip = str(sockaddr[0]).split("%", 1)[0]
        if ip not in seen:
            seen.append(ip)
    return sorted(seen, key=lambda a: ":" in a)


def _verdict(host: str) -> tuple[str, ...] | None:
    """→ 放行时的 IP 列表（连接时钉其中之一），拒绝 = None。每次都重新解析。"""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return None
    if host in METADATA_HOSTS:
        return None
    if host == "localhost":
        return ("127.0.0.1",)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ips = resolve_host(host)
        if not ips:
            return None
        parsed = []
        for text in ips:
            try:
                parsed.append(ipaddress.ip_address(text))
            except ValueError:
                return None
        return tuple(str(p) for p in parsed) if all(ip_allowed(p) for p in parsed) else None
    return (str(ip),) if ip_allowed(ip) else None


def plain_http_allowed(host: str) -> bool:
    """这个主机（IP 字面量 / 主机名）能不能收明文 http。主机名的结果缓存 `VERDICT_TTL_S` 秒。"""
    host = (host or "").strip().lower().rstrip(".")
    if not host or host == "localhost":
        return host == "localhost"
    if host in METADATA_HOSTS:
        return False
    try:
        return ip_allowed(ipaddress.ip_address(host))
    except ValueError:
        pass
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(host)
        if hit is not None and now - hit[0] < VERDICT_TTL_S:
            return hit[1] is not None
    got = _verdict(host)
    with _CACHE_LOCK:
        _CACHE[host] = (now, got)
    return got is not None


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def is_insecure_lan(base_url: str) -> bool:
    """明文 http 且（按此刻的判定）放行的非回环地址——界面据此提示「这个地址不加密」。回环不算（本机不出网）。"""
    try:
        u = urllib.parse.urlsplit(base_url)
        host = (u.hostname or "").lower()
    except ValueError:
        return False
    if u.scheme != "http" or not host or not plain_http_allowed(host):
        return False
    try:
        return ipaddress.ip_address(host) not in _LOOPBACK_IPS
    except ValueError:
        return host != "localhost"


def pin_url(url: str) -> tuple[str, str]:
    """http URL → `(把主机换成已校验 IP 的 URL, 原 Host 头)`；不放行 / 解析不出 → `ValueError`。给 urllib 请求用（连接用判定那次的解析）。"""
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    ips = _verdict(host)
    if u.scheme != "http" or not ips:
        raise ValueError("plain http not allowed")
    ip = ips[0]
    netloc = f"[{ip}]" if ":" in ip else ip
    if u.port:
        netloc += f":{u.port}"
    original = host if ":" not in host else f"[{host}]"
    if u.port:
        original += f":{u.port}"
    return urllib.parse.urlunsplit(("http", netloc, u.path, u.query, u.fragment)), original


def pinned_connection(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    """明文 http 的直连：先按 `_verdict` 解析并校验，再**连那个 IP**（不让 `HTTPConnection` 自己再解析一遍），
    返回 `(连接, 原 Host 头)`。不读代理设置。不放行 → `ValueError`。"""
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    ips = _verdict(host)
    if u.scheme != "http" or not ips:
        raise ValueError("plain http not allowed")
    original = host if ":" not in host else f"[{host}]"
    if u.port:
        original += f":{u.port}"
    return http.client.HTTPConnection(ips[0], u.port or 80, timeout=timeout), original
