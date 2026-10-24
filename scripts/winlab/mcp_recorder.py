"""透明的 MCP stdio 记录器（Windows Lab `hosts-live`；规则 docs/rules/ci/windows-lab.md「AI 宿主分层」）。

    python mcp_recorder.py <记录文件.jsonl> -- <真正的 MCP server 命令…>

宿主（Claude Code / Codex / CodeBuddy …）以为自己在跟 Tavotto MCP server 说话——确实是：本进程把
stdin/stdout 的字节原样转发给子进程，一个字节都不改。旁路只记**服务端事实**，供各宿主同一套判定：

- 宿主发了哪些方法（`initialize` / `tools/list` / `tools/call`）、调的是哪个工具；
- 每个响应是否出错（JSON-RPC `error` 或 MCP `result.isError`）、`tools/list` 列出了哪些 Tavotto 工具；
- 判 L5 要用的几个有界字段：`session_id`、`patch_hash`、`ok`。

参数与结果正文**不记**（路径、图内文字、SVG 都不进记录），只截上面这几个字段。
MCP stdio 帧是按行分隔的 JSON-RPC；解析不了的行照样转发、只记一条 `unparsed`。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time

# 仓库级约定：入口钉 UTF-8（本文件只写二进制流与 utf-8 记录文件，钉住只是为 stderr 报错）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_FIELDS = re.compile(rb'"(session_id|patch_hash|ok)"\s*:\s*("(?:[^"\\]|\\.){0,100}"|true|false)')


def _facts(text: bytes) -> dict:
    out = {}
    for k, v in _FIELDS.findall(text[:200_000]):
        key = k.decode()
        if key not in out:
            out[key] = json.loads(v)
    return out


class Recorder:
    def __init__(self, path: str):
        self._f = open(path, "a", encoding="utf-8")  # noqa: SIM115 — 进程期内一直开着
        self._lock = threading.Lock()
        self._pending: dict = {}

    def write(self, rec: dict) -> None:
        rec["t"] = round(time.time(), 3)
        with self._lock:
            self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._f.flush()

    def from_host(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            self.write({"dir": "host", "unparsed": True})
            return
        if not isinstance(msg, dict) or "method" not in msg:
            return
        tool = (msg.get("params") or {}).get("name") if msg["method"] == "tools/call" else None
        if "id" in msg:
            self._pending[msg["id"]] = (msg["method"], tool)
        self.write({"dir": "host", "method": msg["method"], "tool": tool})

    def from_server(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            self.write({"dir": "server", "unparsed": True})
            return
        if not isinstance(msg, dict) or "id" not in msg or msg["id"] not in self._pending:
            return
        method, tool = self._pending.pop(msg["id"])
        res = msg.get("result") if isinstance(msg.get("result"), dict) else {}
        rec = {
            "dir": "server",
            "method": method,
            "tool": tool,
            "error": "error" in msg or bool(res.get("isError")),
        }
        if method == "tools/list":
            rec["tools"] = sorted(
                str(t.get("name")) for t in res.get("tools") or [] if isinstance(t, dict)
            )
        if method == "tools/call":
            rec["facts"] = _facts(line)
        self.write(rec)


def _pump(src, dst, observe) -> None:
    for line in iter(src.readline, b""):
        try:  # 先登记请求 id 再转发：server 回得快时 from_server 才认得出这条回复
            observe(line)
        except Exception:  # noqa: BLE001 — 记录器出错不许打断宿主与 server 的通信
            pass
        dst.write(line)
        dst.flush()
    try:
        dst.close()
    except OSError:
        pass


def _tail(path: str, child: subprocess.Popen, dst, observe) -> None:
    """server 的 stdout 落在文件里（不开管道：写满 64 KiB 卡死的那类问题根本不存在），这里边写边读、
    按整行原样转给宿主。子进程退出且读到文件末尾才停。"""
    buf = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line += b"\n"
                    dst.write(line)
                    dst.flush()
                    try:
                        observe(line)
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if child.poll() is not None:
                if buf:
                    dst.write(buf)
                    dst.flush()
                return
            time.sleep(0.005)


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        sys.stderr.write("usage: mcp_recorder.py LOG.jsonl -- COMMAND...\n")
        return 2
    rec = Recorder(argv[0])
    rec.write({"event": "start"})
    out_path = argv[0] + f".server-stdout.{os.getpid()}"
    with open(out_path, "wb") as out:
        child = subprocess.Popen(argv[2:], stdin=subprocess.PIPE, stdout=out)
    up = threading.Thread(
        target=_pump, args=(sys.stdin.buffer, child.stdin, rec.from_host), daemon=True
    )
    up.start()
    _tail(out_path, child, sys.stdout.buffer, rec.from_server)
    rc = child.wait()
    rec.write({"event": "exit", "code": rc})
    with contextlib.suppress(OSError):
        os.unlink(out_path)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
