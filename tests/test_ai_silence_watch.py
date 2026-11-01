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


def test_silence_fires_once_then_resets_on_output(monkeypatch):
    monkeypatch.setattr(ai_bridge, "STALL_AFTER_S", 0.1)
    monkeypatch.setattr(ai_bridge, "STALL_POLL_S", 0.02)
    events, emit = _collect()
    done = threading.Event()
    w = ai_bridge._SilenceWatch("s1", emit, done)
    w.start()
    try:
        assert _wait_for(lambda: len(events) >= 1)
        time.sleep(0.2)  # 持续静默也只提示一次
        assert [e for e in events if e[1]["stalled"]] == [events[0]]
        assert events[0][0] == "ai.stalled"
        assert events[0][1]["session"] == "s1" and events[0][1]["idle_s"] >= 0
        w.touch()
        assert events[-1][1] == {"session": "s1", "stalled": False, "idle_s": 0}
    finally:
        done.set()


def test_steady_output_never_fires(monkeypatch):
    monkeypatch.setattr(ai_bridge, "STALL_AFTER_S", 0.15)
    monkeypatch.setattr(ai_bridge, "STALL_POLL_S", 0.02)
    events, emit = _collect()
    done = threading.Event()
    w = ai_bridge._SilenceWatch("s2", emit, done)
    w.start()
    for _ in range(15):
        w.touch()
        time.sleep(0.02)
    done.set()
    assert events == []


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
