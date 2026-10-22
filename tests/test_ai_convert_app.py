"""「生成可编辑版本」端到端（ADR 0143），走真 app 与真 safe worker：`POST /api/ai/convert` → Agent（假 CLI）在候选里新建
Matplotlib 版脚本 → 后端在用户同一路由下真的跑一遍（probe）→ 对照数据 / 预览 → `adopt` 新建进项目。
原脚本字节全程不变；校验用的临时文件跑完即删。"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_bridge,
    ai_convert,
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

ORIGINAL = "# 假装是 Pillow 脚本\nopen('out.png', 'wb').write(b'x')\n"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x50\x00\x00\x00\x28\x08\x02\x00\x00\x00"
    + b"\x00" * 16
)
GOOD = (
    "import matplotlib.pyplot as plt\n\n\ndef main():\n"
    "    fig, ax = plt.subplots(figsize=(3.2, 1.6))\n"
    "    ax.plot([0, 1, 2], [0, 1, 4])\n"
    "    fig.savefig('out.png')\n"
)
WRITE_GOOD = f"(d / 'fig_matplotlib.py').write_text({GOOD!r}, encoding='utf-8')"
WRITE_NO_FIGURE = "(d / 'fig_matplotlib.py').write_text('import matplotlib\\n\\n\\ndef main():\\n    pass\\n', encoding='utf-8')"


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "convert")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(os.environ))
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    state = {"code": WRITE_GOOD}

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        pre = "import pathlib, sys; d = pathlib.Path(sys.argv[1]); "
        return [sys.executable, "-c", pre + state["code"], cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig.py").write_bytes(ORIGINAL.encode())  # 按字节写：Windows 文本模式会换成 CRLF
    (figs / "out.png").write_bytes(PNG)
    m.open_project(str(figs))
    state["initial"] = sorted(
        p.name for p in figs.iterdir() if p.is_file()
    )  # open_project 会写 tavotto_registry.json
    try:
        yield m, m.app.test_client(), figs, state
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()
        ai_tasks.unregister(ai_convert.TASK_TYPE)


def _wait_final(client, sid, timeout=90.0):
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(timeout)
        assert not pump.is_alive()
    end = time.time() + 5
    while time.time() < end:
        got = client.get(f"/api/ai/sessions/{sid}").get_json()
        if got.get("final"):
            return got
        time.sleep(0.1)
    raise AssertionError("会话没有收尾")


def _start(client, **over):
    body = {"script": "fig.py", "library": "pillow", "path": "out.png", "agent": "codex", **over}
    return client.post("/api/ai/convert", json=body)


def test_convert_runs_validates_compares_and_adopts_without_touching_the_original(project):
    m, client, figs, state = project
    before = (figs / "fig.py").read_bytes()
    r = _start(client)
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["session"]
    sess = _wait_final(client, sid)
    assert sess["status"] == "done" and sess["task"]["outcome"] == "completed", sess.get("task")
    st = client.get(f"/api/ai/convert/{sid}").get_json()
    assert st["state"] == "ready" and st["has_preview"] is True
    assert st["comparison"]["original"]["width_px"] == 80
    assert st["comparison"]["candidate"]["figures"] == 1
    assert (
        st["comparison"]["candidate"]["width_mm"] and st["comparison"]["aspect_delta"] is not None
    )
    pv = client.get(f"/api/ai/convert/{sid}/preview.svg")
    assert pv.status_code == 200 and pv.mimetype == "image/svg+xml"
    assert "sandbox" in pv.headers["Content-Security-Policy"]
    assert b"<svg" in pv.data
    # 采用前项目里什么都没多：校验用的临时文件已删
    assert sorted(p.name for p in figs.iterdir() if p.is_file()) == state["initial"]
    assert not any(p.name.startswith(ai_convert.STAGE_PREFIX) for p in figs.iterdir())
    a = client.post(f"/api/ai/convert/{sid}/adopt")
    assert a.status_code == 200, a.get_json()
    body = a.get_json()
    assert body["script"] == "fig_matplotlib.py" and body["refresh"]["status"] in (
        "ok",
        "not_wired",
    )
    assert (figs / "fig_matplotlib.py").read_text(encoding="utf-8") == GOOD
    assert (figs / "fig.py").read_bytes() == before
    again = client.post(f"/api/ai/convert/{sid}/adopt")
    assert again.status_code == 200 and again.get_json()["script"] == "fig_matplotlib.py"


def test_r_script_converts_end_to_end_without_running_r_and_reports_the_install_step(
    project, monkeypatch
):
    """R 来源（ADR 0143 修订）：真 safe worker 跑 Agent 写的 Python 候选；R 脚本字节不变；依赖钩子（自动安装）被调用且结果进对照数据。
    安装入口被替换（不装任何真包）。"""
    from tavotto.engine import convertdeps

    m, client, figs, state = project
    (figs / "fig.R").write_bytes(b'df <- read.csv("data.csv")\nplot(df)\n')
    seen = []

    def fake_ensure(proj, rel, cancel=None):
        seen.append((proj, rel))
        return {"status": "installed", "installed": ["pandas"]}

    monkeypatch.setattr(convertdeps, "ensure", fake_ensure)
    r = client.post(
        "/api/ai/convert",
        json={"script": "fig.R", "source_language": "r", "path": "out.png", "agent": "codex"},
    )
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["session"]
    sess = _wait_final(client, sid)
    assert sess["task"]["outcome"] == "completed", sess.get("task")
    st = client.get(f"/api/ai/convert/{sid}").get_json()
    assert st["source_language"] == "r" and st["library"] is None
    assert st["installed"] == ["pandas"]
    assert st["comparison"]["verification"] == "side_by_side"
    assert seen and seen[0][1].startswith(ai_convert.STAGE_PREFIX)
    assert (figs / "fig.R").read_bytes() == b'df <- read.csv("data.csv")\nplot(df)\n'
    a = client.post(f"/api/ai/convert/{sid}/adopt")
    assert a.status_code == 200 and a.get_json()["script"] == "fig_matplotlib.py"


def test_a_script_that_captures_no_figure_fails_with_a_reason(project):
    m, client, figs, state = project
    state["code"] = WRITE_NO_FIGURE
    sid = _start(client).get_json()["session"]
    sess = _wait_final(client, sid)
    assert sess["task"]["outcome"] == "failed"
    assert any(u["reason_code"] == "no_matplotlib_figure" for u in sess["task"]["unmet"])
    assert client.get(f"/api/ai/convert/{sid}").status_code in (404, 409)
    assert sorted(p.name for p in figs.iterdir() if p.is_file()) == state["initial"]


def test_candidate_that_writes_a_project_file_is_stopped_by_the_write_tripwire(
    project, monkeypatch
):
    """P1（Codex #1277）：真 worker——候选在校验运行里写用户的 `out.png`（绝对路径，与 cwd 无关）在写发生时就失败，字节不变。"""
    m, client, figs, state = project
    # pytest 的 tmp_path 在系统临时目录之下，而绊线白名单放行系统临时目录：把 worker 的临时目录挪走（同 test_mcp_write_tripwire）
    worker_tmp = figs.parent / "worker-tmp"
    worker_tmp.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(worker_tmp))
    monkeypatch.setenv("LOCALAPPDATA", str(figs.parent / "localappdata"))
    before = (figs / "out.png").read_bytes()
    bad = (
        f"import pathlib\npathlib.Path({str(figs / 'out.png')!r}).write_bytes(b'clobbered')\n"
        + GOOD
    )
    state["code"] = f"(d / 'fig_matplotlib.py').write_text({bad!r}, encoding='utf-8')"
    sid = _start(client).get_json()["session"]
    sess = _wait_final(client, sid)
    assert sess["task"]["outcome"] == "failed"
    assert (figs / "out.png").read_bytes() == before
    assert any(u["reason_code"] == "run_failed" for u in sess["task"]["unmet"])
    assert sorted(p.name for p in figs.iterdir() if p.is_file()) == state["initial"]


