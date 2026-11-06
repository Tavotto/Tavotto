"""服务实时判定（ADR 0172）在 CI 这一侧的看护：回答逐字段核对，问不到一律不算过。

`cla_gate.py` 只做离线判定，`cla_eligibility_fetch.py` 只负责把问题问对。这里钉住的
三件事坏了都不会让别的测试红：不可达时必须红；回答必须绑定这次的 PR、head、作者和
nonce，错配与重放都不算过；客户端不带凭据、不跟随跳转、地址只取默认分支的 policy。
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOLDER, EXTERNAL, REPO, PR, NOW = 88193520, 261183172, 1234, 42, 1_793_900_000
HEAD, BASE = "a" * 40, "b" * 40
NONCE = "n" * 48
ORIGIN = "https://cla.example.com"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"cla_eligibility_{name}", ROOT / "scripts" / "ci" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load("cla_gate")


@pytest.fixture(scope="module")
def fetcher():
    return _load("cla_eligibility_fetch")


@pytest.fixture
def policy():
    p = json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
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
    ids = {"erwanjun": HOLDER, "dependabot[bot]": 49699333, "github-actions[bot]": 41898282}
    for ex in p["exemptions"]:
        ex["github_id"] = ids[ex["login"]]
    return p


def _answer(gate, policy, *, group=False, **over) -> dict:
    answer = {
        "protocol": "cla-eligibility-v1",
        "repository_id": REPO,
        "kind": "merge_group" if group else "pr",
        "head_sha": HEAD,
        "base_sha": BASE,
        "policy_sha256": gate._policy_digest(policy),
        "contributors": [{"id": EXTERNAL, "login": "someone"}],
        "revision": 3,
        "observed_at": NOW,
        "nonce": NONCE,
        "eligible": True,
    }
    answer.update({"members": [{"pr": PR, "head_sha": "c" * 40}]} if group else {"pr": PR})
    answer.update(over)
    return answer


def _context(answer, *, group=False, **over) -> dict:
    context = {
        "kind": "merge_group" if group else "pull_request",
        "pr": None if group else PR,
        "head_sha": HEAD,
        "base_sha": BASE if group else None,
        "repository_id": REPO,
        "not_before": NOW - 10,
        "now": NOW + 1,
        "nonce": NONCE,
        "eligibility": answer,
        "contributors": [
            {"login": "someone", "sources": ["pr_author"], "ids": [EXTERNAL], "details": []}
        ],
        "group_members": [{"pr": PR, "headSha": "c" * 40}] if group else None,
    }
    context.update(over)
    return context


def _verdict(gate, policy, answer, **over):
    return gate.signed_logins(None, policy, _context(answer, **over))


@pytest.mark.parametrize("group", [False, True])
def test_a_matching_eligible_answer_passes(gate, policy, group):
    answer = _answer(gate, policy, group=group)
    assert _verdict(gate, policy, answer, group=group) == ({"*"}, None)


def test_not_eligible_is_not_signed(gate, policy):
    signed, why = _verdict(gate, policy, _answer(gate, policy, eligible=False))
    assert signed == set() and why == "provider_not_eligible"


@pytest.mark.parametrize(
    "over",
    [
        {"pr": PR + 1},  # 别的 PR
        {"pr": str(PR)},  # 类型不同
        {"head_sha": "d" * 40},  # 别的提交
        {"repository_id": 1},  # 别的仓库
        {"kind": "merge_group"},  # 拿合并组的回答冒充 PR
        {"nonce": "m" * 48},  # 重放：别的校验要来的回答
        {"policy_sha256": "0" * 64},  # 别的策略
        {"protocol": "tavotto-cla-v1"},
        {"observed_at": NOW - 16},  # 早于这次校验
        {"observed_at": NOW + 7},  # 来自未来
        {"observed_at": float(NOW)},
        {"revision": -1},
        {"revision": True},
        {"eligible": "true"},  # 只有布尔 True 才算过
        {"eligible": 1},
        {"base_sha": "short"},
        {"contributors": []},
        {"contributors": [{"id": 5, "login": "another"}]},  # 回答说的不是这个作者
        {"contributors": [{"id": EXTERNAL + 1, "login": "someone"}]},  # 同名不同人
        {"contributors": [{"id": 0, "login": "someone"}]},
        {"contributors": [{"id": EXTERNAL, "login": "someone", "signed": True}]},
    ],
)
def test_mismatch_or_replay_never_passes(gate, policy, over):
    signed, why = _verdict(gate, policy, _answer(gate, policy, **over))
    assert signed == set() and why == "provider_eligibility_invalid"


def test_extra_or_missing_fields_never_pass(gate, policy):
    answer = _answer(gate, policy, extra=1)
    assert _verdict(gate, policy, answer) == (set(), "provider_eligibility_invalid")
    answer = _answer(gate, policy)
    del answer["revision"]
    assert _verdict(gate, policy, answer) == (set(), "provider_eligibility_invalid")


def test_group_answer_must_bind_its_base_and_members(gate, policy):
    answer = _answer(gate, policy, group=True, base_sha="d" * 40)
    assert _verdict(gate, policy, answer, group=True)[0] == set()
    answer = _answer(gate, policy, group=True, members=[])
    assert _verdict(gate, policy, answer, group=True)[0] == set()


def test_a_pr_answer_cannot_pass_a_merge_group(gate, policy):
    assert _verdict(gate, policy, _answer(gate, policy), group=True)[0] == set()


def test_a_contributor_the_gate_saw_must_be_in_the_answer(gate, policy):
    people = [
        {"login": "someone", "sources": ["pr_author"], "ids": [EXTERNAL]},
        {"login": "co-author", "sources": ["co_author"], "ids": []},
    ]
    signed, why = _verdict(gate, policy, _answer(gate, policy), contributors=people)
    assert signed == set() and why == "provider_eligibility_invalid"


def test_swapped_ids_between_two_contributors_never_pass(gate, policy):
    """Codex 评审 #1794：只比两个集合时，两个人的 id 互换也能过。"""
    people = [
        {"login": "someone", "sources": ["pr_author"], "ids": [EXTERNAL]},
        {"login": "other", "sources": ["commit_author"], "ids": [7]},
    ]
    swapped = [{"id": 7, "login": "someone"}, {"id": EXTERNAL, "login": "other"}]
    answer = _answer(gate, policy, contributors=swapped)
    assert _verdict(gate, policy, answer, contributors=people) == (
        set(),
        "provider_eligibility_invalid",
    )
    right = [{"id": EXTERNAL, "login": "someone"}, {"id": 7, "login": "other"}]
    answer = _answer(gate, policy, contributors=right)
    assert _verdict(gate, policy, answer, contributors=people) == ({"*"}, None)


