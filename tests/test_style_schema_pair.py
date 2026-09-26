"""样式内容的键集合：`engine/profilestore._STYLE_KEYS` ↔ `web/src/lib/stylePresets.ts` 的
`StyleProfileData`（严格同源对，`docs/rules/repo/same-origin-pairs.md`）。

两侧各自的用例只量得到自己那一侧：后端白名单漏了前端新加的键时，存一次就被收进 `extra`，
前端再也读不到它——`pt_basis` 正是这样一个键（漏掉的话新样式存一次就退回旧口径，
缩放过的图上的字号跟着错）。这里把两侧摆在一起比。

前端那一侧**按 TypeScript 源码结构读**（`tests/support/tsconst`：先抹掉注释与字符串再定位
结构；注释掉的成员声明、嵌套对象类型里的成员都不算）。`extra` 是前端收容未知字段的桶、后端单独认它（`known = (*_STYLE_KEYS,
"extra")`），不在白名单里。
"""

from __future__ import annotations

from pathlib import Path

from tavotto.engine import profilestore as store
from tests.support.tsconst import exported_interface_members

ROOT = Path(__file__).resolve().parents[1]
TS = ROOT / "web" / "src" / "lib" / "stylePresets.ts"


def _ts_style_fields() -> dict[str, str]:
    """结构性地读（`tests/support/tsconst.exported_interface_members`）：注释掉的成员不算，
    嵌套对象类型里的成员不算顶层成员。"""
    return exported_interface_members(TS.read_text(encoding="utf-8"), "StyleProfileData")


def test_style_content_keys_are_one_set_on_both_sides():
    ts = _ts_style_fields()
    assert "element" in ts and "pt_basis" in ts, ts  # 解析本身有效：已知的两个键都认得出
    assert set(ts) - {"extra"} == set(store._STYLE_KEYS)


def test_pt_basis_has_the_same_single_value_on_both_sides():
    """`pt_basis` 只有一个合法取值 `"page"`：前端的类型字面量与后端校验认的是同一个串。"""
    assert _ts_style_fields()["pt_basis"] == "'page'"
    kept = store._validate_style({"element": {}, "pt_basis": "page"})
    dropped = store._validate_style({"element": {}, "pt_basis": "Page"})
    assert kept.get("pt_basis") == "page"
    assert "pt_basis" not in dropped


def test_the_reader_does_not_count_commented_out_members():
    """看护本身的反证：注释掉的声明（缩进也对得上）不算成员，嵌套对象里的成员不算顶层成员。"""
    src = """export interface X {
  a: number
  // b?: 'page'
  /* c: string */
  page?: { w: number; h: number }
}
"""
    assert list(exported_interface_members(src, "X")) == ["a", "page"]
