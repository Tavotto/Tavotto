"""兼容性改造 P03（DEP-05 / DEP-07 / DEP-08 / DEP-14，AC-08 / AC-09 / AC-22）：Import Origin 的 provider、版本、来源接进安装决策（计划层）。

授权不变量（ADR 0123）：**观察到的提供者只能用于披露**——让计划说出「环境里现在是谁、新一代会装谁、两者是否不同」，
不能单独决定装什么。装哪个发行包只有三条授权通道（`depresolve.INSTALLABLE_SOURCES`）：项目声明、Tavotto 的表、用户确认的指定。
editable / 本地 wheel / VCS 的观察照实列出（`reproducible=false`），从不折成 PyPI `name==version`。

纯逻辑（合成目标事实 + tmp 里造的 dist-info，不起解释器、不装任何东西）。真实 generation + 联合 pip 的事务面见
`test_dependency_provider_scopes.py`。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import depplan, depresolve, importscan

MANAGED = depplan.TARGET_MANAGED
OPENCV = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)
SP_REL = "lib/python3.12/site-packages"
_EDITABLE = {"url": "file:///src/fork", "dir_info": {"editable": True}}
_LOCAL_WHEEL = {"url": "file:///wheels/labtool-1.whl", "archive_info": {}}
_VCS = {"url": "https://example.invalid/r.git", "vcs_info": {"vcs": "git", "commit_id": "abc"}}


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _facts(installed: dict | None = None, prefix: str = "/fake") -> depplan.TargetFacts:
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
    }
    return depplan.TargetFacts(
        python=f"{prefix}/bin/python",
        marker_env=marker_env,
        stdlib=importscan.HOST_STDLIB,
        installed={"matplotlib": "3.10.0", "numpy": "2.1.0", **(installed or {})},
        prefix=prefix,
    )


def _env(tmp_path: Path, name: str = "run") -> tuple[Path, Path]:
    prefix = tmp_path / name
    site = prefix / SP_REL
    site.mkdir(parents=True)
    _write(prefix, "pyvenv.cfg", "home = /usr/bin\nversion = 3.12.1\n")
    return prefix, site


def _dist(site: Path, name: str, version: str, *, top: str, direct_url: dict | None = None) -> None:
    d = site / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\nBODY\n", encoding="utf-8"
    )
    (d / "top_level.txt").write_text(top, encoding="utf-8")
    rows = [f"{top.strip()}/__init__.py,sha256=abc,10", f"{d.name}/METADATA,,"]
    (d / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if direct_url is not None:
        (d / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")


def _running(tmp_path: Path, dists: dict[str, tuple[str, str, dict | None]]):
    """跑脚本的环境：`{发行包: (版本, 顶层 import 名, direct_url)}`。"""
    prefix, site = _env(tmp_path)
    for name, (version, top, direct) in dists.items():
        _dist(site, name, version, top=f"{top}\n", direct_url=direct)
    return _facts({n: v[0] for n, v in dists.items()}, prefix=str(prefix))


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


def _plan(proj, running, **kw):
    kw.setdefault("install_facts", _facts({}))  # 新一代装之前：什么都没有
    return depplan.plan(proj, "plot.py", facts=running, target_kind=MANAGED, **kw)


def _decision(plan, name: str) -> dict:
    (hit,) = [d for d in plan.provider_decisions if d["import_name"] == name]
    return hit


# ===========================================================================
# DEP-05：四个 cv2 provider，各自作为「已观察」与「已声明」
# ===========================================================================
@pytest.mark.parametrize("observed", OPENCV)
class TestObservedProviders:
    def test_the_observation_is_disclosed_with_version_and_provenance(self, tmp_path, observed):
        proj = _project(tmp_path, "import cv2\n")
        plan = _plan(proj, _running(tmp_path, {observed: ("4.10.0.84", "cv2", None)}))
        d = _decision(plan, "cv2")
        assert [
            (o["distribution"], o["version"], o["provenance"], o["reproducible"])
            for o in d["observed"]
        ] == [(observed, "4.10.0.84", "index", True)]
        assert d["basis"] == depresolve.SOURCE_CURATED and d["action"] == "install"
        # 沿用表：新一代装的是表里的名字，不是观察到的——观察不是授权
        assert plan.requirements == ("opencv-python",)
        assert d["replaces_observed"] is (observed != "opencv-python")

    @pytest.mark.parametrize("declared", OPENCV)
    def test_the_declaration_beats_the_observation_and_only_that_provider_is_installed(
        self, tmp_path, observed, declared
    ):
        proj = _project(tmp_path, "import cv2\n", requirements=f"{declared}==4.10.0.84\n")
        plan = _plan(proj, _running(tmp_path, {observed: ("4.10.0.84", "cv2", None)}))
        assert plan.requirements == (f"{declared}==4.10.0.84",)  # 每代只装所需的那一种，保留约束
        d = _decision(plan, "cv2")
        assert d["distribution"] == declared and d["basis"] == depresolve.SOURCE_PROJECT_DECLARED
        assert not plan.blocked
        installed_cv2 = {
            depresolve.normalize_distribution(r.split("==")[0]) for r in plan.requirements
        } & set(OPENCV)
        assert installed_cv2 == {declared}

    def test_observation_never_changes_what_is_installed(self, tmp_path, observed):
        """ADR 0123 的授权不变量（替换原来的「安装模块不读观察」阶段性 AST 看护）：同一个项目，有观察与没有观察
        （`dists=None`），`requirements` / `constraints` / `identity` / `status` 逐字相同；只有披露字段不同。"""
        proj = _project(tmp_path, "import cv2\n")
        running = _running(tmp_path, {observed: ("4.10.0.84", "cv2", None)})
        with_obs = _plan(proj, running)
        without = _plan(proj, running, dists=None)
        for field in ("requirements", "constraints", "identity", "status", "missing", "unknown"):
            assert getattr(with_obs, field) == getattr(without, field), field
        assert any(d["observed"] for d in with_obs.provider_decisions)
        assert not any(
            d["observed"] for d in without.provider_decisions
        )  # 没读索引 = 没有观察可披露


class TestHashProviderReceipt:
    def _plan(self, tmp_path):
        lock = "\n".join(
            f"{name}=={version} --hash=sha256:{'a' * 64}"
            for name, version in (
                ("opencv-python-headless", "4.10.0.84"),
                ("matplotlib", "3.10.0"),
                ("numpy", "2.1.0"),
            )
        )
        proj = _project(tmp_path, "import cv2\n", requirements=lock + "\n")
        active = _running(tmp_path, {"opencv-python": ("4.10.0.84", "cv2", None)})
        return _plan(proj, active, install_facts=active)

    def test_the_lock_discloses_the_provider_it_will_install_not_the_active_alternative(
        self, tmp_path
    ):
        """#970：active 的 opencv-python 满足 import，不代表 hash 锁不会安装 headless。"""
        plan = self._plan(tmp_path)
        assert plan.status == depplan.STATUS_READY and plan.require_hashes
        assert plan.requirements == (
            "opencv-python-headless==4.10.0.84",
            "matplotlib==3.10.0",
            "numpy==2.1.0",
        )
        d = _decision(plan, "cv2")
        assert (d["action"], d["distribution"], d["basis"]) == (
            "install",
            "opencv-python-headless",
            depresolve.SOURCE_PROJECT_DECLARED,
        )
        assert d["replaces_observed"] is True

    def test_the_lock_records_the_import_mapping_for_the_provider_actually_installed(
        self, tmp_path
    ):
        plan = self._plan(tmp_path)
        (entry,) = [e for e in plan.install_entries if e["import_name"] == "cv2"]
        assert entry["distribution"] == "opencv-python-headless"
        assert entry["resolution_source"] == depresolve.SOURCE_PROJECT_DECLARED
        assert not [e for e in plan.install_entries if e["distribution"] == "opencv-python"]


