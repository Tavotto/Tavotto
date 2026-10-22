"""「生成可编辑版本」（`convert_to_editable`，ADR 0143）：适配器注册、候选只写一个新文件、提交 O_EXCL 不覆盖、原脚本字节不变、
校验失败路径、总开关。

Agent CLI 换成一段 python（不联网）：它在**自己的 cwd**（候选目录）里写 `<原名>_matplotlib.py`。校验运行（真实环境里走 probe）
由用例注入的假 `runner` 代替——真 worker 的端到端在 `test_ai_convert_app.py`。
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_bridge,
    ai_convert,
    ai_figure_edit,
    ai_history,
    ai_prompt_pack,
    ai_tasks,
    config,
    scriptlock,
)

ORIGINAL = """from PIL import Image

img = Image.new("RGB", (40, 20), "white")
img.save("out.png")
"""
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x28\x00\x00\x00\x14\x08\x02\x00\x00\x00"
    + b"\x00" * 16
)
GOOD = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2])\nfig.savefig('out.png')\n"

#: Agent 在 cwd 里写那一个新文件
WRITE_GOOD = f"(d / 'fig_matplotlib.py').write_text({GOOD!r}, encoding='utf-8')"
WRITE_NO_MPL = "(d / 'fig_matplotlib.py').write_text('print(1)\\n', encoding='utf-8')"
WRITE_SYNTAX = "(d / 'fig_matplotlib.py').write_text('def f(:\\n', encoding='utf-8')"
WRITE_NOTHING = "pass"
#: 额外还动了副本里的原脚本 / 多建了一个文件
TOUCH_COPY = (
    f"(d / 'fig_matplotlib.py').write_text({GOOD!r}, encoding='utf-8'); "
    "(d / 'fig.py').write_text('# tampered\\n', encoding='utf-8')"
)
EXTRA_FILE = (
    f"(d / 'fig_matplotlib.py').write_text({GOOD!r}, encoding='utf-8'); "
    "(d / 'extra.png').write_bytes(b'x')"
)
#: 越界：直接往原项目里写
WRITE_PROJECT = (
    f"(d / 'fig_matplotlib.py').write_text({GOOD!r}, encoding='utf-8'); "
    "(proj / 'sneaky.txt').write_text('x', encoding='utf-8')"
)


def ok_runner(figures_dir, rel, write_roots=None):
    return {
        "stems": ["out"],
        "size_mm": [100.0, 50.0],
        "svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
    }


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "convert")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(os.environ))
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig.py").write_bytes(ORIGINAL.encode())  # 按字节写：Windows 文本模式会换成 CRLF
    (figs / "out.png").write_bytes(PNG)
    (figs / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    state = {"code": WRITE_GOOD, "cwd": [], "prompts": [], "figs": figs, "runner": ok_runner}

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        state["cwd"].append(cwd)
        state["prompts"].append(prompt)
        pre = f"import pathlib, sys; d = pathlib.Path(sys.argv[1]); proj = pathlib.Path({str(figs)!r}); "
        code = state["codes"].pop(0) if state.get("codes") else state["code"]
        return [sys.executable, "-c", pre + code, cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    yield state
    ai_tasks.unregister(ai_convert.TASK_TYPE)


def _run(env, *, runner="default", original="out.png", events=None, library="pillow"):
    runner = env["runner"] if runner == "default" else runner
    return ai_bridge.run(
        "codex",
        "fig.py",
        "生成可编辑的 Matplotlib 版本",
        str(env["figs"]),
        on_event=(lambda n, d: events.append((n, d))) if events is not None else None,
        task_hooks={"runner": runner} if runner else None,
        convert={"library": library, "original_rel": original},
    )


def _wait(sid, timeout=20.0):
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(timeout)
        assert not pump.is_alive(), "pump 线程没有收尾"
    return ai_bridge.SESSIONS[sid]


def _project_files(figs: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(figs.iterdir()) if p.is_file()}


def _project(env) -> str:
    return str(env["figs"].resolve())


# --------------------------------------------------------------------------- 适配器与候选
def test_adapter_is_registered_as_a_task_adapter_not_a_new_channel(env):
    sid = _run(env)
    _wait(sid)
    assert ai_tasks.ADAPTERS[ai_convert.TASK_TYPE] is ai_convert.ADAPTER
    row = ai_history.get(sid)
    assert row["task_type"] == "convert_to_editable"
    assert row["provider"] == "codex", "执行仍走既有的 Agent 注册表"


def test_agent_runs_in_a_candidate_workspace_and_only_one_new_file_is_staged(env):
    events: list = []
    sid = _run(env, events=events)
    sess = _wait(sid)
    cand_dir = Path(env["cwd"][0])
    assert cand_dir.parent == ai_convert.CONVERT_DIR, "Agent 的 cwd 必须是候选目录，不是项目目录"
    assert str(env["figs"]) in env["prompts"][0], "提示词说明原项目只读的位置"
    assert "fig_matplotlib.py" in env["prompts"][0] and "reference/out.png" in env["prompts"][0]
    assert sess["status"] == "done" and sess["changed"] is False
    assert sess["task"]["outcome"] == "completed", sess["task"]
    # 候选留存待采用：目标文件 + meta + 预览；原脚本副本与参考图原样
    names = sorted(p.name for p in cand_dir.iterdir())
    assert names == ["fig.py", "fig_matplotlib.py", "meta.json", "preview.svg", "reference"]
    assert (cand_dir / "fig.py").read_text(encoding="utf-8") == ORIGINAL
    # 项目里什么都没多、什么都没少
    assert sorted(_project_files(env["figs"])) == ["data.csv", "fig.py", "out.png"]
    done = next(d for n, d in events if n == "ai.done")
    assert done["changed"] is False and done["refresh"]["status"] == "skipped"
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == "ready" and st["has_preview"] and st["target_name"] == "fig_matplotlib.py"
    assert st["comparison"]["original"]["width_px"] == 40
    assert st["comparison"]["original"]["height_px"] == 20
    assert st["comparison"]["candidate"]["width_mm"] == 100.0
    assert st["comparison"]["pixel_diff"] is None


def test_original_script_bytes_never_change_through_the_whole_flow(env):
    before = _project_files(env["figs"])
    sid = _run(env)
    _wait(sid)
    assert _project_files(env["figs"]) == before
    ai_convert.adopt(sid, _project(env))
    after = _project_files(env["figs"])
    assert after["fig.py"] == before["fig.py"] == ORIGINAL.encode()
    assert set(after) - set(before) == {"fig_matplotlib.py"}


def test_revert_of_a_convert_session_is_refused(env):
    sid = _run(env)
    _wait(sid)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.revert(sid)
    assert exc.value.code == "ai_revert_not_applicable"
    assert ai_bridge.get(sid)["revert_available"] is False


# --------------------------------------------------------------------------- 检查失败路径
@pytest.mark.parametrize(
    ("code", "failed"),
    [
        (WRITE_NOTHING, ("candidate.target", "target_missing")),
        (WRITE_SYNTAX, ("candidate.syntax", "syntax_error")),
        (WRITE_NO_MPL, ("candidate.matplotlib", "no_matplotlib_import")),
        (TOUCH_COPY, ("candidate.tree", "other_file_changed")),
        (EXTRA_FILE, ("candidate.tree", "other_file_changed")),
        (WRITE_PROJECT, ("project.untouched", "project_files_changed")),
    ],
)
def test_failed_prechecks_stage_nothing_and_leave_the_project_alone(env, code, failed):
    env["code"] = code
    before = _project_files(env["figs"])
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    unmet = {(u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]}
    assert failed in unmet
    assert not (ai_convert.CONVERT_DIR / sid).exists(), "没通过的候选收尾即删"
    after = _project_files(env["figs"])
    if code is WRITE_PROJECT:
        after.pop(
            "sneaky.txt"
        )  # Agent 越界写的那个文件由检查如实报出（上面的 unmet），不是被提交的
    assert after == before
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def test_validation_run_that_overwrites_project_files_fails_the_task(env):
    """校验运行会在原脚本旁真跑候选：它写相对路径输出（改到原图）也要被跑完后的复核抓住。"""
    figs = env["figs"]

    def writing_runner(figures_dir, rel, write_roots=None):
        (figs / "out.png").write_bytes(b"clobbered")
        return ok_runner(figures_dir, rel, write_roots)

    env["runner"] = writing_runner
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    unmet = {(u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]}
    assert ("project.untouched", "project_files_changed") in unmet
    assert not (ai_convert.CONVERT_DIR / sid).exists()
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def test_validation_run_is_isolated_by_write_roots_and_user_out_png_is_untouched(env):
    """P1（Codex #1277）：隔离而不只是事后检测——校验运行拿到的写入根只有候选目录；候选写 `out.png` 在写发生时就被拦。"""
    figs = env["figs"]
    before = (figs / "out.png").read_bytes()
    seen = {}

    def tripwire_runner(figures_dir, rel, write_roots=None):
        seen["roots"] = write_roots
        # 模拟 worker 绊线：根外的写在事件发生时失败（真 worker 的端到端在 test_ai_convert_app.py）
        target = figs / "out.png"
        if not any(str(target.resolve()).startswith(str(Path(r).resolve())) for r in write_roots):
            return {"error": {"code": "write_outside_roots", "message": "x"}}
        target.write_bytes(b"clobbered")
        return ok_runner(figures_dir, rel, write_roots)

    env["runner"] = tripwire_runner
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert (figs / "out.png").read_bytes() == before
    cdir = ai_convert.CONVERT_DIR / sid
    assert seen["roots"] == (str(cdir),), "写入根只能是候选目录，不含项目目录"
    assert str(figs) not in "".join(seen["roots"])
    assert ("render.capture", "run_failed") in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }


def test_no_figure_captured_is_a_failure_with_a_reason(env):
    env["runner"] = lambda figures_dir, rel, write_roots=None: {
        "error": {"code": "no_figures_captured", "message": "x"}
    }
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert ("render.capture", "no_matplotlib_figure") in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }
    assert not (ai_convert.CONVERT_DIR / sid).exists()


def test_run_error_is_a_failure_and_the_stage_file_is_gone(env):
    seen = []

    def boom(figures_dir, rel, write_roots=None):
        seen.append(Path(figures_dir) / rel)
        assert (Path(figures_dir) / rel).is_file(), "校验期间候选以临时文件存在于原脚本旁"
        return {"error": {"code": "script_exception", "message": "KeyError: 'x'"}}

    env["runner"] = boom
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert ("render.capture", "run_failed") in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }
    assert (
        seen and seen[0].parent == env["figs"] and seen[0].name.startswith(ai_convert.STAGE_PREFIX)
    )
    assert not seen[0].exists(), "校验用的临时文件跑完即删"
    assert sorted(_project_files(env["figs"])) == ["data.csv", "fig.py", "out.png"]


def test_without_a_wired_runner_the_candidate_is_not_staged(env):
    sid = _run(env, runner=None)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert not (ai_convert.CONVERT_DIR / sid).exists()


