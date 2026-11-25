"""可复现项目包：打包（布局+素材+清单）与检视（缺失/漂移对照）。"""

import copy
import hashlib
import io
import json
import lzma
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


def test_package_keeps_historical_static_pair_after_pdf_replacement(env):
    from tavotto.engine import patchspec, svgstore
    from test_svgstore import _args

    client, figs, _ = env
    source = "p1.pdf"
    original = (figs / source).read_bytes()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"/>'
    patch = patchspec.patch_hash([])
    root = figs / "tavottofile" / "figure_snapshots"
    svgstore.save(
        figs,
        source,
        patch,
        **_args(source, patch, pdf=original, svg=svg),
        static_sha256=hashlib.sha256(original).hexdigest(),
        storage_root=root,
    )
    saved = client.post("/api/layouts/history?target=project", json=_layout())
    assert saved.status_code == 200, saved.get_json()
    ref = saved.get_json()["figure_snapshots"]["o1"]
    layout = client.get("/api/layouts/history").get_json()
    (figs / source).write_bytes(b"replacement PDF bytes")
    response = client.post("/api/package", json={"doc": layout, "stem": "history"})
    assert response.status_code == 200, response.get_json()
    with zipfile.ZipFile(figs / "tavottofile" / "export" / response.get_json()["name"]) as archive:
        assert json.loads(archive.read("package_manifest.json"))["figure_snapshots"] == 1
        assert json.loads(archive.read("layout.json"))["objects"][0]["figureSnapshot"] == ref
        assert archive.read("assets/p1.pdf") == b"replacement PDF bytes"
        prefix = "assets/tavottofile/figure_snapshots/"
        payload = json.loads(archive.read(prefix + "snapshots/" + ref["snapshotId"] + ".json"))[
            "payload"
        ]
        assert payload["identity"]["static_sha256"] == hashlib.sha256(original).hexdigest()
        blobs = [
            archive.read(name) for name in archive.namelist() if name.startswith(prefix + "blobs/")
        ]
        assert original in blobs and svg in blobs


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


