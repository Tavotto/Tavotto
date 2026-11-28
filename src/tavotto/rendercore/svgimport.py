"""外部 SVG → 画布上可编辑的对象（ADR 0189）。

输入是**不受信任**的 SVG 文本（Illustrator / Inkscape / BioRender 之类导出或复制出来的），输出是画布文档里的对象
（前端 `types/document` 的形状：`path` / `text`，组用 `groupId` / `groupPath`，剪切用 `clipMask`，ADR 0191）。坐标是
页面 mm、以 SVG 视口左上为原点；id 是临时的（`s1`… / 组 `sg1`…），前端落到画布时整批换新。

**安全**：只经 `svgcheck.parse_svg(tolerant=True)` 解析——与静态 SVG 素材、ADR 0182 场景解析**同一个受限解析器**（标准库
expat + 有界 TreeBuilder）：DTD / 实体（XXE、十亿笑声）、`<script>` 与一切非静态元素、`on*` 事件属性、外链与 `data:` 以外的 href、
`data:` 图片超限、引用环与指数展开、深度 / 元素数 / 属性 / 文字预算一律在这里被拒；`tolerant` 只多删编辑器私有命名空间与无作用的 CSS。
被拒的输入记一条原因、整个不拆。之后的换算另有视口上限（10 m、viewBox 1e7）与墙钟上限（5 秒，逐元素查）。这里只读几何与样式，
不执行、不联网、不读盘。

**兜底**：做不准的东西（滤镜、蒙版、图案填充、外链 / 内嵌图片、foreignObject、textPath、复杂的线端标记、复杂选择器……）**一个都不
悄悄丢**：记进 `unsupported`（原因码 + 次数），调用方据此把整个 SVG 当图片放进来并说一句原因，不出半张图。

几何规则：所有图形（path / rect / circle / ellipse / line / polyline / polygon）一律变成自由路径——变换矩阵烤进锚点（编辑起来
就是一条普通路径）；椭圆弧换成三次贝塞尔（每段 ≤ 90°）。线宽按变换的面积比例（√|det|）换成 pt。`<text>` 落成文字对象（字号、
颜色、粗斜、对齐、旋转；tspan 的上下标与局部粗斜写成 LaTeX 子集），位置按基线估算。简单三角 / 开口箭头 / 圆点的 `marker-start / marker-end`
换成路径的 `headStart / headEnd`（ADR 0208 第 5 期）。纯标准库。
"""

from __future__ import annotations

import math
import re
import sys
import time
from dataclasses import dataclass, field

from .. import richtext
from . import svgcheck

MM_PER_PX = 25.4 / 96.0
MM_PER_PT = 25.4 / 72.0
MAX_BYTES = 8 * 1024 * 1024
MAX_ELEMENTS = 50_000
#: 一次导入里所有路径的锚点总数上限，与单条 `d` 的字符上限（防一条路径塞满 8 MB）
MAX_ANCHORS = 100_000
MAX_PATH_D_CHARS = 2 * 1024 * 1024
#: 一条 `transform` 属性的字符上限与调用个数上限（逐字符 / 逐调用的 Python 循环，没有墙钟可查；再长的不是人写的变换）
MAX_TRANSFORM_CHARS = 256 * 1024
MAX_TRANSFORM_CALLS = 10_000
#: 单个渐变的色标数上限
MAX_STOPS = 1_000
#: `stroke-dasharray` 的项数上限
MAX_DASH_ITEMS = 1_000
#: 导入后 CSS 规则（选择器）总数上限
MAX_CSS_SELECTORS = 10_000
#: 渐变 `href` 继承链最多跟这么多跳（再长的链不是人写的；同时避免递归过深）
MAX_HREF_HOPS = 32
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


#: 无歧义的数值语法：`\d+(?:\.\d*)?` 与原来的 `\d+\.?\d*` 接受同一批字符串，但一串数字只有一种划分，回溯不会平方级
#: （原写法遇到「几万个 9 再接非法后缀」要十几秒，Codex 线程 4656678185）
_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(_NUM)
#: 单个数值 token 的字符上限：再长的不是坐标（float 的有效位数也就十几位），按非法处理
MAX_NUM_CHARS = 64


def _nums(s: str) -> list[float]:
    if s and len(s) > MAX_PATH_D_CHARS:
        raise SvgImportError(
            "too_many_elements", "数值列表过长"
        )  # 几百万个数的属性：逐个 float 没有墙钟可查
    toks = _NUM_RE.findall(s or "")
    if any(len(t) > MAX_NUM_CHARS for t in toks):
        return []
    return [float(x) for x in toks]


def _transform_calls(s: str):
    """`name ( args )` 逐个切出来：单趟线性扫描（原来的 `([a-zA-Z]+)\\s*\\(([^)]*)\\)` 遇到一长串字母、或 `A((((…`
    这种没有右括号的输入会从每个起点重扫一遍，是平方级回溯，CodeQL py/polynomial-redos）"""
    i, n = 0, len(s)
    while i < n:
        if not s[i].isascii() or not s[i].isalpha():
            i += 1
            continue
        j = i
        while j < n and s[j].isascii() and s[j].isalpha():
            j += 1
        name = s[i:j]
        while j < n and s[j].isspace():
            j += 1
        if j < n and s[j] == "(":
            k = s.find(")", j + 1)
            if k < 0:
                return
            yield name, s[j + 1 : k]
            i = k + 1
        else:
            i = j


def parse_transform(s: str | None) -> Matrix:
    m = IDENTITY
    if not s:
        return m
    if len(s) > MAX_TRANSFORM_CHARS:
        raise SvgImportError("too_many_elements", "transform 过长")
    for calls, (name, args) in enumerate(_transform_calls(s)):
        if calls >= MAX_TRANSFORM_CALLS:
            raise SvgImportError("too_many_elements", "transform 调用过多")
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


_LENGTH_RE = re.compile(rf"({_NUM})\s*([a-z%]*)\Z")


def _length(s: str | None, ref: float = 0.0, default: float = 0.0) -> float:
    """SVG 长度 → 用户单位（px）。百分比按 `ref`。"""
    if s is None:
        return default
    s = str(s).strip()
    if len(s) > 2 * MAX_NUM_CHARS:
        return default
    m = _LENGTH_RE.match(s)
    if not m or len(m.group(1)) > MAX_NUM_CHARS:
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
    | {"baseline-shift", "vertical-align"}
    | {
        "paint-order",
        "vector-effect",
        "mix-blend-mode",
        "isolation",
        "shape-rendering",
        "transform",
        "stroke-dashoffset",
    }  # 只给标记判定用  # 上下标（只 tspan 用；不继承，不在 INHERITED 里）
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
            v = v.strip()
            # 去掉结尾的 !important：不用 `\\s*!important\\s*$`（一长串空格时平方级回溯，CodeQL py/polynomial-redos）
            if v.endswith("!important"):
                v = v[: -len("!important")].rstrip()
            if k in STYLE_PROPS:
                out[k] = v
    return out


