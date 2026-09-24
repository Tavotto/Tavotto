"""按出版规范修图：规范问题 → 最小修改计划 → 对真实渲染的裁决（桌面「问题」面板的修复）。

一句话：**哪里违规由预检说，改成多少由这里算，改完对不对由真实渲染说。**

桌面端原来的修复是前端逐条算、盲写 override（`web/src/lib/issueFix.ts`）：
每条规则只算「对我这条最省事的那个数」，于是 7 pt 的刻度被提到 8.5 pt、比
8.25 pt 的轴标题还大（层级倒过来）；刻度字变大把轴标题挤得更出界也没人管；
字体一条都不修。这里把**面板内部**的修复收成一条事务（ADR 0051 的桌面入口）：

* **哪些元素违规**：只认 `preflight.run()` 报出来的 gid——与问题面板同一个
  求值器（两侧靠 golden vectors 对齐），这里不写第二份判据。
* **改成多少**：`plan()`。字号按角色层级抬升（标题 ≥ 轴标题 ≥ 刻度 / 图例 /
  其余文字），抬了下层就把上层补齐到同一档，绝不让层级倒过来；字体族改成规范的
  拉丁字体；线宽吸到最近档位；刻度朝向、图例边框、封闭坐标轴、字重按规范写。
  所有数值按**页面上读者量到的 pt** 算，再按面板缩放换回脚本坐标系。
* **改完对不对**：`verdict()`。拿修改后真实渲染的 manifest 对着 B0：
  `normalize.compare()` 管受保护属性、结构、字体真的落成了那张脸、几何新增 /
  加重；这里再加一条——**计划要修的每一条都真的不见了**。少一条就不提交。

纯标准库、不 import matplotlib：Flask 父进程在 import 链上（进程边界）。
渲染由调用方（`app.py` 的 `/api/engine/specfix`）拿现有 worker 做。
"""

from __future__ import annotations

import math

from . import normalize, preflight

#: 这里修得了的规则（都是**面板内部**的；画布标注与页面宽度在前端，那边没有渲染）。
FIXABLE_RULES = (
    "font-below-absolute-floor",
    "font-too-small",
    "font-too-large",
    "legend-font-size",
    "font-family-substituted",
    "line-width-off-preset",
    "tick-direction",
    "legend-frame",
    "spines-not-enclosed",
    "text-weight-policy",
    "element-outside-figure",
)

#: 不在 `plan()` 里算、由外边距重排（ADR 0051 的 `adapt_margins`，有预算、有真实渲染
#: 裁决）来修的规则。挪的是子图的位置，不是那条文字——文字本身一个属性都不动
LAYOUT_RULES = ("element-outside-figure",)

#: 「全部处理」不碰的等级：建议档是口味（轴标题加不加粗），只在用户逐条点它时才改。
BATCH_SKIP_SEVERITIES = ("suggestion",)

FONT_RULES = (
    "font-below-absolute-floor",
    "font-too-small",
    "font-too-large",
    "legend-font-size",
)

#: 带字号的 prop（与 `preflight._iter_font_elements` 同一组）
FONT_SIZE_PROPS = ("fontsize", "tick_fontsize", "title_fontsize")

#: 字号层级：数大的角色字号不许比数小的角色小。没登记的角色（刻度、图例、
#: 图内文字、色条……）都在最底层。只用于「抬了下层要不要顺带补上层」。
ROLE_RANK = {"title": 3, "axis_label": 2}

#: 改一个 prop 时**按引擎语义会跟着变**的别的 prop（真实渲染里实测到的连带）。
#: 不登记的话裁决会把它们判成「受保护属性被改了」而挡掉一次正确的修复：
#: `spine_linewidth` 是四条边的总开关，四边各自的线宽跟着变；曲线的 `linewidth`
#: 变了，跟随源的图例示意线（ADR 0034 的 `follow_source`）跟着变。
COUPLED_PROPS = {
    "spine_linewidth": tuple(f"spine_{s}_linewidth" for s in ("top", "right", "bottom", "left")),
    "linewidth": ("handle_linewidth",),
}

#: 人用的 0.5 pt 档格（字号）；与前端 `issueFix.ts` 的 GRID 同值
GRID = 0.5

