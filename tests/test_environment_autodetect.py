"""环境自动检测（T13a，ADR 0114 §六：用户 2026-10-06 裁决「环境不让用户选」）。

默认模式 `detect`：用户点了准备 / 运行之后，候选环境里**能跑这个脚本**（解释器健康 + 脚本要的 import 全都 import
得到）的直接用，多个能跑时按既有排序挑；都不能跑才出「安装缺少的组件」——一条待办、一个 `prepare_dependencies`
动作，装进 Tavotto 管理的环境。报告里关于环境只有一个可展示的事实（`environment`），不要求用户动作。

判据的主语：

* **哪个时刻**：扫描 / GET / 建议（零执行，同 T02 / T05）vs 准备会话的检查与 `pool.acquire`（用户发起之后）；
* **哪一份决定**：全局锁定、用户选过且仍有效的、明确选回内置的——一个都不碰；失效的（被重建 / 被删）作废后重新
  检测，换到的另一个是新决定：报告里说出来、会话修订加一、旧动作作废、旧计划不跑；
* **装进哪里**：都不能跑时安装目标是 Tavotto 管理的环境，不是用户项目里那个跑不了的 venv。

分层：挑选顺序与边界用替身（不起解释器、确定）；「能跑就直接用」「都不能跑只有一张安装卡」用真 venv / 真 worker /
离线 wheelhouse 各走一遍。T05 的确认模式仍可选（`TAVOTTO_ENV_ADOPTION=confirm`），它的用例在
`test_environment_adoption.py` / `test_environment_session.py` 里原样钉着。
"""

# ruff: noqa: F811 — 夹具（house / opened / offline_managed_env）按 pytest 的参数名注入
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import envworld
from support.dependency_repair import WORKER_PY, needs_worker
from support.envworld import World, rebuild_venv
from tavotto.engine import (
    config,
    depplan,
    deprepair,
    envadvice,
    envlease,
    managedenv,
    pool as engine_pool,
    preparation,
    prepsession,
    projectenv,
    taskdiag,
    userenvs,
)
from test_preparation_session_dependencies import (  # noqa: F401 — 同一套夹具与工具，不写第二份
    ALPHA,
    SESSIONS,
    _act,
    _create,
    _get,
    _kinds,
    _wait,
    house,
    opened,
)

_REAL_JOINT_PLAN_FOR = deprepair.joint_plan_for

pytest_plugins = ("support.dependency_repair",)

posix_only = pytest.mark.skipif(not envworld.POSIX, reason="哨兵是 POSIX shell 脚本")


@pytest.fixture(autouse=True)
def _detect(clean_state, monkeypatch):
    """默认模式（不设 `TAVOTTO_ENV_ADOPTION`）+ 打开用户环境发现（测试进程默认关，见 conftest）。机器上真实的
    Conda / pyenv / 登录 shell / 系统 Python 一律遮掉：用例结果不随这台机器装了什么而变。"""
    monkeypatch.delenv("TAVOTTO_ENV_ADOPTION", raising=False)
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.delenv("TAVOTTO_USER_ENV_DISCOVERY", raising=False)
    monkeypatch.setattr(userenvs, "_conda_prefixes", lambda *a, **k: [])
    monkeypatch.setattr(userenvs, "_pyenv_pythons", lambda *a, **k: [])
    monkeypatch.setattr(userenvs, "login_shell_pythons", lambda: [])
    monkeypatch.setattr(userenvs, "cached_login_shell_pythons", lambda: [])
    monkeypatch.setattr(engine_pool, "system_python_candidates", lambda: [])
    userenvs.reset_cache()
    envadvice.reset_cache()
    envlease.reset_for_tests()
    depplan.reset_cache()
    taskdiag.reset_for_tests()
    prepsession.SESSIONS.reset_for_tests()
    yield
    prepsession.SESSIONS.reset_for_tests()
    engine_pool.shutdown_all(wait=True)
    userenvs.reset_cache()
    envadvice.reset_cache()
    envlease.reset_for_tests()


# ===========================================================================
# 模式：默认检测；确认模式可选（环境变量 / 设置）；旧名 auto = 旧三处静默采用
# ===========================================================================
def test_the_default_is_detection_and_confirmation_stays_available(monkeypatch):
    assert projectenv.adoption_mode() == projectenv.ADOPTION_DETECT
    assert projectenv.silent_adoption_enabled() and not projectenv.legacy_adoption_enabled()

    # 设置里打开确认模式（给想自己挑的人）
    cfg = config.load()
    cfg["worker"] = {**cfg.get("worker", {}), "environment_adoption": "confirm"}
    config.save(cfg)
    try:
        assert projectenv.adoption_mode() == projectenv.ADOPTION_CONFIRM
        assert not projectenv.silent_adoption_enabled()
        # 环境变量压过设置
        monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "detect")
        assert projectenv.adoption_mode() == projectenv.ADOPTION_DETECT
    finally:
        cfg = config.load()
        cfg["worker"] = {k: v for k, v in cfg["worker"].items() if k != "environment_adoption"}
        config.save(cfg)

    # ADR 0114 §五写下的旧名 `auto` 仍是旧三处静默采用（兼容一版），不是新的检测
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "auto")
    assert projectenv.adoption_mode() == projectenv.ADOPTION_LEGACY
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "nonsense")
    assert projectenv.adoption_mode() == projectenv.ADOPTION_DETECT
    # 跑前的「换不换解释器」只有一个决定者，`pool.acquire`（编辑 / MCP）与准备计划都经它
    assert deprepair.decide_environment_pinned in engine_pool.ENVIRONMENT_DECIDERS


# ===========================================================================
# 零执行：扫描 / GET / 建议不检测（检测只在用户发起准备 / 运行之后）；也不再问用户选环境
# ===========================================================================
@posix_only
def test_reading_the_environment_runs_nothing_and_never_asks_to_choose(tmp_path, monkeypatch):
    world = World(tmp_path)
    monkeypatch.setenv("SHELL", str(world.shell))
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    resp = client.post("/api/projects/open", json={"path": str(world.root)})
    assert resp.status_code == 200, resp.get_json()
    pj = resp.get_json()["id"]
    try:
        env = client.get("/api/engine/environment", query_string={"pj": pj})
        assert env.status_code == 200
        scan = client.post("/api/project/scan", json={}, query_string={"pj": pj})
        assert scan.status_code in (200, 202), scan.get_json()
        rec = envadvice.recommend(world.root, "plot.py")

        assert world.fired() == []  # 项目 venv、登录 shell 一个都没被起
        decision = env.get_json()["project"]["recommendation"]["decision"]
        assert decision["mode"] == "detect" and decision["needs_decision"] is False
        assert rec["decision"]["needs_decision"] is False
        assert projectenv.remembered_record(world.root) is None
    finally:
        m.reset_projects()


# ===========================================================================
# 挑选顺序与边界（替身：不起解释器）
# ===========================================================================
class _Joint:
    def __init__(self, status: str) -> None:
        self.status = status

    def to_payload(self) -> dict:
        return {
            "status": self.status,
            "missing": [{"import_name": "alpha", "distribution": "alpha"}],
            "satisfied": [{"import_name": "numpy", "distribution": "numpy"}],
            "unknown": [],
        }


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", "utf-8")
    return str(path)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """默认链条（「内置」）能不能跑、有哪些候选、哪些候选能跑——全由用例给定；记下被体检的候选。"""
    root = tmp_path / "proj"
    root.mkdir()
    builtin = _touch(tmp_path / "builtin" / "bin" / "python")
    box: dict = {"runs_now": False, "candidates": [], "satisfying": set(), "probed": []}

    def resolve(figures_dir=None, *, script=None, discover=True):
        record = projectenv.remembered_record(root)
        if record and record.get("exists") and record.get("mode") != "default":
            return record["path"], engine_pool.remembered_source(root, record["path"])
        return builtin, engine_pool.SOURCE_BUNDLED

    def joint_plan_for(r, s, **kw):
        current = resolve(r, script=s)[0]
        runs = box["runs_now"] if current == builtin else current in box["satisfying"]
        return _Joint("nothing_needed" if runs else "ready"), "managed", current

    def evaluate(cands, needed, unknown, **kw):
        cands = cands[: userenvs.PROBE_LIMIT]  # 与真 evaluate 同一个上限
        if kw.get("cache_only"):  # 没有缓存结论：未检查，不探测
            return [
                {**c, "ok": None, "satisfies": None, "checked": False, "missing": []} for c in cands
            ]
        if kw.get("use_cache") is not False:  # 采用前的现量复核不算"体检了哪些候选"
            box["probed"].append([c["python"] for c in cands])
        return [
            {
                **c,
                "ok": True,
                "code": "",
                "support": "verified",
                "python_version": "3.12.0",
                "matplotlib_version": "3.9.0",
                "missing": [] if c["python"] in box["satisfying"] else ["alpha"],
                "satisfies": c["python"] in box["satisfying"],
                "checked": True,
            }
            for c in cands
        ]

    monkeypatch.setattr(engine_pool, "resolve_worker_python", resolve)
    monkeypatch.setattr(deprepair, "joint_plan_for", joint_plan_for)
    monkeypatch.setattr(deprepair, "private_python_target", lambda r, s: None)
    monkeypatch.setattr(deprepair, "unknown_imports_missing", lambda plan, py, root="": [])
    monkeypatch.setattr(
        deprepair,
        "user_environment_candidates",
        lambda r, s, exclude="": [c for c in box["candidates"] if c["python"] != exclude],
    )
    monkeypatch.setattr(userenvs, "evaluate", evaluate)

    def cand(name: str, source: str, label: str = "") -> str:
        python = _touch(tmp_path / name / "bin" / "python")
        box["candidates"].append({"python": python, "source": source, "label": label})
        return python

    box.update(root=root, builtin=builtin, cand=cand)
    return box


