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
from pathlib import Path

from tests.support.tsconst import exported_string_array

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
    r"""TS 侧用 `tests/support/tsconst.py` 结构性地读（评审 #300-5 的同族）。

    从前是 `re.search(r"export const … = \[([^\]]+)\]")` + `re.findall(r"'([^']+)'")`
    ——正则看不见语法结构，注释与无关的字符串字面量都满足它，而 `re.search`
    取的是**第一处**匹配。Python 侧一直是真 AST（`ast.literal_eval`），
    两侧不对称：一边严一边松，松的那边先失守。
    """
    return tuple(exported_string_array(TS.read_text(encoding="utf-8"), name))


def test_entry_style_props_are_the_same_closed_set_in_order():
    assert _ts_list("LEGEND_ENTRY_STYLE_PROPS") == _py_tuple("LEGEND_ENTRY_STYLE_PROPS")


def test_bindings_are_the_same_closed_set_in_order():
    assert _ts_list("LEGEND_BINDINGS") == _py_tuple("LEGEND_BINDINGS")
