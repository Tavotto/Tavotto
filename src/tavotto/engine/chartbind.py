"""显式建模流程的后端合同（SG06 / ADR 0182）：校准证据 → ChartModel + Binding。

用户在确认预览里做的事，落到这里是纯函数：
- **校准证据**（每个轴两条参考：两个已知数据值各在哪个用户单位位置）→
  推出轴范围；线性推导 + log/倒轴校验；**类别轴没有连续映射的证据就不建模型**
  （宁缺勿猜）。
- **系列选择**（场景节点 + 数据列 + 样式保持）→ ChartModel.series。
- 产出一律经 `chartmodel.validate_model / validate_binding`——确认产物与
  运行时消费的是同一份合同，不存在第二套判据。
"""

from __future__ import annotations

import math

from tavotto.engine import chartdata, chartmodel

#: 校准证据的字段闭集。
CALIBRATION_KEYS = ("p1_user", "p1_value", "p2_user", "p2_value", "scale")
#: 绑定请求的字段闭集。
BINDING_REQUEST_KEYS = (
    "axes_id",
    "rect",
    "y",
    "series",
    "scene_digest",
    "nodes",
    "completeness",
)
#: N4 legend 合同的可选请求键（与 ``x`` 的条件追加同一写法：缺省请求逐字节不变）。
LEGEND_REQUEST_KEYS = ("id", "node", "rect", "entries")
#: 请求条目以**系列的场景节点**指认跟随对象——series id 由本函数按序分配，
#: 前端确认时还没有它；落进 binding 前在这里翻译成 series id。
LEGEND_ENTRY_REQUEST_KEYS = ("index", "label", "series_node")

#: 稳定错误码（确认流程给用户的失败理由）。
ERROR_CODES = (
    "bind_evidence_invalid",  # 校准证据不完整/矛盾
    "bind_log_needs_positive",  # log 轴证据里有非正值
    "bind_degenerate_span",  # 两个参考点重合（推不出范围）
    "bind_node_not_graphic",  # 选中的节点不是可绑定图形元素
    "bind_column_missing",  # 选的数据列不存在
    "bind_series_count",  # 系列数与所选节点数不一致
)


