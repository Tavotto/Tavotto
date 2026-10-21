"""导出来源成为值（#1209 / A2）：「这次导出里这张图用哪份来源」只在这里回答。

纯标准库、纯函数、导出前、不执行、不读文件——调用方（`app.py`）把事实（有无脚本、是不是 runtime、
发给后端的 overrides）收齐传进来。两个层面的使用者读同一份判据：

* **执行路径**：`wants_execution()` 是 `app._resolve_panel_source`、`app._export_produce_original` 的
  `execution_source`、`rendercore.sources.needs_execution` 共用的唯一谓词（原先三处各写一遍）；
  `original_rerender()` 是 `app._original_rerender` 的本体。
* **发布**：`build_route()` 给 `/api/export/validate` 的 `route`——每个面板、每种所选格式的做法
  （`copy` 原件逐字复制 / `convert` 同一份原件转换 / `redraw` 脚本重画）或逐项不可用及原因码。

「只回事实、不做决定」：route 不拦导出；没有基准证明的格式（SVG 素材的 eps 要字节绑定、静态 PDF 的 svg 要配对快照）
写进 `conditions`，真实裁决仍在导出路径（`exportjob.enforce_one_basis` 等）。
与前端 `web/src/lib/exportRoute.ts` 的 `embedsDiskBitmap` 的严格同源对由
`tests/golden/export_route_vectors.json` 看护（`tests/test_export_route_vectors.py` 两个范围各对一遍）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SCOPE_ORIGINAL = "original"
SCOPE_CANVAS = "canvas"

#: 面板文件种类（由后缀 / id 前缀得出，调用方传入）。
KIND_RASTER, KIND_PDF, KIND_SVG, KIND_RUNTIME = "raster", "pdf", "svg", "runtime"

#: 来源：磁盘原件 / 由当次 worker 现画 / 现画不了（画布范围：要现画却没有脚本）。
SOURCE_DISK, SOURCE_REDRAW, SOURCE_UNAVAILABLE = "disk", "redraw", "unavailable"
#: 面板带保存时的 Figure 快照引用且本来要现画：不跑脚本，加载持久化的快照；引用过期则拒绝导出。
SOURCE_SNAPSHOT = "snapshot"

#: 一种格式的做法。
HOW_COPY, HOW_CONVERT, HOW_REDRAW, HOW_UNAVAILABLE = "copy", "convert", "redraw", "unavailable"

#: 只在这里出现的原因码（其余沿用导出路径本来就用的稳定码）。
REASON_SOURCE_NEEDS_EXECUTION = "source_needs_execution"
REASON_EPS_NOT_FOR_CANVAS = "eps_not_for_canvas"
REASON_EPS_NEEDS_SCRIPT = "eps_needs_script"
REASON_SVG_SOURCE_UNSUPPORTED = "svg_source_unsupported"
REASON_SVG_ASSET_UNSUPPORTED = "svg_asset_export_unsupported"

#: `conditions`：route 给不出、要导出时才核的前提。
COND_LIVE_FIGURE = "live_figure"
COND_SVG_ASSET_BINDING = "svg_asset_binding"
COND_FIGURE_SNAPSHOT = "figure_snapshot"


def original_rerender(formats: Iterable[str]) -> bool:
    """原图范围：所选格式要不要让有脚本的图现画（哪怕没有 override）——要 EPS 且不要 SVG。

    SVG 走快照那条路；EPS 只有 worker 写得出，PDF / PNG / TIFF 就得和它出自同一次脚本运行。
    """
    fmts = tuple(formats)
    return "eps" in fmts and "svg" not in fmts


def wants_execution(
    *, runtime: bool, overrides: Sequence[Any] | None, rerender: bool = False
) -> bool:
    """这个面板这次要不要 worker 现画：runtime 素材永远要；带 override 的要；`rerender` 时有脚本就要。

    与有没有脚本无关——脚本是否存在由调用方的处置决定（原图范围退回磁盘文件、画布范围报
    `source_needs_execution`）。
    """
    return bool(runtime) or bool(overrides) or bool(rerender)


def executes_from_snapshot(*, has_reference: bool) -> bool:
    """要现画的面板带了快照引用时，`app._execution_source` 先查引用、加载快照，不跑脚本、不看有无脚本。

    （调用方只在 `wants_execution` 成立后才问这一句；`rerender` 不算——它走 `_resolve_panel_source`，
    不经 `_execution_source`。）
    """
    return bool(has_reference)


def panel_route(
    *,
    scope: str,
    formats: Sequence[str],
    file_kind: str,
    overrides: Sequence[Any] | None,
    has_script: bool,
    has_snapshot: bool = False,
) -> dict:
    """一个面板的 route：`{source, embeds_disk_bitmap, formats: {fmt: {how, reason?, conditions?}}}`。

    `overrides` 是**该范围实际发给后端的**那份（原图范围已过受守卫 PNG 的守卫；画布范围原样）。
    """
    runtime = file_kind == KIND_RUNTIME
    canvas = scope == SCOPE_CANVAS
    svg_asset = file_kind == KIND_SVG
    rerender = (not canvas) and original_rerender(formats)
    executes = wants_execution(runtime=runtime, overrides=overrides, rerender=rerender)
    can_draw = runtime or has_script
    edits = wants_execution(runtime=runtime, overrides=overrides)
    if edits and executes_from_snapshot(has_reference=has_snapshot):
        source = SOURCE_SNAPSHOT
    elif not executes:
        source = SOURCE_DISK
    elif can_draw:
        source = SOURCE_REDRAW
    elif canvas:
        source = SOURCE_UNAVAILABLE
    else:
        source = SOURCE_DISK  # 原图范围：没有脚本，退回磁盘文件
    static_svg_asset = svg_asset and not overrides and (canvas or not has_snapshot)
    unedited_svg_asset = static_svg_asset and not canvas
    out: dict[str, dict] = {}
    for fmt in formats:
        out[fmt] = _format_route(
            fmt,
            source=source,
            file_kind=file_kind,
            canvas=canvas,
            static_svg_asset=static_svg_asset,
            unedited_svg_asset=unedited_svg_asset,
            has_script=can_draw,
            has_snapshot=has_snapshot,
            wants_svg="svg" in formats,
        )
    if source == SOURCE_SNAPSHOT or (
        source == SOURCE_DISK and file_kind == KIND_PDF and has_snapshot
    ):
        # 引用核验发生在任何一种格式开始之前（`_execution_source` / 静态 PDF 的快照加载），对不上整次作业被拒：
        # 是这个面板**所有**格式的前提，不是某个格式分支的事
        for entry in out.values():
            entry.setdefault("conditions", [])
            if COND_FIGURE_SNAPSHOT not in entry["conditions"]:
                entry["conditions"].insert(0, COND_FIGURE_SNAPSHOT)
    # EPS 能不能给——**不论用户这次勾没勾**：对话框要在勾之前就说清楚这个选项可不可用（禁用并说原因）。
    # EPS 一项不依赖来源（只看范围 / 有无脚本 / 是否 SVG 素材），所以与 formats 无关
    offers = {
        "eps": out["eps"]
        if "eps" in out
        else _format_route(
            "eps",
            source=source,
            file_kind=file_kind,
            canvas=canvas,
            static_svg_asset=static_svg_asset,
            unedited_svg_asset=unedited_svg_asset,
            has_script=can_draw,
            has_snapshot=has_snapshot,
            wants_svg="svg" in formats,
        ),
    }
    return {
        "source": source,
        "embeds_disk_bitmap": file_kind == KIND_RASTER and source == SOURCE_DISK,
        "formats": out,
        "offers": offers,
    }


def _unavailable(reason: str) -> dict:
    return {"how": HOW_UNAVAILABLE, "reason": reason}


def _format_route(
    fmt: str,
    *,
    source: str,
    file_kind: str,
    canvas: bool,
    static_svg_asset: bool,
    unedited_svg_asset: bool,
    has_script: bool,
    has_snapshot: bool,
    wants_svg: bool,
) -> dict:
    if fmt == "eps":
        if canvas:
            return _unavailable(REASON_EPS_NOT_FOR_CANVAS)
        if not has_script:
            return _unavailable(REASON_EPS_NEEDS_SCRIPT)
        if unedited_svg_asset:
            return {
                "how": HOW_REDRAW,
                "conditions": [COND_SVG_ASSET_BINDING, COND_LIVE_FIGURE],
            }
        # 同一作业还要 svg 时 eps 只取已有的活 Figure（`existing_only`），不启动 worker
        return {"how": HOW_REDRAW, **({"conditions": [COND_LIVE_FIGURE]} if wants_svg else {})}
    if static_svg_asset and (canvas or fmt != "svg"):
        # 没有编辑的 SVG 素材：画布合成不接，原图范围只有 svg（逐字复制）与 eps（字节绑定的活 Figure）
        return _unavailable(REASON_SVG_ASSET_UNSUPPORTED)
    if source == SOURCE_SNAPSHOT:
        # 加载持久化快照，不跑脚本；引用过期（与源或编辑对不上）整次导出被拒
        return {"how": HOW_CONVERT}
    if source == SOURCE_UNAVAILABLE:
        return _unavailable(REASON_SOURCE_NEEDS_EXECUTION)
    if fmt == "svg":
        if unedited_svg_asset:
            return {"how": HOW_COPY}
        if source == SOURCE_REDRAW:
            return {"how": HOW_REDRAW}
        if file_kind == KIND_RASTER and not canvas:
            return _unavailable(REASON_SVG_SOURCE_UNSUPPORTED)
        return {"how": HOW_CONVERT, "conditions": [COND_FIGURE_SNAPSHOT]}
    if source == SOURCE_REDRAW:
        return {"how": HOW_REDRAW}
    # 磁盘原件。原图范围：矢量 PDF 整页搬运不重画，位图保源像素网格；画布范围：不论格式都经
    # `rendercore.job.produce` 重新合成页面，没有逐字复制
    copy = fmt == "pdf" and file_kind == KIND_PDF and not canvas
    return {"how": HOW_COPY if copy else HOW_CONVERT}


def build_route(
    *,
    scope: str,
    formats: Sequence[str],
    panels: Iterable[Mapping[str, Any]],
) -> dict:
    """整次导出的 route：`{scope, formats, panels: {panel_id: panel_route}}`。

    `panels` 每项 `{id, file_kind, overrides, has_script, has_snapshot?}`。同 id 的多个实例（画布上同一张图
    摆了几次）只在它们 route 相同时合并；不同就各自带 `#n` 后缀留下来，不替它们裁决。
    """
    result: dict[str, dict] = {}
    for panel in panels:
        route = panel_route(
            scope=scope,
            formats=formats,
            file_kind=str(panel["file_kind"]),
            overrides=panel.get("overrides"),
            has_script=bool(panel.get("has_script")),
            has_snapshot=bool(panel.get("has_snapshot")),
        )
        key = str(panel["id"])
        n = 1
        while key in result and result[key] != route:
            n += 1
            key = f"{panel['id']}#{n}"
        result[key] = route
    return {"scope": scope, "formats": list(formats), "panels": result}
