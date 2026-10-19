"""TAF 改图的渲染层验证（ADR 0138 Phase 3b），走真 app 与真 safe worker：`/api/engine/render` 先渲染一次（改前基线），
开关打开后 `/api/ai/run`，Agent（假 CLI）在候选里改副本 → 提交 → 改后渲染 → 数据指纹对比。

脚本每执行一次往项目**外**的计数文件追加一行：钉住「验证不多跑一次用户脚本」——改后那次 build 就是前端随后要做的
那次重渲染，前端来时会话已经 built。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_bridge,
    ai_figure_edit,
    ai_history,
    ai_tasks,
    config,
    pool,
    project_watch,
)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib.pyplot as plt

RUNS = {runs!r}


def main():
    with open(RUNS, "a", encoding="utf-8") as fh:
        fh.write("x\\n")
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0.0, 1.0, 4.0, 9.0], label="signal")
    ax.set_title("old")
    fig.savefig("Fig1.pdf")
"""

REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
    }
)

RETITLE = "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8')"
REDATA = "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('4.0, 9.0', '40.0, 90.0'), encoding='utf-8')"


@pytest.fixture
def project(tmp_path, monkeypatch):
    import pikepdf

    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(os.environ))
    monkeypatch.setattr(config, "ai_settings", lambda: {"taf": {"enabled": True}})
    state = {"code": RETITLE}

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        pre = "import pathlib, sys; d = pathlib.Path(sys.argv[1]); "
        return [sys.executable, "-c", pre + state["code"], cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    runs = tmp_path / "runs.txt"
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT.format(runs=str(runs)), encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / "Fig1.pdf")
    m.open_project(str(figs))
    try:
        yield m, m.app.test_client(), figs, runs, state
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()
        ai_tasks.unregister(ai_figure_edit.TASK_TYPE)


def _render(client):
    r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": []})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["manifest"]


def _runs(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _ai(client):
    r = client.post(
        "/api/ai/run", json={"agent": "codex", "prompt": "把标题改成 new", "id": "Fig1.pdf"}
    )
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["session"]
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(120)
        assert not pump.is_alive(), "pump 线程没有收尾"
    return sid, ai_bridge.SESSIONS[sid]


def _checks(sid):
    row = ai_history.get(sid)
    return {
        c["id"]: (c["verdict"], c["reason_code"]) for c in row["verification"]["figure"]["checks"]
    }


def test_style_edit_is_verified_against_the_real_render_without_an_extra_run(project):
    m, client, figs, runs, state = project
    _render(client)
    assert _runs(runs) == 1
    sid, sess = _ai(client)
    assert sess["status"] == "done" and '"new"' in (figs / "fig1.py").read_text(encoding="utf-8")
    checks = _checks(sid)
    assert checks["render.figure"] == ("verified", None), checks
    data = {k: v for k, v in checks.items() if k.startswith("Fig1.data.")}
    assert data and all(v == ("verified", None) for v in data.values()), checks
    assert sess["task"]["outcome"] == "completed", sess["task"]
    assert _runs(runs) == 2, "改后验证那次 build 就是重渲染本身"
    # 前端收到 panel.file_changed 后的重渲染：会话已 built，不再执行脚本
    man = _render(client)
    assert _runs(runs) == 2
    assert any(f.get("value") == "new" for el in man["elements"] for f in el.get("editable", []))


def test_data_edit_fails_the_preserve_check(project):
    m, client, figs, runs, state = project
    state["code"] = REDATA
    _render(client)
    sid, sess = _ai(client)
    assert sess["task"]["outcome"] == "failed", sess["task"]
    assert "Fig1.data.axes_0.lines_0" in [u["constraint_id"] for u in sess["task"]["unmet"]]
    assert _checks(sid)["Fig1.data.axes_0.lines_0"] == ("failed", "data_changed")
    assert sess["status"] == "done", "改动已经提交；结局 failed 只说明验证没过，回滚由用户点"


def test_without_a_prior_render_nothing_extra_runs(project):
    m, client, figs, runs, state = project
    sid, sess = _ai(client)
    assert _runs(runs) == 0, "没有改前基线：不为验证执行脚本"
    assert _checks(sid)["render.figure"] == ("unknown", "no_baseline")
    assert sess["task"]["outcome"] == "partial"


def test_session_built_from_an_older_script_is_not_a_baseline(project):
    m, client, figs, runs, state = project
    _render(client)
    assert _runs(runs) == 1
    # 用户刚编辑了脚本、watcher 还没来得及作废那条会话：停掉 watcher 把这个窗口固定住
    project_watch.stop(str(figs))
    p = figs / "fig1.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n# edited by the user\n", encoding="utf-8")
    sid, sess = _ai(client)
    assert sess["status"] == "done"
    assert _checks(sid)["render.figure"] == ("unknown", "no_baseline")
    assert _runs(runs) == 1, "旧会话不是基线，也不为验证多跑一次脚本"


def test_ai_commit_retires_old_sessions_synchronously_before_the_refresh(project, monkeypatch):
    m, client, figs, runs, state = project
    _render(client)
    events: list = []
    real_invalidate, real_refresh = pool.invalidate, m.refresh_project

    def invalidate(script, figures_dir=None, run=None, **kw):
        events.append(
            (
                "invalidate",
                script,
                bool(kw.get("force")),
                '"new"' in (figs / script).read_text(encoding="utf-8"),
            )
        )
        return real_invalidate(script, figures_dir, run, **kw)

    def refresh(*a, **kw):
        events.append(("refresh",))
        return real_refresh(*a, **kw)

    monkeypatch.setattr(pool, "invalidate", invalidate)
    monkeypatch.setattr(m, "refresh_project", refresh)
    _ai(client)
    first_refresh = events.index(("refresh",))
    forced = [e for e in events[:first_refresh] if e[:3] == ("invalidate", "fig1.py", True)]
    assert forced and forced[0][3] is True, events  # 刷新之前、写入之后、force=True（同步确认退役）


def test_launch_sampled_sha_that_is_not_what_ran_is_no_baseline(project):
    """A→B→A：会话启动时采样的是 A，build 实际执行的是别的版本。判据是 worker 自报执行的 sha256。"""
    m, client, figs, runs, state = project
    _render(client)
    worker = pool.peek("fig1.py", str(figs))
    assert worker is not None and worker.built
    import hashlib

    assert (
        worker.last_build_source_sha256
        == hashlib.sha256((figs / "fig1.py").read_bytes()).hexdigest()
    )
    worker.last_build_source_sha256 = "0" * 64  # 模拟「采样 A、实际执行 B」
    sid, sess = _ai(client)
    assert _checks(sid)["render.figure"] == ("unknown", "no_baseline")
    assert _runs(runs) == 1


def test_after_session_running_an_older_version_is_stale_not_verified(project, monkeypatch):
    """P1：旧会话没退役（watcher 先做了异步 invalidate / 去重回旧会话）——after 拿到的会话执行的是旧版。"""
    m, client, figs, runs, state = project
    _render(client)
    from tavotto.engine import enginesession

    real_resolve = enginesession.resolve

    def resolve(**kw):
        w = real_resolve(**kw)
        w.last_build_source_sha256 = "1" * 64  # 会话自报执行的是旧版本
        w.built = True
        return w

    monkeypatch.setattr(enginesession, "resolve", resolve)
    sid, sess = _ai(client)
    assert _checks(sid)["render.figure"] == ("unknown", "stale_session")
    assert sess["task"]["outcome"] == "partial", sess["task"]
