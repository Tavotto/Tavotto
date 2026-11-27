# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「真实里常见的脏格式」表的读法（ADR 0178 修订 2026-11-27 · 读脏表）。

每一节对应一个判据，判据的主语都是**表里某一处的写法**，不是整张表好不好：

1. 尾部没有名字、整列都是空的列（Excel 另存 CSV 常见：`a,b,c,,`）→ 丢掉；名字空但有数据的列、中间的空名字仍是 `data_header_invalid`。
2. XLSX 开头有标题行 → 与文本表同一套「猜表头行」的判据。
3. 第一行整行都是数值 → 没有表头，列名叫 `col1`、`col2`…
4. 日期的写法：中文年月日、`YYYY/M/D`、带时刻的 ISO。
5. 整列都带 `%` 的数值；分号 / 制表符文件里整列一致的小数逗号。

共同的口径：**父进程与生成的脚本读出同一张表**（用例把公共模块真的导入、用它读同一个文件，与父进程逐项对拍）；
小表（整份读）与大表（流式）一致；这些用例里没有网络、没有计时。
"""

from __future__ import annotations

import importlib.util
import socket
import sys

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_template as tpl,
)
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_profile import make_xlsx
from tests.test_ai_data_propose import project  # noqa: F401


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(P, "CANDIDATE_ROOT", tmp_path / "propose")


def write(tmp_path, text, name="t.csv", encoding="utf-8"):
    folder = tmp_path / "d"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(text, encoding=encoding, newline="")
    return path


def runtime(tmp_path, monkeypatch):
    """公共模块真的导入（脚本里读表用的就是它）。"""
    name, data = tpl.runtime_module()
    path = tmp_path / "rt" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, path.stem, mod)
    spec.loader.exec_module(mod)
    return mod


def read_large(path, monkeypatch, **kw):
    """强制走流式读（大表那条路）。"""
    with monkeypatch.context() as m:
        m.setattr(adf, "SMALL_BYTES", 0)
        info = adf.read_text_table(path, **kw)
    if info.summary is not None:
        assert info.summary.large is not None  # 确实走了流式读
    return info


# =========================================================================== 1. 尾部没有名字的空列
TRAILING = "depth_m,temp_C,salinity,,\n0,18.16,34.000,,\n50,17.30,34.050,,\n100,16.72,34.100,,\n"
TRAIL_COLS = ["depth_m", "temp_C", "salinity"]


def test_trailing_empty_columns_from_an_excel_save_are_dropped(tmp_path):
    info = adf.read_text_table(write(tmp_path, TRAILING))
    assert info.status == "ok" and info.summary is not None
    assert info.summary.columns == TRAIL_COLS
    assert info.summary.rows == [
        ["0", "18.16", "34.000"],
        ["50", "17.30", "34.050"],
        ["100", "16.72", "34.100"],
    ]


def test_trailing_empty_columns_streaming_reader_reads_the_same_table(tmp_path, monkeypatch):
    p = write(tmp_path, TRAILING)
    small = adf.read_text_table(p)
    large = read_large(p, monkeypatch)
    assert large.status == small.status == "ok"
    assert large.summary.columns == small.summary.columns == TRAIL_COLS
    assert large.summary.row_count == len(small.summary.rows) == 3


def test_trailing_empty_columns_script_runtime_reads_the_same_table(tmp_path, monkeypatch):
    p = write(tmp_path, TRAILING)
    parent = adf.inspect_data_source(p, None, v2=True)
    header, rows = runtime(tmp_path, monkeypatch).read_delimited(p, ",")
    assert header == parent.columns == TRAIL_COLS
    assert rows == [list(r) for r in parent.rows]


def test_trailing_empty_columns_tab_and_explicit_header_row(tmp_path):
    p = write(tmp_path, "notes\na\tb\t\t\n1\t2\t\t\n3\t4\t\t\n5\t6\t\t\n", "t.tsv")
    info = adf.read_text_table(p, header_row=2)
    assert info.status == "ok" and info.summary.columns == ["a", "b"]
    assert info.summary.rows == [["1", "2"], ["3", "4"], ["5", "6"]]


def test_trailing_empty_columns_old_reader_and_xlsx_script_agree(tmp_path, monkeypatch):
    """`_summarize`（老读法 / XLSX 的整份读）与脚本里的 `read_xlsx` 用的是同一个裁剪。"""
    mod = runtime(tmp_path, monkeypatch)
    table = [["a", "b", "", ""], ["1", "2", "", ""], ["3", "4", "", ""]]
    summ = adf._summarize(table, path_name="t", sha256="0", size=1, parse=adf.parse_cell)
    assert summ.columns == ["a", "b"] and summ.rows == [["1", "2"], ["3", "4"]]
    # v1 的读法逐字不变：同一张表仍是 `data_header_invalid`
    with pytest.raises(adf.DataSourceError) as exc:
        adf._summarize(table, path_name="t", sha256="0", size=1)
    assert exc.value.code == "data_header_invalid"
    # 脚本里的 `_trim_tail` 对 XLSX 的行同样裁剪
    h, body = mod._trim_tail(["a", "b", "", ""], iter([(0, ["1", "2", "", ""])]))
    assert h == ["a", "b"] and list(body) == [(0, ["1", "2"])]


@pytest.mark.parametrize(
    "text",
    [
        "a,b,\n1,2,3\n4,5,6\n",  # 末尾一列名字空、却有数据
        "a,b,,\n1,2,,9\n4,5,,\n7,8,,\n",  # 尾部两个空名字，只有最后一列在第一行有数据
        "a,b,,\n1,2,,\n4,5,7,\n7,8,,\n",  # 尾部两个空名字，倒数第二列在中间一行有数据
        "a,,c\n1,2,3\n4,5,6\n",  # 中间的空名字（不是尾部）
        "a,,c,\n1,,3,\n4,,6,\n",  # 中间空名字整列也空：只丢尾部，中间的仍不行
    ],
    ids=[
        "tail-data",
        "tail-data-last",
        "tail-data-middle-row",
        "middle",
        "middle-empty-column",
    ],
)
def test_a_nameless_column_with_data_or_in_the_middle_is_still_invalid(tmp_path, text):
    p = write(tmp_path, text)
    small = adf.read_text_table(p)
    assert small.summary is None and small.status == "unreadable"
    assert small.code == "data_header_invalid"
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(p, None, v2=True)
    assert exc.value.code == "data_header_invalid"


def test_rows_of_another_width_under_a_blank_tail_header_keep_the_header_code(tmp_path):
    """表头判定先于行宽判定（与此前同码）：用户指定第 1 行当表头、它末尾有空名字、数据行却更窄，仍是 `data_header_invalid`，不变成 `data_ragged`。"""
    p = write(tmp_path, "a,b,,\n1,2\n3,4\n")
    info = adf.read_text_table(p, header_row=1)
    assert info.status == "unreadable" and info.code == "data_header_invalid"


@pytest.mark.parametrize("text", ["a,b,,\n1,2,,9\n4,5,,\n7,8,,\n", "a,b,,\n1,2,,\n4,5,7,\n7,8,,\n"])
def test_streaming_reader_rejects_data_under_a_nameless_column_with_the_same_code(
    tmp_path, monkeypatch, text
):
    p = write(tmp_path, text)
    with monkeypatch.context() as m:
        m.setattr(adf, "SMALL_BYTES", 0)
        info = adf.read_text_table(p)
    assert info.status == "unreadable" and info.code == "data_header_invalid"


def test_a_guessed_header_row_with_data_under_blank_names_is_still_unsure(tmp_path):
    """标题行 `实验一,,,` 夹在前面：猜的表头读不出合格的表 → 仍是 `unsure`（让用户点一下），不因尾部裁剪变成别的。"""
    p = write(tmp_path, "run 7,,\n,a,b\n1,2,3\n2,4,5\n3,6,7\n")
    assert adf.read_text_table(p).status == "unsure"


def test_propose_offers_candidates_for_the_trailing_column_export(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "t.csv").write_text(TRAILING, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp


# =========================================================================== 2. XLSX 开头有标题行
#: 第 1 行只有 A1 有字（标题）、第 2 行空、第 3 行才是列名（酶标仪导出）
PLATE = [
    ["酶标仪导出 2026-11-20 OD600", None, None, None],
    [],
    ["时间 (min)", "对照", "低剂量", "高剂量"],
    [0, 0.058, 0.052, 0.044],
    [30, 0.188, 0.145, 0.104],
    [60, 0.298, 0.228, 0.145],
    [90, 0.41, 0.31, 0.2],
]
PLATE_COLS = ["时间 (min)", "对照", "低剂量", "高剂量"]


def test_xlsx_with_a_title_row_reads_the_names_below_it(tmp_path):
    p = make_xlsx(tmp_path / "plate.xlsx", {"Sheet1": PLATE})
    s = adf.inspect_data_source(p, "Sheet1", v2=True)
    assert s.columns == PLATE_COLS and len(s.rows) == 4
    assert s.header_row == s.header_line == 3  # 工作表里的行号
    assert s.row_numbers == [4, 5, 6, 7]


def test_xlsx_title_row_summary_round_trips_through_its_header_line(tmp_path):
    """摘要的 `header_line` 会被出图 / 合画 / 提交那几处当 `header_row` 传回来重读：读出的必须是同一张表。"""
    p = make_xlsx(tmp_path / "plate.xlsx", {"Sheet1": PLATE})
    first = adf.inspect_data_source(p, "Sheet1", v2=True)
    again = adf.inspect_data_source(p, "Sheet1", v2=True, header_row=first.header_line)
    assert again.columns == first.columns and again.rows == first.rows
    assert again.header_row == first.header_row == 3
    # 指定的行号落在空行上：读的是它之后的第一条非空行，记下的是真正的表头行
    blank = adf.inspect_data_source(p, "Sheet1", v2=True, header_row=2)
    assert blank.columns == PLATE_COLS and blank.header_row == 3


def test_xlsx_explicit_header_row_past_the_end_is_the_header_row_error(tmp_path):
    p = make_xlsx(tmp_path / "plate.xlsx", {"Sheet1": PLATE})
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(p, "Sheet1", v2=True, header_row=99)
    assert exc.value.code == "data_header_row_invalid"


def test_xlsx_normal_tables_are_untouched(tmp_path):
    """第一行读得成表头的表：不猜、不记行号（`header_row` / `header_line` 仍是 None，生成的脚本与此前逐字相同）。"""
    rows = [["time", "a", "b"], [0, 1, 2], [1, 3, 4], [2, 5, 6]]
    p = make_xlsx(tmp_path / "t.xlsx", {"S": rows})
    s = adf.inspect_data_source(p, "S", v2=True)
    assert s.columns == ["time", "a", "b"] and s.header_row is None and s.header_line is None
    # 第一行就是表头、行号指定为 1：也不记（没有真的跳过行）
    again = adf.inspect_data_source(p, "S", v2=True, header_row=1)
    assert again.header_row is None and again.rows == s.rows


@pytest.mark.parametrize(
    "rows",
    [
        # 标题行后面直接是数据，没有列名行：猜不出来，仍是原来的错
        [["酶标仪导出", None, None], [0, 1, 2], [1, 3, 4], [2, 5, 6]],
        # 全是文字、没有数值列
        [["标题", None], ["a", "b"], ["c", "d"]],
    ],
    ids=["title-then-data", "no-numeric-block"],
)
def test_xlsx_without_a_findable_header_is_still_invalid(tmp_path, rows):
    p = make_xlsx(tmp_path / "t.xlsx", {"S": rows})
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(p, "S", v2=True)
    assert exc.value.code == "data_header_invalid"


def test_xlsx_v1_reader_is_unchanged(tmp_path):
    """v1（`v2=False`）逐字不变：标题行在前的表仍是 `data_header_invalid`。"""
    p = make_xlsx(tmp_path / "plate.xlsx", {"Sheet1": PLATE})
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(p, "Sheet1")
    assert exc.value.code == "data_header_invalid"


def test_xlsx_title_row_uses_the_same_criterion_as_text_tables(tmp_path):
    """同一套判据：文本表里同样的「标题 + 空行 + 列名 + 数据」读出的表头行是同一行、同一张表。"""
    text = "酶标仪导出 2026-11-20 OD600,,,\n\n时间 (min),对照,低剂量,高剂量\n0,0.058,0.052,0.044\n30,0.188,0.145,0.104\n60,0.298,0.228,0.145\n90,0.41,0.31,0.2\n"
    t = adf.read_text_table(write(tmp_path, text))
    x = adf.inspect_data_source(make_xlsx(tmp_path / "plate.xlsx", {"S": PLATE}), "S", v2=True)
    assert (
        t.status == "unsure"
    )  # 文本表里这是「猜的」：与 XLSX 的 `xlsx_header_guess` 同一个 `_pick_header`
    assert t.summary.columns == x.columns == PLATE_COLS
    assert t.summary.header_line == x.header_line == 3


def test_xlsx_title_row_script_reads_the_same_table_and_draws(tmp_path, monkeypatch):
    mod = runtime(tmp_path, monkeypatch)
    p = make_xlsx(tmp_path / "plate.xlsx", {"Sheet1": PLATE})
    parent = adf.inspect_data_source(p, "Sheet1", v2=True)
    header, rows = mod.read_xlsx(p, "Sheet1", parent.header_row)
    assert header == parent.columns and rows == [list(r) for r in parent.rows]
    # 默认读法（不给行号）仍从第一条非空行读起：标题行读不成表头，脚本里不猜（行号由 Tavotto 写进脚本）
    with pytest.raises(mod.NamelessTailData):
        mod.read_xlsx(p, "Sheet1")


def test_xlsx_title_row_generated_script_carries_the_row_and_runs(tmp_path, monkeypatch):
    import subprocess

    folder = tmp_path / "f"
    folder.mkdir()
    make_xlsx(folder / "plate.xlsx", {"Sheet1": PLATE})
    make_xlsx(folder / "plain.xlsx", {"S": [["time", "a"], [0, 1], [1, 3], [2, 5], [3, 6]]})
    profile = adf.profile_folder(folder)
    work = tmp_path / "work"
    work.mkdir()
    for table, sheet, ys, x in (
        ("plate.xlsx", "Sheet1", ["对照", "低剂量"], "时间 (min)"),
        ("plain.xlsx", "S", ["a"], "time"),
    ):
        (entry,) = [t for t in profile.tables if t.path == table and t.sheet == sheet]
        recipe = R.FigureRecipe.from_dict(
            {
                "table": table,
                "family": "trend",
                "layers": [{"mark": "line"}],
                "y": ys,
                "why": "说明",
                "x": x,
                "sheet": sheet,
            }
        )
        stem = table.split(".")[0]
        rendered = tpl.render_recipe_script(entry, recipe, data_rel=table, stem=stem)
        (work / table).write_bytes((folder / table).read_bytes())
        (work / f"{stem}.py").write_text(rendered.script, encoding="utf-8")
        (work / rendered.runtime_name).write_bytes(rendered.runtime)
        want = "read_xlsx(DATA, SHEET, 3)" if stem == "plate" else "read_xlsx(DATA, SHEET)"
        assert want in rendered.script
        out = subprocess.run(
            [sys.executable, f"{stem}.py"],
            cwd=work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={"MPLBACKEND": "Agg", "PATH": "", "HOME": str(tmp_path)},
        )
        assert out.returncode == 0, out.stderr[-2000:]
        assert (work / f"{stem}.pdf").exists()


def test_propose_offers_candidates_for_the_plate_reader_workbook(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    make_xlsx(root / "data" / "plate.xlsx", {"Sheet1": PLATE})
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp


# =========================================================================== 3. 第一行整行都是数值 = 没有表头
XRD = "".join(f"{10 + 0.05 * i:.2f}  {100 + (i * 7) % 40}\n" for i in range(30))
XRD_ROWS = [[f"{10 + 0.05 * i:.2f}", str(100 + (i * 7) % 40)] for i in range(30)]


def test_an_all_numeric_first_row_means_no_header(tmp_path):
    t = adf.read_text_table(write(tmp_path, XRD, "xrd.txt"))
    assert t.status == "unsure"  # 仍是「猜的」：与此前没有表头的兜底同一个把握
    assert t.summary.columns == ["col1", "col2"] and t.summary.no_header
    assert t.summary.rows == XRD_ROWS  # 第一行是数据，一行没丢
    assert t.summary.row_numbers == list(range(1, 31))  # 物理行号照旧
    assert t.header_line == 1 and t.rows == 30


def test_no_header_after_comment_lines_keeps_physical_line_numbers(tmp_path):
    p = write(
        tmp_path,
        "# scan 7 of sample A\n# step is 0.05 deg\n10.00  116\n10.05  101\n10.10  114\n",
        "x.dat",
    )
    t = adf.read_text_table(p)
    assert t.summary.columns == ["col1", "col2"] and t.summary.no_header
    assert t.summary.row_numbers == [3, 4, 5] and t.header_line == 3


@pytest.mark.parametrize(
    ("name", "text", "columns"),
    [
        # 表头本来就是数字、第一列是文字的宽表：第一格是文字，不是「整行数值」
        (
            "wide.csv",
            "sample,2019,2020,2021\nA,1,2,3\nB,4,5,6\nC,7,8,9\n",
            ["sample", "2019", "2020", "2021"],
        ),
        (
            "wide.txt",
            "sample  2019  2020  2021\nA  1  2  3\nB  4  5  6\nC  7  8  9\n",
            ["sample", "2019", "2020", "2021"],
        ),
        # 第一格是文字、下面全是数（`id` 列是数字编号）
        ("ids.csv", "id,2019,2020\n1,5,6\n2,7,8\n3,9,10\n", ["id", "2019", "2020"]),
        # 第一行有一格是空的 / 是文字
        ("gap.csv", "1,,3\n4,5,6\n7,8,9\n", None),
        ("word.csv", "1,x,3\n4,5,6\n7,8,9\n", None),
        # 下面的行里有文字：不是一张纯数字的表
        ("mixed.txt", "1  2\n3  four\n5  6\n7  8\n", None),
        # 有表头的老样子
        ("named.txt", "x  y\n1  2\n3  4\n5  6\n", ["x", "y"]),
    ],
)
def test_tables_with_a_header_are_not_turned_into_headerless(tmp_path, name, text, columns):
    t = adf.read_text_table(write(tmp_path, text, name))
    assert not (t.summary and t.summary.no_header)
    if columns is not None:
        assert t.summary.columns == columns


def test_the_numeric_header_over_numeric_data_limit_is_pinned(tmp_path):
    """分不开的情形：表头是纯数字（年份）、下面也全是数。按没有表头读（第一行是数据），嗅探仍标「猜的」——用户在预览里看得到这一行。"""
    t = adf.read_text_table(
        write(tmp_path, "2019  2020  2021\n1  2  3\n4  5  6\n7  8  9\n", "y.txt")
    )
    assert t.status == "unsure" and t.summary.columns == ["col1", "col2", "col3"]
    assert t.summary.rows[0] == ["2019", "2020", "2021"]


def test_no_header_streaming_reader_reads_the_same_table(tmp_path, monkeypatch):
    p = write(tmp_path, XRD, "xrd.txt")
    small = adf.read_text_table(p)
    large = read_large(p, monkeypatch)
    assert large.status == small.status == "unsure"
    assert large.summary.columns == small.summary.columns == ["col1", "col2"]
    assert large.summary.row_count == 30 and large.summary.no_header
    assert large.summary.large.preview[0] == XRD_ROWS[0]  # 第一行在前几行预览里


def test_no_header_script_reader_reads_the_same_table(tmp_path, monkeypatch):
    mod = runtime(tmp_path, monkeypatch)
    p = write(tmp_path, XRD, "xrd.txt")
    parent = adf.inspect_data_source(p, None, v2=True)
    entry = adf.TableEntry("xrd.txt", None, False, parent)
    expr = tpl._reader_expr(entry, False)
    assert expr == "read_delimited(DATA, 'ws', 1, False, True)"
    header, rows = eval(expr, {**vars(mod), "DATA": p})  # noqa: S307 - 生成脚本里的那一句
    assert header == parent.columns and rows == [list(r) for r in parent.rows]
    # 常态的脚本逐字不变
    named = adf.inspect_data_source(write(tmp_path, "x  y\n1  2\n3  4\n", "n.txt"), None, v2=True)
    assert "True" not in tpl._reader_expr(adf.TableEntry("n.txt", None, False, named), False)


def test_no_header_summary_round_trips_through_its_header_line(tmp_path):
    """摘要的 `header_line` 会被出图 / 合画 / 提交那几处当 `header_row` 传回来重读：读出的必须是同一张表；用户另指一行就按他指的读。"""
    p = write(tmp_path, XRD, "xrd.txt")
    first = adf.inspect_data_source(p, None, v2=True)
    again = adf.inspect_data_source(p, None, v2=True, header_row=first.header_line)
    assert (again.columns, again.rows, again.no_header) == (first.columns, first.rows, True)
    other = adf.inspect_data_source(p, None, v2=True, header_row=2)
    assert not other.no_header and other.columns == XRD_ROWS[1] and len(other.rows) == 28


def test_no_header_generated_script_runs_and_draws(tmp_path):
    import subprocess

    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "xrd.txt").write_text(XRD, encoding="utf-8")
    profile = adf.profile_folder(folder)
    (entry,) = profile.tables
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "xrd.txt",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["col2"],
            "why": "说明",
            "x": "col1",
        }
    )
    rendered = tpl.render_recipe_script(entry, recipe, data_rel="xrd.txt", stem="xrd")
    assert "read_delimited(DATA, 'ws', 1, False, True)" in rendered.script
    work = tmp_path / "work"
    work.mkdir()
    (work / "xrd.txt").write_text(XRD, encoding="utf-8")
    (work / "xrd.py").write_text(rendered.script, encoding="utf-8")
    (work / rendered.runtime_name).write_bytes(rendered.runtime)
    out = subprocess.run(
        [sys.executable, "xrd.py"],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"MPLBACKEND": "Agg", "PATH": "", "HOME": str(tmp_path)},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert (work / "xrd.pdf").exists()


def test_propose_offers_a_line_for_the_headerless_xrd_scan(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "xrd.txt").write_text(XRD, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp
    rec = (
        next(i for i in resp["items"] if i.get("recommended"))
        if any(i.get("recommended") for i in resp["items"])
        else resp["items"][0]
    )
    assert rec["values"]["x"] == "col1" and rec["values"]["y"] == ["col2"]


def test_no_header_large_reduce_reads_the_same_rows_in_parent_and_script(tmp_path, monkeypatch):
    """大表缩减那一遍：父进程（`large_reduce`）与脚本里的 `read_reduced` 读同一个没有表头行的文件，缩减结果逐项一致。"""
    mod = runtime(tmp_path, monkeypatch)
    text = "".join(f"{i * 0.05:.2f}  {100 + (i * 7) % 40}\n" for i in range(400))
    p = write(tmp_path, text, "big.txt")
    monkeypatch.setattr(adf, "SMALL_ROWS", 100)
    values = adf.inspect_data_source(p, None, v2=True, allow_large=True)
    assert values.large is not None and values.no_header and values.columns == ["col1", "col2"]
    assert values.row_count == 400  # 第一行也算
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "big.txt",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["col2"],
            "why": "说明",
            "x": "col1",
        }
    )
    expr = tpl._large_reader_expr(adf.TableEntry("big.txt", None, False, values))
    assert expr == "read_reduced(DATA, 'ws', 1, False, 'utf-8', REDUCE, no_header=True)"
    rows, _info = tpl.large_reduce(values, recipe)
    spec = tpl.large_spec(values, recipe)
    header, srows = mod.read_reduced(p, "ws", 1, False, "utf-8", spec, no_header=True)
    assert header == ["col1", "col2"] and srows == rows


# =========================================================================== 4. 日期的写法
WHEN_YES = [
    ("2026-03-01", (2026, 3, 1)),
    ("2026-03-01 08:00", (2026, 3, 1, 8, 0, 0)),
    ("2026-03-01T08:00", (2026, 3, 1, 8, 0, 0)),
    ("2026-03-01T08:00:30", (2026, 3, 1, 8, 0, 30)),
    ("2026-03-01 23:59:59", (2026, 3, 1, 23, 59, 59)),
    ("2026/3/1", (2026, 3, 1)),
    ("2026/03/01", (2026, 3, 1)),
    ("2026年3月1日", (2026, 3, 1)),
    ("2026年12月31日", (2026, 12, 31)),
    ("  2026年3月1日  ", (2026, 3, 1)),
]
WHEN_NO = [
    "03/04/2026",  # 分不清日月：不猜
    "3/4/26",
    "04-03-2026",
    "2026-3-1",  # 横杠写法仍要求两位（与 `parse_date` 一致）
    "2026-02-30",  # 不存在的日子
    "2026/13/01",
    "2026-03-01 25:00",
    "2026-03-01T08:00Z",  # 时区后缀不认
    "2026-03-01T08:00+08:00",
    "2026-03-01 08",
    "2026年3月",
    "2026年3月1",
    "20260301",
    "March 1, 2026",
    "08:00",
    "",
]


@pytest.mark.parametrize(("text", "parts"), WHEN_YES)
def test_parse_when_reads_year_first_dates_with_optional_time(text, parts):
    import datetime as dt

    got = adf.parse_when(text)
    want = dt.datetime(*parts) if len(parts) == 6 else dt.date(*parts)
    assert got == want and type(got) is type(want)


@pytest.mark.parametrize("text", WHEN_NO)
def test_parse_when_does_not_guess_ambiguous_or_invalid_dates(text):
    assert adf.parse_when(text) is None


def test_the_v1_date_rule_is_unchanged():
    """v1 逐字不变：`parse_date` 只认 ISO 日期；v1 的摘要里新写法的列仍是文字。"""
    for text in ("2026年3月1日", "2026/3/1", "2026-03-01 08:00", "2026-03-01T08:00"):
        assert adf.parse_date(text) is None
    table = [["d", "v"], ["2026年3月1日", "1"], ["2026年3月2日", "2"]]
    v1 = adf._summarize(table, path_name="t", sha256="0", size=1)
    v2 = adf._summarize(table, path_name="t", sha256="0", size=1, parse=adf.parse_cell)
    assert v1.per_column["d"].kind == "text" and v1.per_column["d"].dates == 0
    assert v2.per_column["d"].kind == "date" and v2.per_column["d"].dates == 2


def test_a_time_of_day_is_a_fraction_of_a_day_so_same_day_rows_stay_apart(tmp_path, monkeypatch):
    mod = runtime(tmp_path, monkeypatch)
    base = adf.date_number(adf.parse_when("2026-03-01"))
    assert adf.date_number(adf.parse_when("2026-03-01 12:00")) == base + 0.5
    assert adf.date_number(adf.parse_when("2026-03-01 06:00:00")) == base + 0.25
    assert adf.date_number(adf.parse_when("2026年3月1日")) == adf.date_number(
        adf.parse_when("2026/03/01")
    )
    # 同一天三个不同时刻 = 三个不同的横坐标，不会被当成同一个 x 的重复测量
    rows = [["2026-03-01 08:00", "1"], ["2026-03-01 14:00", "2"], ["2026-03-01 20:00", "3"]]
    series = mod.trend_data(["t", "v"], rows, "t", ["v"], None, None, None, True)["series"]
    ((xs, vals),) = series.values()
    assert len(set(xs)) == 3 and vals == [1.0, 2.0, 3.0]
    # 父进程与脚本（公共模块）是同一个函数
    for text, _ in WHEN_YES:
        assert mod.parse_when(text) == adf.parse_when(text)
    for text in WHEN_NO:
        assert mod.parse_when(text) is None
    assert mod.date_number(mod.parse_when("2026-03-01 12:00")) == base + 0.5


@pytest.mark.parametrize(
    ("name", "text", "col"),
    [
        ("cn.csv", "日期,销量\n2026年1月1日,105\n2026年2月1日,116\n2026年3月1日,126\n", "日期"),
        ("slash.csv", "d,v\n2026/1/1,1\n2026/2/1,2\n2026/3/1,3\n", "d"),
        (
            "iso.csv",
            "timestamp,t\n2026-03-01 08:00,11.5\n2026-03-02 08:00,11.8\n2026-03-03T08:00,14.5\n",
            "timestamp",
        ),
    ],
)
def test_new_date_writings_make_the_column_a_date_column(tmp_path, name, text, col):
    s = adf.inspect_data_source(write(tmp_path, text, name), None, v2=True)
    assert s.per_column[col].kind == "date"


def test_a_column_with_an_ambiguous_date_among_good_ones_stays_text(tmp_path):
    p = write(tmp_path, "d,v\n2026/3/1,1\n03/04/2026,2\n2026/3/3,3\n", "mix.csv")
    s = adf.inspect_data_source(p, None, v2=True)
    assert s.per_column["d"].kind == "text" and s.per_column["d"].dates == 2


def test_date_column_with_times_is_drawn_as_a_line_in_order(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    text = "timestamp,temperature\n" + "".join(
        f"2026-03-0{1 + i // 3} {8 + 6 * (i % 3):02d}:00,{11 + i * 0.3:.1f}\n" for i in range(9)
    )
    (root / "data" / "t.csv").write_text(text, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates", resp
    rec = next((i for i in resp["items"] if i.get("recommended")), resp["items"][0])
    assert rec["values"]["x"] == "timestamp" and rec["title_code"] == "line_points"


def test_date_column_script_runs_and_draws_with_times_and_cn_dates(tmp_path):
    import subprocess

    folder = tmp_path / "f"
    folder.mkdir()
    text = "timestamp,temperature\n" + "".join(
        f"2026-03-0{1 + i // 3} {8 + 6 * (i % 3):02d}:00,{11 + i * 0.3:.1f}\n" for i in range(9)
    )
    (folder / "t.csv").write_text(text, encoding="utf-8")
    profile = adf.profile_folder(folder)
    (entry,) = profile.tables
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "t.csv",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["temperature"],
            "why": "说明",
            "x": "timestamp",
        }
    )
    rendered = tpl.render_recipe_script(entry, recipe, data_rel="t.csv", stem="t")
    work = tmp_path / "work"
    work.mkdir()
    (work / "t.csv").write_text(text, encoding="utf-8")
    (work / "t.py").write_text(rendered.script, encoding="utf-8")
    (work / rendered.runtime_name).write_bytes(rendered.runtime)
    out = subprocess.run(
        [sys.executable, "t.py"],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"MPLBACKEND": "Agg", "PATH": "", "HOME": str(tmp_path)},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert (work / "t.pdf").exists()


def test_x_labels_of_missing_error_bars_show_the_time_when_there_is_one():
    assert P._x_text(adf.date_number(adf.parse_when("2026-03-01")), True) == "2026-03-01"
    assert (
        P._x_text(adf.date_number(adf.parse_when("2026-03-01 08:00")), True) == "2026-03-01 08:00"
    )
    assert (
        P._x_text(adf.date_number(adf.parse_when("2026-03-01T08:00:30")), True)
        == "2026-03-01 08:00:30"
    )
    assert P._x_text(2.5, False) == "2.5"


# =========================================================================== 5. 整列带 % 的数值、分号 / 制表符文件里整列一致的小数逗号
PERCENT = "成分,占比\nSiO2,52.3%\nAl2O3,18.1%\nFe2O3,9.4%\nCaO,8.8%\nMgO,6.2%\n其它,5.2%\n"


def values_of(summary, col):
    """摘要里某一列每一格 `parse_cell` 的结果（类别, 值）。"""
    i = summary.columns.index(col)
    return [adf.parse_cell(r[i]) for r in summary.rows]


def test_a_column_that_is_all_percent_reads_as_numbers_without_dividing(tmp_path):
    s = adf.inspect_data_source(write(tmp_path, PERCENT), None, v2=True)
    assert s.per_column["占比"].kind == "numeric" and s.per_column["成分"].kind == "text"
    assert [v for _c, v in values_of(s, "占比")] == [52.3, 18.1, 9.4, 8.8, 6.2, 5.2]
    assert s.rows[0] == ["SiO2", "52.3%"]  # 原文照旧，只是认成了数


@pytest.mark.parametrize(
    "cells",
    [
        ["52.3%", "18.1", "9.4%"],  # 有一格不带 %
        ["52.3%", "18.1%", "多"],  # 有一格是文字
        ["52.3%", "%", "9.4%"],  # 只有 %
        ["0.52", "0.18", "0.09"],  # 没有 % 的小数
    ],
)
def test_a_column_that_is_not_consistently_percent_stays_text_or_numeric_as_before(tmp_path, cells):
    text = "k,v\n" + "".join(f"{'abcdef'[i]},{c}\n" for i, c in enumerate(cells))
    s = adf.inspect_data_source(write(tmp_path, text), None, v2=True)
    kinds = [c for c, _ in values_of(s, "v")]
    if cells[0] == "0.52":
        assert kinds == ["numeric"] * 3
    else:
        assert "text" in kinds and not any(isinstance(r[1], adf.NumberCell) for r in s.rows)


def test_percent_with_sign_and_space_and_blank_cells(tmp_path):
    s = adf.inspect_data_source(
        write(tmp_path, "k,v\na,-5.5 %\nb,\nc,+3%\nd,100%\n"), None, v2=True
    )
    nums = [(c, v) for c, v in values_of(s, "v")]
    assert nums[0] == ("numeric", -5.5) and nums[1][0] == "empty"
    assert nums[2] == ("numeric", 3.0) and nums[3] == ("numeric", 100.0)


def test_number_cell_keeps_its_tag_through_strip_and_copies():
    import copy
    import pickle

    c = adf.NumberCell("52.3%", 52.3)
    assert adf.parse_cell(c) == ("numeric", 52.3) and adf.parse_cell(c.strip()) == ("numeric", 52.3)
    assert adf.parse_cell("52.3%") == ("text", None)  # 单格看不出来：裸的 `52.3%` 仍是文字
    for d in (copy.copy(c), copy.deepcopy(c), pickle.loads(pickle.dumps(c))):
        assert adf.parse_cell(d) == ("numeric", 52.3) and str(d) == "52.3%"


def test_percent_script_runtime_reads_the_same_table(tmp_path, monkeypatch):
    mod = runtime(tmp_path, monkeypatch)
    p = write(tmp_path, PERCENT)
    parent = adf.inspect_data_source(p, None, v2=True)
    header, rows = mod.read_delimited(p, ",")
    assert header == parent.columns and rows == [list(r) for r in parent.rows]
    assert [mod.parse_cell(r[1]) for r in rows] == values_of(parent, "占比")


def _same_columns(small, large):
    """小表整份读与大表流式读的摘要：每列的类型与计数、数值范围（`large.stats`）一致。"""
    assert large.summary.large is not None
    assert large.summary.columns == small.summary.columns
    for name in small.summary.columns:
        assert large.summary.per_column[name].to_dict() == small.summary.per_column[name].to_dict()


def test_percent_streaming_reader_reads_the_same_columns_as_the_whole_file_reader(
    tmp_path, monkeypatch
):
    """大表是流式读，看不到整列：先预扫一遍定出整列判定（与小表同一个 `ColumnTagger`），再按它逐行读——读出同一张表。"""
    p = write(tmp_path, PERCENT)
    small = adf.read_text_table(p)
    large = read_large(p, monkeypatch)
    _same_columns(small, large)
    assert large.summary.per_column["占比"].kind == "numeric"
    assert large.summary.col_tags == {1: True}
    st = large.summary.large.stats["占比"]
    assert (st.lo, st.hi, st.numeric) == (5.2, 52.3, 6)


def test_large_scan_agrees_when_a_later_row_breaks_the_percent_column(tmp_path, monkeypatch):
    """开头几行都带 `%`、后面有一行不带：预扫的第二遍要发现它——整列不认，与小表一致（也验证开头筛选不会漏掉后面的反例）。"""
    rows = "".join(f"r{i},{i}%\n" for i in range(12)) + "last,0.5\n"
    p = write(tmp_path, "k,v\n" + rows)
    monkeypatch.setattr(adf, "TAG_HEAD_ROWS", 4)
    small = adf.read_text_table(p)
    large = read_large(p, monkeypatch)
    assert small.summary.per_column["v"].kind == "mixed"
    _same_columns(small, large)
    assert large.summary.col_tags == {}


def test_large_scan_skips_the_second_pass_when_no_column_can_qualify(tmp_path, monkeypatch):
    p = write(tmp_path, "k,v\n" + "".join(f"r{i},{i}.5\n" for i in range(30)))
    calls = []
    real = adf.ColumnTagger.feed
    monkeypatch.setattr(
        adf.ColumnTagger, "feed", lambda self, cells: (calls.append(1), real(self, cells))[1]
    )
    monkeypatch.setattr(adf, "TAG_HEAD_ROWS", 5)
    large = read_large(p, monkeypatch)
    assert large.summary.col_tags == {} and len(calls) == 5  # 只看了开头几行


EURO = "Zeit;Temperatur;Druck\n0,0;20,07;1013,0\n0,5;21,63;1012,2\n1,0;23,21;1011,4\n1,5;24,02;1010,9\n"


def test_a_semicolon_file_with_decimal_commas_reads_as_numbers(tmp_path):
    s = adf.inspect_data_source(write(tmp_path, EURO), None, v2=True)
    assert [s.per_column[c].kind for c in s.columns] == ["numeric"] * 3
    assert [v for _c, v in values_of(s, "Zeit")] == [0.0, 0.5, 1.0, 1.5]
    assert [v for _c, v in values_of(s, "Druck")] == [1013.0, 1012.2, 1011.4, 1010.9]
    assert s.rows[1] == ["0,5", "21,63", "1012,2"]  # 原文照旧


def test_decimal_commas_in_a_tab_separated_file_read_too(tmp_path):
    p = write(tmp_path, EURO.replace(";", "\t"), "t.tsv")
    s = adf.inspect_data_source(p, None, v2=True)
    assert [s.per_column[c].kind for c in s.columns] == ["numeric"] * 3
    assert values_of(s, "Temperatur")[1] == ("numeric", 21.63)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        # 有一格长得像千分位（`1,234`）：分不清是 1.234 还是 1234 → 整列不认（分号文件里仍是文字，不猜）
        ("a.csv", "k;v\na;1,5\nb;1,234\nc;2,25\n"),
        ("b.csv", "k;v\na;1,234\nb;5,678\nc;2,345\n"),
        # 有一格不是数
        ("c.csv", "k;v\na;1,5\nb;n/a\nc;2,25\n"),
        # 整列没有一个逗号
        ("d.csv", "k;v\na;1\nb;2\nc;3\n"),
        # 逗号是别的东西（两个逗号）
        ("e.csv", "k;v\na;1,5,6\nb;2,25\nc;3,5\n"),
    ],
)
def test_semicolon_columns_with_a_thousands_lookalike_or_other_text_stay_as_before(
    tmp_path, name, text
):
    s = adf.inspect_data_source(write(tmp_path, text, name), None, v2=True)
    assert not any(isinstance(r[1], adf.NumberCell) for r in s.rows)
    if name == "d.csv":
        assert s.per_column["v"].kind == "numeric"
    else:
        assert s.per_column["v"].kind in ("text", "mixed")


def test_tab_file_with_a_thousands_lookalike_still_reads_thousands(tmp_path):
    """制表符文件里 `1,000` 此前按千分位读成 1000：同一列里有这样的格子，整列不按小数逗号改读。"""
    s = adf.inspect_data_source(
        write(tmp_path, "k\tv\na\t1,000\nb\t2,500\nc\t3,250\n", "t.tsv"), None, v2=True
    )
    assert [v for _c, v in values_of(s, "v")] == [1000.0, 2500.0, 3250.0]


def test_decimal_commas_are_never_read_in_comma_or_space_separated_files(tmp_path):
    quoted = adf.inspect_data_source(
        write(tmp_path, 'k,v\na,"1,5"\nb,"2,25"\nc,"3,5"\n'), None, v2=True
    )
    assert quoted.per_column["v"].kind == "text"
    ws = adf.inspect_data_source(
        write(tmp_path, "k  v\na  1,5\nb  2,25\nc  3,5\n", "t.dat"), None, v2=True
    )
    assert ws.per_column["v"].kind == "text"


def test_decimal_comma_script_runtime_reads_the_same_table(tmp_path, monkeypatch):
    mod = runtime(tmp_path, monkeypatch)
    p = write(tmp_path, EURO)
    parent = adf.inspect_data_source(p, None, v2=True)
    header, rows = mod.read_delimited(p, ";")
    assert header == parent.columns and rows == [list(r) for r in parent.rows]
    assert [mod.parse_cell(r[2]) for r in rows] == values_of(parent, "Druck")


def test_percent_and_decimal_comma_files_get_candidates_and_scripts_that_run(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "euro.csv").write_text(EURO, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates", resp
    rec = next((i for i in resp["items"] if i.get("recommended")), resp["items"][0])
    assert rec["values"]["x"] == "Zeit" and set(rec["values"]["y"]) <= {"Temperatur", "Druck"}


def test_decimal_comma_generated_script_runs_and_draws(tmp_path):
    import subprocess

    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "euro.csv").write_text(EURO, encoding="utf-8")
    (entry,) = adf.profile_folder(folder).tables
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "euro.csv",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["Temperatur"],
            "why": "说明",
            "x": "Zeit",
        }
    )
    rendered = tpl.render_recipe_script(entry, recipe, data_rel="euro.csv", stem="euro")
    work = tmp_path / "work"
    work.mkdir()
    (work / "euro.csv").write_text(EURO, encoding="utf-8")
    (work / "euro.py").write_text(rendered.script, encoding="utf-8")
    (work / rendered.runtime_name).write_bytes(rendered.runtime)
    out = subprocess.run(
        [sys.executable, "euro.py"],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"MPLBACKEND": "Agg", "PATH": "", "HOME": str(tmp_path)},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert (work / "euro.pdf").exists()


def test_a_decimal_comma_column_may_also_hold_whole_numbers(tmp_path):
    s = adf.inspect_data_source(write(tmp_path, "k;v\na;0\nb;1,5\nc;2\nd;2,25\n"), None, v2=True)
    assert [v for _c, v in values_of(s, "v")] == [0.0, 1.5, 2.0, 2.25]


def test_decimal_comma_streaming_reader_reads_the_same_columns_as_the_whole_file_reader(
    tmp_path, monkeypatch
):
    p = write(tmp_path, EURO)
    small = adf.read_text_table(p)
    large = read_large(p, monkeypatch)
    _same_columns(small, large)
    assert large.summary.col_tags == {0: False, 1: False, 2: False}
    # 像千分位的整列、逗号分隔的文件：大表同样不认
    for name, text in (("a.csv", "k;v\na;1,5\nb;1,234\n"), ("b.csv", 'k,v\na,"1,5"\nb,"2,25"\n')):
        q = write(tmp_path, text, name)
        assert read_large(q, monkeypatch).summary.col_tags == {}


def test_large_reduce_with_column_tags_reads_the_same_rows_in_parent_and_script(
    tmp_path, monkeypatch
):
    """大表缩减：父进程（`large_reduce`）与脚本里的 `read_reduced`（`tags=` 字面量）读同一个文件，缩减结果逐项一致。"""
    mod = runtime(tmp_path, monkeypatch)
    text = "t;v\n" + "".join(
        f"{i * 0.5:.1f};{20 + (i * 7) % 13},{i % 10}\n".replace(".", ",", 1) for i in range(300)
    )
    p = write(tmp_path, text)
    monkeypatch.setattr(adf, "SMALL_ROWS", 100)
    values = adf.inspect_data_source(p, None, v2=True, allow_large=True)
    assert values.large is not None and values.col_tags == {0: False, 1: False}
    assert values.per_column["v"].kind == "numeric"
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "t.csv",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["v"],
            "why": "说明",
            "x": "t",
        }
    )
    expr = tpl._large_reader_expr(adf.TableEntry("t.csv", None, False, values))
    assert expr.endswith("REDUCE, tags={0: False, 1: False})")
    rows, _info = tpl.large_reduce(values, recipe)
    spec = tpl.large_spec(values, recipe)
    header, srows = mod.read_reduced(p, ";", None, False, "utf-8", spec, tags={0: False, 1: False})
    assert header == ["t", "v"] and srows == rows and rows
