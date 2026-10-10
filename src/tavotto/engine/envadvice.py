"""环境「建议 → 检查 → 采用」（T05，ADR 0114）：三个动作、三种授权，各自只做自己的那一件。

| 动作 | 授权 | 会不会起候选解释器 | 写什么 |
|---|---|---|---|
| `recommend()` | 无（纯读，GET / 扫描 / 面板都能调） | **不会**：线索来自磁盘记录与已有的体检结论 | 无 |
| `check()` | 用户明确点了「检查」（带范围、预算、取消） | **会**，且只会起被点名范围里的候选 | 进程内体检结论缓存 |
| 采用 | 用户明确点了「使用」→ `PATCH /api/engine/environment`（`candidate` + `expected_generation`） | 现场再体检一次那个候选 | 项目设置里一条 `automatic=False` 的记录（带环境代） |

`check()` 可以带脚本所需的 `modules`（Import Origin PR3）：这时每个被检查的候选还会回答「这个脚本要的 import 它装齐了没有」
（覆盖度 `coverage`：`not_found` 与 `import_error` 分开）。覆盖度只有**一份**——写进 `userenvs._probe_cache`（路径 + 环境代 +
import 集合），依赖门 / 检测读的是同一个，不另建第二个缓存。**这样的检查会真 import 那些包**，也就是执行它们的 `__init__`：
结果里的 `executed` 如实标注，绝不宣称无副作用；`recommend()` 只读缓存里已有的覆盖度，一个进程都不起。

本模块**不是第二套 resolver**：解释器怎么选仍只有 `pool.resolve_worker_python`；这里回答的是「候选有哪些、
各自凭什么被推荐、检查过没有」。**使用不等于安装**：没有任何一行 pip；采用只写一条项目记录，内置 runtime
始终只读，缺包时的「装进哪里」仍归 `deprepair` / `managedenv`（由用户在它自己的确认里授权）。

推荐的排序只看**证据层次**，不看 Python 新旧、也不看谁装的包更多：

1. `selected`            —— 仍有效的显式选择（用户采用过、路径还在、环境代没变）；
   `auto_detected`       —— 检测模式（默认，ADR 0114 §六）替用户采用、此刻在用的那一个；
2. `remembered_legacy`   —— ADR 0114 之前机器替用户记下的：照用，但证明不了用户确认过（迁移：不重新询问）；
3. `project_hint`        —— 项目自己声明 / 编辑器指向的（项目 venv、`.vscode`、`.python-version`、`environment.yml`、shebang）；
4. `checked_compatible`  —— 用户的机器上、**已经被明确检查过**且健康的环境；
5. `machine_hint`        —— 终端 / Conda / pyenv 的落盘线索，**未检查**；
6. `bundled`             —— 内置 / 默认链条的基线（只读，永远可选回）。

纯标准库 + 兄弟模块；不 import app。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from . import pool, projectenv, projscan, runtime, userenvs

REC_VERSION = 1

LABEL_SELECTED = "selected"
#: 检测模式（ADR 0114 §六）替用户采用、此刻在用的那一个
LABEL_AUTO = "auto_detected"
LABEL_LEGACY = "remembered_legacy"
LABEL_PROJECT = "project_hint"
LABEL_CHECKED = "checked_compatible"
LABEL_MACHINE = "machine_hint"
LABEL_BUNDLED = "bundled"
#: 闭集，也是排序（小 = 更该被推荐）
LABELS = (
    LABEL_SELECTED,
    LABEL_AUTO,
    LABEL_LEGACY,
    LABEL_PROJECT,
    LABEL_CHECKED,
    LABEL_MACHINE,
    LABEL_BUNDLED,
)

STATUS_UNCHECKED = "unchecked"
STATUS_HEALTHY = "healthy"
STATUS_UNSUPPORTED = "unsupported_python"
STATUS_NO_MATPLOTLIB = "no_matplotlib"
STATUS_WORKER_IMPORT = "worker_import_failed"
STATUS_UNUSABLE = "unusable"
STATUS_MISSING = "missing"
STATUS_CHANGED = "changed"
STATUSES = (
    STATUS_UNCHECKED,
    STATUS_HEALTHY,
    STATUS_UNSUPPORTED,
    STATUS_NO_MATPLOTLIB,
    STATUS_WORKER_IMPORT,
    STATUS_UNUSABLE,
    STATUS_MISSING,
    STATUS_CHANGED,
)
#: 体检 code → 状态：不支持的 Python / 缺 matplotlib / worker 起不来（DLL、ABI）是三件事，出路各不相同
_STATUS_OF_CODE = {
    projectenv.ERROR_UNSUPPORTED_PYTHON: STATUS_UNSUPPORTED,
    projectenv.ERROR_NO_MATPLOTLIB: STATUS_NO_MATPLOTLIB,
    projectenv.ERROR_WORKER_IMPORT: STATUS_WORKER_IMPORT,
}
#: 已知不能用的状态：不被推荐；`unchecked` / `healthy` 之外都在里面
_BAD = frozenset(STATUSES) - {STATUS_UNCHECKED, STATUS_HEALTHY}

#: 内置 / 默认链条在候选表里的固定 id（不是路径、不是 `userenvs.env_id`）
BUILTIN_ID = "builtin"

#: 检查的预算：一次最多检查多少个候选、整次的总时限（每个候选另有 `projectenv.PROBE_TIMEOUT_S`）。
#: 候选是顺序检查的——取消 / 时限在两个候选之间生效，不会杀正在跑的那一个之外的东西。
CHECK_MAX_CANDIDATES = 6
CHECK_DEADLINE_S = 180.0
#: 剩余预算低于它就不再起新的探测：几秒钟连解释器冷启动 + import matplotlib 都不够，起了只会得到一个
#: 超时的「不可用」假结论。直接记 `deadline`（没检查 ≠ 检查过不行）。
MIN_PROBE_BUDGET_S = 5.0
SCOPE_PROJECT = "project"
SCOPE_MACHINE = "machine"
SCOPE_ALL = "all"
CHECK_SCOPES = (SCOPE_PROJECT, SCOPE_MACHINE, SCOPE_ALL)
#: 一次检查最多量多少个 import（每个是一次 `__import__`）。超出的丢掉并在结果里数出来，不静默截断
MAX_COVERAGE_MODULES = 64

# ---- 覆盖度（脚本要的 import 在某个候选环境里装齐了没有）：结构化 detail，**不是发布的错误码** ----
#: 单个模块的状态（`modules_detail` 的值，探测脚本量出来的）：`found` 找到且导入成功；`not_found` 被要求的名字找不到；
#: `import_error` 在、但导入时抛了别的；`deferred` 运行前的 `find_spec` 落在项目里（不 import，等运行再量）；`unknown` 量不出
MODULE_FOUND = "found"
MODULE_NOT_FOUND = "not_found"
MODULE_IMPORT_ERROR = "import_error"
MODULE_DEFERRED = "deferred"
MODULE_UNKNOWN = "unknown"
MODULE_STATES = (
    MODULE_FOUND,
    MODULE_NOT_FOUND,
    MODULE_IMPORT_ERROR,
    MODULE_DEFERRED,
    MODULE_UNKNOWN,
)
#: 整个环境对这组 import 的覆盖度
COV_COVERED = "covered"  # 全部找到
COV_PARTIAL = "partial"  # 一部分找到、一部分没有
COV_MISSING = "missing"  # 一个都没找到
COV_UNUSABLE = "unusable"  # 环境本身跑不了 Tavotto（Python 版本不支持 / 没 matplotlib / worker 起不来）：与「缺包」分开
COV_NOT_CHECKED = "not_checked"  # 没量过 / 量过但换代了 / 延后到运行：不冒充装齐，也不冒充没装齐
COVERAGE_STATES = (COV_COVERED, COV_PARTIAL, COV_MISSING, COV_UNUSABLE, COV_NOT_CHECKED)
#: 结构化 detail 的 code（闭集；`web/src/lib/api.ts` 镜像）。前两个是**用户的**候选环境，第三个是正要跑脚本的那个
#: （目标环境）；`*_import_failed_*` 是「包在、导入失败」——装它救不了
DETAIL_NOT_FOUND_USER = "module_not_found_in_user_environment"
DETAIL_IMPORT_FAILED_USER = "module_import_failed_in_user_environment"
DETAIL_MISSING_TARGET = "module_missing_in_target_environment"
DETAIL_IMPORT_FAILED_TARGET = "module_import_failed_in_target_environment"
DETAIL_NOT_CHECKED = "environment_not_checked"
DETAIL_UNUSABLE = "environment_unusable"
DETAIL_CODES = (
    DETAIL_NOT_FOUND_USER,
    DETAIL_IMPORT_FAILED_USER,
    DETAIL_MISSING_TARGET,
    DETAIL_IMPORT_FAILED_TARGET,
    DETAIL_NOT_CHECKED,
    DETAIL_UNUSABLE,
)
ROLE_USER = "user"
ROLE_TARGET = "target"

_lock = threading.Lock()
#: 检查留下的结论：(解释器路径键, 环境代) → 体检摘要。键里带环境代：同一路径被重建，旧结论自然对不上。
_verdicts: dict[tuple[str, str], dict] = {}


def reset_cache() -> None:
    with _lock:
        _verdicts.clear()


def _register_reset() -> None:
    if reset_cache not in projectenv.RESET_HOOKS:
        projectenv.RESET_HOOKS.append(reset_cache)


_register_reset()


# ---------------------------------------------------------------- 覆盖度（只读缓存，不起进程）


def normalize_modules(modules: Iterable[str] | None) -> tuple[tuple[str, ...], int]:
    """要量的 import 名：只留合形状的顶级名（`projectenv.valid_module_name`），去重保序，最多 `MAX_COVERAGE_MODULES`。
    回 `(名字, 被丢掉的个数)`——丢掉的数出来，不静默截断。"""
    seen: dict[str, None] = {}
    dropped = 0
    for m in modules or ():
        if not isinstance(m, str) or not projectenv.valid_module_name(m):
            dropped += 1
        elif m not in seen:
            if len(seen) >= MAX_COVERAGE_MODULES:
                dropped += 1
            else:
                seen[m] = None
    return tuple(seen), dropped


def script_modules(root: str | Path, script: str) -> tuple[str, ...] | None:
    """这个脚本开跑要 import 得到的第三方名（静态：只读源码，不起解释器）；算不出回 None。

    与依赖门 / 检测「装齐」的判据是同一份（联合计划的 `missing + satisfied` 与映射不到包名的 `unknown`），不另写。"""
    from . import deprepair  # noqa: PLC0415 — 明确的检查动作才需要；不把安装器拖进每次 import

    names = deprepair.script_import_names(root, script)
    return None if names is None else tuple(names)


def coverage_of(health: dict | None, modules: tuple[str, ...], *, role: str = ROLE_USER) -> dict:
    """一条体检结论（`userenvs` 缓存里的，或 `None` = 没量过）→ 这组 import 的覆盖度。纯函数，不碰磁盘、不起进程。

    * 环境本身不健康 → `unusable`，**不看**模块（跑不了 Tavotto 的环境谈不上「缺包」；也别把它和缺包混成一句）；
    * `deferred` / 量不出 → `not_checked`；
    * 否则按每个模块的 `not_found` / `import_error` 分开说，role=`target` 是正要跑脚本的那个环境。
    `versions_checked` 恒为 False：覆盖度只回答「导入得到吗」，已装版本满不满足项目声明归 `distmeta`。"""
    base: dict = {
        "role": role,
        "modules": {},
        "detail": [],
        "executed_user_code": bool((health or {}).get("execution", {}).get("may_run_package_init")),
        "versions_checked": False,
    }
    if health is None:
        return {
            **base,
            "state": COV_NOT_CHECKED,
            "detail": [{"code": DETAIL_NOT_CHECKED, "reason": "no_verdict"}],
        }
    if health.get("deferred_env") or health.get("health_deferred"):
        return {
            **base,
            "state": COV_NOT_CHECKED,
            "detail": [{"code": DETAIL_NOT_CHECKED, "reason": "deferred"}],
        }
    if not health.get("ok"):
        return {
            **base,
            "state": COV_UNUSABLE,
            "detail": [
                {
                    "code": DETAIL_UNUSABLE,
                    "reason": _STATUS_OF_CODE.get(str(health.get("code") or ""), STATUS_UNUSABLE),
                }
            ],
        }
    ok_map = health.get("modules_ok") or {}
    detail_map = health.get("modules_detail") or {}
    states: dict[str, str] = {}
    for name in modules:
        state = detail_map.get(name)
        if state not in MODULE_STATES:
            # 老形状的结论（只有 modules_ok）：True 可信；None 是延后；False 说不出是没装还是导入失败——不猜
            state = {True: MODULE_FOUND, None: MODULE_DEFERRED}.get(
                ok_map.get(name), MODULE_UNKNOWN
            )
            if name not in ok_map:
                state = MODULE_UNKNOWN
        states[name] = state
    base["modules"] = states
    failed = {MODULE_NOT_FOUND: [], MODULE_IMPORT_ERROR: []}
    for name, state in states.items():
        if state in failed:
            failed[state].append(name)
    code_of = {
        ROLE_USER: {
            MODULE_NOT_FOUND: DETAIL_NOT_FOUND_USER,
            MODULE_IMPORT_ERROR: DETAIL_IMPORT_FAILED_USER,
        },
        ROLE_TARGET: {
            MODULE_NOT_FOUND: DETAIL_MISSING_TARGET,
            MODULE_IMPORT_ERROR: DETAIL_IMPORT_FAILED_TARGET,
        },
    }[role if role in (ROLE_USER, ROLE_TARGET) else ROLE_USER]
    detail = [
        {"code": code_of[kind], "module": name, "reason": kind}
        for kind in (MODULE_NOT_FOUND, MODULE_IMPORT_ERROR)
        for name in failed[kind]
    ]
    found = [n for n, st in states.items() if st == MODULE_FOUND]
    unresolved = [n for n, st in states.items() if st in (MODULE_DEFERRED, MODULE_UNKNOWN)]
    if detail:
        state = COV_PARTIAL if found else COV_MISSING
    elif unresolved:
        state = COV_NOT_CHECKED
        detail = [{"code": DETAIL_NOT_CHECKED, "reason": "module_state_unknown"}]
    else:
        state = COV_COVERED
    return {**base, "state": state, "detail": detail}


def _coverage_for(
    python: str, modules: tuple[str, ...], *, role: str, bundled: bool = False
) -> dict:
    """某个候选此刻的覆盖度：只读 `userenvs` 的缓存（路径 + 环境代 + import 集合）。没有就是「没量过」。"""
    return coverage_of(userenvs.cached_probe(python, modules, bundled=bundled), modules, role=role)


# ---------------------------------------------------------------- 候选（私有行 → 公开行）


def _rows(root: Path, script: str | None) -> list[dict]:
    """候选行（含私有 `_python`）。线索来自 `projscan.environment_evidence`——**同一个候选证据入口**，不另写
    onboarding resolver；登录 shell 的答案只在被明确问过时才有。"""
    return projscan.environment_evidence(root, script, private=True)["candidates"]


def candidate_python(root: str | Path, script: str | None, candidate_id: str) -> str | None:
    """候选 id → 解释器路径（只来自本机自己的枚举，**不接受调用方给的路径**）；找不到回 None。"""
    for row in _rows(Path(root), script):
        if row["id"] == candidate_id:
            return row["_python"]
    return None


def _verdict(python: str, generation: str) -> dict | None:
    with _lock:
        return _verdicts.get((projectenv._executable_key(python), generation))


def _store_verdict(python: str, generation: str, health: dict) -> dict:
    summary = {
        "ok": bool(health.get("ok")),
        "code": str(health.get("code") or ""),
        "support": str(health.get("support") or ""),
        "python_version": str(health.get("python_version") or ""),
        "matplotlib_version": str(health.get("matplotlib_version") or ""),
        "checked_at": time.time(),
    }
    with _lock:
        _verdicts[(projectenv._executable_key(python), generation)] = summary
    return summary


def _status_of(summary: dict | None) -> str:
    if summary is None:
        return STATUS_UNCHECKED
    if summary["ok"]:
        return STATUS_HEALTHY
    return _STATUS_OF_CODE.get(summary["code"], STATUS_UNUSABLE)


def _public_health(summary: dict | None) -> dict | None:
    if summary is None:
        return None
    return {
        k: summary[k]
        for k in ("ok", "code", "support", "python_version", "matplotlib_version", "checked_at")
    }


def _builtin_coverage(mods: tuple[str, ...], is_target: bool) -> dict:
    python = runtime.bundled_python()
    role = ROLE_TARGET if is_target else ROLE_USER
    if not python:
        return coverage_of(None, mods, role=role)
    return _coverage_for(python, mods, role=role, bundled=True)


def _locked_by() -> dict | None:
    """全局显式解释器（环境变量 / 设置里指定的）压过一切项目级决定：采用不会生效，说清楚是谁锁的。"""
    pinned = pool.explicit_worker_python()
    if not pinned:
        return None
    return {"source": pinned[1]}


def recommend(
    root: str | Path, script: str | None = None, *, modules: Iterable[str] | None = None
) -> dict:
    """此刻的环境建议（公开投影）。**纯读：不起任何解释器、不问登录 shell、不写任何东西**。

    `modules`（可选，脚本所需的 import）：每个候选另带 `coverage`——**只读** `userenvs` 缓存里已有的覆盖度（明确的检查
    或依赖门留下的），没量过如实写 `not_checked`。覆盖度不改推荐顺序、不改任何决定（显式选择永远排在最前）。

    候选 = 线索（项目 venv / 编辑器 / `.python-version` / Conda / pyenv 的落盘记录）+ 项目记住的决定 + 用户
    明确检查过留下的结论。没检查过的候选如实写 `unchecked`，不冒充 verified。"""
    root = Path(root)
    record = projectenv.remembered_record(root)
    consent = projectenv.consent_of(record)
    default_chain = bool(record and record.get("mode") == projectenv.MODE_DEFAULT_CHAIN)
    locked = _locked_by()
    remembered_python = record["path"] if record and record.get("path") else ""
    rows = _rows(root, script)
    mods, _dropped = normalize_modules(modules) if modules is not None else ((), 0)
    candidates: list[dict] = []
    current_id: str | None = None
    for index, row in enumerate(rows):
        python = row["_python"]
        generation = projectenv.environment_generation(python)
        is_current = bool(remembered_python) and projectenv._same_executable(
            python, remembered_python
        )
        verdict = _verdict(python, generation)
        status = _status_of(verdict)
        if is_current:
            current_id = row["id"]
            if not record.get("exists"):
                status = STATUS_MISSING
            elif projectenv.generation_changed(record):
                status = STATUS_CHANGED
            label = {
                projectenv.CONSENT_CONFIRMED: LABEL_SELECTED,
                projectenv.CONSENT_AUTO_DETECTED: LABEL_AUTO,
            }.get(consent, LABEL_LEGACY)
        elif row["scope"] == projscan.SCOPE_PROJECT:
            label = LABEL_PROJECT
        elif status == STATUS_HEALTHY:
            label = LABEL_CHECKED
        else:
            label = LABEL_MACHINE
        candidates.append(
            {
                "id": row["id"],
                "label": label,
                "name": row.get("label") or "",
                "sources": list(row["sources"]),
                "scope": row["scope"],
                "python_relative": row.get("python_relative"),
                "generation": generation,
                "status": status,
                "checked": verdict is not None,
                "health": _public_health(verdict),
                "current": is_current,
                "_order": index,
                **(
                    {
                        "coverage": _coverage_for(
                            python, mods, role=ROLE_TARGET if is_current else ROLE_USER
                        )
                    }
                    if mods
                    else {}
                ),
            }
        )
    candidates.sort(key=lambda c: (LABELS.index(c["label"]), c["_order"]))
    for c in candidates:
        c.pop("_order")
    candidates.append(
        {
            "id": BUILTIN_ID,
            "label": LABEL_BUNDLED,
            "name": "",
            "sources": [],
            "scope": SCOPE_MACHINE,
            "python_relative": None,
            "generation": "",
            "status": STATUS_UNCHECKED,
            "checked": False,
            "health": None,
            "current": default_chain,
            "read_only": True,
            # 内置 runtime 的覆盖度只在它正是目标环境时给，且同样只读缓存（依赖门量过才有）
            **(
                {"coverage": _builtin_coverage(mods, default_chain)}
                if mods and default_chain
                else {}
            ),
        }
    )
    recommended = None
    if locked is None and not default_chain:
        for c in candidates:
            if c["id"] != BUILTIN_ID and c["status"] not in _BAD:
                recommended = c["id"]
                break
    mode = projectenv.adoption_mode()
    # 只有确认模式才问用户（ADR 0114 §二）；检测模式（默认，§六）由准备 / 运行时的自动检测决定，用户不选环境
    needs_decision = bool(
        mode == projectenv.ADOPTION_CONFIRM
        and locked is None
        and consent == projectenv.CONSENT_NONE
        and not default_chain
        and any(
            c["scope"] == projscan.SCOPE_PROJECT and c["status"] not in _BAD
            for c in candidates
            if c["id"] != BUILTIN_ID
        )
    )
    return {
        "version": REC_VERSION,
        "decision": {
            "mode": mode,
            "consent": "builtin" if default_chain else consent,
            "locked_by": locked,
            "needs_decision": needs_decision,
            "current_id": current_id,
        },
        "recommended_id": recommended,
        "candidates": candidates,
        "python_requirement": _python_requirement(rows),
        "check": {
            "executes_candidates": True,
            "max_candidates": CHECK_MAX_CANDIDATES,
            "deadline_s": CHECK_DEADLINE_S,
            "per_candidate_timeout_s": projectenv.PROBE_TIMEOUT_S,
            "scopes": list(CHECK_SCOPES),
            # 检查会真起候选解释器、真 import 脚本要的包（= 执行它们的 `__init__`）：不是只读观察，不宣称无副作用
            "executes_user_code": True,
            "side_effect_free": False,
        },
    }


# ---------------------------------------------------------------- 报告里的那一个事实（检测模式）

#: 「用的是哪一类」（闭集，界面按它给一句人话；不带路径、不带「环境 / 解释器」之类的词）
KIND_BUILTIN = "builtin"  # Tavotto 自带的（内置 runtime / 自身 / 源码模式自建的）
KIND_PROJECT = "project"  # 项目自己带的（项目 venv）
KIND_USER = "user"  # 这台电脑上已有的（Conda / pyenv / 登录 shell / 系统 Python）
KIND_MANAGED = "managed"  # Tavotto 为这个项目装好的
KIND_LOCKED = "locked"  # 全局指定的（环境变量 / 设置里），压过一切项目级决定
KINDS = (KIND_BUILTIN, KIND_PROJECT, KIND_USER, KIND_MANAGED, KIND_LOCKED)
_KIND_OF_SOURCE = {
    pool.SOURCE_ENV: KIND_LOCKED,
    pool.SOURCE_CONFIGURED: KIND_LOCKED,
    pool.SOURCE_MANAGED: KIND_BUILTIN,
    pool.SOURCE_BUNDLED: KIND_BUILTIN,
    pool.SOURCE_CURRENT: KIND_BUILTIN,
    pool.SOURCE_SYSTEM: KIND_USER,
    pool.SOURCE_PROJECT_VENV: KIND_PROJECT,
    pool.SOURCE_MANAGED_PROJECT: KIND_MANAGED,
}
#: 谁定的：用户明确选的 / 机器检测（或旧版自动）定的 / 没有项目级决定（默认）/ 全局指定压着
DECIDED_USER = "user"
DECIDED_AUTO = "auto"
DECIDED_DEFAULT = "default"
DECIDED_LOCKED = "locked"


def _switched(adopted: dict | None, invalidated: dict | None, effective: str) -> bool:
    """这次生效的解释器与之前生效的是不是两个——由前后选择推导，不只看有没有「采用」（Codex #820 P2）。

    记住的解释器消失 / 被重建、默认链条能跑脚本时，检测不采用任何新候选（`adopted` 为 None），用户却已经被
    换到回退解释器；那一次作废记录里的 `python` 就是「之前」，此刻生效的 `effective` 是「之后」。"""
    if adopted is not None:
        return True
    previous = str((invalidated or {}).get("python") or "")
    return bool(previous and effective and not pool.same_python(previous, effective))


def adoption_fact(
    root: str | Path,
    source: str,
    *,
    adopted: dict | None,
    invalidated: dict | None,
    effective: str = "",
) -> dict:
    """准备报告里关于环境**唯一**要给用户看的事实（ADR 0114 §六）：这次用的是哪一类、谁定的、这次检查是不是
    刚换了一个（`switched`）、换之前那个为什么不能用（`replaced`）。**不要求用户做任何事**——要用户动手的只有
    「安装缺少的组件」，那在依赖检查项与 `prepare_dependencies` 动作里。纯读，不带路径。"""
    kind = _KIND_OF_SOURCE.get(source) if source else None
    if kind == KIND_LOCKED:
        decided = DECIDED_LOCKED
    else:
        consent = projectenv.consent_of(projectenv.remembered_record(root))
        decided = {
            projectenv.CONSENT_CONFIRMED: DECIDED_USER,
            projectenv.CONSENT_AUTO_DETECTED: DECIDED_AUTO,
            projectenv.CONSENT_LEGACY_AUTO: DECIDED_AUTO,
        }.get(consent, DECIDED_DEFAULT)
    return {
        "mode": projectenv.adoption_mode(),
        "kind": kind,
        "decided_by": decided,
        "switched": _switched(adopted, invalidated, effective),
        "replaced": {"reason": str(invalidated.get("reason") or "")} if invalidated else None,
    }


def _python_requirement(rows: list[dict]) -> dict:
    """这个范围对 Python 的要求：支持矩阵的区间（`projectenv` 的运行时镜像，不抄第三份）+ 项目线索。
    `.python-version` 是**线索**，不伪造成完整兼容区间；没有任何声明时 `declared` 为 None、`status` 保持 unknown。"""
    hint = None
    for row in rows:
        if userenvs.SOURCE_PYTHON_VERSION in row["sources"] and row.get("label"):
            hint = {"source": userenvs.SOURCE_PYTHON_VERSION, "value": row["label"]}
            break
    lo, hi = projectenv.PYTHON_MIN, projectenv.PYTHON_MAX_EXCLUSIVE
    return {
        "supported": {"min": ".".join(map(str, lo)), "max_exclusive": ".".join(map(str, hi))},
        "declared": hint,
        "status": "unknown",
    }


# ---------------------------------------------------------------- 采用（明确动作）


#: 采用被拒的稳定码（常量式：`tests/test_error_codes.py` 直接读 `ERROR_CODES`）。体检不过时 code 是体检自己的
#: （`projectenv.ERROR_*`），不在这张表里。
ERROR_LOCKED = "environment_locked"
ERROR_CANDIDATE_GONE = "environment_candidate_gone"
ERROR_CHANGED = "environment_changed"
ERROR_GENERATION_REQUIRED = "environment_generation_required"
ERROR_INTERPRETER_NOT_FOUND = "interpreter_not_found"
#: 项目设置写不进去（只读 / 数据卷满）：`projectenv.remember()` 回 False，决定没落盘、解析器不变
ERROR_SAVE_FAILED = "environment_save_failed"
ERROR_CODES = (
    ERROR_LOCKED,
    ERROR_CANDIDATE_GONE,
    ERROR_CHANGED,
    ERROR_GENERATION_REQUIRED,
    ERROR_INTERPRETER_NOT_FOUND,
    ERROR_SAVE_FAILED,
)


class AdoptionRefused(Exception):
    """采用没有发生（项目设置一个字节没写）。`code` 稳定；`status` 是 HTTP 那一侧的状态码；
    体检不过时 `health` 是体检结论、`python` 是那条解释器（只给调用方做项目相对投影）。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: int = 400,
        health: dict | None = None,
        python: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.health = health
        self.python = python


