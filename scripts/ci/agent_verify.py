#!/usr/bin/env python3
"""开发 Agent 的统一验证入口：按**白名单场景 ID** 调用仓库已有的验证器，交回结构化证据。

    python scripts/ci/agent_verify.py list [--json]
    python scripts/ci/agent_verify.py run <场景ID>... [--out DIR] [--json] [--python PY]

为什么存在（ADR 0131）：Claude / Codex 这类 Agent 改完代码后要回答「我验证了什么、
结果是什么、哪些没验证」，而仓库里的验证器各说各话——`perf_report.py` 严重卡顿也
退 0、`bench_render.py` 缺字体时是一个 HTTP 500 的 traceback、pytest 的 skip 混在绿里。
这里不重写任何一个验证器，只做三件它们各自做不了的事：

* **白名单**：只认 `SCENARIOS` 里登记的 ID，argv 是登记时写死的模板，不接受外部
  传入的命令或参数——Agent 能触发的只有维护者审过的那几条命令。
* **结果诚实**：`result` 是闭集枚举，skipped / not_run / environment_unavailable /
  not_reproduced 各自独立，**任何一个都不会被聚合成 passed**；前置条件不满足
  （缺字体、缺 Python 包、缺测试文件）在执行之前就判出来，不让它伪装成产品失败，
  也不让产品失败伪装成环境问题。
* **可独立审查**：每个场景在 `--out/<id>/` 下有自己的数据目录、原始 stdout/stderr
  与证据文件；`report.json` 记 commit、环境事实、退出码、基线状态、证据 sha256
  和**未验证项**。报告里不写本机绝对路径（argv 保留占位符、证据写相对路径），
  但原始日志是本机证据，可能含路径——它们只留在本机，不上传。

刻意不做：不是服务、不常驻、不进 CI（CI 已有自己的门禁与证据链，见
`docs/rules/ci/verification-chain.md`）；不更新任何基线；纯标准库，与 `_common` 同一假设。
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from _common import run_metadata

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 1
REPORT_KIND = "tavotto-agent-verify"

# --------------------------------------------------------------------------
# 结果枚举与聚合——唯一出处
# --------------------------------------------------------------------------
#: 从坏到好。`overall` 取所有场景里最靠前的那个；只有全部 passed 才是 passed。
SEVERITY = (
    "error",
    "failed",
    "environment_unavailable",
    "not_run",
    "skipped",
    "not_reproduced",
    "passed",
)
RESULTS = frozenset(SEVERITY)

#: 退出码只对本地 Agent 有意义：CI 的 pwsh 步骤会把非 0/1 折成 1
#: （docs/rules/ci/pwsh-exit-codes.md），那里以 report.json 的 `overall` 为准。
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ENV = 3
EXIT_INCOMPLETE = 4

_EXIT_BY_RESULT = {
    "passed": EXIT_PASSED,
    "failed": EXIT_FAILED,
    "error": EXIT_FAILED,
    "environment_unavailable": EXIT_ENV,
    "not_run": EXIT_INCOMPLETE,
    "skipped": EXIT_INCOMPLETE,
    "not_reproduced": EXIT_INCOMPLETE,
}


def aggregate(results: list[str]) -> str:
    """多个场景结果 → 一个。空列表不是 passed：什么都没跑就是 not_run。"""
    if not results:
        return "not_run"
    unknown = set(results) - RESULTS
    if unknown:
        raise ValueError(f"unknown result {sorted(unknown)}")
    return min(results, key=SEVERITY.index)


def exit_code_for(overall: str) -> int:
    return _EXIT_BY_RESULT[overall]


# --------------------------------------------------------------------------
# 场景登记表
# --------------------------------------------------------------------------
#: argv 占位符：`{python}` 目标解释器、`{evidence}` 本场景证据目录、
#: `{private}` 本场景私有目录（不进报告、跑完删除）、`{work}` 临时工作目录。
#: 相对路径一律相对仓库根。登记新场景前先读 docs/rules/ci/agent-verification.md。


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    #: pytest：从 junit 读通过 / 失败 / 跳过计数，不信退出码一个数。
    parser: str = "exit_code"  # exit_code | pytest | perf_manifest | perf_agent


@dataclass(frozen=True)
class Scenario:
    id: str
    summary: str
    kind: str  # regression | measurement | selftest
    steps: tuple[Step, ...]
    timeout_s: int
    preconditions: tuple[str, ...] = ()
    #: 这个场景「通过」到底证明了什么——写给读报告的人，防止把 passed 读大。
    claim: str = ""
    unverified: tuple[str, ...] = ()
    owner_doc: str = ""
    platforms: tuple[str, ...] = ("darwin", "linux", "win32")
    issue: str = ""
    extra_env: tuple[tuple[str, str], ...] = field(default=())
    #: 结果语义的附加标注（不进结果闭集）。`diagnostic_only`：被登记的验证器自己只产出诊断，
    #: 没有 pass 的概念；场景 passed 只说明验证器逻辑自检通过，不是任何性能 / 呈现结论。
    semantics: str = ""


def _pytest(*paths: str) -> Step:
    return Step(
        "pytest",
        (
            "{python}",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            "{work}/pytest",
            "--junitxml",
            "{evidence}/junit.xml",
            *paths,
        ),
        parser="pytest",
    )


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in (
        Scenario(
            id="perf.manifest_timing",
            summary="真实后端 manifest 热态分段计时（bench_render → perf_manifest_report）",
            kind="measurement",
            steps=(
                Step(
                    "bench_render",
                    (
                        "{python}",
                        "scripts/bench_render.py",
                        "--python",
                        "{python}",
                        "--plane",
                        "python",
                        "--repeat",
                        "9",
                        "--json",
                        "{private}/bench-render.json",
                    ),
                ),
                Step(
                    "perf_manifest_report",
                    (
                        "{python}",
                        "scripts/perf_manifest_report.py",
                        "{private}/bench-render.json",
                        "--json",
                        "{evidence}/manifest-summary.json",
                    ),
                    parser="perf_manifest",
                ),
            ),
            timeout_s=900,
            preconditions=(
                "fonts",
                "python_module:matplotlib",
                "python_module:flask",
                "file:examples/figures/tavotto_registry.json",
            ),
            claim=(
                "passed = 每个示例面板都拿到 ≥9 个完整且阶段守恒的热态样本"
                "（tavotto-perf-manifest/1 status=recorded）；不是「性能达标」"
            ),
            unverified=(
                "性能是否回归：没有钉住的同机基线，comparison_verdict 原样透传，不判快慢",
                "workerd（Rust supervisor）控制面未测，只跑 Python 池",
                "画布拖动 / 上屏延迟（ADR 0075）要真机探针报告，本场景不覆盖",
            ),
            owner_doc="docs/perf-manifest-agent.md",
        ),
        Scenario(
            id="perf.manifest_analyzer.selftest",
            summary="perf_manifest_report 分析器自身的反向用例（离线、确定性）",
            kind="selftest",
            steps=(_pytest("tests/test_perf_manifest_report.py"),),
            timeout_s=300,
            preconditions=("python_module:pytest", "file:tests/test_perf_manifest_report.py"),
            claim="passed = 分析器拒绝不完整 / 非法 / 外来数据的用例全部通过，且无 skip",
            unverified=("只验分析器逻辑，不产生任何真实计时",),
            owner_doc="docs/perf-manifest-agent.md",
        ),
        Scenario(
            id="perf.agent.symbol_ui",
            summary="真实后端 + Playwright 的符号 / 预设插入交互计时（perf_agent run，不含拖动、不比基线）",
            kind="measurement",
            steps=(
                Step(
                    "perf_agent_run",
                    (
                        "{python}",
                        "scripts/perf_agent.py",
                        "run",
                        "--browser",
                        "chromium",
                        "--repeat",
                        "16",
                        "--out",
                        "{evidence}/run.json",
                        "--summary",
                        "{evidence}/summary.json",
                    ),
                    parser="perf_agent",
                ),
            ),
            timeout_s=1500,
            preconditions=(
                "fonts",
                "python_module:matplotlib",
                "python_module:flask",
                "tool:node",
                "tool:pnpm",
                "file:web/node_modules/@playwright/test/package.json",
                "any_file:src/tavotto/web/index.html|web/dist/index.html",
                "playwright_browser:chromium",
                "file:scripts/perf_agent.py",
            ),
            claim=(
                "passed = 五个符号 / 预设交互场景在 chromium 里都拿到了完整样本"
                "（tavotto-perf-agent/1 status=ok，符号套件 ok）；"
                "不是「性能达标」，也不是「没有回归」"
            ),
            unverified=(
                "性能是否回归：没有同机基线，不传 --baseline，regression_status 原样透传、绝不更新基线",
                "拖动：不含 --include-drag，canvas_drag=not_requested，拖动性能未测",
                "指标是 click capture 到第二个 rAF 的浏览器代理，不是 GPU 上屏或端到端延迟",
                "原生窗口移动 / WindowServer / 拖影（见 perf.macos_window.selftest 与 needs_native_mac_capture）",
                "webkit 未测；前端构建是否对应当前源码不核对（src/tavotto/web 优先于 web/dist）",
            ),
            owner_doc="docs/perf-agent.md",
        ),
        Scenario(
            id="perf.agent.selftest",
            summary="perf_agent 的反向用例：摘要状态机、基线只读、比较门禁（离线、确定性）",
            kind="selftest",
            steps=(_pytest("tests/test_perf_agent.py"),),
            timeout_s=900,
            preconditions=("python_module:pytest", "file:tests/test_perf_agent.py"),
            claim=(
                "passed = perf_agent 对不完整 / 不可比 / 别名输入拒绝放行的用例全部通过，且无 skip；"
                "不产生任何真实计时"
            ),
            unverified=(
                "不启动浏览器与后端：真实交互计时见 perf.agent.symbol_ui",
                "Windows 上创建符号链接受限的用例会 skip，此时整场景降级为 skipped",
            ),
            owner_doc="docs/perf-agent.md",
        ),
        Scenario(
            id="perf.macos_window.selftest",
            summary="perf_macos_window 的解析、归因边界与脱敏契约自检（离线；不取真窗口样本）",
            kind="selftest",
            steps=(_pytest("tests/test_perf_macos_window.py"),),
            timeout_s=300,
            preconditions=("python_module:pytest", "file:tests/test_perf_macos_window.py"),
            claim=(
                "passed = ps 解析、进程家族归类、compare 只出 diagnostic_only / incomparable 且"
                "绝不出 pass、输出脱敏这些离线契约的用例全部通过；不含任何真实窗口取样，"
                "更不是呈现延迟 / 拖影结论"
            ),
            unverified=(
                "record / compare 需要真实运行的 macOS Tavotto 窗口和人工拖动窗口，"
                "Agent 无法无人值守执行，本入口不登记：真窗口取证（idle + 活动阶段）整体未覆盖",
                "测的是进程家族 CPU/RSS 与系统级 WindowServer CPU，不是呈现延迟 / GPU / 拖影；"
                "真相需 Instruments 与视频对照",
                "compare 的 diagnostic_only 在真机数据上的取值未验",
            ),
            owner_doc="docs/perf-macos-window.md",
            issue="#1001",
            semantics="diagnostic_only",
        ),
        Scenario(
            id="regression.961.engine_files_incomplete",
            summary="#961：引擎自家模块缺失不被误判成用户缺包（模拟缺件的 engine 目录）",
            kind="regression",
            # workerd（Rust）那一条单独成场景：本机多半没有产物，混在这里会让整场景恒为 skipped
            steps=(_pytest("tests/test_engine_files_incomplete.py", "-k", "not workerd"),),
            timeout_s=600,
            preconditions=("python_module:pytest", "file:tests/test_engine_files_incomplete.py"),
            claim=(
                "passed = 在本机模拟「engine 目录缺 figcapture.py」时，worker / 父进程把它分诊成"
                "引擎文件不完整而不是缺依赖，且真缺第三方包的分诊不回归"
            ),
            unverified=(
                "Windows 真机：Codex（MSIX）交接后 sidecar 是否真的从 LocalCache 副本启动、副本为何缺件",
                "交接是否确认目标 Figure 渲染成功（launch / render / verified 三态，见 ADR 0131 后续）",
                "NSIS 安装 → 升级后的真实文件集",
            ),
            owner_doc="docs/adr/0131-agent-verification-entry.md",
            issue="#961",
        ),
        Scenario(
            id="regression.961.engine_files_incomplete.workerd",
            summary="#961：workerd（Rust supervisor）路径下 worker 启动期缺件同样分诊成引擎文件不完整",
            kind="regression",
            steps=(_pytest("tests/test_engine_files_incomplete.py", "-k", "workerd"),),
            timeout_s=600,
            preconditions=(
                "python_module:pytest",
                "workerd_binary",
                "file:tests/test_engine_files_incomplete.py",
            ),
            claim="passed = 经 tavotto-workerd 起的 worker 在装引擎模块时缺 figcapture.py，分诊为 engine_files_incomplete",
            unverified=("只验本机构建的 workerd；Windows 真机与打包产物里的 workerd 未验",),
            owner_doc="docs/adr/0131-agent-verification-entry.md",
            issue="#961",
        ),
    )
}


# --------------------------------------------------------------------------
# 前置条件探针
# --------------------------------------------------------------------------
def _fonts_dir() -> Path:
    # 与 rendercore.fonts.fonts_dir() 同一判据（环境变量排他覆盖 → 包内 resources/fonts）；
    # 不 import tavotto：探针要在产品依赖不全时也能跑。
    override = os.environ.get("TAVOTTO_FONTS_DIR")
    return Path(override) if override else ROOT / "src" / "tavotto" / "resources" / "fonts"


def _approved_fonts_problems(d: Path) -> list[str]:
    """批准字体集合齐不齐——直接用 `scripts/fetch_fonts.py` 的 `check()`（allowlist 每张脸与
    许可证全文逐个核 sha256），不另写一份判据：只看「有没有一个字体文件」会把中断下载留下的
    半套字体当成就绪，之后的字体失败就被误报成产品失败。"""
    spec = importlib.util.spec_from_file_location(
        "_tavotto_fetch_fonts", ROOT / "scripts" / "fetch_fonts.py"
    )
    if spec is None or spec.loader is None:
        return ["fetch_fonts_unloadable"]
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.check(d))


def _import_failure_category(stderr: str) -> str:
    """import 失败只留异常类名（`ModuleNotFoundError` …）进报告；stderr 末行可能带本机绝对路径。"""
    last = (stderr.strip().splitlines() or [""])[-1]
    head = last.split(":", 1)[0].strip()
    return f"import_failed:{head}" if head.isidentifier() else "import_failed"


def _playwright_browser_check(spec: str, browser: str) -> dict:
    """Playwright 浏览器二进制装没装：问 web/ 里装的 @playwright/test 自己，而不是猜缓存目录。"""
    if browser not in ("chromium", "webkit"):
        raise ValueError(f"unknown playwright browser {browser!r}")
    env = _base_env()
    node = shutil.which("node", path=env.get("PATH"))
    if node is None:
        return {"check": spec, "ok": False, "detail": "tool_missing", "raw": ""}
    script = (
        f"const p=require('@playwright/test').{browser}.executablePath();"
        "process.exit(p&&require('fs').existsSync(p)?0:1)"
    )
    try:
        proc = subprocess.run(
            [node, "-e", script],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "check": spec,
            "ok": False,
            "detail": f"node_unavailable:{type(exc).__name__}",
            "raw": str(exc),
        }
    ok = proc.returncode == 0
    detail = (
        "ok" if ok else f"browser_not_installed（cd web && pnpm exec playwright install {browser}）"
    )
    return {"check": spec, "ok": ok, "detail": detail, "raw": proc.stderr}


def check_precondition(spec: str, python: str) -> dict:
    """一条前置条件 → `{check, ok, detail, raw}`。判不出也算不满足。

    `detail` 进 report.json，只许是不含路径的类别；`raw` 是原始输出，由调用方写进本机证据后丢掉。"""
    kind, _, arg = spec.partition(":")
    if kind == "fonts":
        try:
            problems = _approved_fonts_problems(_fonts_dir())
        except Exception as exc:  # 判不出也算不满足
            problems = [f"check_failed:{type(exc).__name__}"]
        ok = not problems
        detail = (
            "ok"
            if ok
            else f"approved_fonts_incomplete:{len(problems)}（先跑 python scripts/fetch_fonts.py）"
        )
        return {"check": spec, "ok": ok, "detail": detail, "raw": "\n".join(problems)}
    if kind == "workerd_binary":
        # 与产品同一个判据（workerd_client.find_workerd），在目标解释器里问，不在这里另抄候选路径
        try:
            proc = subprocess.run(
                [
                    python,
                    "-c",
                    "import sys; from tavotto.engine.workerd_client import find_workerd; "
                    "sys.exit(0 if find_workerd() else 1)",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env=_base_env(),
            )
            ok = proc.returncode == 0
            detail, raw = (
                (
                    "ok"
                    if ok
                    else "workerd_binary_missing（cargo build --manifest-path workerd/Cargo.toml）"
                ),
                proc.stderr,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok, detail, raw = False, f"interpreter_unavailable:{type(exc).__name__}", str(exc)
        return {"check": spec, "ok": ok, "detail": detail, "raw": raw}
    if kind == "file":
        ok = (ROOT / arg).is_file()
        return {"check": spec, "ok": ok, "detail": "ok" if ok else "file_missing", "raw": ""}
    if kind == "any_file":
        # `a|b`：任一存在即可（前端构建产物有包内与 web/dist 两个出处）
        ok = any((ROOT / part).is_file() for part in arg.split("|"))
        return {"check": spec, "ok": ok, "detail": "ok" if ok else "file_missing", "raw": ""}
    if kind == "tool":
        # 与场景子进程同一个 PATH（白名单环境），而不是调用方 shell 的
        ok = shutil.which(arg, path=_base_env().get("PATH")) is not None
        return {"check": spec, "ok": ok, "detail": "ok" if ok else "tool_missing", "raw": ""}
    if kind == "playwright_browser":
        return _playwright_browser_check(spec, arg)
    if kind == "python_module":
        try:
            proc = subprocess.run(
                [python, "-c", f"import {arg}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env=_base_env(),
            )
            ok = proc.returncode == 0
            detail = "ok" if ok else _import_failure_category(proc.stderr)
            raw = proc.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok, detail, raw = False, f"interpreter_unavailable:{type(exc).__name__}", str(exc)
        return {"check": spec, "ok": ok, "detail": detail, "raw": raw}
    raise ValueError(f"unknown precondition {spec!r}")


# --------------------------------------------------------------------------
# 隔离执行
# --------------------------------------------------------------------------
#: 从调用方环境里**只**继承这些；其余（GITHUB_*、用户的 TAVOTTO_* 开关、代理凭据…）
#: 一律不带进场景——Agent 的 shell 里有什么不该决定验证结果。
_ENV_PASSTHROUGH = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "TAVOTTO_FONTS_DIR",
)


def _base_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    env["TAVOTTO_NO_TELEMETRY"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    # 绝对路径：worktree 里写相对的 `src` 会让应用子进程 import 到别处的旧代码。
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def scenario_env(sdir: Path, scenario: Scenario) -> dict[str, str]:
    env = _base_env()
    env["TAVOTTO_DATA_DIR"] = str(sdir / "work" / "data")
    env["MPLCONFIGDIR"] = str(sdir / "work" / "mpl")
    # benchmark.py / lab 工具的持久化根：绝不碰 /srv/tavotto-ci 里的真基线。
    env["TAVOTTO_CI_STATE_ROOT"] = str(sdir / "work" / "ci-state")
    env.update(dict(scenario.extra_env))
    return env


def expand(argv: tuple[str, ...], mapping: dict[str, str]) -> list[str]:
    out = []
    for a in argv:
        for key, val in mapping.items():
            a = a.replace("{" + key + "}", val)
        out.append(a)
    return out


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30
            )
        else:
            os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()


def run_step(argv: list[str], env: dict, log_prefix: Path, timeout: float) -> dict:
    """跑一步，stdout/stderr 原样落盘。超时连同子进程树一起杀掉。"""
    out_path = log_prefix.with_suffix(".stdout.log")
    err_path = log_prefix.with_suffix(".stderr.log")
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    t0 = time.monotonic()
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        try:
            proc = subprocess.Popen(
                argv, cwd=ROOT, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL, **kwargs
            )
        except OSError as exc:
            # 解释器路径与异常原文（POSIX 上也带路径）只落本地 stderr 证据，report.json 只记类别；
            # Windows 的 WinError 文本不含路径，所以显式写 argv[0]
            err.write(f"{argv[0]}: {type(exc).__name__}: {exc}\n".encode("utf-8", "replace"))
            code = errno.errorcode.get(exc.errno, "") if exc.errno else ""
            return {
                "exit_code": None,
                "timed_out": False,
                "duration_s": 0.0,
                "spawn_error": f"{type(exc).__name__}({code})" if code else type(exc).__name__,
            }
        try:
            rc = proc.wait(timeout=max(timeout, 1))
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.wait()
            rc, timed_out = None, True
    return {
        "exit_code": rc,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - t0, 3),
    }


# --------------------------------------------------------------------------
# 各验证器输出 → 结果枚举
# --------------------------------------------------------------------------
def parse_junit(path: Path) -> dict | None:
    """pytest junit → 计数。文件缺失 / 坏掉回 None（由调用方判成 error，而不是 passed）。"""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in counts:
            try:
                counts[k] += int(s.get(k, "0"))
            except ValueError:
                return None
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return counts


def judge_pytest(step: dict, junit: Path) -> tuple[str, str, dict | None]:
    counts = parse_junit(junit)
    rc = step["exit_code"]
    if rc == 5:  # pytest：没有收集到任何用例
        return "not_run", "pytest 没有收集到用例", counts
    if counts is None:
        return "error", f"pytest 退出码 {rc}，且没有可读的 junit 报告", None
    if counts["failures"] or counts["errors"]:
        return "failed", f"{counts['failures']} failed / {counts['errors']} errors", counts
    if rc != 0:
        return "error", f"pytest 退出码 {rc} 但 junit 没有失败记录", counts
    if counts["tests"] == 0:
        return "not_run", "0 条用例", counts
    if counts["skipped"]:
        # skip 判不出「验证了」：整场景降级为 skipped，而不是带着跳过的绿。
        return "skipped", f"{counts['skipped']} 条用例被跳过", counts
    return "passed", f"{counts['passed']} passed", counts


def judge_perf_manifest(step: dict, summary_path: Path) -> tuple[str, str, dict | None]:
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    rc = step["exit_code"]
    if data is None or data.get("schema") != "tavotto-perf-manifest/1":
        return "failed", f"perf_manifest_report 退出码 {rc}，没有可读的匿名摘要", None
    facts = {
        "status": data.get("status"),
        "comparison_verdict": data.get("comparison_verdict"),
        "scenarios": [
            {
                "scenario": s.get("scenario"),
                "status": s.get("status"),
                "valid_sample_count": s.get("valid_sample_count"),
                "manifest_ms": (s.get("phases") or {}).get("manifest_ms"),
            }
            for s in data.get("scenarios", [])
        ],
    }
    if rc == 0 and data.get("status") == "recorded":
        return "passed", "所有面板数据完整（recorded）；性能未与基线比较", facts
    return "failed", f"数据不完整（status={data.get('status')}，退出码 {rc}）", facts


#: perf_agent.ALLOWED_SCENARIOS 的镜像（入口不 import 被验脚本）；一致性由看护用例核对。
_PERF_AGENT_SCENARIOS = frozenset(
    {
        "dialog.presets_open",
        "symbol.alpha",
        "symbol.superscript_2",
        "symbol.micron",
        "preset.dimension",
    }
)
_PERF_AGENT_ENV_KEYS = (
    "platform",
    "arch",
    "browser",
    "browser_major",
    "browser_version",
    "viewport_w",
    "viewport_h",
    "dpr",
    "headless",
    "os_release",
    "cpu_model",
    "cpu_count",
    "total_mem_gib",
)


def _perf_agent_facts(data: dict) -> dict:
    scenarios = data.get("scenarios")
    rows = []
    if isinstance(scenarios, dict):
        for name in sorted(k for k in scenarios if isinstance(k, str)):
            row = scenarios[name] if isinstance(scenarios[name], dict) else {}
            rows.append(
                {
                    "id": name,
                    "count": row.get("count"),
                    "expected_count": row.get("expected_count"),
                    "p50_ms": row.get("p50_ms"),
                    "p95_ms": row.get("p95_ms"),
                    "max_ms": row.get("max_ms"),
                }
            )
    env = data.get("environment")
    return {
        "status": data.get("status"),
        "regression_status": data.get("regression_status"),
        "coverage": data.get("coverage"),
        "not_covered": data.get("not_covered"),
        "suites": data.get("suites"),
        "environment": (
            {k: env.get(k) for k in _PERF_AGENT_ENV_KEYS} if isinstance(env, dict) else None
        ),
        "scenarios": rows,
    }


def _perf_agent_complete(facts: dict) -> bool:
    """`ok` 的自洽核对：入口不盲信一个字符串，样本数必须和摘要自己声称的期望一致。"""
    suites = facts["suites"] if isinstance(facts["suites"], dict) else {}
    rows = facts["scenarios"]
    return (
        suites.get("symbol_ui") == "ok"
        and suites.get("canvas_drag") == "not_requested"
        and {r["id"] for r in rows} == _PERF_AGENT_SCENARIOS
        and all(
            type(r["count"]) is int
            and r["count"] > 0
            and r["count"] == r["expected_count"]
            and isinstance(r["p95_ms"], (int, float))
            for r in rows
        )
        and isinstance(facts["regression_status"], str)
        and facts["regression_status"].startswith("not_checked")
    )


def judge_perf_agent(step: dict, summary_path: Path) -> tuple[str, str, dict | None]:
    """perf_agent 的结论以 summary.json 的 `status` 为准，退出码只作一致性旁证。

    ok→passed（且自洽）；incomplete / failed / regression→failed；
    incomparable / no_baseline→not_run（没有得到裁决，不是失败也不是通过）；
    pending、未知状态、摘要缺失 / 损坏、ok 与退出码或内容矛盾→error。"""
    rc = step["exit_code"]
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("schema") != "tavotto-perf-agent/1":
        return "error", f"perf_agent 退出码 {rc}，没有可读的 tavotto-perf-agent/1 摘要", None
    facts = _perf_agent_facts(data)
    status = data.get("status")
    if status == "ok":
        if rc != 0:
            return "error", f"摘要 status=ok 但退出码 {rc}，自相矛盾", facts
        if not _perf_agent_complete(facts):
            return "error", "摘要 status=ok 但套件 / 场景 / 样本数不自洽，不按 ok 采信", facts
        return "passed", "符号交互样本完整（ok）；性能未与基线比较、拖动未测", facts
    if status in ("incomplete", "failed", "regression"):
        return "failed", f"perf_agent status={status}（退出码 {rc}）", facts
    if status in ("incomparable", "no_baseline"):
        return "not_run", f"perf_agent status={status}：没有得到性能裁决", facts
    return "error", f"perf_agent 摘要状态 {status!r} 无法判定（退出码 {rc}）", facts


# --------------------------------------------------------------------------
# 执行一个场景
# --------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _evidence_list(sdir: Path, out: Path) -> list[dict]:
    files = []
    for p in sorted(sdir.rglob("*")):
        if p.is_file() and "work" not in p.relative_to(sdir).parts:
            files.append(
                {
                    "path": p.relative_to(out).as_posix(),
                    "bytes": p.stat().st_size,
                    "sha256": _sha256(p),
                }
            )
    return files


def run_scenario(scenario: Scenario, out: Path, python: str) -> dict:
    sdir = out / scenario.id
    evidence = sdir / "evidence"
    private = sdir / "work" / "private"
    for d in (evidence, private, sdir / "work" / "data", sdir / "work" / "mpl"):
        d.mkdir(parents=True, exist_ok=True)
    rec: dict = {
        "id": scenario.id,
        "kind": scenario.kind,
        "summary": scenario.summary,
        "issue": scenario.issue or None,
        "claim": scenario.claim,
        "semantics": scenario.semantics or None,
        "owner_doc": scenario.owner_doc,
        "result": "not_run",
        "reason": "",
        "timeout_s": scenario.timeout_s,
        "timed_out": False,
        "duration_s": 0.0,
        "preconditions": [],
        "steps": [],
        "facts": None,
        "baseline": None,
        "unverified": list(scenario.unverified),
        "evidence": [],
    }
    if sys.platform not in scenario.platforms:
        rec["result"] = "environment_unavailable"
        rec["reason"] = f"本场景不支持平台 {sys.platform}"
        return rec

    checks = [check_precondition(c, python) for c in scenario.preconditions]
    raw_lines = [f"## {c['check']}\n{c['raw']}" for c in checks if c.get("raw")]
    if raw_lines:
        # 原始输出（可能含本机路径）只留在本机证据里；report.json 里只有类别
        (evidence / "preconditions.log").write_text("\n\n".join(raw_lines) + "\n", encoding="utf-8")
    for c in checks:
        c.pop("raw", None)
    rec["preconditions"] = checks
    missing = [c for c in checks if not c["ok"]]
    if missing:
        # 缺测试文件是「这个场景本身还没就绪」，其余是本机环境不满足。
        only_files = all(c["check"].startswith("file:tests/") for c in missing)
        rec["result"] = "not_run" if only_files else "environment_unavailable"
        rec["reason"] = "; ".join(f"{c['check']}: {c['detail']}" for c in missing)
        shutil.rmtree(sdir / "work", ignore_errors=True)
        rec["evidence"] = _evidence_list(sdir, out)
        return rec

    env = scenario_env(sdir, scenario)
    mapping = {
        "python": python,
        "evidence": str(evidence),
        "private": str(private),
        "work": str(sdir / "work"),
    }
    deadline = time.monotonic() + scenario.timeout_s
    t0 = time.monotonic()
    result, reason, facts = "passed", "", None
    for i, step in enumerate(scenario.steps, 1):
        remaining = deadline - time.monotonic()
        argv = expand(step.argv, mapping)
        srec = run_step(argv, env, evidence / f"{i:02d}-{step.name}", remaining)
        srec = {"name": step.name, "argv": list(step.argv), **srec}
        rec["steps"].append(srec)
        if srec.get("spawn_error"):
            result, reason = "error", f"{step.name} 无法启动：{srec['spawn_error']}"
            break
        if srec["timed_out"]:
            rec["timed_out"] = True
            result, reason = "error", f"{step.name} 超时（场景预算 {scenario.timeout_s}s）"
            break
        if step.parser == "pytest":
            result, reason, facts = judge_pytest(srec, evidence / "junit.xml")
        elif step.parser == "perf_manifest":
            result, reason, facts = judge_perf_manifest(srec, evidence / "manifest-summary.json")
            if facts:
                rec["baseline"] = {
                    "status": "not_checked",
                    "verdict": facts.get("comparison_verdict"),
                }
        elif step.parser == "perf_agent":
            result, reason, facts = judge_perf_agent(srec, evidence / "summary.json")
            if facts:
                rec["baseline"] = {
                    "status": "not_checked",
                    "verdict": facts.get("regression_status"),
                }
        elif srec["exit_code"] != 0:
            result, reason = "failed", f"{step.name} 退出码 {srec['exit_code']}"
        if result != "passed":
            break
    rec["duration_s"] = round(time.monotonic() - t0, 3)
    rec["result"], rec["reason"], rec["facts"] = result, reason, facts
    # 私有目录（原始 bench JSON 含项目路径与文件名）不进证据、跑完即删。
    shutil.rmtree(sdir / "work", ignore_errors=True)
    rec["evidence"] = _evidence_list(sdir, out)
    return rec


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def commit_facts() -> dict:
    # 未跟踪文件也算：没 add 的新用例 / 新模块同样会被 pytest 收集、被 import，
    # 报告验的就不是 sha 那棵树（.gitignore 掉的构建产物不在 porcelain 里）
    lines = _git("status", "--porcelain", "--untracked-files=normal").splitlines()
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    return {
        "sha": _git("rev-parse", "HEAD") or None,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or None,
        # 有未提交改动时，报告验的不是 sha 那棵树——审查者必须知道。
        "dirty": bool(lines),
        "dirty_files": len(lines) - untracked,
        "untracked_files": untracked,
    }


def _same_dir(a: str, b: Path) -> bool:
    if not a:
        return False
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def environment_facts(python: str) -> dict:
    meta = run_metadata()
    keep = ("os", "kernel", "cpu_model", "cpu_count", "ram_gib", "node", "pnpm", "rust")
    env = {k: (meta.get(k) or None) for k in keep}
    try:
        proc = subprocess.run(
            [
                python,
                "-c",
                "import platform,sys;print(platform.python_version());print(sys.prefix)",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        version, prefix = (proc.stdout.splitlines() + ["", ""])[:2]
    except (OSError, subprocess.TimeoutExpired):
        version, prefix = "", ""
    env["target_python"] = version or None
    # 只认**这个仓库根**下的 .venv：别处同名的 .venv（主工作区、别的项目）装的依赖不是这棵树声明的
    env["target_python_in_repo_venv"] = _same_dir(prefix, ROOT / ".venv")
    env["fonts_present"] = check_precondition("fonts", python)["ok"]
    env["platform"] = sys.platform
    return env


def build_report(records: list[dict], python: str, out: Path, commit: dict | None = None) -> dict:
    overall = aggregate([r["result"] for r in records])
    return {
        "schema": SCHEMA,
        "kind": REPORT_KIND,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": commit if commit is not None else commit_facts(),
        "environment": environment_facts(python),
        "invocation": {"scenarios": [r["id"] for r in records]},
        "overall": overall,
        "exit_code": exit_code_for(overall),
        "counts": {k: sum(r["result"] == k for r in records) for k in SEVERITY},
        "scenarios": records,
        "unverified": sorted({u for r in records for u in r["unverified"]}),
        "privacy": (
            "report.json 不含本机绝对路径；evidence/ 下的原始日志可能含本机路径，"
            "仅供本机审查，不上传、不贴进公开 PR"
        ),
    }


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _list(as_json: bool) -> int:
    rows = [
        {
            "id": s.id,
            "kind": s.kind,
            "summary": s.summary,
            "timeout_s": s.timeout_s,
            "preconditions": list(s.preconditions),
            "claim": s.claim,
            "semantics": s.semantics or None,
            "owner_doc": s.owner_doc,
        }
        for s in SCENARIOS.values()
    ]
    if as_json:
        print(json.dumps({"schema": SCHEMA, "scenarios": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['id']:<42} [{r['kind']}] {r['summary']}")
    return EXIT_PASSED


def _usage(msg: str) -> int:
    print(json.dumps({"ok": False, "code": "usage", "error": msg}, ensure_ascii=False))
    return EXIT_USAGE


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按白名单场景 ID 调用现有验证器，输出结构化证据")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="列出登记的场景")
    p_list.add_argument("--json", action="store_true")
    p_run = sub.add_parser("run", help="运行一个或多个场景")
    p_run.add_argument("scenario", nargs="+")
    p_run.add_argument("--out", type=Path, help="证据目录（必须不存在或为空）；默认新建临时目录")
    p_run.add_argument("--python", default=sys.executable, help="被验的 Python 解释器")
    p_run.add_argument("--json", action="store_true", help="把完整报告打到 stdout")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        return _list(args.json)

    unknown = [s for s in args.scenario if s not in SCENARIOS]
    if unknown:
        return _usage(f"未登记的场景 ID：{unknown}；用 list 查看")
    if len(set(args.scenario)) != len(args.scenario):
        return _usage("场景 ID 重复")
    if args.out is None:
        out = Path(tempfile.mkdtemp(prefix="tavotto-agent-verify-"))
    else:
        out = args.out.resolve()
        # 混进上一轮的证据，审查者就分不清哪份文件属于这次。
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            return _usage(f"--out 必须不存在或为空：{out}")
        out.mkdir(parents=True, exist_ok=True)

    # 先拍 git 状态再跑：--out 若在仓库里，场景自己写出的证据不能反过来把树判成 dirty
    commit = commit_facts()
    records = [run_scenario(SCENARIOS[s], out, args.python) for s in args.scenario]
    report = build_report(records, args.python, out, commit)
    _write_json(out / "report.json", report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in records:
            print(f"{r['result']:<24} {r['id']}  {r['reason']}")
        print(f"overall: {report['overall']}  report: {out / 'report.json'}")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
