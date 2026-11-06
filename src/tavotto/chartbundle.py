"""可携带的声明式计算 SVG（ADR 0166 路径 C，纯标准库）。

包内只有原场景字节与闭式模型/绑定，既不读文件也不恢复 Python 来源权限。
可见图摘要只检测「保留元数据却修改了图」；重放是否与可见图一致，仍须由
调用方实际计算并比对，摘要不是计算等价证人。

源 SVG 和模型均无压缩；解码前检查编码长度，避免解压炸弹与无界分配。
SVG 节点、深度、引用展开与内嵌图像预算沿用 svgcheck 的唯一安全边界。
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from tavotto.engine import chartmodel
from tavotto.rendercore.svgcheck import (
    SVG_NS,
    SVGBudget,
    SVGError,
    parse_svg,
    strip_matplotlib_doctype,
)
from tavotto.svgscene import SceneError, build_node_index, scene_inventory

BUNDLE_SCHEMA = "tavotto.chart-bundle/1"
BUNDLE_ATTRIBUTE = "data-tavotto-chart"
_SVG_BUDGET = SVGBudget()
# 双层 base64（源字节在 JSON 中、JSON 在 XML 属性中）仍须留在静态 SVG 预算内。
MAX_SOURCE_BYTES = _SVG_BUDGET.max_bytes // 4
MAX_CHART_BYTES = _SVG_BUDGET.max_text_chars // 2
MAX_PAYLOAD_BYTES = ((MAX_SOURCE_BYTES + 2) // 3 * 4) + MAX_CHART_BYTES + 4096
MAX_ATTRIBUTE_CHARS = (MAX_PAYLOAD_BYTES + 2) // 3 * 4
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = _SVG_BUDGET.max_expanded_elements
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA = re.compile(r"tavotto\.chart-bundle/([1-9][0-9]*)\Z")


class ChartBundleError(ValueError):
    """有界、稳定的拒绝原因；既有模型与 SVG 错误码原样保留。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ChartBundle:
    source_svg: bytes
    model: dict
    binding: dict
    rendered_svg_digest: str

    @property
    def chart(self) -> dict:
        """按既有计算请求合同取模型/绑定；不含任何来源授权。"""
        return {"model": self.model, "binding": self.binding}


def _invalid(detail: str) -> None:
    raise ChartBundleError("chart_model_invalid", detail)


def _keys(value, expected: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        _invalid(f"{where} has an invalid field set")


def _json_budget(value) -> None:
    count = 0
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ChartBundleError("svg_resource_limit", "chart JSON budget exceeded")
        if isinstance(node, dict):
            if not all(isinstance(key, str) for key in node):
                _invalid("chart JSON keys must be strings")
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, (list, tuple)):
            pending.extend((child, depth + 1) for child in node)


def _json_bytes(value) -> bytes:
    _json_budget(value)
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ChartBundleError("chart_model_invalid", "chart is not finite UTF-8 JSON") from exc


def _loads(data: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                _invalid("duplicate chart JSON key")
            out[key] = value
        return out

    def reject_constant(_token):
        _invalid("non-finite chart JSON number")

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, ChartBundleError):
            raise
        raise ChartBundleError("chart_model_invalid", "invalid chart JSON") from exc
    _json_budget(value)
    return value


def _decode(value, maximum: int, where: str) -> bytes:
    if not isinstance(value, str):
        _invalid(f"{where} must be base64 text")
    if len(value) > (maximum + 2) // 3 * 4:
        raise ChartBundleError("svg_resource_limit", f"{where} budget exceeded")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ChartBundleError("chart_model_invalid", f"invalid {where} base64") from exc
    if len(raw) > maximum:
        raise ChartBundleError("svg_resource_limit", f"{where} budget exceeded")
    return raw


def _svg(data: bytes) -> ET.Element:
    try:
        return parse_svg(strip_matplotlib_doctype(data))
    except SVGError as exc:
        raise ChartBundleError(exc.code) from exc


def _check_markers(root: ET.Element, *, source: bool = False) -> None:
    for node in root.iter():
        if BUNDLE_ATTRIBUTE in node.attrib and (source or node is not root):
            _invalid("nested chart bundles are not admitted")


