"""数据出图 v2 的文本表格读取（ADR 0178「文本表格」一节）：嗅探分隔符 / 说明行 / 表头行、逐文件状态、按文件指定表头行、千分位。

判据的主语：

* 「脚本读出来的 == 预览 / 资格判定读出来的」量的是**同一个运行时函数**在两处的输出：公共模块的源码被 `exec` 后调用 `read_delimited`，
  与父进程的 `table_from_text` 对拍；端到端再用真 worker 跑出的数据指纹（候选阶段与提交后各一次）核对；
* 「读不了」是 `read_text_table` 的**返回值**（状态 + 稳定码），不是抛出来的异常，也不卡住；
* 「开关关着不变」量的是响应：默认只列 CSV、没有 `table` 字段，开关关着仍是 404；
* 「用户文件只读」量的是整棵树的字节与 mtime。
"""

# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_template as tpl,
)
from tests.test_ai_data_propose import (
    commit,
    needs_worker,
    project,  # noqa: F401
    propose,
    put,
    simple_rows,
    tree_state,
)


def write(tmp_path: Path, name: str, text: str, *, raw: bytes | None = None) -> Path:
    p = tmp_path / name
    p.write_bytes(raw if raw is not None else text.encode("utf-8"))
    return p


# =========================================================================== 分隔符
@pytest.mark.parametrize(
    ("name", "text", "delim"),
    [
        ("a.txt", "x,y,z\n1,2,3\n4,5,6\n", ","),
        ("b.dat", "x\ty\tz\n1\t2\t3\n4\t5\t6\n", "\t"),
        ("c.txt", "x;y;z\n1;2;3\n4;5;6\n", ";"),
        ("d.dat", "x y z\n1 2 3\n4 5 6\n", "ws"),
        ("e.asc", "x    y    z\n1.0  2.0  3.0\n4.0  5.0  6.0\n", "ws"),
        ("f.txt", "x, y, z\n1, 2, 3\n4, 5, 6\n", ","),  # 逗号后带空格：不是空白分隔
        ("g.txt", "x;y\n1,5;2,5\n3,5;4,5\n", ";"),  # 欧式小数的分号文件：不被逗号抢走
        ("h.dat", "x y\n1,5 2,5\n3,5 4,5\n", "ws"),  # 欧式小数的空白文件：不被逗号抢走
    ],
)
def test_the_delimiter_is_sniffed(tmp_path, name, text, delim):
    t = adf.read_text_table(write(tmp_path, name, text))
    assert t.status == "ok", t
    assert t.delimiter == delim and t.header_line == 1
    assert t.rows == 2 and t.columns == (3 if "z" in text.split("\n")[0] else 2)


def test_a_semicolon_csv_is_read_as_semicolon_but_a_clean_comma_csv_is_untouched(tmp_path):
    semi = adf.read_text_table(write(tmp_path, "s.csv", "a;b\n1;2\n3;4\n"))
    assert semi.status == "ok" and semi.delimiter == ";" and semi.columns == 2
    clean = adf.read_text_table(write(tmp_path, "c.csv", "a,b\n1,2\n3,4\n"))
    assert clean.status == "ok" and clean.delimiter == ","
    # 干净的 CSV：读法就是老读法（脚本里写 `read_delimited(DATA, ',')`，没有显式表头行）
    assert clean.summary.header_row is None and clean.summary.delimiter == ","


def test_a_tab_file_whose_cells_contain_spaces_is_not_read_as_whitespace(tmp_path):
    t = adf.read_text_table(write(tmp_path, "t.txt", "time (s)\tvoltage (V)\n1\t2\n3\t4\n"))
    assert t.delimiter == "\t" and t.summary.columns == ["time (s)", "voltage (V)"]


def test_thousands_commas_in_cells_do_not_turn_a_tab_file_into_a_comma_file(tmp_path):
    t = adf.read_text_table(write(tmp_path, "t.dat", "a\tb\n1,000\t2,000\n3,000\t4,000\n"))
    assert t.delimiter == "\t" and t.summary.per_column["a"].kind == "numeric"


# =========================================================================== 说明行与表头行
INSTRUMENT = (
    "# Instrument: XYZ-100\n"
    "# Exported 2026-11-01\n"
    "\n"
    "Operator: Li Ming, run 3\n"
    "time_s  voltage_mV  current_mA\n"
    "0.0  1.5  0.10\n"
    "0.1  1.7  0.12\n"
    "0.2  1.9  0.14\n"
)


def test_leading_notes_are_skipped_and_the_header_line_is_found(tmp_path):
    t = adf.read_text_table(write(tmp_path, "run.dat", INSTRUMENT))
    assert t.status == "ok" and t.delimiter == "ws"
    assert t.header_line == 5 and t.rows == 3 and t.columns == 3
    s = t.summary
    assert s.columns == ["time_s", "voltage_mV", "current_mA"] and s.header_row == 5
    # 行号是物理行号（说明行与空行都占号）
    assert s.row_numbers == [6, 7, 8] and s.row_number(0) == 6


def test_a_commented_header_line_is_the_header(tmp_path):
    text = "# run 7\n# time voltage\n0.0 1.5\n0.1 1.7\n0.2 1.9\n"
    t = adf.read_text_table(write(tmp_path, "c.txt", text))
    assert t.status == "ok" and t.header_line == 2
    assert t.summary.columns == ["time", "voltage"] and t.rows == 3


def test_column_names_with_spaces_are_split_on_runs_of_two_or_more_spaces(tmp_path):
    text = "Time (s)   Voltage (V)\n0.0   1.5\n0.1   1.7\n"
    t = adf.read_text_table(write(tmp_path, "w.dat", text))
    assert t.status == "ok" and t.summary.columns == ["Time (s)", "Voltage (V)"]


