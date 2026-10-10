"""Import Origin PR5a：PR4（#920）遗留、要求「PR5 前修」的四条 issue（#922 #924 #925 #929）。

每条一节：修前见红的验收用例 + 对照用例（无变化时行为不变）。Windows 路径形态不写死（`tests/support/envworld`）。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from support import envworld
from tavotto.engine import depplan, deprepair, envadvice, envlease, projectenv, runtime

pytest_plugins = ("support.dependency_repair",)


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


# ===========================================================================
# #922 执行期过期检查绑定 import 层
# ===========================================================================
class TestExecutionTimeLayerCheck:
    """确认与执行之间 `.pth` / 可见 site 层变了而 `importlib.metadata` 的名字版本表没变：`TargetFacts.digest()` 不变，
    旧计划本会被接受。执行期再比一次 import 层指纹（`depplan.layer_digest`），变了 = 同一条 `repair_plan_stale`。"""

    def _setup(self, tmp_path, monkeypatch):
        project = tmp_path / "paper"
        site = tmp_path / "venv" / "site-packages"
        site.mkdir(parents=True)
        _write(project, "requirements.txt", "six\n")
        _write(project, "figure.py", "import six\n")
        monkeypatch.setattr(deprepair, "_base_python", sys.executable)
        monkeypatch.setattr(deprepair, "_base_python_known", True)
        monkeypatch.setattr(
            deprepair,
            "joint_target_for",
            lambda p, s: (deprepair.TARGET_MANAGED, sys.executable, "x"),
        )
        state = {"extra": ()}

        def facts_now(python, use_cache=True):
            return depplan.TargetFacts(
                python=python,
                marker_env={},
                stdlib=frozenset(),
                installed={},
                site_roots=(str(site),),
                extra_roots=tuple(state["extra"]),
            )

        monkeypatch.setattr(depplan, "target_facts", facts_now)
        return project, site, state

    @staticmethod
    def _ran(monkeypatch):
        seen = []

        def fake(job, cancel_ev):
            seen.append(job)
            return {"ran": True}

        monkeypatch.setattr(deprepair, "_run_generation", fake)
        return seen

    def test_a_pth_added_in_the_confirmation_window_makes_the_plan_stale(
        self, tmp_path, monkeypatch
    ):
        project, site, state = self._setup(tmp_path, monkeypatch)
        seen = self._ran(monkeypatch)
        plan = deprepair.create_joint_plan(project, "figure.py")
        extra = tmp_path / "elsewhere"
        extra.mkdir()
        _write(site, "late.pth", f"{extra}\n")  # 新增指向含该模块目录的 .pth
        state["extra"] = (str(extra),)  # 重量事实会看到它加进了 sys.path
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_PLAN_STALE
        assert seen == []  # 一个字节不装
        assert deprepair.get_joint_plan(plan.plan_id) is None

    def test_a_pth_edited_in_place_makes_the_plan_stale_even_when_the_path_list_is_the_same(
        self, tmp_path, monkeypatch
    ):
        """可见路径列表一个字没变、只是 .pth 的内容换了（比如多了一行 import 钩子）：指纹含 .pth 内容。"""
        project, site, state = self._setup(tmp_path, monkeypatch)
        seen = self._ran(monkeypatch)
        _write(site, "hook.pth", "# nothing yet\n")
        plan = deprepair.create_joint_plan(project, "figure.py")
        _write(site, "hook.pth", "import sitecustomize_hook\n")
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_PLAN_STALE
        assert seen == []

    def test_a_visible_site_layer_appearing_makes_the_plan_stale(self, tmp_path, monkeypatch):
        project, site, state = self._setup(tmp_path, monkeypatch)
        plan = deprepair.create_joint_plan(project, "figure.py")
        state["extra"] = (str(tmp_path / "new_layer"),)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_PLAN_STALE

    def test_a_zip_or_egg_source_appearing_makes_the_plan_stale(self, tmp_path, monkeypatch):
        """`opaque_roots`（PYTHONPATH 上的 zip / egg）也在指纹里：多一个不可读来源就不是同一个 import 层。"""
        project, site, state = self._setup(tmp_path, monkeypatch)
        plan = deprepair.create_joint_plan(project, "figure.py")
        monkeypatch.setattr(
            depplan,
            "target_facts",
            lambda python, use_cache=True: depplan.TargetFacts(
                python=python,
                marker_env={},
                stdlib=frozenset(),
                installed={},
                site_roots=(str(site),),
                opaque_roots=1,
            ),
        )
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_PLAN_STALE

    def test_nothing_changed_runs_as_before(self, tmp_path, monkeypatch):
        project, site, state = self._setup(tmp_path, monkeypatch)
        seen = self._ran(monkeypatch)
        _write(site, "hook.pth", "# nothing yet\n")
        plan = deprepair.create_joint_plan(project, "figure.py")
        assert deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest) == {"ran": True}
        assert len(seen) == 1

    def test_the_layer_fingerprint_is_not_a_plan_input(self, tmp_path, monkeypatch):
        """不改 inputs_digest / identity / 披露：层不同的两份事实算出同一份 identity 与 payload。"""
        project, site, state = self._setup(tmp_path, monkeypatch)
        a = deprepair.create_joint_plan(project, "figure.py")
        _write(site, "late.pth", "/somewhere\n")
        b = deprepair.create_joint_plan(project, "figure.py")
        assert a.layers_digest != b.layers_digest
        assert (a.identity, a.inputs_digest, a.impact_digest) == (
            b.identity,
            b.inputs_digest,
            b.impact_digest,
        )
        assert "layers" not in str(a.to_payload()) and "layers" not in str(a.joint)

    def test_facts_without_layers_have_an_empty_fingerprint(self):
        bare = depplan.TargetFacts(python="p", marker_env={}, stdlib=frozenset(), installed={})
        assert depplan.layer_digest(bare) == "" and depplan.layer_digest(None) == ""
        assert bare.digest() == dataclasses.replace(bare, site_roots=("/x",)).digest()

    def test_with_a_real_venv_a_pth_added_after_confirming_makes_the_plan_stale(
        self, tmp_path, monkeypatch
    ):
        """真 venv + 真事实探测：确认后往它的 site-packages 放一个指向别处目录的 `.pth`（包名版本表没变，`facts.digest()` 不变）。"""
        import subprocess

        from support import envworld

        venv = tmp_path / "pv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(venv)],
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=300,
        )
        python = str(envworld.venv_python(venv))
        project = tmp_path / "paper"
        _write(project, "requirements.txt", "six\n")
        _write(project, "figure.py", "import six\n")
        monkeypatch.setattr(
            deprepair,
            "joint_target_for",
            lambda p, s: (deprepair.TARGET_PROJECT_VENV, python, "x"),
        )
        monkeypatch.setattr(deprepair, "_refuse_if_pinned", lambda: None)
        plan = deprepair.create_joint_plan(project, "figure.py")
        before = depplan.target_facts(python, use_cache=False)
        # `getsitepackages()` 在 Windows 首项是 sys.prefix：真正的 site 目录取叫 site-packages 的那一项
        site = next(r for r in before.site_roots if Path(r).name.lower() == "site-packages")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        _write(Path(site), "late.pth", f"{elsewhere}\n")
        after = depplan.target_facts(python, use_cache=False)
        assert after.digest() == before.digest()  # 名字版本表没变：旧判据看不出来
        assert depplan.layer_digest(after) != depplan.layer_digest(before)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.prepare(plan.plan_id, confirmed_impact=plan.impact_digest)
        assert err.value.code == deprepair.ERROR_PLAN_STALE


# ===========================================================================
# #924 ① 映射发行包与备选同时装着 = 歧义；② 内置候选的覆盖度角色尊重全局解释器锁
# ===========================================================================
def _facts(installed: dict) -> depplan.TargetFacts:
    return depplan.TargetFacts(
        python="p",
        marker_env={"python_full_version": "3.12.1", "sys_platform": "linux"},
        stdlib=frozenset({"os", "sys"}),
        installed=dict(installed),
    )


def _plan_of(tmp_path, script: str, installed: dict, requirements: str = ""):
    proj = tmp_path / "proj"
    _write(proj, "plot.py", script)
    if requirements:
        _write(proj, "requirements.txt", requirements)
    return depplan.plan(proj, "plot.py", facts=_facts(installed), target_kind="project_venv")


class TestMappedAndAlternativeBothInstalled:
    BOTH = {"opencv-python": "4.9.0", "opencv-python-headless": "4.9.0"}

    def test_both_installed_and_undeclared_is_ambiguous_not_satisfied(self, tmp_path):
        plan = _plan_of(tmp_path, "import cv2\n", self.BOTH)
        assert plan.satisfied == () and plan.missing == () and plan.requirements == ()
        assert plan.unknown == ("cv2",)
        (origin,) = plan.origins
        assert origin["reason"] == "module_origin_ambiguous"

    def test_the_declared_one_is_satisfied(self, tmp_path):
        plan = _plan_of(tmp_path, "import cv2\n", self.BOTH, "opencv-python-headless>=4\n")
        (entry,) = plan.satisfied
        assert entry["distribution"] == "opencv-python-headless" and plan.origins == ()

    def test_declaring_the_mapped_one_is_satisfied_too(self, tmp_path):
        plan = _plan_of(tmp_path, "import cv2\n", self.BOTH, "opencv-python>=4\n")
        (entry,) = plan.satisfied
        assert entry["distribution"] == "opencv-python" and plan.origins == ()

    def test_only_the_mapped_one_installed_stays_satisfied(self, tmp_path):
        plan = _plan_of(tmp_path, "import cv2\n", {"opencv-python": "4.9.0"})
        (entry,) = plan.satisfied
        assert entry["distribution"] == "opencv-python" and plan.origins == ()

    def test_an_ordinary_single_provider_import_is_unchanged(self, tmp_path):
        plan = _plan_of(tmp_path, "import yaml\n", {"pyyaml": "6.0"})
        (entry,) = plan.satisfied
        assert entry["distribution"] == "pyyaml" and plan.origins == ()


class TestBuiltinCoverageRoleFollowsTheGlobalLock:
    @pytest.fixture
    def seen(self, tmp_path, monkeypatch):
        root = tmp_path / "p"
        _write(root, "p.py", "import covok_a\n")
        monkeypatch.setattr(runtime, "bundled_python", lambda: sys.executable)
        projectenv.remember_default(root)  # 项目明确选回默认链条
        roles: list[str] = []

        def fake(python, mods, *, role, bundled=False):
            roles.append(role)
            return {"role": role}

        monkeypatch.setattr(envadvice, "_coverage_for", fake)
        return root, roles

    def test_without_a_global_lock_the_builtin_candidate_is_the_target(self, seen, monkeypatch):
        root, roles = seen
        monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
        monkeypatch.setattr(envadvice.pool, "explicit_worker_python", lambda: None)
        out = envadvice.recommend(root, "p.py", modules=("covok_a",))
        builtin = out["candidates"][-1]
        assert builtin["id"] == "builtin" and builtin["coverage"]["role"] == envadvice.ROLE_TARGET

    def test_a_global_interpreter_makes_the_builtin_candidate_a_user_environment(
        self, seen, tmp_path, monkeypatch
    ):
        root, roles = seen
        other = tmp_path / "elsewhere" / envworld.venv_rel("g")
        other.parent.mkdir(parents=True)
        other.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            envadvice.pool, "explicit_worker_python", lambda: (str(other), "environment")
        )
        out = envadvice.recommend(root, "p.py", modules=("covok_a",))
        builtin = out["candidates"][-1]
        assert builtin["id"] == "builtin" and builtin["current"] is True  # 记住的决定还在
        assert builtin["coverage"]["role"] == envadvice.ROLE_USER

    def test_the_global_interpreter_being_the_bundled_one_keeps_it_the_target(
        self, seen, monkeypatch
    ):
        root, roles = seen
        monkeypatch.setattr(
            envadvice.pool, "explicit_worker_python", lambda: (sys.executable, "environment")
        )
        out = envadvice.recommend(root, "p.py", modules=("covok_a",))
        assert out["candidates"][-1]["coverage"]["role"] == envadvice.ROLE_TARGET


# ===========================================================================
# #925 ① 未建模的 .pth 钩子让名字层判不完整；② 环境静态索引用目标解释器的精确扩展后缀
# ===========================================================================
LINUX_SUFFIXES = (".cpython-313-x86_64-linux-gnu.so", ".abi3.so", ".so")
WINDOWS_SUFFIXES = (".cp313-win_amd64.pyd", ".pyd")


def _site(tmp_path: Path, name: str = "venv") -> tuple[Path, Path]:
    """造一个只有 site-packages 的假环境前缀（解释器一次也不起）。site-packages 的目录名用平台里真的那种，不写死 `lib/pythonX`。"""
    prefix = tmp_path / name
    site = prefix / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    _write(prefix, "pyvenv.cfg", "home = /usr/bin\nversion = 3.13.0\n")
    return prefix, site


def _site_facts(prefix: Path, site: Path, installed=None, **kw) -> depplan.TargetFacts:
    base = _facts(installed or {})
    return dataclasses.replace(
        base,
        python=str(prefix / "bin" / "python"),
        prefix=str(prefix),
        site_roots=(str(site),),
        **kw,
    )


def _plan_in(tmp_path, facts, script="import yaml\n"):
    proj = tmp_path / "proj"
    _write(proj, "plot.py", script)
    return depplan.plan(proj, "plot.py", facts=facts, target_kind="project_venv")


class TestUnmodelledPthHooks:
    def test_an_unmodelled_import_hook_keeps_the_package_out_of_missing(self, tmp_path):
        prefix, site = _site(tmp_path)
        _write(site, "hook.pth", "import some_custom_finder; some_custom_finder.install()\n")
        plan = _plan_in(tmp_path, _site_facts(prefix, site))
        assert plan.missing == () and plan.requirements == ()
        assert plan.status == "nothing_needed"
        (origin,) = plan.origins
        assert origin["import_name"] == "yaml" and origin["reason"] == "unverified"
        assert origin["distribution_status"] == "environment_not_checked"

    def test_a_path_line_that_is_not_followed_is_the_same(self, tmp_path):
        prefix, site = _site(tmp_path)
        _write(site, "extra.pth", str(tmp_path / "somewhere-else") + "\n")
        plan = _plan_in(tmp_path, _site_facts(prefix, site))
        assert plan.missing == () and [o["reason"] for o in plan.origins] == ["unverified"]

    def test_only_the_known_benign_hooks_still_report_missing(self, tmp_path):
        prefix, site = _site(tmp_path)
        _write(site, "_virtualenv.pth", "import _virtualenv\n")
        _write(
            site,
            "distutils-precedence.pth",
            "import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, 'local')"
            " == 'local'; enabled and __import__('_distutils_hack').add_shim();\n",
        )
        plan = _plan_in(tmp_path, _site_facts(prefix, site))
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"]
        assert plan.requirements == ("pyyaml",) and plan.origins == ()

    def test_a_hook_in_a_layer_python_never_runs_is_not_counted(self, tmp_path):
        """经 `PYTHONPATH` / `.pth` 路径行进 `sys.path` 的 site-packages 不处理它的 `.pth`：那里的 `import` 行不会执行，
        不让名字层判不完整（否则任何「挂了宿主 site-packages」的环境都被当成看不全）。"""
        prefix, site = _site(tmp_path)
        other = tmp_path / "host" / "site-packages"
        other.mkdir(parents=True)
        _write(other, "hook.pth", "import some_custom_finder\n")
        base = _site_facts(prefix, site)
        both = dataclasses.replace(base, site_roots=(str(site), str(other)))
        processed_only_own = dataclasses.replace(both, pth_roots=(str(site),))
        plan = _plan_in(tmp_path, processed_only_own)
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"] and plan.origins == ()
        # 对照：那一层也是 Python 会处理的 site 目录 → 钩子照常让名字层看不全；没量到 `pth_roots` 也按旧口径
        for facts in (dataclasses.replace(both, pth_roots=(str(site), str(other))), both):
            depplan.reset_cache()
            plan = _plan_in(tmp_path, facts)
            assert plan.missing == () and [o["reason"] for o in plan.origins] == ["unverified"]

    def test_a_path_line_to_another_reported_site_layer_is_followed(self, tmp_path):
        prefix, site = _site(tmp_path)
        other = tmp_path / "host" / "site-packages"
        other.mkdir(parents=True)
        _write(site, "host.pth", f"{other}\n")
        facts = dataclasses.replace(_site_facts(prefix, site), site_roots=(str(site), str(other)))
        plan = _plan_in(tmp_path, facts)
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"] and plan.origins == ()

    def test_the_new_generation_is_still_installed_for(self, tmp_path):
        """钩子在**当前**环境里：新建的一代没有它，装进新一代不会遮蔽任何东西。"""
        prefix, site = _site(tmp_path)
        _write(site, "hook.pth", "import some_custom_finder\n")
        proj = tmp_path / "proj"
        _write(proj, "plot.py", "import yaml\n")
        plan = depplan.plan(
            proj,
            "plot.py",
            facts=_site_facts(prefix, site),
            target_kind="tavotto_managed",
            install_facts=_facts({}),
        )
        assert "pyyaml" in plan.requirements


class TestExactExtensionSuffixes:
    def _layer(self, tmp_path, file_name):
        prefix, site = _site(tmp_path)
        (site / file_name).write_bytes(b"")
        return prefix, site

    def test_a_win_pyd_on_a_linux_target_is_not_yaml_being_there(self, tmp_path):
        prefix, site = self._layer(tmp_path, "yaml.cp313-win_amd64.pyd")
        plan = _plan_in(tmp_path, _site_facts(prefix, site, ext_suffixes=LINUX_SUFFIXES))
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"]
        assert plan.requirements == ("pyyaml",) and plan.origins == ()

    def test_the_targets_own_extension_is_a_provider(self, tmp_path):
        prefix, site = self._layer(tmp_path, "yaml.cpython-313-x86_64-linux-gnu.so")
        plan = _plan_in(tmp_path, _site_facts(prefix, site, ext_suffixes=LINUX_SUFFIXES))
        assert plan.missing == () and [o["reason"] for o in plan.origins] == ["unverified"]

    def test_a_windows_target_takes_the_pyd(self, tmp_path):
        prefix, site = self._layer(tmp_path, "yaml.cp313-win_amd64.pyd")
        plan = _plan_in(tmp_path, _site_facts(prefix, site, ext_suffixes=WINDOWS_SUFFIXES))
        assert plan.missing == () and [o["reason"] for o in plan.origins] == ["unverified"]

    def test_without_measured_suffixes_the_loose_shape_stays(self, tmp_path):
        prefix, site = self._layer(tmp_path, "yaml.cp313-win_amd64.pyd")
        facts = _site_facts(prefix, site)
        assert facts.ext_suffixes == ()
        plan = _plan_in(tmp_path, facts)
        assert plan.missing == () and [o["reason"] for o in plan.origins] == ["unverified"]

    def test_the_record_of_an_installed_distribution_is_read_with_the_same_suffixes(self, tmp_path):
        from tavotto.engine import distmeta

        prefix, site = _site(tmp_path)
        info = site / "foo-1.0.dist-info"
        info.mkdir()
        _write(info, "METADATA", "Metadata-Version: 2.1\nName: foo\nVersion: 1.0\n")
        _write(info, "RECORD", "fastlib.cp313-win_amd64.pyd,,\nfoo-1.0.dist-info/METADATA,,\n")
        linux = distmeta.index_environment(
            prefix, site_paths=[str(site)], ext_suffixes=LINUX_SUFFIXES
        )
        win = distmeta.index_environment(
            prefix, site_paths=[str(site)], ext_suffixes=WINDOWS_SUFFIXES
        )
        assert linux.lookup("fastlib").candidates == ()
        assert [c.distribution for c in win.lookup("fastlib").candidates] == ["foo"]

    def test_suffixes_are_not_a_plan_input(self, tmp_path):
        prefix, site = _site(tmp_path)
        a = _site_facts(prefix, site)
        b = dataclasses.replace(a, ext_suffixes=LINUX_SUFFIXES, pth_roots=(str(site),))
        assert a.digest() == b.digest() and a.to_payload() == b.to_payload()
