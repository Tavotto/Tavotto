"""机理图（ADR 0220）：开源侧编排——解析条目、问闭源组件要图元几何、校验回包、铸对象、命名分组、核对可编辑性。

**权益判断只在这里**（`license.has("mechanism-diagram")`，ADR 0193 §一 / ADR 0220 §二）；`vectorscene` / `vectoredit` 不判权益，只在 `add` 里
把三种新 `kind`（`frame` / `glyph` / `graph`）交给本模块。**示意图框 `frame` 免费**（维护者 2026-11-28 定），未授权也照常收下、命名分组与核对；
一整批 `add` 里只要有一个 `glyph` / `graph`，**整批**先过权益，未授权抛 `MechanismError("not_licensed")`，工具层（`vectoredit._plan`）折成结构化
`pro_required`，不写一半（含同批的 `frame`）。

**sidecar 只收到数字与结构**（ADR 0198 §三）：命令 `mechanism.catalog`（图元清单与参数规格）、`mechanism.glyph`（按 kind / version / params / 毫米尺寸出几何）；
条目里的文字（标题、身份、块名）不发出本机。回包只收闭集：框内比例坐标的子路径节点、命名端口、`array` 标记、实际用的 `version`；
不回颜色、名字、文字——颜色与名字由本模块定。`graph`（通路布局）协议见 ADR 0220 §四，实现在后续期。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Callable

from . import agentobjects, license as engine_license, mechcheck, mechgroup, prohost

FEATURE = "mechanism-diagram"
KINDS = ("frame", "glyph", "graph")
#: 需要 `mechanism-diagram` 权益的条目种类（示意图框免费）
PAID_KINDS = ("glyph", "graph")
CMD_CATALOG = "mechanism.catalog"
CMD_GLYPH = "mechanism.glyph"
MAX_GLYPHS = 24
MAX_FRAMES = 1
MAX_SUBPATHS = 64
MAX_NODES = 4000
MAX_PORTS = 16
#: 默认画法（颜色与线宽由开源侧定，sidecar 不回）
DEFAULT_COLOR = "#4a4842"
DEFAULT_STROKE_PT = 1.0
FRAME_STROKE_PT = 0.75
FRAME_COLOR = "#8a8780"
LEARN_MORE = "了解高级版"

_FRAME_KEYS = {"kind", "name", "title", "at_mm", "size_mm", "fill"}
_GLYPH_KEYS = {
    "kind",
    "name",
    "glyph",
    "identity",
    "frame",
    "block",
    "at_mm",
    "size_mm",
    "params",
    "version",
    "style",
}
_STYLE_KEYS = {"color", "strokePt", "fill"}

#: `vectoredit.OPS_DOC` 的 `add` 接在 `vectorscene.ITEMS_DOC` 后面的那一段（机理图条目词汇的**唯一**说明）。免费用户的工具说明里也有它，
#: 调用回结构化 `pro_required`。位置用画布毫米（`at_mm`，框内条目也是画布毫米，没有「局部坐标」概念）。
ITEMS_DOC = (
    "机理图条目（示意图框 frame 免费；glyph / graph 是高级版 `mechanism-diagram`，没有授权时含它们的整批回 pro_required、一条也不写）："
    "frame {name, title?, at_mm: [x, y], size_mm: [w, h], fill?}（示意图框：一个带名字的组 + 一个外框形状；一个 add 最多一个）；"
    "glyph {name, glyph: 图元种类, at_mm, size_mm?, params?, version?, identity?, frame?, block?, style?: {color, strokePt, fill}}"
    "（语义图元，落成一个可编辑路径；glyph 字段由引擎写、不收；frame 填同一个 add 里 frame 的 name，block = 框下的结构块名，如「细胞膜」）；"
    "graph（通路图，尚未开放）。免费条目（箭头 / 文字 / 括号 / 外框…）也可带 frame / block 进同一个示意图框。"
    "名字由引擎按「角色 + 身份」生成并固化，同批重名自动加序号；落地后过可编辑性核对，不过整批不落地（not_editable）。"
)

Ask = Callable[[str, dict], dict]


class MechanismError(Exception):
    """`code`：`not_licensed`（整批拒，工具层折成 `pro_required`）/ `unavailable`（组件不在或起不来）/ `pro_outdated`（组件不认这条命令）/
    `bad_item`（条目写法不对，`field` 指到哪个条目）/ `unsupported_version`（组件不认这个图元修订号）/ `bad_reply`（组件回包不合闭集）/
    `not_ready`（`graph` 还没实现）。"""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(code if field is None else f"{code}: {field}")
        self.code, self.field = code, field


def licensed() -> bool:
    return engine_license.has(FEATURE)


def pro_required_result() -> dict:
    """未授权时工具层回给 Agent / 桌面的结构化结果（**不是错误**）：一句话 + 「了解高级版」。插件与改图助手共用这一处翻译
    （一键排版的 MCP 工具，ADR 0199 §十三，以后复用它）。"""
    return {
        "ok": False,
        "code": "pro_required",
        "feature": FEATURE,
        "message": "机理图的语义图元与通路图是高级版功能；示意图框和免费的箭头、文字、括号、外框等标注不受影响。",
        "learn_more": LEARN_MORE,
    }


def is_mechanism_item(it: object) -> bool:
    return isinstance(it, dict) and it.get("kind") in KINDS


def _finite(*vals: object) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals
    )


def _pair(v: object, positive: bool = False) -> list[float] | None:
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and _finite(*v)):
        return None
    out = [float(v[0]), float(v[1])]
    lim = agentobjects.table()["budgets"]["max_coord_mm"]
    if positive and not (all(x > 0 for x in out) and all(x <= lim for x in out)):
        return None
    if not positive and not all(abs(x) <= lim for x in out):
        return None
    return out


# ------------------------------------------------------------------ 组件调用
def _call(ask: Ask | None, cmd: str, payload: dict) -> dict:
    try:
        if ask is not None:
            return ask(cmd, payload)
        return prohost.call(cmd, payload, feature=FEATURE)
    except prohost.ProUnavailable as exc:
        # 旧组件不认这条命令：折成「需要更新高级版组件」（ADR 0199 §十二 同口径），不改文档
        raise MechanismError(
            "pro_outdated" if exc.code == "unsupported" else "unavailable"
        ) from None


def load_catalog(ask: Ask | None = None) -> dict:
    """`mechanism.catalog` → `{kind: {versions: [int], latest: int, params: {名: 规格}, default_size_mm: [w, h]}}`（只收合法的，其余丢弃）。"""
    raw = _call(ask, CMD_CATALOG, {})
    out: dict = {}
    for spec in raw.get("kinds") if isinstance(raw.get("kinds"), list) else []:
        if not isinstance(spec, dict) or not isinstance(spec.get("kind"), str):
            continue
        versions = [
            v
            for v in spec.get("versions") or []
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1
        ]
        size = _pair(spec.get("default_size_mm"), positive=True)
        if not versions or size is None:
            continue
        params = {
            p["name"]: p
            for p in spec.get("params") or []
            if isinstance(p, dict) and isinstance(p.get("name"), str)
        }
        out[spec["kind"]] = {
            "versions": sorted(set(versions)),
            "latest": max(versions),
            "params": params,
            "default_size_mm": size,
        }
    return out


def _check_params(spec: dict, params: object, name: str) -> dict:
    """参数只许是该图元规格里有的名字，值在范围 / 枚举内；缺的补默认值（新版本只许**加**参数并给默认值）。"""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise MechanismError("bad_item", name)
    known = spec["params"]
    out: dict = {}
    for k, v in params.items():
        p = known.get(k)
        if p is None:
            raise MechanismError("bad_item", name)
        if p.get("type") == "enum":
            if v not in (p.get("values") or []):
                raise MechanismError("bad_item", name)
        elif p.get("type") == "bool":
            if not isinstance(v, bool):
                raise MechanismError("bad_item", name)
        else:
            if not _finite(v) or isinstance(v, bool):
                raise MechanismError("bad_item", name)
            lo, hi = p.get("min"), p.get("max")
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                raise MechanismError("bad_item", name)
        out[k] = v
    for k, p in known.items():
        if k not in out and "default" in p:
            out[k] = p["default"]
    return out


def _check_reply(reply: object, name: str) -> dict:
    """`mechanism.glyph` 回包闭集：`{version, subpaths: [{closed, nodes: [{p, in?, out?}]}], ports?: {名: [rx, ry]}, array?: bool}`。"""
    if not isinstance(reply, dict):
        raise MechanismError("bad_reply", name)
    version = reply.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise MechanismError("bad_reply", name)
    subs = reply.get("subpaths")
    if not isinstance(subs, list) or not subs or len(subs) > MAX_SUBPATHS:
        raise MechanismError("bad_reply", name)
    total = 0
    clean = []
    for sp in subs:
        nodes = sp.get("nodes") if isinstance(sp, dict) else None
        if not isinstance(nodes, list) or len(nodes) < 2 or not isinstance(sp.get("closed"), bool):
            raise MechanismError("bad_reply", name)
        cn = []
        for n in nodes:
            if not isinstance(n, dict) or set(n) - {"p", "in", "out"} or _pair(n.get("p")) is None:
                raise MechanismError("bad_reply", name)
            node = {"p": [float(x) for x in n["p"]]}
            for k in ("in", "out"):
                if n.get(k) is not None:
                    if _pair(n[k]) is None:
                        raise MechanismError("bad_reply", name)
                    node[k] = [float(x) for x in n[k]]
            cn.append(node)
        total += len(cn)
        clean.append({"closed": sp["closed"], "nodes": cn})
    if total > MAX_NODES:
        raise MechanismError("bad_reply", name)
    ports = reply.get("ports") or {}
    if not isinstance(ports, dict) or len(ports) > MAX_PORTS:
        raise MechanismError("bad_reply", name)
    for k, v in ports.items():
        if not isinstance(k, str) or _pair(v) is None:
            raise MechanismError("bad_reply", name)
    return {
        "version": version,
        "subpaths": clean,
        "ports": {k: [float(x) for x in v] for k, v in ports.items()},
        "array": reply.get("array") is True,
    }


# ------------------------------------------------------------------ 编排
@dataclass
class Prepared:
    """`prepare` 的结果：交给 0208 求解器的免费条目、本模块造好的对象（名字链形态的 `groupPath`，由 `agentobjects` 铸 id）、
    免费条目要进哪个框 / 块、`glyph` 字段（校验之后才写：`glyph` 是字段表的保留字段，Agent 不能直接写）。"""

    free_items: list
    objects: list = field(default_factory=list)
    free_groups: dict = field(default_factory=dict)  # 条目名 → [组名, …]
    glyph_meta: dict = field(default_factory=dict)  # 局部 id → glyph 字段
    catalog: dict | None = None
    has_frame: bool = False
    ports: dict = field(default_factory=dict)  # 局部 id → {端口名: 画布毫米坐标}
    echo: list = field(default_factory=list)
    names: mechgroup.Names = field(default_factory=mechgroup.Names)

    @property
    def active(self) -> bool:
        return bool(self.objects or self.free_groups or self.has_frame)


def prepare(items: list, *, locale: str = "zh", ask: Ask | None = None) -> Prepared:
    """把一批 `add` 条目里的 `frame` / `glyph` / `graph` 拆出来处理，其余原样还给 0208 求解器。

    没有三种新 `kind` = 零开销、不判权益。有 = 先判权益（整批拒），再校验、问组件。"""
    mech = [it for it in items if is_mechanism_item(it)]
    stray = [
        it
        for it in items
        if not is_mechanism_item(it) and isinstance(it, dict) and ("frame" in it or "block" in it)
    ]
    if not mech:
        if stray:
            raise MechanismError("bad_item", str(stray[0].get("name")))  # 点名了框，却没有框
        return Prepared(free_items=items)
    # 维护者 2026-11-28 定：示意图框 `frame` 免费；`glyph` / `graph` 算高级版。只有它们触发权益判断与整批拒
    if any(it["kind"] in PAID_KINDS for it in mech) and not licensed():
        raise MechanismError("not_licensed")
    out = Prepared(free_items=[])
    loc = mechgroup.locale_of(locale)
    frames = [it for it in mech if it["kind"] == "frame"]
    glyphs = [it for it in mech if it["kind"] == "glyph"]
    if any(it["kind"] == "graph" for it in mech):
        raise MechanismError(
            "not_ready", str(next(it for it in mech if it["kind"] == "graph").get("name"))
        )
    if len(frames) > MAX_FRAMES or len(glyphs) > MAX_GLYPHS:
        raise MechanismError("bad_item", "items")
    names_seen: set[str] = set()
    for it in mech:
        n = it.get("name")
        if not isinstance(n, str) or not n or n in names_seen:
            raise MechanismError("bad_item", str(n))
        names_seen.add(n)
    frame_group: str | None = None
    frame_name: str | None = None
    if frames:
        fr = frames[0]
        name = fr["name"]
        if set(fr) - _FRAME_KEYS:
            raise MechanismError("bad_item", name)
        title, at, size = (
            fr.get("title"),
            _pair(fr.get("at_mm")),
            _pair(fr.get("size_mm"), positive=True),
        )
        if (title is not None and not isinstance(title, str)) or at is None or size is None:
            raise MechanismError("bad_item", name)
        fill = fr.get("fill")
        if fill is not None and not isinstance(fill, str):
            raise MechanismError("bad_item", name)
        frame_name = name
        frame_group = mechgroup.name_for("frame", title or name, loc)
        out.has_frame = True
        out.objects.append(
            {
                "id": f"{name}__box",
                "type": "shape",
                "shape": "rect",
                "x": at[0],
                "y": at[1],
                "w": size[0],
                "h": size[1],
                "strokePt": FRAME_STROKE_PT,
                "color": FRAME_COLOR,
                "fill": fill,
                "name": out.names.unique(mechgroup.name_for("frame_box", title or name, loc)),
                "groupPath": [frame_group],
            }
        )
        out.echo.append(
            f"示意图框「{title or name}」{size[0]:g}×{size[1]:g} mm，在 ({at[0]:g}, {at[1]:g})"
        )
    for it in items:
        if is_mechanism_item(it):
            continue
        ref = it.get("frame") if isinstance(it, dict) else None
        block = it.get("block") if isinstance(it, dict) else None
        if isinstance(it, dict) and ("frame" in it or "block" in it):
            if (
                ref != frame_name
                or frame_group is None
                or (block is not None and not isinstance(block, str))
            ):
                raise MechanismError("bad_item", str(it.get("name")))
            clean = {k: v for k, v in it.items() if k not in ("frame", "block")}
            out.free_items.append(clean)
            out.free_groups[str(it.get("name"))] = [frame_group, *([block] if block else [])]
        else:
            out.free_items.append(it)
    if glyphs:
        out.catalog = load_catalog(ask)
    for it in glyphs:
        _glyph(it, out, ask, loc, frame_name, frame_group)
    return out


def _glyph(
    it: dict,
    out: Prepared,
    ask: Ask | None,
    loc: str,
    frame_name: str | None,
    frame_group: str | None,
) -> None:
    name = it["name"]
    if set(it) - _GLYPH_KEYS:
        raise MechanismError("bad_item", name)
    kind = it.get("glyph")
    spec = (out.catalog or {}).get(kind) if isinstance(kind, str) else None
    if spec is None:
        raise MechanismError("bad_item", name)
    at = _pair(it.get("at_mm"))
    size = (
        _pair(it.get("size_mm"), positive=True)
        if it.get("size_mm") is not None
        else spec["default_size_mm"]
    )
    if at is None or size is None:
        raise MechanismError("bad_item", name)
    frame = it.get("frame")
    block = it.get("block")
    if frame is not None and (frame != frame_name or frame_group is None):
        raise MechanismError("bad_item", name)
    if block is not None and (not isinstance(block, str) or not block.strip() or frame is None):
        raise MechanismError("bad_item", name)
    version = it.get("version", spec["latest"])
    if not isinstance(version, int) or isinstance(version, bool) or version not in spec["versions"]:
        raise MechanismError("unsupported_version", name)
    params = _check_params(spec, it.get("params"), name)
    style = it.get("style") or {}
    if not isinstance(style, dict) or set(style) - _STYLE_KEYS:
        raise MechanismError("bad_item", name)
    # sidecar 只收数字与结构：不发名字、身份、块名
    reply = _check_reply(
        _call(
            ask, CMD_GLYPH, {"kind": kind, "version": version, "params": params, "size_mm": size}
        ),
        name,
    )
    if reply["version"] != version:
        raise MechanismError("unsupported_version", name)
    identity = it.get("identity")
    if identity is not None and not isinstance(identity, str):
        raise MechanismError("bad_item", name)
    path = [*([frame_group] if frame_group and frame else []), *([block.strip()] if block else [])]
    obj = {
        "id": name,
        "type": "path",
        "x": at[0],
        "y": at[1],
        "w": size[0],
        "h": size[1],
        "subpaths": reply["subpaths"],
        "strokePt": style.get("strokePt", DEFAULT_STROKE_PT),
        "color": style.get("color", DEFAULT_COLOR),
        "fill": style.get("fill"),
        "name": out.names.unique(mechgroup.name_for(f"glyph:{kind}", identity, loc)),
    }
    if path:
        obj["groupPath"] = path
    out.objects.append(obj)
    meta = {
        "kind": kind,
        "params": params,
        "version": version,
        "hash": mechcheck.glyph_hash(reply["subpaths"]),
    }
    if reply["array"]:
        meta["array"] = True
    out.glyph_meta[name] = meta
    out.ports[name] = {
        k: [at[0] + v[0] * size[0], at[1] + v[1] * size[1]] for k, v in reply["ports"].items()
    }
    out.echo.append(f"图元「{mechgroup.role_word(f'glyph:{kind}', loc)}」{name}（修订 {version}）")


def place_free_groups(prep: Prepared, free_objects: list[dict], locale: str = "zh") -> list[dict]:
    """免费条目求解出来的对象按条目名进框 / 块（名字链形态的 `groupPath`，之后和图元对象一起交给 `agentobjects` 铸 id）；
    进了框而没有名字的，按「角色 + 身份」补名字（文字用它的内容，其余用条目名；同批重名加序号）。"""
    loc = mechgroup.locale_of(locale)
    out = []
    for o in free_objects:
        chain = prep.free_groups.get(o.get("id"))
        if not chain:
            out.append(o)
            continue
        new = {**o, "groupPath": list(chain)}
        if not (isinstance(o.get("name"), str) and o["name"].strip()):
            ident = (
                str(o.get("text"))[:24]
                if o.get("type") == "text" and o.get("text")
                else str(o.get("id"))
            )
            new["name"] = prep.names.unique(mechgroup.name_for(str(o.get("type")), ident, loc))
        out.append(new)
    return out


def finish(prep: Prepared, checked: dict) -> dict:
    """`agentobjects.validate_new_objects` 之后：把 `glyph` 字段写到铸好 id 的对象上（保留字段，只有引擎能写）。
    返回 `{局部 id: 写好 glyph 的对象}`（调用方再换成最终 id）。"""
    by_local = {}
    inv = {real: local for local, real in checked["id_map"].items()}
    for o in checked["objects"]:
        local = inv.get(o["id"], o["id"])
        new = copy.deepcopy(o)
        if local in prep.glyph_meta:
            new["glyph"] = copy.deepcopy(prep.glyph_meta[local])
        by_local[local] = new
    return by_local
