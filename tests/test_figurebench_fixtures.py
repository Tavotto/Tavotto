"""Tavotto-FigureBench 夹具的真值、参考解与反例测试（`tests/fixtures/figurebench/`，TAF Phase 0）。

主语说清：这些用例证明的是**基准本身可信**，不是 TAF 或改图助手能用——那两件事
在 Phase 2 起由 Figure Verifier 取得资格，Phase 0 一律 `not_run`。

* **输入真值**——CSV 与夹具脚本里写的数字与手写的 `truth.json` 一致。全部用标准库解析，
  **不 import 任何产品模块**：基准的真值不能由被测产品来背书。
* **场景可解且不是恒真**（`check == "probe"` 的改图场景）——在夹具的临时副本里跑三类变体，
  由 `_probe.py` 在装了 matplotlib 的解释器里读出客观事实：
  - 基线：所有「保持」断言对自身成立；至少一条「目标」断言**不**成立（否则场景不考任何东西）；
  - 参考解：所有「目标」与「保持」断言都成立（场景可解，断言没写反）；
  - 每个反例：它点名的 `must_fail` 断言**全部**不成立（门禁不是空转）。
  每条编辑先验落点：`find` 在源码里出现的次数必须正好是 `count`，再看红不红。
* **夹具不被污染**——执行前后夹具目录的文件快照逐字节相同。

解释器只借用 `pool.find_worker_python()` 找一个装了 matplotlib 的（与
`test_foundation_fixtures.py` 同一个借法；它不是被测对象），找不到就 skip——skip 是 `not_run`，不是绿。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import struct
from pathlib import Path

import pytest

from support.figurebench import (
    FIGURE_FIXTURES,
    ROOT,
    SCENARIO_FILES,
    all_scenarios,
    probe_scenarios,
    run_probe,
    stage,
    variants,
)

CATEGORIES = {"single_element", "complex_figure", "scientific", "data_to_figure", "reliability"}
TASK_TYPES = {"figure_edit", "data_to_figure", "infrastructure"}
CHECKS = {"probe", "policy", "input_truth", "covered_by", "gap"}
OUTCOMES = {"completed", "partial", "needs_input", "failed", None}
WHICH = {"title", "xlabel", "ylabel"}


def _load(split: str) -> dict:
    return json.loads(SCENARIO_FILES[split].read_text(encoding="utf-8"))


_all_scenarios = all_scenarios
_probe_scenarios = probe_scenarios


def _digest(values: list, shape: tuple[int, ...]) -> str:
    """`_probe.py::digest` / `engine/manifest.py::canonical_digest` 的标准库镜像：形状 + 小端 float64 字节的 sha256，
    NaN 统一成 Python `float("nan")` 的位型（与 numpy 的 `np.nan` 相同）。"""
    vals = [math.nan if math.isnan(float(v)) else float(v) for v in values]
    head = ("x".join(str(n) for n in shape) + ":").encode()
    return hashlib.sha256(head + struct.pack(f"<{len(vals)}d", *vals)).hexdigest()


def _xy_digest(xs: list, ys: list) -> str:
    return _digest([v for pair in zip(xs, ys) for v in pair], (len(xs), 2))


def _snapshot(base: Path) -> dict[str, str]:
    return {
        p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


# --------------------------------------------------------------------------- 断言求值
def _axes(facts: dict, i: int) -> dict:
    return facts["axes"][i]


def _series(facts: dict, a: dict) -> dict:
    hits = [ln for ln in _axes(facts, a["axes"])["lines"] if ln["label"] == a["series"]]
    if len(hits) != 1:
        raise LookupError(a["series"])
    return hits[0]


def _close(x, y, tol: float = 1e-6) -> bool:
    return abs(float(x) - float(y)) <= tol


def _holds(a: dict, cur: dict, base: dict) -> bool:
    """一条断言在 `cur`（变体的事实）上成立吗；`base` 是同一夹具基线的事实。

    找不到断言指名的对象（曲线被删、子图没了）一律算不成立——「看不见」不是「没变」。"""
    k = a["kind"]
    try:
        if k == "line_property":
            v = _series(cur, a)[a["prop"]]
            return (
                _close(v, a["expected"])
                if isinstance(a["expected"], (int, float))
                else v == a["expected"]
            )
        if k == "line_property_unchanged":
            return _series(cur, a)[a["prop"]] == _series(base, a)[a["prop"]]
        if k == "series_data_unchanged":
            return _series(cur, a)["data"] == _series(base, a)["data"]
        if k == "series_count_unchanged":
            return len(_axes(cur, a["axes"])["lines"]) == len(_axes(base, a["axes"])["lines"])
        if k in (
            "text_equals",
            "text_contains",
            "text_unchanged",
            "fontsize",
            "fontsize_unchanged",
        ):
            got = _axes(cur, a["axes"])[a["which"]]
            if k == "text_equals":
                return got["text"] == a["expected"]
            if k == "text_contains":
                return a["expected"] in got["text"]
            if k == "text_unchanged":
                return got["text"] == _axes(base, a["axes"])[a["which"]]["text"]
            if k == "fontsize":
                return _close(got["fontsize"], a["expected"])
            return _close(got["fontsize"], _axes(base, a["axes"])[a["which"]]["fontsize"])
        if k == "legend_loc":
            lg = _axes(cur, a["axes"])["legend"]
            return lg is not None and lg["loc"] == a["expected"]
        if k == "axis_scale":
            return _axes(cur, a["axes"])[a["axis"] + "scale"] == a["expected"]
        if k == "axis_scale_unchanged":
            key = a["axis"] + "scale"
            return _axes(cur, a["axes"])[key] == _axes(base, a["axes"])[key]
        if k == "label_alignment":
            edge = {"x0": 0, "y0": 1, "x1": 2, "y1": 3}[a["edge"]]
            vals = [_axes(cur, i)[a["which"]]["bbox"][edge] for i in a["axes"]]
            return max(vals) - min(vals) <= a["tol"]
        if k == "shared_x":
            first, *rest = a["axes"]
            return all(j in _axes(cur, first)["shared_x"] for j in rest)
        if k == "fig_size":
            return _close(cur["size_in"][a["dim"] == "height"], a["expected"])
        if k == "fig_size_unchanged":
            i = a["dim"] == "height"
            return _close(cur["size_in"][i], base["size_in"][i])
        if k == "axes_count_unchanged":
            return len(cur["axes"]) == len(base["axes"])
        if k in ("errorbar_linewidth", "errorbar_data_unchanged", "errorbar_points_unchanged"):
            eb = _axes(cur, a["axes"])["errorbars"][a["index"]]
            if k == "errorbar_linewidth":
                return _close(eb["linewidth"], a["expected"])
            ref = _axes(base, a["axes"])["errorbars"][a["index"]]
            field = "data" if k == "errorbar_data_unchanged" else "points"
            return eb[field] == ref[field]
        if k in ("image_cmap", "image_data_unchanged", "image_clim_unchanged"):
            im = _axes(cur, a["axes"])["images"][0]
            if k == "image_cmap":
                return im["cmap"] == a["expected"]
            ref = _axes(base, a["axes"])["images"][0]
            return (
                im["data"] == ref["data"]
                if k == "image_data_unchanged"
                else im["clim"] == ref["clim"]
            )
        if k in (
            "bar_property",
            "bar_property_unchanged",
            "bar_heights_unchanged",
            "bar_count_unchanged",
        ):
            bar = _axes(cur, a["axes"])["bars"][a["index"]]
            ref = _axes(base, a["axes"])["bars"][a["index"]]
            if k == "bar_property":
                v = bar[a["prop"]]
                return (
                    _close(v, a["expected"])
                    if isinstance(a["expected"], (int, float))
                    else v == a["expected"]
                )
            if k == "bar_property_unchanged":
                return bar[a["prop"]] == ref[a["prop"]]
            if k == "bar_heights_unchanged":
                return bar["heights"] == ref["heights"]
            return bar["count"] == ref["count"]
    except (LookupError, TypeError):
        return False
    raise AssertionError(f"未知断言类型 {k!r}——闭集见 tests/fixtures/figurebench/README.md")


ASSERTION_KINDS = {
    "line_property",
    "line_property_unchanged",
    "series_data_unchanged",
    "series_count_unchanged",
    "text_equals",
    "text_contains",
    "text_unchanged",
    "fontsize",
    "fontsize_unchanged",
    "legend_loc",
    "axis_scale",
    "axis_scale_unchanged",
    "label_alignment",
    "shared_x",
    "fig_size",
    "fig_size_unchanged",
    "axes_count_unchanged",
    "errorbar_linewidth",
    "errorbar_data_unchanged",
    "errorbar_points_unchanged",
    "image_cmap",
    "image_data_unchanged",
    "image_clim_unchanged",
    "bar_property",
    "bar_property_unchanged",
    "bar_heights_unchanged",
    "bar_count_unchanged",
}


# --------------------------------------------------------------------------- 场景文件形状
def test_scenario_files_are_well_formed_and_split_by_rule():
    seen: set[str] = set()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for split in SCENARIO_FILES:
        doc = _load(split)
        assert doc["schema"] == 1 and doc["split"] == split
        assert doc["scenarios"], f"{split} 为空"
        for s in doc["scenarios"]:
            sid = s["id"]
            assert sid not in seen, f"重复 id {sid}"
            seen.add(sid)
            want = (
                "holdout" if int(hashlib.sha256(sid.encode()).hexdigest(), 16) % 5 == 0 else "dev"
            )
            assert s["split"] == split == want, f"{sid} 不在拆分规则指定的文件里"
            assert (
                s["category"] in CATEGORIES
                and s["task_type"] in TASK_TYPES
                and s["check"] in CHECKS
            ), sid
            assert s["expected_outcome"] in OUTCOMES, sid
            assert s["instruction"].strip(), sid
            assert f"`{sid}`" in readme, f"README 没列 {sid}"
    assert len(seen) >= 30, "首轮至少 30 个场景"


def test_probe_scenarios_use_closed_assertion_kinds_and_valid_references():
    for s in _probe_scenarios():
        assert s["fixture"] in FIGURE_FIXTURES, s["id"]
        ids = [a["id"] for a in s["goal"] + s["preserve"]]
        assert len(ids) == len(set(ids)), s["id"]
        assert s["preserve"], f"{s['id']} 没有任何保持断言"
        assert s["goal"] or s["subjective"], f"{s['id']} 无目标断言却没标 subjective"
        for a in s["goal"] + s["preserve"]:
            assert a["kind"] in ASSERTION_KINDS, (s["id"], a)
            if "which" in a:
                assert a["which"] in WHICH, (s["id"], a)
        assert s["reference_solution"], s["id"]
        assert s["negative_variants"], f"{s['id']} 没有反例"
        for n in s["negative_variants"]:
            assert n["must_fail"] and set(n["must_fail"]) <= set(ids), (s["id"], n["id"])


def test_reliability_scenarios_point_at_existing_tests_or_a_phase():
    repo = ROOT.parents[2]
    for s in _all_scenarios():
        if s["check"] == "covered_by":
            for path in s["covered_by"]:
                assert (repo / path).is_file(), (s["id"], path)
        elif s["check"] == "gap":
            assert s["gap_phase"] in range(1, 7), s["id"]


def test_readme_lists_every_fixture_directory():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dirs = sorted(
        p.name for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    )
    assert dirs == sorted((*FIGURE_FIXTURES, "csv_inputs"))
    for name in dirs:
        assert f"`{name}/`" in readme, name


# --------------------------------------------------------------------------- 输入真值
def test_csv_inputs_match_hand_written_truth():
    truth = json.loads((ROOT / "csv_inputs" / "truth.json").read_text(encoding="utf-8"))
    files = sorted(p.name for p in (ROOT / "csv_inputs").glob("*.csv"))
    assert files == sorted(truth["files"])
    for name, t in truth["files"].items():
        with (ROOT / "csv_inputs" / name).open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            assert reader.fieldnames == t["columns"], name
        assert len(rows) == t["rows"], name
        for col, want in t["per_column"].items():
            got = {"numeric": 0, "empty": 0, "nan": 0, "text": []}
            for row in rows:
                cell = row[col]
                if cell == "":
                    got["empty"] += 1
                    continue
                try:
                    v = float(cell)
                except ValueError:
                    got["text"].append(cell)
                    continue
                got["nan" if math.isnan(v) else "numeric"] += 1
            assert got == want, (name, col)


def test_every_data_to_figure_scenario_points_at_a_csv_input():
    truth = json.loads((ROOT / "csv_inputs" / "truth.json").read_text(encoding="utf-8"))
    for s in _all_scenarios():
        if s["task_type"] == "data_to_figure":
            assert (
                s["fixture"].startswith("csv_inputs/")
                and s["fixture"].split("/", 1)[1] in truth["files"]
            ), s["id"]


# --------------------------------------------------------------------------- 原生执行
@pytest.fixture(scope="module")
def probe_results(tmp_path_factory):
    try:
        from tavotto.engine import pool

        python = pool.find_worker_python()
    except Exception as exc:  # noqa: BLE001 —— 找不到解释器就是 not_run
        pytest.skip(f"not_run：找不到装了 matplotlib 的解释器（{type(exc).__name__}）")
    work = tmp_path_factory.mktemp("figurebench")
    before = _snapshot(ROOT)
    jobs = [
        {"id": job_id, "script": str(stage(work, job_id, fixture, edits, sid))}
        for job_id, fixture, edits, sid in variants()
    ]
    results = run_probe(python, jobs, work)
    assert _snapshot(ROOT) == before, "夹具目录在执行后变了"
    return results


@pytest.mark.parametrize("fixture", FIGURE_FIXTURES)
def test_fixture_scripts_draw_the_hand_written_truth(probe_results, fixture):
    res = probe_results[f"base__{fixture}"]
    assert res["ok"], res.get("error")
    facts = res["facts"]
    truth = json.loads((ROOT / fixture / "truth.json").read_text(encoding="utf-8"))
    assert facts["size_in"] == truth["size_in"]
    for t in truth["series"]:
        line = _series(facts, {"axes": t["axes"], "series": t["label"]})
        assert line["data"]["sha256"] == _xy_digest(t["x"], t["y"]), (fixture, t["label"])
    for t in truth.get("errorbar_points", []):
        eb = facts["axes"][t["axes"]]["errorbars"][t["index"]]
        assert eb["points"]["sha256"] == _xy_digest(t["x"], t["y"]), fixture
    for t in truth.get("bars", []):
        bar = facts["axes"][t["axes"]]["bars"][t["index"]]
        assert bar["heights"]["sha256"] == _digest(t["heights"], (len(t["heights"]),)), fixture
    if "image" in truth:
        z = truth["image"]["z"]
        im = facts["axes"][truth["image"]["axes"]]["images"][0]
        assert im["data"]["sha256"] == _digest(
            [v for row in z for v in row], (len(z), len(z[0]))
        ), fixture


@pytest.mark.parametrize("scenario", _probe_scenarios(), ids=lambda s: s["id"])
def test_scenario_is_solvable_and_its_gates_are_not_hollow(probe_results, scenario):
    sid = scenario["id"]
    base = probe_results[f"base__{scenario['fixture']}"]["facts"]
    for a in scenario["preserve"]:
        assert _holds(a, base, base), f"{sid}: 保持断言 {a['id']} 在基线上就不成立"
    if scenario["goal"]:
        assert not all(_holds(a, base, base) for a in scenario["goal"]), (
            f"{sid}: 基线已满足全部目标，场景不考任何东西"
        )
    ref = probe_results[f"{sid}__ref"]
    assert ref["ok"], f"{sid} 参考解跑不起来：{ref.get('error')}"
    for a in scenario["goal"] + scenario["preserve"]:
        assert _holds(a, ref["facts"], base), f"{sid}: 参考解没满足 {a['id']}"
    by_id = {a["id"]: a for a in scenario["goal"] + scenario["preserve"]}
    for n in scenario["negative_variants"]:
        res = probe_results[f"{sid}__{n['id']}"]
        assert res["ok"], (
            f"{sid}/{n['id']} 反例本身跑崩了（反例应当是能出图的错误改法）：{res.get('error')}"
        )
        for aid in n["must_fail"]:
            assert not _holds(by_id[aid], res["facts"], base), (
                f"{sid}/{n['id']}: {aid} 没有红——门禁空转"
            )