class BindError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        if code not in ERROR_CODES:
            raise AssertionError(f"undisclosed bind error: {code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def axis_limits_from_evidence(evidence: dict) -> tuple[float, float, str, bool]:
    """两条参考证据 → 该轴的 (lo, hi, scale, inverted)。

    证据的坐标基准是**轴的绘图区用户单位**（rect 的两个端点各自已知数据值）；
    线性外推到绘图区两端即得范围。log 轴要求两个参考值都为正。
    """
    if not isinstance(evidence, dict) or sorted(evidence) != sorted(CALIBRATION_KEYS):
        raise BindError("bind_evidence_invalid", f"expected keys {sorted(CALIBRATION_KEYS)}")
    scale = evidence["scale"]
    if scale not in chartmodel.SCALES:
        raise BindError("bind_evidence_invalid", f"scale must be one of {chartmodel.SCALES}")
    p1u, p1v = float(evidence["p1_user"]), float(evidence["p1_value"])
    p2u, p2v = float(evidence["p2_user"]), float(evidence["p2_value"])
    for v in (p1u, p1v, p2u, p2v):
        if not math.isfinite(v):
            raise BindError("bind_evidence_invalid", "evidence values must be finite")
    if p1u == p2u:
        raise BindError("bind_degenerate_span", "reference points share one position")
    if scale == "log" and (p1v <= 0 or p2v <= 0):
        raise BindError("bind_log_needs_positive", "log evidence needs positive data values")

    def value_at(user: float) -> float:
        # 线性内插/外推：数据值随用户位置线性变化（log 轴在对数域内插）
        if scale == "log":
            lv = math.log10(p1v) + (math.log10(p2v) - math.log10(p1v)) * (user - p1u) / (p2u - p1u)
            return 10.0**lv
        return p1v + (p2v - p1v) * (user - p1u) / (p2u - p1u)

    lo, hi = value_at(0.0), value_at(1.0)
    inverted = lo > hi
    return lo, hi, scale, inverted


def build_model_and_binding(request: dict, *, engine_identity: dict) -> dict:
    """确认动作 → 可校验的 ``{"model": …, "binding": …}``（一律过 chartmodel 闸）。

    ``request``：axes_id / rect([x,y,w,h] 用户单位) / x,y(轴校准证据) /
    series([{node, x_col, y_col, kind, style?}]) / scene_digest / nodes(场景清单) /
    completeness。数据在 ``request["dataset_text"]``（CSV/表格文本）或
    ``request["dataset"]``（已解析列）。
    """
    if not isinstance(request, dict):
        raise BindError("bind_evidence_invalid", "request must be an object")
    required = set(BINDING_REQUEST_KEYS)
    if request.get("x_kind") != "categorical":
        required.add("x")
    missing = sorted(required - set(request))
    if missing:
        raise BindError("bind_evidence_invalid", f"missing keys {missing}")
    scene_digest = request.get("scene_digest")
    nodes = request.get("nodes") or []
    node_ids = {n["id"] for n in nodes if isinstance(n, dict) and "id" in n}
    graphic_ids = {
        n["id"]
        for n in nodes
        if isinstance(n, dict)
        and n.get("tag")
        in ("path", "rect", "circle", "ellipse", "line", "polygon", "polyline", "image", "use", "g")
    }
    rect = request.get("rect")
    if not isinstance(rect, list) or len(rect) != 4 or rect[2] <= 0 or rect[3] <= 0:
        raise BindError("bind_evidence_invalid", "rect must be [x,y,w,h] with positive size")

    series_req = request.get("series") or []
    image_only = bool(series_req) and all(sr.get("kind") == "image" for sr in series_req)
    if "dataset" in request and request["dataset"] is not None:
        parsed = request["dataset"]
        blocking = [
            i
            for i in parsed.get("issues", [])
            if i.get("column") not in (parsed.get("text_columns") or {})
        ]
        if blocking:
            raise BindError("bind_evidence_invalid", f"dataset has issues: {blocking[:3]}")
    elif image_only and not (request.get("dataset_text") or "").strip():
        # 纯网格绑定：没有标量数据表是合法的
        parsed = {"columns": {}, "text_columns": {}, "issues": []}
    else:
        parsed = chartdata.parse_table(request.get("dataset_text") or "")
        text_cols = parsed.get("text_columns") or {}
        blocking = [i for i in parsed["issues"] if i.get("column") not in text_cols]
        if blocking:
            raise BindError("bind_evidence_invalid", f"dataset has issues: {blocking[:3]}")
    columns = parsed["columns"]
    if not columns and not image_only:
        raise BindError("bind_column_missing", "no fully numeric columns in the dataset")

    y_ev = request.get("y") or {}
    ylo, yhi, yscale, yinv = axis_limits_from_evidence(y_ev)
    axis_kind = "numeric"
    categories: list[str] | None = None
    if request.get("x_kind") == "categorical":
        # 类别轴：类目来自文本列（用户显式指认），位置 = 序号；绝不套连续数值映射
        cat_col = request.get("categories_col")
        text_columns = parsed.get("text_columns") or {}
        if cat_col not in text_columns:
            raise BindError(
                "bind_column_missing", f"categories_col {cat_col!r} is not a text column"
            )
        categories = text_columns[cat_col]
        axis_kind = "categorical"
        xlo, xhi, xinv, xscale = -0.5, len(categories) - 0.5, False, "linear"
        if request.get("x"):
            raise BindError(
                "bind_evidence_invalid", "categorical axes take categories, not numeric evidence"
            )
    else:
        x_ev = request.get("x") or {}
        xlo, xhi, xscale, xinv = axis_limits_from_evidence(x_ev)

    if not series_req:
        raise BindError("bind_series_count", "at least one series is required")
    series: list[dict] = []
    for i, sr in enumerate(series_req):
        node = sr.get("node")
        if node not in node_ids:
            raise BindError("bind_node_not_graphic", f"node {node!r} is not in the scene")
        if node not in graphic_ids:
            raise BindError("bind_node_not_graphic", f"node {node!r} is not a graphic element")
        if sr.get("kind") != "image":
            # image 系列的数据源是网格，不是 x/y 列
            for key in ("x_col", "y_col"):
                if sr.get(key) not in columns and sr.get(key) not in (
                    parsed.get("text_columns") or {}
                ):
                    raise BindError("bind_column_missing", f"series[{i}].{key} {sr.get(key)!r}")
            if sr.get("x_col") in (parsed.get("text_columns") or {}) and axis_kind != "categorical":
                raise BindError(
                    "bind_evidence_invalid",
                    f"series[{i}].x_col is a text column; the axis must be categorical",
                )
        series.append(
            {
                "id": f"s{i + 1}",
                "kind": sr.get("kind") if sr.get("kind") in chartmodel.SERIES_KINDS else "line",
                "axes": "ax1",
                "node": node,
                "x": sr.get("x_col", "-"),
                "y": sr.get("y_col", "-"),
                "style": dict(sr.get("style") or {}),
            }
        )
    # image 系列的色条区域节点（可选）：组合时由片段的新色条整体接管
    if any(sr.get("kind") == "image" for sr in series_req):
        node_ids_pre = {n["id"] for n in (request.get("nodes") or []) if isinstance(n, dict)}
        for i, sr in enumerate(series_req):
            if sr.get("kind") == "image" and sr.get("cbar_node"):
                if sr["cbar_node"] not in node_ids_pre:
                    raise BindError(
                        "bind_node_not_graphic",
                        f"cbar_node {sr['cbar_node']!r} is not in the scene",
                    )
                series[i]["cbar_node"] = sr["cbar_node"]

    # 图像/网格系列（可选）：grid_text 是数值矩阵文本；样式带 cmap/vmin/vmax
    grids: dict = {}
    if any(sr.get("kind") == "image" for sr in series_req):
        from tavotto.engine import chartdata as _cd

        parsed_grid = _cd.parse_grid(request.get("grid_text") or "")
        if parsed_grid["issues"] or not parsed_grid["values"]:
            raise BindError(
                "bind_evidence_invalid", f"grid has issues: {parsed_grid['issues'][:3]}"
            )
        grids = {"g1": {"values": parsed_grid["values"]}}

    # 误差列（可选）：yerr_col 命名数据列，进入模型事实（误差定义随模型走）
    for i, sr in enumerate(series_req):
        if sr.get("yerr_col"):
            if sr["yerr_col"] not in columns:
                raise BindError("bind_column_missing", f"series[{i}].yerr_col {sr['yerr_col']!r}")
            series[i]["yerr"] = sr["yerr_col"]

    # 数据列可能长短不齐：以所选系列的 x/y 列对齐为准，短列报错（不静默截断）
    merged = {**columns, **(parsed.get("text_columns") or {})}
    for i, sr in enumerate(series_req):
        if sr.get("kind") == "image":
            # image 系列：数据源是网格；x/y 占位为 "-"（chartmodel 对 image 跳过列校验）
            series[i]["x"] = "-"
            series[i]["y"] = "-"
            series[i]["grid"] = "g1"
    scalar_series = [sr for sr in series if sr["kind"] != "image"]
    if scalar_series:
        lengths = {len(merged[sr["x"]]) for sr in scalar_series} | {
            len(merged[sr["y"]]) for sr in scalar_series
        }
        if len(lengths) > 1:
            raise BindError("bind_evidence_invalid", "series columns have different lengths")

    model = {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": rect,
                "xlim": [min(xlo, xhi), max(xlo, xhi)],
                "ylim": [min(ylo, yhi), max(ylo, yhi)],
                "xscale": xscale,
                "yscale": yscale,
                "invert_x": xinv,
                "invert_y": yinv,
                "kind": axis_kind,
                **({"categories": categories} if categories is not None else {}),
            }
        ],
        "series": series,
        # 文本列（类别/标签）随数据集走：脚手架与校验都能引用（chartmodel 允许字符串值）
        "dataset": {"columns": merged, **({"grids": grids} if grids else {})},
        "scene_digest": scene_digest,
        "data_digest": chartmodel.digest_of(
            {"columns": merged, **({"grids": grids} if grids else {})}
        ),
        "engine": engine_identity,
    }
    chartmodel.validate_model(model)
    # 绑定条目 = 系列数据节点 + 用户确认的轴区域节点（旧刻度/标注/色条区域）。
    # 轴区域由模型 rect 定义边界；axes_nodes 是**替换范围**（确认预览里列出）。
    entries = [{"object": f"series:{sr['id']}", "node": sr["node"]} for sr in series]
    # N5 所有权贯穿：image 系列声明的 cbar_node 直接进入有效绑定——
    # 组合端据它把旧色条区域替换为片段新色条，不需要调用方再手工传 axes_nodes。
    for sr in series:
        cbar_node = sr.get("cbar_node")
        if cbar_node:
            if cbar_node not in node_ids:
                raise BindError(
                    "bind_node_not_graphic", f"cbar_node {cbar_node!r} is not in the scene"
                )
            entries.append({"object": "axes:ax1", "node": cbar_node})
    for node_id in request.get("axes_nodes") or []:
        if node_id not in node_ids:
            raise BindError("bind_node_not_graphic", f"axes node {node_id!r} is not in the scene")
        entries.append({"object": "axes:ax1", "node": node_id})
    binding = {
        "model_rev": model["rev"],
        "scene_digest": scene_digest,
        "entries": entries,
    }
    legend_req = request.get("legend")
    if legend_req is not None:
        binding["legend"] = _legend_spec_from_request(legend_req, series, node_ids, graphic_ids)
    chartmodel.validate_binding(binding, model)
    return {"model": model, "binding": binding}


