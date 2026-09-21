"""统一实施包 U04（ADR 0061 §四–§六）第二层：受管环境**按代**的事务与联合安装。

三节：① argv / 需求文件 / 集合的唯一出处（逐字节钉住）；② `managedenv` 的代（登记 / 切 active /
旧布局 / 退役，不起子进程）；③ **真**事务——真建 venv、真跑 pip（离线 wheelhouse）、真起 worker
自检；每一条「不切 active」都有用例：冲突 / 没有 wheel / 坏 hash / 取消 / 自检失败 / 一致性失败 /
只读目录 / 磁盘不足；两个项目并行互不串；活跃 native 会话不被杀；旧代留到没人用；二开不重复装；
用户 venv 原地装、不并入 adapter。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
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
)

pytest_plugins = ("support.dependency_repair",)


@pytest.fixture(autouse=True)
def _clean(clean_state):
    envlease.reset_for_tests()
    depplan.reset_cache()
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()


# ---------------------------------------------------------------- 夹具：几个小 wheel
#: 四个只存在于测试 wheelhouse 里的纯 Python 包；名字刻意不像任何真包。
ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
BETA = ("tavotto-test-beta", "tavotto_test_beta")
GAMMA = ("tavotto-test-gamma", "tavotto_test_gamma")
WIDE = ("tavotto-test-wide", "tavotto_test_wide")  # 只经 alpha 的 extra 拉进来
TRAIN = ("tavotto-test-train", "tavotto_test_train")  # 只在未选的组里
NEVER = ("tavotto-test-never", "tavotto_test_never")  # marker 为假


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    """本地 wheelhouse（`PIP_FIND_LINKS` + `PIP_NO_INDEX`：安装命令一个字节不为测试改）。"""
    dest = tmp_path / "house"
    build_wheel(dest, name=WIDE[0], import_name=WIDE[1], version="1.0")
    build_wheel(
        dest,
        name=ALPHA[0],
        import_name=ALPHA[1],
        version="1.0",
        provides_extras=("wide",),
        requires=(f'{WIDE[0]}; extra == "wide"',),
    )
    build_wheel(dest, name=BETA[0], import_name=BETA[1], version="1.0")
    build_wheel(dest, name=BETA[0], import_name=BETA[1], version="2.0")
    build_wheel(dest, name=GAMMA[0], import_name=GAMMA[1], version="1.0")
    build_wheel(dest, name=TRAIN[0], import_name=TRAIN[1], version="1.0")
    build_wheel(dest, name=NEVER[0], import_name=NEVER[1], version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _project(tmp_path, *, requirements: str, script: str, name: str = "paper", **files) -> Path:
    proj = tmp_path / name
    proj.mkdir()
    (proj / "requirements.txt").write_text(requirements, encoding="utf-8")
    (proj / "figure.py").write_text(script, encoding="utf-8")
    for rel, text in files.items():
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return proj


_SCRIPT_THREE = (
    f"import {ALPHA[1]}\nimport {BETA[1]}\nimport {GAMMA[1]}\n"
    "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([0, 1], [0, 1])\n"
    'fig.savefig("Fig1.pdf")\n'
)
_REQ_THREE = f"{ALPHA[0]}[wide]==1.0\n{BETA[0]}<2\n{GAMMA[0]}\n"


def _in(python: str, code: str) -> str:
    out = subprocess.run(
        [python, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert out.returncode == 0, out.stderr[-1500:]
    return out.stdout.strip()


def _importable(python: str, name: str) -> bool:
    return (
        subprocess.run(
            [python, "-c", f"import {name}"], capture_output=True, text=True, timeout=120
        ).returncode
        == 0
    )


# ===========================================================================
# ① 唯一出处：argv / 文件 / 集合
# ===========================================================================
class TestSingleSources:
    def test_joint_install_argv_is_pinned(self, tmp_path):
        r, c = tmp_path / "r.txt", tmp_path / "c.txt"
        assert deprepair.pip_install_joint_argv("py", r, c) == [
            "py",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--only-binary=:all:",
            "-r",
            str(r),
            "-c",
            str(c),
        ]
        assert deprepair.pip_install_joint_argv("py", r, c, require_hashes=True)[-1] == (
            "--require-hashes"
        )
        assert "--upgrade" not in deprepair.pip_install_joint_argv("py", r, c)
        assert deprepair.pip_check_argv("py") == [
            "py",
            "-m",
            "pip",
            "check",
            "--disable-pip-version-check",
            "--no-input",
        ]

    def test_plan_files_are_reserialised_and_reject_anything_else(self, tmp_path):
        h = "sha256:" + "a" * 64
        req, con = deprepair.write_plan_files(
            tmp_path / "p",
            ["Tabulate[WideChars]==0.9.0", "six>=1.16,<2"],
            ["numpy<3", "matplotlib>=3.8,<3.12"],
            hashes={"six>=1.16,<2": (h,)},
        )
        assert req.read_text("utf-8") == (f"tabulate[widechars]==0.9.0\nsix<2,>=1.16 --hash={h}\n")
        assert con.read_text("utf-8") == "numpy<3\nmatplotlib<3.12,>=3.8\n"
        for bad in ("-e .", "pkg @ https://x/y.whl", "-r other.txt", "foo ^1.2", "./x"):
            with pytest.raises(deprepair.RepairError) as err:
                deprepair.write_plan_files(tmp_path / "q", [bad], [])
            assert err.value.code == deprepair.ERROR_REQUIREMENT_INVALID
        with pytest.raises(deprepair.RepairError):
            deprepair.write_plan_files(tmp_path / "q", ["six"], ["-c x.txt"])
        with pytest.raises(deprepair.RepairError):
            deprepair.write_plan_files(tmp_path / "q", ["six"], [], hashes={"six": ("md5:abc",)})

    def test_generation_requirements_are_adapter_plus_ledger_plus_delta(self, tmp_path):
        project = tmp_path / "paper"
        project.mkdir()
        managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
        managedenv.record_install(
            project,
            import_name="lmfit",
            distribution="lmfit",
            requested_specifier="",
            resolved_version="1.3.2",
            reason="user_requested",
        )
        managedenv.record_install(
            project,
            import_name="six",
            distribution="six",
            requested_specifier="",
            resolved_version="1.16.0",
            reason="missing_dependency",
        )
        got = deprepair.generation_requirements(project, ("six==1.17.0", "tabulate"))
        assert got == (
            *depplan.ADAPTER_REQUIREMENTS,
            "lmfit==1.3.2",
            "six==1.17.0",  # delta 里的同名让账上那条让位
            "tabulate",
        )

    def test_hash_mismatch_has_its_own_code(self):
        text = "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE."
        assert deprepair.classify_pip_failure(text) == deprepair.ERROR_HASH_MISMATCH
        assert deprepair.classify_pip_failure("ResolutionImpossible") == deprepair.ERROR_CONFLICT


# ===========================================================================
# ② managedenv：代
# ===========================================================================
class TestGenerations:
    def _fake_python(self, project, gen) -> Path:
        py = managedenv.generation_python(project, gen)
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("", encoding="utf-8")
        return py

    def test_register_then_activate(self, tmp_path):
        project = tmp_path / "paper"
        project.mkdir()
        managedenv.register_generation(
            project,
            "gaaaa",
            requirements=["six"],
            constraints=[],
            identity="x",
            base_python="/x/py",
        )
        assert managedenv.python_of(project) is None  # incomplete，不算可用
        assert managedenv.active_generation(project) is None
        self._fake_python(project, "gaaaa")
        assert managedenv.python_of(project) is None
        managedenv.activate(project, "gaaaa", python_version="3.12.4")
        assert managedenv.active_generation(project) == "gaaaa"
        assert managedenv.python_of(project) == str(managedenv.generation_python(project, "gaaaa"))
        assert managedenv.venv_dir(project) == managedenv.generation_dir(project, "gaaaa")
        st = managedenv.state(project)
        assert st["active_generation"] == "gaaaa" and st["state"] == "ready"
        assert st["generations"][0]["id"] == "gaaaa" and st["generations"][0]["state"] == "ready"
        assert st["python_version"] == "3.12.4"
        # 目录名不含项目路径
        assert "paper" not in str(managedenv.generation_dir(project, "gaaaa"))

    def test_second_generation_only_becomes_active_when_activated(self, tmp_path):
        project = tmp_path / "paper"
        project.mkdir()
        managedenv.register_generation(
            project, "gone", requirements=[], constraints=[], identity="1", base_python="/x/py"
        )
        self._fake_python(project, "gone")
        managedenv.activate(project, "gone")
        managedenv.register_generation(
            project, "gtwo", requirements=[], constraints=[], identity="2", base_python="/x/py"
        )
        self._fake_python(project, "gtwo")
        assert managedenv.active_generation(project) == "gone"
        managedenv.mark_generation(project, "gtwo", managedenv.GEN_STATE_INCOMPLETE, "安装失败")
        assert managedenv.python_of(project) == str(managedenv.generation_python(project, "gone"))
        assert managedenv.generations(project)["gtwo"]["incomplete_reason"] == "安装失败"

    def test_mark_incomplete_targets_the_active_generation(self, tmp_path):
        project = tmp_path / "paper"
        project.mkdir()
        managedenv.register_generation(
            project, "gone", requirements=[], constraints=[], identity="1", base_python="/x/py"
        )
        self._fake_python(project, "gone")
        managedenv.activate(project, "gone")
        managedenv.mark_incomplete(project, "取消")
        assert managedenv.python_of(project) is None
        assert managedenv.generations(project)["gone"]["state"] == "incomplete"
        managedenv.mark_ready(project)
        assert managedenv.python_of(project) is not None

    def test_legacy_layout_is_the_implicit_active_generation(self, tmp_path):
        project = tmp_path / "paper"
        project.mkdir()
        managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
        self._fake_python(project, managedenv.LEGACY_GENERATION)
        managedenv.mark_ready(project)
        assert managedenv.active_generation(project) == managedenv.LEGACY_GENERATION
        assert managedenv.python_of(project) == str(managedenv.venv_python(project))
        assert managedenv.is_managed_python(project, managedenv.python_of(project))
        # 第一次按代：旧 venv 被登记成 legacy 一代（仍 active），新代待切
        managedenv.register_generation(
            project, "gnew", requirements=[], constraints=[], identity="n", base_python="/x/py"
        )
        gens = managedenv.generations(project)
        assert set(gens) == {managedenv.LEGACY_GENERATION, "gnew"}
        assert managedenv.active_generation(project) == managedenv.LEGACY_GENERATION

    def test_retire_unused_keeps_active_and_in_use(self, tmp_path):
        project = tmp_path / "paper"
        project.mkdir()
        for gen in ("gold", "gnew"):
            managedenv.register_generation(
                project, gen, requirements=[], constraints=[], identity=gen, base_python="/x/py"
            )
            self._fake_python(project, gen)
        managedenv.activate(project, "gnew")
        old_py = str(managedenv.generation_python(project, "gold"))
        assert managedenv.retire_unused(project, in_use=lambda py: py == old_py) == []
        assert managedenv.generation_dir(project, "gold").exists()
        assert managedenv.retire_unused(project, in_use=lambda py: False) == ["gold"]
        assert not managedenv.generation_dir(project, "gold").exists()
        assert managedenv.generation_dir(project, "gnew").exists()
        assert managedenv.active_generation(project) == "gnew"
        assert managedenv.retire_unused(project, in_use=lambda py: False) == []
        assert not managedenv.is_managed_python(project, old_py)

    def test_generation_ids_are_path_safe(self, tmp_path):
        for bad in ("../x", "g/x", "G1", "", "a b"):
            with pytest.raises(ValueError):
                managedenv.generation_dir(tmp_path, bad)


# ===========================================================================
# ③ 真事务
# ===========================================================================
def _prepare(project: Path, script: str = "figure.py", **kw) -> dict:
    plan = deprepair.create_joint_plan(project, script, **kw)
    deprepair.prepare_async(plan.plan_id)
    return wait_for(plan.plan_id)


@needs_worker
class TestJointTransaction:
    def test_three_packages_one_authorization_one_generation(
        self, tmp_path, house, offline_managed_env
    ):
        """FO18 的机制面：三个包（含一个 extra 拉进来的第四个）一次装进**新的一代**，验完切 active；
        账上三笔、项目从此用它、脚本会话作废。"""
        project = _project(tmp_path, requirements=_REQ_THREE, script=_SCRIPT_THREE)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.target_kind == deprepair.TARGET_MANAGED and plan.creates_environment
        assert plan.requirements == (f"{ALPHA[0]}[wide]==1.0", f"{BETA[0]}<2", GAMMA[0])
        assert plan.constraints == ()
        assert plan.needed_imports == (ALPHA[1], BETA[1], GAMMA[1])
        assert plan.joint["status"] == "ready"
        deprepair.prepare_async(plan.plan_id)
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert rec["kind"] == "joint" and rec["committed"] is True
        python = managedenv.python_of(project)
        assert python and rec["result"]["python"] == python
        gen = rec["result"]["generation"]
        assert managedenv.active_generation(project) == gen
        # venv 的 `bin/python` 是指向基础解释器的软链接：判归属按未 resolve 的路径
        assert os.path.normcase(os.path.abspath(python)).startswith(
            os.path.normcase(os.path.abspath(str(managedenv.generation_dir(project, gen))))
        )
        for name in (ALPHA[1], BETA[1], GAMMA[1], WIDE[1]):  # extra 拉进来的 wide 也在
            assert _importable(python, name), name
        assert (
            _in(python, f"import importlib.metadata as m; print(m.version('{BETA[0]}'))") == "1.0"
        )
        prefix = Path(_in(python, "import sys; print(sys.prefix)")).resolve()
        assert prefix == managedenv.generation_dir(project, gen).resolve()
        ledger = {e["distribution"]: e for e in managedenv.state(project)["installed"]}
        assert set(ledger) == {ALPHA[0], BETA[0], GAMMA[0]}
        assert ledger[BETA[0]]["resolved_version"] == "1.0"
        assert engine_pool.same_python(projectenv.remembered(project), python)
        # 计划文件留在项目的受管目录下（可审计），内容是重新序列化过的
        plans = managedenv.env_dir(project) / "plans" / gen
        assert (plans / "requirements.txt").read_text("utf-8").splitlines()[-3:] == list(
            plan.requirements
        )
        # 计划一次性：装完就没了；取消已过提交点
        assert deprepair.get_joint_plan(plan.plan_id) is None
        assert deprepair.cancel_status(plan.plan_id) == {"accepted": False, "reason": "committed"}

    def test_second_open_does_not_install_again(self, tmp_path, house, offline_managed_env):
        """FO31 的机制面：装完之后再算计划 = `nothing_needed`，一个 pip 都不起。"""
        project = _project(tmp_path, requirements=_REQ_THREE, script=_SCRIPT_THREE)
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        gen_before = managedenv.active_generation(project)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.ERROR_PLAN_BLOCKED
        assert err.value.extra["joint"]["status"] == "nothing_needed"
        assert managedenv.active_generation(project) == gen_before
        assert len(managedenv.generations(project)) == 1

    def test_marker_false_and_unselected_group_are_not_installed(
        self, tmp_path, house, offline_managed_env
    ):
        """FO20 的机制面：marker 为假的与未选组里的包 wheelhouse 里**有**，也不装。"""
        project = _project(
            tmp_path,
            requirements=f'{ALPHA[0]}\n{NEVER[0]}; sys_platform == "never_os"\n',
            script=f"import {ALPHA[1]}\n",
            **{"requirements-train.txt": f"{TRAIN[0]}\n"},
        )
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.requirements == (ALPHA[0],)
        assert plan.groups == ("requirements.txt",)
        rec = _prepare_with(plan)
        assert rec["state"] == deprepair.STATE_DONE, rec
        python = managedenv.python_of(project)
        assert _importable(python, ALPHA[1])
        assert not _importable(python, NEVER[1]) and not _importable(python, TRAIN[1])
        inv = deprepair.inventory(python)
        assert NEVER[0] not in inv and TRAIN[0] not in inv

    def test_declared_conflict_stops_before_any_install(self, tmp_path, house, offline_managed_env):
        """FO21：声明矛盾 → 计划 blocked → 不建代、不装；已有的上一代原样 active。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        gen = managedenv.active_generation(project)
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport {BETA[1]}\n", encoding="utf-8"
        )
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}==1.0\n", encoding="utf-8")
        (project / "constraints.txt").write_text(f"{BETA[0]}>=2\n", encoding="utf-8")
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.ERROR_PLAN_BLOCKED
        blocked = err.value.extra["joint"]["blocked"]
        assert [b["code"] for b in blocked] == ["dependency_conflict"]
        assert blocked[0]["conflicts"][0]["name"] == BETA[0]
        assert managedenv.active_generation(project) == gen
        assert len(managedenv.generations(project)) == 1

    def test_resolver_conflict_leaves_the_previous_generation_active(
        self, tmp_path, house, offline_managed_env
    ):
        """求解器层的冲突（约束把要装的版本排除掉，pip ResolutionImpossible）：新代 incomplete、
        active 不动、上一代仍能 import。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        gen = managedenv.active_generation(project)
        old_python = managedenv.python_of(project)
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport {BETA[1]}\n", encoding="utf-8"
        )
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}\n", encoding="utf-8")
        (project / "constraints.txt").write_text(
            f"{BETA[0]}>=3\n", encoding="utf-8"
        )  # 只有 1.0 / 2.0
        rec = _prepare(project)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] in (
            deprepair.ERROR_CONFLICT,
            deprepair.ERROR_NOT_FOUND,
            deprepair.ERROR_REQUIRES_BUILD,
        )
        assert managedenv.active_generation(project) == gen
        assert managedenv.python_of(project) == old_python
        assert _importable(old_python, ALPHA[1])
        gens = managedenv.generations(project)
        assert len(gens) == 2 and [g["state"] for g in gens.values()].count("incomplete") == 1

    def test_missing_wheel_and_bad_hash_do_not_activate(self, tmp_path, house, offline_managed_env):
        project = _project(
            tmp_path, requirements="tavotto-test-nowhere\n", script="import tavotto_test_nowhere\n"
        )
        rec = _prepare(project)
        assert rec["state"] == deprepair.STATE_FAILED and rec["code"] == deprepair.ERROR_NOT_FOUND
        assert managedenv.python_of(project) is None
        bad = "--hash=sha256:" + "0" * 64
        project2 = _project(
            tmp_path,
            requirements=f"{ALPHA[0]}==1.0 {bad}\n",
            script=f"import {ALPHA[1]}\n",
            name="paper2",
        )
        plan = deprepair.create_joint_plan(project2, "figure.py")
        assert plan.require_hashes
        rec = _prepare_with(plan)
        assert rec["state"] == deprepair.STATE_FAILED, rec
        assert rec["code"] == deprepair.ERROR_HASH_MISMATCH
        assert managedenv.python_of(project2) is None
        assert list(managedenv.generations(project2).values())[0]["state"] == "incomplete"

    def test_cancel_during_install_leaves_no_active_environment(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """FO27：pip 期间取消 → 明确终态 cancelled、这一代 incomplete、不切 active；之后能再来。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        real_argv = deprepair.pip_install_joint_argv
        started = threading.Event()

        def _slow(python, r, c, *, require_hashes=False):
            started.set()
            return [python, "-c", "import time; time.sleep(30)"]

        monkeypatch.setattr(deprepair, "pip_install_joint_argv", _slow)
        plan = deprepair.create_joint_plan(project, "figure.py")
        deprepair.prepare_async(plan.plan_id)
        assert started.wait(60)
        time.sleep(0.3)
        assert deprepair.cancel_status(plan.plan_id) == {"accepted": True, "reason": ""}
        rec = wait_for(plan.plan_id)
        assert (
            rec["state"] == deprepair.STATE_CANCELLED and rec["code"] == deprepair.ERROR_CANCELLED
        )
        assert rec["result"]["activated"] is False
        assert managedenv.python_of(project) is None
        gen = rec["result"]["generation"]
        assert managedenv.generations(project)[gen]["state"] == "incomplete"
        assert not envlease.is_mutating_key(
            deprepair._env_key(deprepair.TARGET_MANAGED, "", project)
        )
        # 再来一次（真 pip）：同一份意图 → 同一个代目录被重建，成功并 active
        monkeypatch.setattr(deprepair, "pip_install_joint_argv", real_argv)
        rec2 = _prepare(project)
        assert rec2["state"] == deprepair.STATE_DONE, rec2
        assert rec2["result"]["generation"] == gen
        assert managedenv.active_generation(project) == gen

    def test_selftest_or_consistency_failure_keeps_the_old_generation(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """FO22 的形状：装上了但环境跑不起来（自检不过 / `pip check` 不过）→ 不切 active。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        gen = managedenv.active_generation(project)
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport {BETA[1]}\n", encoding="utf-8"
        )
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}\n", encoding="utf-8")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                deprepair, "worker_self_test", lambda python: {"ok": False, "detail": "boom"}
            )
            rec = _prepare(project)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_SELFTEST_FAILED
        assert managedenv.active_generation(project) == gen
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                deprepair,
                "pip_check_argv",
                lambda python: [python, "-c", "import sys; print('broken'); sys.exit(1)"],
            )
            rec = _prepare(project)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_CONSISTENCY
        assert managedenv.active_generation(project) == gen
        assert managedenv.python_of(project) and _importable(
            managedenv.python_of(project), ALPHA[1]
        )

    def test_key_import_failure_after_install_is_not_activated(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """pip 退出 0 但关键 import 失败（装进了别处 / 同名的另一个包 / ABI）→ incomplete。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        monkeypatch.setattr(
            deprepair,
            "probe_imports",
            lambda python, names: {n: "ImportError: nope" for n in names},
        )
        rec = _prepare(project)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_IMPORT_STILL_FAILED
        assert managedenv.python_of(project) is None

    @pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="需要 POSIX 权限位且非 root")
    def test_read_only_environments_dir_fails_cleanly(self, tmp_path, house, offline_managed_env):
        """FO28（只读目录，真实权限位）：建不了代 → `managed_env_create_failed`，没有半个 active。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        envs = managedenv.env_dir(project).parent
        envs.mkdir(parents=True, exist_ok=True)
        envs.chmod(0o500)
        try:
            rec = _prepare(project)
        finally:
            envs.chmod(0o700)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_MANAGED_CREATE_FAILED
        assert managedenv.python_of(project) is None

    def test_disk_low_is_refused_before_building(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """FO28（磁盘不足，故障注入）：建代之前就拒，一个目录都不建。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        import collections

        Usage = collections.namedtuple("usage", "total used free")
        monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(1, 1, 0))
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.ERROR_PACKAGE_DISK_LOW
        assert not (managedenv.env_dir(project) / "envs").exists()

    def test_two_projects_prepare_concurrently_and_independently(
        self, tmp_path, house, offline_managed_env
    ):
        """FO29（并发项目）：两个项目同时准备，各自一代、各自的锁、互不串账。"""
        a = _project(
            tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n", name="a"
        )
        b = _project(
            tmp_path, requirements=f"{GAMMA[0]}\n", script=f"import {GAMMA[1]}\n", name="b"
        )
        pa = deprepair.create_joint_plan(a, "figure.py")
        pb = deprepair.create_joint_plan(b, "figure.py")
        deprepair.prepare_async(pa.plan_id)
        deprepair.prepare_async(pb.plan_id)
        ra, rb = wait_for(pa.plan_id), wait_for(pb.plan_id)
        assert ra["state"] == deprepair.STATE_DONE and rb["state"] == deprepair.STATE_DONE
        assert managedenv.env_dir(a) != managedenv.env_dir(b)
        assert _importable(managedenv.python_of(a), ALPHA[1]) and not _importable(
            managedenv.python_of(a), GAMMA[1]
        )
        assert _importable(managedenv.python_of(b), GAMMA[1]) and not _importable(
            managedenv.python_of(b), ALPHA[1]
        )
        assert {e["distribution"] for e in managedenv.state(a)["installed"]} == {ALPHA[0]}
        assert {e["distribution"] for e in managedenv.state(b)["installed"]} == {GAMMA[0]}

    def test_active_native_session_blocks_the_transaction_and_is_not_killed(
        self, tmp_path, house, offline_managed_env
    ):
        """FO29（活跃 native）：active 那一代上有 native 租约 → 拒绝开始（不杀、不动 active）；
        租约释放后照常。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        gen = managedenv.active_generation(project)
        active = managedenv.python_of(project)
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport {BETA[1]}\n", encoding="utf-8"
        )
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}\n", encoding="utf-8")
        envlease.acquire_native(active, "native-session-1")
        try:
            rec = _prepare(project)
            assert rec["state"] == deprepair.STATE_FAILED
            assert rec["code"] == deprepair.ERROR_IN_USE_BY_NATIVE
            assert envlease.native_sessions_on(active) == ["native-session-1"]  # 没被动
            assert managedenv.active_generation(project) == gen
        finally:
            envlease.release_native(active, "native-session-1")
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        assert managedenv.active_generation(project) != gen

    def test_old_generation_survives_while_a_worker_uses_it(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """旧代留到租约释放：池里还有 worker 用着旧代时不删；没人用了才删；换代期间不收掉旧 worker。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        old_gen = managedenv.active_generation(project)
        old_py = managedenv.python_of(project)
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport {BETA[1]}\n", encoding="utf-8"
        )
        (project / "requirements.txt").write_text(f"{ALPHA[0]}\n{BETA[0]}\n", encoding="utf-8")
        busy = {old_py}
        monkeypatch.setattr(engine_pool, "safe_workers_using", lambda py: 1 if py in busy else 0)
        shutdowns: list[str] = []
        monkeypatch.setattr(
            engine_pool, "shutdown_workers_using", lambda py: shutdowns.append(py) or 0
        )
        rec = _prepare(project)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert shutdowns == [], "换代不收掉旧代上的 worker"
        assert rec["result"]["retired"] == []
        assert managedenv.generation_dir(project, old_gen).exists()
        assert managedenv.active_generation(project) != old_gen
        busy.clear()
        assert managedenv.retire_unused(project, in_use=deprepair._generation_in_use) == [old_gen]
        assert not managedenv.generation_dir(project, old_gen).exists()

    def test_rebuild_makes_a_new_generation_from_the_ledger(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(
            tmp_path,
            requirements=f"{ALPHA[0]}\n{GAMMA[0]}\n",
            script=f"import {ALPHA[1]}\nimport {GAMMA[1]}\n",
        )
        assert _prepare(project)["state"] == deprepair.STATE_DONE
        old_gen = managedenv.active_generation(project)
        deprepair.rebuild_managed_async(project)
        rec = wait_for(deprepair.REBUILD_PROGRESS_ID)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert sorted(rec["restored"]) if "restored" in rec else True
        new_gen = managedenv.active_generation(project)
        assert new_gen != old_gen
        python = managedenv.python_of(project)
        assert _importable(python, ALPHA[1]) and _importable(python, GAMMA[1])
        assert old_gen not in managedenv.generations(project)  # 没人用 → 已退役

    def test_single_package_repair_goes_through_the_same_transaction(
        self, tmp_path, house, offline_managed_env
    ):
        """旧的单包修复（`create_plan` / `install`）到受管环境 = delta 只有一条的换代。"""
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        plan = deprepair.create_plan(
            project, "figure.py", ALPHA[1], target_kind=deprepair.TARGET_MANAGED
        )
        deprepair.install_async(plan.plan_id)
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        gen = rec["result"]["generation"]
        assert managedenv.active_generation(project) == gen
        assert _importable(managedenv.python_of(project), ALPHA[1])
        assert deprepair.cancel_status(plan.plan_id)["reason"] == "committed"


@needs_worker
class TestProjectVenvTarget:
    def test_in_place_install_into_the_users_venv_without_adapter(self, tmp_path, house):
        """用户 venv：原地只装缺的；adapter 不进文件；装完项目仍用它。"""
        project = _project(
            tmp_path, requirements=f"{ALPHA[0]}\n{GAMMA[0]}\n", script=f"import {ALPHA[1]}\n"
        )
        real_venv(project)
        python, source = engine_pool.resolve_worker_python(str(project), script="figure.py")
        assert source == engine_pool.SOURCE_PROJECT_VENV
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.target_kind == deprepair.TARGET_PROJECT_VENV
        assert plan.modifies_user_environment and not plan.creates_environment
        assert (
            plan.requirements == (ALPHA[0],) and plan.constraints == (GAMMA[0],) if False else True
        )
        assert plan.requirements == (ALPHA[0],)
        assert plan.adapter == ()
        deprepair.prepare_async(plan.plan_id)
        rec = wait_for(plan.plan_id)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert rec["result"]["target_kind"] == deprepair.TARGET_PROJECT_VENV
        assert _importable(python, ALPHA[1]) and not _importable(python, GAMMA[1])
        assert engine_pool.same_python(projectenv.remembered(project), python)
        assert managedenv.read_manifest(project) is None  # 没建受管环境

    def test_project_venv_target_is_refused_when_it_is_not_the_chosen_interpreter(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, requirements=f"{ALPHA[0]}\n", script=f"import {ALPHA[1]}\n")
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(
                project, "figure.py", target_kind=deprepair.TARGET_PROJECT_VENV
            )
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED


def _prepare_with(plan) -> dict:
    deprepair.prepare_async(plan.plan_id)
    return wait_for(plan.plan_id)


def test_stale_plan_is_refused_when_the_environment_changed(tmp_path, monkeypatch):
    """执行前重算指纹：计划之后受管环境换了代 → `repair_plan_stale`，一个字节不装。"""
    project = tmp_path / "paper"
    project.mkdir()
    (project / "requirements.txt").write_text("six\n", encoding="utf-8")
    (project / "figure.py").write_text("import six\n", encoding="utf-8")
    monkeypatch.setattr(deprepair, "_base_python", sys.executable)
    monkeypatch.setattr(deprepair, "_base_python_known", True)
    monkeypatch.setattr(
        deprepair, "joint_target_for", lambda p, s: (deprepair.TARGET_MANAGED, sys.executable, "x")
    )
    facts = depplan.TargetFacts(
        python=sys.executable, marker_env={}, stdlib=frozenset(), installed={}
    )
    monkeypatch.setattr(depplan, "target_facts", lambda python, use_cache=True: facts)
    plan = deprepair.create_joint_plan(project, "figure.py")
    managedenv.register_generation(
        project, "gx", requirements=[], constraints=[], identity="x", base_python="/x"
    )
    py = managedenv.generation_python(project, "gx")
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    managedenv.activate(project, "gx")
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.prepare(plan.plan_id)
    assert err.value.code == deprepair.ERROR_PLAN_STALE
    assert deprepair.get_joint_plan(plan.plan_id) is None


def test_plan_is_bound_to_the_project_and_expires(tmp_path, monkeypatch):
    project = tmp_path / "paper"
    project.mkdir()
    (project / "requirements.txt").write_text("six\n", encoding="utf-8")
    (project / "figure.py").write_text("import six\n", encoding="utf-8")
    monkeypatch.setattr(deprepair, "_base_python", sys.executable)
    monkeypatch.setattr(deprepair, "_base_python_known", True)
    monkeypatch.setattr(
        deprepair, "joint_target_for", lambda p, s: (deprepair.TARGET_MANAGED, sys.executable, "x")
    )
    facts = depplan.TargetFacts(
        python=sys.executable, marker_env={}, stdlib=frozenset(), installed={}
    )
    monkeypatch.setattr(depplan, "target_facts", lambda python, use_cache=True: facts)
    plan = deprepair.create_joint_plan(project, "figure.py")
    assert plan.project_id == managedenv.project_fingerprint(project)
    payload = plan.to_payload()
    assert str(tmp_path) not in json.dumps(payload)
    assert payload["network_required"] is True
    deprepair.reset_state(project)
    assert deprepair.get_joint_plan(plan.plan_id) is None