def test_when_the_builtin_cannot_run_the_best_runnable_one_is_used_without_asking(rig):
    conda = rig["cand"]("conda-env", userenvs.SOURCE_CONDA, "other")
    shell = rig["cand"]("shell-py", userenvs.SOURCE_LOGIN_SHELL)
    rig["cand"]("broken", userenvs.SOURCE_PYENV)
    rig["satisfying"] |= {conda, shell}

    entry = deprepair.decide_environment(rig["root"], "fig.py")

    # 既有排序：登录 shell 先于 Conda（ADR 0079 §三）
    assert entry is not None and entry["python"] == shell
    record = projectenv.remembered_record(rig["root"])
    assert record["automatic"] is True and record["trigger"] == projectenv.TRIGGER_AUTO_DETECTED
    assert projectenv.consent_of(record) == projectenv.CONSENT_AUTO_DETECTED
    # 项目后来多了一个能跑的 venv（项目自己的线索排在前面）：检查阶段不起它、不动已有选择；点运行才体检，能跑就换过去
    # （Codex #820 r4233340712：排在后面的先前选择不能把项目环境永远挡在体检之外）
    venv = rig["cand"]("proj/.venv", userenvs.SOURCE_PROJECT_VENV)
    rig["satisfying"].add(venv)
    rig["probed"].clear()
    assert (
        deprepair.decide_environment_pinned(rig["root"], "fig.py", project_exec=False).adopted
        is None
    )
    assert rig["probed"] == []
    assert projectenv.remembered_record(rig["root"])["path"] == shell


def test_a_runnable_builtin_is_kept_unless_the_project_brings_its_own_runnable_env(rig):
    conda = rig["cand"]("conda-env", userenvs.SOURCE_CONDA, "proj")
    rig["satisfying"].add(conda)
    rig["runs_now"] = True

    # 默认链条能跑：机器上别处的环境再好也不换（只有项目自己的线索排在默认链条前面），也不去体检它们
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
    assert projectenv.remembered_record(rig["root"]) is None
    assert rig["probed"] == []

    # 项目自己带了能跑的 venv：用它（ADR 0057 FO11）
    venv = rig["cand"]("proj/.venv", userenvs.SOURCE_PROJECT_VENV)
    rig["satisfying"].add(venv)
    entry = deprepair.decide_environment(rig["root"], "fig.py")
    assert entry["python"] == venv
    assert rig["probed"] == [[venv]]


def test_project_declared_envs_outside_the_project_keep_precedence_at_check_time(rig):
    """Codex #820 r4233842661：默认链条能跑时，`.python-version` → pyenv 里的环境（项目声明、不在项目里）仍按排序在检查阶段
    体检、采用；项目里的 .venv（项目说了算）等运行才体检；机器上别处的 Conda 不抢在前面。"""
    pyenv = rig["cand"]("pyenv-ver", userenvs.SOURCE_PYTHON_VERSION, "3.11")
    conda = rig["cand"]("conda-env", userenvs.SOURCE_CONDA, "other")
    venv = rig["cand"]("proj/.venv", userenvs.SOURCE_PROJECT_VENV)
    rig["satisfying"] |= {pyenv, conda, venv}
    rig["runs_now"] = True

    entry = deprepair.decide_environment_pinned(rig["root"], "fig.py", project_exec=False).adopted
    assert entry is not None and entry["python"] == pyenv
    assert rig["probed"] == [[pyenv]]  # .venv 与 Conda 都没被体检
    # 点运行：项目里的 .venv 才体检（排在前面），能跑就换过去
    rig["probed"].clear()
    entry = deprepair.decide_environment(rig["root"], "fig.py")
    assert entry is not None and entry["python"] == venv


def test_a_declared_pyenv_env_is_preferred_over_the_runnable_default_but_conda_is_not(rig):
    pyenv = rig["cand"]("pyenv-ver", userenvs.SOURCE_PYTHON_VERSION, "3.11")
    conda = rig["cand"]("conda-env", userenvs.SOURCE_CONDA, "other")
    rig["satisfying"] |= {pyenv, conda}
    rig["runs_now"] = True
    entry = deprepair.decide_environment_pinned(rig["root"], "fig.py", project_exec=False).adopted
    assert entry is not None and entry["python"] == pyenv
    assert rig["probed"] == [[pyenv]]


def test_more_candidates_than_the_probe_limit_never_abort_preparation(rig):
    """Codex #820 r4234324630：候选多于体检上限时（13 个 Conda / pyenv），`evaluate` 只回前 12 个——按原候选逐个取结果会取空。
    检查阶段（project_exec=False）要正常收场：上限内最好的被采用，或者什么都不采用，都不抛。"""
    many = [
        rig["cand"](f"conda-{i}", userenvs.SOURCE_CONDA, f"e{i}")
        for i in range(userenvs.PROBE_LIMIT + 1)
    ]
    rig["satisfying"].add(many[2])
    entry = deprepair.decide_environment_pinned(rig["root"], "fig.py", project_exec=False).adopted
    assert entry is not None and entry["python"] == many[2]
    assert len(rig["probed"][0]) == userenvs.PROBE_LIMIT
    # 能跑的排在上限之外：这一轮看不到，也不抛
    projectenv.forget(rig["root"])
    rig["satisfying"].clear()
    rig["satisfying"].add(many[-1])
    assert (
        deprepair.decide_environment_pinned(rig["root"], "fig.py", project_exec=False).adopted
        is None
    )


def test_a_mixed_split_over_the_limit_keeps_ranking_order_and_returns_only_evaluated(rig):
    free = [rig["cand"](f"conda-{i}", userenvs.SOURCE_CONDA) for i in range(8)]
    held = [rig["cand"](f"proj/venv{i}/x", userenvs.SOURCE_PROJECT_VENV) for i in range(6)]
    got = deprepair._evaluate_candidates(
        str(rig["root"]),
        [
            {
                "python": p,
                "source": userenvs.SOURCE_CONDA if p in free else userenvs.SOURCE_PROJECT_VENV,
                "label": "",
            }
            for p in free + held
        ],
        [],
        [],
        project_exec=False,
    )
    assert [e["python"] for e in got] == (free + held)[: userenvs.PROBE_LIMIT]
    assert all(e["checked"] is False for e in got if e["python"] in held)


def test_nothing_runnable_adopts_nothing_and_drops_an_auto_choice_that_stopped_working(rig):
    venv = rig["cand"]("proj/.venv", userenvs.SOURCE_PROJECT_VENV)
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
    assert projectenv.remembered_record(rig["root"]) is None  # 跑不了的项目 venv 不被采用

    # 检测先前挑的那个后来跑不了了（脚本多 import 了一个它没有的包），别处也没有能跑的：作废它——安装目标于是是
    # Tavotto 管理的环境，而不是用户的 venv
    assert projectenv.remember(
        rig["root"], venv, automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED
    )
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
    assert projectenv.remembered_record(rig["root"]) is None
    assert engine_pool.invalidated_decision(rig["root"])["reason"] == "cannot_run"

    # 历史记录（ADR 0114 之前的自动记录）照旧有效：跑不了也不被检测作废（装包仍按既有路径）
    assert projectenv.remember(
        rig["root"], venv, automatic=True, trigger=projectenv.TRIGGER_FIRST_OPEN
    )
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
    assert projectenv.remembered_record(rig["root"])["trigger"] == projectenv.TRIGGER_FIRST_OPEN


def test_the_users_own_choice_and_a_global_lock_are_never_overridden(rig, monkeypatch):
    conda = rig["cand"]("conda-env", userenvs.SOURCE_CONDA)
    rig["satisfying"].add(conda)
    chosen = _touch(rig["root"].parent / "chosen" / "bin" / "python")

    # 用户为本项目选过、仍有效的（含装包之后记下的受管环境）：跑不了也不换
    assert projectenv.remember(rig["root"], chosen, automatic=False, trigger="user_selected")
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
    assert projectenv.remembered_record(rig["root"])["path"] == chosen
    # 明确选回内置
    projectenv.remember_default(rig["root"])
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
    assert projectenv.uses_default_chain(rig["root"])
    projectenv.forget(rig["root"])

    # 全局指定（环境变量）压着：不体检、不记任何东西
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", chosen)
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
    assert projectenv.remembered_record(rig["root"]) is None
    assert rig["probed"] == []


def test_no_decision_is_written_while_an_install_holds_the_project(rig):
    conda = rig["cand"]("conda-env", userenvs.SOURCE_CONDA)
    rig["satisfying"].add(conda)
    pid = managedenv.project_fingerprint(str(rig["root"]))
    # 在途依赖作业登记是 `_active_jobs`（#814 把它与包作业表 `_jobs` 分开了）；`unless_installing` 只看这张表
    with deprepair._lock:
        deprepair._active_jobs["dp-held"] = {"project_id": pid}
    try:
        assert deprepair.decide_environment(rig["root"], "fig.py") is None
        assert projectenv.remembered_record(rig["root"]) is None
    finally:
        with deprepair._lock:
            deprepair._active_jobs.pop("dp-held", None)
    assert deprepair.decide_environment(rig["root"], "fig.py")["python"] == conda


def test_an_engaged_environment_is_not_picked(rig):
    busy = rig["cand"]("busy-env", userenvs.SOURCE_LOGIN_SHELL)
    free = rig["cand"]("free-env", userenvs.SOURCE_CONDA)
    rig["satisfying"] |= {busy, free}
    with envlease.mutating(envlease.env_key_of(busy), busy):  # 正在往它里面装包：体检读的是半成品
        assert deprepair.decide_environment(rig["root"], "fig.py")["python"] == free


# ===========================================================================
# 检测出的解释器是不可变值：同项目两个脚本并发时，不被另一个脚本换掉（Codex #820 r4220794171）
# ===========================================================================
class _Recorder:
    """替身会话：照真 worker 那样在构造时**自己**解析解释器——这正是竞态要钉死的那一步。"""

    instances: list = []

    def __init__(self, script_name, figures_dir, entry):
        self.script_name, self.entry = script_name, entry
        self.python = engine_pool.resolve_worker_python(figures_dir, script=script_name)[0]
        self.last_used = 0.0
        self.down = False
        _Recorder.instances.append(self)

    def alive(self):
        return not self.down

    def shutdown(self):
        self.down = True