def test_a_login_listed_twice_never_passes(gate, policy):
    twice = [{"id": EXTERNAL, "login": "someone"}, {"id": 7, "login": "SomeOne"}]
    for people in (twice, twice[::-1]):  # 后写的那条 id 正确时，也不能靠覆盖过关
        answer = _answer(gate, policy, contributors=people)
        assert _verdict(gate, policy, answer) == (set(), "provider_eligibility_invalid")


def test_login_case_does_not_matter(gate, policy):
    answer = _answer(gate, policy, contributors=[{"id": EXTERNAL, "login": "SomeOne"}])
    assert _verdict(gate, policy, answer) == ({"*"}, None)


@pytest.mark.parametrize("code", ["authority_unreachable", "verification_busy", "http_503"])
def test_an_unreachable_service_never_passes(gate, policy, code):
    signed, why = _verdict(gate, policy, {"error": code})
    assert signed == set() and why.startswith("provider_eligibility_unavailable:")


def test_a_hostile_error_code_is_not_echoed(gate, policy):
    _, why = _verdict(gate, policy, {"error": "<script>"})
    assert why == "provider_eligibility_unavailable:unknown"


def test_missing_context_never_passes(gate, policy):
    answer = _answer(gate, policy)
    assert _verdict(gate, policy, answer, nonce=None) == (
        set(),
        "provider_verification_context_missing",
    )


def test_a_receipt_is_ignored_outside_service_mode(gate):
    p = json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
    assert gate.signed_logins(None, p, {"eligibility": {"eligible": True}}) == (
        set(),
        "provider_not_configured",
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://cla.example.com",
        "https://cla.example.com/",
        "https://u@cla.example.com",
        "https://cla.example.com:8443",
        "https://CLA.example.com",
        "",
    ],
)
def test_authority_origin_must_be_a_bare_https_origin(gate, policy, origin):
    policy["authority_origin"] = origin
    with pytest.raises(gate.ConfigError, match="authority_origin"):
        gate.validate_policy(policy)


