"""旧后端（PyMuPDF `pdfbackend.compose`，退役前冻结的产物）vs 新核心（RenderCore）的**校准对拍**（统一实施包 U07，
ADR 0066；03 §6「需要校准」、05 §5、registry RC-094 / RC-096；U10 起旧那一侧是批准资产
`tests/fixtures/legacy_pymupdf/calibration/<case>.pdf`——06 §3：历史差分只在隔离工具 / 批准资产，ADR 0072）。

纪律：**同一读取栈**（PDFium）先比几何（对象包围盒），再比固定读取器的图像（PDFium 同 scale 栅格，逐 RGBA
通道、底噪 3——与 `pdfbackend.compare_png` 同一判据形状）；**按 case 记阈值，不全局放大，不自动位移对齐**
（把两张图挪到最像的位置再比会把布局错误藏起来）。旧后端的行为不是绝对真值（RC-096）：它在 opacity < 1 /
flip 时退位图，新核心保矢量——那种 case 只比几何与「像素差在预期区域」，不要求像素相同。

需要 RenderCore 依赖 + 批准字体（U10 起是运行时闭包）；不在的机器 skip 并写明（skip 不是绿）。
"""

from __future__ import annotations

import importlib.util
import math
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
LEGACY = ROOT / "tests" / "fixtures" / "legacy_pymupdf"
HAS = all(
    importlib.util.find_spec(m) is not None for m in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
needs = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")

PAGE_MM = (120.0, 60.0)
MM = 72.0 / 25.4
SCALE = 2.0

#: 每个 case 自己的阈值（03 §6：按 case 记，不全局放大）。本机（macOS arm64，PDFium 153，PyMuPDF 1.28.2）
#: 2026-09-21 实测值写在旁边——阈值是量出来再定的，不是先定再放宽。旧那一侧自 U10 起是冻结产物
#: （退役前在同一台机器上跑出，见 legacy_pymupdf/oracle.json）。
#: geometry_pt：PDFium 报的对象包围盒（页面空间 pt）逐角容差；pixels / mean：面板框（或整页）内
#: changed_pixel_ratio 与 mean_abs_diff 的上限（逐 RGBA 通道最大差、底噪 3，与 `pdfbackend.compare_png` 同形）。
CASES = {
    # 同一份源页整页搬运：两边都是 Form XObject、矩阵到 0.01 pt 一致 → 几何 ≤ 0.5 pt；
    # 像素只差边缘抗锯齿（实测 0.34% / mean 0.73）
    "panel_plain": {"geometry_pt": 0.5, "pixels": 0.01, "mean": 2.0},
    # 旋转 90° + crop：旧 show_pdf_page 的 clip / rotate 与新 placement 同一合同 → 几何 ≤ 0.6 pt；
    # 矩阵差 0.007 pt 让每条边与旋转文字的抗锯齿都挪一档（实测 2.5% / mean 2.07 / 差全在边上）
    "panel_rot_crop": {"geometry_pt": 0.6, "pixels": 0.04, "mean": 3.0},
    # 形状 + 箭头：同源对 `_polygon_points` / `_dash_pattern` → 几何 ≤ 0.6 pt；像素几乎逐字节（实测 0.003%）
    "shapes": {"geometry_pt": 0.6, "pixels": 0.001, "mean": 0.1},
    # 面板 opacity 0.5：旧后端退位图（Image 覆盖整个目标框）、新核心透明组（Form，PDFium 报的是内容包围盒）
    # ——对象种类刻意不同：判「新内容框落在旧图框之内」+ 面板框内像素差（实测 3.1% / mean 0.71；
    # 位图化的抗锯齿 + 半透明合成的量化，RC-096：旧位图不是真值）
    "panel_opacity": {"geometry_pt": 1.0, "pixels": 0.06, "mean": 2.0},
    # 文字：base-14 Times ↔ Liberation Serif advance 相同（墨的左右边 ≤ 1 pt），基线**按批准的量**不同——
    # 两边同一条基线公式 `y0 + size·((line_h − (asc − desc))/2 + asc)`，PyMuPDF 给 Times 的 ascender 是 bbox
    # 的 1.053，Liberation Serif 的 OS/2 typo ascender 是 0.693（ADR 0060 §4 的 D07 迁移）：12 pt 时基线差
    # 1.77 pt。判据是「差恰好等于那个量（±0.6 pt）」，不是「差得不多」；像素不判（字形本来就不同）
    "text": {"geometry_pt": 1.0, "baseline_tol_pt": 0.6, "pixels": None, "mean": None},
}


@pytest.fixture(scope="module")
def provider():
    if not HAS:
        pytest.skip("候选包未装（not_run）")
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}")
    return HbFaceProvider(reg)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("proj")
    (root / "figs").mkdir()
    shutil.copy(FIXTURE / "page.pdf", root / "figs" / "Fig1.pdf")
    return root


