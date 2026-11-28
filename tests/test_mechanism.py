"""机理图骨架（ADR 0220，6.B）：权益 / `pro_required`、整批拒、协议校验、frame、命名分组、可编辑性核对、假图元端到端。

闭源图元一个都不在这里：`tests/fixtures/pro_echo` 的假图元（形状固定的替身）让开源侧用**真的 sidecar 进程**走完协议。"""

from __future__ import annotations

import copy
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


def test_path_quality_is_listed_unknown_until_6a_lands():
    out = mechcheck.check([_path("p", [RING_OUT])], require_names=False)
    assert [u["detail"]["reason"] for u in out["unknown"]] == ["pathquality_missing"]


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
