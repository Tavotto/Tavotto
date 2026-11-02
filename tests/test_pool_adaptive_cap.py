"""worker 池上限按内存自适应 + 后台（邻居）补渲染不挤掉前台（ADR 0159）。

「渲染越改越慢」(TVM-Y37X)：写死 `MAX_ALIVE=3` 时，4~6 张图轮流渲染是 LRU 的最坏情形——每次都冷启动。
全部用假 worker 与计数器，不依赖真时间、不起子进程。
"""

from __future__ import annotations

import sys
import threading

import pytest

from tavotto import app as app_mod
from tavotto.engine import pool, sysmem

GIB = 1024**3


# ------------------------------- 上限计算 ---------------------------------


@pytest.fixture
def adaptive(monkeypatch):
    """取消 conftest 钉住的 3，让 `max_alive()` 真走环境变量 / 自适应。"""
    monkeypatch.setattr(pool, "MAX_ALIVE", None)
    monkeypatch.setattr(pool, "_cap_cache", None)
    monkeypatch.delenv(pool.MAX_ALIVE_ENV, raising=False)


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (None, 3),  # 读不到 → 下限
        (0, 3),
        (-5, 3),
        (512 * 1024**2, 3),  # 太小 → 夹到下限
        (4 * GIB, 4),  # 4GiB * 0.5 / 500MiB
        (6 * GIB, 6),
        (64 * GIB, 8),  # 太大 → 夹到上限
    ],
)
def test_cap_from_available_is_clamped(available, expected):
    assert pool.cap_from_available(available) == expected
    assert pool.MIN_ALIVE <= pool.cap_from_available(available) <= pool.MAX_ALIVE_CEILING


def test_max_alive_reads_memory_once_per_window(adaptive, monkeypatch):
    reads = []
    monkeypatch.setattr(sysmem, "available_memory_bytes", lambda: reads.append(1) or 6 * GIB)
    assert pool.max_alive() == 6
    assert pool.max_alive() == 6
    assert len(reads) == 1


def test_max_alive_falls_back_to_floor_when_memory_unreadable(adaptive, monkeypatch):
    monkeypatch.setattr(sysmem, "available_memory_bytes", lambda: None)
    assert pool.max_alive() == pool.MIN_ALIVE


def test_env_override_wins_and_is_clamped(adaptive, monkeypatch):
    monkeypatch.setattr(sysmem, "available_memory_bytes", lambda: 64 * GIB)
    monkeypatch.setenv(pool.MAX_ALIVE_ENV, "5")
    assert pool.max_alive() == 5
    monkeypatch.setenv(pool.MAX_ALIVE_ENV, "0")
    assert pool.max_alive() == 1
    monkeypatch.setenv(pool.MAX_ALIVE_ENV, "9999")
    assert pool.max_alive() == pool.MAX_ALIVE_ENV_LIMIT
    monkeypatch.setenv(pool.MAX_ALIVE_ENV, "lots")  # 非整数：忽略，回自适应
    assert pool.max_alive() == pool.MAX_ALIVE_CEILING


def test_every_cap_consumer_goes_through_max_alive():
    """上限单一出处：生产代码里除了 `max_alive()` 自己，没有任何地方读 `MAX_ALIVE`（AST，不看注释和文档串）。"""
    import ast
    from pathlib import Path

    root = Path(pool.__file__).resolve().parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed = set()
        if path.name == "pool.py":
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "max_alive":
                    allowed.update(id(n) for n in ast.walk(node))
        for node in ast.walk(tree):
            name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
            if name == "MAX_ALIVE" and id(node) not in allowed:
                is_decl = isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                if not (path.name == "pool.py" and is_decl):  # 模块级声明（钩子本身）不算读取
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


# ----------------------------- 内存读取（sysmem） ----------------------------


def test_parse_meminfo_and_vm_stat():
    assert sysmem.parse_meminfo("MemTotal: 100 kB\nMemAvailable:    2048 kB\n") == 2048 * 1024
    assert sysmem.parse_meminfo("MemTotal: 100 kB\n") is None
    vm = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               10.\n"
        "Pages inactive:                           20.\n"
        "Pages speculative:                        30.\n"
    )
    assert sysmem.parse_vm_stat(vm) == 60 * 16384
    assert sysmem.parse_vm_stat("garbage") is None


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux", "freebsd13"])
def test_unreadable_memory_is_none_on_every_platform(monkeypatch, platform):
    def boom():
        raise OSError("denied")

    monkeypatch.setattr(sys, "platform", platform)
    for name in ("_windows", "_macos", "_linux"):
        monkeypatch.setattr(sysmem, name, boom)
    assert sysmem.available_memory_bytes() is None


