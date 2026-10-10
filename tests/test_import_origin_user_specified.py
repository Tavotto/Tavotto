"""Import Origin PR5b：预填 + 用户确认（user_specified）、装后「发行包与版本」核验、#931、#932。

维护者裁决 C1（#864 关闭）：表外 / 来源未定的 import 后端只给 `suggested_distribution` 预填——**不是**可信来源、不进 requirements、
不进 `INSTALLABLE_SOURCES`、不自动装；用户在确认界面接受（可改）后作为 `user_specified` 提交，进联合计划与影响披露，装后核验装上的
发行包确实提供那个 import、版本满足约束。

六节：① 预填（该给 / 不该给）② `plan(user_specified=)` ③ 联合计划绑定与影响披露（IMPACT_VERSION 2）
④ `_verify_distributions` 单元 ⑤ 真安装端到端（本地 wheelhouse，离线）⑥ #931 / #932。

每个行为至少一条修前见红的用例 + 对照用例。Windows 路径形态不写死（`tests/support/envworld`）。
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker, real_venv, wait_for
from tavotto.engine import (
    depplan,
    deprepair,
    depresolve,
    envlease,
    importscan,
    managedenv,
    pool as engine_pool,
    projectenv,
    runtime,
)

pytest_plugins = ("support.dependency_repair",)

LAB = ("tavotto-test-lab-tools", "tavotto_test_lab_tools")  # 预填 = 规范化的 import 名 = 真包名
OTHER = ("tavotto-test-other-pkg", "tavotto_test_other_pkg")  # 提供的是别的 import
WRAP = ("tavotto-test-wrapper", "tavotto_test_wrapper")  # 依赖 LAB，自己不提供 LAB 的 import
ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
NEWBIE = ("tavotto-test-newbie", "tavotto_test_newbie")  # wheelhouse 里没有

LINUX_SUFFIXES = (".cpython-313-x86_64-linux-gnu.so", ".abi3.so", ".so")
WINDOWS_SUFFIXES = (".cp313-win_amd64.pyd", ".pyd")


@pytest.fixture(autouse=True)
def _clean(clean_state):
    envlease.reset_for_tests()
    depplan.reset_cache()
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _facts(installed: dict | None = None) -> depplan.TargetFacts:
    return depplan.TargetFacts(
        python="/fake/python",
        marker_env={
            "implementation_name": "cpython",
            "implementation_version": "3.12.4",
            "os_name": "posix",
            "platform_machine": "arm64",
            "platform_release": "",
            "platform_system": "Darwin",
            "platform_version": "",
            "python_full_version": "3.12.4",
            "platform_python_implementation": "CPython",
            "python_version": "3.12",
            "sys_platform": "darwin",
        },
        stdlib=importscan.HOST_STDLIB,
        installed=dict(installed or {}),
    )


def _site(tmp_path: Path, name: str = "venv") -> tuple[Path, Path]:
    """只有 site-packages 的假环境前缀（解释器一次也不起）。"""
    prefix = tmp_path / name
    site = prefix / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    _write(prefix, "pyvenv.cfg", "home = /usr/bin\nversion = 3.13.0\n")
    return prefix, site


def _site_facts(prefix: Path, site: Path, installed=None, **kw) -> depplan.TargetFacts:
    return dataclasses.replace(
        _facts(installed),
        python=str(prefix / "bin" / "python"),
        prefix=str(prefix),
        site_roots=(str(site),),
        **kw,
    )


def _dist(site: Path, name: str, version: str, *provides: str) -> None:
    """往假 site-packages 里放一个有 RECORD 的发行包（单文件模块）。"""
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )
    rows = [f"{mod}.py,," for mod in provides] + [f"{info.name}/METADATA,,"]
    (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    for mod in provides:
        (site / f"{mod}.py").write_text("", encoding="utf-8")


def version_of(n: int) -> str:
    return f"{n}.0"


def _plan(tmp_path, script="import lab_tools\n", facts=None, **kw):
    proj = tmp_path / "proj"
    _write(proj, "plot.py", script)
    return depplan.plan(
        proj, "plot.py", facts=facts or _facts(), target_kind="tavotto_managed", **kw
    )


def _health(**detail) -> dict:
    return {"ok": True, "modules_detail": dict(detail), "modules_ok": {}}


# ===========================================================================
# ① 预填
# ===========================================================================
class TestPrefill:
    def test_a_table_outside_import_gets_a_prefill_that_is_not_a_requirement(self, tmp_path):
        plan = _plan(tmp_path)
        (origin,) = plan.origins
        assert origin["import_name"] == "lab_tools"
        assert origin["reason"] == "distribution_unresolved"
        assert origin["suggested_distribution"] == "lab-tools"
        # 预填**不是**可信来源：不进 missing / requirements / 状态，来源集合一个字没动
        assert plan.missing == () and plan.requirements == () and plan.user_specified == ()
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert depresolve.INSTALLABLE_SOURCES == ("project_declared", "curated", "user_specified")
        assert depresolve.resolve(tmp_path / "proj", "lab_tools") is None  # 解析器仍然不猜

    @pytest.mark.parametrize(
        "name",
        [
            "pil",  # curated import 名 `PIL` 的大小写变体：PyPI 上是另一个项目
            "Yaml",
            "CV2",
            "pillow",  # curated 的包名（该装的是 `PIL` 对应的那个）
            "scikit_learn",
            "PyYAML",
            "opencv_python",  # 登记过的备选发行包
            "numpy",  # curated 已有映射：根本不是表外名
            "json",  # 标准库
            "tkinter",
            "_private",  # 规范化后过不了包名语法
        ],
    )
    def test_names_that_collide_with_curated_or_are_not_installable_names_get_no_prefill(
        self, name
    ):
        assert depresolve.suggest_distribution(name) == ""

    @pytest.mark.parametrize("name,expected", [("lab_tools", "lab-tools"), ("my.lab", "")])
    def test_control_a_plain_outside_name_gets_its_normalised_name(self, name, expected):
        assert depresolve.suggest_distribution(name) == expected

    def test_the_prefill_is_not_given_when_the_environment_says_the_module_is_importable(
        self, tmp_path
    ):
        for state in ("found", "import_error"):
            plan = _plan(tmp_path, coverage=lambda names, s=state: _health(lab_tools=s))
            (origin,) = plan.origins
            assert "suggested_distribution" not in origin, state
        # 对照：量过、确实 import 不到 → 照给
        plan = _plan(tmp_path, coverage=lambda names: _health(lab_tools="not_found"))
        assert plan.origins[0]["suggested_distribution"] == "lab-tools"

    def test_the_prefill_is_not_given_when_something_already_claims_the_module(self, tmp_path):
        prefix, site = _site(tmp_path)
        _write(site, "lab_tools.py", "")  # 模块在、没有任何发行包认领（手拷进去的）
        plan = _plan(tmp_path, facts=_site_facts(prefix, site))
        (origin,) = plan.origins
        assert origin["reason"] == "unverified"
        assert "suggested_distribution" not in origin

    def test_the_prefill_is_not_given_when_the_name_layer_could_not_be_read(self, tmp_path):
        prefix, site = _site(tmp_path)
        other = tmp_path / "host" / "site-packages"  # 报告了、却不存在：名字层读不全
        facts = dataclasses.replace(_site_facts(prefix, site), site_roots=(str(site), str(other)))
        plan = _plan(tmp_path, facts=facts)
        (origin,) = plan.origins
        assert "suggested_distribution" not in origin
        # 对照：读全了就给
        depplan.reset_cache()
        assert _plan(tmp_path, facts=_site_facts(prefix, site)).origins[0]["suggested_distribution"]

    def test_no_prefill_for_an_import_with_a_trusted_name_or_a_declared_one(self, tmp_path):
        plan = _plan(tmp_path, "import docx\nimport lab_tools\n")
        by_name = {o["import_name"]: o for o in plan.origins}
        assert set(by_name) == {"lab_tools"}  # docx 有 curated 名，是 missing，不是 origins
        assert [m["distribution"] for m in plan.missing] == ["python-docx"]


# ===========================================================================
# ② plan(user_specified=)
# ===========================================================================
class TestUserSpecifiedInThePlan:
    def test_an_accepted_prefill_enters_the_joint_plan_as_a_user_specified_requirement(
        self, tmp_path
    ):
        plan = _plan(tmp_path, user_specified={"lab_tools": "lab-tools>=1.2"})
        assert plan.status == depplan.STATUS_READY
        assert plan.requirements == ("lab-tools>=1.2",)
        (entry,) = plan.missing
        assert entry["import_name"] == "lab_tools"
        assert entry["resolution_source"] == "user_specified"
        assert entry["specifiers"] == [">=1.2"] and entry["declared"] is False
        # 离开 unknown / origins（两者恒等）
        assert plan.unknown == () and plan.origins == ()
        (disclosed,) = plan.user_specified
        assert disclosed == {
            "import_name": "lab_tools",
            "distribution": "lab-tools",
            "specifier": ">=1.2",
            "requirement": "lab-tools>=1.2",
            "resolution_source": "user_specified",
            "verified_source": False,
        }
        assert plan.to_payload()["user_specified"] == [disclosed]

    def test_the_user_may_change_the_name_and_the_version_is_optional(self, tmp_path):
        plan = _plan(tmp_path, user_specified={"lab_tools": "Lab_Tools_Pro"})
        assert plan.requirements == ("lab-tools-pro",)
        assert plan.user_specified[0]["specifier"] == ""

    def test_without_it_the_same_script_is_unchanged(self, tmp_path):
        plan = _plan(tmp_path)
        assert "user_specified" not in plan.to_payload()
        assert plan.requirements == ()

    @pytest.mark.parametrize(
        "text",
        [
            "-r evil.txt",
            "--index-url http://evil lab-tools",
            "lab-tools @ https://evil/x.whl",
            "git+https://evil/x.git",
            "../local-package",
            "lab-tools[extra]",
            "lab-tools; python_version>'3'",
            "lab tools",
            "lab-tools==1.0 --hash=sha256:" + "0" * 64,
            "",
        ],
    )
    def test_the_strict_requirement_syntax_still_applies(self, tmp_path, text):
        with pytest.raises(depplan.UserSpecifiedRejected) as err:
            _plan(tmp_path, user_specified={"lab_tools": text})
        assert err.value.kind == depplan.UserSpecifiedRejected.INVALID

    def test_only_a_plain_outside_name_can_be_specified(self, tmp_path):
        # 不在脚本里 / 有 curated 名的 / 已经有可信解析的：都不是「来源未定的表外名」，不能被覆盖成别的包
        for imp in ("not_in_script", "docx", "numpy"):
            with pytest.raises(depplan.UserSpecifiedRejected) as err:
                _plan(
                    tmp_path,
                    "import lab_tools\nimport docx\nimport numpy\n",
                    user_specified={imp: "evil-pkg"},
                )
            assert err.value.kind == depplan.UserSpecifiedRejected.NOT_APPLICABLE, imp
        # 对照：同一个脚本里真正的表外名可以
        plan = _plan(
            tmp_path,
            "import lab_tools\nimport docx\n",
            user_specified={"lab_tools": "lab-tools"},
        )
        assert sorted(plan.requirements) == ["lab-tools", "python-docx"]

    def test_a_name_the_environment_already_claims_cannot_be_specified(self, tmp_path):
        prefix, site = _site(tmp_path)
        _write(site, "lab_tools.py", "")
        with pytest.raises(depplan.UserSpecifiedRejected) as err:
            _plan(
                tmp_path,
                facts=_site_facts(prefix, site),
                user_specified={"lab_tools": "lab-tools"},
            )
        assert err.value.kind == depplan.UserSpecifiedRejected.NOT_APPLICABLE

    def test_a_lock_file_plan_cannot_carry_a_requirement_without_a_hash(self, tmp_path):
        proj = tmp_path / "proj"
        _write(proj, "requirements.txt", "six==1.16.0 --hash=sha256:" + "a" * 64 + "\n")
        _write(proj, "plot.py", "import six\nimport lab_tools\n")
        plan = depplan.plan(
            proj,
            "plot.py",
            facts=_facts(),
            target_kind="project_venv",
            user_specified={"lab_tools": "lab-tools"},
        )
        assert plan.status == depplan.STATUS_BLOCKED
        codes = [b["code"] for b in plan.blocked]
        assert codes == ["dependency_hashes_incomplete"]
        assert plan.blocked[0]["user_specified"] == ["lab_tools"]
        assert "lab-tools" not in plan.requirements
        # 没有新增 blocked 理由 / origin 理由
        assert "user_specified" not in "".join(depplan.BLOCK_REASONS + depplan.ORIGIN_REASONS)

    def test_the_requested_version_is_an_input_of_the_identity(self, tmp_path):
        a = _plan(tmp_path, user_specified={"lab_tools": "lab-tools>=1"})
        b = _plan(tmp_path, user_specified={"lab_tools": "lab-tools>=2"})
        assert a.identity != b.identity


# ===========================================================================
# ③ 联合计划绑定与影响披露
# ===========================================================================
class TestBindingAndDisclosure:
    @pytest.fixture
    def stubbed(self, tmp_path, monkeypatch):
        project = tmp_path / "paper"
        _write(project, "figure.py", "import lab_tools\n")
        monkeypatch.setattr(deprepair, "_base_python", sys.executable)
        monkeypatch.setattr(deprepair, "_base_python_known", True)
        monkeypatch.setattr(
            deprepair,
            "joint_target_for",
            lambda p, s: (deprepair.TARGET_MANAGED, sys.executable, "x"),
        )
        monkeypatch.setattr(depplan, "target_facts", lambda python, use_cache=True: _facts())
        seen: list = []
        monkeypatch.setattr(
            deprepair, "_run_generation", lambda job, ev: seen.append(job) or {"ran": True}
        )
        return project, seen

    def test_impact_version_is_two_and_names_the_user_specified_packages(self, stubbed):
        project, _seen = stubbed
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={"lab_tools": "lab-tools>=1"}
        )
        assert deprepair.IMPACT_VERSION == 2 and plan.impact["impact_version"] == 2
        assert plan.impact["user_specified"] == [
            {
                "import_name": "lab_tools",
                "requirement": "lab-tools>=1",
                "trust": "user_specified_unverified",
            }
        ]
        assert plan.impact["installs"] == ["lab-tools>=1"]
        payload = plan.to_payload()
        assert (
            payload["impact"]["user_specified"] and payload["impact_digest"] == plan.impact_digest
        )
        assert payload["joint"]["user_specified"][0]["verified_source"] is False

    def test_the_disclosure_is_part_of_the_digest(self, stubbed):
        """同一个安装集合、同一个目标，只是「这些包是用户指定的」这一事实不同：摘要必须不同，旧同意不覆盖它。"""
        project, _seen = stubbed
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={"lab_tools": "lab-tools>=1"}
        )
        undisclosed = {**plan.impact, "user_specified": []}
        assert deprepair.impact_digest(undisclosed) != plan.impact_digest
        # 对照：版本 1 的形状（没有这个字段）永远对不上
        legacy = {k: v for k, v in plan.impact.items() if k != "user_specified"}
        legacy["impact_version"] = 1
        assert deprepair.impact_digest(legacy) != plan.impact_digest

    def test_a_different_requested_version_is_a_different_authorisation(self, stubbed):
        project, seen = stubbed
        a = deprepair.create_joint_plan(
            project, "figure.py", user_specified={"lab_tools": "lab-tools>=1"}
        )
        b = deprepair.create_joint_plan(
            project, "figure.py", user_specified={"lab_tools": "lab-tools>=2"}
        )
        assert a.impact_digest != b.impact_digest
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(b.plan_id, confirmed_impact=a.impact_digest)
        assert err.value.code == deprepair.ERROR_IMPACT_CHANGED and seen == []
        deprepair.prepare(a.plan_id, confirmed_impact=a.impact_digest)
        assert len(seen) == 1
        assert seen[0].user_specified == a.user_specified  # 执行端带着披露（核验与替身重算要用）

    def test_the_replan_after_the_private_python_lands_carries_the_user_specified_package(
        self, stubbed, monkeypatch
    ):
        """干净机器：计划的事实是替身，私有 Python 就位后按真解释器重算（`_replan_on_base`）。重算必须把用户指定的需求再交给
        `depplan.plan`，否则重算出的 pip 输入少了它，与用户确认的不一致（`dependency_impact_changed`）——或者更糟，悄悄少装。"""
        project, seen = stubbed
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={"lab_tools": "lab-tools>=1"}
        )
        deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        (job,) = seen
        monkeypatch.setattr(depplan, "fresh_venv_facts", lambda base, **kw: _facts())
        new = deprepair._replan_on_base(job, sys.executable)
        assert tuple(new.delta) == ("lab-tools>=1",)
        assert [r["distribution"] for r in new.record] == ["lab-tools"]
        assert new.needed_imports == ("lab_tools",)

    def test_the_task_diagnostic_projection_never_carries_the_users_package_names(self):
        import json

        impact = deprepair.impact_of(
            target_kind="tavotto_managed",
            requirements=("secret-lab-tools>=1",),
            constraints=(),
            require_hashes=False,
            adapter=(),
            groups=(),
            creates_environment=True,
            private_python=None,
            env_fingerprint="x",
            user_specified=({"import_name": "secret_lab", "requirement": "secret-lab-tools>=1"},),
        )
        snap = deprepair.diagnostic_projection(
            {
                "state": "failed",
                "code": deprepair.ERROR_IMPORT_STILL_FAILED,
                "impact": impact,
                "impact_digest": deprepair.impact_digest(impact),
                "plan_id": "dp-x",
            }
        )
        assert "secret" not in json.dumps(snap)

    def test_a_plan_without_user_specified_discloses_an_empty_list(self, tmp_path):
        impact = deprepair.impact_of(
            target_kind="tavotto_managed",
            requirements=("a",),
            constraints=(),
            require_hashes=False,
            adapter=(),
            groups=(),
            creates_environment=True,
            private_python=None,
            env_fingerprint="x",
        )
        assert impact["user_specified"] == [] and impact["impact_version"] == 2

    def test_bad_syntax_and_ineligible_imports_are_refused_with_published_codes(self, stubbed):
        project, _seen = stubbed
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(
                project, "figure.py", user_specified={"lab_tools": "-r evil.txt"}
            )
        assert err.value.code == deprepair.ERROR_REQUIREMENT_INVALID
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(
                project, "figure.py", user_specified={"numpy": "numpy-evil"}
            )
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED
        # 没有用户指定：来源未定的 import 不装（nothing_needed → 没有可执行的计划）
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")
        assert err.value.code == deprepair.ERROR_PLAN_BLOCKED

    def test_the_gate_offer_never_carries_user_specified(self, stubbed):
        project, _seen = stubbed
        joint, _kind, _python = deprepair.joint_plan_for(project, "figure.py")
        assert joint.user_specified == () and joint.origins[0]["suggested_distribution"]
        # 跑前的门 / 准备会话展示的影响与绑定出的计划同一个函数：这里没有用户指定，披露为空表
        # （`joint` 是 nothing_needed，不可授权，门不给摘要）
        assert (
            deprepair.offer_impact(str(project), joint, deprepair.TARGET_MANAGED, sys.executable)[
                "user_specified"
            ]
            == []
        )

    def test_bundled_runtime_is_never_an_install_target(self, stubbed):
        project, _seen = stubbed
        for kind in ("bundled", "bundled_runtime", "builtin", ""):
            if kind == "":
                continue
            with pytest.raises(deprepair.RepairError) as err:
                deprepair.create_joint_plan(
                    project,
                    "figure.py",
                    target_kind=kind,
                    user_specified={"lab_tools": "lab-tools"},
                )
            assert err.value.code == deprepair.ERROR_NOT_ALLOWED, kind
        assert set(deprepair.TARGETS) == {"project_venv", "tavotto_managed"}


# ===========================================================================
# ④ 装后核验单元：发行包与版本对照 distmeta
# ===========================================================================
class TestVerifyDistributions:
    def _env(self, tmp_path, monkeypatch, installed, *, dists=(), facts_none=False):
        prefix, site = _site(tmp_path)
        for name, version, provides in dists:
            _dist(site, name, version, *provides)
        facts = _site_facts(prefix, site, installed)
        monkeypatch.setattr(
            depplan, "target_facts", lambda python, use_cache=True: None if facts_none else facts
        )
        return str(prefix / "bin" / "python")

    def _verify(self, python, *, requirements=("lab-tools>=1",), constraints=(), strict=False):
        return deprepair._verify_distributions(
            python,
            ({"import_name": "lab_tools", "distribution": "lab-tools", "specifier": ">=1"},),
            requirements,
            constraints,
            user_specified=({"import_name": "lab_tools"},) if strict else (),
        )

    def _codes(self, err) -> dict:
        assert err.value.code == deprepair.ERROR_IMPORT_STILL_FAILED  # 复用既有失败路径与错误码
        return err.value.extra["imports"]

    def test_the_planned_distribution_present_with_a_satisfying_version_that_provides_the_import_passes(
        self, tmp_path, monkeypatch
    ):
        py = self._env(
            tmp_path,
            monkeypatch,
            {"lab-tools": "1.4"},
            dists=[("lab-tools", "1.4", ["lab_tools"])],
        )
        self._verify(py)
        self._verify(py, strict=True)

    def test_a_planned_distribution_that_is_not_installed_fails(self, tmp_path, monkeypatch):
        py = self._env(tmp_path, monkeypatch, {})
        with pytest.raises(deprepair.RepairError) as err:
            self._verify(py)
        assert self._codes(err) == {"lab-tools": deprepair.DIST_NOT_INSTALLED}

    def test_a_version_that_does_not_satisfy_the_constraint_fails(self, tmp_path, monkeypatch):
        py = self._env(
            tmp_path,
            monkeypatch,
            {"lab-tools": "0.5"},
            dists=[("lab-tools", "0.5", ["lab_tools"])],
        )
        with pytest.raises(deprepair.RepairError) as err:
            self._verify(py)
        assert self._codes(err) == {"lab-tools": deprepair.DIST_VERSION_MISMATCH}

    def test_a_project_constraint_is_checked_too(self, tmp_path, monkeypatch):
        py = self._env(
            tmp_path,
            monkeypatch,
            {"lab-tools": "1.4"},
            dists=[("lab-tools", "1.4", ["lab_tools"])],
        )
        with pytest.raises(deprepair.RepairError) as err:
            self._verify(py, constraints=("lab-tools<1",))
        assert self._codes(err) == {"lab-tools": deprepair.DIST_VERSION_MISMATCH}
        self._verify(py, constraints=("lab-tools<2",))  # 对照

    def test_the_import_provided_by_another_distribution_fails_for_everyone(
        self, tmp_path, monkeypatch
    ):
        py = self._env(
            tmp_path,
            monkeypatch,
            {"lab-tools": "1.4", "other": "1.0"},
            dists=[("lab-tools", "1.4", ["lab_tools_x"]), ("other", "1.0", ["lab_tools"])],
        )
        for strict in (False, True):
            with pytest.raises(deprepair.RepairError) as err:
                self._verify(py, strict=strict)
            assert self._codes(err) == {"lab_tools": deprepair.DIST_DOES_NOT_PROVIDE}

    def test_no_claimant_at_all_is_judged_only_for_user_specified(self, tmp_path, monkeypatch):
        """判不出就别判：非用户指定的 import 没有任何发行包认领它 = 没有证据，不当不一致；用户指定的是唯一没有可信来源背书的一类，不放行。"""
        py = self._env(tmp_path, monkeypatch, {"lab-tools": "1.4"})
        self._verify(py)
        with pytest.raises(deprepair.RepairError) as err:
            self._verify(py, strict=True)
        assert self._codes(err) == {"lab_tools": deprepair.DIST_DOES_NOT_PROVIDE}

    def test_an_unreadable_environment_does_not_pass_a_user_specified_package(
        self, tmp_path, monkeypatch
    ):
        py = self._env(tmp_path, monkeypatch, {}, facts_none=True)
        self._verify(py)  # 非用户指定：量不出事实就没有可比的东西（导入验证已过）
        with pytest.raises(deprepair.RepairError) as err:
            self._verify(py, strict=True)
        assert self._codes(err) == {"lab_tools": deprepair.DIST_UNVERIFIABLE}

    def test_the_version_is_the_unshadowed_layers_not_the_last_metadata_entry(
        self, tmp_path, monkeypatch
    ):
        """include-system-site-packages 的 venv：同名发行包在 venv（v2）与基础层（v1）各一份，`facts.installed` 可能是被遮蔽的 v1。"""
        prefix, site = _site(tmp_path)
        base = tmp_path / "base" / "site-packages"
        base.mkdir(parents=True)
        _dist(site, "lab-tools", version_of(2), "lab_tools")
        _dist(base, "lab-tools", version_of(1), "lab_tools")
        facts = dataclasses.replace(
            _site_facts(prefix, site, {"lab-tools": "1.0"}), site_roots=(str(site), str(base))
        )
        monkeypatch.setattr(depplan, "target_facts", lambda python, use_cache=True: facts)
        py = str(prefix / "bin" / "python")
        self._verify(py, requirements=("lab-tools>=2",))  # 修前：误报 distribution_version_mismatch
        with pytest.raises(deprepair.RepairError) as err:  # 对照：venv 里真是 1
            self._verify(py, requirements=("lab-tools>=3",))
        assert self._codes(err) == {"lab-tools": deprepair.DIST_VERSION_MISMATCH}

    def test_control_when_the_venv_really_has_the_old_version_it_fails(self, tmp_path, monkeypatch):
        prefix, site = _site(tmp_path)
        base = tmp_path / "base" / "site-packages"
        base.mkdir(parents=True)
        _dist(site, "lab-tools", version_of(1), "lab_tools")
        _dist(base, "lab-tools", version_of(2), "lab_tools")
        facts = dataclasses.replace(
            _site_facts(prefix, site, {"lab-tools": "2.0"}), site_roots=(str(site), str(base))
        )
        monkeypatch.setattr(depplan, "target_facts", lambda python, use_cache=True: facts)
        with pytest.raises(deprepair.RepairError) as err:
            self._verify(str(prefix / "bin" / "python"), requirements=("lab-tools>=2",))
        assert self._codes(err) == {"lab-tools": deprepair.DIST_VERSION_MISMATCH}

    def test_a_distribution_the_plan_did_not_ask_pip_for_is_not_judged(self, tmp_path, monkeypatch):
        """记账条目里的发行包不在这次的需求里（已满足 / 别的来源）：没装它，就不核「装上的它」。"""
        py = self._env(
            tmp_path, monkeypatch, {"other": "1.0"}, dists=[("other", "1.0", ["lab_tools"])]
        )
        self._verify(py, requirements=())

    def test_the_fresh_measurement_does_not_leave_a_stale_cache_behind(self, tmp_path, monkeypatch):
        py = self._env(
            tmp_path,
            monkeypatch,
            {"lab-tools": "1.4"},
            dists=[("lab-tools", "1.4", ["lab_tools"])],
        )
        seen: list[bool] = []
        real = depplan.target_facts
        monkeypatch.setattr(
            depplan, "target_facts", lambda p, use_cache=True: seen.append(use_cache) or real(p)
        )
        self._verify(py)
        assert seen == [False]  # 重新量，不信缓存里「装之前」的事实


# ===========================================================================
# ⑤ 真安装端到端（本地 wheelhouse，离线）：真 create_joint_plan → prepare → 核验
# ===========================================================================
@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    build_wheel(dest, name=LAB[0], import_name=LAB[1], version="1.0")
    build_wheel(dest, name=OTHER[0], import_name=OTHER[1], version="1.0")
    build_wheel(dest, name=WRAP[0], import_name=WRAP[1], version="1.0", requires=(LAB[0],))
    build_wheel(dest, name=ALPHA[0], import_name=ALPHA[1], version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _project(tmp_path: Path, *imports: str, requirements: str = "", name: str = "paper") -> Path:
    proj = tmp_path / name
    proj.mkdir()
    if requirements:
        (proj / "requirements.txt").write_text(requirements, encoding="utf-8")
    body = "".join(f"import {i}\n" for i in imports)
    (proj / "figure.py").write_text(
        f"{body}import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([0, 1], [0, 1])\n"
        "fig.savefig('Fig1.pdf')\n",
        encoding="utf-8",
    )
    return proj


def _importable(python: str, name: str) -> bool:
    proc = subprocess.run(
        [python, "-c", f"import {name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc.returncode == 0


def _run(plan) -> dict:
    deprepair.prepare_async(plan.plan_id, confirmed_impact=plan.impact_digest)
    return wait_for(plan.plan_id)


def _adopt(project: Path) -> None:
    assert projectenv.remember(
        project,
        projectenv.interpreter_of(project / ".venv"),
        automatic=False,
        trigger=projectenv.TRIGGER_RECOMMENDED,
    )


@needs_worker
class TestEndToEndManaged:
    def test_the_prefill_alone_installs_nothing(self, tmp_path, house, offline_managed_env):
        project = _project(tmp_path, LAB[1])
        joint, kind, _python = deprepair.joint_plan_for(project, "figure.py")
        (origin,) = joint.origins
        assert origin["suggested_distribution"] == LAB[0]
        assert joint.requirements == () and joint.status == depplan.STATUS_NOTHING_NEEDED
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_joint_plan(project, "figure.py")  # 没有用户确认：没有可执行的计划
        assert err.value.code == deprepair.ERROR_PLAN_BLOCKED
        assert managedenv.python_of(project) is None and managedenv.generations(project) == {}

    def test_a_confirmed_prefill_installs_into_the_managed_environment_and_verifies(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, LAB[1])
        joint, _kind, _python = deprepair.joint_plan_for(project, "figure.py")
        suggested = joint.origins[0]["suggested_distribution"]
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={LAB[1]: f"{suggested}>=1"}
        )
        assert plan.target_kind == deprepair.TARGET_MANAGED and not plan.modifies_user_environment
        assert plan.requirements == (f"{LAB[0]}>=1",)
        assert plan.impact["user_specified"][0]["trust"] == "user_specified_unverified"
        rec = _run(plan)
        assert rec["state"] == deprepair.STATE_DONE, rec
        python = managedenv.python_of(project)
        assert python and _importable(python, LAB[1])
        # 绝不写 bundled / 宿主的运行环境
        assert not _importable(engine_pool.find_worker_python(), LAB[1])
        bundled = runtime.bundled_python()
        assert not bundled or not _importable(bundled, LAB[1])
        # 记了账（重建时装得回去）
        assert any(r.startswith(LAB[0]) for r in managedenv.installed_requirements(project))

    def test_a_wrong_name_fails_and_the_previous_generation_survives(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ALPHA[1], requirements=f"{ALPHA[0]}\n")
        assert _run(deprepair.create_joint_plan(project, "figure.py"))["state"] == "done"
        gen = managedenv.active_generation(project)
        old_python = managedenv.python_of(project)
        (project / "figure.py").write_text(
            f"import {ALPHA[1]}\nimport {NEWBIE[1]}\n"
            "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('Fig1.pdf')\n",
            encoding="utf-8",
        )
        # 用户把预填改成了 wheelhouse 里另一个包：它能装、却不提供 NEWBIE 的 import
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={NEWBIE[1]: OTHER[0]}
        )
        rec = _run(plan)
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == deprepair.ERROR_IMPORT_STILL_FAILED
        assert managedenv.active_generation(project) == gen
        assert managedenv.python_of(project) == old_python
        assert _importable(old_python, ALPHA[1]) and not _importable(old_python, OTHER[1])
        assert [g["state"] for g in managedenv.generations(project).values()].count(
            "incomplete"
        ) == 1

    def test_a_package_that_only_pulls_in_the_import_is_not_a_provider(
        self, tmp_path, house, offline_managed_env
    ):
        """用户填了 WRAP：它依赖 LAB（传递安装），所以 `import LAB` 成功——但 WRAP 自己不提供它。导入验证放行，发行包核验不放行。"""
        project = _project(tmp_path, LAB[1])
        plan = deprepair.create_joint_plan(project, "figure.py", user_specified={LAB[1]: WRAP[0]})
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_IMPORT_STILL_FAILED
        assert err.value.extra["imports"] == {LAB[1]: deprepair.DIST_DOES_NOT_PROVIDE}
        assert managedenv.python_of(project) is None  # 没有任何一代 active
        (gen_id,) = managedenv.generations(project)
        assert managedenv.generations(project)[gen_id]["state"] == "incomplete"
        # 证明导入验证本身是过了的：被丢弃的那一代里 import 是成功的
        assert _importable(str(managedenv.generation_python(project, gen_id)), LAB[1])

    def test_a_version_the_observation_says_is_unsatisfied_fails(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """pip 自己不会装出不满足约束的版本，所以这里让**装后重新观测**的版本读数不满足（元数据读数与约束的对照是核验的内容）。"""
        project = _project(tmp_path, LAB[1])
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={LAB[1]: f"{LAB[0]}>=1"}
        )
        real = depplan.target_facts

        def spy(python, *, use_cache=True):
            facts = real(python, use_cache=use_cache)
            if facts is not None and not use_cache and LAB[0] in facts.installed:
                facts = dataclasses.replace(facts, installed={**facts.installed, LAB[0]: "0.1"})
                for d in depplan.static_index(facts)._dists:  # 元数据读数也一致地说 0.1
                    if d.key == LAB[0]:
                        d.version = "0.1"
            return facts

        monkeypatch.setattr(depplan, "target_facts", spy)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.extra["imports"] == {LAB[0]: deprepair.DIST_VERSION_MISMATCH}
        assert managedenv.python_of(project) is None

    def test_the_check_covers_every_joint_install_not_only_user_specified(
        self, tmp_path, house, offline_managed_env, monkeypatch
    ):
        """可信来源（项目声明）的安装也核验：pip 装完后，`PYTHONPATH` 里出现了一个同名模块遮蔽了刚装的发行包——import 成功、
        但 import 到的不是计划里装的那个包。"""
        front = tmp_path / "front"
        front.mkdir()
        monkeypatch.setenv(
            "PYTHONPATH",
            os.pathsep.join(p for p in (str(front), os.environ.get("PYTHONPATH", "")) if p),
        )
        project = _project(tmp_path, ALPHA[1], requirements=f"{ALPHA[0]}\n")
        plan = deprepair.create_joint_plan(project, "figure.py")
        real = deprepair._run_pip_install

        def pip_then_shadow(*args, **kw):
            out = real(*args, **kw)
            (front / f"{ALPHA[1]}.py").write_text("SHADOW = True\n", encoding="utf-8")
            return out

        monkeypatch.setattr(deprepair, "_run_pip_install", pip_then_shadow)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_IMPORT_STILL_FAILED
        assert err.value.extra["imports"] == {ALPHA[1]: deprepair.DIST_DOES_NOT_PROVIDE}
        assert managedenv.python_of(project) is None

    def test_control_the_trusted_install_still_passes_the_same_check(
        self, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, ALPHA[1], requirements=f"{ALPHA[0]}\n")
        assert _run(deprepair.create_joint_plan(project, "figure.py"))["state"] == "done"
        assert _importable(managedenv.python_of(project), ALPHA[1])


@needs_worker
class TestEndToEndProjectVenv:
    def test_in_place_user_specified_install_verifies_and_discloses_no_full_rollback(
        self, tmp_path, house
    ):
        project = _project(tmp_path, LAB[1])
        real_venv(project)
        _adopt(project)
        python, source = engine_pool.resolve_worker_python(str(project), script="figure.py")
        assert source == engine_pool.SOURCE_PROJECT_VENV
        plan = deprepair.create_joint_plan(project, "figure.py", user_specified={LAB[1]: LAB[0]})
        assert plan.target_kind == deprepair.TARGET_PROJECT_VENV
        assert plan.impact["modifies_user_environment"] is True
        assert plan.impact["rollback"] == deprepair.ROLLBACK_NONE
        assert plan.impact["user_specified"][0]["import_name"] == LAB[1]
        rec = _run(plan)
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert _importable(python, LAB[1])
        assert not _importable(engine_pool.find_worker_python(), LAB[1])

    def test_in_place_mismatch_fails_and_the_partial_change_is_disclosed_not_hidden(
        self, tmp_path, house
    ):
        project = _project(tmp_path, LAB[1])
        real_venv(project)
        _adopt(project)
        python, _source = engine_pool.resolve_worker_python(str(project), script="figure.py")
        plan = deprepair.create_joint_plan(project, "figure.py", user_specified={LAB[1]: WRAP[0]})
        assert plan.impact["rollback"] == deprepair.ROLLBACK_NONE  # 授权时就说了：失败不能完整回滚
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.extra["imports"] == {LAB[1]: deprepair.DIST_DOES_NOT_PROVIDE}
        assert _importable(python, WRAP[1])  # 如实：用户 venv 里已经有被装上的包（不做自动卸载）
        # 项目的环境记录没被这次失败的安装改写
        assert projectenv.remembered_record(project)["trigger"] == projectenv.TRIGGER_RECOMMENDED


@needs_worker
class TestEndpoint:
    def _open(self, project: Path):
        from tavotto import app as m

        m.open_project(str(project))

    def test_the_plan_endpoint_takes_user_specified_and_discloses_it(
        self, client, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, LAB[1])
        self._open(project)
        resp = client.post(
            "/api/engine/dependencies/plan",
            json={"script": "figure.py", "user_specified": {LAB[1]: LAB[0]}},
        )
        assert resp.status_code == 200, resp.get_json()
        plan = resp.get_json()["plan"]
        assert plan["impact"]["impact_version"] == 2
        assert plan["impact"]["user_specified"][0]["trust"] == "user_specified_unverified"
        assert plan["joint"]["user_specified"][0]["resolution_source"] == "user_specified"
        assert plan["requirements"] == [LAB[0]]

    def test_bad_shapes_are_400_with_published_codes(
        self, client, tmp_path, house, offline_managed_env
    ):
        project = _project(tmp_path, LAB[1])
        self._open(project)
        for bad in ([LAB[1]], {LAB[1]: 3}, "x"):
            resp = client.post(
                "/api/engine/dependencies/plan", json={"script": "figure.py", "user_specified": bad}
            )
            assert resp.status_code == 400, bad
            assert resp.get_json()["code"] == deprepair.ERROR_REQUIREMENT_INVALID
        resp = client.post(
            "/api/engine/dependencies/plan",
            json={"script": "figure.py", "user_specified": {LAB[1]: "-r evil.txt"}},
        )
        assert resp.status_code == 400
        assert resp.get_json()["code"] == deprepair.ERROR_REQUIREMENT_INVALID
        resp = client.post(
            "/api/engine/dependencies/plan",
            json={"script": "figure.py", "user_specified": {"numpy": "numpy-evil"}},
        )
        assert resp.status_code == 400
        assert resp.get_json()["code"] == deprepair.ERROR_NOT_ALLOWED
        assert managedenv.python_of(project) is None


# ===========================================================================
# ⑥ #931 / #932
# ===========================================================================
def _plan_in(tmp_path, facts, script="import yaml\n"):
    proj = tmp_path / "proj"
    _write(proj, "plot.py", script)
    return depplan.plan(proj, "plot.py", facts=facts, target_kind="project_venv")


@pytest.mark.skipif(os.name == "nt", reason="符号链接在 Windows 上需要特权，CI 的 Windows 腿建不了")
class TestSymlinkedPthInAnUnprocessedLayer:
    """#931：Python 不处理 `.pth` 的层（经 `PYTHONPATH` 进来的 site-packages）里的符号链接 `.pth` 不生效、也没藏任何东西。"""

    def _layers(self, tmp_path):
        prefix, site = _site(tmp_path)
        other = tmp_path / "host" / "site-packages"
        other.mkdir(parents=True)
        real = _write(tmp_path / "shared", "real.pth", "# nothing\n")
        (other / "linked.pth").symlink_to(real)
        return prefix, site, other

    def test_a_really_missing_package_is_still_reported_missing(self, tmp_path):
        prefix, site, other = self._layers(tmp_path)
        facts = dataclasses.replace(
            _site_facts(prefix, site), site_roots=(str(site), str(other)), pth_roots=(str(site),)
        )
        plan = _plan_in(tmp_path, facts)
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"] and plan.origins == ()
        assert plan.requirements == ("pyyaml",)

    def test_control_in_a_layer_python_does_process_the_link_still_hides_the_names(self, tmp_path):
        prefix, site, other = self._layers(tmp_path)
        for pth in ((str(site), str(other)), ()):  # 处理它 / 没量到（旧口径：每层都处理）
            depplan.reset_cache()
            facts = dataclasses.replace(
                _site_facts(prefix, site), site_roots=(str(site), str(other)), pth_roots=pth
            )
            plan = _plan_in(tmp_path, facts)
            assert plan.missing == ()
            assert [o["reason"] for o in plan.origins] == ["unverified"]


