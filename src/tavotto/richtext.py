"""标注文字的两种写法（旧标记 / LaTeX 子集），以及 Unicode 科学文本的解释。

**与前端 web/src/lib/richText.ts 严格同源**（同名注释与同名常量）：画布上怎么
排，导出的 PDF 就得怎么排。改一边必须同步另一边。

为什么是标记而不是富文本模型：
  * 文档 schema 不用动（TextObject.text 仍是一个字符串），旧文档零影响；
  * 同一套语法在图内元素那边直接就是 matplotlib mathtext（`cm$^{-1}$`），
    用户学一次；
  * 只有 `^{`/`_{` 才触发，正文里孤零零的 `^` 或 `_` 原样显示——存量文字
    不会因为升级突然变形。
需要字面量的 `^`/`_`/`\\` 时写 `\\^`、`\\_`、`\\\\`。

**第二种写法 `latex`**（ADR 0152，所见即所得编辑框）：对象带 `text_format == "latex"` 时，
同一个字符串按 LaTeX 子集读——`\\textit{}` / `\\emph{}` / `\\textbf{}` /
`\\textsuperscript{}` / `\\textsubscript{}` / `$…$` 公式。没有这个字段的对象一律按上面的
旧标记读，逐字不变。见本文件「LaTeX 子集」一节。

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


#: 标注文字的写法。缺省（None / ""）= 旧标记；闭集，与 `richText.ts` 的 `TEXT_FORMATS` 同源。
TEXT_FORMATS = ("latex",)


class TextRun(NamedTuple):
    text: str
    script: str  # "" | "sup" | "sub"
    italic: bool = False
    bold: bool = False
    #: True = 这一段是公式源码（`$…$` 里面那串），`text` 原样是用户写的 mathtext
    math: bool = False
    #: True = 读不懂的命令原文（`\\textsc{abc}`）：照字面显示，写回时原样、不转义
    raw: bool = False


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


def parse_runs(text: str, text_format: str | None = None) -> list[TextRun]:
    """标记文本 → 片段列表。`text_format == "latex"` 走 LaTeX 子集，其余走旧标记。

    解析失败的片段（没有配对的 `}`）按字面量原样保留，绝不吞掉用户的字符。
    """
    if text_format == "latex":
        return _parse_latex(text)
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


def plain_text(text: str, text_format: str | None = None) -> str:
    """去掉全部标记，只留可读文本（公式段留它的源码）。"""
    return "".join(r.text for r in parse_runs(text, text_format))


def has_scripts(text: str, text_format: str | None = None) -> bool:
    return any(r.script for r in parse_runs(text, text_format))


# ---------------------------------------------------------------------------
# LaTeX 子集（`text_format == "latex"`，ADR 0152）
# ---------------------------------------------------------------------------
#
# **宽进**：用户从论文里粘贴过来的文字要尽量读对；读不懂的命令连同它的花括号原样当文字，
# 绝不吞字。**严出**在前端 `serializeRuns(…, "latex")`：编辑框写回的永远是规范形。
#
#     \textit{…} \emph{…}           斜体
#     \textbf{…}                    粗体
#     \textup{…} \textmd{…}         取消斜体 / 取消粗体；\textnormal{…} 两者都取消
#     \textsuperscript{…}           上标；\textsubscript{…} 下标
#     $…$  $$…$$  \(…\)  \[…\]      公式（源码原样进 TextRun.text，math=True）
#     {…}                           分组，花括号本身不显示
#     \$ \% \& \# \_ \{ \} \^{} \~{}   字面量；\textbackslash{} 是反斜杠
#     ~                             不换行空格；\\ 换行；\␣ 空格
#     其余：原样是字（裸 `^` `_` `%` `&` `#` 也是字——宽进）

_STYLE_CMDS = {
    "textit": ("italic", True),
    "emph": ("italic", True),
    "textup": ("italic", False),
    "textbf": ("bold", True),
    "textmd": ("bold", False),
    "textsuperscript": ("script", "sup"),
    "textsubscript": ("script", "sub"),
}
_LITERAL_ESCAPES = "$%&#_{}"
_ACCENT_LITERALS = "^~"
_NBSP = "\u00a0"


class _St(NamedTuple):
    script: str = ""
    italic: bool = False
    bold: bool = False


def _find_unescaped(text: str, needle: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i] == "\\":
            if text.startswith(needle, i):  # needle 本身以反斜杠开头（`\)` / `\]`）
                return i
            i += 2
            continue
        if text.startswith(needle, i):
            return i
        i += 1
    return -1


def _parse_latex(text: str) -> list[TextRun]:
    runs: list[TextRun] = []
    _latex_into(text, _St(), runs)
    return runs


def _emit(runs: list[TextRun], text: str, st: _St, math: bool = False, raw: bool = False) -> None:
    if not text:
        return
    last = runs[-1] if runs else None
    if (
        last is not None
        and not math
        and not last.math
        and last.raw == raw
        and (last.script, last.italic, last.bold) == (st.script, st.italic, st.bold)
    ):
        runs[-1] = last._replace(text=last.text + text)
    else:
        runs.append(TextRun(text, st.script, st.italic, st.bold, math, raw))


def _latex_into(text: str, st: _St, runs: list[TextRun]) -> None:  # noqa: C901 —— 一个扫描器
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt.isascii() and nxt.isalpha():
                j = i + 1
                while j < n and text[j].isascii() and text[j].isalpha():
                    j += 1
                name = text[i + 1 : j]
                has_group = j < n and text[j] == "{"
                close = _match_brace(text, j) if has_group else -1
                if name in _STYLE_CMDS and close > 0:
                    field, value = _STYLE_CMDS[name]
                    _latex_into(text[j + 1 : close], st._replace(**{field: value}), runs)
                    i = close + 1
                    continue
                if name == "textnormal" and close > 0:
                    _latex_into(text[j + 1 : close], st._replace(italic=False, bold=False), runs)
                    i = close + 1
                    continue
                if name == "textbackslash":
                    _emit(runs, "\\", st)
                    if text.startswith("{}", j):
                        i = j + 2
                    else:  # 控制词后面的空格是分隔符（LaTeX 规则）
                        while j < n and text[j] == " ":
                            j += 1
                        i = j
                    continue
                # 不认识的命令：连同紧跟的那组花括号原样是字
                end = close + 1 if close > 0 else j
                _emit(runs, text[i:end], st, raw=True)
                i = end
                continue
            if nxt in _LITERAL_ESCAPES:
                _emit(runs, nxt, st)
                i += 2
                continue
            if nxt in _ACCENT_LITERALS:
                _emit(runs, nxt, st)
                i += 4 if text.startswith("{}", i + 2) else 2
                continue
            if nxt == "\\":
                _emit(runs, "\n", st)
                i += 2
                continue
            if nxt == " ":
                _emit(runs, " ", st)
                i += 2
                continue
            if nxt in "([":
                closer = "\\)" if nxt == "(" else "\\]"
                end = _find_unescaped(text, closer, i + 2)
                if end >= 0:
                    _emit(runs, text[i + 2 : end], st, math=True)
                    i = end + 2
                    continue
            _emit(runs, ch, st)
            i += 1
            continue
        if ch == "$":
            double = text.startswith("$$", i)
            delim = "$$" if double else "$"
            end = _find_unescaped(text, delim, i + len(delim))
            if end >= 0:
                _emit(runs, text[i + len(delim) : end], st, math=True)
                i = end + len(delim)
                continue
            _emit(runs, ch, st)
            i += 1
            continue
        if ch == "{":
            close = _match_brace(text, i)
            if close > 0:
                _latex_into(text[i + 1 : close], st, runs)
                i = close + 1
                continue
        if ch == "~":
            _emit(runs, _NBSP, st)
            i += 1
            continue
        _emit(runs, ch, st)
        i += 1


_ESCAPE_OUT = {
    "\\": "\\textbackslash{}",
    "$": "\\$",
    "%": "\\%",
    "&": "\\&",
    "#": "\\#",
    "_": "\\_",
    "{": "\\{",
    "}": "\\}",
    "^": "\\^{}",
    "~": "\\~{}",
    _NBSP: "~",
}


def serialize_latex(runs: list[TextRun]) -> str:
    """片段 → LaTeX 子集的**规范形**（与 `richText.ts` 的 `serializeRuns(…, 'latex')` 逐字同源）。

    相邻片段按「粗体 → 斜体 → 上下标」逐层分组，每组只包一次命令：`\\textbf{a \\textit{b}} c`
    原样往返。公式段写回 `$源码$`。`parse_runs(serialize_latex(rs), "latex")` 与 `rs` 等价。"""
    return _serialize_level(list(runs), 0)


_LEVELS = ("bold", "italic", "script")


def _serialize_level(runs: list[TextRun], level: int) -> str:
    if level == len(_LEVELS):
        return "".join(
            f"${r.text}$"
            if r.math
            else r.text
            if r.raw
            else "".join(_ESCAPE_OUT.get(ch, ch) for ch in r.text)
            for r in runs
        )
    field = _LEVELS[level]
    out: list[str] = []
    i = 0
    while i < len(runs):
        value = getattr(runs[i], field)
        j = i
        while j < len(runs) and getattr(runs[j], field) == value:
            j += 1
        inner = _serialize_level(runs[i:j], level + 1)
        if field == "bold" and value:
            inner = f"\\textbf{{{inner}}}"
        elif field == "italic" and value:
            inner = f"\\textit{{{inner}}}"
        elif field == "script" and value:
            inner = f"\\text{'superscript' if value == 'sup' else 'subscript'}{{{inner}}}"
        out.append(inner)
        i = j
    return "".join(out)


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

    def emit(text: str, script: str, like: TextRun) -> None:
        if not text:
            return
        last = out[-1] if out else None
        if (
            last is not None
            and not last.math
            and (last.script, last.italic, last.bold) == (script, like.italic, like.bold)
        ):
            out[-1] = last._replace(text=last.text + text)
        else:
            out.append(TextRun(text, script, like.italic, like.bold))

    for run in runs:
        if run.math:  # 公式源码不是字，不折
            out.append(run)
            continue
        if run.script:
            emit(run.text, run.script, run)
            continue
        i = 0
        text = run.text
        while i < len(text):
            script = _script_of(text[i])
            if not script:
                emit(text[i], "", run)
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
                emit(base, script, run)
            else:
                emit(chunk, "", run)
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


# ---------------------------------------------------------------------------
# 一眼读得懂的字（小缩略图，ADR 0152）
# ---------------------------------------------------------------------------
#
# 画布列表、版本列表的小缩略图不排公式（几十张图各发一次排版请求不值，它只回答「哪一页 / 哪一版」），
# 但 `plain_text` 留的是公式源码（`\frac{RT^2}{k}`），一眼读不出来。`glance_text` 把常见的
# mathtext 读成近似的 Unicode 文字（`RT²/k`）。**只给人认页面**：不是排版，不进导出、不写回；
# 读不懂的命令去掉反斜杠留名字，绝不吞掉用户写的字母。下面几张表与 `richText.ts` 同名同源，
# 由 `tests/golden/rich_text_vectors.json` 的 `glance` 一节逐项看护。

#: 命令 → 字符：希腊字母与常用符号。
GLANCE_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ",
    "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠", "approx": "≈",
    "sim": "∼", "simeq": "≃", "equiv": "≡", "propto": "∝", "ll": "≪", "gg": "≫",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏", "int": "∫", "oint": "∮",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "leftrightarrow": "↔",
    "uparrow": "↑", "downarrow": "↓", "degree": "°", "circ": "∘", "prime": "′",
    "AA": "Å", "hbar": "ℏ", "ell": "ℓ", "cdots": "⋯", "ldots": "…", "dots": "…",
    "langle": "⟨", "rangle": "⟩", "lbrace": "{", "rbrace": "}", "vert": "|", "Vert": "‖", "mid": "|",
    "in": "∈", "notin": "∉", "subset": "⊂", "cup": "∪", "cap": "∩", "forall": "∀", "exists": "∃",
    "perp": "⊥", "parallel": "∥", "angle": "∠", "star": "⋆", "ast": "∗", "bullet": "•",
    "dagger": "†", "emptyset": "∅", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ",
    "wedge": "∧", "vee": "∨", "neg": "¬",
}  # fmt: skip

#: 函数名命令：照名字写出来，两边留空格（`\ln x` → `ln x`）。
GLANCE_FUNCS = (
    "arccos", "arcsin", "arctan", "arg", "cos", "cosh", "cot", "coth", "csc", "deg", "det", "dim",
    "exp", "gcd", "hom", "inf", "ker", "lg", "lim", "liminf", "limsup", "ln", "log", "max", "min",
    "Pr", "sec", "sin", "sinh", "sup", "tan", "tanh",
)  # fmt: skip

#: 只换字体的命令：留下参数本身。
GLANCE_UNWRAP = (
    "mathrm", "mathit", "mathbf", "mathsf", "mathtt", "mathcal", "mathbb", "mathfrak", "mathscr",
    "mathdefault", "text", "textrm", "textit", "textbf", "operatorname", "boldsymbol",
)  # fmt: skip

#: 字母上的记号：参数只有一个字符时加组合附加符（`\hat{x}` → `x̂`），否则只留参数。
GLANCE_ACCENTS = {
    "hat": "̂", "widehat": "̂", "bar": "̄", "overline": "̅", "vec": "⃗",
    "dot": "̇", "ddot": "̈", "tilde": "̃", "widetilde": "̃",
}  # fmt: skip

#: 间距命令（含 `\,` 这类单符号的）：都读成一个空格，`\!` 读成没有。
GLANCE_SPACES = {",": " ", ":": " ", ";": " ", " ": " ", "!": "", "quad": " ", "qquad": " "}

#: 只影响排版、不带字的命令：去掉。
GLANCE_DROP = (
    "left", "right", "displaystyle", "textstyle", "scriptstyle", "limits", "nolimits",
    "big", "Big", "bigg", "Bigg",
)  # fmt: skip

_SUP_OF = {base: ch for ch, base in SUPERSCRIPT_BASE.items()}
_SUB_OF = {base: ch for ch, base in SUBSCRIPT_BASE.items()}
#: 分式 / 根号的一边含这些字符就加括号（`\frac{a+b}{c}` → `(a+b)/c`）。
_GLANCE_OPS = "+-=/ ×·±<>≤≥≈≠"
_FRACS = ("frac", "dfrac", "tfrac", "cfrac")


def glance_text(text: str, text_format: str | None = None) -> str:
    """一眼读得懂的字：公式读成近似的 Unicode，上下标能换成 Unicode 上下标字符的就换。"""
    out = []
    for r in parse_runs(text, text_format):
        if r.math:
            out.append(_glance_math(r.text))
        elif r.script:
            out.append(_glance_script(r.text, "^" if r.script == "sup" else "_", fallback=r.text))
        else:
            out.append(r.text)
    return "".join(out)


def _glance_math(src: str) -> str:
    return " ".join(_glance_seq(src).split()).replace("( ", "(").replace(" )", ")")


def _glance_seq(src: str) -> str:
    out = []
    i = 0
    while i < len(src):
        piece, i = _glance_item(src, i)
        out.append(piece)
    return "".join(out)


def _glance_item(src: str, i: int) -> tuple[str, int]:
    """从 `i` 读一项，回 (读出的字, 下一项起点)。"""
    ch = src[i]
    if ch.isspace() or ch == "}":  # 公式里的空格不显示；落单的 `}` 去掉
        return "", i + 1
    if ch == "{":
        close = _match_brace(src, i)
        if close < 0:
            return "", i + 1
        return _glance_seq(src[i + 1 : close]), close + 1
    if ch in "^_":
        arg, j = _glance_arg(src, i + 1)
        return _glance_script(arg, ch, fallback=None), j
    if ch == "\\":
        return _glance_command(src, i)
    if ch == "~":
        return " ", i + 1
    return ch, i + 1


def _glance_arg(src: str, i: int) -> tuple[str, int]:
    while i < len(src) and src[i].isspace():
        i += 1
    if i >= len(src):
        return "", i
    return _glance_item(src, i)


def _glance_script(arg: str, kind: str, fallback: str | None) -> str:
    """上下标：每个字都有 Unicode 上下标字符就换；否则公式里写成 `^x` / `^(ab)`，正文片段原样。"""
    arg = arg.strip()
    if not arg:
        return ""
    if kind == "^" and arg == "∘":
        return "°"
    if kind == "^" and all(c in "′″" for c in arg):
        return arg
    table = _SUP_OF if kind == "^" else _SUB_OF
    if all(c in table for c in arg):
        return "".join(table[c] for c in arg)
    if fallback is not None:
        return fallback
    return kind + (f"({arg})" if len(arg) > 1 else arg)


def _glance_paren(s: str) -> str:
    return f"({s})" if len(s) > 1 and any(c in _GLANCE_OPS for c in s) else s


def _glance_command(src: str, i: int) -> tuple[str, int]:  # noqa: C901 —— 一张命令表
    if i + 1 >= len(src):
        return "", i + 1
    nxt = src[i + 1]
    if not nxt.isascii() or not nxt.isalpha():
        if nxt in GLANCE_SPACES:
            return GLANCE_SPACES[nxt], i + 2
        if nxt == "\\":
            return " ", i + 2
        return nxt, i + 2  # \{ \$ \% \_ …：字面量
    j = i + 1
    while j < len(src) and src[j].isascii() and src[j].isalpha():
        j += 1
    name = src[i + 1 : j]
    if name in _FRACS:
        num, j = _glance_arg(src, j)
        den, j = _glance_arg(src, j)
        return f"{_glance_paren(num)}/{_glance_paren(den)}", j
    if name == "sqrt":
        k = j
        while k < len(src) and src[k].isspace():
            k += 1
        if k < len(src) and src[k] == "[":
            end = src.find("]", k)
            if end > 0:
                j = end + 1
        arg, j = _glance_arg(src, j)
        return "√" + _glance_paren(arg), j
    if name in GLANCE_UNWRAP:
        return _glance_arg(src, j)
    if name in GLANCE_ACCENTS:
        arg, j = _glance_arg(src, j)
        return (arg + GLANCE_ACCENTS[name] if len(arg) == 1 else arg), j
    if name in GLANCE_SPACES:
        return GLANCE_SPACES[name], j
    if name in GLANCE_DROP:
        if name in ("left", "right") and j < len(src) and src[j] == ".":
            j += 1
        return "", j
    if name in GLANCE_SYMBOLS:
        return GLANCE_SYMBOLS[name], j
    if name in GLANCE_FUNCS:
        k = j
        while k < len(src) and src[k].isspace():
            k += 1
        # 紧跟括号、上下标或到头就不留后面的空格（`\sin(x)` → `sin(x)`，`\lim_{x}` → `limₓ`）
        tight = k >= len(src) or src[k] in "([^_" or src.startswith("\\left", k)
        return f" {name}" + ("" if tight else " "), j
    return name, j