#: 修复**引入**的规范问题到这个等级就挡事务（见 `verdict`）
STRICT_SEVERITIES = ("error", "warn")

EXIT_DONE = "done"
EXIT_NOTHING_TO_DO = "nothing_to_do"
EXIT_NOT_RESOLVED = "not_resolved"


def _field(el: dict, prop: str):
    return normalize._field(el, prop)


def _has(el: dict, prop: str) -> bool:
    return normalize._has_field(el, prop)


def _num(v) -> float | None:
    return normalize._num(v)


def _up(v: float) -> float:
    return math.ceil(v / GRID - 1e-9) * GRID


def _down(v: float) -> float:
    return math.floor(v / GRID + 1e-9) * GRID


def _rank(role: str) -> int:
    return ROLE_RANK.get(role, 1)


def profile_issues(manifest: dict, profile: dict, scale: float) -> list[dict]:
    """这张图的规范问题，**逐 gid 展开**。

    预检按规则聚合（一条规则一个条目、gid 累积）；事务要按 `(规则, gid)` 配对前后
    两份清单——聚合形态下只修了其中一个 gid，剩下那几个会以「新 gid 组合」的身份
    出现，被当成新增问题挡掉事务。`element-outside-figure` 不进来：它在
    `normalize.geometry_issues()` 里逐元素、带毫米数地算过一遍，这里再算是同一件事
    说两遍（而且展开后每个 gid 都带着最糟那一个的毫米数，量不准）。
    """
    spec = preflight.spec_from_manifest(manifest, scale=scale)
    out: list[dict] = []
    for issue in preflight.run(spec, profile):
        if issue.get("id") == "element-outside-figure":
            continue
        gids = issue.get("gids") or []
        if not gids:
            out.append(issue)
            continue
        for gid in gids:
            out.append({**issue, "gids": [gid]})
    return out


def all_issues(manifest: dict, profile: dict, scale: float) -> list[dict]:
    """选修复对象用的完整清单：规范问题（逐 gid）+ 逐元素的裁切（带毫米数与边）。"""
    return profile_issues(manifest, profile, scale) + normalize.per_element_clipping(manifest)


def select(issues: list[dict], only: list[dict] | None) -> list[tuple[str, str]]:
    """要修哪些 `(规则, gid)`。`only` 为空 = 「全部处理」：可修规则里去掉建议档。"""
    fixable = [i for i in issues if i.get("id") in FIXABLE_RULES and i.get("gids")]
    if only:
        want = {(str(o.get("rule")), str(o.get("gid") or "")) for o in only}
        rules_any_gid = {r for r, g in want if not g}
        return [
            (i["id"], i["gids"][0])
            for i in fixable
            if (i["id"], i["gids"][0]) in want or i["id"] in rules_any_gid
        ]
    return [
        (i["id"], i["gids"][0]) for i in fixable if i.get("severity") not in BATCH_SKIP_SEVERITIES
    ]


# ----------------------------- 计划 -----------------------------------------
def plan(manifest: dict, profile: dict, *, scale: float, targets: list[tuple[str, str]]) -> dict:
    """`(规则, gid)` → patch 列表。

    返回 `{patches, changes, skipped}`：
    * `patches`：要追加进全量列表的 `{gid, prop, value}`（脚本坐标系）；
    * `changes`：给界面与留档的逐条说明 `{rule, gid, prop, before, after}`，
      `after` / `before` 是**页面上的**值（字号、线宽乘过缩放）；为保层级顺带
      抬的那几条 `rule` 是 `"keep-hierarchy"`；
    * `skipped`：算不出安全目标值的 `(规则, gid)` 与理由（不假装修了）。
    """
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError(f"scale 必须为正: {scale!r}")
    by_gid = {str(el.get("gid", "")): el for el in manifest.get("elements") or []}
    patches: dict[tuple[str, str], object] = {}
    changes: list[dict] = []
    skipped: list[dict] = []

    def put(rule: str, gid: str, prop: str, value, before, after) -> None:
        patches[(gid, prop)] = value
        changes.append({"rule": rule, "gid": gid, "prop": prop, "before": before, "after": after})

    font_targets: dict[str, list[str]] = {}
    for rule, gid in targets:
        el = by_gid.get(gid)
        if el is None:
            skipped.append({"rule": rule, "gid": gid, "reason": "element_missing"})
            continue
        if rule in FONT_RULES:
            font_targets.setdefault(gid, []).append(rule)
            continue
        if rule in LAYOUT_RULES:
            continue  # 由调用方的外边距重排修（见 LAYOUT_RULES）
        step = _plan_one(rule, el, profile, scale)
        if step is None:
            skipped.append({"rule": rule, "gid": gid, "reason": "no_safe_value"})
            continue
        for prop, value, before, after in step:
            put(rule, gid, prop, value, before, after)

    if font_targets:
        for item in _plan_fonts(manifest, profile, scale, font_targets):
            if "reason" in item:
                skipped.append(item)
            else:
                put(
                    item["rule"],
                    item["gid"],
                    item["prop"],
                    item["value"],
                    item["before"],
                    item["after"],
                )

    return {
        "patches": [{"gid": g, "prop": p, "value": v} for (g, p), v in patches.items()],
        "changes": changes,
        "skipped": skipped,
    }


