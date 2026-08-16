"""可复现项目包：打包（布局+素材+清单）与检视（缺失/漂移对照）。"""
import io
import json
import zipfile

import pymupdf
import pytest

from magplot import app as m


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    monkeypatch.setattr(m, "FIGURES_DIR", figs)
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.app.config["TESTING"] = True
    return m.app.test_client(), figs, tmp_path


def _layout(file_id="p1.pdf"):
    return {
        "schema": 2, "name": "t", "page": {"w": 150, "h": 100},
        "objects": [{"id": "o1", "type": "panel", "fileId": file_id,
                     "fileKind": "pdf", "nativeW": 35, "nativeH": 17,
                     "x": 0, "y": 0, "w": 35, "h": 17, "overrides": []}],
        "guides": [],
    }


def test_package_roundtrip(env):
    client, figs, tmp = env
    resp = client.post("/api/package", json={"stem": "pk", "doc": _layout(),
                                             "settings": {"dpi": 600}})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["assets"] == 1 and body["missing"] == []
    # 新包：.magplot 扩展 + magplot-package 标识
    assert body["name"].endswith(".magplot")

    zpath = tmp / "exports" / body["name"]
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        assert "layout.json" in names
        assert "package_manifest.json" in names
        assert "assets/p1.pdf" in names
        man = json.loads(z.read("package_manifest.json"))
        assert man["assets"][0]["sha1"]
        assert man["kind"] == "magplot-package"

    # 原机检视：素材齐全
    with open(zpath, "rb") as f:
        resp = client.post("/api/package/open",
                           data={"package": (io.BytesIO(f.read()), "pk.zip")},
                           content_type="multipart/form-data")
    body = resp.get_json()
    assert body["missing"] == [] and body["drifted"] == []
    assert body["doc"]["schema"] == 2


def test_package_open_reports_missing_and_drift(env, tmp_path):
    client, figs, tmp = env
    zdata = client.post("/api/package", json={"doc": _layout()}).get_json()
    zpath = tmp / "exports" / zdata["name"]

    # 素材内容漂移
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=50)
    page.insert_text((10, 25), "changed")
    doc.save(figs / "p1.pdf")
    doc.close()
    with open(zpath, "rb") as f:
        body = client.post("/api/package/open",
                           data={"package": (io.BytesIO(f.read()), "pk.zip")},
                           content_type="multipart/form-data").get_json()
    assert body["drifted"] == ["p1.pdf"]

    # 素材缺失
    (figs / "p1.pdf").unlink()
    with open(zpath, "rb") as f:
        body = client.post("/api/package/open",
                           data={"package": (io.BytesIO(f.read()), "pk.zip")},
                           content_type="multipart/form-data").get_json()
    assert body["missing"] == ["p1.pdf"]


def test_package_schema3_collects_assets_across_canvases(env, tmp_path):
    client, figs, tmp = env
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p2.pdf")
    doc.close()
    pd = {
        "schema": 3,
        "project": {"id": "p1", "name": "多画布项目"},
        "canvases": [
            {"id": "c1", "name": "Fig 1", "page": {"w": 150, "h": 100},
             "objects": [{"id": "o1", "type": "panel", "fileId": "p1.pdf"}],
             "guides": []},
            {"id": "c2", "name": "Fig 2", "page": {"w": 80, "h": 60},
             "objects": [{"id": "o2", "type": "panel", "fileId": "p2.pdf"}],
             "guides": []},
        ],
        "activeCanvasId": "c1", "createdAt": 0, "updatedAt": 0,
    }
    body = client.post("/api/package", json={"doc": pd}).get_json()
    assert body["assets"] == 2  # 两张画布的素材都进包
    zpath = tmp / "exports" / body["name"]
    with zipfile.ZipFile(zpath) as z:
        assert json.loads(z.read("layout.json"))["schema"] == 3
    with open(zpath, "rb") as f:
        opened = client.post("/api/package/open",
                             data={"package": (io.BytesIO(f.read()), "x.magplot")},
                             content_type="multipart/form-data").get_json()
    assert opened["doc"]["schema"] == 3
    assert opened["missing"] == []


def test_legacy_mmpack_zip_still_opens(env):
    """Magic Matplot 时代的 .mmpack.zip（kind=magic-matplot-package）必须继续可开。"""
    client, figs, tmp = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("layout.json", json.dumps(_layout()))
        z.writestr("package_manifest.json", json.dumps({
            "kind": "magic-matplot-package", "version": 1,
            "assets": [{"id": "p1.pdf"}], "scripts": [],
        }))
    buf.seek(0)
    body = client.post("/api/package/open",
                       data={"package": (buf, "old.mmpack.zip")},
                       content_type="multipart/form-data").get_json()
    assert body["doc"]["schema"] == 2
    assert body["missing"] == []


def test_package_rejects_bad_zip(env):
    client, _, _ = env
    resp = client.post("/api/package/open",
                       data={"package": (io.BytesIO(b"not a zip"), "x.zip")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_export_writes_proof_report(env):
    client, figs, tmp = env
    resp = client.post("/api/export", json={
        "page_w_mm": 100, "page_h_mm": 50, "formats": ["pdf"], "stem": "pf",
        "objects": [],
        "proof": {"kind": "magplot-proof", "checks": []},
    })
    files = resp.get_json()["files"]
    proof = next(f for f in files if f["name"].endswith("_proof.json"))
    data = json.loads((tmp / "exports" / proof["name"]).read_text(encoding="utf-8"))
    assert data["kind"] == "magplot-proof"
    assert data["files"]  # 成图文件名回填进 report
