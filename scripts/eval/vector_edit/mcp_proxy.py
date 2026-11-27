"""评测用的 stdio MCP 代理：把工具结果里的 `structuredContent` 也以文本块附在 `content` 后面再交给宿主。

为什么要有它：插件的排版工具把 `base_revision`、`echo_digest`、场景里的对象 id 等**只放进 `structuredContent`**（server.py 头注释：
给 Codex 这类会把它交给模型的宿主）。opencode 只把 `content` 文本交给模型，agent 拿不到这几个值，`edit_plan → edit_apply` 走不通。
B2 组用这个代理模拟「宿主把 structuredContent 一并给了模型」，其余与 B 相同。

    python mcp_proxy.py -- <真正的 MCP server 命令…>

按行转发 JSON-RPC（stdio 传输是换行分隔）；只改带 `result.structuredContent` 的响应。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading

CAP = 24000


def augment(line: str) -> str:
    try:
        msg = json.loads(line)
    except ValueError:
        return line
    res = msg.get("result") if isinstance(msg, dict) else None
    if isinstance(res, dict) and isinstance(res.get("structuredContent"), dict):
        body = json.dumps(res["structuredContent"], ensure_ascii=False, separators=(",", ":"))
        note = "" if len(body) <= CAP else f"…（已截断，共 {len(body)} 字符）"
        res.setdefault("content", []).append(
            {"type": "text", "text": "structuredContent: " + body[:CAP] + note}
        )
        return json.dumps(msg, ensure_ascii=False)
    return line


def main() -> None:
    cmd = sys.argv[sys.argv.index("--") + 1 :]
    child = subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        encoding="utf-8",
    )

    def pump_in() -> None:
        for line in sys.stdin:
            child.stdin.write(line)
            child.stdin.flush()
        child.stdin.close()

    threading.Thread(target=pump_in, daemon=True).start()
    for line in child.stdout:
        sys.stdout.write(augment(line.rstrip("\n")) + "\n")
        sys.stdout.flush()
    child.wait()


if __name__ == "__main__":
    main()
