"""准备会话里 safe 被拦下后，先在 safe 下换一个静态可解析的出图入口（ADR 0127 修订 2026-10-30，#1500）。

旧的素材栏探测（`probe.entry_candidates` / `entry_retry_allowed`）会在 `__main__` 被 `safe_file_operation_blocked` 拦下后换入口，
只建图、不进 `save()` 的出图函数在 safe 下就出图；准备会话以前直接失败。现在顺序是：换入口（safe）→ 桌面 native 回退 / 浏览器确认卡。
候选与「值不值得换」只复用 probe 那一份；总执行次数有上限；账本记清每一次。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import time

import pytest

from tavotto import security
from tavotto.engine import execpolicy, pool as engine_pool, probe
from test_execution_fallback import BLOCKED, _history  # noqa: F401
from test_execution_fallback_origin import (  # noqa: F401
    NONCE,
    _mode,
    _restore_auth_config,
    native,
)
from test_preparation_api import (  # noqa: F401
    _open,
    _project,
    client,
    fake_pool,
)
from test_preparation_session import SESSIONS, _action, sessions  # noqa: F401

KIND = "confirm_native_run"
MAIN_FIRST = """\
import matplotlib.pyplot as plt


def draw_figure():
    fig, ax = plt.subplots()
    ax.plot([1, 2])
    return fig


if __name__ == "__main__":
    draw_figure().savefig("out.png")
"""
TOP_LEVEL_ONLY = """\
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1])
fig.savefig("out.png")
"""


@pytest.fixture
def entries(monkeypatch, fake_pool):
    """记录每次 build 用的入口；`blocked_entries` 里的入口按 safe 守卫拦下，其余照 fake_pool 成功。"""
    box = {"seen": [], "blocked": {"__main__"}}
    inner = engine_pool.build_owned

    def build_owned(script, root, entry, **kw):
        box["seen"].append(entry)
        if entry in box["blocked"]:
            raise BLOCKED
        return inner(script, root, entry, **kw)

    monkeypatch.setattr(engine_pool, "build_owned", build_owned)
    return box


def _script(root, name, text):
    (root / name).write_text(text, encoding="utf-8")


def _go(client, name):
    report = client.post(SESSIONS, json={"script": name}).get_json()
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": _action(report, "run")["id"],
            "expected_config_revision": report["config_revision"],
        },
    )
    assert resp.status_code == 202, resp.get_json()
    deadline = time.time() + 15
    while True:
        final = client.get(f"{SESSIONS}/{report['session_id']}").get_json()
        if final["phase"] not in ("running", "awaiting_runtime_input"):
            break
        assert time.time() < deadline
        time.sleep(0.02)
    time.sleep(0.3)
    return client.get(f"{SESSIONS}/{report['session_id']}").get_json()


def _setup(client, tmp_path, name, text):
    root = _project(tmp_path, "p")
    _script(root, name, text)
    _open(client, root)
    return root


def _desktop(client):
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))


def _browser(client):
    state = _mode(desktop=False, secret="s")
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))


def test_the_candidates_are_the_probe_ones_and_static_only_never_guesses(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "a.py").write_text(MAIN_FIRST, encoding="utf-8")
    (ok / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    assert probe.entry_candidates(ok, "a.py", static_only=True) == ["__main__", "draw_figure"]
    assert probe.entry_candidates(ok, "a.py", static_only=True) == probe.entry_candidates(
        ok, "a.py"
    )  # 能解析时同一份
    assert probe.entry_candidates(ok, "bad.py")  # 盲试表
    assert probe.entry_candidates(ok, "bad.py", static_only=True) == []  # 解析不了不盲试


@pytest.mark.parametrize("origin", ["browser", "desktop", "plain"])
def test_blocked_main_then_draw_function_succeeds_in_safe(
    client, tmp_path, fake_pool, entries, native, sessions, origin
):
    """复现 #1500 的补充场景：`__main__` 被拦、`draw_figure` 在 safe 下出图——当成功，任何来源都不动 native。"""
    _setup(client, tmp_path, "d.py", MAIN_FIRST)
    {"browser": _browser, "desktop": _desktop, "plain": lambda c: None}[origin](client)
    final = _go(client, "d.py")
    assert entries["seen"] == ["__main__", "draw_figure"]
    # 第二次尝试的执行成功了（替身 worker 的登记另说：它的描述符是固定的 `fig`）；关键是没有再被拦、没碰 native
    assert final["result"]["status"] == "ready"
    assert final["outcome"].get("code") != "safe_file_operation_blocked"
    assert native["calls"] == []
    hist = _history(final)
    assert [h["profile"] for h in hist] == ["safe", "safe"]
    assert hist[1]["cause"] == "safe_file_operation_blocked"
    assert "launch_context" in hist[1]["changed"] and "profile" not in hist[1]["changed"]
    assert hist[0]["run"] == hist[1]["run"]
    assert KIND not in [a["kind"] for a in final["actions"]]


