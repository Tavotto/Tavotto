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
    assert base["severity"]["page-width"] == "suggestion"
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
    assert src["widths_mm.double"]["scope"] == "journal"
    assert "widths_mm.single" not in src or src["widths_mm.single"]["scope"] == "publisher"
    assert journals.journal_sources("fx-alpha", cat)["widths_mm.double"]["scope"] == "publisher"


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
        "fx-alpha",
        "fx-beta",
        "fx-delta",
        "fx-gamma",
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
