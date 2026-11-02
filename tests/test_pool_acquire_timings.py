"""取会话分段计时 + 渲染池健康摘要（TVM-Y37X：`worker_get_ms` 把等锁 / 淘汰 / 握手混成一个数）。

看护三件事：
1. 分段字段是**闭集**，且只有进过那一段才出现；
2. 计数器在冷启动（新建会话）与 LRU 淘汰时递增，环形记录有界；
3. 进诊断包（`detail-render-env.json` 的 `render_pool`）的摘要不含脚本名 / 路径，池锁被占时不卡住备包。
"""

from __future__ import annotations

import collections
import json
import sys
import threading

import pytest

from tavotto.engine import diagdetail, pool

HOT_ONLY = {"resolve_python_ms", "launcher_prewarm_ms", "pool_wait_ms"}


class Worker:
    def __init__(self, script="plot.py", entry="main"):
        self.script_name = script
        self.entry = entry
        self.python = sys.executable
        self.build_failed = False
        self.running = True
        self.last_used = 0

    def alive(self):
        return self.running

    def shutdown(self):
        self.running = False


def _new_worker(script, figures_dir, entry, **kw):
    with pool._seg("handshake_ms"):  # 真实构造函数里的同名段
        return Worker(script, entry)


@pytest.fixture
def isolated_pool(monkeypatch):
    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(pool, "_pending_retirements", {})
    monkeypatch.setattr(pool, "_last_acquired", threading.local())
    monkeypatch.setattr(pool, "_TRACE", threading.local())
    monkeypatch.setattr(pool, "_health", {"created": 0, "evicted": 0, "seq": 0})
    monkeypatch.setattr(pool, "_cold_starts", collections.deque(maxlen=pool.COLD_START_RING))
    monkeypatch.setattr(pool, "COLD_ACQUIRE_OBSERVER", None)
    monkeypatch.setattr(pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(pool, "ACQUIRE_LEASE", 0.0)
    monkeypatch.setattr(pool, "resolve_worker_python", lambda *a, **kw: (sys.executable, "test"))
    monkeypatch.setattr(pool, "_refuse_if_mutating", lambda *a: None)
    monkeypatch.setattr(pool.launcher_mod, "prewarm", lambda *a: None)
    monkeypatch.setattr(pool, "_schedule_prune", lambda: None)
    monkeypatch.setattr(pool, "generation_stale", lambda worker: False)
    monkeypatch.setattr(pool, "_remap_current", lambda *a: True)
    monkeypatch.setattr(pool, "_workdir_gate", lambda *a: None)
    monkeypatch.setattr(pool, "_new_worker", _new_worker)


def _timings(script, fd="/p"):
    mark = pool.acquire_mark()
    w, created = pool.acquire(script, fd, "main")
    return w, created, pool.acquire_timings(mark, w)


def test_cold_path_has_cold_segments_and_hot_path_does_not(isolated_pool):
    w, created, cold = _timings("a.py")
    assert created and "handshake_ms" in cold
    assert HOT_ONLY <= set(cold) <= set(pool.ACQUIRE_SEGMENTS)
    assert "evict_ms" not in cold  # 没淘汰就不是这一段

    w2, created2, hot = _timings("a.py")
    assert w2 is w and not created2
    assert set(hot) == HOT_ONLY
    assert all(isinstance(v, float) and v >= 0 for v in hot.values())


def test_timings_never_leak_from_an_earlier_or_other_acquire(isolated_pool):
    w, _, _ = _timings("a.py")
    mark = pool.acquire_mark()
    # 这次渲染没经过池（例如原生会话）：不许把上一次的段算到头上
    assert pool.acquire_timings(mark, w) == {}
    other, _ = pool.acquire("b.py", "/p", "main")
    assert pool.acquire_timings(mark, w) == {}  # 最近一次取到的不是 w
    assert pool.acquire_timings(mark, other) != {}


def test_counters_follow_cold_starts_and_lru_evictions(isolated_pool, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 2)
    for i, name in enumerate(["a.py", "b.py", "c.py"]):
        w, _ = pool.acquire(name, "/p", "main")
        w.last_used = i  # acquire 会写 time.time()；让 LRU 顺序确定
    pool.acquire("c.py", "/p", "main")  # 热命中：什么都不加
    snap = pool.health_snapshot()
    assert snap["max_alive"] == 2
    assert snap["sessions_created"] == 3
    assert snap["lru_evictions"] == 1
    assert snap["alive"] == 2 and snap["lock_busy"] is False
    assert [c["seq"] for c in snap["recent_cold_starts"]] == [1, 2, 3]
    assert {c["cause"] for c in snap["recent_cold_starts"]} == {"new"}
    # 淘汰发生在第三次冷启动里，它的分段里要有 evict_ms
    assert "evict_ms" in snap["recent_cold_starts"][-1]["timings"]
    assert "evict_ms" not in snap["recent_cold_starts"][0]["timings"]


def test_rebuild_cause_is_a_closed_code(isolated_pool):
    w, _ = pool.acquire("a.py", "/p", "main")
    w.running = False  # 已死 → 重建
    pool.acquire("a.py", "/p", "main")
    snap = pool.health_snapshot()
    assert [c["cause"] for c in snap["recent_cold_starts"]] == ["new", "dead"]


def test_cold_start_ring_is_bounded(isolated_pool, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 100)
    for i in range(pool.COLD_START_RING + 5):
        pool.acquire(f"s{i}.py", "/p", "main")
    snap = pool.health_snapshot()
    assert snap["sessions_created"] == pool.COLD_START_RING + 5
    assert len(snap["recent_cold_starts"]) == pool.COLD_START_RING


def test_snapshot_does_not_wait_for_a_busy_pool_lock(isolated_pool, monkeypatch):
    """池锁被别的线程占着（正在起会话）时，备包不能跟着卡死；「锁被占」本身就是证据。"""
    monkeypatch.setattr(pool, "_lock", threading.Lock())
    with pool._lock:
        snap = pool.health_snapshot()
    assert snap["lock_busy"] is True and snap["alive"] is None


def test_diagnostic_summary_is_in_render_env_and_carries_no_names(isolated_pool, tmp_path):
    secret = "secret_fig_name_xyz.py"
    pool.acquire(secret, str(tmp_path / "secret_project_dir"), "main")
    src = diagdetail.Sources(project={"figures_dir": str(tmp_path)})
    # 只验摘要段：探针子进程与本测试无关
    items = diagdetail.collect_render_env(src, diagdetail.DetailOptions(), diagdetail.Scrubber())
    text = items[0].data if isinstance(items[0].data, str) else items[0].data.decode("utf-8")
    doc = json.loads(text)
    rp = doc["render_pool"]
    assert rp["sessions_created"] == 1 and rp["max_alive"] == pool.MAX_ALIVE
    assert set(rp["recent_cold_starts"][0]["timings"]) <= set(pool.ACQUIRE_SEGMENTS)
    blob = json.dumps(rp)
    assert "secret_fig_name_xyz" not in blob and "secret_project_dir" not in blob


try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


@pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器")
def test_real_python_pool_cold_start_records_constructor_segments(tmp_path):
    (tmp_path / "f.py").write_text("def main():\n    pass\n", encoding="utf-8")
    try:
        mark = pool.acquire_mark()
        w, created = pool.acquire("f.py", str(tmp_path), "main")
        segs = pool.acquire_timings(mark, w)
        if created:
            assert {"env_probe_ms", "fingerprint_ms", "spawn_gate_ms"} <= set(segs)
            assert ("spawn_ms" in segs) or ("handshake_ms" in segs)  # 两条控制面各占其一
        mark = pool.acquire_mark()
        w2, created2 = pool.acquire("f.py", str(tmp_path), "main")
        assert w2 is w and not created2
        assert set(pool.acquire_timings(mark, w2)) == HOT_ONLY
    finally:
        pool.shutdown_all(str(tmp_path), wait=True)
