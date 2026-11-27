"""数据出图 v2 的文本编码（ADR 0178「文本表格」的编码一节）与「数据文件夹写不进」的稳定码。

* 编码规则只有一条：BOM → 严格 UTF-8 → 严格 GB18030（含合理性检查）→ 否则 `data_decode_error`；
* 父进程读的和生成的脚本读的是同一段文本（同一 codec 名、同一份字节）：真 worker 端到端 + 「脚本用了别的编码」的反证；
* UTF-8 / UTF-8 BOM 的脚本与此前逐字相同（公共模块哈希不换）；
* 只读目录 / 磁盘满：稳定错误码、不留半截文件、不带路径。
"""

# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名

from __future__ import annotations

import dataclasses
import errno
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_overview as ov,
    ai_data_task as task_mod,
    ai_data_template as tpl,
    scriptlock,
)
from tests.test_ai_data_propose import (
    commit,
    needs_worker,
    project,  # noqa: F401
    propose,
    tree_state,
)

HEADER = ["组别", "电压_mV"]


def zh_rows():
    rows = [HEADER]
    for gi, g in enumerate(("对照", "药物甲", "药物乙")):
        for k in range(6):
            rows.append([g, round(2 + gi * 1.5 + k * 0.3 + (k % 3) * 0.1, 3)])
    return rows


def zh_text(rows=None) -> str:
    return "\r\n".join(",".join(str(c) for c in r) for r in (rows or zh_rows())) + "\r\n"


ENCODED = {
    "utf-8": (lambda t: t.encode("utf-8"), "utf-8"),
    "utf-8-bom": (lambda t: b"\xef\xbb\xbf" + t.encode("utf-8"), "utf-8-bom"),
    "utf-16le": (lambda t: b"\xff\xfe" + t.encode("utf-16-le"), "utf-16le"),
    "utf-16be": (lambda t: b"\xfe\xff" + t.encode("utf-16-be"), "utf-16be"),
    "gb18030": (lambda t: t.encode("gb18030"), "gb18030"),
    "gbk": (lambda t: t.encode("gbk"), "gb18030"),
    "gb2312": (lambda t: t.encode("gb2312"), "gb18030"),
}


def tree_sans_dir_mtime(base: Path) -> list[tuple]:
    """`tree_state` 去掉目录的 mtime（建了又删一个子目录必然动父目录的 mtime；文件仍逐字节 + mtime 比对）。"""
    return [
        (rel, is_dir, size, None if is_dir else mt, data)
        for rel, is_dir, size, mt, data in tree_state(base)
    ]