def test_one_bounded_correction_can_fix_a_missing_matplotlib_import(env):
    env["codes"] = [WRITE_NO_MPL, WRITE_GOOD]
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed" and sess["task"]["corrections"] == 1
    assert "import matplotlib" in env["prompts"][1]
    assert ai_convert.status(sid, _project(env))["state"] == "ready"


# --------------------------------------------------------------------------- 采用 / 丢弃
def test_adopt_creates_the_file_and_never_overwrites(env):
    figs = env["figs"]
    (figs / "fig_matplotlib.py").write_text("# user's own file\n", encoding="utf-8")
    sid = _run(env)
    _wait(sid)
    made = ai_convert.adopt(sid, _project(env))
    assert made == {"script": "fig_matplotlib_2.py", "name": "fig_matplotlib_2.py"}
    assert (figs / "fig_matplotlib.py").read_text(encoding="utf-8") == "# user's own file\n"
    assert (figs / "fig_matplotlib_2.py").read_text(encoding="utf-8") == GOOD
    # 重入：不再写第二个文件
    assert ai_convert.adopt(sid, _project(env)) == made
    assert sorted(p.name for p in figs.glob("fig_matplotlib*")) == [
        "fig_matplotlib.py",
        "fig_matplotlib_2.py",
    ]
    assert ai_convert.status(sid, _project(env))["state"] == "adopted"