def _plan_one(rule: str, el: dict, profile: dict, scale: float) -> list[tuple] | None:
    """非字号的那几条：一条规则 → 这个元素上的 `(prop, 脚本值, 页面前值, 页面后值)`。"""
    axis = profile.get("axis_policy") or {}
    if rule == "font-family-substituted":
        want = (profile.get("font_family") or {}).get("latin")
        if not isinstance(want, str) or not want or not _has(el, "fontfamily"):
            return None
        return [("fontfamily", want, _field(el, "fontfamily"), want)]
    if rule == "tick-direction":
        want = axis.get("tick_direction")
        if want not in ("in", "out", "inout") or not _has(el, "direction"):
            return None
        return [("direction", want, _field(el, "direction"), want)]
    if rule == "legend-frame":
        if not _has(el, "frameon"):
            return None
        return [("frameon", False, _field(el, "frameon"), False)]
    if rule == "spines-not-enclosed":
        sides = [s for s in ("top", "right", "bottom", "left") if _field(el, f"spine_{s}") is False]
        if not sides:
            return None
        return [(f"spine_{s}", True, False, True) for s in sides]
    if rule == "text-weight-policy":
        want = (profile.get("text_weight_policy") or {}).get(str(el.get("role", "")))
        if want not in ("bold", "normal") or not _has(el, "weight"):
            return None
        return [("weight", want, _field(el, "weight"), want)]
    if rule == "line-width-off-preset":
        return _plan_line_width(el, profile, scale)
    return None


def _plan_line_width(el: dict, profile: dict, scale: float) -> list[tuple] | None:
    """吸到最近的档位；等距时取更细的一档（保守：不无声加粗数据线）。

    哪个 prop 由角色决定，与 `preflight._check_panel_axes` 报的三种形状一一对应。
    """
    role = str(el.get("role", ""))
    if role == "axes":
        prop = "spine_linewidth"
        presets = (profile.get("axis_policy") or {}).get("frame_linewidth_pt") or []
    elif role == "legend_text":
        prop = "handle_linewidth"
        presets = profile.get("line_widths_pt") or []
    else:
        prop = "linewidth"
        presets = profile.get("line_widths_pt") or []
    presets = [float(p) for p in presets if _num(p) is not None and float(p) > 0]
    cur = _num(_field(el, prop))
    if not presets or cur is None or cur <= 0:
        return None
    eff = cur * scale
    best = min(presets, key=lambda p: (abs(p - eff), p))
    value = round(best / scale, 3)
    if value <= 0:
        return None
    return [(prop, value, round(eff, 3), best)]


def _font_entries(manifest: dict) -> list[dict]:
    """manifest 里一切可见的带字号的 `(gid, prop, role, 当前脚本值)`。"""
    out = []
    for el in manifest.get("elements") or []:
        if _field(el, "visible") is False:
            continue
        for prop in FONT_SIZE_PROPS:
            v = _num(_field(el, prop)) if _has(el, prop) else None
            if v is not None and v > 0:
                out.append(
                    {
                        "gid": str(el.get("gid", "")),
                        "prop": prop,
                        "role": str(el.get("role", "")),
                        "size": v,
                    }
                )
    return out


