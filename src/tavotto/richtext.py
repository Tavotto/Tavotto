"""标注文字的行内标记：上标 `^{…}`、下标 `_{…}`，以及 Unicode 科学文本的解释。

**与前端 web/src/lib/richText.ts 严格同源**（同名注释与同名常量）：画布上怎么
排，导出的 PDF 就得怎么排。改一边必须同步另一边。

为什么是标记而不是富文本模型：
  * 文档 schema 不用动（TextObject.text 仍是一个字符串），旧文档零影响；
  * 同一套语法在图内元素那边直接就是 matplotlib mathtext（`cm$^{-1}$`），
    用户学一次；
  * 只有 `^{`/`_{` 才触发，正文里孤零零的 `^` 或 `_` 原样显示——存量文字
    不会因为升级突然变形。
需要字面量的 `^`/`_`/`\\` 时写 `\\^`、`\\_`、`\\\\`。

纯标准库。
"""

from __future__ import annotations

from typing import NamedTuple

# 上下标字号 = 正文的这个比例
SCRIPT_SIZE = 0.62
# 上标基线抬高 = 正文字号 × 这个比例
SUP_RISE = 0.42
# 下标基线下沉 = 正文字号 × 这个比例
SUB_DROP = 0.18

_OPENER = {"^": "sup", "_": "sub"}


class TextRun(NamedTuple):
    text: str
    script: str  # "" | "sup" | "sub"


def _match_brace(text: str, open_at: int) -> int:
    """从 `{` 起找配对的 `}`（支持嵌套）；找不到回 -1。"""
    depth = 0
    i = open_at
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def parse_runs(text: str) -> list[TextRun]:
    """标记文本 → 片段列表。

    解析失败的片段（没有配对的 `}`）按字面量原样保留，绝不吞掉用户的字符。
    """
    runs: list[TextRun] = []
    buf = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in "^_\\":
            buf += text[i + 1]  # 转义：\^ \_ \\ → 字面量
            i += 2
            continue
        kind = _OPENER.get(ch)
        if kind and i + 1 < len(text) and text[i + 1] == "{":
            close = _match_brace(text, i + 1)
            if close > 0:
                if buf:
                    runs.append(TextRun(buf, ""))
                    buf = ""
                inner = text[i + 2 : close]
                if inner:
                    runs.append(TextRun(inner, kind))
                i = close + 1
                continue
        buf += ch
        i += 1
    if buf:
        runs.append(TextRun(buf, ""))
    return runs


def plain_text(text: str) -> str:
    """去掉全部标记，只留可读文本。"""
    return "".join(r.text for r in parse_runs(text))


def has_scripts(text: str) -> bool:
    return any(r.script for r in parse_runs(text))


# ---------------------------------------------------------------------------
# 受控科学文本解释：Unicode 上下标字符 → 渲染用的 sup/sub 片段
# ---------------------------------------------------------------------------
#
# **绝不改写 raw text。** `parse_runs` ↔ `serialize_runs` 那一对仍然是文档里
# 那个字符串的往返；本节的 `interpret_runs` 只生成**渲染表示**，落在渲染器与
# 预检那一侧。用户复制走的、保存下去的、重开看到的，永远是他自己打的字。
#
# ### 合成是有代价的，所以默认只在「不然就是方框」时才合成
#
# 把 `⁵` 画成「62% 的 5 抬高 42%」以后，**PDF 文本层里那个字符就是 `5`**
# ——实测导出后抽回来的文本从 `×10⁵` 变成 `×105`。审稿人复制走的是 105，
# 这是语义损坏，比「上标是另一张脸画的」严重。（`^{…}` 标记那条路本来就
# 有这个性质，那是用户自己写的；把它悄悄扩到所有 Unicode 上标字符上不是。）
#
# 所以两档，各自诚实：
#
#     auto（默认）  只有这一串里**有字符谁都画不出**（否则就是方框）时才合成。
#                   今天画得对的东西一个像素不变，文本层也一个字符不变。
#     scientific    认得的 Unicode 上下标一律合成。字体统一了，代价是文本层
#                   里的上标数字变成普通数字——所以它必须是用户明确选的。
#
# 两档都还要求**折出来的基础字符正文脸全画得出**，否则白折一场。
#
# 「整串一起折」是刻意的：`m⁻²` 里两个字符的处境可能不同，逐字符处理会得到
# 一个小的合成减号紧挨着一个大的设计上标，比原样还难看。
#
# ### 支持的字符表是闭集
#
# 不在表里的字符原样保留（连同它自己的分层与回退）。表里每一项都是
# 「Unicode 上/下标字符 → 它的基础字符」；基础字符画不出时同样不折。

