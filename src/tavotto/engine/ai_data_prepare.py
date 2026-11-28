"""TAF 的 `data_prepare` 适配器（ADR 0215 第一期·后端内核）：模型写一个 Python **准备脚本**，把原始数据文件整理成一张整洁的表（CSV）；
Tavotto 在候选目录里试跑、核契约、报错喂回去改；用户**采用**之后才往数据文件夹的 `tavotto_figures/` 里新建「准备脚本 + 整理好的表」。
画图仍走现有配方（`ai_data_propose` / `ai_data_task`，本期**不接**）。纯标准库，Flask 父进程 import。

形态（与 `ai_convert` 同一套：候选 + 采用，**不改 `run_task` 的写入放行**）：

* 执行器 `PrepareExecutor`：`kind="agent"`、`writes_user_files=False`、契约 `allowed_writes=[]`。`execute` 驱动注入的 `agent` 回调（生产里将是
  `agent_runner` 子进程，本期不接桥；测试用 `inprocess_agent(model)` 在进程内跑同一个工具集），`commit` = 把通过检查的候选留存成 `ready`
  （`meta.json`，不碰用户文件）。失败 / 取消 / 超限：候选目录与试跑目录都删，项目文件夹逐字节不变。
* 候选目录 `data_dir()/cache/ai_prepare/<sid>/`：`prepare.py`（模型写的）、`out.csv`（最近一次通过检查的输出，ready 之后只读且不再变）、`meta.json`。
  原始数据**不拷贝**（可能几个 GB）：脚本以绝对路径作 argv 直接读原文件。
* `candidate_table(sid)`：只在 ready 时回 `out.csv` 的路径，给之后接 `ai_data_propose` 在**采用之前**出备选图用；只读入口。
* `adopt(sid, project)`：把准备脚本（原字节）与整理好的表（开头 `#` 行是来源证明）**新建**进 `<数据文件夹>/tavotto_figures/`，只经
  `scriptlock.create_script`（已存在即失败，永不覆盖；重名换序号；失败回滚本次已建的）。画图脚本不在本期。

**准备脚本契约**（`run_check`；不满足不让采用）：`python prepare.py <输入> <输出.csv>`；恰好写出这一张 UTF-8 CSV；stdout 最后一行是
`{"rows_in","rows_out","dropped":[{"reason","count"}]}`；输出能被 `ai_data_figure.read_text_table` 读成至少 1 列数值、数据行数 == `rows_out`；
同一输入跑两次输出字节相同（两次用**不同的 PYTHONHASHSEED**，让依赖哈希顺序的不确定输出暴露）；运行里没有写出沙盒 / 起进程 / 联网 / 读白名单之外。
记账 `rows_in == rows_out + Σdropped` 只做**软检查**（聚合 / 汇总天然不满足），不符记 `accounting: not_reconciled`；`rows_in` 本身核不了，一律标「脚本自报」。
输入文件的 sha256 在第一次 `check` 时算一次，之后只比文件身份（size / mtime_ns / inode / ctime）；**局限**：保留 mtime 的原地改写测不出。
确定性复跑耗时：第一遍 ≤ `DETERMINISM_FULL_S` 才全量跑第二遍，否则在输入前 `SAMPLE_CHECK_BYTES` 上跑两次比对并记 `determinism: sample_only`。
`check` 的全量那一遍不受 300 s 单次试跑上限限制，用任务剩余的墙钟预算（`remaining()`）。**取消只在两次运行之间生效**（一次运行最长 = 它的超时）。

**读白名单**（`run_python(read_allow=…)`，进程内审计钩子，C 层 / ctypes 绕得过，Windows 只有这一层且未验证）：本次登记的 source 文件、试跑目录、解释器前缀
（含 venv 根）与系统只读位置（`system_read_roots`，按平台列）；白名单外的 open 记 `blocked_reads` 并判失败。OS 沙盒的凭据拒读与断网照旧叠着（`strict_deny`）。
**防篡改的收窄**（`trial.run_python(tamper=…)`，闭集 `full` / `stdlib`，没有「全关」）：`strict_deny` 的三条防篡改规则（禁看解释器对象 = gc 三个事件、禁改函数属性、禁把文件夹当文件开）
原样（`full`，实验模式 ADR 0197 的默认，一个字节不变）会让标准库 `collections.namedtuple(defaults=…)` 失败，于是 `import inspect / dataclasses / numpy` 与
`tempfile.NamedTemporaryFile / TemporaryDirectory` 起不来。数据准备用 `stdlib`：gc 三个事件照拦；函数的 `__code__` / `__globals__` / `__kwdefaults__` 照拦；`__defaults__`
只在「执行这次赋值的帧的代码对象就是 `collections.namedtuple` 的」时可赋（帧深度 1，实测）；文件夹只在位于本次试跑目录之内时可当文件开（`tempfile` 开的是试跑目录里的 tmp）。
这三处合起来仍挡得住「gc 找到钩子再换 `__code__`」「别的函数改 `__defaults__`」「开试跑目录之外的文件夹」（有用例，`full` / `stdlib` 两种都锁）。
断网、凭据位置、凭据形状文件名两种模式相同。仍是 ADR 0215「已知盲区」：C 层 / ctypes（导入不拦）绕得过；Windows 没有系统沙盒，进程内钩子是唯一一层且未验证。
"""

from __future__ import annotations

import ast
import codecs
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    ai_data_figure,
    ai_data_task,
    ai_tasks,
    atomicio,
    config,
    inputremap,
    projectenv,
    scriptlock,
)
from .agent_runner import loop, prepare_tools
from .agent_runner.prepare_tools import HookReply, PrepareHooks, Source, normalize_label
from .agent_runner.redact import NO_REDACTION
from .agent_runner.tools import RunEnvelope, _file_identity
from .ai_tasks import FAILED, VERIFIED, CheckResult, TaskError
from .fixtools import readscope, trial

LOG = logging.getLogger(__name__)

TASK_TYPE = "data_prepare"
PREPARE_DIR = config.data_dir() / "cache" / "ai_prepare"
#: 待采用候选的保留时间（同 convert）
RETAIN_S = 24 * 3600
META_NAME = "meta.json"
SCRIPT_NAME = prepare_tools.SCRIPT_NAME
OUT_NAME = "out.csv"
_SID = re.compile(r"[0-9a-f]{12}")

