"""PDF 后端的 PyMuPDF 实现——**全仓库唯一 import pymupdf 的模块**。

这条边界是有意维持的：PyMuPDF 是 AGPL-3.0，它的传染范围决定了整个发行版的
许可证。要换后端（进而改变许可证选项），照 `pdfbackend/__init__.py` 里的契约
新写一个模块即可，`app.py` 无需改动。改动本文件时不要把 pymupdf 对象泄漏到
返回值里——除了 `Canvas`，它本身就是「当前后端的画布」这一概念。

几何公式与前端严格同源：`_dash_pattern` ↔ `shapeGeometry.dashArray`、
`_polygon_points` ↔ `shapeGeometry` 的正多边形、`_draw_shape` 的 brace ↔
`bracePath`、`_draw_arrow` ↔ `ArrowView`、`_draw_shape` 的 line 端点 ↔
`ShapeView` + `types/document.lineEndpoints`、`_draw_text` 换行 ↔ `TextView`。
改一边必须同步另一边，pytest 用 get_drawings() 做几何级看护。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import pymupdf

from .. import richtext

BACKEND_NAME = "pymupdf"


# ---------------------------------------------------------------------------
# 单位与颜色（与后端无关的纯换算，实现模块之间可直接复用）
# ---------------------------------------------------------------------------
def mm2pt(mm: float) -> float:
    return mm * 72.0 / 25.4


def hex2rgb(s: str) -> tuple[float, float, float]:
    s = (s or "#000000").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 字体与文本度量
# ---------------------------------------------------------------------------
_FONT_CACHE: dict[str, pymupdf.Font] = {}


def get_font(name: str) -> pymupdf.Font:
    if name not in _FONT_CACHE:
        _FONT_CACHE[name] = pymupdf.Font(name)
    return _FONT_CACHE[name]


def latin_font(bold: bool, italic: bool) -> pymupdf.Font:
    return get_font({
        (False, False): "times-roman",
        (True, False): "times-bold",
        (False, True): "times-italic",
        (True, True): "times-bolditalic",
    }[(bool(bold), bool(italic))])


def _script_runs(s: str) -> list[list]:
    """按 CJK / 拉丁切分连续段：[[is_cjk, seg], ...]"""
    out: list[list] = []
    for ch in s:
        cjk = ord(ch) > 0x2E80
        if out and out[-1][0] == cjk:
            out[-1][1] += ch
        else:
            out.append([cjk, ch])
    return out


def _mixed_width(s: str, latin: pymupdf.Font, cjk: pymupdf.Font, size: float) -> float:
    return sum((cjk if c else latin).text_length(seg, size) for c, seg in _script_runs(s))


def text_width(s: str, size_pt: float, bold: bool = False, italic: bool = False) -> float:
    """中英混排字符串宽度（pt）——边界层对外的度量接口。"""
    return _mixed_width(s, latin_font(bold, italic), get_font("china-ss"), size_pt)


# ---------------------------------------------------------------------------
# 素材探测与预览栅格化
# ---------------------------------------------------------------------------
def probe_asset(path: Path, kind: str) -> dict:
    """读素材原始尺寸。kind="pdf" 回 pt 尺寸，kind="raster" 回像素尺寸。
    读不出来就抛异常，由调用方决定跳过。"""
    if kind == "pdf":
        with pymupdf.open(path) as doc:
            r = doc[0].rect
        return {"kind": "pdf", "w_pt": r.width, "h_pt": r.height}
    pix = pymupdf.Pixmap(str(path))
    return {"kind": "raster", "px_w": pix.width, "px_h": pix.height}


def render_preview_png(path: Path, width_px: int, out: Path) -> None:
    """把矢量面板首页渲染成指定像素宽度的 PNG（画布显示与缩略图用）。"""
    with pymupdf.open(path) as doc:
        page = doc[0]
        zoom = width_px / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out))


# ---------------------------------------------------------------------------
# 几何辅助
# ---------------------------------------------------------------------------
def _obj_rect(o: dict) -> pymupdf.Rect:
    return pymupdf.Rect(
        mm2pt(o["x_mm"]), mm2pt(o["y_mm"]),
        mm2pt(o["x_mm"] + o["w_mm"]), mm2pt(o["y_mm"] + o["h_mm"]))


def _crop_clip(src_rect: pymupdf.Rect, crop: dict | None) -> pymupdf.Rect | None:
    """归一化 crop（0-1、top-origin）→ 源页坐标 clip 矩形。"""
    if not crop:
        return None
    return pymupdf.Rect(
        src_rect.x0 + float(crop["x"]) * src_rect.width,
        src_rect.y0 + float(crop["y"]) * src_rect.height,
        src_rect.x0 + (float(crop["x"]) + float(crop["w"])) * src_rect.width,
        src_rect.y0 + (float(crop["y"]) + float(crop["h"])) * src_rect.height)


def _obj_morph(o: dict):
    """任意角度旋转（度，顺时针，绕包围盒中心）→ TextWriter/Shape 的 morph 参数。
    CSS rotate() 顺时针 = 页面坐标（y 向下）里 pymupdf.Matrix(deg) 的旋转方向。"""
    deg = float(o.get("rotation_deg") or 0) % 360
    if not deg:
        return None
    center = pymupdf.Point(mm2pt(o["x_mm"] + o["w_mm"] / 2),
                           mm2pt(o["y_mm"] + o["h_mm"] / 2))
    return center, pymupdf.Matrix(deg)


def _flip_pixmap_rows(pix: pymupdf.Pixmap) -> pymupdf.Pixmap:
    """垂直镜像：按行倒序重排 samples（水平镜像 = 垂直镜像 + 旋转 180°）。"""
    stride = pix.stride
    s = pix.samples
    flipped = b"".join(s[i * stride:(i + 1) * stride]
                       for i in range(pix.height - 1, -1, -1))
    return pymupdf.Pixmap(pix.colorspace, pix.width, pix.height, flipped,
                          bool(pix.alpha))


def _dash_pattern(dash: str | None, sw: float) -> str | None:
    """虚线间距按线宽比例换算；与前端 shapeGeometry.dashArray 同一比例。"""
    if dash == "dashed":
        return f"[{sw * 4:.3f} {sw * 2.5:.3f}] 0"
    if dash == "dotted":
        return f"[{max(sw * 0.01, 0.01):.3f} {sw * 2:.3f}] 0"
    return None


def _arrow_heads(o: dict) -> tuple[str, str]:
    """新旧端型统一：head_start/head_end 优先，缺失按旧 head 推导三角头。"""
    hs, he = o.get("head_start"), o.get("head_end")
    if hs is not None or he is not None:
        return str(hs or "none"), str(he or "none")
    legacy = o.get("head", "end")
    return ("triangle" if legacy == "both" else "none",
            "triangle" if legacy in ("end", "both") else "none")


def _polygon_points(sides: int, w: float, h: float, inset: float) -> list:
    """正 N 边形顶点（内切包围盒，顶点朝上）；与前端 shapeGeometry 同一公式。"""
    n = max(3, min(12, int(round(sides))))
    rx, ry = max(w / 2 - inset, 0.001), max(h / 2 - inset, 0.001)
    return [(w / 2 + rx * math.cos(-math.pi / 2 + i * 2 * math.pi / n),
             h / 2 + ry * math.sin(-math.pi / 2 + i * 2 * math.pi / n))
            for i in range(n)]


# ---------------------------------------------------------------------------
# 对象绘制
# ---------------------------------------------------------------------------
def _place_panel(page: pymupdf.Page, o: dict, dpi: int, path: Path) -> None:
    """面板合成：PDF 真矢量 show_pdf_page；位图带 crop/rotation 时经 convert_to_pdf
    取得 clip/rotate 能力。rotation 限 90° 倍数（非 90 倍数时 show_pdf_page 不填满
    目标矩形，无法与画布语义一致）；opacity<1 或 flip 时该面板以 dpi 分辨率位图
    嵌入（PDF 矢量 xobject 无整体 alpha、show_pdf_page 无镜像，属明示的保真取舍）。

    path 由调用方解析——带 override 的面板要先经引擎重渲染，那是项目层的事。"""
    rect = _obj_rect(o)
    crop = o.get("crop")
    rotation = int(round(float(o.get("rotation") or 0) / 90.0)) * 90 % 360
    opacity = o.get("opacity")
    opacity = 1.0 if opacity is None else max(0.0, min(1.0, float(opacity)))
    flip_h = bool(o.get("flip_h"))
    flip_v = bool(o.get("flip_v"))
    needs_bitmap = opacity < 1.0 or flip_h or flip_v

    is_pdf = path.suffix.lower() == ".pdf"
    if not needs_bitmap and not is_pdf and not crop and not rotation:
        page.insert_image(rect, filename=str(path))  # 快路径：普通位图直贴
        return

    if is_pdf:
        src = pymupdf.open(path)
    else:
        with pymupdf.open(path) as img:
            src = pymupdf.open("pdf", img.convert_to_pdf())
    try:
        clip = _crop_clip(src[0].rect, crop)
        if not needs_bitmap:
            page.show_pdf_page(rect, src, 0, clip=clip, rotate=-rotation)
            return
        # 位图路径：翻转 / 半透明按导出 DPI 渲染后嵌入
        zoom = max(1.0, dpi / 72.0)
        pix = src[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        # 内容空间先翻转后旋转（与前端 CSS transform 语义一致）：
        # flipH = 行倒序 + 转 180°，flipH+flipV = 只转 180°
        extra_rot = 0
        if flip_h and flip_v:
            extra_rot = 180
        elif flip_h:
            pix = _flip_pixmap_rows(pix)
            extra_rot = 180
        elif flip_v:
            pix = _flip_pixmap_rows(pix)
        if opacity < 1.0:
            pix = pymupdf.Pixmap(pix, 1)  # 加 alpha 通道
            pix.set_alpha(bytes([int(round(255 * opacity))]) * (pix.width * pix.height))
        page.insert_image(rect, pixmap=pix, rotate=(-(rotation + extra_rot)) % 360)
    finally:
        src.close()


def _draw_text(page: pymupdf.Page, t: dict) -> None:
    """中英混排分段书写：拉丁走 Times、CJK 走宋体。整框套 CJK 字体会把
    拉丁字母排成全角步进（"E x p o r t"），必须按 script 切段各用各的字体。
    行高 line_height（缺省 1.25）、按框宽贪心换行（CJK 逐字、拉丁按词，
    单词自己就超宽时逐字兜底），与前端 TextView 一致；
    背景/描边/内边距/下划线/旋转同前端语义。"""
    text = t.get("text", "")
    if not text.strip():
        return
    size = float(t.get("size_pt", 9))
    line_h = float(t.get("line_height") or 1.25)
    pad = mm2pt(float(t.get("padding_mm") or 0))
    latin = latin_font(t.get("bold", False), t.get("italic", False))
    cjk = get_font("china-ss")
    x0, y0 = mm2pt(t["x_mm"]) + pad, mm2pt(t["y_mm"]) + pad
    box_w = mm2pt(t["w_mm"]) - 2 * pad
    align = t.get("align", "left")
    morph = _obj_morph(t)

    # 背景 / 描边先落（矢量矩形），文字压在上面
    bg = t.get("bg")
    border = t.get("border_color")
    if bg or border:
        shape = page.new_shape()
        shape.draw_rect(_obj_rect(t))
        shape.finish(color=hex2rgb(border) if border else None,
                     fill=hex2rgb(bg) if bg else None,
                     width=max(float(t.get("border_pt") or 0.75), 0.05)
                     if border else 1,
                     morph=morph)
        shape.commit()

    # 行内标记（上标 ^{…} / 下标 _{…}）先解析成片段，换行与书写都按片段走：
    # 上下标字号只有正文的 62%，把它当正文宽度算会提前折行。
    def _unit_w(u: list[tuple[str, str]]) -> float:
        return sum(_mixed_width(seg, latin, cjk, richtext.run_metrics(sc, size)[0])
                   for seg, sc in u)

    def _rstrip(u: list[tuple[str, str]]) -> list[tuple[str, str]]:
        out = list(u)
        while out and not out[-1][0].rstrip():
            out.pop()
        if out:
            out[-1] = (out[-1][0].rstrip(), out[-1][1])
        return out

    def _chars_of(u: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
        """把一个换行单元拆成逐字符的单元；script 标记跟着每个字符走
        （上下标段拆开后仍要按各自字号量宽、按各自基线书写）。"""
        return [[(ch, sc)] for seg, sc in u for ch in seg]

    lines: list[list[tuple[str, str]]] = []
    for raw in text.split("\n"):
        units: list[list[tuple[str, str]]] = []   # 每个换行单元 = 片段序列
        cur: list[tuple[str, str]] = []

        def _push_char(ch: str, sc: str) -> None:
            if cur and cur[-1][1] == sc:
                cur[-1] = (cur[-1][0] + ch, sc)
            else:
                cur.append((ch, sc))

        for run in richtext.parse_runs(raw):
            for ch in run.text:
                if ord(ch) > 0x2E80:      # 换行单元：CJK 逐字
                    if cur:
                        units.append(cur)
                        cur = []
                    units.append([(ch, run.script)])
                else:                      # 拉丁按词（空格附着前词）
                    _push_char(ch, run.script)
                    if ch == " ":
                        units.append(cur)
                        cur = []
        if cur:
            units.append(cur)

        line: list[tuple[str, str]] = []
        for u in units:
            # 单个 unit 自己就超宽（无空格的长化学式 / DOI / URL / 驼峰变量名）：
            # 先收掉当前行让它独占新行，仍放不下就逐字符断——不兜这一刀的话整个词
            # 会横向冲出文本框（实测越界 151pt）。与前端 TextView 的
            # word-break:break-word 同义：只有一个词独占一行都放不下才词内断开。
            if len(_chars_of(u)) > 1 and _unit_w(_rstrip(u)) > box_w:
                if line:
                    lines.append(_rstrip(line))
                    line = []
                for cu in _chars_of(u):
                    cand = line + cu
                    if line and _unit_w(_rstrip(cand)) > box_w:
                        lines.append(_rstrip(line))
                        line = list(cu)
                    else:
                        line = cand
                continue
            cand = line + u
            if line and _unit_w(_rstrip(cand)) > box_w:
                lines.append(_rstrip(line))
                line = list(u)
            else:
                line = cand
        lines.append(_rstrip(line))

    # CSS 行盒基线：半行距 + ascent（与 TextView 的 line-height 对齐）
    asc, desc = latin.ascender, latin.descender
    baseline0 = y0 + size * ((line_h - (asc - desc)) / 2 + asc)
    tw = pymupdf.TextWriter(page.rect, color=hex2rgb(t.get("color", "#000000")))
    underlines: list[tuple[float, float, float]] = []  # (x, y, width)
    for i, line in enumerate(lines):
        if not line:
            continue
        w = _unit_w(line)
        x = (x0 + (box_w - w) / 2 if align == "center"
             else x0 + box_w - w if align == "right" else x0)
        y = baseline0 + i * size * line_h
        if t.get("underline"):
            # 下划线始终画在正文基线上：上下标不该把线拉出锯齿
            underlines.append((x, y + size * 0.11, w))
        for seg_text, seg_script in line:
            seg_size, rise = richtext.run_metrics(seg_script, size)
            for is_cjk, sub in _script_runs(seg_text):
                f = cjk if is_cjk else latin
                tw.append((x, y - rise), sub, font=f, fontsize=seg_size)
                x += f.text_length(sub, seg_size)
    tw.write_text(page, morph=morph)
    if underlines:
        shape = page.new_shape()
        for x, y, w in underlines:
            shape.draw_line(pymupdf.Point(x, y), pymupdf.Point(x + w, y))
        shape.finish(color=hex2rgb(t.get("color", "#000000")),
                     width=max(size * 0.06, 0.3), morph=morph)
        shape.commit()


def _draw_arrow(page: pymupdf.Page, o: dict) -> None:
    """逐点复刻前端 ArrowView 几何：帽长 4×线宽、帽半宽 1.7×线宽、
    仅 triangle 端线段回缩 0.75×帽长、圆线帽；端型独立 + 虚线 + 旋转。"""
    x, y = mm2pt(o["x_mm"]), mm2pt(o["y_mm"])
    w, h = mm2pt(o["w_mm"]), mm2pt(o["h_mm"])
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    color = hex2rgb(o.get("color", "#000000"))
    morph = _obj_morph(o)
    ax, ay = x + float(o["start"]["rx"]) * w, y + float(o["start"]["ry"]) * h
    bx, by = x + float(o["end"]["rx"]) * w, y + float(o["end"]["ry"]) * h
    dx, dy = bx - ax, by - ay
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    nx, ny = -uy, ux
    head_len, head_half = sw * 4.0, sw * 1.7
    hs, he = _arrow_heads(o)
    trim = head_len * 0.75
    p1 = pymupdf.Point(ax + (ux * trim if hs == "triangle" else 0),
                       ay + (uy * trim if hs == "triangle" else 0))
    p2 = pymupdf.Point(bx - (ux * trim if he == "triangle" else 0),
                       by - (uy * trim if he == "triangle" else 0))

    shape = page.new_shape()
    shape.draw_line(p1, p2)
    shape.finish(color=color, width=sw, lineCap=1,
                 dashes=_dash_pattern(o.get("dash"), sw), morph=morph)
    for tip_x, tip_y, sign, kind in ((bx, by, 1.0, he), (ax, ay, -1.0, hs)):
        if kind == "none":
            continue
        base_x, base_y = tip_x - sign * ux * head_len, tip_y - sign * uy * head_len
        wing1 = pymupdf.Point(base_x + nx * head_half, base_y + ny * head_half)
        wing2 = pymupdf.Point(base_x - nx * head_half, base_y - ny * head_half)
        if kind == "triangle":
            shape.draw_polyline([pymupdf.Point(tip_x, tip_y), wing1, wing2])
            shape.finish(color=None, fill=color, closePath=True, morph=morph)
        elif kind == "open":
            shape.draw_polyline([wing1, pymupdf.Point(tip_x, tip_y), wing2])
            shape.finish(color=color, width=sw, lineCap=1, lineJoin=1, morph=morph)
        elif kind == "bar":
            shape.draw_line(pymupdf.Point(tip_x + nx * head_half, tip_y + ny * head_half),
                            pymupdf.Point(tip_x - nx * head_half, tip_y - ny * head_half))
            shape.finish(color=color, width=sw, lineCap=1, morph=morph)
    shape.commit()


def _draw_shape(page: pymupdf.Page, o: dict) -> None:
    """rect/ellipse 描边居中内缩半线宽（外沿贴合包围盒），line 画 start→end 端点连线
    （缺省即包围盒水平中线）；
    triangle/diamond/polygon/brace/圆角/虚线/填充透明度/旋转与前端 ShapeView 逐点一致。"""
    x, y = mm2pt(o["x_mm"]), mm2pt(o["y_mm"])
    w, h = mm2pt(o["w_mm"]), mm2pt(o["h_mm"])
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    color = hex2rgb(o.get("color", "#000000"))
    fill = hex2rgb(o["fill"]) if o.get("fill") else None
    fill_opacity = float(o.get("fill_opacity") or 1.0) if fill else 1.0
    dashes = _dash_pattern(o.get("dash"), sw)
    morph = _obj_morph(o)
    inset = sw / 2
    kind = o.get("shape")
    shape = page.new_shape()
    filled = dict(color=color, fill=fill, width=sw, dashes=dashes,
                  fill_opacity=fill_opacity, lineJoin=1, morph=morph)
    if kind == "rect":
        radius_mm = float(o.get("corner_radius_mm") or 0)
        rect = pymupdf.Rect(x + inset, y + inset,
                            x + max(w - inset, inset), y + max(h - inset, inset))
        if radius_mm > 0:
            # radius 参数是相对短边的比例
            frac = min(mm2pt(radius_mm) / max(min(rect.width, rect.height), 0.001), 0.5)
            shape.draw_rect(rect, radius=frac)
        else:
            shape.draw_rect(rect)
        shape.finish(**filled)
    elif kind == "ellipse":
        shape.draw_oval(pymupdf.Rect(x + inset, y + inset,
                                     x + max(w - inset, inset), y + max(h - inset, inset)))
        shape.finish(**filled)
    elif kind == "triangle":
        shape.draw_polyline([pymupdf.Point(x + w / 2, y + inset),
                             pymupdf.Point(x + w - inset, y + h - inset),
                             pymupdf.Point(x + inset, y + h - inset)])
        shape.finish(**filled, closePath=True)
    elif kind == "diamond":
        shape.draw_polyline([pymupdf.Point(x + w / 2, y + inset),
                             pymupdf.Point(x + w - inset, y + h / 2),
                             pymupdf.Point(x + w / 2, y + h - inset),
                             pymupdf.Point(x + inset, y + h / 2)])
        shape.finish(**filled, closePath=True)
    elif kind == "polygon":
        pts = _polygon_points(int(o.get("sides") or 6), w, h, inset)
        shape.draw_polyline([pymupdf.Point(x + px, y + py) for px, py in pts])
        shape.finish(**filled, closePath=True)
    elif kind == "brace":
        # 大括号「{」：与前端 bracePath 同一构造（二次贝塞尔）
        cx = w / 2
        tip_gap = min(h * 0.06, h / 2 - inset)
        pt = lambda px, py: pymupdf.Point(x + px, y + py)  # noqa: E731
        shape.draw_curve(pt(w - inset, inset), pt(cx, inset), pt(cx, h * 0.25))
        shape.draw_line(pt(cx, h * 0.25), pt(cx, h / 2 - tip_gap))
        shape.draw_curve(pt(cx, h / 2 - tip_gap), pt(cx, h / 2), pt(inset, h / 2))
        shape.draw_curve(pt(inset, h / 2), pt(cx, h / 2), pt(cx, h / 2 + tip_gap))
        shape.draw_line(pt(cx, h / 2 + tip_gap), pt(cx, h * 0.75))
        shape.draw_curve(pt(cx, h * 0.75), pt(cx, h - inset), pt(w - inset, h - inset))
        shape.finish(color=color, width=sw, lineCap=1, dashes=dashes, morph=morph)
    else:  # line
        # 端点与 _draw_arrow 同一套算法：包围盒比例坐标 → 绝对 pt。
        # 缺省 (0,0.5)→(1,0.5) 即包围盒水平中线，兜住没有 start/end 的旧布局文件
        # （前端同源缺省在 types/document.lineEndpoints）。
        s = o.get("start") or {"rx": 0.0, "ry": 0.5}
        e = o.get("end") or {"rx": 1.0, "ry": 0.5}
        shape.draw_line(
            pymupdf.Point(x + float(s["rx"]) * w, y + float(s["ry"]) * h),
            pymupdf.Point(x + float(e["rx"]) * w, y + float(e["ry"]) * h),
        )
        shape.finish(color=color, width=sw, lineCap=1, dashes=dashes, morph=morph)
    shape.commit()


# ---------------------------------------------------------------------------
# 合成画布
# ---------------------------------------------------------------------------
class Canvas:
    """一页白底合成画布。用法：

        with compose(page_w_mm, page_h_mm) as canvas:
            for o in objects:
                canvas.place(o, dpi=dpi, resolve_panel=resolve)
            canvas.save_pdf(path)      # 真矢量
            canvas.save_png(path, dpi) # 由同一页渲染，保证两份完全一致
    """

    def __init__(self, page_w_mm: float, page_h_mm: float):
        self._doc = pymupdf.open()
        self._page = self._doc.new_page(width=mm2pt(page_w_mm), height=mm2pt(page_h_mm))
        self._page.draw_rect(self._page.rect, color=None, fill=(1, 1, 1))  # 白底

    def place(self, o: dict, dpi: int,
              resolve_panel: Callable[[dict, int], Path]) -> None:
        """按对象类型落一个元素。panel 的源文件路径由 resolve_panel 回调给出
        （项目路径解析与引擎重渲染留在调用方，后端只管画）。"""
        kind = o.get("type")
        if kind == "panel":
            _place_panel(self._page, o, dpi, resolve_panel(o, dpi))
        elif kind == "text":
            _draw_text(self._page, o)
        elif kind == "arrow":
            _draw_arrow(self._page, o)
        elif kind == "shape":
            _draw_shape(self._page, o)

    def save_pdf(self, path: Path) -> None:
        self._doc.save(str(path), deflate=True)

    def save_png(self, path: Path, dpi: int) -> None:
        zoom = dpi / 72.0
        pix = self._page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pix.save(str(path))

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> "Canvas":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def compose(page_w_mm: float, page_h_mm: float) -> Canvas:
    return Canvas(page_w_mm, page_h_mm)
