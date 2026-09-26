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
import math
from pathlib import Path

import pytest

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
    """恰好是 `round(x, 2)` 这一个调用。

    条件表达式不在这里认：它的每条分支（包括数字常量的缺省值）都要过 `_value_ok` 的同一条
    判据——从前这里对常量分支只看「是个数」，`round(v, 2) if v else 0.125` 照样绿（#557 评审 P1）。
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "round"
        and len(node.args) == 2
        and not node.keywords
        and not isinstance(node.args[0], ast.Starred)
        # `round(1e309, 2)` / `round(float("inf"), 2)` 取整完还是无穷：写死的非有限数不因为包了 round 就合格
        and not _is_non_finite_literal(node.args[0])
        and isinstance(node.args[1], ast.Constant)
        and type(node.args[1].value) is int
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
    """两位以内的**有限**小数。

    `round(inf, 2) == inf`：只比「取整前后相等」的话，溢出成无穷的字面量（`1e309`）会被当成两位小数
    放行，manifest 就能报出非有限的页面 pt 值（#557 评审 P2）。先判有限，再判位数。
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and round(float(value), 2) == float(value)
    )


def _is_non_finite_literal(node: ast.expr) -> bool:
    """写死的非有限数：溢出的字面量（`1e309`）、`float("inf")` / `float("nan")`、`math.inf` / `math.nan`。

    只认这几种能在源码上读出来的形状；运行时算出来的无穷（`round(float(p.lw), 2)` 里 `p.lw` 是 inf）
    不在 AST 的视野里——那是引擎取值的事，这把尺子判不了，也不假装判。
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_non_finite_literal(node.operand)
    if isinstance(node, ast.Constant):
        v = node.value
        return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isfinite(v)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        try:
            return not math.isfinite(float(node.args[0].value))
        except ValueError:
            return False
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "math"
        and node.attr in {"inf", "nan"}
    )


def _table_touched(tree: ast.Module, name: str) -> bool:
    """模块里有没有改写这张表的地方：`NAME[...] = …`、`del NAME[...]`、`NAME.update(…)` 这类。"""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and node.func.attr in _MUTATORS
        ):
            return True
    return False


_MUTATORS = frozenset({"update", "setdefault", "__setitem__", "pop", "popitem", "clear", "__ior__"})


def _module_const_dict(module: Path, tree: ast.Module, name: str) -> ast.Dict | None:
    """`name` 在模块级的字典字面量定义：本模块定义的，或从引擎另一个模块 import 进来的。

    定义必须是**唯一一处**模块级绑定、字典字面量里没有 `**` 展开，而且定义它的模块与引用它的
    模块里都没有改写它——否则读到的那一项不是运行时的取值，一律回 None（判红）。
    """
    if _table_touched(tree, name):
        return None
    defs = [
        stmt
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
    ]
    rebinds = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store)
    ]
    if defs:
        stmt = defs[0]
        ok = (
            len(defs) == 1
            and len(rebinds) == 1
            and len(stmt.targets) == 1
            and isinstance(stmt.value, ast.Dict)
            and None not in stmt.value.keys
        )
        return stmt.value if ok else None
    if rebinds:
        return None
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
                    if (
                        not isinstance(node.value, ast.Dict)
                        or isinstance(node, ast.AugAssign)
                        or None in node.value.keys  # `**` 展开可能盖掉这一项
                    ):
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
            and node.func.attr in _MUTATORS
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
    # 数字常量（缺省值）同一条两位小数判据，不管它出现在哪一层分支里
    if isinstance(expr, ast.Constant):
        return _is_two_decimal_number(expr.value)
    # 条件表达式：两条分支都要过；`a or b` / `a and b`：取值可能是任何一个操作数，全部要过
    if isinstance(expr, ast.IfExp):
        branches: list[ast.expr] = [expr.body, expr.orelse]
    elif isinstance(expr, ast.BoolOp):
        branches = list(expr.values)
    else:
        branches = []
    if branches:
        return all(_value_ok(b, func, module, tree, depth + 1) for b in branches)
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


# ---- 字段字面量的去处：只许原样交出去，外层任何能覆盖取值的写法都红（#557 评审六轮 P1）----
#
# 逐个 `ast.Dict` 判 `value` 看不见外层：`{"prop": …, "value": round(v, 2)} | {"value": float(v)}`、
# `dict(d, value=…)`、`{**d, "value": …}`、`d.update(…)`、`d["value"] = …`、`fields[0]["value"] = …`
# 都会在运行时把取好整的值换掉，而字面量本身照样合格。能覆盖的写法数不完，所以反过来 **fail closed**：
# 只认下面这几种「原样交出去」的去处，认不出的父表达式一律红。
#
# - `return {…}`；
# - 列表 / 元组字面量的元素、`*` 展开进列表、条件表达式的分支——再看那个容器的去处（递归）；
# - `for f in ({…}, {…})` 的推导式，且推导式产出的就是 `f` 本身——再看推导式的去处；
# - `X.append({…})` / `X.insert(i, {…})`、`X = [{…}]` / `X += [{…}]`——`X` 是本函数里的局部列表，
#   它的每一处使用也只许是 `X.append / extend / insert(…)`、`return X`、`X += […]`、放进要交出去的列表；
#   下标、迭代、别名、当实参传给别的函数……一律红（外面拿到元素就能改它）。
#
# 字段字面量本身绑到名字上（`d = {…}`）一律红：之后的 `d["value"] = …` / `d.update` / `d | …`
# 看全就得做别名分析，引擎里没有这种写法。

_CONTAINER_METHODS = frozenset({"append", "extend", "insert"})


def _pure_filter(comp: ast.AST | None, gen: ast.comprehension) -> bool:
    """`[f for f in X if …]`：产出的就是迭代变量本身，条件里对它只有 `f[常量]` 的读取（不传出去、不改）。"""
    if not (
        isinstance(comp, ast.ListComp)
        and len(comp.generators) == 1
        and isinstance(gen.target, ast.Name)
        and isinstance(comp.elt, ast.Name)
        and comp.elt.id == gen.target.id
        and not gen.is_async
    ):
        return False
    var = gen.target.id
    for cond in gen.ifs:
        cparents = {c: p for p in ast.walk(cond) for c in ast.iter_child_nodes(p)}
        for n in ast.walk(cond):
            if isinstance(n, ast.NamedExpr):
                return False
            if isinstance(n, ast.Name) and n.id == var:
                p = cparents.get(n)
                if not (
                    isinstance(p, ast.Subscript)
                    and p.value is n
                    and isinstance(p.ctx, ast.Load)
                    and isinstance(p.slice, ast.Constant)
                ):
                    return False
    return True


def _container_ok(func: ast.AST, name: str, parents: dict[ast.AST, ast.AST], depth: int) -> bool:
    """局部列表 `name` 的每一处出现都是「往里加」或「原样交出去」。"""
    if isinstance(func, ast.Module):
        return False  # 模块级的列表谁都能改
    for node in ast.walk(func):
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return False  # 声明成全局 / 外层的，也是谁都能改
    for node in ast.walk(func):
        if not (isinstance(node, ast.Name) and node.id == name):
            continue
        p = parents.get(node)
        if isinstance(node.ctx, ast.Store):
            if isinstance(p, (ast.Assign, ast.AnnAssign)) and isinstance(
                p.value, (ast.List, ast.ListComp)
            ):
                targets = p.targets if isinstance(p, ast.Assign) else [p.target]
                if targets == [node]:
                    continue
            # `X += …`：只往后接，不碰已有的元素（接进来的是什么由它自己的字面量判）
            if isinstance(p, ast.AugAssign) and p.target is node and isinstance(p.op, ast.Add):
                continue
            return False
        # 纯过滤：`[f for f in X if f["prop"] …]`——元素原样留下，条件里只许读 `f[常量]`
        if isinstance(p, ast.comprehension) and p.iter is node:
            comp = parents.get(p)
            if _pure_filter(comp, p):
                holder = parents.get(comp)
                back_to_self = (
                    isinstance(holder, ast.Assign)
                    and holder.value is comp
                    and len(holder.targets) == 1
                    and isinstance(holder.targets[0], ast.Name)
                    and holder.targets[0].id == name
                )
                if back_to_self or _emitted_as_is(comp, parents, func, depth + 1):
                    continue
            return False
        if (
            isinstance(p, ast.Attribute)
            and p.value is node
            and p.attr in _CONTAINER_METHODS
            and isinstance(parents.get(p), ast.Call)
            and parents[p].func is p
        ):
            continue
        if isinstance(p, ast.Starred) or (isinstance(p, (ast.List, ast.Tuple)) and node in p.elts):
            if _emitted_as_is(node, parents, func, depth + 1):
                continue
            return False
        if isinstance(p, ast.Return):
            continue
        return False
    return True


def _emitted_as_is(
    node: ast.AST, parents: dict[ast.AST, ast.AST], func: ast.AST, depth: int = 0
) -> bool:
    """这个表达式（字段字面量或装着它的容器）是不是原样交出去的；认不出就 False。"""
    if depth > 8:
        return False
    p = parents.get(node)
    if isinstance(p, ast.Return):
        return True
    if isinstance(p, (ast.List, ast.Tuple)) and node in p.elts:
        gp = parents.get(p)
        if isinstance(gp, ast.comprehension) and gp.iter is p:
            comp = parents.get(gp)
            return (
                isinstance(comp, (ast.ListComp, ast.GeneratorExp))
                and isinstance(gp.target, ast.Name)
                and isinstance(comp.elt, ast.Name)
                and comp.elt.id == gp.target.id
                and _emitted_as_is(comp, parents, func, depth + 1)
            )
        return _emitted_as_is(p, parents, func, depth + 1)
    if isinstance(p, ast.Starred):
        return _emitted_as_is(p, parents, func, depth + 1)
    if isinstance(p, ast.IfExp) and node in (p.body, p.orelse):
        return _emitted_as_is(p, parents, func, depth + 1)
    if (
        isinstance(p, ast.Call)
        and isinstance(p.func, ast.Attribute)
        and isinstance(p.func.value, ast.Name)
        and not p.keywords
        and (
            (p.func.attr == "append" and p.args == [node])
            or (p.func.attr == "extend" and p.args == [node] and not isinstance(node, ast.Dict))
            or (p.func.attr == "insert" and len(p.args) == 2 and p.args[1] is node)
        )
    ):
        return _container_ok(func, p.func.value.id, parents, depth)
    if isinstance(node, ast.Dict):
        return False  # 字段字面量绑名字、进运算、当别的参数：一律红
    if isinstance(p, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = p.targets if isinstance(p, ast.Assign) else [p.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and p.value is node:
            return _container_ok(func, targets[0].id, parents, depth)
    return False


def _dict_keys(node: ast.AST) -> set[str]:
    return (
        {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if isinstance(node, ast.Dict)
        else set()
    )


def _value_key_writes(path: Path) -> list[str]:
    """下游改写：引擎里任何字面量地写 `"value"` 这个键的地方（不管写的是谁）。

    字段列表交出去之后在别的函数里改（`fields[0]["value"] = …`、`f.update(value=…)`）是跨函数的，
    `_emitted_as_is` 只看得到本函数；这里把「写 value 键」这件事在整个模块里按红算——引擎里本来一处都没有。
    写键的方式看不出键名的（`d.update(other)`、`d[k] = …`）不在视野里，写在盲点里。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in ast.walk(tree):
        what = None
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and _const_key(node.slice) == "value"
        ):
            what = 'x["value"] = …'
        elif isinstance(node, ast.Call):
            f = node.func
            named = any(k.arg == "value" for k in node.keywords)
            literal = any("value" in _dict_keys(a) for a in node.args)
            first = bool(node.args) and _const_key(node.args[0]) == "value"
            if isinstance(f, ast.Attribute) and f.attr in _MUTATORS and (named or literal or first):
                what = f".{f.attr}(value …)"
            elif isinstance(f, ast.Name) and f.id == "dict" and named:
                what = "dict(…, value=…)"
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.BitOr)
            and ("value" in _dict_keys(node.left) or "value" in _dict_keys(node.right))
        ):
            what = '… | {"value": …}'
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.BitOr)
            and "value" in _dict_keys(node.value)
        ):
            what = '|= {"value": …}'
        elif isinstance(node, ast.Dict) and None in node.keys and "value" in _dict_keys(node):
            spread_at = node.keys.index(None)
            if any(_const_key(k) == "value" for k in node.keys[spread_at + 1 :] if k is not None):
                what = '{**d, "value": …}'
        if what:
            bad.append(f"{path.name}:{node.lineno} 改写 value 键：{what}")
    return bad


