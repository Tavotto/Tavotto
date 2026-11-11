"""计算图表的 worker 适配器：同一次 SVG draw 配对原生 manifest。

调用方提供全新 Figure、语义角色到实际 artist/container 的所有权映射，以及
完整 overrides（不是增量）。原生 instrument/overrides/manifest 仍是编辑能力与
几何的唯一权威；这里仅连接它们，不解析 SVG 来反推命中几何。

返回的 manifest 只覆盖明确拥有的 scaffold 元素，坐标仍是 Figure 分数。
``coordinates.composed`` 始终为 False：原场景的祖先变换、clip、opacity 与
未拥有元素没有在此合成，不能把这个结果当最终场景的编辑资格。

本模块可由父进程安全 import；科学栈仅在 worker 调用函数时加载。
"""

from __future__ import annotations

import copy
import io
import math
from pathlib import Path


class ChartManifestError(ValueError):
    """稳定错误码供 worker/API 边界转换，details 不含用户图内文字。"""

    def __init__(self, code: str, details: str = ""):
        self.code = code
        self.details = details
        super().__init__(f"{code}: {details}" if details else code)


def _frame(frame: dict) -> tuple[list[float], list[float], list[float]]:
    from tavotto.svgcoord import viewbox_to_pt

    try:
        vb = list(frame["view_box"])
        size = [frame["w_pt"], frame["h_pt"]]
        if len(vb) != 4 or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
            for x in vb + size
        ):
            raise ValueError
        if min(vb[2], vb[3], *size) <= 0:
            raise ValueError
        preserve = frame.get("preserve")
        if preserve is not None and not isinstance(preserve, str):
            raise ValueError
        matrix = viewbox_to_pt(vb, *size, preserve)
    except (KeyError, TypeError, ValueError) as exc:
        raise ChartManifestError("chart_manifest_frame_invalid") from exc
    return vb, size, list(matrix)


def _claims(fig, ownership, container_type, artist_type):
    """只展开容器/显式成员，绝不通过 Axes.get_children 接管邻居。"""
    if not isinstance(ownership, dict) or not ownership:
        raise ChartManifestError("chart_manifest_ownership_invalid")
    claims: dict[int, str] = {}
    inputs: dict[str, list[str]] = {}
    kinds = {"figure", "axes", "series", "colorbar", "legend", "text"}

    def add(obj, role):
        if obj is None:
            return
        if isinstance(obj, (list, tuple)) and not isinstance(obj, container_type):
            for member in obj:
                add(member, role)
            return
        if not isinstance(obj, (container_type, artist_type)):
            # Colorbar 本身不是 Artist；它的代理由原生 instrument 登记。
            if type(obj).__module__ != "matplotlib.colorbar" or not hasattr(obj, "ax"):
                raise ChartManifestError("chart_manifest_ownership_invalid", "not_an_artist")
            owner_fig = obj.ax.figure
        elif isinstance(obj, container_type):
            owner_fig = None
        else:
            owner_fig = obj if obj is fig else obj.figure
        if owner_fig is not None and owner_fig is not fig:
            raise ChartManifestError("chart_manifest_ownership_invalid", "different_figure")
        previous = claims.get(id(obj))
        if previous is not None:
            if previous != role:
                raise ChartManifestError("chart_manifest_ownership_invalid", "overlapping_roles")
            return
        claims[id(obj)] = role
        getter = getattr(obj, "get_gid", None)
        old = getter() if getter is not None else None
        if isinstance(old, str) and old and old not in inputs[role]:
            inputs[role].append(old)
        if isinstance(obj, container_type):
            for member in obj:
                add(member, role)
            # BarContainer 的误差棒不在 tuple 成员里，但属于同一绘图调用。
            add(getattr(obj, "errorbar", None), role)

    for role, obj in ownership.items():
        if not isinstance(role, str) or ":" not in role:
            raise ChartManifestError("chart_manifest_ownership_invalid", "invalid_role")
        kind, name = role.split(":", 1)
        if kind not in kinds or not name or ":" in name:
            raise ChartManifestError("chart_manifest_ownership_invalid", "invalid_role")
        inputs[role] = []
        add(obj, role)
    return claims, inputs


