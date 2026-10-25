"""AI 修复会话（智能修复主线 D2b，ADR 0148 / 0149）：「✦ 使用你的 AI 来修复」背后的 `fix_environment` 任务。

一次会话 = 一个线程里跑内置执行器的「模型 ↔ 工具」循环（`agent_runner.loop.run`），工具是 `fixtools.REGISTRY` 里**全部已登记的**
（档位由 `FixContext.for_fix_session` 决定，本模块不改）。循环**在本进程**里跑，因为工具要碰应用的状态（准备会话报告、
弹卡片等用户点）；模型只有 `HttpModel` 一条出站路径，密钥只在内存、不进事件 / 卡片 / 审计 / 日志。

* **同一时刻一个会话**；会话 id 用 `secrets` 生成、比较用 `compare_digest`；会话绑定项目，别的项目读不到。
* **`ctx.ask`**：把卡片挂在会话上、推一条「重读」提示（SSE `ai.fix`，不带内容），线程阻塞等 `answer()` 或取消 / 超时。
  回答按卡种类校验（`approve` → yes/no；`choice` → 选项 id / other / skip；`pick` → skip 或路径；`send` → sent/no）。
* **预算**：步数 / 墙钟（用户思考的时间也算在墙钟里）/ token；`finish` 之后不再请求模型。
* 结束后由前端重跑准备会话验证——本模块不替用户判「修好了」，`finish(fixed)` 只是 AI 的说法。

**CLI 连接（D3，ADR 0149 修订）**：只连了 Codex / Claude Code 时，同一个会话改为起那个 CLI（`ai_bridge.fix_cli_command`），
它经 MCP 修复模式（`fixmcp.py`）回调本会话的 `call_tool` / `tool_list`——工具仍在**本进程**里由 `run_tool` 执行
（卡片、快照、审计、项目作用域与内置循环是同一份）。回调凭本次会话的 scoped token（`secrets` 生成、`compare_digest`、
只认本会话、会话结束 / 取消即失效）。CLI 的 stdout 一个字不当结论，结论只认 `finish` 工具；退出时没调 `finish` = `failed`。
"""

from __future__ import annotations

import logging
import secrets
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import fixtools
from .agent_runner import loop
from .agent_runner.redact import NO_REDACTION, Redactor
from .agent_runner.tools import Limits, RunEnvelope, Tool
from .agent_runner.wires import WIRES, AnthropicWire, Turn
from .fixtools.audit import AuditLog
from .fixtools.observe import read_run_report

LOG = logging.getLogger(__name__)

#: 卡片等用户多久（秒）；到点回 `{"answer": "timeout"}`
ASK_TIMEOUT = 600.0
#: 一次会话的预算。墙钟含用户读卡片、点按钮的时间，所以给得宽
FIX_LIMITS = Limits(
    max_turns=40, max_total_tokens=600_000, max_seconds=1800.0, max_output_tokens=4096
)
KEEP_SESSIONS = 8
CARD_KINDS = ("approve", "choice", "pick", "send")

#: 卡住检测（秒；测试里改小）。ADR 0149 D3：任何一种模式都不许静默挂着
DELIVER_WAIT = 20.0  # CLI 报「MCP 工具调用开始」后应用这边这么久没收到回调 = 工具没送达（多半在等 CLI 自己的审批）
STALL_IDLE = 120.0  # 没有工具在跑、没有 CLI 输出 / 模型事件这么久 = 卡住
STALL_HARD = 300.0  # 卡住持续这么久自动取消，outcome=failed，原因码 = 卡住的原因
FORCE_SETTLE = 10.0  # 自动取消后内置循环（模型请求挂住）还没返回，这么久后强行收尾
WATCH_TICK = 0.5
STALL_APPROVAL = "cli_waiting_approval"
STALL_NOT_DELIVERED = "cli_tool_not_delivered"
STALL_IDLE_CODE = "stalled"

STATE_RUNNING = "running"
STATE_WAITING = "waiting"
STATE_FINISHED = "finished"

#: 终局 outcome（`finish` 之外的结局）
OUTCOME_FIXED = "fixed"
OUTCOME_NOT_FIXED = "not_fixed"
OUTCOME_CONVERT = "convert"  # 已交接给「生成可编辑版本」（`ctx.handoff`，ADR 0143 / 0149 §七）
OUTCOME_CANCELLED = "cancelled"
OUTCOME_FAILED = "failed"


class FixError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


