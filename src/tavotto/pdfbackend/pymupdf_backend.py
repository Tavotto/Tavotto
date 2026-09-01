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
import shutil
from pathlib import Path
from typing import Callable

import pymupdf

from .. import glyphplan, richtext

BACKEND_NAME = "pymupdf"
#: 本后端实现的版本号。它属于契约层是因为**渲染缓存的身份需要它**：
#: 同一个 PDF 换一版渲染库，出来的像素可以不一样（字体回退、抗锯齿、色彩管理
#: 都在变）。不进缓存键的话，升级完 Tavotto 的用户会一直看着旧版本渲出来的
#: 预览，而磁盘上的图早就该重画了。读不到就交空串（缓存退化成只按内容分键，
#: 仍然正确）。
BACKEND_VERSION = str(getattr(pymupdf, "__version__", "") or "")


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


#: 画布文字（标注 / 自由文字）能选的字体族。**闭集，且只有这三个**：
#: Flask 进程里没有 matplotlib（见 `src/tavotto/AGENTS.md` 的进程边界），
#: 合成与写回只能用 PyMuPDF 自带的 base-14——它恰好覆盖这三个通用族。
#: 具体字体名（Times New Roman / Arial…）**不进这个集合**：它们要么得内嵌
#: 用户磁盘上的字体文件（另一件事），要么就是「界面上选得中、导出时悄悄
#: 换一个」——那正是本轮要消灭的那类静默替换。
#:
#: 严格同源：`web/src/lib/typography.ts` 的 `CANVAS_TEXT_FAMILIES`
#: （看护 `tests/test_typography_families.py`）。
CANVAS_TEXT_FAMILIES = ("serif", "sans-serif", "monospace")

#: 族 → base-14 的四个字形（常规 / 粗 / 斜 / 粗斜）。
_LATIN_FACES: dict[str, tuple[str, str, str, str]] = {
    "serif": ("times-roman", "times-bold", "times-italic", "times-bolditalic"),
    "sans-serif": ("helv", "hebo", "heit", "hebi"),
    "monospace": ("cour", "cobo", "coit", "cobi"),
}

#: 中日韩字形只有这一张脸。**实测（PyMuPDF 1.28.2）`china-ss` / `china-s` /
#: `china-ssb` / `china-sb` 四个别名回的是同一个 `Droid Sans Fallback
#: Regular`**——所以「衬线中文 / 无衬线中文」在这条路上不是一个真实的选择，
#: 换族只换拉丁那一半。旧注释写的「CJK 走宋体」是没有量过的断言。
_CJK_FACE = "china-ss"


def latin_family(name: object) -> str:
    """任意取值 → 闭集里的一个族。认不出来的一律回默认（衬线）。

    **不认得的名字不许当成「用户指定的字体」去解析**：那条路的终点是
    PyMuPDF 抛异常或悄悄给一张别的脸，两个结果都比「按默认画、并且界面
    根本不让你选到这里」更坏。
    """
    return name if name in _LATIN_FACES else CANVAS_TEXT_FAMILIES[0]


def latin_font(bold: bool, italic: bool, family: object = "serif") -> pymupdf.Font:
    faces = _LATIN_FACES[latin_family(family)]
    return get_font(faces[(1 if bold else 0) + (2 if italic else 0)])


def cjk_font() -> pymupdf.Font:
    """中日韩字形。**与族无关**（见 `_CJK_FACE`），所以它没有 family 形参
    ——多一个不起作用的形参等于多一句做不到的承诺。"""
    return get_font(_CJK_FACE)


#: 分层结果按 (拉丁脸, CJK 脸, 码位) 记忆。贪心换行会对同一批字符反复量宽，
#: 每次都去问 `has_glyph` 的话，一段 200 字的文本要问上万次。
_LAYER_CACHE: dict[tuple[str, str, int], str] = {}


def coverage(
    family: object = "serif", bold: bool = False, italic: bool = False
) -> glyphplan.Coverage:
    """这一族/字重下的三层覆盖 oracle——**问的是真字体**，不是任何一张抄下来的表。

    导出那一端永远走这条路；浏览器没有字体引擎，读的是
    `canvas_coverage.json`（`scripts/gen_canvas_coverage.py --check` 看护两者一致）。
    """
    return _coverage_of(latin_font(bold, italic, family), cjk_font())


