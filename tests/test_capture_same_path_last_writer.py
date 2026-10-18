"""同一路径多个写入者：捕获的是**最后一次写入**的那张图（Compatibility Guardian G2）。

脚本在终端里跑，`fig.savefig("a.pdf")` 在循环里被不同 Figure 反复写同一个路径，磁盘上留下的
是最后一张；Tavotto 以前对每个 stem 先到先得，静默留下第一张。规则（`LiveFigureSession.note_savefig`）：

* 同一路径（stem + 格式）换了写入者：最后写入者胜，被顶掉那张的 savefig 记账一并清掉；
  同 stem 不同格式不算同一路径（磁盘上各有各的文件），stem 仍归先认领的那张；
  先认领那张在别的格式上仍是最后写入者时不顶掉（它的文件还在磁盘上）；
* 同一张图存两次：仍是一张图；不同 stem：互不影响；
* pyplot 兜底永远不顶掉显式 savefig；
* 已在编辑（`states`）的 stem 不换图。

用图幅（figsize）区分「哪一张」。纯会话规则单测直接驱动 `LiveFigureSession`；
端到端经真 worker（与 `test_compat_capture_parity.py` 同一条纪律）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tavotto.engine import pool
from test_compat_capture_parity import desktop_build, write

ENGINE_DIR = str(Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
import figcapture  # noqa: E402 — figsession 平铺 import 的那一份
import figsession  # noqa: E402

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SAVEFIG = figcapture.SOURCE_SAVEFIG
PYPLOT = figcapture.SOURCE_PYPLOT


# ===========================================================================
# 会话规则：add_figure / note_savefig
# ===========================================================================
class _Fig:
    """可弱引用的替身（`object()` 不行）。"""


def _session():
    return figsession.LiveFigureSession(Path("."))


def _save(s, stem, fig, fmt="pdf", extras=None, source=SAVEFIG):
    """worker 的 savefig 钩子做的两步：先认领（add_figure），再记调用（note_savefig）。"""
    s.add_figure(stem, fig, source)
    s.note_savefig(stem, fig, {"format": fmt}, extras)


class TestSamePathRule:
    def test_last_writer_of_the_same_path_wins(self):
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a)
        _save(s, "x", b)
        assert s.capture["x"] is b

    def test_three_writers_last_wins(self):
        s, figs = _session(), [_Fig(), _Fig(), _Fig()]
        for f in figs:
            _save(s, "x", f)
        assert s.capture["x"] is figs[2]

    def test_returning_writer_wins_again(self):
        s, a, b = _session(), _Fig(), _Fig()
        for f in (a, b, a):
            _save(s, "x", f)
        assert s.capture["x"] is a

    def test_same_figure_twice_is_one_figure(self):
        s, a = _session(), _Fig()
        _save(s, "x", a)
        _save(s, "x", a)
        assert s.capture == {"x": a}
        assert len(s.savefig_calls["x"]) == 2

    def test_different_stems_unaffected(self):
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a)
        _save(s, "y", b)
        assert s.capture == {"x": a, "y": b}

    def test_same_stem_other_format_is_not_a_takeover(self):
        """`a.pdf` 是甲、`a.svg` 是乙：磁盘上各有各的文件，不是同一路径，stem 仍归先认领的甲。"""
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a, "pdf")
        _save(s, "x", b, "svg")
        assert s.capture["x"] is a
        assert s.savefig_calls["x"] == [{"format": "pdf"}]

    def test_holder_still_latest_on_another_format_is_not_displaced(self):
        """甲写 `x.pdf` + `x.png`，乙再写 `x.pdf`：磁盘上 `x.png` 仍是甲，甲不能从捕获里消失。"""
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a, "pdf")
        s.note_savefig("x", a, {"format": "png"})
        _save(s, "x", b, "pdf")
        assert s.capture["x"] is a
        assert s.savefig_calls["x"] == [{"format": "pdf"}, {"format": "png"}]

    def test_replaced_figure_records_are_dropped(self):
        """甲在每个格式上都被乙覆盖之后才换图；换图时甲的调用与 extras 一并清掉。"""
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a, "pdf", ["art-a"])
        s.note_savefig("x", a, {"format": "png"}, ["art-a2"])
        _save(s, "x", b, "pdf", None)
        assert s.capture["x"] is a
        _save(s, "x", b, "png", None)
        assert s.capture["x"] is b
        assert s.savefig_calls["x"] == [{"format": "png"}]
        assert s.savefig_extras["x"] == [None]

    def test_pyplot_claim_is_never_displaced(self):
        s, a, b = _session(), _Fig(), _Fig()
        s.add_figure("x", a, PYPLOT)
        _save(s, "x", b)
        s.note_savefig("x", _Fig(), {"format": "pdf"})  # 又一个写入者撞上 b 写过的路径
        assert s.capture["x"] is a and s.capture_source["x"] == PYPLOT

    def test_pyplot_fallback_does_not_displace_savefig(self):
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a)
        assert s.add_figure("x", b, PYPLOT) is False
        assert s.capture["x"] is a and s.capture_source["x"] == SAVEFIG

    def test_instrumented_stem_keeps_its_figure(self):
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a)
        s.states["x"] = _Fig()
        _save(s, "x", b)
        assert s.capture["x"] is a

    def test_failed_stem_keeps_its_figure(self):
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a)
        s.failures["x"] = {}
        _save(s, "x", b)
        assert s.capture["x"] is a

    def test_unobserved_call_never_takes_over(self):
        """`paper_style.save` 捷径没观察到参数：不知道路径，不顶替（保持先到先得）。"""
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a)
        s.add_figure("x", b, SAVEFIG)
        s.note_savefig("x", b, None)
        assert s.capture["x"] is a


# ===========================================================================
# 端到端：真 worker，figsize 区分是哪一张
# ===========================================================================
LOOP = """\
import matplotlib.pyplot as plt
for i in range({n}):
    fig, ax = plt.subplots(figsize=(4 + i, 2.6))
    ax.set_title(f"cg_pass_{{i}}")
    fig.savefig("shape.pdf")
    plt.close(fig)
