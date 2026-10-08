"""受控依赖修复：把缺的包装进一个**明确的**环境（Session 7B）。

ADR 0018（Session 7）解决的是「项目自己有一个能跑通的 `.venv`」。真实用户里
还有一半不是这样：项目有 `.venv` 但它也缺这个包、或者项目根本没有 venv。那时
Tavotto 给出的仍然是一句 `ModuleNotFoundError` ——用户得先知道 pip 是什么。

本模块给这类局面一条产品化的路：

    missing_dependency
        ↓  depresolve：import 名 → 可信的 distribution（解析不到就停在这儿）
    repair plan（绑定项目 + 环境指纹 + 需求 + 有效期）
        ↓  用户明确点击（改用户环境时文案说清「这会改你的环境」）
    pip install（wheels 优先、shell=False、不 --upgrade）
        ↓
    验证三层：import 缺的那个包 / import matplotlib / **真起一次 worker**
        ↓
    作废旧 worker → 重跑脚本 → Figure 出来

**安装目标只有两种，内置 runtime 不在其中**：

    project .venv     改的是**用户的**环境 → 必须明确确认，且没有完整 rollback
    Tavotto managed   改的是**我们的**东西 → 可删可重建（`engine/managedenv.py`）

    bundled runtime   **永远不是安装目标**。它是「重装就能修」这条退路的
                      前提，被 pip resolver 逐渐污染之后，用户之间就不再有
                      同一个基线。缺包时它只是**触发器**。

纯标准库（Flask 父进程 import 链上）。设计与取舍见
`docs/adr/0019-controlled-dependency-repair.md`。
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from . import (
    depplan,
    depresolve,
    envlease,
    execspec,
    importscan,
    logsafe,
    managedenv,
    pool,
    privatepython,
    projectenv,
    runcodes,
    runtime,
    taskdiag,
    userenvs,
    workdir,
)

LOG = logging.getLogger("tavotto.deprepair")

# ---------------------------------------------------------------------------
# 稳定错误码（协议契约：code 不许改名，文案随便改）
# ---------------------------------------------------------------------------
ERROR_UNRESOLVED = "dependency_unresolved"
#: 没有计划 / 计划不属于这里 / 安装目标不合法——**只**留给这三种「没有用户意图」
#: 的情形，那时「请重新开始」才是对的建议。以前它还盖着「这一轮已经试过」与
#: 「环境里已经有了」两种原因，前端那句「安装未获确认，请重新开始」对它们是假话
#: （#466）——判据对了，它说的话不对，而判据一旦对，人更会信那句错的。
ERROR_NOT_ALLOWED = "dependency_install_not_allowed"
#: 同一环境上的同一需求这一轮已经装成功过：再装一遍改不了任何东西。
ERROR_ALREADY_ATTEMPTED = "dependency_already_attempted"
#: 目标环境里已经 import 得到它了——渲染报缺，说明渲染用的不是这个环境。
ERROR_ALREADY_PRESENT = "dependency_already_present"
#: 全局显式解释器（`TAVOTTO_WORKER_PYTHON` / 设置里指定的）正在生效：项目级的
#: 环境决策全部轮不到（`pool.explicit_worker_python`，#465），装进任何目标都不会被用。
ERROR_INTERPRETER_PINNED = "dependency_interpreter_pinned"
ERROR_CANCELLED = "dependency_install_cancelled"
ERROR_FAILED = "dependency_install_failed"
ERROR_TIMEOUT = "dependency_install_timeout"
ERROR_REQUIRES_BUILD = "dependency_requires_build"
ERROR_NOT_FOUND = "dependency_not_found"
ERROR_NETWORK = "dependency_network_unavailable"
ERROR_CONFLICT = "dependency_conflict"
ERROR_IMPORT_STILL_FAILED = "dependency_import_still_failed"
ERROR_SELFTEST_FAILED = "dependency_worker_selftest_failed"
ERROR_REQUIREMENT_INVALID = "package_requirement_invalid"
ERROR_PIP_UNAVAILABLE = "pip_unavailable"
ERROR_MANAGED_UNAVAILABLE = "managed_env_unavailable"
ERROR_MANAGED_CREATE_FAILED = "managed_env_create_failed"
ERROR_MANAGED_BROKEN = "managed_env_broken"
#: 受管环境的 manifest 写不下去（卷满 / 只读）：登记 / 切 active 没落盘，事务不宣称成功
ERROR_MANAGED_WRITE_FAILED = "managed_env_write_failed"
ERROR_PLAN_STALE = "repair_plan_stale"
ERROR_BUSY = "dependency_install_busy"
#: 环境被一条活跃的 `tavotto run` 会话占着（ADR 0021 §6）。**与 `ERROR_BUSY`
#: 分开是必须的**：另一次安装等几十秒就好，而这一条要用户自己去结束那个
#: 脚本——两件事的下一步动作完全不同，混成一个码就只能给一句含糊的「忙」。
ERROR_IN_USE_BY_NATIVE = runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION
ERROR_ROUNDS_EXHAUSTED = "dependency_repair_rounds_exhausted"
#: 包管理（设置 → 包管理，ADR 0038）专有的几条。它们与上面那批共用同一个
#: 漏斗（`app._repair_error`）与同一张文案表（`errors:engine.repairError.*`）。
ERROR_PACKAGE_PROTECTED = "package_protected"
ERROR_PACKAGE_NOT_INSTALLED = "package_not_installed"
ERROR_PACKAGE_ENV_MISSING = "package_env_missing"
ERROR_PACKAGE_DISK_LOW = "package_disk_low"
ERROR_PACKAGE_OP_INVALID = "package_op_invalid"
ERROR_PACKAGE_STILL_INSTALLED = "package_still_installed"
ERROR_PACKAGE_NOT_FOUND_AFTER = "package_not_found_after_install"
#: 包查找（设置 -> 包管理的搜索，ADR 0038 的 2026-09-07 修订）的**闭集四档**。
#: 分成四条而不是一条「查不到」，是因为四种下一步动作完全不同：换个名字 /
#: 检查网络与镜像源 / 稍后重试 / 直接输入包名安装。
ERROR_LOOKUP_NOT_FOUND = "package_lookup_not_found"
ERROR_LOOKUP_OFFLINE = "package_lookup_offline"
ERROR_LOOKUP_TIMEOUT = "package_lookup_timeout"
ERROR_LOOKUP_FAILED = "package_lookup_failed"
#: 端点按这张表定 HTTP 状态（`app.api_packages_lookup`）。**闭集**：查找只会
#: 用这四个码，加上语法不合法的 `package_requirement_invalid`。
LOOKUP_ERROR_CODES = (
    ERROR_LOOKUP_NOT_FOUND,
    ERROR_LOOKUP_OFFLINE,
    ERROR_LOOKUP_TIMEOUT,
    ERROR_LOOKUP_FAILED,
)

# ---------------------------------------------------------------------------
# 目标环境
# ---------------------------------------------------------------------------
TARGET_PROJECT_VENV = "project_venv"
TARGET_MANAGED = "tavotto_managed"
#: 「这台机器上已有的解释器里已经装着它」（ADR 0044）——**不是安装目标**：
#: 采用它一个字节都不装，走的是项目环境 PATCH（`app._set_project_environment`），
#: 所以刻意不进 `TARGETS`：`create_plan` 对它一律 `dependency_install_not_allowed`。
TARGET_SYSTEM = "system_interpreter"
TARGETS = (TARGET_PROJECT_VENV, TARGET_MANAGED)

#: 同一个 (项目, 脚本) 上最多修几轮。**不是**「自动装三次」——每一轮都要用户
#: 明确点一次；这个上限挡的是「装完还缺、再装还缺」把用户拖进无尽循环。
MAX_DEPENDENCY_REPAIR_ROUNDS = 3

#: 计划的有效期。够用户读完确认文案，短到不至于让一条旧计划在环境变了之后
#: 还能被执行（真正防 TOCTOU 的是环境指纹，这只是第二道）。
PLAN_TTL_S = 600.0

#: pip 的超时。装一个带 wheel 的科研包通常几十秒；网络慢时给足。
INSTALL_TIMEOUT_S = 900
PIP_PROBE_TIMEOUT_S = 60
SELFTEST_TIMEOUT_S = 180

#: 进度状态机。前端按它换文案，**不解析日志**。
STATE_PREPARING = "preparing"
#: 私有 Python 供应中（U05）：`download` 段带 stage / done_bytes / total_bytes。
STATE_DOWNLOADING_PYTHON = "downloading_python"
STATE_CREATING_ENV = "creating_env"
STATE_INSTALLING = "installing"
STATE_VERIFYING = "verifying"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"

_lock = threading.RLock()
_plans: dict[str, "RepairPlan"] = {}
_progress: dict[str, dict] = {}
_cancels: dict[str, threading.Event] = {}
#: (项目指纹, 脚本) → 已经修过几轮。
_rounds: dict[tuple[str, str], int] = {}
#: (项目指纹, 环境 key, 需求串) → 这一轮已经试过。同一个环境 + 同一个需求
#: 一轮只试一次。
#:
#: **key 里必须带项目指纹**（Codex 评审 P2）：`reset_state(project)` 承诺
#: 「丢弃计划 / 轮次 / 已试过」，而只按环境 key 存的话它清不掉这一项——
#: 受管环境重建之后解释器路径一模一样，`create_plan` 会一直以「这一轮已经
#: 试过了」拒绝那个依赖，直到整个应用重启。
_attempted: set[tuple[str, str, str]] = set()
#: 基础解释器探测结果的进程内缓存（`None` = 还没探过）。探一次要起好几个
#: 子进程，而问它的地方在**渲染出错**那条路上。
_base_python: str | None = None
_base_python_known = False
#: 基础解释器缓存的世代：`reset_state()` 每次 +1。探测（后台线程或同步）起步时记下世代，写回时世代变了就
#: 丢弃——否则重置之后才结束的旧探测会把重置前的答案写回来（用例之间 / 用户改设置后重来，都会撞到）。
_base_epoch = 0


class RepairError(RuntimeError):
    """带稳定 code 的修复失败。app 层直接把 code 交给前端。"""

    def __init__(self, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class _Widened:
    """单包修复要新建一代时的联合求解结果（字段与 `JointRepairPlan` 里同名字段同义）。"""

    requirements: tuple[str, ...]
    constraints: tuple[str, ...]
    hashes: dict
    needed_imports: tuple[str, ...]
    record: tuple[dict, ...]
    groups: tuple[str, ...]
    inputs_digest: str


@dataclasses.dataclass(frozen=True)
class PipInputs:
    """交给 pip 的全部输入——**披露（`impact`）与执行（`_GenerationJob` / 原地 pip）读的是同一份**。

    以前 `impact` 与执行各自从计划字段拼一遍，约束漏进披露三次（r4217… / r4218254708 一族）。现在每种计划
    只有 `pip_inputs` 这一处决定「pip 会收到什么」：影响摘要从它派生，执行端的作业也从它构造；
    再加一个输入（索引来源、额外参数……）只能加在这里，两边同时看见。"""

    requirements: tuple[str, ...]
    constraints: tuple[str, ...]
    hashes: dict
    require_hashes: bool
    adapter: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class RepairPlan:
    """一次修复的完整描述——**执行端只认它，不读请求体里的任何别的字段**。

    这是防 TOCTOU 的机制面：用户看到的是「把 lmfit 装进 项目 .venv」，点下去
    执行的必须是**那一件事**。如果执行时按新请求里的 python / distribution 走，
    一个构造出来的请求就能把「装 lmfit 到项目环境」换成「装别的东西到别处」。
    """

    plan_id: str
    project: str
    project_id: str
    script: str
    target_kind: str
    python: str  # 受管环境还没建时为空
    env_fingerprint: str
    requirement: depresolve.DependencyRequirement
    modifies_user_environment: bool
    creates_environment: bool
    network_required: bool
    created_at: float
    expires_at: float
    #: 这次授权是否**包含先下载私有 Python**（U05，ADR 0063）：`privatepython.offer_payload()`
    #: 的载荷（版本 / 目标 / `download_bytes`），没有基础解释器又提供这条路时才有值。
    #: 界面必须把 `download_bytes` 说出口；执行端据它决定要不要在建 venv 之前先供应。
    private_python: dict | None = None
    #: 受管目标要**新建一代**时，这一代装的是脚本开跑所需的全部第三方依赖（ADR 0061 §五 2026-09-30 修订）：
    #: 联合计划的求解结果，而不只是缺的那一个包。原地往已有一代加包 / 项目 venv 目标为 None（行为不变）。
    widened: "_Widened | None" = None
    #: 形成计划那一刻项目级解释器决定的签名（`selection_signature`）：执行前再比，变了 = 期间用户采用了别的环境
    selection: tuple = ()

    @property
    def pip_inputs(self) -> PipInputs:
        """pip 会收到的全部输入（单包 = 只有那一条 + 联合求解出的约束；披露与执行共用，见 `PipInputs`）。"""
        wide = self.widened if self.target_kind == TARGET_MANAGED else None
        return PipInputs(
            requirements=self.requirements,
            constraints=tuple(wide.constraints) if wide is not None else (),
            hashes={},
            require_hashes=False,
            adapter=tuple(depplan.ADAPTER_REQUIREMENTS)
            if self.target_kind == TARGET_MANAGED
            else (),
        )

    @property
    def impact(self) -> dict:
        """这份授权的实际影响（`impact_of`）；摘要见 `impact_digest`。输入集合来自 `pip_inputs`。"""
        wide = self.widened
        pi = self.pip_inputs
        return impact_of(
            target_kind=self.target_kind,
            requirements=pi.requirements,
            constraints=pi.constraints,
            require_hashes=pi.require_hashes,
            adapter=pi.adapter,
            groups=wide.groups if wide is not None else (),
            creates_environment=self.creates_environment,
            private_python=self.private_python,
            env_fingerprint=self.env_fingerprint,
        )

    @property
    def impact_digest(self) -> str:
        return impact_digest(self.impact)

    @property
    def requirements(self) -> tuple[str, ...]:
        """这份授权要装的全部需求（规范串）：单包 = 只有那一条；联合 = 整个 delta（不含 adapter）。"""
        if self.widened is not None:
            return self.widened.requirements
        return (self.requirement.requirement(),)

    def to_payload(self) -> dict:
        """交给前端的形态。**不出绝对路径**（项目内的出项目相对）。"""
        return {
            "requirements": list(self.requirements),
            "joint": self.widened is not None and len(self.widened.requirements) > 1,
            "plan_id": self.plan_id,
            "target_kind": self.target_kind,
            "python": projectenv.project_relative(self.project, self.python)
            or ("" if self.creates_environment else "…"),
            "creates_environment": self.creates_environment,
            "modifies_user_environment": self.modifies_user_environment,
            "network_required": self.network_required,
            "expires_at": int(self.expires_at),
            "private_python": dict(self.private_python) if self.private_python else None,
            "impact": self.impact,
            "impact_digest": self.impact_digest,
            **self.requirement.to_payload(),
        }


def _env_key(target_kind: str, python: str, project: str) -> str:
    """环境锁与「试过没有」的粒度——**一个环境一把锁，不是全局一把**。

    A 项目在装 lmfit 不该让 B 项目的健康 worker 停下来。受管环境还没建出来时
    用项目指纹当 key（那时还没有解释器路径，但目标环境已经确定）。
    """
    if python:
        return os.path.normcase(os.path.normpath(os.path.abspath(python)))
    return f"{target_kind}:{managedenv.project_fingerprint(project)}"


def _fingerprint_project_venv(python: str) -> str:
    """项目 venv 的身份指纹：解释器 + `pyvenv.cfg` 的 mtime/size。

    要回答的是「用户确认之后，这个环境被换过了吗」——venv 被删掉重建、被换成
    另一个 Python，指纹都会变。**不算整棵目录树的哈希**：那要走几万个文件，
    而这里在一次点击的响应路径上。
    """
    parts: list[str] = [os.path.normcase(os.path.abspath(python))]
    for path in (Path(python), Path(python).parent.parent / "pyvenv.cfg"):
        try:
            st = path.stat()
            parts.append(f"{int(st.st_mtime_ns)}:{st.st_size}")
        except OSError:
            parts.append("-")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _fingerprint_managed(project: str) -> str:
    python = managedenv.python_of(project)
    if not python:
        return "absent"
    data = managedenv.read_manifest(project) or {}
    active = managedenv.active_generation(project) or ""
    return f"{data.get('created_at', 0)}:{active}:{_fingerprint_project_venv(python)}"


def _fingerprint(target_kind: str, python: str, project: str) -> str:
    if target_kind == TARGET_MANAGED:
        return _fingerprint_managed(project)
    return _fingerprint_project_venv(python)


def rounds_used(project: str, script: str) -> int:
    with _lock:
        return _rounds.get((managedenv.project_fingerprint(project), script), 0)


def rounds_remaining(project: str, script: str) -> int:
    return max(0, MAX_DEPENDENCY_REPAIR_ROUNDS - rounds_used(project, script))


def _note_round(project: str, script: str) -> None:
    key = (managedenv.project_fingerprint(project), script)
    with _lock:
        _rounds[key] = _rounds.get(key, 0) + 1


def reset_state(project: str | Path | None = None) -> None:
    """丢弃计划 / 轮次 / 已试过（测试之间、用户手动重来时）。"""
    global _base_python, _base_python_known, _base_epoch
    with _lock:
        if project is None:
            _plans.clear()
            _joint_plans.clear()
            _running.clear()
            _jobs.clear()
            _active_jobs.clear()
            _joined.clear()
            _listeners.clear()
            _committed.clear()
            _gate_skipped.clear()
            _progress.clear()
            _cancels.clear()
            _rounds.clear()
            _attempted.clear()
            _base_python, _base_python_known = None, False
            _base_epoch += 1
            return
        pid = managedenv.project_fingerprint(project)
        for key in [k for k, p in _plans.items() if p.project_id == pid]:
            _plans.pop(key, None)
        for key in [k for k, p in _joint_plans.items() if p.project_id == pid]:
            _joint_plans.pop(key, None)
        for key in [k for k in _gate_skipped if k[0] == pid]:
            _gate_skipped.discard(key)
        for key in [k for k in _rounds if k[0] == pid]:
            _rounds.pop(key, None)
        for key in [k for k in _attempted if k[0] == pid]:
            _attempted.discard(key)


def _prune_plans() -> None:
    now = time.time()
    with _lock:
        for key in [k for k, p in _plans.items() if p.expires_at < now]:
            _plans.pop(key, None)
        for key in [k for k, p in _joint_plans.items() if p.expires_at < now]:
            _joint_plans.pop(key, None)
        for key in [k for k, t in _committed.items() if t < now - PLAN_TTL_S]:
            _committed.pop(key, None)


# ---------------------------------------------------------------------------
# 授权影响摘要（T06）
#
# 用户确认的不是「某个 plan_id」，而是**这次确认会造成的实际影响**：往哪个环境（含它的代）、装哪一个具体
# 集合、写入范围、要不要先下载私有 Python、出不出网、能不能回滚。摘要由 `impact_of` 一处算出，摘要的
# 摘要（`impact_digest`）就是授权的绑定物：
#
# * 进度、文案、计划 id、有效期、事实 digest 不在里面——它们变了不撤销授权；
# * 安装集合 / 约束 / hash 模式 / 目标类型 / 目标环境与代 / 写入范围 / 私有 Python 下载变了，摘要就变，
#   旧同意**不覆盖**新的范围（`ERROR_IMPACT_CHANGED`，零副作用）；
# * 新增一类影响要加 `IMPACT_VERSION`——旧版本摘要永远对不上，未知的新范围不会被旧同意继承。
# ---------------------------------------------------------------------------
IMPACT_VERSION = 1
#: 依赖安装结束时 `projectenv.remember` 的 trigger（两处写入点共用这一个字面量）
TRIGGER_DEPENDENCY_REPAIR = "dependency_repair"
SCOPE_MANAGED_GENERATION = "managed_generation"
SCOPE_PROJECT_VENV_IN_PLACE = "project_venv_in_place"
#: 失败后原环境的状态：受管环境换代是事务（失败的代不 active、上一代原样）；用户 venv 原地装只进不退
ROLLBACK_GENERATION_ATOMIC = "generation_atomic"
ROLLBACK_NONE = "none_partial_changes_possible"
#: 授权的失效码：用户看到的影响与此刻要执行的不是同一份
ERROR_IMPACT_CHANGED = "dependency_impact_changed"
#: 执行请求没有带用户确认的影响摘要（`/dependencies/prepare` 必填 `impact_digest`）
ERROR_IMPACT_REQUIRED = "dependency_impact_required"


def new_plan_id() -> str:
    """计划 / 作业 id（不透明）。带固定前缀：`secrets.token_urlsafe` 的首字符可能是 `-` / `_`，而任务诊断的
    引用（`taskdiag.ident`）要求首字符是字母数字——不带前缀时约每 32 个计划就有一个终局快照存不进去。"""
    return "dp-" + secrets.token_urlsafe(24)


def _opaque(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def _private_summary(private: dict | None) -> dict | None:
    """私有 Python 段的影响摘要：只有真要供应（`required`）才算影响，且只留身份 / 来源 / 字节数。"""
    if not private or not private.get("required", True):
        return None
    return {
        "id": str(private.get("id") or ""),
        "version": str(private.get("version") or ""),
        "origin": str(private.get("origin") or ""),
        "download_bytes": int(private.get("download_bytes") or 0),
    }


def impact_of(
    *,
    target_kind: str,
    requirements,
    constraints,
    require_hashes: bool,
    adapter,
    groups,
    creates_environment: bool,
    private_python: dict | None,
    env_fingerprint: str,
    scope_policy: str = "",
    drops=(),
    changes=(),
) -> dict:
    """一次依赖安装授权的实际影响（公开形态：没有机器路径，环境只给不透明引用）。

    `scope_policy="switch"`（D04）：换成本作用域——新一代只装本作用域的集合：账上别的作用域装进去的包不再
    在 active 的环境里（`drops`），保留的包里版本要变的（`changes`，别的作用域依赖的那个版本不在了）。
    这是**更大的影响**，所以单列、进摘要。"""
    in_place = target_kind == TARGET_PROJECT_VENV
    return {
        "impact_version": IMPACT_VERSION,
        "target_kind": target_kind,
        "scope": SCOPE_PROJECT_VENV_IN_PLACE if in_place else SCOPE_MANAGED_GENERATION,
        "installs": sorted(requirements),
        "constraints": sorted(constraints),
        "adapter": sorted(adapter),
        "require_hashes": bool(require_hashes),
        "groups": sorted(groups),
        "creates_environment": bool(creates_environment),
        "modifies_user_environment": in_place,
        "private_python": _private_summary(private_python),
        "network_required": True,
        "writes": ["user_environment"] if in_place else ["tavotto_managed_environment"],
        "rollback": ROLLBACK_NONE if in_place else ROLLBACK_GENERATION_ATOMIC,
        "scope_policy": scope_policy,
        "drops": sorted(drops),
        "changes": sorted(changes),
        # 目标环境与它的代：重建 / 换代 / 换成别的环境都换引用（只由指纹算出，不泄露路径）
        "environment_ref": _opaque(env_fingerprint),
    }


def impact_digest(impact: dict) -> str:
    return hashlib.sha256(
        json.dumps(impact, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]


def impact_delta(previous: dict | None, current: dict | None) -> dict:
    """已确认的影响 → 此刻的影响：差额（给「差异重计划」用）。只比较公开字段，不推断授权。"""
    before = set((previous or {}).get("installs") or ())
    after = set((current or {}).get("installs") or ())
    return {
        "added": sorted(after - before),
        "already_installed": sorted(before - after),
        "unchanged": sorted(before & after),
        "target_changed": bool(
            previous
            and current
            and (previous.get("target_kind"), previous.get("environment_ref"))
            != (current.get("target_kind"), current.get("environment_ref"))
        ),
        "scope_widened": bool(
            current
            and (
                (
                    current.get("modifies_user_environment")
                    and not (previous or {}).get("modifies_user_environment")
                )
                or (current.get("private_python") and not (previous or {}).get("private_python"))
            )
        ),
    }


def _managed_scope(root: str, kind: str) -> tuple[bool, dict | None, str]:
    """(要不要新建环境, 私有 Python 供应载荷, 计划绑定的解释器)——`create_joint_plan` 与跑前的门显示的
    影响摘要共用这一处，所以两边算出的摘要逐字相同。没有基础解释器又不提供私有 Python 时抛
    `managed_env_unavailable`（建不出计划，门也就没有可授权的东西）。"""
    if kind != TARGET_MANAGED:
        return False, None, ""
    managed_python = managedenv.python_of(root) or ""
    return not managed_python, _private_python_offer(), managed_python


def offer_impact(root: str, joint, kind: str, python: str, scope_policy: str = "") -> dict | None:
    """跑前的门 / 准备会话给用户看的影响摘要：与 `create_joint_plan` 绑定出的计划同一个函数、同一组输入。
    联合计划不可授权（blocked / 没缺的且不是干净机器）或建不出受管环境时回 None。"""
    try:
        creates, private, bound = _managed_scope(root, kind)
    except (RepairError, privatepython.ProvisionError):
        return None
    return impact_of(
        target_kind=kind,
        requirements=joint.requirements,
        constraints=joint.constraints,
        require_hashes=joint.require_hashes,
        adapter=joint.adapter,
        groups=tuple(joint.selection.get("selected_groups") or ()),
        creates_environment=creates,
        private_python=private,
        env_fingerprint=_fingerprint(kind, bound if kind == TARGET_MANAGED else python, root),
        scope_policy=scope_policy,
        **_effects(root, joint.requirements, joint.constraints, scope_policy),
    )


def selection_signature(project: str | Path) -> tuple:
    """项目级解释器决定此刻是什么——计划记下它，执行前再比：期间用户采用了别的环境，旧计划不再有效
    （安装结束会把结果记成项目的环境，不能盖掉用户刚做的选择）。"""
    record = projectenv.remembered_record(project)
    if record is None or record.get("trigger") == TRIGGER_DEPENDENCY_REPAIR:
        # 依赖安装自己写下的记录不算「用户的决定」：A 装完把结果记成项目环境，不能因此让同时形成的 B 计划
        # 变成"选择变了"（B 该得到更具体的 `dependency_already_attempted`）
        return ()
    return (
        record.get("mode"),
        os.path.normcase(str(record.get("path") or "")),
        bool(record.get("automatic")),
    )


def _selection_unchanged(project: str | Path, before: tuple) -> None:
    """计划形成期间（长事实探测之后）项目级解释器决定被别处改了：目标 / 影响是按旧决定算的，不能把新决定的
    签名记到这份计划上蒙混过执行前的比对——直接 `repair_plan_stale`，让用户按新决定重新开始。"""
    if selection_signature(project) != before:
        raise RepairError(ERROR_PLAN_STALE, "形成计划期间项目的解释器选择变了，请重新开始")


#: 作用域策略：空 = 并入（默认，沿用账）；`switch` = 换成本作用域（D04，新一代只装本作用域的集合）。
SCOPE_POLICY_SWITCH = "switch"


def scope_of(script: str) -> str:
    """脚本的作用域：它所在目录的项目相对 POSIX 路径（项目根 = `.`）。同一目录的脚本共用一套声明（`_decl_dirs`
    从脚本目录逐级向上），所以目录就是依赖的作用域。"""
    return Path(str(script)).parent.as_posix() or "."


def _scope_conflicts(root: str, script: str, joint: depplan.JointPlan) -> list[dict]:
    """要装的集合里，哪些与账上**别的作用域**装进去的版本互斥（D04）。

    受管环境是项目级的：A 目录装了 `numpy==1.26`，B 目录的声明要 `numpy>=2`——并入账再装会让 A 的脚本悄悄跑在
    numpy 2 上（`generation_requirements` 里新声明让位旧账）。这里只在**能判定归属**时才算互斥（账上这一笔记了
    `scope`、与本脚本的作用域不同、记的版本不满足本次的声明）；老账没有归属不参与，同一作用域里用户改了自己的声明
    是正常升级，也不算。需求与约束都看。"""
    ledger = {
        depresolve.normalize_distribution(str(e.get("distribution") or "")): e
        for e in managedenv.ledger_entries(root)
        if e.get("scope") and e.get("resolved_version")
    }
    mine = scope_of(script)
    requirements_mod, _markers, specifiers, _utils, _version = depresolve._pkg()
    out: list[dict] = []
    # 已经装着、但版本不满足本作用域的声明、而这一笔是别的作用域装的：`depplan` 对这类只报告不改（FO-038），
    # 对本作用域的脚本却是实打实的互斥——不能让它悄悄带着不满足的版本"准备好了"
    for item in joint.satisfied:
        entry = ledger.get(depresolve.normalize_distribution(str(item.get("distribution") or "")))
        if item.get("matches_declared", True) or entry is None or entry["scope"] == mine:
            continue
        out.append(
            {
                "name": depresolve.normalize_distribution(str(item["distribution"])),
                "installed_version": str(
                    item.get("installed_version") or entry["resolved_version"]
                ),
                "installed_for": str(entry["scope"]),
                "wanted": ",".join(item.get("specifiers") or ()),
                "wanted_for": mine,
                "via": "installed",
            }
        )
    for via, texts in (("requirement", joint.requirements), ("constraint", joint.constraints)):
        for text in texts:
            try:
                req = requirements_mod.Requirement(text)
            except Exception:  # noqa: BLE001 — 认不出的串别的闸会拦（write_plan_files），这里不替它判
                continue
            entry = ledger.get(depresolve.normalize_distribution(req.name))
            if entry is None or entry["scope"] == mine or not str(req.specifier):
                continue
            try:
                satisfied = req.specifier.contains(str(entry["resolved_version"]), prereleases=True)
            except specifiers.InvalidSpecifier:
                continue
            if not satisfied:
                out.append(
                    {
                        "name": depresolve.normalize_distribution(req.name),
                        "installed_version": str(entry["resolved_version"]),
                        "installed_for": str(entry["scope"]),
                        "wanted": str(req.specifier),
                        "wanted_for": mine,
                        "via": via,
                    }
                )
    return out[:20]


def _with_scope_check(
    root: str, script: str, joint: depplan.JointPlan, kind: str, scope_policy: str
) -> depplan.JointPlan:
    """联合计划对着账再核一道作用域互斥：互斥 → `blocked`（`dependency_scope_conflict`，带冲突项与出路），
    不并入、不装。只对受管目标、没有别的 blocked 理由的计划（ready 或"什么都不缺"）做。

    `scope_policy=switch`：用户选了"换成本作用域"——新一代按本作用域的全部 needed 重装（`requirements` 是
    全集，不是相对 active 那一代的差额），所以只要有要装的就是 `ready`，哪怕当前环境里"碰巧都有"。"""
    if kind != TARGET_MANAGED:
        return joint
    if scope_policy == SCOPE_POLICY_SWITCH:
        if joint.status == depplan.STATUS_NOTHING_NEEDED and joint.requirements:
            return dataclasses.replace(joint, status=depplan.STATUS_READY)
        return joint
    if joint.status not in (depplan.STATUS_READY, depplan.STATUS_NOTHING_NEEDED):
        return joint
    conflicts = _scope_conflicts(root, script, joint)
    if not conflicts:
        return joint
    entry = {
        "code": depplan.BLOCK_SCOPE_CONFLICT,
        "conflicts": conflicts,
        "count": len(conflicts),
        # 两条出路：换成本作用域（旧代留着直到没人用；别的作用域的包不再 active）/ 把这个子目录当独立项目
        # （各有各的受管环境）。第一版只服务一个优先的绘图作用域，不替用户硬合并
        "options": ["switch_scope", "separate_project"],
    }
    return dataclasses.replace(
        joint, status=depplan.STATUS_BLOCKED, blocked=(*joint.blocked, entry)
    )


def _switch_effects(
    root: str, requirements, constraints=()
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """换成本作用域时对账上现有内容的影响 `(drops, changes)`（规范化名）：`drops` = 不在新集合里、不再出现在
    active 环境里的包；`changes` = 新集合里还有、但账上记的版本不满足新声明、所以版本会变的包。都要告诉用户——
    别的作用域的脚本下次在新环境里会缺它们 / 拿到另一个版本。

    「新声明」= pip 实际会收到的 `-r` 需求**与** `-c` 约束一起（Codex #814 r4218254708）：需求写得没有版本、
    约束写 `beta>=2` 而账上是 `beta==1` 时，pip 会把它换成 >=2，披露就必须说出来。约束只收紧"装进来的包"，
    所以只对需求里出现的名字生效（约束里单独出现的名字 pip 不会因它而装）。"""
    requirements_mod, _markers, specifiers, _utils, _version = depresolve._pkg()
    wanted: dict[str, list[str]] = {}
    for text in requirements:
        try:
            req = requirements_mod.Requirement(text)
        except Exception:  # noqa: BLE001 — 认不出的串别的闸会拦
            continue
        wanted.setdefault(depresolve.normalize_distribution(req.name), []).append(
            str(req.specifier)
        )
    for text in constraints:
        try:
            con = requirements_mod.Requirement(text)
        except Exception:  # noqa: BLE001 — 认不出的串别的闸会拦
            continue
        name = depresolve.normalize_distribution(con.name)
        if name in wanted:
            wanted[name].append(str(con.specifier))
    drops: set[str] = set()
    changes: set[str] = set()
    for entry in managedenv.ledger_entries(root):
        name = depresolve.normalize_distribution(str(entry.get("distribution") or ""))
        if not name:
            continue
        if name not in wanted:
            drops.add(name)
            continue
        version = str(entry.get("resolved_version") or "")
        if not version:
            continue
        for spec in wanted[name]:
            if not spec:
                continue
            try:
                if not specifiers.SpecifierSet(spec).contains(version, prereleases=True):
                    changes.add(name)
                    break
            except specifiers.InvalidSpecifier:
                continue
    return tuple(sorted(drops)), tuple(sorted(changes))


def _effects(root: str, requirements, constraints, scope_policy: str) -> dict:
    """`impact_of` 的 `drops` / `changes` 两个参数（只有"换成本作用域"才有）。需求与约束都要给——
    它们是 pip 收到的全部输入（见 `_switch_effects`）。"""
    if scope_policy != SCOPE_POLICY_SWITCH:
        return {"drops": (), "changes": ()}
    drops, changes = _switch_effects(root, requirements, constraints)
    return {"drops": drops, "changes": changes}


# ---------------------------------------------------------------------------
# 「能怎么修」——不起任何子进程
# ---------------------------------------------------------------------------
def managed_available() -> bool | None:
    """能不能建受管环境。`None` = 还不知道（正在后台探）。

    三态而不是两态：探一次基础解释器要起好几个子进程，而问它的地方是**渲染
    出错的响应**——为了贴一个按钮把出错响应卡住几十秒是本末倒置。第一次问
    时后台探，之后是一次字典查询。
    """
    with _lock:
        if _base_python_known:
            return bool(_base_python)
    threading.Thread(target=_warm_base_python, daemon=True, name="tavotto-base-python").start()
    return None


def _warm_base_python() -> None:
    _probe_base_python()


def _probe_base_python() -> str | None:
    """探一次并写回缓存——只在探测期间没被 `reset_state()` 过时才写（世代相同）。"""
    global _base_python, _base_python_known
    with _lock:
        epoch = _base_epoch
    found = managedenv.base_python()
    with _lock:
        if epoch == _base_epoch:
            _base_python, _base_python_known = found, True
    return found


def base_python() -> str | None:
    """基础解释器（同步；没有回 None）。计划创建那条路上用它。"""
    with _lock:
        if _base_python_known:
            return _base_python
    return _probe_base_python()


def supported_python_range() -> dict:
    """支持的 Python 次版本闭区间 `{"min": "3.10", "max": "3.14"}`（界面文案用）。"""
    lo, hi = projectenv.PYTHON_MIN, projectenv.PYTHON_TESTED[-1]
    return {"min": f"{lo[0]}.{lo[1]}", "max": f"{hi[0]}.{hi[1]}"}


def offer(project: str | Path, script: str, module: str, project_env: dict | None = None) -> dict:
    """缺 `module` 时「能怎么修」——**只读判断，不装任何东西**。

    挂在 `missing_dependency` 的错误响应上（ADR 0019 §UX）。`project_env` 是
    Session 7 自动接手失败时的结构化原因：它已经体检过候选 venv 了，这里直接
    复用那份结论，不重新起解释器。

    解析不到可信 distribution 时 `requirement` 为 None、`targets` 为空——那时
    界面给的是「指定安装包…」与「选择其他 Python」，**绝不**拿 import 名去装。
    """
    root = str(Path(project))
    requirement = depresolve.resolve(root, module, script)
    out: dict = {
        "import_name": module,
        # 哪个脚本缺的：创建计划时要按 (项目, 脚本) 记轮次，而前端手里只有
        # 这份 offer。项目相对路径，与注册表同一种写法。
        "script": script,
        "requirement": requirement.to_payload() if requirement else None,
        "rounds_remaining": rounds_remaining(root, script),
        "targets": [],
        "system_rejected": [],
        # 界面让用户「装一个受支持的 Python」时要说出范围：取支持口径的运行时镜像
        # （`projectenv.PYTHON_MIN` / `PYTHON_TESTED`，与 docs/support-matrix.json
        # 由 test_support_matrix 对拍），文案里不再手写版本号。
        "python_supported": supported_python_range(),
    }
    # ---- 全局显式解释器生效：下面每一条路都写项目级决策，一条也轮不到 ----
    # （#465）自动接手 / 采用系统解释器 / 装进项目 .venv 或受管环境，最后都落到
    # `pool.resolve_worker_python()` 的第 3 档，而第 1、2 档只要存在就压过它。
    # 那时提供安装目标等于让用户真的联网装一遍、装完渲染照样缺——所以一个目标
    # 都不给，把「指定了哪条、来源是什么」说出来，让界面给出能解开它的那一步。
    pinned = pinned_payload()
    if pinned:
        out["pinned"] = pinned
        out["code"] = ERROR_INTERPRETER_PINNED
        return out
    # ---- 0. 这台机器上已有的解释器里已经装着它：采用，不装 ---------------
    # **排在两个提前返回之前**：采用不需要解析出包名（它什么都不装），也不
    # 消耗修复轮次——解析不出 / 轮次用完时，这条路正是用户仅剩的那条。
    # Session 7 的第二层（ADR 0044）在接手失败时已经把系统解释器体检过了，
    # 结论就在 `project_env["system"]` 里——这里同样**不再起解释器**。健康的
    # 排在最前：它一个字节都不装、不联网、不改任何环境，比两种安装都便宜。
    # 探到了但不合格的（版本不支持 / 没有 matplotlib / 起不来）单列在
    # `system_rejected`：用户手边那套环境为什么没被采用，界面要说出来。
    detail = project_env or {}
    system = detail.get("system") if isinstance(detail, dict) else None
    recommended = (
        detail.get("recommended")
        if isinstance(detail, dict) and detail.get("code") == projectenv.ERROR_CONFIRMATION_REQUIRED
        else None
    )
    # 建议带着「看到它那一刻」的候选 id 与环境代：缺一个都不列成目标——没有绑定的「改用」只能退回按路径采用，
    # 环境在看到之后被重建，就会静默采用用户没看过的那一代（与 `MissingDependencyCard` 同一条纪律）
    if isinstance(recommended, dict) and recommended.get("id") and recommended.get("generation"):
        # 确认模式（ADR 0114）：项目自己的 venv 体检通过、缺的包也在里面——不再无提示接手，列成一个
        # 「改用」目标，用户点一次才记进项目设置（与系统解释器同一个采用端点、同一次现场体检）。项目内的
        # 解释器给项目相对路径：采用端点把它钉回项目根之内
        health = recommended.get("health") or {}
        python = str(recommended.get("python") or "")
        out["targets"].append(
            {
                "kind": TARGET_SYSTEM,
                "venv": projectenv.project_relative(root, recommended.get("venv") or "") or "",
                "python": projectenv.project_relative(root, python) or python,
                "modifies_user_environment": False,
                "creates_environment": False,
                "available": True,
                "reason": "",
                "python_version": health.get("python_version", ""),
                "matplotlib_version": health.get("matplotlib_version", ""),
                "support": health.get("support", ""),
                # 与 `PATCH /api/engine/environment` 的 `candidate` + `expected_generation` 一一对应
                "candidate": {
                    "id": str(recommended["id"]),
                    "generation": str(recommended["generation"]),
                },
            }
        )
    found = projectenv.healthy_system_candidate(system)
    if found:
        out["targets"].append(
            {
                "kind": TARGET_SYSTEM,
                "venv": "",
                # 项目之外的绝对路径：它本来就不跟项目走，界面上也正该显示
                # 「/usr/bin/python3」而不是一个相对到项目外的 `../../..`。
                "python": found["python"],
                "modifies_user_environment": False,
                "creates_environment": False,
                "available": True,
                "reason": "",
                "python_version": found.get("python_version", ""),
                "matplotlib_version": found.get("matplotlib_version", ""),
                "support": found.get("support", ""),
            }
        )
    out["system_rejected"] = [
        {
            "python": r["python"],
            "code": r.get("code", ""),
            "python_version": r.get("python_version", ""),
        }
        for r in projectenv.rejected_system_candidates(system)
    ]

    if requirement is None or not requirement.installable:
        out["code"] = ERROR_UNRESOLVED
        return out
    if out["rounds_remaining"] <= 0:
        out["code"] = ERROR_ROUNDS_EXHAUSTED
        return out

    # ---- A. 项目自己的 .venv：只有「除了这个包之外都健康」才提供 ----------
    # Session 7 的体检已经回答过这件事：`project_env_module_missing` 的语义
    # 正是「找到了、Python 与 matplotlib 都行、就是没有这个包」。其他失败码
    # （没有 matplotlib / 版本不支持 / 起不来）**不该**提供安装——往一个跑不起
    # worker 的环境里装包，装完还是跑不起来。
    if detail.get("code") == projectenv.ERROR_MODULE_MISSING:
        venv = detail.get("venv") or ""
        # `venv` 来自调用方交来的体检结果：照样钉在项目根之内再用（与 `_pick_project_venv` 同一条纪律）
        python = projectenv.interpreter_of(venv, root=root) if venv else None
        if python:
            out["targets"].append(
                {
                    "kind": TARGET_PROJECT_VENV,
                    "venv": projectenv.project_relative(root, venv) or venv,
                    "python": projectenv.project_relative(root, python) or python,
                    "modifies_user_environment": True,
                    "creates_environment": False,
                    "available": True,
                    "reason": "",
                }
            )

    # ---- B. Tavotto 受管环境：可删可重建，改的是我们自己的东西 ------------
    managed = managedenv.state(root)
    # 受管目标**每次**都建新的一代（U04 §五）：有没有 active 代都要基础解释器，「可用」看的是 base
    # （三态：None = 还在后台探）。没有 base 但这个目标上提供私有 Python（U05）：这条路仍然可用，
    # 只是授权里多一项「先下载 N 字节」——载荷挂在目标上，界面据此把数字说出口（Codex #464 P2）
    available = managed_available()
    private = privatepython.offer_payload() if available is False else None
    if private is not None:
        available = True
    # 这一代要装的全部包**不在 offer 里算**：联合求解要量解释器事实（起子进程），而 offer 在渲染失败的响应路径上
    # 不起任何解释器。要装什么由形成计划（`create_plan`）时算、随计划载荷说出口，卡片按预读的计划写那一句话
    out["targets"].append(
        {
            "kind": TARGET_MANAGED,
            "venv": "",
            "python": "",
            "modifies_user_environment": False,
            "creates_environment": not managed["exists"],
            # None = 还不知道（基础解释器正在后台探）。界面照样把这条列出来，
            # 真正的答案在创建计划那一步——那时用户已经点过，等几秒是合理的。
            "available": available,
            "reason": "" if available is not False else ERROR_MANAGED_UNAVAILABLE,
            "private_python": private,
        }
    )
    out["managed"] = managed
    return out


# ---------------------------------------------------------------------------
# 全局显式解释器（#465）
# ---------------------------------------------------------------------------
def pinned_payload() -> dict | None:
    """正在生效的全局显式解释器，投影成界面认的形状；没有回 None。

    `variable` 只在 `env_override` 时有值，且是**供值的那个**名字（新名或旧名
    `MM_WORKER_PYTHON`）：界面「清掉环境变量后重启」那句按它点名。
    """
    pinned = pool.explicit_worker_python()
    if not pinned:
        return None
    python, source = pinned
    out = {"python": python, "source": source, "variable": ""}
    if source == pool.SOURCE_ENV:
        pair = pool.worker_python_env_pair()
        out["variable"] = pair[0] if pair else pool.WORKER_PYTHON_ENV
    return out


def _refuse_if_pinned() -> None:
    pinned = pinned_payload()
    if pinned:
        raise RepairError(
            ERROR_INTERPRETER_PINNED,
            f"渲染解释器已固定为 {pinned['python']}，为项目安装的环境不会被使用",
            pinned=pinned,
        )


# ---------------------------------------------------------------------------
# 创建计划
# ---------------------------------------------------------------------------
def create_plan(
    project: str | Path, script: str, module: str, *, target_kind: str, user_distribution: str = ""
) -> RepairPlan:
    """把「装什么、装到哪」定下来，发一个短期计划 id。

    这里做**一次**目标环境体检（受管环境还没建时跳过——没有可体检的东西）：
    「选了但装不了」比「没选」更难查，而这一步是用户主动点出来的，等几秒
    合理。体检同时给出安装前状态（§安装前后状态的第一层）。
    """
    root = str(Path(project))
    selection0 = selection_signature(root)  # 先于目标解析与体检：计划记的是**算目标时**的决定
    if target_kind not in TARGETS:
        raise RepairError(ERROR_NOT_ALLOWED, f"未知的安装目标: {target_kind!r}")
    _refuse_if_pinned()  # 与 `offer()` 同一条判据：装进去也不会被用的计划一开始就不形成
    if rounds_remaining(root, script) <= 0:
        raise RepairError(ERROR_ROUNDS_EXHAUSTED, "这个脚本的自动依赖修复已经用满")
    if not projectenv.valid_module_name(module):
        raise RepairError(ERROR_UNRESOLVED, f"模块名不合形状: {module!r}")

    if user_distribution:
        requirement = depresolve.from_user_input(module, user_distribution)
        if requirement is None:
            raise RepairError(ERROR_REQUIREMENT_INVALID, "只接受 `包名` 或 `包名>=版本` 这样的形态")
    else:
        requirement = depresolve.resolve(root, module, script)
        if requirement is None:
            raise RepairError(ERROR_UNRESOLVED, f"无法确定 {module} 对应哪个安装包")
    if not requirement.installable:
        raise RepairError(ERROR_REQUIREMENT_INVALID, "这个需求不可安装")

    python = ""
    creates = False
    private: dict | None = None
    widened: _Widened | None = None
    if target_kind == TARGET_PROJECT_VENV:
        python = _pick_project_venv(root, script, module)
        health = projectenv.probe_environment(python, module)
        if health.get("code") == projectenv.ERROR_MODULE_MISSING:
            pass  # 正是我们要修的状态
        elif health.get("ok"):
            raise RepairError(ERROR_ALREADY_PRESENT, f"这个环境里已经有 {module} 了")
        else:
            raise RepairError(
                health.get("code") or ERROR_NOT_ALLOWED, "这个环境不适合作为安装目标", health=health
            )
    else:
        python = managedenv.python_of(root) or ""
        creates = not python
        # 受管目标**每一次**都建新的一代（U04 §五），有没有 active 代都要基础解释器：已有环境的机器上
        # 系统 Python 被删掉之后，单包修复同样要走私有 Python（Codex #464 第二轮 P2）
        private = _private_python_offer()
        if creates:
            # 从空 venv 起的第一代：脚本开跑要的**全部**第三方依赖一次装齐，不只是缺的这一个——
            # 新一代没有内置 runtime 里的 pandas 等，只装一个包，自动重跑必然又撞跑前门
            # （2026-09-29 干净 macOS 虚拟机实测：装完 openpyxl 后卡在「缺 pandas」）
            widened = _widen_for_fresh_generation(root, script, requirement)

    key = _env_key(target_kind, python, root)
    with _lock:
        if (managedenv.project_fingerprint(root), key, requirement.requirement()) in _attempted:
            raise RepairError(ERROR_ALREADY_ATTEMPTED, "同一个环境上的同一个需求这一轮已经装过了")
    _selection_unchanged(root, selection0)
    now = time.time()
    plan = RepairPlan(
        plan_id=new_plan_id(),
        project=root,
        project_id=managedenv.project_fingerprint(root),
        script=script,
        target_kind=target_kind,
        python=python,
        env_fingerprint=_fingerprint(target_kind, python, root),
        requirement=requirement,
        modifies_user_environment=target_kind == TARGET_PROJECT_VENV,
        creates_environment=creates,
        network_required=True,
        created_at=now,
        expires_at=now + PLAN_TTL_S,
        private_python=private,
        widened=widened,
        selection=selection0,
    )
    _prune_plans()
    with _lock:
        _plans[plan.plan_id] = plan
    LOG.info(
        "依赖修复计划: %s → %s（%s）",
        ", ".join(plan.requirements),
        logsafe.known(target_kind, TARGETS),
        script,
    )
    return plan


def _fresh_generation_joint(project: str, script: str) -> depplan.JointPlan | None:
    """受管目标要新建一代时，这个脚本的联合计划——与 `create_joint_plan` 同一条取事实的路（`_facts_for` /
    `private_python_target`）：缺什么按此刻会跑脚本的解释器量，装什么按将要新建的那一代量。
    量不出来（没有解释器 / 事实取不到）回 None，由调用方退回单包。"""
    standin = private_python_target(project, script)
    install = None
    if standin is not None:
        facts = standin[2]
    else:
        try:
            python = pool.resolve_worker_python(project, script=script)[0]
        except pool.WorkerError:
            python = ""
        run, install, _measured = _facts_for(TARGET_MANAGED, python, project)
        facts = run if run is not None else install
        install = install if run is not None else None
    if facts is None:
        return None
    return depplan.plan(
        project,
        script,
        facts=facts,
        target_kind=TARGET_MANAGED,
        groups=depplan.selected_groups_setting(project),
        install_facts=install,
    )


def _fold_requested(
    requirement: depresolve.DependencyRequirement, joint: depplan.JointPlan
) -> _Widened | None:
    """把用户点的那一个包并进联合计划：联合集合里已有同名的用它（带声明的 extras / 版本），没有就补上。
    联合计划 blocked / hash 模式（锁就是闭包，多补一条没有 hash 的会被 pip 拒）回 None——退回单包。"""
    if joint.status == depplan.STATUS_BLOCKED or joint.require_hashes:
        return None
    wanted = depresolve.normalize_distribution(requirement.distribution)
    delta = list(joint.requirements)
    if all(depresolve.normalize_distribution(_name_of(r)) != wanted for r in delta):
        delta.append(requirement.requirement())
    imports = [m["import_name"] for m in joint.missing]
    if requirement.import_name and requirement.import_name not in imports:
        imports.append(requirement.import_name)
    # 账目里用户点的那个包沿用单包修复原来的写法（distribution / specifier 取自它自己的 requirement），其余取自联合计划
    record = [
        {
            "import_name": requirement.import_name,
            "distribution": requirement.distribution,
            "specifier": requirement.specifier,
        }
    ]
    record += [
        {
            "import_name": m["import_name"],
            "distribution": m["distribution"],
            "specifier": ",".join(m["specifiers"]),
        }
        for m in joint.missing
        if depresolve.normalize_distribution(m["distribution"]) != wanted
    ]
    return _Widened(
        requirements=tuple(delta),
        constraints=tuple(joint.constraints),
        hashes={},
        needed_imports=tuple(imports),
        record=tuple(record),
        groups=tuple(joint.selection.get("selected_groups") or ()),
        inputs_digest=joint.inputs_digest,
    )


def _widen_for_fresh_generation(
    project: str, script: str, requirement: depresolve.DependencyRequirement
) -> _Widened | None:
    joint = _fresh_generation_joint(project, script)
    return _fold_requested(requirement, joint) if joint is not None else None


def _private_python_offer() -> dict | None:
    """受管环境要新建而这台机器没有基础解释器时：提供私有 Python 就回它的载荷，否则照旧拒绝。

    回 None = 有基础解释器（不需要下载）；回载荷 = 这份计划的授权包含下载；抛 = 两者都没有。
    这一步不联网、不起子进程：`offer_payload` 只看锁文件与磁盘。"""
    if base_python():
        return None
    private = privatepython.offer_payload()
    if private is None:
        raise RepairError(ERROR_MANAGED_UNAVAILABLE, "这台机器上没有可以用来创建环境的 Python")
    privatepython.require_free_disk(privatepython.source_for())
    return private


def _pick_project_venv(project: str, script: str, module: str) -> str:
    """项目 venv 目标的解释器——**从发现结果里取**，不接受调用方给路径。

    接受路径就等于开了一个「往任意解释器里 pip install」的接口。
    """
    for venv in projectenv.discover(project, script):
        python = projectenv.interpreter_of(venv, root=project)
        if python:
            return python
    raise RepairError(projectenv.ERROR_NOT_FOUND, f"这个项目里没有可用的虚拟环境（缺 {module}）")


def get_plan(plan_id: str) -> RepairPlan | None:
    _prune_plans()
    with _lock:
        return _plans.get(str(plan_id or ""))


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def progress(plan_id: str) -> dict:
    with _lock:
        return dict(
            _progress.get(str(plan_id or ""))
            or {"state": "idle", "plan_id": "", "log": "", "error": None, "code": ""}
        )


def cancel(plan_id: str) -> bool:
    """请求取消。真正的处置在安装线程里（见 `_finish_cancelled`）。

    过了提交点（受管环境已切 active，U04）的计划拒绝取消：那之后没有可以「不留下」的
    东西了，`cancel_status()` 会说 `committed`。
    """
    return cancel_status(plan_id)["accepted"]


def install_async(plan_id: str, on_event=None, *, confirmed_impact: str | None) -> bool:
    """起线程执行一份单包修复计划；回 False = 这份计划已在执行（不再起第二个线程）。认领在起线程之前
    （T06：联合准备早有的 `_claim` 纪律，单包路径同样——两个标签页点同一个「安装」只跑一个 pip）。"""
    pid = str(plan_id or "")
    plan = get_plan(pid)
    _check_confirmed(plan, confirmed_impact)
    if not _claim(
        pid,
        project_id=getattr(plan, "project_id", ""),
        digest=getattr(plan, "impact_digest", ""),
        flow=FLOW_SINGLE,
        scope=_plan_scope(plan),
    ):
        return False
    _register_cancel(pid)
    threading.Thread(
        target=lambda: _install_guarded(pid, on_event, claimed=True),
        daemon=True,
        name="tavotto-dep-install",
    ).start()
    return True


def _install_guarded(
    plan_id: str, on_event, *, claimed: bool = False, confirmed_impact: str | None = None
) -> dict:
    # 计划是一次性的：`install()` 的 finally 会把它从表里摘掉，失败的终态要在这之前拿住它——
    # 终态上的 `retryable`（pip 跑成之后再失败的不给重试）按计划里的项目与需求算（Codex #709）
    plan = get_plan(plan_id)
    try:
        return install(plan_id, on_event, claimed=claimed, confirmed_impact=confirmed_impact)
    except RepairError as exc:
        _log_repair_failure("依赖修复", plan_id, exc.code)
        pinned = (exc.extra or {}).get("pinned")
        return _emit(
            plan_id,
            STATE_FAILED,
            on_event,
            plan=plan,
            code=exc.code,
            error=str(exc),
            pinned=pinned if isinstance(pinned, dict) else None,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.exception("依赖安装线程异常")
        return _emit(plan_id, STATE_FAILED, on_event, plan=plan, code=ERROR_FAILED, error=str(exc))


def install(
    plan_id: str, on_event=None, *, claimed: bool = False, confirmed_impact: str | None
) -> dict:
    """执行一个计划。**这是唯一一处会往磁盘上装包的代码。**

    执行端只认 `plan_id`：装什么、装到哪、哪个项目，全部来自计划本身
    （防 TOCTOU，ADR 0019 §计划绑定）。计划不存在 / 过期 / 环境在确认期间
    变过，一律拒绝。

    同一份计划只认领一次（T06，联合准备早有的 `_claim` 纪律）：已在执行的再来一次得
    `dependency_install_not_allowed`，不起第二个 pip。

    `confirmed_impact` 是**必传**的关键字参数：调用方回显的、它给用户看过的影响摘要（见 `_check_confirmed`）。
    只有 `claimed=True`（入口已在认领前核过摘要、这里是线程体）时可以传 None。
    """
    pid = str(plan_id or "")
    if not claimed:
        plan0 = get_plan(pid)
        _check_confirmed(plan0, confirmed_impact)
        if not _claim(
            pid,
            project_id=getattr(plan0, "project_id", ""),
            digest=getattr(plan0, "impact_digest", ""),
            flow=FLOW_SINGLE,
            scope=_plan_scope(plan0),
        ):
            raise RepairError(ERROR_NOT_ALLOWED, "这份修复计划已经在执行")
    try:
        return _install_claimed(pid, on_event)
    finally:
        _release(pid)


def _install_claimed(plan_id: str, on_event) -> dict:
    plan = get_plan(plan_id)
    if plan is None:
        # 没有计划就没有用户意图。**后端自己就是能力边界**，不靠
        # 「按钮理论上不会调这个接口」。
        raise RepairError(ERROR_NOT_ALLOWED, "没有这个修复计划（或已过期）")
    current = _fingerprint(plan.target_kind, plan.python, plan.project)
    if current != plan.env_fingerprint:
        with _lock:
            _plans.pop(plan.plan_id, None)
        raise RepairError(ERROR_PLAN_STALE, "确认期间目标环境发生了变化")
    if plan.selection != selection_signature(plan.project):
        with _lock:
            _plans.pop(plan.plan_id, None)
        raise RepairError(ERROR_PLAN_STALE, "确认期间项目的解释器选择变了")

    cancel_ev = _register_cancel(plan.plan_id)
    try:
        if plan.target_kind == TARGET_MANAGED:
            # 换代的锁：合成 key + active 那一代的解释器，**不收掉**旧代上的 worker
            key = _env_key(TARGET_MANAGED, "", plan.project)
            with pool.mutating_environment(key, plan.python, shutdown=False):
                _refuse_if_pinned()  # 全局显式解释器压着时，装进受管环境也不会被用（#465 / E05）
                return _run_install(plan, key, on_event, cancel_ev)
        key = _env_key(plan.target_kind, plan.python, plan.project)
        with pool.mutating_environment(key, plan.python):
            # **租约在手之后**才复查全局固定（Codex 评审 #469 两轮 P1）：确认窗口里
            # 从别处把全局解释器钉上，环境指纹看不见这条；而钉的那条路
            # （`envlease.unless_mutating`）与这把租约互斥——先钉上的这里看得见，
            # 后钉的被拒。租约之前查一次没有用：查完到拿到租约之间照样能钉。
            # 计划照常在 finally 里作废：形成时的前提已经不成立。
            _refuse_if_pinned()
            return _run_install(plan, key, on_event, cancel_ev)
    except pool.EnvironmentBusy as exc:
        raise _busy_error(exc) from exc
    finally:
        with _lock:
            _cancels.pop(plan.plan_id, None)
            _plans.pop(plan.plan_id, None)  # 计划是一次性的


def _busy_error(exc) -> "RepairError":
    """`EnvironmentBusy` → `RepairError`，**把它的 code 带过来**。

    `envlease` 用两个 code 区分两种忙（另一次安装 / 有 native 会话）。在这里
    统统折成 `ERROR_BUSY` 的话，前端就只能给一句「忙，稍后再试」——而"稍后"
    对 native 那一条永远不会到来：那个脚本要用户自己去结束。
    """
    code = getattr(exc, "code", "")
    if code == ERROR_IN_USE_BY_NATIVE:
        return RepairError(ERROR_IN_USE_BY_NATIVE, str(exc))
    return RepairError(ERROR_BUSY, str(exc))


def _run_install(plan: RepairPlan, env_key: str, on_event, cancel_ev: threading.Event) -> dict:
    project, script = plan.project, plan.script
    req = plan.requirement
    _emit(plan.plan_id, STATE_PREPARING, on_event, plan=plan)

    python = plan.python
    if plan.target_kind == TARGET_MANAGED:
        # 受管环境按代（U04，ADR 0061 §五）：单包修复是联合准备的特例——建新的一代、装完整集合、
        # 验完再切 active。这里不再往 active 那一代原地 pip。从空 venv 新建第一代时集合是脚本开跑
        # 要的全部（`plan.widened`）；往已有账上加一个包时 delta 只有那一条。
        wide = plan.widened
        pi = plan.pip_inputs
        job = _GenerationJob(
            progress_id=plan.plan_id,
            project=project,
            script=script,
            delta=pi.requirements,
            constraints=pi.constraints,
            hashes=pi.hashes,
            require_hashes=pi.require_hashes,
            needed_imports=wide.needed_imports
            if wide
            else ((req.import_name,) if req.import_name else ()),
            record=wide.record
            if wide
            else (
                {
                    "import_name": req.import_name,
                    "distribution": req.distribution,
                    "specifier": req.specifier,
                },
            ),
            reason=managedenv.REASON_MISSING_DEPENDENCY,
            identity="",
            emit=lambda state, **kw: _emit_repair_step(
                plan.plan_id, state, on_event, plan=plan, **kw
            ),
            on_log=lambda text: _append_log(plan.plan_id, text, on_event),
            label=f"repair-{req.distribution}",
            provision_private=plan.private_python is not None,
            private_plan=plan.private_python,
            # 「这一轮装成功过」与项目 venv 那条路同一条纪律（#466）：代事务在 **pip 退出码 0 之后**
            # 才登记。以前写在建代之前，私有 Python 下载失败 / 取消 / 断网的那一次也被记成「装过了」，
            # 界面说「检查网络后重试」，重试撞到的却是 `dependency_already_attempted`，只能重启应用
            # （2026-09-28 Windows Server 2025 冻结包实测）。
            attempted=(plan.project_id, env_key, req.requirement()),
            # 计划里带着下载 = 事实来自替身：供应之后按真解释器重算，那时仍要把用户点的这一个并进去
            groups=wide.groups if wide else (),
            replan=bool(wide and plan.private_python is not None),
            confirmed_inputs=pi,
            confirmed_impact=plan.impact,
            inputs_digest=wide.inputs_digest if wide else "",
            requested=req if wide else None,
        )
        outcome = _run_generation_locked(job, cancel_ev, env_key)
        if not outcome.get("ok"):
            return outcome
        version = outcome["installed"].get(depresolve.normalize_distribution(req.distribution), "")
        result = {
            "ok": True,
            "python": outcome["python"],
            "version": version,
            "distribution": req.distribution,
            "import_name": req.import_name,
            "target_kind": plan.target_kind,
            "generation": outcome["generation"],
        }
        _emit(plan.plan_id, STATE_DONE, on_event, plan=plan, result=result)
        LOG.info(
            "依赖修复成功: %s %s → %s",
            req.distribution,
            logsafe.version(version),
            logsafe.known(plan.target_kind, TARGETS),
        )
        return result
    if cancel_ev.is_set():
        return _finish_cancelled(plan, on_event, python)

    # ---- pip 在不在 -------------------------------------------------------
    rc, out = _run([python, "-m", "pip", "--version"], PIP_PROBE_TIMEOUT_S)
    if rc != 0:
        # **不静默 ensurepip**：那是往用户环境里再加一样东西，而用户确认的是
        # 「装 lmfit」。
        raise RepairError(ERROR_PIP_UNAVAILABLE, _sanitize(out)[-800:])

    # ---- 安装 -------------------------------------------------------------
    # 租约在手、解释器已知之后再查一次「这一轮装成功过没有」（Codex 评审 #469 P2）：
    # 计划是在 `create_plan` 时查的，而另一个页签的同一需求可能在这之后才装完——
    # 环境指纹看不见 site-packages，两个计划都有效，第二个照样跑一遍无意义的 pip。
    # key 的算法与下面 `_attempted.add` 那一处逐字相同。
    attempted_key = (
        plan.project_id,
        _env_key(plan.target_kind, python, project),
        req.requirement(),
    )
    with _lock:
        already = attempted_key in _attempted
    if already:
        raise RepairError(ERROR_ALREADY_ATTEMPTED, "同一个环境上的同一个需求这一轮已经装过了")
    _emit(plan.plan_id, STATE_INSTALLING, on_event, plan=plan)
    # 项目 venv 原地：pip 只收到这一条需求（没有约束文件）——`pip_inputs` 也只披露这一条
    (in_place_requirement,) = plan.pip_inputs.requirements
    code, out = _pip_install(
        python,
        in_place_requirement,
        cancel_ev,
        lambda text: _append_log(plan.plan_id, text, on_event),
        on_mirror=lambda url: _note_mirror(plan.plan_id, url, on_event),
        on_source=lambda src: _note_source(plan.plan_id, src, on_event),
    )
    if code == ERROR_CANCELLED:
        return _finish_cancelled(plan, on_event, python)
    if code:
        raise RepairError(code, _sanitize(out)[-800:])
    # pip 退出码 0 **之后**才记「试过了」（#466）：这条黑名单挡的是「装完还缺、
    # 再装还缺」的循环——同一需求再装一遍改不了任何东西。网络断掉 / 用户取消的
    # 那次 pip 没跑成，再来一次是有意义的；以前写在 pip 之前，失败文案说
    # 「检查网络后重试」，重试撞到的却是「这一轮已经试过了」。
    with _lock:
        _attempted.add(attempted_key)

    # ---- 验证三层 ---------------------------------------------------------
    _emit(plan.plan_id, STATE_VERIFYING, on_event, plan=plan)
    health = projectenv.probe_environment(python, req.import_name or None)
    if not health.get("ok"):
        # pip 退出码 0 不等于「装对了」：装进了另一个环境、装的是同名的另一个
        # 包、扩展模块的 ABI 对不上——这三种都是 exit 0 + import 失败。
        raise RepairError(
            ERROR_IMPORT_STILL_FAILED if req.import_name else ERROR_FAILED,
            health.get("detail") or health.get("error") or "",
            health=health,
        )
    selftest = worker_self_test(python)
    if not selftest.get("ok"):
        raise RepairError(ERROR_SELFTEST_FAILED, _sanitize(selftest.get("detail", ""))[-800:])

    # ---- 记账 + 换环境 + 作废旧 worker -------------------------------------
    with _lock:
        _committed[plan.plan_id] = time.time()
    version = installed_version(python, req.distribution)
    projectenv.remember(
        project,
        python,
        automatic=False,
        trigger=TRIGGER_DEPENDENCY_REPAIR,
        module=req.import_name,
        health=health,
    )
    pool.note_project_python_ok(python)
    # 安装期间这个环境上的 worker 已经被停掉了（`mutating_environment`）。
    # 这里再点名作废一次：脚本自己的会话必须重建，import 系统 / sys.modules /
    # 已加载的动态库都不会因为磁盘上多了个包而刷新。
    pool.invalidate(script, project)
    depplan.reset_cache(python)
    _note_round(project, script)
    result = {
        "ok": True,
        "python": python,
        "version": version,
        "distribution": req.distribution,
        "import_name": req.import_name,
        "target_kind": plan.target_kind,
    }
    _emit(plan.plan_id, STATE_DONE, on_event, plan=plan, result=result)
    LOG.info(
        "依赖修复成功: %s %s → %s",
        req.distribution,
        logsafe.version(version),
        logsafe.known(plan.target_kind, TARGETS),
    )
    return result


#: 重建受管环境的进度 id：**每次重建一个**（#606 第 3 条）。以前是固定的 `"managed-rebuild"`——SSE 不带项目，
#: 两个项目各起一次重建，前端分不清进度是谁的，只好全局单飞；同一项目先后两次，轮询还会把上一次的终态当成这一次的。
#: 它没有 plan（重建不装新东西，只是把我们记过的那些装回去），所以 id 由发起方给：前端在发请求**之前**生成
#: （`web/src/store/depRepairStore.ts` 的 `newRebuildProgressId()`），SSE 早于 POST 响应到达也认得出；网络层失败时
#: 还能拿同一个 id 去 `GET /api/engine/dependency/state` 问实况（#606 第 5 条）。格式是两侧的同源对：32 位小写十六进制。
REBUILD_PROGRESS_ID_RE = re.compile(r"^[0-9a-f]{32}$")
#: **没给 id 的请求**（升级前的前端标签页、MCP）用的旧的固定 id（#634 评审 P2）。老前端认的就是这个字面量——
#: 给它一个随机 id，那个标签页永远看不到进度、它那边的单飞也永远不放。固定 id 同一时刻只能有一次在跑：
#: 第二个没给 id 的请求（另一个项目的老标签页）拿到的是 `environment_mutating`（「正在安装依赖，请稍候」），
#: 与老前端本来就会遇到的那句拒绝同一个 code。新前端永远给 id，不走这条。
LEGACY_REBUILD_PROGRESS_ID = "managed-rebuild"
#: 发起方给的 id 格式不对 / 已经被占用
ERROR_PROGRESS_ID_INVALID = "invalid_progress_id"
TERMINAL_STATES = (STATE_DONE, STATE_FAILED, STATE_CANCELLED)


def new_rebuild_progress_id() -> str:
    """同步调用方（`rebuild_managed` 没给 id）用：格式与前端生成的相同。"""
    return secrets.token_hex(16)


def claim_rebuild_progress_id(raw: str | None) -> str:
    """校验并占用一个重建进度 id。**只占 id，不动任何别的状态**：端点在它之后才清这个项目的计划 / 轮次
    （#634 评审 P1：先清再校验的话，一个被拒的请求照样抹掉了项目的准备状态）。

    * 没给：旧的固定 id `managed-rebuild`（老前端兼容）；它此刻在跑就抛 `RepairError(environment_mutating)`；
    * 给了：必须是 32 位小写十六进制、且不与任何计划 / 进度 / 在跑的作业同名，否则 `invalid_progress_id`。

    占用在锁里做（两次同 id 的请求只有一次拿得到），并先记一格 `preparing`：线程起来之前 `progress(id)`
    就不是 idle——网络层失败后前端来问实况时，分得清「已经起了」与「根本没到后端」。"""
    raw = str(raw or "")
    with _lock:
        if not raw:
            pid = LEGACY_REBUILD_PROGRESS_ID
            rec = _progress.get(pid)
            if pid in _cancels or (rec is not None and rec.get("state") not in TERMINAL_STATES):
                raise RepairError(
                    pool.ENVIRONMENT_MUTATING, "受管环境正在重建，请等这一次结束再试。"
                )
        else:
            pid = raw
            if not REBUILD_PROGRESS_ID_RE.match(pid):
                raise RepairError(ERROR_PROGRESS_ID_INVALID, "重建进度编号格式不对")
            if pid in _progress or pid in _plans or pid in _cancels:
                raise RepairError(ERROR_PROGRESS_ID_INVALID, "重建进度编号已经被占用")
        _progress[pid] = {
            "plan_id": pid,
            "state": STATE_PREPARING,
            "log": "",
            "error": None,
            "code": "",
        }
    return pid


def start_rebuild(project: str | Path, on_event, progress_id: str) -> None:
    """起重建线程（`progress_id` 必须已经由 `claim_rebuild_progress_id` 占用）。"""
    threading.Thread(
        target=lambda: _rebuild_guarded(project, on_event, progress_id),
        daemon=True,
        name="tavotto-managed-rebuild",
    ).start()


def rebuild_managed_async(project: str | Path, on_event=None, progress_id: str = "") -> str:
    """占用 id + 起线程；回这次重建的进度 id（`claim_rebuild_progress_id` 的规则）。"""
    pid = claim_rebuild_progress_id(progress_id)
    start_rebuild(project, on_event, pid)
    return pid


def _rebuild_guarded(project, on_event, progress_id: str) -> dict:
    try:
        return rebuild_managed(project, on_event, progress_id=progress_id)
    except RepairError as exc:
        _log_repair_failure("受管环境重建", progress_id, exc.code)
        return _emit(progress_id, STATE_FAILED, on_event, code=exc.code, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        LOG.exception("受管环境重建异常")
        return _emit(
            progress_id,
            STATE_FAILED,
            on_event,
            code=ERROR_MANAGED_CREATE_FAILED,
            error=str(exc),
        )


def rebuild_managed(project: str | Path, on_event=None, *, progress_id: str = "") -> dict:
    """重建受管环境：按账上记过的那些**建新的一代**，验完再切 active（U04 起不再删旧的
    重来——旧代留到没人用）。`progress_id` 没给就现生成一个（同步调用方用回值里的 `progress_id`）。

    **读账与建代在同一把环境锁之内**（Codex 评审 P1 的形状）：锁是合成 key +
    active 那一代的解释器，install 那条路（同一把）被挡在外面。

    **不声称 lockfile 级复现**：`environment.json` 里记的是安装当时解析出来的
    版本，重建时那个版本可能已经从 index 上撤了。真撤了就如实报错，不悄悄
    换一个别的版本装上——「重建完跟以前不一样」比「重建失败」难查得多。
    """
    root = str(Path(project))
    pid = progress_id or new_rebuild_progress_id()
    cancel_ev = threading.Event()
    with _lock:
        _cancels[pid] = cancel_ev
    try:
        job = _GenerationJob(
            progress_id=pid,
            project=root,
            script="",
            delta=(),
            constraints=(),
            hashes={},
            require_hashes=False,
            needed_imports=(),
            record=(),
            reason=managedenv.REASON_MISSING_DEPENDENCY,
            identity="",
            emit=lambda state, **kw: _emit(pid, state, on_event, **kw),
            on_log=lambda text: _append_log(pid, text, on_event),
            label="rebuild",
        )
        outcome = _run_generation(job, cancel_ev)
        if outcome.get("ok"):
            outcome["restored"] = managedenv.installed_requirements(root)
        outcome["progress_id"] = pid
        return outcome
    finally:
        with _lock:
            _cancels.pop(pid, None)


def _finish_cancelled(plan: RepairPlan, on_event, python: str) -> dict:
    """取消之后的**如实**处置——两种环境处置不同，这条差异要说出来。

    * 受管环境：标成 incomplete，下次不直接复用（我们自己的东西，重建即可）。
    * 用户的 `.venv`：**不假装能完整 rollback**。pip 可能已经写了一部分文件，
      甚至已经改了某个传递依赖的版本；`pip uninstall` 恢复不了那个状态，
      硬做只会把「装了一半」变成「拆坏了」。如实告诉用户「可能已发生部分
      修改」，并跑一次体检把当前状态摆出来。
    """
    detail: dict = {}
    if plan.target_kind == TARGET_MANAGED:
        managedenv.mark_incomplete(plan.project, "安装被取消")
    elif python:
        health = projectenv.probe_environment(python, plan.requirement.import_name or None)
        detail = {"health_ok": bool(health.get("ok")), "health_code": health.get("code", "")}
    _emit(plan.plan_id, STATE_CANCELLED, on_event, plan=plan, code=ERROR_CANCELLED, result=detail)
    return {"ok": False, "code": ERROR_CANCELLED, **detail}


# ---------------------------------------------------------------------------
# pip
# ---------------------------------------------------------------------------
#: 网络类失败的判据（**排在「找不到版本」之前**：断网时 pip 两句都会打，
#: 只看后一句会把「没网」报成「这个包不存在」）。
_NETWORK_MARKERS = (
    "temporary failure in name resolution",
    "network is unreachable",
    "could not fetch url",
    "failed to establish a new connection",
    "connection refused",
    "connection reset",
    "read timed out",
    "newconnectionerror",
    "proxyerror",
    "retrying (retry",
    "name or service not known",
    "getaddrinfo failed",
)
_CONFLICT_MARKERS = (
    "resolutionimpossible",
    "conflicting dependencies",
    "cannot install",
    "dependency conflicts",
)


def classify_pip_failure(text: str) -> str:
    """pip 的失败输出 → 稳定 code。**每一条都要能给用户不同的下一步。**

    「没有适合的 wheel」与「根本没这个包」在 `--only-binary=:all:` 下的输出
    只差一句：pip 会列出它**看得见**的版本。`(from versions: none)` = index
    上没有这个包；列出了版本却仍然装不上 = 有源码没轮子。
    """
    low = (text or "").lower()
    if any(m in low for m in _NETWORK_MARKERS):
        return ERROR_NETWORK
    if any(m in low for m in _HASH_MARKERS):
        # `--require-hashes` 下的 hash 不符（U04）：与「找不到」「冲突」都不是一回事——
        # 下一步是核对锁文件，不是换源或改声明
        return ERROR_HASH_MISMATCH
    if any(m in low for m in _CONFLICT_MARKERS):
        return ERROR_CONFLICT
    if "could not find a version" in low or "no matching distribution" in low:
        return ERROR_NOT_FOUND if "from versions: none" in low else ERROR_REQUIRES_BUILD
    return ERROR_FAILED


def pip_install_argv(
    python: str, requirement: str, *, upgrade: bool = False, index_url: str | None = None
) -> list[str]:
    """安装命令——**唯一出处**，测试逐字节钉住。

    每一个参数都有理由：

    * `-m pip`：绝不用 PATH 上的 `pip`（那个 pip 属于哪个解释器全看 PATH）；
    * `--disable-pip-version-check` / `--no-input`：子进程里没人能回答提示；
    * `--only-binary=:all:`：一键路径**只装 wheel**。sdist 会调本机编译器、
      跑 build backend，十几分钟起步，失败原因完全在 Tavotto 的控制面之外；
    * **默认没有 `--upgrade`**：默认就是 pip 的 only-if-needed——往用户的科研
      环境里装一个包，不该顺手把 NumPy/SciPy 栈整体升级掉。只有用户在
      包管理里**明确点「升级」**（`upgrade=True`，目标只会是受管环境）才带上，
      而且升级策略仍是 pip 默认的 only-if-needed——升级它，不顺手升级它的依赖。

    * `index_url` 只有一个合法值：`PYPI_MIRROR_URL`（网络类失败、且用户没有自配源时的那一次重试，
      ADR 0111；`_run_pip_install` 是唯一传它的地方）。默认不带——先用官方 / 用户自己的配置。

    argv 是 list、`shell=False`；包名与版本已在 `depresolve.parse_requirement`
    过了严格语法，`-r` / `--index-url` / URL / 本地路径在那里就死了。
    """
    argv = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
    ]
    if index_url:
        argv += ["--index-url", index_url]
    if upgrade:
        argv.append("--upgrade")
    argv.append(requirement)
    return argv


def pip_uninstall_argv(python: str, distribution: str) -> list[str]:
    """卸载命令——唯一出处。`-y` 是因为子进程里没人能回答 pip 的确认提示；
    真正的确认发生在界面上（`create_package_job` 把依赖它的包报出来，用户点过
    才会走到这里）。"""
    return [
        str(python),
        "-m",
        "pip",
        "uninstall",
        "--disable-pip-version-check",
        "--no-input",
        "-y",
        distribution,
    ]


def _pip_install(
    python: str,
    requirement: str,
    cancel_ev: threading.Event,
    on_log,
    *,
    upgrade: bool = False,
    on_mirror=None,
    on_source=None,
) -> tuple[str, str]:
    """跑一次 pip install（网络类失败 / 官方源太慢时按 `_run_pip_install` 的规则至多再走一次镜像）。
    回 `("", 输出)` 表示成功，否则 `(错误码, 输出)`。"""
    if depresolve.parse_requirement(requirement) is None:
        # 第二道门：真正拼进 argv 之前再验一次形状。第一道在解析处，
        # 这一道挡的是「以后有人从别的路径构造出需求串」。
        return ERROR_REQUIREMENT_INVALID, f"需求串不合形状: {requirement!r}"
    LOG.info("pip install: %s%s", requirement, " (upgrade)" if upgrade else "")
    return _run_pip_install(
        lambda index_url: pip_install_argv(
            python, requirement, upgrade=upgrade, index_url=index_url
        ),
        python,
        cancel_ev,
        on_log,
        on_mirror=on_mirror,
        on_source=on_source,
    )


# ---------------------------------------------------------------------------
# PyPI 镜像回退（ADR 0111；慢 / 超时与预算：ADR 0112 §二）
#
# 国内网络直连 PyPI 常常连不上 / 读超时。先用官方 / 用户自己的配置装；**仅当**失败是网络类的、
# 且用户没有自配任何包源时，改用一个固定的镜像重试**一次**。用户配过源（index / extra-index /
# 离线 wheelhouse）一律不动——那是他明确说过「从哪装」。
# ---------------------------------------------------------------------------
#: 固定的一个镜像（清华 TUNA，PyPI 全量镜像，HTTPS）。不做镜像列表、不测速、不轮换。
PYPI_MIRROR_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
#: pip 的默认索引（没配任何源时 `pip install` 用的就是它）。判「用户配了自定义源」时与它比。
PYPI_DEFAULT_INDEX = "https://pypi.org/simple"

#: 在**目标解释器**里让 pip 自己把 `pip install` 的选项解析一遍，回生效的包源（JSON 一行）。不联网、不装东西。
#: 走的就是 `pip install` 的选项解析：配置文件按 pip 的覆盖顺序合并、`[install]` 压过 `[global]`、
#: `PIP_CONFIG_FILE` / `PIP_*` 环境变量都算——`pip config list` 的打印顺序不是覆盖顺序、`config get`
#: 不认 `PIP_CONFIG_FILE`（#767 实测，两轮 Codex P1 同一个根因），所以不再自己读配置。入口是 pip 的内部
#: `create_command`（19.3 起一直在）；导入 / 解析失败（pip 太旧、配置坏了）输出 `{"error": 类型名}`，
#: 调用方按「不知道」处理、不猜。**插件 `codex-plugin/mcp/server.py::_PIP_OPTIONS_PROBE` 是它的逐字镜像**
#: （插件 import 不到引擎；严格同源对，看护 `tests/test_pip_config_pair.py`）。
PIP_OPTIONS_PROBE = """\
import json
try:
    import pip
    from pip._internal.commands import create_command
    o, _ = create_command("install").parse_args([])
    print(json.dumps({
        "pip_version": pip.__version__,
        "index_url": o.index_url or "",
        "extra_index_urls": list(o.extra_index_urls or []),
        "no_index": bool(o.no_index),
        "find_links": list(o.find_links or []),
    }))
