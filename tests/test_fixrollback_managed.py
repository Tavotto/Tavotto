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


def switch_and_retire(project, to: str):
    """模拟 `apply_python_change` 的提交 + 事后清理：新一代 active，没人占着的旧代被 `retire_unused` 删掉。"""
    make_generation(project, to)
    managedenv.activate(project, to, python_version="3.12")
    managedenv.retire_unused(project, in_use=deprepair._generation_in_use)


def ctx_for(box, **kw) -> FixContext:
    def changer(req):
        switch_and_retire(box.proj, "gb")
        return {"ok": True, "python": str(managedenv.python_of(box.proj)), "to": "3.12"}

    kw.setdefault("python_changer", changer)
    return FixContext.for_fix_session(
        "s1", box.proj, home=box.home, case_insensitive=False, freezer=lambda _p: "", **kw
    )


def call(ctx, name, args):
    return run_tool(REGISTRY[name], ctx, args, AuditLog())


def test_without_a_lease_the_old_generation_is_gone_and_with_one_it_survives(box):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box)
    r = call(ctx, "change_python", {})
    assert not r.is_error, r.content
    assert managedenv.python_of(box.proj) != old  # 切到新一代了
    assert managedenv.generation_python(box.proj, "ga").is_file()  # 有租约：旧代没被清理
    assert envlease.native_sessions_on(old)  # 租约确实挂在旧代的解释器上


def test_rollback_points_the_project_back_at_the_old_generation(box):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box)
    assert not call(ctx, "change_python", {}).is_error
    snap = ctx.call_meta["snapshot"]
    assert managedenv.active_generation(box.proj) == "gb"
    r = call(ctx, "rollback", {"snapshot": snap})
    assert not r.is_error, r.content
    assert managedenv.active_generation(box.proj) == "ga"
    assert managedenv.python_of(box.proj) == old  # 解释器真实存在、可用（ready）
    assert "previous Tavotto environment" in r.content


def test_rollback_reports_target_gone_when_the_old_generation_was_deleted(box):
    make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box)
    assert not call(ctx, "change_python", {}).is_error
    snap = ctx.call_meta["snapshot"]
    managedenv.generation_python(box.proj, "ga").unlink()  # 人为删掉（用户清过数据目录之类）
    r = call(ctx, "rollback", {"snapshot": snap})
    assert r.is_error and r.code == "rollback_target_gone"
    assert managedenv.active_generation(box.proj) == "gb"  # 没有动：不把项目指向一个不存在的解释器


def test_rollback_reports_target_gone_when_a_restored_setting_points_nowhere(box):
    from tavotto.engine import config

    config.set_project_settings(
        str(box.proj),
        {"environment": {"python": str(box.tmp / "no" / "python"), "automatic": False}},
    )
    ctx = ctx_for(box)
    assert not call(ctx, "switch_execution", {"mode": "safe"}).is_error
    snap = ctx.call_meta["snapshot"]
    r = call(ctx, "rollback", {"snapshot": snap})
    assert r.is_error and r.code == "rollback_target_gone"


def test_a_failed_change_gives_the_lease_back(box):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box, python_changer=lambda req: {"ok": False, "code": "boom"})
    r = call(ctx, "change_python", {})
    assert r.is_error and ctx.leases == [] and not envlease.native_sessions_on(old)


def test_the_lease_is_released_when_the_session_ends(box):
    old = make_generation(box.proj, "ga", active=True)
    model_steps = [("change_python", {}), ("finish", {"outcome": "not_fixed", "summary": "x"})]

    class Scripted:
        def complete(self, **_kw):
            if not model_steps:
                return Turn(text="done", stop="end")
            n, a = model_steps.pop(0)
            return Turn(tool_calls=[ToolCall("c", n, a)], stop="tool_use")

    def changer(req):
        switch_and_retire(box.proj, "gb")
        assert envlease.native_sessions_on(old), "会话还在跑：租约应当还挂着"
        return {"ok": True, "python": str(managedenv.python_of(box.proj)), "to": "3.12"}

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
    assert not envlease.native_sessions_on(old)  # 会话结束，租约放了
    assert managedenv.generation_python(
        box.proj, "ga"
    ).is_file()  # 放了之后旧代才可能被清理（这里还没人清）


def test_a_busy_environment_stops_the_change_before_anything_is_switched(box):
    old = make_generation(box.proj, "ga", active=True)
    ctx = ctx_for(box)
    gate = threading.Event()
    started = threading.Event()

    def hold():
        with envlease.mutating("k-test", old):
            started.set()
            gate.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    assert started.wait(5)
    try:
        r = call(ctx, "change_python", {})
        assert (
            r.is_error and r.code == "env_busy" and managedenv.active_generation(box.proj) == "ga"
        )
    finally:
        gate.set()
        t.join(5)
