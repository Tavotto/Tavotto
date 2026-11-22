"""数据出图 v2 的大表（ADR 0178「修订（2026-11-19）：大表」）：流式读 + 整表统计。

判据的主语：

* 「流式读 == 整份读」量的是**同一个文件**在两条路径（小表整份读 / 大表流式读）下 `read_text_table` 的返回值逐字段相等
  （状态、码、分隔符、表头行、编码、预览、说明行、摘要的列统计 / 布局 / 分组 / 编号线索 / sha256）——不是看一个中间量；
* 「小表不变」量的是 `read_text_table` 的小表分支仍返回带行数据的摘要（`large is None`），而界线只由 `SMALL_BYTES` / `SMALL_ROWS` 决定；
* 「大表摘要不带行」量的是 `rows == []` 且 `large` 的整表统计与**独立算出来的**值（用 `statistics` / 手算）相等；
* 上限：超过 `MAX_BYTES` / `MAX_ROWS` 仍是原来的稳定码，不截断。
"""

# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
from __future__ import annotations

import io
import math
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_recipe as recipe_mod,
    ai_data_template as tpl,
)
from tests.test_ai_data_encodings import ENCODED, zh_text
from tests.test_ai_data_propose import (  # noqa: F401 - `project` 是 fixture
    checks_of,
    commit,
    needs_worker,
    project,
    propose,
    put,
    tree_state,
)
from tests.test_ai_data_text_tables import CORPUS, INSTRUMENT, RAGGED


def write(tmp_path: Path, name: str, text: str = "", *, raw: bytes | None = None) -> Path:
    p = tmp_path / name
    p.write_bytes(raw if raw is not None else text.encode("utf-8"))
    return p


def _summary_view(s: adf.DataSummary | None, n_preview: int = adf.LARGE_PREVIEW_ROWS):
    if s is None:
        return None
    shown, _more = s.preview(n_preview)
    return {
        "columns": s.columns,
        "per_column": {c: v.to_dict() for c, v in s.per_column.items()},
        "per_column_dates": {c: v.dates for c, v in s.per_column.items()},
        "layout": s.layout,
        "groups": s.groups,
        "id_like": s.id_like,
        "repeats": s.repeats,
        "sha256": s.sha256,
        "size": s.size,
        "delimiter": s.delimiter,
        "header_line": s.header_line,
        "encoding": s.encoding,
        "units_row": s.units_row,
        "rows": s.row_count if s.row_count is not None else len(s.rows),
        "preview": [(n, [str(c) for c in r]) for n, r in shown],
    }


def _view(t: adf.TextTable):
    return {
        "status": t.status,
        "code": t.code,
        "delimiter": t.delimiter,
        "header_line": t.header_line,
        "columns": t.columns,
        "rows": t.rows,
        "encoding": t.encoding,
        "preview": t.preview,
        "lead": t.lead,
        "summary": _summary_view(t.summary),
    }


def _both(p: Path, monkeypatch, **kw):
    small = adf.read_text_table(p, **kw)
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    large = adf.read_text_table(p, **kw)
    monkeypatch.undo()
    if large.summary is not None:  # 不是空转：确实走了流式读
        assert large.summary.large is not None and large.summary.rows == []
    return small, large


MORE = [
    ("a.dat", INSTRUMENT),
    ("units.dat", "Wavelength  Intensity\nnm  a.u.\n400 1.5\n410 1.7\n420 1.9\n"),
    ("two.dat", "title line\nx y\n1 2\n3 4\n"),
    ("nohdr.txt", "1 2\n3 4\n5 6\n"),
    ("prose.txt", "hello world\nthis is prose\nnothing here\n"),
    ("semi.csv", "x;y\n1,5;2,5\n3,5;4,5\n"),
    ("thou.csv", 'x,y\n"1,000",2\n"2,000",3\n"3,000",4\n'),
    ("blank.csv", "x,y\n\n1,2\n\n3,4\n"),
    ("crlf.csv", "x,y\r\n1,2\r\n3,4\r\n"),
    ("cr.csv", "x,y\r1,2\r3,4\r"),
    ("quoted.csv", 'x,y\n"a\nb",2\n3,4\n'),
    ("ids.csv", "subject_id,v\n1,2\n1,3\n2,4\n2,5\n"),
    ("ids2.csv", "subject,v\na,2\na,3\nb,4\nb,5\n"),
    ("grp.csv", "g,v\na,1\na,2\nb,3\nb,4\nb,5\n"),
    ("long.csv", "g,t,v\na,1,1\na,2,2\nb,1,3\nb,2,4\nb,3,5\n"),
    ("labels.csv", "name,a,b\nr1,1,2\nr2,3,4\nr3,5,6\n"),
    ("mixed.csv", "x,y\n1,2\n3,<0.1\n5,6\n"),
    ("dates.csv", "date,v\n2024-01-01,1\n2024-01-02,2\n"),
    ("empty.csv", "x,y\n"),
    ("hashrows.txt", "x y\n1 2\n# c\n#z 3\n5 6\n"),
]


@pytest.mark.parametrize(
    ("name", "text"), [*CORPUS, *MORE, *RAGGED], ids=lambda v: v if len(v) < 24 else None
)
def test_the_streaming_reader_agrees_with_the_whole_file_reader(tmp_path, monkeypatch, name, text):
    p = write(tmp_path, name, text)
    small, large = _both(p, monkeypatch)
    assert _view(large) == _view(small)
    for row in (1, 2, 3):
        s2, l2 = _both(p, monkeypatch, header_row=row)
        assert _view(l2) == _view(s2), row