def _objects(case: str) -> list[dict]:
    panel = {"type": "panel", "id": "figs/Fig1.pdf", "x_mm": 5, "y_mm": 5, "w_mm": 67.5, "h_mm": 40}
    if case == "panel_plain":
        return [panel]
    if case == "panel_rot_crop":
        return [
            {
                **panel,
                "w_mm": 40,
                "h_mm": 67.5 * 0.5,
                "rotation": 90,
                "crop": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0},
            }
        ]
    if case == "panel_opacity":
        return [{**panel, "opacity": 0.5}]
    if case == "shapes":
        return [
            {
                "type": "shape",
                "id": "s1",
                "shape": "rect",
                "x_mm": 10,
                "y_mm": 10,
                "w_mm": 30,
                "h_mm": 15,
                "fill": "#ff0000",
                "color": "#000000",
                "stroke_pt": 1.0,
            },
            {
                "type": "arrow",
                "id": "a1",
                "x_mm": 50,
                "y_mm": 10,
                "w_mm": 40,
                "h_mm": 20,
                "color": "#0000ff",
                "stroke_pt": 1.5,
                "start": {"rx": 0.0, "ry": 1.0},
                "end": {"rx": 1.0, "ry": 0.0},
                "head_end": "triangle",
            },
        ]
    if case == "text":
        return [
            {
                "type": "text",
                "id": "t1",
                "text": "Calibration 123",
                "x_mm": 10,
                "y_mm": 30,
                "w_mm": 80,
                "h_mm": 10,
                "size_pt": 12,
            }
        ]
    raise AssertionError(case)


def _old_pdf(case: str, out: Path) -> None:
    """旧后端那一侧：退役前由旧 `pdfbackend.compose`（dpi 600）对同一组对象跑出的冻结产物。"""
    shutil.copyfile(LEGACY / "calibration" / f"{case}.pdf", out)


def _new_pdf(objects: list[dict], project: Path, out: Path, provider) -> None:
    from tavotto.rendercore import pdfwriter, plan, sources

    compiled = plan.compile_page(
        *PAGE_MM, objects, sources=sources.StaticSourceResolver(project), faces=provider
    )
    files = {k: sources.read_frozen(fs) for k, fs in compiled.sources.items()}
    pdfwriter.write_pdf(compiled.page, out, provider, files)


