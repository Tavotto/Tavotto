"""从 TS 源码里读出一个**导出的字符串数组常量**的真实取值。

为什么不用正则直接啃：正则看不见语法结构，于是**注释和无关的字符串字面量
都满足它**。`test_telemetry_disclosure.py` 上真实发生过的形状是——在改过的
活声明前面留一份注释掉的旧赋值，`re.search` 取的是**第一处**匹配，也就是
那段注释；于是界面渲染出来的披露已经与后端 `EVENTS` 漂开了，门禁却是绿的。
这是本仓库最在意的「空门禁」家族：判据在，跑了，永远绿。

这里的做法是**先把注释与字符串字面量的内容抹掉，再在剩下的代码上定位结构**：

1. 一遍左到右的扫描，认出行注释 / 块注释 / 单引号 / 双引号 / 模板串，把它们
   整段（含引号）换成等长的空格——偏移量因此与原文一一对应，同时记下每个
   字符串字面量**内容**的区间；
2. 在抹干净的代码上找 `export const <名字> = [`。**必须带 `export`**：界面
   消费的是导出的那个绑定，没导出的同名局部量不算数。找到零处或两处以上都
   当场红——「有两份声明」正是漂移的温床；
3. 方括号配对同样在抹干净的代码上做（字符串 / 注释里的括号已经不在了）；
4. 括号之间除了字符串字面量、逗号、空白**不许有别的东西**。展开
   （`...OTHER`）、变量、计算值一律红：读不出确切取值时，宁可报错也不能猜——
   一个语义错的精确值比一个诚实的失败更坏；
5. 数组内的字符串字面量按出现顺序返回，**顺序是内容的一部分**。

**已知边界**（写在明处，别让下一个人以为它是完整的 TS 解析器）：正则字面量
不认。`/` 只有后面跟着 `/` 或 `*` 时才被当成注释开头，所以一条包含引号的正则
字面量会让扫描错位。第 4 条那道「括号之间只许有字符串和逗号」的断言是这条
边界的兜底：一旦扫错位，那道断言先红，而不是安静地给出一个错的答案。
"""

from __future__ import annotations

import re

__all__ = ["blank_comments_and_strings", "exported_string", "exported_string_array"]


def blank_comments_and_strings(src: str) -> tuple[str, list[tuple[int, int]]]:
    """返回 `(code, spans)`。

    `code` 与 `src` **等长**：注释与字符串字面量（连同它们的引号）换成空格，
    换行保留，于是 `code` 上任何一处偏移都能直接拿回 `src` 去切。
    `spans` 是每个字符串字面量**内容**的 `(start, end)`（不含引号），按出现顺序。
    """
    out = list(src)
    spans: list[tuple[int, int]] = []
    i, n = 0, len(src)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            end = src.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
            continue
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == ch:
                    break
                j += 1
            if j >= n:
                raise AssertionError(f"字符串字面量没有收尾引号（偏移 {i}）")
            spans.append((i + 1, j))
            blank(i, j + 1)
            i = j + 1
            continue
        i += 1

    return "".join(out), spans


def exported_string_array(src: str, name: str) -> list[str]:
    """`export const <name> = [ '…', '…' ]` 里那几个字符串，按源码顺序。"""
    code, spans = blank_comments_and_strings(src)
    decl = re.compile(
        # 允许写类型标注（`: readonly string[]`）——只要它不含 `=`，
        # 非贪婪就停在紧跟着的那个赋值号上
        r"\bexport\s+const\s+" + re.escape(name) + r"\b\s*(?::[^=]*?)?=\s*\[",
    )
    hits = list(decl.finditer(code))
    if len(hits) != 1:
        raise AssertionError(
            f"源码里找到 {len(hits)} 处 `export const {name} = [`——"
            "判据只认恰好一处活声明（零处 = 名字改了或没导出；两处 = 漂移的温床）"
        )

    open_at = hits[0].end() - 1
    depth = 0
    close_at = -1
    for k in range(open_at, len(code)):
        if code[k] == "[":
            depth += 1
        elif code[k] == "]":
            depth -= 1
            if depth == 0:
                close_at = k
                break
    if close_at < 0:
        raise AssertionError(f"{name} 的数组没有收尾方括号")

    inner = code[open_at + 1 : close_at]
    if not re.fullmatch(r"[\s,]*", inner):
        raise AssertionError(
            f"{name} 的数组里除了字符串字面量还有别的东西（展开 / 变量 / 计算值）——"
            f"判据读不出它确切的取值，先看一眼：{src[open_at : close_at + 1]!r}"
        )

    values = [src[a:b] for a, b in spans if open_at < a and b < close_at]
    if not values:
        raise AssertionError(f"{name} 解析成空的——判据本身坏了")
    return values


def exported_string(src: str, name: str) -> str:
    """`export const <name> = '…'` 里那个字符串。

    与 `exported_string_array` 同一条纪律：注释里的、没导出的、有两份的都不算。

    取值不能用「`=` 之后的第一个引号」去找——抹干净之后引号本身也是空格了。
    改成：找到赋值号，再要求紧跟着的第一个**字符串字面量**与它之间只隔空白。
    隔着别的东西（函数调用、拼接、三元）就报错，不猜。
    """
    code, spans = blank_comments_and_strings(src)
    decl = re.compile(
        r"\bexport\s+const\s+" + re.escape(name) + r"\b\s*(?::[^=]*?)?=",
    )
    hits = list(decl.finditer(code))
    if len(hits) != 1:
        raise AssertionError(
            f"源码里找到 {len(hits)} 处 `export const {name} =`——判据只认恰好一处活声明"
        )
    at = hits[0].end()
    for content_start, content_end in spans:
        if content_start - 1 < at:
            continue
        if code[at : content_start - 1].strip() == "":
            return src[content_start:content_end]
        break
    raise AssertionError(f"{name} 的取值不是一个字符串字面量——判据读不出确切取值")