def test_non_positive_reading_is_none(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sysmem, "_linux", lambda: 0)
    assert sysmem.available_memory_bytes() is None


# ------------------------------ 淘汰 / 冷启动 -------------------------------


class Worker:
    def __init__(self, script, entry="main"):
        self.script_name = script
        self.entry = entry
        self.python = sys.executable
        self.build_failed = False
        self.running = True
        self.last_used = 0.0

    def alive(self):
        return self.running

    def shutdown(self):
        self.running = False


@pytest.fixture
def rig(monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(pool, "_pending_retirements", {})
    monkeypatch.setattr(pool, "_last_acquired", threading.local())
    monkeypatch.setattr(pool, "COLD_ACQUIRE_OBSERVER", None)
    monkeypatch.setattr(pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(pool, "resolve_worker_python", lambda *a, **kw: (sys.executable, "test"))
    monkeypatch.setattr(pool, "_refuse_if_mutating", lambda *a: None)
    monkeypatch.setattr(pool.launcher_mod, "prewarm", lambda *a: None)
    monkeypatch.setattr(pool, "_schedule_prune", lambda: None)
    monkeypatch.setattr(pool, "generation_stale", lambda worker: False)
    monkeypatch.setattr(pool, "_remap_current", lambda *a: True)
    monkeypatch.setattr(pool, "_workdir_gate", lambda *a: None)

    def new_worker(script, figures_dir, entry, **kw):
        spawned.append(script)
        return Worker(script, entry)

    monkeypatch.setattr(pool, "_new_worker", new_worker)
    ticks = iter(range(1, 10_000))
    monkeypatch.setattr(pool.time, "time", lambda: float(next(ticks)))

    class Rig:
        project = str(tmp_path)
        cold = spawned

        def take(self, script, purpose=pool.PURPOSE_FOREGROUND):
            with pool.acquire_purpose(purpose):
                w, _ = pool.acquire(script, self.project, "main")
            w._lease_until = 0.0  # 调用方发出了第一条请求：租约结束
            return w

        def alive(self):
            return sorted(w.script_name for w in pool._workers.values() if w.alive())

    return Rig()


def _rotate(rig, scripts, rounds):
    for _ in range(rounds):
        for name in scripts:
            rig.take(name)


@pytest.mark.parametrize("scripts", [2, 3])
def test_few_scripts_never_cold_start_twice(rig, monkeypatch, scripts):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    _rotate(rig, [f"s{i}.py" for i in range(scripts)], rounds=10)
    assert len(rig.cold) == scripts


@pytest.mark.parametrize("scripts", [4, 6])
def test_fixed_cap_three_cold_starts_on_every_rotation(rig, monkeypatch, scripts):
    """复现悬崖：4~6 个脚本轮流，上限 3 时每一次取会话都是冷启动。"""
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    _rotate(rig, [f"s{i}.py" for i in range(scripts)], rounds=5)
    assert len(rig.cold) == scripts * 5


@pytest.mark.parametrize("scripts", [4, 6])
def test_adaptive_cap_removes_the_cliff(rig, monkeypatch, adaptive, scripts):
    monkeypatch.setattr(sysmem, "available_memory_bytes", lambda: 8 * GIB)
    assert pool.max_alive() == 8
    _rotate(rig, [f"s{i}.py" for i in range(scripts)], rounds=5)
    assert len(rig.cold) == scripts  # 每个脚本只冷启动一次


# ------------------------------ 意图：邻居 --------------------------------


def test_default_purpose_is_foreground_and_unknown_is_rejected():
    assert pool.current_purpose() == pool.PURPOSE_FOREGROUND
    with pytest.raises(ValueError), pool.acquire_purpose("whenever"):
        pass
    with pool.acquire_purpose(pool.PURPOSE_NEIGHBOUR):
        assert pool.current_purpose() == pool.PURPOSE_NEIGHBOUR
    assert pool.current_purpose() == pool.PURPOSE_FOREGROUND


def test_neighbour_hit_does_not_refresh_lru(rig, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    a = rig.take("a.py")
    before = a.last_used
    rig.take("a.py", pool.PURPOSE_NEIGHBOUR)
    assert a.last_used == before
    rig.take("a.py")
    assert a.last_used > before


def test_neighbour_is_deferred_instead_of_evicting_a_foreground_session(rig, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    for name in ("a.py", "b.py", "c.py"):
        rig.take(name)
    with pytest.raises(pool.WorkerError) as caught:
        rig.take("n.py", pool.PURPOSE_NEIGHBOUR)
    assert caught.value.code == pool.BACKGROUND_DEFERRED
    assert rig.alive() == ["a.py", "b.py", "c.py"]
    assert rig.cold == ["a.py", "b.py", "c.py"]  # 连会话都没起
    # 同一个请求换成前台：照旧淘汰最久未用的
    rig.take("n.py")
    assert rig.alive() == ["b.py", "c.py", "n.py"]


def test_neighbour_cold_starts_when_there_is_room(rig, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    rig.take("a.py")
    n = rig.take("n.py", pool.PURPOSE_NEIGHBOUR)
    assert n.alive() and n._background_only is True
    assert rig.alive() == ["a.py", "n.py"]


def test_background_only_sessions_are_evicted_before_foreground_ones(rig, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    n = rig.take("n.py", pool.PURPOSE_NEIGHBOUR)  # 最先建、last_used 最小也是最先走
    rig.take("a.py")
    rig.take("b.py")
    # 让邻居的 last_used 比前台还新：优先级仍然让它先走
    n.last_used = 10_000.0
    rig.take("c.py")
    assert n._evicted is True
    assert rig.alive() == ["a.py", "b.py", "c.py"]


def test_foreground_touch_promotes_a_background_session(rig, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 3)
    n = rig.take("n.py", pool.PURPOSE_NEIGHBOUR)
    rig.take("n.py")  # 用户进了这张图
    assert n._background_only is False


def test_neighbour_rebuild_of_a_dead_session_counts_only_the_others(rig, monkeypatch):
    monkeypatch.setattr(pool, "MAX_ALIVE", 2)
    rig.take("a.py")
    n = rig.take("n.py")
    n.running = False  # 死了：重建它不占额外名额
    again = rig.take("n.py", pool.PURPOSE_NEIGHBOUR)
    assert again is not n and again.alive()
    # 池里另一条（a）加上新建的 n 刚好 2 条，没有挤掉任何人
    assert rig.alive() == ["a.py", "n.py"]


# ------------------------------- HTTP 契约 --------------------------------


@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    app_mod.reset_projects()
    yield app_mod.app.test_client()
    app_mod.reset_projects()
    from tavotto.engine import project_watch

    project_watch.stop()


def _open_project(client, tmp_path):
    import pymupdf

    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    client.post("/api/projects/open", json={"path": str(figs)})


class _Worker:
    built = True
    rev = 1

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        return {"manifest": {"elements": []}, "warnings": []}


@pytest.fixture
def seen(monkeypatch):
    purposes = []

    def get(*a, **kw):
        purposes.append(pool.current_purpose())
        return _Worker()

    monkeypatch.setattr(
        app_mod.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )
    monkeypatch.setattr(app_mod.engine_pool, "get", get)
    return purposes


def test_render_without_purpose_is_foreground(client, tmp_path, seen):
    _open_project(client, tmp_path)
    resp = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert resp.status_code == 200
    assert seen == [pool.PURPOSE_FOREGROUND]


def test_render_passes_the_neighbour_purpose_down(client, tmp_path, seen):
    _open_project(client, tmp_path)
    resp = client.post(
        "/api/engine/render", json={"id": "p1.pdf", "patches": [], "purpose": "neighbour"}
    )
    assert resp.status_code == 200
    assert seen == [pool.PURPOSE_NEIGHBOUR]


def test_render_rejects_an_unknown_purpose(client, tmp_path, seen):
    _open_project(client, tmp_path)
    resp = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": [], "purpose": "x"})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_purpose"
    assert seen == []


def test_deferred_background_render_is_409_without_events_or_toast(
    client, tmp_path, monkeypatch, seen
):
    _open_project(client, tmp_path)
    events = []
    monkeypatch.setattr(app_mod, "sse_publish", lambda kind, data=None: events.append(kind))

    def full(*a, **kw):
        raise pool.WorkerError("full", code=pool.BACKGROUND_DEFERRED)

    monkeypatch.setattr(app_mod.engine_pool, "get", full)
    resp = client.post(
        "/api/engine/render", json={"id": "p1.pdf", "patches": [], "purpose": "neighbour"}
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == pool.BACKGROUND_DEFERRED and body["deferred"] is True
    assert events == []  # 没有 render.preparing / render.failed（后者前端会弹失败提示）


def test_purpose_set_and_deferred_code_match_the_frontend():
    """严格同源对（same-origin-pairs.md）：后端意图闭集 / 暂缓码 ↔ `web/src/lib/renderPurpose.ts`。"""
    import re
    from pathlib import Path

    ts = (Path(__file__).resolve().parents[1] / "web/src/lib/renderPurpose.ts").read_text(
        encoding="utf-8"
    )
    union = re.search(r"export type RenderPurpose = ([^\n]+)", ts)
    assert union is not None
    assert tuple(re.findall(r"'([a-z]+)'", union.group(1))) == pool.PURPOSES
    code = re.search(r"RENDER_DEFERRED_CODE = '([a-z_]+)'", ts)
    assert code is not None and code.group(1) == pool.BACKGROUND_DEFERRED


def test_render_response_keeps_the_acquire_segment_timings(client, tmp_path, rig, monkeypatch):
    """handler 拆成 `_api_engine_render_body` 之后，worker_get_ms 与池分段（#1637）仍在 timings 里。"""
    _open_project(client, tmp_path)

    class Hot(Worker):
        built = True
        rev = 1

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            return {
                "manifest": {"elements": []},
                "warnings": [],
                "timings": {"canvas_draw_ms": 1.0},
            }

    monkeypatch.setattr(pool, "_new_worker", lambda script, fd, entry, **kw: Hot(script, entry))
    monkeypatch.setattr(
        app_mod.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )
    resp = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert resp.status_code == 200
    timings = resp.get_json()["timings"]
    assert "worker_get_ms" in timings and timings["canvas_draw_ms"] == 1.0
    assert set(pool.ACQUIRE_SEGMENTS) & set(timings), timings


# --------------------------- 上限不自激振荡 --------------------------------


def _sessions(monkeypatch, n):
    monkeypatch.setattr(pool, "_workers", {("k", i): Worker(f"s{i}.py") for i in range(n)})


def test_cap_does_not_shrink_as_pool_sessions_use_memory(adaptive, monkeypatch):
    """同一台机器：每多一个活会话，available 就少一个估计值；上限不许因此下降。"""
    total_free = 8 * GIB  # 没有任何会话时的空闲内存
    caps = []
    for alive in range(0, 9):
        _sessions(monkeypatch, alive)
        monkeypatch.setattr(pool, "_cap_cache", None)
        monkeypatch.setattr(
            sysmem,
            "available_memory_bytes",
            lambda alive=alive: total_free - alive * pool.SESSION_MEMORY_ESTIMATE,
        )
        caps.append(pool.max_alive())
    assert caps == [caps[0]] * len(caps)
    assert caps[0] >= 6


def test_cap_hysteresis_keeps_hot_sessions_at_the_boundary(rig, adaptive, monkeypatch):
    """边界值附近内存来回抖动：已存活的热会话不被反复淘汰。"""
    base = 4 * GIB + 100  # 刚好够 4 个
    jitter = iter([0, -300 * 1024**2, 0, -300 * 1024**2, 0, 0])

    def available():  # 会话占着内存：available = 空闲基数 − 活会话 × 估计值 ± 抖动
        alive = sum(1 for w in pool._workers.values() if w.alive())
        return base - alive * pool.SESSION_MEMORY_ESTIMATE + next(jitter)

    monkeypatch.setattr(sysmem, "available_memory_bytes", available)

    def expire():  # 让缓存窗口过期，下一次 max_alive() 重新读内存
        monkeypatch.setattr(pool, "_cap_cache", (-1e9, pool._cap_cache[1]))

    first = pool.max_alive()
    assert first == 4
    scripts = [f"s{i}.py" for i in range(first)]
    _rotate(rig, scripts, rounds=1)
    colds = len(rig.cold)
    for _ in range(4):  # 抖动的读数（边界上下）：上限维持，轮流渲染零新增冷启动
        expire()
        assert pool.max_alive() == first
        _rotate(rig, scripts, rounds=1)
    assert len(rig.cold) == colds


def test_cap_still_shrinks_on_real_memory_pressure(adaptive, monkeypatch):
    monkeypatch.setattr(sysmem, "available_memory_bytes", lambda: 8 * GIB)
    assert pool.max_alive() == 8
    monkeypatch.setattr(pool, "_cap_cache", (-1e9, 8))
    monkeypatch.setattr(sysmem, "available_memory_bytes", lambda: 2 * GIB)
    assert pool.max_alive() == 3


def test_vm_stat_uses_an_absolute_path(monkeypatch):
    seen = []

    class Done:
        returncode = 0
        stdout = "page size of 4096 bytes\nPages free: 1.\n"

    monkeypatch.setattr(sysmem.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or Done())
    assert sysmem._macos() == 4096
    assert seen == [["/usr/bin/vm_stat"]]