def adopt_candidate(
    root: str | Path,
    script: str | None,
    candidate_id: str,
    *,
    expected_generation: str = "",
    module: str = "",
) -> dict:
    """环境建议上点的「使用」——**唯一实现**：HTTP `PATCH /api/engine/environment {candidate}` 与 MCP 的
    `adopt_environment=` 都委派这里（T10），不复制判据。成功回体检结论；没采用一律 `AdoptionRefused`；
    环境正被安装占着抛 `envlease.EnvironmentBusy`（与安装同一把锁）。

    顺序就是授权的含义：全局显式解释器压着 → 不假装能采用（`environment_locked`）；候选 id 只从本机自己的
    枚举换路径（不接受调用方给路径）；`expected_generation` 是用户看到建议那一刻的环境代，对不上（这期间被重建）
    → `environment_changed`，绝不采用另一个环境；现场体检不过 → 体检的 code；写项目设置只有这一处
    （`automatic=False`）。"""
    # 采用是少见的明确动作：不把安装器拖进每次 import
    from . import deprepair, envlease  # noqa: PLC0415

    root = str(root)
    if pool.explicit_worker_python():
        raise AdoptionRefused(
            "全局指定的解释器正在生效，项目级的选择不会被使用；请先解除全局指定",
            code=ERROR_LOCKED,
            status=409,
        )
    found = candidate_python(root, script or None, candidate_id)
    if found is None:
        raise AdoptionRefused("这个候选环境已经找不到了，请重新检查", code=ERROR_CANDIDATE_GONE)
    if not expected_generation:
        # 采用必须绑着用户看到那一刻的环境代：不传就等于「采用此刻碰巧在那儿的任何环境」（pr04 #814 同一条线）
        raise AdoptionRefused(
            "采用候选环境需要带上你确认时看到的环境版本，请重新查看再确认",
            code=ERROR_GENERATION_REQUIRED,
        )
    if projectenv.environment_generation(found) != expected_generation:
        raise AdoptionRefused(
            "这个环境在你确认之前被重建过，请重新查看再确认",
            code=ERROR_CHANGED,
            status=409,
        )
    if not Path(found).is_file():
        raise AdoptionRefused(
            "找不到这个环境的解释器", code=ERROR_INTERPRETER_NOT_FOUND, python=found
        )
    health = projectenv.probe_environment(found, module or None)
    if not health.get("ok"):
        raise AdoptionRefused(
            "这个环境没有通过体检",
            code=str(health.get("code") or ""),
            health=health,
            python=found,
        )
    # 采用与依赖安装互斥（T06）：目标环境本身正被改动时它的体检也是瞬时的，一并拒绝
    if envlease.is_mutating(found):
        raise envlease.EnvironmentBusy("这个环境正在安装依赖，请等它结束再采用。")
    stale = False

    def _commit():
        nonlocal stale
        # 最后一道：体检（可能数十秒）期间环境可能被重建，health 量的就不是用户确认的那一代。
        # 紧贴写入再比一次，不符就什么都不记
        if projectenv.environment_generation(found) != expected_generation:
            stale = True
            return None
        return projectenv.remember(
            root,
            found,
            automatic=False,
            trigger=projectenv.TRIGGER_RECOMMENDED,
            module=module,
            health=health,
        )

    saved = deprepair.unless_installing(root, _commit)
    if stale:
        raise AdoptionRefused(
            "这个环境在你确认之前被重建过，请重新查看再确认",
            code=ERROR_CHANGED,
            status=409,
        )
    if not saved:
        # `remember()` 回 False = 决定没写进项目设置（只读 / 数据卷满）：解析器不变，后面的脚本仍在旧 / 默认
        # 解释器里跑。不重置池、不假装采用（Codex #818 r4220889695）
        raise AdoptionRefused(
            "没能把这个环境保存到项目设置里（设置文件只读或磁盘已满），本次没有采用",
            code=ERROR_SAVE_FAILED,
            status=500,
        )
    pool.reset_worker_python()
    pool.shutdown_all(root)
    return health


