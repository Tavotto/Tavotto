"""Agent 新增的画布对象：严格校验、id 生成、转成导出载荷（ADR 0208 §五）。

**只用于 Agent 新增的对象**（场景求解的产物、经 `svgimport` 落地的对象）。不许挂到文档保存路径：
用户自己的编辑走 `engine/documents.validate_document`，那里刻意只查骨架（理由见它的说明）。

三件事：

- `validate_new_objects`：按字段表 `profiles/agent_objects.json`（唯一出处，前端 `lib/agentObjects.ts`
  读同一份）逐字段校验。未知字段、保留字段、越界、类型不对一律拒绝，错误码是闭集 `ERROR_CODES`；
  Agent 给的局部 id / 组名换成正式 id，映射回给调用方。整批要么全收、要么全拒。
- `new_id`：与前端 `lib/id.newId` 同一格式（`<前缀>_` + 8 位 `[a-z0-9]`），前缀按类型取
  （字段表 `id_prefix`，与前端各处 `newId('t' | 'a' | 's' | 'p' | 'g')` 一致）。
- `to_export_objects`：校验过的对象 → 导出载荷，与前端 `lib/exportPayload.toExportObject` 对这几种对象
  逐字段相同（严格同源对，`tests/golden/agent_object_vectors.json` 两侧对拍）。Agent 的对象没有剪切蒙版、
  没有路径文字，所以这里不移植 `clip` 链与 `pathText`。
"""

from __future__ import annotations

import json
import math
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..rendercore import geometry

TABLE_FILE = "agent_objects.json"

#: 错误码闭集（两侧同名；golden 向量只比码与字段，不比措辞）。
ERROR_CODES = (
    "not_a_list",  # 输入不是数组
    "too_many",  # 对象数 / 锚点数 / 文字长度超出预算
    "not_an_object",  # 某一项不是对象
    "unknown_type",  # type 不在字段表里
    "missing_field",  # 缺必填字段
    "unknown_field",  # 字段表里没有的字段（含不给 Agent 的字段）
    "reserved_field",  # 字段表保留、还没开放的字段（anchor / glyph）
    "bad_value",  # 类型不对 / 不是有限数 / 枚举外 / 颜色写法不对 / 结构不对
    "out_of_range",  # 数值越界
    "duplicate_id",  # 同一批里局部 id 重复
    "bad_group_path",  # 组路径为空、过深、名字不对
)

_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_HEX6 = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@lru_cache(maxsize=1)
def table() -> dict:
    path = Path(__file__).resolve().parent.parent / "profiles" / TABLE_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def new_id(prefix: str, taken: set[str]) -> str:
    """`<prefix>_` + 8 位；与 `taken` 不撞（撞了重抽）。用 `secrets`，不依赖随机种子。"""
    while True:
        s = prefix + "_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(8))
        if s not in taken:
            taken.add(s)
            return s


# ------------------------------------------------------------------ 校验


class _Errors:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, code: str, index: int | None = None, field: str | None = None) -> None:
        assert code in ERROR_CODES, code
        e: dict[str, Any] = {"code": code}
        if index is not None:
            e["index"] = index
        if field is not None:
            e["field"] = field
        self.items.append(e)


