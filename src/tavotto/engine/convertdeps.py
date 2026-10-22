"""「生成可编辑版本」新脚本缺 Python 包时的**自动安装**（ADR 0143 修订「R / MATLAB 来源」裁决 4）。

R / MATLAB 转写出的新脚本要 pandas、statsmodels 这类包，用户的环境里不一定有。维护者裁决：缺了就自动装进用户环境、不弹卡。
这里**不新写任何 pip 调用路径**：缺什么由 `deprepair.joint_plan_for`（= `importscan` 静态扫 + `depplan` 对照目标解释器）算，
装由 `deprepair.create_joint_plan` + `deprepair.prepare`（联合准备的现有入口：envlease、环境代事务、装后核验、记账、
账本、取消、worker 退役全在里面）。本模块只回答一件事：**这一次能不能不问用户直接走那条现有入口**。

只在下面全部成立时自动装，否则回 `status="card"`（什么都不装，校验运行照常跑、缺包由 `missing_dependency` 如实失败，
用户走现有的缺依赖卡）：

1. 开关没关：`ai.convert.auto_install != false` 且没有 `TAVOTTO_NO_CONVERT_AUTO_INSTALL=1`；
2. 目标是现有链条里的**安装目标**，且此刻就是项目会跑脚本的那个环境：
   * `project_venv`：项目选中的就是项目自己的 `.venv`（原地装，摘要里 `modifies_user_environment`）；
   * `tavotto_managed`：项目选中的就是 Tavotto 受管环境的现有一代（`SOURCE_MANAGED_PROJECT`），且计划**不新建环境、不下载私有 Python**；
   内置 runtime / 本进程解释器 / 系统或 Conda 解释器 / 用户设置或环境变量固定的解释器 / 项目记录里用户自己采用的环境，**都不是**——
   往受管环境装并不会让一个跑在别处的脚本找到包，且安装结束会把项目的解释器记成受管环境（等于无声换环境，ADR 0114 要用户点一次）；
3. 计划 `ready`、没有 `blocked` / `unknown` / 点名“装不了的包”，且要装的**每一个**发行包都在 `AUTO_INSTALL_DISTRIBUTIONS`
   闭集里（提示词推荐的那批科学计算包）——Agent 写进脚本的任意 import 名不能变成对用户环境的任意 pip 安装；
4. `create_joint_plan` 没有拒绝（全局固定解释器、修复轮次用满、磁盘不足……都在那里拒绝）。

授权口径（需要维护者知悉）：`deprepair._check_confirmed` 要求调用方回显“给用户看过的摘要”，并写明服务端自己持有的摘要不得代替回显。
这里回显的是**同一次调用里刚算出的**计划摘要，其依据是用户 2026-10-23 裁决“新脚本要的包缺失时自动装进用户环境”这条常驻同意
（可用上面的开关收回）。摘要对不上 / 环境在期间变了，`prepare` 自己的 `dependency_impact_changed` / `repair_plan_stale` 照样拦。

纯标准库（Flask 父进程 import 链；`deprepair` 在函数内 import，让本模块的常量与开关不拖入它的整条依赖）。
"""

from __future__ import annotations

import os
import re

from . import config, depresolve

