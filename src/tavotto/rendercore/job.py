"""接 `engine.exportjob` 的 `produce` 形状（统一实施包 U06，ADR 0059）。

`exportjob.run(job, produce)` 只要求一个 `produce(job, tmp_dir) -> list[Produced]`；旧 facade 那条路是
`app._export_produce_canvas`。这里给 RenderCore 一条同形的路，**作业生命周期（临时目录 → 原子发布 /
partial / 取消 / 终局字段顺序）一个字不改**，全在 `exportjob` 里。

`scope=canvas`：编译 RenderPlan → 每个面板的冻结源在写入前 `read_frozen()` 读进内存并核 hash
（RC-014；写入器只吃这一份字节）→ `pdfwriter` 写进作业自己的临时目录（RC-015 / 016：中间文件私有）。
其它格式按 `ir.CAPABILITIES` 逐项报 `format_failed`（error 里带结构化理由），PDF 照常交付——`partial`
是 `exportjob` 的既有语义。写入器的 `WriterError`（不支持的操作、源打不开、字节身份不符）同样落到
`format_failed`，不整页失败、不静默降级。

编译期的事实（缺字、落到 CJK 脸的字符、hidden 被丢）进 `job.warnings`，与旧路 worker 的
warnings 同一个口子——「导出的图和画布上不一样」必须有个说法。

**本模块在 U06 不接任何用户可见入口**（`app.py` 不 import 它）；由 `tests/test_rendercore_job.py`
经真实的 `exportjob.prepare / run` 驱动。
"""

from __future__ import annotations

from pathlib import Path

from ..engine import exportjob, exportreq
from . import ir, pdfwriter, plan
from .hbshaper import HbFaceProvider
from .sources import SourceError, SourceResolver, read_frozen

#: 一次作业里全部冻结源的字节总预算（读进内存之前按 `SourceArtifact.size_bytes` 判）。像素预算只管解码后的
#: 位图，管不住「几份很大的 PDF / 压缩得很小的位图」把导出进程挤死（Codex #463 第四轮 P2）；超过就是结构化
#: 失败（`export_render_failed` + `source_budget_exceeded`），一个字节不读。
SOURCE_BYTES_BUDGET = 512 * 1024 * 1024


def produce(
    job: exportjob.ExportJob,
    tmp_dir: Path,
    *,
    sources: SourceResolver,
    provider: HbFaceProvider,
) -> list[exportjob.Produced]:
    req = job.request
    job.check_cancelled()
    try:
        rp = plan.compile_plan(req, sources=sources, faces=provider)
    except plan.PlanError as exc:
        # reason 里带上里层的 code（source_* / font_*）：读者据它分类，不必解析文案
        inner = exc.params.get("source_code") or exc.params.get("font_code") or ""
        code = f"{exc.code}:{inner}" if inner else exc.code
        raise exportreq.ExportRequestError(
            "export_render_failed",
            f"{exc.params.get('id', '')} 编译失败: {exc}",
            {"id": str(exc.params.get("id", "")), "reason": f"{code}: {exc}"},
        ) from exc
    except ir.IRError as exc:
        raise exportreq.ExportRequestError(
            "export_render_failed",
            f"IR 不合法: {exc}",
            {"id": exc.path, "reason": f"{exc.code}: {exc}"},
        ) from exc
    for p in rp.problems:
        msg = f"{p['object_id']}: {p['code']} {''.join(p.get('chars', []))}".rstrip()
        if msg not in job.warnings:
            job.warnings.append(msg)
    for oid in rp.dropped_hidden:
        msg = f"{oid}: hidden"
        if msg not in job.warnings:
            job.warnings.append(msg)
    # 冻结源在写入之前逐个读进内存并核 hash（RC-014）：写入器用的就是这一份字节，不再碰文件。
    # 读之前先按冻结时记下的大小判总预算——超过的作业一个字节不读、结构化失败
    total_bytes = sum(int(fs.artifact.size_bytes) for fs in rp.sources.values())
    if total_bytes > SOURCE_BYTES_BUDGET:
        raise exportreq.ExportRequestError(
            "export_render_failed",
            f"冻结源合计 {total_bytes} 字节，超过预算 {SOURCE_BYTES_BUDGET}",
            {
                "id": "",
                "reason": f"source_budget_exceeded: {total_bytes} > {SOURCE_BYTES_BUDGET}",
                "source_bytes": total_bytes,
                "budget": SOURCE_BYTES_BUDGET,
            },
        )
    files: dict[str, bytes] = {}
    for key, fs in rp.sources.items():
        job.check_cancelled()
        try:
            files[key] = read_frozen(fs)
        except SourceError as exc:
            raise exportreq.ExportRequestError(
                "export_render_failed",
                str(exc),
                {"id": fs.artifact.source_id, "reason": f"{exc.code}: {exc}"},
            ) from exc

    produced: list[exportjob.Produced] = []
    for fmt in req.formats:
        job.check_cancelled()
        gaps = rp.unsupported.get(fmt) or []
        if fmt != exportreq.FORMAT_PDF:
            # 本切片只有 PDF 写入器。空页 / 全 hidden 的页在能力表上没有任何操作可判、缺口为空，
            # 但那不等于「PNG 能出」——把 PDF 字节写进 .png 报 vector=True 就是假成功（Codex #460 P2）
            gaps = gaps or [
                {
                    "operation": "format",
                    "reason": f"U06 只有 PDF 写入器；{fmt} 的栅格 / 序列化归 U07（ADR 0059）",
                    "object_id": "",
                }
            ]
        if gaps:
            produced.append(
                exportjob.Produced(
                    format=fmt,
                    error_code="format_failed",
                    error_params={
                        "error": "; ".join(f"{g['operation']}: {g['reason']}" for g in gaps)[:200],
                        "unsupported": gaps,
                    },
                )
            )
            continue
        tmp = tmp_dir / f"out.{fmt}"
        try:
            facts = pdfwriter.write_pdf(rp.page, tmp, provider, files)
        except pdfwriter.WriterError as exc:
            produced.append(
                exportjob.Produced(
                    format=fmt,
                    error_code="format_failed",
                    error_params={"error": str(exc)[:200], **exc.params},
                )
            )
            continue
        produced.append(
            exportjob.Produced(
                format=fmt,
                tmp_path=tmp,
                width_mm=round(req.canvas.page_w_mm, 3),
                height_mm=round(req.canvas.page_h_mm, 3),
                vector=True,
                error_params={"plan_identity": rp.plan_identity, "sha256": facts.sha256},
            )
        )
    return produced