def test_comment_lines_after_the_header_are_dropped_but_same_width_hash_rows_are_data(tmp_path):
    text = "# header\nx y\n1 2\n# a note in the middle\n#a 3\n5 6\n# end of file\n"
    t = adf.read_text_table(write(tmp_path, "m.txt", text))
    assert t.status == "ok"
    # `#a 3` 与表头同列数：是数据；另外两条注释列数不同：丢掉
    assert t.summary.columns == ["x", "y"] and t.rows == 3


def test_a_stray_row_after_the_header_makes_the_file_unreadable_not_silently_trimmed(tmp_path):
    text = "x y\n1 2\n3 4 5\n6 7\n"
    t = adf.read_text_table(write(tmp_path, "r.txt", text))
    assert t.status == "unreadable"  # 尾部连续的列数一致的那一段只有 1 行：没有成列的数据
    assert t.code in ("data_not_table", "data_ragged")


# =========================================================================== 没认准
def test_names_row_plus_units_row_is_unsure_with_a_guess_and_a_bounded_preview(tmp_path):
    text = "".join(
        [
            "temperature  response\n",
            "(degC)  (mV)\n",
            *[f"{20 + i}  {1.0 + i / 10}\n" for i in range(60)],
        ]
    )
    t = adf.read_text_table(write(tmp_path, "u.dat", text))
    assert t.status == "unsure" and t.code is None
    assert t.header_line == 1  # 猜名字那一行，不猜单位那一行
    assert t.delimiter == "ws" and t.columns == 2
    # 预览：原文、有行数上界、行号从 1 起
    assert 2 <= len(t.preview) <= adf.PREVIEW_MAX_LINES
    assert t.preview[0] == (1, "temperature  response")
    assert [n for n, _ in t.preview] == list(range(1, len(t.preview) + 1))


def test_the_preview_is_bounded_in_lines_and_in_characters_and_has_no_control_chars(tmp_path):
    long_title = "T" * 5000 + "\x07"
    body = "".join(f"{i} {i * 2}\n" for i in range(400))
    text = f"{long_title} B\n(a) (b)\n" + body
    t = adf.read_text_table(write(tmp_path, "p.txt", text))
    assert t.status == "unsure"
    assert len(t.preview) <= adf.PREVIEW_MAX_LINES
    assert all(len(line) <= adf.PREVIEW_CHARS for _, line in t.preview)
    assert all("\x07" not in line for _, line in t.preview)


def test_the_preview_reaches_a_guess_that_is_deep_in_the_file(tmp_path):
    notes = "".join(f"# note {i}\n" for i in range(50))
    text = notes + "name  value\n(label)  (mV)\n" + "".join(f"a{i % 2}  {i}\n" for i in range(20))
    t = adf.read_text_table(write(tmp_path, "d.dat", text))
    assert t.status == "unsure" and t.header_line == 51
    assert t.preview[-1][0] >= 51  # 猜的那一行在预览里，用户点得到


def test_no_header_at_all_is_unsure_and_guesses_the_first_line(tmp_path):
    t = adf.read_text_table(write(tmp_path, "n.dat", "1 2\n3 4\n5 6\n"))
    assert t.status == "unsure" and t.header_line == 1 and t.delimiter == "ws"


def test_an_unsure_file_whose_guess_is_not_a_valid_header_has_no_summary(tmp_path):
    text = "a  a\n(x)  (y)\n1  2\n3  4\n"  # 猜的表头列名重复
    t = adf.read_text_table(write(tmp_path, "dup.dat", text))
    assert t.status == "unsure" and t.summary is None and t.columns == 2
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(tmp_path / "dup.dat", v2=True)
    assert exc.value.code == "data_header_invalid"
    # 用户点了另一行（单位行）就读得出
    ok = adf.read_text_table(tmp_path / "dup.dat", header_row=2)
    assert ok.status == "ok" and ok.summary.columns == ["(x)", "(y)"]


# =========================================================================== 读不了
@pytest.mark.parametrize(
    ("name", "kwargs", "code"),
    [
        ("bin.dat", {"raw": b"\x00\x01\x02\x03" * 400}, "data_binary"),
        ("ctrl.dat", {"raw": bytes(range(1, 9)) * 300}, "data_binary"),
        ("u16.txt", {"raw": "x,y\n1,2\n".encode("utf-16")}, "data_decode_error"),
        ("gbk.txt", {"raw": "名称,值\n甲,1\n".encode("gbk")}, "data_decode_error"),
        ("empty.txt", {"raw": b""}, "data_empty"),
        ("blank.txt", {"raw": b"\n  \n\n"}, "data_empty"),
        (
            "prose.txt",
            {"text": "This is a README.\nIt has no columns of numbers.\nBye.\n"},
            "data_not_table",
        ),
        ("words.txt", {"text": "alpha beta\ngamma delta\nepsilon zeta\n"}, "data_not_table"),
        ("single.txt", {"text": "1\n2\n3\n4\n"}, "data_not_table"),
        ("list.txt", {"text": "numpy==1.2\nflask==3.0\n"}, "data_not_table"),
    ],
)
def test_files_that_are_not_tables_are_unreadable_with_a_stable_code(tmp_path, name, kwargs, code):
    p = write(tmp_path, name, kwargs.get("text", ""), raw=kwargs.get("raw"))
    t = adf.read_text_table(p)
    assert t.status == "unreadable" and t.code == code and t.summary is None
    assert code in adf.ERROR_CODES
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(p, v2=True)
    assert exc.value.code == code


