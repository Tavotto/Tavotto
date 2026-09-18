"""从 `overrides.py` 按 artist family 切出来的模块，各自守着切割配方的三条判据。

2026-09-17 审计任务书 PR D 第二步（`docs/architecture/figstate-dependencies.md`）：每切出一族，
那个模块**只依赖标准库 + matplotlib + axestraversal**，不 import `overrides` 也不 import
`manifest`（否则 manifest ↔ overrides 那个环只是换了个名字回来）；`overrides` 里不再有同名
定义（遍历权威那条「只迁移一份、不复制」的纪律推广到每一族）；`overrides.HANDLERS` 里那一族
的条目全部来自模块导出的表（登记在一处，别处不许再手写一条同 key 的 handler）。

纯 `ast`，不 import 产品模块，任何环境都跑得起来。新切一族：把它加进 `FAMILIES`。
"""

from __future__ import annotations

import ast
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(REPO, "src", "tavotto", "engine")

#: 模块名 → 它导出的、会被 `overrides.HANDLERS` 展开进去的表名。
FAMILIES: dict[str, tuple[str, ...]] = {
    "axestraversal": (),
    "spinemodel": ("HANDLERS_STYLE", "HANDLERS_VISIBILITY"),
    "tickmodel": ("HANDLERS_TEXT", "HANDLERS_SIDES", "HANDLERS_MARKS"),
}

#: 族模块允许 import 的顶层名字（标准库之外）。
ALLOWED_THIRD_PARTY = {"matplotlib", "mpl_toolkits", "numpy", "axestraversal"}


def _parse(name: str) -> ast.Module:
    with open(os.path.join(ENGINE, f"{name}.py"), encoding="utf-8") as f:
        return ast.parse(f.read())


def _imports(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


def _is_stdlib(name: str) -> bool:
    import sys

    return name in sys.stdlib_module_names or name == "__future__"


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_family_module_is_a_leaf(name: str):
    """族模块不 import overrides / manifest，也不 import 别的族之外的仓库模块。"""
    bad = sorted(
        m for m in _imports(_parse(name)) if not _is_stdlib(m) and m not in ALLOWED_THIRD_PARTY
    )
    assert bad == [], (
        f"{name}.py 不该 import 这些：{bad}（族模块是 overrides 与 manifest 共同的底层）"
    )


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_overrides_keeps_no_copy_of_the_family(name: str):
    """族模块顶层定义的函数 / 类 / 常量，overrides.py 里不许再有同名定义。"""
    family = {
        n.name for n in _parse(name).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
    } | {
        t.id
        for n in _parse(name).body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    assert family, f"{name}.py 顶层什么都没定义——判据量在空集合上"
    overrides = _parse("overrides")
    dup = sorted(
        n.name
        for n in overrides.body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in family
    ) + sorted(
        t.id
        for n in overrides.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name) and t.id in family
    )
    assert dup == [], f"overrides.py 里还留着 {name} 那一族的同名定义：{dup}（只迁移一份，不复制）"


def _handlers_literal(tree: ast.Module) -> ast.Dict:
    for n in tree.body:
        if (
            isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "HANDLERS"
        ):
            assert isinstance(n.value, ast.Dict)
            return n.value
    raise AssertionError("overrides.py 里找不到 HANDLERS 字面量")


def test_handlers_take_family_tables_by_unpacking_and_never_handwrite_a_family_key():
    """`overrides.HANDLERS` 里，族的条目只能以 `**<module>.<TABLE>` 展开进来。

    手写一条 `("axes", "spine_top_color")` 会把同一 key 登记两遍，后写的静默盖掉前面的——
    界面上什么都看不出来，只有那条 prop 的行为变了。
    """
    handlers = _handlers_literal(_parse("overrides"))
    unpacked = set()
    for key, value in zip(handlers.keys, handlers.values):
        if key is None and isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            unpacked.add(f"{value.value.id}.{value.attr}")
    expected = {f"{mod}.{table}" for mod, tables in FAMILIES.items() for table in tables}
    assert expected <= unpacked, f"HANDLERS 没有展开这些族表：{sorted(expected - unpacked)}"
    # 族表里的 key 不许在 HANDLERS 字面量里再手写一遍
    family_keys: set[tuple[str, str]] = set()
    for mod, tables in FAMILIES.items():
        tree = _parse(mod)
        for n in tree.body:
            if (
                isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name)
                and n.target.id in tables
            ):
                assert isinstance(n.value, ast.Dict)
                for k in n.value.keys:
                    if isinstance(k, ast.Tuple):
                        family_keys.add(
                            tuple(e.value for e in k.elts if isinstance(e, ast.Constant))
                        )
    assert family_keys, "族表里一条 key 都没读到——判据量在空集合上"
    handwritten = sorted(
        tuple(e.value for e in k.elts if isinstance(e, ast.Constant))
        for k in handlers.keys
        if isinstance(k, ast.Tuple)
    )
    dup = sorted(set(handwritten) & family_keys)
    assert dup == [], f"这些 key 在 HANDLERS 里手写了一遍、族表里又登记了一遍：{dup}"
