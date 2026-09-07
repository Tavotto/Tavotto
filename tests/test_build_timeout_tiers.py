"""build 超时按注册表的 cost 分档（ADR 0048）。"""

import json
import time

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


def test_one_shot_replays_follow_the_same_tier(monkeypatch, tmp_path):
    """写回前的干净重放跑的就是那个脚本，预算必须一样。

    强行按 medium 的话，heavy 脚本的写回校验必然超时——而校验正是「热态所见 ==
    写进文件的」这条不变式的执行面，让它对最慢的那批脚本永远失败等于把不变式关掉。
    """
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
    monkeypatch.setattr("tavotto.engine.workerd_client.find_workerd", lambda: None)
    shot = pool.one_shot("fig.py", str(tmp_path), "main")
    try:
        seen = {}
        monkeypatch.setattr(
            shot, "request", lambda obj, timeout=None: seen.update(timeout=timeout) or {"stems": {}}
        )
        shot.ensure_built()
        assert seen["timeout"] == pool.BUILD_TIMEOUT * 4
    finally:
        pool.discard(shot)


def test_only_build_timeouts_get_the_heavy_hint(monkeypatch, tmp_path):
    """`override` / `export` 超时走的是别的档，标 heavy 一点用都没有——
    那句提示与那个码都只对 build 说（Codex 评审 P2）。"""

    class _Rec:
        """会话活着、但**永远读不到回应**——超时那条路的最小复现。

        读线程必须真的阻塞：`stdout.readline()` 立刻返回的话走的是「管道 EOF =
        进程没了」那条分支（code 为空），量不到超时。
        """

        def __init__(self, argv, **kw):
            self.pid = 1
            self.stdin = self
            self.stdout = self

        def poll(self):
            return None

        def readline(self):
            time.sleep(30)
            return ""

        def write(self, *_a):
            return None

        def flush(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(pool.subprocess, "Popen", _Rec)
    monkeypatch.setattr(
        pool, "select_worker_python", lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM)
    )
    w = pool.EngineWorker("fig.py", str(tmp_path), "main")
    monkeypatch.setattr(pool, "BUILD_TIMEOUT", 0.2)
    monkeypatch.setattr(pool, "REQUEST_TIMEOUT", 0.2)

    with pytest.raises(pool.WorkerError) as build_err:
        w.request({"cmd": "build"}, 0.2)
    assert build_err.value.code == pool.BUILD_TIMEOUT_CODE == "worker_build_timeout"
    assert "heavy" in str(build_err.value)

    w._dead = False
    with pytest.raises(pool.WorkerError) as render_err:
        w.request({"cmd": "render", "stem": "Fig1"}, 0.2)
    assert render_err.value.code == "worker_timeout"
    assert "heavy" not in str(render_err.value)
    # 两个码都要让会话判死（状态未知的绝不复用）
    assert pool.BUILD_TIMEOUT_CODE in pool._FATAL_CODES and "worker_timeout" in pool._FATAL_CODES


def test_workerd_build_timeouts_use_the_same_code(monkeypatch, tmp_path):
    """两条控制面同一个答案：workerd 只知道「超时」，是不是 build 由这边判。"""
    monkeypatch.setattr(
        pool, "select_worker_python", lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM)
    )

    class _Timeout(_FakeClient):
        def call(self, op, **kw):
            if op == "open_session":
                return {"ok": True, "session_id": "s-1"}
            raise workerd_client.WorkerdError("超时", code="worker_timeout")

    w = pool.WorkerdWorker("fig.py", str(tmp_path), "main", client=_Timeout())
    with pytest.raises(pool.WorkerError) as err:
        w.ensure_built()
    assert err.value.code == pool.BUILD_TIMEOUT_CODE
    # `override` 在没 build 过时会先 build——要量的是 render 这一跳，先把它标成已建
    w._dead = False
    w._session_id = "s-1"
    w.built = True
    with pytest.raises(pool.WorkerError) as err2:
        w.override("Fig1", [])
    assert err2.value.code == "worker_timeout"
