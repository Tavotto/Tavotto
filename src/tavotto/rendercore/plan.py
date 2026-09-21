"""RenderPlan —— 从规范化的 `ExportRequest` 编译出 Render IR（统一实施包 U06，ADR 0059）。

04_ARCHITECTURE §2 那一行：*规范化原 ExportRequest、资源引用、顺序 / 单位 / 变换 / clip、
已排字形；无文件扫描 / 包安装 / 任意脚本*。这里做的就是这句话：

* **输入**只有三样：`exportreq.ExportRequest`（「要什么」的唯一定义，缺省值不在这里
  重复）、`sources.SourceResolver`（面板 → 冻结源）、`typography.FaceProvider`（族 →
  脸）。不读项目目录、不起子进程、不碰网络。
* **身份**复用 U01 的 `exportreq.render_plan_ref()`：`plan_identity` 由请求的渲染语义 +
  每个资源的语义坐标派生，字节 hash 单列（04 §3 三种身份不混）。本模块不再造第二个
  身份函数。
* **画布 → IR 的换算只在 `compile_page()` 一处**：毫米 → pt（`ir.mm2pt`），顶原点 y 向下 →
  左下原点 y 向上（`page_h_pt - y - h`），对象旋转 → `Group.transform`（CSS 顺时针 =
  y 向上空间里的负角）。写入器不知道毫米，也不再翻 y（RC-008）。
* **paint order = `objects` 的列表顺序**。`hidden` 的对象不进 IR，id 记在 `dropped_hidden`；
  同层顺序原样保留，不按 id / 类型排序（RC-009）。
* 文字经 `typography.layout_text()` 变成 `ShapedText`（已排字形），Arrow / Shape 经
  `geometry` 变成 `Path`，面板变成 `ImportedPage`（PDF）或 `Image`（位图）+ 一条
  `FileResource`（冻结源的语义身份 + 字节 hash）。
* 编译完立刻 `ir.validate()`：非法数字 / 尺寸 / 资源引用在这里被拒，写入器只收合法的页。

`scope=original` 在 U06 没有生产者（页面尺寸要探源页，U08 接 probe），`compile_plan()`
对它抛 `PlanError("scope_not_compiled")`，不猜一个页面尺寸出来。

纯标准库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..engine import exportreq
from . import geometry, typography
from .ir import (
    IDENTITY,
    RGB,
    FileResource,
    GlyphRun,
    Group,
    Image,
    ImportedPage,
    Matrix,
    Node,
    Page,
    Paint,
    Path,
    Resource,
    ShapedText,
    Stroke,
    compose,
    hex2rgb,
    mm2pt,
    rotate_about,
    unsupported_for,
    validate,
)
from .sources import FrozenSource, SourceError, SourceResolver
from .typography import FaceProvider, FaceSet, faces_for

PLAN_ERROR_CODES = (
    "scope_not_compiled",  # scope=original 本切片不编译
    "bad_object",  # 对象缺必填字段 / 类型不认识
    "source",  # SourceResolver 抛的错（原 code 在 params["source_code"]）
    "fonts",  # 字体不可用（原 code 在 params["font_code"]）
)


class PlanError(ValueError):
    def __init__(self, code: str, message: str, params: dict | None = None) -> None:
        assert code in PLAN_ERROR_CODES, code
        super().__init__(message)
        self.code = code
        self.params = params or {}


@dataclass(frozen=True)
class CompiledPage:
    page: Page
    #: 被丢掉的 hidden 对象 id（按输入顺序）。
    dropped_hidden: tuple[str, ...]
    #: 编译期发现、但不阻断的事实：`[{code, object_id, ...}]`（缺字、落到 cjk 脸的字符）。
    problems: tuple[dict, ...]
    #: 用到的冻结源，按首次出现顺序（资源 key → FrozenSource）。
    sources: dict[str, FrozenSource]


@dataclass(frozen=True)
class RenderPlan:
    """一次导出的完整计划：身份引用（U01 形状）+ IR + 每个格式的能力缺口。"""

    plan_ref: dict
    page: Page
    formats: tuple[str, ...]
    ppi: int | None
    background: str
    #: 格式 → `ir.unsupported_for()` 的结果（空表 = 这个格式能完整兑现）。
    unsupported: dict[str, list[dict]] = field(default_factory=dict)
    dropped_hidden: tuple[str, ...] = ()
    problems: tuple[dict, ...] = ()
    sources: dict[str, FrozenSource] = field(default_factory=dict)

    @property
    def plan_identity(self) -> str:
        return self.plan_ref["plan_identity"]


# ---------------------------------------------------------------------------
# 对象 → 节点
# ---------------------------------------------------------------------------
def _box_pt(o: dict, *, allow_zero: bool = False) -> tuple[float, float, float, float]:
    """对象框（毫米）→ pt。负的宽高一律拒；零宽 / 零高只有形状 / 箭头允许（一条水平线的框就是
    零高，路径照样合法），文字与面板在这里就拒——它们在降低成 IR 时会丢掉框的维度（文字变成
    基线点、面板的 rect 才会被 validate 拦），不在这里拦就没人拦了。"""
    oid = str(o.get("id", ""))
    what = o.get("id", o.get("type"))
    try:
        box = tuple(float(o[k]) for k in ("x_mm", "y_mm", "w_mm", "h_mm"))
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError(
            "bad_object", f"{what}: 缺 x_mm/y_mm/w_mm/h_mm 或不是数", {"id": oid}
        ) from exc
    if not all(math.isfinite(v) for v in box):
        raise PlanError("bad_object", f"{what}: x_mm/y_mm/w_mm/h_mm 必须是有限数", {"id": oid})
    x, y, w, h = box
    if w < 0 or h < 0 or (not allow_zero and (w == 0 or h == 0)):
        raise PlanError("bad_object", f"{what}: w_mm/h_mm 必须为正（{w} × {h}）", {"id": oid})
    return (mm2pt(x), mm2pt(y), mm2pt(w), mm2pt(h))


def _rotation_deg(o: dict, key: str = "rotation_deg") -> float:
    return float(o.get(key) or 0) % 360


def _box_to_page(x: float, y_top: float, page_h: float) -> Matrix:
    """框空间（原点框左上、y 向下）→ 页面空间（y 向上）：平移 + y 翻转。行列式 −1，可逆。"""
    return (1.0, 0.0, 0.0, -1.0, x, page_h - y_top)


def _object_transform(o: dict, page_h: float, key: str = "rotation_deg") -> Matrix:
    """框空间 → 页面空间，再绕框中心按画布语义（顺时针）旋转。"""
    x, y, w, h = _box_pt(o, allow_zero=True)
    m = _box_to_page(x, y, page_h)
    deg = _rotation_deg(o, key)
    if deg:
        m = compose(m, rotate_about(-deg, x + w / 2, page_h - (y + h / 2)))
    return m


def _panel_node(
    o: dict,
    page_h: float,
    resolver: SourceResolver,
    resources: dict[str, Resource],
    frozen: dict[str, FrozenSource],
) -> Node:
    try:
        fs = resolver.resolve(o)
    except SourceError as exc:
        raise PlanError(
            "source", str(exc), {"id": str(o.get("id", "")), "source_code": exc.code, **exc.params}
        ) from exc
    art = fs.artifact
    key = f"file:{art.bytes_sha256[:16]}:{art.semantic_identity()[7:23]}"
    if key not in resources:
        resources[key] = FileResource(
            source_id=art.source_id,
            kind=art.kind,
            sha256=art.bytes_sha256,
            origin=art.origin,
            semantic_identity=art.semantic_identity(),
        )
        frozen[key] = fs
    x, y, w, h = _box_pt(o)
    rect = (x, page_h - y - h, w, h)
    crop = o.get("crop")
    crop_t = (
        (float(crop["x"]), float(crop["y"]), float(crop["w"]), float(crop["h"]))
        if isinstance(crop, dict)
        else None
    )
    opacity = o.get("opacity")
    opacity = 1.0 if opacity is None else max(0.0, min(1.0, float(opacity)))
    oid = str(o.get("id", ""))
    if art.kind == "pdf":
        # 面板旋转限 90° 倍数（旧 facade 用户合同：非 90 倍数时 show_pdf_page 不填满目标矩形）
        rot = int(round(float(o.get("rotation") or 0) / 90.0)) * 90 % 360
        return ImportedPage(
            resource=key,
            rect=rect,
            opacity=opacity,
            crop=crop_t,
            rotate_cw_deg=float(rot),
            flip_h=bool(o.get("flip_h")),
            flip_v=bool(o.get("flip_v")),
            object_id=oid,
        )
    return Image(
        resource=key,
        rect=rect,
        opacity=opacity,
        crop=crop_t,
        flip_h=bool(o.get("flip_h")),
        flip_v=bool(o.get("flip_v")),
        object_id=oid,
    )


def _text_node(
    t: dict, page_h: float, faces: FaceSet, resources: dict[str, Resource], problems: list[dict]
) -> Node | None:
    bx, by, bw, bh = _box_pt(t)  # 先校验框（缺字段 / 非有限数在这里就拒），再排版
    block = typography.layout_text(t, faces)
    if block is None:
        return None
    oid = str(t.get("id", ""))
    children: list[Node] = []
    bg, border = t.get("bg"), t.get("border_color")
    if bg or border:
        stroke = None
        if border:
            stroke = Stroke(Paint(hex2rgb(border)), max(float(t.get("border_pt") or 0.75), 0.05))
        children.append(
            Path(
                geometry.rect_segments(bx, page_h - by - bh, bw, bh),
                fill=Paint(hex2rgb(bg)) if bg else None,
                stroke=stroke,
                object_id=oid,
            )
        )
    color = Paint(hex2rgb(block.color))
    underlines: list[Path] = []
    for line in block.lines:
        runs: list[GlyphRun] = []
        for piece in line.pieces:
            key = f"font:{piece.face.resource.face_id}"
            resources.setdefault(key, piece.face.resource)
            runs.append(
                GlyphRun(
                    font=key,
                    size=piece.size,
                    glyphs=tuple(piece.glyphs),
                    rise=piece.rise,
                    actual_text=piece.actual_text,
                )
            )
        if runs:
            children.append(ShapedText(line.x, page_h - line.baseline, tuple(runs), color, oid))
        if block.underline:
            uy = page_h - (line.baseline + block.size * 0.11)
            underlines.append(
                Path(
                    (("M", line.x, uy), ("L", line.x + line.width, uy)),
                    stroke=Stroke(color, max(block.size * 0.06, 0.3)),
                    object_id=oid,
                )
            )
    children.extend(underlines)
    if block.missing:
        problems.append({"code": "glyph_missing", "object_id": oid, "chars": list(block.missing)})
    if block.cjk_chars:
        problems.append({"code": "cjk_face", "object_id": oid, "chars": list(block.cjk_chars)})
    if not children:
        return None
    deg = _rotation_deg(t)
    transform = IDENTITY
    if deg:
        transform = rotate_about(-deg, bx + bw / 2, page_h - (by + bh / 2))
    return Group(tuple(children), transform=transform, object_id=oid)


def _shape_node(o: dict, page_h: float) -> Node:
    _x, _y, w, h = _box_pt(o, allow_zero=True)
    paths = (
        geometry.arrow_paths(o, w, h) if o.get("type") == "arrow" else geometry.shape_paths(o, w, h)
    )
    return Group(
        tuple(paths), transform=_object_transform(o, page_h), object_id=str(o.get("id", ""))
    )


def compile_page(
    page_w_mm: float,
    page_h_mm: float,
    objects: list[dict],
    *,
    sources: SourceResolver,
    faces: FaceProvider,
    background: RGB | None = (1.0, 1.0, 1.0),
) -> CompiledPage:
    """画布（毫米、顶原点、z 序对象列表）→ 已校验的 `Page`。"""
    page_w, page_h = mm2pt(float(page_w_mm)), mm2pt(float(page_h_mm))
    resources: dict[str, Resource] = {}
    frozen: dict[str, FrozenSource] = {}
    problems: list[dict] = []
    dropped: list[str] = []
    children: list[Node] = []
    for o in objects:
        if not isinstance(o, dict):
            raise PlanError("bad_object", f"对象不是 dict: {o!r}")
        if o.get("hidden"):
            dropped.append(str(o.get("id", "")))
            continue
        kind = o.get("type")
        node: Node | None
        if kind == "panel":
            node = _panel_node(o, page_h, sources, resources, frozen)
        elif kind == "text":
            try:
                fs = faces_for(
                    faces, o.get("font_family"), o.get("bold", False), o.get("italic", False)
                )
            except Exception as exc:  # noqa: BLE001 —— FontsUnavailable 之类要带 code 出去
                raise PlanError(
                    "fonts",
                    str(exc),
                    {"id": str(o.get("id", "")), "font_code": getattr(exc, "code", "")},
                ) from exc
            node = _text_node(o, page_h, fs, resources, problems)
        elif kind in ("arrow", "shape"):
            node = _shape_node(o, page_h)
        else:
            raise PlanError(
                "bad_object", f"不认识的对象类型: {kind!r}", {"id": str(o.get("id", ""))}
            )
        if node is not None:
            children.append(node)
    page = validate(Page(page_w, page_h, tuple(children), resources, background))
    return CompiledPage(page, tuple(dropped), tuple(problems), frozen)


def compile_plan(
    req: exportreq.ExportRequest, *, sources: SourceResolver, faces: FaceProvider
) -> RenderPlan:
    """`ExportRequest`（已规范化）→ `RenderPlan`。只编译 `scope=canvas`。"""
    if req.scope != exportreq.SCOPE_CANVAS or req.canvas is None:
        raise PlanError("scope_not_compiled", f"U06 只编译 scope=canvas（收到 {req.scope}）")
    src = req.canvas
    background: RGB | None = (
        None if req.background == exportreq.BACKGROUND_TRANSPARENT else (1.0, 1.0, 1.0)
    )
    compiled = compile_page(
        src.page_w_mm,
        src.page_h_mm,
        src.objects,
        sources=sources,
        faces=faces,
        background=background,
    )
    ref = exportreq.render_plan_ref(
        req, [fs.artifact.to_payload() for fs in compiled.sources.values()]
    )
    return RenderPlan(
        plan_ref=ref,
        page=compiled.page,
        formats=tuple(req.formats),
        ppi=req.ppi,
        background=req.background,
        unsupported={fmt: unsupported_for(compiled.page, fmt) for fmt in req.formats},
        dropped_hidden=compiled.dropped_hidden,
        problems=compiled.problems,
        sources=compiled.sources,
    )


__all__ = [
    "CompiledPage",
    "PLAN_ERROR_CODES",
    "PlanError",
    "RenderPlan",
    "compile_page",
    "compile_plan",
]
