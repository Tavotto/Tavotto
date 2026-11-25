"""Sign in with ChatGPT（SIWC）的令牌提供方契约（ADR 0201 §三）。**本阶段不实现登录、不联网、不落盘任何凭据。**

改图请求在 spawn 内置执行器之前向 `TokenProvider.access_token(min_valid_s=…)` 要一个够新的访问令牌，令牌只进子进程的
环境变量 `TAVOTTO_AGENT_API_KEY`（与 Key / 账号登录连接同一出口，接入在 PR-3）。这里定下：

* **接口**（`TokenProvider`）：`access_token` / `mark_rejected` / `status`。`status()` 只给登录态、额度许可与过期时刻，
  **没有任何令牌字段**——它是唯一允许进前端状态、连接视图、诊断包的形状。
* **刷新状态机**（`RefreshingProvider`）：按官方 sign-in / token-reference / errors-and-recovery 页（2026-11-23 读取）——
  - 访问令牌 1 小时；`min_valid_s` 缺省 `MIN_VALID_S` = 一次任务的最长时限（转换 900 s）+ 5 分钟余量。**不复用**
    `ai_oauth.REFRESH_MARGIN_MS`（10 分钟短于一次任务，令牌会在任务中途过期）。
  - 同一会话的刷新**串行化**（官方：「serialize refreshes … so two processes do not race a rotating token」）：进程内一把锁；
    跨进程的文件锁随存储实现一起做（`_load` / `_save` 由子类提供）。
  - 刷新成功后访问令牌、过期时刻、已授权范围、轮换后的刷新令牌**一起替换**（`TokenSet` 整体写回）。
  - 终态刷新错误（`TERMINAL_REFRESH_ERRORS`）才清凭据并要求重新登录；`invalid_client` 是配置错，不清；网络 / 5xx 等暂时失败
    **保留凭据**（官方：不在暂时失败时抹掉凭据）。
  - 没有 `chatgpt.tokens.use.direct` 范围 = 仍算已登录、但不许用订阅额度（`PlanUsageDisabled`），**不自动换计费方式**。
  - `earliest_refresh_at`：官方只列了字段名、没有定义含义；只记下、**不拦刷新**——2026-11-25 真服务核对：它约在签发后 54 分钟，
    而签发后几分钟发的刷新照样 200 且轮换了刷新令牌。若把它当「之前不许刷新」，每小时会有约 14 分钟任务起不来（剩余寿命 < 20 分钟又不许刷）。
* **不读取、不迁移 Codex CLI 的令牌**（`~/.codex` 一律不碰；看护用例按 AST / 源码钉住）。

本模块的 `provider()` 恒返回 `DisabledProvider`（或测试换上的那个）；真正的提供方（落盘的账号 + 联网刷新）由
`siwc_login.provider()` 组装（ADR 0209），这里保持不联网。测试用 `MemoryProvider`（内存里的 `TokenSet` + 注入的刷新函数与时钟）覆盖状态机。

纯标准库，Flask 父进程可 import；不 import `ai_agents` / `ai_oauth` / 任何网络模块。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

#: 订阅额度必须的授权范围（官方：没有它，有效的 ID 令牌也不许用订阅推理）
REQUIRED_SCOPE = "chatgpt.tokens.use.direct"
#: 一次任务的最长时限（`agent_runner.tools` 转换任务 900 s）+ 余量；`tests/test_siwc_auth.py` 对拍 TOOLSETS
TASK_MAX_SECONDS = 900
MIN_VALID_S = TASK_MAX_SECONDS + 300

#: 官方 errors-and-recovery：刷新令牌不可再用 → 清掉令牌、按已保存的 client_id 重新走 OAuth
TERMINAL_REFRESH_ERRORS = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)
#: 配置错（client_id 不对）：不是用户的问题，也不清凭据
CONFIG_REFRESH_ERRORS = frozenset({"invalid_client"})

#: `mark_rejected(reason)` 的闭集。**不清凭据**：官方 errors-and-recovery 要求「只在确认撤销或终态刷新错误后才要求重新登录」，
#: 一次 401 只说明这个访问令牌不被接受——下一次 `access_token` 先刷新；刷新报终态错误才清（退出登录另走撤销流程，以后实现）
REJECT_REASONS = frozenset({"plan_invalid_user", "http_401"})


class NeedsLogin(RuntimeError):
    """没有可用的登录（从没登录 / 刷新令牌已终态失效 / client 配置错）。`reason` 是闭集短码，不含任何内容。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PlanUsageDisabled(NeedsLogin):
    """已登录，但授权里没有 `chatgpt.tokens.use.direct`：不能用订阅额度（不自动换别的计费方式）。"""

    def __init__(self):
        super().__init__("plan_usage_disabled")


