"""路径锚点编辑的纯函数（ADR 0212 第 4 期）：逐函数照前端 `web/src/lib/pathEdit.ts` 与 `store/pathActions.ts` / `lib/pathfinder.ts`
里的对象级规则（剪断、连接、反转、复合路径）。**无 IO、无状态**；操作层（`engine/vectoredit.py` 的 `_op_*`）只管校验、记账与复述。

编辑一律在**页面 mm、未旋转**的空间里做（`vectorgeom.path_to_mm` 给出的子路径：`{"closed", "nodes": [{"p", "in", "out", "smooth"}]}`，
点一律是 tuple——`path_from_mm` 用 `in != p` 决定写不写手柄，list 与 tuple 永不相等）；改完由 `vectorgeom.path_from_mm` 把包围盒
收回曲线实际范围（旋转对象的中心随框挪了就整体补回，画面上没动的锚点逐点不动）。页面坐标（已旋转）与编辑空间的互换是
`unrotate_point` / `unrotate_delta`。

**与前端不同的地方**（如实）：前端对无效 / 重复的锚点地址静默跳过，这里的纯函数照搬（保持逐数同源），**校验在操作层**
（越界、重复、非端点、`t` 不在 (0,1)、位移为 0 都抛 `EditError`）；`convert_anchors` 多一个显式目标 `to`（缺省 = 前端的「切换」）；
`add_anchors` 重复地址不去重（操作层已拒绝）。
"""

from __future__ import annotations

import math

from .vectorgeom import XY, rotate_about, rotation_of

Sub = dict
Ref = tuple[int, int]  # (sp, i)

SAME_POINT_MM = 1e-6


def _lerp(a: XY, b: XY, t: float) -> XY:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _dist(a: XY, b: XY) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def clone(subs: list[Sub]) -> list[Sub]:
    return [{"closed": sp["closed"], "nodes": [dict(n) for n in sp["nodes"]]} for sp in subs]


def _valid(subs: list[Sub], a: Ref) -> bool:
    return 0 <= a[0] < len(subs) and 0 <= a[1] < len(subs[a[0]]["nodes"])


# ------------------------------------------------------------------ 页面坐标 ↔ 编辑空间


def unrotate_point(o: dict, p: XY) -> XY:
    """页面坐标（已旋转）→ 编辑空间（未旋转）：绕对象框中心转 −rotationDeg"""
    c = (float(o["x"]) + float(o["w"]) / 2, float(o["y"]) + float(o["h"]) / 2)
    return rotate_about(p, c, -rotation_of(o))


def rotate_point(o: dict, p: XY) -> XY:
    c = (float(o["x"]) + float(o["w"]) / 2, float(o["y"]) + float(o["h"]) / 2)
    return rotate_about(p, c, rotation_of(o))


def unrotate_delta(o: dict, d: XY) -> XY:
    """页面上的一段位移换到编辑空间（只转方向）"""
    return rotate_about(d, (0.0, 0.0), -rotation_of(o))


# ------------------------------------------------------------------ 平移 / 手柄


def move_anchors(subs: list[Sub], refs: list[Ref], d: XY) -> list[Sub]:
    """平移锚点（连同它两侧的手柄）"""
    out = clone(subs)
    seen: set[Ref] = set()
    for a in refs:
        if not _valid(out, a) or a in seen:
            continue
        seen.add(a)
        n = out[a[0]]["nodes"][a[1]]
        for k in ("p", "in", "out"):
            n[k] = (n[k][0] + d[0], n[k][1] + d[1])
    return out


