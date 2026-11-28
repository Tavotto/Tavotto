"""图例示意与它代表的曲线 / 柱样式不一致（`legend-style-mismatch`）：真 worker 的只读事实 + 预检 + 改图助手的事实。

事实 `legend_style_mismatch` 只在图例项元素上、有源（`legend_entry.source_gid`）、认得准、确实不一致时出现
（产生者只有 `engine/legendmodel.legend_style_mismatch`）；预检两侧与改图助手的「图上的事实」都只读它。
每个「不报」的用例都配一个「报」的对照，用例才有鉴别力。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import changeplan, figure_facts, normalize, pool, preflight, profiles

ROOT = Path(__file__).resolve().parent.parent
RULE = "legend-style-mismatch"
REAL_SNAPSHOT = ROOT / "tests" / "golden" / "legend_style_mismatch_real.json"
PROFILES = ("lab-publication-v1", "journal-base-v1", "free-form-v1")

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ------------------------------ 不需要 worker ------------------------------
def test_rule_is_registered_as_warn_in_all_three_severity_tables():
    raw = json.loads((ROOT / "src/tavotto/profiles/publication.json").read_text(encoding="utf-8"))
    tables = {pid: p.get("severity") or {} for pid, p in raw["profiles"].items()}
    for pid in PROFILES:
        assert tables[pid].get(RULE) == "warn", pid
        assert profiles.severity_of(profiles.load(pid), RULE) == "warn", pid


def test_rule_never_blocks_a_transaction():
    """改曲线是用户要的 / 规范要的，图例项不跟随而没跟上不该挡掉那一处改动。"""
    assert RULE in preflight.NON_BLOCKING_RULES
    issue = {"id": RULE, "severity": "error", "gids": ["axes_0.legend.texts_0"]}
    assert normalize._blocks(issue, {}) is False  # noqa: SLF001


def _entry(diffs, *, text="Blank", source="axes_0.lines_1", visible=True, gid=None):
    el = {
        "gid": gid or "axes_0.legend.texts_1",
        "role": "legend_text",
        "label": f"图例项 “{text}”",
        "editable": [
            {"prop": "text", "type": "text", "value": text},
            {"prop": "visible", "type": "bool", "value": visible},
        ],
        "legend_entry": {"index": 1, **({"source_gid": source} if source else {})},
    }
    if diffs is not None:
        el["legend_style_mismatch"] = diffs
    return el


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


FACE = {"prop": "markerfacecolor", "legend": "#9467bd", "source": "#ff7f0e"}


def test_preflight_reads_the_fact_and_says_which_entry_and_what():
    man = {"elements": [_entry([FACE])]}
    (hit,) = _hits(man)
    assert hit["severity"] == "warn"
    assert hit["gids"] == ["axes_0.legend.texts_1"] and hit["object_ids"] == ["p1"]
    assert hit["message"] == {"key": "legendStyleMismatchMarkerColor", "params": {"label": "Blank"}}
    assert hit["text"] == "图例「Blank」的标记颜色与曲线不一致"
    assert hit["detail"] == {"props": ["markerfacecolor"], "source_gid": "axes_0.lines_1"}


@pytest.mark.parametrize(
    ("props", "key"),
    [
        (["color"], "legendStyleMismatchColor"),
        (["linestyle"], "legendStyleMismatchLinestyle"),
        (["linewidth"], "legendStyleMismatchLinewidth"),
        (["marker"], "legendStyleMismatchMarker"),
        (["markerfacecolor", "markeredgecolor"], "legendStyleMismatchMarkerColor"),
        (["facecolor"], "legendStyleMismatchFill"),
        (["color", "marker"], "legendStyleMismatchMany"),
    ],
)
def test_message_key_per_kind_of_difference(props, key):
    diffs = [{"prop": p, "legend": "a", "source": "b"} for p in props]
    (hit,) = _hits({"elements": [_entry(diffs)]})
    assert hit["message"]["key"] == key


@pytest.mark.parametrize(
    "el",
    [
        _entry(None),  # 没有事实 = 一致 / 判不了
        _entry([FACE], source=None),  # 没有对应关系
        _entry([FACE], visible=False),  # 条目藏起来了
        _entry([{"prop": "zorder", "legend": 1, "source": 2}]),  # 不认得的属性
        _entry("garbage"),
    ],
)
def test_preflight_stays_quiet_without_a_usable_fact(el):
    assert _hits({"elements": [el]}) == []


def test_change_verification_does_not_count_it_as_a_new_problem():
    """改图验收（`figureverify._issue_keys`）不把它算进「这次新增的问题」，问题面板照报。"""
    from tavotto.engine import figureverify

    man = {"size_mm": [80.0, 60.0], "elements": [_entry([FACE])]}
    prof = profiles.load("lab-publication-v1")
    assert len(_hits(man)) == 1
    assert not any(k[0] == RULE for k in figureverify._issue_keys(man, prof))  # noqa: SLF001


def test_hidden_legend_is_not_reported():
    leg = {
        "gid": "axes_0.legend",
        "role": "legend",
        "editable": [{"prop": "visible", "type": "bool", "value": False}],
    }
    assert _hits({"elements": [leg, _entry([FACE])]}) == []
    leg["editable"][0]["value"] = True
    assert len(_hits({"elements": [leg, _entry([FACE])]})) == 1


def test_figure_facts_list_the_mismatch_first_with_the_fix():
    fixable = {**FACE, "fix": {"prop": "binding", "value": "follow_source"}}
    stuck = {"prop": "markerfacecolor", "legend": "#9467bd", "source": "#2ca02c"}
    color = {
        "prop": "color",
        "legend": "#ff7f0e",
        "source": "#2ca02c",
        "fix": {"prop": "handle_color", "value": "#2ca02c"},
    }
    man = {
        "elements": [
            _entry([fixable]),
            _entry(
                [color, stuck],
                text="Catalyst",
                gid="axes_0.legend.texts_0",
                source="axes_0.lines_0",
            ),
        ]
    }
    lines = figure_facts.figure_facts(man)
    assert lines[0] == (
        "图例项「Blank」（axes_0.legend.texts_1）与它代表的 axes_0.lines_1 样式不一致："
        "标记填充色 图例 #9467bd / 曲线 #ff7f0e。改法：axes_0.legend.texts_1 的 binding 设为 follow_source"
    )
    assert lines[1] == (
        "图例项「Catalyst」（axes_0.legend.texts_0）与它代表的 axes_0.lines_0 样式不一致："
        "线色 图例 #ff7f0e / 曲线 #2ca02c；标记填充色 图例 #9467bd / 曲线 #2ca02c。"
        "改法：axes_0.legend.texts_0 的 handle_color 设为 #2ca02c，标记填充色在图内改不了（要回脚本改句柄）"
    )
    assert figure_facts.figure_facts({"elements": [_entry(None)]}) == []


def test_mismatch_fact_survives_the_line_cap_on_a_busy_figure():
    """多系列图上事实很多：不一致排在最前，不被 `MAX_LINES` 截掉。"""
    axes = [
        {
            "gid": f"axes_{i}",
            "role": "axes",
            "label": f"子图 {i}",
            "bbox": [0.1 + 0.2 * (i % 4), 0.1 + 0.2 * (i // 4), 0.15, 0.15],
            "editable": [{"prop": "position", "type": "rect", "value": [0, 0, 1, 1]}],
        }
        for i in range(12)
    ]
    notes = [
        {
            "gid": f"axes_{i}.texts_{k}",
            "role": "text",
            "label": f"注 {k}",
            "bbox": [0.12 + 0.2 * (i % 4), 0.12 + 0.2 * (i // 4), 0.01, 0.01],
            "editable": [],
        }
        for i in range(12)
        for k in range(6)
    ]
    lines = figure_facts.figure_facts({"elements": [*axes, *notes, _entry([FACE])]})
    assert len(lines) == figure_facts.MAX_LINES
    assert any("图例项「Blank」" in ln for ln in lines)


def test_facts_reach_the_change_plan_prompt_with_the_rule():
    facts = figure_facts.figure_facts({"elements": [_entry([FACE])]})
    for source in ("assistant", "review"):
        system, user = changeplan.build_review_prompt(
            [{"id": "a1", "anchor": {"kind": "overall"}, "text": "有什么问题"}],
            [],
            source=source,
            facts=facts,
        )
        assert "图例项「Blank」" in user and "<figure_facts>" in user
        assert "样式不一致" in system and "不动曲线" in system
        # 什么时候主动提只在助手提示里（审阅提示没有 reply / 待办）
        assert ("图例与曲线对不上要主动说" in system) == (source == "assistant")


# ------------------------------ 真 worker ------------------------------
CASES = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


def main():
    t = np.linspace(0, 60, 30)
    fig, axs = plt.subplots(3, 4, figsize=(12, 8), dpi=50)
    a = iter(axs.ravel())

    def nxt(title):
        ax = next(a)
        ax.set_title(title)
        return ax

    # 一致：默认图例
    ax = nxt("same")
    ax.plot(t, t, color="#1f77b4", label="A")
    ax.plot(t, t / 2, color="#ff7f0e", ls="--", marker="s", label="B")
    ax.legend()

    # 标记填充色：作者单独改了图例句柄
    ax = nxt("face")
    ax.plot(t, t, color="#ff7f0e", marker="s", label="B")
    ax.legend().legend_handles[0].set_markerfacecolor("#9467bd")

    # 线色
    ax = nxt("color")
    ax.plot(t, t, color="#ff7f0e", label="B")
    ax.legend().legend_handles[0].set_color("#9467bd")

    # 标记形状：图例代理句柄与曲线同名、形状不同
    ax = nxt("marker")
    ax.plot(t, t, color="#2ca02c", marker="s", label="C")
    ax.legend(handles=[Line2D([], [], color="#2ca02c", marker="o", label="C")])

    # 线型
    ax = nxt("linestyle")
    ax.plot(t, t, color="#2ca02c", ls="--", label="D")
    ax.legend(handles=[Line2D([], [], color="#2ca02c", ls="-", label="D")])

    # 线宽：差得少不报（1.0 vs 1.2），差得多报（1.0 vs 2.5）
    ax = nxt("linewidth")
    ax.plot(t, t, color="#2ca02c", lw=1.0, label="thin")
    ax.plot(t, t / 2, color="#d62728", lw=1.0, label="thick")
    ax.legend(handles=[
        Line2D([], [], color="#2ca02c", lw=1.2, label="thin"),
        Line2D([], [], color="#d62728", lw=2.5, label="thick"),
    ])

    # 'none' / 不可见：曲线空心标记 + 图例实心；图例只画标记、曲线有线（只一边画的不比）
    ax = nxt("none")
    ax.plot(t, t, color="#1f77b4", marker="o", mfc="none", label="hollow")
    ax.plot(t, t / 2, color="#ff7f0e", marker="s", label="nolines")
    ax.legend(handles=[
        Line2D([], [], color="#1f77b4", marker="o", mfc="#d62728", label="hollow"),
        Line2D([], [], color="#ff7f0e", marker="s", ls="None", label="nolines"),
    ])

    # 看不见的曲线
    ax = nxt("hidden")
    ln, = ax.plot(t, t, color="#ff7f0e", label="gone")
    ax.legend().legend_handles[0].set_color("#9467bd")
    ln.set_visible(False)

    # 找不到对应关系：代理句柄的名字和样式都对不上任何曲线
    ax = nxt("nosource")
    ax.plot(t, t, color="#ff7f0e", label="real")
    ax.legend(handles=[Line2D([], [], color="#9467bd", label="other")])

    # 误差棒 / 填充带：句柄认不准，改了图例颜色也不报
    ax = nxt("errorbar")
    ax.errorbar(t[:5], t[:5], yerr=1, color="#ff7f0e", label="eb")
    ax.fill_between(t, t, t + 5, color="#1f77b4", alpha=0.3, label="band")
    leg = ax.legend()
    for h in leg.legend_handles:
        try:
            h.set_color("#9467bd")
        except Exception:
            pass

    # 柱：图例色块的面色
    ax = nxt("bars")
    ax.bar([0, 1, 2], [1, 2, 3], color="#ff7f0e", label="bars")
    ax.legend().legend_handles[0].set_facecolor("#9467bd")

    # 隐藏的图例
    ax = nxt("hiddenlegend")
    ax.plot(t, t, color="#ff7f0e", label="B")
    leg = ax.legend()
    leg.legend_handles[0].set_color("#9467bd")
    leg.set_visible(False)

    fig.savefig("Cases.pdf")


if __name__ == "__main__":
    main()
"""


