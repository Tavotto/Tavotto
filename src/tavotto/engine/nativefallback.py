"""MCP 打开图时的 safe → native 回退（ADR 0127 修订 2026-11-17）。

MCP 桥（`codex-plugin/mcp/tavotto_mcp/bridge.py`）在**自己的进程里**渲染一张图，safe worker 以
`safe_file_operation_blocked` 收场（典型：脚本 `savefig("fig.png")` 后又 `Image.open("fig.png")` 读回，
safe 拦下不落盘的 savefig）时，经这里换成真实的 Python 进程再跑一次，之后这条 MCP 会话的编辑 / 预览 / 导出
都落在那张 native 活图上。

与桌面回退的分工：

* **判据同一份**：`execpolicy.decide_fallback(mcp_initiated=True)`——只顶替第一道来源判据；运维开关、项目的
  「只用安全运行」、原因必须是 `blocked`、单向、至多一次一道不少。解释器沿用 safe 当时那一个。
* **起进程同一个原语**：`execspec.desktop_native_spec` + `desktopproc.start`（取租约 → spawn → 握手 → 单 reader），
  会话是 `nativesession.NativeSession`。桌面那一支的准备会话、项目代次、活路由绑定、SSE 都属于 sidecar，这里不需要。
* **没人作答**：MCP 没有界面能回答脚本的 `input()`，stdin 是 `null`（读到 EOF）。

桥只经本模块（`_optional_engine("nativefallback")`）碰 native：旧引擎没有它 = 不回退，如实报 blocked。
`NativeWorker` 把 `NativeSession` 的 `RunError` 翻成 `pool.WorkerError`，让桥现有的错误投影原样可用。
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
from pathlib import Path

from . import (
    desktopproc,
    discover,
    execpolicy,
    execspec,
    figcapture,
    launchscript,
    nativesession,
    pathignore,
    pool,
    runcodes,
    workdir,
)
from .runcodes import RunError

#: 回包里说「这次怎么跑的」的闭集值
EXECUTION_NATIVE = "native"
#: 回退的原因（目前唯一一种）
REASON_BLOCKED = pool.FILE_OPERATION_BLOCKED_CODE
#: 等脚本跑到屏障的上限（秒）。重图（注册表 `cost == "heavy"`）给得长一些；超时 = 收掉进程、如实报超时。
BARRIER_TIMEOUT_S = 300.0
BARRIER_TIMEOUT_HEAVY_S = 1800.0
#: 超时的稳定码（与 bridge 的 BridgeError.code 同一个命名空间）
CODE_TIMEOUT = "native_fallback_timeout"
#: 回退起来了，可脚本没画出这张图（native 下 stem 不在 build 结果里）
CODE_STEM_MISSING = "native_fallback_stem_missing"

#: 给 agent 的一句话（如实：脚本是真跑的、写文件没有范围限制）
NOTE = (
    "安全模式下这个脚本读回了自己刚保存的文件（savefig 之后又读那个路径），"
    "Tavotto 已改用真实的 Python 再运行一次：脚本写的文件会真的写到磁盘上，不受授权目录限制。"
)


@dataclasses.dataclass(frozen=True)
class Verdict:
    """要不要回退：`ok` 为真才起进程；否则 `reason` 是 `execpolicy.REASONS` 里的闭集码。"""

    ok: bool
    reason: str


def _native_entry(project: str, script: str, entry: str | None) -> tuple[str, str | None]:
    """与桌面 `preparation.native_entry_for` 同一份判据（`discover.native_entry_of`）；算不出按原样跑整份模块。"""
    try:
        path = Path(project) / figcapture.normalize_relative_script(script)
        return discover.native_entry_of(path, entry)
    except Exception:  # noqa: BLE001 — 判不出就不改（原行为）
        return discover.NATIVE_RUN_MODULE, None


def decide(
    project: str,
    cause_code: str,
    *,
    python: str,
    script: str | None = None,
    entry: str | None = None,
) -> Verdict:
    """MCP 这一次打开（一次运行）里，safe 以 `cause_code` 收场后能不能换 native。纯判定，不起进程。

    `script` / `entry`（ADR 0127 修订 2026-11-18）：safe 当时调的入口。原样运行够不到它（`unreachable`）就不回退，
    与桌面同一个理由码 `plan_unavailable`。"""
    if not python:
        return Verdict(False, execpolicy.REASON_PLAN_UNAVAILABLE)
    if (
        script
        and not launchscript.source_for(project, script)
        and _native_entry(project, script, entry)[0] == discover.NATIVE_ENTRY_UNREACHABLE
    ):
        return Verdict(False, execpolicy.REASON_PLAN_UNAVAILABLE)
    ledger = execpolicy.AttemptLedger()
    ledger.begin_run()
    interp = execpolicy.digest(python)
    ledger.record("safe", execpolicy.SpecKey(execpolicy.PROFILE_SAFE, interp, ""))
    ledger.close("safe", execpolicy.OUTCOME_BLOCKED, cause_code)
    d = execpolicy.decide_fallback(
        mode=execpolicy.mode_for(project),
        enabled=execpolicy.fallback_enabled(),
        ledger=ledger,
        cause_code=cause_code,
        nxt=execpolicy.SpecKey(execpolicy.PROFILE_NATIVE, interp, ""),
        mcp_initiated=True,
    )
    return Verdict(bool(d.ok), str(d.reason or ""))


def _as_worker_error(exc: RunError) -> pool.WorkerError:
    err = pool.WorkerError(str(exc), code=exc.code)
    return err


class NativeWorker:
    """`NativeSession` 的 Worker-like 外壳：方法逐个转发，`RunError` 一律翻成 `pool.WorkerError`。"""

    execution = EXECUTION_NATIVE

    def __init__(self, session: nativesession.NativeSession, *, python: str):
        self.session = session
        self.python = python

    def __getattr__(self, name):
        target = getattr(self.session, name)
        if not callable(target):
            return target

        # 保留原签名：桥按 `inspect.signature(worker.export)` 判能不能一次导出 PDF+SVG
        @functools.wraps(target)
        def call(*args, **kwargs):
            try:
                return target(*args, **kwargs)
            except RunError as exc:
                raise _as_worker_error(exc) from exc

        return call

    @property
    def rev(self) -> int:
        return self.session.rev

    def alive(self) -> bool:
        try:
            return bool(self.session.alive()) and self.session.state == nativesession.BARRIER
        except Exception:  # noqa: BLE001 — 问不出来就当已经不在
            return False

    def end(self) -> None:
        end(self.session)


def end(session) -> None:
    """让一条停在屏障上的 native 会话结束：先放行（脚本跑完、恢复原样），放不了就真终止（与桌面 `_end_native_session` 同序）。"""
    try:
        session.resume()
        owned = getattr(session, "owned", None)
        if owned is not None and owned.wait(15):
            return
    except Exception:  # noqa: BLE001 — 收尾路径：放行失败就走终止
        pass
    with contextlib.suppress(Exception):
        session.cancel_owned()


def _script_error(session) -> pool.WorkerError:
    error = dict(session.script_error or {})
    failure = pool.WorkerError(
        str(error.get("message") or "脚本运行出错"),
        traceback_text=str(error.get("traceback") or ""),
        code=pool.SCRIPT_ERROR_CODE,
    )
    category = getattr(session, "exception_category", None)
    if category:
        failure.extra["exception_category"] = category
    return failure


def _ended_error(session) -> pool.WorkerError:
    err = session.terminal_error or {}
    if err:
        return pool.WorkerError(
            str(err.get("detail") or "运行没有正常结束"),
            code=str(err.get("code") or runcodes.BRIDGE_CHILD_EXITED),
        )
    owned = getattr(session, "owned", None)
    tail = owned.stderr.text() if owned is not None else ""
    return pool.WorkerError(
        f"脚本退出（退出码 {session.exit_code}），没有留下可编辑的图",
        traceback_text=tail,
        code=pool.SCRIPT_ERROR_CODE if session.exit_code else "no_figure_captured",
    )


def _stem_missing(resp: dict, stem: str) -> pool.WorkerError:
    """这张图不在 build 结果里：脚本截下了却没能出图（`figure_facts` 里有失败）就把那条真实原因交出去，
    别吞成「没画出」——agent 照「没画出」去改脚本是白改。"""
    for fact in resp.get("figure_facts") or []:
        failure = fact.get("failure") if isinstance(fact, dict) else None
        if fact.get("stem") == stem and isinstance(failure, dict) and failure.get("message"):
            err = pool.WorkerError(
                f"真实 Python 运行完了，可 {stem} 没能出图：{failure['message']}",
                code=CODE_STEM_MISSING,
            )
            err.extra["capture_failure"] = {
                "code": str(failure.get("code") or ""),
                "message": str(failure["message"])[:500],
            }
            return err
    return pool.WorkerError(f"真实 Python 运行完了，却没有画出 {stem}", code=CODE_STEM_MISSING)


def launch(
    project: str,
    script: str,
    stem: str,
    *,
    python: str,
    run=None,
    heavy: bool = False,
    start=None,
    entry: str | None = None,
) -> NativeWorker:
    """起一个真实 Python 跑 `script`，等它停在屏障上并 build 一次；返回 Worker-like 的 `NativeWorker`。

    失败一律收掉进程再抛 `pool.WorkerError`（码照实：脚本异常 = `script_error`、超时 = `native_fallback_timeout`、
    这张图不在 = `native_fallback_stem_missing`、环境正被安装改动 = `environment_mutating`…）。`start` 只给测试换替身。"""
    root = str(Path(project))
    target = str(Path(root) / figcapture.normalize_relative_script(script))
    spec = execspec.desktop_native_spec(
        target,
        interpreter=python,
        cwd=execspec.desktop_cwd(root, script, workdir.mode_for(root)),
        project_root=root,
        stdin=execspec.STDIN_NULL,
        env={"PYTHONUNBUFFERED": "1"},
        ignored_paths=pathignore.paths_for(root),
        # 启动脚本（ADR 0194）优先：执行它的字节、身份仍是这个脚本；否则库式模块跑完模块再调函数入口（ADR 0127 修订 2026-11-18）
        script_source=launchscript.source_for(root, script),
        entry=None
        if launchscript.source_for(root, script)
        else _native_entry(root, script, entry)[1],
        **execspec.run_kwargs(run),
    )
    try:
        session = (start or desktopproc.start)(desktopproc.OwnedLaunch(spec))
    except RunError as exc:
        raise _as_worker_error(exc) from exc
    try:
        timeout = BARRIER_TIMEOUT_HEAVY_S if heavy else BARRIER_TIMEOUT_S
        state = session.wait_for_state((nativesession.BARRIER,), timeout)
        if state != nativesession.BARRIER:
            if state in nativesession.TERMINAL_STATES:
                raise _ended_error(session)
            raise pool.WorkerError(
                f"脚本在真实 Python 里运行超过 {int(timeout)} 秒还没画完", code=CODE_TIMEOUT
            )
        if session.script_error:
            raise _script_error(session)
        try:
            resp = session.ensure_built()
        except RunError as exc:
            raise _as_worker_error(exc) from exc
        if stem not in (resp.get("stems") or {}):
            raise _stem_missing(resp, stem)
    except BaseException:
        end(session)
        raise
    return NativeWorker(session, python=python)
