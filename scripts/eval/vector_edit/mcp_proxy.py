"""评测用的 MCP server 入口：照常跑插件的 MCP server，只在进程内把工具结果的 `structuredContent` 也以文本块附在 `content` 后面。

为什么要有它：插件的排版工具（修复前）把 `base_revision`、`echo_digest`、场景里的对象 id 等**只放进 `structuredContent`**
（给 Codex 这类会把它交给模型的宿主），opencode 只把 `content` 文本交给模型，agent 拿不到这几个值，`edit_plan → edit_apply` 走不通。
B2 组用它模拟「宿主把 structuredContent 一并给了模型」，其余与 B 相同。**对已经带这个修复的源码树用它会重复附一份**，B2 只对修复前的树有意义。

    python mcp_proxy.py <插件 mcp 目录（含 tavotto_mcp 包）>

进程内包一层 `server.call_tool`，**不起子进程、没有管道要排空**；stdio 的收发仍是 server 自己的。
"""

from __future__ import annotations

import json
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

CAP = 24000


def augment(result: dict) -> dict:
    body = result.get("structuredContent") if isinstance(result, dict) else None
    if isinstance(body, dict):
        text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        note = "" if len(text) <= CAP else f"…（已截断，共 {len(text)} 字符）"
        result.setdefault("content", []).append(
            {"type": "text", "text": "structuredContent: " + text[:CAP] + note}
        )
    return result


def main() -> int:
    sys.path.insert(0, sys.argv[1])
    from tavotto_mcp import server

    real = server.call_tool
    server.call_tool = lambda name, args: augment(real(name, args))
    return int(server.main() or 0)


if __name__ == "__main__":
    sys.exit(main())
