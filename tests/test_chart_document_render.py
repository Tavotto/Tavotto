"""已保存INPUT附件进入实际inspect/bind/compute/render/PNG与生产导出。"""

from __future__ import annotations

import base64
import copy
import json
from urllib.parse import quote

import pytest

from tavotto import app as m
from tavotto.engine import chartprofile, documentassets
from test_chart_documents_v4 import _get_named, _save_named, env as env
from test_chart_native_api import SOURCE, _assert_line, _bind, _color, _render, project as project
from test_chart_native_export import PT_MM, _assert_svg_color, _export, _original, _panel


def _reference(root, chart, data=SOURCE, source_id="chart.svg"):
    return documentassets.freeze_source(
        root / "tavottofile", source_id, data, scene_digest=chart["model"]["scene_digest"]
    )


def _ref_render(client, chart, reference, *, file="chart.svg", edits=()):
    response = client.post(
        "/api/engine/render",
        json={
            "id": file,
            "chart": chart,
            "patches": list(edits),
            "chart_source": reference,
            "inline_svg": True,
        },
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


@pytest.mark.parametrize("source_state", ["deleted", "drifted"])
def test_all_chart_entries_use_the_saved_input_when_original_is_unavailable(project, source_state):
    client, root = project
    chart = _bind(client)
    edits = [_color("#ff0000")]
    old = _render(client, chart, edits)
    reference = _reference(root, chart)
    expected = _ref_render(client, chart, reference, edits=edits)
    assert expected["svg"] == old["svg"] and expected["manifest"] == old["manifest"]
    assert expected["chart_revision"] != old["chart_revision"]
    original = root / "chart.svg"
    if source_state == "deleted":
        original.unlink()
    else:
        original.write_bytes(SOURCE.replace(b"Source keep", b"Different source"))
    inspected = client.get(
        "/api/svg/chart/inspect",
        query_string={"id": "chart.svg", "chart_source": json.dumps(reference)},
    )
    assert inspected.status_code == 200, inspected.get_json()
    assert inspected.json["scene_digest"] == chart["model"]["scene_digest"]
    computed = client.post(
        "/api/svg/chart/compute",
        json={
            "file": "chart.svg",
            **chart,
            "render_profile": chartprofile.NATIVE_PROFILE,
            "overrides": edits,
            "chart_source": reference,
        },
    )
    assert computed.status_code == 200, computed.get_json()
    assert computed.json["svg"] == expected["svg"]
    assert computed.json["render_revision"] == expected["chart_revision"]
    restored = _ref_render(client, chart, reference, edits=edits)
    _assert_line(restored, y=0.25, color="#ff0000")
    assert restored["manifest"] == expected["manifest"]
    assert restored["chart_revision"] == expected["chart_revision"]
    png = client.post(
        "/api/engine/preview_png",
        json={
            "id": "chart.svg",
            "chart": chart,
            "patches": edits,
            "chart_source": reference,
            "w": 400,
            "with_manifest": True,
        },
    )
    assert png.status_code == 200, png.get_json()
    assert base64.b64decode(png.json["png"]).startswith(b"\x89PNG\r\n\x1a\n")
    assert png.json["manifest"] == restored["manifest"]
    assert png.json["chart_revision"] == restored["chart_revision"]
    evidence = {"p1_user": 0, "p1_value": 0, "p2_user": 1, "p2_value": 1, "scale": "linear"}
    bound = client.post(
        "/api/svg/chart/bind",
        json={
            "file": "chart.svg",
            "chart_source": reference,
            "axes_id": "ax1",
            "rect": [10, 10, 80, 80],
            "axes_nodes": ["old_axis"],
            "x": evidence,
            "y": evidence,
            "series": [{"node": "curve", "kind": "line", "x_col": "x", "y_col": "y"}],
            "dataset_text": "x,y\n0,0.25\n1,0.25\n",
            "completeness": "self-contained",
        },
    )
    assert bound.status_code == 200, bound.get_json()
    assert bound.json["model"]["scene_digest"] == chart["model"]["scene_digest"]
    assert documentassets.load_source(root / "tavottofile", reference).input_svg == SOURCE
    assert (
        not original.exists()
        if source_state == "deleted"
        else b"Different source" in original.read_bytes()
    )


@pytest.mark.parametrize("damage", ["null", "missing", "tampered", "wrong_source"])
def test_bad_saved_input_never_falls_back_to_the_still_valid_original(project, monkeypatch, damage):
    client, root = project
    chart = _bind(client)
    reference = _reference(root, chart)
    path = documentassets.source_path(root / "tavottofile", reference)
    if damage == "null":
        reference = None
    elif damage == "missing":
        path.unlink()
    elif damage == "tampered":
        path.write_bytes(SOURCE.replace(b"Source keep", b"Tamper keep"))
    else:
        reference = {**reference, "sourceId": "another.svg"}
    monkeypatch.setattr(
        m, "_chart_scaffold_via_worker", lambda *a, **k: pytest.fail("invalid input executed")
    )
    response = client.post(
        "/api/engine/render",
        json={"id": "chart.svg", "chart": chart, "patches": [], "chart_source": reference},
    )
    assert response.status_code == 422, response.get_json()
    assert response.json["code"] in {"chart_model_invalid", "chart_source_mismatch"}
    assert (root / "chart.svg").read_bytes() == SOURCE


def test_production_v2_attachment_keeps_complete_input_and_is_verified_before_reexport(project):
    client, root = project
    chart = _bind(client)
    edits = [_color("#ff0000"), {"gid": "axes_0", "prop": "xlim", "value": [0, 2]}]
    produced = _export(client, original=_original(chart, edits), formats=("svg",))[
        "svg"
    ].read_bytes()
    reference = _reference(root, chart, produced, "portable.svg")
    (root / "chart.svg").unlink()
    options = {"id": "portable.svg", "chart_source": json.dumps(reference)}
    inspected = client.get("/api/svg/chart/inspect", query_string=options)
    assert inspected.status_code == 200, inspected.get_json()
    assert inspected.json["restored_render"]["overrides"] == edits
    with m.app.test_request_context():
        snapshot = m._render_chart_snapshot(
            "portable.svg",
            chart,
            render_profile=chartprofile.NATIVE_PROFILE,
            overrides=edits,
            chart_source=reference,
        )
    assert snapshot.input_svg == produced and snapshot.source_svg == SOURCE
    exported = _export(
        client,
        original=_original(
            chart, edits, figure_id="portable.svg", chart_source=copy.deepcopy(reference)
        ),
        formats=("svg",),
        name="saved-input",
    )["svg"].read_bytes()
    from tavotto import chartbundle

    restored = chartbundle.extract_chart(exported)
    assert restored is not None and restored.overrides == edits and restored.source_svg == SOURCE
    assert documentassets.load_source(root / "tavottofile", reference).input_svg == produced


@pytest.mark.parametrize("scope", ["annotations", "padding", "canvas"])
def test_saved_input_is_forwarded_through_each_composed_export_scope(project, scope):
    client, root = project
    chart = _bind(client)
    reference = _reference(root, chart)
    (root / "chart.svg").unlink()
    edits = [_color("#ff0000")]
    if scope == "canvas":
        outputs = _export(
            client,
            canvas={
                "page_w_mm": 100 * PT_MM,
                "page_h_mm": 100 * PT_MM,
                "objects": [{**_panel(chart, edits), "chart_source": reference}],
            },
            formats=("svg",),
        )
    else:
        changes = {"w_mm": 100 * PT_MM, "h_mm": 100 * PT_MM, "chart_source": reference}
        if scope == "padding":
            changes["pad_mm"] = [5 * PT_MM] * 4
        else:
            changes["objects"] = [
                {
                    "type": "text",
                    "text": "Saved input annotation",
                    "x_mm": 1,
                    "y_mm": 1,
                    "w_mm": 20,
                    "h_mm": 4,
                    "size_pt": 6,
                }
            ]
        outputs = _export(client, original=_original(chart, edits, **changes), formats=("svg",))
    _assert_svg_color(outputs["svg"].read_bytes(), "#ff0000")
    assert not (root / "chart.svg").exists()


def test_one_job_distinguishes_two_input_revisions_of_the_same_logical_source(project):
    client, root = project
    chart = _bind(client)
    refs = []
    for index, color in enumerate(("#ff0000", "#00ff00")):
        raw = _export(
            client,
            original=_original(chart, [_color(color)]),
            formats=("svg",),
            name=f"input-{index}",
        )["svg"].read_bytes()
        refs.append(_reference(root, chart, raw))
    assert refs[0]["bytesSha256"] != refs[1]["bytesSha256"]
    (root / "chart.svg").unlink()
    edits = [_color("#0000ff")]
    variants = [_ref_render(client, chart, ref, edits=edits) for ref in refs]
    assert variants[0]["svg"] == variants[1]["svg"]
    assert variants[0]["manifest"] == variants[1]["manifest"]
    assert variants[0]["chart_revision"] != variants[1]["chart_revision"]
    pages = [_original(chart, edits, chart_source=ref) for ref in refs]
    _export(client, pages=pages, formats=("pdf",), name="two-saved-inputs")
    # A still valid first page must not lend its cached source to damaged B.
    damaged = documentassets.source_path(root / "tavottofile", refs[1])
    damaged.write_bytes(damaged.read_bytes() + b" ")
    response = client.post(
        "/api/export",
        json={
            "scope": "original",
            "formats": ["pdf"],
            "filename": "bad-second-input",
            "overwrite": "replace",
            "merge_pdf": {"pages": pages},
        },
    )
    assert response.status_code == 500, response.get_json()
    body = response.get_json()
    assert body["status"] == "failed", body
    assert body["outputs"][0]["error"]["code"] == "chart_source_mismatch", body
    assert body["outputs"][0]["name"] is None


@pytest.mark.parametrize("endpoint", ["/api/engine/render", "/api/engine/preview_png"])
def test_source_without_chart_cannot_enter_the_legacy_execution_path(
    project, monkeypatch, endpoint
):
    client, _root = project
    monkeypatch.setattr(m, "_engine_worker", lambda *a, **k: pytest.fail("entered legacy worker"))
    response = client.post(endpoint, json={"id": "not-a-chart.py", "chart_source": None})
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "chart_model_invalid"


def _frame_document(chart, edits, *, frame: bool):
    """v4 文档；frame=True 时面板把 /2 的 chartFrame（渲染外框落位）持久化在文档对象上。"""
    panel = {
        "id": "panel_frame",
        "type": "panel",
        "fileId": "chart.svg",
        "fileKind": "svg",
        "nativeW": 100 * PT_MM,
        "nativeH": 100 * PT_MM,
        "x": 5,
        "y": 7,
        "w": 100 * PT_MM,
        "h": 100 * PT_MM,
        "overrides": list(edits),
        "chart": copy.deepcopy(chart),
    }
    if frame:
        panel["chartFrame"] = {
            "version": 1,
            "renderProfile": "native-manifest/2",
            "originPt": [0, 0],
        }
    return {
        "schema": 4,
        "project": {"id": "p_frame", "name": "计算框"},
        "canvases": [
            {
                "id": "c1",
                "name": "Figure 1",
                "page": {"w": 200, "h": 160},
                "objects": [panel],
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 1,
        "updatedAt": 2,
    }


def test_v2_panel_survives_save_and_cold_replay_pairs_with_the_direct_render(env):
    """保存 /2 面板（profile 持久化在文档对象上）→ 冻结链照显式 profile 重放；重开冷重放与直接渲染配对。

    旧文档缺 chartFrame 仍走 /1；畸形的 profile 交 validate_profile 判定、保存 422。
    """
    client, _root, _data = env
    chart = _bind(client)
    edits = [_color("#ff0000")]
    body = {
        "id": "chart.svg",
        "chart": chart,
        "patches": edits,
        "render_profile": chartprofile.COMPLETE_PROFILE,
        "inline_svg": True,
    }
    direct = client.post("/api/engine/render", json=body)
    assert direct.status_code == 200, direct.get_json()
    direct = direct.get_json()
    frame = direct["render_frame"]
    assert frame["rect_pt"][2:] == list(direct["manifest"]["render_frame"]["rect_pt"][2:])

    _save_named(client, _frame_document(chart, edits, frame=True), "Frame")
    _stored, stored = _get_named(client, "Frame")
    panel = stored["canvases"][0]["objects"][0]
    # chartFrame 原样过冻结链（profile 持久化在文档对象上），figureSnapshot 照旧不进文档
    assert panel["chartFrame"] == {
        "version": 1,
        "renderProfile": "native-manifest/2",
        "originPt": [0, 0],
    }
    assert "figureSnapshot" not in panel
    replayed = client.post(
        "/api/engine/render", json={**body, "chart_source": panel["chartSource"]}
    )
    assert replayed.status_code == 200, replayed.get_json()
    replayed = replayed.get_json()
    assert replayed["svg"] == direct["svg"]
    assert replayed["manifest"] == direct["manifest"]
    assert replayed["render_frame"] == frame
    # 带冻结来源的重放身份绑 input sha（与 /1 的保存输入配对同一条规则），与直接渲染必然不同
    assert replayed["chart_revision"] != direct["chart_revision"]

    # 旧文档缺 chartFrame：冷重放维持 /1（无外框声明）
    _save_named(client, _frame_document(chart, edits, frame=False), "LegacyPanel")
    _stored, legacy = _get_named(client, "LegacyPanel")
    assert "chartFrame" not in legacy["canvases"][0]["objects"][0]
    one = client.post(
        "/api/engine/render",
        json={
            "id": "chart.svg",
            "chart": chart,
            "patches": edits,
            "chart_source": legacy["canvases"][0]["objects"][0]["chartSource"],
            "inline_svg": True,
        },
    )
    assert one.status_code == 200, one.get_json()
    one = one.get_json()
    assert one.get("render_frame") is None
    assert one["manifest"].get("render_frame") is None

    # 畸形 profile：显式值交 validate_profile，保存当场拒绝
    bad = _frame_document(chart, edits, frame=True)
    bad["canvases"][0]["objects"][0]["chartFrame"]["renderProfile"] = "native-manifest/9"
    response = client.post(
        f"/api/documents/v4/layouts/{quote('BadFrame')}?target=project&base_revision=absent",
        json=bad,
    )
    assert response.status_code == 422, response.get_json()
