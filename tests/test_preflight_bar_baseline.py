"""V-002「线性轴上柱的基线被截掉」（`bar-baseline-truncated`）：真 worker 的事实 + 两侧同一条规则。

手写的 golden 向量（`tests/golden/preflight_vectors.json` 的 `bar-baseline-*`）只证明两个求值器对同一份
`bar_baseline` + `[xy]lim` / `[xy]scale` 结论一致；这里证明**事实本身**是从真实 matplotlib 量出来的：规格
（`docs/sciviz/validation-spec.md` V-002）里的每个写法都在真 worker 里渲染一次，读 manifest，再交给 `preflight.run`。
每个「不报」的边界都配了一个真截掉的范围——去掉对应的那道守卫它就会报，用例才有鉴别力。

`tests/golden/bar_baseline_real.json` 是真 worker 产出的 axes 元素（`bar_baseline` 与那一维的 lim / scale 字段）
的快照（这里重新渲染并逐字比对），`web/src/lib/preflight.barBaseline.test.ts` 直接消费它（错题本 L-042）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool, preflight, profiles

ROOT = Path(__file__).resolve().parent.parent
RULE = "bar-baseline-truncated"
REAL_SNAPSHOT = ROOT / "tests" / "golden" / "bar_baseline_real.json"
FIGUREBENCH = ROOT / "tests" / "fixtures" / "figurebench"
PROFILES = ("lab-publication-v1", "journal-base-v1", "free-form-v1")

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ------------------------------ 规则登记（不需要 worker） ------------------------------
def test_rule_is_registered_as_warn_in_all_three_severity_tables():
    """没登记的检查项兜底就是 warn，所以「结果是 warn」测不出漏登记：直接读表。"""
    raw = json.loads((ROOT / "src/tavotto/profiles/publication.json").read_text(encoding="utf-8"))
    tables = {pid: p.get("severity") or {} for pid, p in raw["profiles"].items()}
    for pid in PROFILES:
        assert tables[pid].get(RULE) == "warn", pid
        assert profiles.severity_of(profiles.load(pid), RULE) == "warn", pid


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (4.0, "4"),
        (8.65, "8.65"),
        (123456.7, "123457"),
        (1e7, "10000000"),
        (1e-7, "0.0000001"),
        (0.1 + 0.2, "0.3"),
        (1000.5, "1001"),  # 正好一半：远离 0（JS toFixed 的规则），不是取偶
        (-1000.5, "-1001"),
        (2.5e-13, "0.00000000000025"),
    ],
)
def test_axis_number_format(value, text):
    """`_axis_num` ↔ `axisNum` 同源：这里钉 Python 侧，golden `bar-baseline-number-format-*` 让 TS 侧逐字对上。"""
    assert preflight._axis_num(value) == text  # noqa: SLF001


# ------------------------------ 真 worker ------------------------------
CASES_SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, axs = plt.subplots(5, 5, figsize=(14, 11), dpi=50)
    a = iter(axs.ravel())

    def nxt(name):
        ax = next(a)
        ax.set_title(name)
        return ax

    ax = nxt("normal"); ax.bar([0, 1], [5, 8])
    ax = nxt("cut"); ax.bar([0, 1], [5, 8]); ax.set_ylim(4, 9)
    ax = nxt("barh_cut"); ax.barh([0, 1], [5, 8]); ax.set_xlim(4, 9)
    ax = nxt("barh_normal"); ax.barh([0, 1], [5, 8])
    # 浮动柱 / 甘特：底各不相同，没有共同基线——范围照样截在柱底之上
    ax = nxt("floating"); ax.bar([0, 1, 2], [2, 3, 1], bottom=[4, 5, 6]); ax.set_ylim(4.5, 10)
    ax = nxt("gantt"); ax.barh([0, 1, 2], [2, 3, 1], left=[1, 4, 2]); ax.set_xlim(1.5, 8)
    # 堆叠：下层高都是 3，上层的底恰好统一成 3——它是下层的顶，不是基线；范围停在 2.5 只是放大，不报
    ax = nxt("stacked_upper"); ax.bar([0, 1], [3, 3]); ax.bar([0, 1], [2, 2], bottom=3); ax.set_ylim(0, 2.5)
    ax = nxt("stacked_cut"); ax.bar([0, 1], [2, 3]); ax.bar([0, 1], [1, 1], bottom=[2, 3]); ax.set_ylim(1, 7)
    ax = nxt("diff"); ax.bar([0, 1, 2], [-3, 2, 5])
    ax = nxt("diff_cut"); ax.bar([0, 1], [-3, 5]); ax.set_ylim(1, 6)
    # 负值柱的底是锚点 0，不是包围盒下沿；取错成 -3 / -5 就成了「浮动柱」不报
    ax = nxt("negative_cut"); ax.bar([0, 1], [-3, -5]); ax.set_ylim(-6, -1)
    ax = nxt("inverted_cut"); ax.bar([0, 1], [5, 8]); ax.set_ylim(9, 4)
    ax = nxt("inverted_normal"); ax.bar([0, 1], [5, 8]); ax.invert_yaxis()
    ax = nxt("log"); ax.bar([0, 1], [5, 50]); ax.set_yscale("log")
    ax = nxt("symlog_cut"); ax.bar([0, 1], [5, 50]); ax.set_yscale("symlog"); ax.set_ylim(2, 60)
    ax = nxt("hidden")
    bars = ax.bar([0, 1], [5, 8])
    for p in bars:
        p.set_visible(False)
    ax.plot([0, 1], [5, 8]); ax.set_ylim(4, 9)
    ax = nxt("twin_left"); ax.bar([0, 1], [5, 8])
    tw = ax.twinx(); tw.set_title("twin_right"); tw.bar([0.4, 1.4], [50, 80], width=0.3); tw.set_ylim(40, 90)
    ax = nxt("bottom100"); ax.bar([0, 1], [20, -10], bottom=100)
    ax = nxt("bottom100_cut"); ax.bar([0, 1], [20, 30], bottom=100); ax.set_ylim(105, 135)
    ax = nxt("hist"); ax.hist([1, 2, 2, 3, 3, 3], bins=3)
    ax = nxt("hist_cut"); ax.hist([1, 2, 2, 3, 3, 3], bins=3); ax.set_ylim(0.5, 3.5)
    ax = nxt("hist_horizontal_cut")
    ax.hist([1, 2, 2, 3, 3, 3], bins=3, orientation="horizontal"); ax.set_xlim(0.5, 3.5)
    ax = nxt("line_only"); ax.plot([0, 1], [5, 8]); ax.set_ylim(4, 9)
    fig.savefig("BarCases.pdf")
"""