#: 准备脚本输出表的上限（整理好的表应当小；要整份读进内存采用，所以不放大）
OUT_MAX_BYTES = 64 * 1024 * 1024
#: 单个原始文件的上限（只读、不拷贝，主要防误选）
SOURCE_MAX_BYTES = 64 * 1024**3
#: 确定性复跑
DETERMINISM_FULL_S = 60.0
SAMPLE_CHECK_BYTES = 64 * 1024 * 1024
#: 全量那一遍的最短 / 最长超时（秒）；上限同 Budget 的墙钟上限
FULL_RUN_MIN_S = 30.0
#: 任务墙钟预算（ADR 0215 第五节初值）；Budget 的上限是 3600
WALL_S = 900.0
MAX_CHECKS = prepare_tools.MAX_CHECKS
#: 报告里 `reason` 的长度、`dropped` 的条数上限
REASON_MAX = 80
DROPPED_MAX = 50
#: 给模型看的脚本输出（单次）
OUTPUT_CLIP_CHARS = 6000
PREVIEW_LINES = 6
PREVIEW_CHARS = 2000
FIGURES_DIR = ai_data_task.FIGURES_DIR
MAX_NUMBERED = 99
#: 采用时 `#` 行的上限（`ai_data_figure.LEAD_MAX_LINES` 是读表时显示的说明行数）
PROVENANCE_MAX_LINES = ai_data_figure.LEAD_MAX_LINES

_STEM_SAFE = re.compile(r"[^A-Za-z0-9_-]+")
_META_STATES = ("ready", "adopted")


class PrepareError(Exception):
    """对外的稳定错误码（同 `ai_convert.ConvertError`）。"""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


# --------------------------------------------------------------------------- 小工具
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | os.PathLike, cancel: threading.Event | None = None) -> str | None:
    """整份文件的 sha256（1 MiB 分块、可取消；取消回 None，读不了抛 OSError）。现有的 `data_digest` 只认项目相对路径且封顶 512 MiB，
    `_stream_check` 只给文本表且同时判编码，都不合用。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            if cancel is not None and cancel.is_set():
                return None
            h.update(chunk)
    return h.hexdigest()


def identity_of(path: str | os.PathLike) -> tuple:
    return tuple(_file_identity(os.stat(path), change_time=os.name != "nt"))


def _remove_dir(path: Path) -> None:
    """删候选目录（含只读的 out.csv：先放开权限再删，Windows 上只读文件删不掉）。"""

    def onerror(func, p, _exc):
        with contextlib.suppress(OSError):
            os.chmod(p, 0o700)
            func(p)

    if path.is_symlink():
        with contextlib.suppress(OSError):
            path.unlink()
        return
    shutil.rmtree(path, onerror=onerror)


def _under_prepare_dir(path: Path) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(PREPARE_DIR)]) == (
            os.path.realpath(PREPARE_DIR)
        )
    except ValueError:
        return False


def remove_dir(cdir: Path) -> None:
    if _under_prepare_dir(cdir) and os.path.realpath(cdir) != os.path.realpath(PREPARE_DIR):
        with contextlib.suppress(OSError):
            _remove_dir(cdir)


def prune_expired(now: float | None = None) -> int:
    """删超龄的候选（建新候选时顺手做）。"""
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(PREPARE_DIR.iterdir())
    except OSError:
        return 0
    for d in entries:
        try:
            if _SID.fullmatch(d.name) and d.is_dir() and now - d.stat().st_mtime > RETAIN_S:
                remove_dir(d)
                removed += 1
        except OSError:
            continue
    return removed


# --------------------------------------------------------------------------- 读白名单
def system_read_roots(platform: str | None = None, environ: dict | None = None) -> list[str]:
    """脚本进程除「解释器前缀 / 试跑目录 / 登记文件」外还可读的系统只读位置（按平台，能少则少）。纯逻辑。
    这些是标准库 / 时区 / 设备文件所需；用户目录、项目目录一律不在其中。"""
    plat = platform or sys.platform
    env = os.environ if environ is None else environ
    if plat == "darwin":
        return [
            "/System",
            "/usr/lib",
            "/usr/share",
            "/private/var/db/timezone",
            "/private/etc/localtime",
            "/sys/fs/cgroup",  # macOS 上不存在，加了无害；见下 Linux 一条的理由
            "/dev/null",
            "/dev/zero",
            "/dev/urandom",
            "/dev/random",
            "/dev/tty",
        ]
    if plat.startswith("linux"):
        return [
            "/usr",
            "/lib",
            "/lib64",
            "/etc/localtime",
            "/etc/timezone",
            "/proc/self",
            "/proc/cpuinfo",
            "/proc/meminfo",
            "/sys/devices/system/cpu",
            # 内核的 CPU 配额信息（不含用户数据）：pandas 3.x 的 `pandas/compat/_cpu.py` 首次读表时会读 cgroup 的 cpu.max /
            # cpu.cfs_quota_us / cpu.cfs_period_us；被拦时它自己吞掉 PermissionError，但 `blocked_reads>0` 会判败，所以放行这一棵。Windows 不加
            "/sys/fs/cgroup",
            "/dev/null",
            "/dev/zero",
            "/dev/urandom",
            "/dev/random",
            "/dev/tty",
        ]
    root = env.get("SystemRoot") or env.get("SYSTEMROOT")
    return [root] if root else []


def interpreter_roots(python: str) -> list[str]:
    """脚本解释器自己的根：venv 根（`pyvenv.cfg` 所在）或 `bin` / `Scripts` 的上一级、conda 环境根，以及它的真实路径所在的安装前缀。
    守卫在目标解释器里还会加 `sys.prefix / base_prefix / exec_prefix / base_exec_prefix`；这里补的是守卫在 `site` 之前看不到的 venv 根。"""
    p = Path(python)
    roots: list[str] = []
    for cand in (p.parent, p.parent.parent):
        if (cand / "pyvenv.cfg").is_file():
            roots.append(str(cand))
            break
    else:
        roots.append(
            str(p.parent.parent if p.parent.name.lower() in ("bin", "scripts") else p.parent)
        )
    real = Path(os.path.realpath(python))
    roots.append(
        str(real.parent.parent if real.parent.name.lower() in ("bin", "scripts") else real.parent)
    )
    return list(dict.fromkeys(roots))


def read_allow_for(python: str, source_paths) -> dict:
    return {
        "roots": [*interpreter_roots(python), *system_read_roots()],
        "files": [os.path.realpath(p) for p in source_paths],
    }


def strict_roots(project_root: Path) -> list[str]:
    return readscope.ReadScope(home=os.path.expanduser("~"), project_root=project_root).deny_roots()


# --------------------------------------------------------------------------- 候选
@dataclass
class PrepareCandidate:
    sid: str
    dir: Path
    project_root: Path
    source: Source
    source_rel: str
    python: str
    #: 最近一次**通过**检查的事实（`run_check` 填；`verify` / `stage` / `adopt` 只信它）
    checked: dict | None = None
    ready: bool = False
    #: 对桥 / Task Core 而言「没写用户文件」：恒 False
    committed: bool = False
    extras: dict = field(default_factory=dict)

    @property
    def script(self) -> Path:
        return self.dir / SCRIPT_NAME

    @property
    def out(self) -> Path:
        return self.dir / OUT_NAME


def _check_sid(sid: object) -> str:
    if not isinstance(sid, str) or not _SID.fullmatch(sid):
        raise PrepareError("prepare_not_found", 404)
    return sid


def create_candidate(
    project_root: str | Path, source_rel: str, python: str, *, sid: str | None = None
) -> PrepareCandidate:
    """建候选目录并登记**一份**原始数据文件（项目内、普通文件、非符号链接出界；不拷贝）。失败不留目录。"""
    root = Path(project_root)
    label = normalize_label(source_rel)
    real = projectenv.contained_path(root, label) if label else None
    if real is None:
        raise PrepareError("prepare_source_invalid", 400)
    try:
        st = os.stat(real)
    except OSError:
        raise PrepareError("prepare_source_invalid", 400) from None
    if not stat.S_ISREG(st.st_mode) or st.st_size > SOURCE_MAX_BYTES:
        raise PrepareError("prepare_source_invalid", 400)
    prune_expired()
    sid = sid or uuid.uuid4().hex[:12]
    cdir = PREPARE_DIR / _check_sid(sid)
    cdir.parent.mkdir(parents=True, exist_ok=True)
    try:
        cdir.mkdir(exist_ok=False)
    except FileExistsError:
        raise PrepareError("prepare_exists", 409) from None
    src = Source(label, real, st.st_size, identity_of(real))
    return PrepareCandidate(sid, cdir, root, src, label, python)


# --------------------------------------------------------------------------- 解释器与可用包
_PROBE_CODE = """
import sys, json, importlib.util as u, importlib.metadata as m
out = {"python": "%d.%d.%d" % sys.version_info[:3]}
for n in ("numpy", "pandas"):
    if u.find_spec(n) is None:
        out[n] = None
    else:
        try:
            out[n] = m.version(n)
        except Exception:
            out[n] = "?"
