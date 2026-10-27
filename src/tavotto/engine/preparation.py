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
import hashlib
import logging
import threading
import time
import uuid
from pathlib import Path

from . import (
    databinding,
    deprepair,
    depresolve,
    envadvice,
    execpolicy,
    execspec,
    exectrace,
    figcapture,
    inputbroker,
    inputremap,
    launchctx,
    launcher,
    nativesession,
    pool,
    projectenv,
    receipt,
    rootenv,
    runconfig,
    runroute,
    scriptargs,
    taskdiag,
    trace as tracemod,
    workdir,
)

LOG = logging.getLogger("tavotto.preparation")

#: 计划形态的版本。加可选字段不升；改语义 / 删字段才升。
PLAN_VERSION = 1

TARGET_ASSET = "asset"
TARGET_SCRIPT = "script"
#: 脚本目标的执行语义「让运行前路由挑」（ADR 0133）：HTTP 入口在请求省略执行语义时带进目标；引擎层省略 = safe
EXECUTION_AUTO = runroute.EXECUTION_AUTO

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
STALE_SOURCE = "script_changed"
STALE_REASONS = (STALE_GRANT, STALE_ENVIRONMENT, STALE_DATA_BINDING, STALE_SOURCE)
_STALE_MESSAGES = {
    STALE_GRANT: "工作目录的授权在计划之后变了，这份计划作废；请重新准备",
    STALE_ENVIRONMENT: "这个项目选中的解释器在计划之后变了，这份计划作废；请重新准备",
    STALE_DATA_BINDING: "脚本要读的数据在预检之后变了，这份计划作废；请重新准备",
    STALE_SOURCE: "脚本在检查之后被改了（参数声明 / 输出文件可能不同），这份计划作废；请重新检查",
}


