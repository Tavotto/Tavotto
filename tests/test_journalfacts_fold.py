"""v2 事实折叠成预检规则 + 「需要你确认」（ADR 0155 §14）。向量在 `tests/golden/journal_v2_fold_vectors.json`。"""

import json
from pathlib import Path

import pytest

from tavotto import app as appmod, security
from tavotto.engine import journalfacts_fold as fold_mod, journalfacts_remote as facts, journals
from test_journalfacts_remote import fact_bundle, index_for, signed

pytest_plugins = ("support.journalfacts_fixture",)

VECTORS = json.loads(
    (Path(__file__).parent / "golden" / "journal_v2_fold_vectors.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["id"])
def test_golden_vectors(case):
    assert fold_mod.fold(case["bundle"]) == case["expected"]


def _case(name):
    return next(c for c in VECTORS["cases"] if c["id"] == name)["expected"]


def test_only_unconditional_minimum_dpi_folds_into_a_v1_shaped_rule():
    out = _case("dpi-min-folds")
    assert list(out["rules"]) == ["min_raster_dpi"] and out["confirm"] == []
    rule = out["rules"]["min_raster_dpi"]
    assert rule["qualifier"] == "min" and rule["value"] == 300 and rule["stage"] == "any"
    assert (
        rule["src"] == "v2:s1:journal"
        and out["sources"]["v2:s1:journal"]["coverage"] == "journal_specific"
    )
    assert rule["snapshot"]["file"].startswith("snapshots/")


def test_unconditional_minimum_line_width_folds_into_a_v1_shaped_rule():
    """用户 2026-11-17 拍板：无条件的线宽下限也折成判据（只判数据线，见 preflight 的 `DATA_LINE_ROLES`）。"""
    out = _case("line-width-minimum-folds")
    assert list(out["rules"]) == ["min_line_width_pt"] and out["confirm"] == []
    rule = out["rules"]["min_line_width_pt"]
    assert rule["qualifier"] == "min" and rule["value"] == 2 and rule["stage"] == "any"
    assert rule["src"] == "v2:s1:journal"


@pytest.mark.parametrize(
    "name",
    [
        "dpi-with-condition-text-confirms",
        "dpi-with-named-condition-confirms",
        "dpi-eq-is-not-a-minimum",
        "line-width-with-condition-text-confirms",
        "line-width-eq-is-not-a-minimum",
        "line-width-unspecified-listed-not-folded",
        "width-in-with-text-condition-confirms",
        "stage-revision-does-not-fold",
        "unspecified-strength-is-listed-not-folded",
    ],
)
def test_conditional_or_unjudgeable_never_folds_but_is_listed_for_confirmation(name):
    out = _case(name)
    assert out["rules"] == {} and len(out["confirm"]) == 1


def test_confirm_row_keeps_the_original_condition_verbatim():
    row = _case("dpi-with-condition-text-confirms")["confirm"][0]
    assert row["condition"] == "All images at final size" and row["value"] == 300


@pytest.mark.parametrize(
    "name",
    [
        "permitted-and-typical-are-not-requirements",
        "unknown-origin-is-ignored",
    ],
)
def test_non_requirements_are_ignored_entirely(name):
    assert _case(name) == {"rules": {}, "sources": {}, "confirm": []}


def test_conflicting_values_are_not_guessed():
    out = _case("dpi-two-different-values-not-guessed")
    assert out["rules"] == {} and [r["value"] for r in out["confirm"]] == [300, 600]
    assert list(_case("dpi-two-equal-values-fold-once")["rules"]) == ["min_raster_dpi"]


def test_folded_rules_are_accepted_by_the_v1_catalog_validator():
    """叠到 v1 条目上之后整份目录仍过 `validate_catalog`，且折出的覆盖与 v1 同形。"""
    out = _case("dpi-min-folds")
    cat = {
        "schema": journals.SCHEMA,
        "catalog_rev": 1,
        "publishers": {"p": {"label": "P"}},
        "journals": {
            "j": {
                "label": "J",
                "publisher": "p",
                "sources": out["sources"],
                "rules": out["rules"],
            }
        },
    }
    journals.validate_catalog(cat)
    assert journals.journal_override("j", cat)["min_raster_dpi"] == 300


def test_folded_line_width_floor_reaches_the_preflight_as_a_warn():
    """v2 折出的线宽下限进目录后：覆盖里有 `min_line_width_pt`，严重级别从建议抬到警告，预检真的报。"""
    from tavotto.engine import preflight, profiles

    out = _case("line-width-minimum-folds")
    cat = {
        "schema": journals.SCHEMA,
        "catalog_rev": 1,
        "publishers": {"p": {"label": "P"}},
        "journals": {
            "j": {"label": "J", "publisher": "p", "sources": out["sources"], "rules": out["rules"]}
        },
    }
    journals.validate_catalog(cat)
    override = journals.journal_override("j", cat)
    assert override["min_line_width_pt"] == 2
    assert override["severity"]["line-width-below-min"] == "warn"
    profile = profiles.load("journal-base-v1", override)
    manifest = {
        "stem": "F",
        "size_mm": [80.0, 60.0],
        "elements": [
            {
                "gid": "axes_0.lines_0",
                "role": "line",
                "bbox": [0.1, 0.1, 0.2, 0.2],
                "editable": [{"prop": "linewidth", "type": "number", "value": 1.0}],
            }
        ],
    }
    spec = {
        "page": {"w_mm": 80.0, "h_mm": 60.0, "margin_mm": 0.0},
        "panels": [
            {
                "id": "p1",
                "name": "p1",
                "kind": "pdf",
                "rect_mm": [0, 0, 80.0, 60.0],
                "scale": 1.0,
                "manifest": manifest,
                "px_w": None,
                "missing": False,
                "stale": False,
                "render_error": None,
                "unapplied_overrides": 0,
                "hidden": False,
            }
        ],
        "texts": [],
        "objects": [{"id": "p1", "type": "panel", "rect_mm": [0, 0, 80.0, 60.0], "hidden": False}],
    }
    hits = [i for i in preflight.run(spec, profile) if i["id"] == "line-width-below-min"]
    assert [(i["severity"], i["gids"]) for i in hits] == [("warn", ["axes_0.lines_0"])]


@pytest.fixture
def http_client(monkeypatch):
    state = security.SessionState("test-nonce", api_secret="test-secret")
    state.port = 54321
    monkeypatch.setitem(appmod.app.config, security.STATE_KEY, state)
    return appmod.app.test_client(), {"Host": "127.0.0.1:54321", "X-Tavotto-Auth": "test-secret"}


def test_preflight_endpoint_switch_session_and_shape(http_client, isolated, monkeypatch):
    client, headers = http_client
    key, _ = isolated
    bundle = fact_bundle()
    bundle["facts"][0].update(
        subject="raster_dpi", relation="gte", value=300, unit="dpi", stage="unknown"
    )
    bundle["facts"][0].pop("conditions")
    raw = json.dumps(bundle).encode()
    envelope = signed(key, index_for(raw))
    monkeypatch.setattr(
        facts,
        "_request",
        lambda m, url, cap, h: (200, envelope if url.endswith("index.signed.json") else raw),
    )
    res = client.post(
        "/api/journal-facts/preflight",
        json={"journal_id": "fx-journal"},
        headers={"Host": headers["Host"]},
    )
    assert res.status_code == 401
    res = client.post(
        "/api/journal-facts/preflight", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert res.status_code == 200 and res.headers["Cache-Control"] == "no-store"
    assert res.json["rules"]["min_raster_dpi"]["value"] == 300 and res.json["catalog_rev"] >= 1
    for body in ({}, {"journal_id": "fx-journal", "x": 1}, {"journal_id": 3}):
        bad = client.post("/api/journal-facts/preflight", json=body, headers=headers)
        assert bad.status_code == 400 and bad.json["code"] == "bad_request"
    unknown = client.post(
        "/api/journal-facts/preflight", json={"journal_id": "nope"}, headers=headers
    )
    assert unknown.status_code in (404, 503)
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", False)
    off = client.post(
        "/api/journal-facts/preflight", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert off.status_code == 403 and off.json["code"] == "journal_catalog_disabled"


def test_color_mode_and_file_format_are_listed_never_folded():
    out = _case("color-mode-and-format-are-listed-for-confirmation")
    assert out["rules"] == {}
    rows = {r["subject"]: r for r in out["confirm"]}
    assert rows["color_mode"]["value"] == ["RGB", "CMYK"] and rows["color_mode"]["condition"] == ""
    assert rows["file_format"]["condition"] == "Line art only"


def test_example_font_and_typical_size_are_listed_never_folded():
    out = _case("font-example-and-typical-size-are-listed-not-folded")
    assert out["rules"] == {}
    rows = {r["subject"]: r for r in out["confirm"]}
    assert (
        rows["font_family"]["value"] == ["Helvetica"]
        and rows["font_family"]["strength"] == "example"
    )
    assert rows["font_size"]["value"] == 8 and rows["font_size"]["strength"] == "typical"


def test_conflicting_font_lists_all_stay_available_for_confirmation():
    """同一项两份不同的无条件字体名单：不猜、也不吞——两条都进「需要你确认」（Codex #1996）。"""
    base = next(c for c in VECTORS["cases"] if c["id"] == "font-family-recommended-folds")["bundle"]
    template = base["facts"][0]
    bundle = {
        **base,
        "facts": [
            {**template, "id": "a", "value": ["Helvetica"], "strength": "required"},
            {**template, "id": "b", "value": ["Arial"], "strength": "required"},
        ],
    }
    out = fold_mod.fold(bundle)
    assert out["rules"] == {} and out["sources"] == {}
    assert [r["value"] for r in out["confirm"]] == [["Helvetica"], ["Arial"]]
