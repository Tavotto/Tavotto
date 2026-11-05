"""只存进文件对象（BytesIO 等）、随后被 close 的图也要捕获（#749，从 #739 拆出）。

`fig.savefig(buf)` → PIL 转 TIFF → `plt.close(fig)` 的脚本里，缓冲区那次是这张图唯一的存盘：跑完时 pyplot 里
已经没有它，没有 stem 可认领。以前一张都捕获不到（单图）、或悄悄少一张且没有任何提示（多图）。

钉住的是**结果**（实现在 `figcapture.BufferSaves` + `collect_pyplot_figures`，safe worker 与浏览器各调一次）：

* 补回来（worker / 浏览器、`Figure()` 不 import pyplot、批量、上限、丢了进 `dropped_figures` / `truncated_figures`）；
* 缓冲区那次 savefig 的参数随兜底 stem 走（图活着、被关掉都一样），图幅与脚本生成的那份一致；
* worker 与浏览器对同一脚本给出逐字段相同的 descriptors；
* 多图形状：第一张只存缓冲区后 close、其余存路径，原生 3 张、以前 Tavotto 2 张且无提示。

本进程不 import matplotlib：真 worker / 真浏览器的驱动同 `test_compat_capture_parity.py`。
"""

from __future__ import annotations

import gc
import weakref

import pytest

from tavotto.engine import figcapture, pool
from test_compat_capture_parity import browser_load, desktop_build, write

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

FIGSIZE_MM = [50.8, 25.4]  # figsize=(2, 1) 英寸

BUFFER_ONLY_THEN_CLOSE = """\
import io
import matplotlib.pyplot as plt
from PIL import Image

fig, ax = plt.subplots(figsize=(2, 1))
ax.plot([1, 2])
ax.set_xlabel("a rather long x label that spills past the axes")
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=50, bbox_inches="tight")
buf.seek(0)
with Image.open(buf) as raster:
    raster.convert("RGB").load()
plt.close(fig)
"""

#: 同一张图，不关：pyplot 兜底本来就捕获得到，缓冲区那次的参数也要跟着走
BUFFER_ONLY_ALIVE = BUFFER_ONLY_THEN_CLOSE.replace("plt.close(fig)\n", "")

OO_API_BUFFER_ONLY = """\
import io
from matplotlib.figure import Figure

fig = Figure(figsize=(2, 1))
fig.subplots().plot([1, 2])
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=50)
assert buf.getvalue()[:8] == b"\\x89PNG\\r\\n\\x1a\\n"
"""

#: Issue #749 最后一条评论的形状：第一张只存缓冲区后 close，其余存路径（原生 3 张）
BUFFER_FIRST_THEN_PATHS = """\
import io
import matplotlib.pyplot as plt

f1, a1 = plt.subplots(figsize=(2, 1))
a1.set_title("one")
buf = io.BytesIO()
f1.savefig(buf, format="png", bbox_inches="tight")
plt.close(f1)
f2, a2 = plt.subplots(figsize=(2, 1))
a2.set_title("two")
f2.savefig("two.pdf")
f3, a3 = plt.subplots(figsize=(2, 1))
a3.set_title("three")
f3.savefig("three.pdf")
"""

CLAIMED_THEN_BUFFER_ONLY = """\
import io
import matplotlib.pyplot as plt

for i in range(8):  # 前 8 张：先存缓冲区、再按路径存盘（被认领）
    fig, ax = plt.subplots(figsize=(1, 1))
    fig.savefig(io.BytesIO(), format="png", dpi=20)
    fig.savefig(f"F{i}.pdf")
    plt.close(fig)
fig, ax = plt.subplots(figsize=(1, 1))  # 第 9 张：只存缓冲区、随后关掉
fig.savefig(io.BytesIO(), format="png", dpi=20)
plt.close(fig)
"""

