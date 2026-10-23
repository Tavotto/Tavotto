"""「试」组工具（tier=observe）：在指定环境里起一次性子进程试一下，**用户文件一律不动**（ADR 0149 §四）。

子进程封装与体检同一纪律（`projectenv._probe_environment`）：`runtime.probe_args`（`-B`，不往用户解释器目录写 .pyc）、
`runtime.probe_env` + `launcher.activate_env`（Conda 环境按激活后的环境起）、`stdin=DEVNULL`、`creationflags=CREATE_NO_WINDOW`、
`encoding="utf-8"`、超时。环境只能用会话给出的环境 id 指认，不接受任意解释器路径。

**fixtools 起的每一个子进程只有一条执行路径 `run_python`**，它都在 **safe 的写入根绊线**（`engine/writetripwire.py`，ADR 0141，worker 的
safe 执行同一个守卫）下跑目标代码：`try_run` 的脚本、`try_import` 的 import（模块的导入副作用）、`inspect_environment` 的探测代码一视同仁。
写入根只有本次专用的缓存子目录（`data_dir()/cache/fix_trials/<会话>-<随机>/`，里面是副本 `work/`、专用 TMPDIR、matplotlib 配置目录）；
读不限。守卫装不上（解释器 < 3.8 等）就拒绝执行（`trial_unguarded`），不退化成裸跑。临时目录一律建在缓存目录里（不用系统临时目录），
清理走 `_remove_tree` 重试 + `.cleanup_pending` 标记 + `sweep_stale_trials()`。子进程输出只留有上限的尾部，总量超限即结束子进程。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from ..agent_runner.wires import ToolResult
from .base import FixContext, FixTool, err, obj, ok

INSPECT_TIMEOUT_S = 40.0
IMPORT_TIMEOUT_S = 40.0
RUN_TIMEOUT_DEFAULT_S = 90.0
RUN_TIMEOUT_MAX_S = 300.0
ERROR_TAIL_LINES = 40
#: try_run 复制项目目录的上限；超了只复制脚本一个文件
COPY_MAX_BYTES = 64 * 1024 * 1024
COPY_MAX_FILES = 5000
_COPY_SKIP = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".hg", ".svn"})

#: 子进程输出：每路只在内存里留尾部这么多字节；两路合计超过 OUTPUT_LIMIT_BYTES 就结束子进程（`output_truncated`）
OUTPUT_TAIL_BYTES = 64 * 1024
OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
#: 试跑副本的清理：重试次数 / 间隔（Windows 上子进程或扫描器还占着文件）；残留目录超过这个年龄被 `sweep_stale_trials` 清
CLEANUP_ATTEMPTS = 4
CLEANUP_DELAY_S = 0.5
STALE_TRIAL_AGE_S = 3600.0
PENDING_MARKER = ".cleanup_pending"

_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
RUN_MODES = ("script",)


def valid_dotted_module(name: object) -> bool:
    return isinstance(name, str) and len(name) <= 200 and _MODULE_RE.fullmatch(name) is not None


class _Capture:
    """一路子进程输出：只留尾部 `OUTPUT_TAIL_BYTES` 字节，另外数总字节；两路共用一个总量计数，超限就叫停。"""

    def __init__(self, stream, shared: dict, lock: threading.Lock, on_overflow) -> None:
        self.tail: deque[bytes] = deque()
        self.tail_len = 0
        self.total = 0
        self._stream, self._shared, self._lock, self._on_overflow = (
            stream,
            shared,
            lock,
            on_overflow,
        )
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        try:
            while True:
                chunk = self._stream.read1(65536) if hasattr(self._stream, "read1") else b""
                if not chunk:
                    return
                self.total += len(chunk)
                self.tail.append(chunk)
                self.tail_len += len(chunk)
                while self.tail_len - len(self.tail[0]) >= OUTPUT_TAIL_BYTES:
                    self.tail_len -= len(self.tail.popleft())
                with self._lock:
                    self._shared["bytes"] += len(chunk)
                    over = self._shared["bytes"] > OUTPUT_LIMIT_BYTES
                if over:
                    self._on_overflow()
                    return
        except (OSError, ValueError):
            return

    def text(self) -> str:
        data = b"".join(self.tail)[-OUTPUT_TAIL_BYTES:]
        return data.decode("utf-8", errors="replace")


def _kill_own(proc: subprocess.Popen) -> None:
    """只结束**自己起的**子进程（POSIX 连它的进程组，那是 `start_new_session` 给它单开的）；绝不按名字杀。"""
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


GUARD_EXIT_CODE = 97
_GUARDED_SRC = r"""
import json, os, sys
guard, root, out, target = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
# 先把后面要用的标准库导进来，再装守卫：守卫只管「写」，读不受限
import runpy, traceback, importlib.util, tempfile, re, threading, errno
try:
    spec = importlib.util.spec_from_file_location("_tavotto_trial_guard", guard)
    wt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wt)
    wt.install([root])
    wt.arm(True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"guarded": True}, f)
