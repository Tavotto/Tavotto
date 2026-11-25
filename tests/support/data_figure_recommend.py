"""第 2 步「按科学意义推荐图型」的用例集、画法闭集与规则基线测量（只测量，不调模型、不联网）。

用例在 `tests/fixtures/data_figure_cases/recommend_cases.json`（40 条）；设计与结论在同目录 `RECOMMEND_NOTES.md`。**标准答案凭科学可视化的道理先写定**，
不依据规则 / 现有模板的输出调整；`expressible` 是事后对着规则量出来写回的（测试只校验它的形状，不断言它等于现算的值——下一个实现功能的代理会让它变）。

**用例字段**：`id` / `title` / `tier`（`pair` 最小对 / `plain` 普通 / `hard` 难例）/ `theme` / `lang`；`tables`（`{文件名: CSV}`，单表）；`hint`；
`roles`（每列的 `ai_data_roles.ROLES` 角色）；`picks`（`{y: [列], x: 列|"none", group: 列|"none"}`，可带 `design` / `unit`——用户确认过的事实）；
`identity`（数据的统计身份，`IDENTITIES`）；`task`（`TASKS`）与 `task_source`（`from_hint` / `from_data` / `needs_confirm`）；`confirm`（`needs_confirm` 时要问的事实，`CONFIRM_FACTS`）；
`acceptable_top`（排第一都算对的画法集合，`METHODS` 的键）；`forbidden`（`[{method, g}]`：不该出现在候选里或不该标推荐，只收 firm / strong 强度的准则）；
`derive`（可提示、不擅自做的派生，`DERIVES`；G-037）；`guidance`（依据的 G-id）；`reason`（`{data_fact, perceptual, user_purpose}` 三档各一句中文或 null，G-039）；
`pair`（最小对才有：`{id, side, differs_by}`）；`expressible`（事后量出来的天花板记录）。

**画法闭集 `METHODS`** 每项带状态：`coded`（`default_candidates` 有码）/ `legal_uncoded`（模板画得出来，但规则从不给）/ `no_template`（族有资格规则、没有模板）/
`missing`（规则和模板都没有）。`classify(recipe)` 把一份配方归到画法，不靠 `title_code` 字符串（将来模型出的配方也能归类）。
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as r,
    ai_data_roles as R,
    ai_data_template as tpl,
)

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "tests" / "fixtures" / "data_figure_cases"
RECOMMEND_FILE = CASES_DIR / "recommend_cases.json"
PROJECT = "recommend-bench-project"

# --------------------------------------------------------------------------- 任务闭集（定义见 RECOMMEND_NOTES.md）
TASKS: dict[str, str] = {
    "compare_groups": "比较几个独立分组某个量的高低 / 差别（组之间无顺序、不是同一批对象）",
    "trend": "看一个量随有序量（时间、位置、梯度）怎么变",
    "dose_response": "剂量（浓度）由低到高、反应随之变（横轴常跨几个数量级）",
    "relationship": "看两个不同的数值量有没有关联、怎么关联（无顺序、不是同一个量）",
    "agreement": "两个轴是同一个量的两种测法 / 预测与实测，看它们一致不一致",
    "distribution": "看一个量取值的分布形状：集中在哪、散多开、有没有离群",
    "part_whole": "看整体由哪几部分组成（部分互斥、合计为整体）",
    "paired_change": "同一批对象在两个条件下各测一次，看每个对象各自的变化",
    "ranking": "一堆互不相干的条目各有一个值，看谁高谁低",
    "intervals": "每个条目已有估计值与上下限，看区间",
    "pattern_in_matrix": "行 × 列的一张同类数值表（基因 × 样本、相关矩阵），找高低模式",
    "undetermined": "光看数据与那句话定不了任务（只与 needs_confirm 同用）",
}
TASK_SOURCES = ("from_hint", "from_data", "needs_confirm")
CONFIRM_FACTS: dict[str, str] = {
    "paired_design": "这些行是不是同一批对象的前后 / 配对测量",
    "error_kind": "误差列是 SD、SEM 还是 95% CI",
    "ratio_axis": "这个量是不是比值（决定能不能用对数轴画柱）",
    "same_quantity": "两个轴是不是同一个量的两种测法",
    "exclusive_parts": "这些部分是否互斥且合计为整体",
    "ordered_x": "这一列是有顺序的量还是无序类别",
    "zero_control": "剂量 0 的对照组怎么处理（线性轴 / 对数轴外单独标出 / 单独成组）",
}
DERIVES: dict[str, str] = {
    "difference": "差值（后 − 前、处理 − 对照）",
    "ratio": "比值 / 占比 / 倍数",
    "normalize": "归一化（相对初始值、相对对照）",
    "log": "取对数后再汇总（均值要在对数尺度上算，G-011）",
    "bin": "分箱",
    "aggregate": "聚合（按组求均值等）",
}
IDENTITIES = ("raw", "summary_with_error", "summary_no_error", "counts", "percent")
TIERS = ("pair", "plain", "hard")
LANGS = ("zh", "en", "mixed")
REASON_KEYS = ("data_fact", "perceptual", "user_purpose")
PAIR_DIFFERS = ("meaning", "hint", "n_rows", "n_per_group", "id_repeat", "scale")

# --------------------------------------------------------------------------- 准则 id → 强度（抄自 sciviz_guidance.json，2026-11-21，提交 f18b5f360 起 54 条；测试不读那个文件）
GUIDANCE_STRENGTH: dict[str, str] = {
    "G-001": "firm",
    "G-002": "firm",
    "G-003": "firm",
    "G-004": "firm",
    "G-005": "firm",
    "G-006": "firm",
    "G-007": "firm",
    "G-008": "strong",
    "G-009": "firm",
    "G-010": "strong",
    "G-011": "firm",
    "G-012": "strong",
    "G-013": "strong",
    "G-014": "strong",
    "G-015": "strong",
    "G-016": "strong",
    "G-017": "strong",
    "G-018": "strong",
    "G-019": "strong",
    "G-020": "strong",
    "G-021": "strong",
    "G-022": "strong",
    "G-023": "strong",
    "G-024": "strong",
    "G-025": "strong",
    "G-026": "firm",
    "G-027": "strong",
    "G-028": "strong",
    "G-029": "strong",
    "G-030": "firm",
    "G-031": "strong",
    "G-032": "strong",
    "G-033": "strong",
    "G-034": "strong",
    "G-035": "firm",
    "G-036": "firm",
    "G-037": "inference",
    "G-038": "firm",
    "G-039": "inference",
    "G-040": "strong",
    "G-041": "preference",
    "G-042": "preference",
    "G-043": "preference",
    "G-044": "preference",
    "G-045": "preference",
    "G-046": "preference",
    "G-047": "preference",
    "G-048": "preference",
    "G-049": "preference",
    "G-050": "preference",
    "G-051": "preference",
    # 2026-11-21 准则主人更新（sciviz 提交 f18b5f360）新增
    "G-052": "inference",
    "G-053": "strong",
    "G-054": "strong",
}
FORBIDDEN_STRENGTHS = frozenset({"firm", "strong"})

# --------------------------------------------------------------------------- 画法闭集
COMMON_STATUSES = ("coded", "legal_uncoded", "no_template", "missing")
#: 画法 id → (状态, 族, 一句话)。`coded` 的 14 个与 `ai_data_recipe.CANDIDATE_CODES` 一一对应（测试核）。
METHODS: dict[str, tuple[str, str, str]] = {
    # coded：default_candidates 有码
    "points_mean": ("coded", "comparison", "抖动点 + 均值线（SD）"),
    "box_points": ("coded", "comparison", "箱线 + 原始点"),
    "violin_points": ("coded", "comparison", "小提琴 + 原始点"),
    "bar_error": ("coded", "comparison", "均值柱 + 误差线"),
    "ecdf_groups": ("coded", "distribution", "分组经验累积分布"),
    "line_points": ("coded", "trend", "原始点 + 连线（有序横轴）"),
    "line_band": ("coded", "trend", "汇总线 + 误差带 + 原始点"),
    "line_units": ("coded", "trend", "逐个体连线（需确认个体编号）"),
    "scatter_fit": ("coded", "relationship", "散点 + 描述性拟合线"),
    "scatter": ("coded", "relationship", "散点"),
    "hist": ("coded", "distribution", "直方图"),
    "ecdf": ("coded", "distribution", "经验累积分布"),
    "density": ("coded", "distribution", "核密度"),
    "box_points_single": ("coded", "distribution", "单组箱线 + 原始点"),
    # 用表里现成的误差列画误差线（不再从原始值汇总；G-053）。写这套用例时它还是 `missing` 的 `errorbar_from_columns`，
    # 实现后按族落成这两个码——标准答案的含义没变，只是有了名字
    "points_err": ("coded", "comparison", "每个类别一个点 + 表里现成的误差线"),
    "line_err": ("coded", "trend", "点 + 连线 + 表里现成的误差线"),
    # 已汇总的分组表上的柱（从 0 起）+ 表里现成的误差线（G-053 / G-002）；不是推荐第一（G-050），所以任何用例的 `acceptable_top` 都不含它
    "bar_err": ("coded", "comparison", "均值柱 + 表里现成的误差线"),
    # legal_uncoded：模板画得出来、规则从不给
    "bar_points": ("legal_uncoded", "comparison", "均值柱 + 原始点"),
    "median_points": ("legal_uncoded", "comparison", "抖动点 + 中位数 / IQR"),
    "points_mean_logy": ("legal_uncoded", "comparison", "抖动点 + 均值线，纵轴取对数"),
    "step_line": ("legal_uncoded", "trend", "阶梯线"),
    "line_points_logx": ("legal_uncoded", "trend", "原始点 + 连线，横轴取对数"),
    "line_band_logx": ("legal_uncoded", "trend", "汇总线 + 误差带，横轴取对数"),
    "scatter_logx": ("legal_uncoded", "relationship", "散点，横轴取对数"),
    "scatter_logxy": ("legal_uncoded", "relationship", "散点，双对数轴"),
    "hist_groups": ("legal_uncoded", "distribution", "分组直方图"),
    "density_groups": ("legal_uncoded", "distribution", "分组核密度"),
    # no_template：族有资格规则、没有模板（`RENDERABLE` 之外）
    "paired_lines": ("no_template", "paired", "配对连线 + 原始点"),
    "heatmap": ("no_template", "matrix", "热图"),
    "stacked_bar": ("no_template", "composition", "堆叠柱（构成）"),
    "ranking_bar": ("no_template", "ranking", "按数值排序的条形"),
    "ranking_points": ("no_template", "ranking", "按数值排序的点图"),
    "interval_errorbar": ("no_template", "interval", "点 + 给定上下限的区间线"),
    "trajectories": ("no_template", "trajectories", "逐个体轨迹 + 汇总线"),
    # missing：规则与模板都没有
    "scatter_identity": ("missing", "relationship", "等比例坐标轴 + y = x 参考线的散点（G-026）"),
    "logx_with_zero_control": (
        "missing",
        "trend",
        "对数横轴，剂量 0 的对照单独标在轴外（断轴）并告知用户（G-001 / G-054）",
    ),
    "trend_facets": ("missing", "trend", "量纲不同的几列上下分面、共用横轴（G-041）"),
}


def methods_by_status(status: str) -> list[str]:
    return [m for m, (s, _f, _t) in METHODS.items() if s == status]


def load_recommend_cases(path: Path = RECOMMEND_FILE) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    return cases


# --------------------------------------------------------------------------- 配方 → 画法
def classify(rec: dict | r.FigureRecipe) -> str | None:
    """一份配方（`FigureRecipe` 或它的 `to_dict` / 候选的 `values`）归到哪个画法；不属于 `METHODS` 任何一个 → None。看族、各层图元与汇总、坐标尺度、是否带个体编号。"""
    d = rec.to_dict() if isinstance(rec, r.FigureRecipe) else rec
    fam, scale, unit = d["family"], d.get("scale", "linear"), d.get("unit")
    group = d.get("group") or bool(
        d.get("stack")
    )  # 「把列当分组」时类别列是转换后长表里的那一列，配方里没有 group 字段
    layers = d["layers"]
    marks = {ly["mark"] for ly in layers}
    main = [ly["mark"] for ly in layers if ly["role"] == "main"]
    log_x = scale in ("log_x", "log_xy")
    if d.get("err"):
        if fam == "comparison":
            return "bar_err" if "bar" in main else "points_err"
        return {"trend": "line_err"}.get(fam)
    if fam == "comparison":
        if "bar" in main:
            return "bar_points" if marks & {"jitter", "points"} else "bar_error"
        if "box" in main:
            return "box_points"
        if "violin" in main:
            return "violin_points"
        if marks & {"jitter", "points"} and "hline_summary" in marks:
            summ = next(ly["summary"] for ly in layers if ly["mark"] == "hline_summary")
            if scale == "log_y":
                return "points_mean_logy"
            return "median_points" if summ == "median_iqr" else "points_mean"
        return None
    if fam == "trend":
        if "step" in marks:
            return "step_line"
        if "band" in marks:
            return "line_band_logx" if log_x else "line_band"
        if unit and "line" in main:
            return "line_units"
        if "points" in main and "line" in marks:
            return "line_points_logx" if log_x else "line_points"
        return None
    if fam == "relationship":
        if scale == "log_xy":
            return "scatter_logxy"
        if log_x:
            return "scatter_logx"
        return "scatter_fit" if "fit_line" in marks else "scatter"
    if fam == "distribution":
        if "hist" in marks:
            return "hist_groups" if group else "hist"
        if "ecdf" in marks:
            return "ecdf_groups" if group else "ecdf"
        if "density" in marks:
            return "density_groups" if group else "density"
        if "box" in marks:
            return "box_points_single"
        return None
    other = {
        "paired": "paired_lines",
        "matrix": "heatmap",
        "composition": "stacked_bar",
        "interval": "interval_errorbar",
        "trajectories": "trajectories",
    }
    if fam == "ranking":
        return "ranking_points" if "points" in main else "ranking_bar"
    return other.get(fam)


# --------------------------------------------------------------------------- 一条用例的输入
def materialize(case: dict, project_root: Path) -> str:
    folder = project_root / case["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in case["tables"].items():
        (folder / name).write_text(text, encoding="utf-8")
    return case["id"]


def table_inputs(
    case: dict, root: Path
) -> tuple[adf.FolderProfile, adf.TableEntry, adf.DataSummary]:
    folder = materialize(case, root)
    profile = adf.profile_folder(root / folder)
    entry = profile.tables[0]
    values = adf.inspect_data_source(
        root / folder / entry.path, entry.sheet, v2=True, allow_large=True
    )
    return profile, entry, values


def answers_of(case: dict) -> dict:
    """用例的 `picks`（y / x / group，可带 design / unit）→ `propose` 的 `answers`。"""
    return dict(case["picks"])


def structure_signature(case: dict, root: Path) -> tuple:
    """规则眼里的表结构：列数、各列类型序列、行数量级（位数）。最小对两张表必须相等（`n_rows` 在 `differs_by` 里的对行数量级可以不同）。"""
    _profile, entry, values = table_inputs(case, root / "sig")
    s = entry.summary
    return (
        len(s.columns),
        tuple(s.per_column[c].kind for c in s.columns),
        len(str(len(values.rows))),
    )


def structure_signature_no_rows(sig: tuple) -> tuple:
    return sig[:2]


# --------------------------------------------------------------------------- 规则基线
def ideal_answers(case: dict, root: Path) -> dict:
    """「理想模型」回放（不调模型）：用例的 `roles`（每列的标准角色）当模型对各列的判断、标准 `picks` 当模型的选择，走**真的** `ai_data_roles.interpret`，
    再按前端的做法把建议的 `answers` 并到标准 `picks` 上。→ 交给 `propose` 的 `answers`。判断逻辑全在产品代码里，这里只拼输入。"""
    _profile, entry, values = table_inputs(case, root / "ideal")
    shape = {
        "columns": [{"name": c, "role": ro} for c, ro in case["roles"].items()],
        "picks": {k: v for k, v in case["picks"].items() if k in ("y", "x", "group")},
    }
    und = R.interpret(entry, values, R.validate_roles_shape(shape), case.get("hint"))
    return {**answers_of(case), **und.to_payload(entry.path)["answers"]}


def rules_run(case: dict, root: Path, *, raw: bool = False, ideal_roles: bool = False) -> dict:
    """对这条用例用标准答案的列角色（`picks`）跑现有规则（懒模式，不调模型）。→ `{kind, methods: [画法 id 按推荐顺序], recommended: 画法 id | None, raw}`。
    `ideal_roles=True`：再带上「理想模型」对各列角色的判断（`ideal_answers`）——量「模型把列角色判对之后」规则能给出什么。"""
    folder = materialize(case, root)
    resp = P.propose(
        root,
        project=PROJECT,
        folder=folder,
        hint=case.get("hint"),
        render="lazy",
        answers=ideal_answers(case, root) if ideal_roles else answers_of(case),
    )
    items = resp.get("items") or []

    def of(it: dict) -> str | None:
        # 响应里的 `values` 是界面回填用的那几项，不带误差列；误差列在 `error_column`
        col = it.get("error_column")
        return classify({**it["values"], **({"err": col} if col else {})})

    methods = [of(it) for it in items]
    rec = next((of(it) for it in items if it.get("recommended")), None)
    return {
        "kind": resp["kind"],
        "methods": methods,
        "recommended": rec,
        "titles": [it["title_code"] for it in items],
        "reasons": resp.get("reasons"),
        "roles": resp.get("roles"),
        **({"raw_items": items} if raw else {}),
    }


def _shape(
    entry: adf.TableEntry, method: str, role: dict, scale: str = "linear"
) -> r.FigureRecipe | None:
    """某个画法在这张表、这组列角色上的配方（没有所需的角色 = None）。**不判合法**——合法由 `legal_universe` 过 `validate_recipe` 与模板检查。"""
    x, ys, grp, fac, unit = role["x"], role["y"], role["group"], role["facet"], role["unit"]
    y = ys[0] if ys else None

    def mk(family, layers, **kw):
        return r.FigureRecipe(
            table=entry.path,
            sheet=entry.sheet,
            family=family,
            layers=tuple(r.Layer(*ly) for ly in layers),
            y=tuple(kw.pop("ys", ys)),
            why="x",
            scale=kw.pop("scale", "linear"),
            **kw,
        )

    if y is None:
        return None
    J = ("jitter", "none", "main")
    if method in ("points_mean", "median_points", "points_mean_logy"):
        if not grp:
            return None
        summ = "median_iqr" if method == "median_points" else "mean_sd"
        return mk(
            "comparison",
            [J, ("hline_summary", summ, "overlay")],
            ys=[y],
            x=grp,
            group=fac,
            scale="log_y" if method == "points_mean_logy" else "linear",
        )
    if method in ("box_points", "violin_points"):
        if not grp:
            return None
        mark = "box" if method == "box_points" else "violin"
        return mk(
            "comparison",
            [(mark, "none", "main"), ("jitter", "none", "overlay")],
            ys=[y],
            x=grp,
            group=fac,
        )
    if method == "bar_error":
        return (
            mk(
                "comparison",
                [("bar", "mean_sd", "main"), ("errorbar", "mean_sd", "overlay")],
                ys=[y],
                x=grp,
            )
            if grp
            else None
        )
    if method == "bar_points":
        return (
            mk(
                "comparison",
                [("bar", "mean_sd", "main"), ("jitter", "none", "overlay")],
                ys=[y],
                x=grp,
            )
            if grp
            else None
        )
    gx = {"group": grp, **({"facet": fac} if fac else {})} if grp else {}
    if method.startswith(("line_", "step_")):
        if not x:
            return None
        log = "log_x" if method.endswith("_logx") else "linear"
        if method == "line_units":
            if not unit:
                return None
            lay = [("line", "none", "main"), ("points", "none", "overlay")]
            return mk("trend", lay, x=x, unit=unit, **gx)
        if method.startswith("line_points"):
            lay = [("points", "none", "main"), ("line", "none", "overlay")]
        elif method.startswith("line_band"):
            lay = [
                ("line", "mean_sd", "main"),
                ("band", "mean_sd", "overlay"),
                ("points", "none", "overlay"),
            ]
        else:
            lay = [("step", "none", "main"), ("points", "none", "overlay")]
        return mk("trend", lay, x=x, scale=log, **gx)
    if method.startswith("scatter") and method != "scatter_identity":
        if not x:
            return None
        lay = [("points", "none", "main")]
        if method == "scatter_fit":
            lay.append(("fit_line", "none", "overlay"))
        scale = {"scatter_logx": "log_x", "scatter_logxy": "log_xy"}.get(method, "linear")
        return mk("relationship", lay, ys=[y], x=x, scale=scale, **({"group": grp} if grp else {}))
    if method in ("hist", "ecdf", "density", "box_points_single"):
        if grp and method != "box_points_single":
            return None
        lay = {
            "hist": [("hist", "none", "main")],
            "ecdf": [("ecdf", "none", "main")],
            "density": [("density", "none", "main")],
            "box_points_single": [("box", "none", "main"), ("jitter", "none", "overlay")],
        }[method]
        return mk("distribution", lay, ys=[y])
    if method in ("ecdf_groups", "hist_groups", "density_groups"):
        if not grp:
            return None
        mark = method.split("_")[0]
        return mk("distribution", [(mark, "none", "main")], ys=[y], group=grp)
    return None


_QUESTION_PICK = {
    "pick_value": "y",
    "pick_group": "group",
    "pick_x": "x",
    "pick_design": "design",
    "pick_unit": "unit",
}


def _lazy_candidates(profile, values, case: dict, conf: r.Confirmed) -> r.Candidates | None:
    """`default_candidates` 的懒模式：规则要问的每个问题都答「不确定」，夹着文字格的数值列 = 不画那几行（与 `propose(render="lazy")` 同一口径）。落不定 = None。"""
    picks = {
        k: case["picks"][k] for k in ("y", "x", "group", "design", "unit") if k in case["picks"]
    }
    picks.pop("design", None)
    picks.pop("unit", None)
    cells: dict[str, r.CellRule] = {}
    for _ in range(8):
        res = r.default_candidates(
            profile, confirmed=conf, hint=case.get("hint"), values=values, picks=picks, cells=cells
        )
        if isinstance(res, r.Candidates):
            return res
        if isinstance(res, r.MixedCells):
            cells.update({c["column"]: r.CellRule(c["column"], "skip") for c in res.columns})
            continue
        key = _QUESTION_PICK.get(getattr(res, "question_code", ""))
        if key is None or picks.get(key) == "unsure":
            return None
        picks[key] = "unsure"
    return None


#: 取自表里现成误差列的画法：不按列角色实例化，直接取规则给出的那份配方（`legal_universe`）。新增这类画法（带 `err` 的 coded 码）必须加进来，否则它走 `_shape`、
#: 得到 None，规则给出的候选就不在全集里（`test_every_rules_candidate_is_in_its_legal_universe` 拦）
ERR_METHODS = frozenset({"points_err", "line_err", "bar_err"})


def legal_universe(case: dict, root: Path) -> tuple[set[str], dict | None]:
    """这条用例「现有模板画得出来的全部画法」：把每个有模板的画法按标准答案的列角色实例化，过 `validate_recipe`（带行数据）与模板自己的检查。
    列角色、`stack` / `cells` / `numcat` 这几条事实取自规则对同一份 `picks` 落定的第一个候选（`rules_run`），所以**规则给出的每个候选必在全集里**（测试核）。
    → `(画法 id 集合, 列角色)`；规则一个候选都没给（要问 / 画不了）= 空集。"""
    profile, entry, values = table_inputs(case, root / "uni")
    picks = case["picks"]
    conf = r.Confirmed()
    if picks.get("design") == "repeated":
        conf = r.Confirmed("repeated", picks.get("unit"))
    cands = _lazy_candidates(profile, values, case, conf)
    if cands is None or not cands.items:
        return set(), None
    first = cands.items[0].recipe
    ri = cands.roles or {}
    stack = tuple(first.stack)
    role = {
        "x": ri.get("x"),
        "y": list(ri.get("y") or first.y),
        "group": ri.get("group"),
        "facet": ri.get("facet"),
        "unit": ri.get("unit"),
    }
    if stack:
        vname, cname = r.stack_names(stack)
        role.update(x=None, y=[vname], group=cname, facet=None, unit=None)
    out: set[str] = set()
    # 现成误差列的两种画法不按列角色实例化：哪一列是谁的误差由规则按列名配（`paired_error`），这里直接取规则给出的那份配方
    by_err = {classify(c.recipe): c.recipe for c in cands.items if c.recipe.err}
    for method, (status, _fam, _t) in METHODS.items():
        if status not in ("coded", "legal_uncoded"):
            continue
        rec = by_err.get(method) if method in ERR_METHODS else _shape(entry, method, role)
        if rec is None:
            continue
        if stack:
            rec = replace(rec, stack=stack)
        if first.cells:
            rec = replace(rec, cells=tuple(c for c in first.cells if c.column in (*rec.y, rec.x)))
        numcat = tuple(first.numcat)
        if numcat:
            cat_x = (
                "cat" in r.FAMILIES[rec.family].x_types
                and "ord" not in r.FAMILIES[rec.family].x_types
            )
            used = {rec.group, rec.facet, *([rec.x] if cat_x else [])}
            rec = replace(rec, numcat=tuple(c for c in numcat if c in used))
        if r.validate_recipe(profile, rec, confirmed=conf, values=values):
            continue
        try:
            tpl._check(entry, rec)  # noqa: SLF001
        except tpl.TemplateError:
            continue
        out.add(method)
    return out, role


# --------------------------------------------------------------------------- 天花板（C）
def measure(case: dict, root: Path, *, ideal_roles: bool = False) -> dict:
    """对一条新用例量天花板：规则第一名是否落在 `acceptable_top`、`acceptable_top` 是否在规则给出的候选里（第几）、是否在合法全集里、`forbidden` 是否出现 / 被标推荐。
    `ideal_roles`：带上理想模型的列角色判断（见 `rules_run`）；合法全集不受影响。"""
    run = rules_run(case, root / "run", ideal_roles=ideal_roles)
    uni, _role = legal_universe(case, root)
    top = set(case["acceptable_top"])
    forb = {f["method"] for f in case.get("forbidden", [])}
    ranks = {m: run["methods"].index(m) + 1 for m in top if m in run["methods"]}
    missing = {m: METHODS[m][0] for m in sorted(top - uni)}
    return {
        "rules_top": run["recommended"],
        "rules_candidates": run["methods"],
        "rules_top_ok": run["recommended"] in top,
        "acceptable_in_candidates": ranks,
        "acceptable_in_universe": sorted(top & uni),
        "universe_size": len(uni),
        "forbidden_present": sorted(forb & set(run["methods"])),
        "forbidden_recommended": run["recommended"] in forb,
        "needs": missing if not (top & uni) else {},
        "extra_gaps": list(case.get("extra_gaps", [])),
    }


def _role_shape(case: dict) -> tuple:
    """列角色的「形状」：有没有横轴、有没有分组、纵轴几列、有没有确认的个体编号（不看具体列名）。形状相同的最小对，第 1 步（读懂每列）分不出来。"""
    p = case["picks"]
    return (p["x"] != "none", p["group"] != "none", len(p["y"]), p.get("unit") is not None)


def tally(rows: Sequence[tuple[dict, dict]]) -> dict:
    """汇总四个数字与缺口排序。`rows` = `[(用例, measure 结果)]`。"""
    n = len(rows)
    base = sum(m["rules_top_ok"] for _c, m in rows)
    rerank = sum(bool(m["acceptable_in_candidates"]) for _c, m in rows)
    universe = sum(bool(m["acceptable_in_universe"]) for _c, m in rows)
    gaps: Counter = Counter()
    status_of: dict[str, str] = {}
    for _c, m in rows:
        for method, status in m["needs"].items():
            gaps[method] += 1
            status_of[method] = status
    pairs: dict[str, list] = {}
    for c, m in rows:
        if c["tier"] == "pair":
            pairs.setdefault(c["pair"]["id"], []).append((c, m))
    # 只跑一部分用例（`--only`）时一对可能只来了一边：成对的统计只算两边都在的，那一条仍计入上面的逐条合计
    pairs = {pid: ab for pid, ab in pairs.items() if len(ab) == 2}
    same_top = sum(1 for ab in pairs.values() if ab[0][1]["rules_top"] == ab[1][1]["rules_top"])
    shape_same = [
        pid for pid, ab in pairs.items() if _role_shape(ab[0][0]) == _role_shape(ab[1][0])
    ]
    same_shape_same_top = [
        pid for pid in shape_same if pairs[pid][0][1]["rules_top"] == pairs[pid][1][1]["rules_top"]
    ]
    both_right = [pid for pid, ab in pairs.items() if all(m["rules_top_ok"] for _c, m in ab)]
    return {
        "cases": n,
        "rules_baseline": base,
        "rerank_ceiling": rerank,
        "universe_ceiling": universe,
        "needs_new": n - universe,
        "gaps": [(m, k, status_of[m]) for m, k in gaps.most_common()],
        "pairs": len(pairs),
        "pairs_same_rules_top": same_top,
        "pairs_same_role_shape": sorted(shape_same),
        "pairs_same_role_shape_and_same_top": sorted(same_shape_same_top),
        "pairs_rules_right_on_both_sides": sorted(both_right),
        "forbidden_recommended": sum(m["forbidden_recommended"] for _c, m in rows),
        "forbidden_present": sum(bool(m["forbidden_present"]) for _c, m in rows),
    }


# --------------------------------------------------------------------------- A：现有 52 条用例上「重排能动多大」
def existing_cases_stats(root: Path) -> dict:
    """主集 22 + 留出 30，用各自标准答案的列角色（`model_answer.picks`）：合法全集大小、`default_candidates` 实际给了几个 / 哪些、规则第一名的画法分布。"""
    import data_figure_roles as DR  # noqa: PLC0415 — 同在 tests/support，避免循环

    rows = []
    for c in DR.load_case_set("all"):
        case = {
            "id": c["id"],
            "tables": c["tables"],
            "hint": c.get("hint"),
            "picks": {
                k: v
                for k, v in (c["model_answer"].get("picks") or {}).items()
                if k in ("y", "x", "group")
            },
        }
        try:
            run = rules_run(case, root / "a_run")
            uni, _ = legal_universe(case, root / "a_uni")
        except Exception as exc:  # 读表失败等：如实记，不吞
            rows.append({"id": c["id"], "error": f"{type(exc).__name__}: {exc}"[:80]})
            continue
        rows.append(
            {
                "id": c["id"],
                "kind": run["kind"],
                "given": run["methods"],
                "universe": sorted(uni),
                "top": run["recommended"],
            }
        )
    return {"rows": rows, **_a_summary(rows)}


def _a_summary(rows: list[dict]) -> dict:
    ok = [x for x in rows if "error" not in x]
    sizes = Counter(len(x["universe"]) for x in ok)
    more = sum(1 for x in ok if len(x["universe"]) > len(x["given"]))
    tops = Counter(x["top"] for x in ok)
    return {
        "n": len(rows),
        "errors": [x for x in rows if "error" in x],
        "universe_size_distribution": dict(sorted(sizes.items())),
        "universe_larger_than_given": more,
        "given_not_in_universe": sum(1 for x in ok if set(x["given"]) - set(x["universe"])),
        "top_distribution": dict(tops.most_common()),
    }


# --------------------------------------------------------------------------- 形状校验
def check_case_shape(case: dict, root: Path) -> None:
    """一条用例自证：表读得出、`roles` 与表的列逐一对上、闭集取值都合法、准则 id 存在、`forbidden` 只引 firm / strong、`acceptable_top` 与 `forbidden` 不相交、
    三档理由符合 G-039（`user_purpose` 只在有 `hint` 时非空）、`picks` 引用的列存在。**不**断言 `expressible` 的取值（见模块文档）。"""
    cid = case["id"]
    folder = materialize(case, root / "shape")
    entry = adf.profile_folder(root / "shape" / folder).tables[0]
    cols = set(entry.summary.columns)
    assert set(case["roles"]) == cols, cid
    assert all(role in R.ROLES for role in case["roles"].values()), cid
    picks = case["picks"]
    assert set(picks) >= {"y", "x", "group"} and set(picks) <= {
        "y",
        "x",
        "group",
        "design",
        "unit",
    }, cid
    named = [
        *picks["y"],
        *(c for c in (picks["x"], picks["group"], picks.get("unit")) if c not in (None, "none")),
    ]
    assert all(c in cols for c in named), cid
    assert case["tier"] in TIERS and case["lang"] in LANGS and case["identity"] in IDENTITIES, cid
    assert case["task"] in TASKS and case["task_source"] in TASK_SOURCES, cid
    assert (case["task"] == "undetermined") <= (case["task_source"] == "needs_confirm"), cid
    hint = case.get("hint")
    assert hint is None or (isinstance(hint, str) and hint.strip()), cid
    assert (case["task_source"] == "from_hint") <= bool(hint), cid
    if case["task_source"] == "needs_confirm":
        assert case.get("confirm") in CONFIRM_FACTS, cid
    else:
        assert "confirm" not in case or case["confirm"] is None, cid
    top = case["acceptable_top"]
    assert top and len(top) == len(set(top)) and all(m in METHODS for m in top), cid
    forb = case["forbidden"]
    for f in forb:
        assert set(f) == {"method", "g"} and f["method"] in METHODS, cid
        assert GUIDANCE_STRENGTH.get(f["g"]) in FORBIDDEN_STRENGTHS, (cid, f)
    assert not set(top) & {f["method"] for f in forb}, cid
    assert all(d in DERIVES for d in case.get("derive", [])), cid
    guidance = case["guidance"]
    assert guidance and all(g in GUIDANCE_STRENGTH for g in guidance), cid
    assert {f["g"] for f in forb} <= set(guidance), cid
    if case.get("derive"):
        assert "G-037" in guidance, cid
    if case["task_source"] == "needs_confirm":
        assert "G-038" in guidance or case.get("confirm") in ("ratio_axis", "zero_control"), cid
    assert all(isinstance(x, str) and x for x in case.get("extra_gaps", [])), cid
    assert isinstance(case.get("note", ""), str), cid
    reason = case["reason"]
    assert set(reason) == set(REASON_KEYS), cid
    assert all(v is None or (isinstance(v, str) and v.strip()) for v in reason.values()), cid
    assert reason["data_fact"] or reason["perceptual"], cid
    assert (reason["user_purpose"] is not None) == bool(hint), cid
    ex = case.get("expressible")
    if ex is not None:
        assert isinstance(ex, dict) and set(ex) >= {"rules_top", "rules_top_ok", "needs"}, cid
        assert isinstance(ex["rules_top_ok"], bool), cid
    if case["tier"] == "pair":
        pair = case["pair"]
        assert set(pair) == {"id", "side", "differs_by"} and pair["side"] in ("A", "B"), cid
        assert pair["differs_by"] and all(d in PAIR_DIFFERS for d in pair["differs_by"]), cid
    else:
        assert "pair" not in case, cid


def check_pairs(cases: Sequence[dict], root: Path) -> None:
    """最小对：每个 id 恰好 A、B 两条；规则眼里的结构（列数 + 列类型序列）逐项相同，行数量级相同——除非 `differs_by` 含 `n_rows`；
    两条的 `acceptable_top` 不相同（否则不是「按含义推荐」的对子）。"""
    pairs: dict[str, dict[str, dict]] = {}
    for c in cases:
        if c["tier"] == "pair":
            pairs.setdefault(c["pair"]["id"], {})[c["pair"]["side"]] = c
    for pid, sides in pairs.items():
        assert set(sides) == {"A", "B"}, pid
        a, b = sides["A"], sides["B"]
        assert a["pair"]["differs_by"] == b["pair"]["differs_by"], pid
        sa, sb = structure_signature(a, root), structure_signature(b, root)
        if "n_rows" in a["pair"]["differs_by"]:
            sa, sb = sa[:2], sb[:2]
        assert sa == sb, (pid, sa, sb)
        assert set(a["acceptable_top"]) != set(b["acceptable_top"]) or a["task"] != b["task"], pid


def run_all(cases: Sequence[dict], *, ideal_roles: bool = False) -> list[tuple[dict, dict]]:
    with tempfile.TemporaryDirectory(prefix="recommend-measure-") as tmp:
        root = Path(tmp)
        return [(c, measure(c, root / c["id"], ideal_roles=ideal_roles)) for c in cases]


def write_back(path: Path = RECOMMEND_FILE) -> list[tuple[dict, dict]]:
    """把现算的 `expressible` 写回用例文件（只动这一个字段；`acceptable_top` / `forbidden` 等标准答案一个字不碰）。"""
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = run_all(doc["cases"])
    for case, m in rows:
        case["expressible"] = m
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return rows


def format_report(
    rows: Sequence[tuple[dict, dict]], existing: dict | None = None, *, ideal_roles: bool = False
) -> str:
    """C 的那张表（与 A 的统计，给了 `existing` 才有）。"""
    s = tally(rows)
    n = s["cases"]
    arm = "规则臂 + 理想列角色回放，不调模型" if ideal_roles else "规则臂，不调模型"
    out = ["", f"== 天花板测量（{n} 条，{arm}）=="]
    out.append(
        f"① 规则现在第一名落在 acceptable_top：{s['rules_baseline']}/{n}（{s['rules_baseline'] / n:.0%}）"
    )
    out.append(f"② 只重排现有候选最多：{s['rerank_ceiling']}/{n}（{s['rerank_ceiling'] / n:.0%}）")
    out.append(
        f"③ 允许从合法全集换进候选最多：{s['universe_ceiling']}/{n}（{s['universe_ceiling'] / n:.0%}）"
    )
    out.append(f"④ 还需要现在没有的模板或能力：{s['needs_new']}/{n}")
    for method, k, status in s["gaps"]:
        out.append(f"     {k} 条  {method}（{status}）  {METHODS[method][2]}")
    out.append(
        f"最小对：规则给两张表相同第一名的 {s['pairs_same_rules_top']}/{s['pairs']} 对；"
        f"列角色形状相同的 {len(s['pairs_same_role_shape'])} 对里相同第一名的 {len(s['pairs_same_role_shape_and_same_top'])} 对"
        f"（{', '.join(s['pairs_same_role_shape_and_same_top'])}）；两边规则都对的 {len(s['pairs_rules_right_on_both_sides'])} 对"
    )
    out.append(
        f"forbidden：被标推荐 {s['forbidden_recommended']} 条；出现在候选里 {s['forbidden_present']} 条"
    )
    out.append("")
    for tier in TIERS:
        sub = [(c, m) for c, m in rows if c["tier"] == tier]
        ok = sum(m["rules_top_ok"] for _c, m in sub)
        out.append(f"  {tier:5s} {len(sub):2d} 条：规则第一名对 {ok}")
    out.append("")
    for c, m in rows:
        out.append(
            f"  {c['id']:8s} {'对' if m['rules_top_ok'] else '错'}  规则第一={m['rules_top']}  候选={m['rules_candidates']}"
            + (f"  缺={list(m['needs'])}" if m["needs"] else "")
        )
    if existing is not None:
        out += [
            "",
            f"== A2：现有 {existing['n']} 条用例（用标准答案的列角色）==",
            f"合法全集大小分布：{existing['universe_size_distribution']}",
            f"全集 > 规则给出的候选个数：{existing['universe_larger_than_given']}/{existing['n']}",
            f"规则给出的候选不在全集里（自检，必须 0）：{existing['given_not_in_universe']}",
            f"规则第一名分布：{existing['top_distribution']}",
        ]
    return "\n".join(out)
