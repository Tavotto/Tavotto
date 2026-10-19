"""本机会话凭据文件与客户端助手（纯标准库——Flask 父进程与 handoff 共用）。

浏览器模式的服务端启动时把一份**本机 API 凭据**写进数据目录
（`data_dir()/session/port-<端口>.json`，目录 0700、文件 0600）：

    {"port": 5089, "pid": 12345, "secret": "<256-bit 随机>", "created": ...}

这份文件是「同一个用户的本机进程」的身份证明——能读到它的进程本来就能读
用户的任何文件，网页（含 DNS rebinding 页面）读不到。持有 secret 的调用方
有两条路：

1. 请求头 `X-Tavotto-Auth: <secret>`——CLI / 冒烟脚本 / `tavotto open`
   直接带上即可通过认证（见 tavotto/security.py 的 guard）。
2. `POST /api/session/relaunch {"secret": ...}` 换一枚一次性 nonce，
   拼成 `http://127.0.0.1:<port>/#dnonce=<nonce>` 交给浏览器——第二次
   `tavotto` 启动「把浏览器指过去」的实例复用走的就是这条（安全的
   token 交接，不是裸探测端口）。

服务端退出时删除文件；publish 时顺手清掉遗留的陈旧文件。桌面 sidecar
**不写**这份**完整**凭据——它的端口（壳建议、sidecar 实绑）与 nonce 由 Tauri 壳经 stdin 管道 / 握手文件交接，
磁盘上不应有能换 relaunch nonce、能调任何端点的 secret。

桌面 sidecar 只写另一份**范围受限**的文件（ADR 0008 修订 2026-10-16、ADR 0131 第六节）：

    data_dir()/session/handoff-status-<端口>.json（目录 0700、文件 0600）
    {"port": ..., "pid": ..., "token": "<256-bit 随机，与 nonce / secret 无关>",
     "scope": "handoff_render_status", "created": ...}

这个 token **只**在 `GET /api/handoff/render-status` 上有效（security.py 的 guard 按路径 + 方法限定），
换不到 relaunch nonce，调任何其它端点都是 401。它的唯一用途是让 `tavotto open` 查「某项目某 stem 最近一次
渲染成功与否」。
"""

from __future__ import annotations

import http.client
import json
import os
import stat
import time

from . import config

AUTH_HEADER = "X-Tavotto-Auth"
RELAUNCH_PATH = "/api/session/relaunch"
#: 交接状态查询端点——scoped token 唯一能打的路径（security.py 的 guard 与此同源）
HANDOFF_STATUS_PATH = "/api/handoff/render-status"
STATUS_SCOPE = "handoff_render_status"
_STATUS_PREFIX = "handoff-status-"
_STALE_AFTER_S = 30 * 24 * 3600  # 陈旧文件清理阈值（正常退出会删，这是兜底）


def session_dir() -> str:
    return os.path.join(str(config.data_dir()), "session")


def session_file_path(port: int) -> str:
    return os.path.join(session_dir(), f"port-{int(port)}.json")


def publish_secret(port: int, secret: str) -> str:
    """原子写入本机 API 凭据文件，返回路径。POSIX 上目录 0700、文件 0600；
    Windows 没有等价位，但文件在用户 profile 下，ACL 天然限本人。"""
    d = session_dir()
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)
    except OSError:
        pass
    _prune_stale(d)
    path = session_file_path(port)
    tmp = path + ".tmp"
    payload = {"port": int(port), "pid": os.getpid(), "secret": secret, "created": time.time()}
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    return path


def remove_secret(port: int) -> None:
    try:
        os.unlink(session_file_path(port))
    except OSError:
        pass


