"""画布文字的字体族：闭集、跨语言同源、真的画进 PDF。

Prompt 13 之前画布文字（标注 / 自由文字）**只有一个字体**——`_draw_text` 里
写死了 Times + PyMuPDF 自带的 CJK 脸。「标注文字不能设置字体」是这一轮要修
的那条，这组用例守住修完之后不许再退回去的三件事：

1. 能选的族是一个**闭集**，两侧一个字不差（前端摆出来的选项后端必须画得出，
   否则就是「界面上选得中、导出时悄悄换一个」）；
2. 族真的走到了 PDF 的字体资源上，而不是只在前端预览里换了个样子；
3. 认不出来的族**按默认画**，不抛异常也不去解析一个不存在的字体名。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from tavotto import pdfbackend
from tavotto.pdfbackend import pymupdf_backend as impl

ROOT = Path(__file__).resolve().parents[1]


def test_the_family_set_is_one_closed_set_on_both_sides():
    """`pdfbackend.CANVAS_TEXT_FAMILIES` ↔ `web/src/lib/typography.ts`。

    **顺序也比**：前端下拉按这个顺序出，第一个是「没设过时生效的那个」。
    两侧不一致的后果不是崩溃，是一个选得中却画不出来的选项——用户点了、
    界面显示成功了、导出的图一个像素没变。
    """
    src = (ROOT / "web" / "src" / "lib" / "typography.ts").read_text(encoding="utf-8")
    m = re.search(r"export const CANVAS_TEXT_FAMILIES = \[([^\]]+)\] as const", src)
    assert m, "web/src/lib/typography.ts 里找不到 CANVAS_TEXT_FAMILIES"
    front = tuple(v.strip().strip("'\"") for v in m.group(1).split(",") if v.strip())
    assert front == pdfbackend.CANVAS_TEXT_FAMILIES

    d = re.search(r"export const CANVAS_TEXT_DEFAULT_FAMILY: CanvasTextFamily = '([a-z-]+)'", src)
    assert d, "找不到 CANVAS_TEXT_DEFAULT_FAMILY"
    # 默认族必须是闭集里的第一个——`latin_family()` 认不出来时回的就是它
    assert d.group(1) == pdfbackend.CANVAS_TEXT_FAMILIES[0]


@pytest.mark.parametrize(
    ("family", "bold", "italic", "want"),
    [
        ("serif", False, False, "Times-Roman"),
        ("serif", True, True, "Times-BoldItalic"),
        ("sans-serif", False, False, "Helvetica"),
        ("sans-serif", True, False, "Helvetica-Bold"),
        ("monospace", False, True, "Courier-Oblique"),
    ],
)
def test_each_family_maps_to_its_own_base14_face(family, bold, italic, want):
    assert impl.latin_font(bold, italic, family).name == want


@pytest.mark.parametrize("bad", [None, "", "Times New Roman", "Arial", "宋体", 7])
def test_an_unknown_family_falls_back_to_the_default_instead_of_resolving_it(bad):
    """认不出来的名字**不当成用户指定的字体去解析**。

    那条路的终点是 PyMuPDF 抛异常（导出整个失败）或者悄悄给一张别的脸
    （导出的图与画布不一样）。两个结果都比「按默认画」更坏，而界面从一开始
    就不会让用户选到这里——闭集是上一条用例守的。
    """
    assert impl.latin_family(bad) == "serif"
    assert impl.latin_font(False, False, bad).name == "Times-Roman"


def _text(y_mm: float, **kw) -> dict:
    return {
        "type": "text",
        "x_mm": 5.0,
        "y_mm": y_mm,
        "w_mm": 60.0,
        "h_mm": 10.0,
        "text": "Export",
        "size_pt": 10.0,
        "bold": False,
        "italic": False,
        "color": "#000000",
        "align": "left",
        **kw,
    }


def test_the_family_reaches_the_pdf_font_resources(tmp_path):
    """族要走到**产物**里，不能只在前端预览里换个样子。

    判据量的是 PDF 页面的字体资源表——「画布上看着变了」与「导出的文件里
    真的是那个字体」是两个答案，本轮加的能力必须两个都成立。
    """
    out = tmp_path / "families.pdf"
    with pdfbackend.compose(80, 60) as canvas:
        canvas.place(_text(5.0), dpi=300, resolve_panel=lambda o, d: None)
        canvas.place(
            _text(20.0, font_family="sans-serif"), dpi=300, resolve_panel=lambda o, d: None
        )
        canvas.place(_text(35.0, font_family="monospace"), dpi=300, resolve_panel=lambda o, d: None)
        canvas.save_pdf(out)
    with pymupdf.open(out) as doc:
        names = {f[3] for f in doc[0].get_fonts(full=True)}
    # 缺省那一条（没有 font_family）画的仍然是 Times——老文档一个像素不变
    assert {"Times-Roman", "Helvetica", "Courier"} <= names


def test_measuring_uses_the_same_family_as_writing():
    """量宽与落笔必须同族，否则换行位置与画出来的字对不上。

    等宽族明显比衬线宽，无衬线略宽——三个数两两不等就是「量到了族这一维」；
    量不到的话三个数会相等，而那正是「尺子看不见那个维度」的样子。
    """
    serif = pdfbackend.text_width("Export", 10.0)
    sans = pdfbackend.text_width("Export", 10.0, family="sans-serif")
    mono = pdfbackend.text_width("Export", 10.0, family="monospace")
    assert len({round(serif, 3), round(sans, 3), round(mono, 3)}) == 3
    # 认不出来的族与默认族给同一个数（同一条回退，不是另一把尺子）
    assert pdfbackend.text_width("Export", 10.0, family="nonsense") == serif


def test_the_cjk_face_does_not_change_with_the_family():
    """中日韩那一半**不跟着族走**——PyMuPDF 这一版的四个 china-* 别名回的是
    同一张脸。这条用例是那句注释的看护：注释是断言，没量过的断言迟早变成
    「界面说换了、字形没换」。"""
    faces = {impl.get_font(n).name for n in ("china-ss", "china-s", "china-ssb", "china-sb")}
    assert len(faces) == 1
    assert impl.cjk_font().name in faces