class TestPackageInitFollowsTheTargetExtensionSuffixes:
    """#932①：前面一层只有别的平台留下的 `yaml/__init__.<tag>.pyd`（Linux 目标）不是会被加载的常规包，不能遮蔽后面 site 层里能用的 PyYAML。"""

    def _env(self, tmp_path, init_name):
        prefix, site = _site(tmp_path)
        _dist(site, "PyYAML", "6.0", "yaml")
        (site / "yaml.py").unlink()
        _write(site, "yaml/__init__.py", "")
        info = next(site.glob("PyYAML-*.dist-info"))
        (info / "RECORD").write_text("yaml/__init__.py,,\n", encoding="utf-8")
        front = tmp_path / "front"
        _write(front, f"yaml/{init_name}", "")
        return prefix, site, front

    def _facts(self, prefix, site, front, suffixes):
        return dataclasses.replace(
            _site_facts(prefix, site, {"pyyaml": "6.0"}),
            extra_roots=(str(front),),
            path_order=(str(front), str(site)),
            ext_suffixes=suffixes,
        )

    def test_a_foreign_platform_init_does_not_shadow_the_usable_provider(self, tmp_path):
        prefix, site, front = self._env(tmp_path, "__init__.cp313-win_amd64.pyd")
        plan = _plan_in(tmp_path, self._facts(prefix, site, front, LINUX_SUFFIXES))
        assert [s["distribution"] for s in plan.satisfied] == ["pyyaml"] and plan.origins == ()

    def test_the_targets_own_init_extension_does_shadow_it(self, tmp_path):
        prefix, site, front = self._env(tmp_path, "__init__.cp313-win_amd64.pyd")
        plan = _plan_in(tmp_path, self._facts(prefix, site, front, WINDOWS_SUFFIXES))
        assert plan.satisfied == () and len(plan.origins) == 1

    def test_a_plain_init_py_always_shadows_it(self, tmp_path):
        prefix, site, front = self._env(tmp_path, "__init__.py")
        plan = _plan_in(tmp_path, self._facts(prefix, site, front, LINUX_SUFFIXES))
        assert plan.satisfied == () and len(plan.origins) == 1

    def test_without_measured_suffixes_the_loose_shape_stays(self, tmp_path):
        prefix, site, front = self._env(tmp_path, "__init__.cp313-win_amd64.pyd")
        plan = _plan_in(tmp_path, self._facts(prefix, site, front, ()))
        assert plan.satisfied == () and len(plan.origins) == 1