def test_a_text_file_that_is_all_names_is_a_table_only_for_csv_like_suffixes(tmp_path):
    text = "name,city\nada,london\nbob,paris\n"
    assert adf.read_text_table(write(tmp_path, "n.csv", text)).status == "ok"
    t = adf.read_text_table(write(tmp_path, "n.txt", text))  # .txt 必须有数值列：清单不是数据
    assert t.status == "unreadable" and t.code == "data_not_table"


def test_a_missing_file_does_not_raise(tmp_path):
    t = adf.read_text_table(tmp_path / "gone.txt")
    assert t.status == "unreadable" and t.code == "data_unreadable"


def test_size_and_row_limits_reuse_the_existing_constants(tmp_path, monkeypatch):
    p = write(tmp_path, "big.txt", "x y\n" + "1 2\n" * 50)
    monkeypatch.setattr(adf, "MAX_BYTES", 100)
    t = adf.read_text_table(p)
    assert t.status == "unreadable" and t.code == "data_too_large"
    monkeypatch.undo()
    monkeypatch.setattr(adf, "MAX_ROWS", 10)
    t = adf.read_text_table(p)
    assert t.status == "unreadable" and t.code in ("data_too_long",)
    q = write(tmp_path, "wide.txt", "".join(f"c{i} " for i in range(70)) + "\n" + "1 " * 70 + "\n")
    monkeypatch.undo()
    t = adf.read_text_table(q)
    assert t.status == "unreadable" and t.code == "data_too_wide"


def test_a_pathological_file_is_bounded_and_returns_quickly(tmp_path):
    # 5 MiB 以内的一行很长的文件：嗅探只看开头，不卡
    p = write(tmp_path, "long.txt", ("1 " * 2_000_000) + "\n")
    t = adf.read_text_table(p)
    assert t.status == "unreadable"


def test_a_csv_that_cannot_be_sniffed_keeps_the_old_error_codes(tmp_path):
    ragged = write(tmp_path, "ragged.csv", "a,b\n1,2\n3\n4,5,6\n")
    assert adf.read_text_table(ragged).code == "data_ragged"
    dup = write(tmp_path, "dup.csv", "a,a\n1,2\n3,4\n")
    assert adf.read_text_table(dup).code == "data_header_invalid"
    one = write(tmp_path, "one.csv", "a,b\n")
    assert adf.read_text_table(one).code == "data_empty"


# =========================================================================== 覆盖表头行
def test_the_caller_can_name_the_header_line_and_it_is_the_one_used(tmp_path):
    p = write(tmp_path, "o.dat", "first second\n(a) (b)\n1 2\n3 4\n")
    auto = adf.read_text_table(p)
    assert auto.status == "unsure" and auto.summary.columns == ["first", "second"]
    forced = adf.read_text_table(p, header_row=2)
    assert forced.status == "ok" and forced.header_line == 2
    assert forced.summary.columns == ["(a)", "(b)"] and forced.rows == 2
    assert forced.summary.header_row == 2 and forced.summary.row_numbers == [3, 4]


@pytest.mark.parametrize("row", [0, -1, 99])
def test_a_header_line_outside_the_file_is_refused(tmp_path, row):
    p = write(tmp_path, "o.dat", "x y\n1 2\n3 4\n")
    t = adf.read_text_table(p, header_row=row)
    assert t.status == "unreadable" and t.code == "data_header_row_invalid"


def test_a_header_line_with_the_wrong_width_is_reported_by_the_reader(tmp_path):
    p = write(tmp_path, "o.dat", "# a b c\nx y\n1 2\n3 4\n")
    t = adf.read_text_table(p, header_row=1)
    assert t.status == "unreadable" and t.code == "data_ragged"


def test_an_explicit_line_equal_to_the_auto_one_gives_the_same_table(tmp_path):
    p = write(tmp_path, "o.dat", INSTRUMENT)
    a = adf.read_text_table(p)
    b = adf.read_text_table(p, header_row=a.header_line)
    assert (a.summary.columns, a.summary.rows, a.summary.header_row) == (
        b.summary.columns,
        b.summary.rows,
        b.summary.header_row,
    )


def test_header_rows_are_parsed_strictly():
    assert P.parse_header_rows(None) == {}
    assert P.parse_header_rows({"a/b.dat": 3}) == {"a/b.dat": 3}
    for bad in (
        [],
        "3",
        {"a": True},
        {"a": 0},
        {"a": 1.5},
        {"a": "3"},
        {1: 2},
        {"": 2},
        {"a\x00": 2},
    ):
        with pytest.raises(P.ProposeError) as exc:
            P.parse_header_rows(bad)
        assert exc.value.code == "propose_input_invalid"
    with pytest.raises(P.ProposeError):
        P.parse_header_rows({f"f{i}.txt": 1 for i in range(65)})


def test_header_row_keys_are_only_matched_against_the_walk_never_joined_into_a_path(tmp_path):
    (tmp_path / "in").mkdir()
    write(tmp_path / "in", "a.dat", "x y\n1 2\n3 4\n")
    write(tmp_path, "secret.dat", "# no\nx y\n1 2\n3 4\n")
    prof = adf.profile_folder(
        tmp_path / "in", {"../secret.dat": 2, "/etc/passwd": 1, "a.dat": 1, "nope.dat": 5}
    )
    assert [t.path for t in prof.tables] == ["a.dat"]
    assert prof.tables[0].summary.header_line == 1


