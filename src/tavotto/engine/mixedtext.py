"""公式与中文混排的文字分段排（ADR 0045 §7）：`$…$` 外面的汉字不再画成方框。

问题：matplotlib 只要一行字里有成对的 `$`，就把**整行**交给 mathtext——`$` 外面的字也一样，而 mathtext 的
正文部分只用一张脸（`findfont(prop)`，族列表第一环），没有逐字形回退。正文脸是 Arial / Liberation 这类
西文字体时，「浓度 ($\\mu$mol/L)」里的「浓度」就是两个方框（ADR 0045 的中文尾巴够不着它）。

做法与文字处理软件的混排同一个思路：把这一行切成几段，**每段交给能画它的那条路**，共用一条基线——

* 「正文脸画不出、回退链里有脸画得出」的中日韩字（下称外来字）连成的段：普通文字，走带回退链的字体属性，
  matplotlib 逐字形退到中文脸；
* 其余照旧：含 `$…$` 的段原样交给 mathtext（公式、上下标与原来一个像素都不差），不含公式的西文段是普通文字；
* 段两头的空白单独成段、按普通文字量（3.8 的 mathtext 量不准行尾空白的宽度）。

切点在**渲染器的两个入口**：`get_text_width_height_descent`（量）与 `draw_text`（画）。matplotlib 的 Text
排版、tight / constrained 布局、`get_window_extent`、命中框都经前者，所有后端（Agg 预览 / PNG、PDF、SVG
文字与路径两种、PS、路径特效）的落笔都经后者，所以热态、导出、写回重放走同一条路，结构上就一致。只用
renderer 的公开协议，3.8–3.11 同一份。

**什么都没变的情况占绝大多数**：行里没有 `$`、没有中日韩字、正文脸自己画得出、回退链也画不出（没装中文
字体）——四种都原样交给 matplotlib，字节不变。

**只在 Tavotto 接手的时段生效**（`arm()` / `disarm()`）：safe worker 在脚本跑完、采 baseline 时武装；native
只在屏障内武装，放行前撤掉——脚本自己的 `savefig` 存出来的仍是它在终端里会得到的那份（与 ADR 0045 尾巴
同一条纪律）。武装 / 撤除时清 matplotlib 的文字度量缓存：缓存键里没有「切不切段」。
"""

from __future__ import annotations

import functools
import math
import re
from contextlib import contextmanager

#: 中日韩字符类——与 `manifest._CJK_CHAR` / `preflight._CJK` 字面相同（`tests/test_cjk_figure_text.py` 看护）。
CJK_CHAR = re.compile("[⺀-⻿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")

#: matplotlib mathtext 的整行语法（`_mathtext.Parser`）：`non_math (math non_math)*`。非公式段里 `\$` 是转义的
#: 美元号；公式段是 `QuotedString('$', escChar='\\')`——反斜杠转义任意一个字符。
_NON_MATH = re.compile(r"(?:\\\$|[^$])*")
_MATH = re.compile(r"\$(?:\\.|[^\\$])*\$", re.S)

_ARMED = False
_MARK = "_tavotto_mixed_runs"


def armed() -> bool:
    """此刻是否分段排。manifest 的缺字扫描据此判 `$` 外的中日韩字由谁画（`manifest._glyph_scan`）。"""
    return _ARMED


def arm() -> None:
    """Tavotto 接手渲染：开始分段排（第一次顺带 `install()`）。幂等。"""
    global _ARMED
    install()
    if not _ARMED:
        _ARMED = True
        clear_text_metric_caches()


@contextmanager
def suspended():
    """这一段按脚本**自己**会得到的样子量字（不切段）：图幅、脚本 savefig 时的 tight 布局都是原件那一刻的
    事实，要与磁盘上的原件一致（ADR 0098）。出来时恢复进来前的状态。"""
    was = _ARMED
    disarm()
    try:
        yield
    finally:
        if was:
            arm()


def disarm() -> None:
    """交还给脚本（native 放行前）：停止分段排。幂等。"""
    global _ARMED
    if _ARMED:
        _ARMED = False
        clear_text_metric_caches()


