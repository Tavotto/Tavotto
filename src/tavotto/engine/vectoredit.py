"""Agent 改画布上已有的矢量对象（ADR 0212 第 1 期）：选择器 + 补丁式写入（改字段、挪位置、删除）。

与 0208 的「加」同一条路：读排版 → 方案（服务端算出改后的对象、核对、**复述改了谁、从什么改成什么**）→ 写入（带回摘要、
锁里再核修订号、先存撤销副本）→ 一步撤销。写入与撤销共用 `layoutannotate.write_canvas` / `undo`，锁与冲突判据只有一份。

**能改什么**（维护者 2026-11-25 定）：画布上全部矢量对象（文字 / 箭头 / 形状 / 路径），含用户手画的；子图、图片与其它类型不在此列，
图内数据元素走 `apply_overrides`。

补丁是一列操作，每条只点名一个对象：

* `{"op": "set", "id", "set": {字段: 值}}`：字段只认字段表（`profiles/agent_objects.json`）里这一类的键 + 几何（x / y / w / h /
  rotationDeg）+ 名字 + 锁定 / 隐藏；值照字段表的上下限校验。不认识的键、越界的值整批拒；
* `{"op": "move", "id", "by_mm": [dx, dy]}`，或 `{"op": "move", "id", "to": 位置, "attach": 方位点 | "from" | "to"}`：
  位置写法与 0208 相同（锚点 + 偏移，数据点从导出实测）；箭头的 `attach: "to"` = 把箭头尖对到那里（整支平移，不改形）；
* `{"op": "delete", "id"}`；
* 第 2 期「排」（与前端同一语义，见各函数注释）：`align`（对齐到选区 / 关键对象 / 子图 / 页面，按看得见的框）、`distribute`
  （等间距 / 按间距排）、`order`（前后层级）、`group` / `ungroup`（编组一层）、`rotate`（设绝对角度，绕框中心）。
* 第 3 期「几何」（内核是前端同一份 vecops.wasm，经 `engine/vecops.py`；语义在 `engine/vectorgeom.py`，照 `lib/pathfinder.ts`）：
  `pathfinder`（`ids` + `mode`：合并 / 减去顶层 / 相交 / 排除重叠 / 分割 / 修边…）、`offset`（旁边多一个偏移后的新路径）、
  `outline_stroke`（描边变成填充轮廓）、`simplify`（在容差内删多余锚点）。目前只收 `path`（形状要先有「转为路径」的 Python 版）；
  内核不可用 = `kernel_unavailable`，其余操作照常。

核对只看这次动到的对象（改后的样子），画布上没动的文字当障碍；用户早就接受的地方不重新报。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from . import (
    agentobjects,
    doclock,
    layoutannotate as la,
    vecops,
    vectorcheck,
    vectorgeom,
    vectorscene,
)

#: 能改的对象类型（矢量对象）
EDITABLE_TYPES = ("text", "arrow", "shape", "path")
#: 字段表之外、编辑时额外允许的键（新建时不给 Agent，改已有对象时可以）
EDIT_ONLY = {"locked": {"kind": "bool"}, "hidden": {"kind": "bool"}}
#: 几何与名字（字段表 common 里除 id / type / groupPath 之外的键；编组走 `group` / `ungroup`）
GEOMETRY = ("x", "y", "w", "h", "rotationDeg", "name")

ERROR_CODES = (
    "bad_op",
    "unknown_object",
    "not_vector",
    "bad_field",
    "bad_value",
    "out_of_range",
    "duplicate_target",
    "kernel_unavailable",
)

_KIND_WORD = {"text": "文字", "arrow": "箭头", "shape": "形状", "path": "路径"}


class EditError(Exception):
    def __init__(self, code: str, index: int | None = None, field: str | None = None) -> None:
        assert code in ERROR_CODES, code
        super().__init__(code)
        self.code, self.index, self.field = code, index, field

    def as_dict(self) -> dict:
        out: dict = {"code": self.code}
        if self.index is not None:
            out["index"] = self.index
        if self.field is not None:
            out["field"] = self.field
        return out


# ------------------------------------------------------------------ 选


def _box(o: dict) -> list[float]:
    return [float(o.get("x", 0)), float(o.get("y", 0)), float(o.get("w", 0)), float(o.get("h", 0))]


def _brief(o: dict) -> dict:
    return {
        "id": o.get("id"),
        "type": o.get("type"),
        "name": o.get("name"),
        "text": o.get("text") if o.get("type") == "text" else None,
        "box_mm": _box(o),
        "by_agent": (o.get("anchor") or {}).get("by") == "agent",
        "locked": bool(o.get("locked")),
        "hidden": bool(o.get("hidden")),
        "group": (o.get("groupPath") or ([o["groupId"]] if o.get("groupId") else None)),
    }


_QUERY_KEYS = ("ids", "types", "name", "text", "by_agent", "within", "anchored_to")


def check_query(query: object) -> dict:
    """选择条件的形状：不认识的键、写错类型的值抛 `EditError("bad_op", field=键)`（不悄悄当成「全选」）。"""
    if query is None:
        return {}
    if not isinstance(query, dict):
        raise EditError("bad_op", field="select")
    for k, v in query.items():
        ok = {
            "ids": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
            "types": lambda v: isinstance(v, list) and all(x in EDITABLE_TYPES for x in v),
            "name": lambda v: isinstance(v, str),
            "text": lambda v: isinstance(v, str),
            "by_agent": lambda v: isinstance(v, bool),
            "within": lambda v: (
                isinstance(v, list) and len(v) == 4 and all(agentobjects._is_num(x) for x in v)
            ),  # noqa: SLF001
            "anchored_to": lambda v: isinstance(v, str),
        }.get(k)
        if ok is None or not ok(v):
            raise EditError("bad_op", field=f"select.{k}")
    return dict(query)


def select(canvas: dict, query: dict | None = None) -> list[dict]:
    """画布上的矢量对象，按条件筛（条件之间是「且」）：`ids`、`types`、`name`（包含）、`text`（文字内容包含）、
    `by_agent`、`within`（画布毫米框 `[x, y, w, h]`，与对象框相交）、`anchored_to`（0208 锚点里指着的子图对象 id）。
    回每个对象的简述（id、类型、名字、文字、毫米框、是不是 Agent 加的、锁定 / 隐藏、所在组）。"""
    q = query or {}
    out = []
    for o in canvas.get("objects") or []:
        if not isinstance(o, dict) or o.get("type") not in EDITABLE_TYPES:
            continue
        if "ids" in q and o.get("id") not in q["ids"]:
            continue
        if "types" in q and o.get("type") not in q["types"]:
            continue
        if "name" in q and q["name"] not in str(o.get("name") or ""):
            continue
        if "text" in q and q["text"] not in str(o.get("text") or ""):
            continue
        if "by_agent" in q and ((o.get("anchor") or {}).get("by") == "agent") != bool(
            q["by_agent"]
        ):
            continue
        if "within" in q:
            x, y, w, h = (float(v) for v in q["within"])
            bx, by, bw, bh = _box(o)
            if bx > x + w or bx + bw < x or by > y + h or by + bh < y:
                continue
        if "anchored_to" in q:
            refs = {
                (a.get("data") or a.get("element") or {}).get("panel")
                or (a.get("panel") or {}).get("id")
                for a in (o.get("anchor") or {}).get("anchors") or []
            }
            if q["anchored_to"] not in refs:
                continue
        out.append(_brief(o))
    return out


# ------------------------------------------------------------------ 改


def _spec_for(o: dict, field: str) -> dict | None:
    t = agentobjects.table()
    if field in EDIT_ONLY:
        return EDIT_ONLY[field]
    if field in GEOMETRY:
        return t["common"].get(field)
    return t["types"].get(o.get("type"), {}).get(field)


def _point_of(o: dict, attach: str) -> tuple[float, float]:
    """对象上的一个点**画出来**在哪：框里的位置再绕框中心顺时针转 `rotationDeg`（与导出 `plan._object_transform`
    同一个方向）——转过的箭头按没转的框算尖端，挪完看得见的尖端会落在别处（Codex #2220）。"""
    x, y, w, h = _box(o)
    if o.get("type") == "arrow" and attach in ("from", "to"):
        p = o.get("start" if attach == "from" else "end") or {"rx": 0.0, "ry": 0.0}
        px, py = x + float(p["rx"]) * w, y + float(p["ry"]) * h
    elif attach in vectorscene.POINTS:
        fx, fy = vectorscene.POINTS[attach]
        px, py = x + fx * w, y + fy * h
    else:
        raise EditError("bad_op", field="attach")
    deg = float(o.get("rotationDeg") or 0.0)
    if not deg:
        return (px, py)
    cx, cy = x + w / 2, y + h / 2
    c, sn = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (cx + (px - cx) * c - (py - cy) * sn, cy + (px - cx) * sn + (py - cy) * c)


