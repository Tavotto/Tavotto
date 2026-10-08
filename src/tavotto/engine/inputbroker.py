"""脚本 `input()` 的父进程侧中介（ADR 0099 §二 / §五）。

worker 在自己的会话缓存目录里写 `req-<n>.json` 发问（`scriptinput`）；这里在 **`ensure_built` 这一次请求
期间**起一个轮询线程（`serving()`，两条控制面的 `ensure_built` 都包在它里面——只有一份实现），看到新的
请求就按 worker 身上的策略决定怎么答，回复用 `atomicio` 写 `reply-<n>.json`：

1. 写回 verify 的一次性重放（`ReplayAnswers.of`）：只按热态 build 实际用到的那组答案严格重放，对不上就
   「无答案」——从不问人，热态 == 重放因此成立；**口令除外**：热态没留它，重新问（没人能答就失败）；
2. 池会话、这批产物有执行转录（`inputtranscript`，T08）：冷重放按**那一次执行**的转录作答（`ReplayAnswers.transcript`），
   上下文对不上就重新问（旧答案只当建议）——不读之后被改写的项目答案文件；
3. 池会话、没有转录：记住的答案（`scriptanswers.recall`）连上下文都对上才立即回填，并告诉界面「已用上次的答案」；
4. 否则此刻有能答题的界面连着 → 发 `script.input_requested`（带建议与理由），等 `answer()` / `stop()`；
5. 没人能答 → 立即回「无答案」，worker 抛 `ScriptNeedsInput`，build 以 `script_needs_input` 失败。**绝不卡死。**

答案策略在 `serving()` 进门那一刻冻结（执行前冻结已知输入）；这一次 build 里每一问的去向记成有界的计数
（`InputFacts`），build 结束时挂在 worker / 异常上，供任务诊断的白名单投影——只有计数与闭集理由，没有提示与答案。

界面那一侧（发 SSE、数有没有能答题的客户端）由 app 用 `set_frontend()` 接进来：本模块纯标准库，不 import Flask。
日志只记「第 N 问已作答」，不记提示与答案（可能含路径）。
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import atomicio, inputtranscript, scriptanswers, scriptinput, taskdiag

LOG = logging.getLogger("tavotto.engine.inputbroker")

#: 父进程看会合目录的间隔（秒）。只在 build 期间轮询。
BROKER_POLL = 0.2
#: `serving()` 退出时等轮询线程收尾的上限（秒）。
_JOIN_TIMEOUT = 2.0

#: 「无答案」的理由（进回复，也进 `script_needs_input` 的载荷与任务诊断，闭集）。
REASON_NO_CLIENT = "no_interactive_client"
REASON_REPLAY_MISSING = "replay_missing"
#: 重放到口令那一问：口令从不留存，要重新提供，而没有能答题的界面（T08）
REASON_SECRET_REQUIRED = "secret_required"
#: 冷重放按执行转录作答，这一问的上下文 / 提示对不上，而没有能答题的界面（T08）
REASON_TRANSCRIPT_MISMATCH = "transcript_mismatch"
REASONS = (
    REASON_NO_CLIENT,
    REASON_REPLAY_MISSING,
    REASON_SECRET_REQUIRED,
    REASON_TRANSCRIPT_MISMATCH,
)

#: `ReplayAnswers.match()` 的四种结论
MATCH_ANSWER = "answer"
MATCH_EOF = "eof"
MATCH_SECRET = "secret"
MATCH_MISMATCH = "mismatch"


@dataclass(frozen=True)
class ReplayAnswers:
    """按一次执行实际用到的答案重放：写回 verify 的热态那组（`of`，严格），或冷重放的执行转录（`transcript`）。"""

    answers: tuple = ()
    #: True = 冷重放：对不上就去问界面（旧答案只当建议）；False = 写回 verify：对不上就失败，从不问人
    ask_on_mismatch: bool = False
    transcript_id: str | None = None

    @classmethod
    def of(cls, records) -> "ReplayAnswers":
        return cls(tuple(dict(r) for r in (records or []) if isinstance(r, dict)))

    @classmethod
    def transcript(cls, t: "inputtranscript.Transcript") -> "ReplayAnswers":
        return cls(tuple(dict(e) for e in t.entries), ask_on_mismatch=True, transcript_id=t.id)

    def match(
        self, index: int, prompt: str, kind: str, context: str | None, prompt_id: str | None = None
    ) -> tuple[str, dict | None]:
        """这一问能不能按记录作答：(结论, 记录里同序号的那条)。

        对上 = 同序号、同提示、同读取方式，且两边都有上下文摘要时摘要相同。口令那一问（`secret`）记录里没有值：
        结论是「要重新提供」，绝不当成空串或 EOF。"""
        for r in self.answers:
            if r.get("index") != index:
                continue
            same = (
                r.get("prompt") == prompt
                and r.get("prompt_id") == prompt_id
                and (r.get("kind") in (None, kind))
                and not (r.get("context") and context and r.get("context") != context)
            )
            if not same:
                return MATCH_MISMATCH, r
            if r.get("secret"):
                return MATCH_SECRET, r
            return (MATCH_ANSWER, r) if isinstance(r.get("answer"), str) else (MATCH_EOF, r)
        return MATCH_MISMATCH, None


@dataclass
class InputFacts:
    """一次 build 里每一问的去向（只有计数与闭集理由）：任务诊断的白名单投影读它。"""

    asked: int = 0
    shown: int = 0
    answered: int = 0
    eof: int = 0
    autofilled: int = 0
    replayed: int = 0
    timed_out: int = 0
    stopped: int = 0
    secret: int = 0
    no_answer: str | None = None
    transcript: str | None = None

    def payload(self) -> dict:
        return dataclasses.asdict(self)


_FACT_COUNTS = (
    "asked",
    "shown",
    "answered",
    "eof",
    "autofilled",
    "replayed",
    "timed_out",
    "stopped",
    "secret",
)


def facts_projection(facts) -> dict | None:
    """任务诊断里「这次运行的输入」那一段（T04 白名单）：逐字段挑计数与闭集理由；**没有**提示、答案、
    输出片段、上下文摘要、转录 id 之外的任何东西。一问都没问过 = None（不加这一段）。"""
    if not isinstance(facts, dict) or not taskdiag.count(facts.get("asked")):
        return None
    out = {k: taskdiag.count(facts.get(k)) for k in _FACT_COUNTS}
    out["no_answer"] = taskdiag.closed(facts.get("no_answer"), REASONS)
    out["transcript_replay"] = facts.get("transcript") is not None
    return out


@dataclass
class Pending:
    """一问正在等界面作答。"""

    id: str
    project_root: str
    script: str
    index: int
    prompt: str
    kind: str
    directory: Path
    stdout_tail: str = ""
    private: bool = False
    #: 上下文摘要与运行配置引用（T08）：作答后按它们记住——本机比对用，**不进**界面事件
    context: str | None = None
    run_config: str | None = None
    #: 旧答案只当建议（不预填、不自动交）；`recheck` 说为什么要重新确认（闭集）
    suggestion: str | None = None
    recheck: str | None = None
    facts: InputFacts | None = field(default=None, repr=False)
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    #: 同一问的作答 / 丢弃串行：只有赢下的那一次能落盘答案（两个界面同时答时，记住的 == worker 拿到的）
    guard: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def secret(self) -> bool:
        return self.kind == "getpass"

    def event_payload(self) -> dict:
        return {
            "id": self.id,
            "script": self.script,
            "index": self.index,
            # 不叫 `kind`：SSE 客户端把载荷里的 kind 当事件名处理（`subscribeEvents`）
            "input_kind": self.kind,
            "prompt": self.prompt,
            "stdout_tail": self.stdout_tail,
            # 口令：界面用密码框、不记住；建议永远不给口令
            "secret": self.secret,
            "suggestion": None if self.secret else self.suggestion,
            "recheck": self.recheck,
        }

    def public(self) -> dict:
        """准备会话报告里的投影（T08）：认得出是哪一问、要不要掩码——**没有**提示、输出片段与建议。"""
        return {"id": self.id, "index": self.index, "input_kind": self.kind, "secret": self.secret}


_publish: Callable[[str, str, dict], None] | None = None
_has_answerer: Callable[[str], bool] | None = None
_pending: dict[str, Pending] = {}
_lock = threading.Lock()


def set_frontend(
    publish: Callable[[str, str, dict], None], has_answerer: Callable[[str], bool]
) -> None:
    """app 接进来：`publish(事件名, 项目根, 载荷)`；`has_answerer(项目根)` = 此刻有没有能答题、**且正在看这个
    项目**的界面（开着别的项目的界面按 `pj` 丢掉这一问的事件，算不上答题方）。"""
    global _publish, _has_answerer
    _publish, _has_answerer = publish, has_answerer


def _emit(event: str, project_root: str, data: dict) -> None:
    if _publish is None:
        return
    try:
        _publish(event, project_root, data)
    except Exception:  # noqa: BLE001 — 通知失败不能让问答线程死掉
        LOG.exception("脚本输入事件发送失败: %s", event)


def _reply(directory: Path, index: int, payload: dict, *, private: bool = False) -> None:
    """写回复。敏感会话的答案是明文：临时文件权限收成 0600（POSIX），读它的 worker 读完即删（Codex #812 P1）；
    写失败由 `atomicio` 清掉临时文件，异常照常上抛。"""
    path = directory / scriptinput.reply_name(index)
    data = atomicio.dumps_json(payload)
    if private:
        atomicio.write_bytes(path, data, mode=0o600)
    else:
        atomicio.write_bytes(path, data)


def _count(facts: InputFacts | None, name: str) -> None:
    if facts is None:
        return
    with _lock:
        setattr(facts, name, getattr(facts, name) + 1)


def pending() -> list[Pending]:
    with _lock:
        return list(_pending.values())


def get_pending(pending_id: str) -> Pending | None:
    with _lock:
        return _pending.get(pending_id)


def answer(pending_id: str, text: str | None, *, eof: bool = False) -> Pending | None:
    """界面作答（`eof=True` = 「结束输入」）。答案按项目记住（getpass 的除外）。不在等的回 None。"""
    with _lock:
        p = _pending.get(pending_id)
    if p is None:
        return None
    # 占住这一问再校验、落盘：两个界面同时答同一问时，后到的等先到的做完——先到的成功了它就拿到「不在等」，
    # 绝不在输掉之后再改写记住的答案（否则 worker 拿到 A、下次运行却用 B，Codex #680 P1）
    with p.guard:
        with _lock:
            if _pending.get(pending_id) is not p:
                return None  # 同一问已被答掉 / 被停止了
        # 与 worker 的「等到超时」抢定案：worker 已经按 EOF 往下跑了的话，这个答案既不回给脚本也不记住——
        # 本次输出没用它，下次运行却用它，就是「显示的结果」与「记住的答案」对不上（Codex #680 P2）
        try:
            won = scriptinput.claim(p.directory, p.index, scriptinput.CLAIM_ANSWER)
        except OSError:
            won = False  # 会合目录已经删了：build 结束了
        if not won:
            with _lock:
                _pending.pop(pending_id, None)
            p.done.set()
            _count(p.facts, "timed_out")
            _emit("script.input_closed", p.project_root, {"id": p.id, "reason": "timed_out"})
            return None
        if not (eof or text is None) and p.kind != "getpass" and not p.private:
            # 先校验、先落盘，**成功之后才出队**：答案不合法（太长）或写盘失败时这一问仍在等，界面改好再交一次
            # 照样答得上（Codex #680 P2）。先出队的话改好的那次拿到 404，而 worker 白等到超时。
            # 口令不落盘：getpass 的答案每次都问（ADR 0099 §四）。按上下文 + 运行配置记（T08）
            try:
                scriptanswers.remember(
                    p.project_root,
                    p.script,
                    p.index,
                    p.prompt,
                    text,
                    p.kind,
                    context=p.context,
                    run_config=p.run_config,
                )
            except BaseException:
                scriptinput.release(
                    p.directory, p.index
                )  # 定案放掉：worker 照旧等，也照旧能到点超时
                raise
        with _lock:
            del _pending[pending_id]
    if eof or text is None:
        _reply(p.directory, p.index, {"eof": True})
        _count(p.facts, "eof")
    else:
        _reply(p.directory, p.index, {"answer": text}, private=p.private or p.kind == "getpass")
        _count(p.facts, "answered")
    LOG.info("脚本输入：第 %d 问已作答", p.index)
    p.done.set()
    _emit("script.input_closed", p.project_root, {"id": p.id, "reason": "answered"})
    return p


def discard(pending_id: str, reason: str) -> Pending | None:
    """不再等这一问（停止脚本 / build 结束）。不写回复——worker 这时已经被杀或已经不在了。"""
    with _lock:
        p = _pending.get(pending_id)
    if p is None:
        return None
    with p.guard:  # 正在落盘的作答先做完：它赢了就不再是「丢弃」
        with _lock:
            if _pending.pop(pending_id, None) is None:
                return None
    p.done.set()
    if reason in ("stopped", "timed_out"):
        _count(p.facts, reason)
    _emit("script.input_closed", p.project_root, {"id": p.id, "reason": reason})
    return p


def _run_config_of(worker) -> str | None:
    run = getattr(worker, "run", None)
    cid = getattr(run, "config_id", None)
    return cid if isinstance(cid, str) and cid else None


def _no_answer(directory: Path, index: int, reason: str, facts: InputFacts | None) -> None:
    _reply(directory, index, {"no_answer": True, "reason": reason})
    if facts is not None:
        with _lock:
            facts.no_answer = reason


def _decide(
    worker,
    directory: Path,
    request: dict,
    policy: "ReplayAnswers | None" = None,
    facts: InputFacts | None = None,
) -> None:
    index = request.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        return
    prompt = str(request.get("prompt") or "")[: scriptinput.PROMPT_CHARS + 1]
    kind = request.get("kind") if request.get("kind") in scriptinput.KINDS else "input"
    context = request.get("context") if isinstance(request.get("context"), str) else None
    # 键用父进程自己记的脚本名与项目，不信 worker 写来的 `script`
    project_root = str(worker.figures_dir)
    script = str(worker.script_name)
    run_config = _run_config_of(worker)
    private = bool(getattr(getattr(worker, "run", None), "sensitive", False))
    _count(facts, "asked")
    if kind == "getpass":
        _count(facts, "secret")
    suggestion = recheck = None
    reason = REASON_NO_CLIENT
    if isinstance(policy, ReplayAnswers):
        verdict, entry = policy.match(index, prompt, kind, context, request.get("prompt_id"))
        if verdict in (MATCH_ANSWER, MATCH_EOF):
            payload = {"answer": entry["answer"]} if verdict == MATCH_ANSWER else {"eof": True}
            _reply(directory, index, payload)
            _count(facts, "replayed")
            if verdict == MATCH_ANSWER and policy.ask_on_mismatch:
                _emit(
                    "script.input_autofilled",
                    project_root,
                    {"script": script, "index": index, "prompt": prompt, "answer": entry["answer"]},
                )
            return
        if verdict == MATCH_SECRET:
            reason = REASON_SECRET_REQUIRED  # 口令从不留存：重新问，没人能答就明确失败
        elif not policy.ask_on_mismatch:
            _no_answer(directory, index, REASON_REPLAY_MISSING, facts)
            return
        else:
            reason = REASON_TRANSCRIPT_MISMATCH
            recheck = scriptanswers.RECHECK_CONTEXT
            if (
                entry is not None
                and not entry.get("secret")
                and entry.get("prompt") == prompt
                and isinstance(entry.get("answer"), str)
            ):
                suggestion = entry["answer"]
    elif kind != "getpass" and not private:
        found = scriptanswers.recall(
            project_root, script, index, prompt, kind=kind, context=context, run_config=run_config
        )
        if found.answer is not None:
            _reply(directory, index, {"answer": found.answer})
            _count(facts, "autofilled")
            _emit(
                "script.input_autofilled",
                project_root,
                {"script": script, "index": index, "prompt": prompt, "answer": found.answer},
            )
            return
        suggestion, recheck = found.suggestion, found.recheck
    if _has_answerer is None or not _has_answerer(project_root):
        _no_answer(directory, index, reason, facts)
        return
    tail = str(request.get("stdout_tail") or "")[-scriptinput.TAIL_CHARS :]
    p = Pending(
        id=uuid.uuid4().hex,
        project_root=project_root,
        script=script,
        index=index,
        prompt=prompt,
        kind=kind,
        directory=directory,
        stdout_tail=tail,
        private=private,
        context=context,
        run_config=run_config,
        suggestion=suggestion,
        recheck=recheck,
        facts=facts,
    )
    with _lock:
        _pending[p.id] = p
    _count(facts, "shown")
    _emit("script.input_requested", project_root, p.event_payload())


def _serve(
    worker,
    directory: Path,
    stop: threading.Event,
    policy: "ReplayAnswers | None" = None,
    facts: InputFacts | None = None,
) -> None:
    seen: set[int] = set()
    while not stop.is_set():
        try:
            names = [e.name for e in directory.iterdir()] if directory.is_dir() else []
        except OSError:
            names = []
        for name in sorted(names):
            index = scriptinput.index_of(name, "req")
            if index is None or index in seen:
                continue
            seen.add(index)
            try:
                request = json.loads((directory / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                seen.discard(index)  # 原子写下不该读到半截；读失败下一轮再试
                continue
            if isinstance(request, dict):
                try:
                    _decide(worker, directory, request, policy, facts)
                except Exception:  # noqa: BLE001 — 一问答坏了也不能让轮询线程死掉
                    LOG.exception("脚本输入第 %d 问处理失败", index)
        _retire_timed_out(directory)
        stop.wait(BROKER_POLL)


def _retire_timed_out(directory: Path) -> None:
    """worker 已经等到超时、按 EOF 往下跑了的问：当场收起（界面关掉那个框），不留到 build 结束（Codex #680 P2）。"""
    with _lock:
        mine = [p for p in _pending.values() if p.directory == directory]
    for p in mine:
        if scriptinput.claimed_by(directory, p.index) == scriptinput.CLAIM_TIMEOUT:
            discard(p.id, "timed_out")


