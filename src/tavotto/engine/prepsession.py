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
* **后端生成的动作**（`run` / `cancel` / `recheck`）：不透明 id，绑定会话、`config_revision`、检查那一刻的
  计划与影响摘要；确认前一行用户代码不跑；
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
from typing import Callable

from . import preparation, registry
from .preparation import TARGET_SCRIPT

LOG = logging.getLogger("tavotto.prepsession")

SESSION_VERSION = 1

# ---------------------------------------------------------------- 词汇（闭集）

#: phase 词汇沿用附件：这一阶段产生 scanning 之外的全部（`scanning` 留给 T02 的有界扫描，
#: `preparing_environment` 留给 T06 的联合安装；词汇先收进来，保证前端只面对一份闭集）。
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
)

CHECK_OK = "ok"
CHECK_UNKNOWN = "unknown"
CHECK_NEEDS_ACTION = "needs_action"
CHECK_BLOCKED = "blocked"
CHECK_STATUSES = (CHECK_OK, CHECK_UNKNOWN, CHECK_NEEDS_ACTION, CHECK_BLOCKED)

ACTION_RUN = "run"
ACTION_CANCEL = "cancel"
ACTION_RECHECK = "recheck"
ACTION_KINDS = (ACTION_RUN, ACTION_CANCEL, ACTION_RECHECK)

# 稳定错误码（协议契约：code 不许改，文案随便改）
ERROR_NOT_FOUND = "preparation_session_not_found"
ERROR_ACTION_UNKNOWN = "preparation_action_unknown"
ERROR_REVISION_CHANGED = "preparation_config_revision_changed"
ERROR_NOT_RUNNABLE = "preparation_not_runnable"
ERROR_SESSIONS_FULL = "preparation_sessions_full"
ERROR_BAD_REQUEST = "bad_request"

#: 「跑完了但没有图」的两个 code（`pool.NO_FIGURES_CODE` / `_SILENT_CODE` 的值；这里不 import pool 的常量
#: 名，闭集字面量由 `tests/test_preparation_session.py` 钉着与 pool 一致）。
_NO_FIGURE_CODES = frozenset({"no_figures_captured", "no_figures_captured_silent"})
#: 旧协议终局 `needs_input` 里 `required_input.code` → phase。
_WORKDIR_CODE = "workdir_confirmation_required"
_DEPENDENCY_CODE = "dependency_preparation_required"

#: 数量 / 生命周期预算。活跃的（有没跑完 / 没登记完的尝试）永远不被这里回收。
MAX_SESSIONS = 64
IDLE_TTL_S = 30 * 60
#: 每个会话保留多少个已认领的动作 id（重复点击要找得到当初认领的尝试）。
_MAX_CONSUMED_ACTIONS = 32


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
    if env_error:
        checks.append(_check("environment", CHECK_BLOCKED, str(env_error.get("code") or "")))
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
    if required_code == _DEPENDENCY_CODE:
        checks.append(_check("dependencies", CHECK_NEEDS_ACTION, _DEPENDENCY_CODE))
    elif offer is None:
        # 工作目录要先答 / 环境走不通时依赖没有被评估——如实 unknown，不虚构已满足
        checks.append(_check("dependencies", CHECK_UNKNOWN))
    elif ((offer.get("plan") or {}).get("status")) == "blocked":
        checks.append(_check("dependencies", CHECK_BLOCKED, "dependency_blocked"))
    else:
        checks.append(_check("dependencies", CHECK_OK))
    checks.append(
        _check("data", CHECK_OK) if plan.binding is not None else _check("data", CHECK_UNKNOWN)
    )
    return checks