def _font_bounds(entry: dict, profile: dict) -> tuple[float, float | None]:
    """这个字号**能通过**的页面 pt 区间 `[lo, hi]`（`hi` 为 None = 无上限）。

    绝对下限不含等号（`eff <= floor` 才算违规，ADR 0006 / 0029），所以正好等于
    floor 的那一档过不了、再上一档；图例（`legend` 角色的 `fontsize`）另外夹进
    图例区间（两端闭）。
    """
    fb = preflight.profiles_mod.FALLBACK_MIN_FONT_SIZE_PT
    strict = _num(profile.get("min_effective_font_size_pt"))
    floor = _num(profile.get("absolute_min_font_size_pt"))
    strict = fb if strict is None else strict
    floor = fb if floor is None else floor
    lo = _up(max(strict, floor))
    if lo <= floor:
        lo += GRID
    hi = _num(profile.get("max_font_size_pt"))
    if entry["role"] == "legend" and entry["prop"] == "fontsize":
        legend = profile.get("legend_policy") or {}
        llo, lhi = _num(legend.get("min_font_size_pt")), _num(legend.get("max_font_size_pt"))
        if llo is not None:
            lo = max(lo, llo)
        if lhi is not None:
            hi = lhi if hi is None else min(hi, lhi)
    return lo, hi


def _plan_fonts(
    manifest: dict, profile: dict, scale: float, targets: dict[str, list[str]]
) -> list[dict]:
    """字号：被点名的夹进能通过的区间，再按层级把上层补齐。

    「最小字号」只补齐低于阈值的——不把所有文字压成一个数（那会抹平层级，
    ADR 0051 同一条纪律）。补齐之后，若某个下层角色（刻度 / 图例）被抬得比上层
    （轴标题 / 标题）还大，上层跟着抬到同一档：用户要的是「字号合规」，不是
    「刻度比轴标题还醒目」。上层被自己的上限挡住时就停在上限，不越界。
    """
    entries = _font_entries(manifest)
    eff = {(e["gid"], e["prop"]): e["size"] * scale for e in entries}
    new: dict[tuple[str, str], tuple[float, str]] = {}
    out: list[dict] = []
    for e in entries:
        rules = targets.get(e["gid"])
        if not rules:
            continue
        key = (e["gid"], e["prop"])
        lo, hi = _font_bounds(e, profile)
        if hi is not None and lo > hi:
            out.extend({"rule": r, "gid": e["gid"], "reason": "constraint_conflict"} for r in rules)
            continue
        cur = eff[key]
        if cur < lo - 1e-9:
            target = lo
        elif hi is not None and cur > hi + 1e-9:
            target = _down(hi)
            if target < lo:
                target = lo
        else:
            continue  # 这个 prop 本来就在区间里（同一元素上的另一个字号 prop 违规）
        new[key] = (target, rules[0])

    # 层级：只因**这一轮抬高**而倒过来的才管；图里原本就有的倒挂不是我们造成的。
    # 自下而上一层一层补：轴标题补齐之后，标题再对着「刻度 + 轴标题」补。
    rank_of = {(e["gid"], e["prop"]): _rank(e["role"]) for e in entries}
    for level in sorted({r for r in rank_of.values() if r > 1}):
        lifted = [t for k, (t, _) in new.items() if rank_of[k] < level and t > eff[k] + 1e-9]
        if not lifted:
            continue
        need = max(lifted)
        for e in entries:
            key = (e["gid"], e["prop"])
            if rank_of[key] != level:
                continue
            cur = new[key][0] if key in new else eff[key]
            _, hi = _font_bounds(e, profile)
            target = need if hi is None else min(need, hi)
            if target > cur + 1e-9:
                new[key] = (target, new[key][1] if key in new else "keep-hierarchy")

    by_key = {(e["gid"], e["prop"]): e for e in entries}
    for key, (target, rule) in new.items():
        e = by_key[key]
        # 取整方向跟着目标走：往上抬就往上取，否则两位小数的舍入会把结果推回违规那侧
        raw = target / scale * 100
        value = (math.ceil(raw - 1e-9) if target >= eff[key] else math.floor(raw + 1e-9)) / 100
        if value <= 0:
            out.append({"rule": rule, "gid": e["gid"], "reason": "no_safe_value"})
            continue
        out.append(
            {
                "rule": rule,
                "gid": e["gid"],
                "prop": e["prop"],
                "value": value,
                "before": round(eff[key], 2),
                "after": round(target, 2),
            }
        )
    return out