def _is_num(v: object) -> bool:
    """有限的数（布尔不算）。JSON 里的超大整数（如 10**400）转 float 会抛 OverflowError：当成不是数，
    走结构化拒绝，不让校验本身崩掉（Codex #2196）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))
    except OverflowError:
        return False


def _check_point(v: object, lim: float) -> bool:
    return (
        isinstance(v, (list, tuple))
        and len(v) == 2
        and all(_is_num(c) and abs(c) <= lim for c in v)
    )


def _check_value(spec: dict, v: object, budgets: dict) -> str | None:
    """一个字段的值 → None（合格）或错误码。"""
    kind = spec["kind"]
    lim = budgets["max_ratio"]
    if kind == "number" or kind == "int":
        if not _is_num(v) or (kind == "int" and float(v) != int(v)):  # type: ignore[arg-type]
            return "bad_value"
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):  # type: ignore[operator]
            return "out_of_range"
        return None
    if kind == "coord":
        if not _is_num(v):
            return "bad_value"
        return "out_of_range" if abs(v) > budgets["max_coord_mm"] else None  # type: ignore[arg-type]
    if kind == "size":
        if not _is_num(v):
            return "bad_value"
        return "out_of_range" if not 0 <= v <= budgets["max_coord_mm"] else None  # type: ignore[operator]
    if kind == "bool":
        return None if isinstance(v, bool) else "bad_value"
    if kind == "enum":
        return None if isinstance(v, str) and v in spec["values"] else "bad_value"
    if kind == "color":
        return None if isinstance(v, str) and _HEX6.match(v) else "bad_value"
    if kind == "color_or_null":
        return None if v is None or (isinstance(v, str) and _HEX6.match(v)) else "bad_value"
    if kind == "text":
        if not isinstance(v, str) or "\x00" in v:
            return "bad_value"
        return "too_many" if len(v) > budgets["max_text_chars"] else None
    if kind == "name":
        if not isinstance(v, str) or not v.strip() or _CONTROL.search(v):
            return "bad_value"
        return "out_of_range" if len(v) > budgets["max_name_chars"] else None
    if kind == "endpoint":
        ok = isinstance(v, dict) and set(v) == {"rx", "ry"}
        return None if ok and _check_point([v["rx"], v["ry"]], lim) else "bad_value"  # type: ignore[index]
    if kind == "dash_pattern":
        if not isinstance(v, list) or len(v) > budgets["max_dash_items"]:
            return "bad_value"
        return None if geometry.valid_dash_pattern(v) is not None else "bad_value"
    if kind == "gradient":
        if not isinstance(v, dict):
            return "bad_value"
        stops = v.get("stops")
        if not isinstance(stops, list) or len(stops) > budgets["max_gradient_stops"]:
            return "bad_value"
        if set(v) - {"kind", "stops", "from", "to", "center", "radius"}:
            return "bad_value"
        for st in stops:
            if not isinstance(st, dict) or set(st) - {"offset", "color", "opacity"}:
                return "bad_value"
            if not _is_num(st.get("offset")) or not 0 <= st["offset"] <= 1:
                return "bad_value"
            if not isinstance(st.get("color"), str) or not _HEX6.match(st["color"]):
                return "bad_value"
            if "opacity" in st and (not _is_num(st["opacity"]) or not 0 <= st["opacity"] <= 1):
                return "bad_value"
        # 读得成才算数：与导出端同一条（`geometry.read_gradient` ↔ 前端 `readGradient`）
        return None if geometry.read_gradient(v) is not None else "bad_value"
    if kind == "subpaths":
        return _check_subpaths(v, budgets)
    return "bad_value"  # 字段表里写了这里不认识的 kind：拒绝，不放行


def _check_subpaths(v: object, budgets: dict) -> str | None:
    lim = budgets["max_ratio"]
    if not isinstance(v, list) or not v:
        return "bad_value"
    for sp in v:
        if not isinstance(sp, dict) or set(sp) - {"closed", "nodes"}:
            return "bad_value"
        if not isinstance(sp.get("closed"), bool):
            return "bad_value"
        nodes = sp.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return "bad_value"
        for n in nodes:
            if not isinstance(n, dict) or set(n) - {"p", "in", "out", "smooth"}:
                return "bad_value"
            if not _check_point(n.get("p"), lim):
                return "bad_value"
            for k in ("in", "out"):
                if k in n and not _check_point(n[k], lim):
                    return "bad_value"
            if "smooth" in n and n["smooth"] is not True:
                return "bad_value"
    return None


def _node_count(objs: list) -> int:
    total = 0
    for o in objs:
        if isinstance(o, dict) and isinstance(o.get("subpaths"), list):
            for sp in o["subpaths"]:
                if isinstance(sp, dict) and isinstance(sp.get("nodes"), list):
                    total += len(sp["nodes"])
    return total


def validate_new_objects(
    objs: object, *, taken_ids: set[str] | frozenset[str] = frozenset()
) -> dict:
    """校验一批 Agent 新增的对象 → `{"ok", "objects", "id_map", "group_map", "errors"}`。

    `taken_ids`：文档里已经有的全部对象 id 与组 id（含隐藏的蒙版、别的画布），新 id 不与它们撞。
    `ok` 为假时 `objects` 为空：整批不落地。错误只报码、序号与字段名，不回显输入内容。"""
    t = table()
    budgets = t["budgets"]
    errs = _Errors()
    if not isinstance(objs, list):
        errs.add("not_a_list")
        return _result(errs)
    if len(objs) > budgets["max_objects"]:
        errs.add("too_many")
        return _result(errs)
    if _node_count(objs) > budgets["max_nodes"]:
        errs.add("too_many", field="subpaths")
        return _result(errs)
    seen: set[str] = set()
    for i, o in enumerate(objs):
        if not isinstance(o, dict):
            errs.add("not_an_object", i)
            continue
        typ = o.get("type")
        fields = t["types"].get(typ) if isinstance(typ, str) else None
        if fields is None:
            errs.add("unknown_type", i, "type")
            continue
        spec = {**t["common"], **fields}
        for k, s in spec.items():
            if s.get("required") and k not in o:
                errs.add("missing_field", i, k)
        for k, v in o.items():
            if k in t["reserved"]:
                errs.add("reserved_field", i, k)
                continue
            s = spec.get(k)
            if s is None:
                errs.add("unknown_field", i, k)
                continue
            if s["kind"] == "type":
                continue
            if s["kind"] == "local_id":
                if not isinstance(v, str) or not v or len(v) > budgets["max_local_id_chars"]:
                    errs.add("bad_value", i, k)
                elif v in seen:
                    errs.add("duplicate_id", i, k)
                else:
                    seen.add(v)
                continue
            if s["kind"] == "group_path":
                ok = (
                    isinstance(v, list)
                    and 1 <= len(v) <= budgets["max_group_depth"]
                    and all(
                        isinstance(g, str) and g and len(g) <= budgets["max_local_id_chars"]
                        for g in v
                    )
                    and len(set(v)) == len(v)
                )
                if not ok:
                    errs.add("bad_group_path", i, k)
                continue
            code = _check_value(s, v, budgets)
            if code:
                errs.add(code, i, k)
    if errs.items:
        return _result(errs)
    return _mint(objs, set(taken_ids), t["id_prefix"])


def _mint(objs: list, taken: set[str], prefix: dict) -> dict:
    """局部 id / 组名 → 正式 id。组名按整条路径认（不同父组下同名的组是两个组）；路径按元组认，不拼字符串——
    组名里可以有 `/`，`['a/b']` 与 `['a', 'b']` 是两个组（Codex #2196）。`group_map` 回成
    `[{"path": [组名…], "id": 正式 id}]`。"""
    id_map: dict[str, str] = {}
    groups: dict[tuple, str] = {}
    out: list[dict] = []
    for o in objs:
        real = new_id(prefix[o["type"]], taken)
        id_map[o["id"]] = real
        n = {k: v for k, v in o.items() if k != "groupPath"}
        n["id"] = real
        path = o.get("groupPath")
        if path:
            chain: list[str] = []
            for depth in range(len(path)):
                key = tuple(path[: depth + 1])
                if key not in groups:
                    groups[key] = new_id(prefix["group"], taken)
                chain.append(groups[key])
            n["groupId"] = chain[-1]
            if len(chain) >= 2:  # 只有两层及以上才写 groupPath（`types/document.ObjectBase`）
                n["groupPath"] = chain
        out.append(n)
    group_map = [{"path": list(k), "id": v} for k, v in groups.items()]
    return {"ok": True, "objects": out, "id_map": id_map, "group_map": group_map, "errors": []}


def _result(errs: _Errors) -> dict:
    return {"ok": False, "objects": [], "id_map": {}, "group_map": [], "errors": errs.items}


# ------------------------------------------------------------------ 导出载荷


def _stroke_opacity(v: object) -> float | None:
    """与前端 `shapeGeometry.strokeOpacityOf` 同一条：缺省 / ≥1 / 非有限数 = 不发。"""
    if not _is_num(v) or v >= 1:  # type: ignore[operator]
        return None
    return max(0.0, float(v))  # type: ignore[arg-type]


def _truthy(v: object) -> bool:
    """JS 的 `||` / `&&` 真值（数值 0 / NaN、空串、None、False 为假）。"""
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0 and not math.isnan(v)
    if isinstance(v, str):
        return v != ""
    return True


def _appearance(o: dict) -> dict:
    out: dict[str, Any] = {}
    ml = geometry.miter_limit(o.get("miterLimit"))
    if ml != 10.0:
        out["miter_limit"] = ml
    dp = geometry.valid_dash_pattern(o.get("dashPattern"))
    if dp:
        out["dash_pattern"] = list(dp)
    align = o.get("strokeAlign")
    if o["type"] == "shape" and o.get("shape") not in geometry.STROKE_ONLY_SHAPES:
        if align in ("center", "outside"):
            out["stroke_align"] = align
    elif o["type"] == "path" and align in ("inside", "outside"):
        out["stroke_align"] = align
    return out


def _listy(v: Any) -> Any:
    """`read_gradient` 回元组；载荷是 JSON，与前端 `readGradient` 的数组同形。"""
    if isinstance(v, (list, tuple)):
        return [_listy(x) for x in v]
    if isinstance(v, dict):
        return {k: _listy(x) for k, x in v.items()}
    return v


def _gradients(o: dict, stroke_on: bool) -> dict:
    out: dict[str, Any] = {}
    f = geometry.read_gradient(o.get("fillGradient")) if _truthy(o.get("fill")) else None
    st = geometry.read_gradient(o.get("strokeGradient")) if stroke_on else None
    if f:
        out["fill_gradient"] = _listy(f)
    if st:
        out["stroke_gradient"] = _listy(st)
    return out


def _put(d: dict, k: str, v: object) -> None:
    """JS 序列化丢掉 undefined：这里 None 就不写（`null` 要写的字段如 `fill` 直接赋值，不走这里）。"""
    if v is not None:
        d[k] = v


def _rot(o: dict) -> float | None:
    v = o.get("rotationDeg")
    return v if _truthy(v) else None


def to_export_object(o: dict) -> dict:
    """一个校验过的 Agent 对象 → 导出载荷；与 `exportPayload.toExportObject` 对 text / arrow / shape / path 逐字段相同。"""
    e: dict[str, Any] = {"x_mm": o["x"], "y_mm": o["y"], "w_mm": o["w"], "h_mm": o["h"]}
    typ = o["type"]
    if typ == "text":
        e.update(
            type="text",
            text=o["text"],
            size_pt=o["sizePt"],
            bold=o["bold"],
            italic=o.get("italic") is True,
            color=o["color"],
            align=o["align"],
        )
        _put(e, "font_family", o.get("fontFamily"))
        _put(e, "interpretation", o.get("interpretation"))
        _put(e, "text_format", o.get("textFormat"))
        _put(e, "underline", True if _truthy(o.get("underline")) else None)
        _put(e, "line_height", o.get("lineHeight"))
        _put(e, "padding_mm", o.get("padding") if _truthy(o.get("padding")) else None)
        _put(e, "bg", o.get("bg") if _truthy(o.get("bg")) else None)
        bc = o.get("borderColor")
        _put(e, "border_color", bc if _truthy(bc) else None)
        bp = o.get("borderPt")
        _put(e, "border_pt", (0.75 if bp is None else bp) if _truthy(bc) else None)
        _put(e, "rotation_deg", _rot(o))
        return e
    if typ == "arrow":
        e.update(
            type="arrow",
            start=o["start"],
            end=o["end"],
            stroke_pt=o["strokePt"],
            color=o["color"],
            head=o["head"],
        )
        _put(e, "head_start", o.get("headStart"))
        _put(e, "head_end", o.get("headEnd"))
        _head_sizes(e, o)
        _put(e, "dash", o.get("dash") if o.get("dash") not in (None, "solid") else None)
        _put(e, "stroke_opacity", _stroke_opacity(o.get("strokeOpacity")))
        _put(e, "line_cap", _not_default(o.get("lineCap"), geometry.DEFAULT_LINE_CAP))
        _put(e, "line_join", _not_default(o.get("lineJoin"), "round"))
        e.update(_appearance(o))
        _put(e, "rotation_deg", _rot(o))
        return e
    if typ == "shape":
        kind = o["shape"]
        stroke_on = kind in geometry.STROKE_ONLY_SHAPES or o.get("stroke") is not False
        e.update(type="shape", shape=kind)
        if kind == "line":
            _put(e, "start", o.get("start"))
            _put(e, "end", o.get("end"))
        e.update(stroke_pt=o["strokePt"], color=o["color"], fill=o["fill"])
        _put(
            e, "corner_radius_mm", o.get("cornerRadius") if _truthy(o.get("cornerRadius")) else None
        )
        sides = o.get("sides")
        if kind == "polygon":
            e["sides"] = 6 if sides is None else sides
        elif kind == "star":
            e["sides"] = geometry.DEFAULT_STAR_POINTS if sides is None else sides
        ratio = o.get("starRatio")
        if kind == "star" and ratio is not None and ratio != geometry.DEFAULT_STAR_RATIO:
            e["star_ratio"] = ratio
        _fill_opacity(e, o)
        _put(e, "dash", o.get("dash") if o.get("dash") not in (None, "solid") else None)
        _put(e, "stroke", None if stroke_on else False)
        _put(e, "stroke_opacity", _stroke_opacity(o.get("strokeOpacity")) if stroke_on else None)
        _put(e, "line_cap", _not_default(o.get("lineCap"), geometry.DEFAULT_LINE_CAP))
        _put(e, "line_join", _not_default(o.get("lineJoin"), geometry.default_line_join(kind)))
        e.update(_appearance(o))
        e.update(_gradients(o, stroke_on))
        _put(e, "rotation_deg", _rot(o))
        return e
    if typ == "path":
        stroke_on = o.get("stroke") is not False
        e.update(
            type="path",
            subpaths=[
                {
                    "closed": sp["closed"],
                    "nodes": [
                        {"p": n["p"], **{k: n[k] for k in ("in", "out") if n.get(k)}}
                        for n in sp["nodes"]
                    ],
                }
                for sp in o["subpaths"]
            ],
        )
        _put(e, "fill_rule", "evenodd" if o.get("fillRule") == "evenodd" else None)
        e.update(stroke_pt=o["strokePt"], color=o["color"], fill=o["fill"])
        _fill_opacity(e, o)
        _put(e, "dash", o.get("dash") if o.get("dash") not in (None, "solid") else None)
        _put(e, "stroke", None if stroke_on else False)
        _put(e, "stroke_opacity", _stroke_opacity(o.get("strokeOpacity")) if stroke_on else None)
        _put(e, "line_cap", _not_default(o.get("lineCap"), geometry.DEFAULT_LINE_CAP))
        _put(e, "line_join", _not_default(o.get("lineJoin"), geometry.PATH_DEFAULT_JOIN))
        e.update(_appearance(o))
        e.update(_gradients(o, stroke_on))
        _put(e, "head_start", _not_default(o.get("headStart"), "none"))
        _put(e, "head_end", _not_default(o.get("headEnd"), "none"))
        _head_sizes(e, o)
        _put(e, "rotation_deg", _rot(o))
        return e
    raise ValueError(f"not an agent object type: {typ!r}")


def _not_default(v: object, default: str) -> object:
    return v if _truthy(v) and v != default else None


def _head_sizes(e: dict, o: dict) -> None:
    _put(e, "head_size_start", _not_default(o.get("headSizeStart"), "medium"))
    _put(e, "head_size_end", _not_default(o.get("headSizeEnd"), "medium"))


def _fill_opacity(e: dict, o: dict) -> None:
    fo = o.get("fillOpacity")
    if _truthy(o.get("fill")) and fo is not None and fo < 1:
        e["fill_opacity"] = fo


def to_export_objects(objs: list[dict]) -> list[dict]:
    """顺序即 z 序；隐藏的不发（Agent 的对象不会是隐藏的，但与前端同一条契约）。"""
    return [to_export_object(o) for o in objs if not o.get("hidden")]
