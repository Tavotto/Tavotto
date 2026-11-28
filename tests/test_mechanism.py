"""机理图骨架（ADR 0220，6.B）：权益 / `pro_required`、整批拒、协议校验、frame、命名分组、可编辑性核对、假图元端到端。

闭源图元一个都不在这里：`tests/fixtures/pro_echo` 的假图元（形状固定的替身）让开源侧用**真的 sidecar 进程**走完协议。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tavotto.engine import (
    agentobjects,
    license as engine_license,
    mechanism,
    mechcheck,
    mechgroup,
    prohost,
    vectoredit as ve,
    vectorplan as vp,
)
from test_layoutannotate import _doc
from test_vectoredit import MINE

ECHO = Path(__file__).parent / "fixtures" / "pro_echo"


def _canvas():
    return copy.deepcopy(_doc(MINE)["canvases"][0])


FRAME = {"kind": "frame", "name": "egf", "title": "EGF 信号", "at_mm": [5, 5], "size_mm": [60, 40]}
MEMBRANE = {
    "kind": "glyph",
    "name": "mem",
    "glyph": "membrane",
    "frame": "egf",
    "block": "细胞膜",
    "at_mm": [10, 20],
    "identity": "质膜",
}
RTK = {
    "kind": "glyph",
    "name": "r1",
    "glyph": "receptor_rtk",
    "frame": "egf",
    "block": "细胞膜",
    "at_mm": [30, 12],
    "identity": "EGFR",
}


def _add(*items):
    return {"op": "add", "items": list(items)}


@pytest.fixture
def licensed(monkeypatch):
    """授权通过 + 真的 sidecar（开源示例扩展）：认 `mechanism-diagram`。"""
    monkeypatch.setattr(engine_license, "has", lambda feature: feature == mechanism.FEATURE)


@pytest.fixture
def pro(monkeypatch, tmp_path, licensed):
    monkeypatch.delenv(engine_license.ENV_HARD_OFF, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(engine_license.ENV_ENDPOINT, "http://127.0.0.1:9")
    monkeypatch.setenv("TAVOTTO_DIAG_DEV_LOOPBACK", "1")
    monkeypatch.setenv(prohost.ENV_PRO_DIR, str(ECHO))
    monkeypatch.setenv("PRO_ECHO_FEATURES", "quick-layout,mechanism-diagram")
    monkeypatch.setattr(prohost, "_crashes", 0)
    prohost.shutdown()
    yield
    prohost.shutdown()


def _plan(*items, **kw):
    return ve.propose_memory(_canvas(), [_add(*items)], **kw)


# ---------------------------------------------------------------- 权益与整批拒
def test_feature_key_is_registered():
    assert mechanism.FEATURE == "mechanism-diagram" in engine_license.FEATURES


def test_unlicensed_returns_a_structured_pro_required_not_an_error(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: False)
    res = _plan(FRAME, MEMBRANE)
    assert res["ok"] is False and res["code"] == "pro_required"
    assert res["feature"] == "mechanism-diagram" and res["message"]
    assert res["learn_more"] == {
        "label": "了解高级版",
        "url": "https://www.tavotto.com/zh/premium/",
    }
    assert res["errors"] == []  # 不是错误
    assert "objects" not in res or not res.get("changed")


def test_one_paid_item_rejects_the_whole_batch_even_with_free_items(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: False)
    free = {
        "kind": "text",
        "name": "t",
        "text": "hello",
        "at": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [2, -3]},
    }
    res = ve.propose_memory(_canvas(), [_add(free), _add(free, MEMBRANE)])
    assert res["code"] == "pro_required"
    assert "changed" not in res  # 前面免费的那一步也不写：整批拒


def test_free_items_never_ask_for_a_licence(monkeypatch):
    def boom(feature):
        raise AssertionError("免费条目不该判权益")

    monkeypatch.setattr(engine_license, "has", boom)
    free = {"kind": "box", "name": "frame", "fit": [{"panel": {"id": "P1"}}]}
    res = ve.propose_memory(_canvas(), [_add(free)])
    assert res["ok"], res


def test_frame_alone_is_free_and_never_asks_for_a_licence(monkeypatch):
    """维护者 2026-11-28 定：示意图框免费；未授权也照常收下，进命名分组与核对。"""

    def boom(feature):
        raise AssertionError("frame 不该判权益")

    monkeypatch.setattr(engine_license, "has", boom)
    res = _plan(FRAME)
    assert res["ok"], res
    (box,) = res["changed"]
    assert box["name"] == "示意图框 · EGF 信号" and list(box["groupNames"].values()) == [
        "示意图 · EGF 信号"
    ]


def test_frame_plus_glyph_is_rejected_as_a_whole_when_unlicensed(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: False)
    res = _plan(FRAME, MEMBRANE)
    assert res["code"] == "pro_required" and "changed" not in res
    res = _plan(FRAME, {"kind": "graph", "name": "g", "nodes": [], "edges": []})
    assert res["code"] == "pro_required"


def test_graph_is_recognised_but_not_ready(licensed):
    res = _plan({"kind": "graph", "name": "g", "nodes": [], "edges": []})
    assert res["ok"] is False and res["errors"][0]["code"] == "bad_item"


# ---------------------------------------------------------------- frame / 命名 / 分组
def test_frame_is_a_named_group_with_a_box_and_no_new_object_type(licensed):
    res = _plan(FRAME)
    assert res["ok"], res
    (box,) = [o for o in res["changed"] if o["id"] != "x"]
    assert box["type"] == "shape" and box["shape"] == "rect"
    assert box["name"] == "示意图框 · EGF 信号"
    assert box["groupNames"] == {box["groupId"]: "示意图 · EGF 信号"}
    assert (box["x"], box["y"], box["w"], box["h"]) == (5, 5, 60, 40)
    assert any("示意图框" in e for e in res["echo"])


def test_frame_locale_is_baked_in_at_creation(licensed):
    res = ve.propose_memory(_canvas(), [_add(FRAME)], context={"locale": "en-US"})
    (box,) = res["changed"]
    assert box["name"] == "Schematic frame · EGF 信号"
    assert list(box["groupNames"].values()) == ["Schematic · EGF 信号"]


def test_free_items_join_the_frame_and_block(licensed):
    free = {
        "kind": "text",
        "name": "tag",
        "text": "EGF",
        "frame": "egf",
        "block": "胞外",
        "at": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [2, -3]},
    }
    second = {
        **free,
        "name": "tag2",
        "text": "EGFR",
        "at": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [2, -3]},
    }
    res = _plan(FRAME, free, second)
    assert res["ok"], res
    tag = next(o for o in res["changed"] if o["type"] == "text")
    assert len(tag["groupPath"]) == 2
    assert [tag["groupNames"][g] for g in tag["groupPath"]] == ["示意图 · EGF 信号", "胞外"]


def test_a_free_item_naming_a_missing_frame_is_refused(licensed):
    free = {
        "kind": "text",
        "name": "tag",
        "text": "EGF",
        "frame": "nope",
        "at": {"anchor": {"panel": {"id": "P1", "point": "nw"}}},
    }
    res = _plan(free)
    assert res["ok"] is False and res["errors"][0]["code"] == "bad_item"


def test_names_are_role_plus_identity_and_unique_within_a_batch():
    assert mechgroup.name_for("glyph:receptor_rtk", "EGFR", "zh-CN") == "受体酪氨酸激酶 · EGFR"
    assert mechgroup.name_for("glyph:receptor_rtk", None, "en") == "Receptor tyrosine kinase"
    assert mechgroup.name_for("glyph:made_up", "x", "zh") == "made_up · x"  # 词表缺了退回 kind
    n = mechgroup.Names()
    assert [n.unique("A"), n.unique("A"), n.unique("A")] == ["A", "A 2", "A 3"]
    assert mechgroup.locale_of("zh-CN") == "zh" and mechgroup.locale_of("fr") == "en"


# ---------------------------------------------------------------- 假图元端到端（真的 sidecar 进程）
def test_fake_glyphs_end_to_end(pro):
    res = _plan(FRAME, MEMBRANE, RTK)
    assert res["ok"], res
    assert res["failed"] == [] and res["errors"] == []
    by = {o["name"]: o for o in res["changed"]}
    rtk = by["受体酪氨酸激酶 · EGFR"]
    assert rtk["type"] == "path" and rtk["glyph"]["kind"] == "receptor_rtk"
    assert rtk["glyph"]["version"] == 1 and rtk["glyph"]["params"] == {
        "dimer": True
    }  # 缺的参数补默认值
    assert rtk["glyph"]["hash"] == mechcheck.glyph_hash(rtk["subpaths"])
    mem = by["双层膜 · 质膜"]
    assert mem["glyph"]["version"] == 2 and mem["glyph"]["array"] is True  # 缺省用 latest
    assert len(mem["subpaths"]) == 2
    # 同一个「细胞膜」块：两个图元在同一个块组里，块组在框组里
    assert rtk["groupPath"] == mem["groupPath"] and len(rtk["groupPath"]) == 2
    names = [rtk["groupNames"][g] for g in rtk["groupPath"]]
    assert names == ["示意图 · EGF 信号", "细胞膜"]
    # 叠放顺序：背景（框）→ 结构 → …
    order = [o["id"] for o in res["objects"][-3:]]
    assert res["objects"][-3]["type"] == "shape" and order[1:] == [mem["id"], rtk["id"]]
    # 每个新对象的 id 是引擎铸的，不是条目名
    assert all(o["id"] not in ("mem", "r1", "egf__box") for o in res["changed"])


def test_sidecar_only_receives_numbers_and_structure(pro, monkeypatch):
    monkeypatch.setenv("PRO_ECHO_GLYPH_MODE", "echo_payload")
    seen = []

    def spy(cmd, payload):
        seen.append((cmd, copy.deepcopy(payload)))
        return prohost.call(cmd, payload, feature=mechanism.FEATURE)

    secret = "SECRET-LABEL"
    item = {k: v for k, v in {**MEMBRANE, "identity": secret}.items() if k != "block"}
    res = _plan(FRAME, item, context={"mechanism_ask": spy})
    assert res["ok"], res
    glyph_calls = [p for c, p in seen if c == "mechanism.glyph"]
    assert glyph_calls == [
        {
            "kind": "membrane",
            "version": 2,
            "params": {"curvature": 0.0, "style": "solid"},
            "size_mm": [40.0, 6.0],
        }
    ]
    assert secret not in repr(seen) and "细胞膜" not in repr(seen) and "EGF" not in repr(seen)


def test_regeneration_inputs_use_the_requested_version_and_size(pro, monkeypatch):
    monkeypatch.setenv("PRO_ECHO_GLYPH_MODE", "echo_payload")
    one = {k: v for k, v in MEMBRANE.items() if k != "block"}
    res = _plan(FRAME, {**one, "version": 1, "size_mm": [55, 8], "params": {"style": "dots"}})
    assert res["ok"], res
    mem = next(o for o in res["changed"] if o["type"] == "path")
    assert mem["glyph"]["version"] == 1 and mem["glyph"]["params"] == {
        "style": "dots",
        "curvature": 0.0,
    }
    assert (mem["w"], mem["h"]) == (55, 8)


# ---------------------------------------------------------------- 协议校验（注入替身，不走进程）
def _ask_with(reply):
    def ask(cmd, payload):
        if cmd == "mechanism.catalog":
            return {
                "kinds": [
                    {
                        "kind": "blob",
                        "versions": [1],
                        "default_size_mm": [10, 10],
                        "params": [
                            {"name": "n", "type": "number", "min": 1, "max": 3, "default": 2}
                        ],
                    }
                ]
            }
        return reply(payload) if callable(reply) else reply

    return ask


GOOD_NODES = [{"p": [0, 0]}, {"p": [1, 0.2], "out": [1, 0.5]}, {"p": [0.5, 1]}]
BLOB = {"kind": "glyph", "name": "b", "glyph": "blob", "at_mm": [1, 1]}


@pytest.mark.parametrize(
    "reply",
    [
        {
            "version": 1,
            "subpaths": [{"closed": True, "nodes": [{"p": [float("nan"), 0]}, {"p": [1, 1]}]}],
        },
        {
            "version": 1,
            "subpaths": [
                {"closed": True, "nodes": [{"p": [0, 0], "color": "#f00"}, {"p": [1, 1]}]}
            ],
        },
        {"version": 1, "subpaths": []},
        {"version": 1, "subpaths": [{"closed": True, "nodes": [{"p": [0, 0]}]}]},
        {"version": "1", "subpaths": [{"closed": True, "nodes": GOOD_NODES}]},
        {
            "version": 1,
            "subpaths": [{"closed": True, "nodes": GOOD_NODES}],
            "ports": {"a": [1, 2, 3]},
        },
        {"version": 1, "subpaths": [{"closed": 1, "nodes": GOOD_NODES}]},
        "not a dict",
    ],
)
def test_bad_replies_are_refused_before_anything_lands(licensed, reply):
    res = _plan(BLOB, context={"mechanism_ask": _ask_with(reply)})
    assert res["ok"] is False and res["errors"][0]["code"] == "pro_unavailable"


def test_reply_version_must_match_the_request(licensed):
    res = _plan(
        BLOB,
        context={
            "mechanism_ask": _ask_with(
                {"version": 7, "subpaths": [{"closed": True, "nodes": GOOD_NODES}]}
            )
        },
    )
    assert res["errors"][0]["code"] == "pro_unavailable"


def test_param_and_kind_validation(licensed):
    ok = {"version": 1, "subpaths": [{"closed": True, "nodes": GOOD_NODES}]}
    ask = _ask_with(ok)
    assert _plan({**BLOB, "params": {"n": 2.5}}, context={"mechanism_ask": ask})["ok"]
    for bad in (
        {**BLOB, "params": {"n": 9}},  # 超范围
        {**BLOB, "params": {"zzz": 1}},  # 规格里没有的参数
        {**BLOB, "glyph": "nope"},  # 清单里没有的图元
        {**BLOB, "version": 5},  # 组件不认的修订号
        {**BLOB, "at_mm": [float("inf"), 0]},
        {**BLOB, "size_mm": [-1, 4]},
        {**BLOB, "color": "#fff"},  # 条目里没有的键（颜色由开源侧定）
        {**BLOB, "frame": "egf"},  # 点名了不存在的框
    ):
        res = _plan(bad, context={"mechanism_ask": ask})
        assert res["ok"] is False and res["errors"][0]["code"] in ("bad_item", "pro_unavailable"), (
            bad
        )


def test_an_old_component_is_a_needs_update_not_a_crash(licensed):
    def old(cmd, payload):
        raise prohost.ProUnavailable("unsupported", cmd)

    res = _plan(BLOB, context={"mechanism_ask": old})
    assert res["errors"] == [{"code": "pro_unavailable", "index": 0}]
    assert ve.ERROR_WORDS["pro_unavailable"]


def test_a_missing_component_is_unavailable(licensed, monkeypatch):
    monkeypatch.delenv(prohost.ENV_PRO_DIR, raising=False)
    prohost.shutdown()
    res = _plan(BLOB)
    assert res["errors"][0]["code"] == "pro_unavailable"


# ---------------------------------------------------------------- 可编辑性核对（纯判据）
def _path(oid, subs, **kw):
    return {
        "id": oid,
        "type": "path",
        "x": 0,
        "y": 0,
        "w": 20,
        "h": 10,
        "strokePt": 1,
        "color": "#000000",
        "fill": None,
        "name": oid + "x",
        "subpaths": subs,
        **kw,
    }


def _line(a, b, closed=False):
    return {"closed": closed, "nodes": [{"p": a}, {"p": b}]}


RECT = {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]}
RING_OUT = {
    "closed": True,
    "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1.2]}, {"p": [-0.1, 0.5]}],
}
RING_IN = {"closed": True, "nodes": [{"p": [0.3, 0.3]}, {"p": [0.7, 0.3]}, {"p": [0.5, 0.7]}]}
FAR_A = {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [0.2, 0]}, {"p": [0.1, 0.3]}]}
FAR_B = {"closed": True, "nodes": [{"p": [0.7, 0.6]}, {"p": [0.95, 0.6]}, {"p": [0.85, 0.95]}]}


def _failed(objs, **kw):
    return {
        (f["check"], f["detail"].get("reason") or tuple(f["detail"]))
        for f in mechcheck.check(objs, require_names=False, **kw)["failed"]
    }


def test_one_object_rule():
    assert components_of([RING_OUT, RING_IN]) == 1  # 带洞（嵌套）放行
    assert components_of([FAR_A, FAR_B]) == 2  # 两个分开的形状并成一条 = 两个东西
    crossing = [_line([0, 0], [1, 1]), _line([0, 1], [1, 0])]
    assert components_of(crossing) == 1
    assert ("one_object", ("components",)) in _failed([_path("p", [FAR_A, FAR_B])])
    assert not _failed([_path("p", [RING_OUT, RING_IN])])


def components_of(subs):
    return mechcheck.components(_path("p", subs))


def test_array_glyph_is_exempt_from_one_object():
    g = {
        "kind": "membrane",
        "version": 1,
        "params": {},
        "hash": mechcheck.glyph_hash([FAR_A, FAR_B]),
        "array": True,
    }
    assert not _failed([_path("p", [FAR_A, FAR_B], glyph=g)])
    g2 = {k: v for k, v in g.items() if k != "array"}
    assert ("one_object", ("components",)) in _failed([_path("p", [FAR_A, FAR_B], glyph=g2)])


def test_native_shapes_must_not_be_drawn_as_paths():
    assert mechcheck._fits_native(_path("p", [RECT])) == "rect"  # noqa: SLF001
    assert mechcheck._fits_native(_path("p", [_line([0, 0.5], [1, 0.5])])) == "line"  # noqa: SLF001
    k = 0.5522847498
    ell = {
        "closed": True,
        "nodes": [
            {"p": [0.5, 0], "in": [0.5 - k / 2, 0], "out": [0.5 + k / 2, 0]},
            {"p": [1, 0.5], "in": [1, 0.5 - k / 2], "out": [1, 0.5 + k / 2]},
            {"p": [0.5, 1], "in": [0.5 + k / 2, 1], "out": [0.5 - k / 2, 1]},
            {"p": [0, 0.5], "in": [0, 0.5 + k / 2], "out": [0, 0.5 - k / 2]},
        ],
    }
    assert mechcheck._fits_native(_path("p", [ell])) == "ellipse"  # noqa: SLF001
    assert mechcheck._fits_native(_path("p", [RING_OUT])) is None  # noqa: SLF001
    assert ("native_shape", ("use",)) in _failed([_path("p", [RECT])])


def test_glyph_meta_is_checked_against_geometry_and_catalog():
    subs = [RING_OUT]
    good = {"kind": "blob", "version": 1, "params": {}, "hash": mechcheck.glyph_hash(subs)}
    cat = {"blob": {"versions": [1, 2]}}
    assert not _failed([_path("p", subs, glyph=good)], catalog=cat)
    assert ("glyph_meta", "hash_mismatch") in _failed(
        [_path("p", subs, glyph={**good, "hash": "0" * 16})]
    )  # 手改了形状 = 脱钩
    assert ("glyph_meta", "malformed") in _failed([_path("p", subs, glyph={"kind": "blob"})])
    assert ("glyph_meta", "unknown_kind_or_version") in _failed(
        [_path("p", subs, glyph={**good, "version": 9})], catalog=cat
    )
    assert ("glyph_meta", "unknown_kind_or_version") in _failed(
        [_path("p", subs, glyph={**good, "kind": "zz"})], catalog=cat
    )


def test_glyph_hash_survives_moving_and_scaling_but_not_reshaping():
    a = mechcheck.glyph_hash([RING_OUT])
    assert a == mechcheck.glyph_hash([copy.deepcopy(RING_OUT)])
    moved = copy.deepcopy(RING_OUT)
    moved["nodes"][0]["p"][0] += 0.00001  # 量化到 1e-4 以下的抖动不算
    assert a == mechcheck.glyph_hash([moved])
    moved["nodes"][0]["p"][0] += 0.01
    assert a != mechcheck.glyph_hash([moved])


def test_group_tree_and_naming_rules():
    def o(oid, name, path=(), gnames=None):
        d = {"id": oid, "type": "text", "name": name, "x": 0, "y": 0, "w": 1, "h": 1}
        if path:
            d["groupId"] = path[-1]
            if len(path) > 1:
                d["groupPath"] = list(path)
            d["groupNames"] = gnames or {g: f"组名{g}" for g in path}
        return d

    ok = [o("a", "甲", ["g1", "g2"]), o("b", "乙", ["g1", "g2"])]
    assert mechcheck.check(ok)["failed"] == []
    two_tops = [
        o("a", "甲", ["g1"]),
        o("b", "乙", ["g1"]),
        o("c", "丙", ["g9"]),
        o("d", "丁", ["g9"]),
    ]
    assert {"multiple_top_groups"} <= {
        f["detail"].get("reason") for f in mechcheck.check(two_tops)["failed"]
    }
    single = [o("a", "甲", ["g1", "g2"]), o("b", "乙", ["g1"])]
    assert "single_member_group" in {
        f["detail"].get("reason") for f in mechcheck.check(single)["failed"]
    }
    assert mechcheck.check([o("a", "甲", ["g1"])])["failed"] == []  # 顶层框先只有框线：放行
    deep = [
        o("a", "甲", ["g1", "g2", "g3", "g4", "g5"]),
        o("b", "乙", ["g1", "g2", "g3", "g4", "g5"]),
    ]
    assert "too_deep" in {f["detail"].get("reason") for f in mechcheck.check(deep)["failed"]}
    reasons = lambda objs: {f["detail"].get("reason") for f in mechcheck.check(objs)["failed"]}  # noqa: E731
    assert "unnamed" in reasons([{**o("a", "x"), "name": ""}])
    assert "generic_name" in reasons([o("a", "路径 3"), o("b", "Group 2")])
    assert "duplicate" in reasons([o("a", "甲"), o("b", "甲")])
    assert "unnamed_group" in reasons(
        [o("a", "甲", ["g1"], {"g1": " "}), o("b", "乙", ["g1"], {"g1": " "})]
    )


def test_path_quality_is_wired_to_the_free_path_checker():
    """自由路径（不带 `glyph`）过 6.A 的 `pathquality`：干净的不报，重合锚点 fail、锚点爆炸 fail。"""
    clean = mechcheck.check([_path("p", [RING_OUT])], require_names=False)
    assert not [f for f in clean["failed"] if f["check"] == "path_quality"] and not clean["unknown"]
    dup = {
        "closed": True,
        "nodes": [*RING_OUT["nodes"], dict(RING_OUT["nodes"][0])],
    }  # 最后一个锚点是第一个的复本
    bad = mechcheck.check([_path("q", [dup])], require_names=False)
    pq = [f for f in bad["failed"] if f["check"] == "path_quality"]
    assert pq and pq[0]["item"] == "q"  # 点名对象
    assert pq[0]["detail"]["issues"] and "simplify" in pq[0]["detail"]["next"]
    # 带 glyph 的图元路径不核（由参数重生成）
    g = {"kind": "k", "version": 1, "params": {}, "hash": mechcheck.glyph_hash([dup])}
    assert not [
        f
        for f in mechcheck.check([_path("r", [dup], glyph=g)], require_names=False)["failed"]
        if f["check"] == "path_quality"
    ]


def test_a_blob_that_looks_like_a_rect_is_refused_at_add_time(licensed):
    """端到端：组件回了一个其实是矩形的「图元」→ 可编辑性核对不过，整批不落地。"""
    rect_reply = {"version": 1, "subpaths": [{"closed": True, "nodes": RECT["nodes"]}]}
    res = _plan(BLOB, context={"mechanism_ask": _ask_with(rect_reply)})
    assert res["ok"] is False
    assert res["errors"] == [
        {
            "code": "not_editable",
            "index": 0,
            "field": "native_shape:" + res["errors"][0]["field"].split(":", 1)[1],
        }
    ]


# ---------------------------------------------------------------- 桌面改图助手 / 回归
def test_desktop_assistant_unlicensed_keeps_the_free_frame_and_drops_glyphs(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: False)
    out = vp.validate_ops({"ops": [_add(FRAME), _add(FRAME, MEMBRANE)], "unhandled": []}, _canvas())
    assert out["ops"] == [_add(FRAME)]  # 框照收
    assert (
        out["unhandled"][0]["code"] == "not_licensed" and "机理图" in out["unhandled"][0]["reason"]
    )


def test_desktop_assistant_keeps_a_licensed_frame(licensed):
    out = vp.validate_ops({"ops": [_add(FRAME)], "unhandled": []}, _canvas())
    assert len(out["ops"]) == 1 and out["unhandled"] == []


def test_ops_doc_mentions_the_three_kinds_everywhere_it_should():
    for word in ("frame", "glyph", "graph", "pro_required"):
        assert word in ve.OPS_DOC


def test_minted_objects_pass_the_agent_object_validator_without_a_glyph_field(licensed):
    """`glyph` 是字段表保留字段：Agent 自己写会被拒，只有引擎在校验之后写。"""
    bad = agentobjects.validate_new_objects([{**_path("p", [RING_OUT]), "glyph": {"kind": "x"}}])
    assert not bad["ok"] and bad["errors"][0]["code"] == "reserved_field"


def test_the_plugin_tool_layer_translates_pro_required_without_judging_licences():
    """工具层只转话：引擎回 `pro_required`，MCP 层不判权益（`license.has` 只在登记的模块里，`test_license_callsites` 看护）。"""
    from tavotto_mcp import server

    out = server._pro_required(mechanism.pro_required_result())  # noqa: SLF001
    assert out["structuredContent"]["code"] == "pro_required"
    assert out["structuredContent"]["learn_more"]["url"].startswith("https://www.tavotto.com/")
    assert (
        out["structuredContent"]["learn_more"]["url"] in out["content"][0]["text"]
    )  # 转给 Agent 的话里带链接
    assert "isError" not in out
    assert server._pro_required({"ok": True}) is None  # noqa: SLF001


def test_premium_page_url_matches_brand_ts():
    """`engine/brand.py` 与 `web/src/lib/brand.ts`：官网域名与高级版介绍页是同一个地址（严格同源对）。"""
    import re

    from tavotto.engine import brand

    ts = (Path(__file__).resolve().parent.parent / "web/src/lib/brand.ts").read_text(
        encoding="utf-8"
    )
    (site,) = re.findall(r"^export const WEBSITE_URL = '([^']+)'$", ts, re.M)
    zh, en = re.findall(r"locale === 'zh-CN' \? '(/zh/premium/)' : '(/premium/)'", ts)[0]
    assert brand.WEBSITE_URL == site
    assert (
        brand.premium_page_url("zh-CN") == site + zh
        and brand.premium_page_url("en-US") == site + en
    )
    assert brand.premium_page_url("zh") == site + zh and brand.premium_page_url(None) == site + en


def test_pro_required_picks_the_link_and_label_by_interface_language(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: False)
    res = ve.propose_memory(_canvas(), [_add(FRAME, MEMBRANE)], context={"locale": "en-US"})
    assert res["learn_more"] == {
        "label": "Learn about Premium",
        "url": "https://www.tavotto.com/premium/",
    }


def test_an_unrelated_free_annotation_next_to_a_frame_keeps_the_0208_rules(licensed):
    """可编辑性核对只作用于机理图成员：框外的普通标注（没有显示名）不被 naming 判掉。"""
    oval = {
        "kind": "shape",
        "name": "oval",
        "shape": "ellipse",
        "at": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [2, 2]},
        "size_mm": [6, 4],
    }
    alone = _plan(oval)
    assert alone["ok"], alone
    res = _plan(FRAME, oval)
    assert res["ok"], res
    assert len(res["changed"]) == 2  # 框线 + 椭圆
    # 框里的免费条目仍归核对管（有名字、进了框）
    inside = {**oval, "name": "oval2", "frame": "egf"}
    res2 = _plan(FRAME, inside, {**inside, "name": "oval3"})
    assert res2["ok"], res2


def _seg(a, b):
    return {"closed": False, "nodes": [{"p": a}, {"p": b}]}


def test_t_junctions_connect_in_both_directions():
    """20×10 mm：横线 y=0.5 与竖线 x=0.3 的上半段，竖线的下端点落在横线上——谁碰谁都算连通。"""
    horiz = _seg([0, 0.5], [1, 0.5])
    stem_up = _seg([0.3, 0.5], [0.3, 0])  # 起点在横线上
    stem_down = _seg([0.3, 0], [0.3, 0.5])  # 终点在横线上（反向）
    assert components_of([horiz, stem_up]) == 1
    assert components_of([horiz, stem_down]) == 1
    assert components_of([stem_up, horiz]) == 1 and components_of([stem_down, horiz]) == 1


def test_t_junction_on_a_curved_segment_and_near_misses():
    bow = {
        "closed": False,
        "nodes": [{"p": [0, 0.5], "out": [0.3, 0.0]}, {"p": [1, 0.5], "in": [0.7, 0.0]}],
    }
    # 曲线中点附近落一根竖线的端点：拍平后的折线上（容差内）算连通
    pts = mechcheck._flatten(_path("p", [bow])["subpaths"][0], 20, 10)  # noqa: SLF001
    x, y = pts[len(pts) // 2]
    stem = _seg([x / 20, y / 10], [x / 20, 1])
    assert components_of([bow, stem]) == 1
    # 端点贴近但没接触（0.5 mm，远大于 0.01 mm 容差）→ 不连通；两个方向都一样
    horiz = _seg([0, 0.5], [1, 0.5])
    near_up = _seg([0.3, 0.45], [0.3, 0])  # 20×10 mm 下离横线 0.5 mm
    near_down = _seg([0.3, 0], [0.3, 0.45])
    assert components_of([horiz, near_up]) == 2
    assert components_of([horiz, near_down]) == 2
    # 容差内（< 0.01 mm）的贴合算接上
    assert components_of([horiz, _seg([0.3, 0.5004], [0.3, 0])]) == 1


def test_reply_subpath_limit_is_256():
    nodes = [{"p": [0, 0]}, {"p": [1, 1]}]
    ok = {"version": 1, "subpaths": [{"closed": False, "nodes": nodes}] * 256}
    assert mechanism._check_reply(ok, "x")["subpaths"]  # noqa: SLF001
    with pytest.raises(mechanism.MechanismError):
        mechanism._check_reply({**ok, "subpaths": ok["subpaths"] + [ok["subpaths"][0]]}, "x")  # noqa: SLF001


# ---------------------------------------------------------------- 嵌入膜（ADR 0220 §五之二）
def _embed_ask(log, curv=0.0, protein_w=6.0):
    """替身 sidecar：膜给 `info`（自然尺寸、厚度、间距、中线采样与朝外法线），蛋白给 `info`（自然尺寸、跨膜段中点 / 宽）。"""

    def midline():
        pts = []
        for k in range(21):
            x = 40 * k / 20
            y = 4 + 10 * curv * ((x - 20) / 20) ** 2
            m = 20 * curv * (x - 20) / 400  # dy/dx
            n = (m**2 + 1) ** 0.5
            pts.append([x, y, m / n, -1 / n])
        return pts

    def ask(cmd, payload):
        log.append((cmd, copy.deepcopy(payload)))
        if cmd == "mechanism.catalog":
            return {
                "kinds": [
                    {
                        "kind": "membrane",
                        "versions": [1],
                        "default_size_mm": [40, 8],
                        "params": [{"name": "inserts", "type": "inserts", "default": []}],
                    },
                    {
                        "kind": "prot",
                        "versions": [1],
                        "default_size_mm": [protein_w, 12],
                        "params": [
                            {
                                "name": "membrane_mm",
                                "type": "number",
                                "min": 3.5,
                                "max": 5.5,
                                "default": 4.0,
                            }
                        ],
                    },
                ]
            }
        tri = [
            {"p": [0.1, 0.0]},
            {"p": [0.9, 0.1]},
            {"p": [1.0, 0.6]},
            {"p": [0.5, 1.0]},
            {"p": [0.0, 0.5]},
        ]
        if payload["kind"] == "membrane":
            return {
                "version": 1,
                "array": True,
                "subpaths": [
                    {"closed": False, "nodes": [{"p": [0, 0.2]}, {"p": [1, 0.3]}]},
                    {"closed": False, "nodes": [{"p": [0, 0.8]}, {"p": [1, 0.7]}]},
                ],
                "ports": {"center": [0.5, 0.5]},
                "info": {
                    "natural_size_mm": [40, 8],
                    "thickness_mm": 6.0,
                    "pitch_mm": 1.7,
                    "midline": midline(),
                },
            }
        return {
            "version": 1,
            "subpaths": [{"closed": True, "nodes": tri}],
            "ports": {"membrane": [0.5, 0.58]},
            "info": {
                "natural_size_mm": [protein_w, 12],
                "tm_mid_mm": [protein_w / 2, 7.0],
                "tm_width_mm": 2.0,
                "tm_length_mm": payload["params"]["membrane_mm"],
            },
        }

    return ask


MEM = {"kind": "glyph", "name": "mem", "glyph": "membrane", "at_mm": [10, 30]}


def _prot(name, **kw):
    return {
        "kind": "glyph",
        "name": name,
        "glyph": "prot",
        "embed": {"membrane": "mem", **kw.pop("embed", {})},
        **kw,
    }


def _prep(*items, log=None, curv=0.0, **kw):
    return mechanism.prepare(
        list(items), ask=_embed_ask(log if log is not None else [], curv, **kw)
    )


@pytest.fixture
def lic(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: True)


def _by(prep):
    return {o["id"]: o for o in prep.objects}


def test_embedded_proteins_without_at_get_equal_gaps_by_their_actual_widths(lic):
    prep = _prep(MEM, _prot("a"), _prot("b"), _prot("c"))
    o = _by(prep)
    # 膜 x = 10..50（长 40），蛋白宽 6（跨膜段中点在框内 x = 3）；空隙 = (40 − 3·6) / 4 = 5.5 ≥ 2 个脂质间距 3.4
    lefts = [o[n]["x"] for n in ("a", "b", "c")]
    assert [round(v, 6) for v in lefts] == [10 + 5.5, 10 + 5.5 + 6 + 5.5, 10 + 5.5 + 2 * (6 + 5.5)]
    rights = [o[n]["x"] + o[n]["w"] for n in ("a", "b", "c")]
    gaps = [lefts[0] - 10, lefts[1] - rights[0], lefts[2] - rights[1], 50 - rights[2]]
    assert all(abs(g - 5.5) < 1e-9 for g in gaps)  # 首尾与蛋白之间的空隙都相等
    for n in ("a", "b", "c"):
        assert abs((o[n]["y"] + 7.0) - 34.0) < 1e-6  # 跨膜段中点落在膜中线 y = 30 + 4
        assert "rotationDeg" not in o[n]
    assert prep.free_items == []


def test_unequal_protein_widths_still_get_equal_gaps(lic):
    """宽度不同的蛋白：空隙 = (膜长 − 蛋白总宽) / (n + 1)，各蛋白按**自己**的实际宽度排。"""
    base = _embed_ask([])
    widths = iter([4.0, 10.0])

    def varied(cmd, payload):
        if cmd == "mechanism.glyph" and payload["kind"] == "prot":
            return _embed_ask([], protein_w=next(widths))(cmd, payload)
        return base(cmd, payload)

    o = _by(mechanism.prepare([MEM, _prot("a"), _prot("b")], ask=varied))
    gap = (40 - 14) / 3
    assert abs((o["a"]["x"] - 10) - gap) < 1e-6
    assert abs(o["b"]["x"] - (o["a"]["x"] + o["a"]["w"] + gap)) < 1e-6
    assert abs((50 - (o["b"]["x"] + o["b"]["w"])) - gap) < 1e-6


def test_membrane_keeps_at_least_two_lipids_free_at_both_ends(lic):
    for at in (0.1, 0.9):  # 蛋白框离膜端不到 2 个脂质间距（3.4 mm）
        with pytest.raises(mechanism.MechanismError):
            _prep(MEM, _prot("a", embed={"at": at}))
    with pytest.raises(mechanism.MechanismError):  # 空隙放不下：8 个蛋白
        _prep(MEM, *[_prot(f"p{i}") for i in range(8)])
    with pytest.raises(
        mechanism.MechanismError
    ):  # 空隙不到 2 个脂质间距：5 × 6 = 30，(40 − 30) / 6 = 1.67 < 3.4
        _prep(MEM, *[_prot(f"q{i}") for i in range(5)])


def test_given_at_is_respected_and_the_membrane_is_asked_again_with_the_cutouts(lic):
    log = []
    prep = _prep(MEM, _prot("a", embed={"at": 0.25}), _prot("b", embed={"at": 0.7}), log=log)
    glyph_calls = [p for c, p in log if c == "mechanism.glyph" and p["kind"] == "membrane"]
    assert len(glyph_calls) == 2  # 第一次不带让位（量厚度 / 中线），第二次带
    assert glyph_calls[0]["params"]["inserts"] == []
    # 让位窗口 = 跨膜部分的总宽 2.0（贴杆的净空与窗口之间的重新排布由膜自己做）
    assert glyph_calls[1]["params"]["inserts"] == [
        {"at": 0.25, "width_mm": 2.0},
        {"at": 0.7, "width_mm": 2.0},
    ]
    assert prep.glyph_meta["mem"]["params"]["inserts"] == glyph_calls[1]["params"]["inserts"]
    o = _by(prep)
    assert (
        abs(o["a"]["x"] + 3.0 - (10 + 40 * 0.25)) < 1e-6
        and abs(o["b"]["x"] + 3.0 - (10 + 40 * 0.7)) < 1e-6
    )


def test_transmembrane_length_follows_the_membrane_thickness_clamped_to_the_spec(lic):
    log = []
    _prep(MEM, _prot("a"), log=log)
    prot_call = next(p for c, p in log if c == "mechanism.glyph" and p["kind"] == "prot")
    assert prot_call["params"]["membrane_mm"] == 5.5  # 膜厚 6.0 夹进 [3.5, 5.5]
    log2 = []
    _prep(MEM, _prot("a", params={"membrane_mm": 4.2}), log=log2)  # 用户自己给了就用用户的
    assert (
        next(p for c, p in log2 if c == "mechanism.glyph" and p["kind"] == "prot")["params"][
            "membrane_mm"
        ]
        == 4.2
    )


def test_overlap_out_of_range_and_bad_embeds_are_refused(lic):
    for items in (
        (MEM, _prot("a", embed={"at": 0.5}), _prot("b", embed={"at": 0.52})),  # 两个蛋白重叠
        (MEM, _prot("a", embed={"at": 0.0})),  # 一半在膜外
        (MEM, _prot("a", embed={"at": 1.0})),
        (MEM, _prot("a", embed={"at": 0.05})),  # 离膜端不到 2 个脂质
        (MEM, _prot("a", embed={"membrane": "nope"})),  # 没有这条膜
        (MEM, {**_prot("a"), "at_mm": [1, 1]}),  # 嵌入的位置由宿主算，不许再给
        (MEM, _prot("a", embed={"at": 2})),
        (
            MEM,
            {"kind": "glyph", "name": "p0", "glyph": "prot", "at_mm": [0, 0]},
            _prot("a", embed={"membrane": "p0"}),
        ),  # 目标不是膜
    ):
        with pytest.raises(mechanism.MechanismError) as e:
            _prep(*items)
        assert e.value.code == "bad_item", items


def test_on_a_curved_membrane_proteins_are_rotated_along_the_surface_normal(lic):
    prep = _prep(
        MEM,
        _prot("a", embed={"at": 0.2}),
        _prot("b", embed={"at": 0.5}),
        _prot("c", embed={"at": 0.8}),
        curv=0.3,
    )
    o = _by(prep)
    assert "rotationDeg" not in o["b"]  # 弧顶：法线朝正上方
    assert (
        o["a"]["rotationDeg"] < -1 < 1 < o["c"]["rotationDeg"]
    )  # 左边向左倾、右边向右倾（顺时针为正）
    assert abs(o["a"]["rotationDeg"] + o["c"]["rotationDeg"]) < 1e-6  # 对称
    # 跨膜段中点仍然落在中线上：把中点旋转回画布坐标，应在 y = 30 + 4 + 10·curv·((x−20)/20)²
    import math

    for n, at in (("a", 0.2), ("c", 0.8)):
        g = o[n]
        th = math.radians(g["rotationDeg"])
        cx, cy = g["x"] + g["w"] / 2, g["y"] + g["h"] / 2
        dx, dy = 3.0 - g["w"] / 2, 7.0 - g["h"] / 2
        mx, my = (
            cx + dx * math.cos(th) - dy * math.sin(th),
            cy + dx * math.sin(th) + dy * math.cos(th),
        )
        xm = mx - 10  # 膜框内 x
        assert abs(my - (30 + 4 + 10 * 0.3 * ((xm - 20) / 20) ** 2)) < 0.15


def test_embedded_objects_keep_membrane_first_and_pass_the_editability_checks(lic):
    prep = _prep(MEM, _prot("a"), _prot("b"))
    assert [o["id"] for o in prep.objects] == ["mem", "a", "b"]
    objs = [
        {**o, "glyph": prep.glyph_meta[o["id"]]} for o in prep.objects
    ]  # `glyph` 字段在校验之后才写（保留字段）
    assert not mechcheck.check(objs, catalog=prep.catalog, require_names=False)["failed"]
    assert any("嵌在膜" in e for e in prep.echo)


def test_membrane_inserts_param_is_validated(lic):
    ok = _prep({**MEM, "params": {"inserts": [{"at": 0.5, "width_mm": 3}]}})
    assert ok.glyph_meta["mem"]["params"]["inserts"] == [{"at": 0.5, "width_mm": 3.0}]
    for bad in (
        [{"at": 2, "width_mm": 3}],
        [{"at": 0.5}],
        [{"at": 0.5, "width_mm": 0}],
        "x",
        [{"at": 0.5, "width_mm": 3, "z": 1}],
        [{"at": 0.1, "width_mm": 1}] * 17,
    ):
        with pytest.raises(mechanism.MechanismError):
            _prep({**MEM, "params": {"inserts": bad}})


@pytest.mark.parametrize(
    "info",
    [
        {"midline": [[0, 0, 0]]},
        {"midline": "x"},
        {"natural_size_mm": [0, 3]},
        {"zzz": 1},
        {"thickness_mm": -1},
        {"tm_mid_mm": [float("nan"), 1]},
        {"midline": [[0, 0, 0, -1]] * 66},
    ],
)
def test_reply_info_is_a_closed_set(info):
    with pytest.raises(mechanism.MechanismError):
        mechanism._check_info(info, "x")  # noqa: SLF001


# ---------------------------------------------------------------- Agent 现写的配方（随条目发，替代 kind）
AGENT_RECIPE = {
    "recipe_format": 1,
    "kind": "bulb",
    "versions": [1],
    "params": [
        {"name": "radius", "type": "number", "min": 1.0, "max": 4.0, "default": 2.0},
        {"name": "flag", "type": "bool", "default": True},
    ],
    "steps": [{"let": {"r": "params.radius"}}],
    "emit": ["a"],
}


def _recipe_ask(log, bad=None):
    tri = [
        {"p": [0.1, 0.0]},
        {"p": [0.9, 0.1]},
        {"p": [1.0, 0.6]},
        {"p": [0.5, 1.0]},
        {"p": [0.0, 0.5]},
    ]

    def ask(cmd, payload):
        log.append((cmd, copy.deepcopy(payload)))
        if cmd == "mechanism.catalog":
            return {"kinds": []}
        if bad:
            raise mechanism.prohost.ProUnavailable("bad_request", bad)
        return {
            "version": payload["version"],
            "subpaths": [{"closed": True, "nodes": tri}],
            "ports": {},
            "info": {"natural_size_mm": [5, 6]},
        }

    return ask


def _rit(**kw):
    return {
        "kind": "glyph",
        "name": "bulb1",
        "recipe": copy.deepcopy(AGENT_RECIPE),
        "at_mm": [1, 2],
        **kw,
    }


def test_a_recipe_item_sends_the_recipe_instead_of_a_kind_and_records_it_with_a_hash(lic):
    log = []
    prep = mechanism.prepare([_rit(params={"radius": 3.0})], ask=_recipe_ask(log))
    call = next(p for c, p in log if c == "mechanism.glyph")
    assert "kind" not in call and call["recipe"] == AGENT_RECIPE
    assert (
        call["params"] == {"radius": 3.0, "flag": True} and call["version"] == 1
    )  # 参数按配方自己的规格补默认值
    meta = prep.glyph_meta["bulb1"]
    assert meta["recipe"] == AGENT_RECIPE and meta["recipe_format"] == 1 and meta["kind"] == "bulb"
    assert (
        meta["recipe_hash"] == mechcheck.recipe_hash(AGENT_RECIPE)
        and len(meta["recipe_hash"]) == 16
    )
    (obj,) = prep.objects
    assert (obj["w"], obj["h"]) == (5, 6)  # 外框取回包里的自然尺寸
    # 元数据过可编辑性核对；配方全文被改了（hash 对不上）= 不过
    ok = {**obj, "glyph": meta}
    assert not mechcheck.check([ok], catalog={}, require_names=False)["failed"]
    tampered = {**ok, "glyph": {**meta, "recipe": {**AGENT_RECIPE, "steps": []}}}
    assert ("glyph_meta", "recipe_hash_mismatch") in {
        (f["check"], f["detail"]["reason"])
        for f in mechcheck.check([tampered], catalog={}, require_names=False)["failed"]
    }


def test_recipe_hash_covers_the_whole_text_and_ignores_key_order():
    a = mechcheck.recipe_hash(AGENT_RECIPE)
    assert a == mechcheck.recipe_hash(dict(reversed(list(AGENT_RECIPE.items()))))
    assert a != mechcheck.recipe_hash({**AGENT_RECIPE, "emit": ["b"]})


def test_bad_recipe_items_are_refused_by_the_host_before_asking(lic):
    big = {**AGENT_RECIPE, "style_hint": {"zh": "x" * 17000}}
    for item in (
        {**_rit(), "glyph": "membrane"},  # 二选一
        {k: v for k, v in _rit().items() if k != "recipe"},  # 两个都没有
        _rit(recipe={**AGENT_RECIPE, "recipe_format": 2}),
        _rit(recipe={k: v for k, v in AGENT_RECIPE.items() if k != "recipe_format"}),
        _rit(recipe=big),
        _rit(recipe="x"),
        _rit(recipe={**AGENT_RECIPE, "versions": []}),
        _rit(recipe={**AGENT_RECIPE, "params": "x"}),
        _rit(params={"radius": 9}),  # 超出配方自己规格里的范围
        _rit(params={"nope": 1}),
        _rit(version=2),
    ):
        log = []
        with pytest.raises(mechanism.MechanismError) as e:
            mechanism.prepare([item], ask=_recipe_ask(log))
        assert e.value.code in ("bad_item", "unsupported_version"), item
        assert not [c for c, _p in log if c == "mechanism.glyph"]  # 没问组件


def test_a_component_bad_request_becomes_a_named_bad_item_not_a_missing_component(lic):
    with pytest.raises(mechanism.MechanismError) as e:
        mechanism.prepare([_rit()], ask=_recipe_ask([], bad="constraint ecd_icd_height_ratio"))
    assert (
        e.value.code == "bad_item"
        and "bulb1" in e.value.field
        and "constraint ecd_icd_height_ratio" in e.value.field
    )

    # 内置图元的参数 / 约束不合格也一样点名（`membrane` 的请求被组件回了 bad_request）
    base = _embed_ask([])

    def refuse(cmd, payload):
        if cmd == "mechanism.glyph":
            raise mechanism.prohost.ProUnavailable("bad_request", "param curvature")
        return base(cmd, payload)

    with pytest.raises(mechanism.MechanismError) as e2:
        mechanism.prepare([MEM], ask=refuse)
    assert e2.value.code == "bad_item" and "mem: param curvature" == e2.value.field


# ---------------------------------------------------------------- 真几何（pro 回包的拷贝）过本仓库的核对
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "pro_glyph_replies.json").read_text(encoding="utf-8")
)


def _as_object(case, with_glyph):
    r = case["reply"]
    w, h = r["info"]["natural_size_mm"]
    o = {
        "id": "g1",
        "type": "path",
        "x": 0,
        "y": 0,
        "w": w,
        "h": h,
        "subpaths": r["subpaths"],
        "strokePt": 1,
        "color": "#000000",
        "fill": None,
        "name": "图元",
    }
    if with_glyph:
        meta = {
            "kind": case["kind"],
            "version": 1,
            "params": case["params"],
            "hash": mechcheck.glyph_hash(r["subpaths"]),
        }
        if r.get("array"):
            meta["array"] = True
        o["glyph"] = meta
    return o


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_the_real_pro_geometry_passes_path_quality_and_the_editability_checks(case):
    """`tests/fixtures/pro_glyph_replies.json` 是 pro 仓库真图元的回包（来源提交写在文件的 `_source`）：
    把它当自由路径交给 6.A 的 `pathquality`，不许有 fail；带 `glyph` 元数据过 `mechcheck`，不许有 failed。"""
    from tavotto.engine import pathquality

    free = pathquality.check_path(_as_object(case, with_glyph=False))
    assert free["verdict"] in (pathquality.CLEAN, pathquality.ACCEPTABLE), free["issues"]
    obj = _as_object(case, with_glyph=True)
    verdict = mechcheck.check([obj], catalog={case["kind"]: {"versions": [1]}}, require_names=False)
    assert verdict["failed"] == [], verdict["failed"]
    assert FIXTURE["_source"].startswith("Tavotto/pro ")


def _real_ask(log=None, curv=0.0, length=40.0):
    """用 pro 的真回包当替身：蛋白取固定的二聚体 / 单体真几何，膜用解析中线（弧长 = length）。"""
    by = {c["name"]: c["reply"] for c in FIXTURE["cases"]}
    dimer, mono = by["rtk/default"], by["rtk/monomer"]

    def ask(cmd, payload):
        if log is not None:
            log.append((cmd, copy.deepcopy(payload)))
        if cmd == "mechanism.catalog":
            return {
                "kinds": [
                    {
                        "kind": "membrane",
                        "versions": [1],
                        "default_size_mm": [length, 8],
                        "params": [
                            {"name": "inserts", "type": "inserts", "default": []},
                            {
                                "name": "curvature",
                                "type": "number",
                                "min": -0.35,
                                "max": 0.35,
                                "default": 0.0,
                            },
                            {
                                "name": "thickness_r",
                                "type": "number",
                                "min": 4.0,
                                "max": 6.0,
                                "default": 6.0,
                            },
                            {
                                "name": "head_radius_mm",
                                "type": "number",
                                "min": 0.4,
                                "max": 1.5,
                                "default": 0.8,
                            },
                        ],
                    },
                    {
                        "kind": "receptor_rtk",
                        "versions": [1],
                        "default_size_mm": [10, 20],
                        "params": [
                            {"name": "dimer", "type": "bool", "default": True},
                            {
                                "name": "membrane_mm",
                                "type": "number",
                                "min": 3.5,
                                "max": 5.5,
                                "default": 4.0,
                            },
                        ],
                    },
                ]
            }
        if payload["kind"] == "receptor_rtk":
            return copy.deepcopy(dimer if payload["params"].get("dimer", True) else mono)
        pts = []
        for k in range(21):
            x = length * k / 20
            m = 20 * curv * (x - length / 2) / (length / 2) ** 2 * 1.0
            n = (m * m + 1) ** 0.5
            pts.append([x, 4 + 10 * curv * ((x - length / 2) / (length / 2)) ** 2, m / n, -1 / n])
        return {
            "version": 1,
            "array": True,
            "subpaths": [
                {"closed": False, "nodes": [{"p": [0, 0.2]}, {"p": [1, 0.3]}]},
                {"closed": False, "nodes": [{"p": [0, 0.8]}, {"p": [1, 0.7]}]},
            ],
            "ports": {"center": [0.5, 0.5]},
            "info": {
                "natural_size_mm": [length, 8],
                "thickness_mm": 6.0,
                "pitch_mm": 1.7,
                "midline": pts,
                "core_half_width_mm": 0.48,
            },
        }

    return ask


def _scene(curv, length, n=3):
    items = [
        {
            "kind": "glyph",
            "name": "mem",
            "glyph": "membrane",
            "at_mm": [0, 20],
            "params": {"curvature": curv},
        }
    ]
    items += [
        {
            "kind": "glyph",
            "name": f"r{i}",
            "glyph": "receptor_rtk",
            "embed": {"membrane": "mem"},
            "params": {"dimer": i != n - 1},
        }
        for i in range(n)
    ]
    return mechanism.prepare(items, ask=_real_ask(curv=curv, length=length))


def _two_dimers(curv, length, distance):
    a1, a2 = 0.5 - distance / 2 / length, 0.5 + distance / 2 / length
    items = [
        {
            "kind": "glyph",
            "name": "mem",
            "glyph": "membrane",
            "at_mm": [0, 20],
            "params": {"curvature": curv},
        },
        {
            "kind": "glyph",
            "name": "r0",
            "glyph": "receptor_rtk",
            "embed": {"membrane": "mem", "at": a1},
        },
        {
            "kind": "glyph",
            "name": "r1",
            "glyph": "receptor_rtk",
            "embed": {"membrane": "mem", "at": a2},
        },
    ]
    return mechanism.prepare(items, ask=_real_ask(curv=curv, length=length))


def test_converging_proteins_on_a_downward_curved_membrane_are_refused_when_their_real_outlines_overlap(
    lic,
):
    """两个真二聚体（自然宽约 12.6 mm）相距 13.5 mm：外框盒不交叠，但向下凸（法线在外侧会聚）时旋转后的真实外廓交叠 → bad_item（点名后一个）；
    同样的距离在直膜与向上凸（发散）上没有问题。"""
    with pytest.raises(mechanism.MechanismError) as e:
        _two_dimers(-0.35, 40.0, 13.5)
    assert e.value.code == "bad_item" and e.value.field == "r1"
    assert len(_two_dimers(0.35, 40.0, 13.5).objects) >= 3
    assert len(_two_dimers(0.0, 40.0, 13.5).objects) >= 3
    assert len(_two_dimers(-0.35, 40.0, 15.0).objects) >= 3  # 拉开一点就放得下（或把膜拉长）


def test_three_real_dimers_fit_on_a_long_enough_membrane_whichever_way_it_bends(lic):
    for curv in (-0.2, 0.0, 0.25):
        prep = _scene(curv, 56.0)
        assert [o["id"] for o in prep.objects if o["id"].startswith("r")] == ["r0", "r1", "r2"]


def test_straight_membrane_with_real_dimers_never_trips_the_overlap_check(lic):
    assert len([o for o in _scene(0.0, 50.0).objects if o["id"].startswith("r")]) == 3


# ---------------------------------------------------------------- 疏水核心底带与样式预设
def _core(prep):
    return next(o for o in prep.objects if o["id"] == "mem__core")


def test_a_membrane_lands_with_a_separate_hydrophobic_core_band_under_the_lipids(lic):
    prep = _scene(0.0, 50.0)
    ids = [o["id"] for o in prep.objects]
    assert ids.index("mem__core") < ids.index("mem")  # 叠在磷脂下面：先放
    core, mem = _core(prep), next(o for o in prep.objects if o["id"] == "mem")
    assert core["name"] == "疏水核心" and (core["x"], core["y"], core["w"], core["h"]) == (
        mem["x"],
        mem["y"],
        mem["w"],
        mem["h"],
    )
    assert (
        core["stroke"] is False
        and core["fill"] != mem["fill"]
        and core["fill"] == mechanism._lighten(mem["fill"])
    )  # noqa: SLF001
    assert (
        prep.glyph_meta["mem__core"]["host_made"] is True
        and prep.glyph_meta["mem__core"]["array"] is True
    )
    # 宽 = 上下两排头基圆心之间（层厚 6 r × 头半径 0.8 = 4.8 mm）：每段多边形的上下边相距 4.8 mm，衬在尾巴后面
    seg = core["subpaths"][0]["nodes"]
    half = len(seg) // 2
    assert abs(abs(seg[half - 1]["p"][1] - seg[half]["p"][1]) * core["h"] - 4.8) < 1e-3
    # 两端收在最外侧磷脂头的圆心处（半个脂质间距 1.7 / 2 = 0.85 mm 内），不探出膜端
    xs = [n["p"][0] * core["w"] for sp in core["subpaths"] for n in sp["nodes"]]
    assert min(xs) == pytest.approx(0.85, abs=1e-3) and max(xs) == pytest.approx(
        50.0 - 0.85, abs=1e-3
    )
    # 让位处同样断开：三个蛋白 = 四段
    assert len(core["subpaths"]) == 4
    objs = [{**o, "glyph": prep.glyph_meta[o["id"]]} for o in prep.objects]
    assert not mechcheck.check(objs, catalog=prep.catalog, require_names=False)["failed"]


def test_core_band_is_one_unbroken_segment_without_embedded_proteins_and_can_be_turned_off(lic):
    one = mechanism.prepare(
        [{"kind": "glyph", "name": "mem", "glyph": "membrane", "at_mm": [0, 0]}], ask=_real_ask()
    )
    assert len(_core(one)["subpaths"]) == 1
    off = mechanism.prepare(
        [{"kind": "glyph", "name": "mem", "glyph": "membrane", "at_mm": [0, 0], "core": False}],
        ask=_real_ask(),
    )
    assert "mem__core" not in [o["id"] for o in off.objects]
    with pytest.raises(mechanism.MechanismError):
        mechanism.prepare(
            [{"kind": "glyph", "name": "mem", "glyph": "membrane", "at_mm": [0, 0], "core": 1}],
            ask=_real_ask(),
        )


def test_old_components_without_a_midline_just_get_no_band(lic):
    base = _real_ask()

    def old(cmd, payload):
        r = base(cmd, payload)
        if cmd == "mechanism.glyph" and payload["kind"] == "membrane":
            r["info"].pop("midline")
        return r

    prep = mechanism.prepare(
        [{"kind": "glyph", "name": "mem", "glyph": "membrane", "at_mm": [0, 0]}], ask=old
    )
    assert [o["id"] for o in prep.objects] == ["mem"]


def test_style_presets_are_defaults_the_user_can_override(lic):
    prep = _scene(0.0, 50.0)
    mem, rec = (
        next(o for o in prep.objects if o["id"] == "mem"),
        next(o for o in prep.objects if o["id"] == "r0"),
    )
    assert (mem["strokePt"], mem["fill"]) == (0.6, "#f3dcc0") and (
        rec["strokePt"],
        rec["fill"],
    ) == (0.85, "#d4dde6")
    assert mem["strokePt"] < rec["strokePt"]  # 膜描边比蛋白细，退到蛋白后面
    own = mechanism.prepare(
        [
            {
                "kind": "glyph",
                "name": "mem",
                "glyph": "membrane",
                "at_mm": [0, 0],
                "style": {"strokePt": 2.0, "fill": "#ffffff"},
            }
        ],
        ask=_real_ask(),
    )
    m2 = next(o for o in own.objects if o["id"] == "mem")
    assert (m2["strokePt"], m2["fill"]) == (2.0, "#ffffff")
    assert _core(own)["fill"] == "#ffffff"[:0] + mechanism._lighten("#ffffff")  # noqa: SLF001


def test_core_band_follows_a_stretched_membrane_not_the_natural_size(lic):
    """Codex #2384 P1：膜给了 `size_mm` 拉伸后，底带的中线要跟着缩放（40×8 拉到 80×16：底带约 80 mm 长、居中在 y = 8）。"""
    prep = mechanism.prepare(
        [
            {
                "kind": "glyph",
                "name": "mem",
                "glyph": "membrane",
                "at_mm": [0, 0],
                "size_mm": [80, 16],
            }
        ],
        ask=_real_ask(),
    )
    core = _core(prep)
    xs = [p["p"][0] * core["w"] for sp in core["subpaths"] for p in sp["nodes"]]
    ys = [p["p"][1] * core["h"] for sp in core["subpaths"] for p in sp["nodes"]]
    assert max(xs) - min(xs) > 80 * 0.9  # 不是只有 40 mm
    assert abs((max(ys) + min(ys)) / 2 - 8.0) < 0.5  # 中线在拉伸后的 y = 8，不是 4


