"""`scripts/dev/exectrace_bench.py --quick`：报告 schema 与「关闭时什么都不写」（ADR 0151 PR4 §4.7）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_quick_bench_schema_and_off_mode_writes_nothing(tmp_path):
    out = tmp_path / "r.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "dev" / "exectrace_bench.py"),
            "--quick",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["schema"] == "tavotto.exectrace-bench/1"
    assert "environment-specific" in rep["note"]
    assert set(rep["machine"]) >= {"python", "platform"}
    text = json.dumps(rep)
    assert str(tmp_path) not in text and str(ROOT) not in text
    assert rep["off_check"] == {"null_trace": True, "files_written": 0}
    for side in ("on", "off"):
        assert rep["micro"][side]["span_pair"]["repeats"] >= 5
        assert rep["task"][side]["repeats"] >= 10
    assert rep["storage"]["at_caps"]["within_max_record_bytes"] is True
    assert rep["flood"]["dropped_count"] >= 0 and rep["memory"]["peak_bytes"] > 0