def _legend_spec_from_request(
    legend_req: object, series: list[dict], node_ids: set[str], graphic_ids: set[str]
) -> list[dict]:
    """确认请求的 ``legend`` → 绑定合同的 ``legend``（就地翻译 series 节点 → id）。

    判据：区域节点必须在场景里且是图形元素（与系列节点同一 ``graphic_ids`` 口径，
    否则 `bind_node_not_graphic`）；条目指认的 series_node 必须是本次绑定的某个
    系列节点。形状与值域的最终把关在 `chartmodel.validate_binding`（同一个闸）。
    """
    if not isinstance(legend_req, list) or not legend_req:
        raise BindError("bind_evidence_invalid", "legend must be a non-empty list")
    series_by_node = {}
    for sr in series:
        series_by_node.setdefault(sr["node"], sr["id"])
    legend = []
    for i, item in enumerate(legend_req):
        if not isinstance(item, dict) or set(item) - set(LEGEND_REQUEST_KEYS):
            raise BindError("bind_evidence_invalid", f"legend[{i}] field set")
        node = item.get("node")
        if node not in node_ids:
            raise BindError("bind_node_not_graphic", f"legend node {node!r} is not in the scene")
        if node not in graphic_ids:
            raise BindError(
                "bind_node_not_graphic", f"legend node {node!r} is not a graphic element"
            )
        entries_req = item.get("entries")
        if not isinstance(entries_req, list) or not entries_req:
            raise BindError(
                "bind_evidence_invalid", f"legend[{i}].entries must be a non-empty list"
            )
        entries = []
        for k, entry in enumerate(entries_req):
            if not isinstance(entry, dict) or set(entry) - set(LEGEND_ENTRY_REQUEST_KEYS):
                raise BindError("bind_evidence_invalid", f"legend[{i}].entries[{k}] field set")
            out = {"index": entry.get("index"), "label": entry.get("label")}
            series_node = entry.get("series_node")
            if series_node is not None:
                if series_node not in series_by_node:
                    raise BindError(
                        "bind_node_not_graphic",
                        f"legend[{i}].entries[{k}].series_node {series_node!r} is not a bound series node",
                    )
                out["series"] = series_by_node[series_node]
            entries.append(out)
        legend.append(
            {
                "id": item.get("id"),
                "node": node,
                "rect": item.get("rect"),
                "entries": entries,
            }
        )
    return legend


