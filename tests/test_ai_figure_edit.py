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
        return [sys.executable, "-c", pre + state["code"], cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    yield state
    ai_tasks.unregister(ai_figure_edit.TASK_TYPE)  # 生产注册表开关关着时为空（test_ai_tasks 钉着）


def _run(env, on_changed=None, events=None):
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
