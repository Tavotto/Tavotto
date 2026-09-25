"""页面 pt 换算的精度前提（ADR 0082）：manifest 把 `PAGE_PT_PROPS` 里的每一条按两位小数回报。

前端的页面 pt 透镜（`web/src/lib/stylePresets.ts` 的 `pagePtLens`）写进 override 的脚本值取两位
小数，读回显示时乘缩放比再取两位——前提是引擎回报的也是两位。少一位（`round(…, 1)`）的话，
缩放比 0.6 时输入 8.5 存 14.17、manifest 回报 14.2、属性页回显 8.52：用户敲进去的数渲染一次就变了
（#557 评审：`mutation_scale` / `labelpad` / `arrow_head` 原来就是一位）。

判的是 AST：引擎里每个 `{"prop": <表里的名字>, "value": …}` 字典字面量，`value` 必须是
`round(…, 2)`。表从 TS 源码里结构化地读（那一份是唯一出处），读不出确切取值就红——空门禁比
没有门禁更坏。字段表不是字典字面量写出来的（比如由别的模块的 getter 表拼出来）的那几条不在
这把尺子的视野里；用例同时要求表里的每一条都至少被看见一次，漏了就得先把尺子接上。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.support.tsconst import exported_string_array

ROOT = Path(__file__).resolve().parents[1]
TS = ROOT / "web" / "src" / "lib" / "stylePresets.ts"
ENGINE = ROOT / "src" / "tavotto" / "engine"


def page_pt_props() -> set[str]:
    """表从 TS 源码里读：结构化解析（`tests/support/tsconst.exported_string_array`），不用正则。

    正则只捡得到它认得的那种行，展开（`...EXTRA`）、标识符进来的条目会被静默漏掉，而
    条数下限照样过（#557 评审 P1）。解析器对读不出确切取值的写法直接报错。
    """
    names = exported_string_array(TS.read_text(encoding="utf-8"), "PAGE_PT_PROPS")
    assert len(names) == len(set(names)), "PAGE_PT_PROPS 里有重复条目"
    return set(names)


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


# ---- 值在上游取好的字段：顺着**这一条字段的 value 表达式**追到它的来源 ----
#
# 有的字段 `value` 不是就地取整，而是读同一个函数里先算好的局部字典（`"value": bb["lw"]`，
# `bb` 在两条分支里各赋一次：量到的 `round(…, 2)`、没有框时的 `BBOX_DEFAULTS[...]`）。
# 这里的判据**从字段表达式出发**：`bb["lw"]` → 同一函数里 `bb` 的每一处赋值 → 每处的
# `"lw"` 那一项，逐项再过同一条判据；常量表（`BBOX_DEFAULTS["bbox_linewidth"]`）追到它的
# 模块级定义，取值必须是两位以内的有限小数。追不到（`bb` 不是字典字面量、键不在、被
# `bb[...] = …` / `.update` 改过、常量表查不到）一律红——豁免只给追得到、且追到的每一处都
# 合格的那条表达式，不再是「模块里某处有个叫 lw 的键」就放行（#557 评审 P1）。
#
# 前提写在这里：局部量按**名字在整个函数体里**找（含嵌套函数，偏严不偏松）；常量表只认
# 本模块定义或 `from <引擎模块> import <名字>` 进来的；常量表在别的模块里被改写的情况
# 不在视野里（引擎里没有这种写法，真出现了本判据看不见）。

_MAX_DEPTH = 4


def _is_two_decimal_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and round(float(value), 2) == float(value)
    )


def _module_const_dict(module: Path, tree: ast.Module, name: str) -> ast.Dict | None:
    """`name` 在模块级的字典字面量定义：本模块定义的，或从引擎另一个模块 import 进来的。"""
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == name
        ):
            return stmt.value if isinstance(stmt.value, ast.Dict) else None
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom) or stmt.module is None:
            continue
        if not any(a.name == name and a.asname in (None, name) for a in stmt.names):
            continue
        src = ENGINE / f"{stmt.module.rsplit('.', 1)[-1]}.py"
        if src.exists() and src != module:
            return _module_const_dict(src, ast.parse(src.read_text(encoding="utf-8")), name)
    return None


def _const_key(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _dict_item(d: ast.Dict, key: str) -> list[ast.expr]:
    return [v for k, v in zip(d.keys, d.values) if k is not None and _const_key(k) == key]


def _local_sources(func: ast.AST, var: str, key: str) -> tuple[bool, list[ast.expr] | None]:
    """函数里局部字典 `var` 的 `key` 那一项的全部来源。

    回 `(是不是局部量, 来源)`：来源为 None 表示追不清。局部量追不清时**不许**再去模块级找
    同名的常量表——那等于让别处的同名定义替这一条担保。
    """
    bound, sources = _is_bound_locally(func, var), []
    for node in ast.walk(func):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Name) and tgt.id == var:
                    # 整体赋值必须是字典字面量，且确实写了这个键
                    if not isinstance(node.value, ast.Dict) or isinstance(node, ast.AugAssign):
                        return True, None
                    items = _dict_item(node.value, key)
                    if len(items) != 1:
                        return True, None
                    sources.extend(items)
                elif (
                    isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == var
                ):
                    if _const_key(tgt.slice) != key or node.value is None:
                        return True, None  # 改的是别的键或键不是字面量：看不清，按红算
                    sources.append(node.value)
                elif isinstance(tgt, (ast.Tuple, ast.List)) and any(
                    isinstance(e, ast.Name) and e.id == var for e in ast.walk(tgt)
                ):
                    return True, None
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == var
            and node.func.attr in {"update", "setdefault", "__setitem__"}
        ):
            return True, None
    if not bound:
        return False, None
    # 形参、for / with / 推导式绑定的、只有整体赋值却一处都没写这个键的：追不清
    return True, (sources if sources and _only_assigned(func, var) else None)


def _bindings(func: ast.AST, var: str) -> list[ast.AST]:
    """函数里所有把 `var` 绑成局部名的节点（赋值、形参、for、with、推导式、import……）。"""
    out: list[ast.AST] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id == var and isinstance(node.ctx, ast.Store):
            out.append(node)
        elif isinstance(node, ast.arg) and node.arg == var:
            out.append(node)
    return out


def _is_bound_locally(func: ast.AST, var: str) -> bool:
    return not isinstance(func, ast.Module) and bool(_bindings(func, var))


def _only_assigned(func: ast.AST, var: str) -> bool:
    """`var` 的每一处绑定都是普通赋值语句的目标（不是形参 / for / with / 推导式 / 海象）。"""
    parents = {c: p for p in ast.walk(func) for c in ast.iter_child_nodes(p)}
    for node in _bindings(func, var):
        if isinstance(node, ast.arg):
            return False
        if not isinstance(parents.get(node), (ast.Assign, ast.AnnAssign)):
            return False
    return True


def _value_ok(
    expr: ast.expr, func: ast.AST, module: Path, tree: ast.Module, depth: int = 0
) -> bool:
    """这一个表达式按两位小数回报：就地 `round(…, 2)`，或追到的每一处来源都是。"""
    if _is_round_2(expr):
        return True
    if depth >= _MAX_DEPTH:
        return False
    if isinstance(expr, ast.IfExp):
        return all(
            _value_ok(b, func, module, tree, depth + 1)
            or (isinstance(b, ast.Constant) and _is_two_decimal_number(b.value))
            for b in (expr.body, expr.orelse)
        )
    if not (isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Name)):
        return False
    key = _const_key(expr.slice)
    if key is None:
        return False
    bound, local = _local_sources(func, expr.value.id, key)
    if bound:
        return local is not None and all(
            _value_ok(src, func, module, tree, depth + 1) for src in local
        )
    table = _module_const_dict(module, tree, expr.value.id)
    if table is None:
        return False
    items = _dict_item(table, key)
    return (
        len(items) == 1
        and isinstance(items[0], ast.Constant)
        and _is_two_decimal_number(items[0].value)
    )


def _enclosing_function(parents: dict[ast.AST, ast.AST], node: ast.AST) -> ast.AST | None:
    cur = parents.get(node)
    while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
        cur = parents.get(cur)
    return cur


def _field_violations(path: Path, wanted: set[str], seen: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    bad: list[str] = []
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
            func = _enclosing_function(parents, node) or tree
            if not _value_ok(entries["value"], func, path, tree):
                bad.append(f"{path.name}:{node.lineno} {name}")
    return bad


def test_every_page_pt_field_is_reported_with_two_decimals():
    wanted = page_pt_props()
    seen: set[str] = set()
    bad: list[str] = []
    for path in sorted(ENGINE.glob("*.py")):
        bad.extend(_field_violations(path, wanted, seen))
    assert bad == []
    assert wanted - seen == set(), "表里有属性没在引擎的字段字面量里找到：尺子没接上"


# ---- 尺子本身的看护：追溯只认这一条字段表达式，别处同名的键不算数 ----


def _violations_in(tmp_path: Path, source: str, prop: str = "bbox_linewidth") -> list[str]:
    path = tmp_path / "fields.py"
    path.write_text(source, encoding="utf-8")
    return _field_violations(path, {prop}, set())


_TRACED_OK = """
TABLE = {"bbox_linewidth": 0.0}
def fields(p):
    if p:
        bb = {"lw": round(float(p.lw), 2)}
    else:
        bb = {"lw": TABLE["bbox_linewidth"]}
    return [{"prop": "bbox_linewidth", "value": bb["lw"]}]