@pytest.fixture
def raced(rig, monkeypatch):
    """真 `pool.acquire()` + 真检测；在**检测落地之后、消费方起会话之前**，另一个脚本把共享的项目记录换成它的解释器。"""
    _Recorder.instances = []
    conda = rig["cand"]("conda-env", userenvs.SOURCE_CONDA)
    rig["satisfying"].add(conda)
    other = _touch(rig["root"].parent / "other-script-env" / "bin" / "python")
    monkeypatch.setattr(engine_pool, "_new_worker", lambda sn, fd, en, **k: _Recorder(sn, fd, en))
    monkeypatch.setattr(engine_pool, "_schedule_prune", lambda: None)
    box = rig
    box.update(conda=conda, other=other, race=True)

    def racing_decider(project, script):
        decision = deprepair.decide_environment_pinned(project, script)
        if box["race"]:
            # 另一个脚本的检测在第一个脚本探测成功与再解析之间落地：共享记录换成了它的解释器
            assert projectenv.remember(
                project, other, automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED
            )
        return decision

    monkeypatch.setattr(engine_pool, "ENVIRONMENT_DECIDERS", [racing_decider])
    yield box
    with engine_pool._lock:
        engine_pool._workers.clear()


def test_acquire_never_starts_the_session_on_an_interpreter_another_script_just_recorded(raced):
    """修复前：`acquire()` 在锁外再解析一次共享记录，第一个脚本的会话起在第二个脚本的解释器上——那条解释器从未
    为它验证过。修复后：检测钉下的解释器是不可变值，会话起出来与它对不上就 `environment_changed`，且收掉那条会话。"""
    with pytest.raises(engine_pool.WorkerError) as caught:
        engine_pool.acquire("fig.py", str(raced["root"]), "__main__")
    assert caught.value.code == engine_pool.ENVIRONMENT_CHANGED
    assert [w.down for w in _Recorder.instances] == [True]  # 刚起的会话被收掉，没留在池里
    assert not engine_pool._workers


def test_acquire_without_a_race_starts_on_the_detected_interpreter(raced):
    raced["race"] = False
    w, created = engine_pool.acquire("fig.py", str(raced["root"]), "__main__")
    assert created and w.python == raced["conda"]


def _cache_worker(raced, generation):
    """把一条已活的缓存会话放进池里：解释器是检测会选的那一个，环境代由调用方指定。"""
    w = _Recorder("fig.py", str(raced["root"]), "__main__")
    w.python = raced["conda"]
    w.python_generation = generation
    key = engine_pool._worker_key(str(raced["root"]), "fig.py", None, None)
    with engine_pool._lock:
        engine_pool._workers[key] = w
    return w


def test_acquire_rebuilds_a_cached_worker_born_in_an_obsolete_environment_generation(raced):
    """#820 r4232403630：同一路径的环境被原地重建后，缓存的进程还握着旧 / 混合的包——不许复用，拆掉重建。"""
    raced["race"] = False
    stale = _cache_worker(raced, "obsolete-generation")
    w, created = engine_pool.acquire("fig.py", str(raced["root"]), "__main__")
    assert stale.down and created and w is not stale
    assert w.python == raced["conda"]


def test_acquire_reuses_a_cached_worker_of_the_current_environment_generation(raced):
    raced["race"] = False
    fresh = _cache_worker(raced, projectenv.environment_generation(raced["conda"]))
    w, created = engine_pool.acquire("fig.py", str(raced["root"]), "__main__")
    assert w is fresh and not created and not fresh.down


def test_a_pin_with_a_generation_rejects_a_worker_that_does_not_record_one(raced):
    py = raced["conda"]
    gen = projectenv.environment_generation(py)
    pin = engine_pool.EnvironmentDecision(python=py, generation=gen)
    w = _Recorder("fig.py", str(raced["root"]), "__main__")
    w.python = py
    w.python_generation = ""
    assert not pin.matches_worker(w)  # 钉下有环境代、会话没记：当作不一致
    w.python_generation = gen
    assert pin.matches_worker(w)
    w.python_generation = "old"
    assert not pin.matches_worker(w)
    # 钉下没有环境代：只比路径
    assert engine_pool.EnvironmentDecision(python=py).matches_worker(w)


def test_the_plan_snapshot_keeps_the_detected_interpreter_and_goes_stale_when_the_record_moves(
    raced, monkeypatch
):
    """计划快照取的是检测钉下的解释器（含环境代），不重新解析共享记录；记录被别的脚本换走之后，执行前的对账
    （`_stale_reason`）以 `environment_changed` 拦下，而不是在另一个解释器下跑。"""
    root = str(raced["root"])
    monkeypatch.setattr(deprepair, "gate", lambda r, s: None)
    monkeypatch.setattr(deprepair, "preparation_offer", lambda r, s: None)
    monkeypatch.setattr(
        deprepair,
        "decide_environment_pinned",
        lambda r, s, **kw: pool_decision(raced["conda"]),
    )

    def pool_decision(python):
        return engine_pool.EnvironmentDecision(
            adopted={"python": python},
            python=python,
            source=engine_pool.SOURCE_SYSTEM,
            generation=projectenv.environment_generation(python),
        )

    # 共享记录此刻已是另一个脚本的解释器
    assert projectenv.remember(
        root, raced["other"], automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED
    )
    plan = preparation.plan_for(
        project_id="pj",
        project_root=root,
        asset_id="a",
        stem="fig",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
    )
    assert plan.interpreter == raced["conda"]
    assert plan.environment["generation"] == projectenv.environment_generation(raced["conda"])
    assert preparation.PreparationService._stale_reason(plan) == (
        preparation.STALE_ENVIRONMENT,
        {},
    )


# ---- 真 `_new_worker` / 真 `one_shot`：只把「起进程」换成记账（Codex #820 r4221133355 / r4221133372） ----
_REAL_RESOLVE = engine_pool.resolve_worker_python  # 在任何夹具替换它之前取下


class _FakeProc:
    pid = 4242
    stdin = stdout = None

    def poll(self):
        return None

    def kill(self):
        pass

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def real_spawn(tmp_path, monkeypatch):
    """共享的项目记录是另一个脚本的解释器 Y；第一个脚本检测钉下的是 X。解析、门、构造函数全是真的，只有起进程是记账。"""
    from tavotto.engine import workerd_client

    root = tmp_path / "proj"
    root.mkdir()
    x = _touch(tmp_path / "env-x" / "bin" / "python")
    y = _touch(tmp_path / "env-y" / "bin" / "python")
    monkeypatch.setattr(engine_pool, "resolve_worker_python", _REAL_RESOLVE)
    monkeypatch.setattr(engine_pool, "_has_matplotlib", lambda p: True)
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: None)
    monkeypatch.setattr(engine_pool, "_schedule_prune", lambda: None)
    launched: list[str] = []

    def popen(argv, **kw):
        launched.append(argv[0])
        return _FakeProc()

    monkeypatch.setattr(engine_pool.subprocess, "Popen", popen)
    # 没有真进程可优雅关停：shutdown 只做逻辑标记
    monkeypatch.setattr(
        engine_pool.EngineWorker, "shutdown", lambda self: setattr(self, "_dead", True)
    )
    gate_saw: list[str] = []

    def spy_gate(figures_dir, script_name):
        gate_saw.append(engine_pool.resolve_worker_python(figures_dir, script=script_name)[0])

    monkeypatch.setattr(engine_pool, "SPAWN_GATES", [spy_gate])
    # 共享记录此刻是 Y（另一个脚本的检测刚落地）
    assert projectenv.remember(
        str(root), y, automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED
    )
    pin = engine_pool.EnvironmentDecision(
        python=x,
        source=engine_pool.SOURCE_SYSTEM,
        generation=projectenv.environment_generation(x),
    )
    yield {
        "root": str(root),
        "x": x,
        "y": y,
        "pin": pin,
        "launched": launched,
        "gate_saw": gate_saw,
    }
    for w in list(engine_pool._workers.values()):
        w.shutdown()
    engine_pool._workers.clear()


def test_real_new_worker_hands_the_pinned_interpreter_to_the_gate_and_the_constructor(real_spawn):
    """修复前：依赖门与 `EngineWorker` 构造函数各自再解析一遍共享记录，拿到的是 Y——第一个脚本会为 Y 弹依赖授权、
    甚至先起在 Y 上再被拒。修复后：钉下的决定作为参数一路传进门和构造函数。"""
    w = engine_pool._new_worker("fig.py", real_spawn["root"], "__main__", pinned=real_spawn["pin"])
    try:
        assert real_spawn["gate_saw"] == [real_spawn["x"]]
        assert real_spawn["launched"] == [real_spawn["x"]]
        assert w.python == real_spawn["x"]
    finally:
        w.shutdown()


def test_real_new_worker_without_a_pin_still_follows_the_shared_record(real_spawn):
    """对照：不钉就是原来的行为（共享记录 = Y），证明上面那条不是夹具本来就只会回 X。"""
    w = engine_pool._new_worker("fig.py", real_spawn["root"], "__main__")
    try:
        assert real_spawn["gate_saw"] == [real_spawn["y"]] and w.python == real_spawn["y"]
    finally:
        w.shutdown()


def test_a_rebuilt_pinned_environment_fails_before_anything_is_started(real_spawn):
    Path(real_spawn["x"]).unlink()
    _touch(Path(real_spawn["x"]))  # 同一路径删了重建：环境代换了
    os.utime(real_spawn["x"], (1, 1))
    with pytest.raises(engine_pool.WorkerError) as caught:
        engine_pool._new_worker("fig.py", real_spawn["root"], "__main__", pinned=real_spawn["pin"])
    assert caught.value.code == engine_pool.ENVIRONMENT_CHANGED
    assert real_spawn["launched"] == []


def test_one_shot_replays_on_the_hot_workers_interpreter_not_the_current_record(real_spawn):
    """写回的全量重放要和热态同一个解释器；同项目另一个脚本把记录换成 Y 之后，重放仍是 X（修复前是 Y）。"""
    hot = type("Hot", (), {})()
    hot.python, hot.python_source = real_spawn["x"], engine_pool.SOURCE_SYSTEM
    hot.python_generation = projectenv.environment_generation(real_spawn["x"])
    fresh = engine_pool.one_shot(
        "fig.py", real_spawn["root"], "__main__", pinned=engine_pool.pin_of_worker(hot)
    )
    try:
        assert fresh.python == real_spawn["x"] and real_spawn["launched"] == [real_spawn["x"]]
    finally:
        engine_pool.discard(fresh)
    # 对照：不钉就是共享记录
    other = engine_pool.one_shot("fig.py", real_spawn["root"], "__main__")
    try:
        assert other.python == real_spawn["y"]
    finally:
        engine_pool.discard(other)


