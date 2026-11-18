"""外部 SVG → 画布上可编辑的对象（ADR 0189）。

输入是**不受信任**的 SVG 文本（Illustrator / Inkscape / BioRender 之类导出或复制出来的），输出是画布文档里的对象
（前端 `types/document` 的形状：`path` / `text`，组用 `groupId` / `groupPath`，剪切用 `clipMask`，ADR 0191）。坐标是
页面 mm、以 SVG 视口左上为原点；id 是临时的（`s1`… / 组 `sg1`…），前端落到画布时整批换新。

**安全**：只经 `svgcheck.parse_svg(tolerant=True)` 解析——与静态 SVG 素材、ADR 0182 场景解析**同一个受限解析器**（标准库
expat + 有界 TreeBuilder）：DTD / 实体（XXE、十亿笑声）、`<script>` 与一切非静态元素、`on*` 事件属性、外链与 `data:` 以外的 href、
`data:` 图片超限、引用环与指数展开、深度 / 元素数 / 属性 / 文字预算一律在这里被拒；`tolerant` 只多删编辑器私有命名空间与无作用的 CSS。
被拒的输入记一条原因、整个不拆。之后的换算另有视口上限（10 m、viewBox 1e7）与墙钟上限（5 秒，逐元素查）。这里只读几何与样式，
不执行、不联网、不读盘。

**兜底**：做不准的东西（滤镜、蒙版、图案填充、外链 / 内嵌图片、foreignObject、textPath、标记、复杂选择器……）**一个都不
悄悄丢**：记进 `unsupported`（原因码 + 次数），调用方据此把整个 SVG 当图片放进来并说一句原因，不出半张图。

几何规则：所有图形（path / rect / circle / ellipse / line / polyline / polygon）一律变成自由路径——变换矩阵烤进锚点（编辑起来
就是一条普通路径）；椭圆弧换成三次贝塞尔（每段 ≤ 90°）。线宽按变换的面积比例（√|det|）换成 pt。`<text>` 落成文字对象（字号、
颜色、粗斜、对齐、旋转），位置按基线估算。纯标准库。
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from . import svgcheck

MM_PER_PX = 25.4 / 96.0
MM_PER_PT = 25.4 / 72.0
MAX_BYTES = 8 * 1024 * 1024
MAX_ELEMENTS = 50_000
#: 一次导入里所有路径的锚点总数上限，与单条 `d` 的字符上限（防一条路径塞满 8 MB）
MAX_ANCHORS = 100_000
MAX_PATH_D_CHARS = 2 * 1024 * 1024
KAPPA_ARC_MAX = math.pi / 2

#: 原因码（前端按它说一句原因）
UNSUPPORTED_CODES = (
    "filter",
    "mask",
    "pattern",
    "image",
    "foreign_object",
    "text_path",
    "marker",
    "external_ref",
    "css_selector",
    "clip_units",
    "text_layout",
    "skew_text",
    "symbol_viewbox",
    "unsupported_element",
    "script",
    "reference_cycle",
)


class SvgImportError(ValueError):
    """不是 SVG / 太大 / 解析不了。`code` 稳定。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m1: Matrix, m2: Matrix) -> Matrix:
    """m1 · m2（先 m2 后 m1，作用于列向量）。"""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(_NUM)


def _nums(s: str) -> list[float]:
    return [float(x) for x in _NUM_RE.findall(s or "")]


def parse_transform(s: str | None) -> Matrix:
    m = IDENTITY
    if not s:
        return m
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", s):
        v = _nums(args)
        t: Matrix
        if name == "matrix" and len(v) == 6:
            t = tuple(v)  # type: ignore[assignment]
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            t = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0)
            if len(v) == 3:
                t = mul(mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = mul(m, t)
    return m


def _length(s: str | None, ref: float = 0.0, default: float = 0.0) -> float:
    """SVG 长度 → 用户单位（px）。百分比按 `ref`。"""
    if s is None:
        return default
    s = str(s).strip()
    m = re.match(rf"^({_NUM})\s*([a-z%]*)$", s)
    if not m:
        return default
    v, unit = float(m.group(1)), m.group(2)
    return {
        "": v,
        "px": v,
        "pt": v * 96 / 72,
        "pc": v * 16,
        "mm": v * 96 / 25.4,
        "cm": v * 96 / 2.54,
        "in": v * 96,
        "em": v * 16,
        "%": v / 100 * ref,
    }.get(unit, v)


# --------------------------------------------------------------------- 颜色
_NAMED = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
    "yellow": "#ffff00", "cyan": "#00ffff", "aqua": "#00ffff", "magenta": "#ff00ff", "fuchsia": "#ff00ff",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "maroon": "#800000", "olive": "#808000",
    "lime": "#00ff00", "teal": "#008080", "navy": "#000080", "purple": "#800080", "orange": "#ffa500",
    "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3",
    "darkblue": "#00008b", "darkred": "#8b0000", "darkgreen": "#006400", "steelblue": "#4682b4",
    "gold": "#ffd700", "pink": "#ffc0cb", "brown": "#a52a2a", "coral": "#ff7f50", "tomato": "#ff6347",
}  # fmt: skip


def parse_color(s: str | None, current: str = "#000000") -> tuple[str, float] | None:
    """颜色 → (#rrggbb, alpha)；none / transparent = None。认不出的当黑色（与浏览器对无效值的回退不同，但不丢对象）。"""
    if s is None:
        return None
    s = s.strip().lower()
    if s in ("none", "transparent", ""):
        return None
    if s == "currentcolor":
        return parse_color(current)
    if s in _NAMED:
        return _NAMED[s], 1.0
    if s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        if len(h) == 8:
            return "#" + h[:6], int(h[6:], 16) / 255
        if len(h) == 6 and re.fullmatch(r"[0-9a-f]{6}", h):
            return "#" + h, 1.0
        return "#000000", 1.0
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        vals = []
        for p in parts[:3]:
            v = float(p[:-1]) * 2.55 if p.endswith("%") else float(p)
            vals.append(max(0, min(255, round(v))))
        a = 1.0
        if len(parts) > 3:
            a = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
        return "#{:02x}{:02x}{:02x}".format(*vals), max(0.0, min(1.0, a))
    return "#000000", 1.0


