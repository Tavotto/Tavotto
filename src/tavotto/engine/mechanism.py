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
import json
import math
from dataclasses import dataclass, field
from typing import Callable

from . import agentobjects, brand, license as engine_license, mechcheck, mechgroup, prohost

FEATURE = "mechanism-diagram"
KINDS = ("frame", "glyph", "graph")
#: 需要 `mechanism-diagram` 权益的条目种类（示意图框免费）
PAID_KINDS = ("glyph", "graph")
CMD_CATALOG = "mechanism.catalog"
CMD_GLYPH = "mechanism.glyph"
MAX_GLYPHS = 24
MAX_FRAMES = 1
MAX_SUBPATHS = 256  # 一个膜 = 几十个脂质 × 3 条子路径（闭合圆头 + 两条尾）；ADR 0220 §三
MAX_NODES = 4000
MAX_PORTS = 16
#: 默认画法（颜色与线宽由开源侧定，sidecar 不回）
DEFAULT_COLOR = "#4a4842"
DEFAULT_STROKE_PT = 1.0
FRAME_STROKE_PT = 0.75
FRAME_COLOR = "#8a8780"
LEARN_MORE = {"zh": "了解高级版", "en": "Learn about Premium"}

_FRAME_KEYS = {"kind", "name", "title", "at_mm", "size_mm", "fill"}
_GLYPH_KEYS = {
    "embed",
    "recipe",
    "core",
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
    "（语义图元，落成一个可编辑路径；glyph 字段由引擎写、不收；也可不写 glyph 而写 recipe: {…配方 JSON，recipe_format: 1…}（现写的图元配方，不含代码，"
    "由组件校验并执行，约束不满足回 bad_item 并点名）；frame 填同一个 add 里 frame 的 name，block = 框下的结构块名，如「细胞膜」）；"
    "跨膜蛋白可带 embed {membrane: 同一个 add 里膜条目的 name, at?: 0–1 沿膜弧长}（不给 at_mm、不给 at 就按各自实际宽度沿膜等空隙排，膜两端至少留 2 个脂质间距）；膜条目默认随膜落一个「疏水核心」底带对象（core: false 关掉）；样式预设（膜描边 0.6 pt、蛋白 0.85 pt）可被 style 覆盖：引擎把跨膜段中点对齐膜中线、"
    "沿膜法线旋转、跨膜段长取膜厚、给膜算 inserts 让位（窗口里的磷脂不画），蛋白都在膜的长度范围内且两两不重叠，否则 bad_item；"
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


def pro_required_result(locale: str | None = "zh") -> dict:
    """未授权时工具层回给 Agent / 桌面的结构化结果（**不是错误**）：一句话 + 「了解高级版」。插件与改图助手共用这一处翻译
    （一键排版的 MCP 工具，ADR 0199 §十三，以后复用它）。"""
    return {
        "ok": False,
        "code": "pro_required",
        "feature": FEATURE,
        "message": "机理图的语义图元与通路图是高级版功能；示意图框和免费的箭头、文字、括号、外框等标注不受影响。",
        # 「了解高级版」指向官网的高级版介绍页（维护者 2026-11-28）；地址的单一出处是 `brand.premium_page_url`，按界面语言选中英
        "learn_more": {
            "label": LEARN_MORE[mechgroup.locale_of(locale)],
            "url": brand.premium_page_url(locale),
        },
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
def _call(ask: Ask | None, cmd: str, payload: dict, name: str | None = None) -> dict:
    try:
        if ask is not None:
            return ask(cmd, payload)
        return prohost.call(cmd, payload, feature=FEATURE)
    except prohost.ProUnavailable as exc:
        if exc.code == "bad_request" and name is not None:
            # 组件认定这个条目的参数 / 配方不合格（越界、约束不满足、配方写错）：点名条目与原话（组件的话只有「constraint 名 / param 名」这类定位信息）
            detail = str(exc).split(": ", 1)[1][:120] if ": " in str(exc) else ""
            raise MechanismError("bad_item", f"{name}: {detail}" if detail else name) from None
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


MAX_INSERTS = 16
MIDLINE_MAX = 65


def _check_inserts(v: object, name: str) -> list:
    """`[{at: 0–1, width_mm}]`（膜给跨膜蛋白让位的窗口）：键闭集、有限数、≤ 16 条。"""
    if not isinstance(v, list) or len(v) > MAX_INSERTS:
        raise MechanismError("bad_item", name)
    out = []
    for it in v:
        if not isinstance(it, dict) or set(it) != {"at", "width_mm"}:
            raise MechanismError("bad_item", name)
        at, w = it["at"], it["width_mm"]
        if not _finite(at, w) or isinstance(at, bool) or isinstance(w, bool):
            raise MechanismError("bad_item", name)
        if not (0.0 <= at <= 1.0 and 0.0 < w <= 40.0):
            raise MechanismError("bad_item", name)
        out.append({"at": float(at), "width_mm": float(w)})
    return sorted(out, key=lambda x: (x["at"], x["width_mm"]))


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
        if p.get("type") == "inserts":
            v = _check_inserts(v, name)
        elif p.get("type") == "enum":
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
        "info": _check_info(reply.get("info"), name),
    }


_INFO_NUMBERS = ("thickness_mm", "pitch_mm", "tm_width_mm", "tm_length_mm", "core_half_width_mm")


def _check_info(info: object, name: str) -> dict:
    """回包里可选的 `info`（只有数字，宿主拿来定位 / 嵌入）：`natural_size_mm`、`tm_mid_mm`（框内毫米坐标，左上角原点、y 向下）、
    `thickness_mm / pitch_mm / tm_width_mm / tm_length_mm`、`midline`（≤ 65 个 `[x, y, nx, ny]`，朝外的单位法线）。不合 = `bad_reply`。"""
    if info is None:
        return {}
    if not isinstance(info, dict) or set(info) - {
        *_INFO_NUMBERS,
        "natural_size_mm",
        "tm_mid_mm",
        "midline",
    }:
        raise MechanismError("bad_reply", name)
    out: dict = {}
    for k in _INFO_NUMBERS:
        if k in info:
            if not _finite(info[k]) or isinstance(info[k], bool) or info[k] <= 0:
                raise MechanismError("bad_reply", name)
            out[k] = float(info[k])
    if "natural_size_mm" in info:
        v = _pair(info["natural_size_mm"], positive=True)
        if v is None:
            raise MechanismError("bad_reply", name)
        out["natural_size_mm"] = v
    if "tm_mid_mm" in info:
        v = _pair(info["tm_mid_mm"])
        if v is None:
            raise MechanismError("bad_reply", name)
        out["tm_mid_mm"] = v
    if "midline" in info:
        ml = info["midline"]
        if not isinstance(ml, list) or not (2 <= len(ml) <= MIDLINE_MAX):
            raise MechanismError("bad_reply", name)
        pts = []
        for row in ml:
            if not (isinstance(row, (list, tuple)) and len(row) == 4 and _finite(*row)):
                raise MechanismError("bad_reply", name)
            pts.append([float(x) for x in row])
        out["midline"] = pts
    return out


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
    frame_group: str | None = None

    @property
    def member_locals(self) -> set[str]:
        """机理图成员的局部 id：框线、图元、放进框里的免费条目（可编辑性核对只作用于它们）。"""
        return {o["id"] for o in self.objects} | set(self.free_groups)

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
    out.frame_group = frame_group
    if glyphs:
        _glyphs(glyphs, out, ask, loc, frame_name, frame_group)
    return out


@dataclass
class _G:
    """一个 glyph 条目解析后的样子（还没问组件）。"""

    it: dict
    name: str
    kind: str
    spec: dict
    version: int
    params: dict
    user_params: set
    at: list | None
    size: list | None
    style: dict
    frame: str | None
    block: str | None
    identity: str | None
    embed: dict | None
    reply: dict | None = None
    pose: dict | None = None
    recipe: dict | None = None  # Agent 现写的配方（随条目发，替代 kind）
    core: bool = True  # 膜：随膜放一个「疏水核心」底带对象


MAX_RECIPE_BYTES = 16384
RECIPE_FORMAT = 1


def _recipe_hash(recipe: dict) -> str:
    return mechcheck.recipe_hash(recipe)


def _recipe_spec(recipe: object, name: str) -> tuple[str, dict]:
    """Agent 现写的配方：宿主只做浅校验（形状 / 大小 / 格式版本 / 参数规格长什么样），表达式与部件由组件解释并校验；返回 `(显示用的 kind, 规格)`。"""
    if (
        not isinstance(recipe, dict)
        or recipe.get("recipe_format") != RECIPE_FORMAT
        or isinstance(recipe.get("recipe_format"), bool)
    ):
        raise MechanismError("bad_item", name)
    try:
        if (
            len(json.dumps(recipe, ensure_ascii=False, separators=(",", ":")).encode())
            > MAX_RECIPE_BYTES
        ):
            raise MechanismError("bad_item", name)
    except (TypeError, ValueError):
        raise MechanismError("bad_item", name) from None
    versions = recipe.get("versions", [1])
    if not (
        isinstance(versions, list)
        and versions
        and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in versions)
    ):
        raise MechanismError("bad_item", name)
    plist = recipe.get("params")
    if not isinstance(plist, list) or any(
        not isinstance(p, dict) or not isinstance(p.get("name"), str) for p in plist
    ):
        raise MechanismError("bad_item", name)
    kind = (
        recipe.get("kind") if isinstance(recipe.get("kind"), str) and recipe["kind"] else "custom"
    )
    spec = {
        "versions": sorted(set(versions)),
        "latest": max(versions),
        "params": {p["name"]: p for p in plist},
        "default_size_mm": [10.0, 10.0],  # 组件不看它（几何与它无关）；对象的外框取回包里的自然尺寸
    }
    return kind[:40], spec


