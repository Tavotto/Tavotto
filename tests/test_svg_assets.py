"""SVG file display: exact bytes, physical dimensions, live revalidation and project isolation."""

import pytest

from tavotto import app as m, svgasset
from tavotto.engine import originalspec, pool, project_refresh, project_watch

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="48" viewBox="0 0 96 48"><path d="M0 0L96 48" stroke="red"/></svg>'


@pytest.mark.parametrize(
    ("dimensions", "expected"),
    [
        ("width='25.4mm' height='12.7mm'", (72, 36)),
        ("width='1in' height='0.5in'", (72, 36)),
        ("width='72pt' height='36pt'", (72, 36)),
        ("viewBox='0 0 96 48'", (72, 36)),
        ("width='72pt' viewBox='0 0 2 1'", (72, 36)),
    ],
)
def test_dimensions(tmp_path, dimensions, expected):
    source = tmp_path / "F.svg"
    source.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {dimensions}/>')
    _, facts = svgasset.read(source)
    assert (facts["w_pt"], facts["h_pt"]) == expected
    spec = originalspec.asset_spec(source, "svg", facts)
    assert spec["source_kind"] == "vector"
    assert (spec["logical_w_mm"], spec["logical_h_mm"]) == (25.4, 12.7)
    assert spec["px_w"] is None


@pytest.mark.parametrize(
    "content",
    [
        b'<svg width="100%" height="100%"/>',
        b'<svg width="0" height="1"/>',
        b'<svg viewBox="0 0 nan 1"/>',
        b'<svg height="1pt" viewBox="0 0 1e-308 1e308"/>',
        b'<svg width="1pt" viewBox="0 0 1e308 1e-308"/>',
        b'<svg height="100pt" viewBox="0 0 1e308 1e-308"/>',
        b'<svg width="100pt" viewBox="0 0 1e-308 1e308"/>',
        b"<svg/>",
        b'<!DOCTYPE svg SYSTEM "https://example.org/evil.dtd"><svg width="1" height="1"/>',
        b'<svg width="1" height="1"><script>alert(1)</script></svg>',
        b'<svg width="1" height="1"><image href="file:///private/data.png"/></svg>',
        b'<svg width="1" height="1" onload="alert(1)"/>',
    ],
)
def test_unsupported_content_is_never_served(tmp_path, content):
    source = tmp_path / "F.svg"
    source.write_bytes(content)
    with pytest.raises(svgasset.UnsupportedSVG):
        svgasset.read(source)


def test_inventory_keeps_svg_beside_pdf_and_prunes_hidden(tmp_path):
    (tmp_path / "F.pdf").touch()
    (tmp_path / "F.svg").write_bytes(SVG)
    (tmp_path / ".private.svg").write_bytes(SVG)
    assert {(p.name, kind) for p, kind in project_refresh.iter_assets(tmp_path)} == {
        ("F.pdf", "pdf"),
        ("F.svg", "svg"),
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "F.svg").write_bytes(SVG)
    m.open_project(str(root))
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("static SVG display executed a script")
    )
    yield m.app.test_client(), root
    m.reset_projects()
    project_watch.stop()


def test_http_lists_and_serves_svg_and_revalidates_modified_bytes(project):
    client, root = project
    response = client.get("/api/panels")
    assert response.status_code == 200
    (panel,) = response.json["panels"]
    assert panel["kind"] == "svg"
    assert (panel["native_w_mm"], panel["native_h_mm"]) == (25.4, 12.7)
    response = client.get("/api/file?id=F.svg")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert response.data == SVG
    preview = client.get("/api/render?id=F.svg&w=400")
    assert preview.status_code == 200
    assert preview.mimetype == "image/svg+xml"
    assert preview.data == SVG
    assert "sandbox" in response.headers["Content-Security-Policy"]
    (root / "F.svg").write_text("<svg><script>alert(1)</script></svg>")
    assert client.get("/api/file?id=F.svg").status_code == 422
    response = client.get("/api/panels")
    assert response.json["panels"] == []
    assert response.json["unsupported"][0]["code"] == "svg_asset_unsupported"


def test_svg_file_path_still_requires_project_containment(project):
    client, root = project
    (root.parent / "secret.svg").write_bytes(SVG)
    assert client.get("/api/file?id=../secret.svg").status_code == 403
    assert client.get("/api/file?id=F.svg&pj=not-open").status_code == 409


