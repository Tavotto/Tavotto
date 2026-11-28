"""组名进文档模型（ADR 0191 修订 / ADR 0208 前置 6.0）：读法对拍、铸 id 留名、`group` 存名、`rename_group`、
拆组摘链清键、旧文档兼容、导出 `group_names`。前端镜像 `web/src/lib/groupTree.ts` 用同一份 `tests/golden/group_names_vectors.json`。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import agentobjects, vectoredit as ve

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "golden" / "group_names_vectors.json").read_text("utf-8"))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_read_rule_matches_the_shared_vectors(case):
    objs = case["objects"]
    gids = {g for o in objs for g in agentobjects._group_path_of(o)}  # noqa: SLF001
    got = {g: agentobjects.group_name_of(objs, g) for g in gids}
    assert {g: n for g, n in got.items() if n} == case["names"]


def _t(oid, x=0, **kw):
    return {
        "id": oid,
        "type": "text",
        "x": x,
        "y": 0,
        "w": 5,
        "h": 3,
        "text": "t",
        "sizePt": 8,
        "color": "#000000",
        **kw,
    }


def _rect(oid, path, **kw):
    return {
        "id": oid,
        "type": "shape",
        "shape": "rect",
        "x": 0,
        "y": 0,
        "w": 2,
        "h": 2,
        "strokePt": 1,
        "color": "#000000",
        "fill": None,
        "groupPath": path,
        **kw,
    }


def _err(objs, ops):
    with pytest.raises(ve.EditError) as e:
        ve.apply_ops(objs, ops)
    return e.value.as_dict()


# ---------------------------------------------------------------- 铸 id 留名


def test_mint_keeps_group_names_keyed_by_the_minted_ids():
    r = agentobjects.validate_new_objects(
        [
            _rect("a", ["机理图", "细胞膜"]),
            _rect("b", ["机理图"]),
            _rect("c", ["另一个"]),
            {**_rect("d", ["x"])},
        ]
    )
    assert r["ok"], r["errors"]
    a, b, c, _ = r["objects"]
    outer, inner = a["groupPath"]
    assert a["groupNames"] == {outer: "机理图", inner: "细胞膜"}
    assert b["groupId"] == outer and b["groupNames"] == {outer: "机理图"}  # 一层组：键 = groupId
    assert c["groupNames"] == {c["groupId"]: "另一个"}
    assert agentobjects.group_name_of(r["objects"], inner) == "细胞膜"


def test_agents_cannot_write_group_names_directly():
    r = agentobjects.validate_new_objects([{**_rect("a", ["x"]), "groupNames": {"g": "x"}}])
    assert not r["ok"] and r["errors"][0]["code"] == "reserved_field"


# ---------------------------------------------------------------- 导出


def test_export_carries_group_names_and_nothing_extra_for_unnamed_docs():
    named = agentobjects.validate_new_objects(
        [_rect("a", ["机理图", "细胞膜"]), _rect("b", ["机理图"])]
    )["objects"]
    ea, eb = agentobjects.to_export_objects(named)
    assert ea["group_names"] == ["机理图", "细胞膜"] and eb["group_names"] == ["机理图"]
    # 没有名字的文档：一个键都不多（旧文档逐字节不变）
    bare = [{k: v for k, v in o.items() if k != "groupNames"} for o in named]
    assert all("group_names" not in e for e in agentobjects.to_export_objects(bare))
    # 名字只在被隐藏的成员上：照样发给可见成员（读法按全集）
    hidden_named = [
        {**named[0], "hidden": True},
        {k: v for k, v in named[1].items() if k != "groupNames"},
    ]
    (only,) = agentobjects.to_export_objects(hidden_named)
    assert only["group_names"] == ["机理图"]
    # 中间一层没名字 = 空串占位，长度与 group_path 一致
    mid = [{**named[0], "groupNames": {named[0]["groupPath"][0]: "外"}}]
    (e,) = agentobjects.to_export_objects(mid)
    assert e["group_names"] == ["外", ""]


# ---------------------------------------------------------------- group / rename_group / ungroup


def test_group_op_really_stores_the_name_on_every_member():
    objs = [_t("a"), _t("x"), _t("b")]
    r = ve.apply_ops(objs, [{"op": "group", "ids": ["a", "b"], "name": "标注"}])
    by = {o["id"]: o for o in r["objects"]}
    gid = by["a"]["groupId"]
    assert [by[k].get("groupNames") for k in "axb"] == [{gid: "标注"}, None, {gid: "标注"}]
    assert {(c["id"], c["field"]) for c in r["changes"]} >= {
        ("a", "groupNames"),
        ("b", "groupNames"),
    }
    # 不给名字就不写这个键
    r2 = ve.apply_ops(objs, [{"op": "group", "ids": ["a", "b"]}])
    assert all("groupNames" not in o for o in r2["objects"])


def test_rename_group_writes_every_member_and_unifies_old_disagreements():
    objs = [
        _t("a", groupId="g1", groupNames={"g1": "甲"}),
        _t("b", groupId="g1", groupNames={"g1": "乙"}),
        _t("c", groupId="g1"),
        _t("z"),
    ]
    r = ve.apply_ops(objs, [{"op": "rename_group", "group": "g1", "name": "新名"}])
    assert [o.get("groupNames") for o in r["objects"]] == [{"g1": "新名"}] * 3 + [None]
    assert r["echo"] == ["组 g1：甲 → 新名（3 个对象）"]
    assert {c["id"] for c in r["changes"]} == {"a", "b", "c"}
    # 空串 = 去掉名字，键一并拿掉
    r2 = ve.apply_ops(r["objects"], [{"op": "rename_group", "group": "g1", "name": ""}])
    assert all("groupNames" not in o for o in r2["objects"])
    # 同一个名字再写一遍：没有改动
    r3 = ve.apply_ops(r["objects"], [{"op": "rename_group", "group": "g1", "name": "新名"}])
    assert r3["changes"] == []


def test_group_name_is_stripped_and_whitespace_only_is_refused():
    objs = [_t("a"), _t("b")]
    r = ve.apply_ops(objs, [{"op": "group", "ids": ["a", "b"], "name": "  标注  "}])
    assert {n for o in r["objects"] for n in o["groupNames"].values()} == {"标注"}
    assert _err(objs, [{"op": "group", "ids": ["a", "b"], "name": "   "}]) == {
        "code": "bad_value",
        "index": 0,
        "field": "name",
    }


def test_the_plan_path_checks_and_lands_the_names_on_panels_too():
    """桌面改图助手 / 插件走的 `propose_memory`（带核对）：组里含子图时 `rename_group` 也写到子图上，没有失败的核对项。"""
    canvas = {
        "id": "c",
        "page": {"w": 100, "h": 80},
        "objects": [
            _t("t1", groupId="g1"),
            {
                "id": "P",
                "type": "panel",
                "fileId": "f.pdf",
                "x": 10,
                "y": 10,
                "w": 30,
                "h": 20,
                "groupId": "g1",
            },
            _t("t2", 50),
            _t("t3", 60),
        ],
    }
    r = ve.propose_memory(
        canvas,
        [
            {"op": "rename_group", "group": "g1", "name": "机理图"},
            {"op": "group", "ids": ["t2", "t3"], "name": "外"},
        ],
    )
    assert r["ok"] and r["failed"] == [] and r["errors"] == []
    by = {o["id"]: o for o in r["objects"]}
    assert by["P"]["groupNames"] == {"g1": "机理图"} and by["t1"]["groupNames"] == {"g1": "机理图"}
    assert by["t2"]["groupNames"] == by["t3"]["groupNames"] != by["t1"]["groupNames"]
    # 每个组名变化都带着它是第几步操作产生的（桌面方案卡按步勾选）
    assert {c["op"] for c in r["changes"] if c.get("field") == "groupNames"} == {0, 1}


def test_rename_group_keeps_other_layers_names_and_validates():
    objs = [_t("a", groupId="g2", groupPath=["g1", "g2"], groupNames={"g1": "外", "g2": "内"})]
    r = ve.apply_ops(objs, [{"op": "rename_group", "group": "g2", "name": "里"}])
    assert r["objects"][0]["groupNames"] == {"g1": "外", "g2": "里"}
    assert (
        _err(objs, [{"op": "rename_group", "group": "nope", "name": "x"}])["code"]
        == "unknown_object"
    )
    assert _err(objs, [{"op": "rename_group", "group": "g1", "name": 5}]) == {
        "code": "bad_value",
        "index": 0,
        "field": "name",
    }
    assert (
        _err(objs, [{"op": "rename_group", "group": "g1", "name": "x" * 121}])["code"]
        == "bad_value"
    )
    assert _err(objs, [{"op": "rename_group", "group": "g1"}])["code"] == "bad_value"
    assert _err(objs, [{"op": "rename_group", "name": "x"}])["code"] == "bad_op"
    assert (
        _err(objs, [{"op": "rename_group", "group": "g1", "name": "x", "extra": 1}])["code"]
        == "bad_op"
    )


def test_ungroup_drops_the_names_of_the_peeled_layer_only():
    objs = [
        _t("a", groupId="g2", groupPath=["g1", "g2"], groupNames={"g1": "外", "g2": "内"}),
        _t("b", groupId="g1", groupNames={"g1": "外"}),
    ]
    r = ve.apply_ops(objs, [{"op": "ungroup", "group": "g2"}])
    a = r["objects"][0]
    assert a["groupId"] == "g1" and a["groupNames"] == {"g1": "外"}
    r2 = ve.apply_ops(objs, [{"op": "ungroup", "group": "g1"}])
    assert r2["objects"][0]["groupNames"] == {"g2": "内"} and "groupNames" not in r2["objects"][1]


def test_scene_shows_group_names_and_old_docs_are_unchanged():
    named = [_t("a", groupId="g1", groupNames={"g1": "机理图"}), _t("b", groupId="g1")]
    out = ve.select({"objects": named})
    assert [b.get("group_names") for b in out] == [
        {"g1": "机理图"},
        {"g1": "机理图"},
    ]  # b 没写，读法按全集
    old = ve.select({"objects": [_t("a", groupId="g1")]})
    assert "group_names" not in old[0]


def test_stale_names_from_an_old_build_are_ignored_not_misapplied():
    """旧构建只改了 groupId：键对不上，名字不会安到新组上，也不会让取消编组出错。"""
    objs = [_t("a", groupId="g_new", groupNames={"g_old": "旧名"}), _t("b", groupId="g_new")]
    assert agentobjects.group_name_of(objs, "g_new") == ""
    r = ve.apply_ops(objs, [{"op": "ungroup", "group": "g_new"}])
    assert "groupId" not in r["objects"][0]
    assert "groupNames" not in r["objects"][0]  # 摘出组链后不在链上的键一并清掉


def test_ops_doc_mentions_rename_group():
    assert "rename_group" in ve.OPS_DOC


# ---------------------------------------------------------------- 真编译：名字写进 SVG 的 <g id>


def _svg_group_ids(tmp_path, provider, payload):
    from xml.etree import ElementTree as ET

    from tavotto.rendercore import plan
    from tavotto.rendercore.svgwriter import write_svg

    compiled = plan.compile_page(120, 90, payload, sources=None, faces=provider)
    out = tmp_path / "o.svg"
    write_svg(compiled.svg_page or compiled.page, out, provider, {})
    root = ET.parse(out).getroot()

    def walk(el):
        for c in el:
            if c.tag.rsplit("}", 1)[-1] == "g":
                yield c.get("id"), list(walk(c))

    return list(walk(root))


def test_frontend_payload_names_land_in_the_svg(tmp_path):
    pytest.importorskip("pikepdf")
    pytest.importorskip("uharfbuzz")
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider
    from test_svg_export_groups import GOLDEN  # 前端 `toExportObjects` 写的载荷

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"not_run: approved fonts missing: {reg.missing}")
    case = next(c for c in GOLDEN["cases"] if c["name"].startswith("组有名字"))
    tree = _svg_group_ids(tmp_path, HbFaceProvider(reg), case["payload"])

    def flat(nodes):
        for gid, kids in nodes:
            yield gid
            yield from flat(kids)

    ids = [g for g in flat(tree) if g]
    assert "机理图" in ids and "受体簇" in ids and "不一致的名字" not in ids