def _owners(state, claims, runtime):
    """原生 gid → 语义角色；轴只隐式拥有刻度与轴标签，不拥有图例/数据。"""
    axes = {
        id(el["artist"]): claims[id(el["artist"])]
        for el in state.elements
        if el["role"] in ("axes", "axes3d") and id(el["artist"]) in claims
    }
    furniture = {
        id(axis.label): axes[id(el["artist"])]
        for el in state.elements
        if id(el["artist"]) in axes
        for name in ("x", "y", "z")
        if (axis := getattr(el["artist"], f"{name}axis", None)) is not None
    }
    legends = {
        el["gid"]: claims[id(el["artist"])]
        for el in state.elements
        if el["role"] == "legend" and id(el["artist"]) in claims
    }
    out = {}
    for el in state.elements:
        artist, gid = el["artist"], el["gid"]
        role = claims.get(id(artist))
        if role is None:
            role = furniture.get(id(artist))
        if role is None and isinstance(artist, runtime.overrides.SeriesGroup):
            role = claims.get(id(artist.container))
            if role is None:
                members = artist.members()
                owners = {claims.get(id(a)) for a in members}
                if members and len(owners) == 1 and None not in owners:
                    role = owners.pop()
        if role is None and isinstance(artist, runtime.overrides.ColorbarProxy):
            role = claims.get(id(artist.cb))
        if role is None and el["role"] in ("ticks", "ticklabel", "axis_label"):
            role = axes.get(id(getattr(artist, "ax", getattr(artist, "axes", None))))
        if role is None:
            role = next((r for prefix, r in legends.items() if gid.startswith(prefix + ".")), None)
        if role is not None:
            out[gid] = role
    return out


def _svg_members(state, gid_roles, runtime):
    """为原生伪元素给出真实 SVG 成员 id，不伪造可编辑 gid 的 DOM 组。"""
    registered = {id(el["artist"]): el["gid"] for el in state.elements}
    members = {role: [] for role in gid_roles.values()}

    def assign(artist, gid, role):
        artist.set_gid(gid)
        if gid not in members[role]:
            members[role].append(gid)

    for el in state.elements:
        gid, artist = el["gid"], el["artist"]
        role = gid_roles.get(gid)
        if role is None:
            continue
        if isinstance(artist, runtime.overrides.SeriesGroup):
            for index, member in enumerate(artist.members()):
                assign(member, registered.get(id(member), f"{gid}.members_{index}"), role)
        elif isinstance(artist, runtime.overrides.ColorbarProxy):
            for name in ("solids", "outline", "dividers"):
                member = getattr(artist.cb, name, None)
                if member is not None:
                    assign(member, f"{gid}.{name}", role)
            for index, member in enumerate(artist.cb.lines):
                assign(member, f"{gid}.lines_{index}", role)
        elif isinstance(artist, runtime.tickmodel.TickLabel):
            live = artist.live()
            if live is not None:
                assign(live, gid, role)
        elif el["role"] in ("axes", "axes3d"):
            # Axes 的 DOM 组仍包含别的数据。分件 id 让组合方可选择轴家具，
            # roles.svg_ids 不表示某节点的所有后代也归该角色所有。
            assign(artist, gid, role)
            assign(artist.patch, f"{gid}.background", role)
            for name in ("x", "y", "z"):
                axis = getattr(artist, f"{name}axis", None)
                if axis is not None:
                    assign(axis, f"{gid}.{name}axis", role)
            for name, spine in artist.spines.items():
                assign(spine, f"{gid}.spine_{name}", role)
        elif hasattr(artist, "get_gid"):
            actual = artist.get_gid()
            if actual and actual not in members[role]:
                members[role].append(actual)
    return members


