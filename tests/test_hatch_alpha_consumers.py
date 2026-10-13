"""#956：会让 matplotlib ≥3.11 重解析花纹色的 setter（`set_alpha` / `set_fill`）的消费方清单。

共享判据修一处不算修完（#950 漏了文本框 / 图例框）：这里把 `overrides.py` / `legendmodel.py` 里**每一处**直接
`set_alpha(` / `set_fill(` 调用登记成清单。新增一处直接调用会让本用例红，提示先判断 artist 是不是
Patch / Collection（可能带花纹）：是 → 走 `overrides._set_alpha_pinned` / `_set_patch_fill`；不是（Text / Line2D /
AxesImage 没有花纹色）→ 在下面登记并写明理由。清单的人读版在 docs/rules/backend/override-application-and-replay.md。
"""

from __future__ import annotations

import ast
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"

#: (文件, 调用所在源码行) → (次数, 为什么不用钉住)
DIRECT_CALLS = {
    (
        "overrides.py",
        "_with_hatch_pinned(a, lambda: a.set_alpha(None if v is None else float(v)))",
    ): (
        1,
        "这就是钉住入口 `_set_alpha_pinned` 本身",
    ),
    ("overrides.py", "lambda a, v: a.set_alpha(None if v is None else float(v)),"): (
        3,
        "text / image / line 三族：Text、AxesImage、Line2D 没有花纹色（`set_hatchcolor` 不存在）",
    ),
    (
        "overrides.py",
        "lambda a: a.get_alpha(), lambda a, v: a.set_alpha(None if v is None else float(v))",
    ): (
        2,
        "errorbar / stem_series：成员是 Line2D 与两点线段的 LineCollection，线段没有可填充面积、花纹画不出来"
        "（3.11.2 / 3.10.9 实测 set_alpha 前后像素逐字节相同），没有可观测的分叉",
    ),
    ("overrides.py", "_with_hatch_pinned(p, lambda: p.set_fill(bool(v)))"): (
        1,
        "这就是钉住入口 `_set_patch_fill` 本身",
    ),
    ("legendmodel.py", "dst.set_alpha(src.get_alpha())"): (1, "图例文字（Text）"),
    ("legendmodel.py", "leg.get_title().set_alpha(title_alpha)"): (1, "图例标题（Text）"),
}


def _calls() -> dict[tuple[str, str], int]:
    found: dict[tuple[str, str], int] = {}
    for name in ("overrides.py", "legendmodel.py"):
        src = (ENGINE / name).read_text(encoding="utf-8")
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("set_alpha", "set_fill")
            ):
                key = (name, lines[node.lineno - 1].strip())
                found[key] = found.get(key, 0) + 1
    return found


def test_every_direct_set_alpha_or_set_fill_call_is_either_pinned_or_a_registered_non_hatch_artist():
    found = _calls()
    expected = {k: n for k, (n, _why) in DIRECT_CALLS.items()}
    assert found == expected, (
        "直接调用 set_alpha / set_fill 的位置与清单不符。Patch / Collection（含图例框、文本背景框、"
        "线集合）必须走 overrides._set_alpha_pinned / _set_patch_fill（#874 / #956）；"
        f"没有花纹色的 artist 在本文件登记。\n实际：{found}\n清单：{expected}"
    )
