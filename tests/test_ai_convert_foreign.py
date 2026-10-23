"""「生成可编辑版本」的 R / MATLAB 来源（ADR 0143 修订，2026-10-23）：

* 候选 / 校验 / 采用全套沿用，目标文件名 `fig.R` → `fig_matplotlib.py`，`library` 为 None；
* 提示词是同一节的变体（逐步搬运 + 对照表 + 没有可靠对应就停下），Python 来源的提示词不变；
* Agent 没写文件而回答里带 `TAVOTTO_NEEDS_INPUT:` → 结局 `needs_input`（不生成新脚本）；无标记照旧失败；
* 没有原图 → `comparison.reference is None`、`verification == "unknown"`；有原图 → 并排；
* 新脚本缺 Python 包：按 `convertdeps` 的规则自动装 / 停住等用户在卡片上决定（候选不删、接着走同一个候选）/ 取消传到安装，测试一律 monkeypatch 安装入口。

Agent CLI 换成一段 python（不联网）；校验运行由假 runner 代替；`deprepair` 的计划 / 安装入口全部 monkeypatch——不装任何真包。
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
    ai_convert,
    ai_figure_edit,
    ai_history,
    ai_prompt_pack,
    ai_tasks,
    config,
    convertdeps,
    pool,
)
from tests.test_convertdeps import _as, _joint, fake_repair, store  # noqa: F401  —— 夹具复用

R_ORIGINAL = 'library(ggplot2)\ndf <- read.csv("data.csv")\nggplot(df, aes(x, y)) + geom_point()\n'
M_ORIGINAL = "% 曲线\nx = linspace(0, 1, 20);\nfigure;\nplot(x, x.^2);\n"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x28\x00\x00\x00\x14\x08\x02\x00\x00\x00"
    + b"\x00" * 16
)
GOOD = (
    "import matplotlib.pyplot as plt\nimport pandas as pd\n"
    "fig, ax = plt.subplots()\nax.plot([1, 2])\nfig.savefig('out.png')\n"
)
WRITE_GOOD = f"(d / 'fig_matplotlib.py').write_text({GOOD!r}, encoding='utf-8')"
NEEDS_REASON = "lme4::lmer 的随机斜率模型在 Python 里没有逐一对应"
SAY_NEEDS_INPUT = (
    "import json; print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', "
    f"'text': 'TAVOTTO_NEEDS_INPUT: {NEEDS_REASON}'}}}}))"
)
SAY_PLAIN = (
    "import json; print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', "
    "'text': '我看了一下，没什么可改的。'}}))"
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
    monkeypatch.delenv("TAVOTTO_NO_CONVERT_AUTO_INSTALL", raising=False)
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig.R").write_bytes(R_ORIGINAL.encode())
    (figs / "fig.m").write_bytes(M_ORIGINAL.encode())
    (figs / "out.png").write_bytes(PNG)
    (figs / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    state = {"code": WRITE_GOOD, "prompts": [], "figs": figs, "runner": ok_runner}

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        state["prompts"].append(prompt)
        pre = "import pathlib, sys; d = pathlib.Path(sys.argv[1]); "
        code = state["codes"].pop(0) if state.get("codes") else state["code"]
        return [sys.executable, "-c", pre + code, cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    yield state
    ai_tasks.unregister(ai_convert.TASK_TYPE)


def _run(env, *, script="fig.R", language="r", original="out.png", runner="default", hooks=None):
    runner = env["runner"] if runner == "default" else runner
    task_hooks = {"runner": runner, **(hooks or {})} if runner else None
    return ai_bridge.run(
        "codex",
        script,
        "生成可编辑的 Matplotlib 版本（R）",
        str(env["figs"]),
        task_hooks=task_hooks,
        convert={"library": None, "source_language": language, "original_rel": original},
    )


def _wait(sid, timeout=20.0):
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(timeout)
        assert not pump.is_alive(), "pump 线程没有收尾"
    return ai_bridge.SESSIONS[sid]


def _project(env) -> str:
    return str(env["figs"].resolve())


def _files(figs: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(figs.iterdir()) if p.is_file()}


# --------------------------------------------------------------------------- 目标名 / 提示词
@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("fig.R", "fig_matplotlib.py"),
        ("fig.r", "fig_matplotlib.py"),
        ("sub/dir/Plot.m", "Plot_matplotlib.py"),
        ("fig.py", "fig_matplotlib.py"),
    ],
)
def test_target_name_for_handles_non_py_suffixes(script, expected):
    assert ai_convert.target_name_for(script) == expected


def test_r_and_matlab_prompts_are_variants_of_the_same_section():
    kw = {"script": "fig.R", "target": "fig_matplotlib.py", "reference": None, "project_root": "/p"}
    r = ai_prompt_pack.convert_prompt(source_language="r", **kw)
    m = ai_prompt_pack.convert_prompt(source_language="matlab", **kw)
    py = ai_prompt_pack.convert_prompt(
        library="root", **kw
    )  # pillow 的对照表自带 NEEDS_INPUT 约定（0143 修订 2026-10-23）
    for text in (r, m):
        assert "TAVOTTO_NEEDS_INPUT:" in text and "不会运行它" in text
        assert "fig_matplotlib.py" in text and "/p" in text, "目标与工作副本说明沿用同一份模板"
    assert "geom_smooth" in r and "lowess" in r and "frac" in r and "statsmodels.formula" in r
    assert "read.csv" in r and "groupby" in r and "facet_wrap" in r
    assert "fitlm" in m and "polyfit" in m and "errorbar" in m and "readtable" in m
    assert "geom_smooth" not in m and "fitlm" not in r
    assert "TAVOTTO_NEEDS_INPUT" not in py, "Python 来源的提示词不带这套变体"
    assert "ROOT / RooFit" in py


def test_prompt_variant_rejects_an_unknown_source_language():
    with pytest.raises(ai_prompt_pack.PackError):
        ai_prompt_pack.convert_prompt(
            script="a", target="b", reference=None, project_root="/p", source_language="julia"
        )


# --------------------------------------------------------------------------- R 的整条流程
def test_r_script_flow_stages_a_python_candidate_and_never_touches_the_original(env):
    before = _files(env["figs"])
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed", sess["task"]
    assert _files(env["figs"]) == before, "R 脚本与项目里一切文件原样"
    cand_dir = ai_convert.CONVERT_DIR / sid
    assert (cand_dir / "fig.R").read_bytes() == R_ORIGINAL.encode(), "候选里复制了原 R 脚本当参考"
    assert (cand_dir / "fig_matplotlib.py").is_file()
    prompt = env["prompts"][0]
    assert "fig.R" in prompt and "reference/out.png" in prompt and "read.csv" in prompt
    st = ai_convert.status(sid, _project(env))
    assert st["source_language"] == "r" and st["library"] is None and st["installed"] == []
    assert st["comparison"]["reference"] == "out.png"
    assert st["comparison"]["verification"] == ai_convert.VERIFICATION_SIDE_BY_SIDE
    sidecar = json.loads((ai_bridge.SNAP_DIR / f"{sid}.json").read_text(encoding="utf-8"))
    assert sidecar["kind"] == "convert" and sidecar["source_language"] == "r", (
        "会话元数据记来源语言"
    )
    made = ai_convert.adopt(sid, _project(env))
    assert made["name"] == "fig_matplotlib.py"
    assert _files(env["figs"])["fig.R"] == R_ORIGINAL.encode()


def test_matlab_script_flow_uses_the_matlab_variant(env):
    sid = _run(env, script="fig.m", language="matlab")
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed", sess["task"]
    assert "fitlm" in env["prompts"][0] and "geom_smooth" not in env["prompts"][0]
    assert ai_convert.status(sid, _project(env))["source_language"] == "matlab"


def test_without_an_original_image_the_comparison_is_honestly_unverified(env):
    sid = _run(env, original=None)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed", sess["task"]
    cmp = ai_convert.status(sid, _project(env))["comparison"]
    assert cmp["reference"] is None and cmp["original"] is None
    assert cmp["verification"] == "unknown"
    assert cmp["pixel_diff"] is None
    assert "reference/" not in env["prompts"][0], "没有原图就不在提示词里提参考图"


# --------------------------------------------------------------------------- needs_input
def test_marker_without_a_new_file_ends_in_needs_input_and_stages_nothing(env):
    env["code"] = SAY_NEEDS_INPUT
    before = _files(env["figs"])
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "needs_input", sess["task"]
    assert sess["status"] == "done"
    assert sess["task"]["unmet"] == [
        {"constraint_id": "intent", "reason_code": "needs_input", "detail": NEEDS_REASON}
    ]
    assert sess["task"]["explain"]["params"]["reason"] == NEEDS_REASON
    assert not (ai_convert.CONVERT_DIR / sid).exists(), "不生成新脚本、候选收尾即删"
    assert _files(env["figs"]) == before
    assert len(env["prompts"]) == 1, "needs_input 是终态：不再发起修正"
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def test_no_marker_and_no_file_is_still_an_ordinary_failure(env):
    env["code"] = SAY_PLAIN
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert ("candidate.target", "target_missing") in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }


def test_marker_next_to_a_written_file_does_not_stop_the_task(env):
    """标记只在“没写新文件”时算数：写了文件就按文件走检查。"""
    env["code"] = SAY_NEEDS_INPUT + "; " + WRITE_GOOD
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed"


def test_marker_after_the_correction_round_also_ends_in_needs_input(env):
    env["codes"] = [SAY_PLAIN, SAY_NEEDS_INPUT]
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "needs_input", sess["task"]
    assert len(env["prompts"]) == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  换\n行\t和  空格  ", "换 行 和 空格"),
        ("**加粗的原因**", "加粗的原因"),
        ("x" * 500, "x" * ai_convert.NEEDS_INPUT_REASON_MAX),
        ("控制\x00字\x1b符", "控制 字 符"),
        ("", ""),
    ],
)
def test_needs_input_reason_is_cleaned_and_bounded(raw, expected):
    assert ai_convert.clean_needs_input_reason(raw) == expected


def test_marker_is_read_from_assistant_messages_only_at_line_start():
    msg = lambda t: {"kind": "message", "text": t}  # noqa: E731
    assert ai_convert.needs_input_reason([msg("TAVOTTO_NEEDS_INPUT: 原因甲")]) == "原因甲"
    assert ai_convert.needs_input_reason([msg("前言\nTAVOTTO_NEEDS_INPUT: 原因乙")]) == "原因乙"
    assert ai_convert.needs_input_reason([msg("说明里提到 TAVOTTO_NEEDS_INPUT: 不算")]) is None
    assert (
        ai_convert.needs_input_reason([{"kind": "action", "text": "TAVOTTO_NEEDS_INPUT: x"}])
        is None
    )
    assert ai_convert.needs_input_reason([msg("没有")]) is None
    assert (
        ai_convert.needs_input_reason(
            [msg("TAVOTTO_NEEDS_INPUT: 先"), msg("TAVOTTO_NEEDS_INPUT: 后")]
        )
        == "后"
    )
    assert ai_convert.needs_input_reason([msg("TAVOTTO_NEEDS_INPUT:")]) == ""


# --------------------------------------------------------------------------- 校验前的依赖步骤
def test_installed_packages_are_recorded_and_the_run_proceeds(env):
    calls = []

    def hook(project, rel, cancel=None):
        calls.append((project, rel))
        return {"status": "installed", "installed": ["pandas", "statsmodels"]}

    sid = _run(env, hooks={"deps": hook})
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed", sess["task"]
    assert len(calls) == 1 and calls[0][1].startswith(ai_convert.STAGE_PREFIX)
    assert ai_convert.status(sid, _project(env))["installed"] == ["pandas", "statsmodels"]


def test_install_failure_is_a_final_failure_without_running_the_candidate(env):
    ran = []
    env["runner"] = lambda *a, **k: ran.append(1) or ok_runner(*a)
    sid = _run(
        env,
        hooks={
            "deps": lambda p, r, c: {
                "status": "failed",
                "code": "dependency_network_unavailable",
                "missing": ["pandas"],
            }
        },
    )
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert not ran, "装不上就不跑候选"
    assert ("render.capture", "dependency_install_failed") in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }
    assert sess["task"]["corrections"] == 0, "改代码解决不了装包失败：不修正"
    assert not (ai_convert.CONVERT_DIR / sid).exists()


def test_skipped_branch_runs_the_candidate_and_missing_dependency_is_not_corrected(env):
    env["runner"] = lambda *a, **k: {
        "error": {"code": "missing_dependency", "message": "No module named 'statsmodels'"}
    }
    sid = _run(env, hooks={"deps": lambda p, r, c: {"status": "skipped", "reason": "plan_blocked"}})
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert ("render.capture", "missing_dependency") in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }
    assert sess["task"]["corrections"] == 0 and len(env["prompts"]) == 1


def test_a_crashing_dependency_hook_never_breaks_the_validation(env):
    def boom(project, rel, cancel=None):
        raise RuntimeError("x")

    sid = _run(env, hooks={"deps": boom})
    assert _wait(sid)["task"]["outcome"] == "completed"


# --------------------------------------------------------------------------- 停住等用户决定（Codex #1330 P1-2）与取消（P1-1）
CARD = {
    "kind": "ask_once",
    "packages": ["pandas", "statsmodels"],
    "outside_list": [],
    "env": {"kind": "conda", "name": "myenv"},
    "action": "install_managed",
    "impact_digest": "digest-abc",
    "target": "tavotto_managed",
    "creates_environment": True,
    "downloads_python": False,
    "modifies_user_environment": False,
}


def _hold_hook(card=None):
    return lambda p, r, c=None: {
        "status": "hold",
        "card": dict(card or CARD),
        "missing": ["pandas"],
    }


def test_hold_ends_needs_input_with_structured_card_and_keeps_the_candidate(env):
    """P1-2：不自动装的分支必须给真卡片——任务以明确、可恢复的结局停住，结构化字段随结局交给前端，候选不删、不重跑 Agent。"""
    before = _files(env["figs"])
    sid = _run(env, hooks={"deps": _hold_hook()})
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "needs_input", sess["task"]
    (unmet,) = sess["task"]["unmet"]
    assert unmet["reason_code"] == "dependency_decision"
    assert unmet["data"] == CARD, (
        "缺哪些包 / 环境可读名 / 是否名单内 / 可执行动作 / 摘要，都在结构化字段里"
    )
    assert len(env["prompts"]) == 1, "停住不是失败：不进修正轮"
    assert _files(env["figs"]) == before, "项目里一个字节没动"
    cdir = ai_convert.CONVERT_DIR / sid
    assert (cdir / "fig_matplotlib.py").is_file(), "候选没删"
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == ai_convert.STATE_AWAITING and st["dependency_card"] == CARD
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def _await_resume(env, sid, *, runner, resolve, timeout=20.0):
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    out = {}
    t = threading.Thread(
        target=lambda: out.update(
            ai_convert.run_resume(
                sid,
                _project(env),
                cdir,
                meta,
                cancel,
                runner=runner,
                resolve=convertdeps.resolve,
                confirmed_impact="digest-abc",
            )
        )
    )
    return t, out, cancel


def test_resume_continues_the_same_candidate_without_rerunning_the_agent(env, monkeypatch):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    seen = []

    def resolve(project, rel, *, confirmed_impact, cancel=None):
        seen.append((rel, confirmed_impact))
        return {"status": "installed", "installed": ["pandas", "statsmodels"]}

    monkeypatch.setattr(convertdeps, "resolve", resolve)
    t, out, _cancel = _await_resume(env, sid, runner=ok_runner, resolve=resolve)
    t.start()
    t.join(20)
    assert out["state"] == "ready", out
    assert seen and seen[0][0].startswith(ai_convert.STAGE_PREFIX) and seen[0][1] == "digest-abc"
    assert len(env["prompts"]) == 1, "没有第二次 Agent 调用"
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == "ready" and st["installed"] == ["pandas", "statsmodels"]
    assert st["dependency_card"] is None
    assert ai_convert.adopt(sid, _project(env))["name"] == "fig_matplotlib.py"
    assert not ai_convert.resuming(sid)


def test_resume_goes_back_to_the_card_when_the_summary_no_longer_matches(env, monkeypatch):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    fresh = {**CARD, "impact_digest": "digest-new"}
    monkeypatch.setattr(
        convertdeps,
        "resolve",
        lambda p, r, **kw: {
            "status": "hold",
            "code": "dependency_impact_changed",
            "card": fresh,
        },
    )
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    t.join(20)
    assert out["state"] == ai_convert.STATE_AWAITING
    card = ai_convert.status(sid, _project(env))["dependency_card"]
    assert card["impact_digest"] == "digest-new" and card["notice"] == "dependency_impact_changed"


def test_resume_failure_is_recorded_with_a_reason(env, monkeypatch):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    monkeypatch.setattr(
        convertdeps,
        "resolve",
        lambda p, r, **kw: {
            "status": "failed",
            "code": "dependency_network_unavailable",
            "missing": ["pandas"],
        },
    )
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    t.join(20)
    assert out["state"] == "failed" and out["reason"] == "dependency_install_failed"
    st = ai_convert.status(sid, _project(env))
    assert (
        st["state"] == "failed"
        and st["failure"]["installer_code"] == "dependency_network_unavailable"
    )


def test_closing_the_card_during_resume_cancels_the_install_and_drops_the_candidate(
    env, monkeypatch
):
    """P1-1：关闭 / 停止卡片 → discard → 取消标记传到正在进行的安装；安装停手后候选目录被删，不会变成 ready。"""
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    started = threading.Event()
    got = {}

    def slow_resolve(project, rel, *, confirmed_impact, cancel=None):
        got["cancel"] = cancel
        started.set()
        cancel.wait(10)
        return {
            "status": "cancelled",
            "code": "dependency_install_cancelled",
            "missing": ["pandas"],
        }

    monkeypatch.setattr(convertdeps, "resolve", slow_resolve)
    ran = []
    t, out, _c = _await_resume(
        env, sid, runner=lambda *a, **k: ran.append(1) or ok_runner(*a), resolve=None
    )
    t.start()
    assert started.wait(10)
    assert ai_convert.status(sid, _project(env))["state"] == ai_convert.STATE_RESUMING
    assert ai_convert.discard(sid, _project(env)) == {"ok": True}
    t.join(20)
    assert got["cancel"].is_set(), "取消传到了安装入口"
    assert out["state"] == "cancelled" and not ran, "取消后不跑校验"
    assert not (ai_convert.CONVERT_DIR / sid).exists()


def _slow_prepare(monkeypatch, *, past_commit):
    """假 deprepair.prepare：登记取消句柄后挂起，直到测试放行；`past_commit` = 先把计划记成已过提交点（取消会被 cancel_status 拒绝）。"""
    from tavotto.engine import deprepair

    entered, release = threading.Event(), threading.Event()
    seen = {}

    def prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        deprepair._register_cancel(plan_id)
        if past_commit:
            monkeypatch.setitem(deprepair._committed, plan_id, 1e18)  # 测试结束还原，不串到别的用例
        seen["plan"] = plan_id
        entered.set()
        release.wait(10)
        with deprepair._lock:
            ev = deprepair._cancels.get(plan_id)
        if ev is not None and ev.is_set():
            return {"state": deprepair.STATE_CANCELLED}
        return {"state": deprepair.STATE_DONE}

    monkeypatch.setattr(deprepair, "prepare", prepare)
    return entered, release, seen


def _resume_real_deps(env, sid):
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    out = {}
    ran = []
    t = threading.Thread(
        target=lambda: out.update(
            ai_convert.run_resume(
                sid,
                _project(env),
                cdir,
                meta,
                cancel,
                runner=lambda *a, **k: ran.append(1) or ok_runner(*a),
                resolve=convertdeps.resolve,
                confirmed_impact="digest-1",
            )
        )
    )
    return t, out, ran


def test_a_stop_after_the_commit_point_keeps_the_candidate_and_waits_for_continue(
    env,
    fake_repair,  # noqa: F811 —— 夹具复用
    tmp_path,
    monkeypatch,
):
    """Codex #1330 P2：慢 prepare 过了提交点后用户点停止 → 修复层拒绝取消、环境已换代，convertdeps 回 installed。
    续跑以安装结果为准：候选保留、状态 deps_installed（已装好待继续）、安装如实记录、不自动跑校验；继续（replan 入口）后正常 ready。"""
    from tavotto.engine import deprepair, pool

    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    entered, release, _seen = _slow_prepare(monkeypatch, past_commit=True)
    t, out, ran = _resume_real_deps(env, sid)
    t.start()
    assert entered.wait(10)
    assert ai_convert.discard(sid, _project(env)) == {
        "ok": True
    }  # 停止：取消标记置位，安装已过提交点
    release.set()
    t.join(20)
    assert out["state"] == ai_convert.STATE_INSTALLED, out
    assert out["installed"], "环境写入如实记录"
    assert not ran, "不违背停止意图：不自动跑校验"
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == ai_convert.STATE_INSTALLED and st["installed"] == out["installed"]
    assert st["dependency_card"] is None
    assert (ai_convert.CONVERT_DIR / sid).exists(), "候选保留"
    assert not ai_convert.resuming(sid)
    with pytest.raises(ai_convert.ConvertError):  # 还没校验过，不能采用
        ai_convert.adopt(sid, _project(env))
    # 用户点「继续」：走 replan 入口，已不缺包，校验同一个候选 → ready
    monkeypatch.setattr(convertdeps, "ensure", lambda p, r, cancel=None: {"status": "none"})
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    res = ai_convert.run_resume(
        sid, _project(env), cdir, meta, cancel, runner=ok_runner, ensure=convertdeps.ensure
    )
    assert res["state"] == "ready"
    assert deprepair is not None


def test_a_stop_before_the_commit_point_still_cancels_and_drops_the_candidate(
    env,
    fake_repair,  # noqa: F811 —— 夹具复用
    tmp_path,
    monkeypatch,
):
    """未过提交点的取消：prepare 的取消检查点停手 → cancelled，候选删，不跑校验（原有语义不变）。"""
    from tavotto.engine import pool

    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    _as(fake_repair, tmp_path, source=pool.SOURCE_CONFIGURED)
    entered, release, _seen = _slow_prepare(monkeypatch, past_commit=False)
    t, out, ran = _resume_real_deps(env, sid)
    t.start()
    assert entered.wait(10)
    ai_convert.discard(sid, _project(env))
    import time

    time.sleep(0.3)  # 让监视线程把取消送进 cancel_status
    release.set()
    t.join(20)
    assert out["state"] == "cancelled" and not ran
    assert not (ai_convert.CONVERT_DIR / sid).exists()


def test_a_second_click_while_resuming_is_refused(env, monkeypatch):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    started = threading.Event()

    def slow(project, rel, *, confirmed_impact, cancel=None):
        started.set()
        cancel.wait(10)
        return {"status": "cancelled"}

    monkeypatch.setattr(convertdeps, "resolve", slow)
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    assert started.wait(10)
    with pytest.raises(ai_convert.ConvertError) as exc:
        ai_convert.prepare_resume(sid, _project(env))
    assert exc.value.code in ("convert_resume_busy", "convert_not_awaiting_dependencies")
    ai_convert.discard(sid, _project(env))
    t.join(20)


def test_resume_refuses_a_candidate_whose_files_changed_since_the_hold(env):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").write_text("print(1)\n", encoding="utf-8")
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    t.join(20)
    assert out["state"] == "failed" and out["reason"] == "convert_candidate_changed"


def test_task_cancel_reaches_the_dependency_step_and_ends_cancelled(env):
    """P1-1：任务取消的标记被交给依赖钩子；钩子（= 正在进行的安装）收到信号，之后候选既不提交也不留存。"""
    entered = threading.Event()
    seen = {}

    def hook(project, rel, cancel=None):
        seen["cancel"] = cancel
        entered.set()
        assert cancel is not None and cancel.wait(10), "取消信号没到"
        return {"status": "cancelled", "code": "dependency_install_cancelled"}

    sid = _run(env, hooks={"deps": hook})
    assert entered.wait(15)
    assert ai_bridge.cancel(sid)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "cancelled", sess["task"]
    assert seen["cancel"].is_set()
    assert not (ai_convert.CONVERT_DIR / sid).exists()
    assert len(env["prompts"]) == 1


@pytest.mark.parametrize("kind", ["system", "uncommon", "ask_once"])
def test_a_stop_during_a_plain_dependency_wait_wins_and_drops_the_candidate(env, kind):
    """Codex #1330 P2：普通依赖等待（system / uncommon / ask_once，环境没被动）里停止已置位 → 取消赢：
    任务 cancelled、候选删除、不留 awaiting_dependencies；不再以 needs_input 终局把已停止的转换藏起来。"""
    entered = threading.Event()

    def hook(project, rel, cancel=None):
        entered.set()
        assert cancel is not None and cancel.wait(10), "取消信号没到"
        return {"status": "hold", "card": {**CARD, "kind": kind}, "missing": ["pandas"]}

    sid = _run(env, hooks={"deps": hook})
    assert entered.wait(15)
    assert ai_bridge.cancel(sid)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "cancelled", sess["task"]
    assert not (ai_convert.CONVERT_DIR / sid).exists(), "候选删除"


def test_a_stop_after_the_install_commit_point_in_the_initial_task_keeps_the_candidate(
    env, monkeypatch
):
    """Codex #1330 P2（首跑）：依赖步骤已过安装提交点（返回 installed）而用户的停止已置位 → 与续跑同一语义：
    候选保留、状态 deps_installed、installed 如实记录、runner 没调用；任务结局 needs_input + dependency_installed_late
    （带 installed 结构化字段），不是 cancelled；之后「继续」→ ready。"""
    entered = threading.Event()
    ran = []
    env["runner"] = lambda *a, **k: ran.append(1) or ok_runner(*a)

    def hook(project, rel, cancel=None):
        entered.set()
        assert cancel is not None and cancel.wait(10), "取消信号没到"
        return {"status": "installed", "installed": ["pandas"]}  # 修复层拒绝了晚到的取消

    sid = _run(env, hooks={"deps": hook})
    assert entered.wait(15)
    assert ai_bridge.cancel(sid)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "needs_input", sess["task"]
    (unmet,) = sess["task"]["unmet"]
    assert unmet["reason_code"] == "dependency_installed_late"
    assert unmet["data"] == {"installed": ["pandas"]}
    assert not ran, "不违背停止意图：不跑校验"
    assert len(env["prompts"]) == 1
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == ai_convert.STATE_INSTALLED and st["installed"] == ["pandas"]
    assert st["dependency_card"] is None
    assert (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").is_file(), "候选保留"
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))
    monkeypatch.setattr(convertdeps, "ensure", lambda p, r, cancel=None: {"status": "none"})
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    res = ai_convert.run_resume(
        sid, _project(env), cdir, meta, cancel, runner=ok_runner, ensure=convertdeps.ensure
    )
    assert res["state"] == "ready"
    assert ai_convert.status(sid, _project(env))["installed"] == ["pandas"]


# --------------------------------------------------------------------------- 换环境后按新环境重新判定（Codex #1330 P1：系统卡 → 选了别的环境，不用丢候选重跑付费 Agent）
SYSTEM_CARD = {
    **CARD,
    "kind": "system",
    "action": "choose_env",
    "env": {"kind": "system", "name": "Python"},
    "impact_digest": "",
}


def _replan(env, sid, *, timeout=20.0):
    """走与 `/dependencies {action: replan}` 相同的入口：`prepare_resume` 登记 → `run_resume(ensure=convertdeps.ensure)`。"""
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    out = {}
    t = threading.Thread(
        target=lambda: out.update(
            ai_convert.run_resume(
                sid,
                _project(env),
                cdir,
                meta,
                cancel,
                runner=ok_runner,
                ensure=convertdeps.ensure,
            )
        )
    )
    t.start()
    t.join(timeout)
    return out


@pytest.fixture
def held_on_system(env, fake_repair, tmp_path):  # noqa: F811
    """候选已经因“系统 Python”停在等用户；之后各用例再把项目解释器换成别的。"""
    sid = _run(env, hooks={"deps": _hold_hook(SYSTEM_CARD)})
    _wait(sid)
    return sid, fake_repair, tmp_path


def test_replan_after_switching_to_a_project_venv_installs_and_continues_the_same_candidate(
    env, held_on_system
):
    sid, st, _tmp = held_on_system
    # 默认夹具 = 项目 venv：此刻解释器已不是系统 Python
    out = _replan(env, sid)
    assert out["state"] == "ready", out
    assert "prepare" in [c[0] for c in st.calls], "项目 venv 在名单内：直接装"
    assert len(env["prompts"]) == 1, "没有第二次 Agent 调用"
    stat = ai_convert.status(sid, _project(env))
    assert stat["state"] == "ready" and stat["installed"] == ["pandas", "statsmodels"]
    assert ai_convert.adopt(sid, _project(env))["name"] == "fig_matplotlib.py"


def test_replan_after_switching_to_conda_comes_back_with_the_ask_once_card(env, held_on_system):
    sid, st, tmp = held_on_system
    _as(st, tmp, source=pool.SOURCE_SYSTEM, conda=True)
    out = _replan(env, sid)
    assert out["state"] == ai_convert.STATE_AWAITING, out
    card = ai_convert.status(sid, _project(env))["dependency_card"]
    assert card["kind"] == "ask_once" and card["env"] == {"kind": "conda", "name": "myenv"}
    assert "prepare" not in [c[0] for c in st.calls], "问之前一个字节不装"
    assert len(env["prompts"]) == 1 and (ai_convert.CONVERT_DIR / sid).is_dir(), "候选还在"


def test_replan_when_the_new_environment_is_no_longer_missing_anything_just_continues(
    env, held_on_system
):
    sid, st, _tmp = held_on_system
    st.joint = _joint(status="nothing_needed", requirements=())
    out = _replan(env, sid)
    assert out["state"] == "ready", out
    assert "prepare" not in [c[0] for c in st.calls] and "create_joint_plan" not in [
        c[0] for c in st.calls
    ]
    assert len(env["prompts"]) == 1


def test_replan_while_still_on_the_system_python_returns_the_system_card_again(env, held_on_system):
    sid, st, tmp = held_on_system
    _as(st, tmp, source=pool.SOURCE_SYSTEM, ext=True)
    out = _replan(env, sid)
    assert out["state"] == ai_convert.STATE_AWAITING, out
    card = ai_convert.status(sid, _project(env))["dependency_card"]
    assert card["kind"] == "system" and card["action"] == "choose_env"
    assert "prepare" not in [c[0] for c in st.calls]
    assert len(env["prompts"]) == 1


# --------------------------------------------------------------------------- 孤儿续跑（Codex #1330 P2）
def _persist_resuming(sid, **extra):
    cdir = ai_convert.CONVERT_DIR / sid
    meta = json.loads((cdir / ai_convert.META_NAME).read_text(encoding="utf-8"))
    meta.update({"state": ai_convert.STATE_RESUMING, **extra})
    (cdir / ai_convert.META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return cdir


def _persisted_state(sid):
    cdir = ai_convert.CONVERT_DIR / sid
    return json.loads((cdir / ai_convert.META_NAME).read_text(encoding="utf-8"))["state"]


def test_orphan_resume_after_restart_is_rolled_back_in_meta_and_can_be_continued(env, monkeypatch):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    _persist_resuming(sid)
    assert not ai_convert.resuming(sid), "模拟重启：进程内没有续跑登记"
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == ai_convert.STATE_AWAITING and st["dependency_card"] == CARD
    assert _persisted_state(sid) == ai_convert.STATE_AWAITING, (
        "不只是读出来像 awaiting，meta 已持久化回滚"
    )
    monkeypatch.setattr(
        convertdeps,
        "resolve",
        lambda p, r, **kw: {"status": "installed", "installed": ["pandas"]},
    )
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    t.join(20)
    assert out["state"] == "ready", out


def test_prepare_resume_alone_reconciles_an_orphan_without_a_prior_get(env):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    _persist_resuming(sid)
    _cdir, meta, _ev = ai_convert.prepare_resume(sid, _project(env))
    assert meta["state"] == ai_convert.STATE_AWAITING
    ai_convert.cancel_registration(sid)


def test_orphan_resume_with_installed_record_rolls_back_to_deps_installed(env):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    _persist_resuming(sid, installed=["pandas"])
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == ai_convert.STATE_INSTALLED and st["installed"] == ["pandas"]
    assert _persisted_state(sid) == ai_convert.STATE_INSTALLED
    _cdir, _meta, _ev = ai_convert.prepare_resume(sid, _project(env))
    ai_convert.cancel_registration(sid)


def test_a_really_running_resume_is_not_rolled_back(env):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    _persist_resuming(sid)
    with ai_convert._RESUMES_LOCK:
        ai_convert._RESUMES[sid] = threading.Event()
    try:
        st = ai_convert.status(sid, _project(env))
        assert st["state"] == ai_convert.STATE_RESUMING
        assert _persisted_state(sid) == ai_convert.STATE_RESUMING
        with pytest.raises(ai_convert.ConvertError) as exc:
            ai_convert.prepare_resume(sid, _project(env))
        assert exc.value.code in ("convert_resume_busy", "convert_not_awaiting_dependencies")
        assert _persisted_state(sid) == ai_convert.STATE_RESUMING
    finally:
        ai_convert.cancel_registration(sid)


# --------------------------------------------------------------------------- 完整性闸门先于依赖等待（Codex #1330 P1）
def test_project_changed_during_the_dependency_survey_never_enters_the_hold(env):
    """依赖调查期间别的进程改了项目里另一个文件：不进依赖等待、结局是完整性失败、候选不留存（不是 needs_input）。"""

    def hook(project, rel, cancel=None):
        (env["figs"] / "other.csv").write_text("changed-by-someone\n", encoding="utf-8")
        return _hold_hook()(project, rel, cancel)

    sid = _run(env, hooks={"deps": hook})
    sess = _wait(sid)
    task = sess["task"]
    assert task["outcome"] != "needs_input", task
    assert any(u["constraint_id"] == "project.untouched" for u in task["unmet"]), task["unmet"]
    assert not (ai_convert.CONVERT_DIR / sid).exists(), "候选不留存"
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.status(sid, _project(env))


def test_resume_fails_and_drops_the_candidate_when_a_project_file_changed_after_the_hold(env):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    (env["figs"] / "other.csv").write_text("changed-after-hold\n", encoding="utf-8")
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    t.join(20)
    assert out["state"] == "failed" and out["reason"] == "convert_project_changed", out
    cdir = ai_convert.CONVERT_DIR / sid
    assert not (cdir / "fig_matplotlib.py").exists(), "候选脚本已删"
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def test_resume_fails_and_drops_the_candidate_when_the_original_script_changed_after_the_hold(env):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    (env["figs"] / "fig.R").write_text("# edited\n", encoding="utf-8")
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    t.join(20)
    assert out["state"] == "failed" and out["reason"] == "convert_source_changed", out
    assert not (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").exists()
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def test_resume_baseline_is_the_one_taken_at_start_and_a_missing_one_fails_closed(env):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    meta_path = ai_convert.CONVERT_DIR / sid / ai_convert.META_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert (
        isinstance(meta["project_before"], dict) and meta["project_before"].get("files") is not None
    )
    del meta["project_before"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    t.join(20)
    assert out["state"] == "failed" and out["reason"] == "convert_project_changed", out


def test_resume_fails_closed_when_the_project_outgrows_the_fingerprint_limit_after_the_hold(
    env, monkeypatch
):
    """Codex #1330 P2：停住时测得出、续跑时超限 → `fingerprint_changes` 回 None，按“已变化”拒：不装、不跑候选、删候选。"""
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    monkeypatch.setattr(ai_figure_edit, "FINGERPRINT_MAX_FILES", 1)  # 项目此刻“长过上限”
    installed = []

    def resolve(project, rel, *, confirmed_impact, cancel=None):
        installed.append(rel)
        return {"status": "installed", "installed": ["pandas"]}

    monkeypatch.setattr(convertdeps, "resolve", resolve)
    ran = []

    def runner(figures_dir, rel, write_roots=None):
        ran.append(rel)
        return ok_runner(figures_dir, rel, write_roots)

    t, out, _c = _await_resume(env, sid, runner=runner, resolve=resolve)
    t.start()
    t.join(20)
    assert out["state"] == "failed" and out["reason"] == "convert_project_changed", out
    assert not installed and not ran, "任何动作之前就拒"
    assert not (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").exists(), "候选已删"
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def _stall_before_ready(monkeypatch):
    reached, go = threading.Event(), threading.Event()

    def hook(sid):
        reached.set()
        assert go.wait(10)

    monkeypatch.setattr(ai_convert, "_before_ready_stage", hook)
    return reached, go


def test_a_discard_arriving_just_before_ready_wins_and_the_candidate_never_becomes_ready(
    env, monkeypatch
):
    """Codex #1330 P2：校验通过、落 ready 之前到达的 discard → 结局 cancelled、候选删、绝不 ready。"""
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    monkeypatch.setattr(convertdeps, "resolve", lambda *a, **k: {"status": "none"})
    reached, go = _stall_before_ready(monkeypatch)
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    assert reached.wait(10)
    assert ai_convert.discard(sid, _project(env)) == {"ok": True}
    go.set()
    t.join(20)
    assert out["state"] == "cancelled", out
    assert not (ai_convert.CONVERT_DIR / sid).exists()
    assert not ai_convert.resuming(sid)


def test_without_a_discard_the_stalled_resume_becomes_ready_and_a_later_discard_deletes_it(
    env, monkeypatch
):
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    monkeypatch.setattr(convertdeps, "resolve", lambda *a, **k: {"status": "none"})
    reached, go = _stall_before_ready(monkeypatch)
    t, out, _c = _await_resume(env, sid, runner=ok_runner, resolve=None)
    t.start()
    assert reached.wait(10)
    go.set()
    t.join(20)
    assert out["state"] == "ready", out
    assert ai_convert.status(sid, _project(env))["state"] == "ready"
    assert ai_convert.discard(sid, _project(env)) == {"ok": True}  # 已 ready 的候选：照常删
    assert not (ai_convert.CONVERT_DIR / sid).exists()


# --------------------------------------------------------------------------- 自动装依赖写项目自己的 .venv 不算改了项目（Codex #1330 P1）
def _project_venv(env):
    venv = env["figs"] / ".venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
    return venv


def test_installing_into_the_project_venv_does_not_reject_a_valid_candidate(env):
    venv = _project_venv(env)

    def hook(project, rel, cancel=None):
        (venv / "lib" / "pandas.py").write_text("installed\n", encoding="utf-8")
        (venv / "pyvenv.cfg").write_text("home = /y\n", encoding="utf-8")
        return {"status": "installed", "installed": ["pandas"]}

    sid = _run(env, hooks={"deps": hook})
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed", sess["task"]
    assert not any(u["constraint_id"] == "project.untouched" for u in sess["task"]["unmet"])


def test_resume_after_installing_into_the_project_venv_is_not_rejected(env, monkeypatch):
    venv = _project_venv(env)
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)

    def resolve(project, rel, *, confirmed_impact, cancel=None):
        (venv / "lib" / "pandas.py").write_text("installed\n", encoding="utf-8")
        return {"status": "installed", "installed": ["pandas"]}

    monkeypatch.setattr(convertdeps, "resolve", resolve)
    t, out, _cancel = _await_resume(env, sid, runner=ok_runner, resolve=resolve)
    t.start()
    t.join(20)
    assert out["state"] == "ready", out


