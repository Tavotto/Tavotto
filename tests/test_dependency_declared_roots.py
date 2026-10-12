"""兼容性改造 P02（DEP-02 / DEP-03 / DEP-04 / DEP-11，AC-06 / AC-07 / AC-08 启用门）：声明成为真实安装根。

计划层（纯逻辑，合成目标事实，不装任何东西）。真实 generation + 联合 pip 的事务面在
`test_dependency_transaction.py::TestDeclaredRootsTransaction`。

三条边界一起钉：

* 选中的声明（requirements / PEP 621 / PEP 723）是新托管环境的安装 roots——哪怕脚本里没有一条被 AST 认出的 import
  能指到它（异名包、变量名 / 别名 / 函数体内的动态 import）；
* 已装版本不满足声明的版本约束算「差集」：托管环境里进安装集合并让计划 `ready`，用户自己的环境只记录偏差、
  不改状态、不进安装集合（不静默升降包）；
* roots 与旧 curated 推断不能取并集——同一个 import 名有多个互斥发行包（`cv2`）时，项目声明点名的那个胜出，
  声明里同时点名两个互斥的 = `dependency_conflict`，不是「两个都装」。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tavotto.engine import depplan, deprepair, depresolve, importscan

MANAGED = depplan.TARGET_MANAGED
VENV = depplan.TARGET_PROJECT_VENV


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _facts(installed: dict | None = None, **env) -> depplan.TargetFacts:
    marker_env = {
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
        **env,
    }
    return depplan.TargetFacts(
        python="/fake/python",
        marker_env=marker_env,
        stdlib=importscan.HOST_STDLIB,
        installed={"matplotlib": "3.10.0", "numpy": "2.1.0", **(installed or {})},
        prefix="/fake",
    )


@pytest.fixture(autouse=True)
def _clean():
    depplan.reset_cache()
    yield
    depplan.reset_cache()


def _project(tmp_path: Path, script: str, requirements: str | None = None, **files) -> Path:
    proj = tmp_path / "proj"
    _write(proj, "plot.py", script)
    if requirements is not None:
        _write(proj, "requirements.txt", requirements)
    for rel, text in files.items():
        _write(proj, rel, text)
    return proj


def _plan(proj, facts=None, target=MANAGED, **kw):
    return depplan.plan(proj, "plot.py", facts=facts or _facts(), target_kind=target, **kw)


# ---------------------------------------------------------------------------
# DEP-02：异名包——声明点名了安装名，脚本里的 import 名映射不到它
# ---------------------------------------------------------------------------
class TestRenamedProvider:
    def test_the_declared_provider_of_an_unmapped_import_is_a_root(self, tmp_path):
        proj = _project(
            tmp_path, "import auditclient\nprint(auditclient.VALUE)\n", "audit-driver==1.0\n"
        )
        plan = _plan(proj)
        assert plan.unknown == ("auditclient",)  # 来源仍然不猜：import 名没有映射
        assert plan.status == depplan.STATUS_READY
        assert plan.requirements == ("audit-driver==1.0",)
        assert plan.constraints == ()  # 不再只是约束
        (entry,) = plan.install_entries
        assert entry["distribution"] == "audit-driver"
        assert entry["resolution_source"] == depresolve.SOURCE_PROJECT_DECLARED
        assert entry["declared"] is True

    def test_nothing_changes_when_the_declared_provider_is_already_there(self, tmp_path):
        proj = _project(tmp_path, "import auditclient\n", "audit-driver==1.0\n")
        plan = _plan(proj, _facts({"audit-driver": "1.0"}))
        assert plan.requirements == ()
        assert plan.constraints == ("audit-driver==1.0",)  # 满足的声明照旧只是约束
        assert plan.declared_gaps == ()


# ---------------------------------------------------------------------------
# DEP-03 / DEP-04：动态导入的各种形态 × 三种声明格式
# ---------------------------------------------------------------------------
_VARIABLE = (
    'import importlib\nname = "auditlate"\nmod = importlib.import_module(name)\nprint(mod.VALUE)\n'
)
_DYNAMIC = {
    "canonical_literal": 'import importlib\nmod = importlib.import_module("auditlate")\nprint(mod.VALUE)\n',
    "alias_literal": 'import importlib as il\nmod = il.import_module("auditlate")\nprint(mod.VALUE)\n',
    "variable": _VARIABLE,
    "deferred": "def run():\n    import auditlate\n    print(auditlate.VALUE)\nrun()\n",
}


class TestDynamicImports:
    @pytest.mark.parametrize("shape", sorted(_DYNAMIC))
    def test_every_shape_of_a_dynamic_import_installs_the_declared_package(self, tmp_path, shape):
        proj = _project(tmp_path, _DYNAMIC[shape], "auditlate==1.0\n")
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_READY, shape
        assert plan.requirements == ("auditlate==1.0",), shape
        assert plan.constraints == (), shape

    @pytest.mark.parametrize("fmt", ["requirements", "pep621", "pep723"])
    def test_requirements_pep621_and_pep723_all_become_roots(self, tmp_path, fmt):
        if fmt == "requirements":
            proj = _project(tmp_path, _VARIABLE, "auditlate==1.0\n")
        elif fmt == "pep621":
            proj = _project(
                tmp_path,
                _VARIABLE,
                **{
                    "pyproject.toml": '[project]\nname = "p"\nversion = "0.1"\ndependencies = ["auditlate==1.0"]\n'
                },
            )
        else:
            proj = _project(
                tmp_path, '# /// script\n# dependencies = ["auditlate==1.0"]\n# ///\n' + _VARIABLE
            )
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_READY, fmt
        assert plan.requirements == ("auditlate==1.0",), fmt

    def test_extras_and_the_specifier_are_kept_on_the_root(self, tmp_path):
        proj = _project(tmp_path, _VARIABLE, "AuditLate[wide]>=1,<3\n")
        plan = _plan(proj)
        assert plan.requirements == ("auditlate[wide]<3,>=1",)

    def test_an_import_that_is_not_the_declared_package_does_not_make_it_a_root_alone(
        self, tmp_path
    ):
        """没有一条 import 需求能指到的声明，不为它单独启动安装：脚本静态 import 的都在、没有未解释的 import / 动态导入。"""
        proj = _project(tmp_path, "import numpy\n", "pytest>=8\n")
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        # 但它被如实记下（不静默忽略），并标明与这个脚本没有关联
        (gap,) = plan.declared_gaps
        assert gap["distribution"] == "pytest" and gap["state"] == "absent"
        assert gap["relevant"] is False


# ---------------------------------------------------------------------------
# 反例：不成为 root 的东西
# ---------------------------------------------------------------------------
class TestNotRoots:
    def test_unselected_group_marker_false_and_pure_constraints_are_not_roots(self, tmp_path):
        proj = _project(
            tmp_path,
            _VARIABLE,
            'auditlate==1.0\nauditnever==1.0; sys_platform == "never_os"\n',
            **{
                "requirements-train.txt": "audittrain==1.0\n",
                "constraints.txt": "auditconstrained<2\n",
            },
        )
        plan = _plan(proj)
        assert plan.requirements == ("auditlate==1.0",)
        assert "audittrain" not in " ".join(plan.requirements)
        assert "auditnever" not in " ".join(plan.requirements)
        assert "auditconstrained" not in " ".join(plan.requirements)
        assert "auditconstrained<2" in plan.constraints  # 约束照样管版本
        assert {g["distribution"] for g in plan.declared_gaps} == {"auditlate"}

    def test_the_user_venv_target_never_turns_declarations_into_installs(self, tmp_path):
        """用户自己的 venv：只进不退的原地安装仍然只装脚本静态需要的缺口，声明偏差只记录。"""
        proj = _project(tmp_path, _VARIABLE, "auditlate==1.0\n")
        plan = _plan(proj, target=VENV)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert plan.requirements == ()
        (gap,) = plan.declared_gaps
        assert gap["action"] == "recorded"

    def test_hash_mode_is_untouched(self, tmp_path):
        h = "sha256:" + "a" * 64
        proj = _project(tmp_path, _VARIABLE, f"auditlate==1.0 --hash={h}\n")
        plan = _plan(proj)
        assert plan.require_hashes is True
        assert plan.hashes == {"auditlate==1.0": (h,)}


_H = " --hash=sha256:" + "a" * 64


def _lock(*lines: str) -> str:
    """完整 hash 锁：adapter 两项钉死（`==`）+ 给定的行。"""
    return "\n".join(f"{ln}{_H}" for ln in ("matplotlib==3.10.0", "numpy==2.1.0", *lines)) + "\n"


class TestInstallSetDecidesNotTheRootsFlag:
    """Codex #960 round-2 P2：ready 与冲突看**本计划实际安装集合**，不看「是不是 roots 模式」这个全局开关。"""

    def test_a_hash_lock_relevant_only_through_a_dynamic_import_is_ready(self, tmp_path):
        proj = _project(tmp_path, _VARIABLE, _lock("auditlate==1"))
        plan = _plan(proj)
        assert plan.require_hashes is True and plan.requirements
        (gap,) = [g for g in plan.declared_gaps if g["distribution"] == "auditlate"]
        assert gap["relevant"] is True and gap["state"] == "absent"
        assert plan.status == depplan.STATUS_READY
        assert gap["action"] == "install"
        assert plan.actionable

    def test_a_hash_lock_gap_that_the_script_never_touches_stays_nothing_needed(self, tmp_path):
        proj = _project(tmp_path, "import numpy\n", _lock("auditlate==1"))
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED

    def test_two_cv2_providers_do_not_block_a_script_that_never_installs_them(self, tmp_path):
        proj = _project(
            tmp_path,
            "import numpy\n",
            "opencv-python-headless==4.10.0.84\nopencv-contrib-python==4.10.0.84\n",
        )
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert plan.requirements == ()
        assert not [b for b in plan.blocked if b["code"] == depplan.BLOCK_CONFLICT]

    def test_two_cv2_providers_block_a_script_that_imports_cv2(self, tmp_path):
        proj = _project(
            tmp_path,
            "import cv2\n",
            "opencv-python-headless==4.10.0.84\nopencv-contrib-python==4.10.0.84\n",
        )
        assert _plan(proj).status == depplan.STATUS_BLOCKED

    def test_two_cv2_providers_block_when_a_dynamic_import_makes_both_installable(self, tmp_path):
        proj = _project(
            tmp_path,
            _VARIABLE,
            "opencv-python-headless==4.10.0.84\nopencv-contrib-python==4.10.0.84\n",
        )
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_BLOCKED
        assert [b for b in plan.blocked if b["code"] == depplan.BLOCK_CONFLICT]

    def test_a_hash_lock_holding_two_exclusive_providers_is_a_conflict(self, tmp_path):
        proj = _project(
            tmp_path,
            "import numpy\n",
            _lock("opencv-python-headless==4.10.0.84", "opencv-contrib-python==4.10.0.84"),
        )
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_BLOCKED
        (block,) = [b for b in plan.blocked if b["code"] == depplan.BLOCK_CONFLICT]
        assert "cv2" in {c["name"] for c in block["conflicts"]}


