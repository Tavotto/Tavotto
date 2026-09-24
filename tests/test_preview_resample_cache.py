"""预览 SVG 那一遍的图片重采样缓存（`preview_hybrid.preview_resample_cache`）。

2026-09-24 一张用户图：预览 `savefig(svg)` 217 ms 里约 170 ms 是 (a) 面板那张大图的两次 lanczos
重采样，图没变时输入逐字节相同。缓存装在 `matplotlib.image._resample`（纯函数）上，键是它的实参
内容加它自己读的 getter；修后 217 → 61 ms，见 docs/perf-baseline.md。

**比慢更糟的是旧图**，所以判据的主语（worker 解释器里、`tests/support/preview_resample_cache_probe.py`
采的事实）是：
* 同一时刻、同一张 figure，开缓存与不开缓存的 SVG 逐字节相同——未命中、命中、**再命中**（命中时交出
  缓存本体的话，调用方就地一改，第二次命中就是脏的）、换 dpi 都算；
* 图上每种改法各做一次（cmap / clim / alpha 标量与数组 / 插值 / 插值阶段 / resample / filternorm /
  filterrad / origin / extent / 轴位置 / 亚像素平移 / 原地改数据 / mask），rgba 与 data 两个阶段的底图上
  都与不开缓存相同，且**改动真的改变了画面**（否则「相同」恒等成立）；
* 直接调 `_resample`，实参逐维扰动（含 matplotlib 自己从不传的 `alpha=`）：开缓存与原函数逐元素相同。
  有的维度**本来就不影响输出**，只要求相同、不要求变化：mask（C 那一层读的是 `.data`，mask 进键
  是不押注「C 看不看 mask」的保险）；origin 只有 3.11 起 `_resample` 自己读、且只在 nearest 落在像素
  分界上时起作用——那一条（`origin_nearest`）在 3.11 上必须真的改变输出；
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
    "data_stage",
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
    "subpixel_shift",
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
    assert c["hit2_same_as_uncached"], c
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
    per_base = probe["mutations"][mutation]
    assert any(m["changed_picture"] for m in per_base.values()), (
        f"{mutation} 在哪张底图上都没改变画面：「与不开缓存相同」恒等成立，换一个看得出来的改法"
    )
    for base, m in per_base.items():
        assert m["same_as_uncached"], f"{mutation} 之后预览仍是旧图（底图 {base}）"


#: 本来就不影响输出的维度（见模块文档）；其余每一维扰动了必须真的改变输出。origin 只在 nearest
#: 且输出像素落在输入像素分界上时才有作用（`origin_nearest`），lanczos 那条在哪一版都不变
_INERT = {"mask", "origin"}


def test_direct_calls_cover_every_argument(probe):
    direct = probe["direct"]
    inert = _INERT | (set() if probe["reads_origin"] else {"origin_nearest"})
    assert len(direct) >= 20, direct
    for name, r in direct.items():
        assert r["same"], f"{name}：开缓存与原函数不同（缓存键漏了这一维）"
        if name.split(":", 1)[1] not in inert:
            assert r["changed"], f"{name}：扰动没改变输出，这一条什么都没验"


def test_version_gate(probe):
    g = probe["gate"]
    assert g["real_resample_recognised"], (
        f"matplotlib {probe['matplotlib']} 的 _resample 读了认不出的名字"
    )
    assert g["installed"]
    assert g["unknown_read_rejected"]
    assert g["not_a_function_rejected"]


def test_oversized_output_is_neither_stored_nor_copied(probe):
    g = probe["store_gate"]
    assert g["copies_fits"] == 1, g  # 活的尺子：放得下的那次确实拷了
    assert not g["stored_oversized"], g
    assert g["copies_oversized"] == 0, g


def test_input_side_is_bounded_and_layout_independent(probe):
    """Codex #530 第二轮：非连续输入算键时额外内存只有一行（不整份拷贝）；同样的内容换一种内存布局仍命中；
    超过输入上限直通、不缓存。"""
    g = probe["input_side"]
    assert g["key_made"], g  # 活的尺子：这份输入确实走到了算键那一步
    assert g["biggest_copy"] <= g["row_bytes"], g
    assert g["biggest_copy"] < g["input_bytes"] // 100, g
    assert g["hits_across_layouts"], g
    assert g["over_cap_passthrough"], g
