# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""TAF 数据出图 v2 认 GROMACS / Grace 的 `.xvg`（ADR 0178 2026-11-18 修订）。

判据的主语：读表量的是**父进程读出的表**（`read_text_table`）与**脚本独立重跑读出的表**是不是同一张（提交后的数据指纹由真 worker 的 manifest 对拍）；
标签清洗量的是函数输出（逐条规则一条用例）；原件只读量的是字节与 mtime。夹具全部手写。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_template as tpl
from tests.test_ai_data_propose import (
    WORKER_PY,
    checks_of,
    commit,
    needs_worker,
    project,  # noqa: F401
    propose,
    tree_state,
)

ENERGY = """\
# This file was created Tue Nov 17 10:00:00 2026
# by:
#                :-) GROMACS - gmx energy, 2024 (-:
#
@    title "GROMACS Energies"
@    xaxis  label "Time (ps)"
@    yaxis  label "(kJ/mol)"
@TYPE xy
@ view 0.15, 0.15, 0.75, 0.85
@ legend on
@ legend length 2
@ s0 legend "Potential"
@ s1 legend "Kinetic En."
@ s2 legend "Temperature"
    0.000000  -503.1  210.5  300.1
    1.000000  -505.2  211.0  300.9
    2.000000  -504.8  209.7  299.5
    3.000000  -506.0  212.3  301.7
    4.000000  -507.4  213.1  302.2
    5.000000  -506.6  211.8  300.8
    6.000000  -508.1  214.0  303.0
    7.000000  -507.9  212.9  301.9
"""

RMS = """\
# This file was created Tue Nov 17 10:00:00 2026
@    title "RMSD"
@    xaxis  label "Time (ps)"
@    yaxis  label "RMSD (nm)"
@TYPE xy
    0.0  0.0010
   10.0  0.0950
   20.0  0.1210
   30.0  0.1330
   40.0  0.1290
"""


def write(tmp_path: Path, name: str, text: str, newline: str = "\n") -> Path:
    p = tmp_path / name
    p.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return p


def read(p: Path):
    return adf.read_text_table(p)


# =========================================================================== 读表
def test_gmx_energy_names_come_from_the_directives_and_the_physical_line_numbers_stay(tmp_path):
    t = read(write(tmp_path, "energy.xvg", ENERGY))
    assert t.status == "ok" and t.delimiter == "\t"
    s = t.summary
    assert s.columns == ["Time (ps)", "Potential", "Kinetic En.", "Temperature"]
    assert len(s.rows) == 8 and s.rows[0] == ["0.000000", "-503.1", "210.5", "300.1"]
    assert t.header_line == 14 and s.row_number(0) == 15, "行号仍是原文件里的物理行号"
    assert all(s.per_column[c].kind == "numeric" for c in s.columns)


def test_gmx_rms_single_data_column_uses_the_yaxis_label(tmp_path):
    s = read(write(tmp_path, "rmsd.xvg", RMS)).summary
    assert s.columns == ["Time (ps)", "RMSD (nm)"] and len(s.rows) == 5


def test_a_legend_wins_over_the_yaxis_label_and_missing_columns_are_named_by_position(tmp_path):
    two = '@ xaxis label "t"\n@ yaxis label "Y axis"\n@ s0 legend "Leg"\n0 1\n1 2\n2 3\n'
    assert read(write(tmp_path, "a.xvg", two)).summary.columns == ["t", "Leg"]
    # 两条以上的数据列不用 yaxis label；只给了部分 legend，缺的列叫 col<N>
    part = '@ yaxis label "Y"\n@ s1 legend "b"\n0 1 2 3\n1 2 3 4\n2 3 4 5\n'
    assert read(write(tmp_path, "b.xvg", part)).summary.columns == ["col1", "col2", "b", "col4"]


def test_only_the_first_data_set_is_read_for_amp_and_target_forms(tmp_path):
    amp = '@ xaxis label "t"\n@ s0 legend "a"\n0 1\n1 2\n2 3\n&\n0 9\n1 8\n2 7\n'
    s = read(write(tmp_path, "amp.xvg", amp)).summary
    assert s.columns == ["t", "a"] and [r[1] for r in s.rows] == ["1", "2", "3"]
    target = (
        '@ xaxis label "t"\n@ s0 legend "a"\n0 1\n1 2\n2 3\n@target G0.S1\n@type xy\n0 9\n1 8\n'
    )
    s = read(write(tmp_path, "target.xvg", target)).summary
    assert [r[1] for r in s.rows] == ["1", "2", "3"]


def test_a_file_without_any_directive_falls_back_to_the_existing_no_header_rule(tmp_path):
    t = read(write(tmp_path, "plain.xvg", "# just data\n0 1 2\n1 2 3\n2 3 4\n"))
    assert t.status == "unsure", "与没有表头的 .txt 同一条兜底：猜第一行当表头、请用户确认"
    assert t.summary.columns == ["0", "1", "2"]


