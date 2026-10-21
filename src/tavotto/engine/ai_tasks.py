"""TAF Task Core（ADR 0138）：任务契约、任务适配器注册表、编排与预算。纯标准库，Flask 父进程 import。

一个任务就是 `ai_history.sessions` 里的一条会话：请求幂等走 `ai_requests` 的原子预留（ADR 0132），重启后的中断识别
走 `ai_history.mark_interrupted_running`，任务事实是 sessions 上追加的几列（`task_type` / `phase` / `outcome` /
`verification` / `envelope` / `budget_used` / `parent_task_id`）。这里**不**另建表、库、状态机或 Agent 注册表：
「谁来执行」仍只认 `ai_agents.AGENT_REGISTRY`，任务适配器注册表回答的是「做什么任务」，两者正交。

验证四态只有一个出处：`rendercore.inspector.VERDICTS`。任务结局（`OUTCOMES`）与验证结论分开记——Agent 正常退出 ≠
任务完成 ≠ 验证通过；`unknown` 永不折算成 `verified`（`derive_outcome`）。

Phase 1 的边界：Task Core **不写用户脚本**。写已有脚本的提交只经 AI 桥的会话（Phase 3 接入，届时同一进程只有
`ai_bridge.SESSIONS` 一张会话表）；`TaskEnvelope` 校验拒绝 Phase 1 执行器声明写入用户文件。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..rendercore.inspector import FAILED, NOT_APPLICABLE, UNKNOWN, VERDICTS, VERIFIED
from . import ai_agents, ai_history

SCHEMA_VERSION = 1

#: 任务结局闭集（与验证四态分开）。`needs_input` 是终态：用户的回答是一个新任务（`parent_task_id`），
#: 不让会话长时间挂着占用脚本
OUTCOMES = ("completed", "partial", "needs_input", "failed", "cancelled", "interrupted")
#: 进行中的阶段（非终态，落 `sessions.phase`；结束时清空）
PHASES = ("planning", "executing", "verifying", "correcting", "ready_to_commit", "committing")
LAYERS = ("L1", "L2", "L3", "L4", "L5")
#: 图验证 / 持久化验证分开汇总；开发验证（ADR 0131）不在任务结果里
VERIFICATION_KINDS = ("figure", "persistence")
#: 检查在提交前还是提交后做：提交前的检查不全为 verified / not_applicable 就不提交
STAGES = ("precommit", "postcommit")
#: 检查的角色：`goal` = 用户要求的变化；`preserve` = 不该变的科学内容；`integrity` = 完整性（L1）
ROLES = ("goal", "preserve", "integrity")
#: 预算的计数项。`corrections` 是 `agent_runs` 里「为修正提交前失败而再跑一次」的那部分（两边都记）
BUDGET_KEYS = ("agent_runs", "renders", "corrections")

#: ↔ `app._CLIENT_REQUEST_ID`：与 `/api/ai/run` 同一个格式（任务就是一次 AI 请求）
_CLIENT_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_TASK_TYPE = re.compile(r"[a-z][a-z0-9_]{1,39}")

#: 执行器 id：已注册的编码 Agent，或 `deterministic`（不调模型的确定性执行器）
DETERMINISTIC = "deterministic"


class ContractError(ValueError):
    """契约不成立（字段缺失 / 多余 / 类型不对 / 版本不认识 / 任务类型未注册）。**在登记任何会话之前**抛出：
    与 `/api/ai/run` 在 spawn 之前的明确拒绝同一类——一定没执行。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class TaskError(Exception):
    """执行器 / 适配器报告的、带稳定错误码的失败。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class BudgetExceeded(TaskError):
    def __init__(self, key: str):
        super().__init__("budget_exhausted", key)
        self.key = key


# --------------------------------------------------------------------------- 契约
def _strict(d: Any, allowed: set[str], required: set[str], where: str) -> dict:
    if not isinstance(d, dict):
        raise ContractError("contract_type", f"{where} 不是对象")
    extra = set(d) - allowed
    if extra:
        raise ContractError("contract_unknown_field", f"{where}: {sorted(extra)}")
    missing = required - set(d)
    if missing:
        raise ContractError("contract_missing_field", f"{where}: {sorted(missing)}")
    return d


def _str_list(v: Any, where: str) -> tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ContractError("contract_type", f"{where} 不是字符串列表")
    return tuple(v)


@dataclass(frozen=True)
class Budget:
    max_agent_runs: int = 1
    max_renders: int = 2
    max_wall_s: float = 900.0
    #: 有界修正（Phase 4）：默认 0 = 不修正，提交前检查不过即失败；适配器的契约显式放开
    max_corrections: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> Budget:
        _strict(
            d, {"max_agent_runs", "max_renders", "max_wall_s", "max_corrections"}, set(), "budget"
        )
        b = cls(**d)
        for name in ("max_agent_runs", "max_renders", "max_corrections"):
            v = getattr(b, name)
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 10:
                raise ContractError("contract_budget", f"{name}={v!r}")
        if not isinstance(b.max_wall_s, (int, float)) or not 0 < b.max_wall_s <= 3600:
            raise ContractError("contract_budget", f"max_wall_s={b.max_wall_s!r}")
        return b

    def limit(self, key: str) -> int:
        return {
            "agent_runs": self.max_agent_runs,
            "renders": self.max_renders,
            "corrections": self.max_corrections,
        }[key]


_REF_KINDS = {
    "input_refs": ({"script", "data_file", "figure"}, {"kind", "path_rel", "sha256", "size"}),
    "target_refs": (
        {"figure", "axes", "artist", "script"},
        {"kind", "stem", "gid", "identity", "path_rel"},
    ),
}


def _refs(v: Any, name: str) -> tuple[dict, ...]:
    kinds, allowed = _REF_KINDS[name]
    if not isinstance(v, list):
        raise ContractError("contract_type", f"{name} 不是列表")
    out = []
    for i, ref in enumerate(v):
        _strict(ref, allowed, {"kind"}, f"{name}[{i}]")
        if ref["kind"] not in kinds:
            raise ContractError("contract_ref_kind", f"{name}[{i}].kind={ref['kind']!r}")
        for k, val in ref.items():
            if k != "size" and not isinstance(val, str):
                raise ContractError("contract_type", f"{name}[{i}].{k}")
        path = ref.get("path_rel")
        if path is not None and (
            path.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", path)
            or ".." in re.split(r"[\\/]", path)
        ):
            # 受控引用只收项目相对路径；碰文件系统前仍由调用方经 `projectenv.contained_path` 落地（这里挡最明显的越界写法）
            raise ContractError("contract_path", f"{name}[{i}].path_rel")
        out.append(dict(ref))
    return tuple(out)


@dataclass(frozen=True)
class TaskEnvelope:
    """一次任务请求被持久化接受时冻结的契约（`docs/implementation/ai-foundation/03-contracts.md` §一）。

    未知字段一律拒绝：防止模型或前端塞进越权参数（例如自己扩大 `output_contract`）。"""

    task_type: str
    project_key: str
    user_intent: str
    executor: str
    client_request_id: str | None = None
    input_refs: tuple[dict, ...] = ()
    target_refs: tuple[dict, ...] = ()
    base_revision: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    allowed_writes: tuple[str, ...] = ()
    new_figures: bool = False
    required_checks: tuple[str, ...] = ()
    send_to_model: tuple[str, ...] = ()
    budget: Budget = Budget()
    parent_task_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    _FIELDS = {
        "schema_version",
        "task_type",
        "project_ref",
        "client_request_id",
        "user_intent",
        "input_refs",
        "target_refs",
        "base_revision",
        "constraints",
        "output_contract",
        "execution_policy",
        "verification_policy",
        "budget",
        "privacy_policy",
        "parent_task_id",
    }

    @classmethod
    def from_dict(cls, d: dict) -> TaskEnvelope:
        _strict(
            d,
            cls._FIELDS,
            {"schema_version", "task_type", "project_ref", "user_intent", "execution_policy"},
            "envelope",
        )
        if d["schema_version"] != SCHEMA_VERSION:
            raise ContractError("contract_version", repr(d["schema_version"]))
        if not isinstance(d["task_type"], str) or not _TASK_TYPE.fullmatch(d["task_type"]):
            raise ContractError("contract_task_type", repr(d["task_type"]))
        project = _strict(d["project_ref"], {"project_key"}, {"project_key"}, "project_ref")[
            "project_key"
        ]
        if not isinstance(project, str) or not project:
            raise ContractError("contract_type", "project_ref.project_key")
        intent = d["user_intent"]
        if not isinstance(intent, str) or not intent.strip():
            raise ContractError("contract_type", "user_intent")
        if len(intent.encode("utf-8")) > ai_history.INSTRUCTION_MAX_BYTES:
            raise ContractError("contract_intent_too_large", str(ai_history.INSTRUCTION_MAX_BYTES))
        rid = d.get("client_request_id")
        if rid is not None and (not isinstance(rid, str) or not _CLIENT_REQUEST_ID.fullmatch(rid)):
            raise ContractError("contract_request_id", "")
        ex = _strict(
            d["execution_policy"], {"executor", "render"}, {"executor"}, "execution_policy"
        )
        if ex.get("render", "safe") != "safe":
            # 验证渲染恒为 safe：native 不在任务契约里（ADR 0138 §四；授权只来自桌面用户的运行动作，ADR 0127）
            raise ContractError("contract_render_policy", repr(ex.get("render")))
        executor = ex["executor"]
        if executor != DETERMINISTIC and ai_agents.get_agent(executor) is None:
            raise ContractError("contract_executor", repr(executor))
        out = _strict(
            d.get("output_contract", {}),
            {"allowed_writes", "new_figures"},
            set(),
            "output_contract",
        )
        new_figures = out.get("new_figures", False)
        if not isinstance(new_figures, bool):
            raise ContractError("contract_type", "output_contract.new_figures")
        ver = _strict(d.get("verification_policy", {}), {"required"}, set(), "verification_policy")
        priv = _strict(d.get("privacy_policy", {}), {"send_to_model"}, set(), "privacy_policy")
        for name in ("base_revision", "constraints"):
            if not isinstance(d.get(name, {}), dict):
                raise ContractError("contract_type", name)
        parent = d.get("parent_task_id")
        if parent is not None and not isinstance(parent, str):
            raise ContractError("contract_type", "parent_task_id")
        return cls(
            task_type=d["task_type"],
            project_key=project,
            user_intent=intent,
            executor=executor,
            client_request_id=rid,
            input_refs=_refs(d.get("input_refs", []), "input_refs"),
            target_refs=_refs(d.get("target_refs", []), "target_refs"),
            base_revision=dict(d.get("base_revision", {})),
            constraints=dict(d.get("constraints", {})),
            allowed_writes=_str_list(
                out.get("allowed_writes", []), "output_contract.allowed_writes"
            ),
            new_figures=new_figures,
            required_checks=_str_list(ver.get("required", []), "verification_policy.required"),
            send_to_model=_str_list(priv.get("send_to_model", []), "privacy_policy.send_to_model"),
            budget=Budget.from_dict(d.get("budget", {})),
            parent_task_id=parent,
        )

    def to_dict(self, *, include_intent: bool = True) -> dict:
        d = {
            "schema_version": self.schema_version,
            "task_type": self.task_type,
            "project_ref": {"project_key": self.project_key},
            "client_request_id": self.client_request_id,
            "input_refs": [dict(r) for r in self.input_refs],
            "target_refs": [dict(r) for r in self.target_refs],
            "base_revision": dict(self.base_revision),
            "constraints": dict(self.constraints),
            "output_contract": {
                "allowed_writes": list(self.allowed_writes),
                "new_figures": self.new_figures,
            },
            "execution_policy": {"executor": self.executor, "render": "safe"},
            "verification_policy": {"required": list(self.required_checks)},
            "budget": {
                "max_agent_runs": self.budget.max_agent_runs,
                "max_renders": self.budget.max_renders,
                "max_wall_s": self.budget.max_wall_s,
                "max_corrections": self.budget.max_corrections,
            },
            "privacy_policy": {"send_to_model": list(self.send_to_model)},
            "parent_task_id": self.parent_task_id,
        }
        if include_intent:
            d["user_intent"] = self.user_intent
        return d


@dataclass(frozen=True)
class CheckResult:
    """一条验证证据（`03-contracts.md` §三）。`verdict` 只取 `VERDICTS`。"""

    id: str
    layer: str
    stage: str
    role: str
    verdict: str
    claim: str = ""
    method: str = ""
    kind: str = "figure"
    reason_code: str | None = None
    observed: dict = field(default_factory=dict)
    not_covered: tuple[str, ...] = ()

    def __post_init__(self):
        for name, allowed in (
            ("layer", LAYERS),
            ("stage", STAGES),
            ("role", ROLES),
            ("verdict", VERDICTS),
            ("kind", VERIFICATION_KINDS),
        ):
            if getattr(self, name) not in allowed:
                raise ValueError(f"CheckResult.{name}={getattr(self, name)!r}")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "layer": self.layer,
            "stage": self.stage,
            "role": self.role,
            "kind": self.kind,
            "verdict": self.verdict,
            "claim": self.claim,
            "method": self.method,
            "reason_code": self.reason_code,
            "observed": dict(self.observed),
            "not_covered": list(self.not_covered),
        }


def aggregate_verdict(verdicts) -> str:
    """一组检查的汇总：最差项为准（failed > unknown > verified）；not_applicable 不参与，全是 / 空集才是 not_applicable。"""
    vs = [v for v in verdicts if v != NOT_APPLICABLE]
    if not vs:
        return NOT_APPLICABLE
    for worst in (FAILED, UNKNOWN):
        if worst in vs:
            return worst
    return VERIFIED


def verification_summary(checks) -> dict:
    return {
        kind: {
            "verdict": aggregate_verdict(c.verdict for c in checks if c.kind == kind),
            "checks": [c.to_dict() for c in checks if c.kind == kind],
        }
        for kind in VERIFICATION_KINDS
    }


def precommit_admits(checks) -> bool:
    """提交前的检查放不放行提交：**每一条**都必须是 verified / not_applicable——unknown 不是通过（验证器自己崩了也是 unknown）。"""
    return all(c.verdict in (VERIFIED, NOT_APPLICABLE) for c in checks if c.stage == "precommit")


def derive_outcome(
    *, committed: bool, checks, required: tuple[str, ...] = ()
) -> tuple[str, list[dict]]:
    """由客观检查推任务结局与未满足项（`03-contracts.md` §二）。规则写死在这一处：

    * 没提交 → failed（提交前检查不过、执行失败都走这里）；
    * 完整性 / 保持类检查任一 failed → failed（科学内容被动了，不是「部分完成」）；
    * 目标类：全部 failed → failed；部分 failed → partial；
    * 必检项（`required`）缺失 → 最高 partial；
    * 已提交时**任何**检查为 unknown（含验证器自己崩了合成的 `verifier_error`，不论是不是必检项）→ 最高 partial：
      `unmet` 里列着它，结局就不能是 completed；
    * 其余 → completed。"""
    checks = list(checks)
    unmet = [
        {"constraint_id": c.id, "reason_code": c.reason_code or c.verdict}
        for c in checks
        if c.verdict in (FAILED, UNKNOWN)
    ]
    by_id = {c.id: c for c in checks}
    for rid in required:
        if rid not in by_id:
            unmet.append({"constraint_id": rid, "reason_code": "not_run"})
    if not committed:
        return "failed", unmet
    if any(c.verdict == FAILED and c.role in ("integrity", "preserve") for c in checks):
        return "failed", unmet
    goals = [c for c in checks if c.role == "goal"]
    failed_goals = [c for c in goals if c.verdict == FAILED]
    if goals and len(failed_goals) == len(goals):
        return "failed", unmet
    if failed_goals:
        return "partial", unmet
    if any(rid not in by_id for rid in required) or any(c.verdict == UNKNOWN for c in checks):
        return "partial", unmet
    return "completed", unmet


# --------------------------------------------------------------------------- 适配器与执行器
@dataclass
class Plan:
    """任务计划：步骤序列（简单任务就一步）。`needs_input` 非空 = 决定科学含义的信息缺失，先问用户、不执行。"""

    steps: tuple[str, ...] = ("execute",)
    needs_input: str | None = None
    tools: frozenset[str] = frozenset()


class TaskAdapter(Protocol):
    """把一类业务任务变成可执行计划（`03-contracts.md` §六）。**不**实现历史、去重、取消、恢复、模型发现、SSE、写文件。"""

    task_type: str

    def validate(self, envelope: TaskEnvelope) -> list[str]: ...

    def plan(self, envelope: TaskEnvelope) -> Plan: ...

    def verify(self, stage: str, run: TaskRun, candidate: Any) -> list[CheckResult]: ...

    def explain(self, outcome: str, unmet: list[dict]) -> dict: ...

    # 可选：`correction(run, failed_checks) -> {"prompt": str} | None`——提交前检查失败时给出修正指令；不可修
    # （例如原项目被动了、只有 unknown）回 None。准不准修正、预算够不够由编排器判（`_admit_correction`），适配器不判


class Executor(Protocol):
    """产生候选并负责提交 / 丢弃。`kind == "agent"` 的执行器每次 `execute` 计一次 `agent_runs`。

    Phase 1 的执行器**不得**写用户文件（`writes_user_files` 必须为 False）；写已有脚本只经 AI 桥（Phase 3）。"""

    executor_id: str
    kind: str
    writes_user_files: bool

    def execute(self, run: TaskRun) -> Any: ...

    def commit(self, run: TaskRun, candidate: Any) -> None: ...

    def discard(self, run: TaskRun, candidate: Any) -> None: ...

    # 可选：`correct(run, candidate, feedback) -> candidate`——在**同一个**候选上按 `feedback["prompt"]` 再跑一次
    # Agent（不提交、不碰用户文件）；与 `execute` 同样以 TaskError 表示这次运行本身没成


#: 任务类型 → 适配器。**生产注册表只放真的能跑起来的**（同 `AGENT_REGISTRY` 的纪律）：Phase 1 为空，适配器随
#: Phase 3（figure_edit）/ Phase 5（data_to_figure）各自取得资格后登记；测试里的 Fake 适配器经 `register` 临时注入
ADAPTERS: dict[str, TaskAdapter] = {}


def register(adapter: TaskAdapter) -> None:
    name = getattr(adapter, "task_type", None)
    if not isinstance(name, str) or not _TASK_TYPE.fullmatch(name):
        raise ValueError(f"task_type {name!r}")
    if name in ADAPTERS and ADAPTERS[name] is not adapter:
        raise ValueError(f"task_type {name!r} 已登记")
    ADAPTERS[name] = adapter


def unregister(task_type: str) -> None:
    ADAPTERS.pop(task_type, None)


def get_adapter(task_type: str) -> TaskAdapter:
    try:
        return ADAPTERS[task_type]
    except KeyError:
        raise ContractError("task_type_unknown", task_type) from None


# --------------------------------------------------------------------------- 编排
class TaskRun:
    """一次任务执行的上下文：冻结的契约、计划、预算记账、取消与期限。只由编排器构造。"""

    def __init__(self, task_id: str, envelope: TaskEnvelope, plan: Plan, *, clock=time.monotonic):
        self.task_id = task_id
        self.envelope = envelope
        self.plan = plan
        self._clock = clock
        self._t0 = clock()
        self._cancel = threading.Event()
        #: 「取消」与「进入提交」互斥的门：`cancel()` 与 `_drive` 的最后一次复查都在门内，二者只有一个先到
        self._gate = threading.Lock()
        self._committing = False
        self.used = dict.fromkeys(BUDGET_KEYS, 0)
        #: 没过提交前检查的每一轮（证据）：`{round, failed: [[id, reason]], next}`，`next` = `correct` 或拒绝修正的原因
        self.attempts: list[dict] = []

    def charge(self, key: str, n: int = 1) -> None:
        """记一笔预算；超出上限抛 `BudgetExceeded`，**不记**超出的那一笔（它没被执行）。"""
        if self.used[key] + n > self.envelope.budget.limit(key):
            raise BudgetExceeded(key)
        self.used[key] += n

    def elapsed(self) -> float:
        return self._clock() - self._t0

    def remaining(self) -> float:
        return self.envelope.budget.max_wall_s - self.elapsed()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def budget_used(self) -> dict:
        return {**self.used, "wall_s": round(self.elapsed(), 3)}


#: 本进程里正在执行的任务（取消用）。跨进程的「同一请求只执行一次」靠 `ai_requests` 的原子预留，不靠这里
_RUNNING: dict[str, TaskRun] = {}
_RUNNING_LOCK = threading.Lock()


def cancel(task_id: str) -> bool:
    """请求取消一个本进程里正在执行的任务。编排器在每个阶段之间、以及**提交之前**复查：已取消就丢弃候选、不提交。

    回 True = 取消被接受（任务一定不会提交）；**进入提交后回 False**（取消未被接受，候选照常提交）。判定与「进入提交」
    在同一把门（`TaskRun._gate`）内，所以「回 True 却照样提交」不会发生。"""
    with _RUNNING_LOCK:
        run = _RUNNING.get(task_id)
    if run is None:
        return False
    with run._gate:
        if run._committing:
            return False
        run._cancel.set()
    return True


def load(task_id: str, db_path=None) -> dict | None:
    """任务的机器可读状态（来自历史库）。不是 TAF 任务的会话回 None。

    `outcome` 为空而会话已终态（老进程在写结局之前死了 → 启动清理改成 interrupted）时按 status 推：
    interrupted / cancelled 原样；其余记 `interrupted`——**不会**凭空给出 completed。"""
    row = ai_history.get(task_id, db_path=db_path)
    if row is None or row.get("task_type") is None:
        return None
    status = row["status"]
    outcome = row.get("outcome")
    phase = (
        row.get("phase") if status == "running" else None
    )  # 终态没有「进行中的阶段」（防老行残留）
    if outcome is None and status != "running":
        outcome = status if status in ("cancelled", "interrupted") else "interrupted"
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": row["id"],
        "task_type": row["task_type"],
        "status": status,
        "phase": phase,
        "outcome": outcome,
        "verification": row.get("verification"),
        "budget_used": row.get("budget_used"),
        "error": row.get("error"),
        "parent_task_id": row.get("parent_task_id"),
        "final": status != "running",
    }


def _status_for(outcome: str, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    return {"cancelled": "cancelled", "interrupted": "interrupted", "failed": "failed"}.get(
        outcome, "done"
    )


def run_task(
    envelope: TaskEnvelope, executor: Executor, *, db_path=None, clock=time.monotonic
) -> dict:
    """同步执行一个任务并返回它的 `load()` 结果（带 `deduped`）。

    顺序：校验（不登记）→ 原子预留 + 登记会话（同一 rid 第二次来 → 不执行、回既有任务）→ planning →
    executing → verifying（提交前）→ ready_to_commit →（复查取消 / 期限）→ committing → verifying（提交后）→ 终态。
    终态与结局在同一条 UPDATE 里落库（`ai_history.record_end(task_result=…)`）。"""
    adapter = get_adapter(envelope.task_type)
    if executor.executor_id != envelope.executor:
        raise ContractError("contract_executor", "执行器与契约不符")
    if executor.writes_user_files or envelope.allowed_writes:
        # Phase 1：写用户文件的提交只经 AI 桥（同一进程只有一张会话表、同一把脚本锁）；在那之前一律拒绝
        raise ContractError("contract_writes_unsupported", "Phase 1 的 Task Core 不写用户文件")
    problems = adapter.validate(envelope)
    if problems:
        raise ContractError("task_input_invalid", ",".join(problems))
    task_id = uuid.uuid4().hex[:12]
    reserved, existing = ai_history.record_start(
        {
            "id": task_id,
            "project": envelope.project_key,
            "provider": envelope.executor,
            "prompt": envelope.user_intent,
            "client_request_id": envelope.client_request_id,
            "task_type": envelope.task_type,
            "phase": "planning",
            "envelope": envelope.to_dict(include_intent=False),
            "parent_task_id": envelope.parent_task_id,
        },
        db_path=db_path,
    )
    if not reserved:
        found = load(existing, db_path=db_path) or {
            "task_id": existing,
            "outcome": None,
            "final": None,
        }
        return {**found, "deduped": True}

    # 计划在会话登记之后才做（它可以读项目 / 数据摘要）：它抛异常也必须落成一个终态，不能留一行永远 running
    run = TaskRun(task_id, envelope, Plan(steps=()), clock=clock)
    with _RUNNING_LOCK:
        _RUNNING[task_id] = run
    try:
        outcome, checks, unmet, error, timed_out = _drive(adapter, executor, run, db_path)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(task_id, None)
    ai_history.record_end(
        task_id,
        _status_for(outcome, timed_out),
        error=error,
        db_path=db_path,
        task_result={
            "outcome": outcome,
            "verification": {
                **verification_summary(checks),
                "unmet": unmet,
                "explain": _explain(adapter, outcome, unmet),
                "attempts": list(run.attempts),
            },
            "budget_used": run.budget_used(),
        },
    )
    return {**load(task_id, db_path=db_path), "deduped": False}


def begin_registered(task_id: str, envelope: TaskEnvelope, *, clock=time.monotonic) -> TaskRun:
    """AI 桥（Phase 3）已经替这个任务做完原子预留与会话登记（`ai_bridge.run` 的 `record_start` 带着任务字段）：这里只
    建 `TaskRun` 并挂进本进程的运行表——此后 `cancel(task_id)` 才有对象可取消。与 `drive_registered` 成对使用。

    桥是**唯一**允许带写用户文件的执行器走编排的调用方：同一进程只有一张会话表（`ai_bridge.SESSIONS`）、同一把脚本锁、
    同一份改前快照（回滚沿用 `ai_bridge.revert`）。`run_task` 对写文件的契约照旧拒绝。"""
    adapter = get_adapter(envelope.task_type)
    problems = adapter.validate(envelope)
    if problems:
        raise ContractError("task_input_invalid", ",".join(problems))
    run = TaskRun(task_id, envelope, Plan(steps=()), clock=clock)
    with _RUNNING_LOCK:
        _RUNNING[task_id] = run
    return run


def discard_registered(task_id: str) -> None:
    """`begin_registered` 之后、`drive_registered` 之前失败（例如进程没起来）：把任务从运行表摘下，此后 cancel 回 False。"""
    with _RUNNING_LOCK:
        _RUNNING.pop(task_id, None)


def drive_registered(run: TaskRun, executor: Executor, *, db_path=None) -> dict:
    """在 `begin_registered` 挂好的任务上推进阶段，回 `{outcome, error, timed_out, task_result}`。

    **不写终态**：终态与 `task_result` 由桥的 `_finish_session` 在同一条 `record_end` UPDATE 里落库（桥还要补 diff /
    changed / refresh，分两次写会留下「outcome 有了、diff 还没」的半条记录）。返回之前从运行表摘下：此后 cancel 回 False。"""
    adapter = get_adapter(run.envelope.task_type)
    try:
        outcome, checks, unmet, error, timed_out = _drive(adapter, executor, run, db_path)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(run.task_id, None)
    return {
        "outcome": outcome,
        "error": error,
        "timed_out": timed_out,
        "task_result": {
            "outcome": outcome,
            "verification": {
                **verification_summary(checks),
                "unmet": unmet,
                "explain": _explain(adapter, outcome, unmet),
                "attempts": list(run.attempts),
            },
            "budget_used": run.budget_used(),
        },
    }


def _explain(adapter: TaskAdapter, outcome: str, unmet: list[dict]) -> dict:
    try:
        out = adapter.explain(outcome, unmet)
    except Exception:  # noqa: BLE001 —— 文案生成失败不能让任务结局落不了库
        return {"message_key": f"task.outcome.{outcome}", "params": {}}
    return (
        out if isinstance(out, dict) else {"message_key": f"task.outcome.{outcome}", "params": {}}
    )


def _guarded_verify(adapter: TaskAdapter, stage: str, run: TaskRun, candidate) -> list[CheckResult]:
    """验证器自己崩溃 = 一条 unknown（`verifier_error`），不是 verified，也不是任务崩溃。"""
    try:
        return list(adapter.verify(stage, run, candidate))
    except Exception as exc:  # noqa: BLE001 —— 验证器的任何异常都要落成证据，而不是吞掉或冒泡
        return [
            CheckResult(
                id=f"{stage}.verifier",
                layer="L1",
                stage=stage,
                role="integrity",
                verdict=UNKNOWN,
                reason_code="verifier_error",
                observed={"error": type(exc).__name__},
            )
        ]


def _failure_signature(checks) -> frozenset[tuple[str, str]]:
    """一轮提交前检查的失败集合：没过的每条 `(id, reason_code)`。同一任务内执行配置不变，这就是「根因」的指纹。"""
    return frozenset(
        (c.id, c.reason_code or c.verdict)
        for c in checks
        if c.stage == "precommit" and c.verdict not in (VERIFIED, NOT_APPLICABLE)
    )


def _admit_correction(adapter, executor, run: TaskRun, pre) -> tuple[dict | None, dict | None]:
    """修正准入（`03-contracts.md` §五），回 `(feedback, None)` 或 `(None, 拒绝原因 | None)`。规则只写在这一处：

    * 契约没放开（`max_corrections == 0`）或执行器 / 适配器不支持修正 → 不修正、不加原因（与 Phase 3 逐字相同）；
    * 每条没过的检查都必须是**具体的 failed**——有 unknown（量不出来）就没有可以告诉 Agent 的失败条件；
    * 适配器判可修（原项目被动了之类的失败不可修，见各适配器）；
    * 失败集合与之前任何一轮都不同（0127「无新根因不重试」：同样的失败再来一轮只会再失败一次）；
    * `corrections` 与（Agent 执行器的）`agent_runs` 都还有余量（先判、不记账：被拒的修正没有执行）。"""
    budget = run.envelope.budget
    offer = getattr(adapter, "correction", None)
    if budget.max_corrections == 0 or offer is None or getattr(executor, "correct", None) is None:
        return None, None
    failing = [
        c for c in pre if c.stage == "precommit" and c.verdict not in (VERIFIED, NOT_APPLICABLE)
    ]
    if any(c.verdict != FAILED for c in failing):
        return None, {"constraint_id": "correction", "reason_code": "not_correctable"}
    try:
        feedback = offer(run, failing)
    except Exception:  # noqa: BLE001 —— 写不出修正指令就不修正，结局照旧是提交前检查不过
        feedback = None
    if not isinstance(feedback, dict) or not isinstance(feedback.get("prompt"), str):
        return None, {"constraint_id": "correction", "reason_code": "not_correctable"}
    signature = [list(k) for k in sorted(_failure_signature(pre))]
    if any(a["failed"] == signature for a in run.attempts):
        return None, {"constraint_id": "correction", "reason_code": "no_new_failure"}
    for key in ("corrections", "agent_runs") if executor.kind == "agent" else ("corrections",):
        if run.used[key] >= budget.limit(key):
            return None, {"constraint_id": f"budget.{key}", "reason_code": "budget_exhausted"}
    return feedback, None


def _drive(adapter: TaskAdapter, executor: Executor, run: TaskRun, db_path):
    """阶段推进本体。回 `(outcome, checks, unmet, error, timed_out)`；只有它决定提不提交。"""
    checks: list[CheckResult] = []
    candidate = None
    committed = False

    def phase(name: str) -> None:
        ai_history.record_task_phase(run.task_id, name, db_path=db_path)

    def stop() -> str | None:
        if run.cancelled():
            return "cancelled"
        if run.expired():
            return "timeout"
        return None

    def discard() -> None:
        if candidate is not None and not committed:
            try:
                executor.discard(run, candidate)
            except Exception:  # noqa: BLE001 —— 清理失败不改变结局；候选只在执行器自己的目录里
                pass

    def halted(why: str):
        discard()
        if why == "cancelled":
            return (
                "cancelled",
                checks,
                [{"constraint_id": "task", "reason_code": "cancelled"}],
                None,
                False,
            )
        return (
            "failed",
            checks,
            [{"constraint_id": "budget.max_wall_s", "reason_code": "timeout"}],
            "timeout",
            True,
        )

    try:
        run.plan = adapter.plan(run.envelope)
        if run.plan.needs_input:
            return (
                "needs_input",
                checks,
                [{"constraint_id": "intent", "reason_code": run.plan.needs_input}],
                None,
                False,
            )
        if why := stop():  # 计划期间就被取消 / 超时：一行用户代码、一次模型调用都不发生
            return halted(why)
        phase("executing")
        if executor.kind == "agent":
            run.charge("agent_runs")
        candidate = executor.execute(run)
        if why := stop():
            return halted(why)
        phase("verifying")
        pre = _guarded_verify(adapter, "precommit", run, candidate)
        while not precommit_admits(pre):
            # 有界修正（Phase 4）：只在提交前、只在同一个候选上；被拒的那一轮连同拒绝原因记进 attempts
            feedback, refused = _admit_correction(adapter, executor, run, pre)
            run.attempts.append(
                {
                    "round": len(run.attempts) + 1,
                    "failed": [list(k) for k in sorted(_failure_signature(pre))],
                    "next": "correct"
                    if feedback is not None
                    else (refused or {}).get("reason_code"),
                }
            )
            if feedback is None:
                checks += pre
                discard()
                outcome, unmet = derive_outcome(
                    committed=False, checks=checks, required=run.envelope.required_checks
                )
                if refused:
                    unmet.append(refused)
                return outcome, checks, unmet, "precommit_not_verified", False
            if why := stop():
                checks += pre
                return halted(why)
            phase("correcting")
            if executor.kind == "agent":
                run.charge("agent_runs")
            run.charge("corrections")
            candidate = executor.correct(run, candidate, feedback)
            if why := stop():
                checks += pre
                return halted(why)
            phase("verifying")
            pre = _guarded_verify(adapter, "precommit", run, candidate)
        checks += pre
        phase("ready_to_commit")
        with (
            run._gate
        ):  # 最后一次复查与「进入提交」原子：此后 cancel() 回 False，此前的取消必然被这里看到
            why = stop()
            if not why:
                run._committing = True
        if why:
            return halted(why)
        phase("committing")
        executor.commit(run, candidate)
        committed = True
        phase("verifying")
        checks += _guarded_verify(adapter, "postcommit", run, candidate)
    except BudgetExceeded as exc:
        discard()
        outcome, unmet = derive_outcome(
            committed=committed, checks=checks, required=run.envelope.required_checks
        )
        unmet.append({"constraint_id": f"budget.{exc.key}", "reason_code": "budget_exhausted"})
        return (
            ("partial" if committed and outcome == "completed" else outcome),
            checks,
            unmet,
            exc.code,
            False,
        )
    except TaskError as exc:
        discard()
        outcome, unmet = derive_outcome(
            committed=committed, checks=checks, required=run.envelope.required_checks
        )
        return ("failed" if not committed else outcome), checks, unmet, exc.code, False
    except Exception as exc:  # noqa: BLE001 —— 未预期的执行器异常：如实记失败，不合成成功
        discard()
        outcome, unmet = derive_outcome(
            committed=committed, checks=checks, required=run.envelope.required_checks
        )
        return (
            ("failed" if not committed else outcome),
            checks,
            unmet,
            f"executor_error:{type(exc).__name__}",
            False,
        )
    outcome, unmet = derive_outcome(
        committed=True, checks=checks, required=run.envelope.required_checks
    )
    if run.expired():
        # 提交 / 提交后验证把墙钟预算用完了：已提交的改动撤不回，结局最高 partial（failed 保持 failed）。
        # `timed_out` 取 False：落库 status 为 done/failed 而不是 `timeout`——`timeout` 在现有路径里意味着「没提交」，
        # 套在已提交的任务上会让人以为什么都没发生；超时事实由 error="timeout" 与 unmet 里的 budget.max_wall_s 表达
        unmet.append({"constraint_id": "budget.max_wall_s", "reason_code": "timeout"})
        return ("partial" if outcome == "completed" else outcome), checks, unmet, "timeout", False
    return outcome, checks, unmet, None, False