# ---------------------------------------------------------------------------
# DEP-11 / AC-07：版本差异影响准备状态
# ---------------------------------------------------------------------------
_TWO_CV2 = "opencv-python-headless==4.10.0.84\nopencv-contrib-python==4.10.0.84\n"
_NONBINDING = {
    "optional": "try:\n    import cv2\nexcept ImportError:\n    cv2 = None\n",
    "type_checking": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import cv2\n",
    "conditional": "import sys\nif sys.argv[1:]:\n    import cv2\n",
}


class TestBindingContextsAreOneDefinition:
    """Codex #960 round-3 P2：互斥 provider 的冲突触发与声明相关性共用「绑定上下文」一处定义；非字面量动态导入也带外围上下文。"""

    @pytest.mark.parametrize("shape", sorted(_NONBINDING))
    def test_a_non_binding_cv2_import_neither_blocks_nor_installs_either_provider(
        self, tmp_path, shape
    ):
        proj = _project(tmp_path, _NONBINDING[shape], _TWO_CV2)
        plan = _plan(proj)
        assert plan.status != depplan.STATUS_BLOCKED
        assert not [b for b in plan.blocked if b["code"] == depplan.BLOCK_CONFLICT]
        assert plan.requirements == ()
        assert {g["distribution"] for g in plan.declared_gaps} == {
            "opencv-python-headless",
            "opencv-contrib-python",
        }
        assert not any(g["relevant"] for g in plan.declared_gaps)

    def test_an_unconditional_cv2_import_still_blocks(self, tmp_path):
        proj = _project(tmp_path, "import cv2\n", _TWO_CV2)
        # 无条件 import 仍是冲突（正例对照）
        assert _plan(proj).status == depplan.STATUS_BLOCKED

    @pytest.mark.parametrize(
        "body",
        [
            "try:\n    mod = importlib.import_module(name)\nexcept ImportError:\n    mod = None\n",
            "if name:\n    mod = importlib.import_module(name)\n",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    mod = importlib.import_module(name)\n",
        ],
    )
    def test_a_dynamic_import_in_a_non_binding_context_makes_nothing_relevant(self, tmp_path, body):
        script = 'import importlib\nname = "auditlate"\n' + body
        proj = _project(tmp_path, script, "auditlate>=1\n")
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert plan.requirements == ()
        (gap,) = plan.declared_gaps
        assert gap["relevant"] is False

    def test_an_unconditional_or_deferred_dynamic_import_still_makes_the_declaration_relevant(
        self, tmp_path
    ):
        proj = _project(tmp_path, _VARIABLE, "auditlate>=1\n")
        assert _plan(proj).status == depplan.STATUS_READY
        deferred = "import importlib\ndef run(name):\n    return importlib.import_module(name)\n"
        proj2 = _project(tmp_path / "b", deferred, "auditlate>=1\n")
        assert _plan(proj2).status == depplan.STATUS_READY

    def test_the_scanner_keeps_the_enclosing_context_without_changing_the_payload(self, tmp_path):
        script = (
            "import importlib\n"
            "try:\n    importlib.import_module(a)\nexcept ImportError:\n    pass\n"
            "importlib.import_module(b)\n"
        )
        proj = _project(tmp_path, script)
        scan = importscan.scan(proj, "plot.py")
        assert [(u.context, u.enclosing) for u in scan.dynamic] == [
            ("dynamic", "optional"),
            ("dynamic", "unconditional"),
        ]
        assert all("enclosing" not in d for d in scan.to_payload()["dynamic"])


