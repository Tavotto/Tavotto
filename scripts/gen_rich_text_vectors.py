#!/usr/bin/env python3
"""生成 / 校对 `tests/golden/rich_text_vectors.json`。

标注文字的两种写法（旧标记 / LaTeX 子集，ADR 0152）有**两个求值器**：`tavotto/richtext.py`
给导出与预检，`web/src/lib/richText.ts` 给画布与编辑框。让它们不分叉的办法与
`gen_glyph_plan_vectors.py` 一样：**同一份向量，两边各跑一遍**。

    python scripts/gen_rich_text_vectors.py            # 校对（有分歧就非零退出）
    python scripts/gen_rich_text_vectors.py --write    # 按 Python 侧重新生成

Python 是参考实现。每条向量给出：解析出的片段、写回 LaTeX 子集的规范形（旧标记那几条就是
「第一次用新编辑框保存时」会变成的字符串）、去掉标记后的可读文本。

纯标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto import richtext  # noqa: E402

OUT = ROOT / "tests" / "golden" / "rich_text_vectors.json"

#: (名字, 写法, 原文)。写法 None = 旧标记。每一条都有理由，别随手加「看起来差不多」的第二条。
CASES: list[tuple[str, str | None, str]] = [
    # —— 旧标记：读法逐字不变，并看它转成 LaTeX 子集后画出来一样
    ("legacy-sup", None, "cm^{-1}"),
    ("legacy-sub", None, "H_{2}O"),
    ("legacy-lone-caret", None, "a^b _c 100%"),  # 孤立 ^ _ 是字；% 转 LaTeX 时要转义
    ("legacy-escapes", None, r"x\^{2} \\ y"),
    ("legacy-dollar-pair", None, "costs $5 and $10"),  # 旧标注里成对的 $ 不是公式，转换时写成 \$
    ("legacy-backslash-end", None, "C:\\data\\"),
    ("legacy-cjk", None, "样品 A ×10⁵"),
    # —— LaTeX 子集：样式命令
    ("italic", "latex", r"Temperature \textit{T} (K)"),
    ("emph", "latex", r"\emph{in vivo} test"),
    ("bold-italic-nested", "latex", r"\textbf{a \textit{b}} c"),
    ("italic-in-bold-canon", "latex", r"\textit{\textbf{x}}"),  # 规范形是粗体在外
    ("script-in-italic", "latex", r"\textit{x\textsubscript{i}}"),
    ("superscript", "latex", r"cm\textsuperscript{-1}"),
    ("upright-in-italic", "latex", r"\emph{a \textup{b} c}"),
    ("textnormal", "latex", r"\textbf{\textit{a \textnormal{b}}}"),
    ("grouping-braces", "latex", r"{\textit{g}}roup"),
    # —— 公式
    ("math-dollar", "latex", r"Rate \textit{k} = $A e^{-E/RT}$"),
    ("math-display-dollar", "latex", r"$$\frac{a}{b}$$ end"),
    ("math-paren-bracket", "latex", r"\(x^2\) and \[y\]"),
    ("math-in-bold", "latex", r"q \textbf{$x$ y}"),
    ("math-escaped-dollar", "latex", r"$a\$b$"),
    # —— 字面量与宽进
    ("escapes", "latex", r"50\% \& \# \_ \{x\} \$5"),
    ("accents", "latex", r"a\^{}b \~{}c"),
    ("backslash", "latex", r"\textbackslash{}x \textbackslash y"),
    ("nbsp", "latex", "Fig.~1"),
    ("newline", "latex", r"a\\b"),
    ("control-space", "latex", r"a\ b"),
    ("unknown-command", "latex", r"\textsc{abc} d"),  # 读不懂的命令连同花括号原样是字
    ("unclosed-command", "latex", r"\textit{x"),
    ("unpaired-dollar", "latex", "cost $5"),
    ("lone-caret-underscore", "latex", "a^b c_d 100% x&y #1"),
    ("raw-newline", "latex", "line1\nline2"),
    ("cjk-italic", "latex", r"温度 \textit{T}"),
    ("empty-command", "latex", r"a\textit{}b"),
]


def _run(r: richtext.TextRun) -> dict:
    """TS 侧的形状：可选字段只有为真时才出现。"""
    d: dict = {"text": r.text, "script": r.script}
    if r.italic:
        d["italic"] = True
    if r.bold:
        d["bold"] = True
    if r.math:
        d["math"] = True
    if r.raw:
        d["raw"] = True
    return d


def build() -> dict:
    vectors = []
    for name, fmt, text in CASES:
        runs = richtext.parse_runs(text, fmt)
        vectors.append(
            {
                "name": name,
                "format": fmt,
                "text": text,
                "runs": [_run(r) for r in runs],
                "latex": richtext.serialize_latex(runs),
                "plain": richtext.plain_text(text, fmt),
            }
        )
    return {
        "schema": 1,
        "comment": (
            "生成物：标注两种写法的跨语言看护向量。"
            "唯一产生者 scripts/gen_rich_text_vectors.py；手改无效。"
            "pytest（tests/test_rich_text_vectors.py）与 vitest"
            "（web/src/lib/richText.golden.test.ts）各跑一遍同一份。"
        ),
        "vectors": vectors,
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="按 Python 侧重新生成")
    args = ap.parse_args()
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT.relative_to(ROOT)}（{len(data['vectors'])} 条）")
        return 0
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
        print(f"{OUT.relative_to(ROOT)} 与 Python 侧不一致：跑 --write 重新生成", file=sys.stderr)
        return 1
    print(f"向量与 Python 侧一致（{len(data['vectors'])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
