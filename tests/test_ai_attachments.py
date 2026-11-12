"""改图助手的参考图（ADR 0175）。

判据的主语：**不附图时**请求 / 提示词 / 工作区与原来逐字节相同；**附图时**图片只在这次任务的工作目录里（候选的 `_attachments/`
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
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_agents,
    ai_attachments,
    ai_bridge,
    ai_figure_edit,
    ai_history,
    ai_prompt_pack,
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
    "ref = d / '_attachments'; "
    "rec.write_text(json.dumps({{'top': sorted(p.name for p in d.iterdir()), "
    "'ref': sorted(p.name for p in ref.iterdir()) if ref.is_dir() else None, "
    "'ref_bytes': [p.read_bytes().hex() for p in sorted(ref.iterdir())] if ref.is_dir() else None}})); "
    "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8')"
)
#: Agent：改标题之外还把参考图改了 / 删了（提交前检查必须不在乎）
EDIT_AND_TOUCH_REFS = (
    "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8'); "
    "r = d / '_attachments'; "
    "[q.write_bytes(b'tampered') for q in r.iterdir()]"
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_attachments, "staged_dir", lambda: tmp_path / "staged")
    monkeypatch.setattr(ai_attachments, "session_root", lambda: tmp_path / "sessions")
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
    return [ai_attachments.stage(PNG, f"ref-{i + 1}.png")["id"] for i in range(n)]


def _run(env, attachments=None):
    sid = ai_bridge.run(
        "codex",
        "fig1.py",
        "把标题改成 new",
        str(env["figs"]),
        on_changed=lambda script: {"registry": {}, "assets": {}, "published": []},
        attachments=attachments,
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
    info = ai_attachments.stage(PNG, "a.png")
    assert info["mime"] == "image/png" and info["width"] == 1 and info["height"] == 1
    assert info["bytes"] == len(PNG)
    assert ai_attachments.stage(b"\xff\xd8\xff" + b"0" * 64, "a.jpg")["mime"] == "image/jpeg"
    assert (
        ai_attachments.stage(b"GIF89a\x01\x00\x01\x00" + b"0" * 64, "a.gif")["mime"] == "image/gif"
    )
    assert (
        ai_attachments.stage(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 64, "a.webp")["mime"]
        == "image/webp"
    )


def test_stage_takes_any_file_and_keeps_its_name(env):
    csv = ai_attachments.stage("时间,值\n1,2\n".encode(), "data 结果.csv")
    assert csv["kind"] == "file" and csv["mime"] is None and csv["name"] == "data 结果.csv"
    svg = ai_attachments.stage(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.svg")
    assert svg["kind"] == "file", "SVG 不是模型能「看」的位图：当文件放进去"
    assert ai_attachments.stage(PNG, "shot.png")["kind"] == "image"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"", "attachment_empty"),
        (
            b"\x89PNG\r\n\x1a\n" + b"0" * (ai_attachments.MAX_IMAGE_BYTES + 1),
            "attachment_too_large",
        ),
        (b"x" * (ai_attachments.MAX_FILE_BYTES + 1), "attachment_too_large"),
    ],
)
def test_stage_refuses_empty_and_oversized(env, raw, code):
    with pytest.raises(ai_attachments.AttachmentError) as exc:
        ai_attachments.stage(raw, "x.bin")
    assert exc.value.code == code


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\a.csv", "a.csv"),
        ("a:b*c?.txt", "a_b_c_.txt"),
        ("", "attachment"),
        ("CON.txt", "attachment_CON.txt"),
        ("...", "attachment"),
        ("数据.xlsx", "数据.xlsx"),
    ],
)
def test_safe_name_never_escapes_the_directory(given, expected):
    assert ai_attachments.safe_name(given, "attachment") == expected


def test_safe_name_caps_length_and_keeps_extension():
    out = ai_attachments.safe_name("a" * 300 + ".csv")
    assert len(out) <= 80 and out.endswith(".csv")


def test_materialize_dedupes_names_and_enforces_the_total_cap(env, monkeypatch, tmp_path):
    a = ai_attachments.stage(b"x,y\n", "d.csv")["id"]
    b = ai_attachments.stage(b"x,z\n", "d.csv")["id"]
    out = ai_attachments.materialize([a, b], tmp_path / "dest")
    assert [x["name"] for x in out] == ["d.csv", "d_2.csv"]
    monkeypatch.setattr(ai_attachments, "MAX_TOTAL_BYTES", 6)
    c = ai_attachments.stage(b"12345", "c.txt")["id"]
    d = ai_attachments.stage(b"12345", "d.txt")["id"]
    out = ai_attachments.materialize([c, d], tmp_path / "dest2")
    assert [x["name"] for x in out] == ["c.txt"], "合计超限之后的不收"


def test_discard_removes_the_staged_file_and_is_idempotent(env):
    rid = _stage_png()[0]
    assert ai_attachments.discard(rid) is True
    assert ai_attachments.discard(rid) is False
    assert ai_attachments.discard("../../etc/passwd") is False


def test_valid_ids_keeps_at_most_six_distinct_wellformed_ids():
    ids = [f"{i:032x}" for i in range(9)]
    assert ai_attachments.valid_ids([*ids, ids[0], "../x", 5]) == ids[:6]
    assert ai_attachments.valid_ids("nope") == []
    assert ai_attachments.valid_ids(None) == []


# ------------------------------------------------------------------ 不附图 = 逐字节不变
@pytest.mark.parametrize("taf", [True, False])
@pytest.mark.parametrize("refs", [None, []])
def test_without_images_prompt_workspace_and_history_are_what_they_always_were(env, taf, refs):
    env["taf"] = taf
    sid, sess = _run(env, attachments=refs)
    prompt = env["prompts"][0]
    expected = ai_bridge._build_prompt("fig1.py", "把标题改成 new", {}, str(env["figs"]))
    if taf:
        assert prompt.startswith(expected)
        assert prompt.endswith(ai_prompt_pack.candidate_note(ai_prompt_pack.DEFAULT, env["figs"]))
        assert "参考图" not in prompt
        rec = json.loads(env["rec"].read_text())
        assert rec["top"] == ["fig1.py"] and rec["ref"] is None, "工作区里没有 _attachments"
    else:
        assert prompt == expected
    assert "read_dirs" not in env["kw"][0]
    assert "attachment_count" not in sess
    assert ai_history.get(sid)["attachment_count"] is None
    assert not (env["tmp"] / "sessions").exists()
    assert _project_files(env) == ["fig1.py"]


# ------------------------------------------------------------------ 候选路径（TAF / 内置执行器）
def test_taf_images_land_in_the_candidate_and_the_prompt_names_them(env):
    ids = _stage_png(2)
    sid, sess = _run(env, attachments=ids)
    rec = json.loads(env["rec"].read_text())
    assert rec["top"] == ["_attachments", "fig1.py"]
    assert rec["ref"] == ["ref-1.png", "ref-2.png"]
    assert bytes.fromhex(rec["ref_bytes"][0]) == PNG
    prompt = env["prompts"][0]
    assert "_attachments/ref-1.png" in prompt and "_attachments/ref-2.png" in prompt
    assert "2 张参考图" in prompt and "其他附件" not in prompt
    assert prompt.startswith(
        ai_bridge._build_prompt("fig1.py", "把标题改成 new", {}, str(env["figs"]))
    )
    # 图是被「移」进来的：暂存里不再有，同一个 id 不能被第二次任务拿去
    assert not any((env["tmp"] / "staged").glob("*"))
    assert sess["attachment_count"] == 2 and ai_history.get(sid)["attachment_count"] == 2


def test_taf_mixed_files_are_listed_in_two_groups_and_land_beside_the_images(env):
    ids = _stage_png(1) + [ai_attachments.stage("t,v\n1,2\n".encode(), "结果 数据.csv")["id"]]
    sid, sess = _run(env, attachments=ids)
    rec = json.loads(env["rec"].read_text())
    assert rec["ref"] == ["ref-1.png", "结果 数据.csv"]
    prompt = env["prompts"][0]
    assert "1 张参考图：_attachments/ref-1.png" in prompt
    assert "1 个数据 / 其他附件：_attachments/结果 数据.csv" in prompt
    assert sess["attachment_count"] == 2
    assert _project_files(env) == ["fig1.py"]


def test_only_data_files_means_no_image_sentence(env):
    ids = [ai_attachments.stage(b"a,b\n", "d.csv")["id"]]
    _run(env, attachments=ids)
    assert "参考图" not in env["prompts"][0] and "数据 / 其他附件" in env["prompts"][0]


def test_taf_images_never_touch_the_project_and_do_not_fail_the_precommit_check(env):
    sid, sess = _run(env, attachments=_stage_png(1))
    assert sess["status"] == "done" and sess["changed"] is True
    assert '"new"' in (env["figs"] / "fig1.py").read_text(encoding="utf-8")
    assert _project_files(env) == ["fig1.py"], "项目里不许多出 _attachments 或别的东西"
    failed = [
        c
        for c in sess["task"].get("unmet", [])
        if c.get("constraint_id", "").startswith("candidate.")
    ]
    assert failed == []
    assert not (env["tmp"] / "cand" / sid).exists(), "候选（连同 _attachments）收尾即删"


def test_precommit_does_not_care_if_the_agent_overwrites_the_reference_images(env):
    env["code"] = EDIT_AND_TOUCH_REFS
    sid, sess = _run(env, attachments=_stage_png(1))
    assert sess["status"] == "done" and sess["changed"] is True, "参考图被动了不算「改了别的文件」"
    assert _project_files(env) == ["fig1.py"]


def test_candidate_tree_excludes_the_reference_dir(tmp_path):
    root = tmp_path / "c"
    (root / "_attachments").mkdir(parents=True)
    (root / "_attachments" / "ref-1.png").write_bytes(PNG)
    (root / "fig1.py").write_text("x", encoding="utf-8")
    (root / "sub" / "_attachments").mkdir(parents=True)
    (root / "sub" / "_attachments" / "keep.txt").write_text("y", encoding="utf-8")
    assert sorted(ai_figure_edit._tree(root)) == ["fig1.py", "sub/_attachments/keep.txt"], (
        "只有候选根下的 _attachments 被排除；子目录里同名的不是参考图区"
    )


def test_missing_staged_ids_just_mean_fewer_images(env):
    ids = _stage_png(1) + ["0" * 32]
    sid, sess = _run(env, attachments=ids)
    rec = json.loads(env["rec"].read_text())
    assert rec["ref"] == ["ref-1.png"]
    assert sess["attachment_count"] == 1
    # 一张都没有等于没附
    sid2, sess2 = _run(env, attachments=["1" * 32])
    assert "attachment_count" not in sess2 and "参考图" not in env["prompts"][-1]


# ------------------------------------------------------------------ 旧路径（外部 CLI 在项目里改）
def test_old_path_images_live_in_the_data_dir_not_the_project_and_are_removed_at_the_end(env):
    env["taf"] = False
    env["code"] = (
        "import json; pathlib.Path({rec!r}).write_text(json.dumps(sorted(p.name for p in d.iterdir())))"
    ).format(rec=str(env["rec"]))
    sid, sess = _run(env, attachments=_stage_png(2))
    sdir = env["tmp"] / "sessions" / sid
    prompt = env["prompts"][0]
    assert env["cwd"] == [str(env["figs"])], "旧路径的 cwd 仍是项目目录"
    assert str(sdir / "ref-1.png") in prompt and str(sdir / "ref-2.png") in prompt
    assert env["kw"][0]["read_dirs"] == (str(sdir),)
    assert json.loads(env["rec"].read_text()) == ["fig1.py"], "项目里没有任何参考图痕迹"
    assert _project_files(env) == ["fig1.py"]
    assert not sdir.exists(), "会话收尾删旧路径的参考图目录"
    assert ai_history.get(sid)["attachment_count"] == 2


def test_old_path_images_are_removed_when_the_attempt_is_rejected(env, monkeypatch):
    env["taf"] = False
    ids = _stage_png(1)

    def boom(*a, **k):
        raise ai_history.ScriptBusy("fig1.py")

    monkeypatch.setattr(ai_history, "record_start", boom)
    with pytest.raises(ai_bridge.AgentError):
        ai_bridge.run("codex", "fig1.py", "x", str(env["figs"]), attachments=ids)
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
    names = ai_attachments.materialize(
        [ai_attachments.stage(PNG, "r.png")["id"]], tmp_path / ai_attachments.ATTACHMENTS_DIRNAME
    )
    session = tools.FigureEditSession(tools.RunEnvelope("figure_edit", tmp_path, "fig1.py", "x"))
    res = session.read_image({"path": f"{ai_attachments.ATTACHMENTS_DIRNAME}/{names[0]['name']}"})
    assert not res.is_error and res.images, res.content
    assert base64.b64decode(res.images[0].data) == PNG


# ------------------------------------------------------------------ 隐私
def test_diagnostics_bundle_never_contains_attachments(env, monkeypatch):
    from tavotto import app as m

    sid, _ = _run(env, attachments=_stage_png(1))
    # 暂存图与旧路径会话目录挪到**真的位置**（`data_dir()/cache/` 下，诊断包采集日志的那个目录）：此刻那里躺着一张没发出去的暂存图
    # 和一个没收尾的会话目录，诊断包仍不许带走它们
    monkeypatch.setattr(config, "data_dir", lambda: env["tmp"] / "data")
    monkeypatch.setattr(
        ai_attachments, "staged_dir", lambda: config.data_dir() / "cache" / "ai_refs_staged"
    )
    monkeypatch.setattr(
        ai_attachments, "session_root", lambda: config.data_dir() / "cache" / "ai_refs"
    )
    (config.data_dir() / "cache").mkdir(parents=True, exist_ok=True)
    (config.data_dir() / "cache" / "app.log").write_text("log line\n", encoding="utf-8")
    staged = ai_attachments.stage(PNG, "leak.png")
    leftover = ai_attachments.session_dir("deadbeef0000")
    ai_attachments.materialize([ai_attachments.stage(PNG, "l.png")["id"]], leftover)
    m.app.config["TESTING"] = True
    m.reset_projects()
    res = m.app.test_client().get("/api/diagnostics/bundle")
    assert res.status_code == 200
    z = zipfile.ZipFile(BytesIO(res.data))
    joined = "\n".join(z.namelist())
    assert "_attachments" not in joined and "ai_refs" not in joined and staged["id"] not in joined
    for name in z.namelist():
        assert PNG not in z.read(name), f"{name} 里出现了参考图字节"
    m.reset_projects()


def test_no_telemetry_event_mentions_reference_images():
    from tavotto.engine import telemetry

    text = json.dumps(telemetry.EVENTS, default=str).lower()
    assert "attachment" not in text and "reference" not in text


# ------------------------------------------------------------------ 端点
def test_endpoints_stage_and_discard(env):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    c = m.app.test_client()
    ok = c.post("/api/ai/attachments?name=shot.png", data=PNG, content_type="image/png")
    assert ok.status_code == 200 and ok.json["mime"] == "image/png" and ok.json["kind"] == "image"
    csv = c.post(
        "/api/ai/attachments?name=%E6%95%B0%E6%8D%AE.csv",
        data=b"a,b\n1,2\n",
        content_type="text/csv",
    )
    assert csv.json["kind"] == "file" and csv.json["name"] == "数据.csv"
    assert c.post("/api/ai/attachments", data=b"", content_type="image/png").status_code == 400
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (ai_attachments.MAX_IMAGE_BYTES + 1)
    assert c.post("/api/ai/attachments", data=big, content_type="image/png").status_code == 413
    assert c.delete(f"/api/ai/attachments/{ok.json['id']}").json == {"ok": True}
    assert c.delete(f"/api/ai/attachments/{csv.json['id']}").json == {"ok": True}
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
    assert ai_attachments.known_text_only(model) is text_only


def test_capabilities_omit_no_vision_when_nothing_is_known_text_only():
    assert ai_attachments.text_only_models(["gpt-5", "claude-opus-5-5"]) == []
    assert ai_attachments.text_only_models(["gpt-5", "deepseek-chat"]) == ["deepseek-chat"]


def test_contained_rejects_names_escaping_the_base(tmp_path):
    """落盘前的包含性核对：清洗之外的第二道，`..`、绝对路径、子目录都不许。"""
    from tavotto.engine import ai_attachments as a

    assert a._contained(tmp_path, "ok.csv") == Path(os.path.realpath(tmp_path)) / "ok.csv"
    for bad in ("../x.csv", "/etc/passwd", "sub/x.csv", "..", ""):
        assert a._contained(tmp_path, bad) is None, bad
