"""内置期刊标准库（ADR 0154）：数据结构、双侧同源、每条校验各自的失败码。

向量里的期刊是**测试夹具**（fx-*，example.invalid），不是真实期刊；真实
`journals.json` 在本 PR 里刻意是空库，这里只证明「空库合法」与「结构随包分发」。
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import journals, profiles

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "golden" / "journal_vectors.json").read_text("utf-8"))
sys.path.insert(0, str(ROOT / "scripts"))
import gen_journal_vectors as gen  # noqa: E402


def _fixture() -> dict:
    return copy.deepcopy(VECTORS["catalog"])


def _code_of(catalog: dict) -> str:
    with pytest.raises(journals.JournalCatalogError) as exc:
        journals.validate_catalog(catalog)
    return exc.value.code


# ------------------------------ 随包分发 / 空库 ------------------------------
def test_shipped_catalog_is_valid_and_sits_next_to_publication_json():
    assert journals.journals_path().parent == profiles.profiles_path().parent
    assert journals.journals_path().name == journals.JOURNALS_FILE
    doc = journals.load_catalog()
    assert doc["schema"] == 1
    # PR 1 刻意是空库：真实数据必须逐条带出处、经人工核对后才进来
    assert doc["publishers"] == {} and doc["journals"] == {}
    assert journals.list_journals() == []


def test_shipped_catalog_is_not_gitignored():
    out = subprocess.run(
        ["git", "check-ignore", "-q", "src/tavotto/profiles/journals.json"],
        cwd=ROOT,
        capture_output=True,
    )
    assert out.returncode == 1  # 1 = 没被忽略


def test_typescript_reads_the_same_file():
    for cfg in ("vite.config.ts", "vitest.config.ts", "vite.mcp.config.ts"):
        text = (ROOT / "web" / cfg).read_text(encoding="utf-8")
        assert "'@journals'" in text, f"{cfg} 没配 @journals 别名"
        assert "../src/tavotto/profiles/journals.json" in text, f"{cfg} 的 @journals 指错了文件"
    tsconfig = (ROOT / "web" / "tsconfig.app.json").read_text(encoding="utf-8")
    assert '"@journals": ["../src/tavotto/profiles/journals.json"]' in tsconfig


def test_error_code_sets_match_typescript():
    ts = (ROOT / "web" / "src" / "lib" / "journals.ts").read_text(encoding="utf-8")
    block = ts[ts.index("JOURNAL_ERROR_CODES = [") :].split("] as const")[0]
    assert tuple(re.findall(r"'(journal_\w+)'", block)) == journals.ERROR_CODES


# ------------------------------ journal-base-v1 ------------------------------
def test_base_profile_is_neutral_and_hidden_from_the_spec_list():
    base = profiles.load(journals.BASE_PROFILE_ID)
    assert base["allowed_aspect_ratios"] == []
    assert base["font_family"]["latin"] == "" and base["font_family"]["latin_accepted"] == []
    assert base["cjk_fallback"]["required"] is False
    assert base["min_effective_font_size_pt"] == 0 and base["absolute_min_font_size_pt"] == 0
    assert base["line_widths_pt"] == [] and base["text_weight_policy"] == {}
    assert base["axis_policy"]["tick_direction"] is None and base["legend_policy"]["frame"] is None
    # 栏宽没规定就是 null，不放占位数字冒充事实
    assert base["widths_mm"]["single"] is None and base["widths_mm"]["double"] is None
    assert base["severity"]["page-width"] == "suggestion"
    # severity 表补齐全部检查 id（默认 warn = DEFAULT_SEVERITY，行为不变），期刊才能对每个检查写规则
    lab = profiles.load("lab-publication-v1")
    assert set(lab["severity"]) <= set(base["severity"])
    for check in set(base["severity"]) - set(
        k for k, v in base["severity"].items() if v == "suggestion"
    ):
        assert base["severity"][check] in ("warn", "error")
    assert base["severity"]["raster-dpi"] == profiles.DEFAULT_SEVERITY
    assert base["severity"]["element-outside-figure"] == "error"
    # 不在规范下拉里，但 load() 取得到（期刊覆盖要叠在它上面）
    assert base.get("listed") is False
    assert journals.BASE_PROFILE_ID not in [p["profile_id"] for p in profiles.list_profiles()]
    assert profiles.list_profiles()[0]["profile_id"] == profiles.default_profile_id()


# ------------------------------ golden 向量 ------------------------------
def test_vector_file_is_what_the_generator_writes():
    assert gen.build() == VECTORS
    assert gen.fixture_catalog() == VECTORS["catalog"]


def test_list_vector():
    assert journals.list_journals(_fixture()) == VECTORS["list"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["journal_id"])
def test_fold_vectors(case):
    cat = _fixture()
    jid = case["journal_id"]
    assert journals.journal_override(jid, cat) == case["override"]
    assert journals.journal_sources(jid, cat) == case["sources"]
    merged = journals.journal_profile(jid, cat)
    for path, want in case["merged_fields"].items():
        node = merged
        for part in path.split("."):
            node = node[part]
        assert node == want


@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda c: c["name"])
def test_invalid_vectors_fail_with_their_own_code(case):
    assert _code_of(gen.apply_ops(_fixture(), case["ops"])) == case["code"]


def test_every_validation_code_has_a_failing_vector():
    covered = {c["code"] for c in VECTORS["invalid"]}
    assert covered == set(journals.ERROR_CODES) - {"journal_unknown"}


def test_unknown_journal_id():
    for fn in (journals.journal_override, journals.journal_sources, journals.journal_profile):
        with pytest.raises(journals.JournalCatalogError) as exc:
            fn("nope", _fixture())
        assert exc.value.code == VECTORS["unknown_journal_code"]


# ------------------------------ 折叠语义 ------------------------------
def test_journal_rules_override_publisher_rules_and_inherit_the_rest():
    cat = _fixture()
    over = journals.journal_override("fx-beta", cat)
    assert over["widths_mm"] == {"single": 85, "double": 180}  # double 被期刊改写，single 继承
    src = journals.journal_sources("fx-beta", cat)
    dbl = src["widths_mm.double"]
    assert (dbl["rule_scope"], dbl["source_scope"]) == ("journal", "journal")
    assert dbl["overrides"]["value"] == 175 and dbl["overrides"]["rule_scope"] == "publisher"
    assert src["widths_mm.single"]["rule_scope"] == "publisher"
    assert src["widths_mm.single"]["overrides"] is None
    alpha = journals.journal_sources("fx-alpha", cat)["widths_mm.double"]
    assert (alpha["rule_scope"], alpha["source_scope"]) == ("publisher", "publisher")


def test_rule_scope_and_source_scope_are_different_things():
    """期刊自己的规则引用出版社的出处：值是期刊声明的，出处挂在出版社上。"""
    font = journals.journal_sources("fx-beta", _fixture())["min_effective_font_size_pt"]
    assert (font["rule_scope"], font["source_scope"]) == ("journal", "publisher")
    assert font["coverage"] == "publisher_general" and font["qualifier"] == "min"


def test_single_journal_sample_source_cannot_back_publisher_rules():
    cat = _fixture()
    sources = journals.journal_sources("fx-beta", cat)
    assert sources["widths_mm.double"]["coverage"] == "single_journal_sample"
    cat["publishers"]["fx-press"]["sources"]["s1"]["coverage"] = "single_journal_sample"
    assert _code_of(cat) == "journal_source_coverage"


def test_info_rules_are_shown_but_never_folded_into_the_override():
    cat = _fixture()
    over = journals.journal_override("fx-beta", cat)
    assert "info" not in over and "max_height_mm" not in over
    src = journals.journal_sources("fx-beta", cat)
    assert src["info.max_height_mm"]["affects"] == "info"
    assert src["info.one_half_mm"]["value_alt"] == 136
    assert src["widths_mm.single"]["affects"] == "preflight"
    mark = src["info.max_height_mm"]
    assert mark["mark"] in mark["quote"] and mark["derivation"]


def test_only_exact_or_direction_matching_rules_fold_into_the_override():
    """typical / recommended / 方向不符的 min、max 只留在出处里（affects=info），不进预检。"""
    cat = _fixture()
    over = journals.journal_override("fx-zeta", cat)
    assert over["legend_policy"] == {"max_font_size_pt": 9}  # max 配上限字段：折
    assert over["min_raster_dpi"] == 450  # exact：折（盖过出版社的 600）
    for gone in ("min_effective_font_size_pt", "max_font_size_pt", "font_family"):
        assert gone not in over, gone
    src = journals.journal_sources("fx-zeta", cat)
    assert src["min_effective_font_size_pt"]["affects"] == "advice"  # typical → 只生成建议性提示
    for path in ("font_family.latin_accepted", "max_font_size_pt"):
        assert src[path]["affects"] == "info", path
    assert src["min_raster_dpi"]["affects"] == "preflight"
    merged = journals.journal_profile("fx-zeta", cat)
    assert merged["min_effective_font_size_pt"] == 0  # typical 5 pt 没变成硬下限
    assert merged["font_family"]["latin_accepted"] == []
    assert merged["max_font_size_pt"] == 72


def test_page_columns_win_and_derive_single_and_double():
    """期刊自有栏位：page_columns 为准，widths_mm 由 exact 栏位派生（最窄 single、最宽 double）。"""
    cat = _fixture()
    over = journals.journal_override("fx-cols-inherit", cat)  # 出版社写了三档，期刊继承
    assert over["widths_mm"] == {"single": 57, "double": 184}  # 121 栏在中间，不占 single/double
    assert [c["id"] for c in over["page_columns"]] == ["col1", "col2", "col3"]
    prof = journals.journal_profile("fx-cols-inherit", cat)
    assert [c["width_mm"] for c in profiles.derive_page_columns(prof)] == [57, 121, 184]
    assert prof["severity"]["page-width"] == "warn"  # 写了栏位 → 抬升


def test_page_columns_one_exact_one_typical():
    """期刊自己的 page_columns 盖过出版社的；只有一档 exact（另一档 typical 不参与判据）时 double 为 null，
    出版社那条 widths_mm.single=999 被 page_columns 盖掉。"""
    over = journals.journal_override("fx-cols", _fixture())
    assert over["widths_mm"] == {"single": 90, "double": None}
    assert [c["id"] for c in over["page_columns"]] == ["narrow", "wide"]


def test_default_page_columns_are_derived_from_widths():
    cols = profiles.derive_page_columns(profiles.load("lab-publication-v1"))
    assert [(c["id"], c["label"]["zh"]) for c in cols] == [("single", "单栏"), ("double", "双栏")]
    assert profiles.derive_page_columns(profiles.load(journals.BASE_PROFILE_ID)) == []


def test_advisories_carry_journal_name_and_quote_but_never_become_a_floor():
    cat = _fixture()
    over = journals.journal_override("fx-adv", cat)
    assert "min_effective_font_size_pt" not in over and "min_script_font_size_pt" not in over
    roles = {a["role"]: a for a in over["advisories"]}
    assert roles["body_min"]["value_pt"] == 7 and roles["script_min"]["value_pt"] == 6
    assert roles["body_min"]["journal"] == "Fixture Advice Press"  # 出版社声明 → 出版社名
    assert "FIXTURE 7 pt" in roles["body_min"]["quote"]
    own = journals.journal_override("fx-adv-own", cat)["advisories"]
    assert [a["journal"] for a in own] == ["Fixture Advice Own"]  # 期刊自己声明 → 期刊名
    src = journals.journal_sources("fx-adv", cat)
    assert {
        src[p]["affects"] for p in ("min_effective_font_size_pt", "min_script_font_size_pt")
    } == {"advice"}
    assert journals.journal_profile("fx-adv", cat)["min_effective_font_size_pt"] == 0


def test_unset_removes_the_publisher_rule_for_that_journal_only():
    cat = _fixture()
    assert "min_raster_dpi" in journals.journal_override("fx-alpha", cat)
    assert "min_raster_dpi" not in journals.journal_override("fx-epsilon", cat)
    entry = journals.journal_sources("fx-epsilon", cat)["min_raster_dpi"]
    assert entry["unset"] is True and entry["value"] is None
    assert entry["overrides"]["value"] == 600 and entry["quote"]
    # 底的 300 兜底原样在，没被出版社那条 600 污染
    assert journals.journal_profile("fx-epsilon", cat)["min_raster_dpi"] == 300


def test_severity_rises_with_the_rules_the_journal_filled_in():
    cat = _fixture()
    base = profiles.load(journals.BASE_PROFILE_ID)
    assert base["severity"]["line-width-off-preset"] == "suggestion"
    alpha = journals.journal_profile("fx-alpha", cat)
    assert alpha["severity"]["line-width-off-preset"] == "warn"  # 期刊写了线宽档 → 抬到 warn
    assert alpha["severity"]["page-width"] == "error"  # 期刊显式写了的以它为准，不被抬升覆盖
    beta = journals.journal_profile("fx-beta", cat)
    assert beta["severity"]["font-family-substituted"] == "warn"
    assert beta["severity"]["tick-direction"] == "suggestion"  # 没写的检查不动
    # 只抬不降：底里已经是 warn / error 的不变
    assert beta["severity"]["font-too-small"] == base["severity"]["font-too-small"]
    assert beta["severity"]["element-outside-figure"] == "error"
    delta = journals.journal_profile("fx-delta", cat)
    assert delta["severity"] == base["severity"]


def test_null_widths_mean_unspecified_in_the_evaluator():
    """期刊只写了双栏宽：单栏是 null，提示里不出现编造的数字。"""
    spec = {
        "page": {"w_mm": 120.0, "h_mm": 80.0},
        "panels": [],
        "texts": [],
        "objects": [],
    }
    from tavotto.engine import preflight

    plain = journals.journal_profile("fx-delta", _fixture())
    assert "page-width" not in {i["id"] for i in preflight.run(spec, plain)}
    both = profiles.load(journals.BASE_PROFILE_ID, {"widths_mm": {"double": 170.0}})
    issue = next(i for i in preflight.run(spec, both) if i["id"] == "page-width")
    assert issue["message"]["params"]["want"] == "170"  # 只列期刊写了的那一栏
    assert issue["detail"]["single_mm"] is None


def test_unspecified_items_are_not_filled_in():
    """期刊没写的项一律保持 journal-base-v1 的中性值，不被任何别的规范补上。"""
    cat = _fixture()
    base = profiles.load(journals.BASE_PROFILE_ID)
    assert journals.journal_override("fx-delta", cat) == {}
    assert journals.journal_profile("fx-delta", cat) == base
    merged = journals.journal_profile("fx-beta", cat)
    lab = profiles.load("lab-publication-v1")
    assert merged["axis_policy"] == base["axis_policy"]
    assert merged["axis_policy"] != lab["axis_policy"]
    assert merged["allowed_aspect_ratios"] == []
    assert merged["font_family"]["latin"] == ""  # 只写了 latin_accepted，latin 不被补
    assert merged["derived_from"] == journals.BASE_PROFILE_ID


def test_source_link_forms():
    cat = _fixture()
    src = journals.journal_sources("fx-alpha", cat)
    assert src["widths_mm.single"]["link"].endswith("/artwork#:~:text=FIXTURE%20single")
    assert src["min_raster_dpi"]["link"].endswith("/guide.pdf#page=12")
    assert src["widths_mm.double"]["link"] == src["widths_mm.double"]["url"]


def test_env_override_points_at_another_catalog(tmp_path, monkeypatch):
    other = tmp_path / "j.json"
    other.write_text(json.dumps(_fixture()), encoding="utf-8")
    monkeypatch.setenv(journals.JOURNALS_ENV, str(other))
    assert [j["journal_id"] for j in journals.list_journals()] == [
        "fx-adv",
        "fx-adv-own",
        "fx-alpha",
        "fx-beta",
        "fx-cols",
        "fx-cols-inherit",
        "fx-delta",
        "fx-epsilon",
        "fx-gamma",
        "fx-zeta",
    ]


def test_unreadable_or_garbled_file_is_a_schema_error(tmp_path, monkeypatch):
    monkeypatch.setenv(journals.JOURNALS_ENV, str(tmp_path / "missing.json"))
    with pytest.raises(journals.JournalCatalogError) as exc:
        journals.load_catalog()
    assert exc.value.code == "journal_catalog_schema"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(journals.JOURNALS_ENV, str(bad))
    with pytest.raises(journals.JournalCatalogError) as exc:
        journals.load_catalog()
    assert exc.value.code == "journal_catalog_schema"


def test_errors_are_profile_errors():
    """调用方按 ProfileError 转 4xx 的既有路径不用改。"""
    assert issubclass(journals.JournalCatalogError, profiles.ProfileError)