def test_service_mode_requires_the_authority_origin(gate, policy):
    del policy["authority_origin"]
    with pytest.raises(gate.ConfigError, match="authority_origin"):
        gate.validate_policy(policy)


def _commit(login: str, uid: int) -> dict:
    return {
        "sha": HEAD,
        "author": {"login": login, "id": uid},
        "commit": {"message": "c", "author": {"name": "n", "email": "x@example.com"}},
    }


def _group_snapshot(*, author="someone", uid=EXTERNAL, commits=None):
    pr_head = "c" * 40
    items = commits if commits is not None else [{**_commit("someone", EXTERNAL), "sha": pr_head}]
    pr = {
        "number": PR,
        "state": "open",
        "head": {"sha": pr_head},
        "base": {"sha": BASE, "ref": "main", "repo": {"id": REPO}},
        "commits": len(items),
        "user": {"login": author, "id": uid},
    }
    queue = [
        {
            "data": {
                "repository": {
                    "databaseId": REPO,
                    "mergeQueue": {
                        "entries": {
                            "totalCount": 1,
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "entry-one",
                                    "position": 1,
                                    "baseCommit": {"oid": BASE},
                                    "headCommit": {"oid": HEAD},
                                    "pullRequest": {
                                        "number": PR,
                                        "headRefOid": pr_head,
                                        "baseRefName": "main",
                                    },
                                }
                            ],
                        }
                    },
                }
            }
        }
    ]
    return {
        "repository_id": REPO,
        "base_sha": BASE,
        "head_sha": HEAD,
        "queue_before": queue,
        "queue_after": copy.deepcopy(queue),
        "prs": [
            {
                "before": pr,
                "after": copy.deepcopy(pr),
                "commits": items,
                "compare_base": BASE,
                "total_commits": len(items),
            }
        ],
    }


def _cli(
    gate,
    tmp_path,
    policy,
    doc,
    *,
    event="pull_request",
    now=NOW,
    group_snapshot=None,
    include_group_snapshot=True,
    commits_override=None,
):
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(policy), encoding="utf-8")
    commits = tmp_path / "commits.json"
    commits.write_text(
        json.dumps(
            commits_override if commits_override is not None else [_commit("someone", EXTERNAL)]
        ),
        encoding="utf-8",
    )
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(doc), encoding="utf-8")
    argv = ["--event", event, "--policy", str(pol), "--repo-root", str(ROOT),
            "--commits-json", str(commits), "--expected-commits", "1",
            "--eligibility-json", str(answer), "--eligibility-nonce", NONCE,
            "--verification-head", HEAD, "--verification-repository-id", str(REPO),
            "--verification-not-before", str(now - 10)]  # fmt: skip
    if event == "pull_request":
        argv += ["--verification-pr", str(PR), "--pr-author", "someone",
                 "--pr-author-id", str(EXTERNAL)]  # fmt: skip
    else:
        argv += ["--verification-base", BASE]
        if include_group_snapshot:
            snapshot = tmp_path / "group-snapshot.json"
            snapshot.write_text(
                json.dumps(_group_snapshot() if group_snapshot is None else group_snapshot),
                encoding="utf-8",
            )
            argv += ["--group-snapshot-json", str(snapshot)]
    return gate.main(argv)


@pytest.mark.parametrize(
    "event,group", [("pull_request", False), ("merge_group", True)], ids=["pr", "group"]
)
def test_cli_end_to_end(gate, policy, tmp_path, monkeypatch, event, group):
    monkeypatch.setattr(gate.time, "time", lambda: NOW)
    answer = _answer(gate, policy, group=group)
    assert _cli(gate, tmp_path, policy, answer, event=event) == 0
    assert _cli(gate, tmp_path, policy, {**answer, "eligible": False}, event=event) == 1
    assert _cli(gate, tmp_path, policy, {**answer, "nonce": "m" * 48}, event=event) == 1
    assert _cli(gate, tmp_path, policy, {"error": "authority_unreachable"}, event=event) == 1


