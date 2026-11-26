"""图内排版（ADR 0199 §十二）：开源侧的认轴、跟随、核对与事务，不要闭源求解器。

真实几何来自 worker 解释器里真渲染的 manifest（一次子进程把几张夹具图的 B0 manifest 都吐出来）；
端到端那几条在 worker 解释器里整段跑 `quicklayout.plan_figure`，求解器换成本文件里一个最小的假求解器。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool, prohost, quicklayout, quicklayoutfig

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
ENGINE_DIR = SRC / "tavotto" / "engine"

try:
    WORKER_PY = pool.find_worker_python()
except Exception:  # noqa: BLE001 — 没有带 matplotlib 的解释器：真几何用例跳过
    WORKER_PY = None

needs_worker = pytest.mark.skipif(WORKER_PY is None, reason="no worker python with matplotlib")

#: 夹具图（worker 解释器里执行）：维护者 2026-11-23 实例的结构——上排三个示意图（等比例）由一个 (a) 管、
#: 中间两个频率图共用一根色条与一个 supylabel、最下面一块左轴线不齐；外加 twin / inset / 子轴 / 只有一个轴。
FIXTURES = r"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def sample():
    fig = plt.figure(figsize=(86 / 25.4, 132 / 25.4))
    gs = fig.add_gridspec(4, 3, height_ratios=[1, 1, 1, 1.1])
    for i in range(3):
        a = fig.add_subplot(gs[0, i]); a.imshow(np.arange(64).reshape(8, 8) % (i + 3)); a.set_xticks([]); a.set_yticks([])
    b1 = fig.add_subplot(gs[1, :]); b2 = fig.add_subplot(gs[2, :])
    x = np.linspace(0, 1, 30); X, Y = np.meshgrid(x, np.linspace(1.30, 1.38, 20))
    m = b1.pcolormesh(X, Y, np.sin(X * 6) * Y); b2.pcolormesh(X, Y, np.cos(X * 6) * Y)
    fig.colorbar(m, ax=[b1, b2], label="Amp")
    fig.supylabel("Frequency (THz)")
    c = fig.add_axes([0.2, 0.06, 0.7, 0.17]); c.plot(x, x ** 2); c.set_xlabel("Time (ps)"); c.set_ylabel("E")
    fig.text(0.02, 0.97, "(a)"); fig.text(0.02, 0.72, "(b)")
    b2.text(-0.15, 1.0, "(c)", transform=b2.transAxes); fig.text(0.02, 0.24, "(d)")
    return fig

def twin():
    fig, (a, b) = plt.subplots(2, 1, figsize=(4, 5))
    a.plot([1, 2]); t = a.twinx(); t.plot([3, 1]); t.set_ylabel("right")
    b.plot([1, 3]); b.set_ylabel("left")
    return fig

def twin_only():
    fig, a = plt.subplots(figsize=(4, 3))
    a.plot([1, 2]); a.twinx().plot([3, 1])
    return fig

def inset():
    fig, (a, b) = plt.subplots(1, 2, figsize=(6, 3))
    a.plot([1, 2]); b.plot([2, 1]); a.inset_axes([0.55, 0.55, 0.4, 0.4]).plot([1, 3, 2])
    return fig

def secondary():
    fig, (a, b) = plt.subplots(1, 2, figsize=(6, 3))
    a.plot([1, 2]); b.plot([2, 1])
    a.secondary_xaxis("top", functions=(lambda v: 2 * v, lambda v: v / 2)).set_xlabel("top")
    return fig

def stray_labels():
    fig, (a, b) = plt.subplots(1, 2, figsize=(6, 3))
    a.plot([1, 2]); b.plot([2, 1])
    fig.text(0.02, 0.95, "(a)"); fig.text(0.5, 0.95, "(c)")
    return fig

def roomy_stray_axes():
    # 轴里画的「A」「C」：不是从 a 起连续的序列，认不出 → 画布上加 (a)(b)，脚本的字不挪
    fig = roomy()
    for ax, t in zip(fig.axes, "AC"):
        ax.text(-0.1, 1.04, t, transform=ax.transAxes)
    return fig

def roomy_stray_fig():
    # 图级的一个孤零零「(b)」，正落在画布标签要放的地方：不许叠字 → 整份作废
    fig = roomy()
    fig.text(0.1, 0.78, "(b)")
    return fig

def roomy():
    # 两个轴、四周留足了地方：假求解器原样摆回去也能过验收（用来测事务的管线，不测求解）
    fig = plt.figure(figsize=(80 / 25.4, 50 / 25.4))
    for x in (0.16, 0.64):
        ax = fig.add_axes([x, 0.2, 0.3, 0.55]); ax.plot([0, 1], [0, 1]); ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
    return fig
"""

