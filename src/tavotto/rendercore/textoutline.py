"""文字转轮廓（ADR 0185 M5 第二批）：画布上的文字对象 → 一条或几条自由路径，**字形取自导出用的同一份字体**。

做法是把这个文字对象按导出的同一条路编成 IR（`plan.compile_page`：同一张字体登记表、同一次 HarfBuzz 排字、同一份公式排版），
再把每个字形的轮廓（fontTools，与 `svgwriter` 画轮廓字同一份 glyph set）按 IR 里的落笔位置、字号、模拟斜体错切、对象旋转
换到页面——所以转出来的路径与导出 PDF 里那段字逐点重合。背景框 / 边框 / 下划线 / 公式这些 IR 里本来就是路径的，原样换成路径对象。

输出是画布文档里的路径对象（页面 mm、顶原点；`path` 的框 + 比例坐标锚点），按 IR 的绘制顺序：底框 → 字 → 公式 → 下划线。
一种颜色 / 一种模拟粗体的字形并成一条复合路径（非零规则，字形自带的方向就对）；模拟粗体 = 同色描边（`ir.synth_bold_width`）。
"""

from __future__ import annotations

from . import ir, plan
from .svgimport import MM_PER_PT, path_object

QUAD_TO_CUBIC = 2.0 / 3.0


class _Collect:
    """fontTools 的 pen：把字形轮廓收成「子路径 = 锚点 + 手柄」（二次样条精确升三次），坐标经 `m` 换到页面 pt。"""

    def __init__(self, glyph_set, m: ir.Matrix):
        from fontTools.pens.basePen import BasePen

        outer = self

        class Pen(BasePen):
            def _moveTo(self, p):
                outer._start(p)

            def _lineTo(self, p):
                outer._line(p)

            def _curveToOne(self, p1, p2, p3):
                outer._curve(p1, p2, p3)

            def _qCurveToOne(self, p1, p2):
                p0 = self._getCurrentPoint()
                c1 = (
                    p0[0] + QUAD_TO_CUBIC * (p1[0] - p0[0]),
                    p0[1] + QUAD_TO_CUBIC * (p1[1] - p0[1]),
                )
                c2 = (
                    p2[0] + QUAD_TO_CUBIC * (p1[0] - p2[0]),
                    p2[1] + QUAD_TO_CUBIC * (p1[1] - p2[1]),
                )
                outer._curve(c1, c2, p2)

            def _closePath(self):
                outer._close()

            def _endPath(self):
                outer.cur = None

        self.pen = Pen(glyph_set)
        self.m = m
        self.subs: list[dict] = []
        self.cur: dict | None = None

    def _pt(self, p):
        return ir.apply(self.m, float(p[0]), float(p[1]))

    def _start(self, p):
        q = self._pt(p)
        self.cur = {"closed": False, "nodes": [{"p": q, "in": q, "out": q}]}
        self.subs.append(self.cur)

    def _line(self, p):
        q = self._pt(p)
        self.cur["nodes"].append({"p": q, "in": q, "out": q})

    def _curve(self, p1, p2, p3):
        self.cur["nodes"][-1]["out"] = self._pt(p1)
        q = self._pt(p3)
        self.cur["nodes"].append({"p": q, "in": self._pt(p2), "out": q})

    def _close(self):
        if self.cur is None:
            return
        n = self.cur["nodes"]
        if len(n) > 1 and n[-1]["p"] == n[0]["p"]:
            n[0]["in"] = n[-1]["in"]
            n.pop()
        self.cur["closed"] = True
        self.cur = None


def _to_mm(subs: list[dict], page_h: float) -> list[dict]:
    def mm(q):
        return (q[0] * MM_PER_PT, (page_h - q[1]) * MM_PER_PT)

    return [
        {
            "closed": sp["closed"],
            "nodes": [{k: mm(n[k]) for k in ("p", "in", "out")} for n in sp["nodes"]],
        }
        for sp in subs
    ]


def _segs_to_subs(segs, m: ir.Matrix) -> list[dict]:
    subs: list[dict] = []
    cur: dict | None = None
    for s in segs:
        if s[0] == "M":
            q = ir.apply(m, s[1], s[2])
            cur = {"closed": False, "nodes": [{"p": q, "in": q, "out": q}]}
            subs.append(cur)
        elif s[0] == "L" and cur is not None:
            q = ir.apply(m, s[1], s[2])
            cur["nodes"].append({"p": q, "in": q, "out": q})
        elif s[0] == "C" and cur is not None:
            cur["nodes"][-1]["out"] = ir.apply(m, s[1], s[2])
            q = ir.apply(m, s[5], s[6])
            cur["nodes"].append({"p": q, "in": ir.apply(m, s[3], s[4]), "out": q})
        elif s[0] == "Z" and cur is not None:
            n = cur["nodes"]
            if len(n) > 1 and n[-1]["p"] == n[0]["p"]:
                n[0]["in"] = n[-1]["in"]
                n.pop()
            cur["closed"] = True
            cur = None
    return subs


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c * 255))) for c in rgb))


