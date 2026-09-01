"""PDF 后端边界层——全仓库唯一允许 import PyMuPDF 的地方在其实现模块里。

**为什么要有这一层**：PyMuPDF 以 AGPL-3.0 发布，Tavotto 因此也只能是
AGPL-3.0-only。把「读页面尺寸 / 栅格化 / 按布局合成」这三件事收敛成下面这组
与实现无关的函数后，换用其它 PDF 库只需新写一个实现模块，HTTP 层（`app.py`）
一行不用动。`app.py` 只认这里导出的名字，不认识 `pymupdf`。

边界契约（进出全是 dict / Path / 基本类型，不泄漏任何后端对象）：

  probe_asset(path, kind)           → 素材原始尺寸，供图库列表换算物理尺寸
  render_preview_png(path, w, out)  → 画布显示用的位图预览（带磁盘缓存）
  text_width(s, size_pt, ...)       → 中英混排字符串宽度（pt；`family` 与落笔同族）
  text_plan(s, family, ...)         → 字形归属计划 [(片段, 层)]；层见 glyphplan.GLYPH_LAYERS
  missing_glyphs(s, family, ...)    → 这段文字里画不出来的字符（预检的唯一依据）
  coverage_ranges() / COVERAGE_MAX_CP
                                    → 三层覆盖的区间表，生成 canvas_coverage.json 用
  CANVAS_TEXT_FAMILIES              → 画布文字能选的字体族**闭集**（三个通用族）。
                                       与 `web/src/lib/typography.ts` 严格同源；
                                       换后端实现时这条闭集要跟着新后端画得出
                                       什么走，不能照抄——它是一句能力承诺。
  compare_png(a, b)                 → 两张 PNG 的像素差异指标（写回像素门）
  compose(page_w_mm, page_h_mm, transparent=False)
                                    → 合成画布；place() 逐个落对象，save_*() 出图
  original_pdf(src, out, page_pt)   → 按原图尺寸出 PDF（矢量源整页搬运，不重画；
                                       位图源的页面尺寸由调用方给——密度的解析
                                       只有 engine/originalspec 一处）
  original_png(src, out, ppi, transparent)
                                    → 按原图出 PNG（位图**永远**保源像素网格；
                                       JPEG 源换容器不换像素）
  annotate_asset(pdf, png, objs)    → 把画布标注画进单图文件（写回原图带标注）
  BACKEND_NAME / BACKEND_VERSION    → 后端身份（进渲染缓存键：换实现/换版本
                                       出来的像素可能就不一样了）

唯一的例外是 `compose()` 返回的画布对象本身——它由实现模块定义，
但只通过 place/save_pdf/save_png/close 这几个方法被使用。
"""

from .pymupdf_backend import (  # noqa: F401
    BACKEND_NAME,
    BACKEND_VERSION,
    CANVAS_TEXT_FAMILIES,
    COVERAGE_MAX_CP,
    annotate_asset,
    compare_png,
    compose,
    coverage_ranges,
    hex2rgb,
    missing_glyphs,
    mm2pt,
    original_pdf,
    original_png,
    probe_asset,
    render_preview_png,
    text_plan,
    text_width,
)

__all__ = [
    "BACKEND_NAME",
    "BACKEND_VERSION",
    "CANVAS_TEXT_FAMILIES",
    "COVERAGE_MAX_CP",
    "annotate_asset",
    "compare_png",
    "compose",
    "coverage_ranges",
    "hex2rgb",
    "missing_glyphs",
    "mm2pt",
    "original_pdf",
    "original_png",
    "probe_asset",
    "render_preview_png",
    "text_plan",
    "text_width",
]