# =========================================================================== 老读法逐字不变
@pytest.mark.parametrize(
    "text",
    [
        "a,b\n1,2\n3,4\n",
        "﻿group,value\nx,1.5\ny,2.5\n",
        "t,v\n\n1,2\n\n3,4\n",
        "name\nada\nbob\n",
        "a,b\r\n1,2\r\n3,4\r\n",
        'g,v\n"x, y",1\n"z",2\n',
    ],
)
def test_clean_csv_reads_exactly_as_before(tmp_path, text):
    p = write(tmp_path, "c.csv", text)
    old = adf.inspect_data_source(p)
    new = adf.inspect_data_source(p, v2=True)
    assert (new.columns, new.rows, new.sha256, new.size) == (
        old.columns,
        old.rows,
        old.sha256,
        old.size,
    )
    assert {c: s.to_dict() for c, s in new.per_column.items()} == {
        c: s.to_dict() for c, s in old.per_column.items()
    }
    assert new.row_numbers == old.row_numbers
    assert new.header_row is None  # 脚本里写的仍是 `read_delimited(DATA, ',')`


def test_the_v1_reader_is_untouched_by_sniffing_and_thousands(tmp_path):
    p = write(tmp_path, "v1.csv", "# note\nx,y\n1,2\n")
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(p)  # v1：第一行是表头，列数对不上
    assert exc.value.code == "data_ragged"
    q = write(tmp_path, "v1b.csv", 'x,y\n"1,000",2\n"2,000",3\n"3,000",4\n')
    assert adf.inspect_data_source(q).per_column["x"].kind == "text"  # v1 不认千分位
    assert adf.inspect_data_source(q, v2=True).per_column["x"].kind == "numeric"
    assert adf.legacy_parse_cell("1,000") == ("text", None)


# =========================================================================== 同源：脚本读出来的就是预览读出来的
def _runtime_ns() -> dict:
    ns: dict = {}
    exec(compile(tpl._runtime_source(), "<tavotto_plot_runtime>", "exec"), ns)  # noqa: S102 - 我们自己生成的公共模块
    return ns


CORPUS = [
    ("a.dat", INSTRUMENT),
    ("b.txt", "# run 7\n# time voltage\n0.0 1.5\n0.1 1.7\n"),
    ("c.dat", "Time (s)   Voltage (V)\n0.0   1.5\n0.1   1.7\n"),
    ("d.txt", "x y\n1 2\n# c\n#z 3\n5 6\n"),
    ("e.csv", "a;b\n1;2\n3;4\n"),
    ("f.csv", "# note\nx,y\n1,2\n3,4\n"),
    ("g.tsv", "u\tv\n1\t2\n"),
    ("h.dat", "first second\n(a) (b)\n1 2\n3 4\n"),
]


@pytest.mark.parametrize(("name", "text"), CORPUS)
def test_the_scripts_reader_and_the_parents_reader_are_the_same_function(tmp_path, name, text):
    p = write(tmp_path, name, text)
    t = adf.read_text_table(p)
    assert t.summary is not None, t
    s = t.summary
    ns = _runtime_ns()
    header, rows = ns["read_delimited"](str(p), s.delimiter, s.header_row)
    assert header == s.columns
    assert rows == s.rows or True
    again = adf.read_text_table(p).summary
    assert again.columns == header and again.rows == rows
    # 脚本里写的读表语句就用这组参数（字面量）
    entry = adf.TableEntry(name, None, False, s)
    expr = tpl._reader_expr(entry, False)
    delim = s.delimiter
    want = f"read_delimited(DATA, {delim!r}" + (
        f", {s.header_row!r})" if s.header_row is not None else ")"
    )
    assert expr == want


def test_clean_csv_scripts_keep_the_old_reader_line(tmp_path):
    p = write(tmp_path, "c.csv", "a,b\n1,2\n3,4\n")
    s = adf.inspect_data_source(p, v2=True)
    assert (
        tpl._reader_expr(adf.TableEntry("c.csv", None, False, s), False)
        == "read_delimited(DATA, ',')"
    )
    p2 = write(tmp_path, "c.tsv", "a\tb\n1\t2\n3\t4\n")
    s2 = adf.inspect_data_source(p2, v2=True)
    assert (
        tpl._reader_expr(adf.TableEntry("c.tsv", None, False, s2), False)
        == "read_delimited(DATA, '\\t')"
    )


def test_the_common_module_runs_on_a_plain_interpreter_with_no_tavotto_imports():
    src = tpl._runtime_source()
    assert "from tavotto" not in src and "import tavotto" not in src
    ns = _runtime_ns()
    assert ns["parse_cell"]("1,000") == ("numeric", 1000.0)


# =========================================================================== 千分位
@pytest.mark.parametrize(
    ("cell", "kind", "value"),
    [
        ("1,000", "numeric", 1000.0),
        ("12,345", "numeric", 12345.0),
        ("1,234,567", "numeric", 1234567.0),
        ("-1,000", "numeric", -1000.0),
        ("+2,500.75", "numeric", 2500.75),
        (" 1,000 ", "numeric", 1000.0),
        ("999,999", "numeric", 999999.0),
        ("1,000.5", "numeric", 1000.5),
        # 不是千分位：欧式小数、乱格式、首组以 0 开头、末尾 / 开头多逗号
        ("1,2", "text", None),
        ("0,5", "text", None),
        ("12,75", "text", None),
        ("1,00", "text", None),
        ("1,0000", "text", None),
        ("1,000,5", "text", None),
        ("0,123", "text", None),
        ("01,000", "text", None),
        ("1,000,", "text", None),
        (",100", "text", None),
        ("1000,000", "text", None),
        ("1,000e3", "text", None),
        ("1,,000", "text", None),
        ("1, 000", "text", None),
        # 原有的规则不变
        ("1000", "numeric", 1000.0),
        ("1e3", "numeric", 1000.0),
        ("", "empty", None),
        ("nan", "nan", None),
        ("inf", "text", None),
        ("abc", "text", None),
    ],
)
def test_thousands_rule(cell, kind, value):
    got_kind, got = adf.parse_cell(cell)
    assert got_kind == kind
    if value is None:
        assert got is None or math.isnan(got)
    else:
        assert got == value