def test_discard_endpoint_removes_the_candidate(project):
    m, client, figs, state = project
    sid = _start(client).get_json()["session"]
    _wait_final(client, sid)
    assert client.post(f"/api/ai/convert/{sid}/discard").get_json() == {"ok": True}
    assert client.get(f"/api/ai/convert/{sid}").status_code == 404
    assert not (figs / "fig_matplotlib.py").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"script": "../outside.py", "library": "pillow"},
        {"script": "fig.py", "library": "not-a-lib"},
        {"script": "fig.txt", "library": "pillow"},
        {"script": 3, "library": "pillow"},
    ],
)
def test_bad_requests_are_rejected_before_anything_runs(project, body):
    m, client, figs, state = project
    r = client.post("/api/ai/convert", json={"agent": "codex", **body})
    assert r.status_code in (400, 404)
    assert not ai_convert.CONVERT_DIR.exists() or not list(ai_convert.CONVERT_DIR.iterdir())


def test_the_master_switch_turns_the_endpoint_off(project, monkeypatch):
    m, client, figs, state = project
    monkeypatch.setattr(config, "ai_settings", lambda: {"convert": {"enabled": False}})
    r = _start(client)
    assert r.status_code == 404 and r.get_json()["code"] == "convert_disabled"
    assert Path(figs / "fig.py").read_text(encoding="utf-8") == ORIGINAL


