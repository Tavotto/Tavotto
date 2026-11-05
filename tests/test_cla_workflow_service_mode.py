"""`cla-check` 的服务模式：把 ci.yml 里两步 `run: |` 的原文交给 bash 真跑。

`gh` 是替身，判定器是本树的 scripts/ci/cla_gate.py。形状判据证明不了等待循环真的会
等、会停、会在第一轮就放行豁免者，这里逐条跑出来。替身发的 check 形状取自 staging 的
真实发布记录：凭证在 `output.summary`。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOLDER, DEPENDABOT, ACTIONS, EXTERNAL = 88193520, 49699333, 41898282, 261183172
APP_ID, SLUG, CHECK, REPO_ID = 7654321, "tavotto-cla", "Tavotto CLA", 1234
PR, BASE, HEAD = 42, "b" * 40, "a" * 40

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("jq") is None,
    reason="这两步只在 ubuntu 的 bash + jq 里执行；本机没有可用的 bash / jq",
)

_spec = importlib.util.spec_from_file_location(
    "cla_contract_for_service_mode", ROOT / "tests" / "test_cla_workflow_contract.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
_step_script = _contract._step_script

_FAKE_GH = r"""
import json, os, sys, time
from urllib.parse import urlsplit

st = json.load(open(os.environ["FAKE_GH_STATE"], encoding="utf-8"))
path = next(a for a in sys.argv[2:] if a.startswith("repos/"))
log = os.environ["FAKE_GH_LOG"]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(path + "\n")
calls = sum(1 for ln in open(log, encoding="utf-8") if "/check-runs" in ln)
if "/check-runs" in path:
    if st.get("api_down") or calls < st.get("api_up_on_call", 0):
        sys.exit(1)
    runs = []
    if st.get("proof_on_call") and calls >= st["proof_on_call"]:
        now = int(time.time())
        proof = {**st["proof"], "issued_at": now, "expires_at": now + 120}
        text = "tavotto-cla-v1\n" + json.dumps(proof, sort_keys=True, separators=(",", ":"))
        runs = [{"id": 9, "name": st["check"], "app": st["app"], "status": "completed",
                 "conclusion": "success", "head_sha": proof["head_sha"],
                 "output": {"title": st["check"], "summary": text, "text": None}}]
    print(json.dumps({"total_commits": 0, "check_runs": runs}))
elif "/compare/" in urlsplit(path).path:
    if "per_page=1" in path and "per_page=100" not in path:
        print(st.get("total", len(st["commits"])))
    else:
        print(json.dumps(st["commits"]))
else:
    sys.exit(f"fake gh: 未建模的端点 {path}")
