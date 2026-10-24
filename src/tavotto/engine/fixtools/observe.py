"""「看」组工具（tier=observe）：都是现有函数的薄壳，只读、不联网、不改任何东西（ADR 0149 §四）。唯一例外是 `inspect_environment`：它要在目标环境里起一个短命子进程，登记为 tier=run，走 `trial.run_python` 的同一套保护。

读文件类一律走 `readscope`（范围 + 硬拦 + 脱敏 + 截断）；find / grep 有条数与时间上限，跳过拦截区与大目录。
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from collections import deque

from ..agent_runner.wires import ToolResult
from . import readscope
from .base import FixContext, FixTool, err, home_relative, obj, ok, resolve_or_error

LIST_LIMIT = 200
FIND_LIMIT_MAX = 500
GREP_LIMIT_MAX = 200
#: find / grep 的墙钟上限（秒）
SCAN_SECONDS = 8.0
#: 不下探的大目录（名字匹配）。需要时模型可以把它们当 root 直接指给 find / grep。
SKIP_DIRS = frozenset(
    {"node_modules", ".git", "__pycache__", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".tox"}
)
#: site-packages 之下只下探这么多层（深层是第三方源码，海量）
SITE_PACKAGES_DEPTH = 2
GREP_FILE_BYTES = 2 * 1024 * 1024
LOG_LINES_MAX = 400
REPORT_MAX_CHARS = 8000

_LOG_SOURCES = ("app", "worker")


# --------------------------------------------------------------------------- read_run_report
def _tail(text: str, n: int = 40) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _precis(value, depth: int = 0):
    if isinstance(value, str):
        return _tail(value, 40) if len(value) > 1200 else value
    if isinstance(value, list):
        return [_precis(v, depth + 1) for v in value[-12:]]
    if isinstance(value, dict) and depth < 4:
        return {str(k): _precis(v, depth + 1) for k, v in list(value.items())[:30]}
    return value if isinstance(value, bool | int | float | type(None)) else str(value)[:200]


_REPORT_KEYS = (
    "phase",
    "outcome",
    "code",
    "error",
    "error_tail",
    "attempt_history",
    "remedy",
    "dependency",
)


def read_run_report(ctx: FixContext, args: dict) -> ToolResult:
    report = ctx.report() if ctx.report else None
    if not isinstance(report, dict):
        return err("no_report", "There is no preparation session report right now.")
    picked = {
        k: _precis(report[k]) for k in _REPORT_KEYS if report.get(k) not in (None, "", [], {})
    }
    text = json.dumps(picked, ensure_ascii=False, indent=1, default=str)
    if len(text) > REPORT_MAX_CHARS:
        text = text[:REPORT_MAX_CHARS] + "\n[truncated]"
    return ok(text, f"report with {len(picked)} sections")


# --------------------------------------------------------------------------- read_file / list_dir
def _int(v, default: int | None, lo: int, hi: int) -> int | None:
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        return default
    return max(lo, min(hi, v))


def read_file(ctx: FixContext, args: dict) -> ToolResult:
    r = resolve_or_error(ctx, args.get("path"), "read_file")
    if isinstance(r, ToolResult):
        return r
    start = _int(args.get("start_line"), 1, 1, 10**9) or 1
    max_lines = _int(args.get("max_lines"), None, 1, 10**6)
    out = readscope.read_text_capped(r.real, start_line=start, max_lines=max_lines)
    shown = home_relative(ctx, r.real)
    if out.kind == "error":
        msg = {
            "not_a_file": "Not a regular file (use list_dir for directories).",
            "read_failed": "The file could not be read.",
        }.get(out.code, "The file could not be read.")
        return err(out.code, msg)
    if out.kind == "binary":
        return ok(
            f"{shown}: {out.note}, {out.size} bytes. Binary files are not shown.", "binary file"
        )
    body = out.text if out.text else "(empty)"
    if out.note:
        body += "\n" + out.note
    return ok(
        body,
        f"read lines {out.start_line}-{out.end_line}",
        compact=f"[already read {shown} lines {out.start_line}-{out.end_line}; read it again if you need the text]",
    )


def list_dir(ctx: FixContext, args: dict) -> ToolResult:
    r = resolve_or_error(ctx, args.get("path"), "list_dir")
    if isinstance(r, ToolResult):
        return r
    try:
        entries, partial = _scan_bounded(r.real, time.monotonic() + SCAN_SECONDS)
    except OSError:
        return err("not_a_dir", "Could not list that path (is it a directory?).")
    rows: list[str] = []
    hidden = 0
    for e in entries:
        full = os.path.join(r.real, e.name)
        if ctx.scope.is_denied(full):
            hidden += 1  # 不透露被拦的东西叫什么，只说有几项
            continue
        if len(rows) >= LIST_LIMIT:
            break
        if e.is_symlink():
            rows.append(f"{e.name}  (symlink)")
        elif e.is_dir(follow_symlinks=False):
            rows.append(f"{e.name}/")
        else:
            try:
                rows.append(f"{e.name}  ({e.stat(follow_symlinks=False).st_size} bytes)")
            except OSError:
                rows.append(e.name)
    more = len(entries) - hidden - len(rows)
    tail = (f"\n... and {more} more" if more > 0 else "") + (
        f"\n({hidden} protected entries not shown)" if hidden else ""
    )
    if partial:
        tail += PARTIAL_NOTE
    return ok(("\n".join(rows) or "(empty)") + tail, f"listed {len(rows)} entries")


# --------------------------------------------------------------------------- find / grep
#: 单个目录最多枚举的条目数：超大目录不先整个读进内存再排序
DIR_ENTRY_CAP = 5000


def _scan_bounded(path: str, deadline: float) -> tuple[list[os.DirEntry], bool]:
    """惰性枚举一个目录：边读边查时限与条数上限（不 `sorted(os.scandir(...))` 一次吃光），读到的再排序。
    → (条目, 是否没读全)。没读全必须如实告诉调用方——否则「没扫到」会被当成「不存在」。"""
    out: list[os.DirEntry] = []
    partial = False
    with os.scandir(path) as it:
        for i, e in enumerate(it):
            if len(out) >= DIR_ENTRY_CAP or (i % 256 == 0 and time.monotonic() > deadline):
                partial = True
                break
            out.append(e)
    out.sort(key=lambda e: e.name)
    return out, partial


PARTIAL_NOTE = (
    f"\n[incomplete: some directories have more than {DIR_ENTRY_CAP} entries and were only partly scanned; "
    "a missing result does not mean the file / text is absent — narrow the root]"
)


def _walk(ctx: FixContext, root: str, deadline: float, stats: dict | None = None):
    """受限遍历：不跟随目录符号链接、跳过大目录与拦截区、限时。产出 `(文件的绝对路径, 解析结果)`。

    文件符号链接会被跟随，`解析结果` 就是**链接目标**的 `ReadScope.check`（项目外判断以目标为准；与 `resolve_or_error`
    同一个判据）；普通文件为 None。读了内容的调用方要对非 None 的结果调 `ctx.note_read`。"""
    stack = deque([(root, -1)])  # (目录, site-packages 之下的层数；-1 = 不在其中)
    while stack:
        cur, site = stack.popleft()
        if time.monotonic() > deadline:
            return
        try:
            it, partial = _scan_bounded(cur, deadline)
            if partial and stats is not None:
                stats["partial"] = True
        except OSError:
            continue
        for e in it:
            full = os.path.join(cur, e.name)
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name in SKIP_DIRS or ctx.scope.is_denied(full):
                        continue
                    nxt = 0 if e.name == "site-packages" else (site + 1 if site >= 0 else -1)
                    if nxt > SITE_PACKAGES_DEPTH:
                        continue
                    stack.append((full, nxt))
                elif e.is_file(follow_symlinks=False):
                    yield full, None
                elif e.is_symlink():
                    target = ctx.scope.check(full)
                    if target.ok and os.path.isfile(target.real):
                        yield full, target
            except OSError:
                continue


def _glob_ok(name: str, rel: str, glob: str | None) -> bool:
    return not glob or fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(rel, glob)


def find_files(ctx: FixContext, args: dict) -> ToolResult:
    r = resolve_or_error(ctx, args.get("root"), "find_files")
    if isinstance(r, ToolResult):
        return r
    glob = args.get("glob") if isinstance(args.get("glob"), str) and args.get("glob") else "*"
    limit = _int(args.get("limit"), 100, 1, FIND_LIMIT_MAX) or 100
    deadline = time.monotonic() + SCAN_SECONDS
    found: list[str] = []
    stats: dict = {}
    for full, _target in _walk(ctx, r.real, deadline, stats):
        rel = os.path.relpath(full, r.real).replace(os.sep, "/")
        if ctx.scope.is_denied(full) or not _glob_ok(os.path.basename(full), rel, glob):
            continue
        found.append(rel)
        if len(found) >= limit:
            break
    timed_out = time.monotonic() > deadline
    note = "\n[stopped at the time limit; narrow the root or the glob]" if timed_out else ""
    if len(found) >= limit:
        note = f"\n[showing the first {limit}; narrow the glob for more]"
    if stats.get("partial"):
        note += PARTIAL_NOTE
    return ok("\n".join(found) + note if found else "(no matches)" + note, f"{len(found)} files")


def grep_files(ctx: FixContext, args: dict) -> ToolResult:
    """**字面量**搜索（不是正则）：按原样 `in` 匹配，可选大小写不敏感。没有正则就没有灾难性回溯这一类问题。"""
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern or len(pattern) > 500 or "\n" in pattern:
        return err(
            "bad_arguments",
            "`pattern` must be a non-empty single-line text to search for (at most 500 chars).",
        )
    ignore_case = args.get("ignore_case") is True
    needle = pattern.casefold() if ignore_case else pattern
    r = resolve_or_error(ctx, args.get("root"), "grep_files")
    if isinstance(r, ToolResult):
        return r
    glob = args.get("glob") if isinstance(args.get("glob"), str) and args.get("glob") else None
    limit = _int(args.get("limit"), 50, 1, GREP_LIMIT_MAX) or 50
    deadline = time.monotonic() + SCAN_SECONDS
    hits: list[str] = []
    files = 0
    noted: set[str] = set()
    stats: dict = {}
    for full, target in _walk(ctx, r.real, deadline, stats):
        rel = os.path.relpath(full, r.real).replace(os.sep, "/")
        if ctx.scope.is_denied(full) or not _glob_ok(os.path.basename(full), rel, glob):
            continue
        try:
            if os.path.getsize(full) > GREP_FILE_BYTES:
                continue
            with open(full, "rb") as f:
                data = f.read(GREP_FILE_BYTES)
        except OSError:
            continue
        if target is not None and target.real not in noted:
            noted.add(target.real)  # 实际读的是链接目标：项目外就记进 reads
            ctx.note_read(target, "grep_files")
        if b"\x00" in data[:8192]:
            continue
        files += 1
        for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if needle in (line.casefold() if ignore_case else line):
                hits.append(f"{rel}:{i}: {line[:300]}")
                if len(hits) >= limit:
                    break
        if len(hits) >= limit or time.monotonic() > deadline:
            break
    note = ""
    if len(hits) >= limit:
        note = f"\n[showing the first {limit} matches]"
    elif time.monotonic() > deadline:
        note = "\n[stopped at the time limit; narrow the root or the glob]"
    if stats.get("partial"):
        note += PARTIAL_NOTE
    return ok(
        "\n".join(hits) + note if hits else "(no matches)" + note,
        f"{len(hits)} matches in {files} files",
    )


# --------------------------------------------------------------------------- 环境
def _environment_entries(ctx: FixContext) -> list[dict]:
    if ctx.environment_source is not None:
        return list(ctx.environment_source())
    from .. import userenvs

    return userenvs.discover(ctx.project_root, ask_login_shell=None)


#: `list_environments` 一次最多列这么多；超出如实报总数（#1361），按 `match` 筛
ENV_LIST_LIMIT = 30


def _env_rank(ctx: FixContext, entry: dict, remembered_key) -> int:
    """排序键：本项目当前所用的环境（前缀在 `env_prefixes` 里）与用户为本项目选过的环境排最前，项目自己的 `.venv` 其次。"""
    from .. import projectenv

    py = entry.get("python")
    if not isinstance(py, str) or not py:
        return 3
    try:
        key = projectenv.env_key(py)
        norm = os.path.normcase(os.path.abspath(py))
        for prefix in ctx.env_prefixes:
            base = os.path.normcase(os.path.abspath(str(prefix))) + os.sep
            if norm.startswith(base):
                return 0
        if remembered_key is not None and key == remembered_key:
            return 0
    except (OSError, ValueError):
        pass
    return 1 if entry.get("source") == "project_venv" else 2


def list_environments(ctx: FixContext, args: dict) -> ToolResult:
    from .. import projectenv, userenvs

    match = args.get("match")
    if match is not None and not isinstance(match, str):
        return err("bad_arguments", "`match` must be a text fragment such as `conda` or `lab`.")
    try:
        entries = _environment_entries(ctx)
    except Exception as exc:  # noqa: BLE001 - 发现失败是工具错误，不抛
        return err("discover_failed", f"Could not list environments ({type(exc).__name__}).")
    valid = [e for e in entries if isinstance(e.get("python"), str) and e.get("python")]
    total = len(valid)
    if match and match.strip():
        needle = match.strip().casefold()
        # 名字 / 来源 / 路径片段都能筛；路径只用来筛，不会出现在返回里
        valid = [
            e
            for e in valid
            if needle
            in " ".join(str(e.get(k) or "") for k in ("label", "source", "python")).casefold()
        ]
    try:
        remembered = projectenv.remembered(ctx.project_root)
        remembered_key = projectenv.env_key(remembered) if remembered else None
    except Exception:  # noqa: BLE001 - 取不到「用户选过」只影响排序
        remembered_key = None
    ranked = sorted(enumerate(valid), key=lambda it: (_env_rank(ctx, it[1], remembered_key), it[0]))
    shown = [e for _, e in ranked[:ENV_LIST_LIMIT]]
    rows = []
    for e in shown:
        py = e["python"]
        eid = userenvs.env_id(py, e.get("overlay"))
        ctx.environments[eid] = py  # 之后 inspect_environment / try_* 用这个 id 指认
        if isinstance(e.get("overlay"), dict):
            ctx.environment_overlays[eid] = e["overlay"]
        row = {"id": eid, "source": e.get("source", ""), "label": e.get("label", "")}
        if "satisfies" in e or "checked" in e:
            # 已评估过的：给落选原因的稳定码（与界面 envRejectionText 同一套 reason code）
            rej = userenvs.rejection_reason(e)
            row["status"] = "usable" if e.get("satisfies") else rej["reason"]
            for k in ("missing", "import_failed"):
                if rej.get(k):
                    row[k] = rej[k]
        rows.append(row)
    if not rows:
        if total and match:
            return ok(
                f"(none of the {total} environments matches `{match.strip()[:60]}`)",
                "0 environments",
            )
        return ok(
            "(no Python environments found). This project has no environment that can run scripts yet: "
            "after the user agrees (propose_fix), create a Tavotto-managed one with change_python.",
            "0 environments",
        )
    text = json.dumps(rows, ensure_ascii=False, indent=1)
    note = ""
    if len(valid) > len(rows):
        note = (
            f"\n[showing {len(rows)} of {len(valid)}"
            + (
                f" matching environments ({total} found in all)"
                if match and match.strip()
                else " environments"
            )
            + "; the others are not listed and have no id yet. The project's current and previously chosen "
            "environments come first. Pass `match` (part of a name, source or path) to list the rest.]"
        )
    elif match and match.strip():
        note = f"\n[{len(rows)} of {total} environments match `{match.strip()[:60]}`]"
    return ok(text + note, f"{len(rows)} of {len(valid)} environments")


_INSPECT_SRC = r"""
import json, platform, sys
out = {"python_version": platform.python_version(), "platform": "-".join((platform.system(), platform.release(), platform.machine())),
       "implementation": platform.python_implementation()}