"""


def _width_in(built: dict, stem: str) -> float:
    return round(built["stems"][stem]["size_mm"][0] / 25.4, 2)


@needs_worker
class TestRealWorker:
    def test_loop_over_same_path_captures_last(self, tmp_path):
        write(tmp_path, "loop.py", LOOP.format(n=3))
        built = desktop_build(tmp_path, "loop.py")
        assert list(built["stems"]) == ["shape"]
        assert _width_in(built, "shape") == 6.0  # i=2；先到先得时是 4.0

    def test_same_figure_saved_twice_is_one_figure(self, tmp_path):
        write(
            tmp_path,
            "twice.py",
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(4, 2.6))\n"
            "fig.savefig('s.pdf'); fig.savefig('s.pdf')\n",
        )
        built = desktop_build(tmp_path, "twice.py")
        assert list(built["stems"]) == ["s"]
        assert len(built["descriptors"][0]["savefig_calls"]) == 2

    def test_two_figures_two_paths(self, tmp_path):
        write(
            tmp_path,
            "two.py",
            "import matplotlib.pyplot as plt\n"
            "for i, n in enumerate(('a', 'b')):\n"
            "    fig, ax = plt.subplots(figsize=(4 + i, 2.6)); fig.savefig(n + '.pdf')\n",
        )
        built = desktop_build(tmp_path, "two.py")
        assert (_width_in(built, "a"), _width_in(built, "b")) == (4.0, 5.0)

    def test_explicit_savefig_beats_pyplot_fallback(self, tmp_path):
        """显式存过盘的图在前、另有一张只在 pyplot 里的图：stem 与来源不变。"""
        write(
            tmp_path,
            "mix.py",
            "import matplotlib.pyplot as plt\n"
            "f1, _ = plt.subplots(figsize=(4, 2.6)); f1.savefig('shape.pdf')\n"
            "f2, _ = plt.subplots(figsize=(5, 2.6))\n",
        )
        built = desktop_build(tmp_path, "mix.py")
        assert built["stems"]["shape"]["source"] == "savefig"
        assert _width_in(built, "shape") == 4.0
        assert len(built["stems"]) == 2
