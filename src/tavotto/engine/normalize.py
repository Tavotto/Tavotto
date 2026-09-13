"""保留式规范化事务（ADR 0051）：原图基准 B0 → 修改约定 → 最小编辑 → 真实渲染检测
→ 有界局部修复 → 验收。

一句话：**用户没要求改的不改，要求改的精确改，不得不动的动得有尺子、有上限，
装不下就退出并留原件。**

这个模块**只算不画**：输入是引擎 manifest（真实渲染后的几何与属性），输出是
patch 列表 / 裁决 / 报告，渲染与导出由调用方（Codex 插件的 `bridge`、将来的
Flask）拿着现有 worker 做。纯标准库，不 import matplotlib。

## 结构

* **修改约定（contract）** —— `build_contract()`：记下 B0（尺寸、每个元素的受保护
  属性快照、结构、字体真实解析、几何、原有问题）、用户点名的目标、由目标**推导**
  出来的允许集合、局部适配的许可与预算。修复循环拿到的是它的只读视图，
  `allowed` 不在循环里增长。
* **计划** —— `plan_patches()`：目标 → 最小 patch 列表。只指定宽度时按原长宽比
  推高度；「最小字号」只补齐低于阈值的，不把所有文字压平；「指定字号」才统一。
* **授权检查** —— `authorize()`：执行**前**看 patch 的 (gid, prop) 在不在允许集合。
* **实效检查** —— `compare()`：执行**后**拿新 manifest 对着 B0 逐项比：受保护属性
  一个都没变、结构一个都没变、字体真的落成了那张脸、尺寸真的到了、几何位移在预算
  内、问题清单按稳定身份分成 新增 / 加重 / 未加重 / 改善。
* **局部修复** —— `adapt_margins()` / `legend_candidates()`：确定性的候选，不是
  自由重画。外边距按「装饰物需要多少 mm」重排（与 tight_layout 同一思路，但落在
  现有的 `axes.position` override 上，热态与重放同一条路）；图例只在**它自己的**
  子图里换预设位置。

## 预算（全部相对 B0，按 mm 与图幅比例计，不按操作数）

见下面的常量与各自的理由。累计位移永远对 B0 算：每轮各挪一点、最后大面积漂移
的那种事在这里加不起来。
"""

from __future__ import annotations

import hashlib
import json
import uuid

from . import interference, preflight

#: 子图任一条边相对「B0 位置按比例缩放后」最多挪动**新图幅对应尺寸**的这么大比例。
#: 取法：10 pt 的轴标题 + 刻度在 80 mm 图上要 ~10 mm 的边距，而 150 → 80 缩放
#: 后 B0 的边距只剩 ~5 mm，缺口约 5 mm = 6%；15% 给复合装饰（外置图例、色条）
#: 留余量，同时挡住「把子图挤到只剩一角」——超过它说明这个宽度本来就装不下。
EDGE_SHIFT_BUDGET_FRAC = 0.15
#: 局部适配之后每个子图至少保留其「B0 尺寸按比例缩放」的这么多。低于它的图
#: 已经不是原来的排版了。
AXES_KEEP_FRAC = 0.6
#: 子图绘图区的绝对下限；再小的图上什么刻度都摆不下。
MIN_AXES_MM = 8.0
#: 局部修复最多几轮（每轮 = 一次候选 + 一次真实渲染 + 一次验收）。
MAX_REPAIR_ROUNDS = 3
#: 图幅边缘留白的下限：B0 的留白按比例缩后不许小于它（文字贴边就是裁切的前奏）。
PAD_MIN_MM = 0.35
#: 与 B0 比「有没有加重」的容差：探出 / 重叠尺寸差在这之内算未加重（布局框抖动）。
WORSEN_TOL_MM = 0.2
WORSEN_TOL_RATIO = 1.15
#: 尺寸目标的容差（manifest 的 size_mm 保留两位小数）。
SIZE_TOL_MM = 0.05
#: 字号的容差（manifest 保留两位小数）。
FONT_TOL_PT = 0.05
#: 浮点属性「变没变」的容差。
VALUE_TOL = 1e-6

TARGET_KEYS = ("width_mm", "height_mm", "font_family", "min_font_pt", "font_size_pt")

EXIT_DONE = "done"
EXIT_NOTHING_TO_DO = "nothing_to_do"
EXIT_REQUIRES_AUTHORIZATION = "requires_authorization"
EXIT_CONSTRAINT_CONFLICT = "constraint_conflict"
EXIT_FONT_UNAVAILABLE = "font_unavailable"
EXIT_BUDGET_EXCEEDED = "budget_exceeded"
EXIT_PROTECTED_CHANGED = "protected_changed"
EXIT_ACCEPTANCE_FAILED = "acceptance_failed"
EXIT_UNSUPPORTED = "unsupported"

#: 带字号的 prop（按角色）；`fontsize` 之外那两个是图例标题与色条刻度。
FONT_SIZE_PROPS = ("fontsize", "title_fontsize", "tick_fontsize")
#: 图例在自己子图里的预设位置（matplotlib `loc` 的字面量）——局部修复只在这
#: 张表里挑，不挪到图外、不换锚框。
LEGEND_LOCS = (
    "upper right",
    "upper left",
    "lower left",
    "lower right",
    "center left",
    "center right",
    "upper center",
    "lower center",
)
#: 每个预设在 3×3 网格上的格位，用来给候选排「离原位最近的先试」。
_LOC_GRID = {
    "upper left": (0, 0),
    "upper center": (1, 0),
    "upper right": (2, 0),
    "center left": (0, 1),
    "center": (1, 1),
    "center right": (2, 1),
    "lower left": (0, 2),
    "lower center": (1, 2),
    "lower right": (2, 2),
    "best": (1, 1),
    "right": (2, 1),
}


