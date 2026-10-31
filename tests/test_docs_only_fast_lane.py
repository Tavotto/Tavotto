"""docs-only 快车道的 workflow 形状契约 + Gate 步骤的真跑（含 bootstrap 空窗的旧判定器）。

机制（详见 ci.yml 的 `docs-scope` 段与 scripts/ci/aggregate_gate.py）：
`docs-scope` 分类 job 输出 `docs_only`；七个快线 job 与五个重型 job 在 `docs_only == 'true'`
时 skipped；两个 Gate 只在「分类 job success 且 docs_only == 'true'」时才带
`--classifier docs-scope --docs-only-skippable …`，判定器再用 needs 里的 result/outputs 核一遍。

这里钉三件事：
1. 哪些 job 被短路、哪些照跑（名单是闭集，写死在测试里，改名单必须改这里并说理由）；
2. Gate 的两个 shell 步骤**原样执行**（bash 真跑 ci.yml 里的 run 脚本）在各种 needs 组合下的结论；
3. bootstrap：默认分支上还是引入前的旧判定器时，普通 PR / 队列候选不能被判红
   （`tests/fixtures/ci_gate/aggregate_gate_pre_docs_scope.py` 是那份旧判定器的冻结副本）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
NEW_JUDGE = ROOT / "scripts" / "ci" / "aggregate_gate.py"
OLD_JUDGE = ROOT / "tests" / "fixtures" / "ci_gate" / "aggregate_gate_pre_docs_scope.py"

#: docs-only 的 run 上**照跑**的 fast 档 job（python-lint / cla-check 是几秒的门禁；release-note-gate
#: 是发行说明闸；docs-scope 是分类器自己）。
KEPT = {"python-lint", "cla-check", "release-note-gate", "docs-scope"}
#: docs-only 的 run 上被短路的 fast 档 job。
FAST_SKIPPABLE = {
    "invariants",
    "backend-fast",
    "frontend",
    "plugin-candidate",
    "workerd",
    "desktop-shell",
    "compat-smoke",
}
HEAVY = {"backend-platforms", "package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e"}
GUARD = "needs.docs-scope.outputs.docs_only != 'true'"


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _job(job_id: str) -> str:
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)", CI, re.S)
    assert m, f"ci.yml 里切不出 job `{job_id}`"
    return _code(m.group(0))


def _needs(job_id: str) -> set[str]:
    m = re.search(r"(?m)^    needs: \[([^\]]*)\]", _job(job_id))
    assert m, f"{job_id} 读不出单行 needs"
    return {s.strip() for s in m.group(1).split(",")}


def _required(job_id: str) -> list[str]:
    m = re.search(r"--required\s+([\w,\-]+)", _job(job_id))
    assert m
    return m.group(1).split(",")


def _skippable_flag(job_id: str) -> set[str]:
    m = re.search(r"--docs-only-skippable\s+([\w,\-]+)", _job(job_id))
    assert m, f"{job_id} 里没有 --docs-only-skippable"
    return set(m.group(1).split(","))


def _condition(job_id: str) -> str:
    code = _job(job_id)
    folded = re.search(r"(?m)^    if: >-\n((?: {6,}\S.*\n)+)", code)
    if folded:
        return " ".join(ln.strip() for ln in folded.group(1).splitlines())
    m = re.search(r"(?m)^    if: (.+)$", code)
    assert m, f"{job_id} 没有 job 级 if"
    return m.group(1).strip()


# ───────────────────────────────────────────── 形状
class TestWorkflowShape:
    def test_the_classifier_job_runs_on_pr_and_merge_group_and_exposes_the_output(self):
        code = _job("docs-scope")
        assert re.search(
            r"(?m)^    if: github\.event_name == 'pull_request' \|\| github\.event_name == 'merge_group'$",
            code,
        ), "分类 job 在 merge_group 上被跳过 = fast gate 永久红"
        assert "docs_only: ${{ steps.classify.outputs.docs_only }}" in code

    def test_the_classifier_comes_from_the_default_branch_and_runs_isolated(self):
        code = _job("docs-scope")
        assert "default_branch" in code
        assert 'python3 -I "$TRUSTED/docs_scope.py"' in code

    def test_merge_group_classification_uses_base_and_head_sha(self):
        code = _job("docs-scope")
        assert "BASE_SHA: ${{ github.event.merge_group.base_sha }}" in code
        assert "HEAD_SHA: ${{ github.event.merge_group.head_sha }}" in code
        assert 'ARGS+=(--base-sha "$BASE_SHA" --head-sha "$HEAD_SHA")' in code
        assert "fetch-depth: ${{ github.event_name == 'merge_group' && '0' || '1' }}" in code

    def test_pull_request_classification_reconciles_the_file_count(self):
        code = _job("docs-scope")
        assert "--expected-files" in code and ".changed_files" in code

    def test_the_minimal_docs_check_runs_only_when_docs_only_and_is_part_of_the_job(self):
        code = _job("docs-scope")
        m = re.search(
            r"(?ms)^      - name: 校验 发行说明里的相对链接.*?\n        if: (.+?)\n.*?pytest -q (\S+)",
            code,
        )
        assert m, "docs-only 的最小说明检查不见了"
        assert m.group(1) == "steps.classify.outputs.docs_only == 'true'"
        assert m.group(2) == "tests/test_docs_references.py"

    def test_exactly_the_listed_fast_jobs_are_short_circuited(self):
        fast_required = set(_required("ci-fast-gate"))
        assert fast_required == KEPT | FAST_SKIPPABLE
        for job in FAST_SKIPPABLE:
            cond = _condition(job)
            assert cond.endswith(f"&& {GUARD}"), (job, cond)
            assert "docs-scope" in _needs(job), job
        for job in KEPT - {"docs-scope"}:
            assert "docs-scope" not in _condition(job), f"{job} 不该被 docs-only 短路"
            assert "docs-scope" not in _job(job), f"{job} 不该依赖分类 job"

    def test_exactly_the_five_heavy_jobs_are_short_circuited(self):
        assert set(_required("ci-integration-gate")) == HEAVY
        for job in HEAVY:
            cond = _condition(job)
            assert cond.endswith(f"&& {GUARD}"), (job, cond)
            assert "'full-ci'" in cond and "github.event_name == 'merge_group'" in cond
            assert "docs-scope" in _needs(job), job

    def test_every_short_circuit_expression_is_the_unambiguous_form(self):
        """只认 `!= 'true'`：分类 job 失败 / 输出为空时重型照常「跳」（needs 失败），
        而不是靠 `== 'false'` 这种在空输出上两头都不成立的写法。"""
        for job in FAST_SKIPPABLE | HEAVY:
            assert "== 'false'" not in _condition(job)

    def test_fast_gate_requires_the_classifier_and_passes_the_exact_skip_list(self):
        assert "docs-scope" in _required("ci-fast-gate")
        assert "docs-scope" in _needs("ci-fast-gate")
        assert _skippable_flag("ci-fast-gate") == FAST_SKIPPABLE
        code = _job("ci-fast-gate")
        assert '[ "$DOCS_ONLY" = "true" ]' in code
        assert "--classifier docs-scope" in code

    def test_integration_gate_sees_the_classifier_but_does_not_require_it(self):
        assert _needs("ci-integration-gate") == HEAVY | {"docs-scope"}
        assert "docs-scope" not in _required("ci-integration-gate")
        assert _skippable_flag("ci-integration-gate") == HEAVY
        code = _job("ci-integration-gate")
        assert '[ "$DOCS_ONLY" = "true" ]' in code
        assert 'elif [ "$DOCS_SCOPE_RESULT" = "success" ]' in code
        assert 'del(.["docs-scope"])' in code

    def test_both_gates_still_take_the_judge_from_the_default_branch(self):
        for gate in ("ci-fast-gate", "ci-integration-gate"):
            code = _job(gate)
            assert "default_branch" in code
            assert 'python3 -I "$RUNNER_TEMP/trusted-gate/aggregate_gate.py"' in code

    def test_the_scope_is_release_notes_only(self):
        sys.path.insert(0, str(ROOT / "scripts" / "ci"))
        import docs_scope

        assert docs_scope.SCOPE_PREFIX == "docs/release-notes/"
        assert docs_scope.EXCLUDED == {"docs/release-notes/UNRELEASED.md"}


# ───────────────────────────────────────────── Gate 步骤真跑
bash = shutil.which("bash")
jq = shutil.which("jq")
pytestmark_gate = pytest.mark.skipif(
    bash is None or jq is None or sys.platform == "win32",
    reason="Gate 步骤只在 ubuntu 的 bash + jq 里执行",
)


def _step_script(job_id: str, event: str) -> str:
    block = _job(job_id)
    m = re.search(
        r"(?ms)^      - name: 聚合判定\n.*?^        run: \|\n((?:          .*\n)+)", block
    )
    assert m, f"{job_id} 读不出「聚合判定」的 run: | 块"
    script = "\n".join(ln[10:] for ln in m.group(1).splitlines()) + "\n"
    return script.replace("${{ github.event_name }}", event)


def _run_gate(job_id, judge: Path, needs: dict, *, event="pull_request", full_ci="false", tmp_path):
    runner_temp = tmp_path / "rt"
    (runner_temp / "trusted-gate").mkdir(parents=True, exist_ok=True)
    shutil.copy(judge, runner_temp / "trusted-gate" / "aggregate_gate.py")
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    if not (shim / "python3").exists():
        (shim / "python3").symlink_to(sys.executable)
    cls = needs.get("docs-scope", {})
    import os

    env = {
        "PATH": f"{shim}:{os.environ.get('PATH', '')}",
        "RUNNER_TEMP": str(runner_temp),
        "NEEDS_JSON": json.dumps(needs),
        "DOCS_ONLY": cls.get("outputs", {}).get("docs_only", ""),
        "DOCS_SCOPE_RESULT": cls.get("result", ""),
        "GATE_EVENT": event,
        "GATE_FULL_CI": full_ci,
    }
    proc = subprocess.run(
        [bash, "-c", _step_script(job_id, event)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        # 旧判定器不认新参数时 argparse 直接退出 2、没有 JSON——必须是非零（红），不能是 0
        assert proc.returncode != 0, f"\nstdout={proc.stdout}\nstderr={proc.stderr}"
        return proc.returncode, {"stderr": proc.stderr}
    assert len(lines) == 1, f"\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return proc.returncode, json.loads(lines[0])


def _n(result, **outputs):
    d = {"result": result}
    if outputs:
        d["outputs"] = outputs
    return d


def _fast_needs(*, docs_only: str, cls="success", skipped=FAST_SKIPPABLE, **over):
    needs = {j: _n("success") for j in KEPT - {"docs-scope"}}
    needs["docs-scope"] = _n(cls, docs_only=docs_only)
    for j in FAST_SKIPPABLE:
        needs[j] = _n("skipped" if j in skipped else "success")
    for j, r in over.items():
        needs[j.replace("_", "-")] = _n(r)
    return needs


def _heavy_needs(*, docs_only: str, cls="success", heavy="skipped"):
    needs = {j: _n(heavy) for j in HEAVY}
    needs["docs-scope"] = _n(cls, docs_only=docs_only)
    return needs


@pytestmark_gate
class TestFastGateStep:
    @pytest.mark.parametrize("event", ["pull_request", "merge_group"])
    def test_docs_only_run_passes_with_the_new_judge(self, event, tmp_path):
        rc, v = _run_gate(
            "ci-fast-gate",
            NEW_JUDGE,
            _fast_needs(docs_only="true"),
            event=event,
            tmp_path=tmp_path,
        )
        assert rc == 0 and v["reason"] == "docs_only_fast_path", v

    def test_plain_run_passes_with_the_new_judge(self, tmp_path):
        rc, v = _run_gate(
            "ci-fast-gate",
            NEW_JUDGE,
            _fast_needs(docs_only="false", skipped=()),
            tmp_path=tmp_path,
        )
        assert rc == 0 and v["reason"] == "all_required_success", v

    def test_skipped_heavy_without_docs_only_is_red(self, tmp_path):
        """docs_only=false 时 skipped 照旧是失败——快车道没有顺带放松别的。"""
        rc, v = _run_gate(
            "ci-fast-gate", NEW_JUDGE, _fast_needs(docs_only="false"), tmp_path=tmp_path
        )
        assert rc == 1 and v["reason"] == "upstream_not_success", v

    @pytest.mark.parametrize("cls", ["failure", "skipped", "cancelled"])
    def test_classifier_not_success_is_red(self, cls, tmp_path):
        rc, v = _run_gate(
            "ci-fast-gate",
            NEW_JUDGE,
            _fast_needs(docs_only="true", cls=cls),
            tmp_path=tmp_path,
        )
        assert rc == 1, v

    @pytest.mark.parametrize("bad", ["failure", "skipped"])
    @pytest.mark.parametrize("job", ["python_lint", "cla_check", "release_note_gate"])
    def test_a_kept_gate_failing_is_still_red_on_a_docs_only_run(self, job, bad, tmp_path):
        rc, _ = _run_gate(
            "ci-fast-gate",
            NEW_JUDGE,
            _fast_needs(docs_only="true", **{job: bad}),
            tmp_path=tmp_path,
        )
        assert rc == 1

    # ---- bootstrap：默认分支上还是旧判定器 ----
    def test_bootstrap_plain_run_is_not_red_with_the_old_judge(self, tmp_path):
        """引入本机制的 PR 自己（以及合入前所有新 workflow + 旧判定器的组合）：docs_only 恒 false，
        步骤不传新参数，docs-scope 是普通 success 的 required——旧判定器照绿。"""
        for event in ("pull_request", "merge_group"):
            rc, v = _run_gate(
                "ci-fast-gate",
                OLD_JUDGE,
                _fast_needs(docs_only="false", skipped=()),
                event=event,
                tmp_path=tmp_path,
            )
            assert rc == 0, (event, v)

    def test_bootstrap_docs_only_run_fails_closed_with_the_old_judge(self, tmp_path):
        """旧判定器不认 skipped 豁免：docs-only 的 run 在 bootstrap 期间红（慢路径靠重跑 /
        full-ci 标签之外无解，但绝不是假绿）。合入后默认分支就是新判定器。"""
        rc, _ = _run_gate(
            "ci-fast-gate", OLD_JUDGE, _fast_needs(docs_only="true"), tmp_path=tmp_path
        )
        assert rc != 0


@pytestmark_gate
class TestIntegrationGateStep:
    def test_merge_group_docs_only_passes(self, tmp_path):
        rc, v = _run_gate(
            "ci-integration-gate",
            NEW_JUDGE,
            _heavy_needs(docs_only="true"),
            event="merge_group",
            tmp_path=tmp_path,
        )
        assert rc == 0 and v["reason"] == "docs_only_fast_path", v

    def test_plain_pr_docs_only_passes(self, tmp_path):
        rc, v = _run_gate(
            "ci-integration-gate", NEW_JUDGE, _heavy_needs(docs_only="true"), tmp_path=tmp_path
        )
        assert rc == 0 and v["reason"] == "docs_only_fast_path", v

    def test_merge_group_without_docs_only_still_needs_real_heavy_results(self, tmp_path):
        rc, v = _run_gate(
            "ci-integration-gate",
            NEW_JUDGE,
            _heavy_needs(docs_only="false"),
            event="merge_group",
            tmp_path=tmp_path,
        )
        assert rc == 1 and v["reason"] == "upstream_not_success", v

    @pytest.mark.parametrize("cls", ["failure", "skipped", "cancelled"])
    @pytest.mark.parametrize("event", ["pull_request", "merge_group"])
    def test_classifier_not_success_is_red(self, cls, event, tmp_path):
        rc, v = _run_gate(
            "ci-integration-gate",
            NEW_JUDGE,
            _heavy_needs(docs_only="", cls=cls),
            event=event,
            tmp_path=tmp_path,
        )
        assert rc == 1, v

    def test_plain_pr_still_defers_and_merge_group_still_requires_heavy(self, tmp_path):
        rc, v = _run_gate(
            "ci-integration-gate", NEW_JUDGE, _heavy_needs(docs_only="false"), tmp_path=tmp_path
        )
        assert rc == 0 and v["status"] == "deferred", v
        rc, v = _run_gate(
            "ci-integration-gate",
            NEW_JUDGE,
            _heavy_needs(docs_only="false", heavy="success"),
            event="merge_group",
            tmp_path=tmp_path,
        )
        assert rc == 0 and v["reason"] == "all_required_success", v

    def test_full_ci_label_on_a_non_docs_pr_is_unchanged(self, tmp_path):
        rc, _ = _run_gate(
            "ci-integration-gate",
            NEW_JUDGE,
            _heavy_needs(docs_only="false"),
            full_ci="true",
            tmp_path=tmp_path,
        )
        assert rc == 1

    # ---- bootstrap ----
    def test_bootstrap_plain_runs_are_not_red_with_the_old_judge(self, tmp_path):
        """docs-scope 从 needs JSON 里剔掉后，参数与引入前逐字相同：PR 仍 deferred、队列仍 success。"""
        rc, v = _run_gate(
            "ci-integration-gate", OLD_JUDGE, _heavy_needs(docs_only="false"), tmp_path=tmp_path
        )
        assert rc == 0 and v["status"] == "deferred", v
        rc, v = _run_gate(
            "ci-integration-gate",
            OLD_JUDGE,
            _heavy_needs(docs_only="false", heavy="success"),
            event="merge_group",
            tmp_path=tmp_path,
        )
        assert rc == 0 and v["status"] == "success", v

    def test_bootstrap_classifier_failure_is_red_with_the_old_judge_too(self, tmp_path):
        """分类 job 没 success 时不剔除 → 旧判定器看到多出来的条目 → contract_violation。"""
        rc, v = _run_gate(
            "ci-integration-gate",
            OLD_JUDGE,
            _heavy_needs(docs_only="", cls="failure"),
            event="merge_group",
            tmp_path=tmp_path,
        )
        assert rc == 1 and v["reason"] == "contract_violation", v

    def test_bootstrap_docs_only_merge_group_fails_closed_with_the_old_judge(self, tmp_path):
        rc, _ = _run_gate(
            "ci-integration-gate",
            OLD_JUDGE,
            _heavy_needs(docs_only="true"),
            event="merge_group",
            tmp_path=tmp_path,
        )
        assert rc != 0