print(json.dumps(out))
"""
_PROBE_CACHE: dict[str, str] = {}
NOTE_FALLBACK = (
    "Python version unknown; use only the Python standard library. Do not import tavotto."
)


def probe_environment(python: str, project_root: Path) -> str:
    """首条消息里那一行：Python 版本、numpy / pandas 是否可导入（只有有 / 无与版本号）。同一个解释器只探一次；探测在同一个受保护的
    子进程里跑（断网、读白名单、不装包、`find_spec` 不导入包）；失败回保守的「只用标准库」。"""
    if python in _PROBE_CACHE:
        return _PROBE_CACHE[python]
    note = NOTE_FALLBACK
    try:
        with trial.scratch_dir("prepare-probe") as d:
            res = trial.run_python(
                python,
                code=_PROBE_CODE,
                timeout=trial.INSPECT_TIMEOUT_S,
                trial_dir=d,
                strict_deny=strict_roots(project_root),
                tamper="stdlib",
                read_allow=read_allow_for(python, ()),
            )
        if res.get("status") == "ok" and res.get("returncode") == 0:
            info = json.loads((res.get("stdout") or "").strip().splitlines()[-1])
            parts = [f"Python {info['python']}"]
            for name in ("numpy", "pandas"):
                v = info.get(name)
                parts.append(f"{name} {v} is available" if v else f"{name} is not available")
            note = (
                "; ".join(parts)
                + ". Use only the Python standard library and the packages listed as available here. "
                "Do not import tavotto."
            )
    except Exception:  # noqa: BLE001 - 探测失败只是保守
        note = NOTE_FALLBACK
    _PROBE_CACHE[python] = note
    return note


# --------------------------------------------------------------------------- 输出整理（给模型看的）
def _path_forms(p: str | os.PathLike) -> list[str]:
    raw = os.fspath(p)
    forms = {raw, os.path.realpath(raw), os.path.abspath(raw)}
    forms |= {f.replace("\\", "/") for f in forms} | {f.replace("/", "\\") for f in forms}
    return sorted((f for f in forms if f), key=len, reverse=True)


def sanitize_output(text: str, mapping: list[tuple[str, str]]) -> str:
    """把绝对路径换成标签（试跑目录 → `work`、原始文件 → `source/<标签>`、主目录 → `~`），再过全套 `readscope.scrub`。
    mac / Windows 的文件系统不分大小写，所以不区分大小写地换。`mapping` 里长的在前。"""
    flags = re.IGNORECASE if (os.name == "nt" or sys.platform == "darwin") else 0
    for old, new in mapping:
        for form in _path_forms(old):
            text = re.sub(re.escape(form), lambda _m, n=new: n, text, flags=flags)
    return readscope.scrub(text)


def _tail_chars(text: str, limit: int = OUTPUT_CLIP_CHARS) -> str:
    if len(text) <= limit:
        return text
    return "[... earlier output omitted ...]\n" + text[-limit:]


# --------------------------------------------------------------------------- 报告行
def parse_report(stdout: str) -> tuple[dict | None, str]:
    """stdout 最后一个非空行必须是 `{"rows_in","rows_out","dropped"}`（恰好这三个键）。回 `(报告, "")` 或 `(None, 原因短码)`。"""
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None, "report_missing"
    try:
        obj = json.loads(lines[-1])
    except ValueError:
        return None, "report_missing"
    if not isinstance(obj, dict) or set(obj) != {"rows_in", "rows_out", "dropped"}:
        return None, "report_invalid"

    def nonneg(v) -> bool:
        return type(v) is int and v >= 0

    if not nonneg(obj["rows_in"]) or not nonneg(obj["rows_out"]):
        return None, "report_invalid"
    dropped = obj["dropped"]
    if not isinstance(dropped, list) or len(dropped) > DROPPED_MAX:
        return None, "report_invalid"
    for d in dropped:
        if (
            not isinstance(d, dict)
            or set(d) != {"reason", "count"}
            or not isinstance(d["reason"], str)
            or not 1 <= len(d["reason"]) <= REASON_MAX
            or any(ord(c) < 32 for c in d["reason"])
            or type(d["count"]) is not int
            or d["count"] <= 0
        ):
            return None, "report_invalid"
    return obj, ""


def reconciled(report: dict) -> bool:
    return report["rows_in"] == report["rows_out"] + sum(d["count"] for d in report["dropped"])


# --------------------------------------------------------------------------- 一次试跑
@dataclass
class TrialRun:
    status: str  # ok | timeout | spawn_failed | env_unavailable | trial_unguarded | scratch_failed | input_changed | cancelled | no_time
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    out_size: int | None = None
    out_sha256: str | None = None
    extra_outputs: list[str] = field(default_factory=list)
    blocked: dict = field(default_factory=dict)
    output_truncated: bool = False
    out_copy: Path | None = None
    preview: str = ""
    sample: bool = False


def _make_sample(src: Source, dest_dir: Path, nbytes: int) -> Path:
    """输入的前 `nbytes` 字节（截到最后一个换行；没有换行就原样）。放进试跑目录的 `in/`，保留原文件名（脚本可能按扩展名分流）。"""
    with open(src.path, "rb") as fh:
        raw = fh.read(nbytes)
    if len(raw) == nbytes:
        cut = raw.rfind(b"\n")
        if cut > 0:
            raw = raw[: cut + 1]
    dest_dir.mkdir(exist_ok=True)
    out = dest_dir / Path(src.path).name
    out.write_bytes(raw)
    return out


def _stray_files(trial_dir: Path, sample_name: str | None) -> list[str]:
    """试跑目录里「不该有」的产物：`work/`、`in/`、`out/` 里除脚本、指定输出、输入样本、守卫握手文件外的一切（含子目录与链接），
    以及试跑目录顶层的陌生名字。`tmp/`、`mpl/` 是草稿区，不计。"""
    allowed = {
        ("work", SCRIPT_NAME),
        ("work", OUT_NAME),
        ("out", "result.json"),
        *([("in", sample_name)] if sample_name else []),
    }
    stray: list[str] = []
    try:
        for top in os.scandir(trial_dir):
            if (
                top.name not in ("work", "tmp", "mpl", "out", "in")
                and top.name != trial.PENDING_MARKER
            ):
                stray.append(top.name)
    except OSError:
        pass
    for sub in ("work", "in", "out"):
        try:
            entries = list(os.scandir(trial_dir / sub))
        except OSError:
            continue
        for e in entries:
            if e.name == "__pycache__":
                continue
            if e.is_symlink() or e.is_dir(follow_symlinks=False) or (sub, e.name) not in allowed:
                stray.append(f"{sub}/{e.name}")
    return stray


def run_trial(
    cand: PrepareCandidate,
    *,
    timeout: float,
    hash_seed: int,
    sample_bytes: int | None = None,
    keep_output: bool = False,
    cancel: threading.Event | None = None,
) -> TrialRun:
    """在一个专用试跑目录里跑一次 `prepare.py`（`trial.run_python`：写入绊线 + 禁起进程 + 系统沙盒 + 断网 + 读白名单），
    返回事实。输出 CSV 只留 `size` / `sha256`（`keep_output` 才复制回候选目录的 `out.csv`）。试跑目录无论成败都清理。"""
    if cancel is not None and cancel.is_set():
        return TrialRun("cancelled")
    try:
        before = identity_of(cand.source.path)
    except OSError:
        return TrialRun("input_changed")
    if tuple(cand.source.identity) != before:
        return TrialRun("input_changed")
    try:
        script_bytes = cand.script.read_bytes() if not cand.script.is_symlink() else b""
    except OSError:
        script_bytes = b""
    if not script_bytes.strip():
        return TrialRun("script_missing")
    t0 = time.monotonic()
    try:
        with trial.scratch_dir(f"prepare-{cand.sid}") as trial_dir:
            return _run_in(
                cand,
                trial_dir,
                script_bytes,
                before,
                t0,
                timeout,
                hash_seed,
                sample_bytes,
                keep_output,
                cancel,
            )
    except OSError:
        return TrialRun("scratch_failed")


def _run_in(
    cand, trial_dir, script_bytes, before, t0, timeout, hash_seed, sample_bytes, keep_output, cancel
):
    work = trial_dir / "work"
    copy_to = work / "prepare.py"  # 试跑目录里的副本（Tavotto 自己的缓存，不是用户脚本）
    copy_to.write_bytes(script_bytes)
    input_path, sample_name = cand.source.path, None
    if sample_bytes is not None and sample_bytes < cand.source.size:
        made = _make_sample(cand.source, trial_dir / "in", sample_bytes)
        input_path, sample_name = str(made), made.name
    out_path = work / OUT_NAME
    res = trial.run_python(
        cand.python,
        script=str(copy_to),
        argv=(input_path, str(out_path)),
        timeout=timeout,
        trial_dir=trial_dir,
        extra_env={"PYTHONHASHSEED": str(hash_seed)},
        strict_deny=strict_roots(cand.project_root),
        tamper="stdlib",
        read_allow=read_allow_for(cand.python, [cand.source.path]),
    )
    run = TrialRun(
        res.get("status", "spawn_failed"),
        res.get("returncode"),
        res.get("stdout") or "",
        res.get("stderr") or "",
        time.monotonic() - t0,
        output_truncated=bool(res.get("output_truncated")),
        sample=sample_name is not None,
    )
    run.blocked = {
        "writes": list(res.get("blocked_writes") or []),
        "spawns": list(res.get("blocked_spawns") or []),
        "network": int(res.get("blocked_network") or 0),
        "reads": int(res.get("blocked_reads") or 0),
        "read_paths": list(res.get("blocked_read_paths") or []),
    }
    if run.status != "ok":
        return run
    run.extra_outputs = _stray_files(trial_dir, sample_name)
    try:
        if identity_of(cand.source.path) != before:
            run.status = "input_changed"
            return run
    except OSError:
        run.status = "input_changed"
        return run
    if out_path.is_symlink():
        run.extra_outputs.append(f"work/{OUT_NAME} (link)")
    elif out_path.is_file():
        run.out_size = out_path.stat().st_size
        if run.out_size <= OUT_MAX_BYTES:
            run.out_sha256 = file_sha256(out_path, cancel)
            run.preview = _preview(out_path)
            if keep_output and run.out_sha256 is not None:
                tmp = cand.dir / (OUT_NAME + ".tmp")
                shutil.copyfile(out_path, tmp)
                os.replace(tmp, cand.out)
                run.out_copy = cand.out
    return run


def _preview(path: Path) -> str:
    """输出表的前几行原文（≤ `PREVIEW_CHARS` 字符）。只套密钥形状规则（这是数据）。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(PREVIEW_CHARS * 4)
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()[:PREVIEW_LINES]
    return readscope.scrub_secret_shapes("\n".join(lines))[:PREVIEW_CHARS]