class NormalizeError(ValueError):
    def __init__(self, message: str, code: str, **extra) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


# ----------------------------- 小工具 ---------------------------------------
def _field(el: dict, prop: str):
    for f in el.get("editable") or []:
        if f.get("prop") == prop:
            return f.get("value")
    return None


def _has_field(el: dict, prop: str) -> bool:
    return any(f.get("prop") == prop for f in el.get("editable") or [])


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and abs(f) != float("inf") else None


def _same(a, b) -> bool:
    """两个 manifest 取值「一样」：数字按容差，列表逐项，其余按相等。"""
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= VALUE_TOL * max(1.0, abs(fa), abs(fb))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _norm_family(name) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


GENERIC_FAMILIES = ("serif", "sans-serif", "monospace", "cursive", "fantasy")


def family_matches(requested, face) -> bool | None:
    """请求的族名与真正画字的脸是否对得上；通用族（serif …）按名字判不了，回 None。"""
    if requested is None or str(requested).strip() == "":
        return None
    if str(requested) in GENERIC_FAMILIES:
        return None
    if not face:
        return False
    want, got = _norm_family(requested), _norm_family(face)
    return bool(want) and (got == want or got.startswith(want))


def manifest_hash(manifest: dict) -> str:
    text = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def merge_patches(base: list[dict], extra: list[dict]) -> list[dict]:
    """全量列表语义下把 `extra` 盖到 `base` 上：同一 (gid, prop) 后者赢，顺序保留。

    重复执行同一份规范化不会因此多出重复的 override——同一个键永远只有一条。
    """
    out: list[dict] = []
    index: dict[tuple[str, str], int] = {}
    for p in [*base, *extra]:
        key = (str(p["gid"]), str(p["prop"]))
        entry = {"gid": key[0], "prop": key[1], "value": p["value"]}
        if key in index:
            out[index[key]] = entry
        else:
            index[key] = len(out)
            out.append(entry)
    return out


# ----------------------------- 目标 -----------------------------------------
def normalize_targets(raw: dict) -> dict:
    """校验用户点名的目标；至少一项，数值必须是正数。"""
    if not isinstance(raw, dict):
        raise NormalizeError("targets 必须是对象", "bad_targets")
    unknown = sorted(k for k in raw if k not in TARGET_KEYS)
    if unknown:
        raise NormalizeError(f"不认识的目标: {', '.join(unknown)}", "bad_targets", unknown=unknown)
    out: dict = {}
    for key in ("width_mm", "height_mm", "min_font_pt", "font_size_pt"):
        v = raw.get(key)
        if v is None:
            continue
        f = _num(v)
        if f is None or f <= 0:
            raise NormalizeError(f"{key} 必须是正数: {v!r}", "bad_targets", key=key)
        out[key] = f
    fam = raw.get("font_family")
    if fam is not None:
        if not isinstance(fam, str) or not fam.strip():
            raise NormalizeError("font_family 必须是非空字符串", "bad_targets", key="font_family")
        out["font_family"] = fam.strip()
    if not out:
        raise NormalizeError(
            "至少要点名一个目标（width_mm / height_mm / font_family / min_font_pt / font_size_pt）",
            "no_targets",
        )
    if "font_size_pt" in out and "min_font_pt" in out and out["font_size_pt"] < out["min_font_pt"]:
        raise NormalizeError(
            "font_size_pt 低于 min_font_pt，两个目标互相矛盾", "bad_targets", key="font_size_pt"
        )
    return out


# ----------------------------- B0 快照 ---------------------------------------
def protected_snapshot(manifest: dict) -> dict[str, dict]:
    """每个元素的角色 + 全部 editable 取值（B0 的受保护属性快照，也是实效比对的尺）。"""
    out: dict[str, dict] = {}
    for el in manifest.get("elements") or []:
        gid = str(el.get("gid", ""))
        out[gid] = {
            "role": el.get("role"),
            "props": {str(f.get("prop")): f.get("value") for f in el.get("editable") or []},
            "bbox": list(el.get("bbox") or []),
            "face": el.get("face"),
            "math_face": el.get("math_face"),
        }
    return out


def _rect_of(el: dict):
    return interference.visible_rect(el)


def per_element_clipping(manifest: dict) -> list[dict]:
    """`element-outside-figure` 的**逐元素**清单（预检的那份按 id 聚合，对比 B0 要逐个）。"""
    size = manifest.get("size_mm") or [0.0, 0.0]
    w = _num(size[0] if len(size) > 0 else None) or 0.0
    h = _num(size[1] if len(size) > 1 else None) or 0.0
    out = []
    if w <= 0 or h <= 0:
        return out
    for el in manifest.get("elements") or []:
        hit = preflight.element_overflow(el, w, h)
        if hit is None:
            continue
        side, over = hit
        out.append(
            {
                "id": "element-outside-figure",
                "severity": "error",
                "text": f"元素超出图幅 {over:g} mm（{side}），导出时超出的部分会被裁掉",
                "message": {"key": "elementOutsideFigure", "params": {"mm": f"{over:g}"}},
                "object_ids": ["figure"],
                "gids": [str(el.get("gid", ""))],
                "detail": {"overflow_mm": over, "side": side, "certain": True},
            }
        )
    return out


def geometry_issues(manifest: dict, profile: dict | None) -> list[dict]:
    """事务里逐项比对用的几何问题清单：逐元素裁切 + 干涉。"""
    return per_element_clipping(manifest) + interference.detect(manifest, profile)


