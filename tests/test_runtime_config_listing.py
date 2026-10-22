"""A configured capture is not an argument-free capture of the same registered stem."""

from pathlib import Path

import pytest

from tavotto.engine import figcapture, registry, runconfig, runtimeasset


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "state"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "plot.py").write_text("# Unmodified script identity\n", encoding="utf-8")
    reg = registry.Registry()
    reg.load_data({"scripts": {"plot.py": {"entry": "__main__", "stems": ["plot", "other"]}}})
    return root, reg


def capture(root: Path, stem: str, config_id=""):
    desc = figcapture.build_descriptor(
        script="plot.py",
        entry="__main__",
        stem=stem,
        capture_source=figcapture.SOURCE_PYPLOT,
        execution_profile=figcapture.PROFILE_SAFE,
        size_mm=(120, 90),
        source_fingerprint="sha256:example",
        run_config=config_id,
    ).to_payload()
    svg = root / "preview.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    assert runtimeasset.materialize(root, desc, svg)
    return desc["asset_id"]


def listing(project):
    root, reg = project
    return runtimeasset.list_assets(root, reg, worker_python="python")


def test_only_real_variants_are_offered_for_a_configured_stem(project):
    root, _ = project
    cfg = runconfig.put(root, "plot.py", ["hppc"])
    observed = capture(root, "plot", cfg.id)
    rows = listing(project)
    assert [row["id"] for row in rows if row["stem"] == "plot"] == [observed]
    assert next(row for row in rows if row["id"] == observed)["run_config"] == cfg.id
    # A different, never captured registered stem still has its legacy placeholder.
    assert next(row for row in rows if row["stem"] == "other")["cached"] is False


def test_both_real_default_and_parameterized_captures_remain_distinct(project):
    root, reg = project
    plain = capture(root, "plot")
    ids = [plain]
    for value in ("one", "two"):
        cfg = runconfig.put(root, "plot.py", [value])
        ids.append(capture(root, "plot", cfg.id))
    assert {row["id"] for row in listing(project) if row["stem"] == "plot"} == set(ids)
    # Saved document identities keep resolving independently of the listing policy.
    assert runtimeasset.resolve(plain, reg) == {
        "script": "plot.py",
        "stem": "plot",
        "entry": "__main__",
        "cost": "medium",
    }


def test_an_unexecuted_configuration_does_not_suppress_legacy_placeholders(project):
    root, _ = project
    runconfig.put(root, "plot.py", ["hppc"])
    rows = listing(project)
    assert len(rows) == 2
    assert all(row["cached"] is False and "run_config" not in row for row in rows)
