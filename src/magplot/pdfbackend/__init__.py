"""PDF 后端边界层——全仓库唯一允许 import PyMuPDF 的地方在其实现模块里。

**为什么要有这一层**：PyMuPDF 以 AGPL-3.0 发布，Magplot 因此也只能是
AGPL-3.0-only。把「读页面尺寸 / 栅格化 / 按布局合成」这三件事收敛成下面这组
与实现无关的函数后，换用其它 PDF 库只需新写一个实现模块，HTTP 层（`app.py`）
一行不用动。`app.py` 只认这里导出的名字，不认识 `pymupdf`。

边界契约（进出全是 dict / Path / 基本类型，不泄漏任何后端对象）：

  probe_asset(path, kind)           → 素材原始尺寸，供图库列表换算物理尺寸
  render_preview_png(path, w, out)  → 画布显示用的位图预览（带磁盘缓存）
  text_width(s, size_pt, ...)       → 中英混排字符串宽度（pt）
  compose(page_w_mm, page_h_mm)     → 合成画布；place() 逐个落对象，save_*() 出图
  annotate_asset(pdf, png, objs)    → 把画布标注画进单图文件（写回原图带标注）
  BACKEND_NAME / BACKEND_VERSION    → 后端身份（进渲染缓存键：换实现/换版本
                                       出来的像素可能就不一样了）

唯一的例外是 `compose()` 返回的画布对象本身——它由实现模块定义，
但只通过 place/save_pdf/save_png/close 这几个方法被使用。
"""
from .pymupdf_backend import (  # noqa: F401
    BACKEND_NAME,
    BACKEND_VERSION,
    annotate_asset,
    compose,
    hex2rgb,
    mm2pt,
    probe_asset,
    render_preview_png,
    text_width,
)

__all__ = [
    "BACKEND_NAME",
    "BACKEND_VERSION",
    "annotate_asset",
    "compose",
    "hex2rgb",
    "mm2pt",
    "probe_asset",
    "render_preview_png",
    "text_width",
]