def _filtered_manifest(manifest, gid_roles):
    """去掉未拥有条目及指向它们的编辑关系，保留原生几何原值。"""
    out = copy.deepcopy(manifest)
    out["elements"] = [el for el in out["elements"] if el["gid"] in gid_roles]
    live = {el["gid"] for el in out["elements"]}
    groups = [
        group
        for group in out.get("groups", [])
        if set(group["members"]) <= live
        and group["colorbar_gid"] in live
        and (not group.get("mappable_gid") or group["mappable_gid"] in live)
    ]
    live.update(group["gid"] for group in groups)
    singular = (
        "geom_gid",
        "host_gid",
        "parent_gid",
        "mappable_gid",
        "colorbar_gid",
        "inset_of",
        "arrow_of",
        "drag_host",
    )
    plural = ("follow_gids", "scale_gids", "cmap_group_gids", "alias_covers", "owner_gids")
    for el in out["elements"]:
        for key in singular:
            if el.get(key) and el[key] not in live:
                el.pop(key)
                if key == "geom_gid":
                    el["resizable"] = False
                elif key == "drag_host":
                    el["draggable"] = False
        for key in plural:
            if key in el:
                el[key] = [gid for gid in el[key] if gid in live]
        info = el.get("legend_entry")
        if info and info.get("source_gid") not in live:
            info.pop("source_gid", None)
            info.pop("binding_default", None)
        if not el.get("alias_covers"):
            el.pop("alias_pure", None)
    # 组节点的成立条件涉及所有宿主；只保留完整拥有的原生组，不重建部分组。
    if groups:
        out["groups"] = groups
    else:
        out.pop("groups", None)
    return out


def _gid_svg_members(state, gid_roles, svg_ids, runtime):
    """按真实 artist 身份列 DOM 成员，供组合后的预览/绘制事实消费。"""
    result = {}
    for el in state.elements:
        artist, gid = el["artist"], el["gid"]
        if gid not in gid_roles:
            continue
        if isinstance(artist, runtime.overrides.SeriesGroup):
            artists = artist.members()
        elif isinstance(artist, runtime.tickmodel.TickSet):
            artists = artist.labels
        elif isinstance(artist, runtime.tickmodel.TickLabel):
            artists = [artist.live()]
        elif isinstance(artist, runtime.overrides.ColorbarProxy):
            # The proxy's visible/placement setters own the complete colorbar
            # axes, including labels and an optionally rasterized solids image.
            # Its individual solids need not have an SVG gid after rasterizing.
            artists = [artist.cb.ax]
        else:
            artists = [artist]
        members = []
        for member in artists:
            getter = getattr(member, "get_gid", None)
            value = getter() if getter is not None else None
            if value in svg_ids and value not in members:
                members.append(value)
        result[gid] = members
    return result


def _primary_gid(state, gids, root, container_type):
    """以显式所有者为主对象，不能把 BarContainer 附带误差棒当成柱系列。"""
    if isinstance(root, (list, tuple)) and not isinstance(root, container_type):
        for member in root:
            primary = _primary_gid(state, gids, member, container_type)
            if primary is not None:
                return primary
        return None
    for el in state.elements:
        if el["gid"] not in gids:
            continue
        artist = el["artist"]
        if (
            root is artist
            or root is getattr(artist, "container", None)
            or root is getattr(artist, "cb", None)
        ):
            return el["gid"]
    return None


def _legend_source_gid(state, source) -> str | None:
    """源对象在原生登记表里的元素 gid（legend_entry.source_gid 的取值）。"""
    for el in state.elements:
        art = el["artist"]
        if art is source or getattr(art, "container", None) is source:
            return el["gid"]
    return None


def _apply_legend_follow_contract(ownership: dict, state, legend_follow: dict) -> None:
    """声明式 legend 合同的显式指认（N4）：条目 → 源对象按合同落进条目模型。

    instrument 的 ``bind_legend_entries`` 靠示意线指纹**猜**源（自建 Legend 的
    既有判据）；同名同色的两个系列并列时它会弃权，而声明式合同这里是**用户
    逐条指认**——判据主语从「指纹匹配」变成「用户指认」，不存在并列弃权。
    在 manifest/patch 之前把合同映射写进条目模型，后续 ``sync_legends``、重建
    与 manifest 的 ``legend_entry`` 字段都以它为准；custom 条目显式无源
    （指纹猜测的并列命中也一并撤销——跟随会把代理示意线换成源派生，违背
    「保持脚本原样」）。
    """
    for legend_role, series_roles in legend_follow.items():
        leg = ownership.get(legend_role)
        model = getattr(leg, "_mm_entries", None)
        if model is None or not isinstance(series_roles, list) or len(series_roles) != model.n:
            raise ChartManifestError("chart_manifest_ownership_invalid", "legend_follow_contract")
        for j, series_role in enumerate(series_roles):
            if series_role is None:
                model.sources[j] = None
                model.source_gids[j] = None
                model.default_binding[j] = "custom"
                model.binding_override.pop(j, None)
                continue
            source = ownership.get(series_role)
            if source is None:
                raise ChartManifestError(
                    "chart_manifest_ownership_invalid", f"legend_follow_source {series_role!r}"
                )
            if isinstance(source, list) and len(source) == 1:
                # plot 系列的 ownership 值是单元素列表；容器（bar/errorbar）保持原样
                source = source[0]
            model.sources[j] = source
            model.source_gids[j] = _legend_source_gid(state, source)
            model.default_binding[j] = "follow_source"