def _module_violations(path: Path, wanted: set[str], seen: set[str]) -> list[str]:
    """一个引擎模块的全部判据：字段字面量（取值 + 去处）与下游的 value 键改写。"""
    return _field_violations(path, wanted, seen) + _value_key_writes(path)


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
        func = _enclosing_function(parents, node) or tree
        # `**extra` 可能在运行时盖掉 value；外层的 `|` / `dict(…)` / 下标赋值 / `.update` 同理（六轮 P1）
        spread = None in node.keys or not _emitted_as_is(node, parents, func)
        names = _prop_names(entries["prop"])
        if not names and _is_number_field(entries):
            # 属性名不是字面量（`"prop": prop`、认不出的 f-string）：看不出它是不是表里的，
            # 于是数字字段一律按表里的判——否则经由变量进来的页面 pt 量整条不在视野里
            if spread or not _value_ok(entries["value"], func, path, tree):
                bad.append(f"{path.name}:{node.lineno} <{ast.unparse(entries['prop'])}>")
            continue
        for name in names:
            if name not in wanted:
                continue
            seen.add(name)
            if spread or not _value_ok(entries["value"], func, path, tree):
                bad.append(f"{path.name}:{node.lineno} {name}")
    return bad


def _is_number_field(entries: dict[object, ast.expr]) -> bool:
    """manifest 字段（有 `type`）且类型是 `"number"`，或类型不是字面量（看不出就按数字算）。"""
    ty = entries.get("type")
    if ty is None:
        return False  # 没有 `type` 的是编辑 / 补丁记录，不是 manifest 字段
    return not (isinstance(ty, ast.Constant) and ty.value != "number")