def _parse_glyph(it: dict, out: Prepared, frame_name: str | None, frame_group: str | None) -> _G:
    name = it["name"]
    if set(it) - _GLYPH_KEYS:
        raise MechanismError("bad_item", name)
    recipe = it.get("recipe")
    if (it.get("glyph") is None) == (recipe is None):
        raise MechanismError("bad_item", name)  # 内置图元（glyph）与现写配方（recipe）二选一
    if recipe is not None:
        kind, spec = _recipe_spec(recipe, name)
    else:
        kind = it.get("glyph")
        spec = (out.catalog or {}).get(kind) if isinstance(kind, str) else None
        if spec is None:
            raise MechanismError("bad_item", name)
    embed = it.get("embed")
    if embed is not None:
        if (
            not isinstance(embed, dict)
            or set(embed) - {"membrane", "at"}
            or not isinstance(embed.get("membrane"), str)
            or (
                "at" in embed
                and (
                    not _finite(embed["at"])
                    or isinstance(embed["at"], bool)
                    or not 0.0 <= embed["at"] <= 1.0
                )
            )
            or it.get("at_mm") is not None
        ):
            raise MechanismError("bad_item", name)  # 嵌进膜的蛋白位置由宿主算，不许再给 at_mm
        at = None
    else:
        at = _pair(it.get("at_mm"))
        if at is None:
            raise MechanismError("bad_item", name)
    size = _pair(it.get("size_mm"), positive=True) if it.get("size_mm") is not None else None
    if it.get("size_mm") is not None and size is None:
        raise MechanismError("bad_item", name)
    frame, block = it.get("frame"), it.get("block")
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
    identity = it.get("identity")
    if identity is not None and not isinstance(identity, str):
        raise MechanismError("bad_item", name)
    core = it.get("core", True)
    if not isinstance(core, bool):
        raise MechanismError("bad_item", name)
    return _G(
        it, name, kind, spec, version, params, set(it.get("params") or {}), at, size, style,
        frame, block.strip() if block else None, identity, embed, recipe=recipe, core=core,
    )  # fmt: skip