def _render(tmp: Path, source: str, stem: str):
    (tmp / "cases.py").write_text(source, encoding="utf-8")
    w = pool.one_shot("cases.py", str(tmp), "main")
    try:
        w.ensure_built()
        return w, w.override(stem, [])["manifest"]
    except BaseException:
        pool.discard(w)
        raise


def _by_title(manifest) -> dict[str, list[dict]]:
    """子图标题 → 该子图里的图例项元素（按 gid 排序）。"""
    titles = {}
    for e in manifest["elements"]:
        if e["role"] == "title" and e["gid"].endswith(".title"):
            text = next((f["value"] for f in e["editable"] if f["prop"] == "text"), "")
            titles[e["gid"].split(".", 1)[0]] = text
    out: dict[str, list[dict]] = {}
    for e in manifest["elements"]:
        if e["role"] == "legend_text" and e.get("legend_entry"):
            out.setdefault(titles.get(e["gid"].split(".", 1)[0], ""), []).append(e)
    return {k: sorted(v, key=lambda e: e["gid"]) for k, v in out.items()}


def _props(e):
    return [d["prop"] for d in e.get("legend_style_mismatch") or []]


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    w, man = _render(tmp_path_factory.mktemp("lsm"), CASES, "Cases")
    yield w, man
    pool.discard(w)


