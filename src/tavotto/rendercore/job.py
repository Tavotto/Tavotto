"""接 `engine.exportjob` 的 `produce` 形状（统一实施包 U06 / U07，ADR 0059 / 0066）。

`exportjob.run(job, produce)` 只要求一个 `produce(job, tmp_dir) -> list[Produced]`；旧 facade 那条路是
`app._export_produce_canvas`。这里给 RenderCore 一条同形的路，**作业生命周期（临时目录 → 原子发布 /
partial / 取消 / 终局字段顺序）一个字不改**，全在 `exportjob` 里。

`scope=canvas`：编译 RenderPlan → 每个面板的冻结源在写入前 `read_frozen()` 读进内存并核 hash（RC-014；写入器
只吃这一份字节）→ `pdfwriter` 把 **Canonical PDF** 写进作业自己的临时目录（RC-015 / 016：中间文件私有）→
要了 PNG / TIFF 时把这份 PDF 交给 render child **栅格化一次**（`renderhost.RenderHost.render(dpi=…)`），
得到一个 `RasterBuffer`，PNG 与 TIFF 从**同一个** buffer 编码（RC-052 / RC-053：同一份字节两个容器；透明背景
= 页面不画底 + child 从全 0 起算，PNG 色型 6 / TIFF ExtraSamples 2 都是 straight alpha；密度 = 请求的 ppi）。
PDF 没要也照样写进临时目录——它是 PNG / TIFF 的唯一来源，不存在「PNG 用另一个 layout 引擎」这条路。
其它格式按 `ir.CAPABILITIES` 逐项报 `format_failed`（error 里带结构化理由），能出的照常交付——`partial`
是 `exportjob` 的既有语义；EPS 报旧路同一个稳定码 `eps_not_for_canvas`（U08 入口审计：老客户端认它）。写入器的 `WriterError`（不支持的操作、源打不开、字节身份不符）与 child 的
`RenderChildError`（超时 / 崩溃 / 预算）同样落到该格式的 `format_failed`，不整页失败、不静默降级、不拿
旧文件冒充。

编译期的事实（缺字、落到 CJK 脸的字符、hidden 被丢）进 `job.warnings`，与旧路 worker 的
warnings 同一个口子——「导出的图和画布上不一样」必须有个说法。

U08 起 `app._export_produce` 在候选后端被选中时（`pdfbackend.selected() == "rendercore"`，ADR 0067）把
`scope=canvas` 交给这里：`sources` 是 `ExecutionSourceResolver`（带 override / runtime 素材由当次 worker 现画
并附回执）、`provider` / `host` 来自 `rendercore.facade`（一个进程一份字体注册表、一个 render child）。
默认后端仍是 PyMuPDF，那条路一字不变。`host=None` 时用进程级共享的 render child（`renderhost.shared()`）。
"""

from __future__ import annotations

from pathlib import Path

from ..engine import exportjob, exportreq
from . import BACKEND_NAME, ir, pdfwriter, plan, raster
from .hbshaper import HbFaceProvider
from .renderhost import RenderChildError, RenderHost
from .sources import SourceError, SourceResolver, read_frozen

#: 一次作业里全部冻结源的字节总预算（读进内存之前按 `SourceArtifact.size_bytes` 判）。像素预算只管解码后的
#: 位图，管不住「几份很大的 PDF / 压缩得很小的位图」把导出进程挤死（Codex #463 第四轮 P2）；超过就是结构化
#: 失败（`export_render_failed` + `source_budget_exceeded`），一个字节不读。**记账 = 全部源之和 + 最大的那一份**：
#: `read_frozen()` 交出 `bytes` 那一刻同时存在预分配缓冲与结果各一份，峰值多出最大单个源的大小（第六轮 P2）。
SOURCE_BYTES_BUDGET = 512 * 1024 * 1024