def _request(g: _G, ask: Ask | None) -> dict:
    """问组件要几何（sidecar 只收数字与结构：不发名字、身份、块名）。尺寸只给（缺省取清单里的缺省尺寸，几何本身与它无关）。"""
    ident = {"recipe": g.recipe} if g.recipe is not None else {"kind": g.kind}
    reply = _check_reply(
        _call(
            ask,
            CMD_GLYPH,
            {
                **ident,
                "version": g.version,
                "params": g.params,
                "size_mm": g.size or g.spec["default_size_mm"],
            },
            g.name,
        ),
        g.name,
    )
    if reply["version"] != g.version:
        raise MechanismError("unsupported_version", g.name)
    return reply


def _midline_len(samples: list, sx: float, sy: float) -> list[float]:
    """中线采样点的累计弧长（毫米，已按对象缩放）。"""
    acc = [0.0]
    for a, b in zip(samples, samples[1:]):
        acc.append(acc[-1] + math.hypot((b[0] - a[0]) * sx, (b[1] - a[1]) * sy))
    return acc


def _frame_at(samples: list, acc: list[float], frac: float) -> tuple:
    """中线上弧长占比 `frac` 处的位置与朝外单位法线（采样点之间线性插值）。"""
    target = frac * acc[-1]
    i = 0
    while i < len(acc) - 2 and acc[i + 1] < target:
        i += 1
    span = (acc[i + 1] - acc[i]) or 1.0
    t = min(1.0, max(0.0, (target - acc[i]) / span))
    a, b = samples[i], samples[i + 1]
    x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
    nx, ny = a[2] + (b[2] - a[2]) * t, a[3] + (b[3] - a[3]) * t
    n = math.hypot(nx, ny) or 1.0
    return x, y, nx / n, ny / n


