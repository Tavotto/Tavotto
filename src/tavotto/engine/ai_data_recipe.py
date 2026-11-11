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
)
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


def _has_control(s: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in s)


def _choice(value, allowed: tuple[str, ...], what: str) -> str:
    if not isinstance(value, str):
        raise RecipeError("recipe_malformed", what)
    if value not in allowed:
        raise RecipeError("recipe_unknown_value", what)
    return value


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


def _type_ok(token: str, s: adf.DataSummary, col: str) -> bool:
    kind = s.per_column[col].kind
    if token == "num":
        return kind == "numeric"
    if token == "ord":
        return kind in ("numeric", "date")
    if token == "cat":
        return col in s.groups
    if token == "label":
        return kind == "text"
    raise ValueError(token)


def _fits(tokens: tuple[str, ...], s: adf.DataSummary, col: str) -> bool:
    return any(_type_ok(t, s, col) for t in tokens)


def _unit_of(col: str) -> str | None:
    """列名后缀里的单位（只认 `ai_data_figure.UNITS` 闭集）；认不出 → None。"""
    m = adf._UNIT_SUFFIX.match(col)
    return adf.UNITS.get(m.group("unit")) if m else None


# --------------------------------------------------------------------------- 取值（需要带行数据的 DataSummary）
class _Values:
    def __init__(self, s: adf.DataSummary):
        self.s = s
        self.idx = {c: i for i, c in enumerate(s.columns)}
        self._cache: dict[str, list] = {}

    def cells(self, col: str) -> list[str]:
        return [r[self.idx[col]].strip() for r in self.s.rows]

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
        return list(zip(*cs)) if cs else [()] * len(self.s.rows)


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
        if not xs_by or min(len(x) for x in xs_by.values()) < MIN_X_TREND:
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
    return P