PATH_THEN_BUFFER = """\
import io
import matplotlib.pyplot as plt

for i in range(8):  # 前 8 张：先按路径存盘（被认领），再存缓冲区
    fig, ax = plt.subplots(figsize=(1, 1))
    fig.savefig(f"F{i}.pdf")
    fig.savefig(io.BytesIO(), format="png", dpi=20)
    plt.close(fig)
fig, ax = plt.subplots(figsize=(1, 1))  # 第 9 张：只存缓冲区、随后关掉
fig.savefig(io.BytesIO(), format="png", dpi=20)
plt.close(fig)
"""

MANY_BUFFER_ONLY = """\
import io
import matplotlib.pyplot as plt

for i in range(11):
    fig, ax = plt.subplots(figsize=(1, 1))
    fig.savefig(io.BytesIO(), format="png", dpi=20)
    plt.close(fig)
"""

NOISY_FINALIZER = """\
import gc
import io
import matplotlib.pyplot as plt

gc.disable()  # 让下面这个环只可能在引擎自己的 gc.collect() 里被回收


class Noisy:
    def __del__(self):
        print("bye from __del__")


n = Noisy()
n.me = n
del n
fig, ax = plt.subplots(figsize=(1, 1))
fig.savefig(io.BytesIO(), format="png", dpi=20)
"""

#: 两张图先后存到同一路径（原生磁盘上只剩后一张）
SAME_PATH_TWICE = """\
import matplotlib.pyplot as plt

for i in range(2):
    fig, ax = plt.subplots(figsize=(2, 1))
    ax.set_title(f"t{i}")
    fig.savefig("same.pdf")
"""

#: 每张既存缓冲区又存同一路径：缓冲区那次不许让任何一张多出来
SAME_PATH_WITH_BUFFER = """\
import io
import matplotlib.pyplot as plt

for i in range(2):
    fig, ax = plt.subplots(figsize=(2, 1))
    ax.set_title(f"t{i}")
    fig.savefig(io.BytesIO(), format="png", bbox_inches="tight")
    fig.savefig("same.pdf")
    plt.close(fig)
"""

EXTRA_ARTISTS = """\
import io
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(2, 1))
note = fig.text(0.5, 1.2, "outside")
fig.savefig(io.BytesIO(), format="png", bbox_inches="tight", bbox_extra_artists=[note])
plt.close(fig)
"""

#: 参与逐字段对照的形状不含 claimed / reverse：它们 9 张图，浏览器另有 `MAX_FIGURES = 8` 的总数上限，与桌面（只限兜底那部分）
#: 的差别是既有的、与缓冲区无关的口径，不在这一单里。
#: 失败的缓冲区保存（往已关闭的 BytesIO 存 tight）被脚本 try/except 吞掉，之后用别的参数存新缓冲区成功
FAILED_THEN_OK = """\
import io
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(2, 1))
ax.set_xlabel("a rather long x label that spills past the axes")
dead = io.BytesIO()
dead.close()
try:
    fig.savefig(dead, format="png", bbox_inches="tight")
except ValueError:
    pass
fig.savefig(io.BytesIO(), format="png", dpi=50)
plt.close(fig)
ghost = plt.figure(figsize=(2, 1))  # 只在失败的保存里出现过的图，从没成功存过
try:
    ghost.savefig(dead, format="png")
except ValueError:
    pass
plt.close(ghost)
"""

#: 9 张：每张存缓冲区、show、close（show 账与缓冲区账都超出上限）
SHOW_AND_BUFFER = """\
import io
import matplotlib.pyplot as plt

for i in range(9):
    fig, ax = plt.subplots(figsize=(1, 1))
    fig.savefig(io.BytesIO(), format="png", dpi=20)
    plt.show()
    plt.close(fig)
"""

SHAPES = {
    "buffer_only": BUFFER_ONLY_THEN_CLOSE,
    "alive": BUFFER_ONLY_ALIVE,
    "oo": OO_API_BUFFER_ONLY,
    "multi": BUFFER_FIRST_THEN_PATHS,
    "same_path": SAME_PATH_TWICE,
    "same_path_buffer": SAME_PATH_WITH_BUFFER,
    "extras": EXTRA_ARTISTS,
}


def _png_call(descriptor: dict) -> dict:
    (call,) = [c for c in descriptor["savefig_calls"] if c["format"] == "png"]
    return call


