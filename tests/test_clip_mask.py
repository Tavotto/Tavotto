"""剪切蒙版（ADR 0188）的后端一侧。

1. 共享向量 `tests/golden/clip_mask_vectors.json`：`plan.clip_outline` 与前端 `lib/clipMask.maskOutline` 出同一条轮廓；
2. IR：带 `clip` 的对象外面一层层包 `Group(clip=…)`（外层蒙版在外）；画不出轮廓的蒙版 = 这个对象整个不画；
   没有 `clip` 的对象出的 IR 与加功能之前同一份；
3. 三种写入器都是真裁剪：PDF `W n` / SVG `<clipPath>` / EPS `clip`，不栅格化；
4. PNG 逐像素（PDFium 栅格化 Canonical PDF）：蒙版里面是对象的颜色、外面是纸，嵌套两层取交集。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tavotto.rendercore import ir, plan

ROOT = Path(__file__).resolve().parent
VECTORS = json.loads((ROOT / "golden" / "clip_mask_vectors.json").read_text(encoding="utf-8"))
MM_PER_PT = 25.4 / 72.0
HAS = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL"))
needs_rc = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_outline_matches_the_shared_vectors(case):
    page_h = VECTORS["page_h_mm"] / MM_PER_PT
    segs, rule = plan.clip_outline(case["export"], page_h)
    assert rule == case["fill_rule"]
    assert len(segs) == len(case["outline_mm"])
    for got, want in zip(segs, case["outline_mm"]):
        assert got[0] == want[0]
        for i in range(1, len(got), 2):
            assert got[i] * MM_PER_PT == pytest.approx(want[i], abs=1e-6)
            assert VECTORS["page_h_mm"] - got[i + 1] * MM_PER_PT == pytest.approx(
                want[i + 1], abs=1e-6
            )


def test_vector_set_covers_rotation_evenodd_and_open_paths():
    names = [c["name"] for c in VECTORS["cases"]]
    assert any(c["export"].get("rotation_deg") for c in VECTORS["cases"]), names
    assert any(c["fill_rule"] == "evenodd" for c in VECTORS["cases"]), names
    assert any(not sp["closed"] for c in VECTORS["cases"] for sp in c["export"]["subpaths"]), names


# ---------------------------------------------------------------------------
# 2. IR
# ---------------------------------------------------------------------------
def _rect(**over):
    o = {
        "type": "shape",
        "shape": "rect",
        "id": "r",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 60,
        "h_mm": 60,
        "stroke_pt": 1,
        "color": "#000000",
        "fill": "#ff0000",
        "stroke": False,
    }
    o.update(over)
    return o


def _mask(x, y, w, h, **over):
    m = {
        "type": "path",
        "x_mm": x,
        "y_mm": y,
        "w_mm": w,
        "h_mm": h,
        "subpaths": [
            {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]}
        ],
    }
    m.update(over)
    return m


def _compiled(*objs):
    return plan.compile_page(70.0, 70.0, list(objs), sources=None, faces=None).page  # type: ignore[arg-type]


def test_clip_wraps_outer_mask_outside_inner():
    outer, inner = _mask(0, 0, 40, 40), _mask(20, 20, 40, 40)
    page = _compiled(_rect(clip=[outer, inner]))
    (top,) = page.children
    assert isinstance(top, ir.Group) and top.clip is not None
    (mid,) = top.children
    assert isinstance(mid, ir.Group) and mid.clip is not None
    # 外层在外：最外层的裁剪是 outer（左上角在页面原点）
    xs = [s[1] for s in top.clip.segments if s[0] in ("M", "L")]
    assert min(xs) == pytest.approx(0) and max(xs) == pytest.approx(40 / MM_PER_PT)
    xs_in = [s[1] for s in mid.clip.segments if s[0] in ("M", "L")]
    assert min(xs_in) == pytest.approx(20 / MM_PER_PT)
    assert top.object_id == mid.object_id == "r"


def test_rotated_mask_turns_about_its_own_center():
    m = _mask(10, 10, 20, 10, rotation_deg=90)
    segs, _ = plan.clip_outline(m, 70 / MM_PER_PT)
    pts = [(s[1] * MM_PER_PT, 70 - s[2] * MM_PER_PT) for s in segs if s[0] in ("M", "L")]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    # 20×10 绕中心 (20, 15) 转 90° → 10×20
    assert min(xs) == pytest.approx(15) and max(xs) == pytest.approx(25)
    assert min(ys) == pytest.approx(5) and max(ys) == pytest.approx(25)


def test_mask_without_outline_drops_the_object():
    empty = _mask(0, 0, 10, 10, subpaths=[])
    assert _compiled(_rect(clip=[empty])).children == ()


def test_bad_clip_is_refused():
    with pytest.raises(plan.PlanError):
        _compiled(_rect(clip={"type": "path"}))


def test_objects_without_clip_are_unchanged():
    assert _compiled(_rect()) == _compiled(_rect(clip=[]))
    (node,) = _compiled(_rect()).children
    assert isinstance(node, ir.Group) and node.clip is None


# ---------------------------------------------------------------------------
# 3. 写入器
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def provider():
    if not HAS:
        pytest.skip("RenderCore 依赖未装")
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    return HbFaceProvider(fonts.FontRegistry.discover())


def _clipped_page():
    return _compiled(_rect(clip=[_mask(10, 10, 40, 40)]))


@needs_rc
def test_pdf_clips_with_w_n(provider, tmp_path):
    from tavotto.rendercore import pdfwriter

    sys_path = ROOT / "support"
    import sys

    if str(sys_path) not in sys.path:
        sys.path.insert(0, str(sys_path))
    import pdfread

    out = tmp_path / "c.pdf"
    pdfwriter.write_pdf(_clipped_page(), out, provider)
    _head, content = pdfread.page(pdfread.objects(out.read_bytes()))
    assert b"W n" in content
    assert b"/Image" not in out.read_bytes()  # 没有栅格化


@needs_rc
def test_svg_clips_with_clip_path(provider, tmp_path):
    from tavotto.rendercore import svgwriter

    out = tmp_path / "c.svg"
    svgwriter.write_svg(_clipped_page(), out, provider)
    text = out.read_text(encoding="utf-8")
    assert "<clipPath" in text and "clip-path=" in text
    assert "<image" not in text


@needs_rc
def test_eps_clips(provider):
    from tavotto.rendercore import epswriter

    ps, _facts = epswriter.overlay_ps(_clipped_page(), provider)
    assert "\nclip\n" in ps


# ---------------------------------------------------------------------------
# 4. PNG 逐像素
# ---------------------------------------------------------------------------
def _render(page, provider, tmp_path, scale=4):
    pdfium = pytest.importorskip("pypdfium2")
    from tavotto.rendercore import pdfwriter

    path = tmp_path / "r.pdf"
    pdfwriter.write_pdf(page, path, provider)
    img = pdfium.PdfDocument(str(path))[0].render(scale=scale).to_pil().convert("RGB")
    k = scale * 72 / 25.4
    return lambda x_mm, y_mm: img.getpixel((int(x_mm * k), int(y_mm * k)))


@needs_rc
def test_png_inside_mask_is_painted_outside_is_paper(provider, tmp_path):
    px = _render(_clipped_page(), provider, tmp_path)
    assert px(30, 30) == (255, 0, 0)
    assert px(5, 5) == (255, 255, 255)
    assert px(55, 30) == (255, 255, 255)


@needs_rc
def test_png_nested_masks_intersect(provider, tmp_path):
    page = _compiled(_rect(clip=[_mask(0, 0, 40, 40), _mask(20, 20, 40, 40)]))
    px = _render(page, provider, tmp_path)
    assert px(30, 30) == (255, 0, 0)  # 两个蒙版的交集
    assert px(10, 10) == (255, 255, 255)  # 只在外层里
    assert px(50, 50) == (255, 255, 255)  # 只在内层里
