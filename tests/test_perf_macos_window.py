"""macOS 窗口诊断脚本的解析、归因边界与数据脱敏契约。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import perf_macos_window as P  # noqa: E402


def test_process_family_excludes_unrelated_webkit_and_counts_descendants():
    rows = P.parse_ps(
        "100 1 5.2 102400 /Applications/Tavotto.app/Contents/MacOS/Tavotto\n"
        "101 100 2.5 20480 /Library/Frameworks/Python Framework/Python\n"
        "102 101 1.0 10240 /somewhere/workerd\n"
        "200 1 25.0 40960 /System/Library/Frameworks/WebKit.WebContent\n"
        "300 1 9.0 40000 /System/Library/PrivateFrameworks/SkyLight.framework/WindowServer\n"
    )
    m = P.classify(rows)
    assert m["root_count"] == 1
    assert m["family_processes"] == 3
    assert m["family_cpu_pct"] == 8.7
    assert m["family_rss_sum_mib"] == 130.0
    assert m["windowserver_global_cpu_pct"] == 9.0


def test_no_app_is_not_silently_assumed_running():
    data = P.classify(P.parse_ps("200 1 25.0 40960 WebKit.WebContent\n"))
    assert data["root_count"] == 0
    assert data["family_processes"] == 0
    assert data["windowserver_global_cpu_pct"] is None


def test_report_has_numerical_evidence_only_and_cannot_claim_gpu_measurement():
    report = {
        "schema": P.SCHEMA,
        "stage": "drag",
        "samples": [
            {
                "root_count": 1,
                "family_cpu_pct": 6.0,
                "family_rss_sum_mib": 250.0,
                "windowserver_global_cpu_pct": 12.0,
            },
            {
                "root_count": 1,
                "family_cpu_pct": 10.0,
                "family_rss_sum_mib": 258.0,
                "windowserver_global_cpu_pct": 24.0,
            },
        ],
    }
    stats = P.summarize(report)
    assert stats["family_cpu_pct"]["p95"] == 10
    assert stats["windowserver_global_cpu_pct"]["p50"] == 12
    assert "Tavotto.app" not in json.dumps(report)
    assert "WindowServer" not in json.dumps(stats)


def test_invalid_schema_rejected():
    try:
        P.summarize({"schema": "other", "samples": []})
    except ValueError:
        pass
    else:
        raise AssertionError("foreign reports must fail")


def test_native_resource_delta_is_not_mislabeled_as_presentation_pass():
    idle = {
        "schema": P.SCHEMA,
        "stage": "idle",
        "macos_version": "26.1",
        "arch": "arm64",
        "samples": [
            {"family_cpu_pct": 2.0, "family_rss_sum_mib": 120.0, "windowserver_global_cpu_pct": 8.0}
            for _ in range(8)
        ],
    }
    drag = {
        **idle,
        "stage": "drag",
        "samples": [
            {
                "family_cpu_pct": 8.0,
                "family_rss_sum_mib": 128.0,
                "windowserver_global_cpu_pct": 21.0,
            }
            for _ in range(8)
        ],
    }
    report = P.compare_reports(idle, drag)
    assert report["status"] == "diagnostic_only"
    assert report["delta_p95"]["family_cpu_pct"] == 6.0
    assert report["delta_p95"]["windowserver_global_cpu_pct"] == 13.0
    assert "display_ghosting" in report["not_covered"]


def test_native_resource_delta_rejects_cross_os_samples():
    report = {
        "schema": P.SCHEMA,
        "stage": "idle",
        "macos_version": "26.1",
        "arch": "arm64",
        "samples": [{"family_cpu_pct": 1.0, "family_rss_sum_mib": 100.0} for _ in range(8)],
    }
    active = {**report, "stage": "drag", "macos_version": "25.4"}
    assert P.compare_reports(report, active)["status"] == "incomparable"