# ===========================================================================
# 真 worker / 真浏览器驱动
# ===========================================================================
@needs_worker
class TestBufferOnlyFigureIsCaptured:
    def test_closed_figure_comes_back_with_the_buffer_saves_arguments(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "buffer_only.py", BUFFER_ONLY_THEN_CLOSE)
        resp = desktop_build(figs, "buffer_only.py")
        (d,) = resp["descriptors"]
        assert d["stem"] == "buffer_only"
        assert d["capture_source"] == "pyplot"  # 缓冲区不是磁盘上的原件
        assert d["original_artifact"] is None and d["can_writeback_artifact"] is False
        assert _png_call(d)["bbox_inches"] == "tight" and _png_call(d)["dpi"] == 50.0
        # 图幅就是脚本生成的那份：tight 之后比 figsize 宽高都变了（溢出的轴标签被框进来）
        assert d["size_mm"] != FIGSIZE_MM

    def test_a_living_figure_gets_the_same_arguments_and_frame(self, tmp_path):
        """图还活着时 pyplot 兜底本来就捕获得到——但缓冲区那次的参数同样要挂到兜底 stem 上（r4140220140）。"""
        figs = tmp_path / "figs"
        write(figs, "alive.py", BUFFER_ONLY_ALIVE)
        (alive,) = desktop_build(figs, "alive.py")["descriptors"]
        write(figs, "closed.py", BUFFER_ONLY_THEN_CLOSE)
        (closed,) = desktop_build(figs, "closed.py")["descriptors"]
        assert _png_call(alive) == _png_call(closed)
        assert alive["size_mm"] == closed["size_mm"] != FIGSIZE_MM

    def test_a_figure_without_pyplot_is_captured(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "oo_only.py", OO_API_BUFFER_ONLY)
        resp = desktop_build(figs, "oo_only.py")
        assert [d["stem"] for d in resp["descriptors"]] == ["oo_only"]

    def test_extra_artists_are_counted_and_do_not_break_the_frame(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "extras.py", EXTRA_ARTISTS)
        (d,) = desktop_build(figs, "extras.py")["descriptors"]
        assert _png_call(d)["bbox_extra_artists"] == 1
        assert d["size_mm"] != FIGSIZE_MM  # 框进了 figsize 之外的文字

    def test_a_printing_finalizer_during_collection_does_not_break_the_protocol(self, tmp_path):
        """引擎在脚本 stdout 重定向之后强制回收：`__del__` 里的 print 不能写进 JSON 协议流（#739 Codex P2）。"""
        figs = tmp_path / "figs"
        write(figs, "noisy.py", NOISY_FINALIZER)
        resp = desktop_build(figs, "noisy.py")
        assert [d["stem"] for d in resp["descriptors"]] == ["noisy"], resp


@needs_worker
class TestMultiFigureShape:
    """Issue #749 最后一条评论：第一张只存缓冲区后 close，其余存路径。原生 3 张。"""

    def test_desktop_brings_back_the_buffer_only_figure(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "multi.py", BUFFER_FIRST_THEN_PATHS)
        resp = desktop_build(figs, "multi.py")
        assert [d["stem"] for d in resp["descriptors"]] == ["two", "three", "multi"]
        assert [d["capture_source"] for d in resp["descriptors"]] == [
            "savefig",
            "savefig",
            "pyplot",
        ]
        assert not resp.get("dropped_figures")

    def test_browser_agrees(self, tmp_path):
        resp = browser_load(BUFFER_FIRST_THEN_PATHS, "multi.py", tmp_path / "ws")
        assert resp.get("ok"), resp
        assert [d["stem"] for d in resp["descriptors"]] == ["two", "three", "multi"]
        assert not resp.get("truncated_figures")

    def test_claimed_figures_do_not_crowd_out_a_buffer_only_one(self, tmp_path):
        """前 8 张先存缓冲区、再按路径被认领；第 9 张只存缓冲区又被关掉——它照样补回来，不算丢（#739）。"""
        figs = tmp_path / "figs"
        write(figs, "claimed.py", CLAIMED_THEN_BUFFER_ONLY)
        resp = desktop_build(figs, "claimed.py")
        assert [d["stem"] for d in resp["descriptors"]] == [
            *(f"F{i}" for i in range(8)),
            "claimed",
        ]
        assert not resp.get("dropped_figures")

    def test_figures_claimed_by_path_first_do_not_come_back_through_a_buffer(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "reverse.py", PATH_THEN_BUFFER)
        resp = desktop_build(figs, "reverse.py")
        assert [d["stem"] for d in resp["descriptors"]] == [
            *(f"F{i}" for i in range(8)),
            "reverse",
        ]
        assert not resp.get("dropped_figures")


