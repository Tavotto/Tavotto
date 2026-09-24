"""预览 SVG 那一遍的图片重采样缓存（`preview_hybrid.preview_resample_cache`）。

2026-09-24 一张用户图：预览 `savefig(svg)` 217 ms 里约 170 ms 是 (a) 面板那张大图的两次 lanczos
重采样，图没变时输入逐字节相同。缓存装在 `matplotlib.image._resample`（纯函数）上，键是它的实参
内容加它自己读的 getter；修后 217 → 61 ms，见 docs/perf-baseline.md。

**比慢更糟的是旧图**，所以判据的主语（worker 解释器里、`tests/support/preview_resample_cache_probe.py`
采的事实）是：
* 同一时刻、同一张 figure，开缓存与不开缓存的 SVG 逐字节相同——未命中那一遍、命中那一遍、换 dpi 都算；
* 缓存键的每一维各改一次（cmap / clim / alpha 标量与数组 / 插值 / 插值阶段 / resample / filternorm /
  filterrad / origin / extent / 轴位置 / 原地改数据 / mask），改完仍与不开缓存相同，且**改动真的改变了画面**
  （否则「相同」恒等成立）；
* 命中时 C 那一层的 `_image.resample` 一次都不调（活的尺子：不开缓存时 ≥ 1）；
* 版本闸：`_resample` 引用了认不出的名字（新版本多读了一个属性）就不装。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "tests", "support", "preview_resample_cache_probe.py")

BASES = (
    "rgb_uint8",
    "scalar_masked",
    "alpha_array",
    "origin_lower_nearest",
    "auto_interp",
    "rgba_stage",
    "figimage",
)
MUTATIONS = (
    "cmap",
    "clim",
    "alpha_scalar",
    "alpha_array",
    "interpolation",
    "interpolation_stage",
    "resample",
    "filternorm",
    "filterrad",
    "origin",
    "extent",
    "axes_position",
    "data_inplace",
    "mask",
)


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run(
        [WORKER_PY, PROBE], capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_probe_covers_every_case(probe):
    # 清单两侧对齐：探针加了 / 删了一种而这里没跟上，参数化就会静默少跑
    assert set(probe["bases"]) == set(BASES)
    assert set(probe["mutations"]) == set(MUTATIONS)


@pytest.mark.parametrize("case", BASES)
def test_cached_preview_is_byte_identical(probe, case):
    c = probe["bases"][case]
    assert c["uncached_stable"], c  # 尺子本身稳定（hashsalt 钉住了）
    assert c["miss_same_as_uncached"], c
    assert c["hit_same_as_uncached"], c
    assert c["dpi_changed_picture"] and c["dpi_same_as_uncached"], c


@pytest.mark.parametrize("case", BASES)
def test_second_preview_does_not_resample(probe, case):
    c = probe["bases"][case]
    assert c["calls_uncached"] >= 1, c  # 活的尺子
    assert c["calls_miss"] == c["calls_uncached"], c
    assert c["calls_hit"] == 0, c
    # 出了 `preview_resample_cache()` 就直通：导出 / manifest 不吃缓存
    assert c["calls_uncached_after"] == c["calls_uncached"], c


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_changes_are_never_served_stale(probe, mutation):
    m = probe["mutations"][mutation]
    assert m["changed_picture"], f"{mutation} 没改变画面：「与不开缓存相同」恒等成立，换一个看得出来的改法"
    assert m["same_as_uncached"], f"{mutation} 之后预览仍是旧图"


def test_version_gate(probe):
    g = probe["gate"]
    assert g["real_resample_recognised"], f"matplotlib {probe['matplotlib']} 的 _resample 读了认不出的名字"
    assert g["installed"]
    assert g["unknown_read_rejected"]
    assert g["not_a_function_rejected"]
