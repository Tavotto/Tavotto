"""自由路径的质量核对（ADR 0208 §七之二 第 3 条「干净路径」；第 ⑥ 期 6.A）。

纯函数、纯标准库、无 I/O：输入一个 `path` 对象（Tavotto 的 `subpaths` 节点格式，比例坐标 + 页面 mm 外框），
输出逐条问题与一个三档结论。**本期只有判据，没有接进任何写入路径**（`add` / `svgimport` 的接入是后面的期）；
不过就让调用方回 `next` 提示用补丁操作 `simplify` 自己重写，**这里不偷偷简化、也不改入参**。

适用对象：`type == "path"` 且**不带 `glyph`**（带 `glyph` 的图元路径由参数重生成，不核）。别的对象回 `skipped`。

判据（数是起步值，要用真图校准；全部集中在下面的常量里）：

1. `extremum_off_anchor`：每段三次曲线内部若有比两端锚点更远的极值（逐轴求导根），说明锚点没落在极值点上；
   超出 `EXTREMUM_WARN_MM` 记 warn，超出 `EXTREMUM_FAIL_MM` 记 fail。
   **拐点没判**：「锚点落在拐点」没有不依赖语义的判法（直线接圆弧处的切换点就是拐点却不该多出锚点），判不出就不判。
2. `smooth_handles_not_collinear`：`smooth` 的锚点两侧手柄夹角（偏离「一条直线」的角度）超过 `SMOOTH_WARN_DEG` 记 warn，
   超过 `SMOOTH_FAIL_DEG` 记 fail；任一侧手柄短于 `EPS_MM`（等于没有手柄）不判。
3. `zero_length_segment`（一段的四个控制点全部重合）与 `coincident_anchors`（相邻锚点重合）：距离 < `EPS_MM`，fail。
   闭合子路径把「最后一个锚点 → 第一个锚点」也算相邻（常见毛病：最后一个锚点是第一个的复本）。
4. `too_many_anchors`：每个子路径锚点数 > 「可见拐角数 × 2 + 8」，fail。拐角 = 把子路径拍平后转角 > `CORNER_DEG` 的点。
5. `not_really_closed`：没标闭合、但首尾锚点重合（< `EPS_MM`）→ 看起来闭合其实没闭合，fail；
   `filled_but_open`：有填充却没闭合（渲染时被隐式闭合，编辑时看不出缺口），warn。
6. `too_few_anchors`：子路径少于 2 个锚点（画不出线），fail。

不在这里的：「子路径不连通的拆开」是可编辑性第 1 条（`mechcheck`，6.B），与本判据共用的是同一份几何，但归那一期。

结论三档：`clean`（没有任何问题）、`acceptable`（只有 warn：能用，建议再整理）、`fail`（有 fail：不落地）。
ADR 原文只写了「过 / 不过」，没有给这三档命名与界线；这里 warn / fail 的划分是实现里的推荐值，不是 ADR 拍板的口径。
"""

from __future__ import annotations

import math

#: 锚点 / 手柄「重合」的距离（页面 mm）
EPS_MM = 0.01
#: 极值超出两端锚点多少 mm 算「锚点没落在极值点上」：warn / fail
EXTREMUM_WARN_MM = 0.1
EXTREMUM_FAIL_MM = 1.0
#: 平滑点两侧手柄偏离共线的角度（度）：warn / fail
SMOOTH_WARN_DEG = 1.0
SMOOTH_FAIL_DEG = 5.0
#: 拍平后转角超过这个度数算一个「可见拐角」
CORNER_DEG = 30.0
#: 每段三次曲线拍平成多少步（只用来数拐角）
FLATTEN_STEPS = 16
#: 锚点上限 = 拐角数 × ANCHOR_PER_CORNER + ANCHOR_BASE
ANCHOR_PER_CORNER = 2
ANCHOR_BASE = 8

CLEAN, ACCEPTABLE, FAIL, SKIPPED = "clean", "acceptable", "fail", "skipped"

XY = tuple[float, float]


def applies_to(o: object) -> bool:
    """只核自由轮廓：`path` 且不带 `glyph`。"""
    return isinstance(o, dict) and o.get("type") == "path" and not o.get("glyph")


# ------------------------------------------------------------------ 读入（页面 mm）


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _pt(v: object) -> XY | None:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        a, b = _num(v[0]), _num(v[1])
        if a is not None and b is not None:
            return (a, b)
    return None


