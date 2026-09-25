"""`value_original` 这一键：`engine/manifest.VALUE_ORIGINAL_KEY` ↔ `web/src/lib/api.ts` 的
`EditableField.value_original`（严格同源对，`docs/rules/repo/same-origin-pairs.md`）。

键名漂了的话，引擎照常发、前端读到的永远是 `undefined`——而 `undefined` 在前端恰好是
「不知道 → 不让位」那条保守路径，界面上一切如常，只是样式写的 override 再也不给脚本让位
（ADR 0081 §十三）。两侧各自的用例都看不见这种漂移，所以摆在一起比。

两侧都按源码结构读（后端 AST、前端 `tests/support/tsconst`）：本进程不 import matplotlib。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.support.tsconst import exported_interface_members

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "tavotto" / "engine" / "manifest.py"
API = ROOT / "web" / "src" / "lib" / "api.ts"


def _backend_key() -> str:
    tree = ast.parse(MANIFEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "VALUE_ORIGINAL_KEY" for t in node.targets
        ):
            assert isinstance(node.value, ast.Constant), "VALUE_ORIGINAL_KEY 必须是字面量"
            return node.value.value
    raise AssertionError("manifest.py 里没有 VALUE_ORIGINAL_KEY")


def test_value_original_key_is_one_name_on_both_sides():
    members = exported_interface_members(API.read_text(encoding="utf-8"), "EditableField")
    assert "value" in members and "marker_original" in members, members  # 解析本身有效
    assert _backend_key() in members, (_backend_key(), sorted(members))


def test_the_backend_actually_writes_the_constant_not_a_literal():
    """发射点用的是常量本身：常量改了名、发射点还写着旧字面量的话，上面那条仍然是绿的。"""
    src = MANIFEST.read_text(encoding="utf-8")
    assert "f[VALUE_ORIGINAL_KEY] =" in src
    assert src.count('"value_original"') == 1, "value_original 字面量只许出现在常量定义那一处"
