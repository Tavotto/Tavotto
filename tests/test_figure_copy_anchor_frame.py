"""ADR 0207 PR 3：脚本按 `bbox_inches='tight'` 存盘时，整图 / 子图副本的落点换算（前端 `copyPlacementOf`）。

manifest 的锚点 / 外框量在 savefig 图幅（`manifest.frame`，ADR 0098）里，引擎把整图副本的 `at` 按
`fig.transFigure`（figsize）解读——差一个图幅的偏移与缩放。前端按 `frame.figsize_mm / savefig_mm` 把图幅分数
换回 figsize 分数再写 `at`（`frameFracToFigsize`）。这里用同一套公式的 Python 镜像去验证：副本外框 = 源外框
右下错开 4 mm（图幅 mm 里）。子图宿主的 `position` 与锚点同在图幅里，axes 分数不需要换算。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool
from test_figure_copy_override import STEM, WORKER_PY, _copy_patch, _els  # noqa: F401

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(6, 4))
    fig.subplots_adjust(left=0.35, right=0.8, bottom=0.3, top=0.8)
    ax.plot([0, 1], [0, 1])
    ax.set_title("Title")
    ax.set_ylabel("Signal")
    ax.text(0.3, 0.6, "data text")
    fig.text(0.45, 0.12, "fig text")
    fig.text(0.1, 0.9, "corner")
    fig.savefig("Dup.pdf", bbox_inches="tight", pad_inches=0.1)
"""

OFFSET_MM = 4.0


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    d = tmp_path_factory.mktemp("dup-frame")
    (d / "dup.py").write_text(SCRIPT, encoding="utf-8")
    w = pool.one_shot("dup.py", str(d), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _front_at(resp: dict, host: str, src: str) -> list[float]:
    """前端 `copyPlacementOf` 的 Python 镜像：锚点右下错开 4 mm（图幅 mm）→ 宿主分数。"""
    m = resp["manifest"]
    els = _els(resp)
    w_mm, h_mm = m["size_mm"]
    ax_, ay_ = els[src]["anchor"]
    x, y = ax_ + OFFSET_MM / w_mm, ay_ + OFFSET_MM / h_mm  # 图幅分数，y 向下
    if host == "fig":
        f = m.get("frame")
        if f and f["source"] == "savefig" and f["active"]:
            gw, gh = f["figsize_mm"]
            left, top, fw, fh = f["savefig_mm"]
            x, y = (left + x * fw) / gw, (top + y * fh) / gh
        return [x, 1.0 - y]
    px, py, pw, ph = next(f["value"] for f in els[host]["editable"] if f["prop"] == "position")
    return [(x - px) / pw, ((1.0 - y) - py) / ph]


@pytest.mark.parametrize(
    "host,src,text",
    [
        ("fig", "fig.texts_0", "fig text"),
        ("fig", "fig.texts_1", "corner"),
        ("axes_0", "axes_0.title", "Title"),
        ("axes_0", "axes_0.ylabel", "Signal"),
        ("axes_0", "axes_0.texts_0", "data text"),
    ],
)
def test_copy_is_the_source_shifted_4mm_under_a_savefig_frame(worker, host, src, text):
    base = worker.override(STEM, [])
    frame = base["manifest"].get("frame")
    assert frame and frame["active"], "夹具没造出生效的 savefig 图幅"
    assert frame["savefig_mm"][0] != 0 or frame["savefig_mm"][1] != 0
    cp = _copy_patch(host, src, src, text, _front_at(base, host, src))
    resp = worker.override(STEM, [cp])
    assert resp["warnings"] == []
    w_mm, h_mm = base["manifest"]["size_mm"]
    sx, sy, sw, sh = _els(base)[src]["bbox"]
    want = [sx + OFFSET_MM / w_mm, sy + OFFSET_MM / h_mm, sw, sh]
    got = _els(resp)[cp["gid"]]["bbox"]
    # 容差 5e-4（图幅分数）：`savefig_mm` 只报到 0.01 mm
    assert got == pytest.approx(want, abs=5e-4), f"{src}：副本 {got}，期望 {want}"
    worker.override(STEM, [])


def test_only_flipping_y_would_miss(worker):
    """反面：整图文字只翻 y、不按图幅换算（Codex #2163 说的旧做法）会落到别处——证明上面的断言在区分。"""
    base = worker.override(STEM, [])
    src = "fig.texts_0"
    ax_, ay_ = _els(base)[src]["anchor"]
    w_mm, h_mm = base["manifest"]["size_mm"]
    naive = [ax_ + OFFSET_MM / w_mm, 1.0 - (ay_ + OFFSET_MM / h_mm)]
    cp = _copy_patch("fig", src, src, "fig text", naive)
    got = _els(worker.override(STEM, [cp]))[cp["gid"]]["bbox"]
    sx, sy = _els(base)[src]["bbox"][:2]
    assert (
        abs(got[0] - (sx + OFFSET_MM / w_mm)) > 5e-3 or abs(got[1] - (sy + OFFSET_MM / h_mm)) > 5e-3
    )
    worker.override(STEM, [])
