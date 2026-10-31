"""MCP「修复模式」服务（智能修复主线 D3，ADR 0149 修订 D3）：让只连了 Codex / Claude Code 的用户也能用「使用你的 AI 来修复」。

`tavotto fix-mcp --url <回环地址> --fix-id <修复会话 id>` 由 CLI 当 stdio MCP 服务拉起（命令行见 `ai_agents` 的 `build_fix_command`）。
它是**纯翻译**：一个工具也不自己执行。

* `tools/list` → `GET /api/ai/fix/<id>/tools`：名字 / 描述 / schema 来自应用进程里的 `fixtools.REGISTRY`（与内置执行器同口径，**不含 tier**）；
* `tools/call` → `POST /api/ai/fix/<id>/tool`：由应用进程里的 `run_tool(tool, 会话上下文, 参数, 审计)` 执行——卡片、快照、审计、项目作用域
  全是 D2b 修复会话那一份，这里不另做一套。用户还没点卡片时这个调用就一直等（上限与卡片超时一致），会话取消时应用端立刻回错误。

令牌（本次会话的 scoped token）只经环境变量 `TAVOTTO_FIX_TOKEN` 传入，**不进 argv**、不进日志；请求头 `X-Tavotto-Fix-Token`。
回环请求走字面量 `http.client.HTTPConnection("127.0.0.1", 端口)`（与 `session_client.loopback_request` 同一条纪律，#1132）：不读任何代理设置、不跟随重定向，令牌不会被带给代理。**纯标准库**：stdout 归协议独占，别的一律写 stderr，且不写任何内容。
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.parse

ENV_TOKEN = "TAVOTTO_FIX_TOKEN"
TOKEN_HEADER = "X-Tavotto-Fix-Token"
SERVER_NAME = "tavotto_fix"
PROTOCOL_VERSION = "2025-06-18"
#: 一次工具调用最长等多久（秒）：比卡片超时（`fixsession.ASK_TIMEOUT`）略长，让「用户没点」先由应用端报 timeout
CALL_TIMEOUT = 660.0
LIST_TIMEOUT = 30.0
_MAX_RESPONSE = 1024 * 1024

# JSON-RPC 错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class FixMcpError(Exception):
    pass


def _loopback_port(base_url: str) -> int:
    """只认 `http://127.0.0.1:<端口>`：修复回调只回本机应用，别的地址一律拒（不会被拿去连外部）。"""
    u = urllib.parse.urlsplit(base_url)
    if u.scheme != "http" or u.hostname != "127.0.0.1" or not u.port or u.path not in ("", "/"):
        raise FixMcpError("bad_url")
    return u.port


def _loopback_transport(
    port: int, method: str, path: str, body: bytes | None, headers: dict, timeout: float
) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read(_MAX_RESPONSE + 1)
    finally:
        conn.close()


class Callback:
    """对应用修复端点的两个回环请求。`transport` 可注入（测试用），形状同 `_loopback_transport`。"""

    def __init__(self, base_url: str, fix_id: str, token: str, transport=None) -> None:
        self.port = _loopback_port(base_url)
        self.fix_id = fix_id
        self.token = token
        self.transport = transport or _loopback_transport

    def _request(self, method: str, suffix: str, body: dict | None, timeout: float) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        path = f"/api/ai/fix/{urllib.parse.quote(self.fix_id, safe='')}/{suffix}"
        headers = {TOKEN_HEADER: self.token, "Content-Type": "application/json"}
        try:
            status, raw = self.transport(self.port, method, path, data, headers, timeout)
        except (http.client.HTTPException, OSError, TimeoutError, ValueError):
            raise FixMcpError("unreachable") from None
        if status != 200:
            raise FixMcpError(f"http_{status}")
        if len(raw) > _MAX_RESPONSE:
            raise FixMcpError("response_too_large")
        try:
            out = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise FixMcpError("bad_response") from None
        if not isinstance(out, dict):
            raise FixMcpError("bad_response")
        return out

    def tools(self) -> list[dict]:
        out = self._request("GET", "tools", None, LIST_TIMEOUT)
        tools = out.get("tools")
        if not isinstance(tools, list):
            raise FixMcpError("bad_response")
        return tools

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        out = self._request("POST", "tool", {"name": name, "arguments": arguments}, CALL_TIMEOUT)
        return str(out.get("content") or ""), bool(out.get("is_error"))


def _tool_def(t: dict) -> dict:
    return {
        "name": str(t.get("name") or ""),
        "description": str(t.get("description") or ""),
        "inputSchema": t.get("inputSchema") or t.get("schema") or {"type": "object"},
    }


class FixMcpServer:
    """一条 stdio 上的 MCP 会话（一行一条 JSON，UTF-8）。"""

    def __init__(self, callback: Callback, stdin, stdout) -> None:
        self.cb, self._in, self._out = callback, stdin, stdout

    def _send(self, msg: dict) -> None:
        self._out.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self._out.flush()

    def _error(self, rid, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def handle(self, msg: dict) -> None:
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if not isinstance(method, str):
            if rid is not None:
                self._error(rid, INVALID_REQUEST, "missing method")
            return
        if rid is None:  # 通知（notifications/initialized、cancelled 等）：不回
            return
        if method == "initialize":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": SERVER_NAME, "version": "1"},
                    },
                }
            )
        elif method == "ping":
            self._send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            try:
                tools = [_tool_def(t) for t in self.cb.tools()]
            except FixMcpError as exc:
                return self._error(rid, INTERNAL_ERROR, f"tavotto_unavailable:{exc}")
            self._send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(args, dict):
                return self._error(rid, INVALID_PARAMS, "bad tools/call params")
            try:
                text, is_error = self.cb.call(name, args)
            except FixMcpError as exc:
                text, is_error = f"Tavotto could not run this tool ({exc}).", True
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
                }
            )
        else:
            self._error(rid, METHOD_NOT_FOUND, f"unknown method {method}")

    def serve(self) -> int:
        while True:
            line = self._in.readline()
            if not line:
                return 0
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                self._error(None, PARSE_ERROR, "not JSON")
                continue
            if isinstance(msg, dict):
                self.handle(msg)


def cli(argv: list[str]) -> int:
    """`tavotto fix-mcp` 的入口。stdout 在这里抢下来给协议，`sys.stdout` 改道 stderr。"""
    ap = argparse.ArgumentParser(prog="tavotto fix-mcp", add_help=False)
    ap.add_argument("--url", required=True)
    ap.add_argument("--fix-id", required=True)
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    token = os.environ.get(ENV_TOKEN, "")
    try:
        callback = Callback(args.url, args.fix_id, token) if token else None
    except (FixMcpError, ValueError):
        callback = None
    if callback is None:
        sys.stderr.write("tavotto fix-mcp: missing token or non-loopback url\n")
        return 2
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    return FixMcpServer(callback, sys.stdin.buffer, out).serve()
