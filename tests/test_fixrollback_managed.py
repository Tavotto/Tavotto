"""change_python 的回滚与受管环境旧代（Codex #1383 P2）：切到新一代后 `retire_unused` 会删没人占着的旧代，
回滚就会把项目指回一个已删除的解释器却报成功。

判据的主语：**磁盘上旧代的解释器文件在不在**、**manifest 的 active 指向谁**、**`rollback` 的返回码**、**`envlease` 上有没有租约**。
真实的 `managedenv`（临时数据目录里的 manifest + 假解释器文件）、真实的 `snapshots` / `rollback`、真实的 `retire_unused`
（`deprepair._generation_in_use` 做判据）；换 Python 的执行器是假的（模拟 `apply_python_change` 的「切 active + 清旧代」），不起真实环境。
"""

from __future__ import annotations

import threading
import time

import pytest

from tavotto.engine import deprepair, envlease, fixsession, managedenv
from tavotto.engine.agent_runner.wires import ToolCall, Turn
from tavotto.engine.fixtools import REGISTRY, AuditLog, FixContext, run_tool


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    envlease.reset_for_tests()
    fixsession.SESSIONS.reset_for_tests()
    deprepair._attempted.clear() if hasattr(deprepair, "_attempted") else None
    yield type("Box", (), {"home": home, "proj": proj, "tmp": tmp_path})
    fixsession.SESSIONS.reset_for_tests()
    envlease.reset_for_tests()


def make_generation(project, name: str, *, active: bool = False) -> str:
    managedenv.register_generation(
        project,
        name,
        requirements=[],
        constraints=[],
        identity=(name * 12),
        base_python="/base/python",
    )
    py = managedenv.generation_python(project, name)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_bytes(b"")
    if active:
        managedenv.activate(project, name, python_version="3.11")
    return str(py)


class _Joint:
    plan_id = "joint-1"
    impact_digest = "digest-1"


@pytest.fixture
def real_transaction(monkeypatch):
    """换代事务走**真实的** `pyversionfix.apply_python_change` → `deprepair._run_generation` → `pool.mutating_environment` /
    `envlease.mutating` → `managedenv.retire_unused(in_use=_generation_in_use)`。只有最底层的 pip / 建 venv / 自检 / 联合计划的
    构造用替身（`prepare` 只是把一个最小的 `_GenerationJob` 交给真的 `_run_generation`）。"""
    from tavotto.engine import projectenv, pyversionfix

    def create_venv(project, generation, base):
        py = managedenv.generation_python(project, generation)
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"")
        return True, ""

    monkeypatch.setattr(managedenv, "create_generation_venv", create_venv)
    monkeypatch.setattr(deprepair, "_run", lambda *a, **k: (0, ""))
    monkeypatch.setattr(deprepair, "_run_pip_install", lambda *a, **k: ("", ""))
    monkeypatch.setattr(deprepair, "_verify_imports", lambda *a, **k: None)
    monkeypatch.setattr(deprepair, "_verify_distributions", lambda *a, **k: [])
    monkeypatch.setattr(deprepair, "worker_self_test", lambda python: {"ok": True})
    monkeypatch.setattr(deprepair, "_versions_of", lambda *a, **k: {})
    monkeypatch.setattr(deprepair, "_freeze", lambda python: "")
    monkeypatch.setattr(deprepair, "_private_runtime_of", lambda base: "")
    monkeypatch.setattr(deprepair.privatepython, "retire_unused", lambda **k: None)
    monkeypatch.setattr(managedenv, "python_version_of", lambda python: "3.12")
    monkeypatch.setattr(projectenv, "probe_environment", lambda python, *a, **k: {"ok": False})
    monkeypatch.setattr(deprepair, "create_joint_plan", lambda *a, **k: _Joint())

    def prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        project = str(_PROJECT["root"])
        job = deprepair._GenerationJob(
            progress_id="pid-1",
            project=project,
            script="",
            delta=(),
            constraints=(),
            hashes={},
            require_hashes=False,
            needed_imports=(),
            record=(),
            reason="test",
            identity=_PROJECT.get("identity", "b" * 12),
            emit=lambda state, **kw: kw.get("result") or {},
            on_log=lambda text: None,
        )
        return deprepair._run_generation(job, threading.Event())

    monkeypatch.setattr(deprepair, "prepare", prepare)
    return pyversionfix


