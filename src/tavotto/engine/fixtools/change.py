"""「改」组工具（ADR 0149 §七）：装包 / 切运行方式 / 换 Python / 换环境 / 跑命令 / 转写 / 回滚。

**每个动作的执行都在自己的出处里**，本模块只做「守门 → 问用户 → 打快照 → 调出处」：

* 装 / 卸包：`deprepair._pip_install` / `_pip_uninstall`（同一份 argv 出处、只装 wheel、镜像回退、取消）+ `pool.mutating_environment`
  （环境独占锁，池里用这个解释器的 worker 先收掉）。需求串先过 `depresolve.parse_requirement`（`-r` / URL / 路径 / 带标记的都死在那里）。
  系统自带 / 内置 runtime 的解释器永不装（`convertdeps.describe_env` 同一口径）。「常见大包」名单 = `convertdeps.in_allowlist`
  （`autoinstall_dists.txt`，PyPI 公开下载量生成）：名单内不问，名单外与 `run_command` / `select_environment` 经 `ctx.ask` 问用户。
* 切运行方式：`execpolicy.set_mode`（项目级 `execution_policy`；`safe` = `safe_only`，`native` = `compatible`，即允许 safe 被自己的守卫拦下时回退 native）。
* 换环境：`projectenv.remember(..., automatic=False)`（与界面「选择运行环境」同一个写入）。
* 换 Python / 转写：执行器由 `FixContext` 注入（`python_changer` / `script_converter`），没有就 `unavailable`；本模块不自己造。
* 跑命令：不经 shell（`shlex` 拆）、cwd=项目、去掉密钥类环境变量、有超时、输出有界并脱敏；先过**硬拦表**（问都不问）。

所有会改东西的工具在执行前打快照（`snapshots.py`），快照 id 进审计行与工具返回，`rollback` 用它恢复。
**没有 `ctx.ask`（无界面）= 未获同意**：需要点头的动作一律 `needs_approval`，审计记 `blocked`。
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from ..agent_runner.wires import ToolResult
from . import executors, snapshots
from .base import FixContext, FixTool, err, obj, ok
from .readscope import scrub

PACKAGES_MAX = 20
COMMAND_TOKENS_MAX = 64
COMMAND_TOKEN_CHARS = 4096
COMMAND_TIMEOUT_DEFAULT_S = 60.0
COMMAND_TIMEOUT_MAX_S = 300.0
#: 命令输出给模型看的最大行数（字节另有 `trial.OUTPUT_TAIL_BYTES` 的尾部上限）
OUTPUT_LINES = 200

_VERSION_RE = re.compile(r"3\.\d{1,2}(\.\d{1,2})?")

# --------------------------------------------------------------------------- 询问用户
_NO_ASK = "This needs the user's approval, and no approval could be asked for in this session, so nothing was done."
_DECLINED = "The user did not approve this action, so nothing was done."


def approve(ctx: FixContext, tool: str, summary: str, **extra) -> ToolResult | None:
    """问用户点头。通过回 None（并在 `ctx.call_meta` 记 `approved_by=user`），否则回 `needs_approval` 错误。

    卡片里的文字先脱敏。没有 `ctx.ask`、用户说不、超时、`ask` 自己抛了，一律按未获同意。"""
    if ctx.ask is None:
        return err("needs_approval", _NO_ASK)
    card = {"kind": "approve", "tool": tool, "summary": scrub(summary)[:600], **extra}
    try:
        answer = ctx.ask(card)
    except Exception:  # noqa: BLE001 - 界面那头出任何问题都不能当作同意
        return err("needs_approval", _DECLINED)
    if isinstance(answer, dict) and answer.get("answer") == "yes":
        ctx.call_meta["approved_by"] = "user"
        return None
    return err("needs_approval", _DECLINED)


def _snapshot_or_error(
    ctx: FixContext, tool: str, env_ids: list[str] | None = None
) -> ToolResult | None:
    """改动前打快照；打不出来就不改（没有退路的改动不做）。"""
    if snapshots.take(ctx, tool, env_ids) is None:
        return err(
            "snapshot_failed", "Could not save a snapshot to roll back to, so nothing was changed."
        )
    return None


def _with_snapshot(ctx: FixContext, text: str) -> str:
    snap = ctx.call_meta.get("snapshot")
    return f"{text}\nsnapshot: {snap} (use rollback to undo)" if snap else text


def _done(ctx: FixContext, text: str, summary: str) -> ToolResult:
    res = ok(_with_snapshot(ctx, text), summary)
    res.mutated = True
    return res


def _forget_env_caches(ctx: FixContext) -> None:
    from .. import projectenv

    try:
        projectenv.reset_cache(ctx.project_root)  # 装过包：此前的体检结论作废
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- install_packages
def install_packages(ctx: FixContext, args: dict) -> ToolResult:
    from .. import convertdeps, depresolve

    python = ctx.python_of(args.get("env"))
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    raw = args.get("packages")
    if (
        not isinstance(raw, list)
        or not raw
        or len(raw) > PACKAGES_MAX
        or not all(isinstance(x, str) for x in raw)
    ):
        return err(
            "bad_arguments",
            f"`packages` must be a list of 1 to {PACKAGES_MAX} requirement strings such as `seaborn` or `numpy==1.26.4`.",
        )
    reqs: list[str] = []
    names: list[str] = []
    for item in raw:
        parsed = depresolve.parse_requirement(item.strip())
        if parsed is None:
            return err(
                "bad_requirement",
                "Only `name`, `name==1.2.3`, `name>=1.2` (optionally with `,<2`) are accepted: "
                "no options, URLs, paths, extras or markers.",
            )
        reqs.append(f"{parsed[0]}{parsed[1]}")
        names.append(parsed[0])
    kind = convertdeps.describe_env(python, "")["kind"]
    if kind in (convertdeps.KIND_SYSTEM, convertdeps.KIND_BUILTIN):
        return err(
            "env_not_installable",
            "Packages are never installed into the operating system's Python or Tavotto's built-in runtime. "
            "Choose another environment (list_environments) or create one with change_python.",
        )
    outside = sorted({n for n in names if not convertdeps.in_allowlist(n)})
    if outside:  # 名单外 = user_env：一律问
        denied = approve(
            ctx,
            "install_packages",
            f"Install {', '.join(reqs)} into a Python environment on this computer. "
            f"{', '.join(outside)} is not on Tavotto's list of common packages.",
            packages=reqs,
            outside_list=outside,
        )
        if denied is not None:
            return denied
    bad = _snapshot_or_error(ctx, "install_packages", [str(args.get("env"))])
    if bad is not None:
        return bad
    res = executors.run_installer(ctx, python, reqs)
    _forget_env_caches(ctx)
    if not res.get("ok"):
        done = [d for d in (res.get("done") or []) if isinstance(d, str)]
        return err(
            str(res.get("code") or "install_failed")[:60],
            _with_snapshot(
                ctx,
                "The installation did not finish"
                + (f" (installed before it stopped: {', '.join(done)})" if done else "")
                + ".",
            ),
        )
    return _done(ctx, f"Installed: {', '.join(reqs)}.", "packages installed")


# --------------------------------------------------------------------------- switch_execution
_EXECUTION_MODES = {"safe": "safe_only", "native": "compatible"}


def switch_execution(ctx: FixContext, args: dict) -> ToolResult:
    from .. import execpolicy

    mode = args.get("mode")
    if mode not in _EXECUTION_MODES:
        return err("bad_arguments", "`mode` must be `safe` or `native`.")
    target = _EXECUTION_MODES[mode]
    root = str(ctx.project_root)
    current = execpolicy.mode_for(root)
    if current == target:
        return ok(f"Already set: {mode}. Nothing changed.", "no change")
    if execpolicy.decided(root) and current == execpolicy.MODE_SAFE_ONLY:
        # 用户明确选过「只用安全运行」：放宽它是推翻用户的决定，要问
        denied = approve(
            ctx,
            "switch_execution",
            "Allow this project to run natively when the safe mode cannot do what the script needs? "
            "You had chosen to only use the safe mode.",
        )
        if denied is not None:
            return denied
    bad = _snapshot_or_error(ctx, "switch_execution")
    if bad is not None:
        return bad
    try:
        execpolicy.set_mode(root, target)
    except (OSError, ValueError):
        return err("settings_not_saved", "The project setting could not be saved.")
    note = (
        "The project now only runs in the safe mode."
        if mode == "safe"
        else "The project may now run natively (on your own Python, with your files) when the safe mode is "
        "blocked by its own guard."
    )
    return _done(ctx, note, f"execution set to {mode}")


# --------------------------------------------------------------------------- change_python
def change_python(ctx: FixContext, args: dict) -> ToolResult:
    version = args.get("version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version.strip()):
        return err("bad_arguments", "`version` must look like `3.12` or `3.12.4`.")
    if ctx.python_changer is None:
        return err(
            "unavailable",
            "Creating a Tavotto environment with another Python version is not available yet.",
        )
    bad = _snapshot_or_error(ctx, "change_python")
    if bad is not None:
        return bad
    res = executors.call_executor(
        ctx.python_changer, {"version": version.strip(), "project": str(ctx.project_root)}
    )
    if not res.get("ok"):
        return err(
            str(res.get("code") or "change_python_failed")[:60],
            _with_snapshot(
                ctx, str(res.get("message") or "The new environment could not be created.")
            ),
        )
    text = str(res.get("message") or f"Created a Tavotto environment with Python {version}.")
    python = res.get("python")
    if isinstance(python, str) and python:
        from .. import userenvs

        eid = userenvs.env_id(python)
        ctx.environments[eid] = (
            python  # 新环境的 id：之后 inspect_environment / install_packages 可以指认
        )
        text += f"\nnew environment id: {eid}"
    _forget_env_caches(ctx)
    return _done(ctx, text, f"python {version}")


# --------------------------------------------------------------------------- select_environment
def select_environment(ctx: FixContext, args: dict) -> ToolResult:
    from .. import projectenv

    env = args.get("env")
    python = ctx.python_of(env)
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    if not os.path.isfile(python):
        return err("env_missing", "That environment's Python is no longer there.")
    denied = approve(
        ctx,
        "select_environment",
        "Make this project use another Python environment that already exists on this computer.",
        env=str(env),
    )
    if denied is not None:
        return denied
    bad = _snapshot_or_error(ctx, "select_environment")
    if bad is not None:
        return bad
    saved = projectenv.remember(
        ctx.project_root,
        python,
        automatic=False,
        trigger="user_selected",
        env_overlay=ctx.environment_overlays.get(str(env)),
    )
    if not saved:
        return err("settings_not_saved", "The project setting could not be saved.")
    return _done(ctx, "This project now uses that environment.", "environment selected")


# --------------------------------------------------------------------------- run_command
_SHELLS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "cmd", "powershell", "pwsh"}
)
_PRIVILEGE = frozenset({"sudo", "doas", "su", "runas", "pkexec"})
_DISK = frozenset(
    {
        "diskpart",
        "format",
        "fdisk",
        "sfdisk",
        "parted",
        "mkswap",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
    }
)
_RECURSIVE_TOOLS = frozenset({"rm", "rmdir", "rd", "del", "erase", "chmod", "chown", "chgrp"})
_PIPE_TO_SHELL = re.compile(
    r"\|\s*(?:sudo\s+)?(?:\S*[/\\])?(?:sh|bash|zsh|dash|ksh|csh|tcsh|fish|cmd|powershell|pwsh)(?:\.exe)?\b",
    re.IGNORECASE,
)
_SHELL_SYNTAX = frozenset({"|", "||", "&", "&&", ";", ">", ">>", "<", "2>", "2>&1"})
_SECRET_ENV = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|_PWD$|CREDENTIAL|AUTH|COOKIE|SIGNATURE|PRIVATE)",
    re.IGNORECASE,
)


def _program_name(argv0: str) -> str:
    base = os.path.basename(argv0.replace("\\", "/")).lower()
    return base[:-4] if base.endswith(".exe") else base


def _expand(ctx: FixContext, token: str) -> str:
    if token == "~" or token.startswith(("~/", "~\\")):
        return str(ctx.home) + token[1:]
    return token


def _dangerous_root(ctx: FixContext, token: str) -> bool:
    """这个删除 / 改权限的目标是不是根目录、盘符根、主目录或主目录的上级。"""
    raw = token.strip("\"'")
    if raw in ("*", "/*", "~/*", "\\*"):
        return True
    expanded = _expand(ctx, raw)
    if not os.path.isabs(expanded):
        expanded = os.path.join(
            str(ctx.project_root), expanded
        )  # 相对路径相对项目（命令的 cwd），不是本进程的 cwd
    path = os.path.normcase(os.path.abspath(expanded))
    home = os.path.normcase(os.path.abspath(str(ctx.home)))
    anchor = os.path.normcase(os.path.splitdrive(path)[0] + os.sep)
    if path == anchor or path == os.path.normcase(os.sep):
        return True
    if raw.startswith("$HOME") or raw.startswith("%USERPROFILE%"):
        return True
    return path == home or home.startswith(path.rstrip(os.sep) + os.sep)


def hard_block_reason(ctx: FixContext, argv: list[str], text: str) -> str | None:
    """硬拦表（方案 HARD_BLOCK）：命中就不执行、也不问用户。回理由（给模型看，不含命令内容），不命中回 None。"""
    prog = _program_name(argv[0])
    if prog in _PRIVILEGE:
        return "commands that raise privileges (sudo and the like) are never run"
    if prog in _DISK or prog == "mkfs" or prog.startswith("mkfs."):
        return "disk formatting, partitioning and power commands are never run"
    if prog in _SHELLS:
        return "shells are not started; run the program itself with its arguments"
    if _PIPE_TO_SHELL.search(text):
        return "piping output into a shell is never run"
    if any(t in _SHELL_SYNTAX for t in argv) or any("$(" in t or "`" in t for t in argv):
        return "shell syntax (pipes, redirects, ;, &&, substitutions) is not supported; run one program with its arguments"
    if prog == "dd" and any(t.startswith("of=/dev/") or t.startswith("of=\\\\.\\") for t in argv):
        return "writing to a raw device is never run"
    if prog in _RECURSIVE_TOOLS:
        rest = argv[1:]
        recursive = any(
            re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", t) or t in ("--recursive", "/s", "/S")
            for t in rest
        )
        targets = [t for t in rest if not t.startswith("-") and t.lower() not in ("/s", "/q", "/f")]
        if recursive and any(_dangerous_root(ctx, t) for t in targets):
            return "recursive delete / permission changes on the root, a drive or the home folder are never run"
    for token in argv[1:]:
        for part in (token, token.partition("=")[2]):
            if part and "://" not in part and not part.startswith("-"):
                probe = _expand(ctx, part)
                if not os.path.isabs(probe):
                    probe = os.path.join(str(ctx.project_root), probe)
                if ctx.scope.is_denied(probe):
                    return "that location holds credentials or private browser data"
    return None


def _split_command(command: str) -> list[str]:
    if os.name == "nt":
        return [t.strip("\"'") for t in shlex.split(command, posix=False)]
    return shlex.split(command)


def _command_argv(args: dict) -> list[str] | ToolResult:
    raw_argv, command = args.get("argv"), args.get("command")
    if (raw_argv is None) == (command is None):
        return err("bad_arguments", "Give exactly one of `argv` (a list) or `command` (a string).")
    if raw_argv is not None:
        if not isinstance(raw_argv, list) or not all(isinstance(t, str) for t in raw_argv):
            return err("bad_arguments", "`argv` must be a list of strings.")
        argv = list(raw_argv)
    else:
        if not isinstance(command, str):
            return err("bad_arguments", "`command` must be a string.")
        try:
            argv = _split_command(command)
        except ValueError:
            return err("bad_arguments", "`command` could not be split (unbalanced quotes?).")
    if (
        not argv
        or not argv[0].strip()
        or len(argv) > COMMAND_TOKENS_MAX
        or any(len(t) > COMMAND_TOKEN_CHARS or "\x00" in t for t in argv)
    ):
        return err("bad_arguments", "The command is empty, too long, or contains a NUL character.")
    return argv


def clean_env() -> dict[str, str]:
    """子进程的环境：去掉密钥类变量（名字带 KEY / TOKEN / SECRET / PASSWORD / AUTH 等），其余原样。"""
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}


def _project_env_ids(ctx: FixContext) -> list[str]:
    """本项目当前所用的环境（前缀在 `env_prefixes` 里的）：命令可能改它，所以一并打快照。"""
    out: list[str] = []
    for eid, py in ctx.environments.items():
        norm = os.path.normcase(os.path.abspath(py))
        if any(
            norm.startswith(os.path.normcase(os.path.abspath(str(p))) + os.sep)
            for p in ctx.env_prefixes
        ):
            out.append(eid)
    return out


def run_command(ctx: FixContext, args: dict) -> ToolResult:
    from . import trial

    argv = _command_argv(args)
    if isinstance(argv, ToolResult):
        return argv
    why = hard_block_reason(ctx, argv, " ".join(argv))
    if why is not None:
        return err("hard_blocked", f"Refused: {why}.")
    timeout = args.get("timeout_s")
    timeout = (
        float(min(max(timeout, 1), COMMAND_TIMEOUT_MAX_S))
        if isinstance(timeout, int | float) and not isinstance(timeout, bool)
        else COMMAND_TIMEOUT_DEFAULT_S
    )
    cwd = str(ctx.project_root)
    if not os.path.isdir(cwd):
        return err("no_project", "The project folder is not there.")
    # 用户脚本永不被改：执行前把项目里的用户脚本字节备份进快照，备份不了（太多 / 太大 / 读不了）就不让命令去跑
    saved_files = snapshots.collect_scripts(ctx.project_root)
    if saved_files is None:
        return err(
            "scripts_too_large",
            "The project has too many or too large script files to protect while a command runs, "
            "so the command was not run. Run it yourself, or use a more specific tool.",
        )
    shown = shlex.join(argv) if os.name != "nt" else subprocess.list2cmdline(argv)
    denied = approve(
        ctx,
        "run_command",
        f"Run this command in the project folder: {shown[:400]}",
        argv=[scrub(t)[:200] for t in argv[:16]],
    )
    if denied is not None:
        return denied
    if snapshots.take(ctx, "run_command", _project_env_ids(ctx), saved_files) is None:
        return err(
            "snapshot_failed", "Could not save a snapshot to roll back to, so nothing was changed."
        )
    from .. import runtime

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            env=clean_env(),
            creationflags=runtime.CREATE_NO_WINDOW,
            start_new_session=(os.name != "nt"),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return err(
            "spawn_failed",
            _with_snapshot(ctx, "The program could not be started (is it installed?)."),
        )
    overflow = threading.Event()

    def stop() -> None:
        overflow.set()
        trial._kill_own(proc)

    cap = trial._Capture(proc.stdout, {"bytes": 0}, threading.Lock(), stop)
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        trial._kill_own(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    cap.thread.join(timeout=5)
    try:
        proc.stdout.close()
    except OSError:
        pass
    tail = "\n".join(cap.text().splitlines()[-OUTPUT_LINES:])
    notes = ""
    # 命令结束后逐个核对用户脚本：被改 / 被删的经 scriptlock 的写入路径恢复原字节，并如实报告
    restored, failed = snapshots.verify_scripts(saved_files)
    if restored:
        notes += (
            f"\nscripts_restored: {len(restored)} user script(s) were changed or deleted by the command "
            f"and put back to their original bytes ({snapshots.display_names(ctx, restored)}); "
            "user scripts are never modified"
        )
    if failed:
        snapshots.mark_unrestored(ctx, ctx.call_meta["snapshot"], failed)
        notes += (
            f"\nscripts_unrestored: {len(failed)} user script(s) were changed by the command and could NOT "
            f"be put back ({snapshots.display_names(ctx, failed)}); rollback will try again. Tell the user."
        )
    if overflow.is_set():
        notes += (
            f"\noutput_truncated: more than {trial.OUTPUT_LIMIT_BYTES // (1024 * 1024)} MiB of output, "
            "the program was stopped; only the last part is kept"
        )
    body = _with_snapshot(
        ctx, f"exit code: {proc.returncode}{notes}\n--- output (last lines) ---\n{tail}"
    )
    if failed:
        res = err("scripts_unrestored", body)
    elif timed_out and not overflow.is_set():
        res = err(
            "timeout",
            f"The command did not finish within {int(timeout)} s and was stopped.\n{body}",
        )
    else:
        res = ok(body, f"exit {proc.returncode}")
    res.mutated = True  # 命令可能改了任何东西
    return res


# --------------------------------------------------------------------------- convert_script
def _sha256(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _inside(root: Path, path: Path) -> bool:
    try:
        r, p = os.path.realpath(root), os.path.realpath(path)
    except OSError:
        return False
    return p == r or p.startswith(r + os.sep)


def _free_output(script: Path) -> Path | None:
    for i in range(1, 50):
        suffix = "_matplotlib" if i == 1 else f"_matplotlib_{i}"
        out = script.with_name(script.stem + suffix + ".py")
        if not os.path.lexists(out):
            return out
    return None


def convert_script(ctx: FixContext, args: dict) -> ToolResult:
    target = args.get("target")
    if target is not None and not (isinstance(target, str) and target.strip()):
        return err(
            "bad_arguments", "`target` must be a script path inside the project, or omitted."
        )
    if isinstance(target, str):
        script = Path(target.strip())
        if not script.is_absolute():
            script = ctx.project_root / script
    elif ctx.script_path is not None:
        script = Path(ctx.script_path)
    else:
        return err("no_script", "There is no script to convert in this session.")
    if not script.is_file():
        return err("no_script", "That script file is not there.")
    if not _inside(ctx.project_root, script):
        return err("outside_project", "Only a script inside the project can be converted.")
    out = _free_output(script)
    if out is None:
        return err("target_exists", "Too many converted copies already exist next to the script.")
    if ctx.script_converter is None:
        return err(
            "unavailable", "Converting a script to matplotlib is not available in this session yet."
        )
    bad = _snapshot_or_error(ctx, "convert_script")
    if bad is not None:
        return bad
    try:
        original = script.read_bytes()
    except OSError:
        return err("no_script", "That script file could not be read.")
    # 转换器拿到的是原脚本的**临时副本**，不是原路径：它没有任何理由去写原脚本
    from .. import config

    work = config.data_dir() / "cache" / "fix_convert" / uuid.uuid4().hex
    try:
        work.mkdir(parents=True)
        copy_path = work / script.name
        copy_path.write_bytes(original)
    except OSError:
        shutil.rmtree(work, ignore_errors=True)
        return err("copy_failed", "Could not make a scratch copy of the script.")
    try:
        res = executors.call_executor(
            ctx.script_converter,
            {"script": str(copy_path), "output": str(out), "project": str(ctx.project_root)},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # 原脚本绝对不动：无论执行器说什么，字节变了就用执行前读到的原字节经 scriptlock 恢复，并报错
    if _sha256(script) != hashlib.sha256(original).hexdigest():
        back = snapshots.restore_script(script, original)
        return err(
            "original_modified",
            _with_snapshot(
                ctx,
                "The conversion changed the original script, which must never happen. "
                + (
                    "The original bytes were put back."
                    if back
                    else "The original could NOT be put back."
                )
                + " Tell the user.",
            ),
        )
    if not res.get("ok") or not out.is_file():
        return err(
            str(res.get("code") or "convert_failed")[:60],
            _with_snapshot(
                ctx, str(res.get("message") or "The conversion did not produce a script.")
            ),
        )
    return _done(
        ctx,
        f"Converted. The new script was saved next to the original as `{out.name}`; the original is untouched.",
        "script converted",
    )


# --------------------------------------------------------------------------- 描述与登记
_ENV = {"type": "string", "description": "Environment id from list_environments."}

CHANGE_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "install_packages",
        "Install packages into one environment (wheels only, through Tavotto's own installer). Packages on "
        "Tavotto's list of common packages are installed without asking; any other package asks the user first. "
        "Never installs into the operating system's Python or Tavotto's built-in runtime. A snapshot is taken "
        "first; the result names it so rollback can undo this. `env` must be an id returned by list_environments.",
        obj(
            {
                "env": _ENV,
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Requirements such as `seaborn` or `numpy==1.26.4` (no options, URLs or paths).",
                },
            },
            ["env", "packages"],
        ),
        install_packages,
        tier="listed",
        waits_for_user=True,
    ),
    FixTool(
        "switch_execution",
        "Change how this project runs scripts: `safe` = only Tavotto's guarded safe mode, `native` = allow running "
        "natively (on the user's own Python and files) when the safe mode is blocked by its own guard. Asks the "
        'user first only when it would undo a choice of "safe only" the user made. Takes a snapshot.',
        obj({"mode": {"type": "string", "enum": ["safe", "native"]}}, ["mode"]),
        switch_execution,
        tier="tavotto",
        waits_for_user=True,
    ),
    FixTool(
        "change_python",
        "Create a Tavotto-managed environment with another Python version for this project (for example when "
        "the packages need Python 3.12). Does not touch the user's own Pythons. Takes a snapshot.",
        obj({"version": {"type": "string", "description": "For example `3.12`."}}, ["version"]),
        change_python,
        tier="tavotto",
    ),
    FixTool(
        "select_environment",
        "Make this project use another environment that already exists on the computer. Always asks the user. "
        "Takes a snapshot. `env` must be an id returned by list_environments.",
        obj({"env": _ENV}, ["env"]),
        select_environment,
        tier="user_env",
        waits_for_user=True,
    ),
    FixTool(
        "run_command",
        "Run one program with arguments in the project folder, after the user approves it. Give `argv` (a list) or "
        "`command` (split into words; no shell: no pipes, redirects, `&&`, substitutions). Never runs sudo, shells, "
        "disk tools, piping into a shell or recursive deletes of the root or home folder; secret-looking "
        "environment variables are removed; output is cut and masked; there is a time limit. Takes a snapshot.",
        obj(
            {
                "argv": {"type": "array", "items": {"type": "string"}},
                "command": {"type": "string"},
                "timeout_s": {
                    "type": "integer",
                    "description": "Seconds before it is stopped (default 60, max 300).",
                },
            }
        ),
        run_command,
        tier="user_env",
        waits_for_user=True,
    ),
    FixTool(
        "convert_script",
        "Convert a script that does not draw with matplotlib into one that does, saved next to it as "
        "`<name>_matplotlib.py`. The original script is never changed. `target` is a script path inside the project "
        "(default: the script being fixed).",
        obj({"target": {"type": "string", "description": "Script path inside the project."}}),
        convert_script,
        tier="tavotto",
    ),
    FixTool(
        "rollback",
        "Undo changes back to a snapshot named in an earlier result of this session: project settings go back "
        "exactly; packages installed since are removed and changed or removed ones go back to the old version.",
        obj(
            {
                "snapshot": {
                    "type": "string",
                    "description": "Snapshot id such as snap_20261023T051200_ab12cd.",
                }
            },
            ["snapshot"],
        ),
        snapshots.rollback,
        tier="tavotto",
    ),
)
