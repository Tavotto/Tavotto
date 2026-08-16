"""_draw_text 的排版几何测试——画布↔导出等价性的后端锚点。

前端 TextView 与后端 _draw_text 是同一算法的两份实现（换行单元：CJK 逐字、
拉丁按词；行高 1.25；CSS 行盒基线）。这里从真实 PDF 页面提取字形原点，
把后端行为钉死；前端若要改排版算法，必须同步改这里的期望值。
"""
import pymupdf
import pytest

from magplot import app as m
from magplot.pdfbackend import pymupdf_backend as pb


def _draw(t: dict) -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    pb._draw_text(page, t)
    return page


def _chars(page) -> list[tuple[float, float, str]]:
    """页面全部字形：[(origin_x, origin_y, char)]，按 (y, x) 排序。"""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    out.append((ch["origin"][0], ch["origin"][1], ch["c"]))
    return sorted(out, key=lambda c: (round(c[1], 1), c[0]))


def _rows(page) -> list[str]:
    """按基线 y 分组重建的行文本（x 序拼接）。"""
    rows: dict[float, list] = {}
    for x, y, c in _chars(page):
        rows.setdefault(round(y, 1), []).append((x, c))
    return ["".join(c for _, c in sorted(v)) for _, v in sorted(rows.items())]


def _base(text: str, **kw) -> dict:
    return {"text": text, "x_mm": 10, "y_mm": 10, "w_mm": 60, "h_mm": 20,
            "size_pt": 9, **kw}


SIZE = 9.0
LATIN = pb.latin_font(False, False)
CJK = pb.get_font("china-ss")


def width(s: str) -> float:
    return pb._mixed_width(s, LATIN, CJK, SIZE)


def pt2mm(pt: float) -> float:
    return pt * m.MM_PER_PT


def test_latin_wraps_by_word():
    # 框宽恰好放下 "hello world"：第三个词换行，且不折断单词
    box_w = width("hello world") + 0.5
    page = _draw(_base("hello world foo", w_mm=pt2mm(box_w)))
    assert _rows(page) == ["hello world", "foo"]


def test_cjk_wraps_by_char():
    box_w = width("等离子") + 0.5  # 恰好三个字
    page = _draw(_base("等离子体加工", w_mm=pt2mm(box_w)))
    assert _rows(page) == ["等离子", "体加工"]


def test_explicit_newline_preserved():
    page = _draw(_base("ab\ncd"))
    assert _rows(page) == ["ab", "cd"]


def test_mixed_script_single_line_advances():
    """中英混排各用各的字体，但 x 步进必须连续（CJK 段起点 = 拉丁段宽度）。"""
    page = _draw(_base("abcd等离子"))
    chars = _chars(page)
    assert "".join(c for _, _, c in chars) == "abcd等离子"
    x0 = pb.mm2pt(10)
    cjk_start = next(x for x, _, c in chars if c == "等")
    assert cjk_start == pytest.approx(x0 + LATIN.text_length("abcd", SIZE), abs=0.2)


def test_baseline_matches_css_line_box():
    """基线 = y0 + size*((1.25-(asc-desc))/2 + asc)，与前端 line-height:1.25 对齐。"""
    page = _draw(_base("Mg"))
    _, y, _ = _chars(page)[0]
    asc, desc = LATIN.ascender, LATIN.descender
    expected = pb.mm2pt(10) + SIZE * ((1.25 - (asc - desc)) / 2 + asc)
    assert y == pytest.approx(expected, abs=0.2)


def test_second_line_offset_is_1_25em():
    box_w = width("aa") + 0.5
    page = _draw(_base("aa bb", w_mm=pt2mm(box_w)))
    ys = sorted({round(y, 1) for _, y, _ in _chars(page)})
    assert len(ys) == 2
    assert ys[1] - ys[0] == pytest.approx(SIZE * 1.25, abs=0.2)


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_alignment(align):
    text = "hello"
    box_w = width(text) * 3
    page = _draw(_base(text, w_mm=pt2mm(box_w), align=align))
    x0 = pb.mm2pt(10)
    w = width(text)
    expected = {"left": x0,
                "center": x0 + (box_w - w) / 2,
                "right": x0 + box_w - w}[align]
    assert _chars(page)[0][0] == pytest.approx(expected, abs=0.2)


def test_empty_text_draws_nothing():
    page = _draw(_base("   "))
    assert _chars(page) == []


def _fonts(page) -> set[str]:
    return {span["font"]
            for block in page.get_text("rawdict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])}


def test_italic_and_bold_pick_matching_latin_fonts():
    assert any("Italic" in f for f in _fonts(_draw(_base("abc", italic=True))))
    assert any("BoldItalic" in f
               for f in _fonts(_draw(_base("abc", bold=True, italic=True))))
    assert all("Italic" not in f for f in _fonts(_draw(_base("abc"))))