class TestProviderNamesInTheExecutionTimeCheck:
    """#932②：层指纹只记层路径与 `.pth`，不记层里面的名字。确认之后往更前的 `PYTHONPATH` 目录新加同名模块，指纹不变，旧计划本会装一个
    运行时已被遮蔽的 PyYAML。"""

    def _setup(self, tmp_path, monkeypatch):
        project = tmp_path / "paper"
        _write(project, "figure.py", "import yaml\n")
        prefix, site = _site(tmp_path)
        front = tmp_path / "front"
        front.mkdir()
        monkeypatch.setattr(deprepair, "_base_python", sys.executable)
        monkeypatch.setattr(deprepair, "_base_python_known", True)
        monkeypatch.setattr(
            deprepair,
            "joint_target_for",
            lambda p, s: (deprepair.TARGET_MANAGED, str(prefix / "bin" / "python"), "x"),
        )
        monkeypatch.setattr(managedenv, "python_of", lambda root: str(prefix / "bin" / "python"))

        def facts_now(python, use_cache=True):
            depplan.reset_cache(python)  # 真 `target_facts` 重量时同样作废旧索引
            return dataclasses.replace(
                _site_facts(prefix, site),
                python=str(python),
                extra_roots=(str(front),),
                path_order=(str(front), str(site)),
            )

        monkeypatch.setattr(depplan, "target_facts", facts_now)
        seen: list = []
        monkeypatch.setattr(
            deprepair, "_run_generation", lambda job, ev: seen.append(job) or {"ran": True}
        )
        return project, front, seen, facts_now, str(prefix / "bin" / "python")

    def test_a_same_named_module_added_in_front_makes_the_plan_stale(self, tmp_path, monkeypatch):
        project, front, seen, facts_now, python = self._setup(tmp_path, monkeypatch)
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert plan.requirements == ("pyyaml",) and plan.names == ("yaml",)
        before = depplan.layer_digest(facts_now(python))
        _write(front, "yaml.py", "SHADOW = True\n")
        assert depplan.layer_digest(facts_now(python)) == before  # 层指纹看不见：这正是缺口
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_PLAN_STALE
        assert seen == []  # 一个字节不装

    def test_a_module_the_plan_does_not_use_does_not_invalidate_it(self, tmp_path, monkeypatch):
        project, front, seen, _facts_now, _python = self._setup(tmp_path, monkeypatch)
        plan = deprepair.create_joint_plan(project, "figure.py")
        _write(front, "unrelated_helper.py", "")
        deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert len(seen) == 1

    def test_a_confirmed_user_specified_import_is_covered_by_the_digest_before_any_byte_is_installed(
        self, tmp_path, monkeypatch
    ):
        """用户指定的 import 已离开 `unknown`、又不在 `needed`：名字集合漏了它，前层出现同名提供者时 pip 先改了环境才由装后核验发现。"""
        project, front, seen, _facts_now, _python = self._setup(tmp_path, monkeypatch)
        _write(project, "figure.py", "import lab_tools\n")
        pip_calls: list = []
        monkeypatch.setattr(
            deprepair, "_run_pip_install", lambda *a, **k: pip_calls.append(a) or (0, "")
        )
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={"lab_tools": "lab-tools"}
        )
        assert "lab_tools" in plan.names
        _write(front, "lab_tools.py", "SHADOW = True\n")
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_PLAN_STALE
        assert seen == [] and pip_calls == []  # 装任何字节之前拒绝

    def test_control_the_same_user_specified_plan_runs_when_nothing_appeared(
        self, tmp_path, monkeypatch
    ):
        project, _front, seen, _facts_now, _python = self._setup(tmp_path, monkeypatch)
        _write(project, "figure.py", "import lab_tools\n")
        plan = deprepair.create_joint_plan(
            project, "figure.py", user_specified={"lab_tools": "lab-tools"}
        )
        deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert len(seen) == 1

    def test_nothing_changed_is_accepted(self, tmp_path, monkeypatch):
        project, _front, seen, _facts_now, _python = self._setup(tmp_path, monkeypatch)
        plan = deprepair.create_joint_plan(project, "figure.py")
        deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert len(seen) == 1

    def test_the_digest_is_empty_without_an_index_so_substitute_facts_are_never_blocked(self):
        assert depplan.provider_digest(None, ("yaml",)) == ""
        assert depplan.provider_digest(_facts(), ("yaml",)) == ""  # 没有前缀：没有索引