def test_a_live_worker_records_its_environment_generation(real_spawn):
    w = engine_pool._new_worker("fig.py", real_spawn["root"], "__main__", pinned=real_spawn["pin"])
    try:
        assert w.python_generation == projectenv.environment_generation(real_spawn["x"])
        assert engine_pool.pin_of_worker(w).matches(real_spawn["x"])
    finally:
        w.shutdown()


# ===========================================================================
# 默认链条一个能跑的解释器都没有：检测要在第一次解析失败之后立刻跑（Codex #820 r4221391630）
# ===========================================================================
@pytest.fixture
def no_default_python(rig, monkeypatch):
    venv = rig["cand"]("proj/.venv", userenvs.SOURCE_PROJECT_VENV)
    rig["satisfying"].add(venv)
    root = rig["root"]

    def resolve(figures_dir=None, *, script=None, discover=True):
        record = projectenv.remembered_record(root)
        if record and record.get("exists") and record.get("mode") != "default":
            return record["path"], engine_pool.SOURCE_PROJECT_VENV
        raise engine_pool.WorkerError("没有可用的 Python", code="no_worker_python")

    monkeypatch.setattr(engine_pool, "resolve_worker_python", resolve)
    _Recorder.instances = []
    monkeypatch.setattr(engine_pool, "_new_worker", lambda sn, fd, en, **k: _Recorder(sn, fd, en))
    monkeypatch.setattr(engine_pool, "_schedule_prune", lambda: None)
    yield {**rig, "venv": venv}
    with engine_pool._lock:
        engine_pool._workers.clear()


@pytest.mark.parametrize("entry_point", ["acquire", "get"], ids=["render-acquire", "probe-mcp-get"])
def test_pool_only_entry_points_detect_the_project_venv_when_the_default_chain_has_nothing(
    no_default_python, entry_point
):
    """修复前：`acquire()` 的第一次解析就抛 `no_worker_python`，永远走不到决定者。渲染已有素材（acquire）与旧试运行 /
    MCP（`get` → acquire）都只经池。修复后：检测先跑，项目 venv 能跑就用它。"""
    root = str(no_default_python["root"])
    got = getattr(engine_pool, entry_point)("fig.py", root, "__main__")
    w = got[0] if isinstance(got, tuple) else got
    assert w.python == no_default_python["venv"]


@pytest.mark.parametrize("entry_point", ["acquire", "get"], ids=["render-acquire", "probe-mcp-get"])
def test_detection_derives_imports_without_the_missing_default_interpreter(
    no_default_python, monkeypatch, entry_point
):
    """Codex #820 r4232654893：这里**不**替身 `joint_plan_for`——真的联合计划要先解析默认解释器，在默认链条一个
    都没有时它自己也抛 `no_worker_python`，修复前检测在那里提前返回、候选根本没被体检。修复后：脚本的 import 静态
    推出，拿去体检项目 venv，能跑就采用。"""
    monkeypatch.setattr(deprepair, "joint_plan_for", _REAL_JOINT_PLAN_FOR)
    root = no_default_python["root"]
    (root / "fig.py").write_text("import alpha\n", "utf-8")
    with pytest.raises(engine_pool.WorkerError) as caught:
        _REAL_JOINT_PLAN_FOR(str(root), "fig.py")
    assert caught.value.code == "no_worker_python"  # 前提：真实解析确实失败，不是被绕过
    got = getattr(engine_pool, entry_point)("fig.py", str(root), "__main__")
    w = got[0] if isinstance(got, tuple) else got
    assert w.python == no_default_python["venv"]
    # 体检用的是脚本真实的 import
    assert (
        no_default_python["probed"] and no_default_python["venv"] in no_default_python["probed"][0]
    )


# ===========================================================================
# 项目自带的解释器在用户点「运行」之前不执行（Codex 安全 #820 r4232804805）
# ===========================================================================
def _env_root(python: str) -> str:
    """解释器所在环境的根（`<root>/bin/python` 的上两级，不跟软链接）：两个 venv 的 `bin/python` 都软链到同一个基础解释器，
    拿 realpath 比较会把不同环境判成同一个。"""
    return os.path.realpath(os.path.dirname(os.path.dirname(python)))