def _visible_digest(root: ET.Element) -> str:
    """去惰性元数据、规范化 XML；不依赖进程的 ElementTree 前缀注册状态。"""
    root = copy.deepcopy(root)
    root.attrib.pop(BUNDLE_ATTRIBUTE, None)
    for parent in root.iter():
        for child in list(parent):
            if child.tag in ("metadata", f"{{{SVG_NS}}}metadata"):
                parent.remove(child)
    canonical = ET.canonicalize(
        ET.tostring(root, encoding="unicode"), rewrite_prefixes=True, with_comments=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_chart(source_svg: bytes, chart: dict) -> tuple[dict, dict]:
    _keys(chart, {"model", "binding"}, "chart")
    raw = _json_bytes(chart)
    if len(raw) > MAX_CHART_BYTES:
        raise ChartBundleError("svg_resource_limit", "chart model budget exceeded")
    # 复制一份 JSON 值：输出不保留调用者的别名或自定义 Python 对象。
    chart = _loads(raw)
    try:
        model = chartmodel.validate_model(chart["model"])
        binding = chartmodel.validate_binding(chart["binding"], model)
        source_root = _svg(source_svg)
        _check_markers(source_root, source=True)
        # 场景物理尺寸也必须有效。节点索引不使用会在 5000 条截断的 UI 清单。
        scene = scene_inventory(source_svg)
        chartmodel.verify_scene(binding, scene["scene_digest"], set(build_node_index(source_root)))
    except (chartmodel.ChartModelError, SceneError) as exc:
        raise ChartBundleError(exc.code, exc.detail) from exc
    return model, binding


def embed_chart(rendered_svg: bytes, source_svg: bytes, chart: dict) -> bytes:
    """显式将自包含编辑数据嵌入产物；普通外观导出不应调用本函数。

    原场景以精确字节保留，故模型/绑定继续引用同一来源摘要。产物再次经过
    静态 SVG 校验；超出完整产物预算时失败，绝不输出无法再次导入的包。
    """
    if len(source_svg) > MAX_SOURCE_BYTES:
        raise ChartBundleError("svg_resource_limit", "chart source budget exceeded")
    model, binding = _validate_chart(source_svg, chart)
    root = _svg(rendered_svg)
    _check_markers(root)
    root.attrib.pop(BUNDLE_ATTRIBUTE, None)
    payload = {
        "schema": BUNDLE_SCHEMA,
        "model": model,
        "binding": binding,
        "source": {
            "encoding": "base64",
            "bytes": len(source_svg),
            "sha256": hashlib.sha256(source_svg).hexdigest(),
            "svg": base64.b64encode(source_svg).decode("ascii"),
        },
        "rendered": {"canonical_sha256": _visible_digest(root)},
    }
    raw = _json_bytes(payload)
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ChartBundleError("svg_resource_limit", "chart bundle budget exceeded")
    root.set(BUNDLE_ATTRIBUTE, base64.b64encode(raw).decode("ascii"))
    out = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    _svg(out)
    return out


def extract_chart(svg: bytes) -> ChartBundle | None:
    """校验并恢复包，零 I/O、零脚本执行。

    普通安全 SVG 返回 None。旧版只含 model/binding 的数据没有原场景，亦返回
    None，不声称具备冷恢复资格；它仍须是可解析的有界 JSON。新协议字段、摘要、
    来源或绑定不符均抛 ChartBundleError，调用方不可悄悄换回普通编辑来源。
    """
    root = _svg(svg)
    _check_markers(root)
    encoded = root.attrib.get(BUNDLE_ATTRIBUTE)
    if encoded is None:
        return None
    if len(encoded) > MAX_ATTRIBUTE_CHARS:
        raise ChartBundleError("svg_resource_limit", "chart bundle attribute budget exceeded")
    payload = _loads(_decode(encoded, MAX_PAYLOAD_BYTES, "chart bundle"))
    if isinstance(payload, dict) and set(payload) == {"model", "binding"}:
        return None
    _keys(payload, {"schema", "model", "binding", "source", "rendered"}, "chart bundle")
    schema = payload["schema"]
    match = _SCHEMA.fullmatch(schema) if isinstance(schema, str) else None
    if match is None:
        _invalid("invalid chart bundle schema")
    if schema != BUNDLE_SCHEMA:
        raise ChartBundleError("chart_model_unsupported", "unsupported chart bundle schema")
    source = payload["source"]
    _keys(source, {"encoding", "bytes", "sha256", "svg"}, "chart source")
    if source["encoding"] != "base64":
        _invalid("chart source encoding must be base64")
    size = source["bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_SOURCE_BYTES:
        raise ChartBundleError("svg_resource_limit", "invalid chart source size")
    if not isinstance(source["sha256"], str) or not _SHA256.fullmatch(source["sha256"]):
        _invalid("invalid chart source digest")
    source_svg = _decode(source["svg"], MAX_SOURCE_BYTES, "chart source")
    if len(source_svg) != size or hashlib.sha256(source_svg).hexdigest() != source["sha256"]:
        raise ChartBundleError("chart_source_mismatch", "embedded chart source changed")
    rendered = payload["rendered"]
    _keys(rendered, {"canonical_sha256"}, "chart rendered identity")
    digest = rendered["canonical_sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _invalid("invalid chart rendered digest")
    if _visible_digest(root) != digest:
        raise ChartBundleError("chart_source_mismatch", "rendered SVG changed outside the chart")
    model, binding = _validate_chart(
        source_svg, {"model": payload["model"], "binding": payload["binding"]}
    )
    return ChartBundle(source_svg, model, binding, digest)


def verify_replay(bundle: ChartBundle, rendered_svg: bytes) -> None:
    """核对调用方**实际重算**的完整 SVG 与包内可见图，失败不得继续恢复。

    应先按包内原始 model/binding 重放并调用本函数，再处理用户本次请求的新
    编辑。仅核对摘要字段或复用包本身字节，均不能代替这次实际重放。
    """
    root = _svg(rendered_svg)
    _check_markers(root)
    if _visible_digest(root) != bundle.rendered_svg_digest:
        raise ChartBundleError("chart_source_mismatch", "computed SVG differs from bundled artwork")