# --------------------------------------------------------------------------- 契约核对
def _strict_utf8(path: str | os.PathLike) -> bool:
    dec = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                dec.decode(chunk)
            dec.decode(b"", final=True)
    except (UnicodeDecodeError, OSError):
        return False
    return True


def table_facts(path: str | os.PathLike) -> tuple[dict | None, str]:
    """用现有读表读输出：`({"columns", "numeric_columns", "rows"}, "")` 或 `(None, 原因)`。"""
    info = ai_data_figure.read_text_table(path)
    if info.summary is None:
        return None, "table_unreadable"
    s = info.summary
    rows = s.row_count if s.row_count is not None else len(s.rows)
    numeric = [c for c in s.columns if s.per_column.get(c) and s.per_column[c].numeric > 0]
    return {"columns": list(s.columns), "numeric_columns": numeric, "rows": rows}, ""


def judge_basic(run: TrialRun) -> tuple[str, str, dict | None]:
    """一次试跑的**运行级**硬性判据（写出面、退出码、额外产物、报告行、输出文件在不在 / 大小）。通过回 `("", "", {"report"})`。"""
    if run.status == "cancelled":
        return "cancelled", "Cancelled.", None
    if run.status == "timeout":
        return "run_timeout", "The script did not finish within its time limit.", None
    if run.status == "input_changed":
        return "input_changed", "The data file changed after the task started.", None
    if run.status == "script_missing":
        return "script_missing", "Create prepare.py with content first.", None
    if run.status != "ok":
        return "run_unavailable", f"The script could not be run ({run.status}).", None
    b = run.blocked
    if b.get("writes"):
        return (
            "blocked_writes",
            "The script tried to write outside its output file; only the output path may be written.",
            None,
        )
    if b.get("network"):
        return "blocked_network", "The script tried to use the network; there is none.", None
    if b.get("spawns"):
        return (
            "blocked_spawns",
            "The script tried to start another process; that is not allowed.",
            None,
        )
    if b.get("reads"):
        return (
            "blocked_reads",
            "The script tried to read a file other than its input; only the input file can be read.",
            None,
        )
    if run.output_truncated:
        return "output_flood", "The script printed far too much; print only a short summary.", None
    if run.returncode != 0:
        return "run_failed", f"The script exited with code {run.returncode}.", None
    if run.extra_outputs:
        return (
            "extra_output",
            "The script must write exactly one file, the output CSV; other files were written: "
            + ", ".join(sorted(readscope.scrub_secret_shapes(x) for x in run.extra_outputs)[:5]),
            None,
        )
    report, problem = parse_report(run.stdout)
    if report is None:
        return (
            problem,
            "The last line of stdout must be one JSON object with exactly rows_in, rows_out and dropped "
            "(a list of {reason, count}).",
            None,
        )
    if run.out_size is None:
        return "no_output", "The script did not write the output CSV given as sys.argv[2].", None
    if run.out_size > OUT_MAX_BYTES:
        return (
            "output_too_large",
            f"The output table is larger than {OUT_MAX_BYTES // 2**20} MiB.",
            None,
        )
    return "", "", {"report": report}