@pytest.mark.parametrize("enc", list(ENCODED))
def test_the_streaming_reader_agrees_on_every_supported_encoding(tmp_path, monkeypatch, enc):
    encode, _label = ENCODED[enc]
    p = write(tmp_path, f"{enc}.csv", raw=encode(zh_text()))
    small, large = _both(p, monkeypatch)
    assert _view(large) == _view(small)


def test_a_file_that_is_not_decodable_or_is_binary_gets_the_same_code(tmp_path, monkeypatch):
    for name, raw in (
        ("bin.csv", b"a,b\n1,2\n\x00\x00\x00\n"),
        ("bad.csv", b"a,b\n\xff\xfe\xfd,2\n"),
        ("empty.csv", b"   \n\n"),
        ("u32.csv", b"\xff\xfe\x00\x00a\x00\x00\x00"),
    ):
        p = write(tmp_path, name, raw=raw)
        small, large = _both(p, monkeypatch)
        assert (large.status, large.code) == (small.status, small.code), name


def test_the_boundary_is_small_bytes_and_small_rows_and_small_files_are_untouched(
    tmp_path, monkeypatch
):
    p = write(tmp_path, "a.csv", "x,y\n" + "1,2\n" * 30)
    t = adf.read_text_table(p)
    assert t.summary.large is None and len(t.summary.rows) == 30
    monkeypatch.setattr(adf, "SMALL_ROWS", 29)  # 行数超过小表界线 → 大表读法
    t = adf.read_text_table(p)
    assert t.status == "ok" and t.summary.large is not None and t.summary.rows == []
    assert t.rows == 30 and t.summary.row_count == 30
    monkeypatch.undo()
    monkeypatch.setattr(adf, "SMALL_BYTES", 10)  # 字节数超过小表界线 → 大表读法
    t = adf.read_text_table(p)
    assert t.summary.large is not None and t.summary.row_count == 30


def test_large_summaries_carry_exact_whole_table_statistics(tmp_path, monkeypatch):
    rows = [(i * 0.5, (-1) ** i * i * 0.25, "" if i % 7 == 0 else i) for i in range(1, 2001)]
    text = "t,y,z\n" + "".join(f"{a},{b},{c}\n" for a, b, c in rows)
    p = write(tmp_path, "s.csv", text)
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    s = adf.read_text_table(p).summary
    st = s.large.stats
    ys = [b for _a, b, _c in rows]
    assert (st["y"].numeric, st["y"].lo, st["y"].hi) == (2000, min(ys), max(ys))
    zs = [c for _a, _b, c in rows if c != ""]
    assert (st["z"].numeric, st["z"].lo, st["z"].hi) == (len(zs), min(zs), max(zs))
    assert s.per_column["z"].empty == 2000 - len(zs)
    assert s.large.rows == 2000 and s.large.preview_numbers[:2] == [2, 3]
    assert len(s.large.preview) == adf.LARGE_PREVIEW_ROWS
    assert math.isclose(st["t"].hi, 1000.0)


def test_the_hard_limits_still_refuse_rather_than_truncate(tmp_path, monkeypatch):
    p = write(tmp_path, "a.csv", "x,y\n" + "1,2\n" * 40)
    monkeypatch.setattr(adf, "SMALL_ROWS", 5)
    monkeypatch.setattr(adf, "MAX_ROWS", 20)
    t = adf.read_text_table(p)
    assert (t.status, t.code) == ("unreadable", "data_too_long")
    monkeypatch.undo()
    monkeypatch.setattr(adf, "MAX_BYTES", 50)
    t = adf.read_text_table(p)
    assert (t.status, t.code) == ("unreadable", "data_too_large")


def test_v1_xlsx_and_merge_stay_small_only(tmp_path):
    p = write(tmp_path, "a.csv", "x,y\n" + "1,2\n" * (adf.SMALL_ROWS + 5))
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(p)  # v1 不认大表
    assert e.value.code == "data_too_long"


def test_the_scan_is_cached_per_file_state_but_never_for_unkeyed_reads(tmp_path, monkeypatch):
    p = write(tmp_path, "a.csv", "x,y\n" + "1,2\n" * 30)
    monkeypatch.setattr(adf, "SMALL_ROWS", 5)
    a = adf.read_text_table(p, use_cache=True)
    assert adf.read_text_table(p, use_cache=True) is a
    assert adf.read_text_table(p) is not a  # 不要缓存的读取（提交前核 sha256）总是重读
    p.write_bytes(p.read_bytes() + b"3,4\n")  # 文件变了（字节数变）→ 不命中
    assert adf.read_text_table(p, use_cache=True) is not a
    assert adf.read_text_table(p, use_cache=True).summary.row_count == 31


def test_xvg_is_not_supported_as_a_large_table(tmp_path, monkeypatch):
    p = write(tmp_path, "a.xvg", '@ xaxis label "t"\n0 1\n1 2\n2 3\n')
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    t = adf.read_text_table(p)
    assert (t.status, t.code) == ("unreadable", "data_too_large")