def _evil_project(tmp_path: Path) -> dict:
    root = tmp_path / "attacker"
    root.mkdir()
    (root / "fig.py").write_text("import alpha\n", "utf-8")
    marks = tmp_path / "marks"
    marks.mkdir()

    def trap(path: Path, name: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho x > '{marks / name}'\nexit 3\n", "utf-8")
        path.chmod(0o755)
        return str(path)

    evil = trap(root / "python-evil", "vscode")
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text(
        '{"python.defaultInterpreterPath": "${workspaceFolder}/python-evil"}', "utf-8"
    )
    venv = trap(root / ".venv" / "bin" / "python", "venv")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", "utf-8")
    return {"root": root, "marks": marks, "evil": evil, "venv": venv}


@posix_only
@pytest.mark.parametrize("no_default", [False, True], ids=["default-chain", "no-default-python"])
def test_project_controlled_interpreters_are_not_executed_before_run(
    tmp_path, monkeypatch, no_default
):
    """检查（建会话 / 出报告 / 门）发现 `.vscode` 指向的 `python-evil` 与项目里的 `.venv/bin/python` 都只列不起；
    运行那一下（`decide_environment`）才体检。no_default：默认链条一个解释器都没有时（静态计划那条路）同样成立。"""
    world = _evil_project(tmp_path)
    root = str(world["root"])
    if no_default:

        def resolve(figures_dir=None, *, script=None, discover=True):
            raise engine_pool.WorkerError("没有可用的 Python", code="no_worker_python")

        monkeypatch.setattr(engine_pool, "resolve_worker_python", resolve)
    cands = deprepair.user_environment_candidates(root, "fig.py")
    assert {c["source"] for c in cands} >= {userenvs.SOURCE_VSCODE, userenvs.SOURCE_PROJECT_VENV}
    assert all(userenvs.is_project_controlled(c, root) for c in cands)

    preparation.plan_for(
        project_id="pj",
        project_root=root,
        asset_id="a",
        stem="fig",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
    )
    plan = {"missing": [{"import_name": "alpha", "distribution": "alpha"}], "unknown": []}
    offer = deprepair.user_environment_offer(root, "fig.py", plan, "")
    assert offer and all(e["checked"] is False for e in offer)  # 列出来，未检查
    assert sorted(p.name for p in world["marks"].iterdir()) == []
    assert projectenv.remembered_record(root) is None

    # 用户点了「运行」：现在才体检（候选起了；它们跑不了，所以不采用）
    assert deprepair.decide_environment(root, "fig.py") is None
    assert {p.name for p in world["marks"].iterdir()} == {"vscode", "venv"}


def test_the_project_controlled_predicate_has_one_definition(tmp_path):
    root = tmp_path / "p"
    (root / "sub").mkdir(parents=True)
    outside = tmp_path / "out" / "bin" / "python"
    outside.parent.mkdir(parents=True)
    outside.write_text("")
    inside = root / "sub" / "python"
    inside.write_text("")
    pc = userenvs.is_project_controlled
    assert pc({"python": str(inside), "source": userenvs.SOURCE_CONDA}, root)  # 路径在项目根里
    assert pc(
        {"python": str(outside), "source": userenvs.SOURCE_VSCODE}, root
    )  # 项目文件写出的路径
    assert pc({"python": str(outside), "source": userenvs.SOURCE_SHEBANG}, root)
    assert not pc({"python": str(outside), "source": userenvs.SOURCE_CONDA}, root)
    assert not pc({"python": str(outside), "source": userenvs.SOURCE_PYTHON_VERSION}, root)
    assert not pc({"python": str(root) + "-sibling/python", "source": userenvs.SOURCE_SYSTEM}, root)


def test_an_explicit_selection_failure_is_still_raised_untouched(no_default_python, monkeypatch):
    def resolve(figures_dir=None, **kw):
        err = engine_pool.WorkerError("x", code="explicit_python_unusable")
        err.explicit = {"source": "env"}
        raise err

    monkeypatch.setattr(engine_pool, "resolve_worker_python", resolve)
    with pytest.raises(engine_pool.WorkerError) as caught:
        engine_pool.acquire("fig.py", str(no_default_python["root"]), "__main__")
    assert caught.value.code == "explicit_python_unusable"


# ===========================================================================
# 作废的事实只报一次（Codex #820 r4221391657）
# ===========================================================================
def test_the_replacement_is_reported_exactly_once(rig, monkeypatch):
    root = str(rig["root"])
    monkeypatch.setattr(deprepair, "gate", lambda r, s: None)
    monkeypatch.setattr(deprepair, "preparation_offer", lambda r, s: None)
    gone = rig["cand"]("gone-env", userenvs.SOURCE_CONDA)
    assert projectenv.remember(root, gone, automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED)
    engine_pool.invalidate_remembered(root, projectenv.remembered_record(root), "missing")
    rig["runs_now"] = True

    def plan():
        return preparation.plan_for(
            project_id="pj",
            project_root=root,
            asset_id="a",
            stem="fig",
            script="fig.py",
            entry="__main__",
            original_artifact=None,
        )

    first = plan().environment
    assert first["adoption"]["switched"] is True and first["adoption"]["replaced"] == {
        "reason": "missing"
    }
    assert first["invalidated"] is not None
    second = plan().environment
    assert second["adoption"]["switched"] is False and second["adoption"]["replaced"] is None
    assert second["invalidated"] is None
    # 又发生了新的作废：再报一次
    assert projectenv.remember(root, gone, automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED)
    engine_pool.invalidate_remembered(root, projectenv.remembered_record(root), "rebuilt")
    third = plan().environment
    assert third["adoption"]["switched"] is True and third["adoption"]["replaced"] == {
        "reason": "rebuilt"
    }


# ===========================================================================
# 报告里的「换过」：由生效的前后推导，作废→默认链条也算（Codex #820 r4220794183）
# ===========================================================================
def test_switched_is_derived_from_the_effective_before_and_after():
    gone = {"python": "/old/venv/bin/python", "reason": "missing", "trigger": "auto_detected"}
    fact = envadvice.adoption_fact(
        "/p",
        engine_pool.SOURCE_BUNDLED,
        adopted=None,
        invalidated=gone,
        effective="/bundled/python",
    )
    assert fact["switched"] is True  # 记住的没了、没采用新候选，回退到默认链条：用户已被换了
    assert fact["replaced"] == {"reason": "missing"}
    # 作废之后又回到同一条路径（重建回来）：前后没变，不算换
    same = envadvice.adoption_fact(
        "/p", engine_pool.SOURCE_BUNDLED, adopted=None, invalidated=gone, effective=gone["python"]
    )
    assert same["switched"] is False
    # 采用了新候选：照旧算换
    adopted = envadvice.adoption_fact(
        "/p", engine_pool.SOURCE_SYSTEM, adopted={"python": "/x"}, invalidated=None, effective="/x"
    )
    assert adopted["switched"] is True and adopted["replaced"] is None
    # 什么都没发生
    quiet = envadvice.adoption_fact(
        "/p",
        engine_pool.SOURCE_BUNDLED,
        adopted=None,
        invalidated=None,
        effective="/bundled/python",
    )
    assert quiet["switched"] is False and quiet["replaced"] is None


def test_a_vanished_remembered_interpreter_that_falls_back_to_the_default_chain_is_reported(
    rig, monkeypatch
):
    """记住的解释器被作废、默认链条能跑脚本：检测没有可采用的新候选，报告仍要说「原来那套不能用了，已换好」。"""
    root = str(rig["root"])
    monkeypatch.setattr(deprepair, "gate", lambda r, s: None)
    monkeypatch.setattr(deprepair, "preparation_offer", lambda r, s: None)
    gone = rig["cand"]("gone-env", userenvs.SOURCE_CONDA)
    assert projectenv.remember(root, gone, automatic=True, trigger=projectenv.TRIGGER_AUTO_DETECTED)
    engine_pool.invalidate_remembered(root, projectenv.remembered_record(root), "missing")
    rig["runs_now"] = True
    plan = preparation.plan_for(
        project_id="pj",
        project_root=root,
        asset_id="a",
        stem="fig",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
    )
    fact = plan.environment["adoption"]
    assert fact["switched"] is True and fact["replaced"] == {"reason": "missing"}


# ===========================================================================
# 真走一遍：能跑就直接用 / 都不能跑只有一张安装卡（真 venv、真 worker、离线 wheelhouse）
# ===========================================================================
def _install_into(python: str, house: Path, dist: str) -> None:
    out = subprocess.run(
        [python, "-m", "pip", "install", "--no-index", "--find-links", str(house), dist],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]


@needs_worker
def test_a_project_env_that_can_run_the_script_is_used_and_the_report_only_says_so(
    client, house, opened
):
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    venv_python = envworld.real_venv(proj, ".venv", python=WORKER_PY)
    _install_into(venv_python, house, ALPHA[0])

    # 检查（用户点「开始准备」）不起项目自带的解释器（Codex 安全 #820 r4232804805）：此刻还没采用它
    report = _create(client, {"script": "figure.py"})
    assert projectenv.remembered_record(proj) is None
    assert report["environment"]["decided_by"] != "auto"
    # 点「运行」那一下才体检、采用：能跑就记下，之后的检查读到的就是它
    entry = deprepair.decide_environment(proj, "figure.py")
    assert entry is not None and entry["python"] == venv_python
    record = projectenv.remembered_record(proj)
    assert record["trigger"] == projectenv.TRIGGER_AUTO_DETECTED and record["generation"]
    report = _create(client, {"script": "figure.py"})
    assert report["phase"] == "ready_to_run", report
    assert [r for r in report["requirements"] if r.get("blocking", True)] == []
    assert "run" in _kinds(report) and "prepare_dependencies" not in _kinds(report)
    assert report["environment"] == {
        "mode": "detect",
        "kind": "project",
        "decided_by": "auto",
        "switched": False,
        "replaced": None,
    }
    env_check = next(c for c in report["checks"] if c["id"] == "environment")
    assert env_check["status"] == "ok"

    assert _act(client, report, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert final["phase"] == "completed", final
    worker = next(iter(engine_pool._workers.values()))
    assert Path(worker.python).resolve() == Path(venv_python).resolve()


@needs_worker
def test_when_nothing_can_run_the_only_todo_is_installing_into_tavottos_environment(
    client, house, offline_managed_env, opened
):
    """项目里有一个跑不了这个脚本的 venv（缺包）：确认模式下这是一次「选环境」，检测模式下不是——只有一条安装待办、
    一个 `prepare_dependencies` 动作，目标是 Tavotto 管理的环境；装完同一个会话里就能运行出图。"""
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    envworld.real_venv(proj, ".venv", python=WORKER_PY)  # 健康，但没有 tavotto_test_alpha

    first = _create(client, {"script": "figure.py"})
    # 项目 venv 还没体检（要等「运行」）：首查以运行为主；体检跑不了才回到安装待办
    assert "run" in _kinds(first) and "prepare_dependencies" not in _kinds(first)
    assert _act(client, first, "run").status_code == 202
    _wait(client, first["session_id"], lambda r: "prepare_dependencies" in _kinds(r))
    report = _get(client, first["session_id"])

    assert report["phase"] == "awaiting_confirmation", report
    assert [r["kind"] for r in report["requirements"]] == ["dependency_authorization"]
    assert not any(r["kind"] == "environment_choice" for r in report["requirements"])
    assert _kinds(report) == ["prepare_dependencies", "recheck"]
    (action,) = [a for a in report["actions"] if a["kind"] == "prepare_dependencies"]
    assert action["impact"]["target_kind"] == deprepair.TARGET_MANAGED
    assert action["impact"]["modifies_user_environment"] is False
    assert report["environment"]["kind"] == "builtin" and report["environment"]["switched"] is False
    assert projectenv.remembered_record(proj) is None

    assert _act(client, report, "prepare_dependencies").status_code == 202
    ready = _wait(client, report["session_id"], lambda r: r["phase"] == "ready_to_run")
    assert ready["environment"]["kind"] == "managed"
    assert _act(client, ready, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert final["phase"] == "completed", final


@needs_worker
def test_clean_machine_with_an_unchecked_project_venv_offers_run_and_run_adopts_it(
    client, house, opened, monkeypatch
):
    """Codex #820 r4233563595：默认链条一个解释器都没有（干净机器）+ 项目 .venv 还没体检：首份报告主动作是运行（不先推
    私有 Python / 受管环境安装），点一次运行：体检、采用 .venv、就地重算、出图。"""
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    venv_python = envworld.real_venv(proj, ".venv", python=WORKER_PY)
    _install_into(venv_python, house, ALPHA[0])
    orig = engine_pool.resolve_worker_python

    def resolve(*a, **k):
        rec = projectenv.remembered_record(proj)
        if not (rec and rec.get("exists") and rec.get("mode") != "default"):
            raise engine_pool.WorkerError("没有可用的 Python", code="no_worker_python")
        return orig(*a, **k)

    monkeypatch.setattr(engine_pool, "resolve_worker_python", resolve)

    report = _create(client, {"script": "figure.py"})
    assert projectenv.remembered_record(proj) is None  # 检查没起 .venv
    assert report["phase"] == "ready_to_run", report
    kinds = _kinds(report)
    assert "run" in kinds and "prepare_dependencies" not in kinds
    assert _act(client, report, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert final["phase"] == "completed", final
    assert _env_root(projectenv.remembered_record(proj)["path"]) == _env_root(venv_python)


@needs_worker
def test_unchecked_project_env_makes_run_primary_and_run_adopts_it_in_one_action(
    client, house, opened
):
    """首查：项目 venv 还没体检 → 主动作是运行（不推去受管环境装包），不起它；点「运行」一次：体检、采用、就地重算计划、
    在项目 venv 里出图——不报 `environment_changed`、不要用户重新检查。"""
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    venv_python = envworld.real_venv(proj, ".venv", python=WORKER_PY)
    _install_into(venv_python, house, ALPHA[0])

    report = _create(client, {"script": "figure.py"})
    assert report["phase"] == "ready_to_run", report
    kinds = _kinds(report)
    assert "run" in kinds and "prepare_dependencies" not in kinds
    deps = next(c for c in report["checks"] if c["id"] == "dependencies")
    assert deps["status"] == "ok" and deps["detail"] == {"deferred": "project_environment"}
    assert projectenv.remembered_record(proj) is None  # 检查没起它

    assert _act(client, report, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert final["phase"] == "completed", final
    assert "environment_changed" not in json.dumps(final)
    record = projectenv.remembered_record(proj)
    assert record["trigger"] == projectenv.TRIGGER_AUTO_DETECTED
    assert _env_root(record["path"]) == _env_root(venv_python)
    # 会话的计划跟上了：再读一次报告，环境是项目的，没有安装待办
    again = _get(client, report["session_id"])
    assert again["environment"]["kind"] == "project" and "prepare_dependencies" not in _kinds(again)


@needs_worker
def test_run_probe_that_finds_the_project_env_unfit_falls_back_to_the_install_outcome(
    client, house, opened, monkeypatch
):
    """项目 venv 存在但没装脚本要的包：点运行 → 体检跑不了 → 回到常规的「需要安装」（受管环境、带影响摘要），不是错误。"""
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    envworld.real_venv(proj, ".venv", python=WORKER_PY)  # 不装 alpha

    report = _create(client, {"script": "figure.py"})
    assert "run" in _kinds(report) and "prepare_dependencies" not in _kinds(report)
    # 刷新到安装待办必须和"尝试收尾"原子落地：把运行之后的重新规划拖慢，读者一旦看到尝试结束，报告就已经是安装待办
    slow = prepsession.SessionService._plan_of

    def slow_plan_of(*a, **k):
        time.sleep(1.5)
        return slow(*a, **k)

    monkeypatch.setattr(prepsession.SessionService, "_plan_of", staticmethod(slow_plan_of))
    assert _act(client, report, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert final["phase"] != "completed", final
    assert "environment_changed" not in json.dumps(final)
    assert projectenv.remembered_record(proj) is None  # 跑不了的项目 venv 不被采用
    assert "prepare_dependencies" in _kinds(_get(client, report["session_id"]))


def _lower_priority_env(tmp_path, monkeypatch, house, *, with_alpha: bool = True) -> str:
    """项目外的一个 Conda 环境（排在项目自己的环境后面）；`with_alpha` = 装了脚本要的包。"""
    base = tmp_path / "conda"
    base.mkdir()
    python = envworld.real_venv(base, "lab", python=WORKER_PY)
    if with_alpha:
        _install_into(python, house, ALPHA[0])
    monkeypatch.setattr(userenvs, "_conda_prefixes", lambda *a, **k: [str(base / "lab")])
    userenvs.reset_cache()
    return python


@needs_worker
def test_a_pending_project_env_is_probed_on_run_before_a_lower_priority_one_is_adopted(
    client, house, opened, tmp_path, monkeypatch
):
    """Codex #820 r4233340712：默认链条跑不了、项目 .venv 还没体检、项目外有个能跑的 Conda：检查阶段什么都不采用（不让
    排在后面的先挡住项目的），点运行先体检 .venv，能跑就用它。"""
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    venv_python = envworld.real_venv(proj, ".venv", python=WORKER_PY)
    _install_into(venv_python, house, ALPHA[0])
    _lower_priority_env(tmp_path, monkeypatch, house)

    report = _create(client, {"script": "figure.py"})
    assert projectenv.remembered_record(proj) is None  # 检查没采用任何环境
    assert "run" in _kinds(report) and "prepare_dependencies" not in _kinds(report)
    assert _act(client, report, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert final["phase"] == "completed", final
    record = projectenv.remembered_record(proj)
    assert _env_root(record["path"]) == _env_root(venv_python)


@needs_worker
def test_when_the_pending_project_env_cannot_run_the_next_candidate_is_used(
    client, house, opened, tmp_path, monkeypatch
):
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    envworld.real_venv(proj, ".venv", python=WORKER_PY)  # 没装 alpha
    lab = _lower_priority_env(tmp_path, monkeypatch, house)

    report = _create(client, {"script": "figure.py"})
    assert projectenv.remembered_record(proj) is None
    assert _act(client, report, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))

    # 落到下一个候选（项目外的用户环境）：采用了它，同时默认工作目录档变成「在脚本目录里跑」（ADR 0107 §二）——授权档变了
    # 不就地吸收（那是用户没确认过的写入范围），照旧报过期、让用户重新检查看到新档；重新检查之后一次运行就出图
    assert _env_root(projectenv.remembered_record(proj)["path"]) == _env_root(lab)
    assert final["outcome"]["kind"] == "stale" and final["outcome"]["reason"] == "grant_changed", (
        final
    )
    again = _act(client, final, "recheck")
    assert again.status_code in (200, 202)
    fresh = _get(client, report["session_id"])
    assert "run" in _kinds(fresh)
    assert _act(client, fresh, "run").status_code == 202
    done = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert done["phase"] == "completed", done


@needs_worker
def test_an_earlier_lower_priority_choice_does_not_keep_a_new_project_env_unprobed(
    house, opened, tmp_path, monkeypatch
):
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    lab = _lower_priority_env(tmp_path, monkeypatch, house)
    first = deprepair.decide_environment(proj, "figure.py")
    assert first is not None and _env_root(first["python"]) == _env_root(lab)
    venv_python = envworld.real_venv(proj, ".venv", python=WORKER_PY)
    _install_into(venv_python, house, ALPHA[0])
    # 检查阶段：不起 .venv，已有的选择不动
    assert (
        deprepair.decide_environment_pinned(proj, "figure.py", project_exec=False).adopted is None
    )
    # 运行：先体检 .venv（排在前面），能跑就换过去
    second = deprepair.decide_environment(proj, "figure.py")
    assert second is not None and _env_root(second["python"]) == _env_root(venv_python)


def test_the_probe_cache_is_keyed_by_environment_generation(tmp_path, monkeypatch):
    """Codex #820 r4233884149：同一路径被重建（换代）之后，旧的体检结论不再命中——缓存读（含 cache_only）也一样。"""
    py = tmp_path / "env" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    cfg = tmp_path / "env" / "pyvenv.cfg"
    cfg.write_text("home = /x\n")
    calls = []

    def probe(python, modules=(), **kw):
        calls.append(python)
        return {"ok": True, "modules_ok": {m: True for m in modules}}

    monkeypatch.setattr(projectenv, "probe_environment", probe)
    userenvs.reset_cache()
    assert userenvs._probe(str(py), ("alpha",))["ok"] is True
    userenvs._probe(str(py), ("alpha",))
    assert len(calls) == 1 and userenvs.cached_probe(str(py), ("alpha",)) is not None
    cfg.write_text("home = /x/rebuilt-with-a-longer-line\n")  # 重建：pyvenv.cfg 被重写
    assert userenvs.cached_probe(str(py), ("alpha",)) is None
    userenvs._probe(str(py), ("alpha",))
    assert len(calls) == 2


@needs_worker
def test_a_cached_fit_verdict_is_not_trusted_after_the_env_lost_its_packages(
    house, opened, tmp_path, monkeypatch
):
    """装包被卸掉（不换代）：缓存里还是"装齐"，采用前现量一次，量不过就落到下一个能跑的候选。"""
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    base = tmp_path / "conda"
    base.mkdir()
    top = envworld.real_venv(base, proj.name, python=WORKER_PY)  # 名字对上项目：排在前面
    low = envworld.real_venv(base, "lab", python=WORKER_PY)
    for py in (top, low):
        _install_into(py, house, ALPHA[0])
    monkeypatch.setattr(
        userenvs, "_conda_prefixes", lambda *a, **k: [str(base / proj.name), str(base / "lab")]
    )
    userenvs.reset_cache()
    first = deprepair.decide_environment(proj, "figure.py")
    assert _env_root(first["python"]) == _env_root(top)
    # 清掉记录，卸掉 top 里的包（路径与代都不变），缓存里仍是"装齐"
    snapshot = dict(userenvs._probe_cache)  # 采用时留下的体检结论（含 top 的「装齐」）
    assert snapshot
    projectenv.forget(proj)
    subprocess.run(
        [top, "-m", "pip", "uninstall", "-y", ALPHA[0]],
        capture_output=True,
        timeout=120,
        check=True,
    )
    userenvs._probe_cache.update(snapshot)  # 缓存里仍是过期的「装齐」
    second = deprepair.decide_environment(proj, "figure.py")
    assert second is not None and _env_root(second["python"]) == _env_root(low)


@needs_worker
def test_the_run_acquires_the_planned_interpreter_even_if_the_record_moves_just_before(
    client, house, opened, monkeypatch
):
    """Codex #820 r4234067402：检查对账之后、取会话之前，另一个脚本把项目记录换成别的能跑的解释器——这次运行钉着计划自己的
    解释器：要么在计划的解释器里跑，要么 `environment_changed`，绝不静默跑进另一个。"""
    proj = opened([ALPHA], script_body="import tavotto_test_alpha as _a\nY = _a.VALUE\n")
    planned = envworld.real_venv(proj, ".venv", python=WORKER_PY)
    other = envworld.real_venv(proj, ".venv2", python=WORKER_PY)
    for py in (planned, other):
        _install_into(py, house, ALPHA[0])
    assert projectenv.remember(proj, planned, automatic=False, trigger="user_selected")
    report = _create(client, {"script": "figure.py"})
    assert "run" in _kinds(report)

    real_acquire = engine_pool.acquire
    moved = []

    def racy(*a, **k):
        if not moved:  # 对账之后、取会话之前：另一个脚本换了共享记录
            moved.append(1)
            assert projectenv.remember(proj, other, automatic=False, trigger="user_selected")
        return real_acquire(*a, **k)

    monkeypatch.setattr(engine_pool, "acquire", racy)
    assert _act(client, report, "run").status_code == 202
    final = _wait(client, report["session_id"], lambda r: r["phase"] not in ("running",))
    assert moved
    worker = engine_pool.peek("figure.py", str(proj))
    if worker is not None:
        assert _env_root(worker.python) == _env_root(planned), "跑进了另一个解释器"
    else:
        assert final["phase"] != "completed", final


def _editable_world(tmp_path: Path, *, sitecustomize: bool = False):
    """Codex 安全 #820 r4234465621：项目外的 venv 装了指向 `<项目>/src` 的 .pth（可编辑安装），`payload/__init__.py` 在被
    import 时写哨兵。脚本 `import payload`——import 它就是执行项目代码。"""
    root = tmp_path / "proj"
    (root / "src" / "payload").mkdir(parents=True)
    marker = tmp_path / "payload-ran"
    (root / "src" / "payload" / "__init__.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\n", "utf-8"
    )
    if sitecustomize:
        # 解释器一启动（site 处理 .pth 时）就执行项目代码：sitecustomize.py，以及 .pth 里的 `import` 行解析进项目
        # （可编辑安装的 finder 就是这个形状）。有的发行版自带 stdlib 级 sitecustomize 会遮住前者，所以两条都放
        (root / "src" / "sitecustomize.py").write_text(
            f"open({str(marker)!r}, 'w').write('boot')\n", "utf-8"
        )
        (root / "src" / "startup_hook.py").write_text(
            f"open({str(marker)!r}, 'w').write('boot')\n", "utf-8"
        )
    (root / "fig.py").write_text(
        "import payload\nimport matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('o.pdf')\n",
        "utf-8",
    )
    base = tmp_path / "ext"
    base.mkdir()
    py = envworld.real_venv(base, "lab", python=WORKER_PY)
    site_dir = next((base / "lab" / "lib").glob("python*")) / "site-packages"
    (site_dir / "proj.pth").write_text(str(root / "src") + "\n", "utf-8")
    if sitecustomize:
        (site_dir / "zz_boot.pth").write_text("import startup_hook\n", "utf-8")
    return root, marker, py, base


@posix_only
@needs_worker
def test_the_pre_run_probe_never_imports_modules_that_resolve_into_the_project(tmp_path):
    root, marker, py, _base = _editable_world(tmp_path)
    spec = projectenv.probe_environment(
        py, modules=("payload", "json"), import_mode="spec", project_root=str(root)
    )
    assert not marker.exists(), "运行之前的体检 import 了项目里的模块"
    # 环境的 site-packages 里有指向项目的 .pth：整个环境延后（什么都没 import、.pth 只当文本读）
    assert spec.get("deferred_env") is True
    # 对照（尺子是活的）：import 方式真的会执行它
    projectenv.probe_environment(py, modules=("payload",))
    assert marker.exists()


@posix_only
@needs_worker
@pytest.mark.parametrize("a_first", [True, False], ids=["A-then-B", "B-then-A"])
def test_spec_probe_results_are_per_project_root(tmp_path, a_first):
    """Codex #820 r4234612525：一个共享的外部解释器里，模块可编辑安装指向项目 A 的 src——在 A 里它解析进项目（延后），
    在 B 里不在项目里（装齐）。两个项目的检查顺序无论怎样，结论都各算各的。"""
    root_a, marker, py, _base = _editable_world(tmp_path)
    root_b = tmp_path / "other"
    root_b.mkdir()
    need = [{"import_name": "payload", "distribution": "payload"}]
    cand = [{"python": py, "source": userenvs.SOURCE_CONDA, "label": "lab"}]
    userenvs.reset_cache()

    def check(root):
        (got,) = userenvs.evaluate(cand, need, [], root=str(root))
        return got

    order = [("a", root_a), ("b", root_b)] if a_first else [("b", root_b), ("a", root_a)]
    results = {name: check(root) for name, root in order}
    assert results["a"].get("deferred") is True and results["a"]["satisfies"] is None
    assert results["b"]["satisfies"] is True and not results["b"].get("deferred")
    assert not marker.exists()
    # cache_only 读也按各自的项目根
    (ca,) = userenvs.evaluate(cand, need, [], cache_only=True, root=str(root_a))
    (cb,) = userenvs.evaluate(cand, need, [], cache_only=True, root=str(root_b))
    assert ca.get("deferred") is True and cb["satisfies"] is True


def test_the_spec_probe_launches_isolated_without_site(monkeypatch, tmp_path):
    """结构守卫：运行之前的体检（用户的环境）以 `-I -S` 启动；内置 runtime 与 import 方式（运行之后）不变。"""
    seen = []

    def fake_run(argv, **kw):
        seen.append(list(argv))
        raise OSError("stop")

    monkeypatch.setattr(projectenv.subprocess, "run", fake_run)
    projectenv.probe_environment("/x/python", modules=("a",), import_mode="spec", project_root="/p")
    projectenv.probe_environment("/x/python", modules=("a",))
    spec_argv, import_argv = seen
    assert "-I" in spec_argv and "-S" in spec_argv
    assert "-I" not in import_argv and "-S" not in import_argv


@posix_only
@needs_worker
@pytest.mark.parametrize(
    "declared", [True, False], ids=["python-version-declared", "undeclared-conda"]
)
def test_an_external_env_with_the_project_installed_editable_is_not_started_before_run(
    tmp_path, monkeypatch, declared
):
    """Codex 安全 #820 r4234643135：外部环境装了指向 `<项目>/src` 的 .pth，src 里的 sitecustomize.py 在解释器启动期执行。
    检查（plan_for / 决定 / offer）不得启动它的任何一种形态；点运行才体检。"""
    root, marker, py, base = _editable_world(tmp_path, sitecustomize=True)
    if declared:
        (root / ".python-version").write_text("lab\n", "utf-8")
        monkeypatch.setattr(userenvs, "_pyenv_version_dirs", lambda: [str(base)])
    else:
        monkeypatch.setattr(userenvs, "_conda_prefixes", lambda *a, **k: [str(base / "lab")])
    userenvs.reset_cache()
    monkeypatch.setattr(engine_pool, "system_python_candidates", lambda: [])
    cands = deprepair.user_environment_candidates(str(root), "fig.py")
    assert any(
        os.path.realpath(c["python"]) and _env_root(c["python"]) == _env_root(py) for c in cands
    )
    preparation.plan_for(
        project_id="pj",
        project_root=str(root),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )
    deprepair.decide_environment_pinned(root, "fig.py", project_exec=False)
    plan = {"missing": [{"import_name": "payload", "distribution": "payload"}], "unknown": []}
    offer = deprepair.user_environment_offer(root, "fig.py", plan, "")
    assert not marker.exists(), "检查阶段启动了装着项目的外部环境（sitecustomize / .pth）"
    assert projectenv.remembered_record(root) is None
    lab = [e for e in offer if _env_root(e["python"]) == _env_root(py)]
    assert lab and all(e.get("deferred") for e in lab)
    # 点了运行：同意体检，真启动它
    deprepair.decide_environment(root, "fig.py")
    assert marker.exists()


@posix_only
@needs_worker
def test_the_isolated_probe_only_sees_the_user_site_when_the_interpreter_would(
    tmp_path, monkeypatch
):
    """Codex #820 r4234766732：`pip install --user` 的包只在这个解释器正常启动会启用用户 site 时才算数——venv（不含系统
    site-packages）默认关，非 venv 的解释器开。"""
    user_base = tmp_path / "ubase"
    ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_dir = user_base / "lib" / ver / "site-packages"
    if sys.platform == "darwin":
        site_dir = user_base / "lib" / "python" / "site-packages"
    site_dir.mkdir(parents=True)
    (site_dir / "usermod_only.py").write_text("X = 1\n", "utf-8")
    monkeypatch.setenv("PYTHONUSERBASE", str(user_base))
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)

    base = tmp_path / "ext"
    base.mkdir()
    venv_py = envworld.real_venv(base, "lab", python=WORKER_PY)
    cfg = base / "lab" / "pyvenv.cfg"
    cfg.write_text(
        cfg.read_text("utf-8").replace(
            "include-system-site-packages = true", "include-system-site-packages = false"
        ),
        "utf-8",
    )
    spec = projectenv.probe_environment(
        venv_py, modules=("usermod_only",), import_mode="spec", project_root=str(tmp_path / "proj")
    )
    assert spec["modules_ok"] == {"usermod_only": False}, (
        "venv 的用户 site 默认关，--user 装的包不算"
    )
    # 非 venv 的解释器：用户 site 正常启用
    plain = projectenv.probe_environment(
        os.path.realpath(getattr(sys, "_base_executable", sys.executable)),
        modules=("usermod_only",),
        import_mode="spec",
        project_root=str(tmp_path / "proj"),
    )
    assert plain["modules_ok"] == {"usermod_only": True}


def _probe_helpers(os_module):
    """把体检进程里的 `_norm` / `_inside` / `_finder_defers` 原样取出来，在给定的 `os`（可以是 ntpath 冒充的）上跑——在 macOS /
    Linux 上也能模拟 Windows 的路径规则。"""
    import ast as _ast
    import re
    import types

    src = projectenv._PROBE_SRC
    start = src.index("def _norm(")
    end = src.index("def _prepare_isolated_path")
    ns = {"os": types.SimpleNamespace(path=os_module), "ast": _ast}
    exec(re.sub(r"\n{3,}", "\n\n", src[start:end]), ns)  # noqa: S102 — 只执行我们自己的源码片段
    return ns


def test_editable_finder_paths_are_normalised_before_the_project_check():
    """Codex #820 r4234882840：Windows 的 PEP 660 finder 把 MAPPING 写成双反斜杠、大小写不定的字符串字面量。ast 解析（从不执行）后
    规范化再用 commonpath 判，解析不了就延后。"""
    import ntpath

    h = _probe_helpers(ntpath)
    root = "C:\\Users\\Me\\Proj"
    inside = "MAPPING = {'payload': 'c:\\\\users\\\\me\\\\PROJ\\\\src\\\\payload'}\n"
    outside = "MAPPING = {'payload': 'D:\\\\elsewhere\\\\payload'}\nNAME = 'x'\n"
    sibling = "MAPPING = {'payload': 'C:\\\\Users\\\\Me\\\\Proj2\\\\src'}\n"
    assert h["_finder_defers"](inside, root) is True  # 双反斜杠 + 不同大小写
    assert h["_finder_defers"](outside, root) is False
    assert h["_finder_defers"](sibling, root) is False  # 前缀相同的兄弟目录不算在项目里
    assert h["_finder_defers"]("MAPPING = {'a': ", root) is True  # 解析不了 -> 延后
    assert (
        h["_finder_defers"]("MAPPING = {'a': 'rel/path'}\n", root) is True
    )  # 证明不了在项目外 -> 延后


@posix_only
@needs_worker
def test_a_finder_pointing_into_the_project_defers_and_one_pointing_out_does_not(tmp_path):
    root, marker, py, base = _editable_world(tmp_path)
    site_dir = next((base / "lab" / "lib").glob("python*")) / "site-packages"
    (site_dir / "proj.pth").unlink()
    (site_dir / "a_editable.pth").write_text(
        "import __editable___payload_finder; __editable___payload_finder.install()\n", "utf-8"
    )
    finder = site_dir / "__editable___payload_finder.py"

    def probe():
        return projectenv.probe_environment(
            py, modules=("payload",), import_mode="spec", project_root=str(root)
        )

    finder.write_text(f"MAPPING = {{'payload': {str(root / 'src' / 'payload')!r}}}\n", "utf-8")
    assert probe().get("deferred_env") is True
    finder.write_text("MAPPING = {'payload': '/nowhere/else/payload'}\n", "utf-8")
    assert not probe().get("deferred_env")
    finder.write_text("MAPPING = {'payload': ", "utf-8")  # 解析不了
    assert probe().get("deferred_env") is True
    assert not marker.exists()


@posix_only
@needs_worker
@pytest.mark.parametrize("spec", [False, True], ids=["import-mode", "spec-mode"])
def test_installing_a_missing_package_into_the_same_env_invalidates_the_cached_negative(
    tmp_path, spec
):
    """Codex #820 r4234882848：缓存里是"缺 X"，用户随后把 X 装进同一个环境（路径、环境代都没变）——重查必须看到。指纹 = 它的
    site-packages 目录 mtime，不起解释器。"""
    base = tmp_path / "ext"
    base.mkdir()
    py = envworld.real_venv(base, "lab", python=WORKER_PY)
    cand = [{"python": py, "source": userenvs.SOURCE_CONDA, "label": "lab"}]
    need = [{"import_name": "late_installed_pkg", "distribution": "late-installed-pkg"}]
    kw = {"root": str(tmp_path / "proj")} if spec else {}
    userenvs.reset_cache()
    (first,) = userenvs.evaluate(cand, need, [], **kw)
    assert first["satisfies"] is False and first["missing"] == ["late-installed-pkg"]
    (again,) = userenvs.evaluate(cand, need, [], **kw)
    assert again["satisfies"] is False  # 没变：命中缓存
    site_dir = next((base / "lab" / "lib").glob("python*")) / "site-packages"
    time.sleep(0.02)
    (site_dir / "late_installed_pkg").mkdir()
    (site_dir / "late_installed_pkg" / "__init__.py").write_text("", "utf-8")
    (now,) = userenvs.evaluate(cand, need, [], **kw)
    assert now["satisfies"] is True, "装进同一个环境之后，旧的『缺』仍被缓存命中"


class _FakeReparse:
    """lstat 结果的替身：Windows junction（reparse point，tag = MOUNT_POINT，name-surrogate 位）。"""

    def __init__(self, real):
        self._real = real
        self.st_mode = real.st_mode & ~0o170000 | 0o040000  # 目录，不是符号链接
        self.st_file_attributes = 0x400 | 0x10
        self.st_reparse_tag = 0xA0000003

    def __getattr__(self, name):
        return getattr(self._real, name)


@posix_only
@pytest.mark.parametrize("flavour", ["symlink", "junction"])
def test_discovery_never_follows_a_redirect_inside_the_project(tmp_path, monkeypatch, flavour):
    """Codex 安全 #820 r4234904358：项目里的符号链接 / Windows junction 可以把"看着像本地"的线索指到攻击者的共享，任何跟随的
    stat / is_file / realpath 都会发出网络认证。准备会话的发现（不是导入即扫描）也先逐级 lstat、被重定向的整条丢弃。"""
    outside = tmp_path / "attacker"
    (outside / "bin").mkdir(parents=True)
    exe = outside / "bin" / "python"
    exe.write_text("#!/bin/sh\n", "utf-8")
    exe.chmod(0o755)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.py").write_text("import json\n", "utf-8")
    link = root / "linkdir"
    link.symlink_to(outside, target_is_directory=True)
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text(
        '{"python.defaultInterpreterPath": "${workspaceFolder}/linkdir/bin/python"}', "utf-8"
    )
    (root / ".venv").symlink_to(outside, target_is_directory=True)  # 项目 venv 目录自己也是重定向

    real_lstat = os.lstat
    if flavour == "junction":
        # 在 POSIX 上模拟 Windows：把 linkdir / .venv 的 lstat 说成 reparse point（不是符号链接）
        monkeypatch.setattr(
            os,
            "lstat",
            lambda p, *a, **k: (
                _FakeReparse(real_lstat(p, *a, **k))
                if os.fspath(p).rstrip("/").endswith(("linkdir", ".venv"))
                else real_lstat(p, *a, **k)
            ),
        )
    followed = []
    real_stat, real_realpath, real_isfile = os.stat, os.path.realpath, os.path.isfile

    def spy(fn):
        def wrapper(path, *a, **k):
            if os.fspath(path).startswith((str(link), str(root / ".venv"))):
                followed.append(os.fspath(path))
            return fn(path, *a, **k)

        return wrapper

    monkeypatch.setattr(os, "stat", spy(real_stat))
    monkeypatch.setattr(os.path, "realpath", spy(real_realpath))
    monkeypatch.setattr(os.path, "isfile", spy(real_isfile))
    monkeypatch.setattr(Path, "is_file", lambda self, **k: (spy(real_isfile)(str(self)), False)[1])

    cands = userenvs.discover(str(root), "fig.py", ask_login_shell=False)
    projs = projectenv.discover(str(root), "fig.py")
    assert not any(
        c["python"].startswith(str(link)) or c["source"] == userenvs.SOURCE_PROJECT_VENV
        for c in cands
    )
    assert projs == []
    assert followed == [], f"跟随了项目里的重定向: {followed[:3]}"


@posix_only
def test_a_user_site_install_invalidates_the_cached_negative(tmp_path, monkeypatch):
    """Codex #820 r4235000684：指纹用的是体检进程自己放进 sys.path 的那份目录表——含用户 site（目录原本不存在也算）。"""
    user_base = tmp_path / "ubase"
    monkeypatch.setenv("PYTHONUSERBASE", str(user_base))
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
    py = os.path.realpath(
        getattr(sys, "_base_executable", sys.executable)
    )  # 非 venv 的解释器：用户 site 会启用
    userenvs.reset_cache()
    root = str(tmp_path / "proj")

    def probe():
        return userenvs._probe(py, ("late_user_pkg",), mode="spec", root=root)["modules_ok"]

    assert probe() == {"late_user_pkg": False}
    assert probe() == {"late_user_pkg": False}  # 没变：命中缓存
    import subprocess as _sp

    user_site = _sp.run(
        [py, "-c", "import site;print(site.getusersitepackages())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    os.makedirs(user_site, exist_ok=True)  # `pip install --user` 先建目录、再放包
    (Path(user_site) / "late_user_pkg.py").write_text("X = 1\n", "utf-8")
    assert probe() == {"late_user_pkg": True}, "--user 装进用户 site 之后，旧的『缺』仍被缓存命中"


def test_the_spec_probe_source_has_no_import_of_requested_modules():
    """结构守卫：`spec` 方式的取证函数里不出现 `__import__` / `import_module`（点号名的 find_spec 也只问顶层名）。"""
    src = projectenv._PROBE_SRC
    start = src.index("def _spec_state")
    body = src[start : src.index("def _import_state")]
    assert "__import__" not in body and "import_module" not in body
    assert 'name.split(".")[0]' in body


@posix_only
@needs_worker
def test_check_defers_imports_that_resolve_into_the_project_and_run_is_the_consent(
    tmp_path, monkeypatch
):
    root, marker, py, base = _editable_world(tmp_path)
    monkeypatch.setattr(userenvs, "_conda_prefixes", lambda *a, **k: [str(base / "lab")])
    userenvs.reset_cache()
    monkeypatch.setattr(engine_pool, "system_python_candidates", lambda: [])
    plan = preparation.plan_for(
        project_id="pj",
        project_root=str(root),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )
    deprepair.decide_environment_pinned(root, "fig.py", project_exec=False)
    assert not marker.exists(), "检查阶段执行了项目里的代码"
    assert projectenv.remembered_record(root) is None
    del plan
    # 点了运行：同意体检，import 它（项目代码在这时才跑）
    deprepair.decide_environment(root, "fig.py")
    assert marker.exists()


@needs_worker
def test_a_rebuilt_environment_is_redetected_said_out_loud_and_old_actions_stop_working(
    client, opened
):
    """用户确认过的 A 在同一路径被重建：检测模式不停下来问，作废它重新检测（这里重新检测到的就是重建后的那个）；
    报告说出「换了，因为被重建」，修订加一，旧修订上的动作被拒，确认之前做的计划不会拿去跑新环境。"""
    proj = opened([], name="rebuilt")
    venv_python = envworld.real_venv(proj, ".venv", python=WORKER_PY)
    assert projectenv.remember(proj, venv_python, automatic=False, trigger="user_selected")
    first = _create(client, {"script": "figure.py"})
    assert first["phase"] == "ready_to_run", first
    assert (
        first["environment"]["decided_by"] == "user" and first["environment"]["kind"] == "project"
    )
    plan = preparation.plan_for(
        project_id="pj-x",
        project_root=str(proj),
        asset_id="",
        stem="",
        script="figure.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )

    rebuild_venv(proj, ".venv", python=WORKER_PY)
    engine_pool._project_python_ok.clear()

    # 旧计划：环境换了代，一行用户代码都不跑
    assert preparation.stale_reason(plan) == (preparation.STALE_ENVIRONMENT, {})
    recheck = next(a for a in first["actions"] if a["kind"] == "recheck")
    redo = client.post(
        f"{SESSIONS}/{first['session_id']}/actions",
        json={"action_id": recheck["id"], "expected_config_revision": first["config_revision"]},
    )
    assert redo.status_code == 200, redo.get_json()
    after = _get(client, first["session_id"])
    assert after["config_revision"] == first["config_revision"] + 1
    assert after["environment"]["switched"] is True
    assert after["environment"]["replaced"] == {"reason": "rebuilt"}
    # 重新检查（运行之前）不起重建后的项目 venv：作废了旧决定，新决定等用户点「运行」那一下才体检、采用
    assert after["environment"]["decided_by"] != "auto"
    assert deprepair.decide_environment(proj, "figure.py")["python"] == venv_python
    record = projectenv.remembered_record(proj)
    assert record["trigger"] == projectenv.TRIGGER_AUTO_DETECTED
    assert record["generation"] == projectenv.environment_generation(venv_python)
    stale_run = client.post(
        f"{SESSIONS}/{first['session_id']}/actions",
        json={
            "action_id": next(a for a in first["actions"] if a["kind"] == "run")["id"],
            "expected_config_revision": first["config_revision"],
        },
    )
    assert stale_run.status_code in (404, 409)  # 旧修订上的 run：动作已撤 / 修订对不上，都不执行
    assert stale_run.get_json()["code"] in (
        prepsession.ERROR_ACTION_UNKNOWN,
        prepsession.ERROR_REVISION_CHANGED,
    )


def test_the_confirmation_mode_still_asks_and_detection_does_not(tmp_path, monkeypatch):
    """同一份事实，两种模式：确认模式下项目 venv 线索 = 要用户先选；检测模式下不问（由检测决定）。"""
    root = tmp_path / "p"
    root.mkdir()
    (root / "fig.py").write_text("import matplotlib\n", "utf-8")
    _touch(root / ".venv" / "bin" / "python")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /x\n", "utf-8")

    assert envadvice.recommend(root, "fig.py")["decision"]["needs_decision"] is False
    monkeypatch.setenv("TAVOTTO_ENV_ADOPTION", "confirm")
    assert envadvice.recommend(root, "fig.py")["decision"]["needs_decision"] is True
    assert os.environ["TAVOTTO_ENV_ADOPTION"] == "confirm"
