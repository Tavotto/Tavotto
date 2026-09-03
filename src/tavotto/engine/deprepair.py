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
from pathlib import Path

from . import depresolve, envlease, execspec, managedenv, pool, projectenv, runcodes, runtime

LOG = logging.getLogger("tavotto.deprepair")

# ---------------------------------------------------------------------------
# 稳定错误码（协议契约：code 不许改名，文案随便改）
# ---------------------------------------------------------------------------
ERROR_UNRESOLVED = "dependency_unresolved"
ERROR_NOT_ALLOWED = "dependency_install_not_allowed"
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

# ---------------------------------------------------------------------------
# 目标环境
# ---------------------------------------------------------------------------
TARGET_PROJECT_VENV = "project_venv"
TARGET_MANAGED = "tavotto_managed"
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

    def to_payload(self) -> dict:
        """交给前端的形态。**不出绝对路径**（项目内的出项目相对）。"""
        return {
            "plan_id": self.plan_id,
            "target_kind": self.target_kind,
            "python": projectenv.project_relative(self.project, self.python)
            or ("" if self.creates_environment else "…"),
            "creates_environment": self.creates_environment,
            "modifies_user_environment": self.modifies_user_environment,
            "network_required": self.network_required,
            "expires_at": int(self.expires_at),
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
    return f"{data.get('created_at', 0)}:{_fingerprint_project_venv(python)}"


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
    global _base_python, _base_python_known
    with _lock:
        if project is None:
            _plans.clear()
            _progress.clear()
            _cancels.clear()
            _rounds.clear()
            _attempted.clear()
            _base_python, _base_python_known = None, False
            return
        pid = managedenv.project_fingerprint(project)
        for key in [k for k, p in _plans.items() if p.project_id == pid]:
            _plans.pop(key, None)
        for key in [k for k in _rounds if k[0] == pid]:
            _rounds.pop(key, None)
        for key in [k for k in _attempted if k[0] == pid]:
            _attempted.discard(key)


def _prune_plans() -> None:
    now = time.time()
    with _lock:
        for key in [k for k, p in _plans.items() if p.expires_at < now]:
            _plans.pop(key, None)


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
    global _base_python, _base_python_known
    found = managedenv.base_python()
    with _lock:
        _base_python, _base_python_known = found, True


def base_python() -> str | None:
    """基础解释器（同步；没有回 None）。计划创建那条路上用它。"""
    global _base_python, _base_python_known
    with _lock:
        if _base_python_known:
            return _base_python
    found = managedenv.base_python()
    with _lock:
        _base_python, _base_python_known = found, True
    return found


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
    }
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
    detail = project_env or {}
    if detail.get("code") == projectenv.ERROR_MODULE_MISSING:
        venv = detail.get("venv") or ""
        python = projectenv.interpreter_of(venv) if venv else None
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
    available = True if managed["exists"] else managed_available()
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
        }
    )
    out["managed"] = managed
    return out


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
    if target_kind not in TARGETS:
        raise RepairError(ERROR_NOT_ALLOWED, f"未知的安装目标: {target_kind!r}")
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
    if target_kind == TARGET_PROJECT_VENV:
        python = _pick_project_venv(root, script, module)
        health = projectenv.probe_environment(python, module)
        if health.get("code") == projectenv.ERROR_MODULE_MISSING:
            pass  # 正是我们要修的状态
        elif health.get("ok"):
            raise RepairError(ERROR_NOT_ALLOWED, f"这个环境里已经有 {module} 了")
        else:
            raise RepairError(
                health.get("code") or ERROR_NOT_ALLOWED, "这个环境不适合作为安装目标", health=health
            )
    else:
        python = managedenv.python_of(root) or ""
        creates = not python
        if creates and not base_python():
            raise RepairError(ERROR_MANAGED_UNAVAILABLE, "这台机器上没有可以用来创建环境的 Python")

    key = _env_key(target_kind, python, root)
    with _lock:
        if (managedenv.project_fingerprint(root), key, requirement.requirement()) in _attempted:
            raise RepairError(ERROR_NOT_ALLOWED, "同一个环境上的同一个需求这一轮已经试过了")
    now = time.time()
    plan = RepairPlan(
        plan_id=secrets.token_urlsafe(24),
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
    )
    _prune_plans()
    with _lock:
        _plans[plan.plan_id] = plan
    LOG.info("依赖修复计划: %s → %s（%s）", plan.requirement.requirement(), target_kind, script)
    return plan


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
    """请求取消。真正的处置在安装线程里（见 `_finish_cancelled`）。"""
    with _lock:
        ev = _cancels.get(str(plan_id or ""))
    if ev is None:
        return False
    ev.set()
    return True


