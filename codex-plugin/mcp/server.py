#!/usr/bin/env python3
"""Codex 起 MCP server 的那一跳：**先找到装着 tavotto 引擎的解释器，再交棒**。

Codex 用 `python3 ./mcp/server.py` 启动本文件（见 `.mcp.json`），而那个 `python3`
不一定是装了 Tavotto 的那个——用户多半是 `pipx install tavotto` 或装的桌面版。
所以本文件的全部职责就是**运行时解析（resolver）**：

  1. 当前解释器 `import tavotto.engine` 成功 → 直接跑，不折腾；
  2. 否则按固定优先级找一个**验证过能 import 引擎**的解释器（见
     `resolver_candidates()`），`os.execv` 交棒过去——同一个进程，stdio 原样
     继承，host 那边察觉不到换过人。候选链里**用户显式指定的永远最先**，
     插件自管 runtime 其次，从 CLI 反推的与 PATH 兜底最后；
  3. 找不到时起一个**只会说人话的降级 server**：initialize 照常握手，
     tools/list 只列一个 `tavotto_health`（诊断工具，真的可用），六个正常
     工具名的调用回结构化错误——**绝不把不可用的工具伪装成可用**，也绝不
     静默退出（那样用户在 Codex 里看到的只是「插件没有工具」）。

## 「验证过」是硬门槛

每个候选都要真的跑一遍 `import tavotto.engine` 才算数。**frozen 的
`tavotto-cli`（桌面版带的）永远给不出解释器**：它是 PyInstaller 单件，没有
shebang、旁边没有 python——交接（`tavotto open`）用它绰绰有余，MCP server
却要在进程内 import 引擎，这两件事不能混。见 `diagnose()` 的三态。

## 插件自管 runtime（`--provision`）

`python3 mcp/server.py --provision` 在 **Tavotto 用户配置目录**下建一个
插件专属 venv（`mcp-runtime/venv`），装**钉在插件版本上的** tavotto——
不碰系统 Python / Conda / 用户任何全局环境，删掉目录即卸载，重跑即重建
（可复现）。装完 resolver 自动优先用它。这是「桌面版用户零手工配置」的
路：跑一条命令，不用自己建 venv、不用改 PATH。

## 体检（`--health`）

`python3 mcp/server.py --health` 输出一行 JSON：resolver 每一步的结论与
耗时、引擎在哪 / 缺什么、画布产物在不在、桌面版装没装。**它区分得开**
「插件装了但没引擎」「引擎在但画布产物缺失」「一切就绪但 Codex 会话还没
重载工具」这几种在 `codex plugin list` 里长得一模一样的状态。

纯标准库，Python 3.8+（找不到 tavotto 时用户机器上的 python3 可能很老）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
#: 显式指定 MCP server 用哪个解释器（最高优先级；指错了如实报错，不悄悄换）
MCP_PYTHON_ENV = "TAVOTTO_MCP_PYTHON"
#: 渲染解释器的两个环境变量（与 engine/pool.worker_python_env() 同源的名字）。
#: 它们指向的通常是科学栈环境；装了 tavotto 的话 MCP 也能直接用。
WORKER_PYTHON_ENVS = ("TAVOTTO_WORKER_PYTHON", "MM_WORKER_PYTHON")
#: execv 交棒后的防环护栏：棒交过去了 import 还是失败（装了一半的环境），
#: 不许再交第二次——那是无限 exec 循环。
_EXECED_ENV = "TAVOTTO_MCP_EXECED"

#: 只装了桌面版时的那一格。**不能说「没装 Tavotto」**——他明明装了。
DESKTOP_ONLY_HINT = (
    "这台机器上装的是 Tavotto 桌面版。交接（把图交给 Tavotto 窗口打开）照常能用，"
    "但 Codex 里的内嵌画布与六个工具需要一个能 import tavotto 的 Python 环境——"
    "桌面版带的 tavotto-cli 是打包成单文件的可执行程序，给不出解释器。"
    "两条恢复路（可共存）：① 一条命令建插件自管环境："
    "`python3 <插件目录>/mcp/server.py --provision`；"
    "② `pipx install tavotto`（或 `pip install tavotto`）。"
    "装完**新开一次 Codex 会话**——已开的会话不会重新加载工具。"
)


# ------------------------------ 单点探测 -----------------------------------
#: 桥（tavotto_mcp/bridge.py）真正 import 的那组引擎模块。**验证的就是这组**，
#: 不是笼统的 `import tavotto.engine`：2026-08-20 实测，PyPI 的 0.8.0 wheel
#: 发在 telemetry 合并之前——engine 包 import 得动、bridge 一 import 就
#: ImportError，resolver 交棒过去 server 当场崩死（比诚实降级糟得多）。
#: 两侧由 tests/test_mcp_resolver.py::test_bridge_import_probe_matches_the_bridge
#: 对拍，改 bridge 的 import 必须同步这里。
_BRIDGE_IMPORT = (
    "from tavotto.engine import config, handoff, patchspec, "
    "pool, preflight, previewbudget, profiles, profilestore, project_refresh, "
    "readiness, registry, telemetry"
)


def _importable(python: str, timeout: float = 30.0) -> bool:
    """这个解释器装的 tavotto 引擎**够不够本插件用**。

    判据是 `_BRIDGE_IMPORT`（桥需要的整组模块）而不是 `import tavotto`：
    同名空壳、或版本太旧缺模块的环境都不算数——放它过关的下场是交棒后
    在 host 面前崩死。
    """
    try:
        proc = subprocess.run(
            [python, "-c", _BRIDGE_IMPORT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _current_engine_ok() -> bool:
    """当前解释器里的引擎够不够用（与 `_importable` 同一把尺，进程内验）。"""
    try:
        exec(_BRIDGE_IMPORT, {})
    except Exception:  # noqa: BLE001 — 缺模块/坏安装都算不够
        return False
    return True


def _plugin_locator():
    """插件自带的那份定位器（`engine/locate.py` 的镜像，有矩阵测试看着）。

    与本文件同属一个插件包，按相对路径 import 即可——**不在这里抄第三遍**
    路径规则（Tavotto 一份、插件的 handoff 一份，已经是能接受的上限）。
    """
    scripts = os.path.abspath(os.path.join(HERE, "..", "skills", "tavotto-figure", "scripts"))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import handoff  # noqa: PLC0415

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
        line = blob[at + 2 : blob.find(b"\n", at) if blob.find(b"\n", at) != -1 else len(blob)]
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
    return [
        os.path.join(base, n)
        for n in ("python.exe", "python3", "python")
        if os.path.isfile(os.path.join(base, n))
    ]


def _interp_key(path: str) -> str:
    """解释器的**身份键**：规范化路径，但**绝不 realpath**。

    venv 的 `bin/python3` 是指向基础解释器的符号链接，realpath 会把它解析成
    `/opt/homebrew/...python3.13`——于是「自管 venv」被判成「就是当前解释器」
    而**跳过探测**（2026-08-20 实测：provision 刚成功，server 却照样降级）。
    符号链接指向同一个二进制的两个 venv 是**两个不同的解释器**（pyvenv.cfg
    与 argv0 决定 site-packages），身份必须按调用路径算。
    """
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _interpreters_for(found: dict) -> "list[str]":
    """定位结果 → 可能能 import tavotto 的解释器候选（CLI 反推 + PATH 兜底）。"""
    out: "list[str]" = []
    for exe in found.get("cmd") or []:
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
        key = _interp_key(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


# --------------------------- 插件自管 runtime -------------------------------
def managed_runtime_dir() -> str:
    """插件自管环境的家：`<Tavotto 配置目录>/mcp-runtime`。

    落在用户配置目录（目录规则复用定位器的 `config_dir()`，不抄第三遍）：
    插件安装目录归 Codex 管、升级时整个被换掉，**绝不往里写**。
    """
    return os.path.join(_plugin_locator().config_dir(), "mcp-runtime")


def managed_python() -> str:
    """自管 venv 里的解释器路径（存不存在由调用方查）。"""
    venv = os.path.join(managed_runtime_dir(), "venv")
    if os.name == "nt":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python3")


def _configured_worker_python() -> "str | None":
    """Tavotto 设置里指定的渲染解释器（config.json 的 worker.python）。

    读的是 `engine/config.py` 写的那份用户配置；键名两侧同源。装了 tavotto
    的话它同样能当 MCP 解释器（要过 `_importable` 这一关才算数）。
    """
    cfg_path = os.path.join(_plugin_locator().config_dir(), "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    worker = data.get("worker")
    python = worker.get("python") if isinstance(worker, dict) else None
    return python if isinstance(python, str) and python.strip() else None


def resolver_candidates(found: dict, environ: "dict | None" = None) -> "list[tuple[str, str]]":
    """解释器候选（(路径, 来源标签)，前面的赢）。**当前解释器不在表里**——
    它在 `main()` 里已经试过（试过才会走到这儿）。

      1. `TAVOTTO_MCP_PYTHON`       用户显式指给 MCP 的，永远第一
      2. `TAVOTTO_WORKER_PYTHON`    渲染解释器（装了 tavotto 才算数）
      3. 设置里的 worker.python     同上
      4. 插件自管 runtime           `--provision` 建的那个 venv
      5. 从 tavotto CLI 反推        pip/pipx 装的 console script 的 shebang
      6. PATH 里的 python3/python   兜底

    frozen 的 `tavotto-cli` 在 5 里天然出局（无 shebang、旁边无 python）。
    """
    env = os.environ if environ is None else environ
    cands: "list[tuple[str, str]]" = []
    override = (env.get(MCP_PYTHON_ENV) or "").strip()
    if override:
        cands.append((override, "mcp_env"))
    for name in WORKER_PYTHON_ENVS:
        value = (env.get(name) or "").strip()
        if value:
            cands.append((value, "worker_env"))
    configured = _configured_worker_python()
    if configured:
        cands.append((configured, "configured"))
    cands.append((managed_python(), "managed"))
    for python in _interpreters_for(found):
        cands.append((python, "discovered"))
    seen, uniq = set(), []
    for path, source in cands:
        key = _interp_key(path)  # 不 realpath，见 _interp_key
        if key not in seen:
            seen.add(key)
            uniq.append((path, source))
    return uniq


def resolve(found: dict) -> dict:
    """跑一遍候选链。返回
    `{"python": str|None, "source": str|None, "tried": [...]}`；
    `tried` 里是每个候选的 (路径, 来源, 存在与否, import 结论, 耗时 ms)。"""
    tried: "list[dict]" = []
    for python, source in resolver_candidates(found):
        entry = {
            "python": python,
            "source": source,
            "exists": os.path.isfile(python),
            "importable": False,
            "ms": 0,
        }
        # 「刚试过就是它」只认**同一条路径**，不 realpath（见 _interp_key）：
        # venv 的 python 是指向基础解释器的符号链接，realpath 相同 ≠ 同一个
        # 解释器——按 realpath 跳过会把刚 provision 好的自管环境略过不探测。
        if entry["exists"] and _interp_key(python) != _interp_key(sys.executable):
            t = time.monotonic()
            entry["importable"] = _importable(python)
            entry["ms"] = int((time.monotonic() - t) * 1000)
        tried.append(entry)
        if entry["importable"]:
            return {"python": python, "source": source, "tried": tried}
    return {"python": None, "source": None, "tried": tried}


# ------------------------------- 诊断三态 -----------------------------------
def diagnose(found: dict) -> "tuple[str, str]":
    """定位结果 + 找不到解释器 → (机器可读 code, 说人话的 hint)。

    三态互斥，**不许混成一句「没装 Tavotto」**：
      tavotto_missing            真没装
      desktop_found_cli_missing  桌面版装了，但那一版没带 tavotto-cli（旧安装）
      desktop_only               装的是桌面版：交接能用，但 MCP 要 Python 环境
    显式指了解释器却用不了的另算：engine_unavailable（见 `diagnose_resolved`）。
    """
    handoff = _plugin_locator()
    if found.get("cmd"):
        return "desktop_only", DESKTOP_ONLY_HINT
    if found.get("desktop"):
        return "desktop_found_cli_missing", handoff.UPGRADE_HINT
    return "tavotto_missing", handoff.INSTALL_HINT


def diagnose_resolved(found: dict, resolution: dict) -> "tuple[str, str]":
    """带上 resolver 结论的完整诊断。

    用户**显式指定**的解释器（TAVOTTO_MCP_PYTHON）用不了时，必须报
    `engine_unavailable` 并指名道姓——静默落到「桌面版」那格，用户改了半天
    桌面安装，问题其实在他自己设的那个变量上。
    """
    override = next((t for t in resolution["tried"] if t["source"] == "mcp_env"), None)
    if override is not None and not override["importable"]:
        why = (
            "指向的文件不存在"
            if not override["exists"]
            else "import tavotto.engine 失败（那个环境里没装 tavotto）"
        )
        return (
            "engine_unavailable",
            f"{MCP_PYTHON_ENV} 指定的解释器用不了：{override['python']}"
            f"（{why}）。修正它，或者去掉这个变量让 resolver 自己找；"
            "装引擎可用 `python3 <插件目录>/mcp/server.py --provision`。"
            "改完新开一次 Codex 会话。",
        )
    return diagnose(found)


# ------------------------------- 降级 server --------------------------------
#: 正常模式下的七个工具名。降级模式**不把它们列进 tools/list**（列了就是
#: 伪装成可用），但对着旧会话里模型记住的名字调用时，回结构化错误而不是
#: method_not_found——错误里说清缺什么、怎么修。
NORMAL_TOOLS = (
    "tavotto_open_figure",
    "tavotto_apply_overrides",
    "tavotto_preflight",
    "tavotto_export",
    "tavotto_verify_replay",
    "tavotto_refresh_project",
    "tavotto_close_session",
)


#: 恢复步骤（结构化，降级 server 与 --health 共用一份）
def _recovery_steps(code: str) -> "list[str]":
    steps = []
    if code in (
        "desktop_only",
        "engine_unavailable",
        "tavotto_missing",
        "desktop_found_cli_missing",
    ):
        steps.append(
            "方式一（推荐，零配置）：python3 <插件目录>/mcp/server.py"
            " --provision  （在 Tavotto 配置目录下建插件自管环境，"
            "不碰系统 Python）"
        )
        steps.append(
            "方式二：pipx install tavotto（或 pip install tavotto），"
            "或把 TAVOTTO_MCP_PYTHON 指到一个装了 tavotto 的解释器"
        )
    steps.append("装好后**新开一次 Codex 会话**——已开的会话不会重新加载 MCP 工具（这一步最容易漏）")
    steps.append("自检：python3 <插件目录>/mcp/server.py --health")
    return steps


def _degraded_payload(code: str, hint: str, resolution: "dict | None") -> dict:
    return {
        "ok": False,
        "code": code,
        "error": hint,
        "engine": {"available": False, "missing": "一个能 import tavotto.engine 的 Python 解释器"},
        "canvas": {
            "available": False,
            "reason": "内嵌画布跑在 MCP server 里，引擎不可用时它也"
            "不可用。桌面窗口 / 浏览器**不是**内嵌画布的替代品。",
        },
        "unavailable_tools": list(NORMAL_TOOLS),
        "recovery": _recovery_steps(code),
        "tried": (resolution or {}).get("tried", []),
    }


def _degraded_server(
    code: str, hint: str, resolution: "dict | None" = None, stdin=None, stdout=None
) -> int:
    """跑不起来时的降级 server。

    * 能握手（initialize / ping），serverInfo.version 固定 "0"——健康的
      server 报的是 tavotto 版本号，这个 0 就是「引擎不在」的显性信号；
    * tools/list **只列 `tavotto_health`**——它是唯一真的可用的工具；
    * 六个正常工具名的调用回 `isError` + 结构化 code——**绝不回「画布已
      打开」之类的成功**；
    * 不声明 resources——没有引擎就没有画布，声明了就是给 host 一个白框。
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if hasattr(stdout, "reconfigure"):
        try:
            stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    payload = _degraded_payload(code, hint, resolution)
    health_tool = {
        "name": "tavotto_health",
        "title": "Tavotto 健康检查",
        "description": (
            "诊断 Tavotto MCP 的当前状态：引擎在不在、缺什么、"
            "怎么恢复。当前引擎不可用（" + code + "），其余工具"
            "暂不提供。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }

    def send(obj):
        stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        stdout.flush()

    for raw in stdin:
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
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": want if isinstance(want, str) else "2025-11-25",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "tavotto", "version": "0"},
                        "instructions": f"[{code}] {hint}",
                    },
                }
            )
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [health_tool]}})
        elif method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            if name == "tavotto_health":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"[{code}] {hint}\n恢复步骤：\n- "
                                    + "\n- ".join(payload["recovery"]),
                                }
                            ],
                            "structuredContent": payload,
                        },
                    }
                )
            elif name in NORMAL_TOOLS:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "result": {
                            "isError": True,
                            "content": [{"type": "text", "text": f"[{code}] {hint}"}],
                            "structuredContent": payload,
                        },
                    }
                )
            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32601, "message": f"没有这个工具: {name}"},
                    }
                )
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method in ("resources/list", "resources/templates/list"):
            key = "resources" if method == "resources/list" else "resourceTemplates"
            send({"jsonrpc": "2.0", "id": rid, "result": {key: []}})
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"不支持的方法: {method}"},
                }
            )
    return 0