#: 允许自动安装的发行包（PEP 503 规范名）。与 `ai_prompt_pack` 的 R / MATLAB 对照表同一批：读数据、统计、拟合、读 Excel。
AUTO_INSTALL_DISTRIBUTIONS = frozenset(
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

STATUS_NONE = "none"  # 没有缺的（或没有可装的）
STATUS_INSTALLED = "installed"  # 装好了
STATUS_CARD = "card"  # 不自动装：退回现有的缺依赖卡
STATUS_FAILED = "failed"  # 想装、装失败了

#: `card` 的原因闭集（只进结果与测试，不进日志）
CARD_REASONS = (
    "disabled",
    "target_not_allowed",
    "plan_unavailable",
    "plan_blocked",
    "not_in_allowlist",
    "would_create_environment",
    "plan_refused",
)

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def enabled() -> bool:
    """自动安装开关：默认开（用户 2026-10-23 裁决），`ai.convert.auto_install == false` 或 `TAVOTTO_NO_CONVERT_AUTO_INSTALL=1` 关。"""
    if os.environ.get("TAVOTTO_NO_CONVERT_AUTO_INSTALL") == "1":
        return False
    conv = config.ai_settings().get("convert")
    return not (isinstance(conv, dict) and conv.get("auto_install") is False)


def _dist_of(requirement: str) -> str:
    m = _REQ_NAME.match(requirement or "")
    return depresolve.normalize_distribution(m.group(1)) if m else ""


def _result(status: str, **extra) -> dict:
    return {"status": status, "installed": [], "missing": [], "reason": "", "code": "", **extra}


def ensure(project: str, stage_rel: str) -> dict:
    """校验运行**之前**调：`stage_rel` 是已落盘的候选临时脚本（项目相对）。回
    `{"status": none|installed|card|failed, "installed": [发行包名…], "missing": [缺的发行包名…], "reason": 闭集, "code": 修复错误码}`。
    不抛：任何意外都归成 `card`（让校验运行自己如实失败）。"""
    from . import depplan, deprepair, pool

    if not enabled():
        return _result(STATUS_CARD, reason="disabled")
    root = str(project)
    try:
        joint, kind, _python = deprepair.joint_plan_for(root, stage_rel)
    except Exception:  # noqa: BLE001 —— 解释器解析不出 / 事实取不到：不是这里的事，校验运行会给出它自己的错
        return _result(STATUS_CARD, reason="plan_unavailable")
    if joint.status == depplan.STATUS_NOTHING_NEEDED:
        return _result(STATUS_NONE)
    if joint.status != depplan.STATUS_READY or joint.blocked or joint.unknown:
        return _result(STATUS_CARD, reason="plan_blocked")
    if getattr(joint, "not_pip_installable", ()):
        return _result(STATUS_CARD, reason="plan_blocked")
    wanted = sorted({d for d in (_dist_of(r) for r in joint.requirements) if d})
    outside = [d for d in wanted if d not in AUTO_INSTALL_DISTRIBUTIONS]
    if outside or not wanted:
        return _result(STATUS_CARD, reason="not_in_allowlist", missing=wanted)
    try:
        target_kind, _py, source = deprepair.joint_target_for(root, stage_rel)
    except Exception:  # noqa: BLE001
        return _result(STATUS_CARD, reason="plan_unavailable", missing=wanted)
    allowed = (
        target_kind == deprepair.TARGET_PROJECT_VENV and source == pool.SOURCE_PROJECT_VENV
    ) or (target_kind == deprepair.TARGET_MANAGED and source == pool.SOURCE_MANAGED_PROJECT)
    if not allowed:
        return _result(STATUS_CARD, reason="target_not_allowed", missing=wanted)
    try:
        plan = deprepair.create_joint_plan(root, stage_rel, target_kind=target_kind)
    except deprepair.RepairError as exc:
        return _result(STATUS_CARD, reason="plan_refused", missing=wanted, code=exc.code)
    except Exception:  # noqa: BLE001
        return _result(STATUS_CARD, reason="plan_refused", missing=wanted)
    if plan.target_kind == deprepair.TARGET_MANAGED and (
        plan.creates_environment or plan.private_python is not None
    ):
        deprepair._discard_plan(plan.plan_id)  # 没执行的计划不留着
        return _result(STATUS_CARD, reason="would_create_environment", missing=wanted)
    names = sorted({d for d in (_dist_of(r) for r in plan.requirements) if d})
    if any(n not in AUTO_INSTALL_DISTRIBUTIONS for n in names):
        deprepair._discard_plan(
            plan.plan_id
        )  # 计划比静态扫多出了东西（声明文件里的包）：不在闭集就不自动装
        return _result(STATUS_CARD, reason="not_in_allowlist", missing=names)
    try:
        done = deprepair.prepare(plan.plan_id, None, confirmed_impact=plan.impact_digest)
    except deprepair.RepairError as exc:
        return _result(STATUS_FAILED, missing=names, code=exc.code)
    except Exception:  # noqa: BLE001
        return _result(STATUS_FAILED, missing=names, code=deprepair.ERROR_FAILED)
    if (done or {}).get("state") == deprepair.STATE_DONE:
        return _result(STATUS_INSTALLED, installed=names)
    return _result(
        STATUS_FAILED, missing=names, code=str((done or {}).get("code") or deprepair.ERROR_FAILED)
    )