class TestVersionGap:
    SCRIPT = "import auditroot\nprint(auditroot.VALUE)\n"

    def test_a_declared_floor_above_the_installed_version_makes_the_plan_ready(self, tmp_path):
        proj = _project(tmp_path, self.SCRIPT, "auditroot>=2.0\n")
        plan = _plan(proj, _facts({"auditroot": "1.0"}))
        assert plan.status == depplan.STATUS_READY
        assert plan.requirements == ("auditroot>=2.0",)
        assert plan.constraints == ()
        (gap,) = plan.declared_gaps
        assert gap == {
            "distribution": "auditroot",
            "specifier": ">=2.0",
            "state": "version_unsatisfied",
            "installed_version": "1.0",
            "relevant": True,
            "action": "install",
        }
        # 旧的事实面不动：satisfied 里仍如实写着「装着、但不满足声明」
        (sat,) = plan.satisfied
        assert sat["installed_version"] == "1.0" and sat["matches_declared"] is False

    def test_a_satisfied_version_stays_a_constraint(self, tmp_path):
        proj = _project(tmp_path, self.SCRIPT, "auditroot>=2.0\n")
        plan = _plan(proj, _facts({"auditroot": "2.1"}))
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert plan.requirements == () and plan.constraints == ("auditroot>=2.0",)
        assert plan.declared_gaps == ()

    def test_the_users_own_environment_records_the_deviation_and_changes_nothing(self, tmp_path):
        proj = _project(tmp_path, self.SCRIPT, "auditroot>=2.0\n")
        plan = _plan(proj, _facts({"auditroot": "1.0"}), target=VENV)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED  # 能原样跑：不自动升降包
        assert plan.requirements == ()
        (gap,) = plan.declared_gaps
        assert (gap["state"], gap["action"], gap["installed_version"]) == (
            "version_unsatisfied",
            "recorded",
            "1.0",
        )

    def test_a_run_environment_that_is_not_ours_to_replace_is_only_recorded(self, tmp_path):
        """受管目标，但此刻跑脚本的是用户交给我们的系统解释器（`run_env_replaceable=False`）：能跑就不为偏差动手。"""
        proj = _project(tmp_path, self.SCRIPT, "auditroot>=2.0\n")
        plan = _plan(proj, _facts({"auditroot": "1.0"}), run_env_replaceable=False)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        (gap,) = plan.declared_gaps
        assert gap["action"] == "recorded"

    def test_the_payload_carries_the_gaps_only_when_there_are_some(self, tmp_path):
        proj = _project(tmp_path, self.SCRIPT, "auditroot>=2.0\n")
        with_gap = _plan(proj, _facts({"auditroot": "1.0"})).to_payload()
        assert with_gap["declared_gaps"][0]["distribution"] == "auditroot"
        clean = _plan(proj, _facts({"auditroot": "2.0"})).to_payload()
        assert "declared_gaps" not in clean

    def test_a_relevant_gap_past_the_payload_cap_still_decides_the_status(self, tmp_path):
        """Codex #960 P2：40 个不相关的缺席声明排在前面，相关的版本差集在第 41 位——状态用完整缺口集合判，只截断对外载荷。"""
        extra = "".join(f"audit-pkg-{i:02d}\n" for i in range(depplan.MAX_DECLARED_GAPS))
        proj = _project(tmp_path, "import zstandard\n", extra + "zstandard>=2\n")
        plan = _plan(proj, _facts({"zstandard": "1.0"}))
        assert plan.status == depplan.STATUS_READY
        assert "zstandard>=2" in plan.requirements
        assert len(plan.declared_gaps) == depplan.MAX_DECLARED_GAPS
        payload = plan.to_payload()
        assert payload["declared_gaps_total"] == depplan.MAX_DECLARED_GAPS + 1
        small = _plan(
            proj, _facts({"zstandard": "1.0", **{f"audit-pkg-{i:02d}": "1" for i in range(40)}})
        )
        assert "declared_gaps_total" not in small.to_payload()