def _label(o: dict) -> str:
    who = o.get("name") or (f"「{o.get('text')}」" if o.get("type") == "text" else None)
    kind = _KIND_WORD.get(o.get("type"), o.get("type"))
    x, y, w, h = _box(o)
    return f"{kind}{who or ''}（{o.get('id')}，约在 {x + w / 2:.0f}, {y + h / 2:.0f} mm）"


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.3g}"
    if isinstance(v, (dict, list)):
        return "…"
    return str(v)


# ------------------------------------------------------------------ 排（第 2 期）：几何 / 组链的纯函数，与前端同一语义

_ALIGN_MODES = ("left", "hcenter", "right", "top", "vcenter", "bottom")
#: 模式 → (动词, 参照线名, 轴, 参照线在参照框里的位置系数)
_ALIGN_WORDS = {
    "left": ("左对齐", "左边", "x", 0.0),
    "hcenter": ("水平居中对齐", "水平中线", "x", 0.5),
    "right": ("右对齐", "右边", "x", 1.0),
    "top": ("顶对齐", "上边", "y", 0.0),
    "vcenter": ("垂直居中对齐", "垂直中线", "y", 0.5),
    "bottom": ("底对齐", "下边", "y", 1.0),
}
_ORDER_WORDS = {
    "front": "移到最上层",
    "back": "移到最下层",
    "forward": "上移一层",
    "backward": "下移一层",
}
_NDIGITS = 6

Rect4 = tuple[float, float, float, float]


def _vb(o: dict) -> Rect4:
    """看得见的外接框：照 `web/src/lib/geometry.visualBounds`（旋转过的对象绕框中心转出来再取外接矩形）。"""
    x, y, w, h = _box(o)
    deg = o.get("rotationDeg") or 0
    if not deg:
        return (x, y, w, h)
    rad = float(deg) * math.pi / 180
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    vw, vh = w * c + h * s, w * s + h * c
    return (x + w / 2 - vw / 2, y + h / 2 - vh / 2, vw, vh)


def _bounds(rects: list[Rect4]) -> Rect4:
    x = min(r[0] for r in rects)
    y = min(r[1] for r in rects)
    x2 = max(r[0] + r[2] for r in rects)
    y2 = max(r[1] + r[3] for r in rects)
    return (x, y, x2 - x, y2 - y)


def _gpath(o: dict) -> list[str]:
    """对象的组链（外 → 内）：照 `lib/groupTree.groupPathOf`（链与 groupId 对不上就只认 `[groupId]`）。"""
    g = o.get("groupId")
    if not g:
        return []
    p = o.get("groupPath")
    if (
        isinstance(p, list)
        and len(p) >= 2
        and p[-1] == g
        and all(isinstance(v, str) and v for v in p)
        and len(set(p)) == len(p)
    ):
        return list(p)
    return [g]


def _unit(o: dict) -> tuple[str, str]:
    """对象在画布顶层（不在组内编辑）的单元：最外层组，或它自己。"""
    p = _gpath(o)
    return ("g", p[0]) if p else ("o", str(o.get("id")))


def _normalize_deg(deg: float) -> float:
    """照 `geometry.normalizeRotationDeg`：保留 0.1°，规范到 (-180, 180]，0 → 0。"""
    r = math.floor(deg * 10 + 0.5) / 10
    q = ((r % 360) + 360) % 360
    if q > 180:
        q -= 360
    return 0.0 if q == 0 else round(q, 1)


class _State:
    """一次补丁在对象列表副本上的工作态。"""

    def __init__(self, objects: list[dict], panels: dict | None, page: dict | None) -> None:
        self.out = copy.deepcopy(objects)
        self.panels = panels or {}
        self.page = page
        self.deleted: list[str] = []
        self.touched: set[str] = set()
        self.changes: list[dict] = []
        self.echo: list[str] = []
        self.budgets = agentobjects.table()["budgets"]
        self.minted: set[str] = set()
        #: 这次补丁拆掉的组 id（写入层按它清布局组约束，#2244）
        self.ungrouped: set[str] = set()
        self.reindex()

    def reindex(self) -> None:
        self.index = {o.get("id"): i for i, o in enumerate(self.out) if isinstance(o, dict)}

    def live(self) -> list[dict]:
        return [o for o in self.out if isinstance(o, dict) and o.get("id") not in self.deleted]

    def set_order(self, live: list[dict]) -> None:
        gone = [o for o in self.out if not isinstance(o, dict) or o.get("id") in self.deleted]
        self.out = [*live, *gone]
        self.reindex()

    def set_field(self, o: dict, k: str, v: object) -> None:
        """写一个字段并记前后值；`v` 为 None = 去掉这个键（与前端落盘形态一致：缺省不写）。"""
        before = o.get(k)
        if before == v:
            return
        if v is None:
            o.pop(k, None)
        else:
            o[k] = v
        self.changes.append({"id": o["id"], "field": k, "before": before, "after": v})
        self.touched.add(o["id"])

    def set_path(self, o: dict, path: list[str]) -> None:
        """照 `groupTree.setGroupPath`：空 = 不在组里；一层只写 groupId，两层及以上才写 groupPath。"""
        self.set_field(o, "groupId", path[-1] if path else None)
        self.set_field(o, "groupPath", list(path) if len(path) >= 2 else None)

    def shift(self, o: dict, dx: float, dy: float, i: int) -> None:
        for k, d in (("x", dx), ("y", dy)):
            if abs(d) < 1e-9:
                continue
            v = round(float(o.get(k, 0)) + d, _NDIGITS)
            spec = agentobjects.table()["common"][k]
            if agentobjects._check_value(spec, v, self.budgets) is not None:  # noqa: SLF001
                raise EditError("out_of_range", i, k)
            self.set_field(o, k, v)


