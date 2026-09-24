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
却要在进程内 import 引擎，这两件事不能混。见 `diagnose()` 的四态。

## 插件自管 runtime（`--provision`）

`python3 mcp/server.py --provision` 在 **Tavotto 用户配置目录**下建一个
插件专属 venv（`mcp-runtime/venv`），装**钉在插件版本上的** tavotto——
不碰系统 Python / Conda / 用户任何全局环境，删掉目录即卸载，重跑即重建
（可复现）。装完 resolver 自动优先用它。这是「桌面版用户零手工配置」的
路：跑一条命令，不用自己建 venv、不用改 PATH。**建 venv 的基础解释器先验
版本**（`find_venv_base()`）：跑本文件的 `python3` 可以很老，venv 不能。

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
    "from tavotto.engine import artifactcheck, artifactinspect, config, deprepair, exportjob, exportreq, "
    "figcapture, handoff, interference, normalize, patchspec, pool, preflight, previewbudget, "
    "profiles, profilestore, project_refresh, readiness, registry, telemetry, workdir"
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


#: 引擎的 Python 支持区间——`engine/projectenv.PYTHON_MIN` / `PYTHON_MAX_EXCLUSIVE`
#: 的镜像（插件 import 不到 tavotto，与定位器一样是无法避免的那份重复）。两侧由
#: tests/test_mcp_resolver.py::test_provision_python_range_mirrors_the_engine 对拍，
#: projectenv 那侧又被 test_support_matrix 钉在 pyproject 的 requires-python 上。
#:
#: 它在这里只有一个用途：**建 venv 之前先判基础解释器**。启动本文件的 `python3`
#: 允许很老（本文件只用标准库，3.8 就能跑），但 venv 继承它的版本，而区间外的
#: 解释器上 pip 对 `tavotto==x.y.z` 只会说 "No matching distribution found"
#: （3.9 自带的 pip 21 连「哪些版本因 Requires-Python 被忽略」都不打印）——
#: 2026-09-20 一位 macOS 用户在 Xcode CLT 的 `/usr/bin/python3`（3.9.6）上撞到，
#: Codex 把它读成「0.15.0 还没发」。
PYTHON_MIN = (3, 10)
PYTHON_MAX_EXCLUSIVE = (3, 15)
assert PYTHON_MIN[0] == PYTHON_MAX_EXCLUSIVE[0] == 3, (
    "候选名字按 3.x 枚举，跨大版本要改 _SUPPORTED_MINORS"
)
#: 支持区间里的小版本号，**新的在前**：都能用时挑最新的（wheel 齐全，也是桌面版
#: runtime 所在的那一带）。
_SUPPORTED_MINORS = tuple(range(PYTHON_MAX_EXCLUSIVE[1] - 1, PYTHON_MIN[1] - 1, -1))
_PROBE_VERSION = "import sys; print(sys.version_info[0], sys.version_info[1], sys.executable)"


def python_range_text() -> str:
    """给人看的区间：`3.10–3.14`。"""
    return (
        f"{PYTHON_MIN[0]}.{PYTHON_MIN[1]}–{PYTHON_MAX_EXCLUSIVE[0]}.{PYTHON_MAX_EXCLUSIVE[1] - 1}"
    )


def _python_supported(version: "tuple[int, int] | None") -> bool:
    return version is not None and PYTHON_MIN <= tuple(version) < PYTHON_MAX_EXCLUSIVE