def write(tmp_path: Path, name: str, raw: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(raw)
    return p


# =========================================================================== 解码规则
@pytest.mark.parametrize("kind", sorted(ENCODED))
def test_every_supported_encoding_reads_the_same_table_as_the_utf8_file(tmp_path, kind):
    enc, label = ENCODED[kind]
    ref = adf.read_text_table(write(tmp_path, "ref.csv", zh_text().encode("utf-8")))
    got = adf.read_text_table(write(tmp_path, "t.csv", enc(zh_text())))
    assert got.status == "ok" and got.encoding == label, got
    assert ref.summary is not None and got.summary is not None
    assert got.summary.columns == ref.summary.columns == HEADER
    assert got.summary.rows == ref.summary.rows
    assert got.summary.encoding == label
    assert {r[0] for r in got.summary.rows} == {"对照", "药物甲", "药物乙"}
    assert got.to_dict()["encoding"] == label
    # 摘要里列的类型 / 分组也与 UTF-8 的一样（编码不改变任何后续判定）
    assert got.summary.groups == ref.summary.groups and got.summary.layout == ref.summary.layout
    # v2 入口同一份
    assert adf.inspect_data_source(tmp_path / "t.csv", v2=True).rows == ref.summary.rows


def test_the_sha_is_of_the_original_bytes_whatever_the_encoding(tmp_path):
    raw = zh_text().encode("gb18030")
    t = adf.read_text_table(write(tmp_path, "t.csv", raw))
    assert t.summary.sha256 == hashlib.sha256(raw).hexdigest() and t.summary.size == len(raw)


def test_decode_rule_order_is_bom_then_utf8_then_gb18030():
    assert adf.decode_text("中文".encode("utf-8")) == ("中文", "utf-8")
    assert adf.decode_text("abc".encode("ascii")) == ("abc", "utf-8")  # 纯 ASCII 是 UTF-8
    assert adf.decode_text(b"\xef\xbb\xbfabc") == ("abc", "utf-8-bom")
    assert adf.decode_text(b"\xff\xfea\x00") == ("a", "utf-16le")
    assert adf.decode_text(b"\xfe\xff\x00a") == ("a", "utf-16be")
    assert adf.decode_text("中文".encode("gb18030")) == ("中文", "gb18030")
    # 4 字节的 GB18030（生僻 / 扩展区）也认
    assert adf.decode_text("㐀".encode("gb18030")) == ("㐀", "gb18030")


def test_undecodable_or_implausible_bytes_are_refused_not_turned_into_mojibake(tmp_path):
    for raw in (
        b"a,b\n\x81\x20,1\n",  # GB18030 里的非法双字节
        "a,b\nx,1\n".encode("utf-32"),  # UTF-32：不支持
        "a,b\nx,1\n".encode("utf-16")[:-1],  # UTF-16 奇数字节
        b"a,b\n\xaa\xa1,1\n",  # GB18030 合法但落在私用区（多半是别的编码被硬解）
    ):
        assert adf.decode_text(raw) is None, raw
        t = adf.read_text_table(write(tmp_path, "bad.csv", raw))
        assert (t.status, t.code, t.encoding) == ("unreadable", "data_decode_error", None), raw
    # Big5 / Shift-JIS / Latin-1 不在支持里：Latin-1 的 é（0xE9）后面跟 ASCII 不是合法 UTF-8 / GB18030
    assert adf.decode_text("a,b\ncafé,1\n".encode("latin-1")) is None
    assert "latin" not in " ".join(adf.TEXT_ENCODINGS) and "big5" not in adf.TEXT_ENCODINGS


def test_utf16_nul_bytes_are_not_binary_but_a_real_nul_still_is(tmp_path):
    ok = adf.read_text_table(write(tmp_path, "a.csv", b"\xff\xfe" + zh_text().encode("utf-16-le")))
    assert ok.status == "ok"
    nul = b"\xff\xfe" + ("a,b\r\n1,2\r\n\x00,3\r\n").encode("utf-16-le")
    t = adf.read_text_table(write(tmp_path, "n.csv", nul))
    assert (t.status, t.code) == ("unreadable", "data_binary")
    t = adf.read_text_table(write(tmp_path, "b.csv", b"a,b\n1,2\n\x00\x00\x00\n"))
    assert (t.status, t.code) == ("unreadable", "data_binary")


def test_a_file_larger_than_the_sniff_window_is_decoded_whole_so_a_cut_inside_a_character_cannot_fail(
    tmp_path, monkeypatch
):
    # 整份文件（≤ MAX_BYTES）一次性解码，编码不靠开头一段猜：无论 256 KiB 的嗅探窗口切在哪个多字节字符中间，结果都一样
    for pad in range(0, 4):
        rows = [HEADER] + [["对照" + "x" * pad, i] for i in range(40000)]
        raw = zh_text(rows).encode("gb18030")
        assert len(raw) > adf.SNIFF_CHARS
        t = adf.read_text_table(write(tmp_path, f"big{pad}.csv", raw))
        assert t.status == "ok" and t.encoding == "gb18030" and t.rows == 40000, (pad, t.code)
    monkeypatch.setattr(adf, "MAX_BYTES", adf.SMALL_BYTES * 2 - 1)  # 大表硬上限收紧到刚好放不下
    t = adf.read_text_table(write(tmp_path, "huge.csv", b"\xc4\xe3" * adf.SMALL_BYTES))
    assert t.code == "data_too_large"


def test_utf8_wins_even_when_the_bytes_are_also_valid_gb18030(tmp_path):
    raw = "a,b\n的的,1\n的的,2\n的的,3\n".encode("utf-8")
    assert raw.decode("gb18030") != raw.decode(
        "utf-8"
    )  # 这些字节两种都解得开（GB18030 的读法是乱码）
    t = adf.read_text_table(write(tmp_path, "t.csv", raw))
    assert t.encoding == "utf-8" and t.summary.rows[0][0] == "的的"


def test_text_in_a_decoded_table_is_still_untrusted_preview_has_no_control_chars(tmp_path):
    text = "标题\x1b[31m说明\n甲 乙\n(a) (b)\n1 2\n3 4\n"
    t = adf.read_text_table(write(tmp_path, "p.txt", text.encode("gb18030")))
    assert t.encoding == "gb18030"
    for _n, line in t.preview + t.lead:
        assert all(ch >= " " for ch in line)


# =========================================================================== 脚本读法
def _entry(tmp_path, name, raw):
    p = write(tmp_path, name, raw)
    s = adf.inspect_data_source(p, v2=True)
    return adf.TableEntry(name, None, False, s), s


@pytest.mark.parametrize(
    ("kind", "codec"),
    [("gb18030", "gb18030"), ("gbk", "gb18030"), ("utf-16le", "utf-16"), ("utf-16be", "utf-16")],
)
def test_the_script_names_the_codec_as_a_literal_and_the_runtime_is_unchanged(
    tmp_path, kind, codec
):
    enc, _label = ENCODED[kind]
    entry, s = _entry(tmp_path, "t.csv", enc(zh_text()))
    expr = tpl._reader_expr(entry, False)
    assert expr == f"table_from_text(DATA.read_bytes().decode({codec!r}), ',')[:2]"
    # 脚本那一句与父进程读出的是同一张表（公共模块源码被 exec 后跑这一句）
    ns: dict = {}
    exec(compile(tpl._runtime_source(), "<tavotto_plot_runtime>", "exec"), ns)  # noqa: S102 - 我们自己生成的公共模块
    ns["DATA"] = tmp_path / "t.csv"
    header, rows = eval(expr, ns)  # noqa: S307
    assert header == s.columns and rows == s.rows


@pytest.mark.parametrize("kind", ["utf-8", "utf-8-bom"])
def test_utf8_scripts_are_byte_identical_to_before(tmp_path, kind):
    enc, _label = ENCODED[kind]
    entry, s = _entry(tmp_path, "t.csv", enc(zh_text()))
    assert tpl._reader_expr(entry, False) == "read_delimited(DATA, ',')"
    # 编码标注不进脚本：把标注抹掉（= 本 PR 之前的摘要）渲染出的脚本与现在逐字相同
    old = adf.TableEntry("t.csv", None, False, dataclasses.replace(s, encoding=None))
    assert tpl._reader_expr(old, False) == tpl._reader_expr(entry, False)


def test_the_common_module_did_not_change_so_its_hash_and_name_stay_put():
    # 编码处理只在父进程与脚本里的一句表达式，没有往公共模块加函数：文件名里的 8 位哈希与改动前相同
    name, data = tpl.runtime_module()
    assert name == f"tavotto_plot_{hashlib.sha256(data).hexdigest()[:8]}.py"
    assert "gb18030" not in data.decode("utf-8") and "decode_text" not in data.decode("utf-8")
    assert name == PINNED_RUNTIME_NAME


#: 公共模块文件名；公共模块有意变更时同步更新这一行（改动前 origin/main f1aee0e8d 是 `tavotto_plot_1d525a3a.py`；
#: 2026-11-17「表头后紧跟单位行」给 `table_from_text` / `read_delimited` 加了 `units_row` 参数 → `tavotto_plot_3c540192.py`；
#: 同日用户决定图里不写任何脚注，删掉只给脚注用的运行时函数（`n_note` / `n_phrase` / `trend_point_counts` / `trend_series_counts`）→ `tavotto_plot_86dcfebb.py`；
#: 2026-11-18 合画（几份列名相同的数据拼成一张图）给公共模块加了 `merge_tables` → `tavotto_plot_76e4a6f6.py`；
#: 同日合画标签撞车保险给它加了 `group_key` 与标签重复检查 → `tavotto_plot_642ce634.py`；
#: 同日认 `.xvg`（`xvg_text` / `xvg_label` 进公共模块）→ `tavotto_plot_8dccb037.py`；
#: 2026-11-19 大表（ADR 0178 修订）：`table_from_text` / `delimited_records` 改成流式 `iter_table` / `iter_records` 的整份文本版 → `tavotto_plot_98157f49.py`；
#: 2026-11-21 整数编码的分组（`knockout` 0 / 1）：公共模块多了 `NumCat` / `apply_numcat`，`distinct` 遇到整列 `NumCat` 按数值升序 → `tavotto_plot_f7aa140e.py`；
#: 同日「把列当分组」（宽表转长表）：公共模块多了 `apply_stack` → `tavotto_plot_3bf67213.py`；
#: 2026-11-23 按期刊字号范围出图：公共模块多了 `fit_fonts` / `place_legends` / `build_legend` / `legend_hits_data`（图例图内放不下挪到图外；文字互相干涉时同一类一起退字号）→ `tavotto_plot_e44d5b65.py`；
#: 2026-11-24 没画的行要有计数（G-055）：大表缩减那一遍多记两个数（`key_empty` / `y_invalid`）→ `tavotto_plot_5b98475c.py`；
#: 同日误差线取自现成的误差列（`FigureRecipe.err`）：`comparison_data` / `trend_data` 多一个可选的 `err` 参数，公共模块多了 `comparison_error_points` / `trend_error_points` → `tavotto_plot_3212ec84.py`；
#: 2026-11-25 现成误差列的多个 y（每个 y 各配各的误差列）：`trend_data` 的 `err` 也可以是与 `ys` 等长的列名数组 → `tavotto_plot_b40c1579.py`；
#: 2026-11-27 表头第一格为空时叫 `col1`：公共模块多了 `fill_header`（`iter_table` / `read_xlsx` 调用）→ `tavotto_plot_ee3e54e2.py`；
#: 2026-11-27 表头末尾没有名字、整列都是空的列（Excel 另存 CSV 多出来的）丢掉：公共模块多了 `NamelessTailData` / `nameless_tail` / `cut_tail` / `_trim_tail`（`iter_table` / `read_xlsx` 调用） → `tavotto_plot_9d371be6.py`；
#: 2026-11-27 XLSX 开头有标题行：`read_xlsx` 多一个可选的表头行号参数（只在真的跳过了行时写进脚本） → `tavotto_plot_3923ef3f.py`；
#: 2026-11-27 没有表头行（第一行整行都是数值）：`iter_table` / `table_from_text` / `read_delimited` / `reduce_file` / `read_reduced` 多一个可选的 `no_header` 参数 → `tavotto_plot_64a121df.py`；
#: 2026-11-27 日期写法（中文年月日、`YYYY/M/D`、带时刻的 ISO）：公共模块多了 `parse_when`，`date_number` 认带时刻的日期 → 这一个）
PINNED_RUNTIME_NAME = "tavotto_plot_e464e904.py"


# =========================================================================== 接口
def test_data_files_and_overview_carry_the_encoding(project):
    m, client, figs, _state = project
    (figs / "d").mkdir()
    (figs / "d" / "u.csv").write_bytes(zh_text().encode("utf-8"))
    (figs / "d" / "g.csv").write_bytes(zh_text().encode("gb18030"))
    (figs / "d" / "w.csv").write_bytes(b"\xff\xfe" + zh_text().encode("utf-16-le"))
    (figs / "d" / "bad.csv").write_bytes(b"a,b\n\x81\x20,1\n")
    files = {f["path"]: f for f in client.get("/api/ai/data-files?formats=all").get_json()["files"]}
    assert files["d/u.csv"]["table"]["encoding"] == "utf-8"
    assert files["d/g.csv"]["table"]["encoding"] == "gb18030"
    assert files["d/w.csv"]["table"]["encoding"] == "utf-16le"
    assert files["d/bad.csv"]["table"]["code"] == "data_decode_error"
    assert files["d/bad.csv"]["table"]["encoding"] is None
    for name, want in (("g.csv", "gb18030"), ("w.csv", "utf-16le"), ("u.csv", "utf-8")):
        body = client.get(f"/api/ai/data-overview?path=d/{name}").get_json()
        assert body["status"] == "ok" and body["encoding"] == want, body
        assert [f["name"] for f in body["fields"]] == HEADER
        assert body["preview_rows"][0]["cells"] == ["对照", "2.0"]
    bad = client.get("/api/ai/data-overview?path=d/bad.csv").get_json()
    assert bad["status"] == "unreadable" and bad["code"] == "data_decode_error"
    assert bad["encoding"] is None
    assert set(ov._base("x", "ok")) >= {"encoding"}


def test_the_flag_off_responses_are_untouched(project):
    m, client, figs, state = project
    (figs / "d").mkdir()
    (figs / "d" / "g.csv").write_bytes(zh_text().encode("gb18030"))
    state["taf"] = False
    for url in ("/api/ai/data-files?formats=all", "/api/ai/data-overview?path=d/g.csv"):
        r = client.get(url)
        assert r.status_code == 404 and r.get_json()["code"] == "taf_disabled"


# =========================================================================== 端到端（真 worker）
def _put_raw(figs: Path, name: str, raw: bytes) -> Path:
    (figs / "data").mkdir(exist_ok=True)
    p = figs / "data" / name
    p.write_bytes(raw)
    return p


@needs_worker
@pytest.mark.parametrize("kind", ["gb18030", "gbk", "utf-16le", "utf-16be", "utf-8-bom"])
def test_non_utf8_tables_go_end_to_end_and_the_fingerprints_match(project, kind):
    m, client, figs, _ = project
    enc, label = ENCODED[kind]
    raw = enc(zh_text())
    data = _put_raw(figs, "g.csv", raw)
    before = tree_state(figs)
    body = propose(client).get_json()
    assert body["kind"] == "candidates", body
    assert not any(d["reason_code"] == "candidate_fingerprint_mismatch" for d in body["dropped"])
    assert tree_state(figs) == before, "出备选不碰用户文件夹"
    first = body["items"][0]
    assert first["values"]["table"] == "g.csv" and first["values"]["y"] == ["电压_mV"]
    out = commit(client, body["session"], first["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    script = (figs / out["script"]).read_text(encoding="utf-8")
    codec = adf.script_codec(label)
    if codec:
        assert f"decode({codec!r})" in script
    else:
        assert "read_delimited(DATA, ',')" in script
    assert data.read_bytes() == raw, "用户的数据文件一个字节都没动"


@needs_worker
def test_a_script_reading_with_the_wrong_codec_is_dropped_not_plotted(project, monkeypatch):
    m, client, figs, _ = project
    _put_raw(figs, "g.csv", zh_text().encode("gb18030"))
    # Latin-1 什么字节都解得开：脚本若用它读，读出的是乱码表，必须被「渲染 / 指纹」这一关剔除
    monkeypatch.setattr(adf, "script_codec", lambda enc: "latin-1" if enc else None)
    body = propose(client).get_json()
    assert body["kind"] != "candidates", body
    reasons = {d["reason_code"] for d in body.get("dropped", [])}
    assert reasons and reasons <= {"candidate_fingerprint_mismatch", "candidate_render_failed"}, (
        body
    )


# =========================================================================== 写不进数据文件夹
skip_perm = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="not_run：需要非 root 的 POSIX 才能用目录权限模拟只读",
)


def _prepared(client, figs):
    _put_raw(figs, "g.csv", zh_text().encode("utf-8"))
    body = propose(client).get_json()
    assert body["kind"] == "candidates", body
    return body["session"], body["items"][0]["id"]


def _no_residue(figs: Path):
    data = figs / "data"
    assert (
        not (data / "tavotto_figures").exists() or list((data / "tavotto_figures").iterdir()) == []
    )


@needs_worker
@skip_perm
def test_commit_into_a_read_only_data_folder_gets_a_stable_code_and_leaves_nothing(project):
    m, client, figs, _ = project
    session, item = _prepared(client, figs)
    data = figs / "data"
    snapshot = tree_sans_dir_mtime(figs)
    os.chmod(data, 0o555)
    try:
        r = commit(client, session, item)
    finally:
        os.chmod(data, 0o755)
    out = r.get_json()
    assert r.status_code == 200 and out["task"]["outcome"] == "failed", out
    assert out["task"]["error"] == "data_folder_not_writable", out
    assert "script" not in out
    assert str(figs) not in json.dumps(out), "不泄露绝对路径"
    assert tree_sans_dir_mtime(figs) == snapshot
    # 权限恢复后同一个备选能正常落盘（失败没有毒化会话或留下占位）
    ok = commit(client, session, item).get_json()
    assert ok["task"]["outcome"] == "completed", ok


@needs_worker
@skip_perm
def test_commit_into_a_read_only_figures_dir_leaves_no_half_written_pair(project):
    m, client, figs, _ = project
    session, item = _prepared(client, figs)
    fdir = figs / "data" / "tavotto_figures"
    fdir.mkdir()
    snapshot = tree_sans_dir_mtime(figs)
    os.chmod(fdir, 0o555)
    try:
        out = commit(client, session, item).get_json()
    finally:
        os.chmod(fdir, 0o755)
    assert out["task"]["error"] == "data_folder_not_writable", out
    assert str(figs) not in json.dumps(out)
    assert tree_sans_dir_mtime(figs) == snapshot


@needs_worker
@pytest.mark.parametrize(
    ("fail_on", "err", "code"),
    [
        (1, errno.ENOSPC, "disk_full"),  # 公共模块写不下
        (2, errno.ENOSPC, "disk_full"),  # 公共模块写成了，脚本写不下：模块与新建的目录要撤回
        (2, errno.EROFS, "data_folder_not_writable"),
        (2, errno.EACCES, "data_folder_not_writable"),
    ],
)
def test_a_failed_write_in_the_pair_undoes_the_whole_pair(project, monkeypatch, fail_on, err, code):
    m, client, figs, _ = project
    session, item = _prepared(client, figs)
    snapshot = tree_sans_dir_mtime(figs)
    real = scriptlock.create_script
    calls = {"n": 0}

    def flaky(path, data):
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise OSError(err, os.strerror(err), str(path))  # 异常里带着绝对路径：响应里不许出现
        return real(path, data)

    monkeypatch.setattr(scriptlock, "create_script", flaky)
    out = commit(client, session, item).get_json()
    assert out["task"]["outcome"] == "failed" and out["task"]["error"] == code, out
    assert str(figs) not in json.dumps(out) and "script" not in out
    assert tree_sans_dir_mtime(figs) == snapshot, (
        "公共模块与脚本要么都在要么都不在；本次新建的目录也撤回"
    )


def test_write_failure_mapping_only_claims_the_distinguishable_cases():
    wf = task_mod.write_failure
    assert wf(PermissionError(errno.EACCES, "x")).code == "data_folder_not_writable"
    assert wf(OSError(errno.EROFS, "x")).code == "data_folder_not_writable"
    assert wf(OSError(errno.EPERM, "x")).code == "data_folder_not_writable"
    assert wf(OSError(errno.ENOSPC, "x")).code == "disk_full"
    assert wf(OSError(errno.EIO, "x")) is None and wf(ValueError("x")) is None
    assert set(task_mod.WRITE_ERROR_CODES) == {"data_folder_not_writable", "disk_full"}