def move_handle(subs: list[Sub], a: Ref, which: str, pos: XY, break_smooth: bool) -> list[Sub]:
    """拖一个手柄到 `pos`。平滑点：另一侧手柄保持共线（长度不变，长度为 0 时跟着镜像）；`break_smooth`：只动这一侧，锚点变尖角。"""
    out = clone(subs)
    if not _valid(out, a):
        return out
    n = out[a[0]]["nodes"][a[1]]
    n[which] = pos
    other = "out" if which == "in" else "in"
    if break_smooth:
        n["smooth"] = False
    elif n["smooth"]:
        ln = _dist(n[other], n["p"]) or _dist(pos, n["p"])
        d = _dist(pos, n["p"])
        if d > 1e-12:
            n[other] = (
                n["p"][0] - ((pos[0] - n["p"][0]) / d) * ln,
                n["p"][1] - ((pos[1] - n["p"][1]) / d) * ln,
            )
    return out


# ------------------------------------------------------------------ 删 / 加 / 转换


def delete_anchors(subs: list[Sub], refs: list[Ref]) -> list[Sub]:
    """删锚点；闭合子路径剩不足 3 个变开放；少于两个锚点的子路径由 `path_from_mm` 去掉"""
    drop = set(refs)
    out = []
    for si, sp in enumerate(clone(subs)):
        nodes = [n for i, n in enumerate(sp["nodes"]) if (si, i) not in drop]
        closed = False if sp["closed"] and len(nodes) < 3 else sp["closed"]
        out.append({"closed": closed, "nodes": nodes})
    return out


def seg_ends(sp: Sub, seg: int) -> tuple[int, int] | None:
    """第 `seg` 段（锚点 seg → 下一个；闭合子路径的最后一段回到 0）；不存在 = None"""
    n = len(sp["nodes"])
    if seg < 0 or n < 2:
        return None
    if seg < n - 1:
        return (seg, seg + 1)
    if seg == n - 1 and sp["closed"]:
        return (seg, 0)
    return None


def insert_anchor(subs: list[Sub], sp: int, seg: int, t: float) -> list[Sub]:
    """在第 `seg` 段的参数 `t` 处加一个锚点，曲线一点不变（de Casteljau）：直线段加无手柄的点，曲线段加平滑点。"""
    out = clone(subs)
    path = out[sp] if 0 <= sp < len(out) else None
    ends = seg_ends(path, seg) if path else None
    if not path or not ends:
        return out
    a = path["nodes"][ends[0]]
    b = path["nodes"][ends[1]]
    if a["out"] == a["p"] and b["in"] == b["p"]:
        p = _lerp(a["p"], b["p"], t)
        node = {"p": p, "in": p, "out": p, "smooth": False}
    else:
        q0 = _lerp(a["p"], a["out"], t)
        q1 = _lerp(a["out"], b["in"], t)
        q2 = _lerp(b["in"], b["p"], t)
        r0 = _lerp(q0, q1, t)
        r1 = _lerp(q1, q2, t)
        node = {"p": _lerp(r0, r1, t), "in": r0, "out": r1, "smooth": True}
        a["out"] = q0
        b["in"] = q2
    path["nodes"].insert(ends[0] + 1, node)
    return out


def add_anchors(subs: list[Sub], refs: list[Ref]) -> tuple[list[Sub], list[Ref], list[Ref]]:
    """「添加锚点」：给了锚点 = 在每个锚点后面那一段的中点加一个；没给 = 每一段都加。
    回 `(新子路径, 新锚点地址（新状态下）, 被跳过的锚点（后面没有段：开放路径末端）)`。"""
    targets = (
        [a for a in refs if _valid(subs, a) and seg_ends(subs[a[0]], a[1])]
        if refs
        else [
            (si, i)
            for si, sp in enumerate(subs)
            for i in range(len(sp["nodes"]))
            if seg_ends(sp, i)
        ]
    )
    skipped = [a for a in refs if _valid(subs, a) and not seg_ends(subs[a[0]], a[1])]
    ordered = sorted(targets, key=lambda a: (a[0], -a[1]))  # 同一条子路径从后往前插
    cur = clone(subs)
    for a in ordered:
        cur = insert_anchor(cur, a[0], a[1], 0.5)
    added: list[Ref] = []
    for sp in sorted({a[0] for a in ordered}):
        mine = sorted(a[1] for a in ordered if a[0] == sp)
        added += [
            (sp, i + 1 + k) for k, i in enumerate(mine)
        ]  # 第 k 个（升序）插入点的新下标 = 原下标 + 1 + k
    return cur, added, skipped


