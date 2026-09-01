"""字形归属计划：一段文字里的每个字符**由哪张脸画出来**。

**与前端 `web/src/lib/glyphPlan.ts` 严格同源**（同名常量、同一套分层顺序、
同一份看护向量 `tests/golden/glyph_plan_vectors.json`）。改一边必须同步另一边。

## 为什么要有这一层

在这之前，画布文字的分段判据是一句 `ord(ch) > 0x2E80`——**它量的是码位，
不是「这张脸画不画得出这个字」**。两个方向都会错：

* `₂`（U+2082）在拉丁段，base-14 没有它，于是交给 PyMuPDF，PyMuPDF 自己
  悄悄换了一张 `Noto Serif Regular` 把它画出来。画面是对的，**但没有任何人
  知道刚才换了脸**——用户选的是 sans-serif，那个下标是衬线的。
* `━`（U+2501）一类制表符在拉丁段，base-14 没有、隐式回退也没有，而我们
  手里的 CJK 脸恰好有——现行路由不会去问它，结果是一个本可以画出来的方框。

本模块把这件事变成一份**可以被读出来的计划**：每个字符属于哪一层、哪些字
根本画不出来。落笔（`_draw_text`）、量宽（`text_width`）、检查（预检的
`glyph-missing`）与前端预览读的是同一份计划，而不是各自再写一遍分段。

## 四层，顺序不可交换

    primary   请求的那个族的 base-14 脸自己画得出
    cjk       中日韩脸画得出（**只有码位在 CJK 段、或前两层都没有时**才轮到它）
    fallback  前两层都没有，但 PyMuPDF 自己挑得出一张脸（实测是 Noto Serif）
    missing   谁都画不出——导出上就是一个方框，必须进问题系统

顺序里那句「只有码位在 CJK 段」是刻意的：把它去掉，`₂` 会从隐式回退的
衬线脸改判到中日韩脸（Droid Sans Fallback），**既有文档的像素会变**。
本模块的分层与改造前逐字符等价，只多救回第 4 步那 87 个码位（实测）。

## 覆盖判据从哪来

Python 侧问**真字体**（`pymupdf_backend` 提供 oracle），所以导出那一端永远
是对的。浏览器里没有字体引擎，前端读生成物
`src/tavotto/pdfbackend/canvas_coverage.json`（`scripts/gen_canvas_coverage.py
--check` 看护它与真字体一致）。**两侧的算法同源，oracle 不同源**——这条
差异是明示的，看护它的是那个 `--check` 和 golden 向量。

纯标准库。
"""

from __future__ import annotations

from typing import Callable, NamedTuple

#: 分层名。**闭集**，顺序即优先级（与 `glyphPlan.ts` 的 `GLYPH_LAYERS` 同源）。
GLYPH_LAYERS = ("primary", "cjk", "fallback", "missing")

#: 「按中日韩脸走」的码位下界。这是**排版分段**的历史判据，保留它是为了让
#: 分层结果与改造前逐字符等价；它不是覆盖判据（覆盖由 oracle 回答）。
CJK_START = 0x2E80


class GlyphRun(NamedTuple):
    text: str
    layer: str  # GLYPH_LAYERS 之一


class Coverage(NamedTuple):
    """三层覆盖的 oracle。每个是 `(codepoint) -> bool`。"""

    primary: Callable[[int], bool]
    cjk: Callable[[int], bool]
    fallback: Callable[[int], bool]


def layer_of(cp: int, cov: Coverage) -> str:
    """一个码位归哪一层。**四步的顺序不可交换**（见模块 docstring）。"""
    if cov.primary(cp):
        return "primary"
    if cp > CJK_START and cov.cjk(cp):
        return "cjk"
    if cov.fallback(cp):
        return "fallback"
    if cov.cjk(cp):
        return "cjk"
    return "missing"


def plan(text: str, cov: Coverage) -> list[GlyphRun]:
    """字符串 → 分层片段。相邻同层合并；空串回空表。"""
    runs: list[GlyphRun] = []
    for ch in text:
        layer = layer_of(ord(ch), cov)
        if runs and runs[-1].layer == layer:
            runs[-1] = GlyphRun(runs[-1].text + ch, layer)
        else:
            runs.append(GlyphRun(ch, layer))
    return runs


def missing_chars(text: str, cov: Coverage) -> list[str]:
    """这段文字里**画不出来**的字符（去重、保出现顺序）。

    问题面板要把它们逐字列给用户看，所以顺序是原文顺序，不是码位顺序。
    """
    seen: dict[str, None] = {}
    for ch in text:
        if layer_of(ord(ch), cov) == "missing":
            seen.setdefault(ch, None)
    return list(seen)