def test_cli_not_eligible_tells_people_where_to_sign(gate, policy, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate.time, "time", lambda: NOW)
    _cli(gate, tmp_path, policy, _answer(gate, policy, eligible=False))
    assert ORIGIN in capsys.readouterr().out


@pytest.mark.parametrize("unresolved_kind", ["commit_author", "co_author"])
def test_group_unresolved_identity_cannot_be_overridden_by_eligible(gate, policy, unresolved_kind):
    context = _context(_answer(gate, policy, group=True), group=True)
    unresolved = [
        {"kind": unresolved_kind, "sha": HEAD[:8], "name": "unknown", "email": "u@example.com"}
    ]
    verdict = gate.decide("merge_group", policy, None, context["contributors"], unresolved, context)
    assert verdict["status"] == "failure" and verdict["reason"] == "unresolved_contributors"
    assert verdict["problems"][0].startswith("unresolved:")


def test_group_missing_snapshot_never_takes_exemption(gate, policy, tmp_path):
    holder = [_commit("erwanjun", HOLDER)]
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            {"eligible": True},
            event="merge_group",
            include_group_snapshot=False,
            commits_override=holder,
        )
        == 2
    )
    contributors, unresolved = gate.collect_contributors(None, holder)
    verdict = gate.decide("merge_group", policy, None, contributors, unresolved)
    assert verdict["status"] == "failure" and verdict["reason"] == "group_snapshot_required"


@pytest.mark.parametrize(
    "author",
    [
        {"login": "erwanjun", "id": HOLDER},
        {"login": "web-flow", "id": 19864447},
        {"login": "github-actions[bot]", "id": 41898282},
        None,
    ],
)
def test_group_contributors_come_from_original_prs_not_synthetic_metadata(gate, author):
    snapshot = _group_snapshot()
    candidate = {**_commit("erwanjun", HOLDER), "author": author}
    candidate["committer"] = {"login": "web-flow", "id": 19864447}
    context = {"repository_id": REPO, "base_sha": BASE, "head_sha": HEAD}
    people, unresolved, members = gate.collect_group_contributors(snapshot, context, [candidate])
    assert [(p["login"], p["ids"]) for p in people] == [("someone", [EXTERNAL])]
    assert unresolved == [] and members == [{"pr": PR, "headSha": "c" * 40}]


@pytest.mark.parametrize("shas", [[], ["d" * 40], [HEAD, HEAD]])
def test_synthetic_candidate_sha_binding_and_completeness_are_still_required(gate, shas):
    context = {"repository_id": REPO, "base_sha": BASE, "head_sha": HEAD}
    with pytest.raises(gate.ConfigError, match="候选提交"):
        gate.collect_group_contributors(
            _group_snapshot(), context, [{**_commit("erwanjun", HOLDER), "sha": s} for s in shas]
        )


def test_a_bot_in_original_pr_is_still_a_contributor(gate):
    original = {**_commit("web-flow", 19864447), "sha": "c" * 40}
    snapshot = _group_snapshot(commits=[original])
    context = {"repository_id": REPO, "base_sha": BASE, "head_sha": HEAD}
    people, unresolved, _ = gate.collect_group_contributors(
        snapshot, context, [_commit("erwanjun", HOLDER)]
    )
    assert [(p["login"], p["ids"]) for p in people] == [
        ("someone", [EXTERNAL]),
        ("web-flow", [19864447]),
    ]
    assert unresolved == []


@pytest.mark.parametrize("source", ["candidate", "original_pr"])
@pytest.mark.parametrize("message", [None, 1, False, [], {}])
def test_group_rejects_nonstring_commit_messages(gate, policy, tmp_path, source, message):
    candidate = [_commit("erwanjun", HOLDER)]
    snapshot = _group_snapshot(
        author="erwanjun", uid=HOLDER, commits=[{**candidate[0], "sha": "c" * 40}]
    )
    candidate = copy.deepcopy(candidate)
    item = candidate[0] if source == "candidate" else snapshot["prs"][0]["commits"][0]
    item["commit"]["message"] = message
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            {},
            event="merge_group",
            group_snapshot=snapshot,
            commits_override=candidate,
        )
        == 2
    )