@needs_worker
def test_real_worker_facts_per_case(cases):
    _w, man = cases
    by = _by_title(man)
    assert [_props(e) for e in by["same"]] == [[], []]
    (face,) = by["face"]
    assert face["legend_style_mismatch"] == [
        {
            "prop": "markerfacecolor",
            "legend": "#9467bd",
            "source": "#ff7f0e",
            "fix": {"prop": "binding", "value": "follow_source"},
        }
    ]
    assert _props(by["color"][0]) == ["color"]
    assert _props(by["marker"][0]) == ["marker"]
    assert _props(by["linestyle"][0]) == ["linestyle"]
    thin, thick = by["linewidth"]
    assert _props(thin) == [] and _props(thick) == ["linewidth"]
    assert [_props(e) for e in by["none"]] == [[], []]
    assert [_props(e) for e in by.get("hidden", [])] == [[] for _ in by.get("hidden", [])]
    assert all("source_gid" not in e["legend_entry"] for e in by["nosource"])
    assert [_props(e) for e in by["errorbar"]] == [[] for _ in by["errorbar"]]
    assert _props(by["bars"][0]) == ["facecolor"]
    assert [_props(e) for e in by["hiddenlegend"]] == [[]]


@needs_worker
def test_real_worker_preflight_reports_exactly_the_mismatched_entries(cases):
    _w, man = cases
    by = _by_title(man)
    want = sorted(
        e["gid"] for t in ("face", "color", "marker", "linestyle", "bars") for e in by[t]
    ) + [by["linewidth"][1]["gid"]]
    for pid in PROFILES:
        (hit,) = _hits(man, pid)
        assert sorted(hit["gids"]) == sorted(want), pid


