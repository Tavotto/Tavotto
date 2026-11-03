"""存盘后脚本又改过这张图：如实告知（ADR 0139 §八），不额外渲染、不复制 Figure。

Tavotto 编辑的是脚本跑完时的最终 Figure；脚本 `savefig` 之后又改了它，磁盘上的文件与编辑基础就不同。
系统不声称拥有保存瞬间的快照，但要让用户知道「这张图和你的文件不一样」。

判据（`figcapture.mark_saved` / `note_modified_after_save`）：给 Figure 的 `stale_callback` 套一层只记录的包装，
每次保存记完账把私有标记清成 False，脚本跑完时标记为 True = 存完又被改过。**`fig.stale` 一处不写**——用户脚本读到的
stale 与原生 matplotlib 完全一致；同 stem 惰性验证那次真渲染是我们的动作，标记原样还回去；每张图只判一次，之后 Tavotto
自己的编辑不算。manifest 只在为真时带 `modified_after_save: true`。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tavotto.engine import figcapture, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)

ENGINE = Path(figcapture.__file__).resolve().parent


def test_manifest_reads_the_same_marker_name_as_figcapture():
    """manifest 不 import figcapture，两边各写一个字面量：钉住相等（同源对）。"""
    tree = ast.parse((ENGINE / "manifest.py").read_text(encoding="utf-8"))
    value = next(
        n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "MODIFIED_AFTER_SAVE_ATTR" for t in n.targets)
    )
    assert value == figcapture.MODIFIED_AFTER_SAVE


HEAD = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(2, 1.5))
    line, = ax.plot([0, 1], [0, 1])
    ax.set_title("saved")
"""

CASES = {
    # 存完又改：标题与线色
    "mutated": HEAD
    + '    fig.savefig("fig.png")\n    ax.set_title("mutated")\n    line.set_color("red")\n',
    # 存完只关
    "close_only": HEAD + '    fig.savefig("fig.png")\n    plt.close(fig)\n',
    # tight 保存（matplotlib 自己会动一下再还原）
    "tight": HEAD + '    fig.savefig("fig.png", bbox_inches="tight")\n',
    # 同一张图两种格式，最后一次之后没改
    "two_formats": HEAD + '    fig.savefig("fig.png")\n    fig.savefig("fig.pdf")\n',
    # 改完再存：最后一次保存之后没改
    "saved_again": HEAD
    + '    fig.savefig("fig.png")\n    ax.set_title("final")\n    fig.savefig("fig.png")\n',
    # 存完脚本自己重绘一次（draw 中 matplotlib 会自己置 stale）：没改图
    "draw_only": HEAD + '    fig.savefig("fig.png")\n    fig.canvas.draw()\n',
    # 只改 rcParams：不改已有的 artist，不算改图
    "rc_only": HEAD + '    fig.savefig("fig.png")\n    plt.rcParams["lines.linewidth"] = 5\n',
}
EXPECT = {
    "mutated": True,
    "close_only": False,
    "tight": False,
    "two_formats": False,
    "saved_again": False,
    "rc_only": False,
    "draw_only": False,
}