def clear_text_metric_caches() -> None:
    """清 matplotlib 记下的文字度量（键里没有「切不切段」）。两代实现都认，认不出就什么都不做。

    3.8 / 3.10：一份全局 lru（`_get_text_metrics_with_cache_impl`）；3.11 起：按渲染器一份 lru，外层是
    `_get_text_metrics_function` 默认参数里的 WeakKeyDictionary——清掉外层，所有渲染器的内层随之作废。
    """
    from matplotlib import text as mtext

    impl = getattr(mtext, "_get_text_metrics_with_cache_impl", None)
    if impl is not None and hasattr(impl, "cache_clear"):
        impl.cache_clear()
    outer = getattr(mtext, "_get_text_metrics_function", None)
    defaults = getattr(outer, "__defaults__", None) or ()
    for d in defaults:
        if hasattr(d, "clear") and hasattr(d, "keys"):
            d.clear()


def _tokens(s: str) -> list[tuple[bool, str]] | None:
    """按 mathtext 的语法把一行切成 [(是公式, 原文)]；不合语法（落单的 `$`）回 None。"""
    out: list[tuple[bool, str]] = []
    i, n = 0, len(s)
    while True:
        m = _NON_MATH.match(s, i)
        if m.end() > i:
            out.append((False, m.group()))
        i = m.end()
        if i >= n:
            return out
        m = _MATH.match(s, i)
        if m is None:
            return None
        out.append((True, m.group()))
        i = m.end()


def _face_has(prop):
    """(正文脸画得出 ch?, 回退链里有脸画得出 ch?)——正文脸是 mathtext 用的那张（`findfont(prop)`），
    回退链是普通文字逐字形回退用的那一串（`fontManager._find_fonts_by_props`，3.6 起就在）。"""
    from matplotlib import font_manager as fm

    primary = fm.get_font(fm.findfont(prop))
    finder = getattr(fm.fontManager, "_find_fonts_by_props", None)
    chain = []
    if finder is not None:
        try:
            chain = [fm.get_font(p) for p in finder(prop)]
        except Exception:  # noqa: BLE001 — 链解析不出：当作没有回退，不切
            chain = []

    def has(ch: str) -> tuple[bool, bool]:
        code = ord(ch)
        if primary.get_char_index(code):
            return True, True
        return False, any(f.get_char_index(code) for f in chain)

    return has


def plan(s, prop, ismath) -> list[tuple[str, bool]] | None:
    """这一行要不要切、怎么切：[(段文字, 这段交不交给 mathtext)]；不切回 None（原样交给 matplotlib）。"""
    if not _ARMED or ismath is not True or not isinstance(s, str):
        return None
    if "$" not in s or CJK_CHAR.search(s) is None:
        return None
    toks = _tokens(s)
    if toks is None:
        return None
    has = None
    # 逐字归类：外来字 True，其余 False；公式段整段 False
    marks: list[tuple[bool, str, bool]] = []  # (外来, 文字, 是公式)
    found = False
    for is_math, text in toks:
        if is_math:
            marks.append((False, text, True))
            continue
        for ch in text:
            foreign = False
            if CJK_CHAR.match(ch):
                if has is None:
                    has = _face_has(prop)
                in_primary, in_chain = has(ch)
                foreign = not in_primary and in_chain
            found = found or foreign
            marks.append((foreign, ch, False))
    if not found:
        return None
    # 连续同类并段
    groups: list[tuple[bool, list[tuple[str, bool]]]] = []
    for foreign, text, is_math in marks:
        if groups and groups[-1][0] == foreign:
            groups[-1][1].append((text, is_math))
        else:
            groups.append((foreign, [(text, is_math)]))
    segs: list[tuple[str, bool]] = []
    for foreign, parts in groups:
        raw = "".join(t for t, _ in parts)
        if foreign:
            segs.append((raw, False))
            continue
        core = raw.strip()
        lead = raw[: len(raw) - len(raw.lstrip())]
        tail = raw[len(raw.rstrip()) :] if core else ""
        if lead:
            segs.append((lead, False))
        if core:
            if any(is_math for _, is_math in parts):
                segs.append((core, True))
            else:
                segs.append((core.replace("\\$", "$"), False))
        if tail:
            segs.append((tail, False))
    # 相邻的普通文字段并成一段（少一次落笔；回退链逐字形照样生效）
    merged: list[tuple[str, bool]] = []
    for text, is_math in segs:
        if merged and not is_math and not merged[-1][1]:
            merged[-1] = (merged[-1][0] + text, False)
        else:
            merged.append((text, is_math))
    return merged


