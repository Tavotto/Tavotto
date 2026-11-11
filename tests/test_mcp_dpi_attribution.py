"""Journal-bound MCP DPI warnings report their provenance boundary."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin/mcp"))
from tavotto.engine import journals, profiles  # noqa: E402
from tavotto_mcp import bridge  # noqa: E402


@pytest.fixture
def fixture_catalog():
    return json.loads((ROOT / "tests/golden/journal_vectors.json").read_text())["catalog"]


def session(profile):
    return SimpleNamespace(
        id="synthetic",
        stem="synthetic",
        manifest={"size_mm": [100, 80], "elements": []},
        profile=profile,
        patch_hash=lambda: "synthetic",
    )


def test_actual_mcp_preflight_marks_journal_source_unverified(monkeypatch, fixture_catalog):
    monkeypatch.setattr(bridge.engine_telemetry, "capture", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bridge, "get_session", lambda _id: session(profiles.load()))
    monkeypatch.setattr(
        bridge,
        "journal_selection",
        lambda pid, journal, jid: (
            journals.select_for_agent(jid, pid, journal, fixture_catalog)
            if jid
            else (pid, journal, None)
        ),
    )
    for jid, threshold in [("fx-delta", 300), ("fx-zeta", 450)]:
        out = bridge.run_preflight(
            "synthetic", journal_id=jid, export_formats=["png"], export_dpi=threshold - 1
        )
        issue = next(i for i in out["warnings"] if i["id"] == "raster-dpi")
        assert issue["severity"] == "warn"
        assert issue["detail"]["min_dpi"] == threshold
        assert issue["message"]["key"] == "exportRasterDpiJournalSourceUnverified"
        assert "此回执未核验该阈值的期刊出处" in issue["text"]
        assert "此回执未核验该阈值的期刊出处" in out["report"]
        assert out["journal_ref"]["id"] == jid


def test_inherited_journal_session_and_plain_profile_remain_distinct(monkeypatch, fixture_catalog):
    monkeypatch.setattr(bridge.engine_telemetry, "capture", lambda *_args, **_kwargs: None)
    p = journals.journal_profile("fx-delta", fixture_catalog)
    p["journal_ref"] = journals.journal_ref("fx-delta", fixture_catalog)
    original = copy.deepcopy(p)
    monkeypatch.setattr(bridge, "get_session", lambda _id: session(p))
    out = bridge.run_preflight("synthetic", export_formats=["png"], export_dpi=299)
    assert (
        next(i for i in out["warnings"] if i["id"] == "raster-dpi")["message"]["key"]
        == "exportRasterDpiJournalSourceUnverified"
    )
    assert p == original
    monkeypatch.setattr(bridge, "get_session", lambda _id: session(profiles.load()))
    plain = bridge.run_preflight("synthetic", export_formats=["png"], export_dpi=299)
    assert (
        next(i for i in plain["warnings"] + plain["errors"] if i["id"] == "raster-dpi")["message"][
            "key"
        ]
        == "exportRasterDpi"
    )
