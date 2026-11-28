# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「从数据到图」多认几种文件类型（ADR 0178 修订 2026-11-28）：`.prn` / `.xy`（走现有文本嗅探）与 `.jsonl` / `.ndjson`（JSON Lines）。

判据的主语：
1. 后缀白名单的**边界**——认的认、不认的（`.out` / `.json` / `.xlsm`）不进 `TABLE_SUFFIXES`；
2. JSON Lines 的读法：每个非空行一个**扁平对象**，列 = 键的并集（首次出现顺序），缺键 / null = 空格子，数字照原文，布尔 `true` / `false`；
   嵌套 / 不是对象 / 重复键 / 非标准记号 / 非 UTF-8 一律拒绝，不猜；
3. **父进程读出的表与生成脚本读出的表是同一张**（公共模块真的导入，用它读同一个文件，逐项对拍）；
4. 每种新类型都有「能给出候选图、生成的脚本能独立跑出图」的端到端用例。

没有网络、没有计时；写文件一律 `encoding="utf-8"`。
"""

from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
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


def write(tmp_path, name, text, encoding="utf-8"):
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


def script_table(tmp_path, monkeypatch, path):
    """脚本读这个文件会得到的表：把 `_reader_expr` 生成的那一句真的在公共模块里执行。"""
    mod = runtime(tmp_path, monkeypatch)
    summ = adf.inspect_data_source(path, None, v2=True)
    entry = adf.TableEntry(path.name, None, False, summ)
    expr = tpl._reader_expr(entry, False)
    ns = {k: getattr(mod, k) for k in dir(mod) if not k.startswith("__")}
    ns["DATA"] = path
    return summ, expr, eval(expr, ns)  # noqa: S307 - 表达式由 Tavotto 自己生成


# =========================================================================== 1. 后缀白名单的边界
@pytest.mark.parametrize("suffix", [".prn", ".xy", ".jsonl", ".ndjson"])
def test_new_suffixes_are_table_suffixes(suffix):
    assert suffix in adf.TABLE_SUFFIXES


@pytest.mark.parametrize("suffix", [".out", ".json", ".xlsm", ".log", ".ods"])
def test_other_suffixes_stay_out_of_this_change(suffix):
    assert suffix not in adf.TABLE_SUFFIXES


def test_the_column_limit_default_matches_the_parent_limit():
    assert adf.jsonl_text.__defaults__ == (adf.MAX_COLUMNS,)


# =========================================================================== 2. `.prn` / `.xy`
PRN = "time  temp\n0  20.1\n1  20.5\n2  21.0\n3  21.4\n"
XY = "".join(f"{10 + 0.05 * i:.2f}  {100 + (i * 7) % 40}\n" for i in range(30))


def test_prn_with_a_header_reads_through_the_text_sniffer(tmp_path):
    t = adf.read_text_table(write(tmp_path, "run.prn", PRN))
    assert t.status == "ok" and t.summary.columns == ["time", "temp"]
    assert t.summary.rows == [["0", "20.1"], ["1", "20.5"], ["2", "21.0"], ["3", "21.4"]]


def test_xy_without_a_header_gets_col_names_like_txt(tmp_path):
    a = adf.read_text_table(write(tmp_path, "scan.xy", XY))
    b = adf.read_text_table(write(tmp_path, "scan.txt", XY))
    assert a.summary.columns == ["col1", "col2"] and a.summary.no_header
    assert (a.status, a.summary.columns, a.summary.rows) == (
        b.status,
        b.summary.columns,
        b.summary.rows,
    )


@pytest.mark.parametrize("name", ["notes.prn", "notes.xy"])
def test_prose_with_these_suffixes_is_not_a_table(tmp_path, name):
    t = adf.read_text_table(write(tmp_path, name, "hello world this is prose\nmore prose here\n"))
    assert (t.status, t.code) == ("unreadable", "data_not_table")


def test_a_binary_prn_is_unreadable_and_prose_is_not_listed_in_a_folder(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "ok.prn").write_text(PRN, encoding="utf-8")
    (folder / "prose.xy").write_text("just some words\nand more words\n", encoding="utf-8")
    (folder / "bin.prn").write_bytes(b"\x00\x01\x02" * 50)
    prof = adf.profile_folder(folder)
    assert [e.path for e in prof.tables] == ["ok.prn"]
    assert [(s.path, s.code) for s in prof.skipped] == [("bin.prn", "data_binary")]


@pytest.mark.parametrize("name,text", [("run.prn", PRN), ("scan.xy", XY)])
def test_prn_and_xy_script_runtime_reads_the_same_table(tmp_path, monkeypatch, name, text):
    p = write(tmp_path, name, text)
    summ, expr, (header, rows) = script_table(tmp_path, monkeypatch, p)
    assert expr.startswith("read_delimited(DATA, 'ws'")
    assert header == summ.columns and rows == [list(r) for r in summ.rows]


def _run_script(tmp_path, rendered, data_name, data_text, stem):
    work = tmp_path / "work"
    work.mkdir()
    (work / data_name).write_text(data_text, encoding="utf-8")
    (work / f"{stem}.py").write_text(rendered.script, encoding="utf-8")
    (work / rendered.runtime_name).write_bytes(rendered.runtime)
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


def test_prn_generated_script_runs_and_draws(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "run.prn").write_text(PRN, encoding="utf-8")
    (entry,) = adf.profile_folder(folder).tables
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "run.prn",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["temp"],
            "why": "说明",
            "x": "time",
        }
    )
    rendered = tpl.render_recipe_script(entry, recipe, data_rel="run.prn", stem="run")
    _run_script(tmp_path, rendered, "run.prn", PRN, "run")


def test_propose_offers_candidates_for_prn_and_xy(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "scan.xy").write_text(XY, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp
    assert resp["table"] == "scan.xy"
    (root / "data" / "scan.xy").unlink()
    (root / "data" / "run.prn").write_text(PRN, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp
    assert resp["table"] == "run.prn"


# =========================================================================== 3. JSON Lines 的读法
LOG = (
    '{"t": 0, "v": 1.50, "g": "a", "ok": true}\n'
    "\n"
    '{"t": 1, "v": 1e5, "g": "b", "extra": null}\n'
    '{"t": 2, "v": 12345678901234567890, "g": "a"}\n'
    '{"t": 3, "v": -0.0, "g": "b", "ok": false}\n'
)


@pytest.mark.parametrize("name", ["log.jsonl", "log.ndjson", "LOG.JSONL"])
def test_jsonl_reads_the_union_of_keys_numbers_verbatim(tmp_path, name):
    t = adf.read_text_table(write(tmp_path, name, LOG))
    assert t.status == "ok", t
    assert t.summary.columns == ["t", "v", "g", "ok", "extra"]
    assert t.summary.rows == [
        ["0", "1.50", "a", "true", ""],  # 1.50 保留原文；布尔是 true / false 文字
        ["1", "1e5", "b", "", ""],  # 缺键与 null 都是空格子
        ["2", "12345678901234567890", "a", "", ""],  # 大整数不经 float 往返
        ["3", "-0.0", "b", "false", ""],
    ]
    assert t.delimiter == "," and t.summary.header_line == 1


def test_jsonl_strings_are_verbatim_including_commas_quotes_and_unicode(tmp_path):
    text = '{"name": "a,b", "note": "say \\"hi\\"", "zh": "温度", "n": 1}\n{"name": "c", "note": "x", "zh": "y", "n": 2}\n'
    t = adf.read_text_table(write(tmp_path, "s.jsonl", text))
    assert t.summary.rows == [["a,b", 'say "hi"', "温度", "1"], ["c", "x", "y", "2"]]


def test_jsonl_accepts_a_bom_crlf_and_blank_lines(tmp_path):
    raw = "﻿" + LOG.replace("\n", "\r\n")
    p = write(tmp_path, "b.jsonl", raw)
    t = adf.read_text_table(p)
    assert t.status == "ok" and t.encoding == "utf-8-bom" and len(t.summary.rows) == 4


def test_jsonl_columns_follow_first_appearance_order(tmp_path):
    text = '{"b": 1, "a": 2}\n{"c": 3, "a": 4, "b": 5}\n'
    t = adf.read_text_table(write(tmp_path, "o.jsonl", text))
    assert t.summary.columns == ["b", "a", "c"]
    assert t.summary.rows == [["1", "2", ""], ["5", "4", "3"]]


def test_jsonl_physical_line_numbers_are_the_source_line_plus_one(tmp_path):
    t = adf.read_text_table(write(tmp_path, "n.jsonl", LOG))
    assert t.summary.row_numbers == [
        2,
        4,
        5,
        6,
    ]  # 表头占第 1 行；原文第 1、3、4、5 行各 +1，空行（原文第 2 行）保留


@pytest.mark.parametrize(
    "name,text,code",
    [
        ("nested_obj", '{"a": 1, "b": {"c": 2}}\n{"a": 2, "b": {"c": 3}}\n', "data_malformed"),
        ("nested_arr", '{"a": 1, "b": [1, 2]}\n{"a": 2, "b": [3]}\n', "data_malformed"),
        ("not_object_list", "[1, 2]\n[3, 4]\n", "data_malformed"),
        ("not_object_scalar", "1\n2\n3\n", "data_malformed"),
        ("mixed", '{"a": 1}\n[2]\n', "data_malformed"),
        ("dup_key", '{"a": 1, "a": 2}\n{"a": 3}\n', "data_malformed"),
        ("nan", '{"a": NaN}\n{"a": 1}\n', "data_malformed"),
        ("infinity", '{"a": Infinity}\n{"a": 1}\n', "data_malformed"),
        ("neg_infinity", '{"a": -Infinity}\n', "data_malformed"),
        ("bad_json", '{"a": 1}\n{"a": \n', "data_malformed"),
        ("pretty_printed", '{\n  "a": 1\n}\n', "data_malformed"),
        ("trailing_comma", '{"a": 1,}\n', "data_malformed"),
        ("nul_in_string", '{"a": "x\\u0000y"}\n{"a": "z"}\n', "data_malformed"),
        ("lone_surrogate", '{"a": "x\\ud800y"}\n{"a": "z"}\n', "data_malformed"),
        ("hash_first_cell", '{"a": "# note", "b": 1}\n{"a": "x", "b": 2}\n', "data_malformed"),
        ("hash_first_key", '{"#a": 1, "b": 1}\n{"#a": 2, "b": 2}\n', "data_malformed"),
        ("only_empty_objects", "{}\n{}\n", "data_empty"),
        ("only_blank", "\n\n   \n", "data_empty"),
    ],
)
def test_jsonl_refuses_what_it_cannot_read_exactly(tmp_path, name, text, code):
    t = adf.read_text_table(write(tmp_path, f"{name}.jsonl", text))
    assert (t.status, t.code) == ("unreadable", code), t
    assert t.summary is None


def test_a_json_array_file_with_a_jsonl_name_is_refused(tmp_path):
    p = write(tmp_path, "arr.jsonl", '[{"a": 1}, {"a": 2}]\n')
    t = adf.read_text_table(p)
    assert (t.status, t.code) == ("unreadable", "data_malformed")


def test_jsonl_too_many_columns_is_refused_not_truncated(tmp_path):
    wide = {f"c{i}": i for i in range(adf.MAX_COLUMNS + 1)}
    t = adf.read_text_table(write(tmp_path, "w.jsonl", json.dumps(wide) + "\n"))
    assert (t.status, t.code) == ("unreadable", "data_too_wide")
    # 刚好上限：能读
    ok = {f"c{i}": i for i in range(adf.MAX_COLUMNS)}
    t = adf.read_text_table(write(tmp_path, "w2.jsonl", json.dumps(ok) + "\n"))
    assert t.status in ("ok", "unsure") and len(t.summary.columns) == adf.MAX_COLUMNS


def test_a_late_rare_key_that_pushes_past_the_column_limit_refuses_the_whole_file(tmp_path):
    rows = [json.dumps({f"c{i}": 1 for i in range(adf.MAX_COLUMNS)}) for _ in range(5)]
    rows.append(json.dumps({"c0": 1, "rare": 2}))
    t = adf.read_text_table(write(tmp_path, "r.jsonl", "\n".join(rows) + "\n"))
    assert (t.status, t.code) == ("unreadable", "data_too_wide")


@pytest.mark.parametrize("encoding", ["utf-16", "gb18030"])
def test_jsonl_only_accepts_utf8(tmp_path, encoding):
    p = tmp_path / "d"
    p.mkdir()
    f = p / "e.jsonl"
    f.write_bytes('{"名字": "温度", "v": 1}\n{"名字": "湿度", "v": 2}\n'.encode(encoding))
    t = adf.read_text_table(f)
    assert t.status == "unreadable" and t.code in ("data_decode_error", "data_binary"), t


def test_jsonl_has_no_large_table_path(tmp_path, monkeypatch):
    p = write(tmp_path, "l.jsonl", LOG)
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    t = adf.read_text_table(p)
    assert (t.status, t.code) == ("unreadable", "data_too_large")  # 比 CSV 更严，不是更松


def test_jsonl_row_limit_matches_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(adf, "SMALL_ROWS", 10)
    text = "".join(f'{{"a": {i}}}\n' for i in range(40))
    t = adf.read_text_table(write(tmp_path, "m.jsonl", text))
    assert (t.status, t.code) == ("unreadable", "data_too_large")


def test_jsonl_header_row_hint_is_ignored_not_misapplied(tmp_path):
    p = write(tmp_path, "h.jsonl", LOG)
    a = adf.read_text_table(p, header_row=3)
    b = adf.read_text_table(p)
    assert a.status == b.status == "ok"
    assert a.summary.columns == b.summary.columns and a.summary.rows == b.summary.rows


def test_jsonl_original_file_is_untouched(tmp_path):
    p = write(tmp_path, "keep.jsonl", LOG)
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    adf.read_text_table(p)
    adf.inspect_data_source(p, None, v2=True)
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before


# =========================================================================== 4. 父进程与脚本读同一张表
@pytest.mark.parametrize("text", [LOG, '{"b": 1, "a": 2}\n{"c": 3, "a": 4, "b": 5}\n'])
def test_jsonl_script_runtime_reads_the_same_table(tmp_path, monkeypatch, text):
    p = write(tmp_path, "t.jsonl", text)
    summ, expr, (header, rows) = script_table(tmp_path, monkeypatch, p)
    assert expr == "table_from_text(jsonl_text(DATA.read_bytes().decode('utf-8-sig')), ',')[:2]"
    assert header == summ.columns and rows == [list(r) for r in summ.rows]


def test_jsonl_script_runtime_reads_a_bom_file_the_same(tmp_path, monkeypatch):
    p = write(tmp_path, "t.ndjson", "﻿" + LOG)
    summ, _expr, (header, rows) = script_table(tmp_path, monkeypatch, p)
    assert header == summ.columns and rows == [list(r) for r in summ.rows]


def test_jsonl_script_runtime_refuses_what_the_parent_refuses(tmp_path, monkeypatch):
    mod = runtime(tmp_path, monkeypatch)
    for text in ('{"a": {"b": 1}}\n', '{"a": 1, "a": 2}\n', '{"a": NaN}\n', "[1]\n", "{}\n"):
        with pytest.raises(mod.JsonlError):
            mod.jsonl_text(text)
        with pytest.raises(adf.JsonlError):
            adf.jsonl_text(text)


def test_the_runtime_module_carries_the_new_function_and_the_json_import():
    src = tpl.runtime_module()[1].decode("utf-8")
    assert "def jsonl_text(" in src and "class JsonlError(" in src and "import json\n" in src
    assert adf.jsonl_text in tpl.RUNTIME_FUNCTIONS and adf.JsonlError in tpl.RUNTIME_FUNCTIONS


# =========================================================================== 5. 文件夹、概览、候选图、脚本
def test_a_folder_lists_good_jsonl_and_skips_bad_ones_with_a_code(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "good.jsonl").write_text(LOG, encoding="utf-8")
    (folder / "good2.ndjson").write_text(LOG, encoding="utf-8")
    (folder / "nested.jsonl").write_text('{"a": {"b": 1}}\n', encoding="utf-8")
    (folder / "package.json").write_text('{"name": "x", "version": "1"}\n', encoding="utf-8")
    prof = adf.profile_folder(folder)
    assert [e.path for e in prof.tables] == ["good.jsonl", "good2.ndjson"]
    assert [(s.path, s.code) for s in prof.skipped] == [("nested.jsonl", "data_malformed")]


def test_data_files_and_overview_list_jsonl(project):
    m, client, figs, _ = project
    d = figs / "data"
    d.mkdir()
    (d / "log.jsonl").write_text(LOG, encoding="utf-8")
    (d / "bad.jsonl").write_text("[1]\n", encoding="utf-8")
    files = client.get("/api/ai/data-files?formats=all").get_json()["files"]
    by = {f["path"].split("/")[-1]: f for f in files}
    assert by["log.jsonl"]["table"]["status"] == "ok"
    assert by["bad.jsonl"]["table"]["status"] == "unreadable"
    assert by["bad.jsonl"]["table"]["code"] == "data_malformed"
    r = client.get("/api/ai/data-overview?path=data/log.jsonl")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert [f["name"] for f in body["fields"]] == ["t", "v", "g", "ok", "extra"]


SERIES = "".join(
    json.dumps({"day": i, "level": round(1.0 + 0.1 * i + (0.05 if i % 2 else 0), 3), "tank": "A"})
    + "\n"
    for i in range(12)
)


def test_jsonl_generated_script_runs_and_draws(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "series.jsonl").write_text(SERIES, encoding="utf-8")
    (entry,) = adf.profile_folder(folder).tables
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "series.jsonl",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["level"],
            "why": "说明",
            "x": "day",
        }
    )
    rendered = tpl.render_recipe_script(entry, recipe, data_rel="series.jsonl", stem="series")
    assert "jsonl_text(" in rendered.script
    _run_script(tmp_path, rendered, "series.jsonl", SERIES, "series")


@pytest.mark.parametrize("name", ["series.jsonl", "series.ndjson"])
def test_propose_offers_candidates_for_jsonl(project, tmp_path, name):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / name).write_text(SERIES, encoding="utf-8")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp
    assert resp["table"] == name