# --------------------------------------------------------------------------- 校验
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
    spec = FAMILIES[recipe.family]
    P: list[Problem] = []

    def need(col: str | None, fieldname: str, tokens: tuple[str, ...]) -> bool:
        if col is None:
            return False
        if col not in s.per_column:
            P.append(Problem("column_not_found", fieldname))
            return False
        if not _fits(tokens, s, col):
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
    cols_ok &= need(recipe.x, "x", spec.x_types) if recipe.x is not None and spec.x_types else True
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
    # 取值（只在列都对得上时）
    if values is not None and values.rows and cols_ok:
        if values.columns != s.columns or values.sheet != s.sheet:
            raise ValueError("values 与 profile 里那张表的列 / 工作表对不上")
        P.extend(_value_problems(recipe, spec, s, _Values(values), unit_confirmed))
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
    return [c for c in s.columns if _fits(tokens, s, c)]


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
    entry: adf.TableEntry, confirmed: Confirmed, *, values: adf.DataSummary | None = None
) -> list[Eligibility]:
    """一张表对十二类问题各自的资格（按 `FAMILIES` 顺序）。结构判定只看摘要；给了 `values`（同一张表带行数据的摘要）
    才在最有利的列组合（至多各取前几列试）上判取值类图元的拒绝，没给就把相关码列在 `unchecked`。"""
    s = entry.summary
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
QUESTION_CODES = ("pick_table", "pick_value", "pick_group", "pick_x", "pick_design", "pick_unit")
CANDIDATE_CODES = (
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
    "value_column_mixed",  # 数值列里混着文字（`<0.1`、`n/a`）：不猜，请用户先整理
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

    def to_dict(self) -> dict:
        return {"code": self.code, "params": dict(self.params), "answer": dict(self.answer)}


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


def _is_id_like_col(s: adf.DataSummary, col: str) -> bool:
    return col in s.id_like


def _usable_table(t: adf.TableEntry) -> bool:
    s = t.summary
    n = s.row_count if s.row_count is not None else len(s.rows)
    return n >= 1 and any(
        s.per_column[c].kind == "numeric" and c not in s.id_like for c in s.columns
    )


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


def _resolve_roles(
    entry: adf.TableEntry, hint: str | None, picks: dict
) -> _Roles | NeedsChoice | str:
    """列角色的挑选规则（**一处文字定义，下面是实现**；保守：能由结构或用户点名唯一确定就确定，有歧义就问，绝不猜）。

    记号：`ids` = `id_like` 列（**永不**当 y / x / 分组，只可能经用户确认成 `unit`）；`nums` = 数值列（kind `numeric`，去掉 ids）；
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
    4. layout 是 `matrix`（对称矩阵）→ 画不了（`matrix_unsupported`）：模板没有热图。"""
    s = entry.summary
    cols = s.columns
    ids = set(s.id_like)
    nums = [c for c in cols if s.per_column[c].kind == "numeric" and c not in ids]
    dates = [c for c in cols if s.per_column[c].kind == "date" and c not in ids]
    cats = [c for c in cols if c in s.groups and c not in ids]
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
    named_y = [c for c in named if c in nums]
    if p in nums:
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
    # --- 分组列
    if len(cats) > 1:
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
    xs = [c for c in dates + nums if c not in ys]
    x_ord: str | None = None
    px = picks.get("x")
    named_x = [c for c in named if c in xs]
    if len(xs) == 1:
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
    return _Roles(
        tuple(ys), tuple(cats), x_ord, bool(x_ord and s.per_column[x_ord].kind == "numeric")
    )


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


def default_candidates(
    profile: adf.FolderProfile,
    *,
    table: str | None = None,
    sheet: str | None = None,
    confirmed: Confirmed,
    hint: str | None = None,
    values: adf.DataSummary | None = None,
    picks: dict | None = None,
) -> Candidates | NeedsChoice:
    """不调模型的备选：对选定的表给 2–4 个现在**有模板能画**（`RENDERABLE`）的配方，全部经 `validate_recipe` + `enforce_order`。

    * 选表、列角色挑选的规则见 `pick_table` / `_resolve_roles`（有歧义 → `NeedsChoice`，不猜）；`hint` 只做「按词点名列 / 文件」的确定性
      匹配；`unit` / `paired` 只来自 `confirmed`（`id_like` / `repeats` 只是线索——编号列存在、编号重复、用户还没表态且能画趋势时问一次
      「同一对象重复测量吗」，`unsure` = 当独立数据画、不带编号；确认「重复测量」且有多个编号列时再问用哪一列）。
    * 每个备选组是「按顺序试、第一个合格的入选」的几个等价画法；组的顺序就是推荐顺序：有序横轴在时趋势 / 关系图在前，随后是分组比较
      （原始点优先）与分布，柱加误差线排最后（`enforce_order` 在此之上再强制一遍）；至多取 4 组。取值类规则（每组 n、非负、重复 x）要 `values`（选中那张表带行数据的摘要）；不给就只判结构。
    * 一个都画不了 → `Candidates(items=())`，带 `reasons`；被护栏剔除的备选在 `dropped`。
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
    if confirmed.design == "repeated" and confirmed.unit_col is None:
        ids = [c for c in s.id_like]
        if len(ids) == 1:
            eff = Confirmed("repeated", ids[0])
        elif picks.get("unit") in ids:
            eff = Confirmed("repeated", picks["unit"])
        elif picks.get("unit") == "unsure" or not ids:
            eff = Confirmed()
        else:
            return NeedsChoice("pick_unit", _col_options("unit", ids, s), _unsure("unit"))
    can_trend = roles.x_ord is not None
    if eff.design is None and s.repeats and can_trend and picks.get("design") != "unsure":
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
    ok_families = {
        e.family
        for e in eligible(entry, eff, values=values)
        if e.status in ("ok", "partial") and e.renderable
    }
    groups: list[list[tuple[str, FigureRecipe]]] = []

    def grp(*alts: tuple[str, FigureRecipe]) -> None:
        groups.append(list(alts))

    cats = roles.cats
    # 有序横轴在时：趋势 / 关系图是主图，分组列做颜色分组、恰好两个分组列时另一个做分面；没有横轴时分组列是比较的横轴
    gx = {"group": cats[0], **({"facet": cats[1]} if len(cats) == 2 else {})} if cats else {}
    if roles.x_ord is not None:
        x = roles.x_ord
        if "trend" in ok_families:
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
            entry.path, entry.sheet, (), reasons=("no_renderable_family",), confirmed=eff
        )
    picked: list[tuple[str, FigureRecipe]] = []
    dropped: list[Dropped] = []
    for gi, alts in enumerate(groups):
        for code, rec in alts:
            problems = validate_recipe(profile, rec, confirmed=eff, values=values)
            if len(rec.y) > 1 and any(p.code == "dual_axis_unsupported" for p in problems):
                # 量纲不同的几列不画同一张图（不做双 y 轴）：退到第一列，不静默丢掉也不报错
                rec = replace(rec, y=rec.y[:1])
                problems = validate_recipe(profile, rec, confirmed=eff, values=values)
            if not problems:
                picked.append((code, rec))
                break
            dropped.append(Dropped(gi, tuple(problems)))
    res = enforce_order(
        profile, [r for _, r in picked][:MAX_CANDIDATES], confirmed=eff, values=values
    )
    by_recipe = {id(r): c for c, r in picked}
    items = tuple(Candidate(by_recipe[id(r)], r) for r in res.kept)
    dropped += list(res.dropped)
    if not items:
        return Candidates(
            entry.path, entry.sheet, (), tuple(dropped), ("all_candidates_rejected",), eff
        )
    return Candidates(entry.path, entry.sheet, items, tuple(dropped), (), eff)


__all__ = [
    "ASK_FIRST",
    "CANDIDATE_CODES",
    "Candidate",
    "Candidates",
    "NOTHING_REASONS",
    "NeedsChoice",
    "Option",
    "QUESTION_CODES",
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