def test_the_whole_file_text_functions_are_thin_wrappers_over_the_streaming_ones():

    text = "# c\nx y\n1 2\n# d\n3 4\n"
    h, rows, lines = adf.table_from_text(text, "ws", 2)
    h2, body = adf.iter_table(io.StringIO(text, newline=""), "ws", 2)
    pairs = list(body)
    assert (h, rows, lines) == (h2, [c for _, c in pairs], [n for n, _ in pairs])


# =========================================================================== 缩减（趋势）：纯函数


def _reduce(text: str, spec: dict, tmp_path: Path):
    p = write(tmp_path, "r.csv", text)
    return tpl.reduce_file(str(p), ",", None, False, "utf-8", spec)


def _spec(ys, keys=(), bins=40, series_max=4, x="t"):
    return {"kind": "trend", "x": x, "ys": list(ys), "keys": list(keys), "date": False,
            "bins": bins, "series_max": series_max}  # fmt: skip


def _wave(n, *, spike_at=None):
    rows = []
    for i in range(n):
        a = math.sin(i / 37.0) + (i % 5) * 0.01
        b = math.cos(i / 53.0) * (1 + i / n)
        if spike_at is not None and i == spike_at:
            a += 9.0
        rows.append((i * 0.01, a, b))
    return rows


def _text(rows, group=None):
    head = "t,a,b" + (",g" if group else "")
    body = [
        ",".join([f"{t!r}", f"{a!r}", f"{b!r}"] + ([group[i % len(group)]] if group else []))
        for i, (t, a, b) in enumerate(rows)
    ]
    return head + "\n" + "\n".join(body) + "\n"


def test_the_reduction_keeps_every_global_extreme_the_ends_and_stays_bounded(tmp_path):
    rows = _wave(5000, spike_at=1234)
    header, kept, info = _reduce(_text(rows), _spec(["a", "b"]), tmp_path)
    assert header == ["t", "a", "b"] and info["rows"] == 5000
    assert len(kept) < 40 * (2 + 3 * 2) and info["bins"] <= 40
    ts = [float(r[0]) for r in kept]
    assert ts == sorted(ts)  # 文件顺序（这里文件按 t 排好）
    for col, j in (("a", 1), ("b", 2)):
        vals = [r[j] for r in rows]  # 独立算：整表的最小 / 最大
        got = [float(r[1 + (col == "b")]) for r in kept]
        assert min(got) == min(vals) and max(got) == max(vals)
    assert float(kept[0][0]) == rows[0][0] and float(kept[-1][0]) == rows[-1][0]
    assert any(float(r[1]) >= 9.0 for r in kept)  # 孤立的尖峰不能被抹掉
    assert info["y"][0] == (5000, min(r[1] for r in rows), max(r[1] for r in rows))
    assert info["mono"] is True and info["dup"] is False


def test_the_kept_rows_are_exactly_the_per_bin_extremes_at_the_final_grid(tmp_path):
    # 独立的暴力实现：按最终的（起点, 箱宽）把整表分箱，逐箱取 最左 / 最右 / 每个 y 的最小 / 最大——与流式合并出来的行集合必须一致
    rows = _wave(3000, spike_at=777)
    _h, kept, info = _reduce(_text(rows), _spec(["a", "b"], bins=25), tmp_path)
    ((o, w),) = info["grid"].values()
    bins: dict[int, list[tuple[int, tuple]]] = {}
    for n, r in enumerate(rows):
        bins.setdefault(math.floor((r[0] - o) / w), []).append((n, r))
    want: set[int] = set()
    for members in bins.values():
        want.add(min(members, key=lambda m: (m[1][0], m[0]))[0])
        want.add(max(members, key=lambda m: (m[1][0], -m[0]))[0])
        for j in (1, 2):
            want.add(min(members, key=lambda m: (m[1][j], m[0]))[0])
            want.add(max(members, key=lambda m: (m[1][j], -m[0]))[0])
    got = {round(float(r[0]) / 0.01) for r in kept}
    assert got == want


def test_small_series_are_kept_whole(tmp_path):
    rows = _wave(30)
    _h, kept, info = _reduce(_text(rows), _spec(["a"], bins=2000), tmp_path)
    assert len(kept) == 30 and info["kept"] == 30


def test_series_are_reduced_separately_by_group_and_unsorted_x_is_binned_by_value(tmp_path):
    rows = _wave(4000)
    text = _text(rows, group=["x", "y"])
    _h, kept, info = _reduce(text, _spec(["a"], keys=["g"], bins=30), tmp_path)
    assert info["series"] == 2
    for g in ("x", "y"):
        mine = [r for r in kept if r[3] == g]
        vals = [row[1] for i, row in enumerate(rows) if "xy"[i % 2] == g]
        assert min(float(r[1]) for r in mine) == min(vals) and max(
            float(r[1]) for r in mine
        ) == max(vals)
    shuffled = rows[::-1]  # 反着给：x 不是递增，仍按 x 值分箱，全局极值照样在
    _h, kept2, info2 = _reduce(_text(shuffled), _spec(["a"], bins=30), tmp_path)
    assert info2["mono"] is False and info2["dup"] is False
    assert min(float(r[1]) for r in kept2) == min(r[1] for r in rows)
    assert max(float(r[1]) for r in kept2) == max(r[1] for r in rows)