def test_every_page_pt_field_is_reported_with_two_decimals():
    wanted = page_pt_props()
    seen: set[str] = set()
    bad: list[str] = []
    for path in sorted(ENGINE.glob("*.py")):
        bad.extend(_module_violations(path, wanted, seen))
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
        # 常量表里溢出成无穷的字面量（#557 评审 P2）
        ('TABLE = {"bbox_linewidth": 0.0}', 'TABLE = {"bbox_linewidth": 1e309}'),
        ('bb = {"lw": round(float(p.lw), 2)}', 'bb = {"lw": 1e309}'),
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


# ---- #557 第二轮：每一条分支、每一个缺省值、每一处可能盖掉取值的写法都过同一条判据 ----


@pytest.mark.parametrize(
    "value",
    [
        "round(float(p.lw), 2) if p else 0.125",  # 缺省值三位小数
        "0.125 if p else round(float(p.lw), 2)",
        "round(float(p.lw), 2) if p else (0.5 if q else 0.333)",  # 嵌套分支里的常量
        "round(float(p.lw), 2) or 0.125",  # BoolOp 的每个操作数
        "p.lw and round(float(p.lw), 2)",
        "round(float(p.lw), 2) if p else True",  # 布尔不是数
        "round(float(p.lw), 2) if p else -0.5",  # 一元负号：读不出就红
        "round(float(p.lw), 2.0)",
        "round(float(p.lw), ndigits=2)",
        # 非有限的字面量（#557 评审 P2）：AST 把 `1e309` 存成 inf，而 `round(inf, 2) == inf`
        "round(float(p.lw), 2) if p else 1e309",
        "round(float(p.lw), 2) or 1e309",
        "1e309",
        "round(1e309, 2)",
        "round(-1e309, 2)",
        "round(float('inf'), 2)",
        "round(float('nan'), 2)",
        "round(math.inf, 2)",
        "round(math.nan, 2)",
    ],
)
def test_every_branch_and_fallback_is_two_decimals(tmp_path, value):
    src = f'def fields(p, q):\n    return [{{"prop": "bbox_linewidth", "value": {value}}}]\n'
    assert _violations_in(tmp_path, src), value