def _pdfium_boxes(path: Path) -> list[tuple[int, tuple[float, float, float, float]]]:
    """(对象类型, 页面空间包围盒)。PDFium 同一读取栈读两边；嵌套对象只取顶层（面板 = 一个 form / image）。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    page = doc[0]
    out = []
    for obj in page.get_objects(max_depth=1):
        out.append((obj.type, tuple(round(v, 3) for v in obj.get_bounds())))
    page.close()
    doc.close()
    return out


def _raster(path: Path):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    img = doc[0].render(scale=SCALE).to_pil().convert("RGBA")
    doc.close()
    return img


def _pixel_diff(a, b, box=None) -> dict:
    """与 `pdfbackend.compare_png` 同形：逐 RGBA 通道取最大差、底噪 3；可只看一个像素框。"""
    assert a.size == b.size
    pa, pb = a.load(), b.load()
    x0, y0, x1, y1 = box or (0, 0, a.size[0], a.size[1])
    total = changed = 0
    signal = 0
    mx = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            d = max(abs(p - q) for p, q in zip(pa[x, y], pb[x, y]))
            total += 1
            if d > 3:
                changed += 1
                signal += d
                mx = max(mx, d)
    return {
        "changed_pixel_ratio": changed / total,
        "mean_abs_diff": signal / total,
        "max_abs_diff": mx,
    }


def _panel_box_px(objects: list[dict]) -> tuple[int, int, int, int]:
    o = objects[0]
    x0, y0 = o["x_mm"] * MM * SCALE, o["y_mm"] * MM * SCALE
    return (int(x0), int(y0), int(x0 + o["w_mm"] * MM * SCALE), int(y0 + o["h_mm"] * MM * SCALE))


@needs
@pytest.mark.parametrize("case", list(CASES))
def test_old_and_new_backends_agree_within_the_case_thresholds(case, project, provider, tmp_path):
    objects = _objects(case)
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    _old_pdf(case, old)
    _new_pdf(objects, project, new, provider)
    thr = CASES[case]

    # ---- 几何：同一读取栈（PDFium）报的顶层对象包围盒 ------------------------------------
    boxes_old, boxes_new = _pdfium_boxes(old), _pdfium_boxes(new)
    if case == "panel_opacity":
        # 旧：Image 铺满目标框；新：Form（透明组），PDFium 报的是组内内容的包围盒——它必须落在旧图框之内
        old_box = next(b for t, b in boxes_old if t == 3)
        new_box = next(b for t, b in boxes_new if t == 5)
        g = thr["geometry_pt"]
        assert new_box[0] >= old_box[0] - g and new_box[1] >= old_box[1] - g, (old_box, new_box)
        assert new_box[2] <= old_box[2] + g and new_box[3] <= old_box[3] + g, (old_box, new_box)
        # 目标框本身：新写入器记的落位 bbox == 旧图框
        rect = _new_placed_rect(objects)
        assert all(abs(a - b) <= g for a, b in zip(rect, old_box)), (rect, old_box)
    elif case == "text":
        _check_text_ink(old, new, thr, objects, provider)
    else:
        # 顶层对象逐个对应（旧后端的白底矩形也是一个 path；两边都先画白底）
        kinds_old = [t for t, _ in boxes_old]
        kinds_new = [t for t, _ in boxes_new]
        assert kinds_old == kinds_new, (kinds_old, kinds_new)
        for (_, bo), (_, bn) in zip(boxes_old, boxes_new):
            assert all(abs(a - b) <= thr["geometry_pt"] for a, b in zip(bo, bn)), (case, bo, bn)

    # ---- 图像：固定读取器（PDFium）同 scale 栅格，逐通道差，不位移对齐 ------------------------
    if thr["pixels"] is None:
        return
    box = _panel_box_px(objects) if case.startswith("panel") else None
    diff = _pixel_diff(_raster(old), _raster(new), box)
    assert diff["changed_pixel_ratio"] <= thr["pixels"], (case, diff)
    assert diff["mean_abs_diff"] <= thr["mean"], (case, diff)


def _new_placed_rect(objects: list[dict]) -> tuple[float, float, float, float]:
    """新核心记的面板落位框（页面 pt，y 向上 → 换成 PDFium 的 y 向上左下原点同一口径）。"""
    o = objects[0]
    x0, y_top = o["x_mm"] * MM, o["y_mm"] * MM
    w, h = o["w_mm"] * MM, o["h_mm"] * MM
    H = PAGE_MM[1] * MM
    return (x0, H - y_top - h, x0 + w, H - y_top)


def _ink_extent(img, box) -> tuple[float, float, float]:
    """文字区域里深色像素的 (最左, 最右, 最下) 边，换回 pt（顶原点）。"""
    p = img.load()
    x0, y0, x1, y1 = box
    xs, ys = [], []
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b, _ = p[x, y]
            if r < 100 and g < 100 and b < 100:
                xs.append(x)
                ys.append(y)
    assert xs, "文字区域里没有墨"
    return (min(xs) / SCALE, (max(xs) + 1) / SCALE, (max(ys) + 1) / SCALE)


def _check_text_ink(old: Path, new: Path, thr: dict, objects: list[dict], provider) -> None:
    """`Calibration 123` 没有下伸部：墨的底边就是基线。左右边差 ≤ geometry_pt（advance 兼容）；基线差
    == 批准的度量差（同一条公式、两边各自的 ascender / descender），±baseline_tol_pt。"""
    import json

    o = objects[0]
    box = (
        int(o["x_mm"] * MM * SCALE) - 4,
        int(o["y_mm"] * MM * SCALE) - 4,
        int((o["x_mm"] + o["w_mm"]) * MM * SCALE) + 4,
        int((o["y_mm"] + o["h_mm"] + 6) * MM * SCALE),
    )
    lo, ro, bo = _ink_extent(_raster(old), box)
    ln, rn, bn = _ink_extent(_raster(new), box)
    assert abs(lo - ln) <= thr["geometry_pt"] and abs(ro - rn) <= thr["geometry_pt"], (
        lo,
        ro,
        ln,
        rn,
    )
    size, line_h = float(o["size_pt"]), 1.25
    # 旧脸（Times-Roman）的 ascender / descender 是退役前从 PyMuPDF 记下的数（oracle.json）
    old_face = json.loads((LEGACY / "oracle.json").read_text(encoding="utf-8"))[
        "serif_regular_face"
    ]
    new_face = provider.face_for("serif", False, False)

    def baseline(
        asc: float, desc: float
    ) -> float:  # 两边同一条公式（旧 _draw_text / 新 typography），这里手写一份
        return size * ((line_h - (asc - desc)) / 2 + asc)

    approved_shift = baseline(old_face["ascender"], old_face["descender"]) - baseline(
        new_face.ascender, new_face.descender
    )
    assert approved_shift > 1.0, approved_shift  # 这是一个真实存在的、批准过的差（不是零）
    assert abs((bo - bn) - approved_shift) <= thr["baseline_tol_pt"], (bo, bn, approved_shift)


@needs
def test_a_deliberately_shifted_panel_is_caught_not_aligned_away(project, provider, tmp_path):
    """反证：把新核心那份的面板挪 2 mm 再比——几何判据必须红（不自动位移对齐）。"""
    objects = _objects("panel_plain")
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    _old_pdf("panel_plain", old)
    shifted = [{**objects[0], "x_mm": objects[0]["x_mm"] + 2}]
    _new_pdf(shifted, project, new, provider)
    bo = [b for t, b in _pdfium_boxes(old) if t == 5][0]
    bn = [b for t, b in _pdfium_boxes(new) if t == 5][0]
    assert abs(bo[0] - bn[0]) > CASES["panel_plain"]["geometry_pt"]
    assert not math.isclose(bo[0], bn[0], abs_tol=CASES["panel_plain"]["geometry_pt"])
    diff = _pixel_diff(_raster(old), _raster(new), _panel_box_px(objects))
    assert diff["changed_pixel_ratio"] > CASES["panel_plain"]["pixels"]
