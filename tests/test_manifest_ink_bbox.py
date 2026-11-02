"""manifest 的 `figure.ink_bbox`（ADR 0161 §2）：图的内容边缘，「紧凑」停靠点的依据。

`fig.get_tightbbox(renderer)`——matplotlib `bbox_inches="tight"` 用的同一个框，**不加 pad**——
换成相对 manifest 对外报的图幅的分数（y 向下）。钉住：

1. 只出现在 `gid == "figure"` 上，形状是四个有限数 `[x0, y0, x1, y1]`，落在 [0, 1] 内且比整幅小；
2. 与 matplotlib 自己的 tight 框同值（独立在 worker 解释器里再量一遍对拍）；
3. 有 savefig frame（ADR 0098）时相对 **frame** 而不是 figsize：`pad_inches=0.1` 的 tight frame，
   内容边缘到图幅边缘恰为 0.1 in；
4. 量不出（`get_tightbbox` 抛）时字段缺席，不猜。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

PLAIN = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0), dpi=100)
    fig.subplots_adjust(left=0.25, right=0.8, bottom=0.3, top=0.8)
    ax.plot([0, 1, 2], [1, 3, 2])
    ax.set_xlabel("x label")
    ax.set_ylabel("y label")
    fig.savefig("Plain.pdf")
"""

#: 脚本自己用 tight + 0.1 in 的 pad 存盘 → 有 frame；内容边缘离图幅边恰好 0.1 in
FRAMED = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0), dpi=100)
    ax.plot([0, 1, 2], [1, 3, 2])
    ax.set_xlabel("x label")
    ax.set_ylabel("y label")
    fig.savefig("Framed.pdf", bbox_inches="tight", pad_inches=0.1)
"""

#: 另一个脚本：get_tightbbox 被替换成抛异常，字段不该出现
BROKEN = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0), dpi=100)
    ax.plot([0, 1, 2], [1, 3, 2])

    def boom(*a, **k):
        raise RuntimeError("no tight bbox")

    fig.get_tightbbox = boom
    fig.savefig("Broken.pdf")
"""


def _manifest(tmp_path_factory, name, source):
    figs = tmp_path_factory.mktemp(name)
    script = f"fig_{name}.py"
    (figs / script).write_text(source, encoding="utf-8")
    w = pool.one_shot(script, str(figs), "main")
    try:
        w.ensure_built()
        resp = w.override(name.capitalize(), [])
        return resp["manifest"]
    finally:
        pool.discard(w)


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    return _manifest(tmp_path_factory, "plain", PLAIN)


@pytest.fixture(scope="module")
def framed(tmp_path_factory):
    return _manifest(tmp_path_factory, "framed", FRAMED)


@pytest.fixture(scope="module")
def broken(tmp_path_factory):
    return _manifest(tmp_path_factory, "broken", BROKEN)


def _fig(man):
    return next(e for e in man["elements"] if e["gid"] == "figure")


def test_ink_bbox_only_on_the_figure_element(plain):
    assert [e["gid"] for e in plain["elements"] if "ink_bbox" in e] == ["figure"]


def test_ink_bbox_is_four_finite_extents_inside_the_figure_and_smaller_than_it(plain):
    ink = _fig(plain)["ink_bbox"]
    assert len(ink) == 4
    x0, y0, x1, y1 = ink
    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0, ink
    # 比 figure 的 bbox [0, 0, 1, 1] 小：子图留了边距，内容没铺满
    assert (x1 - x0) < 1.0 and (y1 - y0) < 1.0, ink
    # 子图框 left=0.25 right=0.8 bottom=0.3 top=0.8，轴标题在外面：内容边缘必须包住子图框
    ax = next(e for e in plain["elements"] if e["gid"] == "axes_0")["bbox"]
    assert x0 <= ax[0] + 1e-6 and x1 >= ax[0] + ax[2] - 1e-6
    assert y0 <= ax[1] + 1e-6 and y1 >= ax[1] + ax[3] - 1e-6


def test_ink_bbox_matches_a_tight_bbox_measured_independently(plain, tmp_path):
    """对拍：另起一个解释器按同一份脚本量 `get_tightbbox`，换算成 y 向下分数。"""
    import json
    import subprocess

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json\nimport matplotlib\nmatplotlib.use('Agg')\n"
        + "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(4.0, 3.0), dpi=100)\n"
        "fig.subplots_adjust(left=0.25, right=0.8, bottom=0.3, top=0.8)\n"
        "ax.plot([0, 1, 2], [1, 3, 2]); ax.set_xlabel('x label'); ax.set_ylabel('y label')\n"
        "fig.canvas.draw()\n"
        "bb = fig.get_tightbbox(fig.canvas.get_renderer())\n"
        "print(json.dumps([bb.x0/4.0, 1-bb.y1/3.0, bb.x1/4.0, 1-bb.y0/3.0]))\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [WORKER_PY, str(probe)], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert out.returncode == 0, out.stdout + out.stderr
    want = json.loads(out.stdout.strip().splitlines()[-1])
    # 字体度量在 Agg 与矢量 renderer 之间差零点几像素
    assert _fig(plain)["ink_bbox"] == pytest.approx(want, abs=3e-3)


def test_ink_bbox_with_a_savefig_frame_is_relative_to_the_frame(framed):
    assert "frame" in framed, "夹具没造出 savefig frame"
    w_mm, h_mm = framed["size_mm"]
    # tight + pad 0.1 in：内容边缘离图幅四边各 0.1 in = 2.54 mm
    ink = _fig(framed)["ink_bbox"]
    x0, y0, x1, y1 = ink
    assert x0 == pytest.approx(2.54 / w_mm, abs=3e-3), ink
    assert y0 == pytest.approx(2.54 / h_mm, abs=3e-3), ink
    assert x1 == pytest.approx(1 - 2.54 / w_mm, abs=3e-3), ink
    assert y1 == pytest.approx(1 - 2.54 / h_mm, abs=3e-3), ink


def test_ink_bbox_is_absent_when_the_tight_bbox_cannot_be_measured(broken):
    assert "ink_bbox" not in _fig(broken)
    assert _fig(broken)["bbox"] == [0.0, 0.0, 1.0, 1.0]  # 其余照常
