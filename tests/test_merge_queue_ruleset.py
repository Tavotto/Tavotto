"""Ruleset 迁移工具（scripts/ci/merge_queue_ruleset.py）的看护。

这个脚本写的是**线上合并保护**——它出错的形态不是测试红，是仓库锁死
（required context 指向一个 main 上不存在的名字）或保护被静默削弱
（bypass actor 混进来、pull_request rule 被抹掉、strict 关了却没强制队列）。
所以每条用例都在 fixture 上模拟 GitHub API，逐条钉住「不许发生什么」。

全部平台无关、纯标准库、零网络——`gh_api` 被 FakeApi 整个换掉，
任何用例都不该发真实请求。
"""

from __future__ import annotations

import base64
import copy
import json
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import merge_queue_ruleset as MQ  # noqa: E402

REPO = "Tavotto/Tavotto"


def _ruleset(
    *,
    ruleset_id=21121430,
    name=MQ.DEFAULT_RULESET_NAME,
    target="branch",
    strict=True,
    merge_queue=False,
    contexts=None,
    extra_rules=(),
    bypass=(),
):
    contexts = (
        contexts
        if contexts is not None
        else ["backend (ubuntu-latest, 3.10)", "frontend", "workerd", "invariants"]
    )
    rules = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "pull_request",
            "parameters": {
                "required_review_thread_resolution": True,
                "allowed_merge_methods": ["merge", "squash"],
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": strict,
                "do_not_enforce_on_create": False,
                "required_status_checks": [{"context": c} for c in contexts],
            },
        },
        *copy.deepcopy(list(extra_rules)),
    ]
    if merge_queue:
        rules.append({"type": "merge_queue", "parameters": dict(MQ.MERGE_QUEUE_PARAMS)})
    return {
        "id": ruleset_id,
        "name": name,
        "target": target,
        "source_type": "Repository",
        "source": REPO,
        "enforcement": "active",
        "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
        "rules": rules,
        "bypass_actors": list(bypass),
    }


def _wf_text(with_merge_group=True):
    body = "on:\n  pull_request:\n"
    if with_merge_group:
        body += "  merge_group:\n    types: [checks_requested]\n"
    return body


class FakeApi:
    """`gh_api` 的假实现：记录每一次调用，写请求单独记账。"""

    def __init__(
        self,
        rulesets,
        *,
        gates_conclusion="success",
        gates_present=True,
        workflows_have_merge_group=True,
    ):
        self.rulesets = rulesets
        self.gates_conclusion = gates_conclusion
        self.gates_present = gates_present
        self.workflows_have_merge_group = workflows_have_merge_group
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, dict]] = []

    def __call__(self, path, *, method="GET", body=None):
        self.calls.append((method, path))
        if method != "GET":
            self.writes.append((path, body))
            return {}
        if path == f"repos/{REPO}":
            return {"default_branch": "main"}
        if path == f"repos/{REPO}/rulesets":
            return [
                {"id": r["id"], "name": r["name"], "target": r["target"]} for r in self.rulesets
            ]
        for r in self.rulesets:
            if path == f"repos/{REPO}/rulesets/{r['id']}":
                return copy.deepcopy(r)
        if path == f"repos/{REPO}/commits/main":
            return {"sha": "a" * 40}
        if path.startswith(f"repos/{REPO}/commits/{'a' * 40}/check-runs"):
            runs = []
            if self.gates_present:
                runs = [{"name": g, "conclusion": self.gates_conclusion} for g in MQ.GATE_CONTEXTS]
            return {"check_runs": runs}
        if path.startswith(f"repos/{REPO}/contents/"):
            text = _wf_text(self.workflows_have_merge_group)
            return {"content": base64.b64encode(text.encode()).decode()}
        raise AssertionError(f"FakeApi 不认识 {method} {path}")


@pytest.fixture()
def plan_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _plan_and_apply(api, phase, plan_dir, *, yes=True):
    plan_file = plan_dir / f"plan-{phase}.json"
    rc = None
    with _patched(api):
        rc = MQ.main(["plan", "--phase", phase, "--plan-file", str(plan_file)])
        assert rc == 0
        rc = MQ.main(
            ["apply", "--phase", phase, "--plan-file", str(plan_file)] + (["--yes"] if yes else [])
        )
    return rc