def read_secret(port: int) -> str | None:
    """读该端口实例的本机 API 凭据；没有 / 读不懂一律 None（不是错误——
    对面可能是老版本或 --insecure-no-auth 的实例）。"""
    try:
        with open(session_file_path(port), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    secret = data.get("secret") if isinstance(data, dict) else None
    return secret if isinstance(secret, str) and secret else None


def auth_headers(port: int) -> dict:
    """本机调用方的认证请求头；没有凭据文件时为空 dict（老实例兼容）。"""
    secret = read_secret(port)
    return {AUTH_HEADER: secret} if secret else {}


#: `relaunch_nonce` 的响应体上限：只有一枚 nonce。其余调用方（refresh / readiness / probe 的响应随项目里
#: 脚本、素材数量增长）不设上限，与原来 `urlopen` 的完整读取一致，截断会让合法 JSON 解不开
RELAUNCH_MAX_RESPONSE_BYTES = 64 * 1024


def loopback_request(
    port: int,
    path: str,
    *,
    payload: bytes | None = None,
    headers: dict | None = None,
    timeout: float,
    max_bytes: int | None = None,
) -> tuple[int, bytes]:
    """对 `127.0.0.1:<port>` 发一次请求，返回 (状态码, 响应体)；`payload` 非 None 为 POST，否则 GET。

    带 `X-Tavotto-Auth` / 会话 secret 的本机请求一律走这里，**不用 `urlopen`**：后者按
    `HTTP_PROXY` / `ALL_PROXY` / 系统设置走代理，凭据会连同请求发给代理（#1132）。`HTTPConnection`
    不读任何代理设置；也不跟随重定向（3xx 原样交给调用方）。`max_bytes=None` 完整读取，给了就最多读这么多。失败抛 `http.client.HTTPException` /
    `OSError` / `ValueError`，由调用方按自己的「连不上」语义收敛。
    """
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
    try:
        conn.request(
            "GET" if payload is None else "POST", path, body=payload, headers=headers or {}
        )
        resp = conn.getresponse()
        return resp.status, resp.read(max_bytes)
    finally:
        conn.close()


def relaunch_nonce(port: int, timeout: float = 3.0) -> str | None:
    """向已在运行的实例换一枚一次性浏览器 nonce。任何失败都返回 None：
    调用方退回「打开不带 fragment 的地址」，页面侧会给出可操作的提示。"""
    secret = read_secret(port)
    if not secret:
        return None
    try:
        status, raw = loopback_request(
            port,
            RELAUNCH_PATH,
            payload=json.dumps({"secret": secret}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=timeout,
            max_bytes=RELAUNCH_MAX_RESPONSE_BYTES,
        )
        if not 200 <= status < 300:
            return None
        data = json.loads(raw.decode("utf-8", "replace"))
    except (http.client.HTTPException, OSError, ValueError):
        return None
    nonce = data.get("nonce") if isinstance(data, dict) else None
    return nonce if isinstance(nonce, str) and nonce else None


def status_file_path(port: int) -> str:
    return os.path.join(session_dir(), f"{_STATUS_PREFIX}{int(port)}.json")


def _pid_alive(pid: int) -> bool | None:
    """`True` / `False`；量不了回 None（Windows 上 `os.kill(pid, 0)` 不是探测）。"""
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def publish_status_token(port: int, token: str) -> str:
    """原子写入**只读交接状态**凭据（scoped，见模块文档），返回路径。权限与 `publish_secret` 相同。"""
    d = session_dir()
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)
    except OSError:
        pass
    _prune_stale(d)
    path = status_file_path(port)
    tmp = path + ".tmp"
    payload = {
        "port": int(port),
        "pid": os.getpid(),
        "token": token,
        "scope": STATUS_SCOPE,
        "created": time.time(),
    }
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    return path


def remove_status_token(port: int) -> None:
    try:
        os.unlink(status_file_path(port))
    except OSError:
        pass


def status_credentials() -> list[tuple[int, str]]:
    """扫描 `handoff-status-*.json`，返回 (端口, token)：scope 对、pid 没死的才算。

    读不懂 / 不是本人的 / scope 不对 / pid 已死一律跳过——这是发现，不是校验，
    校验在服务端（token 不对就是 401）。
    """
    d = session_dir()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out: list[tuple[int, str]] = []
    for name in names:
        if not (name.startswith(_STATUS_PREFIX) and name.endswith(".json")):
            continue
        path = os.path.join(d, name)
        try:
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
                continue
            if os.name == "posix" and st.st_uid != os.getuid():
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("scope") != STATUS_SCOPE:
            continue
        port, pid, token = data.get("port"), data.get("pid"), data.get("token")
        if not (isinstance(port, int) and 0 < port < 65536 and isinstance(token, str) and token):
            continue
        if isinstance(pid, int) and _pid_alive(pid) is False:
            continue
        out.append((port, token))
    return out


def _prune_stale(d: str) -> None:
    """清掉明显陈旧的凭据文件（进程崩溃没走到清理时留下的）。"""
    try:
        names = os.listdir(d)
    except OSError:
        return
    now = time.time()
    for name in names:
        is_port = name.startswith("port-") and name.endswith(".json")
        is_status = name.startswith(_STATUS_PREFIX) and name.endswith(".json")
        if not (is_port or is_status):
            continue
        path = os.path.join(d, name)
        try:
            stale = now - os.path.getmtime(path) > _STALE_AFTER_S
            if is_status and not stale:
                # 桌面 sidecar 崩了没走到清理：pid 已死的 scoped 凭据当场清掉
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                pid = data.get("pid") if isinstance(data, dict) else None
                stale = isinstance(pid, int) and _pid_alive(pid) is False
            if stale:
                os.unlink(path)
        except (OSError, ValueError):
            pass
