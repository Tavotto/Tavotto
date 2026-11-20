"""一键排版（ADR 0199）：引擎侧编排——收集几何、问闭源求解器、校验操作、按规范修、量验收指标。

**只算不写**（照 `/api/engine/specfix`）：每张图的渲染由调用方给的 `render(patches)` 做；本模块不碰文档、不落盘。
事务结束时，每张图 worker 的热态要么是 B0，要么是回给前端的那份列表；任何一次渲染不干净由调用方作废 worker。

权益判断只在这里（`license.has("quick-layout")`，ADR 0199 §九）；免费能力（specfix、拖边改图幅、加标签）
的模块里不出现任何判断。sidecar 只拿到数字与结构：角色与外框，不给文字、数据、文件名或路径（ADR 0198 §三）。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import license as engine_license, prohost, specfix

LOG = logging.getLogger("tavotto.quicklayout")

FEATURE = "quick-layout"
MAX_ROUNDS = 3
PT_MM = 25.4 / 72.0
#: 验收阈值（ADR 0199 §六）
ALIGN_TOL_MM = 0.1 * PT_MM
GAP_TOL_MM = 0.1
WIDTH_TOL_MM = 0.1
#: 页面四周文字外沿到页边的空隙（用户没设安全边距时；ADR 0199 §六，维护者 2026-11-20 定）
DEFAULT_PADDING_MM = 2.0
#: 文字外沿离图框至少这么远：字形会略微超出测得的外框，贴着图框会被裁（2026-11-20 走查）
EDGE_MIN_MM = 0.2
EDGE_TOL_MM = 0.05
#: 按规范修了哪几类（给胶囊那一行灰字）：属性 → 类别码
SPEC_CATEGORY = {
    "fontfamily": "font_family",
    "fontsize": "font_size",
    "title_fontsize": "font_size",
    "tick_fontsize": "font_size",
    "linewidth": "line_width",
    "width": "line_width",
    "direction": "tick_direction",
    "frameon": "legend_frame",
    "weight": "text_weight",
}
#: 求解器只看得到这些角色（ADR 0199 §四）
ELEMENT_ROLES = frozenset({"axes", "colorbar"})
TARGETS = ("single", "double", "page")
#: 画布：keep = 在用户定好的画布里排；fit = 页宽取期刊栏宽、页高跟着内容（ADR 0199 §一，维护者 2026-11-20）
CANVAS_MODES = ("keep", "fit")
MAX_COLUMNS = 6
OPS = frozenset({"panel.place", "figure.size", "axes.position", "label.place"})
MAX_PAGE_MM = 2000.0


class QuickLayoutError(Exception):
    """方案作废。`code` 给前端选一句话：`not_licensed` / `unavailable` / `too_few` / `invalid_plan` / `not_converged` / `render_failed`。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


Render = Callable[[list], dict]


@dataclass
class Panel:
    key: str
    rel_id: str
    base: list
    box: list[float]
    render: Render
    patches: list = field(default_factory=list)
    manifest: dict | None = None
    size_mm: list[float] = field(default_factory=list)
    gid_of: dict[str, str] = field(default_factory=dict)  # 元素短号 → gid
    equal_aspect: bool = False
    specfix_ok: bool | None = None


def licensed() -> bool:
    return engine_license.has(FEATURE)


# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------
def _finite(*vals: object) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals
    )


def _upsert(patches: list, gid: str, prop: str, value) -> list:
    out = [p for p in patches if not (p.get("gid") == gid and p.get("prop") == prop)]
    out.append({"gid": gid, "prop": prop, "value": value})
    return out


def _figure(manifest: dict) -> dict:
    for e in manifest.get("elements") or []:
        if e.get("role") == "figure":
            return e
    raise QuickLayoutError("render_failed", "no figure element")


