"""Capture provenance survives cache eviction; unknown legacy stems keep placeholders."""

import json
import os
import shutil
from pathlib import Path

import pytest

from tavotto.engine import atomicio, discover, figcapture, probe, registry, runconfig, runtimeasset


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(registry, "_DEFAULT", registry.Registry())
    root = tmp_path / "project"
    root.mkdir()
    (root / "plot.py").write_text("# Unmodified script identity\n", encoding="utf-8")
    discover.register(root, "plot.py", ["plot", "other"], entry="__main__")
    return root, registry.open_registry(root)


def capture(root: Path, stem: str, config_id="", *, register=True):
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
    if register:
        result = probe.register_probed(
            root,
            "plot.py",
            {"entry": "__main__", "stems": [stem], "run_config": config_id},
        )
        assert result["registered"], result
    svg = root / "preview.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    assert runtimeasset.materialize(root, desc, svg)
    return desc["asset_id"]


def listing(project):
    root, reg = project
    reg.load(root)
    return runtimeasset.list_assets(root, reg, worker_python="python")


def test_only_real_variants_are_offered_for_a_configured_stem(project):
    root, _ = project
    cfg = runconfig.put(root, "plot.py", ["hppc"])
    # This stem is first registered by a configured capture, not an unknown legacy entry.
    observed = capture(root, "new-plot", cfg.id)
    rows = listing(project)
    assert [row["id"] for row in rows if row["stem"] == "new-plot"] == [observed]
    assert next(row for row in rows if row["id"] == observed)["run_config"] == cfg.id
    assert next(row for row in rows if row["stem"] == "other")["cached"] is False
    # Losing all previews must not turn a configured-only stem into a no-argv identity.
    assert runtimeasset.prune_cache(keep=0) == 1
    assert all(row["stem"] != "new-plot" for row in listing(project))


def test_both_real_default_and_parameterized_captures_remain_distinct(project):
    root, reg = project
    plain = capture(root, "plot")
    ids = [plain]
    for value in ("one", "two"):
        cfg = runconfig.put(root, "plot.py", [value])
        ids.append(capture(root, "plot", cfg.id))
    assert {row["id"] for row in listing(project) if row["stem"] == "plot"} == set(ids)
    assert runtimeasset.resolve(plain, reg) == {
        "script": "plot.py",
        "stem": "plot",
        "entry": "__main__",
        "cost": "medium",
    }
    configured = runtimeasset.resolve(ids[-1], reg)
    assert configured["run_config"] == cfg.id
    assert runtimeasset.run_selection(root, configured).argv == ("two",)


def test_default_asset_survives_pruning_only_its_older_cache(project):
    root, reg = project
    # Start fresh: the legacy fallback must not mask missing argument-free provenance.
    discover.register(root, "plot.py", [], entry="__main__")
    plain = capture(root, "plot")
    cfg = runconfig.put(root, "plot.py", ["hppc"])
    configured = capture(root, "plot", cfg.id)
    base_cache = runtimeasset.cache_dir(root, plain)
    configured_cache = runtimeasset.cache_dir(root, configured)
    os.utime(base_cache, (1, 1))
    os.utime(configured_cache, (2, 2))
    provenance_path = runconfig.store_path(root)
    before = provenance_path.read_bytes()
    capture_history = json.loads(before)["captures"]["plot.py"]
    assert capture_history["legacy_stems"] == []
    history = capture_history["runs"]
    assert history[""] == ["plot"]
    assert history[cfg.id] == ["plot"]

    assert runtimeasset.prune_cache(keep=1) == 1
    assert not base_cache.exists()
    assert configured_cache.is_dir()
    assert provenance_path.read_bytes() == before
    rows = {row["id"]: row for row in listing(project)}
    assert set(rows) == {plain, configured}
    assert rows[plain]["cached"] is False
    assert rows[plain]["status"] == runtimeasset.STALE_NEEDS_RERUN
    assert rows[configured]["cached"] is True
    # An existing document keeps its exact identity and argument-free replay selection.
    assert runtimeasset.run_selection(root, runtimeasset.resolve(plain, reg)) is None
    assert provenance_path.read_bytes() == before  # listing remains read-only


