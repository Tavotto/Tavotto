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
    # 框宽要让两个词**各自**都放得下：只按 "aa" 算的话 "bb" 宽 0.5pt 装不下，
    # 会被超宽单词兜底拆成 "b"/"b"，测的就不是行距了
    box_w = max(width("aa"), width("bb")) + 0.5
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


# ---------------- 行内标记：上标 ^{…} / 下标 _{…} ---------------------------

def _spans(page):
    """页面全部 span：[(size, origin_y, text)]。上下标靠字号与基线区分。"""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = "".join(ch["c"] for ch in span.get("chars", []))
                oy = span["chars"][0]["origin"][1] if span.get("chars") else 0
                out.append((round(span["size"], 3), round(oy, 2), text))
    return out


def test_superscript_is_smaller_and_raised():
    """cm^{-1}：上标字号 = 正文 × SCRIPT_SIZE，基线抬高 = 正文 × SUP_RISE。
    常量与前端 lib/richText.ts 同源，改一边必须同步另一边。"""
    from magplot import richtext

    page = _draw(_base("cm^{-1}"))
    spans = _spans(page)
    body = [s for s in spans if s[2] == "cm"][0]
    sup = [s for s in spans if s[2] == "-1"][0]
    assert sup[0] == pytest.approx(SIZE * richtext.SCRIPT_SIZE, abs=0.01)
    assert body[1] - sup[1] == pytest.approx(SIZE * richtext.SUP_RISE, abs=0.01)


def test_subscript_is_smaller_and_lowered():
    from magplot import richtext

    page = _draw(_base("H_{2}O"))
    spans = _spans(page)
    sub = [s for s in spans if s[2] == "2"][0]
    body = [s for s in spans if s[2] == "H"][0]
    assert sub[0] == pytest.approx(SIZE * richtext.SCRIPT_SIZE, abs=0.01)
    assert sub[1] - body[1] == pytest.approx(SIZE * richtext.SUB_DROP, abs=0.01)
    # 标记之外的字符原样书写，顺序不乱（上下标基线不同，按 x 序重建）
    assert "".join(c for _, c in sorted((x, c) for x, _, c in _chars(page))) == "H2O"


def test_plain_caret_and_underscore_stay_literal():
    """只有 `^{`/`_{` 才是标记。存量文字里孤零零的 ^ 或 _ 必须原样显示——
    升级不该让任何一段已有文字突然变形。"""
    page = _draw(_base("a^b _c 100%"))
    assert "".join(_rows(page)) == "a^b _c 100%"


def test_escaped_markers_render_literally():
    page = _draw(_base(r"x\^{2}"))
    assert "".join(_rows(page)) == "x^{2}"


# ---------------- 超宽单词逐字兜底（前端 word-break:break-word 同源）---------

def _max_x1(page) -> float:
    """页面全部字形右边界的最大值——越界与否只能看 x1，看不了 origin。"""
    return max(ch["bbox"][2]
               for block in page.get_text("rawdict")["blocks"]
               for line in block.get("lines", [])
               for span in line.get("spans", [])
               for ch in span.get("chars", []))


def test_long_unbroken_word_breaks_inside_word_to_stay_in_box():
    """窄框 + 一串无空格长英文（长化学式 / DOI / URL / 驼峰变量名）：整词放不下
    时必须词内断开。修前后端只按空格切换行单元、超宽 unit 不再拆，整词横向
    冲出框右边界（实测 box_right=85.0 max_x1=236.3 overflow=151.3pt）。
    前端 TextView 是 word-break:break-word，浏览器里从来不越界。"""
    t = _base("ABCDEFGHIJKLMNOPQRSTUVWXYZ", x_mm=10, w_mm=20, h_mm=30, size_pt=12)
    page = _draw(t)
    box_right = pb.mm2pt(t["x_mm"] + t["w_mm"])
    assert _max_x1(page) <= box_right + 0.5
    # 断成了多行，且字符一个不丢、顺序不乱
    rows = _rows(page)
    assert len(rows) > 1
    assert "".join(rows) == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_long_word_gets_a_fresh_line_before_being_broken():
    """超宽词先让它独占新行再拆（CSS overflow-wrap 的语义），
    前面那半行不该被卷进逐字断行里。"""
    t = _base("The ABCDEFGHIJKLMNOPQRSTUVWXYZ end",
              x_mm=10, w_mm=20, h_mm=30, size_pt=12)
    page = _draw(t)
    assert _max_x1(page) <= pb.mm2pt(t["x_mm"] + t["w_mm"]) + 0.5
    rows = _rows(page)
    assert rows[0] == "The" and rows[-1] == "end"


CONTROL = ("The quick brown fox jumps over the lazy dog "
           "while plasma etching proceeds at a steady rate")


@pytest.mark.parametrize("w_mm,expected", [
    (60, ["The quick brown fox jumps over the lazy dog",
          "while plasma etching proceeds at a steady rate"]),
    (30, ["The quick brown fox",
          "jumps over the lazy",
          "dog while plasma",
          "etching proceeds at a",
          "steady rate"]),
])
def test_normal_words_wrap_exactly_as_before_the_force_break_fallback(w_mm, expected):
    """对照组：期望值是加"超宽单词逐字兜底"之前跑出来的真实断行结果。
    兜底分支只在"这个 unit 自己就放不下一整行"时才触发，正常带空格的句子
    一个字节都不该变——这条用例就是拦住它误伤的。"""
    assert _rows(_draw(_base(CONTROL, w_mm=w_mm))) == expected


def test_force_break_keeps_script_marks_on_each_char():
    """上下标段被逐字拆开后，script 标记要跟着每个字符走：下标仍是小字号 +
    下沉基线，不能因为断行退化成正文。"""
    from magplot import richtext

    t = _base("Ca_{10}(PO_{4})_{6}(OH)_{2}",
              x_mm=10, w_mm=20, h_mm=30, size_pt=12)
    page = _draw(t)
    assert _max_x1(page) <= pb.mm2pt(t["x_mm"] + t["w_mm"]) + 0.5
    sizes = sorted({s[0] for s in _spans(page)})
    assert sizes == pytest.approx([12 * richtext.SCRIPT_SIZE, 12.0], abs=0.01)
    # 下标字形都落在各自那行正文基线下方 SUB_DROP×size 处
    drop = 12 * richtext.SUB_DROP
    body_ys = sorted({round(y, 1) for x, y, c in _chars(page) if c in "CaPOH()"})
    for size, oy, text in _spans(page):
        if size < 12.0:
            assert any(abs(oy - (by + drop)) < 0.05 for by in body_ys), text
    # 字符一个不丢（上下标基线不同，按 x 序在各自行内重建）
    assert sorted(c for _, _, c in _chars(page)) == sorted("Ca10(PO4)6(OH)2")


def test_script_width_uses_smaller_size_for_wrapping():
    """折行按上下标的真实（更小的）宽度算，不能当正文宽度——
    否则一行明明放得下也会被提前折断。"""
    from magplot import richtext

    body_only = _draw(_base("mmmm", w_mm=200))
    marked = _draw(_base("^{mmmm}", w_mm=200))
    w_body = max(x for x, _, _ in _chars(body_only))
    w_sup = max(x for x, _, _ in _chars(marked))
    x0 = pb.mm2pt(10)
    assert (w_sup - x0) < (w_body - x0) * (richtext.SCRIPT_SIZE + 0.1)