def test_embedded_protein_measures_follow_its_own_size_mm(lic):
    """Codex #2384 P1：蛋白给了 `size_mm`（6×12 拉到 12×24）时，跨膜中点按缩放后的 (6, 14) 对准中线 y = 34，间距宽度也按 12。"""
    prep = _prep(MEM, _prot("a", size_mm=[12, 24]))
    a = _by(prep)["a"]
    assert abs((a["y"] + 14.0) - 34.0) < 1e-6
    assert abs((a["x"] + 6.0) - 30.0) < 1e-6  # 单个蛋白居中（膜 x = 10..50 的中点）


def test_ellipse_check_looks_at_the_handles_not_just_the_anchors():
    """Codex #2384 P2：四个中点锚点 + 手柄不对（短手柄的圆角菱形 / 手柄贴锚点的正菱形）不是椭圆，不该被 native_shape 拦。"""
    k = 0.5522847498

    def quad(f):
        return {
            "closed": True,
            "nodes": [
                {"p": [0.5, 0], "in": [0.5 - f / 2, 0], "out": [0.5 + f / 2, 0]},
                {"p": [1, 0.5], "in": [1, 0.5 - f / 2], "out": [1, 0.5 + f / 2]},
                {"p": [0.5, 1], "in": [0.5 + f / 2, 1], "out": [0.5 - f / 2, 1]},
                {"p": [0, 0.5], "in": [0, 0.5 + f / 2], "out": [0, 0.5 - f / 2]},
            ],
        }

    assert mechcheck._fits_native(_path("p", [quad(k)])) == "ellipse"  # noqa: SLF001
    assert mechcheck._fits_native(_path("p", [quad(0.15)])) is None  # noqa: SLF001
    assert mechcheck._fits_native(_path("p", [quad(0.0)])) is None  # noqa: SLF001