def render_scaffold(
    fig,
    ownership: dict,
    frame: dict,
    patches: list[dict],
    *,
    stem: str = "chart",
    salt: str = "tavotto",
    coordinate_space: str = "viewbox-user",
    complete_frame: bool = False,
    legend_follow: dict | None = None,
) -> dict:
    """全新 scaffold Figure + 完整 patch 列表 → 配对 SVG/native manifest。

    ownership 的值为实际 Artist、Container 或显式成员列表；每个对象只归一个
    ``axes:id`` / ``series:id`` / ``colorbar:id`` / ``legend:id`` / ``text:id`` /
    ``figure:id``。轴家具隐式跟轴，其他对象必须明确声明。角色、原始输入 gid、
    原生 editable gid、实际 SVG id 分开返回；SVG id 不授予后代所有权。

    ``legend_follow``（N4，可选）：声明式 legend 合同的显式指认——
    ``{"legend:<id>": ["series:<id>" | None, …]}``，列表按新图例的条目位置给出
    每项跟随的系列（None = custom）。instrument 之后、manifest/patch 之前落进
    条目模型，判据主语是**用户指认**而不是指纹猜测。

    无法在当前 scaffold 找到的完整 patch（包括原场景未拥有对象）稳定拒绝，
    不能为了预览静默丢弃。setter/identity 失败由 warnings 与 unrestored 显式
    返回，调用方须在接受编辑前处理。函数会修改传入 Figure，只用于新建图。
    """
    from matplotlib import rc_context
    from matplotlib.artist import Artist
    from matplotlib.container import Container

    from tavotto.engine import bridgeboot
    from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype

    vb, scene_size, scene_map = _frame(frame)
    if coordinate_space not in ("viewbox-user", "scene-viewport-pt"):
        raise ChartManifestError("chart_manifest_frame_invalid", "coordinate_space")
    runtime = bridgeboot.load_engine_modules(
        str(Path(__file__).resolve().parent), bridgeboot.ENGINE_SIBLINGS
    )
    canonical, dropped = runtime.patchspec.canonicalize_with_diagnostics(patches)
    if dropped:
        raise ChartManifestError("chart_manifest_patches_invalid", dropped[0]["reason"])
    claims, inputs = _claims(fig, ownership, Container, Artist)
    state = runtime.overrides.FigState(fig)
    runtime.manifest.instrument(state)
    if legend_follow:
        _apply_legend_follow_contract(ownership, state, legend_follow)
    gid_roles = _owners(state, claims, runtime)
    for patch in canonical:
        gid = patch["gid"]
        if gid not in gid_roles:
            # 原生 index 也有容器已消费成员、色条语义身份等别名；按实际对象
            # 资格核对，不能把合法全量 patch 当作缺失目标丢掉。
            artist = state.resolve(gid)
            if artist is not None and (
                id(artist) in claims
                or any(el["artist"] is artist and el["gid"] in gid_roles for el in state.elements)
            ):
                continue
            # 原生动态刻度标签：范围/定位器 patch 后才出现，沿用其唯一 gid 语法。
            match = runtime.tickmodel.TICKLABEL_GID.match(gid)
            axis_gid = f"axes_{match[1]}" if match else None
            if axis_gid not in gid_roles or state.resolve(gid) is None:
                raise ChartManifestError("chart_manifest_target_unowned", gid)
    # canonical 只用于输入校验/资格核对。应用顺序仍是原请求完整列表。
    warnings = runtime.overrides.apply(state, copy.deepcopy(patches))
    runtime.manifest.prepare_preview(state)
    gid_roles = _owners(state, claims, runtime)
    members = _svg_members(state, gid_roles, runtime)
    stream = io.BytesIO()
    with rc_context({"svg.fonttype": "none", "svg.hashsalt": salt, "savefig.bbox": None}):
        with runtime.manifest.capture_preview_manifest(state, stem) as measured:
            fig.savefig(stream, format="svg", metadata={"Date": None, "Creator": None})
            manifest = measured()
            render_frame = None
            if complete_frame:
                from matplotlib.transforms import Bbox

                from tavotto.engine import chartframe

                if coordinate_space != "scene-viewport-pt":
                    raise ChartManifestError("chart_manifest_frame_invalid", "complete_frame_space")
                ink = next(
                    (e.get("ink_bbox") for e in manifest["elements"] if e["gid"] == "figure"), None
                )
                render_frame = chartframe.from_ink(scene_size, ink)
                left, top, width, height = render_frame["rect_pt"]
                box = Bbox.from_bounds(
                    left / 72, (scene_size[1] - top - height) / 72, width / 72, height / 72
                )
                with runtime.manifest.pathgeom.measured_output_frame(fig, box):
                    manifest = measured()
    # SVG 的最终 draw 可以增加刻度；原生 measured 同步的是最终登记表。
    gid_roles = _owners(state, claims, runtime)
    fragment = strip_matplotlib_doctype(stream.getvalue())
    svg = parse_svg(fragment)
    svg_ids = {el.attrib["id"] for el in svg.iter() if "id" in el.attrib}
    fragment_vb = [float(v) for v in svg.attrib["viewBox"].split()]
    fx, fy, fw, fh = fragment_vb
    sx, sy = vb[2] / fw, vb[3] / fh
    fragment_map = [sx, 0.0, 0.0, sy, vb[0] - fx * sx, vb[1] - fy * sy]
    if coordinate_space == "scene-viewport-pt":
        from tavotto.svgcoord import invert

        if any(
            not math.isclose(a, b, abs_tol=1e-7)
            for a, b in zip(fragment_vb, [0.0, 0.0, *scene_size])
        ):
            raise ChartManifestError("chart_manifest_frame_invalid", "physical_figure_size")
        fragment_map = list(invert(scene_map))
    roles = {}
    for role in ownership:
        gids = [el["gid"] for el in state.elements if gid_roles.get(el["gid"]) == role]
        if not gids:
            raise ChartManifestError("chart_manifest_ownership_invalid", "unregistered_owner")
        roles[role] = {
            "primary_gid": _primary_gid(state, gids, ownership[role], Container) or gids[0],
            "gids": gids,
            "svg_ids": [gid for gid in members.get(role, []) if gid in svg_ids],
            "input_gids": inputs[role],
        }
    failed = {(p["gid"], p["prop"]) for p in canonical} - state.applied.keys()
    result = {
        "fragment": fragment,
        "manifest": _filtered_manifest(manifest, gid_roles),
        "roles": roles,
        "gid_roles": gid_roles,
        "unowned_gids": [el["gid"] for el in state.elements if el["gid"] not in gid_roles],
        "coordinates": {
            "kind": (
                "scene-viewport-figure-fraction"
                if coordinate_space == "scene-viewport-pt"
                else "scaffold-figure-fraction"
            ),
            "composed": False,
            "view_box": vb,
            "fragment_view_box": fragment_vb,
            "scaffold_size_pt": [fw, fh],
            "scene_size_pt": scene_size,
            "scene_user_to_pt": scene_map,
            "fragment_to_scene_user": fragment_map,
        },
        "warnings": warnings,
        "unrestored": len(failed | state.unrestored),
    }
    if render_frame is not None:
        result["render_frame"] = render_frame
        result["manifest"]["render_frame"] = copy.deepcopy(render_frame)
    if coordinate_space == "scene-viewport-pt":
        result["svg_members"] = _gid_svg_members(state, gid_roles, svg_ids, runtime)
        result["paint_order_overrides"] = [
            gid for gid, prop in state.applied if prop == "zorder" and gid in gid_roles
        ]
    return result