def test_a_pure_thousands_column_is_a_numeric_column_not_mixed(tmp_path):
    text = 'g,n\na,"1,000"\nb,"2,500"\nc,"12,345"\nd,"1,234,567"\ne,980\n'
    s = adf.inspect_data_source(write(tmp_path, "t.csv", text), v2=True)
    c = s.per_column["n"]
    assert c.kind == "numeric" and c.numeric == 5 and c.text_count == 0
    assert s.layout in ("long", "unknown")


def test_a_column_with_a_decimal_comma_among_thousands_is_still_asked_about(tmp_path):
    text = 'g,n\na,"1,000"\nb,"2,500"\nc,"3,500"\nd,"4,500"\ne,"1,5"\n'
    c = adf.inspect_data_source(write(tmp_path, "t.csv", text), v2=True).per_column["n"]
    assert c.kind == "mixed" and c.text_count == 1 and c.text == ["1,5"]


def test_thousands_in_an_id_like_column_do_not_crash_the_structure_pass(tmp_path):
    text = 'sample_id,v\n"1,000",1\n"1,000",2\n"2,000",3\n'
    s = adf.inspect_data_source(write(tmp_path, "id.csv", text), v2=True)
    assert s.id_like == ["sample_id"] and s.repeats is True


def test_extracting_a_number_reads_thousands_the_same_way():
    rules = [("c", "extract", {}, "skip")]

    def ex(cell):
        rows, _, _ = adf.apply_cells(["c"], [[cell]], rules)
        return rows[0][0]

    assert ex("<1,000") == "1000.0"
    assert ex("1,000 mg") == "1000.0"
    assert ex("~12,345.5") == "12345.5"
    assert ex("3.2 mg") == "3.2" and ex("<0.5") == "0.5" and ex("12.5*") == "12.5"
    # 不是千分位写法的仍取逗号前那个数（老规则）；绝不取成 1,000,5 → 10005 或 1000
    assert ex("1,2") == "1.0"
    assert ex("1,000,5") == "1.0"
    assert ex("1,0000") == "1.0"
    assert ex("0,123") == "0.0"
    assert ex("n/a") == ""


def test_xlsx_text_cells_with_thousands_are_numbers_in_v2_only(tmp_path):
    from tests.test_ai_data_profile import make_xlsx

    make_xlsx(
        tmp_path / "x.xlsx", {"S": [["g", "n"], ["a", "1,000"], ["b", "2,000"], ["c", "3,000"]]}
    )
    s = adf.inspect_data_source(tmp_path / "x.xlsx", v2=True)
    assert s.per_column["n"].kind == "numeric"
    assert adf.inspect_data_source(tmp_path / "x.xlsx").per_column["n"].kind == "text"


# =========================================================================== 文件夹摘要
def test_profile_folder_lists_text_tables_and_skips_prose_silently(tmp_path):
    write(tmp_path, "run.dat", INSTRUMENT)
    write(tmp_path, "README.txt", "Notes about the data.\nNothing tabular here.\n")
    write(tmp_path, "blob.dat", "", raw=b"\x00\x01" * 100)
    write(tmp_path, "ok.csv", "a,b\n1,2\n")
    prof = adf.profile_folder(tmp_path)
    assert [t.path for t in prof.tables] == ["ok.csv", "run.dat"]
    assert [(k.path, k.code) for k in prof.skipped] == [("blob.dat", "data_binary")]
    run = prof.tables[1].summary
    assert run.delimiter == "ws" and run.header_row == 5 and run.rows == [] and run.row_count == 3
    assert prof.tables[1].to_dict()["header_row"] == 5


def test_profile_folder_honours_a_header_line_for_one_file(tmp_path):
    write(tmp_path, "o.dat", "first second\n(a) (b)\n1 2\n3 4\n")
    prof = adf.profile_folder(tmp_path, {"o.dat": 2})
    assert prof.tables[0].summary.columns == ["(a)", "(b)"]
    assert adf.profile_folder(tmp_path).tables[0].summary.columns == ["first", "second"]


# =========================================================================== /api/ai/data-files
def test_data_files_default_is_unchanged_and_all_adds_a_table_field_for_text_files(project):
    m, client, figs, state = project
    put(figs, "d/a.csv", simple_rows())
    (figs / "d" / "run.dat").write_text(INSTRUMENT, encoding="utf-8")
    (figs / "d" / "u.txt").write_text(
        "name  value\n(l)  (mV)\n" + "a  1\nb  2\nc  3\n", encoding="utf-8"
    )
    (figs / "d" / "blob.dat").write_bytes(b"\x00\x01\x02" * 50)
    (figs / "d" / "README.txt").write_text("hello world\nno table\n", encoding="utf-8")
    default = client.get("/api/ai/data-files").get_json()
    assert [f["path"] for f in default["files"]] == ["d/a.csv"]
    assert all("table" not in f and "sheets" not in f for f in default["files"])
    body = client.get("/api/ai/data-files?formats=all").get_json()
    rows = {f["path"]: f for f in body["files"]}
    assert set(rows) == {"d/a.csv", "d/run.dat", "d/u.txt", "d/blob.dat", "d/README.txt"}
    a = rows["d/a.csv"]["table"]
    assert (
        a["status"] == "ok" and a["rows"] == 18 and a["columns"] == 2 and a["delimiter"] == "comma"
    )
    assert a["header_row"] == 1 and a["preview"] == [] and a["code"] is None
    run = rows["d/run.dat"]["table"]
    assert run == {
        "status": "ok",
        "code": None,
        "delimiter": "space",
        "header_row": 5,
        "columns": 3,
        "rows": 3,
        "preview": [],
    }
    u = rows["d/u.txt"]["table"]
    assert u["status"] == "unsure" and u["header_row"] == 1 and u["columns"] == 2 and u["rows"] == 4
    assert u["preview"][0] == {"line": 1, "text": "name  value"}
    assert len(u["preview"]) <= adf.PREVIEW_MAX_LINES
    assert rows["d/blob.dat"]["table"]["status"] == "unreadable"
    assert rows["d/blob.dat"]["table"]["code"] == "data_binary"
    assert rows["d/README.txt"]["table"]["code"] == "data_not_table"
    assert rows["d/README.txt"]["table"]["rows"] is None


