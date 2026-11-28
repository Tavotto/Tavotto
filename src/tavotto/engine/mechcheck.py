"""机理图的可编辑性核对（ADR 0208 §七之二 的五条，设计草案 §3；ADR 0220 §六）。纯判据，不判权益、不碰闭源。

对一批**新落地**的对象跑（`mechanism` 在 `add` 里调用，`fail` 不落地，`unknown` 如实列出，口径见 `docs/rules/repo/predicate-subject.md`）：

1. **一个看得见的东西 = 一个对象**（`one_object`）：每个 `path`（不带 `glyph.array`）的各子路径拍平后，相交 / 相接 / 嵌套（洞）算同一个东西，
   互不相连的 = 几个东西并成了一条 → fail。
2. **拆组后外观不变**：画布上的组没有任何自己的属性（只有成员带 `groupId`），除剪切组外无需判；字节级 / 像素级的对拍在测试里（`test_svg_export_groups`）。
3. **形状好改**：`native_shape`（拟合得出矩形 / 椭圆 / 直线的路径应当用原生对象）、`glyph_meta`（`glyph` 字段形状、`hash` 与几何一致、`kind` / `version`
   在 catalog 里）、`path_quality`（自由路径的锚点质量，接 6.A 的 `engine/pathquality`；该模块还不在本分支时如实列 unknown）、`thin_outline`（疑似轮廓化描边，warn）。
4. **智能分组**（`group_tree`）：顶层只有一个组、深度 ≤ `max_group_depth`、框下的块不留单成员组（顶层的示意图框可以暂时只有框线）。语义上「分得合不合理」判不了，只核结构。
5. **智能命名**（`naming`）：没有无名对象 / 无名组、同一批无重名、不是 `路径 3 / Group 2` 这类通用名。名字好不好听判不了。
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from . import agentobjects

#: 把一段三次曲线拍成折线用的段数
_FLATTEN = 8
_EPS_MM = 0.01
_GENERIC = re.compile(
    r"^\s*(路径|组|形状|文字|箭头|图层|group|path|layer|shape|text|arrow|untitled)\s*\d*\s*$",
    re.IGNORECASE,
)
KAPPA = 0.5522847498


def glyph_hash(subpaths: list) -> str:
    """生成几何（子路径节点的比例坐标，量化到 1e-4）的摘要：读时比它，对不上 = 用户改过形状（脱钩）。"""

    def q(p: object) -> list | None:
        return (
            [round(float(v), 4) for v in p]
            if isinstance(p, (list, tuple)) and len(p) == 2
            else None
        )

    canon = [
        {
            "c": bool(sp.get("closed")),
            "n": [
                {k: q(n.get(k)) for k in ("p", "in", "out") if n.get(k) is not None}
                for n in sp.get("nodes") or []
            ],
        }
        for sp in subpaths
    ]
    return hashlib.sha256(
        json.dumps(canon, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


# ------------------------------------------------------------------ 几何
def _flatten(sp: dict, w: float, h: float) -> list[tuple[float, float]]:
    nodes = sp.get("nodes") or []
    pts: list[tuple[float, float]] = []

    def m(p):
        return (p[0] * w, p[1] * h)

    def seg(a: dict, b: dict) -> None:
        p0, p3 = m(a["p"]), m(b["p"])
        p1, p2 = m(a.get("out") or a["p"]), m(b.get("in") or b["p"])
        for i in range(1, _FLATTEN + 1):
            t = i / _FLATTEN
            u = 1 - t
            pts.append(
                tuple(
                    u**3 * p0[k] + 3 * u * u * t * p1[k] + 3 * u * t * t * p2[k] + t**3 * p3[k]
                    for k in (0, 1)
                )
            )

    if not nodes:
        return pts
    pts.append(m(nodes[0]["p"]))
    for a, b in zip(nodes, nodes[1:]):
        seg(a, b)
    if sp.get("closed") and len(nodes) > 1:
        seg(nodes[-1], nodes[0])
    return pts


def _segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
        return True
    return False


def _near(p, q) -> bool:
    return math.hypot(p[0] - q[0], p[1] - q[1]) <= _EPS_MM


def _inside(pt, poly) -> bool:
    x, y = pt
    ins = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            ins = not ins
    return ins


def _touch(pa: list, ca: bool, pb: list, cb: bool) -> bool:
    for i in range(len(pa) - 1):
        for j in range(len(pb) - 1):
            if _segments_cross(pa[i], pa[i + 1], pb[j], pb[j + 1]):
                return True
    if any(_near(p, q) for p in pa[:: max(1, len(pa) // 16)] for q in pb[:: max(1, len(pb) // 16)]):
        return True
    if ca and pb and _inside(pb[0], pa):
        return True
    return bool(cb and pa and _inside(pa[0], pb))


def components(o: dict) -> int:
    """路径里互不相连的「东西」个数（相交 / 相接 / 嵌套算连通）。"""
    w, h = float(o.get("w") or 0), float(o.get("h") or 0)
    subs = [
        (_flatten(sp, w, h), bool(sp.get("closed")))
        for sp in o.get("subpaths") or []
        if len(sp.get("nodes") or []) >= 2
    ]
    parent = list(range(len(subs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(subs)):
        for j in range(i + 1, len(subs)):
            if find(i) != find(j) and _touch(subs[i][0], subs[i][1], subs[j][0], subs[j][1]):
                parent[find(i)] = find(j)
    return len({find(i) for i in range(len(subs))}) if subs else 0


def _fits_native(o: dict, tol: float = 0.05) -> str | None:
    """路径是不是其实是一个矩形 / 椭圆 / 直线（容差 0.05 mm）：是 = 返回该用的原生形状。"""
    subs = o.get("subpaths") or []
    if len(subs) != 1:
        return None
    nodes = subs[0].get("nodes") or []
    w, h = float(o.get("w") or 0), float(o.get("h") or 0)
    tx, ty = tol / max(w, tol), tol / max(h, tol)

    def at(p, x, y):
        return abs(p[0] - x) <= tx and abs(p[1] - y) <= ty

    if not subs[0].get("closed"):
        if len(nodes) == 2 and not any(n.get("in") or n.get("out") for n in nodes):
            return "line"
        return None
    if len(nodes) == 4:
        straight = not any(n.get("in") or n.get("out") for n in nodes)
        corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
        ps = [n["p"] for n in nodes]
        for k in range(4):  # 起点可以是任一角，方向任意
            for step in (1, -1):
                if all(at(ps[(k + step * i) % 4], *corners[i]) for i in range(4)):
                    if straight:
                        return "rect"
        mids = [(0.5, 0), (1, 0.5), (0.5, 1), (0, 0.5)]
        for k in range(4):
            for step in (1, -1):
                if all(at(ps[(k + step * i) % 4], *mids[i]) for i in range(4)) and all(
                    n.get("in") and n.get("out") for n in nodes
                ):
                    return "ellipse"
    return None


# ------------------------------------------------------------------ 五条
def _naming(objs: list[dict], group_names: dict[str, str]) -> list[dict]:
    out = []
    seen: dict[tuple, str] = {}
    for o in objs:
        name = o.get("name")
        if not isinstance(name, str) or not name.strip():
            out.append({"check": "naming", "item": o.get("id"), "detail": {"reason": "unnamed"}})
            continue
        if _GENERIC.match(name):
            out.append(
                {"check": "naming", "item": o.get("id"), "detail": {"reason": "generic_name"}}
            )
        key = (tuple(agentobjects._group_path_of(o)), name)  # noqa: SLF001
        if key in seen:
            out.append(
                {
                    "check": "naming",
                    "item": o.get("id"),
                    "detail": {"reason": "duplicate", "with": seen[key]},
                }
            )
        else:
            seen[key] = o.get("id")
    for gid, n in group_names.items():
        if not n.strip():
            out.append({"check": "naming", "item": gid, "detail": {"reason": "unnamed_group"}})
        elif _GENERIC.match(n):
            out.append({"check": "naming", "item": gid, "detail": {"reason": "generic_name"}})
    return out


def _group_tree(new: list[dict], everything: list[dict], max_depth: int) -> list[dict]:
    out = []
    chains = [agentobjects._group_path_of(o) for o in new]  # noqa: SLF001
    tops = {c[0] for c in chains if c}
    if len(tops) > 1:
        out.append(
            {
                "check": "group_tree",
                "item": None,
                "detail": {"reason": "multiple_top_groups", "count": len(tops)},
            }
        )
    for o, c in zip(new, chains):
        if len(c) > max_depth:
            out.append(
                {
                    "check": "group_tree",
                    "item": o.get("id"),
                    "detail": {"reason": "too_deep", "depth": len(c)},
                }
            )
    members: dict[str, int] = {}
    for o in everything:
        for g in agentobjects._group_path_of(o):  # noqa: SLF001
            members[g] = members.get(g, 0) + 1
    tops_only = {
        c[0] for c in chains if c
    }  # 示意图框本身（顶层组）可以暂时只有框线一个成员：先画框、后补内容
    for g in {g for c in chains for g in c} - tops_only:
        if members.get(g, 0) < 2:
            out.append(
                {"check": "group_tree", "item": g, "detail": {"reason": "single_member_group"}}
            )
    return out


def check(
    new: list[dict],
    everything: list[dict] | None = None,
    *,
    catalog: dict | None = None,
    require_names: bool = True,
) -> dict:
    """对新落地的对象跑五条判据 → `{"failed": [...], "warned": [...], "unknown": [...]}`（每项 `{check, item, detail}`）。
    `everything` = 落地后画布上的全部对象（数组成员要在整张画布里数）；`catalog` = `mechanism.catalog` 的回包（`kind → {versions}`），没有就不核 kind / version。"""
    failed: list[dict] = []
    warned: list[dict] = []
    unknown: list[dict] = []
    max_depth = agentobjects.table()["budgets"]["max_group_depth"]
    free_paths = 0
    for o in new:
        if o.get("type") != "path":
            continue
        g = o.get("glyph")
        if isinstance(g, dict):
            ok = (
                isinstance(g.get("kind"), str)
                and isinstance(g.get("version"), int)
                and not isinstance(g.get("version"), bool)
                and isinstance(g.get("params"), dict)
                and isinstance(g.get("hash"), str)
            )
            if not ok:
                failed.append(
                    {"check": "glyph_meta", "item": o["id"], "detail": {"reason": "malformed"}}
                )
            else:
                if g["hash"] != glyph_hash(o.get("subpaths") or []):
                    failed.append(
                        {
                            "check": "glyph_meta",
                            "item": o["id"],
                            "detail": {"reason": "hash_mismatch"},
                        }
                    )
                spec = (catalog or {}).get(g["kind"])
                if catalog is not None and (
                    spec is None or g["version"] not in (spec.get("versions") or ())
                ):
                    failed.append(
                        {
                            "check": "glyph_meta",
                            "item": o["id"],
                            "detail": {"reason": "unknown_kind_or_version"},
                        }
                    )
            if not g.get("array"):
                if components(o) > 1:
                    failed.append(
                        {
                            "check": "one_object",
                            "item": o["id"],
                            "detail": {"components": components(o)},
                        }
                    )
                native = _fits_native(o)
                if native:  # 图元回了个其实是矩形 / 椭圆 / 直线的轮廓：该用原生对象
                    failed.append(
                        {"check": "native_shape", "item": o["id"], "detail": {"use": native}}
                    )
            continue
        free_paths += 1
        n = components(o)
        if n > 1:
            failed.append({"check": "one_object", "item": o["id"], "detail": {"components": n}})
        native = _fits_native(o)
        if native:
            failed.append({"check": "native_shape", "item": o["id"], "detail": {"use": native}})
        w, h = float(o.get("w") or 0), float(o.get("h") or 0)
        if o.get("fill") and min(w, h) > 0 and max(w, h) / min(w, h) > 50 and min(w, h) < 0.5:
            warned.append(
                {
                    "check": "thin_outline",
                    "item": o["id"],
                    "detail": {"reason": "looks_like_outlined_stroke"},
                }
            )
    if free_paths:
        try:
            from . import pathquality  # noqa: F401 — 6.A（路径质量核对）合入后接上
        except ImportError:
            unknown.append(
                {
                    "check": "path_quality",
                    "item": None,
                    "detail": {"reason": "pathquality_missing", "paths": free_paths},
                }
            )
        else:  # pragma: no cover — 接口占位：6.A 合入后在这里逐个路径调它
            unknown.append(
                {"check": "path_quality", "item": None, "detail": {"reason": "not_wired"}}
            )
    if new:
        failed += _group_tree(new, everything or new, max_depth)
    if require_names:
        gnames: dict[str, str] = {}
        for o in new:
            for g, n in (o.get("groupNames") or {}).items():
                gnames.setdefault(g, n)
            for g in agentobjects._group_path_of(o):  # noqa: SLF001
                gnames.setdefault(g, agentobjects.group_name_of(new, g))
        failed += _naming(new, gnames)
    return {"failed": failed, "warned": warned, "unknown": unknown}
