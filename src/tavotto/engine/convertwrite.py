"""转换会话的「代写目标文件」工具（`write_conversion_target`，TVD-BCZ0 方案 A，ai-agent-bridge.md「转换目标由 Tavotto 代写」）。

问题：Windows 上 Codex 自带的文件写入工具（file_change / apply_patch）在 unelevated 沙盒里一律 `Failed to write file`
（Lab run 20261102T055146Z-8fc66e，OpenAI 官方模型），模型读完源码却写不出目标，转换落到 `agent_write_denied`。

做法：转换会话里由 **Tavotto 在沙盒外写**目标文件。Codex 经调用级 `-c mcp_servers.tavotto_convert.*` 拉起 `tavotto fix-mcp --convert-id <sid>`
（复用修复模式的 MCP 服务进程 `fixmcp.py`），它把模型给的**文件内容**回调到本进程 `POST /api/ai/convert/<sid>/target`，这里写进候选工作区里
那一个目标文件。判据的主语：

* **谁决定路径**：只有 Tavotto（`Grant.target` = 候选的 `cand.target`）；工具参数里没有路径，模型指不了别处；
* **谁有资格写**：本会话的 scoped token（`X-Tavotto-Convert-Token`，随机，只经子进程环境变量 `TAVOTTO_CONVERT_TOKEN` 传，不进 argv、不进日志），
  会话结束（`close`）即失效；别的会话 / 错令牌 / 已结束一律同一个 403，不泄漏哪一项错了；
* **写什么**：UTF-8 文本、≤ `MAX_BYTES`（2 MiB）、非空白；写入走 `atomicio.write_bytes`（同目录临时文件 + fsync + replace），目标是符号链接则拒绝；
* **记什么**：只记闭集码与字节数（`logsafe` 规则）：`calls` / `ok` / `last_code` / `bytes` 放进 `cand.extras["target_tool"]`，供
  `ai_convert.write_denied`（工具失败且目标缺失 = `agent_write_denied`）与运行事实 `target_tool_ok` 读。

纯标准库 + `atomicio`（Flask 父进程一侧）。
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import atomicio
from .fixmcp import CONVERT_SERVER_NAME, CONVERT_TOKEN_ENV, CONVERT_TOOL_NAME

#: 单次写入的内容上限（字节，UTF-8 编码后）。转换产物是一份 matplotlib 脚本，远小于此；超限让模型缩减而不是截断。
MAX_BYTES = 2 * 1024 * 1024
#: 授权的兜底寿命（秒）：正常由会话收尾 `close`；没走到收尾（进程异常）时靠它不让令牌无限期有效。
GRANT_TTL_S = 3 * 3600.0
#: 工具失败码闭集（返回给模型 / 写进运行事实的就是这些）。
CODES = frozenset({"bad_content", "empty", "too_large", "target_unsafe", "write_failed"})
DEFAULT_PORT = 5089


class Forbidden(Exception):
    """令牌不对 / 会话不对 / 已结束：同一个异常，不区分原因。"""


@dataclass
class Grant:
    sid: str
    token: str
    target: Path
    #: 候选的 `extras`：工具状态写在 `extras["target_tool"]`（`ai_convert.write_denied` 读）
    extras: dict
    expires: float
    calls: int = 0
    ok: bool = False
    last_code: str = ""
    last_bytes: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _record(self) -> None:
        self.extras["target_tool"] = {
            "calls": self.calls,
            "ok": self.ok,
            "last_code": self.last_code,
            "bytes": self.last_bytes,
        }


_LOCK = threading.Lock()
_GRANTS: dict[str, Grant] = {}
_PORT = [DEFAULT_PORT]


def note_port(port: object) -> None:
    """记下本进程的回环端口（转换端点从请求里取）；回调 URL 只用它。非法值忽略。"""
    try:
        p = int(str(port))
    except (TypeError, ValueError):
        return
    if 0 < p < 65536:
        _PORT[0] = p


def callback_url() -> str:
    return f"http://127.0.0.1:{_PORT[0]}"


def _prune(now: float) -> None:
    for sid in [s for s, g in _GRANTS.items() if g.expires <= now]:
        del _GRANTS[sid]


def open_grant(sid: str, target: Path, extras: dict) -> Grant:
    """为转换会话 `sid` 发一枚令牌；之后 `write` 只会写 `target`。同一 `sid` 重复开 = 换新令牌（旧的失效）。"""
    now = time.monotonic()
    g = Grant(sid, secrets.token_urlsafe(32), Path(target), extras, now + GRANT_TTL_S)
    g._record()
    with _LOCK:
        _prune(now)
        _GRANTS[sid] = g
    return g


def close(sid: str) -> None:
    """会话结束：令牌立即失效。幂等。"""
    with _LOCK:
        _GRANTS.pop(str(sid), None)


def ok(sid: str) -> bool:
    """该会话（此刻还登记着的授权）里是否至少成功写过一次。"""
    with _LOCK:
        g = _GRANTS.get(str(sid))
    return bool(g and g.ok)


def _authorized(sid: object, token: object) -> Grant:
    if not isinstance(sid, str) or not isinstance(token, str) or not token:
        raise Forbidden
    with _LOCK:
        _prune(time.monotonic())
        g = _GRANTS.get(sid)
    if g is None or not hmac.compare_digest(g.token.encode(), token.encode()):
        raise Forbidden
    return g


def token_valid(sid: object, token: object) -> bool:
    """会话认证 guard 的 scoped token 检查（`security.py`）。"""
    try:
        _authorized(sid, token)
    except Forbidden:
        return False
    return True


def write(sid: object, token: object, content: object) -> dict:
    """工具本体。→ `{"ok": True, "bytes": n}` 或 `{"ok": False, "code": 闭集码}`；令牌不对抛 `Forbidden`（端点回 403）。"""
    g = _authorized(sid, token)
    with g.lock:
        g.calls += 1
        data, code = _encode(content)
        if code is None:
            code = _store(g.target, data)
        if code is None:
            g.ok, g.last_code, g.last_bytes = True, "", len(data)
            g._record()
            return {"ok": True, "bytes": len(data)}
        g.last_code = code
        g._record()
        return {"ok": False, "code": code}


def _encode(content: object) -> tuple[bytes, str | None]:
    if not isinstance(content, str):
        return b"", "bad_content"
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError:  # 孤立代理项等：不是合法文本
        return b"", "bad_content"
    if len(data) > MAX_BYTES:
        return b"", "too_large"
    if not content.strip():
        return b"", "empty"
    return data, None


def _store(target: Path, data: bytes) -> str | None:
    try:
        if target.is_symlink() or target.is_dir():
            return "target_unsafe"
        atomicio.write_bytes(target, data)
    except (atomicio.AtomicWriteError, OSError):
        return "write_failed"
    return None


__all__ = [
    "CODES",
    "CONVERT_SERVER_NAME",
    "CONVERT_TOKEN_ENV",
    "CONVERT_TOOL_NAME",
    "Forbidden",
    "GRANT_TTL_S",
    "MAX_BYTES",
    "callback_url",
    "close",
    "note_port",
    "ok",
    "open_grant",
    "token_valid",
    "write",
]