except BaseException:
    sys.exit(97)
sys.modules.pop("_tavotto_trial_guard", None)
code = 0
sys.argv = [target] + sys.argv[5:]
try:
    runpy.run_path(target, run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
except BaseException:
    traceback.print_exc()
    code = 1
figs = None
if "matplotlib.pyplot" in sys.modules:
    try:
        figs = len(sys.modules["matplotlib.pyplot"].get_fignums())
    except Exception:
        figs = None
try:
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"guarded": True, "figures": figs, "blocked_writes": wt.violations()}, f)
except OSError:
    pass
sys.exit(code)
"""


def _guard_path() -> str:
    """worker 的 safe 执行用的写入根绊线（`engine/writetripwire.py`，纯标准库）的文件路径；子进程按文件加载它。"""
    from .. import writetripwire

    return str(Path(writetripwire.__file__).resolve())


def run_python(
    python: str,
    *,
    timeout: float,
    code: str | None = None,
    script: str | None = None,
    argv: tuple[str, ...] = (),
    session_id: str = "fixtool",
    trial_dir: Path | None = None,
    extra_env: dict | None = None,
) -> dict:
    """fixtools 唯一的起 Python 子进程的入口：目标（`code` 源码，或 `trial_dir/work` 里的 `script`）一律在写入根绊线下执行。

    写入根 = 本次专用目录 `trial_dir`（调用方给，如 `try_run` 把副本放在里面并自己清理；不给就在缓存目录里新建、结束时清理，
    清不掉如实回 `cleanup_pending`）。回 `{"status": ok|timeout|spawn_failed|env_unavailable|trial_unguarded|scratch_failed,
    returncode, stdout, stderr, output_truncated, figures, blocked_writes, cleanup_pending}`，不抛。stdout / stderr 只是**尾部**
    （各 `OUTPUT_TAIL_BYTES`）；总输出超 `OUTPUT_LIMIT_BYTES` 即结束子进程。"""
    empty = {
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "output_truncated": False,
        "figures": None,
        "blocked_writes": [],
        "cleanup_pending": False,
    }
    own = trial_dir is None
    if own:
        try:
            sweep_stale_trials()
            trial_dir = _make_trial_dir(session_id)
        except OSError:
            return {"status": "scratch_failed", **empty}
    assert trial_dir is not None
    try:
        res = _run_guarded(python, trial_dir, timeout, code, script, argv, extra_env, empty)
    finally:
        pending = not _remove_tree(trial_dir) if own else False
    if own:
        res["cleanup_pending"] = pending
    return res


def _run_guarded(python, trial_dir, timeout, code, script, argv, extra_env, empty) -> dict:
    from .. import launcher, runtime

    out_file = str(trial_dir / "out" / "result.json")
    target = script
    if code is not None:
        src = trial_dir / "out" / "target.py"
        try:
            src.write_text(code, encoding="utf-8")
        except OSError:
            return {"status": "scratch_failed", **empty}
        target = str(src)
    if target is None:
        return {"status": "spawn_failed", **empty}
    tmp = str(trial_dir / "tmp")
    work = str(trial_dir / "work")
    try:
        env = launcher.activate_env(python, runtime.probe_env(python), timeout=timeout)
    except launcher.LauncherUnavailable:
        return {"status": "env_unavailable", **empty}
    env = dict(os.environ if env is None else env)
    env.update(MPLBACKEND="Agg", TMPDIR=tmp, TEMP=tmp, TMP=tmp, MPLCONFIGDIR=str(trial_dir / "mpl"))
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.Popen(
            [
                python,
                *runtime.probe_args(),
                "-c",
                _GUARDED_SRC,
                _guard_path(),
                str(trial_dir),
                out_file,
                target,
                *argv,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=work,
            env=env,
            creationflags=runtime.CREATE_NO_WINDOW,
            start_new_session=(os.name != "nt"),
        )
    except (OSError, subprocess.SubprocessError):
        return {"status": "spawn_failed", **empty}
    overflow = threading.Event()

    def stop() -> None:
        overflow.set()
        _kill_own(proc)

    shared = {"bytes": 0}
    lock = threading.Lock()
    out = _Capture(proc.stdout, shared, lock, stop)
    err_ = _Capture(proc.stderr, shared, lock, stop)
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_own(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    for cap in (out, err_):
        cap.thread.join(timeout=5)
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except OSError:
            pass
    if timed_out and not overflow.is_set():
        return {"status": "timeout", **empty}
    info: dict = {}
    try:
        with open(out_file, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        pass
    if proc.returncode == GUARD_EXIT_CODE or not info.get("guarded"):
        return {"status": "trial_unguarded", **empty}  # 守卫没装上：不报成一次正常执行
    return {
        "status": "ok",
        "returncode": proc.returncode,
        "stdout": out.text(),
        "stderr": err_.text(),
        "output_truncated": overflow.is_set(),
        "output_bytes": out.total + err_.total,
        "figures": info.get("figures"),
        "blocked_writes": info.get("blocked_writes") or [],
        "cleanup_pending": False,
    }


def guard_note(res: dict) -> str:
    """给 `run_python` 结果的守卫相关附注：被拦的越界写、清不掉的残留。"""
    note = ""
    blocked = res.get("blocked_writes") or []
    if blocked:
        shown = ", ".join(f"{b.get('event')} {b.get('path')}" for b in blocked[:8])
        note += (
            f"\nblocked_writes: {len(blocked)} attempt(s) to write outside the scratch folder were "
            f"blocked and nothing was written there ({shown})"
        )
    if res.get("cleanup_pending"):
        note += (
            "\ncleanup_pending: the scratch folder could not be fully deleted; it is kept in Tavotto's cache "
            "folder and will be removed later."
        )
    return note


def _tail(text: str, n: int = ERROR_TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


# --------------------------------------------------------------------------- try_import
_IMPORT_SRC = (
    "import importlib, sys\n"
    "m = importlib.import_module(sys.argv[1])\n"
    "print('version=' + str(getattr(m, '__version__', '')))\n"
)


def try_import(ctx: FixContext, args: dict) -> ToolResult:
    python = ctx.python_of(args.get("env"))
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    module = args.get("module")
    if not valid_dotted_module(module):
        return err(
            "bad_module", "`module` must be a dotted Python identifier such as `scipy.optimize`."
        )
    res = run_python(
        python,
        code=_IMPORT_SRC,
        argv=(module,),
        timeout=IMPORT_TIMEOUT_S,
        session_id=ctx.session_id,
    )
    note = guard_note(res)
    if res["status"] != "ok":
        return err(res["status"], f"The import test did not finish ({res['status']}).{note}")
    if res["returncode"] == 0:
        version = res["stdout"].strip().removeprefix("version=")
        return ok(
            f"import {module}: OK" + (f" (version {version})" if version else "") + note,
            "import ok",
        )
    return ok(
        f"import {module}: FAILED (exit {res['returncode']})\n{_tail(res['stderr'])}{note}",
        "import failed",
    )


# --------------------------------------------------------------------------- try_run
def _copy_project(src_dir: Path, script: Path, dest: Path) -> tuple[str, bool]:
    """把脚本所在目录复制到 `dest`（有上限，超了只复制脚本）。回 (脚本在 dest 里的相对名, 是否整目录复制)。"""
    total = files = 0
    plan: list[Path] = []
    for root, dirs, names in os.walk(src_dir):
        dirs[:] = [
            d for d in dirs if d not in _COPY_SKIP and not os.path.islink(os.path.join(root, d))
        ]
        for n in names:
            p = Path(root, n)
            if p.is_symlink():
                continue
            try:
                total += p.stat().st_size
            except OSError:
                continue
            files += 1
            plan.append(p)
            if total > COPY_MAX_BYTES or files > COPY_MAX_FILES:
                shutil.copy2(script, dest / script.name)
                return script.name, False
    for p in plan:
        target = dest / p.relative_to(src_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return str(script.relative_to(src_dir)), True


def trial_root() -> Path:
    """试跑副本的家：Tavotto 自己的缓存目录，不放系统临时目录（清不掉的残留要找得到、扫得到）。"""
    from ..config import data_dir

    return data_dir() / "cache" / "fix_trials"


def _rmtree_once(path: Path) -> None:
    """删一棵树，尽量多删；有任何一处删不掉就在最后抛 OSError。"""
    first: list[BaseException] = []

    def on_err(func, p, exc):  # noqa: ANN001
        e = exc if isinstance(exc, BaseException) else exc[1]
        try:  # Windows 只读文件：改成可写再试一次
            os.chmod(p, 0o700)
            func(p)
            return
        except OSError:
            pass
        first.append(e)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=on_err)
    else:
        shutil.rmtree(path, onerror=lambda f, p, ei: on_err(f, p, ei[1]))
    if first:
        raise OSError(f"could not remove {path}") from first[0]


def _remove_tree(path: Path, *, attempts: int | None = None) -> bool:
    """有限次重试地删；成功（含本来就不在）回 True。仍失败：在目录里留 `.cleanup_pending` 标记，回 False。"""
    n = CLEANUP_ATTEMPTS if attempts is None else attempts
    for i in range(max(1, n)):
        try:
            _rmtree_once(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if i + 1 < n:
                time.sleep(CLEANUP_DELAY_S)
    try:
        (path / PENDING_MARKER).write_text("cleanup pending\n", encoding="utf-8")
    except OSError:
        pass
    return not path.exists()


def sweep_stale_trials(*, max_age_s: float | None = None, now: float | None = None) -> int:
    """清残留的试跑副本：有 `.cleanup_pending` 标记的，或超过 `STALE_TRIAL_AGE_S` 没动过的。回清掉的个数。

    挂点：`try_run` 每次开头调一次（fixtools 首次使用时就扫）。在途的试跑目录既没标记又很新，不会被误删。"""
    age = STALE_TRIAL_AGE_S if max_age_s is None else max_age_s
    now = time.time() if now is None else now
    root = trial_root()
    removed = 0
    try:
        entries = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
    except OSError:
        return 0
    for d in entries:
        try:
            stale = (d / PENDING_MARKER).exists() or now - d.stat().st_mtime > age
        except OSError:
            continue
        if stale and _remove_tree(d, attempts=1):
            removed += 1
    return removed


def _make_trial_dir(session_id: str) -> Path:
    root = trial_root()
    root.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id))[:40] or "s"
    d = Path(tempfile.mkdtemp(prefix=f"{safe}-", dir=root))  # 随机后缀，0700
    for sub in ("work", "tmp", "mpl", "out"):
        (d / sub).mkdir()
    return d


def _default_runner(python: str, work: str, script_name: str, mode: str, timeout: float) -> dict:
    """在 safe 的写入根绊线下跑副本。`work` 是 `<试跑目录>/work`；写入根 = 整个试跑目录（副本 + 专用 TMPDIR + mpl 配置）。"""
    return run_python(
        python,
        script=str(Path(work) / script_name),
        timeout=timeout,
        trial_dir=Path(work).parent,
    )


def try_run(ctx: FixContext, args: dict) -> ToolResult:
    python = ctx.python_of(args.get("env"))
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    mode = args.get("mode", "script")
    if mode == "native":
        return err(
            "native_trial_not_observe",
            "Trial runs in the user's own environment are not available at this level.",
        )
    if mode not in RUN_MODES:
        return err("bad_arguments", f"`mode` must be one of {list(RUN_MODES)}.")
    script = ctx.script_path
    if script is None or not Path(script).is_file():
        return err("no_script", "There is no script to run in this session.")
    timeout = args.get("timeout_s")
    timeout = (
        float(min(max(timeout, 5), RUN_TIMEOUT_MAX_S))
        if isinstance(timeout, int | float) and not isinstance(timeout, bool)
        else RUN_TIMEOUT_DEFAULT_S
    )
    script = Path(script).resolve()
    src_dir = script.parent
    try:
        sweep_stale_trials()
        trial_dir = _make_trial_dir(ctx.session_id)
    except OSError:
        return err("copy_failed", "Could not make a scratch copy of the script.")
    pending = False
    try:
        try:
            name, whole = _copy_project(src_dir, script, trial_dir / "work")
        except OSError:
            return err("copy_failed", "Could not make a scratch copy of the script.")
        runner = ctx.runner or _default_runner
        res = runner(python, str(trial_dir / "work"), name, mode, timeout)
    finally:
        # 副本里的一切（含脚本写出的文件）随它去；用户目录从未被写。删不掉（Windows 文件占用）就如实报 cleanup_pending
        pending = not _remove_tree(trial_dir)
    note = (
        "\ncleanup_pending: the scratch copy could not be fully deleted; it is kept in Tavotto's cache "
        "folder and will be removed later."
        if pending
        else ""
    )
    if res.get("status") == "trial_unguarded":
        return err(
            "trial_unguarded",
            "The write guard could not be enabled for this environment, so the trial run was refused."
            + note,
        )
    if res.get("status") != "ok":
        return err(
            str(res.get("status") or "run_failed"),
            f"The trial run did not finish ({res.get('status')})." + note,
        )
    figures = res.get("figures")
    head = (
        f"trial run: exit code {res.get('returncode')}, figures: "
        f"{figures if figures is not None else 'unknown'}"
        + ("" if whole else " (only the script file was copied; the folder was too large)")
    )
    extra = ""
    blocked = res.get("blocked_writes") or []
    if blocked:
        shown = ", ".join(f"{b.get('event')} {b.get('path')}" for b in blocked[:8])
        extra += (
            f"\nblocked_writes: {len(blocked)} attempt(s) to write outside the scratch copy were "
            f"blocked and nothing was written there ({shown})"
        )
    if res.get("output_truncated"):
        extra += (
            f"\noutput_truncated: the script printed more than {OUTPUT_LIMIT_BYTES // (1024 * 1024)} MiB, "
            "so it was stopped; only the last part of the output is kept"
        )
    tail = _tail(res.get("stderr") or "")
    summary = "trial run done" + (" (writes blocked)" if blocked else "")
    summary += " (cleanup_pending)" if pending else ""
    return ok(
        head + extra + note + (f"\n--- last lines of the error output ---\n{tail}" if tail else ""),
        summary,
    )


# --------------------------------------------------------------------------- 登记
_ENV = {"type": "string", "description": "Environment id from list_environments."}

TRIAL_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "try_import",
        "Check whether a module imports in one environment, in a throw-away process (nothing is installed or "
        "changed). Returns OK with the version, or the last lines of the error.",
        obj(
            {
                "env": _ENV,
                "module": {
                    "type": "string",
                    "description": "Dotted module name, e.g. `scipy.optimize`.",
                },
            },
            ["env", "module"],
        ),
        try_import,
    ),
    FixTool(
        "try_run",
        "Run a copy of the user's script once in one environment with a non-interactive matplotlib backend, and "
        "report the exit code, the number of figures and the last 40 lines of the error output. The user's files "
        "are never touched: it runs in a temporary copy that is deleted afterwards, and any attempt to write "
        "outside that copy is blocked and reported.",
        obj(
            {
                "env": _ENV,
                "mode": {"type": "string", "enum": list(RUN_MODES)},
                "timeout_s": {
                    "type": "integer",
                    "description": "Seconds before giving up (default 90, max 300).",
                },
            },
            ["env"],
        ),
        try_run,
    ),
)