@pytest.mark.parametrize(
    "value",
    [
        "round(float(p.lw), 2) if p else 0.25",
        "round(float(p.lw), 2) if p else 8",
        "round(float(p.lw), 2) if p else (0.5 if q else 1.0)",
        "round(float(p.lw), 2) or 0.0",
        # 有限判据不误伤：写死的有限数包 round 照样合格
        "round(0.125, 2)",
        "round(float('1.5'), 2)",
    ],
)
def test_two_decimal_fallbacks_still_pass(tmp_path, value):
    src = f'def fields(p, q):\n    return [{{"prop": "bbox_linewidth", "value": {value}}}]\n'
    assert _violations_in(tmp_path, src) == [], value


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # 字段字面量里的 `**` 展开可能盖掉 value
        ('"value": bb["lw"]}', '"value": bb["lw"], **p.extra}'),
        # 局部字典里的 `**` 展开可能盖掉这一项
        ('bb = {"lw": round(float(p.lw), 2)}', 'bb = {"lw": round(float(p.lw), 2), **p.extra}'),
        # 常量表：展开、重复定义、被改写、被重新绑定
        ('TABLE = {"bbox_linewidth": 0.0}', 'TABLE = {"bbox_linewidth": 0.0, **EXTRA}'),
        (
            'TABLE = {"bbox_linewidth": 0.0}',
            'TABLE = {"bbox_linewidth": 0.0}\nTABLE = {"bbox_linewidth": 0.0}',
        ),
        (
            'TABLE = {"bbox_linewidth": 0.0}',
            'TABLE = {"bbox_linewidth": 0.0}\nTABLE["bbox_linewidth"] = 0.125',
        ),
        (
            'TABLE = {"bbox_linewidth": 0.0}',
            'TABLE = {"bbox_linewidth": 0.0}\nTABLE.update(bbox_linewidth=0.125)',
        ),
        (
            'TABLE = {"bbox_linewidth": 0.0}',
            'TABLE = {"bbox_linewidth": 0.0}\nTABLE |= {"bbox_linewidth": 0.125}',
        ),
        (
            'TABLE = {"bbox_linewidth": 0.0}',
            'TABLE = {"bbox_linewidth": 0.0}\nfor TABLE in []: pass',
        ),
    ],
)
def test_anything_that_can_overwrite_the_value_is_a_red(tmp_path, old, new):
    assert old in _TRACED_OK
    assert _violations_in(tmp_path, _TRACED_OK.replace(old, new)), new


