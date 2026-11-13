"""精确磁盘事实与原图产物；清单让位不等于源文件不存在。"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

from tavotto import app as m, security
from tavotto.engine import exportjob, pool
from tavotto.rendercore import renderhost


def _png(path: Path, width=64, height=48, *, dpi=None):
    def chunk(kind, body):
        return (
            struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind + body))
        )

    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
    if dpi is not None:
        data += chunk(b"pHYs", struct.pack("!2IB", round(dpi / 0.0254), round(dpi / 0.0254), 1))
    data += chunk(b"IDAT", zlib.compress((b"\x00" + b"\x24\x60\x90" * width) * height))
    path.write_bytes(data + chunk(b"IEND", b""))


def _pdf(path: Path):
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(100, 100))
        page.obj.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
        )
        page.obj.Contents = pdf.make_stream(b"BT /F1 9 Tf 8 45 Td (OriginalIntrinsic) Tj ET")
        pdf.save(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    m.reset_projects(wait=True)
    root = tmp_path / "project"
    root.mkdir()
    _pdf(root / "same.pdf")
    _png(root / "same.png")
    m.open_project(str(root))
    m.app.config["TESTING"] = True
    calls = []

    def deny(*args, **kwargs):
        calls.append("user_execution")
        raise AssertionError("Read-only facts and static originals must never execute a script")

    for name in ("get", "build", "build_owned", "one_shot"):
        monkeypatch.setattr(pool, name, deny)
    monkeypatch.setattr(m, "_serialize_figure", deny)
    try:
        yield m.app.test_client(), root
    finally:
        m.reset_projects(wait=True)
        exportjob.reset_for_tests()
        renderhost.shutdown_shared()
        assert calls == []


def _facts(client, id_, **kwargs):
    return client.get("/api/panel-stem", query_string={"id": id_, "original_spec": "1", **kwargs})


def test_default_stem_wire_and_inventory_priority_are_unchanged(env):
    client, _ = env
    assert (
        client.get("/api/panel-stem?id=same.png").get_data() == b'{"id":"same.png","stem":"same"}\n'
    )
    assert [row["id"] for row in client.get("/api/panels").get_json()["panels"]] == ["same.pdf"]


def test_exact_hidden_png_facts_reuse_original_spec_authority(env):
    client, root = env
    response = _facts(client, "same.png")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    result = response.get_json()
    assert set(result) == {"id", "stem", "kind", "original_spec"}
    assert (result["id"], result["stem"], result["kind"]) == (
        "same.png",
        "same",
        "raster",
    )
    assert result["original_spec"] == m._asset_original_spec(root / "same.png", "raster")
    assert result["original_spec"]["px_w"] == 64
    assert result["original_spec"]["px_h"] == 48
    assert result["original_spec"]["dpi_source"] == "assumed"
    assert result["original_spec"]["dpi"] == 600


def test_exact_facts_preserve_real_density(env):
    client, root = env
    _png(root / "same.png", dpi=300)
    facts = _facts(client, "same.png").get_json()["original_spec"]
    assert facts["dpi_source"] == "metadata"
    assert facts["dpi"] == 300
    assert facts["logical_w_mm"] == pytest.approx(64 * 25.4 / 300, abs=0.001)


@pytest.mark.parametrize(
    "id_,status", [("missing.png", 404), ("../outside.png", 403), ("bad.py", 403)]
)
def test_exact_facts_do_not_relax_safe_paths(env, id_, status):
    client, root = env
    (root.parent / "outside.png").write_bytes((root / "same.png").read_bytes())
    (root / "bad.py").write_text("raise AssertionError('must not execute')")
    assert _facts(client, id_).status_code == status


def test_exact_source_facts_use_session_authentication(env, monkeypatch):
    client, _ = env
    state = security.SessionState("test-only-nonce")
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    assert _facts(client, "same.png").status_code == 401
    assert _facts(client, "same.png").get_json()["code"] == "session_auth_required"
    token = state.redeem("test-only-nonce")
    client.set_cookie(security.COOKIE_NAME, token)
    assert _facts(client, "same.png").status_code == 200


def test_exact_facts_are_project_scoped_and_wrong_project_does_not_fallback(env):
    client, root = env
    other = root.parent / "other"
    other.mkdir()
    _png(other / "same.png", width=21, height=13)
    other_pj = m.open_project(str(other), make_default=False)["id"]
    assert _facts(client, "same.png").get_json()["original_spec"]["px_w"] == 64
    assert _facts(client, "same.png", pj=other_pj).get_json()["original_spec"]["px_w"] == 21
    assert _facts(client, "same.png", pj="unknown-project").status_code == 409


def test_hidden_png_direct_original_preserves_bytes_grid_and_explicit_pdf_size(env):
    client, root = env
    original = (root / "same.png").read_bytes()
    response = client.post(
        "/api/export",
        json={
            "scope": "original",
            "filename": "exact",
            "formats": ["pdf", "png", "tiff"],
            "ppi": 600,
            "original": {
                "figure_id": "same.png",
                "w_mm": 32,
                "h_mm": 24,
                "source_kind": "raster",
            },
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "done"
    outputs = {item["format"]: item for item in body["outputs"]}
    out = Path(body["export_dir"])
    assert (out / outputs["png"]["name"]).read_bytes() == original
    for fmt in ("png", "tiff"):
        with Image.open(out / outputs[fmt]["name"]) as image:
            assert image.size == (64, 48)
            assert image.info.get("dpi") is None
    with pdfium.PdfDocument(out / outputs["pdf"]["name"]) as pdf:
        assert [x * 25.4 / 72 for x in pdf[0].get_size()] == pytest.approx([32, 24], abs=0.001)


def test_pdf_original_and_its_rasters_report_actual_intrinsic_size(env):
    client, _ = env
    response = client.post(
        "/api/export",
        json={
            "scope": "original",
            "filename": "physical",
            "formats": ["pdf", "png", "tiff"],
            "ppi": 600,
            "original": {
                "figure_id": "same.pdf",
                "w_mm": 40,
                "h_mm": 40,
                "source_kind": "vector",
            },
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "done"
    outputs = {item["format"]: item for item in body["outputs"]}
    out = Path(body["export_dir"])
    with pdfium.PdfDocument(out / outputs["pdf"]["name"]) as pdf:
        assert pdf[0].get_size() == (100, 100)
        textpage = pdf[0].get_textpage()
        try:
            assert "OriginalIntrinsic" in textpage.get_text_range()
        finally:
            textpage.close()
    for fmt in ("pdf", "png", "tiff"):
        assert outputs[fmt]["dimensions"]["mm"] == pytest.approx([100 * 25.4 / 72] * 2, abs=0.001)
    for fmt in ("png", "tiff"):
        with Image.open(out / outputs[fmt]["name"]) as image:
            assert image.size == (833, 833)
            assert [float(x) for x in image.info["dpi"]] == pytest.approx([600, 600], abs=0.01)


def test_invalid_svg_uses_the_same_stable_asset_failure_as_its_preview(env):
    client, root = env
    (root / "bad.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
    response = _facts(client, "bad.svg")
    assert response.status_code == 422
    assert response.get_json()["code"] == "svg_asset_unsupported"
    assert "original_spec" not in response.get_json()


def test_invalid_pdf_has_a_stable_failure_and_never_positive_source_facts(env):
    client, root = env
    (root / "bad.pdf").write_bytes(b"this is not PDF")
    response = _facts(client, "bad.pdf")
    assert response.status_code == 500
    assert response.get_json()["code"] == "internal_error"
    assert "original_spec" not in response.get_json()


def test_exact_fact_io_failure_uses_the_existing_global_error_mapping(env, monkeypatch):
    client, _ = env

    def unavailable(*args, **kwargs):
        raise PermissionError("probe permission denied")

    monkeypatch.setattr(m, "_asset_original_spec", unavailable)
    response = _facts(client, "same.png")
    assert response.status_code == 500
    assert response.get_json()["code"] == "internal_error"
    assert "original_spec" not in response.get_json()
