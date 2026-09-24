"""按规范修图的计划逻辑（合成 manifest，不需要科学栈）。真链路在 `test_specfix_real.py`。"""

from __future__ import annotations

from tavotto.engine import profiles, specfix


def _el(gid: str, role: str, **props) -> dict:
    return {"gid": gid, "role": role, "editable": [{"prop": k, "value": v} for k, v in props.items()]}


def _manifest(*els) -> dict:
    return {"size_mm": [80.0, 60.0], "elements": list(els)}


def _change(plan: dict, gid: str, prop: str) -> dict:
    return next(c for c in plan["changes"] if c["gid"] == gid and c["prop"] == prop)


def _value(plan: dict, gid: str, prop: str):
    return next(p["value"] for p in plan["patches"] if p["gid"] == gid and p["prop"] == prop)


def test_font_target_is_on_the_page_and_converted_back_by_scale():
    """缩到 60% 的图：目标是页面上的 8.5 pt，写进脚本的是 8.5 / 0.6 往上取两位。"""
    m = _manifest(_el("a.xticks", "ticks", fontsize=7.0))
    plan = specfix.plan(
        m, profiles.load(), scale=0.6, targets=[("font-below-absolute-floor", "a.xticks")]
    )
    assert _change(plan, "a.xticks", "fontsize")["after"] == 8.5
    v = _value(plan, "a.xticks", "fontsize")
    assert v * 0.6 > profiles.load()["absolute_min_font_size_pt"]
    assert v == 14.17  # ceil(8.5 / 0.6, 2)：往下取的 14.16 × 0.6 = 8.496 仍会被判偏小


def test_raising_ticks_lifts_the_axis_label_but_never_past_its_own_ceiling():
    m = _manifest(
        _el("a.xticks", "ticks", fontsize=7.0),
        _el("a.xlabel", "axis_label", fontsize=8.2),
        _el("a.title", "title", fontsize=12.0),
    )
    plan = specfix.plan(
        m, profiles.load(), scale=1.0, targets=[("font-below-absolute-floor", "a.xticks")]
    )
    assert _change(plan, "a.xlabel", "fontsize")["rule"] == "keep-hierarchy"
    assert _value(plan, "a.xlabel", "fontsize") == 8.5
    # 标题本来就更大：一个字都不动
    assert not any(c["gid"] == "a.title" for c in plan["changes"])


def test_legend_goes_into_the_intersection_of_both_intervals():
    """图例同时受绝对下限（> 8，所以 8.5）与图例区间（8–9）约束：取交集里的最小档。"""
    m = _manifest(_el("a.legend", "legend", fontsize=6.0))
    plan = specfix.plan(
        m,
        profiles.load(),
        scale=1.0,
        targets=[("font-below-absolute-floor", "a.legend"), ("legend-font-size", "a.legend")],
    )
    assert _value(plan, "a.legend", "fontsize") == 8.5


def test_empty_interval_is_skipped_not_forced():
    p = profiles.load()
    p["legend_policy"] = {**p["legend_policy"], "max_font_size_pt": 8.0}  # 8.5 起才过绝对下限
    m = _manifest(_el("a.legend", "legend", fontsize=6.0))
    plan = specfix.plan(m, p, scale=1.0, targets=[("legend-font-size", "a.legend")])
    assert plan["patches"] == []
    assert plan["skipped"] == [
        {"rule": "legend-font-size", "gid": "a.legend", "reason": "constraint_conflict"}
    ]


def test_line_width_tie_picks_the_thinner_preset():
    p = profiles.load()
    p["line_widths_pt"] = [1.0, 1.5]
    m = _manifest(_el("a.lines_0", "line", linewidth=1.25))
    plan = specfix.plan(m, p, scale=1.0, targets=[("line-width-off-preset", "a.lines_0")])
    assert _value(plan, "a.lines_0", "linewidth") == 1.0


def test_select_skips_suggestions_in_batch_but_honours_them_when_named():
    issues = [
        {"id": "legend-frame", "severity": "warn", "gids": ["a.legend"]},
        {"id": "text-weight-policy", "severity": "suggestion", "gids": ["a.xlabel"]},
        {"id": "axis-label-format", "severity": "suggestion", "gids": ["a.xlabel"]},
    ]
    assert specfix.select(issues, None) == [("legend-frame", "a.legend")]
    named = specfix.select(issues, [{"rule": "text-weight-policy", "gid": "a.xlabel"}])
    assert named == [("text-weight-policy", "a.xlabel")]
    # 不认识的规则（不在 FIXABLE_RULES 里）点名了也不修
    assert specfix.select(issues, [{"rule": "axis-label-format", "gid": ""}]) == []


def test_issues_are_exploded_per_gid():
    """按 (规则, gid) 配对前后两份清单：只修了其中一个 gid，剩下的不能变成「新问题」。"""
    m = _manifest(
        _el("a.xticks", "ticks", fontsize=7.0),
        _el("a.yticks", "ticks", fontsize=7.0),
    )
    rows = specfix.profile_issues(m, profiles.load(), 1.0)
    floor = [r for r in rows if r["id"] == "font-below-absolute-floor"]
    assert sorted(r["gids"][0] for r in floor) == ["a.xticks", "a.yticks"]
    assert all(len(r["gids"]) == 1 for r in rows if r.get("gids"))


def test_fixable_rules_are_the_same_closed_set_on_both_sides():
    """严格同源对：`specfix.FIXABLE_RULES` ↔ `web/src/lib/issueFix.ts` 的 `ENGINE_FIX_RULES`。

    前端据它判「这条走后端修」并给出「修复」按钮；少一条 = 一颗后端不认的按钮
    （后端会回 `not_found`，界面说「没修」，但按钮本不该出现），多一条 = 后端修得了
    的问题界面上不给修。顺序也比（闭集的一种写法，不许两侧各排一套）。
    """
    from pathlib import Path

    from tests.support.tsconst import exported_string_array

    ts = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "issueFix.ts"
    assert tuple(exported_string_array(ts.read_text(encoding="utf-8"), "ENGINE_FIX_RULES")) == (
        specfix.FIXABLE_RULES
    )