class _StaleBeforeRetry(Exception):
    """`before_retry` 发现计划在两次执行之间过期了（理由是 `STALE_REASONS` 之一）。"""

    def __init__(self, reason: str, detail: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


_NEEDS_INPUT_AFTER_REPLAN_NOTE = (
    "自动采用用户的 Python 后，新环境需要先答一件事；没有执行任何脚本，答完后重新准备"
)


class _NeedsInputBeforeRetry(Exception):
    """缺包后自动接手换了解释器、就地重算出的计划要用户先答一件事（依赖准备 / 运行目录）：第二次执行不能跑。"""

    def __init__(self, required_input: dict) -> None:
        super().__init__("needs_input")
        self.required_input = dict(required_input)


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
    #: 准备的主语（T01）：`asset` = 已知的一张图（`asset_id` / `stem` 有值）；`script` = 只有一份脚本、
    #: 还没有 `asset_id`（`stem` 为空，「成功」= 至少捕获到一张图，不是某一张）。旧计划没有这个键 = `asset`。
    target: str = TARGET_ASSET
    #: 用户给的精确 argv 的运行配置（T03，`execspec.RunSelection`）。**私有**：argv 原文只在这里和 worker
    #: 命令行里，`to_payload()` / 回执 / LaunchContext 只带数量与本机不透明引用 `run_config`。None = 没给参数。
    run: object | None = None
    #: 数据改指也是计划的输入快照。私有字段；动作认领与执行线程都经 `_stale_reason` 对账。
    input_remap_generation: int | None = None
    #: 脚本目标的源码修订（`scriptargs.source_revision`：字节摘要 + 解析出的 schema 摘要，私有）。披露给用户的影响
    #: （含 `script_writes`）是按这一版源码算的；认领 / 执行前再算一次，不同 = `preparation_plan_stale` /
    #: `script_changed`，一行不跑。资产目标（已知图）不记（None）：它们的源码修订由写回事务管。
    script_revision: str | None = None
    #: 这份计划选定的执行语义（`execspec.PROFILE_SAFE` | `PROFILE_NATIVE`=桌面拥有的 native，P06 / ADR 0125）。
    #: 私有：不进 `to_payload()`（公开的是 `launch_context`）。就地重算计划、复用现有会话都按它对账，
    #: 不得悄悄回落成 safe。
    execution: str = execspec.PROFILE_SAFE
    #: `execution == native` 时的桌面拥有的启动身份（`execspec.desktop_native_spec` 的产物；`desktopproc.OwnedLaunch`
    #: 吃它，P07）。**私有**：含 argv / env 原文，不进 `to_payload()`；公开的是 `launch_context`。safe 恒 None。
    native_spec: object | None = None
    #: 计划那一刻解释器的完整启动上下文（`launcher.context_key`：`direct` 或 Conda 激活 + conda + prefix + 环境版本）。
    #: 私有。conda install / 改 activate.d、conda 从找不到变成找得到（直接启动 → 激活）都不换「代」，但跑起来的环境变了：
    #: 执行前 `_stale_reason` 比它，对不上 = 计划过期（P09）
    environment_state: str = ""
    #: 计划那一刻解释器要带的叠加环境（ROOT，`rootenv.overlay_spec`）。私有：含安装路径，不进 `to_payload()`。
    #: 起会话的钉下决定（`app._plan_pin`）只认它——漏了它，计划钉的 `EnvironmentDecision` 没有叠加层，worker 在同一条
    #: 解释器里裸起、脚本报缺 ROOT（Win11 真机 2026-10-21）；执行前 `_stale_reason` 比它与项目记录此刻的那份
    environment_overlay: dict | None = None
    #: 这份计划是对上一次尝试的**重试**（V2，ADR 0127）：上一次尝试的 plan_id / 触发它的稳定错误码 / 相对上一次变了哪几维
    #: （`execpolicy.CHANGED_DIMENSIONS`）。三者都是诊断事实：不含路径 / argv。第一次尝试全为空。
    retry_of: str | None = None
    retry_cause: str = ""
    retry_changed: tuple[str, ...] = ()
    #: 运行前路由的结果（V2 PR2，ADR 0133）：只有调用方给了 `execution="auto"` 才有（`runroute.Route.public()`：闭集原因 /
    #: 置信度 / 命中规则 / 行号 / 耗时，不含路径与源码）；显式 safe / native 恒 None。**不入语义指纹**（`execution` 已在里面）
    route: dict | None = None
    #: 登记那一刻项目的关闭代次（私有，仅桌面拥有的 native 计划；`PreparationService.register` 盖章）。启动时带给注册表：
    #: 关闭之后才走到起进程的旧计划，即使项目已重新打开也被拒（代次对不上）
    project_epoch: int | None = None
    #: 登记序号（私有，仅桌面拥有的 native 计划；`PreparationService.register` 盖章，单调递增）。启动的票据与会话都带着它：
    #: 同脚本重跑只取消序号**更小**（更早登记）的启动，序号是唯一的先后依据——和准备层的取消同一个顺序
    launch_order: int | None = None

    def to_payload(self) -> dict:
        return {
            "plan_version": PLAN_VERSION,
            "target": self.target,
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
            **({"route": dict(self.route)} if self.route else {}),
            **(
                {"run_config": self.run.config_id, "argv_count": len(self.run.argv)}
                if self.run is not None
                else {}
            ),
        }


def _last_success_profile(
    root: str, script: str, *, python: str, source: str, run=None
) -> str | None:
    """这个脚本上一次成功用的执行语义（`launchctx` 记录），记录此刻仍成立才算；否则 None。

    成立 = 解释器 / 环境 / 脚本字节 / 运行目录档都没变，**且记录的运行配置就是本次请求的运行配置**（argv 的不透明引用相同）。"""
    if not python:
        return None
    try:
        record = runconfig.launch_record(root, script)
    except runconfig.RunConfigError:
        return None
    if record is None:
        return None
    facets = (
        launchctx.FACET_INTERPRETER,
        launchctx.FACET_ENVIRONMENT,
        launchctx.FACET_SOURCE,
        launchctx.FACET_CWD,
    )
    reason = launchctx.verify(
        record, project_root=root, script=script, python=python, python_source=source, facets=facets
    )
    if reason is not None:
        return None
    # 记录是对**某个运行配置**的成功：本次请求的运行配置不同（或记录有而请求没有），沿用的就是别人的结论
    if record.get("run_config") != (run.config_id if run is not None else None):
        return None
    profile = launchctx.profile_of(record)
    if (
        profile == execspec.PROFILE_NATIVE
        and (record.get("source") or {}).get("basis_observed") is False
    ):
        # 与 #982 的 reuse_last_success 同样保守：这条 native 记录没有完整的 helper / 数据观察（#1047），不证明它们没变，不据此沿用 native
        return None
    return profile


def _route_of(
    root: str, script: str, *, python: str, source: str, decision: dict | None, run=None
) -> tuple[runroute.Route, str | None]:
    """`execution="auto"` 的落点：策略 / 运维开关 / 上次成功 / 静态证据 → 路由（零执行用户代码）。
    同时返回路由分析的那份字节的 sha256，`plan_for` 用它核对计划的 `script_revision`（#1034：分析与 revision 须是同一份内容）。"""
    policy = execpolicy.mode_for(root)
    enabled = execpolicy.fallback_enabled()
    last = None
    if policy != execpolicy.MODE_SAFE_ONLY and enabled:
        last = _last_success_profile(root, script, python=python, source=source, run=run)
    mode = decision["mode"] if decision else workdir.mode_for(root)
    path = Path(root) / figcapture.normalize_relative_script(script)
    try:
        with open(path, "rb") as fh:
            raw: bytes | None = fh.read(runroute.MAX_SOURCE_BYTES + 1)
    except OSError:
        raw = None
    route = runroute.route_for_file(
        path,
        cwd_mode=mode,
        project_root=root,
        script=script,
        policy=policy,
        last_success=last,
        operator_enabled=enabled,
        source=raw,
    )
    LOG.info(
        "run route %s reason=%s confidence=%s rules=%s ms=%.2f",
        route.execution,
        route.reason,
        route.confidence,
        ",".join(sorted({e["rule"] for e in route.evidence})) or "-",
        route.elapsed_ms,
    )
    return route, (hashlib.sha256(raw).hexdigest() if raw is not None else None)


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
    target: str = TARGET_ASSET,
    run=None,
    execution: str = execspec.PROFILE_SAFE,
) -> PreparationPlan:
    """按产品**自己的**决定拼一份计划——这里不做任何选择，只读。

    `execution` 是这份计划打算用哪一种**执行语义**跑（P06，ADR 0125）：默认 `safe`（旧行为，
    逐字节不变）；`native` = 桌面拥有的 native 进程（保留普通文件 I/O，`LaunchContext` 如实写
    `write_mode=unrestricted`、`cwd_origin=desktop.cwd`、`owner=desktop`）。**它只改计划里的启动
    语义，不替调用方起进程**——真正的 runner 仍由调用方注入（`PreparationService.start`），
    `desktopproc.start` 是 native 这一支的执行侧。换解释器与换执行语义是两个维度：选了用户的
    Python 并不会让 safe 自动变成 native。

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
    decision = pool.EnvironmentDecision()
    decide_error: pool.WorkerError | None = None
    remap_generation = inputremap.generation(root)
    if script is not None:
        # 「换不换解释器」先落地（ADR 0079 §四，`deprepair.decide_environment` 是唯一一处；检测模式下就是那次自动检测，
        # ADR 0114 §六）：下面的解释器、LaunchContext、环境事实都是快照，执行前 `_stale_reason` 拿它们与此刻比——快照在
        # 决定之前拍，第一次准备就以 `preparation_plan_stale` 收场（Codex #522 P1）。
        # 检查（建会话 / 出报告）在用户点「运行」之前：项目自己带的解释器（项目 venv、`.vscode` 指向的……）不在这里
        # 起，体检推迟到运行那一下（`_run` 里的 `decide_environment_pinned`；Codex 安全 #820 r4232804805）
        try:
            decision = deprepair.decide_environment_pinned(root, script, project_exec=False)
        except pool.WorkerError as exc:
            # 例如 `conda_launcher_unavailable`（Conda 激活取环境失败）：与解析解释器失败同一条路——写进计划的
            # 环境错误，计划照样成立；不是 500
            decide_error = exc
    adopted = decision.adopted
    try:
        if decide_error is not None:
            raise decide_error
        if decision.pinned:
            # 检测出的解释器是个不可变值（含环境代）：快照只认它，不再解析共享的项目记录——同一项目的另一个脚本
            # 可能刚在两次解析之间把记录换了（Codex #820 P1）。换了的话执行前 `_stale_reason` 拿它与此刻比，
            # 对不上就 `environment_changed`，绝不在没为这个脚本验证过的解释器下跑
            python, source = decision.python, decision.source
        else:
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
    # 环境建议（T05，ADR 0114）：纯读，不起任何解释器。`generation` 是计划那一刻解释器所在环境的「代」——
    # 起会话之前比一次（`_stale_reason`），同一路径被重建的环境不能拿旧计划去跑
    recommendation = envadvice.recommend(root, script)
    # 公开身份：来源标签 + **项目相对**路径（项目外的解释器——bundled / system / 用户在别处
    # 挑的——一律 None：那是安装目录或用户目录，不进投影）+ 项目记住的版本事实。
    discovery = pool.first_open_outcome(root)
    # 作废的事实只报一次：写进这一份计划就消费掉，后面的检查不再重复说「换过了」
    invalidated = pool.consume_invalidated(root)
    # Conda 环境版本的快照（私有，进 `PreparationPlan.environment_state`）：与上面的「代」同一刻、同一份决定取。
    # 必须在这里取——下面 `decision` 这个名字会被工作目录的决定覆盖
    environment_state = (
        (decision.launch_context if decision.pinned else launcher.context_key(python))
        if python
        else ""
    )
    environment_overlay = (
        (decision.env_overlay if decision.pinned else projectenv.remembered_overlay(root, python))
        if python
        else None
    )
    environment = {
        "python": _project_relative(root, python) if python else None,
        "source": source,
        "source_label": pool.SOURCE_LABELS.get(source, source),
        "automatic": bool(state.get("automatic", False)),
        "trigger": state.get("trigger", ""),
        "module": state.get("module", ""),
        "generation": (
            (decision.generation if decision.pinned else projectenv.environment_generation(python))
            if python
            else ""
        ),
        # 项目级决定的授权来源：confirmed（用户明确采用）/ legacy_auto（ADR 0114 之前机器记下的，照用但不当确认）/ none
        "consent": state.get("consent", projectenv.CONSENT_NONE),
        "recommendation": recommendation,
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
            if (inv := invalidated)
            else None
        ),
        "error": env_error,
        # 给用户看的那一个事实（ADR 0114 §六）：用的是哪一类、谁定的、这次是不是刚自动换了一个——不要求用户动作
        "adoption": envadvice.adoption_fact(
            root,
            source,
            adopted=adopted,
            invalidated=invalidated,
            effective=python,
            rejected=decision.rejected,
            overlay=environment_overlay,
        ),
    }
    grant = workdir.grant_for(root)
    launch_context = None
    native_spec = None
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
    route = None
    if execution == EXECUTION_AUTO:
        # 运行前路由（V2 PR2，ADR 0133）：只在用户发起的桌面运行里由准备会话要 `auto`；CLI / MCP 永远是显式 safe
        execution = execspec.PROFILE_SAFE
        if script is not None and target == TARGET_SCRIPT:
            route, routed_digest = _route_of(
                root, script, python=python, source=source, decision=decision, run=run
            )
            if route.execution == execspec.PROFILE_NATIVE and not python:
                route = route.downgraded("no_interpreter")  # 没有解释器就没有 native 可起
            execution = route.execution
    script_revision = _script_revision(root, script) if target == TARGET_SCRIPT else None
    if (
        route is not None
        and route.execution == execspec.PROFILE_NATIVE
        and (script_revision is None or routed_digest != script_revision.split(":", 1)[0])
    ):
        # 路由分析之后脚本又被改写：native 的依据是旧内容，新内容没有被证明过。回 safe，下次检查再路由
        route = route.downgraded("script_changed")
        execution = route.execution
    if script is not None and python and execution == execspec.PROFILE_NATIVE:
        mode = decision["mode"] if decision else workdir.mode_for(root)
        spec = execspec.desktop_native_spec(
            str(Path(root) / figcapture.normalize_relative_script(script)),
            interpreter=python,
            cwd=execspec.desktop_cwd(root, script, mode),
            project_root=root,
            # 桌面「打开并运行」的输入通道：脚本的 input / stdin / getpass 由界面作答（P07，ADR 0126）
            stdin=execspec.STDIN_BRIDGE,
            # 无缓冲：stdout 管道对 Python 默认块缓冲，不设的话界面看不到实时输出（ADR 0125 残余）
            env={"PYTHONUNBUFFERED": "1"},
            **execspec.run_kwargs(run),
        )
        native_spec = spec
        launch_context = execspec.launch_context(spec, grant=grant)
    elif execution not in (execspec.PROFILE_SAFE, execspec.PROFILE_NATIVE):
        raise ValueError(f"execution 非法: {execution!r}")
    elif script is not None and entry is not None and python:
        spec = execspec.safe_spec(
            script,
            root,
            entry,
            interpreter=python,
            sandbox="",
            cwd_mode=decision["mode"] if decision else workdir.mode_for(root),
            **execspec.run_kwargs(run),
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
        if (
            dependency is not None
            and python
            and required is not None
            and required.get("code") == deprepair.ERROR_PREPARATION_REQUIRED
            and deprepair.project_check_pending(root, script, dependency.get("plan") or {})
        ):
            # 项目自带的环境还没体检（用户点「运行」才体检）：现在不说「需要安装」，主动作是运行；体检跑不了再回到
            # 安装待办（`_execute` 里点运行时重算计划）。不要求授权，所以也不挂 `required_input`
            dependency = {**dependency, "project_check_pending": True}
            required = None
    if (
        script is not None
        and clean_machine
        and not python
        and (required is None or required.get("code") == deprepair.ERROR_PREPARATION_REQUIRED)
    ):
        # 默认链条一个解释器都没有（干净机器）而项目带了还没体检的环境（如 .venv）：同样等用户点「运行」才体检——主动作是
        # 运行，不先推去装私有 Python / 受管环境；体检跑不了再回到安装待办（Codex #820 r4233563595）。脚本的 import 静态推出
        payload = (dependency or {}).get("plan") or deprepair.static_plan_payload(root, script)
        if payload is not None and deprepair.project_check_pending(root, script, payload):
            dependency = {**(dependency or {}), "project_check_pending": True}
            environment["project_check_pending"] = True
            required = None
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
        execution=execution,
        route=route.public() if route is not None else None,
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
        target=target,
        run=run,
        input_remap_generation=remap_generation,
        script_revision=script_revision,
        native_spec=native_spec,
        environment_state=environment_state,
        environment_overlay=environment_overlay,
    )


def _kill_owned_worker(worker) -> None:
    """取消「本计划新建、已不在池里」的那条会话。

    桌面拥有的 native 会话（`nativesession.NativeSession`，P07）的 `force_kill` 是**放手**（终端语义，不杀进程）；
    用户在界面里点取消要的是真终止，所以走 `cancel_owned()`（结束进程组 / Job，确认后才放租约）。池里的 safe
    worker 沿用 `force_kill`。"""
    if getattr(worker, "owned", None) is not None and callable(
        getattr(worker, "cancel_owned", None)
    ):
        worker.cancel_owned()
    else:
        worker.force_kill()


def implied_grant_absorbable(project_root: str, old: dict, new: dict) -> bool:
    """授权从 `old`（计划记下的）变成 `new`（此刻重算的），是不是**只**因为本次运行自动采用了用户自己的 Python
    （ADR 0107 §二修订，2026-10-22 用户裁决）——是才可以就地吸收，不让用户再点一次。

    四条同时成立，缺一不可：① 计划时没决定过、没有任何授权、也没有派生理由（用户从未表过态）；② 此刻仍没决定过
    （没人在这期间设过工作目录）；③ 此刻的授权恰是派生默认：`granted=True / granted_at=None / mode=project`、
    `implied_by=user_interpreter`——范围与用户第一次手动点同一个环境时 `workdir.grant_for` 给出的一字不差；
    ④ 吸收的只是这一种变化，别的（用户改了设置、别的会话改了）一律不吸收，照旧 `grant_changed`。"""
    zero = {"granted": False, "granted_at": None, "mode": None}
    if (
        old.get("decided") is not False
        or old.get("implied_by") is not None
        or old.get("cwd_write") != zero
    ):
        return False
    if workdir.decided(project_root):
        return False
    derived = {
        "cwd_write": {"granted": True, "granted_at": None, "mode": workdir.MODE_PROJECT},
        "decided": False,
        "implied_by": workdir.IMPLIED_BY_USER_INTERPRETER,
    }
    return new == derived and workdir.grant_for(project_root) == derived


def _binding_sans_mode(binding: dict | None) -> dict | None:
    """数据绑定去掉随 cwd 档变的 `mode` / `revision` 之后的内容（吸收派生授权时比「数据是否真变了」）。"""
    if binding is None:
        return None
    return {k: v for k, v in binding.items() if k not in ("mode", "revision")}


def _script_revision(root: str, script: str | None) -> str | None:
    if script is None:
        return None
    return scriptargs.source_revision(Path(root) / figcapture.normalize_relative_script(script))


def _captured_stems(build_resp) -> list[str] | None:
    """build 响应里捕获到的 stem 表：`stems`（v1 build 一直带着）优先，没有就按 `descriptors`；两样都没有
    = 说不出（None，不判）。"""
    if not isinstance(build_resp, dict):
        return None
    stems = build_resp.get("stems")
    if isinstance(stems, dict):
        return [str(s) for s in stems]
    descriptors = build_resp.get("descriptors")
    if isinstance(descriptors, list):
        return [str(d.get("stem")) for d in descriptors if isinstance(d, dict) and d.get("stem")]
    return None


def _captured_of(worker, build_resp) -> dict | None:
    """build 响应里捕获到的图（stem 表 + 描述符 + 这次按哪一代改指表跑的），私有，不进公开投影。"""
    stems = _captured_stems(build_resp)
    if stems is None:
        return None
    descriptors = build_resp.get("descriptors") if isinstance(build_resp, dict) else None
    failed = build_resp.get("failed_figures") if isinstance(build_resp, dict) else None
    return {
        **(
            {"failed_figures": [dict(f) for f in failed if isinstance(f, dict)], "partial": True}
            if isinstance(failed, list) and failed
            else {}
        ),
        "stems": sorted(stems),
        "descriptors": list(descriptors) if isinstance(descriptors, list) else [],
        "remap_generation": getattr(worker, "remap_generation", None),
        # 这次执行的会话沙盒目录（safe 档默认模式下脚本的 cwd；`rasterhint` 据此核实相对路径写出的文件）
        "sandbox": str(getattr(getattr(worker, "spec", None), "sandbox", "") or ""),
    }


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
    #: 执行追踪（ADR 0151，只观察；**不进 `to_payload()`**）：执行线程开始时开、终局时把上面的阶段轨迹只读映射进来再冻结
    exec_trace: object = dataclasses.field(default_factory=exectrace.NullTrace, repr=False)
    exec_span: str | None = dataclasses.field(default=None, repr=False)
    #: 这次 build 捕获到的东西（T01）：`{"stems", "descriptors", "remap_generation"}`——执行之后要登记 /
    #: 物化的调用方（`prepsession`）从这里拿，不必再问 worker。**不进 `to_payload()`**（描述符里有路径）。
    captured: dict | None = None
    #: 数据找不到时的指认载荷（ADR 0106，`pool._offer_missing_input` 挂在 `WorkerError.missing_input` 上的那份）。
    #: T07：准备会话把它当一项待答的需求（回答走既有的 `/api/engine/input-remap`）。**不进 `to_payload()`**：
    #: 里面是脚本里写的路径串，诊断快照与回执都不带它。
    missing_input: dict | None = None
    #: 沙盒里落空的探路调用、而脚本目录里找得到（#1254，`workdir.probe_hint`）：失败 / 没出图 / 成功但图可能缺数据时的引导。
    #: 与 `missing_input` 同纪律：**不进 `to_payload()`**（里面是脚本里写的路径串）；准备会话把它当一项不阻塞的需求。
    workdir_hint: dict | None = None
    #: 这次执行里每一问 input 的去向（T08，`inputbroker.InputFacts.payload()`）：只有计数与闭集理由，只给
    #: 诊断快照的白名单投影。**不进 `to_payload()`**。
    input_facts: dict | None = None

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
    force_rebuild: bool = False
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    thread: threading.Thread | None = None
    #: 执行线程取到会话之后由 runner 报上来（`note_owner`，T09）：取到过没有 / 是不是本计划新建的那条。
    #: 取消据此**当场**决定：自己建的 → 立即关掉它；别人的（共享会话的等待者）→ 只是本计划不再等
    acquired: bool = False
    owned: object | None = None


class PreparationService:
    """计划登记表 + 执行线程。进程内一份（`SERVICE`），测试可另起实例。"""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        #: `cancel_project(project_id, script, keep)` 之前调的回调（准备会话登记；纯标记，不取别的锁）
        self.project_cancel_listeners: list = []
        self._order = 0  # 登记序号计数（`PreparationPlan.launch_order`）

    # ---- 登记 ----
    def register(
        self,
        plan: PreparationPlan,
        *,
        force_rebuild: bool = False,
        project_epoch: int | None = None,
    ) -> PreparationResult:
        """登记一份计划。没有脚本可跑的直接落 `static_source_available`（不起线程）。

        `project_epoch`：桌面 native 计划盖的项目关闭代次，缺省 = 登记那一刻的。执行回退（ADR 0127）传**运行开始时**记下的
        代次——登记发生在 safe 尝试之后，期间项目关闭再重开，盖新代次就让关闭前的旧运行通过注册表的代次检查。"""
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
        # 关闭代次只对桌面 native 计划有意义；先在锁外取（注册表有自己的锁），别夹在拿序号与入表之间
        if plan.execution == execspec.PROFILE_NATIVE:
            plan = dataclasses.replace(
                plan,
                project_epoch=(
                    project_epoch
                    if project_epoch is not None
                    else nativesession.REGISTRY.project_epoch(plan.project_root)
                ),
            )
        with self._lock:
            # 登记序号对所有计划盖（safe 的重跑让位也要拿它判先后）；拿序号与入表在同一把锁里，入表顺序
            # （`cancel_project` / `older_than` / `superseded` 用）与 `launch_order`（native runner 用）才一致（Codex #1028 r4273983562）
            self._order += 1
            plan = dataclasses.replace(plan, launch_order=self._order)
            self._sweep()
            self._entries[plan.plan_id] = _Entry(
                plan=plan, result=result, force_rebuild=force_rebuild
            )
        return result

    def start(self, plan_id: str, *, runner, bind=None, on_done=None) -> None:
        """起执行线程。`runner(plan, before_retry=…) -> (worker, build_resp, created)`（app 注入
        `pool.build_owned`），`bind()` 是项目绑定上下文（app 注入 `bound_project(ctx)`）。

        `on_done(plan, result)`：执行线程在终局 `status` 写完之后、**项目绑定仍在**的时候调一次（T01：准备会话
        在这里登记捕获到的图并发事件）。它抛出的异常只记日志，不改本计划的终局。"""
        entry = self._entry(plan_id)
        if entry is None:
            raise KeyError(plan_id)
        if entry.result.status != STATUS_PENDING:
            return

        def _run() -> None:
            with bind() if bind is not None else contextlib.nullcontext():
                self._execute(entry, runner)
                if on_done is not None:
                    try:
                        on_done(entry.plan, entry.result)
                    except Exception:  # noqa: BLE001 — 线程里不许静默死掉，如实记
                        LOG.exception("准备完成回调失败 %s", plan_id)

        entry.thread = threading.Thread(
            target=_run, name=f"tavotto-prep-{plan_id[-8:]}", daemon=True
        )
        entry.thread.start()

    @staticmethod
    def _replan_in_place(entry: _Entry, plan: PreparationPlan) -> PreparationPlan | None:
        """按此刻（已采用项目环境之后）的世界重算同一份计划，沿用计划 id / 创建时间 / 静态原件；换进条目里。"""
        fresh = plan_for(
            project_id=plan.project_id,
            project_root=plan.project_root,
            asset_id=plan.asset_id,
            stem=plan.stem,
            script=plan.script,
            entry=plan.entry,
            original_artifact=None,
            target=plan.target,
            execution=plan.execution,
            **({"run": plan.run} if plan.run is not None else {}),
        )
        # 只吸收「采用换了环境」带来的变化：授权档 / 数据绑定 / 源码 / 改指表任何一个也变了，就是别的过期原因，不就地
        # 换计划（交回 None，由对账给出过期结局）。唯一的授权例外：「没决定过 → 自动采用用户自己的 Python 派生出的
        # 默认授权（脚本目录）」（ADR 0107 §二修订，2026-10-22 用户裁决），见 `implied_grant_absorbable`
        absorbed = fresh.grant != plan.grant
        if absorbed:
            if not implied_grant_absorbable(plan.project_root, plan.grant, fresh.grant):
                return None
            # 授权档变了，绑定里的 `mode`（及随它变的 `revision`）必然跟着变；其余（读哪些文件、内容 sha、
            # 基准目录）一字不差才算同一份数据——数据真变了仍是别的过期原因
            if _binding_sans_mode(fresh.binding) != _binding_sans_mode(plan.binding):
                return None
        elif (fresh.binding or {}).get("revision") != (plan.binding or {}).get("revision"):
            return None
        if (
            fresh.script_revision != plan.script_revision
            or fresh.input_remap_generation != plan.input_remap_generation
        ):
            return None
        fresh = dataclasses.replace(
            fresh,
            plan_id=plan.plan_id,
            created_at=plan.created_at,
            static_source=plan.static_source,
            project_epoch=plan.project_epoch,
            launch_order=plan.launch_order,
        )
        entry.plan = fresh
        if absorbed:
            # 审计：这次运行吸收了一次派生授权（不是用户点头的授权）。facts 只收标量，随 `trace` 进回执旁的诊断
            entry.result.trace.mark(
                "check",
                grant_absorbed=workdir.IMPLIED_BY_USER_INTERPRETER,
                grant_mode=(fresh.grant.get("cwd_write") or {}).get("mode"),
            )
        return fresh

    def _execute(self, entry: _Entry, runner) -> None:
        plan, result = entry.plan, entry.result
        tr = result.trace
        result.started_at = time.time()
        _trace_begin(plan, result)
        tr.mark("plan", plan_id=plan.plan_id)
        existing = pool.peek(
            plan.script, plan.project_root, **({"run": plan.run} if plan.run is not None else {})
        )
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
        if plan.script is not None:
            # An explicit preparation may reuse via peek without calling acquire.
            # Settle source/style notifications before accepting that cached build.
            from . import project_watch

            project_watch.before_acquire(plan.project_root, plan.script)
            if entry.cancel.is_set():
                tr.cancel("check")
                self._finish(entry, STATUS_CANCELLED, note="在起会话之前取消，没有执行任何脚本")
                return
            existing = pool.peek(
                plan.script,
                plan.project_root,
                **({"run": plan.run} if plan.run is not None else {}),
            )
        # 过期计划（FO-007 / FO30，ADR 0071）：计划之后用户撤销 / 改了工作目录决定、项目的解释器决策变了、
        # 或预检记下的数据绑定变了——计划记下的与此刻的不一致时**不跑**：按旧计划跑等于拿撤销前的许可 /
        # 旧数据的判断执行，按新设置跑等于回执与计划两张嘴。报 `preparation_plan_stale` + `reason`，
        # 让调用方重新准备（用户的 live 编辑在文档里，不在这份计划里，一个字不丢）。
        if plan.script is not None:
            # 用户点了「运行」= 同意体检项目自带的候选解释器（Codex 安全 #820）：现在才可以体检、采用。采用了就换了
            # 解释器——这一步是**本次运行自己**做的，不是别人改了环境：就地按采用后的解释器重算计划再往下走（能跑就跑；
            # 缺包由起会话那道依赖门收成「需要安装」），不让用户为此重新检查一次。别的过期原因照旧由下面的对账拦下
            try:
                decision = deprepair.decide_environment_pinned(plan.project_root, plan.script)
            except pool.WorkerError as exc:
                # 例如 `conda_launcher_unavailable`（Conda 激活取环境失败）：一行用户代码都没跑，稳定码原样交给调用方
                code = getattr(exc, "code", "") or "worker_error"
                tr.fail("check", code)
                result.error = {"code": code, "message": str(exc), "executed": False}
                self._finish(entry, STATUS_ERROR, note="启动方式不可用，没有执行任何脚本")
                return
            if decision.adopted is not None:
                before = self._stale_reason(plan)
                if before is None or before[0] in (STALE_ENVIRONMENT, STALE_GRANT):
                    # 吸收环境变化，以及「没决定过 → 派生默认授权」那一种授权变化（`_replan_in_place` 里逐条核；
                    # 用户确认过的授权档变了、或授权因别的原因变了，它交回 None，下面照旧报过期）
                    plan = self._replan_in_place(entry, plan) or plan
                    if plan.required_input:
                        # 吸收后的新计划要用户先答一件事：发布的计划就是这份，不跑（与计划期的「需要输入」同终局）
                        result.required_input = dict(plan.required_input)
                        tr.mark("check", code=str(plan.required_input.get("code") or ""))
                        self._finish(
                            entry,
                            STATUS_NEEDS_INPUT,
                            note=_NEEDS_INPUT_AFTER_REPLAN_NOTE,
                        )
                        return
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
        reusable = None if entry.force_rebuild else self._reusable(existing, plan)
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
            result.captured = _captured_of(existing, reusable)
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
            if self._stem_missing(entry, existing, _captured_stems(reusable)):
                return
            result.workdir_hint = pool.workdir_hint_of_build(existing)
            self._finish(entry, STATUS_READY, note=note)
            return
        tr.mark("spawn")

        def before_retry() -> None:
            # 缺包后自动接手换了解释器（项目 venv / ADR 0107 的系统解释器，后者还把默认工作目录改成脚本
            # 目录）：计划记下的授权 / 解释器 / 数据绑定与此刻再比一次——与起会话之前同一份判据。第二次执行
            # 会在计划没写过的解释器或 cwd 里跑、回执却挂在这份不可变计划上——不跑，作废（Codex #713 P1 ×2）
            nonlocal plan
            stale = self._stale_reason(plan)
            if stale is not None and stale[0] == STALE_GRANT:
                # 缺包后自动接手了用户自己的 Python：同一种「本次运行自己带来的派生授权」，同样就地吸收
                plan = self._replan_in_place(entry, plan) or plan
                if plan.required_input:
                    # 新计划（目标解释器上另一个静态依赖也缺）要先问用户：不跑第二次，回到需要用户动作的结局
                    raise _NeedsInputBeforeRetry(plan.required_input)
                stale = self._stale_reason(plan)
            if stale is not None:
                raise _StaleBeforeRetry(*stale)

        try:
            if entry.force_rebuild:
                # Explicit reruns must not reuse workerd's cached spawn while
                # asynchronous retirement is pending. Refuse if close is unknown.
                pool.invalidate(plan.script, plan.project_root, plan.run, only_run=True, force=True)
            worker, resp, created = runner(plan, before_retry=before_retry)
        except _NeedsInputBeforeRetry as exc:
            if result.status in TERMINAL:
                return  # 等待者已被取消放手（T09）：迟到的结局不写
            tr.mark("execute", code=str(exc.required_input.get("code") or ""))
            result.required_input = dict(exc.required_input)
            self._finish(
                entry,
                STATUS_NEEDS_INPUT,
                note="缺包后自动换了解释器，新环境还需要先授权依赖准备 / 选择运行目录；"
                "没有按新环境重跑，答完后重新准备",
            )
            return
        except _StaleBeforeRetry as exc:
            if result.status in TERMINAL:
                return  # 等待者已被取消放手（T09）：迟到的结局不写
            tr.fail("execute", ERROR_PLAN_STALE, reason=exc.reason)
            result.error = {
                "code": ERROR_PLAN_STALE,
                "reason": exc.reason,
                # 第一次（按计划的解释器与 cwd）已经跑过、报了缺包；换了解释器之后的那一次没跑
                "executed": True,
                "message": _STALE_MESSAGES[exc.reason],
                **exc.detail,
            }
            self._finish(
                entry,
                STATUS_ERROR,
                note=f"缺包后自动换了解释器，计划随之过期（{exc.reason}）：没有按新环境重跑；请重新准备",
            )
            return
        except pool.WorkerError as exc:
            if result.status in TERMINAL:
                return  # 等待者已被取消放手（T09）：迟到的结局不写
            if entry.cancel.is_set():
                # 取消接受时刻 ③ 的落地：本计划新建的会话被当场关掉，build 以 WorkerError 回来——这是用户的取消，
                # 不是脚本的错
                result.input_facts = getattr(exc, "input_facts", None)
                tr.cancel("execute")
                self._finish(
                    entry,
                    STATUS_CANCELLED,
                    note="运行中取消：本计划新起的会话已当场关闭；脚本已经产生的外部副作用不撤销",
                )
                return
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
            _attach_local_detail(
                error, exc, sensitive=bool(plan.run is not None and plan.run.sensitive)
            )
            # 诊断包的「最近缺依赖现场」：准备路径把 WorkerError 接在这里，不经 `app._worker_error`
            # 修复 offer 与试运行路径（probe.py）同一个函数、同样的入参：只读判断，不起解释器、不联网，
            # 复用异常里已体检好的 project_env；算不出来不许盖掉原始错误（线程里不能抛）
            repair_offer = None
            if getattr(exc, "code", "") == "missing_dependency" and getattr(exc, "module", ""):
                try:
                    repair_offer = deprepair.offer(
                        plan.project_root,
                        plan.script or "",
                        exc.module,
                        getattr(exc, "project_env", None),
                    )
                except Exception:  # noqa: BLE001
                    repair_offer = None
            deprepair.note_missing_dependency_of(
                plan.project_root, exc, script=plan.script or "", offer=repair_offer
            )
            module = getattr(exc, "module", "")
            if module:
                error["module"] = module
            if getattr(exc, "install_route", ""):
                error["install_route"] = exc.install_route
            project_env = getattr(exc, "project_env", None)
            if isinstance(project_env, dict):
                error["project_env"] = _public_project_env(project_env)
            missing_input = getattr(exc, "missing_input", None)
            if isinstance(missing_input, dict):
                result.missing_input = dict(missing_input)
            hint = getattr(exc, "workdir_hint", None)
            if isinstance(hint, dict):
                result.workdir_hint = dict(hint)
            result.input_facts = getattr(exc, "input_facts", None)
            result.error = error
            # 脚本自己炸 / 缺依赖是「执行」那一步坏的；起不来（解释器 / 沙盒）是「起会话」坏的
            tr.fail("execute" if getattr(exc, "traceback_text", None) else "spawn", error["code"])
            self._finish(entry, STATUS_ERROR)
            return
        except Exception as exc:  # noqa: BLE001 — 线程里不许静默死掉，如实记
            if result.status in TERMINAL:
                return
            result.error = {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
            tr.fail("spawn", "internal_error")
            self._finish(entry, STATUS_ERROR)
            return
        if result.status in TERMINAL:
            # 本计划是共享会话的等待者、已被取消放手（T09）：会话不是我们的，回执 / 捕获都不归这份计划
            return
        plan = entry.plan  # before_retry 可能就地换过计划（吸收派生授权）：回执挂换后的那份
        tr.mark("execute", created=bool(created))
        result.created_runtime = bool(created)
        result.input_facts = getattr(worker, "last_input_facts", None)
        rcpt = receipt.from_worker(
            worker,
            resp,
            control_plane=pool.control_plane_of(worker),
            grant=plan.grant,
            binding=plan.binding,
        )
        result.receipt = rcpt
        result.captured = _captured_of(worker, resp)
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
                # A different target's explicit rerun may have replaced this
                # worker while build was running. Ownership is instance-scoped.
                closed = pool.force_cancel(
                    plan.script,
                    plan.project_root,
                    expected_worker=worker,
                    **({"run": plan.run} if plan.run is not None else {}),
                )
                if closed:
                    note = "build 期间取消：本计划新起的会话已关闭；脚本已经产生的外部副作用不撤销"
                elif pool.is_fenced(worker):
                    note = "build 期间取消：会话的强制关闭尚未确认，已围栏、下次使用前会重试；脚本可能仍在运行，已产生的外部副作用不撤销"
                else:
                    note = "build 期间取消：本计划的会话已被替换，未关闭后来的会话；脚本已经产生的外部副作用不撤销"

            else:
                note = "build 期间取消：会话属于别的消费者，未关闭；本计划不再等它"
            tr.cancel("receipt")
            self._finish(entry, STATUS_CANCELLED, note=note)
            return
        if self._binding_mismatch(entry, rcpt, executed=True):
            return
        if self._stem_missing(entry, worker, _captured_stems(resp)):
            return
        # build 成功、图也捕获到了，但沙盒里 glob 回空而脚本目录里有文件（图可能缺数据，#1254）
        entry.result.workdir_hint = pool.workdir_hint_of_build(worker)
        self._finish(entry, STATUS_READY)

    def _stem_missing(self, entry: _Entry, worker, known) -> bool:
        """请求的面板没被捕获（一张图都没有 / 捕获到的里没有它）→ 与渲染入口**同一个 code** 的 `error`
        （`no_figures_captured` / `no_figures_captured_silent` / `unknown_stem`，`pool.missing_stem_error` 是
        同一条换码路）。`ready` 是「这张面板可以编辑」的证据，不能在渲染入口会报错的时候给（PATH-B1）。
        回执照样留着：脚本确实跑过，它读了什么、在哪个解释器里跑的都是事实。"""
        if entry.plan.target == TARGET_SCRIPT and (
            not isinstance(known, (list, tuple)) or len(known) > 0
        ):
            return False  # 脚本目标没有「要找的那一张」：捕获到至少一张就够，说不出也不判
        err = pool.missing_stem_error(worker, entry.plan.stem, known)
        if err is None:
            return False
        entry.result.trace.fail("execute", err.code)
        entry.result.error = {"code": err.code, "message": str(err)}
        hint = getattr(err, "workdir_hint", None)
        if isinstance(hint, dict):
            entry.result.workdir_hint = dict(hint)
        self._finish(entry, STATUS_ERROR, note="脚本跑完了，但这张面板没有被捕获")
        return True

    @staticmethod
    def _stale_reason(plan: PreparationPlan, *, source: bool = True) -> tuple[str, dict] | None:
        """起会话之前核对授权、解释器、数据绑定与改指表代次；第一条不一致的就是理由。

        `source`：还核对脚本的源码修订（`script_revision`）——凡是**要执行脚本**的认领与起跑都核；依赖准备的认领
        不核（它不执行脚本，且用自己的影响摘要 `impact_digest` 验证要装什么，与依赖无关的脚本改动不撤销它）。"""
        root = plan.project_root
        if workdir.grant_for(root) != plan.grant:
            return STALE_GRANT, {}
        if (
            plan.input_remap_generation is not None
            and inputremap.generation(root) != plan.input_remap_generation
        ):
            return STALE_DATA_BINDING, {}
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
            # 路径没变、环境换了一代（删了重建）：计划确认的是上一代（ADR 0114 §四）
            planned = (plan.environment or {}).get("generation")
            if planned and projectenv.environment_generation(plan.interpreter) != planned:
                return STALE_ENVIRONMENT, {}
            # 叠加环境（ROOT）换了 / 清了：同一个解释器带不带 ROOT 不是同一个环境（与起会话的 `_overlay_for` 同一判据）
            if rootenv.key_of(getattr(plan, "environment_overlay", None)) != rootenv.key_of(
                projectenv.remembered_overlay(root, plan.interpreter)
            ):
                return STALE_ENVIRONMENT, {}
            # Conda 环境原地变了（conda install / 改 activate.d / env config vars）：代没变，激活后的环境变了
            if plan.environment_state and (
                launcher.context_key(plan.interpreter) != plan.environment_state
            ):
                return STALE_ENVIRONMENT, {}
        if (
            source
            and plan.script_revision is not None
            and plan.script_revision != _script_revision(root, plan.script)
        ):
            return STALE_SOURCE, {}
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
        spec = getattr(existing, "spec", None)
        if getattr(spec, "profile", execspec.PROFILE_SAFE) != plan.execution:
            # 池里的 worker 恒是 safe（吞 savefig、有守卫）：选了 native 的计划不得拿它当「现有 runtime」交差
            return None
        if getattr(existing, "entry", getattr(spec, "entry", None)) != plan.entry:
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
            if entry.result.status in TERMINAL:
                # 已经由取消落了终局（共享会话的等待者不再等，T09）：执行线程迟到的结局不改写它
                return
            if status != STATUS_CANCELLED and entry.cancel.is_set():
                # 取消在最后一次检查之后、提交终局之前被接受（`cancel()` 已回 accepted、已杀自有 worker）：
                # 接受即终局——**所有**终局提交（ready / error / needs_input）都经这一个入口，在同一把锁内复查，
                # 一律让位给 cancelled；完成回调不会对死会话注册 / 物化，也不会把用户的 Stop 落成 failed / awaiting
                status = STATUS_CANCELLED
                note = (
                    "取消：在完成前被接受；本计划新起的会话已关闭，脚本已经产生的外部副作用不撤销"
                )
                entry.result.error = None
                entry.result.required_input = None
                entry.result.missing_input = None
                entry.result.workdir_hint = None
                entry.result.trace.cancel("receipt")
            entry.result.finished_at = time.time()
            if note:
                entry.result.note = note
            entry.result.status = status  # 终局 status 最后写
        _record_terminal(entry.plan, entry.result)

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
            acquired, owned = entry.acquired, entry.owned
        # pending 且线程还没起：当场收工（起了线程由线程自己在接受时刻 ① 收）
        if status == STATUS_PENDING and entry.thread is None:
            self._finish(entry, STATUS_CANCELLED, note="在起会话之前取消，没有执行任何脚本")
        elif acquired and owned is not None:
            # 取消接受时刻 ③（T09）：会话是本计划新建的、还在 build（可能正跑一段很长的计算，或停在一个 input()
            # 上）——当场关掉**这一条**（`expected_worker`：池里那个键此刻若已换成别人的会话，一根手指都不碰），
            # build 随即以 WorkerError 返回，执行线程按取消收工
            if not pool.force_cancel(
                entry.plan.script,
                entry.plan.project_root,
                expected_worker=owned,
                **({"run": entry.plan.run} if entry.plan.run is not None else {}),
            ):
                # 已被 watcher / invalidate 摘出池子：池侧只会优雅关闭（排在 build 后面），这里只杀自己的那条；
                # 桌面拥有的 native 会话不在池里：取消 = 结束它的进程组（P07）
                _kill_owned_worker(owned)
        elif acquired:
            # 共享会话的等待者：会话属于别的消费者（编辑那条渲染、另一份计划）——不关它，只是本计划不再等；
            # 执行线程之后回来时发现自己已经终局，什么都不写
            self._finish(
                entry,
                STATUS_CANCELLED,
                note="取消：会话属于别的消费者，未关闭；本计划不再等它",
            )
        return {"accepted": True, "reason": ""}

    def cancel_project(
        self, project_id: str, script: str | None = None, keep: str | None = None
    ) -> int:
        """取消这个项目名下所有还没终局的计划(执行线程据此在下一个取消接受时刻收工,新建的会话当场关掉)。

        * 项目关闭:只给 `project_id`;
        * 同一脚本重新运行:给 `script` 与 `keep`(新计划自己)——只有**比 keep 更早登记**的同脚本计划让位,更新的(另一个窗口刚发起的重跑)不取消。
        返回接受了取消的数量。别的项目 / 别的脚本的计划不动。

        先通知监听者（准备会话据此给范围内的会话落「已取消」标记，ADR 0127）：safe 尝试刚终局、回退还没接上的
        窗口里没有「未终局的计划」可取消，只有这个标记能拦住随后的 native 回退。范围与下面取消的计划一致。"""
        for listener in list(self.project_cancel_listeners):
            try:
                listener(project_id, script, keep)
            except Exception:  # noqa: BLE001 — 监听者出错不能挡住关项目
                LOG.exception("项目取消监听者失败")
        with self._lock:
            order = list(self._entries)  # 登记顺序 = 计划的先后
            if keep is not None and keep not in order:
                return 0
            older = set(order[: order.index(keep)]) if keep is not None else set(order)
            mine = [
                pid
                for pid, e in self._entries.items()
                if pid in older
                and e.plan.project_id == project_id
                and e.result.status not in TERMINAL
                and (script is None or e.plan.script == script)
            ]
        return sum(1 for pid in mine if self.cancel(pid, project_id).get("accepted"))

    def is_cancelled(self, plan_id: str) -> bool:
        """这份计划是不是已被取消（含被更新的同脚本计划取代、项目关闭）。取消是正向信息：没登记过的计划（直接调 runner 的
        用例）不算已取消。"""
        entry = self._entry(plan_id)
        return entry is not None and entry.cancel.is_set()

    def older_than(self, keep: str) -> frozenset[str]:
        """登记得比 `keep` 更早的计划号（登记顺序 = 计划的先后）；`keep` 未登记 = 空。`cancel_project(keep=)` 取消的就是这一批。"""
        with self._lock:
            order = list(self._entries)
        return frozenset(order[: order.index(keep)]) if keep in order else frozenset()

    def superseded(
        self, plan_id: str, *, ignoring: frozenset[str] | set[str] = frozenset()
    ) -> bool:
        """同项目同脚本里有没有登记得比 `plan_id` 更晚的**运行计划**（登记顺序 = 计划的先后）：有 = 这份计划已被更新的重跑取代。
        只数脚本目标（会话里的运行尝试）：旧端点按素材做准备（`TARGET_ASSET`）不是替代运行，不算取代。
        `ignoring`：不计入的计划号（调用方自己刚登记的回退计划）。`plan_id` 未登记按「没被取代」。"""
        with self._lock:
            order = list(self._entries)
            if plan_id not in order:
                return False
            mine = self._entries[plan_id].plan
            return any(
                pid not in ignoring
                and self._entries[pid].plan.target == TARGET_SCRIPT
                and self._entries[pid].plan.project_id == mine.project_id
                and self._entries[pid].plan.script == mine.script
                for pid in order[order.index(plan_id) + 1 :]
            )

    def launch_blocked(self, plan_id: str) -> bool:
        """native 起进程闸门的判定（`reserve` / `begin_start` / `check_start` 共用）：计划已被取消，**或**同项目同脚本里已有登记得
        更晚的脚本目标计划。后者与 `register` 同一把锁里读登记表：更新的重跑「登记了、还没轮到自己的 `cancel_project(keep=)`」
        的窗口里，`entry.cancel` 还没置位，旧计划不能因此起进程；闸门放行之后才登记的更新重跑，则必然走
        `cancel_project(keep=)` 的真终止路径（Codex #1028 P1：启动认领与被取代判定同序）。"""
        with self._lock:
            entry = self._entries.get(plan_id)
            if entry is None:
                return False
            if entry.cancel.is_set():
                return True
            mine = entry.plan
            after = False
            for pid, e in self._entries.items():
                if pid == plan_id:
                    after = True
                    continue
                if (
                    after
                    and e.plan.target == TARGET_SCRIPT
                    and e.plan.project_id == mine.project_id
                    and e.plan.script == mine.script
                ):
                    return True
            return False

    def cancel_plan(self, plan_id: str) -> bool:
        """按计划号取消(界面只拿到 native 会话的 `launch_id` 时用:它就是计划号)。没有这个计划 / 已终局回 False。"""
        entry = self._entry(plan_id)
        if entry is None:
            return False
        return bool(self.cancel(plan_id, entry.plan.project_id).get("accepted"))

    def note_owner(self, plan_id: str, worker, created: bool) -> None:
        """runner 取到会话那一刻报一次（每次 take 一次，含缺包后换环境的第二次）：`created` 来自 `pool.acquire`
        的池锁，不是快照猜的。取消已经在等（来得比取到会话还早）：这条是本计划新建的 → 当场关掉它，不让它开跑；
        是别人的 → 不碰，本计划不再等它。"""
        entry = self._entry(plan_id)
        if entry is None:
            return
        with entry.lock:
            entry.acquired = True
            entry.owned = worker if created else None
            waiting = entry.cancel.is_set() and entry.result.status not in TERMINAL
        if not waiting:
            return
        if created:
            if not pool.force_cancel(
                entry.plan.script,
                entry.plan.project_root,
                expected_worker=worker,
                **({"run": entry.plan.run} if entry.plan.run is not None else {}),
            ):
                _kill_owned_worker(worker)  # 同上：已离池就只杀这一条
        else:
            self._finish(
                entry,
                STATUS_CANCELLED,
                note="取消：会话属于别的消费者，未关闭；本计划不再等它",
            )

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