class TestTwoDeclaredProvidersStayAConflict:
    def test_two_declared_providers_are_not_both_installed(self, tmp_path):
        proj = _project(
            tmp_path,
            "import cv2\n",
            requirements="opencv-python==4.10.0.84\nopencv-contrib-python-headless==4.10.0.84\n",
        )
        plan = _plan(proj, _running(tmp_path, {"opencv-python": ("4.10.0.84", "cv2", None)}))
        assert [b["code"] for b in plan.blocked] == [depplan.BLOCK_CONFLICT]

    def test_two_observed_providers_without_a_declaration_stay_undecided(self, tmp_path):
        """同名多来源：运行环境里两个 cv2 provider 都装着、项目没点名——歧义保留，不替用户挑。"""
        proj = _project(tmp_path, "import cv2\n")
        running = _running(
            tmp_path,
            {
                "opencv-python-headless": ("4.10.0.84", "cv2", None),
                "opencv-contrib-python": ("4.10.0.84", "cv2", None),
            },
        )
        plan = depplan.plan(proj, "plot.py", facts=running, target_kind=MANAGED)
        assert plan.unknown == ("cv2",)
        assert plan.origins[0]["reason"] == depplan.ORIGIN_AMBIGUOUS
        assert plan.requirements == () and plan.missing == ()
        d = _decision(plan, "cv2")
        assert d["action"] == "undecided" and len(d["observed"]) == 2


