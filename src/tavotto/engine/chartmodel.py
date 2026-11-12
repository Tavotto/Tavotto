"""SVG 计算图表的模型与身份契约（ADR-XXXX）——schema、校验、摘要与稳定错误码。

这里只放**判据**：`ChartModel` / `Binding` / `SourceDescriptor` 的形状、闭式
字段集、非有限数闸与身份摘要。I/O 与产品接线分别在后续批次（SG03 导入、
SG06 绑定流程、SG11 持久化）。前端的同源镜像在 `web/src/types/chartModel.ts`，
两侧共同消费 `tests/golden/chart_model_vectors.json`（唯一产生者
`scripts/gen_chart_model_vectors.py`，手改无效）。

设计要点（ADR-XXXX §3）：
- **闭式字段集**：模型里没有任何可执行代码的容身之处——未知键直接拒绝，
  数值必须有限（NaN/Infinity 字面量在读入闸就死），字符串字段进闭集。
- **身份是摘要不是路径**：场景/模型/数据各算 sha256，身份不包含机器路径、
  不包含时间戳；两份模型等价当且仅当摘要相同。
- **版本关系显式**：模型带 `data_digest` 与 `scene_digest`；绑定带 `model_rev`
  ——数据或场景变了，旧绑定必须被真实消费方拒绝，不许按旧位置套新对象。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from typing import Any

#: 模型 schema 版本。次版本号向后兼容（新增可选字段），主版本号变化才拒读。
MODEL_SCHEMA = "tavotto.chart-model/1"
_MODEL_SCHEMA_RE = re.compile(r"^tavotto\.chart-model/(\d+)$")
#: 本构建能读的最高主版本。
_SUPPORTED_MAJOR = 1

#: 来源种类闭集：figure = 可信 Figure 来源（有执行回执/配对身份，编辑走既有管线）；
#: declarative = 用户显式绑定的声明式模型（本文件定义的东西）。
SOURCE_FIGURE = "figure"
SOURCE_DECLARATIVE = "declarative"
SOURCE_KINDS = (SOURCE_FIGURE, SOURCE_DECLARATIVE)

#: 轴尺度闭集（v1；扩尺度 = 升次版本并同步两侧镜像与 golden vectors）。
SCALES = ("linear", "log")
#: 轴维度闭集：numeric = 连续映射（线性/对数）；categorical = 类别位置（0..n-1），
#: `categories` 列出类目——类别轴绝不套连续数值映射（计划 SG06 禁令）。
AXIS_KINDS = ("numeric", "categorical")
#: 系列种类闭集（v1；SG08/SG09 按同一条升版路径扩族）。
SERIES_KINDS = ("line", "scatter", "bar", "errorbar", "image")
#: 系列样式可写键闭集（与既有 override 词汇对齐，语义见 engine/overrides.py）。
SERIES_STYLE_KEYS = (
    "color",
    "linewidth",
    "linestyle",
    "marker",
    "markersize",
    "alpha",
    "facecolor",
    "edgecolor",
    "bar_width",
    "label",
    "cmap",
    "vmin",
    "vmax",
)

#: 稳定错误码闭集（前后端同源；文案在 web/src/i18n/errors.json 落地时同步）。
ERROR_CODES = (
    "chart_model_unsupported",  # schema 主版本比本构建新
    "chart_model_invalid",  # 结构违反（detail 带字段级原因）
    "chart_source_mismatch",  # 绑定记录的场景摘要 ≠ 当前场景摘要
    "chart_data_changed",  # 数据摘要 ≠ 模型记录的摘要
    "chart_duplicate_id",  # axes/series id 重复
    "chart_binding_stale",  # 绑定的 model_rev < 当前模型 rev
    "chart_node_missing",  # 绑定的场景节点在当前场景里不存在
    "chart_data_invalid",  # 数据形状/缺失值不合法
    "chart_engine_mismatch",  # 计算环境身份与重放合同不一致
)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ChartModelError(ValueError):
    """模型/绑定/数据判据失败。`code` ∈ ERROR_CODES，`detail` 给字段级原因。"""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in ERROR_CODES:
            raise AssertionError(f"undisclosed error code: {code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# 摘要（身份唯一出处；两份模型等价 ⇔ 摘要相同）
# ---------------------------------------------------------------------------


_INT53 = 2**53


def identity_bytes(value: Any, out: list[bytes] | None = None) -> bytes:
    """摘要输入的规范化序列化——**不是 JSON**：类型标记 + 数值按 IEEE 754 位模式。

    为什么要离开 JSON 文本形态：Python 写 `1.0` 而 JS 写 `1`，浮点文本形态两侧
    永远对不齐。这里 1 与 1.0 是同一个值（同一标记），非整值用 big-endian double
    的 16 位十六进制（`struct.pack('>d')`），两侧逐位一致（web/src/types/chartModel.ts
    的 `identityBytes` 是本函数的镜像，改一侧必须同步另一侧与 golden vectors）。
    """
    buf = out if out is not None else []
    if value is None:
        buf.append(b"N")
    elif isinstance(value, bool):
        buf.append(b"T" if value else b"F")
    elif isinstance(value, int):
        if -_INT53 < value < _INT53:
            buf.append(b"i%d;" % value)
        else:  # 超出 JS 安全整数：按 double 记（精度在另一侧本来就守不住）
            buf.append(b"d" + struct.pack(">d", float(value)).hex().encode())
    elif isinstance(value, float):
        if value.is_integer() and -_INT53 < value < _INT53:
            buf.append(b"i%d;" % int(value))
        else:
            buf.append(b"d" + struct.pack(">d", value).hex().encode())
    elif isinstance(value, str):
        raw = value.encode("utf-8")
        buf.append(b"s%d:%s" % (len(raw), raw.hex().encode()))
    elif isinstance(value, (list, tuple)):
        buf.append(b"[")
        for item in value:
            identity_bytes(item, buf)
        buf.append(b"]")
    elif isinstance(value, dict):
        buf.append(b"{")
        for key in sorted(value):
            if not isinstance(key, str):
                raise ChartModelError("chart_model_invalid", "identity keys must be strings")
            identity_bytes(key, buf)
            identity_bytes(value[key], buf)
        buf.append(b"}")
    else:
        raise ChartModelError("chart_model_invalid", f"identity: unsupported value {type(value)!r}")
    return b"".join(buf)


def digest_of(value: Any) -> str:
    return hashlib.sha256(identity_bytes(value)).hexdigest()


def bytes_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 读入闸：NaN/Infinity 字面量在读的时候就死（与 documents.loads_document 同一纪律）
# ---------------------------------------------------------------------------


def _reject_constant(token: str) -> Any:
    raise ChartModelError("chart_model_invalid", f"non-finite literal {token!r}")


def loads_model(text: str | bytes) -> Any:
    """解析模型/绑定 JSON；NaN / Infinity / -Infinity 一律 `chart_model_invalid`。"""
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ChartModelError:
        raise
    except (ValueError, TypeError) as exc:
        raise ChartModelError("chart_model_invalid", f"not JSON: {exc}") from exc


def _finite(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChartModelError("chart_model_invalid", f"{where} must be a number")
    try:
        v = float(value)
    except OverflowError:
        raise ChartModelError("chart_model_invalid", f"{where} must be finite") from None
    if not math.isfinite(v):
        raise ChartModelError("chart_model_invalid", f"{where} must be finite")
    return v


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ChartModelError("chart_model_invalid", f"{where} must be a non-empty string")
    return value


def _keys(obj: Any, allowed: tuple[str, ...], where: str) -> None:
    """闭式字段集：未知键拒绝（模型里没有可执行代码的容身之处）。"""
    if not isinstance(obj, dict):
        raise ChartModelError("chart_model_invalid", f"{where} must be an object")
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ChartModelError("chart_model_invalid", f"{where}: unknown keys {unknown}")


# ---------------------------------------------------------------------------
# ChartModel
# ---------------------------------------------------------------------------

_AXES_KEYS = (
    "id",
    "rect",
    "xlim",
    "ylim",
    "xscale",
    "yscale",
    "invert_x",
    "invert_y",
    "xlabel",
    "ylabel",
    "kind",
    "categories",
)
_SERIES_KEYS = ("id", "kind", "axes", "node", "x", "y", "yerr", "grid", "cbar_node", "style")
_DATASET_KEYS = ("columns", "grids")


def validate_model(model: Any) -> dict:
    """校验一份 ChartModel（就地返回它），失败抛 `ChartModelError`。"""
    _keys(
        model,
        ("schema", "rev", "axes", "series", "dataset", "scene_digest", "data_digest", "engine"),
        "model",
    )
    schema = _str(model.get("schema"), "model.schema")
    m = _MODEL_SCHEMA_RE.match(schema)
    if m is None:
        raise ChartModelError("chart_model_invalid", f"model.schema {schema!r} malformed")
    if int(m.group(1)) > _SUPPORTED_MAJOR:
        raise ChartModelError("chart_model_unsupported", f"{schema} > /{_SUPPORTED_MAJOR}")
    rev = model.get("rev")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        raise ChartModelError("chart_model_invalid", "model.rev must be a positive int")

    axes = model.get("axes")
    if not isinstance(axes, list) or not axes:
        raise ChartModelError("chart_model_invalid", "model.axes must be a non-empty list")
    ids: set[str] = set()
    for i, ax in enumerate(axes):
        _keys(ax, _AXES_KEYS, f"axes[{i}]")
        ax_id = _str(ax.get("id"), f"axes[{i}].id")
        if not _ID_RE.match(ax_id):
            raise ChartModelError("chart_model_invalid", f"axes[{i}].id malformed")
        if ax_id in ids:
            raise ChartModelError("chart_duplicate_id", f"axes id {ax_id!r}")
        ids.add(ax_id)
        rect = ax.get("rect")
        if (
            not isinstance(rect, list)
            or len(rect) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in rect)
        ):
            raise ChartModelError("chart_model_invalid", f"axes[{i}].rect must be [x,y,w,h]")
        for j, v in enumerate(rect):
            _finite(v, f"axes[{i}].rect[{j}]")
        if rect[2] <= 0 or rect[3] <= 0:
            raise ChartModelError("chart_model_invalid", f"axes[{i}].rect must be positive")
        for key in ("xlim", "ylim"):
            lim = ax.get(key)
            if not isinstance(lim, list) or len(lim) != 2:
                raise ChartModelError("chart_model_invalid", f"axes[{i}].{key} must be [lo,hi]")
            lo = _finite(lim[0], f"axes[{i}].{key}[0]")
            hi = _finite(lim[1], f"axes[{i}].{key}[1]")
            if lo == hi:
                raise ChartModelError("chart_model_invalid", f"axes[{i}].{key} is empty")
        for key in ("xscale", "yscale"):
            if ax.get(key) not in SCALES:
                raise ChartModelError(
                    "chart_model_invalid", f"axes[{i}].{key} must be one of {SCALES}"
                )
        # log 轴的范围必须为正（负值/零 log 是无效模型，不许带着假范围进脚手架）
        for lim_key, scale_key in (("xlim", "xscale"), ("ylim", "yscale")):
            if ax.get(scale_key) == "log":
                lim = ax.get(lim_key)
                if any(float(v) <= 0 for v in lim):
                    raise ChartModelError(
                        "chart_data_invalid", f"axes[{i}].{lim_key} must be positive on a log scale"
                    )
        for key in ("invert_x", "invert_y"):
            if not isinstance(ax.get(key), bool):
                raise ChartModelError("chart_model_invalid", f"axes[{i}].{key} must be bool")
        for key in ("xlabel", "ylabel"):
            if key in ax and not isinstance(ax[key], str):
                raise ChartModelError("chart_model_invalid", f"axes[{i}].{key} must be a string")
        kind = ax.get("kind", "numeric")
        if kind not in AXIS_KINDS:
            raise ChartModelError(
                "chart_model_invalid", f"axes[{i}].kind must be one of {AXIS_KINDS}"
            )
        if kind == "categorical":
            cats = ax.get("categories")
            if (
                not isinstance(cats, list)
                or not cats
                or not all(isinstance(c, str) and c for c in cats)
            ):
                raise ChartModelError(
                    "chart_model_invalid", f"axes[{i}].categories must be a non-empty string list"
                )

    dataset = model.get("dataset")
    _keys(dataset, _DATASET_KEYS, "model.dataset")
    columns = dataset.get("columns", {})
    grids = dataset.get("grids", {})
    if not isinstance(columns, dict):
        raise ChartModelError("chart_model_invalid", "model.dataset.columns must be an object")
    if not isinstance(grids, dict):
        raise ChartModelError("chart_model_invalid", "model.dataset.grids must be an object")
    if not columns and not grids:
        raise ChartModelError("chart_model_invalid", "model.dataset needs columns or grids")
    col_names = set()
    for name, col in columns.items():
        _str(name, "dataset column name")
        col_names.add(name)
        if not isinstance(col, list):
            raise ChartModelError(
                "chart_model_invalid", f"dataset column {name!r} must be an array"
            )
        for j, v in enumerate(col):
            if isinstance(v, str):
                # 文本列（类别/标签）：声明 categorical 轴的 categories / bar 的位置标签
                if not v:
                    raise ChartModelError(
                        "chart_data_invalid", f"dataset column {name!r}[{j}] empty string"
                    )
                continue
            _finite(v, f"dataset column {name!r}[{j}]")

    grid_names: set[str] = set()
    for name, grid in grids.items():
        _str(name, "grid name")
        grid_names.add(name)
        _keys(grid, ("values",), f"grids[{name!r}]")
        values = grid.get("values")
        if not isinstance(values, list) or not values:
            raise ChartModelError("chart_data_invalid", f"grid {name!r} must be non-empty")
        width = None
        for r, row in enumerate(values):
            if not isinstance(row, list) or not row:
                raise ChartModelError(
                    "chart_data_invalid", f"grid {name!r} row {r} must be non-empty"
                )
            if width is None:
                width = len(row)
            elif len(row) != width:
                raise ChartModelError("chart_data_invalid", f"grid {name!r} row {r} ragged")
            for c, v in enumerate(row):
                _finite(v, f"grid {name!r}[{r}][{c}]")

    series = model.get("series")
    if not isinstance(series, list) or not series:
        raise ChartModelError("chart_model_invalid", "model.series must be a non-empty list")
    axes_ids = {ax["id"] for ax in axes}
    series_ids: set[str] = set()
    for i, sr in enumerate(series):
        _keys(sr, _SERIES_KEYS, f"series[{i}]")
        s_id = _str(sr.get("id"), f"series[{i}].id")
        if not _ID_RE.match(s_id):
            raise ChartModelError("chart_model_invalid", f"series[{i}].id malformed")
        if s_id in ids or s_id in series_ids:
            raise ChartModelError("chart_duplicate_id", f"series id {s_id!r}")
        series_ids.add(s_id)
        if sr.get("kind") not in SERIES_KINDS:
            raise ChartModelError(
                "chart_model_invalid", f"series[{i}].kind must be one of {SERIES_KINDS}"
            )
        if sr.get("axes") not in axes_ids:
            raise ChartModelError(
                "chart_model_invalid", f"series[{i}].axes {sr.get('axes')!r} is not a known axis"
            )
        _str(sr.get("node"), f"series[{i}].node")
        if sr.get("kind") == "image":
            # 图像/网格系列：数据源是 grid，不是 x/y 列
            if sr.get("grid") not in grid_names:
                raise ChartModelError(
                    "chart_data_invalid",
                    f"series[{i}].grid {sr.get('grid')!r} is not a dataset grid",
                )
        else:
            for key in ("x", "y"):
                if sr.get(key) not in col_names:
                    raise ChartModelError(
                        "chart_data_invalid",
                        f"series[{i}].{key} {sr.get(key)!r} is not a dataset column",
                    )
        style = sr.get("style", {})
        _keys(style, SERIES_STYLE_KEYS, f"series[{i}].style")
        for k, v in style.items():
            if k in ("linewidth", "markersize", "alpha"):
                fv = _finite(v, f"series[{i}].style.{k}")
                if k == "alpha" and not 0.0 <= fv <= 1.0:
                    raise ChartModelError(
                        "chart_model_invalid", f"series[{i}].style.alpha out of range"
                    )
            if k in ("vmin", "vmax"):
                fv = _finite(v, f"series[{i}].style.{k}")
                if "vmin" in style and "vmax" in style and style["vmin"] >= style["vmax"]:
                    raise ChartModelError(
                        "chart_model_invalid", f"series[{i}].style requires vmin < vmax"
                    )

    _str(model.get("scene_digest"), "model.scene_digest")
    _str(model.get("data_digest"), "model.data_digest")
    engine = model.get("engine")
    _keys(engine, ("matplotlib", "numpy", "tavotto"), "model.engine")
    for k in ("matplotlib", "numpy", "tavotto"):
        _str(engine.get(k), f"model.engine.{k}")

    # 记录在模型里的摘要必须与自身内容一致（防「换数据不改账」）
    digest_payload = {"columns": columns}
    if grids:
        digest_payload["grids"] = grids
    if model["data_digest"] != digest_of(digest_payload):
        raise ChartModelError(
            "chart_data_changed", "model.data_digest does not match model.dataset"
        )
    return model


def dumps_model(model: dict) -> str:
    """校验并序列化（往返入口：loads → validate → dumps 逐字节稳定）。"""
    return json.dumps(validate_model(model), sort_keys=True, indent=1, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Binding：场景节点 ↔ 模型对象，带代际
# ---------------------------------------------------------------------------

_BINDING_KEYS = ("model_rev", "scene_digest", "entries", "legend")
_ENTRY_KEYS = ("object", "node")
_OBJECT_RE = re.compile(r"^(axes|series):([a-z][a-z0-9_]{0,63})$")
#: 绑定可选 ``legend`` 合同的闭式字段集（N4）：图例区域 ↔ 模型系列的显式关系。
#: 放 binding 不放 model——它是「场景区域 ↔ 模型对象」关系，与 binding 本职同构；
#: ``panel_identity`` 已把 binding digest 算进面板身份，图例关系变化自动产生新版本。
_LEGEND_KEYS = ("id", "node", "rect", "entries")
_LEGEND_ITEM_KEYS = ("index", "series", "label")


def validate_legend_spec(legend: Any, model: dict) -> list:
    """校验绑定里的可选 ``legend`` 合同（就地返回它）；判据唯一出处。

    两个消费方：``validate_binding``（确认/载入/导出闸）与 worker 脚手架
    （``svgchart`` 不信任父进程，对照 ``validate_model`` 的既有先例）。判据：

    - ``id``：图例 role 名（``legend:<id>`` 进 chartmanifest），``_ID_RE`` 同款，
      多条 legend 之间唯一；
    - ``node``：旧 SVG 图例区域节点 id，legend 之间唯一；与绑定 entries 节点的
      互斥由 ``validate_binding`` 对照 entries 核对（这里没有 entries 可比）；
    - ``rect``：[x, y, w, h] 场景用户单位，与 axes.rect 同一判据（供脚手架按
      legend 盒左上角定位，语义冻结见 N4 计划 §5.3 方案 A）；
    - ``entries``：每项 ``{index, label, series?}``——index 是用户确认的原始序
      （≥0、唯一）；label 是确认预览里旧图例该条目的显示文字（**随合同持久化**，
      冷重放确定性来自文档，不来自对旧 SVG 的再解析）；series 缺席 = custom
      （该条目保持脚本原样示意、不跟随任何系列），在场必须命中模型 series id
      （显式指认允许两条指到同一系列——同名同色多系列歧义由显式消解，不猜）。

    不允许只绑区域不列条目——区域绑了、条目未列就是半接。
    """
    if not isinstance(legend, list) or not legend:
        raise ChartModelError("chart_model_invalid", "binding.legend must be a non-empty list")
    known_series = {sr["id"]: sr.get("kind") for sr in model["series"]}
    ids: set[str] = set()
    nodes: set[str] = set()
    for i, item in enumerate(legend):
        _keys(item, _LEGEND_KEYS, f"binding.legend[{i}]")
        legend_id = _str(item.get("id"), f"binding.legend[{i}].id")
        if not _ID_RE.match(legend_id):
            raise ChartModelError("chart_model_invalid", f"binding.legend[{i}].id malformed")
        if legend_id in ids:
            raise ChartModelError("chart_duplicate_id", f"legend id {legend_id!r} twice")
        ids.add(legend_id)
        node = _str(item.get("node"), f"binding.legend[{i}].node")
        if node in nodes:
            raise ChartModelError("chart_duplicate_id", f"legend node {node!r} twice")
        nodes.add(node)
        rect = item.get("rect")
        if (
            not isinstance(rect, list)
            or len(rect) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in rect)
        ):
            raise ChartModelError(
                "chart_model_invalid", f"binding.legend[{i}].rect must be [x,y,w,h]"
            )
        for j, v in enumerate(rect):
            _finite(v, f"binding.legend[{i}].rect[{j}]")
        if rect[2] <= 0 or rect[3] <= 0:
            raise ChartModelError(
                "chart_model_invalid", f"binding.legend[{i}].rect must be positive"
            )
        entries = item.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ChartModelError(
                "chart_model_invalid", f"binding.legend[{i}].entries must be a non-empty list"
            )
        indexes: set[int] = set()
        for k, entry in enumerate(entries):
            _keys(entry, _LEGEND_ITEM_KEYS, f"binding.legend[{i}].entries[{k}]")
            index = entry.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ChartModelError(
                    "chart_model_invalid",
                    f"binding.legend[{i}].entries[{k}].index must be a non-negative int",
                )
            if index in indexes:
                raise ChartModelError(
                    "chart_duplicate_id", f"binding.legend[{i}] entry index {index} twice"
                )
            indexes.add(index)
            _str(entry.get("label"), f"binding.legend[{i}].entries[{k}].label")
            if "series" in entry:
                if entry["series"] not in known_series:
                    raise ChartModelError(
                        "chart_model_invalid",
                        f"binding.legend[{i}].entries[{k}].series {entry['series']!r} is not in the model",
                    )
                if known_series[entry["series"]] == "image":
                    # matplotlib 没有图像系列的图例 handler：跟着它创建会静默
                    # 丢条目（半接），必须在合同闸就拒绝，不进脚手架。
                    raise ChartModelError(
                        "chart_model_invalid",
                        f"binding.legend[{i}].entries[{k}]: an image series cannot be "
                        "followed by a legend entry",
                    )
    return legend


def validate_binding(binding: Any, model: dict) -> dict:
    """校验绑定并对照模型代际；失败抛 `ChartModelError`。"""
    _keys(binding, _BINDING_KEYS, "binding")
    rev = binding.get("model_rev")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        raise ChartModelError("chart_model_invalid", "binding.model_rev must be a positive int")
    if rev != model.get("rev"):
        raise ChartModelError(
            "chart_binding_stale", f"binding rev {rev} != model rev {model.get('rev')}"
        )
    if binding.get("scene_digest") != model.get("scene_digest"):
        raise ChartModelError("chart_source_mismatch", "binding.scene_digest != model.scene_digest")
    entries = binding.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ChartModelError("chart_model_invalid", "binding.entries must be a non-empty list")
    known_axes = {f"axes:{ax['id']}" for ax in model["axes"]}
    known_series = {f"series:{sr['id']}" for sr in model["series"]}
    seen_series: set[str] = set()
    seen_nodes: set[str] = set()
    for i, entry in enumerate(entries):
        _keys(entry, _ENTRY_KEYS, f"binding.entries[{i}]")
        obj = _str(entry.get("object"), f"binding.entries[{i}].object")
        if _OBJECT_RE.match(obj) is None or (obj not in known_axes and obj not in known_series):
            raise ChartModelError(
                "chart_model_invalid", f"binding.entries[{i}].object {obj!r} is not in the model"
            )
        # 系列与节点一一对应；轴区域可指定多个节点（普通 SVG 的轴框/刻度是散件）
        if obj in known_series:
            if obj in seen_series:
                raise ChartModelError("chart_duplicate_id", f"binding series {obj!r} twice")
            seen_series.add(obj)
        node = _str(entry.get("node"), f"binding.entries[{i}].node")
        if node in seen_nodes:
            raise ChartModelError("chart_duplicate_id", f"binding node {node!r} twice")
        seen_nodes.add(node)
    if "legend" in binding:
        legend = validate_legend_spec(binding["legend"], model)
        # 图例区域节点与绑定条目节点互斥：一个旧节点要么被系列/轴区域接管，
        # 要么是图例区域，不能同时是两者（组合端 targets 会重叠）。
        collisions = sorted(seen_nodes & {item["node"] for item in legend})
        if collisions:
            raise ChartModelError(
                "chart_duplicate_id",
                f"legend node collides with bound entries: {collisions}",
            )
    return binding


def verify_scene(binding: dict, scene_digest: str, scene_node_ids: set[str]) -> None:
    """真实消费方（替换器/导出）在动手前核对的门：场景没换、节点都在。"""
    if binding.get("scene_digest") != scene_digest:
        raise ChartModelError(
            "chart_source_mismatch", "scene bytes changed since the binding was confirmed"
        )
    missing = [e["node"] for e in binding["entries"] if e["node"] not in scene_node_ids]
    missing += [
        item["node"] for item in binding.get("legend", []) if item["node"] not in scene_node_ids
    ]
    if missing:
        raise ChartModelError("chart_node_missing", f"bound nodes absent from the scene: {missing}")


# ---------------------------------------------------------------------------
# 计算环境身份（重放合同：不同环境不得声称同一计算版本）
# ---------------------------------------------------------------------------


def engine_identity(matplotlib_version: str, numpy_version: str, tavotto_version: str) -> dict:
    """worker 在脚手架命令里回报的身份；形状固定，摘要进面板身份。"""
    identity = {
        "matplotlib": _str(matplotlib_version, "engine.matplotlib"),
        "numpy": _str(numpy_version, "engine.numpy"),
        "tavotto": _str(tavotto_version, "engine.tavotto"),
    }
    return identity


def engine_digest(identity: dict) -> str:
    _keys(identity, ("matplotlib", "numpy", "tavotto"), "engine")
    return digest_of(identity)


def panel_identity(
    source_kind: str, scene_sha: str, model: dict | None, binding: dict | None = None
) -> str:
    """一个计算面板的版本身份（透明字符串，消费方只比较不解析）。

    完整身份（N3）：来源 + 模型 + **绑定**——同一模型绑到不同区域（绑定不同）
    产生不同画面，必须是不同版本。``binding`` 缺省时只兼容 figure 来源；
    declarative 来源必须带绑定。
    """
    if source_kind not in SOURCE_KINDS:
        raise ChartModelError("chart_model_invalid", f"unknown source kind {source_kind!r}")
    if source_kind == SOURCE_FIGURE:
        return digest_of({"kind": SOURCE_FIGURE, "scene": scene_sha})
    if model is None:
        raise ChartModelError("chart_model_invalid", "declarative identity needs the model")
    if binding is None:
        raise ChartModelError("chart_model_invalid", "declarative identity needs the binding")
    return digest_of(
        {
            "kind": SOURCE_DECLARATIVE,
            "scene": scene_sha,
            "model": digest_of(model),
            "binding": digest_of(binding),
        }
    )