def _paint_fields(fill: ir.Paint | None, stroke: ir.Stroke | None) -> dict:
    out: dict = {}
    if fill is not None and not ir.is_gradient(fill):
        out["fill"] = _hex(fill.rgb)
        if fill.alpha < 1:
            out["fillOpacity"] = round(fill.alpha, 6)
    else:
        out["fill"] = None
    if stroke is not None and not ir.is_gradient(stroke.paint):
        out["color"] = _hex(stroke.paint.rgb)
        out["strokePt"] = round(stroke.width, 6)
        if stroke.paint.alpha < 1:
            out["strokeOpacity"] = round(stroke.paint.alpha, 6)
        if stroke.cap in ("round", "square"):
            out["lineCap"] = stroke.cap
        if stroke.join in ("miter", "bevel"):
            out["lineJoin"] = stroke.join
        if stroke.dash:
            out["dashPattern"] = [round(v, 6) for v in stroke.dash]
    else:
        out["color"] = out["fill"] or "#000000"
        out["strokePt"] = 1
        out["stroke"] = False
    return out


def outline_text(obj: dict, *, faces, math: dict | None = None) -> list[dict]:
    """一个文字对象（导出载荷的形状，`exportPayload.toExportObject`）→ 页面 mm 的路径对象列表（不带 id）。

    排不出来（空文字）= 空列表。字体不可用等问题与导出同一种异常（`plan.PlanError`）原样抛出。"""
    if obj.get("type") != "text":
        raise plan.PlanError("bad_object", "只有文字对象能转轮廓", {"id": str(obj.get("id", ""))})
    y_mm = float(obj.get("y_mm") or 0) + float(obj.get("h_mm") or 0)
    page_h_mm = max(y_mm + 1000.0, 1000.0)
    page_w_mm = max(float(obj.get("x_mm") or 0) + float(obj.get("w_mm") or 0) + 1000.0, 1000.0)
    compiled = plan.compile_page(
        page_w_mm,
        page_h_mm,
        [obj],
        sources=None,
        faces=faces,
        background=None,
        math=math,  # type: ignore[arg-type]
    )
    page = compiled.page
    page_h = page.height_pt
    out: list[dict] = []
    face_cache: dict[str, object] = {}
    glyph_sets: dict[str, object] = {}

    def face_of(key: str):
        if key not in face_cache:
            face_cache[key] = faces.face_by_resource(page.resources[key])
        return face_cache[key]

    def emit(subs_pt: list[dict], fields: dict) -> None:
        geom = path_object(_to_mm(subs_pt, page_h))
        if geom is not None:
            out.append({"type": "path", **geom, **fields})

    def visit(node, m: ir.Matrix) -> None:
        if isinstance(node, ir.Group):
            # 先内后外：子节点的点先过这一层的变换，再过外层累积的（`compose(m1, m2)` = 先 m1 后 m2）
            mm = ir.compose(node.transform, m)
            for c in node.children:
                visit(c, mm)
            return
        if isinstance(node, ir.Path):
            subs = _segs_to_subs(node.segments, m)
            fields = _paint_fields(node.fill, node.stroke)
            if node.fill_rule == "evenodd":
                fields["fillRule"] = "evenodd"
            emit(subs, fields)
            return
        if isinstance(node, ir.ShapedText):
            pen_x = node.x
            groups: dict[bool, list[dict]] = {}
            widths: dict[bool, float] = {}
            for run in node.runs:
                face = face_of(run.font)
                scale = run.size / face.upem
                if not run.invisible:
                    if run.font not in glyph_sets:
                        glyph_sets[run.font] = face._tt.getGlyphSet()
                    gs = glyph_sets[run.font]
                    gx = pen_x
                    for g in run.glyphs:
                        gm = (
                            scale,
                            0.0,
                            scale * ir.SYNTH_ITALIC_SKEW if run.synth_italic else 0.0,
                            scale,
                            gx + g.x_offset * scale,
                            node.y + run.rise + g.y_offset * scale,
                        )
                        col = _Collect(gs, ir.compose(gm, m))
                        gs[face.glyph_name(g.gid)].draw(col.pen)
                        groups.setdefault(run.synth_bold, []).extend(col.subs)
                        if run.synth_bold:
                            widths[True] = ir.synth_bold_width(run.size)
                        gx += g.x_advance * scale
                pen_x += sum(g.x_advance for g in run.glyphs) * scale
            for bold, subs in groups.items():
                fields = _paint_fields(
                    node.paint, ir.Stroke(node.paint, widths[True], join="round") if bold else None
                )
                emit(subs, fields)
            return
        # 位图 / 置入页不会出现在文字对象里

    for child in page.children:
        visit(child, ir.IDENTITY)
    return out
