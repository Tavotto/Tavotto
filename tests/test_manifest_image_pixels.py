"""「只为布局」的 draw 不重采样图片（manifest 的 `fig.canvas.draw()`、几何 override 之后的布局刷新）。

2026-09-23 一张用户图（2340×1920 的 PNG 以 lanczos 显示）：热态一次渲染 448 ms，其中那张图被重采样
了三遍中的两遍给 manifest 用——manifest 只要布局与包围盒，图片元素的包围盒由 extent 决定，与像素
无关。`overrides.image_pixels_skipped` 在这两次 draw 期间把**这张 figure 里**图片的 draw / make_image
换成空操作（实例属性，出来即删）。修后 448 → 266 ms（manifest 225 → 39 ms），见 docs/perf-baseline.md。

**判据的主语**（worker 解释器里、`tests/support/manifest_image_probe.py` 采的事实）：
* manifest 期间 `matplotlib.image._resample` 的调用次数（不跳过时必须 ≥ 1，尺子是活的）（不是 wall time，性能数字在共享机器上会偶发红）；
* 与不跳过时的 manifest 相比，差异不超过「不跳过连着两次」本身的浮点噪声、非浮点字段零差异；
* 出来以后实例上没有残留、随后的 SVG 里图片还在；别的 figure 不受牵连；几何 override 那条路径同样不重采样。
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
PROBE = os.path.join(REPO, "tests", "support", "manifest_image_probe.py")


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run(
        [WORKER_PY, PROBE], capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


CASES = ("imshow_colorbar", "figimage", "offsetimage", "constrained")


@pytest.mark.parametrize("case", CASES)
def test_manifest_does_not_resample_images(probe, case):
    c = probe["cases"][case]
    assert c["images"] >= 1 and c["elements"] >= 3, c  # 反证：这张图真的有图片、manifest 真的有内容
    # 活的尺子：不跳过时这张图确实会被重采样，计数器数得到
    assert c["resample_calls_unskipped"] >= 1, c
    assert c["resample_calls_in_manifest"] == 0, c


@pytest.mark.parametrize("case", CASES)
def test_manifest_is_the_same_as_when_images_are_resampled(probe, case):
    c = probe["cases"][case]
    float_diff, other_diff = c["diff_vs_unskipped"]
    assert other_diff == 0, c
    assert float_diff <= max(c["control_noise"], 1e-12), c


@pytest.mark.parametrize("case", CASES)
def test_pixels_come_back_for_the_preview(probe, case):
    c = probe["cases"][case]
    assert c["leftover_instance_patches"] == 0, c
    assert c["svg_image_tags"] >= 1, c


def test_other_figures_are_not_touched(probe):
    assert probe["other_figure_untouched"] is True


def test_layout_refresh_after_a_geometry_override_does_not_resample(probe):
    assert probe["geometry_apply_resample_unskipped"] >= 1, "尺子是死的：不跳过时这条路径也没重采样"
    assert probe["geometry_apply_resample_calls"] == 0


def test_class_level_custom_images_run_their_own_draw(probe):
    """Codex #526：子类在类上重写 draw / make_image 的，布局 draw 里照跑它自己的实现（它的 draw 可能更新几何）；
    什么都没重写的普通子类照样跳过——判据只放过自定义实现，不是把优化整个关掉。"""
    c = probe["custom_images"]
    assert c["patched"] == {"class_draw": False, "class_make_image": False, "plain": True}, c
    assert c["class_draw_ran_in_layout_draw"] >= 1, c
    assert c["extent_after_layout_draw"] == c["expected_extent"], c
