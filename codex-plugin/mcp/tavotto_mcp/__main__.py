"""`python -m tavotto_mcp` —— 进程入口。

**第一件事是把 stdout 抢过来**（协议独占它），第二件事才是 import 别的东西：
`tavotto.engine.pool` 一路下去会 import 不少模块，任何一行 print 落进 stdout
都会让 host 侧看到「不是合法 JSON-RPC」然后断开，而错误现场早就过去了。
"""

from __future__ import annotations

import sys

from .rpc import StdioConnection

StdioConnection.hijack_stdout()

from .server import main  # noqa: E402  —— 必须在 hijack 之后

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
