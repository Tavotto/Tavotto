"""ADR 0144：SVG <text> 字体族的唯一出处——批准族补回退链，用户字体原样保留，幂等。"""

from __future__ import annotations

from tavotto.rendercore import svgfonts


def test_approved_families_get_the_shared_fallback_chain_and_user_fonts_are_kept():
    src = (
        "<text style=\"font: 10px 'Liberation Sans'; text-anchor: middle\"/>"
        "<text style=\"font: 12px 'DejaVu Sans'\"/>"
        "<text style=\"font-family: 'Liberation Serif'; font-size: 5px\"/>"
        '<text style="font-family: Liberation Mono"/>'
        "<text style=\"font: 9px 'Noto Sans SC'\"/>"
    )
    out = svgfonts.extend_matplotlib_families(src)
    assert "'Liberation Sans', Arial, Helvetica, sans-serif" in out
    assert "'Liberation Serif', 'Times New Roman', Times, serif; font-size" in out
    assert "'Liberation Mono', 'Courier New', Courier, monospace" in out
    assert "'Noto Sans SC', 'Microsoft YaHei'" in out
    assert "font: 12px 'DejaVu Sans'\"" in out  # user's font untouched
    assert svgfonts.extend_matplotlib_families(out) == out


def test_writer_and_matplotlib_text_share_one_chain_definition():
    from tavotto.rendercore import svgwriter

    assert svgwriter.family_list is svgfonts.family_list
    assert svgfonts.family_list("Liberation Serif", "serif") == (
        "'Liberation Serif', 'Times New Roman', Times, serif"
    )


def test_label_text_that_mentions_a_font_declaration_is_not_rewritten():
    """P2（Codex #1283）：只改 style / font-family 属性，文本节点（含字面量 font: ... 'Liberation Sans'）原样。"""
    literal = "font: 10px 'Liberation Sans'"
    src = (
        "<text style=\"font: 10px 'Liberation Sans'\">"
        + literal
        + "</text>"
        + '<text style="x">a style="font: 9px \'Liberation Sans\'" b</text>'
        + "<text font-family=\"'Liberation Serif'\">font-family: 'Liberation Serif'</text>"
    )
    out = svgfonts.extend_matplotlib_families(src)
    assert f">{literal}</text>" in out
    assert "a style=\"font: 9px 'Liberation Sans'\" b" in out
    assert ">font-family: 'Liberation Serif'</text>" in out
    assert "style=\"font: 10px 'Liberation Sans', Arial, Helvetica, sans-serif\"" in out
    assert "font-family=\"'Liberation Serif', 'Times New Roman', Times, serif\"" in out
    assert svgfonts.extend_matplotlib_families(out) == out