def _read(o: dict) -> list[dict] | None:
    """子路径 → 页面 mm（未旋转：距离、角度、轴向极值都在对象自己的坐标系里量）；读不成数的锚点丢掉。"""
    box = [_num(o.get(k)) for k in ("x", "y", "w", "h")]
    if None in box or not isinstance(o.get("subpaths"), list):
        return None
    x, y, w, h = box
    out = []
    for sp in o["subpaths"]:
        if not isinstance(sp, dict):
            continue

        def at(q: XY) -> XY:
            return (x + q[0] * w, y + q[1] * h)

        nodes = []
        for n in sp.get("nodes") or ():
            p = _pt(n.get("p")) if isinstance(n, dict) else None
            if p is None:
                continue
            nodes.append(
                {
                    "p": at(p),
                    "in": at(_pt(n.get("in")) or p),
                    "out": at(_pt(n.get("out")) or p),
                    "smooth": n.get("smooth") is True,
                }
            )
        out.append({"closed": sp.get("closed") is True, "nodes": nodes})
    return out


def _dist(a: XY, b: XY) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ------------------------------------------------------------------ 几何


def _segments(sp: dict) -> list[tuple[int, int]]:
    """相邻锚点对（下标）；闭合的多一段「末 → 首」。"""
    n = len(sp["nodes"])
    segs = [(i, i + 1) for i in range(n - 1)]
    if sp["closed"] and n >= 2:
        segs.append((n - 1, 0))
    return segs


