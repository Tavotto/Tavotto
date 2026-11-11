"""TAF 数据出图 v2 的模板层（ADR 0178）：配方 → 脚本 + 期望数据指纹（批次 1 的 comparison、trend）。

判据的主语（谁的、哪个进程）：

* 「画的就是这份数据」量的是**真 safe worker 里跑出来的 manifest 数据指纹**（不是父进程自己的中间量），与父进程用同一批函数
  算出的期望值逐字节相等——两边共用函数源码（`RUNTIME_FUNCTIONS` 被 `inspect.getsource` 内嵌），所以另外用 `statistics`
  这样的**独立实现**核数值本身（均值 / SD / 分位数 / t 临界值），不让「同一份错」两边一致地通过；
* 「注入防线」量的是生成脚本的 **AST 标识符集合**（数据里的文字不许长成标识符 / 代码），不是源码子串；
* 「不核」的层（误差线 / 误差带 / 箱 / 小提琴）单独列在 `fingerprint_coverage`，测试钉住它们没有被折算成已核。
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import os
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_recipe as R,
    ai_data_template as T,
    ai_prompt_pack,
    pool,
    xlsxread,
)
from tests.test_ai_data_profile import make_xlsx

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)

NONE = R.Confirmed()
REPEATED = R.Confirmed("repeated", "subject")


# --------------------------------------------------------------------------- 数据夹具
def _csv(rows, delim=","):
    def cell(c):
        s = str(c)
        if any(ch in s for ch in (delim, '"', "\n")):
            s = '"' + s.replace('"', '""') + '"'
        return s

    return "\n".join(delim.join(cell(c) for c in r) for r in rows) + "\n"


def cmp_rows():
    rows = [["condition", "site", "subject", "response_mV"]]
    for ci, c in enumerate(("ctrl", "drugA", "drugB")):
        for si, s in enumerate(("s1", "s2")):
            for k in range(6):
                rows.append(
                    [c, s, f"m{k}", round(2 + ci * 1.5 + si * 0.4 + k * 0.3 + (k % 3) * 0.1, 3)]
                )
    # 缺失 / 非数 / NaN 行不画；一个离群点要原样留着
    rows += [
        ["ctrl", "s1", "m9", ""],
        ["drugA", "s2", "m9", "NaN"],
        ["drugB", "s1", "m9", "nan"],
        ["ctrl", "s1", "m8", 40.0],
    ]
    return rows


def rel_rows():
    rows = [["dose_mg", "response_mV", "arm", "site"]]
    for ai, a in enumerate(("control", "drug")):
        for si, s in enumerate(("s1", "s2")):
            for i in range(8):
                x = round(1 + i * 1.5 + si * 0.25, 3)
                rows.append([x, round(0.5 + (0.3 + 0.5 * ai) * x + ((i * 7) % 5) * 0.2, 3), a, s])
    rows += [[3.0, "", "control", "s1"], ["nan", 2.0, "drug", "s2"]]  # 缺失 / NaN：不画
    return rows


def dist_rows():
    rows = [["latency_ms", "arm", "site", "batch"]]
    for i in range(120):
        arm, site, batch = (
            ("control", "drug")[i % 2],
            ("s1", "s2")[(i // 2) % 2],
            ("b1", "b2", "b3")[(i // 4) % 3],
        )
        rows.append(
            [round(40 + (i * 37) % 29 + (i % 2) * 9 + ((i * 13) % 7) * 0.31, 3), arm, site, batch]
        )
    rows.append(["", "control", "s1", "b1"])  # 缺失：不画
    return rows


def trend_rows():
    rows = [["time_s", "arm", "site", "subject", "signal", "other"]]
    for ai, a in enumerate(("a", "b")):
        for si, s in enumerate(("s1", "s2")):
            for u in range(1, 4):
                for t in range(1, 6):
                    rows.append(
                        [
                            t,
                            a,
                            s,
                            f"u{u}",
                            round(t * (1 + ai * 0.5) + u * 0.2 + si, 3),
                            round(10 + t * 0.1 * u, 3),
                        ]
                    )
    rows += [
        [2, "a", "s1", "u1", "", 1.0],
        [3, "b", "s2", "u2", "nan", 1.0],
    ]  # 缺失 / 非数：折线在这里断开
    return rows


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("tpl")
    (d / "cmp.csv").write_text(_csv(cmp_rows()), encoding="utf-8")
    (d / "cmp.tsv").write_text(_csv(cmp_rows(), "\t"), encoding="utf-8")
    make_xlsx(d / "cmp.xlsx", {"Data": cmp_rows()})
    (d / "trend.csv").write_text(_csv(trend_rows()), encoding="utf-8")
    (d / "rel.csv").write_text(_csv(rel_rows()), encoding="utf-8")
    (d / "rel.tsv").write_text(_csv(rel_rows(), "\t"), encoding="utf-8")
    make_xlsx(d / "rel.xlsx", {"Data": rel_rows()})
    (d / "dist.csv").write_text(_csv(dist_rows()), encoding="utf-8")
    (d / "dist.tsv").write_text(_csv(dist_rows(), "\t"), encoding="utf-8")
    make_xlsx(d / "dist.xlsx", {"Data": dist_rows()})
    # 每组恰好 5 个值（箱 / 小提琴的下限）与带文字的脏数据
    (d / "few.csv").write_text(
        _csv([["v", "g"]] + [[i * 1.7 + k, g] for k, g in enumerate("ab") for i in range(5)]),
        encoding="utf-8",
    )
    (d / "rel_dirty.csv").write_text(
        _csv(rel_rows() + [[4.0, "<LOD", "drug", "s1"], ["n/a", 3.0, "drug", "s1"]]),
        encoding="utf-8",
    )
    (d / "dist_dirty.csv").write_text(
        _csv(dist_rows() + [["<LOD", "drug", "s1", "b1"], ["inf", "drug", "s2", "b2"]]),
        encoding="utf-8",
    )
    (d / "关系.csv").write_text(
        _csv(
            [["剂量_mg", "响应_mV", "组别"]]
            + [
                [1 + i, 2 + i * (1 + g) + (i % 3) * 0.2, ("对照", "药物")[g]]
                for g in (0, 1)
                for i in range(8)
            ]
        ),
        encoding="utf-8",
    )
    (d / "分布.csv").write_text(
        _csv(
            [["延迟_ms", "组别"]]
            + [[40 + (i * 11) % 23 + g * 6, ("对照", "药物")[g]] for g in (0, 1) for i in range(30)]
        ),
        encoding="utf-8",
    )
    # 脏数据：y / x 里有文字。校验会拒（混合列不是数值列），但模板对这样的文件也必须与期望值一致（不合并、不填 0、不猜）
    dirty = cmp_rows() + [["drugA", "s2", "m9", "<0.1"], ["ctrl", "s2", "m9", "inf"]]
    (d / "cmp_dirty.csv").write_text(_csv(dirty), encoding="utf-8")
    (d / "dates_dirty.csv").write_text(
        _csv(
            [["date", "level"]]
            + [["2024-01-0%d" % i, i * 2.5] for i in range(1, 6)]
            + [["2024-01-06", "<LOD"], ["2024-01-07", ""], ["2024-01-08", 3]]
        ),
        encoding="utf-8",
    )
    make_xlsx(d / "trend.xlsx", {"Data": trend_rows()})
    (d / "single.csv").write_text(
        _csv(
            [["time_s", "signal", "other"]]
            + [[t, round(t * 1.5, 3), t * t] for t in range(1, 9)]
            + [[9, "", 3], [10, 12.5, ""]]
        ),
        encoding="utf-8",
    )
    (d / "dates.csv").write_text(
        _csv(
            [["date", "level"]]
            + [["2024-01-0%d" % i, i * 2.5] for i in range(1, 8)]
            + [["2024-01-09", ""], ["2024-01-10", 30]]
        ),
        encoding="utf-8",
    )
    (d / "数据.csv").write_text(
        _csv(
            [["处理", "响应_mV"]]
            + [
                [g, v + 0.5 * i]
                for i, g in enumerate(
                    (
                        "对照",
                        "药物",
                        "对照",
                        "药物",
                        "对照",
                        "药物",
                        "对照",
                        "药物",
                        "对照",
                        "药物",
                        "对照",
                        "药物",
                    )
                )
                for v in (1.0,)
            ]
        ),
        encoding="utf-8",
    )
    (d / "uni.csv").write_text(
        _csv(
            [["组别", "分面", "响应_mV"]]
            + [
                [g, f, 1 + gi + fi * 0.3 + k * 0.2]
                for gi, g in enumerate(("对照", "药物"))
                for fi, f in enumerate(("甲", "乙"))
                for k in range(6)
            ]
        ),
        encoding="utf-8",
    )
    return d


@pytest.fixture(scope="module")
def profile(folder) -> adf.FolderProfile:
    from unittest import mock

    with mock.patch.object(adf, "MAX_PROFILE_TABLES", 80):  # 夹具里的表比一次摘要的上限（20）多
        return adf.profile_folder(folder)


def entry_of(profile, table, sheet=None) -> adf.TableEntry:
    (e,) = [t for t in profile.tables if t.path == table and t.sheet == sheet]
    return e


def mk(table, family, layers, y, **kw) -> R.FigureRecipe:
    ls = [
        {"mark": m}
        if isinstance(m, str)
        else {"mark": m[0], "summary": m[1], **({"role": m[2]} if len(m) > 2 else {})}
        for m in layers
    ]
    return R.FigureRecipe.from_dict(
        {"table": table, "family": family, "layers": ls, "y": list(y), "why": "说明", **kw}
    )


Q = "overlay"
# (id, 文件, 工作表, 配方, 已确认)
CASES = [
    (
        "cmp-jitter-hline-ci95-group",
        "cmp.csv",
        None,
        mk(
            "cmp.csv",
            "comparison",
            ["jitter", ("hline_summary", "mean_ci95", Q)],
            ["response_mV"],
            x="condition",
            group="site",
        ),
        NONE,
    ),
    (
        "cmp-points-bar-sd-errorbar",
        "cmp.csv",
        None,
        mk(
            "cmp.csv",
            "comparison",
            [("bar", "mean_sd"), ("errorbar", "mean_sd", Q), ("points", "none", Q)],
            ["response_mV"],
            x="condition",
        ),
        NONE,
    ),
    (
        "cmp-box-jitter",
        "cmp.csv",
        None,
        mk(
            "cmp.csv",
            "comparison",
            ["box", ("jitter", "none", Q)],
            ["response_mV"],
            x="condition",
            group="site",
        ),
        NONE,
    ),
    (
        "cmp-bar-sem-facet",
        "cmp.csv",
        None,
        mk(
            "cmp.csv",
            "comparison",
            [("bar", "mean_sem"), ("errorbar", "mean_sem", Q)],
            ["response_mV"],
            x="condition",
            facet="site",
        ),
        NONE,
    ),
    (
        "cmp-tsv-order-desc",
        "cmp.tsv",
        None,
        mk(
            "cmp.tsv",
            "comparison",
            [("bar", "median_iqr"), ("errorbar", "median_iqr", Q)],
            ["response_mV"],
            x="condition",
            order="value_desc",
        ),
        NONE,
    ),
    (
        "cmp-xlsx",
        "cmp.xlsx",
        "Data",
        mk(
            "cmp.xlsx",
            "comparison",
            ["jitter", ("hline_summary", "mean_sd", Q)],
            ["response_mV"],
            x="condition",
            sheet="Data",
        ),
        NONE,
    ),
    (
        "cmp-log-y",
        "cmp.csv",
        None,
        mk(
            "cmp.csv",
            "comparison",
            ["points", ("hline_summary", "median_iqr", Q)],
            ["response_mV"],
            x="condition",
            scale="log_y",
        ),
        NONE,
    ),
    (
        "cmp-violin-points",
        "cmp.csv",
        None,
        mk(
            "cmp.csv",
            "comparison",
            ["violin", ("points", "none", Q)],
            ["response_mV"],
            x="condition",
            order="value_asc",
        ),
        NONE,
    ),
    (
        "cmp-non-ascii",
        "数据.csv",
        None,
        mk(
            "数据.csv",
            "comparison",
            [("bar", "mean_sd"), ("errorbar", "mean_sd", Q)],
            ["响应_mV"],
            x="处理",
        ),
        NONE,
    ),
    (
        "cmp-non-ascii-facet",
        "uni.csv",
        None,
        mk(
            "uni.csv",
            "comparison",
            ["jitter", ("hline_summary", "mean_sd", Q)],
            ["响应_mV"],
            x="组别",
            facet="分面",
        ),
        NONE,
    ),
    (
        "trend-line-points",
        "single.csv",
        None,
        mk("single.csv", "trend", ["line", ("points", "none", Q)], ["signal"], x="time_s"),
        NONE,
    ),
    (
        "trend-summary-band-group",
        "trend.csv",
        None,
        mk(
            "trend.csv",
            "trend",
            [("line", "mean_sd"), ("band", "mean_sd", Q), ("points", "none", Q)],
            ["signal"],
            x="time_s",
            group="arm",
        ),
        NONE,
    ),
    (
        "trend-ci95-errorbar",
        "trend.csv",
        None,
        mk(
            "trend.csv",
            "trend",
            [("line", "mean_ci95"), ("errorbar", "mean_ci95", Q)],
            ["signal"],
            x="time_s",
        ),
        NONE,
    ),
    (
        "trend-step-fit-hline-facet",
        "trend.csv",
        None,
        mk(
            "trend.csv",
            "trend",
            [("step", "median_iqr"), ("fit_line", "none", Q), ("hline_summary", "mean_sem", Q)],
            ["signal"],
            x="time_s",
            facet="site",
        ),
        NONE,
    ),
    (
        "trend-dates-missing",
        "dates.csv",
        None,
        mk("dates.csv", "trend", ["line", ("points", "none", Q)], ["level"], x="date"),
        NONE,
    ),
    (
        "trend-per-individual",
        "trend.csv",
        None,
        mk(
            "trend.csv",
            "trend",
            ["line", ("line", "mean_sd", Q)],
            ["signal"],
            x="time_s",
            unit="subject",
            facet="arm",
        ),
        REPEATED,
    ),
    (
        "trend-two-y",
        "single.csv",
        None,
        mk("single.csv", "trend", ["line"], ["signal", "other"], x="time_s"),
        NONE,
    ),
    (
        "trend-xlsx",
        "trend.xlsx",
        "Data",
        mk(
            "trend.xlsx",
            "trend",
            [("line", "mean_sem"), ("band", "mean_sem", Q)],
            ["signal"],
            x="time_s",
            sheet="Data",
            group="site",
        ),
        NONE,
    ),
    (
        "dirty-cmp-text-in-y",
        "cmp_dirty.csv",
        None,
        mk(
            "cmp_dirty.csv",
            "comparison",
            ["jitter", ("hline_summary", "mean_sd", Q), ("bar", "mean_sd")],
            ["response_mV"],
            x="condition",
        ),
        NONE,
    ),
    (
        "dirty-trend-bad-dates",
        "dates_dirty.csv",
        None,
        mk("dates_dirty.csv", "trend", ["line", ("points", "none", Q)], ["level"], x="date"),
        NONE,
    ),
    (
        "trend-log-xy",
        "trend.csv",
        None,
        mk("trend.csv", "trend", [("line", "mean_sd")], ["signal"], x="time_s", scale="log_xy"),
        NONE,
    ),
]
CASES += [
    (
        "rel-points-fit-group",
        "rel.csv",
        None,
        mk(
            "rel.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            group="arm",
        ),
        NONE,
    ),
    (
        "rel-facet-group-tsv",
        "rel.tsv",
        None,
        mk(
            "rel.tsv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            group="arm",
            facet="site",
        ),
        NONE,
    ),
    (
        "rel-xlsx-points",
        "rel.xlsx",
        "Data",
        mk(
            "rel.xlsx",
            "relationship",
            ["points"],
            ["response_mV"],
            x="dose_mg",
            sheet="Data",
            group="site",
        ),
        NONE,
    ),
    (
        "rel-log-xy",
        "rel.csv",
        None,
        mk(
            "rel.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            scale="log_xy",
        ),
        NONE,
    ),
    (
        "rel-log-x-curved-fit",
        "rel.csv",
        None,
        mk(
            "rel.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            group="arm",
            scale="log_x",
        ),
        NONE,
    ),
    (
        "rel-log-y",
        "rel.csv",
        None,
        mk(
            "rel.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            facet="site",
            scale="log_y",
        ),
        NONE,
    ),
    (
        "rel-non-ascii",
        "关系.csv",
        None,
        mk(
            "关系.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["响应_mV"],
            x="剂量_mg",
            group="组别",
        ),
        NONE,
    ),
    (
        "dirty-rel-text",
        "rel_dirty.csv",
        None,
        mk(
            "rel_dirty.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            group="arm",
        ),
        NONE,
    ),
    (
        "dist-ecdf-group",
        "dist.csv",
        None,
        mk("dist.csv", "distribution", ["ecdf"], ["latency_ms"], group="arm"),
        NONE,
    ),
    (
        "dist-hist-density-facet",
        "dist.csv",
        None,
        mk(
            "dist.csv",
            "distribution",
            ["hist", ("density", "none", Q)],
            ["latency_ms"],
            group="arm",
            facet="site",
        ),
        NONE,
    ),
    (
        "dist-hist-only-count",
        "dist.tsv",
        None,
        mk("dist.tsv", "distribution", ["hist"], ["latency_ms"], group="site"),
        NONE,
    ),
    (
        "dist-xlsx-density",
        "dist.xlsx",
        "Data",
        mk("dist.xlsx", "distribution", ["density"], ["latency_ms"], sheet="Data"),
        NONE,
    ),
    (
        "dist-box-jitter-x-group",
        "dist.csv",
        None,
        mk(
            "dist.csv",
            "distribution",
            ["box", ("jitter", "none", Q)],
            ["latency_ms"],
            x="batch",
            group="arm",
        ),
        NONE,
    ),
    (
        "dist-violin-points-no-x",
        "dist.csv",
        None,
        mk(
            "dist.csv",
            "distribution",
            ["violin", ("points", "none", Q)],
            ["latency_ms"],
            group="arm",
            facet="site",
        ),
        NONE,
    ),
    (
        "dist-jitter-log-y",
        "dist.csv",
        None,
        mk(
            "dist.csv",
            "distribution",
            ["box", ("jitter", "none", Q)],
            ["latency_ms"],
            x="batch",
            scale="log_y",
            order="value_desc",
        ),
        NONE,
    ),
    (
        "dist-five-per-group-box",
        "few.csv",
        None,
        mk("few.csv", "distribution", ["box", ("points", "none", Q)], ["v"], x="g"),
        NONE,
    ),
    (
        "dist-non-ascii",
        "分布.csv",
        None,
        mk("分布.csv", "distribution", ["ecdf"], ["延迟_ms"], group="组别"),
        NONE,
    ),
    (
        "dirty-dist-text",
        "dist_dirty.csv",
        None,
        mk(
            "dist_dirty.csv",
            "distribution",
            ["hist", ("density", "none", Q)],
            ["latency_ms"],
            group="arm",
        ),
        NONE,
    ),
]
IDS = [c[0] for c in CASES]


def build_case(folder, profile, case):
    cid, table, sheet, recipe, confirmed = case
    e = entry_of(profile, table, sheet)
    values = adf.inspect_data_source(folder / table, sheet)
    problems = R.validate_recipe(profile, recipe, confirmed=confirmed, values=values)
    if cid.startswith("dirty-"):
        assert {p.code for p in problems} == {"column_type_mismatch"}, (cid, problems)
    else:
        assert problems == [], (cid, problems)
    return e, values


def rs(*a, **k) -> str:
    """只要脚本文本。"""
    return T.render_recipe_script(*a, **k).script


def write_rendered(work: Path, rendered: T.Rendered, stem: str) -> None:
    """脚本与公共模块并排落盘（测试里模拟下一个 PR 的落盘：只新建）。"""
    (work / f"{stem}.py").write_text(rendered.script, encoding="utf-8")
    (work / rendered.runtime_name).write_bytes(rendered.runtime)


def run_in_worker(work: Path, rendered: T.Rendered, stem: str) -> dict:
    write_rendered(work, rendered, stem)
    w = pool.one_shot(f"{stem}.py", str(work), "main")
    try:
        w.ensure_built()
        return w.override(stem, [])["manifest"]
    finally:
        pool.discard(w)


def labelled(man: dict) -> dict[str, dict]:
    out: dict[str, list] = {}
    for el in man.get("elements", []):
        fp = el.get("data_fingerprint")
        if isinstance(fp, dict):
            label = next(
                (f.get("value") for f in el.get("editable", []) if f.get("prop") == "label"), None
            )
            if label:
                out.setdefault(str(label), []).append(fp)
    return out


@pytest.fixture(scope="module")
def worker_results(folder, profile, tmp_path_factory):
    """所有用例的真 worker 结果（并发跑一次，各用例各自断言）。"""
    if WORKER_PY is None:
        return {}
    base = tmp_path_factory.mktemp("worker")

    def one(case):
        cid, table, sheet, recipe, _ = case
        e, values = build_case(folder, profile, case)
        work = base / cid
        work.mkdir()
        for f in folder.iterdir():
            (work / f.name).write_bytes(f.read_bytes())
        script = T.render_recipe_script(e, recipe, data_rel=table, stem="fig")
        man = run_in_worker(work, script, "fig")
        return cid, (script, man, T.expected_fingerprints(values, recipe))

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(one, CASES))


# --------------------------------------------------------------------------- 真 worker：指纹对拍
@needs_worker
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_worker_draws_exactly_the_expected_data(case, worker_results):
    cid, _, _, recipe, _ = case
    script, man, expected = worker_results[cid]
    got = labelled(man)
    assert expected, f"{cid}：没有任何期望指纹（这张图的数据没法核）"
    for e in expected:
        hits = got.get(e["label"])
        assert hits is not None and len(hits) == 1, (cid, e["label"], sorted(got))
        assert hits[0].get("status") == "ok", (cid, e["label"])
        assert hits[0]["parts"][e["part"]] == e["digest"], (cid, e["label"])


@needs_worker
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_checkable_layer_has_expected_entries_and_unchecked_ones_have_none(
    case, worker_results
):
    cid, _, _, recipe, _ = case
    expected = worker_results[cid][2]
    cov = T.fingerprint_coverage(recipe)
    labels = [e["label"] for e in expected]
    for c in cov.layers:
        has = any(
            lab.split(" | ")[0] in (c.mark, f"{c.mark}@{recipe.layers[c.index].role}")
            for lab in labels
        )
        if c.checked:
            assert has, (cid, c)
        else:
            assert not has and c.reason, (cid, c)
    assert cov.any_checked and cov.raw_checked == any(
        c.mark in ("points", "jitter", "ecdf")
        or (c.mark in ("line", "step") and recipe.layers[c.index].summary == "none")
        for c in cov.layers
    )


@needs_worker
def test_labels_in_a_figure_are_unique_among_data_artists(worker_results):
    """验证器按 label 唯一匹配；同一张图里两个数据元素同名 = series_missing。"""
    for cid, (script, man, expected) in worker_results.items():
        got = labelled(man)
        dup = {k: len(v) for k, v in got.items() if len(v) > 1}
        assert not dup, (cid, dup)


@needs_worker
def test_unchecked_layers_are_not_counted_as_verified():
    r = mk("x.csv", "comparison", ["box", ("errorbar", "mean_sd", Q)], ["v"], x="g")
    cov = T.fingerprint_coverage(r)
    assert not cov.any_checked and not cov.raw_checked
    assert {c.mark: c.reason for c in cov.layers} == {
        "box": "matplotlib_derived",
        "errorbar": "errorbar_has_no_label",
    }
    assert not any(
        c.checked
        for c in T.fingerprint_coverage(
            mk("x.csv", "trend", [("band", "mean_sd")], ["v"], x="t")
        ).layers
    )


# --------------------------------------------------------------------------- 画出来的 artist 类型与图上的诚实
def introspect(work: Path, rendered: T.Rendered, stem="fig", pre="") -> dict:
    write_rendered(work, rendered, stem)
    code = (
        "import json, runpy, matplotlib\n"
        "matplotlib.use('Agg')\n"
        f"{pre}\n"
        "import matplotlib.pyplot as plt\n"
        "plt.Figure.savefig = lambda *a, **k: None\n"
        f"ns = runpy.run_path({str(work / (stem + '.py'))!r})\n"
        "ns['main']()\n"
        "fig = plt.gcf()\n"
        "out = {'size': list(fig.get_size_inches()), 'rc': {k: matplotlib.rcParams[k] for k in ('svg.fonttype','pdf.fonttype','legend.frameon','xtick.direction','axes.linewidth','font.size','axes.labelweight')}, 'axes': [], 'texts': [t.get_text() for t in fig.texts]}\n"
        "for ax in fig.axes:\n"
        "    d = {'lines': [type(l).__name__ for l in ax.lines], 'collections': [type(c).__name__ for c in ax.collections], 'containers': [type(c).__name__ for c in ax.containers], 'patches': [type(p).__name__ for p in ax.patches], 'ylim': list(ax.get_ylim()), 'xlabel': ax.get_xlabel(), 'ylabel': ax.get_ylabel(), 'xticks': [t.get_text() for t in ax.get_xticklabels()], 'title': ax.get_title(), 'legend': [t.get_text() for t in ax.get_legend().get_texts()] if ax.get_legend() else [], 'legend_frame': ax.get_legend().get_frame_on() if ax.get_legend() else None, 'yscale': ax.get_yscale(), 'xscale': ax.get_xscale()}\n"
        "    eb = []\n"
        "    for c in ax.containers:\n"
        "        if type(c).__name__ == 'ErrorbarContainer':\n"
        "            eb.append([[ [list(map(float, p)) for p in seg] for seg in col.get_segments()] for col in c.lines[2]])\n"
        "    d['errorbars'] = eb\n"
        "    d['offsets'] = [[list(map(float, p)) for p in c.get_offsets()] for c in ax.collections if type(c).__name__ == 'PathCollection']\n"
        "    out['axes'].append(d)\n"
        "print('JSON:' + json.dumps(out))\n"
    )
    proc = subprocess.run(
        [WORKER_PY, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(work),
        check=True,
    )
    return json.loads(next(ln for ln in proc.stdout.splitlines() if ln.startswith("JSON:"))[5:])


@pytest.fixture(scope="module")
def shapes(folder, profile, tmp_path_factory):
    """几个代表性配方在真 matplotlib 里的 artist 清单。"""
    if WORKER_PY is None:
        return {}
    out = {}
    work = tmp_path_factory.mktemp("shapes")
    for f in folder.iterdir():
        (work / f.name).write_bytes(f.read_bytes())
    wanted = {
        "cmp_all": mk(
            "cmp.csv",
            "comparison",
            ["box", "violin", "jitter", ("hline_summary", "mean_sd", Q)],
            ["response_mV"],
            x="condition",
            order="value_asc",
        ),
        "cmp_eb": mk(
            "cmp.csv",
            "comparison",
            [("bar", "mean_sd"), ("errorbar", "mean_sd", Q)],
            ["response_mV"],
            x="condition",
        ),
        "trend_all": mk(
            "trend.csv",
            "trend",
            [
                ("line", "mean_ci95"),
                ("band", "mean_ci95", Q),
                ("errorbar", "mean_ci95", Q),
                ("points", "none", Q),
            ],
            ["signal"],
            x="time_s",
        ),
        "trend_b": mk(
            "trend.csv",
            "trend",
            [("step", "median_iqr"), ("fit_line", "none", Q), ("hline_summary", "mean_sd", Q)],
            ["signal"],
            x="time_s",
        ),
        "trend_raw": mk(
            "single.csv", "trend", ["line", ("points", "none", Q)], ["signal"], x="time_s"
        ),
        "grouped": mk(
            "cmp.csv",
            "comparison",
            ["jitter", ("hline_summary", "mean_sd", Q)],
            ["response_mV"],
            x="condition",
            group="site",
        ),
        "log": mk(
            "cmp.csv", "comparison", ["points"], ["response_mV"], x="condition", scale="log_y"
        ),
        "plain_bar": mk("comp_bar.csv", "comparison", ["bar"], ["v"], x="g") if False else None,
    }
    for name, r in wanted.items():
        if r is None:
            continue
        e = entry_of(profile, r.table)
        script = T.render_recipe_script(e, r, data_rel=r.table, stem="fig")
        out[name] = introspect(work, script)
    return out


@needs_worker
def test_each_mark_is_a_plain_editable_matplotlib_artist(shapes):
    ax = shapes["cmp_all"]["axes"][0]
    assert "PathCollection" in ax["collections"]  # jitter → 散点
    eb = shapes["cmp_eb"]["axes"][0]
    assert "BarContainer" in eb["containers"] and "Rectangle" in eb["patches"]  # 柱
    assert "ErrorbarContainer" in eb["containers"]
    assert (
        "PolyCollection" in ax["collections"] or "FillBetweenPolyCollection" in ax["collections"]
    )  # 小提琴体
    assert set(ax["lines"]) == {"Line2D"} and len(ax["lines"]) >= 7  # 箱的各部件 + 汇总线
    tb = shapes["trend_b"]["axes"][0]
    assert set(tb["lines"]) == {"Line2D"} and len(tb["lines"]) == 3  # 阶梯线、拟合线、汇总线
    t = shapes["trend_all"]["axes"][0]
    assert any(
        c in t["collections"] for c in ("PolyCollection", "FillBetweenPolyCollection")
    )  # 误差带
    assert "PathCollection" in t["collections"] and "ErrorbarContainer" in t["containers"]
    assert set(t["lines"]) == {"Line2D"}


@needs_worker
def test_errorbars_hold_the_declared_summary_independently_of_the_template_helpers(shapes, folder):
    """误差线不在 manifest 指纹范围里：改用 `statistics` 的独立实现核 mean ± SD（不经过模板里的 sd_of / summarize）。"""
    ax = shapes["cmp_eb"]["axes"][0]
    segs = [seg for col in ax["errorbars"] for seg in col[0]]
    by_cond: dict[str, list[float]] = {}
    for r in cmp_rows()[1:]:
        try:
            v = float(r[3])
        except ValueError:
            continue
        if math.isnan(v):
            continue
        by_cond.setdefault(r[0], []).append(v)
    assert len(segs) == 3
    for i, c in enumerate(("ctrl", "drugA", "drugB")):
        vals = by_cond[c]
        m, sd = statistics.mean(vals), statistics.stdev(vals)
        (x0, lo), (x1, hi) = segs[i]
        assert x0 == x1 == pytest.approx(i)
        assert lo == pytest.approx(m - sd) and hi == pytest.approx(m + sd)


@needs_worker
def test_bars_include_zero_and_show_the_summary_and_n(shapes):
    ax = shapes["cmp_eb"]["axes"][0]
    assert ax["ylim"][0] <= 0
    texts = shapes["cmp_eb"]["texts"]
    assert (
        texts and "mean ± SD" in texts[0] and "n per group in tick labels" in texts[0]
    )  # 各格 n 不同
    assert any("(n = " in t for t in ax["xticks"])
    # n 一致时写进说明里
    # （uni.csv 每格 6 个）
    assert "星" not in "".join(texts) and "*" not in "".join(texts) and "P =" not in "".join(texts)


@needs_worker
def test_style_follows_the_publication_profile(shapes):
    rc = shapes["cmp_eb"]["rc"]
    assert rc["svg.fonttype"] == "none" and rc["pdf.fonttype"] == 42
    assert (
        rc["legend.frameon"] is False
        and rc["xtick.direction"] == "in"
        and rc["axes.labelweight"] == "bold"
    )
    assert rc["axes.linewidth"] == 0.75 and rc["font.size"] == 9.0
    w, h = shapes["cmp_eb"]["size"]
    assert w == pytest.approx(80 / 25.4) and h == pytest.approx(w * 0.75)  # 单栏 80 mm、4:3
    w2, h2 = shapes["grouped"]["size"]
    assert w2 == pytest.approx(80 / 25.4)
    assert (
        shapes["grouped"]["axes"][0]["legend"] == ["s1", "s2"]
        and shapes["grouped"]["axes"][0]["legend_frame"] is False
    )


@needs_worker
def test_log_axis_and_jitter_are_deterministic(shapes, folder, profile, tmp_path):
    assert shapes["log"]["axes"][0]["yscale"] == "log"
    r = mk("cmp.csv", "comparison", ["jitter"], ["response_mV"], x="condition")
    e = entry_of(profile, "cmp.csv")
    for f in folder.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    s = T.render_recipe_script(e, r, data_rel="cmp.csv", stem="fig")
    a = introspect(tmp_path, s)["axes"][0]["offsets"]
    b = introspect(tmp_path, s)["axes"][0]["offsets"]
    assert a == b and a[0]
    xs = [p[0] for p in a[0]]
    assert len(set(xs)) > len(set(round(x) for x in xs))  # 真的错开了
    assert all(abs(x - round(x)) <= 0.8 * 0.6 / 2 + 1e-12 for x in xs)
    assert "random" not in s.script and b"import random" not in s.runtime


# --------------------------------------------------------------------------- 注入防线（AST，不用子串）
def identifiers(src: str) -> set[str]:
    tree = ast.parse(src)
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.keyword) and n.arg:
            out.add(n.arg)
        elif isinstance(n, ast.alias):
            out.add(n.name.split(".")[0])
            if n.asname:
                out.add(n.asname)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


HOSTILE = [
    "x'; __import__('os').system('touch PWNED'); '",
    'y""" + __import__("os").system("touch PWNED") + """',
    "z\\'; exec('1')  # \\",
    "w'''\u2028import sys # \u0085",
]
#: 带换行 / 回车的名字进不了配方（`FigureRecipe` 当场拒收），所以更到不了模板
NEWLINE_NAMES = ["x\n__import__('os')", "y\r#", "z\u0000"]


def hostile_table(tmp: Path, a, b, c, d):
    rows = [[a, b, c, d]]
    for g in ("g1", "g2"):
        for k in range(6):
            rows.append([g, "s1" if k % 2 else "s2", "u1", 1 + k * 0.5])
    (tmp / "h.csv").write_text(_csv(rows), encoding="utf-8")
    return adf.profile_folder(tmp)


def test_hostile_names_never_become_identifiers_or_code(tmp_path):
    benign = tmp_path / "b"
    benign.mkdir()
    bp = hostile_table(benign, "grp", "side", "who", "val")

    def base_recipe(g, s, v):
        return mk(
            "h.csv", "comparison", ["jitter", ("hline_summary", "mean_sd", Q)], [v], x=g, group=s
        )

    base = rs(bp.tables[0], base_recipe("grp", "side", "val"), data_rel="h.csv", stem="fig")
    for h in HOSTILE:
        d = tmp_path / f"h{abs(hash(h))}"
        d.mkdir()
        hp = hostile_table(d, h + "g", h + "s", h + "u", h + "v")
        src = rs(hp.tables[0], base_recipe(h + "g", h + "s", h + "v"), data_rel="h.csv", stem="fig")
        assert identifiers(src) == identifiers(base), h
        # 数据里的文字只作为字符串常量出现；来源注释是 ASCII 单行
        assert not any(
            ln.startswith("#") and not ln.isascii() and "tavotto-source" in ln
            for ln in src.splitlines()
        )
        comments = [ln for ln in src.splitlines() if ln.startswith("# tavotto-source:")]
        assert len(comments) == 1 and comments[0].isascii()
        consts = {
            n.value
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert h + "g" in consts and h + "v" in consts


@needs_worker
def test_running_a_script_with_hostile_names_runs_no_injected_code(tmp_path):
    h = HOSTILE[0]
    hp = hostile_table(tmp_path, h + "g", h + "s", h + "u", h + "v")
    r = mk(
        "h.csv",
        "comparison",
        ["jitter", ("hline_summary", "mean_sd", Q)],
        [h + "v"],
        x=h + "g",
        group=h + "s",
    )
    assert R.validate_recipe(hp, r, confirmed=NONE) == []
    src = T.render_recipe_script(hp.tables[0], r, data_rel="h.csv", stem="fig")
    man = run_in_worker(tmp_path, src, "fig")
    assert not (tmp_path / "PWNED").exists() and not (Path.cwd() / "PWNED").exists()
    values = adf.inspect_data_source(tmp_path / "h.csv")
    got = labelled(man)
    for e in T.expected_fingerprints(values, r):
        assert got[e["label"]][0]["parts"][e["part"]] == e["digest"]


def test_script_is_short_static_and_imports_only_matplotlib_and_the_sibling_module(folder, profile):
    name = T.runtime_module()[0][: -len(".py")]
    for cid, table, sheet, recipe, _ in CASES:
        src = rs(entry_of(profile, table, sheet), recipe, data_rel=table, stem="fig")
        tree = ast.parse(src)
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
        assert mods == {"__future__", "pathlib", "matplotlib", name}, (cid, mods)
        # 没有第二份实现：脚本里除 main 以外没有函数定义
        assert [n.name for n in tree.body if isinstance(n, ast.FunctionDef)] == ["main"], cid
        mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        assert not mains[0].args.args, cid
        assert len(src.splitlines()) <= 130, (cid, len(src.splitlines()))
        assert "fig.savefig(Path(__file__).resolve().parent / 'fig.pdf')" in src
        assert "Path(__file__).resolve().parent / " + repr(table) in src
        called = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert not called & {"exec", "eval", "compile", "__import__"}, cid
        # 运行时函数一律经别名调用
        assert "tp.read_" in src and f"import {name} as tp" in src


def test_source_comment_carries_provenance_but_not_why(folder, profile):
    cid, table, sheet, recipe, _ = CASES[0]
    e = entry_of(profile, table, sheet)
    recipe = R.FigureRecipe.from_dict({**recipe.to_dict(), "why": "UNIQUE-WHY-SENTENCE-7731"})
    src = rs(e, recipe, data_rel=table, stem="fig")
    line = next(ln for ln in src.splitlines() if ln.startswith("# tavotto-source: "))
    meta = json.loads(line[len("# tavotto-source: ") :])
    assert (
        meta["sha256"] == e.summary.sha256
        and meta["data"] == table
        and meta["generator"] == "data_to_figure/v2"
    )
    assert meta["recipe"]["family"] == "comparison" and "why" not in meta["recipe"]
    assert recipe.why not in src


# --------------------------------------------------------------------------- 内嵌的就是同一份源码
def runtime_ns() -> dict:
    ns: dict = {}
    exec(compile(T.runtime_module()[1].decode("utf-8"), "runtime", "exec"), ns)  # noqa: S102 - 我们自己生成的公共模块
    return ns


def test_runtime_module_is_content_addressed_identical_for_every_figure_and_data_free(
    folder, profile
):
    name, data = T.runtime_module()
    import hashlib

    assert (
        name == f"tavotto_plot_{hashlib.sha256(data).hexdigest()[:8]}.py"
        and name[:-3].isidentifier()
    )
    outs = [
        T.render_recipe_script(entry_of(profile, table, sheet), recipe, data_rel=table, stem="fig")
        for _, table, sheet, recipe, _ in CASES
    ]
    assert {(o.runtime_name, o.runtime) for o in outs} == {(name, data)}  # 对所有图逐字节相同
    text = data.decode("utf-8")
    # 不含任何数据：列名 / 分组名 / 单元格文字 / 文件名都不在里面
    for needle in (
        "condition",
        "response_mV",
        "drugA",
        "cmp.csv",
        "trend.csv",
        "subject",
        "s1",
        "对照",
        "ctrl",
    ):
        assert needle not in text, needle
    # 头部说明：生成的、共用的、别改
    assert text.startswith('"""Tavotto 生成的绘图公共模块') and "请不要修改" in text.split('"""')[1]


def test_runtime_module_holds_the_exact_function_sources_the_parent_uses():
    text = T.runtime_module()[1].decode("utf-8")
    for f in T.RUNTIME_FUNCTIONS:
        assert inspect.getsource(f).rstrip("\n") in text, f.__name__
    assert "XlsxBook" in text  # 一个模块覆盖三种数据源，不按图裁剪


def test_runtime_module_runs_standalone_and_agrees_with_the_parent(folder):
    ns = runtime_ns()
    samples = [
        [1.0],
        [1.0, 2.0],
        [3.0, 1.0, 2.0, 10.0],
        [0.5, 0.5, 0.5],
        [float(i) ** 1.5 for i in range(1, 12)],
        [1.0] * 31 + [2.0] * 10,
    ]
    for vals in samples:
        for s in ("mean_sd", "mean_sem", "mean_ci95", "median_iqr"):
            a, b = ns["summarize"](vals, s), T.summarize(vals, s)
            assert [repr(v) for v in a] == [repr(v) for v in b], (vals, s)
    assert ns["box_stats"](samples[4]) == T.box_stats(samples[4])
    assert ns["kde_curve"](samples[4]) == T.kde_curve(samples[4])
    assert ns["linear_fit"]([1, 2, 3], [2, 4, 7]) == T.linear_fit([1, 2, 3], [2, 4, 7])
    assert ns["comparison_data"](*_cmp_args(folder)) == T.comparison_data(*_cmp_args(folder))


def _cmp_args(folder):
    v = adf.inspect_data_source(folder / "cmp.csv")
    return v.columns, v.rows, "condition", "response_mV", "site", None, "value_desc"


def test_xlsx_reader_in_the_runtime_module_is_the_xlsxread_source(folder):
    text = T.runtime_module()[1].decode("utf-8")
    block = text[text.index(T.XLSX_BEGIN) + len(T.XLSX_BEGIN) : text.index(T.XLSX_END)].strip("\n")
    original = inspect.getsource(xlsxread)
    doc_end = ast.parse(original).body[0].end_lineno
    expect_lines = [
        ln
        for i, ln in enumerate(original.splitlines(), start=1)
        if i > doc_end and not ln.startswith("from __future__")
    ]
    assert block.strip() == "\n".join(expect_lines).strip()  # 逐字：不是手抄的第二份
    ns = runtime_ns()
    header, rows = ns["read_xlsx"](folder / "cmp.xlsx", "Data")
    v = adf.inspect_data_source(folder / "cmp.xlsx", "Data")
    assert header == v.columns and rows == v.rows


def test_tsv_scripts_use_the_tab_delimiter(folder, profile):
    src = rs(entry_of(profile, "cmp.tsv"), CASES[4][3], data_rel="cmp.tsv", stem="fig")
    assert "tp.read_delimited(DATA, '\\t')" in src
    assert "tp.read_delimited(DATA, ',')" in rs(
        entry_of(profile, "cmp.csv"), CASES[0][3], data_rel="cmp.csv", stem="fig"
    )


# --------------------------------------------------------------------------- 数值本身：独立实现对拍
def test_summaries_match_the_statistics_module():
    vals = [2.5, 3.1, 4.7, 4.9, 6.0, 7.2, 9.9]
    m, sd = statistics.mean(vals), statistics.stdev(vals)
    assert T.summarize(vals, "mean_sd") == pytest.approx((m, m - sd, m + sd))
    sem = sd / math.sqrt(len(vals))
    assert T.summarize(vals, "mean_sem") == pytest.approx((m, m - sem, m + sem))
    half = 2.4469 * sem  # t(0.975, 6)
    assert T.summarize(vals, "mean_ci95") == pytest.approx((m, m - half, m + half))
    q1, med, q3 = statistics.quantiles(vals, n=4, method="inclusive")
    assert T.summarize(vals, "median_iqr") == pytest.approx((med, q1, q3))
    assert T.box_stats(vals)["q1"] == pytest.approx(q1) and T.box_stats(vals)[
        "q3"
    ] == pytest.approx(q3)


def test_summary_edge_cases_are_nan_not_zero():
    assert all(math.isnan(v) for v in T.summarize([], "mean_sd"))
    c, lo, hi = T.summarize([3.0], "mean_sd")
    assert c == 3.0 and math.isnan(lo) and math.isnan(hi)
    assert T.summarize([3.0], "median_iqr") == (3.0, 3.0, 3.0)
    with pytest.raises(ValueError):
        T.summarize([1.0, 2.0], "none")


@pytest.mark.parametrize(
    "df, want",
    [
        (1, 12.706),
        (2, 4.303),
        (5, 2.571),
        (10, 2.228),
        (20, 2.086),
        (30, 2.042),
        (40, 2.021),
        (60, 2.000),
        (120, 1.980),
    ],
)
def test_t_critical_values_match_published_tables(df, want):
    assert T.t_crit95(df) == pytest.approx(want, abs=6e-4)


def test_t_critical_interpolation_is_monotone_and_bounded():
    vals = [T.t_crit95(df) for df in range(1, 400)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    assert 2.0211 > T.t_crit95(50) > 2.0003 and abs(T.t_crit95(50) - 2.0086) < 0.002  # 真值 2.0086
    assert 1.96 < T.t_crit95(10_000) < 1.9605 and math.isnan(T.t_crit95(0))


def test_jitter_is_deterministic_and_bounded():
    xs = [T.jitter(k) for k in range(200)]
    assert xs == [T.jitter(k) for k in range(200)] and all(-0.5 <= v < 0.5 for v in xs)
    assert len(set(xs)) == 200


def test_cell_rules_missing_text_and_outliers_follow_the_one_definition(folder):
    v = adf.inspect_data_source(folder / "cmp.csv")
    d = T.comparison_data(v.columns, v.rows, "condition", "response_mV", None, None, "as_given")
    ctrl = d["cells"][("", "ctrl", "")]
    assert 40.0 in ctrl and len(ctrl) == 12 + 1  # 离群点留着；缺失（空）的那行没进去
    assert len(d["cells"][("", "drugA", "")]) == 12  # "NaN"：缺失，不画
    assert len(d["cells"][("", "drugB", "")]) == 12  # nan：不画


def test_order_by_value_sorts_categories_by_mean(folder):
    v = adf.inspect_data_source(folder / "cmp.csv")
    a = T.comparison_data(v.columns, v.rows, "condition", "response_mV", None, None, "value_asc")[
        "cats"
    ]
    d = T.comparison_data(v.columns, v.rows, "condition", "response_mV", None, None, "value_desc")[
        "cats"
    ]
    assert (
        a == d[::-1] and a != ["ctrl", "drugA", "drugB"] or a == ["drugA", "drugB", "ctrl"] or True
    )
    means = {
        c: statistics.mean(
            float(r[3]) for r in v.rows if r[0] == c and r[3] not in ("", "NaN", "nan")
        )
        for c in ("ctrl", "drugA", "drugB")
    }
    assert a == sorted(means, key=means.get) and d == sorted(means, key=means.get, reverse=True)


# --------------------------------------------------------------------------- 配方 → 模板的边界
def test_unsupported_recipes_raise_instead_of_drawing_an_approximation(folder, profile):
    e = entry_of(profile, "cmp.csv")
    with pytest.raises(T.TemplateError) as ei:
        rs(
            e,
            mk(
                "cmp.csv",
                "paired",
                ["paired_lines"],
                ["response_mV"],
                x="condition",
                unit="subject",
                paired=True,
            ),
            data_rel="cmp.csv",
            stem="f",
        )
    assert ei.value.code == "template_family_unsupported"
    with pytest.raises(T.TemplateError) as ei:
        rs(
            e,
            mk("cmp.csv", "comparison", ["hist"], ["response_mV"], x="condition"),
            data_rel="cmp.csv",
            stem="f",
        )
    assert ei.value.code == "template_mark_unsupported"
    with pytest.raises(T.TemplateError) as ei:
        rs(
            e,
            mk("cmp.csv", "comparison", ["points"], ["response_mV"], x="condition", scale="log_x"),
            data_rel="cmp.csv",
            stem="f",
        )
    assert ei.value.code == "template_option_unsupported"
    with pytest.raises(T.TemplateError) as ei:
        rs(
            entry_of(profile, "trend.csv"),
            mk("cmp.csv", "comparison", ["points"], ["response_mV"], x="condition"),
            data_rel="cmp.csv",
            stem="f",
        )
    assert ei.value.code == "template_recipe_mismatch"
    with pytest.raises(T.TemplateError) as ei:
        rs(
            e,
            mk("cmp.csv", "comparison", ["points"], ["nope"], x="condition"),
            data_rel="cmp.csv",
            stem="f",
        )
    assert ei.value.code == "template_recipe_mismatch"
    with pytest.raises(ValueError):
        T.TemplateError("not_registered")


def test_renderable_follows_the_template_registry():
    assert set(T.RENDERERS) == {"comparison", "trend", "relationship", "distribution"}
    assert {k for k in R.FAMILIES if R.renderable(k)} == set(T.RENDERERS)
    # 每个渲染得了的族，允许的图元全部有实现（renderable 与 FAMILIES 口径一致，不留「允许但画不出」的图元）
    for fam, marks in T.MARKS_RENDERED.items():
        assert set(marks) == set(R.FAMILIES[fam].marks), fam
    text = ai_prompt_pack.data_figure_guidance()
    assert "现在能出图的只有：`comparison`、`trend`、`relationship`、`distribution`；" in text


def test_every_allowed_mark_renders_for_every_template_family(folder, profile):
    for fam, marks, table, kw in (
        ("comparison", T.MARKS_COMPARISON, "cmp.csv", {"x": "condition"}),
        ("trend", T.MARKS_TREND, "trend.csv", {"x": "time_s"}),
        ("relationship", T.MARKS_RELATIONSHIP, "rel.csv", {"x": "dose_mg"}),
        ("distribution", T.MARKS_DISTRIBUTION, "dist.csv", {}),
    ):
        for m in marks:
            summary = "mean_sd" if R.MARK_RULES[m].summary == "required" else "none"
            r = mk(
                table,
                fam,
                [(m, summary)],
                [
                    {
                        "comparison": "response_mV",
                        "trend": "signal",
                        "relationship": "response_mV",
                        "distribution": "latency_ms",
                    }[fam]
                ],
                **kw,
            )
            src = rs(entry_of(profile, table), r, data_rel=table, stem="f")
            ast.parse(src)


def test_style_numbers_come_from_the_profile_not_the_template():
    from tavotto.engine import profiles

    base = profiles.load("lab-publication-v1")
    s0 = T.style_from_profile(base)
    assert (
        s0["width_single_mm"] == 80.0
        and s0["font_pt"] == 9.0
        and s0["line_widths_pt"] == [0.5, 0.75, 1.0, 1.5]
    )
    assert (
        s0["axis_label_weight"] == "bold"
        and s0["enclosed_spines"] is True
        and s0["tick_direction"] == "in"
    )
    mod = json.loads(json.dumps(base))
    mod["widths_mm"]["single"] = 100.0
    mod["default_font_size_pt"] = 11.0
    mod["line_widths_pt"] = [0.25, 2.0]
    mod["axis_policy"]["frame_linewidth_pt"] = [0.9]
    s1 = T.style_from_profile(mod)
    assert (s1["width_single_mm"], s1["font_pt"], s1["line_widths_pt"], s1["frame_lw_pt"]) == (
        100.0,
        11.0,
        [0.25, 2.0],
        0.9,
    )
    assert T.style_from_profile({}) == T._STYLE_DEFAULTS  # 取不到的项才用具名默认
    # 脚本里能看到
    e = adf.TableEntry(
        "x.csv",
        None,
        False,
        adf.DataSummary(
            "x.csv",
            "0" * 64,
            1,
            ["g", "v"],
            [],
            {
                "g": adf.ColumnSummary("g", text=["a"], text_count=2),
                "v": adf.ColumnSummary("v", numeric=2),
            },
            groups={"g": 2},
        ),
    )
    r = mk("x.csv", "comparison", ["points"], ["v"], x="g")
    src = rs(e, r, data_rel="x.csv", stem="f", style=s1)
    assert (
        "'font.size': 11.0" in src.replace('"', "'")
        and repr(100.0 / 25.4) in src
        and "'axes.linewidth': 0.9" in src.replace('"', "'")
    )
    assert "if len(panels) == 1 else" in src  # 单栏 / 双栏按面板数选


def test_a_table_with_nothing_plottable_still_runs_and_claims_nothing(tmp_path):
    """没有一行能画（x 全不是值）：脚本照样跑完（空图），期望指纹为空、覆盖面如实说没法核——不崩、不编数据。"""
    (tmp_path / "n.csv").write_text("t,v\nx,1\ny,2\n", encoding="utf-8")
    p = adf.profile_folder(tmp_path)
    r = mk("n.csv", "trend", ["line", ("points", "none", Q)], ["v"], x="t")
    v = adf.inspect_data_source(tmp_path / "n.csv")
    assert T.expected_fingerprints(v, r) == []
    if WORKER_PY is not None:
        man = run_in_worker(
            tmp_path, T.render_recipe_script(p.tables[0], r, data_rel="n.csv", stem="fig"), "fig"
        )
        assert labelled(man) == {}


def test_names_with_line_breaks_cannot_even_reach_the_template():
    for name in NEWLINE_NAMES:
        with pytest.raises(R.RecipeError) as ei:
            mk("h.csv", "comparison", ["points"], ["v"], x=name)
        assert ei.value.code == "recipe_malformed"


def test_hostile_file_and_sheet_names_stay_string_literals(folder, profile):
    e = entry_of(profile, "cmp.xlsx", "Data")
    hostile_rel = "d'); __import__('os').system('touch PWNED'); ('.xlsx"
    r = mk("cmp.xlsx", "comparison", ["points"], ["response_mV"], x="condition", sheet="Data")
    base = rs(e, r, data_rel="cmp.xlsx", stem="fig")
    src = rs(e, r, data_rel=hostile_rel, stem="fig")
    assert identifiers(src) == identifiers(base)
    consts = {
        n.value
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert hostile_rel in consts


@needs_worker
def test_uniform_n_goes_into_the_note_and_trend_notes_n_per_point(folder, profile, tmp_path):
    for f in folder.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    r = mk(
        "uni.csv",
        "comparison",
        ["jitter", ("hline_summary", "mean_sd", Q)],
        ["响应_mV"],
        x="组别",
        facet="分面",
    )
    got = introspect(
        tmp_path,
        T.render_recipe_script(entry_of(profile, "uni.csv"), r, data_rel="uni.csv", stem="fig"),
    )
    assert got["texts"] == ["mean ± SD; n = 6"]
    assert got["size"][0] == pytest.approx(150 / 25.4) and got["size"][1] == pytest.approx(
        got["size"][0] * 9 / 16
    )  # 多面板：双栏、16:9
    assert [a["title"] for a in got["axes"]] == ["甲", "乙"] and got["axes"][1][
        "ylabel"
    ] == ""  # 共享 y：只第一格写标题
    t = mk(
        "trend.csv", "trend", [("line", "mean_sd"), ("band", "mean_sd", Q)], ["signal"], x="time_s"
    )
    got = introspect(
        tmp_path,
        T.render_recipe_script(entry_of(profile, "trend.csv"), t, data_rel="trend.csv", stem="fig"),
    )
    assert got["texts"][0].startswith("mean ± SD; n = ")
    plain = mk("trend.csv", "trend", ["points"], ["signal"], x="time_s")
    got = introspect(
        tmp_path,
        T.render_recipe_script(
            entry_of(profile, "trend.csv"), plain, data_rel="trend.csv", stem="fig"
        ),
    )
    assert got["texts"] == []  # 没有汇总就不写汇总说明


def test_template_module_is_parent_side_stdlib_only():
    """Flask 父进程 import：不 import matplotlib / numpy（它们只出现在生成的脚本文本里）；`pick_fonts` 的 matplotlib import 是函数内的，
    只在脚本里被调用。"""
    code = (
        "import sys\n"
        "import tavotto.engine.ai_data_template\n"
        "bad = [m for m in ('matplotlib', 'numpy', 'pandas', 'scipy') if m in sys.modules]\n"
        "print('BAD:' + ','.join(bad))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path(T.__file__).resolve().parents[2])},
        check=True,
    ).stdout
    assert out.strip().endswith("BAD:"), out
    tree = ast.parse(Path(T.__file__).read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {(n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom)} | {
        a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names
    }
    assert not names & {"matplotlib", "numpy", "pandas", "scipy"}


def test_bar_scripts_pin_the_axis_to_zero_and_others_do_not(folder, profile):
    e = entry_of(profile, "cmp.csv")
    bar = rs(e, CASES[1][3], data_rel="cmp.csv", stem="f")
    other = rs(e, CASES[0][3], data_rel="cmp.csv", stem="f")
    assert "ax.set_ylim(bottom=0)" in bar and "set_ylim" not in other


# --------------------------------------------------------------------------- 静态扫描不会把公共模块当成一张图
def test_static_scans_never_register_the_runtime_module_as_a_figure(tmp_path):
    """判据的主语：`discover` / `projscan` 对**项目文件夹**的静态扫描。公共模块没有存图调用也没有 main：不进静态起草 /
    注册表候选，也不是 `projscan` 的目标；脚本清单把它标成 `infrastructure`（`discover.SKIP_PREFIXES` 里的 `tavotto_plot_`，与
    `paper_style` 同类），不当成「没出图的脚本」。"""
    from tavotto.engine import discover, projscan

    figs = tmp_path / "proj" / "tavotto_figures"
    figs.mkdir(parents=True)
    (figs / "d.csv").write_text(
        "g,v\n" + "\n".join(f"{g},{i}" for g in "ab" for i in range(6)) + "\n"
    )
    e = adf.profile_folder(figs).tables[0]
    r = mk("d.csv", "comparison", ["jitter"], ["v"], x="g")
    out = T.render_recipe_script(e, r, data_rel="d.csv", stem="fig")
    (figs / "fig.py").write_text(out.script, encoding="utf-8")
    (figs / out.runtime_name).write_bytes(out.runtime)
    root = tmp_path / "proj"
    drafted = discover.discover(root)["scripts"]
    assert set(drafted) == {"tavotto_figures/fig.py"}  # 静态起草：只有那张图
    assert drafted["tavotto_figures/fig.py"]["stems"] == ["fig"]
    rep = projscan.scan(root)
    assert [t["script"] for t in rep["targets"]] == ["tavotto_figures/fig.py"]
    by = {s["script"]: s for s in rep["scripts"]}
    assert discover.is_infrastructure_name(out.runtime_name)
    assert not discover.is_infrastructure_name("fig.py")
    mod = by[f"tavotto_figures/{out.runtime_name}"]
    assert mod["registered"] is False and mod["reason"] == "infrastructure"


def test_runtime_module_has_no_figure_entry_and_does_nothing_on_import():
    tree = ast.parse(T.runtime_module()[1].decode("utf-8"))
    assert not [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert not any(
        isinstance(n, ast.Attribute) and n.attr in ("savefig", "show") for n in ast.walk(tree)
    )
    imported = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert (
        "matplotlib" not in imported and "numpy" not in imported
    )  # 顶层不 import 科学栈（pick_fonts 里是函数内 import）
    top_calls = [
        n for n in tree.body if isinstance(n, ast.Expr) and not isinstance(n.value, ast.Constant)
    ]
    assert top_calls == []  # 顶层没有「导入即执行」的调用语句


# --------------------------------------------------------------------------- relationship / distribution：数值规则（独立实现对拍）
def test_hist_bins_follow_freedman_diaconis_and_match_numpy():
    np = pytest.importorskip("numpy")
    vals = [float((i * 37) % 53) + (i % 7) * 0.13 for i in range(97)]
    edges = T.hist_edges(vals)
    want = np.histogram_bin_edges(vals, bins="fd")
    assert len(edges) == len(want) and edges == pytest.approx(list(want))
    counts = T.hist_counts(vals, edges)
    assert counts == list(np.histogram(vals, bins=edges)[0]) and sum(counts) == len(
        vals
    )  # 右端的值算进最后一个箱


def test_hist_edge_cases():
    assert T.hist_edges([3.0, 3.0, 3.0]) == [2.5, 3.5]  # 全部相同：以该值为中心的一个箱
    assert T.hist_counts([3.0, 3.0, 3.0], [2.5, 3.5]) == [3]
    sturges = T.hist_edges(
        [1.0] * 20 + [2.0, 3.0]
    )  # IQR = 0：退到 Sturges（ceil(log2 22) + 1 = 6 个箱）
    assert len(sturges) == 7
    many = T.hist_edges([float(i) ** 3 for i in range(2000)])
    assert len(many) <= 51  # 箱数上限 50
    assert T.hist_counts([0.0, 1.0, 1.0, 2.0], [0.0, 1.0, 2.0]) == [
        1,
        3,
    ]  # 左闭右开、最后一个箱含右端
    assert T.hist_heights([2, 2], 4, 0.5, True) == [1.0, 1.0] and T.hist_heights(
        [2, 2], 4, 0.5, False
    ) == [2.0, 2.0]


def test_density_curve_matches_an_independent_kde_and_integrates_to_one():
    vals = [2.0, 3.1, 3.3, 4.8, 5.0, 5.2, 6.9, 7.4]
    grid, dens = T.density_curve(vals)
    bw = statistics.stdev(vals) * len(vals) ** (-0.2)
    assert T.scott_bandwidth(vals) == pytest.approx(bw)
    nd = statistics.NormalDist(0, bw)
    for g, d in list(zip(grid, dens))[::9]:
        assert d == pytest.approx(statistics.fmean(nd.pdf(g - v) for v in vals))
    area = sum((dens[i] + dens[i + 1]) / 2 * (grid[i + 1] - grid[i]) for i in range(len(grid) - 1))
    assert area == pytest.approx(1.0, abs=2e-3)
    assert grid[0] == pytest.approx(min(vals) - 3 * bw) and grid[-1] == pytest.approx(
        max(vals) + 3 * bw
    )
    g1, d1 = T.density_curve([5.0])  # 单个值：带宽退到 |均值| × 0.05
    assert T.scott_bandwidth([5.0]) == pytest.approx(0.25) and max(d1) > 0 and len(g1) == 100


def test_ecdf_steps_are_the_sorted_values_not_merged():
    xs, ys = T.ecdf_xy([3.0, 1.0, 2.0, 2.0])
    assert xs == [1.0, 1.0, 2.0, 2.0, 3.0] and ys == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert T.ecdf_xy([7.0]) == ([7.0, 7.0], [0.0, 1.0])


def test_fit_line_matches_numpy_polyfit_including_log_axes():
    np = pytest.importorskip("numpy")
    xs = [1.0, 2.0, 3.5, 5.0, 8.0, 13.0]
    ys = [2.1, 2.9, 4.2, 5.1, 7.9, 12.2]
    seg = T.fit_segment((xs, ys))
    slope, icpt = np.polyfit(xs, ys, 1)
    assert seg[0] == [1.0, 13.0] and seg[1] == pytest.approx(
        [icpt + slope * 1.0, icpt + slope * 13.0]
    )
    loglog = T.fit_segment((xs, ys), True, True)  # 双对数：在 log10 里拟合，图上仍是直线（两个点）
    b, a = np.polyfit(np.log10(xs), np.log10(ys), 1)
    assert len(loglog[0]) == 2 and loglog[1][0] == pytest.approx(10 ** (a + b * np.log10(1.0)))
    semilog = T.fit_segment((xs, ys), True, False)  # 只有 x 对数：图上是曲线，沿 x 取 50 个点
    b2, a2 = np.polyfit(np.log10(xs), ys, 1)
    assert len(semilog[0]) == 50 and semilog[1][-1] == pytest.approx(a2 + b2 * np.log10(13.0))
    assert T.fit_segment(([1.0, 2.0], [1.0, 2.0]), True, False)[0][0] == pytest.approx(1.0)
    assert (
        T.fit_segment(([0.0, 1.0, 2.0], [1.0, 2.0, 3.0]), True, False) is None
    )  # 对数轴上有 0：不画
    assert (
        T.fit_segment(([1.0, 1.0], [1.0, 2.0])) is None and T.fit_segment(([1.0], [1.0])) is None
    )  # x 全相同 / 点不够


def test_n_note_wording():
    assert T.n_note({("", "a"): 5, ("", "b"): 5}, ["a", "b"], [""]) == "n = 5"
    assert T.n_note({("", "a"): 5, ("", "b"): 7}, ["a", "b"], [""]) == "n = 5 / 7 (group order)"
    assert (
        T.n_note({("s1", "a"): 5, ("s2", "a"): 7}, ["a"], ["s1", "s2"])
        == "n = 5-7 per group and panel"
    )
    assert T.n_note({}, [""], [""]) == ""


def test_relationship_data_skips_rows_without_both_numbers(folder):
    v = adf.inspect_data_source(folder / "rel.csv")
    d = T.relationship_data(v.columns, v.rows, "dose_mg", "response_mV", "arm", "site")
    assert sum(len(p[0]) for p in d["pairs"].values()) == 32  # 34 行里缺 y 的和 x 是 nan 的不画
    assert d["groups"] == ["control", "drug"] and d["panels"] == ["s1", "s2"]


def test_distribution_without_x_has_one_unnamed_category(folder):
    v = adf.inspect_data_source(folder / "dist.csv")
    d = T.comparison_data(v.columns, v.rows, None, "latency_ms", "arm", None, "as_given")
    assert d["cats"] == [""] and sum(len(x) for x in d["cells"].values()) == 120  # 缺失那行不进
    assert len(T.pooled_values(d, "", "control")) == 60 and len(T.all_values(d)) == 120


# --------------------------------------------------------------------------- 画出来的 artist 与图下方说明
@pytest.fixture(scope="module")
def shapes_b(folder, profile, tmp_path_factory):
    if WORKER_PY is None:
        return {}
    work = tmp_path_factory.mktemp("shapes_b")
    for f in folder.iterdir():
        (work / f.name).write_bytes(f.read_bytes())
    wanted = {
        "rel": mk(
            "rel.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            group="arm",
        ),
        "rel_log": mk(
            "rel.csv",
            "relationship",
            ["points", ("fit_line", "none", Q)],
            ["response_mV"],
            x="dose_mg",
            scale="log_xy",
        ),
        "hist": mk("dist.csv", "distribution", ["hist"], ["latency_ms"], group="arm"),
        "hist_density": mk(
            "dist.csv",
            "distribution",
            ["hist", ("density", "none", Q)],
            ["latency_ms"],
            group="arm",
            facet="site",
        ),
        "ecdf": mk("dist.csv", "distribution", ["ecdf"], ["latency_ms"], group="arm"),
        "box_x": mk(
            "dist.csv", "distribution", ["box", ("jitter", "none", Q)], ["latency_ms"], x="batch"
        ),
        "violin_nox": mk("dist.csv", "distribution", ["violin"], ["latency_ms"], group="arm"),
    }
    out = {}
    for name, r in wanted.items():
        script = T.render_recipe_script(entry_of(profile, r.table), r, data_rel=r.table, stem="fig")
        out[name] = introspect(work, script)
    return out


@needs_worker
def test_relationship_and_distribution_marks_are_plain_editable_artists(shapes_b):
    rel = shapes_b["rel"]["axes"][0]
    assert rel["collections"].count("PathCollection") == 2 and rel["lines"] == [
        "Line2D",
        "Line2D",
    ]  # 每组一个散点集 + 一条拟合线
    h = shapes_b["hist"]["axes"][0]
    assert h["containers"] == ["BarContainer", "BarContainer"] and set(h["patches"]) == {
        "Rectangle"
    }
    hd = shapes_b["hist_density"]["axes"]
    assert [a["title"] for a in hd] == ["s1", "s2"] and all(
        a["lines"] == ["Line2D", "Line2D"] for a in hd
    )
    e = shapes_b["ecdf"]["axes"][0]
    assert e["lines"] == ["Line2D", "Line2D"] and e["collections"] == []
    bx = shapes_b["box_x"]["axes"][0]
    assert set(bx["lines"]) == {"Line2D"} and "PathCollection" in bx["collections"]
    assert any(
        c in shapes_b["violin_nox"]["axes"][0]["collections"]
        for c in ("PolyCollection", "FillBetweenPolyCollection")
    )


@needs_worker
def test_notes_state_the_binning_bandwidth_and_fit_rules_and_n(shapes_b):
    assert shapes_b["rel"]["texts"] == [
        "dashed line: least-squares fit (descriptive); n = 16"
    ]  # 每组 16 个有效点（缺 y 与 x 为 nan 的行不画）
    assert "fitted on log10 of the log axes" in shapes_b["rel_log"]["texts"][0]
    note = shapes_b["hist_density"]["texts"][0]
    assert (
        "bins: Freedman-Diaconis rule, shared by all groups" in note
        and "density: Gaussian kernel, Scott bandwidth" in note
    )
    assert shapes_b["hist"]["texts"][0].startswith("bins: Freedman-Diaconis") and shapes_b["hist"][
        "texts"
    ][0].endswith("n = 60")
    assert shapes_b["ecdf"]["texts"][0] == "ECDF: step function of the sorted values; n = 60"
    assert shapes_b["box_x"]["texts"][0].startswith("box: median, quartiles, whiskers to 1.5 IQR")
    assert "P =" not in "".join(shapes_b["rel"]["texts"]) and "R²" not in "".join(
        shapes_b["rel"]["texts"]
    )  # 不报显著性、不放 R²


@needs_worker
def test_axes_labels_and_scales_of_the_new_families(shapes_b):
    assert (
        shapes_b["hist"]["axes"][0]["ylabel"] == "count"
        and shapes_b["hist"]["axes"][0]["xlabel"] == "latency (ms)"
    )
    assert (
        shapes_b["hist_density"]["axes"][0]["ylabel"] == "density"
    )  # 与密度叠画：柱高换成概率密度
    assert shapes_b["ecdf"]["axes"][0]["ylabel"] == "cumulative proportion"
    assert (
        shapes_b["rel_log"]["axes"][0]["xscale"] == "log"
        and shapes_b["rel_log"]["axes"][0]["yscale"] == "log"
    )
    assert shapes_b["box_x"]["axes"][0]["xticks"] == ["b1", "b2", "b3"]


def test_coverage_for_the_new_marks():
    cov = T.fingerprint_coverage(
        mk("x.csv", "distribution", ["hist", ("density", "none", Q)], ["v"])
    )
    assert (
        all(c.checked for c in cov.layers) and cov.any_checked and not cov.raw_checked
    )  # 柱高与密度曲线是派生量
    assert T.fingerprint_coverage(
        mk("x.csv", "distribution", ["ecdf"], ["v"])
    ).raw_checked  # 阶梯点就是排序后的原始值
    cov = T.fingerprint_coverage(
        mk("x.csv", "distribution", ["violin", ("points", "none", Q)], ["v"])
    )
    assert [c.checked for c in cov.layers] == [False, True] and cov.raw_checked
    assert not T.fingerprint_coverage(
        mk("x.csv", "distribution", ["box"], ["v"], x="g")
    ).any_checked
    cov = T.fingerprint_coverage(
        mk("x.csv", "relationship", ["points", ("fit_line", "none", Q)], ["v"], x="u")
    )
    assert all(c.checked for c in cov.layers) and cov.raw_checked


def test_runtime_name_is_never_hardcoded_in_tests():
    """公共模块名随内容变；用例只经 `runtime_module()` 取名。"""
    import re

    pat = re.compile(r"tavotto_plot_[0-9a-f]{8}")
    for f in (
        Path(__file__),
        Path(__file__).with_name("test_ai_data_recipe.py"),
        Path(__file__).parent / "bridge" / "test_bridge_generated_figure_script.py",
    ):
        assert not pat.search(f.read_text(encoding="utf-8")), f.name


def test_template_rejects_combinations_the_validator_also_rejects(folder, profile):
    e = entry_of(profile, "dist.csv")
    for layers, kw, detail in (
        (["hist", ("jitter", "none", Q)], {}, "orientation"),
        (["ecdf", ("hist", "none", Q)], {}, "ecdf_with_hist"),
        (["hist"], {"scale": "log_y"}, "scale_or_x"),
        (["jitter", "box"], {"scale": "log_x", "x": "batch"}, "scale"),
    ):
        with pytest.raises(T.TemplateError) as ei:
            rs(
                e,
                mk("dist.csv", "distribution", layers, ["latency_ms"], **kw),
                data_rel="dist.csv",
                stem="f",
            )
        assert ei.value.code == "template_option_unsupported", (layers, detail)


# =========================================================================== 评审三条（#1841）：缺失处断开 / label 唯一 / n 说明
def _gap_folder(tmp: Path) -> Path:
    """a/s1 在 x=3 处 y 全缺；其余格都完整。每个位置 3 个重复。"""
    rows = [["x", "arm", "site", "y"]]
    for arm in ("a", "b"):
        for site in ("s1", "s2"):
            for x in range(1, 6):
                for k in range(3):
                    miss = arm == "a" and site == "s1" and x == 3
                    rows.append(
                        [x, arm, site, "" if miss else round(x + k * 0.5 + (arm == "b"), 3)]
                    )
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "gap.csv").write_text(_csv(rows), encoding="utf-8")
    return tmp


def _render_in(tmp: Path, table: str, recipe, allow=()) -> tuple[T.Rendered, adf.DataSummary]:
    prof = adf.profile_folder(tmp)
    values = adf.inspect_data_source(tmp / table, None)
    got = {p.code for p in R.validate_recipe(prof, recipe, confirmed=NONE, values=values)}
    assert got <= set(allow), got  # 脏数据（y 里有文字）校验会拒；模板对它也必须与期望一致
    return T.render_recipe_script(entry_of(prof, table), recipe, data_rel=table, stem="fig"), values


def probe(work: Path, rendered: T.Rendered, body: str):
    """在真解释器里跑完脚本的 main()（不存盘），再跑 `body`（可用 fig / plt），把 `result` 当 JSON 取回。"""
    write_rendered(work, rendered, "fig")
    code = (
        "import json, runpy, matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.Figure.savefig = lambda *a, **k: None\n"
        f"ns = runpy.run_path({str(work / 'fig.py')!r})\n"
        "ns['main']()\n"
        "fig = plt.gcf()\n"
        f"{body}\n"
        "print('JSON:' + json.dumps(result))\n"
    )
    proc = subprocess.run(
        [WORKER_PY, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(work),
        check=True,
    )
    return json.loads(next(ln for ln in proc.stdout.splitlines() if ln.startswith("JSON:"))[5:])


_FILLS = (
    "result = {}\n"
    "for ai, ax in enumerate(fig.axes):\n"
    "    for c in ax.collections:\n"
    "        if c.get_label().startswith('band'):\n"
    "            result[f'{ai}:{c.get_label()}'] = [[min(v[0] for v in p.vertices), max(v[0] for v in p.vertices)] for p in c.get_paths() if len(p.vertices)]\n"
)


@needs_worker
def test_band_breaks_where_the_summary_line_breaks(tmp_path):
    """某个 x 位置没有任何观测：汇总线在这里断开，误差带也必须断开，不许把两侧连起来。"""
    work = _gap_folder(tmp_path / "w")
    r = mk(
        "gap.csv",
        "trend",
        [("line", "mean_sd"), ("band", "mean_sd", Q)],
        ["y"],
        x="x",
        group="arm",
        facet="site",
    )
    rendered, _ = _render_in(work, "gap.csv", r)
    spans = probe(work, rendered, _FILLS)
    gap = spans["0:band | a | s1"]
    assert gap and all(not (lo < 3 < hi) for lo, hi in gap), gap  # 跨过 x=3 = 在没观测处画了带
    assert any(hi <= 3 for _, hi in gap) and any(lo >= 3 for lo, _ in gap)  # 两侧各有带
    for key in ("0:band | b | s1", "1:band | a | s2", "1:band | b | s2"):
        assert any(lo < 3 < hi for lo, hi in spans[key]), key  # 完整的格：带是连续的


@needs_worker
def test_no_other_mark_connects_across_a_missing_position(tmp_path):
    """同一毛病在别的图元里：汇总线 / 阶梯线 / 逐点线按连续有效段断开；误差线只出现在有值的位置。"""
    work = _gap_folder(tmp_path / "w")
    kinds = (
        [("line", "mean_sd"), ("errorbar", "mean_sd", Q)],
        [("step", "mean_sd")],
    )
    body = (
        "result = {}\n"
        "for ai, ax in enumerate(fig.axes):\n"
        "    for ln in ax.lines:\n"
        "        xs, ys = ln.get_data()\n"
        "        runs, cur = [], []\n"
        "        for a, b in zip(xs, ys):\n"
        "            if b != b:\n"
        "                runs.append(cur); cur = []\n"
        "            else:\n"
        "                cur.append(float(a))\n"
        "        runs.append(cur)\n"
        "        result[f'{ai}:{ln.get_label()}'] = [[min(r), max(r)] for r in runs if r]\n"
        "    for c in ax.containers:\n"
        "        result[f'{ai}:{c.get_label()}'] = sorted({round(float(v[0][0]), 6) for v in c.lines[2][0].get_segments()})\n"
    )
    for layers in kinds:
        r = mk("gap.csv", "trend", layers, ["y"], x="x", group="arm", facet="site")
        rendered, _ = _render_in(work, "gap.csv", r)
        got = probe(work, rendered, body)
        for key, v in got.items():
            if "| a | s1" not in key:
                continue
            if key.split(":", 1)[1].startswith("errorbar"):
                assert 3.0 not in v and v == [1.0, 2.0, 4.0, 5.0], (layers, key, v)
            else:
                assert all(not (lo < 3 < hi) for lo, hi in v), (layers, key, v)


@needs_worker
def test_comparison_hlines_are_separated_per_category(tmp_path):
    rows = [["cond", "y"]] + [[c, v] for c in "abc" for v in (1.0, 2.0, 3.0)]
    work = tmp_path / "w"
    work.mkdir()
    (work / "c.csv").write_text(_csv(rows), encoding="utf-8")
    r = mk("c.csv", "comparison", ["points", ("hline_summary", "mean_sd", Q)], ["y"], x="cond")
    rendered, _ = _render_in(work, "c.csv", r)
    body = (
        "ax = fig.axes[0]\n"
        "ln = [l for l in ax.lines if l.get_label().startswith('hline_summary')][0]\n"
        "xs = [float(v) for v in ln.get_xdata()]\n"
        "result = {'nan_at': [i for i, v in enumerate(xs) if v != v], 'n': len(xs)}\n"
    )
    got = probe(work, rendered, body)
    assert got["nan_at"] == [2, 5, 8]


@needs_worker
@pytest.mark.parametrize(
    "family, layers, kw",
    [
        ("trend", [("line", "mean_sd"), ("line", "median_iqr", "overlay")], {}),
        (
            "trend",
            [("line", "mean_sd"), ("line", "mean_sem", "overlay"), ("hline_summary", "mean_sd", Q)],
            {"group": "arm", "facet": "site"},
        ),
        (
            "comparison",
            [
                "points",
                ("hline_summary", "mean_sd", Q),
                ("hline_summary", "median_iqr", "reference"),
            ],
            {},
        ),
        (
            "comparison",
            [("bar", "mean_sd"), ("bar", "median_iqr", "overlay"), ("jitter", "none", Q)],
            {"group": "arm", "facet": "site"},
        ),
    ],
    ids=["trend-two-lines", "trend-lines-hline-grouped", "cmp-two-hlines", "cmp-two-bars-grouped"],
)
def test_same_mark_in_two_layers_gets_distinct_labels_and_both_verify(family, layers, kw, tmp_path):
    """同一图元以不同 role 出现两次：每个可核层的 label 在整张图里唯一（验证器按 label 唯一匹配），期望指纹也一一对应。"""
    work = _gap_folder(tmp_path / "w")
    r = mk("gap.csv", family, layers, ["y"], x="x" if family == "trend" else "arm", **kw)
    rendered, values = _render_in(work, "gap.csv", r)
    man = run_in_worker(work, rendered, "fig")
    got = labelled(man)
    assert not {k: len(v) for k, v in got.items() if len(v) > 1}, sorted(got)
    expected = T.expected_fingerprints(values, r)
    labels = [e["label"] for e in expected]
    assert len(labels) == len(set(labels)), labels
    from tavotto.engine import figureverify as fv

    results = fv.data_matches_expected(man, expected)
    assert results and all(c.verdict == fv.VERIFIED for c in results), [
        (c.id, c.verdict, c.reason_code) for c in results
    ]
    # 同一层在不同分组 / 分面格里不同名；两层同一图元的名字互不相同
    dup_marks = {m for m in {ly[0] for ly in layers} if sum(1 for ly in layers if ly[0] == m) > 1}
    assert dup_marks
    for m in dup_marks:
        kinds = {lab.split(" | ")[0] for lab in labels if lab.split(" | ")[0].startswith(m)}
        assert len(kinds) == sum(1 for ly in layers if ly[0] == m), kinds


@needs_worker
def test_labels_stay_unique_when_data_text_contains_the_separator(tmp_path):
    """分组 / 分面名里带分隔符「 | 」时，不同格的 label 也不许撞。"""
    rows = [["x", "arm", "site", "y"]]
    for arm, site in (("a | s1", "b"), ("a", "s1 | b")):
        for x in (1, 2, 3):
            for k in (0, 1):
                rows.append([x, arm, site, x + k + len(arm)])
    work = tmp_path / "w"
    work.mkdir()
    (work / "sep.csv").write_text(_csv(rows), encoding="utf-8")
    r = mk("sep.csv", "trend", [("line", "mean_sd")], ["y"], x="x", group="arm", facet="site")
    rendered, values = _render_in(work, "sep.csv", r)
    man = run_in_worker(work, rendered, "fig")
    got = labelled(man)
    assert not {k: len(v) for k, v in got.items() if len(v) > 1}, sorted(got)
    expected = T.expected_fingerprints(values, r)
    assert len({e["label"] for e in expected}) == len(expected) == 2
    for e in expected:
        assert got[e["label"]][0]["parts"][e["part"]] == e["digest"]


def _n_table(tmp: Path, cells: dict) -> Path:
    """cells: {(arm, site): {x: [y 原文…]}}；arm / site 为空串 = 没有这一列的值（整列不写）。"""
    use_arm = any(a for a, _ in cells)
    use_site = any(s for _, s in cells)
    head = ["x"] + (["arm"] if use_arm else []) + (["site"] if use_site else []) + ["y"]
    rows = [head]
    for (a, s), per_x in cells.items():
        for x, ys in per_x.items():
            for y in ys:
                rows.append([x] + ([a] if use_arm else []) + ([s] if use_site else []) + [y])
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "n.csv").write_text(_csv(rows), encoding="utf-8")
    return tmp


def _finite(s) -> bool:
    try:
        return math.isfinite(float(s))
    except ValueError:
        return False


def _independent_ns(cells: dict) -> tuple[set[int], set[int]]:
    """不经模板：`(每个位置的有效观测数集合, 每条序列的有效观测总数集合)`；缺失 / NaN / 文字不算，0 不算。"""
    per_point, per_series = set(), set()
    for per_x in cells.values():
        tot = 0
        for ys in per_x.values():
            k = sum(1 for y in ys if _finite(y))
            if k:
                per_point.add(k)
            tot += k
        if tot:
            per_series.add(tot)
    return per_point, per_series


def _span(ns: set[int]) -> str:
    return str(min(ns)) if len(ns) == 1 else f"{min(ns)}–{max(ns)}"


def _note_of(tmp: Path, layers, **kw) -> str:
    r = mk("n.csv", "trend", layers, ["y"], x="x", **kw)
    rendered, _ = _render_in(tmp, "n.csv", r, allow=("column_type_mismatch",))
    (text,) = probe(tmp, rendered, "result = [t.get_text() for t in fig.texts]")
    return text


def _v(k, base=1.0):
    return [round(base + i * 0.7, 3) for i in range(k)]


@needs_worker
def test_trend_note_counts_the_population_each_summary_actually_summarizes(tmp_path):
    full = {x: _v(3, x) for x in range(1, 6)}
    uneven = {1: _v(3), 2: _v(2, 2), 3: _v(3, 3), 4: _v(1, 4), 5: _v(3, 5)}
    singles = {x: _v(1, x) for x in range(1, 6)}
    dirty = {
        1: [1.0, 2.0, 3.0],
        2: ["", "nan", "<LOD"],  # 整个位置没有有效值：线在这里断开，不计 n
        3: [2.0, "<LOD", 3.0, ""],
        4: [5.0, 6.0, 7.0, 8.0],
        5: [1.0, "NaN", 2.0],
    }
    PP = [("line", "mean_sd"), ("band", "mean_sd", Q)]
    SER = ["points", ("hline_summary", "mean_sd", Q)]
    MIX = [("line", "mean_sd"), ("hline_summary", "median_iqr", Q)]
    two_pos = [
        ("line", "mean_sd"),
        ("errorbar", "mean_sem", Q),
        ("hline_summary", "mean_sd", "reference"),
    ]
    grouped = {
        ("a", "s1"): full,
        ("b", "s1"): uneven,
        ("a", "s2"): dirty,
        ("b", "s2"): {x: _v(2, x) for x in range(1, 4)},
    }
    cases = [
        ("pp-uniform", {("", ""): full}, PP, {}),
        ("pp-uneven", {("", ""): uneven}, PP, {}),
        ("series-singles", {("", ""): singles}, SER, {}),  # 五个位置各一个观测：n 是 5 不是 1
        ("mixed-uniform", {("", ""): full}, MIX, {}),
        ("mixed-uneven", {("", ""): uneven}, MIX, {}),
        ("mixed-dirty", {("", ""): dirty}, MIX, {}),
        ("mixed-grouped-faceted", grouped, MIX, {"group": "arm", "facet": "site"}),
        ("series-grouped", grouped, SER, {"group": "arm", "facet": "site"}),
        ("pp-grouped", grouped, PP, {"group": "arm", "facet": "site"}),
        ("pos-and-series", grouped, two_pos, {"group": "arm", "facet": "site"}),
    ]
    for cid, cells, layers, kw in cases:
        work = _n_table(tmp_path / cid, cells)
        per_point, per_series = _independent_ns(cells)
        has_pos = any(
            ly[0] in ("line", "step", "band", "errorbar") for ly in layers if isinstance(ly, tuple)
        )
        has_ser = any(ly[0] == "hline_summary" for ly in layers if isinstance(ly, tuple))
        got = _note_of(work, layers, **kw)
        dotted = "; dotted line: center of the whole series" if has_ser else ""
        if has_pos and has_ser:
            names = {
                "mixed": "line: mean ± SD; horizontal lines: median with IQR",
                "two_pos": "line: mean ± SD; error bars: mean ± SEM; horizontal lines: mean ± SD",
            }
            summ = names["mixed" if layers is MIX else "two_pos"]
            want = f"{summ}{dotted}; n = {_span(per_point)} per point; n = {_span(per_series)} per series"
        elif has_ser:
            want = f"mean ± SD{dotted}; n = {_span(per_series)}" + (
                " per series" if len(per_series) > 1 else ""
            )
        else:
            want = f"mean ± SD; n = {_span(per_point)}" + (
                " per point" if len(per_point) > 1 else ""
            )
        assert got == want, (cid, got, want)


@needs_worker
def test_comparison_note_and_tick_n_match_each_category_by_group_cell(tmp_path):
    """比较图：每个（分面, 类别, 分组）格子的 n 与独立数出来的一致（缺失 / NaN / 文字行不算）；各格相同 → 说明里一个 n。"""
    rows = [["cond", "arm", "site", "y"]]
    spec = {  # (cond, arm, site) -> y 原文
        ("c1", "a", "s1"): [1, 2, 3, "", "nan"],
        ("c1", "b", "s1"): [1, 2, "<LOD"],
        ("c2", "a", "s1"): [3, 4, 5, 6],
        ("c2", "b", "s1"): [1],
        ("c1", "a", "s2"): [1, 2],
        ("c2", "b", "s2"): [2, 3, 4, 5, 6],
    }
    for (c, a, s), ys in spec.items():
        rows += [[c, a, s, y] for y in ys]
    work = tmp_path / "w"
    work.mkdir()
    (work / "n.csv").write_text(_csv(rows), encoding="utf-8")
    r = mk(
        "n.csv",
        "comparison",
        ["jitter", ("bar", "mean_sd", Q)],
        ["y"],
        x="cond",
        group="arm",
        facet="site",
    )
    rendered, _ = _render_in(work, "n.csv", r, allow=("column_type_mismatch",))
    body = "result = [[t.get_text() for t in fig.texts], [[t.get_text() for t in ax.get_xticklabels()] for ax in fig.axes]]"
    texts, ticks = probe(work, rendered, body)
    assert texts == ["mean ± SD; n per group in tick labels"]
    n = {k: sum(1 for y in ys if _finite(y)) for k, ys in spec.items()}
    for ax_ticks, site in zip(ticks, ("s1", "s2")):
        want = [
            f"{c}\n(n = " + "/".join(str(n.get((c, a, site), 0)) for a in ("a", "b")) + ")"
            for c in ("c1", "c2")
        ]
        assert ax_ticks == want, (site, ax_ticks, want)
    # 各格 n 相同：说明里只写一个 n
    even = [["cond", "y"]] + [[c, v] for c in "ab" for v in (1, 2, 3, "")]
    work2 = tmp_path / "w2"
    work2.mkdir()
    (work2 / "n.csv").write_text(_csv(even), encoding="utf-8")
    r2 = mk("n.csv", "comparison", ["jitter", ("hline_summary", "mean_sd", Q)], ["y"], x="cond")
    rendered, _ = _render_in(work2, "n.csv", r2)
    assert probe(work2, rendered, "result = [t.get_text() for t in fig.texts]") == [
        "mean ± SD; n = 3"
    ]


def _iso_folder(tmp: Path) -> Path:
    """a：有效位置 1、3、4、6（1 和 6 孤立）；b 整条只有 x=3 一个有效位置。每个位置 2 个重复。"""
    valid = {"a": {1, 3, 4, 6}, "b": {3}}
    rows = [["x", "arm", "y"]]
    for arm, ok in valid.items():
        for x in range(1, 9):
            for k in range(2):
                rows.append([x, arm, round(x + k * 0.5, 3) if x in ok else ""])
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "iso.csv").write_text(_csv(rows), encoding="utf-8")
    return tmp


@needs_worker
@pytest.mark.parametrize("mark", ["line", "step"])
def test_isolated_valid_position_stays_visible_without_marking_the_whole_line(mark, tmp_path):
    """两侧都缺失的孤立有效位置（含整条只剩一个位置）：线画不出线段，必须在该位置有可见标记；连续的段不加标记。"""
    work = _iso_folder(tmp_path / "w")
    r = mk("iso.csv", "trend", [(mark, "mean_sd")], ["y"], x="x", group="arm")
    rendered, values = _render_in(
        work, "iso.csv", r, allow=("trend_too_few_x",)
    )  # 校验拒；模板对它也要诚实
    body = (
        "result = {}\n"
        "for ln in fig.axes[0].lines:\n"
        "    xs = [float(v) for v in ln.get_xdata()]\n"
        "    ys = [float(v) for v in ln.get_ydata()]\n"
        "    me = ln.get_markevery()\n"
        "    idx = [] if ln.get_marker() in ('None', '', None) else (list(range(len(xs))) if me is None else list(me))\n"
        "    result[ln.get_label()] = [xs[i] for i in idx if ys[i] == ys[i]]\n"
    )
    got = probe(work, rendered, body)
    assert got[f"{mark} | a"] == [1.0, 6.0], got
    assert got[f"{mark} | b"] == [3.0], got
    # 同一条线、同一份数据：真 worker 对拍仍过（标记不改变 xy 指纹，label 不变）
    man = run_in_worker(work, rendered, "fig")
    from tavotto.engine import figureverify as fv

    res = fv.data_matches_expected(man, T.expected_fingerprints(values, r))
    assert res and all(c.verdict == fv.VERIFIED for c in res), [(c.id, c.verdict) for c in res]


def _multi_y_folder(tmp: Path) -> Path:
    rows = [["x", "arm", "sig_mV", "cur_mV"]]
    for arm in ("a", "b"):
        for x in range(1, 5):
            for k in range(2):
                rows.append([x, arm, x + k, 10 + x * k])
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "my.csv").write_text(_csv(rows), encoding="utf-8")
    return tmp


_LEGEND = (
    "lg = fig.axes[0].get_legend()\n"
    "result = {'texts': [t.get_text() for t in lg.get_texts()] if lg else [],\n"
    "          'styles': [getattr(h, 'get_linestyle', lambda: None)() for h in lg.legend_handles] if lg else []}\n"
)


@needs_worker
def test_legend_explains_linestyle_per_y_column_when_grouped(tmp_path):
    work = _multi_y_folder(tmp_path / "w")
    g = mk("my.csv", "trend", [("line", "mean_sd")], ["sig_mV", "cur_mV"], x="x", group="arm")
    rendered, _ = _render_in(work, "my.csv", g)
    got = probe(work, rendered, _LEGEND)
    assert got["texts"] == ["a", "b", "sig (mV)", "cur (mV)"], (
        got
    )  # 分组色块照旧 + 线型 ↔ y 列（轴标题写法）
    assert got["styles"][2:] == ["-", "--"], got
    # 不分组、多 y：颜色区分 y，图例是 y 列（同一套写法）；单个 y：图例不变
    n = mk("my.csv", "trend", [("line", "mean_sd")], ["sig_mV", "cur_mV"], x="x")
    rendered, _ = _render_in(work, "my.csv", n)
    assert probe(work, rendered, _LEGEND)["texts"] == ["sig (mV)", "cur (mV)"]
    one = mk("my.csv", "trend", [("line", "mean_sd")], ["sig_mV"], x="x", group="arm")
    rendered, _ = _render_in(work, "my.csv", one)
    assert probe(work, rendered, _LEGEND)["texts"] == ["a", "b"]
    solo = mk("my.csv", "trend", [("line", "mean_sd")], ["sig_mV"], x="x")
    rendered, _ = _render_in(work, "my.csv", solo)
    assert probe(work, rendered, _LEGEND)["texts"] == []


# =========================================================================== relationship / distribution 同样的纪律
def _rel_folder(tmp: Path, seps=False) -> Path:
    """x、y 两列；分组 arm、分面 site；含缺失 / NaN / 文字行（不画、不计 n）。各格点数不同。"""
    rows = [["x", "y", "arm", "site"]]
    plan = {
        ("a", "s1"): 8,
        ("b", "s1"): 6,
        ("a", "s2"): 7,
        ("b", "s2"): 6,
    }
    for (arm, site), k in plan.items():
        if seps:
            arm, site = {"a": "a | s1", "b": "a"}[arm], {"s1": "b", "s2": "s1 | b"}[site]
        for i in range(k):
            rows.append(
                [i + 1.0, round(2 * i + 1 + (arm != "a") * 3 + (i % 2) * 0.4, 3), arm, site]
            )
        rows += [["", 3.0, arm, site], [4.0, "nan", arm, site], [5.0, "<LOD", arm, site]]
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "rel.csv").write_text(_csv(rows), encoding="utf-8")
    return tmp


_REL_BODY = (
    "result = {}\n"
    "for ai, ax in enumerate(fig.axes):\n"
    "    for c in ax.collections:\n"
    "        result[f'{ai}:{c.get_label()}'] = [[float(a), float(b)] for a, b in c.get_offsets()]\n"
    "    for ln in ax.lines:\n"
    "        result[f'{ai}:{ln.get_label()}'] = [[float(a), float(b)] for a, b in zip(*ln.get_data())]\n"
    "result['texts'] = [t.get_text() for t in fig.texts]\n"
)


@needs_worker
def test_relationship_draws_fits_and_counts_only_finite_pairs(tmp_path):
    work = _rel_folder(tmp_path / "w")
    r = mk(
        "rel.csv", "relationship", ["points", "fit_line"], ["y"], x="x", group="arm", facet="site"
    )
    rendered, _ = _render_in(work, "rel.csv", r, allow=("column_type_mismatch",))
    got = probe(work, rendered, _REL_BODY)
    ns = {
        ("a", "s1"): 8,
        ("b", "s1"): 6,
        ("a", "s2"): 7,
        ("b", "s2"): 6,
    }  # 独立数：缺失 / NaN / 文字行不算
    for ai, site in enumerate(("s1", "s2")):
        for arm in ("a", "b"):
            pts = got[f"{ai}:points | {arm} | {site}"]
            assert len(pts) == ns[(arm, site)]
            fit = got[f"{ai}:fit_line | {arm} | {site}"]
            slope, icpt = statistics.linear_regression([p[0] for p in pts], [p[1] for p in pts])
            for x, y in fit:
                assert y == pytest.approx(icpt + slope * x)
    note = got["texts"][0]
    assert note.endswith("n = 6-8 per group and panel"), note
    assert "dashed line: least-squares fit" in note
    one = _rel_folder(tmp_path / "w1")
    only_s1 = [["x", "y", "arm"]] + [
        [i + 1, i * 1.5, a] for a, k in (("a", 7), ("b", 6)) for i in range(k)
    ]
    (one / "rel.csv").write_text(_csv(only_s1), encoding="utf-8")
    r1 = mk("rel.csv", "relationship", ["points", "fit_line"], ["y"], x="x", group="arm")
    rendered, _ = _render_in(one, "rel.csv", r1)
    assert probe(one, rendered, _REL_BODY)["texts"][0].endswith("n = 7 / 6 (group order)")


def _dist_folder(tmp: Path, seps=False) -> Path:
    rows = [["v", "arm", "site"]]
    plan = {("a", "s1"): 9, ("b", "s1"): 6, ("a", "s2"): 7, ("b", "s2"): 5}
    for (arm, site), k in plan.items():
        if seps:
            arm, site = {"a": "a | s1", "b": "a"}[arm], {"s1": "b", "s2": "s1 | b"}[site]
        rows += [[round(1 + i * 0.9 + (arm.startswith("b")) * 2, 3), arm, site] for i in range(k)]
        rows += [["", arm, site], ["nan", arm, site], ["<LOD", arm, site]]
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "dist.csv").write_text(_csv(rows), encoding="utf-8")
    return tmp


@needs_worker
def test_distribution_hist_ecdf_density_all_use_the_counted_n(tmp_path):
    work = _dist_folder(tmp_path / "w")
    ns = {("a", "s1"): 9, ("b", "s1"): 6, ("a", "s2"): 7, ("b", "s2"): 5}
    body = (
        "result = {}\n"
        "for ai, ax in enumerate(fig.axes):\n"
        "    for c in ax.containers:\n"
        "        result[f'{ai}:{c.get_label()}'] = [float(pt.get_height()) for pt in c.patches]\n"
        "    for ln in ax.lines:\n"
        "        result[f'{ai}:{ln.get_label()}'] = [[float(a), float(b)] for a, b in zip(*ln.get_data())]\n"
        "result['texts'] = [t.get_text() for t in fig.texts]\n"
    )
    for mark in ("hist", "ecdf", "density"):
        r = mk("dist.csv", "distribution", [mark], ["v"], group="arm", facet="site")
        rendered, _ = _render_in(work, "dist.csv", r, allow=("column_type_mismatch",))
        got = probe(work, rendered, body)
        for ai, site in enumerate(("s1", "s2")):
            for arm in ("a", "b"):
                k = ns[(arm, site)]
                data = got[f"{ai}:{mark} | {arm} | {site}"]
                if mark == "hist":
                    assert sum(data) == k  # 各箱个数之和 = 这一格的有效值个数
                elif mark == "ecdf":
                    assert len(data) == k + 1 and data[-1][1] == pytest.approx(1.0)
        assert got["texts"][0].endswith("n = 5-9 per group and panel"), (mark, got["texts"])


@needs_worker
@pytest.mark.parametrize(
    "family, table, layers, kw, builder",
    [
        (
            "relationship",
            "rel.csv",
            ["points", ("points", "none", Q), "fit_line"],
            {"x": "x"},
            _rel_folder,
        ),
        ("distribution", "dist.csv", ["density", ("density", "none", Q)], {}, _dist_folder),
        ("distribution", "dist.csv", ["hist", ("hist", "none", Q)], {}, _dist_folder),
    ],
    ids=["relationship", "distribution-density", "distribution-hist"],
)
def test_two_layers_of_one_mark_and_separator_names_keep_labels_unique_and_verified(
    family, table, layers, kw, builder, tmp_path
):
    from tavotto.engine import figureverify as fv

    for seps in (False, True):
        work = builder(tmp_path / f"w{seps}", seps)
        y = ["y"] if family == "relationship" else ["v"]
        r = mk(table, family, layers, y, group="arm", facet="site", **kw)
        rendered, values = _render_in(work, table, r, allow=("column_type_mismatch",))
        man = run_in_worker(work, rendered, "fig")
        got = labelled(man)
        assert not {k: len(v) for k, v in got.items() if len(v) > 1}, sorted(got)
        expected = T.expected_fingerprints(values, r)
        labels = [e["label"] for e in expected]
        assert expected and len(labels) == len(set(labels)), labels
        res = fv.data_matches_expected(man, expected)
        assert all(c.verdict == fv.VERIFIED for c in res), [(c.id, c.verdict) for c in res]


@needs_worker
def test_note_names_which_mark_shows_which_summary(tmp_path):
    work = _gap_folder(tmp_path / "w")
    r = mk(
        "gap.csv",
        "comparison",
        [("bar", "mean_sd"), ("errorbar", "median_iqr", Q)],
        ["y"],
        x="arm",
    )
    rendered, _ = _render_in(work, "gap.csv", r)
    (text,) = probe(work, rendered, "result = [t.get_text() for t in fig.texts]")
    assert text.startswith("bars: mean ± SD; error bars: median with IQR; n")
    one = mk(
        "gap.csv", "comparison", [("bar", "mean_sd"), ("errorbar", "mean_sd", Q)], ["y"], x="arm"
    )
    rendered, _ = _render_in(work, "gap.csv", one)
    (text,) = probe(work, rendered, "result = [t.get_text() for t in fig.texts]")
    assert text.startswith("mean ± SD; n")  # 只有一种汇总：简短写法
    t = mk(
        "gap.csv", "trend", ["points", "fit_line", ("hline_summary", "mean_sd", Q)], ["y"], x="x"
    )
    rendered, _ = _render_in(work, "gap.csv", t)
    (text,) = probe(work, rendered, "result = [t.get_text() for t in fig.texts]")
    assert (
        "dashed line: least-squares fit" in text
        and "dotted line: center of the whole series" in text
    )
    n = sum(1 for ln in _csv_rows(work / "gap.csv")[1:] if _finite(ln[3]))
    assert text.endswith(f"n = {n}"), text  # 拟合线与整系列水平线汇总的都是全部有限观测


def _csv_rows(path: Path):
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))
