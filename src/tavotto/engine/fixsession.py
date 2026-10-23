"""AI 修复会话（智能修复主线 D2b，ADR 0148 / 0149）：「✦ 使用你的 AI 来修复」背后的 `fix_environment` 任务。

一次会话 = 一个线程里跑内置执行器的「模型 ↔ 工具」循环（`agent_runner.loop.run`），工具是 `fixtools.REGISTRY` 里**全部已登记的**
（档位由 `FixContext.for_fix_session` 决定，本模块不改）。循环**在本进程**里跑，因为工具要碰应用的状态（准备会话报告、
弹卡片等用户点）；模型只有 `HttpModel` 一条出站路径，密钥只在内存、不进事件 / 卡片 / 审计 / 日志。

* **同一时刻一个会话**；会话 id 用 `secrets` 生成、比较用 `compare_digest`；会话绑定项目，别的项目读不到。
* **`ctx.ask`**：把卡片挂在会话上、推一条「重读」提示（SSE `ai.fix`，不带内容），线程阻塞等 `answer()` 或取消 / 超时。
  回答按卡种类校验（`approve` → yes/no；`choice` → 选项 id / other / skip；`pick` → skip 或路径；`send` → sent/no）。
* **预算**：步数 / 墙钟（用户思考的时间也算在墙钟里）/ token；`finish` 之后不再请求模型。
* 结束后由前端重跑准备会话验证——本模块不替用户判「修好了」，`finish(fixed)` 只是 AI 的说法。
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import fixtools
from .agent_runner import loop
from .agent_runner.redact import NO_REDACTION, Redactor
from .agent_runner.tools import Limits, RunEnvelope, Tool
from .agent_runner.wires import WIRES, Turn, check_base_url
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

STATE_RUNNING = "running"
STATE_WAITING = "waiting"
STATE_FINISHED = "finished"

#: 终局 outcome（`finish` 之外的结局）
OUTCOME_FIXED = "fixed"
OUTCOME_NOT_FIXED = "not_fixed"
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
or files, call propose_fix with ONE plain sentence and wait: if the user declines, do not do it. Tavotto takes a \
snapshot before each change so it can be rolled back; if a change did not help, roll it back.
4. If you need something only the user knows, use ask_user (a multiple-choice question) or pick_file (the system file \
chooser). Never ask for a password, key or token.
5. If you cannot fix it, call prepare_diagnostics with a short note of what you found and tried (the user decides \
whether to send), then finish with outcome not_fixed.
6. ALWAYS end by calling finish exactly once: outcome `fixed` only if a trial run now succeeds, otherwise `not_fixed`, \
plus one plain sentence for the user.

Hard rules: never edit or delete the user's scripts or data; never read credential locations (they are blocked \
anyway); you have no network access except through the tools; stay within the budget — do not repeat a failed step \
more than twice.
"""


def _first_message(ctx: fixtools.FixContext, script: str) -> str:
    report = read_run_report(ctx, {})
    body = report.content if not report.is_error else "(no report available; call read_run_report)"
    return (
        f"The script `{script}` failed in this project. Fix the cause.\n\n"
        f"Current preparation report (already redacted):\n{body}"
    )


class FixToolset:
    """`loop.run` 要的任务工具集：每次会话现建，工具带本次会话的 `FixContext`。"""

    task_type = "fix_environment"
    default_limits = FIX_LIMITS

    def __init__(self, ctx: fixtools.FixContext, script: str, audit: AuditLog | None = None):
        self.ctx, self.script, self.audit = ctx, script, audit

    def tools(self, envelope: RunEnvelope) -> list[Tool]:
        return fixtools.build_agent_tools(self.ctx, self.audit)

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
            "steps": s.steps,
            "seq": s.seq,
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
        model: object,
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
        toolset = FixToolset(ctx, script, audit)
        wrapped = _FinishAware(model, s.finished)

        def work() -> None:
            self._work(s, wrapped, toolset, envelope)

        if background:
            s.thread = threading.Thread(target=work, name=f"fix-{s.id}", daemon=True)
            s.thread.start()
        else:
            work()
        return s

    def _work(self, s: FixSession, model, toolset: FixToolset, envelope: RunEnvelope) -> None:
        done: dict = {}

        def emit(event: dict) -> None:
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
        with self._lock:
            reported = s.reported
            if s.cancel.is_set() and reported is None:
                s.outcome, s.summary, s.reason = OUTCOME_CANCELLED, "", "cancelled"
            elif reported is not None:
                s.outcome, s.summary = reported
                s.reason = ""
            else:
                # AI 没调 finish 就停了（预算 / 网络 / 额度 / 认证…）：按没修好算，原因码给界面选一句话
                s.outcome = OUTCOME_FAILED
                s.reason = str(done.get("reason") or "internal")[:24]
            s.state, s.card, s.pending = STATE_FINISHED, None, None
        self._notify(s)

    # ---- 弹卡 -----------------------------------------------------------
    def _ask(self, s: FixSession, card: dict, redactor: Redactor) -> dict:
        card = redactor.redact_obj(card)
        if card.get("kind") == "done":
            with self._lock:
                s.reported = (
                    OUTCOME_FIXED if card.get("outcome") == "fixed" else OUTCOME_NOT_FIXED,
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
            if s.cancel.is_set():
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
    """用户连着的、内置执行器能直接用的 API 连接：该 agent 当前选中的第三方接口，有地址 / 密钥 / 型号、地址合规。
    只连了 CLI（官方登录态）时返回 None——CLI 经 MCP 的接法是 D3，此处暂不可用。返回里带密钥，**只给 `build_model` 用**。"""
    from . import ai_agents, ai_providers

    for agent in ai_providers.agents():
        rec = ai_providers.resolve(agent)
        if not rec:
            continue
        family = getattr(ai_agents.get_agent(agent), "endpoint_family", None)
        wire = {"anthropic": "anthropic", "openai": "openai-responses"}.get(str(family))
        model = str(rec.get("default_model") or "").strip() or next(
            iter(rec.get("models") or []), ""
        )
        base, key = str(rec.get("base_url") or ""), str(rec.get("api_key") or "")
        if wire and model and key and check_base_url(base) is None:
            return {"wire": wire, "base_url": base, "api_key": key, "model": model}
    return None


def available() -> bool:
    try:
        return connection() is not None
    except Exception:  # noqa: BLE001 - 探测失败 = 不可用
        return False


def build_model(conn: dict):
    """连接 → `HttpModel`。密钥只放进模型对象（`repr` 不含），返回的 `Redactor` 用于过滤卡片里的回显。"""
    model = loop.HttpModel(
        WIRES[conn["wire"]],
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
    "audit_tail",
    "available",
    "build_model",
    "connection",
]
