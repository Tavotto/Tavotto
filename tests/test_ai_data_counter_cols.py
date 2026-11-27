"""只是在计数的列（重复号、每组各自从头数的行号）默认不当轴，但用户仍能自己选；大表退路也不拿它当横轴（ADR 0178 修订 2026-11-27 的补充）。

判据的主语：`default_candidates` 返回的 `roles`（默认角色与 `options` 下拉）与大表退路出的备选的配方。
"""

from __future__ import annotations

import random
from pathlib import Path

from tavotto.engine import ai_data_figure as adf, ai_data_recipe as recipe_mod


def _write(tmp_path: Path, header: list[str], rows: list[list]) -> Path:
    p = tmp_path / "t.csv"
    p.write_text(
        "\n".join([",".join(header), *(",".join(map(str, r)) for r in rows)]) + "\n",
        encoding="utf-8",
    )
    return p


def _run(tmp_path: Path, p: Path, picks: dict | None = None, *, large: bool = False):
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
        return res
    raise AssertionError("问题没完没了")


def _plots(seed: int = 7):
    rng = random.Random(seed)
    return [[v, r, round(rng.uniform(5, 9), 2)] for v in ("a", "b", "c") for r in range(1, 6)]


def test_the_counter_column_stays_selectable_and_an_explicit_pick_is_honoured(tmp_path):
    p = _write(tmp_path, ["variety", "rep", "yield"], _plots())
    res = _run(tmp_path, p)
    assert res.roles["x"] != "rep" and res.roles["y"] == ["yield"]
    assert "rep" in res.roles["options"]["x"]  # 下拉里仍列得出
    picked = _run(tmp_path, p, {"x": "rep"})
    assert picked.roles["x"] == "rep" and "rep" in {c.recipe.x for c in picked.items}


def test_the_large_table_fallback_never_lands_on_a_replicate_counter(tmp_path, monkeypatch):
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    rows = [
        [1 + i % 4, ["g1", "g2", "g3"][i % 3], 20 + (i * 7) % 11, 3 + (i * 5) % 13, (i * 37) % 23]
        for i in range(60)
    ]
    header = ["rep", "group", "temp", "mass", "response"]
    res = _run(tmp_path, _write(tmp_path, header, rows), {"y": "response"}, large=True)
    assert res.items
    assert res.roles["x"] != "rep" and "rep" not in {c.recipe.x for c in res.items}
    # 反证：同样的取值换个不在闭集里的名字，退路照旧取最左的非常数数值列
    header[0] = "cycle"
    res = _run(tmp_path, _write(tmp_path, header, rows), {"y": "response"}, large=True)
    assert res.roles["x"] == "cycle"
