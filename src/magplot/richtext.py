"""标注文字的行内标记：上标 `^{…}`、下标 `_{…}`。

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
    script: str        # "" | "sup" | "sub"


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
            buf += text[i + 1]           # 转义：\^ \_ \\ → 字面量
            i += 2
            continue
        kind = _OPENER.get(ch)
        if kind and i + 1 < len(text) and text[i + 1] == "{":
            close = _match_brace(text, i + 1)
            if close > 0:
                if buf:
                    runs.append(TextRun(buf, ""))
                    buf = ""
                inner = text[i + 2:close]
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


def run_metrics(script: str, size: float) -> tuple[float, float]:
    """片段的 (字号, 基线偏移)。基线偏移为正 = 往上抬（PDF 的 y 向下，
    调用方要减）。与前端 TextView 里那三个常量同源。"""
    if script == "sup":
        return size * SCRIPT_SIZE, size * SUP_RISE
    if script == "sub":
        return size * SCRIPT_SIZE, -size * SUB_DROP
    return size, 0.0