def test_a_number_field_whose_prop_is_not_a_literal_is_judged_too(tmp_path):
    """`"prop": prop` 看不出是不是表里的——数字字段一律按表里的判，别让变量把它带出视野。"""
    bad = 'def f(prop, v):\n    return {"prop": prop, "type": "number", "value": float(v)}\n'
    assert _violations_in(tmp_path, bad)
    ok = 'def f(prop, v):\n    return {"prop": prop, "type": "number", "value": round(float(v), 2)}\n'
    assert _violations_in(tmp_path, ok) == []
    enum = 'def f(prop, v):\n    return {"prop": prop, "type": "enum", "value": v}\n'
    assert _violations_in(tmp_path, enum) == []
    fstr = 'def f(s, v):\n    return {"prop": f"edge_{s}_width", "type": "number", "value": float(v)}\n'
    assert _violations_in(tmp_path, fstr)


@pytest.mark.parametrize(
    "suffix",
    [").add('new_prop')", ")\n  .add('new_prop')", ") as ReadonlySet<string>", ") || EXTRA"],
)
def test_the_real_declaration_with_a_continuation_is_a_red(suffix):
    """#557 评审 P1：真实声明后面续上 `.add(…)`，读法必须红，而不是静默丢掉那一条。"""
    src = TS.read_text(encoding="utf-8")
    decl = src.index("export const PAGE_PT_PROPS")
    close = src.index("])", decl) + 1
    assert src[close] == ")"
    mutated = src[:close] + suffix + src[close + 1 :]
    with pytest.raises(AssertionError):
        exported_string_array(mutated, "PAGE_PT_PROPS")


# ---- #557 第六轮：字段字面量的去处（外层能覆盖取值的写法）----

_F = '{"prop": "bbox_linewidth", "type": "number", "value": round(float(v), 2)}'


def _wrapped_violations(tmp_path: Path, body: str) -> list[str]:
    """函数体 `body` 里用 `F` 代表一个合格的字段字面量；字段判据与 value 键改写判据一起跑。"""
    lines = body.replace("F", _F).splitlines()
    src = "def fields(v, extra, other):\n" + "".join(f"    {line}\n" for line in lines)
    path = tmp_path / "fields.py"
    path.write_text(src, encoding="utf-8")
    return _module_violations(path, {"bbox_linewidth"}, set())