@pytest.mark.parametrize("source", ["candidate", "original_pr"])
@pytest.mark.parametrize(
    "path",
    [
        ("commit", "message"),
        ("commit",),
        ("commit", "author"),
        ("commit", "author", "email"),
        ("author",),
        ("author", "login"),
        ("author", "id"),
    ],
)
def test_group_rejects_missing_commit_identity_content(gate, policy, tmp_path, source, path):
    candidate = [_commit("erwanjun", HOLDER)]
    snapshot = _group_snapshot(
        author="erwanjun", uid=HOLDER, commits=[{**candidate[0], "sha": "c" * 40}]
    )
    candidate = copy.deepcopy(candidate)
    item = candidate[0] if source == "candidate" else snapshot["prs"][0]["commits"][0]
    for key in path[:-1]:
        item = item[key]
    del item[path[-1]]
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            {},
            event="merge_group",
            group_snapshot=snapshot,
            commits_override=candidate,
        )
        == 2
    )


@pytest.mark.parametrize("source", ["candidate", "original_pr"])
def test_group_accepts_an_explicit_empty_commit_message(gate, policy, tmp_path, source):
    candidate = [_commit("erwanjun", HOLDER)]
    snapshot = _group_snapshot(
        author="erwanjun", uid=HOLDER, commits=[{**candidate[0], "sha": "c" * 40}]
    )
    candidate = copy.deepcopy(candidate)
    item = candidate[0] if source == "candidate" else snapshot["prs"][0]["commits"][0]
    item["commit"]["message"] = ""
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            {},
            event="merge_group",
            group_snapshot=snapshot,
            commits_override=candidate,
        )
        == 0
    )


def test_missing_original_pr_message_cannot_hide_its_only_coauthor(gate, policy, tmp_path, capsys):
    candidate = [_commit("erwanjun", HOLDER)]
    original = {**_commit("erwanjun", HOLDER), "sha": "c" * 40}
    original["commit"]["message"] = "c\n\nCo-authored-by: Unknown <u@example.com>"
    snapshot = _group_snapshot(author="erwanjun", uid=HOLDER, commits=[original])
    kwargs = dict(event="merge_group", group_snapshot=snapshot, commits_override=candidate)
    # The unresolved coauthor exists only in the original PR, not in the synthetic candidate.
    assert _cli(gate, tmp_path, policy, {"eligible": True}, **kwargs) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "unresolved_contributors"
    del original["commit"]["message"]
    assert _cli(gate, tmp_path, policy, {"eligible": True}, **kwargs) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "config_error"


def test_external_group_pr_author_is_checked_before_local_exemption(
    gate, policy, tmp_path, monkeypatch
):
    monkeypatch.setattr(gate.time, "time", lambda: NOW)
    holder = [_commit("erwanjun", HOLDER)]
    snapshot = _group_snapshot(commits=[{**holder[0], "sha": "c" * 40}])
    # 所有提交作者豁免，PR 发起人没有豁免。没有服务回答时不得本地放行。
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            {"error": "authority_unreachable"},
            event="merge_group",
            group_snapshot=snapshot,
            commits_override=holder,
        )
        == 1
    )
    answer = _answer(
        gate,
        policy,
        group=True,
        contributors=[{"id": HOLDER, "login": "erwanjun"}, {"id": EXTERNAL, "login": "someone"}],
    )
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            answer,
            event="merge_group",
            group_snapshot=snapshot,
            commits_override=holder,
        )
        == 0
    )
    answer["contributors"] = [{"id": HOLDER, "login": "erwanjun"}]
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            answer,
            event="merge_group",
            group_snapshot=snapshot,
            commits_override=holder,
        )
        == 1
    )


@pytest.mark.parametrize(
    "members",
    [
        [],
        [{"pr": PR + 1, "head_sha": "c" * 40}],
        [{"pr": PR, "head_sha": "d" * 40}],
        [{"pr": str(PR), "head_sha": "c" * 40}],
        [{"pr": True, "head_sha": "c" * 40}],
        [{"pr": PR, "head_sha": "c" * 40, "extra": 1}],
        [{"pr": PR, "head_sha": "c" * 40}] * 2,
        [{"pr": PR, "head_sha": "c" * 40}, {"pr": 43, "head_sha": "d" * 40}],
        [{"pr": PR, "headSha": "c" * 40}],  # GitHub 内部别名不得作为服务协议接受。
        [{"pr": PR, "head_sha": "c" * 40, "headSha": "c" * 40}],
        [{"pr": PR}],
        [{"head_sha": "c" * 40}],
    ],
)
def test_group_service_members_must_exactly_match_independent_prefix(gate, policy, members):
    answer = _answer(gate, policy, group=True, members=members)
    assert _verdict(gate, policy, answer, group=True) == (set(), "provider_eligibility_invalid")