def _coverage_of(latin: pymupdf.Font, cjk: pymupdf.Font) -> glyphplan.Coverage:
    return glyphplan.Coverage(
        primary=lambda cp: bool(latin.has_glyph(cp)),
        cjk=lambda cp: bool(cjk.has_glyph(cp)),
        # PyMuPDF 自己挑得出的那张脸（实测 1.28.2 上是 Noto Serif Regular，
        # 且**与请求的族和字重无关**——所以 fallback 层画出来的字必然与
        # 正文不同脸，这正是它要被单独报出来的原因）。
        fallback=lambda cp: bool(latin.has_glyph(cp, fallback=True)),
    )


def _plan(s: str, latin: pymupdf.Font, cjk: pymupdf.Font) -> list[glyphplan.GlyphRun]:
    cov = _coverage_of(latin, cjk)
    key0, key1 = latin.name, cjk.name
    runs: list[glyphplan.GlyphRun] = []
    for ch in s:
        cp = ord(ch)
        key = (key0, key1, cp)
        layer = _LAYER_CACHE.get(key)
        if layer is None:
            layer = _LAYER_CACHE[key] = glyphplan.layer_of(cp, cov)
        if runs and runs[-1].layer == layer:
            runs[-1] = glyphplan.GlyphRun(runs[-1].text + ch, layer)
        else:
            runs.append(glyphplan.GlyphRun(ch, layer))
    return runs


def _face_of(layer: str, latin: pymupdf.Font, cjk: pymupdf.Font) -> pymupdf.Font:
    """哪一层交给哪张脸落笔。

    `fallback` 与 `missing` 都仍然交给**正文那张脸**：PyMuPDF 会在 append 时
    自己挑回退脸（missing 那一档挑不到，画出来就是 .notdef 方框）。把它们
    改交给 CJK 脸会换掉既有文档里那些字符的字形——分层是为了说清楚谁画的，
    不是为了改画面。
    """
    return cjk if layer == "cjk" else latin


def _mixed_width(s: str, latin: pymupdf.Font, cjk: pymupdf.Font, size: float) -> float:
    """量宽与落笔**读同一份计划**：分段判据一旦有两份，换行位置就会和画出来的
    字对不上。"""
    return sum(
        _face_of(r.layer, latin, cjk).text_length(r.text, size) for r in _plan(s, latin, cjk)
    )


def text_plan(
    s: str,
    family: object = "serif",
    bold: bool = False,
    italic: bool = False,
) -> list[tuple[str, str]]:
    """边界层对外的字形归属计划：`[(片段, 层), ...]`（层见 `glyphplan.GLYPH_LAYERS`）。

    进出只有基本类型，不泄漏后端字体对象。
    """
    return [(r.text, r.layer) for r in _plan(s, latin_font(bold, italic, family), cjk_font())]


def missing_glyphs(
    s: str,
    family: object = "serif",
    bold: bool = False,
    italic: bool = False,
) -> list[str]:
    """这段文字里**画不出来**的字符（去重、保出现顺序）——预检的唯一依据。"""
    return glyphplan.missing_chars(s, coverage(family, bold, italic))


#: 覆盖表要枚举到哪。BMP + SMP（`0x30000`）之上是私用区与未分配平面，
#: 枚举它们只会让生成物变大而一个字符都救不回来。
COVERAGE_MAX_CP = 0x30000


