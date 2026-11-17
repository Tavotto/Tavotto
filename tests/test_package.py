"""可复现项目包：打包（布局+素材+清单）与检视（缺失/漂移对照）。"""

import io
import json
import zipfile

import pymupdf
import pytest

from tavotto import app as m


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
    resp = client.post(
        "/api/package/open",
        data={"package": (_schema_package(4), "new.tavotto")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "package_schema_too_new"
    assert body["params"] == {"schema": 4}


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


def _v4_package(*, document_format="v4", kind="tavotto-package", version=1) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("layout.json", json.dumps({"schema": 4}))
        z.writestr(
            "package_manifest.json",
            json.dumps({"kind": kind, "version": version, "document_format": document_format}),
        )
    buf.seek(0)
    return buf


def test_v4_package_is_identified_by_content_and_not_opened_as_a_layout(env):
    """同一个入口按包内容分流（#1921）：schema 4 + `document_format = "v4"` 只回答
    「这是计算图表包」，交给检视 / 安装，不按旧排版打开。"""
    client, _, _ = env
    resp = client.post(
        "/api/package/open",
        data={"package": (_v4_package(), "chart.tavotto")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {"document_format": "v4"}


@pytest.mark.parametrize(
    "envelope",
    [
        {"kind": "magic-matplot-package"},
        {"version": 2},
    ],
)
def test_v4_envelope_mismatch_is_not_installed(env, envelope):
    """`document_format = "v4"` 但 kind / version 不符：信封不成立，不能进入检视 / 安装。"""
    client, _, _ = env
    resp = client.post(
        "/api/package/open",
        data={"package": (_v4_package(**envelope), "odd.tavotto")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "package_invalid"
    assert body["params"] == {"reason": "unsupported_envelope"}


def test_schema_4_without_the_v4_document_format_is_too_new(env):
    """只有 schema 4 不够：没有 `document_format = "v4"` 时按 schema 太新处理，不进 v4 路径。"""
    client, _, _ = env
    resp = client.post(
        "/api/package/open",
        data={"package": (_v4_package(document_format="legacy"), "odd.tavotto")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "package_schema_too_new"