def test_service_members_follow_pr_order_while_snapshot_keeps_queue_order(gate, policy):
    members = [{"pr": PR, "head_sha": "c" * 40}, {"pr": PR + 1, "head_sha": "d" * 40}]
    queue = [{"pr": PR + 1, "headSha": "d" * 40}, {"pr": PR, "headSha": "c" * 40}]
    original_queue = copy.deepcopy(queue)
    answer = _answer(gate, policy, group=True, members=members)
    assert _verdict(gate, policy, answer, group=True, group_members=queue) == ({"*"}, None)
    assert queue == original_queue  # 协议规范化不能改写独立枚举的队列顺序。
    answer["members"] = members[::-1]
    assert _verdict(gate, policy, answer, group=True, group_members=queue) == (
        set(),
        "provider_eligibility_invalid",
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("repository_id",), REPO + 1),
        (("base_sha",), "d" * 40),
        (("head_sha",), "d" * 40),
        (("queue_before",), []),
        (("prs",), []),
        (("queue_before", 0, "data", "repository", "databaseId"), REPO + 1),
        (("queue_before", 0, "data", "repository", "mergeQueue", "entries", "totalCount"), 2),
        (
            (
                "queue_before",
                0,
                "data",
                "repository",
                "mergeQueue",
                "entries",
                "pageInfo",
                "hasNextPage",
            ),
            True,
        ),
        (
            (
                "queue_after",
                0,
                "data",
                "repository",
                "mergeQueue",
                "entries",
                "nodes",
                0,
                "pullRequest",
                "headRefOid",
            ),
            "d" * 40,
        ),
        (("prs", 0, "before", "head", "sha"), "d" * 40),
        (("prs", 0, "after", "user", "id"), EXTERNAL + 1),
        (("prs", 0, "before", "user", "id"), None),
        (("prs", 0, "commits"), []),
        (("prs", 0, "total_commits"), 2),
        (("prs", 0, "compare_base"), "d" * 40),
    ],
)
def test_group_incomplete_or_changed_github_input_never_passes(gate, policy, tmp_path, path, value):
    snapshot = _group_snapshot()
    target = snapshot
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert (
        _cli(
            gate,
            tmp_path,
            policy,
            _answer(gate, policy, group=True),
            event="merge_group",
            group_snapshot=snapshot,
        )
        == 2
    )


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Opener:
    """按顺序回放：bytes = 200，int + bytes = HTTP 错误，Exception = 抛出。"""

    def __init__(self, *script):
        self.script, self.requests = list(script), []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, tuple):
            status, body = step
            raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(body))
        return _Response(step)


def _fetch(fetcher, *script, budget=120):
    opener, slept, clock = _Opener(*script), [], [0.0]

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    url = fetcher.build_url(ORIGIN, "pull_request", HEAD, NONCE, pr=PR)
    result = fetcher.fetch(url, opener, budget_seconds=budget, sleep=sleep, clock=lambda: clock[0])
    return result, opener, slept


def test_urls_carry_only_locators(fetcher):
    assert fetcher.build_url(ORIGIN, "pull_request", HEAD, NONCE, pr=PR) == (
        f"{ORIGIN}/eligibility/pr?pr={PR}&head={HEAD}&nonce={NONCE}"
    )
    assert fetcher.build_url(ORIGIN, "merge_group", HEAD, NONCE, base=BASE) == (
        f"{ORIGIN}/eligibility/group?base={BASE}&head={HEAD}&nonce={NONCE}"
    )