_PROJECT: dict = {}


def real_changer(box, pyversionfix):
    """`change_python` 的执行器：规划是手写的（不探测本机 Python），**执行**是真的 `apply_python_change`。"""
    _PROJECT["root"] = box.proj

    def changer(req):
        plan = pyversionfix.PythonChangePlan(
            from_version="3.11",
            to_version="3.12",
            base="system",
            download_bytes=0,
            code="project_env_unsupported_python",
            project=str(box.proj),
            script="fig.py",
            base_python="/sys/python3.12",
        )
        res = pyversionfix.apply_python_change(plan, None, threading.Event())
        if not res.get("ok"):
            return {"ok": False, "code": res.get("code") or "failed"}
        return {"ok": True, "python": str(managedenv.python_of(box.proj)), "to": "3.12"}

    return changer


def ctx_for(box, pyversionfix, **kw) -> FixContext:
    kw.setdefault("python_changer", real_changer(box, pyversionfix))
    return FixContext.for_fix_session(
        "s1", box.proj, home=box.home, case_insensitive=False, freezer=lambda _p: "", **kw
    )


def call(ctx, name, args):
    return run_tool(REGISTRY[name], ctx, args, AuditLog())


NEW = "gbbbbbbbbbbbb"  # `_GenerationJob.identity = "b" * 12` -> `g` + 前 12 位


def test_the_change_succeeds_with_the_retain_lease_and_the_old_generation_survives_retire(
    box, real_transaction
):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box, real_transaction)
    r = call(ctx, "change_python", {})
    assert not r.is_error, r.content  # 保留型租约不挡换代事务自己的 `envlease.mutating()`
    assert managedenv.active_generation(box.proj) == NEW
    assert managedenv.generation_python(
        box.proj, "ga"
    ).is_file()  # 真实的 retire_unused 认保留型：旧代没被清理
    assert envlease.retained_on(old) and not envlease.native_sessions_on(
        old
    )  # 是保留型，不是 native 占用


def test_without_the_lease_the_real_retire_deletes_the_old_generation(box, real_transaction):
    """对照：同一条真实路径，不加租约旧代就被清掉（证明上面的「没被清理」是租约的功劳，不是路径没跑）。"""
    make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box, real_transaction)
    ctx.leases = []
    from tavotto.engine.fixtools import change

    real = change._lease_current_environment
    change._lease_current_environment = lambda c: []  # type: ignore[assignment]
    try:
        assert not call(ctx, "change_python", {}).is_error
    finally:
        change._lease_current_environment = real  # type: ignore[assignment]
    assert not managedenv.generation_python(box.proj, "ga").is_file()


def test_rollback_points_the_project_back_at_the_old_generation(box, real_transaction):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box, real_transaction)
    assert not call(ctx, "change_python", {}).is_error
    snap = ctx.call_meta["snapshot"]
    r = call(ctx, "rollback", {"snapshot": snap})
    assert not r.is_error, r.content
    assert managedenv.active_generation(box.proj) == "ga"
    assert managedenv.python_of(box.proj) == old  # 解释器真实存在、可用（ready）
    assert "previous Tavotto environment" in r.content


def test_rollback_reports_target_gone_when_the_old_generation_was_deleted(box, real_transaction):
    make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box, real_transaction)
    assert not call(ctx, "change_python", {}).is_error
    snap = ctx.call_meta["snapshot"]
    managedenv.generation_python(box.proj, "ga").unlink()  # 人为删掉（用户清过数据目录之类）
    r = call(ctx, "rollback", {"snapshot": snap})
    assert r.is_error and r.code == "rollback_target_gone"
    assert managedenv.active_generation(box.proj) == NEW  # 没有动：不把项目指向一个不存在的解释器