# ===========================================================================
# 同源对：后端载荷 / 请求字段 ↔ web/src/lib/api.ts（只做类型，确认界面的组件归 PR6）
# ===========================================================================
class TestFrontendMirror:
    ROOT = Path(__file__).resolve().parents[1]

    def _api(self) -> str:
        return (self.ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")

    def test_the_trust_word_and_payload_keys_are_spelled_the_same_on_both_sides(self):
        api = self._api()
        assert f"'{deprepair.TRUST_USER_SPECIFIED_UNVERIFIED}'" in api
        for key in ("suggested_distribution", "user_specified", "verified_source"):
            assert key in api, key
        assert f"'{depresolve.SOURCE_USER_SPECIFIED}'" in api

    def test_the_request_field_is_read_by_the_endpoint_and_sent_by_the_client(self):
        app_src = (self.ROOT / "src" / "tavotto" / "app.py").read_text(encoding="utf-8")
        assert 'body.get("user_specified")' in app_src
        assert "user_specified?: Record<string, string>" in self._api()

    def test_the_guard_notices_a_drifted_mirror(self):
        drifted = self._api().replace("user_specified_unverified", "user_specified_checked")
        assert f"'{deprepair.TRUST_USER_SPECIFIED_UNVERIFIED}'" not in drifted


class TestFactsProbeKeepsTheFirstOfDuplicateDistributions:
    """`_FACTS_SRC` 的 `installed`：同名发行包在 `sys.path` 上出现多次，留排在前面（import 到的）那份，不让后面被遮蔽的覆盖。"""

    def test_first_on_sys_path_wins(self, tmp_path, monkeypatch):
        first, second = tmp_path / "first", tmp_path / "second"
        for root, ver in ((first, "2.0"), (second, "1.0")):
            _dist(root, "dup-pkg", ver, "dup_mod")
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(first), str(second))))
        facts = depplan.target_facts(sys.executable, use_cache=False)
        assert facts is not None and facts.installed["dup-pkg"] == "2.0"
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(second), str(first))))
        facts = depplan.target_facts(sys.executable, use_cache=False)
        assert facts.installed["dup-pkg"] == "1.0"
