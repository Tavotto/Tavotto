"""数据出图 v2 大表的**真实进度与取消**（ADR 0178 修订·大表·进度）。

长请求（概览、出备选、缩略图、提交）在大表上要读几十秒；这里给前端一个轻量的旁路：请求带一个请求号，后端在读表循环里按「已读字节 / 总字节」
记进度，前端用同一个请求号轮询（`GET /api/ai/data-progress/<请求号>`）、取消（`POST …/cancel`）。纯标准库、进程内、不落盘、不发遥测。

* **只有大表才有条目**：条目在请求开始时登记（取消要能在读表开始前就到），但请求结束时若从没进过任何大表阶段就删掉；查询时这样的条目
  也读作 `none`。小表的请求没有任何可见变化。
* **有界**：至多 `MAX_ENTRIES` 条、终态条目留 `TTL_S` 秒；条目里只有阶段名与数字（字节、比例、秒），没有路径 / 列名 / 文字。
* **取消 = 协作式**：读表循环定期 `tick()`，发现取消标志就抛 `Cancelled`（`BaseException` 子类：不会被各处 `except Exception` 吞掉，与
  `asyncio.CancelledError` 同一约定）；清理走调用方原有的 `except BaseException` 路径（候选目录删掉、缓存只在成功后才写）。取消端点幂等：
  重复取消、取消已完成 / 不认识 / 别的项目的请求都是 200，`cancelled` 只在「这次真的把一个进行中的请求标成取消」时为真。取消与完成赛跑时
  **以原请求的响应为准**（读完最后一个检查点之后才到的取消不生效，请求照常完成）。缩略图与提交不可取消（提交一旦开始就会落盘）。
* 阶段：`validate`（校验：sha256 / 编码）、`scan`（解析统计）、`stage`（复制候选副本）、`reduce`（缩减）、`render`（worker 里读 + 缩减 + 画图，
  **没有字节进度**：worker 是另一个进程，脚本里不带回调——如实标 `indeterminate`）。`fraction` 单调不减，按每种请求的阶段权重折算。
"""

from __future__ import annotations

import contextvars
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field

MAX_ENTRIES = 32
TTL_S = 10 * 60
#: 请求号：与 `app._CLIENT_REQUEST_ID` 同一口径（字母数字与 `_-`，8–64 位）
RID = re.compile(r"[A-Za-z0-9_-]{8,64}")

STATES = ("none", "running", "done", "cancelled", "failed")
KINDS = ("overview", "propose", "thumbnail", "commit")
#: 哪些请求可取消：读表类可以；缩略图在别的进程里画、提交一旦开始就落盘，不可取消
CANCELLABLE = frozenset({"overview", "propose"})
#: 每种请求的阶段顺序与权重（和为 1；`render` 没有权重：进入它时 `fraction` 为空、`indeterminate` 为真）
_BANDS: dict[str, tuple[tuple[str, float], ...]] = {
    "overview": (("validate", 0.15), ("scan", 0.85)),
    "propose": (("validate", 0.10), ("scan", 0.35), ("stage", 0.10), ("reduce", 0.45)),
    "thumbnail": (("render", 0.0),),
    "commit": (("validate", 0.05), ("scan", 0.20), ("reduce", 0.10), ("render", 0.0)),
}


class Cancelled(BaseException):
    """请求被前端取消（协作式：在读表循环的检查点抛出）。"""


@dataclass
class Entry:
    project: str
    rid: str
    kind: str
    started: float
    updated: float
    state: str = "running"
    phase: str | None = None
    done: int = 0
    total: int = 0
    #: 本阶段第几遍（缩减可能为几个候选各读一遍）与预计总遍数
    pass_no: int = 0
    passes: int = 1
    fraction: float = 0.0
    large: bool = False
    indeterminate: bool = False
    cancel: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)


_LOCK = threading.RLock()
_ENTRIES: OrderedDict[tuple[str, str], Entry] = OrderedDict()
_CUR: contextvars.ContextVar[Entry | None] = contextvars.ContextVar(
    "ai_data_progress", default=None
)


def _sweep(now: float) -> None:
    for key in [k for k, e in _ENTRIES.items() if e.state != "running" and now - e.updated > TTL_S]:
        _ENTRIES.pop(key, None)
    while len(_ENTRIES) >= MAX_ENTRIES:
        # 先丢最老的终态条目；全是进行中的才丢最老的进行中条目（它的请求照常跑完，只是没了进度）
        victim = next(
            (k for k, e in _ENTRIES.items() if e.state != "running"), next(iter(_ENTRIES))
        )
        _ENTRIES.pop(victim, None)


def _start(project: str, rid: str, kind: str) -> Entry:
    now = time.time()
    with _LOCK:
        _sweep(now)
        e = Entry(project, rid, kind, now, now)
        prev = _ENTRIES.get((project, rid))
        if prev is not None and prev.state == "running":
            # 同一请求号在上一次还没跑完时又来了一次（重试）：两次共用一个取消标志，取消端点一次停掉两边——
            # 否则登记表里只剩新的那条，旧请求会在取消之后继续读表、建候选。
            e.cancel = prev.cancel
        _ENTRIES[(project, rid)] = e
        return e