def judge_run(run: TrialRun, out_path: str | os.PathLike | None) -> tuple[str, str, dict | None]:
    """运行级判据 + 输出表判据（UTF-8、现有读表读得出、≥1 列数值、数据行数 == `rows_out`）。`out_path` 是取回的输出文件。
    通过回 `("", "", {"report", "table"})`。只看事实、不改任何东西。"""
    code, detail, facts = judge_basic(run)
    if code:
        return code, detail, None
    report = facts["report"]
    if out_path is None or not _strict_utf8(out_path):
        return "output_not_utf8", "The output CSV must be valid UTF-8.", None
    table, problem = table_facts(out_path)
    if table is None:
        return (
            problem,
            "The output could not be read as a table (one header row, comma separated).",
            None,
        )
    if not table["numeric_columns"]:
        return "no_numeric_column", "The output table needs at least one numeric column.", None
    if table["rows"] != report["rows_out"]:
        return (
            "rows_mismatch",
            f"The report says rows_out={report['rows_out']} but the table has {table['rows']} data rows.",
            None,
        )
    return "", "", {"report": report, "table": table}


# --------------------------------------------------------------------------- 检查与试跑回调
class Checker:
    """一次任务里父进程这一侧的 `run_script` / `check` 回调（`PrepareHooks` 的实现）。"""

    def __init__(
        self,
        cand: PrepareCandidate,
        *,
        remaining: Callable[[], float],
        cancel: threading.Event | None = None,
    ):
        self.cand = cand
        self.remaining = remaining
        self.cancel = cancel or threading.Event()
        self._map: list[tuple[str, str]] | None = None

    # ---- 文本整理 ----
    def _mapping(self) -> list[tuple[str, str]]:
        m: list[tuple[str, str]] = [
            (self.cand.source.path, f"source/{self.cand.source.label}"),
            (str(self.cand.project_root), "<project>"),
            (str(self.cand.dir), "<candidate>"),
            (str(trial.trial_root()), "<trial>"),
            (str(config.data_dir()), "<tavotto-data>"),
            (os.path.expanduser("~"), "~"),
        ]
        return sorted(m, key=lambda kv: len(kv[0]), reverse=True)

    def _clean(self, text: str, limit: int = OUTPUT_CLIP_CHARS) -> str:
        text = sanitize_output(text, self._mapping())
        # 每次试跑目录的名字（会话名 + 随机后缀）也不给模型看：`<trial>/<目录名>/work/…` → `<trial>/work/…`
        text = re.sub(r"<trial>[/\\][^/\\\s\"']+", "<trial>", text)
        # Windows：占位符后面的路径用正斜杠、行尾统一成 LF，三个平台喂给模型的报错长得一样
        text = re.sub(
            r"(<trial>|<project>|<candidate>|<tavotto-data>)((?:\\[^\\\s\"']+)+)",
            lambda m: m.group(1) + m.group(2).replace("\\", "/"),
            text.replace("\r\n", "\n"),
        )
        return _tail_chars(text, limit)

    # ---- run_script ----
    def run_script(self, args: dict) -> HookReply:
        if self.cancel.is_set():
            return HookReply(False, "Cancelled.", "cancelled")
        room = self.remaining() - 15
        if room < 5:
            return HookReply(False, "No time is left for another run.", "no_time")
        timeout = min(float(args.get("timeout_s") or 120.0), prepare_tools.RUN_TIMEOUT_MAX_S, room)
        run = run_trial(
            self.cand,
            timeout=timeout,
            hash_seed=1,
            sample_bytes=args.get("sample_bytes"),
            cancel=self.cancel,
        )
        return self._reply(run)

    def _reply(self, run: TrialRun) -> HookReply:
        parts = [f"exit {run.returncode}" if run.returncode is not None else f"status {run.status}"]
        if run.sample:
            parts[0] += " (run on the first part of the file only)"
        if run.stdout.strip():
            parts.append("--- stdout ---\n" + self._clean(run.stdout))
        if run.stderr.strip():
            parts.append("--- stderr ---\n" + self._clean(run.stderr))
        b = run.blocked
        notes = [
            f"{k}: {v}"
            for k, v in (
                ("blocked_writes", len(b.get("writes") or [])),
                ("blocked_spawns", len(b.get("spawns") or [])),
                ("blocked_network", b.get("network", 0)),
                ("blocked_reads", b.get("reads", 0)),
            )
            if v
        ]
        if notes:
            parts.append("\n".join(notes))
        if run.extra_outputs:
            parts.append("extra files written (not allowed): " + ", ".join(run.extra_outputs[:5]))
        if run.out_size is not None:
            parts.append(f"--- output table: {run.out_size} bytes ---\n{run.preview}")
        ok = run.status == "ok" and run.returncode == 0
        code = "" if ok else (run.status if run.status != "ok" else "run_failed")
        return HookReply(
            ok,
            "\n".join(parts),
            code,
            "run ok" if ok else f"run failed ({code})",
        )

    # ---- check ----
    def check(self) -> tuple[bool, str]:
        cand = self.cand
        try:
            source = cand.script.read_bytes() if not cand.script.is_symlink() else b""
        except OSError:
            source = b""
        if not source.strip():
            return False, "script_missing: create prepare.py first"
        try:
            ast.parse(source.decode("utf-8", errors="replace"))
        except SyntaxError as exc:
            return False, f"syntax_error: line {exc.lineno}: {exc.msg}"
        if cand.source.identity and identity_of(cand.source.path) != tuple(cand.source.identity):
            return (
                False,
                "check failed (input_changed): The data file changed after the task started.",
            )
        # 输入的 sha256：整任务只算一次
        if "source_sha256" not in cand.extras:
            try:
                digest = file_sha256(cand.source.path, self.cancel)
            except OSError:
                return False, "check failed (input_unreadable): The data file could not be read."
            if digest is None:
                return False, "check failed (cancelled): cancelled"
            cand.extras["source_sha256"] = digest
        # 第一遍：全量，用剩余墙钟
        room = self.remaining() - 20
        if room < FULL_RUN_MIN_S:
            return False, "check failed (no_time): No time is left for a full run."
        cand.checked = None  # 重新核：之前通过的那一份事实作废（out.csv 会被这次的输出替换）
        first = run_trial(cand, timeout=room, hash_seed=1, keep_output=True, cancel=self.cancel)
        code, detail, facts = judge_run(first, first.out_copy)
        if code:
            return False, self._fail(code, detail, first)
        report, table = facts["report"], facts["table"]
        # 第二遍：确定性（不同的 PYTHONHASHSEED）。第一遍太慢（或文件很大）就在前缀样本上比两次
        if first.seconds > DETERMINISM_FULL_S and cand.source.size > SAMPLE_CHECK_BYTES:
            determinism = "sample_only"
            a_run, b_run = (
                run_trial(
                    cand, timeout=max(FULL_RUN_MIN_S, min(room, 120.0)), hash_seed=seed,
                    sample_bytes=SAMPLE_CHECK_BYTES, cancel=self.cancel,
                )
                for seed in (3, 4)
            )  # fmt: skip
            pair = (a_run, b_run)
        else:
            determinism = "full"
            room2 = self.remaining() - 15
            if room2 < 5:
                return False, "check failed (no_time): No time is left for the second run."
            second = run_trial(
                cand, timeout=max(5.0, min(room2, max(FULL_RUN_MIN_S, first.seconds * 3))),
                hash_seed=2, cancel=self.cancel,
            )  # fmt: skip
            pair = (first, second)
        reports = []
        for r in pair[1:] if determinism == "full" else pair:
            code, detail, f2 = judge_basic(r)
            if code:
                return False, self._fail(code, detail, r)
            reports.append(f2["report"])
        if determinism == "full":
            reports.insert(0, report)
        if pair[0].out_sha256 != pair[1].out_sha256 or reports[0] != reports[1]:
            return False, self._fail(
                "nondeterministic",
                "Two runs on the same input gave different output; remove timestamps, random numbers "
                "and any dependence on set or hash ordering (sort before writing).",
                pair[1],
            )
        # 通过：记事实（`verify` / `stage` / `adopt` 只信它）
        cand.checked = {
            "script_sha256": _sha256_bytes(source),
            "out_sha256": first.out_sha256,
            "out_size": first.out_size,
            "report": report,
            "columns": table["columns"],
            "numeric_columns": len(table["numeric_columns"]),
            "rows_out": table["rows"],
            "determinism": determinism,
            "accounting": "reconciled" if reconciled(report) else "not_reconciled",
            "source_sha256": cand.extras["source_sha256"],
        }
        msg = (
            f"check passed: wrote {table['rows']} rows, {len(table['columns'])} columns "
            f"({len(table['numeric_columns'])} numeric); the script reported rows_in={report['rows_in']}, "
            f"rows_out={report['rows_out']}, dropped={json.dumps(report['dropped'], ensure_ascii=True)}; "
            f"two runs gave identical output ({determinism.replace('_', ' ')}); "
            + (
                "rows_in = rows_out + dropped."
                if cand.checked["accounting"] == "reconciled"
                else "rows_in is not rows_out + dropped (fine for a summary; say in your final answer how the rows relate)."
            )
        )
        return True, msg

    def _fail(self, code: str, detail: str, run: TrialRun) -> str:
        text = f"check failed ({code}): {detail}"
        if code in ("run_failed", "report_missing", "report_invalid"):
            if run.stderr.strip():
                text += "\n--- stderr ---\n" + self._clean(run.stderr, 3000)
            if run.stdout.strip():
                text += "\n--- stdout (tail) ---\n" + self._clean(run.stdout, 1500)
        if run.sample:
            text += "\n(this run used the first part of the file only)"
        return text[:6000]


