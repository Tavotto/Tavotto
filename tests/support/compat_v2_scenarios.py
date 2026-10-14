"""CompatBench V2 场景表：每个夹具的「原始失败模式 / 预期策略 / 理想执行次数」，以及 main 上实测的形状。

两层判据（不用 `xfail(strict=True)` 套整条用例——strict 只防「意外通过」，不防「失败原因变了」，
见 `tests/test_override_sequences.py` 的说明）：

* `ideal(obs)`：D1–D5 产品决策下**应该**成立的事（返回未达成项列表，空 = 理想达成）；
* `main_baseline`：main 当前实测的形状。有它的场景，测试要求 **理想未达成 且 形状与登记逐项相等**：
  - 行为修好了 -> 理想达成 -> 红，逼着删掉 `main_baseline`（该场景升格为对照组）；
  - 失败换了个样子（别的原因码 / 别的执行次数）-> 形状不符 -> 红，不许被旧登记认领。
  `main_baseline=None` 的场景是对照组：main 上就该达标，之后任何改动也不许让它退步。
"""

from __future__ import annotations

import re
from typing import Callable

from support.compatbench_v2 import Observation, Scenario

# 基线形状只比这些键（会随行为修复而变的判据；耗时等噪声不进）
SHAPE_KEYS = (
    "final_phase",
    "outcome_kind",
    "failed_figures",
    "problem_codes",
    "error_type",
    "exec_count",
    "figures_valid",
    "side_effect_count",
)

# 默认（CI / 本地）跑的冒烟子集：覆盖各类失败形状，约 2 分钟；其余 `TAVOTTO_COMPAT_V2_ALL=1`
SMOKE = (
    "s01_plain",
    "s04b_savefig_tmp_replace",
    "s06_savefig_pil_readback",
    "s16a_zero_division",
    "s25_partial_capture",
    "s26_import_cv2_sklearn",
)

# 本轮没有执行的场景 / 阶段（写清楚，不让它们「消失」）
NOT_RUN = {
    "s14_missing_pkg_then_limit/after_install": "装包后的第二段需要离线 wheelhouse（T11-S2 的 dep_house 装置，进程内 client）；本基线只观测第一段",
    "scene_19_native_cancel": "main 没有桌面拥有的 Native 运行（#1022 在飞），无从观测",
    "scene_20_native_input": "同上",
    "scene_22_reopen_native": "同上",
    "scene_23_two_projects_two_pythons": "需要两套解释器与并存的桌面进程，已有 test_foundation_script_first_run 的两标签用例覆盖 Safe 侧",
    "scene_24_safe_semantic_drift": "main 没有「弱信号」档（audit-D G2），无从观测",
    "scene_27_switch_during_fallback": "main 没有回退窗口",
    "scene_28_python_dash_m": "夹具只覆盖脚本入口；-m 入口的桌面 Native 支持待核（audit-E 场景 28）",
}


def _figure(
    obs: Observation, *, title: str | None = None, title_re: str | None = None, max_exec: int
) -> list[str]:
    miss = []
    if not obs.first_valid_figure:
        miss.append(f"没有首次有效出图（final_phase={obs.final_phase}, codes={obs.problem_codes}）")
    if obs.outcome_kind != "succeeded":
        miss.append(f"会话结果应为 succeeded，实测 {obs.outcome_kind}")
    if obs.render_errors:
        miss.append(f"有捕获的图渲染不出来: {obs.render_errors}")
    if obs.exec_count > max_exec:
        miss.append(f"真实执行 {obs.exec_count} 次，理想上限 {max_exec}")
    if obs.exec_count == 0 and obs.first_valid_figure:
        miss.append("有图但执行日志为 0（日志坏了或图不是这次执行的）")
    if title is not None and title not in obs.titles:
        miss.append(f"图里的标题 {obs.titles!r} 不含语义真值 {title!r}")
    if title_re is not None and not any(re.fullmatch(title_re, t) for t in obs.titles):
        miss.append(f"图里的标题 {obs.titles!r} 不匹配 {title_re!r}")
    return miss


