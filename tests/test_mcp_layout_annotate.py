"""排版标注工具（ADR 0208 第 2 期）走真 stdio + 真 worker：读场景 → 出方案（复述 + 局部图）→ 写入 → 撤销。

项目与 MCP 客户端复用 `test_mcp_roundtrip.py` 的那套（同一个真脚本、同一个真 server 子进程）。排版文件手写一份：
一张子图（那张图的磁盘原件，无改动）放在画布 (10, 10)。"""

from __future__ import annotations

import base64
import json

import pytest

from test_mcp_roundtrip import _worker_python, client, project  # noqa: F401 — 夹具

pytestmark = pytest.mark.skipif(_worker_python() is None, reason="没有可用的 worker Python")


def _layout(project_dir, panel_extra=None):
    panel = {
        "id": "o_panel001",
        "type": "panel",
        "x": 10.0,
        "y": 10.0,
        "w": 80.0,
        "h": 60.0,
        "fileId": "FigM.pdf",
        "fileKind": "pdf",
        "nativeW": 80,
        "nativeH": 60,
        "overrides": [],
        **(panel_extra or {}),
    }
    doc = {
        "schema": 3,
        "project": {"id": "p_test0001", "name": "t"},
        "canvases": [
            {
                "id": "c_main0001",
                "name": "图版 1",
                "page": {"w": 180, "h": 90},
                "objects": [panel],
                "guides": [],
            }
        ],
        "activeCanvasId": "c_main0001",
        "createdAt": 0,
        "updatedAt": 0,
    }
    store = project_dir / "tavottofile"
    store.mkdir(exist_ok=True)
    path = store / "组图.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _named(scene, label):
    for p in scene["panels"]:
        for e in p.get("elements") or []:
            if e.get("role") == "line" and label in str(e.get("label")):
                return p["ref"], e["gid"]
    raise AssertionError(f"场景里没有名为 {label} 的元素")


def _axes_gid(scene):
    return scene["panels"][0]["axes"][0]["gid"]


