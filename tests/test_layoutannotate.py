"""`engine/layoutannotate`（ADR 0208 第 2 期）：项目排版上的读场景 → 方案（复述）→ 写入 → 一步撤销。

量子图用假的 `measure`（手写 manifest，与 `test_vectorcheck.py` 同一张最小图）：这里验的是排版文件这一层——
落盘路径、基线、复述、服务端重解、只追加、`anchor` 落盘、撤销不盖别人的改动。真 worker 的对齐在第 1 期的端到端用例里。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import atomicio, layoutannotate as la

PANEL = {"x": 10.0, "y": 20.0, "w": 80.0, "h": 40.0}
MANIFEST = {
    "size_mm": [80, 40],
    "elements": [
        {"gid": "axes_0", "role": "axes", "bbox": [0.1, 0.1, 0.8, 0.8], "label": None},
        {
            "gid": "axes_0.lines_0",
            "role": "line",
            "label": "Treatment",
            "identity": "label:Treatment",
            "bbox": [0.1, 0.1, 0.8, 0.8],
            "geometry": {"paths": [{"points": [[0.1, 0.9], [0.9, 0.1]], "closed": False}]},
        },
        {
            "gid": "axes_0.bar_0",
            "role": "patch",
            "bbox": [0.7, 0.6, 0.1, 0.3],
            "geometry": {
                "paths": [
                    {"points": [[0.7, 0.6], [0.8, 0.6], [0.8, 0.9], [0.7, 0.9]], "closed": True}
                ]
            },
        },
    ],
}


def _doc(extra_objects=()):
    panel = {
        "id": "figures/fig1.pdf",
        "type": "panel",
        **PANEL,
        "fileId": "figures/fig1.pdf",
        "fileKind": "pdf",
        "nativeW": 80,
        "nativeH": 40,
        "overrides": [],
    }
    return {
        "schema": 3,
        "project": {"id": "p_test0001", "name": "t"},
        "canvases": [
            {
                "id": "c_main0001",
                "name": "图版 1",
                "page": {"w": 120, "h": 80},
                "objects": [panel, *extra_objects],
                "guides": [],
            }
        ],
        "activeCanvasId": "c_main0001",
        "createdAt": 0,
        "updatedAt": 0,
    }


@pytest.fixture
def project(tmp_path):
    store = tmp_path / "tavottofile"
    store.mkdir()
    atomicio.write_json(store / "组图.json", _doc(), indent=1)
    return tmp_path


calls: list = []


def measure(panel, requests):
    """数据点 (x, y) 按坐标轴框线性换算成图幅分数：x ∈ [0, 10]、y ∈ [0, 10]。"""
    calls.append((panel["id"], list(requests)))
    pts = []
    for q in requests:
        fx = 0.1 + 0.8 * q["x"] / 10
        fy = 0.9 - 0.8 * q["y"] / 10
        pts.append({"status": "ok", "xy": [fx, fy]})
    return {"draw_manifest": MANIFEST, "data_points": pts}


def unavailable(panel, requests):
    raise la.PanelUnavailable("needs_input")


def _items():
    return [
        {
            "kind": "arrow",
            "name": "peak",
            "from": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [30, 5]},
            "to": {
                "anchor": {"element": {"panel": "P1", "gid": "axes_0.lines_0", "point": "center"}}
            },
        },
        {
            "kind": "text",
            "name": "label",
            "text": "peak",
            "sizePt": 8,
            "at": {"anchor": {"item": {"name": "peak", "point": "nw"}}, "offset_mm": [0, -0.5]},
            "attach": "sw",
        },
    ]


def test_scene_lists_panels_and_gives_a_base_revision(project):
    s = la.read_scene(project, "组图", measure)
    assert s["panels_by_ref"] == {"P1": "figures/fig1.pdf"}
    assert s["base_revision"] == atomicio.content_revision(project / "tavottofile" / "组图.json")
    labels = {e.get("label") for p in s["scene"]["panels"] for e in p.get("elements", [])}
    assert "Treatment" in labels
    assert s["unmeasured"] == {}


def test_a_panel_that_cannot_be_measured_degrades_to_its_frame(project):
    s = la.read_scene(project, "组图", unavailable)
    assert s["unmeasured"] == {"P1": "needs_input"}
    # 元素锚点在这张图上解不出来：整批拒（不是崩）
    out = la.propose(project, "组图", _items(), unavailable, base_revision=s["base_revision"])
    err = out["errors"][0]
    assert not out["ok"] and err["code"] == "anchor_unresolvable"
    assert err["detail"] == {"panel": "P1", "reason": "needs_input"}


def test_echo_says_what_the_anchor_actually_landed_on(project):
    s = la.read_scene(project, "组图", measure)
    out = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    assert out["ok"], out
    arrow = next(e for e in out["echo"] if e["item"] == "peak")
    to = next(a for a in arrow["anchors"] if a["role"] == "to")
    assert to["label"] == "Treatment" and to["named"] is True
    assert "『Treatment』" in arrow["text"]
    assert arrow["intent"] == "describable"
    # 锚到没有名字的元素：复述只说得出 gid，意图标 unnamed
    items = _items()
    items[0]["to"]["anchor"]["element"]["gid"] = "axes_0.bar_0"
    out = la.propose(project, "组图", items, measure, base_revision=s["base_revision"])
    arrow = next(e for e in out["echo"] if e["item"] == "peak")
    assert arrow["intent"] == "unnamed" and "axes_0.bar_0" in arrow["text"]


def test_commit_appends_objects_with_anchor_and_undo_restores(project):
    path = project / "tavottofile" / "组图.json"
    original = path.read_bytes()
    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    out = la.commit(
        project,
        "组图",
        _items(),
        measure,
        base_revision=s["base_revision"],
        echo_digest_seen=plan["echo_digest"],
        fingerprint=s["scene"]["fingerprint"],
    )
    assert out["written"], out
    doc = json.loads(path.read_text(encoding="utf-8"))
    objs = doc["canvases"][0]["objects"]
    assert objs[0]["id"] == "figures/fig1.pdf"  # 原有对象原样在前
    added = [o for o in objs if o["id"] in out["added"]]
    assert [o["type"] for o in added] == ["arrow", "text"]
    arrow = added[0]
    assert arrow["anchor"]["by"] == "agent"
    to = next(a for a in arrow["anchor"]["anchors"] if a["role"] == "to")
    assert to["element"]["panel"] == "figures/fig1.pdf"  # 短号换成了子图对象 id
    assert to["identity"] == "label:Treatment"
    assert out["revision"] == atomicio.content_revision(path)
    back = la.undo(project, "组图", out["undo_token"])
    assert path.read_bytes() == original
    assert back["restored_to"] == s["base_revision"]
    # 一个 token 只用一次
    with pytest.raises(la.LayoutError) as e:
        la.undo(project, "组图", out["undo_token"])
    assert e.value.code == "undo_unknown"


def test_undo_refuses_after_someone_else_saved(project):
    path = project / "tavottofile" / "组图.json"
    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    out = la.commit(
        project,
        "组图",
        _items(),
        measure,
        base_revision=s["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["canvases"][0]["name"] = "桌面改过"
    atomicio.write_json(path, doc, indent=1)
    edited = path.read_bytes()
    with pytest.raises(la.LayoutError) as e:
        la.undo(project, "组图", out["undo_token"])
    assert e.value.code == "undo_stale"
    assert path.read_bytes() == edited  # 不替用户覆盖


def test_commit_refuses_a_stale_base_revision(project):
    path = project / "tavottofile" / "组图.json"
    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["canvases"][0]["name"] = "别处改过"
    atomicio.write_json(path, doc, indent=1)
    before = path.read_bytes()
    with pytest.raises(la.LayoutError) as e:
        la.commit(
            project,
            "组图",
            _items(),
            measure,
            base_revision=s["base_revision"],
            echo_digest_seen=plan["echo_digest"],
        )
    assert e.value.code == "layout_changed"
    assert path.read_bytes() == before


def test_commit_refuses_when_the_description_differs_from_what_was_echoed(project):
    """Agent 看过的复述与要写的不是同一份（改了描述却没重新出方案）：拒绝，不写。"""
    path = project / "tavottofile" / "组图.json"
    before = path.read_bytes()
    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    items = _items()
    items[0]["from"]["offset_mm"] = [32, 5]  # 复述不变（锚点没变），描述变了
    with pytest.raises(la.LayoutError) as e:
        la.commit(
            project,
            "组图",
            items,
            measure,
            base_revision=s["base_revision"],
            echo_digest_seen=plan["echo_digest"],
        )
    assert e.value.code == "echo_mismatch"
    assert path.read_bytes() == before


def test_failed_checks_are_not_written(project):
    path = project / "tavottofile" / "组图.json"
    before = path.read_bytes()
    s = la.read_scene(project, "组图", measure)
    items = [
        {
            "kind": "text",
            "name": "off",
            "text": "outside",
            "sizePt": 8,
            "at": {"anchor": {"panel": {"id": "P1", "point": "se"}}, "offset_mm": [60, 60]},
        }
    ]
    plan = la.propose(project, "组图", items, measure, base_revision=s["base_revision"])
    assert not plan["ok"] and any(f["check"] == "page" for f in plan["failed"])
    out = la.commit(
        project,
        "组图",
        items,
        measure,
        base_revision=s["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    assert out["written"] is False
    assert path.read_bytes() == before


def test_data_anchor_is_measured_through_the_injected_export(project):
    calls.clear()
    s = la.read_scene(project, "组图", measure)
    items = [
        {
            "kind": "arrow",
            "name": "a",
            "from": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [5, 5]},
            "to": {"anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 5, "y": 5}}},
        }
    ]
    plan = la.propose(project, "组图", items, measure, base_revision=s["base_revision"])
    assert plan["ok"], plan
    assert calls[-1] == ("figures/fig1.pdf", [{"axes": "axes_0", "x": 5, "y": 5}])
    to = next(a for a in plan["echo"][0]["anchors"] if a["role"] == "to")
    assert to["kind"] == "data" and to["measured"] is True


@pytest.mark.parametrize("name", ["../x", "a/b", "_styles", ""])
def test_layout_names_outside_the_store_are_refused(project, name):
    with pytest.raises(la.LayoutError) as e:
        la.read_scene(project, name, measure)
    assert e.value.code in ("layout_not_found", "layout_outside_project")


def test_symlinked_layout_file_is_refused(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "x.json"
    atomicio.write_json(outside, _doc(), indent=1)
    link = project / "tavottofile" / "链接.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("此平台不能建符号链接")
    with pytest.raises(la.LayoutError) as e:
        la.read_scene(project, "链接", measure)
    assert e.value.code == "layout_outside_project"


def test_stale_scene_fingerprint_is_refused(project):
    s = la.read_scene(project, "组图", measure)

    def moved(panel, requests):
        out = measure(panel, requests)
        man = json.loads(json.dumps(out["draw_manifest"]))
        man["elements"][1]["bbox"] = [0.2, 0.1, 0.7, 0.8]
        return {**out, "draw_manifest": man}

    plan = la.propose(
        project,
        "组图",
        _items(),
        moved,
        base_revision=s["base_revision"],
        fingerprint=s["scene"]["fingerprint"],
    )
    assert not plan["ok"] and plan["errors"][0]["code"] == "scene_stale"


def test_layout_listing_skips_reserved_files(project):
    atomicio.write_json(project / "tavottofile" / "_styles.json", {}, indent=1)
    assert la.list_layouts(project) == ["组图"]
    assert la.list_layouts(Path(project) / "nope") == []


def test_anchor_never_reaches_the_export_payload(project):
    """`anchor` 只给桌面重算用：导出载荷不带它（导出端不认识新东西，ADR 0208 §五）。"""
    from tavotto.engine import agentobjects

    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    assert all("anchor" in o for o in plan["objects"])
    assert all("anchor" not in e for e in agentobjects.to_export_objects(plan["objects"]))