@pytest.mark.parametrize(
    "body",
    [
        # 评审给的形状：外层 `|` 盖掉 value
        'return [F | {"value": float(v)}]',
        "return [F | extra]",
        "return [extra | F]",
        "return [dict(F, value=float(v))]",
        "return [dict(F)]",
        'return [{**F, "value": float(v)}]',
        "return [{**F}]",
        # 字段字面量绑到名字上
        "d = F\nreturn [d]",
        'd = F\nd["value"] = float(v)\nreturn [d]',
        "d = F\nd.update(value=float(v))\nreturn [d]",
        'd = F\nd |= {"value": float(v)}\nreturn [d]',
        # 容器交出去之前被改 / 被带出去
        'fields = [F]\nfields[0]["value"] = float(v)\nreturn fields',
        "fields = [F]\nfields[0].update(extra)\nreturn fields",
        'fields = [F]\nfor f in fields:\n    f["value"] = float(v)\nreturn fields',
        "fields = [F]\nalias = fields\nreturn alias",
        "fields = [F]\nmutate(fields)\nreturn fields",
        "fields = [F]\nfields = fields * 2\nreturn fields",
        "fields = [F]\nfields |= other\nreturn fields",
        "global G\nG = [F]",
        # 过滤推导式：条件里把元素传出去 / 海象 / 产出的不是元素本身
        "fields = [F]\nreturn [f for f in fields if mutate(f)]",
        "fields = [F]\nreturn [f for f in fields if (g := f)]",
        "fields = [F]\nreturn [dict(f) for f in fields]",
        # 各条判据单独隔开的样本（不写 value 键，免得被下游判据顺手判红）
        "return [fix(f) for f in (F, F)]",
        "return [1 if F else 2]",
        "mutate(*[F])\nreturn []",
        "fields = []\nfields.append(x, F)\nreturn fields",
        "fields = []\nfields.append(F)\nmutate(fields)\nreturn fields",
        "fields = [F]\nfields = other\nreturn fields",
        "fields = [F]\nfields.pop().update(extra)\nreturn fields",
        'fields = [F]\nreturn [f for f in fields if (g := f["prop"])]',
        # 认不出的父表达式
        "return list(map(fix, [F]))",
        "return fix(F)",
        "yield F",
        "return {'x': F}['x']",
        "return (F, 1)[0]",
    ],
)
def test_a_parent_that_can_overwrite_the_value_is_a_red(tmp_path, body):
    assert _wrapped_violations(tmp_path, body), body


@pytest.mark.parametrize(
    "body",
    [
        "return F",
        "return [F]",
        "return [F, F]",
        "return [*([F] if v else [])]",
        "return F if v else F",
        "fields = [F]\nfields.append(F)\nfields.insert(0, F)\nfields.extend([F])\n"
        "fields += [F]\nfields += other\nreturn fields",
        "fields: list = []\nfields.append(F)\nreturn [*fields]",
        'fields = [F]\nfields = [f for f in fields if f["prop"] != "x"]\nreturn fields',
        'fields = [F]\nreturn [f for f in fields if f["prop"] != "x"]',
        "return [*[f for f in (F, F)]]",
    ],
)
def test_emitting_the_literal_as_is_still_passes(tmp_path, body):
    assert _wrapped_violations(tmp_path, body) == [], body


def test_a_module_level_list_is_a_red(tmp_path):
    """模块级的列表谁都能改：字段字面量放进去就红。"""
    path = tmp_path / "fields.py"
    path.write_text(f"v = 1.0\nFIELDS = [{_F}]\n", encoding="utf-8")
    assert _module_violations(path, {"bbox_linewidth"}, set())


@pytest.mark.parametrize(
    "line",
    [
        'x["value"] = 1',
        'del x["value"]',
        'x["value"] += 1',
        "x.update(value=1)",
        'x.update({"value": 1})',
        'x.setdefault("value", 1)',
        'x.pop("value")',
        "y = dict(x, value=1)",
        'y = x | {"value": 1}',
        'y = {"value": 1} | x',
        'x |= {"value": 1}',
        'y = {**x, "value": 1}',
    ],
)
def test_writing_the_value_key_anywhere_in_the_engine_is_a_red(tmp_path, line):
    """交出去以后在别的函数里改也算：模块里任何字面量地写 value 键都红（下游改写）。"""
    path = tmp_path / "other.py"
    path.write_text(f"def g(x):\n    {line}\n", encoding="utf-8")
    # 走真实引擎用的同一个入口：下游判据没接上，这里就红
    assert _module_violations(path, set(), set()), line


def test_reading_the_value_key_is_not_a_write(tmp_path):
    path = tmp_path / "other.py"
    path.write_text(
        'def g(x):\n    y = x["value"]\n    z = {"value": 1, **x}\n    return x.get("value")\n',
        encoding="utf-8",
    )
    assert _value_key_writes(path) == []
