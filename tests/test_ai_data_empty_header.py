# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""表头第一格是空的（pandas `df.to_csv()` 把行索引写成第一列）也能读（ADR 0178 修订 2026-11-27）。

判据的主语：**表头行里哪一格是空的**，不是整张表好不好。

* 只放宽**第一列**：它叫 `col1`（与 `xvg_text` 的缺名列同一个叫法）；别处的空名字、重复名字（含与 `col1` 撞名）仍是 `data_header_invalid`；
  整行都没有名字的行不可能是表头（全空记录读表时就被丢掉）。
* **父进程与生成的脚本读出同一张表**：占位名由运行时函数 `fill_header` 算（公共模块内嵌同一份源码）；用例把公共模块真的导入、
  用它读同一个文件，与父进程读出的表头逐项对拍。
* 小表（整份读）、大表（流式）、XLSX 三条读表路径各一条。
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
from tests.test_ai_data_profile import make_xlsx
from tests.test_ai_data_propose import project  # noqa: F401

PANDAS = (
    ",id,diet,pulse,time,kind\n"
    "0,1,low fat,85,1 min,rest\n"
    "1,1,low fat,85,15 min,rest\n"
    "2,1,low fat,88,30 min,rest\n"
    "3,2,no fat,90,1 min,rest\n"
    "4,2,no fat,92,15 min,rest\n"
    "5,2,no fat,93,30 min,rest\n"
)
WANT = ["col1", "id", "diet", "pulse", "time", "kind"]


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


def write(tmp_path, text, name="t.csv"):
    folder = tmp_path / "d"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# =========================================================================== 1. 能读、叫 col1
def test_pandas_index_export_reads_with_a_placeholder_first_column(tmp_path):
    info = adf.read_text_table(write(tmp_path, PANDAS))
    assert info.status == "ok" and info.summary is not None
    assert info.summary.columns == WANT
    assert len(info.summary.rows) == 6 and info.summary.rows[0][:3] == ["0", "1", "low fat"]


def test_quoted_empty_header_as_written_by_r_reads_too(tmp_path):
    text = '"","a","b"\n"1",2,3\n"2",4,5\n"3",6,7\n'
    info = adf.read_text_table(write(tmp_path, text))
    assert info.status == "ok" and info.summary.columns == ["col1", "a", "b"]


def test_tab_and_explicit_header_row_paths_fill_it_too(tmp_path):
    p = write(tmp_path, "notes about the run\n\ta\tb\n1\t2\t3\n2\t4\t5\n", "t.tsv")
    info = adf.read_text_table(p, header_row=2)
    assert info.status == "ok" and info.summary.columns == ["col1", "a", "b"]


def test_a_guessed_header_with_an_empty_first_cell_is_still_unsure(tmp_path):
    """两条以上候选表头、没有一条像名字时是猜的：能读了也仍是 `unsure`（让用户点一下确认），不因此变成 `ok`。"""
    p = write(tmp_path, "run 7,,\n,a,b\n1,2,3\n2,4,5\n3,6,7\n")
    info = adf.read_text_table(p)
    assert info.status == "unsure"


def test_the_overview_reads_it_and_names_the_column(tmp_path):
    write(tmp_path, PANDAS)
    profile = adf.profile_folder(tmp_path / "d")
    assert [t.summary.columns for t in profile.tables] == [WANT]


# =========================================================================== 2. 仍拒绝的
@pytest.mark.parametrize(
    "text",
    [
        ",,,\n1,2,3\n",  # 全空：全空记录读表时被丢掉，剩不出表头
        "a,,c\n1,2,3\n4,5,6\n",  # 中间一格空
        "a,b,\n1,2,3\n4,5,6\n",  # 末尾一格空
        ",,c\n1,2,3\n4,5,6\n",  # 第一格空 + 另一格也空：只放宽第一列
        "a,b,a\n1,2,3\n4,5,6\n",  # 重名
        ",a,a\n1,2,3\n4,5,6\n",  # 补了 col1 之后别处仍重名
        ",col1,b\n1,2,3\n4,5,6\n",  # 与已有列撞名：不另起名、不静默覆盖
    ],
    ids=["all-empty", "middle", "trailing", "two-empty", "dup", "dup-after-fill", "collide-col1"],
)
def test_other_bad_headers_are_still_rejected(tmp_path, text):
    p = write(tmp_path, text)
    info = adf.read_text_table(p)
    assert info.summary is None
    with pytest.raises(adf.DataSourceError):
        adf.inspect_data_source(p, None, v2=True)


def test_an_explicit_title_row_with_blank_cells_is_still_rejected(tmp_path):
    """用户指定的行是「标题行，后面都是空格子」：只有第一格空的才放宽，这一行不是。"""
    p = write(tmp_path, "run 7,,,\na,b,c\n1,2,3\n2,4,5\n")
    info = adf.read_text_table(p, header_row=1)
    assert info.status == "unreadable" and info.code == "data_header_invalid"


# =========================================================================== 3. 父进程与脚本同源
def _runtime(tmp_path, monkeypatch):
    name, data = tpl.runtime_module()
    path = tmp_path / "rt" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, path.stem, mod)  # dataclass 要按模块名找回自己
    spec.loader.exec_module(mod)
    return mod


def test_the_generated_scripts_runtime_reads_the_same_header(tmp_path, monkeypatch):
    mod = _runtime(tmp_path, monkeypatch)
    assert "def fill_header" in tpl.runtime_module()[1].decode()  # 公共模块里内嵌的就是这个函数
    p = write(tmp_path, PANDAS)
    parent = adf.inspect_data_source(p, None, v2=True)
    header, rows = mod.read_delimited(p, ",")
    assert header == parent.columns == WANT
    assert rows == [list(r) for r in parent.rows]


def test_the_runtime_module_name_follows_its_content(tmp_path):
    """公共模块按内容寻址：加了 `fill_header`，名字换成新的一份；脚本 import 的是生成时那一份，老脚本不受影响。"""
    name, data = tpl.runtime_module()
    import hashlib

    assert name == f"{tpl.RUNTIME_PREFIX}{hashlib.sha256(data).hexdigest()[:8]}.py"


def test_xlsx_with_an_empty_a1_reads_the_same_in_parent_and_script(tmp_path, monkeypatch):
    rows = [["", "a", "b"], [1, 2, 3], [2, 4, 5], [3, 6, 7]]
    p = make_xlsx(tmp_path / "t.xlsx", {"S": rows})
    parent = adf.inspect_data_source(p, "S", v2=True)
    assert parent.columns == ["col1", "a", "b"]
    header, _body = _runtime(tmp_path, monkeypatch).read_xlsx(p, 0)
    assert header == parent.columns


# =========================================================================== 4. 大表（流式）
def test_the_streaming_reader_agrees_on_the_placeholder(tmp_path, monkeypatch):
    p = write(tmp_path, PANDAS)
    small = adf.read_text_table(p)
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    large = adf.read_text_table(p)
    monkeypatch.undo()
    assert large.summary is not None and large.summary.large is not None  # 确实走了流式读
    assert large.status == small.status == "ok"
    assert large.summary.columns == small.summary.columns == WANT


# =========================================================================== 5. 端到端出候选
def test_propose_offers_candidates_for_a_pandas_export(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "t.csv").write_text(PANDAS, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp
