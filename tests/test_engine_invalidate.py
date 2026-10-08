"""`POST /api/engine/invalidate`：QuickEdit「重新构建」的后端半边。

它只做一件事——让这张图的热会话过期（与脚本文件变更走同一个 `pool.invalidate`）。
三条不变式：**不起 worker**（冷 build 仍由随后的 render 惰性触发）、**不碰源脚本
与原始文件**、native 会话**不杀**且如实回 `invalidated: false`。
"""

from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import (
    enginesession as engine_enginesession,
    project_watch as engine_watch,
    runtimeasset as engine_runtimeasset,
)


@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _open(client, tmp_path):
    figs = tmp_path / "inval"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    (figs / "x.py").write_text("print('untouched')\n", encoding="utf-8")
    client.post("/api/projects/open", json={"path": str(figs)})
    return figs


def _forbid_spawn(monkeypatch):
    """作废绝不能顺手起一个 worker：脚本只在用户要图的那一刻跑。"""

    def _boom(*a, **kw):
        raise AssertionError("invalidate 不该起 worker")

    monkeypatch.setattr(m.engine_pool, "get", _boom)


def test_disk_panel_invalidates_its_script_session_without_spawning(client, tmp_path, monkeypatch):
    figs = _open(client, tmp_path)
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        m.engine_pool,
        "invalidate",
        lambda script, root=None, run=None, **kw: calls.append((script, root)),
    )
    _forbid_spawn(monkeypatch)
    before = (figs / "x.py").read_bytes(), (figs / "p1.pdf").read_bytes()

    res = client.post("/api/engine/invalidate", json={"id": "p1.pdf"})

    assert res.status_code == 200 and res.get_json() == {"invalidated": True}
    assert len(calls) == 1
    script, root = calls[0]
    assert script == "x.py"
    # 只作废**这个项目**的那条会话（不给 root 会作废所有项目里的同名脚本）
    assert root is not None and Path(root).resolve() == figs.resolve()
    assert ((figs / "x.py").read_bytes(), (figs / "p1.pdf").read_bytes()) == before


def test_unregistered_panel_is_404_and_touches_nothing(client, tmp_path, monkeypatch):
    _open(client, tmp_path)
    monkeypatch.setattr(m.engine_registry.Registry, "for_stem", lambda self, s: None)
    calls: list = []
    monkeypatch.setattr(m.engine_pool, "invalidate", lambda *a, **kw: calls.append(a))
    _forbid_spawn(monkeypatch)

    assert client.post("/api/engine/invalidate", json={"id": "p1.pdf"}).status_code == 404
    assert client.post("/api/engine/invalidate", json={"id": "nope.pdf"}).status_code == 404
    assert calls == []


def test_native_session_is_not_killed_and_says_so(client, tmp_path, monkeypatch):
    """native 面板的会话是用户自己终端里的进程：不杀，而且不能装作重跑了。"""
    _open(client, tmp_path)
    monkeypatch.setattr(engine_runtimeasset, "is_runtime_id", lambda rel_id: True)
    monkeypatch.setattr(
        engine_runtimeasset,
        "resolve",
        lambda rel_id, reg: {"script": "x.py", "entry": "main", "stem": "fig"},
    )
    monkeypatch.setattr(
        engine_enginesession,
        "profile_of",
        lambda root, asset_id, **kw: engine_enginesession.PROFILE_NATIVE,
    )
    calls: list = []
    monkeypatch.setattr(m.engine_pool, "invalidate", lambda *a, **kw: calls.append(a))
    _forbid_spawn(monkeypatch)

    res = client.post("/api/engine/invalidate", json={"id": "runtime:whatever"})

    assert res.status_code == 200
    assert res.get_json() == {"invalidated": False, "reason": "native_session"}
    assert calls == []


def test_safe_runtime_panel_invalidates_by_its_script(client, tmp_path, monkeypatch):
    figs = _open(client, tmp_path)
    monkeypatch.setattr(engine_runtimeasset, "is_runtime_id", lambda rel_id: True)
    monkeypatch.setattr(
        engine_runtimeasset,
        "resolve",
        lambda rel_id, reg: {"script": "x.py", "entry": "main", "stem": "fig"},
    )
    monkeypatch.setattr(
        engine_enginesession,
        "profile_of",
        lambda root, asset_id, **kw: engine_enginesession.PROFILE_SAFE,
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        m.engine_pool,
        "invalidate",
        lambda script, root=None, run=None, **kw: calls.append((script, root)),
    )
    _forbid_spawn(monkeypatch)

    res = client.post("/api/engine/invalidate", json={"id": "runtime:fig"})

    assert res.get_json() == {"invalidated": True}
    assert calls and calls[0][0] == "x.py" and Path(calls[0][1]).resolve() == figs.resolve()


