"""存盘后脚本又改过这张图：如实告知（ADR 0139 §八），不额外渲染、不复制 Figure。

Tavotto 编辑的是脚本跑完时的最终 Figure；脚本 `savefig` 之后又改了它，磁盘上的文件与编辑基础就不同。
系统不声称拥有保存瞬间的快照，但要让用户知道「这张图和你的文件不一样」。

判据（`figcapture.mark_saved` / `note_modified_after_save`）：每次保存记完账把 `fig.stale` 清成 False，
脚本跑完时仍为 True = 存完又被改过。matplotlib 自己的 `print_figure` 收尾会把 `stale` 置 True，所以清零在
保存**之后**；同 stem 惰性验证那次真渲染是我们的动作，`stale` 原样还回去；每张图只判一次，之后 Tavotto
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
    置 True；它是我们的动作，甲不能被报成「存完又改过」。"""
    man, _ = _manifest(tmp_path, SAME_STEM, stem="panel")
    assert "modified_after_save" not in man


def test_mark_and_note_are_one_shot_per_figure():
    class Fig:
        stale = True

    fig = Fig()
    figcapture.mark_saved(fig)
    assert fig.stale is False
    capture, source = {"a": fig}, {"a": figcapture.SOURCE_SAVEFIG}
    assert figcapture.note_modified_after_save(capture, source) == []
    fig.stale = True  # 之后（比如 Tavotto 自己的编辑）再变 stale：结论不变
    assert figcapture.note_modified_after_save(capture, source) == []
    # pyplot 兜底的图从没存过盘：不判
    other = Fig()
    assert figcapture.note_modified_after_save({"b": other}, {"b": figcapture.SOURCE_PYPLOT}) == []
