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


def _sweep() -> None:
    """过期作业清出去。**顺便清掉它可能留下的临时目录**——进程被 kill 时
    `run()` 的 finally 跑不到，那些目录会一直躺在用户的导出目录里。"""
    now = time.time()
    for jid, job in list(_JOBS.items()):
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
    真正的清理发生在执行线程回到检查点的那一刻。"""
    job = get(job_id)
    if job is None or job.status in (STATUS_DONE, STATUS_PARTIAL, STATUS_FAILED, STATUS_CANCELLED):
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


def _final_names(job: ExportJob, produced: list[Produced]) -> dict[str, str]:
    """产出 → 最终文件名。覆盖策略在这里落地，**只有这一处**。"""
    req = job.request
    if req.legacy_naming:
        # 旧契约：`<stem>_<MMDD_HHMMSS>.<ext>`。时间戳让它天生撞不了车，
        # 所以老标签页与 CI 脚本从来不需要覆盖策略，行为一个字节不变。
        ts = time.strftime("%m%d_%H%M%S")
        return {p.format: f"{req.filename}_{ts}.{p.format}" for p in produced}

    taken_in_batch: set[str] = set()

    def taken(name: str) -> bool:
        return name in taken_in_batch or (job.export_dir / name).exists()

    names: dict[str, str] = {}
    for p in produced:
        if req.overwrite == exportreq.OVERWRITE_RENAME:
            name = exportreq.dedupe_name(req.filename, p.format, taken)
        else:
            name = exportreq.output_name(req.filename, p.format)
        taken_in_batch.add(name)
        names[p.format] = name
    return names


def run(
    job: ExportJob,
    produce: Callable[[ExportJob, Path], list[Produced]],
    *,
    publish: Callable[[dict], None] | None = None,
    report: Callable[[ExportJob, list[Output]], tuple[bytes, str] | None] | None = None,
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

    # `ask` 策略：撞名就**什么都不做**地回来。先问再动手，别先渲染半分钟
    # 再告诉用户"这个名字已经有了"
    if req.overwrite == exportreq.OVERWRITE_ASK and not req.legacy_naming:
        conflicts = [
            exportreq.output_name(req.filename, f)
            for f in req.formats
            if (job.export_dir / exportreq.output_name(req.filename, f)).exists()
        ]
        if conflicts:
            job.conflicts = conflicts
            job.status = STATUS_CONFLICT
            job.finished_at = time.time()
            job.phase = "conflict"
            _emit(job, publish)
            return job.to_payload()

    try:
        job.export_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail(job, "export_dir_unwritable", {"error": str(exc)}, publish, recoverable=True)

    tmp_dir = job.export_dir / f"{TMP_PREFIX}{job.id}"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return _fail(job, "tmp_dir_failed", {"error": str(exc)}, publish, recoverable=True)
    job._tmp_dir = tmp_dir

    try:
        job.phase = "rendering"
        _emit(job, publish)
        produced = produce(job, tmp_dir)
        job.check_cancelled()

        job.phase = "writing"
        _emit(job, publish)
        names = _final_names(job, [p for p in produced if p.tmp_path is not None])
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
            job.report = _write_report(job, outputs, report, tmp_dir)
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
        job.finished_at = time.time()
        job.phase = job.status
        _emit(job, publish)
    return job.to_payload()


def _write_report(
    job: ExportJob,
    outputs: list[Output],
    report: Callable[[ExportJob, list[Output]], tuple[bytes, str] | None],
    tmp_dir: Path,
) -> Output | None:
    """样式检查报告。**它自己的失败只算它自己的**。"""
    try:
        made = report(job, outputs)
    except Exception as exc:  # noqa: BLE001
        return Output(
            format="report",
            status=STATUS_FAILED,
            error_code="report_failed",
            error_params={"error": str(exc)[:200]},
        )
    if made is None:
        return None
    data, suffix = made
    name = (
        f"{job.request.filename}_{time.strftime('%m%d_%H%M%S')}{suffix}"
        if job.request.legacy_naming
        else f"{job.request.filename}{suffix}"
    )
    tmp = tmp_dir / f"report{suffix}"
    try:
        tmp.write_bytes(data)
        atomicio.publish_file(tmp, job.export_dir / name)
    except (OSError, atomicio.AtomicWriteError) as exc:
        return Output(
            format="report",
            status=STATUS_FAILED,
            error_code=getattr(exc, "code", "report_write_failed"),
            error_params={"error": str(exc)[:200]},
        )
    return Output(
        format="report",
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
    report: Callable[[ExportJob, list[Output]], tuple[bytes, str] | None] | None = None,
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
