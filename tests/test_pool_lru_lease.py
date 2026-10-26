"""池 LRU 淘汰不许收掉「调用方刚取到、还没发请求」的会话（冷启动 4 张图同时重渲的失败根因）。

现象：4 张图同时渲染，`MAX_ALIVE=3`，每次有一张报
「渲染进程退出了：自己以退出码 0 结束了」——worker.log 里只有字体缓存 / findfont 的几行，没有 Traceback。
根因不是字体缓存：第 4 个 `acquire` 淘汰最久未用的那条，淘汰线程的优雅 `shutdown()`
与该条调用方的第一条请求在 `w.lock` 上竞争，淘汰一赢，worker 按 shutdown 正常退出（退出码恒为 0），
调用方随后写不进管道，被读成「worker 自己以退出码 0 结束」。

这里用真 worker 进程（只起、不 build，不 import matplotlib 之外的重东西），确定性地复现那条时序：
先取 4 个会话（调用方都还没发请求），再看谁被淘汰。
"""

import time

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib.pyplot as plt

def main():
    fig, ax = plt.subplots(figsize=(2, 1.5))
    ax.plot([0, 1], [0, 1])
    fig.savefig("Fig.pdf")
"""


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    for i in range(4):
        (d / f"f{i}.py").write_text(SCRIPT, encoding="utf-8")
    try:
        yield d
    finally:
        pool.shutdown_all(str(d), wait=True)


def test_lru_does_not_evict_a_session_whose_caller_has_not_requested_yet(project, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    workers = [pool.get(f"f{i}.py", str(project), "main") for i in range(4)]
    # 4 个调用方都握着会话、都还没发请求：谁也不许被淘汰（上限是软的，租约期满后下一次取会话再收）
    assert all(w.alive() for w in workers)
    assert not any(w._retired for w in workers)


def test_lease_ends_at_first_request_so_the_cap_still_holds(project, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    workers = [pool.get(f"f{i}.py", str(project), "main") for i in range(3)]
    for w in workers:
        w.request({"cmd": "ping"}, 30)  # 第一条请求拿到锁：租约结束
        assert w._lease_until == 0.0
    fourth = pool.get("f3.py", str(project), "main")
    everyone = (*workers, fourth)
    deadline = time.monotonic() + 30  # 淘汰在后台线程里优雅关停
    while sum(w.alive() for w in everyone) > pool.MAX_ALIVE and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sum(w.alive() for w in everyone) == pool.MAX_ALIVE
    assert fourth.alive()
    assert workers[0]._retired  # 最久未用的那条


def test_lease_is_bounded(project, monkeypatch):
    """取了会话却从不发请求的调用方，不能让会话永远淘汰不掉。"""
    monkeypatch.setattr(pool, "MAX_ALIVE", 1)
    monkeypatch.setattr(pool, "ACQUIRE_LEASE", 0.0)
    first = pool.get("f0.py", str(project), "main")
    second = pool.get("f1.py", str(project), "main")
    assert second.alive()
    assert first._retired


def test_request_on_a_session_the_pool_retired_is_not_blamed_on_the_worker(project):
    w = pool.get("f0.py", str(project), "main")
    w.shutdown()  # 池自己收掉了它；调用方手里还是老引用
    with pytest.raises(pool.WorkerError) as caught:
        w.ensure_built()
    err = caught.value
    assert err.code == "session_dead"
    assert err.extra.get("retired") is True
    assert "退出码 0" not in str(err)


def _retired_error():
    err = pool.WorkerError("retired", "", code="session_dead")
    err.extra = {"retired": True}
    return err


class _Stub:
    script_name = "f.py"


def _attempt_rig(monkeypatch):
    from tavotto import app as app_mod

    fetched = []

    def refetch(rel_id, **kw):
        fetched.append(rel_id)
        return _Stub(), "S"

    monkeypatch.setattr(app_mod, "_engine_worker", refetch)
    monkeypatch.setattr(app_mod, "_admit_artifact", lambda *a: None)
    monkeypatch.setattr(app_mod, "_assert_artifact_current", lambda *a: None)
    monkeypatch.setattr(app_mod, "_svg_execution_result", lambda w: {})
    monkeypatch.setattr(app_mod, "_switched_to_project_env", lambda w, e: False)
    return app_mod, fetched


def test_engine_attempt_retries_once_when_the_session_was_retired_unsent(monkeypatch):
    app_mod, fetched = _attempt_rig(monkeypatch)
    calls = []

    def action(worker, stem):
        calls.append(worker)
        if len(calls) == 1:
            raise _retired_error()
        return {"ok": True}

    _, _, result = app_mod._engine_attempt("id", _Stub(), "S", action)
    assert result == {"ok": True}
    assert len(calls) == 2 and len(fetched) == 1


def test_engine_attempt_does_not_retry_a_real_session_death(monkeypatch):
    app_mod, fetched = _attempt_rig(monkeypatch)
    dead = pool.WorkerError("died", "", code="session_dead")  # 没有 retired 标记：真崩溃

    def action(worker, stem):
        raise dead

    with pytest.raises(pool.WorkerError) as caught:
        app_mod._engine_attempt("id", _Stub(), "S", action)
    assert caught.value is dead and not fetched


def test_engine_attempt_retries_only_once(monkeypatch):
    app_mod, fetched = _attempt_rig(monkeypatch)

    def action(worker, stem):
        raise _retired_error()

    with pytest.raises(pool.WorkerError):
        app_mod._engine_attempt("id", _Stub(), "S", action)
    assert len(fetched) == 1
