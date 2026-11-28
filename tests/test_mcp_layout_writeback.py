"""插件绑定会话写回排版（ADR 0217 §四）：改图内修改后把整份列表写回那个子图的 `overrides`。

假 worker 与排版夹具同 `test_mcp_layout_binding`。覆盖：写回只动这个子图的 overrides（identity 保留、别的对象原样）、
没绑定不碰排版、写前排版变了 → `layout_changed`（会话与文件都不变）、撤销凭据能撤、`suggest_apply` 也写、
账本（`agent_additions`）认得出子图的修改、桌面赢之后下一次调用说 `desktop_won`。
"""

# ruff: noqa: F811  — 夹具从别的测试模块导入后在用例形参里同名使用（pytest 的写法）
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp"))

from tavotto.engine import layoutannotate as la  # noqa: E402
from tavotto_mcp import bridge  # noqa: E402
from test_mcp_layout_binding import (  # noqa: E402
    DIRECTION,
    FONT,
    SPINE,
    _open,
    _panel,
    _state,
    _write_layout,
)
from test_mcp_server import _body, _call, _clean_sessions, project  # noqa: E402, F401
from test_mcp_suggest import TODOS, worker  # noqa: E402, F401


def _doc(path: Path) -> dict:
    """排版此刻的正文（经引擎读：落盘时带的外壳由它拆）"""
    return la.open_layout(path.parent.parent, path.stem).doc


def _panel_of(path: Path, pid="o_panel001") -> dict:
    return next(o for c in _doc(path)["canvases"] for o in c["objects"] if o["id"] == pid)


def _apply(sid: str, patches: list, **kw) -> dict:
    return _call("tavotto_apply_overrides", {"session_id": sid, "patches": patches, **kw})


def _other():
    return _panel("o_other01", fileId="Other.pdf", x=100.0)


def _setup(project, overrides=None, **extra):
    path = _write_layout(project, "组图", [_panel(overrides=overrides, **extra), _other()])
    return path, _open(project)["session_id"]


def test_bound_apply_writes_only_this_panels_overrides(project, worker):
    path, sid = _setup(project, [FONT, DIRECTION])
    before = _doc(path)
    res = _apply(sid, [{**FONT, "value": 9.0}, DIRECTION, SPINE])
    body = _body(res)
    assert not res.get("isError"), body
    written = body["layout_written"]
    assert written["layout"] == "组图" and written["panel"] == "o_panel001"
    assert written["undo_token"].startswith("u")
    after = _doc(path)
    got = _panel_of(path)["overrides"]
    assert {(p["gid"], p["prop"]): p["value"] for p in got} == {
        ("axes_0.xticks", "fontsize"): 9.0,
        ("axes_0.xticks", "direction"): "out",
        ("axes_0", "spine_top"): False,
    }
    assert next(p for p in got if p["prop"] == "direction")["identity"] == "tick-id"

    def strip(d: dict) -> dict:
        return {
            **d,
            "canvases": [
                {
                    **c,
                    "objects": [
                        {k: v for k, v in o.items() if k != "overrides"} for o in c["objects"]
                    ],
                }
                for c in d["canvases"]
            ],
        }

    # 别的对象原样；这个子图除 overrides 外的字段、文档别处原样
    assert [o for o in after["canvases"][0]["objects"] if o["id"] == "o_other01"] == [
        o for o in before["canvases"][0]["objects"] if o["id"] == "o_other01"
    ]
    assert strip(after) == strip(before)
    # 绑定跟上了：不会把自己的写入当成桌面改动
    assert "layout_sync" not in _state(sid)


def test_identity_is_inherited_when_the_value_is_unchanged(project, worker):
    path, sid = _setup(project, [DIRECTION])
    bare = {k: v for k, v in DIRECTION.items() if k != "identity"}
    assert bridge.get_session(sid).patches[0]["identity"] == "tick-id"
    _apply(sid, [bare, SPINE])
    got = {p["prop"]: p for p in _panel_of(path)["overrides"]}
    assert got["direction"]["identity"] == "tick-id"


def test_unbound_apply_never_touches_layouts(project, worker):
    path = _write_layout(
        project, "组图", [_panel("o_a"), _panel("o_b")]
    )  # 同一张图两个子图：不自动绑
    before = path.read_bytes()
    sid = _open(project)["session_id"]
    body = _body(_apply(sid, [FONT]))
    assert "layout_written" not in body
    assert path.read_bytes() == before


def test_unchanged_list_writes_nothing(project, worker):
    path, sid = _setup(project, [FONT])
    before = path.read_bytes()
    body = _body(_apply(sid, [FONT]))
    assert "layout_written" not in body
    assert path.read_bytes() == before


