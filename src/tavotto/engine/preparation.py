"""ProjectPreparation 第一版：**计划**与**观测**分开的异步准备接口（统一实施包 U01，ADR 0053）。

04_ARCHITECTURE §1 里 ProjectPreparation 站在四类入口与 ExecutionSpec 之间：环境证据 +
完整依赖意图 + 执行上下文 + 授权。U00 清点（`U00_CAPABILITY_INVENTORY.md` §2）的结论是
这一层今天**不存在**——四样东西分别散在 `pool.resolve_worker_python` / `depresolve` /
`workdir` / 前端确认文案里。本模块把它们**收拢成一份可读的计划**，再把「按这份计划
真的把 runtime 起起来」做成一个可查询、可取消的后台任务。

## 两个结构，两个问题

* `PreparationPlan`（不可变）——「打算怎么跑」：项目 / 入口 / 脚本、原选择与候选理由
  （解释器来源标签 + `projectenv.state()` 的 automatic / trigger / module）、Python 要求
  （今天没有任何地方声明，如实写 `declared: False`）、DependencyIntent 列表与冲突、
  LaunchContext、grant、预算。**它不是执行证明。**
* `PreparationResult`（可变、只由执行线程写）——「实际发生了什么」：状态、现有 runtime
  的事实、这次是不是新起的会话、ExecutionReceipt、错误、取消时刻。

## 状态（闭集）

    pending ─▶ running ─▶ ready
                  │  ├───▶ error
                  │  └───▶ cancelled
                  └── static_source_available（没有脚本可跑 / 只要静态源：不起线程）

「现有 runtime 正在运行」不是一个状态，是 `existing_runtime` 这条**事实**：计划起步时
池里已经有这个脚本的活会话就记下它的 generation 与 built。已经 build 过、且解释器决策
没变的，**直接用它记下的 build 响应装配回执，runner 一次都不调**（FO-031：二开不重复
准备——不是「再问一次 worker」，是一个字节都不发）；正在冷启动的等它（`pool.acquire()`
拿到同一条会话，worker 侧对已 build 的会话早返回，脚本不重跑）。

## 取消的边界（D11 / FO-009）

`cancel()` 只对**这个计划拥有的**东西负责：还没起会话时取消 = 一行用户代码都不跑；
会话是本计划新起的（`created_runtime`，**由 `pool.build_owned()` 在池锁里给出**，不是从
起步时的快照推断——两份计划同时起步都看到「没有」，池只建一条，主人只能是一个）且
还在 build → `pool.force_cancel` 杀掉它；会话本来就在（别的消费者的）→ **不杀**，只是
本计划不再等它、状态记 cancelled。native 会话不在池里，这里永远碰不到它。取消不承诺
撤销已发生的外部副作用（脚本可能已经写了文件），响应里 `note` 如实说。

## 不做的事

* 不复制旧 request 默认值、不解析 `tavotto run` 命令、不另起错误枚举（错误原样是
  `pool.WorkerError` 的 code / message / module）；
* 不新造 trust 系统：grant 只是 `workdir.grant_for()` 那一条记录；
* 不替产品选环境、不装包、不改 cwd——计划**读**的是产品自己的决定。

纯标准库 + 兄弟模块（Flask 父进程 import 链上）。项目绑定由调用方（app.py）用
`bound_project` 注入：engine 层不 import app。
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import threading
import time
import uuid
from pathlib import Path

from . import (
    databinding,
    deprepair,
    depresolve,
    execspec,
    figcapture,
    pool,
    projectenv,
    receipt,
    trace as tracemod,
    workdir,
)

LOG = logging.getLogger("tavotto.preparation")

#: 计划形态的版本。加可选字段不升；改语义 / 删字段才升。
PLAN_VERSION = 1

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_READY = "ready"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
STATUS_STATIC = "static_source_available"
#: 首开需要用户先决定一件事（U03，ADR 0057）：不起线程、一行用户代码不跑。`result.required_input`
#: 是结构化的「需要什么」（今天只有一种：`workdir` 的 cwd 选择，载荷见
#: `workdir.confirmation_payload`）。答完（PATCH /api/engine/workdir）再起一份新计划。
STATUS_NEEDS_INPUT = "needs_input"
STATUSES = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_READY,
    STATUS_ERROR,
    STATUS_CANCELLED,
    STATUS_STATIC,
    STATUS_NEEDS_INPUT,
)
TERMINAL = frozenset(
    {STATUS_READY, STATUS_ERROR, STATUS_CANCELLED, STATUS_STATIC, STATUS_NEEDS_INPUT}
)

#: 计划在起会话之前作废的稳定 code（FO-007 / 「过期授权」）：计划记下的授权 / 模式
#: 与此刻项目设置里的不一致——用户在计划之后撤销或改了工作目录。不跑、不猜，报出来。
ERROR_PLAN_STALE = "preparation_plan_stale"
ERROR_CODES = (ERROR_PLAN_STALE,)
#: 作废的理由（闭集，随 `error.reason` 带出；U09，ADR 0071 / FO30）：授权变了 / 项目的解释器决策变了 /
#: 预检记下的数据绑定变了（同名文件换了内容、被删、被加）。`executed` 说的是「发现时脚本跑过没有」：
#: 起会话之前发现的一行没跑；build 之后按观察到的输入发现的，脚本已经跑过一次——结果照样作废（不把
#: 旧计划的回执冒充实际执行），重新准备会复用那条已 build 的会话。
STALE_GRANT = "grant_changed"
STALE_ENVIRONMENT = "environment_changed"
STALE_DATA_BINDING = "data_binding_changed"
STALE_REASONS = (STALE_GRANT, STALE_ENVIRONMENT, STALE_DATA_BINDING)
_STALE_MESSAGES = {
    STALE_GRANT: "工作目录的授权在计划之后变了，这份计划作废；请重新准备",
    STALE_ENVIRONMENT: "这个项目选中的解释器在计划之后变了，这份计划作废；请重新准备",
    STALE_DATA_BINDING: "脚本要读的数据在预检之后变了，这份计划作废；请重新准备",
}

#: 作业保留多久（秒）——与导出作业同一口径：界面拿 plan_id 补拉要在窗口内。
_TTL_S = 15 * 60


# ---------------------------------------------------------------- 计划


@dataclasses.dataclass(frozen=True)
class PreparationPlan:
    """打算怎么跑（不可变）。机器路径只有 `project_root` 与 `interpreter` 两项，都**不进**
    `to_payload()`——HTTP / MCP 投影是公开身份（ADR 0053 §二），安装目录 / 用户目录 / venv
    的绝对路径只留在私有键与本机日志里。"""

    plan_id: str
    project_id: str
    project_root: str
    interpreter: str  # 解释器绝对路径（机器相关；只给执行线程与诊断用）
    asset_id: str
    stem: str
    script: str | None  # 项目相对 POSIX 路径；没有脚本（纯静态素材）时 None
    entry: str | None
    static_source: dict | None  # 磁盘原件的 SourceArtifact payload（没有就 None）
    environment: dict  # 原选择与候选理由（见 `plan_for`）
    python_requirement: dict  # {"declared": False}——今天没有任何地方声明脚本要哪个 minor
    dependency_intents: tuple[dict, ...]
    dependency_conflicts: tuple[dict, ...]
    launch_context: dict | None  # 没有脚本时 None
    grant: dict
    budget: dict
    created_at: float
    #: 工作目录决定（U03）：`workdir.decision_for()` 的只读结果——决定过没有、用哪一档、
    #: 静态证据（`databinding.evidence`）。没有脚本时 None。
    workdir_decision: dict | None = None
    #: 起会话之前要用户先答的事（U03）；None = 不需要。有值时 `register()` 直接落
    #: `needs_input`，不起线程。U04 起还有第二种：`dependency_preparation_required`（脚本开跑
    #: 要的包目标环境里没有、且能一次装全——先授权一次）。
    required_input: dict | None = None
    #: 联合依赖计划（U04，ADR 0061 §三 / §六）：`deprepair.preparation_offer` 的只读结果——脚本
    #: 开跑要什么 / 目标缺什么 / 交给安装器的集合 / blocked 的理由 / 可选目标。没有脚本或
    #: 解释器解析不出来时 None。**它只是计划的一部分，不装任何东西。**
    dependency_preparation: dict | None = None
    #: 数据绑定（U09，ADR 0070）：预检那一刻按 cwd 档记下的「脚本会读哪些文件、内容是什么」
    #: （`databinding.binding_for()`：相对项目根的路径 → sha256、修订摘要）。没有脚本时 None。
    #: 执行线程起会话之前再算一次，不同就是 `preparation_plan_stale` / `data_binding_changed`。
    binding: dict | None = None

    def to_payload(self) -> dict:
        return {
            "plan_version": PLAN_VERSION,
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "asset_id": self.asset_id,
            "stem": self.stem,
            "script": self.script,
            "entry": self.entry,
            "static_source": dict(self.static_source) if self.static_source else None,
            "environment": dict(self.environment),
            "python_requirement": dict(self.python_requirement),
            "dependency_intents": [dict(d) for d in self.dependency_intents],
            "dependency_conflicts": [dict(d) for d in self.dependency_conflicts],
            "launch_context": dict(self.launch_context) if self.launch_context else None,
            "grant": dict(self.grant),
            "budget": dict(self.budget),
            "created_at": self.created_at,
            "workdir_decision": dict(self.workdir_decision) if self.workdir_decision else None,
            "required_input": dict(self.required_input) if self.required_input else None,
            "dependency_preparation": (
                dict(self.dependency_preparation) if self.dependency_preparation else None
            ),
            "binding": dict(self.binding) if self.binding else None,
        }


def plan_for(
    *,
    project_id: str,
    project_root: str,
    asset_id: str,
    stem: str,
    script: str | None,
    entry: str | None,
    original_artifact: str | None,
    original_path: str | None = None,
) -> PreparationPlan:
    """按产品**自己的**决定拼一份计划——这里不做任何选择，只读。

    `original_artifact` 是素材的项目相对身份（进计划）；`original_path` 是**调用方已经
    校验过**（`app.safe_resolve`：在项目根内、是文件）的绝对路径，读字节算 hash 只用它。
    只给了前者时不读盘：这里不重复做路径校验，也不拿原串重拼。

    环境证据来自 `pool.resolve_worker_python()`（项目级决策的唯一出处）与
    `projectenv.state()`；解析不到解释器时 `environment.error` 带上 code，计划照样
    成立（执行阶段会以同一个错误收场，那时才是 `error` 状态）。**这一步就是「环境选择
    前移」的落点**（U03，ADR 0057）：脚本的首次科学执行发生在 `start()` 之后，而项目 venv
    的发现 + 体检 + 记住在这里已经完成——计划里写清选了谁、凭什么（`evidence`）、发现了
    什么没采用（`discovery.rejected`）、上一条自动决策是不是刚被作废（`invalidated`）。
    显式选择失效（`explicit_python_unusable` / `project_python_unusable`）不静默替换：
    `error.explicit` 说明是哪一条、为什么。
    """
    root = str(project_root)
    try:
        python, source = pool.resolve_worker_python(root, script=script)
        env_error = None
    except pool.WorkerError as exc:
        python, source, env_error = "", "", {"code": exc.code, "message": str(exc)}
        explicit = getattr(exc, "explicit", None)
        if isinstance(explicit, dict):
            env_error["explicit"] = {
                "source": explicit.get("source", ""),
                "reason": explicit.get("reason", ""),
                "trigger": explicit.get("trigger", ""),
                "python": _project_relative(root, str(explicit.get("python", ""))),
            }
    state = projectenv.state(root)
    # 公开身份：来源标签 + **项目相对**路径（项目外的解释器——bundled / system / 用户在别处
    # 挑的——一律 None：那是安装目录或用户目录，不进投影）+ 项目记住的版本事实。
    discovery = pool.first_open_outcome(root)
    environment = {
        "python": _project_relative(root, python) if python else None,
        "source": source,
        "source_label": pool.SOURCE_LABELS.get(source, source),
        "automatic": bool(state.get("automatic", False)),
        "trigger": state.get("trigger", ""),
        "module": state.get("module", ""),
        # 选中那条解释器体检时量到的事实（记住时存进项目设置；没体检过的老链条为空）
        "python_version": state.get("python_version", ""),
        "matplotlib_version": state.get("matplotlib_version", ""),
        "support": state.get("support", ""),
        # 首开发现：找到了哪些 venv、体检过而没采用的各是为什么（项目相对路径）
        "discovery": (
            {
                "ok": bool(discovery.get("ok")),
                "code": discovery.get("code", ""),
                "candidates": [
                    _project_relative(root, c) for c in discovery.get("candidates") or []
                ],
                "rejected": [
                    {
                        "venv": _project_relative(root, r.get("venv", "")),
                        "code": r.get("code", ""),
                        "support": r.get("support", ""),
                        "python_version": r.get("python_version", ""),
                        "matplotlib_version": r.get("matplotlib_version", ""),
                    }
                    for r in discovery.get("rejected") or []
                ],
            }
            if discovery is not None
            else None
        ),
        # 上一条**自动**记住的决策刚被判失效并作废（venv 被删 / 重建成别的 Python）
        "invalidated": (
            {
                "python": _project_relative(root, inv.get("python", "")),
                "reason": inv.get("reason", ""),
                "trigger": inv.get("trigger", ""),
            }
            if (inv := pool.invalidated_decision(root))
            else None
        ),
        "error": env_error,
    }
    grant = workdir.grant_for(root)
    launch_context = None
    decision = None
    if script is not None:
        # 工作目录：决定过就是记住的那一档；没决定过按脚本的静态证据判要不要先问
        # （U03，ADR 0057）。这里**只读**同一份判据（`resolve_mode()` 在 spawn 前抛的
        # 就是它），计划里如实写下证据与「需要输入」，不替用户决定。
        decision = workdir.decision_for(root, script)
    binding = None
    if script is not None:
        # 数据绑定按此刻的 cwd 档记（决定过就是记住的那一档，没决定过是默认沙盒——问的话，答完重新准备时再记）
        binding = databinding.binding_for(
            Path(root) / figcapture.normalize_relative_script(script),
            root,
            decision["mode"] if decision else workdir.mode_for(root),
        )
    if script is not None and entry is not None and python:
        spec = execspec.safe_spec(
            script,
            root,
            entry,
            interpreter=python,
            sandbox="",
            cwd_mode=decision["mode"] if decision else workdir.mode_for(root),
        )
        launch_context = execspec.launch_context(spec, grant=grant)
    intents = depresolve.declared_intents(root, script) if script else []
    # 联合依赖（U04）：工作目录不用问、解释器也解析得出时，看一眼脚本开跑要的包目标环境里
    # 缺不缺。缺且能一次装全 → 这是第二种「需要输入」（同一对每进程只问一次：`deprepair.gate`）；
    # blocked / 什么都不缺 → 只把诊断写进计划，照跑。
    dependency = None
    required = (decision or {}).get("confirmation") or None
    # 一个解释器都没有（`no_worker_python`）时也问一次门：干净机器上门会以私有 Python 为目标算计划
    # （U05，ADR 0063 / 0064）——不提供私有 Python 时门回 None，照旧以原错误收场
    clean_machine = bool(env_error) and env_error.get("code") == deprepair.NO_WORKER_PYTHON
    if script is not None and (python or clean_machine) and required is None:
        gate = deprepair.gate(root, script)
        if gate is not None:
            dependency = gate
            required = gate
        else:
            dependency = deprepair.preparation_offer(root, script)
    static = None
    if original_artifact and original_path:
        try:
            if Path(original_path).stat().st_size > 0:
                static = figcapture.source_artifact_from_file(
                    original_path,
                    source_id=original_artifact.replace("\\", "/"),
                    origin=figcapture.ORIGIN_STATIC,
                ).to_payload()
        except (OSError, ValueError):
            static = None
    return PreparationPlan(
        plan_id=f"prep-{uuid.uuid4().hex}",
        project_id=project_id,
        project_root=root,
        interpreter=python,
        asset_id=asset_id,
        stem=stem,
        script=script,
        entry=entry,
        static_source=static,
        environment=environment,
        python_requirement={"declared": False},
        dependency_intents=tuple(i.to_payload() for i in intents),
        dependency_conflicts=tuple(depresolve.conflicts(intents)),
        launch_context=launch_context,
        grant=grant,
        budget={
            "build_idle_timeout_s": pool.BUILD_IDLE_TIMEOUT,
            "build_hard_timeout_s": pool.BUILD_HARD_TIMEOUT,
        },
        created_at=time.time(),
        workdir_decision=decision,
        required_input=required,
        dependency_preparation=dependency,
        binding=binding,
    )


def _project_relative(root: str, path: str) -> str | None:
    """项目内的路径 → 项目相对 POSIX；项目外 → None（**不回绝对路径**，它不进公开投影）。"""
    try:
        return (
            Path(path)
            .resolve(strict=False)
            .relative_to(Path(root).resolve(strict=False))
            .as_posix()
        )
    except (ValueError, OSError):
        return None


#: 错误分支里 `pool.try_project_env` 的结构化结果进公开投影时只留这几个键：其余
#: （`python` / `candidates` / `system` 体检表…）都带着解释器的绝对路径。
_PUBLIC_PROJECT_ENV_KEYS = ("ok", "code", "module", "reason")


def _public_project_env(outcome: dict) -> dict:
    return {k: outcome[k] for k in _PUBLIC_PROJECT_ENV_KEYS if k in outcome}


# ---------------------------------------------------------------- 观测


@dataclasses.dataclass
class PreparationResult:
    """实际发生了什么（只由执行线程与 `cancel()` 写；读者拿的是快照）。

    终局字段（`receipt` / `error` / `finished_at`）**先于**终局 `status` 写——与
    `exportjob` 同一条纪律（issue #381）：另一线程读到 `ready` 时回执一定已经在。
    """

    status: str = STATUS_PENDING
    existing_runtime: dict | None = None  # 起步时池里那条会话的事实
    created_runtime: bool = False  # 本计划新起的会话
    receipt: receipt.ExecutionReceipt | None = None
    error: dict | None = None
    #: `status == needs_input` 时是要用户答的那件事（与 `plan.required_input` 同源；
    #: 执行期才发现要问的——计划之后被撤销了决定——也落在这里）。
    required_input: dict | None = None
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested_at: float | None = None
    note: str = ""
    #: 阶段轨迹（U09，ADR 0071）：plan → check → spawn → execute → receipt，坏在哪一步就停在哪一步。
    trace: tracemod.Trace = dataclasses.field(default_factory=tracemod.Trace)

    def to_payload(self) -> dict:
        return {
            "status": self.status,
            "existing_runtime": dict(self.existing_runtime) if self.existing_runtime else None,
            "created_runtime": self.created_runtime,
            "receipt": self.receipt.to_payload() if self.receipt is not None else None,
            "error": dict(self.error) if self.error else None,
            "required_input": dict(self.required_input) if self.required_input else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested_at": self.cancel_requested_at,
            "note": self.note,
            "trace": self.trace.to_payload(),
        }


@dataclasses.dataclass
class _Entry:
    plan: PreparationPlan
    result: PreparationResult
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    thread: threading.Thread | None = None


class PreparationService:
    """计划登记表 + 执行线程。进程内一份（`SERVICE`），测试可另起实例。"""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    # ---- 登记 ----
    def register(self, plan: PreparationPlan) -> PreparationResult:
        """登记一份计划。没有脚本可跑的直接落 `static_source_available`（不起线程）。"""
        result = PreparationResult()
        if plan.script is None:
            result.note = (
                "这张图没有脚本，只有静态原件"
                if plan.static_source
                else "这张图没有脚本，也没有可读的静态原件"
            )
            result.finished_at = time.time()
            result.status = STATUS_STATIC
        elif plan.required_input:
            # 首开要先问用户（U03 的运行目录 / U04 的依赖准备）：不起线程、不碰 pool、一行用户代码不跑。
            result.required_input = dict(plan.required_input)
            result.note = (
                "需要先授权一次依赖准备（或明确选择不准备直接运行）；答完后重新准备"
                if plan.required_input.get("code") == deprepair.ERROR_PREPARATION_REQUIRED
                else "需要先选择脚本的运行目录；答完后重新准备"
            )
            result.finished_at = time.time()
            result.status = STATUS_NEEDS_INPUT
        with self._lock:
            self._sweep()
            self._entries[plan.plan_id] = _Entry(plan=plan, result=result)
        return result

    def start(self, plan_id: str, *, runner, bind=None) -> None:
        """起执行线程。`runner(plan) -> (worker, build_resp, created)`（app 注入
        `pool.build_owned`），`bind()` 是项目绑定上下文（app 注入 `bound_project(ctx)`）。"""
        entry = self._entry(plan_id)
        if entry is None:
            raise KeyError(plan_id)
        if entry.result.status != STATUS_PENDING:
            return

        def _run() -> None:
            with bind() if bind is not None else contextlib.nullcontext():
                self._execute(entry, runner)

        entry.thread = threading.Thread(
            target=_run, name=f"tavotto-prep-{plan_id[-8:]}", daemon=True
        )
        entry.thread.start()

    def _execute(self, entry: _Entry, runner) -> None:
        plan, result = entry.plan, entry.result
        tr = result.trace
        result.started_at = time.time()
        tr.mark("plan", plan_id=plan.plan_id)
        existing = pool.peek(plan.script, plan.project_root)
        if existing is not None:
            result.existing_runtime = {
                "generation": int(existing.generation),
                "built": bool(existing.built),
                "control_plane": pool.control_plane_of(existing),
            }
        # 取消接受时刻 ①：还没碰 pool——一行用户代码都没跑，直接收工。
        if entry.cancel.is_set():
            tr.cancel("check")
            self._finish(entry, STATUS_CANCELLED, note="在起会话之前取消，没有执行任何脚本")
            return
        # 过期计划（FO-007 / FO30，ADR 0071）：计划之后用户撤销 / 改了工作目录决定、项目的解释器决策变了、
        # 或预检记下的数据绑定变了——计划记下的与此刻的不一致时**不跑**：按旧计划跑等于拿撤销前的许可 /
        # 旧数据的判断执行，按新设置跑等于回执与计划两张嘴。报 `preparation_plan_stale` + `reason`，
        # 让调用方重新准备（用户的 live 编辑在文档里，不在这份计划里，一个字不丢）。
        stale = self._stale_reason(plan) if plan.script is not None else None
        if stale is not None:
            reason, detail = stale
            tr.fail("check", ERROR_PLAN_STALE, reason=reason)
            result.error = {
                "code": ERROR_PLAN_STALE,
                "reason": reason,
                "executed": False,
                "message": _STALE_MESSAGES[reason],
                **detail,
            }
            self._finish(entry, STATUS_ERROR, note=f"计划过期（{reason}），没有执行任何脚本")
            return
        tr.mark("check")
        result.status = STATUS_RUNNING
        reusable = self._reusable(existing, plan)
        if reusable is not None:
            # 已 build 过的会话：回执从它记下的 build 响应装配，不发任何请求、不碰脚本。
            result.created_runtime = False
            rcpt = receipt.from_worker(
                existing,
                reusable,
                control_plane=pool.control_plane_of(existing),
                grant=plan.grant,
                binding=plan.binding,
            )
            result.receipt = rcpt
            check = rcpt.binding_check()
            tr.mark(
                "receipt", reused=True, completeness=rcpt.completeness, binding=check["matched"]
            )
            # 热态 Figure 代表它跑那一刻的数据（04 §3）：此刻预检记下的数据与它当时读到的不同，不是错误——
            # 图是明确的旧快照，回执的 `binding_check.matched=False` 如实写着；要不要重算是用户的事，
            # 不自动清空编辑、不自动重跑
            note = "复用现有 runtime，没有重新执行脚本"
            if check["matched"] is False:
                note += "；它读到的数据与此刻预检记下的不同（图是旧快照，重算请重建会话）"
            self._finish(entry, STATUS_READY, note=note)
            return
        tr.mark("spawn")
        try:
            worker, resp, created = runner(plan)
        except pool.WorkerError as exc:
            confirmation = getattr(exc, "confirmation", None)
            if isinstance(confirmation, dict):
                # 计划时不用问、起会话时要问（决定在中间被清掉了）：与计划期的「需要输入」
                # 同一个终局，不是错误。
                tr.mark("spawn", code=workdir.ERROR_CONFIRMATION_REQUIRED)
                result.required_input = dict(confirmation)
                self._finish(
                    entry, STATUS_NEEDS_INPUT, note="需要先选择脚本的运行目录；答完后重新准备"
                )
                return
            dependency = getattr(exc, "dependency_preparation", None)
            if isinstance(dependency, dict):
                # 依赖那道门（U04）在起会话那一刻拦住了：同样是「需要输入」，不是错误。
                tr.mark("spawn", code=deprepair.ERROR_PREPARATION_REQUIRED)
                result.required_input = dict(dependency)
                self._finish(
                    entry,
                    STATUS_NEEDS_INPUT,
                    note="需要先授权一次依赖准备（或明确选择不准备直接运行）；答完后重新准备",
                )
                return
            error = {"code": getattr(exc, "code", "") or "worker_error", "message": str(exc)}
            module = getattr(exc, "module", "")
            if module:
                error["module"] = module
            project_env = getattr(exc, "project_env", None)
            if isinstance(project_env, dict):
                error["project_env"] = _public_project_env(project_env)
            result.error = error
            # 脚本自己炸 / 缺依赖是「执行」那一步坏的；起不来（解释器 / 沙盒）是「起会话」坏的
            tr.fail("execute" if getattr(exc, "traceback_text", None) else "spawn", error["code"])
            self._finish(entry, STATUS_ERROR)
            return
        except Exception as exc:  # noqa: BLE001 — 线程里不许静默死掉，如实记
            result.error = {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
            tr.fail("spawn", "internal_error")
            self._finish(entry, STATUS_ERROR)
            return
        tr.mark("execute", created=bool(created))
        result.created_runtime = bool(created)
        rcpt = receipt.from_worker(
            worker,
            resp,
            control_plane=pool.control_plane_of(worker),
            grant=plan.grant,
            binding=plan.binding,
        )
        result.receipt = rcpt
        tr.mark(
            "receipt",
            completeness=rcpt.completeness,
            rejected=rcpt.runtime_rejected,
            observation=(rcpt.inputs or {}).get("observation"),
        )
        # 取消接受时刻 ②：build 期间来了取消。本计划新起的会话才由我们收掉；
        # 本来就在的（别的消费者的）一根手指都不碰（FO-009）。
        if entry.cancel.is_set():
            if result.created_runtime:
                pool.force_cancel(plan.script, plan.project_root)
                note = "build 期间取消：本计划新起的会话已关闭；脚本已经产生的外部副作用不撤销"
            else:
                note = "build 期间取消：会话属于别的消费者，未关闭；本计划不再等它"
            tr.cancel("receipt")
            self._finish(entry, STATUS_CANCELLED, note=note)
            return
        if self._binding_mismatch(entry, rcpt, executed=True):
            return
        self._finish(entry, STATUS_READY)

    @staticmethod
    def _stale_reason(plan: PreparationPlan) -> tuple[str, dict] | None:
        """起会话之前把计划记下的三样东西与此刻各比一次：授权（`workdir.grant_for`）、项目的解释器决策
        （`pool.resolve_worker_python`）、数据绑定（`databinding.binding_for`）。第一条不一致的就是理由。"""
        root = plan.project_root
        if workdir.grant_for(root) != plan.grant:
            return STALE_GRANT, {}
        if plan.interpreter:
            try:
                python_now = pool.resolve_worker_python(root, script=plan.script)[0]
            except pool.WorkerError:
                python_now = None
            if python_now is None or (
                python_now != plan.interpreter
                and not pool.same_python(python_now, plan.interpreter)
            ):
                # 只报「变了」，不报路径（公开投影不带机器路径，ADR 0053 §二）
                return STALE_ENVIRONMENT, {}
        if plan.binding is not None:
            now = databinding.binding_for(
                Path(root) / figcapture.normalize_relative_script(plan.script),
                root,
                plan.binding["mode"],
            )
            if now["revision"] != plan.binding["revision"]:
                before, after = plan.binding.get("expected") or {}, now.get("expected") or {}
                changed = sorted(
                    k for k in set(before) | set(after) if before.get(k) != after.get(k)
                )
                return STALE_DATA_BINDING, {"changed": changed}
        return None

    def _binding_mismatch(
        self, entry: _Entry, rcpt: receipt.ExecutionReceipt, *, executed: bool
    ) -> bool:
        """本计划**新跑**的 build 之后按观察到的输入再核一次数据绑定（FO30 的第二道：数据在起会话与读取
        之间被改）。不一致 → 计划作废（`executed=True` 如实写着脚本已经跑过一次；重新准备会复用这条会话，
        新计划的绑定就是它读到的那份）；一致 / 没观察到 → 不拦。复用现有 runtime 的那条路不走这里。"""
        check = rcpt.binding_check()
        if check["matched"] is not False:
            return False
        entry.result.trace.fail("receipt", ERROR_PLAN_STALE, reason=STALE_DATA_BINDING)
        entry.result.error = {
            "code": ERROR_PLAN_STALE,
            "reason": STALE_DATA_BINDING,
            "executed": executed,
            "changed": list(check["changed"]),
            "message": _STALE_MESSAGES[STALE_DATA_BINDING],
        }
        self._finish(
            entry,
            STATUS_ERROR,
            note="计划过期（data_binding_changed）：脚本读到的数据与预检记下的不一致；请重新准备",
        )
        return True

    @staticmethod
    def _reusable(existing, plan: PreparationPlan) -> dict | None:
        """池里那条会话能不能直接当「现有 runtime」用：活着、build 过、**且**它的解释器
        仍是这个项目此刻的决策（用户换了环境的话它是旧世界的，`pool.get()` 会重建）。
        能用就回它记下的 build 响应形态（descriptors + runtime），否则 None。"""
        if existing is None or not getattr(existing, "built", False):
            return None
        try:
            wanted = pool.resolve_worker_python(plan.project_root)[0]
        except pool.WorkerError:
            return None
        if not pool.same_python(getattr(existing, "python", None), wanted):
            return None
        return {
            "descriptors": list(getattr(existing, "last_build_descriptors", None) or []),
            "runtime": getattr(existing, "last_build_runtime", None),
        }

    def _finish(self, entry: _Entry, status: str, *, note: str = "") -> None:
        with entry.lock:
            entry.result.finished_at = time.time()
            if note:
                entry.result.note = note
            entry.result.status = status  # 终局 status 最后写

    # ---- 查询 / 取消 ----
    def _entry(self, plan_id: str) -> _Entry | None:
        with self._lock:
            return self._entries.get(plan_id)

    def get(
        self, plan_id: str, project_id: str
    ) -> tuple[PreparationPlan, PreparationResult] | None:
        """按项目取。**项目对不上就是没有**（FO-008：两个项目同名脚本的异步状态不串用）。"""
        entry = self._entry(plan_id)
        if entry is None or entry.plan.project_id != project_id:
            return None
        return entry.plan, entry.result

    def cancel(self, plan_id: str, project_id: str) -> dict:
        """请求取消。回「接受了没有」，不是「已经取消了」（提交点见模块头）。"""
        entry = self._entry(plan_id)
        if entry is None or entry.plan.project_id != project_id:
            return {"accepted": False, "reason": "not_found"}
        with entry.lock:
            status = entry.result.status
            if status in TERMINAL:
                return {"accepted": False, "reason": status}
            entry.result.cancel_requested_at = time.time()
            entry.cancel.set()
        # pending 且线程还没起：当场收工（起了线程由线程自己在接受时刻 ① 收）
        if status == STATUS_PENDING and entry.thread is None:
            self._finish(entry, STATUS_CANCELLED, note="在起会话之前取消，没有执行任何脚本")
        return {"accepted": True, "reason": ""}

    def wait(self, plan_id: str, timeout: float | None = None) -> bool:
        """测试 / CLI 用：等执行线程结束。"""
        entry = self._entry(plan_id)
        if entry is None or entry.thread is None:
            return entry is not None
        entry.thread.join(timeout)
        return not entry.thread.is_alive()

    def _sweep(self) -> None:
        cutoff = time.time() - _TTL_S
        for pid, e in list(self._entries.items()):
            fin = e.result.finished_at
            if fin is not None and fin < cutoff:
                del self._entries[pid]

    def reset_for_tests(self, *, join_timeout: float = 30.0) -> None:
        """清空登记表，**并把还在跑的执行线程收回来**：用例结束时 monkeypatch 已经还原，一条没跑完的线程会在
        还原后的世界里继续——拿真 pool 起一条真 worker，泄到下一个用例（U09 全量里 `test_workerd_pool` 就被这样
        诬告过）。先置取消位再 join（有界），线程要么在两个取消接受时刻收工，要么跑完。"""
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            entry.cancel.set()
        for entry in entries:
            if entry.thread is not None and entry.thread.is_alive():
                entry.thread.join(join_timeout)


#: 进程内唯一登记表（app.py 用它）。
SERVICE = PreparationService()