def _main_axes(manifest: dict) -> list[dict]:
    """主坐标轴：去掉色条轴；外框完全落在另一个主轴里面的（inset）也去掉（ADR 0199 §三）。"""
    axes = [
        e
        for e in manifest.get("elements") or []
        if e.get("role") == "axes" and not e.get("is_colorbar") and len(e.get("bbox") or []) == 4
    ]

    def inside(a: dict, b: dict) -> bool:
        ax, ay, aw, ah = a["bbox"]
        bx, by, bw, bh = b["bbox"]
        return (
            a is not b
            and ax >= bx
            and ay >= by
            and ax + aw <= bx + bw
            and ay + ah <= by + bh
            and aw * ah < bw * bh
        )

    return [a for a in axes if not any(inside(a, b) for b in axes)]


def _union(rects: list[list[float]]) -> list[float]:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return [x0, y0, x1, y1]


def _equal_aspect(manifest: dict) -> bool:
    for e in manifest.get("elements") or []:
        if e.get("role") != "axes":
            continue
        for ed in e.get("editable") or []:
            if ed.get("prop") == "aspect" and ed.get("value") not in (None, "auto"):
                return True
    return False


def _ink(manifest: dict) -> list[float]:
    ink = _figure(manifest).get("ink_bbox")
    if isinstance(ink, list) and len(ink) == 4 and _finite(*ink):
        return [float(v) for v in ink]
    return [0.0, 0.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# 与求解器之间的输入输出
# ---------------------------------------------------------------------------
def _panel_input(ref: str, p: Panel) -> dict:
    m = p.manifest or {}
    elements = []
    p.gid_of = {}
    n = 0
    for e in m.get("elements") or []:
        if e.get("role") != "axes" or len(e.get("bbox") or []) != 4:
            continue
        n += 1
        er = f"e{n}"
        p.gid_of[er] = e["gid"]
        role = "colorbar" if e.get("is_colorbar") else "axes"
        main = role == "axes" and any(e is a for a in _main_axes(m))
        elements.append(
            {"ref": er, "role": role, "main": main, "bbox": [round(float(v), 6) for v in e["bbox"]]}
        )
    return {
        "ref": ref,
        "kind": "figure",
        "box": [round(v, 4) for v in p.box],
        "size_mm": [round(float(v), 4) for v in p.size_mm],
        "aspect_locked": True,
        "equal_aspect": p.equal_aspect,
        "ink_bbox": [round(v, 6) for v in _ink(m)],
        "elements": elements,
    }


def _apply_ops(ops: object, refs: dict[str, Panel], page: dict) -> dict[str, list]:
    """校验并落成各图候选覆盖与新摆放；任何一条不合规整份作废（ADR 0199 §五）。返回 `{"labels": [...]}`。"""
    if not isinstance(ops, list):
        raise QuickLayoutError("invalid_plan", "ops not a list")
    labels: list[dict] = []
    staged: dict[str, dict] = {ref: {"box": None, "size": None, "pos": {}} for ref in refs}
    # 页面随内容收紧（ADR 0199 §五），这里只防荒谬值
    pw = ph = MAX_PAGE_MM
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise QuickLayoutError("invalid_plan", "unknown op")
        ref = op.get("ref")
        if ref not in refs:
            raise QuickLayoutError("invalid_plan", "unknown ref")
        kind = op["op"]
        if kind == "panel.place":
            x, y, w, h = (op.get(k) for k in ("x", "y", "w", "h"))
            if (
                not _finite(x, y, w, h)
                or w <= 0
                or h <= 0
                or x < -0.01
                or y < -0.01
                or x + w > pw + 0.01
                or y + h > ph + 0.01
            ):
                raise QuickLayoutError("invalid_plan", "panel outside page")
            staged[ref]["box"] = [float(x), float(y), float(w), float(h)]
        elif kind == "figure.size":
            w, h = op.get("w"), op.get("h")
            if not _finite(w, h) or not (5 <= w <= 1000 and 5 <= h <= 1000):
                raise QuickLayoutError("invalid_plan", "bad size")
            staged[ref]["size"] = [round(float(w), 3), round(float(h), 3)]
        elif kind == "axes.position":
            er = op.get("element_ref")
            gid = refs[ref].gid_of.get(er) if isinstance(er, str) else None
            x, y, w, h = (op.get(k) for k in ("x", "y", "w", "h"))
            if (
                gid is None
                or not _finite(x, y, w, h)
                or w <= 0
                or h <= 0
                or x < -0.5
                or y < -0.5
                or x + w > 1.5
                or y + h > 1.5
            ):
                raise QuickLayoutError("invalid_plan", "bad axes position")
            # 求解器用 y 向下的外框比例；`axes.position` 是 matplotlib 的 [左, 下, 宽, 高]（y 向上）
            staged[ref]["pos"][gid] = [round(x, 4), round(1.0 - y - h, 4), round(w, 4), round(h, 4)]
        elif kind == "label.place":
            x, y = op.get("x"), op.get("y")
            if not _finite(x, y) or not (0 <= x <= pw and 0 <= y <= ph):
                raise QuickLayoutError("invalid_plan", "bad label")
            labels.append({"ref": ref, "x": round(float(x), 3), "y": round(float(y), 3)})
    for ref, s in staged.items():
        p = refs[ref]
        if s["box"] is not None:
            p.box = s["box"]
        if s["size"] is not None:
            p.patches = _upsert(p.patches, "figure", "size_mm", s["size"])
        for gid, pos in s["pos"].items():
            p.patches = _upsert(p.patches, gid, "position", pos)
    return {"labels": labels}


def _render(p: Panel, patches: list) -> None:
    resp = p.render(patches)
    m = resp.get("manifest")
    if not isinstance(m, dict) or resp.get("warnings"):
        raise QuickLayoutError("render_failed", p.rel_id)
    p.manifest = m
    size = m.get("size_mm")
    if not (isinstance(size, list) and len(size) == 2 and _finite(*size)):
        raise QuickLayoutError("render_failed", "no size")
    p.size_mm = [float(size[0]), float(size[1])]
    p.equal_aspect = _equal_aspect(m)


# ---------------------------------------------------------------------------
# 验收指标（开源侧判，不信求解器自己的结论）
# ---------------------------------------------------------------------------
def _page_rect(p: Panel, rect: list[float]) -> list[float]:
    """图幅比例坐标（x0,y0,x1,y1，y 向下）→ 页面 mm。图框与图幅等大（缩放 = 1）。"""
    bx, by, bw, bh = p.box
    return [bx + rect[0] * bw, by + rect[1] * bh, bx + rect[2] * bw, by + rect[3] * bh]


def _groups(panels: list[Panel], idx: int) -> list[list[Panel]]:
    out: list[list[Panel]] = []
    for p in sorted(panels, key=lambda q: q.box[idx]):
        if out and abs(out[-1][0].box[idx] - p.box[idx]) < 0.05:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def check(
    panels: list[Panel],
    page_w: float,
    gap: float,
    padding: float,
    labels: list[dict],
    label_pt: float,
    page_h: float | None = None,
) -> list[dict]:
    """量 ADR 0199 §六 的指标，返回没过的条目（空 = 全过）。间距与页边都按**文字外沿**（ink）量。"""
    bad: list[dict] = []
    inks: list[tuple[Panel, list[float]]] = []
    for p in panels:
        if abs(p.box[2] - p.size_mm[0]) > 0.05 or abs(p.box[3] - p.size_mm[1]) > 0.05:
            bad.append({"metric": "scale", "ref": p.key})
        ink = _ink(p.manifest or {})
        w, h = p.size_mm
        edge = min(ink[0] * w, ink[1] * h, (1 - ink[2]) * w, (1 - ink[3]) * h)
        if edge < EDGE_MIN_MM - EDGE_TOL_MM:
            bad.append({"metric": "edge", "ref": p.key, "mm": round(edge, 3)})
        inks.append((p, _page_rect(p, ink)))
    aligned = [p for p in panels if not p.equal_aspect and _main_axes(p.manifest or {})]
    for col in _groups(aligned, 0):
        lefts = [_page_rect(p, _box_rect(p))[0] for p in col]
        if max(lefts) - min(lefts) > ALIGN_TOL_MM:
            bad.append({"metric": "align_left", "delta_mm": round(max(lefts) - min(lefts), 4)})
    for row in _groups(aligned, 1):
        bottoms = [_page_rect(p, _box_rect(p))[3] for p in row]
        tops = [_page_rect(p, _box_rect(p))[1] for p in row]
        if max(bottoms) - min(bottoms) > ALIGN_TOL_MM:
            bad.append(
                {"metric": "align_bottom", "delta_mm": round(max(bottoms) - min(bottoms), 4)}
            )
        if max(tops) - min(tops) > ALIGN_TOL_MM:
            bad.append({"metric": "align_top", "delta_mm": round(max(tops) - min(tops), 4)})
    ink_of = {id(p): r for p, r in inks}
    # 相邻两张图文字外沿之间至少空出「图间距」（同一行看左右，同一列看上下）
    for row in _groups(panels, 1):
        row = sorted(row, key=lambda q: q.box[0])
        for a, b in zip(row, row[1:]):
            d = ink_of[id(b)][0] - ink_of[id(a)][2]
            if d < gap - GAP_TOL_MM:
                bad.append({"metric": "gap", "delta_mm": round(d - gap, 4)})
    for col in _groups(panels, 0):
        col = sorted(col, key=lambda q: q.box[1])
        for a, b in zip(col, col[1:]):
            d = ink_of[id(b)][1] - ink_of[id(a)][3]
            if d < gap - GAP_TOL_MM:
                bad.append({"metric": "gap", "delta_mm": round(d - gap, 4)})
    # 页面四周：任何文字外沿（含标签）离页边都不少于 padding
    lh = label_pt * PT_MM * 1.2
    lw = label_pt * PT_MM * 0.6 * 3
    rects = [r for _p, r in inks] + [
        [lab["x"], lab["y"], lab["x"] + lw, lab["y"] + lh] for lab in labels
    ]
    if (
        min(r[0] for r in rects) < padding - EDGE_TOL_MM
        or min(r[1] for r in rects) < padding - EDGE_TOL_MM
    ):
        bad.append({"metric": "page_edge"})
    if max(r[2] for _p, r in inks) > page_w - padding + EDGE_TOL_MM:
        bad.append({"metric": "page_edge"})
    if page_h is not None and max(r[3] for _p, r in inks) > page_h - padding + EDGE_TOL_MM:
        bad.append({"metric": "page_edge"})
    for i, (pa, a) in enumerate(inks):
        for pb, b in inks[i + 1 :]:
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                bad.append({"metric": "overlap", "refs": [pa.key, pb.key]})
    for lab in labels:
        r = [lab["x"], lab["y"], lab["x"] + lw, lab["y"] + lh]
        for pk, a in inks:
            if r[0] < a[2] and a[0] < r[2] and r[1] < a[3] and a[1] < r[3]:
                bad.append({"metric": "label_overlap", "refs": [lab["ref"], pk.key]})
    return bad


def _box_rect(p: Panel) -> list[float]:
    axes = _main_axes(p.manifest or {})
    return (
        _union([[a["bbox"][0], a["bbox"][1], a["bbox"][2], a["bbox"][3]] for a in axes])
        if axes
        else [0, 0, 1, 1]
    )


# ---------------------------------------------------------------------------
# 事务
# ---------------------------------------------------------------------------
def target_width(profile: dict, target: str, page: dict) -> float:
    if target == "page":
        return float(page["w"])
    widths = profile.get("widths_mm") if isinstance(profile, dict) else None
    w = widths.get(target) if isinstance(widths, dict) else None
    if not _finite(w) or w <= 0:
        raise QuickLayoutError("invalid_plan", "no target width")
    return float(w)


def _spec_fix(p: Panel, profile: dict) -> None:
    """改过图幅之后按规范修（scale = 1：图框与图幅等大），复用 specfix，不另写判据（ADR 0199 §二）。

    specfix 一次修整张图，任何一条冲突就整张不改。整张没修成时退一步按规则逐组修——与用户在问题面板里
    逐条点「修复」走的是同一个免费入口（`only`），每组各自是一次完整事务、各自判、各自回滚。"""
    out = specfix.transaction(p.render, p.patches, 1.0, profile, None)
    if out.get("ok") and isinstance(out.get("patches"), list):
        p.patches = list(out["patches"])
        p.specfix_ok = True
        return
    if p.manifest is None:
        p.specfix_ok = False
        return
    _render(p, p.patches)
    issues = specfix.all_issues(p.manifest or {}, profile, 1.0)
    # 只修「全部处理」本来会修的那些规则（`select(…, None)` 去掉了建议档），不多修
    targets = specfix.select(issues, None)
    rules = sorted({rule for rule, _gid in targets})
    all_ok = True

    def attempt(only: list[dict]) -> bool:
        got = specfix.transaction(p.render, p.patches, 1.0, profile, only)
        if got.get("ok") and isinstance(got.get("patches"), list):
            p.patches = list(got["patches"])
            return True
        return got.get("exit") == specfix.EXIT_NOTHING_TO_DO

    for rule in rules:
        if attempt([{"rule": rule, "gid": ""}]):
            continue
        # 这条规则整组修不成：再逐个元素修（同样是问题面板的逐条修复），修得了的修，修不了的照实报
        for r, gid in targets:
            if r == rule and not attempt([{"rule": r, "gid": gid}]):
                all_ok = False
    p.specfix_ok = all_ok


def _each(panels: list[Panel], fn: Callable[[Panel], object]) -> list:
    """按图并行跑 `fn`（每张图有自己的 worker，互不共享热态）；任何一张抛异常就原样抛出。"""
    if len(panels) <= 1:
        return [fn(p) for p in panels]
    with ThreadPoolExecutor(
        max_workers=min(8, len(panels)), thread_name_prefix="quicklayout"
    ) as ex:
        return list(ex.map(fn, panels))


def plan(
    panels: list[Panel],
    fixed: list[dict],
    *,
    page: dict,
    target: str,
    gap_mm: float,
    profile: dict,
    label_pt: float,
    fix_spec: bool = True,
    columns: int | None = None,
    canvas: str = "fit",
    solver: Callable[[dict], dict] | None = None,
) -> dict:
    """一键排版事务本体。成功返回 `{ok: True, panels: [{key, patches, box}], labels: [...], notes: [...]}`；
    失败抛 `QuickLayoutError`（调用方把每张图渲回 B0）。"""
    if not licensed():
        raise QuickLayoutError("not_licensed")
    if len(panels) < 2:
        raise QuickLayoutError("too_few")
    if target not in TARGETS or not _finite(gap_mm) or not (0 <= gap_mm <= 30):
        raise QuickLayoutError("invalid_plan", "bad options")
    if canvas not in CANVAS_MODES or (columns is not None and not (1 <= columns <= MAX_COLUMNS)):
        raise QuickLayoutError("invalid_plan", "bad options")
    # 保持画布：在用户定好的画布里排，页宽就是画布宽；跟着内容：页宽 = 期刊栏宽
    tw = float(page["w"]) if canvas == "keep" else target_width(profile, target, page)
    # 用户设了安全边距就用它，否则默认 2 mm（文字外沿到页边）
    padding = float(page.get("margin") or 0.0) or DEFAULT_PADDING_MM
    if tw <= 2 * padding + 10:
        raise QuickLayoutError("invalid_plan", "page too narrow")
    spec_changed: set[str] = set()
    ask = solver or (lambda payload: prohost.call("quick_layout.plan", payload, feature=FEATURE))
    refs = {f"p{i + 1}": p for i, p in enumerate(panels)}
    # 计时：每个阶段的渲染次数与耗时（只写本地日志）
    stats: dict[str, list[float]] = {}
    stats_lock = threading.Lock()
    phase_of: dict[int, str] = {}
    for p in panels:
        raw = p.render

        def timed(patches: list, raw=raw, key=id(p)) -> dict:
            t = time.perf_counter()
            try:
                return raw(patches)
            finally:
                with stats_lock:
                    stats.setdefault(phase_of.get(key, "?"), []).append(time.perf_counter() - t)

        p.render = timed
    t_all = time.perf_counter()

    def first_render(p: Panel) -> None:
        phase_of[id(p)] = "b0"
        p.patches = list(p.base)
        _render(p, p.patches)

    _each(panels, first_render)
    labels: list[dict] = []
    bad: list[dict] = []
    for rnd in range(1, MAX_ROUNDS + 1):
        payload = {
            "round": rnd,
            "page": {k: float(page[k]) for k in ("w", "h", "margin") if k in page},
            "target": {"width_mm": tw, "gap_mm": float(gap_mm), "padding_mm": padding},
            "label": {"pt": float(label_pt)},
            "arrange": {"columns": columns, "canvas": canvas},
            "panels": [_panel_input(ref, p) for ref, p in refs.items()],
            "fixed": [
                {"ref": f"f{i + 1}", "box": [float(v) for v in f["box"]]}
                for i, f in enumerate(fixed)
            ],
        }
        t_s = time.perf_counter()
        try:
            result = ask(payload)
        except prohost.ProUnavailable as exc:
            raise QuickLayoutError("unavailable", exc.code) from None
        stats.setdefault(f"sidecar{rnd}", []).append(time.perf_counter() - t_s)
        if result.get("error") == "page_too_small":
            raise QuickLayoutError("page_too_small")
        out = _apply_ops(result.get("ops"), refs, page)
        labels = out["labels"] or labels

        def advance(p: Panel, rnd: int = rnd) -> set[str]:
            changed: set[str] = set()
            if rnd == 1 and fix_spec:
                phase_of[id(p)] = "spec"
                before = {(x.get("gid"), x.get("prop")): x.get("value") for x in p.patches}
                _spec_fix(p, profile)
                for x in p.patches:
                    k = (x.get("gid"), x.get("prop"))
                    if k not in before or before[k] != x.get("value"):
                        changed.add(SPEC_CATEGORY.get(str(x.get("prop"))) or "other")
            phase_of[id(p)] = f"round{rnd}"
            _render(p, p.patches)
            return changed

        # 各张图有各自的 worker：按图并行（ADR 0199 §二「提速」）
        for changed in _each(panels, advance):
            spec_changed |= changed
        bad = check(
            panels,
            tw,
            float(gap_mm),
            padding,
            labels,
            label_pt,
            page_h=float(page["h"]) if canvas == "keep" else None,
        )
        if not bad:
            break
    LOG.info(
        "一键排版计时 总 %.2fs：%s",
        time.perf_counter() - t_all,
        "，".join(f"{k} {len(v)}次 {sum(v):.2f}s" for k, v in stats.items()),
    )
    if bad:
        # 等比例轴的图本来就不计对齐（`check` 已排除），所以这里任何一条没过都是真没排好
        raise QuickLayoutError("not_converged", ",".join(sorted({b["metric"] for b in bad})))
    notes = [{"code": "equal_aspect", "ref": p.key} for p in panels if p.equal_aspect]
    # 免费的「按期刊修正」自己判了不改（比如字号提上去图例会压住数据）：照实说，不绕过它的判断
    notes += [{"code": "spec_not_fixed", "ref": p.key} for p in panels if p.specfix_ok is False]
    by_ref = {ref: p.key for ref, p in refs.items()}
    # 一键排版的结果就是最终那张图：页宽 = 目标宽度；页高收紧到「最低的文字外沿 + 留白」（含原地不动的固定盒子）
    bottoms = [_page_rect(p, _ink(p.manifest or {}))[3] for p in panels]
    bottoms += [f["box"][1] + f["box"][3] for f in fixed]
    page_out = (
        {"w": round(float(page["w"]), 3), "h": round(float(page["h"]), 3)}
        if canvas == "keep"
        else {"w": round(tw, 3), "h": round(max(bottoms) + padding, 3)}
    )
    return {
        "ok": True,
        "page": page_out,
        "panels": [
            {"key": p.key, "patches": p.patches, "box": [round(v, 3) for v in p.box]}
            for p in panels
        ],
        "labels": [{"key": by_ref[lab["ref"]], "x": lab["x"], "y": lab["y"]} for lab in labels],
        "notes": notes,
        # 按规范修了哪几类（`fix_spec` 关着时为空）
        "spec_changes": sorted(spec_changed),
    }