#: 标题 → 期望的 `bar_baseline`（None = 键缺席）。值是 `{维: [(基线, 柱数), ...]}`
EXPECTED_FACTS = {
    "normal": {"y": [(0.0, 2)]},
    "cut": {"y": [(0.0, 2)]},
    "barh_cut": {"x": [(0.0, 2)]},
    "barh_normal": {"x": [(0.0, 2)]},
    "floating": {"y": []},
    "gantt": {"x": []},
    "stacked_upper": {"y": [(0.0, 2)]},
    "stacked_cut": {"y": [(0.0, 2)]},
    "diff": {"y": [(0.0, 3)]},
    "diff_cut": {"y": [(0.0, 2)]},
    "negative_cut": {"y": [(0.0, 2)]},
    "inverted_cut": {"y": [(0.0, 2)]},
    "inverted_normal": {"y": [(0.0, 2)]},
    "log": {"y": [(0.0, 2)]},  # 事实不看尺度；log 不判由规则读 yscale 决定
    "symlog_cut": {"y": [(0.0, 2)]},
    "hidden": None,  # 容器里没有可见的柱
    "twin_left": {"y": [(0.0, 2)]},
    "twin_right": {"y": [(0.0, 2)]},
    "bottom100": {"y": [(100.0, 2)]},
    "bottom100_cut": {"y": [(100.0, 2)]},
    "hist": {"y": [(0.0, 3)]},
    "hist_cut": {"y": [(0.0, 3)]},
    "hist_horizontal_cut": {"x": [(0.0, 3)]},
    "line_only": None,
}

