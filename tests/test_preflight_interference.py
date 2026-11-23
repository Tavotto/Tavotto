"""V-011：干涉检测（`text-overlap` / `text-over-axes` / `legend-over-data`）接进常规预检。

判据只有 `engine/interference.detect` 一份；`preflight.run` 把它逐对的结果折进聚合清单（画布 / 导出对话框的
TS 求值器是同一套算法的逐行移植，`web/src/lib/interference.ts`）。这里看四件事：

1. 两侧对拍：`tests/golden/interference_vectors.json`（逐对）与 `preflight_vectors.json` 的 `interference-*`（聚合）
   都由生成器按 Python 侧重出、逐字比对；vitest 跑同一份。
2. 登记：三张 severity 表沿用原来的档位（都是 warn）；预检源码里三条都是字面量 `sink.add`。
3. 不重复报：已经逐对调 `detect` 的调用方（保留式规范化 / 按规范修图的事务、改图验收、MCP 桥）不会再收到聚合项。
4. 真 worker：FigureBench 夹具与边界写法（长标签、旋转、`loc="best"`、inset、色条、隐藏图例）上报什么、不报什么。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import figureverify, interference, pool, preflight, profiles, specfix

ROOT = Path(__file__).resolve().parent.parent
FIGUREBENCH = ROOT / "tests" / "fixtures" / "figurebench"
IDS = set(interference.CHECK_IDS)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ------------------------------ 登记与对拍（不需要 worker） ------------------------------
def test_severity_registration_is_unchanged_warn_in_all_three_tables():
    """沿用已有登记、不改档位；free-form 没写这三条，兜底就是 warn（漏登记 = 兜底，所以直接读表）。"""
    raw = json.loads((ROOT / "src/tavotto/profiles/publication.json").read_text(encoding="utf-8"))
    tables = {pid: p.get("severity") or {} for pid, p in raw["profiles"].items()}
    for cid in IDS:
        assert tables["lab-publication-v1"].get(cid) == "warn", cid
        assert tables["journal-base-v1"].get(cid) == "warn", cid
        assert cid not in tables["free-form-v1"], cid
        for pid in ("lab-publication-v1", "journal-base-v1", "free-form-v1"):
            assert profiles.severity_of(profiles.load(pid), cid) == "warn", (pid, cid)


@pytest.mark.parametrize("script", ["gen_interference_vectors.py", "gen_preflight_vectors.py"])
def test_golden_vectors_match_this_implementation(script):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_vectors_are_asserted_on_the_typescript_side_and_cover_all_three_checks():
    ts = (ROOT / "web/src/lib/interference.test.ts").read_text(encoding="utf-8")
    assert "tests/golden/interference_vectors.json" in ts
    data = json.loads((ROOT / "tests/golden/interference_vectors.json").read_text(encoding="utf-8"))
    pair_ids = {i["id"] for c in data["cases"] for i in c["expected"]}
    assert pair_ids == IDS
    risk = [i for c in data["cases"] for i in c["expected"] if i["id"] == "legend-over-data"]
    assert {i["detail"]["certain"] for i in risk} == {True, False}
    agg = json.loads((ROOT / "tests/golden/preflight_vectors.json").read_text(encoding="utf-8"))
    folded = {
        i["id"]
        for c in agg["cases"]
        if c["name"].startswith("interference-")
        for i in c["expected"]
    }
    assert IDS <= folded


def test_preflight_source_registers_the_three_ids_as_literals():
    """`test_preflight._all_check_ids` 只认字面量 `sink.add("…"`：变量会让这三条漏出登记看护。"""
    import re

    src = (ROOT / "src/tavotto/engine/preflight.py").read_text(encoding="utf-8")
    assert IDS <= set(re.findall(r'sink\.add\(\s*"([a-z0-9-]+)"', src))


# ------------------------------ 聚合形状与「不重复报」 ------------------------------
def _crowded_manifest() -> dict:
    """标题与标注叠在一起；图例压住一条真画出来的折线（figure 分数、y 向下）。"""

    def el(gid, role, bbox, **extra):
        return {"gid": gid, "role": role, "label": gid, "bbox": bbox, "editable": [], **extra}

    return {
        "stem": "Fig1",
        "size_mm": [80.0, 60.0],
        "elements": [
            el("axes_0", "axes", [0.125, 0.125, 0.75, 0.75]),
            el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            el("axes_0.texts_0", "text", [0.34375, 0.25, 0.125, 0.0625]),
            el("axes_0.legend", "legend", [0.5, 0.5, 0.25, 0.25]),
            el(
                "axes_0.lines_0",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry={
                    "kind": "polyline",
                    "paths": [{"points": [[0.125, 0.875], [0.875, 0.125]], "closed": False}],
                },
            ),
        ],
    }


def test_run_folds_interference_and_the_opt_out_drops_exactly_those_ids():
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_crowded_manifest())
    with_ = preflight.run(spec, p)
    without = preflight.run(spec, p, with_interference=False)
    got = {i["id"]: i for i in with_ if i["id"] in IDS}
    assert set(got) == {"text-overlap", "legend-over-data"}
    assert got["text-overlap"]["gids"] == ["axes_0.texts_0", "axes_0.title"]
    assert got["legend-over-data"]["gids"] == ["axes_0.legend"]
    assert got["legend-over-data"]["detail"]["certain"] is True
    assert got["legend-over-data"]["message"]["key"] == "interferenceLegendOverData"
    # 文案参数里没有 gid：问题行的主语已经是那个元素
    for issue in got.values():
        assert not any("axes_" in str(v) for v in issue["message"]["params"].values())
    assert [i for i in with_ if i["id"] not in IDS] == without
    assert not IDS & {i["id"] for i in without}


def test_specfix_and_figureverify_see_each_collision_once():
    """事务与改图验收按 `(规则, gid)` 配对前后两份清单：逐对来自 `detect`，预检不再折一份聚合项进来。"""
    m = _crowded_manifest()
    p = profiles.load("lab-publication-v1")
    assert not IDS & {i["id"] for i in specfix.profile_issues(m, p, 1.0)}
    keys = figureverify._issue_keys(m, p)
    pairs = {k for k in keys if k[0] in IDS}
    assert pairs == {(i["id"], tuple(sorted(i["gids"]))) for i in interference.detect(m, p)}


def test_mcp_bridge_keeps_its_per_pair_list_and_drops_the_folded_items():
    sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))
    from tavotto_mcp import bridge

    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(_crowded_manifest())
    kept = bridge._without_interference(preflight.run(spec, p))
    assert not IDS & {i["id"] for i in kept}
    assert kept == preflight.run(spec, p, with_interference=False)
    # 旧引擎的 run() 没有 with_interference 参数：桥按 id 过滤，不传这个参数
    import ast

    tree = ast.parse((ROOT / "codex-plugin/mcp/tavotto_mcp/bridge.py").read_text(encoding="utf-8"))
    passed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and any(k.arg == "with_interference" for k in node.keywords)
    ]
    assert passed == []


# ------------------------------ 真 worker ------------------------------
def _render_fixture(tmp: Path, name: str) -> dict:
    dst = tmp / name
    shutil.copytree(FIGUREBENCH / name, dst)
    w = pool.one_shot("figure.py", str(dst), "main")
    try:
        w.ensure_built()
        return w.override(name, [])["manifest"]
    finally:
        pool.discard(w)


def _interference(manifest: dict, profile_id: str = "lab-publication-v1") -> list[dict]:
    spec = preflight.spec_from_manifest(manifest, panel_id="p1")
    return [i for i in preflight.run(spec, profiles.load(profile_id)) if i["id"] in IDS]


@needs_worker
@pytest.mark.parametrize(
    "name", ["line_two_series", "cjk_labels", "log_decay", "heatmap_colorbar", "grouped_bars"]
)
def test_figurebench_fixtures_without_collisions_report_nothing(tmp_path, name):
    man = _render_fixture(tmp_path, name)
    for pid in ("lab-publication-v1", "journal-base-v1", "free-form-v1"):
        assert _interference(man, pid) == [], (name, pid)


@needs_worker
def test_figurebench_multi_panel_reports_the_real_collision_and_nothing_else(tmp_path):
    """`multi_panel` 不是干净的：没有 tight_layout，右列的 y 轴标题与三个刻度标签画进了左列子图的
    框里（压在 axes_0 的右脊线上，渲染出来肉眼可见）。钉住恰好这 4 段文字，没有别的。"""
    got = _interference(_render_fixture(tmp_path, "multi_panel"))
    assert [i["id"] for i in got] == ["text-over-axes"]
    assert got[0]["gids"] == [
        "axes_1.ylabel",
        "axes_1.yticklabels_1",
        "axes_1.yticklabels_2",
        "axes_1.yticklabels_3",
    ]
    assert got[0]["detail"]["against"] == "axes_0"
    assert got[0]["message"]["key"] == "interferenceTextOverAxes"


@needs_worker
def test_figurebench_errorbar_fit_is_only_a_risk(tmp_path):
    """误差棒容器只按包围盒判（`BBOX_BASIS_ROLES`），它的框盖住整个数据范围：`loc="best"` 的图例
    落在空白处也只能说「可能」。钉住现状（待维护者裁决的噪音来源，见交接报告）。"""
    got = _interference(_render_fixture(tmp_path, "errorbar_fit"))
    assert len(got) == 1
    assert got[0]["id"] == "legend-over-data"
    assert got[0]["detail"]["certain"] is False
    assert got[0]["message"]["key"] == "interferenceLegendOverDataRisk"


EDGE_SCRIPT = """\
import matplotlib.pyplot as plt
import numpy as np