def _pick(s: _State, i: int, ids: object, field: str, *, strict: bool) -> list[dict]:
    """点名的对象 + 它们顶层单元里的全部成员（前端选中成组对象 = 整组，`expandGroups`），按文档顺序。
    `strict`：组里有非矢量对象（子图 / 图片）就整批拒，因为这一步会改到它们。"""
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(v, str) for v in ids)
        or len(set(ids)) != len(ids)
    ):
        raise EditError("bad_op", i, field)
    for v in ids:
        if v not in s.index or v in s.deleted:
            raise EditError("unknown_object", i, field)
        if s.out[s.index[v]].get("type") not in EDITABLE_TYPES:
            raise EditError("not_vector", i, field)
    live = s.live()
    units = {_unit(o) for o in live if o.get("id") in ids}
    members = [o for o in live if _unit(o) in units]
    if strict and any(o.get("type") not in EDITABLE_TYPES for o in members):
        raise EditError("not_vector", i, "group_member")
    return members


def _movable(s: _State, members: list[dict]) -> list[dict]:
    """照 `actions.movableTargets`：锁定的对象不动；组里只要有一个锁定，整组不动。"""
    locked_units = {_unit(o) for o in s.live() if o.get("locked") and _gpath(o)}
    return [o for o in members if not o.get("locked") and _unit(o) not in locked_units]


def _op_align(s: _State, i: int, op: dict) -> None:
    """`actions.alignSelectedTo` + `geometry.alignOneVisual`：量看得见的框，只平移 x / y；锁定的不动但算进参照框。"""
    if set(op) - {"op", "ids", "mode", "to"}:
        raise EditError("bad_op", i)
    mode = op.get("mode")
    if mode not in _ALIGN_MODES:
        raise EditError("bad_op", i, "mode")
    members = _pick(s, i, op.get("ids"), "ids", strict=True)
    to = op.get("to", "selection")
    key: dict | None = None
    if to == "selection":
        box, ref = _bounds([_vb(o) for o in members]), "选区"
    elif to == "page":
        pg = s.page or {}
        if not (pg.get("w", 0) > 0 and pg.get("h", 0) > 0):
            raise EditError("bad_op", i, "to")
        box, ref = (0.0, 0.0, float(pg["w"]), float(pg["h"])), "页面"
    elif isinstance(to, dict) and set(to) == {"key"}:
        key = next((o for o in members if o.get("id") == to["key"]), None)
        if key is None:  # 关键对象必须是点名的对象之一（前端的 primary 就在选区里）
            raise EditError("bad_op", i, "to")
        box, ref = _vb(key), f"关键对象{_short(key)}"
    elif isinstance(to, dict) and set(to) == {"panel"}:
        entry = s.panels.get(to["panel"]) if isinstance(to["panel"], str) else None
        if not entry:
            raise EditError("unknown_object", i, "to")
        box, ref = _box(entry["panel"]), f"子图 {to['panel']}"
    else:
        raise EditError("bad_op", i, "to")
    # 以关键对象为参照时它自己不动
    others = [o for o in members if o is not key]
    movable = [o for o in _movable(s, members) if o is not key]
    if not movable:
        raise EditError("bad_op", i, "locked" if others else "ids")
    for o in movable:
        vx, vy, vw, vh = _vb(o)
        dx = dy = 0.0
        if mode == "left":
            dx = box[0] - vx
        elif mode == "right":
            dx = box[0] + box[2] - vw - vx
        elif mode == "hcenter":
            dx = box[0] + (box[2] - vw) / 2 - vx
        elif mode == "top":
            dy = box[1] - vy
        elif mode == "bottom":
            dy = box[1] + box[3] - vh - vy
        else:
            dy = box[1] + (box[3] - vh) / 2 - vy
        s.shift(o, dx, dy, i)
    verb, edge, axis, frac = _ALIGN_WORDS[mode]
    line = (box[0] + frac * box[2]) if axis == "x" else (box[1] + frac * box[3])
    note = f"；{len(others) - len(movable)} 个锁定的没动" if len(others) > len(movable) else ""
    sep = " " if ref[-1].isascii() else ""
    s.echo.append(f"{verb} {len(movable)} 个对象到{ref}{sep}{edge}（{axis} = {line:.1f} mm）{note}")


def _short(o: dict) -> str:
    who = o.get("name") or (f"「{o.get('text')}」" if o.get("type") == "text" else None)
    return f"{_KIND_WORD.get(o.get('type'), o.get('type'))}{who or ''}"


def _op_distribute(s: _State, i: int, op: dict) -> None:
    """等间距 = `geometry.applyAlign` 的 hdist / vdist（两端不动，参照框是选区外接框）；
    给 `gap_mm` = `actions.setSelectionSpacing`（最左 / 最上的不动，其余依次贴上去）。量的都是看得见的框。"""
    if set(op) - {"op", "ids", "axis", "gap_mm"}:
        raise EditError("bad_op", i)
    axis = op.get("axis")
    if axis not in ("h", "v"):
        raise EditError("bad_op", i, "axis")
    members = _pick(s, i, op.get("ids"), "ids", strict=True)
    has_gap = "gap_mm" in op
    if len(op["ids"]) < (2 if has_gap else 3):
        raise EditError("bad_op", i, "ids")
    k, sz = (0, 2) if axis == "h" else (1, 3)
    kn = "x" if axis == "h" else "y"
    aw = "水平" if axis == "h" else "垂直"
    if has_gap:
        gap = op["gap_mm"]
        if not agentobjects._is_num(gap):  # noqa: SLF001
            raise EditError("bad_value", i, "gap_mm")
        gap = float(gap)
        if abs(gap) > s.budgets["max_coord_mm"]:
            raise EditError("out_of_range", i, "gap_mm")
        # 偏离前端：前端的精确间距不看锁定；Agent 这边有锁定成员就整批拒，不替用户挪锁住的东西
        if len(_movable(s, members)) != len(members):
            raise EditError("bad_op", i, "locked")
        ordered = sorted(members, key=lambda o: _vb(o)[k])
        cur = _vb(ordered[0])[k]
        for o in ordered:
            vb = _vb(o)
            s.shift(o, *((cur - vb[k], 0.0) if k == 0 else (0.0, cur - vb[k])), i)
            cur += vb[sz] + gap
        s.echo.append(
            f"{aw}按 {gap:.1f} mm 间距排 {len(members)} 个对象（最{'左' if k == 0 else '上'}的不动）"
        )
        return
    box = _bounds([_vb(o) for o in members])
    targets = _movable(s, members)
    if len(targets) < 2:
        raise EditError("bad_op", i, "locked")
    ordered = sorted(targets, key=lambda o: _vb(o)[k])
    total = sum(_vb(o)[sz] for o in ordered)
    gap = (box[sz] - total) / (len(ordered) - 1)
    cur = box[k]
    for o in ordered:
        vb = _vb(o)
        s.shift(o, *((cur - vb[k], 0.0) if k == 0 else (0.0, cur - vb[k])), i)
        cur += vb[sz] + gap
    s.echo.append(f"{aw}等间距分布 {len(ordered)} 个对象（间距 {gap:.1f} mm，{kn} 方向两端不动）")


