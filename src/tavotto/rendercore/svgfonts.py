"""SVG `<text>` 的字体族写法——唯一出处（ADR 0144）。

`svgwriter`（RenderCore 自己排的文字）与「按原图导出」的 matplotlib 文字（`svg.fonttype=none`）共用这一处：
批准脸的真实族名 + 度量兼容 / 平台原生的回退链 + 通用族。用户脚本自己指定的字体（DejaVu Sans、Arial……）
**不动**，按 matplotlib 的输出保留。纯标准库：Flask 父进程直接 import。
"""

from __future__ import annotations

import re
import sys

#: 通用族（allowlist 里的 `family`）→ 紧跟真实族名之后的回退链。
FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "serif": ("'Times New Roman'", "Times", "serif"),
    "sans-serif": ("Arial", "Helvetica", "sans-serif"),
    "monospace": ("'Courier New'", "Courier", "monospace"),
    "cjk": ("'Microsoft YaHei'", "'PingFang SC'", "'Noto Sans CJK SC'", "sans-serif"),
    "cjk-serif": ("'Songti SC'", "SimSun", "'Noto Serif CJK SC'", "serif"),
}

#: 批准脸的真实族名（`SfntInfo.family_name`）→ 通用族。matplotlib 文字里碰到这些名字才补回退链。
APPROVED_FAMILIES: dict[str, str] = {
    "Liberation Serif": "serif",
    "Liberation Sans": "sans-serif",
    "Liberation Mono": "monospace",
    "Noto Sans SC": "cjk",
    "Noto Sans SC Bold": "cjk",
    "Noto Serif SC": "cjk-serif",
    "Noto Serif SC Bold": "cjk-serif",
}


def css_family(name: str) -> str:
    return "'" + name.replace("\\", "").replace("'", "") + "'"


#: 第一个字体族写用户机器上真有的等价字体（ADR 0208 §七之三 第 4 条）：Illustrator 之类只认列表里的第一个，
#: 第一个写 `Liberation Serif` 就被换成一个不相干的本机字体。Liberation 三族与 Times New Roman / Arial /
#: Courier New 字宽一一对应；批准的族名留作第二个，没有 Times New Roman 的机器（Linux）仍用度量一致的脸。
REAL_FIRST_FAMILY: dict[str, str] = {
    "Liberation Serif": "Times New Roman",
    "Liberation Sans": "Arial",
    "Liberation Mono": "Courier New",
}

#: 中文：按平台写系统字体（macOS：黑体 / 宋体；Windows：微软雅黑 / 宋体）。Linux 没有统一的系统中文字体，
#: 第一个仍是批准的 Noto。**这几个名字在 Illustrator 里能不能对上还没实测**（ADR 0208 §七之三 第 4 条）。
SYSTEM_CJK_FIRST: dict[str, dict[str, str]] = {
    "cjk": {"darwin": "Heiti SC", "win32": "Microsoft YaHei"},
    "cjk-serif": {"darwin": "Songti SC", "win32": "SimSun"},
}


def real_first_family(name: str, generic: str, platform: str | None = None) -> str | None:
    """批准族名 → 第一个该写的真实字体族；没有对应（用户字体、Linux 上的中文）= None。"""
    if name in REAL_FIRST_FAMILY:
        return REAL_FIRST_FAMILY[name]
    plat = sys.platform if platform is None else platform
    return SYSTEM_CJK_FIRST.get(generic, {}).get(plat)


def family_list(name: str, generic: str, platform: str | None = None) -> str:
    """`'Times New Roman', 'Liberation Serif', Times, serif`：真实字体在前，批准的族名紧随其后，再是回退链。

    用户脚本自己指定的字体（`name` 不在批准表里，`generic` 是它没有对应的通用族）调用方不会走到这里。"""
    chain = list(FALLBACK_CHAINS.get(generic, ()))
    first = real_first_family(name, generic, platform)
    if first is None:
        return ", ".join((css_family(name), *chain))
    chain = [c for c in chain if c.strip("'") != first]  # 回退链里已有的同名项不重复写
    return ", ".join((css_family(first), css_family(name), *chain))


_NAMES = "|".join(map(re.escape, APPROVED_FAMILIES))
# 名字后面紧跟逗号 = 已经是一条回退链，不再补（幂等）
_FONT_DECL = re.compile(r"(font\s*:[^;\"]*?)'(" + _NAMES + r")'(?!\s*,)")
_FAMILY_DECL = re.compile(
    r"(font-family\s*:\s*)(?:'(" + _NAMES + r")'|(" + _NAMES + r"))(?!\s*,)(?=\s*[;\"'])"
)


# 只在标签内部找属性；文本节点里的 `<` `>` 在 XML 里一定被转义，所以 `<[^<>]*>` 只会命中真标签。
_TAG = re.compile(r"<[^<>]*>")
_STYLE_ATTR = re.compile(r"(\sstyle\s*=\s*)(\"[^\"]*\"|'[^']*')")
_FAMILY_ATTR = re.compile(
    r"(\sfont-family\s*=\s*)\"\s*(?:'(" + _NAMES + r")'|(" + _NAMES + r"))\s*\""
)


def extend_matplotlib_families(svg: str) -> str:
    """matplotlib `svg.fonttype=none` 的 `style="font: 10px 'Liberation Sans'"` → 补上回退链。
    只认批准族名；用户脚本自己指定的字体原样保留。**只改标签里的 `style` / `font-family` 属性值**，
    文本节点（用户标签文字里恰好写着 `font: 10px 'Liberation Sans'`）一个字不动。"""

    def fill(m: re.Match) -> str:
        name = m.group(2) or m.group(3)
        return m.group(1) + family_list(name, APPROVED_FAMILIES[name])

    def style(m: re.Match) -> str:
        value = m.group(2)
        if value[0] == "'":  # 单引号包的 style 里不可能再放带引号的族名；不碰
            return m.group(0)
        return m.group(1) + _FAMILY_DECL.sub(fill, _FONT_DECL.sub(fill, value))

    def family_attr(m: re.Match) -> str:
        name = m.group(2) or m.group(3)
        return m.group(1) + '"' + family_list(name, APPROVED_FAMILIES[name]) + '"'

    def tag(m: re.Match) -> str:
        t = m.group(0)
        if t.startswith(("<!", "<?", "</")):
            return t
        return _FAMILY_ATTR.sub(family_attr, _STYLE_ATTR.sub(style, t))

    return _TAG.sub(tag, svg)