def parse_css(
    text: str, notes: dict[str, int], start: int = 0, deadline: float = math.inf
) -> list[_Rule]:
    # 注释 / @ 规则：进到这里的样式表已经过 `svgcheck._stylesheet` 的校验（选择器与声明里都不许有 `/*`、`@`），实际走不到；
    # 先查有没有再替换，避免对根本没有它们的文本白扫
    if "/*" in text:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    # @font-face / @media 这类块：整块跳过（@media 里的规则不应用——打印 / 屏幕差异不影响导入几何）
    if "@" in text:
        text = re.sub(r"@[\w-]+[^{;]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", text)
        text = re.sub(r"@[\w-]+[^;{]*;", "", text)
    rules: list[_Rule] = []
    order = start
    # 与 `([^{}]+)\{([^{}]*)\}` 的 findall 同一语义，但是线性扫描（那个正则对「合法规则后面跟一大串空白」会在每个起点重试，
    # 平方级，Codex 线程 4656811230）
    ticks = 0
    for _s, _e, sels, body in svgcheck._iter_rules(text):
        decls = _parse_decls(body)
        for sel in sels.split(","):
            ticks += 1
            if not ticks & 0xFF and time.monotonic() > deadline:
                raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
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
            if (
                order > MAX_CSS_SELECTORS
            ):  # `order` 从 `start`（已累计的规则数）接着数，是全文档的总数
                raise SvgImportError("too_many_elements", "CSS 选择器过多")
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


def _same_point(a: tuple[float, float], b: tuple[float, float], segments: int = 0) -> bool:
    """子路径终点和起点算不算同一个点。逐位相等一定算；否则只吸收**浮点累加噪声**。

    容差按浮点误差上界算、不按坐标比例：从起点起经过 `segments` 段相对坐标累加，误差不超过 `segments × ε × 坐标量级`，
    取 `(segments + 1) × 4 × ε × max(|a|, |b|, 1)`（ε = `sys.float_info.epsilon`）。远离原点时它也只有 1e-9 量级，
    真实的小缺口（SVG 数字写 3 位小数就是 1e-3，写 .0005 也是）不会被并掉；`segments = 0` 退化成几乎逐位相等。"""
    eps = sys.float_info.epsilon
    ok = True
    for u, v in zip(a, b):
        tol = (segments + 1) * 4 * eps * max(abs(u), abs(v), 1.0)
        ok = ok and abs(u - v) <= tol
    return ok


def parse_path_d(
    d: str, *, max_anchors: int = MAX_ANCHORS, deadline: float = math.inf
) -> list[dict]:
    """`d` → 子路径 [{closed, nodes: [{p, in, out}]}]（用户坐标，绝对；二次曲线精确升三次，弧换成三次）。

    单条路径自己带预算：锚点数超 `max_anchors` 抛 `too_many_elements`，墙钟过了 `deadline` 抛 `timeout`
    （元素级的预算管不到塞在一个 `d` 里的几十万段）。"""
    if len(d or "") > MAX_PATH_D_CHARS:
        raise SvgImportError("too_many_elements", "单条路径过长")
    toks = _CMD.findall(d or "")
    if any(len(t[1]) > MAX_NUM_CHARS for t in toks):
        return []  # 数值 token 过长 = 非法：整条当没有几何
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
        spend()  # 子路径的起点也是锚点：与线段 / 曲线同一个预算（Codex 线程 4656811246）
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
                    # 闭合回到起点：最后一个锚点与起点重合时并掉（起点的入手柄接它）。
                    # 相对坐标（`c` / `l`）累加回来的终点和起点常差浮点噪声，不是逐位相等，所以按相对容差判重合
                    if len(n) > 1 and _same_point(n[-1]["p"], n[0]["p"], len(n) - 1):
                        # 末段是直线（入手柄就是它自己的锚点）：首锚点的入手柄取首锚点本身，不带进浮点噪声
                        n[0]["in"] = n[0]["p"] if n[-1]["in"] == n[-1]["p"] else n[-1]["in"]
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


def fit_box(
    subs: list[dict], deadline: float = math.inf
) -> tuple[float, float, float, float] | None:
    """曲线实际范围（与前端 `pathGeom.fitPathBox` 同一条：曲线极值、短边钳到 0.01 mm、居中补齐）。"""
    xs: list[float] = []
    ys: list[float] = []
    for sp in subs:
        ns = sp["nodes"]
        if len(ns) < 2:
            continue
        pairs = list(zip(ns, ns[1:])) + ([(ns[-1], ns[0])] if sp["closed"] else [])
        for tick, (a, b) in enumerate(pairs):
            if not tick & 0x3FF and time.monotonic() > deadline:
                raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
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


def _transform_subs(subs: list[dict], m: Matrix, deadline: float = math.inf) -> list[dict]:
    out = []
    ticks = 0
    for sp in subs:
        nodes = []
        for n in sp["nodes"]:
            ticks += 1
            if not ticks & 0x3FF and time.monotonic() > deadline:
                raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
            nodes.append({k: apply(m, *n[k]) for k in ("p", "in", "out")})
        out.append({"closed": sp["closed"], "nodes": nodes})
    return out


def _r(v: float) -> float:
    return round(v, 6) + 0.0


def path_object(subs_mm: list[dict], deadline: float = math.inf) -> dict | None:
    """页面 mm 的子路径 → 路径对象的几何字段（框 + 比例坐标锚点；手柄与锚点重合不写）。"""
    subs_mm = [s for s in subs_mm if len(s["nodes"]) >= 2]
    box = fit_box(subs_mm, deadline)
    if box is None:
        return None
    x, y, w, h = box

    def frac(q):
        return [_r((q[0] - x) / w), _r((q[1] - y) / h)]

    out_subs = []
    ticks = 0
    for sp in subs_mm:
        nodes = []
        for n in sp["nodes"]:
            ticks += 1
            if not ticks & 0x3FF and time.monotonic() > deadline:
                raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
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
    stops_cache: dict[str, list[dict]] = field(default_factory=dict)
    #: 原因码 → 一句建议（每个码只留第一条；有就随结果回去，没有就没有这个键）
    hints: dict[str, str] = field(default_factory=dict)
    parents: dict = field(default_factory=dict)

    def note(self, code: str, hint: str | None = None) -> None:
        self.notes[code] = self.notes.get(code, 0) + 1
        if hint and code not in self.hints:
            self.hints[code] = hint


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
    # 每个元素都要对整张规则表逐条比（最多 1 万条选择器）：每 256 条查一次墙钟，一个元素自己就能吃掉 0.1 s 以上，
    # 不查的话超时触发后要等这个元素比完才返回
    for n, r in enumerate(
        ctx.rules
    ):  # `ctx.rules` 在建 ctx 时已按 (spec, order) 排好序，这里不再每个元素排一遍
        if not n & 0xFF:
            _check_clock(ctx)
        if (
            (r.tag is None or r.tag == tag)
            and all(c in cls for c in r.classes)
            and (r.ident is None or r.ident == ident)
        ):
            st.update(r.decls)
    if el.get("style"):
        st.update(_parse_decls(el.get("style")))
    if st.get("fill", "").strip().lower() == "inherit":
        st["fill"] = parent.get("fill", "black")
    return st


def _url_id(v: str | None) -> str | None:
    m = re.match(r"\s*url\(\s*['\"]?#([^'\")\s]+)['\"]?\s*\)", v or "")
    return m.group(1) if m else None