LONG = ["Control group", "Low dose treatment", "High dose treatment",
        "Recovery week 4", "Recovery week 8", "Sham operated"]


def long_flat():
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.bar(range(6), [3, 4, 5, 4, 3, 2])
    ax.set_xticks(range(6), LONG)
    fig.savefig("LongFlat.pdf")


def long_rot90():
    fig, ax = plt.subplots(figsize=(3.2, 3.2))
    ax.bar(range(6), [3, 4, 5, 4, 3, 2])
    ax.set_xticks(range(6), LONG, rotation=90)
    fig.tight_layout()
    fig.savefig("LongRot90.pdf")


def legend_best():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    x = np.linspace(0, 10, 50)
    ax.plot(x, np.sin(x) + 2, label="sin")
    ax.plot(x, 0.2 * x, label="ramp")
    ax.legend(loc="best")
    fig.savefig("LegendBest.pdf")


def legend_center():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.plot([0, 1], [0, 1], label="diag")
    ax.plot([0, 1], [1, 0], label="anti")
    ax.legend(loc="center")
    fig.savefig("LegendCenter.pdf")


def inset():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.plot([0, 1, 2], [0, 1, 4])
    ax.set_title("main")
    ins = ax.inset_axes([0.55, 0.15, 0.4, 0.35])
    ins.plot([0, 1], [1, 0])
    ins.set_title("zoom", fontsize=7)
    fig.savefig("Inset.pdf")


