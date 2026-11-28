"""改排版上已有矢量对象的插件工具（ADR 0212 第 5 期）走真 stdio + 真 worker：场景列出对象 → 出编辑方案（复述 + 局部图）
→ 写入 → 撤销。排版与 MCP 客户端复用 `test_mcp_layout_annotate.py` 那一套（一张真脚本出的子图在画布 (10, 10)）。"""

from __future__ import annotations

import json

import pytest

from test_mcp_layout_annotate import (
    _layout,
    _legacy_layout_file_form,  # noqa: F401 — 夹具：钉旧 <名>.json 形态（ADR 0211 第二期 PR 3）
)
from test_mcp_roundtrip import _worker_python, client, project  # noqa: F401 — 夹具

pytestmark = pytest.mark.skipif(_worker_python() is None, reason="没有可用的 worker Python")

ARROW = {
    "id": "a_user0001",
    "type": "arrow",
    "x": 100,
    "y": 20,
    "w": 20,
    "h": 6,
    "start": {"rx": 0, "ry": 0},
    "end": {"rx": 1, "ry": 1},
    "color": "#000000",
    "strokePt": 0.5,
    "head": "end",
    "name": "红箭头",
}
NOTE = {
    "id": "t_user0001",
    "type": "text",
    "x": 130,
    "y": 20,
    "w": 15,
    "h": 4,
    "text": "note",
    "sizePt": 8,
    "bold": False,
    "align": "left",
    "color": "#000000",
}


def _with_objects(project_dir):
    path = _layout(project_dir)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["canvases"][0]["objects"] += [ARROW, NOTE]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _objs(path):
    return {
        o["id"]: o for o in json.loads(path.read_text(encoding="utf-8"))["canvases"][0]["objects"]
    }


def test_tools_are_listed(client):  # noqa: F811
    names = {t["name"] for t in client.call("tools/list", {})["result"]["tools"]}
    assert {"tavotto_layout_edit_plan", "tavotto_layout_edit_apply"} <= names


def test_scene_plan_apply_undo_edit_over_real_stdio(client, project):  # noqa: F811
    path = _with_objects(project)
    original = path.read_bytes()
    target = {"project_path": str(project), "layout": "组图"}
    scene = client.tool("tavotto_layout_scene", target)
    assert {o["id"] for o in scene["vector_objects"]} == {"a_user0001", "t_user0001"}
    arrows = client.tool("tavotto_layout_scene", {**target, "select": {"types": ["arrow"]}})
    assert [o["id"] for o in arrows["vector_objects"]] == ["a_user0001"]

    ops = [
        {"op": "set", "id": "a_user0001", "set": {"strokePt": 0.75, "color": "#c0392b"}},
        {"op": "align", "ids": ["a_user0001", "t_user0001"], "mode": "top"},
    ]
    common = {**target, "ops": ops, "base_revision": scene["base_revision"]}
    raw = client.call("tools/call", {"name": "tavotto_layout_edit_plan", "arguments": common})[
        "result"
    ]
    assert not raw.get("isError"), raw
    plan = raw["structuredContent"]
    assert plan["ok"], plan
    # 复述逐条说改了谁、从什么改成什么
    assert any("strokePt 0.5 → 0.75" in e for e in plan["echo"]), plan["echo"]
    assert "objects" not in plan  # 改后的整张对象表不进回包
    # 局部图是真合成：一张 PNG 跟在文字后面
    assert any(c["type"] == "image" for c in raw["content"])
    assert path.read_bytes() == original  # 出方案不写盘

    done = client.tool("tavotto_layout_edit_apply", {**common, "echo_digest": plan["echo_digest"]})
    assert done["written"] and done["undo_token"]
    now = _objs(path)
    assert now["a_user0001"]["strokePt"] == 0.75 and now["a_user0001"]["color"] == "#c0392b"
    assert now["o_panel001"] == json.loads(original)["canvases"][0]["objects"][0]  # 子图逐字段不动

    client.tool("tavotto_layout_annotate_undo", {**target, "undo_token": done["undo_token"]})
    assert path.read_bytes() == original


def test_a_bad_patch_says_which_op_and_writes_nothing(client, project):  # noqa: F811
    path = _with_objects(project)
    before = path.read_bytes()
    target = {"project_path": str(project), "layout": "组图"}
    scene = client.tool("tavotto_layout_scene", target)
    raw = client.call(
        "tools/call",
        {
            "name": "tavotto_layout_edit_plan",
            "arguments": {
                **target,
                "ops": [
                    {"op": "set", "id": "a_user0001", "set": {"strokePt": 0.75}},
                    {"op": "delete", "id": "o_panel001"},
                ],
                "base_revision": scene["base_revision"],
            },
        },
    )["result"]
    plan = raw["structuredContent"]
    assert not plan["ok"] and plan["errors"] == [{"code": "not_vector", "index": 1}]
    assert "第 2 条" in raw["content"][0]["text"] + raw["content"][-1]["text"]
    assert path.read_bytes() == before