exectrace.register_vocabulary(
    "outcome", (STATUS_READY, STATUS_ERROR, STATUS_CANCELLED, STATUS_NEEDS_INPUT, STATUS_STATIC)
)
_PREP_TRACE_END = {
    STATUS_READY: "ok",
    STATUS_NEEDS_INPUT: "ok",
    STATUS_STATIC: "ok",
    STATUS_ERROR: "failed",
    STATUS_CANCELLED: "cancelled",
}


def _trace_begin(plan, result: PreparationResult) -> None:
    """执行线程开头开 Trace。采集代码永不让准备失败：计划缺字段（替身 / 旧调用方）就不开。"""
    try:
        result.exec_trace = exectrace.begin(
            "preparation",
            task_ref=getattr(plan, "plan_id", None),
            scope=exectrace.scope_for(getattr(plan, "project_id", None)),
        )
        result.exec_span = result.exec_trace.start_span(
            "script_run", "process", attributes={"execution": getattr(plan, "execution", None)}
        )
    except Exception:  # noqa: BLE001
        result.exec_trace, result.exec_span = exectrace.NullTrace(), None


def _trace_terminal(result: PreparationResult) -> None:
    """执行追踪收尾：阶段事实只读映射（不读 facts、不造区间）→ 执行区间按真实终局结束 → 冻结。永不抛。"""
    try:
        xt = result.exec_trace
        status = _PREP_TRACE_END.get(result.status, "unknown")
        code = (result.error or {}).get("code") if status == "failed" else None
        exectrace.observe_stage_trace(xt, result.trace.to_payload(), parent=result.exec_span)
        xt.end_span(result.exec_span, status, error_code=code)
        xt.finish(status, error_code=code, attributes={"outcome": result.status})
    except Exception:  # noqa: BLE001
        pass