class _FakeSession:
    def __init__(self):
        self.down = False

    def shutdown(self):
        self.down = True


def _seed_sessions(figs, script, runs):
    """往真池里塞假会话（不起 worker）：键用池自己的 `_worker_key`，所以作废的范围判据是真的。"""
    from tavotto.engine import execspec

    seeded = {}
    with m.engine_pool._lock:
        for name in runs:
            run = None if name is None else execspec.RunSelection(name, ("--k", name))
            key = m.engine_pool._worker_key(str(figs), script, None, run)
            seeded[name] = (key, _FakeSession())
            m.engine_pool._workers[key] = seeded[name][1]
    return seeded


def _drop_sessions(seeded):
    with m.engine_pool._lock:
        for key, _ in seeded.values():
            m.engine_pool._workers.pop(key, None)


def test_rebuilding_the_no_argument_variant_keeps_sibling_argv_sessions(
    client, tmp_path, monkeypatch
):
    """Codex 评 #812 P2：重建**无参数**的 runtime 素材不许把同脚本别的 argv 变体的热会话一起作废。"""
    figs = _open(client, tmp_path)
    monkeypatch.setattr(engine_runtimeasset, "is_runtime_id", lambda rel_id: True)
    monkeypatch.setattr(
        engine_runtimeasset,
        "resolve",
        lambda rel_id, reg: {"script": "x.py", "entry": "main", "stem": "fig"},
    )
    monkeypatch.setattr(
        engine_enginesession,
        "profile_of",
        lambda root, asset_id, **kw: engine_enginesession.PROFILE_SAFE,
    )
    seeded = _seed_sessions(figs, "x.py", [None, "rc_a", "rc_b"])
    try:
        res = client.post("/api/engine/invalidate", json={"id": "runtime:fig"})
        assert res.get_json() == {"invalidated": True}
        import time

        time.sleep(0.2)  # 作废后的 shutdown 在守护线程里
        assert seeded[None][1].down and seeded[None][0] not in m.engine_pool._workers
        for name in ("rc_a", "rc_b"):
            assert not seeded[name][1].down and seeded[name][0] in m.engine_pool._workers
    finally:
        _drop_sessions(seeded)


def test_rebuilding_a_disk_panel_scopes_to_its_saved_default_variant(client, tmp_path, monkeypatch):
    """同一条判据的磁盘面板一侧：只作废它用的那份（脚本最近一次明确运行的配置），不是脚本的全部变体。"""
    from tavotto.engine import execspec

    figs = _open(client, tmp_path)
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )
    monkeypatch.setattr(
        m, "_disk_panel_run", lambda script: execspec.RunSelection("rc_a", ("--k", "rc_a"))
    )
    seeded = _seed_sessions(figs, "x.py", [None, "rc_a", "rc_b"])
    try:
        res = client.post("/api/engine/invalidate", json={"id": "p1.pdf"})
        assert res.get_json() == {"invalidated": True}
        import time

        time.sleep(0.2)
        assert seeded["rc_a"][1].down
        assert not seeded[None][1].down and not seeded["rc_b"][1].down
    finally:
        _drop_sessions(seeded)


def test_specfix_retire_hot_worker_scopes_to_the_workers_own_variant(tmp_path):
    """同一判据的第三个消费者：按规范修图后作废的是**这条**会话，不是同脚本全部 argv 变体。"""
    import types

    from tavotto.engine import execspec

    figs = tmp_path / "f"
    figs.mkdir()
    run = execspec.RunSelection("rc_a", ("--k", "rc_a"))
    seeded = _seed_sessions(figs, "x.py", [None, "rc_a", "rc_b"])
    try:
        worker = types.SimpleNamespace(
            script_name="x.py", figures_dir=str(figs), run=run, artifact_source=None
        )
        assert m._retire_hot_worker(worker) is True
        import time

        time.sleep(0.2)
        assert seeded["rc_a"][1].down
        assert not seeded[None][1].down and not seeded["rc_b"][1].down
    finally:
        _drop_sessions(seeded)
