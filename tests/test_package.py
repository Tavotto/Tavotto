"""可复现项目包：打包（布局+素材+清单）与检视（缺失/漂移对照）。"""

import io
import json
import zipfile
from types import SimpleNamespace

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import documentassets, documents
from test_chart_document_assets import (
    SCENE_DIGEST,
    SOURCE,
    SOURCE_ID,
    chart as chart_state,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    m.open_project(str(figs))
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.app.config["TESTING"] = True
    return m.app.test_client(), figs, tmp_path


def _layout(file_id="p1.pdf"):
    return {
        "schema": 2,
        "name": "t",
        "page": {"w": 150, "h": 100},
        "objects": [
            {
                "id": "o1",
                "type": "panel",
                "fileId": file_id,
                "fileKind": "pdf",
                "nativeW": 35,
                "nativeH": 17,
                "x": 0,
                "y": 0,
                "w": 35,
                "h": 17,
                "overrides": [],
            }
        ],
        "guides": [],
    }


def test_package_roundtrip(env):
    client, figs, tmp = env
    resp = client.post(
        "/api/package", json={"stem": "pk", "doc": _layout(), "settings": {"dpi": 600}}
    )
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["assets"] == 1 and body["missing"] == []
    # 新包：.tavotto 扩展 + tavotto-package 标识
    assert body["name"].endswith(".tavotto")

    zpath = figs / "tavottofile" / "export" / body["name"]
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        assert "layout.json" in names
        assert "package_manifest.json" in names
        assert "assets/p1.pdf" in names
        man = json.loads(z.read("package_manifest.json"))
        assert man["assets"][0]["sha1"]
        assert man["kind"] == "tavotto-package"

    # 原机检视：素材齐全
    with open(zpath, "rb") as f:
        resp = client.post(
            "/api/package/open",
            data={"package": (io.BytesIO(f.read()), "pk.zip")},
            content_type="multipart/form-data",
        )
    body = resp.get_json()
    assert body["missing"] == [] and body["drifted"] == []
    assert body["doc"]["schema"] == 2


def test_package_open_reports_missing_and_drift(env, tmp_path):
    client, figs, tmp = env
    zdata = client.post("/api/package", json={"doc": _layout()}).get_json()
    zpath = figs / "tavottofile" / "export" / zdata["name"]

    # 素材内容漂移
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=50)
    page.insert_text((10, 25), "changed")
    doc.save(figs / "p1.pdf")
    doc.close()
    with open(zpath, "rb") as f:
        body = client.post(
            "/api/package/open",
            data={"package": (io.BytesIO(f.read()), "pk.zip")},
            content_type="multipart/form-data",
        ).get_json()
    assert body["drifted"] == ["p1.pdf"]

    # 素材缺失
    (figs / "p1.pdf").unlink()
    with open(zpath, "rb") as f:
        body = client.post(
            "/api/package/open",
            data={"package": (io.BytesIO(f.read()), "pk.zip")},
            content_type="multipart/form-data",
        ).get_json()
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
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 150, "h": 100},
                "objects": [{"id": "o1", "type": "panel", "fileId": "p1.pdf"}],
                "guides": [],
            },
            {
                "id": "c2",
                "name": "Fig 2",
                "page": {"w": 80, "h": 60},
                "objects": [{"id": "o2", "type": "panel", "fileId": "p2.pdf"}],
                "guides": [],
            },
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 0,
    }
    body = client.post("/api/package", json={"doc": pd}).get_json()
    assert body["assets"] == 2  # 两张画布的素材都进包
    zpath = figs / "tavottofile" / "export" / body["name"]
    with zipfile.ZipFile(zpath) as z:
        assert json.loads(z.read("layout.json"))["schema"] == 3
    with open(zpath, "rb") as f:
        opened = client.post(
            "/api/package/open",
            data={"package": (io.BytesIO(f.read()), "x.tavotto")},
            content_type="multipart/form-data",
        ).get_json()
    assert opened["doc"]["schema"] == 3
    assert opened["missing"] == []