except BaseException as e:
    print(json.dumps({"error": type(e).__name__}))
"""


def parse_pip_options(text: str) -> dict | None:
    """`PIP_OPTIONS_PROBE` 的输出 → 选项字典；出错 / 认不出形状回 None（= 不知道）。"""
    for line in reversed((text or "").strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict) or "error" in data or "index_url" not in data:
            return None
        return data
    return None


def _same_index(a: str, b: str) -> bool:
    return (a or "").strip().rstrip("/").lower() == b.rstrip("/").lower()


def options_name_a_custom_index(opts: dict) -> bool:
    """pip 解析出的 install 选项是不是指向**自定义索引**：index-url 不是 PyPI 默认、或有 extra-index-url。
    插件 `server.options_name_a_custom_index` 是它的镜像（同源对）。"""
    return not _same_index(str(opts.get("index_url") or ""), PYPI_DEFAULT_INDEX) or bool(
        opts.get("extra_index_urls")
    )


def options_name_a_user_source(opts: dict) -> bool:
    """「用户说过从哪装」：自定义索引，或离线 wheelhouse（`no-index` / `find-links`）。绕开它去联网是违背意思表示。"""
    return (
        options_name_a_custom_index(opts)
        or bool(opts.get("no_index"))
        or bool(opts.get("find_links"))
    )


def pip_install_options(python: str) -> dict | None:
    """那个解释器的 `pip install` 此刻生效的包源选项（`PIP_OPTIONS_PROBE`）；问不到回 None。"""
    rc, out = _run([str(python), "-c", PIP_OPTIONS_PROBE], 30)
    if rc != 0:
        return None
    return parse_pip_options(out)


def user_package_source(python: str) -> bool | None:
    """这个环境的 pip 是不是配了**用户自己的包源**（`options_name_a_user_source`）。只回真假、不回地址。
    问不出来回 None——镜像回退把 None 当「配过」处理（宁可不换源）。环境变量与配置文件都由 pip 自己解析。"""
    opts = pip_install_options(python)
    return None if opts is None else options_name_a_user_source(opts)


#: 这次 pip install 用的是哪个包源（进日志、进进度 `pypi_source`）。闭集；不含地址——用户配的源只说「用户配置」。
PIP_SOURCE_PYPI = "pypi"  # 没有任何自配：pip 默认的官方 PyPI
PIP_SOURCE_USER = (
    "user_config"  # 用户配过（index / extra-index / no-index / find-links，环境变量或配置文件）
)
PIP_SOURCE_UNKNOWN = "unknown"  # 问不出来（按「配过」处理，不换源）
PIP_SOURCE_MIRROR = "tuna"  # 回退到 PYPI_MIRROR_URL 的那一次
PIP_SOURCES = (PIP_SOURCE_PYPI, PIP_SOURCE_USER, PIP_SOURCE_UNKNOWN, PIP_SOURCE_MIRROR)

#: 官方源「太慢」的判据（ADR 0112 §二；只在没有自配源、换得了源的第一次尝试上测）。取值理由：
#:   * 阿里云华东 Windows 实测 files.pythonhosted.org 约 20 KB/s，TUNA 13 MB/s；matplotlib + numpy + 依赖的
#:     wheel 合计约 30 MB——20 KB/s 要 25 分钟，15 分钟的总预算必然撞超时。
#:   * `PIP_SLOW_BPS` = 100 kB/s：官方源第一次尝试最多能用的 10 分钟（总预算 − `PIP_MIRROR_RESERVE_S`）在这个
#:     速度下约 60 MB，够装常见科学栈；比它慢就几乎注定在预算里装不完。正常宽带比它高一两个数量级，不会误换。
#:   * 判法：pip 每下一个文件先打一行 `Downloading <名字> (<大小>)`，下完才打下一行。这一行出现后过了
#:     `PIP_SLOW_GRACE_S`、且已经超过「大小 / PIP_SLOW_BPS」还没有下一行 → 这个文件的实际速度**一定**低于
#:     阈值（没下完就是证据，不是估计）。小文件（元数据几十 kB）在宽限期内下完，不参与判定。
#:   * 联网阶段（`Collecting` / `Downloading` / `Looking in indexes` / `Obtaining` 之后、`Installing collected
#:     packages` 之前）连续 `PIP_STALL_S` 没有新的一行也算太慢（索引页慢到这个份上，后面的 wheel 更等不起）；
#:     安装阶段不测（Windows 上杀软扫大 wheel 可以几分钟没输出，那不是网络）。
PIP_SLOW_BPS = 100_000
PIP_SLOW_GRACE_S = 30.0
PIP_STALL_S = 90.0
#: 预算（ADR 0112 §二）：`INSTALL_TIMEOUT_S` 是**这次安装**的总预算，两次尝试共用。换得了源时第一次最多用到
#: 「总预算 − 这个保底」为止，保证镜像那一次至少还有 5 分钟（TUNA 13 MB/s 下 30 MB 十几秒）；不换源时第一次用满。
PIP_MIRROR_RESERVE_S = 300.0

_PIP_DOWNLOADING_RE = re.compile(
    r"^\s*Downloading\s+\S+\s+\((\d+(?:\.\d+)?)\s*(bytes|B|kB|KB|MB|GB)\)", re.IGNORECASE
)
_PIP_UNIT = {"bytes": 1, "b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3}
_PIP_NETWORK_PHASE = ("collecting", "downloading", "looking in indexes", "obtaining")
_PIP_INSTALL_PHASE = ("installing collected packages",)

#: `_PipWatch.reason` 的闭集（进日志）。
PIP_SLOW_DOWNLOAD = "slow_download"
PIP_STALLED = "stalled"
PIP_FIRST_BUDGET = "first_attempt_budget"
PIP_SLOW_REASONS = (PIP_SLOW_DOWNLOAD, PIP_STALLED, PIP_FIRST_BUDGET)
#: 换源理由的闭集（进日志）：三种「太慢」+ 网络类失败 + 超时。
PIP_SWITCH_REASONS = (
    *PIP_SLOW_REASONS,
    ERROR_NETWORK,
    ERROR_TIMEOUT,
)


class _PipWatch:
    """官方源第一次尝试的测速（`_run_pip` 每 0.25 s 问一次 `verdict`）。判出来就是这次尝试的结局：
    `_run_pip` 杀掉 pip、回 `ERROR_TIMEOUT`，`reason` 说为什么（`PIP_SLOW_REASONS`）。"""

    def __init__(self, started: float, first_deadline: float):
        self.first_deadline = first_deadline
        self.last_line = started
        self.download: tuple[float, float] | None = None  # (这一行出现的时刻, 字节数)
        self.network_phase = True  # pip 起来先解析 / 下载
        self.reason = ""
        self.detail = ""

    def feed(self, line: str, now: float) -> None:
        self.last_line = now
        low = line.strip().lower()
        m = _PIP_DOWNLOADING_RE.match(line)
        if m:
            size = float(m.group(1)) * _PIP_UNIT[m.group(2).lower()]
            self.download = (now, size)
            self.network_phase = True
            return
        self.download = None
        if low.startswith(_PIP_INSTALL_PHASE):
            self.network_phase = False
        elif low.startswith(_PIP_NETWORK_PHASE):
            self.network_phase = True

    def verdict(self, now: float) -> str:
        if now >= self.first_deadline:
            self.reason = PIP_FIRST_BUDGET
        elif self.download is not None:
            since, size = self.download
            elapsed = now - since
            if elapsed >= PIP_SLOW_GRACE_S and elapsed > size / PIP_SLOW_BPS:
                self.reason = PIP_SLOW_DOWNLOAD
                self.detail = f"{size / 1e6:.1f} MB 的文件 {elapsed:.0f} s 没下完"
        elif self.network_phase and now - self.last_line >= PIP_STALL_S:
            self.reason = PIP_STALLED
            self.detail = f"联网阶段 {now - self.last_line:.0f} s 没有进展"
        return self.reason


def mirror_retry_warranted(code: str, user_source: bool | None) -> bool:
    """这次失败要不要改用镜像重试一次——纯函数。

    判据的主语：**这一次** pip 进程的结局（`_run_pip` 给的 code）与**这个环境**的 pip 配置。
    `ERROR_NETWORK` 只在 pip **退出码非零**且输出带网络特征（`_NETWORK_MARKERS`：DNS 失败 / 连不上 /
    读超时 / 代理错误 / pip 自己的 Retrying）时才会出现（`_run_pip` → `classify_pip_failure`）；退出码 0
    哪怕输出里有过 Retrying 也是成功，不重试。`ERROR_TIMEOUT`（ADR 0112 §二）：没有自配源时第一次尝试
    带着测速（`_PipWatch`）跑，太慢 / 用完第一次的预算就以它收场——那正是换源能解决的（实测 20 KB/s →
    13 MB/s）；配过源时不测速、第一次用满总预算，超时就是终局。取消 / 冲突 / 找不到 / hash 不符都不是换源
    能解决的。用户配过源（True）或问不出来（None）都不换。"""
    return code in (ERROR_NETWORK, ERROR_TIMEOUT) and user_source is False


def _run_pip_install(
    build_argv, python: str, cancel_ev: threading.Event, on_log, *, on_mirror=None, on_source=None
) -> tuple[str, str]:
    """装包的执行器：先按 `build_argv(None)`（官方 / 用户配置）跑；网络类失败、或（没有自配源时）官方源
    太慢 / 用完第一次的预算，按 `build_argv(PYPI_MIRROR_URL)` **再跑一次**，日志里写明用了镜像、
    `on_mirror(url)` 通知调用方记进进度 / 结果。两条 argv 出处（`pip_install_argv` / `pip_install_joint_argv`）
    都经这里。

    ADR 0112 §二：**先问 pip 配没配源**（在目标解释器里让 pip 解析一遍 install 选项，`PIP_OPTIONS_PROBE`，约 1 s），因为要在开始之前决定两件事——
    这次用的是哪个源（`on_source(PIP_SOURCES 之一)`、进日志）、第一次要不要带测速与分段预算。
    `INSTALL_TIMEOUT_S` 是两次尝试**共用**的总预算：换得了源时第一次最多用到「总预算 −
    `PIP_MIRROR_RESERVE_S`」，镜像那一次拿剩下的；换不了源时第一次用满。每次尝试的结局进 app.log。

    「用了镜像」的判据主语是**镜像那次 pip 进程**：只在它 `Popen` 成功之后（`_run_pip` 的 `on_started`）
    才写换源日志、记 `pypi_mirror` / 包源、写那句说明。起之前取消 / 起不来（`OSError`）都不记，如实回
    cancelled / failed；起来之后被取消照记——那次请求确实发往了镜像（Codex #743 两轮 P2）。"""
    started = time.time()
    deadline = started + INSTALL_TIMEOUT_S
    user_source = user_package_source(python)
    if user_source is False:
        source = PIP_SOURCE_PYPI
    elif user_source:
        source = PIP_SOURCE_USER
    else:
        source = PIP_SOURCE_UNKNOWN
    LOG.info("pip install：包源 %s", logsafe.known(source, PIP_SOURCES))
    if on_source is not None:
        on_source(source)
    watch = (
        _PipWatch(started, deadline - PIP_MIRROR_RESERVE_S) if source == PIP_SOURCE_PYPI else None
    )
    code, out = _run_pip(build_argv(None), cancel_ev, on_log, deadline=deadline, watch=watch)
    # 第一次的结局**先**进 app.log，再决定换不换源：换源的话两次尝试各一条结局（#745 Codex P2）
    _log_pip_outcome(code, source)
    if not mirror_retry_warranted(code, user_source):
        return code, out
    reason = (watch.reason if watch is not None else "") or code
    detail = watch.detail if watch is not None else ""
    if reason in PIP_SLOW_REASONS:
        note = f"\n默认的 Python 包源太慢（{detail or reason}），改用 PyPI 镜像 {PYPI_MIRROR_URL} 重试一次\n"
    else:
        note = f"\n连不上默认的 Python 包源，改用 PyPI 镜像 {PYPI_MIRROR_URL} 重试一次\n"
    mirror_started: list[bool] = []

    def _mirror_started() -> None:
        mirror_started.append(True)
        LOG.warning(
            "pip install 换源：官方 PyPI %s（%s）且未自配包源，改用 %s 重试一次（预算还剩 %.0f s）",
            logsafe.known(reason, PIP_SWITCH_REASONS),
            detail or "-",
            PYPI_MIRROR_URL,
            max(0.0, deadline - time.time()),
        )
        # 先记字段、再写日志那句：每条路的 `on_log` 都经 `_append_log` 推一次快照，这样带着那句说明的第一个
        # 快照就已经带着顶层 `pypi_mirror` / 包源——四条路（含联合准备的原地 / 换代）同一个字段、同一刻到达
        if on_mirror is not None:
            on_mirror(PYPI_MIRROR_URL)
        if on_source is not None:
            on_source(PIP_SOURCE_MIRROR)
        if on_log is not None:
            on_log(note)

    code, retry_out = _run_pip(
        build_argv(PYPI_MIRROR_URL),
        cancel_ev,
        on_log,
        deadline=deadline,
        on_started=_mirror_started,
    )
    _log_pip_outcome(code, PIP_SOURCE_MIRROR if mirror_started else source)
    return code, out + (note if mirror_started else "") + retry_out


