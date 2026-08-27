"""稳定 Gate 判定器（scripts/ci/aggregate_gate.py）的看护。

Gate 是 ruleset 收敛后唯一的合并资格出口，它判错的两个方向代价都不便宜：
把坏组合放进 main（假绿）、把好组合无端拦下（假红）。每条用例都钉
「坏掉之后会怎样」——尤其是那几条最容易被巧合掩盖的：cancelled 被当成
成功、上游 job 被删掉后 Gate 照绿、merge_group 上混进 deferred。

全部平台无关、纯标准库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import aggregate_gate as AG  # noqa: E402


def _needs(**results: str) -> dict[str, str]:
    return dict(results)


FAST_REQ = ["invariants", "backend", "frontend", "workerd", "compat-smoke"]
HEAVY_REQ = ["package", "windows-exe-smoke", "macos-app-smoke"]


def _fast(results, event="pull_request"):
    return AG.decide("fast", event, FAST_REQ, results)


def _integration(results, event="pull_request", *, heavy=False, deferred=False, full_ci=False):
    return AG.decide(
        "integration",
        event,
        HEAVY_REQ,
        results,
        require_heavy=heavy,
        allow_deferred=deferred,
        full_ci=full_ci,
    )


def _all(status: str, req=FAST_REQ) -> dict[str, str]:
    return {j: status for j in req}


# ============================================================ fast gate
class TestFastGate:
    def test_all_success_passes(self):
        assert _fast(_all("success"))["status"] == "success"

    def test_any_failure_fails(self):
        r = dict(_all("success"), backend="failure")
        v = _fast(r)
        assert v["status"] == "failure"
        assert any("backend" in p for p in v["problems"])

    def test_cancelled_is_not_success(self):
        """cancelled 最容易被「反正没红」放过——它意味着结论根本没产出。"""
        v = _fast(dict(_all("success"), workerd="cancelled"))
        assert v["status"] == "failure"

    def test_skipped_is_not_success_in_fast_mode(self):
        v = _fast(dict(_all("success"), invariants="skipped"))
        assert v["status"] == "failure"

    def test_missing_required_job_fails(self):
        """上游 job 被删掉 / 改名后，Gate 必须红——绝不「少一个也算齐」。"""
        r = _all("success")
        del r["compat-smoke"]
        v = _fast(r)
        assert v["status"] == "failure"
        assert any("compat-smoke" in p and "missing" in p for p in v["problems"])

    def test_unexpected_job_in_needs_fails(self):
        """有人往 needs 加了 job 却没同步 --required：它的失败 Gate 看不见，
        所以多出来本身就是失败。"""
        v = _fast(dict(_all("success"), extra="failure"))
        assert v["status"] == "failure"
        assert any("extra" in p for p in v["problems"])
        # 就算多出来的那个是 success 也一样——闭集是双向的
        v2 = _fast(dict(_all("success"), extra="success"))
        assert v2["status"] == "failure"

    def test_unknown_result_string_fails(self):
        """GitHub 改了结论枚举时要红着提醒人，不是猜。"""
        v = _fast(dict(_all("success"), backend="neutral"))
        assert v["status"] == "failure"

    def test_matrix_job_aggregate_failure(self):
        """矩阵 job 在 needs 里只有一个条目，任一腿失败整体就是 failure。"""
        v = AG.decide("codeql", "pull_request", ["analyze"], {"analyze": "failure"})
        assert v["status"] == "failure"
        assert (
            AG.decide("codeql", "merge_group", ["analyze"], {"analyze": "success"})["status"]
            == "success"
        )


# ============================================================ integration gate
class TestIntegrationGate:
    def test_mode_requires_an_explicit_flag(self):
        """不显式选 heavy/deferred 就是配置错误——把判定交给巧合不行。"""
        with pytest.raises(AG.ConfigError):
            _integration(_all("success", HEAVY_REQ))

    def test_both_flags_is_a_config_error(self):
        with pytest.raises(AG.ConfigError):
            _integration(_all("success", HEAVY_REQ), heavy=True, deferred=True)

    def test_plain_pr_all_skipped_is_deferred(self):
        v = _integration(_all("skipped", HEAVY_REQ), deferred=True)
        assert v["status"] == "deferred"
        assert v["reason"] == "merge_group_required"

    def test_plain_pr_with_real_results_is_enforced(self):
        """重活真的跑了（PR 1 阶段的 ready PR）就按真实结果判，不算 deferred。"""
        v = _integration(_all("success", HEAVY_REQ), deferred=True)
        assert v["status"] == "success"
        v = _integration(
            dict(_all("success", HEAVY_REQ), **{"windows-exe-smoke": "failure"}), deferred=True
        )
        assert v["status"] == "failure"

    def test_partial_skip_is_failure_not_deferred(self):
        """半套资格不是资格：跑了两个跳了一个，一律失败。"""
        v = _integration(dict(_all("success", HEAVY_REQ), package="skipped"), deferred=True)
        assert v["status"] == "failure"

    def test_merge_group_may_not_defer(self):
        """merge_group 是完整资格的唯一执行点，deferred 在那里是配置错误。"""
        with pytest.raises(AG.ConfigError):
            _integration(_all("skipped", HEAVY_REQ), event="merge_group", deferred=True)

    def test_full_ci_pr_may_not_defer(self):
        with pytest.raises(AG.ConfigError):
            _integration(_all("skipped", HEAVY_REQ), deferred=True, full_ci=True)

    def test_require_heavy_rejects_skipped(self):
        v = _integration(_all("skipped", HEAVY_REQ), event="merge_group", heavy=True)
        assert v["status"] == "failure"

    def test_require_heavy_all_success(self):
        v = _integration(_all("success", HEAVY_REQ), event="merge_group", heavy=True)
        assert v["status"] == "success"

    def test_require_heavy_missing_job_fails(self):
        r = _all("success", HEAVY_REQ)
        del r["macos-app-smoke"]
        v = _integration(r, event="merge_group", heavy=True)
        assert v["status"] == "failure"


# ============================================================ 输入边界
class TestInputs:
    def test_invalid_json_is_a_config_error(self):
        with pytest.raises(AG.ConfigError):
            AG.parse_needs("not json{")

    def test_needs_must_be_an_object(self):
        with pytest.raises(AG.ConfigError):
            AG.parse_needs("[1, 2]")

    def test_entry_without_result_is_a_config_error(self):
        with pytest.raises(AG.ConfigError):
            AG.parse_needs(json.dumps({"backend": {"outputs": {}}}))

    def test_empty_required_is_a_config_error(self):
        with pytest.raises(AG.ConfigError):
            AG.decide("fast", "pull_request", [], {})

    def test_fast_mode_rejects_integration_flags(self):
        with pytest.raises(AG.ConfigError):
            AG.decide("fast", "pull_request", FAST_REQ, _all("success"), allow_deferred=True)


# ============================================================ CLI（退出码契约）
class TestCli:
    def _run(self, args, needs, capsys):
        rc = AG.main(
            args + ["--needs-json", json.dumps({j: {"result": r} for j, r in needs.items()})]
        )
        out = capsys.readouterr().out.strip().splitlines()[-1]
        return rc, json.loads(out)

    def test_success_exit_zero_with_machine_json(self, capsys):
        rc, v = self._run(
            ["--mode", "fast", "--event", "pull_request", "--required", ",".join(FAST_REQ)],
            _all("success"),
            capsys,
        )
        assert rc == 0 and v["status"] == "success"

    def test_failure_exit_one(self, capsys):
        rc, v = self._run(
            ["--mode", "fast", "--event", "pull_request", "--required", ",".join(FAST_REQ)],
            dict(_all("success"), backend="failure"),
            capsys,
        )
        assert rc == 1 and v["status"] == "failure"

    def test_deferred_exit_zero_with_machine_readable_reason(self, capsys):
        """普通 PR 的合法 deferred：结论是成功，但 JSON 里写得明明白白。"""
        rc, v = self._run(
            [
                "--mode",
                "integration",
                "--event",
                "pull_request",
                "--allow-deferred",
                "--required",
                ",".join(HEAVY_REQ),
            ],
            _all("skipped", HEAVY_REQ),
            capsys,
        )
        assert rc == 0
        assert v["status"] == "deferred" and v["reason"] == "merge_group_required"

    def test_config_error_exit_two_and_fails_the_gate(self, capsys):
        """判定器自己坏了（非法组合 / 烂 JSON）也不能算通过。"""
        rc = AG.main(
            [
                "--mode",
                "integration",
                "--event",
                "merge_group",
                "--allow-deferred",
                "--required",
                ",".join(HEAVY_REQ),
                "--needs-json",
                "{}",
            ]
        )
        assert rc == 2
        v = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert v["status"] == "failure" and v["reason"] == "config_error"

    def test_bad_json_exit_two(self, capsys):
        rc = AG.main(
            ["--mode", "fast", "--event", "pull_request", "--required", "a", "--needs-json", "{{{"]
        )
        assert rc == 2

    def test_summary_mentions_deferral(self, capsys, tmp_path, monkeypatch):
        """summary 必须醒目写出「完整资格推迟到 merge_group」——
        一个安静的绿 Gate 会让人以为重型验证过了。"""
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        self._run(
            [
                "--mode",
                "integration",
                "--event",
                "pull_request",
                "--allow-deferred",
                "--required",
                ",".join(HEAVY_REQ),
            ],
            _all("skipped", HEAVY_REQ),
            capsys,
        )
        text = summary.read_text(encoding="utf-8")
        assert "deferred to merge_group" in text

    def test_always_gate_still_fails_after_upstream_failure(self, capsys):
        """`if: always()` 让 Gate 在上游失败后照跑——照跑的它必须红。"""
        rc, v = self._run(
            [
                "--mode",
                "integration",
                "--event",
                "merge_group",
                "--require-heavy",
                "--required",
                ",".join(HEAVY_REQ),
            ],
            {"package": "failure", "windows-exe-smoke": "cancelled", "macos-app-smoke": "skipped"},
            capsys,
        )
        assert rc == 1 and v["status"] == "failure"
