"""TAF 改图（ADR 0138 Phase 3a）：开关打开时 Agent 在候选工作区里改副本，过了提交前检查才写回项目。

Agent CLI 换成一段 python（不联网、不装东西）：它在**自己的 cwd** 里改 `fig1.py`——开关关时 cwd 是项目目录（旧路径），
开时是候选目录。用例断言的是磁盘事实（项目里的脚本变没变、别的文件动没动）与历史库里的结局，不是日志。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto.engine import ai_bridge, ai_figure_edit, ai_history, ai_tasks, config, scriptlock

SCRIPT = """import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 4, 9])
    ax.set_title("old")
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""

#: 在 cwd 里把标题改掉（合法的改动）
EDIT = "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8')"
NOOP = "pass"
SYNTAX = "(d / 'fig1.py').write_text('def main(:\\n', encoding='utf-8')"
#: 修正轮：从原项目的脚本重新做一次正确的修改
FIX = "p = d / 'fig1.py'; p.write_text((proj / 'fig1.py').read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8')"
RENAME_STEM = "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('Fig1.pdf', 'Other.pdf'), encoding='utf-8')"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """隔离的快照 / 候选 / 历史库；Agent CLI 换成 `python -c <前言 + code>`，`d` 是它的 cwd，`proj` 是原项目。"""
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(os.environ))
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    (figs / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    state = {"code": EDIT, "taf": True, "cwd": [], "prompts": [], "figs": figs}
    monkeypatch.setattr(
        config, "ai_settings", lambda: {"taf": {"enabled": True}} if state["taf"] else {}
    )

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        state["cwd"].append(cwd)
        state["prompts"].append(prompt)
        pre = f"import pathlib, sys, time; d = pathlib.Path(sys.argv[1]); proj = pathlib.Path({str(figs)!r}); "
        code = state["codes"].pop(0) if state.get("codes") else state["code"]
        return [sys.executable, "-c", pre + code, cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    yield state
    ai_tasks.unregister(ai_figure_edit.TASK_TYPE)  # 生产注册表开关关着时为空（test_ai_tasks 钉着）


def _run(env, on_changed=None, events=None, task_hooks=None):
    calls = []

    def changed(script):
        calls.append(script)
        return {"registry": {}, "assets": {}, "published": []}

    sid = ai_bridge.run(
        "codex",
        "fig1.py",
        "把标题改成 new",
        str(env["figs"]),
        on_event=(lambda n, d: events.append((n, d))) if events is not None else None,
        on_changed=on_changed or changed,
        task_hooks=task_hooks,
    )
    return sid, calls


def _wait(sid, timeout=20.0):
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(timeout)
        assert not pump.is_alive(), "pump 线程没有收尾"
    return ai_bridge.SESSIONS[sid]


def _project_text(env) -> str:
    return (env["figs"] / "fig1.py").read_text(encoding="utf-8")


def test_flag_off_runs_the_old_path_in_the_project_directory(env):
    env["taf"] = False
    sid, calls = _run(env)
    sess = _wait(sid)
    assert env["cwd"] == [str(env["figs"])]
    assert "task" not in sess and "taf" not in sess
    assert sess["status"] == "done" and sess["changed"] is True
    assert '"new"' in _project_text(env)
    assert ai_history.get(sid)["task_type"] is None
    assert ai_figure_edit.TASK_TYPE not in ai_tasks.ADAPTERS


def test_flag_on_edits_a_copy_then_commits_it(env):
    events: list = []
    sid, calls = _run(env, events=events)
    sess = _wait(sid)
    cand_dir = Path(env["cwd"][0])
    assert cand_dir.parent == ai_figure_edit.CANDIDATE_DIR, "Agent 的 cwd 必须是候选目录"
    assert str(env["figs"]) in env["prompts"][0], "提示词告诉 Agent 原项目只读的位置"
    assert '"new"' in _project_text(env), "过了提交前检查的副本写回了项目"
    assert sess["status"] == "done" and sess["changed"] is True
    assert calls == ["fig1.py"], "提交后刷新恰好一次（不刷两遍）"
    assert not cand_dir.exists(), "候选目录收尾即删"
    # 渲染层验证没接（Phase 3a）：没量过就不说完成
    assert sess["task"]["outcome"] == "partial"
    assert [u["constraint_id"] for u in sess["task"]["unmet"]] == ["render.figure"]
    done = next(d for n, d in events if n == "ai.done")
    assert done["task"]["outcome"] == "partial"
    row = ai_history.get(sid)
    assert row["task_type"] == "figure_edit" and row["outcome"] == "partial"
    assert row["phase"] is None and row["status"] == "done"
    checks = {
        c["id"]: c["verdict"]
        for kind in row["verification"].values()
        if isinstance(kind, dict) and "checks" in kind
        for c in kind["checks"]
    }
    assert checks["candidate.syntax"] == "verified" and checks["candidate.stems"] == "verified"
    assert checks["project.untouched"] == "verified" and checks["render.figure"] == "unknown"
    # 回滚沿用 AI 桥的改前快照
    ai_bridge.revert(sid)
    assert _project_text(env) == SCRIPT


@pytest.mark.parametrize(
    "code, failed_check",
    [
        (
            EDIT + "; (proj / 'data.csv').write_text('poisoned', encoding='utf-8')",
            "project.untouched",
        ),
        (EDIT + "; (proj / 'stray.txt').write_text('x', encoding='utf-8')", "project.untouched"),
        (EDIT + "; (proj / 'fig1.py').write_text('# other', encoding='utf-8')", "project.target"),
        (SYNTAX, "candidate.syntax"),
        (RENAME_STEM, "candidate.stems"),
        (EDIT + "; (d / 'paper_style.py').write_text('x', encoding='utf-8')", None),
    ],
    ids=[
        "writes-project-data",
        "creates-project-file",
        "writes-project-target",
        "syntax",
        "stems",
        "new-helper-file",
    ],
)
def test_precommit_failures_never_reach_the_project(env, code, failed_check):
    env["code"] = code
    before_data = (env["figs"] / "data.csv").read_bytes()
    sid, calls = _run(env)
    sess = _wait(sid)
    row = ai_history.get(sid)
    if failed_check is None:
        # 候选里新建的辅助文件不会被提交，不算改动：照常提交
        assert sess["task"]["outcome"] == "partial" and '"new"' in _project_text(env)
        return
    assert row["outcome"] == "failed" and sess["status"] == "failed"
    assert failed_check in [u["constraint_id"] for u in sess["task"]["unmet"]]
    # 副本里能自己改回来的失败修正一次（同一份代码再跑仍是同样的失败 → 不再修）；原项目被动了的一律不修
    correctable = failed_check in ("candidate.syntax", "candidate.stems")
    assert len(env["prompts"]) == (2 if correctable else 1), env["prompts"]
    assert calls == [] or failed_check == "project.target", (
        "没提交就不刷新（除非项目里的脚本被别人改了）"
    )
    if failed_check != "project.target":
        assert _project_text(env) == SCRIPT, "提交前检查不过：项目里的脚本逐字节不变"
    else:
        assert _project_text(env) == "# other", "项目里被别人写的那一版不被候选覆盖"
    if "data.csv" in code:
        assert (env["figs"] / "data.csv").read_bytes() != before_data  # 兜底只能发现、不能阻止
        assert row["verification"]["persistence"]["checks"][-1]["observed"]["files"] == ["data.csv"]


def test_unchanged_copy_commits_nothing(env):
    env["code"] = NOOP
    sid, calls = _run(env)
    sess = _wait(sid)
    assert sess["changed"] is False and sess["status"] == "done" and calls == []
    assert _project_text(env) == SCRIPT
    assert sess["task"]["outcome"] == "completed"


def test_nonzero_exit_discards_the_copy(env):
    env["code"] = EDIT + "; sys.exit(3)"
    sid, _ = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "failed" and _project_text(env) == SCRIPT
    assert ai_history.get(sid)["error"] == "agent_exit_nonzero"


def test_begin_registered_failure_closes_the_registered_row_and_spawns_nothing(env, monkeypatch):
    """#1232：登记行已写、`begin_registered` 抛——行当场收成 failed（带真实原因），不 spawn，运行表里不留任务。"""
    ai_bridge.reset_spawn_mark()
    spawned = []
    real_popen = ai_bridge._popen
    monkeypatch.setattr(
        ai_bridge, "_popen", lambda *a, **k: spawned.append(a) or real_popen(*a, **k)
    )

    def boom(task_id, envelope, **kw):
        raise RuntimeError("begin boom")

    monkeypatch.setattr(ai_tasks, "begin_registered", boom)
    with pytest.raises(RuntimeError, match="begin boom"):
        _run(env)
    assert spawned == []
    assert ai_tasks._RUNNING == {}
    (row,) = ai_history.list_sessions(str(env["figs"].resolve()))["sessions"]
    assert row["status"] == "failed" and "begin boom" in row["error"] and row["ended_ms"]
    assert not ai_bridge.spawn_started()
    assert _project_text(env) == SCRIPT


def test_spawn_failure_after_begin_registered_leaves_no_running_task(env, monkeypatch):
    """`begin_registered` 已挂上、spawn 抛：运行表里的任务一并摘下（否则 cancel 永远对着一个不会跑的任务返回 True）。"""
    ai_bridge.reset_spawn_mark()

    def no_spawn(*a, **k):
        raise OSError("no spawn")

    monkeypatch.setattr(ai_bridge, "_popen", no_spawn)
    with pytest.raises(OSError):
        _run(env)
    assert ai_tasks._RUNNING == {}
    (row,) = ai_history.list_sessions(str(env["figs"].resolve()))["sessions"]
    assert row["status"] == "failed"


def test_cancel_while_running_commits_nothing_and_frees_the_script(env):
    env["code"] = EDIT + "; time.sleep(30)"
    sid, _ = _run(env)
    deadline = time.monotonic() + 10
    while '"new"' not in (Path(env["cwd"][0]) / "fig1.py").read_text(encoding="utf-8"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert ai_bridge.cancel(sid) is True
    sess = _wait(sid)
    assert sess["status"] == "cancelled" and _project_text(env) == SCRIPT
    assert ai_history.get(sid)["outcome"] == "cancelled"
    assert not ai_bridge.script_busy(env["figs"] / "fig1.py")


def test_cancel_after_commit_started_is_refused(env, monkeypatch):
    seen = []
    real = scriptlock.write_script

    def write_and_cancel(path, data, *, mode=None):
        seen.append(ai_bridge.cancel(sid_box[0]))  # 另一线程此刻点了取消
        real(path, data, mode=mode)

    sid_box: list = []
    monkeypatch.setattr(scriptlock, "write_script", write_and_cancel)
    env["code"] = EDIT + "; time.sleep(0.3)"
    sid, _ = _run(env)
    sid_box.append(sid)
    sess = _wait(sid)
    assert seen == [False], "进入提交后取消不被接受"
    assert sess["status"] == "done" and '"new"' in _project_text(env)


def test_candidate_is_a_copy_not_a_link(env):
    env["code"] = (
        "import os; p = d / 'fig1.py'; assert not os.path.islink(p); assert os.stat(p).st_ino != os.stat(proj / 'fig1.py').st_ino; "
        + EDIT
    )
    sid, _ = _run(env)
    assert _wait(sid)["status"] == "done"


def test_script_changed_between_precommit_and_commit_is_not_overwritten(env, monkeypatch):
    """提交前检查之后、提交之前别人写了项目里的脚本：提交时持锁再核一次基线，对不上就作废候选（不覆盖新内容）。"""
    real = ai_figure_edit.FigureEditAdapter._precommit

    def precommit_then_someone_writes(self, cand):
        out = real(self, cand)
        cand.script_path.write_text("# written meanwhile", encoding="utf-8")
        return out

    monkeypatch.setattr(
        ai_figure_edit.FigureEditAdapter, "_precommit", precommit_then_someone_writes
    )
    sid, _ = _run(env)
    sess = _wait(sid)
    assert _project_text(env) == "# written meanwhile"
    assert (
        sess["status"] == "failed" and ai_history.get(sid)["error"] == "script_changed_since_start"
    )


def _symlink_or_skip(target, link, *, is_dir):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("本机建不了符号链接（Windows 无权限）")


def test_swapped_directory_symlink_in_project_fails_untouched(env, tmp_path):
    """目录符号链接被换指向：`os.walk` 不进入它，指纹要把链接本身（目标）记下来。"""
    a, b = tmp_path / "out_a", tmp_path / "out_b"
    a.mkdir()
    b.mkdir()
    _symlink_or_skip(a, env["figs"] / "datalink", is_dir=True)
    env["code"] = (
        EDIT
        + f"; import os; l = proj / 'datalink'; os.unlink(l); os.symlink({str(b)!r}, l, target_is_directory=True)"
    )
    sid, _ = _run(env)
    sess = _wait(sid)
    row = ai_history.get(sid)
    assert row["outcome"] == "failed" and sess["status"] == "failed"
    check = row["verification"]["persistence"]["checks"][-1]
    assert check["id"] == "project.untouched" and "datalink" in check["observed"]["files"]
    assert _project_text(env) == SCRIPT, "提交前检查不过：项目里的脚本逐字节不变"


def test_retargeted_file_symlink_in_project_fails_untouched(env, tmp_path):
    """文件符号链接被改指向（lstat 的 size / mtime 可能碰巧相同）：目标写进指纹。"""
    if os.utime not in os.supports_follow_symlinks:
        pytest.skip("本平台不能不跟随链接地改 mtime（Windows），量不出「只有目标变」这一形状")
    a, b = tmp_path / "x_a.csv", tmp_path / "x_b.csv"
    a.write_text("1", encoding="utf-8")
    b.write_text("2", encoding="utf-8")
    _symlink_or_skip(a, env["figs"] / "filelink.csv", is_dir=False)
    env["code"] = (
        EDIT
        + f"; import os; l = proj / 'filelink.csv'; st = os.lstat(l); os.unlink(l); os.symlink({str(b)!r}, l); os.utime(l, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)"
    )
    sid, _ = _run(env)
    sess = _wait(sid)
    row = ai_history.get(sid)
    assert row["outcome"] == "failed" and sess["status"] == "failed"
    check = row["verification"]["persistence"]["checks"][-1]
    assert check["id"] == "project.untouched" and "filelink.csv" in check["observed"]["files"]
    assert _project_text(env) == SCRIPT


def test_interrupt_all_leaves_a_committing_task_alone(env, monkeypatch):
    """进入提交后 `interrupt_all` 不得把会话标成 interrupted：任务照常写完，终态按真实结局收尾。"""
    seen = []
    real = scriptlock.write_script

    def write_and_interrupt(path, data, *, mode=None):
        seen.append(ai_bridge.interrupt_all())
        real(path, data, mode=mode)

    monkeypatch.setattr(scriptlock, "write_script", write_and_interrupt)
    env["code"] = EDIT + "; time.sleep(0.3)"
    sid, _ = _run(env)
    sess = _wait(sid)
    assert seen == [0], "提交中的会话不计入被中断数"
    assert sess["status"] == "done" and '"new"' in _project_text(env)
    assert ai_history.get(sid)["status"] == "done"


def test_cancel_wins_even_if_the_killed_process_is_seen_before_the_status_flips(env, monkeypatch):
    """`cancel` 先挂取消、再杀进程、最后才改会话状态：泵线程可能先看到被杀进程的非零退出码（Windows 上常见）。
    结局仍须是 cancelled，不能当成「Agent 运行失败」。"""
    real = ai_bridge._terminate

    def slow_terminate(sess):
        real(sess)
        time.sleep(1.0)  # 进程已死、会话还没改成 cancelled 的那个窗口

    monkeypatch.setattr(ai_bridge, "_terminate", slow_terminate)
    env["code"] = EDIT + "; time.sleep(30)"
    sid, _ = _run(env)
    deadline = time.monotonic() + 10
    while '"new"' not in (Path(env["cwd"][0]) / "fig1.py").read_text(encoding="utf-8"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert ai_bridge.cancel(sid) is True
    sess = _wait(sid)
    assert sess["status"] == "cancelled" and _project_text(env) == SCRIPT
    assert ai_history.get(sid)["outcome"] == "cancelled"


# --------------------------------------------------------------------------- 渲染层验证（Phase 3b，钩子由 app 注入）
def _fp(sha):
    return {"status": "ok", "parts": {"xy": {"sha256": sha, "shape": [3, 2]}}}


def _manifest(sha):
    return {
        "elements": [
            {"gid": "figure", "role": "figure", "editable": []},
            {"gid": "axes_0.lines_0", "role": "line", "editable": [], "data_fingerprint": _fp(sha)},
        ]
    }


expecteds: list = []


def _hooks(env, before, after, log):
    def b():
        log.append(("before", _project_text(env)))
        return before

    def a(expected):
        log.append(("after", _project_text(env)))
        expecteds.append(expected)
        if isinstance(after, Exception):
            raise after
        return after

    return {"before": b, "after": a}


def _render_checks(sid):
    row = ai_history.get(sid)
    return {
        c["id"]: (c["verdict"], c["reason_code"]) for c in row["verification"]["figure"]["checks"]
    }


def test_data_preserved_after_render_completes(env):
    log: list = []
    sid, _ = _run(
        env,
        task_hooks=_hooks(env, {"Fig1": _manifest("a" * 64)}, {"Fig1": _manifest("a" * 64)}, log),
    )
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed", sess["task"]
    checks = _render_checks(sid)
    assert checks["render.figure"] == ("verified", None)
    assert checks["Fig1.data.axes_0.lines_0"] == ("verified", None)
    # 改前取在写之前、改后取在写之后
    assert log[0] == ("before", SCRIPT) and log[1][0] == "after" and '"new"' in log[1][1]


def test_changed_data_after_render_fails_and_the_commit_stands(env):
    log: list = []
    sid, _ = _run(
        env,
        task_hooks=_hooks(env, {"Fig1": _manifest("a" * 64)}, {"Fig1": _manifest("b" * 64)}, log),
    )
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed" and sess["status"] == "done"
    assert '"new"' in _project_text(env), "已提交的改动不自动回滚（回滚本身也是一次写，由用户点）"
    assert _render_checks(sid)["Fig1.data.axes_0.lines_0"] == ("failed", "data_changed")


def test_no_baseline_means_no_extra_render(env):
    log: list = []
    sid, _ = _run(env, task_hooks=_hooks(env, None, {"Fig1": _manifest("a" * 64)}, log))
    sess = _wait(sid)
    assert [k for k, _ in log] == ["before"], "没有改前基线就不做改后渲染（不为验证多跑一次脚本）"
    assert sess["task"]["outcome"] == "partial"
    assert _render_checks(sid)["render.figure"] == ("unknown", "no_baseline")


def test_render_failure_after_commit_is_failed(env):
    class Boom(RuntimeError):
        code = "script_error"

    log: list = []
    sid, _ = _run(env, task_hooks=_hooks(env, {"Fig1": _manifest("a" * 64)}, Boom("x"), log))
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert _render_checks(sid)["render.figure"] == ("failed", "render_failed")


def test_missing_stem_after_render_is_failed(env):
    log: list = []
    sid, _ = _run(env, task_hooks=_hooks(env, {"Fig1": _manifest("a" * 64)}, {}, log))
    _wait(sid)
    assert _render_checks(sid)["render.Fig1"] == ("failed", "stem_not_rendered")


def test_unchanged_copy_never_renders(env):
    env["code"] = NOOP
    log: list = []
    sid, _ = _run(
        env,
        task_hooks=_hooks(env, {"Fig1": _manifest("a" * 64)}, {"Fig1": _manifest("a" * 64)}, log),
    )
    _wait(sid)
    assert log == [], "没改动：不取改前、不渲染"


def test_retire_runs_after_the_write_and_before_refresh_and_after(env):
    log: list = []
    hooks = _hooks(env, {"Fig1": _manifest("a" * 64)}, {"Fig1": _manifest("a" * 64)}, log)
    hooks["retire"] = lambda: log.append(("retire", _project_text(env)))

    def changed(script):
        log.append(("refresh", _project_text(env)))
        return {"registry": {}, "assets": {}, "published": []}

    sid, _ = _run(env, on_changed=changed, task_hooks=hooks)
    sess = _wait(sid)
    assert [k for k, _ in log] == ["before", "retire", "refresh", "after"], log
    assert log[0][1] == SCRIPT and all('"new"' in t for _, t in log[1:]), (
        "retire 时项目脚本已是新内容"
    )
    assert sess["task"]["outcome"] == "completed", sess["task"]


def test_unconfirmed_retire_is_unknown_and_never_renders_after(env):
    log: list = []
    hooks = _hooks(env, {"Fig1": _manifest("a" * 64)}, {"Fig1": _manifest("a" * 64)}, log)

    def boom():
        raise RuntimeError("无法确认旧渲染会话已关闭")

    hooks["retire"] = boom
    sid, _ = _run(env, task_hooks=hooks)
    sess = _wait(sid)
    assert [k for k, _ in log] == ["before"], "旧会话没退役：after 会被去重回旧会话，不能调"
    assert _render_checks(sid)["render.figure"] == ("unknown", "session_not_retired")
    assert sess["status"] == "done" and '"new"' in _project_text(env), "提交不因旁路失败而失败"
    assert sess["task"]["outcome"] == "partial"


def test_stale_session_is_unknown_not_failed_nor_verified(env):
    from tavotto.engine.ai_figure_edit import StaleSession

    log: list = []
    sid, _ = _run(
        env,
        task_hooks=_hooks(env, {"Fig1": _manifest("a" * 64)}, StaleSession("old session"), log),
    )
    sess = _wait(sid)
    assert _render_checks(sid)["render.figure"] == ("unknown", "stale_session")
    assert sess["task"]["outcome"] == "partial", sess["task"]
    assert sess["status"] == "done" and '"new"' in _project_text(env)


def test_after_receives_the_sha256_of_the_committed_bytes(env):
    import hashlib

    expecteds.clear()
    log: list = []
    sid, _ = _run(
        env,
        task_hooks=_hooks(env, {"Fig1": _manifest("a" * 64)}, {"Fig1": _manifest("a" * 64)}, log),
    )
    _wait(sid)
    disk = (Path(env["figs"]) / "fig1.py").read_bytes()
    assert expecteds == [hashlib.sha256(disk).hexdigest()]


# --------------------------------------------------------------------------- 有界修正（Phase 4）
def test_a_syntax_error_is_corrected_once_in_the_same_copy_then_committed(env):
    env["codes"] = [SYNTAX, FIX]
    sid, calls = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "done" and '"new"' in _project_text(env)
    assert calls == ["fig1.py"], "只在最终提交时刷新一次"
    assert env["cwd"][0] == env["cwd"][1] != str(env["figs"]), "修正在同一个候选目录里"
    first, second = env["prompts"]
    assert second.startswith(first), "修正轮带着原提示词"
    assert "第 1 行有语法错误" in second and "不要运行脚本" in second
    row = ai_history.get(sid)
    assert row["budget_used"]["agent_runs"] == 2 and row["budget_used"]["corrections"] == 1
    assert row["verification"]["attempts"] == [
        {"round": 1, "failed": [["candidate.syntax", "syntax_error"]], "next": "correct"}
    ]
    assert sess["task"]["outcome"] == "partial"  # 没接渲染钩子：render_not_wired 是 unknown
    assert not ai_bridge.script_busy(env["figs"] / "fig1.py")


@pytest.mark.parametrize(
    "first",
    [
        EDIT + "; (proj / 'stray.txt').write_text('x', encoding='utf-8')",
        # 与可修的失败混在一起也不修：只要原项目被动了，整轮不可修
        SYNTAX + "; (proj / 'stray.txt').write_text('x', encoding='utf-8')",
        # 候选里的目标脚本被删（本身可修）+ 动了原项目：同样整轮不可修
        "(d / 'fig1.py').unlink(); (proj / 'stray.txt').write_text('x', encoding='utf-8')",
    ],
    ids=["project-only", "project-and-syntax", "project-and-missing-target"],
)
def test_touching_the_original_project_is_never_corrected(env, first):
    env["codes"] = [first, FIX]
    sid, _ = _run(env)
    sess = _wait(sid)
    assert len(env["prompts"]) == 1 and sess["status"] == "failed"
    assert _project_text(env) == SCRIPT
    assert ai_history.get(sid)["verification"]["attempts"][0]["next"] == "not_correctable"


def test_cancel_during_the_correction_run_commits_nothing_and_frees_the_script(env):
    env["codes"] = [SYNTAX, FIX + "; time.sleep(30)"]
    sid, _ = _run(env)
    deadline = time.monotonic() + 15
    while len(env["prompts"]) < 2 or '"new"' not in (Path(env["cwd"][0]) / "fig1.py").read_text(
        encoding="utf-8"
    ):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert ai_history.get(sid)["phase"] == "correcting"
    assert ai_bridge.script_busy(env["figs"] / "fig1.py"), "修正轮的进程还活着：脚本算忙"
    assert ai_bridge.cancel(sid) is True
    sess = _wait(sid)
    assert sess["status"] == "cancelled" and _project_text(env) == SCRIPT
    assert ai_history.get(sid)["outcome"] == "cancelled"
    assert not ai_bridge.script_busy(env["figs"] / "fig1.py")


def test_a_failing_correction_run_discards_the_copy(env):
    env["codes"] = [SYNTAX, FIX + "; sys.exit(3)"]
    sid, _ = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "failed" and _project_text(env) == SCRIPT
    assert ai_history.get(sid)["error"] == "agent_exit_nonzero"


def test_after_cancelling_the_correction_run_the_script_stays_busy_until_that_process_exits(
    env, monkeypatch
):
    """首轮进程退出时 `exited` 已置位；修正轮必须先清掉它——否则取消后 status 一变，第二个进程还活着脚本就被判成空闲。"""
    real = ai_bridge._await_exit
    calls, gate = [], threading.Event()

    def held(sess):
        calls.append(sess["proc"].pid)
        if len(calls) == 2:
            gate.wait(10)  # 修正轮进程的退出确认被扣住
        real(sess)

    monkeypatch.setattr(ai_bridge, "_await_exit", held)
    env["codes"] = [SYNTAX, FIX + "; time.sleep(30)"]
    sid, _ = _run(env)
    deadline = time.monotonic() + 15
    while ai_history.get(sid)["phase"] != "correcting" or len(env["prompts"]) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert ai_bridge.cancel(sid) is True
    while len(calls) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert ai_bridge.script_busy(env["figs"] / "fig1.py"), "修正轮的退出还没确认：脚本仍算忙"
    gate.set()
    _wait(sid)
    assert not ai_bridge.script_busy(env["figs"] / "fig1.py")


def test_correction_is_announced_and_the_final_summary_counts_it(env):
    """界面要能说「正在自动修正」（`ai.task`）与「自动修正过一次」（`ai.done.task.corrections`）；终态不留进行中的阶段。"""
    env["codes"] = [SYNTAX, FIX]
    events: list = []
    sid, _ = _run(env, events=events)
    sess = _wait(sid)
    names = [n for n, _ in events]
    assert (
        "ai.task",
        {"session": sid, "task_type": ai_figure_edit.TASK_TYPE, "phase": "correcting"},
    ) in events
    assert names.index("ai.task") < names.index("ai.done")
    done = next(d for n, d in events if n == "ai.done")
    assert done["task"]["corrections"] == 1 and "phase" not in done["task"]
    assert sess["task"]["corrections"] == 1 and "phase" not in sess["task"]


def test_without_a_correction_no_phase_event_is_sent(env):
    events: list = []
    sid, _ = _run(env, events=events)
    _wait(sid)
    assert "ai.task" not in [n for n, _ in events]
    done = next(d for n, d in events if n == "ai.done")
    assert done["task"]["corrections"] == 0


def test_the_phase_event_is_a_kind_the_frontend_listens_for():
    api_ts = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "api.ts"
    if not api_ts.is_file():
        pytest.skip("没有 web/（wheel/sdist 里不含前端源码）")
    source = api_ts.read_text(encoding="utf-8")
    block = source[source.index("const EVENT_KINDS = [") :]
    assert "'ai.task'" in block[: block.index("] as const")]


def test_a_stem_whose_baseline_replay_failed_is_unknown_not_silently_dropped(env):
    """多 stem 脚本里一个 stem 的改前重放失败（基线值为 None）：它必须显式 unknown，整体不是 completed。"""
    log: list = []
    sid, _ = _run(
        env,
        task_hooks=_hooks(
            env,
            {"Fig1": _manifest("a" * 64), "Fig2": None},
            {"Fig1": _manifest("a" * 64), "Fig2": _manifest("a" * 64)},
            log,
        ),
    )
    sess = _wait(sid)
    checks = _render_checks(sid)
    assert checks["render.Fig2"] == ("unknown", "no_baseline")
    assert checks["Fig1.data.axes_0.lines_0"] == ("verified", None), "有基线的 stem 照常比较"
    assert sess["task"]["outcome"] == "partial", sess["task"]


# --------------------------------------------------------------------------- 指纹不含 Python 环境目录（#1330 P1）
def _pfp(root: Path) -> dict:
    return ai_figure_edit.project_fingerprint(root)


def test_fingerprint_ignores_venv_dirs_marked_by_pyvenv_cfg(tmp_path):
    (tmp_path / "fig1.py").write_text("x = 1\n", encoding="utf-8")
    venv = tmp_path / ".venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
    before = _pfp(tmp_path)
    assert not any(k.startswith(".venv") for k in before["files"])
    (venv / "lib" / "new.py").write_text("n\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = /y\n", encoding="utf-8")
    assert ai_figure_edit.fingerprint_changes(before, _pfp(tmp_path)) == []


def test_fingerprint_still_counts_a_dot_venv_dir_without_the_marker(tmp_path):
    fake = tmp_path / ".venv"
    fake.mkdir()
    (fake / "a.txt").write_text("1\n", encoding="utf-8")
    before = _pfp(tmp_path)
    (fake / "a.txt").write_text("2\n", encoding="utf-8")
    assert ai_figure_edit.fingerprint_changes(before, _pfp(tmp_path)) == [".venv/a.txt"]


def test_fingerprint_ignores_conda_env_dirs_but_not_lookalikes(tmp_path):
    env = tmp_path / "envs" / "myenv"
    (env / "conda-meta").mkdir(parents=True)
    (env / "lib").mkdir()
    other = tmp_path / "envs" / "plain"
    other.mkdir()
    (other / "x.txt").write_text("1\n", encoding="utf-8")
    before = _pfp(tmp_path)
    (env / "lib" / "pkg.py").write_text("p\n", encoding="utf-8")
    (env / "conda-meta" / "pkg.json").write_text("{}", encoding="utf-8")
    assert ai_figure_edit.fingerprint_changes(before, _pfp(tmp_path)) == []
    (other / "x.txt").write_text("2\n", encoding="utf-8")
    assert ai_figure_edit.fingerprint_changes(before, _pfp(tmp_path)) == ["envs/plain/x.txt"]


def test_fingerprint_env_dirs_do_not_count_toward_the_file_budget(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("", encoding="utf-8")
    for i in range(5):
        (venv / f"f{i}.py").write_text("", encoding="utf-8")
    (tmp_path / "fig1.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(ai_figure_edit, "FINGERPRINT_MAX_FILES", 2)
    assert _pfp(tmp_path)["status"] == "ok"