def _op_order(s: _State, i: int, op: dict, o: dict) -> None:
    """`actions.changeZOrder`：选中成组对象 = 整组一起动（组内非矢量成员也跟着，但不算「改了」）。"""
    if set(op) - {"op", "id", "to"}:
        raise EditError("bad_op", i)
    to = op.get("to")
    if to not in _ORDER_WORDS:
        raise EditError("bad_op", i, "to")
    members = _pick(s, i, [o["id"]], "id", strict=False)
    ids = {m["id"] for m in members}
    live = s.live()
    before = {m["id"]: n for n, m in enumerate(live)}
    picked = [m for m in live if m["id"] in ids]
    rest = [m for m in live if m["id"] not in ids]
    if to == "front":
        new = rest + picked
    elif to == "back":
        new = picked + rest
    else:  # 与前端同一个单步：碰到同批的邻居就不动（避免互相挤压）
        step = 1 if to == "forward" else -1
        new = list(live)
        for m in reversed(live) if to == "forward" else live:
            if m["id"] not in ids:
                continue
            at = new.index(m)
            j = at + step
            if j < 0 or j >= len(new) or new[j]["id"] in ids:
                continue
            new.pop(at)
            new.insert(j, m)
    s.set_order(new)
    for n, m in enumerate(new):
        if before[m["id"]] != n and m.get("type") in EDITABLE_TYPES:
            s.changes.append({"id": m["id"], "field": "z", "before": before[m["id"]], "after": n})
            s.touched.add(m["id"])
    mates = f"（连同同组的 {len(members) - 1} 个）" if len(members) > 1 else ""
    s.echo.append(f"把 {_label(o)} {_ORDER_WORDS[to]}{mates}")


def _derive_gid(s: _State, i: int, member_ids: list[str]) -> str:
    """新组 id：格式与 `agentobjects.new_id("g", …)` 相同（`g_` + 8 位 `[a-z0-9]`），但由内容确定地派生——
    方案与写入各算一遍，摘要里的东西必须逐位一致，随机 id 会让「摘要对得上才写」永远对不上。"""
    taken = {o.get("id") for o in s.out if isinstance(o, dict)}
    taken |= {g for o in s.out if isinstance(o, dict) for g in _gpath(o)}
    seed = json.dumps([i, sorted(member_ids)], ensure_ascii=False)
    n = 0
    while True:
        h = hashlib.sha256(f"{seed}|{n}".encode()).digest()
        cand = "g_" + "".join(agentobjects._ID_ALPHABET[b % 36] for b in h[:8])  # noqa: SLF001
        if cand not in taken:
            return cand
        n += 1


def _op_group(s: _State, i: int, op: dict) -> None:
    """`actions.groupUnits`：选中的单元（对象或整组）收进一个新组，挂在顶层；成员挪成连续的一段、落在最上面那个的位置。"""
    if set(op) - {"op", "ids", "name"}:
        raise EditError("bad_op", i)
    name = op.get("name")
    if name is not None and (
        not isinstance(name, str) or not name or len(name) > s.budgets["max_name_chars"]
    ):
        raise EditError("bad_value", i, "name")
    members = _pick(s, i, op.get("ids"), "ids", strict=True)
    if len({_unit(o) for o in members}) < 2:
        raise EditError("bad_op", i, "ids")
    gid = _derive_gid(s, i, [m["id"] for m in members])
    ids = {m["id"] for m in members}
    new_paths = {m["id"]: [gid, *_gpath(m)] for m in members}
    if any(len(p) > s.budgets["max_group_depth"] for p in new_paths.values()):
        raise EditError("out_of_range", i, "groupPath")
    for m in members:
        s.set_path(m, new_paths[m["id"]])
    live = s.live()
    pos = [n for n, m in enumerate(live) if m["id"] in ids]
    if pos[-1] - pos[0] != len(pos) - 1:  # 不连续：挪成一段，落在最上面那个的位置
        rest = [m for m in live if m["id"] not in ids]
        below = pos[-1] + 1 - len(pos)
        live = [*rest[:below], *[m for m in live if m["id"] in ids], *rest[below:]]
        s.set_order(live)
    tail = f"：组名 {name}" if name else ""
    s.echo.append(f"编组 {len(members)} 个对象{tail}")


def _release_mask(s: _State, o: dict) -> None:
    for k in ("clipMask", "clipOff", "hidden"):
        s.set_field(o, k, None)


def _op_ungroup(s: _State, i: int, op: dict) -> None:
    """`actions.ungroupSelected`：只拆一层，成员（对象与子组）升到原来所在的那一层；拆剪切组时蒙版还原成普通可见对象。"""
    if set(op) - {"op", "group"}:
        raise EditError("bad_op", i)
    gid = op.get("group")
    if not isinstance(gid, str) or not gid:
        raise EditError("bad_op", i, "group")
    live = s.live()
    members = [o for o in live if gid in _gpath(o)]
    if not members:
        raise EditError("unknown_object", i, "group")
    for o in members:
        p = _gpath(o)
        if o.get("clipMask") and p[-1] == gid:
            _release_mask(s, o)
        s.set_path(o, [g for g in p if g != gid])
    # `releaseOrphanMasks`：剪切组里已经没有被裁的对象了，蒙版不再藏着；组里只剩它自己就连这一层组也拿掉
    for o in live:
        if not o.get("clipMask"):
            continue
        p = _gpath(o)
        mates = (
            [x for x in live if x is not o and not x.get("clipMask") and p[-1] in _gpath(x)]
            if p
            else []
        )
        if not mates:
            _release_mask(s, o)
            if p and not any(x is not o and p[-1] in _gpath(x) for x in live):
                s.set_path(o, p[:-1])
    s.ungrouped.add(gid)
    s.echo.append(f"取消编组 {gid}（{len(members)} 个对象升到上一层）")