def build_contract(
    manifest: dict,
    targets: dict,
    *,
    profile: dict | None,
    base_patches: list[dict],
    profile_issues: list[dict],
    meta: dict | None = None,
) -> dict:
    """建立本次事务固定的 B0 与修改约定。`targets` 已经过 `normalize_targets`。"""
    size = manifest.get("size_mm") or [0.0, 0.0]
    w0, h0 = float(size[0]), float(size[1])
    snapshot = protected_snapshot(manifest)
    axes_positions = {
        gid: list(_field(el, "position"))
        for gid, el in ((str(e.get("gid", "")), e) for e in manifest.get("elements") or [])
        if interference.is_top_level_axes(el) and _field(el, "position") is not None
    }
    rects = {}
    for el in manifest.get("elements") or []:
        r = _rect_of(el)
        if r is not None:
            rects[str(el.get("gid", ""))] = list(r)
    legends = {}
    for el in manifest.get("elements") or []:
        if el.get("role") == "legend":
            gid = str(el.get("gid", ""))
            legends[gid] = {
                "loc": _field(el, "loc"),
                "loc_anchor": _field(el, "loc_anchor"),
                "entries": list(
                    next(
                        (
                            f.get("options")
                            for f in el.get("editable") or []
                            if f.get("prop") == "entry_order"
                        ),
                        [],
                    )
                    or []
                ),
            }
    fonts = {
        gid: {
            "requested": s["props"].get("fontfamily"),
            "face": s["face"],
            "math_face": s["math_face"],
        }
        for gid, s in snapshot.items()
        if s["face"] is not None
    }
    # 允许集合由目标推导，不接受额外输入；修复循环拿到的是它，不能改它。
    allowed: list[list[str]] = []
    if "width_mm" in targets or "height_mm" in targets:
        allowed.append(["figure", "size_mm"])
    for gid, s in snapshot.items():
        props = s["props"]
        if "font_family" in targets and "fontfamily" in props:
            allowed.append([gid, "fontfamily"])
        if "font_size_pt" in targets or "min_font_pt" in targets:
            for prop in FONT_SIZE_PROPS:
                if prop in props:
                    allowed.append([gid, prop])
    adjust: list[list[str]] = [[gid, "position"] for gid in axes_positions]
    for gid, info in legends.items():
        if info["loc_anchor"] is None and _has_field(_element(manifest, gid) or {}, "loc"):
            adjust.append([gid, "loc"])
    contract = {
        "contract_id": "c-" + uuid.uuid4().hex[:12],
        "schema": 1,
        "targets": dict(targets),
        "meta": dict(meta or {}),
        "profile_id": (profile or {}).get("profile_id"),
        "baseline": {
            "patches": [dict(p) for p in base_patches],
            "manifest_hash": manifest_hash(manifest),
            "size_mm": [w0, h0],
            "snapshot": snapshot,
            "axes_positions": axes_positions,
            "rects": rects,
            "legends": legends,
            "fonts": fonts,
            "profile_issues": [dict(i) for i in profile_issues],
            "geometry_issues": geometry_issues(manifest, profile),
        },
        "allowed": allowed,
        "allowed_adjust": adjust,
        "budget": {
            "edge_shift_budget_frac": EDGE_SHIFT_BUDGET_FRAC,
            "axes_keep_frac": AXES_KEEP_FRAC,
            "min_axes_mm": MIN_AXES_MM,
            "max_repair_rounds": MAX_REPAIR_ROUNDS,
        },
    }
    return contract


def _element(manifest: dict, gid: str) -> dict | None:
    for el in manifest.get("elements") or []:
        if str(el.get("gid", "")) == gid:
            return el
    return None


def allowed_keys(contract: dict) -> set[tuple[str, str]]:
    return {(g, p) for g, p in contract.get("allowed") or []}


def adjust_keys(contract: dict) -> set[tuple[str, str]]:
    return {(g, p) for g, p in contract.get("allowed_adjust") or []}


# ----------------------------- 计划 -----------------------------------------
def plan_patches(contract: dict, manifest: dict) -> dict:
    """目标 → 最小 patch 列表。返回 `{patches, unsupported, notes, size_mm}`。"""
    targets = contract["targets"]
    size = manifest.get("size_mm") or contract["baseline"]["size_mm"]
    w0, h0 = float(size[0]), float(size[1])
    patches: list[dict] = []
    notes: list[str] = []
    unsupported: list[dict] = []

    target_size = None
    if "width_mm" in targets and "height_mm" in targets:
        target_size = [targets["width_mm"], targets["height_mm"]]
    elif "width_mm" in targets:
        if w0 <= 0:
            raise NormalizeError("B0 的宽度为 0，推不出高度", "bad_baseline")
        target_size = [targets["width_mm"], round(h0 * targets["width_mm"] / w0, 3)]
        notes.append(f"只指定了宽度：高度按原图长宽比推为 {target_size[1]:g} mm")
    elif "height_mm" in targets:
        if h0 <= 0:
            raise NormalizeError("B0 的高度为 0，推不出宽度", "bad_baseline")
        target_size = [round(w0 * targets["height_mm"] / h0, 3), targets["height_mm"]]
        notes.append(f"只指定了高度：宽度按原图长宽比推为 {target_size[0]:g} mm")
    if target_size is not None:
        patches.append({"gid": "figure", "prop": "size_mm", "value": target_size})

    fam = targets.get("font_family")
    if fam is not None:
        covered = 0
        for el in manifest.get("elements") or []:
            gid = str(el.get("gid", ""))
            if _has_field(el, "fontfamily"):
                patches.append({"gid": gid, "prop": "fontfamily", "value": fam})
                covered += 1
            elif el.get("role") == "legend" and _field(el, "title"):
                # 图例标题的字体没有 override 入口：逐项说出口，不假装改了
                # （色条的标签与刻度分别是它那条轴的 axis_label / ticks，已经覆盖）
                unsupported.append(
                    {
                        "gid": gid,
                        "prop": "fontfamily",
                        "reason": "no_override_for_role",
                        "role": el.get("role"),
                    }
                )
        notes.append(f"字体族 {fam}：{covered} 个文字元素（含刻度组）")

    if "font_size_pt" in targets:
        size_pt = targets["font_size_pt"]
        n = 0
        for el in manifest.get("elements") or []:
            for prop in FONT_SIZE_PROPS:
                if _has_field(el, prop) and _num(_field(el, prop)) is not None:
                    patches.append({"gid": str(el.get("gid", "")), "prop": prop, "value": size_pt})
                    n += 1
        notes.append(f"统一字号 {size_pt:g} pt：{n} 条")
    elif "min_font_pt" in targets:
        floor = targets["min_font_pt"]
        raised = 0
        for el in manifest.get("elements") or []:
            for prop in FONT_SIZE_PROPS:
                cur = _num(_field(el, prop)) if _has_field(el, prop) else None
                if cur is not None and cur < floor - FONT_TOL_PT:
                    patches.append({"gid": str(el.get("gid", "")), "prop": prop, "value": floor})
                    raised += 1
        notes.append(f"最小字号 {floor:g} pt：补齐 {raised} 条低于阈值的文字，其余字号层级保持不变")
    return {"patches": patches, "unsupported": unsupported, "notes": notes, "size_mm": target_size}


