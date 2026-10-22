"""The diagnostic must observe real arrays without consuming RNG or changing Figures."""

import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import research_numeric_probe as probe  # noqa: E402


@pytest.mark.parametrize("draw", [False, True])
@pytest.mark.parametrize("product_tracer", [False, True])
def test_observer_leaves_arrays_and_rng_unchanged_and_detects_changed_data(
    tmp_path, monkeypatch, draw, product_tracer
):
    np = pytest.importorskip("numpy")
    pytest.importorskip("matplotlib")
    source = """import numpy as np
import matplotlib.pyplot as plt
np.random.seed(42)
K = np.eye(2)
y = np.random.multivariate_normal(np.zeros(2), K, size=4)
fig, ax = plt.subplots()
ax.plot(y[:, 0], y[:, 1])
"""
    if draw:
        source += "fig.canvas.draw()\n"
    script = tmp_path / "synthetic.py"
    script.write_text(source)
    code = compile(source, str(script), "exec")
    plain = {}
    exec(code, plain)
    expected = probe.figure_state(np, plain["fig"])
    expected_rng = probe.rng_identity(np)
    expected_stale = dict(plain["ax"]._stale_viewlims)
    path = tmp_path / "observations.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    monkeypatch.setenv("CG_EXEC_LOG", str(tmp_path / "native-execs.jsonl"))
    try:
        from tavotto.engine.executionfact import on_user_entry

        entries = []
        context = (
            on_user_entry(script, lambda: entries.append("entered"))
            if product_tracer
            else nullcontext()
        )
        with context:
            existing_trace = sys.gettrace()
            probe.attach(code, script, tmp_path, fd)
            assert sys.gettrace() is existing_trace
            observed = {}
            exec(code, observed)
        assert entries == (["entered"] if product_tracer else [])
    finally:
        sys.setprofile(None)
        sys.settrace(None)
        os.close(fd)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    phases = {"seed_return", "script_return"}
    if draw:
        phases.add("draw_return")
    assert {r["phase"] for r in rows} == phases
    assert observed["ax"]._stale_viewlims == expected_stale
    assert probe.figure_state(np, observed["fig"]) == expected
    assert probe.rng_identity(np) == expected_rng
    changed = observed["y"].copy()
    changed[-1, -1] += 0.125
    assert probe.array_identity(np, changed) != probe.array_identity(np, observed["y"])
    plain["plt"].close("all")
