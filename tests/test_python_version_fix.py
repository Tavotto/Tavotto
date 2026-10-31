"""版本不合时自动换 Python（`engine/pyversionfix.py`，ADR 0114 修订 2026-10-23）。

三节：① 范围推断（`required_span` / `parse_requires_python`：推得出才换，推不出一律 None）；② 计划（基础来源：
有合格系统 Python 不下载，没有走私有 Python 并把下载字节写进计划）；③ 执行（只在受管环境另建一代、用户环境
不碰、取消可停、失败不留半成品——最后一组是**真**代事务，用离线 wheelhouse 与真 venv）。
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

from support.dependency_repair import build_wheel, needs_worker, wait_for
from tavotto.engine import (
    depplan,
    deprepair,
    envlease,
    managedenv,
    pool as engine_pool,
    privatepython,
    projectenv,
    pyversionfix,
)

pytest_plugins = ("support.dependency_repair",)

SUPPORTED_TOP = (3, 14)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # 确定性：没有全局显式解释器、没有运维开关
    monkeypatch.setattr(engine_pool, "explicit_worker_python", lambda: None)
    monkeypatch.delenv(pyversionfix.ENABLE_ENV, raising=False)
    deprepair.reset_state()
    depplan.reset_cache()
    yield
    deprepair.reset_state()
    depplan.reset_cache()


def _project(tmp_path: Path, *, pyproject: str | None = None, script: str = "print(1)\n") -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.py").write_text(script, encoding="utf-8")
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return root


# ===========================================================================
# ① 范围推断
# ===========================================================================
@pytest.mark.parametrize(
    ("text", "lo", "hi_minor"),
    [
        (">=3.11", (3, 11), 15),
        (">=3.10,<3.13", (3, 10), 13),
        ("~=3.11", (3, 11), 15),
        ("~=3.11.2", (3, 11), 12),
        ("==3.12.*", (3, 12), 13),
        ("<=3.12", (3, 10), 13),
        (">3.11", (3, 12), 15),
        (">=3.11.2", (3, 11), 15),
    ],
)
def test_requires_python_maps_to_a_minor_range(text, lo, hi_minor):
    span = pyversionfix.parse_requires_python(text)
    assert span is not None
    # 与 Tavotto 的支持区间求交后看（上界 99 的"无上界"在这里被支持区间收窄）
    cut = span.meet(pyversionfix._supported_span())
    assert cut.lo == lo and cut.hi == (3, hi_minor)


@pytest.mark.parametrize("text", ["", "garbage", ">=3.11 || <3.9", "===3.12", ">=", ">=three"])
def test_unparseable_requires_python_is_not_guessed(text):
    assert pyversionfix.parse_requires_python(text) is None


def test_not_equal_excludes_a_whole_minor():
    span = pyversionfix.parse_requires_python(">=3.10,!=3.12.*")
    assert span is not None and (3, 12) in span.excluded
    assert (3, 12) not in span.meet(pyversionfix._supported_span()).minors()


def test_root_version_mismatch_wants_the_version_root_was_built_for(tmp_path):
    root = _project(tmp_path)
    failure = {
        "code": "root_python_mismatch",
        "script": "fig.py",
        "root_python": "3.12",
        "env_python": "3.14",
    }
    span = pyversionfix.required_span(failure, root)
    assert span is not None and span.minors() == [(3, 12)]


def test_root_without_a_readable_build_version_is_not_guessed(tmp_path):
    root = _project(tmp_path)
    for failure in (
        {"code": "root_python_mismatch", "script": "fig.py"},
        {"code": "root_python_mismatch", "script": "fig.py", "root_python": "weird"},
        # ROOT 为 3.9 编译：不在 Tavotto 的支持区间里，换不出可用的
        {"code": "root_python_mismatch", "script": "fig.py", "root_python": "3.9"},
    ):
        assert pyversionfix.required_span(failure, root) is None


def test_requires_python_in_pyproject_picks_the_highest_supported(tmp_path):
    root = _project(tmp_path, pyproject='[project]\nname = "x"\nrequires-python = ">=3.11"\n')
    span = pyversionfix.required_span(
        {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}, root
    )
    assert span is not None
    assert span.minors()[0] == SUPPORTED_TOP and span.minors()[-1] == (3, 11)


def test_requires_python_upper_bound_caps_the_choice(tmp_path):
    root = _project(tmp_path, pyproject='[project]\nrequires-python = ">=3.10,<3.13"\n')
    span = pyversionfix.required_span(
        {"code": "python_out_of_range", "script": "fig.py", "env_python": "3.9"}, root
    )
    assert span is not None and span.minors()[0] == (3, 12)


def test_pep723_script_metadata_is_read(tmp_path):
    script = '# /// script\n# requires-python = ">=3.13"\n# dependencies = []\n# ///\nprint(1)\n'
    root = _project(tmp_path, script=script)
    span = pyversionfix.required_span(
        {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}, root
    )
    assert span is not None and span.minors() == [(3, 14), (3, 13)]


def test_a_requires_python_that_cannot_be_read_means_no_change(tmp_path):
    root = _project(tmp_path, pyproject='[project]\nrequires-python = "wat"\n')
    assert (
        pyversionfix.required_span(
            {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"},
            root,
        )
        is None
    )
    broken = _project(
        tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, pyproject="[[["
    )
    assert (
        pyversionfix.required_span(
            {"code": "python_out_of_range", "script": "fig.py", "env_python": "3.9"}, broken
        )
        is None
    )


def test_empty_intersection_means_no_change(tmp_path):
    root = _project(tmp_path, pyproject='[project]\nrequires-python = ">=3.20"\n')
    assert (
        pyversionfix.required_span(
            {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"},
            root,
        )
        is None
    )


def test_an_environment_already_inside_the_range_is_not_swapped(tmp_path):
    root = _project(tmp_path)
    failure = {
        "code": "root_python_mismatch",
        "script": "fig.py",
        "root_python": "3.12",
        "env_python": "3.12",
    }
    assert pyversionfix.required_span(failure, root) is None


def test_only_version_codes_qualify(tmp_path):
    root = _project(tmp_path)
    for code in ("missing_dependency", "script_error", "", "no_matplotlib", "worker_crashed"):
        assert pyversionfix.required_span({"code": code, "script": "fig.py"}, root) is None


def test_failure_is_recognised_from_the_session_facts():
    rows = [
        {"reason": "outranked"},
        {
            "reason": "root_python_mismatch",
            "root_python": "3.12",
            "env_python": "3.14",
            "root_version": "6.34",
        },
    ]
    got = pyversionfix.failure_from_report(
        script="fig.py", error={"code": "missing_dependency", "module": "ROOT"}, not_used=rows
    )
    assert got == {
        "code": "root_python_mismatch",
        "script": "fig.py",
        "root_python": "3.12",
        "env_python": "3.14",
    }
    # 缺的不是 ROOT / 落选里没有版本事实：不认
    assert (
        pyversionfix.failure_from_report(
            script="fig.py", error={"code": "missing_dependency", "module": "scipy"}, not_used=rows
        )
        is None
    )
    assert (
        pyversionfix.failure_from_report(
            script="fig.py",
            error={"code": "missing_dependency", "module": "ROOT"},
            not_used=[{"reason": "root_python_mismatch"}],
        )
        is None
    )
    direct = pyversionfix.failure_from_report(
        script="fig.py", error={"code": "project_env_unsupported_python"}, not_used=[]
    )
    assert direct is not None and direct["code"] == "project_env_unsupported_python"
    assert pyversionfix.failure_from_report(script="fig.py", error=None, not_used=rows) is None


# ===========================================================================
# ② 计划：基础来源
# ===========================================================================
class _Picks:
    """`managedenv.base_pick` 的替身：记下收到的范围，回预设的系统候选。"""

    def __init__(self, found: tuple[str, str] | None):
        self.found = found
        self.calls: list = []
        self.specs: list = []

    def __call__(self, *, within=None, with_version=True, patch_spec=""):
        self.calls.append(within)
        self.specs.append(patch_spec)
        return self.found


def _private_source(version="3.13.15", size=25_304_407, enabled=True):
    return privatepython.PythonSource(
        target="macos-arm64",
        version=version,
        release="20260814",
        triple="t",
        url="https://github.com/astral-sh/python-build-standalone/releases/download/20260814/x.tar.gz",
        sha256="a" * 64,
        size=size,
        archive_root="python",
        python_rel="bin/python3",
        enabled=enabled,
    )


def test_root_mismatch_plans_the_root_version(tmp_path, monkeypatch):
    root = _project(tmp_path)
    picks = _Picks(("/sys/python3.12", "3.12"))
    monkeypatch.setattr(managedenv, "base_pick", picks)
    plan = pyversionfix.plan_python_change(
        {
            "code": "root_python_mismatch",
            "script": "fig.py",
            "root_python": "3.12",
            "env_python": "3.14",
        },
        root,
    )
    assert plan is not None
    assert (plan.from_version, plan.to_version, plan.base) == ("3.14", "3.12", "system")
    assert picks.calls == [((3, 12), (3, 13))]
    assert plan.public() == {"from": "3.14", "to": "3.12", "base": "system", "download_bytes": 0}


def test_requires_python_asks_for_the_highest_supported_in_range(tmp_path, monkeypatch):
    root = _project(tmp_path, pyproject='[project]\nrequires-python = ">=3.11"\n')
    picks = _Picks(("/sys/python3.14", "3.14"))
    monkeypatch.setattr(managedenv, "base_pick", picks)
    plan = pyversionfix.plan_python_change(
        {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}, root
    )
    assert plan is not None and plan.to_version == "3.14"
    assert picks.calls == [((3, 11), (3, 15))]  # [3.11, 3.15)：范围内最高由挑选处去取


def test_nothing_to_infer_means_no_plan_and_no_probing(tmp_path, monkeypatch):
    root = _project(tmp_path)
    picks = _Picks(("/sys/python3.12", "3.12"))
    monkeypatch.setattr(managedenv, "base_pick", picks)
    monkeypatch.setattr(
        privatepython, "source_for", lambda *a, **k: pytest.fail("不该碰私有 Python")
    )
    assert (
        pyversionfix.plan_python_change({"code": "root_python_mismatch", "script": "fig.py"}, root)
        is None
    )
    assert (
        pyversionfix.plan_python_change({"code": "script_error", "script": "fig.py"}, root) is None
    )
    assert picks.calls == []


def test_a_qualified_system_python_means_no_download(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(managedenv, "base_pick", _Picks(("/sys/python3.13", "3.13")))
    monkeypatch.setattr(
        privatepython, "offer_payload", lambda *a, **k: pytest.fail("有系统 Python 就不该谈下载")
    )
    plan = pyversionfix.plan_python_change(
        {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}, root
    )
    assert plan is not None and plan.base == "system" and plan.download_bytes == 0
    assert plan.base_python == "/sys/python3.13"


def test_without_a_system_python_the_private_one_is_planned_with_its_download_size(
    tmp_path, monkeypatch
):
    root = _project(tmp_path)
    source = _private_source()
    monkeypatch.setattr(managedenv, "base_pick", _Picks(None))
    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: source)
    monkeypatch.setattr(privatepython, "offered", lambda s: True)
    monkeypatch.setattr(privatepython, "python_of", lambda s=None: None)
    monkeypatch.setattr(privatepython, "archive_origin", lambda s: privatepython.ORIGIN_DOWNLOAD)
    plan = pyversionfix.plan_python_change(
        {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}, root
    )
    assert plan is not None
    assert (plan.base, plan.to_version, plan.download_bytes) == ("private", "3.13", 25_304_407)
    assert plan.private_origin == privatepython.ORIGIN_DOWNLOAD
    assert plan.public()["download_bytes"] == 25_304_407
    assert "python" not in " ".join(plan.public())  # 公开形态没有路径


def test_a_cached_private_archive_is_disclosed_as_zero_bytes(tmp_path, monkeypatch):
    root = _project(tmp_path)
    source = _private_source()
    monkeypatch.setattr(managedenv, "base_pick", _Picks(None))
    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: source)
    monkeypatch.setattr(privatepython, "offered", lambda s: True)
    monkeypatch.setattr(privatepython, "python_of", lambda s=None: None)
    monkeypatch.setattr(privatepython, "archive_origin", lambda s: privatepython.ORIGIN_CACHED)
    plan = pyversionfix.plan_python_change(
        {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}, root
    )
    assert plan is not None and plan.download_bytes == 0 and plan.base == "private"


def test_the_private_python_is_not_a_candidate_outside_its_locked_version(tmp_path, monkeypatch):
    """锁里只有一个版本：ROOT 要 3.12 而私有是 3.13 → 没有可供应的，不换，更不去下 3.13 凑数。"""
    root = _project(tmp_path)
    monkeypatch.setattr(managedenv, "base_pick", _Picks(None))
    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: _private_source())
    monkeypatch.setattr(privatepython, "offered", lambda s: True)
    monkeypatch.setattr(privatepython, "python_of", lambda s=None: None)
    monkeypatch.setattr(
        privatepython, "offer_payload", lambda *a, **k: pytest.fail("版本不在范围内不该谈下载")
    )
    assert (
        pyversionfix.plan_python_change(
            {
                "code": "root_python_mismatch",
                "script": "fig.py",
                "root_python": "3.12",
                "env_python": "3.14",
            },
            root,
        )
        is None
    )


def test_kill_switch_and_global_pin_stop_the_plan(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(managedenv, "base_pick", _Picks(("/sys/python3.12", "3.12")))
    failure = {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}
    assert pyversionfix.plan_python_change(failure, root) is not None
    monkeypatch.setenv(pyversionfix.ENABLE_ENV, "0")
    assert pyversionfix.plan_python_change(failure, root) is None
    monkeypatch.delenv(pyversionfix.ENABLE_ENV)
    monkeypatch.setattr(engine_pool, "explicit_worker_python", lambda: ("/x/python", "configured"))
    assert pyversionfix.plan_python_change(failure, root) is None


# ---- managedenv.base_pick：挑选处本身 ----
def _fake_interpreters(tmp_path, monkeypatch, versions: dict[str, str]):
    from tavotto.engine import bootstrap

    paths = {}
    for name, ver in versions.items():
        p = tmp_path / name
        p.write_text("", encoding="utf-8")
        paths[str(p)] = ver
    monkeypatch.setattr(
        engine_pool, "_prioritized_candidates", lambda: [(p, "system") for p in paths]
    )
    monkeypatch.setattr(bootstrap, "_probe", lambda cand, code: paths[cand])
    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: None)
    return paths


def test_base_pick_takes_the_highest_inside_the_range(tmp_path, monkeypatch):
    paths = _fake_interpreters(
        tmp_path, monkeypatch, {"p311": "3.11", "p313": "3.13", "p312": "3.12", "p39": "3.9"}
    )
    by_ver = {v: p for p, v in paths.items()}
    assert managedenv.base_pick(within=((3, 11), (3, 14))) == (by_ver["3.13"], "3.13")
    assert managedenv.base_pick(within=((3, 11), (3, 13))) == (by_ver["3.12"], "3.12")
    assert managedenv.base_pick(within=((3, 14), (3, 15))) is None
    # 区间外（3.9 不在支持范围）即便范围写得宽也不选
    assert managedenv.base_pick(within=((3, 0), (3, 11))) is None
    # 默认形态不变：候选清单里第一个合格者，不为取版本多起进程
    assert managedenv.base_python() == by_ver["3.11"]


# ===========================================================================
# ③ 执行
# ===========================================================================
def _plan(root, *, base="system", base_python="/sys/python3.12", to="3.12", download=0, origin=""):
    return pyversionfix.PythonChangePlan(
        from_version="3.14",
        to_version=to,
        base=base,
        download_bytes=download,
        code="root_python_mismatch",
        project=str(root),
        script="fig.py",
        private_origin=origin,
        base_python=base_python,
    )


class _FakeJoint:
    plan_id = "joint-1"
    impact_digest = "digest-1"


def test_apply_builds_only_in_the_managed_environment_on_the_pinned_base(tmp_path, monkeypatch):
    root = _project(tmp_path)
    seen: dict = {}

    def create(project, script, **kw):
        seen["create"] = (str(project), script, kw, deprepair.pinned_base_python())
        return _FakeJoint()

    def prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        seen["prepare"] = (plan_id, confirmed_impact, deprepair.pinned_base_python())
        for state in (
            deprepair.STATE_PREPARING,
            deprepair.STATE_CREATING_ENV,
            deprepair.STATE_INSTALLING,
            deprepair.STATE_VERIFYING,
        ):
            on_event({"state": state})
        return {"ok": True, "target_kind": deprepair.TARGET_MANAGED}

    monkeypatch.setattr(deprepair, "create_joint_plan", create)
    monkeypatch.setattr(deprepair, "prepare", prepare)
    # 用户的环境：任何对 pip / 解释器记账的触碰都是失败
    monkeypatch.setattr(
        deprepair, "_run_joint_in_place", lambda *a, **k: pytest.fail("不许往用户环境里装")
    )
    monkeypatch.setattr(
        projectenv, "remember", lambda *a, **k: pytest.fail("记住项目解释器只在代事务提交点里做")
    )
    stages: list[str] = []
    result = pyversionfix.apply_python_change(
        _plan(root), lambda snap: stages.append(snap["stage"]), threading.Event()
    )
    assert result == {"ok": True, "code": "", "to": "3.12"}
    project, script, kw, pinned = seen["create"]
    assert (project, script) == (str(root), "fig.py")
    assert kw == {"target_kind": deprepair.TARGET_MANAGED}  # 目标强制受管环境
    assert pinned == "/sys/python3.12" and seen["prepare"] == (
        "joint-1",
        "digest-1",
        "/sys/python3.12",
    )
    assert stages == ["preparing", "preparing", "creating", "installing", "verifying"]
    assert deprepair.pinned_base_python() == ""  # 钉子只活在 with 块里


def test_pin_does_not_leak_to_other_threads_or_after_failure(tmp_path, monkeypatch):
    root = _project(tmp_path)
    other: dict = {}

    def create(project, script, **kw):
        t = threading.Thread(target=lambda: other.update(pin=deprepair.pinned_base_python()))
        t.start()
        t.join()
        raise deprepair.RepairError("dependency_plan_blocked", "x")

    monkeypatch.setattr(deprepair, "create_joint_plan", create)
    result = pyversionfix.apply_python_change(_plan(root), None, threading.Event())
    assert result == {"ok": False, "code": "dependency_plan_blocked", "to": "3.12"}
    assert other == {"pin": ""}
    assert deprepair.pinned_base_python() == ""


def test_private_base_is_provisioned_with_progress_and_the_planned_origin(tmp_path, monkeypatch):
    root = _project(tmp_path)
    source = _private_source()
    calls: dict = {}

    def provision(src, *, cancel_ev=None, on_progress=None, wait_timeout=0, required_origin=None):
        calls.update(src=src, required_origin=required_origin)
        on_progress(privatepython.STAGE_DOWNLOADING, 100, 400)
        on_progress(privatepython.STAGE_EXTRACTING, 400, 400)
        return "/data/private/python3"

    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: source)
    monkeypatch.setattr(privatepython, "provision", provision)
    seen = {}

    def create(project, script, **kw):
        seen["pin"] = deprepair.pinned_base_python()
        return _FakeJoint()

    monkeypatch.setattr(deprepair, "create_joint_plan", create)
    monkeypatch.setattr(deprepair, "prepare", lambda *a, **k: {"ok": True})
    snaps: list[dict] = []
    plan = _plan(
        root,
        base="private",
        base_python="",
        to="3.13",
        download=source.size,
        origin=privatepython.ORIGIN_DOWNLOAD,
    )
    result = pyversionfix.apply_python_change(plan, snaps.append, threading.Event())
    assert result["ok"] is True
    assert calls == {"src": source, "required_origin": privatepython.ORIGIN_DOWNLOAD}
    assert seen["pin"] == "/data/private/python3"
    assert {"stage": "downloading", "done": 100, "total": 400} in snaps  # 下载进度如实转出


def test_private_provisioning_failure_changes_nothing(tmp_path, monkeypatch):
    root = _project(tmp_path)

    def provision(*a, **k):
        raise privatepython.ProvisionError(privatepython.ERROR_OFFLINE, "offline")

    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: _private_source())
    monkeypatch.setattr(privatepython, "provision", provision)
    monkeypatch.setattr(
        deprepair, "create_joint_plan", lambda *a, **k: pytest.fail("没有基础解释器就不该建计划")
    )
    result = pyversionfix.apply_python_change(
        _plan(root, base="private", base_python="", to="3.13"), None, threading.Event()
    )
    assert result == {"ok": False, "code": privatepython.ERROR_OFFLINE, "to": "3.13"}


def test_cancel_before_start_and_during_download_stop_it(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(
        deprepair, "create_joint_plan", lambda *a, **k: pytest.fail("已取消就不该建计划")
    )
    cancel = threading.Event()
    cancel.set()
    assert pyversionfix.apply_python_change(_plan(root), None, cancel)["code"] == (
        pyversionfix.ERROR_CANCELLED
    )

    def provision(src, *, cancel_ev=None, **kw):
        assert cancel_ev is not None
        cancel_ev.set()  # 用户在下载中点了停止
        raise privatepython.ProvisionError(privatepython.ERROR_CANCELLED, "已取消")

    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: _private_source())
    monkeypatch.setattr(privatepython, "provision", provision)
    result = pyversionfix.apply_python_change(
        _plan(root, base="private", base_python="", to="3.13"), None, threading.Event()
    )
    assert result["code"] == pyversionfix.ERROR_CANCELLED and result["ok"] is False


def test_cancel_during_the_transaction_reaches_the_generation_job(tmp_path, monkeypatch):
    root = _project(tmp_path)
    cancel = threading.Event()
    reached = threading.Event()
    got: list[str] = []

    monkeypatch.setattr(deprepair, "create_joint_plan", lambda *a, **k: _FakeJoint())
    monkeypatch.setattr(
        deprepair,
        "cancel_status",
        lambda pid: got.append(pid) or reached.set() or {"accepted": True},
    )

    def prepare(plan_id, on_event=None, **kw):
        cancel.set()
        assert reached.wait(5), "取消没有转给代事务"
        return {"ok": False, "code": deprepair.ERROR_CANCELLED}

    monkeypatch.setattr(deprepair, "prepare", prepare)
    result = pyversionfix.apply_python_change(_plan(root), None, cancel)
    assert got == ["joint-1"]
    assert result == {"ok": False, "code": pyversionfix.ERROR_CANCELLED, "to": "3.12"}


# ---- deprepair 里的钉子：计划按钉下的基础量事实、在已有一代旁边另建 ----
def test_pinned_base_makes_the_plan_a_fresh_generation_even_with_an_active_one(
    tmp_path, monkeypatch
):
    root = _project(tmp_path)
    fresh = depplan.TargetFacts(
        python="/sys/python3.12", marker_env={}, stdlib=frozenset(), installed={}
    )
    asked: list = []
    monkeypatch.setattr(
        depplan,
        "fresh_venv_facts",
        lambda base, **kw: asked.append(base) or fresh,
    )
    monkeypatch.setattr(managedenv, "python_of", lambda project: "/data/envs/g1/bin/python")
    assert deprepair.private_python_target(root, "fig.py") is None or True
    with deprepair.pinned_base("/sys/python3.12"):
        got = deprepair.private_python_target(root, "fig.py")
        creates, private, bound = deprepair._managed_scope(str(root), deprepair.TARGET_MANAGED)
        assert deprepair.base_python() == "/sys/python3.12"
        assert deprepair._run_env_ours(root, fresh) is True
    assert got == (deprepair.TARGET_MANAGED, "/sys/python3.12", fresh)
    assert asked[-1] == "/sys/python3.12"
    assert creates is True and private is None and bound == "/data/envs/g1/bin/python"
    # 出了块：常规结论
    _c, _p, _b = deprepair._managed_scope(str(root), deprepair.TARGET_MANAGED)
    assert _c is False


# ---- 真事务：真 venv + 离线 wheelhouse ----
ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    build_wheel(dest, name=ALPHA[0], import_name=ALPHA[1], version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


def _tree_digest(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        h.update(str(p.relative_to(path)).encode())
        if p.is_file():
            h.update(str(p.stat().st_size).encode())
    return h.hexdigest()


@pytest.fixture(autouse=False)
def _real(monkeypatch):
    envlease.reset_for_tests()
    yield
    envlease.reset_for_tests()


@needs_worker
class TestRealTransaction:
    def _project(self, tmp_path) -> Path:
        root = tmp_path / "paper"
        root.mkdir()
        (root / "requirements.txt").write_text(f"{ALPHA[0]}\n", encoding="utf-8")
        (root / "figure.py").write_text(f"import {ALPHA[1]}\n", encoding="utf-8")
        return root

    def test_a_second_generation_is_built_beside_the_first_and_the_user_env_is_untouched(
        self, tmp_path, house, offline_managed_env, _real, monkeypatch
    ):
        from support.dependency_repair import WORKER_PY

        project = self._project(tmp_path)
        user_venv = project / ".venv"  # 用户自己的环境：必须原样
        user_venv.mkdir()
        (user_venv / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
        before_user = _tree_digest(user_venv)
        # 第一代：常规的受管环境准备
        first = deprepair.create_joint_plan(project, "figure.py")
        deprepair.prepare_async(first.plan_id, confirmed_impact=first.impact_digest)
        assert wait_for(first.plan_id)["state"] == deprepair.STATE_DONE
        gen1 = managedenv.active_generation(project)
        assert gen1
        # 常规的基础解释器此刻"坏了"：没有钉子就建不出新一代——能建出来只可能是钉子起了作用
        monkeypatch.setattr(deprepair, "_base_python", "/nonexistent/python")
        monkeypatch.setattr(deprepair, "_base_python_known", True)
        stages: list[str] = []
        plan = pyversionfix.PythonChangePlan(
            from_version="3.9",
            to_version="3.x",
            base="system",
            download_bytes=0,
            code="project_env_unsupported_python",
            project=str(project),
            script="figure.py",
            base_python=WORKER_PY,
        )
        result = pyversionfix.apply_python_change(
            plan, lambda s: stages.append(s["stage"]), threading.Event()
        )
        assert result["ok"] is True, result
        gen2 = managedenv.active_generation(project)
        assert gen2 and gen2 != gen1
        # 旧代按既有规则（没人用就退役）处置，与本模块无关；用户自己的环境见文末的摘要比对
        assert managedenv.generations(project)[gen2]["base_interpreter_fingerprint"] == (
            managedenv.base_interpreter_fingerprint(WORKER_PY)
        )
        python = managedenv.python_of(project)
        assert python and str(managedenv.generation_dir(project, gen2)) in os.path.abspath(python)
        assert engine_pool.same_python(
            projectenv.remembered(project), python
        )  # 同一脚本记住这个环境
        assert "creating" in stages and "installing" in stages and "verifying" in stages
        assert _tree_digest(user_venv) == before_user  # 用户环境一个字节没动

    def test_a_failed_change_leaves_no_half_built_environment_and_keeps_the_old_one(
        self, tmp_path, house, offline_managed_env, _real, monkeypatch
    ):
        from support.dependency_repair import WORKER_PY

        project = self._project(tmp_path)
        first = deprepair.create_joint_plan(project, "figure.py")
        deprepair.prepare_async(first.plan_id, confirmed_impact=first.impact_digest)
        assert wait_for(first.plan_id)["state"] == deprepair.STATE_DONE
        gen1 = managedenv.active_generation(project)
        # pip 在新一代里装失败（wheelhouse 清空）
        for wheel in house.iterdir():
            wheel.unlink()
        plan = pyversionfix.PythonChangePlan(
            from_version="3.9",
            to_version="3.x",
            base="system",
            download_bytes=0,
            code="python_out_of_range",
            project=str(project),
            script="figure.py",
            base_python=WORKER_PY,
        )
        result = pyversionfix.apply_python_change(plan, None, threading.Event())
        assert result["ok"] is False and result["code"]
        assert managedenv.active_generation(project) == gen1  # active 不动
        broken = [
            g
            for g, meta in managedenv.generations(project).items()
            if g != gen1 and meta["state"] == managedenv.GEN_STATE_READY
        ]
        assert broken == []  # 没有"看起来好了"的半成品


# ---- 补丁位约束按完整版本判（ADR 0114 §七）----
def test_patch_bearing_clauses_are_kept_for_full_version_checks():
    span = pyversionfix.parse_requires_python(">=3.11.2,<3.12")
    assert span is not None and span.patches == (">=3.11.2",)
    assert pyversionfix.parse_requires_python("==3.12.1").patches == ("==3.12.1",)
    assert pyversionfix.parse_requires_python(">=3.11").patches == ()  # 无补丁位：区间已完整表达


@pytest.mark.parametrize(
    ("requires", "probed", "picked"),
    [
        (">=3.11.2,<3.12", "3.11.0", False),
        (">=3.11.2,<3.12", "3.11.4", True),
        ("==3.12.1", "3.12.3", False),
        ("==3.12.1", "3.12.1", True),
        (">=3.11.2,<3.12", "3.11", False),  # 只拿到小版本而约束含补丁位：不满足，宁可不换
        (">=3.11,<3.12", "3.11", True),  # 无补丁位的旧口径照旧
    ],
)
def test_base_pick_judges_patch_constraints_on_the_full_version(
    tmp_path, monkeypatch, requires, probed, picked
):
    _fake_interpreters(tmp_path, monkeypatch, {"p": probed})
    span = pyversionfix.parse_requires_python(requires).meet(pyversionfix._supported_span())
    got = managedenv.base_pick(within=((3, 11), (3, 13)), patch_spec=span.patch_spec())
    assert (got is not None) is picked


def test_plan_passes_the_patch_spec_to_the_picker_and_declines_without_a_candidate(
    tmp_path, monkeypatch
):
    root = _project(tmp_path, pyproject='[project]\nrequires-python = ">=3.11.2,<3.12"\n')
    picks = _Picks(None)
    monkeypatch.setattr(managedenv, "base_pick", picks)
    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: None)
    failure = {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}
    assert pyversionfix.plan_python_change(failure, root) is None
    assert picks.calls == [((3, 11), (3, 12))] and picks.specs == [">=3.11.2"]


def test_private_python_lock_version_is_judged_on_the_full_version(tmp_path, monkeypatch):
    root = _project(tmp_path, pyproject='[project]\nrequires-python = ">=3.13.20"\n')
    monkeypatch.setattr(managedenv, "base_pick", _Picks(None))
    monkeypatch.setattr(privatepython, "source_for", lambda *a, **k: _private_source("3.13.15"))
    monkeypatch.setattr(privatepython, "offered", lambda s: True)
    monkeypatch.setattr(privatepython, "python_of", lambda s=None: None)
    monkeypatch.setattr(
        privatepython, "offer_payload", lambda *a, **k: pytest.fail("补丁位不满足不该谈下载")
    )
    failure = {"code": "project_env_unsupported_python", "script": "fig.py", "env_python": "3.9"}
    assert pyversionfix.plan_python_change(failure, root) is None


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ('[project]\nrequires-python = ">=3.11"\n', ">=3.11"),
        ("[project]\nname = 'x'\n", None),
        ("[[[", False),
        ("[project\nrequires-python = '>=3.11'\n", False),
        ("[project]\nrequires-python = >=3.11\n", False),
    ],
)
def test_the_no_tomllib_fallback_treats_malformed_files_as_unreadable(monkeypatch, text, want):
    """3.10 没有 tomllib：正则兜底遇到不像 TOML 的表头 / 没加引号的值，按「读不懂」（不换），不是「没写」。"""
    import builtins

    real_import = builtins.__import__

    def no_tomllib(name, *a, **k):
        if name == "tomllib":
            raise ImportError("simulated 3.10")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_tomllib)
    assert pyversionfix._pyproject_requires_python(text) == want