def coverage_ranges() -> dict:
    """把**真字体**的三层覆盖压成区间表——`canvas_coverage.json` 的唯一产生者。

    前端读的那份生成物由 `scripts/gen_canvas_coverage.py` 从这里出，
    `--check` 比对它与当前后端是否还一致：PyMuPDF 换版本、换平台导致覆盖
    漂移时，红的是那一格，而不是某个用户的图上多出一个方框。
    """
    latin, cjk = latin_font(False, False, "serif"), cjk_font()
    cov = _coverage_of(latin, cjk)
    lo, hi = 0x20, COVERAGE_MAX_CP
    return {
        "primary": glyphplan.ranges_of(cov.primary, lo, hi),
        "cjk": glyphplan.ranges_of(cov.cjk, lo, hi),
        # 只登记**真的会走到第 3 步**的那一段：`layer_of()` 到第 3 步时已经
        # 排除了 primary、以及「CJK 段且 CJK 脸有」。按这个条件裁剪，表比
        # 原始覆盖小得多，而两侧的分层结果逐字符相同。
        #
        # **不许再多减一个 `cjk`**：`₂`（U+2082）在 CJK 脸里有、码位却在
        # CJK 段之外，第 2 步轮不到它。多减那一下会让前端把它判成 `cjk`，
        # 后端仍判 `fallback`——一个只在下标字符上发作的两侧分歧。
        "fallback": glyphplan.ranges_of(
            lambda cp: (
                not cov.primary(cp)
                and not (cp > glyphplan.CJK_START and cov.cjk(cp))
                and cov.fallback(cp)
            ),
            lo,
            hi,
        ),
    }


def text_width(
    s: str,
    size_pt: float,
    bold: bool = False,
    italic: bool = False,
    family: object = "serif",
) -> float:
    """中英混排字符串宽度（pt）——边界层对外的度量接口。

    `family` 必须与真正落笔时用的那个族一致：等宽族比衬线族宽得多，量宽用
    一个族、书写用另一个族的话，换行位置与画出来的字对不上。
    """
    return _mixed_width(s, latin_font(bold, italic, family), cjk_font(), size_pt)


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
    # `alpha` 是「这张位图带不带透明通道」，原图规格要报它（`engine/originalspec`）。
    # **物理密度不从这里取**：MuPDF 的 `xres` 对「没写 pHYs」与「写着 96 dpi」
    # 一律回 96，两个不同的答案被压成同一个值——那一维由 originalspec 自己
    # 按格式解析。
    return {
        "kind": "raster",
        "px_w": pix.width,
        "px_h": pix.height,
        "alpha": bool(pix.alpha),
    }


def render_preview_png(path: Path, width_px: int, out: Path) -> None:
    """把矢量面板首页渲染成指定像素宽度的 PNG（画布显示与缩略图用）。"""
    with pymupdf.open(path) as doc:
        page = doc[0]
        zoom = width_px / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out))


# ---------------------------------------------------------------------------
# 像素比较（写回的像素门用）
# ---------------------------------------------------------------------------
#: 噪声底噪：抗锯齿与 PNG 量化会让**完全相同的图形**出现 ±1~2 的逐像素抖动，
#: 不先扣掉它 changed_ratio 恒非零。取值与 `scripts/ci/pixelcompare.py` 相同。
PNG_NOISE_FLOOR = 3


def _rgba_pixels(path: Path) -> tuple[bytes, tuple[int, int]]:
    """PNG → (RGBA 交错字节序列, (宽, 高))。

    **不转灰度、不丢 alpha**：等亮度的两种颜色（红 ↔ 同 luma 的绿）在灰度上
    逐字节相同，透明度差异更是只活在 alpha 通道里——写回像素门要接住的恰恰
    是「画面确实不同」的一切形态，把色度与 alpha 折叠掉等于给这两类分歧留了
    一扇永远开着的门（PR #95 评审的 P1）。
    """
    pix = pymupdf.Pixmap(str(path))
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)  # 灰度/CMYK → RGB，alpha 保留
    if not pix.alpha:
        pix = pymupdf.Pixmap(pix, 1)  # 补一条全不透明的 alpha
    return pix.samples, (pix.width, pix.height)


