"""矢量编辑评测的成功判据：对「改完的画布」跑谓词，不判用了哪个操作、只判结果。

判据是 corpus.json 里的一列声明式谓词（`{"t": 名字, …}`），每条返回 `(通过, 一句说明)`。全部通过才算成功。
每题另有两条隐含的：没点名的对象与原来深相等（`may_change`），除非题目允许新增（`may_add`）否则不许多出对象。
纯标准库，不依赖 Tavotto 源码（判据的主语是结果文档，不是引擎内部）。
"""

from __future__ import annotations

import copy
from typing import Callable

#: 浮点比较的默认容差（毫米 / 点）
TOL = 0.05


def _by_id(objs: list[dict]) -> dict[str, dict]:
    return {o["id"]: o for o in objs if isinstance(o, dict) and "id" in o}


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _close(a, b, tol: float = TOL) -> bool:
    x, y = _num(a), _num(b)
    return x is not None and y is not None and abs(x - y) <= tol


def _eq(a, b, tol: float = TOL) -> bool:
    if _num(a) is not None and _num(b) is not None:
        return _close(a, b, tol)
    return str(a).lower() == str(b).lower() if isinstance(a, str) and isinstance(b, str) else a == b


def _get(o: dict | None, field: str):
    return None if o is None else o.get(field)


def _new_objects(before: list[dict], after: list[dict]) -> list[dict]:
    have = set(_by_id(before))
    return [o for o in after if o.get("id") not in have]


def _tip(o: dict) -> tuple[float, float] | None:
    """箭头尖（画布毫米）：`head` 在 end 就是 end 点，在 start 就是 start 点；没写按 end。"""
    st, en = o.get("start"), o.get("end")
    if not (isinstance(st, dict) and isinstance(en, dict)):
        return None
    p = st if o.get("head") == "start" else en
    return o["x"] + p["rx"] * o["w"], o["y"] + p["ry"] * o["h"]


def _center(o: dict) -> tuple[float, float]:
    return o["x"] + o["w"] / 2, o["y"] + o["h"] / 2


# --------------------------------------------------------------------------- 谓词

Result = tuple[bool, str]


def p_field(spec: dict, before: list[dict], after: list[dict]) -> Result:
    o = _by_id(after).get(spec["id"])
    ok = o is not None and _eq(o.get(spec["field"]), spec["eq"], spec.get("tol", TOL))
    return ok, f"{spec['id']}.{spec['field']} = {_get(o, spec['field'])!r}（要 {spec['eq']!r}）"


def p_field_all(spec: dict, before: list[dict], after: list[dict]) -> Result:
    idx = _by_id(after)
    bad = [
        f"{i}={_get(idx.get(i), spec['field'])!r}"
        for i in spec["ids"]
        if not (i in idx and _eq(idx[i].get(spec["field"]), spec["eq"], spec.get("tol", TOL)))
    ]
    return not bad, f"{spec['field']} 要 {spec['eq']!r}，不对的：{', '.join(bad) or '无'}"


def p_same(spec: dict, before: list[dict], after: list[dict]) -> Result:
    """这几个对象的某字段彼此相等（容差内），不指定值。"""
    idx = _by_id(after)
    vals = [_get(idx.get(i), spec["field"]) for i in spec["ids"]]
    ok = all(v is not None for v in vals) and all(
        _close(v, vals[0], spec.get("tol", TOL)) for v in vals
    )
    return ok, f"{spec['field']}：{dict(zip(spec['ids'], vals))}"


def p_ratio(spec: dict, before: list[dict], after: list[dict]) -> Result:
    b, a = _by_id(before).get(spec["id"]), _by_id(after).get(spec["id"])
    bv, av = _num(_get(b, spec["field"])), _num(_get(a, spec["field"]))
    ok = bv is not None and av is not None and abs(av - bv * spec["ratio"]) <= spec.get("tol", TOL)
    return ok, f"{spec['id']}.{spec['field']}：{bv} → {av}（要 ×{spec['ratio']}）"


def p_deleted(spec: dict, before: list[dict], after: list[dict]) -> Result:
    left = [i for i in spec["ids"] if i in _by_id(after)]
    return not left, f"该删的还在：{left or '无'}"


def p_present(spec: dict, before: list[dict], after: list[dict]) -> Result:
    gone = [i for i in spec["ids"] if i not in _by_id(after)]
    return not gone, f"该在的不见了：{gone or '无'}"


def p_front(spec: dict, before: list[dict], after: list[dict]) -> Result:
    ids = [o["id"] for o in after]
    ok = bool(ids) and ids[-1] == spec["id"]
    return ok, f"最上层是 {ids[-1] if ids else None}（要 {spec['id']}）"


def p_back(spec: dict, before: list[dict], after: list[dict]) -> Result:
    ids = [o["id"] for o in after if o.get("type") != "panel"]
    ok = bool(ids) and ids[0] == spec["id"]
    return ok, f"最底层的矢量对象是 {ids[0] if ids else None}（要 {spec['id']}）"


