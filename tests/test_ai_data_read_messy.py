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
    ai_data_template as tpl,
)
from tavotto.engine.agent_runner import wires
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
