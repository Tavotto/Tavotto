"""TAF 数据出图 v2「合画」（ADR 0178 2026-11-18 修订）：几份列名相同的数据拼成一张逻辑表，每份文件是一组。真 app + 真 safe worker。

判据的主语：

* 「用户的数据只读」量的是**文件系统事实**：原件逐字节 + mtime 与提交前相同，提交只在落点的 `tavotto_figures/` 里新建；
* 「画的就是这几份数据」量的是真 worker 里跑出的 manifest 指纹（候选阶段与提交后各一次）、以及脱离 Tavotto 在别的 cwd 里独立重跑；
* 「单份不变」量的是单份请求的响应键集合与已有测试（`test_ai_data_propose.py` 等整份）全过，不是这里再抄一遍。
"""

# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_merge as M,
    ai_data_propose as P,
    ai_data_template as tpl,
    ai_history,
    discover,
)
from tests.test_ai_data_profile import STYLES, C, make_xlsx
from tests.test_ai_data_propose import (
    WORKER_PY,
    checks_of,
    commit,
    needs_worker,
    project,  # noqa: F401
    propose,
    put,
    tree_state,
)


def dose_rows(offset: float = 0.0):
    """每份一个实验条件：剂量（横轴）与响应；列名与顺序在各份里相同。"""
    return [
        ["dose_uM", "response_mV"],
        *[[d, round(1 + offset + d * 0.5, 3)] for d in (1, 2, 3, 4, 5, 6)],
    ]


def value_rows(offset: float = 0.0):
    """只有一个数值列的表：合画后唯一的分组列就是合成的文件列，没有横轴。"""
    return [["value_mV"], *[[round(2 + offset + k * 0.3 + (k % 3) * 0.1, 3)] for k in range(6)]]


def three(figs: Path, rows=dose_rows, folder="data"):
    for i, name in enumerate(("ctrl", "drugA", "drugB")):
        put(figs, f"{folder}/{name}.csv", rows(i * 1.5))
    return [{"path": f"{n}.csv"} for n in ("ctrl", "drugA", "drugB")]


def mp(client, tables, **kw):
    """合画出备选：同一份回答「测的量是响应列」（两个数值列时单份也要问一次）；用例自己给的 `answers` 并在上面。"""
    answers = {"y": "response_mV", **(kw.pop("answers", None) or {})}
    return propose(client, tables=tables, answers=answers, **kw)


def run_standalone(script: Path, cwd: Path, tmp: Path):
    env = {**os.environ, "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(tmp / "mpl")}
    return subprocess.run(
        [WORKER_PY, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        cwd=str(cwd),
    )


# =========================================================================== 出备选
@pytest.mark.parametrize("render", [None, "lazy"])
@needs_worker
def test_two_files_with_the_same_columns_become_one_figure_grouped_by_file(project, render):
    m, client, figs, _ = project
    put(figs, "data/ctrl.csv", dose_rows(0))
    put(figs, "data/drug.csv", dose_rows(2))
    before = tree_state(figs)
    r = mp(client, [{"path": "ctrl.csv"}, {"path": "drug.csv"}], render=render)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["kind"] == "candidates", body
    assert body["group_column"] == "file"
    assert [(s["path"], s["label"]) for s in body["sources"]] == [
        ("ctrl.csv", "ctrl"),
        ("drug.csv", "drug"),
    ]
    first = body["items"][0]
    assert first["recommended"] and first["values"]["group"] == "file"
    assert first["values"]["x"] == "dose_uM" and first["values"]["y"] == ["response_mV"]
    assert first["values"]["table"] == "ctrl.csv"
    # 每个备选里凡是有分组的，分组都是文件列；没有一个备选把文件列当数值
    for item in body["items"]:
        assert "file" not in item["values"]["y"]
        assert item["values"]["group"] in ("file", None) or item["values"]["x"] == "file"
    t = client.get(first["thumbnail_url"])
    assert t.status_code == 200 and t.data.startswith(b"\x89PNG")
    assert tree_state(figs) == before, "出备选不碰用户文件夹"
    if render == "lazy":
        assert body["roles"]["group"] == "file" and body["roles"]["options"]["group"] == ["file"]


@needs_worker
def test_three_files_with_one_value_column_compare_the_files(project):
    m, client, figs, _ = project
    tables = three(figs, value_rows)
    body = mp(client, tables).get_json()
    assert body["kind"] == "candidates", body
    first = body["items"][0]
    assert first["values"]["x"] == "file" and first["values"]["y"] == ["value_mV"]
    assert [s["label"] for s in body["sources"]] == ["ctrl", "drugA", "drugB"]


@needs_worker
def test_the_user_can_still_change_the_group_role(project):
    m, client, figs, _ = project
    tables = three(figs)
    body = mp(client, tables, render="lazy", answers={"group": "none"}).get_json()
    assert body["kind"] == "candidates", body
    for item in body["items"]:
        assert item["values"]["group"] != "file"
    assert body["roles"]["group"] is None
    # 默认（不指定）= 合成的文件列
    assert mp(client, tables, render="lazy").get_json()["items"][0]["values"]["group"] == "file"


def test_lazy_records_the_default_group_only_when_a_question_was_replaced(project):
    m, client, figs, _ = project
    # 文件里本来就有一个分组列：不指定时原本要问「按哪一列分组」，这里替用户选了合成列并写进 defaults
    rows = [
        ["drug", "response_mV"],
        *[[g, 1 + k * 0.5 + i] for i, g in enumerate("ab") for k in range(5)],
    ]
    put(figs, "data/x.csv", rows)
    put(figs, "data/y.csv", rows)
    body = mp(client, [{"path": "x.csv"}, {"path": "y.csv"}], render="lazy").get_json()
    assert body["kind"] == "candidates", body
    assert {"question_code": "pick_group", "answer": {"group": "file"}} in body["defaults"]
    # 单份同样的数据：不带这一条（单份流程逐字不变）
    single = propose(client, table="x.csv", render="lazy").get_json()
    assert not any(
        d["question_code"] == "pick_group" and d.get("answer") == {"group": "file"}
        for d in single["defaults"]
    )


# =========================================================================== 请求形状与错误
def test_tables_and_table_together_or_a_bad_shape_is_an_input_error(project):
    m, client, figs, _ = project
    three(figs)
    both = propose(client, table="ctrl.csv", tables=[{"path": "ctrl.csv"}, {"path": "drugA.csv"}])
    assert both.status_code == 400 and both.get_json()["code"] == "propose_input_invalid"
    for bad in (
        "ctrl.csv",
        [],
        [{"path": "ctrl.csv"}],
        [{"path": "ctrl.csv"}, "drugA.csv"],
        [{"path": "ctrl.csv"}, {"path": ""}],
        [{"path": "ctrl.csv"}, {"path": "drugA.csv", "extra": 1}],
        [{"path": "ctrl.csv"}, {"path": "ctrl.csv"}],  # 同一份给了两次
    ):
        r = propose(client, tables=bad)
        assert r.status_code == 400 and r.get_json()["code"] == "propose_input_invalid", bad


def test_no_table_and_no_tables_is_still_the_folder_flow(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", [["group", "value_mV"], *[[g, k] for g in "ab" for k in range(5)]])
    r = propose(client)
    assert r.status_code == 200 and r.get_json()["kind"] in ("candidates", "ask", "nothing")


def test_different_columns_or_order_is_tables_columns_differ(project):
    m, client, figs, _ = project
    put(figs, "data/a.csv", dose_rows(0))
    put(figs, "data/b.csv", [["response_mV", "dose_uM"], *[[r[1], r[0]] for r in dose_rows(1)[1:]]])
    put(figs, "data/c.csv", [["dose_uM", "other_mV"], *dose_rows(0)[1:]])
    put(figs, "data/d.csv", dose_rows(3))
    for names, index in ((("a", "b"), 1), (("a", "c"), 1), (("a", "d", "c"), 2)):
        r = mp(client, [{"path": f"{n}.csv"} for n in names])
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["code"] == "tables_columns_differ"
        assert r.get_json()["params"] == {"index": index}


def test_too_many_files_is_tables_limit_exceeded_and_the_cap_itself_is_fine(project):
    m, client, figs, _ = project
    n = M.MAX_TABLES
    for i in range(n + 1):
        put(figs, f"data/f{i:02d}.csv", dose_rows(i))
    over = mp(client, [{"path": f"f{i:02d}.csv"} for i in range(n + 1)])
    assert over.status_code == 400 and over.get_json()["code"] == "tables_limit_exceeded"
    assert over.get_json()["params"] == {"max": n}
    ok = mp(client, [{"path": f"f{i:02d}.csv"} for i in range(n)], render="lazy")
    got = ok.get_json()
    # 上限 = 配方里颜色分组的上限：恰好这么多份时四个备选一个都不丢（再多一份趋势 / 关系图的颜色分组就画不了，实测 `group_limit_exceeded`）
    assert got["kind"] == "candidates" and len(got["items"]) == 4 and got["dropped"] == [], got


def test_one_unreadable_file_keeps_the_single_file_code_and_shape(project):
    m, client, figs, _ = project
    put(figs, "data/a.csv", dose_rows(0))
    (figs / "data" / "bad.csv").write_bytes(b"\x00\x01\x02\xff" * 200)
    (figs / "data" / "empty.csv").write_text("dose_uM,response_mV\n", encoding="utf-8")
    single_bad = propose(client, table="bad.csv")
    multi_bad = mp(client, [{"path": "a.csv"}, {"path": "bad.csv"}])
    assert multi_bad.status_code == single_bad.status_code == 400
    assert multi_bad.get_json()["code"] == single_bad.get_json()["code"]
    single_empty = propose(client, table="empty.csv")
    multi_empty = mp(client, [{"path": "a.csv"}, {"path": "empty.csv"}])
    assert multi_empty.status_code == single_empty.status_code
    assert multi_empty.get_json()["code"] == single_empty.get_json()["code"]
    # 不存在的文件 = 单份指名一张不存在的表：200 + nothing / table_not_found
    gone = mp(client, [{"path": "a.csv"}, {"path": "nope.csv"}]).get_json()
    assert gone == {"kind": "nothing", "reasons": ["table_not_found"], "dropped": []}


def test_paths_outside_the_folder_are_refused(project, tmp_path):
    m, client, figs, _ = project
    put(figs, "data/a.csv", dose_rows(0))
    (tmp_path / "outside.csv").write_text("dose_uM,response_mV\n1,2\n", encoding="utf-8")
    for bad in ("../outside.csv", "/etc/passwd", "C:\\x.csv"):
        r = mp(client, [{"path": "a.csv"}, {"path": bad}])
        assert r.status_code == 400 and r.get_json()["code"] == "data_path_outside_project", bad


def test_the_total_size_is_capped(project, monkeypatch):
    m, client, figs, _ = project
    three(figs)
    monkeypatch.setattr(M, "MAX_TOTAL_BYTES", 100)
    r = mp(client, [{"path": "ctrl.csv"}, {"path": "drugA.csv"}])
    assert r.status_code == 400 and r.get_json()["code"] == "data_too_large"


def test_a_user_header_row_applies_to_that_file_only(project):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    (figs / "data" / "a.csv").write_text(
        "operator,lab\nnote,x\ndose_uM,response_mV\n1,2\n2,3\n3,4\n4,5\n5,6\n", encoding="utf-8"
    )
    put(figs, "data/b.csv", dose_rows(1))
    body = propose(
        client,
        tables=[{"path": "a.csv"}, {"path": "b.csv"}],
        header_rows={"a.csv": 3},
        render="lazy",
    ).get_json()
    assert body["kind"] == "candidates", body
    assert [s["header_row"] for s in body["sources"]] == [3, 1]


# =========================================================================== 标签与列名（纯函数）
def test_labels_are_the_stems_and_unique_even_for_same_names():
    f = M.unique_labels
    assert f([("a/ctrl.csv", None), ("a/drug.csv", None)]) == ["ctrl", "drug"]
    assert f([("exp1/data.csv", None), ("exp2/data.csv", None)]) == ["exp1/data", "exp2/data"]
    assert f([("x/exp1/data.csv", None), ("y/exp1/data.csv", None)]) == [
        "x/exp1/data",
        "y/exp1/data",
    ]
    # 同一个工作簿的几张表：带上工作表名
    assert f([("w.xlsx", "S1"), ("w.xlsx", "S2")]) == ["w (S1)", "w (S2)"]
    # 同一文件夹里 a.csv 与 a.tsv：父文件夹区分不了，改用完整文件名
    assert f([("d/a.csv", None), ("d/a.tsv", None)]) == ["a.csv", "a.tsv"]
    # 不区分大小写
    got = f([("A.csv", None), ("sub/a.csv", None)])
    assert len({x.casefold() for x in got}) == 2


@pytest.mark.parametrize("name", ["1", "10", "2025-01-03", "nan", "1e3", "0.5"])
def test_a_label_that_would_parse_as_a_number_or_date_falls_back_to_the_file_name(name):
    labels = M.unique_labels([(f"{name}.csv", None), ("z.csv", None)])
    assert labels == [f"{name}.csv", "z.csv"]
    for lab in labels:
        assert adf.parse_cell(lab)[0] == "text" and adf.parse_date(lab) is None


def test_labels_are_always_unique_and_plain_text_under_odd_names():
    items = [
        ("a.csv", None),
        ("A.CSV", None),
        ("d/a.csv", None),
        ("d/a.tsv", None),
        ("e/a.csv", None),
        ("w.xlsx", "S"),
        ("w.xlsx", "s"),
        ("1.csv", None),
        ("a\nb.csv", None),
    ]
    labels = M.unique_labels(items)
    assert len({x.casefold() for x in labels}) == len(items), labels
    assert all("\n" not in x and adf.parse_cell(x)[0] == "text" for x in labels)


def test_the_synthetic_column_name_never_clashes():
    assert M.column_name(["a", "b"]) == "file"
    assert M.column_name(["a", "File"]) == "file_2"
    assert M.column_name(["file", "file_2"]) == "file_3"


def test_common_parent_and_rebase():
    assert M.common_parent(["a/x.csv", "a/y.csv"]) == "a"
    assert M.common_parent(["a/b/x.csv", "a/c/y.csv"]) == "a"
    assert M.common_parent(["x.csv", "a/y.csv"]) == ""
    assert M.common_parent(["a/b/x.csv", "a/b/c/y.csv"]) == "a/b"
    assert M.rebase("a/b/x.csv", "a") == "b/x.csv" and M.rebase("x.csv", "") == "x.csv"


def test_merge_tables_is_the_one_runtime_function_and_refuses_misaligned_tables():
    h, rows = tpl.merge_tables(
        [(["a", "b"], [["1", "2"]]), (["a", "b"], [["3", "4"], ["5", "6"]])], ["p", "q"], "file"
    )
    assert h == ["a", "b", "file"] and rows == [["1", "2", "p"], ["3", "4", "q"], ["5", "6", "q"]]
    with pytest.raises(ValueError, match="tables_columns_differ"):
        tpl.merge_tables([(["a", "b"], []), (["b", "a"], [])], ["p", "q"], "file")
    assert tpl.merge_tables in tpl.RUNTIME_FUNCTIONS
    assert "def merge_tables" in tpl.runtime_module()[1].decode("utf-8")


def test_a_file_column_that_already_exists_gets_a_suffix(project):
    m, client, figs, _ = project
    for n, off in (("a", 0), ("b", 2)):
        put(
            figs,
            f"data/{n}.csv",
            [["file", "dose_uM", "response_mV"], *[["x", d, d + off] for d in range(1, 7)]],
        )
    body = mp(client, [{"path": "a.csv"}, {"path": "b.csv"}], render="lazy").get_json()
    assert body["kind"] in ("candidates", "nothing"), body
    assert body["group_column"] == "file_2"


def test_numeric_file_names_still_group_by_file(project):
    m, client, figs, _ = project
    for n in ("1", "2"):
        put(figs, f"data/{n}.csv", value_rows(int(n)))
    body = mp(client, [{"path": "1.csv"}, {"path": "2.csv"}], render="lazy").get_json()
    assert body["kind"] == "candidates", body
    assert [s["label"] for s in body["sources"]] == ["1.csv", "2.csv"]
    assert body["items"][0]["values"]["x"] == "file"


# =========================================================================== 提交
@needs_worker
def test_commit_creates_one_script_reads_the_originals_and_verifies_the_data(project, tmp_path):
    m, client, figs, _ = project
    tables = three(figs)
    before = tree_state(figs)
    body = mp(client, tables).get_json()
    assert body["kind"] == "candidates", body
    assert tree_state(figs) == before
    item = body["items"][0]
    c = commit(client, body["session"], item["id"])
    assert c.status_code == 200, c.get_json()
    out = c.get_json()
    assert out["task"]["outcome"] == "completed", out
    assert out["data_check"] == "verified" and out["can_delete_script"] is False
    stem = "ctrl_response_mV_by_file"
    assert out["script"] == f"data/tavotto_figures/{stem}.py" and out["stem"] == stem
    figures = figs / "data" / "tavotto_figures"
    assert sorted(p.name for p in figures.iterdir()) == sorted(
        [f"{stem}.py", Path(out["module"]).name]
    )
    assert discover.registered_stems(figs, out["script"]) == [stem]
    # 原件逐字节 + mtime 不动（注册表是登记新脚本，允许变；新增的只有 tavotto_figures 下的东西）
    after = {p: s for p, *s in tree_state(figs)}
    for rel, is_dir, *rest in before:
        if not is_dir and rel != "tavotto_registry.json":
            assert after[rel] == [is_dir, *rest], f"{rel} 被改动"
    added = {p for p in after} - {p for p, *_ in before}
    assert all(p.startswith("data/tavotto_figures") for p in added), added
    # 脚本：相对路径指回三份原件、不含任何数据文字、读表经公共模块的 merge_tables
    script = (figures / f"{stem}.py").read_text(encoding="utf-8")
    for n in ("ctrl", "drugA", "drugB"):
        assert f"'../{n}.csv'" in script
    assert "tp.merge_tables(" in script and "FILE_COLUMN = 'file'" in script
    assert "# tavotto-source: " in script and "data_to_figure/v2-merge" in script
    assert tpl.parse_source_comment(script) is None, "合画脚本不会被当成来自某一份数据"
    checks = checks_of(out["task"]["task_id"])
    assert checks["render.figure"] == ("verified", None)
    assert checks["data.verifiable"] == ("verified", None)
    assert any(
        k.startswith("data.") and k != "data.verifiable" and v[0] == "verified"
        for k, v in checks.items()
    )
    # 脱离 Tavotto、换一个 cwd 独立重跑：退出 0、出图
    import shutil

    work = tmp_path / "rerun"
    shutil.copytree(figs / "data", work / "data")
    r = run_standalone(work / "data" / "tavotto_figures" / f"{stem}.py", tmp_path, tmp_path)
    assert r.returncode == 0, r.stderr[-600:]
    assert list((work / "data" / "tavotto_figures").glob("*.pdf"))
    # 同一批里的另一个备选还能提交：脚本递增、公共模块复用
    second = commit(client, body["session"], body["items"][1]["id"]).get_json()
    assert second["task"]["outcome"] == "completed"
    assert (
        second["script"].endswith("ctrl_response_mV_by_file_2.py")
        and second["module"] == out["module"]
    )


@needs_worker
def test_files_in_different_folders_land_in_their_nearest_common_parent(project):
    m, client, figs, _ = project
    put(figs, "data/exp1/a.csv", dose_rows(0))
    put(figs, "data/exp2/a.csv", dose_rows(2))
    put(figs, "data/exp2/deep/c.csv", dose_rows(4))
    body = mp(
        client,
        folder="",
        tables=[
            {"path": "data/exp1/a.csv"},
            {"path": "data/exp2/a.csv"},
            {"path": "data/exp2/deep/c.csv"},
        ],
    ).get_json()
    assert body["kind"] == "candidates", body
    # 同名文件靠父文件夹区分，标签唯一
    labels = [s["label"] for s in body["sources"]]
    assert len(set(labels)) == 3 and labels[0] != labels[1], labels
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    assert out["script"] == "data/tavotto_figures/a_response_mV_by_file.py"
    script = (figs / out["script"]).read_text(encoding="utf-8")
    for rel in ("exp1/a.csv", "exp2/a.csv", "exp2/deep/c.csv"):
        assert f"'../{rel}'" in script
    assert (
        not (figs / "tavotto_figures").exists()
        and not (figs / "data" / "exp1" / "tavotto_figures").exists()
    )


@needs_worker
def test_files_in_the_same_subfolder_land_in_that_subfolder(project):
    m, client, figs, _ = project
    for i, n in enumerate(("a", "b")):
        put(figs, f"data/sub/{n}.csv", dose_rows(i))
    body = mp(
        client, folder="data", tables=[{"path": "sub/a.csv"}, {"path": "sub/b.csv"}]
    ).get_json()
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed", out
    assert out["script"] == "data/sub/tavotto_figures/a_response_mV_by_file.py"
    assert "'../a.csv'" in (figs / out["script"]).read_text(encoding="utf-8")


@needs_worker
def test_mixed_formats_encodings_units_row_and_xlsx_sheets(project, tmp_path):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    rows = dose_rows(0)
    (figs / "data" / "a_utf8.csv").write_text(
        "\n".join(",".join(map(str, r)) for r in rows) + "\n", encoding="utf-8"
    )
    zh = [["剂量_uM", "响应_mV"], *[[d, d + 1.0] for d in range(1, 7)]]
    (figs / "data" / "b_gb.csv").write_bytes(
        ("\n".join(",".join(map(str, r)) for r in zh) + "\n").encode("gb18030")
    )
    (figs / "data" / "a_utf8.csv").write_text(
        "\n".join(
            ",".join(map(str, r))
            for r in [["剂量_uM", "响应_mV"], *[[d, d + 0.5] for d in range(1, 7)]]
        )
        + "\n",
        encoding="utf-8",
    )
    # xlsx 的两张工作表也各算一份；文本表格带单位行
    make_xlsx(
        figs / "data" / "c.xlsx",
        {
            "S1": [["剂量_uM", "响应_mV"], *[[d, d + 2.0] for d in range(1, 7)]],
            "S2": [["剂量_uM", "响应_mV"], *[[d, d + 3.0] for d in range(1, 7)]],
        },
    )
    (figs / "data" / "d_units.tsv").write_text(
        "剂量_uM\t响应_mV\nuM\tmV\n" + "\n".join(f"{d}\t{d + 4.0}" for d in range(1, 7)) + "\n",
        encoding="utf-8",
    )
    tables = [
        {"path": "a_utf8.csv"},
        {"path": "b_gb.csv"},
        {"path": "c.xlsx", "sheet": "S1"},
        {"path": "c.xlsx", "sheet": "S2"},
        {"path": "d_units.tsv"},
    ]
    body = mp(client, tables, render="lazy").get_json()
    assert body["kind"] == "candidates", body
    labels = [s["label"] for s in body["sources"]]
    assert labels == ["a_utf8", "b_gb", "c (S1)", "c (S2)", "d_units"]
    assert [s["units_row"] for s in body["sources"]] == [False, False, False, False, True]
    assert [s["sheet"] for s in body["sources"]] == [None, None, "S1", "S2", None]
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "gb18030" in script and "SHEETS = [None, None, 'S1', 'S2', None]" in script
    import shutil

    work = tmp_path / "rerun"
    shutil.copytree(figs / "data", work / "data")
    r = run_standalone(work / "data" / Path(out["script"]).relative_to("data"), tmp_path, tmp_path)
    assert r.returncode == 0, r.stderr[-600:]


@needs_worker
def test_a_cell_rule_applies_over_the_merged_table_and_rows_name_their_file(project):
    m, client, figs, _ = project
    a = dose_rows(0)
    b = dose_rows(2)
    b[3][1] = "<0.1"  # 夹着一个文字格（在第二份里）
    put(figs, "data/a.csv", a)
    put(figs, "data/b.csv", b)
    tables = [{"path": "a.csv"}, {"path": "b.csv"}]
    lazy = mp(client, tables, render="lazy").get_json()
    assert lazy["kind"] == "candidates", lazy
    assert lazy["mixed_cells"] and lazy["mixed_cells"][0]["column"] == "response_mV"
    # 默认「不画那几行」：没画的行带着文件名（行号是那一份自己的文件行号）
    assert lazy["items"][0]["unplotted"]["count"] == 0, (
        "默认「不画那几行」是用户决定过的格子，不算没画"
    )
    ask = mp(client, tables).get_json()
    assert ask["kind"] == "ask" and ask["question_code"] == "mixed_cells"
    preview = ask["columns"][0]["preview"][0]
    assert preview["file"] == "b" and preview["row"] == 4
    rule = {"cells": {"response_mV": {"mode": "extract"}}}
    body = mp(client, tables, answers=rule).get_json()
    assert body["kind"] == "candidates", body
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    assert out["cells"][0]["extracted"] == 1
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "CELLS = " in script and "apply_cells(" in script
    skip = mp(client, tables, answers={"cells": {"response_mV": {"mode": "skip"}}}).get_json()
    done = commit(client, skip["session"], skip["items"][0]["id"]).get_json()
    assert done["unplotted"]["count"] == 0  # 用户已决定处理的格子不再算没画


@needs_worker
def test_a_changed_source_between_propose_and_commit_is_refused_and_nothing_is_written(project):
    m, client, figs, _ = project
    tables = three(figs)
    body = mp(client, tables).get_json()
    snapshot = tree_state(figs)
    put(figs, "data/drugB.csv", dose_rows(9))  # 第三份在出备选后变了
    changed = tree_state(figs)
    r = commit(client, body["session"], body["items"][0]["id"])
    assert r.status_code == 409 and r.get_json()["code"] == "propose_data_changed"
    assert tree_state(figs) == changed and snapshot != changed
    assert not (figs / "data" / "tavotto_figures").exists()


@needs_worker
def test_an_existing_script_of_that_name_is_never_overwritten(project):
    m, client, figs, _ = project
    tables = three(figs)
    figures = figs / "data" / "tavotto_figures"
    figures.mkdir()
    (figures / "ctrl_response_mV_by_file.py").write_text("# mine\n", encoding="utf-8")
    body = mp(client, tables).get_json()
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed"
    assert out["script"].endswith("ctrl_response_mV_by_file_2.py")
    assert (figures / "ctrl_response_mV_by_file.py").read_text(encoding="utf-8") == "# mine\n"


@needs_worker
def test_the_envelope_carries_every_source_and_the_history_records_them(project):
    m, client, figs, _ = project
    tables = three(figs)
    body = mp(client, tables).get_json()
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    row = ai_history.get(out["task"]["task_id"])
    assert row is not None
    refs = json.dumps(row, default=str)
    for n in ("ctrl", "drugA", "drugB"):
        assert f"data/{n}.csv" in refs


def test_idempotent_propose_by_client_request_id_includes_the_tables(project):
    m, client, figs, _ = project
    tables = three(figs)
    a = mp(client, tables[:2], client_request_id="rid-merge-0001", render="lazy").get_json()
    again = mp(client, tables[:2], client_request_id="rid-merge-0001", render="lazy").get_json()
    other = mp(client, tables[1:], client_request_id="rid-merge-0001", render="lazy").get_json()
    assert again == a and other["session"] != a["session"]


@needs_worker
def test_rows_that_cannot_be_plotted_name_their_file(project):
    m, client, figs, _ = project
    b = dose_rows(2)
    b[4][0] = ""  # 第二份的横轴缺一格
    put(figs, "data/a.csv", dose_rows(0))
    put(figs, "data/b.csv", b)
    body = mp(client, [{"path": "a.csv"}, {"path": "b.csv"}], render="lazy").get_json()
    item = body["items"][0]
    assert item["values"]["family"] == "trend" and item["unplotted"] == {
        "count": 1,
        "code": "rows_not_plotted",
    }
    out = commit(client, body["session"], item["id"]).get_json()
    assert out["unplotted"]["rows"] == [
        {"row": 5, "column": "dose_uM", "reason": "x_not_a_value", "file": "b"}
    ]


def test_the_runtime_module_name_changed_with_merge_tables_and_is_content_addressed():
    import hashlib

    name, data = tpl.runtime_module()
    assert name == f"tavotto_plot_{hashlib.sha256(data).hexdigest()[:8]}.py"


def test_one_workbook_with_many_sheets_counts_its_bytes_once(project, monkeypatch):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    book = figs / "data" / "w.xlsx"
    make_xlsx(book, {f"S{i}": dose_rows(i) for i in range(1, 7)})
    size = book.stat().st_size
    monkeypatch.setattr(M, "MAX_TOTAL_BYTES", size + 10)
    tables = [{"path": "w.xlsx", "sheet": f"S{i}"} for i in range(1, 7)]
    ok = mp(client, tables, render="lazy")
    assert ok.status_code == 200 and ok.get_json()["kind"] == "candidates", ok.get_json()
    assert [s["label"] for s in ok.get_json()["sources"]] == [f"w (S{i})" for i in range(1, 7)]
    # 另一个同样大的文件就超了
    other = figs / "data" / "w2.xlsx"
    make_xlsx(other, {"S1": dose_rows(1)})
    over = mp(client, [*tables[:2], {"path": "w2.xlsx", "sheet": "S1"}], render="lazy")
    assert over.status_code == 400 and over.get_json()["code"] == "data_too_large"


@needs_worker
def test_commit_at_the_cap_carries_eight_data_refs_through_the_task(project):
    m, client, figs, _ = project
    n = M.MAX_TABLES
    for i in range(n):
        put(figs, f"data/f{i}.csv", dose_rows(i))
    body = mp(client, [{"path": f"f{i}.csv"} for i in range(n)]).get_json()
    assert body["kind"] == "candidates" and len(body["items"]) == 4, body
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    row = json.dumps(ai_history.get(out["task"]["task_id"]), default=str)
    for i in range(n):
        assert f"data/f{i}.csv" in row


# =========================================================================== 脚本名（规则 + 留给模型的接缝）
@pytest.mark.parametrize(
    ("paths", "y", "name"),
    [
        # 例子：公共前缀 growth + 纵轴列名 + _by_file
        (
            ["growth_control.csv", "growth_drugA.csv", "growth_drugB.csv"],
            "tumor_volume",
            "growth_tumor_volume_by_file",
        ),
        # 前缀以分隔符结尾：去掉尾部分隔符
        (["sample1_ctrl.csv", "sample1_treat.csv"], "od600", "sample1_od600_by_file"),
        # 字母 / 数字交界是词边界
        (["sample1.csv", "sample2.csv"], "value", "sample_value_by_file"),
        # 前缀把一串数字切断：先去掉尾部数字
        (["exp10.csv", "exp11.csv"], "value", "exp_value_by_file"),
        # 前缀把一个词切断：退到最后一个分隔符
        (["growth_ctrl.csv", "growth_cell.csv"], "y", "growth_y_by_file"),
        # 没有公共前缀 / 太短（< 3 字符）：用第一份的 stem
        (["ctrl.csv", "drug.csv"], "response_mV", "ctrl_response_mV_by_file"),
        (["ab_1.csv", "ab_2.csv"], "v", "ab_1_v_by_file"),
        # 目录不参与；扩展名不参与
        (["a/run_x.csv", "b/run_y.tsv"], "v", "run_v_by_file"),
        # 列名安全化：只留 [A-Za-z0-9_-]
        (["m1.csv", "m2.csv"], "Response (mV)", "m1_Response_mV_by_file"),
        (["数据一.csv", "数据二.csv"], "测量", "data_value_by_file"),
    ],
)
def test_rule_stem_branches(paths, y, name):
    assert M.rule_stem(paths, y) == name


def test_rule_stem_caps_the_length_and_keeps_the_suffix():
    long_y = "very_long_column_name_" * 6
    name = M.rule_stem(["growth_a.csv", "growth_b.csv"], long_y)
    assert (
        name.endswith("_by_file")
        and len(name) <= M.STEM_MAX
        and name.startswith("growth_very_long")
    )
    huge = M.rule_stem(["g" * 200 + "_a.csv", "g" * 200 + "_b.csv"], long_y)
    assert len(huge) <= M.STEM_MAX and huge.endswith("_by_file")
    import re

    assert re.fullmatch(r"[A-Za-z0-9_-]+", huge)


def test_the_model_seam_uses_a_valid_suggestion_and_falls_back_otherwise(monkeypatch):
    paths = ["growth_a.csv", "growth_b.csv"]
    rule = M.rule_stem(paths, "v")
    assert M.merged_stem(paths, "v") == rule
    assert (
        M.merged_stem(paths, "v", suggest=lambda p, y: "tumor_growth_by_drug")
        == "tumor_growth_by_drug"
    )
    seen = {}
    M.merged_stem(paths, "v", suggest=lambda p, y: seen.update(args=(p, y)))
    assert seen["args"] == (paths, "v")
    for bad in (None, "", "has space", "../x", "x" * 200, 5, "中文"):
        assert M.merged_stem(paths, "v", suggest=lambda p, y, b=bad: b) == rule

    def boom(p, y):
        raise RuntimeError("model down")

    assert M.merged_stem(paths, "v", suggest=boom) == rule
    monkeypatch.setattr(M, "STEM_SUGGESTER", lambda p, y: "from_the_seam")
    assert M.merged_stem(paths, "v") == "from_the_seam"


@needs_worker
def test_the_script_name_follows_the_chosen_candidates_column_and_repeats_get_a_suffix(project):
    m, client, figs, _ = project
    for n in ("growth_control", "growth_drugA", "growth_drugB"):
        put(
            figs,
            f"data/{n}.csv",
            [["day", "tumor_volume"], *[[d, d * 1.5 + len(n)] for d in range(1, 7)]],
        )
    tables = [{"path": f"{n}.csv"} for n in ("growth_control", "growth_drugA", "growth_drugB")]
    names = []
    for _ in range(2):
        body = propose(client, tables=tables, render="lazy").get_json()
        out = commit(client, body["session"], body["items"][0]["id"]).get_json()
        assert out["task"]["outcome"] == "completed", out
        names.append(Path(out["script"]).name)
    assert names == ["growth_tumor_volume_by_file.py", "growth_tumor_volume_by_file_2.py"]


# =========================================================================== 文件里原本就有分组列
def _arms(batch_of):
    """每份 6 行：`batch` 列由 `batch_of(文件序号, 行号)` 给；`dose` 横轴、`resp` 纵轴。"""

    def rows(i):
        return [
            ["batch", "dose_uM", "resp_mV"],
            *[[batch_of(i, k), k + 1, 1 + i + k * 0.5] for k in range(6)],
        ]

    return rows


def test_a_group_column_that_is_constant_inside_each_file_is_ignored(project):
    m, client, figs, _ = project
    rows = _arms(lambda i, k: f"arm{i}")  # 每个文件里只有一个取值：与「文件」是同一件事
    for i, n in enumerate(("a", "b")):
        put(figs, f"data/{n}.csv", rows(i))
    body = propose(
        client,
        tables=[{"path": "a.csv"}, {"path": "b.csv"}],
        render="lazy",
        answers={"y": "resp_mV"},
    ).get_json()
    assert body["kind"] == "candidates", body
    assert body["roles"]["group"] == "file" and body["roles"]["facet"] is None
    assert body["roles"]["options"]["group"] == ["file"], "被忽略的列不在可换的分组列里"
    assert not any("variant" in it for it in body["items"]), "没有成对的两种画法"
    for it in body["items"]:
        assert "batch" not in (it["values"]["group"], it["values"]["facet"], it["values"]["x"])
    assert not any(d["question_code"] == "pick_group" for d in body["defaults"]), (
        "忽略的列本来就不是问题"
    )


def test_a_group_column_with_several_values_inside_a_file_gives_both_ways(project):
    m, client, figs, _ = project
    rows = _arms(lambda i, k: "p" if k % 2 == 0 else "q")
    for i, n in enumerate(("a", "b")):
        put(figs, f"data/{n}.csv", rows(i))
    body = propose(
        client,
        tables=[{"path": "a.csv"}, {"path": "b.csv"}],
        render="lazy",
        answers={"y": "resp_mV"},
    ).get_json()
    assert body["kind"] == "candidates", body
    items = body["items"]
    marked = [(it["variant"], it["values"]) for it in items if it.get("variant")]
    kinds = [v["kind"] for v, _ in marked]
    assert "split" in kinds and "by_file" in kinds
    assert all(v.get("column") == "batch" for v, _ in marked if v["kind"] == "split")
    # 现状的画法排在前（推荐仍是它的第一张），它后面紧跟只按文件比较的那张
    assert items[0]["recommended"] and items[0]["variant"] == {"kind": "split", "column": "batch"}
    assert items[1]["variant"] == {"kind": "by_file"}
    assert items[0]["title_code"] == items[1]["title_code"]
    # 只按文件比较的：配方里没有 batch；按它拆的：batch 在分面 / 颜色里
    for variant, values in marked:
        used = {values["group"], values["facet"], values["x"]}
        assert ("batch" in used) == (variant["kind"] == "split"), (variant, values)
        if variant["kind"] == "by_file":
            assert values["group"] in ("file", None) or values["x"] == "file"
    # 两种画法画出来一样的（散点只用一个分组）不成对、不加标记
    for it in items:
        if it["title_code"] in ("scatter_fit", "scatter"):
            assert it["variant"] is None


def test_the_user_choosing_a_group_column_turns_the_pairing_off(project):
    m, client, figs, _ = project
    rows = _arms(lambda i, k: "p" if k % 2 == 0 else "q")
    for i, n in enumerate(("a", "b")):
        put(figs, f"data/{n}.csv", rows(i))
    body = propose(
        client,
        tables=[{"path": "a.csv"}, {"path": "b.csv"}],
        render="lazy",
        answers={"y": "resp_mV", "group": "batch"},
    ).get_json()
    assert body["kind"] == "candidates" and not any(it.get("variant") for it in body["items"])


def test_a_single_file_response_has_no_variant_keys(project):
    m, client, figs, _ = project
    put(figs, "data/a.csv", _arms(lambda i, k: "p" if k % 2 == 0 else "q")(0))
    body = propose(client, table="a.csv", render="lazy", answers={"y": "resp_mV"}).get_json()
    assert body["kind"] == "candidates" and not any("variant" in it for it in body["items"])


def test_classify_groups_by_values_inside_each_file(tmp_path):
    def summary(rows):
        p = tmp_path / "x.csv"
        p.write_text("\n".join(",".join(map(str, r)) for r in rows) + "\n", encoding="utf-8")
        return adf.inspect_data_source(p, v2=True)

    a = summary([["tag", "mix", "v"], *[["t0", "p" if k % 2 else "q", k] for k in range(6)]])
    b = summary([["tag", "mix", "v"], *[["t1", "p" if k % 2 else "q", k] for k in range(6)]])
    srcs = [
        M.Source("a.csv", "a.csv", None, "a", a.sha256, a.size, a.header_line),
        M.Source("b.csv", "b.csv", None, "b", b.sha256, b.size, b.header_line),
    ]
    entry, values = M.build(srcs, [a, b], "")
    assert entry.merge.ignored == ("tag",) and entry.merge.splittable == ("mix",)
    assert (
        "tag" not in values.groups and "tag" not in entry.summary.groups and "mix" in values.groups
    )


# =========================================================================== 标签撞车（Codex P1）：绝不静默并组
def _groups_of(labels):
    """合画后按分组键数出来的组数（模板里 `comparison_data` 的分组键）。"""
    header = ["v"]
    tables = [(header, [["1"], ["2"]]) for _ in labels]
    h, rows = tpl.merge_tables(tables, labels, "file")
    d = tpl.comparison_data(h, rows, None, "v", "file", None, "as_given")
    return len(d["groups"])


def _assert_apart(labels, n):
    assert len(labels) == n
    assert len({tpl.group_key(x) for x in labels}) == n, labels
    assert len({M.unique_key(x) for x in labels}) == n, labels
    assert _groups_of(labels) == n


def test_codex_example_a_A_and_a_label_equal_to_the_fallback_form():
    items = [("a.csv", None), ("A.csv", None), ("A.csv #2.csv", None)]
    labels = M.unique_labels(items)
    _assert_apart(labels, 3)
    # 第一次的兜底形态不许撞上第三个文件本来的名字
    assert labels.count("A.csv #2") <= 1


@pytest.mark.parametrize(
    "items",
    [
        # 大小写不敏感口径：不同文件夹里的 a.csv / A.csv
        [("x/a.csv", None), ("y/A.csv", None)],
        [("a.csv", None), ("A.csv", None), ("A.csv #2.csv", None), ("A.csv #3.csv", None)],
        [("a.csv", None), ("a.csv #2.csv", None), ("d/a.csv", None), ("e/a.csv", None)],
        # 标签恰好等于别的文件的「兜底形态」
        [("p/run.csv", None), ("q/run.csv", None), ("p/run #2.csv", None)],
        [("run.csv", None), ("RUN.csv", None), ("run #2.csv", None), ("Run #2.csv", None)],
        # 全数字名、带首尾 / 内部空白、Unicode 规范化等价（é 两种写法）
        [("1.csv", None), ("01.csv", None), ("1.0.csv", None), ("d/1.csv", None)],
        [(" a .csv", None), ("a.csv", None), ("a  .csv", None)],
        [("café.csv", None), ("café.csv", None)],
        # 同一个工作簿的几张表、表名只差大小写
        [("w.xlsx", "S"), ("w.xlsx", "s"), ("w.xlsx", "S ")],
        # 8 个文件全同名、不同文件夹
        [(f"d{i}/data.csv", None) for i in range(8)],
        [("data.csv", None), *[(f"d{i}/data.csv", None) for i in range(7)]],
    ],
)
def test_labels_never_collide_under_the_grouping_key(items):
    _assert_apart(M.unique_labels(items), len(items))


def test_long_names_that_truncate_to_the_same_label_stay_apart():
    items = [(f"{'x' * 150}{i}.csv", None) for i in range(4)]
    _assert_apart(M.unique_labels(items), 4)


try:
    from hypothesis import given, settings, strategies as st
except ImportError:  # pragma: no cover
    given = None

if given is not None:
    _seg = st.text(alphabet="aAbB1 _-.é", min_size=1, max_size=5).filter(
        lambda t: t.strip(" .") != ""
    )
    _path = st.builds(
        lambda dirs, stem, ext: "/".join([*dirs, stem + ext]),
        st.lists(_seg, max_size=2),
        _seg,
        st.sampled_from([".csv", ".tsv", ".CSV", ".txt"]),
    )

    @settings(max_examples=300, deadline=None)
    @given(st.lists(_path, min_size=2, max_size=8, unique=True))
    def test_any_distinct_paths_get_pairwise_distinct_labels(paths):
        items = [(p, None) for p in paths]
        labels = M.unique_labels(items)
        _assert_apart(labels, len(items))
        assert all(adf.parse_cell(x)[0] == "text" or "/" in x or "." in x for x in labels)


def test_the_runtime_function_refuses_labels_that_would_merge_into_one_group():
    t = (["v"], [["1"]])
    with pytest.raises(ValueError, match="tables_labels_collide"):
        tpl.merge_tables([t, t], ["a", " a "], "file")  # 去首尾空白后相同
    tpl.merge_tables([t, t], ["a", "A"], "file")  # 区分大小写：分组时是两组，运行时函数不替用户收紧
    assert tpl.group_key in tpl.RUNTIME_FUNCTIONS


def test_build_fails_loudly_instead_of_merging_colliding_labels(tmp_path):
    def summary(name):
        p = tmp_path / name
        p.write_text("dose,resp\n1,2\n2,3\n3,4\n", encoding="utf-8")
        return adf.inspect_data_source(p, v2=True)

    a, b = summary("a.csv"), summary("b.csv")
    srcs = [
        M.Source("a.csv", "a.csv", None, "same", a.sha256, a.size, a.header_line),
        M.Source("b.csv", "b.csv", None, "same ", b.sha256, b.size, b.header_line),
    ]
    with pytest.raises(M.MergeError) as e:
        M.build(srcs, [a, b], "")
    assert e.value.code == "tables_labels_collide"
    ok = [replace_label(srcs[0], "one"), replace_label(srcs[1], "two")]
    entry, values = M.build(ok, [a, b], "")
    assert len({r[-1].strip() for r in values.rows}) == 2


def replace_label(src, label):
    import dataclasses

    return dataclasses.replace(src, label=label)


def test_the_collide_code_is_a_registered_propose_error():
    assert "tables_labels_collide" in P.ERROR_CODES
    P.ProposeError("tables_labels_collide")


def test_the_paired_mark_lives_on_the_candidate_and_survives_replace():
    """成对的标记长在候选自己身上（#2179）：`replace` 换配方（误差类型、看图目的重排……）标记照旧，不靠 `id(配方)` 旁挂、也不用手动挪。"""
    from dataclasses import replace

    from tavotto.engine import ai_data_recipe as recipe_mod

    split = recipe_mod.CandidateVariant("split", "batch")
    assert split.to_dict() == {"kind": "split", "column": "batch"}
    assert recipe_mod.CandidateVariant("by_file").to_dict() == {"kind": "by_file"}
    cand = recipe_mod.Candidate("trend_mean", object(), split)  # type: ignore[arg-type]
    again = replace(replace(cand, recipe=object()), recipe=object())
    assert again.variant is split


def test_the_split_mark_names_the_column_the_figure_really_splits_by():
    """文件里有两列都在文件内有多个取值时，「按某列再分开」的列名取配方真用到的那一列，不是 `splittable` 的第一个。"""
    from types import SimpleNamespace

    from tavotto.engine.ai_data_propose import _split_column

    rec = SimpleNamespace(facet="condition", group="file", x="time")
    assert _split_column(rec, ("batch", "condition")) == "condition"
    rec = SimpleNamespace(facet=None, group="batch", x="time")
    assert _split_column(rec, ("condition", "batch")) == "batch"
    rec = SimpleNamespace(facet=None, group="file", x="time")
    assert _split_column(rec, ("batch",)) == "batch"  # 配方里一个都没用到：退回第一个（与之前一样）


def test_paired_marks_survive_both_the_error_swap_and_a_purpose_reorder(project):
    """误差类型换配方 + 看图目的重排同时发生：每张成对候选的标记仍对得上它自己的配方（按文件拆的那张配方里有 batch，只按文件的没有）。"""
    _, client, figs, _ = project
    rows = _arms(lambda i, k: "p" if k % 2 == 0 else "q")
    for i, n in enumerate(("a", "b")):
        put(figs, f"data/{n}.csv", rows(i))
    tables = [{"path": "a.csv"}, {"path": "b.csv"}]
    base = propose(client, tables=tables, render="lazy", answers={"y": "resp_mV"}).get_json()
    cur = (base.get("purpose") or {}).get("code")
    purposes = [p for p in (base.get("purpose") or {}).get("options", []) if p != cur]
    answers = {"y": "resp_mV", "error": "sem", **({"purpose": purposes[-1]} if purposes else {})}
    out = propose(client, tables=tables, render="lazy", answers=answers).get_json()
    assert out["kind"] == "candidates", out
    marked = [it for it in out["items"] if it.get("variant")]
    assert {it["variant"]["kind"] for it in marked} == {"split", "by_file"}, out["items"]
    for it in marked:
        v = it["values"]
        used = {v["group"], v["facet"], v["x"]}
        assert ("batch" in used) == (it["variant"]["kind"] == "split"), it
    ids = [(it["title_code"], json.dumps(it.get("variant"), sort_keys=True)) for it in out["items"]]
    assert len(ids) == len(set(ids)), "成对的两张身份必须分得清"


def _excel_percent_fixture(path, styles_per_cell):
    styles = (
        STYLES.replace('numFmts count="3"', 'numFmts count="5"')
        .replace(
            "</numFmts>",
            '<numFmt numFmtId="167" formatCode="0.000%"/>'
            '<numFmt numFmtId="168" formatCode="0.0&quot;%&quot;"/></numFmts>',
        )
        .replace('cellXfs count="5"', 'cellXfs count="8"')
        .replace(
            "</cellXfs>", '<xf numFmtId="10"/><xf numFmtId="167"/><xf numFmtId="168"/></cellXfs>'
        )
    )
    rows = [["date", "response"]] + [
        [f"2026-01-0{i}", C("f" if i == 2 else "n", val, style)]
        for i, (val, style) in enumerate(
            zip([0.125, 0.29, 0.5, 0.75], styles_per_cell, strict=True), 1
        )
    ]
    return make_xlsx(path, {"Sheet1": rows}, styles=styles)


@pytest.mark.parametrize("styles", [[5, 5, 5, 5], [6, 6, 6, 6], [5, 6, 5, 6]])
@needs_worker
def test_csv_and_excel_percentages_render_on_identical_scale(project, tmp_path, styles):
    _, client, figs, _ = project
    put(
        figs,
        "data/text.csv",
        [
            ["date", "response"],
            *[[f"2026-01-0{i}", v] for i, v in enumerate(["12.5%", "29%", "50%", "75%"], 1)],
        ],
    )
    book = _excel_percent_fixture(figs / "data" / "book.xlsx", styles)
    before = book.read_bytes()
    # 默认 / v1 仍读到存储值，不能改非出图调用；v2 与生成的脚本才按显示比例。
    raw = adf.inspect_data_source(book)
    assert [adf.legacy_parse_cell(r[1])[1] for r in raw.rows] == [0.125, 0.29, 0.5, 0.75]
    r = propose(
        client,
        tables=[{"path": "text.csv"}, {"path": "book.xlsx"}],
        render="lazy",
        answers={"x": "date", "y": ["response"]},
    )
    assert r.status_code == 200, r.get_json()
    result = r.get_json()
    rec = next(i for i in result["items"] if i.get("recommended"))
    sess = next(s for s in P._SESSIONS.values() if s.id == result["session"])
    item = sess.items[rec["id"]]
    script = sess.dir / "tavotto_figures" / f"{item.stem}.py"
    code = f"""
import runpy
from pathlib import Path
import matplotlib.pyplot as plt
runpy.run_path({str(script)!r}, run_name="__main__")
ax = plt.gcf().axes[0]
assert ax.get_ylabel() == "response (%)", ax.get_ylabel()
assert {{line.get_label(): list(line.get_ydata()) for line in ax.lines}} == {{
    "line | book": [12.5, 29.0, 50.0, 75.0], "line | text": [12.5, 29.0, 50.0, 75.0],
}}
assert Path({str(script.with_suffix(".pdf"))!r}).read_bytes().startswith(b"%PDF-")
"""
    run = subprocess.run(
        [WORKER_PY, "-c", code],
        cwd=script.parent,
        env={**os.environ, "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(tmp_path / "mpl")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=40,
    )
    assert run.returncode == 0, run.stderr
    assert book.read_bytes() == before


@pytest.mark.parametrize("style", [0, 7])  # General 或自定义格式中带引号的字面 %
def test_unscaled_excel_values_are_not_guessed_or_combined_with_percent_csv(project, style):
    _, client, figs, _ = project
    put(figs, "data/text.csv", [["date", "response"], ["2026-01-01", "12.5%"]])
    book = _excel_percent_fixture(figs / "data" / "book.xlsx", [style] * 4)
    summary = adf.inspect_data_source(book, v2=True)
    assert adf.parse_cell(summary.rows[0][1]) == ("numeric", 0.125)
    assert adf.axis_label("response", summary) == "response"
    r = propose(client, tables=[{"path": "text.csv"}, {"path": "book.xlsx"}], render="lazy")
    assert r.status_code == 400 and r.get_json()["code"] == "tables_percent_scale_mismatch"


def test_excel_mixed_percent_and_general_column_fails_before_plotting(tmp_path):
    book = _excel_percent_fixture(tmp_path / "mixed.xlsx", [5, 0, 5, 5])
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(book, v2=True)
    assert e.value.code == "data_xlsx_percent_scale_mismatch"
