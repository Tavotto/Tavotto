"""桌面量子图的缓存与时限（`engine/vectormeasure.bounded`，ADR 0208 第 ③ 期 3b）：纯函数，用假的原始量法与假时钟。"""

from __future__ import annotations

import threading
import time

import pytest

from tavotto.engine import layoutannotate as la, vectormeasure as vm

PANEL = {"id": "o_p", "type": "panel", "fileId": "f.pdf", "overrides": []}
OK = {"draw_manifest": {"size_mm": [1, 1], "elements": []}, "data_points": []}


def _bounded(raw, **kw):
    kw.setdefault(
        "key_of", lambda p, r: f"{p['fileId']}|{vm.overrides_digest(p.get('overrides'))}|{len(r)}"
    )
    kw.setdefault("cache", vm.MeasureCache())
    return vm.bounded(raw, **kw)


def test_the_second_identical_measure_is_a_cache_hit():
    calls = []

    def raw(panel, requests):
        calls.append(1)
        return OK

    m = _bounded(raw)
    assert m(PANEL, []) == OK and m(PANEL, []) == OK
    assert len(calls) == 1
    m({**PANEL, "overrides": [{"gid": "g", "prop": "x", "value": 1}]}, [])  # 覆盖项不同 = 另一条
    assert len(calls) == 2


def test_the_cache_returns_copies_so_callers_cannot_poison_it():
    m = _bounded(lambda p, r: {"draw_manifest": {"a": [1]}, "data_points": []})
    got = m(PANEL, [])
    got["draw_manifest"]["a"].append(2)
    assert m(PANEL, [])["draw_manifest"]["a"] == [1]


def test_cache_is_bounded_and_expires():
    now = [0.0]
    c = vm.MeasureCache(max_items=2, ttl_s=10, clock=lambda: now[0])
    for k in "abc":
        c.put(k, {"k": k})
    assert c.get("a") is None and c.get("c") == {"k": "c"}  # 最老的被挤掉
    now[0] = 11
    assert c.get("c") is None  # 过期


def test_a_slow_panel_times_out_to_the_frame_without_blocking_the_rest():
    gate = threading.Event()

    def raw(panel, requests):
        if panel["fileId"] == "slow.pdf":
            gate.wait(5)
        return OK

    m = _bounded(raw, per_panel_s=0.2)
    t0 = time.monotonic()
    with pytest.raises(la.PanelUnavailable) as e:
        m({**PANEL, "fileId": "slow.pdf"}, [])
    assert e.value.code == "measure_timeout" and time.monotonic() - t0 < 2
    assert m({**PANEL, "fileId": "fast.pdf"}, []) == OK  # 别的图不受影响
    gate.set()


def test_the_total_budget_runs_out():
    now = [0.0]

    def raw(panel, requests):
        now[0] += 25  # 每张图「用掉」25 s
        return OK

    m = _bounded(raw, total_s=60, clock=lambda: now[0])
    m({**PANEL, "fileId": "a"}, [])
    m({**PANEL, "fileId": "b"}, [])
    m({**PANEL, "fileId": "c"}, [])  # 50 s 时还有 10 s
    with pytest.raises(la.PanelUnavailable) as e:
        m({**PANEL, "fileId": "d"}, [])
    assert e.value.code == "measure_budget"


def test_failures_become_stable_codes_and_are_not_cached():
    calls = []

    def raw(panel, requests):
        calls.append(1)
        if panel["fileId"] == "frozen":
            raise la.PanelUnavailable("composed_from_snapshot")
        raise RuntimeError("boom")

    m = _bounded(raw)
    with pytest.raises(la.PanelUnavailable) as e:
        m({**PANEL, "fileId": "frozen"}, [])
    assert e.value.code == "composed_from_snapshot"
    for _ in range(2):
        with pytest.raises(la.PanelUnavailable) as e:
            m(PANEL, [])
        assert e.value.code == "measure_failed"
    assert len(calls) == 3


def test_a_missing_cache_key_just_skips_the_cache():
    calls = []
    m = _bounded(lambda p, r: calls.append(1) or OK, key_of=lambda p, r: None)
    m(PANEL, [])
    m(PANEL, [])
    assert len(calls) == 2


def test_absurd_overrides_are_refused_before_any_export():
    m = _bounded(lambda p, r: pytest.fail("不该量"))
    with pytest.raises(la.PanelUnavailable) as e:
        m({**PANEL, "overrides": [{}] * (vm.MAX_OVERRIDES + 1)}, [])
    assert e.value.code == "too_many_overrides"


def _measure_threads():
    return [
        t for t in threading.enumerate() if t.name.startswith("vector-measure") and t.is_alive()
    ]


def test_close_stops_the_pool_workers_so_no_non_daemon_thread_outlives_the_plan():
    """`bounded` 起的线程池 worker 是非 daemon 线程；不 `close` 就一直活到池被 GC，
    pytest 会话末尾的 conftest 线程门禁据此判红（CI 上「摘要全绿、退出码 1」）。"""
    before = set(_measure_threads())
    m = _bounded(lambda p, r: OK)
    m(PANEL, [])
    mine = [t for t in _measure_threads() if t not in before]
    assert mine, "测试前提：量过一次后池里有 worker"
    vm.close(m)
    deadline = time.monotonic() + 5
    while any(t.is_alive() for t in mine) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(t.is_alive() for t in mine)
    vm.close(m)  # 重复收不抛
