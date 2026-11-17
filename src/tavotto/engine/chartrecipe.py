"""图表模型 ↔ ADR 0178 配方/期望指纹的桥接（父进程用，纯标准库）。

只做三件事，各自的主语不同：

* **合法性**：把 `ChartModel` 反推成 0178 的 `FigureRecipe`，再跑
  `ai_data_recipe.validate_recipe`。表达不了的模型（image / 混合图元 / 多 x）
  如实回 `model_not_expressible`，绝不伪造一份配方假装判过。
* **绘图值指纹**：按脚手架**实际画出的数组**算期望值，摘要规则是
  `ai_data_figure.digest`（`manifest.canonical_digest` 的 stdlib 镜像）。
* **可核范围**：`errorbar` 等仍不可核的图元沿用
  `ai_data_template.UNCHECKED_MARKS`，不折算成 verified。

gid 只从 `chartmanifest.render_scaffold` 的 `roles[...]["primary_gid"]` 取；
原生 index（`axes_0.lines_j` / `barseries_j`）会被 bar+yerr、前置 errorbar
等合法结构改变，不能预测。
"""

from __future__ import annotations

from . import (
    ai_data_figure as adf,
    ai_data_recipe as recipe_mod,
    ai_data_template as template_mod,
    figureverify,
)

#: 逐轴尺度 → 0178 配方的单尺度词；表里没有的组合不反推。
SCALE_MAP = {
    ("linear", "linear"): "linear",
    ("log", "linear"): "log_x",
    ("linear", "log"): "log_y",
    ("log", "log"): "log_xy",
}

#: ChartModel 的系列种类 → 0178 图元；image 没有可反推的表配方。
_KIND_MARK = {
    "line": "line",
    "scatter": "points",
    "bar": "bar",
    "errorbar": "errorbar",
}

Confirmed = recipe_mod.Confirmed
FigureRecipe = recipe_mod.FigureRecipe


def recipe_from_model(
    model: dict, *, table: str, family: str, why: str
) -> recipe_mod.FigureRecipe | None:
    """把 `ChartModel` 反推成 0178 配方；表达不了时回 None。

    只支持「所有标量系列共享同一 x、同一 kind」的模型。image、混合图元、
    多 x 列都不反推——调用方要把 None 记成 unchecked，不能当成合格。
    """
    series = [sr for sr in model.get("series") or [] if isinstance(sr, dict)]
    if not series or any(sr.get("kind") == "image" for sr in series):
        return None
    kinds = {sr.get("kind") for sr in series}
    xs = {sr.get("x") for sr in series}
    if len(kinds) != 1 or len(xs) != 1:
        return None
    kind = next(iter(kinds))
    mark = _KIND_MARK.get(kind)
    if mark is None:
        return None
    x = next(iter(xs))
    if not isinstance(x, str) or not x:
        return None
    y = tuple(dict.fromkeys(sr.get("y") for sr in series if isinstance(sr.get("y"), str)))
    if not y:
        return None
    axes = (model.get("axes") or [{}])[0]
    scale = SCALE_MAP.get((axes.get("xscale", "linear"), axes.get("yscale", "linear")))
    if scale is None:
        return None
    try:
        return recipe_mod.FigureRecipe.from_dict(
            {
                "table": table,
                "family": family,
                "layers": [{"mark": mark}],
                "x": x,
                "y": list(y),
                "scale": scale,
                "why": why,
            }
        )
    except recipe_mod.RecipeError:
        return None