def test_adopt_holds_the_source_guard_between_the_check_and_the_create(env, monkeypatch):
    """核原脚本字节与新建兄弟之间，别处的编辑拿不到原脚本的锁——不会出现「基于旧源的候选被采用」。"""
    sid = _run(env)
    _wait(sid)
    script = env["figs"] / "fig.py"
    entered = threading.Event()
    done = threading.Event()
    seen = {}

    def editor():
        with scriptlock.script_guard(script):
            seen["adopt_finished"] = "adopted" in (ai_convert.status(sid, _project(env)) or {}).get(
                "state", ""
            )
            script.write_bytes(b"# edited elsewhere\n")
        done.set()

    t = threading.Thread(target=editor)
    real = ai_convert.projectenv.contained_path

    def hooked(root, rel):
        # 核校验和之后、新建之前：另一条线程想改原脚本，必须被挡住
        if not entered.is_set():
            entered.set()
            t.start()
            assert not done.wait(0.3), "原脚本的锁没被 adopt 持有"
        return real(root, rel)

    monkeypatch.setattr(ai_convert.projectenv, "contained_path", hooked)
    made = ai_convert.adopt(sid, _project(env))
    t.join(10)
    assert made["script"].endswith(".py") and (env["figs"] / made["script"]).is_file()
    assert seen["adopt_finished"], "编辑线程应在 adopt 释放锁之后才进到锁里"
    # 编辑已落地后再采用另一个会话基线：已变 → convert_source_changed、不新建
    sid2 = _run(env)
    _wait(sid2)
    script.write_bytes(b"# edited again\n")
    before = sorted(p.name for p in env["figs"].iterdir())
    with pytest.raises(ai_convert.ConvertError) as exc:
        ai_convert.adopt(sid2, _project(env))
    assert exc.value.code == "convert_source_changed"
    assert sorted(p.name for p in env["figs"].iterdir()) == before


@pytest.mark.skipif(os.name != "posix", reason="权限位只在 POSIX 上有意义")
def test_adopt_inherits_the_original_scripts_permission_bits(env):
    (env["figs"] / "fig.py").chmod(0o755)
    sid = _run(env)
    _wait(sid)
    made = ai_convert.adopt(sid, _project(env))
    assert (env["figs"] / made["script"]).stat().st_mode & 0o777 == 0o755


def test_adopt_lands_beside_a_script_in_a_subdirectory(env):
    sub = env["figs"] / "sub"
    sub.mkdir()
    (sub / "fig.py").write_bytes(ORIGINAL.encode())  # 按字节写：Windows 文本模式会换成 CRLF
    sid = ai_bridge.run(
        "codex",
        "sub/fig.py",
        "生成",
        str(env["figs"]),
        task_hooks={"runner": ok_runner},
        convert={"library": "pillow", "original_rel": None},
    )
    _wait(sid)
    assert ai_convert.adopt(sid, _project(env))["script"] == "sub/fig_matplotlib.py"
    assert (sub / "fig_matplotlib.py").is_file()


def test_adopt_refuses_when_the_source_script_changed(env):
    sid = _run(env)
    _wait(sid)
    (env["figs"] / "fig.py").write_bytes((ORIGINAL + "# edited\n").encode())
    with pytest.raises(ai_convert.ConvertError) as exc:
        ai_convert.adopt(sid, _project(env))
    assert exc.value.code == "convert_source_changed"
    assert not (env["figs"] / "fig_matplotlib.py").exists()


def test_adopt_refuses_a_tampered_candidate(env):
    sid = _run(env)
    _wait(sid)
    (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").write_text("import os\n", encoding="utf-8")
    with pytest.raises(ai_convert.ConvertError) as exc:
        ai_convert.adopt(sid, _project(env))
    assert exc.value.code == "convert_candidate_changed"
    assert not (env["figs"] / "fig_matplotlib.py").exists()


def test_other_projects_cannot_touch_the_candidate(env, tmp_path):
    sid = _run(env)
    _wait(sid)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ai_convert.ConvertError) as exc:
        ai_convert.adopt(sid, str(other.resolve()))
    assert exc.value.code == "convert_not_found"
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.status("../../etc", str(other))