def _snapshot(manifest) -> dict:
    """前端消费的真 worker 快照：图例与图例项元素只留规则读的那几格（`visible` / `text` 的 prop / value、
    `legend_entry`、`legend_style_mismatch`）。"""
    keep = {"visible", "text"}
    els = []
    for e in manifest["elements"]:
        if e["role"] not in ("legend", "legend_text") or (
            e["role"] == "legend_text" and not e.get("legend_entry")
        ):
            continue
        out = {
            "gid": e["gid"],
            "role": e["role"],
            "label": e["label"],
            "editable": [
                {"prop": f["prop"], "value": f["value"]}
                for f in e["editable"]
                if f.get("prop") in keep
            ],
        }
        for key in ("legend_entry", "legend_style_mismatch"):
            if key in e:
                out[key] = e[key]
        els.append(out)
    (hit,) = _hits(manifest)
    return {
        "comment": "真 worker 渲染 tests/test_legend_style_mismatch.py 的 CASES 得到的图例 / 图例项元素"
        "（只留 gid / role / label、visible 与 text 两格 editable、legend_entry、legend_style_mismatch）。"
        "pytest 重渲染逐字比对；web/src/lib/preflight.legendStyle.test.ts 消费它。",
        "elements": els,
        "expected": {
            "gids": sorted(hit["gids"]),
            "message": hit["message"],
            "detail": hit["detail"],
        },
    }


@needs_worker
def test_real_snapshot_for_the_frontend_is_what_the_worker_emits(cases):
    """`legend_style_mismatch_real.json` 不许手改：`PYTHONPATH=$PWD/src python tests/test_legend_style_mismatch.py --write`。"""
    assert json.loads(REAL_SNAPSHOT.read_text(encoding="utf-8")) == _snapshot(cases[1])


