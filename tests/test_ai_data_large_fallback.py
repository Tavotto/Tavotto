"""大表默认候选全被「大表不支持」拒掉时的退路（ADR 0178「修订（2026-11-27）：行号列与大表退路」）。

判据的主语：

* 退路的触发量的是 `default_candidates` 这一次出过的**全部**被拒原因：每一条都是 `mark_not_allowed` 且 `detail` 以 `large` 开头，才走退路；
  用户 / 模型对横轴表过态（`picks` 里有 `x`，包括 `none`）、小表、别的原因也拒了的，一律不走。
* 退路出的备选量的是它们**自己的配方**：仍是 `validate_recipe` + `enforce_order` 之后的结果，只会是大表放行的两类（趋势 / 关系）。
* `roles` 量的是响应里如实写出的角色：横轴就是实际用的那一列。
* 大表用 `SMALL_BYTES = 0` 让几十行的文件走大表读法，不真写几十 MB。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_recipe as recipe_mod


def _write(tmp_path: Path, header: list[str], rows: list[list], name: str = "big.csv") -> Path:
    p = tmp_path / name
    p.write_text("\n".join([",".join(header), *(",".join(map(str, r)) for r in rows)]) + "\n")
    return p


def _flights(n: int = 60):
    """类 nyc-flights：行号、常数年份、月份、出发地（分组）、延误（测量值，最后一列）。"""
    return [
        [i + 1, 2013, 1 + i % 12, ["EWR", "JFK", "LGA"][i % 3], (i * 37) % 23 - 5] for i in range(n)
    ]


FLIGHTS = ["rownames", "year", "month", "origin", "delay"]


def _candidates(
    tmp_path: Path, p: Path, picks: dict | None = None, *, large: bool = True, monkeypatch=None
):
    if large:
        monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    prof = adf.profile_folder(tmp_path)
    vals = adf.inspect_data_source(p, None, v2=True, allow_large=large)
    assert (vals.large is not None) is large
    picks = dict(picks or {})
    for _ in range(6):
        res = recipe_mod.default_candidates(
            prof, table=p.name, confirmed=recipe_mod.Confirmed(), values=vals, picks=picks
        )
        if isinstance(res, recipe_mod.NeedsChoice):
            picks.update(res.unsure.answer)
            continue
        return prof, vals, res
    raise AssertionError("问题没完没了")


def test_a_large_table_whose_defaults_are_all_large_rejected_still_gets_a_chart(
    tmp_path, monkeypatch
):
    p = _write(tmp_path, FLIGHTS, _flights())
    prof, vals, res = _candidates(tmp_path, p, monkeypatch=monkeypatch)
    assert res.items and res.reasons == ()
    # 只会是大表放行的两类；配方仍然全部过校验
    assert {c.recipe.family for c in res.items} <= {"trend", "relationship"}
    for c in res.items:
        assert (
            recipe_mod.validate_recipe(prof, c.recipe, confirmed=res.confirmed, values=vals) == []
        )
        assert {layer.mark for layer in c.recipe.layers} <= {"points", "line"}
    # 横轴是整表上不是常数的最左数值列：不是行号，也不是全 2013 的 year
    assert res.roles["x"] == "month"
    assert {c.recipe.x for c in res.items} == {"month"}
    assert res.roles["y"] == ["delay"]
    # 大表不支持的画法仍然记在 dropped 里，没有被悄悄吞掉
    assert any(
        p_.code == "mark_not_allowed" and p_.detail.startswith("large")
        for d in res.dropped
        for p_ in d.problems
    )


def test_the_same_shape_as_a_small_table_never_takes_the_fallback(tmp_path):
    """小表：没有任何「大表不支持」的拒绝，退路不出场（候选来自同一条规则流水）。"""
    p = _write(tmp_path, FLIGHTS, _flights())
    _prof, _vals, res = _candidates(tmp_path, p, large=False)
    assert res.items
    assert not any(p_.detail.startswith("large") for d in res.dropped for p_ in d.problems)


def test_a_user_choice_about_the_x_axis_blocks_the_fallback(tmp_path, monkeypatch):
    p = _write(tmp_path, FLIGHTS, _flights())
    _prof, _vals, res = _candidates(tmp_path, p, {"x": "none"}, monkeypatch=monkeypatch)
    assert res.items == () and res.reasons == ("all_candidates_rejected",)
    assert res.roles["x"] is None


def test_a_date_column_is_preferred_as_the_ordered_axis(tmp_path, monkeypatch):
    rows = [
        [i + 1, f"2013-01-{1 + i % 28:02d}", ["EWR", "JFK", "LGA"][i % 3], (i * 37) % 23 - 5]
        for i in range(60)
    ]
    p = _write(tmp_path, ["rownames", "day", "origin", "delay"], rows)
    _prof, vals, res = _candidates(tmp_path, p, monkeypatch=monkeypatch)
    if vals.per_column["day"].kind != "date":
        pytest.skip("日期识别口径变了：这条只在列被认成日期时有意义")
    assert res.items and res.roles["x"] == "day"


def test_the_row_counter_is_never_the_fallback_axis(tmp_path, monkeypatch):
    """行号之外没有别的横轴候选：不走退路，原来的「没有备选」原样（不拿行号当横轴）。"""
    rows = [[i + 1, ["EWR", "JFK", "LGA"][i % 3], (i * 37) % 23 - 5] for i in range(60)]
    p = _write(tmp_path, ["rownames", "origin", "delay"], rows)
    _prof, vals, res = _candidates(tmp_path, p, monkeypatch=monkeypatch)
    assert vals.row_index == ["rownames"]
    assert res.items == () and res.reasons == ("all_candidates_rejected",)


def test_a_rejection_that_is_not_about_the_large_limit_blocks_the_fallback(tmp_path, monkeypatch):
    p = _write(tmp_path, FLIGHTS, _flights())
    _prof, vals, res = _candidates(tmp_path, p, {"x": "none"}, monkeypatch=monkeypatch)
    roles = recipe_mod.resolve_roles(adf.profile_folder(tmp_path).tables[0], None, {"y": "delay"})
    assert isinstance(roles, recipe_mod.Roles) and roles.x_ord is None
    s = vals
    only_large = res.dropped
    assert recipe_mod._large_fallback_x(s, vals, roles, {}, only_large) == "month"
    other = (
        *only_large,
        recipe_mod.Dropped(9, (recipe_mod.Problem("roles_invalid", "y", "multi_y_not_drawn"),)),
    )
    assert recipe_mod._large_fallback_x(s, vals, roles, {}, other) is None
    assert recipe_mod._large_fallback_x(s, vals, roles, {"x": "none"}, only_large) is None
    assert recipe_mod._large_fallback_x(s, vals, roles, {}, ()) is None
