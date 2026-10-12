"""兼容性改造 P03（DEP-05，AC-08 / AC-22）：互斥 provider（`cv2` 的四个 opencv 发行包）走真实 generation 事务。

离线 wheelhouse（四个 provider 各一个 wheel，同样写一个 `cv2.py`，所以「同一代里装两个」会真的互相覆盖）+ 真 venv + 真 pip：

* 四个 provider 分别作为项目声明：计划里的 requirements、真实装进新一代的发行包集合、脚本实际 import 到的 provider、
  成功回执（`result.providers`）里的 provider / 版本 / 来源 / generation 五者一致；
* 另一个作用域（目录）仍要求竞争 provider 时，切换 provider 不能静默丢弃它：`dependency_scope_conflict`，带出路，不建新代，旧 active 与旧代里的包原样；
* 用户选了「换成本作用域」才换，且新一代只有所选 provider；失败不替换旧 active、不原地卸载旧代里的包。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker, wait_for
from tavotto.engine import depplan, deprepair, envlease, managedenv

pytest_plugins = ("support.dependency_repair",)

OPENCV = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)
VERSION = "4.10.0.84"
_SCRIPT = "import cv2\nprint(cv2.PROVIDER)\n"


@pytest.fixture(autouse=True)
def _clean(clean_state):
    envlease.reset_for_tests()
    depplan.reset_cache()
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()


@pytest.fixture
def cv2_house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "cv2-house"
    for name in OPENCV:
        build_wheel(
            dest, name=name, import_name="cv2", version=VERSION, body=f'PROVIDER = "{name}"\n'
        )
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _scope(project: Path, rel: str, requirements: str) -> None:
    folder = project / rel
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "requirements.txt").write_text(requirements, encoding="utf-8")
    (folder / "figure.py").write_text(_SCRIPT, encoding="utf-8")


def _run(python: str, cwd: Path) -> str:
    out = subprocess.run(
        [python, str(cwd / "figure.py")],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-1500:]
    return out.stdout.strip()


def _opencv_installed(python: str) -> set[str]:
    out = subprocess.run(
        [python, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-800:]
    import json

    return {e["name"].lower().replace("_", "-") for e in json.loads(out.stdout)} & set(OPENCV)


def _prepare(project: Path, script: str) -> dict:
    plan = deprepair.create_joint_plan(project, script)
    deprepair.prepare_async(plan.plan_id, confirmed_impact=plan.impact_digest)
    return wait_for(plan.plan_id)


@needs_worker
@pytest.mark.parametrize("declared", OPENCV)
def test_each_declared_provider_is_the_only_one_installed_and_the_receipt_names_it(
    tmp_path, cv2_house, offline_managed_env, declared
):
    """AC-08 / AC-09：计划的 requirements = 真实装进新一代的 provider = 脚本实际 import 到的 provider = 回执里记的 provider。"""
    project = tmp_path / "paper"
    _scope(project, ".", f"{declared}=={VERSION}\n")
    plan = deprepair.create_joint_plan(project, "figure.py")
    assert plan.requirements == (f"{declared}=={VERSION}",)
    assert plan.joint["provider_decisions"][0]["basis"] == "project_declared"
    deprepair.prepare_async(plan.plan_id, confirmed_impact=plan.impact_digest)
    rec = wait_for(plan.plan_id)
    assert rec["state"] == deprepair.STATE_DONE, rec
    python = managedenv.python_of(project)
    assert _opencv_installed(python) == {declared}  # 每代只装所需的那一种
    assert _run(python, project) == declared  # 脚本实际用到的就是它
    generation = managedenv.active_generation(project)
    assert rec["result"]["providers"] == [
        {
            "import_name": "cv2",
            "distribution": declared,
            "version": VERSION,
            "source": "project_declared",
            "provenance": "index",
            "generation": generation,
        }
    ]
    ledger = {e["distribution"]: e for e in managedenv.ledger_entries(project)}
    assert set(ledger) == {declared} and ledger[declared]["resolved_version"] == VERSION


@pytest.fixture
def two_scopes(tmp_path, cv2_house, offline_managed_env) -> Path:
    """`a` 声明 opencv-python 并已装好（账上记着它是为 `a` 装的）；`b` 声明 headless，尚未装。"""
    project = tmp_path / "paper"
    project.mkdir()
    _scope(project, "a", f"opencv-python=={VERSION}\n")
    _scope(project, "b", f"opencv-python-headless=={VERSION}\n")
    assert _prepare(project, "a/figure.py")["state"] == deprepair.STATE_DONE
    entry = {e["distribution"]: e for e in managedenv.ledger_entries(project)}["opencv-python"]
    assert entry["scope"] == "a"
    return project


@needs_worker
class TestProviderSwitchAcrossScopes:
    def test_another_scope_s_competing_provider_is_not_silently_dropped(self, two_scopes):
        """修复前：`generation_requirements` 把账上互斥的 opencv-python 让位，b 的计划 `ready`、装完 a 的脚本悄悄换了 provider。"""
        project = two_scopes
        active = managedenv.active_generation(project)
        python = managedenv.python_of(project)
        plan, _kind, _py = deprepair.joint_plan_for(project, "b/figure.py")
        assert plan.status == "blocked"
        (block,) = [b for b in plan.blocked if b["code"] == "dependency_scope_conflict"]
        assert block["conflicts"] == [
            {
                "name": "opencv-python",
                "installed_version": VERSION,
                "installed_for": "a",
                "wanted": "",
                "wanted_for": "b",
                "via": "provider",
                "replaced_by": "opencv-python-headless",
            }
        ]
        assert block["options"] == ["switch_scope", "separate_project"]
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "b/figure.py")
        assert err.value.code == deprepair.ERROR_PLAN_BLOCKED
        assert managedenv.active_generation(project) == active
        assert list(managedenv.generations(project)) == [active]
        assert _opencv_installed(python) == {"opencv-python"}  # 旧代里的包原样
        assert {e["distribution"] for e in managedenv.ledger_entries(project)} == {"opencv-python"}

    def test_the_scope_that_owns_the_provider_may_change_it(self, two_scopes):
        """正对照：a 自己改声明（同一作用域）是正常换 provider，不被拦。"""
        project = two_scopes
        (project / "a" / "requirements.txt").write_text(
            f"opencv-contrib-python=={VERSION}\n", encoding="utf-8"
        )
        plan, _kind, _py = deprepair.joint_plan_for(project, "a/figure.py")
        assert not [b for b in plan.blocked if b["code"] == "dependency_scope_conflict"]
        assert plan.requirements == (f"opencv-contrib-python=={VERSION}",)

    def test_a_ledger_without_ownership_does_not_take_part(self, two_scopes):
        """老账没有归属（沿用 D04 的约定）：判不出是谁要的，不据此拦。"""
        project = two_scopes
        data = managedenv.read_manifest(project)
        for entry in data["installed_by_tavotto"]:
            entry.pop("scope")
        managedenv.write_manifest(project, data)
        plan, _kind, _py = deprepair.joint_plan_for(project, "b/figure.py")
        assert not [b for b in plan.blocked if b["code"] == "dependency_scope_conflict"]

    def test_switching_scope_replaces_the_provider_and_keeps_only_the_chosen_one(self, two_scopes):
        project = two_scopes
        old_gen = managedenv.active_generation(project)
        old_python = managedenv.python_of(project)
        offer = deprepair.preparation_offer(project, "b/figure.py")
        switch = offer["scope_switch"]
        assert switch["requirements"] == [f"opencv-python-headless=={VERSION}"]
        assert "opencv-python" in switch["impact"]["drops"]  # 披露：a 的 provider 不再 active
        got = deprepair.start_confirmed(
            project,
            "b/figure.py",
            switch["impact_digest"],
            scope_policy=deprepair.SCOPE_POLICY_SWITCH,
        )
        rec = wait_for(got["plan_id"])
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert managedenv.active_generation(project) != old_gen
        python = managedenv.python_of(project)
        assert _opencv_installed(python) == {"opencv-python-headless"}
        assert _run(python, project / "b") == "opencv-python-headless"
        assert {e["distribution"] for e in managedenv.ledger_entries(project)} == {
            "opencv-python-headless"
        }
        # 旧代没有被原地卸载（还有人用就留着；已退役则目录已不在，两种都不是原地改）
        if Path(old_python).exists():
            assert _opencv_installed(old_python) == {"opencv-python"}

    def test_a_failed_switch_keeps_the_previous_active_generation_and_its_package(self, two_scopes):
        """AC-22：切换目标装不出来（wheelhouse 没有这个版本）——失败代不激活，旧代、旧账原样，旧代里没有被卸载的包。"""
        project = two_scopes
        active = managedenv.active_generation(project)
        python = managedenv.python_of(project)
        ledger_before = managedenv.ledger_entries(project)
        (project / "b" / "requirements.txt").write_text(
            "opencv-python-headless==9.9.9\n", encoding="utf-8"
        )
        offer = deprepair.preparation_offer(project, "b/figure.py")
        got = deprepair.start_confirmed(
            project,
            "b/figure.py",
            offer["scope_switch"]["impact_digest"],
            scope_policy=deprepair.SCOPE_POLICY_SWITCH,
        )
        rec = wait_for(got["plan_id"])
        assert rec["state"] == deprepair.STATE_FAILED, rec
        assert managedenv.active_generation(project) == active
        assert managedenv.python_of(project) == python
        assert managedenv.ledger_entries(project) == ledger_before
        assert _opencv_installed(python) == {"opencv-python"}
        assert _run(python, project / "a") == "opencv-python"
