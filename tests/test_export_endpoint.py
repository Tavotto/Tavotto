"""/api/export 端到端：页面尺寸、矢量文字保真、hidden 过滤、旧契约兼容。

按 CLAUDE.md 的验证约定，导出 PDF 用 pymupdf get_text() 验证矢量文字。
"""

import json
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from tavotto import app as m, pdfbackend as pb
from tavotto.rendercore import pdfsemantic


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path)
    # 纯标注导出不依赖项目；清掉别的测试模块残留的已打开项目，
    # 否则导出会落到那个项目的 tavottofile/export/（project_export_dir 的默认）
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _text_obj(text, **kw):
    return {
        "type": "text",
        "text": text,
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": 80,
        "h_mm": 10,
        "size_pt": 9,
        **kw,
    }


def _export(client, tmp_path, spec):
    resp = client.post(
        "/api/export", json={"page_w_mm": 100, "page_h_mm": 50, "formats": ["pdf"], **spec}
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    files = body["files"]
    assert len(files) == 1
    # 落盘位置以响应里的 export_dir 为准：开着项目时默认是项目内的
    # tavottofile/export/，没开项目才回落到 EXPORT_DIR（fixture 里的 tmp_path）
    out_dir = Path(body["export_dir"])
    # 同 test_compose_annotations：不留文件句柄，否则 Windows 上覆盖导出会失败
    return pymupdf.open(stream=(out_dir / files[0]["name"]).read_bytes(), filetype="pdf")


def _asym_panel_dir(tmp_path):
    """左半有内容、右半空白的不对称面板，翻转与否肉眼（像素）可辨。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=50)
    page.draw_rect(pymupdf.Rect(5, 5, 45, 45), color=None, fill=(0, 0, 0))
    doc.save(figs / "asym.pdf")
    doc.close()
    return figs


def _ink_halves(doc):
    """导出 PDF 渲染后左右两半的墨量（像素值越小越黑）。"""
    pix = doc[0].get_pixmap()
    w, h, n = pix.width, pix.height, pix.n
    s = pix.samples
    left = right = 0
    for y in range(h):
        row = y * pix.stride
        for x in range(w):
            v = s[row + x * n]
            if x < w // 2:
                left += 255 - v
            else:
                right += 255 - v
    return left, right


def test_export_panel_flip_h(client, tmp_path, monkeypatch):
    """水平翻转：墨量从左半移到右半；不翻转的对照在左半。"""
    m.open_project(str(_asym_panel_dir(tmp_path)))
    panel = {"type": "panel", "id": "asym.pdf", "x_mm": 0, "y_mm": 0, "w_mm": 100, "h_mm": 50}
    plain = _export(client, tmp_path, {"stem": "plain", "objects": [panel]})
    flipped = _export(client, tmp_path, {"stem": "flip", "objects": [{**panel, "flip_h": True}]})
    l0, r0 = _ink_halves(plain)
    l1, r1 = _ink_halves(flipped)
    assert l0 > r0 * 3, (l0, r0)  # 原图墨在左
    assert r1 > l1 * 3, (l1, r1)  # 翻转后墨在右


def test_canvas_pdf_bookmarks_follow_the_published_panel_regions(client, tmp_path):
    """Inspect the published destinations and pixels with a reader independent of the writer."""
    figures = _asym_panel_dir(tmp_path)
    with pymupdf.open(figures / "asym.pdf") as source:
        source[0].get_pixmap().save(figures / "raster.png")
    m.open_project(str(figures))
    panel = {"type": "panel", "id": "asym.pdf", "x_mm": -4, "y_mm": 3, "w_mm": 24, "h_mm": 12}
    objects = [
        {
            **panel,
            "bookmark_title": "图一 \n  Cropped",
            "rotation": 90,
            "flip_h": True,
            "crop": {"x": 0.05, "y": 0.12, "w": 0.7, "h": 0.56},
        },
        _text_obj("Not a bookmark"),
        {**panel, "x_mm": 40, "y_mm": 16, "w_mm": 21, "h_mm": 15},
        {
            **panel,
            "id": "raster.png",
            "bookmark_title": "Bitmap",
            "x_mm": 80,
            "y_mm": 44,
            "w_mm": 26,
            "h_mm": 11,
        },
        {**panel, "x_mm": 110, "bookmark_title": "Off page"},
        {**panel, "x_mm": 100, "rotation": 90, "bookmark_title": "Touches right edge"},
        {**panel, "hidden": True, "bookmark_title": "Hidden"},
        {**panel, "opacity": 0, "bookmark_title": "Transparent"},
    ]
    canvas = {"page_w_mm": 100, "page_h_mm": 50, "objects": objects}
    spec = {"scope": "canvas", "canvas": canvas}
    with (
        _export(client, tmp_path, {**spec, "filename": "plain"}) as plain,
        _export(
            client, tmp_path, {**spec, "filename": "bookmarks", "pdf_panel_bookmarks": True}
        ) as bookmarked,
    ):
        assert plain.get_toc() == []
        assert bookmarked.get_toc() == [[1, "1. 图一 Cropped", 1], [1, "2", 1], [1, "3. Bitmap", 1]]
        assert plain[0].get_pixmap().samples == bookmarked[0].get_pixmap().samples
        # FitR numbers are in PDF points with a bottom-left origin.
        # The regions below are computed from fixture positions and the final page edges.
        expectations_mm = [(0, 35, 20, 47), (40, 19, 61, 34), (80, 0, 100, 6)]
        for row, expected_mm in zip(bookmarked.get_toc(simple=False), expectations_mm, strict=True):
            kind, value = bookmarked.xref_get_key(row[3]["xref"], "Dest")
            assert kind == "array"
            page, generation, reference, fit, *coordinates = (
                value.strip("[]").replace("/", " /").split()
            )
            assert (page, generation, reference, fit) == (
                str(bookmarked[0].xref),
                "0",
                "R",
                "/FitR",
            )
            assert [float(value) for value in coordinates] == pytest.approx(
                [value * 72 / 25.4 for value in expected_mm],
                abs=0.0001,  # MuPDF represents PDF real numbers as floats.
            )


def test_canvas_pdf_panel_layers_toggle_instances_without_affecting_default_output(
    client, tmp_path
):
    figures = _asym_panel_dir(tmp_path)
    with pymupdf.open(figures / "asym.pdf") as source:
        source[0].get_pixmap().save(figures / "raster.png")
    m.open_project(str(figures))
    base = {"type": "panel", "id": "asym.pdf", "y_mm": 18, "w_mm": 34, "h_mm": 24}
    left = {**base, "x_mm": -4, "rotation": 90, "bookmark_title": "面板"}
    right = {
        **base,
        "x_mm": 26,
        "flip_h": True,
        "opacity": 0.65,
        "bookmark_title": "面板",
        "crop": {"x": 0.05, "y": 0.12, "w": 0.7, "h": 0.56},
        "clip": [
            {
                "type": "path",
                "x_mm": 42,
                "y_mm": 19,
                "w_mm": 15,
                "h_mm": 17,
                "subpaths": [
                    {
                        "closed": True,
                        "nodes": [
                            {"p": [0, 0]},
                            {"p": [1, 0]},
                            {"p": [1, 1]},
                            {"p": [0, 1]},
                        ],
                    }
                ],
            }
        ],
    }
    bitmap = {**base, "id": "raster.png", "x_mm": 80, "y_mm": 44}
    caption = _text_obj("Canvas caption", y_mm=8)
    # Interleave paint and keep repeat instances as separate objects.
    visible = [left, caption, right, bitmap]
    invisible = [
        {**left, "hidden": True, "bookmark_title": "Hidden"},
        {**left, "opacity": 0, "bookmark_title": "Transparent"},
        {**left, "x_mm": 100, "bookmark_title": "Touches right edge"},
    ]

    def export(name, objects, *, layers=False, annotations=False):
        response = client.post(
            "/api/export",
            json={
                "scope": "canvas",
                "filename": name,
                "formats": ["pdf", "png"],
                "ppi": 144,
                "canvas": {"page_w_mm": 100, "page_h_mm": 50, "objects": objects},
                "pdf_panel_layers": layers,
                "pdf_annotation_layer": annotations,
            },
        )
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["status"] == "done", body
        root = Path(body["export_dir"])
        return {Path(row["name"]).suffix: root / row["name"] for row in body["files"]}

    plain = export("plain panels", visible + invisible)
    layered = export("layered panels", visible + invisible, layers=True, annotations=True)
    assert layered[".png"].read_bytes() == plain[".png"].read_bytes()
    with pymupdf.open(layered[".pdf"]) as actual, pymupdf.open(plain[".pdf"]) as baseline:
        assert baseline.get_ocgs() == {}
        assert actual.get_toc() == []
        layers = actual.layer_ui_configs()
        assert [layer["text"] for layer in layers] == [
            "1. 面板",
            "Canvas annotations",
            "2. 面板",
            "3",
        ]
        assert all(layer["on"] and not layer["locked"] for layer in layers)
        original = baseline[0].get_pixmap().samples
        assert actual[0].get_pixmap().samples == original
        assert "Canvas caption" in actual[0].get_text()
        for index, layer in enumerate(layers):
            reference = export(f"without layer {index}", visible[:index] + visible[index + 1 :])
            with pymupdf.open(reference[".pdf"]) as expected:
                actual.set_layer_ui_config(layer["number"], action=pymupdf.PDF_OC_OFF)
                disabled = actual[0].get_pixmap().samples
                omitted = expected[0].get_pixmap().samples
                if layer["text"] == "2. 面板":
                    # MuPDF composites the page edge one level differently when
                    # the transparent Form is present but disabled.
                    assert max(abs(a - b) for a, b in zip(disabled, omitted, strict=True)) <= 1
                else:
                    assert disabled == omitted
                assert any(abs(a - b) > 32 for a, b in zip(disabled, original, strict=True))
                actual.set_layer_ui_config(layer["number"], action=pymupdf.PDF_OC_ON)
                assert actual[0].get_pixmap().samples == original

    with pikepdf.Pdf.open(layered[".pdf"]) as pdf:
        groups = list(pdf.Root.OCProperties.OCGs)
        assert len({group.objgen for group in groups}) == 4


def test_canvas_pdf_annotation_layer_toggles_marks_without_changing_default_pixels(
    client, tmp_path
):
    m.open_project(str(_asym_panel_dir(tmp_path)))
    panel = {
        "type": "panel",
        "id": "asym.pdf",
        "bookmark_title": "Source",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 100,
        "h_mm": 50,
    }
    shape = {
        "type": "shape",
        "shape": "rect",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 65,
        "h_mm": 50,
        "fill": "#ff0000",
        "stroke": False,
    }
    marks = [shape, panel, _text_obj("Editable label", x_mm=55, y_mm=4, w_mm=38)]

    def export(name, objects, *, layered=False):
        response = client.post(
            "/api/export",
            json={
                "scope": "canvas",
                "filename": name,
                "formats": ["pdf", "png"],
                "ppi": 150,
                "canvas": {"page_w_mm": 100, "page_h_mm": 50, "objects": objects},
                "pdf_panel_bookmarks": True,
                "pdf_annotation_layer": layered,
            },
        )
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["status"] == "done", body
        store = Path(body["export_dir"])
        return {
            Path(item["name"]).suffix: (store / item["name"]).read_bytes() for item in body["files"]
        }

    plain = export("Plain", marks)
    layered = export("Layered", marks, layered=True)
    panel_only = export("Panel only", [panel, _text_obj("Hidden", hidden=True)], layered=True)
    assert plain[".png"] == layered[".png"]

    with (
        pymupdf.open(stream=plain[".pdf"]) as expected,
        pymupdf.open(stream=layered[".pdf"]) as actual,
        pymupdf.open(stream=panel_only[".pdf"]) as without_marks,
    ):
        assert expected.get_ocgs() == without_marks.get_ocgs() == {}
        (layer,) = actual.layer_ui_configs()
        assert layer["text"] == "Canvas annotations" and layer["on"] and not layer["locked"]
        assert actual.get_toc() == expected.get_toc() == [[1, "1. Source", 1]]
        assert len(actual[0].get_images()) == 0
        assert "Editable label" in actual[0].get_text()
        default_pixels = actual[0].get_pixmap()
        assert default_pixels.samples == expected[0].get_pixmap().samples
        assert default_pixels.pixel(40, 40) == (0, 0, 0)  # Panel draws over the annotation.
        assert default_pixels.pixel(160, 50) == (255, 0, 0)  # Unpainted area on the same panel.
        actual.set_layer_ui_config(layer["number"], action=pymupdf.PDF_OC_OFF)
        assert actual[0].get_pixmap().samples == without_marks[0].get_pixmap().samples
        assert "Editable label" not in actual[0].get_text()
        actual.set_layer_ui_config(layer["number"], action=pymupdf.PDF_OC_ON)
        assert actual[0].get_pixmap().samples == default_pixels.samples


def test_canvas_pdf_text_attachment_contains_only_painted_annotations_and_detects_stale_page(
    client, tmp_path
):
    m.open_project(str(_asym_panel_dir(tmp_path)))
    panel = {
        "type": "panel",
        "id": "asym.pdf",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 100,
        "h_mm": 50,
    }
    texts = ("实验组", "Control\nB")
    marks = [
        panel,
        _text_obj(texts[0], id="private-object-id", x_mm=60, y_mm=3),
        _text_obj("Do not attach", hidden=True),
        _text_obj("  "),
        _text_obj(texts[1], x_mm=60, y_mm=22),
    ]
    filename = "tavotto-canvas-text.json"

    def export(name, *, objects=marks, attachment=False):
        response = client.post(
            "/api/export",
            json={
                "scope": "canvas",
                "filename": name,
                "formats": ["pdf"],
                "canvas": {"page_w_mm": 100, "page_h_mm": 50, "objects": objects},
                "pdf_panel_bookmarks": True,
                "pdf_annotation_layer": True,
                "pdf_text_attachment": attachment,
            },
        )
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["status"] == "done", body
        (item,) = body["files"]
        return Path(body["export_dir"]) / item["name"]

    plain = export("Plain")
    enriched = export("With attachment", attachment=True)
    empty = export("No text", objects=[panel], attachment=True)
    with pymupdf.open(plain) as expected, pymupdf.open(enriched) as actual:
        assert expected.embfile_names() == []
        assert actual.embfile_names() == [filename]
        payload = json.loads(actual.embfile_get(filename))
        assert set(payload) == {
            "schema_version",
            "source",
            "page",
            "page_fingerprint",
            "annotations",
        }
        assert payload["schema_version"] == payload["page"] == 1
        assert payload["source"] == "tavotto.canvas.text"
        assert payload["annotations"] == [{"text": text} for text in texts]
        assert actual[0].get_pixmap(dpi=144).samples == expected[0].get_pixmap(dpi=144).samples
        assert "实验组" in actual[0].get_text()
        assert actual.get_toc() == expected.get_toc()

    with pikepdf.Pdf.open(empty) as pdf:
        assert pdfsemantic.read_canvas_text(pdf) == ()
    with pikepdf.Pdf.open(enriched) as pdf:
        assert pdfsemantic.read_canvas_text(pdf) == texts
        pdf.docinfo["/Title"] = "New document title"
        retitled = tmp_path / "retitled.pdf"
        pdf.save(retitled)
    with pikepdf.Pdf.open(retitled) as pdf:
        assert pdfsemantic.read_canvas_text(pdf) == texts
        form = next(iter(pdf.pages[0].Resources.XObject.values()))
        form.write(b"60 60 15 15 re f")
        modified = tmp_path / "modified.pdf"
        pdf.save(modified)
    with pikepdf.Pdf.open(modified) as pdf, pytest.raises(pdfsemantic.StaleTextAttachment):
        pdfsemantic.read_canvas_text(pdf)

    revised = export("Revised canvas", objects=[panel, _text_obj("Revised B")], attachment=True)
    with pikepdf.Pdf.open(revised) as pdf:
        assert pdfsemantic.read_canvas_text(pdf) == ("Revised B",)
        pdf.attachments[filename] = pikepdf.AttachedFileSpec(
            pdf, json.dumps(payload).encode("utf-8"), mime_type="application/json"
        )
        with pytest.raises(pdfsemantic.StaleTextAttachment):
            pdfsemantic.read_canvas_text(pdf)


def test_export_single_figure_frame_with_annotation(client, tmp_path):
    """QA-WIN-20：单图档带标注时前端发的是「页面 = 原图图幅、面板在 (0,0) + 标注」的画布请求
    （`web/src/lib/singleExportDoc.ts`）。这条路径得把图和标注一起写进同一个矢量 PDF，页面就是图幅。"""
    m.open_project(str(_asym_panel_dir(tmp_path)))
    panel = {"type": "panel", "id": "asym.pdf", "x_mm": 0, "y_mm": 0, "w_mm": 100, "h_mm": 50}
    doc = _export(
        client,
        tmp_path,
        {"stem": "annot", "objects": [panel, _text_obj("Label QA", x_mm=55, y_mm=5)]},
    )
    page = doc[0]
    assert "Label QA" in page.get_text()
    assert (round(page.rect.width * 25.4 / 72), round(page.rect.height * 25.4 / 72)) == (100, 50)
    left, right = _ink_halves(doc)
    assert left > right * 2, (left, right)  # 图（左半的方块）仍在


def test_export_panel_flip_v_keeps_horizontal(client, tmp_path, monkeypatch):
    """垂直翻转不影响左右分布（内容仍在左半）。"""
    m.open_project(str(_asym_panel_dir(tmp_path)))
    panel = {
        "type": "panel",
        "id": "asym.pdf",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 100,
        "h_mm": 50,
        "flip_v": True,
    }
    doc = _export(client, tmp_path, {"stem": "flipv", "objects": [panel]})
    left, right = _ink_halves(doc)
    assert left > right * 3, (left, right)


def test_export_vector_text_and_page_size(client, tmp_path):
    doc = _export(client, tmp_path, {"stem": "t", "objects": [_text_obj("Hello 等离子体")]})
    page = doc[0]
    assert page.rect.width == pytest.approx(pb.mm2pt(100), abs=0.1)
    assert page.rect.height == pytest.approx(pb.mm2pt(50), abs=0.1)
    text = page.get_text()
    assert "Hello" in text and "等离子体" in text  # 真矢量文字，非位图


def test_export_italic_text_embeds_italic_font(client, tmp_path):
    doc = _export(client, tmp_path, {"stem": "t", "objects": [_text_obj("slanted", italic=True)]})
    fonts = {
        span["font"]
        for block in doc[0].get_text("rawdict")["blocks"]
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    }
    assert any("Italic" in f for f in fonts)


def test_export_skips_hidden_objects(client, tmp_path):
    doc = _export(
        client,
        tmp_path,
        {
            "stem": "t",
            "objects": [_text_obj("visible"), {**_text_obj("SECRET"), "hidden": True, "y_mm": 30}],
        },
    )
    text = doc[0].get_text()
    assert "visible" in text and "SECRET" not in text


def test_export_legacy_items_texts_contract(client, tmp_path):
    """旧 bundle 标签页仍发 items[]+texts[]，必须继续可用。"""
    doc = _export(
        client,
        tmp_path,
        {"stem": "t", "objects": None, "items": [], "texts": [_text_obj("legacy")]},
    )
    assert "legacy" in doc[0].get_text()


def test_export_shape_and_arrow_draw_vector_paths(client, tmp_path):
    doc = _export(
        client,
        tmp_path,
        {
            "stem": "t",
            "objects": [
                {
                    "type": "shape",
                    "shape": "rect",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 20,
                    "h_mm": 10,
                    "stroke_pt": 1,
                    "color": "#ff0000",
                    "fill": None,
                },
                {
                    "type": "arrow",
                    "x_mm": 30,
                    "y_mm": 5,
                    "w_mm": 20,
                    "h_mm": 10,
                    "start": {"rx": 0, "ry": 0},
                    "end": {"rx": 1, "ry": 1},
                    "stroke_pt": 1,
                    "color": "#000000",
                    "head": "end",
                },
            ],
        },
    )
    # 白底矩形 + rect 描边 + 箭头干线 + 箭头帽
    assert len(doc[0].get_drawings()) >= 4


def test_export_stem_sanitized(client, tmp_path):
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 50,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "stem": "图9/../x",
            "objects": [],
        },
    )
    name = resp.get_json()["files"][0]["name"]
    assert "/" not in name and ".." not in name
    assert name.startswith("图9_")


# ---------------------------------------------------------------------------
# 匿名用量统计：导出是最重要的一条激活事件，因此**只在服务端、只在成功之后**记。
# 前端记的是「用户点了导出」，点了之后还可能失败；这里记的是「导出成功了」。
# ---------------------------------------------------------------------------
def _telemetry_flush():
    from tavotto.engine import telemetry

    assert telemetry.flush(5.0)


def _exports(box):
    return [p for p in box if p["event"] == "export_completed"]


def test_successful_export_captures_exactly_one_event(client, tmp_path, telemetry_sent):
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "objects": [_text_obj("hello")],
        },
    )
    assert resp.status_code == 200
    _telemetry_flush()
    assert len(_exports(telemetry_sent)) == 1


def test_export_event_carries_only_shape_not_content(client, tmp_path, telemetry_sent):
    """事件里不许出现 stem、导出目录、文件名、画布名或项目信息。"""
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf", "png"],
            "stem": "Fig1_kinetics_保密数据",
            "proof": {"kind": "tavotto-proof"},
            "objects": [_text_obj("hello")],
        },
    )
    assert resp.status_code == 200
    out_dir = resp.get_json()["export_dir"]
    _telemetry_flush()
    (event,) = _exports(telemetry_sent)
    assert event["properties"]["pdf"] is True
    assert event["properties"]["png"] is True
    assert event["properties"]["with_proof"] is True
    blob = json.dumps(event, ensure_ascii=False)
    assert "Fig1_kinetics" not in blob
    assert "保密数据" not in blob
    assert out_dir not in blob
    for name in [f["name"] for f in resp.get_json()["files"]]:
        assert name not in blob


def test_export_event_counts_only_visible_panels(client, tmp_path, telemetry_sent):
    m.open_project(str(_asym_panel_dir(tmp_path)))
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "objects": [
                {"type": "panel", "id": "asym.pdf", "x_mm": 0, "y_mm": 0, "w_mm": 40, "h_mm": 20},
                {
                    "type": "panel",
                    "id": "asym.pdf",
                    "x_mm": 0,
                    "y_mm": 25,
                    "w_mm": 40,
                    "h_mm": 20,
                    "hidden": True,
                },
                _text_obj("hello"),
            ],
        },
    )
    assert resp.status_code == 200
    _telemetry_flush()
    (event,) = _exports(telemetry_sent)
    assert event["properties"]["panel_count"] == 1


def test_failed_export_captures_nothing(client, tmp_path, telemetry_sent, monkeypatch):
    """失败的导出绝不能被记成成功——那会让激活率凭空变高。"""
    from tavotto.rendercore import pdfwriter

    def boom(*_a, **_kw):
        raise OSError("磁盘满了")

    # 注在写入器（U10 之前是旧 `compose().save_pdf`，ADR 0072）
    monkeypatch.setattr(pdfwriter, "write_pdf", boom)
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "objects": [_text_obj("hello")],
        },
    )
    assert resp.status_code == 500
    _telemetry_flush()
    assert _exports(telemetry_sent) == []


def test_export_succeeds_even_when_telemetry_transport_fails(
    client, tmp_path, telemetry_sent, monkeypatch
):
    """代理挂了 / 断网 / 埋点自己有 bug —— 导出的响应契约一个字节都不许变。"""
    from tavotto.engine import telemetry

    def boom(_payload):
        raise OSError("代理挂了")

    monkeypatch.setattr(telemetry, "_post", boom)
    monkeypatch.setattr(
        telemetry, "validate", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("埋点炸了"))
    )
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "objects": [_text_obj("hello")],
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["files"] and body["export_dir"] and body["warnings"] == []


def test_export_captures_nothing_without_consent(client, tmp_path, monkeypatch):
    from tavotto.engine import telemetry

    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    box: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", box.append)
    assert telemetry.settings()["consent"] == "unset"
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "objects": [_text_obj("hello")],
        },
    )
    assert resp.status_code == 200
    assert telemetry.flush(5.0)
    assert box == []
    telemetry.reset_for_tests()


def _square_panel_dir(tmp_path):
    """左上象限有黑块的正方形面板：旋转方向（±90°）像素级可辨。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=100)
    page.draw_rect(pymupdf.Rect(0, 0, 50, 50), color=None, fill=(0, 0, 0))
    doc.save(figs / "sq.pdf")
    doc.close()
    return figs


