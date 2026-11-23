"""`engine/siwc_auth`（ADR 0201 §三）：令牌提供方契约与刷新状态机。全部在内存里，不联网、无真实凭据。

判据的主语：`access_token` 的返回值 / 异常与提供方**存着的** `TokenSet`（被清掉了没有、四样是不是一起换了），
刷新函数被调用的次数（串行化 = 并发 8 个请求只刷新 1 次）；隐私看 `repr` / `status().public()` 的文本里有没有令牌金丝雀。
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

from tavotto.engine import siwc_auth as sa
from tavotto.engine.agent_runner import tools

ACCESS = "acc-CANARY-1"
REFRESH = "ref-CANARY-1"
H = 3600 * 1000
SCOPES = frozenset({"openid", "offline_access", sa.REQUIRED_SCOPE})


class Clock:
    def __init__(self, t=1_000_000_000_000):
        self.t = t

    def __call__(self):
        return self.t


def _ts(clock, *, left_ms=H, scopes=SCOPES, access=ACCESS, refresh=REFRESH, earliest=None):
    return sa.TokenSet(access, refresh, clock() + left_ms, scopes, earliest)


def test_min_valid_covers_the_longest_task():
    longest = max(ts.default_limits.max_seconds for ts in tools.TOOLSETS.values())
    assert sa.TASK_MAX_SECONDS >= longest
    assert sa.MIN_VALID_S > longest


def test_disabled_provider_never_returns_a_token():
    p = sa.provider()
    assert isinstance(p, sa.DisabledProvider)
    with pytest.raises(sa.NeedsLogin) as exc:
        p.access_token()
    assert exc.value.reason == "not_signed_in"
    assert p.status().public() == {
        "signed_in": False,
        "plan_usage_enabled": False,
        "expires_at_ms": None,
    }


def test_disabled_even_with_the_flag_on(monkeypatch):
    monkeypatch.setenv("TAVOTTO_EXPERIMENTAL_SIWC", "1")
    assert isinstance(sa.provider(), sa.DisabledProvider)


def test_fresh_token_returned_without_refresh():
    clock = Clock()
    calls = []
    p = sa.MemoryProvider(_ts(clock), refresh=lambda t: calls.append(t), clock=clock)
    assert p.access_token() == ACCESS and calls == []


def test_token_that_would_expire_mid_task_is_refreshed_and_replaced_together():
    clock = Clock()
    new = sa.TokenSet("acc-2", "ref-2", clock() + H, frozenset(), None)
    p = sa.MemoryProvider(_ts(clock, left_ms=10 * 60 * 1000), refresh=lambda t: new, clock=clock)
    assert p.access_token() == "acc-2"
    stored = p._load()
    # 四样一起换；刷新没回 scope = 授权不变
    assert (stored.access, stored.refresh, stored.expires_at_ms) == (
        "acc-2",
        "ref-2",
        new.expires_at_ms,
    )
    assert stored.scopes == SCOPES


@pytest.mark.parametrize("code", sorted(sa.TERMINAL_REFRESH_ERRORS))
def test_terminal_refresh_error_clears_credentials(code):
    clock = Clock()

    def refresh(_t):
        raise sa.RefreshError(code)

    p = sa.MemoryProvider(_ts(clock, left_ms=0), refresh=refresh, clock=clock)
    with pytest.raises(sa.NeedsLogin) as exc:
        p.access_token()
    assert exc.value.reason == "refresh_terminal"
    assert p._load() is None and not p.status().signed_in


@pytest.mark.parametrize("code", ["network", "server", "temporarily_unavailable"])
def test_transient_refresh_failure_keeps_credentials(code):
    clock = Clock()
    original = _ts(clock, left_ms=0)

    def refresh(_t):
        raise sa.RefreshError(code)

    p = sa.MemoryProvider(original, refresh=refresh, clock=clock)
    with pytest.raises(sa.TokenUnavailable):
        p.access_token()
    assert p._load() == original


def test_invalid_client_is_a_config_error_and_keeps_credentials():
    clock = Clock()
    original = _ts(clock, left_ms=0)

    def refresh(_t):
        raise sa.RefreshError("invalid_client")

    p = sa.MemoryProvider(original, refresh=refresh, clock=clock)
    with pytest.raises(sa.NeedsLogin) as exc:
        p.access_token()
    assert exc.value.reason == "invalid_client" and p._load() == original


def test_missing_plan_scope_is_signed_in_but_not_usable():
    clock = Clock()
    p = sa.MemoryProvider(_ts(clock, scopes=frozenset({"openid"})), clock=clock)
    with pytest.raises(sa.PlanUsageDisabled):
        p.access_token()
    st = p.status()
    assert st.signed_in and not st.plan_usage_enabled


def test_earliest_refresh_at_is_respected():
    clock = Clock()
    calls = []
    p = sa.MemoryProvider(
        _ts(clock, left_ms=60_000, earliest=clock() + 120_000),
        refresh=lambda t: calls.append(t),
        clock=clock,
    )
    with pytest.raises(sa.TokenUnavailable) as exc:
        p.access_token()
    assert exc.value.reason == "refresh_not_yet_allowed" and calls == []


def test_short_lived_refresh_result_is_not_handed_out():
    clock = Clock()
    p = sa.MemoryProvider(
        _ts(clock, left_ms=0),
        refresh=lambda t: sa.TokenSet("a2", "r2", clock() + 60_000, SCOPES),
        clock=clock,
    )
    with pytest.raises(sa.TokenUnavailable) as exc:
        p.access_token()
    assert exc.value.reason == "short_lived"


def test_concurrent_requests_refresh_once():
    clock = Clock()
    calls = []

    def refresh(t):
        calls.append(t)
        time.sleep(0.05)
        return sa.TokenSet(f"acc-{len(calls)}", f"ref-{len(calls)}", clock() + H, SCOPES)

    p = sa.MemoryProvider(_ts(clock, left_ms=0), refresh=refresh, clock=clock)
    out: list[str] = []
    threads = [threading.Thread(target=lambda: out.append(p.access_token())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and set(out) == {"acc-1"}


def test_invalidate_only_accepts_closed_reasons():
    clock = Clock()
    p = sa.MemoryProvider(_ts(clock), clock=clock)
    with pytest.raises(ValueError):
        p.invalidate("network")  # 暂时性失败不许清凭据
    assert p.status().signed_in
    p.invalidate("plan_invalid_user")
    assert not p.status().signed_in


def test_no_token_in_repr_or_status():
    clock = Clock()
    ts = _ts(clock)
    p = sa.MemoryProvider(ts, clock=clock)
    text = repr(ts) + repr(p) + repr(p.status()) + str(p.status().public())
    assert ACCESS not in text and REFRESH not in text


def test_module_never_touches_codex_credentials_or_the_network():
    src = Path(sa.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    banned = {"urllib", "http", "socket", "ssl", "subprocess"}
    assert not {m.split(".")[0] for m in imported} & banned, imported
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not names & {"ai_agents", "ai_oauth", "ai_providers"}, names
    strings = [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    # 文档串可以提到「不碰 ~/.codex」，但不许有任何把它当路径用的字符串常量
    code_strings = [s for s in strings if "\n" not in s]
    assert not [s for s in code_strings if ".codex" in s or "auth.json" in s]
