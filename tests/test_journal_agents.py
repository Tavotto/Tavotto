"""内置期刊库的 Agent 入口（ADR 0155 §9）：引擎侧的选择 / 列表 / 开关，以及与桌面开关的同源对拍。

期刊用夹具库（fx-*，example.invalid）；真实库此时是空库。
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from tavotto.engine import journals

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "golden" / "journal_vectors.json").read_text("utf-8"))
UI_SOURCE = ROOT / "web" / "src" / "lib" / "journalUi.ts"
_UI_FLAG = re.compile(r"^export const JOURNAL_CATALOG_UI = (true|false)\s*$", re.MULTILINE)


def _catalog() -> dict:
    return copy.deepcopy(VECTORS["catalog"])


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)


def _code(fn, *a, **k) -> str:
    with pytest.raises(journals.JournalCatalogError) as exc:
        fn(*a, **k)
    return exc.value.code


# ------------------------------- 开关同源 -------------------------------------
def _ui_flag_literal(path: Path = UI_SOURCE) -> bool:
    m = _UI_FLAG.search(path.read_text("utf-8"))
    assert m, (
        "journalUi.ts 里读不到 `export const JOURNAL_CATALOG_UI = true|false`——别让对拍变成恒真"
    )
    return m.group(1) == "true"


def test_agent_switch_is_the_desktop_switch_literally():
    """判据主语：两边常量的字面值。桌面开了 Agent 没开（或反过来）= 一边看得到期刊库、一边看不到。"""
    assert journals.JOURNAL_CATALOG_AGENTS is _ui_flag_literal()


def test_the_parity_reader_fails_when_the_ts_constant_is_unreadable(tmp_path):
    """尺子自检：读不出就失败，不许恒真；读得出就如实返回字面值。"""
    broken = tmp_path / "journalUi.ts"
    broken.write_text("export const JOURNAL_CATALOG_UI = flag()\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        _ui_flag_literal(broken)
    broken.write_text("export const JOURNAL_CATALOG_UI = true\n", encoding="utf-8")
    assert _ui_flag_literal(broken) is True


# ------------------------------- 选期刊 ---------------------------------------
def test_disabled_switch_refuses_before_anything_else(monkeypatch):
    monkeypatch.setattr(
        journals, "JOURNAL_CATALOG_AGENTS", False
    )  # 开关已随 ADR 0155 §11 打开；这里造「关着」
    assert (
        _code(journals.select_for_agent, "fx-alpha", None, None, _catalog())
        == "journal_catalog_disabled"
    )


def test_select_matches_the_desktop_binding(on):
    cat = _catalog()
    pid, over, ref = journals.select_for_agent("fx-alpha", None, None, cat)
    assert pid == journals.BASE_PROFILE_ID
    assert over == journals.journal_override("fx-alpha", cat)
    assert ref["id"] == "fx-alpha" and ref["catalog_rev"] == cat["catalog_rev"]
    dates = [s["retrieved"] for s in journals.journal_sources("fx-alpha", cat).values()]
    assert ref["retrieved"] == max(dates)


def test_select_refuses_to_guess_between_journal_id_and_hand_written(on):
    cat = _catalog()
    assert _code(journals.select_for_agent, "fx-alpha", "lab-publication-v1", None, cat) == (
        "journal_id_conflict"
    )
    assert _code(journals.select_for_agent, "fx-alpha", None, {"widths_mm": {}}, cat) == (
        "journal_id_conflict"
    )


def test_select_unknown_id(on):
    assert _code(journals.select_for_agent, "nope", None, None, _catalog()) == "journal_unknown"
    assert _code(journals.select_for_agent, "  ", None, None, _catalog()) == "journal_unknown"


def test_agent_error_codes_are_outside_the_shared_closed_set():
    assert not set(journals.AGENT_ERROR_CODES) & set(journals.ERROR_CODES)


# ------------------------------- 列表 -----------------------------------------
def test_search_lists_everything_and_filters_by_nfkc_lowercase(on):
    cat = _catalog()
    full = journals.search_journals(None, cat)
    assert full["catalog_rev"] == cat["catalog_rev"]
    assert [j["id"] for j in full["journals"]] == sorted(cat["journals"])
    assert set(full["journals"][0]) == {"id", "label", "publisher", "aliases"}
    assert {j["id"] for j in journals.search_journals("fxa", cat)["journals"]} == {"fx-alpha"}
    # 全角 + 大写与别名同一口径
    assert {j["id"] for j in journals.search_journals("ＦＸＡ", cat)["journals"]} == {"fx-alpha"}
    assert journals.search_journals("zzz-no-such", cat)["journals"] == []


def test_shipped_catalog_lists_the_seed():
    out = journals.search_journals(None)
    assert len(out["journals"]) == len(journals.load_catalog()["journals"]) >= 30
    assert out["catalog_rev"] >= 1