@pytest.mark.parametrize(
    "origin,event,head,nonce,pr,base",
    [
        ("http://cla.example.com", "pull_request", HEAD, NONCE, PR, None),
        ("https://evil.example.com/x", "pull_request", HEAD, NONCE, PR, None),
        (ORIGIN, "pull_request", "zz", NONCE, PR, None),
        (ORIGIN, "pull_request", HEAD, "short", PR, None),
        (ORIGIN, "pull_request", HEAD, NONCE, None, None),
        (ORIGIN, "merge_group", HEAD, NONCE, None, "zz"),
        (ORIGIN, "push", HEAD, NONCE, PR, None),
    ],
)
def test_bad_locators_never_build_a_url(fetcher, origin, event, head, nonce, pr, base):
    with pytest.raises(ValueError):
        fetcher.build_url(origin, event, head, nonce, pr, base)


def test_no_credentials_are_sent_and_redirects_are_refused(fetcher):
    result, opener, _ = _fetch(fetcher, b'{"eligible": true}')
    request, timeout = opener.requests[0]
    assert result == {"eligible": True} and timeout == fetcher.REQUEST_TIMEOUT
    assert {k.lower() for k in request.headers} == {"accept", "user-agent"}
    assert _fetch(fetcher, (302, b""))[0] == {"error": "redirect_refused"}
    assert issubclass(fetcher._NoRedirect, fetcher.urllib.request.HTTPRedirectHandler)
    assert fetcher._NoRedirect().redirect_request() is None


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("down"),
        TimeoutError(),
        OSError("reset"),
        (503, b"{}"),
        (429, b'{"error":"verification_busy"}'),
        (403, b'{"error":"github_unavailable"}'),
        b"<html>not json</html>",
    ],
    ids=["dns", "timeout", "reset", "503", "429", "retryable-403", "not-json"],
)
def test_transient_failures_retry_then_recover(fetcher, failure):
    result, opener, slept = _fetch(fetcher, failure, b'{"eligible": false}')
    assert result == {"eligible": False}
    assert len(opener.requests) == 2 and slept == [fetcher.RETRY_DELAY]


def test_unreachable_ends_in_an_error_code_inside_the_budget(fetcher):
    failure = urllib.error.URLError("down")
    result, opener, slept = _fetch(fetcher, *[failure] * 50)
    assert result == {"error": "authority_unreachable"}
    assert sum(slept) < 120 and len(opener.requests) == len(slept) + 1


def test_budget_zero_tries_once(fetcher):
    result, opener, slept = _fetch(fetcher, urllib.error.URLError("down"), budget=0)
    assert result == {"error": "authority_unreachable"} and slept == []


def test_a_final_rejection_is_not_retried(fetcher):
    result, opener, _ = _fetch(fetcher, (403, b'{"error":"pr_mismatch"}'))
    assert result == {"error": "pr_mismatch"} and len(opener.requests) == 1


def test_oversized_or_non_object_answers_become_errors(fetcher):
    big = b'{"a":"' + b"x" * fetcher.MAX_BYTES + b'"}'
    assert _fetch(fetcher, big)[0] == {"error": "response_too_large"}
    assert _fetch(fetcher, b"[1]")[0] == {"error": "response_not_object"}


def test_main_writes_the_error_so_the_gate_can_turn_red(fetcher, policy, tmp_path, monkeypatch):
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(policy), encoding="utf-8")
    out = tmp_path / "answer.json"

    class Down:
        def open(self, *_, **__):
            raise urllib.error.URLError("down")

    monkeypatch.setattr(fetcher.urllib.request, "build_opener", lambda *_: Down())
    code = fetcher.main(
        [
            "--policy",
            str(pol),
            "--event",
            "pull_request",
            "--head",
            HEAD,
            "--nonce",
            NONCE,
            "--pr",
            str(PR),
            "--out",
            str(out),
            "--budget-seconds",
            "0",
        ]  # fmt: skip
    )
    assert code == 0 and json.loads(out.read_text()) == {"error": "authority_unreachable"}


def test_main_refuses_a_policy_without_an_origin(fetcher, policy, tmp_path):
    bad = copy.deepcopy(policy)
    del bad["authority_origin"]
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(bad), encoding="utf-8")
    out = tmp_path / "answer.json"
    argv = ["--policy", str(pol), "--event", "pull_request", "--head", HEAD, "--nonce", NONCE,
            "--pr", str(PR), "--out", str(out)]  # fmt: skip
    assert fetcher.main(argv) == 2 and not out.exists()
