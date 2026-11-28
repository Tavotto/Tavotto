"""机理图评测的成功判据：对「改完的画布」跑谓词，只判结果、不判 Agent 用了哪些条目。

纯标准库，不依赖 Tavotto 源码（判据的主语是结果文档）。每条返回 `(通过, 一句说明)`，全部通过才算成功。
几何口径：对象 `x y w h`（画布毫米）；路径子路径节点是对象框内的比例（0–1）、三次贝塞尔；`rotationDeg` 绕框中心顺时针为正。
"""

from __future__ import annotations

import math
from typing import Callable

Result = tuple[bool, str]
FRAME_PREFIX = "示意图框"
ADJ_MM = 8.0  # 标签与最近对象的外框间距上限
TIP_MM = 3.0  # 箭头尖离最近对象外框的距离上限


def _by_id(objs: list[dict]) -> dict[str, dict]:
    return {o["id"]: o for o in objs if isinstance(o, dict) and "id" in o}


def new_objects(before: list[dict], after: list[dict]) -> list[dict]:
    have = set(_by_id(before))
    return [o for o in after if o.get("id") not in have]


def is_frame(o: dict) -> bool:
    return o.get("type") == "shape" and str(o.get("name") or "").startswith(FRAME_PREFIX)


def is_core(o: dict) -> bool:
    return (o.get("glyph") or {}).get("host_made") is True


def glyph_kind(o: dict) -> str | None:
    g = o.get("glyph")
    return g.get("kind") if isinstance(g, dict) else None


def box(o: dict) -> tuple[float, float, float, float]:
    return float(o["x"]), float(o["y"]), float(o["w"]), float(o["h"])


def gap(a: tuple, b: tuple) -> float:
    """两个外框的最短距离（相交 = 0）。"""
    dx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]), 0.0)
    dy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]), 0.0)
    return math.hypot(dx, dy)


def _bez(p0, p1, p2, p3, n=10):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append(
            (
                u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
            )
        )
    return out


def polylines(o: dict) -> list[tuple[list[tuple[float, float]], bool]]:
    """路径对象的各子路径 → 画布毫米折线（已按 `rotationDeg` 旋转）。"""
    x, y, w, h = box(o)
    rot = math.radians(float(o.get("rotationDeg") or 0.0))
    c, s = math.cos(rot), math.sin(rot)
    cx, cy = x + w / 2, y + h / 2
    out = []
    for sp in o.get("subpaths") or []:
        nodes = sp.get("nodes") or []
        if len(nodes) < 2:
            continue
        pts = [(x + nodes[0]["p"][0] * w, y + nodes[0]["p"][1] * h)]
        segs = len(nodes) if sp.get("closed") else len(nodes) - 1
        for i in range(segs):
            a, b = nodes[i], nodes[(i + 1) % len(nodes)]
            sc = lambda q: (x + q[0] * w, y + q[1] * h)  # noqa: E731
            pts += _bez(sc(a["p"]), sc(a.get("out", a["p"])), sc(b.get("in", b["p"])), sc(b["p"]))
        if rot:
            pts = [
                (cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c)
                for px, py in pts
            ]
        out.append((pts, bool(sp.get("closed"))))
    return out


def _cross(a, b, c, d) -> bool:
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def _inside(p, poly) -> bool:
    inside = False
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (
            (b[1] - a[1]) or 1e-12
        ) + a[0]:
            inside = not inside
    return inside


def outlines_overlap(a: dict, b: dict) -> bool:
    """两个路径对象的真实轮廓是否交叠：任意两条边相交，或一条闭合轮廓整个落在另一条里面。"""
    pa, pb = polylines(a), polylines(b)
    for la, ca in pa:
        for lb, cb in pb:
            if any(
                _cross(la[i], la[i + 1], lb[j], lb[j + 1])
                for i in range(len(la) - 1)
                for j in range(len(lb) - 1)
            ):
                return True
            if ca and lb and _inside(lb[0], la):
                return True
            if cb and la and _inside(la[0], lb):
                return True
    return False


def _members(after: list[dict]) -> dict:
    mem = [o for o in after if glyph_kind(o) == "membrane"]
    prots = [o for o in after if glyph_kind(o) not in (None, "membrane") and not is_core(o)]
    frame = [o for o in after if is_frame(o)]
    texts = [o for o in after if o.get("type") == "text"]
    arrows = [o for o in after if o.get("type") == "arrow"]
    return {"mem": mem, "prots": prots, "frame": frame, "texts": texts, "arrows": arrows}


# --------------------------------------------------------------------------- 谓词


def p_counts(task: dict, before, after, p: dict) -> Result:
    m = _members(new_objects(before, after))
    want = {k: p.get(k, 0) for k in ("membranes", "proteins", "frames", "texts", "arrows")}
    got = {
        "membranes": len(m["mem"]),
        "proteins": len(m["prots"]),
        "frames": len(m["frame"]),
        "texts": len(m["texts"]),
        "arrows": len(m["arrows"]),
    }
    bad = [k for k, v in want.items() if got[k] < v]
    return (not bad, f"{got}（至少 {want}）" if bad else f"{got}")


def p_proteins_in_membrane(task, before, after, p) -> Result:
    """每个跨膜蛋白：竖直方向横跨膜（顶在膜上沿之上、底在膜下沿之下），水平方向中心落在膜的左右范围内。"""
    m = _members(new_objects(before, after))
    if not m["mem"] or not m["prots"]:
        return False, "没有膜或没有蛋白"
    mx, my, mw, mh = box(m["mem"][0])
    bad = []
    for o in m["prots"]:
        pts = [q for lines, _c in polylines(o) for line in [lines] for q in line]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        cx = (min(xs) + max(xs)) / 2
        if not (min(ys) < my + 0.5 and max(ys) > my + mh - 0.5 and mx <= cx <= mx + mw):
            bad.append(o.get("name"))
    return (not bad, f"不在膜内的：{bad}" if bad else f"{len(m['prots'])} 个蛋白都横跨在膜内")


