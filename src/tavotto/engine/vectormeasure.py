"""桌面改图助手量子图（ADR 0208 第 ③ 期 3b）：把「量一张子图」的原始函数包上进程内缓存与时限。

`la.measure_panels` / `vectoredit._plan` 吃的是 `measure(panel, requests) -> {draw_manifest, data_points}`（插件 `bridge._layout_measure`
同一个形状）；桌面的原始量法在 `app.py`（用桌面引擎会话 `export(..., data_points=…)`，要项目上下文）。这里只管三件事，**纯标准库、不碰引擎**：

* **缓存**：键 = (子图文件、覆盖项的哈希、脚本修订、数据点请求)，进程内、有上限、有 TTL、不落盘。同一张图连续几句话 / 逐条勾选重算都不重复导出；
  脚本改了修订就变，旧条目自然失效。
* **时限**：每张图 `PER_PANEL_S`（20 s）、一次方案总共 `TOTAL_S`（60 s）。`export` 本身不能中途取消，所以每次量交给一个临时线程、
  调用方只等到时限；超时的那次让它自己在 worker 上跑完（结果丢弃），这张图记 `measure_timeout`、退到只有外框。总时限用尽后剩下的图直接 `measure_budget`。
* **只回稳定码**：任何失败都落成 `layoutannotate.PanelUnavailable(码)`，调用方据此退外框并把码带进结果（3c 显示一行）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from . import layoutannotate as la

PER_PANEL_S = 20.0
TOTAL_S = 60.0
CACHE_MAX = 64
CACHE_TTL_S = 30 * 60
#: 一张子图带的覆盖项条数上限（快照来自前端，别让一份畸形请求把导出拖死）
MAX_OVERRIDES = 2000

Measure = Callable[[dict, list], dict]


def overrides_digest(overrides: object) -> str:
    return hashlib.sha1(
        json.dumps(overrides, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


class MeasureCache:
    """进程内的小 LRU（带 TTL）；线程安全。值是量的结果（`draw_manifest` + `data_points`），深拷贝进出。"""

    def __init__(
        self, max_items: int = CACHE_MAX, ttl_s: float = CACHE_TTL_S, clock=time.monotonic
    ):
        self._items: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._max, self._ttl, self._clock = max_items, ttl_s, clock
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            if now - hit[0] > self._ttl:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return copy.deepcopy(hit[1])

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._items[key] = (self._clock(), copy.deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


#: 整个进程一份（桌面一个项目一个后端进程；键里带项目与脚本修订）
CACHE = MeasureCache()


def bounded(
    raw: Measure,
    *,
    key_of: Callable[[dict, list], str | None],
    cache: MeasureCache = CACHE,
    per_panel_s: float = PER_PANEL_S,
    total_s: float = TOTAL_S,
    clock=time.monotonic,
) -> Measure:
    """`raw(panel, requests)` → 带缓存与时限的 `measure`。一次方案造一个（总时限从这里起算）。

    `key_of(panel, requests)` 给缓存键（`None` = 不缓存，如没有脚本修订可取）；它与 `raw` 里的失败一样会被归成 `PanelUnavailable`。"""
    t0 = clock()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vector-measure")

    def measure(panel: dict, requests: list) -> dict:
        overrides = panel.get("overrides")
        if isinstance(overrides, list) and len(overrides) > MAX_OVERRIDES:
            raise la.PanelUnavailable("too_many_overrides")
        key = None
        try:
            key = key_of(panel, requests)
        except la.PanelUnavailable:
            raise
        except Exception:  # noqa: BLE001 - 取不到键只是不缓存
            key = None
        if key is not None and (hit := cache.get(key)) is not None:
            return hit
        left = total_s - (clock() - t0)
        if left <= 0:
            raise la.PanelUnavailable("measure_budget")
        fut = pool.submit(raw, panel, requests)
        try:
            out = fut.result(timeout=min(per_panel_s, left))
        except FutureTimeout:
            fut.cancel()
            raise la.PanelUnavailable("measure_timeout") from None
        except la.PanelUnavailable:
            raise
        except Exception:  # noqa: BLE001 - 引擎层任何失败都退外框，不让一张图的失败拖垮整次方案
            raise la.PanelUnavailable("measure_failed") from None
        if key is not None:
            cache.put(key, out)
        return out

    return measure