def _check_clock(ctx: _Ctx) -> None:
    if time.monotonic() > ctx.deadline:
        raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")


def _href_chain(el, ctx: _Ctx):
    """渐变自己、再沿 `href` 一路继承的祖先（有环就停、最多 `MAX_HREF_HOPS` 跳）。迭代而不递归：几千个渐变首尾相连不会撑爆栈，
    也不再每跳复制一份「已见」元组（平方级）。"""
    seen = {id(el)}
    yield el
    for _ in range(MAX_HREF_HOPS):
        ref = _href(el)
        if not (ref and ref.startswith("#") and ref[1:] in ctx.ids):
            return
        el = ctx.ids[ref[1:]]
        if id(el) in seen:
            return
        seen.add(id(el))
        yield el


def _stops(el, ctx: _Ctx) -> list[dict]:
    """渐变的色标（自己没有就用 `href` 链上第一个有色标的）。结果按渐变缓存：引用它的每个图形不再重算；
    色标数有上限，逐个色标查墙钟（每个色标要走一遍样式级联，色标数 × 规则数是两个预算相乘）"""
    for node in _href_chain(el, ctx):
        key = node.get("id") or ""
        if key and key in ctx.stops_cache:
            got = ctx.stops_cache[key]
        else:
            got = _own_stops(node, ctx)
            if key:
                ctx.stops_cache[key] = got
        if got:
            return got
    return []


def _own_stops(el, ctx: _Ctx) -> list[dict]:
    stops: list[dict] = []
    for s in el:
        if _local(s.tag) != "stop":
            continue
        if len(stops) >= MAX_STOPS:
            raise SvgImportError("too_many_elements", "渐变色标过多")
        _check_clock(ctx)
        st = _computed(s, {}, ctx)
        off = s.get("offset", "0").strip()
        offs = _nums(off)
        o = float(off[:-1]) / 100 if off.endswith("%") else float(offs[0] if offs else 0)
        col = parse_color(st.get("stop-color", "black")) or ("#000000", 0.0)
        alpha = col[1] * float(st.get("stop-opacity", "1") or 1)
        stop = {"offset": _r(max(0.0, min(1.0, o))), "color": col[0]}
        if alpha < 1:
            stop["opacity"] = _r(max(0.0, alpha))
        stops.append(stop)
    return stops


def _grad_attr(el, name, ctx: _Ctx):
    for node in _href_chain(el, ctx):
        v = node.get(name)
        if v is not None:
            return v
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
            items = [v for v in re.split(r"[,\s]+", dash.strip()) if v]
            # 项数有上限（几百万项的虚线数组每项都换算一遍，没有墙钟可查）：超出按没有虚线处理
            d = (
                [_length(v, 1.0) * scale_mm / MM_PER_PT for v in items]
                if len(items) <= MAX_DASH_ITEMS
                else []
            )
            if len(d) % 2:
                d = d * 2
            if d and any(v > 0 for v in d):
                out["dashPattern"] = [_r(v) for v in d]
    if st.get("fill-rule") == "evenodd":
        out["fillRule"] = "evenodd"
    return out


def _bbox(subs: list[dict], deadline: float = math.inf):
    b = fit_box(subs, deadline)
    return b if b else (0.0, 0.0, 1.0, 1.0)


def _new_id(ctx: _Ctx) -> str:
    ctx.seq += 1
    return f"s{ctx.seq}"


def _new_gid(ctx: _Ctx) -> str:
    ctx.gseq += 1
    return f"sg{ctx.gseq}"