def test_a_real_native_session_still_blocks_the_change(box, real_transaction):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box, real_transaction)
    envlease.acquire_native(old, "user-native-session")  # 用户自己的 native 会话占着旧代
    r = call(ctx, "change_python", {})
    assert r.is_error  # 换代事务被 `envlease.mutating()` 拒（environment_in_use_by_native_session）
    assert managedenv.active_generation(box.proj) == "ga" and ctx.leases == []
    assert not envlease.retained_on(old)  # 没换成：保留型租约立即放掉


def test_rollback_reports_target_gone_when_a_restored_setting_points_nowhere(box):
    from tavotto.engine import config

    config.set_project_settings(
        str(box.proj),
        {"environment": {"python": str(box.tmp / "no" / "python"), "automatic": False}},
    )
    ctx = FixContext.for_fix_session(
        "s1", box.proj, home=box.home, case_insensitive=False, freezer=lambda _p: ""
    )
    assert not call(ctx, "switch_execution", {"mode": "safe"}).is_error
    snap = ctx.call_meta["snapshot"]
    r = call(ctx, "rollback", {"snapshot": snap})
    assert r.is_error and r.code == "rollback_target_gone"


def test_a_failed_change_gives_the_lease_back(box, real_transaction):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box, real_transaction, python_changer=lambda req: {"ok": False, "code": "boom"})
    r = call(ctx, "change_python", {})
    assert r.is_error and ctx.leases == [] and not envlease.retained_on(old)


def test_the_lease_is_released_when_the_session_ends(box, real_transaction):
    old = make_generation(box.proj, "ga", active=True)
    model_steps = [("change_python", {}), ("finish", {"outcome": "not_fixed", "summary": "x"})]

    class Scripted:
        def complete(self, **_kw):
            if not model_steps:
                return Turn(text="done", stop="end")
            n, a = model_steps.pop(0)
            return Turn(tool_calls=[ToolCall("c", n, a)], stop="tool_use")

    inner = real_changer(box, real_transaction)

    def changer(req):
        out = inner(req)
        assert envlease.retained_on(old), "会话还在跑：租约应当还挂着"
        return out

    s = fixsession.SESSIONS.start(
        scope="pj",
        prep_session_id="p1",
        script="fig.py",
        project_root=box.proj,
        model=Scripted(),
        audit=AuditLog(),
        ctx_kwargs={
            "home": box.home,
            "case_insensitive": False,
            "python_changer": changer,
            "freezer": lambda _p: "",
        },
    )
    end = time.monotonic() + 15
    while s.state != fixsession.STATE_FINISHED and time.monotonic() < end:
        time.sleep(0.05)
    assert s.state == fixsession.STATE_FINISHED
    assert not envlease.retained_on(old)  # 会话结束，租约放了
    assert managedenv.generation_python(box.proj, "ga").is_file()


def test_a_failed_second_change_keeps_the_lease_an_earlier_change_needs(box, real_transaction):
    """同一会话：A → B 成功，再换一次失败，再换 B → C 成功。失败只撤销它自己新加的租约，A 的保留要留到会话收尾——
    否则第三次换代开头的 retire_unused 会删掉 A，第一步的快照就回滚不了（Codex #1383）。"""
    old = make_generation(box.proj, "ga", active=True)
    good = real_changer(box, real_transaction)
    outcomes = iter(["ok", "fail", "ok"])

    def changer(req):
        if next(outcomes) == "fail":
            return {"ok": False, "code": "boom"}
        return good(req)

    ctx = ctx_for(box, real_transaction, python_changer=changer)
    assert not call(ctx, "change_python", {}).is_error
    first_snap = ctx.call_meta["snapshot"]
    assert call(ctx, "change_python", {}).is_error
    assert envlease.retained_on(old), "失败的那次换代不能放掉 A 的保留"
    _PROJECT["identity"] = "c" * 12
    try:
        assert not call(ctx, "change_python", {}).is_error
    finally:
        _PROJECT.pop("identity", None)
    assert managedenv.generation_python(box.proj, "ga").is_file()
    r = call(ctx, "rollback", {"snapshot": first_snap})
    assert not r.is_error, r.content
    assert managedenv.active_generation(box.proj) == "ga"
    ctx.release_leases()
    assert not envlease.retained_on(old)
