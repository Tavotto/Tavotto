"""ADR 0172 接线：原样运行 ci.yml 的 bash，HTTPS 仅在传输边界替换。

叠栈验证可设 CLA_CONTRACT_SCRIPT_ROOT 指向 #1794 的树；不把依赖脚本复制进本仓。
默认使用本树（#1794 合入并承接之后的常规 CI 路径）。
"""

from __future__ import annotations

import base64
import copy
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
SCRIPTS = Path(os.environ.get("CLA_CONTRACT_SCRIPT_ROOT", ROOT))
HOLDER, DEPENDABOT, ACTIONS, EXTERNAL = 88193520, 49699333, 41898282, 261183172
PR, BASE, HEAD, TRUSTED_SHA, REPO_ID = 42, "b" * 40, "a" * 40, "d" * 40, 1234
ORIGIN = "https://cla.example.org"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("jq") is None,
    reason="这些步骤只在 ubuntu 的 bash + jq 执行；本机缺 bash / jq",
)

_spec = importlib.util.spec_from_file_location(
    "cla_contract_for_service_mode", ROOT / "tests" / "test_cla_workflow_contract.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
_step_script = _contract._step_script

_FAKE_GH = r"""
import json, os, sys, subprocess
from urllib.parse import urlsplit, parse_qs
st = json.load(open(os.environ["FAKE_GH_STATE"], encoding="utf-8"))
path = "graphql" if sys.argv[2] == "graphql" else next(a for a in sys.argv[2:] if a.startswith("repos/"))
log = os.environ["FAKE_GH_LOG"]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(path + "\n")
def emit(doc):
    if "--jq" not in sys.argv:
        print(json.dumps(doc)); return
    jq = sys.argv[sys.argv.index("--jq") + 1]
    p = subprocess.run(["jq", "-rc", jq], input=json.dumps(doc), capture_output=True, text=True, encoding="utf-8", check=True)
    print(p.stdout, end="")
if path == "graphql":
    assert "--paginate" in sys.argv
    query = next(x for x in sys.argv if x.startswith("query="))
    assert "$endCursor:String" in query and "after:$endCursor" in query
    assert "branch=main" in sys.argv and "owner=o" in sys.argv and "name=r" in sys.argv
    calls = open(log, encoding="utf-8").read().splitlines().count("graphql")
    if st.get("queue_down"): sys.exit(1)
    for page in st["queue_before" if calls % 2 else "queue_after"]: emit(page)
    sys.exit(0)
u = urlsplit(path)
if "/contents/" in u.path:
    assert parse_qs(u.query) == {"ref": [st["trusted_sha"]]}, path
    emit({"content": st["files"][u.path.split("/contents/", 1)[1]]})
elif "/commits/" in u.path:
    assert u.path.endswith("/commits/main"), path  # App check 端点也不许走
    emit({"sha": st["trusted_sha"]})
elif "/pulls/" in u.path:
    n = int(u.path.rsplit("/", 1)[1])
    snapshot = next(p for p in st["group_prs"] if p["before"]["number"] == n)
    calls = open(log, encoding="utf-8").read().splitlines().count(path)
    emit(snapshot["before" if calls % 2 else "after"])
elif "/compare/" in u.path:
    locator = u.path.split("/compare/", 1)[1]
    if locator == st["base"] + "..." + st["head"]:
        emit({"total_commits": st.get("total", len(st["commits"])), "commits": st["commits"]})
    else:
        snapshot = next(p for p in st["group_prs"] if locator == p["before"]["base"]["sha"] + "..." + p["before"]["head"]["sha"])
        emit({"total_commits": snapshot["total_commits"], "base_commit": {"sha": snapshot["compare_base"]}, "commits": snapshot["commits"]})
else:
    sys.exit(f"fake gh: 未建模的端点 {path}")
"""

# 真实 fetch 模块的 Request、read_once、重试与 main 全跑，只替换 HTTPS opener 与时钟。
_TRANSPORT = r"""
import importlib.util, json, os, sys, urllib.error
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
spec = importlib.util.spec_from_file_location("fetch_impl", Path(__file__).with_name("fetch_impl.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
st = json.load(open(os.environ["FAKE_GH_STATE"], encoding="utf-8"))
clock = [0]
class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.raw[:limit]
class Opener:
    def open(self, request, timeout):
        assert not any(os.environ.get(k) for k in ("GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN"))
        assert "authorization" not in {k.lower() for k in request.headers}
        assert request.full_url.startswith(st["origin"] + "/eligibility/")
        log = Path(os.environ["FAKE_HTTP_LOG"])
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"url": request.full_url, "headers": request.headers}) + "\n")
        count = len(log.read_text(encoding="utf-8").splitlines())
        clock[0] += 40
        fault = st.get("fault")
        if fault == "fetch_crash": raise RuntimeError("simulated fetch failure")
        if fault == "down" or fault == "recover" and count == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "down", {}, None)
        query = parse_qs(urlsplit(request.full_url).query)
        group = "/group?" in request.full_url
        assert query["head"] == [st["head"]]
        assert query["base"] == [st["base"]] if group else query["pr"] == [str(st["pr"])]
        answer = {**st["answer"], "nonce": query["nonce"][0], "observed_at": int(m.time.time())}
        answer.update(st.get("answer_override", {}))
        response = Response()
        response.raw = json.dumps(answer).encode()
        return response
m.urllib.request.build_opener = lambda *args: Opener()
m.fetch.__kwdefaults__["clock"] = lambda: clock[0]
m.fetch.__kwdefaults__["sleep"] = lambda delay: None
raise SystemExit(m.main())
"""


def _commit(sha: str, login: str | None, uid: int | None, message="c") -> dict:
    return {
        "sha": sha,
        "author": {"login": login, "id": uid} if login else None,
        "commit": {
            "message": message,
            "author": {"name": "n", "email": "unknown@example.com"},
        },
    }


def _policy(*, service=True) -> dict:
    p = json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
    if service:
        p["offline_signatures"] = []
        p["authority_origin"] = ORIGIN
        p["provider"] = {
            "configured": True,
            "protocol": "tavotto-cla-v1",
            "name": "Tavotto CLA",
            "check_name": "Tavotto CLA",
            "app_slug": "tavotto-cla",
            "app_id": 7654321,
        }
        ids = {"erwanjun": HOLDER, "dependabot[bot]": DEPENDABOT, "github-actions[bot]": ACTIONS}
        for ex in p["exemptions"]:
            ex["github_id"] = ids[ex["login"]]
    return p


class Rig:
    def __init__(self, tmp: Path, event: str, *, policy=None, **state):
        self.event, self.work = event, tmp / "w"
        (self.work / "bin").mkdir(parents=True)
        (self.work / "tmp").mkdir()
        gh = self.work / "bin" / "gh"
        gh.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
        gh.chmod(0o755)
        (self.work / "bin" / "python3").symlink_to(sys.executable)
        policy = _policy() if policy is None else policy
        self.trusted = self.work / "trusted"
        files = {}
        for rel in (
            "scripts/ci/cla_gate.py",
            "docs/legal/CLA_INDIVIDUAL.md",
            "docs/legal/CLA_CORPORATE.md",
        ):
            source = SCRIPTS if rel.startswith("scripts/") else ROOT
            content = (source / rel).read_bytes()
            files[rel] = base64.b64encode(content).decode()
            (self.trusted / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.trusted / rel).write_bytes(content)
        impl = (SCRIPTS / "scripts/ci/cla_eligibility_fetch.py").read_text(encoding="utf-8")
        fetch_path = self.trusted / "scripts/ci/cla_eligibility_fetch.py"
        (fetch_path.with_name("fetch_impl.py")).write_text(impl, encoding="utf-8")
        fetch_path.write_text(_TRANSPORT, encoding="utf-8")
        files["scripts/ci/cla_eligibility_fetch.py"] = base64.b64encode(
            _TRANSPORT.encode()
        ).decode()
        (self.trusted / ".github").mkdir()
        (self.trusted / ".github/cla-policy.json").write_text(json.dumps(policy), encoding="utf-8")
        files[".github/cla-policy.json"] = base64.b64encode(json.dumps(policy).encode()).decode()
        material = {k: policy[k] for k in ("agreements", "exemptions")}
        raw = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        group = event == "merge_group"
        answer = {
            "protocol": "cla-eligibility-v1",
            "kind": "merge_group" if group else "pr",
            "repository_id": REPO_ID,
            "head_sha": HEAD,
            "base_sha": BASE,
            "policy_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "contributors": [{"id": EXTERNAL, "login": "someone"}],
            "revision": 1,
            "eligible": True,
        }
        answer["members" if group else "pr"] = [{"pr": PR, "headSha": "c" * 40}] if group else PR
        doc = {
            "trusted_sha": TRUSTED_SHA,
            "base": BASE,
            "head": HEAD,
            "pr": PR,
            "origin": ORIGIN,
            "files": files,
            "answer": answer,
            **state,
        }
        if group:
            member_commits = copy.deepcopy(state.get("member_commits", state.get("commits", [])))
            if member_commits:
                member_commits[-1]["sha"] = "c" * 40
            author = (
                state.get("group_author")
                or (state.get("commits") or [{}])[0].get("author")
                or {"login": "someone", "id": EXTERNAL}
            )
            pr = {
                "number": PR,
                "state": "open",
                "head": {"sha": "c" * 40},
                "base": {"sha": BASE, "ref": "main", "repo": {"id": REPO_ID}},
                "commits": len(member_commits),
                "user": author,
            }
            doc.setdefault(
                "group_prs",
                [
                    {
                        "before": pr,
                        "after": copy.deepcopy(pr),
                        "commits": member_commits,
                        "total_commits": len(member_commits),
                        "compare_base": BASE,
                    }
                ],
            )
            nodes = [
                {
                    "id": f"entry-{i}",
                    "position": i + 1,
                    "baseCommit": {"oid": BASE},
                    "headCommit": {"oid": HEAD if i == len(doc["group_prs"]) - 1 else "e" * 40},
                    "pullRequest": {
                        "number": p["before"]["number"],
                        "headRefOid": p["before"]["head"]["sha"],
                        "baseRefName": "main",
                    },
                }
                for i, p in enumerate(doc["group_prs"])
            ]
            queue = [
                {
                    "data": {
                        "repository": {
                            "databaseId": REPO_ID,
                            "mergeQueue": {
                                "entries": {
                                    "totalCount": len(nodes),
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": nodes,
                                }
                            },
                        }
                    }
                }
            ]
            doc.setdefault("queue_before", queue)
            doc.setdefault("queue_after", copy.deepcopy(queue))
        self.state = self.work / "state.json"
        self.state.write_text(json.dumps(doc), encoding="utf-8")
        self.summary = self.work / "summary.md"
        self.github_env = self.work / "github-env"
        self.github_env.write_text("", encoding="utf-8")
        self.log = self.work / "gh.log"
        self.http_log = self.work / "http.log"
        self.log.write_text("", encoding="utf-8")
        self.env = {
            "PATH": f"{self.work / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": str(self.work / "tmp"),
            "GITHUB_ENV": str(self.github_env),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GH_TOKEN": "github-must-not-reach-authority",
            "GITHUB_TOKEN": "github-must-not-reach-authority",
            "ACTIONS_RUNTIME_TOKEN": "runtime-must-not-reach-authority",
            "REPO": "o/r",
            "REPOSITORY_ID": str(REPO_ID),
            "TRUSTED": str(self.trusted),
            "REF": "main",
            "CLA_SERVICE_MODE": "yes",
            "FAKE_GH_STATE": str(self.state),
            "FAKE_GH_LOG": str(self.log),
            "FAKE_HTTP_LOG": str(self.http_log),
            "PR_AUTHOR": "someone",
            "CLA_PR_AUTHOR_ID": str(EXTERNAL),
            "PR_NUMBER": str(PR),
            "PR_HEAD": HEAD,
            "GROUP_BASE": BASE,
            "GROUP_HEAD": HEAD,
        }

    def _bash(self, script: str, **env):
        return subprocess.run(
            [shutil.which("bash"), "-c", script],
            env={**self.env, **env},
            cwd=self.work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def step(self, name):
        proc = self._bash(_step_script(name))
        for ln in self.github_env.read_text(encoding="utf-8").splitlines():
            key, value = ln.split("=", 1)
            self.env[key] = value
        return proc

    def collect(self):
        return self.step("收集合并组候选的提交（只读元数据）")

    def judge(self, commits=None, *, nonce=None, **env):
        if self.event == "pull_request":
            (self.work / "tmp/pr-commits.json").write_text(json.dumps(commits), encoding="utf-8")
            self.env["CLA_EXPECTED_COMMITS"] = str(len(commits))
        script = _step_script("判定").replace("${{ github.event_name }}", self.event)
        assert "${{" not in script
        if nonce is not None:
            # 只固定随机数的值，fetch/gate 参数绑定仍跑工作流原文。
            target = "not_before=$(date +%s)"
            assert script.count(target) == 1
            script = script.replace(target, f"nonce={nonce}\n{target}")
        proc = self._bash(script, **env)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        assert len(lines) == 1, f"判定没有恰好输出一行 JSON：{proc.stdout}\n{proc.stderr}"
        return proc.returncode, json.loads(lines[0]), proc

    def requests(self):
        if not self.http_log.exists():
            return []
        return [json.loads(ln) for ln in self.http_log.read_text(encoding="utf-8").splitlines()]


HOLDER_COMMIT = [_commit(HEAD, "erwanjun", HOLDER)]
EXTERNAL_COMMIT = [_commit(HEAD, "someone", EXTERNAL)]


def test_exempt_pr_passes_without_any_authority_or_check_read(tmp_path):
    rig = Rig(tmp_path, "pull_request", fault="down")
    rc, verdict, _ = rig.judge(HOLDER_COMMIT, PR_AUTHOR="erwanjun", CLA_PR_AUTHOR_ID=str(HOLDER))
    assert rc == 0 and verdict["reason"] == "all_contributors_exempt_by_id"
    assert rig.requests() == [] and rig.log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
@pytest.mark.parametrize(
    "fault,requests", [(None, 1), ("recover", 2), ("down", 3), ("fetch_crash", 1)]
)
def test_live_answer_or_fetch_error_always_reaches_gate(tmp_path, event, fault, requests):
    rig = Rig(tmp_path, event, commits=EXTERNAL_COMMIT, fault=fault)
    if event == "merge_group":
        assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge(EXTERNAL_COMMIT)
    assert (rc, verdict["status"]) == (
        (1, "failure") if fault in ("down", "fetch_crash") else (0, "success")
    )
    assert len(rig.requests()) == requests
    assert rig.summary.read_text(encoding="utf-8").count("CLA check") == 1
    if fault in ("down", "fetch_crash"):
        assert any("provider_eligibility_unavailable:" in p for p in verdict["problems"])


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
@pytest.mark.parametrize(
    "override",
    [
        {"nonce": "old-nonce"},
        {"observed_at": 1},
        {"head_sha": "c" * 40},
        {"repository_id": REPO_ID + 1},
        {"contributors": [{"login": "someone", "id": HOLDER}]},
        {"policy_sha256": "f" * 64},
        {"eligible": False},
        {"extra": "field"},
    ],
)
def test_mismatched_or_ineligible_answer_never_passes(tmp_path, event, override):
    rig = Rig(tmp_path, event, commits=EXTERNAL_COMMIT, answer_override=override)
    if event == "merge_group":
        assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge(EXTERNAL_COMMIT)
    assert rc == 1 and verdict["status"] == "failure"
    assert len(rig.requests()) == 1


def test_group_base_is_bound_to_this_candidate(tmp_path):
    rig = Rig(
        tmp_path, "merge_group", commits=EXTERNAL_COMMIT, answer_override={"base_sha": "c" * 40}
    )
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 1 and "provider_eligibility_invalid" in verdict["problems"]


def test_each_judgement_generates_a_new_nonce(tmp_path):
    rig = Rig(tmp_path, "pull_request")
    assert rig.judge(EXTERNAL_COMMIT)[0] == 0
    assert rig.judge(EXTERNAL_COMMIT)[0] == 0
    from urllib.parse import parse_qs, urlsplit

    nonces = [parse_qs(urlsplit(r["url"]).query)["nonce"][0] for r in rig.requests()]
    assert len(set(nonces)) == 2 and all(re.fullmatch(r"[A-Za-z0-9_-]{32,64}", n) for n in nonces)


def test_nonce_starting_with_a_hyphen_survives_both_argument_parsers(tmp_path):
    rig = Rig(tmp_path, "pull_request")
    assert rig.judge(EXTERNAL_COMMIT, nonce="-" + "a" * 42)[0] == 0
    assert "nonce=-" in rig.requests()[0]["url"]


def test_config_error_fails_before_fetch(tmp_path):
    broken = _policy()
    del broken["exemptions"][0]["github_id"]
    rig = Rig(tmp_path, "pull_request", policy=broken)
    rc, verdict, _ = rig.judge(EXTERNAL_COMMIT)
    assert rc == 2 and verdict["reason"] == "config_error"
    assert rig.requests() == []


def test_unresolved_pr_identity_fails_before_fetch(tmp_path):
    rig = Rig(tmp_path, "pull_request")
    rc, verdict, _ = rig.judge([_commit(HEAD, None, None)])
    assert rc == 1 and any(p.startswith("unresolved:") for p in verdict["problems"])
    assert rig.requests() == []


def test_all_exempt_merge_group_passes_locally(tmp_path):
    rig = Rig(tmp_path, "merge_group", commits=HOLDER_COMMIT)
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 0 and verdict["reason"] == "merge_candidate_exempt_by_id"
    assert rig.requests() == []


@pytest.mark.parametrize(
    "login,uid",
    [("erwanjun", HOLDER), ("web-flow", 19864447), ("github-actions[bot]", ACTIONS), (None, None)],
)
@pytest.mark.parametrize("exempt", [False, True])
def test_synthetic_queue_identity_is_not_an_original_pr_contributor(tmp_path, login, uid, exempt):
    # 真实沙盒 #2：合成 author=erwanjun，committer=web-flow；其余是防回归边界。
    candidate = _commit(HEAD, login, uid, "Merge pull request #42 from fork/patch")
    candidate["committer"] = {"login": "web-flow", "id": 19864447}
    original = HOLDER_COMMIT if exempt else EXTERNAL_COMMIT
    rig = Rig(
        tmp_path,
        "merge_group",
        commits=[candidate],
        member_commits=original,
        group_author=original[0]["author"],
    )
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 0 and verdict["status"] == "success"
    assert len(rig.requests()) == (0 if exempt else 1)
    if exempt:
        assert verdict["reason"] == "merge_candidate_exempt_by_id"


def test_group_with_multiple_authors_and_coauthor_checks_the_complete_list(tmp_path):
    commits = [
        _commit("d" * 40, "erwanjun", HOLDER),
        _commit(
            HEAD, "someone", EXTERNAL, "c\n\nCo-authored-by: Third <third@users.noreply.github.com>"
        ),
    ]
    rig = Rig(tmp_path, "merge_group", commits=commits)
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 1 and "provider_eligibility_invalid" in verdict["problems"]
    doc = json.loads(rig.state.read_text(encoding="utf-8"))
    doc["answer"]["contributors"] += [
        {"login": "erwanjun", "id": HOLDER},
        {"login": "third", "id": 999},
    ]
    rig.state.write_text(json.dumps(doc), encoding="utf-8")
    assert rig.judge()[0] == 0


def test_group_external_pr_author_cannot_hide_behind_exempt_commit_authors(tmp_path):
    rig = Rig(
        tmp_path,
        "merge_group",
        commits=HOLDER_COMMIT,
        group_author={"login": "someone", "id": EXTERNAL},
        answer_override={"contributors": [{"login": "erwanjun", "id": HOLDER}]},
    )
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 1 and "provider_eligibility_invalid" in verdict["problems"]
    assert len(rig.requests()) == 1
    state = json.loads(rig.state.read_text(encoding="utf-8"))
    state["answer_override"]["contributors"].append({"login": "someone", "id": EXTERNAL})
    rig.state.write_text(json.dumps(state), encoding="utf-8")
    assert rig.judge()[0] == 0


@pytest.mark.parametrize("kind", ["commit_author", "co_author"])
def test_original_pr_unresolved_identity_cannot_disappear_from_the_candidate(tmp_path, kind):
    original = (
        [_commit("c" * 40, None, None)]
        if kind == "commit_author"
        else [_commit("c" * 40, "erwanjun", HOLDER, "c\n\nCo-authored-by: U <u@example.com>")]
    )
    rig = Rig(tmp_path, "merge_group", commits=HOLDER_COMMIT, member_commits=original)
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 1 and verdict["reason"] == "unresolved_contributors"
    assert rig.requests() == []


def test_original_pr_external_commit_author_is_checked_even_when_candidate_is_exempt(tmp_path):
    rig = Rig(
        tmp_path,
        "merge_group",
        commits=HOLDER_COMMIT,
        member_commits=[_commit("c" * 40, "someone", EXTERNAL)],
        answer_override={"contributors": [{"login": "erwanjun", "id": HOLDER}]},
    )
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 1 and "provider_eligibility_invalid" in verdict["problems"]
    assert len(rig.requests()) == 1
    state = json.loads(rig.state.read_text(encoding="utf-8"))
    state["answer_override"]["contributors"].append({"login": "someone", "id": EXTERNAL})
    rig.state.write_text(json.dumps(state), encoding="utf-8")
    assert rig.judge()[0] == 0


@pytest.mark.parametrize("kind", ["commit_author", "co_author"])
def test_group_unresolved_identity_is_rejected_before_authority(tmp_path, kind):
    commits = (
        [_commit(HEAD, None, None)]
        if kind == "commit_author"
        else [_commit(HEAD, "erwanjun", HOLDER, "c\n\nCo-authored-by: U <u@example.com>")]
    )
    rig = Rig(tmp_path, "merge_group", commits=commits)
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge()
    assert rc == 1 and verdict["reason"] == "unresolved_contributors"
    assert rig.requests() == []


@pytest.mark.parametrize(
    "fault",
    [
        "truncated",
        "queue_gap",
        "queue_changed",
        "missing_author_id",
        "missing_pr_commits",
        "graphql_error",
    ],
)
def test_group_bad_independent_github_snapshot_never_reaches_authority(tmp_path, fault):
    rig = Rig(tmp_path, "merge_group", commits=HOLDER_COMMIT)
    state = json.loads(rig.state.read_text(encoding="utf-8"))
    if fault == "missing_author_id":
        del state["group_prs"][0]["before"]["user"]["id"]
    elif fault == "missing_pr_commits":
        state["group_prs"][0]["commits"] = []
    elif fault == "queue_changed":
        state["queue_after"][0]["data"]["repository"]["mergeQueue"]["entries"]["nodes"][0][
            "pullRequest"
        ]["headRefOid"] = "e" * 40
    elif fault == "graphql_error":
        state["queue_before"][0]["errors"] = [{"message": "permission denied"}]
    else:
        for pages in (state["queue_before"], state["queue_after"]):
            entries = pages[0]["data"]["repository"]["mergeQueue"]["entries"]
            if fault == "truncated":
                entries["totalCount"] = 2
            else:
                entries["nodes"][0]["position"] = 2
    rig.state.write_text(json.dumps(state), encoding="utf-8")
    proc = rig.collect()
    if proc.returncode == 0:
        assert rig.judge()[0] == 2
    assert rig.requests() == []


def test_group_two_pages_two_pr_authors_and_exact_service_members(tmp_path):
    rig = Rig(tmp_path, "merge_group", commits=HOLDER_COMMIT)
    state = json.loads(rig.state.read_text(encoding="utf-8"))
    second = copy.deepcopy(state["group_prs"][0])
    for key in ("before", "after"):
        second[key]["number"] = PR + 1
        second[key]["head"]["sha"] = "f" * 40
        second[key]["user"] = {"login": "someone", "id": EXTERNAL}
    second["commits"][0]["sha"] = "f" * 40
    state["group_prs"].append(second)
    page1 = state["queue_before"][0]
    connection = page1["data"]["repository"]["mergeQueue"]["entries"]
    connection["totalCount"] = 2
    connection["pageInfo"] = {"hasNextPage": True, "endCursor": "cursor-one"}
    page2 = copy.deepcopy(page1)
    connection["nodes"][0]["headCommit"]["oid"] = "e" * 40
    entry2 = page2["data"]["repository"]["mergeQueue"]["entries"]
    entry2["pageInfo"] = {"hasNextPage": False, "endCursor": "cursor-two"}
    entry2["nodes"][0].update(
        {
            "id": "entry-two",
            "position": 2,
            "pullRequest": {"number": PR + 1, "headRefOid": "f" * 40, "baseRefName": "main"},
        }
    )
    state["queue_before"] = [page1, page2]
    state["queue_after"] = copy.deepcopy(state["queue_before"])
    state["answer_override"] = {
        "members": [{"pr": PR, "headSha": "c" * 40}, {"pr": PR + 1, "headSha": "f" * 40}],
        "contributors": [{"login": "erwanjun", "id": HOLDER}, {"login": "someone", "id": EXTERNAL}],
    }
    rig.state.write_text(json.dumps(state), encoding="utf-8")
    proc = rig.collect()
    assert proc.returncode == 0, proc.stderr
    assert rig.judge()[0] == 0
    assert len(rig.requests()) == 1  # 第二个 PR 的外部发起人禁止本地豁免
    state["answer_override"]["members"].reverse()
    rig.state.write_text(json.dumps(state), encoding="utf-8")
    rc, verdict, _ = rig.judge()
    assert rc == 1 and "provider_eligibility_invalid" in verdict["problems"]


@pytest.mark.parametrize(
    "state",
    [
        {"commits": HOLDER_COMMIT, "total": 2},
        {"commits": HOLDER_COMMIT * 2},
        {"commits": [_commit("c" * 40, "erwanjun", HOLDER)]},
        {"commits": [], "total": 0},
    ],
)
def test_incomplete_group_enumeration_refuses(tmp_path, state):
    rig = Rig(tmp_path, "merge_group", **state)
    assert rig.collect().returncode == 1


@pytest.mark.parametrize("service", [True, False])
def test_trusted_inputs_are_one_default_branch_revision(tmp_path, service):
    rig = Rig(tmp_path, "pull_request", policy=_policy(service=service))
    proc = rig.step("取默认分支上的可信判定输入")
    assert proc.returncode == 0, proc.stderr
    assert rig.env["CLA_SERVICE_MODE"] == ("yes" if service else "no")
    calls = rig.log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "repos/o/r/commits/main"
    assert all(f"?ref={TRUSTED_SHA}" in p for p in calls[1:])
    assert any("cla_eligibility_fetch.py" in p for p in calls) is service


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
def test_repository_policy_stays_disabled(tmp_path, event):
    rig = Rig(tmp_path, event, policy=_policy(service=False), commits=HOLDER_COMMIT)
    assert rig.step("取默认分支上的可信判定输入").returncode == 0
    assert rig.env["CLA_SERVICE_MODE"] == "no"
    rig.log.write_text("", encoding="utf-8")
    assert rig.collect().returncode == 0
    rc, verdict, _ = rig.judge(HOLDER_COMMIT, PR_AUTHOR="erwanjun", CLA_PR_AUTHOR_ID=str(HOLDER))
    assert rc == 0 and verdict["status"] == (
        "success" if event == "pull_request" else "not_applicable"
    )
    assert rig.requests() == [] and rig.log.read_text(encoding="utf-8") == ""
    assert not (rig.work / "tmp/group-commits.json").exists()


def test_fetch_budget_fits_inside_job_timeout_and_uses_only_trusted_source():
    job = _contract._job(_contract.CI, _contract.CLA_JOB)
    timeout = int(re.search(r"(?m)^    timeout-minutes: (\d+)$", job).group(1))
    budget = int(re.search(r"--budget-seconds (\d+)", job).group(1))
    assert budget == 120 and timeout == 10 and budget + 20 < timeout * 60
    script = _step_script("判定")
    assert 'python3 -I "$TRUSTED/scripts/ci/cla_eligibility_fetch.py"' in script
    assert "--provider-checks-json" not in script and "check-runs" not in script
    block = job[job.index("- name: 判定") :].split("run: |", 1)[0]
    assert "GH_TOKEN:" not in block and "github.token" not in block