# ----------------------------- 授权 -----------------------------------------
def authorize(contract: dict, patches: list[dict]) -> list[dict]:
    """执行前：每条 patch 的 (gid, prop) 都得在允许集合（目标推导 + 局部适配）里。

    返回越权清单；空 = 通过。B0 里已经有的、值也没变的 patch 不算越权（那是
    事务开始前用户自己的编辑，原样带着）。
    """
    ok = allowed_keys(contract) | adjust_keys(contract)
    base = {(str(p["gid"]), str(p["prop"])): p["value"] for p in contract["baseline"]["patches"]}
    bad = []
    for p in patches:
        key = (str(p["gid"]), str(p["prop"]))
        if key in ok:
            continue
        if key in base and _same(base[key], p["value"]):
            continue
        bad.append({"gid": key[0], "prop": key[1], "value": p["value"]})
    return bad


# ----------------------------- 实效比对 -------------------------------------
def _tick_mode_auto(snapshot: dict, gid: str) -> bool:
    """`axes_0.xticklabels_3` 这条刻度文字所属的刻度组在 B0 是不是自动定位。"""
    head, sep, tail = gid.rpartition(".")
    if not sep or "ticklabels_" not in tail:
        return False
    axis = tail.split("ticklabels_")[0]  # "x" / "y" / "z"
    group = snapshot.get(f"{head}.{axis}ticks") or {}
    return (group.get("props") or {}).get("major_mode") == "auto"


def _ticks_group_auto(snapshot: dict, gid: str) -> bool:
    return (snapshot.get(gid, {}).get("props") or {}).get("major_mode") == "auto"


def _classify_issues(before: list[dict], after: list[dict]) -> dict:
    b = {interference.issue_key(i): i for i in before}
    a = {interference.issue_key(i): i for i in after}
    new, worsened, unchanged, improved = [], [], [], []
    for key, issue in a.items():
        prev = b.get(key)
        if prev is None:
            new.append(issue)
            continue
        m0, m1 = interference.measure(prev), interference.measure(issue)
        if (
            m0 is not None
            and m1 is not None
            and m1 > max(m0 * WORSEN_TOL_RATIO, m0 + WORSEN_TOL_MM)
        ):
            worsened.append({**issue, "before": m0, "after": m1})
        elif m0 is not None and m1 is not None and m1 < m0 - WORSEN_TOL_MM:
            improved.append({**issue, "before": m0, "after": m1})
        else:
            unchanged.append(issue)
    for key, issue in b.items():
        if key not in a:
            improved.append({**issue, "before": interference.measure(issue), "after": None})
    return {"new": new, "worsened": worsened, "unchanged": unchanged, "improved": improved}


#: 用户点名的目标**直接决定**的那几条规范规则：用户要 120 mm，规范说只有 80/150，
#: 这不是缺陷，是用户的决定（与 SKILL 里「用户自己点的头别每次报 warning」同一
#: 条纪律）。这些条目在事务里**不挡**，但如实进 `profile_conflicts` 与留档。
TARGET_RULES = {
    "width_mm": ("page-width", "page-aspect"),
    "height_mm": ("page-width", "page-aspect"),
    "font_family": ("font-family-substituted",),
    "min_font_pt": ("font-too-small", "font-below-absolute-floor", "legend-font-size"),
    "font_size_pt": (
        "font-too-small",
        "font-below-absolute-floor",
        "font-too-large",
        "legend-font-size",
    ),
}


def decided_by_targets(targets: dict, issue_id: str) -> bool:
    return any(issue_id in TARGET_RULES.get(key, ()) for key in targets)


def _blocks(issue: dict, targets: dict) -> bool:
    """新增 / 加重的这一条挡不挡事务：error 级挡；确定性干涉（warn）也挡；风险级不挡；
    由用户目标直接决定的规范规则不挡（那是用户的决定，报告里另列）。"""
    if decided_by_targets(targets, str(issue.get("id"))):
        return False
    if issue.get("severity") == "error":
        return True
    if issue.get("id") in interference.CHECK_IDS:
        return bool((issue.get("detail") or {}).get("certain"))
    return False


