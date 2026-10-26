"""按规范修图的计划逻辑（合成 manifest，不需要科学栈）。真链路在 `test_specfix_real.py`。"""

from __future__ import annotations

from tavotto.engine import profiles, specfix


def _el(gid: str, role: str, **props) -> dict:
    return {
        "gid": gid,
        "role": role,
        "editable": [{"prop": k, "value": v} for k, v in props.items()],
    }


def _manifest(*els) -> dict:
    return {"size_mm": [80.0, 60.0], "elements": list(els)}


def _change(plan: dict, gid: str, prop: str) -> dict:
    return next(c for c in plan["changes"] if c["gid"] == gid and c["prop"] == prop)


def _value(plan: dict, gid: str, prop: str):
    return next(p["value"] for p in plan["patches"] if p["gid"] == gid and p["prop"] == prop)


def test_font_target_is_on_the_page_and_converted_back_by_scale():
    """缩到 60% 的图：目标是页面上的 8 pt，写进脚本的是 8 / 0.6 往上取两位。"""
    m = _manifest(_el("a.xticks", "ticks", fontsize=7.0))
    plan = specfix.plan(
        m, profiles.load(), scale=0.6, targets=[("font-below-absolute-floor", "a.xticks")]
    )
    assert _change(plan, "a.xticks", "fontsize")["after"] == 8.0
    v = _value(plan, "a.xticks", "fontsize")
    assert v * 0.6 >= profiles.load()["absolute_min_font_size_pt"]
    assert v == 13.34  # ceil(8 / 0.6, 2)：往下取的 13.33 × 0.6 = 7.998 仍会被判偏小


def test_raising_ticks_lifts_the_axis_label_but_never_past_its_own_ceiling():
    m = _manifest(
        _el("a.xticks", "ticks", fontsize=7.0),
        _el("a.xlabel", "axis_label", fontsize=7.9),
        _el("a.title", "title", fontsize=12.0),
    )
    plan = specfix.plan(
        m, profiles.load(), scale=1.0, targets=[("font-below-absolute-floor", "a.xticks")]
    )
    assert _change(plan, "a.xlabel", "fontsize")["rule"] == "keep-hierarchy"
    assert _value(plan, "a.xlabel", "fontsize") == 8.0
    # 标题本来就更大：一个字都不动
    assert not any(c["gid"] == "a.title" for c in plan["changes"])


def test_legend_goes_into_the_intersection_of_both_intervals():
    """图例同时受绝对下限（≥ 8）与图例区间（8–9）约束：取交集里的最小档。"""
    m = _manifest(
        _el("a.legend", "legend", fontsize=6.0),
        _el("a.legend.texts_0", "legend_text", fontsize=6.0),
    )
    plan = specfix.plan(
        m,
        profiles.load(),
        scale=1.0,
        targets=[("font-below-absolute-floor", "a.legend"), ("legend-font-size", "a.legend")],
    )
    assert _value(plan, "a.legend", "fontsize") == 8.0


def test_legend_is_fixed_by_its_drawn_entries_not_the_container_setter():
    """判的是画出来的条目，改的也得是条目：设置器 8.5 在区间里，条目 11 超了——照样要改，
    且多条取离区间最远的那条（5 比 11 远）；隐藏的条目与标题不参与。"""
    m = _manifest(
        _el("a.legend", "legend", fontsize=8.5, title_fontsize=4.0),
        _el("a.legend.title", "legend_text", fontsize=4.0),
        _el("a.legend.texts_0", "legend_text", fontsize=11.0),
        _el("a.legend.texts_1", "legend_text", fontsize=5.0),
        _el("a.legend.texts_2", "legend_text", fontsize=2.0, visible=False),
    )
    plan = specfix.plan(m, profiles.load(), scale=1.0, targets=[("legend-font-size", "a.legend")])
    assert _change(plan, "a.legend", "fontsize")["before"] == 5.0
    assert _value(plan, "a.legend", "fontsize") == 8.0
    # 标题的 title_fontsize 是设置器，不是被判的量：不顺手改
    assert not any(p["prop"] == "title_fontsize" for p in plan["patches"])
    # 没有画出来的条目：没有可改的量，什么都不登记
    bare = _manifest(_el("a.legend", "legend", fontsize=6.0))
    assert (
        specfix.plan(bare, profiles.load(), scale=1.0, targets=[("legend-font-size", "a.legend")])[
            "patches"
        ]
        == []
    )


