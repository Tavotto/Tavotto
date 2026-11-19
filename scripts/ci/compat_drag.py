"""CompatBench 的「拖动」阶段：位置不是数据的元素，要么能拖，要么说得出为什么（ADR 0100）。

为什么要这一级：2026-11-18 用户来图（地图上 30 条 `transform=ax.transAxes` 的虚线引线）只能改色改粗、
拖不动。元素普查（`artist_census`）只抓「没登记」，这些线登记了、能力却按**所在列表**（`ax.lines` =
数据）推断错了——普查和别的阶段都看不见这一类，用户来信才知道。同一类后来又在 transAxes 的散点、
线组、图片、表格上各找到一次（#2018 / #2024 / 表格那一条）。

判据按**行为**，不读产品的判定函数（`manifest._layout_anchored_*`）——尺子不能借被量的东西：

* 对每个子图分别把 x、y 范围平移四分之一个跨度，看元素**相对它所在子图框**的位置变没变（等比
  子图换范围时框本身会动）。位置量画出来的几何（`geometry` 外接框），没有几何才退回 `bbox`；
* 两次都没动 = 位置不随数据。它必须 `draggable`、或由外框代拖（`drag_host`）、或有端点手柄
  （`arrow_endpoints`）、或在 `unsupported_props` 里给了 `pos_frac` 的理由——理由是产品自己的事实，
  不是这里的白名单；
* 整张图上的元素（`fig.*`）没有数据坐标，同一组角色直接要求同一件事；
* 没有数据点的元素（箱线图里没有离群点的那条线：数据形状 `[0, 2]`，框只是占位）不算；
* 每次扰动之后撤销（空列表）：元素表必须回到基线——比较器与容差是写回事务的那一份
  （`app._compare_manifests`），回不去本身就是产品缺陷，记在本阶段。

纯函数 + 一个 worker 句柄；`compat_matrix.stage_drag` 与 `scripts/dev/drag_oracle.py` 共用这一份。
"""

from __future__ import annotations

#: 会被用户当成「摆在图上的东西」的角色。轴线、刻度、网格、色条成员、图例项是结构件，不在里面。
CANDIDATE_ROLES = frozenset(
    {
        "line",
        "scatter",
        "fill",
        "linecoll",
        "collection",
        "image",
        "patch",
        "arrow_patch",
        "artist",
        "text",
    }
)
#: 平移子图范围的比例（跨度的四分之一）。
SHIFT = 0.25
#: 「没动」的容差（子图框分数）。
MOVE_TOL = 1e-4

CRITERION = (
    "shift each axes' x then y limits by a quarter span; an element whose drawn extent does not "
    "move relative to its axes box (or any figure-level element) of a placeable role must be "
    "draggable, delegate to a drag_host, expose arrow_endpoints, or carry a pos_frac reason in "
    "unsupported_props; every perturbation is undone and the manifest must return to baseline"
)


def _extent(el: dict) -> list[float] | None:
    g = el.get("geometry")
    pts = []
    if isinstance(g, dict):
        for path in g.get("paths") or []:
            pts.extend(path.get("points") or [])
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return [min(xs), min(ys), max(xs), max(ys)]
    bb = el.get("bbox")
    if bb:
        return [bb[0], bb[1], bb[0] + bb[2], bb[1] + bb[3]]
    return None


def _relative(el: dict, host: dict | None) -> list[float] | None:
    e = _extent(el)
    if e is None:
        return None
    if host is None or not host.get("bbox"):
        return e
    x, y, w, h = host["bbox"]
    if w <= 0 or h <= 0:
        return e
    return [(e[0] - x) / w, (e[1] - y) / h, (e[2] - x) / w, (e[3] - y) / h]


def _field(el: dict, prop: str):
    for f in el.get("editable") or []:
        if f.get("prop") == prop:
            return f.get("value")
    return None


def host_gid(gid: str) -> str | None:
    head = gid.split(".", 1)[0]
    return head if head.startswith("axes_") and "." in gid else None


def has_no_data(el: dict) -> bool:
    """数据指纹里每一部分都是零行：画不出任何东西，框只是占位。"""
    parts = ((el.get("data_fingerprint") or {}).get("parts")) or {}
    shapes = [p.get("shape") for p in parts.values() if isinstance(p, dict)]
    return bool(shapes) and all(isinstance(s, list) and s and s[0] == 0 for s in shapes)