@needs_worker
def test_fixes_named_in_the_fact_actually_clear_it(tmp_path):
    """事实里写的「改法」重渲染后真的清掉；写不出改法的（显式另一种标记填充色 + 已有 handle_* override）只报不修。"""
    src = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    t = np.linspace(0, 60, 30)
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot(t, t, color="#1f77b4", label="Catalyst")
    ax.plot(t, t / 2, color="#ff7f0e", ls="--", marker="s", label="Blank")
    ax.legend().legend_handles[1].set_markerfacecolor("#9467bd")
    fig.savefig("Fig.pdf")


if __name__ == "__main__":
    main()
"""
    w, man = _render(tmp_path, src, "Fig")
    entry = "axes_0.legend.texts_1"

    def facts(patches):
        m = w.override("Fig", patches)["manifest"]
        return next(e for e in m["elements"] if e["gid"] == entry).get("legend_style_mismatch")

    # 顺序有讲究：先跟随、再撤掉跟随并写 handle_* 的热会话里，示意线留着跟随时的标记色（引擎既有的热态问题，
    # 不是这条检查的事），所以「恢复跟随」放在最后验
    try:
        (d,) = facts([])
        assert d["fix"] == {"prop": "binding", "value": "follow_source"}
        # 只改一条跟随中的曲线：图例跟着变，另一项照旧
        assert facts([{"gid": "axes_0.lines_0", "prop": "color", "value": "#2ca02c"}]) == [d]
        # 曲线改了色、图例项已有 handle_* override（不再跟随）：线色与描边改得掉，显式紫色的标记填充改不掉
        base = [
            {"gid": entry, "prop": "handle_marker", "value": "s"},
            {"gid": "axes_0.lines_1", "prop": "color", "value": "#2ca02c"},
        ]
        got = {x["prop"]: x.get("fix") for x in facts(base)}
        assert got == {
            "color": {"prop": "handle_color", "value": "#2ca02c"},
            "markerfacecolor": None,
            "markeredgecolor": {"prop": "handle_color", "value": "#2ca02c"},
        }
        left = facts([*base, {"gid": entry, "prop": "handle_color", "value": "#2ca02c"}])
        assert [x["prop"] for x in left] == ["markerfacecolor"] and "fix" not in left[0]
        assert facts([]) == [d]
        assert facts([{"gid": entry, **d["fix"]}]) is None
    finally:
        pool.discard(w)


#: 仓库里带图例的真脚本：一张都不许报（不误报优先）
REAL_SCRIPTS = (
    "src/tavotto/resources/tutorial_project/fig1_kinetics.py",
    "src/tavotto/resources/tutorial_project/fig2_correlation.py",
    "examples/figures/fig2_comparison.py",
    "tests/acceptance/corpus/c01_lines_scatter_bars.py",
    "tests/compat/cases/core_artists/ca_legend_colorbar.py",
    "tests/compat/cases/core_artists/ca_research_patterns.py",
    "tests/fixtures/figurebench/line_two_series/figure.py",
    "tests/fixtures/figurebench/errorbar_fit/figure.py",
    "tests/fixtures/figurebench/grouped_bars/figure.py",
)


@needs_worker
@pytest.mark.parametrize("rel", REAL_SCRIPTS)
def test_real_scripts_in_the_repo_are_not_flagged(tmp_path, rel):
    import shutil

    src = ROOT / rel
    dst = tmp_path / src.parent.name
    shutil.copytree(src.parent, dst)
    w = pool.one_shot(src.name, str(dst), "main")
    try:
        stems = list(w.ensure_built().get("stems") or {})
        assert stems, rel
        bound = 0
        for stem in stems:
            man = w.override(stem, [])["manifest"]
            bound += sum(
                1 for e in man["elements"] if (e.get("legend_entry") or {}).get("source_gid")
            )
            assert [e["gid"] for e in man["elements"] if "legend_style_mismatch" in e] == [], stem
            assert _hits(man) == [], stem
    finally:
        pool.discard(w)
    assert bound > 0, rel


if __name__ == "__main__":
    import sys
    import tempfile

    if sys.argv[1:] != ["--write"]:
        raise SystemExit(
            "用法：PYTHONPATH=$PWD/src python tests/test_legend_style_mismatch.py --write"
        )
    _w, _man = _render(Path(tempfile.mkdtemp()), CASES, "Cases")
    pool.discard(_w)
    REAL_SNAPSHOT.write_text(
        json.dumps(_snapshot(_man), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"已写入 {REAL_SNAPSHOT}")