# --------------------------------------------------------------------- CSS
INHERITED = {
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "font-family", "font-size", "font-weight",
    "font-style", "text-anchor", "color", "visibility",
}  # fmt: skip
STYLE_PROPS = (
    INHERITED
    | {"opacity", "display", "clip-path", "mask", "filter", "stop-color", "stop-opacity"}
    | {"marker-start", "marker-mid", "marker-end", "marker"}
)
_SIMPLE_SEL = re.compile(r"^([a-zA-Z][\w-]*)?((?:[.#][\w-]+)*)$")


@dataclass
class _Rule:
    tag: str | None
    classes: tuple[str, ...]
    ident: str | None
    spec: tuple[int, int, int]
    order: int
    decls: dict[str, str]


def _parse_decls(s: str) -> dict[str, str]:
    out = {}
    for part in s.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k = k.strip().lower()
            v = re.sub(r"\s*!important\s*$", "", v.strip())
            if k in STYLE_PROPS:
                out[k] = v
    return out


def parse_css(text: str, notes: dict[str, int], start: int = 0) -> list[_Rule]:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    # @font-face / @media 这类块：整块跳过（@media 里的规则不应用——打印 / 屏幕差异不影响导入几何）
    text = re.sub(r"@[\w-]+[^{;]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", text)
    text = re.sub(r"@[\w-]+[^;{]*;", "", text)
    rules: list[_Rule] = []
    order = start
    for sels, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        decls = _parse_decls(body)
        for sel in sels.split(","):
            sel = sel.strip()
            m = _SIMPLE_SEL.match(sel) if sel else None
            if not m or (not m.group(1) and not m.group(2)):
                if sel:
                    notes["css_selector"] = notes.get("css_selector", 0) + 1
                continue
            tag = m.group(1)
            parts = re.findall(r"([.#])([\w-]+)", m.group(2))
            classes = tuple(n for k, n in parts if k == ".")
            ids = [n for k, n in parts if k == "#"]
            order += 1
            rules.append(
                _Rule(
                    tag,
                    classes,
                    ids[0] if ids else None,
                    (len(ids), len(classes), 1 if tag else 0),
                    order,
                    decls,
                )
            )
    return rules


# --------------------------------------------------------------------- 路径
def _arc_to_cubics(x1, y1, rx, ry, phi, fa, fs, x2, y2):
    """SVG 椭圆弧（端点参数）→ 三次贝塞尔段 [(c1, c2, p), …]（W3C SVG 附录 B.2.4 的换算）。"""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [((x1, y1), (x2, y2), (x2, y2))]
    cphi, sphi = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cphi * dx + sphi * dy
    y1p = -sphi * dx + cphi * dy
    lam = x1p**2 / rx**2 + y1p**2 / ry**2
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cphi * cxp - sphi * cyp + (x1 + x2) / 2
    cy = sphi * cxp + cphi * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / KAPPA_ARC_MAX - 1e-9))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out = []

    def pt(t):
        ct, st = math.cos(t), math.sin(t)
        return (cx + rx * ct * cphi - ry * st * sphi, cy + rx * ct * sphi + ry * st * cphi)

    def der(t):
        ct, st = math.cos(t), math.sin(t)
        return (-rx * st * cphi - ry * ct * sphi, -rx * st * sphi + ry * ct * cphi)

    t = t1
    for _ in range(n):
        p0, p3 = pt(t), pt(t + step)
        d0, d3 = der(t), der(t + step)
        c1 = (p0[0] + k * d0[0], p0[1] + k * d0[1])
        c2 = (p3[0] - k * d3[0], p3[1] - k * d3[1])
        out.append((c1, c2, p3))
        t += step
    if out:
        c1, c2, _ = out[-1]
        out[-1] = (c1, c2, (x2, y2))
    return out


_CMD = re.compile(rf"([MmZzLlHhVvCcSsQqTtAa])|({_NUM})")


def parse_path_d(
    d: str, *, max_anchors: int = MAX_ANCHORS, deadline: float = math.inf
) -> list[dict]:
    """`d` → 子路径 [{closed, nodes: [{p, in, out}]}]（用户坐标，绝对；二次曲线精确升三次，弧换成三次）。

    单条路径自己带预算：锚点数超 `max_anchors` 抛 `too_many_elements`，墙钟过了 `deadline` 抛 `timeout`
    （元素级的预算管不到塞在一个 `d` 里的几十万段）。"""
    if len(d or "") > MAX_PATH_D_CHARS:
        raise SvgImportError("too_many_elements", "单条路径过长")
    toks = _CMD.findall(d or "")
    anchors = 0
    ticks = 0
    subs: list[dict] = []
    cur: dict | None = None
    x = y = 0.0
    sx = sy = 0.0
    last_c2: tuple[float, float] | None = None
    last_q: tuple[float, float] | None = None
    i = 0
    cmd = ""

    def num():
        nonlocal i
        while i < len(toks) and toks[i][0]:
            return None
        if i >= len(toks):
            return None
        v = float(toks[i][1])
        i += 1
        return v

    def flag():
        """弧的标志位可以紧挨着写（`a1 1 0 0110 10`）：取下一个数字的第一位。"""
        nonlocal i
        if i >= len(toks) or toks[i][0]:
            return None
        s = toks[i][1]
        if s[0] in "01" and len(s) > 1 and not s.startswith(("0.", "1.")):
            toks[i] = ("", s[1:])
            return float(s[0])
        i += 1
        return float(s)

    def start(px, py):
        nonlocal cur
        cur = {"closed": False, "nodes": [{"p": (px, py), "in": (px, py), "out": (px, py)}]}
        subs.append(cur)

    def spend(n=1):
        nonlocal anchors
        anchors += n
        if anchors > max_anchors:
            raise SvgImportError("too_many_elements", "路径锚点超出上限")

    def line_to(px, py):
        spend()
        cur["nodes"].append({"p": (px, py), "in": (px, py), "out": (px, py)})

    def curve_to(c1, c2, p):
        spend()
        cur["nodes"][-1]["out"] = c1
        cur["nodes"].append({"p": p, "in": c2, "out": p})

    while i < len(toks):
        ticks += 1
        if not ticks & 0x3FF and time.monotonic() > deadline:
            raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
            if cmd in "Zz":
                if cur is not None:
                    cur["closed"] = True
                    n = cur["nodes"]
                    # 闭合回到起点：最后一个锚点与起点重合时并掉（起点的入手柄接它）
                    if len(n) > 1 and n[-1]["p"] == n[0]["p"]:
                        n[0]["in"] = n[-1]["in"]
                        n.pop()
                x, y = sx, sy
                cur = None
                last_c2 = last_q = None
                continue
        elif not cmd:
            i += 1
            continue
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            a, b = num(), num()
            if a is None or b is None:
                break
            x, y = ox + a, oy + b
            sx, sy = x, y
            start(x, y)
            cmd = "l" if rel else "L"
            last_c2 = last_q = None
            continue
        if cur is None:
            start(x, y)
        if c == "L":
            a, b = num(), num()
            if a is None or b is None:
                break
            x, y = ox + a, oy + b
            line_to(x, y)
            last_c2 = last_q = None
        elif c == "H":
            a = num()
            if a is None:
                break
            x = ox + a
            line_to(x, y)
            last_c2 = last_q = None
        elif c == "V":
            a = num()
            if a is None:
                break
            y = oy + a
            line_to(x, y)
            last_c2 = last_q = None
        elif c == "C":
            v = [num() for _ in range(6)]
            if None in v:
                break
            c1 = (ox + v[0], oy + v[1])
            c2 = (ox + v[2], oy + v[3])
            x, y = ox + v[4], oy + v[5]
            curve_to(c1, c2, (x, y))
            last_c2, last_q = c2, None
        elif c == "S":
            v = [num() for _ in range(4)]
            if None in v:
                break
            c1 = (2 * x - last_c2[0], 2 * y - last_c2[1]) if last_c2 else (x, y)
            c2 = (ox + v[0], oy + v[1])
            x, y = ox + v[2], oy + v[3]
            curve_to(c1, c2, (x, y))
            last_c2, last_q = c2, None
        elif c in "QT":
            if c == "Q":
                v = [num() for _ in range(4)]
                if None in v:
                    break
                q = (ox + v[0], oy + v[1])
                ex, ey = ox + v[2], oy + v[3]
            else:
                v = [num() for _ in range(2)]
                if None in v:
                    break
                q = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                ex, ey = ox + v[0], oy + v[1]
            c1 = (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y))
            c2 = (ex + 2 / 3 * (q[0] - ex), ey + 2 / 3 * (q[1] - ey))
            x, y = ex, ey
            curve_to(c1, c2, (x, y))
            last_q, last_c2 = q, None
        elif c == "A":
            rx, ry, rot = num(), num(), num()
            fa, fs = flag(), flag()
            a, b = num(), num()
            if None in (rx, ry, rot, fa, fs, a, b):
                break
            ex, ey = ox + a, oy + b
            for c1, c2, p in _arc_to_cubics(
                x, y, rx, ry, math.radians(rot), int(fa), int(fs), ex, ey
            ):
                curve_to(c1, c2, p)
            x, y = ex, ey
            last_c2 = last_q = None
        else:
            i += 1
    return [s for s in subs if len(s["nodes"]) >= 1]