def _probe_python(argv: "list[str]", timeout: float = 15.0) -> "dict | None":
    """真的跑一遍 argv 指向的解释器，回 `{"python": 它自报的路径, "version": (major, minor)}`。

    判据是执行，不是文件名：Windows 商店的 App Execution Alias 叫 `python3.exe`，
    退出码 9009、零输出；pyenv 的 shim 可能指向一个没装的版本；`py -3.13` 这种
    启动器命令要它自己说出 `sys.executable` 才知道解释器在哪。起不来 / 不是
    Python / 超时一律回 None。
    """
    try:
        proc = subprocess.run(
            [*argv, "-c", _PROBE_VERSION], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    parts = (proc.stdout or "").strip().split(" ", 2)
    try:
        version = (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return None
    reported = parts[2].strip() if len(parts) == 3 else ""
    python = reported if reported and os.path.isfile(reported) else argv[0]
    return {"python": python, "version": version}


def _venv_base_candidates(explicit: "str | None" = None) -> "list[list[str]]":
    """建自管 venv 的基础解释器候选（argv 形式，按优先级，只列磁盘上存在的）。

    显式 `--python` 只认它一个——指错了如实报错，不悄悄换（与 `TAVOTTO_MCP_PYTHON`
    同一条纪律）。否则：当前解释器 → PATH 上带版本号的名字（新的在前）→ 常见安装
    位置（Codex 桌面壳起的进程 PATH 往往只有 `/usr/bin:/bin`，Homebrew / python.org
    装的解释器根本不在里面，而 `/usr/bin/python3` 正是那个太老的）→ Windows 的
    `py` 启动器（它知道 python.org 装的每个版本在哪）→ PATH 上的裸 `python3` / `python`。
    """
    if explicit:
        return [[explicit]]
    out: "list[list[str]]" = [[sys.executable]]
    for minor in _SUPPORTED_MINORS:
        which = shutil.which(f"python3.{minor}")
        if which:
            out.append([which])
    if os.name == "nt":
        # python.org 的安装器默认带 `py` 启动器，它知道每个装过的版本在哪——不在这里
        # 抄 Windows 的安装路径规则（路径规则归定位器，tests/test_codex_plugin.py 看着）
        py = shutil.which("py")
        if py:
            out.extend([py, f"-3.{minor}"] for minor in _SUPPORTED_MINORS)
    else:
        for minor in _SUPPORTED_MINORS:
            out.append([f"/opt/homebrew/bin/python3.{minor}"])
            out.append([f"/usr/local/bin/python3.{minor}"])
            out.append(
                [f"/Library/Frameworks/Python.framework/Versions/3.{minor}/bin/python3.{minor}"]
            )
    for name in ("python3", "python"):
        which = shutil.which(name)
        if which:
            out.append([which])
    seen, uniq = set(), []
    for argv in out:
        if len(argv) == 1 and not os.path.isfile(argv[0]):
            continue
        key = " ".join(_interp_key(argv[0]) if i == 0 else a for i, a in enumerate(argv))
        if key not in seen:
            seen.add(key)
            uniq.append(argv)
    return uniq


def find_venv_base(explicit: "str | None" = None) -> "tuple[str | None, list[dict]]":
    """挑第一个在支持区间内的基础解释器；回 (它的路径或 None, 逐个试过的记录)。

    记录里每条都带它自报的版本——失败时用户要看到的是「`/usr/bin/python3` 是 3.9」，
    而不是一句「没找到」。
    """
    tried: "list[dict]" = []
    for argv in _venv_base_candidates(explicit):
        info = _probe_python(argv)
        version = info["version"] if info else None
        entry = {
            "python": " ".join(argv),
            "version": f"{version[0]}.{version[1]}" if version else None,
            "supported": _python_supported(version),
        }
        tried.append(entry)
        if entry["supported"]:
            return info["python"], tried
    return None, tried


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


# ------------------------------- 诊断四态 -----------------------------------
#: 已装插件里那份构建清单的文件名（ADR 0043）。**它是 `engine/pluginmanifest.BUILD_MANIFEST`
#: 的镜像**：这里要诊断的恰恰是「import 不到 tavotto」的机器，跨过去读那个常量做不到，
#: 只能把这个名字重复一遍（两侧由 tests/test_mcp_diagnose.py 对拍）。
BUILD_MANIFEST = "plugin-build.json"

#: 问引擎版本用的子命令。`tavotto doctor --json` 自 0.8.0 起就在，跑在纯标准库那一层
#: （不 import Flask / matplotlib），而且**只读**（写安装清单要另给 `--write-manifest`）。
#: 要问的偏偏是旧引擎，所以不能用只有新版本才认得的入口。
_VERSION_ARGV = ("doctor", "--json")


def required_tavotto_version() -> "str | None":
    """本插件要求的最低引擎版本；读不到 / 形状不对回 None（= **不知道**，不是 0）。

    唯一出处是构建时写进 `plugin-build.json` 的 `min_tavotto_version`，那个值来自
    `scripts/make_plugin_manifest.MIN_TAVOTTO_VERSION`——**这里不写第二份版本号**。
    清单是构建物，源码目录里没有：开发态一律「不知道」，诊断退回原来的三态。
    """
    path = os.path.join(HERE, "..", BUILD_MANIFEST)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    required = data.get("min_tavotto_version") if isinstance(data, dict) else None
    return required if isinstance(required, str) and required.strip() else None


def _tavotto_cli_version(cmd: "list[str]", timeout: float = 30.0) -> "str | None":
    """这台机器上的 `tavotto` 自报的版本；问不出来回 None（= **不知道**）。

    **退出码不作数**：`doctor` 发现问题时返回非 0，而那行 JSON 照样带着版本号，
    我们要的只有 `version` 这一个字段。输出按 UTF-8 解码（子进程那侧由
    `cli.use_utf8_streams()` 钉住），不吃这台机器的区域编码。
    """
    try:
        proc = subprocess.run(
            [*cmd, *_VERSION_ARGV],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    # 那行 JSON 在最后；旧版本可能在它前面还写了别的，所以从后往前找第一行认得的
    for line in reversed((proc.stdout or b"").decode("utf-8", "replace").splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        version = data.get("version") if isinstance(data, dict) else None
        if isinstance(version, str) and version.strip():
            return version.strip()
    return None


def _plugin_update_check():
    """插件自带的那份版本比较（`update_check.parse_version` / `is_newer`）；
    **import 不到就回 None**（= 不知道）。

    `0.10.0 < 0.9.0` 在**字符串**序里成立，而两位数小版本正是本格的现场——版本
    比较不在这里写第二份。

    它不是第三方依赖：`update_check.py` 与 `handoff.py` 同一个目录、同属这个插件包，
    随插件一起发行（`tests/test_mcp_diagnose.py::test_the_version_comparison_ships_
    in_the_bundle`），而那个目录由 `_plugin_locator()` 挂上 sys.path——所以先调它，
    这一步同时是 `diagnose()` 的第一句，import 之前一定跑过。

    仍然接住 ImportError：这一句跑在**降级路径**上，异常逃出去就是 server 起不来，
    用户看到的是「插件一个工具都没有」——比一句不精确的诊断严重得多。装残的目录
    回「不知道」，不回崩溃。
    """
    _plugin_locator()
    try:
        import update_check  # noqa: PLC0415
    except ImportError:
        return None
    return update_check


def engine_too_old(cmd: "list[str]") -> "tuple[str, str] | None":
    """这台机器上的引擎比插件要求的下限还旧吗？是则回 (它的版本, 要求的版本)。

    **三环缺一就回 None**：清单里没写下限、CLI 问不出版本、版本号解不出来——那些都是
    「不知道」，不是「太旧」。把不知道折进这一格等于再造一个万能兜底，而 #285 的根因
    正是有人把一条正交的轴折进了最近的那个取值。

    先读清单再起子进程，**顺序不能反**：清单是一次本地文件读，读不到就一个进程都不起
    （降级判定有时间预算，见 tests/test_mcp_stdio.py）。
    """
    required = required_tavotto_version()
    if not required:
        return None
    have = _tavotto_cli_version(cmd)
    if not have:
        return None
    compare = _plugin_update_check()
    # 比不了（模块不在）与 is_newer 回 None（有一侧解不出来）都是「不知道」，不是「太旧」
    if compare is None or compare.is_newer(required, have) is not True:
        return None
    return have, required


def engine_too_old_hint(have: str, required: str, plugin: "str | None" = None) -> str:
    """`engine_too_old` 的话术：**两个版本号都要说出口**。

    「你的引擎太旧了」里没有用户能执行的东西——他要知道自己现在是哪一版、这个插件
    要的是哪一版，才判断得出该升引擎还是该把插件退回去。
    """
    # 版本号两侧都要留空格：中文里数字紧贴汉字会读成一个词（实测「插件 0.13.0需要」）
    who = f"插件 {plugin} 需要" if plugin else "这个插件需要"
    return (
        f"这台机器上的 Tavotto 是 {have}，而{who} {required} 或更新的引擎："
        f"桥要 import 的那组引擎模块在 {have} 里还没有，所以 Codex 里的内嵌画布与整组"
        "工具都起不来（交接——把图交给 Tavotto 窗口打开——不受影响，那条路只要求 CLI "
        "能执行）。恢复：**升级引擎**（`pipx upgrade tavotto`，或 `pip install -U "
        "tavotto`；桌面版用户升级桌面版），或者反过来把插件退回与这台引擎匹配的那一版。"
        "升完**新开一次 Codex 会话**——已开的会话不会重新加载工具。"
    )


def diagnose(found: dict) -> "tuple[str, str]":
    """定位结果 + 找不到解释器 → (机器可读 code, 说人话的 hint)。

    四态互斥，**不许混成一句「没装 Tavotto」**：
      tavotto_missing            真没装
      desktop_found_cli_missing  桌面版装了，但那一版没带 tavotto-cli（旧安装）
      desktop_only               装的是桌面版：交接能用，但 MCP 要 Python 环境
      engine_too_old             装了，但那个引擎比插件要求的下限还旧（#285）
    显式指了解释器却用不了的另算：engine_unavailable（见 `diagnose_resolved`）。

    **判别顺序：`engine_too_old` 必须排在 `desktop_only` 前面。** 两格的前提是同一个
    （`found["cmd"]` 有东西），而 `desktop_only` 只问「PATH / 安装位置上有没有 tavotto」
    ——它是更宽的那一格，排在前面就会把每一个更窄的答案吃掉。#285 就是这么来的：
    `pip install tavotto==0.10` 的用户被告知「这台机器上装的是 Tavotto 桌面版」。
    窄的先判、宽的兜底。

    反过来，它排在 `engine_unavailable`（在 `diagnose_resolved` 里）**后面**：用户显式
    设了 `TAVOTTO_MCP_PYTHON` 却用不了时，该修的是那个变量，与 PATH 上那个 tavotto 是
    哪一版无关——先报版本会把他支去升级一个可能完全够用的引擎。
    """
    handoff = _plugin_locator()
    if found.get("cmd"):
        too_old = engine_too_old(found["cmd"])
        if too_old is not None:
            return "engine_too_old", engine_too_old_hint(*too_old, plugin=_plugin_version())
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
    if managed_runtime_stale(resolution):
        return "managed_runtime_stale", MANAGED_STALE_HINT
    return diagnose(found)


#: 自管环境在、却 import 不过这一版插件要的引擎——插件升级之后最常见的形状：
#: `codex plugin marketplace upgrade` 换掉了插件目录，而 `mcp-runtime/venv` 在
#: 配置目录里原样留着上一版引擎（#487：0.14.0 的环境撞上 0.15.0 的桥）。
MANAGED_STALE_HINT = (
    "插件自管环境里的引擎跟当前插件对不上（多半是插件升级后环境还是旧版）。"
    "把它重装到插件对应的版本即可：python3 <插件目录>/mcp/server.py --provision，"
    "装完**新开一次 Codex 会话**。"
)
#: 启动器真的在后台起了重装时，降级模式对用户说的那句（只有 main() 起了才用它）。
MANAGED_STALE_KICKED_HINT = (
    "插件自管环境里的引擎跟当前插件对不上（多半是插件升级后环境还是旧版）。"
    "启动器已在后台把它重装到插件对应的版本，通常一两分钟；装完**新开一次 Codex 会话**"
    "即可使用。后台没装成时手动跑：python3 <插件目录>/mcp/server.py --provision"
)


def managed_runtime_stale(resolution: dict) -> bool:
    """自管 venv 的解释器**在**、却 import 不过 `_BRIDGE_IMPORT`。

    只认 resolver 真的探过的那一条（`source == "managed"` 且 exists），不按文件名猜：
    环境不存在是 `tavotto_missing` / `desktop_only` 那几格的事，不是「旧了」。
    """
    return any(
        t["source"] == "managed" and t["exists"] and not t["importable"]
        for t in resolution.get("tried", [])
    )


#: 关掉启动时的后台自动重装（测试、或不想让启动器联网的用户）。
NO_AUTO_PROVISION_ENV = "TAVOTTO_MCP_NO_AUTO_PROVISION"


def _provision_lock_path() -> str:
    return os.path.join(managed_runtime_dir(), "provision.lock")


def _try_lock_fd(fd: int) -> bool:
    """在 fd 上非阻塞地拿**内核**排他锁：拿到 True，别人持有 False。

    用操作系统的文件锁而不是「锁文件 + mtime + 令牌」（#548 Codex 评审，多轮）：
    持有者无论正常结束、崩溃还是被杀，内核都在它退出时释放锁——没有「过期锁」，
    也就不需要接管、心跳、令牌，那一整套「核对所有权再改」在纯文件语义下永远
    不是原子的。POSIX 用 `flock`（按打开的文件描述，同进程两个 fd 也互斥），
    Windows 用 `msvcrt.locking` 锁第 0 字节（文件为空也能锁）。
    只有**被占用**才回 False；锁子系统自己的错（ENOLCK、EIO、文件系统不支持锁……）
    照常抛 OSError——当成「别人在装」会让手动与自动修复永远原地等一个不存在的持有者
    （#548 评审 P2）。"""
    import errno

    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            # 被别的句柄锁着时 CRT 报 EACCES（部分版本 EDEADLOCK）
            if exc.errno in (errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)):
                return False
            raise
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:  # EWOULDBLOCK / EAGAIN：被占用
        return False


def _acquire_provision_lock() -> "int | None":
    """拿重装锁：拿到回持锁的 fd（关掉它 / 进程退出即释放），别人持有回 None。
    打不开锁文件（EMFILE、只读盘……）照常抛 OSError，由调用方报出来。"""
    os.makedirs(managed_runtime_dir(), exist_ok=True)
    fd = os.open(_provision_lock_path(), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        held = _try_lock_fd(fd)
    except OSError:
        os.close(fd)
        raise
    if held:
        return fd
    os.close(fd)
    return None


def _running_executable_is_locked() -> bool:
    """正在跑的解释器文件删不掉（Windows）；POSIX 上 unlink 正在跑的文件没问题。"""
    return os.name == "nt"


def _running_from_managed_venv() -> bool:
    venv = os.path.normcase(os.path.abspath(os.path.join(managed_runtime_dir(), "venv")))
    exe = os.path.normcase(os.path.abspath(sys.executable or ""))
    return exe.startswith(venv + os.sep)


def _release_provision_lock(fd: "int | None") -> None:
    if fd is None:
        return
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    try:
        os.close(fd)  # POSIX 上关 fd 即释放 flock
    except OSError:
        pass


def kick_background_provision() -> dict:
    """在后台起一次 `--provision`，把自管环境重装到插件版本（#487）。

    **不在启动路径上同步跑 pip**：Codex 给 MCP server 的启动预算是
    `startup_timeout_sec`（30 s），联网装一遍科学栈可能远超它——同步跑的结果是
    host 判启动失败、连降级 server 都没有。所以这里只 spawn 一个脱离本进程的
    子进程，本次会话照常以降级模式回话、把「正在后台升级」说出口；装完下一次
    新会话就是正常模式。

    这里只**探一下**锁（拿到立刻放）来省掉明显多余的 spawn；真正的互斥在子进程
    `--provision` 里——它改环境之前自己拿内核锁，拿不到就不动环境。所以几个会话
    同时起、各自探到空闲而各起一个子进程也无妨：只有一个会真的跑 pip。
    返回 `{"started": bool, "reason": str, "log": path}`，进 health 与降级 payload。
    """
    root = managed_runtime_dir()
    log = os.path.join(root, "provision.log")
    if os.environ.get(NO_AUTO_PROVISION_ENV) == "1":
        return {"started": False, "reason": "disabled", "log": log}
    if (
        _running_executable_is_locked()
        and _running_from_managed_venv()
        and not _python_supported(tuple(sys.version_info[:2]))
    ):
        # 本 server 正跑在这个自管 venv 的 python.exe 上，而它的版本在区间外——
        # --provision 要 `venv --clear` 整个重建，Windows 删不掉正在跑的 python.exe，
        # 每次启动都会失败一遍（#548 评审 P2）。不起，明确说要退出 Codex 后手动重建。
        # launch.cmd 只在区间内才优先用自管 venv，所以只有机器上别无 Python 时才走到这。
        return {"started": False, "reason": "venv_in_use", "log": log}
    try:
        probe = _acquire_provision_lock()
    except OSError as exc:
        return {"started": False, "reason": f"lock_failed: {exc}", "log": log}
    if probe is None:
        return {"started": False, "reason": "already_running", "log": log}
    _release_provision_lock(probe)
    try:
        out = open(log, "a", encoding="utf-8")
    except OSError as exc:
        return {"started": False, "reason": f"cannot_write: {exc}", "log": log}
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True  # host 关掉本 server 时不连带杀掉 pip
    env = dict(os.environ)
    env.pop(_EXECED_ENV, None)
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--provision"], env=env, **kwargs
        )
    except OSError as exc:
        return {"started": False, "reason": f"spawn_failed: {exc}", "log": log}
    finally:
        out.close()
    return {"started": True, "reason": "stale_managed_runtime", "log": log}


# ------------------------------- 降级 server --------------------------------
#: 正常模式下 `tavotto_health` 之外的全部工具名。降级模式**不把它们列进
#: tools/list**（列了就是伪装成可用），但对着旧会话里模型记住的名字调用时，
#: 回结构化错误而不是 method_not_found——错误里说清缺什么、怎么修。
NORMAL_TOOLS = (
    "tavotto_open_figure",
    "tavotto_apply_overrides",
    "tavotto_normalize_figure",
    "tavotto_preflight",
    "tavotto_export",
    "tavotto_verify_replay",
    "tavotto_refresh_project",
    "tavotto_session_state",
    "tavotto_close_session",
)


#: 恢复步骤（结构化，降级 server 与 --health 共用一份）
def _recovery_steps(code: str) -> "list[str]":
    steps = []
    if code == "engine_too_old":
        # 这一格的用户**已经装了**引擎，缺的不是「一个环境」而是「新一点的版本」。
        # 给 `--provision` 会让他在旁边再建一个环境，原来那个照样旧——一台装了两份
        # tavotto 的机器比一台装旧了的机器更难查。
        steps.append(
            "升级引擎：pipx upgrade tavotto（或 pip install -U tavotto）；"
            "桌面版用户升级桌面版到匹配的版本"
        )
        steps.append("或者反过来：把插件退回与这台机器上的引擎匹配的那一版")
    elif code == "managed_runtime_stale":
        # 缺的不是「一个环境」，是**这个**环境跟插件对不上：启动时 main() 会在后台
        # 重装它，用户要做的只有等它装完、新开会话。手动那条留作兜底（离线 / 后台失败）。
        steps.append(
            "Codex 启动本插件时，启动器会在后台把自管环境重装到插件对应的版本（日志："
            "<Tavotto 配置目录>/mcp-runtime/provision.log），通常一两分钟"
        )
        steps.append("没装成（离线等）或想立刻修好：python3 <插件目录>/mcp/server.py --provision")
    elif code in (
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
        **(
            {"auto_provision": resolution["auto_provision"]}
            if resolution and "auto_provision" in resolution
            else {}
        ),
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
                                    # code 只留在 structuredContent 里：这段是
                                    # 念给用户听的。
                                    "text": hint
                                    + "\n恢复步骤：\n- "
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
                            "content": [
                                {
                                    "type": "text",
                                    "text": hint
                                    + "\n恢复步骤：\n- "
                                    + "\n- ".join(payload["recovery"]),
                                }
                            ],
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
            # 恢复步骤第一条是 --provision：先替它把「拿什么建 venv」探一遍，让读体检
            # 的人（多半是模型）看得到「启动器是 3.9、但机器上有 3.13 可用」这一行，
            # 而不是等 pip 的那句 "No matching distribution found" 再来猜。
            base, tried = find_venv_base()
            report["provision"] = {
                "launcher_python": sys.executable,
                "launcher_version": f"{sys.version_info[0]}.{sys.version_info[1]}",
                "python_range": python_range_text(),
                "base": base,
                "tried": tried,
            }
            if base is None:
                report["notes"].append(
                    f"这台机器上没有 Python {python_range_text()} 的解释器，--provision 会以"
                    " no_supported_python 失败：先装一个（或走 pipx）。"
                )
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


def provision(spec: "str | None" = None, python_base: "str | None" = None) -> "tuple[dict, int]":
    """`--provision`：建插件自管 venv 并装引擎（钉在插件版本上，可复现）。

    * 只写 Tavotto 配置目录下的 `mcp-runtime/`——**绝不动**系统 Python、
      Conda、用户 site-packages、shell 配置；
    * **基础解释器先验版本再建 venv**（`find_venv_base()`）：启动本文件的
      `python3` 不一定在引擎的支持区间里（macOS 上它常是 Xcode CLT 的 3.9），
      venv 会原样继承它的版本，然后 pip 只会说一句 "No matching distribution
      found"。区间外就换一个候选；一个都没有就以 `no_supported_python` 失败并把
      每个候选的版本说出口——**不在区间外的解释器上起 pip**。上一次在区间外
      解释器上建出来的 venv 也照此重建（`venv --clear`）。`--python` 显式指定
      时只认它一个：先验它，已有的 venv 也换到它上面，指错了如实报错；
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
    # 已有的 venv 也要验：它继承了当年那个基础解释器的版本。区间外 / 已坏就重建，
    # 否则用户删目录之前每次重跑都撞同一堵墙。
    existing = _probe_python([python]) if os.path.isfile(python) else None
    venv_version = existing["version"] if existing else None
    # 显式 --python 就是「用这个建」：已有的 venv 在不在区间内都不算数，先验它、再换到它
    # 上面——否则指错了也会静默沿用旧环境报成功（#453 评审 P2）
    if python_base or not _python_supported(venv_version):
        base, tried = find_venv_base(python_base)
        steps.append({"step": "base", "ok": base is not None, "python": base, "tried": tried})
        if base is None:
            listed = "；".join(
                f"{t['python']} 是 {t['version']}" if t["version"] else f"{t['python']} 起不来"
                for t in tried
            )
            return (
                {
                    "ok": False,
                    "code": "no_supported_python",
                    "steps": steps,
                    "tried": tried,
                    "error": (
                        f"这台机器上找不到 Python {python_range_text()} 的解释器来建自管环境"
                        f"（{listed}），而 {spec} 只装得进这个区间。"
                    ),
                    "recovery": [
                        f"装一个 Python {python_range_text()}（python.org 安装包、"
                        "`brew install python@3.13`、`winget install Python.Python.3.13`），"
                        "然后重跑同一条 --provision（它会自己找到新装的那个）",
                        "或者用 --python <解释器路径> 明确指一个",
                        '或者 pipx install "tavotto[worker]"（pipx 自己管解释器）',
                    ],
                },
                1,
            )
        argv = [base, "-m", "venv"]
        if os.path.isdir(venv_dir):
            argv.append("--clear")  # 上一次在区间外解释器上建的：整个换掉，不在旧壳上叠
        argv.append(venv_dir)
        if not _run(argv, "venv"):
            return (
                {
                    "ok": False,
                    "code": "provision_failed",
                    "steps": steps,
                    "error": f"建不出 venv（基础解释器 {base}）",
                },
                1,
            )
        # venv 继承基础解释器的版本；它能不能用由下面的 `_importable` 验，不再探一次
        venv_version = tuple(int(x) for x in tried[-1]["version"].split("."))
    if not _run([python, "-m", "pip", "install", "--upgrade", spec], "pip"):
        # 走到这里基础解释器已经验过在区间内，所以剩下的多半是索引侧：离线、或 pip
        # 镜像还没同步这一版（新版发出当天在国内镜像上常见）。
        version_text = f"{venv_version[0]}.{venv_version[1]}" if venv_version else "?"
        return (
            {
                "ok": False,
                "code": "provision_failed",
                "steps": steps,
                "error": (
                    f"pip install {spec} 失败。基础解释器是 Python {version_text}，在支持"
                    "区间内，所以不是版本问题——多半是离线，或 pip 镜像还没同步这一版："
                    "设 PIP_INDEX_URL=https://pypi.org/simple 后重跑，"
                    "或给 --from 指一个本地 wheel / 源码目录"
                ),
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
    python_version = f"{venv_version[0]}.{venv_version[1]}" if venv_version else None
    marker = {
        "spec": spec,
        "python": python,
        "python_version": python_version,
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
            "python_version": python_version,
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
        values = {}
        for flag, what in (("--from", "wheel/源码目录/requirement"), ("--python", "解释器路径")):
            values[flag] = None
            if flag in argv:
                at = argv.index(flag)
                values[flag] = argv[at + 1] if at + 1 < len(argv) else None
                if values[flag] is None:
                    print(
                        json.dumps(
                            {"ok": False, "code": "bad_args", "error": f"{flag} 后面要跟{what}"},
                            ensure_ascii=False,
                        )
                    )
                    return 2
        # 改环境之前先拿内核锁：后台那次与手动 / `tavotto codex install` 跑的这次
        # 同一时间只许一个动 venv（#548 评审 P2）；拿不到就不动，明确报出来。
        try:
            lock = _acquire_provision_lock()
        except OSError as exc:
            lock, busy = None, str(exc)
        else:
            busy = None if lock is not None else "running"
        if lock is None:
            report = {
                "ok": False,
                "code": "provision_in_progress" if busy == "running" else "provision_lock_failed",
                "error": "另一次重装正在进行（后台自动修复或另一条 --provision），等它结束后再试"
                if busy == "running"
                else f"拿不到重装锁：{busy}",
            }
            print(json.dumps(report, ensure_ascii=False))
            return 3
        try:
            report, rc = provision(values["--from"], python_base=values["--python"])
        finally:
            _release_provision_lock(lock)
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
    if code == "managed_runtime_stale":
        auto = kick_background_provision()
        resolution = {**resolution, "auto_provision": auto}
        if auto["started"] or auto["reason"] == "already_running":
            hint = MANAGED_STALE_KICKED_HINT
        elif auto["reason"] == "venv_in_use":
            hint = (
                "插件自管环境要整个重建（它的 Python 版本不在 "
                + python_range_text()
                + "），可本会话正跑在它里面的 python.exe 上，Windows 删不掉正在运行的文件。"
                "请先装一个 Python " + python_range_text() + "，退出 Codex，在终端运行："
                "py -3 <插件目录>/mcp/server.py --provision，然后重新打开 Codex。"
            )
        else:
            hint = (
                "插件自管环境里的引擎跟当前插件对不上（多半是插件升级后环境还是旧版），"
                "后台自动重装没有启动（" + auto["reason"] + "）。请手动跑："
                "python3 <插件目录>/mcp/server.py --provision，然后新开一次 Codex 会话。"
            )
    print(
        f"tavotto-mcp: 没找到能 import tavotto.engine 的解释器（{code}），进入降级模式。" + hint,
        file=sys.stderr,
    )
    return _degraded_server(code, hint, resolution)


if __name__ == "__main__":
    raise SystemExit(main())
