"""执行策略与统一重试闸门（V2 PR1，ADR 0127）。

一个用户发起的「运行」可以有多次真实执行尝试（今天只有一种合法的第二次：safe 被自己的守卫拦下后，同一会话里
改用桌面拥有的 native 再跑一次）。本模块是这件事的**唯一裁决处**，纯逻辑、纯标准库，不起进程、不读脚本：

* `ExecutionPolicy`：用户对「执行语义能不能自动改」的显式偏好，项目设置键 `execution_policy`（写法同 `workdir`）。
  缺省 `compatible`（允许按执行证据回退）；`safe_only` = 用户明确只要 safe，任何证据都只显示、不回退。
  运维开关 `TAVOTTO_EXECUTION_FALLBACK=0`（环境变量优先，其次全局设置 `worker.execution_fallback`，写法同
  `TAVOTTO_ENV_ADOPTION`）整个关掉回退。
* 根因（cause）不另造枚举：它就是已有的稳定错误码；`cause_of()` 只把码归到少数几类，`blocked` 是**唯一**允许回退的一类
  （safe 的守卫自己拦下了关键文件操作，换成保留文件语义的执行才有意义；缺包 / 参数 / 路径 / 脚本自己的异常换执行语义不会变好）。
* `AttemptRecord` / `AttemptLedger`：一次运行（run）里每次真实尝试的公开事实——解释器身份摘要、执行语义、启动上下文摘要、
  原因码、与上一次相比变了什么（闭集）。**不含** argv 原文、env 值、路径、脚本内容。
* `RetryGuard`：没有新根因就不许同一份执行身份再来一次；safe→native 单向、一次运行至多一次；取消 / 过期计划不触发额外执行。

CLI（`tavotto run`）与 MCP 路径不 import 本模块（看护：`tests/test_execution_policy.py`）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import threading
from collections.abc import Iterable
from pathlib import Path

from . import config, pool

SETTINGS_KEY = "execution_policy"

MODE_COMPATIBLE = "compatible"
MODE_SAFE_ONLY = "safe_only"
MODES = (MODE_COMPATIBLE, MODE_SAFE_ONLY)
DEFAULT_MODE = MODE_COMPATIBLE

#: 运维开关：`0` / `false` / `off` / `no` = 整个关掉执行回退（环境变量优先，其次全局设置 `worker.execution_fallback`）。
FALLBACK_ENV = "TAVOTTO_EXECUTION_FALLBACK"
_OFF = frozenset({"0", "false", "off", "no"})

ERROR_MODE_INVALID = "execution_policy_invalid"
ERROR_CODES = (ERROR_MODE_INVALID,)

# ---------------------------------------------------------------- 执行语义（与 execspec.PROFILE_* 同值，纯逻辑不 import 它）
PROFILE_SAFE = "safe"
PROFILE_NATIVE = "native"

# ---------------------------------------------------------------- 根因分类（归类已有错误码，不造新码）
CAUSE_BLOCKED = "blocked"  # safe 的守卫拦下关键文件操作：唯一允许回退的一类
CAUSE_CANCELLED = "cancelled"
CAUSE_STALE = "stale"
CAUSE_OTHER = "other"
CAUSES = (CAUSE_BLOCKED, CAUSE_CANCELLED, CAUSE_STALE, CAUSE_OTHER)

#: 属于 `blocked` 的稳定码。目前只有 worker 的 `safe_file_operation_blocked`（savefig 衍生的 FileNotFoundError 也用同一个码）。
BLOCKED_CODES = frozenset({pool.FILE_OPERATION_BLOCKED_CODE})
_CANCEL_CODE = "cancelled"
_STALE_CODE = "preparation_plan_stale"

# ---------------------------------------------------------------- 不回退 / 不重试的理由（闭集；诊断与测试都读它）
REASON_OK = ""
REASON_PROJECT_REOPENED = (
    "project_reopened"  # 运行开始之后项目关闭又重开过（代次变了）：关闭前的运行不起新进程
)
REASON_SUPERSEDED = (
    "superseded"  # 已有更新的同脚本重跑取代了这次运行：回退会结束新一次的 native，不接
)
REASON_NOT_DESKTOP = (
    "not_desktop_initiated"  # 不是桌面用户主动发起的运行（浏览器 / API 客户端 / 无认证）
)
REASON_DISABLED = "fallback_disabled"  # 运维开关关了
REASON_SAFE_ONLY = "policy_safe_only"  # 用户明确只要 safe
REASON_NOT_BLOCKED = "not_blocked"  # 原因不是 safe 自己的限制：换语义不会变好
REASON_CANCELLED = "cancelled"  # 取消发生在回退之前
REASON_STALE = "plan_stale"  # 检查那一刻的世界变了（源码 / 解释器 / 授权）
REASON_ALREADY_FELL_BACK = "already_fell_back"  # 一次运行至多一次
REASON_NATIVE_TO_SAFE = "native_to_safe"  # 单向：native 之后不回 safe
REASON_NO_NEW_ROOT_CAUSE = "no_new_root_cause"  # 同一份执行身份、同一个根因：不重复
REASON_NOT_RUNNABLE = "not_runnable"  # native 计划本身还有要用户答的事 / 不是脚本目标
REASON_INTERPRETER_CHANGED = "interpreter_changed"  # native 计划解出的解释器不是 safe 当时那一个
REASON_PLAN_UNAVAILABLE = "plan_unavailable"  # native 计划算不出来
REASON_LAUNCH_FAILED = "launch_failed"  # 交给执行线程时失败
REASONS = (
    REASON_PROJECT_REOPENED,
    REASON_SUPERSEDED,
    REASON_NOT_DESKTOP,
    REASON_DISABLED,
    REASON_SAFE_ONLY,
    REASON_NOT_BLOCKED,
    REASON_CANCELLED,
    REASON_STALE,
    REASON_ALREADY_FELL_BACK,
    REASON_NATIVE_TO_SAFE,
    REASON_NO_NEW_ROOT_CAUSE,
    REASON_NOT_RUNNABLE,
    REASON_INTERPRETER_CHANGED,
    REASON_PLAN_UNAVAILABLE,
    REASON_LAUNCH_FAILED,
)

# ---------------------------------------------------------------- 「与上次相比变了什么」（执行三维，闭集）
CHANGED_PROFILE = "profile"
CHANGED_INTERPRETER = "interpreter"
CHANGED_LAUNCH = "launch_context"
CHANGED_DIMENSIONS = (CHANGED_PROFILE, CHANGED_INTERPRETER, CHANGED_LAUNCH)

# 尝试的结局（闭集）
OUTCOME_PENDING = "pending"
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_BLOCKED = "blocked"
OUTCOME_CANCELLED = "cancelled"
OUTCOMES = (OUTCOME_PENDING, OUTCOME_SUCCEEDED, OUTCOME_FAILED, OUTCOME_BLOCKED, OUTCOME_CANCELLED)

#: 账本容量（所有 run 合计）。
MAX_RECORDS = 16


# ---------------------------------------------------------------- 策略
def fallback_enabled() -> bool:
    """运维开关此刻是不是允许回退：`TAVOTTO_EXECUTION_FALLBACK` 优先，其次全局设置，都没写 = 允许。"""
    raw = os.environ.get(FALLBACK_ENV, "").strip().lower()
    if raw:
        return raw not in _OFF
    stored = str((config.load().get("worker") or {}).get("execution_fallback") or "")
    return stored.strip().lower() not in _OFF


def _stored(root: str | Path) -> dict | None:
    stored = (config.project_settings(str(Path(root))) or {}).get(SETTINGS_KEY)
    return stored if isinstance(stored, dict) else None


def mode_for(root: str | Path) -> str:
    """这个项目的执行策略。没写过、写坏了（不是合法取值）都按默认 `compatible`。"""
    stored = _stored(root)
    mode = stored.get("mode") if stored else None
    return mode if mode in MODES else DEFAULT_MODE


def decided(root: str | Path) -> bool:
    stored = _stored(root)
    return bool(stored) and stored.get("mode") in MODES


def set_mode(root: str | Path, mode: str) -> dict:
    """记住这个项目的执行策略（项目级，不写全局）。"""
    if mode not in MODES:
        raise ValueError(f"execution policy 非法: {mode!r}（可选 {MODES}）")
    config.set_project_settings(str(Path(root)), {SETTINGS_KEY: {"mode": mode}})
    return state(root)


def forget(root: str | Path) -> None:
    config.set_project_settings(str(Path(root)), {SETTINGS_KEY: None})


def state(root: str | Path) -> dict:
    return {
        "mode": mode_for(root),
        "decided": decided(root),
        "default": DEFAULT_MODE,
        "modes": list(MODES),
        "fallback_enabled": fallback_enabled(),
    }


# ---------------------------------------------------------------- 根因
def cause_of(code: str | None) -> str:
    """稳定错误码 → 根因类别。只认已有的码，其余一律 `other`（不回退）。"""
    code = str(code or "")
    if code in BLOCKED_CODES:
        return CAUSE_BLOCKED
    if code == _CANCEL_CODE:
        return CAUSE_CANCELLED
    if code == _STALE_CODE:
        return CAUSE_STALE
    return CAUSE_OTHER


def may_fall_back(cause_or_code: str | None) -> bool:
    """这个原因允许换执行语义再试吗：只有 `blocked`（safe 自己的限制）为真。接受类别或稳定码。"""
    value = str(cause_or_code or "")
    return (value if value in CAUSES else cause_of(value)) == CAUSE_BLOCKED


# ---------------------------------------------------------------- 身份摘要（不含路径 / argv / env 值）
def digest(*parts: object) -> str:
    """若干私有事实的短摘要（12 位十六进制）：用于比较「是不是同一份」，不可还原。"""
    h = hashlib.sha256()
    for part in parts:
        h.update(repr(part).encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()[:12]


@dataclasses.dataclass(frozen=True)
class SpecKey:
    """一次尝试的执行身份（三维，D3）：执行语义 · 解释器摘要 · 启动上下文摘要。"""

    profile: str
    interpreter: str
    launch: str

    def public(self) -> dict:
        return {"profile": self.profile, "interpreter": self.interpreter, "launch": self.launch}


def changed_between(prev: SpecKey | None, nxt: SpecKey) -> tuple[str, ...]:
    """相对上一次，三维里哪几维变了（闭集，按固定顺序）。没有上一次 = 空。"""
    if prev is None:
        return ()
    out = []
    if prev.profile != nxt.profile:
        out.append(CHANGED_PROFILE)
    if prev.interpreter != nxt.interpreter:
        out.append(CHANGED_INTERPRETER)
    if prev.launch != nxt.launch:
        out.append(CHANGED_LAUNCH)
    return tuple(out)


# ---------------------------------------------------------------- 账本
@dataclasses.dataclass
class AttemptRecord:
    run: int
    seq: int  # 在本次 run 里的序号，从 1 起
    attempt_id: str
    spec: SpecKey
    #: 触发这次尝试的原因（上一次尝试的稳定错误码）；第一次尝试为空
    cause: str = ""
    #: 与上一次相比变了什么；第一次为空
    changed: tuple[str, ...] = ()
    outcome: str = OUTCOME_PENDING
    #: 本次尝试自己的收场码（失败 / 拦截时的稳定错误码）
    code: str = ""
    #: 本次本可回退（原因是 blocked）却没有回退的理由（`REASONS` 里的闭集码）；空 = 不适用 / 已回退
    declined: str = ""

    def public(self) -> dict:
        return {
            "run": self.run,
            "seq": self.seq,
            "attempt_id": self.attempt_id,
            **self.spec.public(),
            "cause": self.cause,
            "changed": list(self.changed),
            "outcome": self.outcome,
            "code": self.code,
            "declined": self.declined,
        }


class AttemptLedger:
    """一个会话里所有真实执行尝试的有界账本；按 run（一次用户发起的运行动作）分组，线程安全。"""

    def __init__(self, max_records: int = MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._max = max_records
        self._records: list[AttemptRecord] = []
        self._run = 0

    def begin_run(self) -> int:
        """一次新的用户发起的运行：预算与「已经回退过」都从头算。"""
        with self._lock:
            self._run += 1
            return self._run

    @property
    def run(self) -> int:
        with self._lock:
            return self._run

    def record(self, attempt_id: str, spec: SpecKey, *, cause: str = "") -> AttemptRecord:
        with self._lock:
            mine = [r for r in self._records if r.run == self._run]
            prev = mine[-1] if mine else None
            rec = AttemptRecord(
                run=self._run,
                seq=len(mine) + 1,
                attempt_id=attempt_id,
                spec=spec,
                cause=cause if prev is not None else "",
                changed=changed_between(prev.spec if prev else None, spec),
            )
            self._records.append(rec)
            del self._records[: -self._max]
            return dataclasses.replace(rec)

    def close(self, attempt_id: str, outcome: str, code: str = "") -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"尝试结局不在闭集里: {outcome!r}")
        with self._lock:
            for rec in reversed(self._records):
                if rec.attempt_id == attempt_id:
                    rec.outcome, rec.code = outcome, str(code or "")
                    return

    def decline(self, attempt_id: str, reason: str) -> None:
        """记下这次尝试**没有**回退的理由（闭集）。"""
        if reason not in REASONS:
            raise ValueError(f"回退理由不在闭集里: {reason!r}")
        with self._lock:
            for rec in reversed(self._records):
                if rec.attempt_id == attempt_id:
                    rec.declined = reason
                    return

    def discard(self, attempt_id: str) -> None:
        """撤回一条尚未真正交出去的尝试（认领后启动失败）。"""
        with self._lock:
            self._records = [r for r in self._records if r.attempt_id != attempt_id]

    def current(self) -> list[AttemptRecord]:
        """当前这次 run 的尝试（副本，按序）。"""
        with self._lock:
            return [dataclasses.replace(r) for r in self._records if r.run == self._run]

    def history(self) -> list[dict]:
        """全部（有界）尝试的公开投影。"""
        with self._lock:
            return [r.public() for r in self._records]

    def count(self) -> int:
        """当前 run 里真实尝试的次数。"""
        return len(self.current())


# ---------------------------------------------------------------- 闸门
@dataclasses.dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str = REASON_OK

    def __bool__(self) -> bool:
        return self.ok


class RetryGuard:
    """「这一次真重试放不放行」的唯一判据。"""

    @staticmethod
    def admit(ledger: AttemptLedger, nxt: SpecKey, cause_code: str) -> Decision:
        """通用的重试检查（与回退策略无关）：

        * native 之后不再回 safe（单向）；
        * 一次 run 里执行语义最多变一次（safe→native 至多一次）；
        * 同一份执行身份、同一个根因已经试过 → 没有新根因，不重复。
        """
        seen = ledger.current()
        if seen and seen[-1].spec.profile == PROFILE_NATIVE and nxt.profile != PROFILE_NATIVE:
            return Decision(False, REASON_NATIVE_TO_SAFE)
        if nxt.profile == PROFILE_NATIVE and any(r.spec.profile == PROFILE_NATIVE for r in seen):
            return Decision(False, REASON_ALREADY_FELL_BACK)
        for rec in seen:
            if rec.spec == nxt and (rec.code or cause_code) == cause_code:
                return Decision(False, REASON_NO_NEW_ROOT_CAUSE)
        return Decision(True)


def decide_fallback(
    *,
    mode: str,
    enabled: bool,
    ledger: AttemptLedger,
    cause_code: str,
    nxt: SpecKey,
    cancelled: bool = False,
    stale: bool = False,
    desktop_initiated: bool = False,
) -> Decision:
    """safe 尝试以 `cause_code` 收场之后，要不要立刻用 `nxt`（native）再来一次。纯函数（读账本快照）。

    顺序即优先级：先看是不是桌面用户主动发起的（调用方须传服务端验证过的事实，默认 False），再看用户 / 运维有没有关掉，再看有没有被取消 / 过期，再看原因本身该不该回退，最后过通用闸门。"""
    if not desktop_initiated:
        return Decision(
            False, REASON_NOT_DESKTOP
        )  # D1 只授权桌面用户主动发起的运行：默认 fail closed
    if not enabled:
        return Decision(False, REASON_DISABLED)
    if mode == MODE_SAFE_ONLY:
        return Decision(False, REASON_SAFE_ONLY)
    if cancelled:
        return Decision(False, REASON_CANCELLED)
    if stale:
        return Decision(False, REASON_STALE)
    if not may_fall_back(cause_code):
        return Decision(False, REASON_NOT_BLOCKED)
    if nxt.profile != PROFILE_NATIVE:
        return Decision(False, REASON_NATIVE_TO_SAFE)
    return RetryGuard.admit(ledger, nxt, cause_code)


def public_history(records: Iterable[AttemptRecord]) -> list[dict]:
    return [r.public() for r in records]