def install_async(plan_id: str, on_event=None) -> None:
    threading.Thread(
        target=lambda: _install_guarded(plan_id, on_event), daemon=True, name="tavotto-dep-install"
    ).start()


def _install_guarded(plan_id: str, on_event) -> dict:
    try:
        return install(plan_id, on_event)
    except RepairError as exc:
        return _emit(plan_id, STATE_FAILED, on_event, code=exc.code, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        LOG.exception("依赖安装线程异常")
        return _emit(plan_id, STATE_FAILED, on_event, code=ERROR_FAILED, error=str(exc))


def install(plan_id: str, on_event=None) -> dict:
    """执行一个计划。**这是唯一一处会往磁盘上装包的代码。**

    执行端只认 `plan_id`：装什么、装到哪、哪个项目，全部来自计划本身
    （防 TOCTOU，ADR 0019 §计划绑定）。计划不存在 / 过期 / 环境在确认期间
    变过，一律拒绝。
    """
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

    cancel_ev = threading.Event()
    with _lock:
        _cancels[plan.plan_id] = cancel_ev
    key = _env_key(plan.target_kind, plan.python, plan.project)
    try:
        with pool.mutating_environment(key, plan.python):
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
    if plan.target_kind == TARGET_MANAGED and not python:
        _emit(plan.plan_id, STATE_CREATING_ENV, on_event, plan=plan)
        python = _create_managed(project, cancel_ev)
        # 环境刚建出来，把解释器路径也纳入同一次改动：接下来的 pip 才是
        # 真正在写它的 site-packages，那段窗口里同样不许起 worker。
        pool.note_mutating_python(env_key, python)
    if cancel_ev.is_set():
        return _finish_cancelled(plan, on_event, python)

    # ---- pip 在不在 -------------------------------------------------------
    rc, out = _run([python, "-m", "pip", "--version"], PIP_PROBE_TIMEOUT_S)
    if rc != 0:
        # **不静默 ensurepip**：那是往用户环境里再加一样东西，而用户确认的是
        # 「装 lmfit」。受管环境是我们自己建的，`python -m venv` 已经带了 pip；
        # 走到这里说明它坏了。
        raise RepairError(
            ERROR_PIP_UNAVAILABLE
            if plan.target_kind == TARGET_PROJECT_VENV
            else ERROR_MANAGED_BROKEN,
            _sanitize(out)[-800:],
        )

    # ---- 安装 -------------------------------------------------------------
    _emit(plan.plan_id, STATE_INSTALLING, on_event, plan=plan)
    with _lock:
        _attempted.add(
            (plan.project_id, _env_key(plan.target_kind, python, project), req.requirement())
        )
    code, out = _pip_install(
        python, req.requirement(), cancel_ev, lambda text: _append_log(plan.plan_id, text, on_event)
    )
    if code == ERROR_CANCELLED:
        return _finish_cancelled(plan, on_event, python)
    if code:
        raise RepairError(code, _sanitize(out)[-800:])

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
    version = installed_version(python, req.distribution)
    if plan.target_kind == TARGET_MANAGED:
        managedenv.record_install(
            project,
            import_name=req.import_name,
            distribution=req.distribution,
            requested_specifier=req.specifier,
            resolved_version=version,
            reason=managedenv.REASON_MISSING_DEPENDENCY,
        )
        managedenv.mark_ready(project)
    projectenv.remember(
        project,
        python,
        automatic=False,
        trigger="dependency_repair",
        module=req.import_name,
        health=health,
    )
    pool.note_project_python_ok(python)
    # 安装期间这个环境上的 worker 已经被停掉了（`mutating_environment`）。
    # 这里再点名作废一次：脚本自己的会话必须重建，import 系统 / sys.modules /
    # 已加载的动态库都不会因为磁盘上多了个包而刷新。
    pool.invalidate(script, project)
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
    LOG.info("依赖修复成功: %s %s → %s", req.distribution, version, plan.target_kind)
    return result


def _create_managed(project: str, cancel_ev: threading.Event) -> str:
    """建一个受管环境并装上 worker 真正需要的那点东西。"""
    base = base_python()
    if not base:
        raise RepairError(ERROR_MANAGED_UNAVAILABLE, "这台机器上没有可以用来创建环境的 Python")
    managedenv.write_manifest(project, managedenv.new_manifest(project, base))
    ok, out = managedenv.create_venv(project, base)
    if not ok:
        managedenv.mark_incomplete(project, "venv 创建失败")
        raise RepairError(ERROR_MANAGED_CREATE_FAILED, _sanitize(out)[-800:])
    python = str(managedenv.venv_python(project))
    for package in managedenv.BASE_PACKAGES:
        if cancel_ev.is_set():
            raise RepairError(ERROR_CANCELLED, "已取消")
        code, out = _pip_install(python, package, cancel_ev, None)
        if code:
            managedenv.mark_incomplete(project, f"{package} 安装失败")
            raise RepairError(
                ERROR_MANAGED_CREATE_FAILED if code == ERROR_FAILED else code, _sanitize(out)[-800:]
            )
    managedenv.update_manifest(project, python_version=managedenv.python_version_of(python))
    managedenv.mark_ready(project)
    return python


#: 重建受管环境时进度用的固定 id（它没有 plan——重建不装新东西，只是把
#: 我们自己记过的那些装回去，用户点的就是「重建」本身）。
REBUILD_PROGRESS_ID = "managed-rebuild"


def rebuild_managed_async(project: str | Path, on_event=None) -> None:
    threading.Thread(
        target=lambda: _rebuild_guarded(project, on_event),
        daemon=True,
        name="tavotto-managed-rebuild",
    ).start()


def _rebuild_guarded(project, on_event) -> dict:
    try:
        return rebuild_managed(project, on_event)
    except RepairError as exc:
        return _emit(REBUILD_PROGRESS_ID, STATE_FAILED, on_event, code=exc.code, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        LOG.exception("受管环境重建异常")
        return _emit(
            REBUILD_PROGRESS_ID,
            STATE_FAILED,
            on_event,
            code=ERROR_MANAGED_CREATE_FAILED,
            error=str(exc),
        )


def rebuild_managed(project: str | Path, on_event=None) -> dict:
    """删掉重建受管环境，并把我们记过的那些装回去。

    **删除、读账、重建必须在同一把环境锁之内**（Codex 评审 P1）。曾经是
    端点先 `is_mutating()` 查一下、然后在锁外把 venv 删掉、再异步去重建：
    那个窗口里一个已经形成的 plan 完全可以开始往这个解释器里 pip install，
    而它的 venv 正在被删。更糟的是两边**根本不互斥**——install 拿的是
    解释器路径 key，而重建当时拿的是 `tavotto_managed:<项目指纹>` 这个
    合成 key（`_env_key` 在 python 为空时的分支）。

    所以这里传**当前就存在的**解释器路径进锁：`mutating_environment` 会把
    路径 key 与合成 key 一起登记，install 那条路才真的被挡在外面。
    读 `installed_requirements()` 也搬进来了——在锁外读，读到的可能是另一次
    安装刚写进去的账。

    **不声称 lockfile 级复现**：`environment.json` 里记的是安装当时解析出来的
    版本，重建时那个版本可能已经从 index 上撤了。真撤了就如实报错，不悄悄
    换一个别的版本装上——「重建完跟以前不一样」比「重建失败」难查得多。
    """
    root = str(Path(project))
    key = _env_key(TARGET_MANAGED, "", root)
    # 锁要盖住**现在这个**解释器：环境还在时它就是 install 会用的那条路径。
    existing = managedenv.python_of(root) or str(managedenv.venv_python(root))
    cancel_ev = threading.Event()
    with _lock:
        _cancels[REBUILD_PROGRESS_ID] = cancel_ev
    try:
        with pool.mutating_environment(key, existing):
            # ---- 拆旧：全部在锁内 ----
            _emit(REBUILD_PROGRESS_ID, STATE_PREPARING, on_event)
            requirements = managedenv.installed_requirements(root)
            if pool.same_python(projectenv.remembered(root), existing):
                # 记住的解释器正是它：先撤决策再删，否则删完那一瞬间
                # `resolve_worker_python()` 会指向一条已经不存在的路径。
                projectenv.forget(root)
            managedenv.remove(root)
            pool.reset_worker_python()
            # ---- 重建 ----
            _emit(REBUILD_PROGRESS_ID, STATE_CREATING_ENV, on_event)
            python = _create_managed(root, cancel_ev)
            pool.note_mutating_python(key, python)
            restored: list[str] = []
            for req in requirements:
                if cancel_ev.is_set():
                    managedenv.mark_incomplete(root, "重建被取消")
                    return _emit(
                        REBUILD_PROGRESS_ID, STATE_CANCELLED, on_event, code=ERROR_CANCELLED
                    )
                _emit(REBUILD_PROGRESS_ID, STATE_INSTALLING, on_event)
                code, out = _pip_install(
                    python,
                    req,
                    cancel_ev,
                    lambda text: _append_log(REBUILD_PROGRESS_ID, text, on_event),
                )
                if code:
                    managedenv.mark_incomplete(root, f"{req} 装不回去")
                    raise RepairError(code, _sanitize(out)[-800:])
                restored.append(req)
            _emit(REBUILD_PROGRESS_ID, STATE_VERIFYING, on_event)
            selftest = worker_self_test(python)
            if not selftest.get("ok"):
                managedenv.mark_incomplete(root, "worker 自检未通过")
                raise RepairError(
                    ERROR_MANAGED_BROKEN, _sanitize(selftest.get("detail", ""))[-800:]
                )
            managedenv.mark_ready(root)
            result = {"ok": True, "python": python, "restored": restored}
            _emit(REBUILD_PROGRESS_ID, STATE_DONE, on_event, result=result)
            return result
    except pool.EnvironmentBusy as exc:
        raise _busy_error(exc) from exc
    finally:
        with _lock:
            _cancels.pop(REBUILD_PROGRESS_ID, None)


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
    if any(m in low for m in _CONFLICT_MARKERS):
        return ERROR_CONFLICT
    if "could not find a version" in low or "no matching distribution" in low:
        return ERROR_NOT_FOUND if "from versions: none" in low else ERROR_REQUIRES_BUILD
    return ERROR_FAILED


def pip_install_argv(python: str, requirement: str, *, upgrade: bool = False) -> list[str]:
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
    python: str, requirement: str, cancel_ev: threading.Event, on_log, *, upgrade: bool = False
) -> tuple[str, str]:
    """跑一次 pip install。回 `("", 输出)` 表示成功，否则 `(错误码, 输出)`。"""
    if depresolve.parse_requirement(requirement) is None:
        # 第二道门：真正拼进 argv 之前再验一次形状。第一道在解析处，
        # 这一道挡的是「以后有人从别的路径构造出需求串」。
        return ERROR_REQUIREMENT_INVALID, f"需求串不合形状: {requirement!r}"
    LOG.info("pip install: %s%s", requirement, " (upgrade)" if upgrade else "")
    return _run_pip(pip_install_argv(python, requirement, upgrade=upgrade), cancel_ev, on_log)


def _pip_uninstall(
    python: str, distribution: str, cancel_ev: threading.Event, on_log
) -> tuple[str, str]:
    """跑一次 pip uninstall。包名同样要过语法关——它一样会进 argv。"""
    parsed = depresolve.parse_requirement(distribution)
    if parsed is None or parsed[1]:
        return ERROR_REQUIREMENT_INVALID, f"包名不合形状: {distribution!r}"
    LOG.info("pip uninstall: %s", distribution)
    return _run_pip(pip_uninstall_argv(python, distribution), cancel_ev, on_log)


def _run_pip(argv: list[str], cancel_ev: threading.Event, on_log) -> tuple[str, str]:
    """流式跑一条 pip 命令（install / uninstall 共用的唯一执行器）。

    可取消、有超时、日志逐行回调。**argv 由调用方的两个 `*_argv()` 出处拼好**，
    这里不再碰它的形状。
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return ERROR_FAILED, str(exc)

    chunks: list[str] = []
    deadline = time.time() + INSTALL_TIMEOUT_S

    def _pump() -> None:
        for line in proc.stdout or ():
            chunks.append(line)
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
        if time.time() > deadline:
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
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
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
        argv = execspec.worker_argv(spec, worker_py=pool.WORKER_PY, out_dir=out_dir)
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    也会泄漏用户所在机构。问不出来回 None。
    """
    for name in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"):
        if os.environ.get(name):
            return True
    rc, out = _run([str(python), "-m", "pip", "config", "list"], 30)
    if rc != 0:
        return None
    return any(k in out for k in ("index-url", "extra-index-url"))


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


def _emit(
    plan_id: str,
    state: str,
    on_event,
    *,
    plan: RepairPlan | None = None,
    code: str = "",
    error: str | None = None,
    result: dict | None = None,
) -> dict:
    with _lock:
        rec = dict(_progress.get(plan_id) or {"log": ""})
        rec.update(
            plan_id=plan_id, state=state, code=code, error=error, result=result or rec.get("result")
        )
        if plan is not None:
            rec.update(
                import_name=plan.requirement.import_name,
                distribution=plan.requirement.distribution,
                target_kind=plan.target_kind,
                script=plan.script,
            )
        _progress[plan_id] = rec
        snapshot = dict(rec)
    if on_event is not None:
        on_event(snapshot)
    return snapshot


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

    if python:
        busy = envlease.is_mutating(python)
    else:
        busy = envlease.is_mutating_key(_env_key(TARGET_MANAGED, "", root))
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
        try:
            target_dir = managedenv.env_dir(root)
            probe_dir = target_dir if target_dir.exists() else target_dir.parent
            probe_dir.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(str(probe_dir)).free
        except OSError:
            free = None
        if free is not None and free < MIN_FREE_BYTES:
            raise RepairError(ERROR_PACKAGE_DISK_LOW, "磁盘剩余空间不足")

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
    LOG.info("包操作作业: %s %s", op, requirement)
    return job


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
        _emit_job(job.job_id, STATE_CREATING_ENV, on_event, job=job)
        python = _create_managed(project, cancel_ev)
        pool.note_mutating_python(env_key, python)
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
            python, job.requirement, cancel_ev, log, upgrade=job.op == OP_UPDATE
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
    LOG.info("包操作完成: %s %s %s", job.op, job.distribution, version)
    return result


def _freeze(python: str) -> str:
    """`pip freeze` 的（已脱敏）文本；问不出来回空串。"""
    rc, out = _run(
        [str(python), "-m", "pip", "freeze", "--disable-pip-version-check"], FREEZE_TIMEOUT_S
    )
    return _sanitize(out) if rc == 0 else ""


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