@pytest.mark.parametrize("schema", [documents.SCHEMA_PROJECT, documents.SCHEMA_CHART_PROJECT])
def test_package_can_select_one_canvas_before_collecting_assets(env, schema):
    client, figs, tmp = env
    shared_pdf = (figs / "p1.pdf").read_bytes()
    (figs / "private.pdf").write_bytes(shared_pdf)
    private = {**_layout("private.pdf"), "id": "private", "name": "private canvas"}
    shared = {**_layout(), "id": "shared", "name": "shared canvas"}
    private["objects"].append({**shared["objects"][0], "id": "shared-copy"})
    doc = {
        "schema": schema,
        "project": {"id": "p1", "name": "Two canvases"},
        "canvases": [private, shared],
        "activeCanvasId": "private",
    }
    response = client.post("/api/package", json={"doc": doc, "canvas_id": "shared"})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["assets"] == 1
    assert body["document_format"] == "v4"
    package = figs / "tavottofile" / "export" / body["name"]
    expected_document = {
        **doc,
        "schema": documents.SCHEMA_CHART_PROJECT,
        "canvases": [shared],
        "activeCanvasId": "shared",
    }
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {"layout.json", "package_manifest.json", "assets/p1.pdf"}
        assert archive.read("assets/p1.pdf") == shared_pdf
        assert json.loads(archive.read("layout.json")) == expected_document
        manifest = json.loads(archive.read("package_manifest.json"))
        assert "figures_dir" not in manifest
        assert [entry["id"] for entry in manifest["assets"]] == ["p1.pdf"]
    (figs / "p1.pdf").unlink()
    destination = tmp / "received canvas"
    response = _open_zip(
        client,
        package,
        "/api/package/install",
        options=json.dumps({"destination": str(destination)}),
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["doc"] == expected_document
    assert (destination / "p1.pdf").read_bytes() == shared_pdf
    assert not (destination / "private.pdf").exists()


@pytest.mark.parametrize(
    ("schema", "active", "expected_active"),
    [
        (documents.SCHEMA_PROJECT, "private", "shared"),
        (documents.SCHEMA_CHART_PROJECT, "detail", "detail"),
    ],
)
def test_package_selected_canvases_share_resources_and_keep_document_order(
    env, schema, active, expected_active
):
    client, figs, tmp = env
    for file in ("private.pdf", "detail.pdf"):
        (figs / file).write_bytes((figs / "p1.pdf").read_bytes())
    private = {**_layout("private.pdf"), "id": "private", "name": "Private"}
    shared = {**_layout(), "id": "shared", "name": "Shared"}
    detail = {**_layout("detail.pdf"), "id": "detail", "name": "Detail"}
    detail["objects"].append({**shared["objects"][0], "id": "shared-copy"})
    doc = {
        "schema": schema,
        "project": {"id": "p1", "name": "Three canvases"},
        "canvases": [private, shared, detail],
        "activeCanvasId": active,
    }
    response = client.post("/api/package", json={"doc": doc, "canvas_ids": ["detail", "shared"]})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["assets"] == 2
    assert body["document_format"] == "v4"
    package = figs / "tavottofile" / "export" / body["name"]
    expected = {
        **doc,
        "schema": documents.SCHEMA_CHART_PROJECT,
        "canvases": [shared, detail],
        "activeCanvasId": expected_active,
    }
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {
            "layout.json",
            "package_manifest.json",
            "assets/p1.pdf",
            "assets/detail.pdf",
        }
        assert json.loads(archive.read("layout.json")) == expected

    destination = tmp / "received canvases"
    installed = _open_zip(
        client,
        package,
        "/api/package/install",
        options=json.dumps({"destination": str(destination)}),
    )
    assert installed.status_code == 201, installed.get_json()
    assert installed.get_json()["doc"] == expected
    assert (destination / "p1.pdf").read_bytes() == (figs / "p1.pdf").read_bytes()
    assert (destination / "detail.pdf").read_bytes() == (figs / "detail.pdf").read_bytes()
    assert not (destination / "private.pdf").exists()


@pytest.mark.parametrize(
    ("selection", "ids"),
    [
        ({"canvas_id": None}, ["c1"]),
        ({"canvas_id": "missing"}, ["c1"]),
        ({"canvas_id": "c1"}, ["c1", "c1"]),
        ({"canvas_ids": []}, ["c1"]),
        ({"canvas_ids": "c1"}, ["c1"]),
        ({"canvas_ids": ["c1", []]}, ["c1"]),
        ({"canvas_ids": ["c1", "c1"]}, ["c1"]),
        ({"canvas_ids": ["c1", "unknown"]}, ["c1"]),
        ({"canvas_ids": ["c1", "unknown"]}, ["c1", "c1"]),
        ({"canvas_id": "c1", "canvas_ids": ["c1"]}, ["c1"]),
    ],
)
def test_package_cannot_fall_back_to_all_canvases_on_bad_selection(env, selection, ids):
    client, figs, _ = env
    doc = {
        "schema": documents.SCHEMA_PROJECT,
        "canvases": [{**_layout(), "id": canvas} for canvas in ids],
        "activeCanvasId": "c1",
    }
    response = client.post("/api/package", json={"doc": doc, **selection})
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_document"
    assert not list((figs / "tavottofile" / "export").glob("*.tavotto"))


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


@pytest.mark.parametrize("bad", ["size", "second_size", "source_id", "crc"])
def test_package_checks_every_chart_reference_and_damaged_member(env, bad):
    from test_chart_document_assets import reference

    client, figs, _ = env
    ref = reference()
    doc = _chart_project_document(ref)
    panels = doc["canvases"][0]["objects"]
    if bad == "second_size":
        panels.append(copy.deepcopy(panels[0]))
        panels[-1]["id"] = "o2"
    if bad in ("size", "second_size"):
        panels[-1]["chartSource"]["sizeBytes"] += 1
    elif bad == "source_id":
        panels[0]["chartSource"]["sourceId"] = "other.svg"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("layout.json", json.dumps(doc))
        archive.writestr("package_manifest.json", json.dumps({"assets": []}))
        archive.writestr(f"chart_sources/{ref['bytesSha256']}.svg", SOURCE)
    raw = buf.getvalue()
    if bad == "crc":
        raw = raw.replace(SOURCE, SOURCE.replace(b"blue", b"pink"))
    response = client.post(
        "/api/package/open", data={"package": (io.BytesIO(raw), "broken.tavotto")}
    )
    if bad == "source_id":
        assert response.status_code == 400
    else:
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["missing_chart_sources"] == [ref["bytesSha256"]]
        assert response.get_json()["verified_chart_sources"] == []
    assert not documentassets.source_path(figs / "tavottofile", ref).exists()


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


def test_package_open_reports_damaged_lzma_chart_member(env):
    from test_chart_document_assets import reference

    client, figs, _ = env
    ref = reference()
    member = f"chart_sources/{ref['bytesSha256']}.svg"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("layout.json", json.dumps(_chart_project_document(ref)))
        archive.writestr("package_manifest.json", json.dumps({"assets": []}))
        archive.writestr(member, SOURCE, compress_type=zipfile.ZIP_LZMA)
        info = archive.getinfo(member)
    raw = bytearray(buf.getvalue())
    # ZIP LZMA 的四字节属性头之后是 lc/lp/pb；255 让解码器明确抛 LZMAError。
    raw[info.header_offset + 30 + len(member.encode("utf-8")) + 4] = 255
    with zipfile.ZipFile(io.BytesIO(raw)) as damaged:
        assert json.loads(damaged.read("layout.json"))["schema"] == 4
        assert json.loads(damaged.read("package_manifest.json")) == {"assets": []}
        with pytest.raises(lzma.LZMAError):
            damaged.read(member)
    response = client.post(
        "/api/package/open", data={"package": (io.BytesIO(raw), "broken.tavotto")}
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["missing_chart_sources"] == [ref["bytesSha256"]]
    assert response.get_json()["verified_chart_sources"] == []
    assert not documentassets.source_path(figs / "tavottofile", ref).exists()
