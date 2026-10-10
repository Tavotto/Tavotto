"""Import Origin PR5a：PR4（#920）遗留、要求「PR5 前修」的四条 issue（#922 #924 #925 #929）。

每条一节：修前见红的验收用例 + 对照用例（无变化时行为不变）。Windows 路径形态不写死（`tests/support/envworld`）。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from tavotto.engine import depplan, deprepair, envlease

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