def test_windows_line_endings_give_the_same_table(tmp_path):
    a = read(write(tmp_path, "u.xvg", ENERGY)).summary
    b = read(write(tmp_path, "w.xvg", ENERGY, "\r\n")).summary
    assert (a.columns, a.rows, a.row_numbers) == (b.columns, b.rows, b.row_numbers)


@pytest.mark.parametrize(
    ("name", "text", "code"),
    [
        ("empty", "", "data_empty"),
        ("blank", "\n\n  \n", "data_empty"),
        ("comments", '# a\n# b\n@ title "x"\n@ xaxis label "t"\n', "data_not_table"),
        ("ragged", '@ xaxis label "t"\n@ s0 legend "a"\n0 1\n30.0\n40.0 0.3\n1 2\n', "data_ragged"),
        ("dup", '@ s0 legend "a"\n@ s1 legend "a"\n0 1 2\n1 2 3\n2 3 4\n', "data_header_invalid"),
        ("binary", "0 1\n\x00\x01\n", "data_binary"),
    ],
)
def test_unreadable_files_keep_the_existing_stable_codes(tmp_path, name, text, code):
    t = read(write(tmp_path, f"{name}.xvg", text))
    assert t.status == "unreadable" and t.code == code


def test_a_user_header_row_still_overrides(tmp_path):
    p = write(tmp_path, "e.xvg", ENERGY)
    t = adf.read_text_table(p, header_row=15)  # 把第一条数据行当表头（用户另指一行）
    assert t.summary.columns == ["0.000000", "-503.1", "210.5", "300.1"]


# =========================================================================== 标签清洗
@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Time (ps)", "Time (ps)"),
        (r"nm\S2\N", "nm2"),  # 上标：去标记、留文字
        (r"H\s2\NO", "H2O"),  # 下标
        (r"D (10\S-5\N cm\S2\N/s)", "D (10-5 cm2/s)"),
        (r"\xl\f{} (kJ/mol)", "λ (kJ/mol)"),  # Symbol 字体的希腊字母
        (r"\xD\f{}G", "ΔG"),
        (r"\xm\f{}m", "μm"),
        (r"\f{Times-Roman}Energy\f{}", "Energy"),  # 字体切换去掉
        (r"keep \q unknown", r"keep \q unknown"),  # 认不出的原样保留
        (r"\x1 digit", r"\x1 digit"),  # \x 后不是字母：不猜
        ("  spaced   out  ", "spaced out"),
    ],
)
def test_grace_label_cleaning_is_conservative(raw, clean):
    assert adf.xvg_label(raw) == clean


def test_cleaned_labels_reach_the_columns(tmp_path):
    t = '@ xaxis label "Time (\\xm\\f{}s)"\n@ s0 legend "MSD (nm\\S2\\N)"\n0 1\n1 2\n2 3\n'
    assert read(write(tmp_path, "m.xvg", t)).summary.columns == ["Time (μs)", "MSD (nm2)"]


# =========================================================================== 发现 / 概览 / 脚本
def test_discovery_overview_and_data_files_list_xvg(project):
    m, client, figs, _ = project
    d = figs / "data"
    d.mkdir()
    (d / "energy.xvg").write_text(ENERGY, encoding="utf-8")
    prof = adf.profile_folder(d)
    assert [e.path for e in prof.tables] == ["energy.xvg"]
    r = client.get("/api/ai/data-overview?path=data/energy.xvg")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert [f["name"] for f in body["fields"]] == [
        "Time (ps)",
        "Potential",
        "Kinetic En.",
        "Temperature",
    ]
    files = client.get("/api/ai/data-files?formats=all").get_json()["files"]
    assert any(f["path"].endswith("energy.xvg") for f in files)


def test_the_script_reader_expression_for_xvg_goes_through_xvg_text(tmp_path):
    p = write(tmp_path, "rmsd.xvg", RMS)
    s = adf.inspect_data_source(p, v2=True)
    entry = adf.TableEntry("rmsd.xvg", None, False, s)
    expr = tpl._reader_expr(entry, False)
    assert expr == "table_from_text(xvg_text(DATA.read_bytes().decode('utf-8-sig')), '\\t')[:2]"
    assert tpl.xvg_text in tpl.RUNTIME_FUNCTIONS and tpl.xvg_label in tpl.RUNTIME_FUNCTIONS
    # 脚本里读到的就是父进程读到的那张表（同一份函数源码）
    src = tpl.runtime_module()[1].decode("utf-8")
    ns: dict = {}
    exec(compile(src, "rt", "exec"), ns)  # 公共模块只用标准库、不含数据
    header, rows, _ = ns["table_from_text"](ns["xvg_text"](RMS), "\t")
    assert header == s.columns and rows == s.rows