def _repair_kind(issue: dict) -> str:
    cid = issue.get("id")
    gids = issue.get("gids") or []
    if cid == "element-outside-figure" or cid == interference.CHECK_TEXT_OVER_AXES:
        return "margins"
    if cid == interference.CHECK_LEGEND_OVER_DATA:
        return "legend"
    if cid == interference.CHECK_TEXT_OVERLAP:
        if any(interference.legend_of(g) or g.endswith(".legend") or ".legend_" in g for g in gids):
            return "legend"
        owners = {interference.owner_axes(g) for g in gids}
        if len(owners) > 1:
            return "margins"
    return ""


def compare(
    contract: dict,
    manifest: dict,
    *,
    profile_issues: list[dict],
    profile: dict | None,
    relocated_legends: set[str] | None = None,
    patches: list[dict] | None = None,
) -> dict:
    """执行后：拿真实渲染的 manifest 对着 B0 逐项比。返回裁决。

    `patches` 是这一版落下的全量列表：预算只对**我们自己挪过的**子图（列表里有它的
    `position`）判超不超；布局引擎（`layout="tight"` / `"constrained"`）自己重排出来
    的位移照样量、照样报，但那不是局部适配的开销，不拿它挡事务。不给 `patches`
    就全部都判（合成 manifest 的单元测试走这一档）。
    """
    base = contract["baseline"]
    snap0: dict = base["snapshot"]
    snap1 = protected_snapshot(manifest)
    targets = contract["targets"]
    ok_keys = allowed_keys(contract)
    adj_keys = adjust_keys(contract)
    relocated = relocated_legends or set()

    # ---- 结构 ----
    missing = [g for g in snap0 if g not in snap1 and not _tick_mode_auto(snap0, g)]
    extra = [g for g in snap1 if g not in snap0 and not _tick_mode_auto(snap0, g)]
    role_changed = [g for g in snap0 if g in snap1 and snap0[g]["role"] != snap1[g]["role"]]
    legend_changes = []
    for gid, info in (base.get("legends") or {}).items():
        el = _element(manifest, gid)
        if el is None:
            continue
        entries = next(
            (f.get("options") for f in el.get("editable") or [] if f.get("prop") == "entry_order"),
            [],
        )
        if list(entries or []) != list(info.get("entries") or []):
            legend_changes.append({"gid": gid, "before": info.get("entries"), "after": entries})
    structure = {
        "missing": missing,
        "extra": extra,
        "role_changed": role_changed,
        "legend_entries_changed": legend_changes,
    }
    structure_ok = not (missing or extra or role_changed or legend_changes)

    # ---- 受保护属性 ----
    protected_changes = []
    for gid, s0 in snap0.items():
        s1 = snap1.get(gid)
        if s1 is None:
            continue
        role = s0["role"]
        auto_ticks = role == "ticks" and _ticks_group_auto(snap0, gid)
        auto_label = role == "ticklabel" and _tick_mode_auto(snap0, gid)
        for prop, v0 in s0["props"].items():
            key = (gid, prop)
            if key in ok_keys:
                continue
            if key in adj_keys and (prop == "position" or gid in relocated):
                continue
            if prop not in s1["props"]:
                continue
            if auto_ticks and prop in ("major_values", "major_step", "minor_step"):
                continue  # 自动刻度按尺寸重算：合法的自适应，不是删改
            if auto_label and prop == "text":
                continue
            if role == "legend" and gid in relocated and prop in ("loc", "loc_frac", "loc_anchor"):
                continue
            v1 = s1["props"][prop]
            if not _same(v0, v1):
                protected_changes.append({"gid": gid, "prop": prop, "before": v0, "after": v1})

    # ---- 字体真的落成了那张脸 ----
    font_unresolved = []
    font_verified: bool | None = None
    fam = targets.get("font_family")
    if fam is not None:
        verdicts = []
        for gid, s1 in snap1.items():
            if (gid, "fontfamily") not in ok_keys:
                continue
            v = family_matches(fam, s1.get("face"))
            mv = family_matches(fam, s1.get("math_face")) if s1.get("math_face") else None
            if v is False or mv is False:
                font_unresolved.append(
                    {
                        "gid": gid,
                        "requested": fam,
                        "face": s1.get("face"),
                        "math_face": s1.get("math_face"),
                    }
                )
            verdicts.append(v)
        font_verified = None if all(v is None for v in verdicts) else not font_unresolved

    # ---- 尺寸 / 字号目标 ----
    size = manifest.get("size_mm") or [0.0, 0.0]
    w1, h1 = float(size[0]), float(size[1])
    size_ok: bool | None = None
    want = _target_size(contract)
    if want is not None:
        size_ok = abs(w1 - want[0]) <= SIZE_TOL_MM and abs(h1 - want[1]) <= SIZE_TOL_MM
    floor_ok: bool | None = None
    if "min_font_pt" in targets or "font_size_pt" in targets:
        floor = targets.get("font_size_pt", targets.get("min_font_pt"))
        below = []
        for el in manifest.get("elements") or []:
            if _field(el, "visible") is False:
                continue
            for prop in FONT_SIZE_PROPS:
                cur = _num(_field(el, prop)) if _has_field(el, prop) else None
                if cur is not None and cur < floor - FONT_TOL_PT:
                    below.append({"gid": el.get("gid"), "prop": prop, "value": cur})
        floor_ok = not below
    uniform_ok: bool | None = None
    if "font_size_pt" in targets:
        want_pt = targets["font_size_pt"]
        off = []
        for el in manifest.get("elements") or []:
            for prop in FONT_SIZE_PROPS:
                cur = _num(_field(el, prop)) if _has_field(el, prop) else None
                if cur is not None and abs(cur - want_pt) > FONT_TOL_PT:
                    off.append({"gid": el.get("gid"), "prop": prop, "value": cur})
        uniform_ok = not off

    # ---- 几何位移预算（相对 B0 按比例缩放后的位置）----
    adjusted = {str(p["gid"]) for p in (patches or []) if str(p.get("prop")) == "position"}
    budget = _budget_report(contract, manifest, adjusted if patches is not None else None)

    # ---- 问题清单：几何（逐元素）+ 规范 ----
    geo_now = geometry_issues(manifest, profile)
    geo = _classify_issues(base["geometry_issues"], geo_now)
    prof = _classify_issues(base["profile_issues"], profile_issues)
    blocking = []
    profile_conflicts = []
    for bucket in ("new", "worsened"):
        for issue in geo[bucket] + prof[bucket]:
            if _blocks(issue, targets):
                blocking.append({**issue, "bucket": bucket, "repair": _repair_kind(issue)})
            elif decided_by_targets(targets, str(issue.get("id"))):
                profile_conflicts.append({**issue, "bucket": bucket})
    repairable = [b for b in blocking if b["repair"]]

    exit_code = EXIT_DONE
    if protected_changes:
        exit_code = EXIT_PROTECTED_CHANGED
    elif not structure_ok:
        exit_code = EXIT_PROTECTED_CHANGED
    elif font_unresolved:
        exit_code = EXIT_FONT_UNAVAILABLE
    elif size_ok is False or floor_ok is False or uniform_ok is False:
        exit_code = EXIT_CONSTRAINT_CONFLICT
    elif budget["over"]:
        exit_code = EXIT_BUDGET_EXCEEDED
    elif blocking:
        exit_code = EXIT_CONSTRAINT_CONFLICT
    return {
        "ok": exit_code == EXIT_DONE,
        "exit": exit_code,
        "contract_id": contract["contract_id"],
        "size_mm": [w1, h1],
        "targets_met": {
            "size": size_ok,
            "font_family": font_verified,
            "font_floor": floor_ok,
            "font_size": uniform_ok,
        },
        "protected_changes": protected_changes,
        "structure": structure,
        "font_unresolved": font_unresolved,
        "geometry_issues": geo,
        "profile_issues": prof,
        "blocking": blocking,
        "repairable": repairable,
        "profile_conflicts": profile_conflicts,
        "budget": budget,
    }


