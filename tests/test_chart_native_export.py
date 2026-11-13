"""原生完整编辑状态经生产导出落盘；不以手工嵌包代替导出接线。

复用真实 API 项目夹具及独立 PDFium 读取器；编辑、发布、删源恢复均走产品入口。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from tavotto import chartbundle
from tavotto.engine import chartprofile, patchspec
from test_chart_export_pipeline import _pdf_page
from test_chart_native_api import SOURCE, _bind, _color, _render, project as _native_project

PT_MM = 25.4 / 72
project = _native_project


def _original(chart, edits=(), **changes):
    return {
        "figure_id": "chart.svg",
        "source_kind": "vector",
        "chart": chart,
        "chart_render_profile": chartprofile.NATIVE_PROFILE,
        "overrides": list(edits),
        **changes,
    }


def _panel(chart, edits, x=0):
    return {
        "type": "panel",
        "id": "chart.svg",
        "chart": chart,
        "chart_render_profile": chartprofile.NATIVE_PROFILE,
        "overrides": list(edits),
        "x_mm": x * PT_MM,
        "y_mm": 0,
        "w_mm": 100 * PT_MM,
        "h_mm": 100 * PT_MM,
    }


def _export(
    client,
    *,
    original=None,
    canvas=None,
    pages=None,
    formats=("svg", "pdf"),
    name="native",
    ppi=None,
    background=None,
):
    payload = {
        "scope": "canvas" if canvas is not None else "original",
        "formats": list(formats),
        "filename": name,
        "overwrite": "replace",
        **({"ppi": ppi} if ppi is not None else {}),
        **({"background": background} if background is not None else {}),
        **({"original": original} if original is not None else {}),
        **({"canvas": canvas} if canvas is not None else {}),
        **({"merge_pdf": {"pages": pages}} if pages is not None else {}),
    }
    response = client.post("/api/export", json=payload)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["status"] == "done", body
    outputs = {}
    for item in body["outputs"]:
        assert item["status"] == "done", body
        outputs[item["format"]] = Path(body["export_dir"]) / item["name"]
        assert outputs[item["format"]].is_file(), body
    assert set(outputs) == set(formats)
    return outputs


def _colored(image, x, y, color):
    channel = {"red": 0, "green": 1, "blue": 2}[color]
    samples = [
        image.getpixel((round(x * 2) + dx, round(y * 2) + dy))
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
    ]
    return any(
        rgb[channel] > 160 and all(v < 100 for i, v in enumerate(rgb) if i != channel)
        for rgb in samples
    )


def _assert_svg_color(raw, color):
    root = ET.fromstring(raw)
    assert any(
        node.get("stroke") == color or f"stroke: {color}" in node.get("style", "")
        for node in root.iter()
    ), "生产 SVG 没有请求中的原生系列颜色"
    return root


def test_production_native_svg_v2_keeps_complete_edits_then_restores_without_original(project):
    client, root = project
    chart = _bind(client)
    edits = [
        _color("#00ff00"),
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 5},
        _color("#ff0000"),
        {"gid": "axes_0", "prop": "xlim", "value": [0, 2]},
    ]
    rendered = _render(client, chart, edits)
    outputs = _export(client, original=_original(chart, edits))
    produced = outputs["svg"].read_bytes()
    bundle = chartbundle.extract_chart(produced)
    assert bundle is not None
    assert bundle.render_profile == chartprofile.NATIVE_PROFILE
    assert bundle.overrides == edits and bundle.chart == chart
    assert bundle.source_svg == SOURCE
    _assert_svg_color(produced, "#ff0000")
    _count, size, _text, image = _pdf_page(pdfium, outputs["pdf"])
    assert size == pytest.approx((100, 100), abs=0.02)
    assert _colored(image, 30, 70, "red") and not _colored(image, 70, 70, "red")
    portable = root / "portable.svg"
    portable.write_bytes(produced)
    (root / "chart.svg").unlink()
    inspected = client.get("/api/svg/chart/inspect?id=portable.svg")
    assert inspected.status_code == 200, inspected.get_json()
    assert inspected.get_json()["restored_chart"] == chart
    restored = _render(client, bundle.chart, bundle.overrides, file="portable.svg")
    assert restored["chart_revision"] == rendered["chart_revision"]
    assert restored["svg"] == rendered["svg"] and restored["manifest"] == rendered["manifest"]
    assert portable.read_bytes() == produced


@pytest.mark.parametrize("scope", ["annotations", "padding", "canvas"])
def test_all_scopes_use_the_same_native_edit_state_in_svg_and_pdf(project, scope):
    client, _root = project
    chart = _bind(client)
    edits = [_color("#ff0000"), {"gid": "axes_0", "prop": "xlim", "value": [0, 2]}]
    if scope == "canvas":
        outputs = _export(
            client,
            canvas={
                "page_w_mm": 100 * PT_MM,
                "page_h_mm": 100 * PT_MM,
                "objects": [_panel(chart, edits)],
            },
        )
    else:
        changes = {"w_mm": 100 * PT_MM, "h_mm": 100 * PT_MM}
        if scope == "annotations":
            changes["objects"] = [
                {
                    "type": "text",
                    "text": "Native annotation",
                    "x_mm": 15 * PT_MM,
                    "y_mm": 3 * PT_MM,
                    "w_mm": 70 * PT_MM,
                    "h_mm": 12 * PT_MM,
                    "size_pt": 6,
                }
            ]
        else:
            changes["pad_mm"] = [n * PT_MM for n in (5, 7, 3, 4)]
        outputs = _export(client, original=_original(chart, edits, **changes))
    _assert_svg_color(outputs["svg"].read_bytes(), "#ff0000")
    _count, size, text, image = _pdf_page(pdfium, outputs["pdf"])
    offset = (5, 7) if scope == "padding" else (0, 0)
    assert size == pytest.approx((108, 111) if scope == "padding" else (100, 100), abs=0.02)
    assert _colored(image, 30 + offset[0], 70 + offset[1], "red")
    assert not _colored(image, 70 + offset[0], 70 + offset[1], "red")
    if scope == "annotations":
        assert "Native annotation" in text


def test_one_job_different_native_edits_of_the_same_model_do_not_share_cached_source(project):
    client, _root = project
    chart = _bind(client)
    a = [_color("#ff0000")]
    b = [_color("#00ff00"), {"gid": "axes_0", "prop": "xlim", "value": [0, 2]}]
    outputs = _export(
        client,
        canvas={
            "page_w_mm": 300 * PT_MM,
            "page_h_mm": 100 * PT_MM,
            "objects": [_panel(chart, a), _panel(chart, b, 100), _panel(chart, a, 200)],
        },
    )
    raw = outputs["svg"].read_bytes()
    _assert_svg_color(raw, "#ff0000")
    _assert_svg_color(raw, "#00ff00")
    _count, size, _text, image = _pdf_page(pdfium, outputs["pdf"])
    assert size == pytest.approx((300, 100), abs=0.02)
    assert _colored(image, 70, 70, "red")
    assert _colored(image, 130, 70, "green") and not _colored(image, 170, 70, "green")
    assert _colored(image, 270, 70, "red")


def test_merged_pdf_native_edits_versions_keep_page_order_and_repeated_variant(project):
    client, _root = project
    chart = _bind(client)
    a, b = [_color("#ff0000")], [_color("#00ff00")]
    outputs = _export(
        client,
        pages=[_original(chart, a), _original(chart, b), _original(chart, a)],
        formats=("pdf",),
    )
    for index, color in enumerate(("red", "green", "red")):
        count, size, _text, image = _pdf_page(pdfium, outputs["pdf"], index=index)
        assert count == 3 and size == pytest.approx((100, 100), abs=0.02)
        assert _colored(image, 50, 70, color)


def test_exported_native_bundle_keeps_raw_order_even_with_same_canonical_patch_hash(project):
    client, _root = project
    chart = _bind(client)
    edits = [_color("#ff0000"), {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 4}]
    reverse = list(reversed(edits))
    assert patchspec.patch_hash(edits) == patchspec.patch_hash(reverse)
    first = _export(client, original=_original(chart, edits), formats=("svg",), name="ordered")
    second = _export(client, original=_original(chart, reverse), formats=("svg",), name="reversed")
    one, two = (
        chartbundle.extract_chart(first["svg"].read_bytes()),
        chartbundle.extract_chart(second["svg"].read_bytes()),
    )
    assert one.overrides == edits and two.overrides == reverse
    assert (
        _render(client, chart, edits)["chart_revision"]
        != _render(client, chart, reverse)["chart_revision"]
    )


def test_omitted_profile_exports_the_actual_legacy_v1_without_native_fields(project):
    client, root = project
    chart = _bind(client)
    original = _original(chart)
    original.pop("chart_render_profile")
    outputs = _export(client, original=original, formats=("svg",))
    produced = outputs["svg"].read_bytes()
    bundle = chartbundle.extract_chart(produced)
    assert bundle.render_profile == chartprofile.LEGACY_PROFILE and bundle.overrides == []
    assert bundle.chart == chart
    assert b"axes_0.lines_0" not in produced
    portable = root / "legacy.svg"
    portable.write_bytes(produced)
    (root / "chart.svg").unlink()
    response = client.get("/api/svg/chart/inspect?id=legacy.svg")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["restored_chart"] == chart


def test_invalid_native_edits_cannot_be_dropped_before_production_export(project):
    client, root = project
    chart = _bind(client)
    response = client.post(
        "/api/export",
        json={
            "scope": "original",
            "formats": ["svg"],
            "filename": "invalid",
            "original": _original(chart, [_color("#ff0000"), None]),
            "overwrite": "replace",
        },
    )
    assert response.status_code == 400, response.get_json()
    assert response.get_json()["code"] == "chart_model_invalid"
    assert not list((root / "tavottofile" / "export").glob("invalid*"))


@pytest.mark.parametrize("scope,background", [("original", "white"), ("canvas", "transparent")])
def test_native_production_png_tiff_share_pixels_physical_density_and_background(
    project, scope, background
):
    from PIL import Image

    from tests.support import tiffcheck

    client, root = project
    chart = _bind(client)
    edits = [_color("#ff0000"), {"gid": "axes_0", "prop": "xlim", "value": [0, 2]}]
    density = 144
    dimensions_pt = (100, 100) if scope == "original" else (120, 125)
    if scope == "original":
        source = {"original": _original(chart, edits)}
        formats = ("png", "tiff")
    else:
        source = {
            "canvas": {
                "page_w_mm": dimensions_pt[0] * PT_MM,
                "page_h_mm": dimensions_pt[1] * PT_MM,
                "objects": [
                    _panel(chart, edits),
                    {
                        "type": "text",
                        "text": "中文绘图",
                        "x_mm": 5 * PT_MM,
                        "y_mm": 104 * PT_MM,
                        "w_mm": 105 * PT_MM,
                        "h_mm": 18 * PT_MM,
                        "size_pt": 10,
                        "color": "#000000",
                        "font_family": "sans-serif",
                    },
                ],
            }
        }
        formats = ("png", "tiff", "pdf")
    outputs = _export(
        client,
        **source,
        formats=formats,
        name=f"native-{scope}-raster",
        ppi=density,
        background=background,
    )
    decoded = {}
    for fmt in ("png", "tiff"):
        with Image.open(outputs[fmt]) as image:
            image.load()
            assert image.size == tuple(round(pt * density / 72) for pt in dimensions_pt)
            assert image.info["dpi"] == pytest.approx((density, density), abs=0.02)
            assert [
                pixels * 25.4 / dpi
                for pixels, dpi in zip(image.size, image.info["dpi"], strict=True)
            ] == pytest.approx([pt * PT_MM for pt in dimensions_pt], abs=0.005)
            if background == "transparent":
                assert image.mode == "RGBA"
            decoded[fmt] = image.copy()
    tags, samples = tiffcheck.decode_samples(outputs["tiff"])
    assert tags["x_resolution"] == tags["y_resolution"] == density
    assert tags["resolution_unit"] == 2 and tags["compression"] == 8
    assert tags["samples_per_pixel"] == len(decoded["tiff"].getbands())
    assert samples == decoded["tiff"].tobytes(), "独立TIFF读取器与Pillow必须解出相同样本"
    assert decoded["png"].mode == decoded["tiff"].mode
    assert decoded["png"].tobytes() == decoded["tiff"].tobytes()
    image = decoded["png"].convert("RGBA")

    def pixel(x_pt, y_pt):
        return image.getpixel((round(x_pt * density / 72), round(y_pt * density / 72)))

    def opaque_dark_pixels(region):
        count = 0
        for y in range(region.height):
            for x in range(region.width):
                r, g, b, a = region.getpixel((x, y))
                count += a > 100 and max(r, g, b) < 100
        return count

    # 独立真值：数据线 y=70pt，xlim扩展后端点从90移到50pt；源白底仍保留。
    assert pixel(30, 70) == (255, 0, 0, 255)
    assert pixel(70, 70) == (255, 255, 255, 255)
    assert pixel(98, 2) == (255, 255, 255, 255)
    english = image.crop((6, 2, 115, 16))
    assert opaque_dark_pixels(english) > 10
    if scope == "canvas":
        assert pixel(110, 50)[3] == 0, "源图外的画布留白必须保持透明"
        assert tags["extra_samples"] == 2, "TIFF透明度为非预乘alpha"
        # 实际PDF文本层与栅格墨迹一起核对；不凭两个空白位图相等声称中文已保留。
        count, size, text, _reference = _pdf_page(pdfium, outputs["pdf"])
        assert count == 1 and size == pytest.approx(dimensions_pt, abs=0.02)
        assert "中文绘图" in text and "Source keep" in text
        chinese = image.crop((10, 208, 220, 246))
        assert opaque_dark_pixels(chinese) > 40
        import pikepdf

        with pikepdf.open(outputs["pdf"]) as pdf:
            names = [
                str(font.get("/BaseFont", "")) for font in pdf.pages[0].Resources.Font.values()
            ]
            assert any("NotoSansSC" in name for name in names), names
    assert (root / "chart.svg").read_bytes() == SOURCE


def test_complete_frame_profile_export_canonical_is_F_scene_stays_S(project):
    """N5 第 5 步：/2 面板经生产导出 job，canonical 页面 = F、scene 数据仍 S；
    /1 孪生请求保持 100×100；原源 SVG 字节不变（scope=original 与 canvas）。"""
    client, root = project
    chart = _bind(client)
    original = _original(chart, chart_render_profile=chartprofile.COMPLETE_PROFILE)
    outputs = _export(client, original=original, formats=("svg", "pdf"), name="frame2")
    count, size, text, _ = _pdf_page(pdfium, outputs["pdf"])
    assert count == 1
    frame_svg = ET.fromstring(outputs["svg"].read_bytes())
    # viewBox = [min-x, min-y, width, height]：F 外框（墨迹 ∪ S viewport + 1pt 边）
    frame_box = [float(v) for v in frame_svg.get("viewBox").split()]
    assert frame_box[2] > 100.5 and frame_box[3] > 100.5
    assert frame_box[0] < 0 or frame_box[1] < 0  # 有墨迹越出源框，才有负原点
    assert frame_svg.get("width") == f"{frame_box[2]:g}pt"
    # canonical PDF 页面 = F（pt），与 SVG envelope 一致
    assert size == pytest.approx((frame_box[2], frame_box[3]), abs=0.05)

    legacy = _original(chart, chart_render_profile=chartprofile.NATIVE_PROFILE)
    legacy_outputs = _export(client, original=legacy, formats=("pdf",), name="frame1")
    _, legacy_size, _, _ = _pdf_page(pdfium, legacy_outputs["pdf"])
    assert legacy_size == pytest.approx((100, 100), abs=0.02)

    # canvas scope：/2 面板字段带同一 profile（前端持久化形状 chartFrame），
    # 面板尺寸 = F 的毫米（size 准入按 produced artifact 裁决）
    panel = _panel(chart, [])
    panel["chartFrame"] = {
        "version": 1,
        "renderProfile": chartprofile.COMPLETE_PROFILE,
        "originPt": [frame_box[0], frame_box[1]],
    }
    panel["w_mm"] = frame_box[2] * PT_MM
    panel["h_mm"] = frame_box[3] * PT_MM
    panel.pop("chart_render_profile")
    page = {"page_w_mm": 160, "page_h_mm": 120, "objects": [panel]}
    canvas_outputs = _export(client, canvas=page, formats=("pdf",), name="frame2canvas")
    _, canvas_size, _, _ = _pdf_page(pdfium, canvas_outputs["pdf"])
    assert canvas_size[0] > 100.5 and canvas_size[1] > 100.5
    assert (root / "chart.svg").read_bytes() == SOURCE