DRIVER = r"""
import json, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import logging; logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
import manifest, overrides
exec(sys.argv[3])

def render_of(fig):
    st = overrides.FigState(fig); manifest.instrument(st)
    def render(patches):
        w = overrides.apply(st, patches) or []
        return {"manifest": manifest.build_manifest(st, "S"), "warnings": [str(x) for x in w]}
    return render

def jsonable(o):
    return json.loads(json.dumps(o, default=float))

mode = sys.argv[4]
if mode == "manifests":
    out = {n: jsonable(render_of(globals()[n]())([])["manifest"]) for n in sys.argv[5].split(",")}
    print(json.dumps(out))
else:
    from tavotto.engine import quicklayout as ql
    ql.licensed = lambda: True
    exec(sys.argv[5])  # 定义 solver(payload)
    name = sys.argv[6]
    r = render_of(globals()[name]())
    p = ql.Panel(key="k", rel_id="s.py", base=[], box=[0, 0, 1, 1], render=r)
    try:
        out = ql.plan_figure(p, page={"w": 210, "h": 297, "margin": 0}, target="single",
                             profile={"widths_mm": {"single": 80.0}, "severity": {}},
                             label_pt=8, fix_spec=False, solver=solver)
        cold = render_of(globals()[name]())(out["panels"][0]["patches"])
        hot = {e["gid"]: e.get("bbox") for e in p.manifest["elements"]}
        same = all(hot.get(e["gid"]) == e.get("bbox") for e in cold["manifest"]["elements"])
        print(json.dumps(jsonable({"ok": True, "out": out, "replay_same": same, "warnings": cold["warnings"], "manifest": p.manifest})))
    except ql.QuickLayoutError as e:
        print(json.dumps({"ok": False, "code": e.code}))
"""

#: 最小假求解器：图幅、轴位置原样（宽度换成目标宽度时按比例），标签放在每个轴文字外沿的正上方
IDENTITY_SOLVER = r"""
def solver(payload):
    w, h = payload["figure"]["size_mm"]
    tw = payload["target"]["width_mm"]
    ops = [{"op": "figure.size", "ref": "f", "w": tw, "h": h}]
    for a in payload["axes"]:
        x, y, bw, bh = a["bbox"]
        ops.append({"op": "axes.position", "ref": "f", "element_ref": a["ref"], "x": x, "y": y, "w": bw, "h": bh})
        if payload["label"]["mode"] == "canvas":
            ops.append({"op": "label.place", "ref": "f", "x": a["ink"][0] * tw, "y": a["ink"][1] * h - 4.0})
    return {"ops": ops}
"""


