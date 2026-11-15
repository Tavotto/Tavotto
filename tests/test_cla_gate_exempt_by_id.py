"""服务模式下按不可变账号 id 的本地豁免放行（cla_gate._id_exempt / _exempt_by_id_verdict）。

主仓库接入 CLA 服务后，绝大多数 PR 与合并组只有著作权人和机器人。服务端对豁免作者
的 PR 不登记、也不发凭证，所以 `cla-check` 必须能在本地把它们放行——但服务模式下
login 不是身份证明（login 能改名、能被抢注），这里钉住：只有可信来源给出的 id 全部
等于豁免条目的 `github_id` 才放行，其余任何形状一律回到「要服务凭证」。
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOLDER_ID = 88193520
DEPENDABOT_ID = 49699333


def _load_gate():
    path = ROOT / "scripts" / "ci" / "cla_gate.py"
    spec = importlib.util.spec_from_file_location("cla_gate_exempt_by_id", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


def _disabled_policy(p: dict) -> dict:
    """仓库 policy 已启用服务（ADR 0183 切换）；造一份「服务未启用」的样本。"""
    p = json.loads(json.dumps(p))
    p["provider"] = {
        "configured": False,
        "name": None,
        "check_name": None,
        "note": "test: disabled",
    }
    p.pop("authority_origin", None)
    return p


def _service_policy(*, ids: bool = True) -> dict:
    p = json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
    p = copy.deepcopy(p)
    p["offline_signatures"] = []
    p["authority_origin"] = "https://cla.example.com"
    p["provider"] = {
        "configured": True,
        "protocol": "tavotto-cla-v1",
        "name": "Tavotto CLA",
        "check_name": "Tavotto CLA",
        "app_slug": "tavotto-cla",
        "app_id": 1234567,
    }
    for ex in p["exemptions"]:
        if ex["kind"] == "countersigned_paper":
            continue  # 纸面签署条目本身就带 github_id（ADR 0183）
        if not ids:
            ex.pop("github_id", None)
            continue
        ex["github_id"] = {
            "erwanjun": HOLDER_ID,
            "dependabot[bot]": DEPENDABOT_ID,
            "github-actions[bot]": 41898282,
        }[ex["login"]]
    return p


def _commit(sha: str, login: str | None, uid: int | None, message: str = "x", email: str = ""):
    author = {"login": login, "id": uid} if login else None
    return {
        "sha": sha * 40 if len(sha) == 1 else sha,
        "author": author,
        "commit": {"message": message, "author": {"name": "n", "email": email}},
    }


def _decide(gate, policy, event, commits, pr_author=None, pr_author_id=None, checks=None):
    contributors, unresolved = gate.collect_contributors(pr_author, commits, pr_author_id)
    context = (
        {"group_members": [{"pr": 42, "headSha": "c" * 40}]} if event == "merge_group" else None
    )
    return gate.decide(event, policy, checks, contributors, unresolved, context=context)


# ─────────────────────────────────────────────── 放行


def test_holder_pr_passes_locally_by_id(gate):
    p = _service_policy()
    gate.validate_policy(p)
    v = _decide(
        gate, p, "pull_request", [_commit("a", "erwanjun", HOLDER_ID)], "erwanjun", HOLDER_ID
    )
    assert v["status"] == "success" and v["reason"] == "all_contributors_exempt_by_id"
    assert [r["verdict"] for r in v["contributors"]] == ["exempt"]


def test_dependabot_pr_passes_locally_by_id(gate):
    p = _service_policy()
    v = _decide(
        gate,
        p,
        "pull_request",
        [_commit("b", "dependabot[bot]", DEPENDABOT_ID)],
        "dependabot[bot]",
        DEPENDABOT_ID,
    )
    assert v["status"] == "success"


def test_all_exempt_merge_group_passes_locally(gate):
    p = _service_policy()
    commits = [_commit("a", "erwanjun", HOLDER_ID), _commit("b", "dependabot[bot]", DEPENDABOT_ID)]
    v = _decide(gate, p, "merge_group", commits)
    assert v["status"] == "success" and v["reason"] == "merge_candidate_exempt_by_id"


# ─────────────────────────────────────────────── 不放行（回到要服务凭证）


@pytest.mark.parametrize(
    "commits,pr_author,pr_author_id,why",
    [
        # 冒名：login 对、id 不对
        ([_commit("a", "erwanjun", 999)], "erwanjun", HOLDER_ID, "commit id mismatch"),
        ([_commit("a", "erwanjun", HOLDER_ID)], "erwanjun", 999, "pr author id mismatch"),
        # 缺 PR 作者 id
        ([_commit("a", "erwanjun", HOLDER_ID)], "erwanjun", None, "pr author id missing"),
        # 只经 noreply 邮箱出现（数字不是身份证明）
        (
            [_commit("a", None, None, email=f"{HOLDER_ID}+erwanjun@users.noreply.github.com")],
            "erwanjun",
            HOLDER_ID,
            "noreply email appearance",
        ),
        # 只经 Co-authored-by 出现
        (
            [
                _commit(
                    "a",
                    "dependabot[bot]",
                    DEPENDABOT_ID,
                    message=f"x\n\nCo-authored-by: J <{HOLDER_ID}+erwanjun@users.noreply.github.com>",
                )
            ],
            "dependabot[bot]",
            DEPENDABOT_ID,
            "co-author trailer",
        ),
        # 混入外部贡献者
        (
            [_commit("a", "erwanjun", HOLDER_ID), _commit("b", "someone", 5)],
            "erwanjun",
            HOLDER_ID,
            "external contributor",
        ),
        # 认不出的提交作者
        ([_commit("a", None, None, email="x@example.com")], "erwanjun", HOLDER_ID, "unresolved"),
    ],
)
def test_pull_request_falls_back_to_service(gate, commits, pr_author, pr_author_id, why):
    p = _service_policy()
    v = _decide(gate, p, "pull_request", commits, pr_author, pr_author_id)
    assert v["status"] == "failure", why
    assert v["reason"] != "all_contributors_exempt_by_id", why


def test_merge_group_with_external_requires_service_proof(gate):
    p = _service_policy()
    commits = [_commit("a", "erwanjun", HOLDER_ID), _commit("b", "someone", 5)]
    v = _decide(gate, p, "merge_group", commits)
    assert v["status"] == "failure" and v["reason"] == "fresh_merge_verification_required"


def test_merge_group_without_commits_requires_service_proof(gate):
    """旧 workflow 没有独立 PR 成员快照：不能再靠服务凭证补足它。"""
    p = _service_policy()
    v = gate.decide("merge_group", p, None, [], [], context=None)
    assert v["status"] == "failure" and v["reason"] == "group_snapshot_required"


def test_merge_group_empty_commit_list_is_not_exempt(gate):
    p = _service_policy()
    v = _decide(gate, p, "merge_group", [])
    assert v["status"] == "failure"


# ─────────────────────────────────────────────── 非服务模式不变


def test_non_service_merge_group_still_not_applicable(gate):
    p = _disabled_policy(
        json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
    )
    v = _decide(gate, p, "merge_group", [_commit("b", "someone", 5)])
    assert v["status"] == "not_applicable"


def test_non_service_pr_exemption_by_login_unchanged(gate):
    p = _disabled_policy(
        json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
    )
    v = _decide(gate, p, "pull_request", [_commit("a", "erwanjun", None)], "erwanjun", None)
    assert v["status"] == "success" and v["reason"] == "all_contributors_qualified"


# ─────────────────────────────────────────────── policy 形状


def test_service_mode_requires_github_id_on_every_exemption(gate):
    p = _service_policy(ids=False)
    with pytest.raises(gate.ConfigError, match="github_id"):
        gate.validate_policy(p)


@pytest.mark.parametrize("bad", [0, -1, "88193520", 1.5, True])
def test_exemption_github_id_must_be_positive_int(gate, bad):
    p = _service_policy()
    p["exemptions"][0]["github_id"] = bad
    with pytest.raises(gate.ConfigError, match="github_id"):
        gate.validate_policy(p)


def test_exemption_github_id_unique(gate):
    p = _service_policy()
    p["exemptions"][1]["github_id"] = HOLDER_ID
    with pytest.raises(gate.ConfigError, match="重复"):
        gate.validate_policy(p)


def test_cli_merge_group_with_only_commits_json_refuses(gate, tmp_path):
    p = _service_policy()
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(p), encoding="utf-8")
    commits = tmp_path / "c.json"
    commits.write_text(json.dumps([_commit("a", "erwanjun", HOLDER_ID)]), encoding="utf-8")
    rc = gate.main(
        [
            "--event",
            "merge_group",
            "--policy",
            str(pol),
            "--repo-root",
            str(ROOT),
            "--commits-json",
            str(commits),
            "--expected-commits",
            "1",
        ]
    )
    assert rc == 2


# ─────────────────────────────────────────────── 纸面双签（countersigned_paper，ADR 0183）

PAPER_ID = 455140  # mvanhorn，仓库 policy 里唯一的纸面签署条目


def _stale(p: dict) -> dict:
    """协议升版：纸面签的 1.0 不再覆盖当前版本。"""
    p["agreements"]["individual"]["version"] = "2.0"
    return p


def _group(gate, policy, commits, checks=None):
    contributors, unresolved = gate.collect_contributors(None, commits, None)
    ctx = {"kind": "merge_group", "group_members": [{"pr": 42, "headSha": "c" * 40}]}
    return gate.decide("merge_group", policy, checks, contributors, unresolved, context=ctx)


@pytest.fixture
def service_says_eligible(gate, monkeypatch):
    """服务按 id 集合豁免、不看版本：模拟它对整组回答 eligible=true。"""
    monkeypatch.setattr(gate, "signed_logins", lambda *a, **k: ({"*"}, None))


def test_repository_policy_carries_the_paper_signer(gate):
    p = _service_policy()
    gate.validate_policy(p)
    (ex,) = [e for e in p["exemptions"] if e["kind"] == "countersigned_paper"]
    assert (ex["login"], ex["github_id"], ex["version"]) == ("mvanhorn", PAPER_ID, "1.0")


def test_paper_signer_pr_passes_locally_by_id(gate):
    v = _decide(
        gate,
        _service_policy(),
        "pull_request",
        [_commit("a", "mvanhorn", PAPER_ID)],
        "mvanhorn",
        PAPER_ID,
    )
    assert v["status"] == "success" and v["reason"] == "all_contributors_exempt_by_id", v


def test_paper_signer_with_another_account_id_is_not_local(gate):
    v = _decide(
        gate, _service_policy(), "pull_request", [_commit("a", "mvanhorn", 9)], "mvanhorn", 9
    )
    assert v["status"] == "failure" and v["reason"] != "all_contributors_exempt_by_id"


def test_stale_paper_pr_is_red_even_when_the_service_says_eligible(gate, service_says_eligible):
    v = _decide(
        gate,
        _stale(_service_policy()),
        "pull_request",
        [_commit("a", "mvanhorn", PAPER_ID)],
        "mvanhorn",
        PAPER_ID,
    )
    assert v["status"] == "failure", v
    assert "2.0" in " ".join(v["problems"])


def test_mixed_pr_with_valid_paper_signer_follows_the_service(gate, service_says_eligible):
    commits = [_commit("a", "mvanhorn", PAPER_ID), _commit("b", "someone", 5)]
    v = _decide(gate, _service_policy(), "pull_request", commits, "someone", 5)
    assert v["status"] == "success", v
    verdicts = {r["login"]: r["verdict"] for r in v["contributors"]}
    assert verdicts == {"mvanhorn": "exempt", "someone": "signed"}


def test_mixed_group_with_valid_paper_signer_follows_the_service(gate, service_says_eligible):
    commits = [_commit("a", "mvanhorn", PAPER_ID), _commit("b", "someone", 5)]
    v = _group(gate, _service_policy(), commits)
    assert v["status"] == "success" and v["reason"] == "fresh_merge_candidate_qualified", v


def test_group_with_stale_paper_is_red_even_when_the_service_says_eligible(
    gate, service_says_eligible
):
    commits = [_commit("a", "mvanhorn", PAPER_ID), _commit("b", "someone", 5)]
    v = _group(gate, _stale(_service_policy()), commits)
    assert v["status"] == "failure" and v["reason"] == "countersigned_paper_not_qualified", v


def test_all_paper_and_holder_group_passes_locally(gate):
    commits = [_commit("a", "mvanhorn", PAPER_ID), _commit("b", "erwanjun", HOLDER_ID)]
    v = _group(gate, _service_policy(), commits)
    assert v["status"] == "success" and v["reason"] == "merge_candidate_exempt_by_id", v


def test_offline_signatures_section_is_retired(gate):
    p = _service_policy()
    p["offline_signatures"] = [{"login": "x"}]
    with pytest.raises(gate.ConfigError, match="ADR 0183"):
        gate.validate_policy(p)