def convert_anchors(subs: list[Sub], refs: list[Ref], to: str | None = None) -> list[Sub]:
    """转换锚点：`to=None` = 前端「切换」（选中的全是平滑点 → 都变尖角，否则都变平滑）；`to="corner"` / `"smooth"` 显式。
    平滑：手柄沿「前一个锚点 → 后一个锚点」的方向，长度取到相邻锚点距离的 1/3（开放端只看有的那一侧）。"""
    out = clone(subs)
    live = [a for a in refs if _valid(out, a)]
    if not live:
        return out
    if to == "corner":
        all_smooth = True
    elif to == "smooth":
        all_smooth = False
    else:
        all_smooth = all(out[a[0]]["nodes"][a[1]]["smooth"] for a in live)
    for a in live:
        path = out[a[0]]
        n = path["nodes"][a[1]]
        if all_smooth:
            n["in"] = n["p"]
            n["out"] = n["p"]
            n["smooth"] = False
            continue
        ln = len(path["nodes"])
        prev = (
            path["nodes"][a[1] - 1]
            if a[1] > 0
            else (path["nodes"][ln - 1] if path["closed"] else None)
        )
        nxt = (
            path["nodes"][a[1] + 1]
            if a[1] < ln - 1
            else (path["nodes"][0] if path["closed"] else None)
        )
        frm = prev["p"] if prev else n["p"]
        to_p = nxt["p"] if nxt else n["p"]
        dx, dy = to_p[0] - frm[0], to_p[1] - frm[1]
        d = math.hypot(dx, dy)
        if d < 1e-12:
            continue
        ux, uy = dx / d, dy / d
        li = _dist(prev["p"], n["p"]) / 3 if prev else 0.0
        lo = _dist(nxt["p"], n["p"]) / 3 if nxt else 0.0
        n["in"] = (n["p"][0] - ux * li, n["p"][1] - uy * li)
        n["out"] = (n["p"][0] + ux * lo, n["p"][1] + uy * lo)
        n["smooth"] = True
    return out


def average_anchors(subs: list[Sub], refs: list[Ref]) -> list[Sub]:
    """平均：选中的锚点挪到它们的平均位置（两侧手柄跟着走）"""
    live = [a for a in refs if _valid(subs, a)]
    if len(live) < 2:
        return clone(subs)
    sx = sum(subs[a[0]]["nodes"][a[1]]["p"][0] for a in live)
    sy = sum(subs[a[0]]["nodes"][a[1]]["p"][1] for a in live)
    c = (sx / len(live), sy / len(live))
    out = clone(subs)
    for a in live:
        p = out[a[0]]["nodes"][a[1]]["p"]
        out = move_anchors(out, [a], (c[0] - p[0], c[1] - p[1]))
    return out


# ------------------------------------------------------------------ 剪断


def _is_end(sp: Sub, i: int) -> bool:
    return not sp["closed"] and (i == 0 or i == len(sp["nodes"]) - 1)


