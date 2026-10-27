"""Harness Lab 基准跑分（ADR 0138 Phase 6b，`scripts/dev/taf_bench.py`）：回放跑通真链路、指标口径、holdout 防泄漏。

跑分本身走真 app + 真桥 + 真 safe worker（只有「理解指令」换成回放），所以这里既是基准工具的自证，也是改图任务端到端
在 FigureBench 上的一次回归：参考解必须 completed + 目标 / 保持全 verified，反例必须被抓住。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "taf_bench", ROOT / "scripts" / "dev" / "taf_bench.py"
)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_replay_runs_the_real_chain_and_negatives_are_caught(tmp_path):
    dev = {s["id"]: s for s in bench.load_scenarios("dev") if bench.benchable(s)}
    picked = [dev["FB-S02"], dev["FB-S03"]]
    report = bench.run(picked, tmp_path / "projects", negatives=True)
    rows = {(r["id"], r["variant"]): r for r in report["rows"]}
    for sid in ("FB-S02", "FB-S03"):
        ref = rows[(sid, "ref")]
        assert ref["outcome"] == "completed" and ref["figure_verdict"] == "verified", ref
        assert ref["goal"] == "verified" and ref["preserve"] == "verified", ref
        assert ref["unintended"] is False and ref["rounds"] == 1, ref
    assert rows[("FB-S02", "n.only_one")]["goal"] == "failed"
    assert rows[("FB-S03", "n.collateral")]["preserve"] == "failed"
    m = report["metrics"]
    assert m["scenarios_run"] == 4 and m["not_run"] == []
    assert m["first_attempt_verified_rate"] == 1.0 and m["negatives_caught_rate"] == 1.0
    assert m["unintended_modification_rate"] == 0.0
    env = report["environment"]
    assert env["pack"] == {"id": "figure_edit", "version": 1} and env["executor"] == "replay"
    json.dumps(report)  # 报告可序列化


def test_metrics_keep_not_run_out_of_the_denominator():
    row = dict.fromkeys(bench.ROW_FIELDS)
    rows = [
        {
            **row,
            "id": "A",
            "variant": "ref",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 1,
            "wall_s": 1.0,
        },
        {
            **row,
            "id": "B",
            "variant": "ref",
            "outcome": "failed",
            "figure_verdict": "unknown",
            "rounds": 1,
            "goal": "unknown",
            "preserve": "unknown",
            "unintended": True,
            "wall_s": 3.0,
        },
        {**row, "id": "C", "variant": "ref", "outcome": None, "unintended": False},  # 没跑起来
        {  # 修正一轮后才成功：不计首发，计最终
            **row,
            "id": "E",
            "variant": "ref",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 2,
            "wall_s": 2.0,
        },
        {
            **row,
            "id": "A",
            "variant": "n.x",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 1,
            "wall_s": 2.0,
        },
    ]
    m = bench._metrics(rows)
    assert m["scenarios_run"] == 4 and m["not_run"] == ["C/ref"]
    # 参考解行 A(首发成功) / B(失败) / E(两轮成功)
    assert m["first_attempt_verified_rate"] == round(1 / 3, 4), "rounds=2 的成功不计首发"
    assert m["final_verified_completion_rate"] == round(2 / 3, 4), "但计最终通过"
    assert m["scientific_preservation_rate"] == round(2 / 3, 4), "unknown 不算保持"
    assert m["unintended_modification_rate"] == round(1 / 3, 4)
    kept_failed = {**rows[0], "id": "F", "preserve": "failed", "unintended": False}
    assert bench._metrics([kept_failed])["unintended_modification_rate"] == 1.0, (
        "保持断言 failed 也算非预期修改（§三口径）"
    )
    assert m["negatives_caught_rate"] == 0.0, "反例没被抓住就是 0，不是跳过"
    assert m["taf_overclaim_rate"] is None, "没有被基准抓住的反例：说多了的比例无从谈起"
    goalless = {**rows[0], "id": "D", "goal": "not_applicable"}
    assert bench._metrics([goalless])["first_attempt_verified_rate"] == 1.0, (
        "没有目标断言 = 目标满足"
    )
    assert bench._metrics([goalless])["final_verified_completion_rate"] == 1.0
    caught = {**rows[-1], "goal": "failed"}
    assert bench._metrics([caught])["taf_overclaim_rate"] == 1.0, (
        "TAF 说 completed、基准判反例违约 = 说多了"
    )


def test_holdout_aggregate_has_no_per_scenario_ids():
    row = dict.fromkeys(bench.ROW_FIELDS)
    rows = [
        {**row, "id": "FB-H99-SECRET", "variant": "ref", "outcome": None, "unintended": False},
        {
            **row,
            "id": "FB-H98-SECRET",
            "variant": "ref",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 1,
            "wall_s": 1.0,
        },
    ]
    m = bench._metrics(rows, aggregate_only=True)
    text = json.dumps(m, ensure_ascii=False)
    assert "FB-H99-SECRET" not in text and "not_run" not in m
    assert m["not_run_count"] == 1


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_same_work_dir_can_be_rerun(tmp_path):
    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    work = tmp_path / "projects"
    for _ in range(2):
        report = bench.run([s02], work)
        assert report["metrics"]["not_run"] == [], report["rows"]
        assert report["rows"][0]["outcome"] == "completed", report["rows"]


def test_only_replayable_dev_scenarios_are_benched_by_default():
    dev = bench.load_scenarios("dev")
    picked = [s for s in dev if bench.benchable(s)]
    assert picked and all(s["split"] == "dev" for s in picked)
    assert not any(s.get("needs_real_model") or s["task_type"] != "figure_edit" for s in picked)
    holdout_ids = {s["id"] for s in bench.load_scenarios("holdout")}
    assert not holdout_ids & {s["id"] for s in picked}


def test_leak_guard_flags_holdout_content_and_passes_clean_text():
    holdout = bench.load_scenarios("holdout")
    some = holdout[0]
    with pytest.raises(bench.LeakError):
        bench.assert_no_leak(["一份候选 Pack", f"提到了 {some['id']}"], holdout)
    with pytest.raises(bench.LeakError):
        bench.assert_no_leak([f"……{some['instruction']}……"], holdout)
    bench.assert_no_leak(
        [
            json.dumps(
                __import__("tavotto.engine.ai_prompt_pack").engine.ai_prompt_pack.V1,
                ensure_ascii=False,
            )
        ],
        holdout,
    )
    markers = bench.holdout_markers(holdout)
    assert all(len(m) >= 4 for m in markers) and some["id"] in markers
    # 每一类都要单独在场：指令原文、断言 id（光靠「碰巧也命中了别的标记」不算守住）
    assert all(s["instruction"] in markers for s in holdout)
    assert all(
        a["id"] in markers
        for s in holdout
        for a in [*s.get("goal", []), *s.get("preserve", [])]
        if len(a["id"]) >= 4
    )


# --------------------------------------------------------------------------- 6c：候选 Pack / 真实执行器的护栏
CANDIDATE = (
    ROOT / "docs" / "implementation" / "ai-foundation" / "packs" / "figure_edit.v2-candidate.json"
)


def test_the_candidate_pack_is_schema_valid_and_holdout_clean():
    from tavotto.engine import ai_prompt_pack

    raw = CANDIDATE.read_text(encoding="utf-8")
    pack = ai_prompt_pack.load(json.loads(raw))
    assert pack.version == 2 and pack.pack_id == "figure_edit"
    bench.assert_no_leak([raw], bench.load_scenarios("holdout"))
    v1 = ai_prompt_pack.V1["templates"]
    differing = [k for k, v in json.loads(raw)["templates"].items() if v != v1[k]]
    assert differing == ["rule_scope"], "候选只改一条，实验才看得出是哪条起的作用"


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_a_candidate_pack_changes_the_prompt_and_the_default_is_restored(tmp_path):
    from tavotto.engine import ai_prompt_pack

    dev = {s["id"]: s for s in bench.load_scenarios("dev") if bench.benchable(s)}
    before = ai_prompt_pack.DEFAULT
    base = bench.run([dev["FB-S02"]], tmp_path / "a")
    cand = bench.run(
        [dev["FB-S02"]], tmp_path / "b", pack=json.loads(CANDIDATE.read_text(encoding="utf-8"))
    )
    assert ai_prompt_pack.DEFAULT is before, "工具只在本进程里临时换 Pack，跑完恢复"
    (ra,), (rb,) = base["rows"], cand["rows"]
    assert (
        ra["prompt_sha256"] and rb["prompt_sha256"] and ra["prompt_sha256"] != rb["prompt_sha256"]
    )
    assert (
        cand["environment"]["pack"]["version"] == 2 and base["environment"]["pack"]["version"] == 1
    )


def test_compare_reports_metric_deltas_and_changed_scenarios():
    row = dict.fromkeys(bench.ROW_FIELDS)
    a = {
        "environment": {"pack": {"version": 1}},
        "metrics": {"first_attempt_verified_rate": 0.5, "not_run": []},
        "rows": [
            {
                **row,
                "id": "S",
                "variant": "ref",
                "outcome": "failed",
                "goal": "failed",
                "preserve": "verified",
            }
        ],
    }
    b = {
        "environment": {"pack": {"version": 2}},
        "metrics": {"first_attempt_verified_rate": 1.0, "not_run": []},
        "rows": [
            {
                **row,
                "id": "S",
                "variant": "ref",
                "outcome": "completed",
                "goal": "verified",
                "preserve": "verified",
            }
        ],
    }
    md = bench.compare(a, b)
    assert "| first_attempt_verified_rate | 0.5 | 1.0 | 0.5 |" in md
    assert "S/ref: failed/failed/verified → completed/verified/verified" in md


def test_real_runs_are_capped_before_anything_starts(tmp_path, monkeypatch, capsys):
    import os

    monkeypatch.setenv("TAVOTTO_DATA_DIR", os.environ.get("TAVOTTO_DATA_DIR", str(tmp_path / "d")))
    monkeypatch.setenv(
        "TAVOTTO_CONFIG_DIR", os.environ.get("TAVOTTO_CONFIG_DIR", str(tmp_path / "c"))
    )
    called = []
    monkeypatch.setattr(bench, "run", lambda *a, **k: called.append(1))
    argv = ["--work", str(tmp_path / "w"), "--real", "codex", "--model", "m"]
    # 2 个场景 × 每任务最多 2 次 CLI 调用 = 4 > 3：按实际调用数拒绝，而不是按场景数（2 ≤ 3）
    assert bench.main([*argv, "--only", "FB-S02,FB-S03", "--max-runs", "3"]) == 2
    assert called == [] and "超过上限" in capsys.readouterr().err


def test_real_mode_refuses_to_run_without_a_pinned_model(tmp_path, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(bench, "run", lambda *a, **k: called.append(1))
    assert bench.main(["--work", str(tmp_path / "w"), "--real", "codex"]) == 2
    assert called == [] and "--model" in capsys.readouterr().err


def test_compare_warns_when_the_two_arms_ran_differently():
    same = {"environment": {"model": "m1", "effort": "low", "executable": "/x", "version": "1"}}
    other = {"environment": {**same["environment"], "model": "m2"}}
    assert "⚠ 两臂执行配置不同" in bench.compare(same, other).splitlines()[0]
    assert "⚠" not in bench.compare(same, same)


def test_settings_are_overlaid_not_replaced(tmp_path, monkeypatch):
    from tavotto.engine import ai_bridge, config

    user = {"providers": {"gw": {"base_url": "https://gw.example"}}, "taf": {"other": 1}}
    monkeypatch.setattr(config, "ai_settings", lambda: user)
    seen = []
    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    real_taf = ai_bridge._taf_enabled
    monkeypatch.setattr(
        ai_bridge, "_taf_enabled", lambda: (seen.append(config.ai_settings()), real_taf())[1]
    )
    bench.run([s02], tmp_path / "projects")
    assert seen, "桥没有读过设置"
    for got in seen:
        assert got["providers"] == user["providers"], "用户的接口配置被丢了"
        assert got["taf"] == {"other": 1, "enabled": True}
    assert user["taf"] == {"other": 1}, "不得原地改用户设置"


def _fake_real_cli(monkeypatch, tmp_path, secret_dir):
    """把 `ai_bridge._cmd` 换成「真实」执行器的替身：参考解放在 work 之外，只记提示词与调用次数。"""
    import sys

    from tavotto.engine import ai_agents, ai_bridge

    box = {"prompts": [], "n": 0}

    def fake_cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        box["n"] += 1
        box["prompts"].append(prompt)
        return [sys.executable, "-c", bench._REPLAY, cwd, str(secret_dir / "edits.json")], {}

    monkeypatch.setattr(ai_bridge, "_cmd", fake_cmd)
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(__import__("os").environ))
    monkeypatch.setattr(
        ai_agents,
        "resolve",
        lambda agent, probe_readiness=True: ai_agents.Resolution(
            argv=["/fake/bin/codex"], path="/fake/bin/codex", version="codex-cli 9.9.9"
        ),
    )
    return box


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_real_mode_never_writes_the_reference_solution_near_the_project(tmp_path, monkeypatch):
    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "edits.json").write_text(json.dumps(s02["reference_solution"]), encoding="utf-8")
    box = _fake_real_cli(monkeypatch, tmp_path, secret)
    work = tmp_path / "run" / "projects"
    report = bench.run([s02], work, real_agent="codex", model="m-pinned", effort="low")
    assert box["n"] >= 1
    assert not list((tmp_path / "run").rglob("*.edits.json")), "真实模式不许生成参考解文件"
    assert not list(work.rglob("*edits*"))
    for e in s02["reference_solution"]:
        assert not any(e["replace"] in p or e["find"] in p for p in box["prompts"]), (
            "提示词带了参考解"
        )
    env = report["environment"]
    assert env["version"] == "codex-cli 9.9.9", "版本取自产品同一份解析，而不是 PATH"
    assert env["executable"] == __import__("sys").executable, "记的是实际 spawn 的 cmd[0]"
    assert (env["model"], env["effort"], env["agent_calls"]) == ("m-pinned", "low", box["n"])


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_real_mode_clears_replay_answers_left_in_a_reused_base(tmp_path, monkeypatch):
    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    work = tmp_path / "run" / "projects"
    bench.run([s02], work)  # 回放：在同一 base 下留下 _replay/*.edits.json
    assert list((tmp_path / "run" / "_replay").glob("*.edits.json"))
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "edits.json").write_text("[]", encoding="utf-8")
    box = _fake_real_cli(monkeypatch, tmp_path, secret)
    seen = []
    inner = __import__("tavotto.engine.ai_bridge", fromlist=["x"])._cmd

    def spy(*a, **k):
        seen.append((tmp_path / "run" / "_replay").exists())
        return inner(*a, **k)

    monkeypatch.setattr(__import__("tavotto.engine.ai_bridge", fromlist=["x"]), "_cmd", spy)
    bench.run([s02], work, real_agent="codex", model="m")
    assert box["n"] >= 1 and seen and not any(seen), "开跑时 _replay 必须已不存在"
    assert not list((tmp_path / "run").rglob("*edits*"))


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_real_mode_refuses_when_an_edits_file_lingers_elsewhere(tmp_path, monkeypatch):
    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "edits.json").write_text("[]", encoding="utf-8")
    box = _fake_real_cli(monkeypatch, tmp_path, secret)
    work = tmp_path / "run" / "projects"
    (tmp_path / "run" / "old" / "deep").mkdir(parents=True)
    (tmp_path / "run" / "old" / "deep" / "FB-S02__ref.edits.json").write_text(
        "[]", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="参考解"):
        bench.run([s02], work, real_agent="codex", model="m")
    assert box["n"] == 0, "拒绝运行时不得调用 CLI"


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_report_records_endpoint_identity_but_never_secrets(tmp_path, monkeypatch):
    from tavotto.engine import ai_bridge

    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "edits.json").write_text("[]", encoding="utf-8")
    _fake_real_cli(monkeypatch, tmp_path, secret)
    rec = {
        "id": "gw1",
        "label": "My GW",
        "agent": "codex",
        "base_url": "https://gw.example.com/v1?token=SECRET-IN-URL",
        "api_key": "sk-SECRET-KEY",
        "token": "TOKEN-SECRET",
    }
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: rec)
    report = bench.run([s02], tmp_path / "run" / "projects", real_agent="codex", model="m")
    ep = report["environment"]["endpoint"]
    assert ep["id"] == "gw1" and ep["host"] == "gw.example.com" and len(ep["fingerprint"]) == 12
    blob = json.dumps(report, ensure_ascii=False)
    for leaked in ("sk-SECRET-KEY", "TOKEN-SECRET", "SECRET-IN-URL"):
        assert leaked not in blob


def test_compare_warns_when_the_endpoint_fingerprint_differs():
    base = {"model": "m1", "effort": "low", "executable": "/x", "version": "1"}
    a = {
        "environment": {**base, "endpoint": {"kind": "provider", "id": "g1", "fingerprint": "aaa"}}
    }
    b = {
        "environment": {**base, "endpoint": {"kind": "provider", "id": "g2", "fingerprint": "bbb"}}
    }
    assert "⚠ 两臂执行配置不同" in bench.compare(a, b).splitlines()[0]
    assert "⚠" not in bench.compare(a, a)


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_real_mode_stops_spawning_once_the_call_cap_is_reached(tmp_path, monkeypatch):
    dev = {s["id"]: s for s in bench.load_scenarios("dev") if bench.benchable(s)}
    picked = [dev["FB-S02"], dev["FB-S03"]]
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "edits.json").write_text("[]", encoding="utf-8")  # 不改脚本 → 目标没达成，会触发修正
    box = _fake_real_cli(monkeypatch, tmp_path, secret)
    report = bench.run(
        picked, tmp_path / "run" / "projects", real_agent="codex", model="m", max_runs=1
    )
    assert box["n"] == 1, "上限到了就不再调用 CLI"
    assert report["environment"]["agent_calls"] == 1
    assert all(r["error"] == "max_runs_reached" for r in report["rows"][-1:]), report["rows"]
    assert any(r["error"] == "max_runs_reached" for r in report["rows"])


def test_a_leaky_candidate_pack_is_refused(tmp_path, monkeypatch, capsys):
    holdout = bench.load_scenarios("holdout")
    leaky = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    leaky["source"] += f"（参考了 {holdout[0]['id']}）"
    path = tmp_path / "leaky.json"
    path.write_text(json.dumps(leaky, ensure_ascii=False), encoding="utf-8")
    called = []
    monkeypatch.setattr(bench, "run", lambda *a, **k: called.append(1))
    assert bench.main(["--work", str(tmp_path / "w"), "--pack", str(path)]) == 2
    assert called == [] and "泄漏" in capsys.readouterr().err


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_repeat_runs_each_scenario_n_times_in_separate_projects(tmp_path):
    dev = {s["id"]: s for s in bench.load_scenarios("dev") if bench.benchable(s)}
    report = bench.run([dev["FB-S02"]], tmp_path / "projects", repeat=2)
    assert [(r["id"], r["rep"]) for r in report["rows"]] == [("FB-S02", 1), ("FB-S02", 2)]
    assert all(r["outcome"] == "completed" for r in report["rows"])
    assert (tmp_path / "projects" / "FB-S02__ref").is_dir() and (
        tmp_path / "projects" / "FB-S02__ref__r2"
    ).is_dir()


def test_the_real_run_cap_counts_repeats(tmp_path, monkeypatch, capsys):
    import os

    monkeypatch.setenv("TAVOTTO_DATA_DIR", os.environ.get("TAVOTTO_DATA_DIR", str(tmp_path / "d")))
    monkeypatch.setenv(
        "TAVOTTO_CONFIG_DIR", os.environ.get("TAVOTTO_CONFIG_DIR", str(tmp_path / "c"))
    )
    called = []
    monkeypatch.setattr(bench, "run", lambda *a, **k: called.append(1))
    per = bench.agent_runs_per_task()
    argv = [
        "--work",
        str(tmp_path / "w"),
        "--real",
        "codex",
        "--model",
        "m",
        "--only",
        "FB-S02",
        "--repeat",
        "3",
    ]
    assert bench.main([*argv, "--max-runs", str(3 * per - 1)]) == 2 and called == []
    assert "重复 3" in capsys.readouterr().err


# --------------------------------------------------------------------------- 内置执行器臂（`--real builtin`，不调真模型）
def _builtin_arm_env(monkeypatch, tmp_path):
    """隔离配置目录 + 假的型号清单（不联网）；`_cmd` 换成回放替身并记下路由结果。"""
    import sys

    from tavotto.engine import ai_bridge, ai_providers, config

    cfg = tmp_path / "cfg"
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(config, "CONFIG_DIR", cfg, raising=False)
    monkeypatch.setattr(
        ai_providers, "live_models", lambda *a, **k: {"source": "none", "reason": "network"}
    )
    # 没有任何 CLI：路由只能凭 TAVOTTO_AI_BUILTIN=1 / Key 连接走 builtin
    monkeypatch.setattr(ai_bridge, "capabilities", lambda refresh=False: {"agents": []})
    box = {"agents": [], "endpoints": [], "models": [], "n": 0, "env": []}
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "edits.json").write_text("[]", encoding="utf-8")

    def fake_cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None, **kw):
        box["n"] += 1
        box["agents"].append(agent_id)
        box["endpoints"].append(endpoint)
        box["models"].append(model)
        box["env"].append(__import__("os").environ.get("TAVOTTO_AI_BUILTIN"))
        return [sys.executable, "-c", bench._REPLAY, cwd, str(secret / "edits.json")], {}

    monkeypatch.setattr(ai_bridge, "_cmd", fake_cmd)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(__import__("os").environ))
    return box


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
@pytest.mark.parametrize(
    "wire,agent,official",
    [
        ("anthropic", "claude", "https://api.anthropic.com"),
        ("openai-responses", "codex", "https://api.openai.com/v1"),
    ],
)
@pytest.mark.parametrize("prefer", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_builtin_arm_routes_through_the_product_to_builtin_and_cleans_up(
    tmp_path, monkeypatch, wire, agent, official, prefer, custom
):
    import os

    from tavotto.engine import ai_bridge, ai_providers, config

    monkeypatch.delenv("TAVOTTO_AI_BUILTIN", raising=False)
    box = _builtin_arm_env(monkeypatch, tmp_path)
    # 可用 CLI 不能掩盖优先开关失效；本进程临时启用必须覆盖原先的 false，结束后恢复。
    monkeypatch.setattr(
        ai_bridge,
        "capabilities",
        lambda refresh=False: {
            "agents": [
                {"id": agent, "installed": True, "enabled": True, "usable": True, "state": "ready"},
            ]
        },
    )
    config.set_ai_settings({"builtin_default": False})
    before = config.ai_settings()
    base = "https://gateway.example.org/v1" if custom else None
    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    report = bench.run(
        [s02],
        tmp_path / "run" / "projects",
        real_agent="builtin",
        model="claude-sonnet-5-5",
        api_key="sk-ant-SECRETSECRETSECRET0123456789",
        builtin_wire=wire,
        base_url=base,
        prefer_builtin=prefer,
    )
    # 产品自己的路由把请求送到了 builtin，连接协议与地址按选择，型号照 --model。
    assert box["agents"] == ["builtin"]
    ep = box["endpoints"][0]
    assert ep["base_url"] == (base or official) and ep["agent"] == agent
    assert ai_providers.builtin_conn(ep)["wire"] == wire
    assert box["models"] == ["claude-sonnet-5-5"] and box["env"] == [None if prefer else "1"]
    env = report["environment"]
    assert env["executor"].startswith("real:builtin") and env["model"] == "claude-sonnet-5-5"
    assert env["wire"] == wire and env["routing"] == ("preferred" if prefer else "forced")
    assert env["endpoint"]["host"] == (
        "gateway.example.org"
        if custom
        else ("api.anthropic.com" if agent == "claude" else "api.openai.com")
    )
    assert "SECRETSECRET" not in json.dumps(report, ensure_ascii=False)
    # 跑完：连接已删、运维开关已还原
    assert ai_providers.list_providers() == []
    assert "TAVOTTO_AI_BUILTIN" not in os.environ
    assert config.ai_settings()["builtin_default"] is before["builtin_default"] is False


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_builtin_acceptance_does_not_credit_a_cli_fallback(tmp_path, monkeypatch):
    from tavotto.engine import ai_bridge, ai_providers

    box = _builtin_arm_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ai_bridge,
        "capabilities",
        lambda refresh=False: {
            "agents": [
                {
                    "id": "claude",
                    "installed": True,
                    "enabled": True,
                    "usable": True,
                    "state": "ready",
                },
            ]
        },
    )
    monkeypatch.setattr(ai_providers, "builtin_conn", lambda rec: None)
    scenario = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    report = bench.run(
        [scenario],
        tmp_path / "projects",
        real_agent="builtin",
        model="test-model",
        api_key="test-key",
        prefer_builtin=True,
    )
    assert box["agents"] == [], "A different executor must not spend quota on builtin acceptance"
    assert report["rows"][0]["outcome"] != "completed"
    assert report["rows"][0]["error"] == "executor_mismatch"
    assert report["environment"]["agent_calls"] == 0
    assert report["environment"]["routed_agents"] == ["claude"]
    assert ai_providers.list_providers() == []


def test_builtin_arm_still_requires_a_pinned_model_and_a_readable_key(
    tmp_path, monkeypatch, capsys
):
    called = []
    monkeypatch.setattr(bench, "run", lambda *a, **k: called.append(1))
    assert bench.main(["--work", str(tmp_path / "w"), "--real", "builtin"]) == 2
    assert "--model" in capsys.readouterr().err
    argv = ["--work", str(tmp_path / "w"), "--real", "builtin", "--model", "m"]
    assert bench.main([*argv, "--key-file", str(tmp_path / "missing")]) == 2
    assert called == []


def test_builtin_arm_key_goes_to_run_only_never_through_argv_or_stdout(
    tmp_path, monkeypatch, capsys
):
    import os

    monkeypatch.setenv("TAVOTTO_DATA_DIR", os.environ.get("TAVOTTO_DATA_DIR", str(tmp_path / "d")))
    monkeypatch.setenv(
        "TAVOTTO_CONFIG_DIR", os.environ.get("TAVOTTO_CONFIG_DIR", str(tmp_path / "c"))
    )
    kf = tmp_path / "key"
    kf.write_text("sk-ant-FILEKEY0123456789\n", encoding="utf-8")
    got = {}
    monkeypatch.setattr(
        bench,
        "run",
        lambda *a, **k: got.update(k) or {"environment": {}, "rows": [], "metrics": {}},
    )
    rc = bench.main(
        [
            "--work",
            str(tmp_path / "w"),
            "--real",
            "builtin",
            "--model",
            "m",
            "--only",
            "FB-S02",
            "--key-file",
            str(kf),
            "--out",
            str(tmp_path / "r.json"),
        ]
    )
    assert (
        rc == 0 and got["api_key"] == "sk-ant-FILEKEY0123456789" and got["real_agent"] == "builtin"
    )
    assert "FILEKEY" not in capsys.readouterr().out + (tmp_path / "r.json").read_text(
        encoding="utf-8"
    )


def test_compare_across_executors_requires_the_same_model_and_effort():
    cli = {
        "environment": {
            "executor": "real:claude 2.1",
            "model": "m",
            "effort": None,
            "executable": "/bin/claude",
            "version": "2.1",
            "endpoint": {"kind": "cli_default"},
        }
    }
    blt = {
        "environment": {
            "executor": "real:builtin tavotto@abc",
            "model": "m",
            "effort": None,
            "executable": "/py",
            "version": "tavotto@abc",
            "endpoint": {"kind": "provider"},
        }
    }
    first = bench.compare(cli, blt).splitlines()[0]
    assert first.startswith("ℹ") and "⚠" not in first  # 执行器不同是预期，模型 / effort 相同
    other = {"environment": {**blt["environment"], "model": "m2"}}
    assert bench.compare(cli, other).splitlines()[0].startswith("⚠ 两臂模型 / effort 不同")


def test_usage_is_taken_only_from_self_reported_events():
    box: dict = {}
    bench._take_usage('{"type":"text","usage":{"input_tokens":9}}', box)  # 非终结事件不计
    bench._take_usage("not json", box)
    assert box == {}
    bench._take_usage(
        '{"type":"done","usage":{"input_tokens":10,"output_tokens":2,"cached_input_tokens":4}}', box
    )
    bench._take_usage(
        '{"type":"result","total_cost_usd":0.5,"usage":{"input_tokens":5,"output_tokens":1,"cache_read_input_tokens":3}}',
        box,
    )
    assert box == {
        "input_tokens": 15,
        "output_tokens": 3,
        "cached_input_tokens": 7,
        "cost_usd": 0.5,
    }


# ---- ADR 0151 PR4：每行记它跑的 AI 会话 id（= Trace 的 task_ref），--exec-trace-out 把基准报告交给只读分析


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_rows_carry_their_session_id_and_the_analysis_links_them_to_the_trace(tmp_path):
    dev = {s["id"]: s for s in bench.load_scenarios("dev") if bench.benchable(s)}
    report = bench.run([dev["FB-S02"]], tmp_path / "work" / "projects")
    (row,) = report["rows"]
    assert row["task_refs"] and all(isinstance(x, str) for x in row["task_refs"])
    assert len(row["task_refs"]) == 1 and "task_refs" in bench.ROW_FIELDS
    out = tmp_path / "analysis.json"
    from tavotto.engine import exectrace

    # `main` 会在 import 前把数据目录设到 --work/data；这里在进程内跑，用记录器实际落盘的那个
    data_dir = exectrace.RECORDER._store.root.parents[1]
    bench.write_exec_trace_analysis(report, data_dir, out)
    analysis = json.loads(out.read_text(encoding="utf-8"))
    linked = [r for r in analysis["rows"] if r["case_id"] == "FB-S02/ref"]
    assert len(linked) == 1, analysis["rows"]
    assert linked[0]["verdict_pass"] is True and linked[0]["verification"] == "verified"
    assert analysis["guardian_results_supplied"] is True


def test_a_row_that_never_got_a_session_has_an_empty_task_refs():
    assert bench._row({"id": "S"}, "ref", error="run 500")["task_refs"] == []


def test_exec_trace_out_is_refused_for_holdout(tmp_path, capsys):
    rc = bench.main(
        ["--holdout", "--exec-trace-out", str(tmp_path / "a.json"), "--work", str(tmp_path / "w")]
    )
    assert rc == 2 and not (tmp_path / "a.json").exists()
    assert "holdout" in capsys.readouterr().err
