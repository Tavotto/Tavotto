"""TAF 数据出图任务（ADR 0138 Phase 5b）：真 app + 真 safe worker 跑 FigureBench 的 FB-D 场景。

断言的是磁盘与历史库里的事实：新脚本在不在、注册表登记了什么、别的文件动没动、任务结局与检查结论；渲染那一次就是
前端随后本来要做的那一次（提交后会话已 built）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_task,
    ai_figure_edit,
    ai_history,
    ai_tasks,
    config,
    discover,
    pool,
    project_watch,
    scriptlock,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "figurebench" / "csv_inputs"

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


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
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    state = {"taf": True}
    monkeypatch.setattr(
        config, "ai_settings", lambda: {"taf": {"enabled": True}} if state["taf"] else {}
    )
    figs = tmp_path / "figures"
    (figs / "data").mkdir(parents=True)
    for f in FIX.glob("*.csv"):
        shutil.copy(f, figs / "data" / f.name)
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"version": 1, "scripts": {}}), encoding="utf-8"
    )
    m.open_project(str(figs))
    try:
        yield m, m.app.test_client(), figs, state
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()
        ai_tasks.unregister(ai_data_task.TASK_TYPE)


def _snapshot(base: Path) -> dict[str, bytes]:
    return {
        p.relative_to(base).as_posix(): p.read_bytes()
        for p in sorted(base.rglob("*"))
        if p.is_file() and p.name != "tavotto_registry.json"
    }


def _post(client, data, instruction, rid=None):
    body = {"data": data, "instruction": instruction}
    if rid:
        body["client_request_id"] = rid
    return client.post("/api/ai/data-figure", json=body)


def _checks(task_id):
    row = ai_history.get(task_id)
    v = row["verification"]
    return {
        c["id"]: (c["verdict"], c["reason_code"])
        for kind in ("figure", "persistence")
        for c in v[kind]["checks"]
    }


needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)


@needs_worker
def test_standard_csv_becomes_a_registered_verified_figure(project):
    m, client, figs, _ = project
    before = _snapshot(figs)
    r = _post(client, "data/standard.csv", "用这份数据画一张折线图。")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    task = body["task"]
    assert task["outcome"] == "completed", task
    assert body["script"] == "data/standard_figure.py" and body["stem"] == "standard_figure"
    script = figs / body["script"]
    assert script.is_file()
    assert discover.registered_stems(figs, body["script"]) == ["standard_figure"]
    checks = _checks(task["task_id"])
    assert checks["render.figure"] == ("verified", None)
    assert checks["data.voltage_mV"] == ("verified", None)
    assert checks["goal.rows_plotted"] == ("verified", None)
    after = _snapshot(figs)
    assert {k: v for k, v in after.items() if k in before} == before, "已有文件一个字节都不动"
    assert set(after) - set(before) == {"data/standard_figure.py"}, "项目里只多出新脚本"


@needs_worker
def test_text_values_are_reported_and_the_task_is_partial(project):
    m, client, figs, _ = project
    body = _post(client, "data/mixed_types.csv", "画每个样品的 value。").get_json()
    task = body["task"]
    assert task["outcome"] == "partial", task
    row = ai_history.get(task["task_id"])
    goal = next(
        c for c in row["verification"]["figure"]["checks"] if c["id"] == "goal.rows_plotted"
    )
    assert goal["verdict"] == "failed" and goal["observed"]["total"] == 2
    assert _checks(task["task_id"])["data.value"] == ("verified", None)


@needs_worker
def test_missing_values_break_the_line_and_still_complete(project):
    m, client, figs, _ = project
    task = _post(client, "data/missing_values.csv", "画 y 随 x 的变化。").get_json()["task"]
    assert task["outcome"] == "completed", task


def test_meaningless_headers_ask_first_and_write_nothing(project):
    m, client, figs, _ = project
    before = _snapshot(figs)
    reg = (figs / "tavotto_registry.json").read_bytes()
    body = _post(client, "data/ambiguous_headers.csv", "把数据画出来。").get_json()
    assert body["task"]["outcome"] == "needs_input"
    assert body["preview"] == {"x": "a", "y": ["b", "c"], "kind": "line", "x_is_date": False}
    assert "script" not in body
    assert _snapshot(figs) == before and (figs / "tavotto_registry.json").read_bytes() == reg


@needs_worker
def test_instructions_inside_the_data_change_nothing(project):
    m, client, figs, _ = project
    before = _snapshot(figs)
    task = _post(client, "data/injection.csv", "画 value 随 x 的变化。").get_json()["task"]
    assert task["outcome"] == "completed", task
    after = _snapshot(figs)
    assert {k: after[k] for k in before} == before
    assert set(after) - set(before) == {"data/injection_figure.py"}


@needs_worker
def test_a_taken_name_gets_a_suffix_and_never_overwrites(project):
    m, client, figs, _ = project
    (figs / "data" / "standard_figure.py").write_text("# mine\n", encoding="utf-8")
    body = _post(client, "data/standard.csv", "画出来").get_json()
    assert body["script"] == "data/standard_figure_2.py"
    assert (figs / "data" / "standard_figure.py").read_text(encoding="utf-8") == "# mine\n"


@needs_worker
@pytest.mark.parametrize("mutate", ["delete", "rename"])
def test_a_retried_request_reconciles_after_the_csv_is_gone(project, mutate):
    """响应丢了、脚本已提交；用户随后删掉 / 改名 CSV，同 id 重发仍回首次的结局（不是 400，不再建新脚本）。"""
    m, client, figs, _ = project
    first = _post(client, "data/standard.csv", "画出来。", rid="rid-d07-0002").get_json()
    assert first["task"]["outcome"] == "completed", first
    before = sorted(p.name for p in (figs / "data").glob("*.py"))
    src = figs / "data" / "standard.csv"
    if mutate == "delete":
        src.unlink()
    else:
        src.rename(figs / "data" / "renamed.csv")
    resp = _post(client, "data/standard.csv", "画出来。", rid="rid-d07-0002")
    assert resp.status_code == 200, resp.get_json()
    again = resp.get_json()
    assert again["task"]["deduped"] is True and again["task"]["task_id"] == first["task"]["task_id"]
    assert again["script"] == first["script"]
    assert sorted(p.name for p in (figs / "data").glob("*.py")) == before


@needs_worker
def test_the_same_request_runs_once(project):
    m, client, figs, _ = project
    first = _post(
        client, "data/two_series.csv", "对比 ctrl 和 treat 两组。", rid="rid-d07-0001"
    ).get_json()
    again = _post(
        client, "data/two_series.csv", "对比 ctrl 和 treat 两组。", rid="rid-d07-0001"
    ).get_json()
    assert first["task"]["outcome"] == "completed", first
    assert again["task"]["deduped"] is True and again["task"]["task_id"] == first["task"]["task_id"]
    # 去重回的是**首次**写下的脚本（本次重算的落点会是 `_2`，不能用）；项目里仍只有一份
    assert again["script"] == first["script"] == "data/two_series_figure.py"
    assert again["stem"] == first["stem"] == "two_series_figure"
    assert sorted(p.name for p in (figs / "data").glob("two_series_figure*.py")) == [
        "two_series_figure.py"
    ]


def test_flag_off_is_404_and_writes_nothing(project):
    m, client, figs, state = project
    state["taf"] = False
    before = _snapshot(figs)
    r = _post(client, "data/standard.csv", "画出来")
    assert r.status_code == 404 and r.get_json()["code"] == "taf_disabled"
    assert _snapshot(figs) == before
    assert ai_data_task.TASK_TYPE not in ai_tasks.ADAPTERS


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ("../outside.csv", "data_path_outside_project"),
        ("data/nope.csv", "data_not_found"),
        ("tavotto_registry.json", "data_not_csv"),
    ],
)
def test_bad_data_paths_are_refused(project, data, code):
    m, client, figs, _ = project
    (figs.parent / "outside.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    r = _post(client, data, "画出来")
    assert r.status_code == 400 and r.get_json()["code"] == code


def test_a_failed_registration_removes_the_new_script(project, monkeypatch):
    m, client, figs, _ = project

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(discover, "register", boom)
    task = _post(client, "data/standard.csv", "画出来").get_json()["task"]
    assert task["outcome"] == "failed" and task["error"] == "register_failed"
    assert not (figs / "data" / "standard_figure.py").exists()


def test_a_stem_registered_meanwhile_is_not_stolen(project, monkeypatch):
    """placement 之后、提交之前别人登记了同名 stem：提交在项目锁里复核，不让 `register` 把它从别的脚本那里摘走。"""
    m, client, figs, _ = project
    real = ai_data_task.TemplateExecutor.commit

    def racing(self, run, cand):
        discover.register(figs, "other.py", [cand.prepared.placement.stem])
        return real(self, run, cand)

    monkeypatch.setattr(ai_data_task.TemplateExecutor, "commit", racing)
    task = _post(client, "data/standard.csv", "画出来").get_json()["task"]
    assert task["outcome"] == "failed" and task["error"] == "stem_taken"
    assert discover.registered_stems(figs, "other.py") == ["standard_figure"]
    assert not (figs / "data" / "standard_figure.py").exists()


def test_a_product_written_meanwhile_is_not_stolen(project, monkeypatch):
    """placement 之后、提交之前同目录写出了未登记的 `<stem>.png`：锁内复核完整空闲检查，什么都不写。"""
    m, client, figs, _ = project
    real = ai_data_task.TemplateExecutor.commit

    def racing(self, run, cand):
        (figs / "data" / "standard_figure.png").write_bytes(b"PNG")
        return real(self, run, cand)

    monkeypatch.setattr(ai_data_task.TemplateExecutor, "commit", racing)
    task = _post(client, "data/standard.csv", "画出来").get_json()["task"]
    assert task["outcome"] == "failed" and task["error"] == "stem_taken"
    assert not (figs / "data" / "standard_figure.py").exists()
    assert (figs / "data" / "standard_figure.png").read_bytes() == b"PNG"


def test_stem_choice_is_case_insensitive(project):
    """大小写不敏感的文件系统上 `Standard_Figure.pdf` 与 `standard_figure` 是同一个名：产物与登记都按 casefold 避开。"""
    m, client, figs, _ = project
    (figs / "data" / "Standard_Figure.pdf").write_bytes(b"%PDF")
    assert ai_data_task.place(figs, "data/standard.csv").stem == "standard_figure_2"
    (figs / "data" / "Standard_Figure.pdf").unlink()
    assert ai_data_task.place(figs, "data/standard.csv").stem == "standard_figure"
    discover.register(figs, "x.py", ["STANDARD_FIGURE"])
    assert ai_data_task.place(figs, "data/standard.csv").stem == "standard_figure_2"


# --------------------------------------------------------------------------- 写入口与 Task Core 的放行规则
def test_create_script_never_overwrites_and_needs_the_guard(tmp_path):
    target = tmp_path / "new.py"
    with pytest.raises(RuntimeError):
        scriptlock.create_script(target, b"A = 1\n")
    with scriptlock.script_guard(target):
        scriptlock.create_script(target, b"A = 1\n")
        with pytest.raises(FileExistsError):
            scriptlock.create_script(target, b"A = 2\n")
    assert target.read_bytes() == b"A = 1\n"


def test_create_script_does_not_follow_a_link(tmp_path):
    victim = tmp_path / "victim.py"
    victim.write_text("# keep\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(victim)
    except OSError:
        pytest.skip("not_run：此平台不能建符号链接")
    with scriptlock.script_guard(link), pytest.raises(FileExistsError):
        scriptlock.create_script(link, b"A = 1\n")
    assert victim.read_text(encoding="utf-8") == "# keep\n"


class _Writer:
    executor_id = ai_tasks.DETERMINISTIC
    kind = ai_tasks.DETERMINISTIC
    writes_user_files = True

    def execute(self, run):
        return {}

    def commit(self, run, cand):
        pass

    def discard(self, run, cand):
        pass


def _env(**over):
    d = {
        "schema_version": 1,
        "task_type": ai_data_task.TASK_TYPE,
        "project_ref": {"project_key": "/p"},
        "user_intent": "画出来",
        "execution_policy": {"executor": "deterministic"},
        "input_refs": [{"kind": "data_file", "path_rel": "d.csv", "sha256": "0"}],
        "output_contract": {"allowed_writes": ["d_figure.py"], "new_figures": True},
    }
    d.update(over)
    return ai_tasks.TaskEnvelope.from_dict(d)


@pytest.mark.parametrize(
    ("creates_only", "env_over", "kind"),
    [
        (False, {}, ai_tasks.DETERMINISTIC),  # 没声明只新建
        (True, {"output_contract": {"allowed_writes": ["d_figure.py"]}}, ai_tasks.DETERMINISTIC),
        (True, {}, "agent"),  # 调模型的执行器不走这条
    ],
)
def test_task_core_only_admits_deterministic_create_only_writers(
    tmp_path, creates_only, env_over, kind
):
    ex = _Writer()
    ex.creates_only = creates_only
    ex.kind = kind
    ai_data_task.ensure_registered()
    try:
        with pytest.raises(ai_tasks.ContractError) as e:
            ai_tasks.run_task(_env(**env_over), ex, db_path=tmp_path / "h.sqlite3")
        assert e.value.code == "contract_writes_unsupported"
    finally:
        ai_tasks.unregister(ai_data_task.TASK_TYPE)


@needs_worker
def test_a_render_that_does_not_match_the_data_fails_the_task(project, monkeypatch):
    """反例：把期望值改掉一个数（等价于「脚本画的不是这份数据」）——验证必须判 failed，而不是看见有图就算过。"""
    m, client, figs, _ = project
    from tavotto.engine import ai_data_figure

    real = ai_data_figure.expected_fingerprints

    def skewed(plotted):
        out = real(plotted)
        out[0]["digest"] = {**out[0]["digest"], "sha256": "0" * 64}
        return out

    monkeypatch.setattr(ai_data_figure, "expected_fingerprints", skewed)
    body = _post(client, "data/standard.csv", "画出来").get_json()
    task = body["task"]
    assert task["outcome"] == "failed", task
    assert body["script"] == "data/standard_figure.py", (
        "提交后验证失败：文件已建，响应如实带 script"
    )
    assert _checks(task["task_id"])["data.voltage_mV"] == ("failed", "data_mismatch")


def test_an_existing_output_with_the_same_name_is_not_adopted(project):
    m, client, figs, _ = project
    (figs / "data" / "standard_figure.pdf").write_bytes(b"%PDF-1.4 old\n")
    pl = ai_data_task.place(figs, "data/standard.csv")
    assert pl.stem == "standard_figure_2", "旧产物不让新图冒认"


def test_data_files_flag_off_is_404(project):
    m, client, figs, state = project
    state["taf"] = False
    r = client.get("/api/ai/data-files")
    assert r.status_code == 404 and r.get_json()["code"] == "taf_disabled"


def test_data_files_lists_project_csv_only(project):
    m, client, figs, state = project
    n = len(list(FIX.glob("*.csv")))
    assert n == 8
    (figs / ".hidden").mkdir()
    (figs / ".hidden" / "secret.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (figs / "node_modules").mkdir()
    (figs / "node_modules" / "dep.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (figs / "data" / "UPPER.CSV").write_text("a,b\n1,2\n", encoding="utf-8")
    (figs / "data" / "notes.txt").write_text("x", encoding="utf-8")
    body = client.get("/api/ai/data-files").get_json()
    paths = [f["path"] for f in body["files"]]
    assert body["truncated"] is False
    assert paths == sorted(paths) and len(paths) == n + 1
    assert "data/UPPER.CSV" in paths and "data/standard.csv" in paths
    assert not any(".hidden" in p or "node_modules" in p for p in paths)
    assert all(isinstance(f["size"], int) and f["size"] > 0 for f in body["files"])


def test_data_files_truncates_at_limit(project, monkeypatch):
    m, client, figs, state = project
    monkeypatch.setattr(m, "_DATA_FILES_MAX", 3)
    body = client.get("/api/ai/data-files").get_json()
    assert len(body["files"]) == 3 and body["truncated"] is True


def test_capabilities_carries_taf_enabled(project):
    m, client, figs, state = project
    assert client.get("/api/ai/capabilities").get_json()["taf_enabled"] is True
    state["taf"] = False
    assert client.get("/api/ai/capabilities").get_json()["taf_enabled"] is False


def test_data_file_listing_stops_at_the_scan_budget(project, monkeypatch):
    m, client, figs, _ = project
    monkeypatch.setattr(m, "_DATA_FILES_SCAN_MAX", 3)
    body = client.get("/api/ai/data-files").get_json()
    assert body["truncated"] is True, "目录项超了预算：停下并如实标 truncated"


def test_data_file_scan_budget_counts_entries_one_by_one(project, monkeypatch):
    """一个目录里的项数就超预算：超预算前已看过的 csv 保留（不是整批丢弃），且如实标 truncated。"""
    m, client, figs, _ = project
    big = figs / "big"
    big.mkdir()
    for i in range(10):
        (big / f"f{i}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(m, "_DATA_FILES_SCAN_MAX", 5)
    body = client.get("/api/ai/data-files").get_json()
    assert body["truncated"] is True
    assert 0 < len(body["files"]) <= 5, body
    paths = [f["path"] for f in body["files"]]
    assert paths == sorted(paths)
