"""Render IR 的合同（统一实施包 U06，ADR 0059）：矩阵约定、paint order、能力表、`validate()` 的
每一条拒绝（RC-008 / RC-009 / RC-011 / RC-012）。

纯模型用例：不需要候选包、不需要字体文件。判据的主语是 `tavotto.rendercore.ir` 的数据结构与
校验器；写入器怎么消费它在 `test_rendercore_writer.py`。
"""

from __future__ import annotations

import math

import pytest

from tavotto.rendercore import ir

SHA = "ab" * 32
FONT = ir.FontResource("f", SHA, "serif", False, False, "truetype", "Fake", 1000)
PDF = ir.FileResource("Fig1.pdf", "pdf", SHA, "static", "sha256:" + "0" * 64)
PNG = ir.FileResource("Fig2.png", "png", SHA, "static", "sha256:" + "1" * 64)
BLACK = ir.Paint((0.0, 0.0, 0.0))


def _rect(x=10.0, y=20.0, w=30.0, h=40.0, **kw) -> ir.Path:
    return ir.Path(
        (("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)),
        fill=kw.pop("fill", BLACK),
        **kw,
    )


def _text(font="font", **kw) -> ir.ShapedText:
    return ir.ShapedText(
        5.0,
        6.0,
        (ir.GlyphRun(font, 9.0, (ir.Glyph(3, "A", 600), ir.Glyph(4, "B", 600)), **kw),),
        BLACK,
    )


def _page(*children, resources=None, background=(1.0, 1.0, 1.0)) -> ir.Page:
    return ir.Page(100.0, 50.0, tuple(children), resources or {"font": FONT}, background)


# ---------------------------------------------------------------- 矩阵（RC-008）


def test_matrix_convention_is_row_vector_like_pdf_cm():
    """`(a b c d e f)`：x' = a·x + c·y + e。平移 (5, 7) 把 (1, 2) 送到 (6, 9)；`compose(m1, m2)` 先 m1 后 m2。"""
    assert ir.apply(ir.translate(5, 7), 1, 2) == (6, 9)
    m = ir.compose(ir.scale(2, 3), ir.translate(5, 7))  # 先缩放再平移
    assert ir.apply(m, 1, 2) == (7, 13)
    m2 = ir.compose(ir.translate(5, 7), ir.scale(2, 3))  # 先平移再缩放
    assert ir.apply(m2, 1, 2) == (12, 27)


def test_rotate_ccw_is_counter_clockwise_in_y_up_space():
    x, y = ir.apply(ir.rotate_ccw(90), 1, 0)
    assert (round(x, 9), round(y, 9)) == (0, 1), "y 向上的空间里 +90° 把 x 轴转到 y 轴"
    x, y = ir.apply(ir.rotate_about(90, 10, 10), 20, 10)
    assert (round(x, 9), round(y, 9)) == (10, 20)


def test_determinant_and_flip_matrix_is_invertible():
    assert ir.determinant((1, 0, 0, -1, 0, 100)) == -1
    assert ir.determinant(ir.IDENTITY) == 1


# ---------------------------------------------------------------- paint order（RC-009）


def test_walk_yields_children_in_list_order_not_by_id():
    """两个对象 id 逆序排列：遍历顺序仍是列表顺序（后画的在上面）。"""
    b = _rect(object_id="b")
    a = _rect(object_id="a")
    grp = ir.Group((_rect(object_id="z"), _rect(object_id="y")), object_id="g")
    page = _page(b, grp, a)
    order = [getattr(n, "object_id", "") for n, _ in ir.walk(page)]
    assert order == ["b", "g", "z", "y", "a"]
    paths = [p for _, p in ir.walk(page)]
    assert paths == [
        "children[0]",
        "children[1]",
        "children[1].children[0]",
        "children[1].children[1]",
        "children[2]",
    ]


# ---------------------------------------------------------------- 能力表（RC-011）


def test_capability_table_covers_every_format_and_operation_with_a_reason():
    for fmt in ir.CAP_FORMATS:
        assert set(ir.CAPABILITIES[fmt]) == set(ir.OPERATIONS), fmt
        for op, cap in ir.CAPABILITIES[fmt].items():
            assert cap.level in ir.CAP_LEVELS, (fmt, op)
            assert cap.reason.strip(), (fmt, op)


def test_before_the_writer_lands_every_pdf_operation_is_declared_unsupported():
    """诚实边界（RC-011 的 must_fail_example：未实现的后端对外声明 native）：这棵树里还没有写入器，
    PDF 的每个操作都必须是 unsupported——第二个 PR 带着写入器与交叉核对用例一起把它们翻成 native。
    位图格式与 EPS 也全 unsupported。"""
    for fmt in ir.CAP_FORMATS:
        assert {c.level for c in ir.CAPABILITIES[fmt].values()} == {"unsupported"}, fmt
    assert "U07" in ir.CAPABILITIES["pdf"]["imported_page"].reason
    assert "写入器" in ir.CAPABILITIES["pdf"]["text"].reason


def test_operations_of_and_unsupported_for_report_what_a_page_actually_uses():
    page = _page(
        _rect(fill=ir.Paint((1, 0, 0), 0.5)),
        ir.ImportedPage("pdf", (0, 0, 10, 10), opacity=0.5, flip_h=True),
        ir.Group((_rect(),), clip=_rect(), opacity=0.5),
        _text(),
        resources={"font": FONT, "pdf": PDF},
    )
    assert ir.operations_of(page.children[0]) == ("path_fill", "object_alpha")
    assert ir.operations_of(page.children[1]) == ("imported_page", "group_opacity", "flip")
    assert ir.operations_of(page.children[2]) == ("clip", "group_opacity")
    assert ir.operations_of(page.children[3]) == ("text",)
    gaps = ir.unsupported_for(page, "pdf")
    # 去重后按遍历顺序：每个 (操作, 对象) 一条；本树里 PDF 全部 unsupported，所以每种操作都在
    assert [(g["operation"], g["object_id"]) for g in gaps] == [
        ("page_background", ""),
        ("path_fill", ""),
        ("object_alpha", ""),
        ("imported_page", ""),
        ("group_opacity", ""),
        ("flip", ""),
        ("clip", ""),
        ("text", ""),
    ]
    assert all(g["reason"] for g in gaps)
    assert ir.unsupported_for(ir.Page(10, 10, (), {}, None), "pdf") == []
    assert {g["operation"] for g in ir.unsupported_for(_page(_rect()), "png")} == {
        "page_background",
        "path_fill",
    }


# ---------------------------------------------------------------- validate：正例


def test_a_full_page_validates_and_is_returned_unchanged():
    page = _page(
        _rect(stroke=ir.Stroke(BLACK, 1.0, dash=(3.0, 1.5))),
        ir.Group(
            (_text(), _rect(fill_rule="evenodd")), transform=ir.rotate_about(30, 5, 5), clip=_rect()
        ),
        ir.ImportedPage("pdf", (1, 1, 5, 5), crop=(0.1, 0.1, 0.5, 0.5), rotate_cw_deg=90),
        ir.Image("png", (1, 1, 5, 5), opacity=0.3),
        resources={"font": FONT, "pdf": PDF, "png": PNG},
    )
    assert ir.validate(page) is page
    assert ir.validate(_page(background=None)) is not None


# ---------------------------------------------------------------- validate：负例（RC-012）

NAN, INF = float("nan"), float("inf")

NEGATIVE = [
    ("non_finite", lambda: ir.Page(NAN, 50, (), {}), "page"),
    ("non_finite", lambda: _page(_rect(x=INF)), "children[0]"),
    (
        "non_finite",
        lambda: _page(ir.Group((_rect(),), transform=(1, 0, 0, 1, NAN, 0))),
        "children[0]",
    ),
    ("non_finite", lambda: _page(_text(rise=NAN)), "children[0].runs[0]"),
    ("non_positive_size", lambda: ir.Page(100, 0, (), {}), "page"),
    (
        "non_positive_size",
        lambda: _page(ir.Image("png", (0, 0, -1, 5)), resources={"png": PNG}),
        "children[0]",
    ),
    (
        "non_positive_size",
        lambda: _page(
            ir.ImportedPage("pdf", (0, 0, 5, 5), crop=(0.8, 0, 0.5, 0.5)), resources={"pdf": PDF}
        ),
        "children[0]",
    ),
    (
        "singular_matrix",
        lambda: _page(ir.Group((_rect(),), transform=(1, 2, 2, 4, 0, 0))),
        "children[0]",
    ),
    ("bad_alpha", lambda: _page(_rect(fill=ir.Paint((0, 0, 0), 1.5))), "children[0]"),
    ("bad_alpha", lambda: _page(ir.Group((_rect(),), opacity=-0.1)), "children[0]"),
    ("bad_color", lambda: _page(_rect(fill=ir.Paint((0, 0, 2.0)))), "children[0]"),
    ("bad_color", lambda: _page(_rect(fill=ir.Paint((0, 0)))), "children[0]"),  # type: ignore[arg-type]
    ("bad_path", lambda: _page(ir.Path((("L", 1, 1), ("Z",)), fill=BLACK)), "children[0]"),
    (
        "bad_path",
        lambda: _page(ir.Path(((),), fill=BLACK)),
        "children[0]",
    ),  # 空的第一段：先判形状再读 opcode
    ("bad_path", lambda: _page(ir.Path((("M", 1, 1), ()), fill=BLACK)), "children[0]"),
    ("bad_path", lambda: _page(ir.Path((("M", 1, 1), ("C", 1, 2, 3)), fill=BLACK)), "children[0]"),
    ("bad_path", lambda: _page(ir.Path((("M", 1, 1), ("L", 2, 2)))), "children[0]"),
    (
        "bad_path",
        lambda: _page(_rect(stroke=ir.Stroke(BLACK, 1.0, dash=(0.0, 0.0)))),
        "children[0]",
    ),
    ("bad_fill_rule", lambda: _page(_rect(fill_rule="winding")), "children[0]"),
    ("bad_stroke", lambda: _page(_rect(stroke=ir.Stroke(BLACK, -1.0))), "children[0]"),
    ("bad_stroke", lambda: _page(_rect(stroke=ir.Stroke(BLACK, 1.0, cap="flat"))), "children[0]"),
    ("unknown_resource", lambda: _page(_text(font="nope")), "children[0].runs[0]"),
    ("unknown_resource", lambda: _page(ir.ImportedPage("nope", (0, 0, 5, 5))), "children[0]"),
    (
        "unknown_resource",
        lambda: _page(
            resources={
                "font": ir.FontResource("f", "zz", "serif", False, False, "truetype", "F", 1000)
            }
        ),
        "resources['font']",
    ),
    (
        "resource_kind_mismatch",
        lambda: _page(ir.ImportedPage("png", (0, 0, 5, 5)), resources={"png": PNG}),
        "children[0]",
    ),
    (
        "resource_kind_mismatch",
        lambda: _page(ir.Image("pdf", (0, 0, 5, 5)), resources={"pdf": PDF}),
        "children[0]",
    ),
    (
        "resource_kind_mismatch",
        lambda: _page(_text(font="pdf"), resources={"pdf": PDF}),
        "children[0].runs[0]",
    ),
    (
        "resource_kind_mismatch",
        lambda: _page(
            resources={"font": ir.FontResource("f", SHA, "serif", False, False, "type1", "F", 1000)}
        ),
        "resources['font']",
    ),
    (
        "bad_glyph",
        lambda: _page(
            ir.ShapedText(0, 0, (ir.GlyphRun("font", 0.0, (ir.Glyph(1, "A", 600),)),), BLACK)
        ),
        "children[0].runs[0]",
    ),
    (
        "bad_glyph",
        lambda: _page(
            ir.ShapedText(0, 0, (ir.GlyphRun("font", 9.0, (ir.Glyph(-1, "A", 600),)),), BLACK)
        ),
        "children[0].runs[0]",
    ),
    (
        "bad_glyph",
        lambda: _page(
            ir.ShapedText(0, 0, (ir.GlyphRun("font", 9.0, (ir.Glyph(1, "A", 600.5),)),), BLACK)
        ),
        "children[0].runs[0]",
    ),  # type: ignore[arg-type]
    (
        "bad_glyph",
        lambda: _page(ir.ShapedText(0, 0, (ir.GlyphRun("font", 9.0, ()),), BLACK)),
        "children[0].runs[0]",
    ),
    ("bad_node", lambda: _page("not a node"), "children[0]"),  # type: ignore[arg-type]
    (
        "bad_node",
        lambda: _page(ir.ImportedPage("pdf", (0, 0, 5, 5), page_index=-1), resources={"pdf": PDF}),
        "children[0]",
    ),
]


@pytest.mark.parametrize("code,build,where", NEGATIVE, ids=[f"{c}:{w}" for c, _, w in NEGATIVE])
def test_validate_rejects_each_illegal_input_with_a_stable_code(code, build, where):
    with pytest.raises(ir.IRError) as ei:
        ir.validate(build())
    assert ei.value.code == code
    assert ei.value.path == where, str(ei.value)
    assert code in ir.IR_ERROR_CODES


def test_nesting_deeper_than_the_limit_is_rejected():
    node: ir.Node = _rect()
    for _ in range(ir.MAX_DEPTH + 1):
        node = ir.Group((node,))
    with pytest.raises(ir.IRError) as ei:
        ir.validate(_page(node))
    assert ei.value.code == "bad_node"


def test_every_error_code_has_at_least_one_negative_case():
    """闭集里的每个 code 都要有一条上面的负例，否则那条 code 是从没被证明会发出的。"""
    covered = {c for c, _, _ in NEGATIVE} | {"bad_node"}
    assert covered == set(ir.IR_ERROR_CODES), set(ir.IR_ERROR_CODES) - covered


# ---------------------------------------------------------------- 其它纯函数


def test_hex2rgb_keeps_the_old_facade_contract():
    assert ir.hex2rgb("#ff0000") == (1.0, 0.0, 0.0)
    assert ir.hex2rgb("#0f0") == (0.0, 1.0, 0.0)
    assert ir.hex2rgb("garbage") == (0.0, 0.0, 0.0)
    assert ir.hex2rgb(None) == (0.0, 0.0, 0.0)


def test_mm2pt_is_the_one_conversion():
    assert math.isclose(ir.mm2pt(25.4), 72.0)


def test_imported_page_never_claims_to_know_its_inside():
    """RC-013：源页内部对 IR 是 unknown——没有字段能装「源页里的 axes / 文字」。"""
    node = ir.ImportedPage("pdf", (0, 0, 1, 1))
    assert node.internal == "unknown"
    assert not any(f in ir.ImportedPage.__dataclass_fields__ for f in ("axes", "texts", "children"))
