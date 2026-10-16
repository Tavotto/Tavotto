"""诊断报告随附的「错误摘要」声明（可选；契约出处 infra 仓库 `docs/diagnostics-api/error-tracking-summary.md`）。

用户**明确点了发送**的那一刻，若匿名统计在当前这一版采集范围（`telemetry.CONSENT_VERSION`）下是开着的、
`TAVOTTO_NO_TELEMETRY` 没设，就给 `POST /v1/reports/init` 多带一个 `error_summary`：同意快照 + 一条最小事实。
任何一项不满足 = 不带（只走自有报告）。服务端会按同一契约**重新验证**每个字段——这里仍把投影做成封闭的：

* 同意快照只有四个键，**没有 install_id、没有时间戳**；
* 事实只来自引擎自己登记的任务快照（`taskdiag`，本身就是白名单投影）里的**稳定错误码**，组件 / 操作是闭集；
  用户脚本的运行与准备（`script_run` / `preparation`）一律不算内部故障，不取；
* 内部帧：当前没有任何结构化来源（引擎不保留异常对象，日志里的 traceback 是文本且混有用户帧），所以 `frames`
  恒为空——不从文本里抠帧；以后若有可信来源，也只许经 `_frame_ok` 的同一把尺子；
* 错误码 / 帧的形状正则是服务端 `contract.ERRSUM_*` 的镜像（看护：`tests/test_diag_summary.py` 的契约表）。

点「发送」不表示同意未来自动上报；这里不写任何持久状态，也不影响统计开关。
"""

from __future__ import annotations

import json
import re

from . import taskdiag, telemetry

SCHEMA = 1
PURPOSE = "error_summary"

#: 服务端 `contract.ERRSUM_*` 的镜像。服务端加值是加法；这里只用到其中的子集。
COMPONENTS = frozenset(
    {
        "engine",
        "render",
        "export",
        "environment",
        "editor",
        "desktop",
        "updater",
        "worker",
        "diagnostics",
    }
)
OPERATIONS = frozenset(
    {"render", "export", "load", "save", "startup", "update", "repair", "edit", "verify", "other"}
)
ERROR_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{2,47}$")
FRAME_MODULE_RE = re.compile(r"^tavotto(?:\.[a-z][a-z0-9_]{0,31}){1,6}$")
FRAME_FUNCTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_FRAMES = 8
MAX_LINENO = 99_999

#: 任务快照类别 → (组件, 操作)。只有引擎自己的两类；脚本运行 / 准备不在表里。
_SOURCES = {
    taskdiag.KIND_EXPORT: ("export", "export"),
    taskdiag.KIND_DEPENDENCY: ("environment", "repair"),
}


def _frame_ok(frame: object) -> bool:
    return (
        isinstance(frame, dict)
        and set(frame) == {"module", "function", "lineno"}
        and isinstance(frame["module"], str)
        and FRAME_MODULE_RE.fullmatch(frame["module"]) is not None
        and isinstance(frame["function"], str)
        and FRAME_FUNCTION_RE.fullmatch(frame["function"]) is not None
        and type(frame["lineno"]) is int
        and 1 <= frame["lineno"] <= MAX_LINENO
    )


def consent_snapshot() -> dict | None:
    """此刻的有效同意快照；不满足（没同意 / 旧版同意 / 硬开关）= None。"""
    if telemetry.hard_disabled() or not telemetry.enabled():
        return None
    return {
        "telemetry_consent": True,
        "no_telemetry_env": False,
        "scope_version": telemetry.CONSENT_VERSION,
        "purposes": [PURPOSE],
    }


def _facts(project_id: str) -> dict | None:
    best = None
    for kind in _SOURCES:
        entry = taskdiag.STORE.latest_failure(project_id, kind)
        if entry is not None and (best is None or entry.recorded_at > best.recorded_at):
            best = entry
    if best is None:
        return None
    try:
        snap = json.loads(best.blob)
    except ValueError:
        return None
    err = snap.get("error") if isinstance(snap, dict) else None
    error_code = err.get("code") if isinstance(err, dict) else None
    if not isinstance(error_code, str) or not ERROR_CODE_RE.fullmatch(error_code):
        return None
    component, operation = _SOURCES[best.kind]
    return {
        "component": component,
        "operation": operation,
        "error_code": error_code,
        "frames": [],
    }


def build(project_id: str) -> dict | None:
    """最终发送时的 `error_summary` 声明；没有同意或没有合格的内部事实 = None（不带这个字段）。"""
    consent = consent_snapshot()
    if consent is None or not project_id:
        return None
    facts = _facts(project_id)
    if facts is None:
        return None
    return {"schema": SCHEMA, "consent": consent, "facts": facts}
