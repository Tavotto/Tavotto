"""准备会话（T01）：「只有脚本、还没有 asset_id」的目标和已有素材共用的可恢复准备 / 执行合同。

`preparation.py` 管**一次**准备（计划 / 观测 / 取消 / 回执），`pool.build_owned` 管执行；本模块是它们
上面的**用户操作层**——一个项目里一个目标（一份脚本，或一张已知的图）对应一个会话，会话里有
多轮不可变的计划与尝试。它**不**是新的任务系统、新的 resolver、新的 worker：检查复用
`preparation.plan_for`，执行复用 `PreparationService.start` → `pool.build_owned`，登记复用
`probe.register_probed`；这里只加四样东西：

* **身份层次**：`session_id`（可恢复的用户体验）· `config_revision`（语义修订：目标 / 解释器 / 工作目录 /
  授权 / 数据绑定变了才 +1）· `observation_seq`（单调的观察序号，进度变化只动它，不让用户正在填的配置
  过期）· `attempt_id`（= 一次执行的 `PreparationPlan.plan_id`）；
* **纯派生的 phase**（`derive`）：只吃「检查 / 尝试 / 失效 / 是否在等输入」这几件已观察到的事实，吐出
  phase + outcome + 是否可执行；`unknown` 不当通过；前端只保存这份投影，不另算；
* **后端生成的动作**（`run` / `cancel` / `recheck` / `prepare_dependencies`）：不透明 id，绑定会话、
  `config_revision`、检查那一刻的计划与影响摘要；确认前一行用户代码不跑、一个包不装（T06：依赖安装的授权绑定
  `deprepair.impact_digest`——安装集合 / 目标环境与代 / 写入范围变了就是另一份授权，进度与文案变了不是）；
* **check-use 窗口**：「比对修订 → 核计划是否失效 → 认领动作 → 提交给 provider」在会话锁内一次完成，
  重复点击 / 两个标签页认领的是同一次尝试，不重复起 worker。

纯标准库 + 兄弟模块（Flask 父进程 import 链上）；engine 不 import app：执行线程怎么起、怎么绑项目、
捕获之后怎么登记，全是 app 在调用点注入的回调。会话只存在内存里——应用重启后 GET 一个旧会话 id 得到
`preparation_session_not_found`（`params.reason = unknown_or_restarted`，客户端重新创建检查会话，
由当时的真实状态重新判定），**不自动重跑**。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from . import deprepair, pool, preparation, registry, scriptargs
from .preparation import TARGET_SCRIPT

LOG = logging.getLogger("tavotto.prepsession")

SESSION_VERSION = 1

# ---------------------------------------------------------------- 词汇（闭集）

#: phase 词汇沿用附件（`scanning` 由 T02 的有界扫描产生，`preparing_environment` 由 T06 的依赖安装产生；
#: 词汇先收进来，保证前端只面对一份闭集）。
PHASE_SCANNING = "scanning"
PHASE_AWAITING_CONFIRMATION = "awaiting_confirmation"
PHASE_PREPARING_ENVIRONMENT = "preparing_environment"
PHASE_AWAITING_CONFIGURATION = "awaiting_configuration"
PHASE_READY_TO_RUN = "ready_to_run"
PHASE_RUNNING = "running"
PHASE_AWAITING_RUNTIME_INPUT = "awaiting_runtime_input"
PHASE_COMPLETED = "completed"
PHASE_PARTIAL = "partial"
PHASE_ACTION_REQUIRED = "action_required"
PHASE_CANCELLED = "cancelled"
PHASES = (
    PHASE_SCANNING,
    PHASE_AWAITING_CONFIRMATION,
    PHASE_PREPARING_ENVIRONMENT,
    PHASE_AWAITING_CONFIGURATION,
    PHASE_READY_TO_RUN,
    PHASE_RUNNING,
    PHASE_AWAITING_RUNTIME_INPUT,
    PHASE_COMPLETED,
    PHASE_PARTIAL,
    PHASE_ACTION_REQUIRED,
    PHASE_CANCELLED,
)

#: 失败 / 结局事实永远在 outcome 里单列：phase 是「现在该关注什么」，outcome 是「发生了什么」。
OUTCOME_PENDING = "pending"  # 还没发生任何执行
OUTCOME_RUNNING = "running"
OUTCOME_SUCCEEDED = "succeeded"  # 执行完且捕获到图（脚本目标）/ 目标面板可用（素材目标）
OUTCOME_NO_FIGURE = "execution_finished_no_figure"  # 跑完了，但没有可编辑的图
OUTCOME_FAILED = "failed"
OUTCOME_BLOCKED = "blocked"  # 检查就判定走不通
OUTCOME_CANCELLED = "cancelled"
OUTCOME_STATIC = "static_source"  # 没有脚本，只有静态原件
OUTCOME_NEEDS_INPUT = "needs_input"  # 旧协议终局 needs_input 的投影
OUTCOME_STALE = "stale"  # 检查那一刻的世界变了
OUTCOME_UNKNOWN = "unknown"  # 有检查项判不出来，或尝试的记录已经过期
#: 脚本跑到一半发现缺包（运行时才知道的那类）：这次执行已经发生，补齐之后是**新的一次尝试**，不是从异常点继续
OUTCOME_NEEDS_DEPENDENCIES = "needs_dependencies"
OUTCOME_KINDS = (
    OUTCOME_PENDING,
    OUTCOME_RUNNING,
    OUTCOME_SUCCEEDED,
    OUTCOME_NO_FIGURE,
    OUTCOME_FAILED,
    OUTCOME_BLOCKED,
    OUTCOME_CANCELLED,
    OUTCOME_STATIC,
    OUTCOME_NEEDS_INPUT,
    OUTCOME_STALE,
    OUTCOME_UNKNOWN,
    OUTCOME_NEEDS_DEPENDENCIES,
)

CHECK_OK = "ok"
CHECK_UNKNOWN = "unknown"
CHECK_NEEDS_ACTION = "needs_action"
CHECK_BLOCKED = "blocked"
CHECK_STATUSES = (CHECK_OK, CHECK_UNKNOWN, CHECK_NEEDS_ACTION, CHECK_BLOCKED)

ACTION_RUN = "run"
ACTION_CANCEL = "cancel"
ACTION_RECHECK = "recheck"
#: 授权一次依赖准备（T06）：认领 = 用户确认了动作上说清的影响（`impact`），后端按 `impact_digest` 核对后起作业
ACTION_PREPARE = "prepare_dependencies"
ACTION_KINDS = (ACTION_RUN, ACTION_CANCEL, ACTION_RECHECK, ACTION_PREPARE)

# 稳定错误码（协议契约：code 不许改，文案随便改）
ERROR_NOT_FOUND = "preparation_session_not_found"
ERROR_ACTION_UNKNOWN = "preparation_action_unknown"
ERROR_REVISION_CHANGED = "preparation_config_revision_changed"
ERROR_NOT_RUNNABLE = "preparation_not_runnable"
ERROR_SESSIONS_FULL = "preparation_sessions_full"
#: 用户确认的影响与此刻要执行的不是同一份（集合 / 目标 / 环境代 / 写入范围变了）：零副作用，带新的影响
ERROR_IMPACT_CHANGED = "preparation_impact_changed"
#: 会改用户自己环境的动作必须回显它看到的 `impact_digest`（不能只靠"点了一下"）
ERROR_IMPACT_UNCONFIRMED = "preparation_impact_unconfirmed"
ERROR_BAD_REQUEST = "bad_request"

#: 「跑完了但没有图」的两个 code（`pool.NO_FIGURES_CODE` / `_SILENT_CODE` 的值；这里不 import pool 的常量
#: 名，闭集字面量由 `tests/test_preparation_session.py` 钉着与 pool 一致）。
_NO_FIGURE_CODES = frozenset({"no_figures_captured", "no_figures_captured_silent"})
#: 旧协议终局 `needs_input` 里 `required_input.code` → phase。
_WORKDIR_CODE = "workdir_confirmation_required"
_DEPENDENCY_CODE = "dependency_preparation_required"
#: 脚本跑到一半才发现缺包（`PreparationResult.error.code`）
_RUNTIME_MISSING_CODE = "missing_dependency"
#: 全局显式解释器压着项目级决定：为项目装的环境不会被用（`deprepair.ERROR_INTERPRETER_PINNED`）
_PINNED_CODE = "dependency_interpreter_pinned"
#: 受管环境里别的作用域装进去的版本与本作用域的声明互斥（`depplan.BLOCK_SCOPE_CONFLICT`）
_SCOPE_CONFLICT_CODE = "dependency_scope_conflict"
#: 环境检查项的 needs_action code（T05）：项目有自己的环境线索，用户还没选
_ENVIRONMENT_CODE = "environment_choice_required"
#: 参数（T07）：脚本里有 argparse 的字面量声明——给一份表单建议。**不是**待答项：识别不全 / 静态看缺必填都照样
#: 能运行（argv 的权威是 token；真 parser 说缺参时是既有的 `script_needs_arguments`）
_ARGUMENTS_CODE = "script_arguments_available"
#: 上一次尝试读不到数据（ADR 0106 的 `missing_input` 载荷）：回答走既有 `/api/engine/input-remap`，答完 `recheck`
_MISSING_INPUT_CODE = "missing_input"

#: 数量 / 生命周期预算。活跃的（有没跑完 / 没登记完的尝试）永远不被这里回收。
MAX_SESSIONS = 64
IDLE_TTL_S = 30 * 60
#: 每个会话保留多少个已认领的动作 id（重复点击要找得到当初认领的尝试）。
_MAX_CONSUMED_ACTIONS = 32
#: 每个会话保留多少次依赖准备尝试（报告只读最近一次；更早的只是历史）。
_MAX_DEP_ATTEMPTS = 16
#: 依赖装好之后等环境租约放掉再重新检查的上限（秒）；超了照样检查（租约只影响 `plan_for` 读到的瞬时状态）。
_REPLAN_WAIT_S = 30.0


class SessionError(Exception):
    """会话层的结构化拒绝：app 层原样换成 `{error, code, params}` + 状态码。"""

    def __init__(self, code: str, message: str, status: int, params: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.params = params or {}


# ---------------------------------------------------------------- 检查（纯函数，吃计划）


def _check(check_id: str, status: str, code: str = "", **detail) -> dict:
    out = {"id": check_id, "status": status}
    if code:
        out["code"] = code
    if detail:
        out["detail"] = detail
    return out


def checks_of(plan: preparation.PreparationPlan) -> list[dict]:
    """从一份计划读出 checks。**每一项都只是计划已经记下的事实的投影**：环境来自 `plan.environment`、
    工作目录来自 `plan.workdir_decision`、依赖来自 `plan.dependency_preparation`、数据来自 `plan.binding`。
    判不出来的写 `unknown`——`unknown` 不是通过。"""
    if plan.script is None:
        return []  # 没有脚本可跑：没有要检查的执行条件（静态素材）
    env = plan.environment or {}
    env_error = env.get("error")
    checks: list[dict] = [_check("target", CHECK_OK)]
    decision = ((env.get("recommendation") or {}).get("decision")) or {}
    if env_error:
        checks.append(_check("environment", CHECK_BLOCKED, str(env_error.get("code") or "")))
    elif decision.get("needs_decision"):
        # T05：项目里有自己的环境线索而用户还没决定用哪个——这不是机器的决定。建议在 requirements 里，
        # 回答走既有的采用端点（`PATCH /api/engine/environment`）或选回内置，答完 `recheck`
        checks.append(_check("environment", CHECK_NEEDS_ACTION, _ENVIRONMENT_CODE))
    elif plan.interpreter:
        checks.append(_check("environment", CHECK_OK, source=str(env.get("source") or "")))
    else:
        checks.append(_check("environment", CHECK_UNKNOWN))
    decision = plan.workdir_decision
    if decision is None:
        checks.append(_check("workdir", CHECK_UNKNOWN))
    elif decision.get("needs_confirmation"):
        checks.append(_check("workdir", CHECK_NEEDS_ACTION, _WORKDIR_CODE))
    else:
        checks.append(_check("workdir", CHECK_OK, decided=bool(decision.get("decided"))))
    required_code = str((plan.required_input or {}).get("code") or "")
    offer = plan.dependency_preparation
    pinned = (offer or {}).get("pinned")
    if pinned and ((offer.get("plan") or {}).get("status")) == "ready":
        # E05：全局显式解释器压着，缺的包装进别的环境也不会被用——不假装"依赖没问题"，也不提供授权动作；
        # 说清是谁锁的（来源与变量名），解开它才是出路
        checks.append(
            _check(
                "dependencies",
                CHECK_BLOCKED,
                _PINNED_CODE,
                pinned={"source": pinned.get("source"), "variable": pinned.get("variable")},
            )
        )
    elif required_code == _DEPENDENCY_CODE:
        # T06：要用户授权的是一份有摘要的影响（集合 / 目标环境与代 / 写入范围），摘要不透明、不含路径
        digest = str((plan.required_input or {}).get("impact_digest") or "")
        checks.append(
            _check(
                "dependencies",
                CHECK_NEEDS_ACTION,
                _DEPENDENCY_CODE,
                **({"impact_digest": digest} if digest else {}),
            )
        )
    elif offer is None:
        # 工作目录要先答 / 环境走不通时依赖没有被评估——如实 unknown，不虚构已满足
        checks.append(_check("dependencies", CHECK_UNKNOWN))
    elif ((offer.get("plan") or {}).get("status")) == "blocked":
        reasons = [str(b.get("code") or "") for b in (offer["plan"].get("blocked") or ())]
        # 作用域互斥（D04）单列一个码：它有用户能走的出路（换成本作用域 / 子目录独立成项目），其余 blocked 没有
        code = _SCOPE_CONFLICT_CODE if _SCOPE_CONFLICT_CODE in reasons else "dependency_blocked"
        checks.append(_check("dependencies", CHECK_BLOCKED, code, reasons=reasons[:8]))
    else:
        checks.append(_check("dependencies", CHECK_OK))
    checks.append(
        _check("data", CHECK_OK) if plan.binding is not None else _check("data", CHECK_UNKNOWN)
    )
    schema = arguments_schema(plan)
    if schema is not None:
        # T07：只有 argparse 的字面量证据时才出现；永远 ok——参数表单是建议，不是执行门槛
        checks.append(_check("arguments", CHECK_OK, **scriptargs.summary(schema)))
    return checks


def arguments_schema(plan: preparation.PreparationPlan) -> dict | None:
    """计划里那份脚本的静态参数 schema（`scriptargs.analyze_file`，只读源码、按 mtime 缓存）；没有 argparse 证据 → None。"""
    if plan.script is None or plan.target != TARGET_SCRIPT:
        return None
    schema = scriptargs.analyze_file(Path(plan.project_root) / plan.script)
    if schema["status"] not in (scriptargs.STATUS_COMPLETE, scriptargs.STATUS_PARTIAL):
        return None
    return schema


def requirements_of(
    plan: preparation.PreparationPlan,
    checks: list[dict],
    *,
    runtime_missing: dict | None = None,
    missing_input: dict | None = None,
) -> list[dict]:
    """要用户先答的事（`needs_action` 的检查项）。载荷原样是既有的 `required_input`——回答走既有端点，
    答完 `recheck`；会话不复制那两条回答协议。

    `runtime_missing`（T06）：上一次尝试跑到一半发现缺包时的差异计划（`_observe_missing` 算出）——不是检查项，
    是**那次执行已经发生之后**多出来的一件要授权的事；补齐之后要新的一次尝试。"""
    out = []
    for c in checks:
        if c["status"] != CHECK_NEEDS_ACTION:
            continue
        if c["id"] == "environment":
            # T05：载荷是只读的环境建议（候选 id + 环境代 + 证据标签）；回答绑定候选身份与环境代，
            # 过期的回答由采用端点的 `expected_generation` 拒绝
            out.append(
                {
                    "id": c["id"],
                    "kind": "environment_choice",
                    "code": c.get("code", ""),
                    "payload": dict((plan.environment or {}).get("recommendation") or {}),
                }
            )
            continue
        out.append(
            {
                "id": c["id"],
                "kind": "workdir_choice" if c["id"] == "workdir" else "dependency_authorization",
                "code": c.get("code", ""),
                "payload": dict(plan.required_input) if plan.required_input else None,
            }
        )
    deps = next((c for c in checks if c["id"] == "dependencies"), None)
    if (
        deps is not None
        and deps["status"] == CHECK_BLOCKED
        and deps.get("code") == _SCOPE_CONFLICT_CODE
    ):
        # D04：不硬合并——把冲突项（谁装的哪个版本 / 本作用域要什么）和两条出路交给用户；"换成本作用域"有它自己
        # 的影响摘要（动作 `prepare_dependencies` 引用），"子目录独立成项目"在会话之外（开那个子目录）
        offer = plan.dependency_preparation or {}
        blocked = next(
            (
                b
                for b in ((offer.get("plan") or {}).get("blocked") or ())
                if b.get("code") == _SCOPE_CONFLICT_CODE
            ),
            {},
        )
        switch = offer.get("scope_switch") or {}
        out.append(
            {
                "id": "dependencies",
                "kind": "dependency_scope_choice",
                "code": _SCOPE_CONFLICT_CODE,
                "payload": {
                    "conflicts": list(blocked.get("conflicts") or ()),
                    "options": list(blocked.get("options") or ()),
                    "switch": (
                        {
                            "impact_digest": switch.get("impact_digest"),
                            "requirements": list(switch.get("requirements") or ()),
                            "drops": list(switch.get("drops") or ()),
                            "changes": list(switch.get("changes") or ()),
                        }
                        if switch
                        else None
                    ),
                },
            }
        )
    if deps is not None and deps["status"] == CHECK_BLOCKED and deps.get("code") == _PINNED_CODE:
        out.append(
            {
                "id": "dependencies",
                "kind": "dependency_pinned",
                "code": _PINNED_CODE,
                "payload": dict((deps.get("detail") or {}).get("pinned") or {}),
            }
        )
    if runtime_missing is not None:
        out.append(
            {
                "id": "dependencies",
                "kind": "dependency_authorization",
                "origin": "runtime_missing",
                "code": _RUNTIME_MISSING_CODE,
                "payload": dict(runtime_missing),
            }
        )
    if missing_input is not None:
        # T07：上一次尝试读不到数据——载荷就是既有指认对话框那份（ADR 0106）；用户亲手指认、同名不同内容不就近猜。
        # 不阻塞再次运行（用户也可能自己把文件放回去），答完 `recheck` 换修订
        out.append(
            {
                "id": "data",
                "kind": "input_location",
                "origin": "last_attempt",
                "code": _MISSING_INPUT_CODE,
                "blocking": False,
                "payload": dict(missing_input),
            }
        )
    if any(c["id"] == "arguments" for c in checks):
        schema = arguments_schema(plan)
        if schema is not None:
            out.append(
                {
                    "id": "arguments",
                    "kind": "script_arguments",
                    "code": _ARGUMENTS_CODE,
                    "blocking": False,
                    "payload": {
                        "schema": schema,
                        # 这次检查用的配置：只有个数与本机引用（参数值不进报告）；表单写回的是前端草稿里的 token
                        "argv_count": len(plan.run.argv) if plan.run is not None else 0,
                        "run_config": plan.run.config_id if plan.run is not None else None,
                    },
                }
            )
    return out


# ---------------------------------------------------------------- phase（纯派生）


def derive(facts: dict) -> dict:
    """已观察到的事实 → `{phase, outcome, runnable, execution_finished, figure_captured}`。

    `facts`：

        target_kind    "script" | "asset"
        has_script     计划里有没有脚本可跑
        checks         `checks_of()` 的结果
        stale          None | {"reason": …}——检查那一刻的世界已经变了（认领动作时发现）
        attempt        None | {status, error, required_input_code, finalized, finalize,
                               captured_count, cancel_requested}（`status` 是 `PreparationResult.status`）
        awaiting_input 这次尝试的脚本此刻有没有在等一个 `input()` 的回答
        dependency     None | {running, state, code, joined, reason}（T06）：比最近一次执行尝试更晚的那次
                       依赖准备；`running` 含「装完了、正在按新环境重新检查」

    纯函数、不读时钟、不读 provider：黄金向量 `tests/golden/preparation_session_vectors.json`
    逐条钉它的输出。provider 的状态保留原权威——这里只**投影**，不另造一份可运行判据。
    """
    dep = facts.get("dependency")
    if dep is not None:
        if dep["running"]:
            reason = "joined_existing" if dep.get("joined") else str(dep.get("reason") or "")
            return _result(PHASE_PREPARING_ENVIRONMENT, OUTCOME_RUNNING, reason=reason)
        if dep["state"] == deprepair.STATE_CANCELLED:
            return _result(
                PHASE_AWAITING_CONFIRMATION,
                OUTCOME_CANCELLED,
                code=str(dep.get("code") or ""),
                reason="dependency_preparation",
            )
        if dep["state"] == deprepair.STATE_FAILED:
            return _result(
                PHASE_ACTION_REQUIRED,
                OUTCOME_FAILED,
                code=str(dep.get("code") or "") or "dependency_install_failed",
                reason="dependency_preparation",
            )
    attempt = facts.get("attempt")
    if attempt is None or (facts.get("stale") and not attempt_running(attempt)):
        # 没有（当前修订的）尝试，或检查那一刻的世界已经变了而没有尝试在跑：phase 由检查 / 失效决定，
        # 不能让上一次的 `completed` 盖住「现在不能再用这份检查」
        return _derive_before_attempt(facts)
    status = attempt["status"]
    error = attempt.get("error") or {}
    code = str(error.get("code") or "")
    if attempt_running(attempt):
        reason = "cancel_requested" if attempt.get("cancel_requested") else ""
        return _result(
            PHASE_AWAITING_RUNTIME_INPUT if facts.get("awaiting_input") else PHASE_RUNNING,
            OUTCOME_RUNNING,
            reason=reason,
        )
    if status == preparation.STATUS_CANCELLED:
        return _result(PHASE_CANCELLED, OUTCOME_CANCELLED, finished=False, captured=False)
    if status == preparation.STATUS_STATIC:
        return _result(PHASE_COMPLETED, OUTCOME_STATIC, finished=None, captured=False)
    if status == preparation.STATUS_NEEDS_INPUT:
        needs = str(attempt.get("required_input_code") or "")
        phase = {
            _WORKDIR_CODE: PHASE_AWAITING_CONFIGURATION,
            _DEPENDENCY_CODE: PHASE_AWAITING_CONFIRMATION,
        }.get(needs, PHASE_ACTION_REQUIRED)
        return _result(phase, OUTCOME_NEEDS_INPUT, code=needs, finished=False, captured=False)
    if status == preparation.STATUS_ERROR:
        if code == preparation.ERROR_PLAN_STALE:
            return _result(
                PHASE_ACTION_REQUIRED,
                OUTCOME_STALE,
                code=code,
                reason=str(error.get("reason") or ""),
                finished=False,
                captured=False,
            )
        if code == _RUNTIME_MISSING_CODE and attempt.get("missing_installable"):
            # 这次执行已经发生（脚本跑到缺包那一行）：事实保留，补齐之后是新的一次尝试——phase 回到等授权，
            # outcome 如实说「需要依赖」，不叫"继续"
            return _result(
                PHASE_AWAITING_CONFIRMATION,
                OUTCOME_NEEDS_DEPENDENCIES,
                code=code,
                reason="rerun_required",
                finished=True,
                captured=False,
            )
        if code in _NO_FIGURE_CODES:
            # 脚本跑完了，只是没有可编辑的图：执行完成 ≠ 首图成功
            return _result(
                PHASE_PARTIAL, OUTCOME_NO_FIGURE, code=code, finished=True, captured=False
            )
        return _result(
            PHASE_ACTION_REQUIRED,
            OUTCOME_FAILED,
            code=code or "worker_error",
            finished=False,
            captured=False,
        )
    # status == ready 且登记（finalize）已经结束
    finalize = attempt.get("finalize")
    if isinstance(finalize, dict) and finalize.get("registered") is False:
        # 图捕获到了，但没能登记成「按 stem 找得到的素材」（stem 冲突 / 改指代次变了）
        return _result(
            PHASE_PARTIAL,
            OUTCOME_FAILED,
            code=str(finalize.get("code") or "registration_failed"),
            finished=True,
            captured=True,
        )
    if facts.get("target_kind") == TARGET_SCRIPT and int(attempt.get("captured_count") or 0) == 0:
        return _result(PHASE_PARTIAL, OUTCOME_NO_FIGURE, finished=True, captured=False)
    return _result(PHASE_COMPLETED, OUTCOME_SUCCEEDED, finished=True, captured=True)


def attempt_running(attempt: dict) -> bool:
    """这次尝试是否还在进行：provider 没到终局，或到了终局但执行线程的收尾（登记 / 缺包差异计划）还没做完。

    与 `SessionService._has_active_attempt` 同一判据（T11）：`error` 在收尾之前也算进行中——
    `missing_dependency` 要等 `_observe_missing` 算出差异计划才说得清是「补包后重跑」还是「认不出包名」，
    先投影成 `failed` 会给出一个没有待办、随后又被改写的终局。取消落地即终局（T09：不等执行线程）。"""
    if attempt["status"] in (preparation.STATUS_PENDING, preparation.STATUS_RUNNING):
        return True
    if attempt["status"] == preparation.STATUS_CANCELLED:
        return False
    return not attempt.get("finalized")


def _derive_before_attempt(facts: dict) -> dict:
    stale = facts.get("stale")
    if stale:
        return _result(
            PHASE_ACTION_REQUIRED,
            OUTCOME_STALE,
            code=preparation.ERROR_PLAN_STALE,
            reason=str(stale.get("reason") or ""),
        )
    if not facts.get("has_script"):
        return _result(PHASE_COMPLETED, OUTCOME_STATIC, finished=None, captured=False)
    checks = facts.get("checks") or []
    for c in checks:
        if c["status"] == CHECK_BLOCKED:
            return _result(
                PHASE_ACTION_REQUIRED, OUTCOME_BLOCKED, code=c.get("code") or f"{c['id']}_blocked"
            )
    for c in checks:
        if c["status"] == CHECK_NEEDS_ACTION:
            phase = (
                PHASE_AWAITING_CONFIGURATION
                if c["id"] == "workdir"
                else PHASE_AWAITING_CONFIRMATION
            )
            return _result(phase, OUTCOME_PENDING, code=c.get("code") or "")
    for c in checks:
        if c["status"] == CHECK_UNKNOWN:
            # unknown 不当通过：有一项判不出来就不能说「可以运行」，给 recheck 重判
            return _result(PHASE_ACTION_REQUIRED, OUTCOME_UNKNOWN, code=f"{c['id']}_unknown")
    return _result(PHASE_READY_TO_RUN, OUTCOME_PENDING, runnable=True)


def _result(
    phase: str,
    kind: str,
    *,
    code: str = "",
    reason: str = "",
    runnable: bool = False,
    finished: bool | None = None,
    captured: bool | None = None,
) -> dict:
    outcome = {"kind": kind}
    if code:
        outcome["code"] = code
    if reason:
        outcome["reason"] = reason
    return {
        "phase": phase,
        "outcome": outcome,
        "runnable": runnable,
        "execution_finished": finished,
        "figure_captured": captured,
    }


# ---------------------------------------------------------------- 会话


@dataclasses.dataclass
class _Action:
    id: str
    kind: str
    config_revision: int
    #: run：被哪次尝试认领（None = 还没人认领）；cancel：它要取消的那次尝试
    attempt_id: str | None = None
    impact: dict = dataclasses.field(default_factory=dict)
    #: prepare_dependencies：用户看到的影响摘要（认领时按它核对）/ 运行时缺包的模块名 / 目标类型（私有，不对外）
    impact_digest: str = ""
    impact_core: dict = dataclasses.field(
        default_factory=dict
    )  # `deprepair.impact_of` 原样（不含动作层的标志）
    module: str = ""
    target_kind: str = ""
    scope_policy: str = ""


@dataclasses.dataclass
class _DepAttempt:
    """一次依赖准备尝试（会话拥有或认领了别人在跑的同一份作业）。provider 状态仍在 `deprepair`（进度记录），
    这里只记「这个会话认领了哪份作业、确认的是哪份影响、终局是什么」。"""

    plan_id: str
    action_id: str
    config_revision: int
    created_at: float
    impact: dict
    impact_digest: str
    module: str = ""
    #: 认领的是别人先起的同一份作业：本会话不拥有它，不提供取消（只退役自己拥有的工作）
    joined: bool = False
    finished: dict | None = None
    #: 终态（成功）之后，按新环境重新检查的那一步是否做完
    absorbed: bool = False
    #: 上一次通知过的（状态, 阶段）：pip 每一行输出都会来一个进度事件，只有阶段变了才值得提示"重新读报告"
    last_notified: tuple = ()


@dataclasses.dataclass
class _Attempt:
    attempt_id: str  # = 这次执行的 PreparationPlan.plan_id
    action_id: str
    config_revision: int
    created_at: float
    #: 执行线程是否已经把「执行之后的登记」做完（脚本目标要登记 / 物化才算图可被编辑请求找到）
    finalized: bool = False
    finalize: dict | None = None
    #: 这次执行以 `missing_dependency` 收场时的差异计划（`_observe_missing`）：模块名 / 能否在会话里授权 /
    #: 影响摘要。None = 不是这类终局
    missing: dict | None = None


@dataclasses.dataclass
class Session:
    session_id: str
    project_id: str
    project_root: str
    target: dict  # 私有全量（含 original_path）；公开投影见 `public_target()`
    plan: preparation.PreparationPlan
    fingerprint: str
    created_at: float
    touched_at: float
    config_revision: int = 1
    observation_seq: int = 0
    attempts: list[_Attempt] = dataclasses.field(default_factory=list)
    dep_attempts: list[_DepAttempt] = dataclasses.field(default_factory=list)
    #: 上一次依赖准备之后，按新环境重新算出的差额（`deprepair.impact_delta`）：只在同一个会话里呈现
    dependency_delta: dict | None = None
    actions: dict[str, _Action] = dataclasses.field(default_factory=dict)
    stale: dict | None = None
    signature: tuple = ()
    lock: threading.RLock = dataclasses.field(default_factory=threading.RLock, repr=False)

    def public_target(self) -> dict:
        t = self.target
        return {
            "kind": t["kind"],
            "script": t.get("script"),
            "entry": t.get("entry"),
            "asset_id": t.get("asset_id") or None,
            "stem": t.get("stem") or None,
            # T03：运行配置只露不透明引用与个数，不露参数值
            **(
                {"run_config": t["run"].config_id, "argv_count": len(t["run"].argv)}
                if t.get("run") is not None
                else {}
            ),
        }


def target_key(target: dict) -> str:
    if target["kind"] == TARGET_SCRIPT:
        # T03：同一脚本、不同 argv 是不同的执行意图——各有各的会话（互斥键含运行配置引用，不含 argv 原文）
        run = target.get("run")
        return f"script:{target['script']}" + (f"~{run.config_id}" if run is not None else "")
    return f"asset:{target['asset_id']}"


def _fingerprint(plan: preparation.PreparationPlan) -> str:
    """语义修订的判据（私有，含解释器路径，**不**对外）：目标、解释器、工作目录档、授权、数据绑定、
    门的结论变了才算「执行意图变了」。进度 / 文案 / 时间戳不在里面——它们只动 `observation_seq`。
    T03 起 argv / 运行配置也加进来。"""
    decision = plan.workdir_decision or {}
    dependencies = (plan.dependency_preparation or {}).get("plan") or {}
    payload = {
        "target": plan.target,
        "script": plan.script,
        "entry": plan.entry,
        "asset": plan.asset_id,
        "stem": plan.stem,
        "interpreter": plan.interpreter,
        # T05：同一路径上被重建的环境是另一代；「需要用户先选环境」也是执行意图的一部分
        "env_generation": (plan.environment or {}).get("generation"),
        "env_needs_decision": bool(
            (((plan.environment or {}).get("recommendation") or {}).get("decision") or {}).get(
                "needs_decision"
            )
        ),
        "cwd_mode": decision.get("mode"),
        "cwd_decided": decision.get("decided"),
        "grant": plan.grant,
        "binding": (plan.binding or {}).get("revision"),
        # T07：数据改指表的代次（ADR 0106）：用户指认了数据位置，同一个目标的执行意图就变了（新修订，旧失败不再是当前的）
        "input_remap": plan.input_remap_generation,
        "required": (plan.required_input or {}).get("code"),
        # T03：运行配置引用（不透明 id，换任何一个 token 都是新引用）——不放 argv 原文
        "run": plan.run.config_id if plan.run is not None else None,
        "env_error": ((plan.environment or {}).get("error") or {}).get("code"),
        # JointPlan owns the installation identity and source-input fingerprint.
        # Declarations remain relevant when the dependency gate is not evaluated.
        "deps": {
            key: dependencies.get(key)
            for key in ("status", "identity", "inputs_digest", "selection")
        },
        "dependency_intents": plan.dependency_intents,
        "dependency_conflicts": plan.dependency_conflicts,
        # T06：要装的集合 / 目标环境与代 / 写入范围变了，先前的确认就不覆盖它（摘要不含进度与文案）
        "deps_impact": (plan.dependency_preparation or {}).get("impact_digest"),
        "deps_switch": ((plan.dependency_preparation or {}).get("scope_switch") or {}).get(
            "impact_digest"
        ),
    }
    return json.dumps(payload, sort_keys=True, default=str)


class SessionService:
    """会话登记表。进程内一份（`SESSIONS`），测试可另起实例（传入自己的 `PreparationService`）。"""

    def __init__(
        self,
        prep: preparation.PreparationService | None = None,
        *,
        max_sessions: int = MAX_SESSIONS,
        idle_ttl_s: float = IDLE_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._prep = prep or preparation.SERVICE
        self._max = max_sessions
        self._ttl = idle_ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._by_id: dict[str, Session] = {}
        self._by_key: dict[tuple[str, str], Session] = {}
        #: app 注入：`notifier({"session_id", "project_id", "target", "config_revision"})`（只是「重新读报告」的提示）。
        #: 在会话锁**之外**调；它抛出的异常只记日志。
        self.notifier: Callable[[dict], None] | None = None

    # ---- 检查（不执行任何用户代码）----
    def check(
        self, *, project_id: str, project_root: str, target: dict, force: bool = False
    ) -> tuple[Session, bool]:
        """创建（或复用）这个目标的检查会话。回 `(会话, 是不是新建的)`。

        检查 = `preparation.plan_for` 的只读计划：不起 worker、不跑用户脚本。有尝试在跑的会话原样返回
        （不重新规划——规划不该在执行中途改写它依据的快照）。已有会话且**语义指纹没变**时保留旧计划与
        旧动作 id（无关的进度变化不撤销已给出的授权）；变了才 `config_revision + 1`、旧动作作废。
        `force`（`recheck` 动作用）：会话标了失效时无论指纹变没变都换新计划（指纹本来就变了的不需要它）。
        """
        key = (project_id, target_key(target))
        self._sweep()
        with self._lock:
            existing = self._by_key.get(key)
            if existing is not None and self._active(existing):
                return existing, False
        plan = preparation.plan_for(
            project_id=project_id,
            project_root=project_root,
            asset_id=target.get("asset_id") or "",
            stem=target.get("stem") or "",
            script=target.get("script"),
            entry=target.get("entry"),
            original_artifact=target.get("original_artifact"),
            original_path=target.get("original_path"),
            target=target["kind"],
            **({"run": target["run"]} if target.get("run") is not None else {}),
        )
        fingerprint = _fingerprint(plan)
        now = self._clock()
        created = False
        with self._lock:
            sess = self._by_key.get(key)
            if sess is None:
                self._make_room()
                sess = Session(
                    session_id=f"psess-{uuid.uuid4().hex}",
                    project_id=project_id,
                    project_root=str(project_root),
                    target=dict(target),
                    plan=plan,
                    fingerprint=fingerprint,
                    created_at=now,
                    touched_at=now,
                )
                self._by_id[sess.session_id] = sess
                self._by_key[key] = sess
                created = True
        with sess.lock:
            sess.touched_at = now
            if not created and not self._has_active_attempt(sess):
                if sess.stale or fingerprint != sess.fingerprint:
                    sess.target = dict(target)
                    sess.plan = plan
                    sess.fingerprint = fingerprint
                    sess.config_revision += 1
                    sess.stale = None
                    # 旧修订的动作全部作废（已认领的留着：重复点击要找得到当初的尝试）
                    sess.actions = {
                        k: v
                        for k, v in sess.actions.items()
                        if v.kind in (ACTION_RUN, ACTION_PREPARE) and v.attempt_id
                    }
        self._notify(sess)
        return sess, created

    # ---- 动作 ----
    def act(
        self,
        session_id: str,
        project_id: str,
        action_id: str,
        expected_config_revision: int | None,
        *,
        launch: Callable[[preparation.PreparationPlan, Callable], None],
        finalize: Callable[[preparation.PreparationPlan, preparation.PreparationResult], dict]
        | None = None,
        impact_digest: str | None = None,
        prepare: Callable[..., dict] | None = None,
    ) -> tuple[Session, bool, str]:
        """认领一个动作。回 `(会话, claimed, 动作 kind)`：`claimed=False` = 这个动作早已被认领（重复点击 / 另一个
        标签页），返回的是当初那次尝试，**没有任何新副作用**。

        `launch(plan, on_done)`：app 注入的「把这份计划交给执行线程」——绑项目、挂 `pool.build_owned`
        runner、把 `on_done` 传给 `PreparationService.start`。`finalize(plan, result)`：脚本目标执行成功之后
        的登记 / 物化（在项目绑定里、执行线程上调；回 `{"registered": bool, "code"?}`）。

        `prepare_dependencies`（T06）：`impact_digest` 是用户看到的影响摘要的回显（会改用户自己环境的动作**必须**
        带；别的动作带了也要对得上）；`prepare(...)` 是 app 注入的「按已确认的摘要起依赖作业」
        （`deprepair.start_confirmed` + SSE 转发），回 `{plan_id, started, joined, progress}`。
        """
        sess = self.session(session_id, project_id)
        recheck = False
        claimed = False
        kind = ""
        late: tuple[_DepAttempt, dict] | None = None
        with sess.lock:
            sess.touched_at = self._clock()
            action = sess.actions.get(action_id)
            if action is None:
                raise SessionError(
                    ERROR_ACTION_UNKNOWN, "这个动作不存在或已经失效", 404, {"id": action_id}
                )
            if expected_config_revision is None:
                raise SessionError(
                    ERROR_BAD_REQUEST,
                    "缺少 expected_config_revision",
                    400,
                    {"field": "expected_config_revision"},
                )
            if (
                expected_config_revision != sess.config_revision
                or action.config_revision != sess.config_revision
            ):
                raise SessionError(
                    ERROR_REVISION_CHANGED,
                    "准备配置在你确认之后变了，请重新查看再确认",
                    409,
                    {"config_revision": sess.config_revision},
                )
            kind = action.kind
            if action.kind == ACTION_RUN:
                if action.attempt_id is not None:
                    return sess, False, action.kind  # 幂等认领：同一个动作 → 同一次尝试
                claimed = self._claim_run(sess, action, launch=launch, finalize=finalize)
            elif action.kind == ACTION_PREPARE:
                if action.attempt_id is not None:
                    return sess, False, action.kind  # 幂等认领：同一个动作 → 同一份依赖作业
                claimed, late = self._claim_prepare(sess, action, impact_digest, prepare)
            elif action.kind == ACTION_CANCEL:
                # 只退役**这次会话拥有的**工作；共享 worker / 别人起的会话的所有权判断在
                # `PreparationService.cancel` 与 `pool.build_owned` 的 created 里，这里不重判
                if action.attempt_id in {d.plan_id for d in sess.dep_attempts}:
                    deprepair.cancel_status(action.attempt_id or "")
                else:
                    self._prep.cancel(action.attempt_id or "", project_id)
                claimed = True
            else:  # ACTION_RECHECK：规划有副作用面（解释器体检），不在会话锁里做
                recheck = True
        if late is not None:
            # 认领到的原作业在我们挂上监听之前已经到了终态：终局补一次
            self._dependency_event(sess, late[0], late[1])
        if recheck:
            self.check(
                project_id=sess.project_id,
                project_root=sess.project_root,
                target=sess.target,
                force=True,
            )
            claimed = True
        else:
            self._notify(sess)
        return sess, claimed, kind

    def _claim_prepare(
        self, sess: Session, action: _Action, echoed: str | None, prepare
    ) -> tuple[bool, tuple[_DepAttempt, dict] | None]:
        """会话锁内：核对修订（调用方已做）→ 核对回显的影响摘要 → 失效检查 → 认领动作 → 交给 `deprepair`。
        比较—消费—提交副作用之间没有无保护窗口；重复点击 / 另一个标签页走 `act` 里的幂等分支，不会到这里。"""
        if echoed is not None and echoed != action.impact_digest:
            # 调用方看到的不是这个动作现在的影响（读了旧报告）：动作本身仍有效，不标失效；把现在的影响交回去
            raise self._impact_changed(
                sess,
                {"impact": action.impact_core, "impact_digest": action.impact_digest},
                stale=False,
            )
        if action.impact.get("modifies_user_environment") and echoed is None:
            raise SessionError(
                ERROR_IMPACT_UNCONFIRMED,
                "这会直接修改你自己的 Python 环境，需要带上你看到的影响摘要（impact_digest）才能确认",
                400,
                {"field": "impact_digest"},
            )
        if self._has_active_attempt(sess):
            raise SessionError(ERROR_NOT_RUNNABLE, "这个会话已经有一次尝试在进行", 409)
        if prepare is None:
            raise SessionError(ERROR_NOT_RUNNABLE, "这个会话不能起依赖准备", 409)
        stale = preparation.stale_reason(sess.plan)
        if stale is not None:
            sess.stale = {"reason": stale[0]}
            sess.actions = {k: v for k, v in sess.actions.items() if v.attempt_id}
            raise SessionError(
                preparation.ERROR_PLAN_STALE,
                "检查之后环境 / 授权 / 数据变了，请重新检查",
                409,
                {"reason": stale[0], "executed": False},
            )
        attempt = _DepAttempt(
            plan_id="",
            action_id=action.id,
            config_revision=sess.config_revision,
            created_at=self._clock(),
            impact=dict(action.impact_core),
            impact_digest=action.impact_digest,
            module=action.module,
        )
        try:
            got = prepare(
                project_root=sess.project_root,
                script=sess.plan.script,
                digest=action.impact_digest,
                target_kind=action.target_kind,
                module=action.module,
                scope_policy=action.scope_policy,
                on_event=lambda snap: self._dependency_event(sess, attempt, snap),
            )
        except deprepair.RepairError as exc:
            if exc.code == deprepair.ERROR_IMPACT_CHANGED:
                raise self._impact_changed(sess, exc.extra) from exc
            # 其余（轮次用完 / 被固定 / 忙 / 计划不可执行…）如实带稳定码，什么都没执行
            raise SessionError(exc.code, str(exc), 409, {"executed": False}) from exc
        except (deprepair.pool.WorkerError, deprepair.privatepython.ProvisionError) as exc:
            # 解释器解析不出来（显式选择失效）/ 私有 Python 磁盘不足：带它们自己的稳定码，不当 500
            raise SessionError(
                str(getattr(exc, "code", "") or "dependency_target_unavailable"),
                str(exc),
                409,
                {"executed": False},
            ) from exc
        plan_id = str(got.get("plan_id") or "")
        if not plan_id:
            # 同一份摘要的作业在我们比较之后、认领之前结束了：世界已经变了，重新检查再来
            sess.stale = {"reason": "dependency_job_ended"}
            sess.actions = {k: v for k, v in sess.actions.items() if v.attempt_id}
            raise SessionError(
                preparation.ERROR_PLAN_STALE,
                "刚才的依赖准备已经结束，请重新检查",
                409,
                {"reason": "dependency_job_ended", "executed": False},
            )
        attempt.plan_id = plan_id
        attempt.joined = not bool(got.get("started"))
        sess.dep_attempts.append(attempt)
        del sess.dep_attempts[:-_MAX_DEP_ATTEMPTS]
        action.attempt_id = plan_id
        sess.dependency_delta = None
        snap = got.get("progress") or {}
        late = (
            (attempt, snap)
            if attempt.joined and snap.get("state") in deprepair.TERMINAL_STATES
            else None
        )
        return bool(got.get("started")), late

    def _impact_changed(self, sess: Session, extra: dict, *, stale: bool = True) -> SessionError:
        """用户确认的影响与此刻要执行的不是同一份，零副作用。`stale`：世界在确认之后变了（现算的计划对不上）——
        会话标失效、旧动作作废、等重新检查；否则只是调用方手里的摘要旧了，动作原样可用。"""
        if stale:
            sess.stale = {"reason": "dependency_impact_changed"}
            sess.actions = {k: v for k, v in sess.actions.items() if v.attempt_id}
        params: dict = {"executed": False, "reason": "dependency_impact_changed"}
        if isinstance(extra.get("impact"), dict):
            params["impact"] = extra["impact"]
        if extra.get("impact_digest"):
            params["impact_digest"] = extra["impact_digest"]
        return SessionError(
            ERROR_IMPACT_CHANGED,
            "要安装的内容 / 目标环境在你确认之后变了，请重新查看再确认",
            409,
            params,
        )

    # ---- 依赖作业的进度 / 终局 ----
    def _dependency_event(self, sess: Session, attempt: _DepAttempt, snap: dict) -> None:
        """`deprepair` 的进度事件（在安装线程上调）。非终态只提示「该重新读报告了」；终态记下终局，成功的
        按新环境重新检查（只重算差额，仍在这个会话里）。"""
        state = snap.get("state")
        if state not in deprepair.TERMINAL_STATES:
            marker = (state, snap.get("stage"))
            if marker != attempt.last_notified:
                attempt.last_notified = marker
                self._notify(sess)
            return
        with sess.lock:
            if attempt.finished is not None:
                return  # 终局只记一次（监听者与补发可能各来一次）
            attempt.finished = {
                "state": state,
                "code": str(snap.get("code") or ""),
                "retryable": snap.get("retryable"),
                "committed": bool(snap.get("committed")),
            }
        if state == deprepair.STATE_DONE:
            threading.Thread(
                target=self._replan_after_dependency,
                args=(sess, attempt),
                daemon=True,
                name="tavotto-prepsession-replan",
            ).start()
        else:
            with sess.lock:
                attempt.absorbed = True
            self._notify(sess)

    def _replan_after_dependency(self, sess: Session, attempt: _DepAttempt) -> None:
        """依赖装好了：环境变了，按新环境重新检查——只重算差额（装好的不再出现），仍在同一个会话 / 同一份
        报告里呈现。等作业把环境租约放掉再量（`plan_for` 要读这个环境）。"""
        deadline = time.monotonic() + _REPLAN_WAIT_S
        while deprepair.is_running(attempt.plan_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        try:
            with sess.lock:
                sess.stale = {"reason": "dependencies_prepared"}
            self.check(
                project_id=sess.project_id, project_root=sess.project_root, target=sess.target
            )
            with sess.lock:
                offer = sess.plan.dependency_preparation or {}
                sess.dependency_delta = {
                    "origin": "runtime_missing" if attempt.module else "joint",
                    "previous_impact_digest": attempt.impact_digest,
                    "current_impact_digest": str(offer.get("impact_digest") or ""),
                    **deprepair.impact_delta(attempt.impact, offer.get("impact")),
                    # 前一次尝试发生过的事实不改写；补齐之后要新的一次尝试（不是"从异常点继续"）
                    "rerun_required": bool(attempt.module),
                }
        except Exception:  # noqa: BLE001 — 重新检查失败时保持失效标记：用户看到 recheck，而不是静默通过
            LOG.exception("依赖准备后的重新检查失败")
        finally:
            with sess.lock:
                attempt.absorbed = True
            self._notify(sess)

    def _observe_missing(self, sess: Session, plan, result) -> dict | None:
        """一次执行以 `missing_dependency` 收场：算出差异计划（要装什么 / 装到哪 / 影响摘要）并记在那次尝试上。
        不起解释器（`deprepair.offer` 只读）；能不能在会话里授权看受管目标是否可用、包名是否可信解析。回 None =
        不是这类终局。"""
        err = result.error or {}
        if result.status != preparation.STATUS_ERROR or err.get("code") != _RUNTIME_MISSING_CODE:
            return None
        module = str(err.get("module") or "")
        info: dict = {"module": module, "installable": False}
        if not module:
            return info
        if err.get("install_route") == pool.INSTALL_ROUTE_STDLIB:
            # 标准库缺了：不查可信解析（不许把 tkinter 之类映射成某个同名 PyPI 包去装），只剩换环境
            info["code"] = "stdlib_module_missing"
            info["route"] = pool.INSTALL_ROUTE_STDLIB
            return info
        try:
            offer = deprepair.offer(sess.project_root, plan.script, module)
            managed = next(
                (
                    t
                    for t in offer.get("targets") or ()
                    if t.get("kind") == deprepair.TARGET_MANAGED and t.get("available") is not False
                ),
                None,
            )
            if offer.get("code") or managed is None or not offer.get("requirement"):
                info["code"] = str(offer.get("code") or "dependency_unresolved")
                info["route"] = pool.INSTALL_ROUTE_UNRESOLVABLE
                return info
            preview = deprepair.preview_impact(
                sess.project_root, plan.script, module, target_kind=deprepair.TARGET_MANAGED
            )
        except Exception as exc:  # noqa: BLE001 — 预览失败 = 不能在会话里授权，如实带码
            info["code"] = str(getattr(exc, "code", "") or "dependency_unresolved")
            return info
        info.update(
            installable=True,
            requirement=dict(offer["requirement"]),
            rounds_remaining=int(offer.get("rounds_remaining") or 0),
            impact=preview["impact"],
            impact_digest=preview["impact_digest"],
        )
        return info

    def _claim_run(self, sess: Session, action: _Action, *, launch, finalize) -> bool:
        """会话锁内：核对 → 失效检查 → 认领 → 提交给 provider。四步之间没有无保护窗口。"""
        if self._has_active_attempt(sess):
            raise SessionError(ERROR_NOT_RUNNABLE, "这个会话已经有一次尝试在进行", 409)
        if not self._runnable(sess):
            raise SessionError(
                ERROR_NOT_RUNNABLE, "现在还不能运行：检查项里有未满足或未知的条件", 409
            )
        stale = preparation.stale_reason(sess.plan)
        if stale is not None:
            # 检查那一刻的世界变了：一行用户代码都不跑；会话标失效，等 recheck
            sess.stale = {"reason": stale[0]}
            sess.actions = {k: v for k, v in sess.actions.items() if v.attempt_id}
            raise SessionError(
                preparation.ERROR_PLAN_STALE,
                "检查之后环境 / 授权 / 数据变了，请重新检查",
                409,
                {"reason": stale[0], "executed": False},
            )
        plan = dataclasses.replace(
            sess.plan, plan_id=f"prep-{uuid.uuid4().hex}", created_at=self._clock()
        )
        sess.dependency_delta = None  # 新的一次尝试：上一次依赖准备留下的差额已经被这次执行消费
        attempt = _Attempt(
            attempt_id=plan.plan_id,
            action_id=action.id,
            config_revision=sess.config_revision,
            created_at=self._clock(),
        )

        def _done(done_plan, done_result) -> None:
            fin = None
            if (
                finalize is not None
                and done_plan.target == TARGET_SCRIPT
                and done_result.status == preparation.STATUS_READY
            ):
                try:
                    fin = finalize(done_plan, done_result)
                except Exception:  # noqa: BLE001 — 线程里不许静默死掉，如实记
                    LOG.exception("登记捕获结果失败 %s", done_plan.script)
                    fin = {"registered": False, "code": "registration_failed"}
            missing = None
            try:
                missing = self._observe_missing(sess, done_plan, done_result)
            except Exception:  # noqa: BLE001 — 差异计划算不出来不能影响这次尝试的终局
                LOG.exception("缺包差异计划失败 %s", done_plan.script)
            with sess.lock:
                attempt.finalize = fin
                attempt.missing = missing
                attempt.finalized = True
            self._notify(sess)

        # A new action after a settled attempt is an explicit rerun. The provider
        # retires the old build only after its cancellation and stale-plan checks.
        self._prep.register(plan, force_rebuild=bool(sess.attempts))
        sess.attempts.append(attempt)
        action.attempt_id = plan.plan_id
        entry = self._prep.get(plan.plan_id, sess.project_id)
        if entry is not None and entry[1].status == preparation.STATUS_PENDING:
            try:
                launch(plan, _done)
            except Exception:
                # 没交出去：撤回认领，动作仍可再用
                sess.attempts.remove(attempt)
                action.attempt_id = None
                raise
        else:
            # register 当场落了终局（needs_input / 静态）：没有执行线程，登记也无从谈起
            attempt.finalized = True
        return True

    # ---- 报告 ----
    def report(
        self, sess: Session, *, awaiting_input: bool = False, runtime_input: dict | None = None
    ) -> dict:
        """会话的投影：目标 / 修订 / 观察序号 / phase / outcome / checks / requirements / actions / provider
        引用。读它会补齐当前该有的动作，并在可观察状态变了时推进 `observation_seq`。"""
        with sess.lock:
            sess.touched_at = self._clock()
            found = self._current_attempt(sess)
            checks = checks_of(sess.plan)
            # 只有**当前修订**的尝试决定 phase：重新检查之后配置变了，上一修订的 `completed` 不能冒充这份新配置的结果
            # （它仍在 provider 引用与历史里）
            current_rev = (
                bool(sess.attempts) and sess.attempts[-1].config_revision == sess.config_revision
            )
            attempt_fact, attempt_gone = (
                self._attempt_fact(sess, found) if current_rev else (None, False)
            )
            dep_fact = self._dependency_fact(sess)
            if attempt_fact is not None and sess.attempts:
                attempt_fact["missing_installable"] = bool(
                    (sess.attempts[-1].missing or {}).get("installable")
                )
            facts = {
                "target_kind": sess.plan.target,
                "has_script": sess.plan.script is not None,
                "checks": checks,
                "stale": sess.stale,
                "attempt": attempt_fact,
                "awaiting_input": bool(awaiting_input) and attempt_fact is not None,
                "dependency": dep_fact,
            }
            derived = derive(facts)
            if attempt_gone:
                # 尝试的记录已被 provider 清掉（内存里的 TTL）：如实说不知道，不猜它成没成
                derived = _result(PHASE_ACTION_REQUIRED, OUTCOME_UNKNOWN, code="attempt_expired")
            missing = self._pending_missing(sess) if attempt_fact is not None else None
            self._sync_actions(
                sess,
                derived,
                checks,
                attempt_active=self._is_running(attempt_fact),
                dependency_busy=dep_fact is not None and dep_fact["running"],
                missing=missing,
            )
            dep_progress = self._dependency_progress(sess)
            signature = (
                derived["phase"],
                derived["outcome"].get("kind"),
                derived["outcome"].get("code"),
                (attempt_fact or {}).get("status"),
                (attempt_fact or {}).get("finalized"),
                bool(facts["awaiting_input"]),
                found[1].trace.current_phase if found else None,
                (dep_progress or {}).get("state"),
                (dep_progress or {}).get("stage"),
            )
            if signature != sess.signature:
                sess.signature = signature
                sess.observation_seq += 1
            plan, result = found if found else (None, None)
            return {
                "session_version": SESSION_VERSION,
                "session_id": sess.session_id,
                "project_id": sess.project_id,
                "target": sess.public_target(),
                "config_revision": sess.config_revision,
                "observation_seq": sess.observation_seq,
                "phase": derived["phase"],
                "outcome": derived["outcome"],
                "facts": {
                    "execution_finished": derived["execution_finished"],
                    "figure_captured": derived["figure_captured"],
                },
                "checks": checks,
                "requirements": requirements_of(
                    sess.plan,
                    checks,
                    runtime_missing=self._public_missing(missing or self._unresolved_missing(sess)),
                    missing_input=(
                        result.missing_input
                        if result is not None
                        and attempt_fact is not None
                        and result.status == preparation.STATUS_ERROR
                        else None
                    ),
                ),
                "actions": [
                    self._action_payload(a) for a in sess.actions.values() if self._open(a)
                ],
                "provider": {
                    "plan_id": sess.plan.plan_id,
                    "attempt_id": plan.plan_id if plan else None,
                    "attempts": len(sess.attempts),
                    # T06：依赖作业的 provider 引用（进度仍读 `GET /api/engine/dependency/state?plan_id=`）
                    "dependency": dep_progress,
                },
                # 环境（ADR 0114 §六）：只是一个可展示的事实——用的是哪一类、谁定的、这次检查有没有自动换；不要求用户动作
                "environment": dict((sess.plan.environment or {}).get("adoption") or {}) or None,
                # 上一次依赖准备之后按新环境重新算出的差额；None = 没有
                "dependency_delta": dict(sess.dependency_delta) if sess.dependency_delta else None,
                # 正在等的那一问（T08）：`inputbroker.Pending.public()`——id / 序号 / 读取方式 / 要不要掩码，
                # 没有提示与答案。回答走既有 `/api/script_input/answer`，与原对话框是同一个请求
                "runtime_input": (
                    dict(runtime_input)
                    if runtime_input is not None
                    and derived["phase"] == PHASE_AWAITING_RUNTIME_INPUT
                    else None
                ),
                # T09：这次尝试真正捕获到的图（与 `/api/registry/probe` 响应里同一份公开描述符：项目相对路径、
                # 运行配置只是不透明引用）。「进入编辑」直接用它——不按图名再找一遍、不为换界面再跑一次脚本。
                # 只在这次尝试成功（捕获到且登记好）时给；跑完但没有图 / 失败 / 登记不上时是空表
                "captured": (
                    [
                        dict(d)
                        for d in ((result.captured or {}).get("descriptors") or ())
                        if isinstance(d, dict)
                    ]
                    if result is not None and derived["outcome"]["kind"] == OUTCOME_SUCCEEDED
                    else []
                ),
                # T09b：这次（无参数）运行把哪些此前登记在这个脚本名下的图名替换掉了（T03 已知缺口：注册表按脚本整条替换）。
                # 只是图名（与 `captured[].stem` 同一口径，项目相对的公开名字），不含参数；界面据此给「用原参数再运行」的提示
                "unlinked_stems": (
                    list((attempt_fact.get("finalize") or {}).get("unlinked_stems") or [])
                    if attempt_fact is not None and derived["outcome"]["kind"] == OUTCOME_SUCCEEDED
                    else []
                ),
                "plan": sess.plan.to_payload(),
                "result": result.to_payload() if result else None,
            }

    def get(self, session_id: str, project_id: str) -> Session | None:
        with self._lock:
            sess = self._by_id.get(session_id)
        return sess if sess is not None and sess.project_id == project_id else None

    # ---- 内部 ----
    def session(self, session_id: str, project_id: str) -> Session:
        sess = self.get(session_id, project_id)
        if sess is None:
            raise SessionError(
                ERROR_NOT_FOUND,
                f"没有这个准备会话（或它属于别的项目，或应用重启后已经丢失）: {session_id}",
                404,
                {"id": session_id, "reason": "unknown_or_restarted"},
            )
        return sess

    def _current_attempt(self, sess: Session):
        if not sess.attempts:
            return None
        return self._prep.get(sess.attempts[-1].attempt_id, sess.project_id)

    def _dependency_fact(self, sess: Session) -> dict | None:
        """给 `derive` 的依赖准备事实：只认比最近一次执行尝试更晚的那次依赖作业；装好并已重新检查的不再出现
        （新环境的检查项自己说话），失败 / 取消的只在同一修订里保留。"""
        if not sess.dep_attempts:
            return None
        dep = sess.dep_attempts[-1]
        if sess.attempts and dep.created_at < sess.attempts[-1].created_at:
            return None
        fin = dep.finished
        if fin is None:
            return {"running": True, "state": "running", "code": "", "joined": dep.joined}
        if fin["state"] == deprepair.STATE_DONE:
            if dep.absorbed:
                return None
            return {
                "running": True,
                "state": fin["state"],
                "code": "",
                "joined": dep.joined,
                "reason": "rechecking",
            }
        if dep.config_revision != sess.config_revision:
            return None
        return {
            "running": False,
            "state": fin["state"],
            "code": fin["code"],
            "joined": dep.joined,
        }

    @staticmethod
    def _dependency_progress(sess: Session) -> dict | None:
        """当前依赖作业的 provider 引用 + 此刻的进度快照里的闭集字段（不含日志与路径）。"""
        if not sess.dep_attempts:
            return None
        dep = sess.dep_attempts[-1]
        rec = deprepair.progress(dep.plan_id)
        return {
            "plan_id": dep.plan_id,
            "joined": dep.joined,
            "origin": "runtime_missing" if dep.module else "joint",
            "state": (dep.finished or {}).get("state") or rec.get("state") or "idle",
            "stage": rec.get("stage"),
            "code": (dep.finished or {}).get("code") or rec.get("code") or "",
            "committed": bool(rec.get("committed")),
            "impact_digest": dep.impact_digest,
        }

    def _missing_info(self, sess: Session) -> dict | None:
        """当前修订的最近一次尝试以缺包收场时的差异计划信息（不论能不能在会话里授权）。"""
        if not sess.attempts:
            return None
        last = sess.attempts[-1]
        if last.config_revision != sess.config_revision or not last.finalized:
            return None
        return last.missing

    def _unresolved_missing(self, sess: Session) -> dict | None:
        info = self._missing_info(sess)
        return info if info is not None and not info.get("installable") else None

    def _pending_missing(self, sess: Session) -> dict | None:
        """当前修订的最近一次执行尝试以缺包收场、且能在会话里授权补齐时的差异计划。"""
        if not sess.attempts:
            return None
        last = sess.attempts[-1]
        if last.config_revision != sess.config_revision or not last.finalized:
            return None
        if not last.missing or not last.missing.get("installable"):
            return None
        # 补齐之后（依赖作业成功并重新检查）修订会变；同一修订里已经有更晚的依赖作业在跑 / 刚装好也不再提议
        if sess.dep_attempts and sess.dep_attempts[-1].created_at >= last.created_at:
            dep = sess.dep_attempts[-1]
            if dep.finished is None or dep.finished["state"] == deprepair.STATE_DONE:
                return None
        return last.missing

    @staticmethod
    def _public_missing(missing: dict | None) -> dict | None:
        if missing is None:
            return None
        if not missing.get("installable"):
            # unknown：认不出该装哪个包——绝不拿 import 名去装；给用户可执行的出路（指定包名 / 换环境，走既有端点）
            return {
                "module": missing["module"],
                "installable": False,
                # 装不了的两种去向（前端按它选一句话，不按 code 文案判断）：unresolvable = 映射不到 PyPI；
                # stdlib_missing = 标准库缺了。预览失败等拿不准的情形回落 unresolvable：同样只剩换环境这条路
                "route": missing.get("route") or pool.INSTALL_ROUTE_UNRESOLVABLE,
                "code": missing.get("code", ""),
                "options": ["specify_package", "choose_environment"],
                "rerun_required": True,
            }
        return {
            "module": missing["module"],
            "installable": True,
            "requirement": missing.get("requirement"),
            "rounds_remaining": missing.get("rounds_remaining"),
            "impact": missing.get("impact"),
            "impact_digest": missing.get("impact_digest"),
            # 这次执行已经发生；补齐之后是新的一次尝试
            "rerun_required": True,
        }

    def _attempt_fact(self, sess: Session, found) -> tuple[dict | None, bool]:
        """最近一次尝试的事实（给 `derive`）。回 `(fact | None, 记录已过期)`。"""
        if not sess.attempts:
            return None, False
        attempt = sess.attempts[-1]
        if found is None:
            return None, True
        _plan, result = found
        required = result.required_input or {}
        captured = result.captured or {}
        return (
            {
                "status": result.status,
                "error": dict(result.error) if result.error else None,
                "required_input_code": str(required.get("code") or ""),
                "finalized": attempt.finalized,
                "finalize": attempt.finalize,
                "captured_count": len(captured.get("stems") or []),
                "cancel_requested": result.cancel_requested_at is not None,
            },
            False,
        )

    @staticmethod
    def _is_running(attempt_fact: dict | None) -> bool:
        return attempt_fact is not None and attempt_running(attempt_fact)

    @staticmethod
    def _dep_running(sess: Session) -> _DepAttempt | None:
        """正在进行的依赖作业（还没有终局）。重新检查（装好之后）不算：那一步要能替换计划。"""
        if sess.dep_attempts and sess.dep_attempts[-1].finished is None:
            return sess.dep_attempts[-1]
        return None

    def _has_active_attempt(self, sess: Session) -> bool:
        if self._dep_running(sess) is not None:
            return True
        found = self._current_attempt(sess)
        if found is None:
            return False
        result = found[1]
        if result.status not in preparation.TERMINAL:
            return True
        if result.status == preparation.STATUS_CANCELLED:
            # 取消已经落地（T09：共享会话的等待者被放手时，执行线程可能还卡在别人的会话上）——不等它收尾，
            # 用户可以马上重新检查 / 再跑一次
            return False
        return not sess.attempts[-1].finalized

    def _active(self, sess: Session) -> bool:
        return self._has_active_attempt(sess)

    def _runnable(self, sess: Session) -> bool:
        if sess.stale or sess.plan.script is None:
            return False
        return all(c["status"] == CHECK_OK for c in checks_of(sess.plan))

    def _sync_actions(
        self,
        sess: Session,
        derived: dict,
        checks: list[dict],
        *,
        attempt_active: bool,
        dependency_busy: bool = False,
        missing: dict | None = None,
    ) -> None:
        """补齐 / 撤掉当前该有的**未认领**动作：`run`（可运行且没有尝试在进行）、`cancel`（有自己拥有的尝试 / 依赖
        作业在进行）、`recheck`（没有尝试在进行）、`prepare_dependencies`（有可授权的依赖影响且没有尝试在进行）。
        已有的未认领动作保持原 id，所以反复读报告不会让用户手里的 id 失效。

        运行时缺包（`missing`）还没补齐时不提供 `run`：同一份配置再跑一遍只会撞同一个缺包，补齐之后才有新的
        一次尝试。"""
        runnable = (
            not attempt_active
            and not dependency_busy
            and not sess.stale
            and sess.plan.script is not None
            and all(c["status"] == CHECK_OK for c in checks)
            and missing is None
        )
        dep = self._dep_running(sess)
        current: str | None = None
        if dep is not None:
            # 认领了别人先起的同一份作业不拥有它：不提供取消（只退役自己拥有的工作）
            current = None if dep.joined else dep.plan_id
        elif attempt_active and sess.attempts:
            current = sess.attempts[-1].attempt_id
        prep = (
            None
            if (attempt_active or dependency_busy or sess.stale)
            else self._prepare_offer(sess, checks, missing)
        )
        want: dict[str, bool] = {
            ACTION_RUN: runnable,
            ACTION_CANCEL: current is not None,
            ACTION_RECHECK: not attempt_active and not dependency_busy,
            ACTION_PREPARE: prep is not None,
        }
        for kind, wanted in want.items():
            open_ones = [a for a in sess.actions.values() if a.kind == kind and self._open(a)]
            if kind == ACTION_CANCEL:
                # cancel 绑当前尝试：尝试换了，旧的取消动作作废
                for a in open_ones:
                    if a.attempt_id != current:
                        sess.actions.pop(a.id, None)
                open_ones = [a for a in open_ones if a.attempt_id == current]
            if kind == ACTION_PREPARE and prep is not None:
                # 影响摘要变了：旧的未认领动作不再是用户看到的那一份，作废重发（认领时本也会被拒）
                for a in open_ones:
                    if a.impact_digest != prep["impact_digest"]:
                        sess.actions.pop(a.id, None)
                open_ones = [a for a in open_ones if a.impact_digest == prep["impact_digest"]]
            if not wanted:
                for a in open_ones:
                    sess.actions.pop(a.id, None)
            elif not open_ones:
                action = _Action(
                    id=f"act-{uuid.uuid4().hex}",
                    kind=kind,
                    config_revision=sess.config_revision,
                    attempt_id=current if kind == ACTION_CANCEL else None,
                    impact=self._prepare_impact(prep)
                    if kind == ACTION_PREPARE and prep is not None
                    else self._impact(sess, kind),
                )
                if kind == ACTION_PREPARE and prep is not None:
                    action.impact_digest = prep["impact_digest"]
                    action.impact_core = dict(prep["impact"])
                    action.module = prep["module"]
                    action.target_kind = prep["target_kind"]
                    action.scope_policy = prep.get("scope_policy", "")
                sess.actions[action.id] = action
        consumed = [
            a
            for a in sess.actions.values()
            if a.kind in (ACTION_RUN, ACTION_PREPARE) and a.attempt_id
        ]
        for stale_action in consumed[:-_MAX_CONSUMED_ACTIONS]:
            sess.actions.pop(stale_action.id, None)

    @staticmethod
    def _prepare_offer(sess: Session, checks: list[dict], missing: dict | None) -> dict | None:
        """此刻有没有可授权的依赖影响：静态的（检查项 `dependencies` 要授权、环境没有更前面的事要答）或运行时
        缺包的差异计划。回 `{impact, impact_digest, module, target_kind}`；没有回 None。"""
        if missing is not None:
            if not missing.get("impact_digest"):
                return None
            return {
                "impact": missing["impact"],
                "impact_digest": missing["impact_digest"],
                "module": missing["module"],
                "target_kind": deprepair.TARGET_MANAGED,
                "scope_policy": "",
            }
        by_id = {c["id"]: c for c in checks}
        deps = by_id.get("dependencies")
        if deps is None:
            return None
        # 环境 / 工作目录 / 数据有更前面的问题（要答、被卡、判不出）：目标环境还没定，装什么装到哪都不是定数
        if any(c["status"] != CHECK_OK for c in checks if c["id"] != "dependencies"):
            return None
        offer = sess.plan.dependency_preparation or {}
        if deps["status"] == CHECK_BLOCKED and deps.get("code") == _SCOPE_CONFLICT_CODE:
            # 作用域互斥：唯一能在会话里授权的出路是"换成本作用域"，它有自己更大的影响摘要
            switch = offer.get("scope_switch") or {}
            if not switch.get("impact") or not switch.get("impact_digest"):
                return None
            return {
                "impact": switch["impact"],
                "impact_digest": str(switch["impact_digest"]),
                "module": "",
                "target_kind": deprepair.TARGET_MANAGED,
                "scope_policy": deprepair.SCOPE_POLICY_SWITCH,
            }
        if deps["status"] != CHECK_NEEDS_ACTION:
            return None
        impact, digest = offer.get("impact"), str(offer.get("impact_digest") or "")
        if not impact or not digest:
            return None
        return {
            "impact": impact,
            "impact_digest": digest,
            "module": "",
            "target_kind": str(offer.get("target_kind") or ""),
            "scope_policy": "",
        }

    @staticmethod
    def _prepare_impact(prep: dict) -> dict:
        """动作上的影响：授权摘要原样 + 动作层的标志（不跑用户脚本、会装包、会改环境），再带上摘要本身。"""
        impact = prep["impact"]
        return {
            **impact,
            "impact_digest": prep["impact_digest"],
            "executes_user_script": False,
            "installs_packages": True,
            "changes_environment": True,
            "writes_to_project": [],
        }

    @staticmethod
    def _open(action: _Action) -> bool:
        """对外展示的动作：run / recheck 是「还没认领」的；cancel 一直开着直到被换掉。"""
        return action.kind == ACTION_CANCEL or action.attempt_id is None

    @staticmethod
    def _impact(sess: Session, kind: str) -> dict:
        if kind == ACTION_RUN:
            return {
                "executes_user_script": True,
                "installs_packages": False,
                "changes_environment": False,
                # T03：只说"带了几个参数"，不说是什么（授权绑定影响摘要，参数值不进公开投影）
                **(
                    {"script_arguments": len(sess.plan.run.argv)}
                    if sess.plan.run is not None
                    else {}
                ),
                # 脚本目标成功后要把捕获到的图名登记进项目的注册表文件（与 /api/registry/probe 同一件事）
                "writes_to_project": (
                    [registry.REGISTRY_NAME] if sess.plan.target == TARGET_SCRIPT else []
                ),
                **SessionService._script_writes(sess.plan),
            }
        return {
            "executes_user_script": False,
            "installs_packages": False,
            "changes_environment": False,
            "writes_to_project": [],
        }

    @staticmethod
    def _script_writes(plan: preparation.PreparationPlan) -> dict:
        """T07（P03）：脚本自己声明了输出文件参数（`FileType('w')` 一类）时，说清这些相对路径会落在哪个工作目录档。
        只给个数与档位，不给参数名 / 路径；Tavotto 从不替用户加 overwrite / force 一类开关。"""
        schema = arguments_schema(plan)
        outputs = scriptargs.summary(schema)["output_files"] if schema is not None else 0
        if not outputs:
            return {}
        mode = str((plan.workdir_decision or {}).get("mode") or "")
        return {"script_writes": {"declared_output_arguments": outputs, "cwd_mode": mode}}

    @staticmethod
    def _action_payload(action: _Action) -> dict:
        return {
            "id": action.id,
            "kind": action.kind,
            "config_revision": action.config_revision,
            "impact": dict(action.impact),
        }

    def _notify(self, sess: Session) -> None:
        notifier = self.notifier
        if notifier is None:
            return
        with sess.lock:
            # 只是「该重新读报告了」的提示：不带 phase / 序号——那些以 GET 的报告为准（序号在读报告时推进）
            payload = {
                "session_id": sess.session_id,
                "project_id": sess.project_id,
                "target": sess.public_target(),
                "config_revision": sess.config_revision,
            }
        try:
            notifier(payload)
        except Exception:  # noqa: BLE001 — 通知失败不能影响会话本身
            LOG.exception("准备会话通知失败")

    # ---- 回收 ----
    def _make_room(self) -> None:
        """登记表（持 `_lock`）里为一个新会话腾位置：先回收闲置的终局会话；还满就拒绝，**绝不**驱逐
        有活跃尝试的会话（普通 TTL 不能误杀在跑的 worker / input / 登记）。"""
        if len(self._by_id) < self._max:
            return
        idle = sorted(
            (s for s in self._by_id.values() if not self._active(s)), key=lambda s: s.touched_at
        )
        if not idle:
            raise SessionError(
                ERROR_SESSIONS_FULL, "同时进行的准备会话太多，请先完成或取消其中一些", 429
            )
        self._drop(idle[0])

    def _drop(self, sess: Session) -> None:
        self._by_id.pop(sess.session_id, None)
        self._by_key.pop((sess.project_id, target_key(sess.target)), None)

    def _sweep(self) -> None:
        cutoff = self._clock() - self._ttl
        with self._lock:
            for sess in list(self._by_id.values()):
                if sess.touched_at < cutoff and not self._active(sess):
                    self._drop(sess)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._by_id.clear()
            self._by_key.clear()


#: 进程内唯一登记表（app.py 用它）。
SESSIONS = SessionService()
