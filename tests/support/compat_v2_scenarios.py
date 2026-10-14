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
from dataclasses import dataclass, field

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
    "figures_captured",
    "render_errors",
    "titles",
    "side_effect_count",
    # 理想判据读的 facts 键（不变量测试 test_every_field_the_ideal_predicates_read... 钉着：判据读什么，形状就钉什么）
    "facts.dependency_installs",
    "facts.dependency_missing",
    "facts.actions_offered_final",
    "facts.modules_absent",
)

# 登记基线时，main 上这几项对所有失败场景相同；场景自己的字典可覆盖
_MAIN_COMMON = {
    "figures_captured": 0,
    "render_errors": [],
    "titles": [],
    "facts.dependency_installs": [],
    "facts.dependency_missing": {},
    "facts.actions_offered_final": ["run", "recheck"],
    "facts.modules_absent": None,  # 非缺包场景不探针
}

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


# ---------------------------------------------------------------- 完整期望表
#
# 每个场景的理想判据是一张显式的期望表（`Expect`）：十个维度**每一个都必须表态**——确定值，或 `ANY` 并在
# `any_reasons` 里写明理由（数据类字段没有默认值，漏写 = 构造时 TypeError；`ANY` 没理由 = 测试红）。
# 判据太宽（只核成功码不核 phase、只核有效张数不核捕获张数……）的同一家族问题靠这张表一次收口。

REQUIRED_DIMS = (
    "final_phase",
    "outcome_kind",
    "problem_codes",
    "figures_valid",
    "figures_captured",
    "render_errors",
    "failed_figures",
    "exec_count",
    "dependency_plan",
    "side_effects",
)

# 每个维度由哪些基线形状键钉住（不变量测试核对：期望表表态的，登记的失败形状也必须钉）
DIM_TO_SHAPE = {
    "final_phase": ("final_phase",),
    "outcome_kind": ("outcome_kind",),
    "problem_codes": ("problem_codes",),
    "figures_valid": ("figures_valid",),
    "figures_captured": ("figures_captured",),
    "render_errors": ("render_errors",),
    "failed_figures": ("failed_figures",),
    "exec_count": ("exec_count",),
    "dependency_plan": (
        "facts.dependency_installs",
        "facts.dependency_missing",
        "facts.actions_offered_final",
        "facts.modules_absent",
    ),
    "side_effects": ("side_effect_count",),
}


class _Sentinel:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


ANY = _Sentinel("ANY")  # 显式「不关心」——必须在 any_reasons 里写理由
NONEMPTY = _Sentinel("NONEMPTY")  # 必须非空（例如「有原因码」，但不钉具体是哪个）
NO_PLAN = _Sentinel("NO_PLAN")  # 没有依赖计划：installs / missing 为空，也没有「准备依赖」动作
NO_SIDE_EFFECT = _Sentinel("NO_SIDE_EFFECT")  # 夹具不写外部副作用：side_effect_count 必须是 None


@dataclass(frozen=True)
class OneOf:
    values: tuple

    def __init__(self, *values):
        object.__setattr__(self, "values", tuple(values))


@dataclass(frozen=True)
class DepPlan:
    """依赖计划：`missing` 是 import 名 -> 发行包名；只对 worker 解释器里**真的缺**的（`facts.modules_absent`）提要求，
    且计划与缺失映射必须**恰好**等于探针判定的缺失集（已装的不得出现）。"""

    missing: dict


@dataclass(frozen=True)
class SideEffects:
    """外部副作用必须真发生，次数在 `allowed` 内，且等于脚本自写的执行日志次数。"""

    allowed: tuple = (1, 2)


@dataclass(frozen=True)
class Expect:
    final_phase: object
    outcome_kind: object
    problem_codes: object
    figures_valid: object
    figures_captured: object
    render_errors: object
    failed_figures: object
    exec_count: object
    dependency_plan: object
    side_effects: object
    # 可选的附加语义判据（不属于十个必填维度）
    error_type: object = ANY
    title: str | None = None
    title_re: str | None = None
    any_reasons: dict = field(default_factory=dict)