def _cubic(p0: XY, c1: XY, c2: XY, p1: XY, t: float) -> XY:
    u = 1 - t
    return tuple(  # type: ignore[return-value]
        u**3 * p0[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t**3 * p1[k] for k in (0, 1)
    )


def _axis_overshoot(a: float, b: float, c: float, d: float) -> float:
    """一维三次贝塞尔：内部极值超出两端 [min(a,d), max(a,d)] 多少（没有 = 0）。"""
    qa, qb, qc = -a + 3 * b - 3 * c + d, 2 * (a - 2 * b + c), b - a
    roots: list[float] = []
    if abs(qa) < 1e-12:
        if abs(qb) > 1e-12:
            roots.append(-qc / qb)
    else:
        disc = qb * qb - 4 * qa * qc
        if disc >= 0:
            s = math.sqrt(disc)
            roots += [(-qb + s) / (2 * qa), (-qb - s) / (2 * qa)]
    lo, hi = min(a, d), max(a, d)
    worst = 0.0
    for t in roots:
        if 1e-9 < t < 1 - 1e-9:
            u = 1 - t
            v = u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d
            worst = max(worst, v - hi, lo - v)
    return worst


def _flatten(sp: dict) -> list[XY]:
    """拍平成折线（去掉相邻重复点）；闭合时不重复首点。"""
    nodes = sp["nodes"]
    if not nodes:
        return []
    pts: list[XY] = [nodes[0]["p"]]
    for i, j in _segments(sp):
        a, b = nodes[i], nodes[j]
        for s in range(1, FLATTEN_STEPS + 1):
            pts.append(_cubic(a["p"], a["out"], b["in"], b["p"], s / FLATTEN_STEPS))
    dedup: list[XY] = []
    for q in pts:
        if not dedup or _dist(q, dedup[-1]) > 1e-9:
            dedup.append(q)
    if sp["closed"] and len(dedup) > 1 and _dist(dedup[0], dedup[-1]) <= 1e-9:
        dedup.pop()
    return dedup


def corner_count(sp: dict) -> int:
    """拍平后转角 > `CORNER_DEG` 的点数（开放子路径的两端不算）。"""
    pts = _flatten(sp)
    n = len(pts)
    if n < 3:
        return 0
    rng = range(n) if sp["closed"] else range(1, n - 1)
    corners = 0
    for i in rng:
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1, v2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-12 or n2 < 1e-12:
            continue
        cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        if math.degrees(math.acos(cos)) > CORNER_DEG:
            corners += 1
    return corners


def anchor_limit(corners: int) -> int:
    return corners * ANCHOR_PER_CORNER + ANCHOR_BASE


# ------------------------------------------------------------------ 判据


def _issue(
    code: str, severity: str, si: int, ai: int | None, value: float | None, message: str
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "subpath": si,
        "anchor": ai,
        "value": None if value is None else round(value, 3),
        "message": message,
    }


def _where(si: int, ai: int | None) -> str:
    return f"第 {si + 1} 个子路径" + (f"的第 {ai + 1} 个锚点" if ai is not None else "")


def _check_subpath(si: int, sp: dict, fill: bool) -> list[dict]:
    nodes, out = sp["nodes"], []
    if len(nodes) < 2:
        return [
            _issue(
                "too_few_anchors",
                "fail",
                si,
                None,
                len(nodes),
                f"{_where(si, None)}不到 2 个锚点，画不出线。",
            )
        ]
    for i, j in _segments(sp):
        a, b = nodes[i], nodes[j]
        gap = _dist(a["p"], b["p"])
        if gap < EPS_MM:
            if _dist(a["out"], a["p"]) < EPS_MM and _dist(b["in"], b["p"]) < EPS_MM:
                out.append(
                    _issue(
                        "zero_length_segment",
                        "fail",
                        si,
                        i,
                        gap,
                        f"{_where(si, i)}到下一个锚点是一段零长度的线，删掉多余的锚点。",
                    )
                )
            else:
                out.append(
                    _issue(
                        "coincident_anchors",
                        "fail",
                        si,
                        i,
                        gap,
                        f"{_where(si, i)}与相邻锚点重合（相距 {gap:.3f} mm），合并成一个。",
                    )
                )
            continue
        over = max(_axis_overshoot(a["p"][k], a["out"][k], b["in"][k], b["p"][k]) for k in (0, 1))
        if over > EXTREMUM_WARN_MM:
            sev = "fail" if over > EXTREMUM_FAIL_MM else "warn"
            out.append(
                _issue(
                    "extremum_off_anchor",
                    sev,
                    si,
                    i,
                    over,
                    f"{_where(si, i)}之后的曲线在两个锚点之间多凸出 {over:.2f} mm，锚点应落在曲线的极值点上。",
                )
            )
    for i, nd in enumerate(nodes):
        if not nd["smooth"]:
            continue
        u = (nd["p"][0] - nd["in"][0], nd["p"][1] - nd["in"][1])
        v = (nd["out"][0] - nd["p"][0], nd["out"][1] - nd["p"][1])
        nu, nv = math.hypot(*u), math.hypot(*v)
        if nu < EPS_MM or nv < EPS_MM:
            continue
        cos = max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))
        ang = math.degrees(math.acos(cos))
        if ang > SMOOTH_WARN_DEG:
            sev = "fail" if ang > SMOOTH_FAIL_DEG else "warn"
            out.append(
                _issue(
                    "smooth_handles_not_collinear",
                    sev,
                    si,
                    i,
                    ang,
                    f"{_where(si, i)}标成平滑点，但两侧手柄没有在一条直线上（差 {ang:.1f}°）。",
                )
            )
    first, last = nodes[0]["p"], nodes[-1]["p"]
    if not sp["closed"]:
        if _dist(first, last) < EPS_MM:
            out.append(
                _issue(
                    "not_really_closed",
                    "fail",
                    si,
                    len(nodes) - 1,
                    _dist(first, last),
                    f"{_where(si, None)}首尾锚点重合却没有标成闭合，应闭合并去掉重复的末锚点。",
                )
            )
        elif fill:
            out.append(
                _issue(
                    "filled_but_open",
                    "warn",
                    si,
                    None,
                    _dist(first, last),
                    f"{_where(si, None)}有填充却没有闭合（首尾相距 {_dist(first, last):.2f} mm）。",
                )
            )
    limit = anchor_limit(corner_count(sp))
    if len(nodes) > limit:
        out.append(
            _issue(
                "too_many_anchors",
                "fail",
                si,
                None,
                len(nodes) - limit,
                f"{_where(si, None)}有 {len(nodes)} 个锚点，按它的拐角数最多 {limit} 个，用更少的锚点重写。",
            )
        )
    return out


def check_path(o: object) -> dict:
    """核一个路径对象 → `{"verdict", "issues", "subpaths"}`。

    `verdict`：`clean` / `acceptable` / `fail` / `skipped`（不是自由路径，或读不成路径）。
    `issues` 每条：`code`、`severity`（`warn` / `fail`）、`subpath`（0 起）、`anchor`（0 起；整条子路径的问题为 None）、`value`（差多少）、`message`（一句人话，序号 1 起）。
    不改入参。"""
    if not applies_to(o):
        return {"verdict": SKIPPED, "issues": [], "subpaths": 0}
    subs = _read(o)  # type: ignore[arg-type]
    if subs is None:
        return {"verdict": SKIPPED, "issues": [], "subpaths": 0}
    fill = bool(o.get("fill"))  # type: ignore[union-attr]
    issues: list[dict] = []
    for si, sp in enumerate(subs):
        issues += _check_subpath(si, sp, fill)
    if any(i["severity"] == "fail" for i in issues):
        verdict = FAIL
    elif issues:
        verdict = ACCEPTABLE
    else:
        verdict = CLEAN
    return {"verdict": verdict, "issues": issues, "subpaths": len(subs)}