def compare_png(baseline: Path, candidate: Path) -> dict:
    """两张 PNG 的差异指标；尺寸不同直接判为最大差异。

    判据结构与指标名与 `scripts/ci/pixelcompare.py` 同构（底噪 3，
    changed_pixel_ratio / mean_abs_diff / max_abs_diff 三指标），但**这里逐
    RGBA 通道比、每像素取通道最大差**，刻意比 CI 那份的灰度比较更严：CI 比的
    是跨进程渲染的整图回归，灰度足够且阈值好定；写回门比的是同环境下的同一张
    图，等亮度换色与纯 alpha 差异也必须被看见。两份实现在灰度等值图（r==g==b、
    全不透明）上逐指标一致，`tests/test_pixel_compare.py` 的对拍用例钉住这个
    交集（与 patchspec ↔ Rust、telemetry 客户端 ↔ 代理同一套纪律）。为什么
    允许第二份实现：那一份跑在 CI（numpy + Pillow），而这里跑在 Flask 父进程
    ——它的依赖边界是 flask + pymupdf，wheel 不带科学栈，也 import 不到
    scripts/。

    阈值不在这里——这里只出指标，判定归调用方（app.py 的写回像素门）。
    """
    a, size_a = _rgba_pixels(baseline)
    b, size_b = _rgba_pixels(candidate)
    if size_a != size_b:
        return {
            "ok": False,
            "reason": "size_mismatch",
            "baseline_size": list(size_a),
            "candidate_size": list(size_b),
            "changed_pixel_ratio": 1.0,
            "mean_abs_diff": 255.0,
            "max_abs_diff": 255,
        }
    total = size_a[0] * size_a[1]
    if a == b or not total:  # 快路径：逐字节相同
        return {
            "ok": True,
            "changed_pixel_ratio": 0.0,
            "mean_abs_diff": 0.0,
            "max_abs_diff": 0,
            "changed_pixels": 0,
            "total_pixels": total,
            "raw_mean_abs_diff": 0.0,
        }
    changed = 0
    signal_sum = 0
    raw_sum = 0
    max_d = 0
    va, vb = memoryview(a), memoryview(b)
    for off in range(0, total * 4, 4):
        d = 0
        for c in range(4):  # 每像素取 RGBA 四通道最大差
            x, y = va[off + c], vb[off + c]
            dc = x - y if x >= y else y - x
            if dc > d:
                d = dc
        if d:
            raw_sum += d
            if d > max_d:
                max_d = d
            if d > PNG_NOISE_FLOOR:
                changed += 1
                signal_sum += d
    return {
        "ok": True,
        "changed_pixel_ratio": round(changed / total, 6),
        "mean_abs_diff": round(signal_sum / total, 4),
        "max_abs_diff": max_d,
        "changed_pixels": changed,
        "total_pixels": total,
        # 原始均值只作记录，不参与判定——排查时能看出「是不是整体偏了一点」
        "raw_mean_abs_diff": round(raw_sum / total, 4),
    }


# ---------------------------------------------------------------------------
# 几何辅助
# ---------------------------------------------------------------------------
def _obj_rect(o: dict) -> pymupdf.Rect:
    return pymupdf.Rect(
        mm2pt(o["x_mm"]),
        mm2pt(o["y_mm"]),
        mm2pt(o["x_mm"] + o["w_mm"]),
        mm2pt(o["y_mm"] + o["h_mm"]),
    )


def _crop_clip(src_rect: pymupdf.Rect, crop: dict | None) -> pymupdf.Rect | None:
    """归一化 crop（0-1、top-origin）→ 源页坐标 clip 矩形。"""
    if not crop:
        return None
    return pymupdf.Rect(
        src_rect.x0 + float(crop["x"]) * src_rect.width,
        src_rect.y0 + float(crop["y"]) * src_rect.height,
        src_rect.x0 + (float(crop["x"]) + float(crop["w"])) * src_rect.width,
        src_rect.y0 + (float(crop["y"]) + float(crop["h"])) * src_rect.height,
    )


def _obj_morph(o: dict):
    """任意角度旋转（度，顺时针，绕包围盒中心）→ TextWriter/Shape 的 morph 参数。

    实测结论（PyMuPDF 1.28.2，get_text/get_drawings 几何级验证）：Shape.finish 与
    TextWriter.write_text 都只把 fixpoint 换算进 PDF 坐标（y 向上），morph 矩阵本身
    原样作用在 y 向上空间——因此 Matrix(deg) 在页面上是**逆**时针，两条路径同向。
    CSS rotate(deg)（顺时针、y 向下）是权威语义，这里必须取负。"""
    deg = float(o.get("rotation_deg") or 0) % 360
    if not deg:
        return None
    center = pymupdf.Point(mm2pt(o["x_mm"] + o["w_mm"] / 2), mm2pt(o["y_mm"] + o["h_mm"] / 2))
    return center, pymupdf.Matrix(-deg)