def _log_pip_outcome(code: str, source: str) -> None:
    """一次 pip install 尝试的结局进 app.log：成功 INFO、失败 WARNING，都带稳定 code 与包源（闭集明文）。"""
    if not code:
        LOG.info("pip install 完成：包源 %s", logsafe.known(source, PIP_SOURCES))
        return
    LOG.warning(
        "pip install 失败：%s（包源 %s）",
        logsafe.known(code, LOGGED_ERROR_CODES),
        logsafe.known(source, PIP_SOURCES),
    )


def _pip_uninstall(
    python: str, distribution: str, cancel_ev: threading.Event, on_log
) -> tuple[str, str]:
    """跑一次 pip uninstall。包名同样要过语法关——它一样会进 argv。"""
    parsed = depresolve.parse_requirement(distribution)
    if parsed is None or parsed[1]:
        return ERROR_REQUIREMENT_INVALID, f"包名不合形状: {distribution!r}"
    LOG.info("pip uninstall: %s", distribution)
    return _run_pip(pip_uninstall_argv(python, distribution), cancel_ev, on_log)


def _run_pip(
    argv: list[str],
    cancel_ev: threading.Event,
    on_log,
    *,
    deadline: float | None = None,
    watch: "_PipWatch | None" = None,
    on_started=None,
) -> tuple[str, str]:
    """流式跑一条 pip 命令（install / uninstall 共用的唯一执行器）。

    可取消、有超时、日志逐行回调。**argv 由调用方的两个 `*_argv()` 出处拼好**，
    这里不再碰它的形状。起 pip 之前先看一眼取消：已经取消的不起（起了再杀，包可能已经写了一半）。
    `on_started()` 只在子进程**真起来之后**、读它的输出之前调一次（镜像回退据此才记「用了镜像」）；
    它抛异常不影响这次 pip。
    `deadline`（绝对时刻）缺省为现在 + `INSTALL_TIMEOUT_S`；`_run_pip_install` 传进来的是两次尝试共用的那一个。
    `watch` 判出「太慢」时同样杀掉、回 `ERROR_TIMEOUT`，理由在 `watch.reason`（ADR 0112 §二）。
    """
    if cancel_ev.is_set():
        return ERROR_CANCELLED, ""
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            # 受管环境里跑的 pip 把缓存放进数据目录（不是 `%LOCALAPPDATA%\pip`）；用户 venv 上的
            # 安装原样继承——那是用户自己的 pip（`runtime.owned_env`）。
            env=runtime.owned_env(argv[0]),
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return ERROR_FAILED, str(exc)
    if on_started is not None:
        try:
            on_started()
        except Exception:  # noqa: BLE001 — 通知失败不许让已经起来的 pip 变成孤儿
            LOG.warning("pip 启动回调异常", exc_info=True)

    chunks: list[str] = []
    if deadline is None:
        deadline = time.time() + INSTALL_TIMEOUT_S

    def _pump() -> None:
        for line in proc.stdout or ():
            chunks.append(line)
            if watch is not None:
                watch.feed(line, time.time())
            if on_log is not None:
                on_log(line)

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    while True:
        try:
            proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel_ev.is_set():
            _kill(proc)
            reader.join(timeout=2.0)
            return ERROR_CANCELLED, "".join(chunks)
        if time.time() > deadline or (watch is not None and watch.verdict(time.time())):
            _kill(proc)
            reader.join(timeout=2.0)
            return ERROR_TIMEOUT, "".join(chunks)
    reader.join(timeout=5.0)
    out = "".join(chunks)
    if proc.returncode != 0:
        return classify_pip_failure(out), out
    return "", out


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait(timeout=5.0)
    except (OSError, subprocess.SubprocessError):
        pass


