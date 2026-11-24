"""推荐配色目录（ADR 0207）的结构、许可分档与排除开关的看护。

主语：`src/tavotto/profiles/palettes.json` / `palettes_copyleft.json` 这两份**文件本身**（前端整份 import 的也是它们），
以及 `engine/palettes.catalog()` 在「收 / 不收 GPL 色板」两档下给出的方案集合。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tavotto.engine import palettes as pal

ROOT = Path(__file__).resolve().parents[1]


def _raw(name: str) -> dict:
    return json.loads((pal.PROFILES_DIR / name).read_text(encoding="utf-8"))


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _contrast_on_white(h: str) -> float:
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 1.05 / (0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b) + 0.05)


def test_both_files_validate_and_ids_are_disjoint():
    pal._load.cache_clear()
    permissive, copyleft = pal._load()
    assert len(permissive["palettes"]) >= 20
    assert {p["source"] for p in copyleft["palettes"]} == {"ggsci"}


def test_permissive_file_holds_no_copyleft_licence():
    doc = _raw(pal.PERMISSIVE_FILE)
    licences = {s["license"] for s in doc["sources"].values()}
    assert licences <= pal.PERMISSIVE_LICENSES
    assert not licences & pal.COPYLEFT_LICENSES
    assert not doc.get("copyleft")


def test_copyleft_file_is_marked_and_only_copyleft():
    doc = _raw(pal.COPYLEFT_FILE)
    assert doc["copyleft"] is True
    assert {s["license"] for s in doc["sources"].values()} <= pal.COPYLEFT_LICENSES


def test_catalog_without_copyleft_has_no_gpl_entry():
    full = pal.catalog(include_copyleft=True)
    lean = pal.catalog(include_copyleft=False)
    assert any(p["copyleft"] for p in full["palettes"])
    assert not any(p["copyleft"] for p in lean["palettes"])
    assert all(p["license"] in pal.PERMISSIVE_LICENSES for p in lean["palettes"])
    assert "ggsci" not in lean["sources"]
    assert pal.palette("ggsci.npg", include_copyleft=False) is None
    assert pal.palette("ggsci.npg", include_copyleft=True)["colors"][0] == "#E64B35"


def test_env_switch_drops_copyleft(monkeypatch):
    monkeypatch.setenv(pal.NO_COPYLEFT_ENV, "1")
    assert not pal.copyleft_enabled()
    assert not any(p["copyleft"] for p in pal.catalog()["palettes"])
    monkeypatch.delenv(pal.NO_COPYLEFT_ENV)
    assert any(p["copyleft"] for p in pal.catalog()["palettes"])


def test_catalog_returns_fresh_objects():
    a = pal.catalog(include_copyleft=False)
    a["palettes"][0]["colors"].append("#000001")
    b = pal.catalog(include_copyleft=False)
    assert "#000001" not in b["palettes"][0]["colors"]


def test_crameri_entries_are_legible_on_white():
    """Crameri S 系列里混着很浅的颜色；目录只收对白底 ≥ 3:1 的（细线、小点也看得见）。"""
    doc = _raw(pal.PERMISSIVE_FILE)
    crameri = [p for p in doc["palettes"] if p["source"] == "crameri"]
    assert {"crameri.batlowS", "crameri.davosS", "crameri.hawaiiS", "crameri.lipariS"} <= {
        p["id"] for p in crameri
    }
    for p in crameri:
        low = [c for c in p["colors"] if _contrast_on_white(c) < 3.0]
        assert not low, (p["id"], low)


def test_every_source_has_its_notice():
    """每个来源都在 THIRD_PARTY_NOTICES.md 有一节；Apache-2.0 的 ColorBrewer 还得带上致谢句原文。"""
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    full = pal.catalog(include_copyleft=True)
    for sid, src in full["sources"].items():
        assert src["url"] in notices, sid
    assert full["sources"]["colorbrewer"]["attribution"] in notices
    assert "Copyright (c) 2020 Fabio Crameri" in notices
    assert full["sources"]["crameri"]["doi"] in notices


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["sources"]["crameri"].__setitem__("license", "GPL-3.0-or-later"), "许可"),
        (lambda d: d["palettes"].append(copy.deepcopy(d["palettes"][0])), "重复"),
        (lambda d: d["palettes"][0]["colors"].__setitem__(0, "red"), "非法色值"),
        (lambda d: d["palettes"][0].__setitem__("kind", "sequential"), "kind"),
        (lambda d: d["palettes"][0].__setitem__("source", "nowhere"), "没登记"),
        (lambda d: d["palettes"][0].__setitem__("colors", ["#000000", "#FFFFFF"]), "少于"),
        (lambda d: d.__setitem__("copyleft", True), "copyleft"),
    ],
)
def test_validate_rejects(mutate, needle):
    """反证：判据真的会拒，而不是恒过。"""
    doc = _raw(pal.PERMISSIVE_FILE)
    mutate(doc)
    with pytest.raises(pal.PaletteCatalogError, match=needle):
        pal.validate(doc, copyleft=False)


def test_gpl_palette_rejected_from_permissive_file():
    """把一套 ggsci 方案原样搬进宽松那份：来源的许可就过不了。"""
    doc = _raw(pal.PERMISSIVE_FILE)
    gpl = _raw(pal.COPYLEFT_FILE)
    doc["sources"]["ggsci"] = gpl["sources"]["ggsci"]
    doc["palettes"].append(gpl["palettes"][0])
    with pytest.raises(pal.PaletteCatalogError, match="ggsci"):
        pal.validate(doc, copyleft=False)