#: 该报的轴 → 期望的 detail（基线、截在哪一端）
EXPECTED_HITS = {
    "cut": ("y", 0.0, 2),
    "barh_cut": ("x", 0.0, 2),
    "stacked_cut": ("y", 0.0, 2),
    "diff_cut": ("y", 0.0, 2),
    "negative_cut": ("y", 0.0, 2),
    "inverted_cut": ("y", 0.0, 2),
    "twin_right": ("y", 0.0, 2),
    "bottom100_cut": ("y", 100.0, 2),
    "hist_cut": ("y", 0.0, 3),
    "hist_horizontal_cut": ("x", 0.0, 3),
}


def _render(tmp: Path, script_name: str, source: str, stem: str, func: str, patches=()):
    (tmp / script_name).write_text(source, encoding="utf-8")
    w = pool.one_shot(script_name, str(tmp), func)
    try:
        w.ensure_built()
        return w, w.override(stem, list(patches))["manifest"]
    except BaseException:
        pool.discard(w)
        raise


def _spec(manifest):
    w, h = manifest.get("size_mm") or (80.0, 60.0)
    rect = [0, 0, float(w), float(h)]
    panel = {
        "id": "p1",
        "name": "p1",
        "kind": "pdf",
        "rect_mm": rect,
        "scale": 1.0,
        "manifest": manifest,
        "px_w": None,
        "missing": False,
        "stale": False,
        "render_error": None,
        "unapplied_overrides": 0,
        "hidden": False,
    }
    return {
        "page": {"w_mm": rect[2], "h_mm": rect[3], "margin_mm": 0.0},
        "panels": [panel],
        "texts": [],
        "objects": [{"id": "p1", "type": "panel", "rect_mm": rect, "hidden": False}],
    }


def _hits(manifest, profile_id="lab-publication-v1"):
    return [i for i in preflight.run(_spec(manifest), profiles.load(profile_id)) if i["id"] == RULE]


def _field(el: dict, prop: str):
    return next((f.get("value") for f in el["editable"] if f.get("prop") == prop), None)


def _axes_by_title(manifest) -> dict[str, dict]:
    els = {e["gid"]: e for e in manifest["elements"]}
    out = {}
    for e in manifest["elements"]:
        if e["role"] != "title":
            continue
        text = _field(e, "text")
        host = els.get(e["gid"].split(".", 1)[0])
        if text and host is not None and host["role"] == "axes":
            out[text] = host
    return out


@pytest.fixture(scope="module")
def cases_manifest(tmp_path_factory):
    w, man = _render(
        tmp_path_factory.mktemp("barbase-cases"),
        "fig_barcases.py",
        CASES_SCRIPT,
        "BarCases",
        "main",
    )
    pool.discard(w)
    return man


@needs_worker
def test_bar_baseline_facts_match_the_table_on_a_real_manifest(cases_manifest):
    axes = _axes_by_title(cases_manifest)
    assert set(axes) == set(EXPECTED_FACTS), sorted(set(axes) ^ set(EXPECTED_FACTS))
    for title, want in EXPECTED_FACTS.items():
        got = axes[title].get("bar_baseline")
        if want is None:
            assert got is None, (title, got)
            continue
        assert got == {
            dim: {"status": "ok", "baselines": [{"value": v, "bars": n} for v, n in entries]}
            for dim, entries in want.items()
        }, (title, got)


