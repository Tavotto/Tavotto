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
* `{"op": "delete", "id"}`。

核对只看这次动到的对象（改后的样子），画布上没动的文字当障碍；用户早就接受的地方不重新报。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from . import agentobjects, doclock, layoutannotate as la, vectorcheck, vectorscene

#: 能改的对象类型（矢量对象）
EDITABLE_TYPES = ("text", "arrow", "shape", "path")
#: 字段表之外、编辑时额外允许的键（新建时不给 Agent，改已有对象时可以）
EDIT_ONLY = {"locked": {"kind": "bool"}, "hidden": {"kind": "bool"}}
#: 几何与名字（字段表 common 里除 id / type / groupPath 之外的键；编组归第 2 期的「排」）
GEOMETRY = ("x", "y", "w", "h", "rotationDeg", "name")

ERROR_CODES = (
    "bad_op",
    "unknown_object",
    "not_vector",
    "bad_field",
    "bad_value",
    "out_of_range",
    "duplicate_target",
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


def apply_ops(objects: list[dict], ops: object, panels: dict | None = None) -> dict:
    """在一份对象列表的副本上跑补丁 → `{"objects": 改后的全部对象, "changed": [改后对象], "deleted": [id],
    "echo": [每条一句], "changes": [{id, field, before, after}]}`；有错抛 `EditError`（整批不动）。"""
    if not isinstance(ops, list) or not ops:
        raise EditError("bad_op")
    out = copy.deepcopy(objects)
    index = {o.get("id"): i for i, o in enumerate(out) if isinstance(o, dict)}
    touched: set[str] = set()
    deleted: list[str] = []
    changes: list[dict] = []
    echo: list[str] = []
    budgets = agentobjects.table()["budgets"]
    ctx = vectorscene._Ctx(panels or {})  # noqa: SLF001 — 锚点解析与 0208 同一个
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in ("set", "move", "delete"):
            raise EditError("bad_op", i)
        oid = op.get("id")
        if oid not in index or oid in deleted:
            raise EditError("unknown_object", i)
        o = out[index[oid]]
        if o.get("type") not in EDITABLE_TYPES:
            raise EditError("not_vector", i)
        label = _label(o)
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
    kept = [o for o in out if not (isinstance(o, dict) and o.get("id") in deleted)]
    changed = [o for o in kept if isinstance(o, dict) and o.get("id") in touched]
    return {
        "objects": kept,
        "changed": changed,
        "deleted": deleted,
        "echo": echo,
        "changes": changes,
    }


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
        if isinstance(op, dict) and "to" in op
    ]


def _plan(lay: la.Layout, canvas: dict, ops: object, measure, profile) -> dict:
    # 子图总要量：改后的对象要核「不遮数据」；锚点挪动另要数据点
    panels, _refs, unmeasured = la.measure_panels(canvas, _needs_panels(ops), measure)
    try:
        res = apply_ops(canvas.get("objects") or [], ops, panels)
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
        visible = [o for o in changed if not o.get("hidden")]
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
            solved = {"objects": [{"id": o["id"]} for o in visible], "anchors": []}
            facts += vectorcheck.check(
                solved, visible, panels, [], page=page, profile=profile, existing_texts=existing
            )["facts"]
    failed = [f for f in facts if f["status"] == "fail"]
    return {
        "ok": not failed,
        "objects": res["objects"],
        "changed": changed,
        "deleted": res["deleted"],
        "changes": res["changes"],
        "echo": res["echo"],
        "echo_digest": edit_digest(res["echo"], ops),  # type: ignore[arg-type]
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
        target["objects"] = [
            new_by_id.get(o.get("id"), o)
            for o in objs
            if not (isinstance(o, dict) and o.get("id") in gone)
        ]

    after, token = la.write_canvas(project, name, lay, canvas.get("id"), patch)
    return {
        "written": True,
        "revision": after,
        "changed": sorted(new_by_id),
        "deleted": sorted(gone),
        "undo_token": token,
        "plan": plan,
    }
