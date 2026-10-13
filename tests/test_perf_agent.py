"""Agent 性能对照工具：不把缺失基线/错机器/少样本误报成 pass。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import perf_agent as A  # noqa: E402


def report(n=16, value=20, browser="chromium"):
    return {
        "schema": A.SCHEMA,
        "iterations": n,
        "environment": {
            "platform": "linux", "arch": "x64", "browser": browser,
            "browser_major": 125, "viewport_w": 1280, "viewport_h": 720,
            "dpr": 1, "headless": True,
        },
        "scenarios": [
            {
                "id": key,
                "two_raf_ms": [value for _ in range(n * (4 if key == "dialog.presets_open" else 1))],
            }
            for key in sorted(A.ALLOWED_SCENARIOS)
        ],
    }


def test_valid_complete_report():
    a = A.summarize(report())
    assert a["status"] == "ok"
    assert a["coverage"] == "browser_webview_proxy_only"
    assert a["scenarios"]["symbol.alpha"]["count"] == 16


def test_missing_scenario_or_browser_is_not_pass():
    x = report()
    x["scenarios"].pop()
    assert A.summarize(x)["status"] == "incomplete"
    y = report(browser="webkit")
    assert A.compare(y, report())["status"] == "incomparable"


def test_regression_requires_relative_and_absolute_delta():
    assert A.compare(report(value=51), report(value=20))["status"] == "regression"
    assert A.compare(report(value=23), report(value=20))["status"] == "pass"
    assert A.compare(report(value=11.0), report(value=8.0))["status"] == "pass"


def test_too_few_samples_not_comparable():
    assert A.compare(report(n=3), report(n=3))["status"] == "incomparable"


def test_summary_rejects_untrusted_names_and_boolean_times():
    data = report()
    data["scenarios"].append({"id": "private.py", "two_raf_ms": [100] * 16})
    data["scenarios"][1]["two_raf_ms"][0] = True
    s = A.summarize(data)
    assert "private.py" not in json.dumps(s)
    assert s["status"] == "incomplete"


def test_load_checks_schema_and_file_size(tmp_path):
    p = tmp_path / "measure.json"
    p.write_text(json.dumps(report()), encoding="utf-8")
    assert A.load(p)["schema"] == A.SCHEMA
    p.write_text(json.dumps({"schema": "alien"}), encoding="utf-8")
    try:
        A.load(p)
    except ValueError:
        pass
    else:
        raise AssertionError("untrusted schema accepted")
