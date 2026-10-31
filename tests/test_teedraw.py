"""`teedraw`：一次 draw 同时出 PDF 与 SVG（ADR 0150）。纯 matplotlib，不起 worker——跑在 CI 的每一档 matplotlib 上。"""

from __future__ import annotations

import io
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib
import numpy as np
import pytest
from matplotlib.backends import backend_pdf, backend_svg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "tavotto" / "engine"))
import teedraw  # noqa: E402

SVG = "{http://www.w3.org/2000/svg}"
RC_PATH = {"svg.fonttype": "path", "svg.image_inline": True}
RC_TEXT = {"svg.fonttype": "none", "svg.image_inline": True}


def _pdf(fig) -> bytes:
    out = io.BytesIO()
    backend_pdf.FigureCanvasPdf(fig).print_pdf(out, metadata={"CreationDate": None, "Creator": "t"})
    return out.getvalue()


def _svg(fig, rc) -> str:
    out = io.StringIO()
    with matplotlib.rc_context(rc):
        backend_svg.FigureCanvasSVG(fig).print_svg(out)
    return out.getvalue()


def _tee(make):
    """同一张图的 tee 产物：(pdf 字节, path 版 SVG, text 版 SVG, session)。"""
    got: dict[str, str] = {}
    targets = [
        teedraw.SvgTarget(RC_PATH, lambda t: got.__setitem__("path", t)),
        teedraw.SvgTarget(RC_TEXT, lambda t: got.__setitem__("text", t)),
    ]
    fig = make()
    with teedraw.single_draw(fig, targets) as session:
        pdf = _pdf(fig)
    return pdf, got.get("path", ""), got.get("text", ""), session


def two_images():
    fig = Figure(figsize=(4, 3))
    ax = fig.subplots()
    r = np.random.RandomState(1)
    ax.imshow(r.rand(8, 8), extent=(0, 1, 0, 1))
    ax.imshow(r.rand(8, 8), extent=(0.5, 1.5, 0.5, 1.5), alpha=0.6)
    ax.set_xlim(0, 1.5)
    ax.set_ylim(0, 1.5)
    return fig


def mesh_and_colorbar():
    fig = Figure(figsize=(4, 3))
    ax = fig.subplots()
    fig.colorbar(ax.pcolormesh(np.random.RandomState(2).rand(6, 6)))
    return fig


def contours():
    fig = Figure(figsize=(4, 3))
    ax = fig.subplots()
    x = np.linspace(-2, 2, 40)
    X, Y = np.meshgrid(x, x)
    ax.contourf(X, Y, np.sin(X * Y), 8)
    ax.contour(X, Y, np.sin(X * Y), 8, colors="k")
    return fig


def markers_text_math():
    fig = Figure(figsize=(4, 3))
    ax = fig.subplots()
    ax.plot(np.random.RandomState(3).rand(30), "o-", ms=4)
    ax.errorbar(range(5), np.random.RandomState(4).rand(5), yerr=0.1, fmt="s")
    ax.annotate("note", (1, 0.5), (2, 0.8), arrowprops={"arrowstyle": "->"})
    ax.set_ylabel(r"rot $\alpha_i$")
    return fig


def hatch_alpha_clip():
    fig = Figure(figsize=(4, 3))
    ax = fig.subplots()
    ax.fill_between([0, 1, 2], [0, 1, 0], hatch="xx", alpha=0.5)
    ax.set_xlim(0.2, 1.8)
    return fig


def rasterized():
    fig = Figure(figsize=(4, 3))
    ax = fig.subplots()
    ax.set_rasterization_zorder(1)
    ax.scatter(np.random.RandomState(4).rand(300), np.random.RandomState(5).rand(300), zorder=0)
    ax.plot([0, 1], [0, 1], zorder=2)
    return fig


def constrained():
    fig = Figure(figsize=(5, 4), layout="constrained")
    for ax in fig.subplots(2, 2).flat:
        ax.plot([0, 1], [0, 1])
        ax.set_title("long title here")
    return fig


CASES = [
    two_images,
    mesh_and_colorbar,
    contours,
    markers_text_math,
    hatch_alpha_clip,
    rasterized,
    constrained,
]


def test_self_test_passes_on_this_matplotlib():
    teedraw.reset_status_for_tests()
    assert teedraw.status() == (True, "ok"), matplotlib.__version__