def make_hooks(
    cand: PrepareCandidate,
    *,
    remaining: Callable[[], float],
    cancel: threading.Event | None = None,
) -> PrepareHooks:
    checker = Checker(cand, remaining=remaining, cancel=cancel)
    return PrepareHooks(
        sources={cand.source.label: cand.source},
        run_script=checker.run_script,
        check=checker.check,
        environment_note=probe_environment(cand.python, cand.project_root),
        extras={"checker": checker},
    )


# --------------------------------------------------------------------------- 适配器
def _check(cid: str, stage: str, role: str, verdict: str, reason: str | None = None, **observed):
    return CheckResult(
        id=cid,
        layer="L1",
        stage=stage,
        role=role,
        verdict=verdict,
        reason_code=reason,
        observed=observed,
        kind="persistence" if cid.startswith("project.") else "figure",
    )


_CAND_FILES = frozenset({SCRIPT_NAME, OUT_NAME, META_NAME, OUT_NAME + ".tmp"})


class PrepareAdapter:
    """`data_prepare`：读一份原始数据，在候选里写准备脚本并核契约。**不写任何用户文件**。"""

    task_type = TASK_TYPE

    def validate(self, envelope) -> list[str]:
        problems = []
        if envelope.executor == ai_tasks.DETERMINISTIC:
            problems.append("executor_must_be_agent")
        if envelope.allowed_writes or envelope.new_figures:
            problems.append("no_user_writes")
        refs = envelope.input_refs
        if len(refs) != 1 or refs[0].get("kind") != "data_file":
            problems.append("exactly_one_data_file")
        return problems

    def plan(self, envelope):
        return ai_tasks.Plan(steps=("execute",))

    def verify(self, stage: str, run, cand: PrepareCandidate) -> list[CheckResult]:
        if stage == "precommit":
            return self._precommit(cand)
        if not cand.ready:
            return [_check("commit.staged", stage, "integrity", FAILED, "not_staged")]
        return [_check("commit.staged", stage, "integrity", VERIFIED)]

    def _precommit(self, cand: PrepareCandidate) -> list[CheckResult]:
        st = "precommit"
        facts = cand.checked
        if facts is None:
            return [_check("prepare.contract", st, "goal", FAILED, "prepare_not_checked")]
        out: list[CheckResult] = []
        try:
            script = cand.script.read_bytes() if not cand.script.is_symlink() else None
        except OSError:
            script = None
        if script is None:
            out.append(_check("candidate.target", st, "integrity", FAILED, "target_missing"))
        elif _sha256_bytes(script) != facts["script_sha256"]:
            out.append(
                _check(
                    "candidate.target",
                    st,
                    "integrity",
                    FAILED,
                    "candidate_changed_since_validation",
                )
            )
        else:
            out.append(_check("candidate.target", st, "integrity", VERIFIED))
        try:
            same_out = (
                not cand.out.is_symlink()
                and cand.out.stat().st_size == facts["out_size"]
                and file_sha256(cand.out) == facts["out_sha256"]
            )
        except OSError:
            same_out = False
        out.append(
            _check("prepare.contract", st, "goal", VERIFIED)
            if same_out
            else _check("prepare.contract", st, "goal", FAILED, "prepare_output_changed")
        )
        try:
            names = {p.name for p in cand.dir.iterdir()}
        except OSError:
            names = set()
        extra = sorted(names - _CAND_FILES)
        out.append(
            _check("candidate.tree", st, "integrity", FAILED, "other_file_changed", files=extra[:5])
            if extra
            else _check("candidate.tree", st, "integrity", VERIFIED)
        )
        try:
            unchanged = identity_of(cand.source.path) == tuple(cand.source.identity)
        except OSError:
            unchanged = False
        out.append(
            _check("project.data", st, "integrity", VERIFIED)
            if unchanged
            else _check("project.data", st, "integrity", FAILED, "data_changed")
        )
        return out

    def explain(self, outcome: str, unmet: list[dict]) -> dict:
        return {
            "message_key": f"task.{TASK_TYPE}.{outcome}",
            "params": {"unmet": [u["constraint_id"] for u in unmet]},
        }