# ---------------------------------------------------------------------------
# AC-08 启用门：roots 与 curated 推断不能并集出两个互斥 provider（cv2）
# ---------------------------------------------------------------------------
CV2 = depresolve.distribution_alternatives("cv2")


class TestProviderGate:
    @pytest.mark.parametrize("provider", CV2)
    def test_the_declared_cv2_provider_wins_over_the_curated_table(self, tmp_path, provider):
        proj = _project(tmp_path, "import cv2\n", f"{provider}==4.10.0.84\n")
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_READY
        assert plan.requirements == (f"{provider}==4.10.0.84",)
        assert [m["distribution"] for m in plan.install_entries] == [provider]
        installs = {depresolve.normalize_distribution(r.split("=")[0]) for r in plan.requirements}
        assert len(installs & set(CV2)) == 1  # 每代只有一个 cv2 provider

    @pytest.mark.parametrize("provider", CV2)
    def test_the_same_holds_on_the_users_venv_target(self, tmp_path, provider):
        proj = _project(tmp_path, "import cv2\n", f"{provider}==4.10.0.84\n")
        plan = _plan(proj, target=VENV)
        assert plan.requirements == (f"{provider}==4.10.0.84",)
        assert [m["distribution"] for m in plan.missing] == [provider]

    def test_an_undeclared_cv2_keeps_the_curated_behaviour(self, tmp_path):
        proj = _project(tmp_path, "import cv2\n")
        assert _plan(proj).requirements == ("opencv-python",)

    def test_a_declared_provider_replaces_a_different_installed_one_only_where_we_own_the_env(
        self, tmp_path
    ):
        """已装 opencv-python，声明要 headless：托管环境里换代；用户的 venv 里能 import 就不动。"""
        proj = _project(tmp_path, "import cv2\n", "opencv-python-headless==4.10.0.84\n")
        facts = _facts({"opencv-python": "4.9.0.80"})
        owned = _plan(proj, facts)
        assert owned.status == depplan.STATUS_READY
        assert owned.requirements == ("opencv-python-headless==4.10.0.84",)
        theirs = _plan(proj, facts, target=VENV)
        assert theirs.status == depplan.STATUS_NOTHING_NEEDED and theirs.requirements == ()

    def test_two_exclusive_providers_declared_together_are_a_conflict_not_both_installed(
        self, tmp_path
    ):
        proj = _project(
            tmp_path,
            "import cv2\n",
            "opencv-python-headless==4.10.0.84\nopencv-contrib-python==4.10.0.84\n",
        )
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_BLOCKED
        (block,) = [b for b in plan.blocked if b["code"] == depplan.BLOCK_CONFLICT]
        names = {c["name"] for c in block["conflicts"]}
        assert "cv2" in names
        assert not plan.actionable  # 不是「都装」，是不可执行

    def test_a_declared_provider_whose_module_the_script_never_imports_is_not_relevant(
        self, tmp_path
    ):
        """Codex #960 P2：只 `import numpy`，共享 requirements 点名 headless——不因仲裁结果装 OpenCV、计划不 ready。"""
        proj = _project(tmp_path, "import numpy\n", "opencv-python-headless==4.10.0.84\n")
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert plan.requirements == ()
        assert [g for g in plan.declared_gaps if g["relevant"]] == []

    def test_an_unexplained_import_does_not_make_an_unimported_provider_relevant(self, tmp_path):
        """脚本有归不到发行包的 import（声明成权威）时，已知模块名的 provider 没被 import 仍不相关。"""
        proj = _project(
            tmp_path,
            "import auditclient\n",
            "audit-driver==1.0\nopencv-python-headless==4.10.0.84\n",
        )
        plan = _plan(proj)
        assert plan.requirements == ("audit-driver==1.0",)

    def test_an_imported_module_still_installs_the_declared_provider(self, tmp_path):
        proj = _project(tmp_path, "import cv2\n", "opencv-python-headless==4.10.0.84\n")
        plan = _plan(proj)
        assert plan.status == depplan.STATUS_READY
        assert plan.requirements == ("opencv-python-headless==4.10.0.84",)

    def test_the_arbitration_does_not_read_observed_providers(self, tmp_path):
        """观察来源不授权安装：运行环境里是 contrib-headless，声明什么都没点名 = 仍是 curated 名。"""
        proj = _project(tmp_path, "import cv2\n")
        plan = _plan(
            proj,
            _facts({"opencv-contrib-python-headless": "4.10.0.84"}),
            install_facts=_facts({}),
        )
        assert plan.requirements == ("opencv-python",)