def test_a_dot_venv_without_pyvenv_cfg_is_still_a_project_change(env):
    fake = env["figs"] / ".venv"
    fake.mkdir()

    def hook(project, rel, cancel=None):
        (fake / "x.txt").write_text("changed\n", encoding="utf-8")
        return {"status": "installed", "installed": ["pandas"]}

    sid = _run(env, hooks={"deps": hook})
    sess = _wait(sid)
    assert any(u["constraint_id"] == "project.untouched" for u in sess["task"]["unmet"]), sess[
        "task"
    ]["unmet"]


def test_a_stop_on_a_resume_whose_survey_finds_nothing_to_install_never_starts_the_validation_run(
    env, monkeypatch
):
    """Codex #1330 P2：续跑里依赖调研回 none / 不缺包，而取消已置位——不得落到不可取消的校验运行，候选删掉。"""
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    monkeypatch.setattr(convertdeps, "resolve", lambda *a, **k: {"status": "none"})
    ran = []
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    cancel.set()  # Stop 在调研返回前就到了
    out = ai_convert.run_resume(
        sid,
        _project(env),
        cdir,
        meta,
        cancel,
        runner=lambda *a, **k: ran.append(1) or ok_runner(*a),
        resolve=convertdeps.resolve,
        confirmed_impact="digest-abc",
    )
    assert out == {"state": "cancelled"}
    assert ran == [], "取消已置位：校验 runner 不许启动"
    assert not cdir.exists()