def test_a_document_replay_also_records_the_argument_free_capture(project):
    root, _ = project
    cfg = runconfig.put(root, "plot.py", ["hppc"])
    configured = capture(root, "new-plot", cfg.id)
    # Rendering a saved base identity can materialize without another probe registration.
    plain = capture(root, "new-plot", register=False)
    shutil.rmtree(runtimeasset.cache_dir(root, plain))
    assert {row["id"] for row in listing(project) if row["stem"] == "new-plot"} == {
        plain,
        configured,
    }


def test_an_unexecuted_configuration_does_not_suppress_legacy_placeholders(project):
    root, _ = project
    runconfig.put(root, "plot.py", ["hppc"])
    rows = listing(project)
    assert len(rows) == 2
    assert all(row["cached"] is False and "run_config" not in row for row in rows)


def test_a_configured_run_does_not_reclassify_unknown_legacy_stems(project):
    root, _ = project
    cfg = runconfig.put(root, "plot.py", ["hppc"])
    configured = capture(root, "plot", cfg.id)
    plain = figcapture.runtime_asset_id("plot.py", "plot")
    rows = {row["id"]: row for row in listing(project) if row["stem"] == "plot"}
    assert set(rows) == {plain, configured}
    assert rows[plain]["cached"] is False


@pytest.mark.parametrize("history", [None, {"plot.py": {"runs": {"": 42}, "legacy_stems": []}}])
def test_legacy_or_malformed_history_keeps_base_beside_a_configured_cache(project, history):
    root, _ = project
    cfg = runconfig.put(root, "plot.py", ["hppc"])
    configured = capture(root, "new-plot", cfg.id)
    path = runconfig.store_path(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    if history is None:
        data.pop("captures")  # old stores have no capture history
    else:
        data["captures"] = history
    path.write_text(json.dumps(data), encoding="utf-8")
    plain = figcapture.runtime_asset_id("plot.py", "new-plot")
    assert {row["id"] for row in listing(project) if row["stem"] == "new-plot"} == {
        plain,
        configured,
    }
    # Subsequent configuration writes retain fail-open treatment of this old stem.
    again = runconfig.put(root, "plot.py", ["other"])
    capture(root, "new-plot", again.id)
    assert plain in {row["id"] for row in listing(project)}


def test_argument_free_provenance_is_per_stem_not_per_script(project):
    root, _ = project
    plain = capture(root, "plot")
    cfg = runconfig.put(root, "plot.py", ["hppc"])
    configured = capture(root, "configured-plot", cfg.id)
    assert {row["id"] for row in listing(project)} == {plain, configured}


def test_static_merge_marks_only_new_stems_and_preserves_unrun_candidates(project):
    root, _ = project
    (root / "plot.py").write_text(
        "import matplotlib.pyplot as plt\n"
        'plt.savefig("plot.pdf")\n'
        'plt.savefig("first.pdf")\n'
        'plt.savefig("second.pdf")\n',
        encoding="utf-8",
    )
    cfg, _, _ = discover.merge(root)
    discover.write_config(root, cfg, static_discovery=True)
    assert discover.registered_stem_origins(root, "plot.py") == {
        "plot": "unknown",
        "other": "unknown",
        "first": "static",
        "second": "static",
    }
    before = registry.registry_path(root).read_bytes()
    assert before == atomicio.dumps_json(cfg, indent=1)
    assert all(
        set(entry) == {"entry", "cost", "notes", "stems"} for entry in cfg["scripts"].values()
    )
    history = json.loads(runconfig.store_path(root).read_text(encoding="utf-8"))["captures"][
        "plot.py"
    ]
    assert history["discovered"] == ["first", "second"]
    # Loading and writing the unchanged registry does not discard local provenance.
    reg = registry.open_registry(root)
    discover.write_config(root, {"version": 1, "scripts": reg.entries()})
    assert registry.registry_path(root).read_bytes() == before
    run = runconfig.put(root, "plot.py", ["hppc"])
    first = capture(root, "first", run.id)
    assert discover.registered_stem_origins(root, "plot.py")["second"] == "static"
    second = capture(root, "second", run.id)
    assert {row["id"] for row in listing(project) if row["stem"] in {"first", "second"}} == {
        first,
        second,
    }
    # A rescan of an old, source-unknown stem cannot manufacture a static-only origin.
    old = capture(root, "plot", run.id)
    assert {row["id"] for row in listing(project) if row["stem"] == "plot"} == {
        old,
        figcapture.runtime_asset_id("plot.py", "plot"),
    }


def test_positive_old_base_cache_is_preserved_when_static_history_is_missing(project):
    root, _ = project
    (root / "plot.py").write_text(
        'import matplotlib.pyplot as plt\nplt.savefig("plot.pdf")\n', encoding="utf-8"
    )
    draft, _ = discover.build_draft(root)
    discover.write_config(root, draft, static_discovery=True)
    plain = capture(root, "plot", register=False)
    run = runconfig.put(root, "plot.py", ["hppc"])
    # Simulate a base preview written before durable capture history existed.
    path = runconfig.store_path(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["captures"]["plot.py"] = {"runs": {}, "legacy_stems": [], "discovered": ["plot"]}
    path.write_text(json.dumps(data), encoding="utf-8")
    configured = capture(root, "plot", run.id)
    shutil.rmtree(runtimeasset.cache_dir(root, plain))
    assert {row["id"] for row in listing(project)} == {plain, configured}
    assert json.loads(path.read_text(encoding="utf-8"))["captures"]["plot.py"]["runs"][""] == [
        "plot"
    ]


@pytest.mark.parametrize("static", [None, "plot", ["plot", 7]])
def test_missing_or_invalid_static_origins_fail_open(project, static):
    root, _ = project
    run = runconfig.put(root, "plot.py", ["hppc"])
    path = runconfig.store_path(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["captures"] = {"plot.py": {"runs": {}, "legacy_stems": [], "discovered": static}}
    path.write_text(json.dumps(data), encoding="utf-8")
    configured = capture(root, "plot", run.id)
    plain = figcapture.runtime_asset_id("plot.py", "plot")
    assert {row["id"] for row in listing(project) if row["stem"] == "plot"} == {plain, configured}


def test_discovery_history_is_local_and_only_records_a_successful_write(project, monkeypatch):
    root, _ = project
    (root / "plot.py").write_text(
        'import matplotlib.pyplot as plt\nplt.savefig("new.pdf")\n', encoding="utf-8"
    )
    registry_path = registry.registry_path(root)
    before = registry_path.read_bytes()
    cfg, _, _ = discover.merge(root)
    local_history = runconfig.store_path(root)
    assert not local_history.exists()  # A scan/draft alone never records provenance.
    assert registry_path.read_bytes() == before

    def fail_write(*args, **kwargs):
        raise OSError("injected registry write failure")

    with monkeypatch.context() as m:
        m.setattr(atomicio, "write_json", fail_write)
        with pytest.raises(OSError, match="injected registry write failure"):
            discover.write_config(root, cfg, static_discovery=True)
    assert not local_history.exists()
    assert registry_path.read_bytes() == before

    discover.write_config(root, cfg, static_discovery=True)
    assert registry_path.read_bytes() == atomicio.dumps_json(cfg, indent=1)
    assert all(
        set(entry) == {"entry", "cost", "notes", "stems"} for entry in cfg["scripts"].values()
    )
    assert local_history.is_relative_to(root.parent / "state")
    assert runconfig.discovered_stems(root, "plot.py") == {"new"}
    configured = capture(root, "new", runconfig.put(root, "plot.py", ["hppc"]).id)
    assert runconfig.discovered_stems(root, "plot.py") == set()
    assert [row["id"] for row in listing(project) if row["stem"] == "new"] == [configured]