def test_export_panel_rotation_is_css_clockwise(client, tmp_path, monkeypatch):
    """面板 rotation 与前端 CSS rotate() 同向（顺时针）：90° 左上黑块转到右上、
    270° 转到左下。两侧都钉——flip 系列用例量的是镜像，看不见旋转方向这一维。"""
    m.open_project(str(_square_panel_dir(tmp_path)))
    # opacity=1 走 show_pdf_page，opacity<1 走位图 insert_image——两条路各有
    # 一个 rotate 参数，方向要分别钉住。
    cases = [(rot, dark_q, op) for rot, dark_q in ((90, "TR"), (270, "BL")) for op in (None, 0.5)]
    for rot, dark_q, opacity in cases:
        panel = {
            "type": "panel",
            "id": "sq.pdf",
            "x_mm": 25,
            "y_mm": 0,
            "w_mm": 50,
            "h_mm": 50,
            "rotation": rot,
        }
        if opacity is not None:
            panel["opacity"] = opacity
        doc = _export(client, tmp_path, {"stem": f"rot{rot}o{opacity}", "objects": [panel]})
        pix = doc[0].get_pixmap()

        def lum(mx, my):
            return sum(pix.pixel(int(pb.mm2pt(mx)), int(pb.mm2pt(my))))

        q = {
            "TL": lum(37.5, 12.5),
            "TR": lum(62.5, 12.5),
            "BL": lum(37.5, 37.5),
            "BR": lum(62.5, 37.5),
        }
        assert min(q, key=q.get) == dark_q, (rot, opacity, q)