def test_discard_removes_the_candidate_and_writes_nothing(env):
    sid = _run(env)
    _wait(sid)
    before = _project_files(env["figs"])
    assert ai_convert.discard(sid, _project(env)) == {"ok": True}
    assert not (ai_convert.CONVERT_DIR / sid).exists()
    assert _project_files(env["figs"]) == before
    assert ai_convert.discard(sid, _project(env)) == {"ok": True}, "已不存在也算成功"


def test_expired_candidates_are_pruned(env):
    d = ai_convert.CONVERT_DIR / "aaaaaaaaaaaa"
    d.mkdir(parents=True)
    old = d.stat().st_mtime - ai_convert.RETAIN_S - 10
    os.utime(d, (old, old))
    assert ai_convert.prune_expired() == 1 and not d.exists()


# --------------------------------------------------------------------------- 总开关 / 契约
def test_enabled_by_default_and_the_escape_hatch_turns_it_off(monkeypatch):
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    assert ai_convert.enabled() is True
    monkeypatch.setattr(config, "ai_settings", lambda: {"convert": {"enabled": False}})
    assert ai_convert.enabled() is False
    monkeypatch.setattr(config, "ai_settings", lambda: {"convert": {"enabled": "false"}})
    assert ai_convert.enabled() is True, "只认显式的 False"
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    monkeypatch.setenv("TAVOTTO_NO_CONVERT_TO_EDITABLE", "1")
    assert ai_convert.enabled() is False


def test_envelope_allows_exactly_one_new_file_and_no_user_file_writes(env):
    sid = _run(env)
    _wait(sid)
    row = ai_history.get(sid)
    assert row["envelope"]["output_contract"] == {
        "allowed_writes": ["fig_matplotlib.py"],
        "new_figures": True,
    }
    assert ai_convert.ConvertExecutor.writes_user_files is False
    env_bad = ai_tasks.TaskEnvelope.from_dict(
        {
            "schema_version": 1,
            "task_type": ai_convert.TASK_TYPE,
            "project_ref": {"project_key": "p"},
            "user_intent": "x",
            "execution_policy": {"executor": "codex"},
            "output_contract": {"allowed_writes": ["a.py", "b.py"]},
        }
    )
    assert "exactly_one_new_file" in ai_convert.ADAPTER.validate(env_bad)
    env_path = ai_tasks.TaskEnvelope.from_dict(
        {
            "schema_version": 1,
            "task_type": ai_convert.TASK_TYPE,
            "project_ref": {"project_key": "p"},
            "user_intent": "x",
            "execution_policy": {"executor": "codex"},
            "output_contract": {"allowed_writes": ["sub/a.py"]},
        }
    )
    assert "exactly_one_new_file" in ai_convert.ADAPTER.validate(env_path)


def test_prompt_pack_section_is_closed_over_its_placeholders():
    text = ai_prompt_pack.convert_prompt(
        script="a{x}.py",
        target="a_matplotlib.py",
        library="ROOT",
        reference=None,
        project_root="/p",
    )
    assert "a{x}.py" in text and "ROOT" in text and "reference/" not in text
    assert "保留原脚本的数据读取" in text and "不要运行脚本" in text
    # figure_edit 的 Pack 契约不受影响：它的键集合没有 convert 的模板
    assert not any(k.startswith("fix_no_") for k in ai_prompt_pack.V1["templates"])


def test_image_dimensions_reads_png_jpeg_gif_headers():
    assert ai_convert.image_dimensions(PNG) == (40, 20)
    assert ai_convert.image_dimensions(b"GIF89a" + b"\x0a\x00\x05\x00") == (10, 5)
    jpeg = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x07\x00\x09" + b"\x00" * 10
    assert ai_convert.image_dimensions(jpeg) == (9, 7)
    assert ai_convert.image_dimensions(b"nope") is None


# --------------------------------------------------------------------------- 提交原语：仅新建（O_EXCL）
def test_create_script_requires_the_script_guard(tmp_path):
    p = tmp_path / "c.py"
    with pytest.raises(RuntimeError):
        scriptlock.create_script(p, b"x")
    with scriptlock.script_guard(p):
        scriptlock.create_script(p, b"x")
    assert p.read_bytes() == b"x"
