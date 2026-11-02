"""首开环境决定里互不依赖的探测并发跑（`pool.run_probes_concurrently`）。

看护：
1. 首次（缓存未命中）并发：总耗时 ≈ 最长的那个，不是总和；
2. 结果与异常和串行逐项对得上（最靠前的失败优先、`CaptureDeferred` 原样抛出）；
3. 缓存命中 / 池锁被持 / `no_capture` 里：不起任何线程；
4. 子段 `probe_*_ms` 属于闭集、只在真探测时出现，并发线程里的也记进调用线程的分段表。
"""

from __future__ import annotations

import threading
import time

import pytest

from tavotto.engine import depplan, deprepair, launcher, pool, userenvs

NAP = 0.3


@pytest.fixture
def no_threads(monkeypatch):
    """任何 `tavotto-probe` 线程被起都当场失败。"""
    real = threading.Thread.start

    def start(self, *a, **k):
        assert self.name != "tavotto-probe", "不该起探测线程"
        return real(self, *a, **k)

    monkeypatch.setattr(threading.Thread, "start", start)


def _nap(value):
    def fn():
        time.sleep(NAP)
        return value

    return fn


def test_runs_concurrently_and_keeps_order():
    t0 = time.perf_counter()
    got = pool.run_probes_concurrently(_nap("a"), _nap("b"), _nap("c"))
    elapsed = time.perf_counter() - t0
    assert got == ("a", "b", "c")
    assert elapsed < NAP * 2  # 串行 = 0.9s；并发 ≈ 0.3s


def test_first_failure_in_order_wins_and_all_finish():
    ran = []

    def slow_ok():
        time.sleep(NAP)
        ran.append("slow")
        return 1

    def first():
        raise ValueError("first")

    def second():
        raise KeyError("second")

    with pytest.raises(ValueError):
        pool.run_probes_concurrently(first, second, slow_ok)
    assert ran == ["slow"]  # 全部跑完才抛，不留悬空线程
    with pytest.raises(KeyError):
        pool.run_probes_concurrently(slow_ok, second)


def test_capture_deferred_and_launcher_unavailable_propagate_unchanged():
    exc = launcher.CaptureDeferred("x")
    with pytest.raises(launcher.CaptureDeferred) as info:
        pool.run_probes_concurrently(lambda: 1, lambda: (_ for _ in ()).throw(exc))
    assert info.value is exc
    exc2 = launcher.LauncherUnavailable("y")
    with pytest.raises(launcher.LauncherUnavailable) as info:
        pool.run_probes_concurrently(lambda: (_ for _ in ()).throw(exc2), _nap(1))
    assert info.value is exc2


def test_serial_fallbacks_start_no_thread(no_threads):
    assert pool.run_probes_concurrently(lambda: 1) == (1,)
    with launcher.no_capture():
        assert pool.run_probes_concurrently(lambda: 1, lambda: 2) == (1, 2)
    with pool._lock:
        assert pool.run_probes_concurrently(lambda: 1, lambda: 2) == (1, 2)


def _managed_env(monkeypatch, *, facts_cached: bool, base_cached: bool):
    monkeypatch.setattr(deprepair.managedenv, "python_of", lambda root: "")
    monkeypatch.setattr(depplan, "facts_cached", lambda py: facts_cached)
    monkeypatch.setattr(deprepair, "_base_python_cached", lambda: base_cached)
    monkeypatch.setattr(
        depplan, "target_facts", lambda py, use_cache=True: (time.sleep(NAP), "F")[1]
    )
    monkeypatch.setattr(deprepair, "base_python", lambda: (time.sleep(NAP), "/base")[1])
    monkeypatch.setattr(depplan, "fresh_venv_facts", lambda base, **k: ("fresh", base))
    monkeypatch.setattr(depplan, "adapter_distributions", lambda: ())


def test_facts_and_base_python_probe_concurrently_on_first_miss(monkeypatch):
    _managed_env(monkeypatch, facts_cached=False, base_cached=False)
    t0 = time.perf_counter()
    got = deprepair._facts_for_inner(deprepair.TARGET_MANAGED, "/py", "/root")
    assert time.perf_counter() - t0 < NAP * 1.7
    assert got == ("F", ("fresh", "/base"), "/py")  # 与串行同一结果