def substituted_chars(text: str, cov: Coverage) -> list[str]:
    """落在 **`fallback`** 层的字符：渲染器自己挑了一张脸把它画出来了。

    与 `missing_chars` 分开：那一档是「画不出来」，这一档是「画出来了，但
    不是你选的那张脸」——压成一句就等于把「方框」和「字体不一致」说成同
    一件事。

    **`cjk` 层不算在内。** 中日韩在这条路上只有一张脸（能力限制，界面上已经
    说清楚），它**不随用户的任何选择变化**：为一个恒定的、改不动的限制在每
    一条中文标注上挂一条建议，只会训练用户忽略整个问题面板。真正值得说的是
    `fallback`——那张脸是渲染器**自己挑的**，与请求的族和字重都无关，所以
    一个符号会让整段文字里冒出另一种字体。
    """
    seen: dict[str, None] = {}
    for ch in text:
        if layer_of(ord(ch), cov) == "fallback":
            seen.setdefault(ch, None)
    return list(seen)


def text_diagnostics(text: str, cov: Coverage, interpretation: str | None = None):
    """一段画布文字**最终画出来**的字里，哪些是方框、哪些换了脸。

    量的是渲染表示，不是原文：行内标记（`^{…}`）在这一步已经被拆掉，
    Unicode 上下标该合成的已经合成——拿原文去问会报出一批不会发生的方框，
    而假红比假绿更难查（用户会去修一个不存在的问题）。

    与 `web/src/lib/glyphPlan.ts` 的 `textDiagnostics` 严格同源。
    """
    from . import richtext

    runs = richtext.interpret_runs(
        richtext.parse_runs(text),
        is_primary=cov.primary,
        is_drawable=lambda cp: layer_of(cp, cov) != "missing",
        mode=interpretation or richtext.DEFAULT_INTERPRETATION,
    )
    body = "".join(r.text for r in runs)
    return missing_chars(body, cov), substituted_chars(body, cov)


def ranges_of(pred: Callable[[int], bool], lo: int, hi: int) -> list[list[int]]:
    """把一个覆盖判据压成闭区间表（生成覆盖表用；`hi` 不含）。"""
    out: list[list[int]] = []
    start = prev = -2
    for cp in range(lo, hi):
        if not pred(cp):
            continue
        if cp == prev + 1:
            prev = cp
        else:
            if start >= 0:
                out.append([start, prev])
            start = prev = cp
    if start >= 0:
        out.append([start, prev])
    return out


def in_ranges(ranges: list[list[int]]) -> Callable[[int], bool]:
    """区间表 → 覆盖判据（二分）。"""
    import bisect

    starts = [r[0] for r in ranges]
    ends = [r[1] for r in ranges]

    def has(cp: int) -> bool:
        i = bisect.bisect_right(starts, cp) - 1
        return i >= 0 and cp <= ends[i]

    return has


def coverage_from_table(table: dict) -> Coverage:
    """生成物 `canvas_coverage.json` → oracle。"""
    layers = table["layers"]
    return Coverage(
        primary=in_ranges(layers["primary"]),
        cjk=in_ranges(layers["cjk"]),
        fallback=in_ranges(layers["fallback"]),
    )


#: 覆盖表的文件名（包内与仓库内同名，别处不得再放第二份）。
COVERAGE_FILE = "canvas_coverage.json"

_TABLE_CACHE: dict | None = None


def coverage_table_path():
    """覆盖表的位置：包内 → 源码树。

    包内那条走 `importlib.resources`——装成 wheel 之后 `__file__` 的上级是
    site-packages（同 `engine/profiles.profiles_path()` 的理由）。
    """
    from pathlib import Path

    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath("pdfbackend", COVERAGE_FILE)))
        if cand.is_file():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    from pathlib import Path as _P

    return _P(__file__).resolve().parent / "pdfbackend" / COVERAGE_FILE


def canvas_coverage() -> Coverage:
    """画布文字覆盖的 oracle，**读的是与前端同一份生成物**。

    预检两个求值器都走这条：它们要给出同一个答案，而 Python 侧「问真字体」
    对预检没有好处——那只会让两边在覆盖漂移时给出不同的结论，而漂移本身
    由 `scripts/gen_canvas_coverage.py --check` 单独看住。真正落笔的
    `pdfbackend` 仍然问真字体。
    """
    global _TABLE_CACHE
    if _TABLE_CACHE is None:
        import json

        _TABLE_CACHE = json.loads(coverage_table_path().read_text(encoding="utf-8"))
    return coverage_from_table(_TABLE_CACHE)
