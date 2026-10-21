"""ADR 0142：SVG <text> 字体族的唯一出处——批准族补回退链，用户字体原样保留，幂等。"""

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
