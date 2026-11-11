"""箭头改虚线：只虚杆，头保持实线（TVD-AVZ0-F9EX-Q697-3VDM）。

matplotlib 对 `FancyArrowPatch.set_linestyle` 的画法是杆与头一起虚——`->` 的两笔头被虚线
切碎、`-|>` 的实心三角描边断开，用户看到「箭头头也变成虚线、显示不正常」。引擎的
`("arrowpatch", "linestyle")` setter 改成只虚杆；还原时钩子拆干净、回到 matplotlib 原样。

主语：真 Agg renderer 上**这支箭头**每条 path 落笔时 gc 的虚线状态（不是 artist 的
linestyle 属性——属性一直是 `--`，判它恒真）。引擎在 worker 解释器里跑，与热会话 / 重放同一份代码。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ENGINE_DIR = Path(__file__).resolve().parent.parent / "src" / "tavotto" / "engine"

_DRIVER = """\
import json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import RendererAgg
from matplotlib.patches import FancyArrowPatch
import overrides

setter = overrides.HANDLERS[("arrowpatch", "linestyle")][1]
restore = overrides._RESTORE[("arrowpatch", "linestyle")]

calls = None
native = RendererAgg.draw_path
def spy(self, gc, path, tr, rgbFace=None):
    if calls is not None:
        calls.append(gc.get_dashes()[1] is not None)
    return native(self, gc, path, tr, rgbFace)
RendererAgg.draw_path = spy

def strokes(fig, arrow):
    global calls
    prev = vars(arrow).get("draw")  # 引擎的钩子（若有）：测完原样放回，不许顺手拆掉
    real = arrow.draw
    def draw(renderer):
        global calls
        calls = []
        real(renderer)
        out = calls
        calls = None
        draw.out = out
    arrow.draw = draw
    calls = None
    fig.canvas.draw()
    if prev is None:
        del arrow.draw
    else:
        arrow.draw = prev
    return draw.out

out = {}
for style in ["->", "-|>", "<->", "]-[", "simple"]:
    fig, ax = plt.subplots()
    ax.axis("off")
    a = FancyArrowPatch((0.1, 0.5), (0.9, 0.5), arrowstyle=style, mutation_scale=20)
    ax.add_patch(a)
    a.set_linestyle("--")
    native_dash = strokes(fig, a)
    a.set_linestyle("-")
    setter(a, "--")
    edited = strokes(fig, a)
    restore(a, "-")
    hooked = "draw" in vars(a) or "_mm_solid_head_hook" in vars(a)
    a.set_linestyle("--")
    after_restore = strokes(fig, a)
    out[style] = {"native": native_dash, "edited": edited,
                  "hooked_after_restore": hooked, "after_restore": after_restore}
    plt.close(fig)
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def strokes():
    res = subprocess.run(
        [WORKER_PY, "-c", _DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("style", ["->", "-|>", "<->", "]-["])
def test_dashed_shaft_solid_heads(strokes, style):
    got = strokes[style]
    # 前提：matplotlib 原样是杆与头一起虚（否则这条用例什么也没证明）
    assert len(got["native"]) >= 2 and all(got["native"]), got
    # 改后：杆虚、每个头实
    assert got["edited"][0] is True, got
    assert len(got["edited"]) == len(got["native"]), got
    assert not any(got["edited"][1:]), got


@pytest.mark.parametrize("style", ["->", "simple"])
def test_restore_removes_hook(strokes, style):
    got = strokes[style]
    assert got["hooked_after_restore"] is False, got
    # 还原后脚本自己设的虚线回到 matplotlib 原样（头也虚）：引擎不改用户原画法
    assert got["after_restore"] == got["native"], got


def test_single_outline_styles_stay_dashed(strokes):
    # simple / fancy / wedge 杆头是一条轮廓：只有一条 path，照旧虚线
    got = strokes["simple"]
    assert got["edited"] == [True], got
