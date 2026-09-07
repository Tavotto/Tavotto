"""build 超时按注册表的 cost 分档（ADR 0046）。"""

import json

import pytest

from tavotto.engine import pool, workerd_client


def test_tiers_scale_with_the_base(monkeypatch):
    assert pool.build_timeout_for("medium") == pool.BUILD_TIMEOUT
    assert pool.build_timeout_for("heavy") == pool.BUILD_TIMEOUT * 4
    assert pool.build_timeout_for("light") == pytest.approx(pool.BUILD_TIMEOUT / 3)
    assert pool.build_timeout_for(None) == pool.BUILD_TIMEOUT
    assert pool.build_timeout_for("weird") == pool.BUILD_TIMEOUT
    # 用例把基数缩到 2 秒时各档一起缩——否则「否则用例要干等 15 分钟」那批注释就成了假话
    monkeypatch.setattr(pool, "BUILD_TIMEOUT", 2.0)
    assert pool.build_timeout_for("heavy") == 8.0


def test_cost_comes_from_the_registry_on_disk(tmp_path):
    assert pool.script_cost(tmp_path, "fig.py") == "medium"  # 没有注册表
    (tmp_path / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "run_all.py": {"entry": "__main__", "cost": "heavy", "stems": ["impact"]},
                    "sub/quick.py": {"entry": "main", "cost": "light", "stems": ["q"]},
                },
            }
        ),
        encoding="utf-8",
    )
    assert pool.script_cost(tmp_path, "run_all.py") == "heavy"
    assert pool.script_cost(tmp_path, "sub/quick.py") == "light"
    assert pool.script_cost(tmp_path, "sub\\quick.py") == "light"  # Windows 调用方的反斜杠归一
    assert pool.script_cost(tmp_path, "unregistered.py") == "medium"
    (tmp_path / "tavotto_registry.json").write_text("{not json", encoding="utf-8")
    assert pool.script_cost(tmp_path, "run_all.py") == "medium"  # 注册表坏了不该让渲染崩


def _registry(tmp_path, cost):
    (tmp_path / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {"fig.py": {"entry": "main", "cost": cost, "stems": ["Fig1"]}},
            }
        ),
        encoding="utf-8",
    )


def test_the_python_pool_sends_the_tiered_timeout(monkeypatch, tmp_path):
    _registry(tmp_path, "heavy")

    class _Rec:
        def __init__(self, argv, **kw):
            self.pid = 1

        def poll(self):
            return None

    monkeypatch.setattr(pool.subprocess, "Popen", _Rec)
    monkeypatch.setattr(
        pool, "select_worker_python", lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM)
    )
    w = pool.EngineWorker("fig.py", str(tmp_path), "main")
    seen = {}
    monkeypatch.setattr(
        w,
        "request",
        lambda obj, timeout=None: seen.update(obj=obj, timeout=timeout) or {"stems": {}},
    )
    w.ensure_built()
    assert seen["obj"] == {"cmd": "build"}
    assert seen["timeout"] == pool.BUILD_TIMEOUT * 4
    assert w.build_timeout == pool.BUILD_TIMEOUT * 4


class _FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, op, **kw):
        self.calls.append((op, kw))
        return {"ok": True, "session_id": "s-1", "stems": {}}


def test_workerd_sends_the_same_tiered_timeout(monkeypatch, tmp_path):
    _registry(tmp_path, "light")
    monkeypatch.setattr(
        pool, "select_worker_python", lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM)
    )
    client = _FakeClient()
    w = pool.WorkerdWorker("fig.py", str(tmp_path), "main", client=client)
    w.ensure_built()
    build = [kw for op, kw in client.calls if op == "build"]
    assert build and build[0]["timeout"] == pytest.approx(pool.BUILD_TIMEOUT / 3)
    assert isinstance(workerd_client.WorkerdError("x"), Exception)  # 只是确认模块可用
