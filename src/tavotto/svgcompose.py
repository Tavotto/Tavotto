"""场景组合：把脚手架片段接进原场景（ADR 0166，SG07 父侧）。

输入是**已经核对过身份**的事实：原场景字节、chartmodel 校验过的模型与绑定、
worker 脚手架的片段。组合规则（SG01 实验三的契约）：

- 绑定的**系列节点**：原元素整个替换为片段里对应的系列组；
- 绑定的**轴区域节点**（``axes:ax1`` 的多个节点）：从场景移除，由片段的
  axis 子树替代（普通 SVG 的手绘刻度/框线随区域退役——确认预览里已列出）；
- 其余一切节点逐字节保留；被外部引用的 defs 先**上提**到根 defs（跨区域
  def 生命周期，实验三事实 2）；
- 片段内 id 全部加后缀隔离；片段引用的根级 defs（matplotlib 3.11 把 clipPath
  放在 ``<svg>`` 末尾）随迁并同样加后缀。
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"s": SVG_NS}


class ComposeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _suffix_ids(el: ET.Element, suffix: str) -> None:
    """id 与内部引用统一加后缀（嫁接片段不能与原场景撞 id）。"""
    for e in el.iter():
        if "id" in e.attrib:
            e.attrib["id"] = e.attrib["id"] + suffix
        for k, v in list(e.attrib.items()):
            if "url(#" in v:
                e.attrib[k] = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{m.group(1)}{suffix})", v)
            elif k.endswith("href") and v.startswith("#"):
                e.attrib[k] = "#" + v[1:] + suffix


def _is_descendant(node: ET.Element, ancestor: ET.Element) -> bool:
    return any(_ is node for _ in ancestor.iter())


def _ancestors(root: ET.Element, node: ET.Element):
    """node 的祖先链（ElementTree 无父指针，按身份查找）。"""
    child = node
    while True:
        parent = next((p for p in root.iter() if any(c is child for c in p)), None)
        if parent is None:
            return
        yield parent
        child = parent


def _retint(el: ET.Element, color: str) -> None:
    """把手柄子树的描边/填充改为模型色（图例与系列同源的第一步）。"""
    for node in (el, *el.iter()):
        if node.attrib.get("stroke") not in (None, "none", ""):
            node.attrib["stroke"] = color
        if node.attrib.get("fill") not in (None, "none", ""):
            node.attrib["fill"] = color
        style = node.attrib.get("style", "")
        if style:
            style = re.sub(r"stroke:\s*[^;]+", f"stroke: {color}", style)
            style = re.sub(r"fill:\s*[^;]+", f"fill: {color}", style)
            node.attrib["style"] = style


def _find_by_id(root: ET.Element, node_id: str) -> ET.Element | None:
    return next((el for el in root.iter() if el.attrib.get("id") == node_id), None)


def _referenced_ids(el: ET.Element) -> set[str]:
    refs: set[str] = set()
    for e in el.iter():
        refs |= _refs_of(e)
    return refs


def _refs_of(e: ET.Element) -> set[str]:
    refs: set[str] = set()
    for k, v in e.attrib.items():
        if k.endswith("href") and v.startswith("#"):
            refs.add(v[1:])
        else:
            m = re.search(r"url\(#([^)]+)\)", v)
            if m:
                refs.add(m.group(1))
    return refs


def _refs_outside(root: ET.Element, target: ET.Element) -> set[str]:
    """root 里 target 子树**之外**的全部引用——同一 def 被内外同时引用时也算外。"""
    refs: set[str] = set()
    for e in root.iter():
        if e is target or _is_descendant(e, target):
            continue
        refs |= _refs_of(e)
    return refs


def compose_scene(
    scene_bytes: bytes, binding: dict, fragment_bytes: bytes, model: dict | None = None
) -> bytes:
    """按绑定把片段接进场景；返回编辑后的场景字节（原字节不动）。

    ``model``（可选）：图例手柄同步需要模型里的系列样式；缺省时手柄按原样保留。
    """
    from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype

    scene = parse_svg(strip_matplotlib_doctype(scene_bytes))
    frag_root = parse_svg(strip_matplotlib_doctype(fragment_bytes))
    edited = copy.deepcopy(scene)
    frag_axes = next(
        (el for el in frag_root.iter() if el.attrib.get("id", "").startswith("axes_")), None
    )
    if frag_axes is None:
        raise ComposeError("compose_fragment_invalid", "fragment has no axes subtree")

    series_bindings = [e for e in binding["entries"] if e["object"].startswith("series:")]
    axes_nodes = [e["node"] for e in binding["entries"] if e["object"].startswith("axes:")]

    parent_map = {id(c): p for p in edited.iter() for c in p}
    root_defs = edited.find("s:defs", NS)
    if root_defs is None:
        root_defs = ET.SubElement(edited, f"{{{SVG_NS}}}defs")

    def hoist_external_defs(target: ET.Element) -> None:
        """被「区域外」引用的 defs 上提到根 defs（图例引用系列组 marker 等）。"""
        inner_defs = list(target.iter(f"{{{SVG_NS}}}defs"))
        def_ids = {c.attrib["id"] for d in inner_defs for c in d if c.attrib.get("id")}
        outside = _refs_outside(edited, target)
        for d in inner_defs:
            for child in list(d):
                if child.attrib.get("id") in def_ids & outside:
                    root_defs.append(copy.deepcopy(child))

    # 1) 轴区域节点：先上提被外部引用的 defs，再整体移除
    for node_id in axes_nodes:
        target = _find_by_id(edited, node_id)
        if target is None:
            raise ComposeError("compose_node_missing", f"bound node {node_id!r} not in scene")
        hoist_external_defs(target)
        par = parent_map[id(target)]
        par.remove(target)

    # 2) 系列节点：以片段里对应的系列组替换（matplotlib line2d_N 按绘制序对应）
    frag_series = [
        g
        for g in frag_axes.iter(f"{{{SVG_NS}}}g")
        if g.attrib.get("id", "").startswith("line2d_") and g.find("s:path", NS) is not None
    ]
    suffix = "__c1"
    series_insert_parent = None
    series_insert_index = None
    model_series = {f"series:{sr['id']}": sr for sr in (model or {}).get("series", [])}
    frag_slot = 0
    for entry in series_bindings:
        target = _find_by_id(edited, entry["node"])
        if target is None:
            raise ComposeError("compose_node_missing", f"bound node {entry['node']!r} not in scene")
        series_def = model_series.get(entry["object"]) or {}
        if entry["node"] != series_def.get("node"):
            # 非数据节点（图例手柄等）：不替换，按模型样式同步描边/填充
            ancestor_ids = {p.attrib.get("id", "") for p in _ancestors(edited, target)}
            if not any(a.startswith("legend_") for a in ancestor_ids):
                raise ComposeError(
                    "compose_node_role_unknown",
                    f"node {entry['node']!r} is neither the series data node nor a legend handle",
                )
            style = series_def.get("style") or {}
            color = style.get("color") or style.get("facecolor")
            if color:
                _retint(target, str(color))
            continue
        hoist_external_defs(target)
        if frag_slot >= len(frag_series):
            raise ComposeError(
                "compose_fragment_invalid",
                f"fragment has {len(frag_series)} series groups, binding needs more",
            )
        frag = copy.deepcopy(frag_series[frag_slot])
        frag_slot += 1
        _suffix_ids(frag, suffix)
        par = parent_map[id(target)]
        idx = list(par).index(target)
        par[idx] = frag
        if series_insert_parent is None:
            series_insert_parent, series_insert_index = par, idx

    # 3) 片段的 axis 子树（刻度/标注/网格）接进场景：插到第一个替换组之前（同一父层）
    #    —— 普通 SVG 没有 matplotlib.axis_* 组；用户绑定轴区域节点后由片段接管。
    if axes_nodes and series_insert_parent is not None:
        frag_axis_subtrees = [
            g for g in frag_axes if g.attrib.get("id", "").startswith("matplotlib.axis_")
        ]
        for j, subtree in enumerate(frag_axis_subtrees):
            frag_sub = copy.deepcopy(subtree)
            _suffix_ids(frag_sub, suffix)
            series_insert_parent.insert(series_insert_index + j, frag_sub)

    # 4) 片段引用的根级 defs 随迁（clipPath 等）
    for sc_defs in frag_root.findall("s:defs", NS):
        for child in sc_defs:
            cc = copy.deepcopy(child)
            _suffix_ids(cc, suffix)
            root_defs.append(cc)

    body = ET.tostring(edited, encoding="unicode")
    header = '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n'
    return (header + body).encode("utf-8")