def test_a_gap_is_kept_and_rows_without_a_group_or_x_are_not_plotted(tmp_path):
    text = "t,a,g\n0,1,p\n1,,p\n2,3,p\n3,4,\n,5,p\nnan,6,p\n4,7,p\n"
    _h, kept, info = _reduce(text, _spec(["a"], keys=["g"], bins=2000), tmp_path)
    assert [r[0] for r in kept] == [
        "0",
        "1",
        "2",
        "4",
    ]  # 空分组行不画；x 空 / NaN 的行不画；空 y 保留（折线在此断开）
    assert info["x_invalid"] == 2 and info["examples"] == [6, 7]


def test_too_many_series_is_refused_not_silently_drawn(tmp_path):
    with pytest.raises(ValueError, match="reduce_series_overflow"):
        _reduce("t,a,g\n0,1,p\n1,2,q\n2,3,r\n", _spec(["a"], keys=["g"], series_max=2), tmp_path)


def test_duplicates_and_non_monotone_x_are_reported(tmp_path):
    _h, _k, info = _reduce("t,a\n0,1\n1,2\n1,3\n2,4\n", _spec(["a"]), tmp_path)
    assert info["dup"] is True and info["mono"] is True
    _h, _k, info = _reduce("t,a\n0,1\n2,2\n1,3\n", _spec(["a"]), tmp_path)
    assert info["mono"] is False
    assert tpl.reduce_file  # 引用，避免被当成没用到的导入
    assert (
        min(info["x_counts"]) >= 3 and 4 >= recipe_mod.MIN_X_TREND
    )  # 封顶 4 ≥ `MIN_X_TREND`：判「太少」不会被封顶吃掉


def test_the_budget_keeps_the_drawn_numbers_under_the_fingerprint_budget():
    import re

    src = (Path(adf.__file__).parent / "manifest.py").read_text(encoding="utf-8")
    budget = int(re.search(r"^FINGERPRINT_BUDGET = ([0-9_]+)", src, re.M).group(1).replace("_", ""))

    for ny in (1, 2, 3, 4):
        for series in (1, 6, 12, 48):
            s = adf.DataSummary("x.csv", "0" * 64, 1, ["t", "g", "f"] + [f"y{i}" for i in range(ny)], [], {}, groups={"g": series, "f": 1})  # fmt: skip
            s.per_column["t"] = adf.ColumnSummary("t", numeric=1)
            rec = recipe_mod.FigureRecipe.from_dict(
                {"table": "x.csv", "family": "trend", "why": "w",
                 "layers": [{"mark": "points"}, {"mark": "line", "role": "overlay"}],
                 "y": [f"y{i}" for i in range(ny)], "x": "t", "group": "g"}
            )  # fmt: skip
            spec = tpl.large_spec(s, rec)
            per_bin = 2 + 2 * ny
            rows = spec["bins"] * per_bin + ny * spec["gap_max"]
            worst = rows * series * ny * 2 * 2
            assert worst <= budget, (ny, series, spec["bins"])


# =========================================================================== 端到端：大表 → 出备选 → 缩略图 → 提交（真 safe worker）
BIG_N = 6000


def _trend_table(n=BIG_N, *, shuffle=False, dup=False):
    rows = [["time_s", "signal_mV", "other_mV"]]
    body = []
    for i in range(n):
        t = i if not dup else i // 2
        body.append(
            [round(t * 0.01, 4), round(math.sin(i / 41.0) + (i % 7) * 0.02, 5), round(i * 0.001, 5)]
        )
    if shuffle:
        body = body[::-1]
    return rows + body


@pytest.fixture
def big(project, monkeypatch):  # noqa: F811 - `project` 是 fixture
    """把「大表」界线压到 100 行：一张几千行的小文件就走大表读法与缩减，其余全是真流程（真 worker、真提交）。"""
    monkeypatch.setattr(adf, "SMALL_ROWS", 100)
    return project


def _items(body):
    return {i["title_code"]: i for i in body["items"]}


