"""TAF 数据出图的确定性内核（ADR 0138 Phase 5a）：受控摘要、列映射、模板脚本、期望数据指纹。

FigureBench 的 `csv_inputs`（FB-D01–D08）是输入真值：摘要逐项对 `truth.json`；映射按场景的 `policy`；生成的脚本交给
**真 safe worker** 跑一遍，manifest 里的数据指纹与父进程按同一规则算出的期望值逐字节相等——两处实现（`plotted_data` 与
模板里的 `_cell` / `_x`）说的是同一件事，才能拿期望值去判「画的就是这份数据」。
"""

from __future__ import annotations

import ast
import json
import math
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tavotto.engine import ai_data_figure as adf, discover, pool

FIX = Path(__file__).resolve().parent / "fixtures" / "figurebench" / "csv_inputs"
TRUTH = json.loads((FIX / "truth.json").read_text(encoding="utf-8"))["files"]
SCENARIOS = {
    s["id"]: s
    for f in ("scenarios.json", "scenarios_holdout.json")
    for s in json.loads((FIX.parent / f).read_text(encoding="utf-8"))["scenarios"]
    if s["task_type"] == "data_to_figure"
}

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


def _summary(sid: str) -> adf.DataSummary:
    return adf.inspect_data_source(FIX.parent / SCENARIOS[sid]["fixture"])


def _plan(sid: str):
    return adf.plan_mapping(_summary(sid), SCENARIOS[sid]["instruction"])


# --------------------------------------------------------------------------- 受控摘要
@pytest.mark.parametrize("name", sorted(TRUTH))
def test_summary_matches_the_input_truth(name):
    s = adf.inspect_data_source(FIX / name)
    t = TRUTH[name]
    assert s.columns == t["columns"] and len(s.rows) == t["rows"]
    for col, want in t["per_column"].items():
        got = s.per_column[col].to_dict()
        assert {k: got[k] for k in ("numeric", "empty", "nan", "text")} == want, col


def test_summary_dict_carries_no_cell_values():
    d = adf.inspect_data_source(FIX / "standard.csv").to_dict()
    assert d["rows"] == 10 and "0.42" not in json.dumps(d), "摘要只带计数与类型，不带数值"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", "data_empty"),
        (b"x,y\n", "data_empty"),
        (b"x,x\n1,2\n", "data_header_invalid"),
        (b"x,\n1,2\n", "data_header_invalid"),
        (b"x,y\n1\n", "data_ragged"),
        (b"x,y\n\xff\xfe,1\n", "data_decode_error"),
    ],
)
def test_bad_inputs_are_refused_with_a_stable_code(tmp_path, content, code):
    p = tmp_path / "d.csv"
    p.write_bytes(content)
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(p)
    assert e.value.code == code


def test_oversized_input_is_refused_not_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(adf, "MAX_ROWS", 3)
    p = tmp_path / "d.csv"
    p.write_text("x,y\n" + "".join(f"{i},{i}\n" for i in range(4)), encoding="utf-8")
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(p)
    assert e.value.code == "data_too_long"


# --------------------------------------------------------------------------- 列映射
@pytest.mark.parametrize(
    ("sid", "x", "ys", "kind", "is_date"),
    [
        ("FB-D01", "time_s", ("voltage_mV",), "line", False),
        ("FB-D03", "x", ("y",), "line", False),
        ("FB-D04", "sample", ("value",), "bar", False),
        ("FB-D05", "date", ("temperature_C",), "line", True),
        ("FB-D06", "x", ("signal",), "line", False),
        ("FB-D07", "t", ("ctrl", "treat"), "line", False),
        ("FB-D08", "x", ("value",), "line", False),
    ],
)
def test_mapping_follows_the_scenario_policy(sid, x, ys, kind, is_date):
    assert _plan(sid) == adf.Mapping(x=x, ys=ys, kind=kind, x_is_date=is_date)


def test_meaningless_headers_ask_the_user_with_a_neutral_preview():
    got = _plan("FB-D02")
    assert isinstance(got, adf.NeedsInput) and got.reason == "column_mapping_ambiguous"
    assert got.preview == adf.Mapping(x="a", ys=("b", "c"), kind="line")
    # 用户点名之后就不再问
    named = adf.plan_mapping(_summary("FB-D02"), "画 c 随 a 的变化")
    assert named == adf.Mapping(x="a", ys=("c",), kind="line")


