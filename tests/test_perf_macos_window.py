"""macOS 窗口诊断脚本的解析、归因边界与数据脱敏契约。"""

import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import perf_macos_window as P  # noqa: E402

ENV = {
    "hw_model": "Mac15,3",
    "power_source": "ac",
    "low_power_mode": False,
    "displays": ["3024 x 1964 @ 120.00Hz"],
    "tavotto_version": "1.0.0",
}


def test_process_family_excludes_unrelated_webkit_and_counts_descendants():
    rows = P.parse_ps(
        "100 1 0:05.20 102400 /Applications/Tavotto.app/Contents/MacOS/Tavotto\n"
        "101 100 0:02.50 20480 /Library/Frameworks/Python Framework/Python\n"
        "102 101 0:01.00 10240 /somewhere/workerd\n"
        "200 1 0:25.00 40960 /System/Library/Frameworks/WebKit.WebContent\n"
        "300 1 0:09.00 40000 /System/Library/PrivateFrameworks/SkyLight.framework/WindowServer\n"
    )
    m = P.classify(rows)
    assert m["root_count"] == 1
    assert m["family_processes"] == 3
    assert sum(m["_family_cpu_s"].values()) == 8.7
    assert m["family_rss_sum_mib"] == 130.0
    assert m["_windowserver_cpu_s"] == {300: 9.0}


