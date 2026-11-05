"""`cla_gate._service_verdict` 读 Cloudflare 服务真实写出的 check（凭证在 `output.summary`）。

形状取自 staging 真实发布记录（2026-11-05 沙盒 PR 与合并组的 proof_text）。这里钉住的
缺陷：判定器原来只读 `output.text`，而 Worker 只写 `summary`，接上之后每个外部贡献者的
合法凭证都会被判 `provider_proof_invalid_or_stale`，形状测试和替身都看不出来。
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD, BASE = "a" * 40, "b" * 40
REPO, PR, ISSUED, APP_ID, SLUG = 1445721664, 4, 1_793_892_000, 7654321, "tavotto-cla"
CHECK = "Tavotto CLA"


def _load_gate():
    path = ROOT / "scripts" / "ci" / "cla_gate.py"
    spec = importlib.util.spec_from_file_location("cla_gate_service_proof", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


@pytest.fixture
def policy():
    p = json.loads((ROOT / ".github" / "cla-policy.json").read_text(encoding="utf-8"))
    p["offline_signatures"] = []
    p["provider"] = {
        "configured": True,
        "protocol": "tavotto-cla-v1",
        "name": "Tavotto CLA",
        "check_name": CHECK,
        "app_slug": SLUG,
        "app_id": APP_ID,
    }
    return p


def _digest(policy: dict) -> str:
    trusted = {k: policy[k] for k in ("agreements", "exemptions")}
    raw = json.dumps(trusted, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _proof(policy: dict, *, group: bool = False, **over) -> dict:
    proof = {
        "protocol": "tavotto-cla-v1",
        "kind": "merge_group" if group else "pr",
        "repository_id": REPO,
        "head_sha": HEAD,
        "base_sha": BASE,
        "policy_sha256": _digest(policy),
        "contributors": [{"id": 5, "login": "someone"}],
        "members": [{"pr": PR, "headSha": "c" * 40}] if group else [],
        "revision": 3,
        "issued_at": ISSUED,
        "expires_at": ISSUED + 120,
    }
    if not group:
        proof["pr"] = PR
    proof.update(over)
    return proof


def _run(proof_text: str, *, field: str = "summary", **over) -> dict:
    run = {
        "id": 9,
        "name": CHECK,
        "app": {"id": APP_ID, "slug": SLUG},
        "status": "completed",
        "conclusion": "success",
        "head_sha": HEAD,
        "output": {"title": CHECK, "summary": None, "text": None},
    }
    run["output"][field] = proof_text
    run.update(over)
    return run


def _context(*, group: bool = False, now: int = ISSUED + 5, not_before: int = ISSUED - 5) -> dict:
    return {
        "kind": "merge_group" if group else "pull_request",
        "pr": None if group else PR,
        "head_sha": HEAD,
        "base_sha": BASE,
        "repository_id": REPO,
        "not_before": not_before,
        "now": now,
    }


def _text(proof: dict) -> str:
    return "tavotto-cla-v1\n" + json.dumps(proof, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("group", [False, True])
def test_worker_shaped_check_is_accepted(gate, policy, group):
    run = _run(_text(_proof(policy, group=group)))
    assert gate._service_verdict([run], policy, _context(group=group)) == ({"*"}, None)


def test_legacy_text_field_still_reads(gate, policy):
    run = _run(_text(_proof(policy)), field="text")
    assert gate._service_verdict([run], policy, _context()) == ({"*"}, None)


def test_legacy_human_summary_does_not_hide_the_proof_in_text(gate, policy):
    """早期 Python 服务：`summary` 是说明文字，凭证在 `text`。"""
    run = _run(_text(_proof(policy)), field="text")
    run["output"]["summary"] = "Fresh CLA proof for this head."
    assert gate._service_verdict([run], policy, _context()) == ({"*"}, None)


def test_revocation_summary_never_passes_even_marked_success(gate, policy):
    revocation = 'tavotto-cla-revocation-v1\n{"attempt":"x","head_sha":"' + HEAD + '"}'
    signed, why = gate._service_verdict([_run(revocation)], policy, _context())
    assert signed == set() and why == "provider_proof_invalid_or_stale"


def test_withdrawn_check_fails_on_conclusion(gate, policy):
    run = _run(_text(_proof(policy)), conclusion="failure")
    signed, why = gate._service_verdict([run], policy, _context())
    assert signed == set() and why == "provider_latest_check_not_success"


@pytest.mark.parametrize(
    "over",
    [
        {"policy_sha256": "0" * 64},
        {"head_sha": "d" * 40},
        {"repository_id": 1},
        {"pr": 5},
        {"expires_at": ISSUED + 121},
    ],
)
def test_tampered_binding_is_rejected(gate, policy, over):
    run = _run(_text(_proof(policy, **over)))
    signed, why = gate._service_verdict([run], policy, _context())
    assert signed == set() and why == "provider_proof_invalid_or_stale"


@pytest.mark.parametrize(
    "ctx",
    [
        {"now": ISSUED + 120},  # 已到期
        {"not_before": ISSUED + 1},  # 早于这次校验开始
    ],
)
def test_stale_proof_is_rejected(gate, policy, ctx):
    run = _run(_text(_proof(policy)))
    signed, why = gate._service_verdict([run], policy, _context(**ctx))
    assert signed == set() and why == "provider_proof_invalid_or_stale"


def test_pr_proof_cannot_pass_a_merge_candidate(gate, policy):
    run = _run(_text(_proof(policy)))
    signed, _ = gate._service_verdict([run], policy, _context(group=True))
    assert signed == set()


def test_group_proof_cannot_pass_a_pull_request(gate, policy):
    run = _run(_text(_proof(policy, group=True, pr=PR)))
    signed, _ = gate._service_verdict([run], policy, _context())
    assert signed == set()


def test_same_name_from_another_app_is_ignored(gate, policy):
    run = _run(_text(_proof(policy)), app={"id": 15368, "slug": "github-actions"})
    signed, why = gate._service_verdict([run], policy, _context())
    assert signed == set() and why == "provider_check_missing_or_invalid"


def test_missing_output_fields_fail_closed(gate, policy):
    run = copy.deepcopy(_run("x"))
    run["output"] = {}
    signed, why = gate._service_verdict([run], policy, _context())
    assert signed == set() and why == "provider_proof_invalid_or_stale"
