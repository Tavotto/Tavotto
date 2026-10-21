"""SVG `<text>` 的字体族写法——唯一出处（ADR 0142）。

`svgwriter`（RenderCore 自己排的文字）与「按原图导出」的 matplotlib 文字（`svg.fonttype=none`）共用这一处：
批准脸的真实族名 + 度量兼容 / 平台原生的回退链 + 通用族。用户脚本自己指定的字体（DejaVu Sans、Arial……）
**不动**，按 matplotlib 的输出保留。纯标准库：Flask 父进程直接 import。
"""

from __future__ import annotations

import re

#: 通用族（allowlist 里的 `family`）→ 紧跟真实族名之后的回退链。
FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "serif": ("'Times New Roman'", "Times", "serif"),
    "sans-serif": ("Arial", "Helvetica", "sans-serif"),
    "monospace": ("'Courier New'", "Courier", "monospace"),
    "cjk": ("'Microsoft YaHei'", "'PingFang SC'", "'Noto Sans CJK SC'", "sans-serif"),
}

#: 批准脸的真实族名（`SfntInfo.family_name`）→ 通用族。matplotlib 文字里碰到这些名字才补回退链。
APPROVED_FAMILIES: dict[str, str] = {
    "Liberation Serif": "serif",
    "Liberation Sans": "sans-serif",
    "Liberation Mono": "monospace",
    "Noto Sans SC": "cjk",
}


def css_family(name: str) -> str:
    return "'" + name.replace("\\", "").replace("'", "") + "'"


def family_list(name: str, generic: str) -> str:
    """`'Liberation Serif', 'Times New Roman', Times, serif`。"""
    return ", ".join((css_family(name), *FALLBACK_CHAINS.get(generic, ())))


_NAMES = "|".join(map(re.escape, APPROVED_FAMILIES))
# 名字后面紧跟逗号 = 已经是一条回退链，不再补（幂等）
_FONT_DECL = re.compile(r"(font\s*:[^;\"]*?)'(" + _NAMES + r")'(?!\s*,)")
_FAMILY_DECL = re.compile(
    r"(font-family\s*:\s*)(?:'(" + _NAMES + r")'|(" + _NAMES + r"))(?!\s*,)(?=\s*[;\"'])"
)


def extend_matplotlib_families(svg: str) -> str:
    """matplotlib `svg.fonttype=none` 的 `style="font: 10px 'Liberation Sans'"` → 补上回退链。
    只认批准族名；用户脚本自己指定的字体原样保留。"""

    def fill(m: re.Match) -> str:
        name = m.group(2) or m.group(3)
        return m.group(1) + family_list(name, APPROVED_FAMILIES[name])

    return _FAMILY_DECL.sub(fill, _FONT_DECL.sub(fill, svg))