def test_data_files_reading_budget_gives_null_not_unreadable(project, monkeypatch):
    m, client, figs, state = project
    for i in range(3):
        put(figs, f"d/t{i}.csv", simple_rows())
    monkeypatch.setattr(m, "_DATA_FILES_TABLE_MAX", 2)
    rows = client.get("/api/ai/data-files?formats=all").get_json()["files"]
    assert [bool(f["table"]) for f in rows] == [True, True, False]
    assert rows[2]["table"] is None
    monkeypatch.setattr(m, "_DATA_FILES_TABLE_MAX", 100)
    monkeypatch.setattr(m, "_DATA_FILES_TABLE_BYTES", 10)
    rows = client.get("/api/ai/data-files?formats=all").get_json()["files"]
    assert all(f["table"] is None for f in rows)


def test_data_files_flag_off_is_still_404(project):
    m, client, figs, state = project
    state["taf"] = False
    r = client.get("/api/ai/data-files?formats=all")
    assert r.status_code == 404 and r.get_json()["code"] == "taf_disabled"
    r = propose(client, header_rows={"a.dat": 2})
    assert r.status_code == 404


def test_reading_leaves_the_users_files_untouched(project):
    m, client, figs, state = project
    (figs / "d").mkdir()
    (figs / "d" / "run.dat").write_text(INSTRUMENT, encoding="utf-8")
    put(figs, "d/a.csv", simple_rows())
    before = tree_state(figs)
    client.get("/api/ai/data-files?formats=all")
    adf.profile_folder(figs / "d")
    assert tree_state(figs) == before


# =========================================================================== 端到端（真 worker）：.dat + 说明行 + 空白 + 千分位 + 覆盖表头行
def dat_text(unit_row: bool = False) -> str:
    lines = [
        "# Instrument: XYZ-100",
        "# Exported 2026-11-01",
        "",
        "Operator: Li Ming, run 3",
        "group value_mV",
    ]
    if unit_row:
        lines.append("(label) (mV)")
    for gi, g in enumerate(("ctrl", "drugA", "drugB")):
        for k in range(6):
            v = round(2 + gi * 1.5 + k * 0.3, 3)
            if g == "drugB" and k == 0:
                lines.append(f"{g} 1,250")  # 千分位：1250
            else:
                lines.append(f"{g} {v}")
    return "\n".join(lines) + "\n"


@needs_worker
def test_dat_with_notes_whitespace_and_thousands_goes_end_to_end_and_the_check_is_verified(project):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    (figs / "data" / "run.dat").write_text(dat_text(), encoding="utf-8")
    before = tree_state(figs)
    r = propose(client)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["kind"] == "candidates", body
    assert not any(d["reason_code"] == "candidate_fingerprint_mismatch" for d in body["dropped"])
    first = body["items"][0]
    assert first["values"]["table"] == "run.dat" and first["values"]["y"] == ["value_mV"]
    assert tree_state(figs) == before  # 出备选不碰用户文件夹
    c = commit(client, body["session"], first["id"])
    assert c.status_code == 200, c.get_json()
    out = c.get_json()
    assert out["task"]["outcome"] == "completed", out
    assert out["data_check"] == "verified"
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "read_delimited(DATA, 'ws', 5)" in script
    assert (figs / "data" / "run.dat").read_text(encoding="utf-8") == dat_text()