def _run(argv: list[str], timeout: int) -> tuple[int, str]:
    """跑一条**只读探测**（`pip --version` / `pip check` / `pip freeze` / `pip config list` /
    问版本 / 盘点）：解释器后面一律插 `-B`（`runtime.probe_args`）——目标可能是用户的 venv，
    问一句不许把 .pyc 写回它的安装目录。装 / 卸走 `_run_pip`，不走这里。"""
    argv = [argv[0], *runtime.probe_args(), *argv[1:]]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=runtime.owned_env(argv[0]),
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 1, f"超时（{timeout}s）"
    except OSError as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def installed_version(python: str, distribution: str) -> str:
    """装完之后这个包的真实版本；问不出来回空串。

    包名已经过语法关，但仍然经 `argv` 传参、不进 f-string 拼的代码字符串。
    """
    rc, out = _run(
        [
            str(python),
            "-c",
            "import sys,importlib.metadata as m;print(m.version(sys.argv[1]))",
            distribution,
        ],
        60,
    )
    return out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""


# ---------------------------------------------------------------------------
# worker 自检（验证的第三层）
# ---------------------------------------------------------------------------
#: 自检脚本。**不碰用户项目**：临时目录里的一份最小脚本，只证明
#: 「这个解释器能起 Tavotto worker 并跑通一次 build」。
_SELFTEST_SCRIPT = """\
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("SelfTest.pdf")
"""
_SELFTEST_NAME = "tavotto_selftest.py"


def worker_self_test(python: str) -> dict:
    """真起一次 worker 跑通一次 build——验证的第三层。

    前两层（import 缺的那个包、import matplotlib）由 `probe_environment` 完成。
    第三层要回答的是**产品意义上**的问题：这个解释器能不能真的把一张 Figure
    捕获出来。import 得到不等于跑得起来（后端不对、字体缓存不可写、动态库在
    子进程里才崩，都只有真跑一次才看得见）。

    argv 走 `execspec.worker_argv`——worker 命令行的唯一出处，这里不另拼一份。
    """
    tmp = tempfile.mkdtemp(prefix="tavotto-selftest-")
    try:
        root = Path(tmp)
        (root / _SELFTEST_NAME).write_text(_SELFTEST_SCRIPT, encoding="utf-8")
        out_dir, sandbox = root / "out", root / "sandbox"
        out_dir.mkdir()
        sandbox.mkdir()
        spec = execspec.safe_spec(
            _SELFTEST_NAME, str(root), "__main__", interpreter=str(python), sandbox=str(sandbox)
        )
        argv = execspec.worker_argv(
            spec,
            worker_py=pool.WORKER_PY,
            out_dir=out_dir,
            runtime_args=runtime.worker_args(
                bundled=pool.same_python(python, runtime.bundled_python())
            ),
        )
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            # 与池里起 worker 同一份 env 判据（受管环境的 matplotlib 缓存落回数据目录）
            env=runtime.owned_env(python),
            creationflags=runtime.CREATE_NO_WINDOW,
        )
        try:
            # legacy 信封（worker 明确支持，手工调试用的就是它）：一条 build
            # 一条 shutdown，读到 EOF 即结束，不必在这里重建一套超时读线程。
            stdout, stderr = proc.communicate(
                '{"cmd": "build"}\n{"cmd": "shutdown"}\n', timeout=SELFTEST_TIMEOUT_S
            )
        except subprocess.TimeoutExpired:
            _kill(proc)
            return {"ok": False, "detail": f"worker 自检超时（{SELFTEST_TIMEOUT_S}s）"}
        for line in (stdout or "").splitlines():
            try:
                resp = json.loads(line)
            except ValueError:
                continue
            if isinstance(resp, dict) and "ok" in resp:
                if resp.get("ok"):
                    # `stems` 才是「捕获到几张图」——调用方据此断言 worker
                    # 是真跑通了，而不是「函数返回了 True」。
                    return {"ok": True, "figures": len(resp.get("stems") or {})}
                return {"ok": False, "detail": str(resp.get("error", ""))[:800]}
        return {"ok": False, "detail": (stderr or stdout or "")[-800:]}
    except OSError as exc:
        return {"ok": False, "detail": str(exc)[:800]}
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 进度与脱敏
# ---------------------------------------------------------------------------
#: pip 会在输出里打出 index 地址，而那条地址可能带凭据
#: （`https://user:token@pypi.example.com/simple`）。**一个字节都不许出门**。
_INDEX_RE = re.compile(
    r"(?i)(looking in indexes:|--index-url[= ]|--extra-index-url[= ]|"
    r"--trusted-host[= ])\s*\S+"
)
_URL_CRED_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+@")


def _sanitize(text: str) -> str:
    """安装日志脱敏：index 地址 / 凭据 / 个人路径 / 密钥。

    路径与密钥那两条走**诊断包同一份规则**（`diagnostics.redact_text`），
    不在这里再写一份；index 地址是 pip 特有的，归本模块。
    """
    text = _INDEX_RE.sub(r"\1 <index>", str(text or ""))
    text = _URL_CRED_RE.sub(r"\1<credentials>@", text)
    try:
        from . import diagnostics

        text = diagnostics.redact_text(text)
    except Exception:  # noqa: BLE001 — 脱敏不该拖垮安装
        pass
    return text


def custom_package_index(python: str) -> bool | None:
    """这个环境是不是配了自定义 index。**只回真假，绝不回地址**。

    诊断里有用（「装不上」经常就是内网 index 不通），而地址本身可能带凭据、
    也会泄漏用户所在机构。问不出来回 None。判据由 pip 自己解析（`pip_install_options`）。
    """
    opts = pip_install_options(python)
    return None if opts is None else options_name_a_custom_index(opts)


_LOG_MAX = 20_000


def _append_log(plan_id: str, line: str, on_event) -> None:
    with _lock:
        rec = _progress.get(plan_id)
        if rec is None:
            return
        rec["log"] = (rec["log"] + _sanitize(line))[-_LOG_MAX:]
        snapshot = dict(rec)
    if on_event is not None:
        on_event(snapshot)


def _note_mirror(progress_id: str, url: str, on_event) -> None:
    """这次安装改用了 PyPI 镜像（ADR 0111）：记在进度记录的 `pypi_mirror` 上——之后每一个快照（含终态）
    都带着它，界面与结果据此把「用了镜像」说出口。"""
    with _lock:
        rec = _progress.get(progress_id)
        if rec is None:
            return
        rec["pypi_mirror"] = url
        snapshot = dict(rec)
    if on_event is not None:
        on_event(snapshot)


def _note_source(progress_id: str, source: str, on_event) -> None:
    """这次安装此刻用的是哪个包源（`PIP_SOURCES`；ADR 0112 §二）：记在进度记录的 `pypi_source` 上，之后每个
    快照（含终态）都带着它，失败日志也按它说出来源。不含地址。"""
    with _lock:
        rec = _progress.get(progress_id)
        if rec is None:
            return
        rec["pypi_source"] = source
        snapshot = dict(rec)
    if on_event is not None:
        on_event(snapshot)


def _log_repair_failure(entry: str, progress_id: str, code: str) -> None:
    """四个线程入口的失败终态进 app.log（ADR 0112 §三）：稳定 code + 最后用的包源。2026-09-29 那台机器上
    `dependency_install_timeout` 只落在 environment.json，日志里一个字都没有。"""
    with _lock:
        rec = _progress.get(progress_id) or {}
        source = str(rec.get("pypi_source") or "-")
    LOG.warning(
        "%s失败：%s（包源 %s）",
        entry,
        logsafe.known(code, LOGGED_ERROR_CODES),
        logsafe.known(source, PIP_SOURCES),
    )


def _emit(
    plan_id: str,
    state: str,
    on_event,
    *,
    plan: RepairPlan | None = None,
    joint: "JointRepairPlan | None" = None,
    code: str = "",
    error: str | None = None,
    result: dict | None = None,
    pinned: dict | None = None,
    impact: dict | None = None,
) -> dict:
    with _lock:
        rec = dict(_progress.get(plan_id) or {"log": ""})
        rec.update(
            plan_id=plan_id, state=state, code=code, error=error, result=result or rec.get("result")
        )
        if pinned is not None:
            # 租约里复查到全局固定而失败：界面要的是那条固定（谁、来源、变量），
            # 只有 code 的话它给不出「恢复自动检测」那一步
            rec["pinned"] = pinned
        if plan is not None:
            rec.update(
                import_name=plan.requirement.import_name,
                distribution=plan.requirement.distribution,
                target_kind=plan.target_kind,
                script=plan.script,
                # 这一代真正要装的全部包（单包修复新建第一代时多于一个）：进度行按它说，不只说用户点的那个
                requirements=list(plan.requirements),
            )
            if state in (STATE_FAILED, STATE_CANCELLED):
                # 终态上说清「同一个需求这一轮还能不能再装」：pip 跑成之后（验证 / 自检期间取消、验证没过）
                # `_attempted` 已登记，再形成计划必然 `dependency_already_attempted`——界面据此不给「重试」
                # （Codex #709）。只看项目与需求、不看环境 key：宁可少给一次重试，也不给一颗必败的按钮
                req = plan.requirement.requirement()
                rec["retryable"] = not any(
                    k[0] == plan.project_id and k[2] == req for k in _attempted
                )
        if joint is not None:
            rec.update(
                target_kind=joint.target_kind,
                script=joint.script,
                requirements=list(joint.requirements),
                flow="joint",
            )
        # T06：授权的实际影响跟着进度走（终态上说清「这次授权是什么」），失败现场也按它冻结（`diagnostic_projection`）
        for source in (plan, joint):
            if source is not None:
                rec["impact"] = source.impact
                rec["impact_digest"] = source.impact_digest
        if impact is not None:
            # 供应私有 Python 之后重算出的、与用户确认的不同的实际影响：终态上带着它与新摘要，界面 / MCP 据此重新披露
            rec["impact"] = impact
            rec["impact_digest"] = impact_digest(impact)
        # 最后一个非终态的阶段：失败 / 取消的终态覆盖了 `state`，「坏在哪一步」靠它
        if state not in TERMINAL_STATES:
            rec["stage"] = state
        if plan_id in _committed:
            rec["committed"] = True
        _progress[plan_id] = rec
        snapshot = dict(rec)
        listeners = list(_listeners.get(plan_id, ()))
        if state in TERMINAL_STATES:
            _listeners.pop(plan_id, None)
    if on_event is not None:
        on_event(snapshot)
    for listener in listeners:
        try:
            listener(snapshot)
        except Exception:  # noqa: BLE001 — 旁观者的异常不能影响安装本身
            LOG.exception("依赖进度监听失败")
    return snapshot


#: 依赖作业走过的非终态阶段（进度记录 `stage`）。
STAGES = (
    STATE_PREPARING,
    STATE_DOWNLOADING_PYTHON,
    STATE_CREATING_ENV,
    STATE_INSTALLING,
    STATE_VERIFYING,
)


def diagnostic_projection(rec: dict) -> dict:
    """一次依赖作业终局的**白名单**投影（T06；任务诊断 `taskdiag.KIND_DEPENDENCY`）。输入是 `_emit` 的终态快照，
    逐字段挑：闭集枚举、稳定码、计数、布尔、不透明摘要。不进来的：包名与版本约束原文（只有个数）、解释器 / 项目 /
    脚本路径、`error` 文字与 pip 日志、镜像地址、`result` 里的路径与已装版本表。"""
    impact = rec.get("impact") or {}
    private = impact.get("private_python") or {}
    result = rec.get("result") or {}
    return taskdiag.clean(
        {
            "snapshot_version": taskdiag.SNAPSHOT_VERSION,
            "kind": taskdiag.KIND_DEPENDENCY,
            "attempt_id": taskdiag.ident(rec.get("plan_id")),
            "outcome": taskdiag.closed(rec.get("state"), TERMINAL_STATES),
            "target": {
                "kind": taskdiag.closed(rec.get("target_kind"), TARGETS),
                "scope": taskdiag.closed(
                    impact.get("scope"), (SCOPE_MANAGED_GENERATION, SCOPE_PROJECT_VENV_IN_PLACE)
                ),
                "flow": taskdiag.closed(rec.get("flow") or FLOW_SINGLE, (FLOW_JOINT, FLOW_SINGLE)),
            },
            "impact": {
                "version": taskdiag.count(impact.get("impact_version")),
                "installs": taskdiag.count(len(impact.get("installs") or ())),
                "constraints": taskdiag.count(len(impact.get("constraints") or ())),
                "require_hashes": taskdiag.flag(impact.get("require_hashes")),
                "creates_environment": taskdiag.flag(impact.get("creates_environment")),
                "modifies_user_environment": taskdiag.flag(impact.get("modifies_user_environment")),
                "private_python_download": taskdiag.flag(bool(private)),
                "private_python_bytes": taskdiag.count(private.get("download_bytes")),
                "rollback": taskdiag.closed(
                    impact.get("rollback"), (ROLLBACK_GENERATION_ATOMIC, ROLLBACK_NONE)
                ),
                "digest": taskdiag.digest(rec.get("impact_digest")),
            },
            "error": {"code": taskdiag.closed(rec.get("code") or None, LOGGED_ERROR_CODES)},
            "stage": {
                "last": taskdiag.closed(rec.get("stage"), STAGES),
                "committed": taskdiag.flag(bool(rec.get("committed"))),
            },
            "result": {
                "activated": taskdiag.flag(result.get("activated")),
                "health_ok": taskdiag.flag(result.get("health_ok")),
            },
            "package_source": {
                "source": taskdiag.closed(rec.get("pypi_source"), PIP_SOURCES),
                "mirror_used": taskdiag.flag(bool(rec.get("pypi_mirror"))),
            },
            "retryable": taskdiag.flag(rec.get("retryable")),
        }
    )


def record_task_diagnostic(project_id, snapshot: dict) -> None:
    """依赖作业到终局：冻结现场（失败的、取消的、成功的都记，成功的只为对照重试）。`project_id` 是调用方（app）
    的项目 id——本模块自己的项目指纹是另一套 id，诊断端点按前者认领。从不抛：登记失败不是安装的一部分。"""
    try:
        state = snapshot.get("state")
        if state not in TERMINAL_STATES or not snapshot.get("plan_id"):
            return
        taskdiag.STORE.record(
            project_id,
            taskdiag.KIND_DEPENDENCY,
            snapshot["plan_id"],
            diagnostic_projection(snapshot),
            outcome=state,
            failed=state == STATE_FAILED,
            subject={("script", str(snapshot.get("script") or ""))},
        )
    except Exception:  # noqa: BLE001
        LOG.debug("依赖诊断快照登记失败", exc_info=True)


def diagnostics_state(project: str | Path) -> dict:
    """诊断包里的 `dependency_repair` 一段。**不含路径、不含 index 地址**。"""
    root = str(Path(project))
    managed = managedenv.state(root)
    with _lock:
        rounds = {
            s: n for (pid, s), n in _rounds.items() if pid == managedenv.project_fingerprint(root)
        }
    return {
        "rounds": rounds,
        "managed_environment": managed,
        "max_rounds": MAX_DEPENDENCY_REPAIR_ROUNDS,
        # 只给份数：快照文件名里有时间戳与操作名，内容（freeze 全文）不进诊断
        "snapshots": len(managedenv.list_snapshots(root)),
    }


# ---------------------------------------------------------------------------
# 最近的缺依赖现场（诊断包用）
#
# 2026-09-28 Windows 实测：渲染报 missing_dependency 时，错误响应里有缺的模块名、体检过的系统
# 候选（3.7.6 装了但版本不支持、WindowsApps 的 python3.EXE 起不来）与修复 offer，但导出的诊断包
# 一样都没有——那些结论只活在那一次 HTTP 响应里，模块名只剩 app.log 里一行文字。这里把**已经算好
# 的**结论记一份（进程内存、有上限、不写盘），诊断包导出时再按出门规则换形
# （`diagnostics._missing_dependency_for_export`）。**不为诊断重新体检任何解释器**。
#
# 这里存的是原值（路径未脱敏）：脱敏只在出口做，与 `render.worker_logs` 按未脱敏的项目目录取会话
# 同一条纪律。`reset_state()` 不清它——用户点「重试」之后，上一次为什么失败正是要看的东西。
# ---------------------------------------------------------------------------
#: 进程里最多记几条（跨项目共用；诊断包只取当前项目的）。
MISSING_DEPENDENCY_EVIDENCE_LIMIT = 8
_missing_evidence: deque[dict] = deque(maxlen=MISSING_DEPENDENCY_EVIDENCE_LIMIT)


def _constants(prefix: str) -> frozenset[str]:
    """本模块 `<prefix>*` 常量的值（源码里的闭集）。调用时取：有几组 `ERROR_*` 定义在本段之后。

    offer 里会出现的 code / 目标 kind 按它放行。诊断包不能 import 本模块（见
    `diagnostics._VETTED_VALUE`），所以这几个值的放行在记录这一刻、由产出方做。"""
    return frozenset(
        v for k, v in list(globals().items()) if k.startswith(prefix) and isinstance(v, str)
    )


def _evidence_value(value, allowed) -> str:
    """闭集成员 / 空串原样，其余 `str:<sha1 前 10 位>`（与诊断包的哈希同一种写法）。"""
    text = str(value or "")
    if not text or text in allowed:
        return text
    return "str:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def note_missing_dependency(
    project: str | Path,
    *,
    script: str,
    module: str,
    python_source: str,
    project_env: dict | None,
    offer: dict | None,
) -> None:
    """记一次缺依赖现场。只抄诊断要的字段（深拷贝出来，调用方之后改自己的 dict 不影响这里）。"""
    detail = project_env if isinstance(project_env, dict) else {}
    repair = offer if isinstance(offer, dict) else {}
    ev = _evidence_value
    codes, kinds = _constants("ERROR_"), _constants("TARGET_")
    requirement = repair.get("requirement") if isinstance(repair.get("requirement"), dict) else {}
    record = {
        "at": time.time(),
        "project_id": managedenv.project_fingerprint(project),
        "script": str(script or ""),
        "module": str(module or ""),
        "python_source": str(python_source or ""),
        "project_env_code": str(detail.get("code") or ""),
        "system": [
            {
                "python": str(e.get("python") or ""),
                "source": str(e.get("source") or ""),
                "ok": bool(e.get("ok")),
                "code": str(e.get("code") or ""),
                "support": str(e.get("support") or ""),
                "python_version": str(e.get("python_version") or ""),
                "matplotlib_version": str(e.get("matplotlib_version") or ""),
                "requested_module_ok": e.get("requested_module_ok"),
            }
            for e in (detail.get("system") or [])
            if isinstance(e, dict)
        ],
        "offer": None
        if not repair
        else {
            "code": ev(repair.get("code"), codes),
            "resolution_source": ev(
                requirement.get("resolution_source"), depresolve.INSTALLABLE_SOURCES
            ),
            "installable": bool(requirement.get("installable")),
            "rounds_remaining": repair.get("rounds_remaining"),
            "pinned_source": str((repair.get("pinned") or {}).get("source") or ""),
            "targets": [
                {
                    "kind": ev(t.get("kind"), kinds),
                    "available": t.get("available"),
                    "reason": ev(t.get("reason"), codes),
                    "modifies_user_environment": bool(t.get("modifies_user_environment")),
                    "creates_environment": bool(t.get("creates_environment")),
                }
                for t in (repair.get("targets") or [])
                if isinstance(t, dict)
            ],
        },
    }
    with _lock:
        _missing_evidence.append(record)


def recent_missing_dependencies(project: str | Path) -> list[dict]:
    """这个项目最近的缺依赖现场（旧 → 新，最多 `MISSING_DEPENDENCY_EVIDENCE_LIMIT` 条）。"""
    pid = managedenv.project_fingerprint(project)
    with _lock:
        return [copy.deepcopy(r) for r in _missing_evidence if r["project_id"] == pid]


def clear_missing_dependency_evidence() -> None:
    """测试之间清空（用户侧没有入口：进程退出即消失）。"""
    with _lock:
        _missing_evidence.clear()


# ---------------------------------------------------------------------------
# 用户包管理（设置 → 包管理；ADR 0038）
#
# 与上面的「缺包修复」共用**同一个执行器、同一把环境锁、同一份脱敏、同一个
# 自检**——这里没有第二套 pip 调用。多出来的只有：
#
#   * 目标环境**只有一种**：这个项目的 Tavotto 受管环境。用户的 `.venv` 与
#     内置 runtime 都不在这里出现（前者是他的研究环境，后者是「重装就能修」
#     的前提）；
#   * 三种操作 install / update / uninstall，每一种都先形成一个 **job**（不改
#     任何东西，把「会发生什么」交给界面确认），再按 job_id 执行——与 plan /
#     install 两步同一条防 TOCTOU 的纪律；
#   * 「内置」与「用户装的」的分界由**依赖闭包**算出来（`protected_distributions`）：
#     matplotlib 及它拉进来的一切、pip 自身，卸掉任何一个环境就废了。
# ---------------------------------------------------------------------------
OP_INSTALL = "install"
OP_UPDATE = "update"
OP_UNINSTALL = "uninstall"
PACKAGE_OPS = (OP_INSTALL, OP_UPDATE, OP_UNINSTALL)

#: 装包前至少要有这么多空闲磁盘。科研 wheel 动辄几十 MB，解压再翻一倍；
#: 磁盘写满时 pip 留下的半个包比「装不上」难查得多。
MIN_FREE_BYTES = 200 * 1024 * 1024

#: 永远算「内置」的：受管环境的基础栈 + 包管理器本身。它们的依赖闭包由
#: `protected_distributions()` 在目标解释器里现算，这里不抄一份 matplotlib
#: 的依赖清单（抄了就会与真实依赖漂移）。
_ALWAYS_PROTECTED = tuple(
    depresolve.normalize_distribution(n) for n in managedenv.BASE_PACKAGES
) + (
    "pip",
    "setuptools",
    "wheel",
)

INVENTORY_TIMEOUT_S = 60
FREEZE_TIMEOUT_S = 60

#: 包状态（界面按它换文案，不解析版本串）。
PKG_INSTALLED = "installed"  # 账上有、环境里也有、版本一致
PKG_MISSING = "missing"  # 账上有、环境里没有（被人手工删了 / 环境重建过一半）
PKG_CHANGED = "changed"  # 账上记的版本与环境里的不一致（别的安装顺手升过它）
PKG_PLANNED = "planned"  # 环境还没建：创建时会装上（内置清单专用）

_jobs: dict[str, "PackageJob"] = {}


@dataclasses.dataclass(frozen=True)
class PackageJob:
    """一次包操作的完整描述——执行端只认它（与 `RepairPlan` 同一条纪律）。"""

    job_id: str
    project: str
    project_id: str
    op: str
    distribution: str
    #: 交给 pip 的那一个参数（install / update：`lmfit>=1.3`；uninstall：包名）
    requirement: str
    python: str  # 受管环境还没建时为空
    env_fingerprint: str
    creates_environment: bool
    #: 卸载时：账上哪些用户包声明依赖它（界面据此二次确认）
    dependents: tuple[str, ...]
    created_at: float
    expires_at: float

    def to_payload(self) -> dict:
        return {
            "job_id": self.job_id,
            "op": self.op,
            "distribution": self.distribution,
            "requirement": self.requirement,
            "creates_environment": self.creates_environment,
            "dependents": list(self.dependents),
            "network_required": self.op != OP_UNINSTALL,
            "expires_at": int(self.expires_at),
        }


# --------------------------------------------------------------- 清单
_INVENTORY_SCRIPT = """\
import json, re, sys
import importlib.metadata as m


def norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()


out = {}
for d in m.distributions():
    name = d.metadata.get("Name") or ""
    if not name:
        continue
    reqs = []
    for r in d.requires or ():
        # `pytest; extra == "test"` 是可选依赖，没装进来；其余标记（python_version
        # 之类）保守地算进去——多保护一个包比少保护一个安全
        if "extra ==" in r or "extra==" in r:
            continue
        mo = re.match(r"\\s*([A-Za-z0-9][A-Za-z0-9._-]*)", r)
        if mo:
            reqs.append(norm(mo.group(1)))
    out[norm(name)] = {"name": name, "version": d.version or "", "requires": sorted(set(reqs))}
sys.stdout.write(json.dumps(out))
"""


