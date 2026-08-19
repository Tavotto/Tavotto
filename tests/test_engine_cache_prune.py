"""cache/engine/ 会话目录治理：落盘的最后使用时间 + 容量/份数预算。

全程不起真 worker（.venv 里没有 matplotlib）：目录用 mkdir + os.utime 造，
worker 用 `__new__` 造壳子只装 `request()` 用得到的几个属性。
"""
import json
import os
import threading

import pytest

from tavotto.engine import pool


def _aged_dir(root, name: str, order: int, size: int = 0):
    """造一个会话缓存目录，mtime 按 order 递增（越小越久未用）。"""
    d = root / name
    (d / "out").mkdir(parents=True)
    if size:
        (d / "out" / "fig.svg").write_bytes(b"x" * size)
    t = 1_700_000_000 + order
    os.utime(d, (t, t))
    return d


class _FakeProc:
    """够 `request()` 走一个来回的最小 stdin/stdout 替身。

    按协议 v1 回显 `request_id`/`protocol_version`——回显对不上的话
    `request()` 会当场判定会话错乱并 kill（见 test_worker_protocol.py）。
    """

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.stdin = self
        self.stdout = self
        self.written: list[str] = []

    def poll(self):
        return None

    def write(self, s: str) -> None:
        self.written.append(s)
        env = json.loads(s)
        reply = json.loads(self._replies.pop(0)) if self._replies else {"ok": True}
        reply.setdefault("protocol_version", env["protocol_version"])
        reply.setdefault("request_id", env["request_id"])
        self._pending = json.dumps(reply) + "\n"

    def flush(self) -> None:
        pass

    def readline(self) -> str:
        return self._pending


def _stub_worker(base, replies=()):
    w = pool.EngineWorker.__new__(pool.EngineWorker)
    w.base = base
    w._touched = 0.0
    w.lock = threading.Lock()
    w.proc = _FakeProc(list(replies))
    w.script_name = base.name
    w.generation = 1       # v1 信封字段：真 __init__ 里由 _next_generation() 给
    w.rev = 0
    return w


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "engine"
    root.mkdir()
    monkeypatch.setattr(pool, "ENGINE_CACHE", root)
    monkeypatch.setattr(pool, "_workers", {})
    return root


def test_prune_evicts_oldest_within_byte_budget(cache):
    for i in range(5):
        _aged_dir(cache, f"s{i}", i, size=100)
    removed = pool.prune_engine_cache(max_bytes=250, keep=99)
    assert removed == 3
    assert sorted(p.name for p in cache.iterdir()) == ["s3", "s4"]


def test_prune_evicts_oldest_within_count_budget(cache):
    """空壳目录撞不到容量线，靠份数上限回收。"""
    for i in range(6):
        _aged_dir(cache, f"s{i}", i)
    removed = pool.prune_engine_cache(max_bytes=10 ** 9, keep=2)
    assert removed == 4
    assert sorted(p.name for p in cache.iterdir()) == ["s4", "s5"]


def test_prune_spares_dirs_held_by_pool_workers(cache):
    """存活会话正在写 out/sandbox，删了它下一次 override 直接炸。"""
    dirs = [_aged_dir(cache, f"s{i}", i, size=100) for i in range(4)]
    # 最老的那个正被一个 worker 端着
    pool._workers[("/p", "s0")] = _stub_worker(dirs[0])
    removed = pool.prune_engine_cache(max_bytes=0, keep=0)
    assert removed == 3
    assert sorted(p.name for p in cache.iterdir()) == ["s0"]


def test_prune_skips_undeletable_dir_and_continues(cache, monkeypatch):
    """Windows 上目录被占用会抛 OSError——跳过这一个，后面的照删。"""
    for i in range(3):
        _aged_dir(cache, f"s{i}", i, size=100)
    real_rmtree = pool.shutil.rmtree

    def flaky(path, *a, **kw):
        if os.path.basename(str(path)) == "s0":
            raise OSError(32, "被占用")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(pool.shutil, "rmtree", flaky)
    removed = pool.prune_engine_cache(max_bytes=0, keep=0)
    assert removed == 2                       # s1/s2 照删
    assert (cache / "s0").exists()            # 删不掉的原样留着


def test_prune_returns_zero_when_cache_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "never-created")
    monkeypatch.setattr(pool, "_workers", {})
    assert pool.prune_engine_cache() == 0


def test_request_touches_cache_dir_but_throttles(cache, monkeypatch):
    """`mkdir(exist_ok=True)` 不动 mtime，得靠 request() 落盘最后使用时间。"""
    base = _aged_dir(cache, "s0", 0)
    calls: list[str] = []
    real_utime = os.utime
    monkeypatch.setattr(os, "utime",
                        lambda p, t=None: (calls.append(str(p)), real_utime(p, t))[1])

    w = _stub_worker(base, ['{"ok": true}\n'] * 3)
    w.request({"cmd": "build"})
    w.request({"cmd": "override"})
    assert calls == [str(base)]               # 同一分钟内只落盘一次

    w._touched -= pool._TOUCH_INTERVAL + 1    # 节流窗口过去了
    w.request({"cmd": "override"})
    assert calls == [str(base), str(base)]


def test_touch_makes_active_dir_newest(cache):
    """回归审计里指出的坑：不落盘的话高频活跃目录会被判成最老、优先删。"""
    old_active = _aged_dir(cache, "hot", 0, size=100)   # 首次创建于很久以前
    _aged_dir(cache, "cold", 1, size=100)               # 之后建的，但没再用过
    _stub_worker(old_active)._touch()                   # 用一次 = 落盘 mtime

    pool.prune_engine_cache(max_bytes=150, keep=99)
    assert (cache / "hot").exists()
    assert not (cache / "cold").exists()


def test_touch_survives_unwritable_dir(cache, monkeypatch):
    """落盘失败不该把渲染带崩——清理是治理手段，不是功能。"""
    base = _aged_dir(cache, "s0", 0)
    monkeypatch.setattr(os, "utime",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError(13, "只读")))
    w = _stub_worker(base, ['{"ok": true}'])
    assert w.request({"cmd": "build"})["ok"] is True