"""


def _commit(sha: str, login: str | None, uid: int | None, email: str = "x@example.com") -> dict:
    return {
        "sha": sha,
        "author": {"login": login, "id": uid} if login else None,
        "commit": {"message": "c", "author": {"name": "n", "email": email}},
    }


def _policy(*, service: bool = True) -> dict:
    p = json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
    if service:
        p["offline_signatures"] = []
        p["provider"] = {
            "configured": True,
            "protocol": "tavotto-cla-v1",
            "name": "Tavotto CLA",
            "check_name": CHECK,
            "app_slug": SLUG,
            "app_id": APP_ID,
        }
        ids = {"erwanjun": HOLDER, "dependabot[bot]": DEPENDABOT, "github-actions[bot]": ACTIONS}
        for ex in p["exemptions"]:
            ex["github_id"] = ids[ex["login"]]
    return p


class Rig:
    def __init__(self, tmp: Path, event: str, *, policy: dict | None = None, **state):
        self.event, self.work = event, tmp / "w"
        (self.work / "bin").mkdir(parents=True)
        (self.work / "tmp").mkdir()
        gh = self.work / "bin" / "gh"
        gh.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
        gh.chmod(0o755)
        (self.work / "bin" / "python3").symlink_to(sys.executable)
        policy = _policy() if policy is None else policy
        trusted = self.work / "trusted"
        for rel in (
            "scripts/ci/cla_gate.py",
            "docs/legal/CLA_INDIVIDUAL.md",
            "docs/legal/CLA_CORPORATE.md",
        ):
            (trusted / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, trusted / rel)
        (trusted / ".github").mkdir()
        (trusted / ".github" / "cla-policy.json").write_text(json.dumps(policy), encoding="utf-8")
        material = {k: policy[k] for k in ("agreements", "exemptions")}
        raw = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        group = event == "merge_group"
        proof = {
            "protocol": "tavotto-cla-v1",
            "kind": "merge_group" if group else "pr",
            "repository_id": REPO_ID,
            "head_sha": HEAD,
            "base_sha": BASE,
            "policy_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "members": [{"pr": PR, "headSha": "c" * 40}] if group else [],
            "contributors": [{"id": EXTERNAL, "login": "someone"}],
            "revision": 1,
        }
        if not group:
            proof["pr"] = PR
        doc = {"check": CHECK, "app": {"id": APP_ID, "slug": SLUG}, "proof": proof, **state}
        (self.work / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        self.summary = self.work / "summary.md"
        self.github_env = self.work / "github-env"
        self.github_env.write_text("", encoding="utf-8")
        self.log = self.work / "gh.log"
        self.log.write_text("", encoding="utf-8")
        self.env = {
            "PATH": f"{self.work / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": str(self.work / "tmp"),
            "GITHUB_ENV": str(self.github_env),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GH_TOKEN": "fake",
            "REPO": "o/r",
            "REPOSITORY_ID": str(REPO_ID),
            "TRUSTED": str(trusted),
            "CLA_SERVICE_MODE": "yes",
            "CLA_WAIT_INTERVAL": "0",
            "FAKE_GH_STATE": str(self.work / "state.json"),
            "FAKE_GH_LOG": str(self.log),
            "PR_AUTHOR": "someone",
            "CLA_PR_AUTHOR_ID": str(EXTERNAL),
            "PR_NUMBER": str(PR),
            "PR_HEAD": HEAD,
            "GROUP_BASE": BASE,
            "GROUP_HEAD": HEAD,
        }

    def _bash(self, script: str, **env) -> subprocess.CompletedProcess:
        return subprocess.run(
            [shutil.which("bash"), "-c", script],
            env={**self.env, **env},
            cwd=self.work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def collect(self) -> subprocess.CompletedProcess:
        proc = self._bash(_step_script("收集合并组候选的提交（只读元数据）"))
        for ln in self.github_env.read_text(encoding="utf-8").splitlines():
            key, value = ln.split("=", 1)
            self.env[key] = value
        return proc

    def judge(
        self, commits: list | None = None, **env
    ) -> tuple[int, dict, subprocess.CompletedProcess]:
        if self.event == "pull_request":
            (self.work / "tmp" / "pr-commits.json").write_text(
                json.dumps(commits), encoding="utf-8"
            )
            self.env["CLA_EXPECTED_COMMITS"] = str(len(commits))
        script = _step_script("判定").replace("${{ github.event_name }}", self.event)
        assert "${{" not in script
        proc = self._bash(script, **env)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        assert len(lines) == 1, f"判定没有恰好输出一行 JSON：{proc.stdout}\n{proc.stderr}"
        return proc.returncode, json.loads(lines[0]), proc

    def check_calls(self) -> int:
        return sum("/check-runs" in ln for ln in self.log.read_text(encoding="utf-8").splitlines())


HOLDER_COMMIT = [_commit(HEAD, "erwanjun", HOLDER)]
EXTERNAL_COMMIT = [_commit(HEAD, "someone", EXTERNAL)]


def test_exempt_pr_passes_on_the_first_round_even_when_github_is_down(tmp_path):
    rig = Rig(tmp_path, "pull_request", api_down=True)
    rc, verdict, _ = rig.judge(HOLDER_COMMIT, PR_AUTHOR="erwanjun", CLA_PR_AUTHOR_ID=str(HOLDER))
    assert rc == 0 and verdict["reason"] == "all_contributors_exempt_by_id"
    assert rig.check_calls() == 1


def test_external_pr_waits_for_a_fresh_proof(tmp_path):
    rig = Rig(tmp_path, "pull_request", proof_on_call=3)
    rc, verdict, _ = rig.judge(EXTERNAL_COMMIT)
    assert rc == 0 and verdict["status"] == "success"
    assert rig.check_calls() == 3


def test_step_summary_is_written_once_for_the_final_verdict(tmp_path):
    rig = Rig(tmp_path, "pull_request", proof_on_call=3)
    rig.judge(EXTERNAL_COMMIT)
    assert rig.summary.read_text(encoding="utf-8").count("CLA check") == 1


def test_external_pr_without_proof_fails_closed_at_the_deadline(tmp_path):
    rig = Rig(tmp_path, "pull_request")
    rc, verdict, proc = rig.judge(EXTERNAL_COMMIT, CLA_WAIT_SECONDS="1", CLA_WAIT_INTERVAL="1")
    assert rc == 1 and verdict["status"] == "failure"
    assert "::error::CLA 未通过：还没有新鲜的服务凭证" in proc.stdout
    assert "读取服务 check 出错" not in proc.stdout


def test_api_errors_are_named_and_never_pass(tmp_path):
    rig = Rig(tmp_path, "pull_request", api_down=True)
    rc, verdict, proc = rig.judge(EXTERNAL_COMMIT, CLA_WAIT_SECONDS="1", CLA_WAIT_INTERVAL="1")
    assert rc == 1 and verdict["status"] == "failure"
    assert "::error::CLA 未通过：读取服务 check 出错" in proc.stdout


def test_api_error_is_retried_and_can_recover(tmp_path):
    """出错不是结论：下一轮读到新鲜凭证照常通过，日志里两种状态分得清。"""
    rig = Rig(tmp_path, "pull_request", api_up_on_call=2, proof_on_call=2)
    rc, verdict, proc = rig.judge(EXTERNAL_COMMIT)
    assert rc == 0 and verdict["status"] == "success"
    assert rig.check_calls() == 2
    assert "读取服务 check 出错，0 秒后再读。" in proc.stdout


def test_merge_group_api_errors_never_pass(tmp_path):
    rig = Rig(tmp_path, "merge_group", commits=EXTERNAL_COMMIT, api_down=True)
    assert rig.collect().returncode == 0
    rc, verdict, proc = rig.judge(None, CLA_WAIT_SECONDS="1", CLA_WAIT_INTERVAL="1")
    assert rc == 1 and verdict["status"] == "failure"
    assert "::error::CLA 未通过：读取服务 check 出错" in proc.stdout


def test_time_budget_keeps_a_margin_inside_the_job_timeout():
    """循环上限 + 余量 ≤ job 上限；改任何一个都要回来算这笔账。"""
    job = _contract._job(_contract.CI, _contract.CLA_JOB)
    timeout = int(re.search(r"(?m)^    timeout-minutes: (\d+)$", job).group(1))
    wait = int(re.search(r"CLA_WAIT_SECONDS:-(\d+)", job).group(1))
    assert wait == 1500 and timeout == 35
    assert wait / 60 + 10 <= timeout


def test_unresolved_identity_fails_at_once(tmp_path):
    rig = Rig(tmp_path, "pull_request")
    commits = [_commit(HEAD, None, None, email="someone@example.com")]
    rc, verdict, _ = rig.judge(commits, CLA_WAIT_SECONDS="60")
    assert rc == 1 and any(p.startswith("unresolved:") for p in verdict["problems"])
    assert rig.check_calls() == 1


def test_config_error_fails_at_once_with_its_exit_code(tmp_path):
    broken = _policy()
    del broken["exemptions"][0]["github_id"]
    rig = Rig(tmp_path, "pull_request", policy=broken)
    rc, verdict, _ = rig.judge(EXTERNAL_COMMIT, CLA_WAIT_SECONDS="60")
    assert rc == 2 and verdict["reason"] == "config_error"
    assert rig.check_calls() == 1


def test_all_exempt_merge_group_passes_locally(tmp_path):
    rig = Rig(tmp_path, "merge_group", commits=HOLDER_COMMIT)
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 0 and verdict["reason"] == "merge_candidate_exempt_by_id"


def test_merge_group_with_external_needs_the_group_proof(tmp_path):
    rig = Rig(tmp_path, "merge_group", commits=EXTERNAL_COMMIT, proof_on_call=2)
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 0 and verdict["reason"] == "fresh_merge_candidate_qualified"
    assert rig.check_calls() == 2


@pytest.mark.parametrize(
    "state",
    [
        {"commits": HOLDER_COMMIT, "total": 2},
        {"commits": HOLDER_COMMIT * 2},
        {"commits": [_commit("d" * 40, "erwanjun", HOLDER)]},
    ],
)
def test_incomplete_group_enumeration_refuses(tmp_path, state):
    rig = Rig(tmp_path, "merge_group", **state)
    assert rig.collect().returncode == 1


def _mode(rig: Rig) -> str:
    """跑 ci.yml 里判定服务模式的那段原文，不复刻 jq 表达式。"""
    script = _step_script("取默认分支上的可信判定输入")
    start = script.index("mode=$(jq")
    proc = rig._bash(script[start:])
    assert proc.returncode == 0, proc.stderr
    exported = rig.github_env.read_text(encoding="utf-8").splitlines()
    return dict(ln.split("=", 1) for ln in exported)["CLA_SERVICE_MODE"]


def test_mode_is_read_from_the_trusted_policy(tmp_path):
    assert _mode(Rig(tmp_path / "on", "pull_request")) == "yes"
    assert _mode(Rig(tmp_path / "off", "pull_request", policy=_policy(service=False))) == "no"


def test_repository_policy_on_a_pull_request_takes_the_old_path(tmp_path):
    """现有 policy 未启用服务：PR 判定一次、不读 check、不进循环。"""
    rig = Rig(tmp_path, "pull_request", policy=_policy(service=False), commits=HOLDER_COMMIT)
    rig.env["CLA_SERVICE_MODE"] = _mode(rig)
    assert rig.env["CLA_SERVICE_MODE"] == "no"
    assert rig.collect().returncode == 0  # 这一步在 PR 上本来被 if 跳过；真跑也立刻退出
    rc, verdict, _ = rig.judge(HOLDER_COMMIT, PR_AUTHOR="erwanjun", CLA_PR_AUTHOR_ID=str(HOLDER))
    assert rc == 0 and verdict["reason"] == "all_contributors_qualified"
    assert rig.log.read_text(encoding="utf-8") == ""


def test_repository_policy_on_a_merge_group_exits_at_once(tmp_path):
    """现有 policy 未启用服务：收集步不调 API、不落文件，判定仍是 not_applicable。"""
    rig = Rig(tmp_path, "merge_group", policy=_policy(service=False), commits=HOLDER_COMMIT)
    rig.env["CLA_SERVICE_MODE"] = _mode(rig)
    assert rig.env["CLA_SERVICE_MODE"] == "no"
    assert rig.collect().returncode == 0
    assert rig.log.read_text(encoding="utf-8") == ""
    assert not (rig.work / "tmp" / "group-commits.json").exists()
    rc, verdict, _ = rig.judge()
    assert rc == 0 and verdict["status"] == "not_applicable"
    assert rig.log.read_text(encoding="utf-8") == ""


def test_collect_step_only_runs_on_merge_groups():
    job = _contract._job(_contract.CI, _contract.CLA_JOB)
    block = job[job.index("- name: 收集合并组候选的提交") :]
    assert "if: github.event_name == 'merge_group'" in block.split("run: |")[0]
