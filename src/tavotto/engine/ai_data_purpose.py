"""数据出图：按「看图目的」重排备选，并给非推荐的备选一句结构化的「为什么不是它」（ADR 0178 修订 2026-11-25）。

目的（`purpose`）不由推荐器自己定（决策框架 §2）：只有两个来源——用户点选的（`answers.purpose`，算「用户确认过的目的」）、
从数据结构推出的**假设**（规则现在第一名所属的族；永远只是假设）。本模块**不调模型、不联网、不读数据**。

硬约束（`tests/test_ai_data_purpose.py` 逐条锁住）：

* **纯排列**：`resolve` 只调换规则已经给出的备选的顺序，绝不新增、不复活被规则拿掉的备选（`x_independent`、`summarised_not_raw`、
  被 `MAX_CANDIDATES` 截掉的……）。输入是 `default_candidates` / 合画并好 / 换过误差类型之后的 `Candidate` 序列，输出是它的一个排列。
* 没有 `answers.purpose` 时，顺序**逐项不变**（假设的目的 = 第一名所属的族，重排是恒等）。
* 重排是稳定分区：同一类里保持规则原来的相对顺序；没有依据的并列项不动。合画成对的两张（同一个码、相邻）因此仍相邻；
  `Candidate` 对象原样搬动，`ai_data_propose` 里按 `id(recipe)` 记的标记不会丢。
* 「为什么不推荐」的原因码是闭集 `WHY_NOT`，每个码引用的 G 编号必须在 `sciviz_guidance` 里存在且 basis 不是 `taste`。
  品味条目（G-042–G-051）不能当把某种画法排后的理由。理由分三档（数据事实 / 感知依据 / 用户确认过的目的），不出「最优」「置信度」。
  `answers_other_purpose` 只在目的是用户点的时候才给（没确认过的目的不能当理由，框架 §6）。

与 `ai_data_task`（提交 / 生成脚本的流程）无关：这里的「目的」是框架 §2 的任务词，叫 `purpose` 只为避免混名。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .ai_data_recipe import Candidate

#: 界面这一期只让用户在这四个里选；与框架 §2 / 推荐考卷（`recommend_cases.json` 的 `task`）同一套词
PURPOSES = ("compare_groups", "trend", "relationship", "distribution")

#: 备选所属的族 → 它回答的目的（决定「假设的目的」与「可选项」）
FAMILY_PURPOSE = {
    "comparison": "compare_groups",
    "trend": "trend",
    "relationship": "relationship",
    "distribution": "distribution",
}

_LINES = frozenset({"line_units", "line_points", "line_band", "line_err"})
_SCATTERS = frozenset({"scatter_fit", "scatter"})
_RAW_COMPARE = frozenset({"points_mean", "box_points", "violin_points", "points_err"})
_SHAPE = frozenset({"box_points", "violin_points"})

#: 目的 → 重排时排到前面的码（稳定分区；`distribution` 另有「箱线 / 小提琴在场才上提」的条件，见 `_front`）
_FRONT = {
    "compare_groups": _RAW_COMPARE,  # 框架 §4：原始点 + 汇总、箱线 + 点
    "trend": _LINES,  # G-018：折线只用于有序横轴上的跟踪
    "relationship": _SCATTERS,  # G-018 / 框架 §4：两个独立测量值用散点
    "distribution": _SHAPE,  # 框架 §4：箱线 + 点 / 小提琴 + 点把整个分布露出来
}

#: 「这个目的也回答」的码（不属于它们、又是用户点的目的 → `answers_other_purpose`）
_ANSWERS = {
    "compare_groups": _RAW_COMPARE | {"bar_error"},
    "trend": _LINES,
    "relationship": _SCATTERS,
    "distribution": _SHAPE
    | {"points_mean", "ecdf_groups", "hist", "ecdf", "box_points_single", "density"},
}

#: 原因码 → (引用的 G 编号, 理由档)。闭集；`tests/test_ai_data_purpose.py` 核每个 G 存在且 basis 不是 taste
WHY_NOT: dict[str, tuple[tuple[str, ...], str]] = {
    # 只画均值与误差，看不到每个点（G-012）——不引 G-050（那是品味条目）
    "summary_hides_spread": (("G-012",), "perceptual"),
    # 看关系：两个量之间没有先后顺序，折线暗示相邻值连续变化（G-018）
    "line_needs_order": (("G-018",), "perceptual"),
    # 看趋势：横轴有顺序，折线才用得上这个顺序；散点不用（G-018）
    "order_prefers_line": (("G-018",), "perceptual"),
    # 这张回答的是另一个问题——只有目的是用户点的才给（用户确认过的目的档，无 G）
    "answers_other_purpose": ((), "purpose"),
}


@dataclass(frozen=True)
class Resolved:
    """`resolve` 的结果：`code` 是实际生效的目的（用户点的、且这批备选里有它的族；否则是假设的），`source` 是 `user` / `data`，
    `options` 是界面可让用户选的目的（至少两个才给，否则空），`items` 是重排后的备选。"""

    code: str | None
    source: str
    options: tuple[str, ...]
    items: tuple[Candidate, ...]


def _purposes_of(items: Sequence[Candidate]) -> set[str]:
    return {FAMILY_PURPOSE[c.recipe.family] for c in items if c.recipe.family in FAMILY_PURPOSE}


def hypothesis(items: Sequence[Candidate]) -> str | None:
    """假设的目的：规则现在第一名所属的族。第一名不在这四个族里 → None（不给目的行）。"""
    if not items:
        return None
    return FAMILY_PURPOSE.get(items[0].recipe.family)


def options(items: Sequence[Candidate]) -> tuple[str, ...]:
    """可选的目的：只列「这批备选里至少有一张能回答」的，按 `PURPOSES` 的固定顺序；不足两个时界面不出这一行（返回空）。"""
    have = _purposes_of(items)
    out = tuple(p for p in PURPOSES if p in have)
    return out if len(out) >= 2 else ()


def _front(purpose: str, items: Sequence[Candidate]) -> frozenset[str]:
    front = _FRONT[purpose]
    if purpose == "distribution" and not any(c.code in _SHAPE for c in items):
        return (
            frozenset()
        )  # 箱线 / 小提琴被规则拿掉（组内点太少等）时不重排：没有更能露出分布的画法可提
    return front


def reorder(items: Sequence[Candidate], purpose: str) -> tuple[Candidate, ...]:
    """稳定分区：这个目的对应的那几个码排到前面，其余与每一类内部都保持原来的相对顺序。是 `items` 的一个排列。"""
    front = _front(purpose, items)
    return tuple([c for c in items if c.code in front] + [c for c in items if c.code not in front])


def resolve(items: Sequence[Candidate], requested: str | None) -> Resolved:
    """`requested` 是 `answers.purpose`（已按闭集校过 / 没给 = None）。用户点的目的要这批备选里有它的族才生效，否则当没点（仍报假设的）。"""
    items = tuple(items)
    hypo = hypothesis(items)
    opts = options(items)
    if requested in PURPOSES and requested in _purposes_of(items):
        return Resolved(requested, "user", opts, reorder(items, requested))
    return Resolved(hypo, "data", opts, items)


def why_not(code: str, purpose: str | None, source: str) -> dict | None:
    """非推荐的备选 `code` 相对当前目的的原因码；没有适用理由 → None（界面什么都不显示）。
    有 G 依据的三类不论目的是用户点的还是假设的都给；`answers_other_purpose` 只在 `source == "user"` 时给。"""
    if purpose is None:
        return None
    reason = None
    if code == "bar_error" and purpose in ("compare_groups", "distribution"):
        reason = "summary_hides_spread"
    elif code in _LINES and purpose == "relationship":
        reason = "line_needs_order"
    elif code in _SCATTERS and purpose == "trend":
        reason = "order_prefers_line"
    elif source == "user" and code not in _ANSWERS[purpose]:
        reason = "answers_other_purpose"
    if reason is None:
        return None
    guidance, tier = WHY_NOT[reason]
    return {"code": reason, "guidance": list(guidance), "tier": tier}


__all__ = [
    "FAMILY_PURPOSE",
    "PURPOSES",
    "Resolved",
    "WHY_NOT",
    "hypothesis",
    "options",
    "reorder",
    "resolve",
    "why_not",
]