def colorbar():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    im = ax.imshow(np.arange(20).reshape(4, 5))
    fig.colorbar(im, ax=ax, label="Intensity (a.u.)")
    fig.savefig("Colorbar.pdf")


def legend_over_label():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.plot([0, 1], [0, 1], label="a rather long legend entry")
    ax.set_xlabel("Concentration (mM)")
    ax.legend(loc="upper left", bbox_to_anchor=(0.2, 0.02))
    fig.savefig("LegendOverLabel.pdf")
"""


def _render_edge(tmp: Path, func: str, stem: str, patches=()) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "edge.py").write_text(EDGE_SCRIPT, encoding="utf-8")
    w = pool.one_shot("edge.py", str(tmp), func)
    try:
        w.ensure_built()
        return w.override(stem, list(patches))["manifest"]
    finally:
        pool.discard(w)


@needs_worker
def test_long_horizontal_tick_labels_overlap_and_rotating_them_90_clears_it(tmp_path):
    flat = _interference(_render_edge(tmp_path / "a", "long_flat", "LongFlat"))
    assert [i["id"] for i in flat] == ["text-overlap"]
    assert all(g.startswith("axes_0.xticklabels_") for g in flat[0]["gids"])
    assert len(flat[0]["gids"]) == 6
    assert _interference(_render_edge(tmp_path / "b", "long_rot90", "LongRot90")) == []


@needs_worker
@pytest.mark.parametrize(
    ("func", "stem"),
    [("legend_best", "LegendBest"), ("inset", "Inset"), ("colorbar", "Colorbar")],
)
def test_boundary_layouts_without_collisions_report_nothing(tmp_path, func, stem):
    """`loc="best"` 躲开了曲线；inset 在父图里是包含不是干涉；色条与它的标题不撞。"""
    assert _interference(_render_edge(tmp_path, func, stem)) == []


@needs_worker
def test_legend_placed_on_the_lines_is_certain(tmp_path):
    got = _interference(_render_edge(tmp_path, "legend_center", "LegendCenter"))
    assert [i["id"] for i in got] == ["legend-over-data"]
    assert got[0]["gids"] == ["axes_0.legend"]
    assert got[0]["detail"]["certain"] is True
    assert got[0]["detail"]["targets"] == ["axes_0.lines_0", "axes_0.lines_1"]


@needs_worker
def test_hiding_the_legend_clears_the_overlap_its_entries_caused(tmp_path):
    """图例整体隐藏后条目自己仍报 visible=True：以前它们照样跟轴标题「重叠」；现在不算。"""
    shown = _render_edge(tmp_path / "a", "legend_over_label", "LegendOverLabel")
    hits = _interference(shown)
    assert "text-overlap" in {i["id"] for i in hits}, hits
    hidden = _render_edge(
        tmp_path / "b",
        "legend_over_label",
        "LegendOverLabel",
        [{"gid": "axes_0.legend", "prop": "visible", "value": False}],
    )
    assert _interference(hidden) == []