def _op_rotate(s: _State, i: int, op: dict, o: dict) -> None:
    """设绝对角度（`rotationDeg`，顺时针为正，绕框中心；x / y / w / h 不变）。落盘形态照 `normalizeRotationDeg`：
    保留 0.1°、规范到 (-180, 180]、0 = 不写这个字段。"""
    if set(op) - {"op", "id", "deg"} or "deg" not in op:
        raise EditError("bad_op", i)
    deg = op["deg"]
    if not agentobjects._is_num(deg):  # noqa: SLF001
        raise EditError("bad_value", i, "deg")
    err = agentobjects._check_value(  # noqa: SLF001
        agentobjects.table()["common"]["rotationDeg"], deg, s.budgets
    )
    if err is not None:
        raise EditError("out_of_range" if err == "out_of_range" else "bad_value", i, "deg")
    after = _normalize_deg(float(deg))
    before = float(o.get("rotationDeg") or 0.0)
    if after != before:
        s.set_field(o, "rotationDeg", after if after != 0 else None)
        s.changes[-1]["after"] = after  # 0 度不落盘，但复述里照实说转到 0
    s.echo.append(f"{_label(o)}：转到 {after:g}°（原 {before:g}°）")


# ------------------------------------------------------------------ 几何（第 3 期）：内核是前端同一份 wasm，语义照 `lib/pathfinder.ts`

_MODE_WORDS = {
    "unite": "合并",
    "minus_front": "减去顶层",
    "intersect": "相交",
    "exclude": "排除重叠",
    "divide": "分割",
    "trim": "修边",
    "merge": "合并同色",
    "crop": "裁剪",
    "minus_back": "减去后方",
}
_JOINS = ("miter", "round", "bevel")


def _kernel(i: int):
    """内核不可用（wasmtime 装不上 / 找不到 .wasm / ABI 对不上）= 这一类操作报错，其余操作照常。"""
    if not vecops.available():
        raise EditError("kernel_unavailable", i)
    return vecops


def _mint(s: _State, i: int, tag: str, seed: list[str], n: int) -> str:
    """新路径 id：`p_` + 8 位 `[a-z0-9]`，由内容确定地派生（方案与写入各算一遍，摘要里的东西必须逐位一致）。"""
    taken = {o.get("id") for o in s.out if isinstance(o, dict)} | s.minted
    k = 0
    while True:
        h = hashlib.sha256(
            json.dumps([i, tag, seed, n, k], ensure_ascii=False).encode("utf-8")
        ).digest()
        cand = "p_" + "".join(agentobjects._ID_ALPHABET[b % 36] for b in h[:8])  # noqa: SLF001
        if cand not in taken:
            s.minted.add(cand)
            return cand
        k += 1


def _operand(s: _State, i: int, oid: object, field: str) -> dict:
    """几何操作的操作数：未锁定、未隐藏的 `path`（形状要先「转为路径」，Python 版还没有 → `needs_path`）。"""
    if not isinstance(oid, str) or oid not in s.index or oid in s.deleted:
        raise EditError("unknown_object", i, field)
    o = s.out[s.index[oid]]
    if o.get("type") not in EDITABLE_TYPES or o.get("type") == "text":
        raise EditError("not_vector", i, field)
    if o.get("type") != "path":
        raise EditError("bad_op", i, "needs_path")
    if o.get("locked"):
        raise EditError("bad_op", i, "locked")
    if o.get("hidden"):
        raise EditError("bad_op", i, "hidden")
    return o


def _count_check(s: _State, i: int) -> None:
    if len(s.live()) > s.budgets["max_objects"]:
        raise EditError("out_of_range", i, "objects")


def _op_pathfinder(s: _State, i: int, op: dict) -> None:
    """`pathfinder.runPathfinder` + `pathfinderActions.applyPlan`：选中的路径按 `mode` 运算；原对象全删，结果放在**最上面那个**
    原对象原来的数组位置（结果之间按内核给的从后往前排）；每个原对象的样式第一次被沿用时，那块结果继承它的 id 与名字。"""
    if set(op) - {"op", "ids", "mode"}:
        raise EditError("bad_op", i)
    mode = op.get("mode")
    if mode not in vectorgeom.PATHFINDER_MODES:
        raise EditError("bad_op", i, "mode")
    ids = op.get("ids")
    if (
        not isinstance(ids, list)
        or not all(isinstance(v, str) for v in ids)
        or len(set(ids)) != len(ids)
    ):
        raise EditError("bad_op", i, "ids")
    for v in ids:
        _operand(s, i, v, "ids")
    if len(ids) < 2:
        raise EditError("bad_op", i, "ids")
    live = s.live()
    src = [o for o in live if o.get("id") in set(ids)]  # 数组顺序（从后往前），与前端 `operands` 同
    kernel = _kernel(i)
    seed = sorted(ids)
    plan = vectorgeom.run_pathfinder(kernel, mode, live, src, lambda n: _mint(s, i, "pf", seed, n))
    if plan is None:
        raise EditError("bad_op", i, "empty_result")
    removed = set(plan["remove"])
    new_live = [o for o in live if o.get("id") not in removed]
    new_live[plan["at"] : plan["at"]] = plan["add"]
    added = {o["id"] for o in plan["add"]}
    for o in src:
        if o["id"] not in added:
            s.deleted.append(o["id"])
            s.changes.append({"id": o["id"], "deleted": True})
    for o in plan["add"]:
        # 继承了原对象 id 的是「换掉」，其余是新长出来的
        s.changes.append(
            {"id": o["id"], "replaced" if o["id"] in plan["remove"] else "added": True}
        )
        s.touched.add(o["id"])
    s.set_order(new_live)
    _count_check(s, i)
    s.echo.append(
        f"{_MODE_WORDS[mode]} {len(src)} 个路径（{'、'.join(o['id'] for o in src)}）→ {len(plan['add'])} 个路径"
        f"（{'、'.join(o['id'] for o in plan['add'])}）"
    )


def _insert_after(s: _State, anchor_id: str, new: dict) -> None:
    live = s.live()
    at = next(n for n, o in enumerate(live) if o.get("id") == anchor_id)
    live.insert(at + 1, new)
    s.set_order(live)


def _op_offset(s: _State, i: int, op: dict) -> None:
    """`pathfinder.offsetPaths`：原对象不动，旁边多一个偏移后的新路径（紧挨着放在它上面）；正 = 往外扩、负 = 往里缩；
    拐角跟原对象的拐角（给了 `join` 就用它，只影响这一次偏移的几何）。"""
    if set(op) - {"op", "id", "delta_mm", "join"}:
        raise EditError("bad_op", i)
    o = _operand(s, i, op.get("id"), "id")
    d = op.get("delta_mm")
    if not agentobjects._is_num(d) or not math.isfinite(d) or d == 0:  # noqa: SLF001
        raise EditError("bad_value", i, "delta_mm")
    if abs(d) > s.budgets["max_coord_mm"]:
        raise EditError("out_of_range", i, "delta_mm")
    join = op.get("join")
    if join is not None and join not in _JOINS:
        raise EditError("bad_value", i, "join")
    kernel = _kernel(i)
    nid = _mint(s, i, "off", [o["id"]], 0)
    src = {**o, "lineJoin": join} if join else o
    made = vectorgeom.offset_path(kernel, src, float(d), nid)
    if made is None:
        raise EditError("bad_op", i, "empty_result")
    if join:  # 新对象的样式仍是原对象的（`join` 只管这一次几何）
        if o.get("lineJoin") is None:
            made.pop("lineJoin", None)
        else:
            made["lineJoin"] = o["lineJoin"]
    _insert_after(s, o["id"], made)
    s.touched.add(nid)
    s.changes.append({"id": nid, "added": True})
    _count_check(s, i)
    way = "往外" if d > 0 else "往里"
    s.echo.append(f"偏移 {_label(o)}：{way} {abs(float(d)):.3g} mm → 新路径 {nid}（在它上面）")