def test_edit_apply_without_the_plans_digest_is_refused(client, project):  # noqa: F811
    path = _with_objects(project)
    before = path.read_bytes()
    target = {"project_path": str(project), "layout": "组图"}
    scene = client.tool("tavotto_layout_scene", target)
    raw = client.call(
        "tools/call",
        {
            "name": "tavotto_layout_edit_apply",
            "arguments": {
                **target,
                "ops": [{"op": "delete", "id": "t_user0001"}],
                "base_revision": scene["base_revision"],
                "echo_digest": "nope",
            },
        },
    )["result"]
    assert raw["isError"] and raw["structuredContent"]["code"] == "echo_mismatch"
    assert path.read_bytes() == before


def test_a_bad_select_is_an_error_not_select_all(client, project):  # noqa: F811
    _with_objects(project)
    raw = client.call(
        "tools/call",
        {
            "name": "tavotto_layout_scene",
            "arguments": {
                "project_path": str(project),
                "layout": "组图",
                "select": {"colour": "red"},
            },
        },
    )["result"]
    assert raw["isError"] and raw["structuredContent"]["code"] == "bad_select"


def test_group_and_rename_group_persist_names_over_real_stdio(client, project):  # noqa: F811
    """插件读盘写入路径：`group {name}` 与 `rename_group` 真的把 `groupNames` 写进盘上排版，场景里读得到 `group_names`。"""
    path = _with_objects(project)
    target = {"project_path": str(project), "layout": "组图"}
    scene = client.tool("tavotto_layout_scene", target)
    common = {
        **target,
        "ops": [{"op": "group", "ids": ["a_user0001", "t_user0001"], "name": "标注"}],
        "base_revision": scene["base_revision"],
    }
    plan = client.tool("tavotto_layout_edit_plan", common)
    assert plan["ok"] and plan["failed"] == [], plan
    done = client.tool("tavotto_layout_edit_apply", {**common, "echo_digest": plan["echo_digest"]})
    assert done["written"]
    now = _objs(path)
    gid = now["a_user0001"]["groupId"]
    assert now["a_user0001"]["groupNames"] == now["t_user0001"]["groupNames"] == {gid: "标注"}
    scene2 = client.tool("tavotto_layout_scene", target)
    assert {o["id"]: o.get("group_names") for o in scene2["vector_objects"]} == {
        "a_user0001": {gid: "标注"},
        "t_user0001": {gid: "标注"},
    }
    common2 = {
        **target,
        "ops": [{"op": "rename_group", "group": gid, "name": "新名字"}],
        "base_revision": scene2["base_revision"],
    }
    plan2 = client.tool("tavotto_layout_edit_plan", common2)
    assert plan2["ok"], plan2
    client.tool("tavotto_layout_edit_apply", {**common2, "echo_digest": plan2["echo_digest"]})
    now = _objs(path)
    assert now["a_user0001"]["groupNames"] == now["t_user0001"]["groupNames"] == {gid: "新名字"}
    assert now["o_panel001"].get("groupNames") is None  # 不在组里的子图不动


def test_mechanism_items_without_a_licence_are_pro_required_not_an_error(client, project):  # noqa: F811
    """ADR 0220：免费用户也看得到 `frame / glyph / graph` 这三种条目（工具说明里有），调用回结构化 `pro_required`：
    一句话 +「了解高级版」，不是 isError、盘上一个字节不动。授权在测试里全局关着（`conftest`），所以这里就是未授权。"""
    path = _with_objects(project)
    original = path.read_bytes()
    target = {"project_path": str(project), "layout": "组图"}
    scene = client.tool("tavotto_layout_scene", target)
    tools = {t["name"]: t for t in client.call("tools/list", {})["result"]["tools"]}
    for word in ("frame", "glyph", "graph", "pro_required"):
        assert (
            word
            in tools["tavotto_layout_edit_plan"]["inputSchema"]["properties"]["ops"]["description"]
        )
    ops = [
        {
            "op": "set",
            "id": "a_user0001",
            "set": {"strokePt": 0.75},
        },  # 前面免费的一步也不写：整批拒
        {
            "op": "add",
            "items": [
                {"kind": "frame", "name": "f", "title": "T", "at_mm": [1, 1], "size_mm": [20, 10]},
                {"kind": "glyph", "name": "m", "glyph": "membrane", "frame": "f", "at_mm": [2, 2]},
            ],
        },
    ]
    common = {**target, "ops": ops, "base_revision": scene["base_revision"]}
    raw = client.call("tools/call", {"name": "tavotto_layout_edit_plan", "arguments": common})[
        "result"
    ]
    assert not raw.get("isError"), raw
    out = raw["structuredContent"]
    assert out["code"] == "pro_required" and out["feature"] == "mechanism-diagram"
    assert out["learn_more"] and out["message"] and out["ok"] is False
    assert "高级版" in raw["content"][0]["text"]
    done = client.call(
        "tools/call",
        {"name": "tavotto_layout_edit_apply", "arguments": {**common, "echo_digest": "x" * 16}},
    )["result"]
    assert not done.get("isError") and done["structuredContent"]["code"] == "pro_required"
    assert path.read_bytes() == original