def _gpath(o: dict | None) -> tuple:
    """所在组：一层只写 `groupId`，两层及以上另有 `groupPath`（与前端 `groupTree.setGroupPath` 同形）。"""
    if not o:
        return ()
    if o.get("groupPath"):
        return tuple(o["groupPath"])
    return (o["groupId"],) if o.get("groupId") else ()


def p_grouped(spec: dict, before: list[dict], after: list[dict]) -> Result:
    idx = _by_id(after)
    paths = {i: _gpath(idx.get(i)) for i in spec["ids"]}
    top = {p[-1] for p in paths.values() if p}
    ok = len(top) == 1 and all(paths.values())
    others = [
        o["id"]
        for o in after
        if o["id"] not in spec["ids"] and top and _gpath(o)[-1:] and _gpath(o)[-1] in top
    ]
    return ok and not others, f"所在组：{paths}；混进组的别人：{others or '无'}"


def p_count_type(spec: dict, before: list[dict], after: list[dict]) -> Result:
    n = sum(1 for o in after if o.get("type") == spec["type"])
    return n == spec["n"], f"{spec['type']} 共 {n} 个（要 {spec['n']}）"


def p_replaced_by_closed_path(spec: dict, before: list[dict], after: list[dict]) -> Result:
    """这几个对象不在了，换成**一个**封闭路径（路径总数与「一条」对得上）。"""
    idx = _by_id(after)
    new = _new_objects(before, after)
    kept = [i for i in spec["ids"] if i in idx]
    # 合并结果可以沿用其中一个 id（顶层那个），也可以是新 id：统计「这批 id + 新增」里的路径数
    cand = [idx[i] for i in kept] + [o for o in new if o.get("type") == "path"]
    paths = [o for o in cand if o.get("type") == "path"]
    closed = all(sp.get("closed") for o in paths for sp in o.get("subpaths") or [])
    ok = len(paths) == 1 and closed and len(kept) + len(new) == 1
    return (
        ok,
        f"原 id 还在 {kept}，新增 {[o['id'] for o in new]}，路径 {len(paths)} 条，封闭={closed}",
    )


def p_new_path_grown(spec: dict, before: list[dict], after: list[dict]) -> Result:
    """旁边多了一个路径，宽 / 高比原来多 `by` mm（偏移 ±delta 各一边，所以 by = 2×delta）。"""
    src = _by_id(before)[spec["id"]]
    new = [o for o in _new_objects(before, after) if o.get("type") == "path"]
    hit = [
        o
        for o in new
        if _close(o["w"], src["w"] + spec["by"], spec.get("tol", 0.3))
        and _close(o["h"], src["h"] + spec["by"], spec.get("tol", 0.3))
    ]
    return (
        bool(hit),
        f"新路径 {[(o['id'], round(o['w'], 2), round(o['h'], 2)) for o in new]}（原 {src['w']}×{src['h']}，要各多 {spec['by']}）",
    )


def p_outlined(spec: dict, before: list[dict], after: list[dict]) -> Result:
    """原来只有描边的线，现在是填充轮廓：有填充色、没有描边。"""
    o = _by_id(after).get(spec["id"])
    ok = (
        o is not None
        and o.get("type") == "path"
        and bool(o.get("fill"))
        and (o.get("stroke") is False or not o.get("strokePt"))
    )
    return (
        ok,
        f"{spec['id']}：type={_get(o, 'type')} fill={_get(o, 'fill')!r} strokePt={_get(o, 'strokePt')!r} stroke={_get(o, 'stroke')!r}",
    )


def p_center_x(spec: dict, before: list[dict], after: list[dict]) -> Result:
    o = _by_id(after).get(spec["id"])
    cx = _center(o)[0] if o else None
    return cx is not None and abs(cx - spec["eq"]) <= spec.get(
        "tol", 1.0
    ), f"{spec['id']} 中心 x = {cx}（要 {spec['eq']}）"


def p_top_between(spec: dict, before: list[dict], after: list[dict]) -> Result:
    o = _by_id(after).get(spec["id"])
    y = o["y"] if o else None
    lo, hi = spec["between"]
    return y is not None and lo <= y <= hi, f"{spec['id']} 顶边 y = {y}（要在 {lo}~{hi}）"


def p_gaps_equal(spec: dict, before: list[dict], after: list[dict]) -> Result:
    idx = _by_id(after)
    boxes = sorted(
        ((idx[i]["x"], idx[i]["x"] + idx[i]["w"]) for i in spec["ids"] if i in idx),
        key=lambda b: b[0],
    )
    gaps = [boxes[k + 1][0] - boxes[k][1] for k in range(len(boxes) - 1)]
    ok = len(boxes) == len(spec["ids"]) and gaps and max(gaps) - min(gaps) <= spec.get("tol", 0.1)
    return bool(ok), f"间距 {[round(g, 3) for g in gaps]}"