def test_scaled_legend_picks_the_worst_entry_on_the_page_not_in_script_points():
    """缩放 0.5：条目 17 / 10 pt 画在页面上是 8.5 / 5 pt。按脚本值比会挑 17（离 8–9 远）、而它页面上
    8.5 已合规 → 不出补丁，5 pt 那条一直留着（Codex #1366 P2）。要按页面字号挑 10。"""
    m = _manifest(
        _el("a.legend", "legend", fontsize=10.0),
        _el("a.legend.texts_0", "legend_text", fontsize=17.0),
        _el("a.legend.texts_1", "legend_text", fontsize=10.0),
    )
    plan = specfix.plan(m, profiles.load(), scale=0.5, targets=[("legend-font-size", "a.legend")])
    assert _change(plan, "a.legend", "fontsize")["before"] == 5.0  # 页面上的字号（10 × 0.5）
    assert _value(plan, "a.legend", "fontsize") == 16.0  # 页面 8 pt / 0.5


def test_empty_interval_is_skipped_not_forced():
    p = profiles.load()
    p["legend_policy"] = {**p["legend_policy"], "max_font_size_pt": 7.5}  # 8 起才过绝对下限
    m = _manifest(
        _el("a.legend", "legend", fontsize=6.0),
        _el("a.legend.texts_0", "legend_text", fontsize=6.0),
    )
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
    # 显式给了空列表 = 一条都不修，不是「全部处理」
    assert specfix.select(issues, []) == []
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


def test_lowering_a_heading_lowers_bigger_lower_ranked_text_with_it():
    """Codex #549 第四轮：只修 20 pt 标题的「字号偏大」时，14 pt 的轴标题也高于新标题，
    不许修成「标题 12 / 轴标题 14」的倒挂——下层跟着降到同一档。"""
    p = profiles.load()
    p["max_font_size_pt"] = 12.0
    m = _manifest(
        _el("a.title", "title", fontsize=20.0),
        _el("a.xlabel", "axis_label", fontsize=14.0),
        _el("a.xticks", "ticks", fontsize=9.0),
    )
    plan = specfix.plan(m, p, scale=1.0, targets=[("font-too-large", "a.title")])
    assert _value(plan, "a.title", "fontsize") == 12.0
    assert _value(plan, "a.xlabel", "fontsize") == 12.0
    assert _change(plan, "a.xlabel", "fontsize")["rule"] == "keep-hierarchy"
    # 本来就更小的下层一个字不动
    assert not any(c["gid"] == "a.xticks" for c in plan["changes"])


def test_font_grid_is_one_number_on_both_sides():
    """严格同源对：`specfix.FONT_GRID_STEPS_PER_PT` ↔ `issueFix.ts` 的同名常量（Codex #549 P1）。

    画布标注的字号在前端修、图内文字在后端修；档格分叉的话，同一种「字号偏小」在两处落在
    不同的档上。TS 侧按结构读（只认一处 `export const`、初始化式必须是整数字面量）。
    """
    from pathlib import Path

    from tests.support.tsconst import exported_number

    ts = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "issueFix.ts"
    assert exported_number(ts.read_text(encoding="utf-8"), "FONT_GRID_STEPS_PER_PT") == (
        specfix.FONT_GRID_STEPS_PER_PT
    )
    assert specfix.GRID == 1 / specfix.FONT_GRID_STEPS_PER_PT