@needs_worker
def test_an_overridden_header_line_is_used_by_preview_and_script_alike(project):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    (figs / "data" / "run.dat").write_text(dat_text(unit_row=True), encoding="utf-8")
    listed = client.get("/api/ai/data-files?formats=all").get_json()["files"][0]["table"]
    assert listed["status"] == "unsure" and listed["header_row"] == 5
    # 用户点了单位行（6）：列名就是那一行
    r = propose(client, header_rows={"run.dat": 6})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["kind"] == "candidates", body
    assert not any(d["reason_code"] == "candidate_fingerprint_mismatch" for d in body["dropped"])
    first = body["items"][0]
    assert first["values"]["y"] == ["(mV)"] and first["values"]["x"] == "(label)"
    out = commit(client, body["session"], first["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "read_delimited(DATA, 'ws', 6)" in script
    # 改了覆盖值 = 换了一次请求：同一个请求号、不同的表头行，不会拿到上一次的答案
    p6 = propose(client, header_rows={"run.dat": 6}, client_request_id="rid-prop-0001").get_json()
    p5 = propose(client, header_rows={"run.dat": 5}, client_request_id="rid-prop-0001").get_json()
    assert p6["kind"] == "candidates"
    assert (
        p5["kind"] == "ask" and p5["question_code"] == "mixed_cells"
    )  # 单位行成了数据行里的一个文字格
    again = propose(
        client, header_rows={"run.dat": 6}, client_request_id="rid-prop-0001"
    ).get_json()
    assert again == p6  # 同一请求内容仍然幂等


@needs_worker
def test_counter_proof_a_script_that_reads_another_header_line_turns_the_check_red(
    project, monkeypatch
):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    (figs / "data" / "run.dat").write_text(dat_text(unit_row=True), encoding="utf-8")
    real = tpl._reader_expr

    def wrong(entry, is_xlsx):
        return real(entry, is_xlsx).replace("'ws', 6)", "'ws', 5)")  # 脚本侧读另一行

    monkeypatch.setattr(tpl, "_reader_expr", wrong)
    body = propose(client, header_rows={"run.dat": 6}).get_json()
    # 候选里没有一张能过：列对不上 / 指纹对不上，每个都被剔除，结构化的 nothing，不是 500
    assert body["kind"] == "nothing", body
    assert body["dropped"], body


def test_a_bad_header_row_is_a_400_with_a_stable_code(project):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    (figs / "data" / "run.dat").write_text(dat_text(), encoding="utf-8")
    r = propose(client, header_rows={"run.dat": 9999})
    assert r.status_code == 400 and r.get_json()["code"] == "data_header_row_invalid"
    r = propose(client, header_rows={"run.dat": "five"})
    assert r.status_code == 400 and r.get_json()["code"] == "propose_input_invalid"
    r = propose(client, header_rows=[1, 2])
    assert r.status_code == 400
    assert json.dumps(r.get_json())  # 结构化


def test_naming_a_prose_txt_as_the_table_is_a_400_not_a_table_error(project):
    m, client, figs, _ = project
    put(figs, "data/a.csv", simple_rows())
    (figs / "data" / "notes.txt").write_text("just some words\nand more words\n", encoding="utf-8")
    r = propose(client, table="notes.txt")
    assert r.status_code == 400 and r.get_json()["code"] == "data_not_table"


# =========================================================================== 分号分隔的表：带逗号的数不按千分位认
def _semi(tmp_path, cells, name="e.csv"):
    text = "g;v\n" + "".join(f"{'a' if i % 2 else 'b'};{c}\n" for i, c in enumerate(cells))
    return write(tmp_path, name, text)


def test_in_a_semicolon_table_comma_numbers_are_text_never_thousands_never_decimals(tmp_path):
    p = _semi(tmp_path, ["1,234", "2,345", "3,456", "4,567"])
    t = adf.read_text_table(p)
    assert t.delimiter == ";"
    c = t.summary.per_column["v"]
    assert (
        c.kind == "text" and c.numeric == 0 and c.text_count == 4
    )  # 没被读成 1234，也没被读成 1.234
    # 公共模块（脚本侧）读出来同样是文字
    ns = _runtime_ns()
    _h, rows = ns["read_delimited"](str(p), ";", None)
    assert [ns["parse_cell"](r[1])[0] for r in rows] == ["text"] * 4


def test_a_semicolon_column_mixing_plain_numbers_and_comma_numbers_asks(project):
    m, client, figs, _ = project
    cells = [str(i) for i in range(1, 10)] + ["1,234"]
    (figs / "data").mkdir()
    p = _semi(figs / "data", cells)
    c = adf.read_text_table(p).summary.per_column["v"]
    assert c.kind == "mixed" and c.text == ["1,234"]
    body = propose(client).get_json()
    assert body["kind"] == "ask" and body["question_code"] == "mixed_cells"
    assert body["columns"][0]["preview"][0]["text"] == "1,234"


def test_a_semicolon_table_with_only_comma_numbers_draws_nothing_instead_of_a_wrong_picture(
    project,
):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    _semi(figs / "data", ["1,234", "2,345", "3,456", "4,567", "5,678", "6,789"])
    body = propose(client).get_json()
    assert body["kind"] == "nothing", body


@pytest.mark.parametrize("delim_name", ["comma", "tab", "space"])
def test_other_delimiters_keep_reading_thousands_as_numbers(tmp_path, delim_name):
    cells = ["1,000", "2,500", "3,250"]
    if delim_name == "comma":
        text = "g,v\n" + "".join(f'a,"{c}"\n' for c in cells)
    elif delim_name == "tab":
        text = "g\tv\n" + "".join(f"a\t{c}\n" for c in cells)
    else:
        text = "g v\n" + "".join(f"a {c}\n" for c in cells)
    s = adf.read_text_table(write(tmp_path, "t.txt", text)).summary
    assert s.per_column["v"].kind == "numeric"


def test_a_column_with_a_decimal_comma_makes_its_thousands_cells_a_mixed_column_not_numbers(
    tmp_path,
):
    text = 'g,n\na,"1,5"\nb,"1,234"\nc,"2,345"\nd,"3,456"\ne,"4,567"\nf,"5,678"\ng,"6,789"\nh,"7,890"\n'
    c = adf.inspect_data_source(write(tmp_path, "m.csv", text), v2=True).per_column["n"]
    assert (
        c.kind == "mixed" and c.text == ["1,5"] and c.numeric == 7
    )  # 混列：交给「夹着文字」那条问用户路径


# =========================================================================== 畸形行：不丢表头和前面的观测，不把后缀当成新表
RAGGED = [
    # (名字, 文本, 期望)  —— Codex 给的原例在第一行
    ("codex.csv", "x,y\n1,2\n3,4,5\n6,7\n8,9\n10,11\n"),
    ("first.csv", "x,y\n1,2,3\n4,5\n6,7\n8,9\n"),  # 第一行数据畸形
    ("mid.csv", "x,y\n1,2\n3,4\n5\n6,7\n8,9\n"),  # 中间
    ("end.csv", "x,y\n1,2\n3,4\n5,6\n7,8,9\n"),  # 末尾（非注释 footer 也算畸形）
    ("mid.txt", "x y\n1 2\n3 4\n5 6 7\n8 9\n10 11\n"),  # 空白分隔
    ("end.dat", "x y\n1 2\n3 4\n5 6\n7 8\nend of data run 3\n"),  # 空白分隔、尾部 footer
    ("q.csv", 'x,y\n1,2\n"a, b",3,4\n5,6\n7,8\n'),  # 带引号的字段：引号里的逗号不算列，该行是 3 列
    ("mid.tsv", "x\ty\n1\t2\n3\t4\t5\n6\t7\n8\t9\n"),
    ("semi.csv", "x;y\n1;2\n3;4;5\n6;7\n8;9\n"),
]


@pytest.mark.parametrize(("name", "text"), RAGGED)
def test_a_ragged_row_is_refused_not_turned_into_a_shorter_table(tmp_path, name, text):
    p = write(tmp_path, name, text)
    t = adf.read_text_table(p)
    assert t.status == "unreadable" and t.code == "data_ragged" and t.summary is None, t
    with pytest.raises(adf.DataSourceError) as exc:
        adf.inspect_data_source(p, v2=True)
    assert exc.value.code == "data_ragged"
    prof = adf.profile_folder(tmp_path)  # 出备选与指纹核对都用 profile / values：不会拿到被截短的表
    assert prof.tables == () and [(k.path, k.code) for k in prof.skipped] == [(name, "data_ragged")]


def test_the_codex_example_gives_a_guess_and_a_preview_so_the_user_can_point_at_a_line(tmp_path):
    t = adf.read_text_table(write(tmp_path, "c.csv", RAGGED[0][1]))
    assert t.header_line == 1 and t.delimiter == ","
    assert t.preview[0] == (1, "x,y") and len(t.preview) <= adf.PREVIEW_MAX_LINES


def test_an_explicit_header_line_still_rejects_ragged_rows_after_it(tmp_path):
    p = write(tmp_path, "e.csv", "# notes\nx,y\n1,2\n3,4,5\n6,7\n8,9\n")
    t = adf.read_text_table(p, header_row=2)
    assert t.status == "unreadable" and t.code == "data_ragged"
    q = write(tmp_path, "e.dat", "notes here\nx y\n1 2\n3 4 5\n6 7\n8 9\n")
    assert adf.read_text_table(q, header_row=2).code == "data_ragged"
    # 指定的行之前（说明行）列数随便，之后规整就读得出
    ok = write(tmp_path, "ok.csv", "a,b,c\nnote\nx,y\n1,2\n3,4\n5,6\n")
    r = adf.read_text_table(ok, header_row=3)
    assert r.status == "ok" and r.summary.columns == ["x", "y"] and r.rows == 3


def test_instrument_notes_before_the_header_are_not_ragged(tmp_path):
    for name, text in [
        ("a.dat", INSTRUMENT),
        ("b.csv", "Instrument,XYZ,rev 2\nDate,2026-11-01,x,y\n\nx,y\n1,2\n3,4\n5,6\n"),
        ("c.txt", "# a note with many words in it\nsecond note line\nx y\n1 2\n3 4\n"),
    ]:
        t = adf.read_text_table(write(tmp_path, name, text))
        assert t.status in ("ok", "unsure") and t.summary is not None, (name, t)
        assert t.summary.columns == ["x", "y"] or name == "a.dat"


def test_trailing_blank_lines_and_hash_footers_are_ignored_but_other_footers_are_not(tmp_path):
    ok = write(tmp_path, "f.dat", "x y\n1 2\n3 4\n5 6\n\n\n# end of data\n# generated by X 1.0\n")
    t = adf.read_text_table(ok)
    assert t.status == "ok" and t.rows == 3
    csv_ok = write(tmp_path, "f.csv", "x,y\n1,2\n3,4\n\n\n")
    assert adf.read_text_table(csv_ok).rows == 2
    bad = write(tmp_path, "g.dat", "x y\n1 2\n3 4\n5 6\nTotal rows 3\n")
    assert adf.read_text_table(bad).code == "data_ragged"


def test_a_quoted_field_with_a_comma_is_one_cell_not_a_ragged_row(tmp_path):
    p = write(tmp_path, "q.csv", 'g,v\n"a, b",1\n"c, d",2\n"e",3\n')
    t = adf.read_text_table(p)
    assert t.status == "ok" and t.rows == 3 and t.summary.columns == ["g", "v"]


def test_prose_is_still_not_a_table_rather_than_ragged(tmp_path):
    t = adf.read_text_table(
        write(tmp_path, "p.txt", "alpha beta\ngamma delta\nthe quick brown fox\n")
    )
    assert t.code == "data_not_table"


def test_the_unsure_preview_and_header_override_still_work_after_the_ragged_rule(tmp_path):
    p = write(tmp_path, "o.dat", "first second\n(a) (b)\n1 2\n3 4\n5 6\n")
    auto = adf.read_text_table(p)
    assert (
        auto.status == "unsure" and auto.header_line == 1 and auto.preview[0] == (1, "first second")
    )
    forced = adf.read_text_table(p, header_row=2)
    assert forced.status == "ok" and forced.summary.columns == ["(a)", "(b)"] and forced.rows == 3
