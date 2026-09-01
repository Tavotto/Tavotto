#!/usr/bin/env python3
"""生成 / 校对 `tests/golden/glyph_plan_vectors.json`。

字形归属计划有**两个求值器**（`tavotto/glyphplan.py` 给导出与 MCP，
`web/src/lib/glyphPlan.ts` 给画布与导出对话框）。让它们不分叉的办法与
preflight / patchspec 完全一样：**同一份向量，两边各跑一遍**。

    python scripts/gen_glyph_plan_vectors.py            # 校对（有分歧就非零退出）
    python scripts/gen_glyph_plan_vectors.py --write    # 按 Python 侧重新生成

Python 是参考实现，而且它问的是**真字体**；TS 侧读生成的覆盖表。所以这份
向量同时在看两件事：算法一致，以及那张表还配得上真字体。

纯标准库（只经 `pdfbackend` 边界层）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto import pdfbackend  # noqa: E402

OUT = ROOT / "tests" / "golden" / "glyph_plan_vectors.json"

#: 样例集。每一条都有理由，别随手加「看起来差不多」的第二条。
CASES: list[tuple[str, str]] = [
    ("empty", ""),
    ("ascii", "Sample A"),
    ("multiply-sign", "×10⁵"),  # 乘号是拉丁段自带的，上标 5 不是
    ("unit-negative-exponent", "A m⁻²"),  # ⁻ 走回退、² 是 primary：一条串里两层
    ("micro-greek", "μm"),  # U+03BC GREEK SMALL LETTER MU
    ("micro-sign", "µm"),  # U+00B5 MICRO SIGN——与上一条是两个码位，别合并
    ("greek-letters", "α β γ Δ"),
    ("degree-celsius", "25 °C"),
    ("angstrom", "Å"),
    ("plus-minus", "±"),
    ("comparisons", "≤ ≥ ≈"),
    ("subscript", "H₂O"),  # ₂ 在 CJK 脸里有，码位却在 CJK 段之外
    ("cjk", "中文标签"),
    ("mixed-cjk-latin", "样品 A ×10⁵"),
    ("box-drawing", "━┃"),  # 只有 CJK 脸有：分层第 4 步救回来的那一族
    ("emoji-surrogate-pair", "😀"),  # TS 侧按码位遍历的看护
    ("math-alphanumeric", "𝛼"),
    ("arabic-question-mark", "؟"),  # 真的谁都画不出
    ("combining-mark", "ـَ"),  # ARABIC TATWEEL + FATHA：组合记号
    ("mixed-missing", "T؟ = 5"),  # 缺字形夹在正常文本里
]

#: 三个族各跑一遍：族只换拉丁脸，不换覆盖判据——**这条是承诺，要被看住**。
FAMILIES = list(pdfbackend.CANVAS_TEXT_FAMILIES)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def build() -> dict:
    vectors = []
    for name, text in CASES:
        for family in FAMILIES:
            vectors.append(
                {
                    "name": f"{name}/{family}",
                    "text": text,
                    "family": family,
                    "runs": [
                        {"text": seg, "layer": layer}
                        for seg, layer in pdfbackend.text_plan(text, family=family)
                    ],
                    "missing": pdfbackend.missing_glyphs(text, family=family),
                }
            )
    return {
        "schema": 1,
        "comment": (
            "生成物：字形归属计划的跨语言看护向量。"
            "唯一产生者 scripts/gen_glyph_plan_vectors.py；手改无效。"
            "pytest（tests/test_glyph_plan.py）与 vitest"
            "（web/src/lib/glyphPlan.golden.test.ts）各跑一遍同一份。"
        ),
        "backend_version": pdfbackend.BACKEND_VERSION,
        "vectors": vectors,
    }


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    fresh = build()
    text = json.dumps(fresh, ensure_ascii=False, indent=1) + "\n"
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT.relative_to(ROOT)}（{len(fresh['vectors'])} 条）")
        return 0

    if not OUT.exists():
        print(f"缺少 {OUT.relative_to(ROOT)}——先跑一次 --write", file=sys.stderr)
        return 1
    stored = json.loads(OUT.read_text(encoding="utf-8"))
    if stored == fresh:
        print(f"向量与 Python 侧一致（{len(fresh['vectors'])} 条）")
        return 0
    old = {v["name"]: v for v in stored.get("vectors", [])}
    new = {v["name"]: v for v in fresh["vectors"]}
    for name in sorted(set(old) | set(new)):
        if old.get(name) != new.get(name):
            print(f"  {name}: {old.get(name)} → {new.get(name)}", file=sys.stderr)
    print("确认过 diff 之后跑 --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
