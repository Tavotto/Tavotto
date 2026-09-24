"""页面 pt 换算的精度前提（ADR 0082）：manifest 把 `PAGE_PT_PROPS` 里的每一条按两位小数回报。

前端的页面 pt 透镜（`web/src/lib/stylePresets.ts` 的 `pagePtLens`）写进 override 的脚本值取两位
小数，读回显示时乘缩放比再取两位——前提是引擎回报的也是两位。少一位（`round(…, 1)`）的话，
缩放比 0.6 时输入 8.5 存 14.17、manifest 回报 14.2、属性页回显 8.52：用户敲进去的数渲染一次就变了
（#557 评审：`mutation_scale` / `labelpad` / `arrow_head` 原来就是一位）。

判的是 AST：引擎里每个 `{"prop": <表里的名字>, "value": …}` 字典字面量，`value` 必须是
`round(…, 2)`。表从 TS 源码里读（那一份是唯一出处），读不到或一条都没匹配上就红——空门禁比
没有门禁更坏。字段表不是字典字面量写出来的（比如由别的模块的 getter 表拼出来）的那几条不在
这把尺子的视野里；用例同时要求表里的每一条都至少被看见一次，漏了就得先把尺子接上。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TS = ROOT / "web" / "src" / "lib" / "stylePresets.ts"
ENGINE = ROOT / "src" / "tavotto" / "engine"


def page_pt_props() -> set[str]:
    src = TS.read_text(encoding="utf-8")
    m = re.search(r"PAGE_PT_PROPS: ReadonlySet<string> = new Set\(\[(.*?)\]\)", src, re.S)
    assert m, "stylePresets.ts 里找不到 PAGE_PT_PROPS 的定义"
    names = set(re.findall(r"^\s*'([a-z_]+)',", m.group(1), re.M))
    assert len(names) >= 20, names
    return names


def _prop_names(node: ast.expr) -> list[str]:
    """字典里 `prop` 的取值：字面量，或 `f"spine_{side}_linewidth"` 这种按四边展开的 f-string。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        parts = ["{}" if isinstance(v, ast.FormattedValue) else str(v.value) for v in node.values]
        pattern = "".join(parts)
        if pattern == "spine_{}_linewidth":
            return [f"spine_{s}_linewidth" for s in ("top", "right", "bottom", "left")]
    return []


def _is_round_2(node: ast.expr) -> bool:
    """`round(x, 2)`；或 `round(x, 2) if … else <数字常量>`（缺省值本来就是有限小数）。"""
    if isinstance(node, ast.IfExp):
        return all(
            _is_round_2(branch)
            or (isinstance(branch, ast.Constant) and isinstance(branch.value, (int, float)))
            for branch in (node.body, node.orelse)
        )
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "round"
        and len(node.args) == 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == 2
    )


# 值不是在字段字面量里取整、而是在上游一处取好的：(属性, 上游字典的键)。上游那一处也按同一条
# 判据查（`_upstream_ok`），所以豁免不是放行，是把尺子挪到真正取整的地方
UPSTREAM = {"bbox_linewidth": "lw"}


def _upstream_ok(tree: ast.AST, key: str) -> bool:
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == key:
                found = True
                # 缺省值来自常量表（`BBOX_DEFAULTS[...]`）的也算：那是写死的有限小数
                if not (_is_round_2(v) or isinstance(v, ast.Subscript)):
                    return False
    return found


def test_every_page_pt_field_is_reported_with_two_decimals():
    wanted = page_pt_props()
    seen: set[str] = set()
    bad: list[str] = []
    for path in sorted(ENGINE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            entries = {
                k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)
            }
            if "prop" not in entries or "value" not in entries:
                continue
            for name in _prop_names(entries["prop"]):
                if name not in wanted:
                    continue
                seen.add(name)
                if name in UPSTREAM and _upstream_ok(tree, UPSTREAM[name]):
                    continue
                if not _is_round_2(entries["value"]):
                    bad.append(f"{path.name}:{node.lineno} {name}")
    assert bad == []
    assert wanted - seen == set(), "表里有属性没在引擎的字段字面量里找到：尺子没接上"
