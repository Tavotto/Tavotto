"""真实 Worker 诊断：拆分的阶段耗时应合回原 manifest_ms。

Flask Python 不一定装 matplotlib；需要独立 worker Python 时按仓库现有约定跳过。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
try:
    WORKER = pool.find_worker_python()
except pool.WorkerError:
    WORKER = None

DRIVER = r"""
import json, sys, tempfile
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import figcapture
from figsession import LiveFigureSession

with tempfile.TemporaryDirectory() as workdir:
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([1, 2, 3], [2, 1, 4], label="curve")
    ax.set_title("F")
    ax.legend()
    session = LiveFigureSession(workdir)
    session.add_figure("F", fig, figcapture.SOURCE_SAVEFIG)
    session.instrument_all()
    timing = {}
    result = session.render("F", timings=timing)
    print(json.dumps({"timing": timing, "elements": len(result["elements"])}))
"""


@pytest.mark.skipif(WORKER is None, reason="matplotlib worker not available")
def test_timing_breakdown_follows_real_session_and_preserves_total():
    result = subprocess.run(
        [str(WORKER), "-c", DRIVER, str(ENGINE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=120,
    )
    payload = json.loads(result.stdout.splitlines()[-1])
    times = payload["timing"]
    assert payload["elements"] > 0
    keys = (
        "manifest_ms",
        "manifest_prepare_ms",
        "manifest_layout_ms",
        "manifest_measure_ms",
        "manifest_final_geometry_ms",
    )
    for key in keys:
        assert 0 <= times[key] < 120_000, (key, times)
    assert (
        abs(
            times["manifest_ms"]
            - times["manifest_prepare_ms"]
            - times["manifest_final_geometry_ms"]
        )
        <= 0.01
    )
    prepare = times["manifest_prepare_ms"]
    gap = prepare - times["manifest_layout_ms"] - times["manifest_measure_ms"]
    assert abs(gap) <= max(2.0, prepare * 0.05)
