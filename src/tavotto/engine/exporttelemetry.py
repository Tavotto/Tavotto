"""导出终态遥测 `export_result` 的取值（纯函数，不碰网络）。

只回答一件事：**一个已经走到终局的导出作业，对外只说哪几个有限值**。发不发、
发给谁归 `engine/telemetry.py`（同意、白名单、硬开关）；什么时候发归
`exportjob._emit`（每个作业最多一次，见 `ExportJob.on_terminal`）。

属性全是闭集或布尔：

- `outcome`：作业的终局 status 原样（done / partial / failed / cancelled）；
- `scope`：`exportreq.SCOPES`；
- `pdf` / `png` / `eps` / `tiff` / `svg` / `with_report`：**请求**了什么（不是写成了什么——
  失败的导出没有"写成"可言，而"哪个格式的请求在失败"正是要看的）；
- `delivered`：真正落盘的图文件数；
- `stage`（非 done 才有）：轨迹上坏在 / 停在哪一步（`trace.PHASES` 里导出那几步）；
- `error_class`（failed / partial 才有）：错误码折进的粗类，**不是**错误码、更不是异常文字。

`conflict`（`overwrite=ask` 撞名、一个字节都没动）不是终态结果：用户会改名后重新提交，
那一次才是尝试——这里不出事件。
"""

from __future__ import annotations

from . import exportjob, exportreq

#: 轨迹里属于导出作业的步子（`trace.PHASES` 去掉准备接口那几个）。
STAGES = (
    "prepare",
    "source",
    "compile",
    "compose",
    "raster",
    "inspect",
    "publish",
    "report",
)

ERROR_CLASSES = (
    "request",
    "environment",
    "backend",
    "artifact_rejected",
    "source_changed",
    "report",
    "other",
)

_REQUEST_CODES = frozenset(
    {
        "no_format",
        "eps_needs_script",
        "eps_source_unverified",
        "eps_needs_live_figure",
        "export_basis_mismatch",
        "eps_not_for_canvas",
        "missing_figure",
        "missing_original",
        "name_exhausted",
        "unsupported",
    }
)
_ENVIRONMENT_CODES = frozenset({"export_dir_unwritable", "tmp_dir_failed", "publish_failed"})
_SOURCE_CODES = frozenset(
    {
        "source_changed",
        "source_unreadable",
        "artifact_source_unavailable",
        "input_remap_changed",
    }
)
_REPORT_CODES = frozenset({"report_failed", "report_missing_payload", "report_write_failed"})

#: 能发的终态。`conflict` / 还在跑的不在其中。
OUTCOMES = (
    exportjob.STATUS_DONE,
    exportjob.STATUS_PARTIAL,
    exportjob.STATUS_FAILED,
    exportjob.STATUS_CANCELLED,
)


def error_class(code: str | None) -> str | None:
    if not code:
        return None
    if code in _REQUEST_CODES or code.startswith("bad_"):
        return "request"
    if code in _ENVIRONMENT_CODES:
        return "environment"
    if code == "backend_unavailable":
        return "backend"
    if code == "artifact_rejected":
        return "artifact_rejected"
    if code in _SOURCE_CODES:
        return "source_changed"
    if code in _REPORT_CODES:
        return "report"
    return "other"


def _first_error_code(job: exportjob.ExportJob) -> str | None:
    if job.error_code:
        return job.error_code
    for o in (*job.outputs, *([job.report] if job.report is not None else [])):
        if o.status != exportjob.STATUS_DONE and o.error_code:
            return o.error_code
    return None


def properties(job: exportjob.ExportJob) -> dict | None:
    """终局作业 → 事件属性；不该发的（非终态 / conflict）回 None。"""
    if job.status not in OUTCOMES:
        return None
    req = job.request
    props: dict = {
        "outcome": job.status,
        "scope": req.scope,
        "pdf": exportreq.FORMAT_PDF in req.formats,
        "png": exportreq.FORMAT_PNG in req.formats,
        "eps": exportreq.FORMAT_EPS in req.formats,
        "tiff": exportreq.FORMAT_TIFF in req.formats,
        "svg": exportreq.FORMAT_SVG in req.formats,
        "with_report": bool(req.include_report),
        "delivered": min(sum(1 for o in job.outputs if o.status == exportjob.STATUS_DONE), 8),
    }
    if job.status != exportjob.STATUS_DONE:
        stage = job.trace.failed_phase
        if stage in STAGES:
            props["stage"] = stage
    if job.status in (exportjob.STATUS_FAILED, exportjob.STATUS_PARTIAL):
        cls = error_class(_first_error_code(job))
        if cls:
            props["error_class"] = cls
    return props