def inventory(python: str) -> dict[str, dict] | None:
    """目标解释器里装了什么、谁依赖谁——**一次子进程**拿全。

    键是 PEP 503 规范化名；问不出来回 None（解释器起不来 / 超时）。
    走 `importlib.metadata` 而不是 `pip list`：后者的输出格式不是契约。
    """
    rc, out = _run([str(python), "-I", "-c", _INVENTORY_SCRIPT], INVENTORY_TIMEOUT_S)
    if rc != 0:
        return None
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return data if isinstance(data, dict) else None


def protected_distributions(inv: dict[str, dict] | None) -> set[str]:
    """不许卸载的那一批：基础栈 + 它们的**传递依赖闭包** + pip 自身。

    闭包在目标环境里现算：matplotlib 依赖什么由装着的那个版本说了算，
    源码里抄一份清单的话 matplotlib 换版本就漂了。
    """
    protected = set(_ALWAYS_PROTECTED)
    if not inv:
        return protected
    stack = list(protected)
    while stack:
        name = stack.pop()
        for dep in (inv.get(name) or {}).get("requires", ()):
            if dep not in protected:
                protected.add(dep)
                stack.append(dep)
    return protected


def _dependents_of(name: str, inv: dict[str, dict] | None, candidates: list[str]) -> list[str]:
    """`candidates`（账上的用户包）里谁**直接或间接**依赖 `name`。"""
    if not inv:
        return []
    target = depresolve.normalize_distribution(name)
    out: list[str] = []
    for cand in candidates:
        key = depresolve.normalize_distribution(cand)
        if key == target:
            continue
        seen: set[str] = set()
        stack = [key]
        hit = False
        while stack and not hit:
            cur = stack.pop()
            for dep in (inv.get(cur) or {}).get("requires", ()):
                if dep == target:
                    hit = True
                    break
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        if hit:
            out.append(cand)
    return out


def _proxy_configured() -> bool:
    return any(
        os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
    )


def list_managed_packages(project: str | Path | None) -> dict:
    """设置 → 包管理那一页要的全部事实（**不装任何东西**）。

    `capability.available=False` 时界面显示原因而不是一片空表：没打开项目 /
    这台机器建不了环境 / 环境正在改动。受管环境存在时起**一次**子进程盘点
    版本与依赖图（几百毫秒）；不存在时一个子进程都不起。
    """
    if project is None:
        return {
            "capability": {"available": False, "reason": "no_project"},
            "environment": None,
            "builtin": [],
            "builtin_source": "",
            "user": [],
            "busy": False,
        }
    root = str(Path(project))
    managed = managedenv.state(root)
    python = managedenv.python_of(root)
    inv = inventory(python) if python else None
    protected = protected_distributions(inv)
    accounted = [
        e
        for e in (managedenv.read_manifest(root) or {}).get("installed_by_tavotto") or []
        if isinstance(e, dict) and e.get("distribution")
    ]
    accounted_names = [str(e["distribution"]) for e in accounted]

    # ---- 内置：受管环境在就按依赖闭包现算；不在就退到内置 runtime 的清单 ----
    builtin: list[dict] = []
    builtin_source = ""
    if inv is not None:
        builtin_source = "managed_env"
        for key in sorted(protected):
            rec = inv.get(key)
            if rec is None:
                if key in _ALWAYS_PROTECTED and key not in ("setuptools", "wheel"):
                    builtin.append({"name": key, "version": "", "status": PKG_MISSING})
                continue
            builtin.append(
                {"name": rec["name"], "version": rec["version"], "status": PKG_INSTALLED}
            )
    else:
        info = runtime.manifest()
        if info and isinstance(info.get("packages"), dict):
            builtin_source = "bundled_runtime"
            for name, ver in sorted(info["packages"].items()):
                builtin.append({"name": str(name), "version": str(ver), "status": PKG_INSTALLED})
        else:
            builtin_source = "planned"
            for name in managedenv.BASE_PACKAGES:
                builtin.append({"name": name, "version": "", "status": PKG_PLANNED})

    # ---- 用户装的：账为主、盘点为证 ----
    user: list[dict] = []
    for e in accounted:
        dist = str(e["distribution"])
        key = depresolve.normalize_distribution(dist)
        rec = inv.get(key) if inv else None
        recorded = str(e.get("resolved_version") or "")
        actual = str(rec["version"]) if rec else ""
        if inv is None:
            status = ""  # 环境不在 / 问不出来：不谎报「已安装」
        elif rec is None:
            status = PKG_MISSING
        elif recorded and actual and recorded != actual:
            status = PKG_CHANGED
        else:
            status = PKG_INSTALLED
        user.append(
            {
                "distribution": dist,
                "requested_specifier": str(e.get("requested_specifier") or ""),
                "installed_version": actual,
                "recorded_version": recorded,
                "reason": str(e.get("reason") or managedenv.REASON_MISSING_DEPENDENCY),
                "status": status,
                # 账上是用户包、闭包里却是基础栈的依赖（用户装了个 numpy）：
                # 卸掉它会拆掉 matplotlib，界面要把它标成只读
                "protected": key in protected,
                "required_by": _dependents_of(dist, inv, accounted_names),
                "installed_at": int(e.get("at") or 0),
            }
        )

    busy = envlease.is_mutating_key(_env_key(TARGET_MANAGED, "", root)) or (
        bool(python) and envlease.is_mutating(python)
    )
    available = True if managed["exists"] else managed_available()
    capability = {"available": available is not False, "reason": ""}
    if available is False:
        capability = {"available": False, "reason": ERROR_MANAGED_UNAVAILABLE}
    elif managed["exists"] and inv is None:
        capability = {"available": True, "reason": ERROR_MANAGED_BROKEN}
    elif managed["state"] == managedenv.STATE_INCOMPLETE and (managedenv.read_manifest(root) or {}):
        capability = {"available": True, "reason": "managed_env_incomplete"}

    return {
        "capability": capability,
        "environment": {
            **managed,
            "python_version": managed["python_version"],
            "in_use": pool.same_python(projectenv.remembered(root), python) if python else False,
        },
        "builtin": builtin,
        "builtin_source": builtin_source,
        "user": user,
        "busy": busy,
        # 三个网络事实（只回真假，绝不回地址）：装包要联网 / 走了代理 / 配了私有源
        "network": {
            "proxy": _proxy_configured(),
            "custom_index": custom_package_index(python) if python else None,
        },
        "snapshots": len(managedenv.list_snapshots(root)),
        # 卸载没有回滚这件事要在界面上**说出来**（ADR 0019 §八）
        "rollback": "snapshot_only",
    }


# --------------------------------------------------------------- 查找
#
# 「这个包叫什么、有哪些版本」——设置 → 包管理的搜索框那一半（ADR 0038
# 2026-09-07 修订）。**只读：一个字节都不装**，也不碰受管环境的磁盘。
#
# 为什么走 `pip index versions` 而不是自己请求 `https://pypi.org/pypi/<name>/json`：
#
#   * **查找必须问安装会问的那个源。** 用户配了镜像 / 内网 index（`pip.conf`、
#     `PIP_INDEX_URL`）时，直连 pypi.org 会给出与 `pip install` 不一致的答案：
#     「PyPI 上没有这个名字」而 pip 装得上，或者反过来。同一个问题只该有一个
#     出处，而这里的出处就是 pip 自己的索引配置。
#   * **代理 / 离线配置也一起继承**，我们不必在 Flask 里再实现一遍 proxy、
#     TLS、重试、私有源认证。
#   * **实测（2026-09-07，本机）**：`pip index versions numpy` 冷启 4.2 s、
#     热 0.7 s；同一个 numpy 的 `pypi.org/pypi/numpy/json` 有几十 MB，10 s 都
#     读不完（`TimeoutError`）。JSON 那条路在大包上根本走不通。
#
# 代价写在明处：`pip index` 的输出不是契约（与 `inventory()` 刻意不解析
# `pip list` 是同一条纪律的反面）。所以 ① 解析只认一行固定前缀，认不出一律
# `package_lookup_failed`，**绝不回一个空版本表冒充「找到了」**；② 查找失败
# 从不影响安装——用户照样可以直接输入包名装，这条功能是纯增量的。
#: pip 自己的 socket 超时与重试。**`--retries 1` 是判据的一部分**：
#: `--retries 0` 时 pip 连不上索引也只打一句 `No matching distribution found`，
#: 与「这个包不存在」逐字相同（2026-09-07 实测），离线就再也认不出来了。
LOOKUP_PIP_TIMEOUT_S = 3
LOOKUP_PIP_RETRIES = 1
#: 整个子进程的墙上时间预算。算术：pip 启动 ~0.3 s + 两次 socket 等待
#: （3 s × (1 + 重试 1)）= 6.3 s，留一点余量给慢磁盘。
LOOKUP_TIMEOUT_S = 8
#: 版本表的上限。numpy 有 120 个版本，几百个是想得到的上界；截断只是防一个
#: 畸形索引把响应撑爆，正常包一个版本都不会少。
LOOKUP_MAX_VERSIONS = 300

#: `pip index versions` 的两行输出前缀。**只认这两个**，认不出就是解析失败。
_AVAILABLE_PREFIX = "available versions:"
_INSTALLED_PREFIX = "installed:"

#: 进 argv 的包名允许出现的**全部**字符。PEP 503 归一之后
#: （`depresolve.normalize_distribution` 把 `[-_.]+` 折成 `-` 再小写）剩下的
#: 就只有这些——比 `_NAME_RE` 窄一档是刻意的：进命令行的那个串已经归过一次。
_ARGV_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"
#: 首字符另算一张表：**必须是字母或数字**。挡参数注入的就是这一条——`-` 开头
#: 的串会被 pip 当成选项（`--index-url=http://evil/simple` 这一族），而位置参数
#: 在不在最后一位与它无关。
_ARGV_NAME_FIRST_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def argv_package_name(name: str) -> str:
    """把包名**按上面那两张常量表重拼一遍**，拼不出来就拒。

    为什么是「重拼」而不是「校验一下放行」——两条理由，缺一条都不足以这么写：

    * **校验与使用是两个动作**，中间隔着的每一行代码都可能换掉那个值
      （`lookup_package` 里「归一化之后再过一次同一道语法」就是这条纪律的
      上一版）。重拼把两个动作合成一个：出去的那个串**由常量表拼出来**，
      它含什么字符不取决于谁在上游验过什么。
    * 它同时是给静态分析看的：CodeQL 的 `py/command-line-injection` 报的是
      「用户输入流进了子进程 argv」这条**数据流**，而不是「这里真能注入」。
      正则校验在它的模型里不是净化器（判据落在另一个值上），所以那条告警
      不会因为上游多验一次而消失。从常量表取字符则把那条流真的切断了——
      这不是为了讨好扫描器改代码：切断的是同一条真实的因果链。

    **不做替换、不做截断**：认不出的字符一律抛 `RepairError`。悄悄改掉用户
    输入的名字会让界面上的名字与真正查的那个身份对不上，而那正是
    `lookup_package` 自己做归一化（而不是让 pip 做）的理由。
    """
    text = str(name or "")
    if not text:
        raise RepairError(ERROR_REQUIREMENT_INVALID, "空的包名不能进命令行")
    out: list[str] = []
    for i, ch in enumerate(text):
        table = _ARGV_NAME_FIRST_CHARS if i == 0 else _ARGV_NAME_CHARS
        pos = table.find(ch)
        if pos < 0:
            raise RepairError(ERROR_REQUIREMENT_INVALID, "这个包名里有不能进命令行的字符")
        out.append(table[pos])
    return "".join(out)


def pip_index_argv(python: str, name: str) -> list[str]:
    """查找命令——**唯一出处**，测试逐字节钉住。

    * `-m pip`：绝不用 PATH 上的 `pip`（那个 pip 属于哪个解释器全看 PATH）；
    * `--disable-pip-version-check`：它自己要再发一次网络请求，实测能把
      一次查找从 0.7 s 拖到 10.6 s；
    * `--no-input`：私有源要密码时子进程里没人能回答，只会挂着；
    * `--retries` / `--timeout`：见上面常量的注释，**重试次数是判据的一部分**；
    * `--`：选项解析到此为止。有了它，「名字会不会被当成选项」这件事就不再
      依赖名字本身长什么样——判据从「上游验过了」变成「pip 不可能这么解释」。
      2026-09-07 实测 pip 25.3：`pip index versions -- <name>` 与不带 `--`
      逐字同输出，两个位置参数才会报 `You need to specify exactly one argument`
      （证明 `--` 被吃掉了、没当成第二个参数）；
    * 包名放**最后**，且由 `argv_package_name` 从常量字母表重拼出来。

    名字拼不出来时**抛异常，不返回一个凑合的 argv**：这个函数是 argv 的唯一
    出处，让它有能力回一个不合规的 argv 等于把上面那句保证作废。
    """
    return [
        str(python),
        "-m",
        "pip",
        "index",
        "versions",
        "--disable-pip-version-check",
        "--no-input",
        "--retries",
        str(LOOKUP_PIP_RETRIES),
        "--timeout",
        str(LOOKUP_PIP_TIMEOUT_S),
        "--",
        argv_package_name(name),
    ]


def _run_lookup(argv: list[str]) -> tuple[int, str, bool]:
    """跑一次查找子进程 → `(退出码, 合并输出, 是不是超时)`。

    比 `_run()` 多回一个 `timed_out` 是有理由的：`_run` 把超时压成
    `(1, "超时（8s）")`，而判「是不是超时」去匹配那句中文属于**拿判据去匹配
    散文**——换一句话、翻译一次，判据就静默失效。这里给结构化的信号。

    测试的**唯一注入点**也是它：包查找的用例一律 monkeypatch 这个函数，
    CI 里一次网络请求都不发。

    解释器后面插 `-B`（只读查询，与 `_run` 同一条）；argv 的形状仍由 `pip_index_argv` 独家产出。
    """
    if argv:
        argv = [argv[0], *runtime.probe_args(), *argv[1:]]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LOOKUP_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            # 查找只在受管环境上跑：pip 的 HTTP 缓存落回数据目录（`runtime.owned_env`）
            env=runtime.owned_env(argv[0]) if argv else None,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 1, "", True
    except OSError as exc:
        return 1, str(exc), False
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), False


def classify_lookup_failure(text: str) -> str:
    """查找失败的输出 → 稳定 code。

    **网络判据排在「没有这个包」之前**，与 `classify_pip_failure` 同一条理由
    再加一条更强的：`pip index versions` 在「索引连不上」与「索引上没有这个
    名字」两种情况下打出的**最后一行完全一样**（`ERROR: No matching
    distribution found for X`，2026-09-07 实测），唯一的差别是前者多一行连接
    失败的重试警告。

    于是有一档刻意的不对称：网络抖了一下、但重试成功并确实查到「没有这个包」
    时，我们会报 `offline`。**这个方向是选的**——把「网断了」说成「PyPI 上
    没有这个名字」会让用户去改一个本来就对的包名，而反过来只是让他重试一次。
    """
    low = (text or "").lower()
    if any(m in low for m in _NETWORK_MARKERS):
        return ERROR_LOOKUP_OFFLINE
    if "no matching distribution" in low or "could not find a version" in low:
        return ERROR_LOOKUP_NOT_FOUND
    return ERROR_LOOKUP_FAILED


def parse_index_versions(text: str) -> tuple[list[str], str]:
    """`pip index versions` 的输出 → `(版本表, 环境里已装的版本)`。

    版本表按 pip 给的顺序（新的在前）原样保留，**不排序**：比较版本号要
    PEP 440 的规则，而那正是 pip 已经替我们做过的事，自己再排一遍只会排错。
    认不出「Available versions:」那一行时回空表——调用方据此报解析失败，
    绝不把空表当成「查到了，但一个版本都没有」。
    """
    versions: list[str] = []
    installed = ""
    for line in (text or "").splitlines():
        low = line.strip().lower()
        if not versions and low.startswith(_AVAILABLE_PREFIX):
            body = line.strip()[len(_AVAILABLE_PREFIX) :]
            versions = [v.strip() for v in body.split(",") if v.strip()]
        elif not installed and low.startswith(_INSTALLED_PREFIX):
            rest = line.strip()[len(_INSTALLED_PREFIX) :].strip().split()
            installed = rest[0] if rest else ""
    return versions, installed


def lookup_package(project: str | Path, name: str) -> dict:
    """按名字问一次索引源：这个包有哪些版本。**不装任何东西。**

    只按**名字**查，不做模糊搜索：PyPI 早已下线全文搜索 API，而「猜一个相近
    的名字」正是抢注攻击的入口（同一条理由让 `depresolve` 没有「同名试试看」
    那一档）。名字按 PEP 503 归一后再查，`SciKit_Learn` 与 `scikit-learn`
    是同一个问题——pip 自己会归一，但它把用户原样输入的那个名字回显出来，
    所以归一化必须由我们做，否则界面上的名字与安装用的身份会对不上。

    用哪个解释器问：这个项目的受管环境在就用它（顺带拿到「这个环境里已经装了
    哪一版」），不在就退到基础解释器。**`installed` 只在前一种情况下有值**
    ——基础解释器里装着什么与这一页要装到的那个环境无关，拿它回答会答错主语。
    """
    parsed = depresolve.parse_requirement(str(name or "").strip())
    if parsed is None or parsed[1]:
        raise RepairError(ERROR_REQUIREMENT_INVALID, "查找只接受包名，不接受版本、路径或地址")
    canonical = depresolve.normalize_distribution(parsed[0])
    # 归一化之后**再过一次同一道语法**：进 argv 的是它，不是上面验过的那个串
    # （与 `_pip_install` 在执行前再验一次是同一条纪律）
    if depresolve.parse_requirement(canonical) != (canonical, ""):
        raise RepairError(ERROR_REQUIREMENT_INVALID, "这个包名归一化之后不是一个合法的包名")

    managed_python = managedenv.python_of(project)
    python = managed_python or base_python() or ""
    if not python:
        raise RepairError(ERROR_LOOKUP_FAILED, "这台机器上没有可以用来查找的 Python")

    rc, text, timed_out = _run_lookup(pip_index_argv(python, canonical))
    if timed_out:
        raise RepairError(ERROR_LOOKUP_TIMEOUT, "查找超时")
    if rc != 0:
        code = classify_lookup_failure(text)
        # 输出里可能带 index 地址（甚至凭据）：进日志之前先脱敏，且**不进响应**
        LOG.info("包查找失败 %s → %s: %s", canonical, code, _sanitize(text)[:400])
        raise RepairError(code, "没有查到这个包")
    versions, installed = parse_index_versions(text)
    if not versions:
        LOG.info("包查找的输出没解析出版本表 %s: %s", canonical, _sanitize(text)[:400])
        raise RepairError(ERROR_LOOKUP_FAILED, "没有读懂索引源的回答")

    index = custom_package_index(python)
    return {
        "name": canonical,
        "versions": versions[:LOOKUP_MAX_VERSIONS],
        "latest": versions[0],
        # 「这个环境里已经装了哪一版」——问不出 / 问的不是这个环境时是空串
        "installed": installed if managed_python else "",
        # 三档而不是两档：「配没配自定义源」问不出来的时候它就是问不出来
        # （[[unknown-is-its-own-value]]）。**这里只有真假，绝不回地址。**
        "source": "unknown" if index is None else ("custom_index" if index else "pypi"),
    }


# --------------------------------------------------------------- 形成作业
def create_package_job(project: str | Path, op: str, spec: str) -> PackageJob:
    """把「对哪个包做什么」定下来——**这一步不改任何东西**。

    校验全在这里：操作名闭集、包名 / 需求串语法、目标环境在不在、内置包不许
    卸、磁盘够不够、环境是不是正被改动。过了才发 job_id。
    """
    root = str(Path(project))
    if op not in PACKAGE_OPS:
        raise RepairError(ERROR_PACKAGE_OP_INVALID, f"未知的包操作: {op!r}")
    parsed = depresolve.parse_requirement(str(spec or "").strip())
    if parsed is None:
        raise RepairError(ERROR_REQUIREMENT_INVALID, "只接受 `包名` 或 `包名>=版本` 这样的形态")
    name, specifier = parsed
    if op == OP_UNINSTALL and specifier:
        raise RepairError(ERROR_REQUIREMENT_INVALID, "卸载只接受包名")
    requirement = f"{name}{specifier}"
    key_name = depresolve.normalize_distribution(name)

    python = managedenv.python_of(root) or ""
    creates = False
    if not python:
        if op != OP_INSTALL:
            raise RepairError(ERROR_PACKAGE_ENV_MISSING, "这个项目还没有 Tavotto 环境")
        creates = True
        if not base_python():
            raise RepairError(ERROR_MANAGED_UNAVAILABLE, "这台机器上没有可以用来创建环境的 Python")

    env_key = _env_key(TARGET_MANAGED, python, root)
    busy = envlease.is_mutating(python) if python else envlease.is_mutating_key(env_key)
    if busy:
        raise RepairError(ERROR_BUSY, "这个环境正在改动，请稍候")

    dependents: tuple[str, ...] = ()
    if op == OP_UNINSTALL:
        inv = inventory(python)
        if inv is None:
            raise RepairError(ERROR_MANAGED_BROKEN, "问不出这个环境里装了什么")
        if key_name not in inv:
            raise RepairError(ERROR_PACKAGE_NOT_INSTALLED, f"环境里没有 {name}")
        if key_name in protected_distributions(inv):
            raise RepairError(ERROR_PACKAGE_PROTECTED, f"{name} 是内置包，卸掉它这个环境就用不了了")
        accounted = [
            str(e.get("distribution"))
            for e in (managedenv.read_manifest(root) or {}).get("installed_by_tavotto") or []
            if isinstance(e, dict) and e.get("distribution")
        ]
        dependents = tuple(_dependents_of(name, inv, accounted))
    else:
        _require_free_disk(root)

    now = time.time()
    job = PackageJob(
        job_id=secrets.token_urlsafe(24),
        project=root,
        project_id=managedenv.project_fingerprint(root),
        op=op,
        distribution=name,
        requirement=requirement,
        python=python,
        env_fingerprint=_fingerprint(TARGET_MANAGED, python, root),
        creates_environment=creates,
        dependents=dependents,
        created_at=now,
        expires_at=now + PLAN_TTL_S,
    )
    with _lock:
        for stale in [k for k, j in _jobs.items() if j.expires_at < now]:
            _jobs.pop(stale, None)
        _jobs[job.job_id] = job
    LOG.info("包操作作业: %s %s", logsafe.known(op, PACKAGE_OPS), requirement)
    return job


def _require_free_disk(root: str) -> None:
    """装包前至少要有 `MIN_FREE_BYTES` 空闲（受管环境所在的卷）；量不出来不拦。"""
    try:
        target_dir = managedenv.env_dir(root)
        probe_dir = target_dir if target_dir.exists() else target_dir.parent
        probe_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(probe_dir)).free
    except OSError:
        free = None
    if free is not None and free < MIN_FREE_BYTES:
        raise RepairError(ERROR_PACKAGE_DISK_LOW, "磁盘剩余空间不足")


def get_package_job(job_id: str) -> PackageJob | None:
    with _lock:
        job = _jobs.get(str(job_id or ""))
    if job is not None and job.expires_at < time.time():
        with _lock:
            _jobs.pop(job.job_id, None)
        return None
    return job


# --------------------------------------------------------------- 执行
def run_package_job_async(job_id: str, on_event=None) -> None:
    threading.Thread(
        target=lambda: _run_package_job_guarded(job_id, on_event),
        daemon=True,
        name="tavotto-package-job",
    ).start()