def _record_terminal(plan: PreparationPlan, result: PreparationResult) -> None:
    """终局那一刻冻结一份诊断快照（T04）。只收这一次尝试自己的事实；记录失败不影响准备本身。"""
    _trace_terminal(result)
    if result.status not in (STATUS_READY, STATUS_ERROR, STATUS_CANCELLED, STATUS_NEEDS_INPUT):
        return
    try:
        taskdiag.STORE.record(
            plan.project_id,
            taskdiag.KIND_PREPARATION,
            plan.plan_id,
            diagnostic_projection(plan, result),
            outcome=result.status,
            failed=result.status == STATUS_ERROR,
            subject={("script", plan.script)} if plan.script else {("asset", plan.asset_id)},
        )
    except Exception:  # noqa: BLE001 —— 诊断登记不是准备的一部分
        LOG.debug("准备诊断快照登记失败", exc_info=True)


def _elapsed_ms(started_at, finished_at) -> int | None:
    if (
        isinstance(started_at, (int, float))
        and isinstance(finished_at, (int, float))
        and finished_at >= started_at
    ):
        return round((finished_at - started_at) * 1000)
    return None


def _attach_local_detail(error: dict, exc: BaseException, *, sensitive: bool) -> None:
    """把旧试运行出口（`probe._error_from_worker`）早就保留的**本地**恢复信息接到准备错误上：`params`（参数解析类别 /
    个数、input 的提示与理由）与 `traceback`（保尾部，过长标 `traceback_truncated`）。字段由错误转换合同
    `pool.recovery_params` / `traceback_tail` 给出，与渲染端点、探测同一份。

    只走本地报告（`result.error` → `/api/engine/preparation*`）：`diagnostic_projection` 逐字段挑，不读这两项，
    共享诊断与遥测不会带出。敏感运行（argv 含口令 / token）worker 已把脚本输出换成一句占位——这里**再**不带
    traceback，防止上游任何一环漏了也不会落进报告；个数与类别不含值，照常保留。"""
    params = pool.recovery_params(exc)
    if params:
        error["params"] = params
    if sensitive:
        return
    tb, truncated = pool.traceback_tail(getattr(exc, "traceback_text", ""))
    if tb:
        error["traceback"] = tb
        if truncated:
            error["traceback_truncated"] = True