# ===========================================================================
# DEP-07 / AC-09：editable / 本地 wheel / VCS 的观察不被扁平化成 PyPI name==version
# ===========================================================================
@pytest.mark.parametrize(
    "direct,provenance",
    [(_EDITABLE, "editable"), (_LOCAL_WHEEL, "local_archive"), (_VCS, "vcs")],
    ids=["editable", "local-wheel", "vcs"],
)
class TestNonReproducibleObservation:
    def test_a_curated_import_served_by_a_local_fork_is_never_installed_as_name_equals_version(
        self, tmp_path, direct, provenance
    ):
        proj = _project(tmp_path, "import yaml\nimport tabulate\n")
        running = _running(tmp_path, {"my-yaml-fork": ("0.1.7", "yaml", direct)})
        plan = _plan(proj, running)
        assert plan.requirements == ("pyyaml", "tabulate") or set(plan.requirements) == {
            "pyyaml",
            "tabulate",
        }
        joined = " ".join(plan.requirements)
        assert "0.1.7" not in joined and "my-yaml-fork" not in joined  # fork 的版本 / 名字不进需求
        d = _decision(plan, "yaml")
        (obs,) = d["observed"]
        assert obs["reproducible"] is False and obs["distribution"] == "my-yaml-fork"
        assert obs["provenance"] != "index"
        assert d["replaces_observed"] is True  # 新一代装 PyPI 的 pyyaml，不是用户的那个——披露出来

    def test_an_unknown_import_served_by_a_local_distribution_stays_unresolved(
        self, tmp_path, direct, provenance
    ):
        proj = _project(tmp_path, "import mylab\n")
        running = _running(tmp_path, {"mylab-dist": ("1.2.3", "mylab", direct)})
        plan = _plan(proj, running)
        assert plan.requirements == () and plan.missing == ()
        assert plan.unknown == ("mylab",)
        assert "mylab" not in " ".join(plan.requirements) and "1.2.3" not in " ".join(
            plan.requirements
        )
        (origin,) = plan.origins
        assert (
            origin["reason"] in depplan.ORIGIN_REASONS
            and origin["reason"] != depplan.ORIGIN_UNRESOLVED
        )
        assert "suggested_distribution" not in origin  # 不对本地来源预填 PyPI 名


class TestLocalSourcesAreNotInstalled:
    def test_a_project_local_helper_that_shares_a_curated_name_is_not_installed(self, tmp_path):
        proj = _project(tmp_path, "import yaml\n", **{"yaml.py": "X = 1\n"})
        plan = _plan(proj, _facts({}))
        assert plan.requirements == () and plan.missing == () and plan.origins == ()

    def test_a_namespace_package_directory_is_not_installed(self, tmp_path):
        proj = _project(tmp_path, "import corp.tools\n", **{"corp/tools/__init__.py": "X = 1\n"})
        plan = _plan(proj, _facts({}))
        assert plan.requirements == () and plan.missing == () and plan.origins == ()


class TestRenamedDeclaredRootsWithObservationPresent:
    def test_a_renamed_declared_root_is_installed_whatever_unrelated_thing_is_observed(
        self, tmp_path
    ):
        proj = _project(tmp_path, "import auditclient\n", requirements="audit-driver==1.0\n")
        running = _running(tmp_path, {"other-dist": ("1.0", "otherthing", None)})
        plan = _plan(proj, running)
        assert plan.requirements == ("audit-driver==1.0",)
        assert plan.status == depplan.STATUS_READY


# ===========================================================================
# 用户明确选择：当前意图，不被旧观察覆盖
# ===========================================================================
class TestExplicitSelection:
    def test_a_user_specified_name_is_installed_and_disclosed_as_such(self, tmp_path):
        proj = _project(tmp_path, "import mystery_lib\n")
        plan = _plan(proj, _facts({}), user_specified={"mystery_lib": "mystery-dist>=2"})
        assert plan.requirements == ("mystery-dist>=2",)
        d = _decision(plan, "mystery_lib")
        assert d["basis"] == depresolve.SOURCE_USER_SPECIFIED and d["observed"] == []

    def test_a_user_specified_name_cannot_displace_something_the_environment_already_provides(
        self, tmp_path
    ):
        """反面：环境里已有别的发行包（这里是 editable）提供这个 import——用户指定不能把它悄悄换成 PyPI 的同名包。"""
        proj = _project(tmp_path, "import mylab\n")
        running = _running(tmp_path, {"mylab-dist": ("1.0", "mylab", _EDITABLE)})
        with pytest.raises(depplan.UserSpecifiedRejected):
            _plan(proj, running, user_specified={"mylab": "mylab"})


# ===========================================================================
# 披露的形状
# ===========================================================================
class TestDisclosureShape:
    def test_nothing_to_say_means_no_key(self, tmp_path):
        proj = _project(tmp_path, "import tabulate\n")
        plan = _plan(proj, _facts({}))
        assert plan.provider_decisions == () and "provider_decisions" not in plan.to_payload()

    def test_the_payload_carries_no_paths_or_file_contents(self, tmp_path):
        proj = _project(tmp_path, "import cv2\n")
        plan = _plan(
            proj, _running(tmp_path, {"opencv-python-headless": ("4.10.0.84", "cv2", None)})
        )
        text = json.dumps(plan.to_payload()["provider_decisions"])
        assert str(tmp_path) not in text and "RECORD" not in text
        assert {
            "import_name",
            "distribution",
            "basis",
            "action",
            "observed",
            "replaces_observed",
        } == set(plan.to_payload()["provider_decisions"][0])
        assert plan.to_payload()["provider_decisions"][0]["basis"] in depresolve.INSTALLABLE_SOURCES
        assert plan.to_payload()["provider_decisions"][0]["action"] in depplan.DECISION_ACTIONS
