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


def test_new_journal_stamps_and_mcp_proof_preserve_old_profile(monkeypatch, fixture_catalog):
    vectors = json.loads((ROOT / "tests/golden/profile_display_vectors.json").read_text())
    for vector in vectors:
        p = {"profile_id": vector["id"], "label": vector["label"], "version": "1"}
        before = copy.deepcopy(p)
        assert profiles.stamp(p)["label"] == vector["expected"]
        assert p == before
    monkeypatch.setattr(bridge.engine_telemetry, "capture", lambda *_args, **_kwargs: None)
    p = journals.journal_profile("fx-delta", fixture_catalog)
    p["journal_ref"] = journals.journal_ref("fx-delta", fixture_catalog)
    before = copy.deepcopy(p)
    s = session(p)
    s.project = "synthetic"
    s.script = "synthetic.py"
    s.patches = []
    monkeypatch.setattr(bridge, "get_session", lambda _id: s)
    checks = bridge.run_preflight("synthetic", export_formats=["png"], export_dpi=299)
    assert checks["profile"]["label"] == "期刊检查基础"
    assert "期刊检查基础" in checks["report"]
    assert "期刊未规定的项一律不判" not in checks["report"]
    proof = json.loads(
        bridge._proof_bytes(s, checks, [], 299, ["png"], forced=False, acknowledged=[])
    )
    assert proof["profile"]["label"] == "期刊检查基础"
    assert proof["journal_ref"] == p["journal_ref"]
    assert p == before