@needs_worker
class TestFailedBufferSaveIsNotRecorded:
    def test_desktop_uses_the_successful_saves_arguments_and_forgets_the_ghost(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "failed.py", FAILED_THEN_OK)
        resp = desktop_build(figs, "failed.py")
        (d,) = resp["descriptors"]  # 只有成功存过的那一张
        assert [c["bbox_inches"] for c in d["savefig_calls"]] == [None]
        assert d["size_mm"] == FIGSIZE_MM
        assert not resp.get("dropped_figures")

    def test_browser_agrees(self, tmp_path):
        resp = browser_load(FAILED_THEN_OK, "failed.py", tmp_path / "ws")
        assert resp.get("ok"), resp
        (d,) = resp["descriptors"]
        assert [c["bbox_inches"] for c in d["savefig_calls"]] == [None]
        assert d["size_mm"] == FIGSIZE_MM


@needs_worker
class TestLossIsCountedOnce:
    def test_a_figure_over_both_ledgers_is_dropped_once(self, tmp_path):
        """9 张都超出 show 账与缓冲区账：捕获 8 张，真未捕获的只有 1 张，不是 2。"""
        figs = tmp_path / "figs"
        write(figs, "both.py", SHOW_AND_BUFFER)
        resp = desktop_build(figs, "both.py")
        assert len(resp["descriptors"]) == figcapture.MAX_PYPLOT_FALLBACK
        assert resp.get("dropped_figures") == 1


def test_one_figure_in_both_ledgers_is_counted_once():
    book = figcapture.DropBook()
    shown = figcapture.ShownFigures(limit=0, book=book)
    saves = figcapture.BufferSaves(limit=0, book=book)
    fig = _Fig()
    saves.note(fig, _call())
    book.add(fig)  # show 账同一张图也超出
    assert book.count == 1
    del fig
    assert saves.figures() == [] and saves.lost() == 1
    assert shown.book is saves.book


@needs_worker
class TestLossIsReported:
    def test_desktop_reports_what_it_could_not_keep(self, tmp_path):
        """11 张只存缓冲区、各自关掉：补回上限那 8 张，丢的 3 张进结构化计数，不只是一行日志。"""
        figs = tmp_path / "figs"
        write(figs, "many.py", MANY_BUFFER_ONLY)
        resp = desktop_build(figs, "many.py")
        assert len(resp["descriptors"]) == figcapture.MAX_PYPLOT_FALLBACK
        assert resp.get("dropped_figures") == 11 - figcapture.MAX_PYPLOT_FALLBACK

    def test_browser_reports_the_same_loss(self, tmp_path):
        resp = browser_load(MANY_BUFFER_ONLY, "many.py", tmp_path / "ws")
        assert resp.get("ok"), resp
        assert len(resp["descriptors"]) == figcapture.MAX_PYPLOT_FALLBACK
        assert resp.get("truncated_figures") == 11 - figcapture.MAX_PYPLOT_FALLBACK