def _run(*args: str) -> dict:
    proc = subprocess.run(
        [WORKER_PY, "-c", DRIVER, str(ENGINE_DIR), str(SRC), FIXTURES, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def manifests() -> dict:
    if WORKER_PY is None:
        pytest.skip("no worker python")
    return _run("manifests", "sample,twin,twin_only,inset,secondary,stray_labels,roomy")


# ---------------------------------------------------------------------------
# 认轴与跟着走的元素
# ---------------------------------------------------------------------------
@needs_worker
def test_sample_structure(manifests):
    a = quicklayoutfig.analyze(manifests["sample"])
    assert len(a.mains) == 6  # 三个示意图 + 两个频率图 + 最下面那块；色条不算
    assert sum(m.equal for m in a.mains) == 3
    (cb,) = a.colorbars
    assert len(cb.hosts) == 2 and cb.vertical  # 一根色条两个宿主（manifest 的 owner_gids）
    (sy,) = a.shared
    assert sy.side == "left" and len(sy.covers) == 2  # supylabel 管中间两个频率图
    assert a.label_mode == "move" and sorted(lab.letter for lab in a.labels) == [0, 1, 2, 3]
    # (a) 管上面三个示意图（同一组）
    top = sorted((m for m in a.mains if m.equal), key=lambda m: m.plot[0])
    assert len({a.group_of[m.gid] for m in top}) == 1
    assert not a.obstacles and not a.locked


@needs_worker
def test_b0_of_the_maintainer_example_fails_for_the_right_reasons(manifests):
    a = quicklayoutfig.analyze(manifests["sample"])
    metrics = {b["metric"] for b in quicklayoutfig.check(a, gap=1.5, padding=2.0)}
    # 最下面一块左轴线不齐、上下两个频率图的刻度文字压在一起、(c) 压着上面那块、x 轴标题出了图框
    assert {"align_left", "gap", "overlap", "edge"} <= metrics


@needs_worker
def test_twin_is_not_a_second_axes(manifests):
    a = quicklayoutfig.analyze(manifests["twin_only"])
    assert len(a.mains) == 1  # 一个轴 + twinx 不进图内排版
    b = quicklayoutfig.analyze(manifests["twin"])
    assert len(b.mains) == 2 and sum(len(m.twins) for m in b.mains) == 1
    assert quicklayout.figure_main_axes(manifests["twin_only"]) == 1


@needs_worker
def test_inset_and_locked_child_axes_follow_their_host(manifests):
    a = quicklayoutfig.analyze(manifests["inset"])
    assert len(a.mains) == 2
    s = quicklayoutfig.analyze(manifests["secondary"])
    assert len(s.mains) == 2 and not s.locked
    # 子轴的落位归父轴的 locator：不当 twin（不写它的 position）
    assert all(not m.twins for m in s.mains)


@needs_worker
def test_unreadable_labels_fall_back_to_canvas_labels_and_stay_put(manifests):
    # 维护者 2026-11-26 定：认不出就照 §二 在画布上加；疑似标签原地不动，图级的当障碍物（不当共享轴标题去挪）
    a = quicklayoutfig.analyze(manifests["stray_labels"])
    assert a.label_mode == "canvas" and not a.labels
    assert not a.shared
    assert {g for g, _r in a.obstacles} == {"fig.texts_0", "fig.texts_1"}


@needs_worker
def test_solver_input_carries_no_text(manifests):
    a = quicklayoutfig.analyze(manifests["sample"])
    refs = {m.gid: f"e{i}" for i, m in enumerate(a.mains)}
    blob = json.dumps(quicklayoutfig.solver_input(a, refs))
    for word in ("Frequency", "THz", "Amp", "Time", "(a)", "axes_"):
        assert word not in blob


def test_colorbar_keeps_its_mm_offset_and_thickness():
    m = quicklayoutfig.MainAxes(gid="h", plot=[10, 10, 50, 40], ink=[5, 10, 50, 45], equal=False)
    cb = quicklayoutfig.Colorbar(
        gid="c", rect=[52, 10, 54, 40], ink=[52, 10, 60, 40], hosts=["h"], vertical=True
    )
    a = quicklayoutfig.Analysis(
        size=(80, 60),
        mains=[m],
        colorbars=[cb],
        shared=[],
        labels=[],
        obstacles=[],
        label_mode="canvas",
    )
    axes, _ = quicklayoutfig.follow(a, {"h": [20, 5, 70, 55]})
    assert axes["c"] == [72, 5, 74, 55]


def test_anchor_moves_with_the_box_not_onto_it():
    # 锚点在外框左上角右下方 (1, 2) mm：挪外框之后锚点照样在新框的 (1, 2) mm 处
    got = quicklayoutfig.anchor_for(
        [10, 10, 20, 14], (11 / 100, 12 / 50), (100, 50), [30, 5, 40, 9], (80, 40)
    )
    assert got == [round(31 / 80, 5), round(7 / 40, 5)]


# ---------------------------------------------------------------------------
# 操作闭集
# ---------------------------------------------------------------------------
MAINS = {"e1": "axes_0", "e2": "axes_1"}
GOOD = [
    {"op": "figure.size", "ref": "f", "w": 80, "h": 60},
    {
        "op": "axes.position",
        "ref": "f",
        "element_ref": "e1",
        "x": 0.1,
        "y": 0.1,
        "w": 0.3,
        "h": 0.5,
    },
    {
        "op": "axes.position",
        "ref": "f",
        "element_ref": "e2",
        "x": 0.6,
        "y": 0.1,
        "w": 0.3,
        "h": 0.5,
    },
]


def test_figure_ops_accept_a_complete_plan():
    out = quicklayout._figure_ops(GOOD, MAINS, {"l1": "fig.texts_0"})
    assert out["size"] == (80.0, 60.0) and set(out["pos"]) == {"axes_0", "axes_1"}


@pytest.mark.parametrize(
    "extra",
    [
        {
            "op": "panel.place",
            "ref": "f",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
        },  # 多图的操作不在图内闭集里
        {"op": "text.place", "ref": "f", "element_ref": "e1", "x": 1, "y": 1},  # 只许挪认出来的标签
        {
            "op": "axes.position",
            "ref": "p1",
            "element_ref": "e1",
            "x": 0,
            "y": 0,
            "w": 0.1,
            "h": 0.1,
        },
        {
            "op": "axes.position",
            "ref": "f",
            "element_ref": "e9",
            "x": 0,
            "y": 0,
            "w": 0.1,
            "h": 0.1,
        },
        {
            "op": "axes.position",
            "ref": "f",
            "element_ref": "e1",
            "x": 0.9,
            "y": 0,
            "w": 0.5,
            "h": 0.1,
        },
        {"op": "figure.size", "ref": "f", "w": float("nan"), "h": 10},
    ],
)
def test_any_bad_op_voids_the_whole_plan(extra):
    with pytest.raises(quicklayout.QuickLayoutError) as exc:
        quicklayout._figure_ops([*GOOD, extra], MAINS, {"l1": "fig.texts_0"})
    assert exc.value.code == "invalid_plan"


def test_a_plan_missing_an_axes_is_void():
    with pytest.raises(quicklayout.QuickLayoutError):
        quicklayout._figure_ops(GOOD[:2], MAINS, {})


# ---------------------------------------------------------------------------
# 旧组件 / 不可用
# ---------------------------------------------------------------------------
def test_old_component_is_told_apart_from_unavailable(monkeypatch):
    def old(cmd, payload, *, feature):
        raise prohost.ProUnavailable("unsupported", cmd)

    monkeypatch.setattr(prohost, "call", old)
    with pytest.raises(quicklayout.QuickLayoutError) as exc:
        quicklayout._ask_figure(None, {})
    assert exc.value.code == "pro_outdated"

    def gone(cmd, payload, *, feature):
        raise prohost.ProUnavailable("unsupported", feature)  # 组件不提供这个功能：不是「太旧」

    monkeypatch.setattr(prohost, "call", gone)
    with pytest.raises(quicklayout.QuickLayoutError) as exc:
        quicklayout._ask_figure(None, {})
    assert exc.value.code == "unavailable"


def test_plan_figure_refuses_without_license(monkeypatch):
    monkeypatch.setattr(quicklayout, "licensed", lambda: False)
    p = quicklayout.Panel(key="k", rel_id="s.py", base=[], box=[0, 0, 1, 1], render=lambda _p: {})
    with pytest.raises(quicklayout.QuickLayoutError) as exc:
        quicklayout.plan_figure(
            p, page={"w": 210, "h": 297}, target="single", profile={}, label_pt=8
        )
    assert exc.value.code == "not_licensed"


# ---------------------------------------------------------------------------
# 端到端（worker 解释器里整段跑事务，假求解器）
# ---------------------------------------------------------------------------
@needs_worker
def test_end_to_end_lands_existing_keys_and_replays_identically():
    got = _run("plan", IDENTITY_SOLVER, "roomy")
    assert got["ok"], got
    out = got["out"]
    assert out["mode"] == "figure" and out["grid"] is None
    assert out["notes"][0]["code"] == "in_figure"
    assert len(out["labels"]) == 2  # 图里没有标签：在画布上加
    props = {p["prop"] for p in out["panels"][0]["patches"]}
    assert props <= {"size_mm", "position"}  # 落成的全是现有键
    assert out["page"]["w"] == pytest.approx(80.0)
    assert got["replay_same"] and not got["warnings"]  # 热态 == 全量重放


@needs_worker
def test_single_axes_figure_is_too_few():
    got = _run("plan", IDENTITY_SOLVER, "twin_only")
    assert got == {"ok": False, "code": "too_few"}


@needs_worker
def test_failed_checks_void_the_plan():
    # 假求解器原样摆回去：实例图的 B0 毛病一个都没修，验收不过 → 整份作废，不回方案
    got = _run("plan", IDENTITY_SOLVER, "sample")
    assert got["ok"] is False and got["code"] in ("not_converged", "figure_obstacle")


@needs_worker
def test_unreadable_axes_labels_get_canvas_labels_without_moving_or_overlapping():
    got = _run("plan", IDENTITY_SOLVER, "roomy_stray_axes")
    assert got["ok"], got
    out = got["out"]
    assert len(out["labels"]) == 2  # 画布上加了 (a)(b)
    patches = out["panels"][0]["patches"]
    assert not any(p["prop"] == "pos_frac" for p in patches)  # 脚本画的「A」「C」一个都没挪
    # 新加的画布标签不压脚本的字（与验收同一口径：8 pt 标签框）
    w, h = out["panels"][0]["box"][2:]
    lw, lh = 8 * quicklayout.PT_MM * 0.6 * 3, 8 * quicklayout.PT_MM * 1.2
    texts = [e for e in got["manifest"]["elements"] if e["gid"].endswith(".texts_0")]
    assert len(texts) == 2
    for lab in out["labels"]:
        r = [lab["x"], lab["y"], lab["x"] + lw, lab["y"] + lh]
        for e in texts:
            x, y, bw, bh = e["bbox"]
            t = [x * w, y * h, (x + bw) * w, (y + bh) * h]
            assert not (r[0] < t[2] and t[0] < r[2] and r[1] < t[3] and t[1] < r[3])


@needs_worker
def test_canvas_label_landing_on_a_suspected_label_voids_the_plan():
    got = _run("plan", IDENTITY_SOLVER, "roomy_stray_fig")
    assert got == {"ok": False, "code": "figure_obstacle"}


# ---------------------------------------------------------------------------
# 障碍物：发给求解器、让它绕开（维护者 2026-11-26 定）
# ---------------------------------------------------------------------------
def _static_panel(manifest: dict) -> quicklayout.Panel:
    return quicklayout.Panel(
        key="k",
        rel_id="s.py",
        base=[],
        box=[0, 0, 1, 1],
        render=lambda _p: {"manifest": manifest, "warnings": []},
    )


@needs_worker
def test_obstacles_go_to_the_solver_as_boxes_only(manifests, monkeypatch):
    monkeypatch.setattr(quicklayout, "licensed", lambda: True)
    seen: list[dict] = []

    def solver(payload):
        seen.append(payload)
        return {"ops": [], "error": "page_too_small"}

    with pytest.raises(quicklayout.QuickLayoutError):
        quicklayout.plan_figure(
            _static_panel(manifests["stray_labels"]),
            page={"w": 210, "h": 297},
            target="single",
            profile={"widths_mm": {"single": 80.0}},
            label_pt=8,
            fix_spec=False,
            solver=solver,
        )
    obs = seen[0]["obstacles"]
    assert len(obs) == 2 and all(set(o) == {"ref", "bbox"} and len(o["bbox"]) == 4 for o in obs)
    assert "(a)" not in json.dumps(seen[0]) and "fig.texts" not in json.dumps(seen[0])


@needs_worker
def test_solver_saying_it_cannot_fit_is_figure_obstacle(manifests, monkeypatch):
    monkeypatch.setattr(quicklayout, "licensed", lambda: True)
    with pytest.raises(quicklayout.QuickLayoutError) as exc:
        quicklayout.plan_figure(
            _static_panel(manifests["stray_labels"]),
            page={"w": 210, "h": 297},
            target="single",
            profile={"widths_mm": {"single": 80.0}},
            label_pt=8,
            fix_spec=False,
            solver=lambda _p: {"ops": [], "error": "figure_obstacle"},
        )
    assert exc.value.code == "figure_obstacle"


@needs_worker
def test_varied_label_offsets_relax_only_that_metric(manifests):
    a = quicklayoutfig.analyze(manifests["sample"])
    strict = {b["metric"] for b in quicklayoutfig.check(a, gap=1.5, padding=2.0)}
    loose = {
        b["metric"] for b in quicklayoutfig.check(a, gap=1.5, padding=2.0, uniform_labels=False)
    }
    assert "label_offset" in strict and strict - loose == {"label_offset"}


def test_obstacles_are_not_held_to_the_page_margin_but_still_block():
    m = quicklayoutfig.MainAxes(gid="h", plot=[10, 10, 50, 40], ink=[5, 5, 55, 45], equal=False)
    a = quicklayoutfig.Analysis(
        size=(80, 60),
        mains=[m],
        colorbars=[],
        shared=[],
        labels=[],
        obstacles=[("fig.texts_0", [0.5, 0.5, 3, 3])],
        label_mode="canvas",
    )
    metrics = {b["metric"] for b in quicklayoutfig.check(a, gap=1.5, padding=2.0)}
    assert "edge" not in metrics  # 脚本原样贴边的字不归我们管
    hit = quicklayoutfig.check(a, gap=1.5, padding=2.0, canvas_labels=[[1, 1, 4, 4]])
    assert any(b["metric"] == "obstacle" for b in hit)  # 新加的标签压上去照样作废
