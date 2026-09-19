"""「没有颜色」这个取值两侧严格同源（#427）。

引擎 `overrides.NO_COLOR` 是 manifest 颜色字段的「无」取值（alpha 为 0 的颜色不报 RGB），
前端 `components/ui/Input.tsx` 的 `NO_COLOR` 让 `ColorField` 把它画成「无」色块而不是
黑色。两边不一致的表现：一侧发 `none`，另一侧当成一个色号交给 `background:` / 取色盘，
检查器又摆出一条并不存在的边——正是这条要消灭的。本进程不 import matplotlib：`overrides.py`
顶层 import 它，这里用 `ast` 读常量。
"""

import ast
from pathlib import Path

from tests.support.tsconst import exported_string

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "src" / "tavotto" / "engine" / "overrides.py"
TS = ROOT / "web" / "src" / "components" / "ui" / "Input.tsx"


def _py_str(name: str) -> str:
    tree = ast.parse(PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, str)
            return value
    raise AssertionError(f"overrides.py 里找不到 {name}")


def test_no_color_is_the_same_string_on_both_sides():
    assert exported_string(TS.read_text(encoding="utf-8"), "NO_COLOR") == _py_str("NO_COLOR")


def test_no_color_is_not_a_hex_colour():
    """它必须是一个色号**解析不了**的串——否则前端把它当颜色画、引擎把它当颜色设，两边都不报错。"""
    v = _py_str("NO_COLOR")
    assert not v.startswith("#") and v == v.lower()
