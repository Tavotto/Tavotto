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

# 序列化的前缀注册（缺省时 ElementTree 会写成 ns0:/ns1:，浏览器能吃但难看且
# 与原场景字节形态漂移）。matplotlib 场景还会带 dc/rdf/creativecommons/xlink。
for _prefix, _uri in (
    ("", SVG_NS),
    ("xlink", "http://www.w3.org/1999/xlink"),
    ("dc", "http://purl.org/dc/elements/1.1/"),
    ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
    ("cc", "http://creativecommons.org/ns#"),
):
    ET.register_namespace(_prefix, _uri)


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


def _parent_of(root: ET.Element, node: ET.Element) -> ET.Element:
    return next(p for p in root.iter() if any(c is node for c in p))


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
    scene_bytes: bytes,
    binding: dict,
    fragment_bytes: bytes,
    model: dict | None = None,
    origin: tuple[float, float] = (0.0, 0.0),
) -> bytes:
    """按绑定把片段接进场景；返回编辑后的场景字节（原字节不动）。

    合同（R01/R02）：
    - 片段里每个语义对象带 ``tv-*`` 角色标记（脚手架经 matplotlib gid 写成
      SVG id）：``tv-series-<id>[-N]`` / ``tv-axes-<id>`` / ``tv-axesbg-<id>`` /
      ``tv-colorbar-<id>``。组合**按角色**取完整产物，不按 line2d_ 前缀或
      遍历序号猜。
    - 绑定引用经 `svgscene.build_node_index` 解析（与 inventory 同一 id 规则），
      无 XML id 的节点可稳定选中（B5）。
    - 系列数据节点 → 角色成员的包装组；``cbar_node`` → 色条子树整体接管（B4）；
      轴区域节点（axes_nodes）退役后接入片段轴家具（刻度/标注/网格，B1）。
    - 被外部引用的 defs 先上提到根 defs；片段根级 defs 随迁并加后缀隔离。
    """
    from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype
    from tavotto.svgcoord import parse_transform
    from tavotto.svgscene import build_node_index

    if isinstance(scene_bytes, str):
        scene_bytes = scene_bytes.encode("utf-8")
    if isinstance(fragment_bytes, str):
        fragment_bytes = fragment_bytes.encode("utf-8")
    scene = parse_svg(strip_matplotlib_doctype(scene_bytes))
    frag_root = parse_svg(strip_matplotlib_doctype(fragment_bytes))
    edited = copy.deepcopy(scene)

    model_series = {f"series:{sr['id']}": sr for sr in (model or {}).get("series", [])}
    axes_id = ((model or {}).get("axes") or [{}])[0].get("id", "")
    tv_axes = next((el for el in frag_root.iter() if el.get("id") == f"tv-axes-{axes_id}"), None)
    if tv_axes is None:
        raise ComposeError("compose_fragment_invalid", "fragment has no role-marked axes")

    # 片段角色成员索引——在整个片段上做：colorbar axes 是主轴的**兄弟**
    # （fig.colorbar 生成），不在 tv-axes 子树内。
    frag_series: dict[str, list[ET.Element]] = {}
    frag_colorbar: dict[str, ET.Element] = {}
    for el in frag_root.iter():
        eid = el.get("id") or ""
        if eid.startswith("tv-series-"):
            sid = eid[len("tv-series-") :].split("-")[0]
            frag_series.setdefault(sid, []).append(el)
        elif eid.startswith("tv-colorbar-"):
            frag_colorbar.setdefault(eid[len("tv-colorbar-") :], el)

    def _is_role_subtree(el: ET.Element) -> bool:
        """子树内含任何 tv-series/tv-colorbar 成员（无 id 的裁剪组也算）。"""
        return any(
            (e.get("id") or "").startswith(("tv-series-", "tv-colorbar-")) for e in el.iter()
        )

    axis_furniture = [
        child
        for child in list(tv_axes)
        if not (child.get("id") or "").startswith(("tv-series-", "tv-colorbar-", "tv-axesbg-"))
        and not _is_role_subtree(child)
    ]

    node_index = build_node_index(edited)
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

    def resolve_target(node_id: str) -> tuple[ET.Element, list, int]:
        target = node_index.get(node_id)
        if target is None:
            raise ComposeError("compose_node_missing", f"bound node {node_id!r} not in scene")
        par = _parent_of(edited, target)
        return target, par, list(par).index(target)

    def parent_space_transform(target: ET.Element) -> str | None:
        """片段全局坐标 → 被替换节点**父空间**的变换（N2）。

        片段成员坐标 = 场景用户空间（origin 平移后）。插入点在 target 的父节点，
        该处的祖先变换 A 会再作用一次；wrapper 需带 T = A⁻¹ ∘ Translate(origin)，
        使 A · T · C = origin + C（与未受父变换时一致）。
        """
        from tavotto.svgcoord import compose as sc_compose, invert as sc_invert

        chain: list[ET.Element] = []
        node: ET.Element | None = target
        while node is not None and node is not edited:
            chain.append(node)
            node = _parent_of(edited, node)
        if chain and chain[-1] is not edited:
            chain.append(edited)
        m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        for el in reversed(chain[:-1]):  # 根 → 父（不含 target 自身）
            tr = el.attrib.get("transform")
            if tr:
                m = sc_compose(m, parse_transform(tr))
        # 行向量序：世界 = C · T · A（父变换在文档结构里自动再作用一次）。
        # 要 world = C · O（O = translate(origin)）⇒ T = O · A⁻¹。
        o = (1.0, 0.0, 0.0, 1.0, origin[0], origin[1])
        try:
            t = sc_compose(o, sc_invert(m))
        except ValueError:
            return None
        if t == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0):
            return None
        from tavotto.svgcoord import matrix_attr

        return matrix_attr(t)

    suffix = "__c1"
    series_bindings = [e for e in binding["entries"] if e["object"].startswith("series:")]
    axes_nodes = [e["node"] for e in binding["entries"] if e["object"].startswith("axes:")]

    series_wrappers: set[ET.Element] = set()
    consumed_axes_nodes: set[str] = set()
    for entry in series_bindings:
        series_def = model_series.get(entry["object"]) or {}
        sid = entry["object"].split(":", 1)[1]
        target, par, idx = resolve_target(entry["node"])
        if entry["node"] != series_def.get("node"):
            # 非数据节点：色条区域接管，或图例手柄同步
            if series_def.get("kind") == "image" and entry["node"] == series_def.get("cbar_node"):
                cbar = frag_colorbar.get(sid)
                if cbar is None:
                    raise ComposeError(
                        "compose_fragment_invalid", "fragment has no colorbar for the image"
                    )
                frag_sub = copy.deepcopy(cbar)
                _suffix_ids(frag_sub, suffix)
                par[idx] = frag_sub
                consumed_axes_nodes.add(entry["node"])
                continue
            ancestor_ids = {a.get("id", "") for a in _ancestors(edited, target)}
            if not any(a.startswith("legend_") for a in ancestor_ids):
                raise ComposeError(
                    "compose_node_role_unknown",
                    f"node {entry['node']!r} is neither the data node, the colorbar region "
                    "nor a legend handle",
                )
            style = series_def.get("style") or {}
            color = style.get("color") or style.get("facecolor")
            if color:
                _retint(target, str(color))
            continue
        members = frag_series.get(sid)
        if not members:
            raise ComposeError(
                "compose_fragment_invalid", f"fragment has no members for series {sid!r}"
            )
        hoist_external_defs(target)
        wrapper = ET.Element(f"{{{SVG_NS}}}g", {"data-tavotto": f"series:{sid}"})
        # 片段坐标相对 viewBox 原点且插在父空间：T = A⁻¹ ∘ Translate(origin)（N1/N2）
        wrapper_t = parent_space_transform(target)
        if wrapper_t:
            wrapper.set("transform", wrapper_t)
        for member in members:
            piece = copy.deepcopy(member)
            _suffix_ids(piece, suffix)
            wrapper.append(piece)
        par[idx] = wrapper
        series_wrappers.add(wrapper)

    # 轴区域节点：色条区域由片段新色条整体接管，其余退役；片段轴家具插到第一个系列位
    cbar_sid_by_node = {
        sr["cbar_node"]: sr["id"] for sr in (model or {}).get("series", []) if sr.get("cbar_node")
    }
    retired_axes = False
    for node_id in axes_nodes:
        if node_id in consumed_axes_nodes:
            continue
        target, par, idx = resolve_target(node_id)
        sid = cbar_sid_by_node.get(node_id)
        if sid is not None:
            cbar = frag_colorbar.get(sid)
            if cbar is None:
                raise ComposeError(
                    "compose_fragment_invalid", "fragment has no colorbar for the image"
                )
            hoist_external_defs(target)
            frag_sub = copy.deepcopy(cbar)
            _suffix_ids(frag_sub, suffix)
            cbar_t = parent_space_transform(target)
            if cbar_t:
                wrapper = ET.Element(f"{{{SVG_NS}}}g", {"transform": cbar_t})
                wrapper.append(frag_sub)
                frag_sub = wrapper
            par[idx] = frag_sub
            continue
        hoist_external_defs(target)
        par.remove(target)
        retired_axes = True
    if retired_axes:
        # 删除旧轴会改变所有后续序号；以最终树里最早绘制的系列对象为锚点，
        # 而不是复用删除前的下标或依赖绑定列表的排列。
        anchor = next((el for el in edited.iter() if el in series_wrappers), None)
        if anchor is None:
            raise ComposeError("compose_fragment_invalid", "axis regions contain the series")
        insert_parent = _parent_of(edited, anchor)
        insert_index = list(insert_parent).index(anchor)
        furniture_group = ET.Element(f"{{{SVG_NS}}}g", {"data-tavotto": f"axes:{axes_id}"})
        furniture_t = parent_space_transform(anchor)
        if furniture_t:
            furniture_group.set("transform", furniture_t)
        for furniture in axis_furniture:
            piece = copy.deepcopy(furniture)
            _suffix_ids(piece, suffix)
            furniture_group.append(piece)
        insert_parent.insert(insert_index, furniture_group)

    # 片段根级 defs 随迁（clipPath 等，含系列/轴家具的引用目标）
    for sc_defs in frag_root.findall("s:defs", NS):
        for child in sc_defs:
            cc = copy.deepcopy(child)
            _suffix_ids(cc, suffix)
            root_defs.append(cc)

    body = ET.tostring(edited, encoding="unicode")
    header = '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n'
    return (header + body).encode("utf-8")