@needs_worker
class TestSameDescriptorsOnBothEntries:
    """worker 与浏览器对同一脚本给出逐字段相同的 descriptors（`test_compat_capture_parity` 同形用例）。"""

    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_descriptors_are_identical(self, tmp_path, shape):
        source = SHAPES[shape]
        figs = tmp_path / "figs"
        write(figs, "s.py", source)
        desktop = desktop_build(figs, "s.py")["descriptors"]
        browser = browser_load(source, "s.py", tmp_path / "ws")
        assert browser.get("ok"), browser
        assert desktop, "不能拿空对空当一致"
        assert browser["descriptors"] == desktop

    def test_two_figures_saved_to_one_path_are_the_same_count_on_both(self, tmp_path):
        """r4140048715：先后存到同一路径的两张图，浏览器不再比桌面多出一张。"""
        figs = tmp_path / "figs"
        write(figs, "s.py", SAME_PATH_TWICE)
        desktop = desktop_build(figs, "s.py")["descriptors"]
        browser = browser_load(SAME_PATH_TWICE, "s.py", tmp_path / "ws")["descriptors"]
        assert [d["stem"] for d in browser] == [d["stem"] for d in desktop]
        assert len(browser) == 2

    def test_a_buffer_save_does_not_add_a_figure_to_a_shared_path(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "s.py", SAME_PATH_WITH_BUFFER)
        desktop = desktop_build(figs, "s.py")["descriptors"]
        browser = browser_load(SAME_PATH_WITH_BUFFER, "s.py", tmp_path / "ws")["descriptors"]
        assert [d["stem"] for d in browser] == [d["stem"] for d in desktop]
        assert all(d["capture_source"] == "savefig" for d in desktop)


# ===========================================================================
# 账本本身（纯函数，不需要 matplotlib）
# ===========================================================================
class _Fig:
    """可弱引用的替身（`object()` 不支持弱引用）。"""


class _Artist:
    pass


def _call(fmt="png", bbox="tight"):
    return {"format": fmt, "bbox_inches": bbox}


def test_the_ledger_keeps_at_most_limit_strong_references():
    """循环「建图 → 存缓冲区 → 关图」的批量脚本不能把每张关掉的图都留到脚本结束：强引用最多 `limit` 张，
    超出的只挂弱引用；被回收了的计入 `lost()`。"""
    saves = figcapture.BufferSaves(limit=3)
    figs = [_Fig() for _ in range(5)]
    for i in range(5):  # 不用 `for f in figs`：循环变量会一直引用最后一张，它就回收不掉
        saves.note(figs[i], _call())
    saves.note(figs[0], _call())  # 同一张重复存：不占位、不算溢出
    assert len(saves._pinned) == 3
    assert saves.figures() == figs  # 超出的两张还活着：照样补回来
    assert saves.lost() == 0
    del figs[3:]  # 超出的两张被脚本关掉、不再有人引用
    assert saves.figures() == figs[:3]
    assert saves.lost() == 2


def test_the_ledger_collects_garbage_before_deciding_what_was_lost():
    """Figure 有引用环，回收时机不定：不先 `gc.collect()`，「活着的补回来、死了的算丢」会随 GC 时机漂移。"""

    class _Cyclic:
        def __init__(self):
            self.me = self  # 引用环：引用计数归零也不会立即回收

    saves = figcapture.BufferSaves(limit=0)
    gc.disable()
    try:
        saves.note(_Cyclic(), _call())  # 满了：只挂弱引用；随即没人引用它，但环还在
        assert saves.figures() == []
        assert saves.lost() == 1
    finally:
        gc.enable()


def test_a_released_slot_goes_to_the_oldest_waiting_figure():
    """a、b 钉满名额，c 只挂弱引用；a 被认领让出的名额必须给**更早的** c，而不是之后才存的 d。"""
    saves = figcapture.BufferSaves(limit=2)
    a, b, c = _Fig(), _Fig(), _Fig()
    for f in (a, b, c):
        saves.note(f, _call())
    saves.claim(a)
    c_ref = weakref.ref(c)
    del c, f  # 脚本不再引用 c：只有名单里的强引用能留住它
    d = _Fig()
    saves.note(d, _call())
    assert saves.figures() == [b, c_ref(), d]
    assert saves.lost() == 0


def test_with_several_waiting_the_oldest_gets_the_slot():
    saves = figcapture.BufferSaves(limit=1)
    a, b, c = _Fig(), _Fig(), _Fig()
    for f in (a, b, c):
        saves.note(f, _call())
    del f
    saves.claim(a)
    b_ref, c_ref = weakref.ref(b), weakref.ref(c)
    del b, c
    assert saves.figures() == [b_ref()]
    assert b_ref() is not None and c_ref() is None
    assert saves.lost() == 1


