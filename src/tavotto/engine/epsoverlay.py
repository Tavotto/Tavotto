"""外层 EPS：把 worker 的 matplotlib 原图 EPS **原样嵌入**，再叠 RenderCore 标注的 PostScript（QA-WIN-20）。

结构（Adobe EPSF 嵌入惯例）::

    %!PS-Adobe-3.0 EPSF-3.0 + DSC 头（BoundingBox = 图幅）
    gsave <平移到嵌入 EPS 的包围盒左下角> <裁到包围盒>
    BeginEPSF（保存 VM / 字典栈 / 操作数栈，/showpage {} def，恢复默认图形状态）
    %%BeginDocument: <name> … 原图 EPS 原样 … %%EndDocument
    EndEPSF（弹回操作数栈 / 字典栈，restore）  grestore
    标注叠加层（`rendercore/epswriter.overlay_ps`，IR 页面空间 = 图幅，原点左下、pt）
    showpage  %%EOF

纯标准库；产物是真矢量（文字是字形轮廓路径），没有位图运算符、没有任何外部程序参与。
"""

from __future__ import annotations

import math
import re

_BBOX = re.compile(rb"^%%BoundingBox:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", re.M)
_HIRES = re.compile(
    rb"^%%HiResBoundingBox:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)", re.M
)
_HEAD = 8192

BEGIN_EPSF = """\
/tvt_state save def
/tvt_dicts countdictstack def
/tvt_ops count 1 sub def
userdict begin
/showpage {} def
0 setgray 0 setlinecap 1 setlinewidth 0 setlinejoin 10 setmiterlimit [] 0 setdash newpath
/languagelevel where {pop languagelevel 1 ne {false setstrokeadjust false setoverprint} if} if
"""

END_EPSF = """\
count tvt_ops sub {pop} repeat
countdictstack tvt_dicts sub {end} repeat
tvt_state restore
"""


class EpsOverlayError(ValueError):
    pass


def embedded_bbox(eps: bytes) -> tuple[int, int, int, int]:
    m = _BBOX.search(eps[:_HEAD]) or _BBOX.search(eps)
    if not m:
        raise EpsOverlayError("embedded EPS has no %%BoundingBox")
    x0, y0, x1, y1 = (int(v) for v in m.groups())
    if x1 <= x0 or y1 <= y0:
        raise EpsOverlayError("embedded EPS has a degenerate %%BoundingBox")
    return x0, y0, x1, y1


def embedded_hires_bbox(eps: bytes) -> tuple[float, float, float, float]:
    """内层 EPS 的精确包围盒：优先 `%%HiResBoundingBox`（matplotlib 会写），没有才退回整数 `%%BoundingBox`。"""
    m = _HIRES.search(eps[:_HEAD]) or _HIRES.search(eps)
    if m:
        try:
            x0, y0, x1, y1 = (float(v) for v in m.groups())
        except ValueError:
            x0 = y0 = x1 = y1 = 0.0
        if x1 > x0 and y1 > y0:
            return x0, y0, x1, y1
    return tuple(float(v) for v in embedded_bbox(eps))  # type: ignore[return-value]


def _n(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def compose(
    inner: bytes,
    overlay: str,
    width_pt: float,
    height_pt: float,
    *,
    underlay: str = "",
    name: str = "figure.eps",
) -> bytes:
    """原图 EPS + 叠加层 → 外层 EPS 字节。外层 BoundingBox = 图幅（向上取整），原图按其包围盒左下角对齐图幅左下角。
    `underlay` = 图层树里放在图**下面**的标注，写在嵌入段之前；`overlay` 在嵌入段之后（图上面）。"""
    if not inner.startswith(b"%!PS-Adobe"):
        raise EpsOverlayError("embedded file is not a DSC EPS")
    embedded_bbox(inner)  # 没有可用的包围盒就拒绝
    x0, y0, ix1, iy1 = embedded_hires_bbox(inner)
    w_i, h_i = math.ceil(width_pt - 1e-6), math.ceil(height_pt - 1e-6)
    head = (
        "%!PS-Adobe-3.0 EPSF-3.0\n"
        f"%%BoundingBox: 0 0 {w_i} {h_i}\n"
        f"%%HiResBoundingBox: 0 0 {width_pt:.6f} {height_pt:.6f}\n"
        "%%Creator: Tavotto\n%%Pages: 1\n%%LanguageLevel: 2\n%%EndComments\n"
        "%%BeginProlog\n%%EndProlog\n%%Page: 1 1\n"
    )
    place = (
        f"gsave {_n(-x0)} {_n(-y0)} translate newpath "
        f"{_n(x0)} {_n(y0)} {_n(ix1 - x0)} {_n(iy1 - y0)} rectclip\n"
    )
    body = inner if inner.endswith(b"\n") else inner + b"\n"
    out = [
        head.encode("ascii"),
        underlay.encode("ascii"),
        place.encode("ascii"),
        BEGIN_EPSF.encode("ascii"),
        f"%%BeginDocument: {name}\n".encode("ascii"),
        body,
        b"%%EndDocument\n",
        END_EPSF.encode("ascii"),
        b"grestore\n",
        overlay.encode("ascii"),
        b"showpage\n%%EOF\n",
    ]
    return b"".join(out)
