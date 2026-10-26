"""改图任务内置执行器 `check` 的真链路（真 app 的 `_convert_runner` + 真探测 + 真 safe worker）：
show-only 脚本按原名比对、写入绊线被识别为“没量到”。假模型那一层见 `test_ai_connections.py::test_builtin_edit_check_*`。"""

from __future__ import annotations

import pytest

from tavotto.engine import ai_bridge, ai_convert, ai_figure_edit, ai_history, pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SHOW_ONLY = (
    "import matplotlib.pyplot as plt\n\n\ndef main():\n"
    "    plt.plot([1, 2, 3], [1, 4, 9])\n"
    "    plt.title('old')\n"
    "    plt.show()\n\n\nif __name__ == '__main__':\n    main()\n"
)
WRITES_CSV = SHOW_ONLY.replace(
    "    plt.show()\n",
    "    import os\n    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.csv'), 'w').write('x')\n    plt.show()\n",
)


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tavotto import app as m

    # pytest 的 tmp_path 在系统临时目录下，而绊线白名单放行临时目录：把 worker 的临时目录挪走，“根外”才真是根外
    # （同 `test_mcp_write_tripwire.world`）
    tmpdir = tmp_path / "worker-tmp"
    tmpdir.mkdir()
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmpdir))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    figs = tmp_path / "figures"
    figs.mkdir()
    m.open_project(str(figs))
    try:
        yield m, figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _check(m, figs, base_src, new_src, sid="s1", entry=None):
    script = figs / "plot.py"
    script.write_text(base_src, encoding="utf-8")
    ctx = next(iter(m.PROJECTS.values())) if hasattr(m, "PROJECTS") else None
    runner = m._convert_runner(ctx, "plot.py", with_manifests=True, entry=entry)
    # 基线 = 原脚本自己的校验运行（stem 是 plot）；只测数据指纹的比对口径，不动热会话
    baseline = runner(str(figs), "plot.py", None).get("manifests")
    assert baseline and "plot" in baseline, baseline
    cand = ai_figure_edit.create_candidate(sid, script, figs, "plot.py")
    cand.target.write_text(new_src, encoding="utf-8")
    cand.extras.update({"runner": runner, "check_before": lambda: baseline})
    try:
        return ai_figure_edit.agent_check(cand, ai_convert.run_validation)
    finally:
        ai_figure_edit.remove_candidate(cand.dir)


def test_show_only_script_keeps_its_name_when_only_the_title_changes(world):
    m, figs = world
    ok, report = _check(m, figs, SHOW_ONLY, SHOW_ONLY.replace("'old'", "'new'"))
    assert ok, report
    assert "data unchanged" in report and "fingerprint not measured" not in report, report


def test_show_only_script_with_changed_data_is_still_caught(world):
    m, figs = world
    ok, report = _check(m, figs, SHOW_ONLY, SHOW_ONLY.replace("[1, 4, 9]", "[1, 4, 10]"))
    assert not ok and "data_changed" in report and "'plot'" in report, report


def test_relative_output_write_is_reported_as_not_measured_not_as_a_broken_script(world):
    m, figs = world
    ok, report = _check(m, figs, SHOW_ONLY, WRITES_CSV.replace("'old'", "'new'"))
    assert ok and "not measured" in report and "does not run" not in report, report
    assert not (figs / "data.csv").exists(), "绊线挡住了写入"


def test_stage_prefix_mirrors_the_convert_module():
    assert ai_figure_edit.STAGE_PREFIX == ai_convert.STAGE_PREFIX


def test_convert_validation_gives_the_write_hint_for_a_relative_output_script(world):
    """转换任务的校验运行走同一条真链路：绊线的码被探测折成 script_probe_failed，也要走到设计好的“写到项目内”提示。"""
    m, figs = world
    script = figs / "plot.py"
    script.write_text(SHOW_ONLY, encoding="utf-8")
    ctx = next(iter(m.PROJECTS.values()))
    cand = ai_figure_edit.create_candidate("c1", script, figs, "plot.py")
    cand.new_bytes = WRITES_CSV.encode()
    try:
        res = ai_convert.run_validation(cand, m._convert_runner(ctx, "plot.py"))
    finally:
        ai_figure_edit.remove_candidate(cand.dir)
    assert res["status"] == "error" and res["code"] == ai_convert.WRITE_OUTSIDE_ROOTS_CODE, res
    assert res["message"] == ai_convert.WRITE_OUTSIDE_HINT


TWO_PLOTS = (
    "import matplotlib.pyplot as plt\n\n\ndef main():\n"
    "    plt.plot([5, 6, 7], [5, 6, 7])\n    plt.title('first')\n    plt.show()\n\n\n"
    "def publication():\n"
    "    plt.plot([1, 2, 3], [1, 4, 9])\n    plt.title('second')\n    plt.show()\n"
)


