"""`scripts/dev/data_figure_bench.py`：手动跑分入口。这里只用假回答自测；`--real` 的护栏（要显式上限、没有连接就不发请求）也只测到「拒绝」这一步，
绝不对真模型发请求。子进程跑，数据 / 配置目录指到临时目录。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dev" / "data_figure_bench.py"


def bench(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_DATA_DIR": str(tmp_path / "d"),
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "c"),
        "TAVOTTO_NO_TELEMETRY": "1",
        "PYTHONUTF8": "1",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        cwd=tmp_path,
    )


def test_fake_good_answers_score_full_marks_and_write_a_report(tmp_path):
    out = tmp_path / "report.json"
    done = bench(tmp_path, "--fake", "good", "--out", str(out))
    assert done.returncode == 0, done.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    s = report["summary"]
    assert report["mode"] == "fake:good" and s["cases"] >= 12
    assert s["pass_rate"] == 1.0 and s["fallback_rate"] == 0 and s["violation_rate"] == 0
    assert s["requests"] == s["cases"] and s["cost_usd"] is None


def test_fake_mixed_answers_lower_the_score(tmp_path):
    out = tmp_path / "report.json"
    done = bench(
        tmp_path,
        "--fake",
        "mixed",
        "--wire",
        "openai-chat",
        "--only",
        "DF-01,DF-04",
        "--out",
        str(out),
    )
    assert done.returncode == 0, done.stderr
    s = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert s["cases"] == 2 and 0 < s["pass_rate"] < 1


def test_real_mode_refuses_without_an_explicit_call_cap(tmp_path):
    for args in (["--real"], ["--real", "--max-calls", "3"]):
        done = bench(tmp_path, *args)
        assert done.returncode == 2 and "没有发出任何请求" in done.stderr


def test_real_mode_without_a_connection_sends_nothing(tmp_path):
    done = bench(tmp_path, "--real", "--max-calls", "100")
    assert done.returncode == 2 and "没有可用的连接" in done.stderr