def test_a_note_column_is_never_plotted_and_its_text_never_reaches_the_script():
    s = _summary("FB-D08")
    m = _plan("FB-D08")
    assert "note" not in m.ys
    text = adf.render_script(s, m, data_rel="injection.csv", stem="injection_figure")
    for t in s.per_column["note"].text:
        assert t not in text


def test_text_columns_are_never_y_even_without_a_named_column():
    m = adf.plan_mapping(_summary("FB-D08"), "画出来")
    assert m == adf.Mapping(x="x", ys=("value",), kind="line"), "note 是备注，不是数据"


def test_column_mention_is_by_word():
    s = adf.inspect_data_source(FIX / "two_series.csv")
    assert adf.plan_mapping(s, "画 treatment 组") == adf.Mapping(
        x="t", ys=("ctrl", "treat"), kind="line"
    ), "treatment 不算提到 treat"


def test_categorical_x_with_several_series_is_unsupported_not_guessed(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("sample,a_val,b_val\nS1,1,2\nS2,3,4\n", encoding="utf-8")
    got = adf.plan_mapping(adf.inspect_data_source(p), "画出来")
    assert got == adf.Unsupported("grouped_bars_unsupported")


def test_named_y_that_cannot_be_plotted_is_refused_not_swapped():
    s = _summary("FB-D08")  # x,value,note
    for ins in ("画 note 随 x 的变化", "画 value 和 note 随 x 的变化"):
        assert adf.plan_mapping(s, ins) == adf.Unsupported("requested_column_not_numeric"), ins
    # x 被点名不算 y
    assert adf.plan_mapping(s, "画 x") == adf.Mapping(x="x", ys=("value",), kind="line")


def test_cjk_column_names_take_the_longest_match(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("时间,温度,温度值\n1,2,3\n2,3,4\n", encoding="utf-8")
    got = adf.plan_mapping(adf.inspect_data_source(p), "画温度值随时间的变化")
    assert got == adf.Mapping(x="时间", ys=("温度值",), kind="line")


def test_x_mixing_numbers_and_text_asks_instead_of_guessing(tmp_path):
    # 「1, oops, 3」像坏值，「A, 2, C」像数字类别名：两种读法不可分，都不自行判折线或柱图
    for name, body in (
        ("bad", "x,y\n1,2\noops,3\n3,4\n"),
        ("labels", "sample,value\nA,1\n2,2\nC,3\n"),
    ):
        p = tmp_path / f"{name}.csv"
        p.write_text(body, encoding="utf-8")
        s = adf.inspect_data_source(p)
        got = adf.plan_mapping(s, "画出来")
        assert isinstance(got, adf.NeedsInput) and got.reason == "x_column_mixed", name
        assert got.preview == adf.Mapping(x=s.columns[0], ys=(s.columns[1],), kind="line"), name


def test_a_date_column_with_a_bad_date_asks_instead_of_becoming_categories(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("date,temp_C\n2026-01-01,1\n2026-01-02,2\nlater,3\n", encoding="utf-8")
    got = adf.plan_mapping(adf.inspect_data_source(p), "画出来")
    assert isinstance(got, adf.NeedsInput) and got.reason == "x_column_mixed"


def test_plotted_data_reports_a_row_whose_x_is_not_a_value(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("x,y\n1,2\noops,3\n3,4\n", encoding="utf-8")
    s = adf.inspect_data_source(p)
    got = adf.plotted_data(s, adf.Mapping(x="x", ys=("y",), kind="line"))
    assert [(r["row"], r["reason"]) for r in got.not_plotted] == [(3, "x_not_a_value")]
    assert got.series[0][3] == (2, 2)
    # 空 x 的行在数值列里照样可达（plan_mapping 判 numeric → 折线）
    q = tmp_path / "e.csv"
    q.write_text("x,y\n1,2\n,3\n3,4\n", encoding="utf-8")
    s2 = adf.inspect_data_source(q)
    assert adf.plan_mapping(s2, "画出来").kind == "line"
    assert [
        r["reason"]
        for r in adf.plotted_data(s2, adf.Mapping(x="x", ys=("y",), kind="line")).not_plotted
    ] == ["x_not_a_value"]


@pytest.mark.parametrize(
    ("col", "label"),
    [
        ("time_s", "time (s)"),
        ("voltage_mV", "voltage (mV)"),
        ("temperature_C", "temperature (°C)"),
        ("signal", "signal"),
        ("x", "x"),
        ("my_value", "my_value"),  # value 不是单位
        ("ctrl", "ctrl"),
    ],
)
def test_axis_labels_take_units_only_from_a_closed_suffix_set(col, label):
    assert adf.axis_label(col) == label


# --------------------------------------------------------------------------- 期望数据
def test_missing_cells_break_the_line_and_are_counted():
    p = adf.plotted_data(_summary("FB-D03"), _plan("FB-D03"))
    ((label, part, values, shape),) = p.series
    ys = values[1::2]
    assert label == "y" and part == "xy" and shape == (8, 2)
    assert sum(1 for v in ys if math.isfinite(v)) == 5 and p.missing == 3
    assert p.not_plotted == ()


def test_text_in_a_numeric_column_is_reported_not_coerced():
    p = adf.plotted_data(_summary("FB-D04"), _plan("FB-D04"))
    assert [(r["row"], r["reason"]) for r in p.not_plotted] == [
        (3, "not_a_number"),
        (5, "not_a_number"),
    ]
    ((_, part, values, shape),) = p.series
    assert part == "bars" and shape == (4, 2) and 0.1 not in values[1::2]


def test_digest_is_the_canonical_rule():
    # 与 tests/test_figurebench_fixtures.py::_digest 同一规则：形状头 + 小端 float64，NaN 统一位型
    import hashlib
    import struct

    vals = [0.0, 1.0, math.nan, 2.0]
    want = hashlib.sha256(b"2x2:" + struct.pack("<4d", *vals)).hexdigest()
    assert adf.digest(vals, (2, 2)) == {"sha256": want, "shape": [2, 2], "finite": 3}
    neg = [0.0, 1.0, -math.nan, 2.0]  # 另一种 NaN 位型也统一
    assert adf.digest(neg, (2, 2))["sha256"] == want


# --------------------------------------------------------------------------- 模板
MAPPED = ["FB-D01", "FB-D03", "FB-D04", "FB-D05", "FB-D06", "FB-D07", "FB-D08"]


@pytest.mark.parametrize("sid", MAPPED)
def test_generated_script_is_static_and_parseable(tmp_path, sid):
    s = _summary(sid)
    stem = adf.stem_for(s.path_name)
    script = tmp_path / f"{stem}.py"
    script.write_text(
        adf.render_script(s, _plan(sid), data_rel=s.path_name, stem=stem), encoding="utf-8"
    )
    tree = ast.parse(script.read_text(encoding="utf-8"))
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert mains and not mains[0].args.args, "无参 main()"
    info = discover.inspect_script(script, tmp_path, glob_disk=False)["info"]
    assert info["stems"] == [stem], "输出名静态可读（登记不需要试运行）"


#: 两种引号都带：不论模板用哪种引号拼，没经 repr 就会变成代码
HOSTILE_X = "t\"); import os; os.remove('x'); (\""

HOSTILE_Y = 'v"\nprint(1)'


def test_hostile_column_names_stay_string_literals(tmp_path):
    p = tmp_path / "d.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        import csv

        csv.writer(fh).writerows([[HOSTILE_X, HOSTILE_Y], [1, 2], [2, 3]])
    s = adf.inspect_data_source(p)
    m = adf.plan_mapping(s, "画出来")
    assert m.x == HOSTILE_X and m.ys == (HOSTILE_Y,)
    text = adf.render_script(s, m, data_rel="d.csv", stem="d_figure")
    tree = ast.parse(text)
    top = {type(n).__name__ for n in tree.body}
    assert top <= {"Expr", "Import", "ImportFrom", "Assign", "FunctionDef"}, top
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imports <= {"csv", "math", "datetime", "re", "matplotlib.pyplot"}, imports
    calls = {getattr(n.func, "id", None) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "print" not in calls
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert HOSTILE_X in consts and HOSTILE_Y in consts, "列名原样以字符串字面量出现"
    comment = next(line for line in text.splitlines() if line.startswith("# tavotto-source:"))
    assert json.loads(comment.split(":", 1)[1])["mapping"]["y"] == [HOSTILE_Y]


def test_stem_is_sanitised():
    assert adf.stem_for("my data (v2).csv") == "my_data_v2_figure"
    assert adf.stem_for("数据.csv") == "data_figure"


def test_date_scripts_pin_the_epoch_and_others_do_not():
    d = _summary("FB-D05")
    text = adf.render_script(d, _plan("FB-D05"), data_rel=d.path_name, stem="t_figure")
    assert 'set_epoch("1970-01-01T00:00:00")' in text
    n = _summary("FB-D03")
    assert "set_epoch" not in adf.render_script(n, _plan("FB-D03"), data_rel=n.path_name, stem="t")


# --------------------------------------------------------------------------- 真 worker 对拍
def _labelled(man: dict) -> dict[str, dict]:
    out = {}
    for el in man.get("elements", []):
        fp = el.get("data_fingerprint")
        if not isinstance(fp, dict):
            continue
        label = next(
            (f.get("value") for f in el.get("editable", []) if f.get("prop") == "label"), None
        )
        if label is not None:
            out[str(label)] = fp
    return out


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_worker_draws_exactly_the_expected_data(tmp_path):
    def build(sid):
        work = tmp_path / sid
        work.mkdir()
        if sid in SCENARIOS:
            s, m = _summary(sid), _plan(sid)
            src = FIX / s.path_name
        else:
            src = tmp_path / "_extra" / sid
            s = adf.inspect_data_source(src)
            m = adf.plan_mapping(s, "画 value")
            assert m.kind == "bar"
        (work / s.path_name).write_bytes(src.read_bytes())
        stem = adf.stem_for(s.path_name)
        (work / f"{stem}.py").write_text(
            adf.render_script(s, m, data_rel=s.path_name, stem=stem), encoding="utf-8"
        )
        w = pool.one_shot(f"{stem}.py", str(work), "main")
        try:
            w.ensure_built()
            man = w.override(stem, [])["manifest"]
        finally:
            pool.discard(w)
        return sid, adf.expected_fingerprints(adf.plotted_data(s, m)), man

    # 夹具之外再加一份「柱图里有空单元格」：缺失行整行不画，两边都不能画成 0 或 NaN 柱
    extra = tmp_path / "_extra"
    extra.mkdir()
    (extra / "bars.csv").write_text("sample,value\nA,1\nB,\nC,nan\nD,2\n", encoding="utf-8")
    (extra / "dups.csv").write_text("sample,value\nA,1\nA,2\nB,3\n", encoding="utf-8")
    BAR_MISSING, BAR_DUPS = "bars.csv", "dups.csv"

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(build, [*MAPPED, BAR_MISSING, BAR_DUPS]))
    for sid, expected, man in results:
        got = _labelled(man)
        for e in expected:
            fp = got.get(e["label"])
            assert fp is not None and fp.get("status") == "ok", (sid, e["label"], sorted(got))
            assert fp["parts"][e["part"]] == e["digest"], (sid, e["label"])


def _run_main(tmp_path, csv_text, instruction, pre=""):
    """在 worker 解释器的子进程里跑生成脚本的 main()（不真存盘），返回 `(summary, mapping, 子进程打印的 JSON)`。"""
    (tmp_path / "d.csv").write_text(csv_text, encoding="utf-8")
    s = adf.inspect_data_source(tmp_path / "d.csv")
    m = adf.plan_mapping(s, instruction)
    script = tmp_path / "d_figure.py"
    script.write_text(adf.render_script(s, m, data_rel="d.csv", stem="d_figure"), encoding="utf-8")
    code = (
        "import json, runpy, matplotlib\n"
        "matplotlib.use('Agg')\n"
        f"{pre}\n"
        "import matplotlib.pyplot as plt\n"
        "plt.Figure.savefig = lambda *a, **k: None\n"
        f"ns = runpy.run_path({str(script)!r})\n"
        "ns['main']()\n"
        "ax = plt.gcf().axes[0]\n"
        "print(json.dumps({'x': [p.get_x() for p in ax.patches],"
        " 'xy': [list(map(float, r)) for r in ax.lines[0].get_xydata()] if ax.lines else []}))\n"
    )
    out = subprocess.run(
        [WORKER_PY, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True,
    ).stdout
    return s, m, json.loads(out.strip().splitlines()[-1])


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_duplicate_categories_are_drawn_at_distinct_positions(tmp_path):
    _, m, got = _run_main(tmp_path, "sample,value\nA,1\nA,2\nB,3\n", "画 value")
    assert m.kind == "bar" and len(got["x"]) == 3 and len(set(got["x"])) == 3, got


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_date_axis_ignores_a_configured_epoch(tmp_path):
    csv_text = "date,v\n2024-01-01,1\n2024-01-03,2\n"
    s, m, got = _run_main(
        tmp_path,
        csv_text,
        "画 v",
        pre="matplotlib.rcParams['date.epoch'] = '2000-01-01T00:00:00'",
    )
    assert m.x_is_date
    flat = tuple(c for pt in got["xy"] for c in pt)
    ((_, _, want, shape),) = adf.plotted_data(s, m).series
    assert adf.digest(flat, shape) == adf.digest(want, shape)
