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


# ============================================================ docs-only 快车道（--classifier）
KEPT = ["python-lint", "cla-check", "release-note-gate", "docs-scope"]
SKIPPABLE = ["invariants", "backend-fast", "frontend", "workerd"]
DOCS_REQ = KEPT + SKIPPABLE
CLS = "docs-scope"


def _docs_results(**over: str) -> dict[str, str]:
    r = {j: "success" for j in KEPT}
    r.update({j: "skipped" for j in SKIPPABLE})
    r.update(over)
    return r


def _outputs(value="true") -> dict:
    return {CLS: {"docs_only": value}}


def _docs(results, *, outputs=None, event="pull_request", skippable=SKIPPABLE, required=DOCS_REQ):
    return AG.decide(
        "fast",
        event,
        required,
        results,
        outputs=_outputs() if outputs is None else outputs,
        classifier=CLS,
        docs_only_skippable=skippable,
    )


class TestDocsOnlyFastLane:
    @pytest.mark.parametrize("event", ["pull_request", "merge_group"])
    def test_docs_only_with_evidence_accepts_the_listed_skips(self, event):
        v = _docs(_docs_results(), event=event)
        assert v["status"] == "success"
        assert v["reason"] == "docs_only_fast_path"
        assert v["docs_only_skipped"] == SKIPPABLE

    @pytest.mark.parametrize("cls_result", ["failure", "skipped", "cancelled"])
    def test_classifier_that_did_not_succeed_gives_no_exemption(self, cls_result):
        v = _docs(_docs_results(**{CLS: cls_result}))
        assert v["status"] == "failure"

    @pytest.mark.parametrize("value", ["false", "", "TRUE", "True", "1", "yes"])
    def test_only_the_literal_true_counts_as_evidence(self, value):
        v = _docs(_docs_results(), outputs=_outputs(value))
        assert v["status"] == "failure"
        assert any("invariants" in p for p in v["problems"])

    def test_missing_outputs_give_no_exemption(self):
        for outputs in ({}, {CLS: {}}, {"other": {"docs_only": "true"}}):
            assert _docs(_docs_results(), outputs=outputs)["status"] == "failure"

    def test_caller_flag_cannot_substitute_for_the_evidence(self):
        """参数只列名单；是否成立只看 needs 里分类 job 的 result 与 outputs。"""
        v = AG.decide(
            "fast",
            "pull_request",
            DOCS_REQ,
            _docs_results(),
            outputs=None,
            classifier=CLS,
            docs_only_skippable=SKIPPABLE,
        )
        assert v["status"] == "failure"

    def test_kept_jobs_must_still_succeed(self):
        for job in ("python-lint", "cla-check", "release-note-gate"):
            for bad in ("failure", "skipped", "cancelled"):
                assert _docs(_docs_results(**{job: bad}))["status"] == "failure", (job, bad)

    def test_a_skippable_job_that_ran_and_failed_still_fails(self):
        v = _docs(_docs_results(frontend="failure"))
        assert v["status"] == "failure"
        assert any("frontend: failure" in p for p in v["problems"])

    def test_skippable_job_that_ran_and_succeeded_is_fine(self):
        assert _docs(_docs_results(frontend="success"))["status"] == "success"

    def test_a_job_outside_the_skippable_list_cannot_be_skipped(self):
        r = _docs_results()
        r["compat-smoke"] = "skipped"
        v = _docs(r, required=DOCS_REQ + ["compat-smoke"])
        assert v["status"] == "failure"
        assert any("compat-smoke" in p for p in v["problems"])

    def test_without_evidence_the_old_strict_rule_is_unchanged(self):
        v = AG.decide("fast", "pull_request", DOCS_REQ, _docs_results())
        assert v["status"] == "failure"

    def test_closed_set_still_applies_in_docs_only_mode(self):
        v = _docs(dict(_docs_results(), extra="success"))
        assert v["status"] == "failure" and v["reason"] == "contract_violation"
        r = _docs_results()
        del r["invariants"]
        assert _docs(r)["reason"] == "contract_violation"

    @pytest.mark.parametrize(
        "kw",
        [
            {"classifier": CLS, "docs_only_skippable": []},
            {"classifier": None, "docs_only_skippable": ["invariants"]},
            {"classifier": CLS, "docs_only_skippable": [CLS, "invariants"]},
            {"classifier": CLS, "docs_only_skippable": ["not-required"]},
        ],
    )
    def test_bad_flag_combinations_are_config_errors(self, kw):
        with pytest.raises(AG.ConfigError):
            AG.decide("fast", "pull_request", DOCS_REQ, _docs_results(), outputs=_outputs(), **kw)

    # ---- integration gate：分类 job 在 needs 里、不在 required 里 ----
    def _int(self, heavy, cls, *, event="merge_group", deferred=False, outputs=None, flags=True):
        results = dict(heavy, **({CLS: cls} if cls else {}))
        kw = {}
        if flags:
            kw = dict(
                outputs=_outputs() if outputs is None else outputs,
                classifier=CLS,
                docs_only_skippable=HEAVY_REQ,
            )
        return AG.decide(
            "integration",
            event,
            HEAVY_REQ,
            results,
            require_heavy=not deferred,
            allow_deferred=deferred,
            **kw,
        )

    def test_merge_group_docs_only_accepts_skipped_heavy(self):
        v = self._int(_all("skipped", HEAVY_REQ), "success")
        assert v["status"] == "success" and v["reason"] == "docs_only_fast_path"

    def test_plain_pr_docs_only_is_a_real_success_not_deferred(self):
        """deferred 的话术是「推迟到 merge_group」，docs-only 在 merge_group 上也不跑重型。"""
        v = self._int(_all("skipped", HEAVY_REQ), "success", event="pull_request", deferred=True)
        assert v["status"] == "success" and v["reason"] == "docs_only_fast_path"

    def test_merge_group_without_evidence_still_fails_on_skipped_heavy(self):
        v = self._int(_all("skipped", HEAVY_REQ), "success", outputs=_outputs("false"))
        assert v["status"] == "failure"

    def test_classifier_failure_fails_the_integration_gate_too(self):
        for res in ("failure", "skipped", "cancelled"):
            v = self._int(_all("skipped", HEAVY_REQ), res)
            assert v["status"] == "failure", res

    def test_failed_classifier_beside_real_heavy_results_still_fails(self):
        """分类 job 红了就红，哪怕重型 job 碰巧都 success（证据自己坏了不能当没看见）。"""
        for res in ("failure", "skipped", "cancelled"):
            v = self._int(_all("success", HEAVY_REQ), res)
            assert v["status"] == "failure", res
            assert any(CLS in p for p in v["problems"]), v

    def test_classifier_extra_without_flags_is_a_contract_violation(self):
        """为什么 ci.yml 在非 docs-only 时把 docs-scope 从 needs JSON 里剔掉：不剔，
        旧判定器（bootstrap 空窗）和新判定器都会把多出来的条目判成 contract_violation。"""
        v = self._int(_all("success", HEAVY_REQ), "success", flags=False)
        assert v["status"] == "failure" and v["reason"] == "contract_violation"

    def test_other_unexpected_jobs_are_still_rejected_next_to_the_classifier(self):
        v = self._int(dict(_all("skipped", HEAVY_REQ), sneaky="success"), "success")
        assert v["status"] == "failure" and v["reason"] == "contract_violation"

    def test_summary_lists_the_skipped_jobs(self):
        v = _docs(_docs_results())
        text = AG.render_summary(v, DOCS_REQ)
        assert "docs-only" in text and "`invariants`" in text