# ---------------------------------------------------------------- 检查（明确动作）


class CheckBusy(Exception):
    """这个项目已经有一次检查在进行：不并发起第二组候选进程。"""


#: 进行中的检查：项目键 → 它的取消事件（`cancel_check` 用）
_running: dict[str, threading.Event] = {}


def cancel_check(root: str | Path) -> bool:
    """请求取消这个项目正在进行的检查；回「有没有检查在进行」。在两个候选之间生效。"""
    with _lock:
        event = _running.get(projectenv._key(root))
    if event is None:
        return False
    event.set()
    return True


def check(
    root: str | Path,
    script: str | None = None,
    *,
    ids: list[str] | None = None,
    scope: str = SCOPE_PROJECT,
    include_login_shell: bool = False,
    deadline_s: float = CHECK_DEADLINE_S,
    cancel: threading.Event | None = None,
    probe: Callable[..., dict] | None = None,
    clock: Callable[[], float] = time.monotonic,
    modules: Iterable[str] | None = None,
) -> dict:
    """对**被点名范围**里的候选运行健康探测（这是起候选解释器的唯一入口），回更新后的建议 + 这次检查的账。

    * 范围：`ids` 点名的候选；没点名按 `scope`（默认只查项目自己的线索——用户自己的环境、最便宜）。内置 /
      默认链条不在其中（它不是候选解释器）。最多 `CHECK_MAX_CANDIDATES` 个，超出的列在 `skipped`；
    * `include_login_shell=True`：先问一次用户的登录 shell（读他的 rc 文件，所以只有这里会问），答案并进候选；
    * 预算：整次 `deadline_s`、每个候选 `PROBE_TIMEOUT_S`——**总时限同样约束正在起的那个探测**：单个探测的超时取
      `min(PROBE_TIMEOUT_S, 剩余)`，剩余不足 `MIN_PROBE_BUDGET_S` 就不起，剩余候选记 `deadline`；`cancel` 在两个候选之间生效（已经起的那个探测
      跑完它自己的超时，不留孤儿——探测本身是 `subprocess.run`）；到限 / 取消的剩余候选记入 `skipped`；
    * 检查 ≠ 采用：这里不写项目设置。结论只进进程内缓存（键带环境代，路径被重建就对不上）；
    * `modules`（脚本所需的 import，见 `script_modules`）：每个被检查的候选另外回答「这些 import 它装齐了没有」，覆盖度写进
      `userenvs` 的体检缓存（依赖门与检测读同一份），结果里每个候选带 `coverage`。**这会真 import 那些包**——执行它们的
      `__init__`，所以结果的 `executed` 如实写明、`side_effect_free` 恒为 False；
    * 返回里的 `executed` 是这次检查**实际做了什么**的账（起了几个候选、import 了哪些名字），不是预估。
    """
    root = Path(root)
    if scope not in CHECK_SCOPES:
        scope = SCOPE_PROJECT
    key = projectenv._key(root)
    own = cancel if cancel is not None else threading.Event()
    with _lock:
        if key in _running:
            raise CheckBusy(key)
        _running[key] = own
    try:
        return _check(
            root, script, ids, scope, include_login_shell, deadline_s, own, probe, clock, modules
        )
    finally:
        with _lock:
            _running.pop(key, None)


