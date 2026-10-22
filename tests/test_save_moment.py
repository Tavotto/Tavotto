"""保存瞬间快照（ADR 0139 §九）：脚本 `savefig` 之后又改了 Figure，可编辑基础用**保存那一刻**的复制品。

三件要钉死的事：
1. 还原出的 Figure 与「脚本到保存那一行就结束」的参照在像素上逐字节一致（才配叫「保存瞬间」）；
2. 拍不了 / 还原不了一律退回最终态，原因落在闭集里（`save_moment_unavailable`），行为与 #1250 相同；
3. 热会话与写回的一次性重放（同一份脚本、同一个 worker 代码路径）看到同一个 Figure——写回事务不变式。
"""

from __future__ import annotations

import ast
import filecmp
import os
from pathlib import Path
from unittest import mock

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
        and any(isinstance(t, ast.Name) and t.id == "SAVE_MOMENT_ATTR" for t in n.targets)
    )
    assert value == figcapture.SAVE_MOMENT


HEAD = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def main():
    fig, ax = plt.subplots(figsize=(2.4, 1.8))
    line, = ax.plot([0, 1, 2], [0, 1, 4], label="a")
    ax.legend()
    ax.set_title("saved")
"""

#: 参照：脚本在保存那一行就结束——它跑完的最终态就是保存瞬间
REFERENCE = HEAD + '    fig.savefig("fig.png")\n'

MUTATIONS = {
    "title_and_color": '    ax.set_title("mutated")\n    line.set_color("red")\n',
    "add_line": '    ax.plot([0, 1, 2], [4, 1, 0], color="g")\n',
    "figsize": "    fig.set_size_inches(3.0, 2.0)\n",
    "text": '    fig.text(0.5, 0.5, "late")\n',
}


def _build(tmp_path, source, name, env=None):
    d = tmp_path / name
    d.mkdir()
    (d / "s.py").write_text(source, encoding="utf-8")
    with mock.patch.dict(os.environ, env or {}):
        return pool.one_shot("s.py", str(d), "main")


def _run(tmp_path, source, name, patches=(), stem="fig", env=None, width=300):
    w = _build(tmp_path, source, name, env)
    try:
        w.ensure_built()
        man = w.override(stem, list(patches))["manifest"]
        png = Path(tmp_path / f"{name}.png")
        png.write_bytes(Path(w.render_png(stem, width)).read_bytes())
        return man, png
    finally:
        pool.discard(w)


@needs_worker
@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_editing_base_is_the_figure_at_the_moment_of_the_save(tmp_path, case):
    ref_man, ref_png = _run(tmp_path, REFERENCE, "ref")
    man, png = _run(tmp_path, REFERENCE + MUTATIONS[case], "mut")
    assert man["modified_after_save"] is True
    assert man.get("save_moment") == "restored", man.get("save_moment_unavailable")
    assert "save_moment_unavailable" not in man
    assert "modified_after_save" not in ref_man
    assert filecmp.cmp(ref_png, png, shallow=False), "保存瞬间的复制品必须与参照逐字节一致"
    assert man["size_mm"] == ref_man["size_mm"]


@needs_worker
def test_without_the_snapshot_the_base_would_be_the_final_state(tmp_path):
    """反证：关掉快照，同一个脚本的编辑基础是最终态——与参照不同（证明上一条不是假绿）。"""
    ref_man, ref_png = _run(tmp_path, REFERENCE, "ref")
    man, png = _run(
        tmp_path,
        REFERENCE + MUTATIONS["title_and_color"],
        "off",
        env={figcapture.SAVE_MOMENT_ENV: "0"},
    )
    assert man["modified_after_save"] is True
    assert "save_moment" not in man and "save_moment_unavailable" not in man
    assert not filecmp.cmp(ref_png, png, shallow=False)


@needs_worker
def test_the_last_save_of_the_same_figure_is_the_one_restored(tmp_path):
    """同一张图多次保存：取最后一次（与「改动判据是最近一次保存之后」同一时刻）。"""
    twice = (
        HEAD
        + '    fig.savefig("fig.png")\n    ax.set_title("second")\n    fig.savefig("fig.png")\n'
        + MUTATIONS["add_line"]
    )
    ref = (
        HEAD
        + '    fig.savefig("fig.png")\n    ax.set_title("second")\n    fig.savefig("fig.png")\n'
    )
    _, ref_png = _run(tmp_path, ref, "ref")
    man, png = _run(tmp_path, twice, "twice")
    assert man.get("save_moment") == "restored", man.get("save_moment_unavailable")
    assert filecmp.cmp(ref_png, png, shallow=False)


@needs_worker
def test_unpicklable_figure_falls_back_to_the_final_state_and_says_why(tmp_path):
    lam = HEAD.replace(
        '    ax.set_title("saved")\n',
        '    ax.set_title("saved")\n'
        '    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, p: f"{x}u"))\n',
    )
    man, _ = _run(tmp_path, lam + '    fig.savefig("fig.png")\n' + MUTATIONS["add_line"], "lam")
    assert man["modified_after_save"] is True
    assert man["save_moment_unavailable"] == "unpicklable"
    assert "save_moment" not in man
    assert len([e for e in man["elements"] if e["gid"].startswith("axes_0.line")]) == 2


@needs_worker
def test_user_classes_are_never_restored(tmp_path):
    """用户自己的类能被 pickle（按名字引用）但还原时被白名单拒绝：不执行用户代码，退回最终态。"""
    src = (
        HEAD.replace(
            "def main():",
            "class MyFmt(matplotlib.ticker.ScalarFormatter):\n    pass\n\n\ndef main():",
        ).replace(
            '    ax.set_title("saved")\n',
            '    ax.set_title("saved")\n    ax.xaxis.set_major_formatter(MyFmt())\n',
        )
        + '    fig.savefig("fig.png")\n'
        + MUTATIONS["add_line"]
    )
    man, _ = _run(tmp_path, src, "cls")
    assert man["modified_after_save"] is True
    assert man["save_moment_unavailable"] in ("unpicklable", "restore_failed")


@needs_worker
def test_bbox_extra_artists_are_not_snapshotted(tmp_path):
    src = (
        HEAD
        + '    t = fig.text(0.5, 0.01, "x")\n    fig.savefig("fig.png", bbox_extra_artists=[t])\n'
        + MUTATIONS["add_line"]
    )
    man, _ = _run(tmp_path, src, "extra")
    assert man["save_moment_unavailable"] == "bbox_extra_artists"


@needs_worker
def test_one_figure_under_two_stems_is_not_guessed(tmp_path):
    src = (
        HEAD
        + '    fig.savefig("a.png")\n    ax.set_title("b")\n    fig.savefig("b.png")\n'
        + MUTATIONS["add_line"]
    )
    w = _build(tmp_path, src, "two")
    try:
        stems = list(w.ensure_built()["stems"])
        assert stems == ["a", "b"]
        for stem in stems:
            man = w.override(stem, [])["manifest"]
            assert man["modified_after_save"] is True
            assert man["save_moment_unavailable"] == "multi_stem"
    finally:
        pool.discard(w)


@needs_worker
def test_unchanged_after_save_figure_is_untouched(tmp_path):
    man, _ = _run(tmp_path, REFERENCE, "same")
    for key in ("modified_after_save", "save_moment", "save_moment_unavailable"):
        assert key not in man


@needs_worker
def test_hot_session_and_fresh_replay_see_the_same_snapshot_figure(tmp_path):
    """写回事务不变式：热态所见 == 一次性 worker 从零重放所得——快照在重放里同样被拍、同样被采用。"""
    patches = [{"gid": "axes_0.title", "prop": "text", "value": "edited"}]
    src = REFERENCE + MUTATIONS["title_and_color"]
    man_a, png_a = _run(tmp_path, src, "hot", patches)
    man_b, png_b = _run(tmp_path, src, "replay", patches)
    assert man_a["save_moment"] == man_b["save_moment"] == "restored"
    assert filecmp.cmp(png_a, png_b, shallow=False)
    # 编辑落在保存瞬间的图上：线的颜色是保存时的，不是后来改的红色
    ref_man, _ = _run(tmp_path, REFERENCE, "ref", patches)
    assert [e.get("color") for e in man_a["elements"]] == [
        e.get("color") for e in ref_man["elements"]
    ]


# ---- 单元：账本本身（不起 worker） ----


def _figure():
    mpl = pytest.importorskip("matplotlib")
    mpl.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(2, 1.5))
    ax.plot([0, 1], [0, 1])
    return fig


def test_taking_a_snapshot_does_not_touch_the_figure_or_its_stale_state():
    fig = _figure()
    figcapture.mark_saved(fig)
    before = (fig.stale, getattr(fig, figcapture.DIRTY_SINCE_SAVE), type(fig.canvas.manager))
    mgr = fig.canvas.manager
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert (
        fig.stale,
        getattr(fig, figcapture.DIRTY_SINCE_SAVE),
        type(fig.canvas.manager),
    ) == before
    assert fig.canvas.manager is mgr, "临时摘掉的 canvas.manager 必须放回"
    restored, reason = ledger.restore(fig)
    assert reason is None and restored is not fig
    import matplotlib.pyplot as plt

    assert restored.number not in plt.get_fignums() or restored is not plt.figure(fig.number)
    assert not hasattr(restored, figcapture.DIRTY_SINCE_SAVE)
    plt.close("all")


def test_budget_is_bytes_not_wall_clock(monkeypatch):
    fig = _figure()
    ledger = figcapture.SaveMomentLedger()
    monkeypatch.setattr(figcapture, "SAVE_MOMENT_MAX_BYTES", 10)
    ledger.take(fig, 1)
    assert ledger.restore(fig) == (None, "too_large")
    monkeypatch.setattr(figcapture, "SAVE_MOMENT_MAX_BYTES", 10**9)
    monkeypatch.setattr(figcapture, "SAVE_MOMENT_TOTAL_BYTES", 10)
    ledger.take(fig, 2)
    assert ledger.restore(fig) == (None, "budget_exceeded")


def test_selected_occurrence_must_be_the_snapshotted_save():
    fig = _figure()
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 3)
    assert ledger.restore(fig, 2) == (None, "not_selected_save")
    assert ledger.restore(fig, 3)[1] is None


class _UserThing:
    pass


def test_restore_refuses_classes_outside_matplotlib_numpy():
    fig = _figure()
    fig._user_thing = _UserThing()
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert ledger.restore(fig) == (None, "restore_failed")


def test_reasons_are_a_closed_set_and_release_frees_bytes():
    fig = _figure()
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert ledger._total > 0
    ledger.release()
    assert ledger._total == 0
    assert figcapture.SAVE_MOMENT_RESTORED not in figcapture.SAVE_MOMENT_REASONS


def _builders():
    import numpy as np

    def basic(plt):
        fig, ax = plt.subplots(figsize=(3, 2))
        ax.plot([1, 2, 3], [1, 4, 9], label="a")
        ax.legend()
        ax.set_title("t")
        return fig

    def rich(plt):
        fig, ax = plt.subplots(figsize=(4, 3))
        sc = ax.scatter(np.arange(50), np.arange(50) ** 0.5, c=np.arange(50))
        fig.colorbar(sc, ax=ax)
        ax.errorbar([1, 2], [1, 2], yerr=0.2)
        ax.fill_between([0, 1, 2], [0, 1, 0], [1, 2, 1], alpha=0.3)
        ax.annotate("x", (1, 1), (10, 10), arrowprops={"arrowstyle": "->"})
        return fig

    def twin_tight(plt):
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.plot([1, 2])
        ax.twinx().plot([3, 1])
        fig.tight_layout()
        return fig

    def constrained(plt):
        fig, axs = plt.subplots(2, 2, figsize=(5, 4), layout="constrained")
        for a in axs.flat:
            a.imshow(np.random.RandomState(0).rand(5, 5))
        return fig

    def three_d(plt):
        fig = plt.figure()
        fig.add_subplot(projection="3d").plot([1, 2], [1, 2], [1, 2])
        return fig

    return [basic, rich, twin_tight, constrained, three_d]


@pytest.mark.parametrize("build", _builders(), ids=lambda f: f.__name__)
def test_restored_figure_renders_pixel_identical_to_the_original(build):
    """还原保真度：同一时刻、同一 dpi，复制品与原件逐字节同一张 PNG（多种 artist 家族）。"""
    import io

    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    def png(fig):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        return buf.getvalue()

    fig = build(plt)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    restored, reason = ledger.restore(fig)
    assert reason is None
    FigureCanvasAgg(restored)
    assert png(restored) == png(fig)
    plt.close("all")


WRITE_BACK_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1, 2], [0, 1, 4], label="saved-series")
    ax.legend()
    ax.set_title("saved title")
    fig.savefig("Snap.pdf")
    ax.set_title("LATE TITLE")
    fig.text(0.5, 0.5, "LATE TEXT")
"""


