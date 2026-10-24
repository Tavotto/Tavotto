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
            id="hosts-live",
            kind="hosts-live",
            summary="AI 宿主 L4/L5：真 Claude Code（国内 Anthropic 兼容模型）调用 Tavotto MCP，改图、导出、重开核对",
            timeout_s=2400,
            needs_model_conf=True,
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
) -> str | None:
    """返回错误文本；None = 合法。所有参数都可审计：没有透传通道。"""
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
def _opener(mode: str):
    if mode == "direct":
        handler = urllib.request.ProxyHandler({})
    else:
        url = f"http://127.0.0.1:{PROXY_PORTS[mode]}"
        handler = urllib.request.ProxyHandler({"http": url, "https": url})
    ctx = ssl.create_default_context()  # 完整证书校验；绝不关
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
            # Tavotto 的数据目录写进宿主的 MCP 配置（用户就是这么配的），不塞进宿主进程自己的环境
            data = f"TAVOTTO_DATA_DIR={run_dir / 'work' / 'tavotto-data'}"
            if h["id"] == "claude-code":
                add = [
                    str(exe),
                    "mcp",
                    "add",
                    "tavotto",
                    "--scope",
                    "user",
                    "-e",
                    data,
                    "--",
                    *server,
                ]
            else:
                add = [str(exe), "mcp", "add", "tavotto", "--env", data, "--", *server]
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


def _host_model_env(conf: dict) -> dict[str, str]:
    """国内 Anthropic 兼容接口（供应商官方给的 Claude Code 接入变量）+ 关掉 Claude Code 发往 Anthropic 的
    非必要流量：Lab 在中国大陆，不经代理、也不碰海外服务。"""
    model = conf.get("model") or "deepseek-flash"
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


def run_hosts_live_task(lab: Lab, run_dir: Path, env: dict, model_conf: dict) -> dict:
    exe = lab.cache / "npm-global" / "claude.cmd"
    py = lab.venv_python()
    if not exe.exists() or not py.exists():
        return {
            "result": "environment_unavailable",
            "summary": "run `test hosts` first (installs Claude Code CLI)",
        }
    version = _tool_version([str(exe), "--version"])
    ev = run_dir / "evidence" / "claude-code-live"
    proj = run_dir / "work" / "project"
    (proj / "out").mkdir(parents=True)
    (proj / "plot.py").write_text(LIVE_FIGURE, encoding="utf-8")
    cfg = run_dir / "work" / "claude-config"
    cfg.mkdir(parents=True)
    henv = dict(env)
    henv.update(_host_model_env(model_conf))
    henv["CLAUDE_CONFIG_DIR"] = str(cfg)
    henv["TAVOTTO_MCP_ROOTS"] = str(proj)
    henv["MPLCONFIGDIR"] = str(run_dir / "work" / "mpl")
    seed = _run_logged([str(py), "plot.py"], proj, henv, ev / "seed-figure", 300)
    if seed.get("exit_code") != 0:
        return {
            "result": "error",
            "summary": "seeding the figure script failed",
            "host_version": version,
        }
    server = [str(py), str(lab.repo / "codex-plugin" / "mcp" / "server.py")]
    _run_logged(
        [
            str(exe),
            "mcp",
            "add",
            "tavotto",
            "--scope",
            "user",
            # `-e` 是可变长选项：名字必须在它之前，`--` 截断后才是命令（实测放在名字前会把名字当成变量吞掉）
            # 只给 MCP 服务器它需要的 Tavotto 变量；模型凭据只在宿主自己的环境里
            "-e",
            f"TAVOTTO_MCP_ROOTS={proj}",
            "-e",
            f"TAVOTTO_DATA_DIR={run_dir / 'work' / 'tavotto-data'}",
            "-e",
            "TAVOTTO_NO_TELEMETRY=1",
            "--",
            *server,
        ],
        proj,
        henv,
        ev / "mcp-add",
        120,
    )

    def claude_print(prompt: str) -> list[str]:
        # 提示词紧跟 `-p`：`--allowedTools` 是可变长选项，放在它后面的提示词会被当成工具名吞掉
        return [
            str(exe),
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            # Tavotto 的 open 结果超过 Claude Code 单次 MCP 结果上限时会被转存成文件，模型要多花几轮去读
            "30",
            "--allowedTools",
            "mcp__tavotto",
        ]

    r1 = _run_logged(claude_print(LIVE_SESSION_1), proj, henv, ev / "session-1", 1200)
    _scrub(ev, model_conf["api_key"])
    s1 = parse_stream(
        Path(f"{ev / 'session-1'}.stdout.log").read_text(encoding="utf-8", errors="replace")
    )
    sid = opened_session_id(s1)
    journal_ok = journal_has_edits(run_dir / "work" / "tavotto-data", sid)
    if sid:
        r2 = _run_logged(
            claude_print(LIVE_SESSION_2.format(sid=sid)), proj, henv, ev / "session-2", 900
        )
        _scrub(ev, model_conf["api_key"])
    else:
        r2 = {"exit_code": None, "skipped": "no session id from session 1"}
        Path(f"{ev / 'session-2'}.stdout.log").write_text("", encoding="utf-8")
    s2 = parse_stream(
        Path(f"{ev / 'session-2'}.stdout.log").read_text(encoding="utf-8", errors="replace")
    )
    pdfs = sorted((proj / "out").glob("*.pdf"))
    levels = judge_live(s1, s2, pdfs[0] if pdfs else None, journal_ok, sid)
    levels["session_id_from_session_1"] = bool(sid)
    if r1.get("timed_out") or r2.get("timed_out"):
        levels["L5"] = "error"
    return {
        "result": av.aggregate([levels["L4"], levels["L5"]]),
        "summary": f"claude-code {version} via {model_conf.get('provider', '?')}/{model_conf.get('model') or 'deepseek-flash'}: "
        f"L4={levels['L4']} L5={levels['L5']}",
        "host": {
            "id": "claude-code",
            "version": version,
            "provider": model_conf.get("provider"),
            "model": model_conf.get("model") or "deepseek-flash",
            "network": "direct",
        },
        "levels": levels,
        "tool_calls": {
            "session_1": [
                {"name": c["name"], "ok": c.get("is_error") is False} for c in s1["calls"]
            ],
            "session_2": [
                {"name": c["name"], "ok": c.get("is_error") is False} for c in s2["calls"]
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
        return run_hosts_live_task(lab, run_dir, env, model_conf or {})
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
    err = validate_test_args(task, ns.tests, ns.shard, network, ns.package, ns.frm)
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
        return _emit(run_task(lab, task, ns.tests, ns.shard, network, model_conf, pkg))
    except LabBusy as busy:
        return _emit(
            {"result": "environment_unavailable", "summary": "lab busy", "lock": busy.args[0]}
        )


if __name__ == "__main__":
    sys.exit(main())