def problems_for_model(
    profile,
    model: dict,
    *,
    table: str,
    family: str,
    why: str,
    confirmed: recipe_mod.Confirmed,
    values=None,
) -> dict:
    """跑 0178 的 `validate_recipe`，返回 `{"problems": [...], "unchecked": [...]}`。

    `values=None` 时只判结构规则；取值规则（柱图基线、重复 x 等）不判，并在
    `unchecked` 里如实标 `value_rules`。
    """
    recipe = recipe_from_model(model, table=table, family=family, why=why)
    if recipe is None:
        return {"problems": [], "unchecked": ["model_not_expressible"]}
    try:
        problems = recipe_mod.validate_recipe(profile, recipe, confirmed=confirmed, values=values)
    except recipe_mod.RecipeError as exc:
        return {
            "problems": [{"code": exc.code, "field": "", "detail": str(exc)}],
            "unchecked": [],
        }
    return {
        "problems": [problem.to_dict() for problem in problems],
        "unchecked": [] if values is not None else ["value_rules"],
    }


def chart_fingerprints(model: dict, roles: dict) -> list[dict]:
    """脚手架实际画出的数组 → 期望项 `{label, gid, part, digest}`。

    `roles` 是 `render_scaffold` 的返回；gid 取 `primary_gid`。`errorbar`
    不产出（误差棒 cap 线段父进程复现不了）。
    """
    out: list[dict] = []
    axes = (model.get("axes") or [{}])[0]
    categorical = axes.get("kind") == "categorical"
    categories = axes.get("categories") or []
    dataset = model.get("dataset") or {}
    columns = dataset.get("columns") or {}
    grids = dataset.get("grids") or {}
    for sr in model.get("series") or []:
        kind = sr.get("kind")
        if kind == "errorbar":
            continue
        role = (roles or {}).get(f"series:{sr.get('id')}") or {}
        gid = role.get("primary_gid")
        if not isinstance(gid, str) or not gid:
            raise ValueError(f"series {sr.get('id')!r} has no primary gid")
        if kind == "image":
            grid = grids.get(sr.get("grid", ""))
            if not isinstance(grid, dict) or not grid.get("values"):
                raise ValueError(f"series {sr.get('id')!r} references unknown grid")
            rows = grid["values"]
            flat = [float(value) for row in rows for value in row]
            shape = (len(rows), len(rows[0]))
            part = "array"
        elif kind in ("line", "scatter"):
            xs = columns.get(sr.get("x"))
            ys = columns.get(sr.get("y"))
            if xs is None or ys is None:
                raise ValueError(f"series {sr.get('id')!r} references unknown columns")
            if categorical:
                xs = list(range(len(categories)))
            if len(xs) != len(ys):
                raise ValueError(f"series {sr.get('id')!r} x/y lengths differ")
            flat = [value for x, y in zip(xs, ys) for value in (float(x), float(y))]
            shape = (len(ys), 2)
            part = "xy"
        elif kind == "bar":
            ys = columns.get(sr.get("y"))
            if ys is None:
                raise ValueError(f"series {sr.get('id')!r} references unknown column")
            flat = [value for y in ys for value in (0.0, float(y))]
            shape = (len(ys), 2)
            part = "bars"
        else:
            raise ValueError(f"series {sr.get('id')!r} has unsupported kind {kind!r}")
        out.append(
            {
                "label": str(sr.get("id")),
                "gid": gid,
                "part": part,
                "digest": adf.digest(flat, shape),
            }
        )
    return out


def chart_coverage(model: dict) -> dict:
    """每条系列是否可核；`errorbar` 仍不可核，绝不折算成 verified。"""
    layers = []
    for sr in model.get("series") or []:
        kind = sr.get("kind")
        mark = "heatmap" if kind == "image" else _KIND_MARK.get(kind, "")
        reason = template_mod.UNCHECKED_MARKS.get(mark, "")
        layers.append(
            {
                "id": sr.get("id"),
                "kind": kind,
                "checked": not reason,
                "reason": reason,
            }
        )
    return {"layers": layers, "any_checked": any(layer["checked"] for layer in layers)}


def chart_checks(model: dict, scaffold: dict) -> list:
    """脚手架返回 → `figureverify.data_matches_expected` 的检查结果。"""
    return figureverify.data_matches_expected(
        scaffold["manifest"],
        chart_fingerprints(model, scaffold.get("roles") or {}),
    )