def test_inert_matplotlib_declaration_is_removed_but_custom_entities_fail(tmp_path):
    from tavotto.rendercore.svgcheck import _MATPLOTLIB_DOCTYPE

    source = tmp_path / "F.svg"
    source.write_bytes(_MATPLOTLIB_DOCTYPE + SVG)
    data, _ = svgasset.read(source)
    assert data == SVG
    source.write_bytes(_MATPLOTLIB_DOCTYPE + b'<!DOCTYPE svg [<!ENTITY x "unsafe">]>' + SVG)
    with pytest.raises(svgasset.UnsupportedSVG):
        svgasset.read(source)


def test_original_svg_export_preserves_validated_source_without_execution(project):
    from tavotto.engine import exportjob

    client, root = project
    exportjob.reset_for_tests()
    try:
        response = client.post(
            "/api/export",
            json={
                "scope": "original",
                "filename": "imported",
                "formats": ["svg"],
                "overwrite": "replace",
                "original": {"figure_id": "F.svg", "overrides": [], "source_kind": "vector"},
            },
        )
        assert response.status_code == 200, response.json
        assert (
            root / "tavottofile" / "export" / response.json["files"][0]["name"]
        ).read_bytes() == SVG
    finally:
        exportjob.reset_for_tests()


def test_canvas_svg_export_fails_explicitly_instead_of_dropping_the_image(project):
    from tavotto.engine import exportjob

    client, root = project
    exportjob.reset_for_tests()
    try:
        response = client.post(
            "/api/export",
            json={
                "scope": "canvas",
                "filename": "canvas",
                "formats": ["pdf"],
                "overwrite": "replace",
                "canvas": {
                    "page_w_mm": 100,
                    "page_h_mm": 100,
                    "objects": [
                        {
                            "type": "panel",
                            "id": "F.svg",
                            "x_mm": 0,
                            "y_mm": 0,
                            "w_mm": 25.4,
                            "h_mm": 12.7,
                            "overrides": [],
                        }
                    ],
                },
            },
        )
        assert response.status_code >= 400, response.json
        assert response.json["code"] == "svg_asset_export_unsupported", response.json
        assert not response.json.get("files")
        assert not (root / "tavottofile" / "export" / "canvas.pdf").exists()
    finally:
        exportjob.reset_for_tests()


def test_stock_declaration_inside_svg_content_is_not_removed():
    from tavotto.rendercore.svgcheck import _MATPLOTLIB_DOCTYPE, strip_matplotlib_doctype

    data = (
        b'<svg width="96" height="48"><text><![CDATA[' + _MATPLOTLIB_DOCTYPE + b"]]></text></svg>"
    )
    assert strip_matplotlib_doctype(data) == data


def _original_eps_request(client, formats):
    from tavotto.engine import exportjob

    exportjob.reset_for_tests()
    try:
        response = client.post(
            "/api/export",
            json={
                "scope": "original",
                "filename": "imported",
                "formats": formats,
                "overwrite": "replace",
                "original": {"figure_id": "F.svg", "overrides": [], "source_kind": "vector"},
            },
        )
        assert response.status_code == 200, response.json
        return response.json
    finally:
        exportjob.reset_for_tests()


def test_scripted_svg_asset_original_eps_goes_through_the_worker(project, monkeypatch):
    """有脚本的 SVG 素材：EPS 不被 SVG 分支提前拦截，走 `_serialize_figure`（与 epsAvailability 同源）。"""
    client, root = project
    calls = []

    class FakeWorker:
        export_dir = root

        def export(self, stem, patches, path, fmt="pdf", dpi=600):
            calls.append((stem, fmt))
            from pathlib import Path

            Path(path).write_bytes(
                b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 200 150\n%%EndComments\n%%EOF\n"
            )
            return {"warnings": []}

    monkeypatch.setattr(m, "_safe_worker", lambda *a, **kw: FakeWorker())
    monkeypatch.setattr(
        m,
        "current_registry",
        lambda: type("R", (), {"for_stem": lambda self, s: {"script": "s.py", "entry": "main"}})(),
    )
    body = _original_eps_request(client, ["svg", "eps"])
    assert body["status"] == "done", body
    assert calls == [("F", "eps")]
    assert {o["format"] for o in body["outputs"] if o["status"] == "done"} == {"svg", "eps"}


def test_unscripted_svg_asset_original_eps_reports_needs_script(project):
    client, _ = project
    body = _original_eps_request(client, ["svg", "eps"])
    assert body["status"] == "partial", body
    eps = next(o for o in body["outputs"] if o["format"] == "eps")
    assert eps["error"]["code"] == "eps_needs_script"