def _match(spec, value) -> bool:
    if spec is ANY:
        return True
    if spec is NONEMPTY:
        return bool(value)
    if isinstance(spec, OneOf):
        return value in spec.values
    if isinstance(spec, list) and isinstance(value, list):
        return sorted(map(str, spec)) == sorted(map(str, value))
    return value == spec


def check(obs: Observation, ex: Expect) -> list[str]:
    """按期望表逐维度核对，回未达成项（空 = 理想达成）。"""
    miss: list[str] = []
    for dim in (
        "final_phase",
        "outcome_kind",
        "problem_codes",
        "figures_valid",
        "figures_captured",
        "render_errors",
        "failed_figures",
        "exec_count",
    ):
        want = getattr(ex, dim)
        got = getattr(obs, dim)
        if not _match(want, got):
            miss.append(f"{dim} 应为 {want!r}，实测 {got!r}")
    if not _match(ex.error_type, obs.error_type):
        miss.append(f"error_type 应为 {ex.error_type!r}，实测 {obs.error_type!r}")

    plan = ex.dependency_plan
    installs = set(obs.facts.get("dependency_installs") or [])
    got_missing = obs.facts.get("dependency_missing") or {}
    offered = obs.facts.get("actions_offered_final") or []
    if plan is NO_PLAN:
        if installs or got_missing or "prepare_dependencies" in offered:
            miss.append(
                f"不该有依赖计划，实测 installs={sorted(installs)} missing={got_missing} actions={offered}"
            )
    elif isinstance(plan, DepPlan):
        absent = set(obs.facts.get("modules_absent", plan.missing))
        want_missing = {k: v for k, v in plan.missing.items() if k in absent}
        if installs != set(want_missing.values()):
            miss.append(
                f"依赖计划应恰好装 {sorted(set(want_missing.values()))}，实测 {sorted(installs)}"
            )
        if got_missing != want_missing:
            miss.append(f"缺失映射应恰好是 {want_missing}，实测 {got_missing}")
        if "prepare_dependencies" not in offered:
            miss.append("用户没有「准备依赖」这个动作可点")
    elif plan is not ANY:
        raise AssertionError(f"未知的 dependency_plan 期望: {plan!r}")

    fx = ex.side_effects
    if fx is NO_SIDE_EFFECT:
        if obs.side_effect_count is not None:
            miss.append(f"夹具不该有外部副作用，实测 {obs.side_effect_count}")
    elif isinstance(fx, SideEffects):
        if obs.side_effect_count not in fx.allowed:
            miss.append(
                f"外部副作用应在 {fx.allowed}，实测 {obs.side_effect_count}（夹具坏了或写入丢了）"
            )
        elif obs.side_effect_count != obs.exec_count:
            miss.append(f"副作用 {obs.side_effect_count} 次与执行日志 {obs.exec_count} 次对不上")
    elif fx is not ANY:
        raise AssertionError(f"未知的 side_effects 期望: {fx!r}")

    if ex.title is not None and ex.title not in obs.titles:
        miss.append(f"图里的标题 {obs.titles!r} 不含语义真值 {ex.title!r}")
    if ex.title_re is not None and not any(re.fullmatch(ex.title_re, t) for t in obs.titles):
        miss.append(f"图里的标题 {obs.titles!r} 不匹配 {ex.title_re!r}")
    return miss


def _fig(exec_count, **kw) -> Expect:
    """干净完成的出图：completed / succeeded / 无原因码 / 一张捕获且有效 / 无失败图 / 无依赖计划。"""
    base = dict(
        final_phase="completed",
        outcome_kind="succeeded",
        problem_codes=[],
        figures_valid=1,
        figures_captured=1,
        render_errors=[],
        failed_figures=[],
        exec_count=exec_count,
        dependency_plan=NO_PLAN,
        side_effects=NO_SIDE_EFFECT,
    )
    base.update(kw)
    return Expect(**base)