def p_new_arrow_tip(spec: dict, before: list[dict], after: list[dict]) -> Result:
    arrows = [o for o in _new_objects(before, after) if o.get("type") == "arrow"]
    tips = [_tip(o) for o in arrows]
    best = min(
        (((t[0] - spec["xy"][0]) ** 2 + (t[1] - spec["xy"][1]) ** 2) ** 0.5 for t in tips if t),
        default=None,
    )
    ok = best is not None and best <= spec["r"]
    return (
        ok,
        f"新增箭头 {len(arrows)} 支，箭头尖离 {spec['xy']} 最近 {None if best is None else round(best, 2)} mm（要 ≤ {spec['r']}）",
    )


def p_new_text(spec: dict, before: list[dict], after: list[dict]) -> Result:
    new = [
        o
        for o in _new_objects(before, after)
        if o.get("type") == "text" and spec["text"] in str(o.get("text", ""))
    ]
    near = spec.get("near")
    if near:
        new = [
            o
            for o in new
            if abs(_center(o)[0] - near[0]) <= near[2] and abs(_center(o)[1] - near[1]) <= near[2]
        ]
    return bool(new), f"含「{spec['text']}」的新文字 {len(new)} 个" + (
        f"（中心应在 {near[:2]} ±{near[2]} mm 内）" if near else ""
    )


def p_new_box_around(spec: dict, before: list[dict], after: list[dict]) -> Result:
    """新增一个形状 / 路径，外框与给定矩形 [x, y, w, h] 各边差不超过 tol。"""
    x, y, w, h = spec["rect"]
    tol = spec.get("tol", 3.0)
    new = [o for o in _new_objects(before, after) if o.get("type") in ("shape", "path")]
    hit = [
        o
        for o in new
        if all(_close(a, b, tol) for a, b in zip((o["x"], o["y"], o["w"], o["h"]), (x, y, w, h)))
    ]
    return (
        bool(hit),
        f"新增形状 / 路径 {[(o['id'], [round(o[k], 1) for k in 'xywh']) for o in new]}（要接近 {spec['rect']}）",
    )


def p_new_near(spec: dict, before: list[dict], after: list[dict]) -> Result:
    """新增对象（类型之一）的外框与矩形 [x, y, w, h] 相交（括号这类：落在子图旁边的某一带里）。"""
    x, y, w, h = spec["rect"]
    new = [o for o in _new_objects(before, after) if o.get("type") in spec["types"]]
    hit = [
        o
        for o in new
        if o["x"] < x + w and o["x"] + o["w"] > x and o["y"] < y + h and o["y"] + o["h"] > y
    ]
    return (
        bool(hit),
        f"新增 {spec['types']} {[(o['id'], [round(o[k], 1) for k in 'xywh']) for o in new]}（要与 {spec['rect']} 相交）",
    )


def p_unchanged(spec: dict, before: list[dict], after: list[dict]) -> Result:
    return before == after, "画布与原来逐字段相同" if before == after else "画布被改动了"


PREDICATES: dict[str, Callable[[dict, list[dict], list[dict]], Result]] = {
    "field": p_field,
    "field_all": p_field_all,
    "same": p_same,
    "ratio": p_ratio,
    "deleted": p_deleted,
    "present": p_present,
    "front": p_front,
    "back": p_back,
    "grouped": p_grouped,
    "count_type": p_count_type,
    "replaced_by_closed_path": p_replaced_by_closed_path,
    "new_path_grown": p_new_path_grown,
    "outlined": p_outlined,
    "center_x": p_center_x,
    "top_between": p_top_between,
    "gaps_equal": p_gaps_equal,
    "new_arrow_tip": p_new_arrow_tip,
    "new_text": p_new_text,
    "new_box_around": p_new_box_around,
    "new_near": p_new_near,
    "unchanged": p_unchanged,
}


def evaluate(task: dict, before: list[dict], after: list[dict]) -> dict:
    """→ `{"ok": 全部通过, "checks": [{"t", "ok", "detail"}…]}`。

    隐含两条：没点名的对象（`may_change` 之外的、原来就有的）与原来深相等；除非 `may_add`，不许多出对象。"""
    before = copy.deepcopy(before)
    after = copy.deepcopy(after)
    checks: list[dict] = []
    for spec in task["predicate"]:
        ok, detail = PREDICATES[spec["t"]](spec, before, after)
        checks.append({"t": spec["t"], "ok": bool(ok), "detail": detail})
    if "unchanged" not in {s["t"] for s in task["predicate"]}:
        may = set(task.get("may_change", []))
        a = _by_id(after)
        moved = [o["id"] for o in before if o["id"] not in may and o["id"] in a and a[o["id"]] != o]
        checks.append(
            {"t": "others_unchanged", "ok": not moved, "detail": f"没点名却被改的：{moved or '无'}"}
        )
        if not task.get("may_add"):
            extra = [o["id"] for o in _new_objects(before, after)]
            checks.append(
                {
                    "t": "no_extra_objects",
                    "ok": not extra,
                    "detail": f"多出来的对象：{extra or '无'}",
                }
            )
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