def requirements_of(plan: preparation.PreparationPlan, checks: list[dict]) -> list[dict]:
    """要用户先答的事（`needs_action` 的检查项）。载荷原样是既有的 `required_input`——回答走既有端点，
    答完 `recheck`；会话不复制那两条回答协议。"""
    out = []
    for c in checks:
        if c["status"] != CHECK_NEEDS_ACTION:
            continue
        out.append(
            {
                "id": c["id"],
                "kind": "workdir_choice" if c["id"] == "workdir" else "dependency_authorization",
                "code": c.get("code", ""),
                "payload": dict(plan.required_input) if plan.required_input else None,
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

    纯函数、不读时钟、不读 provider：黄金向量 `tests/golden/preparation_session_vectors.json`
    逐条钉它的输出。provider 的状态保留原权威——这里只**投影**，不另造一份可运行判据。
    """
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
    """这次尝试是否还在进行：provider 没到终局，或到了终局但执行之后的登记还没做完。"""
    if attempt["status"] in (preparation.STATUS_PENDING, preparation.STATUS_RUNNING):
        return True
    return attempt["status"] == preparation.STATUS_READY and not attempt.get("finalized")


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


@dataclasses.dataclass
class _Attempt:
    attempt_id: str  # = 这次执行的 PreparationPlan.plan_id
    action_id: str
    config_revision: int
    created_at: float
    #: 执行线程是否已经把「执行之后的登记」做完（脚本目标要登记 / 物化才算图可被编辑请求找到）
    finalized: bool = False
    finalize: dict | None = None


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
        }


def target_key(target: dict) -> str:
    if target["kind"] == TARGET_SCRIPT:
        return f"script:{target['script']}"
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
        "cwd_mode": decision.get("mode"),
        "cwd_decided": decision.get("decided"),
        "grant": plan.grant,
        "binding": (plan.binding or {}).get("revision"),
        "required": (plan.required_input or {}).get("code"),
        "env_error": ((plan.environment or {}).get("error") or {}).get("code"),
        # JointPlan owns the installation identity and source-input fingerprint.
        # Declarations remain relevant when the dependency gate is not evaluated.
        "deps": {
            key: dependencies.get(key)
            for key in ("status", "identity", "inputs_digest", "selection")
        },
        "dependency_intents": plan.dependency_intents,
        "dependency_conflicts": plan.dependency_conflicts,
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
                        if v.kind == ACTION_RUN and v.attempt_id
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
    ) -> tuple[Session, bool, str]:
        """认领一个动作。回 `(会话, claimed, 动作 kind)`：`claimed=False` = 这个动作早已被认领（重复点击 / 另一个
        标签页），返回的是当初那次尝试，**没有任何新副作用**。

        `launch(plan, on_done)`：app 注入的「把这份计划交给执行线程」——绑项目、挂 `pool.build_owned`
        runner、把 `on_done` 传给 `PreparationService.start`。`finalize(plan, result)`：脚本目标执行成功之后
        的登记 / 物化（在项目绑定里、执行线程上调；回 `{"registered": bool, "code"?}`）。
        """
        sess = self.session(session_id, project_id)
        recheck = False
        claimed = False
        kind = ""
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
            elif action.kind == ACTION_CANCEL:
                # 只退役**这次会话拥有的**工作；共享 worker / 别人起的会话的所有权判断在
                # `PreparationService.cancel` 与 `pool.build_owned` 的 created 里，这里不重判
                self._prep.cancel(action.attempt_id or "", project_id)
                claimed = True
            else:  # ACTION_RECHECK：规划有副作用面（解释器体检），不在会话锁里做
                recheck = True
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
            with sess.lock:
                attempt.finalize = fin
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
    def report(self, sess: Session, *, awaiting_input: bool = False) -> dict:
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
            facts = {
                "target_kind": sess.plan.target,
                "has_script": sess.plan.script is not None,
                "checks": checks,
                "stale": sess.stale,
                "attempt": attempt_fact,
                "awaiting_input": bool(awaiting_input) and attempt_fact is not None,
            }
            derived = derive(facts)
            if attempt_gone:
                # 尝试的记录已被 provider 清掉（内存里的 TTL）：如实说不知道，不猜它成没成
                derived = _result(PHASE_ACTION_REQUIRED, OUTCOME_UNKNOWN, code="attempt_expired")
            self._sync_actions(sess, derived, checks, attempt_active=self._is_running(attempt_fact))
            signature = (
                derived["phase"],
                derived["outcome"].get("kind"),
                derived["outcome"].get("code"),
                (attempt_fact or {}).get("status"),
                (attempt_fact or {}).get("finalized"),
                bool(facts["awaiting_input"]),
                found[1].trace.current_phase if found else None,
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
                "requirements": requirements_of(sess.plan, checks),
                "actions": [
                    self._action_payload(a) for a in sess.actions.values() if self._open(a)
                ],
                "provider": {
                    "plan_id": sess.plan.plan_id,
                    "attempt_id": plan.plan_id if plan else None,
                    "attempts": len(sess.attempts),
                },
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

    def _has_active_attempt(self, sess: Session) -> bool:
        found = self._current_attempt(sess)
        if found is None:
            return False
        result = found[1]
        if result.status not in preparation.TERMINAL:
            return True
        return not sess.attempts[-1].finalized

    def _active(self, sess: Session) -> bool:
        return self._has_active_attempt(sess)

    def _runnable(self, sess: Session) -> bool:
        if sess.stale or sess.plan.script is None:
            return False
        return all(c["status"] == CHECK_OK for c in checks_of(sess.plan))

    def _sync_actions(
        self, sess: Session, derived: dict, checks: list[dict], *, attempt_active: bool
    ) -> None:
        """补齐 / 撤掉当前该有的**未认领**动作：`run`（可运行且没有尝试在进行）、`cancel`（有尝试在进行）、
        `recheck`（没有尝试在进行）。已有的未认领动作保持原 id，所以反复读报告不会让用户手里的 id 失效。"""
        runnable = (
            not attempt_active
            and not sess.stale
            and sess.plan.script is not None
            and all(c["status"] == CHECK_OK for c in checks)
        )
        want: dict[str, bool] = {
            ACTION_RUN: runnable,
            ACTION_CANCEL: attempt_active,
            ACTION_RECHECK: not attempt_active,
        }
        current = sess.attempts[-1].attempt_id if sess.attempts else None
        for kind, wanted in want.items():
            open_ones = [a for a in sess.actions.values() if a.kind == kind and self._open(a)]
            if kind == ACTION_CANCEL:
                # cancel 绑当前尝试：尝试换了，旧的取消动作作废
                for a in open_ones:
                    if a.attempt_id != current:
                        sess.actions.pop(a.id, None)
                open_ones = [a for a in open_ones if a.attempt_id == current]
            if not wanted:
                for a in open_ones:
                    sess.actions.pop(a.id, None)
            elif not open_ones:
                action = _Action(
                    id=f"act-{uuid.uuid4().hex}",
                    kind=kind,
                    config_revision=sess.config_revision,
                    attempt_id=current if kind == ACTION_CANCEL else None,
                    impact=self._impact(sess, kind),
                )
                sess.actions[action.id] = action
        consumed = [a for a in sess.actions.values() if a.kind == ACTION_RUN and a.attempt_id]
        for stale_action in consumed[:-_MAX_CONSUMED_ACTIONS]:
            sess.actions.pop(stale_action.id, None)

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
                # 脚本目标成功后要把捕获到的图名登记进项目的注册表文件（与 /api/registry/probe 同一件事）
                "writes_to_project": (
                    [registry.REGISTRY_NAME] if sess.plan.target == TARGET_SCRIPT else []
                ),
            }
        return {
            "executes_user_script": False,
            "installs_packages": False,
            "changes_environment": False,
            "writes_to_project": [],
        }

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