ADAPTER = PrepareAdapter()


def ensure_registered() -> None:
    if ai_tasks.ADAPTERS.get(TASK_TYPE) is not ADAPTER:
        ai_tasks.register(ADAPTER)


def envelope_for(
    cand: PrepareCandidate,
    instruction: str,
    *,
    rid: str | None = None,
    executor: str = "builtin",
    wall_s: float = WALL_S,
) -> ai_tasks.TaskEnvelope:
    """任务契约：一份 `data_file` 输入、**不允许写用户文件**。预算：1 次 Agent、不外层修正（循环里 check→改就是修正）、
    `max_renders` 记 check 次数上限。"""
    return ai_tasks.TaskEnvelope.from_dict(
        {
            "schema_version": ai_tasks.SCHEMA_VERSION,
            "task_type": TASK_TYPE,
            "project_ref": {"project_key": str(cand.project_root)},
            "client_request_id": rid,
            "user_intent": instruction,
            "input_refs": [
                {"kind": "data_file", "path_rel": cand.source_rel, "size": cand.source.size}
            ],
            "output_contract": {"allowed_writes": [], "new_figures": False},
            "execution_policy": {"executor": executor},
            "budget": {
                "max_agent_runs": 1,
                "max_corrections": 0,
                "max_renders": MAX_CHECKS,
                "max_wall_s": wall_s,
            },
        }
    )


# --------------------------------------------------------------------------- 执行器
AgentFn = Callable[[PrepareCandidate, PrepareHooks, str, threading.Event], dict]


def inprocess_agent(model, *, limits=None) -> AgentFn:
    """进程内驱动 `data_prepare` 工具集的 `agent` 回调（测试与将来的调试用）：回最后那条 `done` 事件。
    生产里 agent 是 `agent_runner` 子进程（接桥那一期把 hooks 映射到 IPC）。"""

    def run(cand: PrepareCandidate, hooks: PrepareHooks, instruction: str, cancel) -> dict:
        events: list[dict] = []
        envelope = RunEnvelope(
            task_type=TASK_TYPE,
            cwd=cand.dir,
            target="",
            instruction=instruction,
            limits=limits or prepare_tools.PREPARE_LIMITS,
            redactor=NO_REDACTION,
            hooks=hooks,
        )
        loop.run(model, prepare_tools.DataPrepareToolset(), envelope, events.append, cancel)
        return events[-1] if events else {"type": "done", "ok": False, "reason": "internal"}

    return run


class PrepareExecutor:
    """`execute` = 驱动 `agent`；`commit` = 把通过检查的候选留存成 ready（候选目录 + meta.json），**不写任何用户文件**。"""

    executor_id = "builtin"
    kind = "agent"
    writes_user_files = False

    def __init__(self, cand: PrepareCandidate, agent: AgentFn, instruction: str):
        self.cand = cand
        self._agent = agent
        self._instruction = instruction

    def execute(self, run) -> PrepareCandidate:
        cand = self.cand
        try:
            hooks = make_hooks(cand, remaining=run.remaining, cancel=run.cancel_event)
            done = self._agent(cand, hooks, self._instruction, run.cancel_event)
        except BaseException:
            remove_dir(cand.dir)
            raise
        if run.cancelled():
            return cand  # 编排器随后按 cancelled 收场并 discard
        if done.get("ok") is not True:
            remove_dir(cand.dir)
            raise TaskError(f"agent_{done.get('reason') or 'internal'}")
        if cand.checked is None:
            remove_dir(cand.dir)
            raise TaskError("prepare_not_checked")
        return cand

    def commit(self, run, cand: PrepareCandidate) -> None:
        stage(cand)

    def discard(self, run, cand: PrepareCandidate) -> None:
        remove_dir(cand.dir)


# --------------------------------------------------------------------------- 留存 / 只读入口 / 采用
def stage(cand: PrepareCandidate) -> None:
    """通过检查的候选 → `ready`：写 `meta.json`，把 `out.csv` 与 `prepare.py` 置只读（ready 之后不再变）。"""
    facts = cand.checked
    if facts is None:
        raise TaskError("prepare_not_checked")
    st = cand.out.stat()
    meta = {
        "sid": cand.sid,
        "state": "ready",
        "project": os.path.realpath(cand.project_root),
        "source": {
            "label": cand.source.label,
            "size": cand.source.size,
            "identity": list(cand.source.identity),
            "sha256": facts["source_sha256"],
        },
        "script_sha256": facts["script_sha256"],
        "out": {
            "sha256": facts["out_sha256"],
            "size": facts["out_size"],
            "mtime_ns": st.st_mtime_ns,
        },
        "report": facts["report"],
        "columns": facts["columns"],
        "numeric_columns": facts["numeric_columns"],
        "rows_out": facts["rows_out"],
        "determinism": facts["determinism"],
        "accounting": facts["accounting"],
        "created": time.time(),
    }
    atomicio.write_bytes(cand.dir / META_NAME, atomicio.dumps_json(meta))
    for p in (cand.out, cand.script):
        with contextlib.suppress(OSError):
            os.chmod(p, 0o444)
    cand.ready = True


