"""改图助手的参考图（ADR 0175）。

判据的主语：**不附图时**请求 / 提示词 / 工作区与原来逐字节相同；**附图时**图片只在这次任务的工作目录里（候选的 `_reference/`
或数据目录的会话目录），绝不在用户项目里，提交前检查不把它当成「改了别的文件」，任务收尾即清掉，诊断包拿不到。
Agent CLI 换成一段 python（同 `test_ai_figure_edit.py`）：它在自己的 cwd 里能看见什么，由它自己写进项目外的记录文件。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import zipfile
from io import BytesIO

import pytest

from tavotto.engine import (
    ai_agents,
    ai_bridge,
    ai_figure_edit,
    ai_history,
    ai_prompt_pack,
    ai_refimages,
    ai_tasks,
    config,
)
from tavotto.engine.agent_runner import tools

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

SCRIPT = """import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 4, 9])
    ax.set_title("old")
    fig.savefig("Fig1.pdf")
"""

#: Agent：把自己 cwd 里看见的东西记到项目外的记录文件，再改标题
RECORD_AND_EDIT = (
    "import json; "
    "rec = pathlib.Path({rec!r}); "
    "ref = d / '_reference'; "
    "rec.write_text(json.dumps({{'top': sorted(p.name for p in d.iterdir()), "
    "'ref': sorted(p.name for p in ref.iterdir()) if ref.is_dir() else None, "
    "'ref_bytes': [p.read_bytes().hex() for p in sorted(ref.iterdir())] if ref.is_dir() else None}})); "
    "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8')"
)
#: Agent：改标题之外还把参考图改了 / 删了（提交前检查必须不在乎）
EDIT_AND_TOUCH_REFS = (
    "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8'); "
    "r = d / '_reference'; "
    "[q.write_bytes(b'tampered') for q in r.iterdir()]"
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_refimages, "staged_dir", lambda: tmp_path / "staged")
    monkeypatch.setattr(ai_refimages, "session_root", lambda: tmp_path / "sessions")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(os.environ))
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    rec = tmp_path / "record.json"
    state = {
        "taf": True,
        "code": RECORD_AND_EDIT.format(rec=str(rec)),
        "cwd": [],
        "prompts": [],
        "kw": [],
        "figs": figs,
        "rec": rec,
        "tmp": tmp_path,
    }
    monkeypatch.setattr(
        config, "ai_settings", lambda: {"taf": {"enabled": True}} if state["taf"] else {}
    )

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None, **kw):
        state["cwd"].append(cwd)
        state["prompts"].append(prompt)
        state["kw"].append(kw)
        pre = "import pathlib, sys; d = pathlib.Path(sys.argv[1]); "
        return [sys.executable, "-c", pre + state["code"], cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    yield state
    ai_tasks.unregister(ai_figure_edit.TASK_TYPE)


def _stage_png(n: int = 1) -> list[str]:
    return [ai_refimages.stage(PNG)["id"] for _ in range(n)]


def _run(env, ref_images=None):
    sid = ai_bridge.run(
        "codex",
        "fig1.py",
        "把标题改成 new",
        str(env["figs"]),
        on_changed=lambda script: {"registry": {}, "assets": {}, "published": []},
        ref_images=ref_images,
    )
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(20)
        assert not pump.is_alive()
    return sid, ai_bridge.SESSIONS[sid]


def _project_files(env) -> list[str]:
    return sorted(p.relative_to(env["figs"]).as_posix() for p in env["figs"].rglob("*"))


# ------------------------------------------------------------------ 暂存
def test_stage_accepts_the_four_formats_by_magic_and_reports_size(env):
    info = ai_refimages.stage(PNG)
    assert info["mime"] == "image/png" and info["width"] == 1 and info["height"] == 1
    assert info["bytes"] == len(PNG)
    assert ai_refimages.stage(b"\xff\xd8\xff" + b"0" * 64)["mime"] == "image/jpeg"
    assert ai_refimages.stage(b"GIF89a\x01\x00\x01\x00" + b"0" * 64)["mime"] == "image/gif"
    assert ai_refimages.stage(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 64)["mime"] == "image/webp"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"", "ref_image_empty"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "ref_image_format"),
        (b"%PDF-1.7 not an image", "ref_image_format"),
        (b"\x89PNG\r\n\x1a\n" + b"0" * (ai_refimages.MAX_BYTES + 1), "ref_image_too_large"),
    ],
)
def test_stage_refuses_what_a_model_cannot_be_shown(env, raw, code):
    with pytest.raises(ai_refimages.RefImageError) as exc:
        ai_refimages.stage(raw)
    assert exc.value.code == code


def test_stage_refuses_absurd_pixel_counts(env, monkeypatch):
    monkeypatch.setattr(ai_refimages, "image_dimensions", lambda raw: (9000, 9000))
    with pytest.raises(ai_refimages.RefImageError) as exc:
        ai_refimages.stage(PNG)
    assert exc.value.code == "ref_image_too_large"


def test_discard_removes_the_staged_file_and_is_idempotent(env):
    rid = _stage_png()[0]
    assert ai_refimages.discard(rid) is True
    assert ai_refimages.discard(rid) is False
    assert ai_refimages.discard("../../etc/passwd") is False


def test_valid_ids_keeps_at_most_four_distinct_wellformed_ids():
    ids = [f"{i:032x}" for i in range(6)]
    assert ai_refimages.valid_ids([*ids, ids[0], "../x", 5]) == ids[:4]
    assert ai_refimages.valid_ids("nope") == []
    assert ai_refimages.valid_ids(None) == []


# ------------------------------------------------------------------ 不附图 = 逐字节不变
@pytest.mark.parametrize("taf", [True, False])
@pytest.mark.parametrize("refs", [None, []])
def test_without_images_prompt_workspace_and_history_are_what_they_always_were(env, taf, refs):
    env["taf"] = taf
    sid, sess = _run(env, ref_images=refs)
    prompt = env["prompts"][0]
    expected = ai_bridge._build_prompt("fig1.py", "把标题改成 new", {}, str(env["figs"]))
    if taf:
        assert prompt.startswith(expected)
        assert prompt.endswith(ai_prompt_pack.candidate_note(ai_prompt_pack.DEFAULT, env["figs"]))
        assert "参考图" not in prompt
        rec = json.loads(env["rec"].read_text())
        assert rec["top"] == ["fig1.py"] and rec["ref"] is None, "工作区里没有 _reference"
    else:
        assert prompt == expected
    assert "read_dirs" not in env["kw"][0]
    assert "ref_count" not in sess
    assert ai_history.get(sid)["ref_count"] is None
    assert not (env["tmp"] / "sessions").exists()
    assert _project_files(env) == ["fig1.py"]


# ------------------------------------------------------------------ 候选路径（TAF / 内置执行器）
def test_taf_images_land_in_the_candidate_and_the_prompt_names_them(env):
    ids = _stage_png(2)
    sid, sess = _run(env, ref_images=ids)
    rec = json.loads(env["rec"].read_text())
    assert rec["top"] == ["_reference", "fig1.py"]
    assert rec["ref"] == ["ref-1.png", "ref-2.png"]
    assert bytes.fromhex(rec["ref_bytes"][0]) == PNG
    prompt = env["prompts"][0]
    assert "_reference/ref-1.png" in prompt and "_reference/ref-2.png" in prompt
    assert "2 张参考图" in prompt
    assert prompt.startswith(
        ai_bridge._build_prompt("fig1.py", "把标题改成 new", {}, str(env["figs"]))
    )
    # 图是被「移」进来的：暂存里不再有，同一个 id 不能被第二次任务拿去
    assert not any((env["tmp"] / "staged").glob("*"))
    assert sess["ref_count"] == 2 and ai_history.get(sid)["ref_count"] == 2


def test_taf_images_never_touch_the_project_and_do_not_fail_the_precommit_check(env):
    sid, sess = _run(env, ref_images=_stage_png(1))
    assert sess["status"] == "done" and sess["changed"] is True
    assert '"new"' in (env["figs"] / "fig1.py").read_text(encoding="utf-8")
    assert _project_files(env) == ["fig1.py"], "项目里不许多出 _reference 或别的东西"
    failed = [
        c
        for c in sess["task"].get("unmet", [])
        if c.get("constraint_id", "").startswith("candidate.")
    ]
    assert failed == []
    assert not (env["tmp"] / "cand" / sid).exists(), "候选（连同 _reference）收尾即删"


def test_precommit_does_not_care_if_the_agent_overwrites_the_reference_images(env):
    env["code"] = EDIT_AND_TOUCH_REFS
    sid, sess = _run(env, ref_images=_stage_png(1))
    assert sess["status"] == "done" and sess["changed"] is True, "参考图被动了不算「改了别的文件」"
    assert _project_files(env) == ["fig1.py"]


def test_candidate_tree_excludes_the_reference_dir(tmp_path):
    root = tmp_path / "c"
    (root / "_reference").mkdir(parents=True)
    (root / "_reference" / "ref-1.png").write_bytes(PNG)
    (root / "fig1.py").write_text("x", encoding="utf-8")
    (root / "sub" / "_reference").mkdir(parents=True)
    (root / "sub" / "_reference" / "keep.txt").write_text("y", encoding="utf-8")
    assert sorted(ai_figure_edit._tree(root)) == ["fig1.py", "sub/_reference/keep.txt"], (
        "只有候选根下的 _reference 被排除；子目录里同名的不是参考图区"
    )


def test_missing_staged_ids_just_mean_fewer_images(env):
    ids = _stage_png(1) + ["0" * 32]
    sid, sess = _run(env, ref_images=ids)
    rec = json.loads(env["rec"].read_text())
    assert rec["ref"] == ["ref-1.png"]
    assert sess["ref_count"] == 1
    # 一张都没有等于没附
    sid2, sess2 = _run(env, ref_images=["1" * 32])
    assert "ref_count" not in sess2 and "参考图" not in env["prompts"][-1]


# ------------------------------------------------------------------ 旧路径（外部 CLI 在项目里改）
def test_old_path_images_live_in_the_data_dir_not_the_project_and_are_removed_at_the_end(env):
    env["taf"] = False
    env["code"] = (
        "import json; pathlib.Path({rec!r}).write_text(json.dumps(sorted(p.name for p in d.iterdir())))"
    ).format(rec=str(env["rec"]))
    sid, sess = _run(env, ref_images=_stage_png(2))
    sdir = env["tmp"] / "sessions" / sid
    prompt = env["prompts"][0]
    assert env["cwd"] == [str(env["figs"])], "旧路径的 cwd 仍是项目目录"
    assert str(sdir / "ref-1.png") in prompt and str(sdir / "ref-2.png") in prompt
    assert env["kw"][0]["read_dirs"] == (str(sdir),)
    assert json.loads(env["rec"].read_text()) == ["fig1.py"], "项目里没有任何参考图痕迹"
    assert _project_files(env) == ["fig1.py"]
    assert not sdir.exists(), "会话收尾删旧路径的参考图目录"
    assert ai_history.get(sid)["ref_count"] == 2


def test_old_path_images_are_removed_when_the_attempt_is_rejected(env, monkeypatch):
    env["taf"] = False
    ids = _stage_png(1)

    def boom(*a, **k):
        raise ai_history.ScriptBusy("fig1.py")

    monkeypatch.setattr(ai_history, "record_start", boom)
    with pytest.raises(ai_bridge.AgentError):
        ai_bridge.run("codex", "fig1.py", "x", str(env["figs"]), ref_images=ids)
    assert not list((env["tmp"] / "sessions").glob("*"))


# ------------------------------------------------------------------ 各执行器怎么看到图
def test_claude_gets_add_dir_only_when_there_are_reference_dirs(tmp_path):
    adapter = ai_agents.get_agent("claude")

    def build(**kw):
        return adapter.build_command(
            ai_agents.RunContext(argv=["claude"], prompt="P", cwd=str(tmp_path), **kw)
        ).argv

    plain = build()
    assert "--add-dir" not in plain
    with_dir = build(read_dirs=("/data/ai_refs/abc",))
    assert with_dir[: len(plain)] == plain, "原有参数一个字节不变，只在末尾追加"
    assert with_dir[len(plain) :] == ["--add-dir", "/data/ai_refs/abc"]


def test_codex_command_is_unchanged_by_reference_dirs(tmp_path):
    adapter = ai_agents.get_agent("codex")

    def mk(**kw):
        return adapter.build_command(
            ai_agents.RunContext(argv=["codex"], prompt="P", cwd=str(tmp_path), **kw)
        ).argv

    assert mk() == mk(read_dirs=("/x",))


def test_builtin_read_image_sees_the_reference_inside_the_candidate(tmp_path):
    (tmp_path / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    names = ai_refimages.materialize(
        [ai_refimages.stage(PNG)["id"]], tmp_path / ai_refimages.REFERENCE_DIRNAME
    )
    session = tools.FigureEditSession(tools.RunEnvelope("figure_edit", tmp_path, "fig1.py", "x"))
    res = session.read_image({"path": f"{ai_refimages.REFERENCE_DIRNAME}/{names[0]}"})
    assert not res.is_error and res.images, res.content
    assert base64.b64decode(res.images[0].data) == PNG


# ------------------------------------------------------------------ 隐私
def test_diagnostics_bundle_never_contains_reference_images(env, monkeypatch):
    from tavotto import app as m

    sid, _ = _run(env, ref_images=_stage_png(1))
    # 暂存图与旧路径会话目录挪到**真的位置**（`data_dir()/cache/` 下，诊断包采集日志的那个目录）：此刻那里躺着一张没发出去的暂存图
    # 和一个没收尾的会话目录，诊断包仍不许带走它们
    monkeypatch.setattr(config, "data_dir", lambda: env["tmp"] / "data")
    monkeypatch.setattr(
        ai_refimages, "staged_dir", lambda: config.data_dir() / "cache" / "ai_refs_staged"
    )
    monkeypatch.setattr(
        ai_refimages, "session_root", lambda: config.data_dir() / "cache" / "ai_refs"
    )
    (config.data_dir() / "cache").mkdir(parents=True, exist_ok=True)
    (config.data_dir() / "cache" / "app.log").write_text("log line\n", encoding="utf-8")
    staged = ai_refimages.stage(PNG)
    leftover = ai_refimages.session_dir("deadbeef0000")
    ai_refimages.materialize([ai_refimages.stage(PNG)["id"]], leftover)
    m.app.config["TESTING"] = True
    m.reset_projects()
    res = m.app.test_client().get("/api/diagnostics/bundle")
    assert res.status_code == 200
    z = zipfile.ZipFile(BytesIO(res.data))
    joined = "\n".join(z.namelist())
    assert "_reference" not in joined and "ai_refs" not in joined and staged["id"] not in joined
    for name in z.namelist():
        assert PNG not in z.read(name), f"{name} 里出现了参考图字节"
    m.reset_projects()


def test_no_telemetry_event_mentions_reference_images():
    from tavotto.engine import telemetry

    text = json.dumps(telemetry.EVENTS, default=str).lower()
    assert "ref_image" not in text and "reference" not in text


# ------------------------------------------------------------------ 端点
def test_endpoints_stage_and_discard(env):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    c = m.app.test_client()
    ok = c.post("/api/ai/ref-images", data=PNG, content_type="image/png")
    assert ok.status_code == 200 and ok.json["mime"] == "image/png"
    assert c.post("/api/ai/ref-images", data=b"hello", content_type="image/png").status_code == 400
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (ai_refimages.MAX_BYTES + 1)
    assert c.post("/api/ai/ref-images", data=big, content_type="image/png").status_code == 413
    assert c.delete(f"/api/ai/ref-images/{ok.json['id']}").json == {"ok": True}
    assert not any((env["tmp"] / "staged").glob("*"))
    m.reset_projects()


# ------------------------------------------------------------------ 模型看不看得了图
@pytest.mark.parametrize(
    ("model", "text_only"),
    [
        ("deepseek-chat", True),
        ("deepseek-v4-pro", True),
        ("MiniMax-M2", True),
        ("glm-4.6", True),
        ("kimi-k2", True),
        ("qwen3-coder-plus", True),
        ("gpt-5", False),
        ("gpt-4o", False),
        ("claude-sonnet-4-5", False),
        ("claude-opus-5-5", False),
        ("glm-4.5v", False),
        ("qwen-vl-max", False),
        ("kimi-k2.5", False),
        ("some-unknown-model", False),
        ("", False),
        (None, False),
    ],
)
def test_text_only_is_claimed_only_when_sure(model, text_only):
    assert ai_refimages.known_text_only(model) is text_only


def test_capabilities_omit_no_vision_when_nothing_is_known_text_only():
    assert ai_refimages.text_only_models(["gpt-5", "claude-opus-5-5"]) == []
    assert ai_refimages.text_only_models(["gpt-5", "deepseek-chat"]) == ["deepseek-chat"]