def _emit_shape(subs_user, st, ctm, ctx: _Ctx, path: list[str], opacity: float) -> dict | None:
    subs_mm = _transform_subs(subs_user, ctm, ctx.deadline)
    geom = path_object(subs_mm, ctx.deadline)
    if geom is None:
        return None
    scale = math.sqrt(abs(ctm[0] * ctm[3] - ctm[1] * ctm[2]))
    paint = _paint_fields(
        st,
        ctx,
        ctm,
        _bbox(subs_user, ctx.deadline),
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


def _spend_anchors(ctx: _Ctx, subs: list[dict] | None) -> None:
    """所有几何分支共用的锚点预算（path / polyline / polygon / rect / circle / ellipse / line）：跨整次导入累计，
    `<use>` 每展开一次就再扣一次；顺带查墙钟（Codex 线程 4656678190）"""
    if time.monotonic() > ctx.deadline:
        raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
    ctx.anchors += sum(len(sp["nodes"]) for sp in subs or ())
    if ctx.anchors > MAX_ANCHORS:
        raise SvgImportError("too_many_elements", "锚点超出上限")


def _geometry(el, tag: str, ctx: _Ctx | None = None) -> list[dict] | None:
    if ctx is None or tag == "path":
        return _geometry_raw(el, tag, ctx)
    if tag in ("polyline", "polygon") and len(el.get("points") or "") > MAX_PATH_D_CHARS:
        raise SvgImportError("too_many_elements", "points 过长")  # 分词之前先拒
    if time.monotonic() > ctx.deadline:
        raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
    subs = _geometry_raw(el, tag, ctx)
    _spend_anchors(ctx, subs)
    return subs


def _geometry_raw(el, tag: str, ctx: _Ctx | None = None) -> list[dict] | None:
    g = el.get
    if tag == "path":
        if ctx is None:
            return parse_path_d(g("d") or "")
        # 总锚点预算跨整次导入共享（`<use>` 反复引用同一条路径会反复展开）；单条再带墙钟
        subs = parse_path_d(
            g("d") or "", max_anchors=MAX_ANCHORS - ctx.anchors, deadline=ctx.deadline
        )
        _spend_anchors(ctx, subs)  # 累计并立刻查上限（以前只加数不检查）
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
        if ctx is not None and len(v) // 2 > MAX_ANCHORS - ctx.anchors:
            raise SvgImportError("too_many_elements", "点数超出上限")  # 建几十万个节点字典之前先拒
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


class _TextLayout(Exception):
    """tspan 的位置做不准（逐字定位、非上下标的 dy / baseline-shift……）：整段文字记 `text_layout`"""


#: 上下标：偏移至少是正文字号的这个比例才算（再小的当没有）；字号缩到正文的这个比例以内才算「小字号」
_SCRIPT_MIN_SHIFT = 0.15
_SCRIPT_MAX_SIZE = 0.85
#: 关键字 super / sub 的偏移（正文字号的比例；SVG 把量交给浏览器，各家在 0.33–0.5 之间）
_SCRIPT_KEYWORD_SHIFT = 0.4
#: 上下标偏移的上限（正文字号的倍数）：再大的不是上下标，是错开的另一行
_SCRIPT_MAX_SHIFT = 1.0
_DY_RE = re.compile(r"\s*(" + _NUM + r")(px|em)?\s*")


def _style_flags(rst: dict) -> tuple[bool, bool]:
    """样式 → (粗体, 斜体)。Illustrator 把粗斜写进 PostScript 名（`Arial-BoldMT`、`Helvetica-Oblique`），不写 font-weight"""
    weight = (rst.get("font-weight") or "normal").lower()
    ps_name = (rst.get("font-family") or "").lower()
    bold = (
        weight in ("bold", "bolder")
        or (weight.isdigit() and int(weight) >= 600)
        or bool(re.search(r"-(?:semi|demi|extra)?bold|black|heavy", ps_name))
    )
    italic = (rst.get("font-style") or "").lower() in ("italic", "oblique") or bool(
        re.search(r"-\w*(?:italic|oblique)", ps_name)
    )
    return bold, italic


def _rel_size(v: str | None, parent: float) -> float:
    """tspan 的字号（用户单位）：em / % / smaller / larger 按父字号，其余按绝对长度；没写沿用父字号"""
    if v is None:
        return parent
    s = v.strip().lower()
    if s == "smaller":
        return parent / 1.2
    if s == "larger":
        return parent * 1.2
    m = _LENGTH_RE.match(s)
    if not m or len(m.group(1)) > MAX_NUM_CHARS:
        return parent
    if m.group(2) == "em":
        return float(m.group(1)) * parent
    return _length(s, parent, parent)  # `%` 也在这里：按父字号


def _shift_up(nst: dict, fs: float, base_fs: float) -> tuple[float, bool]:
    """元素自己的 baseline-shift / vertical-align → (向上的偏移量（用户单位，向下为负）, 是不是 super / sub 关键字)"""
    v = (nst.get("baseline-shift") or nst.get("vertical-align") or "").strip().lower()
    if v in ("", "baseline", "0"):
        return 0.0, False
    if v == "super":
        return _SCRIPT_KEYWORD_SHIFT * base_fs, True
    if v == "sub":
        return -_SCRIPT_KEYWORD_SHIFT * base_fs, True
    if not _LENGTH_RE.match(v):
        raise _TextLayout  # top / middle / text-top ……：不是上下标
    return _length(v, fs, 0.0), False


def _classify_script(off_up: float, fs: float, base_fs: float, keyword: bool) -> str:
    if abs(off_up) < _SCRIPT_MIN_SHIFT * base_fs:
        return ""
    if abs(off_up) > _SCRIPT_MAX_SHIFT * base_fs or not (
        keyword or fs <= _SCRIPT_MAX_SIZE * base_fs
    ):
        raise _TextLayout  # 偏了但字号没缩小（不是上下标）、或偏得太远
    return "sup" if off_up > 0 else "sub"


@dataclass
class _Chunk:
    """一个 `<text>` 里连续排的一段文字（有自己的 x / y）。片段 = [文字, 粗体, 斜体, 上下标]"""

    x: float
    y: float
    st: dict
    fs: float  # 这一段的正文字号（用户单位）
    segs: list[list] = field(default_factory=list)
    dy: float = 0.0  # 累计的 dy（向下为正）：SVG 里 dy 的偏移一直延续到下一个 dy


def _collect(node, nst: dict, parent_fs: float, bs_up: float, chunk: _Chunk, ctx: _Ctx) -> None:
    """一个 tspan（含嵌套）的文字按顺序收进 `chunk.segs`，每一段带自己的粗斜与上下标。做不准抛 `_TextLayout`。"""
    _check_clock(ctx)
    if any(node.get(a) for a in ("dx", "rotate", "textLength")):
        raise _TextLayout
    fs = _rel_size(nst.get("font-size"), parent_fs)
    if node.get("dy"):
        m = _DY_RE.fullmatch(node.get("dy"))
        if not m or len(m.group(1)) > MAX_NUM_CHARS:
            raise _TextLayout  # 多值 dy（逐字下移）
        chunk.dy += float(m.group(1)) * (fs if m.group(2) == "em" else 1.0)
    shift, kw = _shift_up(nst, fs, chunk.fs)
    bs = bs_up + shift
    bold, italic = _style_flags(nst)
    if node.text:
        chunk.segs.append(
            [node.text, bold, italic, _classify_script(bs - chunk.dy, fs, chunk.fs, kw)]
        )
    for child in node:
        if not isinstance(child.tag, str):
            continue
        t = _local(child.tag)
        if t == "textPath":
            raise _TextLayout("text_path")
        if t != "tspan":
            continue
        if child.get("x") or child.get("y"):
            raise _TextLayout  # 嵌套里的绝对定位
        _collect(child, _computed(child, nst, ctx), fs, bs, chunk, ctx)
        if child.tail:  # 子 tspan 的 dy 会一直延续：尾巴按此刻的累计偏移判
            chunk.segs.append(
                [child.tail, bold, italic, _classify_script(bs - chunk.dy, fs, chunk.fs, kw)]
            )


def _chunk_segments(chunk: _Chunk) -> list[list]:
    """空白折叠（跨片段）、去首尾空格、丢空片段"""
    segs: list[list] = []
    for text, bold, italic, script in chunk.segs:
        text = re.sub(r"\s+", " ", text)
        if segs and segs[-1][0].endswith(" ") and text.startswith(" "):
            text = text[1:]
        if text:
            segs.append([text, bold, italic, script])
    if segs:
        segs[0][0] = segs[0][0].lstrip()
        segs[-1][0] = segs[-1][0].rstrip()
    return [s for s in segs if s[0]]


def _chunk_object_text(segs: list[list]) -> tuple[str, bool, bool, bool]:
    """片段 → (文字, 整段粗体, 整段斜体, 是不是 LaTeX 子集写法)。

    粗斜一致、没有上下标：就是普通文字（与以前逐字相同）。有局部粗斜 / 上下标：写成文字对象的 LaTeX 子集
    （`textFormat: "latex"`，`\\textbf{}` / `\\textit{}` / `\\textsuperscript{}` / `\\textsubscript{}`，ADR 0152）；整段都粗 / 都斜的
    仍用对象自己的 `bold` / `italic`，片段里只写与整段不同的部分。"""
    bold_all = all(s[1] for s in segs)
    italic_all = all(s[2] for s in segs)
    joined = "".join(s[0] for s in segs)
    if (
        all(not s[3] for s in segs)
        and all(s[1] == segs[0][1] and s[2] == segs[0][2] for s in segs)
        and not re.search(
            r"[\\^_]", joined
        )  # 字面的 `^{` `_{` `\` 在旧标记里会被读成上下标 / 转义：走 LaTeX 子集的转义
    ):
        return joined, segs[0][1], segs[0][2], False
    runs = [
        richtext.TextRun(s[0], s[3], italic=s[2] and not italic_all, bold=s[1] and not bold_all)
        for s in segs
    ]
    return richtext.serialize_latex(runs), bold_all, italic_all, True


def _text_width_em(segs: list[list]) -> float:
    """字宽估算（em）：拉丁 0.55 em、CJK 1 em，上下标按 0.62 缩小"""
    return sum(
        (1.0 if ord(ch) > 0x2E80 else 0.55) * (richtext.SCRIPT_SIZE if s[3] else 1.0)
        for s in segs
        for ch in s[0]
    )


def _emit_text(el, st, ctm, ctx: _Ctx, path: list[str], opacity: float) -> None:
    """`<text>` → 文字对象：每个有自己 x / y 的 tspan 各成一个；tspan 里的上下标（baseline-shift / dy 配小字号）与局部粗斜
    保留成文字对象的局部样式；dx / rotate / 逐字 dy / textPath 做不准 → 记不支持。"""
    x0 = _nums(el.get("x") or "0")
    y0 = _nums(el.get("y") or "0")
    cx, cy = (x0[0] if x0 else 0.0), (y0[0] if y0 else 0.0)
    if any(el.get(a) for a in ("dx", "dy", "rotate", "textLength")) or len(x0) > 1 or len(y0) > 1:
        ctx.note("text_layout")
        return
    base_fs = _length(st.get("font-size", "16"), 16, 16)
    bold0, italic0 = _style_flags(st)
    chunks: list[_Chunk] = []
    try:
        if el.text and el.text.strip():
            c0 = _Chunk(cx, cy, st, base_fs)
            c0.segs.append([el.text, bold0, italic0, ""])
            chunks.append(c0)
        for child in el:
            _check_clock(ctx)  # 每个 tspan 都要走一遍样式级联，子节点数 × 规则数
            t = _local(child.tag)
            if t == "textPath":
                ctx.note("text_path")
                return
            if t != "tspan":
                continue
            cst = _computed(child, st, ctx)
            if any(child.get(a) for a in ("dx", "rotate", "textLength")):
                raise _TextLayout
            xs, ys = _nums(child.get("x") or ""), _nums(child.get("y") or "")
            if len(xs) > 1 or len(ys) > 1:
                raise _TextLayout
            tx, ty = (xs[0] if xs else None), (ys[0] if ys else None)
            if tx is None and ty is None and chunks:
                # 同一行接着写：并到上一段（上下标等按上一段的正文字号判）
                _collect(child, cst, base_fs, 0.0, chunks[-1], ctx)
                tail_chunk = chunks[-1]
            else:
                if child.get("dy"):
                    raise _TextLayout  # 新起一段又带 dy：没有正文可比
                fs = _rel_size(cst.get("font-size"), base_fs)
                cc = _Chunk(tx if tx is not None else cx, ty if ty is not None else cy, cst, fs)
                _collect(child, cst, base_fs, 0.0, cc, ctx)
                if not any(s[0].strip() for s in cc.segs):
                    continue  # 空的 tspan 不成对象（与以前相同）
                chunks.append(cc)
                tail_chunk = cc
            if child.tail:  # 只有空白的尾巴也留（`a <tspan>b</tspan> <tspan>c</tspan>` 中间的空格）；首尾空白最后统一去
                tail_chunk.segs.append(
                    [
                        child.tail,
                        bold0,
                        italic0,
                        _classify_script(-tail_chunk.dy, base_fs, tail_chunk.fs, False),
                    ]
                )
    except _TextLayout as e:
        ctx.note(str(e) or "text_layout")
        return
    a, b, c, d = ctm[:4]
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    det = a * d - b * c
    if det <= 0 or abs(a * c + b * d) > 1e-6 * sx * sy or abs(sx - sy) > 1e-6 * max(sx, sy):
        # 斜切 / 非等比 / 镜像的文字：文字对象表达不了
        ctx.note("skew_text")
        return
    rot = math.degrees(math.atan2(b, a))
    for ck in chunks:
        _check_clock(ctx)
        ctx.count += 1  # 文字段也是展开出来的对象：与元素同一个展开上限
        if ctx.count > IMPORT_BUDGET.max_expanded_elements:
            raise SvgImportError("too_many_elements", "展开后的元素超出上限")
        segs = _chunk_segments(ck)
        if not segs:
            continue
        text, bold, italic, latex = _chunk_object_text(segs)
        rst = ck.st
        size_user = ck.fs
        size_pt = size_user * sx * MM_PER_PX / MM_PER_PT
        size_mm = size_pt * MM_PER_PT
        col = parse_color(rst.get("fill", "black"), rst.get("color", "black"))
        if _url_id(rst.get("fill")):
            g, solid = _gradient(_url_id(rst.get("fill")), ctx, (0, 0, 1, 1), ctm, (0, 0, 1, 1))
            col = (solid, 1.0)
        # 字宽估算：拉丁字符 0.55 em、CJK 1 em（前端 TextView 落定后按实际量出来的宽写回）
        width_mm = _text_width_em(segs) * size_mm + 0.5
        anchor = rst.get("text-anchor", "start")
        align = {"middle": "center", "end": "right"}.get(anchor, "left")
        bx, by = apply(ctm, ck.x, ck.y)
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
        obj = {
            "id": _new_id(ctx),
            "type": "text",
            "x": _r(cxm - width_mm / 2),
            "y": _r(cym - h / 2),
            "w": _r(width_mm),
            "h": _r(h),
            "text": text,
            "sizePt": _r(size_pt),
            "bold": bold,
            "italic": italic,
            "color": col[0] if col else "#000000",
            "align": align,
        }
        if latex:
            obj["textFormat"] = "latex"
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
                subs += _transform_subs(
                    g, mul(cm, parse_transform(target.get("transform"))), ctx.deadline
                )
            continue
        g = _geometry(child, tag, ctx)
        if g:
            subs += _transform_subs(
                g, mul(m, parse_transform(child.get("transform"))), ctx.deadline
            )
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
                geom = path_object(
                    _transform_subs(clip_subs, (MM_PER_PX, 0, 0, MM_PER_PX, 0, 0), ctx.deadline),
                    ctx.deadline,
                )
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


# --------------------------------------------------------------------- 线端标记 → 路径的端型（ADR 0208 第 5 期）
#: 能换成路径端型的标记写法：一个 `<marker>` 里只有一个简单图形——实心三角（→ triangle）、开口箭头（→ open）、实心圆（→ dot），
#: 颜色与线相同，朝向与路径端点的切线一致，尺寸落在三档之内。别的（多个图形、曲线、渐变、颜色不同、marker-mid、闭合路径、
#: 朝向不对……）一律仍按 `marker` 记做不准，整段当图片，并回一句建议。
_MARKER_LEAVES = ("path", "line", "polyline")
_GEOMETRY_TAGS = ("path", "line", "polyline", "polygon", "rect", "circle", "ellipse")
#: 端型三档的帽长系数（与 `geometry.HEAD_SIZE_FACTOR` 同源）；帽长 = 4 × 线宽 × 系数，圆点直径 = 3.4 × 线宽 × 系数
_MARKER_SIZES = (("small", 0.7), ("medium", 1.0), ("large", 1.5))
#: 尺寸比（SVG 里的帽长 ÷ 中档帽长）落在这个范围里才换成档位，再大 / 再小换了会明显变样
_MARKER_RATIO = (0.5, 2.5)
#: 标记朝向与路径切线（或反向）的最大夹角（度）
_MARKER_ANGLE_TOL = 15.0
#: 等腰判定：两腰长度之差占较长腰的比例上限
_MARKER_ISOSCELES_TOL = 0.05
_MARKER_HINT = "这个箭头头用了做不准的线端标记（marker）：改用 arrow（或简单三角 marker，颜色与线一致、朝向沿线）后再导入"


def _marker_refs(st: dict) -> tuple[str | None, str | None, str | None]:
    short = st.get("marker")

    def val(k: str) -> str | None:
        v = st.get(k)
        if v is None:
            v = short
        v = (v or "").strip()
        return None if v in ("", "none") else v

    return val("marker-start"), val("marker-mid"), val("marker-end")


def _angle_diff(a: float, b: float) -> float:
    """两个方向（弧度）的夹角，0..180 度"""
    d = (a - b + math.pi) % (2 * math.pi) - math.pi
    return abs(math.degrees(d))


def _same_pt(a, b, eps: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def _tangent(nodes: list[dict], end: bool) -> tuple[float, float] | None:
    """路径起点（`end=False`）/ 终点处的前进方向（单位向量；起点 = 离开起点的方向）"""
    if end:
        n, o = nodes[-1], nodes[-2]
        cands = (n["in"], o["out"], o["p"])
        sign = 1.0
    else:
        n, o = nodes[0], nodes[1]
        cands = (n["out"], o["in"], o["p"])
        sign = -1.0
    ref = n["p"]
    for c in cands:
        if not _same_pt(c, ref):
            dx, dy = (ref[0] - c[0]) * sign, (ref[1] - c[1]) * sign
            ln = math.hypot(dx, dy)
            if ln > 0:
                return dx / ln, dy / ln
    return None


def _straight_end(nodes: list[dict], end: bool) -> bool:
    if end:
        return _same_pt(nodes[-1]["in"], nodes[-1]["p"]) and _same_pt(
            nodes[-2]["out"], nodes[-2]["p"]
        )
    return _same_pt(nodes[0]["out"], nodes[0]["p"]) and _same_pt(nodes[1]["in"], nodes[1]["p"])


#: 标记图形能换成端型的**允许名单**（取计算值：表现属性 / 内联 style / CSS 类都算）。名单之外的任何影响外观的属性出现非默认值，
#: 或取值读不懂，一律不换（宁可整段当图片，也不静默改样子 / 丢头）。
_MARKER_PLAIN_PROPS = frozenset(
    {
        "fill", "stroke", "stroke-width", "stroke-linejoin", "stroke-linecap", "color",  # 颜色与线宽另有检查
        "font-family", "font-size", "font-weight", "font-style", "text-anchor",  # 形状用不到
        "stop-color", "stop-opacity", "baseline-shift", "vertical-align",
    }
)  # fmt: skip
#: 只许等于默认值的属性（字符串比较，小写去空白；数值型另走 `_MARKER_UNITY`）
_MARKER_DEFAULT_ONLY = {
    "fill-rule": {"nonzero"}, "stroke-dasharray": {"none"}, "stroke-miterlimit": {"4"},
    "stroke-dashoffset": {"0", "0px"}, "clip-path": {"none"}, "mask": {"none"}, "filter": {"none"},
    "marker": {"none"}, "marker-start": {"none"}, "marker-mid": {"none"}, "marker-end": {"none"},
    "paint-order": {"normal"}, "vector-effect": {"none"}, "mix-blend-mode": {"normal"},
    "isolation": {"auto"}, "shape-rendering": {"auto"}, "transform": {"none"},
}  # fmt: skip
_MARKER_UNITY = ("opacity", "fill-opacity", "stroke-opacity")
_KNOWN_PAINT = re.compile(r"(?:#[0-9a-f]{3,8}|rgba?\(.*\))\Z")


def _known_paint(v: str) -> bool:
    v = v.strip().lower()
    return (
        v in _NAMED
        or v in ("none", "transparent", "currentcolor", "context-fill", "context-stroke")
        or bool(_KNOWN_PAINT.match(v))
        or bool(_url_id(v))
    )


def _marker_paint(
    v: str | None, op: float, cur: str, ctx_fill: str | None, ctx_stroke: str | None
) -> str | None:
    """一个 paint → `#rrggbb` / `None`（确定没画：none、alpha 恰为 0）/ `"?"`（画了但端型表达不了：渐变、0<alpha<1、读不懂）"""
    v = (v or "").strip()
    if not _known_paint(v) or not 0 <= op <= 1:
        return "?"
    if v == "context-fill":
        c = ctx_fill
    elif v == "context-stroke":
        c = ctx_stroke
    elif _url_id(v):
        return "?"
    else:
        parsed = parse_color(v, cur)
        if parsed is None:
            c = None
        elif parsed[1] == 0:
            c = None
        elif parsed[1] < 1:
            return "?"
        else:
            c = parsed[0]
    if c is None or c == "?":
        return c
    if op == 0:
        return None
    return c if op == 1 else "?"


def _marker_shape(
    child, tag: str, mel, ctx: _Ctx, stroke_hex: str, ctx_fill: str | None, ctx_stroke: str | None
):
    """标记里那一个图形 → `(kind, tip, dir, length, 线宽)`（标记自己的坐标）、`"hidden"`（确定不可见：原图里就没有头）或 `None`（换不了）。

    kind = triangle / open / dot；`tip` 是尖（圆点 = 圆心）；`dir` 是 底边中点 → 尖 的弧度（圆点 = None）；
    `length` = 帽长（圆点 = 直径）；最后一项是开口箭头自己的线宽（标记单位，别的端型 0）。

    **允许名单**：图形、`<marker>` 及其祖先上每个影响外观的属性（取计算值）都得在 `_MARKER_PLAIN_PROPS` 里、或等于默认值，
    不透明度只能是 1；否则 `None`。只有「确定」不可见（自身 display:none / visibility:hidden / opacity 0 / paint 为 none 或 alpha 恰为 0）才回 `"hidden"`。"""
    chain = []
    el = child
    while el is not None and len(chain) < 128:
        chain.append(el)
        el = ctx.parents.get(el)
    chain.reverse()
    mst: dict = {}
    certain_hidden = False
    for e in chain:
        mst = _computed(e, mst, ctx)
        own_style = e in (child, mel)
        for k, v in mst.items():
            if k in _MARKER_PLAIN_PROPS:
                continue
            val = (v or "").strip().lower()
            if k == "display":
                if val == "none":
                    if e is child:
                        certain_hidden = True
                    else:
                        return (
                            None  # marker / 祖先上的 display:none：规范对 marker 另有说法，保守不换
                        )
                elif val not in ("inline", "block", ""):
                    return None
            elif k == "visibility":
                if val in ("hidden", "collapse"):
                    certain_hidden = True
                elif val not in ("visible", ""):
                    return None
            elif k in _MARKER_UNITY:
                try:
                    f = float(val or 1)
                except ValueError:
                    return None
                if f == 0 and k == "opacity":
                    certain_hidden = True
                elif f != 1 and k == "opacity":
                    return None
                # fill-opacity / stroke-opacity 在 paint 里按每个 paint 单独判
            elif k == "transform" and not own_style:
                continue  # 祖先的 transform 不作用于标记内容
            elif val not in _MARKER_DEFAULT_ONLY.get(k, ()):
                return None  # 名单之外的属性：非默认值，或者根本不认识
    if child.get("transform") or mel.get("transform"):
        return None
    if certain_hidden:
        return "hidden"
    cur = mst.get("color", "black")

    def op_of(key: str) -> float:
        try:
            return float(mst.get(key, "1") or 1)
        except ValueError:
            return -1.0

    fill = _marker_paint(mst.get("fill", "black"), op_of("fill-opacity"), cur, ctx_fill, ctx_stroke)
    stroke = _marker_paint(
        mst.get("stroke", "none"), op_of("stroke-opacity"), cur, ctx_fill, ctx_stroke
    )
    if fill is None and stroke is None:
        return "hidden"
    if tag == "circle":
        r = _length(child.get("r"))
        if r <= 0 or fill != stroke_hex or stroke is not None:
            return None
        c = (_length(child.get("cx")), _length(child.get("cy")))
        return "dot", c, None, 2 * r, 0.0
    subs = _geometry(child, tag, ctx)
    if not subs or len(subs) != 1:
        return None
    nodes = subs[0]["nodes"]
    if any(not (_same_pt(n["in"], n["p"]) and _same_pt(n["out"], n["p"])) for n in nodes):
        return None  # 曲线
    pts = [n["p"] for n in nodes]
    if len(pts) >= 2 and _same_pt(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) != 3:
        return None
    filled = fill is not None
    if filled:
        if fill != stroke_hex or stroke is not None:
            return None  # 实心图形带描边（哪怕同色）会比填充大一圈
    else:
        if stroke != stroke_hex or subs[0]["closed"]:
            return None  # 颜色不同；或描边的闭合三角（空心三角）：端型里没有
    if filled:
        # 实心三角：找顶点（两腰等长的那个）；几个都够等腰（正三角）就取最贴近标记 +x 方向的
        best = None
        for i in range(3):
            a, b, c = pts[i], pts[(i + 1) % 3], pts[(i + 2) % 3]
            d1, d2 = math.dist(a, b), math.dist(a, c)
            if max(d1, d2) <= 0 or abs(d1 - d2) > _MARKER_ISOSCELES_TOL * max(d1, d2):
                continue
            mid = ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
            ln = math.dist(mid, a)
            if ln <= 0:
                continue
            ang = math.atan2(a[1] - mid[1], a[0] - mid[0])
            if best is None or abs(ang) < abs(best[2]):
                best = (a, ang, ang, ln)
        if best is None:
            return None
        return "triangle", best[0], best[1], best[3], 0.0
    # 开口箭头：三个点依次是 翼、尖、翼
    a, v, b = pts
    d1, d2 = math.dist(v, a), math.dist(v, b)
    if max(d1, d2) <= 0 or abs(d1 - d2) > _MARKER_ISOSCELES_TOL * max(d1, d2):
        return None
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    ln = math.dist(mid, v)
    if ln <= 0:
        return None
    return (
        "open",
        v,
        math.atan2(v[1] - mid[1], v[0] - mid[0]),
        ln,
        _length(mst.get("stroke-width", "1"), 1.0, 1.0),
    )


def _marker_end_head(
    ref: str,
    at_end: bool,
    nodes: list[dict],
    sw_user: float,
    stroke_hex: str,
    ctx_fill: str | None,
    ctx_stroke: str | None,
    ctx: _Ctx,
):
    """一端的标记 → `(端型, 档位, 端点沿线外移的距离（用户单位）)`；换不了回 `None`"""
    mid = _url_id(ref)
    mel = ctx.ids.get(mid) if mid else None
    if mel is None or _local(mel.tag) != "marker":
        return None
    kids = [
        c
        for c in mel
        if isinstance(c.tag, str) and _local(c.tag) not in ("title", "desc", "metadata")
    ]
    if len(kids) != 1 or _local(kids[0].tag) not in ("path", "polygon", "polyline", "circle"):
        return None
    child = kids[0]
    mw = _length(mel.get("markerWidth"), 0, 3.0)
    mh = _length(mel.get("markerHeight"), 0, 3.0)
    units = (mel.get("markerUnits") or "strokeWidth").strip()
    if mw <= 0 or mh <= 0 or units not in ("strokeWidth", "userSpaceOnUse"):
        return None
    scale = 1.0
    if mel.get("viewBox"):
        vb = _nums(mel.get("viewBox"))
        if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
            return None
        if (mel.get("preserveAspectRatio") or "").split()[:1] == ["none"]:
            return None
        scale = min(mw / vb[2], mh / vb[3])
    total = scale * (sw_user if units == "strokeWidth" else 1.0)
    shape = _marker_shape(child, _local(child.tag), mel, ctx, stroke_hex, ctx_fill, ctx_stroke)
    if shape == "hidden":
        return "hidden"
    if shape is None or total <= 0:
        return None
    kind, tip, tdir, length_c, mark_sw = shape
    if kind == "open" and abs(mark_sw * total - sw_user) > 0.1 * sw_user:
        return None  # 开口箭头是描边画的：端型的线宽总是线宽，与线不等宽就会变样
    tan = _tangent(nodes, at_end)
    if tan is None:
        return None
    out = tan if at_end else (-tan[0], -tan[1])  # 端型朝向：离开线的方向
    out_ang = math.atan2(out[1], out[0])
    orient = (mel.get("orient") or "0").strip()
    if orient == "auto":
        rot = math.atan2(tan[1], tan[0])
    elif orient == "auto-start-reverse":
        rot = math.atan2(tan[1], tan[0]) + (0.0 if at_end else math.pi)
    else:
        if not re.fullmatch(_NUM + r"(?:deg)?", orient):
            return None
        rot = math.radians(_nums(orient.removesuffix("deg"))[0])
    if tdir is not None and _angle_diff(rot + tdir, out_ang) > _MARKER_ANGLE_TOL:
        return None  # 尖朝向与线端的前进方向对不上（比如起点的三角朝向线里面）
    cr, sr = math.cos(rot), math.sin(rot)
    # 尖相对参考点的向量（用户单位）：沿 out 的分量 = 端点要外移多少，横向分量要小
    vx = (tip[0] - _length(mel.get("refX"))) * total
    vy = (tip[1] - _length(mel.get("refY"))) * total
    ux, uy = vx * cr - vy * sr, vx * sr + vy * cr
    head_len = length_c * total
    ext = ux * out[0] + uy * out[1]
    lateral = abs(-ux * out[1] + uy * out[0])
    if kind == "dot":
        ext += head_len / 2  # 圆心 → 最外点
        ratio = head_len / (3.4 * sw_user)
    else:
        ratio = head_len / (4.0 * sw_user)
    if lateral > 0.15 * head_len or not (_MARKER_RATIO[0] <= ratio <= _MARKER_RATIO[1]):
        return None
    size = min(_MARKER_SIZES, key=lambda sf: abs(math.log(ratio / sf[1])))[0]
    if abs(ext) <= 0.1 * head_len:
        ext = 0.0
    return kind, size, ext


def _markers(tag: str, st: dict, geom: list[dict] | None, ctx: _Ctx) -> dict:
    """元素的 `marker-start / marker-end` → 路径对象的 `headStart / headEnd`（及档位）。换不了的记 `marker`。"""
    start, mid, end = _marker_refs(st)
    if not (start or mid or end) or geom is None:
        return {}
    ok = (
        tag in _MARKER_LEAVES
        and len(geom) == 1
        and not geom[0]["closed"]
        and len(geom[0]["nodes"]) >= 2
        and (not mid or len(geom[0]["nodes"]) == 2)  # 两点的线没有中间顶点，marker-mid 无处可画
    )
    sw_user = _length(st.get("stroke-width", "1"), 1.0, 1.0)
    stroke_c = parse_color(st.get("stroke", "none"), st.get("color", "black"))
    if not ok or _url_id(st.get("stroke")) or stroke_c is None or sw_user <= 0:
        ctx.note("marker", _MARKER_HINT)
        return {}
    nodes = geom[0]["nodes"]
    cur = st.get("color", "black")

    def ref_paint(key: str, default: str, op_key: str) -> str | None:
        """引用元素自己的 paint（`context-fill` / `context-stroke` 取它）：None = 没画，`"?"` = 渐变 / 部分透明 / 读不懂"""
        try:
            op = float(st.get(op_key, "1") or 1)
        except ValueError:
            return "?"
        return _marker_paint(st.get(key, default), op, cur, None, None)

    ctx_fill = ref_paint("fill", "black", "fill-opacity")
    ctx_stroke = ref_paint("stroke", "none", "stroke-opacity")
    fields: dict = {}
    moves: list[tuple[bool, float]] = []
    for at_end, ref in ((False, start), (True, end)):
        if not ref:
            continue
        got = _marker_end_head(ref, at_end, nodes, sw_user, stroke_c[0], ctx_fill, ctx_stroke, ctx)
        if got == "hidden":
            continue  # 标记图形不可见：原图里这一端就没有头
        if got is None:
            ctx.note("marker", _MARKER_HINT)
            return {}
        kind, size, ext = got
        if ext and (
            not _straight_end(nodes, at_end)
            or (
                ext < 0
                and -ext
                >= math.dist(nodes[-1 if at_end else 0]["p"], nodes[-2 if at_end else 1]["p"])
            )
        ):
            ctx.note("marker", _MARKER_HINT)  # 尖不在端点上、末段又是曲线 / 缩不回去：没法只移端点
            return {}
        side = "End" if at_end else "Start"
        fields[f"head{side}"] = kind
        if size != "medium":
            fields[f"headSize{side}"] = size
        if ext:
            moves.append((at_end, ext))
    # 尖不在端点上（参考点在底边中点 / 圆心）：沿线把端点移到尖上，让端型的尖落在 SVG 里尖所在的位置
    for at_end, ext in moves:
        n = nodes[-1 if at_end else 0]
        tan = _tangent(nodes, at_end)
        out = tan if at_end else (-tan[0], -tan[1])
        n["p"] = n["in"] = n["out"] = (n["p"][0] + out[0] * ext, n["p"][1] + out[1] * ext)
    return fields


def _walk_inner(el, tag: str, st: dict, m: Matrix, path: list[str], ctx: _Ctx, op: float) -> None:
    if tag not in _GEOMETRY_TAGS and any(_marker_refs(st)):
        ctx.note(
            "marker", _MARKER_HINT
        )  # 组 / use / text 上的 marker：属性是继承的，没法逐个落到路径上
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
    heads = _markers(tag, st, geom, ctx)
    if geom is None:
        return
    obj = _emit_shape(geom, st, to_mm, ctx, path, op)
    if obj is not None:
        obj.update(heads)
        ctx.objects.append(obj)


#: 导入用的预算：比静态 SVG 素材（`svgcheck.SVGBudget` 缺省）更紧——这是粘贴进来的一段，不是一张图的产物
IMPORT_BUDGET = svgcheck.SVGBudget(
    max_bytes=MAX_BYTES,
    max_depth=64,
    max_elements=MAX_ELEMENTS,
    max_image_bytes=4 * 1024 * 1024,
    max_image_pixels=40_000_000,
    max_expanded_elements=200_000,
    max_css_rules=10_000,  # 几十万条空规则也是一种拖时间的写法（Codex 线程 4656678188）
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


def _parse(svg_text: str, deadline: float = math.inf):
    """唯一的入口是 `svgcheck.parse_svg`（与静态 SVG 素材、ADR 0182 的场景解析同一个受限解析器），`tolerant` 模式：只多删掉
    编辑器私有命名空间与无作用的 CSS，安全规则一条不放。被拒绝的输入根本到不了下面的换算。"""
    data = svg_text.encode("utf-8") if isinstance(svg_text, str) else svg_text
    if len(data) > MAX_BYTES:
        raise SvgImportError("too_large", f"SVG 超过 {MAX_BYTES // 1024 // 1024} MB")
    if not re.search(rb"<svg[\s>]", data[:65536]):
        raise SvgImportError("not_svg", "没有 <svg> 元素")
    try:
        # `unicode_ids`：Tavotto 自己导出的 SVG 把对象 / 组的名字原样写进 `id`（中文也行，ADR 0208 §七之三），
        # 导回来不能被拒；id 只是个不被任何引用指到的标记，其余安全规则一条不放
        return svgcheck.parse_svg(
            data, budget=IMPORT_BUDGET, tolerant=True, deadline=deadline, unicode_ids=True
        )
    except svgcheck.SVGError as exc:
        if exc.code == "svg_timeout":
            raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒") from exc
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
    # 墙钟从入口起算：XML 解析、受限校验、CSS 清理与解析、换算共用这一份预算（Codex 线程 4656678188）
    deadline = time.monotonic() + DEADLINE_S
    try:
        root = _parse(svg_text, deadline)
    except _Rejected as rej:
        return {"objects": [], "size_mm": [0.0, 0.0], "unsupported": {rej.code: 1}}
    ids = {}
    parents: dict = {}
    css = []
    for tick, el in enumerate(root.iter()):
        if not tick & 0xFF and time.monotonic() > deadline:
            raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
        if not isinstance(el.tag, str):
            continue
        for c in el:
            parents[c] = el
        if el.get("id"):
            ids.setdefault(el.get("id"), el)
        if _local(el.tag) == "style" and el.text:
            css.append(el.text)
    notes: dict[str, int] = {}
    rules: list[_Rule] = []
    for c in css:
        rules += parse_css(c, notes, len(rules), deadline)
    rules.sort(key=lambda r: (r.spec, r.order))  # 一次排好，级联时按序取用
    ctx = _Ctx(ids=ids, rules=rules, notes=notes, deadline=deadline, parents=parents)
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
    _prune_groups(ctx.objects, deadline)
    out = {
        "objects": ctx.objects,
        "size_mm": [_r(w * MM_PER_PX), _r(h * MM_PER_PX)],
        "unsupported": dict(sorted(ctx.notes.items())),
    }
    if ctx.hints:
        out["hints"] = dict(sorted(ctx.hints.items()))
    return out


def _prune_groups(objs: list[dict], deadline: float = math.inf) -> None:
    from collections import Counter

    def chain(o):
        p = o.get("groupPath")
        return list(p) if p else ([o["groupId"]] if o.get("groupId") else [])

    for _ in range(8):
        if time.monotonic() > deadline:
            raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
        counts = Counter(g for o in objs for g in set(chain(o)))
        lonely = {g for g, n in counts.items() if n < 2}
        # 包住全部对象的那一层（SVG 写入器为翻 y 包的根组、Inkscape 的图层）：粘贴后本来就全选着，再包一层只是多一次双击
        top = chain(objs[0])[:1] if objs else []
        clip_groups = {chain(o)[-1] for o in objs if o.get("clipMask") and chain(o)}
        if top and counts.get(top[0]) == len(objs) and top[0] not in clip_groups:
            lonely.add(top[0])
        if not lonely:
            return
        for tick, o in enumerate(objs):
            if not tick & 0xFFF and time.monotonic() > deadline:
                raise SvgImportError("timeout", f"超过 {DEADLINE_S:g} 秒")
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