# ----------------------------- 裁决 -----------------------------------------
def build_contract(
    manifest: dict,
    profile: dict,
    *,
    scale: float,
    base_patches: list[dict],
    planned: list[dict],
) -> dict:
    """B0 + 修改约定。允许集合 = 计划里出现过的 **prop**（在全图范围内）。

    为什么按 prop 而不是按 `(gid, prop)`：刻度组 / 图例的字号与字体会落到它们的
    子元素上（`axes_0.xticks` 的字号改了，`axes_0.xticklabels_3` 的字号跟着变），
    只放行点名的 gid 会把这种合法的连带变化判成越权。与 `normalize` 对 `min_font_pt`
    放行全图字号 prop 是同一个取舍；非预期的连带变化由「规范问题新增 / 加重」那条
    尺子兜着（改错的字号会以新问题的身份出现）。
    """
    fam = next((p["value"] for p in planned if p["prop"] == "fontfamily"), None)
    targets = {"font_family": fam} if isinstance(fam, str) and fam else {}
    contract = normalize.build_contract(
        manifest,
        targets,
        profile=profile,
        base_patches=base_patches,
        profile_issues=profile_issues(manifest, profile, scale),
        meta={"source": "desktop-specfix", "scale": scale},
    )
    props = {p["prop"] for p in planned}
    for prop in list(props):
        props.update(COUPLED_PROPS.get(prop, ()))
    contract["allowed"] = [
        [gid, prop]
        for gid, snap in contract["baseline"]["snapshot"].items()
        for prop in snap["props"]
        if prop in props
    ]
    return contract


def verdict(
    contract: dict,
    manifest: dict,
    profile: dict,
    *,
    scale: float,
    targets: list[tuple[str, str]],
    patches: list[dict],
) -> dict:
    """修改后的真实渲染对着 B0：`normalize.compare()` 的全部判据 + 点名的问题真的不见了。"""
    after = profile_issues(manifest, profile, scale)
    v = normalize.compare(
        contract, manifest, profile_issues=after, profile=profile, patches=patches
    )
    # 比 normalize 更严的一条：修复**引入**的规范问题，warn 级也挡。
    # 规范化事务里用户点名的目标可能与规范天然冲突，所以那边只挡 error；而这里
    # 的每一个改动都是我们替用户挑的，「修一条、冒一条」就是用户说的「越修越乱」
    seen = {(b.get("id"), tuple(b.get("gids") or [])) for b in v["blocking"]}
    for bucket in ("new", "worsened"):
        for issue in v["profile_issues"][bucket]:
            key = (issue.get("id"), tuple(issue.get("gids") or []))
            if issue.get("severity") in STRICT_SEVERITIES and key not in seen:
                v["blocking"].append({**issue, "bucket": bucket, "repair": ""})
                seen.add(key)
    if v["ok"] and v["blocking"]:
        v["ok"] = False
        v["exit"] = normalize.EXIT_CONSTRAINT_CONFLICT
    after = after + normalize.per_element_clipping(manifest)
    still = {(i["id"], i["gids"][0]) for i in after if i.get("gids")}
    unresolved = [{"rule": r, "gid": g} for r, g in targets if (r, g) in still]
    v["unresolved"] = unresolved
    if v["ok"] and unresolved:
        v["ok"] = False
        v["exit"] = EXIT_NOT_RESOLVED
    return v


def progressed(new: dict, old: dict) -> bool:
    """一轮外边距重排收不收：阻断项严格更少（`normalize.better_candidate`），或者
    阻断项不增、干净（不越权、不超预算、结构没变），而点名没修好的**严格更少**。

    第二条是给「修复前就已经出界」的那种情况：它不是新增 / 加重，进不了阻断清单，
    只能按「点名的问题少了几条」来量。
    """
    if normalize.better_candidate(new, old):
        return True
    if new["protected_changes"] or new["budget"]["over"]:
        return False
    st = new["structure"]
    if st["missing"] or st["extra"] or st["role_changed"] or st["legend_entries_changed"]:
        return False
    return len(new["blocking"]) <= len(old["blocking"]) and len(new["unresolved"]) < len(
        old["unresolved"]
    )