def _check(
    root,
    script,
    ids,
    scope,
    include_login_shell,
    deadline_s,
    cancel,
    probe,
    clock=time.monotonic,
    modules=None,
) -> dict:
    if include_login_shell:
        userenvs.login_shell_pythons()  # 明确动作：问一次，答案进缓存，`_rows` 随后读得到
    probe_fn = probe or projectenv.probe_environment
    mods, dropped = normalize_modules(modules) if modules is not None else ((), 0)
    wanted = [
        r
        for r in _rows(root, script)
        if (ids is not None and r["id"] in ids)
        or (
            ids is None
            and (
                scope == SCOPE_ALL
                or (scope == SCOPE_PROJECT) == (r["scope"] == projscan.SCOPE_PROJECT)
            )
        )
    ]
    started = clock()
    checked: list[str] = []
    skipped: list[dict] = []
    cancelled = False
    for index, row in enumerate(wanted):
        reason = ""
        if cancel.is_set():
            cancelled, reason = True, "cancelled"
        elif deadline_s - (clock() - started) < MIN_PROBE_BUDGET_S:
            reason = "deadline"
        elif len(checked) >= CHECK_MAX_CANDIDATES:
            reason = "limit"
        if reason:
            skipped.append({"id": row["id"], "reason": reason})
            continue
        python = row["_python"]
        generation = projectenv.environment_generation(python)
        remaining = deadline_s - (clock() - started)
        # 不带 modules 时调用形状与以前逐字相同（自定义探测函数不必认识新参数）
        health = probe_fn(
            python,
            timeout=min(projectenv.PROBE_TIMEOUT_S, remaining),
            **({"modules": mods} if mods else {}),
        )
        _store_verdict(python, generation, health)
        # 覆盖度的唯一一份缓存在 userenvs（路径 + 环境代 + import 集合）；这里只往里写，不另存
        userenvs.remember_probe(python, mods, health)
        checked.append(row["id"])
    out = {
        "checked": checked,
        "skipped": skipped,
        "cancelled": cancelled,
        # 如实的账：起了几个候选解释器、真 import 了哪些名字。授权的检查不宣称无副作用——import 一个包就是执行它的
        # `__init__`（可能写文件、联网、改全局状态），我们看不见也拦不住
        "executed": {
            "ran_user_code": bool(checked),
            "candidate_interpreters": len(checked),
            "imported_modules": list(mods) if checked else [],
            "may_run_package_init": bool(checked and mods),
            "side_effect_free": False,
        },
        "recommendation": recommend(root, script, modules=mods or None),
    }
    if modules is not None:
        out["coverage"] = {"modules": list(mods), "dropped": dropped}
    return out
