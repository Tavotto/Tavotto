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
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"s": SVG_NS}
_NATIVE_VIEWPORT_PT_ATTR = "data-tavotto-native-viewport-pt"
_NATIVE_TARGETS_ATTR = "data-tavotto-svg-targets"

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


def _hoist_external_defs(root, target, root_defs):
    inner_defs = list(target.iter(f"{{{SVG_NS}}}defs"))
    def_ids = {c.attrib["id"] for d in inner_defs for c in d if c.attrib.get("id")}
    outside = _refs_outside(root, target)
    for defs in inner_defs:
        for child in defs:
            if child.attrib.get("id") in def_ids & outside:
                root_defs.append(copy.deepcopy(child))


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
        _hoist_external_defs(edited, target, root_defs)

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


def _intersection(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    return [
        x,
        y,
        max(0.0, min(a[0] + a[2], b[0] + b[2]) - x),
        max(0.0, min(a[1] + a[3], b[1] + b[3]) - y),
    ]


def _native_context(root, parent, resolver, size, viewport_clip=None):
    """只消费现有 SVGIR 样式/clip 与 svgcoord CTM，不重测原生文字或路径。"""
    from tavotto.rendercore import ir, svgir
    from tavotto.svgcoord import apply, compose, node_matrix

    chain = [parent, *_ancestors(root, parent)]
    opacity, clip, limits = (
        1.0,
        list(viewport_clip) if viewport_clip is not None else [0.0, 0.0, 1.0, 1.0],
        [],
    )
    style = svgir._DEFAULT
    for owner in reversed(chain):
        style = resolver._style(owner, style)
        opacity *= min(1.0, max(0.0, svgir._length(style.get("opacity", "1"), percent=1)))
        if style.get("display") == "none":
            limits.append("native_context_hidden")
        for name in ("mask", "filter"):
            if style.get(name, "none") != "none":
                limits.append("native_context_" + name)
        value = style.get("clip-path", "none")
        if value == "none":
            continue
        try:
            path = resolver._clip(value)
        except svgir.SVGIRError:
            limits.append("native_clip_unrepresented")
            continue
        if not isinstance(path, ir.Path):
            limits.append("native_clip_union")
            continue
        matrix = compose(node_matrix(root, owner.get("id")), resolver.root_map)
        vertices = [
            tuple(round(v, 9) for v in apply(matrix, *seg[1:]))
            for seg in path.segments
            if seg[0] in ("M", "L")
        ]
        x = sorted({round(p[0], 9) for p in vertices})
        y = sorted({round(p[1], 9) for p in vertices})
        if (
            len(vertices) != 4
            or len(x) != 2
            or len(y) != 2
            or any(seg[0] not in ("M", "L", "Z") for seg in path.segments)
            or set(vertices) != {(px, py) for px in x for py in y}
        ):
            limits.append("native_clip_nonrectangular")
            continue
        clip = _intersection(
            clip, [x[0] / size[0], y[0] / size[1], (x[1] - x[0]) / size[0], (y[1] - y[0]) / size[1]]
        )
    if opacity == 0 or style.get("visibility") in ("hidden", "collapse"):
        limits.append("native_context_hidden")
    return opacity, clip, sorted(set(limits))


def _native_manifest_context(
    manifest, clip, opacity, ranks, roles, *, hidden_axes=False, paint_members=None
):
    """原生分数已是最终物理分数，只补实际保留的祖先绘制上下文。"""
    out = copy.deepcopy(manifest)
    for element in out["elements"]:
        gid = element["gid"]
        if hidden_axes:
            # A hidden native axes does not draw its children, even when their
            # own visible properties remain True. Keep those setter values;
            # there is simply no painted geometry to hit in this snapshot.
            element["geometry"] = {
                "kind": "multi_path",
                "paths": [],
                "fill": False,
                "stroke": False,
            }
        geom = element.get("geometry")
        if geom is None and clip != [0.0, 0.0, 1.0, 1.0]:
            # 原生 bbox 就是文字/图例的既有命中权威，不能另猜 DOM 文字几何；
            # 保留原 bbox 用于布局，仅给它同等的矩形命中形状以表达祖先裁剪。
            x, y, w, h = element["bbox"]
            geom = {
                "kind": "path",
                "fill": True,
                "stroke": False,
                "paths": [
                    {"points": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], "closed": True}
                ],
            }
            element["geometry"] = geom
        if geom is not None:
            geom["clip"] = _intersection(geom.get("clip", [0.0, 0.0, 1.0, 1.0]), clip)
            if "fill_alpha" in geom:
                geom["fill_alpha"] *= opacity
        element["clip_bbox"] = _intersection(element.get("clip_bbox", [0.0, 0.0, 1.0, 1.0]), clip)
        ids = [gid] if gid in ranks else roles.get(gid, [])
        ordered = [ranks[value] for value in ids if value in ranks]
        if ordered and paint_members is not None:
            mine = set().union(*(paint_members.get(value, set()) for value in ids))
            # Like pathgeom.paint_order_of: a pseudo artist spanning another
            # object's paint slot cannot truthfully have one occlusion rank.
            if mine and len(mine) != max(mine) - min(mine) + 1:
                ordered = []
        if ordered:
            target = geom if geom is not None else element
            target["paint_order"] = max(ordered)
        else:
            element.pop("paint_order", None)
            if geom is not None:
                geom.pop("paint_order", None)
    return out