def p_no_overlap(task, before, after, p) -> Result:
    m = _members(new_objects(before, after))
    ps = m["prots"]
    bad = [
        (a.get("name"), b.get("name"))
        for i, a in enumerate(ps)
        for b in ps[i + 1 :]
        if outlines_overlap(a, b)
    ]
    return (not bad, f"交叠：{bad}" if bad else f"{len(ps)} 个蛋白真实轮廓两两不交叠")


def extent(o: dict) -> tuple[float, float, float, float]:
    """对象真正占的范围（画布毫米）：路径取旋转后的真实轮廓，其余取旋转后四角的外接框。"""
    if o.get("type") == "path" and o.get("subpaths"):
        pts = [q for line, _c in polylines(o) for q in line]
        if pts:
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    x, y, w, h = box(o)
    rot = math.radians(float(o.get("rotationDeg") or 0.0))
    if not rot:
        return x, y, w, h
    c, s = math.cos(rot), math.sin(rot)
    cx, cy = x + w / 2, y + h / 2
    pts = [
        (cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c)
        for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    ]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def p_inside_frame(task, before, after, p) -> Result:
    """全部在示意图框内：按旋转后的真实范围（不是未旋转的对象矩形）；框自己没有旋转时取它的外框。"""
    new = new_objects(before, after)
    m = _members(new)
    if not m["frame"]:
        return False, "没有示意图框"
    fx, fy, fw, fh = extent(m["frame"][0])
    out = []
    for o in new:
        if is_frame(o):
            continue
        x, y, w, h = extent(o)
        if x < fx - 0.01 or y < fy - 0.01 or x + w > fx + fw + 0.01 or y + h > fy + fh + 0.01:
            out.append(o.get("name"))
    return (not out, f"框外的：{out}" if out else "全部在示意图框内")


def p_required_texts(task, before, after, p) -> Result:
    texts = [
        str(o.get("text") or "") for o in new_objects(before, after) if o.get("type") == "text"
    ]
    missing = [k for k in p.get("contains", []) if not any(k in t for t in texts)]
    return (not missing, f"缺文字：{missing}" if missing else f"文字都在：{p.get('contains')}")


def p_labels_adjacent(task, before, after, p) -> Result:
    """每个文字标签离最近的**非文字对象**（不含示意图框和疏水底带）不超过 ADJ_MM，且文字中心不落在膜 / 蛋白的真实轮廓里。"""
    new = new_objects(before, after)
    m = _members(new)
    targets = [o for o in new if o.get("type") != "text" and not is_frame(o) and not is_core(o)]
    bad = []
    for t in m["texts"]:
        tb = box(t)
        near = min((gap(tb, box(o)) for o in targets), default=1e9)
        if near > p.get("max_mm", ADJ_MM):
            bad.append(f"{t.get('text')}（最近 {near:.1f} mm）")
            continue
        c = (tb[0] + tb[2] / 2, tb[1] + tb[3] / 2)
        for o in [*m["mem"], *m["prots"]]:
            if any(cl and _inside(c, line) for line, cl in polylines(o)):
                bad.append(f"{t.get('text')}（压在「{o.get('name')}」里）")
                break
    return (
        not bad,
        f"标签不贴近 / 压住图元：{bad}" if bad else f"{len(m['texts'])} 个标签都贴近目标",
    )


def p_arrows_reach(task, before, after, p) -> Result:
    """每支箭的尖离某个非箭头 / 非示意图框 / 非底带对象的外框不超过 TIP_MM（不悬在空中）。"""
    new = new_objects(before, after)
    targets = [
        o
        for o in new
        if o.get("type") not in ("arrow", "text") and not is_frame(o) and not is_core(o)
    ]
    bad = []
    for a in [o for o in new if o.get("type") == "arrow"]:
        st, en = a.get("start"), a.get("end")
        if not (isinstance(st, dict) and isinstance(en, dict)):
            continue
        pt = st if a.get("head") == "start" else en
        tip = (a["x"] + pt["rx"] * a["w"], a["y"] + pt["ry"] * a["h"])
        d = min((gap((tip[0], tip[1], 0, 0), box(o)) for o in targets), default=1e9)
        if d > p.get("max_mm", TIP_MM):
            bad.append(f"{a.get('name')}（尖离目标 {d:.1f} mm）")
    return (not bad, f"悬空的箭：{bad}" if bad else "箭头尖都落在目标上")


def p_originals_kept(task, before, after, p) -> Result:
    now = _by_id(after)
    bad = [o["id"] for o in before if now.get(o["id"]) != o]
    return (not bad, f"原有对象被改 / 删：{bad}" if bad else "原有对象原样保留")


PREDICATES: dict[str, Callable] = {
    "counts": p_counts,
    "proteins_in_membrane": p_proteins_in_membrane,
    "no_overlap": p_no_overlap,
    "inside_frame": p_inside_frame,
    "required_texts": p_required_texts,
    "labels_adjacent": p_labels_adjacent,
    "arrows_reach": p_arrows_reach,
    "originals_kept": p_originals_kept,
}


def evaluate(task: dict, before: list[dict], after: list[dict]) -> dict:
    checks = []
    for spec in task["predicates"]:
        fn = PREDICATES.get(spec["t"])
        if fn is None:
            checks.append({"t": spec["t"], "ok": False, "detail": "未知判据"})
            continue
        ok, detail = fn(task, before, after, spec)
        checks.append({"t": spec["t"], "ok": ok, "detail": detail})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
