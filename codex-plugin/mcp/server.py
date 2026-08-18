#!/usr/bin/env python3
"""Codex 起 MCP server 的那一跳：**先找到装着 magplot 的解释器，再交棒**。

Codex 用 `python3 ./mcp/server.py` 启动本文件（见 `.mcp.json`），而那个 `python3`
不一定是装了 Magplot 的那个——用户多半是 `pipx install magplot` 或装的桌面版。
所以本文件的全部职责就是：

  1. 当前解释器 `import magplot` 成功 → 直接跑，不折腾；
  2. 否则按 `MAGPLOT_CLI` → PATH 上的 `magplot` → 常见桌面安装位置找到 CLI，
     **读它的 shebang** 拿到那个解释器，`os.execv` 交棒过去（同一个进程，
     stdio 原样继承，host 那边察觉不到换过人）；
  3. 都找不到 → 起一个**只会说人话的降级 server**：initialize / tools/list 照常
     响应，每个工具调用回一条「Magplot 没装，这么装」。
     **绝不静默退出**——那样用户在 Codex 里看到的只是「插件没有工具」。

纯标准库，Python 3.8+（找不到 magplot 时用户机器上的 python3 可能很老）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL_HINT = (
    "这台机器上没找到 Magplot。桌面版：https://github.com/erwanjun/magplot/releases；"
    "命令行版：pipx install magplot（或 pip install magplot）。"
    "装好后重开一次 Codex 会话即可。也可以用 MAGPLOT_CLI 指向 magplot 可执行文件。"
)


def _importable(python: str) -> bool:
    try:
        proc = subprocess.run([python, "-c", "import magplot"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return proc.returncode == 0


def _shebang_interpreter(script: str) -> "str | None":
    """console script 的 shebang → 装着 magplot 的那个解释器。

    pip / pipx 生成的 `magplot` 就是一个带 `#!<venv>/bin/python` 的小脚本，
    这一行是「哪个环境装了它」最可靠的答案（比再猜一遍 PATH 强得多）。
    Windows 上是 .exe，没有 shebang——那条路走下面的 `--python` 探测。
    """
    try:
        with open(script, "rb") as f:
            first = f.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    line = first[2:].strip().decode("utf-8", "replace")
    # `#!/usr/bin/env python3` 这种形态给不出具体环境，直接放弃
    parts = line.split()
    if not parts or parts[0].endswith("env"):
        return None
    return parts[0] if os.path.isfile(parts[0]) else None


def _candidates() -> "list[str]":
    """可能装着 magplot 的解释器（按优先级）。"""
    out = []
    cli = (os.environ.get("MAGPLOT_CLI") or "").strip() or shutil.which("magplot")
    if cli:
        interp = _shebang_interpreter(cli)
        if interp:
            out.append(interp)
        # Windows 的 magplot.exe 与 pipx 的 shim：同目录下的 python 通常就是它的环境
        base = os.path.dirname(os.path.abspath(cli))
        for name in ("python.exe", "python3", "python"):
            cand = os.path.join(base, name)
            if os.path.isfile(cand):
                out.append(cand)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            out.append(found)
    seen, uniq = set(), []
    for p in out:
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            uniq.append(p)
    return uniq


def _degraded_server() -> int:
    """Magplot 没装时的降级 server：能握手，每个工具都如实说缺什么。"""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace") \
        if hasattr(sys.stdout, "reconfigure") else None
    tools = [{"name": n, "description": "Magplot 未安装，本工具不可用。" + INSTALL_HINT,
              "inputSchema": {"type": "object", "properties": {},
                              "additionalProperties": True}}
             for n in ("magplot_open_figure", "magplot_apply_overrides",
                       "magplot_preflight", "magplot_export",
                       "magplot_verify_replay", "magplot_close_session")]

    def send(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        rid, method = msg.get("id"), msg.get("method")
        if rid is None:
            continue
        if method == "initialize":
            want = msg.get("params", {}).get("protocolVersion")
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": want if isinstance(want, str) else "2025-11-25",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "magplot", "version": "0"},
                "instructions": "Magplot 未安装。" + INSTALL_HINT}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": INSTALL_HINT}],
                "structuredContent": {"ok": False, "code": "magplot_missing",
                                      "error": INSTALL_HINT}}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"不支持的方法: {method}"}})
    return 0


def main() -> int:
    sys.path.insert(0, HERE)            # 让 `magplot_mcp` 包可 import
    try:
        import magplot  # noqa: F401
    except ImportError:
        pass
    else:
        from magplot_mcp.rpc import StdioConnection
        StdioConnection.hijack_stdout()
        from magplot_mcp.server import main as run
        return run(sys.argv[1:])

    for python in _candidates():
        if os.path.realpath(python) == os.path.realpath(sys.executable):
            continue                    # 刚试过就是它
        if _importable(python):
            # execv 而不是 subprocess：同一个进程 = stdio 原样继承，
            # host 那边不会看到管道换了一层（也不用管转发与信号）
            os.execv(python, [python, os.path.abspath(__file__), *sys.argv[1:]])
    print("magplot-mcp: 没找到装着 magplot 的解释器，进入降级模式。" + INSTALL_HINT,
          file=sys.stderr)
    return _degraded_server()


if __name__ == "__main__":
    raise SystemExit(main())
