"""计算图表 SVG 场景的只读盘点（ADR-XXXX，SG03）。

职责边界：给定一份**已经过安全校验**的静态 SVG 字节，产出绑定流程需要的
稳定场景事实——场景摘要、物理尺寸、viewBox 与**节点清单**（Binding 引用的
稳定节点 id 在这里定义）。本模块不做计算、不改字节、不执行任何内容；
安全边界只有 `rendercore.svgcheck` 一处（解析即校验，被拒输入绝不产出场景）。

节点 id 规则（SG05/SG06 的引用契约）：
- 元素自带 `id` 且全场景唯一 → 原样使用（matplotlib 自产 SVG 的
  ``axes_1`` / ``line2d_13`` 等原样保留）；
- 否则按文档序生成 ``n<祖先索引>-<本索引>`` 形式的路径 id；与原有 id 撞名时
  追加 ``#2``、``#3``……同一份字节永远得到同一张清单（摘要钉住一切）。
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from tavotto.rendercore.svgcheck import SVGBudget, parse_svg, strip_matplotlib_doctype

#: 盘点清单的最大条目数（防御性上限；svgcheck 的 20 万元素预算仍在解析层兜底）。
MAX_INVENTORY_NODES = 5000

#: 场景盘点可报告的「可绑定图形元素」白名单（与 svgcheck 的静态元素集对齐的子集）。
GRAPHIC_TAGS = frozenset(
    {
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polygon",
        "polyline",
        "text",
        "image",
        "use",
        "g",
    }
)

_SCENE_NODE_BUDGET = SVGBudget().max_elements


class SceneError(ValueError):
    """场景盘点失败。`code` 是稳定错误码（进 web/src/i18n/errors.json 时同步）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _physical_size(root: ET.Element) -> tuple[float, float, list[float] | None]:
    """物理 pt 尺寸与 viewBox（换算规则与 svgasset._length 同一口径）。"""
    from tavotto.svgasset import UnsupportedSVG, _length  # noqa: PLC0415 — 延后避免环

    width = root.attrib.get("width")
    height = root.attrib.get("height")
    vb = root.attrib.get("viewBox")
    view_box = None
    if vb:
        try:
            parts = [float(v) for v in vb.replace(",", " ").split()]
        except ValueError as exc:
            raise SceneError("svg_scene_invalid", f"bad viewBox: {exc}") from exc
        if len(parts) != 4:
            raise SceneError("svg_scene_invalid", "viewBox must have 4 components")
        view_box = parts
    try:
        if width is not None and height is not None:
            w_pt = _length(width)
            h_pt = _length(height)
        elif view_box is not None:
            w_pt, h_pt = _length(f"{view_box[2]}pt"), _length(f"{view_box[3]}pt")
        else:
            raise SceneError("svg_scene_invalid", "no width/height and no viewBox")
    except UnsupportedSVG as exc:
        raise SceneError("svg_dimensions", str(exc)) from exc
    return w_pt, h_pt, view_box


