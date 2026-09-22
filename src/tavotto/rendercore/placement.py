"""面板落位的几何合同：源可见框 → crop → 翻转 → 旋转 → 填满目标矩形（统一实施包 U07，ADR 0065）。

这是「Tavotto 的 crop / rotation / flip 顺序」的**唯一出处**。写入器把 `ImportedPage` / `Image`
变成 `q <M> cm <clip re W n> /X Do Q` 时，`M` 与 clip 都从这里来；PDF 源与位图源走同一个函数，
只是「源可见框」不同（PDF：Form XObject 的 BBox 经它自己的 /Matrix 映射后的包围盒；位图：Image
XObject 的单位正方形）。

## 用户合同（逐句来自旧 facade `_place_panel` 与前端 `PanelView`）

* `crop` 是归一化 (x, y, w, h)、**顶原点**、相对源可见框（旧 `_crop_clip` 同一约定）；有 crop 时
  **裁剪后的那块**映到目标框，并在源空间下裁剪。
* 变换从右往左应用：**先在内容空间翻转，再旋转落位**（`PanelView` 的
  `rotate(r) scale(±1, ±1)`）。翻转绕内容框中心；`flip_h` 镜像 x、`flip_v` 镜像 y。
* `rotate_cw_deg` 是**顺时针**（CSS 语义；y 向上的 PDF 空间里取负角），绕目标框中心。90° 的奇数倍
  时内容框的宽高对调（`PanelView` 的 `rotationSwaps`），使旋转后**填满**目标框——这正是旧
  `show_pdf_page(rotate=…)` / `insert_image(rotate=…)`「只有 90 的倍数会填满目标矩形」那句话的
  正面表述。非 90 倍数的角度不在面板的合同里（`plan._panel_node` 先四舍五入到 90 的倍数）。
* 源页自己的 `/Rotate` 与 `/UserUnit` **不在这里**：它们已经折进 Form XObject 的 /Matrix（qpdf
  `as_form_xobject(handle_transformations=True)`），本模块只看映射后的可见框——所以它们恰好被
  应用一次（RC-039 的 must_fail：重复应用旋转）。

纯标准库。几何期望在测试里**手算**（`tests/test_rendercore_placement.py`），不调本模块反推。
"""

from __future__ import annotations

from dataclasses import dataclass

from .ir import Matrix, apply, compose, rotate_ccw, scale, translate

Rect = tuple[float, float, float, float]  # x, y, w, h（y 向上）


@dataclass(frozen=True)
class Placement:
    """写入器要落笔的三样：`cm` 矩阵、源空间里的裁剪矩形、目标空间里的包围盒。"""

    #: 源可见空间 → 页面（或父组）空间。
    matrix: Matrix
    #: 源可见空间里要裁的矩形（`re W n` 的四个数）；没有 crop 时就是整个可见框。
    clip: Rect
    #: `clip` 的四角经 `matrix` 映射后的包围盒（透明组 form 的 BBox 用）。
    bbox: tuple[float, float, float, float]


def visible_box(bbox: tuple[float, float, float, float], matrix: Matrix | None) -> Rect:
    """Form XObject 的 BBox 经它自己的 /Matrix 映射后的包围盒（x, y, w, h）。

    qpdf 把源页的 /Rotate 与 /UserUnit 写进 /Matrix：BBox [15 10 285 170] + Rotate 90 + UserUnit 2
    → Matrix [0 -2 2 0 0 540]，可见框 320 × 540。没有 /Matrix 就是 BBox 自己。
    """
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if matrix is None:
        return (x0, y0, x1 - x0, y1 - y0)
    pts = [apply(matrix, x, y) for x in (x0, x1) for y in (y0, y1)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def rotation_swaps(rotate_cw_deg: float) -> bool:
    """90 的奇数倍：内容框宽高对调（与前端 `rotationSwaps` 同一判据）。"""
    return int(round(rotate_cw_deg / 90.0)) % 2 == 1


def place(
    source: Rect,
    rect: Rect,
    *,
    crop: tuple[float, float, float, float] | None = None,
    rotate_cw_deg: float = 0.0,
    flip_h: bool = False,
    flip_v: bool = False,
) -> Placement:
    """把源可见框（或它的 crop 子块）落进目标框。

    `source` / `rect` 都是 (x, y, w, h)、y 向上。顺序：crop → 缩放到内容框（旋转对调宽高）→
    绕中心翻转 → 绕中心顺时针旋转 → 平移到目标框中心。
    """
    sx, sy, sw, sh = (float(v) for v in source)
    if crop is not None:
        cx, cy, cw, ch = (float(v) for v in crop)
        # 顶原点的归一化 crop → y 向上的源空间：上边 = 可见框顶 − cy·高
        top = sy + sh - cy * sh
        sx, sw = sx + cx * sw, cw * sw
        sh = ch * sh
        sy = top - sh
    tx, ty, tw, th = (float(v) for v in rect)
    content_w, content_h = (th, tw) if rotation_swaps(rotate_cw_deg) else (tw, th)
    m = translate(-(sx + sw / 2.0), -(sy + sh / 2.0))
    m = compose(m, scale(content_w / sw, content_h / sh))
    if flip_h or flip_v:
        m = compose(m, scale(-1.0 if flip_h else 1.0, -1.0 if flip_v else 1.0))
    deg = float(rotate_cw_deg) % 360.0
    if deg:
        m = compose(m, rotate_ccw(-deg))
    m = compose(m, translate(tx + tw / 2.0, ty + th / 2.0))
    clip: Rect = (sx, sy, sw, sh)
    corners = [apply(m, sx + dx, sy + dy) for dx in (0.0, sw) for dy in (0.0, sh)]
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    return Placement(matrix=m, clip=clip, bbox=(min(xs), min(ys), max(xs), max(ys)))


__all__ = ["Placement", "Rect", "place", "rotation_swaps", "visible_box"]