def test_no_app_is_not_silently_assumed_running():
    data = P.classify(P.parse_ps("200 1 0:25.00 40960 WebKit.WebContent\n"))
    assert data["root_count"] == 0
    assert data["family_processes"] == 0
    assert data["windowserver_present"] is False


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
        "sample_interval_seconds": 0.25,
        "environment": dict(ENV),
        "samples": [
            {
                "root_count": 1,
                "family_cpu_pct": 2.0,
                "family_rss_sum_mib": 120.0,
                "windowserver_global_cpu_pct": 8.0,
            }
            for _ in range(8)
        ],
    }
    drag = {
        **idle,
        "stage": "drag",
        "samples": [
            {
                "root_count": 1,
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
        "sample_interval_seconds": 0.25,
        "environment": dict(ENV),
        "samples": [{"family_cpu_pct": 1.0, "family_rss_sum_mib": 100.0} for _ in range(8)],
    }
    active = {**report, "stage": "drag", "macos_version": "25.4"}
    assert P.compare_reports(report, active)["status"] == "incomparable"


def test_parse_cpu_time_formats():
    assert P.parse_cpu_time("0:00.01") == 0.01
    assert P.parse_cpu_time("1:02.50") == 62.5
    assert P.parse_cpu_time("1:02:03.00") == 3723.0
    assert P.parse_cpu_time("2-01:00:00.00") == 2 * 86400 + 3600


def _fake_record_env(monkeypatch, ps_outputs):
    """按顺序回放 ps 输出，假时钟每次 sleep 前进 interval（墙钟 0.5 s/次）。"""
    clock = {"t": 0.0}
    outputs = iter(ps_outputs)

    class Done:
        def __init__(self, stdout):
            self.stdout = stdout

    monkeypatch.setattr(P.sys, "platform", "darwin")
    monkeypatch.setattr(P, "collect_environment", lambda root_command: dict(ENV))
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: Done(next(outputs)))
    monkeypatch.setattr(P.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(P.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))


APP = "/Applications/Tavotto.app/Contents/MacOS/Tavotto"
WS = "/System/Library/PrivateFrameworks/SkyLight.framework/WindowServer"


def test_record_cpu_is_cputime_delta_over_the_poll_interval(monkeypatch):
    # 累计 CPU 0.00 -> 0.10 -> 0.10 -> 0.10 s，墙钟间隔各 0.5 s：区间占用 20% 然后 0%。
    _fake_record_env(
        monkeypatch,
        [
            f"100 1 0:00.00 1024 {APP}\n300 1 1:00.00 500 {WS}\n",
            f"100 1 0:00.10 1024 {APP}\n300 1 1:00.25 500 {WS}\n",
            f"100 1 0:00.10 1024 {APP}\n300 1 1:00.25 500 {WS}\n",
            f"100 1 0:00.10 1024 {APP}\n300 1 1:00.25 500 {WS}\n",
            f"100 1 0:00.10 1024 {APP}\n300 1 1:00.25 500 {WS}\n",
        ],
    )
    report = P.record("drag", 2, 0.5)
    cpu = [s["family_cpu_pct"] for s in report["samples"]]
    assert cpu == [None, 20.0, 0.0, 0.0]
    ws = [s["windowserver_global_cpu_pct"] for s in report["samples"]]
    assert ws == [None, 50.0, 0.0, 0.0]
    assert "pid" not in json.dumps(report).lower().replace("pids", "")
    assert "_family_cpu_s" not in json.dumps(report)


def test_record_rejects_capture_that_loses_the_app(monkeypatch):
    _fake_record_env(
        monkeypatch,
        [
            f"100 1 0:00.00 1024 {APP}\n",
            f"100 1 0:00.10 1024 {APP}\n",
            "300 1 1:00.00 500 " + WS + "\n",
            "300 1 1:00.00 500 " + WS + "\n",
        ],
    )
    try:
        P.record("drag", 2, 0.5)
    except ValueError as exc:
        assert "no report was saved" in str(exc)
    else:
        raise AssertionError("app disappearing mid-capture must not produce a report")


def test_record_rejects_capture_when_app_restarts_with_new_pid(monkeypatch):
    _fake_record_env(
        monkeypatch,
        [f"100 1 0:09.00 1024 {APP}\n", f"200 1 0:00.05 1024 {APP}\n"] * 2,
    )
    try:
        P.record("drag", 2, 0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("a restarted app must not be stitched into one capture")


def test_compare_marks_reports_with_missing_app_samples_incomparable():
    def rep(stage, root_counts):
        return {
            "schema": P.SCHEMA,
            "stage": stage,
            "macos_version": "26.1",
            "arch": "arm64",
            "sample_interval_seconds": 0.25,
            "environment": dict(ENV),
            "samples": [
                {"root_count": n, "family_cpu_pct": 1.0, "family_rss_sum_mib": 10.0}
                for n in root_counts
            ],
        }

    ok = rep("idle", [1] * 6)
    lost = rep("drag", [1, 1, 0, 0, 0, 0])
    assert P.compare_reports(ok, lost)["status"] == "incomparable"
    assert P.compare_reports(ok, rep("drag", [1] * 6))["status"] == "diagnostic_only"


def test_record_marks_interval_unknown_when_a_child_exits(monkeypatch):
    # 子进程 101 在第 3 个样本消失：它退出前用掉的 CPU ps 看不到，该区间记 None。
    # 第 4 个样本有新子进程 102（出生在区间内，取其全部累计时间）。
    _fake_record_env(
        monkeypatch,
        [
            f"100 1 0:00.00 1024 {APP}\n101 100 0:00.00 512 /x/child\n",
            f"100 1 0:00.10 1024 {APP}\n101 100 0:00.10 512 /x/child\n",
            f"100 1 0:00.20 1024 {APP}\n",
            f"100 1 0:00.30 1024 {APP}\n102 100 0:00.05 512 /x/child\n",
        ],
    )
    report = P.record("drag", 2, 0.5)
    samples = report["samples"]
    assert [s["family_cpu_pct"] for s in samples] == [None, 40.0, None, 30.0]
    assert [s["family_exited"] for s in samples] == [0, 0, 1, 0]
    assert "cpu_unknown_when_a_descendant_exits_short_lived_children_invisible" in report["limits"]


def test_record_marks_windowserver_unknown_when_its_pid_changes(monkeypatch):
    ws2 = WS
    _fake_record_env(
        monkeypatch,
        [
            f"100 1 0:00.00 1024 {APP}\n300 1 1:00.00 500 {WS}\n",
            f"100 1 0:00.10 1024 {APP}\n301 1 0:05.00 500 {ws2}\n",
            f"100 1 0:00.20 1024 {APP}\n301 1 0:05.25 500 {ws2}\n",
            f"100 1 0:00.30 1024 {APP}\n301 1 0:05.25 500 {ws2}\n",
        ],
    )
    ws = [s["windowserver_global_cpu_pct"] for s in P.record("drag", 2, 0.5)["samples"]]
    assert ws == [None, None, 50.0, 0.0]


def test_compare_needs_five_valid_cpu_samples_per_side():
    def rep(stage, cpus):
        return {
            "schema": P.SCHEMA,
            "stage": stage,
            "macos_version": "26.1",
            "arch": "arm64",
            "sample_interval_seconds": 0.25,
            "environment": dict(ENV),
            "samples": [
                {"root_count": 1, "family_cpu_pct": c, "family_rss_sum_mib": 10.0} for c in cpus
            ],
        }

    full = rep("idle", [1.0] * 6)
    sparse = rep("drag", [None, 1.0, None, 1.0, None, 1.0])
    assert P.compare_reports(full, sparse)["status"] == "incomparable"
    assert P.compare_reports(full, rep("drag", [None] + [1.0] * 5))["status"] == "diagnostic_only"


def _cmp_report(stage, **over):
    base = {
        "schema": P.SCHEMA,
        "stage": stage,
        "macos_version": "26.1",
        "arch": "arm64",
        "sample_interval_seconds": 0.25,
        "environment": dict(ENV),
        "samples": [
            {"root_count": 1, "family_cpu_pct": 1.0, "family_rss_sum_mib": 10.0} for _ in range(6)
        ],
    }
    return {**base, **over}


def test_compare_rejects_different_sampling_intervals():
    idle = _cmp_report("idle", sample_interval_seconds=0.1)
    drag = _cmp_report("drag", sample_interval_seconds=2.0)
    result = P.compare_reports(idle, drag)
    assert result["status"] == "incomparable"
    assert "same_sample_interval" in result["failed_conditions"]
    assert P.compare_reports(_cmp_report("idle"), _cmp_report("drag"))["status"] == (
        "diagnostic_only"
    )
    missing = _cmp_report("drag")
    del missing["sample_interval_seconds"]
    assert P.compare_reports(_cmp_report("idle"), missing)["status"] == "incomparable"


def test_compare_rejects_samples_with_more_than_one_top_level_family():
    two = _cmp_report("drag")
    two["samples"][2]["root_count"] = 2
    result = P.compare_reports(_cmp_report("idle"), two)
    assert result["status"] == "incomparable"
    assert "single_tavotto_family_throughout" in result["failed_conditions"]


def test_bundled_sidecar_named_tavotto_is_not_a_second_root():
    rows = P.parse_ps(
        f"100 1 0:01.00 1024 {APP}\n101 100 0:01.00 512 /x/Contents/Resources/Tavotto\n"
    )
    m = P.classify(rows)
    assert m["root_count"] == 1
    assert m["family_processes"] == 2


def test_orphan_second_tavotto_tree_counts_as_a_second_root():
    rows = P.parse_ps(f"100 1 0:01.00 1024 {APP}\n500 1 0:01.00 512 {APP}\n")
    assert P.classify(rows)["root_count"] == 2


def test_record_rejects_two_tavotto_trees(monkeypatch):
    two = f"100 1 0:00.00 1024 {APP}\n500 1 0:00.00 1024 {APP}\n"
    _fake_record_env(monkeypatch, [two] * 4)
    try:
        P.record("drag", 2, 0.5)
    except ValueError as exc:
        assert "exactly one" in str(exc) and "no report was saved" in str(exc)
    else:
        raise AssertionError("ambiguous process families must not produce a report")


def test_record_stores_sample_interval(monkeypatch):
    one = f"100 1 0:00.00 1024 {APP}\n"
    _fake_record_env(monkeypatch, [one] * 4)
    assert P.record("idle", 2, 0.5)["sample_interval_seconds"] == 0.5


def test_compare_without_json_still_runs_the_comparison(tmp_path, capsys):
    idle = _cmp_report("idle")
    drag = _cmp_report("drag", macos_version="25.4")
    (tmp_path / "i.json").write_text(json.dumps(idle), encoding="utf-8")
    (tmp_path / "d.json").write_text(json.dumps(drag), encoding="utf-8")
    code = P.main(["compare", str(tmp_path / "i.json"), str(tmp_path / "d.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert "incomparable" in out and "same_macos_version" in out
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_cmp_report("drag")), encoding="utf-8")
    assert P.main(["compare", str(tmp_path / "i.json"), str(ok)]) == 0
    assert "diagnostic_only" in capsys.readouterr().out


def test_compare_needs_exactly_two_reports(tmp_path, capsys):
    f = tmp_path / "i.json"
    f.write_text(json.dumps(_cmp_report("idle")), encoding="utf-8")
    assert P.main(["compare", str(f)]) == 2


def test_compare_rejects_different_capture_environments():
    for key, other in (
        ("hw_model", "Mac14,2"),
        ("power_source", "battery"),
        ("low_power_mode", True),
        ("displays", ["2560 x 1440 @ 60.00Hz"]),
        ("tavotto_version", "1.0.1"),
    ):
        drag = _cmp_report("drag")
        drag["environment"][key] = other
        result = P.compare_reports(_cmp_report("idle"), drag)
        assert result["status"] == "incomparable", key
        assert "same_capture_environment" in result["failed_conditions"], key


def test_compare_fails_closed_on_missing_or_unknown_environment():
    missing = _cmp_report("drag")
    del missing["environment"]
    unknown = _cmp_report("drag")
    unknown["environment"]["power_source"] = None
    idle_unknown = _cmp_report("idle")
    idle_unknown["environment"]["power_source"] = None
    for idle, drag in (
        (_cmp_report("idle"), missing),
        (_cmp_report("idle"), unknown),
        (idle_unknown, unknown),  # 两边都「未知」不能因为相等就放行
    ):
        assert P.compare_reports(idle, drag)["status"] == "incomparable"


def test_compare_fails_closed_on_missing_or_malformed_root_evidence():
    def with_samples(samples):
        return _cmp_report("drag", samples=samples)

    good = [{"root_count": 1, "family_cpu_pct": 1.0, "family_rss_sum_mib": 10.0}] * 6
    no_field = [{"family_cpu_pct": 1.0, "family_rss_sum_mib": 10.0}] * 6
    wrong_type = [{**good[0], "root_count": "1"}] * 6
    bool_type = [{**good[0], "root_count": True}] * 6
    non_dict = [*good[:5], "garbage"]
    for samples in (no_field, wrong_type, bool_type, non_dict):
        result = P.compare_reports(_cmp_report("idle"), with_samples(samples))
        assert result["status"] == "incomparable"
        assert "single_tavotto_family_throughout" in result["failed_conditions"]
    assert P.compare_reports(_cmp_report("idle"), with_samples(good))["status"] == (
        "diagnostic_only"
    )


def test_record_stores_environment_without_identifying_facts(monkeypatch):
    one = f"100 1 0:00.00 1024 {APP}\n"
    _fake_record_env(monkeypatch, [one] * 4)
    report = P.record("idle", 2, 0.5)
    assert report["environment"] == ENV
    assert "/Applications" not in json.dumps(report)


def test_collect_environment_reads_only_non_identifying_facts(monkeypatch, tmp_path):
    app = tmp_path / "Tavotto.app" / "Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "Info.plist").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
        b"<key>CFBundleShortVersionString</key><string>1.2.3</string></dict></plist>"
    )
    answers = {
        "sysctl": "Mac15,3\n",
        "pmset": "Now drawing from 'Battery Power'\n lowpowermode 1\n",
        "system_profiler": json.dumps(
            {
                "SPDisplaysDataType": [
                    {
                        "_name": "Apple M3",
                        "spdisplays_ndrvs": [
                            {
                                "_name": "Jiaqi's Studio Display",
                                "_spdisplays_serial": "SECRET",
                                "_spdisplays_resolution": "2560 x 1440 @ 60.00Hz",
                            }
                        ],
                    }
                ]
            }
        ),
    }

    class Done:
        def __init__(self, stdout):
            self.stdout = stdout

    monkeypatch.setattr(P.subprocess, "run", lambda cmd, **k: Done(answers[cmd[0]]))
    env = P.collect_environment(str(app / "MacOS" / "Tavotto"))
    assert env == {
        "hw_model": "Mac15,3",
        "power_source": "battery",
        "low_power_mode": True,
        "displays": ["2560 x 1440 @ 60.00Hz"],
        "tavotto_version": "1.2.3",
    }
    dumped = json.dumps(env)
    assert "SECRET" not in dumped and "Jiaqi" not in dumped and str(tmp_path) not in dumped


def test_collect_environment_marks_failures_unknown(monkeypatch):
    def boom(*a, **k):
        raise OSError("no such tool")

    monkeypatch.setattr(P.subprocess, "run", boom)
    env = P.collect_environment("/nowhere/Tavotto")
    assert env == {
        "hw_model": None,
        "power_source": None,
        "low_power_mode": None,
        "displays": None,
        "tavotto_version": None,
    }


def test_info_plist_is_found_from_the_executable_path_without_relying_on_separators():
    posix = PurePosixPath("/Applications/Tavotto.app/Contents/MacOS/Tavotto")
    assert P.info_plist_for_executable(posix) == PurePosixPath(
        "/Applications/Tavotto.app/Contents/Info.plist"
    )
    windows = PureWindowsPath("C:\\x\\Tavotto.app\\Contents\\MacOS\\Tavotto")
    assert P.info_plist_for_executable(windows) == PureWindowsPath(
        "C:\\x\\Tavotto.app\\Contents\\Info.plist"
    )
    assert P.info_plist_for_executable(PurePosixPath("/usr/bin/Tavotto")) is None
    assert P.info_plist_for_executable(PurePosixPath("/a/Contents/Other/Tavotto")) is None