def _stop(
    obs: Observation,
    *,
    code: str,
    error_type: str | None = None,
    phase: str,
    kind: str,
) -> list[str]:
    """停下来说明原因：一次执行、没有图、不回退，且会话**投影成停下**（phase / outcome_kind 对），
    而不是错误码还在、界面却说成功。"""
    miss = []
    if obs.final_phase != phase:
        miss.append(f"终局 phase 应为 {phase}，实测 {obs.final_phase}")
    if obs.outcome_kind != kind:
        miss.append(f"outcome.kind 应为 {kind}，实测 {obs.outcome_kind}")
    if obs.exec_count != 1:
        miss.append(f"应恰好执行 1 次（不回退、不重试），实测 {obs.exec_count}")
    if obs.figures_valid:
        miss.append("不该有图")
    if code not in obs.problem_codes:
        miss.append(f"原因码应含 {code}，实测 {obs.problem_codes}")
    if error_type is not None and obs.error_type != error_type:
        miss.append(f"异常类型应为 {error_type}，实测 {obs.error_type}")
    return miss


def _ideal_s05(o):
    return _figure(o, title="bytes>0=True", max_exec=2)


def _ideal_s25(o: Observation) -> list[str]:
    miss = []
    if o.figures_valid != 1:
        miss.append(f"应保留成功那张（1 张），实测 {o.figures_valid}")
    if o.final_phase != "partial":
        miss.append(f"部分捕获失败应投影为 partial，实测 {o.final_phase}")
    if "bad" not in o.failed_figures:
        miss.append(f"会话报告应指认未捕获的 bad，实测 failed_figures={o.failed_figures}")
    if o.exec_count != 1:
        miss.append(f"partial 不是回退理由，应执行 1 次，实测 {o.exec_count}")
    if o.render_errors:
        miss.append(f"保留的那张图渲染不出来: {o.render_errors}")
    return miss


def _ideal_s17(o: Observation) -> list[str]:
    """副作用必须真发生：1（路由直达）或 2（回退，已裁决接受），且与脚本自写的执行日志次数一致。"""
    miss = _figure(o, title="bytes>0=True", max_exec=2)
    if o.side_effect_count not in (1, 2):
        miss.append(f"外部副作用应为 1 或 2 次，实测 {o.side_effect_count}（夹具坏了或写入丢了）")
    elif o.side_effect_count != o.exec_count:
        miss.append(f"副作用 {o.side_effect_count} 次与执行日志 {o.exec_count} 次对不上")
    return miss


def _ideal_s18(o: Observation) -> list[str]:
    miss = []
    if o.exec_count != 1:
        miss.append(f"只读目录不该触发重试 / 回退循环，应执行 1 次，实测 {o.exec_count}")
    if o.figures_valid:
        miss.append("只读目录下不该有图")
    if not o.problem_codes:
        miss.append("没有任何原因码，用户看不到为什么")
    if o.final_phase == "completed" or o.outcome_kind == "succeeded":
        miss.append(f"只读目录写不出东西，会话不该投影成功（{o.final_phase}/{o.outcome_kind}）")
    return miss


def _ideal_deps(o: Observation, missing: dict[str, str]) -> list[str]:
    """只对「worker 解释器里真的缺」的 import 提要求：已装的包依赖计划里本来就不会出现（测试驱动在
    `facts["modules_absent"]` 里写入探针结果；没写 = 全部视为缺）。"""
    absent = set(o.facts.get("modules_absent", missing))
    missing = {imp: dist for imp, dist in missing.items() if imp in absent}
    installs = set(missing.values())
    miss = []
    if o.exec_count != 0:
        miss.append(f"检查阶段不得执行用户代码，实测 {o.exec_count}")
    if o.final_phase != "awaiting_confirmation":
        miss.append(f"缺包应停在 awaiting_confirmation，实测 {o.final_phase}")
    if o.outcome_kind != "pending":
        miss.append(f"outcome.kind 应为 pending，实测 {o.outcome_kind}")
    if "dependency_preparation_required" not in o.problem_codes:
        miss.append(f"原因码应含 dependency_preparation_required，实测 {o.problem_codes}")
    if "prepare_dependencies" not in (o.facts.get("actions_offered_final") or []):
        miss.append("用户没有「准备依赖」这个动作可点")
    got = set(o.facts.get("dependency_installs") or [])
    if not installs <= got:
        miss.append(f"依赖计划应含 {sorted(installs)}，实测 {sorted(got)}")
    got_missing = o.facts.get("dependency_missing") or {}
    for imp, dist in missing.items():
        if got_missing.get(imp) != dist:
            miss.append(f"import {imp} 应映射到发行包 {dist}，实测 {got_missing.get(imp)}")
    return miss


def _scenario(id_: str, scenes, title, failure, strategy, ideal_exec, ideal_max, outcome, **kw):
    return Scenario(
        id=id_,
        scenes=tuple(scenes),
        title=title,
        failure_mode=failure,
        expected_strategy=strategy,
        ideal_exec_count=ideal_exec,
        ideal_exec_count_max=ideal_max,
        ideal_outcome=outcome,
        **kw,
    )