class _patched:
    def __init__(self, api):
        self.api = api

    def __enter__(self):
        self.orig = MQ.gh_api
        MQ.gh_api = self.api

    def __exit__(self, *exc):
        MQ.gh_api = self.orig


# ============================================================ 定位
class TestLocate:
    def test_inspect_finds_by_name_not_id(self, capsys):
        api = FakeApi([_ruleset(ruleset_id=999999)])
        with _patched(api):
            assert MQ.main(["inspect"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["id"] == 999999, "定位必须按名称+条件，不按写死的 ID"

    def test_missing_ruleset_is_an_error(self, capsys):
        api = FakeApi([_ruleset(name="别的名字")])
        with _patched(api):
            assert MQ.main(["inspect"]) == 1

    def test_multiple_same_name_rulesets_refuse(self, capsys):
        api = FakeApi([_ruleset(ruleset_id=1), _ruleset(ruleset_id=2)])
        with _patched(api):
            assert MQ.main(["inspect"]) == 1
        assert "2 个" in capsys.readouterr().err

    def test_tag_ruleset_is_structurally_unreachable(self):
        """tag ruleset（target != branch）连候选都进不了——改不到它。"""
        tag = _ruleset(ruleset_id=21121449, name=MQ.DEFAULT_RULESET_NAME, target="tag")
        api = FakeApi([tag])
        with pytest.raises(MQ.MigrationError):
            MQ.find_ruleset(api, REPO, MQ.DEFAULT_RULESET_NAME, "main")


# ============================================================ enable-queue
class TestEnableQueue:
    def test_adds_queue_and_relaxes_strict_keeping_all_contexts(self):
        cur = _ruleset(strict=True, contexts=["a", "b", "c"])
        new = MQ.build_enable_queue(cur)
        rsc = [r for r in new["rules"] if r["type"] == "required_status_checks"][0]
        assert rsc["parameters"]["strict_required_status_checks_policy"] is False
        assert [c["context"] for c in rsc["parameters"]["required_status_checks"]] == [
            "a",
            "b",
            "c",
        ], "enable-queue 一个旧 context 都不许删"
        mq = [r for r in new["rules"] if r["type"] == "merge_queue"]
        assert len(mq) == 1
        assert mq[0]["parameters"]["grouping_strategy"] == "ALLGREEN"
        assert mq[0]["parameters"]["merge_method"] == "SQUASH"

    def test_unknown_rules_survive(self):
        """未来 GitHub 新增的 rule 类型必须原样带回，绝不因为不认识就丢。"""
        alien = {"type": "future_rule", "parameters": {"x": 1}}
        cur = _ruleset(extra_rules=[alien])
        new = MQ.build_enable_queue(cur)
        assert alien in new["rules"]

    def test_pull_request_rule_survives(self):
        cur = _ruleset()
        new = MQ.build_enable_queue(cur)
        kinds = [r["type"] for r in new["rules"]]
        for keep in ("pull_request", "deletion", "non_fast_forward"):
            assert keep in kinds, f"{keep} rule 被抹掉了"

    def test_bypass_actors_survive_verbatim_and_are_never_added(self):
        someone = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
        cur = _ruleset(bypass=someone)
        assert MQ.build_enable_queue(cur)["bypass_actors"] == someone
        assert MQ.build_enable_queue(_ruleset())["bypass_actors"] == []


# ============================================================ switch-to-gates
class TestSwitchToGates:
    def test_requires_merge_queue_first(self):
        """只关 strict / 只换 contexts 而不强制队列，旧 main 上绿过的 PR
        仍可直接合并——顺序错了要当场拒绝。"""
        with pytest.raises(MQ.MigrationError, match="enable-queue"):
            MQ.build_switch_to_gates(_ruleset(strict=False, merge_queue=False))

    def test_requires_strict_already_off(self):
        with pytest.raises(MQ.MigrationError, match="strict"):
            MQ.build_switch_to_gates(_ruleset(strict=True, merge_queue=True))

    def test_contexts_become_exactly_the_three_gates(self):
        new = MQ.build_switch_to_gates(_ruleset(strict=False, merge_queue=True))
        rsc = [r for r in new["rules"] if r["type"] == "required_status_checks"][0]
        assert [c["context"] for c in rsc["parameters"]["required_status_checks"]] == [
            "CI fast gate",
            "CI integration gate",
            "CodeQL gate",
        ]

    def test_gate_names_match_the_workflow_files(self):
        """脚本里的三个名字必须与 workflow 的 `name:` 逐字相同——差一个字节，
        switch 之后所有 PR 等一个永远不出现的 context，仓库锁死。"""
        wf_dir = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        ci = (wf_dir / "ci.yml").read_text(encoding="utf-8")
        codeql = (wf_dir / "codeql.yml").read_text(encoding="utf-8")
        assert "name: CI fast gate" in ci
        assert "name: CI integration gate" in ci
        assert "name: CodeQL gate" in codeql
        assert MQ.GATE_CONTEXTS == ["CI fast gate", "CI integration gate", "CodeQL gate"]


# ============================================================ apply 的闸门
class TestApply:
    def test_dry_run_and_plan_never_write(self, plan_dir, capsys):
        api = FakeApi([_ruleset()])
        with _patched(api):
            assert MQ.main(["plan", "--phase", "enable-queue"]) == 0
        assert api.writes == [], "plan 发出了写请求"

    def test_apply_without_yes_refuses(self, plan_dir, capsys):
        api = FakeApi([_ruleset()])
        rc = _plan_and_apply(api, "enable-queue", plan_dir, yes=False)
        assert rc == 3 and api.writes == []

    def test_apply_enable_queue_puts_the_full_body(self, plan_dir, capsys):
        api = FakeApi([_ruleset()])
        rc = _plan_and_apply(api, "enable-queue", plan_dir)
        assert rc == 0
        assert len(api.writes) == 1
        path, body = api.writes[0]
        assert path == f"repos/{REPO}/rulesets/21121430"
        assert body["bypass_actors"] == []
        kinds = [r["type"] for r in body["rules"]]
        assert "merge_queue" in kinds and "pull_request" in kinds

    def test_apply_refuses_when_ruleset_drifted(self, plan_dir, capsys):
        """plan 之后别人改过 ruleset：拿旧 JSON 盖上去会抹掉那次修改。"""
        api = FakeApi([_ruleset()])
        plan_file = plan_dir / "p.json"
        with _patched(api):
            assert MQ.main(["plan", "--phase", "enable-queue", "--plan-file", str(plan_file)]) == 0
        # 并发漂移：有人往线上 ruleset 加了一条规则
        api.rulesets[0]["rules"].append({"type": "somebody_elses_rule"})
        with _patched(api):
            rc = MQ.main(
                ["apply", "--phase", "enable-queue", "--plan-file", str(plan_file), "--yes"]
            )
        assert rc == 1 and api.writes == []
        assert "哈希" in capsys.readouterr().err

    def test_switch_refuses_until_gates_ran_green_on_main(self, plan_dir, capsys):
        api = FakeApi([_ruleset(strict=False, merge_queue=True)], gates_present=False)
        rc = _plan_and_apply(api, "switch-to-gates", plan_dir)
        assert rc == 1 and api.writes == []
        assert "从未出现" in capsys.readouterr().err

    def test_switch_refuses_a_non_success_gate(self, plan_dir, capsys):
        api = FakeApi([_ruleset(strict=False, merge_queue=True)], gates_conclusion="failure")
        rc = _plan_and_apply(api, "switch-to-gates", plan_dir)
        assert rc == 1 and api.writes == []
        assert "不是 success" in capsys.readouterr().err

    def test_switch_refuses_workflows_without_merge_group(self, plan_dir, capsys):
        api = FakeApi([_ruleset(strict=False, merge_queue=True)], workflows_have_merge_group=False)
        rc = _plan_and_apply(api, "switch-to-gates", plan_dir)
        assert rc == 1 and api.writes == []

    def test_enable_queue_also_requires_merge_group_listeners(self, plan_dir, capsys):
        """队列一开候选就要在 merge_group 上等 required contexts；workflow 没
        监听的话每个候选白等 90 分钟——enable 前就要拦。"""
        api = FakeApi([_ruleset()], workflows_have_merge_group=False)
        rc = _plan_and_apply(api, "enable-queue", plan_dir)
        assert rc == 1 and api.writes == []

    def test_switch_happy_path_writes_gates_only(self, plan_dir, capsys):
        api = FakeApi([_ruleset(strict=False, merge_queue=True)])
        rc = _plan_and_apply(api, "switch-to-gates", plan_dir)
        assert rc == 0 and len(api.writes) == 1
        body = api.writes[0][1]
        rsc = [r for r in body["rules"] if r["type"] == "required_status_checks"][0]
        assert [
            c["context"] for c in rsc["parameters"]["required_status_checks"]
        ] == MQ.GATE_CONTEXTS
        assert [r for r in body["rules"] if r["type"] == "merge_queue"], (
            "gates-only 的 ruleset 里必须仍有强制 merge_queue"
        )

    def test_the_put_body_is_recomputed_from_live_state_not_the_plan(self, plan_dir, capsys):
        """plan 文件只是确认物：PUT 的 body 必须等于对线上现状重算的变换。

        只抽查 bypass_actors 那种点名单挡不住被编辑的 plan（#119 评审 P1）
        ——这里按「写出去的就是重算出来的」整体钉死。"""
        api = FakeApi([_ruleset()])
        rc = _plan_and_apply(api, "enable-queue", plan_dir)
        assert rc == 0
        body = api.writes[0][1]
        expected = MQ.build_enable_queue(_ruleset())
        assert body["rules"] == expected["rules"]
        assert body["conditions"] == expected["conditions"]

    def test_a_tampered_plan_that_drops_the_pull_request_rule_is_refused(self, plan_dir, capsys):
        """手改 plan 抹掉 pull_request rule：base 哈希核对的是 current，
        量不到 plan 本身——必须靠「与重算结果逐字节相等」逮住。"""
        api = FakeApi([_ruleset()])
        plan_file = plan_dir / "p.json"
        with _patched(api):
            assert MQ.main(["plan", "--phase", "enable-queue", "--plan-file", str(plan_file)]) == 0
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        plan["updated"]["rules"] = [
            r for r in plan["updated"]["rules"] if r["type"] != "pull_request"
        ]
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        with _patched(api):
            rc = MQ.main(
                ["apply", "--phase", "enable-queue", "--plan-file", str(plan_file), "--yes"]
            )
        assert rc == 1 and api.writes == []
        assert "重算" in capsys.readouterr().err

    def test_a_tampered_plan_that_swaps_required_contexts_is_refused(self, plan_dir, capsys):
        api = FakeApi([_ruleset(strict=False, merge_queue=True)])
        plan_file = plan_dir / "p.json"
        with _patched(api):
            assert (
                MQ.main(["plan", "--phase", "switch-to-gates", "--plan-file", str(plan_file)]) == 0
            )
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        rsc = [r for r in plan["updated"]["rules"] if r["type"] == "required_status_checks"][0]
        rsc["parameters"]["required_status_checks"] = [{"context": "totally fake"}]
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        with _patched(api):
            rc = MQ.main(
                ["apply", "--phase", "switch-to-gates", "--plan-file", str(plan_file), "--yes"]
            )
        assert rc == 1 and api.writes == []

    def test_a_plan_that_injects_a_bypass_actor_is_refused(self, plan_dir, capsys):
        """手改 plan 文件塞 bypass actor：apply 要在写之前逮住。"""
        api = FakeApi([_ruleset()])
        plan_file = plan_dir / "p.json"
        with _patched(api):
            assert MQ.main(["plan", "--phase", "enable-queue", "--plan-file", str(plan_file)]) == 0
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        plan["updated"]["bypass_actors"] = [{"actor_id": 1}]
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        with _patched(api):
            rc = MQ.main(
                ["apply", "--phase", "enable-queue", "--plan-file", str(plan_file), "--yes"]
            )
        assert rc == 1 and api.writes == []

    def test_phase_mismatch_between_plan_and_flag_is_refused(self, plan_dir):
        api = FakeApi([_ruleset()])
        plan_file = plan_dir / "p.json"
        with _patched(api):
            assert MQ.main(["plan", "--phase", "enable-queue", "--plan-file", str(plan_file)]) == 0
            rc = MQ.main(
                ["apply", "--phase", "switch-to-gates", "--plan-file", str(plan_file), "--yes"]
            )
        assert rc == 1 and api.writes == []


# ============================================================ 变换的硬边界
class TestTransformSafety:
    def test_a_transform_that_drops_the_pull_request_rule_is_caught(self):
        """`_assert_untouched` 是最后一道闸：托管之外的 rule 变了当场抛。"""
        cur = _ruleset()
        broken = copy.deepcopy(cur)
        broken["rules"] = [r for r in broken["rules"] if r["type"] != "pull_request"]
        with pytest.raises(MQ.MigrationError):
            MQ._assert_untouched(cur, broken)

    def test_conditions_may_not_change(self):
        cur = _ruleset()
        broken = copy.deepcopy(cur)
        broken["conditions"]["ref_name"]["include"] = ["refs/heads/other"]
        with pytest.raises(MQ.MigrationError):
            MQ._assert_untouched(cur, broken)

    def test_stable_hash_is_order_insensitive_for_keys(self):
        a = {"x": 1, "y": [1, 2]}
        b = {"y": [1, 2], "x": 1}
        assert MQ.stable_hash(a) == MQ.stable_hash(b)
        assert MQ.stable_hash(a) != MQ.stable_hash({"x": 2, "y": [1, 2]})


# ============================================================ set-build-concurrency（ADR 0043）
class DecoupledApi(FakeApi):
    """默认分支上画布不在索引里 / marketplace 已切到发行分支 / plugin-stable 存在——
    三条各自可开关，缺一条并发就不许调。"""

    def __init__(
        self, rulesets, *, canvas_tracked=False, stable_source=True, branch_exists=True, **kw
    ):
        super().__init__(rulesets, **kw)
        self.canvas_tracked = canvas_tracked
        self.stable_source = stable_source
        self.branch_exists = branch_exists

    def __call__(self, path, *, method="GET", body=None):
        if method == "GET" and path.startswith(f"repos/{REPO}/contents/{MQ.GENERATED_CANVAS_PATH}"):
            self.calls.append((method, path))
            if self.canvas_tracked:
                return {"content": base64.b64encode(b"<!-- tavotto-mcp-widget x -->").decode()}
            raise MQ.MigrationError("gh api GET failed: HTTP 404: Not Found")
        if method == "GET" and path.startswith(f"repos/{REPO}/contents/{MQ.MARKETPLACE_PATH}"):
            self.calls.append((method, path))
            src = (
                {
                    "source": "git-subdir",
                    "url": "https://github.com/Tavotto/Tavotto.git",
                    "path": "./codex-plugin",
                    "ref": "plugin-stable",
                }
                if self.stable_source
                else {"source": "local", "path": "./codex-plugin"}
            )
            text = json.dumps({"name": "tavotto", "plugins": [{"name": "tavotto", "source": src}]})
            return {"content": base64.b64encode(text.encode()).decode()}
        if method == "GET" and path == f"repos/{REPO}/branches/{MQ.PLUGIN_STABLE_BRANCH}":
            self.calls.append((method, path))
            if self.branch_exists:
                return {"name": MQ.PLUGIN_STABLE_BRANCH}
            raise MQ.MigrationError("gh api GET failed: HTTP 404: Branch not found")
        return super().__call__(path, method=method, body=body)


def _queue_ruleset(build=1):
    rs = _ruleset(merge_queue=True, strict=False, contexts=MQ.GATE_CONTEXTS)
    for r in rs["rules"]:
        if r["type"] == "merge_queue":
            r["parameters"]["max_entries_to_build"] = build
    return rs


def _concurrency(api, plan_dir, n, *, yes=True):
    plan_file = plan_dir / "plan-concurrency.json"
    with _patched(api):
        rc = MQ.main(
            [
                "plan",
                "--phase",
                "set-build-concurrency",
                "--max-entries-to-build",
                str(n),
                "--plan-file",
                str(plan_file),
            ]
        )
        if rc != 0:
            return rc
        return MQ.main(
            [
                "apply",
                "--phase",
                "set-build-concurrency",
                "--max-entries-to-build",
                str(n),
                "--plan-file",
                str(plan_file),
            ]
            + (["--yes"] if yes else [])
        )


class TestSetBuildConcurrency:
    def test_changes_only_max_entries_to_build(self, plan_dir):
        api = DecoupledApi([_queue_ruleset(build=1)])
        assert _concurrency(api, plan_dir, 2) == 0
        assert len(api.writes) == 1
        body = api.writes[0][1]
        mq = next(r for r in body["rules"] if r["type"] == "merge_queue")["parameters"]
        assert mq["max_entries_to_build"] == 2
        assert mq["max_entries_to_merge"] == 1 and mq["min_entries_to_merge"] == 1
        assert mq["grouping_strategy"] == "ALLGREEN"
        rsc = next(r for r in body["rules"] if r["type"] == "required_status_checks")["parameters"]
        assert [c["context"] for c in rsc["required_status_checks"]] == MQ.GATE_CONTEXTS
        assert rsc["strict_required_status_checks_policy"] is False
        assert body["bypass_actors"] == []

    def test_refuses_while_the_canvas_is_still_tracked(self, plan_dir):
        api = DecoupledApi([_queue_ruleset()], canvas_tracked=True)
        assert _concurrency(api, plan_dir, 2) == 1
        assert api.writes == []

    def test_refuses_while_the_marketplace_still_points_at_the_source_tree(self, plan_dir):
        api = DecoupledApi([_queue_ruleset()], stable_source=False)
        assert _concurrency(api, plan_dir, 2) == 1
        assert api.writes == []

    def test_refuses_while_the_release_branch_does_not_exist(self, plan_dir):
        api = DecoupledApi([_queue_ruleset()], branch_exists=False)
        assert _concurrency(api, plan_dir, 2) == 1
        assert api.writes == []

    def test_refuses_absurd_values(self, plan_dir):
        api = DecoupledApi([_queue_ruleset()])
        assert _concurrency(api, plan_dir, 10) == 1
        assert _concurrency(api, plan_dir, 0) == 1
        assert api.writes == []

    def test_without_yes_nothing_is_written(self, plan_dir):
        api = DecoupledApi([_queue_ruleset()])
        assert _concurrency(api, plan_dir, 2, yes=False) == 3
        assert api.writes == []

    def test_plan_value_and_flag_must_agree(self, plan_dir):
        api = DecoupledApi([_queue_ruleset()])
        plan_file = plan_dir / "p.json"
        with _patched(api):
            assert (
                MQ.main(
                    [
                        "plan",
                        "--phase",
                        "set-build-concurrency",
                        "--max-entries-to-build",
                        "2",
                        "--plan-file",
                        str(plan_file),
                    ]
                )
                == 0
            )
            assert (
                MQ.main(
                    [
                        "apply",
                        "--phase",
                        "set-build-concurrency",
                        "--max-entries-to-build",
                        "3",
                        "--plan-file",
                        str(plan_file),
                        "--yes",
                    ]
                )
                == 1
            )
        assert api.writes == []

    def test_a_drifted_ruleset_is_refused(self, plan_dir):
        api = DecoupledApi([_queue_ruleset()])
        plan_file = plan_dir / "p.json"
        with _patched(api):
            assert (
                MQ.main(
                    [
                        "plan",
                        "--phase",
                        "set-build-concurrency",
                        "--max-entries-to-build",
                        "2",
                        "--plan-file",
                        str(plan_file),
                    ]
                )
                == 0
            )
            api.rulesets[0]["rules"].append({"type": "creation"})  # 别人并发改了
            assert (
                MQ.main(
                    [
                        "apply",
                        "--phase",
                        "set-build-concurrency",
                        "--max-entries-to-build",
                        "2",
                        "--plan-file",
                        str(plan_file),
                        "--yes",
                    ]
                )
                == 1
            )
        assert api.writes == []
