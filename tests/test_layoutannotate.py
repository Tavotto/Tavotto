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
            "label": "柱子 1",  # 界面显示名（脚本没起 label=，所以没有 identity）
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
    assert "Treatment" in arrow["text"] and "没有名字" not in arrow["text"]
    assert arrow["intent"] == "describable"
    # 锚到没有名字的元素：复述只说得出 gid，意图标 unnamed
    items = _items()
    items[0]["to"]["anchor"]["element"]["gid"] = "axes_0.bar_0"
    out = la.propose(project, "组图", items, measure, base_revision=s["base_revision"])
    arrow = next(e for e in out["echo"] if e["item"] == "peak")
    assert (
        arrow["intent"] == "unnamed"
        and "没有名字" in arrow["text"]
        and "axes_0.bar_0" in arrow["text"]
    )


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


def test_no_write_when_the_undo_copy_cannot_be_saved(project, monkeypatch):
    """撤销副本存不下（数据目录只读 / 磁盘满）：不写排版——不交出一次没有撤销的写入（Codex #2204）。"""
    path = project / "tavottofile" / "组图.json"
    before = path.read_bytes()
    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])

    def full(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(la, "_save_undo", full)
    with pytest.raises(la.LayoutError) as e:
        la.commit(
            project,
            "组图",
            _items(),
            measure,
            base_revision=s["base_revision"],
            echo_digest_seen=plan["echo_digest"],
        )
    assert e.value.code == "undo_unavailable"
    assert path.read_bytes() == before


def test_cleanup_of_old_undo_copies_never_fails_a_write(project, monkeypatch):
    """清理老副本失败（权限、被占用）只是少省点地方：这次写入照样成功、撤销照样能用。
    只留 1 份、连写两次：第二次写入时要清掉第一次的副本，而删除被拒。"""
    import pathlib

    monkeypatch.setattr(la, "UNDO_KEEP", 1)
    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    first = la.commit(
        project,
        "组图",
        _items(),
        measure,
        base_revision=s["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    items = [
        {**_items()[0], "name": "peak2", "from": {**_items()[0]["from"], "offset_mm": [10, 30]}}
    ]
    plan2 = la.propose(project, "组图", items, measure, base_revision=first["revision"])
    assert plan2["ok"], plan2
    real_unlink = pathlib.Path.unlink
    tried = []

    def stubborn(self, *a, **k):
        if "agent-undo" in str(self):
            tried.append(self.name)
            raise PermissionError(13, "in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "unlink", stubborn)
    out = la.commit(
        project,
        "组图",
        items,
        measure,
        base_revision=first["revision"],
        echo_digest_seen=plan2["echo_digest"],
    )
    monkeypatch.setattr(pathlib.Path, "unlink", real_unlink)
    assert tried, "清理确实跑了"
    assert out["written"]
    la.undo(project, "组图", out["undo_token"])


def test_new_text_may_not_cover_an_annotation_already_on_the_canvas(project):
    """同一批标注再写一次：新字压在上一次写下的字上——判压字，不写（Codex #2204）。"""
    s = la.read_scene(project, "组图", measure)
    plan = la.propose(project, "组图", _items(), measure, base_revision=s["base_revision"])
    first = la.commit(
        project,
        "组图",
        _items(),
        measure,
        base_revision=s["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    again = la.propose(project, "组图", _items(), measure, base_revision=first["revision"])
    clash = [f for f in again["failed"] if f["check"] == "text" and f["item"] == "label"]
    assert clash and any(w.startswith("canvas:") for w in clash[0]["detail"]["with"])
    assert not again["ok"]


def test_schema_4_projects_are_annotated_too(project):
    """schema 4（#1882 单一存储：项目 + 计算图表子图）画布结构与 3 相同，照样能读、能写；schema 2 不认。"""
    path = project / "tavottofile" / "组图.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema"] = 4
    atomicio.write_json(path, doc, indent=1)
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
    assert out["written"]
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 4
    doc["schema"] = 2
    atomicio.write_json(path, doc, indent=1)
    with pytest.raises(la.LayoutError) as e:
        la.read_scene(project, "组图", measure)
    assert e.value.code == "layout_format_unsupported"


def test_undo_store_is_keyed_by_the_real_project_path(project, tmp_path_factory):
    """同一个项目经符号链接别名访问（macOS 的 /var 与 /private/var 同理）：撤销留存落在同一个地方，从任何一种写法都能撤销。"""
    alias = tmp_path_factory.mktemp("alias") / "proj"
    try:
        alias.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip("此平台不能建符号链接")
    base = la.read_scene(alias, "组图", measure)["base_revision"]
    plan = la.propose(alias, "组图", _items(), measure, base_revision=base)
    out = la.commit(
        alias, "组图", _items(), measure, base_revision=base, echo_digest_seen=plan["echo_digest"]
    )
    assert la._undo_dir(alias, "组图") == la._undo_dir(project, "组图")
    la.undo(project, "组图", out["undo_token"])


def test_preview_is_a_real_composition_cropped_to_the_new_elements(project):
    """局部图走真合成：子图源是量它时导出的 PDF，新对象叠在上面，裁到这批对象周围。"""
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from PIL import Image

    pdf = project / "figures" / "fig1.pdf"
    pdf.parent.mkdir()
    fig = plt.figure(figsize=(80 / 25.4, 40 / 25.4))
    fig.patch.set_facecolor("#0000ff")  # 整张子图涂蓝：局部图里有蓝 = 子图源真的合成进来了
    fig.savefig(pdf)
    plt.close(fig)
    s = la.read_scene(project, "组图", measure)
    items = [
        {
            "kind": "arrow",
            "name": "red",
            "color": "#ff0000",
            "strokePt": 2,
            "from": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [20, 10]},
            "to": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [40, 10]},
        }
    ]
    plan = la.propose(project, "组图", items, measure, base_revision=s["base_revision"])
    assert plan["ok"], plan
    lay = la.open_layout(project, "组图")
    out = la.preview(lay.doc["canvases"][0], plan["objects"], {"figures/fig1.pdf": str(pdf)})
    assert out is not None and out["missing_panels"] == []
    x0, y0, x1, y1 = out["region_mm"]
    assert x0 <= 30 and x1 >= 50 and y0 <= 30 and y1 >= 30  # 箭头在画布 (30, 30)–(50, 30)
    img = np.asarray(Image.open(io.BytesIO(out["png"])).convert("RGB")).astype(int)
    red = (img[:, :, 0] > 200) & (img[:, :, 1] < 80) & (img[:, :, 2] < 80)
    blue = (img[:, :, 2] > 200) & (img[:, :, 0] < 80) & (img[:, :, 1] < 80)
    assert red.sum() > 50, "新加的箭头画进了局部图"
    assert blue.sum() > 10, "子图本身也在（不是只画了新对象）"
    # 没量到的子图不画、如实列出
    out = la.preview(lay.doc["canvases"][0], plan["objects"], {})
    assert out is not None and out["missing_panels"] == ["figures/fig1.pdf"]