try:
    from importlib import metadata
    _all = sorted({(d.metadata["Name"] or "") + "==" + (d.version or "")
                   for d in metadata.distributions() if d.metadata["Name"]})
    out["packages"] = _all[:400]
    out["package_total"] = len(_all)
    if len(_all) > 400:
        out["packages_truncated"] = "only the first 400 (alphabetical) are listed; use try_import to check a specific package"
except Exception as exc:
    out["packages_error"] = type(exc).__name__
try:
    import os
    os.environ.setdefault("MPLBACKEND", "Agg")
    import matplotlib
    out["matplotlib_version"] = matplotlib.__version__
    out["matplotlib_backend"] = matplotlib.get_backend()
except Exception as exc:
    out["matplotlib_error"] = type(exc).__name__
print(json.dumps(out))
"""


def inspect_environment(ctx: FixContext, args: dict) -> ToolResult:
    from . import trial

    python = ctx.python_of(args.get("env"))
    if python is None:
        return err(
            "env_unknown",
            "Unknown environment id. Call list_environments first and use one of its ids.",
        )
    res = trial.run_python(
        python, code=_INSPECT_SRC, timeout=trial.INSPECT_TIMEOUT_S, session_id=ctx.session_id
    )
    if res["status"] != "ok":
        return err(
            res["status"],
            f"Could not inspect the environment ({res['status']})." + trial.guard_note(res),
        )
    try:
        info = json.loads(res["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return err(
            "inspect_failed",
            "The environment did not answer in the expected form." + trial.guard_note(res),
        )
    pk = info.get("packages")
    if isinstance(pk, list):
        total = info.pop("package_total", None)
        info["package_count"] = (
            total if isinstance(total, int) else len(pk)
        )  # 真实总数，不是截断后的条数
    info["os_sandbox"] = res.get("os_sandbox") or "none"  # 结果仍是一个 JSON 文档
    if res.get("activation") == "skipped":
        info["activation"] = "skipped (Conda activation not cached; activate scripts were not run)"
    return ok(
        json.dumps(info, ensure_ascii=False, indent=1) + trial.guard_note(res, sandbox=False),
        "inspected environment",
    )


# --------------------------------------------------------------------------- read_logs
def _log_path(ctx: FixContext, source: str):
    if source in ctx.log_paths:
        return ctx.log_paths[source]
    if source == "app":
        from .. import config

        return config.data_dir() / "cache" / "app.log"
    return None


def read_logs(ctx: FixContext, args: dict) -> ToolResult:
    source = args.get("source")
    if source not in _LOG_SOURCES:
        return err("bad_arguments", f"`source` must be one of {list(_LOG_SOURCES)}.")
    n = _int(args.get("lines"), 80, 1, LOG_LINES_MAX) or 80
    path = _log_path(ctx, source)
    if path is None or not os.path.isfile(path):
        return err("no_log", f"There is no {source} log to read.")
    # 日志读取也进「项目外读过的文件」清单（日志是 Tavotto 自己的文件，不受主目录范围限制，但照样告诉用户）
    seen = ctx.scope.check(path)
    if not seen.ok:  # 日志在主目录 / 项目范围之外也照记（不 ok 只是范围判据，不是「没读」）
        seen = readscope.Resolved(True, real=os.path.realpath(path), in_project=False)
    ctx.note_read(seen, "read_logs")
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read()
    except OSError:
        return err("read_failed", "The log could not be read.")
    lines = data.decode("utf-8", errors="replace").splitlines()[-n:]
    return ok("\n".join(lines) or "(empty log)", f"{len(lines)} log lines")


# --------------------------------------------------------------------------- lookup_package
def lookup_package(ctx: FixContext, args: dict) -> ToolResult:
    from .. import depresolve

    name = args.get("name")
    if not isinstance(name, str) or not depresolve.valid_import_name(name):
        return err(
            "bad_arguments", "`name` must be a valid Python import name such as `cv2` or `yaml`."
        )
    dist = depresolve.curated_distribution(name)
    info: dict = {"import_name": name}
    if dist:
        info["distribution"] = dist
        info["source"] = "curated table"
    else:
        info["distribution"] = None
        info["note"] = (
            "Not in Tavotto's table of known import-name to package-name mappings; "
            "the package name may equal the import name or differ (not looked up online)."
        )
    not_pip = depresolve.not_pip_installable(name)
    if not_pip is not None:
        info["not_pip_installable"] = True
    return ok(json.dumps(info, ensure_ascii=False, indent=1), "package lookup")


# --------------------------------------------------------------------------- 描述与登记
_PATH = {
    "type": "string",
    "description": "Absolute path, `~/...` (your home folder), or a path relative to the project.",
}

OBSERVE_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "read_run_report",
        "Read a short version of the current preparation report: failure code, error tail, attempt history and the "
        "suggested remedy if there is one. Use it first, before guessing. It takes no arguments and changes nothing.",
        obj({}),
        read_run_report,
    ),
    FixTool(
        "read_file",
        "Read a text file (at most 256 KiB per call; longer files are cut with a note, use start_line / max_lines "
        "to continue). Allowed: your home folder, the project and the Python environment in use. Credentials, "
        ".env files, private keys and browser data are refused. Secrets in the text are masked. "
        "Binary files only report type and size.",
        obj(
            {
                "path": _PATH,
                "start_line": {"type": "integer", "description": "First line to show (1-based)."},
                "max_lines": {"type": "integer", "description": "How many lines at most."},
            },
            ["path"],
        ),
        read_file,
    ),
    FixTool(
        "list_dir",
        "List the entries directly inside a directory (one level). Same read rules as read_file.",
        obj({"path": _PATH}, ["path"]),
        list_dir,
    ),
    FixTool(
        "find_files",
        "Find files by glob (for example `*.py` or `requirements*.txt`) under a root directory. Skips node_modules, "
        ".git and deep site-packages; has a count and time limit.",
        obj(
            {
                "root": _PATH,
                "glob": {
                    "type": "string",
                    "description": "Glob on the file name or relative path.",
                },
                "limit": {"type": "integer", "description": "Maximum results (default 100)."},
            },
            ["root"],
        ),
        find_files,
    ),
    FixTool(
        "grep_files",
        "Search text files under a root directory for a literal piece of text (NOT a regular expression: every "
        "character, including `.` `*` `(` `[`, is matched as itself). Returns `file:line: text`. "
        "Skips binaries, large files, node_modules, .git; has a count and time limit.",
        obj(
            {
                "root": _PATH,
                "pattern": {
                    "type": "string",
                    "description": "Literal text to look for in a single line (not a regular expression).",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Match upper and lower case alike (default false).",
                },
                "glob": {"type": "string", "description": "Only files matching this glob."},
                "limit": {"type": "integer", "description": "Maximum matches (default 50)."},
            },
            ["root", "pattern"],
        ),
        grep_files,
    ),
    FixTool(
        "inspect_environment",
        "Report one Python environment: version, platform, installed packages and the matplotlib backend. "
        "It starts a short-lived process in that environment under the same protection as try_run (writes outside "
        "a scratch folder blocked, no other processes, OS sandbox where available). "
        "`env` must be an id returned by list_environments.",
        obj(
            {"env": {"type": "string", "description": "Environment id from list_environments."}},
            ["env"],
        ),
        inspect_environment,
        tier="run",
    ),
    FixTool(
        "list_environments",
        "List the Python environments Tavotto found on this computer (id, source, label), with the reason one was "
        "not usable for this project when known. At most 30 are listed (the project's current and previously chosen "
        "ones first) and the result says how many there are in all; pass `match` to narrow the list by part of a "
        "name, source or path, and the matching ones are listed with ids.",
        obj(
            {
                "match": {
                    "type": "string",
                    "description": "Only environments whose name, source or path contains this text.",
                }
            }
        ),
        list_environments,
    ),
    FixTool(
        "read_logs",
        "Read the end of the Tavotto app log or the render worker log (secrets are masked).",
        obj(
            {
                "source": {"type": "string", "enum": list(_LOG_SOURCES)},
                "lines": {
                    "type": "integer",
                    "description": "How many lines from the end (default 80).",
                },
            },
            ["source"],
        ),
        read_logs,
    ),
    FixTool(
        "lookup_package",
        "Map a Python import name to the package name to install, from Tavotto's built-in table "
        "(for example `cv2` -> `opencv-python`). Offline; it does not query PyPI.",
        obj(
            {"name": {"type": "string", "description": "Import name, for example `yaml`."}},
            ["name"],
        ),
        lookup_package,
    ),
)