def _op_outline_stroke(s: _State, i: int, op: dict) -> None:
    """`pathfinder.outlineStrokes`：描边画出来的那块面积变成一个填充对象（填充色 = 原描边色与不透明度，无描边）。
    原对象没有填充：轮廓同 id 换掉它；有填充：原对象留下、关掉描边，轮廓作为新对象紧挨着放在它上面。"""
    if set(op) - {"op", "id"}:
        raise EditError("bad_op", i)
    o = _operand(s, i, op.get("id"), "id")
    if not vectorgeom.can_outline_stroke(o):
        raise EditError("bad_op", i, "no_stroke")
    why = vectorgeom.stroke_unsupported(o)
    if why:
        raise EditError("bad_op", i, f"stroke:{why}")
    kernel = _kernel(i)
    nid = _mint(s, i, "out", [o["id"]], 0)
    res = vectorgeom.outline_stroke(kernel, o, nid)
    if res is None:
        raise EditError("bad_op", i, "empty_result")
    replace, extra = res
    s.out[s.index[o["id"]]] = replace
    s.touched.add(o["id"])
    if extra is None:
        s.changes.append(
            {"id": o["id"], "field": "subpaths", "before": "stroke", "after": "outline"}
        )
        s.echo.append(f"轮廓化描边 {_label(o)}：描边变成填充轮廓（同一个对象，id 不变）")
    else:
        s.changes.append(
            {"id": o["id"], "field": "stroke", "before": o.get("stroke"), "after": False}
        )
        _insert_after(s, o["id"], extra)
        s.touched.add(nid)
        s.changes.append({"id": nid, "added": True})
        s.echo.append(f"轮廓化描边 {_label(o)}：原对象关掉描边，轮廓作为新路径 {nid} 放在它上面")
        _count_check(s, i)


def _op_simplify(s: _State, i: int, op: dict) -> None:
    """`pathfinder.simplifyPaths`：在容差内删掉多余的锚点；同 id、旋转、样式都不变；一个锚点都省不掉 = 不算改动（复述里照实说）。"""
    if set(op) - {"op", "id", "tolerance_pt"}:
        raise EditError("bad_op", i)
    o = _operand(s, i, op.get("id"), "id")
    tol = op.get("tolerance_pt", vectorgeom.SIMPLIFY_TOLERANCE_PT)
    if not agentobjects._is_num(tol) or not 0 < tol <= 100:  # noqa: SLF001
        raise EditError("bad_value", i, "tolerance_pt")
    kernel = _kernel(i)
    made = vectorgeom.simplify_path(kernel, o, float(tol))
    if made is None:
        s.echo.append(f"{_label(o)}：没有可省的锚点（容差 {float(tol):g} pt），未改动")
        return
    count = lambda ob: sum(len(sp["nodes"]) for sp in ob["subpaths"])  # noqa: E731
    s.out[s.index[o["id"]]] = made
    s.touched.add(o["id"])
    s.changes.append({"id": o["id"], "field": "nodes", "before": count(o), "after": count(made)})
    s.echo.append(f"简化 {_label(o)}：锚点 {count(o)} → {count(made)}（容差 {float(tol):g} pt）")


_OPS = (
    "set",
    "move",
    "delete",
    "align",
    "distribute",
    "order",
    "group",
    "ungroup",
    "rotate",
    "pathfinder",
    "offset",
    "outline_stroke",
    "simplify",
)


def apply_ops(
    objects: list[dict], ops: object, panels: dict | None = None, page: dict | None = None
) -> dict:
    """在一份对象列表的副本上跑补丁 → `{"objects": 改后的全部对象, "changed": [改后对象], "deleted": [id],
    "echo": [每条一句], "changes": [{id, field, before, after}], "order": [改后 id 顺序], "reordered": 顺序变了没有}`；
    有错抛 `EditError`（整批不动）。`page` = `{"w", "h"}`，`align` 对齐到页面用。"""
    if not isinstance(ops, list) or not ops:
        raise EditError("bad_op")
    s = _State(objects, panels, page)
    budgets = s.budgets
    touched, changes, echo, deleted = s.touched, s.changes, s.echo, s.deleted
    ctx = vectorscene._Ctx(panels or {})  # noqa: SLF001 — 锚点解析与 0208 同一个
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in _OPS:
            raise EditError("bad_op", i)
        if op["op"] in ("align", "distribute", "group", "ungroup", "pathfinder"):
            {
                "align": _op_align,
                "distribute": _op_distribute,
                "group": _op_group,
                "ungroup": _op_ungroup,
                "pathfinder": _op_pathfinder,
            }[op["op"]](s, i, op)
            continue
        oid = op.get("id")
        if oid not in s.index or oid in deleted:
            raise EditError("unknown_object", i)
        o = s.out[s.index[oid]]
        if o.get("type") not in EDITABLE_TYPES:
            raise EditError("not_vector", i)
        label = _label(o)
        if op["op"] == "order":
            _op_order(s, i, op, o)
            continue
        if op["op"] == "rotate":
            _op_rotate(s, i, op, o)
            continue
        if op["op"] in ("offset", "outline_stroke", "simplify"):
            {"offset": _op_offset, "outline_stroke": _op_outline_stroke, "simplify": _op_simplify}[
                op["op"]
            ](s, i, op)
            continue
        if op["op"] == "delete":
            if set(op) - {"op", "id"}:
                raise EditError("bad_op", i)
            deleted.append(oid)
            changes.append({"id": oid, "deleted": True})
            echo.append(f"删除 {label}")
            continue
        if op["op"] == "set":
            fields = op.get("set")
            if set(op) - {"op", "id", "set"} or not isinstance(fields, dict) or not fields:
                raise EditError("bad_op", i)
            said = []
            for k, v in fields.items():
                spec = _spec_for(o, k)
                if spec is None:
                    raise EditError("bad_field", i, k)
                err = agentobjects._check_value(spec, v, budgets)  # noqa: SLF001 — 与新建同一份值判据
                if err is not None:
                    raise EditError("out_of_range" if err == "out_of_range" else "bad_value", i, k)
                before = o.get(k)
                if before == v:
                    continue
                o[k] = v
                changes.append({"id": oid, "field": k, "before": before, "after": v})
                said.append(f"{k} {_fmt(before)} → {_fmt(v)}")
            if said:
                touched.add(oid)
                echo.append(f"{label}：" + "，".join(said))
            continue
        # move
        if set(op) - {"op", "id", "by_mm", "to", "attach"} or ("by_mm" in op) == ("to" in op):
            raise EditError("bad_op", i)
        if "by_mm" in op:
            d = op["by_mm"]
            if not (
                isinstance(d, list) and len(d) == 2 and all(agentobjects._is_num(v) for v in d)
            ):  # noqa: SLF001
                raise EditError("bad_value", i, "by_mm")
            dx, dy = float(d[0]), float(d[1])
            how = f"平移 ({dx:+.2f}, {dy:+.2f}) mm"
        else:
            attach = op.get("attach", "center")
            try:
                tx, ty = vectorscene._position(op["to"], ctx, str(oid))  # noqa: SLF001
            except vectorscene.SceneError as exc:
                raise EditError("bad_value", i, f"to:{exc.code}") from exc
            ax, ay = _point_of(o, attach)
            dx, dy = tx - ax, ty - ay
            how = f"{attach} 对到 ({tx:.2f}, {ty:.2f}) mm"
        nx, ny = float(o.get("x", 0)) + dx, float(o.get("y", 0)) + dy
        for k, v in (("x", nx), ("y", ny)):
            if agentobjects._check_value(agentobjects.table()["common"][k], v, budgets) is not None:  # noqa: SLF001
                raise EditError("out_of_range", i, k)
        changes.append({"id": oid, "field": "x", "before": o.get("x"), "after": nx})
        changes.append({"id": oid, "field": "y", "before": o.get("y"), "after": ny})
        o["x"], o["y"] = nx, ny
        touched.add(oid)
        echo.append(f"{label}：{how}")
    kept = [o for o in s.out if not (isinstance(o, dict) and o.get("id") in deleted)]
    changed = [o for o in kept if isinstance(o, dict) and o.get("id") in touched]
    first = [o.get("id") for o in objects if isinstance(o, dict) and o.get("id") not in deleted]
    order = [o.get("id") for o in kept if isinstance(o, dict)]
    return {
        "objects": kept,
        "changed": changed,
        "deleted": deleted,
        "echo": echo,
        "changes": changes,
        "order": order,
        "reordered": order != first,
        "ungrouped": sorted(s.ungrouped),
    }