def _load_meta(sid: str) -> tuple[Path, dict]:
    cdir = PREPARE_DIR / _check_sid(sid)
    try:
        meta = json.loads((cdir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PrepareError("prepare_not_found", 404) from None
    if not isinstance(meta, dict) or meta.get("state") != "ready" or meta.get("sid") != sid:
        raise PrepareError("prepare_not_found", 404)
    return cdir, meta


def candidate_table(sid: str) -> Path | None:
    """**只读入口**：候选整理好的表（`out.csv`）的路径，只在状态 `ready` 且文件未变（大小与 mtime 同 `meta`）时回；否则 None。
    给之后接 `ai_data_propose` 在**采用之前**出备选图用；ready 之后 `out.csv` 只读、不再变。调用方不得写它。"""
    try:
        cdir, meta = _load_meta(sid)
        p = cdir / OUT_NAME
        st = p.stat()
        if (
            p.is_symlink()
            or st.st_size != meta["out"]["size"]
            or st.st_mtime_ns != meta["out"]["mtime_ns"]
        ):
            return None
        return p
    except (PrepareError, OSError, KeyError, TypeError):
        return None


def discard(sid: str) -> dict:
    cdir = PREPARE_DIR / _check_sid(sid)
    if not cdir.is_dir():
        raise PrepareError("prepare_not_found", 404)
    remove_dir(cdir)
    return {"discarded": True}


def clean_text(value: object, limit: int = 80) -> str:
    """进 `#` 行 / 文件名位置的文字：不留逗号、引号、控制字符、反斜杠与井号以外的杂质，限长。"""
    text = re.sub(r"[^\w .+()\-/×:=]", "_", str(value), flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text.replace(",", "_")).strip()
    return text[:limit]


def provenance_lines(meta: dict, script_name: str, table_name: str, source_name: str) -> list[str]:
    """采用时写在 CSV 开头的 `#` 行（来源证明）。每行不含逗号 / 引号 / 换行（逗号会被读表当分隔符，让说明行变成数据）。"""
    r = meta["report"]
    dropped = "; ".join(
        f"{clean_text(d['reason'], REASON_MAX)} x {d['count']}" for d in r["dropped"][:10]
    )
    clean_source = clean_text(source_name)
    rerun_input = source_name if clean_source == source_name else "<input file>"
    return [
        "# tavotto-prepared table (made by an AI-written script; the script is not checked for correctness)",
        f"# source: {clean_source}",
        f"# source_bytes: {meta['source']['size']}",
        f"# source_sha256: {meta['source']['sha256']}",
        f"# script: {clean_text(script_name)}",
        f"# script_sha256: {meta['script_sha256']}",
        f"# rows_in: {r['rows_in']} (reported by the script)",
        f"# rows_out: {r['rows_out']}",
        f"# dropped: {dropped or 'none'}",
        f"# accounting: {meta['accounting']}",
        f"# determinism: {meta['determinism']}",
        f"# raw_output_sha256: {meta['out']['sha256']}",
        f"# rerun: python {clean_text(script_name)} {rerun_input} {clean_text(table_name)}",
    ]


def _free_names(figures_dir: Path, stem: str) -> tuple[str, str, str] | None:
    try:
        existing = {p.name.casefold() for p in figures_dir.iterdir()}
    except FileNotFoundError:
        existing = set()
    except OSError:
        return None
    for n in range(1, MAX_NUMBERED + 1):
        suffix = "" if n == 1 else f"_{n}"
        script, table = f"{stem}_prepare{suffix}.py", f"{stem}_prepared{suffix}.csv"
        if script.casefold() not in existing and table.casefold() not in existing:
            return stem, script, table
    return None


def adopt(sid: str, project: str | Path) -> dict:
    """用户采用：把准备脚本（候选字节原样）与整理好的表新建进 `<数据文件夹>/tavotto_figures/`。只新建、永不覆盖；
    任一件已存在 / 写失败就回滚本次已建的（字节仍是自己的才删）。回项目相对 POSIX 路径。画图脚本不在这里。"""
    cdir, meta = _load_meta(sid)
    root = Path(project)
    if os.path.realpath(root) != meta.get("project"):
        raise PrepareError("prepare_not_found", 404)
    script_p, out_p = cdir / SCRIPT_NAME, cdir / OUT_NAME
    try:
        script_bytes = script_p.read_bytes()
        if (
            _sha256_bytes(script_bytes) != meta["script_sha256"]
            or out_p.stat().st_size != meta["out"]["size"]
            or file_sha256(out_p) != meta["out"]["sha256"]
        ):
            raise PrepareError("prepare_candidate_changed", 409)
        table_bytes = out_p.read_bytes()
    except OSError:
        raise PrepareError("prepare_candidate_changed", 409) from None
    src = meta["source"]
    real_src = projectenv.contained_path(root, src["label"])
    try:
        if real_src is None or identity_of(real_src) != tuple(src["identity"]):
            raise PrepareError("data_changed", 409)
    except OSError:
        raise PrepareError("data_changed", 409) from None
    src_path = Path(real_src)
    figures_dir = src_path.parent / FIGURES_DIR
    stem = _STEM_SAFE.sub("_", src_path.stem).strip("_") or "data"
    if figures_dir.is_symlink() or (os.path.lexists(figures_dir) and not figures_dir.is_dir()):
        raise PrepareError("figures_dir_unusable", 409)
    names = _free_names(figures_dir, stem)
    if names is None:
        raise PrepareError("prepare_names_exhausted", 409)
    _stem, script_name, table_name = names
    if len(meta["columns"]) >= 2:  # 单列表的 `#` 行会被读表当数据（列数相同），所以只在 ≥2 列时写
        head = "\n".join(provenance_lines(meta, script_name, table_name, src_path.name)) + "\n"
        table_bytes = head.encode("utf-8") + table_bytes
    script_path, table_path = figures_dir / script_name, figures_dir / table_name
    created: list[tuple[Path, bytes]] = []
    with inputremap.project_mutex(root):
        made_dir = not os.path.lexists(figures_dir)
        try:
            if made_dir:
                try:
                    figures_dir.mkdir()
                except OSError as exc:
                    mapped = ai_data_task.write_failure(exc)
                    if mapped is None:
                        raise
                    raise PrepareError(mapped.code, 409) from None
            try:
                with scriptlock.script_guard(script_path):
                    with scriptlock.script_guard(table_path):
                        for path, data in ((script_path, script_bytes), (table_path, table_bytes)):
                            try:
                                scriptlock.create_script(path, data)
                            except FileExistsError:
                                raise PrepareError("prepare_target_exists", 409) from None
                            created.append((path, data))
            except BaseException as exc:
                for path, data in reversed(created):
                    with contextlib.suppress(OSError):
                        if path.read_bytes() == data:
                            path.unlink()
                if made_dir:
                    with contextlib.suppress(OSError):
                        figures_dir.rmdir()
                if isinstance(exc, OSError):
                    mapped = ai_data_task.write_failure(exc)
                    if mapped is not None:
                        raise PrepareError(mapped.code, 409) from None
                raise
        finally:
            pass
    remove_dir(cdir)
    rel_dir = Path(os.path.relpath(figures_dir, os.path.realpath(root))).as_posix()
    return {"script": f"{rel_dir}/{script_name}", "table": f"{rel_dir}/{table_name}"}
