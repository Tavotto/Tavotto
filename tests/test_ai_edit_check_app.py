"""改图任务内置执行器 `check` 的真链路（真 app 的 `_convert_runner` + 真探测 + 真 safe worker）：
show-only 脚本按原名比对、写入绊线被识别为“没量到”。假模型那一层见 `test_ai_connections.py::test_builtin_edit_check_*`。"""

from __future__ import annotations

import json

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


def _check(m, figs, base_src, new_src, sid="s1", entry=None, register=False, newline=None):
    script = figs / "plot.py"
    script.write_text(base_src, encoding="utf-8")
    if register:
        m.engine_discover.register(figs, "plot.py", ["plot"], entry=entry or "main")
    ctx = next(iter(m.PROJECTS.values())) if hasattr(m, "PROJECTS") else None
    runner = m._convert_runner(ctx, "plot.py", with_manifests=True, entry=entry)
    # 基线 = 原脚本自己的校验运行（stem 是 plot）；只测数据指纹的比对口径，不动热会话
    baseline = runner(str(figs), "plot.py", None).get("manifests")
    assert baseline and "plot" in baseline, baseline
    cand = ai_figure_edit.create_candidate(sid, script, figs, "plot.py")
    cand.target.write_text(new_src, encoding="utf-8", newline=newline)
    # Compare the file at the check boundary, including the platform's line endings.
    before_check = cand.target.read_bytes()
    cand.extras.update({"runner": runner, "check_before": lambda: baseline})
    try:
        result = ai_figure_edit.agent_check(cand, ai_convert.run_validation)
        assert cand.target.read_bytes() == before_check, (
            "Checking an edit must not rewrite its outputs"
        )
        return result
    finally:
        ai_figure_edit.remove_candidate(cand.dir)


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_show_only_script_keeps_its_name_when_only_the_title_changes(world, newline):
    m, figs = world
    ok, report = _check(m, figs, SHOW_ONLY, SHOW_ONLY.replace("'old'", "'new'"), newline=newline)
    assert ok, report
    assert "data unchanged" in report and "fingerprint not measured" not in report, report


def test_show_only_script_with_changed_data_is_still_caught(world):
    m, figs = world
    ok, report = _check(m, figs, SHOW_ONLY, SHOW_ONLY.replace("[1, 4, 9]", "[1, 4, 10]"))
    assert not ok and "data_changed" in report and "'plot'" in report, report


@pytest.mark.parametrize("change_data", [False, True])
def test_registered_savefig_edit_retains_source_output_identity(world, change_data):
    m, figs = world
    source = SHOW_ONLY.replace("plt.show()", "plt.savefig('plot.png')")
    edited = source.replace("'old'", "'new'")
    if change_data:
        edited = edited.replace("[1, 4, 9]", "[1, 4, 10]")
    ok, report = _check(m, figs, source, edited, register=True)
    assert ok is not change_data, report
    assert ("data_changed" if change_data else "data unchanged") in report, report


def test_relative_output_write_is_reported_as_not_measured_not_as_a_broken_script(world):
    m, figs = world
    ok, report = _check(m, figs, SHOW_ONLY, WRITES_CSV.replace("'old'", "'new'"))
    assert ok and "not measured" in report and "does not run" not in report, report
    assert not (figs / "data.csv").exists(), "绊线挡住了写入"


def test_stage_prefix_mirrors_the_convert_module():
    assert ai_figure_edit.STAGE_PREFIX == ai_convert.STAGE_PREFIX


def test_convert_validation_gives_the_write_hint_for_a_relative_output_script(world):
    """真 worker 的写入拒绝须保留候选隔离提示与具体写入，供模型修正后重试。"""
    m, figs = world
    script = figs / "plot.py"
    script.write_text(SHOW_ONLY, encoding="utf-8")
    original_bytes = script.read_bytes()
    ctx = next(iter(m.PROJECTS.values()))
    cand = ai_figure_edit.create_candidate("c1", script, figs, "plot.py")
    cand.new_bytes = WRITES_CSV.encode()
    try:
        res = ai_convert.run_validation(cand, m._convert_runner(ctx, "plot.py"))
    finally:
        ai_figure_edit.remove_candidate(cand.dir)
    assert res["status"] == "error" and res["code"] == ai_convert.WRITE_OUTSIDE_ROOTS_CODE, res
    hint, detail = res["message"].split("\nRejected write: ", 1)
    assert hint == ai_convert.WRITE_OUTSIDE_HINT
    assert json.loads(detail) == {"path": "data.csv", "event": "open"}
    assert script.read_bytes() == original_bytes
    assert not (figs / "data.csv").exists()


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