def test_check_renders_the_registered_entry_not_the_first_inferred_one(world):
    m, figs = world
    ok, report = _check(
        m, figs, TWO_PLOTS, TWO_PLOTS.replace("'second'", "'renamed'"), entry="publication"
    )
    assert ok and "data unchanged" in report, report
    ok, report = _check(
        m,
        figs,
        TWO_PLOTS,
        TWO_PLOTS.replace("[1, 4, 9]", "[1, 4, 10]"),
        sid="s2",
        entry="publication",
    )
    assert not ok and "data_changed" in report, report


# ---------------------------------------------------------------- 标题与图例同一个字符串（真 worker）
SHARED_LABEL = (
    "import matplotlib.pyplot as plt\n\n\ndef main():\n"
    "    txt = 'B'\n"
    "    fig, ax = plt.subplots()\n"
    "    ax.plot([1, 2, 3], [1, 4, 9], label=txt)\n"
    "    ax.set_title(txt)\n"
    "    ax.legend()\n"
    "    plt.show()\n\n\nif __name__ == '__main__':\n    main()\n"
)
NO_LEGEND = SHARED_LABEL.replace("    ax.legend()\n", "")
#: Agent 先改共享字符串（标题和图例一起变），再改回去、只改标题
SHARED_CHANGED = SHARED_LABEL.replace("txt = 'B'", "txt = 'B: treated (n=6)'")
TITLE_ONLY = SHARED_LABEL.replace("ax.set_title(txt)", "ax.set_title('B: treated (n=6)')")


def _label_flow(m, figs, base_src, first_src, final_src, outcome="completed"):
    """复刻内置 Agent 的过程：第一次 check 用 `first_src`（改了共享字符串），最后提交 `final_src`；提交后那次渲染用真 runner。"""
    script = figs / "plot.py"
    script.write_text(base_src, encoding="utf-8")
    ctx = next(iter(m.PROJECTS.values()))
    runner = m._convert_runner(ctx, "plot.py", with_manifests=True)
    baseline = runner(str(figs), "plot.py", None).get("manifests")
    assert baseline and "plot" in baseline, baseline
    cand = ai_figure_edit.create_candidate("lf", script, figs, "plot.py")
    cand.extras.update({"runner": runner, "check_before": lambda: baseline})
    try:
        cand.target.write_text(first_src, encoding="utf-8")
        ai_figure_edit.agent_check(cand, ai_convert.run_validation)
        cand.target.write_text(final_src, encoding="utf-8")
        ok, report = ai_figure_edit.agent_check(cand, ai_convert.run_validation)
        assert ok, report
        # 提交后的最终渲染（真 runner 渲染最终那一版）
        cand.committed = True
        script.write_text(final_src, encoding="utf-8")
        final = runner(str(figs), "plot.py", None)
        ai_figure_edit.record_final_labels(cand, final.get("manifests"))
        return ai_figure_edit.legend_label_suggestions(cand, outcome)
    finally:
        ai_figure_edit.remove_candidate(cand.dir)


def test_shared_title_and_legend_string_yields_a_follow_up_suggestion(world):
    m, figs = world
    got = _label_flow(m, figs, SHARED_LABEL, SHARED_CHANGED, TITLE_ONLY)
    assert got == [
        {
            "kind": "legend_label",
            "stem": "plot",
            "gid": "axes_0.lines_0",
            "legend_gid": "axes_0.legend.texts_0",
            "from": "B",
            "to": "B: treated (n=6)",
        }
    ], got


def test_no_suggestion_when_the_figure_has_no_legend(world):
    m, figs = world
    first = NO_LEGEND.replace("txt = 'B'", "txt = 'B: treated (n=6)'")
    final = NO_LEGEND.replace("ax.set_title(txt)", "ax.set_title('B: treated (n=6)')")
    assert _label_flow(m, figs, NO_LEGEND, first, final) == []


def test_no_suggestion_when_the_task_is_not_completed(world):
    m, figs = world
    assert _label_flow(m, figs, SHARED_LABEL, SHARED_CHANGED, TITLE_ONLY, outcome="partial") == []


TWO_LABELED = (
    "import matplotlib.pyplot as plt\n\n\ndef main():\n"
    "    plt.plot([1, 2, 3], [1, 4, 9], label='control')\n"
    "    plt.plot([1, 2, 3], [2, 3, 5], label='treated')\n"
    "    plt.title('old')\n    plt.xlabel('Time (s)')\n    plt.legend()\n    plt.show()\n\n\n"
    "if __name__ == '__main__':\n    main()\n"
)