def _run_package_job_guarded(job_id: str, on_event) -> dict:
    try:
        return run_package_job(job_id, on_event)
    except RepairError as exc:
        _log_repair_failure("包操作", job_id, exc.code)
        return _emit_job(job_id, STATE_FAILED, on_event, code=exc.code, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        LOG.exception("包操作线程异常")
        return _emit_job(job_id, STATE_FAILED, on_event, code=ERROR_FAILED, error=str(exc))


def run_package_job(job_id: str, on_event=None) -> dict:
    """执行一个作业。**只认 job_id**：做什么、对哪个包、哪个项目，全部来自作业。"""
    job = get_package_job(job_id)
    if job is None:
        raise RepairError(ERROR_NOT_ALLOWED, "没有这个作业（或已过期）")
    if _fingerprint(TARGET_MANAGED, job.python, job.project) != job.env_fingerprint:
        with _lock:
            _jobs.pop(job.job_id, None)
        raise RepairError(ERROR_PLAN_STALE, "确认期间目标环境发生了变化")

    cancel_ev = threading.Event()
    with _lock:
        _cancels[job.job_id] = cancel_ev
    key = _env_key(TARGET_MANAGED, job.python, job.project)
    try:
        with pool.mutating_environment(key, job.python):
            return _run_package_job(job, key, on_event, cancel_ev)
    except pool.EnvironmentBusy as exc:
        raise _busy_error(exc) from exc
    finally:
        with _lock:
            _cancels.pop(job.job_id, None)
            _jobs.pop(job.job_id, None)  # 作业是一次性的


def _run_package_job(job: PackageJob, env_key: str, on_event, cancel_ev: threading.Event) -> dict:
    project = job.project
    _emit_job(job.job_id, STATE_PREPARING, on_event, job=job)

    python = job.python
    if job.creates_environment:
        # 环境还不在：首装 = 建第一代（adapter + 这个包），验完切 active（U04）
        gen_job = _GenerationJob(
            progress_id=job.job_id,
            project=project,
            script="",
            delta=(job.requirement,),
            constraints=(),
            hashes={},
            require_hashes=False,
            needed_imports=(),
            record=(
                {
                    "import_name": "",
                    "distribution": job.distribution,
                    "specifier": job.requirement[len(job.distribution) :],
                },
            ),
            reason=managedenv.REASON_USER_REQUESTED,
            identity="",
            # 代事务自己的 `done` 不外露：包作业的终态由下面带 version 的那一次 emit 给——否则轮询者
            # 会在两次 emit 之间看到一个没有 version 的 done（CI 上真撞到过，Codex #464 那条红）
            emit=lambda state, **kw: _emit_generation_step(
                job.job_id, state, on_event, job=job, **kw
            ),
            on_log=lambda text: _append_log(job.job_id, text, on_event),
            label=f"{job.op}-{job.distribution}",
        )
        outcome = _run_generation_locked(gen_job, cancel_ev, env_key)
        if not outcome.get("ok"):
            return outcome
        version = outcome["installed"].get(depresolve.normalize_distribution(job.distribution), "")
        result = {
            "ok": True,
            "op": job.op,
            "distribution": job.distribution,
            "version": version,
            "python_version": (managedenv.read_manifest(project) or {}).get("python_version", ""),
            "generation": outcome["generation"],
        }
        _emit_job(job.job_id, STATE_DONE, on_event, job=job, result=result)
        LOG.info(
            "包操作完成: %s %s %s",
            logsafe.known(job.op, PACKAGE_OPS),
            job.distribution,
            logsafe.version(version),
        )
        return result
    if cancel_ev.is_set():
        managedenv.mark_incomplete(project, f"{job.op} 被取消")
        return _emit_job(job.job_id, STATE_CANCELLED, on_event, job=job, code=ERROR_CANCELLED)

    rc, out = _run([python, "-m", "pip", "--version"], PIP_PROBE_TIMEOUT_S)
    if rc != 0:
        raise RepairError(ERROR_MANAGED_BROKEN, _sanitize(out)[-800:])

    # 改动前的快照：不是回滚（pip 没有事务），是修复时的对照
    before = _freeze(python)
    managedenv.record_snapshot(project, f"before-{job.op}-{job.distribution}", before)

    _emit_job(job.job_id, STATE_INSTALLING, on_event, job=job)
    log = lambda text: _append_log(job.job_id, text, on_event)  # noqa: E731
    if job.op == OP_UNINSTALL:
        code, out = _pip_uninstall(python, job.distribution, cancel_ev, log)
    else:
        code, out = _pip_install(
            python,
            job.requirement,
            cancel_ev,
            log,
            upgrade=job.op == OP_UPDATE,
            on_mirror=lambda url: _note_mirror(job.job_id, url, on_event),
            on_source=lambda src: _note_source(job.job_id, src, on_event),
        )
    if code == ERROR_CANCELLED:
        # 装 / 卸到一半：这个环境不再假装是干净的（我们自己的东西，重建即可）
        managedenv.mark_incomplete(project, f"{job.op} 被取消")
        return _emit_job(job.job_id, STATE_CANCELLED, on_event, job=job, code=ERROR_CANCELLED)
    if code:
        raise RepairError(code, _sanitize(out)[-800:])

    # ---- 验证：结果真的落地了 + 环境还能画图 ----
    _emit_job(job.job_id, STATE_VERIFYING, on_event, job=job)
    inv = inventory(python)
    key_name = depresolve.normalize_distribution(job.distribution)
    present = inv is not None and key_name in inv
    if job.op == OP_UNINSTALL and present:
        raise RepairError(ERROR_PACKAGE_STILL_INSTALLED, f"pip 退出了，但 {job.distribution} 还在")
    if job.op != OP_UNINSTALL and not present:
        # pip exit 0 + 包不在：装进了别处 / 名字对上了另一个包
        raise RepairError(
            ERROR_PACKAGE_NOT_FOUND_AFTER, f"pip 退出了，但环境里没有 {job.distribution}"
        )
    health = projectenv.probe_environment(python)
    if not health.get("ok"):
        managedenv.mark_incomplete(project, f"{job.op} 之后 matplotlib 不可用")
        raise RepairError(ERROR_MANAGED_BROKEN, health.get("detail") or health.get("code") or "")
    selftest = worker_self_test(python)
    if not selftest.get("ok"):
        managedenv.mark_incomplete(project, f"{job.op} 之后 worker 自检未通过")
        raise RepairError(ERROR_MANAGED_BROKEN, _sanitize(selftest.get("detail", ""))[-800:])

    after = _freeze(python)
    managedenv.record_snapshot(project, f"after-{job.op}-{job.distribution}", after)

    # ---- 记账 + 让这个项目用这个环境 ----
    version = str((inv or {}).get(key_name, {}).get("version") or "") if present else ""
    if job.op == OP_UNINSTALL:
        managedenv.forget_install(project, job.distribution)
    else:
        prior = managedenv.installed_entry(project, job.distribution) or {}
        managedenv.record_install(
            project,
            import_name=str(prior.get("import_name") or ""),
            distribution=job.distribution,
            requested_specifier=job.requirement[len(job.distribution) :],
            resolved_version=version,
            reason=managedenv.REASON_USER_REQUESTED
            if not prior
            else str(prior.get("reason") or managedenv.REASON_USER_REQUESTED),
        )
    managedenv.mark_ready(project)
    if job.op != OP_UNINSTALL:
        # 装进受管环境却不用它，用户看到的是「装了怎么还缺」——与缺包修复
        # 同一条处置：让这个项目从此用这个环境（ADR 0018 项目作用域）。
        projectenv.remember(
            project, python, automatic=False, trigger="package_management", health=health
        )
        pool.note_project_python_ok(python)
    pool.reset_worker_python()
    result = {
        "ok": True,
        "op": job.op,
        "distribution": job.distribution,
        "version": version,
        "python_version": (managedenv.read_manifest(project) or {}).get("python_version", ""),
    }
    _emit_job(job.job_id, STATE_DONE, on_event, job=job, result=result)
    LOG.info(
        "包操作完成: %s %s %s",
        logsafe.known(job.op, PACKAGE_OPS),
        job.distribution,
        logsafe.version(version),
    )
    return result


def _freeze(python: str) -> str:
    """`pip freeze` 的（已脱敏）文本；问不出来回空串。"""
    rc, out = _run(
        [str(python), "-m", "pip", "freeze", "--disable-pip-version-check"], FREEZE_TIMEOUT_S
    )
    return _sanitize(out) if rc == 0 else ""


def _emit_generation_step(job_id: str, state: str, on_event, *, job=None, **kw) -> dict:
    """包作业里代事务的中间步：`done` 由作业自己带 version 发（这里吞掉），其余照发。"""
    if state == STATE_DONE:
        with _lock:
            return dict(_progress.get(job_id) or {})
    return _emit_job(job_id, state, on_event, job=job, **kw)


def _emit_repair_step(plan_id: str, state: str, on_event, *, plan=None, **kw) -> dict:
    """单包修复里代事务的中间步：同上，`done` 由 `_run_install` 带 version / distribution 发。"""
    if state == STATE_DONE:
        with _lock:
            return dict(_progress.get(plan_id) or {})
    return _emit(plan_id, state, on_event, plan=plan, **kw)


def _emit_job(
    job_id: str,
    state: str,
    on_event,
    *,
    job: PackageJob | None = None,
    code: str = "",
    error: str | None = None,
    result: dict | None = None,
) -> dict:
    """作业进度（与 `_emit` 同一张 `_progress` 表，多带 op / job_id）。"""
    with _lock:
        rec = dict(_progress.get(job_id) or {"log": ""})
        rec.update(
            job_id=job_id,
            plan_id=job_id,
            state=state,
            code=code,
            error=error,
            result=result or rec.get("result"),
        )
        if job is not None:
            rec.update(op=job.op, distribution=job.distribution, requirement=job.requirement)
        _progress[job_id] = rec
        snapshot = dict(rec)
    if on_event is not None:
        on_event(snapshot)
    return snapshot


# ---------------------------------------------------------------------------
# 联合准备与按代的受管环境事务（统一实施包 U04，ADR 0061 §四–§六）
#
# 「一次准备多个依赖」的执行面。计划由 `depplan.plan()` 算（不装）；这里把它绑成
# `JointRepairPlan`（plan_id 是唯一凭据，执行端一个字节都不从请求体读，ADR 0019 §四
# 不变），再按目标走两条路：
#
#   受管环境  → **新的一代**：`envs/<身份>/` 里 `python -m venv` → 一次 pip 装完整集合
#              （adapter + 账上记过的 + 这次的）→ `pip check` → 关键 import → worker 自检
#              → 切 `active`（提交点）→ 记账 / 记住 / 作废会话 → 旧代留到没人用再删。
#              任一步不过就是 `incomplete`，`active` 不动，旧环境原样可用。
#   用户 venv → **原地**装这次缺的（ADR 0019 §八：明确确认、只进不退、不假装 rollback）。
#
# 单包修复（`install()`）、重建（`rebuild_managed()`）、包管理里「环境还不在」的首次安装
# 都走同一个 `_run_generation()`——没有第二套建环境 / 装包 / 验证的代码；锁仍是
# `envlease` 那一张表（`pool.mutating_environment`）。
# ---------------------------------------------------------------------------
ERROR_CONSISTENCY = "dependency_consistency_failed"
ERROR_HASH_MISMATCH = "dependency_hash_mismatch"
ERROR_PLAN_BLOCKED = "dependency_plan_blocked"

_HASH_MARKERS = ("do not match the hashes", "hash mismatch", "hashes are required")

_joint_plans: dict[str, "JointRepairPlan"] = {}
#: 已过提交点的 plan_id → 时刻。提交点之后拒绝取消（D11）。
_committed: dict[str, float] = {}


@dataclasses.dataclass(frozen=True)
class JointRepairPlan:
    """一次联合准备的完整描述——执行端只认它。

    绑定：项目 / 脚本 / 完整需求集合（规范串）/ 约束 / hash / 目标类型 / 目标解释器指纹 /
    目标事实 digest / 选中的组 / 有效期。`joint` 是 `depplan.JointPlan` 的载荷（界面按它显示
    要装什么、为什么），执行只读上面那些绑定字段。
    """

    plan_id: str
    project: str
    project_id: str
    script: str
    target_kind: str
    python: str  # 当前选中的解释器（受管环境还没有时为空）
    env_fingerprint: str
    facts_digest: str
    facts_python: str  # 量 `facts_digest` 的解释器（此刻会跑脚本的那个）
    install_facts_digest: str  # 装到哪的事实的 digest（与上面不是同一个环境时才有，否则空）
    requirements: tuple[str, ...]
    constraints: tuple[str, ...]
    hashes: dict
    require_hashes: bool
    adapter: tuple[str, ...]
    identity: str
    needed_imports: tuple[str, ...]
    record: tuple[dict, ...]  # 要记进账的 (import_name, distribution, specifier)
    groups: tuple[str, ...]
    modifies_user_environment: bool
    creates_environment: bool
    created_at: float
    expires_at: float
    joint: dict
    #: 同 `RepairPlan.private_python`：这次授权包不包含先下载私有 Python（U05）。
    private_python: dict | None = None
    #: 计划的事实来自替身（私有 Python 还没落盘）：事务供应之后按真解释器重算 delta（U05 PR B）。
    replan: bool = False
    #: 规划输入的指纹（`depplan.JointPlan.inputs_digest`）：重算前先比它，变了就是 `repair_plan_stale`。
    inputs_digest: str = ""
    #: 形成计划那一刻项目级解释器决定的签名（`selection_signature`）：执行前再比。
    selection: tuple = ()
    #: 作用域策略（`SCOPE_POLICY_SWITCH` = 换成本作用域）与它会让哪些包不再 active（形成计划时按账算好）。
    scope_policy: str = ""
    drops: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()

    @property
    def pip_inputs(self) -> PipInputs:
        """pip 会收到的全部输入（披露与执行共用，见 `PipInputs`）。"""
        return PipInputs(
            requirements=self.requirements,
            constraints=self.constraints,
            hashes=self.hashes,
            require_hashes=self.require_hashes,
            adapter=self.adapter,
        )

    @property
    def impact(self) -> dict:
        """这份授权的实际影响（`impact_of`）：跑前的门显示的就是同一个函数算出的同一份（`offer_impact`）。
        输入集合来自 `pip_inputs`——执行端（作业 / 原地 pip）读的也是它。"""
        pi = self.pip_inputs
        return impact_of(
            target_kind=self.target_kind,
            requirements=pi.requirements,
            constraints=pi.constraints,
            require_hashes=pi.require_hashes,
            adapter=pi.adapter,
            groups=self.groups,
            creates_environment=self.creates_environment,
            private_python=self.private_python,
            env_fingerprint=self.env_fingerprint,
            scope_policy=self.scope_policy,
            drops=self.drops,
            changes=self.changes,
        )

    @property
    def impact_digest(self) -> str:
        return impact_digest(self.impact)

    def to_payload(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "script": self.script,
            "target_kind": self.target_kind,
            "python": projectenv.project_relative(self.project, self.python)
            or ("" if not self.python else "…"),
            "requirements": list(self.requirements),
            "constraints": list(self.constraints),
            "require_hashes": self.require_hashes,
            "adapter": list(self.adapter),
            "identity": self.identity,
            "needed_imports": list(self.needed_imports),
            "groups": list(self.groups),
            "modifies_user_environment": self.modifies_user_environment,
            "creates_environment": self.creates_environment,
            "network_required": True,
            "expires_at": int(self.expires_at),
            "joint": dict(self.joint),
            "private_python": dict(self.private_python) if self.private_python else None,
            "replan": self.replan,
            "impact": self.impact,
            "impact_digest": self.impact_digest,
        }


def joint_target_for(project: str | Path, script: str) -> tuple[str, str, str]:
    """这个项目此刻的解释器 → (目标类型, 解释器路径, 来源)。

    选中的是项目自己的 venv → 目标就是它（原地，要明确确认）；选中的是受管环境 / 内置 /
    自身 / 系统 → 目标是受管环境（新的一代；内置与自身永远不是安装目标，系统解释器在
    用户交给我们的边界之外——ADR 0019 §一 / ADR 0044）。解释器解析不出来（显式选择失效）
    照抛：那是用户要先处理的事，不替他换环境。
    """
    root = str(Path(project))
    python, source = pool.resolve_worker_python(root, script=script)
    if source == pool.SOURCE_PROJECT_VENV:
        return TARGET_PROJECT_VENV, python, source
    return TARGET_MANAGED, python, source


#: 一个渲染解释器都没有（`pool._no_python_error` 的 code）——干净机器的形状。
NO_WORKER_PYTHON = "no_worker_python"


def private_python_target(project: str | Path, script: str) -> tuple[str, str, object] | None:
    """**一个渲染解释器都没有**而本目标提供私有 Python 时的目标：受管环境（要新建）、解释器为空、事实按
    私有 Python 量——已供应就真量（`depplan.target_facts`），还没落盘就用替身（锁的版本 / 实现 + 这台机器
    的平台字段，已装集合为空）。回 None = 这条路不适用（有解释器、或不提供私有 Python），让原路径去走。

    这是干净机器上第一份计划的来源（U05 PR B）：没有它，`joint_target_for` 抛 `no_worker_python`、
    `depplan.plan(facts=None)` 是 `dependency_target_unavailable`——都不是「先下载再准备」这条路。"""
    root = str(Path(project))
    try:
        pool.resolve_worker_python(root, script=script)
        return None
    except pool.WorkerError as exc:
        if getattr(exc, "code", "") != NO_WORKER_PYTHON:
            return None
    facts = private_fresh_facts()
    if facts is None:
        return None
    return TARGET_MANAGED, facts.python, facts


def private_fresh_facts() -> depplan.TargetFacts | None:
    """从私有 Python 新建的一代**装之前**的事实：已供应就真量（`depplan.fresh_venv_facts`）；还没落盘
    就用替身——marker 环境按锁的版本 / 实现 + 这台机器的平台字段、stdlib 用宿主的表、已装集合只有 adapter
    （这一代必然会带上）。不提供私有 Python 回 None。替身只服务披露；供应之后事务按真解释器重算。"""
    source = privatepython.source_for()
    if source is None or not privatepython.offered(source):
        return None
    private = privatepython.python_of(source)
    if private:
        return depplan.fresh_venv_facts(private, provided=depplan.adapter_distributions())
    installed = {name: "" for name in depplan.adapter_distributions()}
    return depplan.TargetFacts(
        python="",
        marker_env=privatepython.standin_marker_env(source),
        stdlib=importscan.HOST_STDLIB,
        installed=installed,
    )


def joint_plan_for(
    project: str | Path,
    script: str,
    *,
    groups: list[str] | None = None,
    scope_policy: str = "",
) -> tuple[depplan.JointPlan, str, str]:
    """算一份联合计划（只读）：缺什么按**此刻选中的**解释器量、装什么按目标量（`_facts_for`）。
    回 (计划, 目标类型, 解释器)。

    一个解释器都没有时（干净机器）先问 `private_python_target`：提供私有 Python 就以它为目标算
    （缺什么与装什么都按将要新建的那一代量——替身或真量）；不提供照抛 `no_worker_python`。"""
    root = str(Path(project))
    if groups is None:
        groups = depplan.selected_groups_setting(root)
    standin = private_python_target(root, script)
    if standin is not None:
        target_kind, python, facts = standin
        install_facts = None
    else:
        target_kind, python, _source = joint_target_for(root, script)
        facts, install_facts, _measured = _facts_for(
            target_kind, python, root, fresh=scope_policy == SCOPE_POLICY_SWITCH
        )
    plan = depplan.plan(
        root,
        script,
        facts=facts,
        target_kind=target_kind,
        groups=groups,
        install_facts=install_facts,
    )
    return _with_scope_check(root, script, plan, target_kind, scope_policy), target_kind, python


def _facts_for(
    kind: str, python: str, root: str, *, use_cache: bool = True, fresh: bool = False
) -> tuple[depplan.TargetFacts | None, depplan.TargetFacts | None, str]:
    """(缺什么按它量的事实, 装到哪的事实——与前者是同一个环境时 None, 前者量的解释器)。

    缺什么按**此刻会跑脚本的**解释器量（门问的是「现在起会话会不会缺包」）；装到哪按**目标**量：
    用户 venv 目标 = 同一个；受管目标 = active 那一代（有）/ 从 base 新建的一代（没有：marker
    环境与 stdlib 按 base，已装集合为空——`depplan.fresh_venv_facts`）。两者不是同一个环境时
    （选中的是项目 venv / 系统解释器，目标是受管环境）计划的集合按目标量，否则新的一代会漏装
    选中环境里碰巧有的包、marker 会按另一个 minor 求值（Codex #461 P1）。
    """
    run = depplan.target_facts(python, use_cache=use_cache) if python else None
    if kind != TARGET_MANAGED:
        return run, None, python
    managed_python = managedenv.python_of(root) or ""
    if managed_python and not fresh:
        if python and pool.same_python(managed_python, python):
            return run, None, python
        return run, depplan.target_facts(managed_python, use_cache=use_cache), python
    base = base_python() or ""
    if not base:
        # 没有基础解释器：提供私有 Python 就按它（真量 / 替身）量新的一代，否则计划只能按选中的量
        return run, private_fresh_facts(), python
    fresh_facts = depplan.fresh_venv_facts(
        base, use_cache=use_cache, provided=depplan.adapter_distributions()
    )
    return run, fresh_facts, python


def create_joint_plan(
    project: str | Path,
    script: str,
    *,
    target_kind: str = "",
    groups: list[str] | None = None,
    scope_policy: str = "",
) -> JointRepairPlan:
    """把联合计划绑定成可执行的（发 plan_id）。**不装任何东西。**

    计划不是 `ready`（没缺的 / blocked）就拒绝：`dependency_plan_blocked` 带 blocked 理由——
    执行端不会「把认不出的那行剥掉偷偷继续」。`target_kind` 可以由调用方指定（用户在用户
    venv 与受管环境之间选），默认按 `joint_target_for`；指定用户 venv 时它必须就是此刻选中
    的那个（不接受任意路径：ADR 0019 §一「从发现结果里取」）。
    """
    root = str(Path(project))
    selection0 = selection_signature(root)  # 先于目标解析与事实探测：计划记的是**算目标时**的决定
    _refuse_if_pinned()  # 与单包修复 / `offer()` 同一条判据：装进去也不会被用的计划一开始就不形成（E05）
    if rounds_remaining(root, script) <= 0:
        raise RepairError(ERROR_ROUNDS_EXHAUSTED, "这个脚本的自动依赖修复已经用满")
    standin = private_python_target(root, script)
    if standin is not None:
        # 干净机器：目标只能是受管环境（要新建），事实来自私有 Python（真量或替身）
        auto_kind, python, facts = standin
    else:
        auto_kind, python, _source = joint_target_for(root, script)
        facts = None
    kind = target_kind or auto_kind
    if kind not in TARGETS:
        raise RepairError(ERROR_NOT_ALLOWED, f"未知的安装目标: {kind!r}")
    if kind == TARGET_PROJECT_VENV and auto_kind != TARGET_PROJECT_VENV:
        raise RepairError(ERROR_NOT_ALLOWED, "这个项目此刻没有选中自己的虚拟环境，不能往里装")
    if scope_policy not in ("", SCOPE_POLICY_SWITCH):
        raise RepairError(ERROR_NOT_ALLOWED, f"未知的作用域策略: {scope_policy!r}")
    if scope_policy == SCOPE_POLICY_SWITCH and kind != TARGET_MANAGED:
        # 「换成本作用域」重建的是 Tavotto 自己的受管环境；用户的 venv 不能被整个换掉
        raise RepairError(ERROR_NOT_ALLOWED, "只有 Tavotto 的受管环境能换成某个作用域")
    if standin is not None:
        install_facts, measured = None, ""
    else:
        facts, install_facts, measured = _facts_for(
            kind, python, root, fresh=scope_policy == SCOPE_POLICY_SWITCH
        )
    groups = depplan.selected_groups_setting(root) if groups is None else list(groups)
    joint = depplan.plan(
        root, script, facts=facts, target_kind=kind, groups=groups, install_facts=install_facts
    )
    joint = _with_scope_check(root, script, joint, kind, scope_policy)
    # 干净机器上「什么都不缺」也得建环境（没有任何解释器可用）：nothing_needed 照样成计划，delta 为空 = 只装 adapter
    if joint.status == depplan.STATUS_BLOCKED or (
        joint.status == depplan.STATUS_NOTHING_NEEDED and standin is None
    ):
        raise RepairError(
            ERROR_PLAN_BLOCKED,
            "联合计划不可执行" if joint.status == depplan.STATUS_BLOCKED else "没有缺的依赖",
            joint=joint.to_payload(),
        )
    # 没有基础解释器且不提供私有 Python 时在这里抛（与跑前的门显示影响摘要同一处，`_managed_scope`）
    creates, private, managed_python = _managed_scope(root, kind)
    if kind == TARGET_MANAGED:
        _require_free_disk(root)  # 新的一代要落盘（FO28）
        bound_python = managed_python
    else:
        bound_python = python
    # 计划里带着下载 = 「装到哪」的事实是替身（私有 Python 还没落盘）：供应之后事务按真解释器重算 delta
    replan = private is not None
    # 「换成本作用域」：新一代装的是本作用域的**全部** needed（不是相对 active 那一代的差额），所以验证 import 与
    # 记账也要覆盖已经在当前环境里的那些；并入时只有缺的
    provided = set(
        depplan.adapter_distributions()
    )  # adapter 自己会装，不进账（账里钉版本会钉死它）
    installing_entries = (
        (
            *joint.missing,
            *(
                e
                for e in joint.satisfied
                if depresolve.normalize_distribution(e["distribution"]) not in provided
            ),
        )
        if scope_policy == SCOPE_POLICY_SWITCH
        else joint.missing
    )
    _selection_unchanged(root, selection0)
    now = time.time()
    plan = JointRepairPlan(
        plan_id=new_plan_id(),
        project=root,
        project_id=managedenv.project_fingerprint(root),
        script=script,
        target_kind=kind,
        python=bound_python,
        env_fingerprint=_fingerprint(kind, bound_python, root),
        facts_digest=facts.digest() if facts is not None else "",
        facts_python=measured,
        install_facts_digest=install_facts.digest() if install_facts is not None else "",
        requirements=tuple(joint.requirements),
        constraints=tuple(joint.constraints),
        hashes={k: tuple(v) for k, v in joint.hashes.items()},
        require_hashes=joint.require_hashes,
        adapter=tuple(joint.adapter),
        identity=joint.identity,
        needed_imports=tuple(m["import_name"] for m in installing_entries),
        record=tuple(
            {
                "import_name": m["import_name"],
                "distribution": m["distribution"],
                "specifier": ",".join(m["specifiers"]),
            }
            for m in installing_entries
        ),
        groups=tuple(joint.selection.get("selected_groups") or ()),
        modifies_user_environment=kind == TARGET_PROJECT_VENV,
        creates_environment=creates,
        created_at=now,
        expires_at=now + PLAN_TTL_S,
        joint=joint.to_payload(),
        private_python=private,
        replan=replan,
        inputs_digest=joint.inputs_digest,
        selection=selection0,
        scope_policy=scope_policy,
        **_effects(root, joint.requirements, joint.constraints, scope_policy),
    )
    _prune_plans()
    with _lock:
        _joint_plans[plan.plan_id] = plan
    LOG.info(
        "联合依赖计划: %s → %s（%s）",
        ", ".join(plan.requirements),
        logsafe.known(kind, TARGETS),
        script,
    )
    return plan


def get_joint_plan(plan_id: str) -> JointRepairPlan | None:
    _prune_plans()
    with _lock:
        return _joint_plans.get(str(plan_id or ""))


#: 已认领（正在执行）的联合计划 id：同一份计划只起一个执行线程（Codex #470 P1：第一次还没
#: 消费掉计划前重复提交 `/prepare`，两个线程各自重算事实、排队拿锁、各装一遍）。
_running: set[str] = set()
#: 在途的依赖作业（联合准备与单包修复共用）：plan_id → {project_id, digest, flow}，`_claim` 时登记、结束时清掉。
#: 「这个项目上有没有安装在跑」（`installing`）与「同一份影响摘要的作业认领」都读它。
_active_jobs: dict[str, dict] = {}
#: 同一份已确认影响摘要的在途作业：(项目指纹, 摘要) → plan_id。第二个提交者（另一个标签页 / 另一个会话）
#: 认领原作业而不是再起一个（T06）。
_joined: dict[tuple[str, str, str], str] = {}
#: 在途作业的追加进度监听者：plan_id → [callable(snapshot)]（认领了原作业的另一方也要看到终局）。
_listeners: dict[str, list] = {}
FLOW_JOINT = "joint"
FLOW_SINGLE = "single"


def _claim(
    plan_id: str,
    *,
    project_id: str = "",
    digest: str = "",
    flow: str = FLOW_JOINT,
    scope: str = "",
) -> bool:
    """认领一份计划：回 True = 这次认领成功；False = 已有人在跑。**在起线程之前**。
    `scope` = 依赖作用域（脚本所在目录，`scope_of`）：同项目同摘要、不同目录的两次授权是两份作业，账上的归属
    记在各自的作用域里（Codex r4218802492）——合并键必须带它。"""
    with _lock:
        if plan_id in _running:
            return False
        _running.add(plan_id)
        _active_jobs[plan_id] = {
            "project_id": project_id,
            "digest": digest,
            "flow": flow,
            "scope": scope,
        }
        if project_id and digest:
            _joined[(project_id, scope, digest)] = plan_id
        return True


def _release(plan_id: str) -> None:
    """作业结束（不论怎么结束）：认领 / 登记 / 监听都清掉。"""
    with _lock:
        _running.discard(plan_id)
        meta = _active_jobs.pop(plan_id, None)
        if meta and meta.get("project_id") and meta.get("digest"):
            key = (meta["project_id"], meta.get("scope", ""), meta["digest"])
            if _joined.get(key) == plan_id:
                _joined.pop(key, None)
        # 监听者不在这里清：失败终态常在作业返回之后才由 `_prepare_guarded` / `_install_guarded` 发出，
        # 终态那一次 `_emit` 发完才摘（`_emit`）


def is_running(plan_id: str) -> bool:
    """这个计划 id 此刻有没有作业在跑（已认领、还没清）。"""
    with _lock:
        return str(plan_id or "") in _running


def installing(project: str | Path) -> bool:
    """这个项目上此刻有没有依赖安装在跑。采用 / 改项目环境前问它（`unless_installing`）。"""
    pid = managedenv.project_fingerprint(str(Path(project)))
    with _lock:
        return any(meta.get("project_id") == pid for meta in _active_jobs.values())


def unless_installing(project: str | Path, action):
    """没有该项目的安装在跑时，在**同一把锁里**执行 `action`；有就抛 `EnvironmentBusy`（`environment_mutating`）。

    采用 / 选回默认会改项目级的解释器决定，而安装结束要把结果记成项目的环境——两者交错，后写的会静默盖掉先
    写的。认领（`_claim`）与这里同一把锁：要么先认领（这里被拒、让用户等安装结束），要么先改决定（作业认领后
    比 `selection_signature` 发现决定变了、以 `repair_plan_stale` 停在写任何东西之前）。`action` 只能是不回头
    碰本模块的短操作（写一次项目设置）。"""
    pid = managedenv.project_fingerprint(str(Path(project)))
    with _lock:
        if any(meta.get("project_id") == pid for meta in _active_jobs.values()):
            raise envlease.EnvironmentBusy(
                "这个项目上有依赖安装正在进行中，请等它结束再改环境。",
                code=envlease.ENVIRONMENT_MUTATING,
            )
        return action()


def add_listener(plan_id: str, fn) -> bool:
    """给在途作业追加进度监听；作业已到终态 / 不在跑回 False（调用方读 `progress()` 拿终局）。"""
    pid = str(plan_id or "")
    with _lock:
        if pid not in _running or (_progress.get(pid) or {}).get("state") in TERMINAL_STATES:
            return False
        _listeners.setdefault(pid, []).append(fn)
        return True


def _plan_scope(plan) -> str:
    """计划的依赖作用域（脚本所在目录）；没有脚本的计划 = 空。"""
    script = getattr(plan, "script", "") or ""
    return scope_of(script) if script else ""


def _check_confirmed(plan, confirmed_impact: str | None) -> None:
    """执行入口的唯一一道门（Codex r4217992305 / r4218802478）：**调用方必须回显它给用户看的影响摘要**。

    * 缺 / 空 / 非字符串 -> `dependency_impact_required`，零副作用。**不再有「None = 旧客户端、不绑定」**：
      服务端自己持有的摘要（计划、动作、缓存里的）一律不得代替调用方回显——那等于没有确认。
    * 与计划此刻的实际影响对不上 -> `dependency_impact_changed`，零副作用。
    """
    if not isinstance(confirmed_impact, str) or not confirmed_impact:
        raise RepairError(
            ERROR_IMPACT_REQUIRED, "执行安装需要带上你确认时看到的影响摘要，请重新查看再确认。"
        )
    if plan is None:
        return
    if plan.impact_digest != confirmed_impact:
        raise RepairError(
            ERROR_IMPACT_CHANGED,
            "要执行的安装影响与你确认的不一致，请重新查看再确认",
            impact=plan.impact,
            impact_digest=plan.impact_digest,
        )


def prepare_async(plan_id: str, on_event=None, *, confirmed_impact: str | None) -> bool:
    """起线程执行一份联合计划；回 False = 这份计划已在执行，**不再起第二个线程**（调用方把
    在途的进度原样交回去）。**认领与取消句柄都在起线程之前**：调用方一回 202 用户就可能取消，
    那时线程可能还在重算事实、还没拿锁——句柄不在表里的话 `cancel_status` 只能回 `not_found`，
    安装照常改环境（Codex #470 P1）。`prepare()` 复用这一个句柄。

    `confirmed_impact`（T06）：**必传**，且必须等于计划此刻的 `impact_digest`；缺 -> `dependency_impact_required`，
    对不上 -> `dependency_impact_changed`（均在认领之前、零副作用）。"""
    pid = str(plan_id or "")
    plan = get_joint_plan(pid)
    _check_confirmed(plan, confirmed_impact)
    if not _claim(
        pid,
        project_id=getattr(plan, "project_id", ""),
        digest=getattr(plan, "impact_digest", ""),
        scope=_plan_scope(plan),
    ):
        return False
    _register_cancel(pid)
    threading.Thread(
        target=lambda: _prepare_guarded(pid, on_event, claimed=True),
        daemon=True,
        name="tavotto-dep-prepare",
    ).start()
    return True


def start_confirmed(
    project: str | Path,
    script: str,
    confirmed_digest: str,
    *,
    target_kind: str = "",
    module: str = "",
    scope_policy: str = "",
    on_event=None,
) -> dict:
    """按**用户确认过的影响摘要**起一次依赖作业——准备会话的动作认领走这里（T06）。

    一次完成「现算计划 → 比摘要 → 认领 → 起线程」，三种结局：

    * 摘要对不上（集合 / 目标 / 环境代 / 写入范围变了）→ `dependency_impact_changed`，计划作废、一个字节不装；
    * 同一份摘要已经有作业在跑（另一个标签页 / 另一个会话先认领了）→ 认领**原作业**（`started=False,
      joined=True`，追加进度监听），不起第二个 pip；
    * 其余 → 新作业（`started=True`）。

    比较—认领在 `_lock` 里（现算计划的子进程不占锁，认领前再看一遍有没有人抢先）。`module` 非空 = 运行时发现的
    缺包（单包修复，同一个 `create_plan` / `install`）；空 = 联合计划。目标由调用方说（`target_kind`，空 = 按当前
    选中）；不接受路径。"""
    root = str(Path(project))
    pj = managedenv.project_fingerprint(root)
    # 回显的摘要是**调用方给的**，不是服务端缓存的：缺 -> dependency_impact_required，零副作用（r4218802478）
    if not isinstance(confirmed_digest, str) or not confirmed_digest:
        _check_confirmed(None, confirmed_digest)
    # 合并键带依赖作用域：同项目同摘要、不同目录的两次授权不能合并成一个作业（账上归属只记在第一个作用域里）
    key = (pj, scope_of(script), confirmed_digest)
    joined = _join_running(key, on_event)
    if joined:
        return {"plan_id": joined, "started": False, "joined": True, "progress": progress(joined)}
    flow = FLOW_SINGLE if module else FLOW_JOINT
    if module:
        plan = create_plan(root, script, module, target_kind=target_kind)
    else:
        plan = create_joint_plan(root, script, target_kind=target_kind, scope_policy=scope_policy)
    try:
        _check_confirmed(plan, key[2])
    except RepairError:
        _discard_plan(plan.plan_id)
        raise
    with _lock:
        again = _joined.get(key)
        if again is not None and again in _running:
            claimed = False
        else:
            claimed = _claim(plan.plan_id, project_id=pj, digest=key[2], flow=flow, scope=key[1])
    if not claimed:
        _discard_plan(plan.plan_id)
        joined = _join_running(key, on_event)
        return {
            "plan_id": joined,
            "started": False,
            "joined": bool(joined),
            "progress": progress(joined),
        }
    _register_cancel(plan.plan_id)
    pid = plan.plan_id
    if flow == FLOW_SINGLE:
        threading.Thread(
            target=lambda: _install_guarded(pid, on_event, claimed=True),
            daemon=True,
            name="tavotto-dep-install",
        ).start()
    else:
        threading.Thread(
            target=lambda: _prepare_guarded(pid, on_event, claimed=True),
            daemon=True,
            name="tavotto-dep-prepare",
        ).start()
    return {"plan_id": pid, "started": True, "joined": False, "progress": progress(pid)}


def preview_impact(
    project: str | Path, script: str, module: str, *, target_kind: str = TARGET_MANAGED
) -> dict:
    """运行时发现缺 `module` 时，授权单包修复的实际影响（T06）：用与 `start_confirmed` 同一个 `create_plan` 算出
    计划、取它的 `impact`/`impact_digest`，然后把计划作废——预览不留下可执行的东西。抛 `RepairError`（无法解析 /
    轮次用完 / 已装过 / 全局固定…）= 这件事现在不能在会话里授权。"""
    plan = create_plan(str(Path(project)), script, module, target_kind=target_kind)
    try:
        return {"impact": plan.impact, "impact_digest": plan.impact_digest}
    finally:
        _discard_plan(plan.plan_id)


def _join_running(key: tuple[str, str, str], on_event) -> str:
    """同一份摘要在途的作业：回它的 plan_id 并（作业还没到终态时）追加监听；没有回空串。"""
    with _lock:
        pid = _joined.get(key)
        if pid is None or pid not in _running:
            return ""
        if on_event is not None and (_progress.get(pid) or {}).get("state") not in TERMINAL_STATES:
            _listeners.setdefault(pid, []).append(on_event)
        return pid


def _discard_plan(plan_id: str) -> None:
    """把一份没有执行的计划作废（摘要对不上 / 被别人抢先）——它不该再被任何人拿去执行。"""
    with _lock:
        _plans.pop(plan_id, None)
        _joint_plans.pop(plan_id, None)


def _register_cancel(plan_id: str) -> threading.Event:
    """这份计划的取消句柄（已有就复用——同步与异步入口、登记与执行两处共用一个）。"""
    with _lock:
        ev = _cancels.get(plan_id)
        if ev is None:
            ev = threading.Event()
            _cancels[plan_id] = ev
        return ev


def _facts_for_plan(
    plan: "JointRepairPlan", *, use_cache: bool
) -> tuple[depplan.TargetFacts | None, depplan.TargetFacts | None]:
    """执行前重量事实——与计划期**同一条路**：干净机器（计划期没有解释器，`facts_python` 为空）按
    `private_python_target` 再算一次（替身是确定的，同锁同机就同 digest；期间私有 Python 已被别的项目
    供应则真量 → digest 变 → stale，让用户重算——那时计划该按真事实来）；其余按 `_facts_for`。"""
    if not plan.facts_python and plan.target_kind == TARGET_MANAGED:
        standin = private_python_target(plan.project, plan.script)
        if standin is not None:
            return standin[2], None
        return None, None
    run, install, _measured = _facts_for(
        plan.target_kind,
        plan.facts_python,
        plan.project,
        use_cache=use_cache,
        fresh=plan.scope_policy == SCOPE_POLICY_SWITCH,
    )
    return run, install


def _prepare_guarded(
    plan_id: str, on_event, *, claimed: bool = False, confirmed_impact: str | None = None
) -> dict:
    try:
        return prepare(plan_id, on_event, claimed=claimed, confirmed_impact=confirmed_impact)
    except RepairError as exc:
        _log_repair_failure("联合依赖准备", plan_id, exc.code)
        return _emit(plan_id, STATE_FAILED, on_event, code=exc.code, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        LOG.exception("联合准备线程异常")
        return _emit(plan_id, STATE_FAILED, on_event, code=ERROR_FAILED, error=str(exc))


def prepare(
    plan_id: str, on_event=None, *, claimed: bool = False, confirmed_impact: str | None
) -> dict:
    """执行一份联合计划。执行端只认 `plan_id`；执行前重算环境指纹与事实（`repair_plan_stale`）。

    同一份计划只能被执行一次（`_claim`；`prepare_async` 已认领的传 `claimed=True`），第二个
    调用方拿到 `dependency_install_not_allowed`。取消句柄从第一行起就在表里（`prepare_async`
    已登记的复用）：重算事实那几秒里来的取消，在拿锁之前就生效，一个字节不写（ADR 0061 §六 ①）；
    不论怎么退出，认领、句柄与计划都在 finally 里清掉。
    """
    pid = str(plan_id or "")
    if not claimed:
        plan0 = get_joint_plan(pid)
        _check_confirmed(plan0, confirmed_impact)  # 认领之前：对不上就零副作用
        if not _claim(
            pid,
            project_id=getattr(plan0, "project_id", ""),
            digest=getattr(plan0, "impact_digest", ""),
            scope=_plan_scope(plan0),
        ):
            raise RepairError(ERROR_NOT_ALLOWED, "这份准备计划已经在执行")
    cancel_ev = _register_cancel(pid)
    try:
        plan = get_joint_plan(pid)
        if plan is None:
            raise RepairError(ERROR_NOT_ALLOWED, "没有这个准备计划（或已过期）")
        if _fingerprint(plan.target_kind, plan.python, plan.project) != plan.env_fingerprint:
            raise RepairError(ERROR_PLAN_STALE, "确认期间目标环境发生了变化")
        if plan.selection != selection_signature(plan.project):
            # 确认期间用户采用 / 选回了别的环境：安装结束会把结果记成项目的环境，不能盖掉他刚做的选择
            raise RepairError(ERROR_PLAN_STALE, "确认期间项目的解释器选择变了")
        # 解释器指纹只看 `pyvenv.cfg`：确认期间有人往目标里装 / 卸了包它不变，事实 digest 会变——
        # 重新量一次（不走缓存），不一样就是 stale，一个字节不装（Codex #461 P2）
        run, install = _facts_for_plan(plan, use_cache=False)
        if (run.digest() if run is not None else "") != plan.facts_digest or (
            install.digest() if install is not None else ""
        ) != plan.install_facts_digest:
            raise RepairError(ERROR_PLAN_STALE, "确认期间目标环境里的包发生了变化")
        if cancel_ev.is_set():
            # ack 之后、拿锁之前来的取消：明确终态，什么都没改（Codex #470 P1 的那一刻）
            return _emit(
                pid,
                STATE_CANCELLED,
                on_event,
                joint=plan,
                code=ERROR_CANCELLED,
                result={"activated": False},
            )
        if plan.target_kind == TARGET_MANAGED:
            pi = plan.pip_inputs
            job = _GenerationJob(
                progress_id=plan.plan_id,
                project=plan.project,
                script=plan.script,
                delta=pi.requirements,
                constraints=pi.constraints,
                hashes=pi.hashes,
                require_hashes=pi.require_hashes,
                needed_imports=plan.needed_imports,
                record=plan.record,
                reason=managedenv.REASON_MISSING_DEPENDENCY,
                identity=plan.identity,
                emit=lambda state, **kw: _emit(plan.plan_id, state, on_event, joint=plan, **kw),
                on_log=lambda text: _append_log(plan.plan_id, text, on_event),
                label=f"prepare-{len(plan.requirements)}",
                provision_private=plan.private_python is not None,
                private_plan=plan.private_python,
                groups=plan.groups,
                replan=plan.replan,
                confirmed_inputs=pi,
                confirmed_impact=plan.impact,
                inputs_digest=plan.inputs_digest,
                replace_ledger=plan.scope_policy == SCOPE_POLICY_SWITCH,
                refuse_pinned=True,
            )
            return _run_generation(job, cancel_ev)
        key = _env_key(TARGET_PROJECT_VENV, plan.python, plan.project)
        try:
            with pool.mutating_environment(key, plan.python):
                _refuse_if_pinned()  # 租约在手之后复查（与单包修复同一条纪律）
                return _run_joint_in_place(plan, on_event, cancel_ev)
        except pool.EnvironmentBusy as exc:
            raise _busy_error(exc) from exc
    finally:
        with _lock:
            _cancels.pop(pid, None)
            _joint_plans.pop(pid, None)
        _release(pid)


def _run_joint_in_place(plan: JointRepairPlan, on_event, cancel_ev: threading.Event) -> dict:
    """用户 venv：原地装这次缺的（ADR 0019 §八 的纪律不变）。"""
    python = plan.python
    _emit(plan.plan_id, STATE_PREPARING, on_event, joint=plan)
    rc, out = _run([python, "-m", "pip", "--version"], PIP_PROBE_TIMEOUT_S)
    if rc != 0:
        raise RepairError(ERROR_PIP_UNAVAILABLE, _sanitize(out)[-800:])
    _emit(plan.plan_id, STATE_INSTALLING, on_event, joint=plan)
    pi = plan.pip_inputs
    with tempfile.TemporaryDirectory(prefix="tavotto-joint-") as tmp:
        req_file, con_file = write_plan_files(
            Path(tmp), pi.requirements, pi.constraints, hashes=pi.hashes
        )
        code, out = _run_pip_install(
            lambda index_url: pip_install_joint_argv(
                python,
                req_file,
                con_file,
                require_hashes=pi.require_hashes,
                index_url=index_url,
            ),
            python,
            cancel_ev,
            lambda text: _append_log(plan.plan_id, text, on_event),
            on_mirror=lambda url: _note_mirror(plan.plan_id, url, on_event),
            on_source=lambda src: _note_source(plan.plan_id, src, on_event),
        )
    if code == ERROR_CANCELLED:
        health = projectenv.probe_environment(python)
        detail = {"health_ok": bool(health.get("ok")), "health_code": health.get("code", "")}
        _emit(
            plan.plan_id, STATE_CANCELLED, on_event, joint=plan, code=ERROR_CANCELLED, result=detail
        )
        return {"ok": False, "code": ERROR_CANCELLED, **detail}
    if code:
        raise RepairError(code, _sanitize(out)[-800:])
    _emit(plan.plan_id, STATE_VERIFYING, on_event, joint=plan)
    _verify_imports(python, plan.needed_imports)
    selftest = worker_self_test(python)
    if not selftest.get("ok"):
        raise RepairError(ERROR_SELFTEST_FAILED, _sanitize(selftest.get("detail", ""))[-800:])
    # 自检期间接受的取消（`cancel_status` 还没过提交点）要算数：包已经在用户 venv 里了，
    # 如实报 cancelled + 体检，不接着 remember / 作废会话（Codex #461 P2）。「看事件 + 定提交」
    # 与 `cancel_status` 同一把锁，两边只会有一个赢
    with _lock:
        late_cancel = cancel_ev.is_set()
        if not late_cancel:
            _committed[plan.plan_id] = time.time()
    if late_cancel:
        health = projectenv.probe_environment(python)
        detail = {"health_ok": bool(health.get("ok")), "health_code": health.get("code", "")}
        _emit(
            plan.plan_id, STATE_CANCELLED, on_event, joint=plan, code=ERROR_CANCELLED, result=detail
        )
        return {"ok": False, "code": ERROR_CANCELLED, **detail}
    projectenv.remember(plan.project, python, automatic=False, trigger=TRIGGER_DEPENDENCY_REPAIR)
    pool.note_project_python_ok(python)
    pool.invalidate(plan.script, plan.project)
    depplan.reset_cache(python)
    _note_round(plan.project, plan.script)
    with _lock:
        _gate_skipped.discard((plan.project_id, plan.script))
    result = {
        "ok": True,
        "python": python,
        "target_kind": TARGET_PROJECT_VENV,
        "installed": _versions_of(python, [r["distribution"] for r in plan.record]),
    }
    _emit(plan.plan_id, STATE_DONE, on_event, joint=plan, result=result)
    return result


# --------------------------------------------------------------- 代事务
@dataclasses.dataclass(frozen=True)
class _GenerationJob:
    """建一代受管环境要知道的全部（四条路共用：联合准备 / 单包修复 / 重建 / 包管理首装）。"""

    progress_id: str
    project: str
    script: str  # 记轮次 / 作废会话用；重建与包管理传空串
    delta: tuple[str, ...]  # 这次新增的需求（规范串）
    constraints: tuple[str, ...]
    hashes: dict
    require_hashes: bool
    needed_imports: tuple[str, ...]
    record: tuple[dict, ...]  # 装完记进账的条目
    reason: str
    identity: str
    emit: object  # (state, **kw) -> dict
    on_log: object  # (text) -> None
    label: str = "generation"  # 快照文件名里的动作名（`after-<label>`）
    #: 计划里明示过「将下载私有 Python」的授权才为真（U05）；重建 / 包管理首装为假——
    #: 那两条路没有说出口的下载，没有基础解释器就照旧 `managed_env_unavailable`。
    provision_private: bool = False
    #: 计划里告诉用户的那段私有 Python 载荷（`offer_payload()` / `present_payload()`）：执行时来源必须还是它说的
    #: 那一处（`origin`、同一个 id），否则 `repair_plan_stale`——不静默换源、不变成联网下载（ADR 0111 §一）。
    private_plan: dict | None = None
    #: 计划的事实来自替身：供应之后按真解释器重算 delta / 关键 import / 记账（`_replan_on_base`）。
    replan: bool = False
    #: 用户确认的那份 pip 输入与影响（`plan.pip_inputs` / `plan.impact`）：替身重算之后的真输入必须与之逐项
    #: 相同，否则 `dependency_impact_changed`、不装（Codex #814 r4218254708 后续，`_replan_on_base`）。
    confirmed_inputs: "PipInputs | None" = None
    confirmed_impact: dict | None = None
    #: 单包修复才有：`_attempted` 的键 (项目指纹, 环境 key, 需求串)。**只在 pip 退出码 0 之后**登记
    #: （#466 的纪律；下载私有 Python 失败 / 取消 / pip 没跑成都不算「装过」）；其余三条路为空。
    attempted: tuple = ()
    groups: tuple[str, ...] = ()
    #: 单包修复并入联合集合的那一个包（`DependencyRequirement`）：替身重算（`_replan_on_base`）时要把它并回去。
    requested: object = None
    #: 用户确认的那份计划是按哪些输入算的（`JointRepairPlan.inputs_digest`）：重算时输入变了就停。
    inputs_digest: str = ""
    #: 「换成本作用域」（D04）：这一代只装本作用域的集合（不并入账上别的作用域的包），active 之后账一并换掉。
    replace_ledger: bool = False
    #: 租约在手之后复查全局显式解释器（E05）：装进去也不会被用的环境，一个字节都不装（联合准备用；重建 / 包管理
    #: 首装是用户对受管环境本身的明确动作，不查）。
    refuse_pinned: bool = False


def generation_requirements(
    project: str | Path,
    delta: tuple[str, ...],
    *,
    hash_mode: bool = False,
    replace: bool = False,
) -> tuple[str, ...]:
    """这一代的完整集合：adapter + 账上记过的 + 这次的（去重、稳定顺序）。

    账上的按 `distribution==resolved_version` 给（重建时装回**当时**那个版本，ADR 0019 §九
    不声称 lockfile 级复现）；这次的 delta 里若已含同名，账上那条让位（新声明更新）。

    **hash 模式只给 delta 本身**（= 整份锁）：`--require-hashes` 下每一条都得带 hash，adapter 与
    账上那些给不出——锁就是闭包，adapter 必须已经被锁钉住（计划期校验，`depplan._adapter_against_lock`），
    账上不在锁里的那些不属于这一代（Codex #461 P1）。
    """
    if hash_mode:
        return tuple(dict.fromkeys(delta))
    out: list[str] = list(depplan.ADAPTER_REQUIREMENTS)
    delta_names = {depresolve.normalize_distribution(_name_of(r)) for r in delta}
    for req in () if replace else managedenv.installed_requirements(project):
        if depresolve.normalize_distribution(_name_of(req)) in delta_names:
            continue
        if req not in out:
            out.append(req)
    for req in delta:
        if req not in out:
            out.append(req)
    return tuple(out)


def _name_of(requirement: str) -> str:
    text = requirement.split(";", 1)[0]
    for i, ch in enumerate(text):
        if ch in "[<>=!~ @":
            return text[:i]
    return text


def _run_generation(job: _GenerationJob, cancel_ev: threading.Event) -> dict:
    project = job.project
    key = _env_key(TARGET_MANAGED, "", project)
    active_python = managedenv.python_of(project) or ""
    try:
        # 锁：合成 key（序列化同一项目的两次换代）+ active 那一代的解释器（挡住包管理的原地
        # 作业同时改它——两者的账要一致）。**不收掉旧代上的 worker**（`shutdown=False`）：
        # 旧代目录不动，它们跑完自然作废；native 会话同理不杀（有就拒绝开始）。
        with pool.mutating_environment(key, active_python, shutdown=False):
            if job.refuse_pinned:
                _refuse_if_pinned()
            return _run_generation_locked(job, cancel_ev, key)
    except pool.EnvironmentBusy as exc:
        raise _busy_error(exc) from exc


def _run_generation_locked(job: _GenerationJob, cancel_ev: threading.Event, key: str) -> dict:
    project = job.project
    job.emit(STATE_PREPARING)
    managedenv.retire_unused(project, in_use=_generation_in_use)
    base = base_python()
    base_runtime = ""
    if not base and job.provision_private:
        # 计划里明示过的下载（U05，ADR 0063）：在锁内、建 venv 之前先把私有 Python 备好。
        # 取消在这一步里由 `privatepython` 按消费者处置；供应失败这一代还没登记，账不动。
        outcome = _provision_private_base(job, cancel_ev)
        if outcome.get("cancelled"):
            return job.emit(
                STATE_CANCELLED, code=ERROR_CANCELLED, result={"generation": "", "activated": False}
            )
        base, base_runtime = outcome["python"], outcome["runtime"]
    if not base:
        raise RepairError(ERROR_MANAGED_UNAVAILABLE, "这台机器上没有可以用来创建环境的 Python")
    if job.replan:
        job = _replan_on_base(job, base)
    if not base_runtime:
        base_runtime = _private_runtime_of(base)
    requirements = generation_requirements(
        project, job.delta, hash_mode=job.require_hashes, replace=job.replace_ledger
    )
    identity = job.identity or depplan._digest(
        {"requirements": sorted(requirements), "constraints": sorted(job.constraints)}
    )
    if base_runtime:
        # 私有 Python 换了版本（新 id）而项目意图没变：venv 挪不走 base，建在另一份 base 上的是
        # **另一代**——身份把 base 折进去（Codex #464 P1）；系统 base 的身份形状不变（U04 的纪律）。
        identity = depplan._digest({"identity": identity, "base_runtime": base_runtime})
    # 目录名永远不撞**在册**的代（active / 旧代还有人用）：重建两次同一份账是同一个身份，
    # 不能把 active 那代删掉重来（Codex #461 P1）
    generation = managedenv.fresh_generation(project, identity)
    if cancel_ev.is_set():
        return job.emit(
            STATE_CANCELLED,
            code=ERROR_CANCELLED,
            result={"generation": generation, "activated": False},
        )
    # ---- 建：最终目录里，先登记为 incomplete ----
    job.emit(STATE_CREATING_ENV)
    try:
        managedenv.register_generation(
            project,
            generation,
            requirements=list(requirements),
            constraints=list(job.constraints),
            identity=identity,
            base_python=base,
            base_runtime=base_runtime,
        )
    except OSError as exc:
        raise RepairError(ERROR_MANAGED_WRITE_FAILED, f"环境清单写入失败: {exc}") from exc
    ok, out = managedenv.create_generation_venv(project, generation, base)
    if not ok:
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "venv 创建失败"
        )
        raise RepairError(ERROR_MANAGED_CREATE_FAILED, _sanitize(out)[-800:])
    python = str(managedenv.generation_python(project, generation))
    pool.note_mutating_python(key, python)
    if cancel_ev.is_set():
        managedenv.mark_generation(project, generation, managedenv.GEN_STATE_INCOMPLETE, "已取消")
        return job.emit(
            STATE_CANCELLED,
            code=ERROR_CANCELLED,
            result={"generation": generation, "activated": False},
        )
    rc, out = _run([python, "-m", "pip", "--version"], PIP_PROBE_TIMEOUT_S)
    if rc != 0:
        managedenv.mark_generation(project, generation, managedenv.GEN_STATE_INCOMPLETE, "没有 pip")
        raise RepairError(ERROR_MANAGED_BROKEN, _sanitize(out)[-800:])
    # ---- 装：一次 pip，完整集合 + 约束 ----
    job.emit(STATE_INSTALLING)
    plans_dir = managedenv.env_dir(project) / "plans" / generation
    req_file, con_file = write_plan_files(
        plans_dir, requirements, job.constraints, hashes=job.hashes
    )
    code, out = _run_pip_install(
        lambda index_url: pip_install_joint_argv(
            python, req_file, con_file, require_hashes=job.require_hashes, index_url=index_url
        ),
        python,
        cancel_ev,
        job.on_log,
        on_mirror=lambda url: _note_mirror(job.progress_id, url, None),
        on_source=lambda src: _note_source(job.progress_id, src, None),
    )
    if code == ERROR_CANCELLED:
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "安装被取消"
        )
        return job.emit(
            STATE_CANCELLED,
            code=ERROR_CANCELLED,
            result={"generation": generation, "activated": False},
        )
    if code:
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, f"安装失败: {code}"
        )
        raise RepairError(code, _sanitize(out)[-800:])
    if job.attempted:
        # pip 跑成了：从这里起「再装一遍同一个需求」改变不了任何东西（验证没过也一样），
        # 防循环的黑名单这时才登记——与项目 venv 那条路 `_run_install` 的登记点同一语义
        with _lock:
            _attempted.add(job.attempted)
    # ---- 验：三层，任一步不过就是 incomplete，active 不动 ----
    job.emit(STATE_VERIFYING)
    rc, out = _run(pip_check_argv(python), PIP_PROBE_TIMEOUT_S)
    if rc != 0:
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "依赖一致性检查未通过"
        )
        raise RepairError(ERROR_CONSISTENCY, _sanitize(out)[-800:])
    try:
        _verify_imports(python, (*job.needed_imports, "matplotlib"))
    except RepairError as exc:
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "关键 import 失败"
        )
        raise exc
    if cancel_ev.is_set():
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "验证期间取消"
        )
        return job.emit(
            STATE_CANCELLED,
            code=ERROR_CANCELLED,
            result={"generation": generation, "activated": False},
        )
    selftest = worker_self_test(python)
    if not selftest.get("ok"):
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "worker 自检未通过"
        )
        raise RepairError(ERROR_SELFTEST_FAILED, _sanitize(selftest.get("detail", ""))[-800:])
    # ---- 提交点：切 active。之后拒绝取消 ----
    # 自检期间接受的取消要算数（Codex #461 P2）：「看事件 + 定提交」与 `cancel_status`「看提交 +
    # 设事件」同一把锁——接受了的取消不会与提交交错，两边只会有一个赢
    with _lock:
        if cancel_ev.is_set():
            late_cancel = True
        else:
            late_cancel = False
            _committed[job.progress_id] = time.time()
    if late_cancel:
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "验证期间取消"
        )
        return job.emit(
            STATE_CANCELLED,
            code=ERROR_CANCELLED,
            result={"generation": generation, "activated": False},
        )
    try:
        managedenv.activate(
            project, generation, python_version=managedenv.python_version_of(python)
        )
    except OSError as exc:
        # 清单没落盘 = 没切：撤回「已提交」，这一代按 incomplete 记（尽力而为），如实报失败
        with _lock:
            _committed.pop(job.progress_id, None)
        managedenv.mark_generation(
            project, generation, managedenv.GEN_STATE_INCOMPLETE, "激活清单写入失败"
        )
        raise RepairError(ERROR_MANAGED_WRITE_FAILED, f"环境清单写入失败: {exc}") from exc
    # 这一代装完之后的 freeze 快照：修复时的对照（不是回滚，ADR 0038）
    managedenv.record_snapshot(project, f"after-{job.label}", _freeze(python))
    installed = _versions_of(python, [r["distribution"] for r in job.record])
    if job.replace_ledger:
        # 「换成本作用域」：新一代只装了本作用域的集合，账要如实反映（否则重建会把别的作用域的包又装回去）
        managedenv.replace_ledger(project)
    for rec in job.record:
        managedenv.record_install(
            project,
            import_name=str(rec.get("import_name") or ""),
            distribution=str(rec["distribution"]),
            requested_specifier=str(rec.get("specifier") or ""),
            resolved_version=installed.get(
                depresolve.normalize_distribution(str(rec["distribution"])), ""
            ),
            reason=job.reason,
            # 这笔是为哪个作用域（脚本所在目录）装的：之后别的作用域的声明与它互斥时能认出来（D04）
            scope=scope_of(job.script) if job.script else "",
        )
    health = projectenv.probe_environment(python)
    projectenv.remember(
        project,
        python,
        automatic=False,
        trigger=TRIGGER_DEPENDENCY_REPAIR,
        health=health if health.get("ok") else None,
    )
    pool.note_project_python_ok(python)
    pool.reset_worker_python()
    if job.script:
        pool.invalidate(job.script, project)
        _note_round(project, job.script)
        with _lock:
            _gate_skipped.discard((managedenv.project_fingerprint(project), job.script))
    else:
        pool.invalidate_project(project)
    depplan.reset_cache(python)
    projectenv.reset_cache(project)
    retired = managedenv.retire_unused(project, in_use=_generation_in_use)
    if base_runtime:
        privatepython.touch(privatepython.source_for())
    privatepython.retire_unused(in_use=_private_runtime_in_use)
    result = {
        "ok": True,
        "python": python,
        "target_kind": TARGET_MANAGED,
        "generation": generation,
        "activated": True,
        "installed": installed,
        "retired": retired,
    }
    job.emit(STATE_DONE, result=result)
    LOG.info("受管环境换代: %s → %s（装 %d 条）", project, generation, len(requirements))
    return result