def prune_layout_groups(
    groups: object, objects: list[dict], *, deleted: bool, ungrouped: list[str]
) -> tuple[list | None, list[str]]:
    """补丁之后画布上的布局组约束（`canvas.layoutGroups`）还剩哪些——照前端（#2244）：

    * 删了对象（`deleteSelected`）：**所有**直属成员（`groupId` = 组 id）不足 2 个的布局组都去掉；
    * 拆了组（`ungroupSelected`）：被拆的组若也是布局组、成员不足 2 个，约束一并去掉。

    回 `(剩下的组 | None = 没有这个键, 去掉的组 id)`；没有可去的就原样回。"""
    if not isinstance(groups, list) or not groups:
        return (groups if isinstance(groups, list) else None), []
    gone = set(ungrouped)

    def members(gid: object) -> int:
        return sum(1 for o in objects if isinstance(o, dict) and o.get("groupId") == gid)

    kept, dropped = [], []
    for g in groups:
        gid = g.get("id") if isinstance(g, dict) else None
        short = members(gid) < 2
        if short and (deleted or gid in gone):
            dropped.append(str(gid))
        else:
            kept.append(g)
    return kept, dropped


def _exportable(o: dict) -> bool:
    t = agentobjects.table()
    req = [
        k
        for k, v in {**t["common"], **t["types"].get(o.get("type"), {})}.items()
        if v.get("required") and k != "id"
    ]
    return all(k in o for k in req)


