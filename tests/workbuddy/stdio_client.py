"""WorkBuddy P0 spike：一个**冒充宿主**的最小 MCP stdio client。

它不是 WorkBuddy。它只把「宿主能怎么握手、会不会声明 roots / elicitation、
确认框点了什么」做成可切换的参数，然后对**真的** `codex-plugin/mcp/server.py`
（真启动器 → 真 resolver → 真引擎 → 真 matplotlib）发帧，把原始往来整段记下来。

回答不了的问题（只有真 WorkBuddy 能答）：它实际声明哪些 capability、`cwd`
落在哪、权限框长什么样。那些写在 `docs/acceptance/workbuddy-mcp-app.md` 的
真客户端清单里。

帧格式与 server 一致：一行一条 JSON（`tavotto_mcp/rpc.py`）。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "codex-plugin"
LAUNCHER = PLUGIN / "mcp" / "server.py"


class StdioHost:
    """起一个真 server 子进程；server→client 的请求按 `policy` 应答。

    policy:
      * ``elicitation``: ``"accept" | "decline" | "cancel" | "silent"``（silent = 不回，
        模拟「宿主声明了却没弹框」）
      * ``roots``: 列表（file:// URI 由这里生成）
    """

    def __init__(
        self,
        *,
        python: str = "python3",
        env_extra: dict[str, str] | None = None,
        cwd: str | None = None,
        policy: dict | None = None,
        drop_env: tuple[str, ...] = (),
    ) -> None:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("TAVOTTO_MCP_", "CODEX_")) and k not in drop_env
        }
        env.update(env_extra or {})
        self.policy = policy or {}
        self.log: list[dict] = []
        self.server_requests: list[dict] = []
        self._id = 0
        self._inbox: queue.Queue = queue.Queue()
        self.proc = subprocess.Popen(
            [python, str(LAUNCHER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            # WorkBuddy 连接器 mcp.json 写 `"cwd": "."` = 连接器目录（插件目录）
            cwd=cwd or str(PLUGIN),
            bufsize=0,
        )
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.stderr_tail: list[str] = []

    # ------------------------------------------------------------------ io
    def _drain_stderr(self) -> None:
        assert self.proc.stderr
        for raw in self.proc.stderr:
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip())
            del self.stderr_tail[:-200]

    def _pump(self) -> None:
        assert self.proc.stdout
        for raw in self.proc.stdout:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            msg = json.loads(line)
            if "method" in msg and "id" in msg:
                self._answer_server_request(msg)
                continue
            self._inbox.put(msg)
        self._inbox.put(None)

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _answer_server_request(self, msg: dict) -> None:
        method = msg["method"]
        self.server_requests.append({"method": method, "params": msg.get("params")})
        if method == "roots/list":
            roots = [
                {"uri": Path(p).resolve().as_uri(), "name": Path(p).name}
                for p in self.policy.get("roots", [])
            ]
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"roots": roots}})
            return
        if method == "elicitation/create":
            mode = self.policy.get("elicitation", "decline")
            if mode == "silent":
                return
            result: dict = {"action": mode}
            if mode == "accept":
                result["content"] = {"approve": True}
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            return
        self._send(
            {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "unsupported"}}
        )

    def request(self, method: str, params: dict | None = None, timeout: float = 600) -> dict:
        self._id += 1
        rid = self._id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        t0 = time.monotonic()
        self._send(msg)
        while True:
            got = self._inbox.get(timeout=timeout)
            if got is None:
                raise RuntimeError("server 退出了：\n" + "\n".join(self.stderr_tail[-20:]))
            if got.get("id") == rid:
                raw = json.dumps(got, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.log.append(
                    {
                        "method": method,
                        "name": (params or {}).get("name"),
                        "ms": round((time.monotonic() - t0) * 1000),
                        "bytes": len(raw),
                    }
                )
                return got

    def notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def initialize(
        self, capabilities: dict, client: dict | None = None, version: str = "2025-06-18"
    ) -> dict:
        res = self.request(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": capabilities,
                "clientInfo": client or {"name": "workbuddy-spike-fake-host", "version": "0"},
            },
        )
        self.notify("notifications/initialized")
        return res

    def call(self, name: str, arguments: dict | None = None, timeout: float = 600) -> dict:
        return self.request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        )

    def close(self) -> None:
        try:
            assert self.proc.stdin
            self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()