# ------------------------------- 体检与自建 ---------------------------------
def health() -> "tuple[dict, int]":
    """`--health`：一行 JSON 说清现状。退出码 0 = 引擎可用。"""
    t0 = time.monotonic()
    current_ok = _current_engine_ok()
    handoff = _plugin_locator()
    found = handoff.find_tavotto()
    widget_file = os.path.join(HERE, "widget", "canvas.html")
    report: dict = {
        "ok": False,
        "mode": "degraded",
        "desktop": {
            "cli": (found.get("cmd") or [None])[0],
            "source": found.get("source"),
            "desktop": found.get("desktop"),
        },
        "widget": {
            "available": os.path.isfile(widget_file) and os.path.getsize(widget_file) > 0,
            "path": widget_file,
        },
        "managed_runtime": {
            "python": managed_python(),
            "present": os.path.isfile(managed_python()),
        },
        "notes": [
            "engine 可用但 Codex 里还是没有工具？新开一次会话——已开的会话"
            "不会重新加载 MCP 工具，`codex plugin list` 的 enabled 也不代表"
            " server 健康。",
        ],
    }
    if current_ok:
        import tavotto

        report.update(
            ok=True,
            mode="engine",
            python=sys.executable,
            source="current",
            engine_version=tavotto.__version__,
        )
        report["tried"] = [
            {
                "python": sys.executable,
                "source": "current",
                "exists": True,
                "importable": True,
                "ms": 0,
            }
        ]
    else:
        resolution = resolve(found)
        report["tried"] = [
            {
                "python": sys.executable,
                "source": "current",
                "exists": True,
                "importable": False,
                "ms": 0,
            }
        ] + resolution["tried"]
        if resolution["python"]:
            report.update(
                ok=True, mode="engine", python=resolution["python"], source=resolution["source"]
            )
            try:
                proc = subprocess.run(
                    [resolution["python"], "-c", "import tavotto; print(tavotto.__version__)"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                report["engine_version"] = (proc.stdout or "").strip() or None
            except (OSError, subprocess.TimeoutExpired):
                report["engine_version"] = None
        else:
            code, hint = diagnose_resolved(found, resolution)
            report.update(code=code, error=hint, recovery=_recovery_steps(code))
    report["timings"] = {"health_ms": int((time.monotonic() - t0) * 1000)}
    return report, (0 if report["ok"] else 3)


def _plugin_version() -> "str | None":
    manifest = os.path.join(HERE, "..", ".codex-plugin", "plugin.json")
    try:
        with open(manifest, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version.strip() else None


def provision(spec: "str | None" = None) -> "tuple[dict, int]":
    """`--provision`：建插件自管 venv 并装引擎（钉在插件版本上，可复现）。

    * 只写 Tavotto 配置目录下的 `mcp-runtime/`——**绝不动**系统 Python、
      Conda、用户 site-packages、shell 配置；
    * 默认装 `tavotto[worker]==<插件版本>`（版本与插件同步发版；`[worker]`
      带上 matplotlib/numpy——pip 形态的引擎发现不了桌面 App 里的内置
      runtime，自管环境不自带渲染栈的话，没有科学栈的机器上 open 第一步
      就会倒在「找不到渲染解释器」，零配置就落空了）；`--from` 可指
      wheel 文件 / 源码目录 / 任意 pip requirement（离线或开发态用）；
    * 装完**验证** `import tavotto.engine`，验证不过就如实失败——半成品
      环境比没有环境更难查。
    """
    t0 = time.monotonic()
    if spec is None:
        version = _plugin_version()
        spec = f"tavotto[worker]=={version}" if version else "tavotto[worker]"
    root = managed_runtime_dir()
    venv_dir = os.path.join(root, "venv")
    python = managed_python()
    steps: "list[dict]" = []

    def _run(argv, what):
        t = time.monotonic()
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            steps.append({"step": what, "ok": False, "error": str(exc)})
            return False
        steps.append(
            {
                "step": what,
                "ok": proc.returncode == 0,
                "ms": int((time.monotonic() - t) * 1000),
                "tail": (proc.stderr or proc.stdout or "").strip().splitlines()[-5:],
            }
        )
        return proc.returncode == 0

    os.makedirs(root, exist_ok=True)
    if not os.path.isfile(python):
        if not _run([sys.executable, "-m", "venv", venv_dir], "venv"):
            return (
                {
                    "ok": False,
                    "code": "provision_failed",
                    "steps": steps,
                    "error": f"建不出 venv（基础解释器 {sys.executable}）",
                },
                1,
            )
    if not _run([python, "-m", "pip", "install", "--upgrade", spec], "pip"):
        return (
            {
                "ok": False,
                "code": "provision_failed",
                "steps": steps,
                "error": f"pip install {spec} 失败（离线？给 --from 指一个本地 wheel 或源码目录）",
            },
            1,
        )
    if not _importable(python):
        return (
            {
                "ok": False,
                "code": "provision_failed",
                "steps": steps,
                "error": "装完仍 import 不了 tavotto.engine——环境是半成品，"
                "删掉 mcp-runtime 目录后重试",
            },
            1,
        )
    marker = {
        "spec": spec,
        "python": python,
        "provisioned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        with open(os.path.join(root, "provision.json"), "w", encoding="utf-8") as fh:
            json.dump(marker, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return (
        {
            "ok": True,
            "python": python,
            "spec": spec,
            "steps": steps,
            "ms": int((time.monotonic() - t0) * 1000),
            "next": "新开一次 Codex 会话即可在 Codex 内使用 Tavotto 画布",
        },
        0,
    )


# --------------------------------- 主入口 -----------------------------------
def main() -> int:
    argv = sys.argv[1:]
    if "--health" in argv:
        report, rc = health()
        print(json.dumps(report, ensure_ascii=False))
        return rc
    if "--provision" in argv:
        spec = None
        if "--from" in argv:
            at = argv.index("--from")
            spec = argv[at + 1] if at + 1 < len(argv) else None
            if spec is None:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "code": "bad_args",
                            "error": "--from 后面要跟 wheel/源码目录/requirement",
                        },
                        ensure_ascii=False,
                    )
                )
                return 2
        report, rc = provision(spec)
        print(json.dumps(report, ensure_ascii=False))
        return rc

    sys.path.insert(0, HERE)  # 让 `tavotto_mcp` 包可 import
    if _current_engine_ok():
        from tavotto_mcp.rpc import StdioConnection

        StdioConnection.hijack_stdout()
        from tavotto_mcp.server import main as run

        return run(argv)

    found = _plugin_locator().find_tavotto()
    if os.environ.get(_EXECED_ENV) != "1":
        t0 = time.monotonic()
        resolution = resolve(found)
        if resolution["python"]:
            print(
                f"tavotto-mcp: 引擎解释器 {resolution['python']}"
                f"（{resolution['source']}，解析 "
                f"{int((time.monotonic() - t0) * 1000)}ms），交棒。",
                file=sys.stderr,
            )
            os.environ[_EXECED_ENV] = "1"
            # execv 而不是 subprocess：同一个进程 = stdio 原样继承，
            # host 那边不会看到管道换了一层（也不用管转发与信号）
            os.execv(resolution["python"], [resolution["python"], os.path.abspath(__file__), *argv])
    else:
        resolution = {"python": None, "source": None, "tried": []}
    code, hint = diagnose_resolved(found, resolution)
    print(
        f"tavotto-mcp: 没找到能 import tavotto.engine 的解释器（{code}），进入降级模式。" + hint,
        file=sys.stderr,
    )
    return _degraded_server(code, hint, resolution)


if __name__ == "__main__":
    raise SystemExit(main())