def test_layout_changed_between_read_and_write_refuses_and_reverts(project, worker, monkeypatch):
    path, sid = _setup(project, [FONT])
    real = bridge._render

    def desktop_writes_during_render(session, patches, **kw):
        out = real(session, patches, **kw)
        if patches != [FONT]:  # 只在这次调用的渲染后改；退回渲染不再改
            _write_layout(project, "组图", [_panel(overrides=[SPINE]), _other()])
        return out

    monkeypatch.setattr(bridge, "_render", desktop_writes_during_render)
    res = _apply(sid, [FONT, DIRECTION])
    monkeypatch.setattr(bridge, "_render", real)
    assert res["isError"] and _body(res)["code"] == "layout_changed"
    assert bridge.get_session(sid).patches == [FONT]  # 会话没变
    assert _panel_of(path)["overrides"] == [SPINE]  # 文件是桌面写的那份
    assert [c for c in worker.calls if c[0] == "override"][-1][2] == [FONT]  # 画面退回
    assert (
        _state(sid)["layout_sync"]["reason"] == "desktop_changed"
    )  # 宿主重读：下一次调用先对齐桌面


def test_undo_token_restores_the_previous_overrides(project, worker):
    path, sid = _setup(project, [FONT])
    token = _body(_apply(sid, [FONT, SPINE]))["layout_written"]["undo_token"]
    assert len(_panel_of(path)["overrides"]) == 2
    res = _call(
        "tavotto_layout_annotate_undo",
        {"project_path": str(project), "layout": "组图", "undo_token": token},
    )
    assert not res.get("isError"), _body(res)
    assert [p["prop"] for p in _panel_of(path)["overrides"]] == ["fontsize"]
    # 自己撤销不算「桌面赢」，只当桌面改动读回
    assert _state(sid)["layout_sync"]["reason"] == "desktop_changed"


def test_suggest_apply_writes_too(project, worker):
    path, sid = _setup(project)
    plan = _body(_call("tavotto_suggest_plan", {"session_id": sid, "todos": TODOS}))
    key = next(t["key"] for t in plan["todos"] if t["label"] == "横轴刻度字号改小")
    done = _body(
        _call(
            "tavotto_suggest_apply", {"session_id": sid, "plan_id": plan["plan_id"], "keys": [key]}
        )
    )
    assert done["layout_written"]["panel"] == "o_panel001"
    assert any(p["prop"] == "fontsize" and p["value"] == 7.0 for p in _panel_of(path)["overrides"])


def test_ledger_reports_the_panel_overrides_change(project, worker):
    path, sid = _setup(project, [FONT])
    base = la.open_layout(project, "组图").revision
    _apply(sid, [{**FONT, "value": 9.0}, SPINE])
    got = la.agent_additions(project, "组图", base)
    assert got is not None and got["count"] == 1
    change = got["canvases"][0]["changed"][0]
    assert change["before"]["id"] == change["after"]["id"] == "o_panel001"
    assert [p["value"] for p in change["before"]["overrides"]] == [7.0]
    assert {p["prop"] for p in change["after"]["overrides"]} == {"fontsize", "spine_top"}


def test_desktop_winning_a_key_is_reported_on_the_next_call(project, worker):
    path, sid = _setup(project, [FONT])
    _apply(sid, [{**FONT, "value": 9.0}, SPINE])
    # 桌面并入时：fontsize 桌面自己也改过（留 5.0），spine_top 取了 Agent 的
    _write_layout(project, "组图", [_panel(overrides=[{**FONT, "value": 5.0}, SPINE]), _other()])
    sync = _state(sid)["layout_sync"]
    assert sync["reason"] == "desktop_won"
    assert sync["won"] == [{"gid": "axes_0.xticks", "prop": "fontsize"}] and sync["won_count"] == 1
    assert {p["prop"]: p["value"] for p in bridge.get_session(sid).patches}["fontsize"] == 5.0
    assert _state(sid).get("layout_sync") is None  # 说一次就够


def test_desktop_keeping_everything_is_not_reported(project, worker):
    path, sid = _setup(project, [FONT])
    _apply(sid, [{**FONT, "value": 9.0}])
    # 桌面只挪了子图：修改没变
    _write_layout(project, "组图", [_panel(overrides=[{**FONT, "value": 9.0}], x=33.0), _other()])
    assert "layout_sync" not in _state(sid)


def test_normalize_commit_writes_once_and_a_stale_layout_rolls_back(project, worker, monkeypatch):
    path, sid = _setup(project, [FONT])
    session = bridge.get_session(sid)

    def fake_normalize(session_id, **kw):
        bridge._render(session, [FONT, SPINE], preview_dpi=None)
        return {"ok": True}

    monkeypatch.setattr(bridge, "_normalize_figure", fake_normalize)
    out = bridge.normalize_figure(sid, targets={})
    assert out["layout_written"]["panel"] == "o_panel001"
    assert {p["prop"] for p in _panel_of(path)["overrides"]} == {"fontsize", "spine_top"}

    def fake_normalize2(session_id, **kw):  # 桌面在事务期间改了：整体不提交，会话退回
        bridge._render(session, [FONT, SPINE, DIRECTION], preview_dpi=None)
        _write_layout(project, "组图", [_panel(overrides=[SPINE]), _other()])
        return {"ok": True}

    monkeypatch.setattr(bridge, "_normalize_figure", fake_normalize2)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.normalize_figure(sid, targets={})
    assert exc.value.code == "layout_changed"
    assert {p["prop"] for p in session.patches} == {"fontsize", "spine_top"}
    assert _panel_of(path)["overrides"] == [SPINE]
