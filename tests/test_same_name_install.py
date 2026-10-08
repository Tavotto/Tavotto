"""表外 import 名按「同名 + 装完核验」安装（维护者 2026-10-08 裁决；ADR 0061 FO-034 修订）。

真实事故：Windows beta 用户的脚本缺 `lxml`，卡片说「运行时缺 lxml，找不到安装包」——`lxml` 不在
`CURATED` / `SAME_NAME` 里，旧规则一律判 unknown、「不装、不猜同名」。新规则：表外的名字按**同名**当 PyPI
包名的候选（来源 `same_name_unverified`），用户点了才装、只装 wheel、只进 Tavotto 受管环境的新一代，
装完在新一代里核验「这个发行包确实提供这个模块、import 得进」，不过就不激活（回到上一代）。

五条判据（每条一组用例，每条都做过变异反证，见 PR 正文）：

| 判据 | 用例 |
|---|---|
| 表外的名字 → 可装、同名、需核验；改名过的（`docx` / `sklearn` / `cv2` / `yaml` / `PIL`…）与同名禁区绝不同名装 | `TestResolution` |
| 授权页面看得出这是未经核对的同名包；用户自己的 Python 不往里装 | `TestAuthorisationAndBoundary` |
| 装完核验：发行包不提供这个模块 / import 失败 → 不激活、上一代原样、给 `dependency_same_name_mismatch` | `TestVerifiedInstall` |
| PyPI 上没有这个名字 → 装不了，`dependency_not_found`，同样不激活 | `TestVerifiedInstall` |
| 跑前的联合扫描不猜同名（只有运行时真的 import 失败才问到同名候选） | `TestResolution::test_pre_run_scan_still_does_not_guess` |

安装与 PyPI 都不联网：临时 wheelhouse + pip 自己的 `PIP_FIND_LINKS` / `PIP_NO_INDEX`（与
`test_dependency_repair_e2e.py` 同一套），「PyPI 404」= 本地 index 里没有这个名字（pip 的输出与真 404 同形）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker, real_venv, wait_for
from tavotto.engine import (
    depplan,
    deprepair,
    depresolve,
    importscan,
    managedenv,
    projectenv,
)

pytest_plugins = ("support.dependency_repair",)


@pytest.fixture(autouse=True)
def _clean(clean_state):
    depplan.reset_cache()
    yield
    depplan.reset_cache()


#: 只存在于测试 wheelhouse 里的纯 Python 包；名字刻意不像任何真包，也不在任何一张表里。
UNLISTED = "tavotto_test_unlisted_pkg"  # import 名 == 发行包名（规范化后）：同名候选能装对
SQUAT = "tavotto_test_squat_pkg"  # 同名的发行包存在，但它**不提供**这个模块（抢注 / 空壳的形状）
BROKEN = "tavotto_test_broken_pkg"  # 同名发行包提供这个模块，但 import 会炸
HOLLOW_REAL = "tavotto_test_hollow_real"  # 另一个发行包，提供 SQUAT 这个模块（被同名发行包依赖）
GHOST = "tavotto_test_ghost_pkg"  # wheelhouse 里没有这个名字：PyPI 404


def _dist(name: str) -> str:
    return name.replace("_", "-")


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    build_wheel(dest, name=_dist(UNLISTED), import_name=UNLISTED)
    # 空壳：发行包叫 SQUAT，里面的模块却叫别的名字——pip 退出码 0，但脚本要的 SQUAT 并不在里面
    build_wheel(dest, name=_dist(SQUAT), import_name="tavotto_test_something_else")
    build_wheel(dest, name=_dist(BROKEN), import_name=BROKEN, body='raise RuntimeError("boom")\n')
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _script(project: Path, module: str) -> None:
    (project / "figure.py").write_text(
        f"import {module}\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\nax.plot([1, 2], [3, 4])\nfig.savefig('Fig1.pdf')\n",
        encoding="utf-8",
    )


def _importable(python: str, module: str) -> bool:
    return (
        subprocess.run(
            [python, "-c", f"import {module}"], capture_output=True, timeout=120
        ).returncode
        == 0
    )


# ===========================================================================
# 一、解析
# ===========================================================================
class TestResolution:
    def test_an_unlisted_name_becomes_an_unverified_same_name_candidate(self, tmp_path):
        req = depresolve.resolve(tmp_path, UNLISTED)
        assert req is not None
        assert req.distribution == UNLISTED
        assert req.resolution_source == depresolve.SOURCE_SAME_NAME_UNVERIFIED
        assert req.confidence == depresolve.CONFIDENCE_UNVERIFIED
        assert req.installable and req.needs_verification
        assert req.to_payload()["unverified"] is True
        assert depresolve.SOURCE_SAME_NAME_UNVERIFIED in depresolve.INSTALLABLE_SOURCES

    def test_lxml_off_the_table_is_installable_by_the_same_name(self, tmp_path, monkeypatch):
        """真实事故的那个名字。与「往 SAME_NAME 补 lxml」的并行改动互不依赖：这里显式把它从白名单拿掉再问。"""
        monkeypatch.setattr(depresolve, "SAME_NAME", depresolve.SAME_NAME - {"lxml"})
        req = depresolve.resolve(tmp_path, "lxml")
        assert (req.distribution, req.resolution_source) == (
            "lxml",
            depresolve.SOURCE_SAME_NAME_UNVERIFIED,
        )
        assert req.requirement() == "lxml" and req.installable

    @pytest.mark.parametrize(
        "import_name,distribution",
        [
            ("docx", "python-docx"),
            ("sklearn", "scikit-learn"),
            ("cv2", "opencv-python"),
            ("yaml", "PyYAML"),
            ("PIL", "Pillow"),
        ],
    )
    def test_renamed_packages_never_install_by_the_same_name(
        self, tmp_path, import_name, distribution
    ):
        """`docx` 的同名包是错包、`sklearn` 是弃用占位、`cv2` / `yaml` / `PIL` 同理：查 CURATED，绝不同名。"""
        req = depresolve.resolve(tmp_path, import_name)
        assert req.distribution == distribution != import_name
        assert req.resolution_source == depresolve.SOURCE_CURATED
        assert not req.needs_verification

    def test_every_curated_key_is_a_same_name_no_go_zone(self, tmp_path):
        """CURATED 的键就是同名禁区：整张表走一遍，没有一个会以同名候选的身份出现。"""
        assert depresolve.CURATED
        for name in depresolve.CURATED:
            assert depresolve.same_name_forbidden(name), name
            req = depresolve.resolve(tmp_path, name)
            assert (
                req is not None and req.resolution_source != depresolve.SOURCE_SAME_NAME_UNVERIFIED
            )
            assert req.distribution == depresolve.CURATED[name]

    def test_the_no_same_name_table_resolves_to_nothing(self, tmp_path):
        """禁同名表里的名字（`dotenv` 的发行包叫 python-dotenv、`utils` 几乎总是用户自己的文件…）→ 不猜。"""
        assert {"dotenv", "jwt", "utils", "Crypto"} <= depresolve.NO_SAME_NAME
        for name in sorted(depresolve.NO_SAME_NAME):
            assert depresolve.resolve(tmp_path, name) is None, name
        # 大小写 / 连字符写法不同也在禁区里（文件系统不分大小写的平台上用户会这么写）
        assert depresolve.same_name_forbidden("Dotenv") and depresolve.same_name_forbidden("DOCX")

    @pytest.mark.parametrize("name", ["_private", "a.b", "not valid", "-r", ""])
    def test_a_name_that_is_not_a_package_name_is_never_a_candidate(self, tmp_path, name):
        assert depresolve.resolve(tmp_path, name) is None

    def test_declared_and_curated_still_outrank_the_same_name_candidate(self, tmp_path):
        (tmp_path / "requirements.txt").write_text(f"{_dist(UNLISTED)}==1.0\n", encoding="utf-8")
        req = depresolve.resolve(tmp_path, UNLISTED)
        assert req.resolution_source == depresolve.SOURCE_PROJECT_DECLARED
        assert not req.needs_verification
        assert depresolve.resolve(tmp_path, "numpy").resolution_source == depresolve.SOURCE_CURATED

    def test_pre_run_scan_still_does_not_guess(self):
        """跑前的联合扫描没有「它真的缺」的证据，不为一个也许是私有 / 动态 / 本地的名字去猜包——
        只有运行时真的 import 失败之后问 `resolve` 才有同名候选（ADR 0061 FO-034 修订的边界）。"""
        assert importscan.map_distribution(UNLISTED, {}) == ("", "")


# ===========================================================================
# 二、授权页面与边界
# ===========================================================================
@needs_worker
class TestAuthorisationAndBoundary:
    def test_the_impact_names_the_unverified_package(self, project):
        _script(project, UNLISTED)
        plan = deprepair.create_plan(
            str(project), "figure.py", UNLISTED, target_kind=deprepair.TARGET_MANAGED
        )
        impact = plan.impact
        assert impact["unverified_same_name"] == [UNLISTED]
        assert impact["impact_version"] == deprepair.IMPACT_VERSION == 2
        assert impact["modifies_user_environment"] is False
        assert impact["rollback"] == deprepair.ROLLBACK_GENERATION_ATOMIC
        assert plan.to_payload()["unverified"] is True

    def test_a_declared_package_has_nothing_to_flag(self, project):
        (project / "requirements.txt").write_text(f"{_dist(UNLISTED)}\n", encoding="utf-8")
        _script(project, UNLISTED)
        plan = deprepair.create_plan(
            str(project), "figure.py", UNLISTED, target_kind=deprepair.TARGET_MANAGED
        )
        assert plan.impact["unverified_same_name"] == []

    def test_the_flag_is_part_of_the_authorisation_digest(self, project):
        """旧同意不覆盖新增的「未经核对」：同一个需求，标记不同，摘要就不同。"""
        _script(project, UNLISTED)
        plan = deprepair.create_plan(
            str(project), "figure.py", UNLISTED, target_kind=deprepair.TARGET_MANAGED
        )
        flat = dict(plan.impact, unverified_same_name=[])
        assert deprepair.impact_digest(flat) != plan.impact_digest

    def test_it_is_never_offered_for_the_users_own_python(self, project):
        """用户自己的 venv 不往里装：offer 不给项目 venv 这个目标，create_plan 也拒绝。"""
        venv = real_venv(project)
        _script(project, UNLISTED)
        detail = {"code": projectenv.ERROR_MODULE_MISSING, "venv": str(venv)}
        offer = deprepair.offer(str(project), "figure.py", UNLISTED, detail)
        kinds = {t["kind"] for t in offer["targets"]}
        assert deprepair.TARGET_MANAGED in kinds
        assert deprepair.TARGET_PROJECT_VENV not in kinds
        assert offer["requirement"]["resolution_source"] == depresolve.SOURCE_SAME_NAME_UNVERIFIED
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.create_plan(
                str(project), "figure.py", UNLISTED, target_kind=deprepair.TARGET_PROJECT_VENV
            )
        assert err.value.code == deprepair.ERROR_NOT_ALLOWED

    def test_a_curated_package_still_may_go_into_the_project_venv(self, project):
        """对照：边界只收紧同名候选，不动既有的、用户确认过的项目 venv 安装。"""
        venv = real_venv(project)
        _script(project, "lmfit")
        detail = {"code": projectenv.ERROR_MODULE_MISSING, "venv": str(venv)}
        offer = deprepair.offer(str(project), "figure.py", "lmfit", detail)
        assert deprepair.TARGET_PROJECT_VENV in {t["kind"] for t in offer["targets"]}

    def test_a_forbidden_name_gets_no_install_target_at_all(self, project):
        offer = deprepair.offer(str(project), "figure.py", "dotenv", None)
        assert offer["requirement"] is None
        assert offer["code"] == deprepair.ERROR_UNRESOLVED
        assert offer["targets"] == []


# ===========================================================================
# 三、装完核验（真建 venv、真跑 pip，离线 wheelhouse）
# ===========================================================================
@needs_worker
class TestVerifiedInstall:
    def _install(self, project, module: str) -> tuple[dict, deprepair.RepairPlan]:
        _script(project, module)
        plan = deprepair.create_plan(
            str(project), "figure.py", module, target_kind=deprepair.TARGET_MANAGED
        )
        assert deprepair.install_async(plan.plan_id, confirmed_impact=plan.impact_digest)
        return wait_for(plan.plan_id), plan

    def test_a_matching_package_installs_and_the_generation_activates(
        self, project, house, offline_managed_env
    ):
        final, plan = self._install(project, UNLISTED)
        assert final["state"] == deprepair.STATE_DONE, final
        python = managedenv.python_of(project)
        assert python and _importable(python, UNLISTED)
        entry = next(
            e
            for e in managedenv.read_manifest(project)["installed_by_tavotto"]
            if e["distribution"] == UNLISTED
        )
        assert entry["import_name"] == UNLISTED and entry["resolved_version"] == "1.0"
        assert plan.requirement.needs_verification

    def test_a_dist_that_does_not_provide_the_module_is_rolled_back(
        self, project, house, offline_managed_env
    ):
        """同名发行包存在、pip 退出码 0，但脚本要的模块不在里面：不激活，没有受管环境被留下来。"""
        final, _plan = self._install(project, SQUAT)
        assert final["state"] == deprepair.STATE_FAILED
        assert final["code"] == deprepair.ERROR_SAME_NAME_MISMATCH, final
        assert managedenv.python_of(project) is None, "核验不过的这一代不许激活"

    def test_a_failed_verification_keeps_the_previous_generation(
        self, project, house, offline_managed_env
    ):
        """先装好一个（第一代），再装一个核验不过的：active 仍是第一代，第一代里的包还在、坏包不在。"""
        ok, _ = self._install(project, UNLISTED)
        assert ok["state"] == deprepair.STATE_DONE, ok
        first = managedenv.python_of(project)
        deprepair.reset_state(str(project))  # 清掉轮次 / 已试过：这里要的是第二次独立的尝试
        bad, _ = self._install(project, SQUAT)
        assert bad["state"] == deprepair.STATE_FAILED
        assert bad["code"] == deprepair.ERROR_SAME_NAME_MISMATCH
        assert managedenv.python_of(project) == first
        assert _importable(first, UNLISTED)
        assert not _importable(first, "tavotto_test_something_else")

    def test_a_module_provided_by_someone_else_does_not_count(
        self, project, tmp_path, monkeypatch, offline_managed_env
    ):
        """import 得进不等于它提供：同名发行包依赖了另一个提供该模块的发行包——import 成功，但**不是它的**，
        核验看的是发行包元数据（top_level / RECORD），不只是 import。"""
        dest = tmp_path / "house2"
        build_wheel(dest, name=_dist(HOLLOW_REAL), import_name=SQUAT)  # 真正提供 SQUAT 的是它
        build_wheel(
            dest,
            name=_dist(SQUAT),
            import_name="tavotto_test_something_else",
            requires=(_dist(HOLLOW_REAL),),
        )
        monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
        monkeypatch.setenv("PIP_NO_INDEX", "1")
        final, _ = self._install(project, SQUAT)
        assert final["state"] == deprepair.STATE_FAILED
        assert final["code"] == deprepair.ERROR_SAME_NAME_MISMATCH, final
        assert managedenv.python_of(project) is None

    def test_a_module_that_cannot_be_imported_is_rolled_back(
        self, project, house, offline_managed_env
    ):
        final, _ = self._install(project, BROKEN)
        assert final["state"] == deprepair.STATE_FAILED
        assert final["code"] == deprepair.ERROR_SAME_NAME_MISMATCH, final
        assert managedenv.python_of(project) is None

    def test_a_name_pypi_does_not_have_is_not_installable(
        self, project, house, offline_managed_env
    ):
        """PyPI 404：pip 的输出是「(from versions: none)」→ `dependency_not_found`，不激活。"""
        final, _ = self._install(project, GHOST)
        assert final["state"] == deprepair.STATE_FAILED
        assert final["code"] == deprepair.ERROR_NOT_FOUND, final
        assert managedenv.python_of(project) is None

    def _install_batch(self, project, modules: list[str], declared: str):
        """脚本 import 了 `modules`；`declared` 在 requirements.txt 里声明（所以首个受管代次把它们一起装）。"""
        (project / "requirements.txt").write_text(f"{declared}\n", encoding="utf-8")
        _script(project, modules[0])
        script = (project / "figure.py").read_text(encoding="utf-8")
        (project / "figure.py").write_text(
            "".join(f"import {m}\n" for m in modules[1:]) + script, encoding="utf-8"
        )
        plan = deprepair.create_plan(
            str(project), "figure.py", modules[0], target_kind=deprepair.TARGET_MANAGED
        )
        assert deprepair.install_async(plan.plan_id, confirmed_impact=plan.impact_digest)
        return wait_for(plan.plan_id), plan

    def test_a_failure_caused_by_another_requirement_in_the_batch_names_that_package(
        self, project, house, offline_managed_env
    ):
        """首个受管代次一起装脚本要的全部依赖：同名候选（wheelhouse 里有）没问题，批次里另一个声明依赖不可得——
        pip 点名的是后者，失败载荷的 `failed_distribution` 就是后者，不是同名候选。"""
        final, plan = self._install_batch(
            project, [UNLISTED, "tavotto_test_declared"], "tavotto-test-declared"
        )
        assert final["state"] == deprepair.STATE_FAILED
        assert final["code"] == deprepair.ERROR_NOT_FOUND, final
        assert plan.widened is not None and len(plan.requirements) > 1
        assert final["failed_distribution"] == "tavotto-test-declared"
        assert final["failed_distribution"] != depresolve.normalize_distribution(UNLISTED)

    def test_a_failure_of_the_same_name_candidate_names_the_candidate(
        self, project, house, offline_managed_env
    ):
        """同一批里，不可得的恰好是同名候选本身（另一个声明依赖在 wheelhouse 里）→ 点名的就是它。"""
        build_wheel(house, name="tavotto-test-declared", import_name="tavotto_test_declared")
        final, plan = self._install_batch(
            project, [GHOST, "tavotto_test_declared"], "tavotto-test-declared"
        )
        assert final["state"] == deprepair.STATE_FAILED
        assert final["code"] == deprepair.ERROR_NOT_FOUND, final
        assert len(plan.requirements) > 1
        assert final["failed_distribution"] == depresolve.normalize_distribution(GHOST)

    def test_a_single_candidate_batch_is_attributable_even_when_pip_names_nothing(self):
        """批次只有那一个候选时别无他选；批次多于一个、pip 又没点名时不猜（卡片回落通用文案）。"""
        assert deprepair.failed_distribution_of("boom", ("Ghost_Pkg>=1",)) == "ghost-pkg"
        assert deprepair.failed_distribution_of("boom", ("a", "b")) == ""
        assert deprepair.failed_distribution_of("boom", ()) == ""
        assert deprepair.failed_distribution_of("", ("a", "b")) == ""

    @pytest.mark.parametrize(
        ("text", "want"),
        [
            ("ERROR: No matching distribution found for Foo_Bar", "foo-bar"),
            ("ERROR: No matching distribution found for foo>=1.2,<2", "foo"),
            (
                "ERROR: Could not find a version that satisfies the requirement lxml (from versions: none)\n"
                "ERROR: No matching distribution found for lxml",
                "lxml",
            ),
            (
                "ERROR: Could not find a version that satisfies the requirement bar>=1 (from foo) (from versions: 0.1)",
                "bar",
            ),
        ],
    )
    def test_pip_output_names_the_failed_distribution(self, text, want):
        assert deprepair.failed_distribution_of(text, ("a", "b")) == want

    def test_the_install_is_wheels_only(self):
        """同名候选走的就是既有的安装命令：只装 wheel、不 `--upgrade`（`--only-binary=:all:` 不变）。"""
        argv = deprepair.pip_install_argv("/env/bin/python", UNLISTED)
        assert "--only-binary=:all:" in argv and "--upgrade" not in argv
        joint = deprepair.pip_install_joint_argv("/env/bin/python", Path("r.txt"), Path("c.txt"))
        assert "--only-binary=:all:" in joint


class TestVerificationProbe:
    """核验探针本身：隔离子进程、有超时、发行包不提供就不 import。"""

    def test_a_package_that_does_not_provide_the_module_is_never_imported(self, tmp_path):
        """抢注包的代码不该因为我们的核验而被执行：发行包元数据里没有这个模块，探针不 import。"""
        site = tmp_path / "site"
        site.mkdir()
        marker = tmp_path / "ran.txt"
        (site / "hollow_mod.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", "utf-8")
        info = site / "other_dist-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: other-dist\nVersion: 1.0\n", "utf-8"
        )
        (info / "RECORD").write_text(
            "other_dist.py,,\nother_dist-1.0.dist-info/RECORD,,\n", "utf-8"
        )
        import json
        import os
        import sys

        out = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                deprepair._SAME_NAME_PROBE_SRC,
                '[["hollow_mod", "other-dist"]]',
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(site)},
        )
        assert out.returncode == 0, out.stderr
        rec = json.loads(out.stdout)["hollow_mod"]
        assert rec["installed"] is True and rec["provided"] is False
        assert not marker.exists(), "不提供该模块的发行包，探针不许 import 它"

    def test_an_unstartable_interpreter_counts_as_not_verified(self, tmp_path):
        got = deprepair.probe_same_name(
            str(tmp_path / "no-such-python"), (("some_mod", "some-dist"),)
        )
        assert got["some_mod"]["provided"] is False and got["some_mod"]["import"]
        with pytest.raises(deprepair.RepairError) as err:
            deprepair._verify_same_name(
                str(tmp_path / "no-such-python"), (("some_mod", "some-dist"),)
            )
        assert err.value.code == deprepair.ERROR_SAME_NAME_MISMATCH

    def test_the_probe_has_a_timeout(self, monkeypatch):
        calls: list[dict] = []

        def _fake_run(argv, **kw):
            calls.append(kw)
            raise subprocess.TimeoutExpired(argv, kw["timeout"])

        monkeypatch.setattr(deprepair.subprocess, "run", _fake_run)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair._verify_same_name("python", (("some_mod", "some-dist"),))
        assert err.value.code == deprepair.ERROR_SAME_NAME_MISMATCH
        assert calls and calls[0]["timeout"] == deprepair.SAME_NAME_VERIFY_TIMEOUT_S
        assert calls[0]["stdin"] == subprocess.DEVNULL
