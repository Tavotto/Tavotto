"""`tavotto mcp`：在**这个进程里**起 Tavotto 的 MCP 服务（stdio），给「接入 AI 编辑器」用。

## 为什么要有它

宿主（Cursor、VS Code、ZCode、WorkBuddy、Claude 桌面版……）的 MCP 配置要一条能起服务端的命令。插件包里的
`mcp/server.py` 是**解析器**：它去找一个能 `import tavotto.engine` 的 Python 再交棒——只装了桌面版的用户
没有这样的 Python（frozen 的 `tavotto-cli` 给不出解释器，见那份文件头），只能先 `--provision` 从 PyPI
装一份引擎。而桌面版里本来就带着整个引擎：让 `tavotto-cli mcp` 直接在进程内跑同一个服务端，宿主配置写
`<桌面版>/tavotto-cli mcp` 即可，**不下载、不建 venv、不碰用户的 Python**（用户 2026-10-27 选定）。

## 服务端只有一份

这里不实现任何工具：`tavotto_mcp` 包（`codex-plugin/mcp/tavotto_mcp/`）就是插件里那一份，冻结产物由
`packaging/tavotto.spec` 把它编进 PYZ（`collect_submodules`），源码树里从 `codex-plugin/mcp` 找到它。
wheel 不带 `codex-plugin/`（打包卫生规则），所以 pip / pipx 装的 `tavotto mcp` 如实报
`mcp_server_not_bundled`——那条路照旧用插件的启动器。

## stdout 归协议独占

与 `tavotto_mcp/__main__.py` 同一条纪律：**先**把 stdout 抢过来，**再** import 服务端（引擎一路 import
下去任何一行 print 落进 stdout，宿主就会看到「不是合法 JSON-RPC」然后断开）。失败的说明只写 stderr。
"""

from __future__ import annotations

import json
import os
import sys

#: 冻结产物里没有服务端（老版本的 spec）/ pip 装的没有 `codex-plugin/` 时的稳定身份
NOT_BUNDLED = "mcp_server_not_bundled"


def _source_tree_mcp_dir() -> str | None:
    """源码树（开发态）里插件 MCP 包所在的目录：`<仓库>/codex-plugin/mcp`。不是源码树就是 None。"""
    here = os.path.dirname(os.path.abspath(__file__))  # <仓库>/src/tavotto/engine
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    cand = os.path.join(root, "codex-plugin", "mcp")
    return cand if os.path.isfile(os.path.join(cand, "tavotto_mcp", "server.py")) else None


def _import_server():
    """返回 `tavotto_mcp.rpc.StdioConnection`（服务端本体等抢下 stdout 之后再 import）；找不到抛 ImportError。"""
    if not getattr(sys, "frozen", False):
        src = _source_tree_mcp_dir()
        if src and src not in sys.path:
            sys.path.insert(0, src)
    from tavotto_mcp.rpc import StdioConnection  # noqa: PLC0415 — 延后：先确认在哪找

    return StdioConnection


def cli(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        sys.stderr.write("用法：tavotto mcp   （stdio MCP 服务；宿主配置里写这条命令）\n")
        return 0
    try:
        stdio = _import_server()
    except ImportError as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "ok": False,
                    "error_code": NOT_BUNDLED,
                    "error": "这份 Tavotto 里没有 MCP 服务端：桌面版请升级；"
                    "pip / pipx 装的请用插件里的 mcp/server.py 启动",
                    "detail": str(exc)[:300],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return 2
    stdio.hijack_stdout()
    from tavotto_mcp.server import main  # noqa: PLC0415 — 必须在 hijack 之后

    return int(main(argv) or 0)