def _world_polys(g: _G) -> list:
    """蛋白旋转落位后各子路径的折线（画布毫米）：核两两交叠用。"""
    w, h = g.pose["size"]
    th = math.radians(g.pose["rot"])
    c, s = math.cos(th), math.sin(th)
    cx, cy = g.at[0] + w / 2, g.at[1] + h / 2
    out = []
    for sp in g.reply["subpaths"]:
        pts = mechcheck._flatten(sp, w, h)  # noqa: SLF001
        out.append(
            (
                [
                    (cx + (x - w / 2) * c - (y - h / 2) * s, cy + (x - w / 2) * s + (y - h / 2) * c)
                    for x, y in pts
                ],
                sp["closed"],
            )
        )
    return out


def _embed_layout(m: _G, prots: list[_G], ask: Ask | None) -> None:
    """一条膜上的嵌入蛋白：定位置、核「整个蛋白在膜长范围内（两端各留至少 2 个磷脂）、两两不重叠」、算膜的 `inserts` 回填（给膜重新要一次几何）、
    算每个蛋白的位姿（跨膜段中点落在中线上，沿该处法线旋转）。

    位置：**没给 at 的蛋白按实际宽度等空隙排**——膜长减去蛋白总宽，均分成 n + 1 个空隙（两端也各一个），空隙不到 2 个脂质间距就 `bad_item`（放不下）；
    有蛋白给了 at 就照给的，其余没给的仍按 (i + 0.5) / n。"""
    mi = m.reply["info"]
    samples = mi.get("midline")
    if not samples:
        raise MechanismError("bad_reply", m.name)
    nat = mi["natural_size_mm"]
    msize = m.size or nat
    sx, sy = msize[0] / nat[0], msize[1] / nat[1]
    acc = _midline_len(samples, sx, sy)
    total = acc[-1]
    k = total / (
        _midline_len(samples, 1.0, 1.0)[-1] or 1.0
    )  # 膜按对象尺寸拉伸后，弧长相对自然尺寸的倍数
    margin = 2 * mi.get("pitch_mm", 0.0) * k  # 膜两端各留至少 2 个脂质
    free = [g for g in prots if "at" not in g.embed]

    def pscale(g):  # 蛋白按对象尺寸缩放的倍数（回包里的测量都是自然尺寸）
        n = g.reply["info"]["natural_size_mm"]
        w, h = g.size or n
        return w / n[0], h / n[1]

    widths = {g.name: g.reply["info"]["natural_size_mm"][0] * pscale(g)[0] for g in prots}
    tmx = {g.name: g.reply["info"]["tm_mid_mm"][0] * pscale(g)[0] for g in prots}
    auto: dict[str, float] = {}
    if free and len(free) == len(prots):
        gap = (total - sum(widths.values())) / (len(prots) + 1)
        if gap < margin - 1e-6:
            raise MechanismError("bad_item", free[-1].name)  # 放不下：空隙不到 2 个脂质间距
        cursor = gap
        for g in prots:
            auto[g.name] = (cursor + tmx[g.name]) / total
            cursor += widths[g.name] + gap
    elif free:
        for g, f in zip(free, [(i + 0.5) / len(free) for i in range(len(free))]):
            auto[g.name] = f
    wins = []
    for g in prots:
        at = g.embed["at"] if "at" in g.embed else auto[g.name]
        s = at * total
        left = s - tmx[g.name]
        right = left + widths[g.name]
        if left < margin - 1e-6 or right > total - margin + 1e-6:
            raise MechanismError("bad_item", g.name)  # 超出膜的长度（或太靠近膜端）
        wins.append((left, right, g, at, g.reply["info"]["tm_width_mm"] * pscale(g)[0]))
    ordered = sorted(wins, key=lambda w: w[0])
    for a, b in zip(ordered, ordered[1:]):
        if b[0] < a[1] - 1e-6:
            raise MechanismError("bad_item", b[2].name)  # 两个蛋白重叠
    given = list(m.params.get("inserts") or [])
    # 让位窗口 = 跨膜部分的总宽；贴着杆的净空由膜自己定（头部半径 + 1/4 间距），窗口之间的每一段它重新均匀排布
    # 膜的窗口宽度按**自然尺寸**的弧长给组件（组件不知道对象被拉伸），所以除以拉伸倍数 k
    computed = [
        {"at": round(at, 6), "width_mm": round(w_tm / k, 6)} for _l, _r, _g, at, w_tm in wins
    ]
    m.params["inserts"] = _check_inserts([*given, *computed], m.name)
    m.reply = _request(m, ask)  # 让位后的膜（外框不变）
    mx, my = m.at or [0.0, 0.0]
    for _l, _r, g, at, _w in wins:
        px, py, nx, ny = _frame_at(samples, acc, at)
        target = (mx + px * sx, my + py * sy)
        nx, ny = nx / sx, ny / sy  # 非等比拉伸：法线按逆转置变换
        nn = math.hypot(nx, ny) or 1.0
        nx, ny = nx / nn, ny / nn
        theta = math.atan2(nx, -ny)  # 蛋白的「上」(0, −1) 转到法线方向，顺时针为正
        pi = g.reply["info"]
        w, h = g.size or pi["natural_size_mm"]
        psx, psy = pscale(g)
        tmx, tmy = pi["tm_mid_mm"][0] * psx, pi["tm_mid_mm"][1] * psy
        dx, dy = tmx - w / 2, tmy - h / 2
        c, sn = math.cos(theta), math.sin(theta)
        cx, cy = target[0] - (dx * c - dy * sn), target[1] - (dx * sn + dy * c)
        g.at = [cx - w / 2, cy - h / 2]
        g.pose = {"rot": math.degrees(theta), "size": [w, h]}
    # 弯膜上法线会聚（向下凸时在外侧）：按**旋转后的真实外廓**核两两不交叠；交叠 = bad_item（把膜拉长 / 少放一个 / 换位置），不静默改用户的造型
    polys = {g.name: _world_polys(g) for _l, _r, g, _at, _w in wins}
    names = [g.name for _l, _r, g, _at, _w in sorted(wins, key=lambda w: w[3])]
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if any(mechcheck._touch(pa, ca, pb, cb) for pa, ca in polys[a] for pb, cb in polys[b]):  # noqa: SLF001
                raise MechanismError("bad_item", b)


