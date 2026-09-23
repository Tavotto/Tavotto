"""条带栅格（ADR 0077 P2）：整页像素超过 render child 的单张预算（`RenderHost.max_pixels`，64 M）时，按行带分几次
渲染、边收边编码进 PNG / TIFF——内存只和一带一样大，旧后端导得出的大尺寸（A4 @ 1200 ppi、A3 @ 600 ppi……）
候选后端也导得出。

* **只在超预算时用**：预算以内的一律整页渲染一次（与从前逐字节相同）。PDFium 按带渲染与整页渲染**不逐字节相同**
  （抗锯齿随位图原点 / 尺寸差 1–3 级，重叠边距消不掉，ADR 0077 实测），所以带的切法固定为 `band_rows_for(宽)`：
  同一输入永远同一份像素。
* **一次栅格、两个容器**：要了 PNG 与 TIFF 时，每一带只渲染一次、同时喂给两个写入器（RC-052 / 053 的「同一份像素」
  在条带下照样成立）。
* 整页上限 `RenderHost.max_total_pixels`（512 M）；超了是 `pixel_budget_exceeded`，父侧在起任何一带之前就拒。
* 任何一步失败：两个写入器都 `abort()`（收线程、关文件），异常原样上抛；半截文件在作业的临时目录里，由作业收。
"""

from __future__ import annotations

from pathlib import Path

from .. import tiffwrite
from . import raster
from .renderchild import BAND_OVERLAP, RenderChildError, band_rows_for
from .renderhost import RenderHost

__all__ = ["needs_bands", "write_banded"]


def needs_bands(host: RenderHost, size_px: tuple[int, int]) -> bool:
    """整页像素超过单张预算才走条带；预算以内的整页渲染一次（与从前逐字节相同）。"""
    return int(size_px[0]) * int(size_px[1]) > host.max_pixels


def write_banded(
    host: RenderHost,
    pdf: Path,
    *,
    dpi: float,
    size_px: tuple[int, int],
    page_size_pt: tuple[float, float],
    transparent: bool,
    png: Path | None = None,
    tiff: Path | None = None,
    page: int = 0,
) -> dict:
    """按行带渲染 `pdf` 的一页，写出要的 PNG / TIFF。`size_px` 是整页像素（调用方按页面尺寸 × dpi 独立算出，
    与 child 同一 `round()` 约定）；child 回来的每一带宽度必须等于它，否则是协议错误。回写进文件的事实。"""
    width, height = int(size_px[0]), int(size_px[1])
    if width * height > host.max_total_pixels:
        raise RenderChildError(
            "pixel_budget_exceeded", f"整页 {width}×{height} > {host.max_total_pixels}"
        )
    channels = 4 if transparent else 3
    # 带高：按宽度的目标行数，但不许超过这个 host 的单张预算扣掉上下重叠后放得下的行数（Codex #513 P2：预算配得比
    # BAND_PIXELS 小时，按目标切出的每一带都会被拒）。默认预算（64 M）远大于 16 M 的目标，带高只由宽度定
    fit = host.max_pixels // width - 2 * BAND_OVERLAP
    if fit < 1:
        raise RenderChildError(
            "pixel_budget_exceeded",
            f"单张预算 {host.max_pixels} 连一行宽 {width} 的带（含上下各 {BAND_OVERLAP} 行重叠）都放不下",
        )
    rows = min(band_rows_for(width), fit)
    writers: list[tuple[str, object]] = []
    bands = 0
    try:
        if png is not None:
            writers.append(("png", raster.PngStreamWriter(png, width, height, channels, dpi)))
        if tiff is not None:
            writers.append(
                ("tiff", tiffwrite.TiffStreamWriter(tiff, width, height, channels, dpi=dpi))
            )
        for y0 in range(0, height, rows):
            band = host.render(
                pdf,
                dpi=float(dpi),
                page=page,
                transparent=transparent,
                page_size_pt=page_size_pt,
                band=(y0, min(rows, height - y0)),
            )
            if (band.width, band.channels) != (width, channels):
                raise RenderChildError(
                    "render_child_protocol",
                    f"行带 {band.width}×{band.channels} 与整页 {width}×{channels} 不符",
                )
            for kind, w in writers:
                if kind == "png":
                    w.feed(band)
                else:
                    w.feed(band.samples, band.height, band.stride)
            bands += 1
        facts = {kind: w.close() for kind, w in writers}
    except BaseException:
        for _kind, w in writers:
            w.abort()
        raise
    return {"px": [width, height], "channels": channels, "bands": bands, "band_rows": rows, **facts}
