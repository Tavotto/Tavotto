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
**不写**这份文件——它的动态端口与 nonce 由 Tauri 壳经 stdin 管道交接，
不需要（也不应有）磁盘上的凭据。
"""
from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.request

from . import config

AUTH_HEADER = "X-Tavotto-Auth"
RELAUNCH_PATH = "/api/session/relaunch"
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
    payload = {"port": int(port), "pid": os.getpid(),
               "secret": secret, "created": time.time()}
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
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


def relaunch_nonce(port: int, timeout: float = 3.0) -> str | None:
    """向已在运行的实例换一枚一次性浏览器 nonce。任何失败都返回 None：
    调用方退回「打开不带 fragment 的地址」，页面侧会给出可操作的提示。"""
    secret = read_secret(port)
    if not secret:
        return None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{RELAUNCH_PATH}",
        data=json.dumps({"secret": secret}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    nonce = data.get("nonce") if isinstance(data, dict) else None
    return nonce if isinstance(nonce, str) and nonce else None


def _prune_stale(d: str) -> None:
    """清掉明显陈旧的凭据文件（进程崩溃没走到清理时留下的）。"""
    try:
        names = os.listdir(d)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not (name.startswith("port-") and name.endswith(".json")):
            continue
        path = os.path.join(d, name)
        try:
            if now - os.path.getmtime(path) > _STALE_AFTER_S:
                os.unlink(path)
        except OSError:
            pass