def _glyphs(
    items: list,
    out: Prepared,
    ask: Ask | None,
    loc: str,
    frame_name: str | None,
    frame_group: str | None,
) -> None:
    gs = [_parse_glyph(it, out, frame_name, frame_group) for it in items]
    by_name = {g.name: g for g in gs}
    # ① 没有嵌入关系的先要几何（膜等）；② 嵌入的蛋白：跨膜段长度取膜厚；③ 每条膜按嵌入的蛋白定位、让位、再要一次
    for g in gs:
        if g.embed is None:
            g.reply = _request(g, ask)
    groups: dict[str, list[_G]] = {}
    for g in gs:
        if g.embed is None:
            continue
        m = by_name.get(g.embed["membrane"])
        if m is None or m.kind != "membrane" or m.embed is not None:
            raise MechanismError("bad_item", g.name)
        thick = (m.reply or {}).get("info", {}).get("thickness_mm")
        if thick is None:
            raise MechanismError("bad_reply", m.name)
        pm = g.spec["params"].get("membrane_mm")
        if pm is not None and "membrane_mm" not in g.user_params:
            g.params["membrane_mm"] = round(
                min(pm["max"], max(pm["min"], thick)), 6
            )  # 跨膜段长度 = 膜厚（夹进该图元允许的范围）
        g.reply = _request(g, ask)
        pi = g.reply["info"]
        if "tm_mid_mm" not in pi or "tm_width_mm" not in pi or "natural_size_mm" not in pi:
            raise MechanismError("bad_reply", g.name)
        groups.setdefault(m.name, []).append(g)
    for mname, prots in groups.items():
        _embed_layout(by_name[mname], prots, ask)
    for g in gs:
        _emit(g, out, loc)
        if g.embed is not None:
            out.echo.append(
                f"「{g.name}」嵌在膜「{g.embed['membrane']}」里（跨膜段中点对齐中线，沿膜法线插入）"
            )