def preview_deviation(
    model: dict, current_node_extents: dict[str, tuple[float, float]]
) -> list[dict]:
    """确认预览的几何偏差报告：每个系列「重算后位置」与「场景现状」的轴分数差。

    ``current_node_extents``：node id → 该节点当前在轴分数坐标系里的 (fx, fy)
    代表点（前端从场景几何提取；线性/log 的分数映射是纯公式——SG01 实验二
    证明它与 matplotlib 一致到 1e-9）。
    """
    ax = model["axes"][0]
    out = []
    for sr in model["series"]:
        col_x = model["dataset"]["columns"][sr["x"]]
        col_y = model["dataset"]["columns"][sr["y"]]
        first = _frac(col_x[0], ax["xlim"], ax["xscale"]), _frac(col_y[0], ax["ylim"], ax["yscale"])
        cur = current_node_extents.get(sr["node"])
        out.append(
            {
                "series": sr["id"],
                "node": sr["node"],
                "recomputed_first_frac": first,
                "current_frac": list(cur) if cur else None,
                "delta": [first[0] - cur[0], first[1] - cur[1]] if cur else None,
            }
        )
    return out


def _frac(v: float, lim: list[float], scale: str) -> float:
    lo, hi = lim
    if scale == "log":
        return (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return (v - lo) / (hi - lo)