@needs_worker
def test_xvg_proposes_commits_and_the_script_runs_on_its_own(project, tmp_path):
    m, client, figs, _ = project
    d = figs / "data"
    d.mkdir()
    f = d / "energy.xvg"
    f.write_bytes(ENERGY.replace("\n", "\r\n").encode("utf-8"))
    before = tree_state(figs)
    body = propose(client, render="lazy").get_json()
    assert body["kind"] == "candidates", body
    assert body["table"] == "energy.xvg" and body["roles"]["y"] == [
        "Potential",
        "Kinetic En.",
        "Temperature",
    ]
    assert body["roles"]["x"] == "Time (ps)"
    assert tree_state(figs) == before, "出备选不碰用户文件夹"
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "tp.xvg_text(" in script and "'../energy.xvg'" in script
    checks = checks_of(out["task"]["task_id"])
    assert checks["render.figure"] == ("verified", None)
    after = {p: s for p, *s in tree_state(figs)}
    for rel, is_dir, *rest in before:
        if not is_dir and rel != "tavotto_registry.json":
            assert after[rel] == [is_dir, *rest], f"{rel} 被改动"
    # 脱离 Tavotto、换 cwd 独立重跑
    import shutil

    work = tmp_path / "rerun"
    shutil.copytree(d, work / "data")
    env = {**os.environ, "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(tmp_path / "mpl")}
    r = subprocess.run(
        [WORKER_PY, str(work / "data" / "tavotto_figures" / Path(out["script"]).name)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr[-600:]
    assert list((work / "data" / "tavotto_figures").glob("*.pdf"))


# =========================================================================== 与合画的交叉
def _xvg(n: int, offset: float, names=("Time (ps)", "RMSD (nm)")) -> str:
    rows = "".join(f"  {k * 10.0:.1f}  {0.1 * k + offset:.4f}\n" for k in range(6))
    return f'# run {n}\n@    xaxis  label "{names[0]}"\n@    yaxis  label "{names[1]}"\n@TYPE xy\n{rows}'


def _merge_propose(client, tables, **kw):
    return propose(client, tables=tables, render="lazy", **kw)


@needs_worker
def test_xvg_files_merge_into_one_figure_grouped_by_file_and_the_script_runs_alone(
    project, tmp_path
):
    m, client, figs, _ = project
    d = figs / "data"
    d.mkdir()
    for n, name in enumerate(("rms_ctrl", "rms_drugA", "rms_drugB")):
        (d / f"{name}.xvg").write_text(_xvg(n, n * 0.05), encoding="utf-8")
    tables = [{"path": f"{n}.xvg"} for n in ("rms_ctrl", "rms_drugA", "rms_drugB")]
    body = _merge_propose(client, tables).get_json()
    assert body["kind"] == "candidates", body
    assert [s["label"] for s in body["sources"]] == ["rms_ctrl", "rms_drugA", "rms_drugB"]
    assert body["group_column"] == "file" and body["roles"]["group"] == "file"
    assert body["roles"]["x"] == "Time (ps)" and body["roles"]["y"] == ["RMSD (nm)"]
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    assert out["script"] == "data/tavotto_figures/rms_RMSD_nm_by_file.py"
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert script.count("tp.xvg_text(") == 3 and "tp.merge_tables(" in script
    import shutil

    work = tmp_path / "rerun"
    shutil.copytree(d, work / "data")
    env = {**os.environ, "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(tmp_path / "mpl")}
    r = subprocess.run(
        [WORKER_PY, str(work / "data" / "tavotto_figures" / Path(out["script"]).name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120, cwd=str(tmp_path),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr[-600:]
    assert list((work / "data" / "tavotto_figures").glob("*.pdf"))


@needs_worker
def test_xvg_and_csv_merge_when_the_column_names_match_and_refuse_when_they_do_not(project):
    m, client, figs, _ = project
    d = figs / "data"
    d.mkdir()
    (d / "a.xvg").write_text(_xvg(0, 0.0), encoding="utf-8")
    (d / "b.csv").write_text(
        "Time (ps),RMSD (nm)\n" + "".join(f"{k * 10.0},{0.1 * k + 0.2}\n" for k in range(6)),
        encoding="utf-8",
    )
    (d / "c.csv").write_text("Time (ps),other\n0,1\n10,2\n20,3\n", encoding="utf-8")
    same = _merge_propose(client, [{"path": "a.xvg"}, {"path": "b.csv"}]).get_json()
    assert same["kind"] == "candidates", same
    assert [s["label"] for s in same["sources"]] == ["a", "b"]
    out = commit(client, same["session"], same["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    diff = _merge_propose(client, [{"path": "a.xvg"}, {"path": "c.csv"}])
    assert diff.status_code == 400 and diff.get_json()["code"] == "tables_columns_differ"
    assert diff.get_json()["params"] == {"index": 1}
    # xvg 之间列名不同（legend 不同）同样拒绝
    (d / "e.xvg").write_text(_xvg(1, 0.0, ("Time (ps)", "Rg (nm)")), encoding="utf-8")
    r = _merge_propose(client, [{"path": "a.xvg"}, {"path": "e.xvg"}])
    assert r.status_code == 400 and r.get_json()["code"] == "tables_columns_differ"