# ---------------- 标注里的公式（ADR 0152 PR 4）----------------------------------


def _mathtext_ready():
    import importlib.util

    if importlib.util.find_spec("matplotlib") is None:
        pytest.skip("测试解释器没有 matplotlib：公式排版子进程无从起（not_run）")


def test_export_latex_math_is_drawn_as_vector_outlines(client, tmp_path, monkeypatch):
    """`k = $x^2$`：公式画成填充轮廓（不是文字、不是位图），周围的字照常是文字层。"""
    _mathtext_ready()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    doc = _export(
        client,
        tmp_path,
        {"stem": "t", "objects": [_text_obj("k = $x^2$ end", text_format="latex")]},
    )
    page = doc[0]
    text = page.get_text()
    assert "k =" in text and "end" in text
    # 文本层里公式处是它的 LaTeX 源码（ActualText，ADR 0152 §七；MuPDF 会在 `$` 旁多带空白，故去空白比）
    assert "k=$x^2$end" in "".join(text.split())
    filled = [d for d in page.get_drawings() if d.get("fill") is not None]
    assert filled, "公式应当是填充的矢量轮廓"


def test_export_unrenderable_math_keeps_source_and_warns(client, tmp_path, monkeypatch):
    _mathtext_ready()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "stem": "t",
            "objects": [_text_obj("k $\\badcmd{x}$", text_format="latex")],
        },
    )
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert any("math_unrenderable" in w for w in body.get("warnings", [])), body.get("warnings")
    out = Path(body["export_dir"]) / body["files"][0]["name"]
    assert "$\\badcmd{x}$" in pymupdf.open(stream=out.read_bytes(), filetype="pdf")[0].get_text()