def _target_size(contract: dict) -> list[float] | None:
    t = contract["targets"]
    w0, h0 = contract["baseline"]["size_mm"]
    if "width_mm" in t and "height_mm" in t:
        return [t["width_mm"], t["height_mm"]]
    if "width_mm" in t:
        return [t["width_mm"], round(h0 * t["width_mm"] / w0, 3)] if w0 else None
    if "height_mm" in t:
        return [round(w0 * t["height_mm"] / h0, 3), t["height_mm"]] if h0 else None
    return None


def _budget_report(contract: dict, manifest: dict, adjusted: set[str] | None = None) -> dict:
    """每个顶层子图相对 B0（按比例缩放后）的边位移与保留比例。`adjusted` 给了就只对
    这些子图判超预算（其余的位移是布局引擎的，只报不挡）；None = 全部都判。"""
    base = contract["baseline"]
    size = manifest.get("size_mm") or [0.0, 0.0]
    w1, h1 = float(size[0]), float(size[1])
    shifts = {}
    over = []
    worst = 0.0
    for gid, pos0 in (base.get("axes_positions") or {}).items():
        el = _element(manifest, gid)
        pos1 = _field(el, "position") if el is not None else None
        if not isinstance(pos1, (list, tuple)) or len(pos1) != 4:
            continue
        x0, y0, w0, h0 = (float(v) for v in pos0)
        x1, y1, w1_, h1_ = (float(v) for v in pos1)
        edges = {
            "left": (x1 - x0) * w1,
            "right": ((x1 + w1_) - (x0 + w0)) * w1,
            "bottom": (y1 - y0) * h1,
            "top": ((y1 + h1_) - (y0 + h0)) * h1,
        }
        keep_w = (w1_ / w0) if w0 else 1.0
        keep_h = (h1_ / h0) if h0 else 1.0
        shifts[gid] = {
            "edge_shift_mm": {k: round(v, 3) for k, v in edges.items()},
            "keep_ratio": [round(keep_w, 4), round(keep_h, 4)],
        }
        judged = adjusted is None or gid in adjusted
        shifts[gid]["judged"] = judged
        for side, mm in edges.items():
            limit = EDGE_SHIFT_BUDGET_FRAC * (w1 if side in ("left", "right") else h1)
            worst = max(worst, abs(mm))
            if judged and abs(mm) > limit + 1e-6:
                over.append(
                    {
                        "gid": gid,
                        "side": side,
                        "shift_mm": round(mm, 3),
                        "limit_mm": round(limit, 3),
                    }
                )
        for axis, keep in (("w", keep_w), ("h", keep_h)):
            if judged and keep < AXES_KEEP_FRAC - 1e-9:
                over.append(
                    {"gid": gid, "keep": axis, "ratio": round(keep, 4), "min": AXES_KEEP_FRAC}
                )
    return {"axes": shifts, "max_edge_shift_mm": round(worst, 3), "over": over}


# ----------------------------- 局部修复：外边距 ------------------------------
def _union(rects):
    xs0 = min(r[0] for r in rects)
    ys0 = min(r[1] for r in rects)
    xs1 = max(r[2] for r in rects)
    ys1 = max(r[3] for r in rects)
    return (xs0, ys0, xs1, ys1)