@needs_worker
def test_write_back_writes_the_saved_moment_plus_edits_and_replay_verifies(tmp_path, monkeypatch):
    """全链路（真 matplotlib + Flask + 一次性重放）：写回的 PDF = 保存瞬间 + 用户的编辑，后来的改动不在其中；
    热态 == 干净重放（`verification.replay == ok`、像素门过）。"""
    import json

    import pymupdf

    from tavotto import app as m
    from tavotto.engine import project_watch

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_snap.py").write_text(WRITE_BACK_SCRIPT, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig_snap.py": {
                        "entry": "main",
                        "cost": "light",
                        "notes": "",
                        "stems": ["Snap"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    doc = pymupdf.open()
    doc.new_page(width=216, height=144)
    doc.save(figs / "Snap.pdf")
    doc.close()
    m.open_project(str(figs))
    client = m.app.test_client()
    try:
        patches = [{"gid": "axes_0.title", "prop": "text", "value": "edited title"}]
        r = client.post("/api/engine/render", json={"id": "Snap.pdf", "patches": patches})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["manifest"]["save_moment"] == "restored"
        resp = client.post(
            "/api/engine/update_source",
            json={
                "id": "Snap.pdf",
                "patches": patches,
                "expected_mtime": int((figs / "Snap.pdf").stat().st_mtime),
            },
        )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["verification"]["replay"] == "ok", body["verification"]
        assert body["verification"]["pixels"] == "ok", body["verification"]
        with pymupdf.open(figs / "Snap.pdf") as written:
            text = written[0].get_text()
        assert "edited title" in text and "saved-series" in text
        assert "LATE TEXT" not in text and "LATE TITLE" not in text
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()