def cut_at_anchors(subs: list[Sub], refs: list[Ref]) -> list[list[Sub]]:
    """剪刀：在选中的锚点处把路径断开（相邻两个都选了 = 剪掉它们之间那一段；其余 = 在这个锚点处断开，开放子路径的端点上断开等于没断）。
    回每条原子路径断出来的**几段**（按顺序）；没动的子路径原样一段。"""
    keys = set(refs)
    result: list[list[Sub]] = []
    for si, sp in enumerate(clone(subs)):
        n = len(sp["nodes"])

        def on(i: int, si: int = si) -> bool:
            return (si, i) in keys

        cut_seg: list[int] = []
        for k in range(n):
            nxt = k + 1 if k + 1 < n else (0 if sp["closed"] else -1)
            if nxt >= 0 and on(k) and on(nxt):
                cut_seg.append(k)

        def in_cut_seg(i: int, cut_seg: list[int] = cut_seg, n: int = n) -> bool:
            return i in cut_seg or (i - 1 + n) % n in cut_seg

        split_at = [i for i in range(n) if on(i) and not in_cut_seg(i) and not _is_end(sp, i)]
        if not cut_seg and not split_at:
            result.append([sp])
            continue
        breaks_after = set(cut_seg)
        split_nodes = set(split_at)
        if sp["closed"]:
            start = (min(cut_seg) + 1) % n if cut_seg else split_at[0]
            order = [(start + k) % n for k in range(n)]
        else:
            start = 0
            order = list(range(n))
        pieces: list[list[dict]] = []
        cur: list[dict] = []
        for k, i in enumerate(order):
            node = sp["nodes"][i]
            cur.append(dict(node))
            last = k == len(order) - 1
            if i in split_nodes and k > 0:
                pieces.append(cur)
                cur = [dict(node)]
            if i in breaks_after and not last:
                pieces.append(cur)
                cur = []
        if sp["closed"] and not cut_seg:
            cur.append(dict(sp["nodes"][start]))  # 闭合的在起点断开：最后一段收在起点那个锚点上
        if cur:
            pieces.append(cur)
        made = []
        for nodes in pieces:
            if len(nodes) < 2:
                continue
            a, b = nodes[0], nodes[-1]
            a["in"] = a["p"]  # 断口处没有曲线了：手柄收回锚点
            b["out"] = b["p"]
            made.append({"closed": False, "nodes": nodes})
        result.append(made)
    return result


# ------------------------------------------------------------------ 连接


def end_of(subs: list[Sub], a: Ref) -> str | None:
    """一个锚点是不是开放子路径的端点；是的话在哪一端"""
    if not 0 <= a[0] < len(subs):
        return None
    sp = subs[a[0]]
    if sp["closed"] or len(sp["nodes"]) < 2:
        return None
    return "start" if a[1] == 0 else ("end" if a[1] == len(sp["nodes"]) - 1 else None)


def join_subpaths(a: Sub, a_end: str, b: Sub, b_end: str) -> Sub:
    """把 `a` 的 `a_end` 端与 `b` 的 `b_end` 端连起来，得到一条开放子路径（a 在前、b 在后，方向随 a）。
    两端重合（< 1e-6 mm）就合成一个锚点（入手柄取 a 的、出手柄取 b 的），否则加一段直线。"""

    def rev(sp: Sub) -> list[dict]:
        return [{**m, "in": m["out"], "out": m["in"]} for m in reversed(sp["nodes"])]

    head = [dict(n) for n in a["nodes"]] if a_end == "end" else rev(a)
    tail = [dict(n) for n in b["nodes"]] if b_end == "start" else rev(b)
    x, y = head[-1], tail[0]
    if math.hypot(x["p"][0] - y["p"][0], x["p"][1] - y["p"][1]) < SAME_POINT_MM:
        head[-1] = {"p": x["p"], "in": x["in"], "out": y["out"], "smooth": False}
        return {"closed": False, "nodes": [*head, *tail[1:]]}
    # 不重合：接上的是一段直线，两个端点上没用到的手柄收回锚点，免得直线被拉弯
    head[-1] = {**x, "out": x["p"], "smooth": False}
    tail[0] = {**y, "in": y["p"], "smooth": False}
    return {"closed": False, "nodes": [*head, *tail]}


def close_subpath(sp: Sub) -> Sub:
    """闭合一条开放子路径：首尾重合就合成一个锚点"""
    nodes = [dict(n) for n in sp["nodes"]]
    a, z = nodes[0], nodes[-1]
    if len(nodes) > 2 and math.hypot(a["p"][0] - z["p"][0], a["p"][1] - z["p"][1]) < SAME_POINT_MM:
        nodes[0] = {"p": a["p"], "in": z["in"], "out": a["out"], "smooth": False}
        nodes.pop()
    return {"closed": True, "nodes": nodes}