def scene_inventory(data: bytes) -> dict:
    """解析并盘点一份 SVG 场景；任何失败都抛 `SceneError`（原件不动）。"""
    if data[:2] == b"\x1f\x8b":
        raise SceneError("svgz_unsupported", "gzip-compressed SVG is not admitted")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SceneError("svg_encoding_unsupported", str(exc)) from exc
    try:
        root = parse_svg(strip_matplotlib_doctype(data))
    except Exception as exc:  # noqa: BLE001 — svgcheck 的稳定码原样上抛
        code = getattr(exc, "code", "svg_scene_invalid")
        raise SceneError(code, str(exc)) from exc
    if root.tag.split("}")[-1] != "svg":
        raise SceneError("svg_scene_invalid", "root element is not <svg>")

    w_pt, h_pt, view_box = _physical_size(root)

    nodes: list[dict] = []
    truncated = False
    used_ids: set[str] = set()

    def visit(el: ET.Element, path: str) -> None:
        nonlocal truncated
        if len(nodes) >= MAX_INVENTORY_NODES:
            truncated = True
            return
        tag = el.tag.split("}")[-1]
        children = [c for c in el if isinstance(c.tag, str)]
        child_path = f"{path}/{tag}" if path else tag
        if tag in GRAPHIC_TAGS or tag == "svg":
            node_id = el.attrib.get("id")
            if node_id:
                if node_id in used_ids:
                    raise SceneError("svg_scene_invalid", f"duplicate element id {node_id!r}")
                used_ids.add(node_id)
            else:
                # id 规则唯一出处：_logical_id（build_node_index 同源）
                node_id = _logical_id(el, path, used_ids)
                used_ids.add(node_id)
            entry: dict = {"id": node_id, "tag": tag}
            if tag == "text":
                content = "".join(el.itertext()).strip()
                if content:
                    entry["text"] = content[:120]
            style = {}
            for decl in el.attrib.get("style", "").split(";"):
                if ":" in decl:
                    k, v = decl.split(":", 1)
                    style[k.strip()] = v.strip()
            for key in ("fill", "stroke"):
                for source in (el.attrib, style):
                    if key in source:
                        entry[key] = str(source[key]).strip()[:40]
                        break
            if el.attrib.get("transform"):
                entry["transformed"] = True
            # 区域所有权需要的结构事实（SG07）：裁剪与透明
            if el.attrib.get("clip-path"):
                entry["clipped"] = True
            for op_key in ("opacity", "fill-opacity", "stroke-opacity"):
                raw = el.attrib.get(op_key) or style.get(op_key)
                if raw is not None:
                    try:
                        alpha = float(raw)
                    except ValueError:
                        continue
                    if alpha < 1.0:
                        entry["alpha"] = min(float(entry.get("alpha", 1.0)), alpha)
            if tag == "use":
                href = el.attrib.get("href") or el.attrib.get(
                    "{http://www.w3.org/1999/xlink}href", ""
                )
                if href:
                    entry["href"] = href[:80]
            nodes.append(entry)
        for i, child in enumerate(children):
            if len(nodes) >= MAX_INVENTORY_NODES:
                truncated = True
                break
            visit(child, f"{child_path}[{i}]" if tag != "svg" else f"{tag}[{i}]")

    visit(root, "")
    return {
        "scene_digest": hashlib.sha256(data).hexdigest(),
        "w_pt": w_pt,
        "h_pt": h_pt,
        "view_box": view_box,
        "nodes": nodes,
        "truncated": truncated,
        "node_budget": _SCENE_NODE_BUDGET,
    }


def _logical_id(el: ET.Element, path: str, used: set[str]) -> str:
    """inventory 与节点索引共用的 id 规则（唯一出处；改这里两处同步）。"""
    node_id = el.attrib.get("id")
    if node_id:
        return node_id
    tag = el.tag.split("}")[-1]
    base = "n" + (f"{path}/{tag}" if path else tag).replace("/", "-")
    logical = base
    n = 2
    while logical in used:
        logical = f"{base}#{n}"
        n += 1
    return logical


def build_node_index(root: ET.Element) -> dict[str, ET.Element]:
    """同一走法下的 逻辑 id → 实际元素 映射（compose 解析绑定引用的唯一入口）。

    与 `scene_inventory` 共用 `_logical_id` 与同一遍历次序：绑定里的逻辑 id 在
    任何一次重新解析中指到同一个元素（B5）。
    """
    index: dict[str, ET.Element] = {}
    used: set[str] = set()

    def visit(el: ET.Element, path: str) -> None:
        tag = el.tag.split("}")[-1]
        child_path = f"{path}/{tag}" if path else tag
        if tag in GRAPHIC_TAGS or tag == "svg":
            logical = _logical_id(el, path, used)
            used.add(logical)
            index.setdefault(logical, el)
        for i, child in enumerate(c for c in el if isinstance(c.tag, str)):
            visit(child, f"{child_path}[{i}]" if tag != "svg" else f"{tag}[{i}]")

    visit(root, "")
    return index


def read_scene(path: Path) -> dict:
    """有界读取 + 盘点（`svgasset.read` 的只读纪律：每次服务都重校验）。"""
    budget = SVGBudget()
    with open(path, "rb") as fh:
        data = fh.read(budget.max_bytes + 1)
    if len(data) > budget.max_bytes:
        raise SceneError("svg_resource_limit", f"scene exceeds {budget.max_bytes} bytes")
    return scene_inventory(data)
