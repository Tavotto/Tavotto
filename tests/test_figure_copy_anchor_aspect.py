"""ADR 0207 PR 3：副本落点换算（前端 `copyPlacementOf`）对「请求框 ≠ 画出来的框」的子图也成立。

前端用 manifest 里宿主子图的 `position` 把源画出来的锚点换成 axes 分数。`set_aspect("equal")` 会让
子图画在比请求框小的那一块里（`apply_aspect` 改 active position）——`position` 报的若是请求框，副本会落歪。
这里用 PR 1 同一把换算（`_anchor_at`）把副本放在源自己的锚点上，要求副本外框与源外框重合。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool
from test_figure_copy_override import (  # noqa: F401
    STEM,
    WORKER_PY,
    _anchor_at,
    _copy_patch,
    _els,
)

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.plot([0, 10], [0, 1])
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.set_title("Title")
    ax.set_ylabel("Signal")
    ax.text(2, 0.5, "data text")
    fig.savefig("Dup.pdf")
"""


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    d = tmp_path_factory.mktemp("dup-aspect")
    (d / "dup.py").write_text(SCRIPT, encoding="utf-8")
    w = pool.one_shot("dup.py", str(d), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


@pytest.mark.parametrize(
    "src,text",
    [("axes_0.title", "Title"), ("axes_0.ylabel", "Signal"), ("axes_0.texts_0", "data text")],
)
def test_copy_at_the_anchor_lands_on_the_source_when_aspect_shrinks_the_axes(worker, src, text):
    base = worker.override(STEM, [])
    cp = _copy_patch("axes_0", src, src, text, _anchor_at(base, "axes_0", src))
    resp = worker.override(STEM, [cp])
    assert resp["warnings"] == []
    got = _els(resp)[cp["gid"]]["bbox"]
    want = _els(base)[src]["bbox"]
    assert got == pytest.approx(want, abs=2e-3)
    worker.override(STEM, [])
