"""JSON-RPC 2.0 over stdio —— MCP 的 stdio 传输层。

帧格式就是**一行一条 JSON**（换行分隔，UTF-8）。没有 Content-Length 头，
所以任何一条写进 stdout 的杂物都会把整条连接搞坏：

* 本模块把 `sys.stdout` 抢过来自己用，并把 `sys.stdout` 换成 `sys.stderr`
  ——被 import 的第三方（matplotlib 的字体缓存重建、用户脚本的 print）
  一旦往 stdout 写字，host 侧看到的是「不是合法 JSON-RPC」然后断开；
* Windows 上还要显式钉 UTF-8：管道下 Python 会退回 cp936/cp1252，
  中文错误信息第一次 print 就 UnicodeEncodeError（handoff.py 上撞过同一件事）。

纯标准库。
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any, BinaryIO

#: 真正的 stdout —— `hijack_stdout()` 之前抢下来的那个句柄。
#: **必须在改 `sys.stdout` 之前存**：抢完再去读 `sys.stdout.buffer` 拿到的是
#: stderr 的缓冲区，协议帧全写到 stderr 上，host 那边表现为「服务器不回消息」
#: （initialize 永远等不到响应，也没有任何报错）。
_REAL_STDOUT: BinaryIO | None = None

#: JSON-RPC 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    """带 JSON-RPC 错误码的异常；`data` 会原样进响应，给调用方可操作的线索。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    def payload(self) -> dict:
        out: dict = {"code": self.code, "message": str(self)}
        if self.data is not None:
            out["data"] = self.data
        return out


class StdioConnection:
    """一条 stdio 上的 JSON-RPC 连接。写是加锁的（通知可能来自别的线程）。"""

    def __init__(self, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> None:
        self._in = stdin if stdin is not None else sys.stdin.buffer
        if stdout is not None:
            self._out = stdout
        else:
            self._out = _REAL_STDOUT if _REAL_STDOUT is not None else sys.stdout.buffer
        self._lock = threading.Lock()

    @staticmethod
    def hijack_stdout() -> None:
        """把 `sys.stdout` 改道到 stderr —— 协议独占真正的 stdout。

        必须在**任何**可能 print 的 import 之前调用。少了这一步，
        「服务器随机断开」会以最难查的形式出现：谁在某次渲染里 print 了一行。
        """
        global _REAL_STDOUT
        if _REAL_STDOUT is None:
            _REAL_STDOUT = sys.stdout.buffer
        for stream in (sys.stderr,):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except (ValueError, OSError):
                    pass
        sys.stdout = sys.stderr

    def read(self) -> dict | None:
        """读一条消息；EOF 回 None。非 JSON 的行抛 RpcError（调用方回 -32700）。"""
        line = self._in.readline()
        if not line:
            return None
        text = line.decode("utf-8", "replace").strip()
        if not text:
            return self.read()
        try:
            msg = json.loads(text)
        except ValueError as exc:
            raise RpcError(PARSE_ERROR, f"不是合法 JSON: {exc}") from exc
        if not isinstance(msg, dict):
            raise RpcError(INVALID_REQUEST, "JSON-RPC 消息必须是对象")
        return msg

    def write(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
        with self._lock:
            self._out.write(data + b"\n")
            self._out.flush()

    def result(self, rid: Any, result: dict) -> None:
        self.write({"jsonrpc": "2.0", "id": rid, "result": result})

    def error(self, rid: Any, exc: RpcError) -> None:
        self.write({"jsonrpc": "2.0", "id": rid, "error": exc.payload()})

    def notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.write(msg)