def test_package_open_keys_off_structure_not_kind_or_extension(env):
    """检视只认 zip 里的结构，不认 `kind` 字符串、也不认扩展名。

    这条**不是**在承诺旧包的兼容（改名到 Tavotto 时选的是干净断裂，见
    `engine/brand.py`），而是钉住 `api_package_open` 的判据：改成按 `kind`
    白名单放行的话，同一份布局换个字段就打不开了，而用户看到的只是
    「不是有效的项目包」。这里拿一个改名前的包当输入，正因为它是最典型的
    「结构对、名字不对」的样本。
    """
    client, figs, tmp = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("layout.json", json.dumps(_layout()))
        z.writestr(
            "package_manifest.json",
            json.dumps(
                {
                    "kind": "magic-matplot-package",
                    "version": 1,
                    "assets": [{"id": "p1.pdf"}],
                    "scripts": [],
                }
            ),
        )
    buf.seek(0)
    body = client.post(
        "/api/package/open",
        data={"package": (buf, "old.mmpack.zip")},
        content_type="multipart/form-data",
    ).get_json()
    assert body["doc"]["schema"] == 2
    assert body["missing"] == []


def test_package_rejects_bad_zip(env):
    client, _, _ = env
    resp = client.post(
        "/api/package/open",
        data={"package": (io.BytesIO(b"not a zip"), "x.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_export_writes_proof_report(env):
    client, figs, tmp = env
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "stem": "pf",
            "objects": [],
            "proof": {"kind": "tavotto-proof", "checks": []},
        },
    )
    files = resp.get_json()["files"]
    proof = next(f for f in files if f["name"].endswith("_proof.json"))
    data = json.loads((figs / "tavottofile" / "export" / proof["name"]).read_text(encoding="utf-8"))
    assert data["kind"] == "tavotto-proof"
    assert data["files"]  # 成图文件名回填进 report


def _schema_package(schema) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("layout.json", json.dumps({**_layout(), "schema": schema}))
        z.writestr("package_manifest.json", json.dumps({"kind": "tavotto-package", "version": 1}))
    buf.seek(0)
    return buf


def test_package_from_a_newer_build_says_too_new_not_invalid(env):
    """更新的 Tavotto 打出的包（排版 schema 比本版本新）：回 `package_schema_too_new`，
    界面据此给「检查更新」，而不是「不是项目包」（#1921）。"""
    client, _, _ = env
    schema = documents.SCHEMA_CURRENT + 1
    resp = client.post(
        "/api/package/open",
        data={"package": (_schema_package(schema), "new.tavotto")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "package_schema_too_new"
    assert body["params"] == {"schema": schema}


def test_schema4_package_is_accepted_and_collects_panel_assets(env):
    """schema 4 与 schema 3 走同一个打包 / 检视入口：不是「无效的排版」。"""
    client, figs, _ = env
    pd = {
        "schema": documents.SCHEMA_CHART_PROJECT,
        "project": {"id": "p1", "name": "计算图表项目"},
        "canvases": [
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 150, "h": 100},
                "objects": [{"id": "o1", "type": "panel", "fileId": "p1.pdf"}],
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 0,
    }
    resp = client.post("/api/package", json={"doc": pd})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["assets"] == 1
    # 项目排版的默认名取 `project.name`（schema 3 / 4 同一支）
    assert body["name"].startswith("计算图表项目")
    zpath = figs / "tavottofile" / "export" / body["name"]
    with zipfile.ZipFile(zpath) as z:
        assert json.loads(z.read("layout.json"))["schema"] == documents.SCHEMA_CHART_PROJECT
    with open(zpath, "rb") as f:
        opened = client.post(
            "/api/package/open",
            data={"package": (io.BytesIO(f.read()), "x.tavotto")},
            content_type="multipart/form-data",
        ).get_json()
    assert opened == {"document_format": "v4"}
    inspected = _open_zip(client, zpath, "/api/package/inspect")
    assert inspected.status_code == 200, inspected.get_json()
    assert inspected.get_json()["doc"]["schema"] == documents.SCHEMA_CHART_PROJECT
    assert inspected.get_json()["missing"] == []


def _chart_project_document(ref):
    return {
        "schema": documents.SCHEMA_CHART_PROJECT,
        "project": {"id": "p", "name": "图表项目"},
        "canvases": [
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 150, "h": 100},
                "objects": [
                    {
                        "id": "o1",
                        "type": "panel",
                        "fileId": SOURCE_ID,
                        "fileKind": "svg",
                        "chart": chart_state(),
                        "chartSource": ref,
                    }
                ],
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 0,
    }


def _open_zip(client, zpath, endpoint="/api/package/open", **fields):
    with open(zpath, "rb") as f:
        return client.post(
            endpoint,
            data={"package": (io.BytesIO(f.read()), "chart.tavotto"), **fields},
            content_type="multipart/form-data",
        )


def test_schema4_package_carries_and_reinstalls_the_chart_source(env, monkeypatch):
    """Inspect leaves the receiver untouched; installation provides the exact source for autosave."""
    client, figs, tmp = env
    root = figs / "tavottofile"
    ref = documentassets.freeze_source(root, SOURCE_ID, SOURCE, scene_digest=SCENE_DIGEST)
    digest = ref["bytesSha256"]
    doc = _chart_project_document(ref)

    resp = client.post("/api/package", json={"doc": doc})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["chart_sources"] == 1
    assert body["missing"] == []

    zpath = root / "export" / body["name"]
    with zipfile.ZipFile(zpath) as z:
        assert z.read(f"assets/tavottofile/chart_sources/{digest}.svg") == SOURCE
        man = json.loads(z.read("package_manifest.json"))
        assert [row["sha256"] for row in man["assets"] if row["kind"] == "chart_source"] == [digest]

    outgoing = zpath.read_bytes()
    (root / "chart_sources" / f"{digest}.svg").unlink()
    inspection = _open_zip(client, zpath, "/api/package/inspect")
    assert inspection.get_json()["doc"]["canvases"][0]["objects"][0]["chartSource"] == ref
    destination = tmp / "received project"
    assert not destination.exists() and not (root / "chart_sources" / f"{digest}.svg").exists()

    # 导入后的第一次自动保存真的写得回去（来源就在目标项目的同一根下）。
    monkeypatch.setattr(
        m,
        "_render_chart_snapshot",
        lambda file_id, chart, **kwargs: SimpleNamespace(
            input_svg=SOURCE,
            scene={"scene_digest": SCENE_DIGEST},
            path=documentassets.source_path(
                kwargs.get("source_storage", destination / "tavottofile"), ref
            ),
            source_sha256=digest,
        ),
    )
    installed = _open_zip(
        client, zpath, "/api/package/install", options=json.dumps({"destination": str(destination)})
    )
    assert installed.status_code == 201, installed.get_json()
    assert zpath.read_bytes() == outgoing
    assert documentassets.source_path(destination / "tavottofile", ref).read_bytes() == SOURCE
    m.open_project(str(destination))
    saved = client.put("/api/autosave/d_imported", json=installed.get_json()["doc"])
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()["chart_sources"][0]["chart_source"] == ref


def test_schema4_package_refuses_a_chart_without_its_source(env):
    """没有冻结来源的图表正文打不成包：接收方第一次保存必然写不回去。"""
    client, figs, _ = env
    root = figs / "tavottofile"
    ref = documentassets.freeze_source(root, SOURCE_ID, SOURCE, scene_digest=SCENE_DIGEST)
    doc = _chart_project_document(ref)
    doc["canvases"][0]["objects"][0].pop("chartSource")
    resp = client.post("/api/package", json={"doc": doc})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "chart_model_invalid"


def test_legacy_chart_package_open_reports_a_tampered_chart_source(env):
    """包里的来源字节被改过：不安装、明确报缺失，而不是装一份假的进项目。"""
    client, figs, _ = env
    root = figs / "tavottofile"
    ref = documentassets.freeze_source(root, SOURCE_ID, SOURCE, scene_digest=SCENE_DIGEST)
    digest = ref["bytesSha256"]
    rewritten = io.BytesIO()
    with zipfile.ZipFile(rewritten, "w") as dst:
        dst.writestr("layout.json", json.dumps(_chart_project_document(ref)))
        dst.writestr("package_manifest.json", json.dumps({"kind": "tavotto-package", "version": 1}))
        dst.writestr(
            f"chart_sources/{digest}.svg", SOURCE.replace(b'stroke="blue"', b'stroke="red"')
        )
    rewritten.seek(0)

    (root / "chart_sources" / f"{digest}.svg").unlink()
    opened = client.post(
        "/api/package/open",
        data={"package": (rewritten, "tampered.tavotto")},
        content_type="multipart/form-data",
    ).get_json()
    assert opened["missing_chart_sources"] == [digest]
    assert not (root / "chart_sources" / f"{digest}.svg").exists()


@pytest.mark.parametrize("schema", [1, "4", None, True])
def test_package_with_a_bogus_schema_stays_unsupported(env, schema):
    client, _, _ = env
    resp = client.post(
        "/api/package/open",
        data={"package": (_schema_package(schema), "odd.tavotto")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "package_schema_unsupported"