def _groups(intervals: dict[str, tuple[float, float]]) -> list[list[str]]:
    """按区间重叠把 axes 并成列 / 行（并查集）。"""
    gids = sorted(intervals)
    parent = {g: g for g in gids}

    def find(g):
        while parent[g] != g:
            parent[g] = parent[parent[g]]
            g = parent[g]
        return g

    for i, a in enumerate(gids):
        for b in gids[i + 1 :]:
            a0, a1 = intervals[a]
            b0, b1 = intervals[b]
            if min(a1, b1) - max(a0, b0) > 1e-6:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for g in gids:
        groups.setdefault(find(g), []).append(g)
    out = list(groups.values())
    out.sort(key=lambda members: min(intervals[m][0] for m in members))
    return out


def _axes_boxes(positions: dict[str, list]) -> dict[str, tuple[float, float, float, float]]:
    """`position`（matplotlib 底原点 [x, y, w, h]）→ y 向下的 (x0, y0, x1, y1)。"""
    out = {}
    for gid, pos in positions.items():
        x, y, w, h = (float(v) for v in pos)
        out[gid] = (x, 1.0 - (y + h), x + w, 1.0 - y)
    return out


def _decorations(manifest: dict, ax_gid: str) -> list:
    rects = []
    prefix = ax_gid + "."
    for el in manifest.get("elements") or []:
        gid = str(el.get("gid", ""))
        if not gid.startswith(prefix):
            continue
        r = interference.visible_rect(el)
        if r is not None:
            rects.append(r)
    return rects


def _pack_axis(
    contract: dict,
    manifest: dict,
    axis: str,
    size_now: tuple[float, float],
) -> dict:
    """一个方向上的重排：列（x）或行（y）。返回 `{"positions": {gid: (lo, hi)}}` 或 `{"conflict": …}`。

    算法与 tight_layout 同一思路：每个子图两侧需要的**装饰物 mm 数**是刚性的
    （文字不随图幅缩放），剩下的空间按 B0 里各列 / 行的比例分。留白与列间距沿用
    B0 的 mm 数按比例缩放（不低于 `PAD_MIN_MM`）；B0 里本来就相互压着的，间距
    目标取 0（不加重也不顺手大修）。
    """
    base = contract["baseline"]
    W0, H0 = base["size_mm"]
    W1, H1 = size_now
    lo_i, hi_i = (0, 2) if axis == "x" else (1, 3)
    L0, L1 = (W0, W1) if axis == "x" else (H0, H1)
    ratio = L1 / L0 if L0 else 1.0
    boxes0 = _axes_boxes(base["axes_positions"])
    if not boxes0:
        return {"positions": {}}
    boxes1 = _axes_boxes(
        {
            gid: _field(_element(manifest, gid) or {}, "position")
            for gid in boxes0
            if _field(_element(manifest, gid) or {}, "position") is not None
        }
    )
    intervals0 = {g: (b[lo_i], b[hi_i]) for g, b in boxes0.items()}
    groups = _groups(intervals0)

    # 每组：B0 装饰后的范围（算间距 / 留白用）与现在需要的两侧 mm
    rects0 = base["rects"]
    ext0, need_lo, need_hi, span0 = {}, {}, {}, {}
    for idx, members in enumerate(groups):
        lo = min(intervals0[m][0] for m in members)
        hi = max(intervals0[m][1] for m in members)
        span0[idx] = hi - lo
        dec0 = [tuple(rects0[g]) for g in rects0 if interference.owner_axes(g) in members]
        dec0 += [boxes0[m] for m in members]
        u = _union(dec0)
        ext0[idx] = (u[lo_i], u[hi_i])
        nlo = nhi = 0.0
        for m in members:
            cur = boxes1.get(m) or boxes0[m]
            dec = _decorations(manifest, m) + [cur]
            u1 = _union(dec)
            nlo = max(nlo, (cur[lo_i] - u1[lo_i]) * L1)
            nhi = max(nhi, (u1[hi_i] - cur[hi_i]) * L1)
        need_lo[idx], need_hi[idx] = nlo, nhi

    # figure 级元素（suptitle / fig.text / fig.legend）：在两端的当成保留带
    band_lo = band_hi = 0.0
    all_lo = min(e[0] for e in ext0.values())
    all_hi = max(e[1] for e in ext0.values())
    for el in manifest.get("elements") or []:
        gid = str(el.get("gid", ""))
        if interference.owner_axes(gid) or el.get("role") in ("figure", "axes", "axes3d"):
            continue
        r0 = rects0.get(gid)
        r1 = interference.visible_rect(el)
        if r0 is None or r1 is None:
            continue
        if r0[hi_i] <= all_lo + 1e-6:
            band_lo = max(band_lo, r1[hi_i] * L1)
        elif r0[lo_i] >= all_hi - 1e-6:
            band_hi = max(band_hi, (1.0 - r1[lo_i]) * L1)

    pad_lo = max(
        PAD_MIN_MM, max(0.0, all_lo * L0) * ratio, band_lo + PAD_MIN_MM if band_lo else 0.0
    )
    pad_hi = max(
        PAD_MIN_MM, max(0.0, (1.0 - all_hi) * L0) * ratio, band_hi + PAD_MIN_MM if band_hi else 0.0
    )
    gaps = []
    for idx in range(len(groups) - 1):
        g0 = (ext0[idx + 1][0] - ext0[idx][1]) * L0
        gaps.append(max(0.0, g0) * ratio if g0 > 0 else 0.0)
        if g0 > 0:
            gaps[-1] = max(gaps[-1], PAD_MIN_MM)

    fixed = pad_lo + pad_hi + sum(gaps) + sum(need_lo.values()) + sum(need_hi.values())
    avail = L1 - fixed
    total0 = sum(span0.values())
    if avail <= 0 or total0 <= 0:
        return {
            "conflict": {
                "axis": axis,
                "reason": "decorations_exceed_figure",
                "needed_mm": round(fixed, 3),
                "available_mm": round(L1, 3),
            }
        }
    positions: dict[str, tuple[float, float]] = {}
    cursor = pad_lo
    for idx, members in enumerate(groups):
        width = avail * span0[idx] / total0
        proportional = span0[idx] * L1
        if width < MIN_AXES_MM or width < AXES_KEEP_FRAC * proportional:
            return {
                "conflict": {
                    "axis": axis,
                    "reason": "axes_too_small",
                    "group": members,
                    "width_mm": round(width, 3),
                    "min_mm": round(max(MIN_AXES_MM, AXES_KEEP_FRAC * proportional), 3),
                    "needed_mm": round(fixed, 3),
                    "available_mm": round(L1, 3),
                }
            }
        cursor += need_lo[idx]
        g_lo, g_hi = cursor, cursor + width
        c0 = min(intervals0[m][0] for m in members)
        c1 = max(intervals0[m][1] for m in members)
        for m in members:
            a0, a1 = intervals0[m]
            u0 = (a0 - c0) / (c1 - c0) if c1 > c0 else 0.0
            u1 = (a1 - c0) / (c1 - c0) if c1 > c0 else 1.0
            positions[m] = ((g_lo + u0 * width) / L1, (g_lo + u1 * width) / L1)
        cursor = g_hi + need_hi[idx] + (gaps[idx] if idx < len(gaps) else 0.0)
    return {"positions": positions}