def _flip_pixmap_rows(pix: pymupdf.Pixmap) -> pymupdf.Pixmap:
    """垂直镜像：按行倒序重排 samples（水平镜像 = 垂直镜像 + 旋转 180°）。"""
    stride = pix.stride
    s = pix.samples
    flipped = b"".join(s[i * stride : (i + 1) * stride] for i in range(pix.height - 1, -1, -1))
    return pymupdf.Pixmap(pix.colorspace, pix.width, pix.height, flipped, bool(pix.alpha))


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
    return (
        "triangle" if legacy == "both" else "none",
        "triangle" if legacy in ("end", "both") else "none",
    )


def _polygon_points(sides: int, w: float, h: float, inset: float) -> list:
    """正 N 边形顶点（内切包围盒，顶点朝上）；与前端 shapeGeometry 同一公式。"""
    n = max(3, min(12, int(round(sides))))
    rx, ry = max(w / 2 - inset, 0.001), max(h / 2 - inset, 0.001)
    return [
        (
            w / 2 + rx * math.cos(-math.pi / 2 + i * 2 * math.pi / n),
            h / 2 + ry * math.sin(-math.pi / 2 + i * 2 * math.pi / n),
        )
        for i in range(n)
    ]


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
    """中英混排分段书写：拉丁走 `font_family` 选中的那个 base-14 族
    （缺省衬线 = Times），CJK 走 PyMuPDF 自带的那一张 CJK 脸（见 `_CJK_FACE`
    ——换族不换它）。整框套 CJK 字体会把
    拉丁字母排成全角步进（"E x p o r t"），必须按 script 切段各用各的字体。
    行内 Unicode 上下标按 `richtext.interpret_runs` 受控解释（`interpretation`
    字段，缺省 auto）——**raw text 一个字符不改**，只改渲染表示。
    行高 line_height（缺省 1.25）、按框宽贪心换行（CJK 逐字、拉丁按词，
    单词自己就超宽时逐字兜底），与前端 TextView 一致；
    背景/描边/内边距/下划线/旋转同前端语义。"""
    text = t.get("text", "")
    if not text.strip():
        return
    size = float(t.get("size_pt", 9))
    line_h = float(t.get("line_height") or 1.25)
    pad = mm2pt(float(t.get("padding_mm") or 0))
    # `font_family` 缺席 = 老文档 / 没设过 = 继承默认（衬线）。**不许把
    # 「没设过」压成一个新的默认值再写回文档**——那两个是不同的答案。
    latin = latin_font(t.get("bold", False), t.get("italic", False), t.get("font_family"))
    cjk = cjk_font()
    # 受控科学文本解释（`interpretation` 缺席 = auto = 老文档也一样）。
    # auto 只救「不然就是方框」的那几个字符——合成会让 PDF 文本层里的 `⁵`
    # 变成 `5`，那笔账只有用户自己选 `scientific` 时才划得来。
    interp = str(t.get("interpretation") or richtext.DEFAULT_INTERPRETATION)
    _cov = _coverage_of(latin, cjk)
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
        shape.finish(
            color=hex2rgb(border) if border else None,
            fill=hex2rgb(bg) if bg else None,
            width=max(float(t.get("border_pt") or 0.75), 0.05) if border else 1,
            morph=morph,
        )
        shape.commit()

    # 行内标记（上标 ^{…} / 下标 _{…}）先解析成片段，换行与书写都按片段走：
    # 上下标字号只有正文的 62%，把它当正文宽度算会提前折行。
    def _unit_w(u: list[tuple[str, str]]) -> float:
        return sum(
            _mixed_width(seg, latin, cjk, richtext.run_metrics(sc, size)[0]) for seg, sc in u
        )

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
        units: list[list[tuple[str, str]]] = []  # 每个换行单元 = 片段序列
        cur: list[tuple[str, str]] = []

        def _push_char(ch: str, sc: str) -> None:
            if cur and cur[-1][1] == sc:
                cur[-1] = (cur[-1][0] + ch, sc)
            else:
                cur.append((ch, sc))

        for run in richtext.interpret_runs(
            richtext.parse_runs(raw),
            is_primary=_cov.primary,
            is_drawable=lambda cp: glyphplan.layer_of(cp, _cov) != "missing",
            mode=interp,
        ):
            for ch in run.text:
                if ord(ch) > 0x2E80:  # 换行单元：CJK 逐字
                    if cur:
                        units.append(cur)
                        cur = []
                    units.append([(ch, run.script)])
                else:  # 拉丁按词（空格附着前词）
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
        x = (
            x0 + (box_w - w) / 2
            if align == "center"
            else x0 + box_w - w
            if align == "right"
            else x0
        )
        y = baseline0 + i * size * line_h
        if t.get("underline"):
            # 下划线始终画在正文基线上：上下标不该把线拉出锯齿
            underlines.append((x, y + size * 0.11, w))
        for seg_text, seg_script in line:
            seg_size, rise = richtext.run_metrics(seg_script, size)
            for sub, layer in _plan(seg_text, latin, cjk):
                f = _face_of(layer, latin, cjk)
                tw.append((x, y - rise), sub, font=f, fontsize=seg_size)
                x += f.text_length(sub, seg_size)
    tw.write_text(page, morph=morph)
    if underlines:
        shape = page.new_shape()
        for x, y, w in underlines:
            shape.draw_line(pymupdf.Point(x, y), pymupdf.Point(x + w, y))
        shape.finish(
            color=hex2rgb(t.get("color", "#000000")), width=max(size * 0.06, 0.3), morph=morph
        )
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
    p1 = pymupdf.Point(
        ax + (ux * trim if hs == "triangle" else 0), ay + (uy * trim if hs == "triangle" else 0)
    )
    p2 = pymupdf.Point(
        bx - (ux * trim if he == "triangle" else 0), by - (uy * trim if he == "triangle" else 0)
    )

    shape = page.new_shape()
    shape.draw_line(p1, p2)
    shape.finish(
        color=color, width=sw, lineCap=1, dashes=_dash_pattern(o.get("dash"), sw), morph=morph
    )
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
            shape.draw_line(
                pymupdf.Point(tip_x + nx * head_half, tip_y + ny * head_half),
                pymupdf.Point(tip_x - nx * head_half, tip_y - ny * head_half),
            )
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
    # lineCap=1（圆帽）：dotted 的线段长只有 0.01×线宽，「点」全靠圆线帽画出来，
    # butt 帽下整圈描边不可见。与前端 ShapeView 的 strokeLinecap='round' 同源。
    filled = dict(
        color=color,
        fill=fill,
        width=sw,
        dashes=dashes,
        fill_opacity=fill_opacity,
        lineJoin=1,
        lineCap=1,
        morph=morph,
    )
    if kind == "rect":
        radius_mm = float(o.get("corner_radius_mm") or 0)
        rect = pymupdf.Rect(
            x + inset, y + inset, x + max(w - inset, inset), y + max(h - inset, inset)
        )
        if radius_mm > 0:
            # radius 参数是相对短边的比例
            frac = min(mm2pt(radius_mm) / max(min(rect.width, rect.height), 0.001), 0.5)
            shape.draw_rect(rect, radius=frac)
        else:
            shape.draw_rect(rect)
        shape.finish(**filled)
    elif kind == "ellipse":
        shape.draw_oval(
            pymupdf.Rect(x + inset, y + inset, x + max(w - inset, inset), y + max(h - inset, inset))
        )
        shape.finish(**filled)
    elif kind == "triangle":
        shape.draw_polyline(
            [
                pymupdf.Point(x + w / 2, y + inset),
                pymupdf.Point(x + w - inset, y + h - inset),
                pymupdf.Point(x + inset, y + h - inset),
            ]
        )
        shape.finish(**filled, closePath=True)
    elif kind == "diamond":
        shape.draw_polyline(
            [
                pymupdf.Point(x + w / 2, y + inset),
                pymupdf.Point(x + w - inset, y + h / 2),
                pymupdf.Point(x + w / 2, y + h - inset),
                pymupdf.Point(x + inset, y + h / 2),
            ]
        )
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
    """一页合成画布。用法：

    with compose(page_w_mm, page_h_mm) as canvas:
        for o in objects:
            canvas.place(o, dpi=dpi, resolve_panel=resolve)
        canvas.save_pdf(path)      # 真矢量
        canvas.save_png(path, dpi) # 由同一页渲染，保证两份完全一致

    `transparent=True` 时**不画白底矩形**，PNG 也带 alpha 通道。透明背景是
    位图才有的能力；PDF 上"透明"只是没有底色，两者都由这同一页出，所以两份
    产物的几何仍然逐点一致。
    """

    def __init__(self, page_w_mm: float, page_h_mm: float, transparent: bool = False):
        self._doc = pymupdf.open()
        self._page = self._doc.new_page(width=mm2pt(page_w_mm), height=mm2pt(page_h_mm))
        self._transparent = bool(transparent)
        if not self._transparent:
            self._page.draw_rect(self._page.rect, color=None, fill=(1, 1, 1))  # 白底

    def place(self, o: dict, dpi: int, resolve_panel: Callable[[dict, int], Path]) -> None:
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
        pix = self._page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=self._transparent)
        pix.save(str(path))

    @property
    def size_pt(self) -> tuple[float, float]:
        return (self._page.rect.width, self._page.rect.height)

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> "Canvas":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def compose(page_w_mm: float, page_h_mm: float, transparent: bool = False) -> Canvas:
    return Canvas(page_w_mm, page_h_mm, transparent)