# ---- Codex #2384 P1（局部拉伸）：弯膜非等比拉伸时，让位窗口宽度按插入点处的局部弧长映射换算 ----
def _stretched_scene(curv, size, at=None, log=None):
    items = [
        {
            "kind": "glyph",
            "name": "mem",
            "glyph": "membrane",
            "at_mm": [0, 20],
            "size_mm": size,
            "params": {"curvature": curv},
        },
        {
            "kind": "glyph",
            "name": "r0",
            "glyph": "receptor_rtk",
            "embed": {"membrane": "mem", **({"at": at} if at is not None else {})},
            "params": {"dimer": False},
        },
    ]
    return mechanism.prepare(items, ask=_real_ask(log=log, curv=curv, length=40.0))


def _last_inserts(log):
    mem = [p for cmd, p in log if cmd == "mechanism.glyph" and p["kind"] == "membrane"]
    return mem[-1]["params"]["inserts"]


def test_local_stretch_at_a_near_horizontal_midpoint_keeps_the_full_opening(lic):
    """曲率 0.3、膜 size_mm=[40,80]（自然 40×8，纵向 ×10）、蛋白在 0.5：该处切线近水平，局部拉伸 ≈ 1，
    画布上的开口（= 跨膜宽）应 ≈ 原宽，而不是被平均拉伸倍数压到约一半。"""
    log: list = []
    prep = _stretched_scene(0.3, [40, 80], at=0.5, log=log)
    ins = _last_inserts(log)[0]
    ref = _real_ask()
    tm_w = ref("mechanism.glyph", {"kind": "receptor_rtk", "params": {"dimer": False}})["info"][
        "tm_width_mm"
    ]
    assert ins["at"] == pytest.approx(0.5, abs=1e-3)
    assert ins["width_mm"] == pytest.approx(tm_w, rel=0.02)  # 自然宽 = 画布宽 / ≈1
    core = _core(prep)
    xs = sorted({round(p["p"][0] * core["w"], 6) for sp in core["subpaths"] for p in sp["nodes"]})
    ends = []
    for sp in core["subpaths"]:
        px = [p["p"][0] * core["w"] for p in sp["nodes"]]
        ends.append((min(px), max(px)))
    ends.sort()
    assert len(ends) == 2
    assert ends[1][0] - ends[0][1] == pytest.approx(tm_w, rel=0.05)  # 底带开口 ≈ 跨膜宽
    assert xs  # 有几何


