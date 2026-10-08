"""T06：依赖安装的授权绑定**实际影响摘要**，而不是某个 plan_id；认领幂等；采用与安装互斥；失败现场进任务诊断。

四节：

① 摘要本身（`deprepair.impact_of` / `impact_digest`）：说的是「装哪一个具体集合、往哪个环境（含代）、写入范围、
  能不能回滚」，不含进度 / 文案 / 计划 id / 有效期 / 机器路径；跑前的门显示的摘要与绑定出的计划逐字相同。
② 认领：摘要对不上 → `dependency_impact_changed`、认领之前、零副作用；同一份摘要已有作业在跑 → 认领**原作业**
  （不起第二个 pip）；单包修复同样只认领一次。
③ 采用 / 选回默认 与安装互斥，期间用户采用了别的环境的旧计划不再有效（安装结束会把结果记成项目的环境）。
④ 终局进 `taskdiag` 的白名单投影：失败的现场不被后来的成功重试改写，秘密 / 路径 / 报错原文不出门。

快速用例用「会停住的假事务」（`_run_generation` 替身）证明认领 / 互斥的本地状态；真事务（真 venv、真 pip、
离线 wheelhouse）的覆盖在 `test_dependency_transaction.py`，④ 与 `test_preparation_session_dependencies.py` 里
各有一条走真实安装入口。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker, real_venv, wait_for
from tavotto.engine import (
    depplan,
    deprepair,
    envlease,
    managedenv,
    pool as engine_pool,
    projectenv,
    taskdiag,
)

pytest_plugins = ("support.dependency_repair",)

ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
BETA = ("tavotto-test-beta", "tavotto_test_beta")
GAMMA = ("tavotto-test-gamma", "tavotto_test_gamma")
#: wheelhouse 里没有的包：真 pip 离线找不到它（`dependency_not_found`）
ABSENT = ("tavotto-test-sentinel-absent", "tavotto_test_sentinel_absent")


@pytest.fixture(autouse=True)
def _clean(clean_state):
    envlease.reset_for_tests()
    depplan.reset_cache()
    taskdiag.reset_for_tests()
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()
    taskdiag.reset_for_tests()


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    for name, import_name in (ALPHA, BETA, GAMMA):
        build_wheel(dest, name=name, import_name=import_name, version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _project(tmp_path, *pairs, name: str = "paper", extra_script: str = "") -> Path:
    """声明并 import 了 `pairs` 里每个包的项目（requirements.txt + figure.py）。"""
    proj = tmp_path / name
    proj.mkdir()
    _declare(proj, *pairs, extra_script=extra_script)
    return proj


def _declare(proj: Path, *pairs, extra_script: str = "") -> None:
    (proj / "requirements.txt").write_text(
        "".join(f"{dist}\n" for dist, _imp in pairs), encoding="utf-8"
    )
    imports = "".join(f"import {imp}\n" for _dist, imp in pairs)
    (proj / "figure.py").write_text(
        f"{imports}{extra_script}import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\nax.plot([0, 1], [0, 1])\nfig.savefig('Fig1.pdf')\n",
        encoding="utf-8",
    )


def _adopt(project: Path, venv: Path) -> None:
    assert projectenv.remember(
        project,
        projectenv.interpreter_of(venv),
        automatic=False,
        trigger=projectenv.TRIGGER_RECOMMENDED,
    )


@pytest.fixture
def held_generation(monkeypatch):
    """会停住的假「换代事务」：`prepare()` 的认领 / 指纹 / 选择复核都是真的，只有真正建 venv + pip 那一步被换成
    「记一次调用 → 发 installing → 等放行 → 发 done」。`calls` 是「事务被起了几次」的独立计数。"""
    gate = threading.Event()
    calls: list[str] = []

    def fake(job, cancel_ev):
        calls.append(job.progress_id)
        job.emit(deprepair.STATE_INSTALLING)
        assert gate.wait(60), "测试没有放行"
        return job.emit(deprepair.STATE_DONE, result={"ok": True, "activated": True})

    monkeypatch.setattr(deprepair, "_run_generation", fake)
    yield gate, calls
    gate.set()
    # 线程都收完再撤替身：晚到的作业线程会去调"此刻的" `_run_generation`，撞进下一个用例的计数
    _wait_until(lambda: not deprepair._running)


def _wait_until(predicate, timeout: float = 30.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("条件没有在限期内成立")


# ===========================================================================
# ① 摘要本身
# ===========================================================================
def test_every_plan_id_can_be_a_task_diagnostic_reference():
    """计划 id 是终局快照的引用：首字符必须是字母数字（`taskdiag.ident`）。裸 `token_urlsafe` 约 1/32 以 `-` / `_`
    开头，那些计划的失败现场会悄悄存不进去、按 id 取不回来。"""
    for _ in range(2000):
        assert taskdiag.ident(deprepair.new_plan_id()) is not None


class TestImpactDigest:
    def test_the_digest_names_what_will_happen_not_which_plan(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ALPHA, BETA)
        a = deprepair.create_joint_plan(project, "figure.py")
        b = deprepair.create_joint_plan(project, "figure.py")
        # 两份计划：id、有效期各不相同；要发生的事相同 → 同一个摘要（授权跟着事，不跟着 id）
        assert a.plan_id != b.plan_id
        assert a.impact_digest == b.impact_digest and a.impact == b.impact
        impact = a.impact
        assert impact["installs"] == sorted(a.requirements) and len(impact["installs"]) == 2
        assert impact["target_kind"] == deprepair.TARGET_MANAGED
        assert impact["scope"] == deprepair.SCOPE_MANAGED_GENERATION
        assert impact["writes"] == ["tavotto_managed_environment"]
        assert impact["modifies_user_environment"] is False
        assert impact["rollback"] == deprepair.ROLLBACK_GENERATION_ATOMIC
        assert impact["network_required"] is True and impact["creates_environment"] is True
        # 公开形态不含机器路径：环境只给不透明引用
        text = json.dumps(a.to_payload(), ensure_ascii=False)
        assert str(tmp_path) not in text and impact["environment_ref"] in text
        # 载荷里带着它们，界面 / 会话据此展示与回显
        assert a.to_payload()["impact_digest"] == a.impact_digest

    def test_the_set_the_target_and_the_scope_each_change_the_digest(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ALPHA, BETA)
        base = deprepair.create_joint_plan(project, "figure.py")
        # 集合变了 → 摘要变（旧同意不覆盖新增的包）
        _declare(project, ALPHA, BETA, GAMMA)
        wider = deprepair.create_joint_plan(project, "figure.py")
        assert wider.impact_digest != base.impact_digest
        assert set(wider.impact["installs"]) - set(base.impact["installs"]) == {GAMMA[0]}
        # 目标变了（项目自己的 venv，原地）→ 摘要变，且写入范围 / 回滚如实不同
        venv = real_venv(project)
        _adopt(project, venv)
        inplace = deprepair.create_joint_plan(project, "figure.py")
        assert inplace.target_kind == deprepair.TARGET_PROJECT_VENV
        assert inplace.impact_digest != wider.impact_digest
        assert inplace.impact["scope"] == deprepair.SCOPE_PROJECT_VENV_IN_PLACE
        assert inplace.impact["modifies_user_environment"] is True
        assert inplace.impact["writes"] == ["user_environment"]
        assert inplace.impact["rollback"] == deprepair.ROLLBACK_NONE

    def test_progress_text_and_unrelated_file_changes_do_not_change_it(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ALPHA)
        before = deprepair.create_joint_plan(project, "figure.py")
        # 脚本里加一行与依赖无关的注释：规划输入的指纹变了，要装的事没变
        (project / "figure.py").write_text(
            "# 作者：测试\n" + (project / "figure.py").read_text("utf-8"), encoding="utf-8"
        )
        after = deprepair.create_joint_plan(project, "figure.py")
        assert after.joint["inputs_digest"] != before.joint["inputs_digest"]
        assert after.impact_digest == before.impact_digest
        # 进度记录（文案 / 阶段 / 日志）不在摘要里
        deprepair._emit(after.plan_id, deprepair.STATE_INSTALLING, None, joint=after)
        assert deprepair.progress(after.plan_id)["impact_digest"] == before.impact_digest

    def test_the_environment_generation_is_part_of_what_was_confirmed(
        self, tmp_path, house, offline_managed_env
    ):
        """装完一代之后，同一份"还缺的"意图装到的是**另一个环境代**：摘要的环境引用变了（旧同意不继承）。"""
        project = _project(tmp_path, ALPHA, BETA)
        first = deprepair.create_joint_plan(project, "figure.py")
        deprepair.prepare_async(first.plan_id)
        assert wait_for(first.plan_id)["state"] == deprepair.STATE_DONE
        _declare(project, ALPHA, BETA, GAMMA)
        second = deprepair.create_joint_plan(project, "figure.py")
        assert second.requirements == (GAMMA[0],)
        assert second.impact["environment_ref"] != first.impact["environment_ref"]
        assert second.impact["creates_environment"] is False

    def test_the_gate_shows_exactly_the_impact_the_bound_plan_will_have(
        self, tmp_path, house, offline_managed_env
    ):
        """跑前的门 / 准备会话展示的摘要与 `create_joint_plan` 绑定出的计划是同一个函数、同一组输入：用户确认的
        就是执行前比的那一份（否则每次认领都会被判"变了"）。受管与项目 venv 两种目标各验一遍。"""
        project = _project(tmp_path, ALPHA, BETA)
        offer = deprepair.preparation_offer(project, "figure.py")
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert offer["impact"] == plan.impact and offer["impact_digest"] == plan.impact_digest
        venv = real_venv(project)
        _adopt(project, venv)
        offer = deprepair.preparation_offer(project, "figure.py")
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.target_kind == deprepair.TARGET_PROJECT_VENV
        assert offer["impact"] == plan.impact and offer["impact_digest"] == plan.impact_digest

    def test_a_private_python_download_is_part_of_what_was_confirmed(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """没有完整 Python 要先供应私有 Python 时，下载（身份 / 来源 / 字节数）进摘要；门显示的与计划一致，字节数变了摘要就变。"""
        payload = {
            "required": True,
            "id": "py-test",
            "version": "3.12.1",
            "origin": "download",
            "download_bytes": 123456,
        }
        monkeypatch.setattr(deprepair, "_private_python_offer", lambda: dict(payload))
        project = _project(tmp_path, ALPHA)
        offer = deprepair.preparation_offer(project, "figure.py")
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.impact["private_python"] == {
            "id": "py-test",
            "version": "3.12.1",
            "origin": "download",
            "download_bytes": 123456,
        }
        assert offer["impact_digest"] == plan.impact_digest
        payload["download_bytes"] = 999
        assert deprepair.create_joint_plan(project, "figure.py").impact_digest != plan.impact_digest

    def test_a_plan_that_cannot_be_authorised_has_no_impact(
        self, tmp_path, house, offline_managed_env
    ):
        """没缺的 / blocked 的计划没有可授权的影响：门不给摘要（没有东西可确认）。"""
        project = _project(tmp_path)  # 什么都不 import
        offer = deprepair.preparation_offer(project, "figure.py")
        assert offer["plan"]["status"] == "nothing_needed"
        assert offer["impact"] is None and offer["impact_digest"] == ""

    def test_a_new_kind_of_impact_can_never_inherit_an_old_consent(self, tmp_path):
        """版本在摘要里：以后新增一类影响（写入范围 / 出网 / 权限）加一个版本号，旧版本算出的摘要永远对不上。"""
        kw = dict(
            target_kind=deprepair.TARGET_MANAGED,
            requirements=("a",),
            constraints=(),
            require_hashes=False,
            adapter=(),
            groups=("requirements.txt",),
            creates_environment=True,
            private_python=None,
            env_fingerprint="fp",
        )
        impact = deprepair.impact_of(**kw)
        assert impact["impact_version"] == deprepair.IMPACT_VERSION
        newer = dict(impact, impact_version=deprepair.IMPACT_VERSION + 1)
        assert deprepair.impact_digest(newer) != deprepair.impact_digest(impact)
        # 私有 Python 的下载是影响的一部分：多了它摘要就变
        with_private = deprepair.impact_of(
            **{**kw, "private_python": {"required": True, "id": "x", "download_bytes": 5}}
        )
        assert deprepair.impact_digest(with_private) != deprepair.impact_digest(impact)
        # 已就位的（required=False，不下载）不算影响
        present = deprepair.impact_of(**{**kw, "private_python": {"required": False, "id": "x"}})
        assert deprepair.impact_digest(present) == deprepair.impact_digest(impact)

    def test_the_delta_between_two_impacts_is_only_what_changed(self):
        before = {"installs": ["a", "b"], "target_kind": "tavotto_managed", "environment_ref": "1"}
        after = {
            "installs": ["b", "c"],
            "target_kind": "tavotto_managed",
            "environment_ref": "2",
            "modifies_user_environment": False,
        }
        delta = deprepair.impact_delta(before, after)
        assert delta["added"] == ["c"] and delta["already_installed"] == ["a"]
        assert delta["unchanged"] == ["b"] and delta["target_changed"] is True
        assert delta["scope_widened"] is False
        widened = deprepair.impact_delta(
            {"installs": ["a"]}, {"installs": ["a"], "modifies_user_environment": True}
        )
        assert widened["scope_widened"] is True


# ===========================================================================
# ② 认领
# ===========================================================================
class TestClaim:
    def test_a_wrong_digest_is_refused_before_anything_is_claimed(
        self, tmp_path, house, offline_managed_env, held_generation
    ):
        gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        plan = deprepair.create_joint_plan(project, "figure.py")
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare_async(plan.plan_id, confirmed_impact="0" * 32)
        assert err.value.code == deprepair.ERROR_IMPACT_CHANGED
        assert err.value.extra["impact_digest"] == plan.impact_digest  # 此刻的实际影响交回去
        # 零副作用：没认领、没起线程、没起事务；计划仍在，用对的摘要可以继续
        assert not deprepair.is_running(plan.plan_id) and not deprepair.installing(project)
        assert calls == []
        assert deprepair.prepare_async(plan.plan_id, confirmed_impact=plan.impact_digest) is True
        _wait_until(lambda: calls)
        gate.set()
        assert wait_for(plan.plan_id)["state"] == deprepair.STATE_DONE
        assert len(calls) == 1

    def test_a_world_that_changed_after_confirming_is_refused_and_the_plan_is_dropped(
        self, tmp_path, house, offline_managed_env, held_generation
    ):
        _gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        confirmed = deprepair.create_joint_plan(project, "figure.py").impact_digest
        deprepair._joint_plans.clear()
        _declare(project, ALPHA, BETA)  # 确认之后项目多声明了一个包
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.start_confirmed(project, "figure.py", confirmed)
        assert err.value.code == deprepair.ERROR_IMPACT_CHANGED
        assert BETA[0] in err.value.extra["impact"]["installs"]
        assert calls == [] and deprepair._joint_plans == {}  # 现算的计划也作废了，没人能拿去执行
        assert not deprepair.installing(project)

    def test_two_confirmations_of_one_impact_run_one_job_and_both_hear_the_end(
        self, tmp_path, house, offline_managed_env, held_generation
    ):
        gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        digest = deprepair.create_joint_plan(project, "figure.py").impact_digest
        deprepair._joint_plans.clear()
        heard_a: list[str] = []
        heard_b: list[str] = []
        first = deprepair.start_confirmed(
            project, "figure.py", digest, on_event=lambda s: heard_a.append(s["state"])
        )
        assert first["started"] is True and first["joined"] is False
        _wait_until(lambda: calls)
        second = deprepair.start_confirmed(
            project, "figure.py", digest, on_event=lambda s: heard_b.append(s["state"])
        )
        # 第二个标签页 / 会话认领的是**原作业**：同一个 plan_id，没有第二个计划、第二条事务
        assert second["started"] is False and second["joined"] is True
        assert second["plan_id"] == first["plan_id"]
        assert deprepair._joint_plans.keys() <= {first["plan_id"]}
        gate.set()
        assert wait_for(first["plan_id"])["state"] == deprepair.STATE_DONE
        _wait_until(lambda: "done" in heard_b)
        assert len(calls) == 1
        assert heard_a[-1] == "done" and heard_b[-1] == "done"
        # 作业结束之后再确认同一份摘要是新的一次明确请求，不是"原作业"
        assert deprepair.installing(project) is False

    def test_a_job_that_ended_between_compare_and_claim_is_not_silently_replaced(
        self, tmp_path, house, offline_managed_env, held_generation, monkeypatch
    ):
        """比较到认领之间别人抢先认领了同一份摘要：我们现算的那份计划作废，认领原作业，不起第二个。"""
        gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        digest = deprepair.create_joint_plan(project, "figure.py").impact_digest
        deprepair._joint_plans.clear()
        real_create = deprepair.create_joint_plan
        holder: dict = {}

        def create_then_lose_the_race(*a, **kw):
            plan = real_create(*a, **kw)
            # 我们刚算完计划、还没认领：另一个标签页先认领了同一份摘要
            holder["other"] = deprepair._claim(
                "someone-else", project_id=plan.project_id, digest=plan.impact_digest
            )
            return plan

        monkeypatch.setattr(deprepair, "create_joint_plan", create_then_lose_the_race)
        got = deprepair.start_confirmed(project, "figure.py", digest)
        assert holder["other"] is True
        assert (
            got["started"] is False and got["joined"] is True and got["plan_id"] == "someone-else"
        )
        assert calls == [] and deprepair._joint_plans == {}
        deprepair._release("someone-else")
        gate.set()

    def test_a_single_package_plan_is_claimed_once(self, project, monkeypatch):
        """单包修复（运行后那条路）早先没有 `_claim`：两个标签页点同一个「安装」会各装一遍。"""
        real_venv(project)
        (project / "requirements.txt").write_text("tavotto-test-missing-dep\n", encoding="utf-8")
        plan = deprepair.create_plan(
            str(project),
            "figure.py",
            "tavotto_test_missing_dep",
            target_kind=deprepair.TARGET_PROJECT_VENV,
        )
        gate = threading.Event()
        calls: list[str] = []

        def held(p, env_key, on_event, cancel_ev):
            calls.append(p.plan_id)
            assert gate.wait(60)
            return {"ok": True}

        monkeypatch.setattr(deprepair, "_run_install", held)
        assert deprepair.install_async(plan.plan_id) is True
        _wait_until(lambda: calls)
        assert deprepair.install_async(plan.plan_id) is False  # 同一份计划只认领一次
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.install(plan.plan_id)
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED
        gate.set()
        _wait_until(lambda: not deprepair.is_running(plan.plan_id))
        assert len(calls) == 1

    def test_another_install_holding_the_environment_is_a_conflict_not_a_second_pip(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """D07 的机制面：别人占着这个环境（envlease）→ 本次安装报忙，一个 venv 都不建；占用方不受影响。"""
        project = _project(tmp_path, ALPHA)
        plan = deprepair.create_joint_plan(project, "figure.py")
        key = deprepair._env_key(deprepair.TARGET_MANAGED, "", str(project))
        built: list[str] = []
        real_create = managedenv.create_generation_venv
        monkeypatch.setattr(
            managedenv,
            "create_generation_venv",
            lambda *a, **k: (built.append("x"), real_create(*a, **k))[1],
        )
        with engine_pool.mutating_environment(key, "", shutdown=False):
            with pytest.raises(deprepair.RepairError) as err:
                deprepair.prepare(plan.plan_id)
            assert err.value.code == deprepair.ERROR_BUSY
            assert envlease.is_mutating_key(key)  # 占用方还占着
        assert built == [] and managedenv.python_of(project) is None


# ===========================================================================
# ③ 采用与安装互斥
# ===========================================================================
class TestAdoptionFence:
    def test_changing_the_project_environment_is_refused_while_an_install_runs(
        self, tmp_path, house, offline_managed_env, held_generation
    ):
        gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert deprepair.prepare_async(plan.plan_id) is True
        _wait_until(lambda: calls)
        wrote: list[str] = []
        with pytest.raises(envlease.EnvironmentBusy) as err:
            deprepair.unless_installing(project, lambda: wrote.append("x"))
        assert err.value.code == envlease.ENVIRONMENT_MUTATING and wrote == []
        other = tmp_path / "elsewhere"
        other.mkdir()
        # 别的项目的安装不挡这个项目（锁的粒度是项目，不是全局）
        assert deprepair.unless_installing(other, lambda: "ok") == "ok"
        gate.set()
        wait_for(plan.plan_id)
        _wait_until(lambda: not deprepair.installing(project))
        assert deprepair.unless_installing(project, lambda: wrote.append("y")) is None
        assert wrote == ["y"]

    def test_a_plan_made_before_the_user_adopted_another_environment_never_runs(
        self, tmp_path, house, offline_managed_env, held_generation
    ):
        _gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        plan = deprepair.create_joint_plan(project, "figure.py")  # 此刻项目没有任何环境决定
        venv = real_venv(project)
        _adopt(project, venv)  # 确认期间用户采用了自己的 .venv
        assert deprepair.prepare_async(plan.plan_id) is True
        _wait_until(lambda: not deprepair.is_running(plan.plan_id))
        rec = deprepair.progress(plan.plan_id)
        assert rec["state"] == deprepair.STATE_FAILED and rec["code"] == deprepair.ERROR_PLAN_STALE
        assert calls == []  # 一个字节都没装
        assert engine_pool.same_python(
            projectenv.remembered(project), projectenv.interpreter_of(venv)
        )

    def test_adopting_another_environment_while_the_plan_is_being_made_rejects_the_plan(
        self, tmp_path, house, offline_managed_env, held_generation, monkeypatch
    ):
        """Codex r4217232862：`create_joint_plan` 的长事实探测期间用户（另一个标签页）采用了别的环境——目标 / 影响是按
        旧决定算的，不能让晚到的 `selection_signature` 把新决定记在这份计划上而通过执行前的比对。"""
        _gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        venv = real_venv(project)
        real_plan = deprepair.depplan.plan

        def _plan_then_adopt(*args, **kwargs):
            joint = real_plan(*args, **kwargs)
            _adopt(project, venv)  # 探测 / 解析进行中，别处完成了采用
            return joint

        monkeypatch.setattr(deprepair.depplan, "plan", _plan_then_adopt)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.ERROR_PLAN_STALE
        assert not deprepair._joint_plans  # 没有发出任何可执行的计划
        assert calls == []
        # 重新开始（决定已经稳定）就能得到按新决定算的计划，且它记的是新签名
        monkeypatch.setattr(deprepair.depplan, "plan", real_plan)
        fresh = deprepair.create_joint_plan(project, "figure.py")
        assert fresh.selection == deprepair.selection_signature(project) != ()

    def test_adopting_another_environment_while_a_single_package_plan_is_made_rejects_it(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """同形状的单包计划（`create_plan`）：目标解析期间决定变了 → `repair_plan_stale`，不发计划。"""
        project = _project(tmp_path, ALPHA)
        venv = real_venv(project)
        real_offer = deprepair._private_python_offer

        def _offer_then_adopt():
            out = real_offer()
            _adopt(project, venv)
            return out

        monkeypatch.setattr(deprepair, "_private_python_offer", _offer_then_adopt)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_plan(
                project, "figure.py", ALPHA[1], target_kind=deprepair.TARGET_MANAGED
            )
        assert err.value.code == deprepair.ERROR_PLAN_STALE
        assert not deprepair._plans

    def test_a_joint_plan_records_the_selection_it_was_computed_under(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ALPHA)
        before = deprepair.selection_signature(project)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.selection == before == ()

    def test_the_install_s_own_record_does_not_make_a_sibling_plan_stale(self, tmp_path):
        """依赖安装自己写下的项目环境记录不算"用户的决定"（否则同时形成的第二份计划得到的是含糊的
        `repair_plan_stale`，而不是更具体的 `dependency_already_attempted`）。"""
        project = tmp_path / "paper"
        project.mkdir()
        assert deprepair.selection_signature(project) == ()
        projectenv.remember(
            project, "/x/py", automatic=False, trigger=deprepair.TRIGGER_DEPENDENCY_REPAIR
        )
        assert deprepair.selection_signature(project) == ()
        projectenv.remember(project, "/y/py", automatic=False, trigger="user_selected")
        assert deprepair.selection_signature(project) != ()

    @needs_worker
    def test_the_adoption_endpoint_waits_for_the_install_instead_of_overwriting_it(
        self, client, tmp_path, house, offline_managed_env, held_generation
    ):
        from tavotto import app as m

        gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        venv = real_venv(project)
        m.open_project(str(project))
        try:
            plan = deprepair.create_joint_plan(project, "figure.py")
            assert deprepair.prepare_async(plan.plan_id) is True
            _wait_until(lambda: calls)
            python = projectenv.interpreter_of(venv)
            resp = client.patch(
                "/api/engine/environment",
                json={"scope": "project", "python": python, "script": "figure.py"},
            )
            assert resp.status_code == 409
            assert resp.get_json()["code"] == envlease.ENVIRONMENT_MUTATING
            assert projectenv.remembered(project) is None  # 一个字节没写
            back = client.patch("/api/engine/environment", json={"scope": "project", "python": ""})
            assert back.status_code == 409  # 选回默认也是改项目的环境决定
            gate.set()
            wait_for(plan.plan_id)
            _wait_until(lambda: not deprepair.installing(project))
            ok = client.patch(
                "/api/engine/environment",
                json={"scope": "project", "python": python, "script": "figure.py"},
            )
            assert ok.status_code == 200, ok.get_json()
        finally:
            gate.set()
            for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(project)]:
                m.close_project(pid, wait=True)


# ===========================================================================
# E05 / C09 / O04 / D06：全局锁定、关闭项目、独立目标、用户 venv 的边界
# ===========================================================================
class TestBoundaries:
    @needs_worker
    def test_a_global_pin_means_no_install_into_an_environment_that_will_not_be_used(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """E05：全局显式解释器压着项目级决定——联合准备不形成计划、门不问、offer 不给影响；说清是谁锁的。"""
        from support.dependency_repair import WORKER_PY

        project = _project(tmp_path, ALPHA)
        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", WORKER_PY)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.ERROR_INTERPRETER_PINNED
        assert err.value.extra["pinned"]["source"] == "env_override"
        offer = deprepair.preparation_offer(project, "figure.py")
        assert offer["impact"] is None and offer["impact_digest"] == ""
        assert offer["pinned"] == {"source": "env_override", "variable": "TAVOTTO_WORKER_PYTHON"}
        assert str(WORKER_PY) not in json.dumps(offer["pinned"])  # 公开投影不带路径
        assert deprepair.gate(project, "figure.py") is None  # 不先让用户确认一次必败的安装
        assert managedenv.python_of(project) is None

    @needs_worker
    def test_a_pin_set_after_the_plan_was_made_stops_the_install_before_it_builds_anything(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        from support.dependency_repair import WORKER_PY

        project = _project(tmp_path, ALPHA)
        plan = deprepair.create_joint_plan(project, "figure.py")
        built: list[str] = []
        real_create = managedenv.create_generation_venv
        monkeypatch.setattr(
            managedenv,
            "create_generation_venv",
            lambda *a, **k: (built.append("x"), real_create(*a, **k))[1],
        )
        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", WORKER_PY)  # 确认窗口里被钉上
        deprepair.prepare_async(plan.plan_id)
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_INTERPRETER_PINNED
        assert built == [] and managedenv.python_of(project) is None

    def test_closing_the_project_does_not_cancel_a_running_install(
        self, tmp_path, house, offline_managed_env, held_generation
    ):
        """C09 / X02：关闭项目（展示层的事）不取消安装；安装只被它的 owner 显式取消。"""
        from tavotto import app as m

        gate, calls = held_generation
        project = _project(tmp_path, ALPHA)
        m.open_project(str(project))
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert deprepair.prepare_async(plan.plan_id) is True
        _wait_until(lambda: calls)
        for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(project)]:
            m.close_project(pid, wait=True)
        assert deprepair.is_running(plan.plan_id)
        assert not deprepair._cancels[plan.plan_id].is_set()  # 没有任何人替它请求取消
        assert deprepair.progress(plan.plan_id)["state"] not in deprepair.TERMINAL_STATES
        gate.set()
        assert wait_for(plan.plan_id)["state"] == deprepair.STATE_DONE  # 没有被取消

    def test_a_tool_script_s_missing_package_never_enters_an_independent_plot_s_plan(
        self, tmp_path, house, offline_managed_env
    ):
        """O04：工具脚本缺的包（同一份 requirements 里声明过）不进绘图脚本的计划，绘图目标独立可推进。"""
        project = tmp_path / "paper"
        project.mkdir()
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{GAMMA[0]}\n", encoding="utf-8")
        (project / "tool.py").write_text(f"import {GAMMA[1]}\n", encoding="utf-8")
        (project / "figure.py").write_text(
            "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('F.pdf')\n",
            encoding="utf-8",
        )
        plot = deprepair.preparation_offer(project, "figure.py")
        assert plot["plan"]["status"] == "nothing_needed" and plot["impact"] is None
        assert deprepair.gate(project, "figure.py") is None  # 绘图脚本的门放行
        tool = deprepair.preparation_offer(project, "tool.py")
        assert tool["plan"]["status"] == "ready"
        assert tool["impact"]["installs"] == [GAMMA[0]]  # 只有工具脚本自己缺的，没有 alpha

    @needs_worker
    def test_a_user_venv_install_that_imports_badly_is_not_rolled_back_and_says_so(
        self, tmp_path, monkeypatch
    ):
        """D06（用户 venv 原地）：pip 退出码 0 只是步骤结果——import 失败时环境不通过，但已装进去的包**不会**被撤回
        （不承诺完整回滚）；摘要事先说了 `none_partial_changes_possible`，失败现场里也留着，项目记录不变。"""
        house = tmp_path / "house"
        build_wheel(
            house,
            name="tavotto-test-boom",
            import_name="tavotto_test_boom",
            version="1.0",
            body="raise RuntimeError('boom at import')\n",
        )
        monkeypatch.setenv("PIP_FIND_LINKS", str(house))
        monkeypatch.setenv("PIP_NO_INDEX", "1")
        project = _project(tmp_path, ("tavotto-test-boom", "tavotto_test_boom"))
        venv = real_venv(project)
        python = projectenv.interpreter_of(venv)
        _adopt(project, venv)
        record_before = projectenv.remembered_record(project)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.impact["rollback"] == deprepair.ROLLBACK_NONE
        deprepair.prepare_async(plan.plan_id, confirmed_impact=plan.impact_digest)
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_IMPORT_STILL_FAILED
        assert rec["stage"] == deprepair.STATE_VERIFYING  # pip 步骤过了，坏在验证
        assert rec["impact"]["rollback"] == deprepair.ROLLBACK_NONE
        # 没有回滚：包还在用户的 venv 里
        shown = deprepair._run([python, "-m", "pip", "show", "tavotto-test-boom"], 60)
        assert shown[0] == 0
        # 项目的环境记录没被这次失败的安装改写
        assert projectenv.remembered_record(project) == record_before
        snap = deprepair.diagnostic_projection(rec)
        assert snap["impact"]["rollback"] == "none_partial_changes_possible"
        assert (
            snap["stage"]["last"] == "verifying"
            and snap["target"]["scope"] == "project_venv_in_place"
        )


# ===========================================================================
# ④ 终局进任务诊断（白名单投影）
# ===========================================================================
@needs_worker
class TestTaskDiagnostic:
    def _open(self, client, project: Path):
        from tavotto import app as m

        m.open_project(str(project))
        return m

    def _run(self, client, project: Path, *, impact_digest: str | None = None) -> str:
        plan = client.post("/api/engine/dependencies/plan", json={"script": "figure.py"})
        assert plan.status_code == 200, plan.get_json()
        payload = plan.get_json()["plan"]
        # `impact_digest` 必填（Codex r4217232854）：默认回显用户看到的（计划自己的）那份
        body = {
            "plan_id": payload["plan_id"],
            "impact_digest": payload["impact_digest"] if impact_digest is None else impact_digest,
        }
        resp = client.post("/api/engine/dependencies/prepare", json=body)
        assert resp.status_code == 200, resp.get_json()
        return payload["plan_id"]

    def test_a_failed_install_is_frozen_without_names_paths_or_error_text(
        self, client, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ABSENT, name="paper-secret-dir")
        m = self._open(client, project)
        try:
            plan_id = self._run(client, project)
            rec = wait_for(plan_id)
            assert rec["state"] == deprepair.STATE_FAILED
            assert rec["code"] == deprepair.ERROR_NOT_FOUND
            assert (
                rec["stage"] == deprepair.STATE_INSTALLING
            )  # 坏在哪一步，终态覆盖了 state 但 stage 还在
            # 旧的"原作业"契约：失败的这一代不 active，没有任何环境被切走
            assert managedenv.active_generation(project) is None
            resp = client.get(f"/api/diagnostics/task?kind=dependency&ref={plan_id}")
            assert resp.status_code == 200, resp.get_json()
            doc = resp.get_json()
            snap = doc["snapshot"]
            assert doc["collection"] == {
                "executed_anything": False,
                "current_state_included": False,
                "captured_at": doc["collection"]["captured_at"],
                "generated_at": doc["collection"]["generated_at"],
            }
            assert snap["kind"] == "dependency" and snap["outcome"] == "failed"
            assert snap["attempt_id"] == plan_id
            assert snap["error"] == {"code": "dependency_not_found"}
            assert snap["stage"]["last"] == "installing"
            assert snap["target"] == {
                "kind": "tavotto_managed",
                "scope": "managed_generation",
                "flow": "joint",
            }
            assert snap["impact"]["installs"] == 1
            assert snap["impact"]["rollback"] == "generation_atomic"
            assert snap["impact"]["modifies_user_environment"] is False
            assert snap["impact"]["digest"] == rec["impact_digest"]
            text = json.dumps(doc, ensure_ascii=False)
            # 白名单：包名、项目目录名、路径、pip 原文一个都不在
            for secret in (ABSENT[0], ABSENT[1], "paper-secret-dir", str(tmp_path), "No matching"):
                assert secret not in text, secret
        finally:
            for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(project)]:
                m.close_project(pid, wait=True)

    def test_a_later_success_never_rewrites_the_earlier_failure(
        self, client, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ABSENT, name="paper")
        m = self._open(client, project)
        try:
            failed_id = self._run(client, project)
            assert wait_for(failed_id)["state"] == deprepair.STATE_FAILED
            # 换成 wheelhouse 里有的包再来一次（同一份脚本）：新的 attempt、新的快照
            _declare(project, ALPHA)
            ok_id = self._run(client, project)
            assert wait_for(ok_id)["state"] == deprepair.STATE_DONE
            old = client.get(f"/api/diagnostics/task?kind=dependency&ref={failed_id}").get_json()
            assert old["snapshot"]["outcome"] == "failed"
            assert old["snapshot"]["error"] == {"code": "dependency_not_found"}
            assert old["snapshot"]["attempt_id"] == failed_id
            assert [a["ref"] for a in old["later_attempts"]] == [ok_id]
            new = client.get(f"/api/diagnostics/task?kind=dependency&ref={ok_id}").get_json()
            assert new["snapshot"]["outcome"] == "done" and new["retry_of"] == failed_id
            assert new["snapshot"]["result"]["activated"] is True
            # 其他项目 / 猜的 id 读不到
            assert client.get("/api/diagnostics/task?kind=dependency&ref=nope").status_code == 404
        finally:
            for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(project)]:
                m.close_project(pid, wait=True)

    def test_the_prepare_endpoint_requires_the_displayed_digest(
        self, client, tmp_path, house, offline_managed_env
    ):
        """Codex r4217232854：只带 plan_id 不行——缺 impact_digest → 400 `dependency_impact_required`，
        什么都没认领、没装；对不上仍是 409 `dependency_impact_changed`。"""
        project = _project(tmp_path, ALPHA)
        m = self._open(client, project)
        try:
            plan = client.post("/api/engine/dependencies/plan", json={"script": "figure.py"})
            payload = plan.get_json()["plan"]
            for body in (
                {"plan_id": payload["plan_id"]},
                {"plan_id": payload["plan_id"], "impact_digest": ""},
                {"plan_id": payload["plan_id"], "impact_digest": None},
                {"plan_id": payload["plan_id"], "impact_digest": 7},
            ):
                resp = client.post("/api/engine/dependencies/prepare", json=body)
                assert resp.status_code == 400, body
                assert resp.get_json()["code"] == "dependency_impact_required"
                assert not deprepair.is_running(payload["plan_id"])
            assert managedenv.python_of(project) is None
            wrong = client.post(
                "/api/engine/dependencies/prepare",
                json={"plan_id": payload["plan_id"], "impact_digest": "e" * 32},
            )
            assert wrong.status_code == 409
            assert wrong.get_json()["code"] == "dependency_impact_changed"
        finally:
            for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(project)]:
                m.close_project(pid, wait=True)

    def test_the_prepare_endpoint_checks_an_echoed_digest_before_claiming(
        self, client, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ALPHA)
        m = self._open(client, project)
        try:
            plan = client.post("/api/engine/dependencies/plan", json={"script": "figure.py"})
            payload = plan.get_json()["plan"]
            assert payload["impact_digest"] and payload["impact"]["installs"]
            bad = client.post(
                "/api/engine/dependencies/prepare",
                json={"plan_id": payload["plan_id"], "impact_digest": "f" * 32},
            )
            assert bad.status_code == 409
            body = bad.get_json()
            assert body["code"] == "dependency_impact_changed"
            assert body["impact_digest"] == payload["impact_digest"]
            assert not deprepair.is_running(payload["plan_id"])
            assert managedenv.python_of(project) is None
            # 对的摘要照常（不带摘要见 test_the_prepare_endpoint_requires_the_displayed_digest）
            good = client.post(
                "/api/engine/dependencies/prepare",
                json={"plan_id": payload["plan_id"], "impact_digest": payload["impact_digest"]},
            )
            assert good.status_code == 200 and good.get_json()["started"] is True
            assert wait_for(payload["plan_id"])["state"] == deprepair.STATE_DONE
        finally:
            for pid in [p for p, c in list(m.PROJECTS.items()) if str(c.path) == str(project)]:
                m.close_project(pid, wait=True)


# ===========================================================================
# 执行依赖变更的入口：一律要求并校验用户看到的影响摘要（Codex r4217232854 / r4217992305）
# ===========================================================================
class TestEveryExecutionEntryRequiresTheDigest:
    """同一类缺口抓到第二次：联合准备端点补了摘要，单包端点 `/dependency/install` 仍只认 plan_id——
    点之前次要依赖的约束变了，包名不变，装进用户没看过的约束。入口清单与逐个用例见 `reports/814-r4.md`。"""

    def _single_plan(self, client, project):
        from tavotto import app as m

        real_venv(project)
        (project / "requirements.txt").write_text("tavotto-test-missing-dep\n", encoding="utf-8")
        m.open_project(str(project))
        resp = client.post(
            "/api/engine/dependency/plan",
            json={
                "module": "tavotto_test_missing_dep",
                "script": "figure.py",
                "target": "project_venv",
            },
        )
        assert resp.status_code == 200, resp.get_json()
        return m, resp.get_json()["plan"]

    def test_the_single_package_endpoint_requires_the_displayed_digest(
        self, client, project, monkeypatch
    ):
        ran: list[str] = []
        monkeypatch.setattr(
            deprepair, "_run_install", lambda p, *a, **k: ran.append(p.plan_id) or {"ok": True}
        )
        m, plan = self._single_plan(client, project)
        pid = plan["plan_id"]
        assert plan["impact_digest"]
        # 缺 / 空 / null / 非字符串：400，什么都没认领、没执行
        for body in (
            {"plan_id": pid},
            {"plan_id": pid, "impact_digest": ""},
            {"plan_id": pid, "impact_digest": None},
            {"plan_id": pid, "impact_digest": 7},
        ):
            resp = client.post("/api/engine/dependency/install", json=body)
            assert resp.status_code == 400, body
            assert resp.get_json()["code"] == deprepair.ERROR_IMPACT_REQUIRED
            assert not deprepair.is_running(pid)
        # 用户看到的是另一份（例如次要依赖约束 beta<2 → beta>=2 之前的摘要）：409，仍然什么都没执行
        resp = client.post(
            "/api/engine/dependency/install", json={"plan_id": pid, "impact_digest": "d" * 32}
        )
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["code"] == deprepair.ERROR_IMPACT_CHANGED
        assert (
            body["impact_digest"] == plan["impact_digest"]
        )  # 把此刻的实际影响交回去，界面据此重新确认
        assert not deprepair.is_running(pid)
        assert ran == []
        # 对的摘要才执行
        ok = client.post(
            "/api/engine/dependency/install",
            json={"plan_id": pid, "impact_digest": plan["impact_digest"]},
        )
        assert ok.status_code == 200 and ok.get_json()["started"] is True
        _wait_until(lambda: ran)
        _wait_until(lambda: not deprepair.is_running(pid))

    def test_every_dependency_executing_call_in_the_entry_layers_binds_the_digest(self):
        """结构性不变量：app.py 与 MCP 桥里每一处调用 `install_async` / `prepare_async` / `prepare(` 的地方都必须
        传 `confirmed_impact=`；起依赖作业的 `start_confirmed` 本身以摘要为必填位置参数。新增入口忘了摘要 → 红。"""
        import ast

        root = Path(deprepair.__file__).resolve().parents[3]
        files = [
            root / "src" / "tavotto" / "app.py",
            root / "codex-plugin" / "mcp" / "tavotto_mcp" / "bridge.py",
        ]
        guarded = {"install_async", "prepare_async", "prepare", "install", "run_prepare"}
        found: list[str] = []
        bad: list[str] = []
        for path in files:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                recv = node.func.value
                if not (isinstance(recv, ast.Name) and recv.id == "engine_deprepair"):
                    continue
                if node.func.attr not in guarded:
                    continue
                found.append(f"{path.name}:{node.func.attr}")
                if "confirmed_impact" not in {k.arg for k in node.keywords}:
                    bad.append(f"{path.name}:{node.lineno} {node.func.attr}")
        # 钉住扫描没有落空：三个已知调用点都被看到了
        assert sorted(found) == sorted(
            ["app.py:install_async", "app.py:prepare_async", "bridge.py:prepare"]
        ), found
        assert not bad, f"这些入口执行依赖变更却没绑定摘要: {bad}"


# ===========================================================================
# 结构性守卫：披露所用的输入 == 交给 pip 的输入（Codex #814 r4218254708，同一主题第四次）
# ===========================================================================
def _assert_impact_covers(impact: dict, *, requirements, constraints, require_hashes, adapter=()):
    """执行端实际收到的需求 / 约束 / hash 模式 / adapter，披露里一条不少、一条不多（约束与 hash 模式严格相等）。"""
    assert set(requirements) <= set(impact["installs"]), (requirements, impact["installs"])
    assert sorted(constraints) == impact["constraints"], (constraints, impact["constraints"])
    assert bool(require_hashes) is impact["require_hashes"]
    assert set(adapter) <= set(impact["adapter"]), (adapter, impact["adapter"])


class TestDisclosureIsDerivedFromWhatPipReceives:
    """每种计划类型各一条：把执行端**真正交给** `_GenerationJob` / `write_plan_files` / `_pip_install` 的东西截下来，
    与该计划 `impact` 逐项对。约束放进计划里（不只是空集），否则"漏了约束"这种漂移测不出来。"""

    CONSTRAINT = "tavotto-test-beta>=1"

    @staticmethod
    def _generation_inputs(project, job):
        final = deprepair.generation_requirements(
            project, job.delta, hash_mode=job.require_hashes, replace=job.replace_ledger
        )
        return final

    def _capture_generation(self, monkeypatch, attr):
        seen = {}

        def fake(job, *a, **kw):
            seen["job"] = job
            raise deprepair.RepairError("captured")

        monkeypatch.setattr(deprepair, attr, fake)
        return seen

    def test_joint_plan_into_a_managed_generation(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        import dataclasses

        project = _project(tmp_path, ALPHA, BETA)
        plan = deprepair.create_joint_plan(project, "figure.py")
        plan = dataclasses.replace(plan, constraints=(self.CONSTRAINT,))
        deprepair._joint_plans[plan.plan_id] = plan
        seen = self._capture_generation(monkeypatch, "_run_generation")
        with pytest.raises(deprepair.RepairError, match="captured"):
            deprepair.prepare(plan.plan_id)
        job = seen["job"]
        final = self._generation_inputs(str(project), job)
        _assert_impact_covers(
            plan.impact,
            requirements=final,
            constraints=job.constraints,
            require_hashes=job.require_hashes,
            adapter=deprepair.depplan.ADAPTER_REQUIREMENTS,
        )
        assert plan.impact["constraints"] == [self.CONSTRAINT]

    def test_single_package_plan_into_a_managed_generation(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        import dataclasses

        project = _project(tmp_path, ALPHA, BETA)
        plan = deprepair.create_plan(
            str(project), "figure.py", ALPHA[1], target_kind=deprepair.TARGET_MANAGED
        )
        assert plan.widened is not None
        plan = dataclasses.replace(
            plan, widened=dataclasses.replace(plan.widened, constraints=(self.CONSTRAINT,))
        )
        deprepair._plans[plan.plan_id] = plan
        seen = self._capture_generation(monkeypatch, "_run_generation_locked")
        with pytest.raises(deprepair.RepairError, match="captured"):
            deprepair._run_install(plan, "k", None, threading.Event())
        job = seen["job"]
        final = self._generation_inputs(str(project), job)
        _assert_impact_covers(
            plan.impact,
            requirements=final,
            constraints=job.constraints,
            require_hashes=job.require_hashes,
            adapter=deprepair.depplan.ADAPTER_REQUIREMENTS,
        )
        assert plan.impact["constraints"] == [self.CONSTRAINT]

    def test_joint_plan_in_place_in_the_project_venv(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        import dataclasses

        project = _project(tmp_path, ALPHA, BETA)
        _adopt(project, real_venv(project))
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.target_kind == deprepair.TARGET_PROJECT_VENV
        plan = dataclasses.replace(plan, constraints=(self.CONSTRAINT,))
        seen: dict = {}

        def fake(build_argv, python, cancel_ev, *a, **kw):
            argv = build_argv(None)
            seen["requirements"] = Path(argv[argv.index("-r") + 1]).read_text().split()
            seen["constraints"] = Path(argv[argv.index("-c") + 1]).read_text().split()
            seen["require_hashes"] = "--require-hashes" in argv
            raise deprepair.RepairError("captured")

        monkeypatch.setattr(deprepair, "_run_pip_install", fake)
        with pytest.raises(deprepair.RepairError, match="captured"):
            deprepair._run_joint_in_place(plan, None, threading.Event())
        _assert_impact_covers(
            plan.impact,
            requirements=seen["requirements"],
            constraints=seen["constraints"],
            require_hashes=seen["require_hashes"],
        )
        assert plan.impact["constraints"] == [self.CONSTRAINT]

    def test_single_package_plan_in_place_in_the_project_venv(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        project = _project(tmp_path, ALPHA, BETA)
        _adopt(project, real_venv(project))
        plan = deprepair.create_plan(
            str(project), "figure.py", ALPHA[1], target_kind=deprepair.TARGET_PROJECT_VENV
        )
        seen: dict = {}

        def fake(python, requirement, *a, **kw):
            seen["requirement"] = requirement
            raise deprepair.RepairError("captured")

        monkeypatch.setattr(deprepair, "_pip_install", fake)
        with pytest.raises(deprepair.RepairError, match="captured"):
            deprepair._run_install(plan, "k", None, threading.Event())
        # 原地只传一条需求、没有约束文件：披露同样只说这一条、没有约束
        _assert_impact_covers(
            plan.impact, requirements=[seen["requirement"]], constraints=[], require_hashes=False
        )

    def test_every_plan_type_exposes_one_pip_inputs_and_the_impact_reads_it(self):
        """结构：`impact` 的 requirements / constraints / hash / adapter 四项只从 `pip_inputs` 读。"""
        import inspect

        for cls in (deprepair.RepairPlan, deprepair.JointRepairPlan):
            assert isinstance(getattr(cls, "pip_inputs"), property)
            src = inspect.getsource(cls.impact.fget)
            for field in ("requirements", "constraints", "require_hashes", "adapter"):
                assert f"{field}=pi.{field}" in src, (cls.__name__, field)


class TestReplanAfterPrivatePythonMustMatchTheConfirmedInputs:
    """私有 Python 就位后按真解释器重算：重算出的 pip 输入（同一个 `PipInputs`）必须与用户确认的逐项相同，
    否则 `dependency_impact_changed`、进度终态带着此刻的实际影响与新摘要、一个字节不装（Codex #814 r4218254708 后续）。"""

    def _run(self, tmp_path, monkeypatch, replanned_constraints):
        import dataclasses

        project = _project(tmp_path, ALPHA, BETA)
        plan = deprepair.create_joint_plan(project, "figure.py")
        deprepair._joint_plans[plan.plan_id] = plan
        seen = {}

        def fake_generation(job, *a, **kw):
            seen["job"] = job
            raise deprepair.RepairError("captured")

        monkeypatch.setattr(deprepair, "_run_generation", fake_generation)
        with pytest.raises(deprepair.RepairError, match="captured"):
            deprepair.prepare(plan.plan_id)
        job = dataclasses.replace(seen["job"], provision_private=True, replan=True)
        assert job.confirmed_inputs == plan.pip_inputs and job.confirmed_impact == plan.impact
        joint, _k, _p = deprepair.joint_plan_for(project, "figure.py")
        joint = dataclasses.replace(
            joint, constraints=replanned_constraints, inputs_digest=job.inputs_digest
        )
        effects: list[str] = []
        monkeypatch.setattr(deprepair, "base_python", lambda: None)
        monkeypatch.setattr(
            deprepair, "_provision_private_base", lambda j, ev: {"python": "/x/py", "runtime": "r"}
        )
        monkeypatch.setattr(depplan, "fresh_venv_facts", lambda *a, **kw: object())
        monkeypatch.setattr(depplan, "plan", lambda *a, **kw: joint)
        for name in ("register_generation", "create_generation_venv", "fresh_generation"):
            monkeypatch.setattr(
                managedenv, name, lambda *a, _n=name, **kw: effects.append(_n) or 1 / 0
            )
        monkeypatch.setattr(
            deprepair, "_run_pip_install", lambda *a, **kw: effects.append("pip") or 1 / 0
        )
        return plan, job, effects

    def test_inputs_that_differ_from_the_disclosure_are_never_installed(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        plan, job, effects = self._run(tmp_path, monkeypatch, ("tavotto-test-beta>=1",))
        with pytest.raises(deprepair.RepairError) as err:
            deprepair._run_generation_locked(job, threading.Event(), "k")
        assert err.value.code == deprepair.ERROR_IMPACT_CHANGED
        assert effects == []  # 没登记代、没建 venv、没起 pip
        now = err.value.extra["impact"]
        assert now["constraints"] == ["tavotto-test-beta>=1"]
        assert err.value.extra["impact_digest"] == deprepair.impact_digest(now)
        assert err.value.extra["impact_digest"] != plan.impact_digest
        # 终态记着此刻的实际影响与新摘要（界面 / MCP 据此重新披露），不是用户确认的那份
        rec = deprepair.progress(plan.plan_id)
        assert (
            rec["state"] == deprepair.STATE_FAILED and rec["code"] == deprepair.ERROR_IMPACT_CHANGED
        )
        assert rec["impact_digest"] == err.value.extra["impact_digest"]

    def test_identical_inputs_go_on_to_install(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        plan, job, effects = self._run(tmp_path, monkeypatch, ())
        with pytest.raises(ZeroDivisionError):  # 走到了「登记代」这一步（被替身截断）
            deprepair._run_generation_locked(job, threading.Event(), "k")
        assert effects and effects[0] in ("fresh_generation", "register_generation")