def excuse(el: dict) -> str | None:
    if el.get("draggable"):
        return "draggable"
    if el.get("drag_host"):
        return "drag_host"
    if el.get("arrow_endpoints"):
        return "arrow_endpoints"
    for u in el.get("unsupported_props") or []:
        if u.get("prop") == "pos_frac":
            return f"reason:{u.get('reason')}"
    return None


def _shifted(lim) -> list[float]:
    lo, hi = float(lim[0]), float(lim[1])
    d = (hi - lo) * SHIFT
    return [lo + d, hi + d]


def _elements(resp: dict) -> dict:
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


def _restore_drift(base_man: dict, back_man: dict) -> str | None:
    """撤销之后与基线的几何分歧——用写回事务放行 / 阻断用户写回的同一个比较器
    （`app._compare_manifests`，replay 阶段也用它），容差一字不差：另起一套只会让两个阶段各绿各的。"""
    from tavotto.app import _compare_manifests

    diffs, _compared = _compare_manifests(base_man, back_man)
    if not diffs:
        return None
    d = diffs[0]
    return (
        f"{d['gid'] or '<figure>'}.{d['field']}: {d['hot']} vs {d['fresh']}（共 {len(diffs)} 处）"
    )


def audit(worker, stem: str, base: dict | None = None) -> dict:
    """一张图的拖动普查。`base` = 零 patch 的元素表（{gid: element}），不给就现取。

    返回 `{findings, excused, restore_errors, skipped}`；`findings` 为空、`restore_errors` 为空才算过。
    """
    base_man = worker.override(stem, [])["manifest"]
    if base is None:
        base = {e["gid"]: e for e in base_man["elements"]}
    axes = {g: e for g, e in base.items() if e.get("role") == "axes"}
    moved: dict[str, bool] = {}
    skipped: list[str] = []
    restore_errors: list[str] = []
    for ax_gid, ax_el in axes.items():
        members = [
            g for g, e in base.items() if host_gid(g) == ax_gid and e.get("role") in CANDIDATE_ROLES
        ]
        if not members:
            continue
        for prop in ("xlim", "ylim"):
            lim = _field(ax_el, prop)
            if not (isinstance(lim, (list, tuple)) and len(lim) == 2):
                skipped.append(f"{ax_gid}.{prop}: 没有可回灌的范围")
                continue
            try:
                got = worker.override(stem, [{"gid": ax_gid, "prop": prop, "value": _shifted(lim)}])
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{ax_gid}.{prop}: {str(exc)[:160]}")
                continue
            if got.get("warnings"):
                skipped.append(f"{ax_gid}.{prop}: {str(got['warnings'][0])[:160]}")
            else:
                after = _elements(got)
                for g in members:
                    a0 = _relative(base[g], base.get(ax_gid))
                    a1 = _relative(after[g], after.get(ax_gid)) if g in after else None
                    if (
                        a0 is None
                        or a1 is None
                        or any(abs(p - q) > MOVE_TOL for p, q in zip(a0, a1))
                    ):
                        moved[g] = True  # 随数据动 / 量不出 / 消失：不报
                    else:
                        moved.setdefault(g, False)
            drift = _restore_drift(base_man, worker.override(stem, [])["manifest"])
            if drift:
                restore_errors.append(f"{ax_gid}.{prop}: {drift}")
    findings: list[dict] = []
    excused: list[dict] = []
    for g, e in base.items():
        if e.get("role") not in CANDIDATE_ROLES or has_no_data(e):
            continue
        host = host_gid(g)
        if host is not None:
            if moved.get(g, True):
                continue
        elif not g.startswith("fig."):
            continue
        row = {"gid": g, "role": e.get("role"), "label": e.get("label")}
        why = excuse(e)
        if why:
            excused.append({**row, "why": why})
        else:
            findings.append(row)
    return {
        "findings": findings,
        "excused": excused,
        "restore_errors": restore_errors,
        "skipped": skipped,
    }


def verdict(result: dict) -> bool:
    return not result["findings"] and not result["restore_errors"]