def _rect_d(x, y, w, h, rx, ry) -> list[dict]:
    if rx <= 0 and ry <= 0:
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return [{"closed": True, "nodes": [{"p": p, "in": p, "out": p} for p in pts]}]
    rx = min(rx or ry, w / 2)
    ry = min(ry or rx, h / 2)
    k = 0.5522847498
    kx, ky = rx * k, ry * k
    n = [
        {"p": (x + rx, y), "in": (x + rx - kx, y), "out": (x + rx, y)},
        {"p": (x + w - rx, y), "in": (x + w - rx, y), "out": (x + w - rx + kx, y)},
        {"p": (x + w, y + ry), "in": (x + w, y + ry - ky), "out": (x + w, y + ry)},
        {"p": (x + w, y + h - ry), "in": (x + w, y + h - ry), "out": (x + w, y + h - ry + ky)},
        {"p": (x + w - rx, y + h), "in": (x + w - rx + kx, y + h), "out": (x + w - rx, y + h)},
        {"p": (x + rx, y + h), "in": (x + rx, y + h), "out": (x + rx - kx, y + h)},
        {"p": (x, y + h - ry), "in": (x, y + h - ry + ky), "out": (x, y + h - ry)},
        {"p": (x, y + ry), "in": (x, y + ry), "out": (x, y + ry - ky)},
    ]
    return [{"closed": True, "nodes": n}]


def _ellipse_d(cx, cy, rx, ry) -> list[dict]:
    k = 0.5522847498
    kx, ky = rx * k, ry * k
    n = [
        {"p": (cx + rx, cy), "in": (cx + rx, cy - ky), "out": (cx + rx, cy + ky)},
        {"p": (cx, cy + ry), "in": (cx + kx, cy + ry), "out": (cx - kx, cy + ry)},
        {"p": (cx - rx, cy), "in": (cx - rx, cy + ky), "out": (cx - rx, cy - ky)},
        {"p": (cx, cy - ry), "in": (cx - kx, cy - ry), "out": (cx + kx, cy - ry)},
    ]
    return [{"closed": True, "nodes": n}]


def _cubic_extent(p0, p1, p2, p3):
    vals = [p0, p3]
    a = -p0 + 3 * p1 - 3 * p2 + p3
    b = 2 * (p0 - 2 * p1 + p2)
    c = p1 - p0
    ts = []
    if abs(a) < 1e-12:
        if abs(b) > 1e-12:
            ts.append(-c / b)
    else:
        disc = b * b - 4 * a * c
        if disc >= 0:
            r = math.sqrt(disc)
            ts += [(-b + r) / (2 * a), (-b - r) / (2 * a)]
    for t in ts:
        if 0 < t < 1:
            mt = 1 - t
            vals.append(mt**3 * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t**3 * p3)
    return min(vals), max(vals)


MIN_DIM_MM = 0.01


def fit_box(subs: list[dict]) -> tuple[float, float, float, float] | None:
    """曲线实际范围（与前端 `pathGeom.fitPathBox` 同一条：曲线极值、短边钳到 0.01 mm、居中补齐）。"""
    xs: list[float] = []
    ys: list[float] = []
    for sp in subs:
        ns = sp["nodes"]
        if len(ns) < 2:
            continue
        pairs = list(zip(ns, ns[1:])) + ([(ns[-1], ns[0])] if sp["closed"] else [])
        for a, b in pairs:
            for k, acc in ((0, xs), (1, ys)):
                lo, hi = _cubic_extent(a["p"][k], a["out"][k], b["in"][k], b["p"][k])
                acc += [lo, hi]
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if w < MIN_DIM_MM:
        x0 -= (MIN_DIM_MM - w) / 2
        w = MIN_DIM_MM
    if h < MIN_DIM_MM:
        y0 -= (MIN_DIM_MM - h) / 2
        h = MIN_DIM_MM
    return x0, y0, w, h