def _native_dom_facts(scene, fragment, scaffold):
    """Actual final DOM targets and paint facts; never a second geometry ruler.

    Native axes may now own pieces in several source paint slots. The original
    native tree tells which pieces belong to that axes, and the final tree tells
    which of those pieces remain ancestors of others. Their antichain is the
    exact set a temporary axes transform may touch without moving decorations
    or applying the transform twice.
    """
    final_ids = {node.get("id"): node for node in scene.iter() if node.get("id")}
    parents = {child: parent for parent in scene.iter() for child in parent}
    order = {node: i for i, node in enumerate(scene.iter())}
    original = {node.get("id"): node for node in fragment.iter() if node.get("id")}
    axes_ids = {
        info["primary_gid"] for role, info in scaffold["roles"].items() if role.startswith("axes:")
    }

    def antichain(values):
        selected = {final_ids[value] for value in values if value in final_ids}
        result = []
        for node in sorted(selected, key=order.get):
            ancestor = parents.get(node)
            while ancestor is not None and ancestor not in selected:
                ancestor = parents.get(ancestor)
            if ancestor is None:
                result.append(node.get("id"))
        return result

    targets = {}
    members = scaffold.get("svg_members", {})
    for element in scaffold["manifest"]["elements"]:
        gid = element["gid"]
        candidates = members.get(gid, [])
        if element["role"] == "colorbar" and element.get("geom_gid") in final_ids:
            # Existing native geometry host: its setters act on cb.ax, even
            # when hidden and Matplotlib omitted all individual solids.
            candidates = [element["geom_gid"]]
        if gid in axes_ids and gid in original:
            candidates = [node.get("id") for node in original[gid].iter() if node.get("id")]
        elif gid in final_ids:
            candidates = [gid]
        actual = antichain(candidates)
        targets[gid] = {"transform": actual, "style": list(actual)}

    ranks, painted, counter = {}, {}, [0]

    def rank(node):
        if node.tag in {f"{{{SVG_NS}}}defs", f"{{{SVG_NS}}}metadata", f"{{{SVG_NS}}}style"}:
            return set()
        mine = set()
        if node.tag.split("}")[-1] in {
            "path",
            "rect",
            "circle",
            "ellipse",
            "line",
            "polygon",
            "polyline",
            "text",
            "use",
            "image",
        }:
            mine.add(counter[0])
            counter[0] += 1
        for child in node:
            mine.update(rank(child))
        if node.get("id") in original and mine:
            ranks[node.get("id")] = max(mine)
            painted[node.get("id")] = mine
        return mine

    rank(scene)
    return targets, ranks, painted