def adapt_margins(contract: dict, manifest: dict, *, directions: set[str] | None = None) -> dict:
    """外边距 / 子图间距的确定性候选：返回 `{"patches": [...], "changed": [...]}`，
    装不下时 `{"conflict": {...}}`。只重排 `directions` 里的方向（默认两个都排）。"""
    size = manifest.get("size_mm") or contract["baseline"]["size_mm"]
    W1, H1 = float(size[0]), float(size[1])
    dirs = directions or {"x", "y"}
    base_pos = contract["baseline"]["axes_positions"]
    new_x = _pack_axis(contract, manifest, "x", (W1, H1)) if "x" in dirs else {"positions": {}}
    if "conflict" in new_x:
        return {"conflict": new_x["conflict"]}
    new_y = _pack_axis(contract, manifest, "y", (W1, H1)) if "y" in dirs else {"positions": {}}
    if "conflict" in new_y:
        return {"conflict": new_y["conflict"]}
    patches, changed = [], []
    for gid, pos0 in base_pos.items():
        el = _element(manifest, gid)
        cur = _field(el, "position") if el is not None else None
        if not isinstance(cur, (list, tuple)) or len(cur) != 4:
            continue
        x, y, w, h = (float(v) for v in cur)
        x0, x1 = new_x["positions"].get(gid, (x, x + w))
        yd0, yd1 = new_y["positions"].get(gid, (1.0 - (y + h), 1.0 - y))
        new = [round(x0, 5), round(1.0 - yd1, 5), round(x1 - x0, 5), round(yd1 - yd0, 5)]
        if all(abs(a - b) <= 1e-4 for a, b in zip(new, (x, y, w, h))):
            continue
        patches.append({"gid": gid, "prop": "position", "value": new})
        changed.append(
            {
                "gid": gid,
                "prop": "position",
                "before": [round(float(v), 5) for v in cur],
                "after": new,
                "shift_mm": {
                    "left": round((new[0] - x) * W1, 3),
                    "right": round(((new[0] + new[2]) - (x + w)) * W1, 3),
                    "bottom": round((new[1] - y) * H1, 3),
                    "top": round(((new[1] + new[3]) - (y + h)) * H1, 3),
                },
            }
        )
    return {"patches": patches, "changed": changed}


def repair_directions(blocking: list[dict]) -> set[str]:
    """由阻断项推「哪个方向需要重排」：裁切按边，压到别的子图按重叠形状。"""
    dirs: set[str] = set()
    for issue in blocking:
        d = issue.get("detail") or {}
        side = d.get("side")
        if side in ("left", "right"):
            dirs.add("x")
        elif side in ("top", "bottom"):
            dirs.add("y")
        elif issue.get("id") in (
            interference.CHECK_TEXT_OVER_AXES,
            interference.CHECK_TEXT_OVERLAP,
        ):
            ov = d.get("overlap_mm") or [0, 0]
            # 咬得浅的那个方向是分开它们的方向
            dirs.add("x" if ov[0] <= ov[1] else "y")
    return dirs


# ----------------------------- 局部修复：图例 --------------------------------
def legend_candidates(contract: dict, legend_gid: str) -> list[str]:
    """一个图例在自己子图里可以试的预设位置，离原位近的先。图例被用户拖动过 /
    锚在外面（B0 有 loc_anchor）的不给候选——那是设计，不是排版。"""
    info = (contract["baseline"].get("legends") or {}).get(legend_gid)
    if not info or info.get("loc_anchor") is not None:
        return []
    if (legend_gid, "loc") not in adjust_keys(contract):
        return []
    cur = str(info.get("loc") or "best")
    cx, cy = _LOC_GRID.get(cur, (1, 1))
    ranked = sorted(
        (loc for loc in LEGEND_LOCS if loc != cur),
        key=lambda loc: (
            abs(_LOC_GRID[loc][0] - cx) + abs(_LOC_GRID[loc][1] - cy),
            LEGEND_LOCS.index(loc),
        ),
    )
    return ranked


def legend_issue_count(issues: list[dict], legend_gid: str) -> int:
    """这一版里与某个图例有关的确定性干涉条数（挑候选用）。"""
    n = 0
    for issue in issues:
        if issue.get("id") not in interference.CHECK_IDS:
            continue
        if not (issue.get("detail") or {}).get("certain"):
            continue
        if any(
            g == legend_gid or interference.legend_of(g) == legend_gid
            for g in issue.get("gids") or []
        ):
            n += 1
    return n
