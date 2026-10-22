"""「生成可编辑版本」的 R / MATLAB 来源（ADR 0143 修订，2026-10-23）：

* 候选 / 校验 / 采用全套沿用，目标文件名 `fig.R` → `fig_matplotlib.py`，`library` 为 None；
* 提示词是同一节的变体（逐步搬运 + 对照表 + 没有可靠对应就停下），Python 来源的提示词不变；
* Agent 没写文件而回答里带 `TAVOTTO_NEEDS_INPUT:` → 结局 `needs_input`（不生成新脚本）；无标记照旧失败；
* 没有原图 → `comparison.reference is None`、`verification == "unknown"`；有原图 → 并排；
* 新脚本缺 Python 包：按 `convertdeps` 的规则自动装（允许的目标）或退回缺依赖卡（不允许的目标），测试一律 monkeypatch 安装入口。

Agent CLI 换成一段 python（不联网）；校验运行由假 runner 代替；`deprepair` 的计划 / 安装入口全部 monkeypatch——不装任何真包。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

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
    deprepair,
    pool,
)

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
    py = ai_prompt_pack.convert_prompt(library="pillow", **kw)
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

    def hook(project, rel):
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
            "deps": lambda p, r: {
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


def test_card_branch_runs_the_candidate_and_missing_dependency_is_not_corrected(env):
    env["runner"] = lambda *a, **k: {
        "error": {"code": "missing_dependency", "message": "No module named 'statsmodels'"}
    }
    sid = _run(env, hooks={"deps": lambda p, r: {"status": "card", "reason": "target_not_allowed"}})
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    assert ("render.capture", "missing_dependency") in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }
    assert sess["task"]["corrections"] == 0 and len(env["prompts"]) == 1


def test_a_crashing_dependency_hook_never_breaks_the_validation(env):
    def boom(project, rel):
        raise RuntimeError("x")

    sid = _run(env, hooks={"deps": boom})
    assert _wait(sid)["task"]["outcome"] == "completed"


# --------------------------------------------------------------------------- convertdeps：自动装的目标与闸
def _plan(requirements=("pandas>=2.0", "statsmodels"), **over):
    base = dict(
        status="ready",
        blocked=(),
        unknown=(),
        not_pip_installable=(),
        requirements=tuple(requirements),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _bound(plan=None, *, requirements=("pandas>=2.0", "statsmodels"), **over):
    base = dict(
        plan_id="p1",
        target_kind=deprepair.TARGET_PROJECT_VENV,
        creates_environment=False,
        private_python=None,
        requirements=tuple(requirements),
        impact_digest="digest-1",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def fake_repair(monkeypatch):
    """替换 `deprepair` 的计划 / 安装入口：记录调用，绝不装包。"""
    st = SimpleNamespace(
        joint=_plan(),
        kind=deprepair.TARGET_PROJECT_VENV,
        source=pool.SOURCE_PROJECT_VENV,
        bound=_bound(),
        prepare_result={"state": deprepair.STATE_DONE},
        prepare_error=None,
        calls=[],
        discarded=[],
    )

    def joint_plan_for(project, script, **kw):
        st.calls.append(("joint_plan_for", project, script))
        return st.joint, st.kind, "/py"

    def joint_target_for(project, script):
        st.calls.append(("joint_target_for", project, script))
        return st.kind, "/py", st.source

    def create_joint_plan(project, script, **kw):
        st.calls.append(("create_joint_plan", project, script, kw))
        if isinstance(st.bound, Exception):
            raise st.bound
        return st.bound

    def prepare(plan_id, on_event=None, *, claimed=False, confirmed_impact=None):
        st.calls.append(("prepare", plan_id, on_event, claimed, confirmed_impact))
        if st.prepare_error is not None:
            raise st.prepare_error
        return st.prepare_result

    monkeypatch.setattr(deprepair, "joint_plan_for", joint_plan_for)
    monkeypatch.setattr(deprepair, "joint_target_for", joint_target_for)
    monkeypatch.setattr(deprepair, "create_joint_plan", create_joint_plan)
    monkeypatch.setattr(deprepair, "prepare", prepare)
    monkeypatch.setattr(deprepair, "_discard_plan", lambda pid: st.discarded.append(pid))
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    monkeypatch.delenv("TAVOTTO_NO_CONVERT_AUTO_INSTALL", raising=False)
    return st


def _names(st):
    return [c[0] for c in st.calls]


def test_auto_install_into_the_projects_own_venv_goes_through_the_existing_prepare_entry(
    fake_repair,
):
    got = convertdeps.ensure("/proj", "tavotto_convert_probe_x.py")
    assert got["status"] == "installed" and got["installed"] == ["pandas", "statsmodels"]
    assert ("prepare", "p1", None, False, "digest-1") in fake_repair.calls
    created = next(c for c in fake_repair.calls if c[0] == "create_joint_plan")
    assert created[1:3] == ("/proj", "tavotto_convert_probe_x.py")
    assert created[3] == {"target_kind": deprepair.TARGET_PROJECT_VENV}


def test_auto_install_into_an_existing_managed_generation_is_allowed(fake_repair):
    fake_repair.kind = deprepair.TARGET_MANAGED
    fake_repair.source = pool.SOURCE_MANAGED_PROJECT
    fake_repair.bound = _bound(target_kind=deprepair.TARGET_MANAGED)
    assert convertdeps.ensure("/proj", "s.py")["status"] == "installed"
    assert "prepare" in _names(fake_repair)


def test_managed_target_that_would_create_an_environment_or_download_python_is_refused(fake_repair):
    fake_repair.kind = deprepair.TARGET_MANAGED
    fake_repair.source = pool.SOURCE_MANAGED_PROJECT
    for over in ({"creates_environment": True}, {"private_python": {"bytes": 1}}):
        fake_repair.calls.clear()
        fake_repair.discarded.clear()
        fake_repair.bound = _bound(target_kind=deprepair.TARGET_MANAGED, **over)
        got = convertdeps.ensure("/proj", "s.py")
        assert (got["status"], got["reason"]) == ("card", "would_create_environment")
        assert "prepare" not in _names(fake_repair) and fake_repair.discarded == ["p1"]


@pytest.mark.parametrize(
    "source",
    [
        pool.SOURCE_BUNDLED,
        pool.SOURCE_CURRENT,
        pool.SOURCE_SYSTEM,
        pool.SOURCE_CONFIGURED,
        pool.SOURCE_ENV,
        pool.SOURCE_MANAGED,
    ],
)
def test_targets_the_existing_rules_do_not_install_into_fall_back_to_the_card(fake_repair, source):
    """内置 runtime / 本进程 / 系统或 Conda / 用户固定的解释器 / 源码模式自建 venv：往受管环境装不会让脚本找到包，
    还会把项目的解释器悄悄记成受管环境——一律退回缺依赖卡，零安装。"""
    fake_repair.kind = deprepair.TARGET_MANAGED
    fake_repair.source = source
    got = convertdeps.ensure("/proj", "s.py")
    assert (got["status"], got["reason"]) == ("card", "target_not_allowed")
    assert "create_joint_plan" not in _names(fake_repair) and "prepare" not in _names(fake_repair)


def test_a_project_venv_kind_with_a_non_venv_source_is_not_allowed(fake_repair):
    fake_repair.source = pool.SOURCE_SYSTEM
    got = convertdeps.ensure("/proj", "s.py")
    assert got["reason"] == "target_not_allowed" and "prepare" not in _names(fake_repair)


@pytest.mark.parametrize("req", [("requests",), ("pandas", "evil-pkg"), ("numpy", "torch>=2")])
def test_packages_outside_the_closed_set_are_never_auto_installed(fake_repair, req):
    fake_repair.joint = _plan(req)
    got = convertdeps.ensure("/proj", "s.py")
    assert (got["status"], got["reason"]) == ("card", "not_in_allowlist")
    assert "create_joint_plan" not in _names(fake_repair) and "prepare" not in _names(fake_repair)


def test_a_bound_plan_that_grew_beyond_the_closed_set_is_discarded_unexecuted(fake_repair):
    fake_repair.bound = _bound(requirements=("pandas", "declared-in-requirements-txt"))
    got = convertdeps.ensure("/proj", "s.py")
    assert got["reason"] == "not_in_allowlist" and fake_repair.discarded == ["p1"]
    assert "prepare" not in _names(fake_repair)


@pytest.mark.parametrize(
    "over",
    [
        {"status": "blocked"},
        {"blocked": ({"reason": "x"},)},
        {"unknown": ("mystery",)},
        {"not_pip_installable": ({"name": "arcpy"},)},
    ],
)
def test_blocked_or_unresolved_plans_fall_back_to_the_card(fake_repair, over):
    fake_repair.joint = _plan(**over)
    got = convertdeps.ensure("/proj", "s.py")
    assert got["status"] == "card" and "prepare" not in _names(fake_repair)


def test_nothing_missing_installs_nothing(fake_repair):
    fake_repair.joint = _plan(status="nothing_needed", requirements=())
    got = convertdeps.ensure("/proj", "s.py")
    assert got["status"] == "none" and "create_joint_plan" not in _names(fake_repair)


def test_the_repair_chains_own_refusals_fall_back_to_the_card(fake_repair):
    fake_repair.bound = deprepair.RepairError(deprepair.ERROR_INTERPRETER_PINNED, "pinned")
    got = convertdeps.ensure("/proj", "s.py")
    assert (got["status"], got["reason"], got["code"]) == (
        "card",
        "plan_refused",
        "dependency_interpreter_pinned",
    )
    assert "prepare" not in _names(fake_repair)


def test_unresolvable_interpreter_falls_back_to_the_card(monkeypatch, fake_repair):
    def boom(*a, **k):
        raise pool.WorkerError("x", "no_worker_python")

    monkeypatch.setattr(deprepair, "joint_plan_for", boom)
    assert convertdeps.ensure("/proj", "s.py")["reason"] == "plan_unavailable"


@pytest.mark.parametrize(
    "result",
    [
        {"state": deprepair.STATE_FAILED, "code": "dependency_network_unavailable"},
        {"state": deprepair.STATE_CANCELLED, "code": "dependency_install_cancelled"},
        None,
    ],
)
def test_install_failure_is_reported_with_the_repair_code(fake_repair, result):
    fake_repair.prepare_result = result
    got = convertdeps.ensure("/proj", "s.py")
    assert got["status"] == "failed" and got["missing"] == ["pandas", "statsmodels"]
    assert got["code"] == ((result or {}).get("code") or deprepair.ERROR_FAILED)


def test_a_raising_prepare_is_a_failure_not_a_crash(fake_repair):
    fake_repair.prepare_error = deprepair.RepairError(deprepair.ERROR_IMPACT_CHANGED, "changed")
    got = convertdeps.ensure("/proj", "s.py")
    assert (got["status"], got["code"]) == ("failed", "dependency_impact_changed")
    fake_repair.prepare_error = RuntimeError("boom")
    assert convertdeps.ensure("/proj", "s.py")["status"] == "failed"


def test_the_switch_turns_auto_install_off_everywhere(fake_repair, monkeypatch):
    monkeypatch.setattr(config, "ai_settings", lambda: {"convert": {"auto_install": False}})
    got = convertdeps.ensure("/proj", "s.py")
    assert (got["status"], got["reason"]) == ("card", "disabled") and not fake_repair.calls
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    monkeypatch.setenv("TAVOTTO_NO_CONVERT_AUTO_INSTALL", "1")
    assert convertdeps.ensure("/proj", "s.py")["reason"] == "disabled" and not fake_repair.calls


def test_auto_install_is_on_by_default_and_only_an_explicit_false_turns_it_off(monkeypatch):
    monkeypatch.setattr(config, "ai_settings", lambda: {})
    assert convertdeps.enabled() is True
    monkeypatch.setattr(config, "ai_settings", lambda: {"convert": {"auto_install": None}})
    assert convertdeps.enabled() is True
    monkeypatch.setattr(config, "ai_settings", lambda: {"convert": {"auto_install": False}})
    assert convertdeps.enabled() is False


def test_the_real_install_entries_are_never_reached_by_these_tests():
    """自检：fixture 替换的正是 convertdeps 调用的那几个入口名，改名会让上面的用例变成空对照。"""
    for name in (
        "joint_plan_for",
        "joint_target_for",
        "create_joint_plan",
        "prepare",
        "_discard_plan",
    ):
        assert callable(getattr(deprepair, name))
    import inspect

    src = inspect.getsource(convertdeps)
    assert "subprocess" not in src, "convertdeps 不新写任何安装 / 子进程路径"