def _generation_in_use(python: str) -> bool:
    return pool.safe_workers_using(python) > 0 or bool(envlease.native_sessions_on(python))


def _private_runtime_in_use(runtime_id: str, python: str) -> bool:
    """这份私有 Python 还有人用吗：任一项目的哪一代记着它为 base（venv 挪不走 base），
    或池里 / envlease 上有会话直接用着它。"""
    if runtime_id in managedenv.referenced_base_runtimes():
        return True
    return pool.safe_workers_using(python) > 0 or bool(envlease.native_sessions_on(python))


def _private_runtime_of(base: str) -> str:
    """`base` 是不是当前锁文件那份私有 Python（是就回它的 id，给这一代记账）。"""
    source = privatepython.source_for()
    if source is None:
        return ""
    try:
        same = os.path.normcase(os.path.realpath(base)) == os.path.normcase(
            os.path.realpath(str(privatepython.runtime_python(source)))
        )
    except OSError:
        return ""
    return source.id if same else ""


def _verify_replanned_inputs(job: _GenerationJob, new: _GenerationJob, adapter) -> None:
    """重算出的真 pip 输入（同一个 `PipInputs`）必须与用户确认的逐项相同；不同 = 用户授权的不是这件事：
    把此刻的实际影响与新摘要记进进度终态（界面 / MCP 重新披露），抛 `dependency_impact_changed`，一个字节不装。"""
    confirmed = job.confirmed_inputs
    if confirmed is None:
        return
    actual = PipInputs(
        requirements=tuple(new.delta),
        constraints=tuple(new.constraints),
        hashes={k: tuple(v) for k, v in new.hashes.items()},
        require_hashes=new.require_hashes,
        adapter=tuple(adapter),
    )

    def view(pi: PipInputs) -> tuple:
        return (
            sorted(pi.requirements),
            sorted(pi.constraints),
            sorted((k, tuple(v)) for k, v in pi.hashes.items()),
            bool(pi.require_hashes),
            sorted(pi.adapter),
        )

    if view(actual) == view(confirmed):
        return
    now = {
        **(job.confirmed_impact or {}),
        "installs": sorted(actual.requirements),
        "constraints": sorted(actual.constraints),
        "adapter": sorted(actual.adapter),
        "require_hashes": bool(actual.require_hashes),
    }
    message = "私有 Python 就位后重算出的安装内容与你确认的不一致，请重新查看再确认"
    job.emit(STATE_FAILED, code=ERROR_IMPACT_CHANGED, error=message, impact=now)
    raise RepairError(ERROR_IMPACT_CHANGED, message, impact=now, impact_digest=impact_digest(now))


def _replan_on_base(job: _GenerationJob, base: str) -> _GenerationJob:
    """替身算的计划在真解释器上重算一遍：delta / 关键 import / 记账 / 身份都换成真量的（U05 PR B）。

    干净机器上第一份计划的事实是替身（锁的版本 + 这台机器的平台字段、已装为空）；私有 Python 落盘之后
    marker 环境要按它真量。真量出来 blocked → `dependency_plan_blocked`（这一代还没登记）；
    `nothing_needed` 照样建（没有别的解释器，环境本身就是要的）。"""
    facts = depplan.fresh_venv_facts(
        base, use_cache=False, provided=depplan.adapter_distributions()
    )
    if facts is None:
        raise RepairError(privatepython.ERROR_LAUNCH_FAILED, "私有 Python 量不出目标事实")
    plan = depplan.plan(
        job.project,
        job.script,
        facts=facts,
        target_kind=TARGET_MANAGED,
        groups=list(job.groups) or None,
    )
    # 重算读的是**此刻**的脚本与声明：用户确认的是按当时输入算的那份。下载期间脚本多了一个 import、
    # requirements 多了一行，就不能顶着旧 plan_id 装进去——输入指纹不同即 stale，一个字节不装（Codex #475 P1）
    if plan.inputs_digest != job.inputs_digest:
        raise RepairError(ERROR_PLAN_STALE, "下载期间脚本或依赖声明发生了变化")
    if plan.status == depplan.STATUS_BLOCKED:
        raise RepairError(ERROR_PLAN_BLOCKED, "联合计划不可执行", joint=plan.to_payload())
    if job.requested is not None:
        # 单包修复：用户点的那个包始终在集合里（联合计划量不出它时也不丢）
        wide = _fold_requested(job.requested, plan)
        if wide is None:
            raise RepairError(ERROR_PLAN_BLOCKED, "联合计划不可执行", joint=plan.to_payload())
        new = dataclasses.replace(
            job,
            delta=wide.requirements,
            constraints=wide.constraints,
            hashes={},
            require_hashes=False,
            needed_imports=wide.needed_imports,
            record=wide.record,
            identity="",
            replan=False,
        )
        # 单包的 adapter 是常量（`generation_requirements` 恒并入），与确认时同一份
        _verify_replanned_inputs(
            job, new, job.confirmed_inputs.adapter if job.confirmed_inputs else ()
        )
        return new
    new = dataclasses.replace(
        job,
        delta=tuple(plan.requirements),
        constraints=tuple(plan.constraints),
        hashes={k: tuple(v) for k, v in plan.hashes.items()},
        require_hashes=plan.require_hashes,
        needed_imports=tuple(m["import_name"] for m in plan.missing),
        record=tuple(
            {
                "import_name": m["import_name"],
                "distribution": m["distribution"],
                "specifier": ",".join(m["specifiers"]),
            }
            for m in plan.missing
        ),
        identity=plan.identity,
        replan=False,
    )
    _verify_replanned_inputs(job, new, plan.adapter)
    return new


