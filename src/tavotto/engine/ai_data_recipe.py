"""TAF 数据出图 v2 的「画法配方」层（ADR 0178）：配方数据结构、十二类科学问题（`FAMILIES`，唯一权威表）、资格判定、
排序护栏。纯标准库，Flask 父进程 import；**不执行**任何东西、不写任何文件、不渲染（模板是下一个 PR）。

边界（ADR 0138 §四、ADR 0178）：被执行的脚本出自 Tavotto 的模板，模型只交一份**闭集**的配方；表里的文字（列名、单元格）
只能作为「列名」被后端按 `FolderProfile` 逐项核对，不会成为代码。配方里没有任何自由文本会进脚本——`why` 是给用户看的
一句话，只显示、不进脚本。

一张表是唯一权威：

* `MARK_RULES`：每种图元的汇总要求、是否露出原始点、一句话含义；
* `REJECTIONS`：每个拒绝条件一个稳定码 + 一句中文（`data_figure_guidance()` 逐条渲染成准则，不另抄第二份）；
* `FAMILIES`：每类科学问题的列角色与类型要求、允许的图元、所属批次、专属拒绝码、「什么时候用 / 不用」。

判定分两层：**结构**（列名真实存在、类型对得上、图元组合合法、用户确认过的事实）只要 `FolderProfile`；**取值**
（每组 n、非负、重复 x、dose > 0……）需要带行数据的 `DataSummary`（`values=`，来自对选中的那张表 `inspect_data_source`）。
没给 `values` 就不判取值类规则、也不假装判过——`eligible()` 把它们列在 `Eligibility.unchecked`。
阈值全是下面具名常量。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import ai_data_figure as adf

# --------------------------------------------------------------------------- 阈值（全部具名）
#: 每个箱 / 小提琴至少要有多少个值，否则分位数不可信
MIN_N_BOX = 5
#: 直方图 / 核密度每组至少多少个值
MIN_N_HIST = 10
#: 拟合线每组至少多少个点
MIN_N_FIT = 6
#: 趋势至少要有几个不同的 x
MIN_X_TREND = 3
#: 分面最多几格
MAX_FACETS = 6
#: 颜色分组最多几组（再多颜色分不清）
MAX_GROUP_LEVELS = 8
#: 矩阵行 / 列上限
MAX_MATRIX_ROWS = 60
MAX_MATRIX_COLS = 40
#: 排名条目上限；构成的部分上限；逐个体轨迹的个体数上限
MAX_RANKING_ITEMS = 30
MAX_COMPOSITION_PARTS = 8
MAX_TRAJECTORIES = 30
#: 配方里的列数 / 层数 / 文字长度
MAX_Y = adf.MAX_COLUMNS  # 配方 y 列数的硬上限 = 摘要的列数上限；各族更严的上限在 FamilySpec.y_max
#: 用户 / 模型一次点名的纵轴列数上限（与宽表分支的「至多 3 个」同一个数）
MULTI_Y_MAX = 3
#: 配方里「按类别用的整数编码列」至多几列：分组 + 分面 + 类别横轴
MAX_NUMCAT = 3
#: 「把这几列当分组」（`FigureRecipe.stack`，宽表转长表）至多几列。这里是**类别个数**（每列一组，进颜色分组 / 类别横轴），不是同一根轴上的几条线，
#: 所以不沿用多列纵轴的 3：取颜色分组的上限 `MAX_GROUP_LEVELS`（8，再多颜色分不清）——这样每一种备选（含 ECDF 的颜色分组）都放得下，
#: 没有「某个备选因类别太多被剔掉」的参差；类别横轴本来的上限（12）更宽，不是瓶颈
STACK_MAX_COLUMNS = MAX_GROUP_LEVELS
#: 「把这几列当分组」每一列至少要有几个数值格（一组只有一个值谈不上互相比较）
STACK_MIN_N = 2
#: 转成长表之后的两列的名字（只在这条配方内部、对应的脚本里存在；表里真有同名的列也不冲突——长表只有这两列）。值列名带上共同的单位后缀（`value_mM`），
#: 轴标题因此仍是 `value (mM)`
STACK_VALUE = "value"
STACK_CAT = "group"
#: 数值列里夹着文字时，非数字的非空格子占比上限与数值格子的最少个数（`numeric_like`）
MAX_TEXT_SHARE = 0.2
MIN_NUMERIC_CELLS = 3
#: 夹着文字格的数值列的处理方式（闭集）与 `map` 规则的键个数上限。`describe`（用户用文字说明）不是可执行方式：
#: 要先由助手翻译成 `map`，直接带它的请求被拒绝（`propose_cells_need_rule`）
CELL_MODES = ("skip", "extract", "map")
CELL_REST = ("skip", "extract")
MAX_MAP_KEYS = 50
MAX_CELL_TEXT = 200
MIN_LAYERS = 1
MAX_LAYERS = 4
MAX_WHY = 80
MAX_NAME = 200
#: 给用户的备选个数
MIN_CANDIDATES = 2
MAX_CANDIDATES = 4

# --------------------------------------------------------------------------- 闭集：图元
SUMMARY_NONE = "none"
SUMMARIES = ("none", "mean_sd", "mean_sem", "mean_ci95", "median_iqr")
COORDS = ("cartesian", "polar")
ORDERS = ("as_given", "value_desc", "value_asc")
SCALES = ("linear", "log_y", "log_x", "log_xy")
#: 层的角色：main = 这一层是图的主体；overlay = 叠在主体上的汇总 / 拟合 / 误差；reference = 参考线
LAYER_ROLES = ("main", "overlay", "reference")
DESIGNS = ("independent", "paired", "repeated")
#: 「误差线来自表里这一列」时它是哪一种（`FigureRecipe.err_kind`，闭集；sciviz G-065：误差线必须写明 SD / SEM / 95% CI）。`unknown` = 名字只说「是误差」、
#: 没说是哪种——照画，但**不当成 SD**，也不当成已确认（界面把它列进「需要你确认」）
ERR_KINDS = ("sd", "sem", "ci95", "unknown")
#: `adf.error_kind` 的种类 → `ERR_KINDS`（`ci` 在名字上不分 95%，这里按列名认作 `ci95`；`unknown` 原样）
ERR_KIND_OF_NAME = {"sd": "sd", "sem": "sem", "ci": "ci95", "unknown": "unknown"}
#: 名字恰好是这些（不分大小写）、又有误差列「陪着」的数值列是样本量列：不当纵轴 / 横轴（`sample_size_columns`）
SAMPLE_SIZE_NAMES = frozenset({"n", "count"})
#: 带 `err`（误差线取自现成误差列）的配方只许这些图元，且都不带汇总：比较类只画点（一个类别一个点，抖动会让误差线对不上点），趋势类的点 / 线 / 阶梯线
ERR_MARKS = {"comparison": frozenset({"points"}), "trend": frozenset({"points", "line", "step"})}


@dataclass(frozen=True)
class MarkRule:
    """`summary`：`none` = 只画原始值、不许带汇总；`required` = 必须写明怎么汇总（误差线必须写明 SD / SEM / 95% CI）；
    `optional` = 可汇总可不汇总。`raw` = 把一个个原始观测露出来（排序护栏认的「含原始点」）。`meaning` 是一句中文，渲染进准则。"""

    summary: str
    raw: bool
    meaning: str


MARK_RULES: dict[str, MarkRule] = {
    "points": MarkRule("none", True, "原始点：每个数据点原样画出来，读者看得到每个观测。"),
    "jitter": MarkRule("none", True, "抖动点：同上，沿类别方向随机错开，避免点叠在一起。"),
    "line": MarkRule(
        "optional",
        False,
        "折线：横轴有序连续、且同一对象被连续跟踪时才连线；连线暗示两点之间的值可内插，所以无序类别（组 A / 组 B）绝不连线。"
        "同一 x 有重复测量时只画汇总线（均值或中位数）。",
    ),
    "step": MarkRule(
        "optional",
        False,
        "阶梯线：值在两次测量之间保持不变（计数、状态、档位），不暗示中间有渐变。",
    ),
    "band": MarkRule(
        "required", False, "误差带：围着汇总线的一圈范围，必须写明是 SD、SEM、95% CI 还是 IQR。"
    ),
    "errorbar": MarkRule(
        "required",
        False,
        "误差线：必须写明是 SD、SEM 还是 95% CI（`summary`）；不写明的误差线读者没法解读。",
    ),
    "bar": MarkRule(
        "optional",
        False,
        "柱：只用于从 0 起算才有意义的量（计数、总量、占比、均值本身有零点意义）；轴必须含 0，不许对数轴、不许截断；"
        "比较各组均值而每组值不多时，优先画原始点，柱会藏起分布。",
    ),
    "stacked_bar": MarkRule(
        "optional", False, "堆叠柱：只用于「部分之和是整体」的构成；各部分非负、合计为正。"
    ),
    "box": MarkRule("none", False, "箱线：显示中位数、四分位与离群，每个箱至少要有一定数量的值。"),
    "violin": MarkRule("none", False, "小提琴：显示分布的形状，每组值要足够多才有意义。"),
    "hist": MarkRule("none", False, "直方图：看单个量的分布形状，每组值要足够多。"),
    "ecdf": MarkRule("none", False, "经验累积分布：不分箱地看整个分布，适合比较两组分布。"),
    "density": MarkRule("none", False, "核密度曲线：平滑的分布形状，每组值要足够多。"),
    "fit_line": MarkRule("none", False, "拟合线：描述性的趋势线，只在点够多时画；图上不报显著性。"),
    "hline_summary": MarkRule(
        "required", False, "汇总水平线：一条表示均值或中位数的横线，必须写明汇总方式。"
    ),
    "heatmap": MarkRule("none", False, "热图：用颜色表示一个矩阵里的数值大小。"),
    "dot_matrix": MarkRule(
        "none", True, "点阵：每个数值一个点（大小或位置表示数值），不靠柱的面积。"
    ),
    "paired_lines": MarkRule(
        "none", True, "配对连线：同一对象的两次测量连成一条线，只用于已确认配对的数据。"
    ),
}
MARKS = tuple(MARK_RULES)
#: 排序护栏认的「原始点」类图元
RAW_MARKS = frozenset(m for m, r in MARK_RULES.items() if r.raw)
#: 汇总线型图元：`summary` 为 none 就是逐点连线（要确认个体才许）
POINTWISE_LINES = frozenset({"line", "step"})
BAR_MARKS = frozenset({"bar", "stacked_bar"})
BOX_MARKS = frozenset({"box", "violin"})
HIST_MARKS = frozenset({"hist", "density"})

# --------------------------------------------------------------------------- 拒绝条件表（稳定码 + 一句话）
#: 码 → 一句中文。`COMMON_REJECTIONS` 是跨族通用的（准则里放在「通用」一节），其余各属一个族（`FamilySpec.rejects`）。
REJECTIONS: dict[str, str] = {
    # 通用：配方与表对不上
    "table_not_found": "配方指的表（文件 / 工作表）不在这个文件夹的摘要里。",
    "column_not_found": "配方里的列名在这张表里不存在；列名要逐字照抄摘要。",
    "column_type_mismatch": "列的类型和它在这类图里的角色对不上（例如把文字列当数值轴）。",
    "roles_invalid": "这类图要的列角色（横轴、纵轴个数、分组、分面）没给对，或给了它不用的。",
    "no_usable_columns": "这张表里找不到这类图需要的列。",
    "mark_not_allowed": "这个图元不在这类图允许的范围里。",
    "no_primary_mark": "没有一层是这类图的主体画法。",
    "layer_role_invalid": "层的角色不对：至少要有一层 main，同一种图元不许在同一角色里重复叠。",
    "summary_missing": "误差线、误差带、汇总线必须写明怎么汇总（SD、SEM、95% CI 或 IQR），不能留空。",
    "summary_misplaced": "画原始值的图元（点、箱、直方图等）不能带汇总方式。",
    # 通用：用户确认过的事实
    "unit_unconfirmed": "个体编号列只有用户在对话里确认过才能用；列名像编号只是线索，不算确认。",
    "unit_required": "这种画法必须有用户确认过的个体编号列；先问用户这些数据是不是同一对象的重复测量。",
    "paired_unconfirmed": "「配对」只有用户确认过才成立；没确认就不许画配对连线。",
    # 通用：取值规则
    "box_violin_n_small": f"每组少于 {MIN_N_BOX} 个值时不画箱线和小提琴，改画原始点。",
    "hist_density_n_small": f"每组少于 {MIN_N_HIST} 个值时不画直方图和核密度，改画原始点或 ECDF。",
    "fit_line_n_small": f"每组点少于 {MIN_N_FIT} 个时不画拟合线。",
    "bar_baseline_invalid": "柱和堆叠柱要求数值不为负、坐标轴含 0，不许用对数轴。",
    "bar_needs_summary": "柱的每个位置只能对应一个值；一个位置有多行数据时要写明按均值还是中位数汇总。",
    "line_duplicate_x_unconfirmed": "同一个 x 有多个值、又没有确认的个体编号时，不许逐点连线，只许画汇总线加误差带。",
    "log_nonpositive": "对数轴上不能有 0 或负数。",
    "dual_axis_unsupported": "不支持双 y 轴：两个量纲不同的量要分面，各画各的坐标轴。",
    "facet_limit_exceeded": f"分面最多 {MAX_FACETS} 格。",
    "group_limit_exceeded": f"颜色分组最多 {MAX_GROUP_LEVELS} 组。",
    "coord_not_allowed": "极坐标只用于沿角度或方位的剖面；其它问题一律用直角坐标。",
    # 排序 / 备选
    "bar_before_raw_points": "能露出原始点时，只有柱加误差线的画法不许排在含原始点的画法前面。",
    "duplicate_candidate": "这个备选和前面的某个备选画法完全相同。",
    "too_many_candidates": f"备选最多给 {MAX_CANDIDATES} 个，多出来的被丢弃。",
    "summarised_not_raw": "这张表每个位置只有一行、还带着现成的误差列（已经汇总过）：不再对它的值算均值 ± 误差、箱线、分布或均值线（sciviz G-053：汇总值不是观测，不能再汇总一次）。",
    # 误差线取自现成的误差列（`FigureRecipe.err`）
    "error_values_negative": "误差列里有负数：误差是离散程度的大小，不能为负。",
    "error_cells_not_unique": "误差线取自现成的误差列时，每个位置（类别 × 分组 × 分面，趋势是分组 × 分面 × 横轴取值）只能有一行；有重复测量的表不画现成的误差列。",
    # 族专属
    "trend_too_few_x": f"不同的横轴取值少于 {MIN_X_TREND} 个时画不出趋势，改画分组比较。",
    "paired_incomplete": "配对要求横轴恰好两个条件、每个个体在两个条件下各一个值。",
    "matrix_too_large": f"矩阵最多 {MAX_MATRIX_ROWS} 行、{MAX_MATRIX_COLS} 列。",
    "composition_negative": "构成图的各部分不能为负。",
    "composition_total_not_positive": "构成图每个位置的各部分之和必须为正。",
    "composition_too_many_parts": f"构成图最多 {MAX_COMPOSITION_PARTS} 个部分。",
    "ranking_too_many_items": f"排名最多 {MAX_RANKING_ITEMS} 个条目。",
    "ranking_needs_order": "排名图必须按数值排序（value_desc 或 value_asc）。",
    "interval_bounds_invalid": "区间图要求每一行都是 下限 ≤ 估计值 ≤ 上限。",
    "trajectories_limit_exceeded": f"逐个体轨迹最多 {MAX_TRAJECTORIES} 个个体，多了改画汇总线加误差带。",
    "dose_nonpositive": "剂量反应要求剂量大于 0。",
    # 夹着文字格的数值列的处理规则（`FigureRecipe.cells`）
    "cell_rule_invalid": "这条处理规则不对：列不存在、不是这次画图用到的数值列、没有夹着文字、或同一列写了两条。",
    "cell_map_too_large": f"映射规则最多写 {MAX_MAP_KEYS} 项。",
    "cell_map_value_invalid": "映射到的值必须是有限的数，或写成缺失（null）。",
    "cell_map_key_unknown": "映射里写的原文在这一列的文字格里找不到（去掉首尾空白后要逐字相同）。",
    # 配方本身不合格（`from_dict`）
    "recipe_malformed": "配方的结构不对：不是对象，或字段类型不对。",
    "recipe_unknown_key": "配方里出现了不认识的字段。",
    "recipe_unknown_value": "配方里的画法、汇总、坐标、族等取值不在允许的范围内。",
    "recipe_layer_count": f"配方要有 {MIN_LAYERS} 到 {MAX_LAYERS} 层。",
    "recipe_why_invalid": f"给用户看的那句话要非空、不超过 {MAX_WHY} 字、不含换行。",
}
#: 拒绝码 → 属于哪个族；不在这里的就是通用。由 `FAMILIES[*].rejects` 反推，不另写一份。
_FAMILY_ONLY: dict[str, str] = {}

#: 这些原因的出路是「先问用户一个选择题」，不是换画法
ASK_FIRST = frozenset({"unit_required", "paired_unconfirmed", "unit_unconfirmed"})

#: 需要读行数据才能判的码（没给 `values` 时 `eligible()` 列在 `unchecked`）
VALUE_RULES = frozenset(
    {
        "box_violin_n_small",
        "hist_density_n_small",
        "fit_line_n_small",
        "bar_baseline_invalid",
        "bar_needs_summary",
        "line_duplicate_x_unconfirmed",
        "log_nonpositive",
        "trend_too_few_x",
        "paired_incomplete",
        "composition_negative",
        "composition_total_not_positive",
        "interval_bounds_invalid",
        "trajectories_limit_exceeded",
        "dose_nonpositive",
    }
)

#: `RecipeError` 与 `Problem` 的稳定码闭集（前端 `errors:backend.<code>` 有中英文文案，`tests/test_error_codes.py` 读这张表）
ERROR_CODES = tuple(REJECTIONS)


class RecipeError(ValueError):
    """配方读不成结构化对象。`code` 必须在 `ERROR_CODES` 里（构造时校验：新码不登记当场抛）。"""

    def __init__(self, code: str, detail: str = ""):
        if code not in ERROR_CODES:
            raise ValueError(f"未登记的 RecipeError 码：{code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Problem:
    """一条不合格：稳定码 + 它落在配方的哪个字段（`layers[1].mark`、`y`……）+ 一小段给模型看的细节。"""

    code: str
    field: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"未登记的 Problem 码：{self.code}")

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "detail": self.detail}


# --------------------------------------------------------------------------- 十二类科学问题（唯一权威表）
@dataclass(frozen=True)
class FamilySpec:
    """一类科学问题。列角色类型 token：`num` 数值列、`ord` 数值或日期列（有顺序）、`cat` 分组列（2–12 个取值、有重复，
    即 `DataSummary.groups`）、`label` 文字列（标签，取值个数不限）。`unit`：`no` 不用个体编号 / `optional` 用户确认过才可带 /
    `required` 必须有确认过的。`x_free_marks`：这些图元不许同时给横轴（分布按颜色分组，不按横轴类别）。"""

    key: str
    title: str
    batch: int
    when_use: str
    when_not: str
    x_types: tuple[str, ...]
    x_required: bool
    y_types: tuple[str, ...]
    y_min: int
    y_max: int
    marks: tuple[str, ...]
    primary: tuple[str, ...]
    group: bool = True
    facet: bool = True
    unit: str = "no"
    paired: bool = False
    polar: bool = False
    explicit_bounds: bool = False
    order_required: bool = False
    x_free_marks: tuple[str, ...] = ()
    rejects: tuple[str, ...] = ()


_RAW_PLUS = ("points", "jitter")

FAMILIES: dict[str, FamilySpec] = {
    f.key: f
    for f in (
        FamilySpec(
            "comparison",
            "比较各组",
            1,
            when_use="读者要比较几组某个量的高低。每组值不多时画原始点并叠均值 / 中位数及写明的误差线；"
            "组多值多时可用箱线或小提琴看分布。",
            when_not="组之间有顺序且读者想看变化趋势（用 trend）；数据其实是同一对象前后测（先问，确认后用 paired）；"
            "只想要「均值柱加误差线」而每组值不多时不用，原始点信息更多。",
            x_types=("cat",),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=1,
            marks=("points", "jitter", "box", "violin", "bar", "errorbar", "hline_summary"),
            primary=("points", "jitter", "box", "violin", "bar"),
            unit="optional",
            rejects=("bar_before_raw_points",),
        ),
        FamilySpec(
            "trend",
            "看变化趋势",
            1,
            when_use="横轴有序连续（时间、温度、浓度梯度）、同一对象被连续跟踪，读者要看量随它怎么变。"
            "同一 x 有重复测量时画汇总线加误差带，个体编号确认过才可逐个体连线。",
            when_not="横轴是无序类别（用 comparison，不连线）；只有 2 个横轴取值（连线夸大了「趋势」）；"
            "两个量纲不同的量要放同一张图时（分面，不做双 y 轴）。",
            x_types=("ord",),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=3,
            marks=("line", "step", "band", "points", "errorbar", "fit_line", "hline_summary"),
            primary=("line", "step", "points"),
            unit="optional",
            rejects=("trend_too_few_x",),
        ),
        FamilySpec(
            "relationship",
            "看两个量的关系",
            1,
            when_use="两个都是数值的量，读者要看一个随另一个怎么变、有没有关联（散点，可叠描述性拟合线；不报显著性）。",
            when_not="其中一个是类别（用 comparison）；横轴是时间且同一对象连续跟踪（用 trend）；点很少时不叠拟合线。",
            x_types=("num",),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=1,
            marks=("points", "fit_line"),
            primary=("points",),
        ),
        FamilySpec(
            "distribution",
            "看分布",
            1,
            when_use="读者要看一个量取值的分布：集中在哪、散多开、有没有离群或双峰。单组用直方图 / 密度，多组用 ECDF、箱线或小提琴叠原始点。",
            when_not="要比较的只是各组均值（用 comparison）；每组值太少时不画直方图 / 密度（画原始点）。直方图 / 密度 / ECDF 不用对数轴，也不与箱线、小提琴、原始点叠在同一张图里（值在横轴与纵轴不同）；ECDF 不与直方图 / 密度叠（纵轴含义不同）。",
            x_types=("cat",),
            x_required=False,
            y_types=("num",),
            y_min=1,
            y_max=1,
            marks=("hist", "ecdf", "density", "box", "violin", "jitter", "points"),
            primary=("hist", "ecdf", "density", "box", "violin"),
            x_free_marks=("hist", "ecdf", "density"),
        ),
        FamilySpec(
            "paired",
            "前后 / 配对比较",
            2,
            when_use="同一对象在两个条件下各测一次（给药前后、左右侧），读者要看每个对象各自的变化：用配对连线加原始点。",
            when_not="没有用户确认的配对关系（先问，不猜；列名像编号只是线索）；条件多于两个（用 trajectories）；各组不同对象（用 comparison）。",
            x_types=("cat",),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=1,
            marks=("paired_lines", "points", "box", "errorbar", "hline_summary"),
            primary=("paired_lines",),
            group=False,
            unit="required",
            paired=True,
            rejects=("paired_incomplete",),
        ),
        FamilySpec(
            "matrix",
            "矩阵（热图）",
            2,
            when_use="行 × 列的一张数值表（相关矩阵、混淆矩阵、基因 × 样本），读者要找高低模式：热图，格子少时可用点阵。",
            when_not=f"行列太多（超过 {MAX_MATRIX_ROWS} × {MAX_MATRIX_COLS}，先聚合）；表是长格式的分组数据（用 comparison / distribution）。",
            x_types=("label",),
            x_required=True,
            y_types=("num",),
            y_min=2,
            y_max=MAX_Y,
            marks=("heatmap", "dot_matrix"),
            primary=("heatmap", "dot_matrix"),
            group=False,
            facet=False,
            rejects=("matrix_too_large",),
        ),
        FamilySpec(
            "composition",
            "构成（占比）",
            2,
            when_use="整体由几个部分组成、部分之和有意义（比例、计数构成），读者要看各部分占多少：堆叠柱。",
            when_not=f"各部分可能为负或合计为零；部分多于 {MAX_COMPOSITION_PARTS} 个（合并小项）；读者要精确比较各部分（用 ranking，堆叠柱中间的段难比较）。",
            x_types=("cat", "label"),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=MAX_Y,
            marks=("stacked_bar", "bar"),
            primary=("stacked_bar", "bar"),
            rejects=(
                "composition_negative",
                "composition_total_not_positive",
                "composition_too_many_parts",
            ),
        ),
        FamilySpec(
            "ranking",
            "排名",
            2,
            when_use="一堆条目各有一个值（基因表达量、方法得分），读者要看谁高谁低：按数值排序的条形 / 点图。",
            when_not=f"条目超过 {MAX_RANKING_ITEMS} 个（只取前后若干）；值有重复测量而要比较分布（用 comparison）。",
            x_types=("label", "cat"),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=1,
            marks=("bar", "points", "errorbar", "hline_summary"),
            primary=("bar", "points"),
            order_required=True,
            rejects=("ranking_too_many_items", "ranking_needs_order"),
        ),
        FamilySpec(
            "interval",
            "区间估计",
            3,
            when_use="每个条目已有估计值和上下限（效应量及其区间、森林图）：点加区间线。区间的含义（CI、SD、范围）要问用户，写进图注。",
            when_not="只有原始数据、没有现成区间（用 comparison 自己汇总，写明 SD / SEM / 95% CI）；区间含义不明时不画。",
            x_types=("label", "cat", "ord"),
            x_required=True,
            y_types=("num",),
            y_min=3,
            y_max=3,
            marks=("errorbar", "points", "hline_summary"),
            primary=("errorbar",),
            group=False,
            facet=False,
            explicit_bounds=True,
            rejects=("interval_bounds_invalid",),
        ),
        FamilySpec(
            "trajectories",
            "逐个体的变化轨迹",
            3,
            when_use="同一批个体被反复测量（每只小鼠的体重曲线），读者要看每个个体各自怎么变：每个个体一条线，可叠汇总线加误差带。",
            when_not=f"没有用户确认的个体编号（先问）；个体多于 {MAX_TRAJECTORIES} 个（改画汇总线加误差带）；横轴无序。",
            x_types=("ord",),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=1,
            marks=("line", "band", "points", "hline_summary"),
            primary=("line",),
            unit="required",
            rejects=("trajectories_limit_exceeded",),
        ),
        FamilySpec(
            "dose_response",
            "剂量反应",
            3,
            when_use="剂量（浓度）由低到高、反应随之变化：横轴常用对数，点加误差线，可叠描述性拟合线。",
            when_not="剂量不是数值或含 0 / 负数（0 剂量对照单独成组，用 comparison）；点太少不叠拟合线。",
            x_types=("num",),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=1,
            marks=("points", "errorbar", "line", "fit_line", "band"),
            primary=("points",),
            rejects=("dose_nonpositive",),
        ),
        FamilySpec(
            "profile",
            "沿位置的剖面",
            3,
            when_use="一个量沿位置 / 深度 / 波长 / 角度连续分布（光谱、色谱、深度剖面、方向图），一列横轴加一到几列曲线；角度数据才可用极坐标。",
            when_not="横轴是无序类别；多条曲线量纲不同（分面，不做双 y 轴）；不是角度的数据不用极坐标。",
            x_types=("ord",),
            x_required=True,
            y_types=("num",),
            y_min=1,
            y_max=4,
            marks=("line", "step", "band", "points", "hline_summary"),
            primary=("line", "step", "points"),
            polar=True,
        ),
    )
}
FAMILY_KEYS = tuple(FAMILIES)

#: 现在真有模板、能出图的族。**唯一出处在这里**（本模块是叶子，模板模块 import 本模块，不能反过来——import 环门禁）；
#: `ai_data_template` 在 import 时核它的 `RENDERERS` 与这张表逐项相同，多写少写当场抛。
#: 批次 2 / 3 与批次 1 里还没写模板的族有完整的资格判定，但不在表里，模板逐族打开。
RENDERABLE: tuple[str, ...] = ("comparison", "trend", "relationship", "distribution")


def renderable(family: str) -> bool:
    """这类图现在有模板能画吗（`RENDERABLE`）。"""
    if family not in FAMILIES:
        raise ValueError(f"未知的族：{family}")
    return family in RENDERABLE


for _f in FAMILIES.values():
    for _c in _f.rejects:
        if _c not in REJECTIONS or _c in _FAMILY_ONLY:
            raise ValueError(f"族 {_f.key} 的拒绝码 {_c} 未登记或被两个族共用")
        _FAMILY_ONLY[_c] = _f.key
COMMON_REJECTIONS = tuple(
    c for c in REJECTIONS if c not in _FAMILY_ONLY and not c.startswith("recipe_")
)
RECIPE_REJECTIONS = tuple(c for c in REJECTIONS if c.startswith("recipe_"))


# --------------------------------------------------------------------------- 配方
@dataclass(frozen=True)
class Layer:
    mark: str
    summary: str = SUMMARY_NONE
    role: str = "main"

    def to_dict(self) -> dict:
        return {"mark": self.mark, "summary": self.summary, "role": self.role}


_RECIPE_KEYS = (
    "table",
    "sheet",
    "family",
    "layers",
    "coord",
    "x",
    "y",
    "group",
    "facet",
    "unit",
    "paired",
    "order",
    "scale",
    "why",
    "cells",
    "numcat",
    "stack",
    "err",
    "err_kind",
)
_CELL_KEYS = ("column", "mode", "map", "rest")
_RECIPE_REQUIRED = ("table", "family", "layers", "y", "why")
_LAYER_KEYS = ("mark", "summary", "role")


def _name(value, what: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_NAME
        or _has_control(value)
    ):
        raise RecipeError("recipe_malformed", what)
    return value


def _names(value, what: str, limit: int = MAX_NUMCAT) -> tuple[str, ...]:
    """列名数组（`numcat` / `stack`）：必须是 0–`limit` 个列名的数组，否则 `recipe_malformed`。"""
    if not isinstance(value, list) or len(value) > limit:
        raise RecipeError("recipe_malformed", what)
    return tuple(_name(v, what) for v in value)


def _has_control(s: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in s)


def _choice(value, allowed: tuple[str, ...], what: str) -> str:
    if not isinstance(value, str):
        raise RecipeError("recipe_malformed", what)
    if value not in allowed:
        raise RecipeError("recipe_unknown_value", what)
    return value


def _cells_from(raw) -> tuple[CellRule, ...]:
    if not isinstance(raw, list) or len(raw) > MAX_Y:
        raise RecipeError("recipe_malformed", "cells")
    return tuple(CellRule.from_dict(c) for c in raw)


@dataclass(frozen=True)
class CellRule:
    """一列数值里夹着的文字格怎么处理（声明式，不含代码）。`mode`：`skip` 当缺失 / `extract` 取第一个数 / `map` 原文→数或缺失
    （`mapping` 是 `((原文, 数 | None), ...)`，`rest` 是不在映射里的按 `skip` 还是 `extract`）。用户的数据文件永不改写——规则只在读表时对内存里的副本生效
    （`ai_data_figure.apply_cells`，生成的脚本与父进程同一份源码）。"""

    column: str
    mode: str
    mapping: tuple[tuple[str, float | None], ...] = ()
    rest: str = "skip"

    def args(self) -> tuple:
        """`apply_cells` 要的 `(列, 方式, 映射字典, 其余)`。"""
        return (self.column, self.mode, dict(self.mapping), self.rest)

    def to_dict(self) -> dict:
        out: dict = {"column": self.column, "mode": self.mode}
        if self.mode == "map":
            out["map"] = {k: v for k, v in self.mapping}
            out["rest"] = self.rest
        return out

    @classmethod
    def from_dict(cls, data) -> CellRule:
        if not isinstance(data, dict):
            raise RecipeError("recipe_malformed", "cell rule")
        bad = [k for k in data if k not in _CELL_KEYS]
        if bad:
            raise RecipeError("recipe_unknown_key", "cells." + ",".join(sorted(map(str, bad)))[:60])
        if "column" not in data or "mode" not in data:
            raise RecipeError("recipe_malformed", "cell rule")
        mode = _choice(data["mode"], CELL_MODES, "mode")
        rest = _choice(data.get("rest", "skip"), CELL_REST, "rest")
        raw = data.get("map", {})
        if not isinstance(raw, dict) or (mode != "map" and raw):
            raise RecipeError("recipe_malformed", "cells.map")
        pairs = []
        for k, v in raw.items():
            if (
                not isinstance(k, str)
                or len(k) > MAX_CELL_TEXT
                or isinstance(v, bool)
                or not (v is None or isinstance(v, (int, float)))
            ):
                raise RecipeError("recipe_malformed", "cells.map")
            pairs.append((k, None if v is None else float(v)))
        return cls(_name(data["column"], "cells.column") or "", mode, tuple(pairs), rest)


@dataclass(frozen=True)
class FigureRecipe:
    """模型唯一能交的东西：一份闭集配方。`why` 是给用户看的一句话，只显示，不进脚本。"""

    table: str
    family: str
    layers: tuple[Layer, ...]
    y: tuple[str, ...]
    why: str
    sheet: str | None = None
    coord: str = "cartesian"
    x: str | None = None
    group: str | None = None
    facet: str | None = None
    unit: str | None = None
    paired: bool = False
    order: str = "as_given"
    scale: str = "linear"
    cells: tuple[CellRule, ...] = ()
    #: 「这一列是整数编码、按类别用」：被点名当分组 / 分面 / 类别横轴的数值列（`DataSummary.numcat` 里的列）。配方里这一条事实贯穿
    #: 校验（`validate_recipe` 把它们当类别列）→ 脚本（读表后 `apply_numcat` 规范成整数写法、类别按数值升序）→ 落盘再校验 → 指纹核对。
    #: 空 = 与从前逐字相同；规则（没有人点名）永远不写它。
    numcat: tuple[str, ...] = ()
    #: 「把这几列当分组」：被点名的 2–`STACK_MAX_COLUMNS` 个同一种量的数值列（宽表：每列一个条件 / 一次重复）在逻辑上转成长表——一列值（`stack_names` 的第一个）、
    #: 一列类别（第二个，取值 = 原来的列名原文、顺序 = `stack` 里的顺序）；`y` 是那个值列、类别列做 `x`（比较类）或 `group`（分布类）。这一条事实贯穿
    #: 校验（`validate_recipe` 按转换后的长表看）→ 脚本（读表后 `apply_stack`）→ 落盘再校验 → 指纹核对。空 = 与从前逐字相同；规则永远不写它。
    stack: tuple[str, ...] = ()
    #: 「误差线取自表里这一列」：被点名的现成误差列（数值列，名字在说「我是误差量」或用户点了它；每个位置只有一行，已汇总的表）。这一条事实贯穿
    #: 校验（`validate_recipe`：列要存在、是数值列、不是别的角色、画的行里非负、每个位置一行）→ 脚本（读表后按同一份行过滤取值，`ax.errorbar(..., yerr=列的值)`）→
    #: 来源注释。空 = 与从前逐字相同；规则只在 `paired_error` 配上了一列、且表是「每个位置一行」时才写它，模型 / 用户点名不写。
    err: str | None = None
    #: 上一列是哪一种误差（`ERR_KINDS`）：名字说了就是名字说的（`sd` / `sem` / `ci` → `ci95`），名字只说「误差」记 `unknown`（**绝不当成 SD**），
    #: 用户点选的（`answers.error`）覆盖名字。`err` 给了就必须有它（G-065：误差线必须写明是哪一种）。
    err_kind: str | None = None

    @classmethod
    def from_dict(cls, data) -> FigureRecipe:
        """严格：不是对象 / 未知键 / 缺必填键 / 类型不对 → `recipe_malformed`、`recipe_unknown_key`；取值不在闭集 →
        `recipe_unknown_value`；层数不是 1–4 → `recipe_layer_count`；`why` 不合格 → `recipe_why_invalid`。"""
        if not isinstance(data, dict):
            raise RecipeError("recipe_malformed", "not an object")
        unknown = [k for k in data if k not in _RECIPE_KEYS]
        if unknown:
            raise RecipeError("recipe_unknown_key", ",".join(sorted(map(str, unknown)))[:80])
        missing = [k for k in _RECIPE_REQUIRED if k not in data]
        if missing:
            raise RecipeError("recipe_malformed", "missing " + ",".join(missing))
        raw_layers = data["layers"]
        if not isinstance(raw_layers, list):
            raise RecipeError("recipe_malformed", "layers")
        if not MIN_LAYERS <= len(raw_layers) <= MAX_LAYERS:
            raise RecipeError("recipe_layer_count", str(len(raw_layers)))
        layers = []
        for item in raw_layers:
            if not isinstance(item, dict):
                raise RecipeError("recipe_malformed", "layer")
            bad = [k for k in item if k not in _LAYER_KEYS]
            if bad:
                raise RecipeError(
                    "recipe_unknown_key", "layer." + ",".join(sorted(map(str, bad)))[:60]
                )
            if "mark" not in item:
                raise RecipeError("recipe_malformed", "layer.mark")
            layers.append(
                Layer(
                    _choice(item["mark"], MARKS, "mark"),
                    _choice(item.get("summary", SUMMARY_NONE), SUMMARIES, "summary"),
                    _choice(item.get("role", "main"), LAYER_ROLES, "role"),
                )
            )
        ys = data["y"]
        if not isinstance(ys, list) or not 1 <= len(ys) <= MAX_Y:
            raise RecipeError("recipe_malformed", "y")
        why = data["why"]
        if not isinstance(why, str):
            raise RecipeError("recipe_malformed", "why")
        if not why.strip() or len(why) > MAX_WHY or _has_control(why):
            raise RecipeError("recipe_why_invalid")
        paired = data.get("paired", False)
        if not isinstance(paired, bool):
            raise RecipeError("recipe_malformed", "paired")
        sheet = data.get("sheet")
        return cls(
            table=_name(data["table"], "table"),
            family=_choice(data["family"], FAMILY_KEYS, "family"),
            layers=tuple(layers),
            y=tuple(_name(c, "y") for c in ys),
            why=why,
            sheet=_name(sheet, "sheet", optional=True),
            coord=_choice(data.get("coord", "cartesian"), COORDS, "coord"),
            x=_name(data.get("x"), "x", optional=True),
            group=_name(data.get("group"), "group", optional=True),
            facet=_name(data.get("facet"), "facet", optional=True),
            unit=_name(data.get("unit"), "unit", optional=True),
            paired=paired,
            order=_choice(data.get("order", "as_given"), ORDERS, "order"),
            scale=_choice(data.get("scale", "linear"), SCALES, "scale"),
            cells=_cells_from(data.get("cells", [])),
            numcat=_names(data.get("numcat", []), "numcat"),
            stack=_names(data.get("stack", []), "stack", STACK_MAX_COLUMNS),
            err=_name(data.get("err"), "err", optional=True),
            err_kind=(
                None
                if data.get("err_kind") is None
                else _choice(data["err_kind"], ERR_KINDS, "err_kind")
            ),
        )

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "sheet": self.sheet,
            "family": self.family,
            "layers": [layer.to_dict() for layer in self.layers],
            "coord": self.coord,
            "x": self.x,
            "y": list(self.y),
            "group": self.group,
            "facet": self.facet,
            "unit": self.unit,
            "paired": self.paired,
            "order": self.order,
            "scale": self.scale,
            "why": self.why,
            **({"cells": [c.to_dict() for c in self.cells]} if self.cells else {}),
            **({"numcat": list(self.numcat)} if self.numcat else {}),
            **({"stack": list(self.stack)} if self.stack else {}),
            **({"err": self.err} if self.err is not None else {}),
            **({"err_kind": self.err_kind} if self.err_kind is not None else {}),
        }


@dataclass(frozen=True)
class Confirmed:
    """用户在对话里点选确认过的事实。`design`：独立 / 配对 / 重复测量；`unit_col`：个体编号列。**只有这里有的才算数**——
    `DataSummary.id_like` / `repeats` 只是线索，绝不当成已确认。"""

    design: str | None = None
    unit_col: str | None = None

    def __post_init__(self) -> None:
        if self.design is not None and self.design not in DESIGNS:
            raise ValueError(f"未知的设计：{self.design}")


# --------------------------------------------------------------------------- 列类型
def _find(profile: adf.FolderProfile, table: str, sheet: str | None) -> adf.TableEntry | None:
    for t in profile.tables:
        if t.path == table and t.sheet == sheet:
            return t
    return None


def numeric_like(c: adf.ColumnSummary) -> bool:
    """这列能当数值列：全是数，或「数里夹着少量文字」——非数字的非空格子占比不超过 `MAX_TEXT_SHARE`、数值格子至少
    `MIN_NUMERIC_CELLS` 个（`<0.1`、`n/a` 这类检出限 / 缺失标记）。夹着的文字格那一行不画并逐行报告（`unplotted_rows`），
    不让用户先去整理数据；超过阈值的列不当数值列（多半是文字列）。日期列不算（`kind` 是 `date`）。"""
    if c.kind == "numeric":
        return True
    if c.kind != "mixed":
        return False
    return c.numeric >= MIN_NUMERIC_CELLS and c.text_count <= MAX_TEXT_SHARE * (
        c.numeric + c.text_count
    )


def _type_ok(token: str, s: adf.DataSummary, col: str, mixed_ok: bool = False) -> bool:
    """`mixed_ok`：数值列里夹着少量文字（`numeric_like`）也算数值——结构资格（`eligible`）与「用户已给了处理规则」的配方校验才开；
    没有规则的配方里这样的列仍是 `column_type_mismatch`（不静默只画数值行）。"""
    kind = s.per_column[col].kind
    if token == "num":
        return kind == "numeric" or (mixed_ok and numeric_like(s.per_column[col]))
    if token == "ord":
        # 有序横轴：数值 / 日期；数值列里夹着少量文字（与 y 列同一判据 `numeric_like`）在有处理规则 / 结构资格时也算
        return kind in ("numeric", "date") or (mixed_ok and numeric_like(s.per_column[col]))
    if token == "cat":
        return col in s.groups
    if token == "label":
        return kind == "text"
    raise ValueError(token)


def _fits(tokens: tuple[str, ...], s: adf.DataSummary, col: str, mixed_ok: bool = False) -> bool:
    return any(_type_ok(t, s, col, mixed_ok) for t in tokens)


def _unit_of(col: str) -> str | None:
    """列名后缀里的单位（只认 `ai_data_figure.UNITS` 闭集）；认不出 → None。"""
    m = adf._UNIT_SUFFIX.match(col)
    return adf.UNITS.get(m.group("unit")) if m else None


def multi_y_ok(s: adf.DataSummary, cols: list[str]) -> bool:
    """用户 / 模型点名的**多列纵轴**能不能用（`_resolve_roles` 与 `ai_data_roles.suggest` 共用这一处）：2 到 `MULTI_Y_MAX` 列、互不相同、
    都在表里、都是数值列（`numeric_like`）、没有 `id_like` 列、列名后缀里认得出的单位至多一种（不同单位 = 双轴，`dual_axis_unsupported`）。"""
    if not 2 <= len(cols) <= MULTI_Y_MAX or len(set(cols)) != len(cols):
        return False
    if any(
        c not in s.per_column or c in s.id_like or not numeric_like(s.per_column[c]) for c in cols
    ):
        return False
    return len({u for u in (_unit_of(c) for c in cols) if u}) <= 1


def id_group_ok(s: adf.DataSummary, col: str) -> bool:
    """名字 / 结构像编号（`id_like`）的列，能不能被点名当分组：它得是分组列（`s.groups`：文字、2–12 个取值、有取值重复出现——
    每行都不同的是标签不是分组）。数字编号（批次 1 2 3）不在 `s.groups`，不算。"""
    return col in s.id_like and col in s.groups


def numcat_pick_ok(s: adf.DataSummary, col: str) -> bool:
    """用户 / 模型点名的列能不能当「整数编码的分组」（`_resolve_roles` 与 `ai_data_roles` 共用这一处）：它得在 `DataSummary.numcat` 里
    （客观条件的唯一实现 `adf.numcat_levels`：非空值全是整数、2–`NUMCAT_MAX_LEVELS` 个取值、有取值重复、不是编号样的列）。"""
    return col in s.numcat and col not in s.groups


def numcat_view(s: adf.DataSummary, cols) -> adf.DataSummary:
    """把这些整数编码列当分组列看的摘要（`groups` 里加上它们的取值个数）：配方校验 / 资格判定里所有「是不是类别列」「有几个取值」的判据
    都读 `groups`，这样不必每处都分叉。`cols` 为空原样返回。"""
    extra = {c: s.numcat[c] for c in cols if c in s.numcat and c not in s.groups}
    return replace(s, groups={**s.groups, **extra}) if extra else s


def stack_names(cols) -> tuple[str, str]:
    """转成长表之后的 `(值列名, 类别列名)`：值列名 = `STACK_VALUE`，各列名后缀里的单位认得出且都一样就接在后面（`value_mM`，轴标题 `value (mM)`）。"""
    keys = []
    for c in cols:
        m = adf._UNIT_SUFFIX.match(c)
        keys.append(m.group("unit") if m and m.group("unit") in adf.UNITS else None)
    unit = keys[0] if keys and keys[0] and len(set(keys)) == 1 else None
    return (f"{STACK_VALUE}_{unit}" if unit else STACK_VALUE), STACK_CAT


def stack_ok(s: adf.DataSummary, cols) -> bool:
    """用户 / 模型点名的这几列能不能「当分组」（**客观条件的唯一实现**：`_resolve_roles`、`ai_data_roles`、`validate_recipe` 共用）：2 到 `STACK_MAX_COLUMNS` 列、
    互不相同、都在表里、**都是纯数值列**（`kind == "numeric"`：夹着文字的列要先有处理规则，这里不支持——干净地回到规则结果）、不是 `id_like` 列、每列至少
    `STACK_MIN_N` 个数值格、列名后缀里认得出的单位至多一种（不同量纲不放在同一根值轴上）；小表（大表没有行数据、缩减只认趋势 / 散点）、非矩阵。"""
    if not isinstance(cols, (list, tuple)) or not 2 <= len(cols) <= STACK_MAX_COLUMNS:
        return False
    if len(set(cols)) != len(cols) or s.large is not None or s.layout == "matrix":
        return False
    for c in cols:
        col = s.per_column.get(c) if isinstance(c, str) else None
        if col is None or c in s.id_like or col.kind != "numeric" or col.numeric < STACK_MIN_N:
            return False
    return len({u for u in (_unit_of(c) for c in cols) if u}) <= 1


def stack_view(s: adf.DataSummary, cols) -> adf.DataSummary:
    """把 `cols` 当分组之后的长表摘要：两列 `[值, 类别]`（`stack_names`），类别是一个分组列（取值个数 = 列数）、值列是数值列；`s` 带行数据就同时转出行
    （`adf.apply_stack`，脚本读表后调用的同一个函数；按列逐列、类别顺序 = `cols` 顺序）。校验 / 资格判定里所有「是不是类别列」「每组 n」的判据都读这张表。
    调用方先过 `stack_ok`。"""
    vname, cname = stack_names(cols)
    n = sum(s.per_column[c].numeric for c in cols)
    per = {
        vname: adf.ColumnSummary(vname, numeric=n),
        cname: adf.ColumnSummary(cname, text_count=n, text=list(cols)[: adf.TEXT_SAMPLES]),
    }
    rows: list[list[str]] = []
    if s.rows:
        _header, rows = adf.apply_stack(s.columns, s.rows, list(cols), vname, cname)
    return replace(
        s,
        columns=[vname, cname],
        rows=rows,
        per_column=per,
        layout="long",
        groups={cname: len(cols)},
        numcat={},
        id_like=[],
        repeats=None,
        row_count=None if s.rows else n,
        row_numbers=[],
        row_sources=[],
        units_row=s.units_row,
    )


def _stack_problems(recipe: FigureRecipe, s: adf.DataSummary) -> list[Problem]:
    """`recipe.stack` 的结构校验（`s` 是原表摘要）：族、与别的事实互斥、每一列客观合格、`y` / 类别角色与转换后的长表对得上。有问题就不再往下判（长表不存在）。"""
    P: list[Problem] = []
    if recipe.family not in ("comparison", "distribution"):
        P.append(Problem("roles_invalid", "stack", "family"))
    if s.large is not None:  # 大表没有行数据：转换没有流式实现
        P.append(Problem("mark_not_allowed", "stack", "large"))
        return P
    if recipe.cells or recipe.numcat or recipe.unit is not None or recipe.paired:
        P.append(Problem("roles_invalid", "stack", "exclusive"))
    for i, c in enumerate(recipe.stack):
        if c not in s.per_column:
            P.append(Problem("column_not_found", f"stack[{i}]"))
        elif (
            c in recipe.stack[:i]
            or c in s.id_like
            or s.per_column[c].kind != "numeric"
            or s.per_column[c].numeric < STACK_MIN_N
        ):
            P.append(Problem("column_type_mismatch", f"stack[{i}]", "num"))
    if len(recipe.stack) < 2:
        P.append(Problem("roles_invalid", "stack", "2+"))
    if P:
        return P
    if len({u for u in (_unit_of(c) for c in recipe.stack) if u}) > 1:
        return [Problem("dual_axis_unsupported", "stack")]
    vname, cname = stack_names(recipe.stack)
    if recipe.y != (vname,):
        P.append(Problem("roles_invalid", "y", "stack"))
    if cname not in (
        recipe.x,
        recipe.group,
        recipe.facet,
    ):  # 类别列得真的参与作图，否则几列被悄悄并成一堆
        P.append(Problem("roles_invalid", "stack", "category"))
    return P


def _err_problems(recipe: FigureRecipe, spec: FamilySpec, s: adf.DataSummary) -> list[Problem]:
    """`recipe.err` / `recipe.err_kind` 的结构校验：只有比较 / 趋势类、恰好一个 `y`；`err` 是另一列（不是 x / y / 分组 / 分面 / 个体）、是纯数值列；
    不与个体编号 / 配对 / 把列当分组并用；`err_kind` 在闭集里且与 `err` 同有同无（G-065：误差线必须写明是哪一种）；
    图元只许 `ERR_MARKS` 里的、不带汇总（带汇总 = 对已汇总的值再汇总一次，G-053）。"""
    P: list[Problem] = []
    if recipe.err is None:
        return [Problem("roles_invalid", "err_kind", "no_err")]
    if recipe.err_kind is None:
        P.append(Problem("summary_missing", "err_kind"))
    if spec.key not in ERR_MARKS:
        P.append(Problem("roles_invalid", "err", "family"))
    if len(recipe.y) != 1:
        P.append(Problem("roles_invalid", "err", "y"))
    if recipe.err in (recipe.x, recipe.group, recipe.facet, recipe.unit, *recipe.y):
        P.append(Problem("roles_invalid", "err", "role"))
    if recipe.unit is not None or recipe.paired or recipe.stack:
        P.append(Problem("roles_invalid", "err", "exclusive"))
    if recipe.err not in s.per_column:
        P.append(Problem("column_not_found", "err"))
    elif s.per_column[recipe.err].kind != "numeric":
        P.append(Problem("column_type_mismatch", "err", "num"))
    allowed = ERR_MARKS.get(spec.key, frozenset())
    for i, layer in enumerate(recipe.layers):
        if layer.mark not in allowed or layer.summary != SUMMARY_NONE:
            P.append(Problem("mark_not_allowed", f"layers[{i}].mark", f"err:{layer.mark}"))
    return P


# --------------------------------------------------------------------------- 取值（需要带行数据的 DataSummary）
class _Values:
    def __init__(self, s: adf.DataSummary, rows: list | None = None):
        self.s = s
        #: 参与判定的行：缺省是整张表；`drawn_rows` 给出的是「这份配方真会画的行」
        self.rows = s.rows if rows is None else rows
        self.idx = {c: i for i, c in enumerate(s.columns)}
        self._cache: dict[str, list] = {}

    def cells(self, col: str) -> list[str]:
        return [r[self.idx[col]].strip() for r in self.rows]

    def nums(self, col: str) -> list[float | None]:
        """数值（日期折成天数）；缺失与非数 = None。"""
        if col not in self._cache:
            out: list[float | None] = []
            for cell in self.cells(col):
                cls, v = adf.parse_cell(cell)
                if cls == "numeric":
                    out.append(v)
                else:
                    d = adf.parse_date(cell) if cls == "text" else None
                    out.append(adf.date_number(d) if d is not None else None)
            self._cache[col] = out
        return self._cache[col]

    def keys(self, cols: list[str | None]) -> list[tuple]:
        cs = [self.cells(c) for c in cols if c]
        return list(zip(*cs)) if cs else [()] * len(self.rows)


def drawn_rows(r: FigureRecipe, s: adf.DataSummary) -> list:
    """这份配方真会画的行：类别（比较类的横轴）/ 分组 / 分面 / 个体（趋势）那一格是空的行，脚本不画（归不到任何一组里，
    `ai_data_propose.unplotted_rows` 会如实计数）。取值规则只看这些行——否则一格空的分组会被当成「只有一个点的一组」，
    把整张趋势图 / 箱线图判成画不了。没有这样的行时原样返回 `s.rows`。"""
    idx = {c: i for i, c in enumerate(s.columns)}
    cols = [r.x if r.family == "comparison" else None, r.group, r.facet]
    if r.family == "trend":
        cols.append(r.unit)
    at = [idx[c] for c in cols if c and c in idx]
    if not at:
        return s.rows
    kept = [row for row in s.rows if all(row[i].strip() for i in at)]
    return s.rows if len(kept) == len(s.rows) else kept


def _cat(s: adf.DataSummary, col: str | None) -> str | None:
    """作为类别维度参与分格的列：`cat` 列才算（数值横轴不分格）。"""
    return col if col is not None and col in s.groups else None


def _count_by(v: _Values, keys: list[tuple], col: str) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for k, y in zip(keys, v.nums(col)):
        if y is not None:
            out[k] = out.get(k, 0) + 1
    return out


def _value_problems(
    r: FigureRecipe, spec: FamilySpec, s: adf.DataSummary, v: _Values, confirmed_unit: bool
) -> list[Problem]:
    P: list[Problem] = []
    marks = {layer.mark for layer in r.layers}
    y0 = r.y[0]
    xcat = _cat(s, r.x)
    cell_keys = v.keys([xcat, r.group, r.facet])
    finite_y = [y for c in r.y for y in v.nums(c) if y is not None]

    if marks & BOX_MARKS:
        n = _count_by(v, cell_keys, y0)
        if n and min(n.values()) < MIN_N_BOX:
            P.append(Problem("box_violin_n_small", "layers", f"min_n={min(n.values())}"))
    if marks & HIST_MARKS:
        n = _count_by(v, v.keys([r.group, r.facet]), y0)
        if n and min(n.values()) < MIN_N_HIST:
            P.append(Problem("hist_density_n_small", "layers", f"min_n={min(n.values())}"))
    if "fit_line" in marks and r.x is not None:
        xs = v.nums(r.x)
        gk = v.keys([r.group, r.facet])
        n: dict[tuple, int] = {}
        for k, xv, yv in zip(gk, xs, v.nums(y0)):
            if xv is not None and yv is not None:
                n[k] = n.get(k, 0) + 1
        if n and min(n.values()) < MIN_N_FIT:
            P.append(Problem("fit_line_n_small", "layers", f"min_n={min(n.values())}"))
    if marks & BAR_MARKS:
        if r.scale in ("log_y", "log_xy") or any(y < 0 for y in finite_y):
            P.append(Problem("bar_baseline_invalid", "layers"))
    if any(layer.mark in BAR_MARKS and layer.summary == SUMMARY_NONE for layer in r.layers):
        seen: set[tuple] = set()
        dup = False
        for k, yv in zip(v.keys([r.x, r.group, r.facet]), v.nums(y0)):
            if yv is None:
                continue
            if k in seen:
                dup = True
                break
            seen.add(k)
        if dup:
            P.append(Problem("bar_needs_summary", "layers"))
    if r.x is not None and any(
        layer.mark in POINTWISE_LINES and layer.summary == SUMMARY_NONE for layer in r.layers
    ):
        if not (r.unit and confirmed_unit):
            xs = v.nums(r.x)
            gk = v.keys([r.group, r.facet])
            for c in r.y:
                seen2: set[tuple] = set()
                dup2 = False
                for k, xv, yv in zip(gk, xs, v.nums(c)):
                    if xv is None or yv is None:
                        continue
                    if (k, xv) in seen2:
                        dup2 = True
                        break
                    seen2.add((k, xv))
                if dup2:
                    P.append(Problem("line_duplicate_x_unconfirmed", "layers", c))
                    break
    if r.scale in ("log_y", "log_xy") and any(y <= 0 for y in finite_y):
        P.append(Problem("log_nonpositive", "scale", "y"))
    if (
        r.scale in ("log_x", "log_xy")
        and r.x is not None
        and any(x <= 0 for x in v.nums(r.x) if x is not None)
    ):
        P.append(Problem("log_nonpositive", "scale", "x"))
    if spec.key == "trend" and r.x is not None:
        gk = v.keys([r.group, r.facet])
        xs_by: dict[tuple, set[float]] = {}
        for k, xv, yv in zip(gk, v.nums(r.x), v.nums(y0)):
            if xv is not None and yv is not None:
                xs_by.setdefault(k, set()).add(xv)
        if trend_too_few_x(len(x) for x in xs_by.values()):
            P.append(Problem("trend_too_few_x", "x"))
    if spec.key == "paired" and r.x is not None and r.unit is not None:
        # 配对键含分面：每个分面格内逐个体判（同一个体在别的分面里的测量不算它的重复）
        per_unit: dict[tuple, dict[str, int]] = {}
        levels: set[str] = set()
        facet_cells = v.keys([r.facet])
        for f, u, lv, yv in zip(facet_cells, v.cells(r.unit), v.cells(r.x), v.nums(y0)):
            if yv is None:
                continue
            levels.add(lv)
            d = per_unit.setdefault((f, u), {})
            d[lv] = d.get(lv, 0) + 1
        ok = len(levels) == 2 and all(
            set(d) == levels and all(n == 1 for n in d.values()) for d in per_unit.values()
        )
        if not ok or not per_unit:
            P.append(Problem("paired_incomplete", "unit"))
    if spec.key == "composition":
        if any(y < 0 for y in finite_y):
            P.append(Problem("composition_negative", "y"))
        totals: dict[tuple, float] = {}
        if len(r.y) > 1:
            for i in range(len(v.s.rows)):
                totals[(i,)] = sum((v.nums(c)[i] or 0.0) for c in r.y)
        else:
            for k, yv in zip(v.keys([r.x, r.facet]), v.nums(y0)):
                totals[k] = totals.get(k, 0.0) + (yv or 0.0)
        if not totals or any(t <= 0 for t in totals.values()):
            P.append(Problem("composition_total_not_positive", "y"))
    if spec.key == "interval" and len(r.y) == 3:
        est, lo, hi = (v.nums(c) for c in r.y)
        if any(
            a is not None and b is not None and c is not None and not (b <= a <= c)
            for a, b, c in zip(est, lo, hi)
        ):
            P.append(Problem("interval_bounds_invalid", "y"))
    if spec.key == "trajectories" and r.unit is not None:
        per_panel: dict[tuple, set[str]] = {}
        for f, u in zip(v.keys([r.facet]), v.cells(r.unit)):
            per_panel.setdefault(f, set()).add(u)
        if max((len(us) for us in per_panel.values()), default=0) > MAX_TRAJECTORIES:
            P.append(Problem("trajectories_limit_exceeded", "unit"))
    if spec.key == "dose_response" and r.x is not None:
        if any(x <= 0 for x in v.nums(r.x) if x is not None):
            P.append(Problem("dose_nonpositive", "x"))
    if r.err is not None and r.err in v.idx and y0 in v.idx:
        P.extend(_err_value_problems(r, spec, v))
    return P


def _err_value_problems(r: FigureRecipe, spec: FamilySpec, v: _Values) -> list[Problem]:
    """现成误差列（`recipe.err`）的取值规则，只看这份配方真会画的行里纵轴有值的那些：误差不能为负（空 / NaN 的误差格只是「这个点没有误差线」，不拒绝；
    数值列里的格子要么是有限数要么是缺失，没有 inf）；每个位置恰好一行（比较类：类别 × 分组 × 分面；趋势类：分组 × 分面 × 横轴取值）——有重复测量的表
    误差线对不上点，不画。"""
    P: list[Problem] = []
    ys, es = v.nums(r.y[0]), v.nums(r.err)
    if any(y is not None and e is not None and e < 0 for y, e in zip(ys, es)):
        P.append(Problem("error_values_negative", "err"))
    if spec.key == "comparison":
        keys = v.keys([r.x, r.group, r.facet])
    else:
        keys = list(zip(v.keys([r.group, r.facet]), v.nums(r.x)))
    seen: set = set()
    for k, y in zip(keys, ys):
        if y is None:
            continue
        if k in seen:
            P.append(Problem("error_cells_not_unique", "err"))
            break
        seen.add(k)
    return P


# --------------------------------------------------------------------------- 夹着文字格的数值列：规则校验与套用
def with_cells_applied(values: adf.DataSummary, recipe: FigureRecipe) -> adf.DataSummary:
    """套上配方的处理规则（夹着文字格的 `cells`、整数编码的 `numcat`、把列当分组的 `stack`）之后的带行数据摘要（取值规则、期望指纹都按「处理后的数据」算）；没有规则原样返回。
    `stack` 与前两者互斥（`validate_recipe` 判），转换后是只有 `[值, 类别]` 两列的长表摘要（`stack_view`）。"""
    if recipe.stack:
        return stack_view(values, recipe.stack)
    rows = values.rows
    if recipe.cells:
        rows, _notes, _stats = adf.apply_cells(
            values.columns, values.rows, [c.args() for c in recipe.cells]
        )
    if recipe.numcat:  # 整数编码的类别列：规范成整数写法（脚本读表后套的同一份 `apply_numcat`）
        rows = adf.apply_numcat(values.columns, rows, list(recipe.numcat))
    return values if rows is values.rows else replace(values, rows=rows)


def mixed_columns(s: adf.DataSummary, used: tuple[str, ...]) -> list[str]:
    """`used` 里夹着少量文字的数值列（`numeric_like` 且 kind 是 `mixed`），按给定顺序。"""
    return [
        c
        for c in used
        if c in s.per_column and s.per_column[c].kind == "mixed" and numeric_like(s.per_column[c])
    ]


def non_numeric_cells(values: adf.DataSummary, col: str) -> list[str]:
    """这一列里的文字格（去首尾空白，按首次出现的顺序去重）。"""
    i = values.columns.index(col)
    seen: dict[str, None] = {}
    for r in values.rows:
        if adf.parse_cell(r[i])[0] == "text":
            seen.setdefault(r[i].strip(), None)
    return list(seen)


def check_cell_rules(
    rules: tuple[CellRule, ...] | list[CellRule],
    s: adf.DataSummary,
    values: adf.DataSummary | None,
    used: tuple[str, ...] | None = None,
) -> list[Problem]:
    """处理规则的校验：列必须存在、是夹着文字的数值列（`used` 给了还必须是这次配方用到的），同一列只写一条；`map` 的键个数 ≤ `MAX_MAP_KEYS`、
    值是有限数或 null、键必须真的出现在该列的文字格里（给了 `values` 才判；`detail` 点名是哪个键）。"""
    P: list[Problem] = []
    seen: set[str] = set()
    for i, rule in enumerate(rules):
        field_ = f"cells[{i}]"
        if (
            rule.column in seen
            or (used is not None and rule.column not in used)
            or not mixed_columns(s, (rule.column,))
        ):
            P.append(Problem("cell_rule_invalid", field_, rule.column[:40]))
            continue
        seen.add(rule.column)
        if rule.mode == "map":
            if len(rule.mapping) > MAX_MAP_KEYS:
                P.append(Problem("cell_map_too_large", field_))
                continue
            for k, v in rule.mapping:
                if v is not None and not math.isfinite(v):
                    P.append(Problem("cell_map_value_invalid", field_, k[:40]))
            if values is not None and values.rows:
                have = set(non_numeric_cells(values, rule.column))
                for k, _v in rule.mapping:
                    if k not in have:
                        P.append(Problem("cell_map_key_unknown", field_, k[:40]))
    return P


def cell_rule_problems(
    recipe: FigureRecipe, s: adf.DataSummary, values: adf.DataSummary | None
) -> list[Problem]:
    used = tuple(recipe.y) + (
        (recipe.x,) if recipe.x and recipe.family in ("relationship", "trend") else ()
    )
    return check_cell_rules(recipe.cells, s, values, used)


# --------------------------------------------------------------------------- 校验
#: 大表（`DataSummary.large`，ADR 0178 修订·大表）能画的图元：家族 → 图元集合。**只放行「缩减后画出来的点就是要给人看的东西、不需要对全部行再算统计量」的画法**
#: （折线 / 阶梯 / 散点 / 点；汇总类图元——误差线、带、柱、箱、小提琴、直方图、密度、拟合线、水平汇总线——要对**全部**行算统计量，
#: 大表上还没有流式的精确实现，一律拒）。图元不在这里 = `mark_not_allowed`（复用既有稳定码，不新增前端文案）。
LARGE_MARKS: dict[str, frozenset[str]] = {
    "trend": frozenset({"line", "step", "points"}),
    "relationship": frozenset({"points"}),
}


def trend_too_few_x(counts) -> bool:
    """趋势的「不同横轴取值太少」判据（小表 `_value_problems` 与大表 `ai_data_template.large_reduce` 共用这一个谓词）：`counts` 是每个（分面, 分组）里
    **横轴与第一个 y 都有限**的观测的不同 x 个数（没有这样的观测的格子不在里面）；一个格子都没有、或最少的一个不足 `MIN_X_TREND` 就拒。"""
    counts = list(counts)
    return not counts or min(counts) < MIN_X_TREND


def _large_problems(recipe: FigureRecipe, s: adf.DataSummary) -> list[Problem]:
    """大表上这份配方的拒绝项：家族 / 图元不在 `LARGE_MARKS`、层带汇总（`summary` 不是 none）、带「夹着文字格」的处理规则（逐行规则没有流式实现）。"""
    allowed = LARGE_MARKS.get(recipe.family, frozenset())
    out: list[Problem] = []
    for i, layer in enumerate(recipe.layers):
        if layer.mark not in allowed or layer.summary != SUMMARY_NONE:
            out.append(Problem("mark_not_allowed", f"layers[{i}].mark", f"large:{layer.mark}"))
    if recipe.cells:
        out.append(Problem("mark_not_allowed", "cells", "large"))
    if (
        recipe.numcat
    ):  # 整数编码的分组：大表暂不支持（缩减的系列数上界读 `groups`，`numcat` 在大表摘要里恒为空）
        out.append(Problem("mark_not_allowed", "numcat", "large"))
    if recipe.stack:  # 把列当分组：大表没有行数据，转换没有流式实现（`_stack_problems` 也判一次）
        out.append(Problem("mark_not_allowed", "stack", "large"))
    if recipe.unit is not None:
        out.append(Problem("mark_not_allowed", "unit", "large"))
    if recipe.err is not None:  # 现成误差列：大表没有行数据可逐行对齐
        out.append(Problem("mark_not_allowed", "err", "large"))
    # 取值类规则里只靠「整表最小 / 最大」就能精确判的：对数轴上的非正值（概览扫描留下了每列的精确最小值）。
    # 要整表流过才知道的（折线的 x 重复 / 没排序、系列的 x 个数）在缩减那一遍判（`ai_data_template.large_reduce` 的 `LargeRuleError`）。
    if s.large is not None:
        stats = s.large.stats
        if recipe.scale in ("log_y", "log_xy") and any(
            c in stats and stats[c].lo is not None and stats[c].lo <= 0 for c in recipe.y
        ):
            out.append(Problem("log_nonpositive", "scale", "y"))
        if (
            recipe.scale in ("log_x", "log_xy")
            and recipe.x in stats
            and stats[recipe.x].lo is not None
            and stats[recipe.x].lo <= 0
        ):
            out.append(Problem("log_nonpositive", "scale", "x"))
    return out


def validate_recipe(
    profile: adf.FolderProfile,
    recipe: FigureRecipe,
    *,
    confirmed: Confirmed,
    values: adf.DataSummary | None = None,
) -> list[Problem]:
    """配方对这份文件夹摘要 + 用户确认过的事实合不合格；空表 = 合格。**不执行、不渲染、不写文件**。

    结构类规则只要 `profile`；取值类规则（`VALUE_RULES`）只在给了 `values`（对那张表 `inspect_data_source` 得到的带行数据
    的摘要，列必须与 profile 里那张表一致）时才判——不给就不判，调用方要知道「合格」只代表结构合格（`eligible()`
    的 `unchecked` 同理）。结构问题（列不存在 / 类型不对）会让后面依赖它的规则不判，不会连环报错。"""
    entry = _find(profile, recipe.table, recipe.sheet)
    if entry is None:
        return [Problem("table_not_found", "table")]
    s = entry.summary
    s0 = s  # 原表摘要（`values` 的列要对着它核；`stack` 之后 `s` 是转换后的长表）
    spec = FAMILIES[recipe.family]
    P: list[Problem] = []
    rule_cols = {c.column for c in recipe.cells}
    if recipe.stack:
        bad = _stack_problems(recipe, s)
        if bad:
            return bad
        s = stack_view(s, recipe.stack)
    if recipe.numcat:
        cat_roles = {recipe.group, recipe.facet} | (
            {recipe.x} if "cat" in spec.x_types and "ord" not in spec.x_types else set()
        )
        for i, c in enumerate(recipe.numcat):
            if c not in s.numcat or c in s.groups or c in recipe.numcat[:i]:
                P.append(Problem("column_type_mismatch", f"numcat[{i}]", "num/cat"))
            elif c not in cat_roles:  # 点名的整数编码列必须真的在分组 / 分面 / 类别横轴里
                P.append(Problem("roles_invalid", f"numcat[{i}]"))
        s = numcat_view(s, recipe.numcat)

    def need(col: str | None, fieldname: str, tokens: tuple[str, ...]) -> bool:
        if col is None:
            return False
        if col not in s.per_column:
            P.append(Problem("column_not_found", fieldname))
            return False
        if not _fits(tokens, s, col, col in rule_cols):
            P.append(Problem("column_type_mismatch", fieldname, "/".join(tokens)))
            return False
        return True

    cols_ok = True
    # 角色个数与有无
    if recipe.x is None and spec.x_required or recipe.x is not None and not spec.x_types:
        P.append(Problem("roles_invalid", "x"))
        cols_ok = False
    if not spec.y_min <= len(recipe.y) <= spec.y_max:
        P.append(Problem("roles_invalid", "y", f"{spec.y_min}-{spec.y_max}"))
        cols_ok = False
    if recipe.group is not None and not spec.group or recipe.facet is not None and not spec.facet:
        P.append(
            Problem(
                "roles_invalid", "group" if recipe.group is not None and not spec.group else "facet"
            )
        )
        cols_ok = False
    if spec.key == "composition" and (recipe.group is None) == (len(recipe.y) == 1):
        # 长格式：一个值列 + 部分列（group）；宽格式：多个值列、不给 group
        P.append(Problem("roles_invalid", "group"))
        cols_ok = False
    if (
        spec.key == "distribution"
        and recipe.x is not None
        and any(ly.mark in spec.x_free_marks for ly in recipe.layers)
    ):
        P.append(Problem("roles_invalid", "x", "hist/ecdf/density 不按横轴分类，用 group"))
        cols_ok = False
    if spec.order_required and recipe.order == "as_given":
        P.append(Problem("ranking_needs_order", "order"))
    # 列存在与类型
    # 已汇总的表（`err`）里每个类别只有一行：类别列的每个取值都不同，不在 `groups`（要有重复才算分组）里，按「标签」（任何文字列）认
    x_tokens = spec.x_types + (
        ("label",) if recipe.err is not None and spec.key == "comparison" else ()
    )
    cols_ok &= need(recipe.x, "x", x_tokens) if recipe.x is not None and spec.x_types else True
    for i, c in enumerate(recipe.y):
        cols_ok &= need(c, f"y[{i}]", spec.y_types)
    cols_ok &= (
        need(recipe.group, "group", ("cat",)) if recipe.group is not None and spec.group else True
    )
    cols_ok &= (
        need(recipe.facet, "facet", ("cat",)) if recipe.facet is not None and spec.facet else True
    )
    if recipe.unit is not None:
        cols_ok &= _unit_col_ok(s, recipe.unit, P)
    if recipe.err is not None or recipe.err_kind is not None:
        bad = _err_problems(recipe, spec, s)
        P.extend(bad)
        cols_ok &= not bad
    # 画法组合
    marks = [layer.mark for layer in recipe.layers]
    for i, layer in enumerate(recipe.layers):
        if layer.mark not in spec.marks:
            P.append(Problem("mark_not_allowed", f"layers[{i}].mark", layer.mark))
            continue
        rule = MARK_RULES[layer.mark]
        needs_summary = rule.summary == "required" and not (
            spec.explicit_bounds and layer.mark == "errorbar"
        )
        if needs_summary and layer.summary == SUMMARY_NONE:
            P.append(Problem("summary_missing", f"layers[{i}].summary", layer.mark))
        if (
            rule.summary == "none"
            and layer.summary != SUMMARY_NONE
            or (spec.explicit_bounds and layer.mark == "errorbar" and layer.summary != SUMMARY_NONE)
        ):
            P.append(Problem("summary_misplaced", f"layers[{i}].summary", layer.mark))
    if not any(m in spec.primary for m in marks):
        P.append(Problem("no_primary_mark", "layers"))
    # 模板能画的组合（图元与坐标的搭配）：分类轴上没有对数 x；分布的「值在横轴」图元不与「值在纵轴」图元叠、不用对数轴、
    # ECDF（累积比例）不与直方图 / 密度（个数 / 密度）叠在同一纵轴上
    if recipe.family in ("comparison", "distribution") and recipe.scale in ("log_x", "log_xy"):
        P.append(Problem("mark_not_allowed", "scale", "log_x"))
    if recipe.family == "distribution":
        value_on_x = {"hist", "ecdf", "density"}
        ms = set(marks)
        if ms & value_on_x and ms - value_on_x:
            P.append(Problem("mark_not_allowed", "layers", "value_axis"))
        if "ecdf" in ms and ms & {"hist", "density"}:
            P.append(Problem("mark_not_allowed", "layers", "ecdf_with_hist"))
        if ms & value_on_x and recipe.scale != "linear":
            P.append(Problem("mark_not_allowed", "scale", "log"))
    if not any(layer.role == "main" for layer in recipe.layers) or len(
        set((ly.mark, ly.role) for ly in recipe.layers)
    ) != len(recipe.layers):
        P.append(Problem("layer_role_invalid", "layers"))
    # 用户确认过的事实
    unit_confirmed = (
        confirmed.design in ("paired", "repeated")
        and confirmed.unit_col is not None
        and confirmed.unit_col == recipe.unit
    )
    if recipe.unit is not None and not unit_confirmed:
        P.append(Problem("unit_unconfirmed", "unit"))
    if spec.unit == "no" and recipe.unit is not None:
        P.append(Problem("roles_invalid", "unit"))
    if spec.unit == "required" and recipe.unit is None:
        P.append(Problem("unit_required", "unit"))
    if recipe.paired and not spec.paired:
        P.append(Problem("roles_invalid", "paired"))
    elif spec.paired and confirmed.design != "paired":
        P.append(Problem("paired_unconfirmed", "paired"))
    elif spec.paired and not recipe.paired:
        P.append(Problem("roles_invalid", "paired", "paired=true"))
    # 规模
    if (
        recipe.facet is not None
        and recipe.facet in s.groups
        and s.groups[recipe.facet] > MAX_FACETS
    ):
        P.append(Problem("facet_limit_exceeded", "facet", str(s.groups[recipe.facet])))
    if (
        recipe.group is not None
        and recipe.group in s.groups
        and s.groups[recipe.group] > MAX_GROUP_LEVELS
    ):
        P.append(Problem("group_limit_exceeded", "group", str(s.groups[recipe.group])))
    if recipe.coord == "polar" and not spec.polar:
        P.append(Problem("coord_not_allowed", "coord"))
    if len(recipe.y) > 1 and cols_ok:
        units = {u for u in (_unit_of(c) for c in recipe.y) if u}
        if len(units) > 1:
            P.append(Problem("dual_axis_unsupported", "y", ",".join(sorted(units))))
    if spec.key == "matrix":
        rows = s.row_count if s.row_count is not None else len(s.rows)
        if rows > MAX_MATRIX_ROWS or len(recipe.y) > MAX_MATRIX_COLS:
            P.append(Problem("matrix_too_large", "y", f"{rows}x{len(recipe.y)}"))
    if spec.key == "composition":
        parts = len(recipe.y) if len(recipe.y) > 1 else (s.groups.get(recipe.group or "", 0))
        if parts > MAX_COMPOSITION_PARTS:
            P.append(Problem("composition_too_many_parts", "y", str(parts)))
    if spec.key == "ranking" and recipe.x is not None and recipe.x in s.per_column:
        items = s.groups.get(recipe.x) or (s.row_count if s.row_count is not None else len(s.rows))
        if items > MAX_RANKING_ITEMS:
            P.append(Problem("ranking_too_many_items", "x", str(items)))
    P.extend(cell_rule_problems(recipe, s, values))
    if s.large is not None:
        P.extend(_large_problems(recipe, s))
    # 取值（只在列都对得上时）
    if values is not None and values.rows and cols_ok:
        if values.columns != s0.columns or values.sheet != s0.sheet:
            raise ValueError("values 与 profile 里那张表的列 / 工作表对不上")
        if (recipe.cells or recipe.numcat or recipe.stack) and not any(
            p.code.startswith("cell_") for p in P
        ):
            values = with_cells_applied(values, recipe)
        P.extend(
            _value_problems(
                recipe, spec, s, _Values(values, drawn_rows(recipe, values)), unit_confirmed
            )
        )
    return P


def _unit_col_ok(s: adf.DataSummary, col: str, P: list[Problem]) -> bool:
    if col not in s.per_column:
        P.append(Problem("column_not_found", "unit"))
        return False
    if s.per_column[col].kind not in ("text", "numeric"):
        P.append(Problem("column_type_mismatch", "unit", "text/num"))
        return False
    return True


# --------------------------------------------------------------------------- 资格
@dataclass(frozen=True)
class Eligibility:
    """一张表对一类问题的资格。`status`：`ok`（能画）/ `no`（不能画，`reasons` 是稳定码；`ASK_FIRST` 里的码出路是先问用户）/
    `partial`（能画，但 `rejected_marks` 里的图元被拒，码在值里）。`unchecked`：没给取值、因此没判的规则码。
    `order_rules`：排序约束码（供 `enforce_order` 之外的调用方在模型排序后再强制一遍）。"""

    family: str
    status: str
    reasons: tuple[str, ...] = ()
    rejected_marks: dict[str, str] = field(default_factory=dict)
    unchecked: tuple[str, ...] = ()
    order_rules: tuple[str, ...] = ()
    renderable: bool = False

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "status": self.status,
            "reasons": list(self.reasons),
            "rejected_marks": dict(self.rejected_marks),
            "unchecked": list(self.unchecked),
            "order_rules": list(self.order_rules),
            "renderable": self.renderable,
        }


def _columns_of(s: adf.DataSummary, tokens: tuple[str, ...]) -> list[str]:
    return [c for c in s.columns if _fits(tokens, s, c, True)]


def _structure_reasons(spec: FamilySpec, s: adf.DataSummary, confirmed: Confirmed) -> list[str]:
    xs = _columns_of(s, spec.x_types) if spec.x_types else []
    ys = _columns_of(s, spec.y_types)
    reasons: list[str] = []
    # 需要不同的两列：x 与 y 不是同一列
    usable_y = [c for c in ys if len(xs) != 1 or c != xs[0]] if spec.x_required else ys
    if spec.x_required and not xs or len(usable_y) < spec.y_min:
        reasons.append("no_usable_columns")
    elif spec.key == "paired" and not any(s.groups[c] == 2 for c in xs if c in s.groups):
        reasons.append("no_usable_columns")
    elif spec.key == "matrix":
        rows = s.row_count if s.row_count is not None else len(s.rows)
        if s.layout not in ("matrix", "wide"):
            reasons.append("no_usable_columns")
        elif rows > MAX_MATRIX_ROWS or len(ys) > MAX_MATRIX_COLS:
            reasons.append("matrix_too_large")
    elif spec.key == "composition":
        long_form = len(_columns_of(s, ("cat",))) >= 2
        wide_form = s.layout == "wide" and len(ys) >= 2
        if not (long_form or wide_form):
            reasons.append("no_usable_columns")
    elif spec.key == "profile" and s.layout != "wide":
        reasons.append("no_usable_columns")
    if spec.unit == "required":
        if confirmed.design not in ("paired", "repeated") or confirmed.unit_col not in s.per_column:
            reasons.append("paired_unconfirmed" if spec.paired else "unit_required")
        elif confirmed.design != "paired" and spec.paired:
            reasons.append("paired_unconfirmed")
    return reasons


def eligible(
    entry: adf.TableEntry,
    confirmed: Confirmed,
    *,
    values: adf.DataSummary | None = None,
    numcat: tuple[str, ...] = (),
) -> list[Eligibility]:
    """一张表对十二类问题各自的资格（按 `FAMILIES` 顺序）。结构判定只看摘要；给了 `values`（同一张表带行数据的摘要）
    才在最有利的列组合（至多各取前几列试）上判取值类图元的拒绝，没给就把相关码列在 `unchecked`。
    `numcat`：被点名按类别用的整数编码列（`FigureRecipe.numcat`）——判定时当分组列看（`numcat_view`）；默认空 = 逐字不变。"""
    s = numcat_view(entry.summary, numcat)
    if numcat and values is not None and values.rows:
        values = replace(values, rows=adf.apply_numcat(values.columns, values.rows, list(numcat)))
    out: list[Eligibility] = []
    for spec in FAMILIES.values():
        reasons = _structure_reasons(spec, s, confirmed)
        if reasons:
            out.append(Eligibility(spec.key, "no", tuple(reasons), renderable=renderable(spec.key)))
            continue
        rejected: dict[str, str] = {}
        unchecked: tuple[str, ...] = ()
        relevant = _relevant_value_rules(spec)
        if values is not None and values.rows:
            rejected = _probe_marks(spec, s, values)
        else:
            unchecked = tuple(sorted(relevant))
        order_rules: tuple[str, ...] = ()
        if spec.key == "comparison" and not any(m in rejected for m in _RAW_PLUS):
            order_rules = ("bar_before_raw_points",)
        out.append(
            Eligibility(
                spec.key,
                "partial" if rejected else "ok",
                tuple(sorted(set(rejected.values()))),
                rejected,
                unchecked,
                order_rules,
                renderable(spec.key),
            )
        )
    return out


def _relevant_value_rules(spec: FamilySpec) -> set[str]:
    rel = {c for c in spec.rejects if c in VALUE_RULES}
    m = set(spec.marks)
    if m & BOX_MARKS:
        rel.add("box_violin_n_small")
    if m & HIST_MARKS:
        rel.add("hist_density_n_small")
    if "fit_line" in m:
        rel.add("fit_line_n_small")
    if m & BAR_MARKS:
        rel |= {"bar_baseline_invalid", "bar_needs_summary"}
    if m & POINTWISE_LINES:
        rel.add("line_duplicate_x_unconfirmed")
    return rel


#: 资格里最多试多少组（x, y）列组合，防止宽表上组合爆炸
_PROBE_PAIRS = 4


def _probe_marks(spec: FamilySpec, s: adf.DataSummary, values: adf.DataSummary) -> dict[str, str]:
    """最有利的列组合上，哪些图元仍被取值规则拒绝（所有试过的组合都拒绝才算拒绝）。只探「每组 n」与「非负」两类，
    其余取值规则依赖具体配方，留给 `validate_recipe`。"""
    v = _Values(values)
    ys = _columns_of(s, spec.y_types)
    xs: list[str | None] = list(_columns_of(s, spec.x_types)) if spec.x_types else []
    if not spec.x_required or not xs:
        xs.insert(0, None)  # x 可选的族（distribution）：「不带 x」也是一种配方，要探
    pairs = [(x, y) for x in xs[:_PROBE_PAIRS] for y in ys[:_PROBE_PAIRS] if x != y][:_PROBE_PAIRS]
    if not pairs:
        return {}
    rejected: dict[str, list[bool]] = {}
    for x, y in pairs:
        keys = v.keys([_cat(s, x)])
        n = _count_by(v, keys, y)
        small = bool(n) and min(n.values()) < MIN_N_BOX
        hist_small = (
            bool(n)
            and min(_count_by(v, [()] * len(values.rows), y).values(), default=0) < MIN_N_HIST
        )
        neg = any(t is not None and t < 0 for t in v.nums(y))
        for m in spec.marks:
            if m in BOX_MARKS:
                rejected.setdefault(m, []).append(small)
            elif m in HIST_MARKS:
                rejected.setdefault(m, []).append(hist_small)
            elif m in BAR_MARKS:
                rejected.setdefault(m, []).append(neg)
    code = {
        **{m: "box_violin_n_small" for m in BOX_MARKS},
        **{m: "hist_density_n_small" for m in HIST_MARKS},
        **{m: "bar_baseline_invalid" for m in BAR_MARKS},
    }
    return {m: code[m] for m, flags in rejected.items() if flags and all(flags)}


# --------------------------------------------------------------------------- 排序护栏
@dataclass(frozen=True)
class Dropped:
    index: int
    problems: tuple[Problem, ...]


@dataclass(frozen=True)
class OrderResult:
    """`kept`：合格且按约束重排后的备选（可能少于 `MIN_CANDIDATES`——如实返回，不凑数）；`dropped`：被剔除的（原序号 + 原因）；
    `moves`：被调序的备选（原序号 + `bar_before_raw_points`）；`short`：剩下不足两个。"""

    kept: tuple[FigureRecipe, ...]
    dropped: tuple[Dropped, ...]
    moves: tuple[Problem, ...]
    short: bool


def _is_bar_only(r: FigureRecipe) -> bool:
    marks = {layer.mark for layer in r.layers}
    return bool(marks & {"bar"}) and not marks & RAW_MARKS


def _has_raw(r: FigureRecipe) -> bool:
    return bool({layer.mark for layer in r.layers} & RAW_MARKS)


def enforce_order(
    profile: adf.FolderProfile,
    recipes: list[FigureRecipe],
    *,
    confirmed: Confirmed,
    values: adf.DataSummary | None = None,
) -> OrderResult:
    """模型给的 2–4 个备选经它校验、去重、重排之后才出给用户。不合格（`validate_recipe` 非空）的剔除并带原因；与前面某个
    完全相同的剔除（`duplicate_candidate`）；超过 4 个的多余部分剔除（`too_many_candidates`）；comparison 里
    「只有柱（无原始点）」的备选一律排在所有含原始点的备选之后（稳定重排，其余顺序保持模型原样）。
    剩不足 2 个时如实返回（`short=True`）。"""
    dropped: list[Dropped] = []
    seen: set[str] = set()
    ok: list[tuple[int, FigureRecipe]] = []
    for i, r in enumerate(recipes):
        if len(ok) >= MAX_CANDIDATES:
            dropped.append(Dropped(i, (Problem("too_many_candidates"),)))
            continue
        problems = validate_recipe(profile, r, confirmed=confirmed, values=values)
        if problems:
            dropped.append(Dropped(i, tuple(problems)))
            continue
        fp = repr(
            {k: val for k, val in r.to_dict().items() if k != "why"}
        )  # 指纹只含决定画面的字段
        if fp in seen:
            dropped.append(Dropped(i, (Problem("duplicate_candidate"),)))
            continue
        seen.add(fp)
        ok.append((i, r))
    moves: list[Problem] = []
    ordered = list(ok)
    raw_exists = any(r.family == "comparison" and _has_raw(r) for _, r in ok)
    if raw_exists:
        keep = [(i, r) for i, r in ok if not (r.family == "comparison" and _is_bar_only(r))]
        late = [(i, r) for i, r in ok if r.family == "comparison" and _is_bar_only(r)]
        ordered = keep + late
        # 被调序的 = 那些原先排在某个含原始点备选之前、现在被挪后的柱备选
        last_raw_pos = max((p for p, (_, r) in enumerate(ok) if _has_raw(r)), default=-1)
        for p, (i, r) in enumerate(ok):
            if r.family == "comparison" and _is_bar_only(r) and p < last_raw_pos:
                moves.append(Problem("bar_before_raw_points", "order", str(i)))
    kept = tuple(r for _, r in ordered)
    return OrderResult(kept, tuple(dropped), tuple(moves), len(kept) < MIN_CANDIDATES)


# --------------------------------------------------------------------------- 确定性备选（不调模型；模型路径的底座）
#: 选择题的稳定码（前端 `errors` 之外的 `dataFigure.question.<码>`）与备选的标题 / 理由码（`dataFigure.candidate.<码>.title|why`）。
#: 后端不写界面文案；这两张表就是前端中英文文案要覆盖的闭集（`tests/test_ai_data_propose.py` 对着 locale 核）。
QUESTION_CODES = (
    "pick_table",
    "pick_value",
    "pick_group",
    "pick_x",
    "pick_design",
    "pick_unit",
    "mixed_cells",
)
CANDIDATE_CODES = (
    "line_err",  # 已汇总的趋势（每个位置一行 + 现成误差列）：点加线，误差线取自误差列
    "points_err",  # 已汇总的分组（每个类别一行 + 现成误差列）：一个类别一个点，误差线取自误差列
    "points_mean",
    "box_points",
    "violin_points",
    "ecdf_groups",
    "bar_error",
    "line_points",
    "line_band",
    "line_units",
    "scatter_fit",
    "scatter",
    "hist",
    "ecdf",
    "density",
    "box_points_single",
)
#: 备选为空时的原因码（`Candidates.reasons`；备选被护栏剔除的原因另在 `dropped` 里，用 `REJECTIONS` 的码）
NOTHING_REASONS = (
    "no_usable_table",
    "value_column_mixed",  # 每一列要么文字太多、要么数值太少（`numeric_like`），没有能当数值的列
    "table_not_found",
    "no_numeric_column",
    "matrix_unsupported",
    "no_renderable_family",
    "all_candidates_rejected",
)
#: 一个选择题最多给几个列选项（不含「不确定」）
MAX_OPTIONS = 4
#: 选表的选择题最多列几张表（超出的用一句话点名列）
MAX_TABLE_OPTIONS = 8


@dataclass(frozen=True)
class Option:
    """选择题的一个选项：`code` 是文案码，`params` 是文案里的插值（文件名、列名、行数…），`answer` 是用户选它时前端原样并进
    下一次请求 `answers` 的键值（后端只认自己出过的键）。"""

    code: str
    params: dict
    answer: dict
    free_text: bool = False  # 前端据此显示输入框（用户用文字说明怎么办）

    def to_dict(self) -> dict:
        out = {"code": self.code, "params": dict(self.params), "answer": dict(self.answer)}
        if self.free_text:
            out["free_text"] = True
        return out


@dataclass(frozen=True)
class NeedsChoice:
    """决定科学含义的一件事只能由用户定：给 2–4 个选项（选表最多 `MAX_TABLE_OPTIONS`）和可选的「不确定」。「不确定」一定有
    安全的默认（每个问题的默认写在 `default_candidates` 的列角色定义里），所以任何回答都能继续走下去，不会卡死。"""

    question_code: str
    options: tuple[Option, ...]
    unsure: Option | None = None

    def to_dict(self) -> dict:
        return {
            "question_code": self.question_code,
            "options": [o.to_dict() for o in self.options],
            "unsure": self.unsure.to_dict() if self.unsure else None,
        }


#: 预览最多几行、单格文字最多显示多少字
MIXED_PREVIEW_ROWS = 5
MIXED_TEXT_SHOWN = 40


@dataclass(frozen=True)
class MixedCells:
    """数值列里夹着少量文字：**先问用户怎么处理**，不静默只画数值行。`columns` 一次列出这次画图用到的所有这样的列：
    `{column, non_numeric, rows, preview: [{row, text, others, extract}], more, options}`——`row` 是文件里的行号（表头算第 1 行，
    XLSX 是表内行号），`others` 是同一行其它用到的列的值，`extract` 是选「取数字部分」后这一格会得到的值（取不出 = None）；
    选项 `skip` / `extract` / `describe`（`free_text`），`answer` 原样并进下一次请求的 `answers.cells`。"""

    columns: tuple[dict, ...]
    question_code: str = "mixed_cells"

    def to_dict(self) -> dict:
        return {"question_code": self.question_code, "columns": [dict(c) for c in self.columns]}


def _shown(text: str) -> str:
    t = text.strip()
    return t if len(t) <= MIXED_TEXT_SHOWN else t[: MIXED_TEXT_SHOWN - 1] + "…"


def mixed_cells_question(
    values: adf.DataSummary, cols: list[str], context: tuple[str, ...]
) -> MixedCells:
    """为 `cols` 里每一列做预览（最多 `MIXED_PREVIEW_ROWS` 行，超出写 `more`）。`context`：这次画图用到的其它列（预览里同行带出它们的值）。"""
    out = []
    for col in cols:
        i = values.columns.index(col)
        c = values.per_column[col]
        hits = [(n, r) for n, r in enumerate(values.rows) if adf.parse_cell(r[i])[0] == "text"]
        preview = []
        for n, r in hits[:MIXED_PREVIEW_ROWS]:
            got, _, _ = adf.apply_cells(["c"], [[r[i]]], [("c", "extract", {}, "skip")])
            ext = got[0][0]
            preview.append(
                {
                    "row": values.row_number(n),
                    **({"file": src} if (src := values.row_source(n)) is not None else {}),
                    "text": _shown(r[i]),
                    "others": {
                        o: _shown(r[values.columns.index(o)])
                        for o in context
                        if o != col and o in values.columns
                    },
                    "extract": float(ext) if ext != "" else None,
                }
            )
        out.append(
            {
                "column": col,
                "non_numeric": len(hits),
                "rows": c.numeric + c.text_count,
                "preview": preview,
                "more": max(0, len(hits) - MIXED_PREVIEW_ROWS),
                "options": [
                    Option("skip", {}, {"cells": {col: {"mode": "skip"}}}).to_dict(),
                    Option("extract", {}, {"cells": {col: {"mode": "extract"}}}).to_dict(),
                    Option(
                        "describe", {}, {"cells": {col: {"mode": "describe"}}}, free_text=True
                    ).to_dict(),
                ],
            }
        )
    return MixedCells(tuple(out))


@dataclass(frozen=True)
class Candidate:
    """一个备选：配方（已过 `validate_recipe`）+ 标题 / 理由文案码（`CANDIDATE_CODES`）。排在第一的就是推荐。"""

    code: str
    recipe: FigureRecipe


@dataclass(frozen=True)
class Candidates:
    """`items` 为空 = 这张表现在画不出（`reasons` 是 `NOTHING_REASONS` 里的码，`dropped` 是被护栏剔除的备选与原因）。
    `confirmed` 是实际生效的已确认事实（用户只有一个编号列时「重复测量」自动落到它上面）。"""

    table: str
    sheet: str | None
    items: tuple[Candidate, ...]
    dropped: tuple[Dropped, ...] = ()
    reasons: tuple[str, ...] = ()
    confirmed: Confirmed = field(default_factory=Confirmed)
    #: 这次实际用的列角色与每个角色可换成的列（`{x, y: [..], group, facet, unit, options: {x, y, group}}`）；还没走到列角色那一步就是 None
    roles: dict | None = None


def _is_id_like_col(s: adf.DataSummary, col: str) -> bool:
    return col in s.id_like


def _usable_table(t: adf.TableEntry) -> bool:
    s = t.summary
    n = s.row_count if s.row_count is not None else len(s.rows)
    return n >= 1 and any(numeric_like(s.per_column[c]) and c not in s.id_like for c in s.columns)


def _table_option(t: adf.TableEntry) -> Option:
    s = t.summary
    rows = s.row_count if s.row_count is not None else len(s.rows)
    params = {"file": t.path, "rows": rows, "groups": len(s.groups)}
    answer = {"table": t.path}
    if t.sheet is not None:
        params["sheet"] = t.sheet
        answer["sheet"] = t.sheet
    return Option("table_sheet" if t.sheet is not None else "table", params, answer)


def _col_options(key: str, cols: list[str], s: adf.DataSummary) -> tuple[Option, ...]:
    return tuple(
        Option("column", {"column": c, "kind": s.per_column[c].kind}, {key: c})
        for c in cols[:MAX_OPTIONS]
    )


def _unsure(key: str) -> Option:
    return Option("unsure", {}, {key: "unsure"})


def pick_table(
    profile: adf.FolderProfile, table: str | None, sheet: str | None, hint: str | None
) -> adf.TableEntry | NeedsChoice | str:
    """选表：指名的表（`table` + 可选 `sheet`）；没指名且只有一张可用表就用它；多张 → 选择题（每张一句人话）；`hint` 里按词点名
    了唯一一张表的文件名或工作表名也算指名。返回表 / 选择题 / 原因码（`no_usable_table` / `table_not_found`）。"""
    if table is not None:
        hits = [
            t for t in profile.tables if t.path == table and (sheet is None or t.sheet == sheet)
        ]
        if not hits:
            return "table_not_found"
        if len(hits) > 1:  # 同一个 XLSX 的多张工作表又没指名工作表：先问是哪一张
            return NeedsChoice(
                "pick_table", tuple(_table_option(t) for t in hits[:MAX_TABLE_OPTIONS])
            )
        return hits[0]
    usable = [t for t in profile.tables if _usable_table(t)]
    shown = [t for t in usable if not t.hidden] or usable
    if not shown:
        mixed = any(
            c.kind == "mixed" for t in profile.tables for c in t.summary.per_column.values()
        )
        return "value_column_mixed" if mixed else "no_usable_table"
    if len(shown) == 1:
        return shown[0]
    if hint:
        names = [Path(t.path).name for t in shown] + [t.sheet for t in shown if t.sheet]
        named = adf._mentioned(hint, names)
        if named:
            hit = [t for t in shown if Path(t.path).name == named[0] or t.sheet == named[0]]
            if len(hit) == 1:
                return hit[0]
    return NeedsChoice("pick_table", tuple(_table_option(t) for t in shown[:MAX_TABLE_OPTIONS]))


@dataclass(frozen=True)
class _Roles:
    ys: tuple[str, ...]  # 数值列（trend 可多个，其余只用第一个）
    cats: tuple[str, ...]  # 分组列（非编号）
    x_ord: str | None  # 有序数值 / 日期 x（None = 没有或有歧义）
    x_numeric: bool  # x_ord 是数值（不是日期）：relationship 才可用
    #: 每个角色此刻**可换成**哪些列（给界面做下拉；换了就把列名放进 `picks` 重出）：`y` 数值列、`group` 分组列（含整数编码列）、`x` 有序横轴候选
    options: dict = field(default_factory=dict)
    #: `cats` 里被点名、按类别用的整数编码列（配方的 `numcat` 来源）；没人点名 = 空
    numcat: tuple[str, ...] = ()
    #: 「把这几列当分组」（`FigureRecipe.stack`）：被点名的宽表列（表里的列顺序）；这时 `ys` 就是它们（真列名）、`cats` 空、没有横轴。没有 = 空
    stack: tuple[str, ...] = ()


def _stack_roles(entry: adf.TableEntry, hint: str | None, picks: dict) -> _Roles | None:
    """`picks.y` 给了多列、而最终**没有有序横轴**，且这几列客观上能「当分组」（`stack_ok`）→ 角色落成「列当分组」；否则 None（交给原有的规则）。
    **只有点名才进**（规则从不自己用）。不支持的情形一律返回 None、回到规则结果：合画（合成的文件列才是主分组）、大表、有文字分组列（「列当分组再分组」）、
    点名了分组列、整数编码的分组点名、有序横轴存在（`picks.x` 点名了它，或没说 `none` 而除这几列外还有日期 / 数值列——此时仍是每列一条线的趋势类）。
    类别顺序 = 表里的列顺序（不是点名的顺序）。"""
    p = picks.get("y")
    s = entry.summary
    if (
        not isinstance(p, list)
        or not all(isinstance(c, str) for c in p)
        or getattr(entry, "merge", None) is not None
        or not stack_ok(s, p)
    ):
        return None
    gp = picks.get("group")
    if isinstance(gp, str) and gp not in ("none", "unsure"):
        return None
    ids = set(s.id_like)
    cols = s.columns
    if any(c in s.groups and c not in ids for c in cols):  # 有文字分组列
        return None
    ys = [c for c in cols if c in p]
    xs = [
        c
        for c in cols
        if c not in ys
        and c not in ids
        and (s.per_column[c].kind == "date" or numeric_like(s.per_column[c]))
    ]
    if xs and picks.get("x") != "none":
        return None
    options = {
        "y": [c for c in cols if numeric_like(s.per_column[c]) and c not in ids],
        "group": [c for c in cols if c in s.numcat and c not in ys],
        "x": xs,
    }
    return _Roles(tuple(ys), (), None, False, options, (), tuple(ys))


def _resolve_roles(
    entry: adf.TableEntry, hint: str | None, picks: dict
) -> _Roles | NeedsChoice | str:
    stacked = _stack_roles(entry, hint, picks)
    if stacked is not None:
        return stacked
    res = _resolve_roles_raw(entry, hint, picks)
    if isinstance(picks.get("y"), list) and isinstance(res, _Roles) and len(res.ys) > 1:
        # 多列纵轴只在有有序横轴（趋势类每列一条线）时才用：分布 / 比较类配方只画第一列（`default_candidates` 的 `[y]`），
        # `roles.y` 报三列而图里只有一列 = 悄悄丢列。没有横轴就当没给这个 pick，回到规则结果
        if res.x_ord is None or picks.get("y") != list(res.ys):
            return _resolve_roles_raw(entry, hint, {k: v for k, v in picks.items() if k != "y"})
    return res


def error_columns(s: adf.DataSummary) -> dict[str, str]:
    """名字在说「我是误差量」的数值列 → 哪一种（`adf.error_kind`）。规则不把它们当测量值 / 横轴去画（sciviz G-053：误差量不是观测）。"""
    out: dict[str, str] = {}
    for c in s.columns:
        if numeric_like(s.per_column[c]):
            kind = adf.error_kind(c, [o for o in s.columns if o != c])
            if kind is not None:
                out[c] = kind
    return out


def _owners(s: adf.DataSummary, skip: set[str]) -> dict[str, str | None]:
    """每个误差列 → 它修饰的测量列（认不准 = None）；测量列池 = 数值列去掉 `skip`。名字规则是 `adf.error_match`（与 `error_kind` 同一份）。"""
    errs = error_columns(s)
    measures = [c for c in s.columns if numeric_like(s.per_column[c]) and c not in skip | set(errs)]
    out: dict[str, str | None] = {}
    for e in errs:
        _kind, partners = adf.error_match(e, [o for o in s.columns if o != e])
        if partners:
            out[e] = partners[0] if len(partners) == 1 and partners[0] in measures else None
        elif len(measures) == 1:
            # 唯一的「测量列」若叫 n / count，多半是样本量、误差列修饰的是别的列（`group, value, sd, n` 里 `value` 被当横轴 / 标签时）：不猜
            only = measures[0]
            out[e] = None if only.strip().lower() in SAMPLE_SIZE_NAMES else only
        else:
            means = [m for m in measures if adf._MEAN_WORDS & set(adf._name_tokens(m))]
            out[e] = means[0] if len(means) == 1 else None
    return out


def sample_size_columns(s: adf.DataSummary) -> list[str]:
    """样本量列：名字恰好是 `n` / `count`（不分大小写）的数值列，**且表里有一个误差列明确配着另一列**（把它们也算进测量列池再配对）——已汇总的表
    `group, mean, sd, n` 里的 `n` 是每个均值背后的样本数，不是测量值也不是横轴。`n` / `count` 自己就是某个误差列的配对对象（`group, count, sd`）、
    或配对有歧义（`group, value, sd, n`、`time, count, sd`）时它仍是普通数值列，规则不动它。"""
    cands = [
        c
        for c in s.columns
        if c.strip().lower() in SAMPLE_SIZE_NAMES and numeric_like(s.per_column[c])
    ]
    if not cands or not error_columns(s):
        return []
    owners = _owners(s, set(s.id_like))
    if not any(o is not None and o not in cands for o in owners.values()):
        return []
    taken = {o for o in owners.values() if o is not None}
    return [c for c in cands if c not in taken]


def error_owners(s: adf.DataSummary, axes: tuple[str, ...] = ()) -> dict[str, str | None]:
    """每个误差列（`error_columns`）→ 它修饰的测量列；**认不准就是 None（不猜）**：

    * 名字里的误差词有它修饰的那一列（`mass_sd` ↔ `mass`、`response_sem` ↔ `mean_response`、`sem` ↔ 名字带 `mean` / `avg` 的列）：恰好一个、且是测量列才算；多个 = None；
    * 光秃秃的 `sd` / `sem` / `err` / 中文词：表里（去掉误差列、样本量列、编号列和 `axes`）恰好一个数值测量列就是它；多个时只认名字带
      `mean` / `avg` / `average` 的那一列，不唯一 = None。

    `axes`：这次画图里不是测量值的列（有序横轴等）；列角色还没定时传空。"""
    return _owners(s, {*sample_size_columns(s), *s.id_like, *axes})


def paired_error(s: adf.DataSummary, y: str, axes: tuple[str, ...] = ()) -> str | None:
    """测量列 `y` 配着的误差列（名字规则同 `adf.error_kind`，见 `error_owners`）；**同一个测量列有不止一个误差列、或配对有歧义 = None**（宁可不画，不猜）。
    sciviz G-053：误差列是误差量、不是观测——这一对关系只用来「把现成的误差画成误差线」，不放宽哪一列算误差列。"""
    mine = [e for e, owner in error_owners(s, axes).items() if owner == y]
    return mine[0] if len(mine) == 1 else None


def error_kind_of(s: adf.DataSummary, err: str) -> str:
    """误差列名字说的是哪一种（`ERR_KINDS`）；名字没说（`unknown`）或认不出都记 `unknown`——**绝不当成 SD**。"""
    kind = adf.error_kind(err, [o for o in s.columns if o != err])
    return ERR_KIND_OF_NAME.get(kind or "", "unknown")


def _resolve_roles_raw(
    entry: adf.TableEntry, hint: str | None, picks: dict
) -> _Roles | NeedsChoice | str:
    """列角色的挑选规则（**一处文字定义，下面是实现**；保守：能由结构或用户点名唯一确定就确定，有歧义就问，绝不猜）。

    记号：`ids` = `id_like` 列（**永不**当 y / x / 分组，只可能经用户确认成 `unit`）；`nums` = 数值列（`numeric_like`：全是数，或夹着不超过 20% 文字格的，去掉 ids，
    再去掉名字在说「我是误差量」的列（`error_columns`：`sd` / `sem` / `mass_se` 一类——误差量不是测量值；用户自己点了它（`picks` 或 `hint` 里点名）、或去掉后一个数值列都不剩时不去）；夹着的文字格那行不画并报告）；
    `dates` = 日期列；`cats` = `groups` 里的列（去掉 ids，列顺序）。`hint` 里按词点名的列（`ai_data_figure._mentioned`，
    不做语义猜测）和选择题的回答 `picks`（键 `y` / `group` / `x`，值是列名或 `unsure`）都算「指定」。

    1. **y（测的量）**：`picks.y` → 点名的第一个数值列 → 只有一个 `nums` 就是它 → 宽表（layout `wide`、第一列是数值 / 日期）：
       第一列是 x、其余数值列都是 y（至多 3 个，更多就问）→ 其它情况有多个数值列：**问**（选项 = 前 4 个数值列）；
       `unsure` 的默认 = 最后一个数值列（惯例：条件 / 时间在前、测量值在后）；没有数值列 → 画不了（`no_numeric_column`）。
    2. **分组列**：`cats` 只有一个就是它；多个：`picks.group` → 点名的第一个 → **问**（选项 = 各分组列，前 4 个）；`unsure` 的默认
       = 最左的分组列（它是比较的横轴，另一个——恰好两个分组列时——当颜色分组，否则不用）。
    3. **x（有序横轴）**：除 y 外还剩下的日期列 + 数值列（`xs`）：恰好一个就是它；多个：`picks.x` → 点名的第一个 → 宽表第一列；
       都没有且**没有分组列** → **问**（`unsure` 的默认 = 最左的日期列，没有就最左的数值列）；有分组列时不问，不出 trend /
       relationship（分组比较与分布是主图，不拿一个猜出来的横轴去换）。有横轴时分组列做颜色分组（恰好两个分组列时另一个做分面）。
    （横轴候选 `xs` 里的数值列可以是夹着少量文字的（`numeric_like`，与 y 同一阈值）：那几行不画、先问 / 懒模式默认跳过，处理规则同 y 列；文字占多数的列不进。）
    （`picks.group == "none"` / `picks.x == "none"`：用户在右侧面板明确不要分组 / 不要有序横轴——当没有这个角色，不问。）
    （读懂数据的三处例外，条件各一处实现：`picks.y` 可以是 2–`MULTI_Y_MAX` 个列名的数组（`multi_y_ok`，不合格当没给）；`picks.group` 点名一个名字像编号、但文字且有重复
    取值的列（`id_group_ok`）时它进分组列；`picks.x` 在有分组列时本来就认。没有这些 `picks` 时与上面逐字相同。）
    4. layout 是 `matrix`（对称矩阵）→ 画不了（`matrix_unsupported`）：模板没有热图。"""
    s = entry.summary
    cols = s.columns
    ids = set(s.id_like)
    nums = [c for c in cols if numeric_like(s.per_column[c]) and c not in ids]
    # 误差列（名字是 `sd` / `sem` / `mass_se` 一类）不是测量值：不进纵轴 / 横轴的候选——除非用户自己点了它（`picks`），
    # 或者去掉之后一个数值列都不剩（那就当普通数值列，别让表画不了）
    py = picks.get("y")
    asked = {
        v for v in [*(py if isinstance(py, list) else [py]), picks.get("x")] if isinstance(v, str)
    }
    # 用户在话里按词点名的列（`hint`）同样算「自己点了」：`plot stderr` 要画的就是 stderr 那一列
    asked |= set(adf._mentioned(hint, cols)) if hint else set()
    nums_all = list(nums)  # 下拉里仍然列得出误差列：用户自己点它是允许的
    errs = {c for c in error_columns(s) if c not in asked}
    if errs and any(c not in errs for c in nums):
        nums = [c for c in nums if c not in errs]
    else:
        errs = set()
    # 已汇总的表里的样本量列（`n` / `count`，有误差列明确配着另一列）也不是测量值 / 横轴；用户自己点了它则不去。分两步：去掉它绝不会把误差列放回来
    sizes = {c for c in sample_size_columns(s) if c not in asked} if errs else set()
    if sizes and any(c not in sizes for c in nums):
        nums = [c for c in nums if c not in sizes]
    else:
        sizes = set()
    errs_and_sizes = errs | sizes
    dates = [c for c in cols if s.per_column[c].kind == "date" and c not in ids]
    cats = [c for c in cols if c in s.groups and c not in ids]
    cats_all = list(cats)
    # 点名当分组的整数编码列（`knockout` 0 / 1）：它不再是测量值 / 横轴候选；剩不下任何数值列就当没点名（回到规则结果）
    nc: str | None = None
    gp_named = picks.get("group")
    if isinstance(gp_named, str) and numcat_pick_ok(s, gp_named) and gp_named not in cats:
        if any(c != gp_named for c in nums):
            nc = gp_named
            nums = [c for c in nums if c != nc]
    if s.layout == "matrix":
        return "matrix_unsupported"
    if not nums:
        mixed = any(s.per_column[c].kind == "mixed" for c in cols)
        return "value_column_mixed" if mixed else "no_numeric_column"
    named = adf._mentioned(hint, cols) if hint else []
    wide_x = cols[0] if s.layout == "wide" and cols[0] in (nums + dates) else None
    # --- y
    ys: list[str]
    p = picks.get("y")
    multi: list[str] | None = None
    if isinstance(p, list):  # 点名多列纵轴（只在列客观上合格时采纳，否则当没给）
        if len(p) == 1 and p[0] in nums:
            p = p[0]
        else:
            multi = list(p) if all(isinstance(c, str) for c in p) and multi_y_ok(s, p) else None
            p = None
    named_y = [c for c in named if c in nums]
    if multi is not None:
        ys = multi
    elif p in nums:
        ys = [p]
    elif p == "unsure":
        ys = [nums[-1]]
    elif named_y:
        ys = [named_y[0]]
    elif len(nums) == 1:
        ys = [nums[0]]
    elif wide_x is not None and 2 <= len([c for c in nums if c != wide_x]) <= 3 and not cats:
        ys = [c for c in nums if c != wide_x]
    else:
        return NeedsChoice("pick_value", _col_options("y", nums, s), _unsure("y"))
    # --- 分组列（`picks.group == "none"`：用户明确不要分组，不走下面的挑选与提问）
    gp0 = picks.get("group")
    if isinstance(gp0, str) and gp0 != "none" and gp0 not in cats and id_group_ok(s, gp0):
        # 名字像编号、但取值重复出现的文字列（`sample` 一类）：被点名当分组才采纳；没点名时仍永不当分组
        cats = [gp0] + cats
        cats_all = [c for c in cols if c in cats_all or c == gp0]
    if nc is not None:
        cats = [nc] + cats
        cats_all = [c for c in cols if c in cats_all or c == nc]
    if picks.get("group") == "none":
        cats = []
    elif len(cats) > 1:
        gp = picks.get("group")
        named_c = [c for c in named if c in cats]
        if gp in cats:
            cats = [gp] + [c for c in cats if c != gp]
        elif gp == "unsure":
            pass
        elif named_c:
            cats = [named_c[0]] + [c for c in cats if c != named_c[0]]
        else:
            return NeedsChoice("pick_group", _col_options("group", cats, s), _unsure("group"))
    # --- x
    # 横轴候选：日期列 + 数值列；夹着少量文字的数值列（`numeric_like`，与 y 同一阈值）也行——那几行不画并报告（`default_candidates` 的 `mixed_cells`）
    xs = [
        c
        for c in dates + nums
        if c not in ys and (s.per_column[c].kind == "date" or numeric_like(s.per_column[c]))
    ]
    x_ord: str | None = None
    px = picks.get("x")
    named_x = [c for c in named if c in xs]
    if px == "none":  # 用户明确不要有序横轴：不出 trend / relationship，也不问
        pass
    elif len(xs) == 1:
        x_ord = xs[0]
    elif len(xs) > 1:
        if px in xs:
            x_ord = px
        elif named_x:
            x_ord = named_x[0]
        elif wide_x in xs:
            x_ord = wide_x
        elif not cats:
            if px == "unsure":
                x_ord = (dates or xs)[0]
            else:
                return NeedsChoice("pick_x", _col_options("x", xs, s), _unsure("x"))
    # 下拉里的分组：文字分组列，再加上客观上能当整数编码分组的列（不含此刻的测量值）。没有模型时默认选择不变，只是多一个可选项
    options = {
        "y": nums_all,
        "group": [c for c in cols if c in cats_all or (c in s.numcat and c not in ys)],
        "x": [c for c in cols if c in xs or (c in errs_and_sizes and c not in ys)],
    }
    return _Roles(
        tuple(ys),
        tuple(cats),
        x_ord,
        bool(x_ord and numeric_like(s.per_column[x_ord])),
        options,
        (nc,) if nc is not None and nc in cats else (),
    )


#: 列角色挑选的公开入口（`ai_data_roles` 用：问「规则此刻会怎么选 / 问什么」，不绕开它）；返回值同 `_resolve_roles`
Roles = _Roles


def resolve_roles(
    entry: adf.TableEntry, hint: str | None, picks: dict
) -> Roles | NeedsChoice | str:
    return _resolve_roles(entry, hint, picks)


def _recipe(entry: adf.TableEntry, code: str, family: str, layers, ys, **roles) -> FigureRecipe:
    return FigureRecipe(
        table=entry.path,
        sheet=entry.sheet,
        family=family,
        layers=tuple(Layer(*lay) for lay in layers),
        y=tuple(ys),
        why=code,  # 确定性备选的 `why` 就是理由码（前端按码取文案）；模型备选才是一句话
        **roles,
    )


#: 对值算汇总 / 分布的图元：已汇总的表（每个位置一行、带现成误差列）不画它们（`summarised_not_raw`）。带汇总方式的层（均值 ± 误差、均值线）另外按 `summary` 认
_SUMMARISING_MARKS = frozenset(
    {"box", "violin", "hist", "ecdf", "density", "band", "errorbar", "hline_summary"}
)


def summarising_mark(rec: FigureRecipe) -> str | None:
    """这份配方里第一个「对 y 值算汇总 / 分布」的层（`图元` 或 `图元:汇总方式`）（均值 ± 误差、箱线、小提琴、ECDF、直方图、核密度、误差带 / 误差线、均值线；带汇总方式的层也算）；没有 = None。"""
    for layer in rec.layers:
        if layer.summary != SUMMARY_NONE:
            return f"{layer.mark}:{layer.summary}"
        if layer.mark in _SUMMARISING_MARKS:
            return layer.mark
    return None


def _one_row_per_cell(
    values: adf.DataSummary, cols: list[str], y: str, x: str | None = None
) -> bool:
    """这份表「每个位置恰好一行」：只看真会画的行（`cols` 的格子都非空、纵轴有数、`x`（趋势的数值 / 日期横轴）有值），这些行的（`cols` 取值[, x 取值]）互不相同，
    且至少两行。已汇总的表（均值 + 误差，一行一个均值）才满足；有重复测量的表不满足。"""
    v = _Values(values)
    ys = v.nums(y)
    keys = v.keys(list(cols))
    xs = v.nums(x) if x is not None else [0.0] * len(ys)
    seen: set = set()
    for k, yv, xv in zip(keys, ys, xs):
        if yv is None or xv is None or any(not part for part in k):
            continue
        key = (k, xv)
        if key in seen:
            return False
        seen.add(key)
    return len(seen) >= 2


def _summarised_shape(
    entry: adf.TableEntry, values: adf.DataSummary | None, roles: _Roles, unit: str | None
) -> dict | None:
    """表是不是「已汇总」的形状：**只有这两种都满足才是**——①单个测量列配着一个误差列（`paired_error`，名字规则同 `adf.error_kind`，配不准 = 不是）；
    ②每个位置恰好一行（`_one_row_per_cell`；有序横轴时是分组 × 分面 × 横轴取值，否则是类别 × 分组）。返回
    `{"shape": "trend" | "category", "err", "kind", "x", "group", "facet"}`（给 `line_err` / `points_err` 用的列角色），不是 = None。
    没有有序横轴、也没有分组列时，表里**恰好一个**每格都不同的文字列（一行一个组，`group, mean, sd, n`；列名像编号的 `sample, mean, err` 也算）当类别轴——只用于这里的 `*_err` 备选，规则别处仍把它当标签。
    要带行数据的小表；个体编号（`unit`）、合画、把列当分组、多列纵轴一概不是（宁可不画误差线，不猜）。"""
    s = entry.summary
    if (
        values is None
        or not values.rows
        or s.large is not None
        or roles.stack
        or unit is not None
        or getattr(entry, "merge", None) is not None
        or len(roles.ys) != 1
    ):
        return None
    y, x, cats = roles.ys[0], roles.x_ord, roles.cats
    err = paired_error(s, y, (x,) if x else ())
    if err is None:
        return None
    if (
        s.per_column[err].kind != "numeric"
    ):  # 夹着文字 / 日期的误差列：校验不会放行，别为它剔掉别的备选
        return None
    ev, yv = _Values(values), _Values(values).nums(y)
    if any(e is not None and e < 0 and yy is not None for e, yy in zip(ev.nums(err), yv)):
        return None
    kind = error_kind_of(s, err)
    group = cats[0] if cats else None
    facet = cats[1] if len(cats) == 2 else None
    if x is not None:
        if not _one_row_per_cell(values, [c for c in (group, facet) if c], y, x):
            return None
        return {"shape": "trend", "err": err, "kind": kind, "x": x, "group": group, "facet": facet}
    if cats:  # 类别 × 分组各一行（`cats[0]` 是比较的横轴，恰好两个分组列时另一个做颜色分组）
        if not _one_row_per_cell(values, [c for c in (cats[0], facet) if c], y):
            return None
        return {
            "shape": "category",
            "err": err,
            "kind": kind,
            "x": cats[0],
            "group": facet,
            "facet": None,
        }
    v = _Values(values)
    labels = []
    for c in s.columns:
        # 名字像编号的列（`sample` / `id`）也算：已汇总的表里它就是那一行的名字，表里没有别的轴可当类别
        if s.per_column[c].kind != "text" or c in s.groups or c == err:
            continue
        cells = [t for t in v.cells(c) if t]
        if 2 <= len(cells) <= MAX_RANKING_ITEMS and len(set(cells)) == len(cells):
            labels.append(c)
    if len(labels) == 1 and _one_row_per_cell(values, [labels[0]], y):
        return {
            "shape": "category",
            "err": err,
            "kind": kind,
            "x": labels[0],
            "group": None,
            "facet": None,
        }
    return None


def default_candidates(
    profile: adf.FolderProfile,
    *,
    table: str | None = None,
    sheet: str | None = None,
    confirmed: Confirmed,
    hint: str | None = None,
    values: adf.DataSummary | None = None,
    picks: dict | None = None,
    cells: dict[str, CellRule] | None = None,
) -> Candidates | NeedsChoice | MixedCells:
    """不调模型的备选：对选定的表给 2–4 个现在**有模板能画**（`RENDERABLE`）的配方，全部经 `validate_recipe` + `enforce_order`。

    * 选表、列角色挑选的规则见 `pick_table` / `_resolve_roles`（有歧义 → `NeedsChoice`，不猜）；`hint` 只做「按词点名列 / 文件」的确定性
      匹配；`unit` / `paired` 只来自 `confirmed`（`id_like` / `repeats` 只是线索——编号列存在、编号重复、用户还没表态且能画趋势时问一次
      「同一对象重复测量吗」，`unsure` = 当独立数据画、不带编号；确认「重复测量」且有多个编号列时再问用哪一列）。
    * 每个备选组是「按顺序试、第一个合格的入选」的几个等价画法；组的顺序就是推荐顺序：有序横轴在时趋势 / 关系图在前，随后是分组比较
      （原始点优先）与分布，柱加误差线排最后（`enforce_order` 在此之上再强制一遍）；至多取 4 组。取值类规则（每组 n、非负、重复 x）要 `values`（选中那张表带行数据的摘要）；不给就只判结构。
    * 用到的数值列夹着少量文字（`numeric_like`，非数字占比 ≤ 20%）且 `cells` 里没有该列的处理规则 → 返回 `MixedCells`（先问用户，不出图）；
      有规则就把规则带进每个备选的配方（`FigureRecipe.cells`）。
    * 一个都画不了 → `Candidates(items=())`，带 `reasons`；被护栏剔除的备选在 `dropped`。
    * `roles.ys` 多于一列时，只返回画全这些列的备选（趋势类）；只画第一列的去掉并记进 `dropped`（`roles_invalid` / `multi_y_not_drawn`），一个不剩就退回单列重算。
    """
    picks = dict(picks or {})
    chosen = pick_table(profile, table, sheet, hint)
    if isinstance(chosen, NeedsChoice):
        return chosen
    if isinstance(chosen, str):
        return Candidates(table or "", sheet, (), reasons=(chosen,), confirmed=confirmed)
    entry = chosen
    s = entry.summary
    roles = _resolve_roles(entry, hint, picks)
    if isinstance(roles, NeedsChoice):
        return roles
    if isinstance(roles, str):
        return Candidates(entry.path, entry.sheet, (), reasons=(roles,), confirmed=confirmed)
    # 重复测量：只有用户确认过才带编号列
    eff = confirmed
    if (
        confirmed.design == "repeated"
        and confirmed.unit_col is not None
        and confirmed.unit_col not in s.per_column
    ):
        eff = Confirmed("repeated")  # 回答里的列名不是这张表的列：当没给，走下面的唯一 / 提问
        confirmed = eff
    promoted = [
        c for c in roles.cats if c in s.id_like
    ]  # 被点名当分组的编号样文字列：不再兼作「重复测量的个体编号」
    if confirmed.design == "repeated" and confirmed.unit_col is None:
        ids = [c for c in s.id_like if c not in promoted]
        if len(ids) == 1:
            eff = Confirmed("repeated", ids[0])
        elif picks.get("unit") in ids:
            eff = Confirmed("repeated", picks["unit"])
        elif picks.get("unit") == "unsure" or not ids:
            eff = Confirmed()
        else:
            return NeedsChoice("pick_unit", _col_options("unit", ids, s), _unsure("unit"))
    # G-018：折线只用于有序横轴。读懂数据判过「规则选作横轴的这一列是测量值」（`picks.x_measure` = 那一列的列名）时，这一列与纵轴是两个独立测量，
    # 趋势类不出、关系类（散点）排第一。事实绑在列名上：用户换了横轴（`roles.x_ord` 不是它）就不生效；没带这个键 = 与从前逐字相同。
    # 没有关系图可换（x 不是数值 / 关系图资格不过）或纵轴是多列（散点画不全这几列）就不拿掉趋势类——宁可保留从前的结果，也不出零备选、不丢列
    x_independent = (
        roles.x_ord is not None
        and roles.x_numeric
        and isinstance(picks.get("x_measure"), str)
        and picks["x_measure"] == roles.x_ord
        and not roles.stack
        # 多列纵轴只有趋势类画得全（每列一条线）：拿掉趋势类会把选好的列悄悄丢成一列，所以这时不生效、保持从前的结果
        and len(roles.ys) == 1
    )
    can_trend = roles.x_ord is not None and not x_independent
    repeats = s.repeats and not (promoted and set(s.id_like) <= set(promoted))
    if eff.design is None and repeats and can_trend and picks.get("design") != "unsure":
        return NeedsChoice(
            "pick_design",
            (
                Option("design_independent", {}, {"design": "independent"}),
                Option("design_repeated", {}, {"design": "repeated"}),
            ),
            _unsure("design"),
        )
    unit = eff.unit_col if eff.design == "repeated" else None
    y = roles.ys[0]
    role_info = {
        "x": roles.x_ord,
        "y": list(roles.ys),
        "group": roles.cats[0] if roles.cats else None,
        "facet": roles.cats[1] if len(roles.cats) == 2 else None,
        "unit": unit,
        "options": {k: list(v) for k, v in roles.options.items()},
        # 「把列当分组」：`y` 就是被当分组的那几列（与每个备选实际画的列一致），没有横轴、没有分组；只有这时才多这一项（其余响应逐字不变）
        **({"stack": True} if roles.stack else {}),
    }
    # 「把列当分组」之后，后面的备选 / 资格都按转换后的长表（`stack_view`）来：一个值列加一个类别列，类别列像文字分组列一样做比较的横轴 / 分布的颜色分组
    view_entry, view_values = entry, values
    if roles.stack:
        view_entry = replace(entry, summary=stack_view(s, roles.stack))
        if values is not None and values.rows:
            view_values = stack_view(values, roles.stack)
        vname, cname = stack_names(roles.stack)
        roles = replace(roles, ys=(vname,), cats=(cname,))
        y = vname
    cells = dict(cells or {})
    # 夹着少量文字的数值列：y 列与有序横轴列同一套规则（先问 / 懒模式默认「不画那几行」，规则进配方）
    mixed = (
        []  # 把列当分组：被当分组的列都是纯数值列（`stack_ok`），没有夹着文字的
        if view_entry is not entry
        else mixed_columns(s, (*roles.ys, *([roles.x_ord] if roles.x_ord else [])))
    )
    pending = [c for c in mixed if c not in cells]
    if pending and values is not None and values.rows:
        context = tuple(
            dict.fromkeys([*(roles.cats[:2]), *([roles.x_ord] if roles.x_ord else []), *roles.ys])
        )
        return mixed_cells_question(values, pending, context)
    ok_families = {
        e.family
        for e in eligible(view_entry, eff, values=view_values, numcat=roles.numcat)
        if e.status in ("ok", "partial") and e.renderable
    }
    x_independent = x_independent and "relationship" in ok_families
    groups: list[list[tuple[str, FigureRecipe]]] = []
    dropped: list[Dropped] = []
    if x_independent and "trend" in ok_families:
        dropped.append(Dropped(0, (Problem("roles_invalid", "x", "x_is_measure"),)))

    def grp(*alts: tuple[str, FigureRecipe]) -> None:
        groups.append(list(alts))

    cats = roles.cats
    # 已汇总的表（每个位置一行 + 现成误差列）：先给「按现成误差列画误差线」的那一张；下面对值再算汇总 / 分布的备选一律不给（G-053）
    summ = _summarised_shape(entry, values, roles, unit) if view_entry is entry else None
    if summ is not None:
        err_roles = {"err": summ["err"], "err_kind": summ["kind"]}
        if summ["shape"] == "category" and not cats:
            # 类别轴取的是「每行一个名字」的那一列（规则别处不当它是分组列）：右栏的「分组」一行要如实显示它，不能写着没有分组而图上按它排开
            opts = role_info["options"]
            role_info["group"] = summ["x"]
            opts["group"] = [summ["x"], *(c for c in opts.get("group", []) if c != summ["x"])]
        if summ["shape"] == "category":
            # 已汇总的分组表没有横轴可选：误差列不是横轴的备选（其它数值列照旧列着）；一列都不剩时右栏不显示「横轴」那一行
            role_info["options"]["x"] = [
                c for c in role_info["options"].get("x", []) if c != summ["err"]
            ]
        if summ["shape"] == "trend":
            # 横轴是独立测量值（`x_measure`，G-018）时不连线：现成误差列不能把被拿掉的折线带回来，只留下面的散点类
            if "trend" in ok_families and not x_independent:
                grp(
                    (
                        "line_err",
                        _recipe(
                            entry,
                            "line_err",
                            "trend",
                            [("points", "none", "main"), ("line", "none", "overlay")],
                            roles.ys,
                            x=summ["x"],
                            **({"group": summ["group"]} if summ["group"] else {}),
                            **({"facet": summ["facet"]} if summ["facet"] else {}),
                            **err_roles,
                        ),
                    )
                )
        else:
            grp(
                (
                    "points_err",
                    _recipe(
                        entry,
                        "points_err",
                        "comparison",
                        [("points", "none", "main")],
                        roles.ys,
                        x=summ["x"],
                        group=summ["group"],
                        **err_roles,
                    ),
                )
            )
    # 有序横轴在时：趋势 / 关系图是主图，分组列做颜色分组、恰好两个分组列时另一个做分面；没有横轴时分组列是比较的横轴
    gx = {"group": cats[0], **({"facet": cats[1]} if len(cats) == 2 else {})} if cats else {}
    if roles.x_ord is not None:
        x = roles.x_ord
        if "trend" in ok_families and not x_independent:
            alts = []
            if unit:
                alts.append(
                    (
                        "line_units",
                        _recipe(
                            entry,
                            "line_units",
                            "trend",
                            [("line", "none", "main"), ("points", "none", "overlay")],
                            roles.ys,
                            x=x,
                            unit=unit,
                            **gx,
                        ),
                    )
                )
            alts += [
                (
                    "line_points",
                    _recipe(
                        entry,
                        "line_points",
                        "trend",
                        [("points", "none", "main"), ("line", "none", "overlay")],
                        roles.ys,
                        x=x,
                        **gx,
                    ),
                ),
                (
                    "line_band",
                    _recipe(
                        entry,
                        "line_band",
                        "trend",
                        [
                            ("line", "mean_sd", "main"),
                            ("band", "mean_sd", "overlay"),
                            ("points", "none", "overlay"),
                        ],
                        roles.ys,
                        x=x,
                        **gx,
                    ),
                ),
            ]
            grp(*alts)
            if unit:  # 个体线之外再给一张跨个体的汇总
                grp(alts[-1])
        if roles.x_numeric and "relationship" in ok_families:
            grp(
                (
                    "scatter_fit",
                    _recipe(
                        entry,
                        "scatter_fit",
                        "relationship",
                        [("points", "none", "main"), ("fit_line", "none", "overlay")],
                        [y],
                        x=x,
                        **({"group": cats[0]} if cats else {}),
                    ),
                ),
                (
                    "scatter",
                    _recipe(
                        entry,
                        "scatter",
                        "relationship",
                        [("points", "none", "main")],
                        [y],
                        x=x,
                        **({"group": cats[0]} if cats else {}),
                    ),
                ),
            )
            if x_independent:
                # 趋势类拿掉之后关系图是主图：不带拟合线的散点单独再给一张（同一组里它只是拟合线不合格时的退路），备选才不少于两个
                grp(
                    (
                        "scatter",
                        _recipe(
                            entry,
                            "scatter",
                            "relationship",
                            [("points", "none", "main")],
                            [y],
                            x=x,
                            **({"group": cats[0]} if cats else {}),
                        ),
                    )
                )
    if cats and "comparison" in ok_families:
        xc = cats[0]
        g2 = cats[1] if len(cats) == 2 else None
        grp(
            (
                "points_mean",
                _recipe(
                    entry,
                    "points_mean",
                    "comparison",
                    [("jitter", "none", "main"), ("hline_summary", "mean_sd", "overlay")],
                    [y],
                    x=xc,
                    group=g2,
                ),
            )
        )
        grp(
            *(
                (
                    code,
                    _recipe(
                        entry,
                        code,
                        "comparison",
                        [(mark, "none", "main"), ("jitter", "none", "overlay")],
                        [y],
                        x=xc,
                        group=g2,
                    ),
                )
                for code, mark in (("box_points", "box"), ("violin_points", "violin"))
            )
        )
    if cats and "distribution" in ok_families:
        grp(
            (
                "ecdf_groups",
                _recipe(
                    entry,
                    "ecdf_groups",
                    "distribution",
                    [("ecdf", "none", "main")],
                    [y],
                    group=cats[0],
                ),
            )
        )
    if cats and "comparison" in ok_families:
        grp(
            (
                "bar_error",
                _recipe(
                    entry,
                    "bar_error",
                    "comparison",
                    [("bar", "mean_sd", "main"), ("errorbar", "mean_sd", "overlay")],
                    [y],
                    x=cats[0],
                ),
            )
        )
    if not cats and roles.x_ord is None and "distribution" in ok_families:
        for code, layers in (
            ("hist", [("hist", "none", "main")]),
            ("ecdf", [("ecdf", "none", "main")]),
            ("box_points_single", [("box", "none", "main"), ("jitter", "none", "overlay")]),
            ("density", [("density", "none", "main")]),
        ):
            grp((code, _recipe(entry, code, "distribution", layers, [y])))
    if not groups:
        return Candidates(
            entry.path,
            entry.sheet,
            (),
            reasons=("no_renderable_family",),
            confirmed=eff,
            roles=role_info,
        )
    picked: list[tuple[str, FigureRecipe]] = []
    y_filtered = False
    for gi, alts in enumerate(groups):
        if summ is not None:
            # 已汇总的表：均值 ± 误差、箱线、分布、均值线都是对「汇总值」再算一次——一个也不给（连同「按顺序试」里还没轮到的），记下为什么（G-053）
            kept: list[tuple[str, FigureRecipe]] = []
            for code, rec in alts:
                bad = None if rec.err is not None else summarising_mark(rec)
                if bad:
                    dropped.append(Dropped(gi, (Problem("summarised_not_raw", "layers", bad),)))
                else:
                    kept.append((code, rec))
            alts = kept
        for code, rec in alts:
            used_cols = (
                *rec.y,
                *([rec.x] if rec.x and rec.family in ("trend", "relationship") else []),
            )
            rules = tuple(cells[c] for c in used_cols if c in cells and c in mixed)
            if rules:
                rec = replace(rec, cells=rules)
            if roles.stack:  # 把列当分组：每个备选都带这条事实（y / 类别列已是长表里的两列）
                rec = replace(rec, stack=roles.stack)
            if (
                roles.numcat
            ):  # 点名的整数编码列：配方里这一列带上「按类别用」的事实（只对它真在类别角色里的配方）
                cat_x = (
                    "cat" in FAMILIES[rec.family].x_types
                    and "ord" not in FAMILIES[rec.family].x_types
                )
                used_cat = {rec.group, rec.facet, *([rec.x] if cat_x else [])}
                rec = replace(rec, numcat=tuple(c for c in roles.numcat if c in used_cat))
            problems = validate_recipe(profile, rec, confirmed=eff, values=values)
            if len(rec.y) > 1 and any(p.code == "dual_axis_unsupported" for p in problems):
                # 量纲不同的几列不画同一张图（不做双 y 轴）：退到第一列，不静默丢掉也不报错
                rec = replace(
                    rec,
                    y=rec.y[:1],
                    cells=tuple(r for r in rec.cells if r.column in (rec.y[0], rec.x)),
                )
                problems = validate_recipe(profile, rec, confirmed=eff, values=values)
            if not problems and len(roles.ys) > 1 and tuple(rec.y) != tuple(roles.ys):
                # `roles.y` 报几列，每个备选就得画这几列：只用第一列的（关系图、分组比较、分布）去掉，记进 dropped
                problems = [Problem("roles_invalid", "y", "multi_y_not_drawn")]
                y_filtered = True
            if not problems:
                if x_independent and any(c == code for c, _ in picked):
                    break  # 退路与前一组落到同一个画法：不重复出（不记 dropped）
                picked.append((code, rec))
                break
            dropped.append(Dropped(gi, tuple(problems)))
    if y_filtered and not picked:
        # 没有一个备选能画全这几列（如量纲不同退到第一列）：回到单列的原有行为，不出零备选
        return default_candidates(
            profile,
            table=table,
            sheet=sheet,
            confirmed=confirmed,
            hint=hint,
            values=values,
            picks={**picks, "y": roles.ys[0]},
            cells=cells,
        )
    res = enforce_order(
        profile, [r for _, r in picked][:MAX_CANDIDATES], confirmed=eff, values=values
    )
    by_recipe = {id(r): c for c, r in picked}
    items = tuple(Candidate(by_recipe[id(r)], r) for r in res.kept)
    dropped += list(res.dropped)
    if not items:
        return Candidates(
            entry.path,
            entry.sheet,
            (),
            tuple(dropped),
            ("all_candidates_rejected",),
            eff,
            role_info,
        )
    return Candidates(entry.path, entry.sheet, items, tuple(dropped), (), eff, role_info)


__all__ = [
    "ASK_FIRST",
    "CANDIDATE_CODES",
    "Candidate",
    "CellRule",
    "Candidates",
    "MixedCells",
    "NOTHING_REASONS",
    "NeedsChoice",
    "Option",
    "QUESTION_CODES",
    "check_cell_rules",
    "default_candidates",
    "pick_table",
    "COMMON_REJECTIONS",
    "ERROR_CODES",
    "FAMILIES",
    "MARKS",
    "MARK_RULES",
    "REJECTIONS",
    "Confirmed",
    "Eligibility",
    "FigureRecipe",
    "Layer",
    "OrderResult",
    "Problem",
    "RecipeError",
    "eligible",
    "enforce_order",
    "renderable",
    "validate_recipe",
]
