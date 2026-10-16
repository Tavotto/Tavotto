"""Agent 统一验证入口（scripts/ci/agent_verify.py，ADR 0131）的看护。

这个入口唯一的价值是「Agent 拿到的结论是真的」。所以每条用例钉的都是它坏掉
之后会怎样说谎：skip / 没跑 / 环境缺失被聚合成 passed、junit 丢了按退出码判绿、
超时的子进程活下来、调用方 shell 里的变量决定了验证结果、白名单被绕过、
报告里带出本机路径。真实验证器用合成场景替身，全部纯标准库、秒级。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import agent_verify as AV  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 聚合与退出码
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad", ["skipped", "not_run", "environment_unavailable", "not_reproduced", "failed", "error"]
)
def test_any_non_passed_result_never_aggregates_to_passed(bad):
    assert AV.aggregate(["passed", bad, "passed"]) == bad
    assert AV.exit_code_for(AV.aggregate(["passed", bad])) != AV.EXIT_PASSED


def test_empty_run_is_not_passed():
    assert AV.aggregate([]) == "not_run"


def test_failure_outranks_environment_and_incomplete():
    assert AV.aggregate(["skipped", "environment_unavailable", "failed"]) == "failed"
    assert AV.aggregate(["skipped", "environment_unavailable"]) == "environment_unavailable"
    assert AV.exit_code_for("error") == AV.exit_code_for("failed") == AV.EXIT_FAILED


def test_unknown_result_is_rejected_not_ignored():
    with pytest.raises(ValueError):
        AV.aggregate(["passed", "pass"])


def test_every_result_has_an_exit_code():
    assert set(AV._EXIT_BY_RESULT) == AV.RESULTS
    assert [k for k, v in AV._EXIT_BY_RESULT.items() if v == AV.EXIT_PASSED] == ["passed"]


# --------------------------------------------------------------------------
# pytest / perf_manifest 判定
# --------------------------------------------------------------------------
def _junit(tmp_path: Path, tests=3, failures=0, errors=0, skipped=0) -> Path:
    p = tmp_path / "junit.xml"
    p.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="{tests}" '
        f'failures="{failures}" errors="{errors}" skipped="{skipped}"/></testsuites>',
        encoding="utf-8",
    )
    return p


def test_pytest_all_passed(tmp_path):
    result, _, counts = AV.judge_pytest({"exit_code": 0}, _junit(tmp_path))
    assert result == "passed" and counts["passed"] == 3


def test_pytest_green_with_skips_is_skipped_not_passed(tmp_path):
    result, reason, _ = AV.judge_pytest({"exit_code": 0}, _junit(tmp_path, skipped=1))
    assert result == "skipped" and "1" in reason


def test_pytest_failures_are_failed(tmp_path):
    assert AV.judge_pytest({"exit_code": 1}, _junit(tmp_path, failures=1))[0] == "failed"
    assert AV.judge_pytest({"exit_code": 1}, _junit(tmp_path, errors=1))[0] == "failed"


def test_pytest_missing_junit_is_error_even_with_rc0(tmp_path):
    assert AV.judge_pytest({"exit_code": 0}, tmp_path / "absent.xml")[0] == "error"


def test_pytest_nothing_collected_is_not_run(tmp_path):
    assert AV.judge_pytest({"exit_code": 5}, _junit(tmp_path, tests=0))[0] == "not_run"
    assert AV.judge_pytest({"exit_code": 0}, _junit(tmp_path, tests=0))[0] == "not_run"


def test_pytest_nonzero_rc_without_failures_is_error(tmp_path):
    assert AV.judge_pytest({"exit_code": 2}, _junit(tmp_path))[0] == "error"


def _summary(tmp_path: Path, status: str) -> Path:
    p = tmp_path / "manifest-summary.json"
    p.write_text(
        json.dumps(
            {
                "schema": "tavotto-perf-manifest/1",
                "status": status,
                "comparison_verdict": "not_checked_without_pinned_same_machine_baseline",
                "scenarios": [{"scenario": 1, "status": status, "valid_sample_count": 9}],
            }
        ),
        encoding="utf-8",
    )
    return p


def test_perf_manifest_recorded_passes_but_does_not_claim_performance(tmp_path):
    result, reason, facts = AV.judge_perf_manifest({"exit_code": 0}, _summary(tmp_path, "recorded"))
    assert result == "passed"
    assert facts["comparison_verdict"].startswith("not_checked")
    assert "未与基线比较" in reason


def test_perf_manifest_incomplete_or_missing_is_failed(tmp_path):
    assert AV.judge_perf_manifest({"exit_code": 2}, _summary(tmp_path, "incomplete"))[0] == "failed"
    assert AV.judge_perf_manifest({"exit_code": 0}, tmp_path / "absent.json")[0] == "failed"


# --------------------------------------------------------------------------
# 登记表本身
# --------------------------------------------------------------------------
def test_registry_ids_are_unique_and_kinds_closed():
    assert len(AV.SCENARIOS) >= 2
    for sid, s in AV.SCENARIOS.items():
        assert sid == s.id
        assert s.kind in {"regression", "measurement", "selftest"}
        assert s.timeout_s > 0 and s.claim and s.owner_doc


def test_registry_points_at_files_that_exist():
    """登记的脚本 / 用例 / 文档都要真实存在——指向空气的场景只会永远 not_run。"""
    for s in AV.SCENARIOS.values():
        assert (ROOT / s.owner_doc).is_file(), s.owner_doc
        for c in s.preconditions:
            if c.startswith("file:"):
                assert (ROOT / c[5:]).is_file(), (s.id, c)
        for step in s.steps:
            for a in step.argv:
                if a.startswith(("scripts/", "tests/")):
                    assert (ROOT / a).is_file(), (s.id, a)


def test_registry_argv_is_argument_vector_not_shell():
    for s in AV.SCENARIOS.values():
        for step in s.steps:
            assert step.argv[0] == "{python}"
            assert step.parser in {"exit_code", "pytest", "perf_manifest"}
            for a in step.argv:
                assert not any(ch in a for ch in ";|&`$<>"), (s.id, a)


def test_registry_preconditions_are_known_kinds(tmp_path):
    for s in AV.SCENARIOS.values():
        for c in s.preconditions:
            assert c.split(":", 1)[0] in {"fonts", "file", "python_module", "workerd_binary"}, c


# --------------------------------------------------------------------------
# 执行：用替身场景走真实的 run_scenario
# --------------------------------------------------------------------------
def _fake(sid: str, code: str, *, timeout_s: int = 60, preconditions=()) -> AV.Scenario:
    return AV.Scenario(
        id=sid,
        summary="fake",
        kind="selftest",
        steps=(AV.Step("probe", ("{python}", "-c", code)),),
        timeout_s=timeout_s,
        preconditions=preconditions,
        claim="fake",
        unverified=("fake unverified",),
        owner_doc="docs/adr/0131-agent-verification-entry.md",
    )


def test_timeout_kills_and_reports_error(tmp_path):
    rec = AV.run_scenario(
        _fake("t.sleep", "import time; time.sleep(30)", timeout_s=1), tmp_path, sys.executable
    )
    assert rec["result"] == "error" and rec["timed_out"] is True
    assert rec["duration_s"] < 20


def test_nonzero_exit_is_failed_and_logs_are_evidence(tmp_path):
    rec = AV.run_scenario(
        _fake("t.fail", "import sys; print('hello-out'); sys.exit(3)"), tmp_path, sys.executable
    )
    assert rec["result"] == "failed" and rec["steps"][0]["exit_code"] == 3
    paths = {e["path"] for e in rec["evidence"]}
    assert "t.fail/evidence/01-probe.stdout.log" in paths
    assert "hello-out" in (tmp_path / "t.fail/evidence/01-probe.stdout.log").read_text(
        encoding="utf-8"
    )
    assert all(len(e["sha256"]) == 64 for e in rec["evidence"])


def test_missing_environment_is_environment_unavailable_and_nothing_runs(tmp_path):
    marker = tmp_path / "ran"
    rec = AV.run_scenario(
        _fake(
            "t.env",
            f"open({str(marker)!r}, 'w', encoding='utf-8').write('x')",
            preconditions=("python_module:definitely_not_a_module_xyz",),
        ),
        tmp_path,
        sys.executable,
    )
    assert rec["result"] == "environment_unavailable"
    assert not marker.exists()


def test_missing_test_file_is_not_run(tmp_path):
    rec = AV.run_scenario(
        _fake("t.nofile", "pass", preconditions=("file:tests/test_no_such_file_xyz.py",)),
        tmp_path,
        sys.executable,
    )
    assert rec["result"] == "not_run"


def test_caller_environment_does_not_leak_and_isolation_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VERIFY_LEAK_PROBE", "leaked")
    monkeypatch.setenv("TAVOTTO_DATA_DIR", "/should/not/be/used")
    code = (
        "import os, json; print(json.dumps({k: os.environ.get(k) for k in "
        "['AGENT_VERIFY_LEAK_PROBE','TAVOTTO_DATA_DIR','TAVOTTO_NO_TELEMETRY','PYTHONPATH',"
        "'TAVOTTO_CI_STATE_ROOT']}))"
    )
    rec = AV.run_scenario(_fake("t.env2", code), tmp_path, sys.executable)
    assert rec["result"] == "passed"
    seen = json.loads(
        (tmp_path / "t.env2/evidence/01-probe.stdout.log").read_text(encoding="utf-8")
    )
    assert seen["AGENT_VERIFY_LEAK_PROBE"] is None
    assert seen["TAVOTTO_NO_TELEMETRY"] == "1"
    assert Path(seen["TAVOTTO_DATA_DIR"]).is_relative_to(tmp_path / "t.env2")
    assert Path(seen["TAVOTTO_CI_STATE_ROOT"]).is_relative_to(tmp_path / "t.env2")
    assert Path(seen["PYTHONPATH"]).is_absolute()


# --------------------------------------------------------------------------
# CLI 与报告
# --------------------------------------------------------------------------
def test_unknown_scenario_is_usage_error(capsys):
    assert AV.main(["run", "no.such.scenario"]) == AV.EXIT_USAGE
    assert json.loads(capsys.readouterr().out)["code"] == "usage"


def test_non_empty_out_dir_is_refused(tmp_path):
    (tmp_path / "stale.json").write_text("{}", encoding="utf-8")
    sid = next(iter(AV.SCENARIOS))
    assert AV.main(["run", sid, "--out", str(tmp_path)]) == AV.EXIT_USAGE


def test_report_shape_and_no_absolute_paths(tmp_path, monkeypatch):
    fake = _fake("t.ok", "print('ok')")
    monkeypatch.setitem(AV.SCENARIOS, fake.id, fake)
    out = tmp_path / "out"
    rc = AV.main(["run", "t.ok", "--out", str(out), "--python", sys.executable])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert rc == AV.EXIT_PASSED == report["exit_code"]
    assert report["schema"] == AV.SCHEMA and report["kind"] == AV.REPORT_KIND
    assert report["overall"] == "passed"
    for key in ("sha", "branch", "dirty"):
        assert key in report["commit"]
    assert report["environment"]["platform"] == sys.platform
    assert report["unverified"] == ["fake unverified"]
    text = (out / "report.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and sys.executable not in text
    assert report["scenarios"][0]["steps"][0]["argv"][0] == "{python}"


def test_mixed_run_exit_code_reflects_worst(tmp_path, monkeypatch):
    ok = _fake("t.ok2", "pass")
    env = _fake("t.env3", "pass", preconditions=("python_module:definitely_not_a_module_xyz",))
    for s in (ok, env):
        monkeypatch.setitem(AV.SCENARIOS, s.id, s)
    out = tmp_path / "out"
    rc = AV.main(["run", "t.ok2", "t.env3", "--out", str(out), "--python", sys.executable])
    assert rc == AV.EXIT_ENV
    assert (
        json.loads((out / "report.json").read_text(encoding="utf-8"))["overall"]
        == "environment_unavailable"
    )


# --------------------------------------------------------------------------
# 前置条件：批准字体要整套核对；失败原因进报告前去路径（#1085 Codex P2）
# --------------------------------------------------------------------------
def test_partial_font_set_is_not_ready(tmp_path, monkeypatch):
    (tmp_path / "liberation").mkdir()
    (tmp_path / "liberation" / "LiberationSans-Regular.ttf").write_bytes(b"not really a font")
    monkeypatch.setenv("TAVOTTO_FONTS_DIR", str(tmp_path))
    rec = AV.check_precondition("fonts", sys.executable)
    assert rec["ok"] is False
    assert rec["detail"].startswith("approved_fonts_incomplete:")
    assert str(tmp_path) not in rec["detail"]


def test_font_check_uses_the_fetch_fonts_authority(tmp_path, monkeypatch):
    """判据只有一个出处：fetch_fonts.check() 说齐了才算齐。"""
    seen = []
    monkeypatch.setattr(AV, "_approved_fonts_problems", lambda d: seen.append(d) or [])
    monkeypatch.setenv("TAVOTTO_FONTS_DIR", str(tmp_path))
    assert AV.check_precondition("fonts", sys.executable)["ok"] is True
    assert seen == [tmp_path]


@pytest.mark.parametrize(
    "stderr, expected",
    [
        (
            "Traceback...\nModuleNotFoundError: No module named 'matplotlib'\n",
            "import_failed:ModuleNotFoundError",
        ),
        (
            "ImportError: dlopen(/Users/someone/venv/lib/_c.so, 2): no suitable image\n",
            "import_failed:ImportError",
        ),
        ("/opt/weird/site.py: line 3: syntax error\n", "import_failed"),
        ("", "import_failed"),
    ],
)
def test_import_failure_detail_is_a_path_free_category(stderr, expected):
    detail = AV._import_failure_category(stderr)
    assert detail == expected
    assert "/" not in detail and "\\" not in detail


def test_precondition_raw_output_goes_to_local_evidence_not_report(tmp_path, monkeypatch):
    fake = _fake("t.rawpre", "pass", preconditions=("python_module:definitely_not_a_module_xyz",))
    monkeypatch.setitem(AV.SCENARIOS, fake.id, fake)
    out = tmp_path / "out"
    assert (
        AV.main(["run", "t.rawpre", "--out", str(out), "--python", sys.executable]) == AV.EXIT_ENV
    )
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    pre = report["scenarios"][0]["preconditions"][0]
    assert pre["detail"] == "import_failed:ModuleNotFoundError" and "raw" not in pre
    log = out / "t.rawpre" / "evidence" / "preconditions.log"
    assert "definitely_not_a_module_xyz" in log.read_text(encoding="utf-8")
    assert "t.rawpre/evidence/preconditions.log" in {
        e["path"] for e in report["scenarios"][0]["evidence"]
    }


# --------------------------------------------------------------------------
# commit / 环境事实（#1085 Codex 第二轮 P2）
# --------------------------------------------------------------------------
def test_untracked_file_makes_the_tree_dirty(monkeypatch):
    monkeypatch.setattr(
        AV, "_git", lambda *a: "?? tests/test_new_thing.py" if a[0] == "status" else "x"
    )
    facts = AV.commit_facts()
    assert facts["dirty"] is True and facts["untracked_files"] == 1 and facts["dirty_files"] == 0


def test_clean_tree_is_not_dirty(monkeypatch):
    monkeypatch.setattr(AV, "_git", lambda *a: "" if a[0] == "status" else "x")
    facts = AV.commit_facts()
    assert facts["dirty"] is False and facts["untracked_files"] == 0


def test_venv_elsewhere_is_not_this_repos_venv(tmp_path):
    other = tmp_path / ".venv"
    other.mkdir()
    assert AV._same_dir(str(other), AV.ROOT / ".venv") is False
    assert AV._same_dir("", AV.ROOT / ".venv") is False
    assert AV._same_dir(str(AV.ROOT / ".venv"), AV.ROOT / ".venv") is True


def test_git_state_is_snapshotted_before_scenarios_write_evidence(tmp_path, monkeypatch):
    """--out 在仓库里时，场景写出的证据不能把树判成 dirty：commit 事实在跑场景之前取。"""
    calls = []
    fake = _fake("t.snap", "pass")
    monkeypatch.setitem(AV.SCENARIOS, fake.id, fake)
    monkeypatch.setattr(AV, "commit_facts", lambda: calls.append("commit") or {"dirty": False})
    real_run = AV.run_scenario
    monkeypatch.setattr(AV, "run_scenario", lambda *a: calls.append("run") or real_run(*a))
    AV.main(["run", "t.snap", "--out", str(tmp_path / "out"), "--python", sys.executable])
    assert calls[:2] == ["commit", "run"] and calls.count("commit") == 1


def test_spawn_error_is_recorded_without_the_executable_path(tmp_path):
    """解释器在前置探测之后消失：report 只记类别，绝对路径只留在本地 stderr 证据里。"""
    missing = tmp_path / "私有目录" / "venv" / "bin" / "python"
    rec = AV.run_step([str(missing), "-c", "0"], {}, tmp_path / "01-step", 5)
    assert rec["exit_code"] is None
    assert rec["spawn_error"].startswith("FileNotFoundError")
    assert str(tmp_path) not in rec["spawn_error"] and "私有目录" not in rec["spawn_error"]
    assert "私有目录" in (tmp_path / "01-step.stderr.log").read_text(encoding="utf-8")


def test_spawn_error_evidence_names_the_executable_when_the_os_text_does_not(tmp_path, monkeypatch):
    """Windows 的 WinError 2 文本不带路径：本地证据仍要写出是哪个解释器起不来。"""

    def boom(*a, **k):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(AV.subprocess, "Popen", boom)
    missing = tmp_path / "私有目录" / "python.exe"
    rec = AV.run_step([str(missing), "-c", "0"], {}, tmp_path / "01-step", 5)
    assert rec["spawn_error"] == "FileNotFoundError(ENOENT)"
    assert "私有目录" in (tmp_path / "01-step.stderr.log").read_text(encoding="utf-8")