class TokenUnavailable(RuntimeError):
    """暂时拿不到够新的令牌（刷新时网络失败、还没到允许刷新的时刻、新令牌寿命不够一次任务）。凭据保留，稍后再试。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RefreshError(Exception):
    """子类的 `_refresh` 抛它：`code` = 令牌端点回的 OAuth 错误码，或 `network` / `server`（暂时失败）。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TokenSet:
    """一次签发 / 刷新的结果，**整体**保存、整体替换。`repr` 不含令牌。"""

    access: str = field(repr=False)
    refresh: str = field(repr=False)
    expires_at_ms: int
    scopes: frozenset[str] = frozenset()
    earliest_refresh_at_ms: int | None = None


@dataclass(frozen=True)
class TokenStatus:
    """唯一可以离开本模块的形状：没有令牌。"""

    signed_in: bool
    plan_usage_enabled: bool
    expires_at_ms: int | None

    def public(self) -> dict:
        return {
            "signed_in": self.signed_in,
            "plan_usage_enabled": self.plan_usage_enabled,
            "expires_at_ms": self.expires_at_ms,
        }


class TokenProvider(Protocol):
    def access_token(self, *, min_valid_s: int = MIN_VALID_S, allow_refresh: bool = True) -> str:
        """剩余寿命 ≥ `min_valid_s` 的访问令牌；不够就（串行地）刷新。
        没登录 / 终态失效 → `NeedsLogin`；没有订阅范围 → `PlanUsageDisabled`；暂时失败 → `TokenUnavailable`。
        `allow_refresh=False`（调用方剩余时限不够一次刷新）：不联网、不等锁——够新就给，不够 / 别人正在刷新 → `TokenUnavailable`。"""
        ...

    def mark_rejected(self, reason: str) -> None:
        """服务端拒了当前访问令牌（`reason` ∈ `REJECT_REASONS`）：下次 `access_token` 强制刷新。不清凭据；暂时性失败绝不调它。"""
        ...

    def status(self) -> TokenStatus: ...


class DisabledProvider:
    """本阶段唯一的生产实现：恒未登录，永不返回令牌。"""

    def access_token(self, *, min_valid_s: int = MIN_VALID_S, allow_refresh: bool = True) -> str:
        raise NeedsLogin("not_signed_in")

    def mark_rejected(self, reason: str) -> None:
        _check_reason(reason)

    def status(self) -> TokenStatus:
        return TokenStatus(signed_in=False, plan_usage_enabled=False, expires_at_ms=None)


def _check_reason(reason: str) -> None:
    if reason not in REJECT_REASONS:
        raise ValueError("reject reason")


def _now_ms() -> int:
    return int(time.time() * 1000)