SYSTEM_PROMPT = """\
You are the repair assistant built into Tavotto, a desktop editor for scientific figures. A plotting script failed to \
run (or produced no figure) and the user asked you to fix the environment around it. The user is a scientist, not a \
programmer: talk in plain words, one short sentence at a time, no jargon.

Work in this order:
1. LOOK first. Call read_run_report, then use the read tools (read_file, list_dir, find_files, grep_files, \
inspect_environment, list_environments, read_logs, lookup_package) until you know the cause. Do not guess.
2. TRY next. try_import and try_run run things in a throw-away copy and change nothing. Use them to confirm a theory \
before you change anything.
3. CHANGE only what you must, and only with the tools you are given. Before any change to the user's environment \
or files, call propose_fix with ONE plain sentence (and, if it installs anything, the package names in `packages`) and wait: if the user declines, do not do it. Tavotto takes a \
snapshot before each change so it can be rolled back; if a change did not help, roll it back.
3b. NO USABLE ENVIRONMENT. If list_environments shows none, or no environment can run this script, do NOT give up. \
First call propose_fix with ONE plain sentence that states the whole plan, and put every package you will install in `packages` (the user confirms once; install_packages will not ask again for those exact names), for example "This project has no Python \
environment that works yet. I will create one just for it and install colorcet." If the user agrees: call \
change_python (it creates a Tavotto-managed environment and installs what the script imports; leave `version` out \
unless you know one is needed), then install_packages for anything still missing into the environment id that \
change_python returned, then try_run to check, then finish with outcome `fixed`. Only if that path fails call \
prepare_diagnostics and finish with `not_fixed`.
3c. ARBITRARY COMMANDS. Prefer the dedicated tools: install_packages, change_python, select_environment, \
switch_execution, convert_script. Only when none of them can do it, use run_command. By default it runs in a \
protective sandbox (the project is read-only for it; it can write only to its own scratch folder, this project's \
Python environment and pip/conda caches; it has network access). Only if the command must write somewhere else, \
call it again with `outside_sandbox: true` and a one-sentence `reason` in plain words: the user is asked in a \
separate, prominent card and may say no. Never ask for it first, and never to avoid a sandbox refusal you could \
work around.
3d. THE SCRIPT RUNS BUT DRAWS WITH ANOTHER LIBRARY. If the script itself runs but its figure is drawn with Pillow, \
OpenCV, imageio, scikit-image, plotly or ROOT (so Tavotto cannot edit it), call convert_script, then finish with \
outcome `convert`. Do not install matplotlib or change the environment for this.
3e. MISSING DATA FILE. If the report has `missing_input` (code missing_input, or a FileNotFoundError for a data file): \
take the missing path from the report; with find_files look for a file of the same (or a close) name in the project, \
in each folder above it up to the home folder, and in the home folder; with read_file read the start of each candidate and \
compare it with how the script uses the file (column names, separator, shape, extension). Exactly one and you are sure: \
link_data_file, with your evidence in the card. Several or unsure: ask_user listing the candidates (path and one \
difference each; put the file in each option's `path`) plus an option "None of these, I will choose", which then goes to pick_file; then link_data_file, try_run, \
finish. link_data_file asks the user itself, so do NOT propose_fix for it; a file the user already chose is linked without \
another card. NEVER edit the script; if the script checks the path in a way a link cannot reach, say so and finish not_fixed.
3f. PROJECT FOLDER SHADOWS A PACKAGE. If a report or trial output has `project_package_shadow` (or the error is \
`No module named 'a.b'` while the project has its own folder `a/`): the import that failed is `a.b`, not `a`; Python \
found `a` in the project folder. Say exactly that, naming the missing piece and the project folder. If the note says \
the folder is a regular package, do NOT install a package named `a` over it (it cannot take effect and would only \
fail or change the environment for nothing), and do not roll back or reinstall in circles. The project's copy is \
missing something that is normally produced when the project is built or installed; tell the user to follow the \
project's own build/install instructions (for example its README or setup.py), never edit the script, and if \
no tool here can do that safely, call prepare_diagnostics if useful and finish `not_fixed` with the reason in plain words. \
If the note says the folder is not a regular package (no `__init__.py`, or one that extends the package path), installing the real package can fix it as usual.
4. If you need something only the user knows, use ask_user (a multiple-choice question) or pick_file (the system file \
chooser). Never ask for a password, key or token.
5. If you cannot fix it, call prepare_diagnostics with a short note of what you found and tried (the user decides \
whether to send), then finish with outcome not_fixed.
6. ALWAYS end by calling finish exactly once: outcome `fixed` only if a trial run now succeeds, `convert` after convert_script, otherwise `not_fixed`, \
plus one plain sentence for the user.

Hard rules: never edit or delete the user's scripts or data; never read credential locations (they are blocked \
anyway); you have no network access except through the tools; stay within the budget — do not repeat a failed step \
more than twice.
"""


def activity_detail(args: object) -> str:
    """进度那一句话里点名的东西（包名 / 模块名）：只取这几个键、短、脱敏；其余参数不进进度。"""
    if not isinstance(args, dict):
        return ""
    for key in ("packages", "package", "module", "name"):
        v = args.get(key)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v[:4])
        if isinstance(v, str) and v.strip():
            return fixtools.scrub(v.strip())[:60]
    return ""


def _first_message(ctx: fixtools.FixContext, script: str) -> str:
    report = read_run_report(ctx, {})
    body = report.content if not report.is_error else "(no report available; call read_run_report)"
    return (
        f"The script `{script}` failed in this project. Fix the cause.\n\n"
        f"Current preparation report (already redacted):\n{body}"
    )