def _transform_subs(subs: list[dict], m: Matrix) -> list[dict]:
    return [
        {
            "closed": sp["closed"],
            "nodes": [{k: apply(m, *n[k]) for k in ("p", "in", "out")} for n in sp["nodes"]],
        }
        for sp in subs
    ]


def _r(v: float) -> float:
    return round(v, 6) + 0.0


def path_object(subs_mm: list[dict]) -> dict | None:
    """页面 mm 的子路径 → 路径对象的几何字段（框 + 比例坐标锚点；手柄与锚点重合不写）。"""
    subs_mm = [s for s in subs_mm if len(s["nodes"]) >= 2]
    box = fit_box(subs_mm)
    if box is None:
        return None
    x, y, w, h = box

    def frac(q):
        return [_r((q[0] - x) / w), _r((q[1] - y) / h)]

    out_subs = []
    for sp in subs_mm:
        nodes = []
        for n in sp["nodes"]:
            node = {"p": frac(n["p"])}
            if n["in"] != n["p"]:
                node["in"] = frac(n["in"])
            if n["out"] != n["p"]:
                node["out"] = frac(n["out"])
            nodes.append(node)
        out_subs.append({"closed": sp["closed"], "nodes": nodes})
    return {"x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h), "subpaths": out_subs}


# --------------------------------------------------------------------- 主流程
@dataclass
class _Ctx:
    ids: dict
    rules: list[_Rule]
    notes: dict[str, int] = field(default_factory=dict)
    objects: list[dict] = field(default_factory=list)
    seq: int = 0
    gseq: int = 0
    count: int = 0
    use_depth: int = 0
    anchors: int = 0
    deadline: float = math.inf

    def note(self, code: str) -> None:
        self.notes[code] = self.notes.get(code, 0) + 1


def _local(tag) -> str:
    return (
        tag.split("}", 1)[1]
        if isinstance(tag, str) and "}" in tag
        else (tag if isinstance(tag, str) else "")
    )


def _href(el) -> str | None:
    for k, v in el.attrib.items():
        if _local(k) == "href" or k == "href":
            return v
    return None


def _classes(el) -> tuple[str, ...]:
    return tuple((el.get("class") or "").split())


def _computed(el, parent: dict, ctx: _Ctx) -> dict:
    """样式级联：继承 → 表现属性 → CSS 规则（按优先级、出现顺序）→ style 属性。"""
    st = {k: v for k, v in parent.items() if k in INHERITED}
    for k in STYLE_PROPS:
        v = el.get(k)
        if v is not None:
            st[k] = v.strip()
    tag = _local(el.tag)
    cls = set(_classes(el))
    ident = el.get("id")
    matched = [
        r
        for r in ctx.rules
        if (r.tag is None or r.tag == tag)
        and all(c in cls for c in r.classes)
        and (r.ident is None or r.ident == ident)
    ]
    for r in sorted(matched, key=lambda r: (r.spec, r.order)):
        st.update(r.decls)
    if el.get("style"):
        st.update(_parse_decls(el.get("style")))
    if st.get("fill", "").strip().lower() == "inherit":
        st["fill"] = parent.get("fill", "black")
    return st


def _url_id(v: str | None) -> str | None:
    m = re.match(r"\s*url\(\s*['\"]?#([^'\")\s]+)['\"]?\s*\)", v or "")
    return m.group(1) if m else None


def _stops(el, ctx: _Ctx, seen=()) -> list[dict]:
    stops = []
    for s in el:
        if _local(s.tag) != "stop":
            continue
        st = _computed(s, {}, ctx)
        off = s.get("offset", "0").strip()
        o = (
            float(off[:-1]) / 100
            if off.endswith("%")
            else float(_nums(off)[0] if _nums(off) else 0)
        )
        col = parse_color(st.get("stop-color", "black")) or ("#000000", 0.0)
        alpha = col[1] * float(st.get("stop-opacity", "1") or 1)
        stop = {"offset": _r(max(0.0, min(1.0, o))), "color": col[0]}
        if alpha < 1:
            stop["opacity"] = _r(max(0.0, alpha))
        stops.append(stop)
    if not stops:
        ref = _href(el)
        if ref and ref.startswith("#") and ref[1:] in ctx.ids and ref[1:] not in seen:
            return _stops(ctx.ids[ref[1:]], ctx, (*seen, ref[1:]))
    return stops


def _grad_attr(el, name, ctx: _Ctx, seen=()):
    v = el.get(name)
    if v is not None:
        return v
    ref = _href(el)
    if ref and ref.startswith("#") and ref[1:] in ctx.ids and ref[1:] not in seen:
        return _grad_attr(ctx.ids[ref[1:]], name, ctx, (*seen, ref[1:]))
    return None


def _gradient(gid: str, ctx: _Ctx, bbox_user, ctm: Matrix, box_mm) -> tuple[dict | None, str]:
    """渐变 → 前端 `Gradient`（对象框的比例坐标）+ 退化用的纯色（位置最靠前的色标）。"""
    el = ctx.ids.get(gid)
    if el is None:
        return None, "#000000"
    kind = _local(el.tag)
    if kind == "pattern":
        ctx.note("pattern")
        return None, "#000000"
    if kind not in ("linearGradient", "radialGradient"):
        return None, "#000000"
    stops = _stops(el, ctx)
    if not stops:
        return None, "#000000"
    solid = sorted(stops, key=lambda s: s["offset"])[0]["color"]
    if len(stops) == 1:
        return None, solid
    units = _grad_attr(el, "gradientUnits", ctx) or "objectBoundingBox"
    gt = parse_transform(_grad_attr(el, "gradientTransform", ctx))
    bx, by, bw, bh = bbox_user
    x, y, w, h = box_mm

    def to_box(px: float, py: float) -> list[float]:
        if units == "objectBoundingBox":
            px, py = bx + px * bw, by + py * bh
        mx, my = apply(ctm, *apply(gt, px, py))
        return [_r((mx - x) / w), _r((my - y) / h)]

    def num(name, default, ref):
        v = _grad_attr(el, name, ctx)
        if v is None:
            return default
        v = v.strip()
        if v.endswith("%"):
            return float(v[:-1]) / 100 * (1 if units == "objectBoundingBox" else ref)
        return _length(v, ref)

    if kind == "linearGradient":
        p0 = to_box(num("x1", 0.0, bw), num("y1", 0.0, bh))
        p1 = to_box(num("x2", 1.0, bw), num("y2", 0.0, bh))
        return {"kind": "linear", "stops": stops, "from": p0, "to": p1}, solid
    cx, cy = num("cx", 0.5, bw), num("cy", 0.5, bh)
    r = num("r", 0.5, math.hypot(bw, bh) / math.sqrt(2))
    c = to_box(cx, cy)
    edge = to_box(cx + r, cy)
    # 半径换成「对象长边」的比例（前端 `gradient.readGradient` 的径向约定）
    rmm = math.hypot((edge[0] - c[0]) * w, (edge[1] - c[1]) * h)
    return {"kind": "radial", "stops": stops, "center": c, "radius": _r(rmm / max(w, h))}, solid


def _paint_fields(
    st: dict, ctx: _Ctx, ctm: Matrix, bbox_user, box_mm, scale_mm: float, opacity: float
) -> dict:
    """填充 / 描边样式 → 路径对象的字段（缺省不写，与前端同一套缺省）。"""
    out: dict = {}
    color_cur = st.get("color", "black")
    fill_v = st.get("fill", "black")
    gid = _url_id(fill_v)
    fill_op = float(st.get("fill-opacity", "1") or 1) * opacity
    if gid:
        g, solid = _gradient(gid, ctx, bbox_user, ctm, box_mm)
        out["fill"] = solid
        if g:
            out["fillGradient"] = g
    else:
        c = parse_color(fill_v, color_cur)
        out["fill"] = c[0] if c else None
        if c:
            fill_op *= c[1]
    if out["fill"] and fill_op < 1:
        out["fillOpacity"] = _r(max(0.0, fill_op))
    stroke_v = st.get("stroke", "none")
    sgid = _url_id(stroke_v)
    stroke_op = float(st.get("stroke-opacity", "1") or 1) * opacity
    if sgid:
        g, solid = _gradient(sgid, ctx, bbox_user, ctm, box_mm)
        out["color"] = solid
        if g:
            out["strokeGradient"] = g
        stroke_on = True
    else:
        c = parse_color(stroke_v, color_cur)
        stroke_on = c is not None
        out["color"] = c[0] if c else "#000000"
        if c:
            stroke_op *= c[1]
    sw_user = _length(st.get("stroke-width", "1"), 1.0, 1.0)
    out["strokePt"] = _r(max(sw_user * scale_mm / MM_PER_PT, 0.05)) if stroke_on else 1
    if not stroke_on or sw_user <= 0:
        out["stroke"] = False
    else:
        if stroke_op < 1:
            out["strokeOpacity"] = _r(max(0.0, stroke_op))
        cap = st.get("stroke-linecap", "butt")
        if cap in ("round", "square"):
            out["lineCap"] = cap
        join = st.get("stroke-linejoin", "miter")
        # 路径的缺省拐角是圆角（`pathGeom.PATH_DEFAULT_JOIN`）：SVG 缺省尖角，所以尖角 / 斜角要写明
        if join in ("miter", "bevel"):
            out["lineJoin"] = join
        ml = _nums(st.get("stroke-miterlimit", "4"))
        if ml and ml[0] != 10:
            out["miterLimit"] = _r(max(1.0, ml[0]))
        dash = st.get("stroke-dasharray", "none")
        if dash and dash != "none":
            d = [
                _length(v, 1.0) * scale_mm / MM_PER_PT
                for v in re.split(r"[,\s]+", dash.strip())
                if v
            ]
            if len(d) % 2:
                d = d * 2
            if d and any(v > 0 for v in d):
                out["dashPattern"] = [_r(v) for v in d]
    if st.get("fill-rule") == "evenodd":
        out["fillRule"] = "evenodd"
    return out


def _bbox(subs: list[dict]):
    b = fit_box(subs)
    return b if b else (0.0, 0.0, 1.0, 1.0)


def _new_id(ctx: _Ctx) -> str:
    ctx.seq += 1
    return f"s{ctx.seq}"


def _new_gid(ctx: _Ctx) -> str:
    ctx.gseq += 1
    return f"sg{ctx.gseq}"


def _emit_shape(subs_user, st, ctm, ctx: _Ctx, path: list[str], opacity: float) -> dict | None:
    subs_mm = _transform_subs(subs_user, ctm)
    geom = path_object(subs_mm)
    if geom is None:
        return None
    scale = math.sqrt(abs(ctm[0] * ctm[3] - ctm[1] * ctm[2]))
    paint = _paint_fields(
        st,
        ctx,
        ctm,
        _bbox(subs_user),
        (geom["x"], geom["y"], geom["w"], geom["h"]),
        scale,
        opacity,
    )
    if paint.get("fill") is None and paint.get("stroke") is False:
        return None  # 既不填也不描：不画（与画布同一条）
    obj = {"id": _new_id(ctx), "type": "path", **geom, **paint}
    if path:
        obj["groupId"] = path[-1]
        if len(path) >= 2:
            obj["groupPath"] = list(path)
    return obj


def _geometry(el, tag: str, ctx: _Ctx | None = None) -> list[dict] | None:
    g = el.get
    if tag == "path":
        if ctx is None:
            return parse_path_d(g("d") or "")
        # 总锚点预算跨整次导入共享（`<use>` 反复引用同一条路径会反复展开）；单条再带墙钟
        subs = parse_path_d(
            g("d") or "", max_anchors=MAX_ANCHORS - ctx.anchors, deadline=ctx.deadline
        )
        ctx.anchors += sum(len(sp["nodes"]) for sp in subs)
        return subs
    if tag == "rect":
        w, h = _length(g("width")), _length(g("height"))
        if w <= 0 or h <= 0:
            return None
        return _rect_d(
            _length(g("x")), _length(g("y")), w, h, _length(g("rx"), 0, -1), _length(g("ry"), 0, -1)
        )
    if tag == "circle":
        r = _length(g("r"))
        return _ellipse_d(_length(g("cx")), _length(g("cy")), r, r) if r > 0 else None
    if tag == "ellipse":
        rx, ry = _length(g("rx")), _length(g("ry"))
        return _ellipse_d(_length(g("cx")), _length(g("cy")), rx, ry) if rx > 0 and ry > 0 else None
    if tag == "line":
        a = (_length(g("x1")), _length(g("y1")))
        b = (_length(g("x2")), _length(g("y2")))
        return [
            {"closed": False, "nodes": [{"p": a, "in": a, "out": a}, {"p": b, "in": b, "out": b}]}
        ]
    if tag in ("polyline", "polygon"):
        v = _nums(g("points") or "")
        pts = [(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]
        if len(pts) < 2:
            return None
        return [{"closed": tag == "polygon", "nodes": [{"p": p, "in": p, "out": p} for p in pts]}]
    return None


_SERIF = (
    "serif",
    "times",
    "georgia",
    "garamond",
    "cambria",
    "minion",
    "songti",
    "simsun",
    "宋",
    "palatino",
)
_SANS = (
    "sans",
    "arial",
    "helvetica",
    "calibri",
    "verdana",
    "segoe",
    "roboto",
    "myriad",
    "heiti",
    "黑",
    "inter",
    "lato",
)
_MONO = ("mono", "courier", "consolas", "menlo")


def _family(s: str | None) -> str | None:
    f = (s or "").lower()
    if any(k in f for k in _MONO):
        return "monospace"
    if "sans" in f or any(k in f for k in _SANS[1:]):
        return "sans-serif"
    if any(k in f for k in _SERIF):
        return None  # 衬线 = 缺省，不写字段
    return None if not f else "sans-serif"


def _emit_text(el, st, ctm, ctx: _Ctx, path: list[str], opacity: float) -> None:
    """`<text>` → 文字对象：每个有自己 x / y 的 tspan 各成一个；dx / dy / rotate / textPath 做不准 → 记不支持。"""
    runs: list[tuple[float, float, str, dict]] = []
    x0 = _nums(el.get("x") or "0")
    y0 = _nums(el.get("y") or "0")
    cx, cy = (x0[0] if x0 else 0.0), (y0[0] if y0 else 0.0)
    if any(el.get(a) for a in ("dx", "dy", "rotate", "textLength")) or len(x0) > 1 or len(y0) > 1:
        ctx.note("text_layout")
        return
    if el.text and el.text.strip():
        runs.append((cx, cy, el.text, st))
    for child in el:
        t = _local(child.tag)
        if t == "textPath":
            ctx.note("text_path")
            return
        if t != "tspan":
            continue
        cst = _computed(child, st, ctx)
        if any(child.get(a) for a in ("dx", "dy", "rotate", "textLength")):
            ctx.note("text_layout")
            return
        xs, ys = _nums(child.get("x") or ""), _nums(child.get("y") or "")
        if len(xs) > 1 or len(ys) > 1:
            ctx.note("text_layout")
            return
        tx, ty = (xs[0] if xs else None), (ys[0] if ys else None)
        text = "".join(child.itertext())
        if tx is None and ty is None and runs:
            # 同一行接着写：并到上一段（样式取上一段的）
            px, py, ptxt, pst = runs[-1]
            runs[-1] = (px, py, ptxt + text, pst)
        elif text.strip():
            runs.append((tx if tx is not None else cx, ty if ty is not None else cy, text, cst))
        if child.tail and child.tail.strip() and runs:
            px, py, ptxt, pst = runs[-1]
            runs[-1] = (px, py, ptxt + child.tail, pst)
    a, b, c, d = ctm[:4]
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    det = a * d - b * c
    if det <= 0 or abs(a * c + b * d) > 1e-6 * sx * sy or abs(sx - sy) > 1e-6 * max(sx, sy):
        # 斜切 / 非等比 / 镜像的文字：文字对象表达不了
        ctx.note("skew_text")
        return
    rot = math.degrees(math.atan2(b, a))
    for rx, ry, text, rst in runs:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        size_user = _length(rst.get("font-size", "16"), 16, 16)
        size_pt = size_user * sx * MM_PER_PX / MM_PER_PT
        size_mm = size_pt * MM_PER_PT
        col = parse_color(rst.get("fill", "black"), rst.get("color", "black"))
        if _url_id(rst.get("fill")):
            g, solid = _gradient(_url_id(rst.get("fill")), ctx, (0, 0, 1, 1), ctm, (0, 0, 1, 1))
            col = (solid, 1.0)
        # 字宽估算：拉丁字符 0.55 em、CJK 1 em（前端 TextView 落定后按实际量出来的宽写回）
        width_mm = sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in text) * size_mm + 0.5
        anchor = rst.get("text-anchor", "start")
        align = {"middle": "center", "end": "right"}.get(anchor, "left")
        bx, by = apply(ctm, rx, ry)
        bx, by = bx * MM_PER_PX, by * MM_PER_PX
        # 基线 → 框：框顶 = 基线 − 0.8 em（行高 1.2 的上半），左右按对齐方式
        ux, uy = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        vx, vy = -uy, ux
        off = {"left": 0.0, "center": -width_mm / 2, "right": -width_mm}[align]
        tlx = bx + off * ux - 0.8 * size_mm * vx
        tly = by + off * uy - 0.8 * size_mm * vy
        h = size_mm * 1.2
        # 框是绕中心转的：由旋转后的左上角反推未旋转的 x / y
        cxm = tlx + ux * width_mm / 2 + vx * h / 2
        cym = tly + uy * width_mm / 2 + vy * h / 2
        weight = (rst.get("font-weight") or "normal").lower()
        # Illustrator 把粗斜写进 PostScript 名（`Arial-BoldMT`、`Helvetica-Oblique`），不写 font-weight
        ps_name = (rst.get("font-family") or "").lower()
        obj = {
            "id": _new_id(ctx),
            "type": "text",
            "x": _r(cxm - width_mm / 2),
            "y": _r(cym - h / 2),
            "w": _r(width_mm),
            "h": _r(h),
            "text": text,
            "sizePt": _r(size_pt),
            "bold": weight in ("bold", "bolder")
            or (weight.isdigit() and int(weight) >= 600)
            or bool(re.search(r"-(?:semi|demi|extra)?bold|black|heavy", ps_name)),
            "italic": (rst.get("font-style") or "").lower() in ("italic", "oblique")
            or bool(re.search(r"-\w*(?:italic|oblique)", ps_name)),
            "color": col[0] if col else "#000000",
            "align": align,
        }
        fam = _family(rst.get("font-family"))
        if fam:
            obj["fontFamily"] = fam
        if abs(rot) > 1e-6:
            obj["rotationDeg"] = _r(rot)
        if path:
            obj["groupId"] = path[-1]
            if len(path) >= 2:
                obj["groupPath"] = list(path)
        ctx.objects.append(obj)


def _clip_mask(cid: str, ctm: Matrix, ctx: _Ctx, bbox_ctm: Matrix) -> list[dict] | None:
    """`clipPath` → 页面 mm 的子路径（多个子图形并成一条复合路径）。"""
    el = ctx.ids.get(cid)
    if el is None or _local(el.tag) != "clipPath":
        return None
    if (el.get("clipPathUnits") or "userSpaceOnUse") != "userSpaceOnUse":
        ctx.note("clip_units")
        return None
    m = mul(ctm, parse_transform(el.get("transform")))
    subs: list[dict] = []
    for child in el:
        tag = _local(child.tag)
        if tag == "use":
            ref = _href(child)
            if not ref or not ref.startswith("#") or ref[1:] not in ctx.ids:
                continue
            target = ctx.ids[ref[1:]]
            cm = mul(
                mul(m, parse_transform(child.get("transform"))),
                (1, 0, 0, 1, _length(child.get("x")), _length(child.get("y"))),
            )
            g = _geometry(target, _local(target.tag), ctx)
            if g:
                subs += _transform_subs(g, mul(cm, parse_transform(target.get("transform"))))
            continue
        g = _geometry(child, tag, ctx)
        if g:
            subs += _transform_subs(g, mul(m, parse_transform(child.get("transform"))))
    return subs or None


def _walk(el, st_parent: dict, ctm: Matrix, path: list[str], ctx: _Ctx, opacity: float) -> None:
    ctx.count += 1
    if ctx.count > IMPORT_BUDGET.max_expanded_elements:
        raise SvgImportError("too_many_elements", "展开后的元素超出上限")
    if time.monotonic() > ctx.deadline:
        raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
    tag = _local(el.tag)
    if tag in (
        "defs", "title", "desc", "metadata", "style", "script", "linearGradient",
        "radialGradient", "clipPath", "symbol", "pattern", "marker", "mask", "filter",
    ) or not tag:  # fmt: skip
        return
    st = _computed(el, st_parent, ctx)
    if st.get("display") == "none" or st.get("visibility") in ("hidden", "collapse"):
        return
    if tag in ("image", "foreignObject"):
        ctx.note("image" if tag == "image" else "foreign_object")
        return
    for prop, code in (("filter", "filter"), ("mask", "mask")):
        if st.get(prop) and st.get(prop) != "none":
            ctx.note(code)
            return
    if any(
        st.get(k) and st.get(k) != "none"
        for k in ("marker", "marker-start", "marker-mid", "marker-end")
    ):
        ctx.note("marker")
    m = mul(ctm, parse_transform(el.get("transform")))
    op = opacity * float(st.get("opacity", "1") or 1)
    # 剪切：这个元素（连同它的子孙）自成一层组，蒙版是组里的最上面一个
    clip_id = _url_id(st.get("clip-path"))
    if clip_id:
        clip_subs = _clip_mask(clip_id, m, ctx, m)
        if clip_subs is None:
            if clip_id not in ctx.ids:
                ctx.note("external_ref")
        else:
            gid = _new_gid(ctx)
            inner = [*path, gid]
            start = len(ctx.objects)
            st2 = {**st, "clip-path": "none"}
            _walk_inner(el, tag, st2, m, inner, ctx, op)
            if len(ctx.objects) > start:
                geom = path_object(_transform_subs(clip_subs, (MM_PER_PX, 0, 0, MM_PER_PX, 0, 0)))
                if geom:
                    mask = {
                        "id": _new_id(ctx),
                        "type": "path",
                        **geom,
                        "strokePt": 1,
                        "color": "#000000",
                        "fill": None,
                        "stroke": False,
                        "groupId": gid,
                        "clipMask": True,
                        "hidden": True,
                    }
                    if len(inner) >= 2:
                        mask["groupPath"] = list(inner)
                    ctx.objects.append(mask)
            return
    _walk_inner(el, tag, st, m, path, ctx, op)


def _walk_inner(el, tag: str, st: dict, m: Matrix, path: list[str], ctx: _Ctx, op: float) -> None:
    to_mm = mul((MM_PER_PX, 0, 0, MM_PER_PX, 0, 0), m)
    if tag in ("g", "a", "switch"):
        kids = [c for c in el if isinstance(c.tag, str)]
        drawable = [
            c for c in kids if _local(c.tag) not in ("title", "desc", "metadata", "defs", "style")
        ]
        # 只有一个子对象的组不成组（Illustrator 的「图层」包装一层很常见），两个以上才成组
        inner = [*path, _new_gid(ctx)] if len(drawable) >= 2 and path is not None else path
        for c in kids:
            _walk(c, st, m, inner, ctx, op)
        return
    if tag == "svg":
        vb = _nums(el.get("viewBox") or "")
        x, y = _length(el.get("x")), _length(el.get("y"))
        inner_m = mul(m, (1, 0, 0, 1, x, y))
        if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
            w = _length(el.get("width"), vb[2], vb[2])
            h = _length(el.get("height"), vb[3], vb[3])
            k = min(w / vb[2], h / vb[3])
            inner_m = mul(inner_m, (k, 0, 0, k, -vb[0] * k, -vb[1] * k))
        for c in el:
            if isinstance(c.tag, str):
                _walk(c, st, inner_m, path, ctx, op)
        return
    if tag == "use":
        ref = _href(el)
        if not ref or not ref.startswith("#"):
            ctx.note("external_ref")
            return
        target = ctx.ids.get(ref[1:])
        if target is None or ctx.use_depth > 16:
            ctx.note("external_ref")
            return
        um = mul(m, (1, 0, 0, 1, _length(el.get("x")), _length(el.get("y"))))
        ctx.use_depth += 1
        try:
            if _local(target.tag) == "symbol":
                if target.get("viewBox"):
                    ctx.note("symbol_viewbox")
                    return
                kids = [c for c in target if isinstance(c.tag, str)]
                inner = [*path, _new_gid(ctx)] if len(kids) >= 2 else path
                sst = _computed(target, st, ctx)
                for c in kids:
                    _walk(c, sst, um, inner, ctx, op)
            else:
                _walk(target, st, um, path, ctx, op)
        finally:
            ctx.use_depth -= 1
        return
    if tag == "text":
        _emit_text(el, st, m, ctx, path, op)
        return
    geom = _geometry(el, tag, ctx)
    if geom is None:
        return
    obj = _emit_shape(geom, st, to_mm, ctx, path, op)
    if obj is not None:
        ctx.objects.append(obj)


#: 导入用的预算：比静态 SVG 素材（`svgcheck.SVGBudget` 缺省）更紧——这是粘贴进来的一段，不是一张图的产物
IMPORT_BUDGET = svgcheck.SVGBudget(
    max_bytes=MAX_BYTES,
    max_depth=64,
    max_elements=MAX_ELEMENTS,
    max_image_bytes=4 * 1024 * 1024,
    max_image_pixels=40_000_000,
    max_expanded_elements=200_000,
)
#: 视口（width / height / viewBox）的上限：一张图不会超过 10 m；更大的多半是故意撑爆的坐标
MAX_VIEWPORT_MM = 10_000.0
MAX_VIEWBOX = 1e7
#: 解析与换算的墙钟上限（秒）：逐元素、逐个路径命令查，超时就停（`timeout`）
DEADLINE_S = 5.0

#: `svgcheck` 的拒绝码 → 本模块的原因码（前端按它说一句原因）
_CHECK_CODES = {
    "svg_resource_limit": "too_large",
    "svg_dtd_forbidden": "external_ref",
    "svg_external_resource": "external_ref",
    "svg_missing_reference": "external_ref",
    "svg_reference_cycle": "reference_cycle",
    "svg_unsafe_element": "unsupported_element",
    "svg_unsafe_attribute": "script",
    "svg_unsafe_css": "css_selector",
    "svg_foreign_namespace": "unsupported_element",
    "svg_invalid_id": "bad_xml",
    "svg_invalid_root": "not_svg",
    "svg_unparseable": "bad_xml",
    "svg_encoding_unsupported": "bad_xml",
    "svg_processing_instruction_forbidden": "bad_xml",
    "svg_invalid_image": "image",
}


def _parse(svg_text: str):
    """唯一的入口是 `svgcheck.parse_svg`（与静态 SVG 素材、ADR 0182 的场景解析同一个受限解析器），`tolerant` 模式：只多删掉
    编辑器私有命名空间与无作用的 CSS，安全规则一条不放。被拒绝的输入根本到不了下面的换算。"""
    data = svg_text.encode("utf-8") if isinstance(svg_text, str) else svg_text
    if len(data) > MAX_BYTES:
        raise SvgImportError("too_large", f"SVG 超过 {MAX_BYTES // 1024 // 1024} MB")
    if not re.search(rb"<svg[\s>]", data[:65536]):
        raise SvgImportError("not_svg", "没有 <svg> 元素")
    try:
        return svgcheck.parse_svg(data, budget=IMPORT_BUDGET, tolerant=True)
    except svgcheck.SVGError as exc:
        code = _CHECK_CODES.get(exc.code, "bad_xml")
        if code in ("bad_xml", "not_svg", "too_large"):
            raise SvgImportError(code, exc.code) from exc
        raise _Rejected(code) from exc


class _Rejected(Exception):
    """受限解析器拒绝了，但不是「不是 SVG」：当成一条做不准（`unsupported`），调用方整个当图片"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def import_svg(svg_text: str) -> dict:
    """SVG 文本 → `{objects, size_mm: [w, h], unsupported: {code: count}}`。

    `unsupported` 非空 = 有做不准的东西，调用方应把整个 SVG 当图片放进来（`objects` 仍给出，只供诊断）。"""
    try:
        root = _parse(svg_text)
    except _Rejected as rej:
        return {"objects": [], "size_mm": [0.0, 0.0], "unsupported": {rej.code: 1}}
    ids = {}
    css = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.get("id"):
            ids.setdefault(el.get("id"), el)
        if _local(el.tag) == "style" and el.text:
            css.append(el.text)
    notes: dict[str, int] = {}
    rules: list[_Rule] = []
    for c in css:
        rules += parse_css(c, notes, len(rules))
    ctx = _Ctx(ids=ids, rules=rules, notes=notes, deadline=time.monotonic() + DEADLINE_S)
    # 视口：width / height（缺省按 viewBox，单位 px = 1/96 in）；viewBox 等比放进视口（preserveAspectRatio 缺省 xMidYMid meet）
    vb = _nums(root.get("viewBox") or "")
    vbw, vbh = (vb[2], vb[3]) if len(vb) == 4 and vb[2] > 0 and vb[3] > 0 else (None, None)
    w = _length(root.get("width"), vbw or 300, vbw or 300) if root.get("width") else (vbw or 300.0)
    h = (
        _length(root.get("height"), vbh or 150, vbh or 150)
        if root.get("height")
        else (vbh or 150.0)
    )
    if (
        not all(math.isfinite(v) for v in (w, h, *vb))
        or max(w, h) * MM_PER_PX > MAX_VIEWPORT_MM
        or (vbw and max(abs(v) for v in vb) > MAX_VIEWBOX)
    ):
        raise SvgImportError("too_large", "视口或 viewBox 超出上限")
    m = IDENTITY
    if vbw:
        k = min(w / vbw, h / vbh)
        tx = (w - vbw * k) / 2 - vb[0] * k
        ty = (h - vbh * k) / 2 - vb[1] * k
        m = (k, 0, 0, k, tx, ty)
    st_root = _computed(root, {}, ctx)
    for c in root:
        if isinstance(c.tag, str):
            _walk(c, st_root, mul(m, IDENTITY), [], ctx, float(st_root.get("opacity", "1") or 1))
    # 只剩一个成员的组（子对象被跳过了）不算组：拿掉这一层
    _prune_groups(ctx.objects)
    return {
        "objects": ctx.objects,
        "size_mm": [_r(w * MM_PER_PX), _r(h * MM_PER_PX)],
        "unsupported": dict(sorted(ctx.notes.items())),
    }


def _prune_groups(objs: list[dict]) -> None:
    from collections import Counter

    def chain(o):
        p = o.get("groupPath")
        return list(p) if p else ([o["groupId"]] if o.get("groupId") else [])

    for _ in range(8):
        counts = Counter(g for o in objs for g in set(chain(o)))
        lonely = {g for g, n in counts.items() if n < 2}
        # 包住全部对象的那一层（SVG 写入器为翻 y 包的根组、Inkscape 的图层）：粘贴后本来就全选着，再包一层只是多一次双击
        top = chain(objs[0])[:1] if objs else []
        clip_groups = {chain(o)[-1] for o in objs if o.get("clipMask") and chain(o)}
        if top and counts.get(top[0]) == len(objs) and top[0] not in clip_groups:
            lonely.add(top[0])
        if not lonely:
            return
        for o in objs:
            p = [g for g in chain(o) if g not in lonely]
            o.pop("groupPath", None)
            o.pop("groupId", None)
            if p:
                o["groupId"] = p[-1]
                if len(p) >= 2:
                    o["groupPath"] = p
            if o.get("clipMask") and not p:
                o.pop("clipMask", None)
                o.pop("hidden", None)