def _frozen_policy(worker) -> "ReplayAnswers | None":
    """这一次 build 的答案策略，进门时冻结：显式给的（写回 verify）原样；池会话有执行转录就按转录重放（T08）。"""
    policy = getattr(worker, "script_input_policy", None)
    if policy is not None:
        return policy
    # Private runs may replay their explicit hot records, but must never read
    # or write durable answer transcripts under a sensitive configuration.
    if getattr(getattr(worker, "run", None), "sensitive", False):
        return None
    try:
        found = inputtranscript.lookup(
            str(worker.figures_dir), str(worker.script_name), _run_config_of(worker)
        )
    except (OSError, AttributeError, TypeError):
        return None
    return ReplayAnswers.transcript(found) if found is not None else None


def finished(worker, records) -> None:
    """一次 build **成功**结束：把它实际用到的问答绑成这批产物的执行转录（T08）。写回 verify 的一次性重放
    不是新产物的执行，不改转录。落盘失败只记一笔——转录缺了，冷重放回到上下文匹配，不会静默套错。"""
    policy = getattr(worker, "script_input_policy", None)
    if isinstance(policy, ReplayAnswers) and not policy.ask_on_mismatch:
        return
    if getattr(getattr(worker, "run", None), "sensitive", False):
        return
    try:
        t = inputtranscript.bind(
            str(worker.figures_dir), str(worker.script_name), _run_config_of(worker), records
        )
    except (OSError, AttributeError, TypeError) as exc:
        LOG.warning("脚本输入转录写入失败: %r", exc)
        return
    with contextlib.suppress(AttributeError):
        worker.last_input_transcript = t.counts() if t is not None else None