@pytest.mark.parametrize("make", CASES, ids=lambda f: f.__name__)
def test_tee_pdf_is_byte_identical_to_the_plain_pdf(make):
    pdf, path_svg, text_svg, session = _tee(make)
    assert pdf == _pdf(make())
    assert session.errors() == [None, None]
    for text in (path_svg, text_svg):
        ET.fromstring(text)


@pytest.mark.parametrize("make", CASES, ids=lambda f: f.__name__)
def test_tee_svg_has_the_same_structure_as_a_standalone_svg(make):
    _pdf_bytes, path_svg, text_svg, _ = _tee(make)

    def tags(text):
        return sorted(e.tag for e in ET.fromstring(text).iter())

    # 版面度量取自 PDF，文字坐标与独立保存可差亚点；元素构成必须一致
    assert tags(path_svg) == tags(_svg(make(), RC_PATH))
    assert tags(text_svg) == tags(_svg(make(), RC_TEXT))
    assert not list(ET.fromstring(path_svg).iter(SVG + "text"))


@pytest.mark.parametrize("make", [markers_text_math, constrained], ids=lambda f: f.__name__)
def test_tee_svg_text_lands_where_the_standalone_svg_puts_it(make):
    """PDF 的 flipy 与 SVG 相反：`draw_text` 的 y 要给 SVG 换成 `H - y`，否则整页文字上下颠倒。"""
    _p, _path, text_svg, _ = _tee(make)

    def places(text):
        return [
            ("".join(e.itertext()), float(e.get("x")), float(e.get("y")))
            for e in ET.fromstring(text).iter(SVG + "text")
            if e.get("x") is not None
            and e.get("y") is not None  # mathtext 的 <text> 坐标在 <tspan> 里
        ]

    tee, plain = places(text_svg), places(_svg(make(), RC_TEXT))
    assert tee and len(tee) == len(plain)
    for (s1, x1, y1), (s2, x2, y2) in zip(sorted(tee), sorted(plain)):
        assert s1 == s2 and abs(x1 - x2) < 3 and abs(y1 - y2) < 3, (s1, (x1, y1), (x2, y2))


def test_counter_formatter_yields_the_same_labels_in_pdf_and_svg():
    """带计数器的回调：每画一遍标签就变。单次绘制下 SVG 的文字就是 PDF 画的那一串。"""

    def make():
        fig = Figure(figsize=(3, 2))
        ax = fig.subplots()
        calls = [0]

        def label(_v, _p):
            calls[0] += 1
            return f"t{calls[0]}"

        ax.xaxis.set_major_formatter(FuncFormatter(label))
        return fig

    trace: list = []
    got: dict[str, str] = {}
    fig = make()
    with teedraw.single_draw(
        fig, [teedraw.SvgTarget(RC_TEXT, lambda t: got.__setitem__("text", t))], trace=trace
    ):
        _pdf(fig)
    drawn = [s for _n, s in trace if re.fullmatch(r"t\d+", s)]
    svg_labels = [
        "".join(e.itertext())
        for e in ET.fromstring(got["text"]).iter(SVG + "text")
        if re.fullmatch(r"t\d+", "".join(e.itertext()))
    ]
    assert drawn and drawn == svg_labels


def test_draw_event_callback_fires_once_and_both_outputs_see_the_same_scene():
    fig = Figure(figsize=(3, 2))
    ax = fig.subplots()
    title = ax.set_title("before")
    fired = []

    def after_draw(_event):
        fired.append(1)
        title.set_text("after")

    fig.canvas.mpl_connect("draw_event", after_draw)
    got: dict[str, str] = {}
    with teedraw.single_draw(
        fig, [teedraw.SvgTarget(RC_TEXT, lambda t: got.__setitem__("text", t))]
    ):
        _pdf(fig)
    assert fired == [1]
    texts = ["".join(e.itertext()) for e in ET.fromstring(got["text"]).iter(SVG + "text")]
    assert "before" in texts and "after" not in texts


def test_a_failing_svg_target_never_costs_the_pdf(monkeypatch):
    plain = _pdf(markers_text_math())

    def boom(self, *args, **kwargs):
        raise RuntimeError("svg draw failed")

    monkeypatch.setattr(backend_svg.RendererSVG, "draw_path", boom)
    got: dict[str, str] = {}
    fig = markers_text_math()
    with teedraw.single_draw(
        fig, [teedraw.SvgTarget(RC_PATH, lambda t: got.__setitem__("p", t))]
    ) as session:
        pdf = _pdf(fig)
    assert pdf == plain
    assert got == {} and "svg draw failed" in str(session.errors()[0])


