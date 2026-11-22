"""科学可视化指导条目（`profiles/sciviz_guidance.json`）的结构看护。

主语：那份 JSON 本身，以及它与 `docs/sciviz/` 的原则 / 规则规格、`publication.json` 的预检 id 之间的指向。
条目内容的真假（引文、页码）在入库时对照原书逐字核过，这里核不了原书，只钉住结构纪律。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tavotto.engine import sciviz_guidance as sg

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "sciviz"


def _publication_check_ids() -> set[str]:
    data = json.loads((ROOT / "src/tavotto/profiles/publication.json").read_text(encoding="utf-8"))
    ids: set[str] = set()
    for prof in data["profiles"].values():
        ids.update((prof.get("severity") or {}).keys())
    return ids


def _doc_headings(name: str, pattern: str) -> set[str]:
    text = (DOCS / name).read_text(encoding="utf-8")
    return set(re.findall(rf"^### ({pattern})\b", text, re.M))


def test_ids_unique_and_well_formed():
    ids = [e["id"] for e in sg.entries()]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"G-\d{3}", i) for i in ids), ids


@pytest.mark.parametrize("e", sg.entries(), ids=lambda e: e["id"])
def test_entry_fields_in_closed_sets(e):
    assert e["basis"] in sg.BASES
    assert e["strength"] in sg.STRENGTHS
    assert e["decidable"] in sg.DECIDABLE
    assert e["title"].strip() and e["agent"].strip()
    assert e["evidence"], "每条至少一条依据"
    sources = sg.load()["sources"]
    for ev in e["evidence"]:
        assert ev["src"] in sources
        assert ev["tag"] in sg.TAGS
        quote = ev.get("quote")
        if quote is not None:
            assert len(quote.split()) <= 25, "引文 ≤ 25 词（版权口径）"
            assert ev["tag"] == "原文", "有逐字引文的依据必须标「原文」"


@pytest.mark.parametrize("e", sg.entries(), ids=lambda e: e["id"])
def test_taste_entries_never_become_checks(e):
    """品味条目给 Agent 当参考，但绝不进预检判据（反伪科学规则）。"""
    if e["basis"] == "taste":
        assert e["strength"] == "preference"
        assert e["decidable"] == "no"
        assert e["checks"] == []
        assert e["spec"] == []


@pytest.mark.parametrize("e", sg.entries(), ids=lambda e: e["id"])
def test_inference_strength_is_labelled_as_inference(e):
    """strength=inference 的条目必须带一条「推导」依据：不许把 Tavotto 的推导包装成原书的结论。"""
    if e["strength"] == "inference":
        assert any(ev["tag"] == "推导" for ev in e["evidence"])


def test_checks_point_at_real_preflight_ids():
    known = _publication_check_ids()
    for e in sg.entries():
        for c in e["checks"]:
            assert c in known, f"{e['id']} 指向不存在的预检 id {c}"


def test_principles_and_specs_exist_in_docs():
    principles = _doc_headings("principles.md", r"P\d+")
    specs = _doc_headings("validation-spec.md", r"V-\d{3}")
    for e in sg.entries():
        for p in e["principles"]:
            assert p in principles, f"{e['id']} 指向 docs/sciviz/principles.md 里没有的 {p}"
        for v in e["spec"]:
            assert v in specs, f"{e['id']} 指向 docs/sciviz/validation-spec.md 里没有的 {v}"


def test_render_is_deterministic_and_order_independent():
    a = sg.render(ids=["G-018", "G-001", "G-043"])
    b = sg.render(ids=["G-043", "G-018", "G-001"])
    assert a == b
    assert (
        a.index("[G-001]") < a.index("[G-018]") < a.index("[G-043]")
    )  # 类序：encoding → visual → taste
    assert "作者偏好" in a


def test_render_unknown_id_raises():
    with pytest.raises(KeyError):
        sg.render(ids=["G-999"])


def test_render_full_mentions_every_entry_once():
    text = sg.render()
    for e in sg.entries():
        assert text.count(f"[{e['id']}]") == 1