#: 样式预设（不改图元几何，用户随时可改；与组件 `catalog` 的 `style_hint` 同一句话：膜描边 0.6 pt、蛋白 0.85 pt，浅暖 / 浅蓝灰，不用渐变）
DEFAULT_STYLE = {
    "membrane": {"color": "#5a4636", "strokePt": 0.6, "fill": "#f3dcc0"},
    "receptor_rtk": {"color": "#3f4d5c", "strokePt": 0.85, "fill": "#d4dde6"},
}
CORE_FILL = "#f8eddf"


def _lighten(hexcolor: object) -> str:
    """头部填色往白里混 40 % 当底带色；不是 `#rrggbb` 就用缺省的极浅暖色。"""
    if isinstance(hexcolor, str) and len(hexcolor) == 7 and hexcolor[0] == "#":
        try:
            rgb = [int(hexcolor[i : i + 2], 16) for i in (1, 3, 5)]
        except ValueError:
            return CORE_FILL
        return "#" + "".join(f"{round(c + (255 - c) * 0.40):02x}" for c in rgb)
    return CORE_FILL


def _core_object(g: _G, size: list, style: dict, path: list, out: Prepared, loc: str):
    """疏水核心：一条沿膜中线、铺满上下两排头基圆心之间的极浅暖色底带，作为**单独一个对象**随膜落地（膜的组里、叠在磷脂下面）；
    两端收在最外侧磷脂头的圆心处，蛋白让位处同样断开、贴杆（窗口 = 跨膜部分总宽）。没有描边。返回 `(对象, glyph 元数据)`；回包不带中线（旧组件）= None。"""
    info = (g.reply or {}).get("info") or {}
    samples, nat = info.get("midline"), info.get("natural_size_mm")
    # 半宽 = 头部圆心离中线的距离 = 层厚倍数 × 头半径 / 2（膜的参数）；没有这两个参数（别的组件 / 旧清单）就退回回包里的 `core_half_width_mm`
    p = g.params
    if (
        _finite(p.get("thickness_r"), p.get("head_radius_mm"))
        and not isinstance(p.get("thickness_r"), bool)
        and not isinstance(p.get("head_radius_mm"), bool)
    ):
        hw = p["thickness_r"] * p["head_radius_mm"] / 2
    else:
        hw = info.get("core_half_width_mm")
    if not samples or not hw or not nat:
        return None
    sx, sy = size[0] / nat[0], size[1] / nat[1]
    acc = _midline_len(samples, sx, sy)
    total = acc[-1]
    if total <= 0:
        return None
    k = total / (_midline_len(samples, 1.0, 1.0)[-1] or 1.0)  # 对象拉伸后弧长相对自然尺寸的倍数
    inset = info.get("pitch_mm", 0.0) * k / 2  # 两端收在最外侧磷脂头的圆心处（约半个脂质间距）
    cuts = sorted(
        (it["at"] * total - it["width_mm"] * k / 2, it["at"] * total + it["width_mm"] * k / 2)
        for it in g.params.get("inserts") or []
    )
    segs, cur = [], inset
    for lo, hi in cuts:
        if min(lo, total - inset) - cur > 0.05:
            segs.append((cur, min(lo, total - inset)))
        cur = max(cur, hi)
    if total - inset - cur > 0.05:
        segs.append((cur, total - inset))
    subs = []
    for a, b in segs:
        arcs = [a, *[s for s in acc if a < s < b], b]
        upper, lower = [], []
        for s in arcs:
            px, py, nx, ny = _frame_at(samples, acc, s / total)
            # 中线与法向偏移都在自然尺寸里算，再整体按对象尺寸缩放（与膜的头部圆同一变换）
            upper.append([(px + nx * hw) / nat[0], (py + ny * hw) / nat[1]])
            lower.append([(px - nx * hw) / nat[0], (py - ny * hw) / nat[1]])
        poly = upper + lower[::-1]
        subs.append({"closed": True, "nodes": [{"p": p} for p in poly]})
    if not subs:
        return None
    cid = f"{g.name}__core"
    obj = {
        "id": cid,
        "type": "path",
        "x": g.at[0],
        "y": g.at[1],
        "w": size[0],
        "h": size[1],
        "subpaths": subs,
        "strokePt": 0.1,
        "stroke": False,
        "color": style.get("color", DEFAULT_COLOR),
        "fill": _lighten(style.get("fill")),
        "name": out.names.unique(mechgroup.name_for("hydrophobic_core", None, loc)),
    }
    if path:
        obj["groupPath"] = list(path)
    meta = {
        "kind": "hydrophobic_core",
        "params": {},
        "version": g.version,
        "hash": mechcheck.glyph_hash(subs),
        "array": True,  # 让位处断开 = 几段；仍是一个对象
        "host_made": True,  # 宿主据膜的 `info` 算出来的，不是组件的图元（不进 catalog 核对）
    }
    return obj, meta


