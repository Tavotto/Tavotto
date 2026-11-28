"""插件会话绑定排版子图（ADR 0217 第二、三节）：自动绑 / 歧义 / 显式、桌面改了下次调用先读、子图没了就解绑、
锁住的元素不改、落盘记录恢复时重新核。

假 worker（`test_mcp_suggest.SuggestWorker`）：渲染不用 matplotlib；排版文件按 `layoutannotate` 读的形状手写。
写回见 `test_mcp_layout_writeback.py`（第四节）。
"""

# ruff: noqa: F811  — 夹具从别的测试模块导入后在用例形参里同名使用（pytest 的写法）
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp"))

from tavotto_mcp import bridge  # noqa: E402
from test_mcp_server import (  # noqa: E402, F401
    _body,
    _call,
    _clean_sessions,
    project,
)
from test_mcp_suggest import worker  # noqa: E402, F401

FONT = {"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}
DIRECTION = {"gid": "axes_0.xticks", "prop": "direction", "value": "out", "identity": "tick-id"}
SPINE = {"gid": "axes_0", "prop": "spine_top", "value": False}


def _panel(pid="o_panel001", overrides=None, **extra):
    return {
        "id": pid,
        "type": "panel",
        "x": 10.0,
        "y": 10.0,
        "w": 80.0,
        "h": 60.0,
        "fileId": "Fig1.pdf",
        "fileKind": "pdf",
        "nativeW": 80,
        "nativeH": 60,
        "overrides": overrides or [],
        **extra,
    }


def _write_layout(project_dir: Path, name: str, panels: list, canvases: list | None = None) -> Path:
    doc = {
        "schema": 3,
        "project": {"id": "p_test0001", "name": "t"},
        "canvases": canvases
        or [
            {
                "id": "c_main0001",
                "name": "图版 1",
                "page": {"w": 180, "h": 90},
                "objects": panels,
                "guides": [],
            }
        ],
        "activeCanvasId": "c_main0001",
        "createdAt": 0,
        "updatedAt": 0,
    }
    store = project_dir / "tavottofile"
    store.mkdir(exist_ok=True)
    path = store / f"{name}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _open(project, **kw) -> dict:
    return bridge.open_figure(str(project), **kw)


def _state(sid: str) -> dict:
    res = _call("tavotto_session_state", {"session_id": sid})
    assert not res.get("isError"), _body(res)
    return _body(res)


def test_unique_panel_is_bound_automatically_and_seeds_the_patches(project, worker):
    _write_layout(project, "组图", [_panel(overrides=[FONT, DIRECTION])])
    out = _open(project)
    assert out["binding"]["state"] == "bound"
    assert out["binding"]["layout"] == "组图" and out["binding"]["panel_id"] == "o_panel001"
    assert out["binding"]["ref"] == "P1"
    assert "ovr" not in out["binding"]
    patches = bridge.get_session(out["session_id"]).patches
    assert {(p["gid"], p["prop"]) for p in patches} == {
        ("axes_0.xticks", "fontsize"),
        ("axes_0.xticks", "direction"),
    }
    # identity 原样保留
    assert next(p for p in patches if p["prop"] == "direction")["identity"] == "tick-id"
    assert "draft_superseded" not in out


def test_figure_in_several_panels_is_ambiguous_and_not_bound(project, worker):
    _write_layout(project, "组图", [_panel("o_a"), _panel("o_b")])
    _write_layout(project, "另一份", [_panel("o_c")])
    out = _open(project)
    assert out["binding"]["state"] == "ambiguous"
    assert out["binding"]["count"] == 3
    assert {c["panel_id"] for c in out["binding"]["candidates"]} == {"o_a", "o_b", "o_c"}
    assert bridge.get_session(out["session_id"]).binding is None
    assert bridge.get_session(out["session_id"]).patches == []


def test_candidates_are_bounded(project, worker):
    _write_layout(project, "组图", [_panel(f"o_{i}") for i in range(12)])
    out = _open(project)
    assert out["binding"]["count"] == 12
    assert len(out["binding"]["candidates"]) == bridge.BINDING_CANDIDATE_LIMIT


def test_no_layout_means_state_none(project, worker):
    out = _open(project)
    assert out["binding"] == {"state": "none"}
    _write_layout(project, "别的图", [_panel(fileId="Other.pdf")])
    bridge.close_session(out["session_id"])
    assert _open(project)["binding"] == {"state": "none"}


def test_explicit_layout_and_panel_resolve_the_ambiguity(project, worker):
    _write_layout(project, "组图", [_panel("o_a", [FONT]), _panel("o_b", [SPINE])])
    by_id = _open(project, layout="组图", panel="o_b")
    assert by_id["binding"]["panel_id"] == "o_b"
    assert bridge.get_session(by_id["session_id"]).patches == [SPINE]
    # 短号 P1 = 画布上第一个子图（与 tavotto_layout_scene 同一套）
    by_ref = _open(project, layout="组图", panel="P1")
    assert by_ref["binding"]["panel_id"] == "o_a"
    assert bridge.get_session(by_ref["session_id"]).patches == [FONT]


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"layout": "组图", "panel": "o_nope"}, "panel_not_found"),
        ({"layout": "组图", "panel": "o_other"}, "other_figure"),
        ({"layout": "组图", "panel": "o_noscript"}, "no_script"),
        ({"layout": "组图", "panel": "o_snap"}, "composed_from_snapshot"),
        ({"layout": "只有别的图"}, "figure_not_in_layout"),
    ],
)
def test_explicit_mismatch_is_a_bridge_error(project, worker, kw, reason):
    registry = json.loads((project / "tavotto_registry.json").read_text(encoding="utf-8"))
    registry["scripts"]["other.py"] = {"entry": "main", "cost": "light", "stems": ["Other"]}
    (project / "tavotto_registry.json").write_text(json.dumps(registry), encoding="utf-8")
    (project / "other.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (project / "Other.pdf").write_bytes(b"%PDF-1.4\n")
    _write_layout(
        project,
        "组图",
        [
            _panel("o_a"),
            _panel("o_other", fileId="Other.pdf"),
            _panel("o_noscript", fileId="Nowhere.pdf"),
            _panel("o_snap", figureSnapshot={"svg": "<svg/>"}),
        ],
    )
    _write_layout(project, "只有别的图", [_panel("o_x", fileId="Other.pdf")])
    with pytest.raises(bridge.BridgeError) as exc:
        _open(project, stem="Fig1", **kw)
    assert exc.value.code == bridge.LAYOUT_BINDING_MISMATCH
    assert exc.value.extra["reason"] == reason
    res = _call("tavotto_open_figure", {"project_path": str(project), "stem": "Fig1", **kw})
    assert res["isError"] and _body(res)["code"] == bridge.LAYOUT_BINDING_MISMATCH


def test_unknown_layout_is_the_layouts_own_error(project, worker):
    with pytest.raises(bridge.BridgeError) as exc:
        _open(project, layout="没有这份")
    assert exc.value.code == "layout_not_found"


def test_snapshot_and_runtime_panels_are_never_bound(project, worker):
    _write_layout(
        project,
        "组图",
        [
            _panel("o_snap", figureSnapshot={"svg": "<svg/>"}),
            _panel("o_origin", figureOrigin={"x": 1}),
            _panel("o_rt", fileKind="runtime"),
        ],
    )
    assert _open(project)["binding"] == {"state": "none"}


def test_draft_is_superseded_by_the_panel_and_kept_on_disk(project, worker):
    sid = _open(project)["session_id"]
    bridge.apply_overrides(sid, [SPINE])
    bridge.close_session(sid)  # 没导出的修改存成草稿
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    out = _open(project)
    assert out["draft_superseded"] is True
    assert "restored_draft" not in out
    assert bridge.get_session(out["session_id"]).patches == [FONT]
    drafts = bridge.sessionjournal.find_drafts(bridge._drafts_dir(), out["project"], "Fig1", None)
    assert len(drafts) == 1 and drafts[0]["patches"] == [SPINE]


def test_identical_draft_is_restored_normally(project, worker):
    sid = _open(project)["session_id"]
    bridge.apply_overrides(sid, [FONT])
    bridge.close_session(sid)
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    out = _open(project)
    assert "draft_superseded" not in out and out["restored_draft"]["patches"] == 1
    assert out["binding"]["state"] == "bound"


def test_reused_untouched_session_is_seeded_from_the_panel(project, worker):
    first = _open(project)
    assert first["binding"] == {"state": "none"}
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    out = _open(project)
    assert out["session_id"] == first["session_id"] and out["reused"] is True
    assert out["binding"]["state"] == "bound" and "draft_superseded" not in out
    assert bridge.get_session(out["session_id"]).patches == [FONT]


def test_desktop_rewrite_is_read_before_the_next_call(project, worker):
    path = _write_layout(project, "组图", [_panel(overrides=[FONT])])
    sid = _open(project)["session_id"]
    renders = len([c for c in worker.calls if c[0] == "override"])
    # 没变：不重渲染、没有 layout_sync
    assert "layout_sync" not in _state(sid)
    assert len([c for c in worker.calls if c[0] == "override"]) == renders
    # 桌面改了：换成别的修改
    _write_layout(project, "组图", [_panel(overrides=[SPINE])])
    assert path.exists()
    out = _state(sid)
    assert out["layout_sync"]["reason"] == "desktop_changed"
    changed = {(c["gid"], c["prop"]) for c in out["layout_sync"]["changed"]}
    assert changed == {("axes_0.xticks", "fontsize"), ("axes_0", "spine_top")}
    assert bridge.get_session(sid).patches == [SPINE]
    assert [c for c in worker.calls if c[0] == "override"][-1][2] == [SPINE]
    # 同一份修订号再调：不再报
    assert "layout_sync" not in _state(sid)


def test_desktop_edit_elsewhere_keeps_the_session_untouched(project, worker):
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    sid = _open(project)["session_id"]
    bridge.apply_overrides(sid, [FONT, SPINE])  # 绑定会话写回排版（ADR 0217 §四）
    _write_layout(project, "组图", [_panel(overrides=[FONT, SPINE], x=30.0)])  # 桌面只挪了子图
    out = _state(sid)
    assert "layout_sync" not in out
    assert bridge.get_session(sid).patches == [FONT, SPINE]


def test_changed_list_is_bounded(project, worker):
    _write_layout(project, "组图", [_panel()])
    sid = _open(project)["session_id"]
    many = [{"gid": "axes_0.xticks", "prop": f"p{i}", "value": i} for i in range(20)]
    _write_layout(project, "组图", [_panel(overrides=many)])
    out = _state(sid)["layout_sync"]
    assert out["changed_count"] == 20
    assert len(out["changed"]) == bridge.LAYOUT_SYNC_CHANGED_LIMIT and out["changed_truncated"]


def test_deleted_panel_unbinds_and_the_session_carries_on(project, worker):
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    sid = _open(project)["session_id"]
    _write_layout(project, "组图", [_panel("o_other_id", overrides=[FONT])])
    out = _state(sid)
    assert out["binding"]["state"] == "lost" and out["binding"]["reason"] == "panel_gone"
    assert bridge.get_session(sid).binding is None
    # 之后照没绑定的会话用：不再带 binding，修改留在会话里
    assert "binding" not in _state(sid)
    assert bridge.get_session(sid).patches == [FONT]


def test_deleted_layout_unbinds(project, worker):
    path = _write_layout(project, "组图", [_panel(overrides=[FONT])])
    sid = _open(project)["session_id"]
    path.unlink()
    out = _state(sid)
    assert out["binding"]["state"] == "lost" and out["binding"]["reason"] == "layout_gone"


def test_panel_turned_into_a_snapshot_unbinds(project, worker):
    _write_layout(project, "组图", [_panel()])
    sid = _open(project)["session_id"]
    _write_layout(project, "组图", [_panel(figureSnapshot={"svg": "<svg/>"})])
    assert _state(sid)["binding"]["reason"] == "composed_from_snapshot"


def test_locked_gid_entries_go_to_rejected(project, worker):
    _write_layout(project, "组图", [_panel(overrides=[FONT], lockedGids=["axes_0.xticks"])])
    sid = _open(project)["session_id"]
    res = _call(
        "tavotto_apply_overrides",
        {
            "session_id": sid,
            "patches": [{**FONT, "value": 9.0}, DIRECTION, SPINE],
        },
    )
    body = _body(res)
    locked = [r for r in body["rejected"] if r["reason"] == "locked"]
    assert {(r["entry"]["gid"], r["entry"]["prop"]) for r in locked} == {
        ("axes_0.xticks", "fontsize"),
        ("axes_0.xticks", "direction"),
    }
    patches = bridge.get_session(sid).patches
    # 锁住元素原有的修改原样留着，没被改也没被抹；没锁的照常改
    assert FONT in patches and SPINE in patches and len(patches) == 2


def test_locked_gid_resending_the_same_value_is_not_a_rejection(project, worker):
    _write_layout(project, "组图", [_panel(overrides=[FONT], lockedGids=["axes_0.xticks"])])
    sid = _open(project)["session_id"]
    body = _body(_call("tavotto_apply_overrides", {"session_id": sid, "patches": [FONT]}))
    assert body["rejected"] == []


def test_unbound_sessions_ignore_locks(project, worker):
    sid = _open(project)["session_id"]
    body = _body(_call("tavotto_apply_overrides", {"session_id": sid, "patches": [FONT]}))
    assert body["rejected"] == []


def test_binding_is_persisted_and_revalidated_on_restore(project, worker):
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    sid = _open(project)["session_id"]
    record = bridge.sessionjournal.load(bridge._journal_dir(), sid)
    assert record["binding"]["panel_id"] == "o_panel001"
    # 进程换了：内存里没有了，记录还在
    bridge.sessions().clear()
    out = _state(sid)
    assert out["restored"] is True and "binding" not in out
    assert bridge.get_session(sid).binding["panel_id"] == "o_panel001"
    # 恢复后桌面又改了：下一次调用读到
    _write_layout(project, "组图", [_panel(overrides=[SPINE])])
    assert _state(sid)["layout_sync"]["reason"] == "desktop_changed"


def test_restored_binding_is_lost_when_the_panel_is_gone(project, worker):
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    sid = _open(project)["session_id"]
    bridge.sessions().clear()
    _write_layout(project, "组图", [_panel("o_new")])
    out = _state(sid)
    assert out["binding"]["state"] == "lost"
    assert bridge.get_session(sid).binding is None


def test_garbage_binding_in_a_record_is_ignored(project, worker):
    sid = _open(project)["session_id"]
    record = bridge.sessionjournal.load(bridge._journal_dir(), sid)
    bridge.sessionjournal.save(bridge._journal_dir(), {**record, "binding": {"layout": 3}})
    bridge.sessions().clear()
    assert bridge.get_session(sid).binding is None


def test_failed_render_keeps_layout_sync_on_the_error(project, worker, monkeypatch):
    _write_layout(project, "组图", [_panel(overrides=[FONT])])
    sid = _open(project)["session_id"]
    _write_layout(project, "组图", [_panel(overrides=[SPINE])])

    def boom(*a, **k):
        raise bridge.BridgeError("渲染失败", code="render_failed")

    real = bridge._render_applicable
    monkeypatch.setattr(bridge, "_render_applicable", boom)
    res = _call("tavotto_session_state", {"session_id": sid})
    assert res["isError"] and _body(res)["layout_sync"]["reason"] == "desktop_changed"
    # 绑定的修订号没前进：下一次调用还会再试
    monkeypatch.setattr(bridge, "_render_applicable", real)
    assert _state(sid)["layout_sync"]["reason"] == "desktop_changed"


def test_open_result_carries_the_bound_patches_for_the_canvas_ledger(project, worker):
    """Codex #2359：修改来自子图时，回包带 `patches`——内嵌画布按它 seed 账本，否则下一次全量 apply 会把子图的修改抹掉。"""
    _write_layout(project, "组图", [_panel(overrides=[FONT, DIRECTION])])
    out = _open(project)
    assert out["binding"]["state"] == "bound"
    assert {(p["gid"], p["prop"]) for p in out["patches"]} == {
        ("axes_0.xticks", "fontsize"),
        ("axes_0.xticks", "direction"),
    }
    assert out["patches"] == bridge.get_session(out["session_id"]).patches


def test_unbound_open_result_has_no_patches_field(project, worker):
    out = _open(project)
    assert out["binding"]["state"] == "none"
    assert "patches" not in out


def test_batch_open_never_binds(project, worker):
    """Codex #2359：批量打开要的是图本身（规则：批量打开不绑），不按排版里的子图换底子、不带锁。"""
    _write_layout(project, "组图", [_panel(overrides=[FONT], lockedGids=["axes_0.xticks"])])
    out = bridge.open_figures(str(project), stems=["Fig1"])
    sid = out["opened"][0]["session_id"]
    session = bridge.get_session(sid)
    assert session.binding is None
    assert session.patches == []
    assert not session.locked_gids
