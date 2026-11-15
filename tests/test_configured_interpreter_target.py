"""第三种安装目标：「你为这个项目选的 Python」（ADR 0019 2026-11-15 修订）。

背景：用户为项目选了自己的系统 Python（项目外），缺包时推荐的「装到 Tavotto 管理环境」装完
worker 自检每次过不去，用户白等十次。本文件钉住：

* 判据 `deprepair.configured_interpreter`（唯一出处）的正反例；
* `deprepair.managed_selftest_failed`：最近一代自检没过才为真，之后成功建出一代就不再为真；
* `create_joint_plan` / `create_plan` 只认判据的结果，不接受任意路径；
* 准备会话：管理环境自检失败过 + 有该目标 → 动作目标是它、`InstallGrant` 拒；没失败过仍是受管（回归）；
* 真 pip 原地装进一个「用户选的、项目外」的 venv（端到端）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker, real_venv, wait_for
from tavotto.engine import (
    depplan,
    deprepair,
    envlease,
    managedenv,
    pool as engine_pool,
    prepsession,
    projectenv,
)

pytest_plugins = ("support.dependency_repair",)

ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")


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
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "paper"
    proj.mkdir()
    (proj / "requirements.txt").write_text(f"{ALPHA[0]}\n", encoding="utf-8")
    (proj / "figure.py").write_text(f"import {ALPHA[1]}\n", encoding="utf-8")
    return proj


def _fake_python(tmp_path: Path, name: str = "userpy") -> str:
    """项目外的一个「文件在」的解释器占位（判据只读设置与 stat，不执行它）。"""
    path = tmp_path / name / "bin" / "python"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    return str(path)


def _select(project: Path, python: str, *, automatic=False, trigger="user_selected") -> None:
    assert projectenv.remember(project, python, automatic=automatic, trigger=trigger)


@pytest.fixture
def resolves_to_remembered(monkeypatch):
    """判据最后一道「此刻解析出的正是它」只许纯读（`peek_project_resolution`：只 stat）：`offer` 在渲染失败的
    响应路径上不许起解释器——真解析（会对记住的解释器起 `import matplotlib` 复检、失败还会作废记录）一调就红。"""

    def _forbidden(*a, **k):
        raise AssertionError("configured_interpreter 不许调 resolve_worker_python（会起子进程）")

    monkeypatch.setattr(engine_pool, "resolve_worker_python", _forbidden)


# ---------------------------------------------------------------- 判据：configured_interpreter
class TestConfiguredInterpreterCriterion:
    def test_user_selected_outside_the_project_is_the_target(
        self, tmp_path, resolves_to_remembered
    ):
        proj, python = _project(tmp_path), _fake_python(tmp_path)
        _select(proj, python)
        assert engine_pool.same_python(deprepair.configured_interpreter(proj), python)

    def test_an_automatic_decision_is_not_the_users_choice(self, tmp_path, resolves_to_remembered):
        proj, python = _project(tmp_path), _fake_python(tmp_path)
        _select(proj, python, automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED)
        assert deprepair.configured_interpreter(proj) is None

    def test_only_the_user_selected_trigger_counts(self, tmp_path, resolves_to_remembered):
        proj, python = _project(tmp_path), _fake_python(tmp_path)
        _select(proj, python, trigger=projectenv.TRIGGER_RECOMMENDED)
        assert deprepair.configured_interpreter(proj) is None

    def test_a_python_inside_the_project_is_not_it(self, tmp_path, resolves_to_remembered):
        proj = _project(tmp_path)
        inside = proj / ".venv" / "bin" / "python"
        inside.parent.mkdir(parents=True)
        inside.write_text("#!/bin/sh\n", encoding="utf-8")
        _select(proj, str(inside))
        assert deprepair.configured_interpreter(proj) is None

    def test_a_tavotto_managed_python_is_not_it(self, tmp_path, resolves_to_remembered):
        proj = _project(tmp_path)
        TestManagedSelftestFailed._register(proj, "g1")  # 在册的一代才算「Tavotto 替它建的」
        gen = managedenv.generation_python(proj, "g1")
        gen.parent.mkdir(parents=True)
        gen.write_text("#!/bin/sh\n", encoding="utf-8")
        _select(proj, str(gen))
        assert deprepair.configured_interpreter(proj) is None

    def test_a_global_pin_hides_it(self, tmp_path, resolves_to_remembered, monkeypatch):
        proj, python = _project(tmp_path), _fake_python(tmp_path)
        _select(proj, python)
        monkeypatch.setattr(
            engine_pool, "explicit_worker_python", lambda: (python, engine_pool.SOURCE_CONFIGURED)
        )
        assert deprepair.configured_interpreter(proj) is None

    def test_it_must_be_what_the_project_resolves_to_right_now(self, tmp_path, monkeypatch):
        proj, python = _project(tmp_path), _fake_python(tmp_path)
        _select(proj, python)
        monkeypatch.setattr(
            engine_pool,
            "peek_project_resolution",
            lambda *a, **k: (sys.executable, engine_pool.SOURCE_SYSTEM),
        )
        assert deprepair.configured_interpreter(proj) is None

    def test_a_vanished_file_is_not_it(self, tmp_path, resolves_to_remembered):
        proj, python = _project(tmp_path), _fake_python(tmp_path)
        _select(proj, python)
        Path(python).unlink()
        assert deprepair.configured_interpreter(proj) is None

    def test_nothing_remembered_is_none(self, tmp_path):
        assert deprepair.configured_interpreter(_project(tmp_path)) is None


# ---------------------------------------------------------------- managed_selftest_failed
class TestManagedSelftestFailed:
    @staticmethod
    def _register(proj: Path, gen: str) -> None:
        managedenv.register_generation(
            proj,
            gen,
            requirements=[],
            constraints=[],
            identity=gen,
            base_python=sys.executable,
        )

    def test_the_latest_generation_failing_its_selftest_is_true(self, tmp_path):
        proj = _project(tmp_path)
        assert deprepair.managed_selftest_failed(proj) is False  # 没有任何一代
        self._register(proj, "g1")
        assert deprepair.managed_selftest_failed(proj) is False  # 建到一半、还没有结论
        managedenv.mark_generation(
            proj, "g1", managedenv.GEN_STATE_INCOMPLETE, managedenv.REASON_SELFTEST_FAILED
        )
        assert deprepair.managed_selftest_failed(proj) is True

    def test_other_incomplete_reasons_do_not_count(self, tmp_path):
        proj = _project(tmp_path)
        self._register(proj, "g1")
        managedenv.mark_generation(proj, "g1", managedenv.GEN_STATE_INCOMPLETE, "关键 import 失败")
        assert deprepair.managed_selftest_failed(proj) is False

    def test_a_later_successful_generation_clears_it(self, tmp_path):
        proj = _project(tmp_path)
        self._register(proj, "g1")
        managedenv.mark_generation(
            proj, "g1", managedenv.GEN_STATE_INCOMPLETE, managedenv.REASON_SELFTEST_FAILED
        )
        self._register(proj, "g2")
        managedenv.activate(proj, "g2")
        assert deprepair.managed_selftest_failed(proj) is False

    def test_the_selftest_failure_site_writes_the_same_constant(self):
        import inspect

        src = inspect.getsource(deprepair._run_generation_locked)
        assert "REASON_SELFTEST_FAILED" in src


# ---------------------------------------------------------------- 计划：只认判据
class TestPlansOnlyAcceptTheCriterionsAnswer:
    def test_joint_plan_refuses_when_nothing_qualifies(self, tmp_path, house):
        proj = _project(tmp_path)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(proj, "figure.py", target_kind=deprepair.TARGET_CONFIGURED)
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED

    def test_single_package_plan_refuses_when_nothing_qualifies(self, tmp_path, house):
        proj = _project(tmp_path)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_plan(
                proj, "figure.py", ALPHA[1], target_kind=deprepair.TARGET_CONFIGURED
            )
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED

    def test_the_new_kind_is_a_known_in_place_target(self):
        assert deprepair.TARGET_CONFIGURED in deprepair.TARGETS
        impact = deprepair.impact_of(
            target_kind=deprepair.TARGET_CONFIGURED,
            requirements=("a",),
            constraints=(),
            require_hashes=False,
            adapter=(),
            groups=(),
            creates_environment=False,
            private_python=None,
            env_fingerprint="x",
        )
        assert impact["scope"] == deprepair.SCOPE_PROJECT_VENV_IN_PLACE
        assert impact["modifies_user_environment"] is True


# ---------------------------------------------------------------- 准备会话
class _Resp(types.SimpleNamespace):
    pass


def _observe(proj: Path):
    from tavotto.engine import preparation

    sess = types.SimpleNamespace(project_root=str(proj))
    plan = types.SimpleNamespace(script="figure.py")
    result = _Resp(
        status=preparation.STATUS_ERROR,
        error={"code": prepsession._RUNTIME_MISSING_CODE, "module": ALPHA[1]},
    )
    return prepsession.SESSIONS._observe_missing(sess, plan, result)


@needs_worker
class TestPreparationSessionPicksTheTarget:
    def _setup(self, tmp_path, house, offline_managed_env, *, selftest_failed: bool):
        proj = _project(tmp_path)
        user_home = tmp_path / "userenv"
        user_home.mkdir()
        venv = real_venv(user_home, name="py")
        python = projectenv.interpreter_of(venv)
        _select(proj, str(python))
        if selftest_failed:
            managedenv.register_generation(
                proj,
                "g1",
                requirements=[],
                constraints=[],
                identity="g1",
                base_python=sys.executable,
            )
            managedenv.mark_generation(
                proj, "g1", managedenv.GEN_STATE_INCOMPLETE, managedenv.REASON_SELFTEST_FAILED
            )
        return proj, str(python)

    def test_after_a_failed_selftest_the_action_targets_the_users_python_and_grant_refuses(
        self, tmp_path, house, offline_managed_env
    ):
        proj, _python = self._setup(tmp_path, house, offline_managed_env, selftest_failed=True)
        info = _observe(proj)
        assert info["installable"] and info["target_kind"] == deprepair.TARGET_CONFIGURED
        assert info["impact"]["target_kind"] == deprepair.TARGET_CONFIGURED
        assert info["impact"]["modifies_user_environment"] is True
        offer = prepsession.SESSIONS._prepare_offer(
            types.SimpleNamespace(plan=types.SimpleNamespace(dependency_preparation={})), [], info
        )
        assert offer["target_kind"] == deprepair.TARGET_CONFIGURED
        # 改用户环境必须用户亲手点：「打开并运行」的自动授权一律拒
        assert prepsession.GRANT.refusal(info["impact"], "", 0) == "target_kind"

    def test_without_a_failed_selftest_it_stays_managed(self, tmp_path, house, offline_managed_env):
        proj, _python = self._setup(tmp_path, house, offline_managed_env, selftest_failed=False)
        info = _observe(proj)
        assert info["installable"] and info["target_kind"] == deprepair.TARGET_MANAGED
        assert info["impact"]["target_kind"] == deprepair.TARGET_MANAGED
        assert prepsession.GRANT.refusal(info["impact"], "", 0) == ""

    def test_offer_lists_it_before_managed_and_flags_the_selftest(
        self, tmp_path, house, offline_managed_env
    ):
        proj, python = self._setup(tmp_path, house, offline_managed_env, selftest_failed=True)
        offer = deprepair.offer(proj, "figure.py", ALPHA[1])
        kinds = [t["kind"] for t in offer["targets"]]
        assert kinds.index(deprepair.TARGET_CONFIGURED) < kinds.index(deprepair.TARGET_MANAGED)
        configured = next(t for t in offer["targets"] if t["kind"] == deprepair.TARGET_CONFIGURED)
        assert configured["modifies_user_environment"] is True
        assert engine_pool.same_python(configured["python"], python)
        managed = next(t for t in offer["targets"] if t["kind"] == deprepair.TARGET_MANAGED)
        assert managed["selftest_failed"] is True


# ---------------------------------------------------------------- 真 pip 端到端
@needs_worker
class TestRealInPlaceInstall:
    def test_installs_into_the_users_outside_python_and_keeps_using_it(
        self, tmp_path, house, offline_managed_env
    ):
        from test_dependency_transaction import _importable  # 同一个小工具，不抄第二份

        proj = _project(tmp_path)
        user_home = tmp_path / "userenv"
        user_home.mkdir()
        python = str(projectenv.interpreter_of(real_venv(user_home, name="py")))
        _select(proj, python)
        assert engine_pool.same_python(deprepair.configured_interpreter(proj), python)
        plan = deprepair.create_joint_plan(
            proj, "figure.py", target_kind=deprepair.TARGET_CONFIGURED
        )
        assert plan.target_kind == deprepair.TARGET_CONFIGURED
        assert engine_pool.same_python(plan.python, python)
        assert plan.modifies_user_environment and not plan.creates_environment
        assert plan.requirements == (ALPHA[0],) and plan.adapter == ()
        assert plan.impact["scope"] == deprepair.SCOPE_PROJECT_VENV_IN_PLACE
        deprepair.prepare_async(plan.plan_id, confirmed_impact=plan.impact_digest)
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert rec["result"]["target_kind"] == deprepair.TARGET_CONFIGURED
        assert _importable(python, ALPHA[1])
        assert managedenv.read_manifest(proj) is None  # 没建受管环境
        assert engine_pool.same_python(projectenv.remembered(proj), python)
        # 装完重新记解释器时仍是「用户亲手选的」：下一次缺包这条目标还在（Codex #1934 P2）
        assert (projectenv.remembered_record(proj) or {}).get("trigger") == "user_selected"
        assert engine_pool.same_python(deprepair.configured_interpreter(proj), python)
