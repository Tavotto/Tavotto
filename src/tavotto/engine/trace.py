"""Trace —— 有界的阶段轨迹，只回答「现在卡在哪一步、是哪一步坏的」（统一实施包 U09，ADR 0071）。

00_MASTER_PROMPT §2 把 Trace 列为 RenderCore 八项基础设施之一；`phases/U09_join.md` 说得更窄：
**默认有界、能定位当前错误阶段，不建全系统追踪平台**。所以它就是一条阶段序列：准备 / 执行 / 捕获 /
编译 / 合成 / 栅格 / 检查 / 发布……每一步一条记录（阶段名、相对起点的毫秒、结果、错误码），
条数有上限（`DEFAULT_LIMIT`），超过就丢**中间**的、记 `truncated`——头（哪里开始）与尾（哪里坏）永远留着。

它**不记内容**：没有路径、没有脚本正文、没有图内文字、没有 argv——`facts` 只许放标量与短串
（`_scalar()` 把别的东西一律换成类型名），公开投影（报告 / 遥测 / 诊断包）可以原样带它。
节点级的来源关系（哪个画布对象来自哪份源、哪份回执）不在这里——那是 `rendercore.job.plan_facts()`
的 `nodes`，同样只记真实知道的 id 与关系，外来页不编造内部语义。

纯标准库；准备接口（`preparation`）与导出作业（`exportjob`）各持一份，两边同一个形状。
"""

from __future__ import annotations

import time

TRACE_VERSION = 1
DEFAULT_LIMIT = 64

OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"
OUTCOMES = (OUTCOME_OK, OUTCOME_FAILED, OUTCOME_CANCELLED)

#: 阶段名闭集（两条链共用；每个名字只在一个地方产生）。准备接口：plan → check（过期判定）→ spawn →
#: execute（脚本在跑）→ receipt；导出作业：prepare → source（冻结 / 现画）→ compile → compose（Canonical PDF）
#: → raster → inspect → publish → report。加名字先加到这里（`tests/test_trace.py` 钉闭集）。
PHASES = (
    "plan",
    "check",
    "spawn",
    "execute",
    "receipt",
    "prepare",
    "source",
    "compile",
    "compose",
    "raster",
    "inspect",
    "publish",
    "report",
)

_MAX_STR = 120


def _scalar(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[: _MAX_STR - 1] + "…"
    return type(value).__name__


class Trace:
    """有界阶段轨迹。`mark()` 记一步；`fail()` / `cancel()` 记坏在哪一步；`to_payload()` 给投影。"""

    def __init__(self, *, limit: int = DEFAULT_LIMIT) -> None:
        if limit < 2:
            raise ValueError("limit 至少 2：头尾各留一条")
        self.limit = int(limit)
        self._t0 = time.perf_counter()
        self._events: list[dict] = []
        self._dropped = 0
        self.failed_phase: str | None = None
        self.failed_code: str | None = None

    # ---- 记 ----
    def mark(
        self, phase: str, outcome: str = OUTCOME_OK, *, code: str | None = None, **facts
    ) -> dict:
        if phase not in PHASES:
            raise ValueError(f"阶段名不在闭集里: {phase!r}")
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome 非法: {outcome!r}")
        event = {
            "phase": phase,
            "at_ms": round((time.perf_counter() - self._t0) * 1000.0, 3),
            "outcome": outcome,
        }
        if code:
            event["code"] = _scalar(str(code))
        if facts:
            event["facts"] = {str(k): _scalar(v) for k, v in facts.items()}
        if len(self._events) >= self.limit:
            # 丢中间的（第 2 条）：头留着说明从哪开始，尾留着说明现在在哪
            del self._events[1]
            self._dropped += 1
        self._events.append(event)
        if outcome != OUTCOME_OK and self.failed_phase is None:
            # 第一次坏在哪就是哪：后面跟着的失败（部分失败的收尾、发布那一步的连带）不盖掉根因
            self.failed_phase = phase
            self.failed_code = event.get("code")
        return event

    def fail(self, phase: str, code: str, **facts) -> dict:
        return self.mark(phase, OUTCOME_FAILED, code=code, **facts)

    def cancel(self, phase: str, **facts) -> dict:
        return self.mark(phase, OUTCOME_CANCELLED, code="cancelled", **facts)

    # ---- 读 ----
    @property
    def current_phase(self) -> str | None:
        return self._events[-1]["phase"] if self._events else None

    @property
    def truncated(self) -> bool:
        return self._dropped > 0

    def to_payload(self) -> dict:
        return {
            "trace_version": TRACE_VERSION,
            "events": [dict(e) for e in self._events],
            "limit": self.limit,
            "truncated": self.truncated,
            "dropped": self._dropped,
            "current_phase": self.current_phase,
            "failed_phase": self.failed_phase,
            "failed_code": self.failed_code,
        }


__all__ = [
    "DEFAULT_LIMIT",
    "OUTCOMES",
    "OUTCOME_CANCELLED",
    "OUTCOME_FAILED",
    "OUTCOME_OK",
    "PHASES",
    "TRACE_VERSION",
    "Trace",
]
