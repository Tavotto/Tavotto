"""「生成可编辑版本」端到端（ADR 0143），走真 app 与真 safe worker：`POST /api/ai/convert` → Agent（假 CLI）在候选里新建
Matplotlib 版脚本 → 后端在用户同一路由下真的跑一遍（probe）→ 对照数据 / 预览 → `adopt` 新建进项目。
原脚本字节全程不变；校验用的临时文件跑完即删。"""

from __future__ import annotations

import json
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
        state.setdefault("prompts", []).append(prompt)
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


def test_conversion_prompt_uses_bound_project_runtime_hint_without_probing(project, monkeypatch):
    m, client, figs, state = project
    selected = str(figs / "selected env" / "python")
    seen = []

    def peek(root):
        seen.append(Path(root))
        return selected, "project_explicit"

    monkeypatch.setattr(pool, "peek_worker_python", peek)
    # The CLI stops before producing a candidate: any runtime probe here would
    # be an eager side effect of preparing the prompt, not candidate validation.
    state["code"] = (
        "import json; print(json.dumps({'type': 'item.completed', "
        "'item': {'type': 'agent_message', 'text': 'TAVOTTO_NEEDS_INPUT: native reference needed'}}))"
    )
    sid = _start(client).get_json()["session"]
    sess = _wait_final(client, sid)
    assert sess["task"]["outcome"] == "needs_input"
    assert seen == [figs]
    prompt = state["prompts"][0]
    assert json.dumps(selected, ensure_ascii=False) in prompt
    assert '"verified": false' in prompt and '"source": "project_explicit"' in prompt