def test_uniform_stretch_local_equals_average(lic):
    """等比拉伸（40×8 → 80×16）：局部 = 平均 = 2，自然宽 = 画布宽 / 2。"""
    log: list = []
    _stretched_scene(0.3, [80, 16], at=0.5, log=log)
    tm_w = _real_ask()("mechanism.glyph", {"kind": "receptor_rtk", "params": {"dimer": False}})[
        "info"
    ]["tm_width_mm"]
    assert _last_inserts(log)[0]["width_mm"] == pytest.approx(tm_w / 2, rel=0.02)


def test_local_k_follows_the_tangent_direction_and_remap_is_monotone():
    import math as _m

    # 圆弧中线：自然尺寸下 θ 从 −45° 到 +45°，半径 10；法线 = (sin θ, −cos θ) 朝外（上）
    n = 21
    samples = []
    for i in range(n):
        th = _m.radians(-45 + 90 * i / (n - 1))
        samples.append([10 * _m.sin(th), 10 - 10 * _m.cos(th), _m.sin(th), -_m.cos(th)])
    accn = mechanism._midline_len(samples, 1.0, 1.0)  # noqa: SLF001
    sx, sy = 1.0, 3.0
    # 中点：切线水平 → k = sx；两端（θ = ±45°）：切线 45° → k = hypot(sx, sy) / √2
    assert mechanism._local_k(samples, accn, 0.5, sx, sy) == pytest.approx(sx, abs=1e-6)  # noqa: SLF001
    assert mechanism._local_k(samples, accn, 1.0, sx, sy) == pytest.approx(  # noqa: SLF001
        _m.hypot(sx, sy) / _m.sqrt(2), abs=1e-6
    )
    assert mechanism._local_k(samples, accn, 0.0, sx, sy) == pytest.approx(  # noqa: SLF001
        _m.hypot(sx, sy) / _m.sqrt(2), abs=1e-6
    )
    acc = mechanism._midline_len(samples, sx, sy)  # noqa: SLF001
    vals = [mechanism._remap(accn, acc, accn[-1] * f / 10) for f in range(11)]  # noqa: SLF001
    assert (
        vals == sorted(vals)
        and vals[0] == pytest.approx(0.0)
        and vals[-1] == pytest.approx(acc[-1])
    )
    # 回来
    assert mechanism._remap(acc, accn, vals[4]) == pytest.approx(accn[-1] * 0.4, abs=1e-9)  # noqa: SLF001


