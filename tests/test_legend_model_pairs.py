"""图例条目模型的两侧常量严格同源（ADR 0034）。

`engine/overrides.LEGEND_ENTRY_STYLE_PROPS` ↔ `web/src/lib/legendModel.ts` 的
同名常量：前端靠这张表判「这一项此刻是不是自定义」（改示意线颜色之后徽标
要立刻变，不等渲染回来），引擎靠它判 `effective_binding`。两边少一条的表现
是：一侧说「跟随」、另一侧说「自定义」，而两侧都不报错。`LEGEND_BINDINGS`
同理（顺序也比）。

本进程不 import matplotlib：`overrides.py` 顶层就 import 它，这里用 `ast`
把常量读出来。
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "src" / "tavotto" / "engine" / "overrides.py"
TS = ROOT / "web" / "src" / "lib" / "legendModel.ts"


def _py_tuple(name: str) -> tuple[str, ...]:
    tree = ast.parse(PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, tuple), f"{name} 必须是 tuple（闭集）"
            return value
    raise AssertionError(f"overrides.py 里找不到 {name}")


def _ts_list(name: str) -> tuple[str, ...]:
    src = TS.read_text(encoding="utf-8")
    m = re.search(rf"export const {name} = \[([^\]]+)\] as const", src)
    assert m, f"legendModel.ts 里找不到 {name}"
    return tuple(re.findall(r"'([^']+)'", m.group(1)))


def test_entry_style_props_are_the_same_closed_set_in_order():
    assert _ts_list("LEGEND_ENTRY_STYLE_PROPS") == _py_tuple("LEGEND_ENTRY_STYLE_PROPS")


def test_bindings_are_the_same_closed_set_in_order():
    assert _ts_list("LEGEND_BINDINGS") == _py_tuple("LEGEND_BINDINGS")