#: CLI 没有「系统提示」入口（Codex 没有、Claude 有但各家不一），所以和内置循环**同一份**提示词放在最前面，后面补这一段
CLI_ADDENDUM = """\

You are running inside a command-line AI tool. Your repair tools are the MCP tools of the server `tavotto_fix` \
(read_run_report, read_file, try_run, propose_fix, finish, ...): use them for everything, and above all for every change. \
Your own shell and editor are read-only here and anything done with them is neither recorded nor undoable. \
What you write in your reply is not shown to the user: the only way to report the result is the `finish` tool.
"""
#: 提示词进命令行（Windows 约 32 KB 上限）：准备报告太长就截断，让模型自己调 read_run_report 读全文
CLI_PROMPT_MAX = 12000


def cli_prompt(ctx: fixtools.FixContext, script: str) -> str:
    text = SYSTEM_PROMPT + CLI_ADDENDUM + "\n" + _first_message(ctx, script)
    if len(text) > CLI_PROMPT_MAX:
        text = text[:CLI_PROMPT_MAX] + "\n[truncated; call read_run_report for the full report]"
    return text


class FixToolset:
    """`loop.run` 要的任务工具集：每次会话现建，工具带本次会话的 `FixContext`。"""

    task_type = "fix_environment"
    default_limits = FIX_LIMITS

    def __init__(
        self,
        ctx: fixtools.FixContext,
        script: str,
        audit: AuditLog | None = None,
        on_activity: Callable[[str, str, str], None] | None = None,
    ):
        self.ctx, self.script, self.audit = ctx, script, audit
        self.on_activity = on_activity

    def tools(self, envelope: RunEnvelope) -> list[Tool]:
        tools = fixtools.build_agent_tools(self.ctx, self.audit)
        if self.on_activity is None:
            return tools
        note = self.on_activity

        def wrap(t: Tool) -> Tool:
            def handler(args: dict, _inner=t.handler, _name=t.name):
                detail = activity_detail(args)
                note(_name, "start", detail)
                try:
                    return _inner(args)
                finally:
                    note(_name, "end", detail)

            return replace(t, handler=handler)

        return [wrap(t) for t in tools]

    def first_message(self, envelope: RunEnvelope) -> str:
        return _first_message(self.ctx, self.script)

    def system_prompt(self, envelope: RunEnvelope) -> str:
        return SYSTEM_PROMPT

    def budget(self, envelope: RunEnvelope) -> Limits:
        return envelope.limits


class _FinishAware:
    """`finish` 之后不再向模型发请求（省一轮钱）：直接回一个空的终止回复。"""

    def __init__(self, inner, finished: threading.Event):
        self.inner, self.finished = inner, finished

    def complete(self, **kw) -> Turn:
        if self.finished.is_set():
            return Turn(text="", stop="end")
        return self.inner.complete(**kw)


@dataclass
class _Pending:
    card_id: str
    kind: str
    options: set[str]
    allow_other: bool
    event: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None


@dataclass
class CliLaunch:
    """起 CLI 所需的一切（命令行里没有令牌；令牌只在 `env` 里）。"""

    argv: list[str]
    env: dict[str, str]
    cwd: str
    #: CLI 结构化输出的逐行解析器（`AgentDefinition.fix_event_parser()`）；只用来显示进度 / 发现卡住，从不当结论
    parse: Callable[[str], list[dict]] | None = None


#: 收尾时等在途工具结束的第一档上限（秒）；到点还没结束就如实记一条警告，**仍不发布终态**，继续等到它结束
TOOL_DRAIN = 120.0

#: `finish` 之后给 CLI 自己收尾退出的时间（秒），到点就结束它自己起的那个进程
FINISH_GRACE = 20.0


@dataclass
class FixSession:
    id: str
    scope: str  # 项目 id
    script: str
    prep_session_id: str
    state: str = STATE_RUNNING
    card: dict | None = None
    outcome: str | None = None
    summary: str = ""
    #: 失败时 `done.reason` 的闭集码（`budget` / `auth` / `quota` / `network` / `max_turns` …）；成功为空
    reason: str = ""
    steps: int = 0
    seq: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    pending: _Pending | None = None
    thread: threading.Thread | None = None
    reported: tuple[str, str] | None = None  # `finish` 交来的 (outcome, summary)
    #: CLI 连接才有：MCP 回调凭它认本会话（不进 `public()`、日志、事件）
    token: str = ""
    ctx: fixtools.FixContext | None = None
    audit: AuditLog | None = None
    #: MCP 回调的工具调用串行（`run_tool` 改 `ctx.step` / `call_meta`，不是线程安全的）
    tool_lock: threading.Lock = field(default_factory=threading.Lock)
    #: CLI 会话收尾中（取消 / 超时 / 进程退出）：不再接受新的工具调用，在途的那个等结束后才发布终态
    closing: threading.Event = field(default_factory=threading.Event)
    cli: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    #: 进度与卡住（D3）：`activity` 是最近一次工具活动；`stalled` 非空 = 现在卡住了，界面要说原因
    activity: dict | None = None
    stalled: dict | None = None
    stall_since: float | None = None
    stall_code: str = ""  # 自动取消时记下的原因码；收尾时据此判 failed
    last_progress: float = field(default_factory=time.monotonic)
    inflight: int = 0
    #: CLI 报告的 MCP 工具调用开始时刻 / 应用实际收到的回调次数（按工具名）；开始数多于回调数 = 有调用没送达
    begins: dict = field(default_factory=dict)
    cbs: dict = field(default_factory=dict)
    approval: str | None = None  # CLI 自己在等审批（值 = 工具名，可空串）
    denied: set = field(default_factory=set)