def _finish(e: Entry, state: str) -> None:
    with _LOCK:
        e.updated = time.time()
        if not e.large:
            if _ENTRIES.get((e.project, e.rid)) is e:
                _ENTRIES.pop((e.project, e.rid), None)  # 小表：不留任何条目
            e.state = state
            return
        e.state = state
        if state == "done":
            e.fraction, e.indeterminate = 1.0, False
            if e.total:
                e.done = e.total


@contextmanager
def track(project: str, rid: str | None, kind: str):
    """登记一个请求的进度条目并让这条线程里的读表代码可见（`tick` / `phase` 经上下文变量找到它）。`rid` 为 `None` = 不跟踪。
    正常结束 = `done`，`Cancelled` = `cancelled`，其它异常 = `failed`（异常原样往外抛）。"""
    if rid is None:
        yield None
        return
    if kind not in KINDS or not RID.fullmatch(rid):
        raise ValueError("progress_input_invalid")
    e = _start(project, rid, kind)
    token = _CUR.set(e)
    try:
        yield e
    except Cancelled:
        _finish(e, "cancelled")
        raise
    except BaseException:
        _finish(e, "failed")
        raise
    else:
        _finish(e, "done")
    finally:
        _CUR.reset(token)


def active() -> bool:
    return _CUR.get() is not None


def _fraction(e: Entry, inner: float) -> float | None:
    band = _BANDS.get(e.kind, ())
    base = 0.0
    for name, w in band:
        if name == e.phase:
            if w == 0.0:
                return None
            return min(0.999, base + w * min(1.0, max(0.0, inner)))
        base += w
    return None


def _apply(e: Entry, phase: str, done: int, total: int) -> None:
    if e.phase != phase:
        e.phase, e.pass_no, e.passes = phase, 1, 1
    elif e.pass_no == 0:
        e.pass_no = 1
    elif done < e.done:
        e.pass_no += 1  # 同一阶段又从头读了一遍（缩减：几个候选各一遍）
    e.done, e.total, e.large = done, total, True
    e.indeterminate = False
    inner = (done / total) if total else 0.0
    f = _fraction(e, (e.pass_no - 1 + inner) / max(1, e.passes))
    if f is None:
        e.indeterminate = phase == "render"
    else:
        e.fraction = max(e.fraction, f)  # 单调不减
    e.updated = time.time()


def tick(phase: str, done: int, total: int) -> None:
    """读表循环里的检查点：记 `phase` 的已读 / 总字节，并在请求被取消时抛 `Cancelled`。没有跟踪的请求里是空操作。"""
    e = _CUR.get()
    if e is None:
        return
    with _LOCK:
        _apply(e, phase, done, total)
        if e.cancel.is_set():
            raise Cancelled


def check() -> None:
    """只查取消标志（耗时步骤之前的检查点）。"""
    e = _CUR.get()
    if e is not None and e.cancel.is_set():
        raise Cancelled


def set_passes(phase: str, passes: int) -> None:
    """告诉进度「这个阶段要读几遍」（出备选里缩减要读几遍由候选的画法数定）；`fraction` 按遍数摊。"""
    e = _CUR.get()
    if e is None:
        return
    with _LOCK:
        e.passes = max(1, passes)
        if e.phase != phase:
            e.phase, e.pass_no, e.done, e.total = phase, 0, 0, 0


def enter(phase: str) -> None:
    """进入一个没有字节进度的阶段（`render`）：只记阶段名与「不确定」。"""
    e = _CUR.get()
    if e is None:
        return
    with _LOCK:
        if e.phase != phase:
            e.phase, e.pass_no, e.passes, e.done, e.total = phase, 1, 1, 0, 0
        e.large = e.large or phase == "render"
        e.indeterminate = _fraction(e, 0.0) is None
        e.updated = time.time()


def snapshot(project: str, rid: str) -> dict:
    """前端轮询看到的那份。没有条目 / 小表请求 / 别的项目 = `{"state": "none"}`。"""
    now = time.time()
    with _LOCK:
        e = _ENTRIES.get((project, rid))
        if e is None or not e.large:
            return {"state": "none"}
        return {
            "state": e.state,
            "kind": e.kind,
            "phase": e.phase,
            "phase_done": e.done,
            "phase_total": e.total,
            "pass": e.pass_no,
            "passes": e.passes,
            "fraction": None if e.indeterminate else round(e.fraction, 4),
            "indeterminate": e.indeterminate,
            "cancellable": e.kind in CANCELLABLE and e.state == "running",
            "elapsed_s": round((e.updated if e.state != "running" else now) - e.started, 1),
        }


def cancel(project: str, rid: str) -> dict:
    """取消。幂等：任何情形都回一份 `snapshot` 加 `cancelled`（这次是否真的把一个进行中的、可取消的请求标成了取消）。"""
    with _LOCK:
        e = _ENTRIES.get((project, rid))
        newly = False
        if (
            e is not None
            and e.state == "running"
            and e.kind in CANCELLABLE
            and not e.cancel.is_set()
        ):
            e.cancel.set()
            newly = True
    return {**snapshot(project, rid), "cancelled": newly}


def reset_for_tests() -> None:
    with _LOCK:
        _ENTRIES.clear()