@contextlib.contextmanager
def serving(worker):
    """在 `worker` 这一次 build 期间当它的答题方。退出时关掉还在等的问、删掉会合目录；这一次的问答去向
    （`InputFacts.payload()`）挂在 `worker.last_input_facts` 上，build 失败时也挂在异常的 `input_facts` 上。"""
    out_dir = getattr(worker, "out_dir", None)
    if out_dir is None:
        # 没有会话缓存目录的就没有会合目录可轮询（只有测试里的替身会这样）：脚本要输入时照样由 worker 自己到点回 EOF
        yield
        return
    directory = Path(out_dir) / scriptinput.DIRNAME
    policy = _frozen_policy(worker)
    facts = InputFacts(transcript=getattr(policy, "transcript_id", None))
    stop = threading.Event()
    thread = threading.Thread(
        target=_serve,
        args=(worker, directory, stop, policy, facts),
        name="tavotto-script-input",
        daemon=True,
    )
    thread.start()
    failure: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        failure = exc
        raise
    finally:
        stop.set()
        thread.join(timeout=_JOIN_TIMEOUT)
        with _lock:
            stale = [p for p in _pending.values() if p.directory == directory]
        for p in stale:
            # worker 已经按超时定了案、轮询还没来得及收起的：如实算「超时」，不混进「build 结束」
            timed_out = scriptinput.claimed_by(directory, p.index) == scriptinput.CLAIM_TIMEOUT
            discard(p.id, "timed_out" if timed_out else "finished")
        with _lock:
            snapshot = facts.payload()
        with contextlib.suppress(AttributeError):
            worker.last_input_facts = snapshot
        if failure is not None:
            with contextlib.suppress(AttributeError, TypeError):
                failure.input_facts = snapshot
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # 目录在会话缓存里，下次 build 开头 worker 会清空它；删不掉只记一笔
            LOG.warning("脚本输入会合目录删除失败: %r", exc)