def edit_digest(echo: list[str], ops: list) -> str:
    raw = json.dumps(
        {"echo": echo, "ops": ops}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 方案 / 写入


def _needs_panels(ops: object) -> list:
    """锚点挪动要的数据点请求（借 0208 的 `required_data_points`：把 `to` 当成一个元素的位置）。"""
    if not isinstance(ops, list):
        return []
    return [
        {"kind": "text", "name": f"_op{i}", "at": op["to"]}
        for i, op in enumerate(ops)
        if isinstance(op, dict) and op.get("op") == "move" and "to" in op
    ]


def _fact_key(f: dict) -> str:
    return json.dumps(
        [f.get("check"), f.get("item"), f.get("detail")],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def _check_items(objs: list[dict], ops: object) -> list[dict]:
    """核对要的「描述」：只用来认锚定端（锚在数据 / 元素上的那一端让出尖端余量，与新加时同一条判据）。

    来源两处：对象上落盘的 0208 锚点（`anchor.anchors[].role`），以及这批补丁里 `move` 把 `from` / `to` 对到的锚点。
    不给的话，Agent 加的、尖端本来就指着曲线的箭头，改个线宽都会被判成「遮了数据」。"""
    by: dict[str, dict] = {o["id"]: {"name": o["id"]} for o in objs}
    for o in objs:
        for a in (o.get("anchor") or {}).get("anchors") or []:
            if not isinstance(a, dict) or a.get("role") not in ("from", "to"):
                continue
            kind = next((k for k in ("data", "element", "panel", "item") if k in a), None)
            if kind:
                by[o["id"]][a["role"]] = {"anchor": {kind: a[kind]}}
    for op in ops if isinstance(ops, list) else []:
        if (
            isinstance(op, dict)
            and op.get("op") == "move"
            and op.get("id") in by
            and op.get("attach") in ("from", "to")
            and isinstance(op.get("to"), dict)
            and isinstance(op["to"].get("anchor"), dict)
        ):
            by[op["id"]][op["attach"]] = {"anchor": op["to"]["anchor"]}
    return list(by.values())


def _plan(lay: la.Layout, canvas: dict, ops: object, measure, profile) -> dict:
    # 子图总要量：改后的对象要核「不遮数据」；锚点挪动另要数据点
    panels, _refs, unmeasured = la.measure_panels(canvas, _needs_panels(ops), measure)
    try:
        res = apply_ops(canvas.get("objects") or [], ops, panels, la._page(canvas))  # noqa: SLF001
    except EditError as exc:
        return {"ok": False, "errors": [exc.as_dict()], "unmeasured": unmeasured}
    changed = res["changed"]
    facts: list[dict] = []
    if changed:
        page = la._page(canvas)  # noqa: SLF001
        ids = {o["id"] for o in changed}
        existing = [
            o
            for o in res["objects"]
            if isinstance(o, dict)
            and o.get("type") == "text"
            and not o.get("hidden")
            and o.get("id") not in ids
        ]
        # 编组 / 拆组会连带改到组里的子图等非矢量对象：它们照写，但核对只看矢量对象
        visible = [o for o in changed if not o.get("hidden") and o.get("type") in EDITABLE_TYPES]
        # 字段表里必填的键缺了（外部工具写的、更老的文档）：导出转换读不成，这几个对象如实标「量不了」，不让整批崩
        unreadable = [o for o in visible if not _exportable(o)]
        for o in unreadable:
            facts.append(
                {
                    "check": "page",
                    "status": "unknown",
                    "item": o["id"],
                    "detail": {"reason": "incomplete_object"},
                }
            )
        visible = [o for o in visible if _exportable(o)]
        if visible:
            items = _check_items(visible, ops)

            def run(objs: list[dict]) -> list[dict]:
                solved = {"objects": [{"id": o["id"]} for o in objs], "anchors": []}
                return vectorcheck.check(
                    solved, objs, panels, items, page=page, profile=profile, existing_texts=existing
                )["facts"]

            got = run(visible)
            # 改之前就已经这样的（用户手画的箭头本来就压着曲线、早就接受的一处压字）：不让它挡住这次编辑，
            # 标 `preexisting` 如实报（ADR 0212 §四「没动的不重新报」的同一个意思，按对象改前的样子再量一次）
            before_by = {o.get("id"): o for o in canvas.get("objects") or [] if isinstance(o, dict)}
            olds = [
                before_by[o["id"]]
                for o in visible
                if o["id"] in before_by
                and not before_by[o["id"]].get("hidden")
                and _exportable(before_by[o["id"]])
            ]
            was = {_fact_key(f) for f in run(olds) if f["status"] == "fail"} if olds else set()
            for f in got:
                if f["status"] == "fail" and _fact_key(f) in was:
                    f = {**f, "status": "preexisting"}
                facts.append(f)
    failed = [f for f in facts if f["status"] == "fail"]
    groups, dropped = prune_layout_groups(
        canvas.get("layoutGroups"),
        res["objects"],
        deleted=bool(res["deleted"]),
        ungrouped=res["ungrouped"],
    )
    echo = list(res["echo"])
    if dropped:
        echo.append(
            f"布局组 {'、'.join(dropped)} 剩下的成员不足 2 个，排列约束一并去掉（对象原地不动）"
        )
    return {
        "ok": not failed,
        "objects": res["objects"],
        "layout_groups": groups,
        "layout_groups_dropped": dropped,
        "changed": changed,
        "deleted": res["deleted"],
        "changes": res["changes"],
        "echo": echo,
        "order": res["order"],
        "reordered": res["reordered"],
        "echo_digest": edit_digest(echo, ops),  # type: ignore[arg-type]
        "facts": facts,
        "failed": failed,
        "unverified": [f for f in facts if f["status"] == "unknown"],
        "unmeasured": unmeasured,
        "errors": [{"code": "checks_failed"}] if failed else [],
    }


def propose(
    project: str | Path,
    name: str,
    ops: object,
    measure,
    *,
    base_revision: str,
    canvas_id: str | None = None,
    profile: dict | None = None,
) -> dict:
    """不写盘：算出改后的对象、核对、复述。"""
    lay = la.open_layout(project, name)
    if doclock.revision_conflict(base_revision, lay.revision):
        raise la.LayoutError("layout_changed", revision=lay.revision)
    canvas = la._canvas(lay.doc, canvas_id)  # noqa: SLF001
    return {"base_revision": lay.revision, **_plan(lay, canvas, ops, measure, profile)}


def commit(
    project: str | Path,
    name: str,
    ops: object,
    measure,
    *,
    base_revision: str,
    echo_digest_seen: str,
    canvas_id: str | None = None,
    profile: dict | None = None,
) -> dict:
    """服务端重算、重核，摘要对得上才写；只换掉被点名的对象、删掉要删的，其余逐字节不动。"""
    lay = la.open_layout(project, name)
    if doclock.revision_conflict(base_revision, lay.revision):
        raise la.LayoutError("layout_changed", revision=lay.revision)
    canvas = la._canvas(lay.doc, canvas_id)  # noqa: SLF001
    plan = _plan(lay, canvas, ops, measure, profile)
    if not plan["ok"]:
        return {"written": False, "plan": plan}
    if plan["echo_digest"] != echo_digest_seen:
        raise la.LayoutError("echo_mismatch")
    new_by_id = {o["id"]: o for o in plan["changed"]}
    gone = set(plan["deleted"])

    def patch(target: dict) -> None:
        objs = target.get("objects") or []
        kept = [
            new_by_id.get(o.get("id"), o)
            for o in objs
            if not (isinstance(o, dict) and o.get("id") in gone)
        ]
        have = {o.get("id") for o in objs if isinstance(o, dict)}
        kept += [
            o for oid, o in new_by_id.items() if oid not in have
        ]  # 第 3 期：几何操作新长出来的对象
        if plan[
            "reordered"
        ]:  # 层级 / 编组挪了顺序：按方案里的顺序重排（同一份修订号，id 集合一致）
            rank = {oid: n for n, oid in enumerate(plan["order"])}
            kept.sort(
                key=lambda o: rank.get(o.get("id") if isinstance(o, dict) else None, len(rank))
            )
        target["objects"] = kept
        # #2244：拆组 / 删除后成员不足的布局组约束（与前端同一规则）
        if plan["layout_groups_dropped"]:
            if plan["layout_groups"]:
                target["layoutGroups"] = plan["layout_groups"]
            else:
                target.pop("layoutGroups", None)

    after, token = la.write_canvas(project, name, lay, canvas.get("id"), patch)
    return {
        "written": True,
        "revision": after,
        "changed": sorted(new_by_id),
        "deleted": sorted(gone),
        "undo_token": token,
        "plan": plan,
    }


def preview_edit(canvas: dict, plan: dict, panel_pdfs: dict[str, str]) -> dict | None:
    """改后的那一块局部图：画布换成方案里改后的全部对象（层级照方案），框住动到的对象改前与改后的位置
    （删掉的按删前的位置）。与 0208 的局部图同一条真合成；画不出来回 None。"""
    before = {o.get("id"): o for o in canvas.get("objects") or [] if isinstance(o, dict)}
    changed = plan.get("changed") or []
    ids = [o.get("id") for o in changed] + list(plan.get("deleted") or [])
    # 框按**看得见的**外接框（旋转过的绕中心转出来，照对齐用的 `_vb`）：只拿 x / y / w / h 会把转成竖的长箭头两头裁掉（Codex #2254）
    focus = [
        dict(zip(("x", "y", "w", "h"), _vb(o)))
        for o in [*changed, *(before.get(i) for i in ids)]
        if isinstance(o, dict) and all(agentobjects._is_num(o.get(k)) for k in ("x", "y", "w", "h"))  # noqa: SLF001
    ]
    if not focus:
        return None
    after = {**canvas, "objects": plan.get("objects") or []}
    return la.preview(after, [], panel_pdfs, focus=focus)