@needs_worker
def test_a_large_trend_goes_propose_thumbnail_commit_and_the_check_is_verified(big):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _trend_table())
    before = tree_state(figs)
    r = propose(client)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["kind"] == "candidates", body
    items = _items(body)
    assert "line_points" in items and not any(c in items for c in ("line_band", "scatter_fit")), (
        items.keys()
    )
    assert any(
        d["reason_code"] == "mark_not_allowed" for d in body["dropped"]
    )  # 汇总类画法在大表上被拒
    first = body["items"][0]
    t = client.get(first["thumbnail_url"])
    assert t.status_code == 200 and t.data.startswith(b"\x89PNG")
    assert tree_state(figs) == before  # 出备选不碰用户文件夹
    out = commit(client, body["session"], first["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "read_reduced(DATA," in script and "REDUCE = " in script
    checks = checks_of(out["task"]["task_id"])
    assert checks["render.figure"] == ("verified", None)
    assert any(
        k.startswith("data.") and k != "data.verifiable" and v[0] == "verified"
        for k, v in checks.items()
    )
    assert (figs / "data" / "run.csv").read_text(encoding="utf-8").count(
        "\n"
    ) == BIG_N + 1  # 数据原样


@needs_worker
def test_counter_proof_a_script_that_reduces_differently_turns_the_check_red(big, monkeypatch):
    m, client, figs, _ = big
    put(figs, "data/run.csv", _trend_table())
    real = tpl._large_reader_expr

    def wrong(entry):
        return real(entry).replace(
            ", REDUCE)", ", {**REDUCE, 'bins': 7})"
        )  # 脚本侧箱数不同：画出的点不是期望的那些

    monkeypatch.setattr(tpl, "_large_reader_expr", wrong)
    # 点名单列纵轴：不点名时宽表是两列纵轴，只画第一列的散点会因 `roles.y` 与图一致的规则被去掉（2026-11-21）
    body = propose(client, answers={"y": "signal_mV"}).get_json()
    # 趋势的备选（折线 + 点）被剔除；散点那张不受「箱数」影响，照常在（同一份数据在大表上有两类画法，只坏了其中一类）
    assert "line_points" not in _items(body), body
    assert any(d["reason_code"] == "candidate_fingerprint_mismatch" for d in body["dropped"]), body


@needs_worker
def test_a_large_line_over_unsorted_or_repeated_x_is_refused_with_the_small_table_code(big):
    m, client, figs, _ = big
    put(
        figs, "data/dup.csv", _trend_table(dup=True)
    )  # 同一 x 出现两次：折线会把同一 x 的两个 y 连起来
    body = propose(client, answers={"y": "signal_mV"}).get_json()
    assert body["kind"] == "candidates", body
    assert "line_points" not in _items(body) and set(_items(body)) <= {"scatter"}, body
    assert any(d["reason_code"] == "line_duplicate_x_unconfirmed" for d in body["dropped"]), body
    # 小表上同一份数据同一条规则：同一个码
    from unittest import mock

    with mock.patch.object(adf, "SMALL_ROWS", 10_000):
        small = propose(client, answers={"y": "signal_mV"}).get_json()
    assert any(d["reason_code"] == "line_duplicate_x_unconfirmed" for d in small["dropped"]), small


@needs_worker
def test_a_large_table_of_unrelated_shape_gives_a_structured_nothing_not_an_error(big):
    m, client, figs, _ = big
    rows = [["group", "value_mV"]] + [
        [("a", "b", "c")[i % 3], round(i * 0.01, 3)] for i in range(2000)
    ]
    put(figs, "data/cmp.csv", rows)  # 分组比较：箱 / 柱 / 汇总都要对全部行算统计量，大表上还没有
    body = propose(client).get_json()
    assert body["kind"] == "nothing", body
    assert body["dropped"] and all(
        d["reason_code"] == "mark_not_allowed" for d in body["dropped"]
    ), body


def test_counter_proof_the_log_axis_rule_reads_the_exact_whole_table_minimum(tmp_path, monkeypatch):
    rows = [["t", "v"]] + [[i, i + 1.0] for i in range(500)] + [[500, -3.0]]  # 只有最后一行非正
    p = write(tmp_path, "d.csv", "\n".join(",".join(map(str, r)) for r in rows) + "\n")
    monkeypatch.setattr(adf, "SMALL_ROWS", 100)
    prof = adf.profile_folder(tmp_path)
    (entry,) = prof.tables
    assert entry.summary.large is not None and entry.summary.large.stats["v"].lo == -3.0
    rec = recipe_mod.FigureRecipe.from_dict(
        {"table": "d.csv", "family": "trend", "why": "w", "layers": [{"mark": "points"}],
         "y": ["v"], "x": "t", "scale": "log_y"}
    )  # fmt: skip
    problems = recipe_mod.validate_recipe(prof, rec, confirmed=recipe_mod.Confirmed())
    assert [p.code for p in problems] == ["log_nonpositive"]
    assert p.exists()


# =========================================================================== 缩减（关系 / 散点）
def _rel_spec(points=50, keys=(), series_max=4):
    return {"kind": "relationship", "x": "x", "ys": ["y"], "keys": list(keys),
            "points": points, "series_max": series_max}  # fmt: skip


def _scatter_text(n, groups=None):
    import random

    rnd = random.Random(7)
    rows = ["x,y" + (",g" if groups else "")]
    for i in range(n):
        x, y = rnd.uniform(-5, 5), rnd.gauss(0, 1) * 2 + 0.3 * rnd.uniform(-5, 5)
        if i == 4321:
            y = 99.5  # 离群点
        rows.append(f"{x!r},{y!r}" + (f",{groups[i % len(groups)]}" if groups else ""))
    return "\n".join(rows) + "\n"


def test_the_scatter_reduction_keeps_the_four_extremes_and_an_even_thinning(tmp_path):
    n = 9000
    text = _scatter_text(n)
    _h, kept, info = _reduce(text, _rel_spec(points=60), tmp_path)
    pts = [tuple(map(float, ln.split(","))) for ln in text.strip().split("\n")[1:]]
    assert info["rows"] == n and info["kept"] == len(kept) <= 60 + 4
    got = [(float(r[0]), float(r[1])) for r in kept]
    assert min(g[0] for g in got) == min(p[0] for p in pts) and max(g[0] for g in got) == max(
        p[0] for p in pts
    )
    assert min(g[1] for g in got) == min(p[1] for p in pts) and max(g[1] for g in got) == 99.5
    # 独立的暴力实现：留下的等距抽样 = 第 0, K, 2K… 个有效点（K 取自信息）
    ((_, k),) = info["strides"].items()
    sample = {i for i in range(n) if i % k == 0}
    expect = {pts[i] for i in sample} | {
        min(pts),
        max(pts),
        min(pts, key=lambda p: p[1]),
        max(pts, key=lambda p: p[1]),
    }
    assert set(got) == expect
    assert info["x"] == (n, min(p[0] for p in pts), max(p[0] for p in pts))


def test_scatter_series_are_thinned_separately_and_invalid_points_are_not_counted(tmp_path):
    text = _scatter_text(3000, groups=["a", "b", "c"]) + "nan,1,a\n2,,b\n,3,c\n4,5,\n"
    _h, kept, info = _reduce(text, _rel_spec(points=40, keys=["g"]), tmp_path)
    assert info["series"] == 3 and info["x_invalid"] == 2 and len(info["examples"]) == 2
    per = {g: [r for r in kept if r[2] == g] for g in "abc"}
    assert all(0 < len(v) <= 44 for v in per.values())
    assert not any(r[2] == "" for r in kept)  # 没有分组的行不画


def test_a_large_scatter_goes_end_to_end_and_unsorted_x_has_no_line_candidate(big):
    m, client, figs, _ = big
    (figs / "data").mkdir()
    (figs / "data" / "cloud.csv").write_text(
        _scatter_text(5000).replace("x,y", "dose_mg,response_mV", 1), encoding="utf-8"
    )
    body = propose(client, answers={"y": "response_mV"}).get_json()
    assert body["kind"] == "candidates", body
    codes = {i["title_code"] for i in body["items"]}
    assert codes == {"scatter"}, codes  # 趋势（折线）因 x 没排序被拒；拟合线是汇总类，大表不画
    reasons = {d["reason_code"] for d in body["dropped"]}
    assert "line_duplicate_x_unconfirmed" in reasons and "mark_not_allowed" in reasons, reasons
    first = body["items"][0]
    assert client.get(first["thumbnail_url"]).status_code == 200
    out = commit(client, body["session"], first["id"]).get_json()
    assert out["task"]["outcome"] == "completed" and out["data_check"] == "verified", out
    assert "read_reduced(DATA," in (figs / out["script"]).read_text(encoding="utf-8")


def test_counter_proof_the_stats_reconciliation_catches_a_reduction_that_loses_rows(
    tmp_path, monkeypatch
):
    p = write(tmp_path, "d.csv", _scatter_text(400).replace("x,y", "dose_mg,response_mV", 1))
    monkeypatch.setattr(adf, "SMALL_ROWS", 100)
    prof = adf.profile_folder(tmp_path)
    (entry,) = prof.tables
    rec = recipe_mod.FigureRecipe.from_dict(
        {"table": "d.csv", "family": "relationship", "why": "w", "layers": [{"mark": "points"}],
         "y": ["response_mV"], "x": "dose_mg"}
    )  # fmt: skip
    assert tpl.expected_fingerprints(entry.summary, rec)  # 正常：对得上
    real = tpl.reduce_relationship

    def lossy(header, body, spec):  # 缩减丢了每 10 行里的一行（但仍报「读到」的行数不变）
        rows, info = real(header, (b for k, b in enumerate(body) if k % 10), spec)
        return rows, info

    monkeypatch.setattr(tpl, "reduce_relationship", lossy)
    tpl._REDUCE_CACHE.clear()
    with pytest.raises(tpl.TemplateError):
        tpl.expected_fingerprints(entry.summary, rec)
    assert p.exists()


# =========================================================================== 候选目录里的大表副本：磁盘有界、不连到用户文件
def _fake_big_session(P, sid, dir_, touched, size):
    return P.Session(
        id=sid, project="proj", folder_rel="", table="a.csv", sheet=None,
        data_sha256="0" * 64, data_size=size, confirmed=recipe_mod.Confirmed(),
        items={}, dir=dir_, touched=touched,
    )  # fmt: skip


def test_large_copies_in_the_candidate_dir_are_bounded_by_the_stage_budget(project, monkeypatch):
    from tavotto.engine import ai_data_propose as P

    root = P.CANDIDATE_ROOT
    root.mkdir(parents=True)
    now = 1000.0
    monkeypatch.setattr(P, "LARGE_STAGE_BUDGET", 3 * adf.SMALL_BYTES)
    for i in range(3):  # 三个「大表」会话 + 一个小表会话
        sid = f"{i:024x}"
        d = root / sid
        d.mkdir()
        P._SESSIONS[sid] = _fake_big_session(P, sid, d, now + i, adf.SMALL_BYTES + 1 + i)
    small = f"{9:024x}"
    (root / small).mkdir()
    P._SESSIONS[small] = _fake_big_session(P, small, root / small, now - 5, 100)
    P.make_room(10)  # 小表不计、不动
    assert len(P._SESSIONS) == 4
    P.make_room(adf.SMALL_BYTES + 1)  # 已有 3 份刚好占满预算：最老的大表会话先让位，小表会话留着
    assert f"{0:024x}" not in P._SESSIONS and small in P._SESSIONS
    assert not (root / f"{0:024x}").exists()
    assert (
        sum(P._staged_bytes(s) for s in P._SESSIONS.values()) + adf.SMALL_BYTES + 1
        <= P.LARGE_STAGE_BUDGET
    )


@needs_worker
def test_the_staged_copy_is_a_separate_file_and_a_full_disk_is_a_structured_nothing(
    big, monkeypatch
):
    import errno
    import os

    from tavotto.engine import ai_data_propose as P

    m, client, figs, _ = big
    original = put(figs, "data/run.csv", _trend_table())
    body = propose(client, render="lazy").get_json()
    assert body["kind"] == "candidates" and body["items"][0]["reduced"]["rows"] == BIG_N
    assert 0 < body["items"][0]["reduced"]["kept"] < BIG_N
    sdir = P.CANDIDATE_ROOT / body["session"]
    staged = sdir / "run.csv"
    assert staged.is_file() and not staged.is_symlink()
    assert os.stat(staged).st_ino != os.stat(original).st_ino  # 不是硬链接：写它不会写到用户的文件
    before = original.read_bytes()
    staged.write_bytes(b"x")
    assert original.read_bytes() == before

    # 磁盘满：结构化的「没有备选」，会话目录不留
    def full(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(P, "_stage_table", full)
    n_dirs = len(list(P.CANDIDATE_ROOT.iterdir()))
    out = propose(client, render="lazy", client_request_id="rid-full-0001").get_json()
    assert out["kind"] == "nothing" and out["reasons"] == ["candidate_render_failed"], out
    assert len(list(P.CANDIDATE_ROOT.iterdir())) == n_dirs


@needs_worker
def test_the_unplotted_total_is_exact_while_the_examples_are_capped(big):
    m, client, figs, _ = big
    rows = _trend_table()
    for i in range(5, 5 + 130):  # 130 行横轴是空的
        rows[i][0] = ""
    put(figs, "data/gaps.csv", rows)
    body = propose(client, render="lazy").get_json()
    item = next(i for i in body["items"] if i["title_code"] == "line_points")
    assert item["unplotted"] == {"count": 130, "code": "rows_not_plotted"}


def test_a_folder_scan_reads_large_tables_only_up_to_its_byte_budget(tmp_path, monkeypatch):
    for name in ("a.csv", "b.csv", "c.csv"):
        write(tmp_path, name, "x,y\n" + "1,2\n" * 60)
    monkeypatch.setattr(adf, "SMALL_BYTES", 100)
    monkeypatch.setattr(adf, "PROFILE_LARGE_BYTES", 2 * (4 + 60 * 4))  # 两份大表放得下
    prof = adf.profile_folder(tmp_path)
    assert [t.path for t in prof.tables] == ["a.csv", "b.csv"]
    assert [(k.path, k.code) for k in prof.skipped] == [("c.csv", "data_too_large")]
    # 用户显式选中的表不受这个预算限制（`with_explicit_table`）
    from tavotto.engine import ai_data_propose as P

    prof2 = P.with_explicit_table(prof, tmp_path, "c.csv", None)
    assert [t.path for t in prof2.tables] == ["a.csv", "b.csv", "c.csv"]
    assert prof2.tables[2].summary.large is not None


def test_a_date_axis_is_reduced_in_days_since_the_epoch(tmp_path):
    import datetime as dt

    d0 = dt.date(2020, 1, 1)
    lines = ["day,v"] + [
        f"{(d0 + dt.timedelta(days=i)).isoformat()},{(i * 7) % 11}" for i in range(2500)
    ]
    p = write(tmp_path, "d.csv", "\n".join(lines) + "\n")
    spec = {**_spec(["v"], x="day", bins=30), "date": True}
    _h, kept, info = tpl.reduce_file(str(p), ",", None, False, "utf-8", spec)
    days = [(dt.date.fromisoformat(r[0]) - d0).days for r in kept]
    assert days == sorted(days) and days[0] == 0 and days[-1] == 2499 and info["bins"] <= 30
    assert info["y"][0] == (2500, 0.0, 10.0) and {r[1] for r in kept} >= {"0", "10"}


def test_front_end_large_file_threshold_is_the_backend_small_bytes():
    """前端「读大文件时说一句并给取消」的界线（`LARGE_FILE_BYTES`）与后端大表界线 `SMALL_BYTES` 同一个数（严格同源对）。"""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "web/src/lib/dataFigure.ts").read_text(
        encoding="utf-8"
    )
    m = re.search(r"export const LARGE_FILE_BYTES = (\d+) \* 1024 \* 1024", src)
    assert m is not None
    assert int(m.group(1)) * 1024 * 1024 == adf.SMALL_BYTES


# =========================================================================== Codex P1 / P2：断开不能被连起来；x 个数与小表同一个谓词
def _gap_table(tmp_path, n=110_000, gaps=(11, 13), spikes=(12, 14)):
    lines = ["t,v"]
    for i in range(n):
        if i in gaps:
            lines.append(f"{i},")
        elif i in spikes:
            lines.append(f"{i},{-1000 if i == spikes[0] else 1000}")  # 12 是最小值、14 是最大值
        else:
            lines.append(f"{i},{(i % 7) * 0.1}")
    return write(tmp_path, "g.csv", "\n".join(lines) + "\n")


def _line_spec(bins=2000, **kw):
    return {**_spec(["v"], bins=bins, **kw), "lines": True, "gap_max": 200}


def test_every_gap_keeps_a_marker_so_kept_points_are_never_joined_across_it(tmp_path):
    # Codex 的复现形状：11 万个递增 x、默认 2000 箱；x=11 与 x=13 缺 y，x=12 与 x=14 是极值
    p = _gap_table(tmp_path)
    _h, kept, info = tpl.reduce_file(str(p), ",", None, False, "utf-8", _line_spec())
    xs = [int(r[0]) for r in kept]
    assert {11, 12, 13, 14} <= set(xs), xs[:20]  # 两段断开都留着标记
    assert info["gap_runs"] == 2 and info["gap_over"] is False
    # 画出的线：trend_data 按 x 排序后，12 与 14 之间必有一条缺失行（折线在那里断开）
    d = tpl.trend_data(_h, kept, "t", ["v"], None, None, None, False)
    (key,) = d["series"]
    sx, sv = d["series"][key]
    i12, i14 = sx.index(12.0), sx.index(14.0)
    assert any(v != v for v in sv[i12:i14])


def test_the_gap_audit_is_independent_of_the_reduction_and_catches_a_lost_gap(
    tmp_path, monkeypatch
):
    _gap_table(tmp_path)
    monkeypatch.setattr(adf, "SMALL_ROWS", 100)
    prof = adf.profile_folder(tmp_path)
    (entry,) = prof.tables
    rec = recipe_mod.FigureRecipe.from_dict(
        {"table": "g.csv", "family": "trend", "why": "w",
         "layers": [{"mark": "points"}, {"mark": "line", "role": "overlay"}], "y": ["v"], "x": "t"}
    )  # fmt: skip
    rows, info = tpl.large_reduce(entry.summary, rec)  # 正常：对得上
    assert info["gap_runs"] == 2
    real = tpl.reduce_file

    def lossy(*a, **k):  # 缩减把 x=13 那段断开弄丢（行与行序号都去掉）
        h, rs, inf = real(*a, **k)
        keep = [i for i, r in enumerate(rs) if r[0] != "13"]
        return h, [rs[i] for i in keep], {**inf, "ords": [inf["ords"][i] for i in keep]}

    monkeypatch.setattr(tpl, "reduce_file", lossy)
    tpl._REDUCE_CACHE.clear()
    with pytest.raises(tpl.TemplateError):
        tpl.large_reduce(entry.summary, rec)


def test_too_many_gaps_refuse_the_line_but_not_the_scatter(tmp_path):
    lines = ["t,v"] + [f"{i}," + ("" if i % 2 else "1") for i in range(3000)]
    p = write(tmp_path, "m.csv", "\n".join(lines) + "\n")
    spec = {**_line_spec(), "gap_max": 10}
    _h, kept, info = tpl.reduce_file(str(p), ",", None, False, "utf-8", spec)
    assert info["gap_over"] is True and info["gap_runs"] > 10
    assert len([r for r in kept if r[1] == ""]) <= 10 + 2000  # 断开标记有界


def test_distinct_x_counts_only_rows_where_x_and_the_first_y_are_finite_like_the_small_table(
    tmp_path, monkeypatch
):
    # Codex P2：11 万行、y 只有两个端点非空 → 小表同样的数据拒（不同 x 不足 3 个），大表也必须拒
    n = 110_000
    text = "t,v\n" + "".join(f"{i},{1.0 if i in (0, n - 1) else ''}\n" for i in range(n))
    small_text = "t,v\n" + "".join(f"{i},{1.0 if i in (0, 40) else ''}\n" for i in range(41))
    rec = recipe_mod.FigureRecipe.from_dict(
        {"table": "d.csv", "family": "trend", "why": "w", "layers": [{"mark": "points"}],
         "y": ["v"], "x": "t"}
    )  # fmt: skip
    p = write(tmp_path, "d.csv", text)
    monkeypatch.setattr(adf, "SMALL_ROWS", 1000)
    (entry,) = adf.profile_folder(tmp_path).tables
    with pytest.raises(tpl.LargeRuleError) as e:
        tpl.large_reduce(entry.summary, rec)
    assert e.value.code == "trend_too_few_x"
    # 小表（同一个谓词）：同样的形状拒
    p.write_text(small_text, encoding="utf-8")
    monkeypatch.setattr(adf, "SMALL_ROWS", 100_000)
    prof = adf.profile_folder(tmp_path)
    values = adf.inspect_data_source(p, v2=True, header_row=prof.tables[0].summary.header_line)
    probs = recipe_mod.validate_recipe(prof, rec, confirmed=recipe_mod.Confirmed(), values=values)
    assert "trend_too_few_x" in [q.code for q in probs]
    # 同一个谓词：不是两份拷贝
    assert recipe_mod.trend_too_few_x([4, 2]) and not recipe_mod.trend_too_few_x([3, 4])
    assert recipe_mod.trend_too_few_x([])


def test_duplicate_x_is_judged_on_finite_pairs_like_the_small_table(tmp_path, monkeypatch):
    # 同一个 x 出现两次、但其中一行 y 缺失：没有两个有限观测共用一个 x，小表不拒；大表也不该拒
    n = 5000
    lines = ["t,v"]
    for i in range(n):
        lines.append(f"{i},{i % 5}")
        if i == 100:
            lines.append(f"{i},")
    p = write(tmp_path, "d.csv", "\n".join(lines) + "\n")
    _h, _k, info = tpl.reduce_file(str(p), ",", None, False, "utf-8", _line_spec())
    assert info["mono"] is True and info["dup"] is False
    lines.append("200,9")  # 现在才是真的两个有限观测同 x（且不再单调）
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _h, _k, info = tpl.reduce_file(str(p), ",", None, False, "utf-8", _line_spec())
    assert info["mono"] is False