def test_report_lists_every_visible_text_change_and_tells_the_model_to_keep_only_the_asked_ones(
    world,
):
    """②：通过的报告列出基线 → 校验渲染之间每一处可见文字变化（标题 / 轴标题 / 曲线 label），
    并附一句“只留用户要求的、其余改回”。刻度文字与图例文字（曲线 label 的派生）不报。"""
    m, figs = world
    new = (
        TWO_LABELED.replace("'old'", "'new title'")
        .replace("'Time (s)'", "'Elapsed (s)'")
        .replace("label='treated'", "label='treated (n=6)'")
    )
    ok, report = _check(m, figs, TWO_LABELED, new)
    assert ok, report
    assert "data unchanged" in report
    assert 'title of axes_0: "old" -> "new title"' in report, report
    assert 'xlabel of axes_0: "Time (s)" -> "Elapsed (s)"' in report, report
    assert 'line label axes_0.lines_1: "treated" -> "treated (n=6)"' in report, report
    assert "Keep only the text changes the user asked for; revert the others." in report, report
    assert "legend" not in report and "ticklabel" not in report and "texts_" not in report, report


def test_report_says_so_when_no_visible_text_changed(world):
    m, figs = world
    ok, report = _check(m, figs, TWO_LABELED, TWO_LABELED.replace("[1, 4, 9]", "[1, 4, 9]") + "\n")
    assert ok and "no visible text changes." in report and "text changes:" not in report, report


def test_rename_passes_the_data_check_but_swapped_labels_are_rejected(world):
    """①：只改名（数据没动）通过；两条曲线的名字对调是在给数据贴错标签，`check` 当场拒绝并点名 label_swapped。"""
    m, figs = world
    ok, report = _check(
        m, figs, TWO_LABELED, TWO_LABELED.replace("label='control'", "label='control (n=5)'")
    )
    assert ok and "data unchanged" in report, report
    swapped = (
        TWO_LABELED.replace("label='control'", "label='X'")
        .replace("label='treated'", "label='control'")
        .replace("label='X'", "label='treated'")
    )
    ok, report = _check(m, figs, TWO_LABELED, swapped, sid="s2")
    assert not ok and "label_swapped" in report and "axes_0.lines_0" in report, report
    assert "Only presentation may change" not in report, (
        "名字对调不是“数据变了”，别让模型去还原数据"
    )
    # 改名的同时改了这条线的数据：仍是 subject_removed
    both = TWO_LABELED.replace("label='treated'", "label='treated (n=6)'").replace(
        "[2, 3, 5]", "[2, 3, 6]"
    )
    ok, report = _check(m, figs, TWO_LABELED, both, sid="s3")
    assert not ok and "subject_removed" in report, report


def _manifest_with(*pairs, role="title"):
    return {
        "elements": [
            {"gid": g, "role": role, "editable": [{"prop": "text", "value": v}]} for g, v in pairs
        ]
    }


def test_text_changes_note_is_bounded_and_covers_added_and_removed_text():
    before = {"fig": _manifest_with(("axes_0.title", "a" * 200), ("axes_0.xlabel", "gone"))}
    after = {"fig": _manifest_with(("axes_0.title", "b" * 200), ("axes_0.text_0", "new note"))}
    note = ai_figure_edit.text_changes_note(after, before)
    assert note.startswith("text changes: ") and "…" in note, note  # 单个文字被截断
    assert (
        'xlabel of axes_0: removed "gone"' in note
        and 'text axes_0.text_0: added "new note"' in note
    )
    many_before = {"fig": _manifest_with(*[(f"axes_{i}.text_0", "x") for i in range(80)])}
    many_after = {"fig": _manifest_with(*[(f"axes_{i}.text_0", "y" * 50) for i in range(80)])}
    long_note = ai_figure_edit.text_changes_note(many_after, many_before)
    assert "more)" in long_note and len(long_note) < 1200, len(long_note)  # 整段有界
    # 基线缺（None）的 stem 不比；多张图时每条带 stem 前缀
    assert "no visible text changes" in ai_figure_edit.text_changes_note(after, {"fig": None})
    two = ai_figure_edit.text_changes_note(
        {"a": after["fig"], "b": after["fig"]}, {"a": before["fig"], "b": before["fig"]}
    )
    assert "[a] " in two and "[b] " in two


def test_renamed_from_verification_is_a_closed_identity_only_shape():
    check = {
        "id": "plot.data.axes_0.lines_1",
        "stage": "postcommit",
        "verdict": "verified",
        "observed": {
            "relabeled": {"from_identity": "l1:aa", "to_identity": "l1:bb"},
            "stem": "plot",
            "gid": "axes_0.lines_1",
        },
    }
    ver = {"figure": {"checks": [check, dict(check), {**check, "verdict": "failed"}]}}
    assert ai_figure_edit.renamed_from_verification(ver) == [
        {"stem": "plot", "gid": "axes_0.lines_1", "from_identity": "l1:aa", "to_identity": "l1:bb"}
    ]
    assert ai_figure_edit.renamed_from_verification(None) == []
    bad = {**check, "observed": {**check["observed"], "stem": ""}}
    assert ai_figure_edit.renamed_from_verification({"figure": {"checks": [bad]}}) == []
