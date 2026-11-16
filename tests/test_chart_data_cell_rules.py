"""chartdata 的单元格判据以 0178 的 parse_cell / parse_date 为唯一出处（#1877）。"""

from __future__ import annotations

import pytest

from tavotto.engine import chartdata


def codes(parsed: dict) -> list[tuple[str, int, int]]:
    return [(issue["code"], issue["row"], issue["col"]) for issue in parsed["issues"]]


@pytest.mark.parametrize(
    ("cell", "value"),
    [("1_000", 1000.0), ("1_0_0", 100.0), ("1e3", 1000.0), (" 2.5 ", 2.5)],
)
def test_numbers_follow_parse_cell(cell, value):
    parsed = chartdata.parse_table(f"x\n{cell}\n")
    assert parsed["issues"] == []
    assert parsed["columns"] == {"x": [value]}


@pytest.mark.parametrize("cell", ["NaN", "nan"])
def test_nan_is_a_missing_value_not_text(cell):
    parsed = chartdata.parse_table(f"x\n{cell}\n1.0\n")
    assert codes(parsed) == [("data_missing", 2, 1)]
    assert parsed["columns"] == {}
    assert "x" not in parsed["text_columns"]


@pytest.mark.parametrize("cell", ["Infinity", "inf", "1e999", "-1e999"])
def test_non_finite_literals_are_diagnosed(cell):
    parsed = chartdata.parse_table(f"x\n{cell}\n")
    assert codes(parsed) == [("data_non_finite", 2, 1)]
    assert parsed["columns"] == {}


def test_thousands_separator_is_reported_not_guessed():
    parsed = chartdata.parse_table("x\ty\n1,234.5\t2.0\n")
    assert ("data_thousands_separator", 2, 1) in codes(parsed)
    assert "x" not in parsed["columns"]


@pytest.mark.parametrize("cell", ["2024-01-01", "2024/01/02", "01/02/2024"])
def test_dates_are_named_not_silently_converted(cell):
    parsed = chartdata.parse_table(f"x\n{cell}\n")
    assert codes(parsed) == [("data_not_numeric", 2, 1)]
    assert "date-like" in parsed["issues"][0]["detail"]


def test_empty_cell_is_missing():
    parsed = chartdata.parse_table("x,y\n1,\n")
    assert codes(parsed) == [("data_missing", 2, 2)]


def test_other_text_is_not_numeric():
    parsed = chartdata.parse_table("x\nabc\n")
    assert codes(parsed) == [("data_not_numeric", 2, 1)]


def test_grid_uses_the_same_rules():
    assert chartdata.parse_grid("1_000 2\n")["values"] == [[1000.0, 2.0]]
    assert chartdata.parse_grid("NaN\n")["issues"] == [
        {"code": "data_missing", "row": 1, "col": 1, "detail": "grid cell 'NaN'"}
    ]
    assert chartdata.parse_grid("Infinity\n")["issues"] == [
        {"code": "data_non_finite", "row": 1, "col": 1, "detail": "grid cell 'Infinity'"}
    ]
    assert chartdata.parse_grid("2024-01-01\n")["issues"] == [
        {"code": "data_not_numeric", "row": 1, "col": 1, "detail": "grid cell '2024-01-01'"}
    ]


def test_grid_overflowing_literal_is_not_silently_inf():
    parsed = chartdata.parse_grid("1e999\n")
    assert codes(parsed) == [("data_non_finite", 1, 1)]
    assert parsed["values"] == []


def test_column_limit_is_64():
    header = "\t".join(f"c{i}" for i in range(64))
    row = "\t".join("1" for _ in range(64))
    parsed = chartdata.parse_table(f"{header}\n{row}\n")
    assert parsed["issues"] == []
    assert len(parsed["columns"]) == 64


def test_too_wide_rejects_the_whole_table():
    header = "\t".join(f"c{i}" for i in range(65))
    row = "\t".join("1" for _ in range(65))
    parsed = chartdata.parse_table(f"{header}\n{row}\n")
    assert codes(parsed) == [("data_too_wide", 1, 1)]
    assert parsed["columns"] == {}
    assert parsed["text_columns"] == {}


def test_row_limit_is_100_000():
    parsed = chartdata.parse_table("x\n" + "1\n" * 100_000)
    assert parsed["issues"] == []
    assert len(parsed["columns"]["x"]) == 100_000


def test_too_long_rejects_the_whole_table():
    parsed = chartdata.parse_table("x\n" + "1\n" * 100_001)
    assert codes(parsed) == [("data_too_long", 1, 1)]
    assert parsed["columns"] == {}


def test_grid_limits():
    wide = " ".join("1" for _ in range(65))
    assert codes(chartdata.parse_grid(wide + "\n")) == [("data_too_wide", 1, 1)]
    assert codes(chartdata.parse_grid("1\n" * 100_001)) == [("data_too_long", 1, 1)]
