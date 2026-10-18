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

import os
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
        # 甲的调用清掉；乙先前被忽略的 pdf 调用从影子日志里带回来，再加这一次 png
        assert s.savefig_calls["x"] == [{"format": "pdf"}, {"format": "png"}]
        assert s.savefig_extras["x"] == [None, None]

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


PDF = {"format": "pdf"}


class _State:
    """`note_savefig_writer` 的五张表，测试里手动驱动。"""

    def __init__(self):
        self.w, self.cap, self.src, self.calls, self.extras = {}, {}, {}, {}, {}

    def claim(self, fig, stem="x"):
        self.cap.setdefault(stem, fig)
        self.src.setdefault(stem, SAVEFIG)

    def save(self, fig, target, call=PDF, extra=None, stem="x", locked=False, verify=None):
        return figcapture.note_savefig_writer(
            self.w,
            self.cap,
            self.src,
            self.calls,
            self.extras,
            stem,
            fig,
            target,
            call,
            extra,
            locked,
            verify,
        )


class TestSharedFunction:
    """三条入口共用的 `note_savefig_writer` 本体（浏览器 / native 的钩子直接调它）。"""

    def test_none_target_neither_displaces_nor_registers(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        assert st.save(a, "/p/x.pdf") is False
        assert st.save(b, "/p/x.pdf") is True
        assert st.save(a, None) is False
        assert st.cap["x"] is b and None not in st.w["x"]["files"]

    def test_first_claimant_without_any_successful_write_is_replaced(self):
        """Codex r4305617407：B 先认领但保存失败（没有写入记录），A 随后成功——捕获 A，调用是 A 的。"""
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(b)  # B 认领了 stem，但它的保存失败，不调用 note_savefig_writer
        assert st.save(a, "/p/x.pdf", PDF, "art") is True
        assert st.cap["x"] is a
        assert st.calls["x"] == [PDF] and st.extras["x"] == ["art"]

    def test_holder_without_writes_is_kept_by_non_savefig_source(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.cap["x"], st.src["x"] = b, PYPLOT
        assert st.save(a, "/p/x.pdf") is False and st.cap["x"] is b
        assert "x" not in st.calls  # 持有者不是 a：不写 calls[stem]

    def test_unlocked_only(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(b)
        assert st.save(a, "/p/x.pdf", locked=True) is False and st.cap["x"] is b

    def test_same_writer_rewriting_its_file_is_not_a_takeover(self):
        st, a = _State(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf")
        assert st.save(a, "/p/x.pdf") is False
        assert st.calls["x"] == [PDF, PDF]

    def test_holder_rewriting_after_a_foreign_write_keeps_its_records(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf")
        st.save(a, "/p/x.png", {"format": "png"})
        assert st.save(b, "/p/x.pdf") is False  # a 在 png 上仍是最近写入者
        assert st.save(a, "/p/x.pdf") is False
        assert [c["format"] for c in st.calls["x"]] == ["pdf", "png", "pdf"]

    def test_displacement_swaps_in_the_winners_own_table(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf", PDF, "a-art")
        assert st.save(b, "/p/x.pdf", PDF, "b-art") is True
        assert st.calls["x"] == [PDF] and st.extras["x"] == ["b-art"]

    def test_unknown_call_is_unknown_for_that_writer_only(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf")
        st.save(a, None, None)  # 没观察到参数
        assert st.calls["x"] is None and "x" not in st.extras
        st.save(b, "/p/x.pdf")
        assert st.cap["x"] is b and st.calls["x"] == [PDF]

    def test_unclaimed_stem_is_not_displaced(self):
        st, a = _State(), _Fig()
        st.src["x"] = SAVEFIG
        st.save(a, "/p/x.pdf")
        st.save(_Fig(), "/p/x.pdf")
        assert st.cap == {}

    def test_hundred_alternations_keep_the_winners_first_call(self):
        """Codex r4305617412：交替很多次后，胜者最早的决定性调用（首次 tight）仍在。"""
        st, a, b = _State(), _Fig(), _Fig()
        tight = {"format": "pdf", "bbox_inches": "tight"}
        st.claim(a)
        st.save(a, "/p/x.pdf", tight)
        for _ in range(100):
            st.save(b, "/p/x.pdf")
            st.save(a, "/p/x.pdf")
        assert st.cap["x"] is a
        assert st.calls["x"][0] == tight
        assert len(st.calls["x"]) <= figcapture.MAX_SAVEFIG_CALLS_HARD

    def test_writer_tables_are_bounded_and_keep_holder_and_latest(self):
        st, held = _State(), _Fig()
        st.claim(held)
        st.save(held, "/p/x.pdf")
        figs = []
        for i in range(figcapture.MAX_SAVEFIG_WRITERS * 3):
            f = _Fig()
            figs.append(f)
            st.save(f, f"/p/x{i}.png", {"format": "png"})
        by = st.w["x"]["by"]
        assert len(by) <= figcapture.MAX_SAVEFIG_WRITERS
        assert id(held) in by and id(figs[-1]) in by  # 持有者与最新的写入者不被淘汰
        assert len(st.w["x"]["files"]) <= figcapture.MAX_SAVEFIG_WRITERS + 1


class TestAlternatingWriters:
    """Codex 4305499990：写入者跨格式交替，顶回来的图要带回它自己的全部调用。"""

    PNG = {"format": "png"}

    def _seq(self, s, a, b, a_png_extra="png-art"):
        s.add_figure("x", a, SAVEFIG)
        s.note_savefig("x", a, PDF, "pdf-a1", target=_path("x", "pdf"))
        s.add_figure("x", b, SAVEFIG)
        s.note_savefig("x", b, PDF, "pdf-b", target=_path("x", "pdf"))
        s.note_savefig("x", a, self.PNG, a_png_extra, target=_path("x", "png"))
        s.note_savefig("x", a, PDF, "pdf-a2", target=_path("x", "pdf"))

    def test_winner_gets_back_its_ignored_calls(self):
        s, a, b = _session(), _Fig(), _Fig()
        self._seq(s, a, b)
        assert s.capture["x"] is a
        assert [c["format"] for c in s.savefig_calls["x"]] == ["pdf", "png", "pdf"]
        assert s.savefig_extras["x"] == ["pdf-a1", "png-art", "pdf-a2"]

    def test_loser_calls_are_not_resurrected_for_others(self):
        s, a, b = _session(), _Fig(), _Fig()
        s.add_figure("x", a, SAVEFIG)
        s.note_savefig("x", a, PDF, None, target=_path("x", "pdf"))
        s.add_figure("x", b, SAVEFIG)
        s.note_savefig("x", b, PDF, None, target=_path("x", "pdf"))
        assert s.capture["x"] is b and s.savefig_calls["x"] == [PDF]

    def test_unknown_call_in_winner_history_rebuilds_as_unknown(self):
        s, a, b = _session(), _Fig(), _Fig()
        s.add_figure("x", a, SAVEFIG)
        s.note_savefig("x", a, PDF, None, target=_path("x", "pdf"))
        s.note_savefig("x", a, None)  # paper_style.save：参数没观察到
        s.add_figure("x", b, SAVEFIG)
        s.note_savefig("x", b, PDF, None, target=_path("x", "pdf"))
        s.note_savefig("x", a, PDF, None, target=_path("x", "pdf"))
        assert s.capture["x"] is a and s.savefig_calls["x"] is None
        assert "x" not in s.savefig_extras  # 整份「不知道」：不留与调用错位的 extras

    def test_unobserved_call_with_a_path_never_takes_over(self):
        s, a, b = _session(), _Fig(), _Fig()
        s.add_figure("x", a, SAVEFIG)
        s.note_savefig("x", a, PDF, None, target=_path("x", "pdf"))
        s.add_figure("x", b, SAVEFIG)
        s.note_savefig("x", b, PDF, None, target=_path("x", "pdf"))
        s.note_savefig("x", a, None, None, target=_path("x", "pdf"))
        assert s.capture["x"] is b

    def test_rebuild_obeys_call_cap(self):
        s, a, b = _session(), _Fig(), _Fig()
        s.add_figure("x", a, SAVEFIG)
        s.note_savefig("x", a, PDF, None, target=_path("x", "pdf"))
        s.add_figure("x", b, SAVEFIG)
        s.note_savefig("x", b, PDF, None, target=_path("x", "pdf"))
        for _ in range(figcapture.MAX_SAVEFIG_CALLS + 3):
            s.note_savefig("x", a, self.PNG, None, target=_path("x", "png"))
        s.note_savefig("x", a, PDF, None, target=_path("x", "pdf"))
        calls = s.savefig_calls["x"]
        assert s.capture["x"] is a
        assert len(calls) == figcapture.MAX_SAVEFIG_CALLS  # 满额；pdf 已记过，不是新格式
        assert [c["format"] for c in calls].count("pdf") == 1


class TestTargetKey:
    def test_same_name_in_different_directories_is_not_a_takeover(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        s, a, b = _session(), _Fig(), _Fig()
        for fig, name in ((a, "one/a.pdf"), (b, "two/a.pdf")):
            s.add_figure("a", fig, SAVEFIG)
            s.note_savefig(
                "a", fig, {"format": "pdf"}, None, target=figcapture.savefig_target_key(name, {})
            )
        assert s.capture["a"] is a

    def test_suffixless_name_with_explicit_format_is_the_literal_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        k = figcapture.savefig_target_key("a", {"format": "png"})
        assert k is not None and k == figcapture.savefig_target_key("a", {"format": "svg"})
        assert (
            figcapture.savefig_target_key("a", {}) is None
        )  # 补什么后缀问 canvas，证明不了就不归因

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


ALTERNATE = """\
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
a.savefig("x.pdf", bbox_inches="tight")
b.savefig("x.pdf")
a.savefig("x.png", dpi=150)
a.savefig("x.pdf", bbox_inches="tight")
"""


@needs_worker
def test_real_worker_alternating_writers_keep_all_winner_calls(tmp_path):
    write(tmp_path, "alt.py", ALTERNATE)
    built = desktop_build(tmp_path, "alt.py")
    assert _width_in(built, "x") < 4.5  # 甲（figsize 4 英寸，tight 后略窄），不是乙的 5
    (desc,) = [d for d in built["descriptors"] if d["stem"] == "x"]
    calls = desc["savefig_calls"]
    assert [c["format"] for c in calls] == ["pdf", "png", "pdf"]
    assert calls[1]["dpi"] == 150


# Codex 4305548808：失败的保存磁盘上没动，不能顶掉上一个写入者
FAILED_SAVE = """\
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
a.savefig("shape.pdf")
try:
    b.savefig("shape.pdf", {bad})
except Exception:
    pass
"""
BAD_SAVES = [
    'format="bogus"',
    'backend="nonexistent"',
    'backend="agg", format="bogus"',
    'dpi="not-a-number"',
]


class TestVerifyRender:
    """`savefig_verify_render`：用未打补丁的 savefig 不落盘真渲染，原生会抛的就是失败。"""

    @staticmethod
    def _fig(**kw):
        from matplotlib.figure import Figure

        fig = Figure(figsize=(3, 2), **kw)
        fig.add_subplot().plot([1, 2, 3])
        return fig, Figure.savefig

    def test_valid_save_passes(self):
        fig, real = self._fig()
        assert figcapture.savefig_verify_render(real, fig, "a.pdf", (), {})
        assert figcapture.savefig_verify_render(real, fig, "a", (), {"format": "png"})

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"format": "bogus"},
            {"backend": "nonexistent"},
            {"backend": "agg", "format": "bogus"},
            {"dpi": "not-a-number"},
            {"bbox_inches": "bogus"},
        ],
    )
    def test_saves_that_raise_natively_fail(self, kwargs):
        fig, real = self._fig()
        assert not figcapture.savefig_verify_render(real, fig, "a.pdf", (), kwargs)

    def test_unmeasurable_tight_frame_is_a_failed_save_like_native(self):
        """tight 量包围盒失败时原生 savefig 会抛、文件不会写出：验证与 native 同源判失败（Codex #1141 r4306180020）。
        验证只决定顶替；持有者自己的保存不经验证，ADR 0098 的 `frame_unavailable` 报告不受影响。"""
        from matplotlib.artist import Artist

        class Unmeasurable(Artist):
            def draw(self, renderer):
                pass

            def get_tightbbox(self, renderer=None):
                raise ValueError("no extent")

        fig, real = self._fig()
        fig.add_artist(Unmeasurable())
        assert not figcapture.savefig_verify_render(
            real, fig, "a.pdf", (), {"bbox_inches": "tight"}
        )
        assert figcapture.savefig_verify_render(real, fig, "a.pdf", (), {})

    def test_unknown_suffix_fails(self):
        fig, real = self._fig()
        assert not figcapture.savefig_verify_render(real, fig, "a.bogus", (), {})

    def test_figure_state_is_unchanged(self):
        fig, real = self._fig(layout="constrained")
        before = (tuple(fig.get_size_inches()), fig.dpi, type(fig.get_layout_engine()))
        for kw in ({"bbox_inches": "tight"}, {"dpi": 300}, {"transparent": True}):
            assert figcapture.savefig_verify_render(real, fig, "a.png", (), kw)
        assert (tuple(fig.get_size_inches()), fig.dpi, type(fig.get_layout_engine())) == before

    def test_does_not_go_through_the_patched_hook(self):
        fig, _ = self._fig()
        seen = []

        def spy(f, *a, **k):
            seen.append(1)

        figcapture.savefig_verify_render(spy, fig, "a.pdf", (), {})
        assert seen == [1]  # 只调传进去的那个“真”函数一次


class TestVerifyOnlyWhenTakingOver:
    @staticmethod
    def _run(verdict):
        calls = []

        def verify():
            calls.append(1)
            return verdict

        return calls, verify

    def test_holder_writing_itself_never_verifies(self):
        st, a = _State(), _Fig()
        st.claim(a)
        n, v = self._run(False)
        st.save(a, "/p/x.pdf", verify=v)
        st.save(a, "/p/x.png", {"format": "png"}, verify=v)
        assert n == [] and st.calls["x"] == [PDF, {"format": "png"}]

    def test_non_taking_foreign_write_is_verified_and_ignored_on_failure(self):
        """不会顶替的非持有者写也更新 `files`，所以也要验证；失败不更新 files。"""
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf")
        st.save(a, "/p/x.png", {"format": "png"})
        n, v = self._run(False)
        assert st.save(b, "/p/x.pdf", {"format": "pdf", "dpi": "bad"}, verify=v) is False
        assert n == [1]
        assert st.w["x"]["files"]["/p/x.pdf"]() is a and id(b) not in st.w["x"]["by"]

    def test_failed_foreign_write_cannot_set_up_a_wrong_later_takeover(self):
        """A 写 pdf+png；B 以坏参数写 pdf（失败）；B 再合法写 png → 磁盘上 pdf 仍是 A，A 不能被顶掉。"""
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf")
        st.save(a, "/p/x.png", {"format": "png"})
        _, bad = self._run(False)
        st.save(b, "/p/x.pdf", {"format": "pdf", "dpi": "bad"}, verify=bad)
        _, ok = self._run(True)
        assert st.save(b, "/p/x.png", {"format": "png"}, verify=ok) is False
        assert st.cap["x"] is a

    def test_taking_over_verifies_once_and_commits_on_success(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf")
        n, v = self._run(True)
        assert st.save(b, "/p/x.pdf", PDF, "art", verify=v) is True
        assert n == [1] and st.cap["x"] is b and st.extras["x"] == ["art"]

    def test_failed_verification_ignores_the_write_entirely(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/x.pdf")
        n, v = self._run(False)
        assert st.save(b, "/p/x.pdf", {"format": "pdf", "dpi": 1}, verify=v) is False
        assert n == [1] and st.cap["x"] is a and st.calls["x"] == [PDF]
        assert id(b) not in st.w["x"]["by"] and st.w["x"]["files"]["/p/x.pdf"]() is a


class TestFileIdentity:
    def test_case_variants_follow_the_directory_filesystem(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        d = str(tmp_path.resolve())
        monkeypatch.setitem(figcapture._CASE_FOLD_CACHE, d, True)
        assert figcapture.savefig_target_key("Plot.pdf", {}) == figcapture.savefig_target_key(
            "plot.pdf", {}
        )
        monkeypatch.setitem(figcapture._CASE_FOLD_CACHE, d, False)
        assert figcapture.savefig_target_key("Plot.pdf", {}) != figcapture.savefig_target_key(
            "plot.pdf", {}
        )

    def test_probe_matches_the_real_filesystem(self, tmp_path):
        d = tmp_path.resolve() / "CaseProbe"
        d.mkdir()
        expected = (d.parent / "caseprobe").exists()
        figcapture._CASE_FOLD_CACHE.pop(str(d), None)
        assert figcapture._dir_folds_case(str(d)) is expected

    def test_probe_sees_a_literal_variant_symlink_as_case_sensitive(self, tmp_path):
        """区分大小写的卷上，目录的大小写变体若是指向它的符号链接，samefile 为真但它是独立条目，不是大小写别名。"""
        real = tmp_path.resolve() / "CaseDir"
        real.mkdir()
        try:
            (tmp_path.resolve() / "casedir").symlink_to(real)
        except (FileExistsError, OSError):
            pytest.skip("本机文件系统不区分大小写或不能建符号链接")
        figcapture._CASE_FOLD_CACHE.pop(str(real), None)
        assert figcapture._dir_folds_case(str(real)) is False

    @pytest.mark.parametrize("literal,expected", [(True, False), (False, True)])
    def test_probe_logic_with_a_faked_filesystem(self, tmp_path, monkeypatch, literal, expected):
        d = str(tmp_path.resolve() / "CaseDir")
        os.makedirs(d)
        figcapture._CASE_FOLD_CACHE.pop(d, None)
        real_listdir = os.listdir
        monkeypatch.setattr(
            figcapture,
            "_UNWATCHED_LISTDIR",
            lambda p: [*real_listdir(p), "cASEdIR"] if literal else real_listdir(p),
        )
        monkeypatch.setattr(figcapture.os.path, "exists", lambda p: True)
        monkeypatch.setattr(figcapture.os.path, "samefile", lambda a, b: True)
        assert figcapture._dir_folds_case(d) is expected
        figcapture._CASE_FOLD_CACHE.pop(d, None)

    def test_probe_falls_back_to_platform_default(self, tmp_path, monkeypatch):
        gone = str(tmp_path / "Nope")
        figcapture._CASE_FOLD_CACHE.pop(gone, None)
        monkeypatch.setattr(figcapture.sys, "platform", "linux")
        assert figcapture._dir_folds_case(gone) is False
        figcapture._CASE_FOLD_CACHE.pop(gone, None)
        monkeypatch.setattr(figcapture.sys, "platform", "darwin")
        assert figcapture._dir_folds_case(gone) is True
        figcapture._CASE_FOLD_CACHE.pop(gone, None)

    def test_case_folding_does_not_over_fold_unicode(self, tmp_path, monkeypatch):
        """不区分大小写的目录里 `shape.ß` 与 `shape.ss` 仍是两个文件：只折叠大小写，不做 casefold（Codex r4305745044）。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(figcapture, "_dir_folds_case", lambda d: True)
        key = figcapture.savefig_target_key
        assert key("shape.ß", {"format": "pdf"}) != key("shape.ss", {"format": "pdf"})
        assert key("Plot.pdf", {}) == key("plot.pdf", {})

    def test_recreated_path_keeps_its_key(self, tmp_path, monkeypatch):
        """删掉再重建同一路径：仍是同一个目标键（不按 inode，Codex #1141 r4305717514）。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.pdf").write_bytes(b"x")
        k1 = figcapture.savefig_target_key("a.pdf", {})
        (tmp_path / "a.pdf").unlink()
        (tmp_path / "a.pdf").write_bytes(b"y")
        assert figcapture.savefig_target_key("a.pdf", {}) == k1
        assert figcapture.savefig_target_key("gone.pdf", {}) != k1


@needs_worker
@pytest.mark.parametrize("bad", BAD_SAVES)
def test_real_worker_failed_save_does_not_displace(tmp_path, bad):
    write(tmp_path, "bad.py", FAILED_SAVE.format(bad=bad))
    built = desktop_build(tmp_path, "bad.py")
    assert _width_in(built, "shape") == 4.0


@pytest.mark.parametrize("bad", BAD_SAVES)
def test_browser_failed_save_does_not_displace(tmp_path, bad):
    from test_compat_capture_parity import browser_load

    if WORKER_PY is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    out = browser_load(FAILED_SAVE.format(bad=bad), "bad.py", tmp_path / "ws")
    assert out["ok"], out
    assert _browser_width_in(out, "shape") == 4.0


# Codex r4305617407：先认领的图保存失败（没有任何成功写入），第一个成功写入者必须取代它
FAILED_FIRST = """\
import matplotlib.pyplot as plt
b, _ = plt.subplots(figsize=(5, 2.6))
a, _ = plt.subplots(figsize=(4, 2.6))
try:
    b.savefig("shape.pdf", {bad})
except Exception:
    pass
a.savefig("shape.pdf")
"""


@needs_worker
@pytest.mark.parametrize("bad", BAD_SAVES)
def test_real_worker_failed_first_claimant_is_replaced(tmp_path, bad):
    write(tmp_path, "first.py", FAILED_FIRST.format(bad=bad))
    built = desktop_build(tmp_path, "first.py")
    assert _width_in(built, "shape") == 4.0


@pytest.mark.parametrize("bad", BAD_SAVES)
def test_browser_failed_first_claimant_is_replaced(tmp_path, bad):
    from test_compat_capture_parity import browser_load

    if WORKER_PY is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    out = browser_load(FAILED_FIRST.format(bad=bad), "first.py", tmp_path / "ws")
    assert out["ok"], out
    assert _browser_width_in(out, "shape") == 4.0


# 非顶替的非持有者写也要验证：失败的写入不能在 files 里占位
FOREIGN_FAIL = """\
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
a.savefig("shape.pdf")
a.savefig("shape.png")
try:
    b.savefig("shape.pdf", dpi="bad")
except Exception:
    pass
b.savefig("shape.png")
"""


@needs_worker
def test_real_worker_failed_non_taking_write_does_not_hold_a_file(tmp_path):
    write(tmp_path, "foreign.py", FOREIGN_FAIL)
    built = desktop_build(tmp_path, "foreign.py")
    assert _width_in(built, "shape") == 4.0  # shape.pdf 磁盘上仍是 A，A 不能被顶掉


class TestLazyHolderVerification:
    """Codex r4305758512：持有者自己的失败保存（未验证的登记）不能挡住别的图的顶替；常规路径仍零渲染。"""

    @staticmethod
    def _counter(verdict):
        n = []

        def thunk():
            n.append(1)
            return verdict

        return n, thunk

    def test_holders_own_write_does_not_render(self):
        st, a = _State(), _Fig()
        st.claim(a)
        n, t = self._counter(False)
        st.save(a, "/p/x.pdf", verify=t)
        st.save(a, "/p/x.png", {"format": "png"}, verify=t)
        assert n == []

    def test_unverified_blocker_that_fails_is_dropped_and_does_not_block(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        n, bad = self._counter(False)
        st.save(a, "/p/x.pdf", {"format": "pdf", "dpi": "bad"}, verify=bad)  # 持有者自己的失败保存
        _, ok = self._counter(True)
        assert st.save(b, "/p/x.png", {"format": "png"}, verify=ok) is True
        assert st.cap["x"] is b and n == [1]
        assert "/p/x.pdf" not in st.w["x"]["files"]  # 从未落盘的登记被删掉

    def test_failed_holder_rewrite_falls_back_to_the_earlier_successful_save(self):
        """A 成功存 x.pdf，再以坏参数重写 x.pdf 失败；B 存 x.png：磁盘上的 x.pdf 仍是 A 第一次的产物，A 不被顶掉
        （Codex r4306209189）。"""
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        n1, good = self._counter(True)
        st.save(a, "/p/x.pdf", verify=good)
        n2, bad = self._counter(False)
        st.save(a, "/p/x.pdf", {"format": "pdf", "dpi": "bad"}, verify=bad)
        _, ok = self._counter(True)
        assert st.save(b, "/p/x.png", {"format": "png"}, verify=ok) is False
        assert st.cap["x"] is a and n2 == [1] and n1 == [1]
        assert st.w["x"]["files"]["/p/x.pdf"]() is a

    def test_fallback_chain_is_bounded(self):
        st, a = _State(), _Fig()
        st.claim(a)
        for _ in range(3 * figcapture._MAX_PREV):
            st.save(a, "/p/x.pdf", verify=self._counter(False)[1])
        depth, node = 0, st.w["x"]["files"]["/p/x.pdf"].prev
        while node is not None:
            depth, node = depth + 1, node.prev
        assert depth <= figcapture._MAX_PREV

    def test_valid_blocker_blocks_and_is_verified_only_once(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        n, good = self._counter(True)
        st.save(a, "/p/x.pdf", verify=good)
        _, ok = self._counter(True)
        assert st.save(b, "/p/x.png", {"format": "png"}, verify=ok) is False
        assert st.save(b, "/p/x.svg", {"format": "svg"}, verify=ok) is False
        assert st.cap["x"] is a and n == [1]  # A 的 pdf 确实在盘上：挡住，且只验证一次

    def test_only_blocking_entries_are_verified(self):
        """没有要挡的顶替（持有者的登记就是这次的目标）时不渲染。"""
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        n, t = self._counter(True)
        st.save(a, "/p/x.pdf", verify=t)
        _, ok = self._counter(True)
        st.save(b, "/p/x.pdf", verify=ok)  # 同一目标：持有者不在「别的」目标上
        assert n == []

    def test_verifier_holds_figure_weakly(self):
        import gc

        fig = _Fig()
        v = figcapture.savefig_verifier(lambda *a, **k: None, fig, "a.pdf", (), {"dpi": 1})
        del fig
        gc.collect()
        assert v() is False


LAZY_SCRIPT = """\
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
try:
    a.savefig("x.pdf", {first})
except Exception:
    pass
b.savefig("x.png")
"""


@needs_worker
@pytest.mark.parametrize("first,expect", [('dpi="bad"', 5.0), ("dpi=100", 4.0)])
def test_real_worker_failed_holder_write_does_not_block(tmp_path, first, expect):
    write(tmp_path, "lazy.py", LAZY_SCRIPT.format(first=first))
    built = desktop_build(tmp_path, "lazy.py")
    assert _width_in(built, "x") == expect


@pytest.mark.parametrize("first,expect", [('dpi="bad"', 5.0), ("dpi=100", 4.0)])
def test_browser_failed_holder_write_does_not_block(tmp_path, first, expect):
    from test_compat_capture_parity import browser_load

    if WORKER_PY is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    out = browser_load(LAZY_SCRIPT.format(first=first), "lazy.py", tmp_path / "ws")
    assert out["ok"], out
    assert _browser_width_in(out, "x") == expect