def test_local_stretch_near_45_degrees_uses_the_tangent_at_the_insertion_point(lic):
    """陡弧（圆弧 ±40°）上、非等比拉伸（纵向 ×3）：插入点接近 45° 处，局部拉伸 = √(sx²cos²θ + sy²sin²θ)，
    给组件的自然宽度 × 局部倍数 = 画布上的跨膜宽；用平均倍数换算会明显偏离。"""
    import math as _m

    base = _real_ask()
    R, TH = 28.0, _m.radians(40)

    def ask(cmd, payload):
        r = base(cmd, payload)
        if cmd == "mechanism.glyph" and payload["kind"] == "membrane":
            n = 41
            pts = []
            for i in range(n):
                th = -TH + 2 * TH * i / (n - 1)
                pts.append(
                    [
                        20 + R * _m.sin(th),
                        12 + R * (1 - _m.cos(th)),
                        _m.sin(th),
                        -_m.cos(th),
                    ]
                )
            r["info"]["midline"] = pts
            r["info"]["natural_size_mm"] = [40, 20]
        return r

    log: list = []

    def logged(cmd, payload):
        log.append((cmd, copy.deepcopy(payload)))
        return ask(cmd, payload)

    items = [
        {
            "kind": "glyph",
            "name": "mem",
            "glyph": "membrane",
            "at_mm": [0, 0],
            "size_mm": [40, 60],
            "params": {"curvature": 0.3},
        },
        {
            "kind": "glyph",
            "name": "r0",
            "glyph": "receptor_rtk",
            "embed": {"membrane": "mem", "at": 0.78},
            "params": {"dimer": False},
        },
    ]
    mechanism.prepare(items, ask=logged)
    ins = _last_inserts(log)[0]
    tm_w = base("mechanism.glyph", {"kind": "receptor_rtk", "params": {"dimer": False}})["info"][
        "tm_width_mm"
    ]
    th = -TH + 2 * TH * ins["at"]  # 圆弧按弧长均匀：自然占比 → 角度
    sx, sy = 1.0, 3.0
    k_local = _m.sqrt((sx * _m.cos(th)) ** 2 + (sy * _m.sin(th)) ** 2)
    assert abs(_m.degrees(th)) > 25  # 确实在陡处
    assert ins["width_mm"] * k_local == pytest.approx(tm_w, rel=0.03)
    k_avg = _m.sqrt((sx) ** 2 + (sy) ** 2) / 1.0  # 粗略的平均量级，仅用来说明两者不同
    assert abs(ins["width_mm"] * k_avg - tm_w) > 0.05 * tm_w