class TestDocsOnlyCli:
    def _run(self, capsys, needs, *extra):
        rc = AG.main(
            [
                "--mode",
                "fast",
                "--event",
                "merge_group",
                "--required",
                ",".join(DOCS_REQ),
                "--needs-json",
                json.dumps(needs),
                *extra,
            ]
        )
        return rc, json.loads(capsys.readouterr().out.splitlines()[0])

    def _needs(self, value="true", **over):
        r = _docs_results(**over)
        needs = {j: {"result": res} for j, res in r.items()}
        needs[CLS]["outputs"] = {"docs_only": value}
        return needs

    FLAGS = ("--classifier", CLS, "--docs-only-skippable", ",".join(SKIPPABLE))

    def test_exit_zero_when_evidence_holds(self, capsys):
        rc, out = self._run(capsys, self._needs(), *self.FLAGS)
        assert rc == 0 and out["reason"] == "docs_only_fast_path"

    def test_exit_one_when_docs_only_is_false(self, capsys):
        rc, out = self._run(capsys, self._needs("false"), *self.FLAGS)
        assert rc == 1 and out["status"] == "failure"

    def test_exit_one_without_the_flags(self, capsys):
        rc, _ = self._run(capsys, self._needs())
        assert rc == 1

    def test_flags_that_name_a_job_outside_required_exit_two(self, capsys):
        rc, out = self._run(
            capsys, self._needs(), "--classifier", CLS, "--docs-only-skippable", "ghost"
        )
        assert rc == 2 and out["reason"] == "config_error"

    def test_outputs_may_be_absent_from_needs_json(self, capsys):
        needs = {j: {"result": r} for j, r in _docs_results().items()}
        rc, _ = self._run(capsys, needs, *self.FLAGS)
        assert rc == 1


