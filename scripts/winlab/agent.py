"""Windows Lab 执行器（Windows 端；规则全文 docs/rules/ci/windows-lab.md）。

部署在 `C:\\TavottoLab\\tools\\winlab\\`（测试账户只读），由 sshd 的
`ForceCommand` 拉起：`tavotto-lab` 这把钥匙**只能**调用这里登记过的动词与任务，
拿不到任意 shell。命令行来自 `SSH_ORIGINAL_COMMAND`，本地调试也可以直接给 argv。

    doctor | status | prepare --sha SHA（bundle 经 stdin）| fonts（zip 经 stdin）| test TASK [选项]
    reports [--id RUN] | clean [--keep N] | stop

结果闭集、聚合与 junit 判定复用 `scripts/ci/agent_verify.py`（同目录部署的那一份），
任何非 passed 都不会被聚合成 passed。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_verify as av  # noqa: E402 — 与本文件一同部署；结果闭集的唯一出处

# Windows 上流被重定向（SSH 管道）时退回 ANSI 代码页：中文报文会丢或炸父进程读线程（test_windows_regressions）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = 1
REPORT_KIND = "tavotto-winlab-run"
DEFAULT_ROOT = Path(r"C:\TavottoLab")
GIT = r"C:\Program Files\Git\cmd\git.exe"
BASE_PYTHON = r"C:\Program Files\Python313\python.exe"
NODE_DIR = r"C:\Program Files\nodejs"
PIP_INDEX = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
NPM_REGISTRY = "https://registry.npmmirror.com"

#: run id：时间 + 随机后缀。清理只认这个形状的目录名（见 `safe_remove_child`）。
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: `--tests` 只收仓库内的测试文件 / 节点 id，不收任何 pytest 选项（不以 `-` 开头、无空白）。
TEST_SELECTOR_RE = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py(::[A-Za-z0-9_\[\]./-]+)*$")
SHARD_RE = re.compile(r"^([1-9][0-9]?)/([1-9][0-9]?)$")
MAX_SELECTORS = 40
#: hosts-live 的 --mode：真宿主的闭集（实现见 LIVE_HOSTS）
LIVE_HOST_IDS = ("claude-code", "codex-cli", "codebuddy", "minimax-code", "dsh")
#: `desktop-hosts --mode`：桌面宿主 L2（固定版本见 DESKTOP_HOSTS）
DESKTOP_HOST_IDS = ("vscode", "cursor", "trae-cn", "workbuddy", "zcode", "claude-desktop")
#: `desktop-live --mode`：桌面宿主 + 其中的 AI 扩展，到 L3–L5（宿主 × 扩展的闭集见 DESKTOP_LIVE）
DESKTOP_LIVE_MODES = ("vscode-claude",)
#: 依赖安装配方的版本：改了 prepare 的安装步骤就加一，已有 venv 随之重装（散列戳里含它）。
DEPS_RECIPE = "2"

# ----------------------------------------------------------------------------- 网络模式
#: 本机 sing-box 的回环入口（bootstrap / proxy-install 写死同一组端口）。没有 direct 出站：
#: 代理断开时请求失败，绝不悄悄直连（docs/rules/ci/windows-lab.md「代理纪律」）。
PROXY_PORTS = {"trojan": 7890, "hysteria2": 7891}
NETWORK_MODES = ("direct", "trojan", "hysteria2")
#: 本地 / tailnet 流量永远不进代理：MCP stdio 之外的本机 HTTP、Tailscale 100.64/10 与 MagicDNS。
NO_PROXY = "localhost,127.0.0.1,::1,[::1],100.64.0.0/10,.ts.net,10.0.0.0/8,192.168.0.0/16"
#: 只用于 L1 网络探测的目标：都是小请求（HEAD / 小 GET），不下载任何大文件。
PROBE_TARGETS = (
    ("anthropic_api", "https://api.anthropic.com/v1/models"),
    ("openai_api", "https://api.openai.com/v1/models"),
    ("chatgpt_web", "https://chatgpt.com/"),
    ("github", "https://github.com/"),
    ("pypi_mirror", "https://mirrors.tuna.tsinghua.edu.cn/"),
)
EGRESS_URL = "https://api.ipify.org"
STREAM_URL = "https://httpbin.org/drip?duration=4&numbytes=8&delay=0"
PROBE_SAMPLES = 3
#: 每个网络模式「必须可达」的目标集合（至少 1/3 样本通）。表里没有的目标只记录、不判：
#: 直连下境外目标预期可能不可达或 403（区域限制、GitHub 直连不稳），代理模式下国内镜像不是代理的职责。
PROBE_MUST_REACH = {
    "trojan": frozenset({"anthropic_api", "openai_api", "github"}),
    "hysteria2": frozenset({"anthropic_api", "openai_api", "github"}),
    "direct": frozenset({"pypi_mirror"}),
}
#: 流式（分块到达）探测是否算判据：代理模式必须通；直连时 httpbin 不稳，只记录。
PROBE_STREAM_REQUIRED = {"trojan": True, "hysteria2": True, "direct": False}


def iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    )


def network_env(mode: str, base: dict[str, str]) -> dict[str, str]:
    """按模式给**单个进程**的环境；从不碰系统 / WinHTTP 代理。"""
    if mode not in NETWORK_MODES:
        raise ValueError(f"unknown network mode {mode!r}")
    env = {k: v for k, v in base.items() if k.upper() not in _PROXY_KEYS}
    if mode != "direct":
        url = f"http://127.0.0.1:{PROXY_PORTS[mode]}"
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
            env[k] = url
        env["NO_PROXY"] = env["no_proxy"] = NO_PROXY
    return env


_PROXY_KEYS = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"})


# ----------------------------------------------------------------------------- 任务登记表
@dataclass(frozen=True)
class Task:
    id: str
    kind: str  # network | pytest | hosts
    summary: str
    timeout_s: int
    claim: str
    unverified: tuple[str, ...]
    #: 允许的网络模式；第一个是默认。pytest 类任务只许 direct（依赖、构建不进 DMIT）。
    networks: tuple[str, ...] = ("direct",)
    #: pytest：固定的用例清单；空 = 必须给 `--tests` 或 `--shard`。
    tests: tuple[str, ...] = ()
    accepts_tests: bool = False
    accepts_shard: bool = False
    needs_venv: bool = True
    #: 需要一次性凭据：Mac 经 stdin 传入、只进被测宿主进程的环境，Lab 上不落盘
    needs_model_conf: bool = False
    #: 安装包：`--package` / `--from` 只收 cache\packages\ 里带 .sha256 的文件名
    accepts_package: bool = False
    #: corpus：`--mode` 只收登记值
    modes: tuple[str, ...] = ()


#: Windows 回归 + 编码 / 路径 / 导出 / 保存重载这一组都是仓库里已有的用例，Lab 只负责在真
#: Windows（zh-CN、GB2312 代码页、Defender 开、LongPathsEnabled=0）上跑它们。
_RENDER_ROUNDTRIP = (
    "tests/test_windows_regressions.py",
    "tests/test_tracked_paths_are_windows_safe.py",
    "tests/test_export_pipeline.py",
    "tests/test_export_svg.py",
    "tests/test_compat_matrix_svg_export.py",
    "tests/test_savefig_frame.py",
    "tests/test_project_layout_save.py",
    "tests/test_modified_after_save.py",
    "tests/test_mcp_roundtrip.py",
)

TASKS: dict[str, Task] = {
    t.id: t
    for t in (
        Task(
            id="network",
            kind="network",
            summary="L1 网络探测：本地代理入口、出口身份、TLS 校验、首响应延迟、流式、断开后不回落直连",
            timeout_s=300,
            networks=("all", "direct", "trojan", "hysteria2"),
            needs_venv=False,
            claim="对每个模式：小请求在完整 TLS 校验下的可达性与延迟；代理模式的出口确为 DMIT（只比对散列）",
            unverified=(
                "不代表任何 AI 服务的账户授权或真实模型调用（L4/L5）",
                "样本数小（每目标 3 次），不是稳定性结论",
            ),
        ),
        Task(
            id="pytest",
            kind="pytest",
            summary="在 Windows Lab 上跑指定的仓库用例（--tests 必填）",
            timeout_s=3600,
            accepts_tests=True,
            claim="所列用例在真 Windows Server 2025（zh-CN / GB2312 / Defender 开）上的 junit 结论",
            unverified=(
                "真窗口 / WebView2 交互不在此任务内",
                "不覆盖 Windows 10 / 11 客户端版本差异",
            ),
        ),
        Task(
            id="pytest-shard",
            kind="pytest",
            summary="与 CI backend-platforms 同口径的全量后端套件分片（--shard N/M 必填；Lab 比 hosted 慢约一倍，建议 M=8）",
            timeout_s=7200,
            accepts_shard=True,
            claim="该分片的全部用例在 Lab 上的 junit 结论；skip 一律让结论降为 skipped",
            unverified=("与 hosted windows-latest 的镜像差异（预装软件、代码页）不由此消除",),
        ),
        Task(
            id="render-roundtrip",
            kind="pytest",
            summary="真实绘图渲染 + 导出 PDF/SVG/PNG + 保存重载 + MCP 往返（固定用例清单）",
            timeout_s=2400,
            tests=_RENDER_ROUNDTRIP,
            claim="清单内用例在 Lab 上全部通过（含真 matplotlib 渲染、导出与保存重载）",
            unverified=("不经过安装包 / 冻结 sidecar", "不经过真窗口"),
        ),
        Task(
            id="hosts",
            kind="hosts",
            summary="AI 宿主 L2/L3：装官方 CLI、取版本、隔离配置里登记 Tavotto MCP 并让宿主自己连上它",
            timeout_s=1800,
            needs_venv=True,
            claim=(
                "L2 = 官方 CLI 可安装可启动；L3 = 宿主 CLI 自己的健康检查（`claude mcp list`）拉起 Tavotto MCP"
                " 并完成握手（真宿主，不是模拟 client）"
            ),
            unverified=(
                "L3 的健康检查只证明握手成功，不证明宿主列出了全部工具（那要 L4 会话里看）",
                "MCP 由 venv 里的仓库 server.py 直接拉起，未经过插件的 mcp/launch 与插件安装路径",
                "L4/L5 需要登录与真实模型调用：未获授权时一律 blocked，不推定",
                "只覆盖 CLI，不覆盖桌面版 / IDE 宿主",
            ),
        ),
        Task(
            id="installer",
            kind="installer",
            summary="安装包验收：静默安装（可先装 --from 旧包再升级）、布局、壳→sidecar、内置 runtime 冒烟、覆盖安装、卸载",
            timeout_s=3600,
            accepts_package=True,
            claim=(
                "与 nightly windows-install 同一组检查，在 Lab（非管理员 currentUser 安装、zh-CN、Defender 开）上对"
                "这个具体安装包成立；给了 --from 时另证升级保留用户数据、版本号变化"
            ),
            unverified=(
                "安装界面那几屏（非 /S）与 SmartScreen 体验不在内",
                "SSH 会话在 session 0：壳能拉起 sidecar 不代表真窗口能显示",
                "Server 2025 不是 Windows 10 / 11 客户端",
            ),
        ),
        Task(
            id="desktop-window",
            kind="desktop-window",
            summary="真窗口：装包后在已登录的交互会话里跑 tests/desktop_windows（WebView2 + msedgedriver 附着），与 nightly 同一套用例",
            timeout_s=2400,
            accepts_package=True,
            claim=(
                "tests/desktop_windows 的真窗口用例在 Lab 的交互桌面（非管理员、zh-CN、Defender 开）上对这个安装包的 junit 结论；"
                "任何 skip 都让结论降为 skipped"
            ),
            unverified=(
                "交互会话是自动登录的控制台会话，不是 RDP、不是多显示器 / 高 DPI",
                "键鼠经 CDP 与窗口消息投递，不是真实硬件输入（IME 不覆盖）",
                "Server 2025 不是 Windows 10 / 11 客户端",
            ),
        ),
        Task(
            id="desktop-hosts",
            kind="desktop-hosts",
            summary="桌面宿主 L2：下载固定版本安装包（核 SHA-256 + Authenticode）、静默装、在交互会话里起出主窗口、卸载",
            timeout_s=2400,
            modes=DESKTOP_HOST_IDS,
            claim=(
                "L2 = 这个固定版本的官方安装包在 Lab（非管理员 currentUser、zh-CN、Defender 开）上静默安装成功，"
                "且在 tavotto-lab 的交互会话里起出了属于安装目录进程的、可见且稳定的主窗口"
            ),
            unverified=(
                "L3+ 要在宿主里登录各自账户、配置 MCP：Lab 没有这些账户，一律 blocked，不推定",
                "主窗口出现 ≠ 能用：首次启动的登录 / 欢迎页之后的界面不在内",
                "海外 CDN 的安装包经 DMIT 下载（用户授权），国内 CDN 直连；下载线路不代表宿主运行时的网络",
                "Server 2025 不是 Windows 10 / 11 客户端",
            ),
        ),
        Task(
            id="desktop-live",
            kind="desktop-live",
            summary="桌面宿主 L3–L5：VS Code 里的 Claude Code 扩展（国内 Anthropic 兼容模型）经 CDP 驱动，调用 Tavotto MCP，改图、导出、重开核对",
            timeout_s=4800,
            needs_model_conf=True,
            modes=DESKTOP_LIVE_MODES,
            claim=(
                "判据与 hosts-live 同一个 judge_live：L3 = 录制器见到 tools/list，且那条 MCP 流量的父进程链里有桌面宿主安装目录下的程序"
                "（不是有人另起了 CLI）；L4 = 有成功的 tools/call；L5 = 改图、导出 PDF、重放一致、全新的宿主进程凭 session_id 恢复同一组修改"
            ),
            unverified=(
                "模型不是 Claude：宿主是真 VS Code + 真 Claude Code 扩展，推理由国内 Anthropic 兼容接口提供（报告写明供应商与模型）",
                "提示词经 CDP 注入输入框并点发送，不是真实键鼠输入（IME 不覆盖）",
                "扩展包经 DMIT 从 Open VSX 下载（用户授权的范围，固定版本与散列），宿主运行时的网络是直连国内模型",
                "只覆盖列出的宿主 × 扩展组合；其它桌面宿主 / 扩展仍是 L2 或 BLOCKED",
                "Server 2025 不是 Windows 10 / 11 客户端",
            ),
        ),
        Task(
            id="env-matrix",
            kind="env-matrix",
            summary="环境矩阵：装包后对每个解释器环境跑 nightly 同款冒烟（内置 / 官方 CPython / uv 3.10·3.12·3.14 / conda / 中文空格路径 venv）",
            timeout_s=5400,
            accepts_package=True,
            claim=(
                "每条腿：装好的 sidecar 在中文 + 空格的项目路径下渲染导出成功，且渲染解释器来源与期望一致"
                "（不指定 → bundled 并带全科学栈；TAVOTTO_WORKER_PYTHON 指定 → env_override 压过内置）"
            ),
            unverified=(
                "uv 装的是 python-build-standalone，不是 python.org 安装器（只有 3.13 是官方安装器）",
                "不覆盖 Microsoft Store Python、pyenv-win、企业代理 / 证书拦截环境",
                "项目自带 .venv 的自动采用（project_venv）不在本任务",
            ),
        ),
        Task(
            id="corpus",
            kind="corpus",
            summary="脚本语料：CompatBench（scripts/ci/compat_matrix.py）在 Lab 上跑——smoke 与 CI compat-smoke 同口径，all 跑全部 case",
            timeout_s=5400,
            modes=("smoke", "all"),
            claim="经评审的 CompatBench 语料在真 Windows（cp936、非管理员、Defender 开）上的结论；不比像素保真度",
            unverified=(
                "只跑仓库里经评审的语料，不跑用户提交的脚本（那要一次性隔离环境）",
                "--no-fidelity：不比像素基线",
            ),
        ),
        Task(
            id="hosts-live",
            kind="hosts-live",
            summary="AI 宿主 L4/L5：真 Claude Code（国内 Anthropic 兼容模型）调用 Tavotto MCP，改图、导出、重开核对",
            timeout_s=2400,
            needs_model_conf=True,
            modes=tuple(LIVE_HOST_IDS),
            claim=(
                "L4 = 真 Claude Code CLI 在 Lab 上执行了 Tavotto MCP 工具调用并拿到成功结果；"
                "L5 = 同一张真实 matplotlib 图经宿主打开、改线宽与图例、导出 PDF、重放校验，"
                "再由一个全新的宿主进程重新打开并读到同一组修改；PDF 与每一步结果由执行器独立核对"
            ),
            unverified=(
                "模型不是 Claude：宿主是真 Claude Code，推理由国内 Anthropic 兼容接口提供（报告写明供应商与模型）",
                "Lab 在中国大陆：Anthropic / OpenAI 的真实模型调用按服务商地区政策不在这里做",
                "只覆盖 CLI 宿主，不覆盖桌面版 / IDE / 插件安装路径",
                "模型行为有随机性：一次通过不代表每次都会这样调用工具",
            ),
        ),
    )
}


PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.exe$")


def package_path(lab: Lab, name: str) -> tuple[Path | None, str | None]:
    """安装包只认 cache\\packages\\ 下名字合规、带 .sha256 且散列吻合的文件（Mac 端核过、经管理员送进来的）。"""
    if not PACKAGE_NAME_RE.fullmatch(name):
        return None, f"bad package name {name!r}"
    p = lab.cache / "packages" / name
    want = lab.cache / "packages" / f"{name}.sha256"
    if not p.is_file() or not want.is_file():
        return None, f"package {name} not staged"
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != want.read_text(encoding="ascii").strip().lower():
        return None, f"package {name} sha256 mismatch"
    return p, None


def validate_test_args(
    task: Task,
    tests: list[str],
    shard: str | None,
    network: str,
    package: str | None = None,
    frm: str | None = None,
    mode: str | None = None,
) -> str | None:
    """返回错误文本；None = 合法。所有参数都可审计：没有透传通道。"""
    if mode and mode not in task.modes:
        return f"task {task.id} does not accept --mode {mode} (allowed: {', '.join(task.modes) or 'none'})"
    if (package or frm) and not task.accepts_package:
        return f"task {task.id} does not accept --package / --from"
    if task.accepts_package and not package:
        return f"task {task.id} requires --package"
    for name in (package, frm):
        if name and not PACKAGE_NAME_RE.fullmatch(name):
            return f"bad package name {name!r}"
    allowed = TASKS["network"].networks if task.kind == "network" else task.networks
    if network not in allowed:
        return f"task {task.id} does not accept network={network} (allowed: {', '.join(allowed)})"
    if tests and not task.accepts_tests:
        return f"task {task.id} does not accept --tests"
    if shard and not task.accepts_shard:
        return f"task {task.id} does not accept --shard"
    if task.accepts_tests and not tests:
        return f"task {task.id} requires --tests"
    if task.accepts_shard and not shard:
        return f"task {task.id} requires --shard N/M"
    if len(tests) > MAX_SELECTORS:
        return f"at most {MAX_SELECTORS} --tests selectors"
    for sel in tests:
        if not TEST_SELECTOR_RE.fullmatch(sel) or ".." in sel.split("::")[0].split("/"):
            return f"bad test selector {sel!r}"
    if shard:
        m = SHARD_RE.fullmatch(shard)
        if not m or not (1 <= int(m.group(1)) <= int(m.group(2))):
            return f"bad shard {shard!r}"
    return None


# ----------------------------------------------------------------------------- 路径安全
def _is_reparse(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return stat.S_ISLNK(st.st_mode) or bool(
        attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def safe_remove_child(parent: Path, name: str) -> bool:
    """删 `parent` 下名为 `name` 的 run 目录。不靠字符串前缀判断：

    - 名字必须是 run id 形状（无分隔符、无 `..`、无盘符）；
    - `parent` 自身与目标都不许是符号链接 / junction；规范化后目标的父目录必须**就是** `parent`；
    - 递归删除时遇到链接 / junction 只删链接本身，绝不跟进去（防止跳出工作区）。
    """
    if not RUN_ID_RE.fullmatch(name):
        return False
    if _is_reparse(parent):
        return False
    target = parent / name
    if not target.exists() and not _is_reparse(target):
        return False
    if _is_reparse(target):
        _unlink_link(target)
        return True
    if os.path.normcase(os.path.realpath(target.parent)) != os.path.normcase(
        os.path.realpath(parent)
    ):
        return False
    _rmtree_no_follow(target)
    return True


def _unlink_link(p: Path) -> None:
    try:
        os.unlink(p)
    except (IsADirectoryError, PermissionError, OSError):
        os.rmdir(p)  # Windows 上目录 junction / 目录符号链接要用 rmdir 删链接本身


def _rmtree_no_follow(top: Path) -> None:
    for entry in os.scandir(top):
        p = Path(entry.path)
        if _is_reparse(p):
            _unlink_link(p)
        elif entry.is_dir(follow_symlinks=False):
            _rmtree_no_follow(p)
        else:
            with contextlib.suppress(OSError):
                os.chmod(p, stat.S_IWRITE)
            os.unlink(p)
    os.rmdir(top)


# ----------------------------------------------------------------------------- 进程树与资源
_JOB_MEMORY_LIMIT = 6 * 1024**3  # 8 GB 机器：给系统和 sshd 留 2 GB


class ProcessTree:
    """一次任务的全部子孙进程。Windows 用 Job Object（KILL_ON_JOB_CLOSE + 内存上限），
    子进程在挂进 Job 之前保持挂起，孙进程逃不出去；其它平台用进程组（单测用）。"""

    def __init__(self, argv: list[str], *, cwd: Path, env: dict, stdout, stderr, stdin=None):
        self._job = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._k32 = k32
            job = k32.CreateJobObjectW(None, None)
            if not job:
                raise OSError(ctypes.get_last_error(), "CreateJobObject")

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    (n, ctypes.c_ulonglong)
                    for n in (
                        "ReadOperationCount",
                        "WriteOperationCount",
                        "OtherOperationCount",
                        "ReadTransferCount",
                        "WriteTransferCount",
                        "OtherTransferCount",
                    )
                ]

            class BASIC(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class EXTENDED(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000 | 0x200  # KILL_ON_JOB_CLOSE | JOB_MEMORY
            info.JobMemoryLimit = _JOB_MEMORY_LIMIT
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
            self._job = job
            flags = 0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP  # CREATE_SUSPENDED
            self.proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
                creationflags=flags,
            )
            if not k32.AssignProcessToJobObject(job, int(self.proc._handle)):  # noqa: SLF001
                err = ctypes.get_last_error()
                self.proc.kill()
                raise OSError(err, "AssignProcessToJobObject")
            ntdll = ctypes.WinDLL("ntdll")
            ntdll.NtResumeProcess(int(self.proc._handle))  # noqa: SLF001
        else:
            self.proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
                start_new_session=True,
            )

    def wait(self, timeout: float) -> tuple[int | None, bool]:
        try:
            return self.proc.wait(timeout=max(timeout, 1)), False
        except subprocess.TimeoutExpired:
            self.kill_all()
            self.proc.wait()
            return None, True

    def kill_all(self) -> None:
        if self._job is not None:
            self._k32.TerminateJobObject(self._job, 1)
        else:
            import signal

            with contextlib.suppress(OSError):
                os.killpg(self.proc.pid, signal.SIGKILL)

    def close(self) -> None:
        """任务结束：哪怕主进程已正常退出，留下的孙进程也随 Job 关闭一起结束。"""
        if self._job is not None:
            self._k32.TerminateJobObject(self._job, 0)
            self._k32.CloseHandle(self._job)
            self._job = None


# ----------------------------------------------------------------------------- 工作区
@dataclass
class Lab:
    root: Path = DEFAULT_ROOT

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def lock_path(self) -> Path:
        return self.state / "job.lock"

    def venv_python(self) -> Path:
        return self.cache / "venvs" / "py313" / "Scripts" / "python.exe"

    @property
    def fonts(self) -> Path:
        return self.cache / "fonts"


def _pid_alive(pid: int) -> bool:
    """Windows 用 OpenProcess + GetExitCodeProcess（STILL_ACTIVE=259），不解析 tasklist 的本地化输出：
    10-24 在 Lab 上实测，基于 tasklist / taskkill 的判定把一个还在跑的任务判成已退出，锁被删掉、第二个任务并行起跑。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_nt(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_alive_nt(pid: int) -> bool:
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        # 拒绝访问 = 进程在但不归我们：当作活着，绝不据此删锁
        return ctypes.get_last_error() == 5
    try:
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == 259
    finally:
        k32.CloseHandle(h)


def _terminate(pid: int) -> None:
    if os.name == "nt":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(0x0001 | 0x1000, False, pid)  # PROCESS_TERMINATE | QUERY_LIMITED
        if h:
            try:
                k32.TerminateProcess(h, 1)
            finally:
                k32.CloseHandle(h)
        return
    import signal

    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


#: 锁文件读不出持有者记录时，mtime 在这个秒数内就视为「正被写入的活锁」，不能当陈旧锁回收。
LOCK_GRACE_S = 30


@contextlib.contextmanager
def job_lock(lab: Lab, run_id: str, task: str):
    """同一时刻只有一个任务（4 核 8 GB；也保证 DMIT 上最多一个联网测试）。
    记录先完整写进同目录临时文件，再用硬链接（原子、目标已存在则失败）发布成 job.lock：
    别的进程要么看不到锁，要么看到完整记录，不会读到写了一半的空文件。"""
    lab.state.mkdir(parents=True, exist_ok=True)
    record = {"pid": os.getpid(), "run_id": run_id, "task": task, "started": iso_now()}
    tmp = lab.lock_path.with_name(f"{lab.lock_path.name}.{os.getpid()}.{run_id}.tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    try:
        for _ in range(2):
            try:
                os.link(tmp, lab.lock_path)
            except FileExistsError:
                holder = read_lock(lab)
                if holder is None:
                    if not lab.lock_path.exists():
                        continue  # 读的当口锁被释放了：重试发布
                    if lock_is_fresh(lab):
                        raise LabBusy({}) from None  # 读不出记录但刚写过：不能当持有者已退出
                elif not _pid_alive(int(holder.get("pid", 0))):
                    pass  # 持有者已不在：陈旧锁
                else:
                    raise LabBusy(holder) from None
                if not _reclaim_stale(lab, holder):
                    raise LabBusy(read_lock(lab) or {}) from None
                continue
            break
        else:
            raise LabBusy(read_lock(lab) or {})
    finally:
        tmp.unlink(missing_ok=True)
    try:
        yield
    finally:
        # 只删自己的锁：持有者判定一旦出错、别的任务已经接手，不能把它的锁一起带走
        if (read_lock(lab) or {}).get("run_id") == run_id:
            lab.lock_path.unlink(missing_ok=True)


def _reclaim_stale(lab: Lab, judged: dict | None) -> bool:
    """回收陈旧锁：不 `unlink`（两个回收者同时判陈旧时，后删的那个会删掉先到者刚发布的新锁），而是原子改名成
    唯一的墓碑——只有一个回收者改得走；再核墓碑里就是刚才判陈旧的那份记录，不是就放回原处并报忙。"""
    tomb = lab.lock_path.with_name(
        f"{lab.lock_path.name}.{os.getpid()}.{secrets.token_hex(4)}.stale"
    )
    try:
        os.rename(lab.lock_path, tomb)
    except FileNotFoundError:
        return True  # 别人已经回收了：重试发布
    except OSError:
        return False
    try:
        got = json.loads(tomb.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    if got != judged:
        with contextlib.suppress(OSError):
            os.link(tomb, lab.lock_path)  # 改走的是别人刚发布的新锁：放回去
        tomb.unlink(missing_ok=True)
        return False
    tomb.unlink(missing_ok=True)
    return True


class LabBusy(Exception):
    pass


def read_lock(lab: Lab) -> dict | None:
    try:
        rec = json.loads(lab.lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def lock_is_fresh(lab: Lab) -> bool:
    """锁文件存在、但读不出持有者记录，且 mtime 在宽限期内：当作正被写入的活锁。"""
    try:
        age = time.time() - lab.lock_path.stat().st_mtime
    except OSError:
        return False
    return age < LOCK_GRACE_S


# ----------------------------------------------------------------------------- 环境事实
def _reg_dword(path: str, name: str):
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def windows_facts() -> dict:
    import locale
    import platform

    facts = {
        "platform": platform.platform(),
        "windows_build": platform.version(),
        "python": platform.python_version(),
        "ansi_codepage": locale.getpreferredencoding(False),
        "long_paths_enabled": _reg_dword(
            r"SYSTEM\CurrentControlSet\Control\FileSystem", "LongPathsEnabled"
        ),
        "pythonutf8": os.environ.get("PYTHONUTF8", "unset"),
        "user": os.environ.get("USERNAME", ""),
        "is_admin": _is_admin(),
    }
    return facts


def _is_admin() -> bool | None:
    if os.name != "nt":
        return None
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _tool_version(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (out.stdout or out.stderr).strip().splitlines()
    return line[0] if out.returncode == 0 and line else None


def proxy_listeners() -> dict:
    """每个代理端口：回环上能否连上；以及非回环地址上**连不上**（不能对外监听）。"""
    out = {}
    for mode, port in PROXY_PORTS.items():
        out[mode] = {"port": port, "loopback_open": _tcp_open("127.0.0.1", port)}
        try:
            lan = socket.gethostbyname(socket.gethostname())
        except OSError:
            lan = None
        out[mode]["non_loopback_open"] = (
            _tcp_open(lan, port) if lan and not lan.startswith("127.") else None
        )
    return out


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ----------------------------------------------------------------------------- 网络探测
def _opener(mode: str, ca_pem: str | None = None, cafile: str | None = None):
    if mode == "direct":
        handler = urllib.request.ProxyHandler({})
    else:
        url = f"http://127.0.0.1:{PROXY_PORTS[mode]}"
        handler = urllib.request.ProxyHandler({"http": url, "https": url})
    ctx = ssl.create_default_context()  # 完整证书校验；绝不关
    if cafile:  # venv 里 certifi 的 CA 包（只用于固定散列下载这一条路径）
        ctx.load_verify_locations(cafile=cafile)
    if ca_pem:  # 只多信一个钉死散列的根证书（见 STARFIELD_ROOT_G2_*），不是放宽校验
        ctx.load_verify_locations(cadata=ca_pem)
    return urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=ctx))


def _timed_request(opener, url: str, method: str = "GET", timeout: float = 15.0) -> dict:
    t0 = time.monotonic()
    req = urllib.request.Request(
        url, method=method, headers={"User-Agent": "tavotto-winlab-probe/1"}
    )
    try:
        with opener.open(req, timeout=timeout) as r:
            ttfb = time.monotonic() - t0
            r.read(256)
            return {"ok": True, "status": r.status, "ttfb_ms": round(ttfb * 1000)}
    except urllib.error.HTTPError as e:  # 4xx/5xx 说明 TLS 与链路是通的
        return {"ok": True, "status": e.code, "ttfb_ms": round((time.monotonic() - t0) * 1000)}
    except ssl.SSLCertVerificationError as e:
        return {"ok": False, "error": "tls_verify_failed", "detail": e.verify_message}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        return {
            "ok": False,
            "error": type(reason).__name__,
            "ms": round((time.monotonic() - t0) * 1000),
        }


def egress_digest(mode: str) -> dict:
    r = _timed_request(_opener(mode), EGRESS_URL)
    if not r.get("ok") or r.get("status") != 200:
        return {"ok": False, **r}
    # 再取一次正文（_timed_request 只读了前 256 字节，IP 足够短）
    try:
        with _opener(mode).open(EGRESS_URL, timeout=15) as resp:
            ip = resp.read(64).decode("ascii", "replace").strip()
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": type(e).__name__}
    return {"ok": True, "sha256": hashlib.sha256(ip.encode()).hexdigest(), "ttfb_ms": r["ttfb_ms"]}


def _stream_probe(mode: str) -> dict:
    """长连接 / 分块到达：服务端 4 s 内滴 8 字节，看是否逐块收到而不是一次性或中断。"""
    t0 = time.monotonic()
    try:
        with _opener(mode).open(STREAM_URL, timeout=20) as r:
            arrivals = []
            while True:
                b = r.read(1)
                if not b:
                    break
                arrivals.append(round((time.monotonic() - t0) * 1000))
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": type(getattr(e, "reason", e)).__name__}
    spread = (arrivals[-1] - arrivals[0]) if len(arrivals) > 1 else 0
    return {
        "ok": len(arrivals) == 8 and spread >= 2000,
        "chunks": len(arrivals),
        "spread_ms": spread,
    }


def probe_mode(mode: str, expected_egress_sha: str | None) -> dict:
    rec: dict = {"mode": mode}
    if mode != "direct":
        port = PROXY_PORTS[mode]
        if not _tcp_open("127.0.0.1", port):
            # 不探测、不回落：代理入口不在 = 这个模式环境不可用
            rec.update(result="environment_unavailable", reason=f"127.0.0.1:{port} not listening")
            return rec
    eg = egress_digest(mode)
    rec["egress"] = {k: v for k, v in eg.items() if k != "sha256"}
    if mode != "direct":
        if not eg.get("ok"):
            rec.update(result="environment_unavailable", reason="egress check through proxy failed")
            return rec
        if expected_egress_sha is None:
            rec.update(
                result="environment_unavailable", reason="no expected egress digest installed"
            )
            return rec
        rec["egress"]["is_dmit"] = eg["sha256"] == expected_egress_sha
        if not rec["egress"]["is_dmit"]:
            rec.update(result="failed", reason="proxy egress is not the DMIT node")
            return rec
    elif eg.get("ok") and expected_egress_sha:
        rec["egress"]["is_dmit"] = eg["sha256"] == expected_egress_sha
    opener = _opener(mode)
    targets = {}
    for name, url in PROBE_TARGETS:
        samples = [
            _timed_request(opener, url, "HEAD" if "api." not in url else "GET")
            for _ in range(PROBE_SAMPLES)
        ]
        ok = [s for s in samples if s.get("ok")]
        targets[name] = {
            "reachable": f"{len(ok)}/{len(samples)}",
            "status": sorted({s["status"] for s in ok}),
            "ttfb_ms": sorted(s["ttfb_ms"] for s in ok),
            "errors": sorted({s.get("error", "") for s in samples if not s.get("ok")}),
        }
    rec["targets"] = targets
    rec["stream"] = _stream_probe(mode)
    must = PROBE_MUST_REACH[mode]
    problems: list[str] = []
    for name, t in targets.items():
        if "tls_verify_failed" in t["errors"]:
            problems.append(f"{name}: tls_verify_failed")
        if name in must:
            if not t["status"]:
                problems.append(f"{name}: unreachable ({t['reachable']})")
        elif mode == "direct" and (not t["status"] or 403 in t["status"]):
            t["expected_unreachable"] = True
    if PROBE_STREAM_REQUIRED[mode] and not rec["stream"].get("ok"):
        problems.append(f"stream: {rec['stream']}")
    if problems:
        rec.update(result="failed", reason="; ".join(problems))
    else:
        rec["result"] = "passed"
    return rec


def fail_closed_probe() -> dict:
    """代理入口不存在时，请求必须失败，而不是被系统层面悄悄直连。"""
    dead = 7899
    if _tcp_open("127.0.0.1", dead):
        return {"result": "error", "reason": f"port {dead} unexpectedly open"}
    handler = urllib.request.ProxyHandler({"https": f"http://127.0.0.1:{dead}"})
    opener = urllib.request.build_opener(handler)
    r = _timed_request(opener, "https://github.com/", "HEAD", timeout=5)
    return {
        "result": "passed" if not r.get("ok") else "failed",
        "request_ok": r.get("ok"),
        "error": r.get("error"),
    }


# ----------------------------------------------------------------------------- 任务执行
def base_env(lab: Lab) -> dict[str, str]:
    keep = (
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERNAME",
        "USERDOMAIN",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
        "HOMEDRIVE",
        "HOMEPATH",
        "PUBLIC",
        "ALLUSERSPROFILE",
    )
    env = {k: os.environ[k] for k in os.environ if k.upper() in keep}
    env["PATH"] = os.pathsep.join(
        [
            str(lab.venv_python().parent),
            r"C:\Program Files\Git\cmd",
            NODE_DIR,
            str(lab.cache / "npm-global"),
            r"C:\Windows\System32",
            r"C:\Windows",
            r"C:\Windows\System32\WindowsPowerShell\v1.0",
        ]
    )
    env["TAVOTTO_NO_TELEMETRY"] = "1"
    # repo\ 由管理员初始化时建、属主是 Administrators：tavotto-lab 的 git 会以「可疑属主」拒认它。
    # 用命令行级（受保护）配置只信任这一个目录，不改任何全局配置
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "safe.directory"
    env["GIT_CONFIG_VALUE_0"] = lab.repo.as_posix()
    # 不设 PYTHONDONTWRITEBYTECODE：CI 不设、用户机器也不设；需要「不留 .pyc」的验证器在自己的子进程里设。
    # 写字节码缓存还省掉每个子进程的重复编译（Defender 会扫描每个读到的源文件）。
    env["TAVOTTO_FONTS_DIR"] = str(lab.fonts)
    env["PIP_INDEX_URL"] = PIP_INDEX
    env["npm_config_registry"] = NPM_REGISTRY
    # 不设 PYTHONUTF8：Lab 的价值之一就是在用户默认的 ANSI 代码页（GB2312）下跑
    return env


def junit_failures(junit: Path, lab_root: Path, limit: int = 60) -> list[dict]:
    """失败 / 错误用例的名字与首行信息（截 200 字）；Lab 根路径换成 `<lab>`，报告不带本机绝对路径。"""
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(junit).getroot()
    except (OSError, ET.ParseError):
        return []
    out = []
    roots = {str(lab_root), str(lab_root).replace("\\", "\\\\"), lab_root.as_posix()}
    for case in root.iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        msg = " ".join((bad.get("message") or bad.text or "").split())
        for r in roots:
            msg = msg.replace(r, "<lab>")
        out.append(
            {
                "test": f"{case.get('classname', '')}::{case.get('name', '')}",
                "kind": bad.tag,
                "message": msg[:200],
            }
        )
        if len(out) >= limit:
            break
    return out


def run_pytest_task(
    lab: Lab, task: Task, run_dir: Path, tests: list[str], shard: str | None, env: dict
) -> dict:
    py = lab.venv_python()
    if not py.exists():
        return {
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    sel = list(task.tests) or tests
    missing = [s for s in sel if not (lab.repo / s.split("::")[0]).exists()]
    if missing:
        return {"result": "not_run", "summary": f"registered tests missing in checkout: {missing}"}
    junit = run_dir / "evidence" / "junit.xml"
    argv = [
        str(py),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--durations=50",
        "-rs",
        "-vv",
        f"--junitxml={junit}",
        "-o",
        "junit_family=xunit2",
    ]
    if shard:
        argv.append(f"--shard={shard}")
    argv += sel
    work = run_dir / "work"
    env = dict(env)
    env.update(
        {
            # 不设 TAVOTTO_DATA_DIR / MPLCONFIGDIR：与 CI 同口径，用例的隔离由 tests/conftest.py 自己做
            "TAVOTTO_CI_STATE_ROOT": str(work / "ci-state"),
            "TEMP": str(work / "tmp"),
            "TMP": str(work / "tmp"),
            "PYTHONPATH": str(lab.repo / "src"),
            # 与 CI 同一只挂死看门狗（tests/support/hang_watchdog.py）：单条超 900 s 就点名失败，不是整片跑满
            "TAVOTTO_TEST_HANG_SECONDS": "900",
        }
    )
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    step = _run_logged(argv, lab.repo, env, run_dir / "evidence" / "pytest", task.timeout_s)
    if step.get("timed_out"):
        return {"result": "error", "summary": f"timed out after {task.timeout_s}s", "step": step}
    if step.get("spawn_error"):
        return {"result": "environment_unavailable", "summary": step["spawn_error"], "step": step}
    result, summary, counts = av.judge_pytest(step, junit)
    return {
        "result": result,
        "summary": summary,
        "counts": counts,
        "step": step,
        "failures": junit_failures(junit, lab.root),
    }


def _run_logged(
    argv: list[str],
    cwd: Path,
    env: dict,
    prefix: Path,
    timeout: float,
    stdin_data: bytes | None = None,
) -> dict:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with open(f"{prefix}.stdout.log", "wb") as out, open(f"{prefix}.stderr.log", "wb") as err:
        try:
            tree = ProcessTree(
                argv,
                cwd=cwd,
                env=env,
                stdout=out,
                stderr=err,
                stdin=subprocess.PIPE if stdin_data is not None else None,
            )
        except OSError as exc:
            err.write(f"{type(exc).__name__}: {exc}\n".encode("utf-8", "replace"))
            return {
                "exit_code": None,
                "timed_out": False,
                "duration_s": 0.0,
                "spawn_error": type(exc).__name__,
            }
        _ACTIVE.append(tree)
        try:
            if stdin_data is not None:
                tree.proc.stdin.write(stdin_data)
                tree.proc.stdin.close()
            rc, timed_out = tree.wait(timeout)
        finally:
            tree.close()
            _ACTIVE.remove(tree)
    return {"exit_code": rc, "timed_out": timed_out, "duration_s": round(time.monotonic() - t0, 3)}


_ACTIVE: list[ProcessTree] = []


def run_network_task(lab: Lab, modes: list[str]) -> dict:
    expected = None
    with contextlib.suppress(OSError):
        expected = (lab.state / "proxy-egress.sha256").read_text(encoding="ascii").strip() or None
    records = [probe_mode(m, expected) for m in modes]
    fc = fail_closed_probe()
    results = [r["result"] for r in records] + [fc["result"]]
    overall = av.aggregate(results)
    return {
        "result": overall,
        "summary": ", ".join(f"{r['mode']}={r['result']}" for r in records),
        "modes": records,
        "fail_closed": fc,
        "listeners": proxy_listeners(),
    }


#: L2/L3 宿主。包名与版本来自官方 npm 包；装进 Lab 自己的前缀，不碰全局。
HOSTS = (
    {
        "id": "claude-code",
        "npm": "@anthropic-ai/claude-code",
        "bin": "claude.cmd",
        "mcp_list": ["mcp", "list"],
        "config_env": "CLAUDE_CONFIG_DIR",
    },
    {
        "id": "codex-cli",
        "npm": "@openai/codex",
        "bin": "codex.cmd",
        "mcp_list": ["mcp", "list", "--json"],
        "config_env": "CODEX_HOME",
    },
)


def run_hosts_task(lab: Lab, run_dir: Path, env: dict) -> dict:
    prefix = lab.cache / "npm-global"
    py = lab.venv_python()
    if not py.exists():
        return {
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    npm = Path(NODE_DIR) / "npm.cmd"
    records = []
    for h in HOSTS:
        rec: dict = {"host": h["id"], "levels": {}}
        ev = run_dir / "evidence" / h["id"]
        inst = _run_logged(
            [
                str(npm),
                "install",
                "-g",
                "--prefix",
                str(prefix),
                h["npm"],
                "--no-fund",
                "--no-audit",
            ],
            lab.root,
            env,
            ev / "npm-install",
            900,
        )
        exe = prefix / h["bin"]
        version = _tool_version([str(exe), "--version"]) if exe.exists() else None
        rec["version"] = version
        rec["levels"]["L2"] = "passed" if inst.get("exit_code") == 0 and version else "failed"
        if rec["levels"]["L2"] != "passed":
            rec["levels"]["L3"] = "not_run"
        else:
            cfg = run_dir / "work" / f"{h['id']}-config"
            cfg.mkdir(parents=True, exist_ok=True)
            henv = dict(env)
            henv[h["config_env"]] = str(cfg)
            server = [str(py), str(lab.repo / "codex-plugin" / "mcp" / "server.py")]
            if h["id"] == "claude-code":
                add = [
                    str(exe),
                    "mcp",
                    "add",
                    "tavotto",
                    "--scope",
                    "user",
                    "--",
                    *server,
                ]
            else:
                add = [str(exe), "mcp", "add", "tavotto", "--", *server]
            _run_logged(add, lab.root, henv, ev / "mcp-add", 120)
            listed = _run_logged([str(exe), *h["mcp_list"]], lab.root, henv, ev / "mcp-list", 180)
            text = Path(f"{ev / 'mcp-list'}.stdout.log").read_text(
                encoding="utf-8", errors="replace"
            )
            if h["id"] == "claude-code":
                # `claude mcp list` 会真正拉起每个 server 做健康检查：这是宿主自己连上 Tavotto
                ok = (
                    listed.get("exit_code") == 0
                    and re.search(r"tavotto:.*(Connected|✓)", text) is not None
                )
                rec["levels"]["L3"] = "passed" if ok else "failed"
            else:
                # codex 的 `mcp list` 只读配置、不连接：只能算 config_tested，不升格为 L3
                ok = listed.get("exit_code") == 0 and '"tavotto"' in text
                rec["levels"]["L3"] = "not_run"
                rec["config_tested"] = ok
        rec["levels"]["L4"] = "environment_unavailable"
        rec["levels"]["L5"] = "environment_unavailable"
        rec["blocked_reason"] = (
            "L4/L5 need an authorized login and real model calls; not granted on the Lab"
        )
        records.append(rec)
    l23 = [v for r in records for k, v in r["levels"].items() if k in ("L2", "L3")]
    return {
        "result": av.aggregate(l23),
        "summary": "; ".join(f"{r['host']} {r['levels']}" for r in records),
        "hosts": records,
    }


#: L5 用的真实科研绘图脚本：两条带误差带的曲线 + 图例，写进 run 自己的项目目录。
LIVE_FIGURE = """import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

t = np.linspace(0, 10, 200)
fig, ax = plt.subplots(figsize=(3.5, 2.6))
for k, label in ((1.0, "control"), (1.6, "treated")):
    y = np.exp(-t / 4) * np.cos(k * t)
    ax.plot(t, y, label=label)
    ax.fill_between(t, y - 0.08, y + 0.08, alpha=0.2)
ax.set_xlabel("time (s)")
ax.set_ylabel("response (a.u.)")
ax.legend(loc="lower right")
fig.savefig("figure.pdf")
"""

LIVE_SESSION_1 = (
    "In this project there is a matplotlib script plot.py that produced figure.pdf. "
    "Use only the tavotto MCP tools. Steps: (1) call tavotto_health; "
    "(2) call tavotto_open_figure on plot.py; (3) with tavotto_apply_overrides set the line width "
    "of the first data line to 3 and move the legend to the upper left (pick gids/props from the "
    "manifest); (4) call tavotto_export with format pdf into the project's out/ folder. The user has "
    "explicitly confirmed exporting even if preflight reports issues, so pass explicit_confirm=true; "
    "(5) call tavotto_verify_replay. Do NOT close the session. "
    "Finish with one line: SESSION <session_id>."
)
#: 第二轮是全新的宿主进程，只拿到第一轮的 session_id：服务端得从会话日志恢复、从零重放同一组修改
LIVE_SESSION_2 = (
    "Use only the tavotto MCP tools. Call tavotto_verify_replay with session_id {sid}, then call "
    "tavotto_close_session with session_id {sid}. Finish with one line: REPLAY <ok field of verify_replay>."
)


DEFAULT_HOST_MODEL = "deepseek-flash"


def _host_model_env(conf: dict) -> dict[str, str]:
    """国内 Anthropic 兼容接口（供应商官方给的 Claude Code 接入变量）+ 关掉 Claude Code 发往 Anthropic 的
    非必要流量：Lab 在中国大陆，不经代理、也不碰海外服务。"""
    model = conf.get("model") or DEFAULT_HOST_MODEL
    return {
        "ANTHROPIC_BASE_URL": conf["base_url"],
        "ANTHROPIC_AUTH_TOKEN": conf["api_key"],
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "CLAUDE_CODE_SUBAGENT_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
    }


def parse_stream(text: str) -> dict:
    """claude `--output-format stream-json` → 工具调用与结果的事实（只取名字、成败、结果文本前 4 KB）。"""
    calls: dict[str, dict] = {}
    order: list[str] = []
    final = ""
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") if isinstance(ev, dict) else None
        if not isinstance(msg, dict):  # system / result 事件里 message 可以是字符串
            msg = {}
        for block in msg.get("content") or [] if isinstance(msg.get("content"), list) else []:
            if block.get("type") == "tool_use":
                calls[block["id"]] = {"name": block.get("name", ""), "input": block.get("input")}
                order.append(block["id"])
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                content = block.get("content")
                if isinstance(content, list):
                    content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
                calls[block["tool_use_id"]].update(
                    is_error=bool(block.get("is_error")), result=str(content)[:4096]
                )
        if isinstance(ev, dict) and ev.get("type") == "result":
            final = str(ev.get("result") or "")
    return {"calls": [calls[i] for i in order], "final": final[:4096]}


def _ok_calls(facts: dict, tool: str) -> list[dict]:
    return [
        c
        for c in facts["calls"]
        if c["name"] == f"mcp__tavotto__{tool}" and c.get("is_error") is False
    ]


SESSION_ID_RE = re.compile(r'"session_id"\s*:\s*"(s-[0-9a-f]{12})"')
_OK_TRUE = re.compile(r'"ok"\s*:\s*true')
_PATCH_HASH_RE = re.compile(r'"patch_hash"\s*:\s*"(sha256:[0-9a-f]+)"')
_ANY_SESSION_ID_RE = re.compile(r'"session_id"\s*:\s*"(s-[0-9a-f]{12})"')


def _first(rx: re.Pattern, text: str) -> str | None:
    m = rx.search(text)
    return m.group(1) if m else None


def opened_session_id(facts: dict) -> str | None:
    """会话 id：open 的结果可能大到被宿主转存成文件（模型只拿到一句提示），所以看所有成功的 Tavotto 结果，
    最后才退回模型自己写的 `SESSION s-…` 那一行。"""
    for c in facts["calls"]:
        if c["name"].startswith("mcp__tavotto__") and c.get("is_error") is False:
            m = SESSION_ID_RE.search(c.get("result", ""))
            if m:
                return m.group(1)
    m = re.search(r"SESSION\s+(s-[0-9a-f]{12})", facts.get("final", ""))
    return m.group(1) if m else None


def journal_has_edits(data_dir: Path, sid: str | None) -> bool:
    """执行器自己读 Tavotto 的会话日志（`mcp-sessions/<sid>.json`），不信模型的转述：线宽 3 那一条要真在里面。"""
    if not sid:
        return False
    for p in data_dir.rglob(f"{sid}.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for patch in rec.get("patches") or []:
            if (
                isinstance(patch, dict)
                and str(patch.get("prop", "")).lower() in ("linewidth", "lw")
                and float(patch.get("value", 0) or 0) == 3
            ):
                return True
    return False


def judge_live(s1: dict, s2: dict, pdf: Path | None, journal_ok: bool, sid: str | None) -> dict:
    """L4 / L5 只看真实发生的事：工具调用要有成功的结果，PDF 要真在、是 PDF，修改要真落进会话日志，
    全新宿主进程凭 session_id 恢复后，会话 id 与 patch_hash 要和第一轮编辑后的一模一样
    （只看 ok:true 不够：恢复丢了修改时，空 patch 集的重放同样一致）。解析不到字段就是失败。"""
    l4 = (
        "passed"
        if _ok_calls(s1, "tavotto_health") or _ok_calls(s1, "tavotto_open_figure")
        else "failed"
    )
    replay1 = _ok_calls(s1, "tavotto_verify_replay")
    replay2 = _ok_calls(s2, "tavotto_verify_replay")
    applies = _ok_calls(s1, "tavotto_apply_overrides")
    edited_hash = _first(_PATCH_HASH_RE, applies[-1].get("result", "")) if applies else None

    def restored_same(c: dict) -> bool:
        text = c.get("result", "")
        return bool(
            sid
            and edited_hash
            and _OK_TRUE.search(text)
            and _first(_ANY_SESSION_ID_RE, text) == sid
            and _first(_PATCH_HASH_RE, text) == edited_hash
        )

    checks = {
        "opened": bool(_ok_calls(s1, "tavotto_open_figure")),
        "applied": bool(applies),
        "exported_pdf": bool(pdf and pdf.is_file() and pdf.read_bytes()[:5] == b"%PDF-"),
        "replay_verified": any(_OK_TRUE.search(c.get("result", "")) for c in replay1),
        "edits_in_session_journal": journal_ok,
        "fresh_process_restored_same_edits": any(restored_same(c) for c in replay2),
    }
    l5 = "passed" if all(checks.values()) else "failed"
    return {"L4": l4, "L5": l5, "checks": checks}


def parse_recorded(path: Path, start_line: int = 0) -> dict:
    """mcp_recorder 的 jsonl（服务端事实）→ 与 parse_stream 同形：判定只认 Tavotto 这一侧真发生了什么，
    不认宿主怎么转述。`start_line` 之后的记录属于这一轮。"""
    calls, listed = [], []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[start_line:]
    except OSError:
        lines = []
    for line in lines:
        with contextlib.suppress(ValueError):
            rec = json.loads(line)
            if rec.get("dir") != "server":
                continue
            if rec.get("method") == "tools/list" and not rec.get("error"):
                listed += rec.get("tools") or []
            if rec.get("method") == "tools/call":
                calls.append(
                    {
                        "name": f"mcp__tavotto__{rec.get('tool')}",
                        "is_error": bool(rec.get("error")),
                        "result": json.dumps(rec.get("facts") or {}, separators=(",", ":")),
                    }
                )
    return {"calls": calls, "final": "", "listed_tools": sorted(set(listed))}


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


@dataclass
class LiveHost:
    id: str
    npm: str
    bin: str
    #: (lab, run_dir, proj, exe, mcp_command, mcp_env, model_conf, env) → 宿主进程的额外环境（配置写在 run 自己的目录里）
    configure: object
    #: prompt → argv（无头模式，提示词紧跟在前面，避免被可变长选项吞掉）
    argv: object


def _claude_configure(lab, run_dir, proj, exe, mcp_cmd, mcp_env, conf, env) -> dict:
    cfg = run_dir / "work" / "claude-config"
    cfg.mkdir(parents=True, exist_ok=True)
    henv = dict(_host_model_env(conf), CLAUDE_CONFIG_DIR=str(cfg))
    add = [str(exe), "mcp", "add", "tavotto", "--scope", "user"]
    for k, v in mcp_env.items():
        add += ["-e", f"{k}={v}"]
    _run_logged(
        [*add, "--", *mcp_cmd],
        proj,
        {**env, **henv},
        run_dir / "evidence" / "live" / "mcp-add",
        120,
    )
    return henv


def _claude_argv(exe: Path, prompt: str, henv: dict) -> list[str]:
    return [
        str(exe),
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "30",
        "--allowedTools",
        "mcp__tavotto",
    ]


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _codex_configure(lab, run_dir, proj, exe, mcp_cmd, mcp_env, conf, env) -> dict:
    """Codex：隔离的 CODEX_HOME + 自定义供应商（DeepSeek 的 OpenAI 兼容 Responses 接口，env_key 取密钥，不走登录）。"""
    home = run_dir / "work" / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    env_lines = ", ".join(f"{k} = {_toml_str(v)}" for k, v in mcp_env.items())
    toml = "\n".join(
        [
            f"model = {_toml_str(conf.get('openai_model') or 'deepseek-flash')}",
            'model_provider = "lab"',
            'forced_login_method = "api"',
            'approval_policy = "never"',
            'sandbox_mode = "workspace-write"',
            "",
            "[model_providers.lab]",
            'name = "lab"',
            f"base_url = {_toml_str(conf.get('openai_base_url') or 'https://api.deepseek.com/')}",
            'wire_api = "responses"',
            'env_key = "WINLAB_MODEL_KEY"',
            "",
            "[mcp_servers.tavotto]",
            f"command = {_toml_str(mcp_cmd[0])}",
            "args = [" + ", ".join(_toml_str(a) for a in mcp_cmd[1:]) + "]",
            f"env = {{ {env_lines} }}",
            "startup_timeout_sec = 60",
            "tool_timeout_sec = 1800",
            'default_tools_approval_mode = "approve"',
            "",
        ]
    )
    (home / "config.toml").write_text(toml, encoding="utf-8")
    return {"CODEX_HOME": str(home), "WINLAB_MODEL_KEY": conf["api_key"]}


def _codex_argv(exe: Path, prompt: str, henv: dict) -> list[str]:
    return [
        str(exe),
        "exec",
        prompt,
        "--skip-git-repo-check",
        "--json",
        "--sandbox",
        "workspace-write",
    ]


def _isolated_home(run_dir: Path) -> dict[str, str]:
    """宿主的用户级配置落在本次 run 的目录里：不碰 tavotto-lab 的真实配置，也不在两次 run 之间串味。"""
    home = run_dir / "work" / "home"
    for sub in ("AppData/Roaming", "AppData/Local"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return {
        "USERPROFILE": str(home),
        "HOME": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
    }


def _mcp_servers_json(mcp_cmd: list[str], mcp_env: dict) -> dict:
    return {"mcpServers": {"tavotto": {"command": mcp_cmd[0], "args": mcp_cmd[1:], "env": mcp_env}}}


def _codebuddy_configure(lab, run_dir, proj, exe, mcp_cmd, mcp_env, conf, env) -> dict:
    """CodeBuddy CLI：models.json 指向 OpenAI 兼容的 Chat Completions（密钥只以 ${变量} 引用），MCP 走 --mcp-config。"""
    henv = _isolated_home(run_dir)
    cfg = Path(henv["USERPROFILE"]) / ".codebuddy"
    cfg.mkdir(parents=True, exist_ok=True)
    model = conf.get("openai_model") or "deepseek-flash"
    base = (conf.get("openai_base_url") or "https://api.deepseek.com/").rstrip("/")
    models = {
        "models": [
            {
                "id": model,
                "name": model,
                "vendor": conf.get("provider") or "lab",
                "url": f"{base}/v1/chat/completions",
                "apiKey": "${WINLAB_MODEL_KEY}",
                "maxInputTokens": 128000,
                "maxOutputTokens": 8192,
                "supportsToolCall": True,
                "supportsImages": False,
            }
        ],
        "availableModels": [model],
    }
    (cfg / "models.json").write_text(json.dumps(models, indent=2), encoding="utf-8")  # 无 BOM
    mcp = run_dir / "work" / "codebuddy-mcp.json"
    mcp.write_text(json.dumps(_mcp_servers_json(mcp_cmd, mcp_env), indent=2), encoding="utf-8")
    henv.update(
        {
            "CODEBUDDY_CONFIG_DIR": str(cfg),
            "WINLAB_MODEL_KEY": conf["api_key"],
            "WINLAB_MCP_CONFIG": str(mcp),
            "WINLAB_MODEL": model,
        }
    )
    return henv


def _codebuddy_argv(exe: Path, prompt: str, henv: dict) -> list[str]:
    # MCP 工具是延迟加载的：经 ToolSearch 找到、经 DeferExecuteTool 执行——无头模式下三者都要预先授权
    return [
        str(exe),
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--model",
        henv["WINLAB_MODEL"],
        "--mcp-config",
        henv["WINLAB_MCP_CONFIG"],
        "--strict-mcp-config",
        "--allowedTools",
        "mcp__tavotto",
        "ToolSearch",
        "DeferExecuteTool",
    ]


def _minimax_configure(lab, run_dir, proj, exe, mcp_cmd, mcp_env, conf, env) -> dict:
    """MiniMax Code：自定义供应商（OpenAI 兼容 completions，密钥经 --api-key-env 引用），MCP 走项目根 .mcp.json。"""
    henv = _isolated_home(run_dir)
    model = conf.get("openai_model") or "deepseek-flash"
    henv.update({"WINLAB_MODEL_KEY": conf["api_key"], "WINLAB_MODEL": f"lab/{model}"})
    (proj / ".mcp.json").write_text(
        json.dumps(_mcp_servers_json(mcp_cmd, mcp_env), indent=2), encoding="utf-8"
    )
    _run_logged(
        [
            str(exe),
            "provider",
            "add",
            "--name",
            "lab",
            "--base-url",
            (conf.get("openai_base_url") or "https://api.deepseek.com/").rstrip("/"),
            "--api-format",
            "openai-completions",
            "--model",
            model,
            "--api-key-env",
            "WINLAB_MODEL_KEY",
            "--use",
        ],
        proj,
        {**env, **henv},
        run_dir / "evidence" / "live" / "provider-add",
        300,
    )
    return henv


def _minimax_argv(exe: Path, prompt: str, henv: dict) -> list[str]:
    # `provider add --use` 已把它设成默认；`--model lab/<id>` 在 cn-prod 路由上不认（10-24 实测）
    return [str(exe), "exec", prompt]


def _dsh_configure(lab, run_dir, proj, exe, mcp_cmd, mcp_env, conf, env) -> dict:
    """DSH：隔离 DSH_HOME / DSH_AGENTS_HOME；模型走 DEEPSEEK_BASE_URL（Anthropic Messages，ADR 0104 同法）；
    MCP 用一层 --patch 插入官方 dsh-mcp-client（与 codex-plugin/dsh/cordis.patch.yml 同一个插件），命令写死不用 !!js。"""
    work = run_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    patch = [
        {
            "insert": [
                {
                    "id": "tavotto-mcp",
                    "name": "@deepseek-ai/dsh-mcp-client",
                    "config": {
                        "transport": "stdio",
                        "serverName": "tavotto",
                        "command": mcp_cmd[0],
                        "args": mcp_cmd[1:],
                        "cwd": str(proj),
                        "toolCallTimeoutMs": 1800000,
                    },
                }
            ]
        }
    ]
    pf = work / "dsh-tavotto.patch.yml"
    pf.write_text(json.dumps(patch, indent=2), encoding="utf-8")  # JSON 即合法 YAML
    henv = {
        "DSH_HOME": str(work / "dsh-home"),
        "DSH_AGENTS_HOME": str(work / "dsh-agents"),
        "DEEPSEEK_BASE_URL": conf.get("base_url") or "https://api.deepseek.com/anthropic",
        "DEEPSEEK_API_KEY": conf["api_key"],
        "WINLAB_DSH_PATCH": str(pf),
    }
    # dsh-mcp-client 不收 env：MCP server 继承宿主进程的环境
    henv.update(mcp_env)
    return henv


def _dsh_argv(exe: Path, prompt: str, henv: dict) -> list[str]:
    return [str(exe), "headless", "--patch", henv["WINLAB_DSH_PATCH"], prompt]


#: 宿主自己说「要登录官方账户」的字样：出现且一次 Tavotto 工具都没调到 → BLOCKED（需要账户），不是 FAIL
LOGIN_MARKERS = re.compile(r"sign in|log ?in to|please login|not logged in|请先登录|需要登录", re.I)

LIVE_HOSTS = {
    "claude-code": LiveHost(
        "claude-code", "@anthropic-ai/claude-code", "claude.cmd", _claude_configure, _claude_argv
    ),
    "codex-cli": LiveHost("codex-cli", "@openai/codex", "codex.cmd", _codex_configure, _codex_argv),
    "codebuddy": LiveHost(
        "codebuddy",
        "@tencent-ai/codebuddy-code",
        "codebuddy.cmd",
        _codebuddy_configure,
        _codebuddy_argv,
    ),
    "minimax-code": LiveHost(
        "minimax-code", "@minimax-ai/code", "mcode.cmd", _minimax_configure, _minimax_argv
    ),
    "dsh": LiveHost("dsh", "@deepseek-ai/dsh@next", "dsh.cmd", _dsh_configure, _dsh_argv),
}


def _seed_live_project(py: Path, run_dir: Path, env: dict, ev: Path) -> tuple[Path, bool]:
    """种图：项目目录里放 LIVE_FIGURE 并跑一遍产出 figure.pdf（hosts-live 与 desktop-live 同一张图）。"""
    proj = run_dir / "work" / "project"
    (proj / "out").mkdir(parents=True)
    (proj / "plot.py").write_text(LIVE_FIGURE, encoding="utf-8")
    seed_env = dict(env, MPLCONFIGDIR=str(run_dir / "work" / "mpl"))
    seed = _run_logged([str(py), "plot.py"], proj, seed_env, ev / "seed-figure", 300)
    return proj, seed.get("exit_code") == 0


def run_hosts_live_task(
    lab: Lab, run_dir: Path, env: dict, model_conf: dict, host_id: str = "claude-code"
) -> dict:
    host = LIVE_HOSTS[host_id]
    py = lab.venv_python()
    if not py.exists():
        return {
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    ev = run_dir / "evidence" / "live"
    prefix = lab.cache / "npm-global"
    exe = prefix / host.bin
    if not exe.exists():
        _run_logged(
            [
                str(Path(NODE_DIR) / "npm.cmd"),
                "install",
                "-g",
                "--prefix",
                str(prefix),
                host.npm,
                "--no-fund",
                "--no-audit",
            ],
            lab.root,
            dict(env, npm_config_better_sqlite3_binary_host=SQLITE_BINARY_MIRROR),
            ev / "npm-install",
            900,
        )
    version = _tool_version([str(exe), "--version"]) if exe.exists() else None
    if not version:
        return {
            "result": "failed",
            "summary": f"{host.id}: official CLI did not install or start (L2)",
            "levels": {"L2": "failed"},
        }
    proj, seeded = _seed_live_project(py, run_dir, env, ev)
    if not seeded:
        return {
            "result": "error",
            "summary": "seeding the figure script failed",
            "host_version": version,
        }
    rec_log = ev / "mcp-recorder.jsonl"
    recorder = Path(__file__).resolve().parent / "mcp_recorder.py"
    mcp_cmd = [
        str(py),
        str(recorder),
        str(rec_log),
        "--",
        str(py),
        str(lab.repo / "codex-plugin" / "mcp" / "server.py"),
    ]
    # 只给 MCP 服务器项目根与遥测开关（写进宿主的 MCP 配置）；模型凭据只进宿主进程。数据目录不指定：
    # MCP server 用 tavotto-lab 自己的默认数据目录，与真实用户一致，会话日志按唯一的 session_id 去那里找
    mcp_env = {"TAVOTTO_MCP_ROOTS": str(proj), "TAVOTTO_NO_TELEMETRY": "1"}
    henv = dict(env)
    henv.update(host.configure(lab, run_dir, proj, exe, mcp_cmd, mcp_env, model_conf, env))
    r1 = _run_logged(host.argv(exe, LIVE_SESSION_1, henv), proj, henv, ev / "session-1", 1200)
    s1 = parse_recorded(rec_log)
    split = _line_count(rec_log)
    sid = opened_session_id(s1)
    # MCP server 继承宿主进程的环境：宿主的用户目录被隔离时（CodeBuddy / MiniMax），它的默认数据目录也跟着走
    data_dir = (
        Path(henv.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA") or Path.home()) / "Tavotto"
    )
    journal_ok = journal_has_edits(data_dir, sid)
    if sid:
        r2 = _run_logged(
            host.argv(exe, LIVE_SESSION_2.format(sid=sid), henv), proj, henv, ev / "session-2", 900
        )
    else:
        r2 = {"exit_code": None, "skipped": "no session id from session 1"}
    _scrub(ev, model_conf["api_key"])
    _scrub(run_dir / "work", model_conf["api_key"])
    s2 = parse_recorded(rec_log, split)
    host_text = ""
    for f in ev.glob("session-1.*.log"):
        host_text += f.read_text(encoding="utf-8", errors="replace")[-20000:]
    if not s1["calls"] and LOGIN_MARKERS.search(host_text):
        return {
            "result": "environment_unavailable",
            "summary": f"{host.id} {version}: the host requires signing in to its own account before any tool call (L4/L5 blocked)",
            "host": {"id": host.id, "version": version},
            "levels": {
                "L2": "passed",
                "L3": "passed" if "tavotto_open_figure" in s1["listed_tools"] else "not_run",
                "L4": "environment_unavailable",
                "L5": "environment_unavailable",
            },
            "blocked_reason": "host login required",
        }
    pdfs = sorted((proj / "out").glob("*.pdf"))
    levels = judge_live(s1, s2, pdfs[0] if pdfs else None, journal_ok, sid)
    levels["L2"] = "passed"
    # L3：宿主自己发了 tools/list 并拿到了 Tavotto 的工具（服务端看到的，不是宿主的自述）
    levels["L3"] = "passed" if "tavotto_open_figure" in s1["listed_tools"] else "failed"
    levels["session_id_from_session_1"] = bool(sid)
    if r1.get("timed_out") or r2.get("timed_out"):
        levels["L5"] = "error"
    return {
        "result": av.aggregate([levels["L2"], levels["L3"], levels["L4"], levels["L5"]]),
        "summary": f"{host.id} {version} via {model_conf.get('provider', '?')}: "
        f"L3={levels['L3']} L4={levels['L4']} L5={levels['L5']}",
        "host": {
            "id": host.id,
            "version": version,
            "provider": model_conf.get("provider"),
            "network": "direct",
        },
        "levels": levels,
        "server_side_calls": {
            "session_1": [
                {"tool": c["name"].split("__")[-1], "ok": not c["is_error"]} for c in s1["calls"]
            ],
            "session_2": [
                {"tool": c["name"].split("__")[-1], "ok": not c["is_error"]} for c in s2["calls"]
            ],
        },
        "exit_codes": {"session_1": r1.get("exit_code"), "session_2": r2.get("exit_code")},
    }


def _scrub(folder: Path, needle: str) -> None:
    """兜底：哪怕宿主把凭据回显进了日志，也在落成证据前抹掉。"""
    if not needle or not folder.is_dir():
        return
    raw = needle.encode()
    for p in folder.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            if raw in data:
                p.write_bytes(data.replace(raw, b"<redacted>"))


MODEL_KEY_SHAPE_RE = re.compile(r"^[A-Za-z0-9_\-\.]{16,200}$")


def read_model_conf(stream) -> dict | str:
    """stdin 上一行 JSON：{"provider", "base_url", "api_key", "model"?}。返回 dict 或错误文本。"""
    raw = stream.read(8192)
    try:
        d = json.loads(raw)
    except ValueError:
        return "model config on stdin is not JSON"
    if not isinstance(d, dict) or not MODEL_KEY_SHAPE_RE.fullmatch(str(d.get("api_key", ""))):
        return "model config needs a plausible api_key"
    if not str(d.get("base_url", "")).startswith("https://"):
        return "model config base_url must be https"
    return d


def _evidence_list(run_dir: Path) -> list[dict]:
    out = []
    ev = run_dir / "evidence"
    if ev.is_dir():
        for p in sorted(ev.rglob("*")):
            if p.is_file():
                out.append(
                    {
                        "path": p.relative_to(run_dir).as_posix(),
                        "bytes": p.stat().st_size,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    }
                )
    return out


def repo_head(lab: Lab) -> str | None:
    if not (lab.repo / ".git").exists():
        return None
    out = subprocess.run(
        [GIT, "-C", str(lab.repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        env=base_env(lab),
    )
    return out.stdout.strip() if out.returncode == 0 else None


def known_main(lab: Lab) -> str | None:
    if not (lab.repo / ".git").exists():
        return None
    out = subprocess.run(
        [GIT, "-C", str(lab.repo), "rev-parse", "-q", "--verify", "refs/winlab/main"],
        capture_output=True,
        env=base_env(lab),
        text=True,
    )
    return out.stdout.strip() if out.returncode == 0 else None


def fonts_ok(lab: Lab) -> bool:
    py = lab.venv_python() if lab.venv_python().exists() else Path(BASE_PYTHON)
    if not (lab.repo / "scripts" / "fetch_fonts.py").exists() or not lab.fonts.is_dir():
        return False
    out = subprocess.run(
        [str(py), "scripts/fetch_fonts.py", "--dest", str(lab.fonts), "--check"],
        cwd=lab.repo,
        capture_output=True,
        env=base_env(lab),
    )
    return out.returncode == 0


FONT_MEMBER_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_. -]+)*$")


def receive_fonts(lab: Lab, data: bytes) -> dict:
    """Mac 核过 sha256 的字体（`fetch_fonts.py --check` 全绿）打成 zip 经 stdin 送来；这里只做安全解包，
    逐文件 sha256 由随后的 `fetch_fonts.py --check` 按 allowlist 复核。"""
    import io
    import zipfile

    staging = lab.cache / "fonts.incoming"
    if staging.exists():
        _rmtree_no_follow(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if not FONT_MEMBER_RE.fullmatch(name) or ".." in name.split("/"):
                _rmtree_no_follow(staging)
                return {"result": "error", "summary": f"refused zip member {name!r}"}
            dest = staging / Path(*name.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info))
    if lab.fonts.exists():
        _rmtree_no_follow(lab.fonts)
    staging.rename(lab.fonts)
    return {
        "result": "passed",
        "summary": "fonts received",
        "files": sum(1 for p in lab.fonts.rglob("*") if p.is_file()),
    }


def run_installer_task(lab: Lab, run_dir: Path, env: dict, package: str, frm: str | None) -> dict:
    setup, err = package_path(lab, package)
    if err:
        return {"result": "environment_unavailable", "summary": err}
    old = None
    if frm:
        old, err = package_path(lab, frm)
        if err:
            return {"result": "environment_unavailable", "summary": err}
    py = lab.venv_python()
    if not py.exists():
        return {
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    pwsh = r"C:\Program Files\PowerShell\7\pwsh.exe"
    script = Path(__file__).resolve().parent / "installer-check.ps1"
    ev = run_dir / "evidence" / "installer"
    argv = [
        pwsh,
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script),
        "-Setup",
        str(setup),
        "-Repo",
        str(lab.repo),
        "-Python",
        str(py),
        "-Evidence",
        str(ev),
    ]
    if old:
        argv += ["-From", str(old)]
    step = _run_logged(argv, lab.repo, env, run_dir / "evidence" / "installer-check", 3500)
    steps = []
    for line in (
        Path(f"{run_dir / 'evidence' / 'installer-check'}.stdout.log")
        .read_text(encoding="utf-8", errors="replace")
        .splitlines()
    ):
        line = line.strip()
        if line.startswith("{"):
            with contextlib.suppress(ValueError):
                steps.append(json.loads(line))
    if step.get("timed_out"):
        return {"result": "error", "summary": "installer check timed out", "steps": steps}
    if not steps:
        return {"result": "error", "summary": "installer check produced no steps", "step": step}
    bad = [s for s in steps if not s.get("ok")]
    result = (
        "passed"
        if not bad
        else (
            "error"
            if any(s.get("step") == "aborted" for s in bad) and len(bad) == 1 and len(steps) < 4
            else "failed"
        )
    )
    return {
        "result": result,
        "summary": f"{len(steps) - len(bad)}/{len(steps)} steps ok"
        + (f"; failed: {[s['step'] for s in bad]}" if bad else ""),
        "package": package,
        "from": frm,
        "steps": steps,
    }


# ----------------------------------------------------------------------------- 交互会话
GUI_TASK = "TavottoLab-GUI"
GUI_START_WAIT_S = 60
WEBVIEW2_RT_KEY = (
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
WEBVIEW2_POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"
DEBUG_PORT = 9222


def ensure_gui_task(lab: Lab) -> dict:
    """tavotto-lab 名下的计划任务：只在它已登录的交互会话里运行（/IT），动作是 gui_host.py。幂等。"""
    host = Path(__file__).resolve().parent / "gui_host.py"
    tr = f'"{BASE_PYTHON}" -I "{host}"'
    out = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            GUI_TASK,
            "/TR",
            tr,
            "/SC",
            "ONCE",
            "/ST",
            "00:00",
            "/IT",
            "/RL",
            "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return {"exit_code": out.returncode}


#: 交互会话请求里密钥走的命名管道名（request.json 只带这个名字，不带值）
GUI_PIPE_RE = re.compile(r"^tavotto-winlab-[0-9a-f]{32}$")
GUI_PIPE_DIR = "\\\\.\\pipe\\"


def split_secret_env(env: dict, secret_values: list[str]) -> tuple[dict, dict]:
    """env → (公开部分, 密钥部分)：值里含有任何一个密钥字符串的项一律归密钥部分，不看变量名。"""
    secret_values = [v for v in secret_values if v]
    public, secret = {}, {}
    for k, v in env.items():
        (secret if any(sv in str(v) for sv in secret_values) else public)[k] = v
    return public, secret


def _pipe_path(name: str) -> str:
    return GUI_PIPE_DIR + name


class SecretPipe:
    """把一小份密钥环境交给交互会话里的 gui_host，**不落盘**：session 0 的 agent 建一个只给 tavotto-lab 的
    命名管道（单实例、拒绝远程客户端），写一次；gui_host 读一次就关。request.json 里只有管道名。
    Windows 专用；测试里 `agent._open_secret_pipe` 换成假的。"""

    def __init__(self, payload: dict):
        self.name = "tavotto-winlab-" + secrets.token_hex(16)
        self._data = json.dumps(payload).encode("utf-8")
        self._handle = None
        self.delivered = False
        self.error: str | None = None
        self._thread = None
        self._lock = threading.Lock()

    def start(self) -> SecretPipe:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.CreateNamedPipeW.restype = wintypes.HANDLE
        k32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        k32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        adv.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        adv.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        adv.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            wintypes.LPVOID,
        ]

        # ACL 只给当前用户（tavotto-lab）：取自己的 SID，DACL 受保护、只有这一条允许项
        token = wintypes.HANDLE()
        if not adv.OpenProcessToken(
            k32.GetCurrentProcess(), 0x8, ctypes.byref(token)
        ):  # TOKEN_QUERY
            raise OSError(ctypes.get_last_error(), "OpenProcessToken")
        need = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(need))  # TokenUser
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(token, 1, buf, need, ctypes.byref(need)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation")
        k32.CloseHandle(token)
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_str = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid_ptr, ctypes.byref(sid_str)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSid")
        sddl = f"D:P(A;;FA;;;{sid_str.value})"

        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL),
            ]

        psd = wintypes.LPVOID()
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(psd), None
        ):
            raise OSError(ctypes.get_last_error(), "ConvertStringSecurityDescriptor")
        sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), psd, False)
        # PIPE_ACCESS_OUTBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE；字节流、阻塞、拒绝远程客户端
        handle = k32.CreateNamedPipeW(
            _pipe_path(self.name),
            0x2 | 0x00080000,
            0x0 | 0x0 | 0x8,
            1,
            65536,
            65536,
            0,
            ctypes.byref(sa),
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateNamedPipe")
        self._handle = handle

        def serve() -> None:
            ok = k32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
            if not ok:
                self.error = f"ConnectNamedPipe winerror={ctypes.get_last_error()}"
                return
            written = wintypes.DWORD()
            if k32.WriteFile(handle, self._data, len(self._data), ctypes.byref(written), None):
                self.delivered = bool(k32.FlushFileBuffers(handle)) and written.value == len(
                    self._data
                )
            else:
                self.error = f"WriteFile winerror={ctypes.get_last_error()}"
            # 直接关句柄、不 DisconnectNamedPipe：断开后读端下一次 read 得 ERROR_PIPE_NOT_CONNECTED
            # （CRT 映射成 errno 22），关掉则是 ERROR_BROKEN_PIPE = 正常 EOF；管道名随之消失，只能读一次
            with self._lock:
                k32.CloseHandle(handle)
                self._handle = None

        self._k32 = k32
        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        """没人来读时（交互会话不在）：自己连一下把阻塞中的 ConnectNamedPipe 放走，再关句柄。"""
        if self._handle is None and self._thread is None:
            return
        if self._thread is not None and self._thread.is_alive():
            with contextlib.suppress(OSError):
                open(_pipe_path(self.name), "rb").close()  # noqa: SIM115
            self._thread.join(timeout=5)
        with self._lock:
            if self._handle is not None:
                self._k32.CloseHandle(self._handle)
                self._handle = None
        self._data = b""


def _open_secret_pipe(payload: dict) -> SecretPipe:
    return SecretPipe(payload).start()


def read_secret_pipe(name: str) -> dict:
    """gui_host 一侧：读一次、关掉。管道名必须是 agent 生成的形状（不接受任意路径）。"""
    if not GUI_PIPE_RE.fullmatch(name):
        raise ValueError("bad secret pipe name")
    last: OSError | None = None
    for _ in range(50):
        try:
            with open(_pipe_path(name), "rb") as f:
                return json.loads(f.read(65536).decode("utf-8"))
        except OSError as exc:  # ERROR_PIPE_BUSY：agent 那一端刚好还没进入等待
            last = exc
            time.sleep(0.2)
    raise last or OSError("secret pipe unreadable")


def run_in_gui(
    lab: Lab,
    run_id: str,
    argv: list[str],
    cwd: Path,
    env: dict,
    out_prefix: Path,
    timeout: float,
    secret_env: dict | None = None,
) -> dict:
    """把一条登记过的命令交给交互会话执行（gui_host.py），等结果。会话不在就 environment_unavailable——
    绝不退回 session 0 里假装跑过。`env` 会明文写进 request.json，**不许装密钥**：密钥放 `secret_env`，
    经命名管道交给 gui_host（只给 tavotto-lab，读一次即关，不落盘）。"""
    gui = lab.state / "gui"
    gui.mkdir(parents=True, exist_ok=True)
    reg = ensure_gui_task(lab)
    if reg["exit_code"] != 0:
        return {"exit_code": None, "gui": "task_register_failed"}
    pipe = _open_secret_pipe(secret_env) if secret_env else None
    try:
        res = _run_in_gui_request(lab, gui, run_id, argv, cwd, env, out_prefix, timeout, pipe)
        if pipe is not None:  # 诊断：只记结果与错误码，不含内容
            res["secret_pipe"] = {"delivered": pipe.delivered, "error": pipe.error}
        return res
    finally:
        if pipe is not None:
            pipe.close()


def _run_in_gui_request(lab, gui, run_id, argv, cwd, env, out_prefix, timeout, pipe) -> dict:
    req = {
        "run_id": run_id,
        "argv": argv,
        "cwd": str(cwd),
        "env": env,
        "timeout": timeout,
        "out_prefix": str(out_prefix),
        "written": time.time(),
    }
    if pipe is not None:
        req["env_pipe"] = pipe.name
    tmp = gui / "request.json.tmp"
    tmp.write_text(json.dumps(req), encoding="utf-8")
    os.replace(tmp, gui / "request.json")
    subprocess.run(["schtasks", "/Run", "/TN", GUI_TASK], capture_output=True)
    started = gui / f"{run_id}.started"
    deadline = time.monotonic() + GUI_START_WAIT_S
    while not started.exists():
        if time.monotonic() > deadline:
            (gui / "request.json").unlink(missing_ok=True)
            return {"exit_code": None, "gui": "no_interactive_session"}
        time.sleep(1)
    info = json.loads(started.read_text(encoding="utf-8"))
    result_path = gui / f"{run_id}.result.json"
    deadline = time.monotonic() + timeout + 120
    while not result_path.exists():
        if time.monotonic() > deadline:
            (gui / f"{run_id}.stop").write_text("timeout", encoding="utf-8")
            return {
                "exit_code": None,
                "timed_out": True,
                "gui": "no_result",
                "session_id": info.get("session_id"),
            }
        time.sleep(2)
    res = json.loads(result_path.read_text(encoding="utf-8"))
    for f in (started, result_path):
        f.unlink(missing_ok=True)
    res["gui"] = "ran"
    return res


def _hkcu_installs() -> list[dict]:
    import winreg

    out = []
    base = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return out
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(root, name) as k:
                vals = {}
                for v in ("DisplayName", "DisplayVersion", "InstallLocation"):
                    with contextlib.suppress(OSError):
                        vals[v] = winreg.QueryValueEx(k, v)[0]
            if str(vals.get("DisplayName", "")).startswith("Tavotto") and vals.get(
                "InstallLocation"
            ):
                vals["InstallLocation"] = str(vals["InstallLocation"]).strip('"')
                out.append(vals)
    return out


def install_package(setup: Path) -> dict:
    subprocess.run([str(setup), "/S"], capture_output=True, timeout=900)
    found = _hkcu_installs()
    if not found:
        return {"ok": False}
    inst = Path(found[-1]["InstallLocation"])
    shells = [p for p in inst.glob("*.exe") if p.name.lower().startswith("tavotto")]
    return {
        "ok": bool(shells),
        "shell": str(shells[0]) if shells else None,
        "version": found[-1].get("DisplayVersion"),
        "name": found[-1].get("DisplayName"),
    }


def uninstall_all() -> bool:
    for ent in _hkcu_installs():
        u = Path(ent["InstallLocation"]) / "uninstall.exe"
        if u.exists():
            subprocess.run([str(u), "/S"], capture_output=True, timeout=600)
            for _ in range(120):  # NSIS 卸载器会从 %TEMP% 重新拉起自己：等目录真的没了
                if not u.exists():
                    break
                time.sleep(0.5)
    return not _hkcu_installs()


def webview2_policy_ok(shell: Path) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WEBVIEW2_POLICY_KEY) as k:
            value = winreg.QueryValueEx(k, shell.name)[0]
    except OSError:
        return False
    return f"--remote-debugging-port={DEBUG_PORT}" in str(value)


def ensure_msedgedriver(lab: Lab, env: dict) -> tuple[Path | None, str]:
    """与本机 WebView2 Runtime 同版本的 msedgedriver（微软下载点直连），核 Authenticode 与版本号后缓存。"""
    import winreg
    import zipfile

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WEBVIEW2_RT_KEY) as k:
            rt = str(winreg.QueryValueEx(k, "pv")[0])
    except OSError:
        return None, "cannot read the WebView2 Runtime version"
    if not re.fullmatch(r"\d+(\.\d+){3}", rt):
        return None, f"odd WebView2 version {rt!r}"
    d = lab.cache / "msedgedriver" / rt
    drv = d / "msedgedriver.exe"
    if not drv.exists():
        d.mkdir(parents=True, exist_ok=True)
        z = d / "edgedriver_win64.zip"
        with _opener("direct").open(
            f"https://msedgedriver.microsoft.com/{rt}/edgedriver_win64.zip", timeout=300
        ) as r:
            z.write_bytes(r.read())
        with zipfile.ZipFile(z) as zf:
            zf.extract("msedgedriver.exe", d)
        z.unlink()
    pwsh = r"C:\Program Files\PowerShell\7\pwsh.exe"
    sig = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-c",
            f"$s=Get-AuthenticodeSignature '{drv}'; \"$($s.Status)|$($s.SignerCertificate.Subject)\"",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout.strip()
    if not (sig.startswith("Valid|") and "O=Microsoft Corporation" in sig):
        drv.unlink(missing_ok=True)
        return None, f"msedgedriver signature check failed: {sig[:120]}"
    ver = _tool_version([str(drv), "--version"]) or ""
    if rt not in ver:
        return None, f"msedgedriver {ver!r} does not match WebView2 {rt}"
    return drv, rt


def run_desktop_window_task(lab: Lab, run_id: str, run_dir: Path, env: dict, package: str) -> dict:
    setup, err = package_path(lab, package)
    if err:
        return {"result": "environment_unavailable", "summary": err}
    py = lab.venv_python()
    if not py.exists():
        return {
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    drv, rt = ensure_msedgedriver(lab, env)
    if drv is None:
        return {"result": "environment_unavailable", "summary": rt}
    uninstall_all()
    inst = install_package(setup)
    if not inst.get("ok"):
        return {
            "result": "error",
            "summary": "silent install did not produce a shell",
            "install": inst,
        }
    shell = Path(inst["shell"])
    try:
        if not webview2_policy_ok(shell):
            return {
                "result": "environment_unavailable",
                "summary": f"HKLM WebView2 debug-port policy missing for {shell.name}: run `winlab.py admin gui-setup`",
            }
        junit = run_dir / "evidence" / "junit.xml"
        genv = dict(env)
        genv.update(
            {
                "TAVOTTO_DESKTOP_EXE": str(shell),
                "TAVOTTO_MSEDGEDRIVER": str(drv),
                "TAVOTTO_DESKTOP_WINDOW_REQUIRED": "1",
                "TAVOTTO_WEBVIEW2_DEBUG_PORT": str(DEBUG_PORT),
                "TAVOTTO_DW_ARTIFACTS": str(run_dir / "evidence" / "desktop-window"),
                # 与 nightly 那一步同：失败信息与 xfail 理由是中文，别让代码页把它们转成 \uXXXX
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": str(lab.repo / "src"),
                "TEMP": str(run_dir / "work" / "tmp"),
                "TMP": str(run_dir / "work" / "tmp"),
            }
        )
        (run_dir / "work" / "tmp").mkdir(parents=True, exist_ok=True)
        argv = [
            str(py),
            "-m",
            "pytest",
            "tests/desktop_windows",
            "-p",
            "no:cacheprovider",
            "-v",
            "-rA",
            f"--junitxml={junit}",
        ]
        step = run_in_gui(lab, run_id, argv, lab.repo, genv, run_dir / "evidence" / "pytest", 1800)
    finally:
        uninstall_all()
    if step.get("gui") == "no_interactive_session":
        return {
            "result": "environment_unavailable",
            "summary": "no interactive session for tavotto-lab (autologon off, or someone RDP'd in as it): run `winlab.py admin gui-setup` and reboot",
        }
    if step.get("gui") != "ran":
        return {"result": "error", "summary": f"gui bridge: {step.get('gui')}", "step": step}
    if not step.get("session_id"):
        # 会话 0 = 服务会话，没有用户桌面：那就不是真窗口验收
        return {
            "result": "error",
            "summary": f"gui host ran in session {step.get('session_id')}, not an interactive one",
            "step": step,
        }
    if step.get("timed_out"):
        return {"result": "error", "summary": "desktop window tests timed out", "step": step}
    result, summary, counts = av.judge_pytest(step, junit)
    return {
        "result": result,
        "summary": summary,
        "counts": counts,
        "step": step,
        "webview2": rt,
        "package": {"name": package, "version": inst.get("version"), "display": inst.get("name")},
        "session_id": step.get("session_id"),
        "failures": junit_failures(junit, lab.root),
    }


# ----------------------------------------------------------------------------- 桌面宿主 L2
@dataclass(frozen=True)
class DesktopHost:
    """一个桌面宿主的固定版本安装包。URL 与 SHA-256 取自 microsoft/winget-pkgs 的清单（经评审的公开来源），
    Lab 上下载后先核散列、再核 Authenticode；升级版本 = 改这里、走 PR。"""

    id: str
    name: str
    version: str
    #: 登记在 HKCU 卸载项里的 DisplayVersion（Lab 实测）；与 URL 里的 `version` 可以不同（TRAE 的构建号 ≠ 3.4.4）
    display_version: str
    url: str
    sha256: str
    #: 下载走哪条线路：国内 CDN 直连；海外 CDN 经 DMIT（用户 10-25 授权，只限这些安装包、一次一个）
    network: str
    #: inno | nsis | squirrel —— 决定静默安装 / 卸载参数
    installer: str
    #: HKCU 卸载项 DisplayName 的匹配（不区分大小写）
    display_re: str
    #: 主程序文件名（DisplayIcon 不指向 .exe 时在安装目录里找它）
    exe_name: str
    #: VS Code 系：用独立的 --user-data-dir / --extensions-dir 启动，不碰账户的真实配置
    vscode_like: bool = False


_INNO_SILENT = (
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/SP-",
    "/CURRENTUSER",
    "/MERGETASKS=!runcode",
)
DESKTOP_HOSTS: dict[str, DesktopHost] = {
    h.id: h
    for h in (
        DesktopHost(
            id="vscode",
            name="Visual Studio Code",
            version="1.141.2",
            display_version="1.141.2",
            url="https://vscode.download.prss.microsoft.com/dbazure/download/stable/16507d91be0dc1ce4f94c04d4d0430210b8685d9/VSCodeUserSetup-x64-1.141.2.exe",
            sha256="9b9e7305bc5f2d9176dff8263ba146fc7b0d65ce1fb499271fd7746ec53d66ed",
            network="direct",
            installer="inno",
            display_re=r"^Microsoft Visual Studio Code",
            exe_name="Code.exe",
            vscode_like=True,
        ),
        DesktopHost(
            id="cursor",
            name="Cursor",
            version="3.20.14",
            display_version="3.20.14",
            url="https://downloads.cursor.com/production/67ac8ff8cc118b1eddcb5f2de564c20cffcae94d/win32/x64/user-setup/CursorUserSetup-x64-3.20.14.exe",
            sha256="67508e9be8187996f48328f16297b411487a2a74d9acff5625c90e656e7502dd",
            network="trojan",
            installer="inno",
            display_re=r"^Cursor",
            exe_name="Cursor.exe",
            vscode_like=True,
        ),
        DesktopHost(
            id="trae-cn",
            name="TRAE CN",
            version="2.3.93438",
            display_version="3.4.4",
            url="https://lf-cdn.trae.com.cn/obj/trae-com-cn/pkg/app/releases/stable/2.3.93438/win32/TRAE_CN-Setup-x64.exe",
            sha256="518d939b4715bf238fc54e2bd9f0373e2602e0fd3f69af2e812a0cbe157ef20f",
            network="direct",
            installer="inno",
            display_re=r"^Trae CN|^TRAE CN",
            exe_name="Trae CN.exe",
            vscode_like=True,
        ),
        DesktopHost(
            id="workbuddy",
            name="WorkBuddy",
            version="5.8.3",
            display_version="5.8.3",
            url="https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.8.3.41334564-db0e9b51.exe",
            sha256="824de10f3196c501004f540eb84ee881292fef2f5df9fb3fc48af9ea5366c03c",
            network="direct",
            installer="nsis",
            display_re=r"^WorkBuddy",
            exe_name="WorkBuddy.exe",
        ),
        DesktopHost(
            id="zcode",
            name="ZCode",
            version="3.14.1",
            display_version="3.14.1",
            url="https://cdn-zcode.z.ai/zcode/electron/releases/3.14.1/windows-x64/ZCode-3.14.1-win-x64.exe",
            sha256="f5216f6df1d5f5cf69d55d732c64927533fe9eeb31e8b39bed2493fac09ed9a0",
            network="trojan",
            installer="nsis",
            display_re=r"^ZCode",
            exe_name="ZCode.exe",
        ),
        DesktopHost(
            id="claude-desktop",
            name="Claude Desktop",
            version="2.42304.2",
            display_version="2.42304.2",
            url="https://downloads.claude.ai/releases/win32/x64/2.42304.2/Claude-af65c0e384c1657459441aac3083c6e622789afe.exe",
            sha256="9eff78c6fd1fde2035806401bd3933578842d9a377b76dacfdb7b14571a002b0",
            network="trojan",
            installer="squirrel",
            display_re=r"^Claude",
            exe_name="claude.exe",
        ),
    )
}
assert tuple(DESKTOP_HOSTS) == DESKTOP_HOST_IDS
#: 单个安装包的上限：超过就停（防止误把别的东西当安装包拖下来）
DESKTOP_MAX_BYTES = 600 * 1024**2


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _fetch_pinned_file(
    d: Path,
    url: str,
    sha256: str,
    network: str,
    max_bytes: int,
    ca_pem: str | None = None,
    cafile: str | None = None,
) -> tuple[Path | None, dict]:
    """下载（或复用缓存）一个固定散列的文件到目录 d。代理线路不通就 environment_unavailable，绝不改走直连；
    超过体积上限即停，散列不符即删。"""
    dest = d / url.rsplit("/", 1)[-1]
    info: dict = {"network": network, "cached": False}
    if dest.exists() and _file_sha256(dest) == sha256:
        info["cached"] = True
        return dest, info
    if network != "direct" and not _tcp_open("127.0.0.1", PROXY_PORTS[network]):
        info["unavailable"] = f"proxy {network} not listening"
        return None, info
    d.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "tavotto-winlab/1"})
    try:
        extra = {k: v for k, v in (("ca_pem", ca_pem), ("cafile", cafile)) if v}
        opener = _opener(network, **extra)
        info["trust_source"] = "certifi" if cafile else "pinned-root" if ca_pem else "system"
        with opener.open(req, timeout=60) as r, open(part, "wb") as f:
            total = 0
            while chunk := r.read(1 << 20):
                total += len(chunk)
                if total > max_bytes:
                    raise OSError(f"larger than {max_bytes} bytes")
                h.update(chunk)
                f.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        part.unlink(missing_ok=True)
        info["unavailable"] = f"download failed: {type(exc).__name__}: {exc}"[:300]
        return None, info
    info.update(bytes=total, seconds=round(time.monotonic() - t0, 1))
    if h.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        info["sha256_mismatch"] = h.hexdigest()
        return None, info
    os.replace(part, dest)
    return dest, info


def fetch_pinned(lab: Lab, host: DesktopHost) -> tuple[Path | None, dict]:
    """下载（或复用缓存）固定版本的安装包并核 SHA-256（规矩见 `_fetch_pinned_file`）。"""
    return _fetch_pinned_file(
        lab.cache / "desktop-hosts" / host.id / host.version,
        host.url,
        host.sha256,
        host.network,
        DESKTOP_MAX_BYTES,
    )


@dataclass(frozen=True)
class ExtensionPin:
    """宿主里的 AI 扩展包（.vsix）：与安装包同规矩——固定版本、固定散列、经 DMIT、一次一个。"""

    id: str  # 发布者.名字（小写，VS Code 的扩展 id）
    version: str
    url: str
    #: Open VSX 公布的 sha256（<包>.sha256），与这里逐字一致才可改动本表
    sha256: str
    network: str
    max_bytes: int
    #: 装好后 --extensions-dir 里的目录名
    dir_name: str
    #: 服务器链的根不在 Lab 的 Windows 根存储里时，多信的那一个根（PEM；散列钉死，见 EXTENSION_CA_SHA256）
    extra_ca_pem: str = ""


#: Open VSX（open-vsx.org 与它重定向到的 openvsx.eclipsecontent.org）的证书链止于 Starfield Root Certificate
#: Authority - G2。Lab 在中国大陆，Windows 的受信任根是按需从微软下发的，这台机器的 ROOT 存储里没有它
#: （Python 的 ssl 不会触发按需下载），于是 `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`
#: （2026-10-26 实测）。解法是只多信这一个**钉死散列**的公开根（SHA-256 与 EXTENSION_CA_SHA256 对得上才用），
#: 其余校验照旧；TLS 校验不关。
STARFIELD_ROOT_G2_PEM = """-----BEGIN CERTIFICATE-----
MIID3TCCAsWgAwIBAgIBADANBgkqhkiG9w0BAQsFADCBjzELMAkGA1UEBhMCVVMx
EDAOBgNVBAgTB0FyaXpvbmExEzARBgNVBAcTClNjb3R0c2RhbGUxJTAjBgNVBAoT
HFN0YXJmaWVsZCBUZWNobm9sb2dpZXMsIEluYy4xMjAwBgNVBAMTKVN0YXJmaWVs
ZCBSb290IENlcnRpZmljYXRlIEF1dGhvcml0eSAtIEcyMB4XDTA5MDkwMTAwMDAw
MFoXDTM3MTIzMTIzNTk1OVowgY8xCzAJBgNVBAYTAlVTMRAwDgYDVQQIEwdBcml6
b25hMRMwEQYDVQQHEwpTY290dHNkYWxlMSUwIwYDVQQKExxTdGFyZmllbGQgVGVj
aG5vbG9naWVzLCBJbmMuMTIwMAYDVQQDEylTdGFyZmllbGQgUm9vdCBDZXJ0aWZp
Y2F0ZSBBdXRob3JpdHkgLSBHMjCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoC
ggEBAL3twQP89o/8ArFvW59I2Z154qK3A2FWGMNHttfKPTUuiUP3oWmb3ooa/RMg
nLRJdzIpVv257IzdIvpy3Cdhl+72WoTsbhm5iSzchFvVdPtrX8WJpRBSiUZV9Lh1
HOZ/5FSuS/hVclcCGfgXcVnrHigHdMWdSL5stPSksPNkN3mSwOxGXn/hbVNMYq/N
Hwtjuzqd+/x5AJhhdM8mgkBj87JyahkNmcrUDnXMN/uLicFZ8WJ/X7NfZTD4p7dN
dloedl40wOiWVpmKs/B/pM293DIxfJHP4F8R+GuqSVzRmZTRouNjWwl2tVZi4Ut0
HZbUJtQIBFnQmA4O5t78w+wfkPECAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAO
BgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFHwMMh+n2TB/xH1oo2Kooc6rB1snMA0G
CSqGSIb3DQEBCwUAA4IBAQARWfolTwNvlJk7mh+ChTnUdgWUXuEok21iXQnCoKjU
sHU48TRqneSfioYmUeYs0cYtbpUgSpIB7LiKZ3sx4mcujJUDJi5DnUox9g61DLu3
4jd/IroAow57UvtruzvE03lRTs2Q9GcHGcg8RnoNAX3FWOdt5oUwF5okxBDgBPfg
8n/Uqgr/Qh037ZTlZFkSIHc40zI+OIF1lnP6aI+xy84fxez6nH7PfrHxBy22/L/K
pL/QlwVKvOoYKAKQvVR4CSFx09F9HdkWsKlhPdAKACL8x3vLCWRFCztAgfd9fDL1
mMpYjn0q7pBZc2T5NnReJaH1ZgUufzkVqSr7UIuOhWn0
-----END CERTIFICATE-----
"""
EXTENSION_CA_SHA256 = "2ce1cb0bf9d2f9e102993fbe215152c3b2dd0cabde1c68e5319b839154dbb7f5"


def extension_ca_pem(ext: ExtensionPin) -> str | None:
    """扩展下载多信的根：PEM 的 DER 散列必须等于钉死的值，否则不用（下载照常走完整校验、多半会失败）。"""
    if not ext.extra_ca_pem:
        return None
    der = ssl.PEM_cert_to_DER_cert(ext.extra_ca_pem)
    return ext.extra_ca_pem if hashlib.sha256(der).hexdigest() == EXTENSION_CA_SHA256 else None


DESKTOP_EXTENSIONS: dict[str, ExtensionPin] = {
    e.id: e
    for e in (
        ExtensionPin(
            id="anthropic.claude-code",
            version="2.1.310",
            url="https://open-vsx.org/api/Anthropic/claude-code/win32-x64/2.1.310/file/Anthropic.claude-code-2.1.310@win32-x64.vsix",
            sha256="ebb9f5016589f0a654ed330fb488dcd42f0f64456216b63ab8242104487cd39c",
            network="trojan",
            max_bytes=400 * 1024**2,
            dir_name="anthropic.claude-code-2.1.310-win32-x64",
            extra_ca_pem=STARFIELD_ROOT_G2_PEM,
        ),
    )
}


def certifi_cafile(lab: Lab) -> str | None:
    """venv 里 certifi 的 CA 包（它含 Starfield Root CA G2）：有就优先用，只作用于固定散列的扩展下载。"""
    p = lab.venv_python().parent.parent / "Lib" / "site-packages" / "certifi" / "cacert.pem"
    return str(p) if p.is_file() else None


def fetch_extension(lab: Lab, ext: ExtensionPin) -> tuple[Path | None, dict]:
    cafile = certifi_cafile(lab)
    return _fetch_pinned_file(
        lab.cache / "desktop-extensions" / ext.id / ext.version,
        ext.url,
        ext.sha256,
        ext.network,
        ext.max_bytes,
        ca_pem=None if cafile else extension_ca_pem(ext),
        cafile=cafile,
    )


def authenticode(path: Path) -> dict:
    pwsh = r"C:\Program Files\PowerShell\7\pwsh.exe"
    out = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-c",
            f"$s=Get-AuthenticodeSignature -LiteralPath '{path}'; \"$($s.Status)|$($s.SignerCertificate.Subject)\"",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout.strip()
    status, _, subject = out.partition("|")
    org = re.search(r"O=(\"[^\"]+\"|[^,]+)", subject)
    return {"status": status, "org": org.group(1).strip('"') if org else None}


def _uninstall_entries(display_re: str) -> list[dict]:
    import winreg

    rx = re.compile(display_re, re.I)
    out = []
    base = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return out
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(root, name) as k:
                vals = {"key": name}
                for v in (
                    "DisplayName",
                    "DisplayVersion",
                    "InstallLocation",
                    "DisplayIcon",
                    "UninstallString",
                    "QuietUninstallString",
                ):
                    with contextlib.suppress(OSError):
                        vals[v] = str(winreg.QueryValueEx(k, v)[0])
            if rx.search(vals.get("DisplayName", "")):
                out.append(vals)
    return out


def _host_exe(host: DesktopHost, ent: dict) -> Path | None:
    """主程序：DisplayIcon 就是 .exe 时直接用；否则在 InstallLocation、图标所在目录、卸载器所在目录里找
    `exe_name`——electron-builder 的 NSIS 包（ZCode）InstallLocation 留空、DisplayIcon 指向 .ico。"""
    icon = ent.get("DisplayIcon", "").split(",")[0].strip().strip('"')
    if icon.lower().endswith(".exe") and Path(icon).exists():
        return Path(icon)
    dirs = [ent.get("InstallLocation", "").strip().strip('"')]
    if icon:
        dirs.append(str(Path(icon).parent))
    for key in ("QuietUninstallString", "UninstallString"):
        if ent.get(key):
            dirs.append(str(Path(_command_argv(ent[key])[0]).parent))
    for d in dirs:
        if d and (Path(d) / host.exe_name).exists():
            return Path(d) / host.exe_name
    return None


def _command_argv(cmd: str) -> list[str]:
    """卸载项里的命令行：开头一段是程序（带不带引号都有），其余按空白切。"""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        exe, _, rest = cmd[1:].partition('"')
    else:
        m = re.match(r"(.+?\.exe)(.*)$", cmd, re.I)
        exe, rest = (m.group(1), m.group(2)) if m else (cmd, "")
    return [exe, *rest.split()]


def _run_until(argv: list[str], cwd: Path, env: dict, log: Path, done, timeout: float) -> dict:
    """跑安装 / 卸载器：主进程退出**且** `done()` 成立才算完——NSIS 卸载器会把自己拷进 %TEMP% 再起一份，
    主进程一退就关 Job 会把真正干活的那份杀掉。主进程**非 0** 退出就是失败，立刻返回，不等到超时。"""
    t0 = time.monotonic()
    with open(f"{log}.log", "wb") as out:
        tree = ProcessTree(argv, cwd=cwd, env=env, stdout=out, stderr=out)
        try:
            while time.monotonic() - t0 < timeout:
                rc = tree.proc.poll()
                if rc not in (None, 0):
                    return {"exit_code": rc, "seconds": round(time.monotonic() - t0, 1)}
                if rc is not None and done():
                    return {
                        "exit_code": tree.proc.returncode,
                        "seconds": round(time.monotonic() - t0, 1),
                    }
                time.sleep(1)
            return {"exit_code": tree.proc.poll(), "timed_out": True}
        finally:
            tree.close()


def install_desktop_host(host: DesktopHost, setup: Path, work: Path, env: dict) -> dict:
    args = {
        "inno": list(_INNO_SILENT),
        "nsis": ["/S", "/currentuser"],
        "squirrel": ["--silent"],
    }[host.installer]
    found: dict = {}

    def done() -> bool:
        # 只认登记版本等于固定版本的那一条：残留的旧版本不算装好
        for ent in reversed(_uninstall_entries(host.display_re)):
            if ent.get("DisplayVersion") != host.display_version:
                continue
            exe = _host_exe(host, ent)
            if exe:
                found.update(entry=ent, exe=exe)
                return True
        return False

    step = _run_until([str(setup), *args], work, env, work / "install", done, 900)
    registered = [e.get("DisplayVersion") for e in _uninstall_entries(host.display_re)]
    if not found or step.get("exit_code") != 0 or step.get("timed_out"):
        return {"ok": False, "step": step, "registered_versions": registered}
    ent = found["entry"]
    return {
        "ok": True,
        "step": step,
        "exe": str(found["exe"]),
        "root": str(found["exe"].parent),
        "display": ent.get("DisplayName"),
        "version": ent.get("DisplayVersion"),
    }


def uninstall_desktop_host(host: DesktopHost, work: Path, env: dict) -> dict:
    flags = {
        "inno": ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        "nsis": ["/S", "/currentuser"],
        "squirrel": ["-s"],
    }[host.installer]
    steps = []
    for ent in _uninstall_entries(host.display_re):
        cmd = ent.get("QuietUninstallString") or ent.get("UninstallString")
        if not cmd:
            continue
        argv = _command_argv(cmd)
        if not ent.get("QuietUninstallString"):
            argv += [f for f in flags if f.lower() not in (a.lower() for a in argv)]
        key = ent["key"]
        steps.append(
            _run_until(
                argv,
                work,
                env,
                work / f"uninstall-{len(steps)}",
                lambda key=key: (
                    not any(e["key"] == key for e in _uninstall_entries(host.display_re))
                ),
                600,
            )
        )
    steps_ok = all(not st.get("timed_out") and st.get("exit_code") == 0 for st in steps)
    return {"ok": steps_ok and not _uninstall_entries(host.display_re), "steps": steps}


def _cleanup_note(base: dict) -> str:
    return "" if base.get("uninstall", {}).get("ok", True) else "；清理失败：最终卸载未卸干净"


def _install_failure_summary(host: DesktopHost, inst: dict) -> str:
    step = inst.get("step", {})
    if step.get("timed_out"):
        return "silent install timed out"
    if step.get("exit_code") not in (0, None):
        return f"installer exited with code {step['exit_code']}"
    seen = [v for v in inst.get("registered_versions", []) if v]
    if seen and host.display_version not in seen:
        return f"installed version {', '.join(seen)} != pinned {host.display_version}"
    return "silent install did not register the host"


def run_desktop_hosts_task(lab: Lab, run_id: str, run_dir: Path, env: dict, mode: str) -> dict:
    host = DESKTOP_HOSTS[mode]
    levels = {"L2": "not_tested", "L3": "blocked", "L4": "blocked", "L5": "blocked"}
    base = {
        "host": {"id": host.id, "name": host.name, "pinned_version": host.version, "url": host.url},
        "levels": levels,
        "blocked_reason": "L3+ 要在宿主里登录各自的账户并配置 MCP（Lab 没有这些账户）",
    }
    setup, dl = fetch_pinned(lab, host)
    base["download"] = dl
    if setup is None:
        if "sha256_mismatch" in dl:
            return {
                **base,
                "result": "error",
                "summary": "installer SHA-256 does not match the pin",
            }
        return {
            **base,
            "result": "environment_unavailable",
            "summary": dl.get("unavailable", "download failed"),
        }
    sig = authenticode(setup)
    base["signature"] = sig
    if sig["status"] != "Valid":
        return {
            **base,
            "result": "error",
            "summary": f"installer Authenticode status {sig['status']!r}",
        }
    py = lab.venv_python()
    if not py.exists():
        return {
            **base,
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    work = run_dir / "work"
    ev = run_dir / "evidence" / "desktop-host"
    work.mkdir(parents=True, exist_ok=True)
    ev.mkdir(parents=True, exist_ok=True)
    henv = network_env("direct", env)
    henv.update({"TEMP": str(work), "TMP": str(work)})
    base["preexisting_uninstall"] = uninstall_desktop_host(host, work, henv)
    if not base["preexisting_uninstall"]["ok"]:
        return {
            **base,
            "result": "environment_unavailable",
            "summary": "残留的旧安装卸不掉",
        }
    inst = install_desktop_host(host, setup, work, henv)
    base["install"] = {k: v for k, v in inst.items() if k != "root"}
    if not inst["ok"]:
        levels["L2"] = "failed"
        base["uninstall"] = uninstall_desktop_host(host, work, henv)  # 装了一半的也收掉，结果进报告
        return {
            **base,
            "result": "failed",
            "summary": _install_failure_summary(host, inst) + _cleanup_note(base),
        }
    try:
        extra = []
        if host.vscode_like:
            prof = run_dir / "work" / "profile"
            extra = [
                f"--user-data-dir={prof / 'data'}",
                f"--extensions-dir={prof / 'ext'}",
                "--skip-welcome",
                "--disable-workspace-trust",
                "--new-window",
            ]
        probe = Path(__file__).resolve().parent / "window_probe.py"
        out = ev / "window.json"
        argv = [
            str(py),
            "-I",
            str(probe),
            "--exe",
            inst["exe"],
            "--root",
            inst["root"],
            "--out",
            str(out),
            "--shot",
            str(ev / "window.png"),
            "--",
            *extra,
        ]
        step = run_in_gui(lab, run_id, argv, work, henv, ev / "probe", 240)
    finally:
        base["uninstall"] = uninstall_desktop_host(host, work, henv)
    if step.get("gui") == "no_interactive_session":
        return {
            **base,
            "result": "environment_unavailable",
            "summary": "no interactive session for tavotto-lab: run `winlab.py admin gui-setup` and reboot"
            + _cleanup_note(base),
        }
    if step.get("gui") != "ran" or not step.get("session_id"):
        return {
            **base,
            "result": "error",
            "summary": f"gui bridge: {step.get('gui')} session={step.get('session_id')}"
            + _cleanup_note(base),
            "step": step,
        }
    try:
        win = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            **base,
            "result": "error",
            "summary": "window probe wrote no result" + _cleanup_note(base),
            "step": step,
        }
    base["window"] = win
    base["session_id"] = step["session_id"]
    if win.get("window"):
        levels["L2"] = "passed"
        seen = f"{host.name} {inst.get('version')}: main window {win['window']['title']!r} in session {step['session_id']} after {win['seconds_to_window']} s"
        if not base["uninstall"]["ok"]:
            # 常驻机器上留着残余安装会污染后续运行：L2 如实记 passed，但整次不报 passed
            return {**base, "result": "error", "summary": f"L2 通过但清理失败：{seen}"}
        return {**base, "result": "passed", "summary": seen}
    levels["L2"] = "failed"
    return {
        **base,
        "result": "failed",
        "summary": f"{host.name} started but no main window within the probe timeout"
        + _cleanup_note(base),
    }


# ----------------------------------------------------------------------------- 桌面宿主 L3–L5（desktop-live）
@dataclass(frozen=True)
class DesktopLive:
    """宿主 × 扩展的一格：宿主见 DESKTOP_HOSTS，扩展见 DESKTOP_EXTENSIONS。"""

    host: str
    extension: str


DESKTOP_LIVE: dict[str, DesktopLive] = {
    "vscode-claude": DesktopLive("vscode", "anthropic.claude-code"),
}
assert tuple(DESKTOP_LIVE) == DESKTOP_LIVE_MODES
#: 宿主装扩展时自报「版本不兼容」：这是宿主限制（not_supported），不是 Tavotto 的故障
EXT_NOT_COMPATIBLE_RE = re.compile(
    r"not compatible with|incompatible with|requires (?:vs ?code|visual studio code)|engine", re.I
)
#: 驱动器（交互会话一侧）静默多久、最长等多久；首个 MCP 流量最晚多久必须出现
DRIVER_QUIET_S = 45
DRIVER_MAX_S = 1200
DRIVER_FIRST_TRAFFIC_S = 300
PANEL_TAIL_CHARS = 2000


def _tail_text(path: Path, n: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


def host_provenance(path: Path, start_line: int = 0, end_line: int | None = None) -> dict:
    """录制器记下的**首个** initialize：clientInfo 与父进程链。`from_host` = 祖先里有桌面宿主安装目录下的程序
    （录制器带 --host-root 才会写 host_ancestor）。没有 initialize 记录就是 seen=False、from_host=False。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[start_line:end_line]
    except OSError:
        lines = []
    for line in lines:
        with contextlib.suppress(ValueError):
            rec = json.loads(line)
            if rec.get("dir") == "host" and rec.get("method") == "initialize":
                anc = rec.get("host_ancestor")
                return {
                    "seen": True,
                    "client": rec.get("client") or {},
                    "ancestry": list(rec.get("ancestry") or []),
                    "host_ancestor": Path(anc).name if anc else None,
                    "from_host": bool(anc),
                }
    return {"seen": False, "from_host": False}


def judge_desktop_live(
    s1: dict,
    s2: dict,
    pdf: Path | None,
    journal_ok: bool,
    sid: str | None,
    prov1: dict,
    prov2: dict,
) -> dict:
    """judge_live 一字不改地下 L4 / L5 的结论；桌面这里只在它**之外**加前置条件：
    L3 还要求流量来自桌面宿主的进程树，否则 failed——此时 L4 / L5 的证据不能算「宿主里的扩展」做的，
    一并 failed（原因写进 provenance）；第二轮同理，凭「全新的宿主进程」恢复的 L5 要求第二轮也来自宿主。"""
    levels = judge_live(s1, s2, pdf, journal_ok, sid)
    listed = "tavotto_open_figure" in s1["listed_tools"]
    levels["L3"] = "passed" if listed and prov1.get("from_host") else "failed"
    if levels["L3"] == "failed" and listed:
        levels["L4"] = levels["L5"] = "failed"
    elif not prov2.get("from_host") and levels["L5"] == "passed":
        levels["L5"] = "failed"
    levels["provenance"] = {"session_1": prov1, "session_2": prov2}
    return levels


def key_placeholder(model_conf: dict) -> str:
    return str(model_conf.get("api_key") or "\0")


def _json_merge_file(path: Path, patch: dict) -> None:
    """把 patch 浅合并进一个 JSON 文件（不存在就新建）。"""
    cur: dict = {}
    with contextlib.suppress(OSError, ValueError):
        cur = json.loads(path.read_text(encoding="utf-8"))
    cur.update(patch)
    path.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def vscode_user_settings(login_prompt_off: bool = True) -> dict:
    """隔离 user-data-dir 里的 settings.json：只放行为开关，**没有任何密钥**（模型凭据只在进程环境里）。"""
    return {
        "telemetry.telemetryLevel": "off",
        "update.mode": "none",
        "extensions.autoUpdate": False,
        "extensions.autoCheckUpdates": False,
        "workbench.startupEditor": "none",
        "workbench.tips.enabled": False,
        "security.workspace.trust.enabled": False,
        "window.restoreWindows": "none",
        "claudeCode.disableLoginPrompt": login_prompt_off,
    }


#: 桌面面板里没人点权限弹窗：会弹窗的工具一律事先定好。放行 tavotto 与只读工具（open_figure 的结果超过
#: 扩展的 MCP 输出上限时会落成文件，模型要能 Read 回来）；会改东西 / 跑命令 / 上网的直接拒绝——
#: 拒绝会立刻回给模型，不会像弹窗那样一直挂着（run 20261026T132012Z-44023f 卡在 Bash 弹窗）。
DESKTOP_CLAUDE_PERMISSIONS = {
    "allow": ["mcp__tavotto", "Read", "Glob", "Grep"],
    "deny": [
        "Bash",
        "PowerShell",
        "Write",
        "Edit",
        "NotebookEdit",
        "WebFetch",
        "WebSearch",
        "Task",
    ],
}


def claude_isolated_config(cfg: Path, proj: Path) -> None:
    """隔离的 CLAUDE_CONFIG_DIR：预先放行 mcp__tavotto（只这一个服务器，不是放开全部），跳过首次引导，信任项目目录。
    `claude mcp add` 已经在这里写好 .claude.json 的 mcpServers，这里只合并其它键。"""
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text(
        json.dumps({"permissions": DESKTOP_CLAUDE_PERMISSIONS}, indent=2), encoding="utf-8"
    )
    _json_merge_file(
        cfg / ".claude.json",
        {"hasCompletedOnboarding": True},
    )
    state = json.loads((cfg / ".claude.json").read_text(encoding="utf-8"))
    projects = state.setdefault("projects", {})
    for key in {str(proj), proj.as_posix()}:
        projects.setdefault(key, {}).update(hasTrustDialogAccepted=True)
    (cfg / ".claude.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def driver_argv(
    py: Path,
    driver: Path,
    *,
    exe: str,
    root: str,
    profile: Path,
    ext_dir: Path,
    workspace: Path,
    prompt_file: Path,
    rec_log: Path,
    out_dir: Path,
    label: str,
) -> list[str]:
    """交互会话里跑驱动器的 argv（登记任务的固定形状；没有任何透传）。"""
    return [
        str(py),
        "-I",
        str(driver),
        "--exe",
        exe,
        "--root",
        root,
        "--user-data-dir",
        str(profile / "data"),
        "--extensions-dir",
        str(ext_dir),
        "--workspace",
        str(workspace),
        "--prompt-file",
        str(prompt_file),
        "--rec-log",
        str(rec_log),
        "--out-dir",
        str(out_dir),
        "--label",
        label,
        "--quiet-s",
        str(DRIVER_QUIET_S),
        "--max-s",
        str(DRIVER_MAX_S),
        "--first-traffic-s",
        str(DRIVER_FIRST_TRAFFIC_S),
    ]


def run_desktop_live_task(
    lab: Lab, run_id: str, run_dir: Path, env: dict, model_conf: dict, mode: str
) -> dict:
    cell = DESKTOP_LIVE[mode]
    host = DESKTOP_HOSTS[cell.host]
    ext = DESKTOP_EXTENSIONS[cell.extension]
    levels = {"L2": "not_tested", "L3": "not_run", "L4": "not_run", "L5": "not_run"}
    base: dict = {
        "host": {"id": host.id, "name": host.name, "pinned_version": host.version},
        "extension": {"id": ext.id, "pinned_version": ext.version, "sha256": ext.sha256},
        "model": {
            "provider": model_conf.get("provider"),
            "model": model_conf.get("model")
            or DEFAULT_HOST_MODEL,  # 与 _host_model_env 实际传给宿主的同一个
        },
        "levels": levels,
    }
    py = lab.venv_python()
    if not py.exists():
        return {
            **base,
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    setup, dl = fetch_pinned(lab, host)
    base["download"] = {"host": dl}
    if setup is None:
        res = "error" if "sha256_mismatch" in dl else "environment_unavailable"
        return {**base, "result": res, "summary": dl.get("unavailable", "host installer rejected")}
    vsix, xl = fetch_extension(lab, ext)
    base["download"]["extension"] = xl
    if vsix is None:
        if "sha256_mismatch" in xl:
            return {
                **base,
                "result": "error",
                "summary": "extension package SHA-256 does not match the pin",
            }
        return {**base, "result": "environment_unavailable", "summary": xl.get("unavailable", "")}
    sig = authenticode(setup)
    base["signature"] = sig
    if sig["status"] != "Valid":
        return {
            **base,
            "result": "error",
            "summary": f"installer Authenticode status {sig['status']!r}",
        }
    work = run_dir / "work"
    ev = run_dir / "evidence" / "desktop-live"
    work.mkdir(parents=True, exist_ok=True)
    ev.mkdir(parents=True, exist_ok=True)
    henv = network_env("direct", env)
    henv.update({"TEMP": str(work), "TMP": str(work)})
    base["preexisting_uninstall"] = uninstall_desktop_host(host, work, henv)
    if not base["preexisting_uninstall"]["ok"]:
        return {**base, "result": "environment_unavailable", "summary": "残留的旧安装卸不掉"}
    inst = install_desktop_host(host, setup, work, henv)
    base["install"] = {k: v for k, v in inst.items() if k != "root"}
    if not inst["ok"]:
        levels["L2"] = "failed"
        base["uninstall"] = uninstall_desktop_host(host, work, henv)
        return {
            **base,
            "result": "failed",
            "summary": _install_failure_summary(host, inst) + _cleanup_note(base),
        }
    ext_dir = (
        run_dir / "ext"
    )  # 扩展目录放在 work 之外：里面有 300 MB 的随包二进制，不参与抹密钥扫描
    try:
        out = _desktop_live_body(
            lab, run_id, run_dir, henv, model_conf, cell, host, ext, vsix, inst, ext_dir, base
        )
    finally:
        base["uninstall"] = uninstall_desktop_host(host, work, henv)
        if ext_dir.exists():
            _rmtree_no_follow(ext_dir)
    out["uninstall"] = base["uninstall"]
    if not base["uninstall"]["ok"]:
        out["summary"] = str(out.get("summary", "")) + _cleanup_note(base)
        if out.get("result") == "passed":
            out["result"] = "error"  # 常驻机器上留着残余安装会污染后续运行
    return out


def _desktop_live_body(
    lab, run_id, run_dir, env, model_conf, cell, host, ext, vsix, inst, ext_dir, base
) -> dict:
    py = lab.venv_python()
    levels = base["levels"]
    work = run_dir / "work"
    ev = run_dir / "evidence" / "desktop-live"
    key = model_conf["api_key"]
    code_cmd = Path(inst["root"]) / "bin" / "code.cmd"
    profile = work / "profile"
    (profile / "data" / "User").mkdir(parents=True, exist_ok=True)
    (profile / "data" / "User" / "settings.json").write_text(
        json.dumps(vscode_user_settings(), indent=2), encoding="utf-8"
    )
    ext_dir.mkdir(parents=True, exist_ok=True)
    install_args = [
        str(code_cmd),
        "--install-extension",
        str(vsix),
        "--force",
        f"--extensions-dir={ext_dir}",
        f"--user-data-dir={profile / 'data'}",
    ]
    step = _run_logged(install_args, work, env, ev / "ext-install", 600)
    log_text = ""
    for f in ev.glob("ext-install.*.log"):
        log_text += f.read_text(encoding="utf-8", errors="replace")
    found = sorted(ext_dir.glob(f"{ext.id}-{ext.version}*")) if ext_dir.is_dir() else []
    installed = found[0] if found else ext_dir / ext.dir_name
    base["extension"]["install_log_tail"] = log_text.replace(
        key_placeholder(model_conf), "<redacted>"
    )[-1500:]
    base["extension"]["extensions_dir_listing"] = sorted(p.name for p in ext_dir.iterdir())[:20]
    claude_exe = installed / "resources" / "native-binary" / "claude.exe"
    base["extension"]["install_exit_code"] = step.get("exit_code")
    if not installed.is_dir() or not claude_exe.is_file():
        if EXT_NOT_COMPATIBLE_RE.search(log_text):
            levels.update(L2="passed", L3="not_supported", L4="not_supported", L5="not_supported")
            return {
                **base,
                "result": "skipped",
                "summary": f"{host.name} {host.version} refused extension {ext.id} {ext.version} as incompatible "
                "(host limitation, not a Tavotto failure; see evidence/desktop-live/ext-install.*.log)",
                "not_supported_reason": "host rejected the extension as incompatible",
            }
        levels["L2"] = "passed"
        return {
            **base,
            "result": "failed",
            "summary": f"extension {ext.id} did not install (exit {step.get('exit_code')})",
        }
    proj, seeded = _seed_live_project(py, run_dir, env, ev)
    if not seeded:
        return {**base, "result": "error", "summary": "seeding the figure script failed"}
    rec_log = ev / "mcp-recorder.jsonl"
    recorder = Path(__file__).resolve().parent / "mcp_recorder.py"
    mcp_cmd = [
        str(py),
        str(recorder),
        str(rec_log),
        "--host-root",
        inst["root"],
        "--",
        str(py),
        str(lab.repo / "codex-plugin" / "mcp" / "server.py"),
    ]
    mcp_env = {"TAVOTTO_MCP_ROOTS": str(proj), "TAVOTTO_NO_TELEMETRY": "1"}
    cfg = work / "claude-config"
    cfg.mkdir(parents=True, exist_ok=True)
    add = [str(claude_exe), "mcp", "add", "tavotto", "--scope", "user"]
    for k, v in mcp_env.items():
        add += ["-e", f"{k}={v}"]
    _run_logged(
        [*add, "--", *mcp_cmd],
        proj,
        {**env, "CLAUDE_CONFIG_DIR": str(cfg)},
        ev / "mcp-add",
        120,
    )
    claude_isolated_config(cfg, proj)
    # 进程环境：隔离配置目录 + 国内 Anthropic 兼容接口。密钥那一项只走命名管道，不进 request.json
    gui_env = dict(env, CLAUDE_CONFIG_DIR=str(cfg), **_host_model_env(model_conf))
    public_env, secret_env = split_secret_env(gui_env, [key])
    driver = Path(__file__).resolve().parent / "desktop_driver.py"

    def one_round(n: int, prompt: str) -> dict:
        pf = work / f"prompt-{n}.txt"
        pf.write_text(prompt, encoding="utf-8")
        argv = driver_argv(
            py,
            driver,
            exe=inst["exe"],
            root=inst["root"],
            profile=profile,
            ext_dir=ext_dir,
            workspace=proj,
            prompt_file=pf,
            rec_log=rec_log,
            out_dir=ev,
            label=f"round{n}",
        )
        step = run_in_gui(
            lab,
            run_id,
            argv,
            work,
            public_env,
            ev / f"driver-{n}",
            DRIVER_MAX_S + 600,
            secret_env=secret_env,
        )
        info: dict = {}
        with contextlib.suppress(OSError, ValueError):
            info = json.loads((ev / f"round{n}.json").read_text(encoding="utf-8"))
        tail = ""
        for f in sorted(ev.glob(f"driver-{n}.*.log")):
            tail += f.read_text(encoding="utf-8", errors="replace")[-1200:]
        progress: list = []
        with contextlib.suppress(OSError):
            for line in (ev / f"round{n}-progress.jsonl").read_text(encoding="utf-8").splitlines():
                with contextlib.suppress(ValueError):
                    progress.append(json.loads(line))
        return {
            "step": step,
            "driver": info,
            "progress": progress,
            "log_tail": tail.replace(key, "<redacted>"),
        }

    r1 = one_round(1, LIVE_SESSION_1)
    if r1["step"].get("gui") == "no_interactive_session":
        return {
            **base,
            "result": "environment_unavailable",
            "summary": "no interactive session for tavotto-lab: run `winlab.py admin gui-setup` and reboot",
        }
    if r1["step"].get("gui") != "ran" or not r1["step"].get("session_id"):
        return {
            **base,
            "result": "error",
            "summary": f"gui bridge: {r1['step'].get('gui')} session={r1['step'].get('session_id')}",
            "step": r1["step"],
        }
    base["session_id"] = r1["step"]["session_id"]
    win = r1["driver"].get("window")
    levels["L2"] = "passed" if win else "failed"
    s1 = parse_recorded(rec_log)
    split = _line_count(rec_log)
    sid = opened_session_id(s1)
    data_dir = Path(env.get("LOCALAPPDATA") or Path.home()) / "Tavotto"
    journal_ok = journal_has_edits(data_dir, sid)
    r2: dict = {"step": {"skipped": "no session id from session 1"}, "driver": {}}
    if sid:
        r2 = one_round(2, LIVE_SESSION_2.format(sid=sid))  # 全新的 VS Code 进程，只拿 session_id
    _scrub(ev, key)
    _scrub(work, key)
    s2 = parse_recorded(rec_log, split)
    base["driver"] = {"session_1": r1["driver"], "session_2": r2["driver"]}
    base["driver_step"] = {"session_1": r1["step"], "session_2": r2["step"]}
    base["driver_progress"] = {"session_1": r1["progress"], "session_2": r2.get("progress", [])}
    base["driver_log_tail"] = r1["log_tail"] if not r1["driver"] else ""
    # 面板文字尾部进报告（证据文件不回传 Mac）：判不出 L5 时能看到模型最后在说什么；已在 _scrub 之后
    base["panel_tail"] = {
        f"session_{n}": _tail_text(ev / f"round{n}-panel.txt", PANEL_TAIL_CHARS).replace(
            key, "<redacted>"
        )
        for n in (1, 2)
    }
    last = (r1["progress"] or [{}])[-1].get("step")
    base["driver_last_step"] = last
    if not win:
        return {
            **base,
            "result": "failed",
            "summary": f"{host.name}: driver stopped after step {last!r} without a main window (error: {r1['driver'].get('error')})",
        }
    panel = ""
    for f in ev.glob("round1-panel*.txt"):
        panel += f.read_text(encoding="utf-8", errors="replace")[-20000:]
    if not s1["calls"] and LOGIN_MARKERS.search(panel):
        levels.update(L4="environment_unavailable", L5="environment_unavailable")
        levels["L3"] = "passed" if "tavotto_open_figure" in s1["listed_tools"] else "not_run"
        return {
            **base,
            "result": "environment_unavailable",
            "summary": f"{ext.id} requires signing in to its own account before any tool call (L3–L5 blocked)",
            "blocked_reason": "extension login required",
        }
    if not r1["driver"].get("submitted"):
        levels.update(L3="not_run", L4="not_run", L5="not_run")
        return {
            **base,
            "result": "error",
            "summary": f"driver could not operate the {ext.id} panel: {r1['driver'].get('error')}",
        }
    prov1 = host_provenance(rec_log, 0, split)
    prov2 = host_provenance(rec_log, split)
    pdfs = sorted((proj / "out").glob("*.pdf"))
    judged = judge_desktop_live(s1, s2, pdfs[0] if pdfs else None, journal_ok, sid, prov1, prov2)
    judged["L2"] = "passed"
    judged["session_id_from_session_1"] = bool(sid)
    if r1["step"].get("timed_out") or r2["step"].get("timed_out"):
        judged["L5"] = "error"
    levels.update(judged)
    return {
        **base,
        "result": av.aggregate([levels["L2"], levels["L3"], levels["L4"], levels["L5"]]),
        "summary": f"{host.id} {host.display_version} + {ext.id} {ext.version} via {model_conf.get('provider', '?')}: "
        f"L3={levels['L3']} L4={levels['L4']} L5={levels['L5']}",
        "server_side_calls": {
            "session_1": [
                {"tool": c["name"].split("__")[-1], "ok": not c["is_error"]} for c in s1["calls"]
            ],
            "session_2": [
                {"tool": c["name"].split("__")[-1], "ok": not c["is_error"]} for c in s2["calls"]
            ],
        },
    }


# ----------------------------------------------------------------------------- 环境矩阵
#: better-sqlite3 等原生模块的预编译件默认从 GitHub release 取（这台机器取不到）→ npmmirror 的二进制镜像
SQLITE_BINARY_MIRROR = "https://registry.npmmirror.com/-/binary/better-sqlite3"
UV_PYTHON_MIRROR = "https://registry.npmmirror.com/-/binary/python-build-standalone"
CONDA_FORGE_MIRROR = "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge"
MINIFORGE_URL = (
    "https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/miniforge/LatestRelease"
)
UV_PYTHONS = ("3.10", "3.12", "3.14")
MATRIX_FIGURES_DIR = "张三 的论文"


def _py_in(venv: Path) -> Path:
    return venv / "Scripts" / "python.exe"


def prepare_envs(lab: Lab, env: dict, log: Path) -> tuple[dict[str, Path], dict[str, str]]:
    """各解释器环境（幂等、缓存在 cache\\envs\\）：每个都是一个装了 matplotlib 的环境。返回 (可用, 失败原因)。"""
    envs = lab.cache / "envs"
    envs.mkdir(parents=True, exist_ok=True)
    ok: dict[str, Path] = {}
    bad: dict[str, str] = {}
    py = str(lab.venv_python())

    def pip_mpl(target: Path, name: str) -> bool:
        if (target.parent.parent / ".winlab-mpl").exists():
            return True
        r = _run_logged(
            [
                str(target),
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "--only-binary=matplotlib",
                "matplotlib",
            ],
            lab.root,
            env,
            log / f"pip-{name}",
            1200,
        )
        if r.get("exit_code") == 0:
            (target.parent.parent / ".winlab-mpl").write_text("ok")
            return True
        return False

    def venv_from(base: str, venv: Path, name: str) -> None:
        if not _py_in(venv).exists():
            r = _run_logged(
                [base, "-m", "venv", str(venv)], lab.root, env, log / f"venv-{name}", 300
            )
            if r.get("exit_code") != 0:
                bad[name] = "venv creation failed"
                return
        if pip_mpl(_py_in(venv), name):
            ok[name] = _py_in(venv)
        else:
            bad[name] = "pip install matplotlib failed"

    venv_from(BASE_PYTHON, envs / "cpython-3.13", "cpython-3.13")
    venv_from(BASE_PYTHON, envs / "张三 的环境" / "venv", "cpython-3.13-cjk-path")

    # uv（PyPI 镜像装进 Lab venv）→ python-build-standalone（npmmirror），不写注册表、不进 PATH
    uv = lab.venv_python().parent / "uv.exe"
    if not uv.exists():
        _run_logged(
            [py, "-m", "pip", "install", "-q", "--disable-pip-version-check", "uv"],
            lab.root,
            env,
            log / "pip-uv",
            600,
        )
    uenv = dict(
        env,
        UV_PYTHON_INSTALL_MIRROR=UV_PYTHON_MIRROR,
        UV_PYTHON_INSTALL_DIR=str(lab.cache / "uv-python"),
        UV_INDEX_URL=PIP_INDEX,
        UV_NO_CONFIG="1",
    )
    for ver in UV_PYTHONS:
        name = f"uv-{ver}"
        venv = envs / name
        if not _py_in(venv).exists():
            r = _run_logged(
                [str(uv), "venv", "--python", ver, "--seed", str(venv)],
                lab.root,
                uenv,
                log / f"uv-{ver}",
                900,
            )
            if r.get("exit_code") != 0:
                bad[name] = "uv venv failed"
                continue
        if pip_mpl(_py_in(venv), name):
            ok[name] = _py_in(venv)
        else:
            bad[name] = "pip install matplotlib failed"

    # Miniforge：TUNA 镜像，核同目录的 .sha256；JustMe 装进 cache，不注册、不进 PATH
    mf = envs / "miniforge"
    conda = mf / "Scripts" / "conda.exe"
    if not conda.exists():
        try:
            with _opener("direct").open(MINIFORGE_URL + "/", timeout=60) as r:
                listing = r.read().decode("utf-8", "replace")
            exe = re.search(r"Miniforge3-[0-9.]+-\d+-Windows-x86_64\.exe(?=[\"'])", listing).group(
                0
            )
            inst = lab.cache / "installers" / exe
            inst.parent.mkdir(parents=True, exist_ok=True)
            if not inst.exists():
                with _opener("direct").open(f"{MINIFORGE_URL}/{exe}", timeout=900) as r:
                    inst.write_bytes(r.read())
            with _opener("direct").open(f"{MINIFORGE_URL}/{exe}.sha256", timeout=60) as r:
                want = r.read().decode("ascii", "replace").split()[0].lower()
            if hashlib.sha256(inst.read_bytes()).hexdigest() != want:
                inst.unlink()
                bad["conda"] = "miniforge sha256 mismatch"
            else:
                _run_logged(
                    [
                        str(inst),
                        "/InstallationType=JustMe",
                        "/RegisterPython=0",
                        "/AddToPath=0",
                        "/S",
                        f"/D={mf}",
                    ],
                    lab.root,
                    env,
                    log / "miniforge",
                    1800,
                )
        except (urllib.error.URLError, OSError, AttributeError) as exc:
            bad["conda"] = f"miniforge download failed: {type(exc).__name__}"
    if conda.exists() and "conda" not in bad:
        cenv = envs / "conda-mpl"
        if not (cenv / "python.exe").exists():
            r = _run_logged(
                [
                    str(conda),
                    "create",
                    "-y",
                    "-q",
                    "-p",
                    str(cenv),
                    "--override-channels",
                    "-c",
                    CONDA_FORGE_MIRROR,
                    "python=3.12",
                    "matplotlib",
                    # conda 26.9：新环境里给 noarch 包（pip / wheel）生成入口要目标环境自带 conda-launchers，
                    # 否则整个事务失败（10-24 实测：FileNotFoundError cli-64.exe）
                    "conda-launchers",
                ],
                lab.root,
                env,
                log / "conda-create",
                1800,
            )
            if r.get("exit_code") != 0:
                bad["conda-prefix"] = "conda create -p failed"
        if (cenv / "python.exe").exists():
            ok["conda-prefix"] = cenv / "python.exe"
        # 第二种布局：`-n` 建在 Miniforge 自己的 envs\\ 下（用户最常见的用法）——两种布局走的是不同的识别 / 激活路径
        named = mf / "envs" / "mpl"
        if not (named / "python.exe").exists():
            r = _run_logged(
                [
                    str(conda),
                    "create",
                    "-y",
                    "-q",
                    "-n",
                    "mpl",
                    "--override-channels",
                    "-c",
                    CONDA_FORGE_MIRROR,
                    "python=3.12",
                    "matplotlib",
                    "conda-launchers",
                ],
                lab.root,
                env,
                log / "conda-create-named",
                1800,
            )
            if r.get("exit_code") != 0:
                bad["conda-named"] = "conda create -n failed"
        if (named / "python.exe").exists():
            ok["conda-named"] = named / "python.exe"
    elif "conda" not in bad:
        bad["conda"] = "miniforge install failed"
    return ok, bad


def _find_sidecar(shell: Path) -> Path | None:
    for p in shell.parent.rglob("Tavotto.exe"):
        if p != shell and p.parent.name.lower() == "tavotto" and (p.parent / "_internal").is_dir():
            return p
    return None


def matrix_leg_argv(
    py: Path, side: Path, name: str, expect: str, figs: str, extra: list[str]
) -> list[str]:
    """一条矩阵腿的 smoke argv。需要 Conda 激活的解释器 workerd 按设计不接，走 Python 渲染池
    （用户 10-25 裁决，与 nightly 同）：`conda-fallback` = 产物带 workerd、这次渲染只走了 Python 池
    （smoke_app 的这个取值随 #1410 进 main）；其余腿必须真走 workerd。"""
    return [
        str(py),
        "scripts/smoke_app.py",
        "--exe",
        str(side),
        "--figures",
        figs,
        "--expect-source",
        expect,
        "--expect-control-plane",
        "conda-fallback" if name.startswith("conda") else "workerd",
        *extra,
    ]


def run_env_matrix_task(lab: Lab, run_dir: Path, env: dict, package: str) -> dict:
    setup, err = package_path(lab, package)
    if err:
        return {"result": "environment_unavailable", "summary": err}
    if not lab.venv_python().exists():
        return {
            "result": "environment_unavailable",
            "summary": "venv missing: run `winlab prepare` first",
        }
    log = run_dir / "evidence" / "envs"
    ready, broken = prepare_envs(lab, env, log)
    uninstall_all()
    inst = install_package(setup)
    legs = []
    try:
        side = _find_sidecar(Path(inst["shell"])) if inst.get("ok") else None
        if side is None:
            return {
                "result": "error",
                "summary": "install produced no sidecar",
                "install": inst,
                "env_problems": broken,
            }
        figures = run_dir / "work" / MATRIX_FIGURES_DIR / "figures"
        shutil.copytree(lab.repo / "examples" / "figures", figures)
        plan = [
            (
                "bundled",
                None,
                "bundled",
                "examples\\runtime_check",
                ["--expect-packages", "numpy,pandas,scipy,seaborn,PIL,matplotlib"],
            )
        ]
        plan += [
            (name, str(pyexe), "env_override", str(figures), [])
            for name, pyexe in sorted(ready.items())
        ]
        for name, worker, expect, figs, extra in plan:
            lenv = dict(env)
            lenv["PYTHONUTF8"] = "0"  # 与 nightly 冒烟同：中文区域的默认值
            lenv.pop("TAVOTTO_WORKER_PYTHON", None)
            if worker:
                lenv["TAVOTTO_WORKER_PYTHON"] = worker
            argv = matrix_leg_argv(lab.venv_python(), side, name, expect, figs, extra)
            step = _run_logged(argv, lab.repo, lenv, run_dir / "evidence" / f"leg-{name}", 900)
            legs.append(
                {
                    "leg": name,
                    "expect_source": expect,
                    "result": "passed"
                    if step.get("exit_code") == 0
                    else ("error" if step.get("timed_out") else "failed"),
                    "exit_code": step.get("exit_code"),
                    "duration_s": step.get("duration_s"),
                }
            )
    finally:
        uninstall_all()
    for name, why in sorted(broken.items()):
        legs.append({"leg": name, "result": "environment_unavailable", "reason": why})
    overall = av.aggregate([leg["result"] for leg in legs])
    return {
        "result": overall,
        "summary": ", ".join(f"{leg['leg']}={leg['result']}" for leg in legs),
        "legs": legs,
        "package": {"name": package, "version": inst.get("version")},
    }


# ----------------------------------------------------------------------------- 脚本语料
def corpus_venv(lab: Lab) -> Path:
    return lab.cache / "venvs" / "py313-corpus" / "Scripts" / "python.exe"


def run_corpus_task(lab: Lab, run_dir: Path, env: dict, mode: str) -> dict:
    py = corpus_venv(lab)
    log = run_dir / "evidence" / "setup"
    stamp = py.parents[1] / "winlab-corpus.sha256"
    pins = subprocess.run(
        [BASE_PYTHON, "scripts/ci/runtime_pins.py", "--include-corpus"],
        cwd=lab.repo,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if pins.returncode != 0:
        return {"result": "error", "summary": "runtime_pins.py failed"}
    digest = hashlib.sha256(
        (pins.stdout + (lab.repo / "pyproject.toml").read_text(encoding="utf-8")).encode()
    ).hexdigest()
    if not py.exists():
        _run_logged(
            [BASE_PYTHON, "-m", "venv", str(py.parents[1])], lab.root, env, log / "venv", 300
        )
    if not stamp.exists() or stamp.read_text().strip() != digest:
        # 与 CI compat-smoke 同：项目 [dev,ci] + 锁定的语料依赖
        for i, args in enumerate((["-e", ".[dev,ci]"], pins.stdout.split())):
            r = _run_logged(
                [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", *args],
                lab.repo,
                env,
                log / f"pip-{i}",
                2400,
            )
            if r.get("exit_code") != 0:
                return {"result": "error", "summary": "installing the corpus environment failed"}
        stamp.write_text(digest)
    else:
        _run_logged(
            [
                str(py),
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "--no-deps",
                "-e",
                ".",
            ],
            lab.repo,
            env,
            log / "pip-nodeps",
            900,
        )
    out = run_dir / "evidence" / "compat"
    scope = ["--smoke", "--gate", "pr"] if mode == "smoke" else ["--all"]
    argv = [str(py), "scripts/ci/compat_matrix.py", *scope, "--no-fidelity", "--out", str(out)]
    step = _run_logged(
        argv,
        lab.repo,
        dict(env, PYTHONPATH=str(lab.repo / "src")),
        run_dir / "evidence" / "compat_matrix",
        5000,
    )
    reports = sorted(out.rglob("compat-report.json")) if out.exists() else []
    summary = {}
    if reports:
        with contextlib.suppress(OSError, ValueError):
            rep = json.loads(reports[-1].read_text(encoding="utf-8"))
            summary = {k: rep.get(k) for k in ("summary", "counts", "totals", "gate") if k in rep}
    if step.get("timed_out"):
        result = "error"
    elif step.get("exit_code") == 0:
        result = "passed"
    elif reports:
        result = "failed"
    else:
        result = "error"
    return {
        "result": result,
        "summary": f"compat_matrix --{mode} exit={step.get('exit_code')}",
        "mode": mode,
        "report": summary,
        "step": step,
    }


def _run_body(
    lab: Lab,
    task: Task,
    run_dir: Path,
    tests,
    shard,
    network: str,
    model_conf: dict | None,
    pkg: dict | None = None,
) -> dict:
    if task.kind == "network":
        return run_network_task(lab, list(NETWORK_MODES) if network == "all" else [network])
    env = network_env(network, base_env(lab))
    if task.kind == "pytest":
        return run_pytest_task(lab, task, run_dir, tests, shard, env)
    if task.kind == "hosts-live":
        return run_hosts_live_task(
            lab, run_dir, env, model_conf or {}, (pkg or {}).get("mode") or "claude-code"
        )
    if task.kind == "env-matrix":
        return run_env_matrix_task(lab, run_dir, env, (pkg or {}).get("package", ""))
    if task.kind == "corpus":
        return run_corpus_task(lab, run_dir, env, (pkg or {}).get("mode") or "smoke")
    if task.kind == "desktop-window":
        return run_desktop_window_task(
            lab, run_dir.name, run_dir, env, (pkg or {}).get("package", "")
        )
    if task.kind == "desktop-hosts":
        return run_desktop_hosts_task(
            lab, run_dir.name, run_dir, env, (pkg or {}).get("mode") or DESKTOP_HOST_IDS[0]
        )
    if task.kind == "desktop-live":
        return run_desktop_live_task(
            lab,
            run_dir.name,
            run_dir,
            env,
            model_conf or {},
            (pkg or {}).get("mode") or DESKTOP_LIVE_MODES[0],
        )
    if task.kind == "installer":
        return run_installer_task(
            lab, run_dir, env, (pkg or {}).get("package", ""), (pkg or {}).get("from")
        )
    return run_hosts_task(lab, run_dir, env)


def _write_report(lab: Lab, run_dir: Path, run_id: str, report: dict) -> None:
    lab.reports.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (run_dir / "report.json").write_text(payload, encoding="utf-8")
    (lab.reports / f"{run_id}.json").write_text(payload, encoding="utf-8")


def run_task(
    lab: Lab,
    task: Task,
    tests: list[str],
    shard: str | None,
    network: str,
    model_conf: dict | None = None,
    pkg: dict | None = None,
) -> dict:
    run_id = new_run_id()
    run_dir = lab.runs / run_id
    (run_dir / "evidence").mkdir(parents=True)
    # 先报 run id：SSH 断了任务照跑（会话不带走进程），Mac 端凭它轮询 `reports --id`
    print(f"WINLAB_RUN_ID {run_id}", file=sys.stderr, flush=True)
    started = iso_now()
    t0 = time.monotonic()
    with job_lock(lab, run_id, task.id):
        # 被测提交在执行 body 之前、锁内记下；报告也在锁内写盘：
        # 锁一放，别的 prepare 可能已切走提交、clean 也可能删掉这个 run
        commit = repo_head(lab)
        try:
            body = _run_body(lab, task, run_dir, tests, shard, network, model_conf, pkg)
        except Exception as exc:  # 执行器自己的缺陷也要落成一份 error 报告，而不是没有报告
            import traceback

            (run_dir / "evidence" / "agent-traceback.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            body = {
                "result": "error",
                "summary": f"agent crashed: {type(exc).__name__}: {exc}"[:400],
            }
        report = {
            "schema": SCHEMA,
            "kind": REPORT_KIND,
            "run_id": run_id,
            "task": task.id,
            "args": {"tests": tests, "shard": shard, "network": network, **(pkg or {})},
            "commit": commit,
            "started": started,
            "finished": iso_now(),
            "duration_s": round(time.monotonic() - t0, 3),
            "environment": windows_facts(),
            "claim": task.claim,
            "unverified": list(task.unverified),
            **body,
        }
        report["result"] = body["result"]
        report["exit_code"] = av.exit_code_for(report["result"])
        report["evidence"] = _evidence_list(run_dir)
        _write_report(lab, run_dir, run_id, report)
    return report


# ----------------------------------------------------------------------------- prepare
def prepare(lab: Lab, sha: str) -> dict:
    """代码只经 stdin 上的 git bundle 进来：这台机器取不到 GitHub 的 git 大传输（实测首次 clone 25 分钟 0 字节），
    Lab 上也不放任何仓库凭据。bundle 带 `refs/winlab/target`（目标提交）与 `refs/winlab/main`（Mac 的 origin/main），
    以 Lab 已有的 `refs/winlab/main` 为基——之后每次只传增量。"""
    steps = {}
    log = lab.state / "prepare"
    log.mkdir(parents=True, exist_ok=True)
    env = base_env(lab)
    if not (lab.repo / ".git").exists():
        steps["init"] = _run_logged(
            [GIT, "init", "-q", str(lab.repo)], lab.root, env, log / "init", 120
        )
    bundle = lab.cache / "incoming.bundle"
    with open(bundle, "wb") as f:
        shutil.copyfileobj(sys.stdin.buffer, f)
    if bundle.stat().st_size == 0:
        # Mac 判定 Lab 已有目标提交：不收 bundle，但必须核实它真在本地
        bundle.unlink()
        have = subprocess.run(
            [GIT, "-C", str(lab.repo), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            env=env,
        )
        if have.returncode != 0:
            return {
                "result": "error",
                "summary": "empty bundle but target commit is not on the Lab",
                "steps": steps,
            }
        return _prepare_checkout(lab, sha, steps, log, env)
    steps["verify_bundle"] = _run_logged(
        [GIT, "-C", str(lab.repo), "bundle", "verify", "-q", str(bundle)],
        lab.root,
        env,
        log / "bundle-verify",
        600,
    )
    if steps["verify_bundle"].get("exit_code") != 0:
        bundle.unlink(missing_ok=True)
        return {
            "result": "error",
            "summary": "bundle incomplete or missing prerequisites (ssh dropped mid-transfer?)",
            "steps": steps,
        }
    steps["fetch"] = _run_logged(
        [GIT, "-C", str(lab.repo), "fetch", "-q", str(bundle), "+refs/winlab/*:refs/winlab/*"],
        lab.root,
        env,
        log / "fetch",
        1200,
    )
    bundle.unlink(missing_ok=True)
    if steps["fetch"].get("exit_code") != 0:
        return {"result": "error", "summary": "fetch from bundle failed", "steps": steps}
    return _prepare_checkout(lab, sha, steps, log, env)


def _prepare_checkout(lab: Lab, sha: str, steps: dict, log: Path, env: dict) -> dict:
    # 工作区只属于 Lab：丢掉上一次运行的任何改动，再钉到目标提交
    steps["checkout"] = _run_logged(
        [GIT, "-C", str(lab.repo), "checkout", "--force", "--detach", sha],
        lab.root,
        env,
        log / "checkout",
        600,
    )
    steps["clean"] = _run_logged(
        [GIT, "-C", str(lab.repo), "clean", "-ffdx", "-q"], lab.root, env, log / "clean", 600
    )
    if repo_head(lab) != sha:
        return {"result": "error", "summary": "checkout did not land on target", "steps": steps}
    venv = lab.venv_python().parents[1]
    if not lab.venv_python().exists():
        steps["venv"] = _run_logged(
            [BASE_PYTHON, "-m", "venv", str(venv)], lab.root, env, log / "venv", 300
        )
    stamp = venv / "winlab-deps.sha256"
    deps = hashlib.sha256(DEPS_RECIPE.encode())
    for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
        p = lab.repo / name
        if p.exists():
            deps.update(p.read_bytes())
    digest = deps.hexdigest()
    py = str(lab.venv_python())
    if not stamp.exists() or stamp.read_text().strip() != digest:
        steps["pip"] = _run_logged(
            [py, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-e", ".[dev]"],
            lab.repo,
            env,
            log / "pip",
            2400,
        )
        if steps["pip"].get("exit_code") != 0:
            return {"result": "error", "summary": "pip install -e .[dev] failed", "steps": steps}
        # 与 CI backend-platforms 同口径：matplotlib 不在 [dev] 里，单独只装二进制轮子
        steps["matplotlib"] = _run_logged(
            [
                py,
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "--only-binary=matplotlib",
                "matplotlib",
            ],
            lab.repo,
            env,
            log / "matplotlib",
            1200,
        )
        if steps["matplotlib"].get("exit_code") != 0:
            return {"result": "error", "summary": "pip install matplotlib failed", "steps": steps}
        stamp.write_text(digest)
    else:
        # 依赖没变：只把 editable 安装指回当前检出（版本号可能随提交变）
        steps["pip"] = _run_logged(
            [
                py,
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "--no-deps",
                "-e",
                ".",
            ],
            lab.repo,
            env,
            log / "pip-nodeps",
            900,
        )
    steps["fonts_check"] = _run_logged(
        [py, "scripts/fetch_fonts.py", "--dest", str(lab.fonts), "--check"],
        lab.repo,
        env,
        log / "fonts-check",
        120,
    )
    ok = all(s.get("exit_code") == 0 for s in steps.values())
    return {
        "result": "passed" if ok else "error",
        "summary": "prepared" if ok else "a prepare step failed",
        "commit": repo_head(lab),
        "steps": steps,
    }


# ----------------------------------------------------------------------------- 动词
def cmd_doctor(lab: Lab) -> dict:
    facts = windows_facts()
    tools = {
        "python": _tool_version([BASE_PYTHON, "--version"]),
        "git": _tool_version([GIT, "--version"]),
        "node": _tool_version([str(Path(NODE_DIR) / "node.exe"), "--version"]),
        "pnpm": _tool_version([str(Path(NODE_DIR) / "pnpm.cmd"), "--version"]),
        "pwsh": _tool_version(
            [
                r"C:\Program Files\PowerShell\7\pwsh.exe",
                "-NoProfile",
                "-c",
                "$PSVersionTable.PSVersion.ToString()",
            ]
        ),
        "sing-box": _tool_version(
            [str(lab.root / "tools" / "sing-box" / "sing-box.exe"), "version"]
        ),
        "tailscale": _tool_version([r"C:\Program Files\Tailscale\tailscale.exe", "version"]),
        "venv": str(lab.venv_python().exists()),
    }
    usage = shutil.disk_usage(str(lab.root)) if lab.root.exists() else None
    secrets_readable = os.access(lab.root / "secrets" / "sing-box.json", os.R_OK)
    checks = {
        "not_admin": facts.get("is_admin") is False,
        "secrets_not_readable_by_lab_user": not secrets_readable,
        "proxy_loopback_only": all(
            v["non_loopback_open"] in (False, None) for v in proxy_listeners().values()
        ),
    }
    return {
        "result": "passed" if all(checks.values()) else "failed",
        "facts": facts,
        "tools": tools,
        "checks": checks,
        "listeners": proxy_listeners(),
        "disk_free_gb": round(usage.free / 1024**3, 1) if usage else None,
        "repo_head": repo_head(lab),
        "lock": read_lock(lab),
    }


def cmd_status(lab: Lab) -> dict:
    reps = sorted(lab.reports.glob("*.json"), reverse=True)[:10] if lab.reports.exists() else []
    recent = []
    for p in reps:
        with contextlib.suppress(OSError, ValueError):
            d = json.loads(p.read_text(encoding="utf-8"))
            recent.append(
                {
                    k: d.get(k)
                    for k in ("run_id", "task", "result", "summary", "commit", "duration_s")
                }
            )
    return {
        "lock": read_lock(lab),
        "repo_head": repo_head(lab),
        "known_main": known_main(lab),
        "fonts_ok": fonts_ok(lab),
        "recent": recent,
    }


def cmd_reports(lab: Lab, run_id: str | None) -> dict:
    if run_id:
        if not RUN_ID_RE.fullmatch(run_id):
            return {"result": "error", "summary": "bad run id"}
        p = lab.reports / f"{run_id}.json"
        if not p.exists():
            return {"result": "error", "summary": "no such run"}
        return json.loads(p.read_text(encoding="utf-8"))
    return {"runs": sorted((p.stem for p in lab.reports.glob("*.json")), reverse=True)[:50]}


def cmd_clean(lab: Lab, keep: int) -> dict:
    """只删 runs\\ 下最旧的 run 目录（报告 JSON 保留）；正在跑的那一个永远不删。"""
    holder = read_lock(lab)
    if holder is None and lock_is_fresh(lab):
        return {"result": "error", "summary": "lock is being written; retry shortly", "removed": []}
    active = (holder or {}).get("run_id")
    names = (
        sorted(n for n in os.listdir(lab.runs) if RUN_ID_RE.fullmatch(n))
        if lab.runs.exists()
        else []
    )
    victims = [n for n in names[: max(0, len(names) - keep)] if n != active]
    removed = [n for n in victims if safe_remove_child(lab.runs, n)]
    return {"result": "passed", "removed": removed, "kept": len(names) - len(removed)}


def cmd_stop(lab: Lab) -> dict:
    """结束持锁的 agent 进程：它持有的 Job Object 句柄随之关闭（KILL_ON_JOB_CLOSE），整棵子孙树一起结束。
    只有确认进程已退出才释放锁；没确认就报 error、锁原样保留。"""
    holder = read_lock(lab)
    if not holder:
        if lock_is_fresh(lab):
            return {
                "result": "error",
                "summary": "lock is being written; retry shortly",
                "stopped": None,
            }
        return {"result": "passed", "summary": "no active job"}
    pid = int(holder.get("pid", 0))
    if RUN_ID_RE.fullmatch(str(holder.get("run_id", ""))):
        # 交互会话里那一半不是本进程的子孙：留停止标记，gui_host 看到就结束它的 Job
        gui = lab.state / "gui"
        gui.mkdir(parents=True, exist_ok=True)
        (gui / f"{holder['run_id']}.stop").write_text("stop", encoding="utf-8")
    _terminate(pid)
    deadline = time.monotonic() + 15
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.5)
    if _pid_alive(pid):
        return {"result": "error", "summary": f"pid {pid} still alive; lock kept", "stopped": None}
    if (read_lock(lab) or {}).get("run_id") == holder.get("run_id"):
        lab.lock_path.unlink(missing_ok=True)
    return {"result": "passed", "stopped": holder.get("run_id")}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="winlab-agent", add_help=False, exit_on_error=False)
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("doctor")
    sub.add_parser("status")
    p = sub.add_parser("prepare")
    p.add_argument("--sha", required=True)
    sub.add_parser("fonts")
    t = sub.add_parser("test")
    t.add_argument("task", choices=sorted(TASKS))
    t.add_argument("--tests", nargs="+", default=[])
    t.add_argument("--shard")
    t.add_argument("--network")
    t.add_argument("--package")
    t.add_argument("--from", dest="frm")
    t.add_argument("--mode")
    r = sub.add_parser("reports")
    r.add_argument("--id")
    c = sub.add_parser("clean")
    c.add_argument("--keep", type=int, default=20)
    sub.add_parser("stop")
    return ap


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()
    res = payload.get("result")
    return av.exit_code_for(res) if res in av.RESULTS else 0


def main(argv: list[str] | None = None, lab: Lab | None = None) -> int:
    if argv is None:
        if "--ssh" in sys.argv[1:]:
            argv = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        else:
            argv = sys.argv[1:]
    lab = lab or Lab()
    try:
        ns = build_parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit) as e:
        sys.stdout.write(json.dumps({"result": "error", "summary": f"usage: {e}"}) + "\n")
        return av.EXIT_USAGE
    if ns.verb == "doctor":
        return _emit(cmd_doctor(lab))
    if ns.verb == "status":
        return _emit(cmd_status(lab))
    if ns.verb == "reports":
        return _emit(cmd_reports(lab, ns.id))
    if ns.verb == "clean":
        return _emit(cmd_clean(lab, max(0, ns.keep)))
    if ns.verb == "stop":
        return _emit(cmd_stop(lab))
    if ns.verb == "fonts":
        try:
            with job_lock(lab, "fonts", "fonts"):
                return _emit(receive_fonts(lab, sys.stdin.buffer.read()))
        except LabBusy as busy:
            return _emit(
                {"result": "environment_unavailable", "summary": "lab busy", "lock": busy.args[0]}
            )
    if ns.verb == "prepare":
        if not SHA_RE.fullmatch(ns.sha):
            return (
                _emit({"result": "error", "summary": "--sha must be a full 40-hex commit id"})
                or av.EXIT_USAGE
            )
        try:
            with job_lock(lab, "prepare", "prepare"):
                out = prepare(lab, ns.sha)
                out["sha"], out["finished"] = ns.sha, iso_now()
                (lab.state / "prepare-last.json").write_text(
                    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return _emit(out)
        except LabBusy as busy:
            return _emit(
                {"result": "environment_unavailable", "summary": "lab busy", "lock": busy.args[0]}
            )
    task = TASKS[ns.task]
    network = ns.network or task.networks[0]
    err = validate_test_args(task, ns.tests, ns.shard, network, ns.package, ns.frm, ns.mode)
    if err:
        sys.stdout.write(json.dumps({"result": "error", "summary": err}) + "\n")
        return av.EXIT_USAGE
    try:
        model_conf = None
        if task.needs_model_conf:
            parsed = read_model_conf(sys.stdin)
            if isinstance(parsed, str):
                return _emit({"result": "environment_unavailable", "summary": parsed})
            model_conf = parsed
        pkg = {"package": ns.package, "from": ns.frm} if task.accepts_package else None
        if task.modes:
            pkg = {"mode": ns.mode or task.modes[0]}
        return _emit(run_task(lab, task, ns.tests, ns.shard, network, model_conf, pkg))
    except LabBusy as busy:
        return _emit(
            {"result": "environment_unavailable", "summary": "lab busy", "lock": busy.args[0]}
        )


if __name__ == "__main__":
    sys.exit(main())
