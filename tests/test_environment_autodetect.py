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

import os
import subprocess
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
    monkeypatch.setattr(deprepair, "unknown_imports_missing", lambda plan, py: [])
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
    # 下一次检查：它仍能跑 → 不动、不再体检——哪怕项目里后来多了一个同样能跑的 venv（在用的 A 能跑，就不换成 B）
    venv = rig["cand"]("proj/.venv", userenvs.SOURCE_PROJECT_VENV)
    rig["satisfying"].add(venv)
    rig["probed"].clear()
    assert deprepair.decide_environment(rig["root"], "fig.py") is None
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
        lambda r, s: pool_decision(raced["conda"]),
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

    report = _create(client, {"script": "figure.py"})

    assert report["phase"] == "ready_to_run", report
    assert [r for r in report["requirements"] if r.get("blocking", True)] == []
    assert "run" in _kinds(report) and "prepare_dependencies" not in _kinds(report)
    assert report["environment"] == {
        "mode": "detect",
        "kind": "project",
        "decided_by": "auto",
        "switched": True,
        "replaced": None,
    }
    env_check = next(c for c in report["checks"] if c["id"] == "environment")
    assert env_check["status"] == "ok"
    record = projectenv.remembered_record(proj)
    assert record["trigger"] == projectenv.TRIGGER_AUTO_DETECTED and record["generation"]

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

    report = _create(client, {"script": "figure.py"})

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
    assert after["phase"] == "ready_to_run", after
    assert after["config_revision"] == first["config_revision"] + 1
    assert after["environment"]["switched"] is True
    assert after["environment"]["replaced"] == {"reason": "rebuilt"}
    assert after["environment"]["decided_by"] == "auto"
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
