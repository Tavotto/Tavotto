"""脚本 `input()` 的父进程侧中介（ADR 0099 §二 / §五）。

worker 在自己的会话缓存目录里写 `req-<n>.json` 发问（`scriptinput`）；这里在 **`ensure_built` 这一次请求
期间**起一个轮询线程（`serving()`，两条控制面的 `ensure_built` 都包在它里面——只有一份实现），看到新的
请求就按 worker 身上的策略决定怎么答，回复用 `atomicio` 写 `reply-<n>.json`：

1. 写回 verify 的一次性重放（`ReplayAnswers`）：只按热态 build 实际用到的那组答案严格重放，对不上就
   「无答案」——从不问人，热态 == 重放因此成立；
2. 池会话：记住的答案（`scriptanswers`）命中就立即回填，并告诉界面「已用上次的答案」；
3. 没记住、且此刻有能答题的界面连着 → 发 `script.input_requested`，等 `answer()` / `stop()`；
4. 没人能答 → 立即回「无答案」，worker 抛 `ScriptNeedsInput`，build 以 `script_needs_input` 失败。**绝不卡死。**

界面那一侧（发 SSE、数有没有能答题的客户端）由 app 用 `set_frontend()` 接进来：本模块纯标准库，不 import Flask。
日志只记「第 N 问已作答」，不记提示与答案（可能含路径）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import atomicio, scriptanswers, scriptinput

LOG = logging.getLogger("tavotto.engine.inputbroker")

#: 父进程看会合目录的间隔（秒）。只在 build 期间轮询。
BROKER_POLL = 0.2
#: `serving()` 退出时等轮询线程收尾的上限（秒）。
_JOIN_TIMEOUT = 2.0

#: 「无答案」的三种理由（进回复，也进 `script_needs_input` 的载荷）。
REASON_NO_CLIENT = "no_interactive_client"
REASON_REPLAY_MISSING = "replay_missing"


@dataclass(frozen=True)
class ReplayAnswers:
    """一次性重放的答案表：热态 build 响应里的 `script_inputs` 原样。"""

    answers: tuple = ()

    @classmethod
    def of(cls, records) -> "ReplayAnswers":
        return cls(tuple(dict(r) for r in (records or []) if isinstance(r, dict)))

    def reply_for(self, index: int, prompt: str, prompt_id: str | None = None) -> dict:
        for r in self.answers:
            if (
                r.get("index") == index
                and r.get("prompt") == prompt
                and r.get("prompt_id") == prompt_id
            ):
                answer = r.get("answer")
                return {"answer": answer} if isinstance(answer, str) else {"eof": True}
        return {"no_answer": True, "reason": REASON_REPLAY_MISSING}


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
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    #: 同一问的作答 / 丢弃串行：只有赢下的那一次能落盘答案（两个界面同时答时，记住的 == worker 拿到的）
    guard: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def event_payload(self) -> dict:
        return {
            "id": self.id,
            "script": self.script,
            "index": self.index,
            # 不叫 `kind`：SSE 客户端把载荷里的 kind 当事件名处理（`subscribeEvents`）
            "input_kind": self.kind,
            "prompt": self.prompt,
            "stdout_tail": self.stdout_tail,
        }


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


def _reply(directory: Path, index: int, payload: dict) -> None:
    atomicio.write_json(directory / scriptinput.reply_name(index), payload)


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
            _emit("script.input_closed", p.project_root, {"id": p.id, "reason": "timed_out"})
            return None
        if not (eof or text is None) and p.kind != "getpass" and not p.private:
            # 先校验、先落盘，**成功之后才出队**：答案不合法（太长）或写盘失败时这一问仍在等，界面改好再交一次
            # 照样答得上（Codex #680 P2）。先出队的话改好的那次拿到 404，而 worker 白等到超时。
            # 口令不落盘：getpass 的答案每次都问（ADR 0099 §四）
            try:
                scriptanswers.remember(p.project_root, p.script, p.index, p.prompt, text, p.kind)
            except BaseException:
                scriptinput.release(
                    p.directory, p.index
                )  # 定案放掉：worker 照旧等，也照旧能到点超时
                raise
        with _lock:
            del _pending[pending_id]
    if eof or text is None:
        _reply(p.directory, p.index, {"eof": True})
    else:
        _reply(p.directory, p.index, {"answer": text})
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
    _emit("script.input_closed", p.project_root, {"id": p.id, "reason": reason})
    return p


def _decide(worker, directory: Path, request: dict) -> None:
    index = request.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        return
    prompt = str(request.get("prompt") or "")[: scriptinput.PROMPT_CHARS + 1]
    kind = request.get("kind") if request.get("kind") in scriptinput.KINDS else "input"
    policy = getattr(worker, "script_input_policy", None)
    if isinstance(policy, ReplayAnswers):
        _reply(directory, index, policy.reply_for(index, prompt, request.get("prompt_id")))
        return
    # 键用父进程自己记的脚本名与项目，不信 worker 写来的 `script`
    project_root = str(worker.figures_dir)
    script = str(worker.script_name)
    # Suppressed private prompts deliberately display the same marker. Never
    # auto-fill/remember them by that marker; frozen replay uses their keyed IDs.
    private = bool(getattr(getattr(worker, "run", None), "sensitive", False))
    remembered = (
        scriptanswers.lookup(project_root, script, index, prompt)
        if kind != "getpass" and not private
        else None
    )
    if remembered is not None:
        _reply(directory, index, {"answer": remembered})
        _emit(
            "script.input_autofilled",
            project_root,
            {"script": script, "index": index, "prompt": prompt, "answer": remembered},
        )
        return
    if _has_answerer is None or not _has_answerer(project_root):
        _reply(directory, index, {"no_answer": True, "reason": REASON_NO_CLIENT})
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
    )
    with _lock:
        _pending[p.id] = p
    _emit("script.input_requested", project_root, p.event_payload())


def _serve(worker, directory: Path, stop: threading.Event) -> None:
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
                    _decide(worker, directory, request)
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


@contextlib.contextmanager
def serving(worker):
    """在 `worker` 这一次 build 期间当它的答题方。退出时关掉还在等的问、删掉会合目录。"""
    out_dir = getattr(worker, "out_dir", None)
    if out_dir is None:
        # 没有会话缓存目录的就没有会合目录可轮询（只有测试里的替身会这样）：脚本要输入时照样由 worker 自己到点回 EOF
        yield
        return
    directory = Path(out_dir) / scriptinput.DIRNAME
    stop = threading.Event()
    thread = threading.Thread(
        target=_serve, args=(worker, directory, stop), name="tavotto-script-input", daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_JOIN_TIMEOUT)
        with _lock:
            stale = [p.id for p in _pending.values() if p.directory == directory]
        for pid in stale:
            discard(pid, "finished")
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # 目录在会话缓存里，下次 build 开头 worker 会清空它；删不掉只记一笔
            LOG.warning("脚本输入会合目录删除失败: %r", exc)