#: 上标字符 → 基础字符。**闭集**，与 `richText.ts` 的 `SUPERSCRIPT_BASE` 同源。
SUPERSCRIPT_BASE = {
    "\u2070": "0",
    "\u00b9": "1",
    "\u00b2": "2",
    "\u00b3": "3",
    "\u2074": "4",
    "\u2075": "5",
    "\u2076": "6",
    "\u2077": "7",
    "\u2078": "8",
    "\u2079": "9",
    "\u207a": "+",
    "\u207b": "-",
    "\u207c": "=",
    "\u207d": "(",
    "\u207e": ")",
    "\u207f": "n",
    "\u2071": "i",
}

#: 下标字符 → 基础字符。**闭集**，与 `richText.ts` 的 `SUBSCRIPT_BASE` 同源。
SUBSCRIPT_BASE = {
    "\u2080": "0",
    "\u2081": "1",
    "\u2082": "2",
    "\u2083": "3",
    "\u2084": "4",
    "\u2085": "5",
    "\u2086": "6",
    "\u2087": "7",
    "\u2088": "8",
    "\u2089": "9",
    "\u208a": "+",
    "\u208b": "-",
    "\u208c": "=",
    "\u208d": "(",
    "\u208e": ")",
    "\u2090": "a",
    "\u2091": "e",
    "\u2092": "o",
    "\u2093": "x",
    "\u2095": "h",
    "\u2096": "k",
    "\u2097": "l",
    "\u2098": "m",
    "\u2099": "n",
    "\u209a": "p",
    "\u209b": "s",
    "\u209c": "t",
}

#: 解释档位。**没有 `math` 这一档**：画布文字不经 matplotlib，摆一个不存在的
#: 模式等于一句做不到的承诺（图内文字那侧的 `$…$` 才是 engine mathtext，
#: 见 `web/src/lib/typography.ts` 的 `mathTextModeOf`）。
TEXT_INTERPRETATIONS = ("auto", "scientific")
DEFAULT_INTERPRETATION = "auto"


def _script_of(ch: str) -> str:
    if ch in SUPERSCRIPT_BASE:
        return "sup"
    if ch in SUBSCRIPT_BASE:
        return "sub"
    return ""


def has_scientific_chars(text: str) -> bool:
    """这段文字里有没有本节认得的 Unicode 上下标字符。

    界面靠它决定要不要露出「解释方式」那一行——没有这类字符时那个选择对
    用户没有任何意义，摆出来只是噪音。
    """
    return any(_script_of(ch) for ch in text)


def interpret_runs(runs, is_primary=None, is_drawable=None, mode=DEFAULT_INTERPRETATION):
    """标记片段 → **渲染用**片段：受控地把 Unicode 上下标折成合成上下标。

    `is_primary(codepoint)`  正文那张脸自己画得出这个字吗
    `is_drawable(codepoint)` 任何一层画得出吗（False = 导出上是个方框）
    `mode`                   `auto`（只救方框）/ `scientific`（一律合成）

    判据缺席（任一个是 `None`）时**一条都不折**——不猜一张默认覆盖表出来。

    只处理 `script == ""` 的片段：已经在 `^{…}` 里的东西是用户显式写的，
    不再叠一层。
    """
    if is_primary is None or is_drawable is None:
        return list(runs)
    scientific = mode == "scientific"
    out: list[TextRun] = []

    def emit(text: str, script: str) -> None:
        if not text:
            return
        if out and out[-1].script == script:
            out[-1] = TextRun(out[-1].text + text, script)
        else:
            out.append(TextRun(text, script))

    for run in runs:
        if run.script:
            emit(run.text, run.script)
            continue
        i = 0
        text = run.text
        while i < len(text):
            script = _script_of(text[i])
            if not script:
                emit(text[i], "")
                i += 1
                continue
            j = i
            while j < len(text) and _script_of(text[j]) == script:
                j += 1
            chunk = text[i:j]
            table = SUPERSCRIPT_BASE if script == "sup" else SUBSCRIPT_BASE
            base = "".join(table[c] for c in chunk)
            worth = any(
                (not is_primary(ord(c))) if scientific else (not is_drawable(ord(c))) for c in chunk
            )
            possible = all(is_primary(ord(c)) for c in base)
            if worth and possible:
                emit(base, script)
            else:
                emit(chunk, "")
            i = j
    return out


def run_metrics(script: str, size: float) -> tuple[float, float]:
    """片段的 (字号, 基线偏移)。基线偏移为正 = 往上抬（PDF 的 y 向下，
    调用方要减）。与前端 TextView 里那三个常量同源。"""
    if script == "sup":
        return size * SCRIPT_SIZE, size * SUP_RISE
    if script == "sub":
        return size * SCRIPT_SIZE, -size * SUB_DROP
    return size, 0.0