def _manifest(tmp_path, source, stem="fig", patches=()):
    (tmp_path / "s.py").write_text(source, encoding="utf-8")
    w = pool.one_shot("s.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        man = w.override(stem, list(patches))["manifest"]
        return man, w.override(stem, [])["manifest"]
    finally:
        pool.discard(w)


@needs_worker
@pytest.mark.parametrize("case", sorted(CASES))
def test_safe_worker_reports_only_a_real_change_after_the_last_save(tmp_path, case):
    man, _ = _manifest(tmp_path, CASES[case])
    assert bool(man.get("modified_after_save")) is EXPECT[case], man.get("modified_after_save")
    if not EXPECT[case]:
        assert "modified_after_save" not in man, "为假时不出现这个键"


@needs_worker
def test_tavotto_edits_do_not_count_as_the_script_changing_it(tmp_path):
    """判一次就定：之后 Tavotto 自己的编辑（改标题）不会让一张没被脚本改过的图翻成「已改」。"""
    man, back = _manifest(
        tmp_path,
        CASES["close_only"],
        patches=[{"gid": "axes_0.title", "prop": "text", "value": "edited in Tavotto"}],
    )
    assert "modified_after_save" not in man
    assert "modified_after_save" not in back


SAME_STEM = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    a, ax = plt.subplots(figsize=(2, 1.5))
    a.savefig("panel.png")
    a.savefig("panel.pdf")
    b, bx = plt.subplots(figsize=(2, 1.5))
    b.savefig("panel.pdf")
"""


@needs_worker
def test_the_lazy_verify_render_of_a_holder_is_not_a_script_change(tmp_path):
    """乙写 `panel.pdf` 时要验证持有者甲在 `panel.png` 上仍是最近写入者——那次真渲染会把甲的 `stale`
    触发 stale 回调；它是我们的动作，甲不能被报成「存完又改过」。"""
    man, _ = _manifest(tmp_path, SAME_STEM, stem="panel")
    assert "modified_after_save" not in man


def _real_figure():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    fig = Figure(figsize=(2, 1.5))
    ax = fig.subplots()
    (line,) = ax.plot([0, 1], [0, 1])
    return fig, ax, line


def _native_stale_after_save(install):
    import io

    fig, _, _ = _real_figure()
    fig.savefig(io.BytesIO())
    if install:
        figcapture.mark_saved(fig)
    return fig.stale


def test_mark_and_note_are_one_shot_per_figure():
    fig, ax, _ = _real_figure()
    figcapture.mark_saved(fig)
    capture, source = {"a": fig}, {"a": figcapture.SOURCE_SAVEFIG}
    assert figcapture.note_modified_after_save(capture, source) == []
    ax.set_title("later")  # 之后（比如 Tavotto 自己的编辑）再变：结论不变
    assert figcapture.note_modified_after_save(capture, source) == []
    # 没存过盘的图（pyplot 兜底）不判
    other, ax2, _ = _real_figure()
    ax2.set_title("x")
    assert figcapture.note_modified_after_save({"b": other}, {"b": figcapture.SOURCE_PYPLOT}) == []


@pytest.mark.parametrize(
    "change",
    [
        lambda fig, ax, line: ax.set_title("t"),
        lambda fig, ax, line: line.set_color("red"),
        lambda fig, ax, line: fig.set_size_inches(3, 3),
        lambda fig, ax, line: fig.text(0.5, 0.5, "x"),
        lambda fig, ax, line: ax.cla(),
    ],
)
def test_any_visual_change_after_save_is_recorded(change):
    import io

    fig, ax, line = _real_figure()
    fig.savefig(io.BytesIO())
    figcapture.mark_saved(fig)
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is False
    change(fig, ax, line)
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is True
    assert figcapture.note_modified_after_save({"a": fig}, {"a": figcapture.SOURCE_SAVEFIG}) == [
        "a"
    ]


def test_mark_saved_does_not_change_public_stale_state():
    """savefig 之后 `fig.stale` 与不装记录时完全一致（原生 matplotlib 是 True）；装多次也一样。"""
    import io

    assert _native_stale_after_save(install=True) is _native_stale_after_save(install=False)
    fig, _, _ = _real_figure()
    fig.savefig(io.BytesIO())
    native = fig.stale
    figcapture.mark_saved(fig)
    figcapture.mark_saved(fig)
    assert fig.stale is native


def test_original_stale_callback_still_called_and_wrapped_once():
    import io

    fig, ax, _ = _real_figure()
    calls = []
    fig.stale_callback = lambda artist, val: calls.append(val)
    fig.savefig(io.BytesIO())
    figcapture.mark_saved(fig)
    first = fig.stale_callback
    figcapture.mark_saved(fig)
    assert fig.stale_callback is first, "重复保存不叠层"
    assert first.orig is not first and not isinstance(first.orig, type(first))
    calls.clear()
    ax.set_title("changed")
    assert calls, "原回调仍被调用"
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is True


def test_user_replacing_stale_callback_never_raises():
    import io

    fig, ax, _ = _real_figure()
    fig.savefig(io.BytesIO())
    figcapture.mark_saved(fig)
    fig.stale_callback = None  # 用户自己换掉：记录丢失，但不能抛
    ax.set_title("x")
    figcapture.mark_saved(fig)  # 下次保存重新套上
    ax.set_title("y")
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is True


def test_the_lazy_verify_render_leaves_the_users_stale_as_it_was():
    """惰性验证渲染发生在**另一张图**保存时：持有者的 `fig.stale`（用户可见）与私有标记都原样还回去。"""
    import io

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure()
    FigureCanvasAgg(fig)
    fig.add_subplot().plot([0, 1])
    fig.savefig(io.BytesIO(), format="png")
    figcapture.mark_saved(fig)
    fig.canvas.draw()  # 脚本自己画过一次：此刻 stale 为 False
    assert fig.stale is False
    real = Figure.savefig
    assert figcapture.savefig_verify_render(
        lambda f, *a, **k: real(f, *a, **k), fig, "x.png", (), {}
    )
    assert fig.stale is False
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is False


def _saved_agg_figure():
    import io

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    ax.plot([0, 1])
    fig.savefig(io.BytesIO(), format="png")
    figcapture.mark_saved(fig)
    return fig, ax


def test_a_redraw_after_save_is_not_a_change():
    """draw 过程中 matplotlib 自己会多次置 stale；`draw_event` 画完清标记，没改图就不报告。"""
    fig, _ = _saved_agg_figure()
    fig.canvas.draw()
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is False


def test_a_change_after_save_is_reported_until_the_next_redraw():
    fig, ax = _saved_agg_figure()
    ax.set_title("changed")
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is True
    # 已知盲区，与旧的「清 stale」语义一致：改完又重绘过，不再报告
    fig.canvas.draw()
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is False


def _our_draw_callbacks(fig):
    return [
        cb for cb in fig.canvas.callbacks.callbacks.get("draw_event", {}).values() if cb is not None
    ]


def test_hooks_live_only_until_the_verdict_then_the_figure_is_native_again():
    """采纳检查（`figsession._require_artifact_figure`）拒绝任何 draw_event 回调与非标准 stale_callback：
    钩子判定时必须卸干净，`stale_callback` 恢复成原值（`is` 相同），且判过之后再 `mark_saved` 不再装。"""
    import io

    fig, ax, _ = _real_figure()
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    FigureCanvasAgg(fig)
    original = fig.stale_callback
    before = _our_draw_callbacks(fig)
    fig.savefig(io.BytesIO(), format="png")
    figcapture.mark_saved(fig)
    assert isinstance(fig.stale_callback, figcapture._StaleRecorder)
    assert len(_our_draw_callbacks(fig)) == len(before) + 1
    ax.set_title("x")
    capture, source = {"a": fig}, {"a": figcapture.SOURCE_SAVEFIG}
    assert figcapture.note_modified_after_save(capture, source) == ["a"]
    assert fig.stale_callback is original
    assert _our_draw_callbacks(fig) == before
    figcapture.mark_saved(fig)  # 已判过：不再装
    assert fig.stale_callback is original
    assert _our_draw_callbacks(fig) == before
    assert figcapture.note_modified_after_save(capture, source) == ["a"], "结论不变"


def test_unhook_keeps_a_callback_the_user_installed():
    import io

    fig, _, _ = _real_figure()
    fig.savefig(io.BytesIO())
    figcapture.mark_saved(fig)
    mine = lambda artist, val: None  # noqa: E731
    fig.stale_callback = mine
    figcapture.note_modified_after_save({"a": fig}, {"a": figcapture.SOURCE_SAVEFIG})
    assert fig.stale_callback is mine