def _group_native_members(fragment, roles):
    """仅为同父、连续的真实系列成员建立其原生容器组，不吞进别的 artist。"""
    limits = []
    parents = {child: parent for parent in fragment.iter() for child in parent}
    for role, info in roles.items():
        if not role.startswith("series:") or _find_by_id(fragment, info["primary_gid"]) is not None:
            continue
        members = [_find_by_id(fragment, value) for value in info["svg_ids"]]
        members = [member for member in members if member is not None]
        owners = {parents.get(member) for member in members}
        if not members or len(owners) != 1 or None in owners:
            limits.append("native_dom_series_members_split")
            continue
        parent = next(iter(owners))
        positions = [list(parent).index(member) for member in members]
        span = list(parent)[min(positions) : max(positions) + 1]
        if set(span) != set(members):
            limits.append("native_dom_series_members_interleaved")
            continue
        group = ET.Element(f"{{{SVG_NS}}}g", {"id": info["primary_gid"]})
        for member in span:
            parent.remove(member)
            group.append(member)
        parent.insert(min(positions), group)
    return limits


def _native_series_sequence(fragment, roles, extra_roles=()):
    """Separately placed roles in native draw order, retaining interleave.

    Main axes furniture is a hierarchical owner rather than a separate data
    slot. Auxiliary axes, such as a bound colorbar, do have their own slot.
    """
    owners = {
        value: role
        for role, info in roles.items()
        if role.startswith("series:") or role in extra_roles
        for value in [info["primary_gid"], *info["svg_ids"]]
    }
    sequence = []

    def visit(node, owner=None):
        if node.tag in {f"{{{SVG_NS}}}defs", f"{{{SVG_NS}}}metadata", f"{{{SVG_NS}}}style"}:
            return
        owner = owners.get(node.get("id"), owner)
        if owner and node.tag.split("}")[-1] in {
            "path",
            "rect",
            "circle",
            "ellipse",
            "line",
            "polygon",
            "polyline",
            "text",
            "use",
            "image",
        }:
            if not sequence or sequence[-1] != owner:
                sequence.append(owner)
        for child in node:
            visit(child, owner)

    visit(fragment)
    return sequence


def _hidden_native_containers(fragment, roles, manifest, main_role):
    """明确隐藏的 artist 不输出 SVG 是合法事实，不是丢失一个可画系列。"""
    elements = {el["gid"]: el for el in manifest["elements"]}

    def hidden(gid):
        return any(
            field["prop"] == "visible" and field.get("value") is False
            for field in elements.get(gid, {}).get("editable", [])
        )

    main_gid = roles[main_role]["primary_gid"]
    hidden_axes = hidden(main_gid)
    for role, info in roles.items():
        gid = info["primary_gid"]
        if role.startswith("axes:") and hidden(gid) and _find_by_id(fragment, gid) is None:
            ET.SubElement(fragment, f"{{{SVG_NS}}}g", {"id": gid, "display": "none"})
    axes = _find_by_id(fragment, main_gid)
    if axes is not None:
        for role, info in roles.items():
            gid = info["primary_gid"]
            if (
                role.startswith("series:")
                and (hidden_axes or hidden(gid))
                and _find_by_id(fragment, gid) is None
            ):
                ET.SubElement(axes, f"{{{SVG_NS}}}g", {"id": gid, "display": "none"})
    return hidden_axes


