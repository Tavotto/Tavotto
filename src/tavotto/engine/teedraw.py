"""一次 `Figure.draw` 同时出 PDF 与 SVG（ADR 0150）。

为什么存在：PDF / SVG 配对导出原来是「PDF 画一遍、SVG 再画一遍」，所以用户的**非纯**回调（带计数器的
`FuncFormatter`、`draw_event` 里改文字、自定义 Artist……）会让两份产物出自两个不同的场景；`figsession` 的
`_svg_pair_blocker` 只好把这些图整个挡在 SVG 之外。这里换一个结构：matplotlib 自己的 `print_pdf` 照常走，
只把它里面的 `MixedModeRenderer` 换成 `TeeRenderer`——**一次 draw，同一串绘制调用同时进 PDF 与 SVG**，
场景相同是结构性的，不再靠「证明回调是纯的」。

约定（改之前读）：

* **PDF 是主**：版面（文字度量）、`flipy`、图像合成选项都由 PDF 的 renderer 回答，与原先「SVG 复用 PDF 的布局」
  同一个口径。PDF 字节与常规 `savefig` 逐像素相同（`tests/test_teedraw.py`）。
* **从 renderer 各有自己的 gc**：每次绘制调用给每个目标单独 `new_gc()` 再 `copy_properties`（方向别写反）；
  `draw_text` / `draw_tex` 的 y 在 `flipy` 不同的目标上换成 `H - y`，其余绘制调用 SVG 自己会翻。
* **`svg.fonttype` / `svg.image_inline` 是 draw 时才读的 rcParam**：每个 SVG 目标带自己的 rc，只在转发给它的
  那一刻生效，所以 path 变体与 text 变体可以在同一次 draw 里并存。
* **SVG 目标失败不拖累 PDF**：从 renderer 抛错就把它停掉并记下原因，PDF 继续画完。
* **`bbox_inches="tight"` 会先建一个探测用 renderer 再丢掉**：SVG 先缓冲在内存里，只有走到 `finalize()` 的
  那一组才交给 sink，探测那一组什么也不留下。
* 依赖 `backend_pdf` 模块里 `MixedModeRenderer` 这个名字与 matplotlib 的 renderer 协议（3.8.4 / 3.10.8 / 3.11.1
  验过）。换版本后 `status()` 的自检会先于任何导出核对它；核对不过 = 回到「二次绘制 + 回调阻断」的老路。
"""

from __future__ import annotations

import io
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import matplotlib as mpl
from matplotlib.backend_bases import RendererBase
from matplotlib.backends import backend_pdf, backend_svg
from matplotlib.backends.backend_mixed import MixedModeRenderer

_DRAW_CALLS = (
    "draw_path",
    "draw_markers",
    "draw_path_collection",
    "draw_quad_mesh",
    "draw_gouraud_triangles",
    "draw_image",
    "draw_text",
    "draw_tex",
)
_TEXT_CALLS = ("draw_text", "draw_tex")


@dataclass
class SvgTarget:
    """一份要从同一次 draw 里出的 SVG：`rc` 只在转发给它时生效，`sink` 收最终文本。"""

    rc: dict
    sink: Callable[[str], None]


@dataclass
class _Follower:
    renderer: object
    target: SvgTarget
    buffer: io.StringIO
    error: BaseException | None = None
    flip: bool = False


@contextmanager
def _rc(over: dict):
    old = {k: mpl.rcParams[k] for k in over}
    try:
        for key, value in over.items():
            mpl.rcParams[key] = value
        yield
    finally:
        for key, value in old.items():
            mpl.rcParams[key] = value