@needs_worker
def test_only_the_truncated_axes_are_reported_on_a_real_manifest(cases_manifest):
    axes = _axes_by_title(cases_manifest)
    for pid in PROFILES:
        hits = _hits(cases_manifest, pid)
        assert len(hits) == 1, pid
        assert hits[0]["severity"] == "warn", pid
        assert sorted(hits[0]["gids"]) == sorted(axes[t]["gid"] for t in EXPECTED_HITS), pid
    # 每根该报的轴单独判一次：detail 的基线、柱数、截在哪一端都要对
    for title, (dim, base, bars) in EXPECTED_HITS.items():
        el = axes[title]
        man = {**cases_manifest, "elements": [el]}
        (hit,) = _hits(man)
        lo, hi = sorted(_field(el, f"{dim}lim"))
        assert hit["detail"] == {
            "axis": dim,
            "baseline": base,
            "lim_low": lo,
            "lim_high": hi,
            "bars": bars,
        }, title
        assert hit["message"]["key"] == f"barBaselineTruncated{dim.upper()}", title


@needs_worker
def test_real_snapshot_for_the_frontend_is_what_the_worker_emits(cases_manifest):
    """`bar_baseline_real.json` 不是手写的：这里重新渲染，逐字等于真 worker 的输出才算数。

    改了 `CASES_SCRIPT` 或事实口径后重出：`PYTHONPATH=$PWD/src python tests/test_preflight_bar_baseline.py --write`，
    不许手改那份 JSON。"""
    assert json.loads(REAL_SNAPSHOT.read_text(encoding="utf-8")) == _snapshot(cases_manifest)


_SNAPSHOT_PROPS = ("visible", "xlim", "ylim", "xscale", "yscale")


def _snapshot(cases_manifest) -> dict:
    axes = _axes_by_title(cases_manifest)
    return {
        "comment": "真 worker 渲染 tests/test_preflight_bar_baseline.py 的 CASES_SCRIPT 得到的 axes 元素"
        "（只留 gid / role / bar_baseline 与 visible / [xy]lim / [xy]scale 这几个 editable 字段）。"
        "pytest 重渲染逐字比对；web/src/lib/preflight.barBaseline.test.ts 消费它。",
        "elements": [
            {
                "title": t,
                "gid": axes[t]["gid"],
                "role": "axes",
                "editable": [f for f in axes[t]["editable"] if f.get("prop") in _SNAPSHOT_PROPS],
                "bar_baseline": axes[t]["bar_baseline"],
            }
            for t in sorted(axes)
            if "bar_baseline" in axes[t]
        ],
        "expected_gids": sorted(hit for i in _hits(cases_manifest) for hit in i["gids"]),
    }


