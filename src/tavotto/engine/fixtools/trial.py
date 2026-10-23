"""「试」组工具（tier=observe）：在指定环境里起一次性子进程试一下，**用户文件一律不动**（ADR 0149 §四）。

子进程封装与体检同一纪律（`projectenv._probe_environment`）：`runtime.probe_args`（`-B`，不往用户解释器目录写 .pyc）、
`runtime.probe_env` + `launcher.activate_env`（Conda 环境按激活后的环境起）、`stdin=DEVNULL`、`creationflags=CREATE_NO_WINDOW`、
`encoding="utf-8"`、超时。cwd 是一次性临时目录；环境只能用会话给出的环境 id 指认，不接受任意解释器路径。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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

_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
RUN_MODES = ("script",)


def valid_dotted_module(name: object) -> bool:
    return isinstance(name, str) and len(name) <= 200 and _MODULE_RE.fullmatch(name) is not None


def run_python(
    python: str,
    args: list[str],
    *,
    timeout: float,
    cwd: str | None = None,
    extra_env: dict | None = None,
) -> dict:
    """在 `python` 里跑一次。回 `{"status": ok|timeout|spawn_failed|env_unavailable, returncode, stdout, stderr}`，不抛。"""
    from .. import launcher, runtime

    scratch = tempfile.mkdtemp(prefix="tavotto-fixtool-") if cwd is None else None
    try:
        try:
            env = launcher.activate_env(python, runtime.probe_env(python), timeout=timeout)
        except launcher.LauncherUnavailable:
            return {"status": "env_unavailable", "returncode": None, "stdout": "", "stderr": ""}
        if extra_env:
            env = dict(os.environ if env is None else env)
            env.update(extra_env)
        try:
            proc = subprocess.run(
                [python, *runtime.probe_args(), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                cwd=cwd or scratch,
                env=env,
                creationflags=runtime.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "returncode": None, "stdout": "", "stderr": ""}
        except (OSError, subprocess.SubprocessError):
            return {"status": "spawn_failed", "returncode": None, "stdout": "", "stderr": ""}
        return {
            "status": "ok",
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


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
    res = run_python(python, ["-c", _IMPORT_SRC, module], timeout=IMPORT_TIMEOUT_S)
    if res["status"] != "ok":
        return err(res["status"], f"The import test did not finish ({res['status']}).")
    if res["returncode"] == 0:
        version = res["stdout"].strip().removeprefix("version=")
        return ok(
            f"import {module}: OK" + (f" (version {version})" if version else ""), "import ok"
        )
    return ok(
        f"import {module}: FAILED (exit {res['returncode']})\n{_tail(res['stderr'])}",
        "import failed",
    )


# --------------------------------------------------------------------------- try_run
_RUN_SRC = r"""
import json, runpy, sys
script, out = sys.argv[1], sys.argv[2]
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
except BaseException:
    import traceback
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
        json.dump({"figures": figs}, f)
except OSError:
    pass
sys.exit(code)
"""


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


def _default_runner(python: str, work: str, script_name: str, mode: str, timeout: float) -> dict:
    out_file = os.path.join(tempfile.mkdtemp(prefix="tavotto-fixrun-"), "result.json")
    try:
        res = run_python(
            python,
            ["-c", _RUN_SRC, script_name, out_file],
            timeout=timeout,
            cwd=work,
            extra_env={"MPLBACKEND": "Agg"},
        )
        figures = None
        try:
            with open(out_file, encoding="utf-8") as f:
                figures = json.load(f).get("figures")
        except (OSError, ValueError):
            pass
        return {**res, "figures": figures}
    finally:
        shutil.rmtree(os.path.dirname(out_file), ignore_errors=True)


def try_run(ctx: FixContext, args: dict) -> ToolResult:
    python = ctx.python_of(args.get("env"))
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    mode = args.get("mode", "script")
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
    work = tempfile.mkdtemp(prefix="tavotto-fixwork-")
    try:
        try:
            name, whole = _copy_project(src_dir, script, Path(work))
        except OSError:
            return err("copy_failed", "Could not make a scratch copy of the script.")
        runner = ctx.runner or _default_runner
        res = runner(python, work, name, mode, timeout)
    finally:
        shutil.rmtree(
            work, ignore_errors=True
        )  # 临时副本里的一切（含脚本写出的文件）随它去；用户目录从未被写
    if res.get("status") != "ok":
        return err(
            str(res.get("status") or "run_failed"),
            f"The trial run did not finish ({res.get('status')}).",
        )
    figures = res.get("figures")
    head = (
        f"trial run: exit code {res.get('returncode')}, figures: "
        f"{figures if figures is not None else 'unknown'}"
        + ("" if whole else " (only the script file was copied; the folder was too large)")
    )
    tail = _tail(res.get("stderr") or "")
    return ok(
        head + (f"\n--- last lines of the error output ---\n{tail}" if tail else ""),
        "trial run done",
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
        "are never touched: it runs in a temporary copy that is deleted afterwards.",
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