def test_a_claimed_figure_saved_to_a_buffer_later_is_not_noted_again():
    saves = figcapture.BufferSaves(limit=1)
    a, b = _Fig(), _Fig()
    saves.claim(a)  # 先按路径存盘被认领
    saves.note(a, _call())  # 之后又存缓冲区
    saves.note(b, _call())
    assert saves.figures() == [b]
    assert saves.calls_of(a) is None
    assert b in saves._pinned.values()


def test_claimed_figures_give_back_their_slot_and_are_not_lost():
    saves = figcapture.BufferSaves(limit=2)
    a, b, c = _Fig(), _Fig(), _Fig()
    saves.note(a, _call())
    saves.note(b, _call())
    saves.claim(a)
    saves.note(c, _call())
    assert saves.figures() == [b, c]
    d = _Fig()
    saves.note(d, _call())  # 满了：进弱引用区
    saves.claim(d)  # 又被认领：不算丢
    del d
    assert saves.lost() == 0 and saves.figures() == [b, c]


def test_collection_output_goes_to_stderr(capsys):
    """回收期间 `__del__` 的 print 改道 stderr：stdout 在 worker 里是协议流。"""

    class _Noisy:
        def __init__(self):
            self.me = self

        def __del__(self):
            print("bye")

    saves = figcapture.BufferSaves()
    gc.disable()
    try:
        _Noisy()
        saves.figures()
    finally:
        gc.enable()
    out = capsys.readouterr()
    assert out.out == "" and "bye" in out.err


def test_every_buffer_call_of_a_figure_is_kept_in_order():
    saves = figcapture.BufferSaves()
    fig = _Fig()
    saves.note(fig, _call("png"))
    saves.note(fig, _call("pdf", None))
    calls, extras = saves.calls_of(fig)
    assert [c["format"] for c in calls] == ["png", "pdf"]
    assert extras == [None, None]


def test_an_unobserved_call_makes_the_figures_record_unknown():
    saves = figcapture.BufferSaves()
    fig = _Fig()
    saves.note(fig, _call())
    saves.note(fig, None)
    saves.note(fig, _call())
    assert saves.calls_of(fig) == (None, [])


def test_extra_artists_do_not_keep_a_figure_alive():
    """artist 引用它所在的图：账里存强引用的话，被脚本关掉又出了作用域的图永远回收不掉。"""
    saves = figcapture.BufferSaves(limit=0)
    fig, artist = _Fig(), _Artist()
    artist.figure = fig
    fig.artists = [artist]
    saves.note(fig, _call(), [artist])
    calls, extras = saves.calls_of(fig)
    assert extras == [[artist]]
    fig_ref = weakref.ref(fig)
    del fig, artist, calls, extras
    assert saves.figures() == [] and fig_ref() is None
    assert saves.lost() == 1


def test_retained_figures_are_collected_without_pyplot():
    capture: dict = {}
    calls: dict = {}
    extras: dict = {}
    saves = figcapture.BufferSaves()
    fig = _Fig()
    saves.note(fig, _call())
    stems, dropped = figcapture.collect_pyplot_figures(
        capture, "oo", None, buffers=saves, savefig_calls=calls, savefig_extras=extras
    )
    assert (stems, dropped) == (["oo"], 0)
    assert capture == {"oo": fig}
    assert calls == {"oo": [_call()]} and extras == {"oo": [None]}
    assert figcapture.savefig_calls_of(calls, "oo", figcapture.SOURCE_PYPLOT) == [_call()]


def test_a_pyplot_capture_that_never_touched_a_buffer_still_says_never_saved():
    assert figcapture.savefig_calls_of({}, "x", figcapture.SOURCE_PYPLOT) == []
    assert not figcapture.defines_frame(figcapture.SOURCE_PYPLOT, None)
    assert not figcapture.defines_frame(figcapture.SOURCE_PYPLOT, [])
    assert figcapture.defines_frame(figcapture.SOURCE_PYPLOT, [_call()])
    assert figcapture.defines_frame(figcapture.SOURCE_SAVEFIG, None)