def test_cache_hits_stay_serial_without_threads(monkeypatch, no_threads):
    _managed_env(monkeypatch, facts_cached=True, base_cached=False)
    assert deprepair._facts_for_inner(deprepair.TARGET_MANAGED, "/py", "/root")[0] == "F"
    _managed_env(monkeypatch, facts_cached=False, base_cached=True)
    assert deprepair._facts_for_inner(deprepair.TARGET_MANAGED, "/py", "/root")[0] == "F"
    # 非受管目标不问基础解释器：单个探测，没有可并发的
    _managed_env(monkeypatch, facts_cached=False, base_cached=False)
    got = deprepair._facts_for_inner("user_venv", "/py", "/root")
    assert got == ("F", None, "/py")


def test_facts_failure_wins_over_base_python(monkeypatch):
    _managed_env(monkeypatch, facts_cached=False, base_cached=False)

    def boom(py, use_cache=True):
        raise launcher.LauncherUnavailable("activation")

    monkeypatch.setattr(depplan, "target_facts", boom)
    with pytest.raises(deprepair.RepairError) as info:
        deprepair._facts_for(deprepair.TARGET_MANAGED, "/py", "/root")
    assert info.value.code == launcher.UNAVAILABLE_CODE


def _plan_env(monkeypatch, *, silent=True, needs_ask=True):
    calls = []
    monkeypatch.setattr(deprepair.projectenv, "silent_adoption_enabled", lambda: silent)
    monkeypatch.setattr(userenvs, "login_shell_needs_ask", lambda: needs_ask)
    monkeypatch.setattr(
        userenvs, "login_shell_pythons", lambda: (time.sleep(NAP), calls.append("shell"))[0]
    )
    monkeypatch.setattr(
        deprepair, "joint_plan_for", lambda root, script: (time.sleep(NAP), ("plan",))[1]
    )
    return calls


def test_login_shell_warmed_concurrently_with_joint_plan(monkeypatch):
    calls = _plan_env(monkeypatch)
    t0 = time.perf_counter()
    assert deprepair._joint_plan_warming_login_shell("/r", "s.py") == ("plan",)
    assert time.perf_counter() - t0 < NAP * 1.7
    assert calls == ["shell"]


@pytest.mark.parametrize("silent,needs_ask", [(False, True), (True, False)])
def test_login_shell_not_warmed_when_unneeded(monkeypatch, no_threads, silent, needs_ask):
    calls = _plan_env(monkeypatch, silent=silent, needs_ask=needs_ask)
    assert deprepair._joint_plan_warming_login_shell("/r", "s.py") == ("plan",)
    assert calls == []


def test_joint_plan_error_propagates_even_when_shell_warmed(monkeypatch):
    _plan_env(monkeypatch)

    def bad(root, script):
        raise pool.WorkerError("no python", code="no_worker_python")

    monkeypatch.setattr(deprepair, "joint_plan_for", bad)
    with pytest.raises(pool.WorkerError):
        deprepair._joint_plan_warming_login_shell("/r", "s.py")


def test_subsegments_are_closed_set_and_only_on_real_probe(monkeypatch):
    sub = {k for k in pool.ACQUIRE_SEGMENTS if k.startswith("probe_")}
    assert sub == {
        "probe_matplotlib_ms",
        "probe_facts_ms",
        "probe_base_python_ms",
        "probe_login_shell_ms",
        "probe_user_envs_ms",
    }
    # 未命中：两个探测都记，并发线程里的也记进调用线程的分段表
    _managed_env(monkeypatch, facts_cached=False, base_cached=False)
    pool._TRACE.cur = cur = {}
    try:
        deprepair._facts_for_inner(deprepair.TARGET_MANAGED, "/py", "/root")
    finally:
        pool._TRACE.cur = None
    assert set(cur) == {"probe_facts_ms", "probe_base_python_ms"}
    # 命中：不出现键
    _managed_env(monkeypatch, facts_cached=True, base_cached=True)
    pool._TRACE.cur = cur = {}
    try:
        deprepair._facts_for_inner(deprepair.TARGET_MANAGED, "/py", "/root")
    finally:
        pool._TRACE.cur = None
    assert cur == {}


def test_has_matplotlib_records_its_subsegment(monkeypatch):
    class Done:
        returncode = 0

    monkeypatch.setattr(pool.subprocess, "run", lambda *a, **k: Done())
    monkeypatch.setattr(pool.launcher_mod, "activate_env", lambda py, env, timeout=None: env)
    pool._TRACE.cur = cur = {}
    try:
        assert pool._has_matplotlib("/py") is True
    finally:
        pool._TRACE.cur = None
    assert "probe_matplotlib_ms" in cur