def _probe(tmp_path: Path, body: str):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src' / 'tavotto' / 'engine')!r})\n"
        "import matplotlib; matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import manifest as M\n" + body,
        encoding="utf-8",
    )
    proc = subprocess.run(
        [WORKER_PY, str(probe)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_worker
def test_over_budget_and_unknown_orientation_are_not_judged(tmp_path):
    """超出配额写 `over_budget`（不扣）、说不出方向写 `unavailable`，都不带基线，规则不判。"""
    out = _probe(
        tmp_path,
        "from matplotlib.container import BarContainer\n"
        "fig, (a, b) = plt.subplots(1, 2)\n"
        "a.bar([0, 1, 2], [5, 6, 7]); a.set_ylim(4, 9)\n"
        "rects = b.bar([0, 1], [5, 6]).patches\n"
        "b.containers.append(BarContainer(rects))  # 手工构造，没有 orientation\n"
        "fig.canvas.draw()\n"
        "budget = [2]\n"
        "small = M.bar_baseline(a, budget)\n"
        "full = M.bar_baseline(a, [M.FINGERPRINT_BUDGET])\n"
        "unknown = M.bar_baseline(b, [M.FINGERPRINT_BUDGET])\n"
        "print(json.dumps({'small': small, 'left': budget[0], 'full': full, 'unknown': unknown,"
        " 'statuses': list(M.BAR_BASELINE_STATUSES)}))\n",
    )
    assert out["small"] == {"y": {"status": "over_budget"}}
    assert out["left"] == 2  # 没量就不扣
    assert out["full"] == {"y": {"status": "ok", "baselines": [{"value": 0.0, "bars": 3}]}}
    assert out["unknown"] == {"x": {"status": "unavailable"}, "y": {"status": "unavailable"}}
    # status 闭集与 TS 侧 `BarBaselineFact.status` 的字面值一致（同源对，见 same-origin-pairs.md）
    assert out["statuses"] == ["ok", "over_budget", "unavailable"]
    ts = (ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    start = ts.index("export interface BarBaselineFact")
    assert "status: 'ok' | 'over_budget' | 'unavailable'" in ts[start : ts.index("}", start)]
    fields = [
        {"prop": "ylim", "type": "pair", "value": [4.0, 9.0]},
        {"prop": "yscale", "type": "enum", "value": "linear"},
    ]
    for facts in (out["small"], out["unknown"]):
        man = {
            "stem": "x",
            "size_mm": [80.0, 60.0],
            "elements": [
                {
                    "gid": "axes_0",
                    "role": "axes",
                    "label": "axes_0",
                    "draggable": False,
                    "bbox": [0.1, 0.1, 0.8, 0.8],
                    "editable": fields,
                    "bar_baseline": facts,
                }
            ],
        }
        assert _hits(man) == []


@needs_worker
def test_polar_and_3d_bars_emit_no_fact(tmp_path):
    """极坐标的玫瑰图、3D 的 `bar` 也是 `BarContainer`，但「柱底」不是同一个概念：只判直角坐标轴。"""
    out = _probe(
        tmp_path,
        "fig = plt.figure()\n"
        "p = fig.add_subplot(1, 3, 1, projection='polar'); p.bar([0, 1], [5, 8]); p.set_ylim(4, 9)\n"
        "d = fig.add_subplot(1, 3, 2, projection='3d'); d.bar([0, 1], [5, 8], zs=0, zdir='y')\n"
        "r = fig.add_subplot(1, 3, 3); r.bar([0, 1], [5, 8])\n"
        "fig.canvas.draw()\n"
        "print(json.dumps([M.bar_baseline(ax, [M.FINGERPRINT_BUDGET]) for ax in (p, d, r)]))\n",
    )
    assert out[0] is None and out[1] is None
    assert out[2] == {"y": {"status": "ok", "baselines": [{"value": 0.0, "bars": 2}]}}


EDIT_SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.bar([0, 1], [5, 8])
    fig.savefig("BarEdit.pdf")
"""


@needs_worker
def test_cutting_the_base_in_the_inspector_brings_the_issue_and_undo_clears_it(tmp_path):
    """编辑引入的问题：override `ylim` 截掉柱底后报，事实本身不变（只有基线）；撤销（空 override 列表）后消失。"""
    w, before = _render(tmp_path, "fig_baredit.py", EDIT_SCRIPT, "BarEdit", "main")
    try:
        fact = {"y": {"status": "ok", "baselines": [{"value": 0.0, "bars": 2}]}}
        ax0 = next(e for e in before["elements"] if e["gid"] == "axes_0")
        assert ax0["bar_baseline"] == fact
        assert _hits(before) == []
        cut = w.override("BarEdit", [{"gid": "axes_0", "prop": "ylim", "value": [4.0, 9.0]}])[
            "manifest"
        ]
        ax1 = next(e for e in cut["elements"] if e["gid"] == "axes_0")
        assert ax1["bar_baseline"] == fact
        hits = _hits(cut)
        assert len(hits) == 1 and hits[0]["gids"] == ["axes_0"]
        assert hits[0]["detail"] == {
            "axis": "y",
            "baseline": 0.0,
            "lim_low": 4.0,
            "lim_high": 9.0,
            "bars": 2,
        }
        assert hits[0]["message"] == {
            "key": "barBaselineTruncatedY",
            "params": {"baseline": "0", "low": "4", "high": "9", "edge": "4"},
        }
        undone = w.override("BarEdit", [])["manifest"]
        assert _hits(undone) == []
    finally:
        pool.discard(w)


def _figurebench(tmp: Path, name: str):
    """夹具在临时副本里执行（FigureBench 纪律：本目录逐字节不变）。"""
    dst = tmp / name
    shutil.copytree(FIGUREBENCH / name, dst)
    w = pool.one_shot("figure.py", str(dst), "main")
    try:
        w.ensure_built()
    except BaseException:
        pool.discard(w)
        raise
    return w


@needs_worker
def test_figurebench_grouped_bars_is_not_flagged_until_the_base_is_cut(tmp_path):
    """真图防误报：两组并列柱从 0 起，三张规范表都不报；把 ylim 截到 2 报，撤销后消失。"""
    w = _figurebench(tmp_path, "grouped_bars")
    try:
        man = w.override("grouped_bars", [])["manifest"]
        ax = next(e for e in man["elements"] if e["gid"] == "axes_0")
        assert ax["bar_baseline"] == {
            "y": {"status": "ok", "baselines": [{"value": 0.0, "bars": 8}]}
        }
        for pid in PROFILES:
            assert _hits(man, pid) == [], pid
        cut = w.override("grouped_bars", [{"gid": "axes_0", "prop": "ylim", "value": [2.0, 6.0]}])
        assert [h["gids"] for h in _hits(cut["manifest"])] == [["axes_0"]]
        assert _hits(w.override("grouped_bars", [])["manifest"]) == []
    finally:
        pool.discard(w)


#: 仓库里其它含柱 / 直方图的真脚本（入口 `main`、只用内置 runtime 的那些）：每张图都不许报
REAL_BAR_SCRIPTS = (
    "examples/figures/fig2_comparison.py",
    "tests/acceptance/corpus/c01_lines_scatter_bars.py",
    "tests/compat/cases/axes_layout/al_grids.py",
    "tests/compat/cases/axes_layout/al_ticks.py",
    "tests/compat/cases/core_artists/ca_basic_series.py",
    "tests/compat/cases/core_artists/ca_distributions.py",
    "tests/compat/cases/core_artists/ca_projections.py",
    "tests/compat/cases/core_artists/ca_research_patterns.py",
    "tests/compat/cases/metamorphic/mm_barlabel_helper.py",
    "tests/compat/cases/metamorphic/mm_histogram_helper.py",
)


@needs_worker
@pytest.mark.parametrize("rel", REAL_BAR_SCRIPTS)
def test_real_bar_scripts_in_the_repo_are_not_flagged(tmp_path, rel):
    src = ROOT / rel
    dst = tmp_path / src.parent.name
    shutil.copytree(src.parent, dst)
    w = pool.one_shot(src.name, str(dst), "main")
    try:
        stems = list(w.ensure_built().get("stems") or {})
        assert stems, rel
        measured = 0
        for stem in stems:
            man = w.override(stem, [])["manifest"]
            measured += sum(1 for e in man["elements"] if "bar_baseline" in e)
            for pid in PROFILES:
                assert _hits(man, pid) == [], (rel, stem, pid)
    finally:
        pool.discard(w)
    # 极坐标那份脚本里只有玫瑰图等非直角坐标的柱：不发事实；其余每份都真量到了柱
    if rel.endswith("ca_projections.py"):
        assert measured == 0, rel
    else:
        assert measured > 0, rel


if __name__ == "__main__":
    import sys
    import tempfile

    if sys.argv[1:] != ["--write"]:
        raise SystemExit(
            "用法：PYTHONPATH=$PWD/src python tests/test_preflight_bar_baseline.py --write"
        )
    _w, _man = _render(
        Path(tempfile.mkdtemp()), "fig_barcases.py", CASES_SCRIPT, "BarCases", "main"
    )
    pool.discard(_w)
    REAL_SNAPSHOT.write_text(
        json.dumps(_snapshot(_man), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"已写入 {REAL_SNAPSHOT}")