def test_scene_plan_apply_undo_over_real_stdio(client, project):  # noqa: F811
    path = _layout(project)
    original = path.read_bytes()
    target = {"project_path": str(project), "layout": "组图"}
    scene = client.tool("tavotto_layout_scene", target)
    assert scene["unmeasured"] == {}
    ref, gid = _named(scene["scene"], "Sample B")
    items = [
        {
            "kind": "arrow",
            "name": "to_b",
            "from": {"anchor": {"panel": {"id": ref, "point": "e"}}, "offset_mm": [20, 0]},
            "to": {
                "anchor": {
                    "data": {"panel": ref, "axes": _axes_gid(scene["scene"]), "x": 8, "y": 0.6}
                }
            },
        },
        {
            "kind": "text",
            "name": "note",
            "text": "slower",
            "sizePt": 7,
            "at": {"anchor": {"item": {"name": "to_b", "point": "e"}}, "offset_mm": [1, 0]},
            "attach": "w",
        },
    ]
    common = {**target, "items": items, "base_revision": scene["base_revision"]}
    raw = client.call(
        "tools/call",
        {
            "name": "tavotto_layout_annotate_plan",
            "arguments": {**common, "fingerprint": scene["scene"]["fingerprint"]},
        },
    )["result"]
    assert not raw.get("isError"), raw
    plan = raw["structuredContent"]
    assert plan["ok"], plan
    # 复述是从求解结果来的：数据点量到了
    to = next(a for a in plan["echo"][0]["anchors"] if a["role"] == "to")
    assert to["kind"] == "data" and to["measured"] is True
    # 锚到脚本里起了 label= 的曲线：复述认得出它的名字，意图可比对；锚到没有 label= 的标题：如实标 unnamed
    probe = [
        {
            "kind": "arrow",
            "name": "p",
            "from": {"anchor": {"panel": {"id": ref, "point": "e"}}, "offset_mm": [20, 0]},
            "to": {"anchor": {"element": {"panel": ref, "gid": gid, "point": "center"}}},
            "over_data": True,
        }
    ]
    echo = client.tool("tavotto_layout_annotate_plan", {**common, "items": probe})["echo"][0]
    assert echo["intent"] == "describable" and "Sample B" in echo["text"]
    eb = next(
        e["gid"]
        for p in scene["scene"]["panels"]
        for e in p.get("elements") or []
        if e["role"] == "title"
    )
    probe[0]["to"]["anchor"]["element"]["gid"] = eb
    echo = client.tool("tavotto_layout_annotate_plan", {**common, "items": probe})["echo"][0]
    assert echo["intent"] == "unnamed" and "没有名字" in echo["text"]
    # 局部图跟着方案回来（真合成）
    imgs = [c for c in raw["content"] if c.get("type") == "image"]
    assert imgs and base64.b64decode(imgs[0]["data"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert plan["preview"]["missing_panels"] == []

    done = client.tool(
        "tavotto_layout_annotate_apply",
        {
            **common,
            "fingerprint": scene["scene"]["fingerprint"],
            "echo_digest": plan["echo_digest"],
        },
    )
    assert done["written"] is True and len(done["added"]) == 2
    doc = json.loads(path.read_text(encoding="utf-8"))
    types = [o["type"] for o in doc["canvases"][0]["objects"]]
    assert types == ["panel", "arrow", "text"]
    assert "slower" in client.last_text  # 写完把复述念给用户

    client.tool("tavotto_layout_annotate_undo", {**target, "undo_token": done["undo_token"]})
    assert path.read_bytes() == original


def test_snapshot_panels_are_framed_not_measured(client, project):  # noqa: F811
    """排版里按冻结快照合成的子图：现跑脚本不一定是它，只给外框（与成品合成同一个判定）。"""
    _layout(project, {"figureSnapshot": {"sha256": "0" * 64}})
    scene = client.tool("tavotto_layout_scene", {"project_path": str(project), "layout": "组图"})
    assert scene["unmeasured"] == {"P1": "composed_from_snapshot"}


def test_apply_without_the_plans_digest_is_refused(client, project):  # noqa: F811
    path = _layout(project)
    before = path.read_bytes()
    target = {"project_path": str(project), "layout": "组图"}
    scene = client.tool("tavotto_layout_scene", target)
    items = [
        {
            "kind": "text",
            "name": "t",
            "text": "x",
            "sizePt": 7,
            "at": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [5, 0]},
        }
    ]
    raw = client.call(
        "tools/call",
        {
            "name": "tavotto_layout_annotate_apply",
            "arguments": {
                **target,
                "items": items,
                "base_revision": scene["base_revision"],
                "echo_digest": "nope",
            },
        },
    )["result"]
    assert raw["isError"] and raw["structuredContent"]["code"] == "echo_mismatch"
    assert path.read_bytes() == before


def test_measure_finds_the_script_through_the_registrys_artifact_lookup(monkeypatch):
    """子目录里的产物（`figures/fig1.pdf`）按注册表的 `for_artifact` 找到 stem，不按路径自己拆（Codex #2213）。"""
    from tavotto.engine import layoutannotate as la
    from tavotto_mcp import bridge

    class Reg:
        def for_stem(self, stem):
            return None  # 拆路径得到的 `figures/fig1` 在 stem 索引里查不到

        def for_artifact(self, rel):
            return {"stem": "fig1", "script": "plot.py"} if rel == "figures/fig1.pdf" else None

    seen = {}

    def opened(target, *, stem=None, **_kw):
        seen["stem"] = stem
        raise bridge.BridgeError("stop here", code="needs_input")

    monkeypatch.setattr(bridge, "open_figure", opened)
    measure = bridge._layout_measure("/tmp/p", Reg(), la, [], {})
    with pytest.raises(la.PanelUnavailable) as e:
        measure({"id": "o_1", "fileId": "figures/fig1.pdf", "overrides": []}, [])
    assert e.value.code == "open_failed" and seen["stem"] == "fig1"  # 认出了脚本，只是这次打不开
    with pytest.raises(la.PanelUnavailable) as e:
        measure({"id": "o_2", "fileId": "figures/other.pdf", "overrides": []}, [])
    assert e.value.code == "no_script"


@pytest.fixture(autouse=True)
def _legacy_layout_file_form(monkeypatch):
    """ADR 0211 第二期 PR 3 起「排版写成目录形态」默认开；这份用例钉的是改造前 `tavottofile/<名>.json` 的形态与语义，
    显式关开关来跑（与 `TAVOTTO_LAYOUT_DIR_WRITER=0` 应急回退同一档；环境变量也让子进程一并关）。目录形态的对应用例在 `tests/test_layoutdir.py`。"""
    from tavotto.engine import layoutdir

    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", False)
    monkeypatch.setenv(layoutdir.WRITER_ENV, "0")