def test_a_stop_during_a_running_validation_force_cancels_the_probe(env, monkeypatch):
    """取消在校验运行中到来：runner 的 force_cancel（= pool.force_cancel 同键硬杀）被调用，结局 cancelled、候选删掉。"""
    sid = _run(env, hooks={"deps": _hold_hook()})
    _wait(sid)
    monkeypatch.setattr(convertdeps, "resolve", lambda *a, **k: {"status": "none"})
    started, killed = threading.Event(), threading.Event()

    def slow_runner(figures_dir, rel, write_roots=None):
        started.set()
        killed.wait(10)  # 被硬杀前一直阻塞（真实 probe 在 EOF 时回 error）
        return {"error": {"code": "worker_died", "message": "killed"}}

    forced = []
    slow_runner.force_cancel = lambda fd, rel, roots: forced.append((rel, roots)) or killed.set()
    t, out, cancel = _await_resume(env, sid, runner=slow_runner, resolve=None)
    t.start()
    assert started.wait(10)
    cancel.set()
    t.join(20)
    assert out == {"state": "cancelled"}, out
    assert len(forced) == 1 and forced[0][0].startswith(ai_convert.STAGE_PREFIX)
    assert not (ai_convert.CONVERT_DIR / sid).exists()


def test_cancel_resume_sets_the_event_while_holding_the_registry_lock():
    """discard 与续跑落 ready 之间不许有空隙：取消标记必须在 _RESUMES_LOCK 内置位（Codex #1330 第十二轮）。"""
    import threading as _t

    from tavotto.engine import ai_convert as ac

    seen = []

    class Probe(_t.Event):
        def set(self):
            got = []

            def try_take():
                ok = ac._RESUMES_LOCK.acquire(blocking=False)
                got.append(ok)
                if ok:
                    ac._RESUMES_LOCK.release()

            th = _t.Thread(target=try_take)
            th.start()
            th.join()
            seen.append(not got[0])  # 别的线程拿不到锁 = 置位时锁被持有
            super().set()

    sid = "s-lockcheck"
    with ac._RESUMES_LOCK:
        ac._RESUMES[sid] = Probe()
    try:
        assert ac._cancel_resume(sid) is True
    finally:
        with ac._RESUMES_LOCK:
            ac._RESUMES.pop(sid, None)
    assert seen == [True]
