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


def _path(stem, fmt):
    """目标文件键：测试里用 stem.格式 当作磁盘上的那个文件。"""
    return f"/p/{stem}.{fmt}"


def _session():
    return figsession.LiveFigureSession(Path("."))


def _save(s, stem, fig, fmt="pdf", extras=None, source=SAVEFIG):
    """worker 的 savefig 钩子做的两步：先认领（add_figure），再记调用（note_savefig）。"""
    s.add_figure(stem, fig, source)
    s.note_savefig(stem, fig, {"format": fmt}, extras, target=_path(stem, fmt))


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
        s.note_savefig("x", a, {"format": "png"}, target=_path("x", "png"))
        _save(s, "x", b, "pdf")
        assert s.capture["x"] is a
        assert s.savefig_calls["x"] == [{"format": "pdf"}, {"format": "png"}]

    def test_replaced_figure_records_are_dropped(self):
        """甲在每个格式上都被乙覆盖之后才换图；换图时甲的调用与 extras 一并清掉。"""
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a, "pdf", ["art-a"])
        s.note_savefig("x", a, {"format": "png"}, ["art-a2"], target=_path("x", "png"))
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
        s.note_savefig(
            "x", _Fig(), {"format": "pdf"}, target=_path("x", "pdf")
        )  # 又一个写入者撞上 b 写过的路径
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

    def test_no_target_path_never_takes_over(self):
        """BytesIO 之类写不出路径：不参与顶替（与 `call is None` 同理）。"""
        s, a, b = _session(), _Fig(), _Fig()
        _save(s, "x", a)
        s.add_figure("x", b, SAVEFIG)
        s.note_savefig("x", b, {"format": "pdf"}, target=None)
        assert s.capture["x"] is a


class TestSharedFunction:
    """三条入口共用的 `note_savefig_writer` 本体（浏览器 / native 的钩子直接调它）。"""

    @staticmethod
    def _call(w, cap, src, fig, target, stem="x", locked=False):
        cap.setdefault(stem, fig)
        src.setdefault(stem, SAVEFIG)
        return figcapture.note_savefig_writer(
            w, cap, src, {stem: [{}]}, {stem: [None]}, stem, fig, target, locked
        )

    def test_none_target_neither_displaces_nor_registers(self):
        w, cap, src, a, b = {}, {}, {}, _Fig(), _Fig()
        assert self._call(w, cap, src, a, "/p/x.pdf") is False
        assert self._call(w, cap, src, b, "/p/x.pdf") is True
        assert self._call(w, cap, src, a, None) is False
        assert cap["x"] is b and w["x"]["/p/x.pdf"]() is b and None not in w["x"]

    def test_dead_previous_writer_does_not_displace(self):
        w, cap, src, a = {}, {}, {}, _Fig()
        self._call(w, cap, src, a, "/p/x.pdf")
        dead = _Fig()
        w["x"]["/p/x.pdf"] = __import__("weakref").ref(dead)
        del dead
        b = _Fig()
        assert self._call(w, cap, src, b, "/p/x.pdf") is False
        assert cap["x"] is a

    def test_displacement_drops_loser_records(self):
        w, cap, src, a, b = {}, {}, {}, _Fig(), _Fig()
        calls, extras = {"x": [{}]}, {"x": [None]}
        for fig in (a, b):
            cap.setdefault("x", fig)
            src.setdefault("x", SAVEFIG)
            figcapture.note_savefig_writer(w, cap, src, calls, extras, "x", fig, "/p/x.pdf")
        assert cap["x"] is b and "x" not in calls and "x" not in extras


class TestTargetKey:
    def test_format_kwarg_does_not_change_the_file(self, tmp_path, monkeypatch):
        """`savefig("a.pdf")` 与 `savefig("a.pdf", format="png")` 写的是同一个文件。"""
        monkeypatch.chdir(tmp_path)
        k1 = figcapture.savefig_target_key("a.pdf", {})
        k2 = figcapture.savefig_target_key("a.pdf", {"format": "png"})
        assert k1 == k2 is not None
        assert k1 != figcapture.savefig_target_key("a.svg", {})

    def test_relative_and_absolute_spellings_agree(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert figcapture.savefig_target_key("a.pdf", {}) == figcapture.savefig_target_key(
            str(tmp_path / "sub" / ".." / "a.pdf"), {}
        )

    def test_buffer_has_no_key(self):
        import io

        assert figcapture.savefig_target_key(io.BytesIO(), {"format": "png"}) is None

    def test_same_file_different_format_kwarg_is_a_takeover(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        s, a, b = _session(), _Fig(), _Fig()
        for fig, kw in ((a, {}), (b, {"format": "png"})):
            s.add_figure("a", fig, SAVEFIG)
            s.note_savefig(
                "a", fig, {"format": "x"}, None, target=figcapture.savefig_target_key("a.pdf", kw)
            )
        assert s.capture["a"] is b

    def test_same_stem_different_files_is_not_a_takeover(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        s, a, b = _session(), _Fig(), _Fig()
        for fig, name in ((a, "a.pdf"), (b, "a.svg")):
            s.add_figure("a", fig, SAVEFIG)
            s.note_savefig(
                "a", fig, {"format": "x"}, None, target=figcapture.savefig_target_key(name, {})
            )
        assert s.capture["a"] is a


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


def _browser_width_in(out: dict, stem: str) -> float:
    (fig,) = [f for f in out["figures"] if f["stem"] == stem]
    return round(fig["size_mm"][0] / 25.4, 2)


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


# ===========================================================================
# 三条入口同源：浏览器 playground、native bridge 与 desktop worker 结果一致
# ===========================================================================
def test_browser_loop_over_same_path_captures_last(tmp_path):
    from test_compat_capture_parity import browser_load

    if WORKER_PY is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    out = browser_load(LOOP.format(n=3), "loop.py", tmp_path / "ws")
    assert out["ok"], out
    assert [f["stem"] for f in out["figures"]] == ["shape"]
    assert _browser_width_in(out, "shape") == 6.0


def test_browser_same_file_via_format_kwarg_is_same_path(tmp_path):
    from test_compat_capture_parity import browser_load

    if WORKER_PY is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    src = (
        "import matplotlib.pyplot as plt\n"
        "f1, _ = plt.subplots(figsize=(4, 2.6)); f1.savefig('s.pdf')\n"
        "f2, _ = plt.subplots(figsize=(5, 2.6)); f2.savefig('s.pdf', format='png')\n"
    )
    out = browser_load(src, "fmt.py", tmp_path / "ws")
    assert out["ok"], out
    assert _browser_width_in(out, "s") == 5.0