# ---------------------------------------------------------------------------
# 账本：旧 provider 不被合并带回来
# ---------------------------------------------------------------------------
class TestLedgerProviderSwitch:
    def _ledger(self, tmp_path, *entries):
        from tavotto.engine import managedenv

        project = tmp_path / "paper"
        project.mkdir()
        managedenv.write_manifest(project, managedenv.new_manifest(project, "/x/py"))
        for dist, ver in entries:
            managedenv.record_install(
                project,
                import_name="cv2",
                distribution=dist,
                requested_specifier="",
                resolved_version=ver,
                reason="user_requested",
            )
        return project

    def test_a_replaced_provider_is_not_merged_back_from_the_ledger(self, tmp_path):
        project = self._ledger(tmp_path, ("opencv-python", "4.9.0.80"), ("lmfit", "1.3.2"))
        got = deprepair.generation_requirements(project, ("opencv-python-headless==4.10.0.84",))
        names = [deprepair._name_of(r) for r in got]
        assert "opencv-python" not in names
        assert "opencv-python-headless" in names and "lmfit" in names

    def test_an_unrelated_ledger_entry_is_kept(self, tmp_path):
        project = self._ledger(tmp_path, ("opencv-python", "4.9.0.80"))
        got = deprepair.generation_requirements(project, ("six",))
        assert "opencv-python==4.9.0.80" in got

    def test_two_exclusive_providers_already_in_the_ledger_collapse_to_the_latest(self, tmp_path):
        project = self._ledger(
            tmp_path, ("opencv-python", "4.9.0.80"), ("opencv-python-headless", "4.10.0.84")
        )
        got = deprepair.generation_requirements(project, ())
        names = [deprepair._name_of(r) for r in got]
        assert names.count("opencv-python-headless") == 1 and "opencv-python" not in names

    def test_peers_come_from_the_one_alternatives_table(self):
        assert depresolve.provider_peers("opencv-python") == frozenset(
            depresolve.normalize_distribution(d) for d in CV2 if d != "opencv-python"
        )
        assert depresolve.provider_peers("six") == frozenset()