def produce(
    job: exportjob.ExportJob,
    tmp_dir: Path,
    *,
    sources: SourceResolver,
    provider: HbFaceProvider,
    host: RenderHost | None = None,
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
    sizes = [int(fs.artifact.size_bytes) for fs in rp.sources.values()]
    total_bytes = sum(sizes)
    peak_bytes = total_bytes + (max(sizes) if sizes else 0)  # 读最大那份时的瞬时双份
    if peak_bytes > SOURCE_BYTES_BUDGET:
        raise exportreq.ExportRequestError(
            "export_render_failed",
            f"冻结源合计 {total_bytes} 字节（读取峰值 {peak_bytes}），超过预算 {SOURCE_BYTES_BUDGET}",
            {
                "id": "",
                "reason": f"source_budget_exceeded: {peak_bytes} > {SOURCE_BYTES_BUDGET}",
                "source_bytes": total_bytes,
                "peak_bytes": peak_bytes,
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

    width_mm, height_mm = round(req.canvas.page_w_mm, 3), round(req.canvas.page_h_mm, 3)
    produced: list[exportjob.Produced] = []
    plan_half = plan_facts(rp)

    def failed(fmt: str, error: str, **params) -> None:
        produced.append(
            exportjob.Produced(
                format=fmt,
                error_code="format_failed",
                error_params={"error": error[:200], **params},
            )
        )

    # ---- Canonical PDF：一定写（PNG / TIFF 从它出），要了才交付 -------------------------
    job.check_cancelled()
    pdf_tmp = tmp_dir / "out.pdf"
    pdf_facts = None
    pdf_error: pdfwriter.WriterError | None = None
    gaps_pdf = rp.unsupported.get(exportreq.FORMAT_PDF) or []
    if not gaps_pdf:
        try:
            pdf_facts = pdfwriter.write_pdf(rp.page, pdf_tmp, provider, files)
        except pdfwriter.WriterError as exc:
            pdf_error = exc

    # ---- 栅格：要了 PNG / TIFF 就把 Canonical PDF 交给 render child 栅格一次 --------------
    wants_raster = any(f in exportreq.RASTER_FORMATS for f in req.formats)
    buf: raster.RasterBuffer | None = None
    raster_error: Exception | None = None
    raster_facts: dict = {}
    if wants_raster and pdf_facts is not None and not _raster_gaps(rp):
        job.check_cancelled()
        dpi = req.ppi or exportreq.PPI_DEFAULT
        h = host if host is not None else _shared_host()
        try:
            buf = h.render(
                pdf_tmp,
                dpi=float(dpi),
                transparent=rp.page.background is None,
                page_size_pt=(rp.page.width_pt, rp.page.height_pt),
            )
            raster_facts = {
                "renderer": "pdfium",
                "px": [buf.width, buf.height],
                "channels": buf.channels,
                "dpi": buf.dpi,
            }
        except RenderChildError as exc:
            raster_error = exc

    for fmt in req.formats:
        job.check_cancelled()
        if fmt == exportreq.FORMAT_PDF:
            if gaps_pdf:
                failed(fmt, _gap_text(gaps_pdf), unsupported=gaps_pdf)
            elif pdf_error is not None:
                failed(fmt, str(pdf_error), **pdf_error.params)
            else:
                assert pdf_facts is not None
                produced.append(
                    exportjob.Produced(
                        format=fmt,
                        tmp_path=pdf_tmp,
                        width_mm=width_mm,
                        height_mm=height_mm,
                        vector=True,
                        error_params={
                            "plan_identity": rp.plan_identity,
                            "sha256": pdf_facts.sha256,
                        },
                        manifest={"plan": {**plan_half, "vector": True, "px": None}},
                    )
                )
            continue
        if fmt in exportreq.RASTER_FORMATS:
            gaps = rp.unsupported.get(fmt) or []
            if gaps:
                failed(fmt, _gap_text(gaps), unsupported=gaps)
            elif pdf_facts is None:
                # Canonical PDF 没写出来：位图不可能从别的东西出（RC-052 must_fail：PNG 用另一个 layout 引擎）
                reason = gaps_pdf or ([pdf_error.params] if pdf_error else [])
                failed(fmt, "Canonical PDF 未写出，位图无从栅格", canonical_pdf=reason)
            elif raster_error is not None:
                failed(fmt, str(raster_error), raster_code=getattr(raster_error, "code", ""))
            else:
                assert buf is not None
                tmp = tmp_dir / f"out.{fmt}"
                try:
                    if fmt == exportreq.FORMAT_TIFF:
                        raster.write_tiff(buf, tmp)
                    else:
                        tmp.write_bytes(raster.encode_png(buf))
                except (OSError, ValueError) as exc:  # noqa: PERF203 —— 一个格式挂了不牵连另一个
                    failed(fmt, f"{type(exc).__name__}: {exc}")
                    continue
                produced.append(
                    exportjob.Produced(
                        format=fmt,
                        tmp_path=tmp,
                        width_px=buf.width,
                        height_px=buf.height,
                        width_mm=width_mm,
                        height_mm=height_mm,
                        vector=False,
                        error_params={"plan_identity": rp.plan_identity, **raster_facts},
                        manifest={
                            "plan": {
                                **plan_half,
                                "vector": False,
                                "px": [buf.width, buf.height],
                                "ppi": float(dpi),
                            }
                        },
                    )
                )
            continue
        # EPS（及将来任何没有写入器的格式）：能力表逐项说明
        gaps = rp.unsupported.get(fmt) or [
            {"operation": "format", "reason": f"RenderCore 没有 {fmt} 写入器", "object_id": ""}
        ]
        if fmt == exportreq.FORMAT_EPS:
            # 画布合成给不出 EPS（没有 PostScript 写入器，ADR 0046）：与旧路同一个稳定码
            # `eps_not_for_canvas`（i18n 两侧都有它），不另造一个码让界面认不出（RC-090）；
            # 结构化理由照样带在 params 里
            produced.append(
                exportjob.Produced(
                    format=fmt, error_code="eps_not_for_canvas", error_params={"unsupported": gaps}
                )
            )
            continue
        failed(fmt, _gap_text(gaps), unsupported=gaps)
    return produced


def plan_facts(rp: plan.RenderPlan) -> dict:
    """ArtifactManifest 的**计划**半张（04 §2：plan / observed / policy 分开）：生产者说它写了什么。
    `text` 是每个 ShapedText 行的用户原文（合成上下标取 ActualText），检查器拿它与抽回来的文字层比；
    `object_boxes` 是画布对象在页面空间的保守包围盒（裁切只对它们判）；`sources` / `execution_receipts`
    是源产物与回执的公开身份（不含路径）。"""
    page = rp.page
    lines: list[str] = []
    boxes: list[dict] = []
    for node, _path in ir.walk(page):
        if isinstance(node, ir.ShapedText):
            text = "".join(
                r.actual_text if r.actual_text is not None else r.cluster_text for r in node.runs
            )
            if text.strip():
                lines.append(text)
        elif isinstance(node, (ir.ImportedPage, ir.Image)):
            x, y, w, h = node.rect
            boxes.append({"id": node.object_id, "bbox": [x, y, x + w, y + h]})
    sources = [
        {
            "source_id": fs.artifact.source_id,
            "origin": fs.artifact.origin,
            "kind": fs.artifact.kind,
            "bytes_sha256": fs.artifact.bytes_sha256,
            "receipt_identity": fs.artifact.receipt_identity,
            "patch_hash": fs.artifact.patch_hash,
        }
        for fs in rp.sources.values()
    ]
    return {
        "backend": BACKEND_NAME,
        "plan_identity": rp.plan_identity,
        "page_pt": [page.width_pt, page.height_pt],
        "text": lines,
        "object_boxes": boxes,
        "sources": sources,
        "execution_receipts": sorted(
            {s["receipt_identity"] for s in sources if s.get("receipt_identity")}
        ),
    }


def _raster_gaps(rp: plan.RenderPlan) -> list[dict]:
    return [g for fmt in exportreq.RASTER_FORMATS for g in (rp.unsupported.get(fmt) or [])]


def _gap_text(gaps: list[dict]) -> str:
    return "; ".join(f"{g['operation']}: {g['reason']}" for g in gaps)


def _shared_host() -> RenderHost:
    from .renderhost import shared

    return shared()