class TeeRenderer(RendererBase):
    """主（PDF）+ 若干 SVG 从 renderer；见模块注释。"""

    def __init__(self, master, followers: list[_Follower], height_pt: float, trace=None):
        super().__init__()
        self._m = master
        self._followers = followers
        self._h = height_pt
        self._trace = trace
        for f in followers:
            f.flip = f.renderer.flipy() != master.flipy()

    # ---- 查询：主回答 ---------------------------------------------------------
    def flipy(self):
        return self._m.flipy()

    def get_canvas_width_height(self):
        return self._m.get_canvas_width_height()

    def points_to_pixels(self, points):
        return self._m.points_to_pixels(points)

    def get_text_width_height_descent(self, s, prop, ismath):
        return self._m.get_text_width_height_descent(s, prop, ismath)

    def option_image_nocomposite(self):
        return self._m.option_image_nocomposite()

    def option_scale_image(self):
        return self._m.option_scale_image()

    def get_image_magnification(self):
        return self._m.get_image_magnification()

    def __getattr__(self, name):  # 只在本类与基类都没有时才到这里：交给主
        if name.startswith("__") or name in ("_m", "_followers"):
            raise AttributeError(name)
        return getattr(self._m, name)

    # ---- 广播 -----------------------------------------------------------------
    def _each(self, call):
        call(self._m, None)
        for f in self._followers:
            if f.error is not None:
                continue
            try:
                with _rc(f.target.rc):
                    call(f.renderer, f)
            except Exception as exc:  # noqa: BLE001 — 一份 SVG 失败不能拖累 PDF
                f.error = exc

    def open_group(self, s, gid=None):
        self._each(lambda r, f: r.open_group(s, gid))

    def close_group(self, s):
        self._each(lambda r, f: r.close_group(s))

    def start_rasterizing(self):
        self._each(lambda r, f: r.start_rasterizing())

    def stop_rasterizing(self):
        self._each(lambda r, f: r.stop_rasterizing())

    def start_filter(self):
        self._each(lambda r, f: r.start_filter())

    def stop_filter(self, filter_func):
        self._each(lambda r, f: r.stop_filter(filter_func))

    def _fan(self, name, gc, *args, **kwargs):
        def one(renderer, follower):
            a = args
            if follower is not None and follower.flip and name in _TEXT_CALLS:
                a = (args[0], self._h - args[1], *args[2:])
            own = renderer.new_gc()
            own.copy_properties(gc)  # 目标 ← tee 的 gc（方向别写反）
            try:
                getattr(renderer, name)(own, *a, **kwargs)
            finally:
                own.restore()

        if self._trace is not None and name in _TEXT_CALLS:
            self._trace.append((name, str(args[2]) if len(args) > 2 else ""))
        self._each(one)

    def finalize(self):
        self._m.finalize()
        for f in self._followers:
            if f.error is not None:
                continue
            try:
                with _rc(f.target.rc):
                    f.renderer.finalize()
                f.target.sink(f.buffer.getvalue())
            except Exception as exc:  # noqa: BLE001
                f.error = exc


for _name in _DRAW_CALLS:
    setattr(
        TeeRenderer,
        _name,
        (lambda n: lambda self, gc, *a, **k: self._fan(n, gc, *a, **k))(_name),
    )


@dataclass
class DrawSession:
    """`single_draw()` 的结果：真正走到 finalize 的那一组 SVG 目标各自的错误（None = 成功写出）。"""

    figure: object
    targets: Sequence[SvgTarget]
    groups: list[list[_Follower]] = field(default_factory=list)
    finalized: list[_Follower] | None = None
    trace: list | None = None

    def errors(self) -> list[BaseException | None]:
        """与 `targets` 一一对应；没有任何一组走到 finalize（PDF 自己失败了）时每项都是 RuntimeError。"""
        if self.finalized is None:
            return [RuntimeError("the PDF pass never finalized") for _ in self.targets]
        return [f.error for f in self.finalized]


#: 当前线程 / 上下文里正在进行的会话。**不是进程级状态**：别的线程（ContextVar 默认不继承）、别的 figure 的
#: `savefig` 看到的是 None / 另一张图，原样走 matplotlib 自己的 `MixedModeRenderer`。
_SESSION: ContextVar[DrawSession | None] = ContextVar("teedraw_session", default=None)
_INSTALL_LOCK = threading.Lock()
_INSTALLED = 0
_REAL_MIXED = backend_pdf.MixedModeRenderer


def _dispatch(figure, width, height, dpi, vector, bbox_inches_restore=None):
    """`backend_pdf.MixedModeRenderer` 的替身：只在「本上下文的会话 + 会话要导的那张 figure」时返回 `TeeRenderer`。"""
    master = _REAL_MIXED(
        figure, width, height, dpi, vector, bbox_inches_restore=bbox_inches_restore
    )
    session = _SESSION.get()
    if session is None or session.figure is not figure:
        return master
    followers = []
    for target in session.targets:
        buffer = io.StringIO()
        with _rc(target.rc):
            svg = backend_svg.RendererSVG(width * 72, height * 72, buffer, image_dpi=dpi)
        mixed = MixedModeRenderer(
            figure, width, height, dpi, svg, bbox_inches_restore=bbox_inches_restore
        )
        followers.append(_Follower(mixed, target, buffer))
    session.groups.append(followers)
    tee = TeeRenderer(master, followers, height * 72, session.trace)
    real_finalize = tee.finalize

    def finalize():
        real_finalize()
        session.finalized = followers

    tee.finalize = finalize
    return tee