def _same(a: str, b: object) -> bool:
    return isinstance(b, str) and secrets.compare_digest(a.encode(), b.encode("utf-8", "replace"))


class FixSessions:
    """修复会话的登记表（进程内一份，见模块底部 `SESSIONS`）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: OrderedDict[str, FixSession] = OrderedDict()
        #: 状态变了的「重读」提示出口：`(scope, fix_id) -> None`（app 里接 SSE）
        self.notifier: Callable[[str, str], None] | None = None

    # ---- 查 -------------------------------------------------------------
    def _find(self, fix_id: object, scope: str) -> FixSession:
        with self._lock:
            for s in self._by_id.values():
                if _same(s.id, fix_id):
                    if s.scope != scope:
                        break
                    return s
        raise FixError("fix_not_found", 404)

    def ids_for(self, scope: str) -> set[str]:
        """这个项目名下（还留着的）修复会话 id：诊断包只取这些会话的审计，别的项目的记录不出项目。"""
        with self._lock:
            return {s.id for s in self._by_id.values() if s.scope == scope}

    def active(self) -> FixSession | None:
        with self._lock:
            for s in self._by_id.values():
                if s.state != STATE_FINISHED:
                    return s
        return None

    def get(self, fix_id: object, scope: str) -> dict:
        return self.public(self._find(fix_id, scope))

    @staticmethod
    def public(s: FixSession) -> dict:
        with_card = s.card if s.state == STATE_WAITING else None
        return {
            "fix_id": s.id,
            "state": s.state,
            "script": s.script,
            "session_id": s.prep_session_id,
            "card": dict(with_card) if with_card else None,
            "outcome": s.outcome,
            "summary": s.summary,
            "reason": s.reason,
            #: 仅 outcome == convert：交给「生成可编辑版本」的 `{kind, script, library, path}`（项目相对路径；path = 报告里原输出图，可为 None）；其余结局没有这个键
            **(
                {
                    "handoff": {
                        k: s.ctx.handoff.get(k) for k in ("kind", "script", "library", "path")
                    }
                }
                if s.outcome == OUTCOME_CONVERT and s.ctx is not None and s.ctx.handoff
                else {}
            ),
            "steps": s.steps,
            "seq": s.seq,
            #: 最近一次工具活动 `{tool, phase: start|end|denied, detail, at_ms}`（界面按 tool 映射成一句话）；还没有 = None
            "activity": dict(s.activity) if s.activity else None,
            #: 现在卡住了：`{reason: cli_waiting_approval|cli_tool_not_delivered|stalled, detail}`；没卡住 = None
            "stalled": dict(s.stalled) if s.stalled else None,
        }

    def _notify(self, s: FixSession) -> None:
        s.seq += 1
        n = self.notifier
        if n is not None:
            try:
                n(s.scope, s.id)
            except Exception:  # noqa: BLE001 - 提示丢了前端还有轮询
                LOG.debug("fix notifier failed", exc_info=True)

    # ---- 起 -------------------------------------------------------------
    def start(
        self,
        *,
        scope: str,
        prep_session_id: str,
        script: str,
        project_root: Path,
        model: object | None = None,
        cli: Callable[[str, str, str], CliLaunch] | None = None,
        report: Callable[[], dict | None] | None = None,
        redactor: Redactor = NO_REDACTION,
        audit: AuditLog | None = None,
        limits: Limits | None = None,
        ctx_kwargs: dict | None = None,
        script_path: Path | None = None,
        background: bool = True,
    ) -> FixSession:
        with self._lock:
            cur = self.active()
            if cur is not None:
                if cur.scope == scope and cur.prep_session_id == prep_session_id:
                    return cur
                raise FixError("fix_busy", 409)
            s = FixSession(
                id="fx_" + secrets.token_urlsafe(12),
                scope=scope,
                script=script,
                prep_session_id=prep_session_id,
                token=secrets.token_urlsafe(32) if cli is not None else "",
            )
            self._by_id[s.id] = s
            while len(self._by_id) > KEEP_SESSIONS:
                for k, v in self._by_id.items():
                    if v.state == STATE_FINISHED:
                        del self._by_id[k]
                        break
                else:
                    break
        ctx = fixtools.FixContext.for_fix_session(
            s.id,
            Path(project_root),
            report=report,
            script_path=script_path,
            ask=lambda card: self._ask(s, card, redactor),
            cancel=s.cancel,
            **(ctx_kwargs or {}),
        )
        envelope = RunEnvelope(
            task_type=FixToolset.task_type,
            cwd=Path(project_root),
            target=script,
            instruction="",
            limits=limits or FIX_LIMITS,
            redactor=redactor,
        )
        s.ctx, s.audit = ctx, audit if audit is not None else AuditLog()
        s.cli = cli is not None
        toolset = FixToolset(
            ctx, script, audit, on_activity=lambda t, ph, d: self._activity(s, t, ph, d)
        )

        if cli is not None:
            prompt = cli_prompt(ctx, script)

            def work() -> None:
                self._work_cli(
                    s, cli, prompt, str(project_root), (limits or FIX_LIMITS).max_seconds
                )

        else:
            wrapped = _FinishAware(model, s.finished)

            def work() -> None:
                self._work(s, wrapped, toolset, envelope)

        threading.Thread(
            target=self._watch, args=(s,), name=f"fixwatch-{s.id}", daemon=True
        ).start()
        if background:
            s.thread = threading.Thread(target=work, name=f"fix-{s.id}", daemon=True)
            s.thread.start()
        else:
            work()
        return s

    def _work(self, s: FixSession, model, toolset: FixToolset, envelope: RunEnvelope) -> None:
        done: dict = {}

        def emit(event: dict) -> None:
            s.last_progress = time.monotonic()  # 模型有任何事件 = 活着
            kind = event.get("type")
            if kind == "tool":
                s.steps += 1
                self._notify(s)
            elif kind == "done":
                done.update(event)

        try:
            loop.run(model, toolset, envelope, emit, s.cancel)
        except Exception:  # noqa: BLE001 - loop.run 不抛；兜底
            LOG.exception("fix session crashed")
            done.setdefault("ok", False)
            done.setdefault("reason", "internal")
        self._settle(s, str(done.get("reason") or "internal"))

    def _settle(self, s: FixSession, failed_reason: str) -> None:
        """会话收尾：`finish` 交来了结论就采用，取消就是 cancelled，否则 failed（原因码是闭集，界面按它选一句话）。"""
        with self._lock:
            if s.state == STATE_FINISHED:
                return  # 已收尾（看门狗强行收尾后，卡住的循环线程迟到的那次不再改结局）
            reported = s.reported
            if s.stall_code and reported is None:
                s.outcome, s.summary, s.reason = OUTCOME_FAILED, "", s.stall_code
            elif s.cancel.is_set() and reported is None:
                s.outcome, s.summary, s.reason = OUTCOME_CANCELLED, "", "cancelled"
            elif reported is not None:
                s.outcome, s.summary = reported
                s.reason = ""
            else:
                # AI 没调 finish 就停了（预算 / 网络 / 额度 / 认证…）：按没修好算
                s.outcome = OUTCOME_FAILED
                s.reason = failed_reason[:24]
            s.state, s.card, s.pending, s.stalled = STATE_FINISHED, None, None, None
            s.done.set()
        if s.ctx is not None:
            s.ctx.release_leases()  # change_python 持的旧代租约：会话结束才放（之后旧代才可能被清理）
        self._notify(s)

    # ---- 进度与卡住（D3） -------------------------------------------------
    def _activity(self, s: FixSession, tool: str, phase: str, detail: str = "") -> None:
        now = time.monotonic()
        with self._lock:
            s.activity = {
                "tool": tool,
                "phase": phase,
                "detail": detail,
                "at_ms": int(time.time() * 1000),
            }
            s.last_progress = now
            if phase == "start":
                s.inflight += 1
            elif phase == "end":
                s.inflight = max(0, s.inflight - 1)
        self._notify(s)

    def _audit_event(self, s: FixSession, code: str, summary: str, tool: str = "") -> None:
        """会话级事件（卡住 / 自动取消 / CLI 权限拒绝）进 `fix_audit.jsonl`，诊断包「修复经过」随之可见。不抛。"""
        if s.audit is None:
            return
        try:
            s.audit.record(
                session=s.id,
                step=s.ctx.step if s.ctx else 0,
                tool="_session",
                tier="observe",
                args={"tool": tool},
                approved_by="blocked",
                result={"ok": False, "code": code, "summary": summary},
            )
        except Exception:  # noqa: BLE001 - 审计失败不影响会话
            LOG.debug("fix audit event failed", exc_info=True)

    def _cli_event(self, s: FixSession, ev: dict) -> None:
        kind, tool = str(ev.get("kind") or ""), str(ev.get("tool") or "")[:60]
        now = time.monotonic()
        audit = None
        with self._lock:
            s.last_progress = now
            if kind == "mcp_begin":
                s.begins.setdefault(tool, []).append(now)
            elif kind == "approval":
                s.approval = tool
                audit = ("cli_waiting_approval", "the CLI is waiting for its own approval")
            elif kind == "denied" and tool not in s.denied:
                s.denied.add(tool)
                s.activity = {
                    "tool": tool,
                    "phase": "denied",
                    "detail": "",
                    "at_ms": int(time.time() * 1000),
                }
                audit = ("cli_permission_denied", "the CLI refused one of its own tools")
        if audit is not None:
            self._audit_event(s, audit[0], audit[1], tool)
            self._notify(s)

    def _watch(self, s: FixSession) -> None:
        """看门狗（两种模式共用）：发现卡住就写进会话状态让界面说出原因，卡得太久自动取消。等用户点卡片不算卡住。"""
        forced_at: float | None = None
        while not s.done.wait(WATCH_TICK):
            now = time.monotonic()
            new_stall = auto_cancel = None
            changed = False
            with self._lock:
                if s.state == STATE_FINISHED:
                    return
                code, detail = "", ""
                if s.state == STATE_WAITING:
                    s.last_progress = now
                elif s.approval is not None:
                    code, detail = STALL_APPROVAL, s.approval
                else:
                    for name, times in s.begins.items():
                        got = s.cbs.get(name, 0)
                        if len(times) > got and now - times[got] > DELIVER_WAIT:
                            code, detail = STALL_NOT_DELIVERED, name
                            break
                    else:
                        if s.inflight == 0 and now - s.last_progress > STALL_IDLE:
                            code = STALL_IDLE_CODE
                if code and (s.stalled is None or s.stalled["reason"] != code):
                    s.stalled = {"reason": code, "detail": detail}
                    s.stall_since = now
                    new_stall, changed = (code, detail), True
                elif not code and s.stalled is not None:
                    s.stalled, s.stall_since, changed = None, None, True
                if (
                    s.stalled
                    and s.stall_since is not None
                    and now - s.stall_since > STALL_HARD
                    and not s.cancel.is_set()
                ):
                    s.stall_code = s.stalled["reason"]
                    auto_cancel = (s.stall_code, s.stalled["detail"])
            if new_stall:
                self._audit_event(s, new_stall[0], "the repair AI looks stuck", new_stall[1])
            if auto_cancel:
                self._audit_event(
                    s, auto_cancel[0], "stopped automatically after being stuck", auto_cancel[1]
                )
                s.cancel.set()
                forced_at = now
            if changed:
                self._notify(s)
            if (
                forced_at is not None
                and not s.cli
                and now - forced_at > FORCE_SETTLE
                and s.inflight == 0
            ):
                self._settle(s, s.stall_code)  # 模型请求挂住、循环线程回不来：不能让会话永远占着位

    # ---- CLI 连接（D3） -------------------------------------------------
    def _work_cli(
        self,
        s: FixSession,
        build: Callable[[str, str, str], CliLaunch],
        prompt: str,
        cwd: str,
        max_seconds: float,
    ) -> None:
        """起 CLI、等它退出。只结束**自己起的**那个进程（`ai_bridge._terminate` 的进程组 / `taskkill /T`）。
        stdout 读掉丢弃（不读会把管道写满），内容不当结论、不入日志。"""
        from . import ai_bridge

        reason = "cli_exit"
        sess: dict = {"exited": threading.Event()}
        try:
            launch = build(s.id, s.token, prompt)
            proc = ai_bridge._popen(launch.argv, launch.cwd, launch.env, subprocess.DEVNULL)
        except Exception:  # noqa: BLE001 - 起不来（没装 / 参数 / 权限）：按没修好算
            LOG.warning("fix cli failed to start", exc_info=True)
            self._settle(s, "cli_start")
            return
        sess["proc"] = proc
        drain = threading.Thread(
            target=self._read_events, args=(s, proc, launch.parse), daemon=True
        )
        drain.start()
        started = time.monotonic()
        finish_seen: float | None = None
        while proc.poll() is None:
            now = time.monotonic()
            if s.cancel.is_set():
                break
            if s.finished.is_set():
                finish_seen = finish_seen or now
                if now - finish_seen > FINISH_GRACE:
                    break
            if now - started > max_seconds:
                reason = "budget"
                break
            time.sleep(0.1)
        s.closing.set()  # 先拒新调用；卡片上阻塞的在途工具由 `_ask` 看到它而醒来
        code = proc.poll()
        if code is None:
            ai_bridge._terminate(sess)
        ai_bridge._await_exit(sess)
        drain.join(timeout=2)
        self._drain_inflight(s)
        if reason != "budget":
            reason = "cli_exit" if (code or 0) != 0 else "cli_no_finish"
        self._settle(s, reason)

    @staticmethod
    def _drain_inflight(s: FixSession) -> None:
        """等应用进程里在途的工具调用（`call_tool` 持着 `tool_lock`）结束，之后才发布终态、释放 `active()`：
        否则界面可以重跑验证或起下一次修复，而 `install_packages` 之类还在改环境。上限到了仍未结束就记警告并继续等（会话保持占位）。"""
        if s.tool_lock.acquire(timeout=TOOL_DRAIN):
            s.tool_lock.release()
            return
        LOG.warning(
            "fix session %s: a tool call is still running after %ss; keeping the slot",
            s.id,
            TOOL_DRAIN,
        )
        s.tool_lock.acquire()
        s.tool_lock.release()

    def _read_events(self, s: FixSession, proc, parse) -> None:
        """读 CLI 的结构化输出：逐行解析成进度 / 卡住的线索，**不当结论、不入日志**（不读会把管道写满）。"""
        try:
            for line in proc.stdout:
                s.last_progress = time.monotonic()  # CLI 有输出 = 活着
                if parse is None:
                    continue
                try:
                    events = parse(line.strip())
                except Exception:  # noqa: BLE001 - 解析器的缺陷不拖垮会话
                    continue
                for ev in events:
                    self._cli_event(s, ev)
        except (OSError, ValueError):
            pass

    def _authorized(self, fix_id: object, token: object) -> FixSession:
        """MCP 回调的认证：会话存在、令牌对、会话没结束没取消。别的一律同一个 403（不泄漏哪一项错了）。"""
        with self._lock:
            for s in self._by_id.values():
                if _same(s.id, fix_id):
                    if (
                        s.token
                        and _same(s.token, token)
                        and s.state != STATE_FINISHED
                        and not s.cancel.is_set()
                        and not s.closing.is_set()
                    ):
                        return s
                    break
        raise FixError("fix_tool_forbidden", 403)

    def token_valid(self, fix_id: object, token: object) -> bool:
        """会话认证 guard 的 scoped token 检查（`security.py`）。"""
        try:
            self._authorized(fix_id, token)
        except FixError:
            return False
        return True

    def tool_list(self, fix_id: object, token: object) -> list[dict]:
        """MCP `tools/list`：注册表里的名字 / 描述 / schema。**不含 tier / waits_for_user**（与 `build_agent_tools` 同口径）。"""
        self._authorized(fix_id, token)
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.schema}
            for t in fixtools.REGISTRY.values()
        ]

    def call_tool(self, fix_id: object, token: object, name: object, args: object) -> dict:
        """MCP `tools/call`：在本进程里 `run_tool`（档位 / 同意卡 / 快照 / 审计全在里面）。阻塞到工具结束；
        卡片等用户的上限是 `ASK_TIMEOUT`，取消立即返回。"""
        s = self._authorized(fix_id, token)
        tool = fixtools.REGISTRY.get(name) if isinstance(name, str) else None
        if tool is None:
            return {"content": "Unknown tool.", "is_error": True}
        detail = activity_detail(args)
        with self._lock:  # 回调到了：CLI 报告的这次调用送达了，它自己的「在等审批」也解除了
            s.cbs[name] = s.cbs.get(name, 0) + 1
            s.approval = None
        self._activity(s, name, "start", detail)
        try:
            with s.tool_lock:
                if s.cancel.is_set() or s.closing.is_set() or s.finished.is_set() or s.ctx is None:
                    return {"content": "The repair session has ended.", "is_error": True}
                result = fixtools.run_tool(tool, s.ctx, args, s.audit)
        finally:
            self._activity(s, name, "end", detail)
        s.steps += 1
        self._notify(s)
        return {"content": result.content, "is_error": bool(result.is_error)}

    # ---- 弹卡 -----------------------------------------------------------
    def _ask(self, s: FixSession, card: dict, redactor: Redactor) -> dict:
        card = redactor.redact_obj(card)
        if card.get("kind") == "done":
            with self._lock:
                said = card.get("outcome")
                if said == "convert" and (s.ctx is None or s.ctx.handoff is None):
                    said = None  # 没有交接记录就不算 convert（`finish` 已拒，这里是第二道）
                s.reported = (
                    said if said in (OUTCOME_FIXED, OUTCOME_CONVERT) else OUTCOME_NOT_FIXED,
                    str(card.get("summary") or "")[:300],
                )
                s.finished.set()
            return {"answer": "ok"}
        kind = card.get("kind")
        if kind not in CARD_KINDS:
            return {"answer": "cancelled"}
        options = {str(o.get("id")) for o in card.get("options") or () if isinstance(o, dict)}
        with self._lock:
            if s.cancel.is_set():
                return {"answer": "cancelled"}
            p = _Pending(secrets.token_urlsafe(9), kind, options, bool(card.get("allow_other")))
            s.pending = p
            s.card = {**card, "id": p.card_id}
            s.state = STATE_WAITING
        self._notify(s)
        deadline = time.monotonic() + ASK_TIMEOUT
        while not p.event.wait(0.2):
            if s.cancel.is_set() or s.closing.is_set():
                p.result = {"answer": "cancelled"}
                break
            if time.monotonic() >= deadline:
                p.result = {"answer": "timeout"}
                break
        with self._lock:
            if s.pending is p:
                s.pending, s.card = None, None
                s.state = STATE_RUNNING
        self._notify(s)
        return p.result or {"answer": "cancelled"}

    def answer(
        self, fix_id: object, scope: str, card_id: object, answer: object, text: object = None
    ) -> dict:
        s = self._find(fix_id, scope)
        with self._lock:
            p = s.pending
            if p is None or not _same(p.card_id, card_id):
                raise FixError("fix_no_pending_card", 409)
            if not isinstance(answer, str) or not answer or len(answer) > 4096 or "\x00" in answer:
                raise FixError("fix_bad_answer")
            result: dict = {"answer": answer}
            if p.kind == "approve":
                ok_ = answer in ("yes", "no")
            elif p.kind == "send":
                ok_ = answer in ("sent", "no")
            elif p.kind == "choice":
                ok_ = (
                    answer in p.options or answer == "skip" or (answer == "other" and p.allow_other)
                )
                if answer == "other":
                    if not isinstance(text, str) or len(text) > 300:
                        raise FixError("fix_bad_answer")
                    result["text"] = text
            else:  # pick：skip 或路径（路径的敏感硬拦在工具里，这里只做形状校验）
                ok_ = True
            if not ok_:
                raise FixError("fix_bad_answer")
            p.result = result
            p.event.set()
        return self.public(s)

    def cancel(self, fix_id: object, scope: str) -> dict:
        s = self._find(fix_id, scope)
        s.cancel.set()
        return self.public(s)

    def cancel_all(self) -> None:
        with self._lock:
            for s in self._by_id.values():
                s.cancel.set()

    def reset_for_tests(self) -> None:
        self.cancel_all()
        for s in list(self._by_id.values()):
            if s.thread is not None:
                s.thread.join(timeout=5)
        with self._lock:
            self._by_id.clear()


SESSIONS = FixSessions()


# --------------------------------------------------------------------------- 连接（API 连接 → 内置执行器的模型）
def connection() -> dict | None:
    """用户连着的、内置循环能直接用的 Key 连接：连接视图（`ai_connections`）里**第一个可用的** Key 连接——有地址 / 密钥 / 型号、
    地址合规、不是账号登录、不是不兼容的旧记录；不再只看各家族「当前选中」的那一条。顺序：旧版选中的接口在前（老用户的结论不变），
    其余按保存顺序。只连了 CLI（官方登录态）时返回 None——那条路走 `cli_agent()` + MCP 接法（D3）。
    返回里带密钥，**只给 `build_model` 用**。"""
    from . import ai_connections

    return ai_connections.first_key_connection(refresh=True)


def cli_agent() -> str | None:
    """没有可用的 API 连接时，能经 MCP 接法修复的 CLI（Codex / Claude Code）；没有 = None。"""
    from . import ai_bridge

    try:
        return ai_bridge.fix_cli_agent()
    except Exception:  # noqa: BLE001 - 探测失败 = 不可用
        return None


def available() -> bool:
    """「使用你的 AI 来修复」能不能真的跑：有内置循环能用的 API 连接，或有可用的 Codex / Claude Code。"""
    try:
        from . import ai_connections

        # 只问「有没有」：不联网（账号登录令牌的刷新留到真要开跑的 `connection()`）
        return ai_connections.first_key_connection() is not None or cli_agent() is not None
    except Exception:  # noqa: BLE001 - 探测失败 = 不可用
        return False


def mcp_launcher() -> list[str]:
    """本地 MCP 修复模式服务的启动命令（`tavotto fix-mcp`）。前缀与内置执行器同一出处（`ai_agents.tavotto_launcher`）：
    冻结产物优先用同目录的 console 版 `tavotto-cli`，源码 / pip 形态走 `-m tavotto`。"""
    from . import ai_agents

    return [*ai_agents.tavotto_launcher(), "fix-mcp"]


def cli_launcher(
    agent_id: str, *, callback_url: str, project_root: Path
) -> Callable[[str, str, str], CliLaunch]:
    """→ `SESSIONS.start(cli=…)` 要的构造器 `(fix_id, token, prompt) -> CliLaunch`。令牌只放进子进程环境。"""

    def build(fix_id: str, token: str, prompt: str) -> CliLaunch:
        from . import ai_agents, ai_bridge, fixmcp

        run_ctx = ai_agents.FixRunContext(
            argv=[],
            prompt=prompt,
            cwd=str(project_root),
            mcp_command=[*mcp_launcher(), "--url", callback_url, "--fix-id", fix_id],
            mcp_name=fixmcp.SERVER_NAME,
            token_env=fixmcp.ENV_TOKEN,
            tool_timeout_s=int(ASK_TIMEOUT) + 120,
            deny=fixtools.readscope.deny_spec(),
        )
        argv, extra_env = ai_bridge.fix_cli_command(agent_id, run_ctx)
        env = ai_bridge._spawn_env(argv[0], {**extra_env, fixmcp.ENV_TOKEN: token})
        agent = ai_agents.get_agent(agent_id)
        parse = agent.fix_event_parser() if agent else None
        return CliLaunch(argv=argv, env=env, cwd=str(project_root), parse=parse)

    return build


def build_model(conn: dict):
    """连接 → `HttpModel`。密钥只放进模型对象（`repr` 不含），返回的 `Redactor` 用于过滤卡片里的回显。"""
    wire = (
        AnthropicWire(bearer_only=True)
        if conn.get("auth") == "bearer" and conn["wire"] == "anthropic"
        else WIRES[conn["wire"]]
    )
    model = loop.HttpModel(
        wire,
        base_url=conn["base_url"],
        api_key=conn["api_key"],
        model=conn["model"],
    )
    return model, Redactor(conn["api_key"])


def audit_tail(session_ids: set[str], limit: int = 200) -> list[dict]:
    """诊断包「修复审计」：`fix_audit.jsonl` 里属于 `session_ids`（当前项目的修复会话）的末尾若干行；
    没给会话 id 就是空。路径类参数的处理在 `diagdetail`（与诊断包其余文件名同一规矩）。"""
    if not session_ids:
        return []
    import json

    try:
        lines = AuditLog().path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("session") in session_ids:
            out.append(rec)
    return out[-limit:]


__all__ = [
    "SESSIONS",
    "FixError",
    "FixSessions",
    "FixToolset",
    "NO_REDACTION",
    "CliLaunch",
    "audit_tail",
    "available",
    "build_model",
    "cli_agent",
    "cli_launcher",
    "cli_prompt",
    "connection",
    "mcp_launcher",
]
