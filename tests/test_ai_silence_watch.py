"""无输出看门狗（`ai_bridge._SilenceWatch`）：静默达阈值只发 `ai.stalled` 提示，不杀进程；有输出即复位；结束即停。"""

from __future__ import annotations

import threading
import time

from tavotto.engine import ai_bridge


def _collect():
    events: list[tuple[str, dict]] = []
    lock = threading.Lock()

    def emit(name, data):
        with lock:
            events.append((name, data))

    return events, emit


def _wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_silence_fires_once_then_resets_on_output(monkeypatch):
    monkeypatch.setattr(ai_bridge, "STALL_AFTER_S", 120)
    events, emit = _collect()
    clock = _Clock()
    w = ai_bridge._SilenceWatch("s1", emit, threading.Event(), clock=clock)
    clock.t += 119.9
    w.check()
    assert events == []
    clock.t += 0.2
    w.check()
    clock.t += 300  # 持续静默也只提示一次
    w.check()
    assert events == [("ai.stalled", {"session": "s1", "stalled": True, "idle_s": 120})]
    w.touch()
    assert events[-1] == ("ai.stalled", {"session": "s1", "stalled": False, "idle_s": 0})
    w.touch()  # 没在提示中就不重复复位
    assert len(events) == 2


def test_steady_output_never_fires(monkeypatch):
    monkeypatch.setattr(ai_bridge, "STALL_AFTER_S", 120)
    events, emit = _collect()
    clock = _Clock()
    w = ai_bridge._SilenceWatch("s2", emit, threading.Event(), clock=clock)
    for _ in range(50):
        clock.t += 100  # 每次都在阈值内来一行
        w.touch()
        w.check()
    assert events == []


def test_state_flip_and_publish_are_atomic(monkeypatch):
    """推送在锁内：后台线程判定卡住与推送之间插不进 `touch`，前端收到的 true / false 顺序与状态一致。"""
    monkeypatch.setattr(ai_bridge, "STALL_AFTER_S", 120)
    held: list[bool] = []
    clock = _Clock()
    box: dict = {}

    def emit(name, data):
        held.append(box["w"]._lock.locked())

    w = box["w"] = ai_bridge._SilenceWatch("s4", emit, threading.Event(), clock=clock)
    clock.t += 121
    w.check()
    w.touch()
    assert held == [True, True]


def test_background_thread_fires(monkeypatch):
    monkeypatch.setattr(ai_bridge, "STALL_AFTER_S", 0.05)
    monkeypatch.setattr(ai_bridge, "STALL_POLL_S", 0.01)
    events, emit = _collect()
    done = threading.Event()
    ai_bridge._SilenceWatch("s5", emit, done).start()
    try:
        assert _wait_for(lambda: len(events) >= 1, timeout=10.0)
        assert events[0][1]["stalled"] is True
    finally:
        done.set()


def test_stops_when_complete(monkeypatch):
    monkeypatch.setattr(ai_bridge, "STALL_AFTER_S", 0.05)
    monkeypatch.setattr(ai_bridge, "STALL_POLL_S", 0.02)
    events, emit = _collect()
    done = threading.Event()
    done.set()
    ai_bridge._SilenceWatch("s3", emit, done).start()
    time.sleep(0.2)
    assert events == []


def test_defaults_are_conservative():
    assert ai_bridge.STALL_AFTER_S == 120
    assert ai_bridge.STALL_AFTER_S < ai_bridge.TIMEOUT_S