# ---------------------------------------------------------------------------
# 按原图导出（scope=original，ADR 0031）
# ---------------------------------------------------------------------------
def original_pdf(src: Path, out: Path, page_pt: tuple[float, float] | None = None) -> dict:
    """把一张图**按它自己的尺寸**写成 PDF。

    * **矢量源**（PDF）：整页原样搬过去。不是重画，是 `insert_pdf`——页面尺寸、
      字形、路径、色彩空间一个字节没动，出来的仍然是真矢量。这正是
      `scope=original` 的全部意义：画布上的缩放/裁剪/旋转在这条路上根本没有
      出场机会。`page_pt` 对它没有意义（页面尺寸在文件里）。
    * **位图源**：新建一页，**页面尺寸由 `page_pt` 给定**，图整页铺满。这是
      "把位图装进 PDF 容器"，**不声称它变成了矢量**（返回的 `vector` 是 False）。

    ### 为什么 `page_pt` 是参数而不是这里算

    「这张位图有多少毫米」= 像素数 ÷ 物理密度，而**密度的解析只有
    `engine/originalspec.py` 一处**（pHYs / JFIF / Exif，读不到时按扩展名假定
    PNG 600 / 其余 300）。这个模块是 PDF 后端的边界层，它连素材的元数据都
    不该认识——在这里再写一个密度常量，等于给同一个问题准备第二个答案。

    第一版就是这么错的：这里写死 96 dpi，于是一张带 300 dpi 元数据的图被摆进
    一个大三倍多的页面，而界面上显示的尺寸来自 `OriginalOutputSpec`——**文件
    与界面各说各的**。评审（PR #214）当场指出来。

    回 `{w_pt, h_pt, px_w, px_h, vector, pages}`。
    """
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        with pymupdf.open(src) as doc:
            rect = doc[0].rect
            pages = doc.page_count
            with pymupdf.open() as dest:
                # 只搬第一页：一张「图」在本产品里恒等于一页（`_place_panel`
                # 也只取 doc[0]）。多页 PDF 被当素材放进来时，画布上看到的是
                # 第一页，导出必须与看到的一致
                dest.insert_pdf(doc, from_page=0, to_page=0)
                dest.save(str(out), deflate=True)
        return {
            "w_pt": rect.width,
            "h_pt": rect.height,
            "px_w": None,
            "px_h": None,
            "vector": True,
            "pages": pages,
        }

    pix = pymupdf.Pixmap(str(src))
    if page_pt is None or page_pt[0] <= 0 or page_pt[1] <= 0:
        raise ValueError("位图源装进 PDF 必须给页面尺寸（见 engine/originalspec）")
    w_pt, h_pt = float(page_pt[0]), float(page_pt[1])
    with pymupdf.open() as dest:
        page = dest.new_page(width=w_pt, height=h_pt)
        page.insert_image(page.rect, filename=str(src))
        dest.save(str(out), deflate=True)
    return {
        "w_pt": w_pt,
        "h_pt": h_pt,
        "px_w": pix.width,
        "px_h": pix.height,
        "vector": False,
        "pages": 1,
    }