def diagnostic_projection(plan: PreparationPlan, result: PreparationResult) -> dict:
    """这一次尝试的**白名单**投影（T04）。逐字段挑，不读 `plan.to_payload()` / `result.to_payload()`。

    环境、依赖、运行配置都是**计划那一刻**的事实（计划不可变），不是此刻再去问的；
    不进来的：脚本路径与入口名、argv 原文（只有个数与本机不透明引用）、解释器与项目路径、`error.message`
    （脚本自己的异常文字）、`note`、`required_input` 的内容、回执的输入文件表。"""
    env = plan.environment or {}
    receipt_ = result.receipt
    rt = (receipt_.runtime if receipt_ is not None else None) or {}
    err = result.error or {}
    dep = plan.dependency_preparation or {}
    return taskdiag.clean(
        {
            "snapshot_version": taskdiag.SNAPSHOT_VERSION,
            "kind": taskdiag.KIND_PREPARATION,
            "attempt_id": taskdiag.ident(plan.plan_id),
            "outcome": taskdiag.closed(result.status, STATUSES),
            "target": {
                "category": taskdiag.closed(plan.target, (TARGET_ASSET, TARGET_SCRIPT)),
                "has_script": plan.script is not None,
                "has_entry": plan.entry is not None,
                "has_static_source": plan.static_source is not None,
            },
            "config": {
                "argv_count": len(plan.run.argv) if plan.run is not None else 0,
                "run_config": taskdiag.ident(plan.run.config_id) if plan.run is not None else None,
                "workdir_mode": taskdiag.closed(
                    (plan.workdir_decision or {}).get("mode"), workdir.MODES
                ),
                "dependency_intents": len(plan.dependency_intents),
                "dependency_conflicts": len(plan.dependency_conflicts),
                "dependency_preparation": bool(plan.dependency_preparation),
                "dependency_blocked": taskdiag.flag(bool(dep.get("blocked"))) if dep else None,
            },
            "environment_at_plan": {
                "source": taskdiag.closed(env.get("source"), pool.SOURCE_LABELS),
                "automatic": taskdiag.flag(env.get("automatic")),
                "python_version": taskdiag.version(env.get("python_version")),
                "matplotlib_version": taskdiag.version(env.get("matplotlib_version")),
                "support": taskdiag.code(env.get("support")),
                "error_code": taskdiag.code((env.get("error") or {}).get("code")),
            },
            "stages": taskdiag.stages(result.trace.to_payload(), tracemod.PHASES),
            "input": inputbroker.facts_projection(result.input_facts),
            "execution": {
                "created_runtime": taskdiag.flag(result.created_runtime),
                "reused_runtime": result.existing_runtime is not None,
                "control_plane": taskdiag.closed(
                    (receipt_.control_plane if receipt_ is not None else None),
                    ("workerd", "python_pool"),
                ),
                "python_source": taskdiag.closed(
                    (receipt_.python_source if receipt_ is not None else None), pool.SOURCE_LABELS
                ),
                "python_version": taskdiag.version(rt.get("python_version")),
                "completeness": taskdiag.closed(
                    (receipt_.completeness if receipt_ is not None else None), receipt.COMPLETENESS
                ),
                "runtime_rejected": taskdiag.code(
                    receipt_.runtime_rejected if receipt_ is not None else None
                ),
                "captured_count": len((result.captured or {}).get("stems") or [])
                if result.captured
                else None,
            },
            "route": (
                {
                    "execution": taskdiag.closed(plan.route.get("execution"), runroute.EXECUTIONS),
                    "reason": taskdiag.closed(plan.route.get("reason"), runroute.REASONS),
                    "confidence": taskdiag.closed(
                        plan.route.get("confidence"), runroute.CONFIDENCES
                    ),
                    "rules": [
                        r
                        for r in plan.route.get("rules") or []
                        if taskdiag.closed(r, runroute.RULES)
                    ],
                    "dynamic_paths": taskdiag.count(plan.route.get("dynamic_paths")),
                    "elapsed_ms": taskdiag.number(plan.route.get("elapsed_ms")),
                }
                if plan.route
                else None
            ),
            "retry": (
                {
                    "of": taskdiag.ident(plan.retry_of),
                    "cause": taskdiag.code(plan.retry_cause),
                    "changed": [
                        c
                        for c in plan.retry_changed
                        if taskdiag.closed(c, execpolicy.CHANGED_DIMENSIONS)
                    ],
                    "execution": taskdiag.closed(plan.execution, execspec.PROFILES),
                }
                if plan.retry_of
                else None
            ),
            "error": (
                {
                    "code": taskdiag.code(err.get("code")),
                    "reason": taskdiag.closed(err.get("reason"), STALE_REASONS),
                    "executed": taskdiag.flag(err.get("executed")),
                }
                if err
                else None
            ),
            "required_input": (
                {"code": taskdiag.code(result.required_input.get("code"))}
                if result.required_input
                else None
            ),
            "cancel_requested": result.cancel_requested_at is not None,
            "timing": {
                "started_at": taskdiag.number(result.started_at),
                "finished_at": taskdiag.number(result.finished_at),
                # 与 script_run 条目同名同单位，`recent_runs` 才能一视同仁地读
                "elapsed_ms": _elapsed_ms(result.started_at, result.finished_at),
            },
        }
    )


def stale_reason(plan: PreparationPlan, *, source: bool = True) -> tuple[str, dict] | None:
    """计划此刻还成不成立（授权 / 解释器决策 / 数据绑定 / 脚本源码修订各比一次）；`None` = 成立。准备会话在认领动作之前
    用它核「检查那一刻的世界」还在不在——与执行线程起会话之前是同一份判据。`source=False`：不执行脚本的认领
    （依赖准备）不核源码修订。"""
    return PreparationService._stale_reason(plan, source=source)


#: 进程内唯一登记表（app.py 用它）。
SERVICE = PreparationService()