# ============================================================ merge_group 快速失败归因
class TestMergeGroupFailFastAttribution:
    """重腿红 → workflow 取消其余 job → 它们的结论是 cancelled。判红规则不变，只多归因。"""

    HEAVY = ["backend-platforms", "package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e"]

    def _mg(self, **overrides):
        r = {j: "success" for j in self.HEAVY}
        r.update(overrides)
        return AG.decide("integration", "merge_group", self.HEAVY, r, require_heavy=True)

    def test_a_red_leg_with_cancelled_siblings_is_red_and_names_the_root(self):
        v = self._mg(
            **{"windows-exe-smoke": "failure", "package": "cancelled", "posix-e2e": "cancelled"}
        )
        assert v["status"] == "failure" and v["reason"] == "upstream_not_success"
        assert v["root_failures"] == ["windows-exe-smoke"]
        assert v["cancelled_by_fail_fast"] == ["package", "posix-e2e"]

    def test_cancelled_without_any_failure_is_still_red_and_not_blamed_on_fail_fast(self):
        """外部取消（新 push / 人手点取消）没有 failure：保持裸 cancelled，不得冒充快速失败。"""
        v = self._mg(**{"package": "cancelled", "posix-e2e": "cancelled"})
        assert v["status"] == "failure"
        assert v["root_failures"] == [] and v["cancelled_by_fail_fast"] == []

    def test_all_cancelled_after_a_failure_can_never_pass(self):
        """快速失败后哪怕其余全是 cancelled，也不能因为『没有别的红』被放过。"""
        v = self._mg(
            **{
                "backend-platforms": "failure",
                "package": "cancelled",
                "windows-exe-smoke": "cancelled",
                "macos-app-smoke": "cancelled",
                "posix-e2e": "cancelled",
            }
        )
        assert v["status"] == "failure"

    def test_attribution_is_merge_group_only(self):
        r = {j: "success" for j in self.HEAVY}
        r.update({"package": "failure", "posix-e2e": "cancelled"})
        v = AG.decide("integration", "pull_request", self.HEAVY, r, require_heavy=True)
        assert v["status"] == "failure"
        assert v["cancelled_by_fail_fast"] == [], "PR / full-ci 的语义不变：不归因快速失败"

    def test_all_success_carries_no_attribution_and_still_passes(self):
        v = self._mg()
        assert v["status"] == "success" and "root_failures" not in v

    def test_summary_points_at_the_root_failure(self):
        v = self._mg(**{"package": "failure", "posix-e2e": "cancelled"})
        text = AG.render_summary(v, self.HEAVY)
        assert "快速失败" in text and "`package`" in text and "`posix-e2e`" in text


