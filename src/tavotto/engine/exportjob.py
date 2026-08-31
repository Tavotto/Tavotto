"""导出作业 —— 一次导出的生命周期只有这一份实现。

`exportreq` 回答「要什么」，这个模块回答「怎么把它变成磁盘上的文件」：

```text
prepare(spec)     → ExportJob（请求已规范化、已校验，还没碰磁盘）
validate(job)     → 这次导出会撞上什么（重名、目录写不了、格式/PPI 不搭）
run(job, produce) → 真的出文件：临时目录 → 逐格式产出 → 原子放到最终位置
cancel(job_id)    → 取消并清干净临时文件
progress(job_id)  → 断线之后补拉当前状态
```

### 三条不肯让步的性质

**1. 原子。** 渲染后端把字节写进 `<export_dir>/.tavotto-export-<job>/` 里的
临时文件，全部产出完成之后才 `os.replace` 到最终名字上。导出中途断电/被杀/
磁盘满，导出目录里**不会出现半个 PDF**——用户点开的每一个文件都是完整的。
临时目录与最终目录同一个文件系统，所以 replace 是原子的。

**2. 部分失败可见。** 一次请求要 PDF+PNG，PNG 挂了，PDF 照常交付，
`status` 是 `partial` 而不是 `done`，`outputs[]` 里那一项带自己的
`error.code`。**不许把部分成功报成全部成功**，也不许因为一项失败就把另一项
已经渲染好的成果扔掉。

**3. 取消不留垃圾。** `cancel()` 置事件位；`produce` 在每个对象、每个格式之间
问一次。取消时整个临时目录被删掉，最终目录一个字节没动过。

### 快照

`document_revision` 是**客户端在导出开始那一刻**取的文档修订号，原样存进作业、
原样回给客户端。服务端不去猜文档变没变——它看不见前端的编辑；客户端拿回执里
的这个值与当前值一比，就能说出「导出期间这份文档又被改过」。多格式共享同一个
作业 = 共享同一份对象快照，所以 PDF 与 PNG 必然出自同一个语义状态。
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import atomicio, exportreq
from .exportreq import ExportRequest, ExportRequestError

#: 作业状态。`partial` 是独立一档 —— 把它并进 `done` 或 `failed` 都会说谎。
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_CONFLICT = "conflict"

#: 本模块会发出的**全部**错误码（同 `exportreq.ERROR_CODES` 的理由：
#: `job.error_code` 是赋值不是字面量响应，正则扫不到）。
#: `app.py` 的导出路径另外会发 `export_render_failed` 与 `source_missing`。
ERROR_CODES = (
    "export_dir_unwritable",
    "tmp_dir_failed",
    "export_failed",
    "format_failed",
    "no_output",
    "publish_failed",
    "report_failed",
    "report_write_failed",
)

#: 作业保留多久（秒）。界面拿 job_id 补拉状态要在这个窗口内。
_TTL_S = 15 * 60

#: 临时目录前缀。以点开头 = 大多数文件管理器默认不显示，也不会被用户当成成果。
TMP_PREFIX = ".tavotto-export-"


@dataclass
class Output:
    """一个格式的产出。**成功与失败是同一个结构**——失败项也要出现在清单里。"""

    format: str
    name: str | None = None
    url: str | None = None
    bytes: int | None = None
    width_px: int | None = None
    height_px: int | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    #: 这一份是不是真矢量。**不许对 PNG 报 True**（共享规则 §8）
    vector: bool = False
    status: str = STATUS_DONE
    error_code: str | None = None
    error_params: dict = field(default_factory=dict)
    #: `replace` 策略下真的盖掉了一个已有文件
    replaced: bool = False

    def to_payload(self) -> dict:
        return {
            "format": self.format,
            "name": self.name,
            "url": self.url,
            "bytes": self.bytes,
            "dimensions": {
                "px": [self.width_px, self.height_px] if self.width_px and self.height_px else None,
                "mm": [self.width_mm, self.height_mm] if self.width_mm and self.height_mm else None,
            },
            "vector": self.vector,
            "status": self.status,
            "replaced": self.replaced,
            "error": (
                {"code": self.error_code, "params": self.error_params} if self.error_code else None
            ),
        }


@dataclass
class Produced:
    """`produce()` 交回来的一件东西：一个临时文件，或者一次失败。"""

    format: str
    tmp_path: Path | None = None
    width_px: int | None = None
    height_px: int | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    vector: bool = False
    error_code: str | None = None
    error_params: dict = field(default_factory=dict)


class Cancelled(Exception):
    """`produce()` 发现取消位被置上时抛它。不是错误，是用户的决定。"""


@dataclass
class ExportJob:
    id: str
    request: ExportRequest
    export_dir: Path
    status: str = STATUS_PENDING
    outputs: list[Output] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_params: dict = field(default_factory=dict)
    #: 结构化错误可不可重试。`False` 的典型是"格式不支持"，重试一百次也一样
    error_recoverable: bool = True
    started_at: float = 0.0
    finished_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    phase: str = "queued"
    step: int = 0
    total: int = 1
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _tmp_dir: Path | None = field(default=None, repr=False)
    #: 已经开始往最终目录落盘。**过了这个点取消不再被接受**——第一个
    #: `os.replace` 之后就没有"一个字节没动过"可言了。
    _committed: bool = field(default=False, repr=False)
    #: 冲突时告诉界面是哪几个名字撞了（`overwrite=ask`）
    conflicts: list[str] = field(default_factory=list)
    validation_summary: dict = field(default_factory=dict)
    report: Output | None = None

    # -- 取消 ---------------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def to_payload(self) -> dict:
        outputs = [o.to_payload() for o in self.outputs]
        if self.report is not None:
            outputs.append(self.report.to_payload())
        return {
            "job_id": self.id,
            "status": self.status,
            "request": self.request.to_payload(),
            "outputs": outputs,
            "warnings": list(self.warnings),
            "validation_summary": dict(self.validation_summary),
            "conflicts": list(self.conflicts),
            "export_dir": str(self.export_dir),
            "document_revision": self.request.document_revision,
            "progress": {"phase": self.phase, "step": self.step, "total": self.total},
            "timing": {
                "started_at": self.started_at or None,
                "finished_at": self.finished_at or None,
                "elapsed_ms": (
                    round((self.finished_at - self.started_at) * 1000)
                    if self.started_at and self.finished_at
                    else None
                ),
            },
            "error": (
                {
                    "code": self.error_code,
                    "params": self.error_params,
                    "recoverable": self.error_recoverable,
                }
                if self.error_code
                else None
            ),
        }


# ------------------------------ 作业登记表 -----------------------------------

_JOBS: dict[str, ExportJob] = {}
_LOCK = threading.Lock()


#: 已经走完的状态。TTL 只清这些——**还在跑的作业不许被清**。
_TERMINAL_STATUSES = frozenset(
    {STATUS_DONE, STATUS_PARTIAL, STATUS_FAILED, STATUS_CANCELLED, STATUS_CONFLICT}
)


def _sweep() -> None:
    """过期作业清出去。**顺便清掉它可能留下的临时目录**——进程被 kill 时
    `run()` 的 finally 跑不到，那些目录会一直躺在用户的导出目录里。

    **只清进过终局的**。第一版按 `max(created_at, finished_at)` 判，而在跑的
    作业 `finished_at` 是 0——一次跑超过 15 分钟的导出会在下一次 `prepare()`
    时被当成过期：临时目录被删、作业从表里消失，于是发起它的客户端拿到
    `unknown`，而它的生产者还在往一个已经不存在的目录里写（PR #214 复审）。
    """
    now = time.time()
    for jid, job in list(_JOBS.items()):
        if job.status not in _TERMINAL_STATUSES:
            continue
        if now - max(job.created_at, job.finished_at) > _TTL_S:
            _drop_tmp(job)
            _JOBS.pop(jid, None)


def get(job_id: str) -> ExportJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def progress(job_id: str) -> dict:
    job = get(job_id)
    if job is None:
        return {"job_id": job_id, "status": "unknown"}
    return job.to_payload()


def cancel(job_id: str) -> bool:
    """请求取消。回「有没有这个作业可取消」——**不是「已经取消了」**：
    真正的清理发生在执行线程回到检查点的那一刻。

    **落盘一开始就没得取消了。** 第一个 `os.replace` 之后，上一版文件的内容
    已经不在了，"最终目录一个字节没动过"这句承诺兑现不了；而回一个
    `cancelling: true` 然后让作业照常报 `done`，是**说了一句做不到的话**
    （PR #214 复审）。所以有一个明确的提交点：`_committed` 置上之后
    `cancel()` 如实回 `False`。
    """
    job = get(job_id)
    if job is None or job.status in _TERMINAL_STATUSES or job._committed:
        return False
    job._cancel.set()
    return True


def sweep_stale_tmp_dirs(export_dir: Path) -> int:
    """删掉导出目录里遗留的临时目录（上一次进程被 kill 留下的）。回删掉几个。

    只删**本模块的前缀**，且只删目录：用户自己在导出目录里放的东西一个不碰。
    """
    removed = 0
    try:
        entries = list(Path(export_dir).iterdir())
    except OSError:
        return 0
    live = set()
    with _LOCK:
        live = {str(j._tmp_dir) for j in _JOBS.values() if j._tmp_dir}
    for p in entries:
        if p.is_dir() and p.name.startswith(TMP_PREFIX) and str(p) not in live:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    return removed


# -------------------------------- 生命周期 -----------------------------------


def prepare(spec: dict, export_dir: Path) -> ExportJob:
    """规范化 + 登记。**不碰磁盘、不出文件**。

    校验失败抛 `ExportRequestError`——请求不合法这件事必须在建作业之前说清楚，
    否则界面会拿到一个 job_id 然后立刻收到它失败了，用户看到的是"导出坏了"
    而不是"这个文件名不能用"。
    """
    request = exportreq.normalize(spec)
    with _LOCK:
        _sweep()
        job = ExportJob(id=uuid.uuid4().hex[:16], request=request, export_dir=Path(export_dir))
        _JOBS[job.id] = job
    return job


def validate(job: ExportJob) -> dict:
    """这次导出**在真的开始之前**能看出来的问题。

    只回事实，不做决定：撞了哪几个名字、目录写不写得了、PPI 在这次格式组合下
    有没有意义。"错误要不要拦住导出"是 Spec 的规则说了算（ADR 0029/0030），
    不在这里现判。
    """
    req = job.request
    existing = [
        exportreq.output_name(req.filename, f)
        for f in req.formats
        if (job.export_dir / exportreq.output_name(req.filename, f)).exists()
    ]
    writable = True
    try:
        job.export_dir.mkdir(parents=True, exist_ok=True)
        probe = job.export_dir / f"{TMP_PREFIX}probe"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        writable = False
    return {
        "conflicts": [] if req.legacy_naming else existing,
        "writable": writable,
        "ppi_applies": req.has_raster,
        "names": {f: exportreq.output_name(req.filename, f) for f in req.formats},
    }


def _drop_tmp(job: ExportJob) -> None:
    if job._tmp_dir is not None:
        shutil.rmtree(job._tmp_dir, ignore_errors=True)
        job._tmp_dir = None


#: 样式检查报告在命名与覆盖策略里的「格式」名。它不是一个输出格式，但**它
#: 是一个会被写到最终目录里的文件**——不把它当成计划中的产物，`ask` 会静默
#: 盖掉上一次的报告，`rename` 会给图编号却仍然覆盖报告（PR #214 评审）。
REPORT_KEY = "report"
REPORT_SUFFIX = "_style-check.json"
LEGACY_REPORT_SUFFIX = "_proof.json"

#: 已经被某个在跑的作业**预定**的最终路径 → 作业 id。
#:
#: 只查磁盘不够：两个标签页同时导出同一个新文件名时，两边都能通过存在性检查
#: （那一刻磁盘上确实没有），渲染半分钟之后**后完成的那个静默盖掉先完成的**
#: ——而两边的用户都看到了"导出成功"。预留表把这个窗口关上：名字在决定的
#: 那一刻就被占住，第二个作业当场报 conflict（PR #214 评审）。
#:
#: 它只保**本进程**。别的进程/别的程序在渲染途中创建同名文件，我们仍然会
#: 覆盖它——那超出我们能许诺的范围，所以不假装能挡。
_RESERVED: dict[str, str] = {}


def _plan_names(job: ExportJob) -> dict[str, str]:
    """这次作业会写出哪几个最终文件名。**覆盖策略只在这里落地。**

    在**渲染之前**一次决定完（含样式检查报告），理由有两个：先问再动手，
    别渲染半分钟再告诉用户"这个名字已经有了"；以及名字一旦决定就能立刻
    预留，把并发窗口关掉。
    """
    req = job.request
    keys = list(req.formats) + ([REPORT_KEY] if req.include_report else [])
    if req.legacy_naming:
        # 旧契约：`<stem>_<MMDD_HHMMSS>.<ext>`。时间戳让它天生撞不了车，
        # 所以老标签页与 CI 脚本从来不需要覆盖策略，行为一个字节不变。
        ts = time.strftime("%m%d_%H%M%S")
        return {
            k: (
                f"{req.filename}_{ts}{LEGACY_REPORT_SUFFIX}"
                if k == REPORT_KEY
                else f"{req.filename}_{ts}.{k}"
            )
            for k in keys
        }

    batch: set[str] = set()

    def taken(name: str) -> bool:
        if name in batch:
            return True
        if str(job.export_dir / name) in _RESERVED:
            return True
        return (job.export_dir / name).exists()

    def plain(key: str) -> str:
        return (
            f"{req.filename}{REPORT_SUFFIX}"
            if key == REPORT_KEY
            else exportreq.output_name(req.filename, key)
        )

    names: dict[str, str] = {}
    for key in keys:
        if req.overwrite == exportreq.OVERWRITE_RENAME:
            name = (
                _dedupe_report(req.filename, taken)
                if key == REPORT_KEY
                else exportreq.dedupe_name(req.filename, key, taken)
            )
        else:
            name = plain(key)
        batch.add(name)
        names[key] = name
    return names


def _dedupe_report(base: str, taken) -> str:
    """报告的去重名。编号规则与 `exportreq.dedupe_name()` 逐字相同
    （`Fig 1 (2)_style-check.json`），只是后缀不是一个格式扩展名。"""
    first = f"{base}{REPORT_SUFFIX}"
    if not taken(first):
        return first
    n = 2
    while n <= 9999:
        candidate = f"{base} ({n}){REPORT_SUFFIX}"
        if not taken(candidate):
            return candidate
        n += 1
    raise exportreq.ExportRequestError("name_exhausted", "无法为报告找到可用的编号", {})


def _claim(job: ExportJob, names: dict[str, str], *, check: bool) -> tuple[list[str], list[str]]:
    """**一次持锁**完成「撞没撞上」与「占住」两件事。回 `(撞上的名字, 已占住的键)`。

    分成两次持锁就等于中间还留着一个窗口：A 查完发现没撞，还没占住，B 也查完
    发现没撞——两个作业都往下走了。合并成一次之后，两者之间不存在"查过但还
    没占"的时刻。

    `check=False`（`replace` / `rename` / 旧契约）时不报撞名，但**照样预留**：
    预留在这两条路上防的是另一件事——两个 `replace` 同时指向同一个路径，
    后完成的那个会盖掉先完成的，而两边的用户都看到了"导出成功"。
    """
    collisions: list[str] = []
    mine: list[str] = []
    with _LOCK:
        if check:
            for name in names.values():
                path = job.export_dir / name
                reserved_by = _RESERVED.get(str(path))
                if path.exists() or (reserved_by is not None and reserved_by != job.id):
                    collisions.append(name)
            if collisions:
                return collisions, []
        else:
            for name in names.values():
                reserved_by = _RESERVED.get(str(job.export_dir / name))
                if reserved_by is not None and reserved_by != job.id:
                    collisions.append(name)
            if collisions:
                return collisions, []
        for name in names.values():
            key = str(job.export_dir / name)
            _RESERVED[key] = job.id
            mine.append(key)
    return [], mine


def _release(keys: list[str], job_id: str) -> None:
    with _LOCK:
        for key in keys:
            if _RESERVED.get(key) == job_id:
                _RESERVED.pop(key, None)


def run(
    job: ExportJob,
    produce: Callable[[ExportJob, Path], list[Produced]],
    *,
    publish: Callable[[dict], None] | None = None,
    report: Callable[[ExportJob, list[Output]], bytes | None] | None = None,
) -> dict:
    """执行一个作业。同步；`run_async` 是它的线程包装。

    `produce(job, tmp_dir)` 由调用方给（合成与渲染的知识留在 `app.py`，这个
    模块不认识 PyMuPDF、也不认识 worker）。它必须：把每个格式写进 `tmp_dir`
    里的一个文件，返回 `Produced` 列表；在每个可中断的点上调
    `job.check_cancelled()`。

    `report(job, outputs)` 生成样式检查报告的字节（可选）。**报告失败不牵连
    成图**——那是 §七 明写的：报告生成失败不应默认让图文件全部失败，但必须
    清楚说明部分失败。
    """
    req = job.request
    job.status = STATUS_RUNNING
    job.started_at = time.time()
    job.total = len(req.formats) + (1 if req.include_report else 0)
    job.step = 0
    job.phase = "preparing"
    _emit(job, publish)

    try:
        job.export_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail(job, "export_dir_unwritable", {"error": str(exc)}, publish, recoverable=True)

    # 名字**在渲染之前**一次决定完（含样式检查报告），然后立刻预留。
    # `ask` 撞名就什么都不做地回来——先问再动手，别渲染半分钟再告诉用户
    # "这个名字已经有了"；预留则关掉「两个作业同时通过存在性检查」那个窗口。
    try:
        names = _plan_names(job)
    except exportreq.ExportRequestError as exc:
        return _fail(job, exc.code, exc.params, publish, recoverable=True)
    conflicts, reserved = _claim(
        job,
        names,
        check=req.overwrite == exportreq.OVERWRITE_ASK and not req.legacy_naming,
    )
    if conflicts:
        job.conflicts = conflicts
        job.status = STATUS_CONFLICT
        job.finished_at = time.time()
        job.phase = "conflict"
        _emit(job, publish)
        return job.to_payload()

    tmp_dir = job.export_dir / f"{TMP_PREFIX}{job.id}"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        _release(reserved, job.id)
        return _fail(job, "tmp_dir_failed", {"error": str(exc)}, publish, recoverable=True)
    job._tmp_dir = tmp_dir

    try:
        job.phase = "rendering"
        _emit(job, publish)
        produced = produce(job, tmp_dir)
        job.check_cancelled()

        # 落盘之前**最后一次**问取消；过了这一行就是提交点，后面不再问
        job.check_cancelled()
        job.phase = "writing"
        job._committed = True
        _emit(job, publish)
        outputs: list[Output] = []
        for p in produced:
            if p.error_code is not None or p.tmp_path is None:
                outputs.append(
                    Output(
                        format=p.format,
                        status=STATUS_FAILED,
                        error_code=p.error_code or "no_output",
                        error_params=p.error_params,
                    )
                )
                continue
            name = names[p.format]
            dest = job.export_dir / name
            replaced = dest.exists()
            try:
                size = p.tmp_path.stat().st_size
                atomicio.publish_file(p.tmp_path, dest)
            except (OSError, atomicio.AtomicWriteError) as exc:
                code = getattr(exc, "code", "publish_failed")
                outputs.append(
                    Output(
                        format=p.format,
                        status=STATUS_FAILED,
                        error_code=code,
                        error_params={"error": str(exc)},
                    )
                )
                continue
            outputs.append(
                Output(
                    format=p.format,
                    name=name,
                    url=f"/exports/{name}",
                    bytes=size,
                    width_px=p.width_px,
                    height_px=p.height_px,
                    width_mm=p.width_mm,
                    height_mm=p.height_mm,
                    vector=p.vector,
                    status=STATUS_DONE,
                    replaced=replaced,
                )
            )
            job.step += 1
            _emit(job, publish)

        job.outputs = outputs

        if req.include_report and report is not None:
            job.phase = "report"
            _emit(job, publish)
            job.report = _write_report(job, outputs, report, tmp_dir, names[REPORT_KEY])
            job.step += 1

        ok = [o for o in outputs if o.status == STATUS_DONE]
        if not ok:
            job.status = STATUS_FAILED
            job.error_code = outputs[0].error_code if outputs else "no_output"
            job.error_params = outputs[0].error_params if outputs else {}
        elif len(ok) < len(outputs) or (
            job.report is not None and job.report.status != STATUS_DONE
        ):
            job.status = STATUS_PARTIAL
        else:
            job.status = STATUS_DONE
    except Cancelled:
        job.status = STATUS_CANCELLED
        job.outputs = []
        job.report = None
    except ExportRequestError as exc:
        job.status = STATUS_FAILED
        job.error_code = exc.code
        job.error_params = exc.params
    except Exception as exc:  # noqa: BLE001 —— 作业失败不能把 HTTP 线程带走
        job.status = STATUS_FAILED
        job.error_code = "export_failed"
        job.error_params = {"error": str(exc)[:400]}
    finally:
        _drop_tmp(job)
        _release(reserved, job.id)
        job.finished_at = time.time()
        job.phase = job.status
        _emit(job, publish)
    return job.to_payload()


def _write_report(
    job: ExportJob,
    outputs: list[Output],
    report: Callable[[ExportJob, list[Output]], bytes | None],
    tmp_dir: Path,
    planned_name: str,
) -> Output | None:
    """样式检查报告。**它自己的失败只算它自己的**。

    名字由 `_plan_names()` 给——报告是一个会被写到最终目录里的文件，
    覆盖策略对它同样成立。自己在这里拼名字的话，`ask` 会静默盖掉上一次的
    报告，`rename` 会给图编号却仍然覆盖报告（PR #214 评审）。
    """
    try:
        data = report(job, outputs)
    except Exception as exc:  # noqa: BLE001
        return Output(
            format=REPORT_KEY,
            status=STATUS_FAILED,
            error_code="report_failed",
            error_params={"error": str(exc)[:200]},
        )
    if data is None:
        return None
    name = planned_name
    tmp = tmp_dir / "report.json"
    try:
        tmp.write_bytes(data)
        atomicio.publish_file(tmp, job.export_dir / name)
    except (OSError, atomicio.AtomicWriteError) as exc:
        return Output(
            format=REPORT_KEY,
            status=STATUS_FAILED,
            error_code=getattr(exc, "code", "report_write_failed"),
            error_params={"error": str(exc)[:200]},
        )
    return Output(
        format=REPORT_KEY,
        name=name,
        url=f"/exports/{name}",
        bytes=len(data),
        status=STATUS_DONE,
    )


def _fail(
    job: ExportJob,
    code: str,
    params: dict,
    publish: Callable[[dict], None] | None,
    *,
    recoverable: bool,
) -> dict:
    job.status = STATUS_FAILED
    job.error_code = code
    job.error_params = params
    job.error_recoverable = recoverable
    job.finished_at = time.time()
    job.phase = STATUS_FAILED
    _emit(job, publish)
    return job.to_payload()


def _emit(job: ExportJob, publish: Callable[[dict], None] | None) -> None:
    if publish is None:
        return
    try:
        publish(job.to_payload())
    except Exception:  # noqa: BLE001 —— 推送失败不影响导出本身
        pass


def run_async(
    job: ExportJob,
    produce: Callable[[ExportJob, Path], list[Produced]],
    *,
    publish: Callable[[dict], None] | None = None,
    report: Callable[[ExportJob, list[Output]], bytes | None] | None = None,
) -> None:
    """在后台线程里跑。**关掉对话框不取消它**——那是 §九 明写的行为。"""
    t = threading.Thread(
        target=run,
        args=(job, produce),
        kwargs={"publish": publish, "report": report},
        name=f"export-{job.id}",
        daemon=True,
    )
    t.start()


def reset_for_tests() -> None:
    with _LOCK:
        _JOBS.clear()


def snapshot() -> dict[str, Any]:
    """诊断用：现在有几个作业、各是什么状态。"""
    with _LOCK:
        return {
            "count": len(_JOBS),
            "by_status": {
                s: sum(1 for j in _JOBS.values() if j.status == s)
                for s in {j.status for j in _JOBS.values()}
            },
        }