def test_conversion_runtime_hint_does_not_start_or_select_a_python(project, monkeypatch):
    m, _client, figs, _state = project
    monkeypatch.setattr(pool, "peek_worker_python", lambda root: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Planning the conversion must not resolve or probe an interpreter")

    monkeypatch.setattr(pool, "resolve_worker_python", forbidden)
    monkeypatch.setattr(pool, "select_worker_python", forbidden)
    with m.app.test_request_context("/"):
        runner = m._convert_runner(m.current_ctx(), "fig.py")
    assert runner.runtime_hint() == {"status": "not_selected", "verified": False}


def test_builtin_correction_continues_existing_bytes_without_resetting_checks(
    project, monkeypatch, tmp_path
):
    """Real child CLI + real safe worker; only the model responses are scripted."""
    from tavotto.engine import ai_agents

    m, client, figs, state = project
    broken = GOOD.replace(
        "ax.plot([0, 1, 2], [0, 1, 4])", "raise RuntimeError('fixture correction needed')"
    )
    broken += "\ndef render():\n    plt.figure()\n    plt.plot([0, 1])\n"
    plans = []
    for index, code in enumerate((broken, GOOD)):
        turns = [{"tool_calls": [{"name": "create_file", "args": {"content": code}}]}]
        turns += [{"tool_calls": [{"name": "check", "args": {}}]}] * (3 if index == 0 else 1)
        turns += [
            {
                "text": "candidate",
                **({"expect": [{"name": "check", "is_error": True}]} if index else {}),
            }
        ]
        path = tmp_path / f"model-{index}.json"
        path.write_text(json.dumps({"turns": turns}))
        plans.append(path)
    calls = []

    def cmd(agent, prompt, cwd, **kw):
        calls.append(kw)
        spec = ai_agents.BuiltinAgent().build_command(
            ai_agents.RunContext(
                argv=[],
                prompt=prompt,
                cwd=cwd,
                target=kw["target"],
                task=kw["task"],
                output=kw["output"],
                resume_output_sha256=kw.get("resume_output_sha256", ""),
                checks_used=kw.get("checks_used", 0),
                endpoint_args=["--wire", "demo", "--demo-script", str(plans[len(calls) - 1])],
            )
        )
        return list(spec.argv), {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    monkeypatch.setattr(ai_bridge, "_cmd", cmd)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda _cmd, extra: {**os.environ, **extra})
    monkeypatch.setattr(ai_bridge.ai_providers, "builtin_conn", lambda *_: {"id": "test"})
    with m.app.test_request_context("/"):
        runner = m._convert_runner(m.current_ctx(), "fig.py")
    sid = ai_bridge.run(
        "builtin",
        "fig.py",
        "convert",
        str(figs),
        task_hooks={"runner": runner},
        convert={"library": "pillow", "original_rel": "out.png"},
    )
    sess = _wait_final(client, sid)
    assert sess["task"]["outcome"] == "completed", sess
    assert len(calls) == 2 and calls[1]["checks_used"] == 3
    assert len(calls[1]["resume_output_sha256"]) == 64
    assert (figs / "fig.py").read_bytes() == ORIGINAL.encode()
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


@pytest.mark.parametrize("reject_after_agent_check", [False, True])
@pytest.mark.parametrize("final_tool", ["check", "finish"])
def test_builtin_final_check_reaches_adoption_only_after_parent_validation(
    project, monkeypatch, tmp_path, reject_after_agent_check, final_tool
):
    """Real runner IPC + worker: last-turn completion cannot bypass Task Core validation."""
    from tavotto.engine import ai_agents

    m, client, figs, _state = project
    plan = tmp_path / "final-check.json"
    plan.write_text(
        json.dumps(
            {
                "turns": [
                    {"tool_calls": [{"name": "create_file", "args": {"content": GOOD}}]},
                    {
                        "tool_calls": [
                            {
                                "name": final_tool,
                                "args": {
                                    "summary": "候选已出图",
                                    **({"final": True} if final_tool == "check" else {}),
                                },
                            }
                        ]
                    },
                ]
            }
        )
    )

    def cmd(agent, prompt, cwd, **kw):
        spec = ai_agents.BuiltinAgent().build_command(
            ai_agents.RunContext(
                argv=[],
                prompt=prompt,
                cwd=cwd,
                target=kw["target"],
                task=kw["task"],
                output=kw["output"],
                resume_output_sha256=kw.get("resume_output_sha256", ""),
                checks_used=kw.get("checks_used", 0),
                endpoint_args=["--wire", "demo", "--demo-script", str(plan), "--max-turns", "2"],
            )
        )
        return list(spec.argv), {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    monkeypatch.setattr(ai_bridge, "_cmd", cmd)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda _cmd, extra: {**os.environ, **extra})
    monkeypatch.setattr(ai_bridge.ai_providers, "builtin_conn", lambda *_: {"id": "test"})
    with m.app.test_request_context("/"):
        real_runner = m._convert_runner(m.current_ctx(), "fig.py")
    calls = []

    def runner(*args, **kwargs):
        result = real_runner(*args, **kwargs)
        calls.append(result)
        if reject_after_agent_check and len(calls) > 1:
            return {"error": {"code": "capture_incomplete", "message": "late validation failed"}}
        return result

    sid = ai_bridge.run(
        "builtin",
        "fig.py",
        "convert",
        str(figs),
        task_hooks={"runner": runner},
        convert={"library": "pillow", "original_rel": "out.png"},
    )
    sess = _wait_final(client, sid)
    assert len(calls) >= 2 and calls[0].get("stems")
    assert (figs / "fig.py").read_bytes() == ORIGINAL.encode()
    assert (figs / "out.png").read_bytes() == PNG
    if reject_after_agent_check:
        assert sess["task"]["outcome"] == "failed", sess
        assert client.post(f"/api/ai/convert/{sid}/adopt").status_code != 200
    else:
        assert sess["task"]["outcome"] == "completed", sess
        assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


def test_agent_check_reads_current_live_pngs_before_worker_retirement(project):
    import base64

    m, _client, figs, state = project
    # App/worker evidence: two differently colored live figures, not a guessed
    # .png pathname beside an SVG/manifest generation.
    script = figs / "visual_check.py"
    script.write_text(
        "import matplotlib.pyplot as plt\nfor color in ('red', 'blue'):\n    fig = plt.figure(figsize=(2, 2), facecolor=color)\n    plt.plot([0, 1])\n"
    )
    with m.app.test_request_context("/"):
        runner = m._convert_runner(m.current_ctx(), "fig.py")
    result = runner.with_images(str(figs), script.name, (str(figs),))
    assert not result.get("error"), result
    assert len(result["images"]) == len(result["stems"]) == 2
    decoded = [base64.b64decode(img["data"]) for img in result["images"]]
    assert all(b.startswith(b"\x89PNG\r\n\x1a\n") for b in decoded)
    assert decoded[0] != decoded[1]
    assert [img["label"].split(":")[0] for img in result["images"]] == [
        "Captured figure 1",
        "Captured figure 2",
    ]
    assert pool.peek(script.name, str(figs), write_roots=(str(figs),)) is None


@pytest.mark.parametrize("output", ["", "partial line without newline"])
def test_silent_conversion_cli_times_out_without_waiting_for_a_stdout_line(
    project, monkeypatch, output
):
    _m, client, figs, state = project
    monkeypatch.setattr(ai_bridge, "TIMEOUT_S", 0.5)
    state["code"] = f"import time; sys.stdout.write({output!r}); sys.stdout.flush(); time.sleep(30)"
    before = (figs / "fig.py").read_bytes()
    sid = _start(client).get_json()["session"]
    sess = _wait_final(client, sid, timeout=8)
    assert sess["status"] == "timeout" and sess["final"] is True
    assert (figs / "fig.py").read_bytes() == before
    assert not (figs / "fig_matplotlib.py").exists()
    assert ai_bridge.SESSIONS[sid]["exited"].is_set()


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
    verified_bytes = (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").read_bytes()
    a = client.post(f"/api/ai/convert/{sid}/adopt")
    assert a.status_code == 200, a.get_json()
    body = a.get_json()
    assert body["script"] == "fig_matplotlib.py" and body["refresh"]["status"] in (
        "ok",
        "not_wired",
    )
    assert (figs / "fig_matplotlib.py").read_bytes() == verified_bytes
    assert (figs / "fig.py").read_bytes() == before
    again = client.post(f"/api/ai/convert/{sid}/adopt")
    assert again.status_code == 200 and again.get_json()["script"] == "fig_matplotlib.py"


@pytest.mark.parametrize(
    "language,suffix,source",
    [
        ("python", "py", ORIGINAL.encode()),
        ("r", "R", b'df <- read.csv("data.csv")\nplot(df)\n'),
        ("matlab", "m", b"% curve\nx = linspace(0,1,20);\nfigure;\nplot(x,x.^2);\n"),
    ],
)
def test_every_conversion_source_prepares_target_python_dependencies(
    project, monkeypatch, language, suffix, source
):
    """All source languages reach the same dependency hook before the real worker.

    Only the model and installer are stubbed; this does not certify real package installation.
    """
    from tavotto.engine import convertdeps

    m, client, figs, state = project
    script = f"fig.{suffix}"
    (figs / script).write_bytes(source)
    seen = []

    def fake_ensure(proj, rel, cancel=None):
        seen.append((proj, rel))
        return {"status": "installed", "installed": ["pandas"]}

    monkeypatch.setattr(convertdeps, "ensure", fake_ensure)
    r = client.post(
        "/api/ai/convert",
        json={
            "script": script,
            "source_language": language,
            "path": "out.png",
            "agent": "codex",
            **({"library": "pillow"} if language == "python" else {}),
        },
    )
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["session"]
    sess = _wait_final(client, sid)
    assert sess["task"]["outcome"] == "completed", sess.get("task")
    st = client.get(f"/api/ai/convert/{sid}").get_json()
    assert st["source_language"] == language
    assert st["library"] == ("pillow" if language == "python" else None)
    assert st["installed"] == ["pandas"]
    assert st["comparison"]["verification"] == "side_by_side"
    assert seen and seen[0][1].startswith(ai_convert.STAGE_PREFIX)
    assert (figs / script).read_bytes() == source
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


@pytest.mark.parametrize(
    ("main_body", "reason"),
    [
        ("fig, ax = plt.subplots(); print(ax.y0)", "run_failed"),
        ("pass", "no_matplotlib_figure"),
    ],
)
def test_conversion_cannot_replace_failed_primary_entry_with_a_working_helper(
    project, main_body, reason
):
    """Actual worker: a partial helper cannot certify the script later reopened at main."""
    _m, client, figs, state = project
    code = (
        "import matplotlib.pyplot as plt\n"
        "def render():\n"
        "    fig, ax = plt.subplots()\n"
        "    ax.plot([0, 1])\n"
        "    fig.savefig('helper.png')\n"
        f"def main():\n    {main_body}\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    state["code"] = f"(d / 'fig_matplotlib.py').write_text({code!r}, encoding='utf-8')"
    sid = _start(client).get_json()["session"]
    session = _wait_final(client, sid)
    assert session["task"]["outcome"] == "failed", session["task"]
    assert any(u["reason_code"] == reason for u in session["task"]["unmet"])
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code != 200
    assert (figs / "fig.py").read_bytes() == ORIGINAL.encode()
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


def test_existing_output_directories_do_not_block_conversion_validation(project, monkeypatch):
    """Actual conversion worker: idempotent mkdir preserves project directories and source bytes."""
    _m, client, figs, state = project
    worker_tmp = figs.parent / "worker-tmp"
    worker_tmp.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(worker_tmp))
    monkeypatch.setenv("LOCALAPPDATA", str(figs.parent / "localappdata"))
    outputs = figs / "data" / "dst"
    outputs.mkdir(parents=True)
    before = outputs.stat().st_mtime_ns
    code = (
        "from pathlib import Path\n"
        "(Path(__file__).parent / 'data' / 'dst').mkdir(parents=True, exist_ok=True)\n" + GOOD
    )
    state["code"] = f"(d / 'fig_matplotlib.py').write_text({code!r}, encoding='utf-8')"
    sid = _start(client).get_json()["session"]
    session = _wait_final(client, sid)
    assert session["task"]["outcome"] == "completed", session["task"]
    assert client.get(f"/api/ai/convert/{sid}").get_json()["state"] == "ready"
    assert outputs.stat().st_mtime_ns == before and list(outputs.iterdir()) == []
    assert (figs / "fig.py").read_bytes() == ORIGINAL.encode()
    assert (figs / "out.png").read_bytes() == PNG
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


@pytest.mark.parametrize(
    "operation,name",
    [("mkdir(exist_ok=True)", "new-output"), ("write_bytes(b'clobbered')", "out.png")],
)
def test_output_write_failure_keeps_its_actual_cause_for_the_next_correction(
    project, monkeypatch, operation, name
):
    """Real worker denial must survive probe wrapping and guide a safe correction."""
    _m, client, figs, state = project
    worker_tmp = figs.parent / "worker-tmp"
    worker_tmp.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(worker_tmp))
    monkeypatch.setenv("LOCALAPPDATA", str(figs.parent / "localappdata"))
    bad = f"from pathlib import Path\nPath({str(figs / name)!r}).{operation}\n" + GOOD
    state["code"] = (
        "p = d / 'fig_matplotlib.py'; "
        f"p.write_text({GOOD!r} if p.exists() else {bad!r}, encoding='utf-8')"
    )
    before = (figs / "out.png").read_bytes()
    sid = _start(client).get_json()["session"]
    session = _wait_final(client, sid)
    assert len(state["prompts"]) == 2, json.dumps(session, ensure_ascii=False)
    assert ai_convert.WRITE_OUTSIDE_HINT in state["prompts"][1]
    assert "保留 Matplotlib savefig" in state["prompts"][1]
    assert "Rejected write:" in state["prompts"][1]
    assert "改成写到项目内" not in state["prompts"][1]
    assert name in state["prompts"][1], "The model must see which write actually failed"
    assert session["task"]["outcome"] == "completed", session
    if name != "out.png":
        assert not (figs / name).exists()
    assert (figs / "out.png").read_bytes() == before
    assert (figs / "fig.py").read_bytes() == ORIGINAL.encode()
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


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


@pytest.mark.parametrize("language", ["python", "r", "matlab"])
def test_replan_endpoint_rechecks_the_same_candidate_with_the_current_environment(
    project, monkeypatch, language
):
    """系统 Python 卡 → 用户换了环境 → `POST …/dependencies {action: replan}`：不带摘要、不重跑 Agent，按此刻环境（`convertdeps.ensure`）
    重新判定同一个候选；再换回仍是系统 Python 就仍回系统卡；非法动作 400。"""
    from tavotto.engine import convertdeps

    m, client, figs, state = project
    script = {"python": "fig.py", "r": "fig.R", "matlab": "fig.m"}[language]
    source = {
        "python": ORIGINAL.encode(),
        "r": b'df <- read.csv("data.csv")\nplot(df)\n',
        "matlab": b"% curve\nx = linspace(0,1,20);\nfigure;\nplot(x,x.^2);\n",
    }[language]
    (figs / script).write_bytes(source)
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
        json={
            "script": script,
            "source_language": language,
            "path": "out.png",
            "agent": "codex",
            **({"library": "pillow"} if language == "python" else {}),
        },
    )
    sid = r.get_json()["session"]
    _wait_final(client, sid)
    assert client.get(f"/api/ai/convert/{sid}").get_json()["state"] == "awaiting_dependencies"
    candidate = ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py"
    generated = candidate.read_bytes()
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 409

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
    assert len(state["prompts"]) == 1, "Resuming dependencies must not ask the model to regenerate"
    assert candidate.read_bytes() == generated
    assert (figs / script).read_bytes() == source
    adopted = client.post(f"/api/ai/convert/{sid}/adopt")
    assert adopted.status_code == 200
    assert (figs / adopted.get_json()["script"]).read_bytes() == generated
    # 已经不在等依赖了：再 replan = 409
    late = client.post(f"/api/ai/convert/{sid}/dependencies", json={"action": "replan"})
    assert late.status_code == 409