def test_the_naming_table_has_a_word_for_every_first_batch_kind_in_both_languages():
    """第一批配方图元（pro 仓库 `recipes_batch1.py`）的显示名：缺了就退回 kind 本身（英文字母的名字出现在图层树里）。"""
    table = json.loads(
        (
            Path(__file__).resolve().parent.parent / "src/tavotto/profiles/mechanism_names.json"
        ).read_text("utf-8")
    )["glyphs"]
    kinds = (
        "gpcr ion_channel transporter receptor_single integrin ligand kinase g_protein antibody enzyme phosphate "
        "dna_helix mrna nucleus mitochondrion vesicle er cell membrane receptor_rtk"
    ).split()
    for k in kinds:
        assert table[k]["zh"] and table[k]["en"], k


def test_first_batch_kinds_have_a_family_style_preset_and_the_user_style_still_wins():
    fam = mechanism.DEFAULT_STYLE
    for k in ("gpcr", "ion_channel", "transporter", "receptor_single", "integrin"):
        assert fam[k]["fill"] == "#d4dde6"
    for k in ("ligand", "kinase", "g_protein", "antibody", "enzyme", "phosphate"):
        assert fam[k]["fill"] == "#f2d8c2"
    for k in ("nucleus", "mitochondrion", "vesicle", "er", "cell"):
        assert fam[k]["fill"] == "#f6e7d4"
    assert fam["dna_helix"]["fill"] is None and fam["mrna"]["fill"] is None
    assert all(v["strokePt"] == 0.85 for k, v in fam.items() if k != "membrane")