class TestFailFastRootFromRunJobs:
    """快速失败取消的是整个 run，红腿自己的 job 结论也是 cancelled（#1655 合入前第一个红组实测：
    backend-platforms (macos-latest, 6) 的 pytest 红，job 却显示 cancelled）。归因改读步骤结论。"""

    HEAVY = TestMergeGroupFailFastAttribution.HEAVY
    RUN_JOBS = [
        {"name": "backend-platforms (macos-latest, 6)", "failed_steps": ["pytest"]},
        {"name": "backend-platforms (macos-latest, 5)", "failed_steps": []},
        {"name": "posix-e2e (1/3)", "failed_steps": []},
        {"name": "CI fast gate", "failed_steps": ["聚合判定"]},
        {"name": "CI integration gate", "failed_steps": ["聚合判定"]},
    ]

    def _all_cancelled(self, event="merge_group", run_jobs=None):
        r = {j: "cancelled" for j in self.HEAVY}
        return AG.decide("integration", event, self.HEAVY, r, require_heavy=True, run_jobs=run_jobs)

    def test_root_is_found_in_step_conclusions_when_every_leg_shows_cancelled(self):
        v = self._all_cancelled(run_jobs=self.RUN_JOBS)
        assert v["status"] == "failure"
        assert v["root_failures"] == ["backend-platforms (macos-latest, 6) · pytest"]
        assert v["cancelled_by_fail_fast"] == sorted(self.HEAVY)

    def test_gates_own_failed_steps_are_never_the_root(self):
        only_gates = [j for j in self.RUN_JOBS if j["name"].startswith("CI ")]
        assert AG.root_failures_from_run_jobs(only_gates) == []
        v = self._all_cancelled(run_jobs=only_gates)
        assert v["root_failures"] == [] and v["cancelled_by_fail_fast"] == []

    def test_without_run_jobs_all_cancelled_stays_external(self):
        v = self._all_cancelled(run_jobs=None)
        assert v["root_failures"] == [] and v["cancelled_by_fail_fast"] == []

    def test_run_jobs_are_ignored_outside_merge_group(self):
        v = self._all_cancelled(event="pull_request", run_jobs=self.RUN_JOBS)
        assert v["status"] == "failure"
        assert v["root_failures"] == [] and v["cancelled_by_fail_fast"] == []

    def test_run_jobs_never_turn_red_into_green(self):
        r = {j: "success" for j in self.HEAVY}
        r["package"] = "cancelled"
        v = AG.decide("integration", "merge_group", self.HEAVY, r, require_heavy=True, run_jobs=[])
        assert v["status"] == "failure"

    def test_main_reads_run_jobs_from_env_and_tolerates_bad_lines(
        self, tmp_path, monkeypatch, capsys
    ):
        f = tmp_path / "run-jobs.ndjson"
        f.write_text(
            "not json\n"
            + "\n".join(json.dumps(j, ensure_ascii=False) for j in self.RUN_JOBS)
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("RUN_JOBS_FILE", str(f))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        needs = json.dumps({j: {"result": "cancelled", "outputs": {}} for j in self.HEAVY})
        rc = AG.main(
            ["--mode", "integration", "--event", "merge_group", "--required", ",".join(self.HEAVY),
             "--needs-json", needs, "--require-heavy"]
        )  # fmt: skip
        assert rc == 1
        verdict = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert verdict["root_failures"] == ["backend-platforms (macos-latest, 6) · pytest"]

    def test_missing_run_jobs_file_is_not_an_error(self, tmp_path):
        assert AG._read_run_jobs(str(tmp_path / "nope.ndjson")) is None
        assert AG._read_run_jobs("") is None


# ============================================================ 改到 e2e 用例的 PR（--e2e-legs）
E2E_LEGS = ["windows-exe-smoke", "posix-e2e"]
E2E_REQ = ["backend-platforms", "package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e"]
E2E_REST = ["backend-platforms", "package", "macos-app-smoke"]


def _e2e_results(legs="success", rest="skipped", **over: str) -> dict[str, str]:
    r = {j: legs for j in E2E_LEGS}
    r.update({j: rest for j in E2E_REST})
    r.update(over)
    r["docs-scope"] = "success"
    return r


def _e2e(results, *, flag="true", event="pull_request", legs=None, **kw):
    kw.setdefault("allow_deferred", True)
    return AG.decide(
        "integration",
        event,
        E2E_REQ,
        results,
        outputs={"docs-scope": {"docs_only": "false", "e2e_specs_changed": flag}},
        e2e_classifier="docs-scope",
        e2e_legs=E2E_LEGS if legs is None else legs,
        **kw,
    )


class TestE2eLegsOnPullRequest:
    """PR 改到 web/e2e/** 时 windows-exe-smoke / posix-e2e 在 PR 上跑，Gate 把它们当必需（#1736）。"""

    def test_legs_green_rest_deferred_is_success_not_deferred(self):
        v = _e2e(_e2e_results())
        assert v["status"] == "success" and v["reason"] == "e2e_legs_only"
        assert v["e2e_legs"] == E2E_LEGS

    @pytest.mark.parametrize("bad", ["failure", "cancelled", "skipped"])
    @pytest.mark.parametrize("leg", E2E_LEGS)
    def test_an_e2e_leg_that_is_not_success_fails_the_gate(self, leg, bad):
        """红 / 取消 / 本该跑却 skipped（ci.yml 的 if 与 Gate 脱节）都判红——这是 #1736 要堵的洞。"""
        v = _e2e(_e2e_results(**{leg: bad}))
        assert v["status"] == "failure" and v["reason"] == "upstream_not_success"
        assert any(p.startswith(f"{leg}: {bad}") for p in v["problems"])

    def test_the_other_heavy_legs_may_not_be_half_run(self):
        v = _e2e(_e2e_results(**{"package": "success"}))
        assert v["status"] == "failure"
        assert any("package" in p for p in v["problems"])

    def test_other_heavy_leg_red_fails(self):
        assert _e2e(_e2e_results(**{"package": "failure"}))["status"] == "failure"

    @pytest.mark.parametrize("flag", ["false", "", "TRUE", "1"])
    def test_without_the_literal_true_evidence_a_partial_run_is_still_a_failure(self, flag):
        """证据不成立：参数形同没传——e2e 腿跑了其余 skipped 是「半跑半跳」，照旧判失败（不留缝）。"""
        v = _e2e(_e2e_results(), flag=flag)
        assert v["status"] == "failure"

    def test_without_the_flag_all_skipped_is_still_deferred(self):
        """不改 e2e 的 PR 照旧：重型整体 skipped → deferred。"""
        v = _e2e(_e2e_results(legs="skipped"), flag="false")
        assert v["status"] == "deferred"

    def test_evidence_with_everything_skipped_fails(self):
        """ci.yml 应该跑起来的腿没跑（比如 if 被改坏）：不能拿 deferred 蒙混。"""
        v = _e2e(_e2e_results(legs="skipped"))
        assert v["status"] == "failure"

    @pytest.mark.parametrize("cls", ["failure", "skipped", "cancelled"])
    def test_classifier_that_did_not_succeed_gives_no_evidence(self, cls):
        r = _e2e_results()
        r["docs-scope"] = cls
        v = _e2e(r)
        assert v["status"] == "failure"

    def test_whole_green_with_evidence_is_success(self):
        v = _e2e(_e2e_results(rest="success"))
        assert v["status"] == "success" and v["reason"] == "all_required_success"

    def test_merge_group_and_full_ci_never_take_this_path(self):
        with pytest.raises(AG.ConfigError):
            AG.decide(
                "integration",
                "merge_group",
                E2E_REQ,
                _e2e_results(rest="success"),
                require_heavy=True,
                e2e_classifier="docs-scope",
                e2e_legs=E2E_LEGS,
            )
        # merge_group 不带参数：一律 require-heavy，skipped 就是失败，e2e 腿也必须 success
        v = AG.decide(
            "integration",
            "merge_group",
            E2E_REQ,
            _e2e_results(rest="success", legs="skipped"),
            require_heavy=True,
        )
        assert v["status"] == "failure"

    def test_config_errors(self):
        r = _e2e_results()
        with pytest.raises(AG.ConfigError):  # 只给腿不给分类 job
            AG.decide(
                "integration", "pull_request", E2E_REQ, r, allow_deferred=True, e2e_legs=E2E_LEGS
            )
        with pytest.raises(AG.ConfigError):  # 腿不在 required
            _e2e(r, legs=["nope"])
        with pytest.raises(AG.ConfigError):  # fast 档不接受
            AG.decide(
                "fast", "pull_request", E2E_REQ, r, e2e_classifier="docs-scope", e2e_legs=E2E_LEGS
            )

    def test_cli_round_trip(self, tmp_path, capsys):
        needs = {
            **{j: {"result": r} for j, r in _e2e_results().items() if j != "docs-scope"},
            "docs-scope": {
                "result": "success",
                "outputs": {"docs_only": "false", "e2e_specs_changed": "true"},
            },
        }
        argv = [
            "--mode", "integration", "--event", "pull_request", "--allow-deferred",
            "--required", ",".join(E2E_REQ), "--needs-json", json.dumps(needs),
            "--e2e-classifier", "docs-scope", "--e2e-legs", ",".join(E2E_LEGS),
        ]  # fmt: skip
        assert AG.main(argv) == 0
        assert json.loads(capsys.readouterr().out.splitlines()[0])["reason"] == "e2e_legs_only"
        needs["posix-e2e"]["result"] = "failure"
        argv[argv.index("--needs-json") + 1] = json.dumps(needs)
        assert AG.main(argv) == 1
