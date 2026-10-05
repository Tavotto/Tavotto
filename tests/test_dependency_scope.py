"""T06 / D04：同一个项目里两个子目录（作用域）的依赖互斥——不硬合并，给明确的出路。

受管环境是项目级的，账（`installed_by_tavotto`）里每一笔现在记着它是为哪个作用域（脚本所在目录）装的。
第一版只服务**一个优先的绘图作用域**：

* A 目录装好了 `beta<2`，B 目录声明 `beta>=2`——B 不能悄悄地"并入"（那会让 A 的脚本跑在 beta 2 上），也不能
  顶着不满足的版本"准备好了"：计划 `blocked`，理由 `dependency_scope_conflict`，带冲突项与两条出路；
* 出路一「换成本作用域」（`scope_policy=switch`）：新一代只装 B 的集合、账换成 B 的，**更大的影响**——它有自己的
  影响摘要（`drops` / `changes`），要单独确认；旧代留到没人用；失败不动 active；
* 出路二「子目录当独立项目」：各有各的受管环境，互不影响。

同一作用域里用户改了自己的声明是正常升级；老账没有归属的条目不参与互斥判断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker, wait_for
from tavotto.engine import depplan, deprepair, envlease, managedenv

pytest_plugins = ("support.dependency_repair",)

ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
BETA = ("tavotto-test-beta", "tavotto_test_beta")
GAMMA = ("tavotto-test-gamma", "tavotto_test_gamma")


@pytest.fixture(autouse=True)
def _clean(clean_state):
    envlease.reset_for_tests()
    depplan.reset_cache()
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    build_wheel(dest, name=ALPHA[0], import_name=ALPHA[1], version="1.0")
    build_wheel(dest, name=BETA[0], import_name=BETA[1], version="1.0")
    build_wheel(dest, name=BETA[0], import_name=BETA[1], version="2.0")
    build_wheel(dest, name=GAMMA[0], import_name=GAMMA[1], version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _scope(project: Path, rel: str, requirements: str, imports: list[str]) -> None:
    """项目里一个子目录（作用域）：自己的 requirements.txt + 一份 import 它们的脚本。"""
    folder = project / rel
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "requirements.txt").write_text(requirements, encoding="utf-8")
    body = "".join(f"import {name}\n" for name in imports)
    (folder / "figure.py").write_text(
        body + "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('F.pdf')\n",
        encoding="utf-8",
    )


@pytest.fixture
def two_scopes(tmp_path, house, offline_managed_env) -> Path:
    """A 要 `beta<2`，B 要 `beta>=2` 外加 `gamma`。先把 A 装好（账上记着 beta 1.0 是为 `a` 装的）。"""
    project = tmp_path / "paper"
    project.mkdir()
    _scope(project, "a", f"{BETA[0]}<2\n{ALPHA[0]}\n", [BETA[1], ALPHA[1]])
    _scope(project, "b", f"{BETA[0]}>=2\n{GAMMA[0]}\n", [BETA[1], GAMMA[1]])
    plan = deprepair.create_joint_plan(project, "a/figure.py")
    deprepair.prepare_async(plan.plan_id)
    assert wait_for(plan.plan_id)["state"] == deprepair.STATE_DONE
    entries = {e["distribution"]: e for e in managedenv.ledger_entries(project)}
    assert set(entries) == {BETA[0], ALPHA[0]}
    assert entries[BETA[0]]["resolved_version"] == "1.0" and entries[BETA[0]]["scope"] == "a"
    return project


def _importable(python: str, name: str) -> bool:
    import subprocess

    return (
        subprocess.run(
            [python, "-c", f"import {name}"], capture_output=True, timeout=120
        ).returncode
        == 0
    )


def _installed_version(project: Path) -> dict[str, str]:
    return {
        deprepair.depresolve.normalize_distribution(e["distribution"]): e["resolved_version"]
        for e in managedenv.ledger_entries(project)
    }


class TestAttribution:
    def test_the_scope_of_a_script_is_its_directory(self):
        assert deprepair.scope_of("figure.py") == "."
        assert deprepair.scope_of("a/figure.py") == "a"
        assert deprepair.scope_of("a/b/c.py") == "a/b"

    def test_an_install_records_who_it_was_for(self, two_scopes):
        # 老账（没有归属）读起来不报错，也不参与互斥判断
        project = two_scopes
        data = managedenv.read_manifest(project)
        for entry in data["installed_by_tavotto"]:
            entry.pop("scope")
        managedenv.write_manifest(project, data)
        plan, _kind, _py = deprepair.joint_plan_for(project, "b/figure.py")
        assert plan.status == "ready"
        assert not [b for b in plan.blocked if b["code"] == "dependency_scope_conflict"]


@needs_worker
class TestMutualExclusion:
    def test_the_other_scope_is_blocked_not_merged(self, two_scopes):
        project = two_scopes
        active = managedenv.active_generation(project)
        plan, kind, _py = deprepair.joint_plan_for(project, "b/figure.py")
        assert kind == deprepair.TARGET_MANAGED and plan.status == "blocked"
        (block,) = [b for b in plan.blocked if b["code"] == "dependency_scope_conflict"]
        conflict = block["conflicts"][0]
        assert conflict == {
            "name": "tavotto-test-beta",
            "installed_version": "1.0",
            "installed_for": "a",
            "wanted": ">=2",
            "wanted_for": "b",
            "via": "installed",
        }
        assert block["options"] == ["switch_scope", "separate_project"]
        # 不硬合并：创建计划被拒（带冲突项），没有新的一代，账与 active 原样
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "b/figure.py")
        assert err.value.code == deprepair.ERROR_PLAN_BLOCKED
        assert err.value.extra["joint"]["blocked"][-1]["code"] == "dependency_scope_conflict"
        assert managedenv.active_generation(project) == active
        assert list(managedenv.generations(project)) == [active]
        assert _installed_version(project) == {
            "tavotto-test-beta": "1.0",
            "tavotto-test-alpha": "1.0",
        }

    def test_the_scope_that_owns_the_install_is_not_in_conflict_with_itself(self, two_scopes):
        """同一作用域里用户改了自己的声明是正常升级，不是互斥。"""
        project = two_scopes
        (project / "a" / "requirements.txt").write_text(f"{BETA[0]}>=2\n", encoding="utf-8")
        plan, _kind, _py = deprepair.joint_plan_for(project, "a/figure.py")
        assert plan.status != "blocked"
        assert not [b for b in plan.blocked if b["code"] == "dependency_scope_conflict"]

    def test_the_gate_offers_a_switch_with_its_own_larger_impact(self, two_scopes):
        project = two_scopes
        offer = deprepair.preparation_offer(project, "b/figure.py")
        assert offer["plan"]["status"] == "blocked"
        assert offer["impact"] is None  # 并入不可授权
        switch = offer["scope_switch"]
        assert sorted(switch["requirements"]) == sorted([f"{BETA[0]}>=2", GAMMA[0]])
        impact = switch["impact"]
        assert impact["scope_policy"] == "switch"
        assert impact["changes"] == ["tavotto-test-beta"]  # A 依赖的 1.0 不在了
        assert impact["drops"] == ["tavotto-test-alpha"]  # A 独有的包不再 active
        assert impact["creates_environment"] is False
        # 摘要含这些：同一个集合但不"换"的计划，摘要不同（更大的影响不能借用较小的同意）
        assert switch["impact_digest"] == deprepair.impact_digest(impact)

    def test_switching_to_this_scope_is_one_atomic_new_generation(self, two_scopes):
        project = two_scopes
        old_gen = managedenv.active_generation(project)
        offer = deprepair.preparation_offer(project, "b/figure.py")
        got = deprepair.start_confirmed(
            project,
            "b/figure.py",
            offer["scope_switch"]["impact_digest"],
            scope_policy=deprepair.SCOPE_POLICY_SWITCH,
        )
        assert got["started"] is True
        rec = wait_for(got["plan_id"])
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert rec["impact"]["scope_policy"] == "switch"
        new_gen = managedenv.active_generation(project)
        assert new_gen and new_gen != old_gen
        python = managedenv.python_of(project)
        out = deprepair._run(
            [python, "-c", "import importlib.metadata as m; print(m.version('tavotto-test-beta'))"],
            120,
        )
        assert out[1].strip() == "2.0"
        # A 独有的 alpha 不在新一代里（换成本作用域 = 只装本作用域的集合，不是并集）
        assert not _importable(python, ALPHA[1])
        # 账换成了 B 的：beta 2.0 + gamma，都记在 b 名下；A 的 beta 1.0 不在账上
        ledger = {e["distribution"]: e for e in managedenv.ledger_entries(project)}
        assert {e["scope"] for e in ledger.values()} == {"b"}
        assert _installed_version(project) == {
            "tavotto-test-beta": "2.0",
            "tavotto-test-gamma": "1.0",
        }
        # 乒乓是显式的：现在轮到 A 与 B 互斥了（A 要 beta<2），同样 blocked + 能换回去
        back, _kind, _py = deprepair.joint_plan_for(project, "a/figure.py")
        assert back.status == "blocked"
        assert back.blocked[-1]["conflicts"][0]["installed_for"] == "b"

    def test_a_failed_switch_leaves_the_active_generation_and_the_ledger_alone(self, two_scopes):
        project = two_scopes
        active = managedenv.active_generation(project)
        ledger_before = managedenv.ledger_entries(project)
        # B 多声明了 wheelhouse 里没有的包：切换这一代装不出来
        (project / "b" / "requirements.txt").write_text(
            f"{BETA[0]}>=2\n{GAMMA[0]}\ntavotto-test-sentinel-absent\n", encoding="utf-8"
        )
        (project / "b" / "figure.py").write_text(
            (project / "b" / "figure.py").read_text("utf-8")
            + "import tavotto_test_sentinel_absent\n",
            encoding="utf-8",
        )
        offer = deprepair.preparation_offer(project, "b/figure.py")
        got = deprepair.start_confirmed(
            project,
            "b/figure.py",
            offer["scope_switch"]["impact_digest"],
            scope_policy=deprepair.SCOPE_POLICY_SWITCH,
        )
        rec = wait_for(got["plan_id"])
        assert rec["state"] == deprepair.STATE_FAILED and rec["code"] == deprepair.ERROR_NOT_FOUND
        assert managedenv.active_generation(project) == active
        assert managedenv.ledger_entries(project) == ledger_before  # 账没动

    def test_only_the_managed_environment_can_be_switched(self, tmp_path, house):
        """「换成本作用域」重建的是 Tavotto 自己的受管环境；用户自己的 venv 不能被整个换掉。"""
        from support.dependency_repair import real_venv
        from tavotto.engine import projectenv

        project = tmp_path / "solo"
        project.mkdir()
        _scope(project, "b", f"{BETA[0]}>=2\n", [BETA[1]])
        venv = real_venv(project)
        projectenv.remember(
            project,
            projectenv.interpreter_of(venv),
            automatic=False,
            trigger=projectenv.TRIGGER_RECOMMENDED,
        )
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "b/figure.py", scope_policy="switch")
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED
        with pytest.raises(deprepair.RepairError):
            deprepair.create_joint_plan(project, "b/figure.py", scope_policy="whatever")

    def test_a_subfolder_as_its_own_project_has_its_own_environment(self, two_scopes, tmp_path):
        """出路二：把 `b/` 当独立项目打开——自己的受管环境，不撞 A 的账，也不动 A 的环境。"""
        project = two_scopes
        active_a = managedenv.active_generation(project)
        ledger_a = managedenv.ledger_entries(project)
        sub = tmp_path / "paper-b"
        sub.mkdir()
        (sub / "requirements.txt").write_text(
            (project / "b" / "requirements.txt").read_text("utf-8"), encoding="utf-8"
        )
        (sub / "figure.py").write_text(
            (project / "b" / "figure.py").read_text("utf-8"), encoding="utf-8"
        )
        plan = deprepair.create_joint_plan(sub, "figure.py")
        assert plan.creates_environment is True
        assert sorted(plan.requirements) == sorted([f"{BETA[0]}>=2", GAMMA[0]])
        deprepair.prepare_async(plan.plan_id)
        assert wait_for(plan.plan_id)["state"] == deprepair.STATE_DONE
        assert managedenv.active_generation(project) == active_a
        assert managedenv.ledger_entries(project) == ledger_a
        assert _installed_version(sub) == {"tavotto-test-beta": "2.0", "tavotto-test-gamma": "1.0"}

    def test_a_constraint_that_the_other_scope_s_version_violates_is_caught_before_pip(
        self, two_scopes
    ):
        """约束也算：B 的 `constraints.txt` 要 `beta<1.5`，A 装的是 1.0 满足、不冲突；要 `beta>1.5` 才互斥。"""
        project = two_scopes
        (project / "b" / "requirements.txt").write_text(f"{GAMMA[0]}\n", encoding="utf-8")
        (project / "b" / "figure.py").write_text(
            f"import {GAMMA[1]}\nimport matplotlib\n", encoding="utf-8"
        )
        (project / "b" / "constraints.txt").write_text(f"{BETA[0]}>1.5\n", encoding="utf-8")
        plan, _kind, _py = deprepair.joint_plan_for(project, "b/figure.py")
        assert plan.status == "blocked"
        conflict = next(b for b in plan.blocked if b["code"] == "dependency_scope_conflict")
        assert conflict["conflicts"][0]["via"] == "constraint"
        (project / "b" / "constraints.txt").write_text(f"{BETA[0]}<1.5\n", encoding="utf-8")
        plan, _kind, _py = deprepair.joint_plan_for(project, "b/figure.py")
        assert plan.status == "ready"


class TestSessionReport:
    @needs_worker
    def test_the_session_shows_the_conflict_and_the_switch_as_one_explicit_choice(
        self, client, two_scopes
    ):
        """会话里：检查项 blocked（`dependency_scope_conflict`）、没有 `run`、一条 `dependency_scope_choice` 要求、一个
        `prepare_dependencies` 动作（它的影响说清 `scope_policy=switch` 与 changes）；确认后新一代 active、可运行。"""
        from tavotto import app as m
        from tavotto.engine import prepsession

        prepsession.SESSIONS.reset_for_tests()
        project = two_scopes
        m.open_project(str(project))
        try:
            resp = client.post("/api/engine/preparation-sessions", json={"script": "b/figure.py"})
            report = resp.get_json()
            (dep,) = [c for c in report["checks"] if c["id"] == "dependencies"]
            assert dep["status"] == "blocked" and dep["code"] == "dependency_scope_conflict"
            assert report["phase"] == "action_required"
            assert report["outcome"] == {"kind": "blocked", "code": "dependency_scope_conflict"}
            kinds = sorted(a["kind"] for a in report["actions"])
            assert kinds == ["prepare_dependencies", "recheck"]  # 没有 run
            (choice,) = [
                r for r in report["requirements"] if r["kind"] == "dependency_scope_choice"
            ]
            assert choice["payload"]["conflicts"][0]["installed_for"] == "a"
            assert choice["payload"]["options"] == ["switch_scope", "separate_project"]
            assert choice["payload"]["switch"]["changes"] == ["tavotto-test-beta"]
            action = next(a for a in report["actions"] if a["kind"] == "prepare_dependencies")
            assert action["impact"]["scope_policy"] == "switch"
            assert action["impact"]["changes"] == ["tavotto-test-beta"]
            text = json.dumps(report, ensure_ascii=False)
            assert str(project) not in text  # 报告里没有机器路径
            sid = report["session_id"]
            old_gen = managedenv.active_generation(project)
            go = client.post(
                f"/api/engine/preparation-sessions/{sid}/actions",
                json={
                    "action_id": action["id"],
                    "expected_config_revision": report["config_revision"],
                    "impact_digest": action["impact"]["impact_digest"],
                },
            )
            assert go.status_code == 202, go.get_json()
            import time

            deadline = time.time() + 300
            while True:
                now = client.get(f"/api/engine/preparation-sessions/{sid}").get_json()
                if now["phase"] == "ready_to_run":
                    break
                assert time.time() < deadline, now
                time.sleep(0.1)
            assert managedenv.active_generation(project) != old_gen
            assert now["dependency_delta"]["already_installed"] == sorted(
                [f"{BETA[0]}>=2", GAMMA[0]]
            )
            assert now["dependency_delta"]["added"] == []
        finally:
            for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(project)]:
                m.close_project(pid, wait=True)
            prepsession.SESSIONS.reset_for_tests()
