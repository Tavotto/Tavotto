"""重新运行（试运行）必须同步退役旧会话，不能让 workerd 把还没关掉的旧会话复用给新的一次。

背景（#815 合并组 windows-exe-smoke 的 `script-input.spec`）：workerd 对同一份 spawn 规格复用活着的会话
（`by_hash`）。试运行原先用异步 `pool.invalidate(...)`，旧会话的关停还没落地新的就开了——拿到旧会话，它的
`build` 命中「已建好」直接回缓存：脚本一行没重跑，`input()` 不再被问，「已用上次的答案」的轻提示也不会出。
Windows 打包产物上关停更慢、窗口更大，所以只红在那里。判据的主语：**重跑**要的是「新的一次执行」。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool, probe


@pytest.fixture
def script_dir(tmp_path):
    (tmp_path / "pick.py").write_text("print('x')\n", encoding="utf-8")
    return tmp_path


def test_rerun_retires_old_session_synchronously_before_building(script_dir, monkeypatch):
    calls: list[tuple] = []

    def fake_invalidate(script, figures_dir=None, run=None, *, only_run=False, force=False, **kw):
        calls.append(("invalidate", force, only_run))
        calls.append(("spare", kw.get("spare_unbuilt")))

    def fake_build(script, figures_dir, entry, **kw):
        calls.append(("build",))
        raise pool.WorkerError("stop here", code="worker_crashed")

    monkeypatch.setattr(pool, "invalidate", fake_invalidate)
    monkeypatch.setattr(pool, "build", fake_build)
    probe.probe(script_dir, "pick.py", entries=["__main__"])
    # 第一次作废（开新会话之前）必须是 force=True：同步关停并确认，之后才 build
    assert calls[0] == ("invalidate", True, True)
    # 同键上别人刚取到、还没跑完第一次 build 的会话不算「旧会话」（TVD-QMJV）
    assert calls[1] == ("spare", True)
    assert calls[2] == ("build",)


def test_unconfirmed_retirement_is_reported_and_never_builds(script_dir, monkeypatch):
    built: list[str] = []

    def refuse(script, figures_dir=None, run=None, *, only_run=False, force=False, **kw):
        if force:
            raise pool.WorkerError("无法确认旧渲染会话已关闭，请重试", code="session_dead")

    monkeypatch.setattr(pool, "invalidate", refuse)
    monkeypatch.setattr(pool, "build", lambda *a, **k: built.append("x"))
    result = probe.probe(script_dir, "pick.py", entries=["__main__"])
    assert built == []  # 旧会话没确认关掉就不开新的（否则又是复用旧会话、假装重跑过）
    assert result["stems"] == []
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# TVD-QMJV：试运行的强制作废不得杀掉另一个调用方刚取到、还没 build 的会话
# ---------------------------------------------------------------------------
class _FakeWorker:
    """池里的会话替身：只有 `invalidate` 会碰的那几个属性。"""

    def __init__(self, *, built: bool, build_failed: bool = False, alive: bool = True):
        self.built = built
        self.build_failed = build_failed
        self._alive = alive
        self.killed = 0

    def alive(self):
        return self._alive

    def force_kill(self):
        self.killed += 1
        self._alive = False
        return True


@pytest.fixture
def bare_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(pool, "_pending_retirements", {})
    key = pool._worker_key(str(tmp_path), "pick.py", None, None)
    return tmp_path, key


@pytest.mark.parametrize(
    ("worker", "retired"),
    [
        (_FakeWorker(built=False), False),  # 另一个调用方刚取到 / 正在 build：不碰
        (_FakeWorker(built=True), True),  # 上一次结果：这才是「旧会话」
        (_FakeWorker(built=False, build_failed=True), True),
        (_FakeWorker(built=False, alive=False), True),
    ],
    ids=["unbuilt-in-use", "built", "build-failed", "dead"],
)
def test_spare_unbuilt_only_spares_a_live_session_that_has_never_finished_a_build(
    bare_pool, worker, retired
):
    figs, key = bare_pool
    pool._workers[key] = worker
    pool.invalidate("pick.py", str(figs), None, only_run=True, force=True, spare_unbuilt=True)
    assert (worker.killed == 1) is retired
    assert (key not in pool._workers) is retired


def test_without_spare_unbuilt_a_forced_invalidate_still_kills_an_unbuilt_session(bare_pool):
    """准备会话的显式重跑（`force_rebuild`）与写回后的退役不带 `spare_unbuilt`：行为不变。"""
    figs, key = bare_pool
    worker = _FakeWorker(built=False)
    pool._workers[key] = worker
    pool.invalidate("pick.py", str(figs), None, only_run=True, force=True)
    assert worker.killed == 1


def test_probe_waiting_on_the_pool_lock_does_not_kill_the_session_it_was_waiting_for(
    bare_pool, monkeypatch
):
    """现场（TVD-QMJV）：A 持池锁起会话（首次建字体缓存，握手十来秒），B 的试运行在
    `invalidate` 的池锁上排队；A 放锁、把新会话登记进池，B 立刻进来——旧实现把它 force-kill，
    A 的第一条 build 就收到 `unknown_session`。判据的主语：B 进锁那一刻池里那条会话的状态。"""
    import threading

    figs, key = bare_pool
    worker = _FakeWorker(built=False)
    with pool._lock:  # A 正在起会话
        t = threading.Thread(
            target=pool.invalidate,
            args=("pick.py", str(figs), None),
            kwargs={"only_run": True, "force": True, "spare_unbuilt": True},
        )
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive(), "B 应当卡在池锁上"
        pool._workers[key] = worker  # A 登记完毕
    t.join(timeout=5)
    assert not t.is_alive()
    assert worker.killed == 0
    assert pool._workers[key] is worker