class RefreshingProvider:
    """刷新状态机（见模块说明）。子类提供 `_load` / `_save` / `_clear` / `_refresh`；存储与 OAuth 请求以后再实现。"""

    def __init__(self, *, clock: Callable[[], int] = _now_ms):
        self._clock = clock
        self._lock = threading.Lock()
        #: 服务端拒过当前访问令牌：下次先刷新（不管剩余寿命）
        self._rejected = False

    # -- 子类实现 --------------------------------------------------------
    def _load(self) -> TokenSet | None:
        raise NotImplementedError

    def _save(self, tokens: TokenSet) -> None:
        raise NotImplementedError

    def _clear(self) -> None:
        raise NotImplementedError

    def _refresh(self, current: TokenSet) -> TokenSet:
        raise NotImplementedError

    def _critical(self) -> contextlib.AbstractContextManager:
        """「读 → 判 → 刷新 → 写回」整段额外持有的锁（存储的跨进程文件锁，见 `siwc_store.StoreProvider`）；缺省无。"""
        return contextlib.nullcontext()

    # -- 契约 --------------------------------------------------------------
    def access_token(self, *, min_valid_s: int = MIN_VALID_S, allow_refresh: bool = True) -> str:
        if not allow_refresh:
            return self._cached_token(min_valid_s)
        with self._lock, self._critical():
            ts = self._load()
            if ts is None or not ts.access:
                raise NeedsLogin("not_signed_in")
            if REQUIRED_SCOPE not in ts.scopes:
                raise PlanUsageDisabled()
            now = self._clock()
            need_ms = min_valid_s * 1000
            if not self._rejected and ts.expires_at_ms - now >= need_ms:
                return ts.access
            if not ts.refresh:
                raise NeedsLogin("refresh_terminal")
            try:
                new = self._refresh(ts)
            except RefreshError as exc:
                if exc.code in TERMINAL_REFRESH_ERRORS:
                    self._clear()
                    raise NeedsLogin("refresh_terminal") from None
                if exc.code in CONFIG_REFRESH_ERRORS:
                    raise NeedsLogin("invalid_client") from None
                raise TokenUnavailable("refresh_failed") from None  # 暂时失败：凭据原样保留
            if not new.access or not new.refresh:
                raise TokenUnavailable("bad_refresh_response")
            # 刷新时没回 scope = 授权不变（官方：省略 scope 保持原授权）
            new = TokenSet(
                access=new.access,
                refresh=new.refresh,
                expires_at_ms=new.expires_at_ms,
                scopes=new.scopes or ts.scopes,
                earliest_refresh_at_ms=new.earliest_refresh_at_ms,
            )
            self._save(new)  # 四样一起替换
            self._rejected = False
            if REQUIRED_SCOPE not in new.scopes:
                raise PlanUsageDisabled()
            if new.expires_at_ms - self._clock() < need_ms:
                raise TokenUnavailable("short_lived")
            return new.access

    def _cached_token(self, min_valid_s: int) -> str:
        """不刷新的取法：不等锁（别人正在刷新 = 暂时拿不到）、不联网；够新就给。"""
        if not self._lock.acquire(blocking=False):
            raise TokenUnavailable("refresh_in_progress")
        try:
            ts = self._load()
            if ts is None or not ts.access:
                raise NeedsLogin("not_signed_in")
            if REQUIRED_SCOPE not in ts.scopes:
                raise PlanUsageDisabled()
            if self._rejected or ts.expires_at_ms - self._clock() < min_valid_s * 1000:
                raise TokenUnavailable("refresh_deferred")
            return ts.access
        finally:
            self._lock.release()

    def mark_rejected(self, reason: str) -> None:
        _check_reason(reason)
        with self._lock:
            self._rejected = True

    def status(self) -> TokenStatus:
        with self._lock:
            ts = self._load()
        if ts is None or not ts.access:
            return TokenStatus(signed_in=False, plan_usage_enabled=False, expires_at_ms=None)
        return TokenStatus(
            signed_in=True,
            plan_usage_enabled=REQUIRED_SCOPE in ts.scopes,
            expires_at_ms=ts.expires_at_ms,
        )


class MemoryProvider(RefreshingProvider):
    """测试用：令牌只在内存里；`refresh` 是注入的刷新函数（None = 刷新一律暂时失败）。"""

    def __init__(
        self,
        tokens: TokenSet | None,
        *,
        refresh: Callable[[TokenSet], TokenSet] | None = None,
        clock: Callable[[], int] = _now_ms,
    ):
        super().__init__(clock=clock)
        self._tokens = tokens
        self._refresh_fn = refresh

    def __repr__(self) -> str:
        return "MemoryProvider(<redacted>)"

    def _load(self) -> TokenSet | None:
        return self._tokens

    def _save(self, tokens: TokenSet) -> None:
        self._tokens = tokens

    def _clear(self) -> None:
        self._tokens = None

    def _refresh(self, current: TokenSet) -> TokenSet:
        if self._refresh_fn is None:
            raise RefreshError("network")
        return self._refresh_fn(current)


_OVERRIDE: TokenProvider | None = None


def provider() -> TokenProvider:
    """当前的提供方。本阶段没有登录实现：恒 `DisabledProvider`（测试经 `set_provider_for_tests` 换）。"""
    return _OVERRIDE if _OVERRIDE is not None else DisabledProvider()


def set_provider_for_tests(p: TokenProvider | None) -> None:
    global _OVERRIDE
    _OVERRIDE = p


__all__ = [
    "MIN_VALID_S",
    "REQUIRED_SCOPE",
    "DisabledProvider",
    "MemoryProvider",
    "NeedsLogin",
    "PlanUsageDisabled",
    "RefreshError",
    "RefreshingProvider",
    "TokenProvider",
    "TokenSet",
    "TokenStatus",
    "TokenUnavailable",
    "provider",
    "set_provider_for_tests",
]
