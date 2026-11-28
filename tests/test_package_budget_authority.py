"""六项便携包预算只有一处出处（#1921）。

判据的主语是**源码结构**：`PackageBudget(...)` 的构造点、以及六个
`max_*` 关键字只在 `engine/packagebudget.py` 出现。数值本身来自
`projectcopy.MAX_BYTES` / `MAX_FILES` 与 `documentpackage.ARCHIVE_CHUNK_BYTES`，
这里只钉「谁在产生预算」，不复制数值——复制数值的判据会在调参时变绿而失效。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tavotto"
BUDGET_MODULE = SRC / "engine" / "packagebudget.py"

BUDGET_FIELDS = {
    "max_archive_bytes",
    "max_members",
    "max_directory_bytes",
    "max_total_bytes",
    "max_layout_bytes",
    "max_manifest_bytes",
}


def _python_files() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def _budget_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else None
        if name == "PackageBudget":
            calls.append(node)
    return calls


def test_only_the_budget_module_constructs_package_budgets() -> None:
    """其余模块只能接收 `PackageBudget` 或预算工厂，不能自己拼一个。"""
    constructors = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = _budget_calls(tree)
        if calls:
            constructors[path.relative_to(ROOT).as_posix()] = len(calls)
    assert constructors == {"src/tavotto/engine/packagebudget.py": 3}, constructors


def test_budget_fields_are_only_passed_where_the_policy_lives() -> None:
    """六个字段名作为关键字参数只出现在预算模块；别处出现即第二份策略。"""
    seen = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _budget_calls(tree):
            for keyword in call.keywords:
                if keyword.arg in BUDGET_FIELDS:
                    seen.setdefault(path.relative_to(ROOT).as_posix(), set()).add(keyword.arg)
    assert seen == {"src/tavotto/engine/packagebudget.py": BUDGET_FIELDS}, seen


def test_input_budget_reads_the_project_copy_authority() -> None:
    """`input_budget` 的归档 / 成员上限来自 `projectcopy`，不写字面量。"""
    tree = ast.parse(BUDGET_MODULE.read_text(encoding="utf-8"), filename=str(BUDGET_MODULE))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "input_budget"
    )
    names = {
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "projectcopy"
    }
    assert {"MAX_BYTES", "MAX_FILES"} <= names, names
    literals = {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, int)
    }
    assert 2_147_483_648 not in literals, literals
    assert 50_002 not in literals, literals
