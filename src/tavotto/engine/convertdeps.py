"""「生成可编辑版本」新脚本缺 Python 包时的**依赖决定**（ADR 0143 修订「R / MATLAB 来源」，维护者 2026-10-23 两次裁决）。

R / MATLAB 转写出的新脚本要 pandas、statsmodels 这类包，用户的环境里不一定有。这里**不新写任何 pip 调用路径**：
缺什么由 `deprepair.joint_plan_for`（= `importscan` 静态扫 + `depplan` 对照目标解释器）算，装由
`deprepair.create_joint_plan` + `deprepair.prepare`（联合准备的现有入口：envlease、环境代事务、装后核验、记账、账本、取消、
worker 退役全在里面）。本模块只回答两件事：**这一次能不能不问用户直接走那条现有入口**，不能时**问什么**。

## 名单（裁决 1）

只装「在名单里」的发行包：`autoinstall_dists.txt`（PEP 503 规范名，一行一个，打包随 `packaging/tavotto.spec` 的 datas 带上；
`scripts/dev/refresh_autoinstall_list.py` 重新生成，产品运行时从不联网取名单）。名单 = 14 个科学计算种子 ∪ PyPI 30 天下载量
前 1000 的项目名 − 排除名单（pip / setuptools 等管理环境与打包工具）。Agent 写进脚本的任意 import 名不会因此变成对用户环境的任意 pip 安装；名单外的包只会先出卡片点名。

## 每个环境问一次（裁决 2）

目标环境按 `pool.resolve_worker_python` 此刻会跑脚本的那个解释器分类（`describe_env`）：

* `project_venv`（项目自己的 `.venv`）/ `managed`（Tavotto 受管环境的现有一代，且计划不新建环境、不下载私有 Python）：
  名单内的包**直接装**（保持上一轮的口径；摘要由服务端同次调用回显，见下）；
* `system`（OS 自带 / `EXTERNALLY-MANAGED`）与 `builtin`（内置 runtime / Tavotto 自建 worker-env / 本进程解释器）：**永不装**，
  卡片 `system`：请用户给项目换环境（主按钮打开现有的「选择运行环境」）；
* 其余（Conda、设置里指定的、环境变量固定的、项目记录里用户采用的……）= `other`：**第一次**需要装时出卡片 `ask_once`
  （“要给这个环境装缺的包吗？”），用户点主按钮才记下**对这个环境**的常驻同意（`ai.convert.auto_install_envs`，键 = 解释器路径的
  sha256 前 16 位，不存明文路径；值只有“种类 + 名字”供设置页列出与撤销）；之后同一环境再缺名单内的包就直接装。
  **现有修复链对这类环境不支持原地 pip**（`deprepair.joint_target_for`：非项目 venv 的一律是受管环境目标），所以“装”= 现有链条对
  它的标准动作：装进 Tavotto 为本项目建的受管环境，并让项目以后用它跑（不改动用户的那个环境；文案如实写明）。
* 名单外的包（任何环境）：不自动装，卡片 `uncommon`，点名“这个包不常见，确认是你要的再装”；点主按钮只装这一次，不记常驻同意。

## 授权口径（需要维护者知悉，ADR 0143 修订节同文）

`deprepair._check_confirmed` 要求调用方回显“给用户看过的摘要”，并写明服务端自己持有的摘要不得代替回显。两种口径：

1. **问过一次 / 点过卡片**：确认来自用户这次真实点击——前端把卡片上带着的 `impact_digest` 原样回传（`resolve`），服务端重算计划，
   摘要对不上（环境或包集合在期间变了）就**不装**、回新的卡片（`dependency_impact_changed`），绝不自回显；
2. **已记过常驻同意的环境（含项目 `.venv` / 受管环境这两类上一轮就直接装的目标）**：由服务端回显**同一次调用里刚算出的**计划摘要，
   依据是维护者 2026-10-23 的常驻同意裁决（可用 `ai.convert.auto_install == false` / `TAVOTTO_NO_CONVERT_AUTO_INSTALL=1` 收回，
   也可在设置里撤销单个环境）。摘要对不上 / 环境在期间变了，`prepare` 自己的 `dependency_impact_changed` / `repair_plan_stale` 照样拦。

## 取消（Codex #1330 P1）

`ensure` / `resolve` 的 `cancel`（`threading.Event`）会传进安装：先把 deprepair 的取消句柄登记在这份计划上（`prepare` 复用同一个），
再由监视线程在 `cancel` 到来时调 `deprepair.cancel_status`（= 现有的计划取消入口）。取消到来时安装在 `prepare` 的取消检查点停手
（拿锁前什么都不写；事务里按它自己的回滚 / 提交点语义：过了提交点的取消被 `cancel_status` 拒绝，环境已经换代，结果如实回 `installed`）；
结局 `cancelled`，之后不再有任何写环境的调用。

纯标准库（Flask 父进程 import 链；`deprepair` / `pool` 在函数内 import，让本模块的常量与开关不拖入它们的整条依赖）。
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
import threading
from pathlib import Path

from . import config, depresolve

LOG = logging.getLogger("tavotto.convertdeps")

#: 名单文件（随包；`scripts/dev/refresh_autoinstall_list.py` 生成）
ALLOWLIST_PATH = Path(__file__).with_name("autoinstall_dists.txt")
#: 名单文件读不到时的兜底（= 上一轮的 14 个科学计算包）。只会让名单更小，从不更大
SEED_DISTRIBUTIONS = frozenset(
    {
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "patsy",
        "seaborn",
        "matplotlib",
        "openpyxl",
        "xlrd",
        "pyarrow",
        "scikit-learn",
        "lifelines",
        "pingouin",
        "adjusttext",
    }
)

STATUS_NONE = "none"  # 没有缺的
STATUS_INSTALLED = "installed"  # 装好了
STATUS_HOLD = "hold"  # 要用户先决定：结果里带 `card`，候选不删，决定后接着走同一个候选
STATUS_SKIPPED = "skipped"  # 没有可做的动作（开关关了 / 计划不可执行）：校验运行照常跑，缺包由 `missing_dependency` 如实失败
STATUS_FAILED = "failed"  # 想装、装失败了
STATUS_CANCELLED = "cancelled"  # 安装进行中被取消

#: `skipped` 的原因闭集（只进结果与测试，不进日志）
SKIP_REASONS = ("disabled", "plan_unavailable", "plan_blocked", "plan_refused")

#: 卡片种类闭集（前端按它分支，文案中英两份）
CARD_ASK_ONCE = "ask_once"  # 这个环境第一次需要装：问一次，主按钮 = 装且以后都自动装
CARD_UNCOMMON = "uncommon"  # 名单外的包：点名，确认是你要的再装（只装这一次）
CARD_SYSTEM = "system"  # 系统自带 / 内置 runtime：不装，请给项目换环境
CARD_KINDS = (CARD_ASK_ONCE, CARD_UNCOMMON, CARD_SYSTEM)

#: 环境种类闭集
KIND_PROJECT_VENV = "project_venv"
KIND_MANAGED = "managed"
KIND_BUILTIN = "builtin"
KIND_SYSTEM = "system"
KIND_CONDA = "conda"
KIND_OTHER = "other"
#: 不问就能装（名单内）的两类：现有链条的原地 / 受管一代目标
DIRECT_KINDS = (KIND_PROJECT_VENV, KIND_MANAGED)

#: `resolve` 里用户的决定
DECISION_INSTALL = "install"

#: 卡片动作（`card["action"]`）：用户点主按钮时实际执行的标准动作
ACTION_INSTALL = "install"  # 往目标原地 / 受管现有一代装
ACTION_INSTALL_MANAGED = (
    "install_managed"  # 现有链条对这类环境的标准动作：装进受管环境并让项目改用它
)
ACTION_CHOOSE_ENV = "choose_env"  # 不装：打开「选择运行环境」

#: 设置里常驻同意的存放位置：`ai.convert.auto_install_envs = {环境键: {"kind": …, "name": …}}`
CONSENT_KEY = "auto_install_envs"

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_VENV_DIR_NAMES = frozenset({".venv", "venv", "env", ".env"})
#: OS 自带解释器所在的目录前缀（不跟随符号链接：venv 里的 python 是指向它们的链接，但 venv 自己不是系统 Python）
_OS_PREFIXES_POSIX = (
    "/usr/bin/",
    "/bin/",
    "/system/",
    "/library/developer/commandlinetools/",
    "/applications/xcode.app/",
)


# --------------------------------------------------------------------------- 开关与名单
def enabled() -> bool:
    """自动安装开关：默认开（用户 2026-10-23 裁决），`ai.convert.auto_install == false` 或 `TAVOTTO_NO_CONVERT_AUTO_INSTALL=1` 关。"""
    if os.environ.get("TAVOTTO_NO_CONVERT_AUTO_INSTALL") == "1":
        return False
    conv = config.ai_settings().get("convert")
    return not (isinstance(conv, dict) and conv.get("auto_install") is False)


@functools.lru_cache(maxsize=1)
def allowlist() -> frozenset[str]:
    """名单（PEP 503 规范名）。读不到文件 = 退回 14 个种子并记一条日志（宁可更小，不更大）。"""
    try:
        text = ALLOWLIST_PATH.read_text(encoding="utf-8")
    except OSError:
        LOG.warning("自动安装名单文件读不到，退回内置的 14 个科学计算包")
        return SEED_DISTRIBUTIONS
    names = set()
    for line in text.splitlines():
        item = line.split("#", 1)[0].strip()
        if item:
            names.add(depresolve.normalize_distribution(item))
    return frozenset(names) | SEED_DISTRIBUTIONS


def in_allowlist(name: str) -> bool:
    """发行包名（任何大小写 / `_` `.` `-` 写法）在不在名单里。"""
    return depresolve.normalize_distribution(name) in allowlist()


def _dist_of(requirement: str) -> str:
    m = _REQ_NAME.match(requirement or "")
    return depresolve.normalize_distribution(m.group(1)) if m else ""


# --------------------------------------------------------------------------- 环境分类
def env_key(python: str) -> str:
    """环境的稳定标识：解释器路径（规范化后）的 sha256 前 16 位。常驻同意只存它，不存明文路径。"""
    norm = os.path.normcase(os.path.abspath(str(python or "")))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _prefix_of(python: str) -> Path:
    p = Path(os.path.abspath(python))
    # POSIX：<prefix>/bin/python；Windows：<prefix>/python.exe 或 <prefix>/Scripts/python.exe
    parent = p.parent
    if parent.name.lower() in ("bin", "scripts"):
        return parent.parent
    return parent


def _is_os_python(python: str) -> bool:
    """OS 自带解释器（路径前缀）或带 `EXTERNALLY-MANAGED` 标记的解释器（PEP 668：Homebrew、Debian 系的 python3 都有）。
    只看路径与标记文件，不起子进程；不跟随符号链接（venv 里指向系统 Python 的链接不让 venv 变成系统 Python）。"""
    p = os.path.abspath(str(python or ""))
    low = p.replace("\\", "/").lower()
    if os.name == "nt":
        root = (
            (os.environ.get("SystemRoot") or r"C:\Windows").replace("\\", "/").lower().rstrip("/")
        )
        if low.startswith(root + "/"):
            return True
    elif low.startswith(_OS_PREFIXES_POSIX):
        return True
    prefix = _prefix_of(p)
    try:
        for pattern in ("lib/python3*/EXTERNALLY-MANAGED", "Lib/EXTERNALLY-MANAGED"):
            if any(prefix.glob(pattern)):
                return True
    except OSError:
        pass
    return False


def _env_name(python: str) -> str:
    prefix = _prefix_of(python)
    if prefix.name.lower() in _VENV_DIR_NAMES and prefix.parent.name:
        return f"{prefix.parent.name}/{prefix.name}"
    return prefix.name or "python"


def describe_env(python: str, source: str) -> dict:
    """此刻会跑脚本的解释器 → `{"kind": 环境种类闭集, "name": 可读名, "key": 稳定标识}`。
    可读名不含机器路径（只有环境目录名）；Conda 的名字沿用 `userenvs` 里同一个取法。"""
    from . import pool, runtime, userenvs

    name = _env_name(python)
    if source in (pool.SOURCE_PROJECT_VENV,):
        kind = KIND_PROJECT_VENV
    elif source == pool.SOURCE_MANAGED_PROJECT:
        kind = KIND_MANAGED
    elif source in (pool.SOURCE_BUNDLED, pool.SOURCE_CURRENT, pool.SOURCE_MANAGED) or (
        python and pool.same_python(python, runtime.bundled_python())
    ):
        kind = KIND_BUILTIN
    elif _is_os_python(python):
        kind = KIND_SYSTEM
    elif (_prefix_of(python) / "conda-meta").is_dir():
        kind = KIND_CONDA
        name = userenvs._conda_label(str(_prefix_of(python)))
    else:
        kind = KIND_OTHER
    return {"kind": kind, "name": name, "key": env_key(python)}


# --------------------------------------------------------------------------- 常驻同意
def consented_envs() -> list[dict]:
    """已允许自动装包的环境（设置页列出 / 撤销用）：`[{"key", "kind", "name"}]`，按名字排序。"""
    conv = config.ai_settings().get("convert")
    raw = conv.get(CONSENT_KEY) if isinstance(conv, dict) else None
    out = []
    for key, rec in (raw if isinstance(raw, dict) else {}).items():
        if isinstance(rec, dict) and isinstance(key, str):
            out.append(
                {"key": key, "kind": str(rec.get("kind") or ""), "name": str(rec.get("name") or "")}
            )
    return sorted(out, key=lambda r: (r["name"], r["key"]))


def has_consent(key: str) -> bool:
    return any(r["key"] == key for r in consented_envs())


def _write_consents(records: dict) -> None:
    conv = config.ai_settings().get("convert")
    merged = dict(conv) if isinstance(conv, dict) else {}
    if records:
        merged[CONSENT_KEY] = records
    else:
        merged.pop(CONSENT_KEY, None)
    config.set_ai_settings({"convert": merged})


def grant(env: dict) -> None:
    """对**这个环境**记下常驻同意（只存键 + 种类 + 名字）。"""
    records = {r["key"]: {"kind": r["kind"], "name": r["name"]} for r in consented_envs()}
    records[env["key"]] = {"kind": env["kind"], "name": env["name"]}
    _write_consents(records)


def revoke(key: str) -> bool:
    """撤销一个环境的常驻同意；回 True = 确有这一条。"""
    records = {r["key"]: {"kind": r["kind"], "name": r["name"]} for r in consented_envs()}
    if key not in records:
        return False
    del records[key]
    _write_consents(records)
    return True


# --------------------------------------------------------------------------- 结果形状
def _result(status: str, **extra) -> dict:
    return {"status": status, "installed": [], "missing": [], "reason": "", "code": "", **extra}


def _public_env(env: dict) -> dict:
    return {"kind": env["kind"], "name": env["name"]}


def _card(
    kind: str, *, env: dict, names: list[str], outside: list[str], action: str, plan=None
) -> dict:
    """交给前端的卡片事实（结构化字段；不含机器路径、不含 plan_id）。`impact_digest` 是用户点主按钮时要原样回传的摘要。"""
    impact = getattr(plan, "impact", None) or {}
    return {
        "kind": kind,
        "packages": list(names),
        "outside_list": list(outside),
        "env": _public_env(env),
        "action": action,
        "impact_digest": getattr(plan, "impact_digest", "") or "",
        "target": str(impact.get("target_kind") or ""),
        "creates_environment": bool(impact.get("creates_environment")),
        "downloads_python": bool(impact.get("private_python")),
        "modifies_user_environment": bool(impact.get("modifies_user_environment")),
    }


# --------------------------------------------------------------------------- 取消感知的安装
def _run_prepare(plan, cancel: threading.Event | None, confirmed_impact: str) -> dict:
    """走现有 `deprepair.prepare`；`cancel` 到来时经现有的 `deprepair.cancel_status` 取消这份计划。回 `{"state", "code"}`。

    取消句柄先于 `prepare` 登记（`prepare` 复用同一个）：取消在 `prepare` 开跑前就到了，也会在它的取消检查点停手、一个字节不写。
    """
    from . import deprepair

    pid = plan.plan_id
    deprepair._register_cancel(pid)
    done = threading.Event()
    watcher: threading.Thread | None = None
    if cancel is not None:

        def watch() -> None:
            while not done.is_set():
                if cancel.wait(0.05):
                    deprepair.cancel_status(pid)
                    return

        watcher = threading.Thread(target=watch, name="convertdeps-cancel", daemon=True)
        watcher.start()
    try:
        if cancel is not None and cancel.is_set():
            deprepair.cancel_status(pid)  # 开跑前就取消了：prepare 的取消检查点会在拿锁前停手
        out = deprepair.prepare(pid, None, confirmed_impact=confirmed_impact)
        return {
            "state": str((out or {}).get("state") or ""),
            "code": str((out or {}).get("code") or ""),
        }
    except deprepair.RepairError as exc:
        return {"state": deprepair.STATE_FAILED, "code": exc.code}
    except Exception:  # noqa: BLE001
        return {"state": deprepair.STATE_FAILED, "code": deprepair.ERROR_FAILED}
    finally:
        done.set()
        if watcher is not None:
            watcher.join(timeout=2)
        with deprepair._lock:
            deprepair._cancels.pop(
                pid, None
            )  # prepare 在拿到句柄之前就抛了（摘要对不上等）时不留着


def _install(plan, names: list[str], cancel: threading.Event | None, confirmed_impact: str) -> dict:
    from . import deprepair

    out = _run_prepare(plan, cancel, confirmed_impact)
    if out["state"] == deprepair.STATE_DONE:
        return _result(STATUS_INSTALLED, installed=names)
    if out["state"] == deprepair.STATE_CANCELLED or out["code"] == deprepair.ERROR_CANCELLED:
        return _result(STATUS_CANCELLED, missing=names, code=deprepair.ERROR_CANCELLED)
    if cancel is not None and cancel.is_set() and out["state"] != deprepair.STATE_DONE:
        return _result(STATUS_CANCELLED, missing=names, code=deprepair.ERROR_CANCELLED)
    return _result(STATUS_FAILED, missing=names, code=out["code"] or deprepair.ERROR_FAILED)


# --------------------------------------------------------------------------- 决定
def _survey(project: str, stage_rel: str) -> dict:
    """只读：缺什么、此刻环境是什么类。回
    `{"verdict": none|skipped|ready, "reason", "names", "env", "target_kind"}`；`ready` 才有 names / env。"""
    from . import depplan, deprepair, pool

    try:
        joint, _kind, _python = deprepair.joint_plan_for(project, stage_rel)
    except Exception:  # noqa: BLE001 —— 解释器解析不出 / 事实取不到：不是这里的事，校验运行会给出它自己的错
        return {"verdict": STATUS_SKIPPED, "reason": "plan_unavailable"}
    if joint.status == depplan.STATUS_NOTHING_NEEDED:
        return {"verdict": STATUS_NONE}
    if (
        joint.status != depplan.STATUS_READY
        or joint.blocked
        or joint.unknown
        or getattr(joint, "not_pip_installable", ())
    ):
        return {"verdict": STATUS_SKIPPED, "reason": "plan_blocked"}
    names = sorted({d for d in (_dist_of(r) for r in joint.requirements) if d})
    if not names:
        return {"verdict": STATUS_SKIPPED, "reason": "plan_blocked"}
    try:
        python, source = pool.resolve_worker_python(project, script=stage_rel)
        target_kind, _py, _src = deprepair.joint_target_for(project, stage_rel)
    except Exception:  # noqa: BLE001
        return {"verdict": STATUS_SKIPPED, "reason": "plan_unavailable"}
    return {
        "verdict": "ready",
        "names": names,
        "env": describe_env(python, source),
        "target_kind": target_kind,
    }


def _make_plan(project: str, stage_rel: str, target_kind: str):
    """形成（不执行）联合计划；拒绝时回 `(None, 错误码)`。"""
    from . import deprepair

    try:
        return deprepair.create_joint_plan(project, stage_rel, target_kind=target_kind), ""
    except deprepair.RepairError as exc:
        return None, exc.code
    except Exception:  # noqa: BLE001
        return None, deprepair.ERROR_FAILED


def _plan_names(plan) -> list[str]:
    return sorted({d for d in (_dist_of(r) for r in plan.requirements) if d})


def _discard(plan) -> None:
    from . import deprepair

    deprepair._discard_plan(plan.plan_id)  # 没执行的计划不留着


def _decide(project: str, stage_rel: str, *, clicked: bool, confirmed_impact: str | None, cancel):
    """`ensure`（clicked=False）与 `resolve`（clicked=True）共用的决定树。"""
    sv = _survey(project, stage_rel)
    if sv["verdict"] == STATUS_NONE:
        return _result(STATUS_NONE)
    if sv["verdict"] == STATUS_SKIPPED:
        return _result(STATUS_SKIPPED, reason=sv["reason"])
    env, names = sv["env"], sv["names"]
    outside = [n for n in names if not in_allowlist(n)]
    kind = env["kind"]

    if kind in (KIND_SYSTEM, KIND_BUILTIN):
        # 系统自带 / 内置 runtime：永不往里装（点击也不行：这是边界，不是偏好）
        return _result(
            STATUS_HOLD,
            missing=names,
            reason=CARD_SYSTEM,
            card=_card(
                CARD_SYSTEM, env=env, names=names, outside=outside, action=ACTION_CHOOSE_ENV
            ),
        )

    direct = kind in DIRECT_KINDS
    target_kind = sv["target_kind"]
    plan, code = _make_plan(project, stage_rel, target_kind)
    if plan is None:
        return _result(STATUS_SKIPPED, reason="plan_refused", missing=names, code=code)
    plan_names = _plan_names(plan)
    plan_outside = [
        n for n in plan_names if not in_allowlist(n)
    ]  # 计划比静态扫多出的（声明文件里的包）也算
    action = ACTION_INSTALL if direct else ACTION_INSTALL_MANAGED
    creates = bool(plan.creates_environment)
    downloads = plan.private_python is not None

    if clicked:
        # 用户这次真实点击：回显的是**卡片上带着的**摘要；服务端不自回显
        if not confirmed_impact or plan.impact_digest != confirmed_impact:
            card_kind = CARD_UNCOMMON if plan_outside else CARD_ASK_ONCE
            card = _card(
                card_kind, env=env, names=plan_names, outside=plan_outside, action=action, plan=plan
            )
            _discard(plan)
            return _result(
                STATUS_HOLD,
                missing=plan_names,
                reason=card_kind,
                code="dependency_impact_changed",
                card=card,
            )
        if not direct and not plan_outside and not has_consent(env["key"]):
            grant(
                env
            )  # 点“装，以后这个环境都自动装”= 对这个环境记下常驻同意（名单外的包只装这一次，不记）
        return _install(plan, plan_names, cancel, confirmed_impact)

    # 自动路径（任务内的校验运行）：名单内 + 已授权的环境才装
    consented = direct or has_consent(env["key"])
    needs_user = (
        bool(plan_outside)
        or not consented
        or downloads  # 下载私有 Python 是大动作：永远要用户点一次
        or (direct and kind == KIND_MANAGED and creates)  # 受管目标要新建环境 = 无声换环境
    )
    if needs_user:
        card_kind = CARD_UNCOMMON if plan_outside else CARD_ASK_ONCE
        card = _card(
            card_kind, env=env, names=plan_names, outside=plan_outside, action=action, plan=plan
        )
        _discard(plan)
        return _result(STATUS_HOLD, missing=plan_names, reason=card_kind, card=card)
    return _install(
        plan, plan_names, cancel, plan.impact_digest
    )  # 已记过常驻同意：服务端回显同次调用算出的摘要


# --------------------------------------------------------------------------- 入口
def ensure(project: str, stage_rel: str, cancel: threading.Event | None = None) -> dict:
    """校验运行**之前**调：`stage_rel` 是已落盘的候选临时脚本（项目相对）。回
    `{"status": none|installed|hold|skipped|failed|cancelled, "installed", "missing", "reason", "code", "card"?}`。
    `hold` = 要用户先决定（带结构化 `card`）。不抛：任何意外都归成 `skipped`（让校验运行自己如实失败）。"""
    if not enabled():
        return _result(STATUS_SKIPPED, reason="disabled")
    try:
        return _decide(str(project), stage_rel, clicked=False, confirmed_impact=None, cancel=cancel)
    except Exception:  # noqa: BLE001
        LOG.warning("自动安装决定失败", exc_info=True)
        return _result(STATUS_SKIPPED, reason="plan_unavailable")


def resolve(
    project: str,
    stage_rel: str,
    *,
    confirmed_impact: str | None,
    cancel: threading.Event | None = None,
) -> dict:
    """用户在卡片上点了主按钮（`install`）：`confirmed_impact` 是卡片带着的摘要，由**前端回显**。
    重算计划；摘要对不上就回新的卡片（`code == dependency_impact_changed`）而不装。名单内、问过一次的 `ask_once`
    点击会对这个环境记下常驻同意。`system` 卡片没有“装”这个动作：回同一张卡。"""
    try:
        return _decide(
            str(project), stage_rel, clicked=True, confirmed_impact=confirmed_impact, cancel=cancel
        )
    except Exception:  # noqa: BLE001
        LOG.warning("自动安装（用户确认后）失败", exc_info=True)
        from . import deprepair

        return _result(STATUS_FAILED, code=deprepair.ERROR_FAILED)