# main 上 safe 里 savefig 衍生的 FileNotFoundError：被当成普通 script_error（audit-D G1：误归因）
_BLOCKED_AS_PLAIN_ERROR = {
    "final_phase": "action_required",
    "outcome_kind": "failed",
    "failed_figures": [],
    "problem_codes": ["script_error"],
    "error_type": "FileNotFoundError",
    "exec_count": 1,
    "figures_valid": 0,
    "side_effect_count": None,
}

SCENARIOS: dict[str, Scenario] = {}
IDEAL: dict[str, Callable[[Observation], list[str]]] = {}


def _add(s: Scenario, ideal: Callable[[Observation], list[str]]) -> None:
    SCENARIOS[s.id] = s
    IDEAL[s.id] = ideal


_add(
    _scenario("s01_plain", (1,), "普通 Matplotlib", "无（对照）", "Safe 直接成功", 1, 1, "figure"),
    lambda o: _figure(o, title=None, max_exec=1),
)
_add(
    _scenario(
        "s04_replace_readback",
        (4,),
        "数据文件临时写后 os.replace 再读回（不经 savefig）",
        "main 的账本只拦 savefig 衍生文件，普通数据文件改名放行——对照组，防回归",
        "Safe 直接成功，读到 2",
        1,
        1,
        "figure",
    ),
    lambda o: _figure(o, title="value=2", max_exec=1),
)
_add(
    _scenario(
        "s04b_savefig_tmp_replace",
        (4, 6),
        "savefig 先写临时文件再 os.replace 成正式图后读回",
        "Safe 的 savefig 目标被改写，改名源不存在 -> FileNotFoundError 被当成普通脚本错误",
        "识别 savefig 衍生的文件语义：直接 Native；或 Safe 确认被限制后回退 Native（一次）",
        1,
        2,
        "figure",
        main_baseline=_BLOCKED_AS_PLAIN_ERROR,
    ),
    lambda o: _figure(o, title="final bytes>0=True", max_exec=2),
)
_add(
    _scenario(
        "s05_dynamic_rename",
        (5,),
        "运行时算出路径做 os.rename 后读回",
        "同上；静态扫描看不到最终文件名，只能靠运行证据",
        "Safe 被确认限制后回退 Native（一次）",
        2,
        2,
        "figure",
        main_baseline=_BLOCKED_AS_PLAIN_ERROR,
    ),
    _ideal_s05,
)
_add(
    _scenario(
        "s06_savefig_pil_readback",
        (6,),
        "savefig 后 PIL Image.open 读回",
        "Safe 把 savefig 目标改写到别处，读回找不到 -> safe_file_operation_blocked（死胡同，只能手动）",
        "识别 savefig->读回：直接 Native；或 blocked 后静默回退 Native（一次）",
        1,
        2,
        "figure",
        main_baseline={
            "final_phase": "action_required",
            "outcome_kind": "failed",
            "failed_figures": [],
            "problem_codes": ["safe_file_operation_blocked"],
            "error_type": "FileNotFoundError",
            "exec_count": 1,
            "figures_valid": 0,
            "side_effect_count": None,
        },
    ),
    lambda o: _figure(o, title_re=r"\d+x\d+", max_exec=2),
)
_add(
    _scenario(
        "s07_savefig_exists",
        (7,),
        "savefig 后检查路径存在，不存在则 sys.exit(3)",
        "Safe 下 exists 为 False（目标被改写）-> 脚本自己退出 3 -> script_exited；灰区：不是 blocked 类，现行账本不归因",
        "灰区：exists 不应单独触发回退；预期 Safe 里 exists 与真实一致，直接成功",
        1,
        2,
        "figure",
        main_baseline={
            "final_phase": "action_required",
            "outcome_kind": "failed",
            "failed_figures": [],
            "problem_codes": ["script_exited"],
            "error_type": "SystemExit",
            "exec_count": 1,
            "figures_valid": 0,
            "side_effect_count": None,
        },
    ),
    lambda o: _figure(o, title="saved", max_exec=2),
)
_add(
    _scenario(
        "s14_missing_pkg_then_limit",
        (14,),
        "先缺 sklearn，修好后才触发 Safe 文件限制（只观测第一段）",
        "两个根因串联：缺包 + savefig->replace",
        "一次依赖确认；装好后同一意图内执行，被限制则回退一次（共享预算，不再要第二次装包确认）",
        0,
        0,
        "dependency_plan",
    ),
    lambda o: _ideal_deps(o, {"sklearn": "scikit-learn"}),
)
_add(
    _scenario(
        "s16a_zero_division",
        (16,),
        "真实脚本 bug：ZeroDivisionError",
        "不是 Safe 语义问题",
        "停下并说明原因；不回退、不 Native",
        1,
        1,
        "stop_no_fallback",
    ),
    lambda o: _stop(
        o,
        code="script_error",
        error_type="ZeroDivisionError",
        phase="action_required",
        kind="failed",
    ),
)
_add(
    _scenario(
        "s16b_name_error",
        (16,),
        "真实脚本 bug：NameError（未定义变量）",
        "不是 Safe 语义问题",
        "停下并说明原因；不回退、不 Native",
        1,
        1,
        "stop_no_fallback",
    ),
    lambda o: _stop(
        o, code="script_error", error_type="NameError", phase="action_required", kind="failed"
    ),
)
_add(
    _scenario(
        "s17_side_effect_then_limit",
        (17,),
        "先写外部副作用（本地 sqlite），之后才遇到 Safe 文件限制",
        "回退会重复一次副作用——用户 2026-10-14 裁决：接受回退时重复一次",
        "路由直达 Native 则副作用 1 次；回退则 2 次（已接受）；绝不超过 2",
        1,
        2,
        "figure",
        main_baseline={**_BLOCKED_AS_PLAIN_ERROR, "side_effect_count": 1},
    ),
    _ideal_s17,
)
_add(
    _scenario(
        "s18_readonly_dir",
        (18,),
        "项目目录只读，脚本往 cwd 写",
        "Safe 沙盒与 Native 对只读目录行为不同；Native 不比 Safe 更能写",
        "不盲目 Native：一次执行，给出可读的原因；不因只读回退循环",
        1,
        1,
        "stop_no_fallback",
        readonly_project=True,
        notes="main 实测：Safe 沙盒里脚本跑完，登记写项目目录失败 -> partial + registration_failed（无图）。"
        "对照组只钉「一次执行、无图、不循环」；原因码是否够清楚属于 PR3a/PR4 的文案问题。",
    ),
    _ideal_s18,
)
_add(
    _scenario(
        "s21_no_figure",
        (21,),
        "退出码 0 但没有任何 Figure",
        "没有阻断证据",
        "不回退：说明「脚本没画图」",
        1,
        1,
        "stop_no_fallback",
    ),
    lambda o: _stop(
        o,
        code="no_figures_captured",
        phase="partial",
        kind="execution_finished_no_figure",
    ),
)
_add(
    _scenario(
        "s25_partial_capture",
        (25,),
        "两张图，其中一张捕获失败",
        "main 把 partial 投影成 completed，失败的那张在会话报告里不可见（audit-D G3）",
        "保留成功那张；会话报告 partial 并指认未捕获的 bad；不因 partial 回退",
        1,
        1,
        "partial_figure",
        main_baseline={
            "final_phase": "completed",
            "outcome_kind": "succeeded",
            "failed_figures": [],
            "problem_codes": [],
            "error_type": None,
            "exec_count": 1,
            "figures_valid": 1,
            "side_effect_count": None,
        },
    ),
    _ideal_s25,
)
_add(
    _scenario(
        "s26_import_cv2_sklearn",
        (),
        "import 名 != 发行包名：cv2 -> opencv-python，sklearn -> scikit-learn",
        "（对照）按 import->发行包映射出依赖计划，检查阶段零执行",
        "一次确认；不盲目 Native",
        0,
        0,
        "dependency_plan",
        notes="另列场景：import 名与发行包名不一致",
    ),
    lambda o: _ideal_deps(o, {"cv2": "opencv-python", "sklearn": "scikit-learn"}),
)
_add(
    _scenario(
        "s27_import_pil_present",
        (),
        "PIL 已装（发行包 pillow）：不得被当成缺包",
        "（对照）import 名 PIL 与发行包 pillow 不同，不应触发依赖计划",
        "Safe 直接成功",
        1,
        1,
        "figure",
        notes="另列场景：import 名与发行包名不一致",
    ),
    lambda o: _figure(o, max_exec=1),
)


# 这些场景要求 worker 解释器里**缺**这些 import 名；一个都不缺 = 前提不成立（测试里 skip，不是红）
NEEDS_ABSENT = {
    "s14_missing_pkg_then_limit": ("sklearn",),
    "s26_import_cv2_sklearn": ("cv2", "sklearn"),
}


def baseline_shape(obs: Observation) -> dict:
    full = obs.to_json()
    return {k: full[k] for k in SHAPE_KEYS}