def _emit(g: _G, out: Prepared, loc: str) -> None:
    reply = g.reply or {}
    info = reply.get("info") or {}
    size = (
        (g.pose or {}).get("size")
        or g.size
        or info.get("natural_size_mm")
        or g.spec["default_size_mm"]
    )
    at = g.at
    rot = (g.pose or {}).get("rot", 0.0)
    path = [
        *([out.frame_group] if out.frame_group and g.frame else []),
        *([g.block] if g.block else []),
    ]
    preset = {} if g.recipe is not None else DEFAULT_STYLE.get(g.kind, {})
    style = {**preset, **g.style}  # 样式预设：用户给的永远压过它
    if g.kind == "membrane" and g.recipe is None and g.core:
        core = _core_object(g, size, style, path, out, loc)
        if core is not None:
            out.objects.append(core[0])  # 叠在磷脂下面：先放
            out.glyph_meta[core[0]["id"]] = core[1]
    obj = {
        "id": g.name,
        "type": "path",
        "x": at[0],
        "y": at[1],
        "w": size[0],
        "h": size[1],
        "subpaths": reply["subpaths"],
        "strokePt": style.get("strokePt", DEFAULT_STROKE_PT),
        "color": style.get("color", DEFAULT_COLOR),
        "fill": style.get("fill"),
        "name": out.names.unique(mechgroup.name_for(f"glyph:{g.kind}", g.identity, loc)),
    }
    if abs(rot) > 1e-9:
        obj["rotationDeg"] = round(rot, 3)
    if path:
        obj["groupPath"] = path
    out.objects.append(obj)
    meta = {
        "kind": g.kind,
        "params": g.params,
        "version": g.version,
        "hash": mechcheck.glyph_hash(reply["subpaths"]),
    }
    if g.recipe is not None:
        # 配方全文进元数据：读时比 `recipe_hash`（覆盖配方全文），`hash` 仍是生成几何的摘要（手改形状 = 脱钩）
        meta["recipe"] = g.recipe
        meta["recipe_format"] = RECIPE_FORMAT
        meta["recipe_hash"] = _recipe_hash(g.recipe)
    if reply["array"]:
        meta["array"] = True
    out.glyph_meta[g.name] = meta
    th = math.radians(rot)
    c, sn = math.cos(th), math.sin(th)
    cx, cy = at[0] + size[0] / 2, at[1] + size[1] / 2

    def to_canvas(v: list) -> list:
        dx, dy = v[0] * size[0] - size[0] / 2, v[1] * size[1] - size[1] / 2
        return [cx + dx * c - dy * sn, cy + dx * sn + dy * c]

    out.ports[g.name] = {k: to_canvas(v) for k, v in reply["ports"].items()}
    out.echo.append(
        f"图元「{mechgroup.role_word(f'glyph:{g.kind}', loc)}」{g.name}（修订 {g.version}）"
    )


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