def join_within(subs: list[Sub], a: Ref, b: Ref) -> list[Sub]:
    """同一个对象里两个端点：同一条子路径的两端 = 闭合；两条子路径的端点 = 连成一条（`pathActions.joinPaths` 的锚点编辑那一支）。
    调用方已验过两个都是端点、且不是同一个端点。"""
    subs = clone(subs)
    ea, eb = end_of(subs, a), end_of(subs, b)
    if a[0] == b[0]:
        subs[a[0]] = close_subpath(subs[a[0]])
        return subs
    first, first_end = (a, ea) if ea == "end" else (b, eb)
    second, second_end = (b, eb) if first is a else (a, ea)
    joined = join_subpaths(subs[first[0]], first_end, subs[second[0]], second_end)  # type: ignore[arg-type]
    out = [sp for k, sp in enumerate(subs) if k != second[0]]
    out[first[0] - (1 if second[0] < first[0] else 0)] = joined  # 按下标算，不按身份找
    return out


def nearest_ends(a: dict, a_subs: list[Sub], b: dict, b_subs: list[Sub]):
    """两个路径对象各自开放子路径的端点里，页面上离得最近的一对：`(a 的 (子路径, 端), b 的 (子路径, 端), 距离)`。
    遍历顺序与前端一致（子路径顺序、先 start 后 end、严格 `<`），并列时选的端点对才相同。"""

    def ends(o: dict, subs: list[Sub]):
        out = []
        for si, sp in enumerate(subs):
            if sp["closed"] or len(sp["nodes"]) < 2:
                continue
            for e in ("start", "end"):
                node = sp["nodes"][0 if e == "start" else len(sp["nodes"]) - 1]
                out.append((si, e, rotate_point(o, node["p"])))
        return out

    best = None
    for x in ends(a, a_subs):
        for y in ends(b, b_subs):
            d = math.hypot(x[2][0] - y[2][0], x[2][1] - y[2][1])
            if best is None or d < best[2]:
                best = ((x[0], x[1]), (y[0], y[1]), d)
    return best


def join_objects(keep: dict, keep_subs: list[Sub], other: dict, other_subs: list[Sub]):
    """两个路径对象 → 取离得最近的一对端点连起来：`keep` 的编辑空间里的新子路径列表；没有开放端点 = None。
    另一条的锚点先换进 `keep` 的编辑空间（页面上不动）。"""
    near = nearest_ends(keep, keep_subs, other, other_subs)
    if near is None:
        return None

    def to_keep(q: XY) -> XY:
        return unrotate_point(keep, rotate_point(other, q))

    moved = [
        {
            "closed": sp["closed"],
            "nodes": [
                {k: to_keep(n[k]) for k in ("p", "in", "out")} | {"smooth": n["smooth"]}
                for n in sp["nodes"]
            ],
        }
        for sp in other_subs
    ]
    subs = clone(keep_subs)
    (a_si, a_e), (b_si, b_e), _d = near
    subs[a_si] = join_subpaths(subs[a_si], a_e, moved[b_si], b_e)
    return [*subs, *[sp for k, sp in enumerate(moved) if k != b_si]]


# ------------------------------------------------------------------ 反转（文档形态上做，不过 path_to_mm / path_from_mm）


def reverse_path(o: dict) -> dict:
    """`pathfinder.reversePath`：锚点倒过来排，进出手柄互换，曲线逐点不变；端型字段不跟着换（起点 / 终点换了头）。"""
    subs = []
    for sp in o.get("subpaths") or []:
        nodes = []
        for n in reversed(sp.get("nodes") or []):
            m: dict = {"p": n["p"]}
            if n.get("out"):
                m["in"] = n["out"]
            if n.get("in"):
                m["out"] = n["in"]
            if n.get("smooth"):
                m["smooth"] = True
            nodes.append(m)
        subs.append({"closed": sp["closed"], "nodes": nodes})
    return {**o, "subpaths": subs}
