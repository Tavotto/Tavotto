"""V2 PR1：执行策略与统一重试闸门（`engine/execpolicy.py`，ADR 0127）的合同——纯逻辑，不起 worker。

* 策略：项目设置键 `execution_policy`（缺省 `compatible`、`safe_only` 要被尊重、坏值按默认）；运维开关
  `TAVOTTO_EXECUTION_FALLBACK=0`（环境变量优先于全局设置）；
* 根因：复用已有稳定错误码，**只有** `safe_file_operation_blocked` 一类允许回退；
* 账本：同一次运行多次尝试，每次记「原因 + 与上次相比变了什么」，不含路径 / argv / env 值；有界、线程安全；
* 闸门：同一执行身份 + 同一根因不重复；safe→native 单向且一次运行至多一次；取消 / 过期不触发额外执行；
* 边界：CLI（`runcli`）与 MCP 包不 import 这套策略。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import execpolicy, pool

ROOT = Path(__file__).resolve().parent.parent
SAFE = execpolicy.SpecKey("safe", "i" * 12, "l" * 12)
NATIVE = execpolicy.SpecKey("native", "i" * 12, "n" * 12)
BLOCKED = "safe_file_operation_blocked"


# ---------------------------------------------------------------- 策略


def test_policy_defaults_to_compatible_and_round_trips(tmp_path):
    assert execpolicy.mode_for(tmp_path) == execpolicy.MODE_COMPATIBLE
    assert execpolicy.state(tmp_path)["decided"] is False
    got = execpolicy.set_mode(tmp_path, execpolicy.MODE_SAFE_ONLY)
    assert got["mode"] == "safe_only" and got["decided"] is True
    assert execpolicy.mode_for(tmp_path) == "safe_only"
    execpolicy.forget(tmp_path)
    assert execpolicy.mode_for(tmp_path) == "compatible"


def test_policy_is_per_project_and_rejects_unknown_modes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    execpolicy.set_mode(a, "safe_only")
    assert execpolicy.mode_for(b) == "compatible"
    with pytest.raises(ValueError):
        execpolicy.set_mode(a, "native_always")
    assert execpolicy.mode_for(a) == "safe_only"  # 非法写入没有改掉原值


def test_a_corrupted_policy_value_reads_as_the_default(tmp_path):
    from tavotto.engine import config

    config.set_project_settings(str(tmp_path), {execpolicy.SETTINGS_KEY: {"mode": "yolo"}})
    assert execpolicy.mode_for(tmp_path) == "compatible"
    config.set_project_settings(str(tmp_path), {execpolicy.SETTINGS_KEY: "safe_only"})
    assert execpolicy.mode_for(tmp_path) == "compatible"


@pytest.mark.parametrize("raw", ["0", "false", "OFF", " no "])
def test_kill_switch_env_turns_fallback_off(monkeypatch, raw):
    monkeypatch.setenv(execpolicy.FALLBACK_ENV, raw)
    assert execpolicy.fallback_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "on", "garbage"])
def test_kill_switch_env_other_values_keep_it_on(monkeypatch, raw):
    monkeypatch.setenv(execpolicy.FALLBACK_ENV, raw)
    assert execpolicy.fallback_enabled() is True


def test_kill_switch_defaults_on_and_env_beats_global_setting(monkeypatch):
    from tavotto.engine import config

    monkeypatch.delenv(execpolicy.FALLBACK_ENV, raising=False)
    assert execpolicy.fallback_enabled() is True
    cfg = config.load()
    cfg["worker"] = {**(cfg.get("worker") or {}), "execution_fallback": "0"}
    config.save(cfg)
    assert execpolicy.fallback_enabled() is False
    monkeypatch.setenv(execpolicy.FALLBACK_ENV, "1")  # 环境变量优先
    assert execpolicy.fallback_enabled() is True


# ---------------------------------------------------------------- 根因：复用已有码，只有 blocked 回退


def test_the_blocked_code_is_the_existing_stable_code_not_a_parallel_one():
    assert execpolicy.BLOCKED_CODES == frozenset({pool.FILE_OPERATION_BLOCKED_CODE})
    assert pool.FILE_OPERATION_BLOCKED_CODE == BLOCKED


@pytest.mark.parametrize(
    "code",
    [
        "script_error",  # 除零这类脚本自己的异常
        "missing_dependency",  # 缺包
        "missing_input",
        "script_exited",
        "script_needs_arguments",
        "script_needs_input",
        "no_figures_captured",
        "no_figures_captured_silent",
        "workdir_confirmation_required",
        "dependency_preparation_required",
        "worker_build_timeout",
        "worker_crashed",
        "preparation_plan_stale",
        "cancelled",
        "internal_error",
        "",
        None,
    ],
)
def test_only_a_blocked_cause_may_fall_back(code):
    assert execpolicy.may_fall_back(code) is False


def test_blocked_may_fall_back_by_code_or_by_class():
    assert execpolicy.may_fall_back(BLOCKED) is True
    assert execpolicy.may_fall_back(execpolicy.CAUSE_BLOCKED) is True
    assert execpolicy.cause_of(BLOCKED) == "blocked"
    assert execpolicy.cause_of("cancelled") == "cancelled"
    assert execpolicy.cause_of("preparation_plan_stale") == "stale"
    assert execpolicy.cause_of("whatever_else") == "other"


# ---------------------------------------------------------------- 闸门与决策


def _decide(ledger=None, **kw):
    base = dict(
        mode="compatible",
        enabled=True,
        ledger=ledger or _ledger_with_safe(),
        cause_code=BLOCKED,
        nxt=NATIVE,
        desktop_initiated=True,
    )
    base.update(kw)
    return execpolicy.decide_fallback(**base)


def _ledger_with_safe(attempt_id="a1"):
    ledger = execpolicy.AttemptLedger()
    ledger.begin_run()
    ledger.record(attempt_id, SAFE)
    return ledger


def test_blocked_safe_attempt_is_admitted_to_one_native_fallback():
    d = _decide()
    assert d.ok and d.reason == "" and bool(d) is True


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"desktop_initiated": False}, "not_desktop_initiated"),
        ({"enabled": False}, "fallback_disabled"),
        ({"mode": "safe_only"}, "policy_safe_only"),
        ({"cancelled": True}, "cancelled"),
        ({"stale": True}, "plan_stale"),
        ({"cause_code": "script_error"}, "not_blocked"),
        ({"cause_code": "missing_dependency"}, "not_blocked"),
        ({"nxt": SAFE}, "native_to_safe"),
    ],
)
def test_every_refusal_has_its_closed_reason(kw, reason):
    d = _decide(**kw)
    assert d.ok is False and d.reason == reason
    assert reason in execpolicy.REASONS


def test_desktop_initiation_defaults_to_false_and_wins_over_everything():
    """D1 只授权桌面用户主动发起的运行：不传就是 fail closed，且先于其它所有判断。"""
    kw = dict(
        mode="compatible", enabled=True, ledger=_ledger_with_safe(), cause_code=BLOCKED, nxt=NATIVE
    )
    assert execpolicy.decide_fallback(**kw).reason == "not_desktop_initiated"
    assert execpolicy.decide_fallback(**{**kw, "enabled": False}).reason == "not_desktop_initiated"


def test_kill_switch_and_safe_only_win_over_everything_else():
    assert _decide(enabled=False, mode="safe_only", cancelled=True).reason == "fallback_disabled"
    assert _decide(mode="safe_only", cancelled=True).reason == "policy_safe_only"


def test_native_never_goes_back_to_safe_and_falls_back_at_most_once():
    ledger = _ledger_with_safe()
    ledger.record("a2", NATIVE, cause=BLOCKED)
    ledger.close("a2", "failed", "script_error")
    # native 之后：回 safe 被拒；再来一次 native 也被拒（一次运行至多一次语义变更 / 同身份同根因）
    assert execpolicy.RetryGuard.admit(ledger, SAFE, "script_error").reason == "native_to_safe"
    again = _decide(ledger=ledger, nxt=NATIVE, cause_code=BLOCKED)
    assert again.ok is False
    assert again.reason in ("no_new_root_cause", "already_fell_back")


def test_the_same_spec_with_an_unchanged_root_cause_is_refused():
    ledger = _ledger_with_safe()
    ledger.close("a1", "blocked", BLOCKED)
    got = execpolicy.RetryGuard.admit(ledger, SAFE, BLOCKED)
    assert got.ok is False and got.reason == "no_new_root_cause"


def test_a_changed_spec_is_not_refused_as_a_repeat():
    ledger = _ledger_with_safe()
    ledger.close("a1", "blocked", BLOCKED)
    other = execpolicy.SpecKey("safe", "x" * 12, SAFE.launch)  # 解释器变了
    assert execpolicy.RetryGuard.admit(ledger, other, BLOCKED).ok is True


def test_a_new_run_starts_a_fresh_budget():
    ledger = _ledger_with_safe()
    ledger.record("a2", NATIVE, cause=BLOCKED)
    assert _decide(ledger=ledger).ok is False
    ledger.begin_run()  # 用户又点了一次运行
    ledger.record("b1", SAFE)
    assert _decide(ledger=ledger).ok is True


# ---------------------------------------------------------------- 账本：记原因与「变了什么」，不含私有事实


def test_ledger_records_cause_and_what_changed():
    ledger = execpolicy.AttemptLedger()
    ledger.begin_run()
    first = ledger.record("a1", SAFE)
    assert (first.seq, first.cause, first.changed) == (1, "", ())
    ledger.close("a1", "blocked", BLOCKED)
    second = ledger.record("a2", NATIVE, cause=BLOCKED)
    assert second.seq == 2 and second.cause == BLOCKED
    assert second.changed == ("profile", "launch_context")  # 解释器没变
    assert set(second.changed) <= set(execpolicy.CHANGED_DIMENSIONS)
    history = ledger.history()
    assert [h["outcome"] for h in history] == ["blocked", "pending"]
    assert history[0]["code"] == BLOCKED and history[1]["cause"] == BLOCKED


def test_ledger_rejects_values_outside_the_closed_sets():
    ledger = _ledger_with_safe()
    with pytest.raises(ValueError):
        ledger.close("a1", "exploded", "x")
    with pytest.raises(ValueError):
        ledger.decline("a1", "because_i_said_so")
    ledger.decline("a1", "policy_safe_only")
    assert ledger.history()[0]["declined"] == "policy_safe_only"


def test_ledger_is_bounded():
    ledger = execpolicy.AttemptLedger(max_records=4)
    for i in range(10):
        ledger.begin_run()
        ledger.record(f"a{i}", SAFE)
    assert len(ledger.history()) == 4
    assert ledger.history()[-1]["attempt_id"] == "a9"


def test_ledger_is_thread_safe():
    ledger = execpolicy.AttemptLedger(max_records=10_000)
    ledger.begin_run()

    def work(n):
        for i in range(200):
            ledger.record(f"t{n}-{i}", SAFE)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    seqs = sorted(r.seq for r in ledger.current())
    assert seqs == list(range(1, 1601))  # 序号不重不漏


def test_public_projection_carries_digests_and_closed_codes_only(tmp_path):
    secret_path = str(tmp_path / "private" / "python")
    key = execpolicy.SpecKey(
        "safe", execpolicy.digest(secret_path), execpolicy.digest("--token", "s3cr3t")
    )
    ledger = execpolicy.AttemptLedger()
    ledger.begin_run()
    ledger.record("a1", key)
    blob = json.dumps(ledger.history())
    assert str(tmp_path) not in blob and "s3cr3t" not in blob and "--token" not in blob
    assert all(len(v) == 12 for v in (key.interpreter, key.launch))


def test_digest_is_stable_and_discriminating():
    assert execpolicy.digest("a", 1) == execpolicy.digest("a", 1)
    assert execpolicy.digest("a", 1) != execpolicy.digest("a", 2)
    assert execpolicy.digest("ab", "c") != execpolicy.digest("a", "bc")


# ---------------------------------------------------------------- 边界：CLI 与 MCP 不 import 这套策略


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {a.name for a in node.names}
    return names


def test_cli_run_and_mcp_sources_never_reference_the_policy_or_the_session_layer():
    sources = [
        ROOT / "src/tavotto/engine/runcli.py",
        *sorted((ROOT / "codex-plugin/mcp").rglob("*.py")),
    ]
    assert len(sources) > 3
    for path in sources:
        text = path.read_text("utf-8")
        for needle in (
            "execpolicy",
            "prepsession",
            "decide_fallback",
            "TAVOTTO_EXECUTION_FALLBACK",
        ):
            assert needle not in text, f"{path} 不该引用 {needle}"
        assert not {"execpolicy", "prepsession"} & _imports_of(path)


def test_importing_the_cli_does_not_load_the_policy_module():
    code = (
        "import sys, tavotto.engine.runcli;"
        "sys.exit(1 if 'tavotto.engine.execpolicy' in sys.modules or "
        "'tavotto.engine.prepsession' in sys.modules else 0)"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
