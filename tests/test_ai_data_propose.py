"""TAF 数据出图 v2 的出备选 → 选中落盘流水（ADR 0178）：真 app + 真 safe worker。

判据的主语：

* 「用户文件夹没被动」量的是**文件系统事实**——出备选前后整棵树（含目录）逐字节与 mtime 相同，不是看某个函数有没有被调用；
* 「画的就是这份数据」量的是真 worker 里跑出的 manifest 指纹（候选阶段与提交后各一次），不是父进程自己的中间量；
* 「没有可核层就不是 verified」量的是任务结局里 `data.verifiable` 这条检查的 verdict 与响应里的 `data_check`；
* 选择题 / 备选的取舍是对 `default_candidates` 的**输出**断言（不许出现的画法一个都不许出现），不是对内部变量。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_task,
    ai_figure_edit,
    ai_history,
    ai_tasks,
    config,
    discover,
    figureverify,
    pool,
    project_watch,
)
from tests.test_ai_data_profile import make_xlsx
from tests.test_ai_data_template import _csv, cmp_rows, dist_rows, rel_rows, trend_rows

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)

PNG = b"\x89PNG\r\n\x1a\n"


def simple_rows():
    """一个分组列 + 一个数值列（结构唯一，不需要任何选择题）：3 组 × 6 个值。"""
    rows = [["group", "value_mV"]]
    for gi, g in enumerate(("ctrl", "drugA", "drugB")):
        for k in range(6):
            rows.append([g, round(2 + gi * 1.5 + k * 0.3 + (k % 3) * 0.1, 3)])
    return rows


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(P, "CANDIDATE_ROOT", tmp_path / "propose")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    state = {"taf": True}
    monkeypatch.setattr(
        config, "ai_settings", lambda: {"taf": {"enabled": True}} if state["taf"] else {}
    )
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"version": 1, "scripts": {}}), encoding="utf-8"
    )
    m.open_project(str(figs))
    try:
        yield m, m.app.test_client(), figs, state
    finally:
        P.reset_for_tests()
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()
        ai_tasks.unregister(ai_data_task.V2_TASK_TYPE)


def put(figs: Path, rel: str, rows, delim=",") -> Path:
    path = figs / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_csv(rows, delim), encoding="utf-8")
    return path


def tree_state(base: Path) -> list[tuple]:
    """整棵树（含目录）的 `(相对路径, 是不是目录, 字节数, mtime_ns, 内容)`：用来证明「一个字节都没动、没建目录、没改 mtime」。"""
    out = []
    for p in sorted(base.rglob("*")):
        st = p.lstat()
        out.append(
            (
                p.relative_to(base).as_posix(),
                p.is_dir(),
                st.st_size,
                st.st_mtime_ns,
                p.read_bytes() if p.is_file() and not p.is_symlink() else None,
            )
        )
    return out


def propose(client, folder="data", **body):
    return client.post("/api/ai/data-figure/propose", json={"folder": folder, **body})


def commit(client, session, item, rid=None):
    body = {"session": session, "id": item}
    if rid:
        body["client_request_id"] = rid
    return client.post("/api/ai/data-figure/commit", json=body)


def checks_of(task_id):
    v = ai_history.get(task_id)["verification"]
    return {
        c["id"]: (c["verdict"], c["reason_code"])
        for kind in ("figure", "persistence")
        for c in v[kind]["checks"]
    }


def codes(body):
    return [i["title_code"] for i in body["items"]]


# =========================================================================== 路径纯函数（不碰磁盘；POSIX 上钉 Windows 口径）
@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (None, ""),
        ("", ""),
        (".", ""),
        ("data", "data"),
        ("./data//sub/", "data/sub"),
        ("data\\sub\\x", "data/sub/x"),  # Windows 分隔符
        ("数据 文件夹/子 目录", "数据 文件夹/子 目录"),  # 非 ASCII 与空格原样
    ],
)
def test_normalize_rel_accepts_and_canonicalizes(raw, want):
    assert P.normalize_rel(raw) == want


@pytest.mark.parametrize(
    "raw",
    [
        "/etc",
        "\\\\server\\share",
        "C:\\Users\\x",
        "c:/users",
        "C:rel",
        "../x",
        "a/../../x",
        "a\\..\\x",
        "a\x00b",
    ],
)
def test_normalize_rel_refuses_escapes(raw):
    with pytest.raises(ValueError, match="path_outside"):
        P.normalize_rel(raw)


@pytest.mark.parametrize(
    ("table", "want"),
    [
        ("a.csv", "../a.csv"),
        ("sub/a.csv", "../sub/a.csv"),
        ("sub\\deep\\a.csv", "../sub/deep/a.csv"),
        ("数据 1/结果 a.csv", "../数据 1/结果 a.csv"),
    ],
)
def test_script_data_rel_is_posix_and_relative_to_the_script(table, want):
    assert P.script_data_rel(table) == want


def test_script_data_rel_refuses_an_empty_or_escaping_path():
    for bad in ("", "../a.csv", "C:\\a.csv"):
        with pytest.raises(ValueError):
            P.script_data_rel(bad)


def test_path_key_windows_semantics_on_any_platform():
    assert P.path_key("C:\\Users\\Me\\Data", windows=True) == P.path_key(
        "c:/users/me/data/", windows=True
    )
    assert P.path_key("C:\\a\\..\\b", windows=True) == "c:/b"
    # 非 Windows：大小写敏感
    assert P.path_key("/a/B", windows=False) != P.path_key("/a/b", windows=False)


# =========================================================================== default_candidates 的选择题与保守默认
def _profile(tmp_path, files: dict[str, list]):
    d = tmp_path / "prof"
    d.mkdir(exist_ok=True)
    for name, rows in files.items():
        (d / name).write_text(_csv(rows), encoding="utf-8")
    return d, adf.profile_folder(d)


def _cands(tmp_path, rows, *, confirmed=None, picks=None, hint=None, name="t.csv"):
    d, prof = _profile(tmp_path, {name: rows})
    values = adf.inspect_data_source(d / name)
    return R.default_candidates(
        prof,
        table=name,
        confirmed=confirmed or R.Confirmed(),
        hint=hint,
        values=values,
        picks=picks,
    )


def test_a_unique_structure_needs_no_question(tmp_path):
    res = _cands(tmp_path, simple_rows())
    assert isinstance(res, R.Candidates)
    assert [c.code for c in res.items] == ["points_mean", "box_points", "ecdf_groups", "bar_error"]
    # 原始点排在柱前（`enforce_order` 之外，组顺序本身也是）；每个备选都带稳定码，why 就是码
    assert all(c.recipe.why == c.code for c in res.items)
    assert 2 <= len(res.items) <= 4


def test_multiple_tables_ask_which_one_with_a_human_sentence_per_option(tmp_path):
    d, prof = _profile(tmp_path, {"response.csv": simple_rows(), "other.csv": dist_rows()})
    res = R.default_candidates(prof, confirmed=R.Confirmed())
    assert isinstance(res, R.NeedsChoice) and res.question_code == "pick_table"
    labels = {o.params["file"]: o.params for o in res.options}
    assert labels["response.csv"] == {"file": "response.csv", "rows": 18, "groups": 1}
    assert [o.answer for o in res.options] == [{"table": "other.csv"}, {"table": "response.csv"}]
    # 回答之后（按文件名指定）就能继续；hint 按词点名文件也算指定
    again = R.default_candidates(prof, table="response.csv", confirmed=R.Confirmed())
    assert isinstance(again, R.Candidates) and again.items
    named = R.default_candidates(prof, confirmed=R.Confirmed(), hint="用 response.csv 画")
    assert isinstance(named, R.Candidates) and named.table == "response.csv"


def test_several_value_columns_ask_and_unsure_has_a_safe_default(tmp_path):
    rows = [["time_s", "arm", "signal", "other"]]
    for a in ("a", "b"):
        for t in range(1, 7):
            rows.append([t, a, t * 1.5, t * 0.5])
    res = _cands(tmp_path, rows)
    assert isinstance(res, R.NeedsChoice) and res.question_code == "pick_value"
    assert [o.answer for o in res.options] == [{"y": "time_s"}, {"y": "signal"}, {"y": "other"}]
    assert res.unsure is not None and res.unsure.answer == {"y": "unsure"}
    safe = _cands(tmp_path, rows, picks={"y": "unsure"})
    assert isinstance(safe, R.Candidates) and safe.items
    assert all(c.recipe.y == ("other",) for c in safe.items), "unsure = 最后一个数值列"
    named = _cands(tmp_path, rows, hint="画 signal 随 time_s 的变化")
    assert isinstance(named, R.Candidates)
    assert all(c.recipe.y == ("signal",) for c in named.items), "hint 按词点名列"


def test_id_like_columns_are_never_roles_and_unconfirmed_never_make_pairs_or_trajectories(tmp_path):
    rows = [["subject_id", "time_s", "response"]]
    for s in range(1, 5):
        for t in range(1, 6):
            rows.append([s, t, round(t * s * 0.7 + (t % 2), 3)])
    d, prof = _profile(tmp_path, {"t.csv": rows})
    s = prof.tables[0].summary
    assert s.id_like == ["subject_id"] and s.repeats is True
    # 编号列 + 编号重复、用户没表态、能画趋势：先问「是不是同一对象重复测量」
    res = R.default_candidates(
        prof, table="t.csv", confirmed=R.Confirmed(), picks={"y": "response"}
    )
    assert isinstance(res, R.NeedsChoice) and res.question_code == "pick_design"
    assert [o.answer for o in res.options] == [{"design": "independent"}, {"design": "repeated"}]
    # 「不确定」= 当独立数据画：不带编号、不出配对 / 逐个体轨迹
    safe = R.default_candidates(
        prof,
        table="t.csv",
        confirmed=R.Confirmed(),
        picks={"y": "response", "design": "unsure"},
        values=adf.inspect_data_source(tmp_path / "prof" / "t.csv"),
    )
    assert isinstance(safe, R.Candidates) and safe.items
    for c in safe.items:
        assert c.recipe.unit is None and not c.recipe.paired
        assert c.recipe.family not in ("paired", "trajectories")
        assert "subject_id" not in (c.recipe.x, c.recipe.group, c.recipe.facet, *c.recipe.y)
    # 确认「重复测量」后唯一的编号列自动落成 unit，才出逐个体的线
    conf = R.default_candidates(
        prof,
        table="t.csv",
        confirmed=R.Confirmed("repeated"),
        picks={"y": "response"},
        values=adf.inspect_data_source(tmp_path / "prof" / "t.csv"),
    )
    assert isinstance(conf, R.Candidates) and conf.confirmed == R.Confirmed(
        "repeated", "subject_id"
    )
    assert conf.items[0].code == "line_units" and conf.items[0].recipe.unit == "subject_id"


def test_nothing_cases_are_structured_not_errors(tmp_path):
    allt = [["name", "note"]] + [[f"n{i}", "abc"] for i in range(5)]
    # 指名了这张表但里面没有数值列
    res = _cands(tmp_path, allt, name="text.csv")
    assert isinstance(res, R.Candidates) and not res.items and res.reasons == ("no_numeric_column",)
    mixed = [["g", "v"]] + [["a", "<0.1"], ["a", "2"], ["b", "3"], ["b", "n/a"]]
    res = _cands(tmp_path, mixed, name="mixed.csv")
    assert not res.items and res.reasons == ("value_column_mixed",), (
        "有文字格的数值列不当数值列，不猜"
    )
    # 没指名：整个文件夹没有可用的表
    d, prof = (
        _profile(tmp_path / "x", {"text.csv": allt})
        if (tmp_path / "x").mkdir() is None
        else (None, None)
    )
    res = R.default_candidates(prof, confirmed=R.Confirmed())
    assert isinstance(res, R.Candidates) and res.reasons == ("no_usable_table",)
    d2, prof2 = (
        _profile(tmp_path / "y", {"mixed.csv": mixed})
        if (tmp_path / "y").mkdir() is None
        else (None, None)
    )
    assert R.default_candidates(prof2, confirmed=R.Confirmed()).reasons == ("value_column_mixed",)
    empty = adf.FolderProfile((), (), False, None)
    assert R.default_candidates(empty, confirmed=R.Confirmed()).reasons == ("no_usable_table",)
    res = R.default_candidates(empty, table="nope.csv", confirmed=R.Confirmed())
    assert res.reasons == ("table_not_found",)


def test_every_code_has_a_stable_name():
    # 备选码、问题码、原因码都是闭集：前端文案与测试对着这三张表
    assert len(set(R.CANDIDATE_CODES)) == len(R.CANDIDATE_CODES)
    assert set(R.QUESTION_CODES) >= {
        "pick_table",
        "pick_value",
        "pick_group",
        "pick_x",
        "pick_design",
        "pick_unit",
    }
    assert set(P.DROP_REASONS) >= {"candidate_render_failed", "candidate_timeout"}


# =========================================================================== 端到端：CSV / TSV / XLSX
def _make(figs: Path, kind: str) -> str:
    rows = simple_rows()
    if kind == "csv":
        put(figs, "data/g.csv", rows)
    elif kind == "tsv":
        put(figs, "data/g.tsv", rows, "\t")
    else:
        (figs / "data").mkdir(parents=True, exist_ok=True)
        make_xlsx(figs / "data" / "g.xlsx", {"Data": rows})
    return {"csv": "g.csv", "tsv": "g.tsv", "xlsx": "g.xlsx"}[kind]


@needs_worker
@pytest.mark.parametrize("kind", ["csv", "tsv", "xlsx"])
def test_propose_thumbnail_commit_end_to_end(project, kind):
    m, client, figs, _ = project
    table = _make(figs, kind)
    before = tree_state(figs)
    r = propose(client)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["kind"] == "candidates", body
    assert 2 <= len(body["items"]) <= 4 and body["items"][0]["recommended"] is True
    assert [i["recommended"] for i in body["items"][1:]] == [False] * (len(body["items"]) - 1)
    first = body["items"][0]
    assert first["title_code"] == "points_mean" and first["why_code"] == "points_mean"
    assert first["values"]["x"] == "group" and first["values"]["y"] == ["value_mV"]
    assert first["coverage"]["any_checked"] is True
    # 缩略图是真 PNG，且 propose 没有碰用户文件夹（整棵树逐字节、mtime 都不变）
    t = client.get(first["thumbnail_url"])
    assert t.status_code == 200 and t.data.startswith(PNG) and t.mimetype == "image/png"
    assert tree_state(figs) == before

    c = commit(client, body["session"], first["id"])
    assert c.status_code == 200, c.get_json()
    out = c.get_json()
    assert out["task"]["outcome"] == "completed", out
    assert out["data_check"] == "verified" and out["can_delete_script"] is False
    stem = adf.stem_for(table)
    assert out["script"] == f"data/tavotto_figures/{stem}.py" and out["stem"] == stem
    figures = figs / "data" / "tavotto_figures"
    module = Path(out["module"])
    assert (figs / out["module"]).is_file() and module.name.startswith("tavotto_plot_")
    assert sorted(p.name for p in figures.iterdir()) == sorted([f"{stem}.py", module.name])
    assert discover.registered_stems(figs, out["script"]) == [stem]
    checks = checks_of(out["task"]["task_id"])
    assert checks["render.figure"] == ("verified", None)
    assert checks["data.verifiable"] == ("verified", None)
    assert checks["commit.common_module"] == ("verified", None)
    assert any(
        k.startswith("data.") and k != "data.verifiable" and v[0] == "verified"
        for k, v in checks.items()
    )
    # 数据文件原样不动；相对路径指回数据
    script = (figures / f"{stem}.py").read_text(encoding="utf-8")
    assert f"'../{table}'" in script or f'"../{table}"' in script
    after = {p: s for p, *s in tree_state(figs)}
    for rel, is_dir, *rest in before:
        if (
            not is_dir and rel != "tavotto_registry.json"
        ):  # 提交会登记（注册表）并在目录里新建文件，已有的数据文件不能变
            assert after[rel] == [is_dir, *rest], f"{rel} 被改动"
    # 提交成功后候选会话即删
    assert client.get(first["thumbnail_url"]).status_code == 404
    assert not any(P.CANDIDATE_ROOT.iterdir())


@needs_worker
def test_a_second_figure_reuses_the_same_common_module(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    figures = figs / "data" / "tavotto_figures"
    outs = []
    for _ in range(2):
        body = propose(client).get_json()
        outs.append(commit(client, body["session"], body["items"][0]["id"]).get_json())
    assert [o["task"]["outcome"] for o in outs] == ["completed", "completed"]
    assert outs[0]["module"] == outs[1]["module"]
    assert outs[1]["script"] == "data/tavotto_figures/g_figure_2.py", "同名走 _2，不覆盖"
    mods = [p.name for p in figures.glob("tavotto_plot_*.py")]
    assert mods == [Path(outs[0]["module"]).name], "公共模块只有一份"
    assert checks_of(outs[0]["task"]["task_id"])["commit.common_module"] == ("verified", None)
    created = [
        next(
            c["observed"]["created"]
            for k in ("figure", "persistence")
            for c in ai_history.get(o["task"]["task_id"])["verification"][k]["checks"]
            if c["id"] == "commit.common_module"
        )
        for o in outs
    ]
    assert created == [True, False], "第二次复用、没有再建"


@needs_worker
def test_a_damaged_common_module_is_refused_and_nothing_is_written(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    first = commit(client, body["session"], body["items"][0]["id"]).get_json()
    module = figs / first["module"]
    module.write_text(module.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    snapshot = tree_state(figs)
    body = propose(client).get_json()
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "failed" and out["reason_code"] == "common_module_conflict"
    assert "script" not in out
    assert tree_state(figs) == snapshot, "零写入：脚本、模块、注册表都没动"
    assert module.read_text(encoding="utf-8").endswith("# tampered\n")


@needs_worker
def test_commit_never_overwrites_an_existing_script(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    mine = figs / "data" / "tavotto_figures" / "g_figure.py"
    mine.parent.mkdir(parents=True)
    mine.write_text("# mine\n", encoding="utf-8")
    body = propose(client).get_json()
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["script"] == "data/tavotto_figures/g_figure_2.py"
    assert mine.read_text(encoding="utf-8") == "# mine\n"


@needs_worker
def test_data_changed_between_propose_and_commit_is_refused(project):
    m, client, figs, _ = project
    path = put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    path.write_text(path.read_text(encoding="utf-8") + "ctrl,99\n", encoding="utf-8")
    snapshot = tree_state(figs)
    r = commit(client, body["session"], body["items"][0]["id"])
    assert r.status_code == 409 and r.get_json()["code"] == "propose_data_changed"
    assert tree_state(figs) == snapshot
    # 前端据此重新出备选：新会话画的是新数据
    again = propose(client).get_json()
    assert again["kind"] == "candidates" and again["session"] != body["session"]


@needs_worker
def test_a_failed_registration_removes_the_new_script_module_and_directory(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(discover, "register", boom)
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "failed" and out["task"]["error"] == "register_failed"
    assert not (figs / "data" / "tavotto_figures").exists(), "本次新建的脚本、公共模块与目录都撤回"


@needs_worker
def test_a_failed_registration_keeps_a_reused_module(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    first = commit(client, body["session"], body["items"][0]["id"]).get_json()
    body = propose(client).get_json()
    monkeypatch.setattr(discover, "register", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["error"] == "register_failed"
    assert (figs / first["module"]).is_file(), "复用的公共模块不删"
    assert sorted(p.name for p in (figs / "data" / "tavotto_figures").iterdir()) == sorted(
        [Path(first["script"]).name, Path(first["module"]).name]
    )


@needs_worker
def test_no_verifiable_layer_is_reported_as_unverifiable_never_verified(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    sess = P.get_session(str(figs.resolve()), body["session"])
    iid = body["items"][0]["id"]
    # 换成只有箱线的配方（箱线 / 误差线不核：manifest 里没有 label 可对）
    sess.items[iid].recipe = R.FigureRecipe.from_dict(
        {
            "table": "g.csv",
            "family": "comparison",
            "layers": [{"mark": "box", "summary": "none", "role": "main"}],
            "x": "group",
            "y": ["value_mV"],
            "why": "box_only",
        }
    )
    out = commit(client, body["session"], iid).get_json()
    assert out["data_check"] == "unverifiable"
    task = out["task"]
    assert task["outcome"] == "partial", task
    assert checks_of(task["task_id"])["data.verifiable"] == ("unknown", "data_not_verifiable")


@needs_worker
def test_a_fingerprint_mismatch_is_reported_with_the_script_deletable(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    real = figureverify.data_matches_expected

    def lying(man, expected, *, stage="postcommit"):
        out = real(man, expected, stage=stage)
        if stage == "postcommit":
            out = [
                type(c)(**{**c.__dict__, "verdict": "failed", "reason_code": "data_mismatch"})
                for c in out
            ]
        return out

    monkeypatch.setattr(figureverify, "data_matches_expected", lying)
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "failed"
    assert out["data_check"] == "mismatch" and out["can_delete_script"] is True
    assert (figs / out["script"]).is_file(), "脚本已落盘，由用户决定删不删"


# =========================================================================== propose 不写用户文件夹；候选渲染的隔离
@needs_worker
def test_propose_leaves_the_user_folder_byte_and_mtime_identical(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    put(figs, "data/sub/more.csv", dist_rows())
    before = tree_state(figs)
    r = propose(client, table="g.csv").get_json()
    assert r["kind"] == "candidates"
    time.sleep(0.05)
    assert tree_state(figs) == before


@needs_worker
def test_a_candidate_script_cannot_write_outside_the_candidate_dir(tmp_path):
    """写入根绊线只开候选目录：候选脚本往用户文件夹写文件，在写发生的那一刻失败，文件根本没出现。"""
    sdir = tmp_path / "sess"
    (sdir / "tavotto_figures").mkdir(parents=True)
    # 基础设施目录（系统临时目录等）本来就放行，所以受害文件放在仓库树里（不在任何放行根下）；无论如何都在 finally 里清掉
    victim = Path(__file__).resolve().parent / f".leak-probe-{os.getpid()}.txt"
    (sdir / "tavotto_figures" / "cand_1.py").write_text(
        "import matplotlib.pyplot as plt\n"
        "def main():\n"
        f"    open({str(victim)!r}, 'w').write('leak')\n"
        "    fig, ax = plt.subplots(); ax.plot([1, 2], [1, 2]); fig.savefig('cand_1.pdf')\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    try:
        out = P._render_one(sdir, "tavotto_figures/cand_1.py", "cand_1", sdir / "thumbs" / "1.png")
    finally:
        pool.shutdown_all(figures_dir=str(sdir), wait=True)
        leaked = victim.exists()
        victim.unlink(missing_ok=True)
    assert out.png is None and out.reason in ("candidate_write_blocked", "candidate_render_failed")
    assert not leaked


@needs_worker
def test_a_failed_candidate_is_one_card_fewer(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    real = P.probe.probe

    def flaky(figures_dir, rel, **kw):
        if rel.endswith("cand_2.py"):
            return {"error": {"code": "script_error", "message": "boom"}, "stems": []}
        return real(figures_dir, rel, **kw)

    monkeypatch.setattr(P.probe, "probe", flaky)
    body = propose(client).get_json()
    assert body["kind"] == "candidates"
    assert codes(body) == ["points_mean", "ecdf_groups", "bar_error"]
    assert {"reason_code": "candidate_render_failed"} in body["dropped"]


def test_every_candidate_failing_is_a_structured_nothing_with_reasons(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())

    class Boom(Exception):
        code = "worker_timeout"

    def dead(*a, **k):
        raise Boom("timeout")

    monkeypatch.setattr(P.probe, "probe", dead)
    r = propose(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body["kind"] == "nothing" and body["reasons"] == ["candidate_timeout"]
    assert len(body["dropped"]) == 4
    assert not any(P.CANDIDATE_ROOT.iterdir()), "全失败也不留候选目录"
    assert not P._SESSIONS


def test_a_fingerprint_mismatch_drops_that_candidate(project, monkeypatch):
    """候选脚本画出来的数据与期望指纹对不上：这个备选被剔除并记原因，不展示。"""
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    monkeypatch.setattr(
        P, "_render_one", lambda *a, **k: P._Rendered(png=Path(P.CANDIDATE_ROOT), manifest={})
    )
    monkeypatch.setattr(
        P.figureverify,
        "data_matches_expected",
        lambda man, expected, stage="precommit": [
            ai_tasks.CheckResult(
                id="data.x",
                layer="L2",
                stage="precommit",
                role="preserve",
                verdict="failed",
                reason_code="data_mismatch",
            )
        ],
    )
    body = propose(client).get_json()
    assert body["kind"] == "nothing" and body["reasons"] == ["candidate_fingerprint_mismatch"]


# =========================================================================== 选择题端点路径
def test_propose_asks_for_the_table_then_continues_with_the_answer(project):
    m, client, figs, _ = project
    put(figs, "data/a.csv", simple_rows())
    put(figs, "data/b.csv", dist_rows())
    ask = propose(client).get_json()
    assert ask["kind"] == "ask" and ask["question_code"] == "pick_table"
    assert {o["params"]["file"] for o in ask["options"]} == {"a.csv", "b.csv"}
    assert ask["unsure"] is None


def test_propose_xlsx_sheet_choice(project):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    make_xlsx(figs / "data" / "book.xlsx", {"One": simple_rows(), "Two": simple_rows()})
    ask = propose(client).get_json()
    assert ask["kind"] == "ask" and ask["question_code"] == "pick_table"
    assert sorted(o["answer"]["sheet"] for o in ask["options"]) == ["One", "Two"]
    assert all(o["code"] == "table_sheet" for o in ask["options"])


@needs_worker
def test_answers_continue_the_flow_and_unsure_takes_the_conservative_default(project):
    m, client, figs, _ = project
    rows = [["time_s", "arm", "subject_id", "signal"]]
    for a in ("a", "b"):
        for u in range(1, 4):
            for t in range(1, 6):
                rows.append([t, a, f"u{u}", round(t * (1 + (a == "b") * 0.5) + u * 0.2, 3)])
    put(figs, "data/t.csv", rows)
    first = propose(client).get_json()
    assert first["kind"] == "ask" and first["question_code"] == "pick_value"
    ask = propose(client, answers={"y": "signal"}).get_json()
    assert ask["kind"] == "ask" and ask["question_code"] == "pick_design"
    safe = propose(client, answers={"y": "signal", "design": "unsure"}).get_json()
    assert safe["kind"] == "candidates"
    assert all(i["values"]["unit"] is None for i in safe["items"])
    assert not any(i["values"]["family"] in ("paired", "trajectories") for i in safe["items"])
    repeated = propose(client, answers={"y": "signal", "design": "repeated"}).get_json()
    assert repeated["kind"] == "candidates"
    assert repeated["items"][0]["title_code"] == "line_units"
    assert repeated["items"][0]["values"]["unit"] == "subject_id"


# =========================================================================== 失败路径：都是结构化结果，不是 500
def test_empty_and_text_only_tables_give_nothing(project):
    m, client, figs, _ = project
    put(figs, "data/empty.csv", [["a", "b"]])
    put(figs, "data/words.csv", [["name", "note"]] + [[f"n{i}", "abc"] for i in range(4)])
    r = propose(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body["kind"] == "nothing" and body["reasons"] == ["no_usable_table"]


def test_a_value_column_with_text_cells_says_so(project):
    m, client, figs, _ = project
    put(figs, "data/lod.csv", [["g", "v"]] + [["a", "<0.1"], ["a", "2"], ["b", "3"], ["b", "n/a"]])
    body = propose(client).get_json()
    assert body["kind"] == "nothing" and body["reasons"] == ["value_column_mixed"]


def test_an_unreadable_xlsx_is_skipped_not_a_500(project):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    (figs / "data" / "broken.xlsx").write_bytes(b"not a zip at all")
    r = propose(client)
    assert r.status_code == 200 and r.get_json()["kind"] == "nothing"


def test_data_folder_errors_are_400_with_stable_codes(project):
    m, client, figs, _ = project
    (figs.parent / "outside").mkdir()
    for folder, code in (("../outside", "data_path_outside_project"), ("nope", "data_not_found")):
        r = propose(client, folder=folder)
        assert r.status_code == 400 and r.get_json()["code"] == code
    r = propose(client, folder="C:\\Windows")
    assert r.status_code == 400 and r.get_json()["code"] == "data_path_outside_project"


def test_a_symlinked_folder_pointing_outside_the_project_is_refused(project):
    m, client, figs, _ = project
    outside = figs.parent / "elsewhere"
    outside.mkdir()
    (outside / "x.csv").write_text(_csv(simple_rows()), encoding="utf-8")
    try:
        (figs / "link").symlink_to(outside)
    except OSError:
        pytest.skip("not_run：此平台不能建符号链接")
    r = propose(client, folder="link")
    assert r.status_code == 400 and r.get_json()["code"] == "data_path_outside_project"


def test_symlinked_data_files_are_not_followed(project):
    m, client, figs, _ = project
    (figs / "data").mkdir()
    outside = figs.parent / "secret.csv"
    outside.write_text(_csv(simple_rows()), encoding="utf-8")
    try:
        (figs / "data" / "linked.csv").symlink_to(outside)
    except OSError:
        pytest.skip("not_run：此平台不能建符号链接")
    body = propose(client).get_json()
    assert body["kind"] == "nothing" and body["reasons"] == ["no_usable_table"]


@pytest.mark.parametrize("path", ["数据 文件夹/子 目录", "plain/deep/sub"])
@needs_worker
def test_non_ascii_spaces_and_subdirectories_round_trip(project, path):
    m, client, figs, _ = project
    put(figs, f"{path}/结果 g.csv", simple_rows())
    put(figs, f"{path}/sub dir/inner.csv", simple_rows())
    body = propose(client, folder=path, table="结果 g.csv").get_json()
    assert body["kind"] == "candidates", body
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed", out
    assert out["script"].startswith(f"{path}/tavotto_figures/")
    # 数据在子目录时脚本里的相对路径也对
    body = propose(client, folder=path, table="sub dir/inner.csv").get_json()
    out = commit(client, body["session"], body["items"][0]["id"]).get_json()
    assert out["task"]["outcome"] == "completed", out
    text = (figs / out["script"]).read_text(encoding="utf-8")
    assert "../sub dir/inner.csv" in text


# =========================================================================== 会话生命周期、幂等、开关
def _fake_session(project_key, sid, dir_, touched):
    return P.Session(
        id=sid,
        project=project_key,
        folder_rel="",
        table="a.csv",
        sheet=None,
        data_sha256="0" * 64,
        data_size=0,
        confirmed=R.Confirmed(),
        items={},
        dir=dir_,
        touched=touched,
    )


def test_sessions_expire_are_capped_and_scoped_to_the_project(project):
    m, client, figs, _ = project
    root = P.CANDIDATE_ROOT
    root.mkdir(parents=True)
    now = time.time()
    for i in range(P.MAX_SESSIONS + 2):
        sid = f"{i:024x}"
        d = root / sid
        d.mkdir()
        (d / "f").write_text("x", encoding="utf-8")
        P._SESSIONS[sid] = _fake_session("proj", sid, d, now - (P.MAX_SESSIONS + 2 - i))
    P.sweep(now)
    assert len(P._SESSIONS) == P.MAX_SESSIONS
    assert len(list(root.iterdir())) == P.MAX_SESSIONS, "超出上限的最老会话连目录一起删"
    oldest = f"{2:024x}"
    # 别的项目拿同一个 id 也是过期
    with pytest.raises(P.ProposeError) as e:
        P.get_session("other", oldest)
    assert e.value.code == "propose_session_expired"
    # TTL：超过就过期并被清掉
    P._SESSIONS[oldest].touched = now - P.SESSION_TTL_S - 1
    with pytest.raises(P.ProposeError):
        P.get_session("proj", oldest)
    P.sweep(now)
    assert oldest not in P._SESSIONS and not (root / oldest).exists()
    # 不是 24 位十六进制的 id 一律当过期（不拼路径）
    for bad in ("../../etc", "x" * 24, "", "ab"):
        with pytest.raises(P.ProposeError):
            P.get_session("proj", bad)


def test_orphan_directories_from_a_previous_process_are_swept(project):
    m, client, figs, _ = project
    root = P.CANDIDATE_ROOT
    root.mkdir(parents=True)
    old, young = root / "old", root / "young"
    for d in (old, young):
        d.mkdir()
        (d / "f").write_text("x", encoding="utf-8")
    t = time.time() - P.SESSION_TTL_S - 60
    os.utime(old, (t, t))
    P.sweep()
    assert not old.exists() and young.exists()


def test_rmtree_failures_are_logged_not_ignored(project, monkeypatch, caplog):
    m, client, figs, _ = project
    root = P.CANDIDATE_ROOT
    root.mkdir(parents=True)
    d = root / ("a" * 24)
    d.mkdir()

    def deny(path):
        raise PermissionError("locked")

    monkeypatch.setattr(P.shutil, "rmtree", deny)
    with caplog.at_level("WARNING"):
        assert P._rmtree(d) is False
    assert "没删掉" in caplog.text and d.exists()


@needs_worker
def test_session_expiry_gives_a_structured_404(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    sess = P.get_session(str(figs.resolve()), body["session"])
    sess.touched = time.time() - P.SESSION_TTL_S - 1
    r = commit(client, body["session"], body["items"][0]["id"])
    assert r.status_code == 404 and r.get_json()["code"] == "propose_session_expired"
    assert client.get(body["items"][0]["thumbnail_url"]).status_code == 404
    unknown = commit(client, "0" * 24, "0" * 12)
    assert unknown.status_code == 404 and unknown.get_json()["code"] == "propose_session_expired"


@needs_worker
def test_unknown_candidate_ids_and_foreign_sessions_are_refused(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    r = commit(client, body["session"], "f" * 12)
    assert r.status_code == 404 and r.get_json()["code"] == "propose_candidate_unknown"
    r = client.get(f"/api/ai/data-figure/thumbnail/{body['session']}/..%2F..%2Fetc")
    assert r.status_code in (404,)
    snapshot = tree_state(figs)
    assert not (figs / "data" / "tavotto_figures").exists()
    assert tree_state(figs) == snapshot


@needs_worker
def test_idempotent_propose_and_commit_by_client_request_id(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    a = propose(client, client_request_id="rid-prop-0001").get_json()
    b = propose(client, client_request_id="rid-prop-0001").get_json()
    assert a == b and len(P._SESSIONS) == 1
    first = commit(client, a["session"], a["items"][0]["id"], rid="rid-comm-0001").get_json()
    again = commit(client, a["session"], a["items"][0]["id"], rid="rid-comm-0001").get_json()
    assert first["task"]["outcome"] == "completed"
    assert again["task"]["deduped"] is True and again["task"]["task_id"] == first["task"]["task_id"]
    assert again["script"] == first["script"], "会话已随提交删掉，去重仍回首次的脚本"
    assert len(list((figs / "data" / "tavotto_figures").glob("g_figure*.py"))) == 1


@needs_worker
def test_discard_removes_the_session_directory(project):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client).get_json()
    assert any(P.CANDIDATE_ROOT.iterdir())
    r = client.post("/api/ai/data-figure/discard", json={"session": body["session"]})
    assert r.status_code == 200
    assert not any(P.CANDIDATE_ROOT.iterdir()) and not P._SESSIONS
    again = client.post("/api/ai/data-figure/discard", json={"session": body["session"]})
    assert again.status_code == 200, "幂等"


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("post", "/api/ai/data-figure/propose", {"folder": "data"}),
        ("get", "/api/ai/data-figure/thumbnail/" + "0" * 24 + "/" + "0" * 12, None),
        ("post", "/api/ai/data-figure/commit", {"session": "0" * 24, "id": "0" * 12}),
        ("post", "/api/ai/data-figure/discard", {"session": "0" * 24}),
    ],
)
def test_flag_off_is_404_taf_disabled_and_writes_nothing(project, method, url, body):
    m, client, figs, state = project
    put(figs, "data/g.csv", simple_rows())
    state["taf"] = False
    before = tree_state(figs)
    r = getattr(client, method)(url, **({"json": body} if body is not None else {}))
    assert r.status_code == 404 and r.get_json()["code"] == "taf_disabled"
    assert tree_state(figs) == before and not P.CANDIDATE_ROOT.exists()
    assert ai_data_task.V2_TASK_TYPE not in ai_tasks.ADAPTERS


@pytest.mark.parametrize(
    "body",
    [None, [], {"answers": "x"}, {"hint": 5}, {"table": {"a": 1}}, {"client_request_id": "a b"}],
)
def test_malformed_requests_are_400_not_500(project, body):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    r = client.post(
        "/api/ai/data-figure/propose",
        json=body if body is not None else None,
        data=None if body is not None else "not json",
    )
    assert r.status_code == 400


# =========================================================================== /api/ai/data-files
def test_data_files_lists_all_table_formats_with_xlsx_sheets(project):
    m, client, figs, _ = project
    put(figs, "d/a.csv", simple_rows())
    put(figs, "d/b.tsv", simple_rows(), "\t")
    put(figs, "d/c.TAB", simple_rows(), "\t")
    make_xlsx(figs / "d" / "x.xlsx", {"S1": simple_rows(), "S2": simple_rows()}, hidden=("S2",))
    (figs / "d" / "~$lock.xlsx").write_bytes(b"lock")
    (figs / "d" / "old.xls").write_bytes(b"x")
    (figs / "d" / "n.txt").write_text("x", encoding="utf-8")
    (figs / "d" / "bad.xlsx").write_bytes(b"nope")
    body = client.get("/api/ai/data-files").get_json()
    rows = {f["path"]: f for f in body["files"]}
    assert set(rows) == {"d/a.csv", "d/b.tsv", "d/c.TAB", "d/x.xlsx", "d/bad.xlsx"}
    assert rows["d/x.xlsx"]["sheets"] == [
        {"name": "S1", "hidden": False},
        {"name": "S2", "hidden": True},
    ]
    assert rows["d/bad.xlsx"]["sheets"] is None
    assert "sheets" not in rows["d/a.csv"]


# =========================================================================== 任务适配器的纯函数
def test_common_module_state_is_absent_same_or_conflict(tmp_path):
    p = tmp_path / "tavotto_plot_12345678.py"
    assert ai_data_task.common_module_state(p, b"A") == "absent"
    p.write_bytes(b"A")
    assert ai_data_task.common_module_state(p, b"A") == "same"
    assert ai_data_task.common_module_state(p, b"B") == "conflict"
    p.unlink()
    p.mkdir()
    assert ai_data_task.common_module_state(p, b"A") == "conflict", "目录不是普通文件"


def test_data_verdict_reads_only_checks():
    def row(*cs):
        return {
            "verification": {
                "figure": {"checks": [dict(id=i, verdict=v) for i, v in cs]},
                "persistence": {"checks": []},
            }
        }

    assert (
        ai_data_task.data_verdict(
            row(
                ("data.verifiable", "verified"),
                ("data.a", "verified"),
                ("render.figure", "verified"),
            )
        )
        == "verified"
    )
    assert (
        ai_data_task.data_verdict(row(("data.verifiable", "verified"), ("data.a", "failed")))
        == "mismatch"
    )
    assert (
        ai_data_task.data_verdict(
            row(("data.verifiable", "unknown"), ("render.figure", "verified"))
        )
        == "unverifiable"
    )
    assert ai_data_task.data_verdict(row(("render.figure", "verified"))) == "unknown"
    assert ai_data_task.data_verdict(None) == "unknown"


def test_unplotted_rows_reports_rows_without_an_x_value(tmp_path):
    d, prof = _profile(tmp_path, {"r.csv": rel_rows()})
    values = adf.inspect_data_source(d / "r.csv")
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "r.csv",
            "family": "relationship",
            "layers": [{"mark": "points"}],
            "x": "dose_mg",
            "y": ["response_mV"],
            "why": "x",
        }
    )
    rows = ai_data_task.unplotted_rows(values, recipe)
    assert rows == [{"row": len(values.rows) + 1, "column": "dose_mg", "reason": "x_not_a_value"}]
    assert trend_rows() and cmp_rows()  # 夹具可用（防止导入被优化掉）