def _measure_with(original):
    def get_text_width_height_descent(self, s, prop, ismath):
        segs = plan(s, prop, ismath)
        if segs is None:
            return original(self, s, prop, ismath)
        width = ascent = descent = 0.0
        for text, is_math in segs:
            w, h, d = original(self, text, prop, is_math)
            width += w
            ascent = max(ascent, h - d)
            descent = max(descent, d)
        return width, ascent + descent, descent

    _carry(original, get_text_width_height_descent)
    return get_text_width_height_descent


def _draw_with(original):
    def draw_text(self, gc, x, y, s, prop, angle, ismath=False, mtext=None):
        segs = plan(s, prop, ismath)
        if segs is None:
            return original(self, gc, x, y, s, prop, angle, ismath, mtext)
        theta = math.radians(angle or 0.0)
        dx, dy = math.cos(theta), math.sin(theta)
        if self.flipy():
            dy = -dy
        measure = self.get_text_width_height_descent
        pen = 0.0
        for text, is_math in segs:
            # 段两头的空白只占位、不落笔：SVG 文字模式下浏览器会吞掉 <text> 首尾的空格，字就挪了位置
            body = text if is_math else text.strip()
            if body:
                lead = text[: len(text) - len(text.lstrip())] if not is_math else ""
                at = pen + (measure(lead, prop, False)[0] if lead else 0.0)
                # mtext 不往下传：SVG 文字模式见到它会按整个 Text 的锚点摆位置，各段就叠在一处了
                original(self, gc, x + at * dx, y + at * dy, body, prop, angle, is_math, None)
            pen += measure(text, prop, is_math)[0]

    _carry(original, draw_text)
    return draw_text


def _carry(original, wrapper) -> None:
    """包装带上被包函数的属性（`functools.update_wrapper`）再打自己的标记。

    别的包装认自己的标记判幂等（`figcapture.install_agg_unhinted_glyph_fallback` 认
    `_tavotto_unhinted_fallback`，safe worker 每次 build 都调一遍）：标记丢了它就会在同一 worker 重试时再包一层，
    可信方法面对不上、存盘那一刻的还原就丢了（`matplotlib_patched`）。"""
    functools.update_wrapper(wrapper, original)
    setattr(wrapper, _MARK, True)


def _patch(cls, name: str, wrap) -> None:
    fn = cls.__dict__.get(name)
    if fn is None or getattr(fn, _MARK, False):
        return
    setattr(cls, name, wrap(fn))


def install() -> None:
    """在 matplotlib 各渲染器的「量字 / 画字」入口装上分段排。幂等；只改**自己定义了**该方法的类。

    装上之后仍由 `arm()` 决定是否生效。"""
    from matplotlib import backend_bases, textpath
    from matplotlib.backends import (
        _backend_pdf_ps,
        backend_agg,
        backend_pdf,
        backend_ps,
        backend_svg,
    )

    for cls in (
        backend_bases.RendererBase,
        backend_agg.RendererAgg,
        _backend_pdf_ps.RendererPDFPSBase,
        backend_svg.RendererSVG,
        textpath.TextToPath,
    ):
        _patch(cls, "get_text_width_height_descent", _measure_with)
    for cls in (
        backend_bases.RendererBase,
        backend_agg.RendererAgg,
        backend_pdf.RendererPdf,
        backend_ps.RendererPS,
        backend_svg.RendererSVG,
    ):
        _patch(cls, "draw_text", _draw_with)