def test_tight_bbox_probe_pass_leaves_no_svg_behind():
    fig = markers_text_math()
    sinks: list[str] = []
    with teedraw.single_draw(fig, [teedraw.SvgTarget(RC_PATH, sinks.append)]):
        fig.savefig(io.BytesIO(), format="pdf", bbox_inches="tight")
    assert len(sinks) == 1
    ET.fromstring(sinks[0])  # 探测那一遍建的 renderer 没有落盘、没有残片


def test_rcparams_and_backend_name_are_restored():
    before = dict(matplotlib.rcParams)
    real = backend_pdf.MixedModeRenderer
    _tee(markers_text_math)
    assert dict(matplotlib.rcParams) == before
    assert backend_pdf.MixedModeRenderer is real


def test_a_broken_tee_turns_the_status_off_instead_of_exporting_wrong_files(monkeypatch):
    """换 matplotlib 后若 tee 不再成立，自检先于任何导出把它关掉（回到二次绘制 + 回调阻断）。"""

    def swapped(self, gc, *args, **kwargs):  # 一个把绘制调用吞掉的 SVG 目标
        return None

    monkeypatch.setattr(backend_svg.RendererSVG, "draw_text", swapped)
    teedraw.reset_status_for_tests()
    try:
        ok, why = teedraw.status()
        assert not ok and why
    finally:
        monkeypatch.undo()
        teedraw.reset_status_for_tests()


def test_other_figures_and_other_threads_never_see_the_tee():
    """Codex #1355：替换 `MixedModeRenderer` 不得波及别的调用方——同线程的另一张图、别的线程同时 savefig 的 PDF 都原样。"""
    import threading

    target_fig = markers_text_math()
    plain_target = _pdf(markers_text_math())
    plain_other = _pdf(contours())
    sinks: list[str] = []
    started, release = threading.Event(), threading.Event()
    background: dict[str, bytes] = {}

    def worker():
        # 会话进行期间在另一个线程里反复导另一张图的 PDF
        started.wait(5)
        out = [_pdf(contours()) for _ in range(5)]
        background["pdf"] = out[0]
        background["same"] = all(b == out[0] for b in out)
        release.set()

    thread = threading.Thread(target=worker)
    thread.start()
    with teedraw.single_draw(target_fig, [teedraw.SvgTarget(RC_PATH, sinks.append)]) as session:
        started.set()
        pdf = _pdf(target_fig)
        # 同线程里别的 figure：不是会话的那张，原样走原类，不产生 SVG
        other = contours()
        same_thread_other = _pdf(other)
        release.wait(10)
    thread.join()
    assert pdf == plain_target
    assert len(sinks) == 1 and session.errors() == [None]  # 只有会话那张图出了 SVG
    ET.fromstring(sinks[0])
    assert same_thread_other == plain_other
    assert background["pdf"] == plain_other and background["same"]
    assert backend_pdf.MixedModeRenderer is teedraw._REAL_MIXED  # 会话结束，模块名字还原


def test_formatter_and_locator_run_exactly_as_often_as_in_a_plain_savefig():
    """tick 槽是单次绘制下唯一的值槽例外：它们在 Axis.draw 阶段只算一次，tee 不得让它们被多跑（每个目标各一遍）。"""
    from matplotlib.ticker import MaxNLocator

    def build():
        fig = Figure(figsize=(3, 2))
        ax = fig.subplots()
        calls = {"fmt": 0, "loc": 0}

        def label(_v, _p):
            calls["fmt"] += 1
            return "x"

        class CountingLocator(MaxNLocator):
            def __call__(self):
                calls["loc"] += 1
                return super().__call__()

        ax.xaxis.set_major_formatter(FuncFormatter(label))
        ax.xaxis.set_major_locator(CountingLocator())
        return fig, calls

    fig, plain_calls = build()
    _pdf(fig)
    fig, tee_calls = build()
    with teedraw.single_draw(
        fig,
        [teedraw.SvgTarget(RC_PATH, lambda t: None), teedraw.SvgTarget(RC_TEXT, lambda t: None)],
    ):
        _pdf(fig)
    assert plain_calls["fmt"] > 0 and plain_calls["loc"] > 0
    assert tee_calls == plain_calls