"""


def test_traced_upstream_value_passes(tmp_path):
    assert _violations_in(tmp_path, _TRACED_OK) == []


def test_same_key_elsewhere_does_not_exempt_the_field(tmp_path):
    """#557 P1 的形状：模块里别处有合格的 `"lw"`，字段表达式自己退化了——必须红。"""
    for value in ("float(p.lw)", 'round(bb["lw"], 1)', "round(float(p.lw), 1)"):
        src = _TRACED_OK.replace('"value": bb["lw"]', f'"value": {value}') + (
            '\nOTHER = {"lw": round(1.0, 2)}\n'
        )
        assert _violations_in(tmp_path, src), value


def test_every_traced_source_must_be_two_decimals(tmp_path):
    for old, new in (
        ('bb = {"lw": round(float(p.lw), 2)}', 'bb = {"lw": float(p.lw)}'),
        ('bb = {"lw": round(float(p.lw), 2)}', 'bb = {"lw": round(float(p.lw), 1)}'),
        ('TABLE = {"bbox_linewidth": 0.0}', 'TABLE = {"bbox_linewidth": 0.125}'),
        ('TABLE = {"bbox_linewidth": 0.0}', "TABLE = dict(bbox_linewidth=0.0)"),
        (
            'bb = {"lw": TABLE["bbox_linewidth"]}',
            'bb = {"lw": TABLE["bbox_linewidth"]}\n    bb["lw"] = float(p.lw)',
        ),
        (
            'bb = {"lw": TABLE["bbox_linewidth"]}',
            'bb = {"lw": TABLE["bbox_linewidth"]}\n    bb.update(lw=p.lw)',
        ),
        ('bb = {"lw": TABLE["bbox_linewidth"]}', "bb = dict(p.bbox)"),
        ('bb = {"lw": TABLE["bbox_linewidth"]}', 'bb = {"width": TABLE["bbox_linewidth"]}'),
    ):
        assert old in _TRACED_OK
        assert _violations_in(tmp_path, _TRACED_OK.replace(old, new)), new