@contextmanager
def _installed():
    """会话存续期间把 `backend_pdf.MixedModeRenderer` 换成 `_dispatch`（引用计数），最后一个会话退出即还原。

    换的只是一个模块级名字，而且替身对没有会话的调用方就是原类的构造：并发的别的 `savefig` 不受影响。
    """
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED == 0:
            backend_pdf.MixedModeRenderer = _dispatch
        _INSTALLED += 1
    try:
        yield
    finally:
        with _INSTALL_LOCK:
            _INSTALLED -= 1
            if _INSTALLED == 0:
                backend_pdf.MixedModeRenderer = _REAL_MIXED


@contextmanager
def single_draw(figure, targets: Sequence[SvgTarget], *, trace: list | None = None):
    """在这个上下文里跑 `figure.savefig(<pdf>)`：同一次 draw 另出 `targets` 里的 SVG。

    只对**这个线程、这张 figure** 生效。出上下文后读 `session.errors()`。只对 `savefig(format="pdf")` 有意义。
    """
    session = DrawSession(figure=figure, targets=list(targets), trace=trace)
    token = _SESSION.set(session)
    try:
        with _installed():
            yield session
    finally:
        _SESSION.reset(token)


# ---------------------------------------------------------------------------
# 自检：换 matplotlib 之后，在任何一次导出之前先核对「这套 tee 还对不对」
# ---------------------------------------------------------------------------
_STATUS: tuple[bool, str] | None = None


def status() -> tuple[bool, str]:
    """`(可用, 原因)`；进程内只跑一次。不可用时原因是一句给诊断看的英文。"""
    global _STATUS
    if _STATUS is None:
        try:
            _STATUS = _self_test()
        except Exception as exc:  # noqa: BLE001 — 自检自己出错也是「不可用」
            _STATUS = (False, f"self-test raised {type(exc).__name__}: {str(exc)[:120]}")
    return _STATUS


def reset_status_for_tests() -> None:
    global _STATUS
    _STATUS = None


def _pdf_bytes(fig) -> bytes:
    """直接走后端的 `print_pdf`，**不经 `Figure.savefig`**：worker / bridge 装了 savefig 捕获钩子，自检不能算一次用户保存。"""
    out = io.BytesIO()
    backend_pdf.FigureCanvasPdf(fig).print_pdf(
        out, metadata={"CreationDate": None, "Creator": "teedraw"}
    )
    return out.getvalue()


def _self_test() -> tuple[bool, str]:
    if not hasattr(backend_pdf, "MixedModeRenderer"):
        return False, "backend_pdf has no MixedModeRenderer"
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    def build(counter: bool):
        fig = Figure(figsize=(2, 1.5))
        ax = fig.subplots()
        ax.plot([0, 1, 2], [0, 1, 0], alpha=0.7)
        ax.bar([0.5], [0.4], hatch="//")
        ax.imshow(np.arange(16.0).reshape(4, 4), extent=(0, 1, 0, 0.5), alpha=0.5)
        ax.scatter([0.3], [0.3], rasterized=True)
        ax.set_title("t $x^2$")
        if counter:
            calls = [0]

            def label(value, _pos):
                calls[0] += 1
                return f"c{calls[0]}"

            ax.xaxis.set_major_formatter(FuncFormatter(label))
        return fig

    sinks: dict[str, str] = {}
    rc_path = {"svg.fonttype": "path", "svg.image_inline": True}
    rc_text = {"svg.fonttype": "none", "svg.image_inline": True}
    targets = [
        SvgTarget(rc_path, lambda text: sinks.__setitem__("path", text)),
        SvgTarget(rc_text, lambda text: sinks.__setitem__("text", text)),
    ]

    # 1) PDF 与常规 savefig 字节相同（确定性的图）
    plain = _pdf_bytes(build(False))
    fig_a = build(False)
    with single_draw(fig_a, targets) as session:
        teed = _pdf_bytes(fig_a)
    if plain != teed:
        return False, "tee PDF differs from the plain savefig PDF"
    if any(session.errors()):
        return False, f"SVG target failed: {next(e for e in session.errors() if e)!r}"[:160]

    # 2) 带计数器回调的图：PDF 与 SVG 看到同一串文字调用，SVG 是合法 XML 且保留了 <text>
    sinks.clear()
    trace: list = []
    fig_b = build(True)
    with single_draw(fig_b, targets, trace=trace):
        _pdf_bytes(fig_b)
    labels = [s for _n, s in trace if s.startswith("c")]
    if not labels or labels != sorted(labels, key=lambda s: int(s[1:])):
        return False, "counter formatter did not run exactly once per tick"
    for variant in ("path", "text"):
        text = sinks.get(variant, "")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return False, f"{variant} SVG is not well-formed: {exc}"[:160]
        if variant == "text":
            seen = {"".join(e.itertext()) for e in root.iter("{http://www.w3.org/2000/svg}text")}
            if not set(labels) <= seen:
                return False, "text SVG does not carry the labels the PDF pass drew"
    return True, "ok"