def test_desktop_swaps_entry_first_then_falls_back_to_native(
    client, tmp_path, fake_pool, entries, native, sessions
):
    entries["blocked"] = {"__main__", "draw_figure"}
    _setup(client, tmp_path, "d.py", MAIN_FIRST)
    _desktop(client)
    final = _go(client, "d.py")
    assert entries["seen"] == ["__main__", "draw_figure"]
    assert [h["profile"] for h in _history(final)] == ["safe", "safe", "native"]
    assert len(native["calls"]) == 1 and final["result"]["status"] == "ready"


def test_browser_swaps_entry_first_then_offers_the_confirmation(
    client, tmp_path, fake_pool, entries, native, sessions
):
    entries["blocked"] = {"__main__", "draw_figure"}
    _setup(client, tmp_path, "d.py", MAIN_FIRST)
    _browser(client)
    final = _go(client, "d.py")
    assert entries["seen"] == ["__main__", "draw_figure"] and native["calls"] == []
    assert KIND in [a["kind"] for a in final["actions"]]
    assert final["remedy"] == {"kind": "switch_native", "silent": False}
    assert [h["profile"] for h in _history(final)] == ["safe", "safe"]


@pytest.mark.parametrize("origin", ["browser", "desktop"])
def test_without_a_statically_resolvable_entry_the_original_branch_runs(
    client, tmp_path, fake_pool, entries, native, sessions, origin
):
    """只有顶层代码、没有可换的出图函数：不盲试，桌面直接 native、浏览器直接出确认卡；safe 只跑一次。"""
    entries["blocked"] = {"__main__"}
    _setup(client, tmp_path, "t.py", TOP_LEVEL_ONLY)
    (_browser if origin == "browser" else _desktop)(client)
    final = _go(client, "t.py")
    assert entries["seen"] == ["__main__"]
    if origin == "desktop":
        assert [h["profile"] for h in _history(final)] == ["safe", "native"]
        assert len(native["calls"]) == 1
    else:
        assert [h["profile"] for h in _history(final)] == ["safe"]
        assert KIND in [a["kind"] for a in final["actions"]] and native["calls"] == []


def test_a_syntax_error_script_is_never_blindly_retried(
    client, tmp_path, fake_pool, entries, native, sessions
):
    entries["blocked"] = {"main", "render", "__main__"}
    _setup(client, tmp_path, "c.py", "def broken(:\n")
    _browser(client)
    _go(client, "c.py")
    assert len(entries["seen"]) == 1  # 没有 main / render 的盲试


def test_the_entry_swap_is_once_per_run_and_total_executions_are_capped():
    ledger = execpolicy.AttemptLedger()
    ledger.begin_run()
    mk = lambda p, n: execpolicy.SpecKey(p, "i", n)  # noqa: E731
    ledger.record("a1", mk("safe", "l1"))
    ledger.record("a2", mk("safe", "l2"), cause="safe_file_operation_blocked")
    ledger.record("a3", mk("native", "l3"), cause="safe_file_operation_blocked")
    admit = execpolicy.RetryGuard.admit
    assert admit(ledger, mk("safe", "l4"), "safe_file_operation_blocked").reason == (
        execpolicy.REASON_ATTEMPT_CAP
    )
    assert execpolicy.MAX_ATTEMPTS_PER_RUN == 3
    # 同一份身份 + 同一根因仍被拒（换入口必须真换了入口）
    fresh = execpolicy.AttemptLedger()
    fresh.begin_run()
    fresh.record("b1", mk("safe", "l1"))
    fresh.close("b1", "blocked", "safe_file_operation_blocked")
    assert admit(fresh, mk("safe", "l1"), "safe_file_operation_blocked").reason == (
        execpolicy.REASON_NO_NEW_ROOT_CAUSE
    )
    assert admit(fresh, mk("safe", "l2"), "safe_file_operation_blocked").ok
