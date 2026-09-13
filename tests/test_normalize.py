"""保留式规范化（ADR 0051）的纯逻辑看护：修改约定、计划、授权、实效比对、
干涉检测、局部修复候选、最终产物验收——全部在合成的 manifest 上跑，不需要
matplotlib。真渲染 + 真导出的整条链在 `tests/test_mcp_normalize.py`。

每条用例盯一件「坏了不报错、只是悄悄放行」的事：越权的 patch 被放过、受保护
属性变了没人发现、字体没装上却宣称达标、包含关系被判成干涉、预算算错方向……
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import artifactcheck, interference, normalize, profiles

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------ 合成 manifest --------------------------------
def _el(gid, role, bbox, **props):
    editable = []
    for k, v in props.items():
        f = {"prop": k, "value": v}
        if k == "entry_order":
            f["options"] = ["A", "B"]
        editable.append(f)
    return {
        "gid": gid,
        "role": role,
        "label": gid,
        "draggable": False,
        "bbox": list(bbox),
        "editable": editable,
    }


def _two_column_manifest(w=150.0, h=60.0, *, ylabel1_x=0.52, xlabel_y=0.92):
    """两个并排子图 + 各自的标题 / 轴标题 / 刻度组 / 图例，B0 一切安好。"""
    elements = [
        _el("figure", "figure", [0, 0, 1, 1], size_mm=[w, h]),
        _el(
            "axes_0",
            "axes",
            [0.08, 0.15, 0.40, 0.70],
            position=[0.08, 0.15, 0.40, 0.70],
            xlim=[0.0, 10.0],
            ylim=[0.0, 1.0],
            xscale="linear",
            visible=True,
        ),
        _el(
            "axes_0.title",
            "title",
            [0.2, 0.05, 0.16, 0.08],
            text="Kinetics",
            fontsize=12.0,
            fontfamily="sans-serif",
            color="#000000",
            weight="normal",
        ),
        _el(
            "axes_0.xlabel",
            "axis_label",
            [0.22, xlabel_y, 0.12, 0.06],
            text="Time (min)",
            fontsize=10.0,
            fontfamily="sans-serif",
            color="#000000",
            weight="normal",
        ),
        _el(
            "axes_0.ylabel",
            "axis_label",
            [0.01, 0.3, 0.03, 0.4],
            text="Conversion (-)",
            fontsize=10.0,
            fontfamily="sans-serif",
            color="#000000",
            weight="normal",
        ),
        _el(
            "axes_0.xticks",
            "ticks",
            [0.09, 0.86, 0.38, 0.05],
            fontsize=10.0,
            fontfamily="sans-serif",
            major_mode="auto",
            major_values=[0.0, 5.0, 10.0],
            format="auto",
            direction="in",
        ),
        _el(
            "axes_0.yticks",
            "ticks",
            [0.03, 0.15, 0.04, 0.70],
            fontsize=10.0,
            fontfamily="sans-serif",
            major_mode="fixed",
            major_values=[0.0, 0.5, 1.0],
            format="auto",
            direction="in",
        ),
        _el("axes_0.xticklabels_1", "ticklabel", [0.09, 0.86, 0.03, 0.05], text="0"),
        _el("axes_0.xticklabels_2", "ticklabel", [0.26, 0.86, 0.03, 0.05], text="5"),
        _el("axes_0.yticklabels_1", "ticklabel", [0.03, 0.80, 0.04, 0.05], text="0.0"),
        _el(
            "axes_0.lines_0",
            "line",
            [0.10, 0.20, 0.36, 0.60],
            color="#1f77b4",
            linewidth=1.0,
            visible=True,
            label="A",
        ),
        _el(
            "axes_0.legend",
            "legend",
            [0.30, 0.60, 0.16, 0.20],
            loc="lower right",
            loc_anchor=None,
            fontsize=10.0,
            frameon=False,
            entry_order=[0, 1],
            visible=True,
        ),
        _el(
            "axes_0.legend.texts_0",
            "legend_text",
            [0.35, 0.63, 0.10, 0.06],
            text="A",
            fontsize=10.0,
            fontfamily="sans-serif",
        ),
        _el(
            "axes_1",
            "axes",
            [0.58, 0.15, 0.40, 0.70],
            position=[0.58, 0.15, 0.40, 0.70],
            xlim=[300.0, 800.0],
            ylim=[1.0, 1e5],
            xscale="linear",
            visible=True,
        ),
        _el(
            "axes_1.title",
            "title",
            [0.7, 0.05, 0.2, 0.08],
            text="Thermal",
            fontsize=12.0,
            fontfamily="sans-serif",
            color="#000000",
            weight="normal",
        ),
        _el(
            "axes_1.xlabel",
            "axis_label",
            [0.7, xlabel_y, 0.18, 0.06],
            text="Temperature (K)",
            fontsize=10.0,
            fontfamily="sans-serif",
            color="#000000",
            weight="normal",
        ),
        _el(
            "axes_1.ylabel",
            "axis_label",
            [ylabel1_x, 0.3, 0.03, 0.4],
            text="Intensity",
            fontsize=10.0,
            fontfamily="sans-serif",
            color="#000000",
            weight="normal",
        ),
        _el(
            "axes_1.lines_0",
            "line",
            [0.60, 0.20, 0.36, 0.60],
            color="#2ca02c",
            linewidth=1.0,
            visible=True,
            label="Signal",
        ),
    ]
    # 曲线几何：axes_1 是从左上到右下的一条对角线；axes_0 的曲线贴着上半部走，
    # 不碰右下角的图例（figure 分数、y 向下）
    elements[-1]["geometry"] = {
        "kind": "polyline",
        "paths": [{"points": [[0.60, 0.20], [0.96, 0.80]], "closed": False}],
        "fill": False,
        "stroke": True,
        "clip": [0.58, 0.15, 0.40, 0.70],
    }
    line0 = next(e for e in elements if e["gid"] == "axes_0.lines_0")
    line0["geometry"] = {
        "kind": "polyline",
        "paths": [{"points": [[0.10, 0.50], [0.30, 0.30], [0.46, 0.25]], "closed": False}],
        "fill": False,
        "stroke": True,
        "clip": [0.08, 0.15, 0.40, 0.70],
    }
    for el in elements:
        if el["role"] in ("title", "axis_label", "legend_text", "ticks", "ticklabel"):
            el["face"] = "DejaVu Sans"
    return {"stem": "Fig", "size_mm": [w, h], "elements": elements}


def _profile():
    return profiles.load("lab-publication-v1")


def _contract(manifest, targets, base_patches=None):
    return normalize.build_contract(
        manifest,
        normalize.normalize_targets(targets),
        profile=_profile(),
        base_patches=base_patches or [],
        profile_issues=[],
    )


def _set(manifest, gid, prop, value):
    for el in manifest["elements"]:
        if el["gid"] == gid:
            for f in el["editable"]:
                if f["prop"] == prop:
                    f["value"] = value
                    return
            el["editable"].append({"prop": prop, "value": value})
            return
    raise AssertionError(gid)


def _compare(contract, manifest, **kw):
    return normalize.compare(
        contract, manifest, profile_issues=kw.pop("profile_issues", []), profile=_profile(), **kw
    )


# ------------------------------- 目标与计划 ----------------------------------
def test_targets_reject_unknown_negative_and_contradictory():
    with pytest.raises(normalize.NormalizeError) as exc:
        normalize.normalize_targets({"colour": "red"})
    assert exc.value.code == "bad_targets"
    with pytest.raises(normalize.NormalizeError):
        normalize.normalize_targets({"width_mm": -3})
    with pytest.raises(normalize.NormalizeError):
        normalize.normalize_targets({})
    with pytest.raises(normalize.NormalizeError):
        normalize.normalize_targets({"font_size_pt": 6, "min_font_pt": 8})
    assert normalize.normalize_targets({"width_mm": 80, "font_family": " Times New Roman "}) == {
        "width_mm": 80.0,
        "font_family": "Times New Roman",
    }


def test_width_only_keeps_the_original_aspect_ratio():
    m = _two_column_manifest()
    plan = normalize.plan_patches(_contract(m, {"width_mm": 80}), m)
    size = next(p for p in plan["patches"] if p["gid"] == "figure")["value"]
    assert size == [80.0, 32.0]
    assert any("长宽比" in n for n in plan["notes"])


def test_min_font_only_raises_text_below_the_floor():
    """「最小 8 pt」不把 12 pt 的标题压成 8 pt。"""
    m = _two_column_manifest()
    _set(m, "axes_0.xticks", "fontsize", 6.0)
    plan = normalize.plan_patches(_contract(m, {"min_font_pt": 8}), m)
    sizes = [(p["gid"], p["value"]) for p in plan["patches"] if p["prop"] == "fontsize"]
    assert sizes == [("axes_0.xticks", 8.0)]


def test_uniform_font_size_touches_every_size_bearing_prop():
    m = _two_column_manifest()
    plan = normalize.plan_patches(_contract(m, {"font_size_pt": 9}), m)
    gids = {p["gid"] for p in plan["patches"] if p["prop"] == "fontsize"}
    assert {"axes_0.title", "axes_0.xticks", "axes_0.legend", "axes_1.ylabel"} <= gids


def test_font_family_reaches_ticks_and_names_what_it_cannot_reach():
    m = _two_column_manifest()
    _set(m, "axes_0.legend", "title", "Samples")
    plan = normalize.plan_patches(_contract(m, {"font_family": "DejaVu Serif"}), m)
    fam = {p["gid"] for p in plan["patches"] if p["prop"] == "fontfamily"}
    assert "axes_0.xticks" in fam and "axes_0.title" in fam and "axes_0.legend.texts_0" in fam
    assert plan["unsupported"] == [
        {
            "gid": "axes_0.legend",
            "prop": "fontfamily",
            "reason": "no_override_for_role",
            "role": "legend",
        }
    ]


def test_merge_patches_never_duplicates_a_key():
    base = [{"gid": "figure", "prop": "size_mm", "value": [80, 60]}]
    merged = normalize.merge_patches(
        base, [{"gid": "figure", "prop": "size_mm", "value": [70, 50]}]
    )
    assert merged == [{"gid": "figure", "prop": "size_mm", "value": [70, 50]}]


# --------------------------------- 授权 --------------------------------------
@pytest.mark.parametrize(
    "gid, prop, value",
    [
        ("axes_0", "ylim", [0.0, 2.0]),
        ("axes_0.lines_0", "visible", False),
        ("axes_0.lines_0", "color", "#ff0000"),
        ("axes_0.legend", "entry_order", [1, 0]),
        ("axes_0.yticks", "major_values", [0.0, 1.0]),
        ("axes_0.title", "text", "New title"),
    ],
)
def test_data_structure_and_style_edits_are_not_authorised_by_a_size_request(gid, prop, value):
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80, "font_family": "DejaVu Serif", "min_font_pt": 8})
    bad = normalize.authorize(c, [{"gid": gid, "prop": prop, "value": value}])
    assert bad == [{"gid": gid, "prop": prop, "value": value}]


def test_the_users_own_prior_edits_ride_along_unchanged():
    m = _two_column_manifest()
    base = [{"gid": "axes_0.lines_0", "prop": "color", "value": "#ff0000"}]
    c = _contract(m, {"width_mm": 80}, base_patches=base)
    assert normalize.authorize(c, base) == []
    assert normalize.authorize(c, [{"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"}])


def test_allowed_set_is_derived_from_targets_and_the_loop_cannot_widen_it():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    assert normalize.allowed_keys(c) == {("figure", "size_mm")}
    assert normalize.adjust_keys(c) == {
        ("axes_0", "position"),
        ("axes_1", "position"),
        ("axes_0.legend", "loc"),
    }


# ------------------------------- 实效比对 ------------------------------------
def test_a_side_effect_on_a_protected_property_fails_the_transaction():
    m = _two_column_manifest()
    c = _contract(m, {"font_family": "DejaVu Serif"})
    after = _two_column_manifest()
    for el in after["elements"]:
        if el["role"] in ("title", "axis_label", "legend_text", "ticks"):
            _set(after, el["gid"], "fontfamily", "DejaVu Serif")
            el["face"] = "DejaVu Serif"
    _set(after, "axes_0.lines_0", "color", "#ff0000")  # 高层命令顺手改了颜色
    v = _compare(c, after)
    assert v["exit"] == normalize.EXIT_PROTECTED_CHANGED
    assert v["protected_changes"] == [
        {"gid": "axes_0.lines_0", "prop": "color", "before": "#1f77b4", "after": "#ff0000"}
    ]


def test_auto_ticks_may_recompute_but_fixed_ticks_may_not():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    after = _two_column_manifest(80, 32)
    # 自动定位的 x 刻度少了一个标签、值变了：合法的自适应
    after["elements"] = [e for e in after["elements"] if e["gid"] != "axes_0.xticklabels_2"]
    _set(after, "axes_0.xticks", "major_values", [0.0, 10.0])
    v = _compare(c, after)
    assert v["structure"]["missing"] == [] and v["protected_changes"] == []
    # 脚本显式 set_yticks 过的那条轴变了：内容改动
    _set(after, "axes_0.yticks", "major_values", [0.0, 1.0])
    v = _compare(c, after)
    assert [c_["gid"] for c_ in v["protected_changes"]] == ["axes_0.yticks"]


def test_a_vanished_element_is_a_structure_change():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    after = _two_column_manifest(80, 32)
    after["elements"] = [e for e in after["elements"] if e["gid"] != "axes_0.legend.texts_0"]
    v = _compare(c, after)
    assert v["structure"]["missing"] == ["axes_0.legend.texts_0"]
    assert v["exit"] == normalize.EXIT_PROTECTED_CHANGED


def test_a_font_that_silently_fell_back_is_not_reported_as_applied():
    m = _two_column_manifest()
    c = _contract(m, {"font_family": "Tavotto Nonexistent Serif"})
    after = _two_column_manifest()
    for el in after["elements"]:
        if "face" in el:
            _set(after, el["gid"], "fontfamily", "Tavotto Nonexistent Serif")  # 名字写进去了
            # 脸还是 DejaVu：matplotlib 静默退到了下一环
    v = _compare(c, after)
    assert v["exit"] == normalize.EXIT_FONT_UNAVAILABLE
    assert v["targets_met"]["font_family"] is False
    assert {f["gid"] for f in v["font_unresolved"]} >= {"axes_0.title", "axes_0.xticks"}


def test_mathtext_drawn_by_another_face_counts_as_unresolved():
    m = _two_column_manifest()
    c = _contract(m, {"font_family": "DejaVu Serif"})
    after = _two_column_manifest()
    for el in after["elements"]:
        if "face" in el:
            _set(after, el["gid"], "fontfamily", "DejaVu Serif")
            el["face"] = "DejaVu Serif"
    tick = next(e for e in after["elements"] if e["gid"] == "axes_1.ylabel")
    tick["math_face"] = "DejaVu Sans"  # `10^4` 仍由默认 mathtext 集画
    v = _compare(c, after)
    assert v["font_unresolved"] == [
        {
            "gid": "axes_1.ylabel",
            "requested": "DejaVu Serif",
            "face": "DejaVu Serif",
            "math_face": "DejaVu Sans",
        }
    ]


def test_generic_family_requests_are_not_verified_by_name():
    m = _two_column_manifest()
    c = _contract(m, {"font_family": "serif"})
    after = _two_column_manifest()
    for el in after["elements"]:
        if "face" in el:
            _set(after, el["gid"], "fontfamily", "serif")
    v = _compare(c, after)
    assert v["targets_met"]["font_family"] is None and v["font_unresolved"] == []


def test_size_target_is_checked_against_the_rendered_size():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    v = _compare(c, _two_column_manifest(80, 32))
    assert v["targets_met"]["size"] is True
    v = _compare(c, _two_column_manifest(150, 60))
    assert v["targets_met"]["size"] is False and v["exit"] == normalize.EXIT_CONSTRAINT_CONFLICT


# ----------------------------- 问题清单对比 ----------------------------------
def _issue(cid, gids, **detail):
    return {
        "id": cid,
        "severity": "warn",
        "text": cid,
        "message": {"key": cid, "params": {}},
        "object_ids": ["f"],
        "gids": gids,
        "detail": detail,
    }


def test_issues_are_classified_by_stable_identity_not_by_count():
    before = [
        _issue("text-overlap", ["a", "b"], overlap_mm2=1.0, certain=True),
        _issue("text-overlap", ["c", "d"], overlap_mm2=1.0, certain=True),
        _issue("element-outside-figure", ["e"], overflow_mm=0.8),
    ]
    after = [
        _issue("text-overlap", ["a", "b"], overlap_mm2=1.05, certain=True),  # 抖动，未加重
        _issue("element-outside-figure", ["e"], overflow_mm=2.5),  # 加重
        _issue("legend-over-data", ["leg", "line"], targets=["line"], certain=True),  # 新增
    ]
    out = normalize._classify_issues(before, after)
    assert [i["gids"] for i in out["unchanged"]] == [["a", "b"]]
    assert [i["gids"] for i in out["worsened"]] == [["e"]]
    assert [i["gids"] for i in out["new"]] == [["leg", "line"]]
    assert [i["gids"] for i in out["improved"]] == [["c", "d"]]


def test_fewer_warnings_do_not_excuse_one_new_severe_occlusion():
    """修掉两个轻微警告、换来一个新的确定性遮挡：仍然不能通过。"""
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    c["baseline"]["geometry_issues"] = [
        _issue("text-overlap", ["a", "b"], overlap_mm2=0.5, certain=True),
        _issue("text-overlap", ["c", "d"], overlap_mm2=0.5, certain=True),
    ]
    after = _two_column_manifest(80, 32)
    # 把图例挪到曲线正中间：几何相交，确定性遮挡
    _set(after, "axes_1.lines_0", "visible", True)
    leg = _el(
        "axes_1.legend",
        "legend",
        [0.70, 0.40, 0.16, 0.20],
        loc="center",
        loc_anchor=None,
        fontsize=10.0,
        frameon=False,
        entry_order=[0],
        visible=True,
    )
    after["elements"].append(leg)
    c["baseline"]["snapshot"]["axes_1.legend"] = normalize.protected_snapshot(after)[
        "axes_1.legend"
    ]
    v = _compare(c, after)
    assert [i["id"] for i in v["geometry_issues"]["improved"]] == ["text-overlap", "text-overlap"]
    assert [b["id"] for b in v["blocking"]] == ["legend-over-data"]
    assert v["ok"] is False


def test_risk_level_interference_does_not_block_but_certain_does():
    certain = {**_issue("legend-over-data", ["l", "d"], certain=True), "severity": "warn"}
    risk = {**_issue("legend-over-data", ["l", "d"], certain=False), "severity": "warn"}
    assert normalize._blocks(certain, {}) is True
    assert normalize._blocks(risk, {}) is False


def test_rules_decided_by_the_users_targets_are_conflicts_not_blockers():
    """用户要 120 mm、规范只认 80/150：这是用户的决定，报告出来但不挡。"""
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 120})
    page_width = {**_issue("page-width", []), "severity": "error"}
    v = _compare(c, _two_column_manifest(120, 48), profile_issues=[page_width])
    assert v["blocking"] == []
    assert [i["id"] for i in v["profile_conflicts"]] == ["page-width"]
    assert v["ok"] is True


# ------------------------------- 预算与位移 ----------------------------------
def test_cumulative_drift_is_measured_against_b0_not_the_previous_round():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    after = _two_column_manifest(80, 32)
    # 每轮各挪一点，最后左边挪了 20% 图幅：相对 B0 超预算
    _set(after, "axes_0", "position", [0.28, 0.15, 0.30, 0.70])
    v = _compare(c, after)
    assert v["exit"] == normalize.EXIT_BUDGET_EXCEEDED
    assert any(o["gid"] == "axes_0" and o.get("side") == "left" for o in v["budget"]["over"])


def test_an_axes_squeezed_below_the_keep_ratio_is_over_budget():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    after = _two_column_manifest(80, 32)
    _set(after, "axes_0", "position", [0.10, 0.15, 0.20, 0.70])  # 宽度只剩一半
    v = _compare(c, after)
    assert any(o["gid"] == "axes_0" and o.get("keep") == "w" for o in v["budget"]["over"])


# ------------------------------ 局部修复：边距 ------------------------------
def test_margin_adaptation_reserves_room_for_decorations_within_budget():
    """缩到 80 mm 后 y 轴标题探出左边：重排把子图往右挪、装饰物留在图内，位移在预算内。"""
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    after = _two_column_manifest(80, 32)
    # 80 mm 下装饰物（mm 不变）占的 figure 分数翻了近一倍：ylabel 探出左边
    _set(after, "axes_0.ylabel", "bbox", None) if False else None
    for el in after["elements"]:
        if el["gid"] == "axes_0.ylabel":
            el["bbox"] = [-0.03, 0.3, 0.06, 0.4]
        if el["gid"] == "axes_0.yticks":
            el["bbox"] = [0.00, 0.15, 0.08, 0.70]
    cand = normalize.adapt_margins(c, after, directions={"x"})
    assert "patches" in cand and [p["gid"] for p in cand["patches"]] == ["axes_0", "axes_1"]
    new0 = next(p for p in cand["patches"] if p["gid"] == "axes_0")["value"]
    assert new0[0] > 0.08 + 0.03  # 左边至少让出探出的那一段
    v = _compare(c, _apply_positions(after, cand["patches"]))
    assert v["budget"]["over"] == []
    assert all(
        abs(s) <= 0.15 * 80
        for a in v["budget"]["axes"].values()
        for s in a["edge_shift_mm"].values()
    )


def _apply_positions(manifest, patches):
    out = json.loads(json.dumps(manifest))
    for p in patches:
        _set(out, p["gid"], p["prop"], p["value"])
    return out


def test_margin_adaptation_refuses_when_decorations_do_not_fit():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 20})
    after = _two_column_manifest(20, 8)
    for el in after["elements"]:
        if el["role"] in ("axis_label", "ticks"):
            el["bbox"] = [el["bbox"][0] - 0.3, el["bbox"][1], el["bbox"][2] + 0.6, el["bbox"][3]]
    cand = normalize.adapt_margins(c, after, directions={"x"})
    assert "conflict" in cand
    assert cand["conflict"]["reason"] in ("decorations_exceed_figure", "axes_too_small")
    assert (
        cand["conflict"]["needed_mm"] > cand["conflict"]["available_mm"]
        or cand["conflict"]["reason"] == "axes_too_small"
    )


def test_margin_adaptation_is_a_no_op_when_nothing_overflows():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 150})
    cand = normalize.adapt_margins(c, _two_column_manifest())
    assert cand["patches"] == []


def test_repair_directions_follow_the_side_that_overflows():
    assert normalize.repair_directions(
        [{"id": "element-outside-figure", "detail": {"side": "left"}}]
    ) == {"x"}
    assert normalize.repair_directions(
        [{"id": "element-outside-figure", "detail": {"side": "top"}}]
    ) == {"y"}


# ------------------------------ 局部修复：图例 ------------------------------
def test_legend_candidates_stay_inside_the_axes_and_start_near_the_original():
    m = _two_column_manifest()
    c = _contract(m, {"width_mm": 80})
    cands = normalize.legend_candidates(c, "axes_0.legend")
    assert cands[0] in ("lower center", "center right") and "lower right" not in cands
    assert set(cands) <= set(normalize.LEGEND_LOCS)


def test_an_externally_anchored_legend_gets_no_candidates():
    m = _two_column_manifest()
    _set(m, "axes_0.legend", "loc_anchor", [1.02, 1.0])
    c = _contract(m, {"width_mm": 80})
    assert normalize.legend_candidates(c, "axes_0.legend") == []
    assert ("axes_0.legend", "loc") not in normalize.adjust_keys(c)


# --------------------------------- 干涉检测 ----------------------------------
def test_text_overlap_needs_a_real_bite_in_both_directions():
    m = _two_column_manifest()
    assert [i for i in interference.detect(m) if i["id"] == "text-overlap"] == []
    # 把 axes_1 的 ylabel 压到 axes_0 的图例上（不碰 xlabel）
    for el in m["elements"]:
        if el["gid"] == "axes_1.ylabel":
            el["bbox"] = [0.34, 0.62, 0.03, 0.25]
    hits = [i for i in interference.detect(m) if i["id"] == "text-overlap"]
    assert {tuple(i["gids"]) for i in hits} == {
        ("axes_0.legend", "axes_1.ylabel"),
        ("axes_0.legend.texts_0", "axes_1.ylabel"),
    }
    assert all(i["detail"]["certain"] for i in hits)


def test_entries_of_the_same_legend_never_overlap_each_other():
    m = _two_column_manifest()
    m["elements"].append(
        _el(
            "axes_0.legend.texts_1",
            "legend_text",
            [0.35, 0.63, 0.10, 0.06],
            text="B",
            fontsize=10.0,
            fontfamily="sans-serif",
        )
    )
    assert [i for i in interference.detect(m) if i["id"] == "text-overlap"] == []


def test_a_subplots_own_inset_is_containment_not_interference():
    m = _two_column_manifest()
    # 插图（落位被锁：没有 position 字段）在 axes_0 里，带自己的刻度文字
    m["elements"].append(_el("axes_2", "axes", [0.30, 0.20, 0.15, 0.25], visible=True))
    m["elements"].append(_el("axes_2.xticks", "ticks", [0.30, 0.42, 0.15, 0.04], fontsize=6.0))
    hits = [i for i in interference.detect(m) if i["id"] == "text-over-axes"]
    assert hits == []


def test_decoration_of_one_subplot_inside_another_is_reported():
    m = _two_column_manifest()
    for el in m["elements"]:
        if el["gid"] == "axes_1.ylabel":
            el["bbox"] = [0.44, 0.3, 0.03, 0.4]  # 落进 axes_0 的绘图区
    hits = [i for i in interference.detect(m) if i["id"] == "text-over-axes"]
    assert [tuple(i["gids"]) for i in hits] == [("axes_1.ylabel", "axes_0")]


def test_legend_over_data_is_certain_with_geometry_and_only_a_risk_without():
    m = _two_column_manifest()
    m["elements"].append(
        _el(
            "axes_1.legend",
            "legend",
            [0.70, 0.40, 0.16, 0.20],
            loc="center",
            loc_anchor=None,
            fontsize=10.0,
            frameon=False,
            entry_order=[0],
            visible=True,
        )
    )
    hits = [i for i in interference.detect(m) if i["id"] == "legend-over-data"]
    assert len(hits) == 1 and hits[0]["detail"]["certain"] is True
    assert hits[0]["detail"]["basis"] == "geometry"
    # 同一个框，数据只有包围盒（误差棒容器）：只标风险
    for el in m["elements"]:
        if el["gid"] == "axes_1.lines_0":
            del el["geometry"]
            el["role"] = "errorbar"
    hits = [i for i in interference.detect(m) if i["id"] == "legend-over-data"]
    assert len(hits) == 1 and hits[0]["detail"]["certain"] is False
    assert hits[0]["message"]["key"] == "legendOverDataRisk"


def test_a_legend_beside_the_curve_is_not_flagged():
    m = _two_column_manifest()
    # 曲线从左上到右下；图例放右上角的空白处
    m["elements"].append(
        _el(
            "axes_1.legend",
            "legend",
            [0.84, 0.16, 0.12, 0.10],
            loc="upper right",
            loc_anchor=None,
            fontsize=10.0,
            frameon=False,
            entry_order=[0],
            visible=True,
        )
    )
    assert [i for i in interference.detect(m) if i["id"] == "legend-over-data"] == []


def test_hidden_elements_never_interfere():
    m = _two_column_manifest()
    for el in m["elements"]:
        if el["gid"] == "axes_1.ylabel":
            el["bbox"] = [0.32, 0.62, 0.03, 0.25]
            _set(m, el["gid"], "visible", False)
    assert interference.detect(m) == []


def test_interference_checks_carry_a_registered_severity_and_message_key():
    p = _profile()
    for cid in interference.CHECK_IDS:
        assert p["severity"][cid] == "warn"
    for locale in ("zh-CN", "en-US"):
        table = json.loads(
            (ROOT / "web" / "src" / "i18n" / "locales" / locale / "errors.json").read_text(
                encoding="utf-8"
            )
        )
        for key in ("textOverlap", "textOverAxes", "legendOverData", "legendOverDataRisk"):
            assert key in table["preflight"], (locale, key)
        for cid in interference.CHECK_IDS:
            assert cid in table["problems"]["title"], (locale, cid)


# ------------------------------ 最终产物验收 ---------------------------------
def test_font_names_match_accepts_subset_names_and_rejects_strangers():
    assert artifactcheck.font_names_match("Times New Roman", ["TimesNewRomanPSMT"]) is True
    assert (
        artifactcheck.font_names_match("Times New Roman", ["TimesNewRomanPSMT", "DejaVuSans"])
        is False
    )
    assert artifactcheck.font_names_match("Times New Roman", []) is None
    assert artifactcheck.font_names_match(None, ["DejaVuSans"]) is None


def test_svg_check_reads_physical_size_and_admits_it_cannot_see_fonts(tmp_path):
    svg = tmp_path / "f.svg"
    svg.write_text(
        '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="226.771654pt" '
        'height="90.708661pt" viewBox="0 0 226.771654 90.708661"><path d="M0 0"/></svg>',
        encoding="utf-8",
    )
    out = artifactcheck.check_file(
        svg, "svg", expect_mm=[80.0, 32.0], dpi=None, font_family="DejaVu Serif"
    )
    assert out["size_mm"] == [80.0, 32.0] and out["size_ok"] is True and out["viewbox_ok"] is True
    assert out["font_ok"] is None and "font_ok" in out["unverified"]
    assert out["ok"] is True
    bad = artifactcheck.check_file(svg, "svg", expect_mm=[81.0, 32.0], dpi=None, font_family=None)
    assert bad["ok"] is False and bad["failed"] == ["size_ok"]


def test_eps_check_reads_the_bounding_box_with_integer_pt_tolerance(tmp_path):
    eps = tmp_path / "f.eps"
    eps.write_bytes(
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 227 91\n%%BeginFont: DejaVuSerif\n"
    )
    out = artifactcheck.check_file(
        eps, "eps", expect_mm=[80.0, 32.0], dpi=None, font_family="DejaVu Serif"
    )
    assert out["size_ok"] is True and out["font_ok"] is True and out["ok"] is True


def test_a_crashing_check_becomes_a_failure_not_an_exception(tmp_path):
    missing = tmp_path / "nope.pdf"
    out = artifactcheck.check_file(
        missing, "pdf", expect_mm=[80.0, 32.0], dpi=None, font_family=None
    )
    assert out["ok"] is False and "error" in out