def _provision_private_base(job: _GenerationJob, cancel_ev: threading.Event) -> dict:
    """事务里的「先备好私有 Python」一步：进度以 `downloading_python` 状态外露（stage / 字节数）。

    回 `{"python", "runtime"}`；消费者取消回 `{"cancelled": True}`；别的失败原样带 code 抛
    `RepairError`（`private_python_*` 闭集）。成功后刷新基础解释器缓存——同一进程里下一次计划
    直接看见它。"""
    source = privatepython.source_for()
    if source is None:
        raise RepairError(privatepython.ERROR_NOT_OFFERED, "这个目标上不提供私有 Python")
    planned = job.private_plan or {}
    if planned.get("id") and planned["id"] != source.id:
        # 确认之后锁文件换了（升级 / 换版本）：要准备的已不是用户看过的那一份（版本 / 字节数 / 来源都可能变）
        raise RepairError(ERROR_PLAN_STALE, "确认之后要准备的 Python 换了一份")
    # 执行只从计划说过的那一处取（ADR 0111 §一）：offer 的 `origin`，已就位载荷（`required=False`）则要求它仍在
    required_origin = planned.get("origin") or (
        privatepython.REQUIRE_PRESENT if planned.get("required") is False else None
    )
    # 字节从哪来（安装包附带 / 缓存 / 下载）：进度里与计划载荷同一个字段名，界面据此说「正在准备」还是
    # 「正在下载」——就是计划里说的那一处
    payload = {
        **source.to_payload(),
        "origin": required_origin
        if required_origin in privatepython.ORIGINS
        else privatepython.archive_origin(source),
    }

    def _progress(stage: str, done: int, total: int) -> None:
        job.emit(
            STATE_DOWNLOADING_PYTHON,
            result={
                "download": {"stage": stage, "done_bytes": int(done), "total_bytes": int(total)},
                # 换了镜像（ADR 0063 修订 2026-09-29 / ADR 0112）时进度说出此刻真在下的那个主机
                "private_python": {
                    **payload,
                    "source_host": privatepython.downloading_from(source) or payload["source_host"],
                },
            },
        )

    _progress(privatepython.STAGE_DOWNLOADING, 0, source.size)
    try:
        python = privatepython.provision(
            source, cancel_ev=cancel_ev, on_progress=_progress, required_origin=required_origin
        )
    except privatepython.ProvisionError as exc:
        if exc.code == privatepython.ERROR_CANCELLED:
            return {"cancelled": True}
        if exc.code == privatepython.ERROR_SOURCE_CHANGED:
            # 计划说的来源此刻不成立：不换源，让用户按此刻的情况重新确认（重新规划会把新的来源 / 字节数说出口）
            raise RepairError(ERROR_PLAN_STALE, str(exc)) from exc
        raise RepairError(exc.code, str(exc), **exc.detail) from exc
    global _base_python, _base_python_known
    with _lock:
        _base_python, _base_python_known = python, True
    return {"python": python, "runtime": source.id}


def _versions_of(python: str, distributions: list[str]) -> dict[str, str]:
    inv = inventory(python) or {}
    out: dict[str, str] = {}
    for dist in distributions:
        key = depresolve.normalize_distribution(dist)
        rec = inv.get(key)
        if rec:
            out[key] = str(rec.get("version") or "")
    return out


# --------------------------------------------------------------- 文件与 argv（唯一出处）
def write_plan_files(
    directory: Path,
    requirements: tuple[str, ...] | list[str],
    constraints: tuple[str, ...] | list[str],
    *,
    hashes: dict | None = None,
) -> tuple[Path, Path]:
    """把计划写成 pip 的需求文件与约束文件——**内容由我们从解析结构生成**，每一行都过一遍
    `depresolve.parse_intent` 的形状关（requirement / constraint 之外的一律拒绝）。`--hash`
    只能出现在需求文件里（pip 的规定）。"""
    directory.mkdir(parents=True, exist_ok=True)
    req_lines: list[str] = []
    for req in requirements:
        it = depresolve.parse_intent(req)
        if it is None or not it.declared:
            raise RepairError(ERROR_REQUIREMENT_INVALID, f"需求串不合形状: {req!r}")
        line = depresolve.requirement_string(it)
        for h in (hashes or {}).get(req, ()):
            if not depresolve._HASH_RE.match(h):
                raise RepairError(ERROR_REQUIREMENT_INVALID, f"hash 不合形状: {h!r}")
            line += f" --hash={h}"
        req_lines.append(line)
    con_lines: list[str] = []
    for con in constraints:
        it = depresolve.parse_intent(con)
        if it is None or not it.declared:
            raise RepairError(ERROR_REQUIREMENT_INVALID, f"约束串不合形状: {con!r}")
        con_lines.append(depresolve.requirement_string(it))
    req_file = directory / "requirements.txt"
    con_file = directory / "constraints.txt"
    req_file.write_text("\n".join(req_lines) + "\n", encoding="utf-8")
    con_file.write_text("\n".join(con_lines) + "\n", encoding="utf-8")
    return req_file, con_file


def pip_install_joint_argv(
    python: str,
    requirements_file: Path,
    constraints_file: Path,
    *,
    require_hashes: bool = False,
    index_url: str | None = None,
) -> list[str]:
    """联合安装命令——**唯一出处**，测试逐字节钉住。与 `pip_install_argv` 只差在需求从文件
    来（`-r` / `-c` 指向我们自己生成的两份文件），其余参数逐字相同、同样没有 `--upgrade`；
    `index_url` 同样只在镜像重试那一次带（ADR 0111）。"""
    argv = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
    ]
    if index_url:
        argv += ["--index-url", index_url]
    argv += [
        "-r",
        str(requirements_file),
        "-c",
        str(constraints_file),
    ]
    if require_hashes:
        argv.append("--require-hashes")
    return argv


def pip_check_argv(python: str) -> list[str]:
    """依赖一致性检查——唯一出处。"""
    return [str(python), "-m", "pip", "check", "--disable-pip-version-check", "--no-input"]


_IMPORTS_PROBE_SRC = r"""
import json, sys
out = {}
for name in sys.argv[1:]:
    try:
        __import__(name)
        out[name] = ""
    except Exception as exc:
        out[name] = "%s: %s" % (type(exc).__name__, exc)
sys.stdout.write(json.dumps(out))
"""


def probe_imports(python: str, names: tuple[str, ...] | list[str]) -> dict[str, str]:
    """一个子进程里逐个 import；回 `{名字: 错误串（空 = 成功）}`。起不来时每个名字都带错误。

    启动条件与 worker 对齐（不带 `-I`、env 继承、cwd 空目录）；多一个 `-B`（只读探测不写 .pyc，
    `runtime.probe_args`）。名字先过形状关。
    """
    names = tuple(n for n in names if projectenv.valid_module_name(n))
    if not names:
        return {}
    scratch = ""
    try:
        scratch = projectenv._probe_scratch_dir()
        proc = subprocess.run(
            [str(python), *runtime.probe_args(), "-c", _IMPORTS_PROBE_SRC, *names],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=projectenv.PROBE_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            cwd=scratch,
            env=runtime.owned_env(python),
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {n: str(exc)[:200] for n in names}
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {n: (proc.stderr or "起不来")[:200] for n in names}
    return {n: str(data.get(n, "no result")) for n in names}


def _verify_imports(python: str, names: tuple[str, ...]) -> None:
    failed = {n: e for n, e in probe_imports(python, names).items() if e}
    if failed:
        raise RepairError(
            ERROR_IMPORT_STILL_FAILED,
            "; ".join(f"{n}: {e}" for n, e in failed.items())[:800],
            imports=failed,
        )


def cancel_status(plan_id: str) -> dict:
    """取消请求的答复：接受了 / 已过提交点 / 没有这个计划。"""
    pid = str(plan_id or "")
    with _lock:
        if pid in _committed:
            return {"accepted": False, "reason": "committed"}
        ev = _cancels.get(pid)
        if ev is None:
            return {"accepted": False, "reason": "not_found"}
        ev.set()  # 与事务「定提交」那一下同一把锁：接受了就不会再被提交
    return {"accepted": True, "reason": ""}


# --------------------------------------------------------------- 跑前的门（ADR 0061 §六）
#
# 起第一个 worker **之前**看一眼：脚本开跑要的第三方包目标环境里缺不缺、缺的能不能一次装
# 全。能（`JointPlan.status == ready`）就不起会话，以 `dependency_preparation_required` 把整份
# 计划交出去让用户授权一次——与 U03 的工作目录门同一处、同一形状（`pool._new_worker`）。
# 不能（`blocked` / `nothing_needed`）就放行：诊断挂在计划上，脚本照跑。
#
# 门**一直问到有答案**：答案要么是一次成功的准备（之后计划就是 nothing_needed），要么是用户
# 明确说「不准备，直接跑」（`skip_preparation`，每进程每 (项目, 脚本) 记一次；脚本以
# `missing_dependency` 收场时看到的是同一份联合 offer——运行后那条路，轮次
# `MAX_DEPENDENCY_REPAIR_ROUNDS` 兜底）。没有轮次了也放行。「有答案才放行 + 轮次上限」
# 一起就是「无无限缺包循环」——而不是让同一个动作第二次悄悄变成另一种行为。
ERROR_PREPARATION_REQUIRED = "dependency_preparation_required"
#: 采用前复核时联合计划算不出来（解释器解析失败等）：不知道脚本要什么，就不能说这个环境「装齐」——
#: 拿空的需求集合去量，任何健康的环境都会「装齐」并被记下（Codex #562 P2）。
ERROR_USER_ENV_UNVERIFIABLE = "user_environment_unverifiable"
#: 本模块里**常量式**、且会落到用户界面的 code（`tests/test_error_codes.py` 的码表读它；
#: `RepairError` 那一族的文案在前端 `engine.repairError.*` 表里，按既有约定不进这里）。
ERROR_CODES = (ERROR_PREPARATION_REQUIRED, ERROR_USER_ENV_UNVERIFIABLE)
_gate_skipped: set[tuple[str, str]] = set()


def skip_preparation(project: str | Path, script: str) -> None:
    """用户明确说「不准备，直接跑」：这一对从此放行（进程内；一次成功的准备会清掉它）。"""
    with _lock:
        _gate_skipped.add((managedenv.project_fingerprint(str(Path(project))), script))


def preparation_skipped(project: str | Path, script: str) -> bool:
    with _lock:
        return (managedenv.project_fingerprint(str(Path(project))), script) in _gate_skipped


def joint_targets(project: str | Path, target_kind: str, python: str) -> list[dict]:
    """联合准备可选的目标（与 `offer()` 的 `targets` 同一形状，少了 system 那一档——跑前没有
    「已经装着它的解释器」这一说，那是运行后体检出来的）。"""
    root = str(Path(project))
    out: list[dict] = []
    if target_kind == TARGET_PROJECT_VENV and python:
        out.append(
            {
                "kind": TARGET_PROJECT_VENV,
                "venv": projectenv.project_relative(root, str(Path(python).parent.parent)) or "",
                "python": projectenv.project_relative(root, python) or python,
                "modifies_user_environment": True,
                "creates_environment": False,
                "available": True,
                "reason": "",
            }
        )
    managed = managedenv.state(root)
    # 门这一侧同步问基础解释器（`base_python()` 有进程内缓存；门本来就在准备计划里跑，等一次探测是
    # 合理的）——三态的 `managed_available()` 留给渲染出错那条响应路径（`offer()`）
    # 受管目标**每次**都建新的一代（有没有 active 代都一样），所以「可用」看的是有没有基础解释器；
    # 没有但本目标提供私有 Python（U05）：这条路仍可用，授权里多一项「先下载 N 字节」
    base = base_python()
    if not base:
        private = privatepython.offer_payload()
    elif _private_runtime_of(base):
        # 基础解释器就是已就位的私有 Python（探测链末级：这台机器没有别的）：不下载，但来源要说出口
        private = privatepython.present_payload()
    else:
        private = None
    available = bool(base) or private is not None
    out.append(
        {
            "kind": TARGET_MANAGED,
            "venv": "",
            "python": "",
            "modifies_user_environment": False,
            "creates_environment": not managed["exists"],
            "available": available,
            "reason": "" if available is not False else ERROR_MANAGED_UNAVAILABLE,
            "private_python": private,
        }
    )
    return out


# --------------------------------------------------------------- 用户自己的环境（ADR 0079）
#
# 内置 / 受管环境缺包时，先看用户自己平时跑脚本的那个 Python 是不是早就装齐了：装齐的里挑最好的
# **直接改用**（记成本项目的自动决策，可撤销），一个都没有才走授权安装那条路。这推翻了 ADR 0044 §二
# 「系统解释器只列候选、采用要用户点一次」——用户的原话是「智能识别哪个环境最好，自动选用」；挑选的
# 判据写死在 `userenvs.rank()`，撤销（界面上的「改回」= `remember_default`）之后不再自动挑。
TRIGGER_USER_ENVIRONMENT = "user_environment"
_adoption_listeners: list = []


def on_user_environment_adopted(listener) -> None:
    """自动改用了用户的环境时回调 `listener(project, entry)`（app 据此发 SSE，界面给一条可撤销的提示；
    `entry` 是 `userenvs.public()` 的形状，不带路径）。"""
    if listener not in _adoption_listeners:
        _adoption_listeners.append(listener)


def user_environment_candidates(
    project: str | Path, script: str, *, exclude: str = ""
) -> list[dict]:
    """发现到的用户环境 + 老链条里的系统解释器（同一张表，去重、保序），去掉 `exclude`（正缺包的那个）。"""
    root = str(Path(project))
    out: list[dict] = []
    silent = projectenv.silent_adoption_enabled()
    if not silent:
        # 确认模式（ADR 0114）：项目自己的 venv 是第一个候选，用户在这张表里点「改用」才算采用。静默采用时代
        # 它归 `pool` 第 4 档管、不进这张表（`_auto_adopt_allowed` 不碰项目 venv）
        for venv in projectenv.discover(root, script):
            py = projectenv.interpreter_of(venv, root=root)
            if py:
                out.append(
                    {"python": py, "source": userenvs.SOURCE_PROJECT_VENV, "label": Path(venv).name}
                )
    # 登录 shell 只在静默采用的旧行为下现问；确认模式只用明确检查动作已经问出来的答案
    out += (
        userenvs.discover(root, script)
        if silent
        else userenvs.discover(root, script, ask_login_shell=None)
    )
    out += [
        {"python": py, "source": userenvs.SOURCE_SYSTEM, "label": ""}
        for py, _src in pool.system_python_candidates()
    ]
    seen: set = set()
    uniq: list[dict] = []
    skip = userenvs._key(exclude) if exclude else None
    for c in out:
        key = userenvs._key(c["python"])
        if key in seen or key == skip:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def recheck_user_environment(project: str | Path, script: str, env_id: str) -> dict | None:
    """界面交回的 id → 按**此刻的**联合计划重新体检的那一条（`userenvs.evaluate` 的形状，不读体检缓存）；
    本机的发现结果里找不到回 None；计划算不出来抛 `WorkerError(code=user_environment_unverifiable)`。

    「还被发现得到」不等于「还能用」：弹窗开着期间环境可能变了（包被卸掉、解释器坏了），调用方也可能交回
    一个本来就没装齐（界面上不可选）的候选。所以采用前与弹窗列出时用同一个判据再量一次——装齐 = 脚本开跑
    要的 import（计划的 `needed`：此刻缺的与此刻有的都算）与映射不到包名的 import 全部 import 得到。"""
    root = str(Path(project))
    cand = next(
        (
            c
            for c in user_environment_candidates(root, script)
            if userenvs.env_id(c["python"]) == env_id
        ),
        None,
    )
    if cand is None:
        return None
    try:
        plan = joint_plan_for(root, script)[0].to_payload()
    except pool.WorkerError as exc:
        raise pool.WorkerError(
            "现在算不出这个脚本需要哪些包，没法确认这个环境装齐了，请重新检查",
            code=ERROR_USER_ENV_UNVERIFIABLE,
        ) from exc
    needed, unknown = _plan_imports(plan)
    return userenvs.evaluate([cand], needed, unknown, use_cache=False)[0]


def _plan_imports(plan: dict) -> tuple[list[dict], list[str]]:
    """联合计划载荷里「候选环境要 import 得到」的两份：脚本开跑要的第三方包（`missing` + `satisfied`，
    带 distribution）与映射不到包名的。**不只是 `missing`**：`missing` 是相对**此刻的**解释器量的差集——
    内置 runtime 里有 numpy、缺 openpyxl 时它只有 openpyxl，一个只装了 openpyxl 的环境就会被判「装齐」、
    自动改用，脚本接着在 numpy 上缺包。"""
    needed = [
        {"import_name": m.get("import_name", ""), "distribution": m.get("distribution", "")}
        for m in [*(plan.get("missing") or []), *(plan.get("satisfied") or [])]
    ]
    return needed, list(plan.get("unknown") or [])


def _user_env_discovery_off() -> bool:
    # 判据唯一出处在 projectenv：运行后缺包的接手（`pool.try_project_env`，ADR 0107）读同一个开关
    return projectenv.auto_adoption_off()


def unknown_imports_missing(plan: dict, python: str) -> list[str]:
    """计划里映射不到包名的无条件 import，此刻的解释器里**确实** import 不到的那几个（ADR 0079 修订
    2026-09-25，QA ENV-08-B1）。

    这类 import 永远不装（FO-034），计划因此是 `nothing_needed`、不是 `ready`，以前也就从不去找用户环境——
    装了这个包的 Conda 环境就在磁盘上，脚本照样在内置环境里缺包失败。现在先在此刻的解释器里量一次
    （与「装齐」同一条体检、同一个缓存）：import 得到的不算缺，量不出的不算缺，条件式 import 本来就不在
    `unknown` 里。开关关着时不量。"""
    if _user_env_discovery_off() or not python:
        return []
    unknown = [u for u in plan.get("unknown") or [] if u]
    if not unknown:
        return []
    # 内置 runtime 由 worker 按 `runtime.child_env()` / `child_args()` 起：体检用同一套（Codex #609 P2）
    bundled = pool.same_python(python, runtime.bundled_python())
    return userenvs.imports_missing(python, unknown, bundled=bundled)


def user_environment_offer(project: str | Path, script: str, plan: dict, python: str) -> list[dict]:
    """对一份 `ready` 的联合计划（或只缺映射不到包名的 import 的计划，见 `unknown_imports_missing`）：
    每个用户环境装没装齐（`userenvs.evaluate` 的结果表，按挑选顺序排）。"""
    if _user_env_discovery_off():
        # 关掉：不发现、不体检、不自动改用（用户的逃生口；测试进程默认关，见 tests/conftest.py）
        return []
    root = str(Path(project))
    needed, unknown = _plan_imports(plan)
    if not needed and not unknown:
        return []
    # 确认模式（ADR 0114）：门不为了「显示推荐」去起候选解释器——只给已有的检查结论，没检查过的列成
    # `checked=False`，用户点「检查并使用」时才由采用端点（`recheck_user_environment`）现场体检
    extra = {} if projectenv.silent_adoption_enabled() else {"cache_only": True}
    entries = userenvs.evaluate(
        user_environment_candidates(root, script, exclude=python), needed, unknown, **extra
    )
    name = Path(root).name
    return sorted(entries, key=lambda e: (not e["satisfies"], userenvs.rank(e, name)))


def _auto_adopt_allowed(project: str, offer: dict) -> bool:
    """只有「此刻的解释器是机器替用户挑的」时才自动换：用户显式选过的（环境变量 / 设置 / 为本项目挑的 /
    明确选回默认链条）一个都不碰；项目自己的 venv 也不碰——那本来就是用户的环境，缺包该装进它。"""
    if offer.get("target_kind") == TARGET_PROJECT_VENV or offer.get("clean_machine"):
        return False
    # 「机器替用户挑的」判据唯一出处 `pool.machine_chosen_interpreter`（运行后缺包的接手同用，ADR 0107）
    return pool.machine_chosen_interpreter(project)


#: 项目记录这一半的判据，唯一出处在 projectenv（运行后缺包的接手同用）
_record_allows_auto_adopt = projectenv.record_allows_auto_adopt


def _auto_adopt(project: str, offer: dict, user_envs: list[dict]) -> dict | None:
    # 正被改动的环境不采用（envlease 是「环境占用」的唯一一张表）：它此刻的体检结论读的是装了一半的
    # site-packages，记下来就是把一次半成品的观测变成项目的决策。这只是挑选时的过滤；真正挡住
    # 「在被占用的环境上起会话」的是调用方的顺序——决定落地之后才解析解释器、才查租约（`decide_environment`）
    free = [e for e in user_envs if not envlease.is_mutating(e["python"])]
    entry = userenvs.best(free, Path(project).name)
    if entry is None or not _auto_adopt_allowed(project, offer):
        return None
    # 上面判过的「项目记录允许自动换」在写入锁里再判一次：判完到写之间用户的显式选择（设置里挑了一个 /
    # 点了「改回」）可能刚落地，不许被这条自动决策盖掉（ADR 0079 §四：用户决定过的一个都不碰）
    if not projectenv.remember(
        project,
        entry["python"],
        automatic=True,
        trigger=TRIGGER_USER_ENVIRONMENT,
        health=entry,
        only_if=_record_allows_auto_adopt,
    ):
        return None
    # 不调 `pool.reset_worker_python()`：`remember()` 已经更新了项目级解析缓存，全局链条的缓存与项目
    # 决策无关。（以前这里跑在持有 `pool._lock` 的 `_new_worker()` 里，再拿锁就是死锁——2026-09-23 真机
    # 抓到；现在 `pool.acquire()` 在锁外、起会话之前调它，但仍没有理由去碰全局缓存。）
    LOG.info("缺包：自动改用用户环境 %s（%s）", entry["python"], entry.get("source"))
    for listener in list(_adoption_listeners):
        try:
            listener(project, userenvs.public(entry))
        except Exception:  # noqa: BLE001 — 通知失败不能挡住渲染
            LOG.exception("用户环境改用通知失败")
    return entry


def preparation_offer(project: str | Path, script: str) -> dict | None:
    """公开的那一份（HTTP / MCP / 渲染错误载荷都是它）：用户环境只带不透明 id，不带路径。"""
    got = _preparation_offer(project, script)
    return got[0] if got is not None else None


def _preparation_offer(project: str | Path, script: str) -> tuple[dict, list[dict]] | None:
    """跑前 / 准备计划要看的东西（**只读，不装**）：联合计划 + 可选目标 + 轮次。

    解释器解析不出来（显式选择失效 / 一个 Python 都没有）回 None：那是另一条错误，让原路径
    去报，这里不替它说话。
    """
    root = str(Path(project))
    try:
        joint, target_kind, python = joint_plan_for(root, script)
    except pool.WorkerError:
        return None
    # 干净机器（U05 PR B）：`joint_plan_for` 已经以私有 Python 为目标算过了。`clean_machine` 是门的判据
    # （nothing_needed 也问：没有任何解释器可跑，环境本身就是要授权的东西）；`private_python` 是给界面说出口的
    # 载荷——要下载（`required=True`、字节数）或已就位（别的项目供应过、`required=False`、不联网）。两者独立：
    # 运行时已在 ≠ 本项目不用建代（Codex #475 P1）
    clean = private_python_target(root, script) is not None
    private = None
    if clean:
        private = privatepython.offer_payload() or privatepython.present_payload()
    plan_payload = joint.to_payload()
    user_envs: list[dict] = []
    unknown_missing: list[str] = []
    if joint.status == depplan.STATUS_READY and not clean:
        user_envs = user_environment_offer(root, script, plan_payload, python)
    elif joint.status == depplan.STATUS_NOTHING_NEEDED and not clean:
        # 没有能装的，但有映射不到包名、此刻又确实 import 不到的：同样的三步去找用户环境（只找、只改用，
        # 仍不装）。门不因此弹框——弹窗是「授权安装」，这里没有可装的东西；决定在 `decide_environment`
        unknown_missing = unknown_imports_missing(plan_payload, python)
        if unknown_missing:
            user_envs = user_environment_offer(root, script, plan_payload, python)
    # 授权的实际影响（T06）：只有真能授权的计划才有——与 `create_joint_plan` 绑定出的计划同一个函数算出，
    # 用户确认的是这一份的摘要，执行前比的也是它
    # 全局显式解释器压着（E05）：装进受管环境 / 项目 venv 都不会被用，没有可授权的影响，也不给"换成本作用域"；
    # 只把"是谁锁的"说出口（来源与变量名，不带路径——公开投影）
    pinned = pinned_payload()
    impact = None
    if pinned is None and (
        joint.status == depplan.STATUS_READY
        or (clean and joint.status == depplan.STATUS_NOTHING_NEEDED)
    ):
        impact = offer_impact(root, joint, target_kind, python)
    # 作用域互斥（D04）：不并入，但给一条明确的出路——「换成本作用域」的计划与影响（它自己的摘要，更大的影响：
    # 账上别的作用域的包不再 active）
    scope_switch = None
    if (
        pinned is None
        and joint.status == depplan.STATUS_BLOCKED
        and any(b.get("code") == depplan.BLOCK_SCOPE_CONFLICT for b in joint.blocked)
    ):
        scope_switch = _scope_switch_offer(root, script, target_kind, python)
    offer = {
        "code": ERROR_PREPARATION_REQUIRED,
        "script": script,
        "plan": plan_payload,
        "impact": impact,
        "impact_digest": impact_digest(impact) if impact else "",
        "scope_switch": scope_switch,
        "pinned": {"source": pinned["source"], "variable": pinned["variable"]} if pinned else None,
        "target_kind": target_kind,
        "targets": joint_targets(root, target_kind, python),
        "rounds_remaining": rounds_remaining(root, script),
        "skipped": preparation_skipped(root, script),
        "clean_machine": clean,
        "private_python": private,
        # 用户自己的环境（ADR 0079）：装齐的排前面、按挑选顺序；界面据此列「改用这个环境」。
        # 只带 id 不带路径（ADR 0053 §二）；采用时 `PATCH /api/engine/environment` 交回 id
        "user_environments": [userenvs.public(e) for e in user_envs],
        # 此刻的解释器里确实 import 不到、又映射不到包名的那几个（ADR 0079 修订）：只有 import 名，不带路径
        "unknown_missing": unknown_missing,
    }
    return offer, user_envs


def _scope_switch_offer(root: str, script: str, kind: str, python: str) -> dict | None:
    """「换成本作用域」的可授权形态：按新一代只装本作用域的集合重新算一遍（`scope_policy=switch`），回
    `{impact, impact_digest, requirements, drops}`；算不出来（目标量不出 / 计划不可执行）回 None。"""
    try:
        joint, _kind, _python = joint_plan_for(root, script, scope_policy=SCOPE_POLICY_SWITCH)
    except pool.WorkerError:
        return None
    if joint.status != depplan.STATUS_READY:
        return None
    impact = offer_impact(root, joint, kind, python, SCOPE_POLICY_SWITCH)
    if impact is None:
        return None
    return {
        "impact": impact,
        "impact_digest": impact_digest(impact),
        "requirements": list(joint.requirements),
        "drops": list(impact["drops"]),
        "changes": list(impact["changes"]),
    }


def _gate_open(root: str, script: str) -> bool:
    """门还问不问（没轮次了 / 用户说过「直接跑」就不问，也不替它换环境）。"""
    return rounds_remaining(root, script) > 0 and not preparation_skipped(root, script)


def decide_environment(project: str | Path, script: str) -> dict | None:
    """「换不换解释器」的**唯一一处**决定（ADR 0079 §四）：缺包且有装齐的用户环境、此刻的解释器又是
    机器替用户挑的，就把它记成本项目的自动决策；回采用的那一条（`userenvs.evaluate` 的形状），不换回 None。

    **必须在「解析解释器」之前调**，它之后的一切都读决定之后的世界：准备计划的快照（`preparation.plan_for`：
    解释器、LaunchContext、环境事实——否则 `_stale_reason` 拿旧快照比新决策，第一次准备就以
    `preparation_plan_stale` 收场）与起会话前的租约检查（`pool.acquire` 经 `pool.ENVIRONMENT_DECIDERS`：
    否则 `is_mutating` 查的是旧解释器，worker 却起在刚换上、可能正被装包的那一个上）。以前这件事藏在
    `gate()` 里、门又跑在快照与租约检查之后——两条 Codex #522 P1 是同一个顺序错误。

    工作目录还要先问时不决定：那道门排在依赖门前面，没答之前不起会话，也就轮不到换环境。"""
    if not projectenv.silent_adoption_enabled():
        # 确认模式（ADR 0114）：「换不换解释器」不再由机器决定。候选环境的体检结果仍随跑前的门 / 修复
        # 卡片的载荷（`user_environments`）交给用户，采用是他点的那一下——这里连门都不必问（不去量一遍
        # 只为了发现自己不该做这个决定）
        return None
    root = str(Path(project))
    if not _gate_open(root, script):
        return None
    if workdir.decision_for(root, script)["needs_confirmation"]:
        return None
    got = _preparation_offer(root, script)
    if got is None:
        return None
    offer, user_envs = got
    if offer["plan"]["status"] != depplan.STATUS_READY and not offer.get("unknown_missing"):
        return None
    return _auto_adopt(root, offer, user_envs)


def gate(project: str | Path, script: str) -> dict | None:
    """起会话前的门：计划 `ready`、还有轮次、用户没说过「直接跑」→ 回载荷（调用方据此不起会话）；
    否则 None（放行）。

    门**只读**，不换解释器：换不换在 `decide_environment()` 里、在解析解释器之前已经决定过了——决定换了，
    这里按新解释器算出来的计划就是 `nothing_needed`，自然放行。"""
    root = str(Path(project))
    if not _gate_open(root, script):
        return None
    got = _preparation_offer(root, script)
    if got is None:
        return None
    offer, _user_envs = got
    if offer.get("pinned"):
        # 全局显式解释器压着：授权安装没有意义（装进去不会被用）。门放行，脚本缺包时以 `missing_dependency` + 带 `pinned`
        # 的修复 offer 收场——告诉用户怎么解开锁，而不是先让他确认一次必败的安装（E05）
        return None
    if offer["plan"]["status"] == depplan.STATUS_READY:
        return offer
    # 干净机器：什么都不缺也没有解释器可跑——环境（含私有 Python）本身就是要授权的东西。判据是
    # `clean_machine`，不是有没有下载载荷：私有 Python 被别的项目供应过之后本项目照样一个解释器都没有、
    # 照样要建自己的一代（Codex #475 P1：只看载荷会把它放行成 no_worker_python）
    if offer.get("clean_machine") and offer["plan"]["status"] == depplan.STATUS_NOTHING_NEEDED:
        return offer
    return None


def _spawn_gate(figures_dir: str, script_name: str) -> None:
    """挂在 `pool.SPAWN_GATES` 上的那一份：要问就抛带载荷的 `WorkerError`。"""
    offer = gate(figures_dir, script_name)
    if offer is None:
        return
    missing = ", ".join(m["distribution"] for m in offer["plan"]["missing"])
    err = pool.WorkerError(
        f"这个脚本开跑就需要的包目标环境里没有：{missing}。Tavotto 可以一次装全再继续；"
        "先授权，或明确选择不准备直接运行。",
        code=ERROR_PREPARATION_REQUIRED,
    )
    err.dependency_preparation = offer
    err.script_name = script_name
    raise err


pool.register_spawn_gate(_spawn_gate)
pool.register_environment_decider(decide_environment)


#: 日志里按闭集明文放行的失败码：本模块全部 `ERROR_*` 的值 + 私有 Python 的（`_log_repair_failure` /
#: `_log_pip_outcome`）。在模块末尾算一次——`ERROR_*` 分散在全文件各处，放在这里才收得全。
LOGGED_ERROR_CODES = frozenset(
    v for k, v in dict(globals()).items() if k.startswith("ERROR_") and isinstance(v, str)
) | frozenset(privatepython.ERROR_CODES)