def original_png(src: Path, out: Path, ppi: int | None, transparent: bool = False) -> dict:
    """把一张图**按它自己的尺寸**写成 PNG。

    * **矢量源**：按 `ppi` 栅格化；`transparent=True` 时带 alpha 通道（不画底）。
      矢量没有像素网格，所以这里必须有一个 ppi；调用方给 `None` 是个契约错误。
    * **位图源**：**保源像素网格**，`ppi` 与 `transparent` 在这条路上都不出场
      ——出来的就是那张图本身，背景是它自己的背景。不重采样是这条路的重点：
      "按原图导出"最容易被悄悄破坏的地方就是顺手按导出 ppi 缩一遍，那会把
      一张 300×200 的图变成 2500×1667 的糊图（共享规则 §8）。

    ### 位图源里 PNG 与 JPEG 走的不是同一条

    源本来就是 PNG 时**逐字节复制**——连 pHYs 这些元数据一起留住，比重新
    编码一遍更保真。源是 JPEG 时**必须转码**：把 JPEG 的字节原样搬进一个叫
    `.png` 的文件，出来的东西签名是 JPEG、扩展名与 MIME 是 PNG，严格的读取端
    直接判它是坏文件（PR #214 复审）。转码只换容器，像素数一个不变。

    ### 为什么没有「按另一个像素网格导出」这个开关

    第一版有过一个 `native_grid=False` 分支，按 `ppi ÷ 假定密度` 重采样。
    它**在产品里没有任何调用点**（界面上没有这个选项），而它用的那个"假定
    密度"是本模块自己写的第三个数字——一段没人执行、又与唯一权威分叉的代码。
    删掉它比留着更诚实：要加这个能力时，密度得从 `engine/originalspec` 来，
    像素网格由调用方算好传进来。

    回 `{px_w, px_h, resampled, transcoded}`（`resampled` 恒 False——像素网格
    从不改；`transcoded` 说的是换过容器编码没有）。
    """
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        if not ppi:
            raise ValueError("矢量源栅格化必须给 ppi")
        with pymupdf.open(src) as doc:
            page = doc[0]
            zoom = ppi / 72.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=bool(transparent))
            pix.save(str(out))
            return {
                "px_w": pix.width,
                "px_h": pix.height,
                "resampled": False,
                "transcoded": True,
            }

    if src.suffix.lower() == ".png":
        # 逐字节复制：元数据（pHYs 之类）一起留住，比重新编码更保真
        shutil.copyfile(src, out)
        pix = pymupdf.Pixmap(str(out))
        return {"px_w": pix.width, "px_h": pix.height, "resampled": False, "transcoded": False}

    # JPEG（或将来别的位图容器）：换容器，**不换像素**
    pix = pymupdf.Pixmap(str(src))
    pix.save(str(out))
    return {"px_w": pix.width, "px_h": pix.height, "resampled": False, "transcoded": True}


def annotate_asset(
    pdf_path: Path, png_path: Path | None, objects: list[dict], dpi: int = 600
) -> None:
    """把画布标注（text/arrow/shape，坐标为**该图自身的 mm**）画进导出的
    单图文件——「写回原始文件」勾选携带标注时用。

    PDF 上矢量绘制（与导出合成同一套 _draw_*，几何严格同源）；PNG 由注好
    的 PDF 重新栅格化，保证两种载体逐像素同源。只有 PNG、没有矢量底的
    素材不支持（调用方先拦）。"""
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[0]
        for o in objects:
            kind = o.get("type")
            if kind == "text":
                _draw_text(page, o)
            elif kind == "arrow":
                _draw_arrow(page, o)
            elif kind == "shape":
                _draw_shape(page, o)
        doc.save(str(pdf_path), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        if png_path is not None:
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            pix.save(str(png_path))
    finally:
        doc.close()
