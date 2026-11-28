"""投稿核对的要求表（ADR 0216 §九）：v1 / v2 按检查项合并、v2 优先；只有 preflight 能说「判过」；组件只拿到数字与结构。"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import journals, submissioncheck as sc


def _fact(fid, subject, relation, value, unit=None, strength="recommended", **extra):
    fact = {
        "id": fid,
        "subject": subject,
        "relation": relation,
        "value": value,
        "strength": strength,
        "stage": "accepted",
        "source": "s",
        "quote": f"Quote {fid}",
        "origin": "journal",
        "review": {"status": "verified", "reviewed": "2026-11-28", "method": "fixture"},
    }
    if unit is not None:
        fact["unit"] = unit
    fact.update(extra)
    return fact


def _iv(lo, hi):
    return {"lower": lo, "upper": hi, "lower_inclusive": True, "upper_inclusive": True}


def _bundle(*facts):
    return {
        "schema": 2,
        "journal_id": "nature",
        "journal": {"label": "Nature", "publisher_id": "springer-nature"},
        "sources": {
            "s": {"url": "https://example.invalid/guide", "kind": "html", "retrieved": "2026-11-28"}
        },
        "facts": list(facts),
    }


def _by(rows, check):
    return [r for r in rows if r["check"] == check]


def test_feature_key_is_registered():
    from tavotto.engine import license as lic

    assert sc.FEATURE in lic.FEATURES


def test_v1_nature_states_follow_what_preflight_actually_judges():
    """Nature：7 pt 上限是 info（不判）→ confirm；5 pt 下限是 advice（只建议）；300 dpi 与栏宽真判过。"""
    rows = sc.requirements("nature")
    assert [(r["value"], r["source_state"]) for r in _by(rows, "font_size_max")] == [(7, "confirm")]
    assert [(r["value"], r["source_state"]) for r in _by(rows, "font_size_min")] == [(5, "advice")]
    assert [(r["value"], r["source_state"]) for r in _by(rows, "raster_dpi_min")] == [
        (300, "preflight")
    ]
    widths = {(r["conditions"].get("column"), r["value"]) for r in _by(rows, "width_final")}
    assert widths == {("single", 89), ("double", 183)}
    assert all(r["source_state"] == "preflight" for r in _by(rows, "width_final"))
    # 检查项之外的（色彩模式、分图标签说明……）照样列出来给人看，只是不发给组件
    assert any(r["check"] is None and r["origin"]["path"] == "info.color_mode" for r in rows)
    assert [r["ref"] for r in rows] == [f"r{i}" for i in range(1, len(rows) + 1)]


def test_v2_merge_follows_what_preflight_judges():
    """v2 的栏宽、字号今天不折进预检：v1 判过的 89 / 183 留着（仍是 preflight），数值相同的 v2 条并进去；
    v1 只展示的 7 pt 上限让给 v2（带 text_role 条件）；v1 只建议的 5 pt 下限保留为 advice。"""
    bundle = _bundle(
        _fact("w1", "width", "eq", 89, "mm", conditions={"column": "single"}),
        _fact("w2", "width", "interval", _iv(120, 136), "mm", strength="permitted"),
        _fact("fmax", "font_size", "lte", 7, "pt", conditions={"text_role": "other_text"}),
        _fact("fmin", "font_size", "gte", 5, "pt", conditions={"text_role": "other_text"}),
        _fact("plab", "font_size", "eq", 8, "pt", conditions={"text_role": "panel_label"}),
    )
    rows = sc.requirements("nature", bundle=bundle)
    widths = {
        (r["conditions"].get("column"), r["value"], r["source_state"], r["origin"]["catalog"])
        for r in _by(rows, "width_final")
    }
    assert widths == {("single", 89, "preflight", "v1"), ("double", 183, "preflight", "v1")}
    (single,) = [r for r in _by(rows, "width_final") if r["value"] == 89]
    assert [o["fact_id"] for o in single["also"]] == ["w1"]
    (fmax,) = _by(rows, "font_size_max")
    assert fmax["origin"]["catalog"] == "v2" and fmax["conditions"] == {"text_role": "other_text"}
    assert fmax["source_state"] == "confirm" and fmax["stage"] == "production"
    (fmin,) = _by(rows, "font_size_min")
    assert fmin["source_state"] == "advice" and fmin["conditions"] == {"text_role": "other_text"}
    assert [o["fact_id"] for o in fmin["also"]] == ["fmin"]
    assert [r["origin"]["catalog"] for r in _by(rows, "raster_dpi_min")] == ["v1"]
    # 分图标签的 8 pt（eq）不是上下限检查项：列出来、没有检查项
    assert any(r["check"] is None and r["origin"].get("fact_id") == "plab" for r in rows)
    # permitted 的 1.5 栏：不是要求，不收
    assert not any(r["origin"].get("fact_id") == "w2" for r in rows)


def test_folded_v2_value_replaces_the_v1_path_it_folds_into():
    bundle = _bundle(
        _fact("dpi", "raster_dpi", "gte", 600, "dpi", strength="required", stage="production")
    )
    (dpi,) = _by(sc.requirements("nature", bundle=bundle), "raster_dpi_min")
    assert (dpi["origin"]["catalog"], dpi["value"], dpi["source_state"]) == ("v2", 600, "preflight")


def test_v2_inches_are_mm_and_interval_is_read_from_the_bundle():
    bundle = _bundle(
        _fact("a", "width", "eq", 4, "in", strength="required", conditions={"column": "single"}),
        _fact("b", "width", "interval", _iv(120, 136), "mm", strength="recommended"),
    )
    rows = [
        r
        for r in _by(sc.requirements("nature", bundle=bundle), "width_final")
        if r["origin"]["catalog"] == "v2"
    ]
    assert {(r["relation"], json.dumps(r["value"])) for r in rows} == {
        ("eq", "101.6"),
        ("interval", json.dumps(_iv(120, 136))),
    }
    assert all(r["unit"] == "mm" for r in rows)


def test_folded_v2_fact_is_preflight_and_preferred_font_list_is_confirm():
    bundle = _bundle(
        _fact("dpi", "raster_dpi", "gte", 600, "dpi", strength="required", stage="production"),
        _fact("fam", "font_family", "in", ["Helvetica", "Arial"], strength="preferred"),
    )
    rows = sc.requirements("nature", bundle=bundle)
    (dpi,) = _by(rows, "raster_dpi_min")
    assert dpi["source_state"] == "preflight" and dpi["origin"]["fact_id"] == "dpi"
    (fam,) = _by(rows, "font_family")
    assert fam["source_state"] == "confirm"  # #2357：preferred 不折进预检
    assert fam["value"] == ["Helvetica", "Arial"]


def test_accepted_stage_fact_is_not_claimed_as_judged():
    """`accepted` 今天不折进预检（`fold._STAGE` 没有这一档）：哪怕是 required 也只能是 confirm，不能说「判过」。"""
    rows = sc.requirements(
        "nature", bundle=_bundle(_fact("dpi", "raster_dpi", "gte", 600, "dpi", strength="required"))
    )
    (dpi,) = [r for r in _by(rows, "raster_dpi_min") if r["origin"]["catalog"] == "v2"]
    assert dpi["source_state"] == "confirm" and dpi["stage"] == "production"
    # 它没折进预检，所以 v1 判过的 300 dpi 照旧在
    assert [r["value"] for r in _by(rows, "raster_dpi_min") if r["origin"]["catalog"] == "v1"] == [
        300
    ]


def test_sidecar_rows_carry_no_quotes_links_or_unknown_conditions():
    bundle = _bundle(
        _fact(
            "fmax",
            "font_size",
            "lte",
            7,
            "pt",
            conditions={"text_role": "other_text", "article_type": "Letters"},
            condition_text="all other text",
        ),
    )
    rows = sc.requirements("nature", bundle=bundle)
    out = sc.sidecar_rows(rows)
    assert all(set(item) == set(sc.SIDECAR_FIELDS) for item in out)
    assert all(item["check"] in sc.CHECKS for item in out)
    blob = json.dumps(out, ensure_ascii=False)
    for leak in ("Quote", "http", "snapshots/", "all other text", "Letters"):
        assert leak not in blob
    (fmax,) = [i for i in out if i["check"] == "font_size_max"]
    assert fmax["conditions"] == {"text_role": "other_text"}
    assert fmax["has_condition_text"] is True


def test_v2_only_journal_works_and_unknown_journal_raises():
    rows = sc.requirements(
        "v2-only-journal", bundle=_bundle(_fact("w", "width", "eq", 85, "mm", strength="required"))
    )
    assert [r["value"] for r in _by(rows, "width_final")] == [85]
    with pytest.raises(journals.JournalCatalogError):
        sc.requirements("no-such-journal")


def test_v2_interval_from_a_validated_bundle_is_listed_next_to_judged_v1_widths():
    """真刊包里区间是对象形状：读得到；v1 判过的 89 / 183 不被它挤掉。"""
    from tavotto.engine import journalfacts_contract as contract

    bundle = _bundle(_fact("b", "width", "interval", _iv(120, 136), "mm", strength="recommended"))
    contract.validate_bundle(bundle)
    rows = _by(sc.requirements("nature", bundle=bundle), "width_final")
    assert sorted((r["origin"]["catalog"], r["relation"], r["source_state"]) for r in rows) == [
        ("v1", "eq", "preflight"),
        ("v1", "eq", "preflight"),
        ("v2", "interval", "confirm"),
    ]
    assert len([i for i in sc.sidecar_rows(rows) if i["check"] == "width_final"]) == 3


def test_non_exact_v1_columns_are_not_claimed_as_judged():
    """v1 栏宽写的是 typical / recommended 时预检不判（`judged_page_columns`）：关系是「等于」，状态是 confirm。"""
    cat = journals.load_catalog()
    jid = next(
        j
        for j, entry in cat["journals"].items()
        if any(
            c.get("qualifier", "exact") not in ("exact", "max")
            for c in ((entry.get("rules") or {}).get("page_columns") or {}).get("value") or []
        )
    )
    rows = _by(sc.requirements(jid), "width_final")
    soft = [r for r in rows if r["source_state"] == "confirm"]
    assert soft and all(
        r["relation"] == "eq" and isinstance(r["value"], (int, float)) for r in soft
    )


def test_any_dropped_condition_marks_the_row_unresolved():
    bundle = _bundle(
        _fact(
            "w",
            "width",
            "eq",
            89,
            "mm",
            strength="required",
            conditions={"column": {"relation": "eq", "value": 1, "unit": "count"}},
        )
    )
    (item,) = [
        i
        for i in sc.sidecar_rows(sc.requirements("nature", bundle=bundle))
        if i["check"] == "width_final" and i["value"] == 89 and not i["conditions"]
    ]
    assert item["conditions"] == {} and item["has_condition_text"] is True