def compose_native_scene(source_svg: bytes, binding: dict, scaffold: dict, model: dict) -> dict:
    """物理 pt scaffold → 场景与配对 manifest；旧 compose_scene 不经过此分支。

    原 scene index 在隔离之前持有 Element 引用，原 source/绑定身份始终不变。
    原生 DOM gids 留给现有预览消费者；只有原场景派生副本的 ids/CSS 改名。
    跨父与插绘通过实际多目标映射保留原位置；非矩形 clip 等当前 manifest
    无法表达的上下文仍给 composition.composed=False，不伪造命中资格。
    """
    from tavotto.engine import chartmodel
    from tavotto.rendercore import svgir
    from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype
    from tavotto.svgcoord import compose, invert, matrix_attr, node_matrix, viewbox_to_pt
    from tavotto.svgisolation import isolate_svg
    from tavotto.svgscene import build_node_index, scene_inventory

    chartmodel.validate_model(model)
    chartmodel.validate_binding(binding, model)
    inventory = scene_inventory(source_svg)
    scene = parse_svg(strip_matplotlib_doctype(source_svg))
    source_index = build_node_index(scene)
    chartmodel.verify_scene(binding, inventory["scene_digest"], set(source_index))
    coords = scaffold.get("coordinates", {})
    size = [inventory["w_pt"], inventory["h_pt"]]
    vb = inventory["view_box"] or [0.0, 0.0, size[0] * 96 / 72, size[1] * 96 / 72]
    mapping = viewbox_to_pt(vb, *size, scene.get("preserveAspectRatio"))
    if (
        coords.get("kind") != "scene-viewport-figure-fraction"
        or coords.get("composed") is not False
        or len(coords.get("scene_size_pt", [])) != 2
        or len(coords.get("fragment_to_scene_user", [])) != 6
        or any(not math.isclose(a, b, abs_tol=1e-7) for a, b in zip(coords["scene_size_pt"], size))
        or any(
            not math.isclose(a, b, abs_tol=1e-7)
            for a, b in zip(coords["fragment_to_scene_user"], invert(mapping))
        )
    ):
        raise ComposeError("compose_fragment_invalid", "native physical coordinate contract")
    fragment = parse_svg(strip_matplotlib_doctype(scaffold["fragment"]))
    roles = scaffold.get("roles", {})
    main_role = f"axes:{model['axes'][0]['id']}"
    if main_role not in roles or not all(r.get("primary_gid") for r in roles.values()):
        raise ComposeError("compose_fragment_invalid", "native ownership roles")
    hidden_axes = _hidden_native_containers(fragment, roles, scaffold["manifest"], main_role)
    # A real contiguous container remains a useful fast path. Its absence is
    # not a limitation: pseudo artists are represented by the final target map.
    _group_native_members(fragment, roles)
    fragment_ids = {e.get("id") for e in fragment.iter() if e.get("id")}
    parents = {child: parent for parent in scene.iter() for child in parent}
    colorbar_roles = {
        series["cbar_node"]: f"axes:colorbar_{series['id']}"
        for series in model["series"]
        if series.get("cbar_node")
    }
    # Binding axes may consist of several old furniture regions, including a
    # model's explicit cbar_node. Resolve that existing model fact before any
    # DOM mutation, rather than mistaking the colorbar slot for main furniture.
    targets = [
        (
            {**entry, "object": colorbar_roles.get(entry["node"], entry["object"])},
            source_index[entry["node"]],
        )
        for entry in binding["entries"]
    ]
    scene_order = {node: i for i, node in enumerate(scene.iter())}
    targets.sort(key=lambda item: scene_order[item[1]])
    if any(a is not b and _is_descendant(a, b) for _, a in targets for _, b in targets):
        raise ComposeError("compose_fragment_invalid", "overlapping bound regions")
    if any(node not in parents for _, node in targets):
        raise ComposeError("compose_fragment_invalid", "root is not a replaceable region")
    # Anonymous parents also need a stable temporary CTM handle. No original
    # binding key is recomputed after this point.
    source_ids = {e.get("id") for e in scene.iter() if e.get("id")}
    for index, node in enumerate(scene.iter()):
        if node.get("id") is None:
            value = f"tvctx_{index}"
            while value in source_ids or value in fragment_ids:
                value += "_"
            node.set("id", value)
            source_ids.add(value)
    resolver = svgir._Translator(scene, *size, None, None)
    render_frame = scaffold.get("render_frame")
    source_nodes = None
    viewport_clip = None
    if render_frame is not None:
        from tavotto import chartframecompose
        from tavotto.engine import chartframe

        render_frame = chartframe.validate_frame(render_frame)
        left, top, width, height = render_frame["rect_pt"]
        viewport_clip = [left / size[0], top / size[1], width / size[0], height / size[1]]
        source_nodes = chartframecompose.freeze_source_presentation(scene, resolver)
    contexts = {}
    placements = []
    limitations = []
    for entry, target in targets:
        parent = parents[target]
        if parent not in contexts:
            contexts[parent] = _native_context(
                scene,
                parent,
                resolver,
                size,
                **({"viewport_clip": viewport_clip} if render_frame is not None else {}),
            )
        alpha, clip, limits = contexts[parent]
        if render_frame is not None:
            clip = chartframe.source_fraction_rect(render_frame, clip)
        transform = compose(invert(mapping), invert(node_matrix(scene, parent.get("id"))))
        placements.append(
            {
                "role": entry["object"],
                "source_node": entry["node"],
                "parent_source_id": parent.get("id"),
                "fragment_to_parent": list(transform),
                "opacity": alpha,
                "clip": clip,
                "limitations": limits,
                "native_gids": roles.get(entry["object"], {}).get("gids", []),
                "svg_members": {
                    gid: scaffold.get("svg_members", {}).get(gid, [])
                    for gid in roles.get(entry["object"], {}).get("gids", [])
                },
            }
        )
        limitations.extend(limits)
    all_parents = {parents[node] for _, node in targets}
    common_parent = next(iter(all_parents)) if len(all_parents) == 1 else None
    bound = {node for _, node in targets}
    contiguous = False
    if common_parent is not None:
        positions = [i for i, child in enumerate(common_parent) if child in bound]
        contiguous = all(
            child in bound for child in list(common_parent)[min(positions) : max(positions) + 1]
        )
    new_axes = not any(entry["object"] == main_role for entry, _ in targets)
    bound_series = {
        entry["object"] for entry, _ in targets if entry["object"].startswith("series:")
    }
    if bound_series != {f"series:{s['id']}" for s in model["series"]}:
        limitations.append("native_series_region_unbound")
    auxiliary_roles = []
    bound_nodes = {entry["node"] for entry, _ in targets}
    for series in model["series"]:
        auxiliary_role = f"axes:colorbar_{series['id']}"
        if auxiliary_role in roles:
            if series.get("cbar_node") not in bound_nodes:
                limitations.append("native_colorbar_region_unbound")
            else:
                auxiliary_roles.append(auxiliary_role)

    digest = hashlib.sha256(source_svg).hexdigest()[:12]
    class_names = {
        c
        for root in (scene, fragment)
        for node in root.iter()
        for c in node.get("class", "").split()
    }
    serial = 0
    while True:
        prefix = f"tvsrc_{digest}_{serial}_"
        source_class, native_class = prefix + "source", prefix + "native"
        if not ({prefix + value for value in source_ids} & fragment_ids) and not (
            {source_class, native_class} & class_names
        ):
            break
        serial += 1
    # Only generated wrappers may declare native preview coordinates. Strip
    # imported lookalikes from this derived DOM without altering source bytes.
    for node in scene.iter():
        node.attrib.pop(_NATIVE_VIEWPORT_PT_ATTR, None)
        node.attrib.pop(_NATIVE_TARGETS_ATTR, None)
        if render_frame is not None:
            node.attrib.pop(chartframe.MARKER, None)
    source_id_map = isolate_svg(scene, prefix=prefix, owned_class=source_class)
    isolate_svg(fragment, prefix="", owned_class=native_class)
    root_defs = scene.find("s:defs", NS)
    if root_defs is None:
        root_defs = ET.SubElement(scene, f"{{{SVG_NS}}}defs")
    for defs in fragment.findall("s:defs", NS):
        for child in defs:
            root_defs.append(copy.deepcopy(child))
    for _, target in targets:
        _hoist_external_defs(scene, target, root_defs)
    native_axes = _find_by_id(fragment, roles[main_role]["primary_gid"])
    if native_axes is None:
        raise ComposeError("compose_fragment_invalid", "native axes DOM missing")
    # Native roots have their own inherited presentation defaults. Preserve the
    # actual source ancestor opacity/clip, without inheriting unrelated stroke
    # widths/fill rules into omitted default-valued matplotlib properties.
    reset = ";".join(
        f"{key}:initial"
        for key in sorted(svgir._INHERITED)
        if key not in (svgir.XML_SPACE, "visibility")
    )

    def payload(nodes, transform):
        wrapper = ET.Element(
            f"{{{SVG_NS}}}g",
            {
                "transform": matrix_attr(transform),
                "style": reset,
                _NATIVE_VIEWPORT_PT_ATTR: " ".join(str(value) for value in size),
            },
        )
        for node in nodes:
            wrapper.append(copy.deepcopy(node))
        return wrapper

    native_sequence = _native_series_sequence(fragment, roles, auxiliary_roles)
    native_interleaved = len(native_sequence) != len(set(native_sequence))
    source_sequence = [
        entry["object"] for entry, _ in targets if entry["object"] in set(native_sequence)
    ]
    shared = (
        common_parent is not None
        and contiguous
        and (native_interleaved or source_sequence == native_sequence)
    )
    if native_interleaved and not shared:
        limitations.append("native_member_slots_underdetermined")
    if not shared and scaffold.get("paint_order_overrides"):
        # The native setter was applied, but source slots may constrain a
        # different final order. Never claim the edit worked by ignoring that
        # real conflict between two explicit placement authorities.
        if native_interleaved or source_sequence != native_sequence:
            limitations.append("native_zorder_source_slots_conflict")
    if shared:
        first_index = min(list(common_parent).index(node) for node in bound)
        axes = copy.deepcopy(native_axes)
        transform = placements[0]["fragment_to_parent"]
        roots = [axes]
        for role, info in roles.items():
            if role in auxiliary_roles:
                node = _find_by_id(fragment, info["primary_gid"])
                if node is not None:
                    roots.append(node)
        for node in bound:
            common_parent.remove(node)
        common_parent.insert(first_index, payload(roots, transform))
    else:
        inserted_roles = set()

        def role_nodes(role):
            values = set(roles.get(role, {}).get("svg_ids", []))
            if role in roles:
                values.add(roles[role]["primary_gid"])
            # Read the actual SVG draw sequence, not the SeriesGroup's member
            # registration order (errorbar data lines have a later zorder).
            found = [node for node in fragment.iter() if node.get("id") in values]
            return [
                node
                for node in found
                if not any(p is not node and _is_descendant(node, p) for p in found)
            ]

        def axis_furniture(role):
            root = _find_by_id(fragment, roles[role]["primary_gid"])
            if root is None:
                return None
            root = copy.deepcopy(root)
            # Remove other owned roles from this axes copy without losing the
            # axis's own labels/spines. Data retain their original source slots.
            other = {
                value
                for r, info in roles.items()
                if r != role
                for value in [info["primary_gid"], *info["svg_ids"]]
            }
            for parent in list(root.iter()):
                for child in list(parent):
                    if child.get("id") in other:
                        parent.remove(child)
            return root

        for (entry, target), placement in zip(targets, placements):
            role = entry["object"]
            nodes = []
            if role.startswith("series:"):
                if new_axes and main_role not in inserted_roles:
                    furniture = axis_furniture(main_role)
                    nodes.append(furniture)
                    inserted_roles.add(main_role)
                nodes.extend(role_nodes(role))
            elif role in roles and role not in inserted_roles:
                # Colorbar solids and outline belong to the proxy role, but
                # remain in the actual colorbar axes subtree at its source slot.
                axes = (
                    _find_by_id(fragment, roles[role]["primary_gid"])
                    if role in auxiliary_roles
                    else axis_furniture(role)
                )
                if axes is not None:
                    nodes.append(axes)
                inserted_roles.add(role)
            parent = parents[target]
            index = list(parent).index(target)
            parent.remove(target)
            if nodes:
                parent.insert(index, payload(nodes, placement["fragment_to_parent"]))

    # The final DOM is the sole authority for actual preview target membership
    # and cross-placement paint facts. No browser geometry is remeasured.
    svg_targets, ranks, painted = _native_dom_facts(scene, fragment, scaffold)
    if new_axes:
        anchor = next(p for p in placements if p["role"].startswith("series:"))
        placements.append(
            {
                **anchor,
                "role": main_role,
                "source_node": None,
                "anchor_source_node": anchor["source_node"],
                "native_gids": roles[main_role]["gids"],
                "svg_members": {
                    gid: scaffold.get("svg_members", {}).get(gid, [])
                    for gid in roles[main_role]["gids"]
                },
            }
        )
    manifest = copy.deepcopy(scaffold["manifest"])
    placement_by_role = {}
    for placement in placements:
        placement_by_role.setdefault(placement["role"], placement)
    for role, info in roles.items():
        placement = placement_by_role.get(role)
        axis_role = role
        if role.startswith("colorbar:"):
            axis_role = f"axes:colorbar_{role.split(':', 1)[1]}"
            placement = placement_by_role.get(axis_role)
        if placement is None:
            continue
        gids = set(info["gids"])
        selected = {
            "elements": [element for element in manifest["elements"] if element["gid"] in gids]
        }
        adjusted = _native_manifest_context(
            selected,
            placement["clip"],
            placement["opacity"],
            ranks,
            scaffold.get("svg_members", {}),
            hidden_axes=(hidden_axes and (role == main_role or role.startswith("series:")))
            or (
                axis_role.startswith("axes:")
                and _find_by_id(fragment, roles[axis_role]["primary_gid"]).get("display") == "none"
            ),
            paint_members=painted,
        )
        replacements = {element["gid"]: element for element in adjusted["elements"]}
        manifest["elements"] = [
            replacements.get(element["gid"], element) for element in manifest["elements"]
        ]
    manifest["svg_targets"] = svg_targets
    # An inert mirror serves mounted-SVG-only temporary preview consumers. The
    # manifest remains the only mapping authority and all geometry stays native.
    scene.set(_NATIVE_TARGETS_ATTR, json.dumps(svg_targets, sort_keys=True, separators=(",", ":")))
    warnings, unrestored = list(scaffold.get("warnings", [])), scaffold.get("unrestored", 0)
    if warnings or unrestored:
        limitations.append("native_patch_unrestored")
    limitations = sorted(set(limitations))
    if render_frame is not None:
        scene = chartframecompose.envelope(
            scene,
            source_nodes,
            render_frame,
            source_size=size,
            source_mapping=mapping,
            source_view_box=vb,
        )
    result = ET.tostring(scene, encoding="utf-8", xml_declaration=True)
    parse_svg(result)
    return {
        "svg": result,
        "manifest": manifest,
        "warnings": warnings,
        "unrestored": unrestored,
        "composition": {
            "composed": not limitations,
            "kind": "scene-viewport-figure-fraction",
            "source_sha256": inventory["scene_digest"],
            "source_id_map": source_id_map,
            "placements": placements,
            "limitations": limitations,
            "paint_order_scope": "owned-native-elements",
            "native_dom_shared": shared,
            "placement_mode": "shared-native" if shared and native_interleaved else "source-slots",
            "native_series_draw_order": [
                role for role in native_sequence if role.startswith("series:")
            ],
            "native_role_draw_order": native_sequence,
            "svg_elements": sum(1 for _ in scene.iter()),
        },
    }
