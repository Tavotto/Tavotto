"""ADR 0144：SVG <text> 字体族的唯一出处——批准族补回退链，用户字体原样保留，幂等。"""

from __future__ import annotations

import sys

import pytest

from tavotto.rendercore import svgfonts


@pytest.fixture(autouse=True)
def _linux(monkeypatch):
    """中文的第一个族按平台写（ADR 0208 §七之三）：这里钉成 linux（没有系统中文字体、第一个仍是批准的 Noto），
    平台分支在 `test_cjk_first_family_follows_the_platform` 里单独测。"""
    monkeypatch.setattr(sys, "platform", "linux")


def test_approved_families_get_the_shared_fallback_chain_and_user_fonts_are_kept():
    src = (
        "<text style=\"font: 10px 'Liberation Sans'; text-anchor: middle\"/>"
        "<text style=\"font: 12px 'DejaVu Sans'\"/>"
        "<text style=\"font-family: 'Liberation Serif'; font-size: 5px\"/>"
        '<text style="font-family: Liberation Mono"/>'
        "<text style=\"font: 9px 'Noto Sans SC'\"/>"
    )
    out = svgfonts.extend_matplotlib_families(src)
    # 第一个族是用户机器上真有的等价字体（Illustrator 只认第一个），批准的 Liberation 紧随其后
    assert "'Arial', 'Liberation Sans', Helvetica, sans-serif" in out
    assert "'Times New Roman', 'Liberation Serif', Times, serif; font-size" in out
    assert "'Courier New', 'Liberation Mono', Courier, monospace" in out
    assert "'Noto Sans SC', 'Microsoft YaHei'" in out
    assert "font: 12px 'DejaVu Sans'\"" in out  # user's font untouched
    assert svgfonts.extend_matplotlib_families(out) == out


def test_writer_and_matplotlib_text_share_one_chain_definition():
    from tavotto.rendercore import svgwriter

    assert svgwriter.family_list is svgfonts.family_list
    assert svgfonts.family_list("Liberation Serif", "serif") == (
        "'Times New Roman', 'Liberation Serif', Times, serif"
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
    assert "style=\"font: 10px 'Arial', 'Liberation Sans', Helvetica, sans-serif\"" in out
    assert "font-family=\"'Times New Roman', 'Liberation Serif', Times, serif\"" in out
    assert svgfonts.extend_matplotlib_families(out) == out


def test_noto_serif_sc_gets_the_serif_cjk_fallback_chain():
    out = svgfonts.extend_matplotlib_families("<text style=\"font: 9px 'Noto Serif SC'\"/>")
    assert "'Noto Serif SC', 'Songti SC', SimSun, 'Noto Serif CJK SC', serif" in out


def test_cjk_first_family_follows_the_platform():
    """ADR 0208 §七之三 第 4 条：中文按平台写系统字体；Linux 没有统一的系统中文字体，第一个仍是批准的 Noto。
    这几个名字在 Illustrator 里能不能对上还没实测。"""
    assert svgfonts.family_list("Noto Sans SC", "cjk", "darwin").startswith(
        "'Heiti SC', 'Noto Sans SC', "
    )
    assert svgfonts.family_list("Noto Serif SC", "cjk-serif", "darwin").startswith(
        "'Songti SC', 'Noto Serif SC', "
    )
    assert svgfonts.family_list("Noto Sans SC", "cjk", "win32").startswith(
        "'Microsoft YaHei', 'Noto Sans SC', "
    )
    assert svgfonts.family_list("Noto Serif SC", "cjk-serif", "win32").startswith(
        "'SimSun', 'Noto Serif SC', "
    )
    assert svgfonts.family_list("Noto Sans SC", "cjk", "linux").startswith("'Noto Sans SC', ")
    # 回退链里已有的同名项不重复
    assert svgfonts.family_list("Noto Sans SC", "cjk", "win32").count("Microsoft YaHei") == 1


def test_the_first_family_is_always_in_the_real_font_table_for_latin_faces():
    for name, generic in (
        ("Liberation Serif", "serif"),
        ("Liberation Sans", "sans-serif"),
        ("Liberation Mono", "monospace"),
    ):
        first = svgfonts.family_list(name, generic).split(",")[0].strip("' ")
        assert first == svgfonts.REAL_FIRST_FAMILY[name]
