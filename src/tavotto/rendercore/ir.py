"""Render IR —— RenderCore 的**纯模型**（统一实施包 U06，ADR 0059）。

这里定义「一页最终要画什么」的数据结构：`Page` / `Group` / `Path` / `Image` /
`ImportedPage` / `ShapedText` 与它们引用的资源（`FontResource` / `FileResource`），
以及 `validate()`——非法数字 / 尺寸 / 矩阵 / 资源引用在**进写入器之前**被拒绝
（RC-012：NaN 不许进 PDF 内容流）。

## 合同（RC-008：长度、矩阵、原点、顺序只有这一份说法）

* **单位 pt**（1/72 in）。**原点左下、y 向上**——与 PDF 用户空间相同。画布语义
  （毫米、顶原点、y 向下）到这里的换算只发生在 `plan.compile_page()` **一处**；
  写入器按 IR 坐标原样落笔，**不再翻转**（`test_rendercore_ir.py` 钉着：IR 里的
  (x, y) 在内容流里就是 (x, y)）。
* **变换是行向量约定** `p' = p · M`，`Matrix = (a, b, c, d, e, f)`，与 PDF 的 `cm`
  逐位相同：`x' = a·x + c·y + e`，`y' = b·x + d·y + f`。`compose(m1, m2)` = 先 m1
  后 m2。节点的 `transform` 把**节点局部空间**映到父空间。
* **顺序**：`Group` 先应用 `transform`，再在**变换后的局部空间**里应用 `clip`
  （clip 路径的坐标与 children 同一空间），然后按 `children` 的列表顺序画。
  PDF 里的形状就是 `q <cm> <clip re W n> …children… Q`。
* **paint order = 列表顺序**（底 → 顶）。`hidden` 的对象**不进 IR**（编译阶段
  丢掉并记账）；同层的稳定顺序就是输入顺序，**绝不按 id 排序**（RC-009 的
  must_fail_example）。
* **alpha**：`Paint.alpha` 是单个对象的常量 alpha（PDF ExtGState `ca` / `CA`）；
  `Group.opacity` 是**整组**的 alpha（透明组，组内从 1 起算，组内重叠不二次压暗）。
  两者语义不同，不许互相折算。
* **fill rule** 只有 `nonzero` / `evenodd` 两个取值，每条 `Path` 自己说；不说 =
  `nonzero`（与前端命中判据同一默认，`hit-and-selection-geometry`）。

## 什么**不在**这里

* 没有任何 native 对象（pikepdf / PDFium / PyMuPDF）、没有 Flask 上下文、没有科学栈：
  本模块与 `geometry` / `typography` / `sources` / `plan` 一起是「纯模型」层，只许
  标准库（`tests/test_rendercore_model.py` 用 importgraph 钉住：RC-007）。
* `ImportedPage` **只是源页的引用与落位**（资源 key + rect / crop / 旋转 / 翻转 /
  opacity）。它不描述源页内部有什么——源 PDF 里的文字、axes、颜色对 IR 来说是
  `unknown`，不为没有身份的东西编造身份（RC-013）。
* 文字是**已经排好的字形**（`ShapedText`：gid / cluster 原文 / advance / offset），
  不是字符串。字符串 → 字形在 `typography` 里做一次，量宽与落笔读同一份结果
  （RC-030）。

纯标准库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator, Union

# ---------------------------------------------------------------------------
# 矩阵（行向量约定，与 PDF `cm` 同形）
# ---------------------------------------------------------------------------
Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def compose(m1: Matrix, m2: Matrix) -> Matrix:
    """先 m1 后 m2（`p' = p · m1 · m2`）。"""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def translate(tx: float, ty: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, float(tx), float(ty))


def scale(sx: float, sy: float) -> Matrix:
    return (float(sx), 0.0, 0.0, float(sy), 0.0, 0.0)


def rotate_ccw(deg: float) -> Matrix:
    """绕原点逆时针 `deg` 度（y 向上的空间里逆时针为正）。"""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (c, s, -s, c, 0.0, 0.0)


def rotate_about(deg_ccw: float, cx: float, cy: float) -> Matrix:
    """绕 (cx, cy) 逆时针 `deg_ccw` 度。"""
    return compose(compose(translate(-cx, -cy), rotate_ccw(deg_ccw)), translate(cx, cy))


def determinant(m: Matrix) -> float:
    a, b, c, d, _, _ = m
    return a * d - b * c


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------
#: `IRError.code` 的闭集。新增一条要同时补 `tests/test_rendercore_ir.py` 的负例。
IR_ERROR_CODES = (
    "non_finite",  # NaN / ±Inf 出现在任何数值字段
    "non_positive_size",  # 页面 / 矩形的宽高 ≤ 0
    "singular_matrix",  # 变换矩阵行列式为 0（画不出任何东西，多半是 bug）
    "bad_alpha",  # alpha 不在 [0, 1]
    "bad_color",  # 颜色分量不在 [0, 1] 或不是三元组
    "bad_path",  # 路径不以 moveto 开头、段的元数不对、或 dash 全 0
    "bad_fill_rule",  # fill_rule 不在闭集里
    "bad_stroke",  # 线宽为负、cap / join 不在闭集里
    "unknown_resource",  # 节点引用的资源 key 不在 page.resources 里
    "resource_kind_mismatch",  # Image 指向 PDF、ImportedPage 指向位图、文字指向文件
    "bad_glyph",  # gid 不是非负整数、字号 ≤ 0、run 的逻辑文字与 cluster 拼不回来
    "bad_node",  # children 里出现不是节点的东西 / 嵌套过深
)

#: 组 / clip 嵌套深度上限。PDF 阅读器的图形状态栈有实现上限（PDF 32000 §8.4.2
#: 建议 ≥ 28），IR 不该造出会把它压爆的树；画布对象本来只有两层。
MAX_DEPTH = 16


class IRError(ValueError):
    """IR 不合法。`code` 是 `IR_ERROR_CODES` 里的稳定枚举，`path` 是出问题的节点位置。"""

    def __init__(self, code: str, message: str, path: str = "") -> None:
        assert code in IR_ERROR_CODES, code
        super().__init__(f"{path}: {message}" if path else message)
        self.code = code
        self.path = path


# ---------------------------------------------------------------------------
# 颜色与画笔
# ---------------------------------------------------------------------------
RGB = tuple[float, float, float]


def hex2rgb(s: object) -> RGB:
    """`#rrggbb` / `#rgb` → 0..1 三元组。认不出来的一律黑（与旧 facade 的取值逐条相同，
    `tests/test_paths_and_baked.py::test_hex2rgb` 那四条用例是它的合同）。"""
    text = str(s or "#000000").lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    try:
        r, g, b = (int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.0, 0.0, 0.0)
    return (r, g, b)


def mm2pt(mm: float) -> float:
    return float(mm) * 72.0 / 25.4


@dataclass(frozen=True)
class Paint:
    """一种颜色 + 常量 alpha。"""

    rgb: RGB
    alpha: float = 1.0


#: 线帽 / 线接的闭集（与 PDF 的 `J` / `j` 取值一一对应）。
LINE_CAPS = ("butt", "round", "square")
LINE_JOINS = ("miter", "round", "bevel")
FILL_RULES = ("nonzero", "evenodd")


@dataclass(frozen=True)
class Stroke:
    paint: Paint
    width: float
    cap: str = "butt"
    join: str = "miter"
    #: 虚线段长表（pt）+ 相位；空表 = 实线。
    dash: tuple[float, ...] = ()
    dash_phase: float = 0.0
    miter_limit: float = 10.0


# ---------------------------------------------------------------------------
# 路径段
# ---------------------------------------------------------------------------
#: 段 = (op, *坐标)。op ∈ {"M", "L", "C", "Z"}；C 是三次贝塞尔（两个控制点 + 终点）。
Segment = tuple

_SEGMENT_ARITY = {"M": 2, "L": 2, "C": 6, "Z": 0}


# ---------------------------------------------------------------------------
# 资源
# ---------------------------------------------------------------------------
#: 字体程序格式的闭集（写入器只会做这两条路，其余显式拒绝，RC-037）。
FONT_KINDS = ("truetype", "cff-cid")
#: 位图 / 矢量源的种类闭集。
FILE_KINDS = ("pdf", "png", "jpg", "jpeg", "tif", "tiff")
RASTER_KINDS = ("png", "jpg", "jpeg", "tif", "tiff")


@dataclass(frozen=True)
class FontResource:
    """一张**已批准**的脸：身份是文件字节（sha256）+ face 序号 + 样式，不是族名
    （RC-023：同名不同字体是两个资源）。"""

    face_id: str
    sha256: str
    family: str
    bold: bool
    italic: bool
    kind: str  # FONT_KINDS
    postscript_name: str
    units_per_em: int
    face_index: int = 0


@dataclass(frozen=True)
class FileResource:
    """一份**冻结的**源产物（U01 `SourceArtifact` 的语义坐标 + 字节 hash）。IR 只引用
    身份，不含路径：字节从哪读、读之前核不核 hash 是 `sources` / 写入器的事。"""

    source_id: str
    kind: str  # FILE_KINDS
    sha256: str
    origin: str  # execution | static
    semantic_identity: str


Resource = Union[FontResource, FileResource]


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Path:
    segments: tuple[Segment, ...]
    fill: Paint | None = None
    stroke: Stroke | None = None
    fill_rule: str = "nonzero"
    #: 来源对象 id（记账 / 报告用；不参与绘制，也不参与排序）。
    object_id: str = ""


@dataclass(frozen=True)
class Glyph:
    """一个已排好的字形。`text` 是它覆盖的 cluster 原文；一个 cluster 出多个字形时只有
    第一个带原文，其余为空串（写入器据此决定 ToUnicode 与 ActualText）。
    advance / offset 单位是**字体单位**（除以 `units_per_em` 再乘字号得 pt）。"""

    gid: int
    text: str
    x_advance: int
    x_offset: int = 0
    y_offset: int = 0


@dataclass(frozen=True)
class GlyphRun:
    font: str  # page.resources 里的 FontResource key
    size: float  # pt
    glyphs: tuple[Glyph, ...]
    #: 基线抬高（pt，正 = 向上；上下标用）。
    rise: float = 0.0
    #: 这一段的**逻辑文字**与 cluster 原文拼出来的不一致时（合成上下标：画的是 `5`，
    #: 用户写的是 `⁵`），这里放用户的原文；写入器包 ActualText。None = 一致。
    actual_text: str | None = None

    @property
    def cluster_text(self) -> str:
        return "".join(g.text for g in self.glyphs)


@dataclass(frozen=True)
class ShapedText:
    """一行（或一行里的一段）已排好的文字。`x, y` 是**基线起点**（pt，y 向上）。"""

    x: float
    y: float
    runs: tuple[GlyphRun, ...]
    paint: Paint
    object_id: str = ""


@dataclass(frozen=True)
class Image:
    """位图放置：资源 `resource` 的整幅像素（或 crop 后的子矩形）映到局部空间的 `rect`。
    `crop` 是归一化 (x, y, w, h)、**顶原点**、相对源图——与旧 facade `_crop_clip` 同一约定；
    `rotate_cw_deg` 绕 rect 中心顺时针（CSS 语义），与 `ImportedPage` 同一条合同（`placement.place`）。
    `opacity < 1` 是常量 alpha（`object_alpha`：位图是一次填充，没有「组内重叠」这回事）。"""

    resource: str
    rect: tuple[float, float, float, float]  # x, y, w, h（局部空间，y 向上）
    opacity: float = 1.0
    crop: tuple[float, float, float, float] | None = None
    rotate_cw_deg: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    object_id: str = ""


@dataclass(frozen=True)
class ImportedPage:
    """外来 PDF 的**一页**作为整体放进 `rect`。源页内部是什么，IR 不知道也不假装知道
    （RC-013：`internal = "unknown"`，永远）。`rotate_cw_deg` 绕 rect 中心顺时针（CSS 语义）。"""

    resource: str
    rect: tuple[float, float, float, float]
    page_index: int = 0
    opacity: float = 1.0
    crop: tuple[float, float, float, float] | None = None
    rotate_cw_deg: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    object_id: str = ""

    @property
    def internal(self) -> str:
        return "unknown"


@dataclass(frozen=True)
class Group:
    children: tuple[Node, ...]
    transform: Matrix = IDENTITY
    #: 局部空间里的裁剪路径（变换之后、children 之前生效）。None = 不裁。
    clip: Path | None = None
    #: 整组 alpha。< 1 时写入器用透明组（组内 alpha 从 1 起算）。
    opacity: float = 1.0
    object_id: str = ""


Node = Union[Group, Path, Image, ImportedPage, ShapedText]
NODE_TYPES = (Group, Path, Image, ImportedPage, ShapedText)


@dataclass(frozen=True)
class Page:
    width_pt: float
    height_pt: float
    children: tuple[Node, ...]
    resources: dict[str, Resource] = field(default_factory=dict)
    #: None = 透明（不画底色）；否则整页先铺这个颜色。
    background: RGB | None = (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# 能力表（RC-011）
# ---------------------------------------------------------------------------
#: 三档取值。`native` = 矢量 / 真文字；`rasterized` = 以位图兑现（要明说）；
#: `unsupported` = 这条路给不出，写入器会以结构化错误拒绝，**不静默降级**。
CAP_NATIVE = "native"
CAP_RASTERIZED = "rasterized"
CAP_UNSUPPORTED = "unsupported"
CAP_LEVELS = (CAP_NATIVE, CAP_RASTERIZED, CAP_UNSUPPORTED)

#: 操作的闭集——写入器遇到 IR 里的每种东西都能在这里找到一条。
OPERATIONS = (
    "page_background",
    "path_fill",
    "path_stroke",
    "clip",
    "group_opacity",
    "object_alpha",
    "text",
    "image",
    "imported_page",
    "flip",
)
CAP_FORMATS = ("pdf", "png", "tiff", "eps")
CAP_SCOPES = ("canvas", "original")


@dataclass(frozen=True)
class Capability:
    level: str
    reason: str


def _caps(level: str, reason: str, ops: tuple[str, ...]) -> dict[str, Capability]:
    return {op: Capability(level, reason) for op in ops}


#: **写入器真实实现了什么**。格式 → 操作 → 能力。
#:
#: 这张表不是愿望清单：`tests/test_rendercore_writer.py` 逐条交叉核对——声明 `native`
#: 的操作，写入器必须真的写出对应的操作符；声明 `unsupported` 的，写入器必须以
#: `UnsupportedCapability` 拒绝（RC-011 must_fail_example：未实现的后端对外声明 native）。
#: 改这张表（加操作 / 翻档）时那条交叉用例必须跟着变。
CAPABILITIES: dict[str, dict[str, Capability]] = {
    # 声明 native 的每一项都由 `pdfwriter` 真的写出，`tests/test_rendercore_writer.py` 逐操作交叉核对
    # （声明了 native 而写入器写不出，就是 RC-011 要挡的那件事）。
    "pdf": {
        **_caps(
            CAP_NATIVE,
            "U06 写入器：路径 / 裁剪 / 透明组 / 常量 alpha / 可检索文字",
            (
                "page_background",
                "path_fill",
                "path_stroke",
                "clip",
                "group_opacity",
                "object_alpha",
                "text",
            ),
        ),
        **_caps(
            CAP_NATIVE,
            "U07 写入器：外来页整页作 Form XObject（qpdf copy_foreign，页盒 / Rotate / UserUnit 折进 /Matrix）、"
            "位图作 Image XObject（Flate + /SMask，JPEG 直通 DCTDecode）、镜像是 cm 里的负缩放——"
            "都是矢量落位，不退位图（ADR 0065）",
            ("image", "imported_page", "flip"),
        ),
    },
    # 位图格式：每个操作都以「PDFium 把 Canonical PDF 栅格化」兑现（ADR 0066）——PNG 与 TIFF 从同一个
    # RasterBuffer 编码，不存在第二条 layout 路；`rasterized` 是如实的档位，不是降级
    "png": _caps(
        CAP_RASTERIZED, "U07 render child：PDFium 栅格化 Canonical PDF（ADR 0066）", OPERATIONS
    ),
    "tiff": _caps(
        CAP_RASTERIZED,
        "U07 render child：与 PNG 同一个 RasterBuffer（ADR 0046 / 0066）",
        OPERATIONS,
    ),
    "eps": _caps(
        CAP_UNSUPPORTED,
        "没有 PostScript 写入器；EPS 只有 worker 的 matplotlib 写得出（ADR 0046）",
        OPERATIONS,
    ),
}


def capability(fmt: str, operation: str) -> Capability:
    return CAPABILITIES[fmt][operation]


def operations_of(node: Node) -> tuple[str, ...]:
    """一个节点用到了哪些操作（给能力核对与写入器的前置检查用）。"""
    ops: list[str] = []
    if isinstance(node, Path):
        if node.fill is not None:
            ops.append("path_fill")
            if node.fill.alpha < 1.0:
                ops.append("object_alpha")
        if node.stroke is not None:
            ops.append("path_stroke")
            if node.stroke.paint.alpha < 1.0:
                ops.append("object_alpha")
    elif isinstance(node, ShapedText):
        ops.append("text")
        if node.paint.alpha < 1.0:
            ops.append("object_alpha")
    elif isinstance(node, Image):
        ops.append("image")
        if node.opacity < 1.0:
            ops.append("object_alpha")
        if node.flip_h or node.flip_v:
            ops.append("flip")
    elif isinstance(node, ImportedPage):
        ops.append("imported_page")
        if node.opacity < 1.0:
            ops.append("group_opacity")
        if node.flip_h or node.flip_v:
            ops.append("flip")
    elif isinstance(node, Group):
        if node.clip is not None:
            ops.append("clip")
        if node.opacity < 1.0:
            ops.append("group_opacity")
    return tuple(dict.fromkeys(ops))


def unsupported_for(page: Page, fmt: str) -> list[dict]:
    """这一页在某个格式下**给不出**的操作：`[{operation, reason, object_id}]`（去重）。
    空表 = 这个格式能完整兑现这一页。"""
    table = CAPABILITIES[fmt]
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if page.background is not None and table["page_background"].level == CAP_UNSUPPORTED:
        out.append(
            {
                "operation": "page_background",
                "reason": table["page_background"].reason,
                "object_id": "",
            }
        )
    for node, _path in walk(page):
        for op in operations_of(node):
            cap = table[op]
            if cap.level != CAP_UNSUPPORTED:
                continue
            key = (op, getattr(node, "object_id", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append({"operation": op, "reason": cap.reason, "object_id": key[1]})
    return out


# ---------------------------------------------------------------------------
# 遍历与校验
# ---------------------------------------------------------------------------
def walk(page: Page) -> Iterator[tuple[Node, str]]:
    """深度优先、按 paint order 产出 (节点, 位置路径)。位置路径形如 `children[2].children[0]`。"""
    stack: list[tuple[Node, str, int]] = [
        (n, f"children[{i}]", 1) for i, n in reversed(list(enumerate(page.children)))
    ]
    while stack:
        node, path, depth = stack.pop()
        yield node, path
        if isinstance(node, Group):
            for i, child in reversed(list(enumerate(node.children))):
                stack.append((child, f"{path}.children[{i}]", depth + 1))


def _finite(v: object, path: str, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise IRError("non_finite", f"{what} 不是数: {v!r}", path)
    f = float(v)
    if not math.isfinite(f):
        raise IRError("non_finite", f"{what} 不是有限数: {v!r}", path)
    return f


def _rect(rect: object, path: str) -> None:
    if not isinstance(rect, tuple) or len(rect) != 4:
        raise IRError("non_positive_size", f"rect 必须是 (x, y, w, h): {rect!r}", path)
    x, y, w, h = (_finite(v, path, "rect") for v in rect)
    if w <= 0 or h <= 0:
        raise IRError("non_positive_size", f"rect 的宽高必须为正: {rect!r}", path)


def _crop(crop: object, path: str) -> None:
    if crop is None:
        return
    if not isinstance(crop, tuple) or len(crop) != 4:
        raise IRError("non_positive_size", f"crop 必须是 (x, y, w, h): {crop!r}", path)
    x, y, w, h = (_finite(v, path, "crop") for v in crop)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        raise IRError("non_positive_size", f"crop 必须落在 [0,1]² 里且宽高为正: {crop!r}", path)


def _alpha(v: object, path: str) -> None:
    a = _finite(v, path, "alpha")
    if a < 0.0 or a > 1.0:
        raise IRError("bad_alpha", f"alpha 必须在 [0, 1]: {v!r}", path)


def _paint(p: object, path: str) -> None:
    if not isinstance(p, Paint):
        raise IRError("bad_color", f"不是 Paint: {p!r}", path)
    if not isinstance(p.rgb, tuple) or len(p.rgb) != 3:
        raise IRError("bad_color", f"颜色必须是三元组: {p.rgb!r}", path)
    for c in p.rgb:
        v = _finite(c, path, "颜色分量")
        if v < 0.0 or v > 1.0:
            raise IRError("bad_color", f"颜色分量必须在 [0, 1]: {p.rgb!r}", path)
    _alpha(p.alpha, path)


def _matrix(m: object, path: str) -> None:
    if not isinstance(m, tuple) or len(m) != 6:
        raise IRError("singular_matrix", f"矩阵必须是六元组: {m!r}", path)
    vals = tuple(_finite(v, path, "矩阵分量") for v in m)
    if abs(determinant(vals)) < 1e-12:
        raise IRError("singular_matrix", f"矩阵不可逆: {m!r}", path)


def _path(node: Path, path: str) -> None:
    if not isinstance(node.segments, tuple) or not node.segments:
        raise IRError("bad_path", "路径没有任何段", path)
    for seg in node.segments:
        if not isinstance(seg, tuple) or not seg or seg[0] not in _SEGMENT_ARITY:
            raise IRError("bad_path", f"不认识的段: {seg!r}", path)
        if len(seg) - 1 != _SEGMENT_ARITY[seg[0]]:
            raise IRError(
                "bad_path", f"段 {seg[0]} 需要 {_SEGMENT_ARITY[seg[0]]} 个数: {seg!r}", path
            )
        for v in seg[1:]:
            _finite(v, path, "路径坐标")
    if node.segments[0][0] != "M":  # 段的形状都对了才看第一段是不是 moveto（空元组不能先被索引）
        raise IRError("bad_path", f"路径必须以 M 开头: {node.segments[0]!r}", path)
    if node.fill_rule not in FILL_RULES:
        raise IRError("bad_fill_rule", f"fill_rule 只能是 {FILL_RULES}: {node.fill_rule!r}", path)
    if node.fill is None and node.stroke is None:
        raise IRError("bad_path", "既不填充也不描边的路径画不出任何东西", path)
    if node.fill is not None:
        _paint(node.fill, path)
    if node.stroke is not None:
        s = node.stroke
        if not isinstance(s, Stroke):
            raise IRError("bad_stroke", f"不是 Stroke: {s!r}", path)
        _paint(s.paint, path)
        if _finite(s.width, path, "线宽") < 0:
            raise IRError("bad_stroke", f"线宽不能为负: {s.width!r}", path)
        if s.cap not in LINE_CAPS or s.join not in LINE_JOINS:
            raise IRError("bad_stroke", f"cap / join 不在闭集里: {s.cap!r} / {s.join!r}", path)
        if _finite(s.miter_limit, path, "miter_limit") < 1.0:
            raise IRError("bad_stroke", f"miter_limit 必须 ≥ 1: {s.miter_limit!r}", path)
        if s.dash:
            lens = [_finite(v, path, "dash") for v in s.dash]
            if any(v < 0 for v in lens) or not any(v > 0 for v in lens):
                raise IRError("bad_path", f"dash 段长不能为负、也不能全 0: {s.dash!r}", path)
            _finite(s.dash_phase, path, "dash_phase")


def _text(node: ShapedText, page: Page, path: str) -> None:
    _finite(node.x, path, "x")
    _finite(node.y, path, "y")
    _paint(node.paint, path)
    if not isinstance(node.runs, tuple):
        raise IRError("bad_glyph", "runs 必须是元组", path)
    for i, run in enumerate(node.runs):
        rp = f"{path}.runs[{i}]"
        if not isinstance(run, GlyphRun):
            raise IRError("bad_glyph", f"不是 GlyphRun: {run!r}", rp)
        res = page.resources.get(run.font)
        if res is None:
            raise IRError("unknown_resource", f"字体资源 {run.font!r} 不在 page.resources 里", rp)
        if not isinstance(res, FontResource):
            raise IRError("resource_kind_mismatch", f"{run.font!r} 不是字体资源", rp)
        if _finite(run.size, rp, "字号") <= 0:
            raise IRError("bad_glyph", f"字号必须为正: {run.size!r}", rp)
        _finite(run.rise, rp, "rise")
        if not isinstance(run.glyphs, tuple) or not run.glyphs:
            raise IRError("bad_glyph", "run 没有字形", rp)
        for g in run.glyphs:
            if not isinstance(g, Glyph):
                raise IRError("bad_glyph", f"不是 Glyph: {g!r}", rp)
            if isinstance(g.gid, bool) or not isinstance(g.gid, int) or g.gid < 0:
                raise IRError("bad_glyph", f"gid 必须是非负整数: {g.gid!r}", rp)
            for v in (g.x_advance, g.x_offset, g.y_offset):
                if isinstance(v, bool) or not isinstance(v, int):
                    raise IRError(
                        "bad_glyph", f"advance / offset 必须是整数（字体单位）: {v!r}", rp
                    )
            if not isinstance(g.text, str):
                raise IRError("bad_glyph", f"cluster 原文必须是字符串: {g.text!r}", rp)
        if run.actual_text is not None and not isinstance(run.actual_text, str):
            raise IRError("bad_glyph", f"actual_text 必须是字符串或 None: {run.actual_text!r}", rp)


def _placement(
    node: Image | ImportedPage, page: Page, path: str, want_kinds: tuple[str, ...]
) -> None:
    res = page.resources.get(node.resource)
    if res is None:
        raise IRError("unknown_resource", f"资源 {node.resource!r} 不在 page.resources 里", path)
    if not isinstance(res, FileResource):
        raise IRError("resource_kind_mismatch", f"{node.resource!r} 不是文件资源", path)
    if res.kind not in want_kinds:
        raise IRError(
            "resource_kind_mismatch",
            f"{type(node).__name__} 需要 {want_kinds} 之一的源，{node.resource!r} 是 {res.kind!r}",
            path,
        )
    _rect(node.rect, path)
    _crop(node.crop, path)
    _alpha(node.opacity, path)
    _finite(node.rotate_cw_deg, path, "rotate_cw_deg")
    if isinstance(node, ImportedPage):
        if (
            isinstance(node.page_index, bool)
            or not isinstance(node.page_index, int)
            or node.page_index < 0
        ):
            raise IRError("bad_node", f"page_index 必须是非负整数: {node.page_index!r}", path)


def _resource(key: str, res: object) -> None:
    path = f"resources[{key!r}]"
    if isinstance(res, FontResource):
        if res.kind not in FONT_KINDS:
            raise IRError("resource_kind_mismatch", f"字体程序格式不在闭集里: {res.kind!r}", path)
        if (
            isinstance(res.units_per_em, bool)
            or not isinstance(res.units_per_em, int)
            or res.units_per_em <= 0
        ):
            raise IRError("bad_glyph", f"units_per_em 必须是正整数: {res.units_per_em!r}", path)
        if not _is_sha256(res.sha256):
            raise IRError("unknown_resource", f"字体资源的 sha256 不合形状: {res.sha256!r}", path)
    elif isinstance(res, FileResource):
        if res.kind not in FILE_KINDS:
            raise IRError("resource_kind_mismatch", f"文件种类不在闭集里: {res.kind!r}", path)
        if not _is_sha256(res.sha256):
            raise IRError("unknown_resource", f"文件资源的 sha256 不合形状: {res.sha256!r}", path)
        if res.origin not in ("execution", "static"):
            raise IRError("unknown_resource", f"origin 不在闭集里: {res.origin!r}", path)
    else:
        raise IRError("resource_kind_mismatch", f"不是资源: {res!r}", path)


def _is_sha256(s: object) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def validate(page: Page) -> Page:
    """整页校验；不合法抛 `IRError`。合法就原样返回（方便链式写法）。

    判据的主语是**写入器将要读到的每一个数**：NaN / Inf、非正尺寸、不可逆矩阵、
    越界 alpha、悬空资源引用，任何一条都在这里挡住，而不是变成内容流里的 `nan`。
    """
    if not isinstance(page, Page):
        raise IRError("bad_node", f"不是 Page: {page!r}")
    w, h = _finite(page.width_pt, "page", "宽"), _finite(page.height_pt, "page", "高")
    if w <= 0 or h <= 0:
        raise IRError("non_positive_size", f"页面宽高必须为正: {w} × {h}", "page")
    if page.background is not None:
        _paint(Paint(page.background), "page.background")
    if not isinstance(page.resources, dict):
        raise IRError("bad_node", "resources 必须是 dict", "page")
    for key, res in page.resources.items():
        if not isinstance(key, str) or not key:
            raise IRError("unknown_resource", f"资源 key 必须是非空字符串: {key!r}", "page")
        _resource(key, res)
    if not isinstance(page.children, tuple):
        raise IRError("bad_node", "children 必须是元组", "page")

    def visit(node: object, path: str, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise IRError("bad_node", f"嵌套超过 {MAX_DEPTH} 层", path)
        if isinstance(node, Group):
            _matrix(node.transform, path)
            _alpha(node.opacity, path)
            if node.clip is not None:
                if not isinstance(node.clip, Path):
                    raise IRError("bad_path", "clip 必须是 Path", f"{path}.clip")
                # clip 只借路径的几何：填充 / 描边在这里没有意义，但校验需要一个 Paint
                _path(
                    Path(
                        node.clip.segments,
                        fill=Paint((0.0, 0.0, 0.0)),
                        fill_rule=node.clip.fill_rule,
                    ),
                    f"{path}.clip",
                )
            if not isinstance(node.children, tuple):
                raise IRError("bad_node", "children 必须是元组", path)
            for i, child in enumerate(node.children):
                visit(child, f"{path}.children[{i}]", depth + 1)
        elif isinstance(node, Path):
            _path(node, path)
        elif isinstance(node, ShapedText):
            _text(node, page, path)
        elif isinstance(node, Image):
            _placement(node, page, path, RASTER_KINDS)
        elif isinstance(node, ImportedPage):
            _placement(node, page, path, ("pdf",))
        else:
            raise IRError("bad_node", f"不是节点: {node!r}", path)

    for i, child in enumerate(page.children):
        visit(child, f"children[{i}]", 1)
    return page
