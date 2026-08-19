#!/usr/bin/env python3
"""Codex 起 MCP server 的那一跳：**先找到装着 tavotto 的解释器，再交棒**。

Codex 用 `python3 ./mcp/server.py` 启动本文件（见 `.mcp.json`），而那个 `python3`
不一定是装了 Tavotto 的那个——用户多半是 `pipx install tavotto` 或装的桌面版。
所以本文件的全部职责就是：

  1. 当前解释器 `import tavotto` 成功 → 直接跑，不折腾；
  2. 否则用**插件自带的定位器**（`skills/tavotto-figure/scripts/handoff.py` 的
     `find_tavotto()`，它是 `engine/locate.py` 的镜像，两侧由
     `tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在一整张
     环境矩阵上比对）找到 tavotto 命令行，再从它**反推出解释器**
     （shebang / 同目录的 python），`os.execv` 交棒过去——同一个进程，
     stdio 原样继承，host 那边察觉不到换过人。**这里绝不抄第三遍路径规则**。
  3. 找不到解释器时起一个**只会说人话的降级 server**：initialize / tools/list
     照常响应，每个工具调用回一条可操作的原因。**绝不静默退出**——那样用户在
     Codex 里看到的只是「插件没有工具」。

## 桌面版用户要单独说一句

这里与交接（`handoff.py`）的要求**不一样**，这是本文件唯一需要自己判断的事：

* 交接只要能**执行** `tavotto open`，桌面版带的 `tavotto-cli` 完全够用；
* 但 MCP server 是一个 Python 模块，它要 `import tavotto.engine.*` 在进程内
  驱动引擎。桌面版的 `tavotto-cli` 是 PyInstaller 打的 frozen 可执行文件，
  **给不出一个能 import tavotto 的解释器**。

所以「只装了桌面版」这一格要报 `desktop_only` 并说清：交接照常能用，画布与
六个工具需要一个 Python 环境（`pipx install tavotto`）。笼统地说「没装 Tavotto」
是错的——他明明装了（这正是 #7 修过的那类错，别在新入口上重犯）。

纯标准库，Python 3.8+（找不到 tavotto 时用户机器上的 python3 可能很老）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
#: 只装了桌面版时的那一格。**不能说「没装 Tavotto」**——他明明装了。
DESKTOP_ONLY_HINT = (
    "这台机器上装的是 Tavotto 桌面版。交接（把图交给 Tavotto 窗口打开）照常能用，"
    "但 Codex 里的画布与这六个工具需要一个能 import tavotto 的 Python 环境——"
    "桌面版带的 tavotto-cli 是打包成单文件的可执行程序，给不出解释器。"
    "跑一句 `pipx install tavotto`（或 `pip install tavotto`）就好，"
    "两者可以共存。装完重开一次 Codex 会话。"
)


def _importable(python: str) -> bool:
    try:
        proc = subprocess.run([python, "-c", "import tavotto"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return proc.returncode == 0


def _plugin_locator():
    """插件自带的那份定位器（`engine/locate.py` 的镜像，有矩阵测试看着）。

    与本文件同属一个插件包，按相对路径 import 即可——**不在这里抄第三遍**
    路径规则（Tavotto 一份、插件的 handoff 一份，已经是能接受的上限）。
    """
    scripts = os.path.abspath(os.path.join(HERE, "..", "skills",
                                           "tavotto-figure", "scripts"))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import handoff                                    # noqa: PLC0415
    return handoff


def _shebang_interpreter(script: str) -> "str | None":
    """console script 的 shebang → 装着 tavotto 的那个解释器。

    pip / pipx 生成的 `tavotto` 就是一个带 `#!<venv>/bin/python` 的小脚本，
    这一行是「哪个环境装了它」最可靠的答案。桌面版的 `tavotto-cli` 是
    frozen 二进制，没有 shebang——它走不到这条，正好是我们要区分的那一格。
    """
    try:
        with open(script, "rb") as f:
            first = f.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].strip().decode("utf-8", "replace").split()
    # `#!/usr/bin/env python3` 给不出具体环境，直接放弃
    if not parts or parts[0].endswith("env"):
        return None
    return parts[0] if os.path.isfile(parts[0]) else None


#: 扫 Windows 启动器里那行 shebang 时的体积上限。distlib 的 launcher 约
#: 100 KB；桌面版那个 frozen 的 `tavotto-cli.exe` 是几十 MB——上限顺带保住了
#: 「这条腿能区分 pip 装的与桌面版自带的」这个性质。
_LAUNCHER_SCAN_MAX = 2 * 1024 * 1024


def _embedded_shebang(exe: str) -> "str | None":
    r"""Windows console script `.exe` 里嵌着的解释器路径。

    pip / pipx 在 Windows 上生成的 `tavotto.exe` 是 distlib 启动器：
    `launcher.exe` + `b"#!<venv>\Scripts\python.exe
"` + 一个 zip。
    **pipx 还会把它复制到共享的 bin 目录暴露出来**，那儿旁边根本没有 python
    （venv 在 `pipx/venvs/tavotto` 里），`_interpreter_beside` 因此一无所获，
    `_shebang_interpreter` 又只读头 512 字节的文本 shebang——两条都落空，
    于是官方推荐的 `pipx install tavotto` 在 Windows 上被判成 `desktop_only`，
    MCP 的工具一个都不出现。复制品里那行 shebang 仍然指着 venv，
    它是「哪个环境装了它」在 Windows 上唯一可靠的答案。
    """
    try:
        if os.path.getsize(exe) > _LAUNCHER_SCAN_MAX:
            return None
        with open(exe, "rb") as fh:
            blob = fh.read(_LAUNCHER_SCAN_MAX)
    except OSError:
        return None
    at = blob.rfind(b"#!")
    while at != -1:
        line = blob[at + 2:blob.find(b"\n", at) if blob.find(b"\n", at) != -1
                    else len(blob)]
        cand = line.strip().strip(b'"').decode("utf-8", "replace").strip()
        # 只认指向真实文件的绝对路径；`#!/usr/bin/env python3` 给不出环境
        if cand and not cand.endswith("env") and os.path.isfile(cand):
            return cand
        at = blob.rfind(b"#!", 0, at)
    return None


def _interpreter_beside(exe: str) -> "list[str]":
    """与 `tavotto.exe` / pipx shim 同目录的 python（Windows 上没有 shebang）。

    桌面版的 `tavotto-cli.exe` 旁边**没有** python（PyInstaller onedir 把运行时
    放在 `_internal/`），所以这条天然区分「pip 装的」与「桌面版带的」。
    """
    base = os.path.dirname(os.path.abspath(exe))
    return [os.path.join(base, n) for n in ("python.exe", "python3", "python")
            if os.path.isfile(os.path.join(base, n))]


def _interpreters_for(found: dict) -> "list[str]":
    """定位结果 → 可能能 import tavotto 的解释器候选。"""
    out: "list[str]" = []
    for exe in (found.get("cmd") or []):
        interp = _shebang_interpreter(exe) or _embedded_shebang(exe)
        if interp:
            out.append(interp)
        out.extend(_interpreter_beside(exe))
    for name in ("python3", "python"):
        which = shutil.which(name)
        if which:
            out.append(which)
    seen, uniq = set(), []
    for p in out:
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            uniq.append(p)
    return uniq


def diagnose(found: dict) -> "tuple[str, str]":
    """定位结果 + 找不到解释器 → (机器可读 code, 说人话的 hint)。

    三态互斥，**不许混成一句「没装 Tavotto」**：
      tavotto_missing            真没装
      desktop_found_cli_missing  桌面版装了，但那一版没带 tavotto-cli（旧安装）
      desktop_only               装的是桌面版：交接能用，但 MCP 要 Python 环境
    """
    handoff = _plugin_locator()
    if found.get("cmd"):
        return "desktop_only", DESKTOP_ONLY_HINT
    if found.get("desktop"):
        return "desktop_found_cli_missing", handoff.UPGRADE_HINT
    return "tavotto_missing", handoff.INSTALL_HINT


def _degraded_server(code: str, hint: str) -> int:
    """跑不起来时的降级 server：能握手，每个工具都如实说缺什么、怎么办。"""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace") \
        if hasattr(sys.stdout, "reconfigure") else None
    tools = [{"name": n, "description": "本工具当前不可用。" + hint,
              "inputSchema": {"type": "object", "properties": {},
                              "additionalProperties": True}}
             for n in ("tavotto_open_figure", "tavotto_apply_overrides",
                       "tavotto_preflight", "tavotto_export",
                       "tavotto_verify_replay", "tavotto_close_session")]

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
                "serverInfo": {"name": "tavotto", "version": "0"},
                "instructions": hint}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "tools/call":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": hint}],
                "structuredContent": {"ok": False, "code": code,
                                      "error": hint}}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"不支持的方法: {method}"}})
    return 0


def main() -> int:
    sys.path.insert(0, HERE)            # 让 `tavotto_mcp` 包可 import
    try:
        import tavotto  # noqa: F401
    except ImportError:
        pass
    else:
        from tavotto_mcp.rpc import StdioConnection
        StdioConnection.hijack_stdout()
        from tavotto_mcp.server import main as run
        return run(sys.argv[1:])

    found = _plugin_locator().find_tavotto()
    for python in _interpreters_for(found):
        if os.path.realpath(python) == os.path.realpath(sys.executable):
            continue                    # 刚试过就是它
        if _importable(python):
            # execv 而不是 subprocess：同一个进程 = stdio 原样继承，
            # host 那边不会看到管道换了一层（也不用管转发与信号）
            os.execv(python, [python, os.path.abspath(__file__), *sys.argv[1:]])
    code, hint = diagnose(found)
    print(f"tavotto-mcp: 没找到能 import tavotto 的解释器（{code}），进入降级模式。"
          + hint, file=sys.stderr)
    return _degraded_server(code, hint)


if __name__ == "__main__":
    raise SystemExit(main())