def test_replan_endpoint_rechecks_the_same_candidate_with_the_current_environment(
    project, monkeypatch
):
    """系统 Python 卡 → 用户换了环境 → `POST …/dependencies {action: replan}`：不带摘要、不重跑 Agent，按此刻环境（`convertdeps.ensure`）
    重新判定同一个候选；再换回仍是系统 Python 就仍回系统卡；非法动作 400。"""
    from tavotto.engine import convertdeps

    m, client, figs, state = project
    (figs / "fig.R").write_bytes(b'df <- read.csv("data.csv")\nplot(df)\n')
    system_card = {
        "kind": "system",
        "packages": ["pandas"],
        "outside_list": [],
        "env": {"kind": "system", "name": "Python"},
        "action": "choose_env",
        "impact_digest": "",
    }
    answers = [
        {"status": "hold", "card": system_card, "missing": ["pandas"]},  # 任务内：系统 Python
        {
            "status": "hold",
            "card": system_card,
            "missing": ["pandas"],
        },  # 换环境前点了 replan：仍是系统
        {"status": "installed", "installed": ["pandas"]},  # 换到项目 venv 后 replan
    ]
    seen = []

    def fake_ensure(proj, rel, cancel=None):
        seen.append(rel)
        return answers.pop(0)

    monkeypatch.setattr(convertdeps, "ensure", fake_ensure)
    r = client.post(
        "/api/ai/convert",
        json={"script": "fig.R", "source_language": "r", "path": "out.png", "agent": "codex"},
    )
    sid = r.get_json()["session"]
    _wait_final(client, sid)
    assert client.get(f"/api/ai/convert/{sid}").get_json()["state"] == "awaiting_dependencies"

    for body in ({"action": "nope"}, {"action": "install"}, {}):
        assert client.post(f"/api/ai/convert/{sid}/dependencies", json=body).status_code == 400

    def settle(timeout=60.0):
        end = time.time() + timeout
        while time.time() < end:
            got = client.get(f"/api/ai/convert/{sid}").get_json()
            if got["state"] != "resuming" and not ai_convert.resuming(sid):
                return got
            time.sleep(0.1)
        raise AssertionError("续跑没有收尾")

    again = client.post(f"/api/ai/convert/{sid}/dependencies", json={"action": "replan"})
    assert again.status_code == 202 and again.get_json()["state"] == "resuming"
    got = settle()
    assert got["state"] == "awaiting_dependencies" and got["dependency_card"]["kind"] == "system"

    done = client.post(f"/api/ai/convert/{sid}/dependencies", json={"action": "replan"})
    assert done.status_code == 202
    got = settle()
    assert got["state"] == "ready" and got["installed"] == ["pandas"], got
    assert len(seen) == 3 and all(rel.startswith(ai_convert.STAGE_PREFIX) for rel in seen)
    # 已经不在等依赖了：再 replan = 409
    late = client.post(f"/api/ai/convert/{sid}/dependencies", json={"action": "replan"})
    assert late.status_code == 409