def _stop(code, *, phase, kind, error_type=ANY) -> Expect:
    """停下来说明原因：一次执行、没有图（捕获 0 张、无渲染错误、无失败图）、不回退。"""
    return Expect(
        final_phase=phase,
        outcome_kind=kind,
        problem_codes=[code],
        figures_valid=0,
        figures_captured=0,
        render_errors=[],
        failed_figures=[],
        exec_count=1,
        dependency_plan=NO_PLAN,
        side_effects=NO_SIDE_EFFECT,
        error_type=error_type,
    )


def _deps(missing: dict) -> Expect:
    return Expect(
        final_phase="awaiting_confirmation",
        outcome_kind="pending",
        problem_codes=["dependency_preparation_required"],
        figures_valid=0,
        figures_captured=0,
        render_errors=[],
        failed_figures=[],
        exec_count=0,
        dependency_plan=DepPlan(missing),
        side_effects=NO_SIDE_EFFECT,
    )


def _scenario(id_: str, scenes, title, failure, strategy, ideal_exec, ideal_max, outcome, **kw):
    if kw.get("main_baseline") is not None:
        kw["main_baseline"] = {**_MAIN_COMMON, **kw["main_baseline"]}
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
EXPECT: dict[str, Expect] = {}
IDEAL: dict = {}  # 兼容旧名：IDEAL[sid](obs) -> 未达成项


def _add(s: Scenario, ex: Expect) -> None:
    SCENARIOS[s.id] = s
    EXPECT[s.id] = ex
    IDEAL[s.id] = lambda obs, _ex=ex: check(obs, _ex)


_add(
    _scenario("s01_plain", (1,), "普通 Matplotlib", "无（对照）", "Safe 直接成功", 1, 1, "figure"),
    _fig(1, title="plain"),
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
    _fig(1, title="value=2"),
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
    _fig(OneOf(1, 2), title="final bytes>0=True"),
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
    _fig(OneOf(1, 2), title="bytes>0=True"),
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
    _fig(OneOf(1, 2), title_re=r"\d+x\d+"),
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
    _fig(OneOf(1, 2), title="saved"),
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
    _deps({"sklearn": "scikit-learn"}),
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
    _stop("script_error", phase="action_required", kind="failed", error_type="ZeroDivisionError"),
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
    _stop("script_error", phase="action_required", kind="failed", error_type="NameError"),
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
    _fig(OneOf(1, 2), title="bytes>0=True", side_effects=SideEffects((1, 2))),
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
    # 只读目录：原因码是否够清楚属于文案问题，这里只要求「有原因码」；phase 只能是停下的两种之一
    Expect(
        final_phase=OneOf("action_required", "partial"),
        outcome_kind="failed",
        problem_codes=NONEMPTY,
        figures_valid=0,
        figures_captured=0,
        render_errors=[],
        failed_figures=[],
        exec_count=1,
        dependency_plan=NO_PLAN,
        side_effects=NO_SIDE_EFFECT,
    ),
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
    _stop("no_figures_captured", phase="partial", kind="execution_finished_no_figure"),
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
            "figures_captured": 1,
            "titles": ["good"],
            "side_effect_count": None,
        },
    ),
    _fig(1, final_phase="partial", failed_figures=["bad"], title="good"),
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
    _deps({"cv2": "opencv-python", "sklearn": "scikit-learn"}),
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
    _fig(1, title="pil present"),
)


# 这些场景要求 worker 解释器里**缺**这些 import 名；一个都不缺 = 前提不成立（测试里 skip，不是红）
NEEDS_ABSENT = {
    "s14_missing_pkg_then_limit": ("sklearn",),
    "s26_import_cv2_sklearn": ("cv2", "sklearn"),
}


def baseline_shape(obs: Observation) -> dict:
    full = obs.to_json()
    return {
        k: (full["facts"].get(k[len("facts.") :]) if k.startswith("facts.") else full[k])
        for k in SHAPE_KEYS
    }
