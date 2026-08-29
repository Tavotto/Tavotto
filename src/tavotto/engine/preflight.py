"""出版规范预检 —— 规则来自 profile，判据来自 manifest 与版面几何。

**输入是一份规范化的 figure spec**（见 `SPEC_DOC`），不是画布文档、也不是
manifest 本身。这样同一套规则能同时服务两条入口：

  * Codex 的 MCP server（`tavotto_preflight`）：一张图 = 一个 panel、scale=1、
    page 就是 manifest 的 size_mm；
  * Tavotto 画布（`web/src/lib/preflight.ts`）：多面板拼版，每个 panel 带自己
    的 scale（摆放宽度 / 原生宽度）。

TS 侧是同一套规则的第二个求值器（浏览器里跑不了 Python），两侧的判据靠
`tests/golden/preflight_vectors.json` 对齐：**pytest 与 vitest 各跑一遍同一份
向量**，改任一侧必须让两边同时绿。规则常量本身只有一份（profile JSON）。

字号一律按**最终物理尺寸**判：manifest 里的 fontsize 是脚本坐标系里的 pt，
面板被缩到 60% 摆上版面时，读者拿尺子量到的是 fontsize × scale。只看原始
fontsize 会让「缩一缩就放行」成为常态，而那正是投稿被拒的头号原因。
"""

from __future__ import annotations

import re

from . import profiles as profiles_mod

SPEC_DOC = """
figure spec（两侧同源的规范化输入）：

{
  "page":   {"w_mm": float, "h_mm": float, "margin_mm": float},
  "panels": [{
     "id": str, "name": str, "kind": "pdf"|"raster",
     "rect_mm": [x, y, w, h],        # 页面落位（MCP 单图 = [0,0,w,h]）
     "scale": float,                 # 最终尺寸 / 原生尺寸，字号线宽都乘它
     "manifest": {...}|null,         # 引擎 manifest（矢量面板才有）
     "px_w": int|null,               # 位图源的像素宽
     "missing": bool, "stale": bool,
     "render_error": str|null,
     "unapplied_overrides": int,
     "bitmap_embed": bool,           # 翻转/半透明 → PDF 里按位图嵌入
     "hidden": bool
  }],
  "texts":   [{"id": str, "text": str, "size_pt": float, "bold": bool,
               "rect_mm": [x,y,w,h], "hidden": bool}],
  "objects": [{"id": str, "type": str, "rect_mm": [x,y,w,h], "hidden": bool}]
}
"""

#: 页面/几何比较的容差（mm）。比 0.05 更小会被浮点噪音刷屏。
EPS_MM = 0.05

_CJK = re.compile("[⺀-⻿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")

#: 带 fontsize 的角色 → text_weight_policy 的键
_TEXT_ROLES = (
    "text",
    "title",
    "axis_label",
    "legend_text",
    "ticklabel",
    "ticks",
    "legend",
    "colorbar",
)

#: 判定「这条曲线是拟合线」的标签特征（只用于 suggestion，判错也不阻断）
_FIT_WORDS = re.compile(r"fit|拟合|regression|回归|trend|linear", re.I)


def has_cjk(text: object) -> bool:
    return isinstance(text, str) and bool(_CJK.search(text))


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _num_or(value: object, default: float) -> float:
    """取数值，**显式的 0 也算数**；只有真的没给（或不是数）才用 default。

    这一条不许退回 `_num(x) or default` 的写法。Python 的真值判断分不清
    「没表态」与「明确设成 0」，而期刊覆盖里 0 恰恰是有意义的值：
    `widths_mm.tolerance_mm = 0` 是「页宽必须精确等于规范值」、
    `absolute_min_font_size_pt = 0` 是「不设绝对字号下限」。用 `or` 的话
    这两条都会被悄悄换回我们的默认值，而 `web/src/lib/preflight.ts` 那份
    求值器用的是 `??`、忠实执行 0——同一份 spec 在 MCP 与画布两条链路上
    于是给出相反的合规结论，且更危险的方向是 Python 一侧**偷偷放宽**了检查。
    """
    got = _num(value)
    return default if got is None else got


def _field(element: dict, prop: str):
    for f in element.get("editable") or []:
        if f.get("prop") == prop:
            return f.get("value")
    return None


def _axes_of(gid: str) -> str:
    """元素 gid → 它所属的 axes 前缀（figure 级元素回空串）。"""
    return gid.split(".", 1)[0] if gid.startswith("axes_") else ""


def _r2(value: float | None) -> float | None:
    return None if value is None else round(float(value) + 0.0, 2)


class _Sink:
    """检查结果收集器：按 check_id 聚合，等级从 profile 取。"""

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self._items: dict[str, dict] = {}
        self._order: list[str] = []
        #: check_id → 目前见过的最糟排名（见 `add` 的说明）
        self._worst: dict[str, float] = {}

    def add(
        self,
        check_id: str,
        text: str,
        *,
        message: tuple[str, dict],
        object_ids=(),
        gids=(),
        detail: dict | None = None,
        worse: float | None = None,
    ) -> None:
        """一条检查项一个条目，命中的对象累积进 object_ids/gids。

        `message` 是这条问题的**可翻译描述符** `(key, params)`（issue #30）：
        宿主界面（MCP 画布 / Codex webview）按自己的 locale 用
        `errors:preflight.<key>` 渲染，`text` 只是没有那份文案表时的回退。
        key 与 params 必须与 `web/src/lib/preflight.ts` 同名调用**逐字对齐**
        ——golden vectors 连它们一起比（两个求值器只允许有一种说法）。

        **文案与 detail 必须来自同一次命中**（与 `web/src/lib/preflight.ts`
        的 `Sink` 逐条同源）：旧实现保留第一条文案却让 detail 被最后一条无
        条件覆盖，8.1pt 与 8.4pt 先后命中 `font-too-small` 时，文案说 8.1、
        `effective_pt` 却是 8.4，写进 proof 的留档自相矛盾。

        * 带 `worse`（越大越糟的量化排名）：**最糟的那次**同时决定文案与
          detail，其余只贡献 object_ids/gids；
        * 不带 `worse`（family / cmap / 文本这类没法比大小的）：第一次命中
          说了算——与保留第一条文案一致，至少永远自洽。
        """
        item = self._items.get(check_id)
        fresh = item is None
        if item is None:
            item = {
                "id": check_id,
                "severity": profiles_mod.severity_of(self.profile, check_id),
                "text": text,
                "message": {"key": message[0], "params": message[1]},
                "object_ids": [],
                "gids": [],
                "detail": {},
            }
            self._items[check_id] = item
            self._order.append(check_id)
        for oid in object_ids:
            if oid not in item["object_ids"]:
                item["object_ids"].append(oid)
        for gid in gids:
            if gid not in item["gids"]:
                item["gids"].append(gid)
        prev = self._worst.get(check_id)
        ranked = worse is not None
        better = ranked and (prev is None or worse > prev)
        if better:
            self._worst[check_id] = worse
        if not (fresh or better):
            return
        item["text"] = text
        item["message"] = {"key": message[0], "params": message[1]}
        if detail:
            item["detail"] = {**item["detail"], **detail}

    def result(self) -> list[dict]:
        return [self._items[k] for k in self._order]


# ------------------------------ 各组检查 ------------------------------------
def _check_page(spec: dict, profile: dict, sink: _Sink) -> None:
    page = spec.get("page") or {}
    w = _num_or(page.get("w_mm"), 0.0)
    h = _num_or(page.get("h_mm"), 0.0)
    widths = profile.get("widths_mm") or {}
    tol = _num_or(widths.get("tolerance_mm"), 0.5)
    single, double = _num(widths.get("single")), _num(widths.get("double"))
    matched = None
    for name, target in (("single", single), ("double", double)):
        if target is not None and abs(w - target) <= tol:
            matched = name
            break
    if matched is None and (single is not None or double is not None):
        want = "/".join(f"{v:g}" for v in (single, double) if v is not None)
        sink.add(
            "page-width",
            f"页面宽 {w:g}mm 不是规范里的单栏/双栏宽度（{want}mm）",
            message=("pageWidth", {"actual": f"{w:g}", "want": want}),
            detail={"page_w_mm": _r2(w), "single_mm": single, "double_mm": double, "column": None},
        )

    ratios = profile.get("allowed_aspect_ratios") or []
    if ratios and w > 0 and h > 0:
        tol_r = _num_or(profile.get("aspect_tolerance"), 0.04)
        actual = w / h
        best = None
        for r in ratios:
            rw, rh = _num(r.get("w")), _num(r.get("h"))
            if not rw or not rh:
                continue
            target = rw / rh
            rel = abs(actual - target) / target
            if best is None or rel < best[1]:
                best = (r.get("id", f"{rw:g}:{rh:g}"), rel)
            if rel <= tol_r:
                best = (r.get("id", ""), 0.0)
                break
        if best is not None and best[1] > tol_r:
            allowed = "、".join(str(r.get("id")) for r in ratios)
            sink.add(
                "page-aspect",
                f"页面比例 {actual:.3f}（{w:g}×{h:g}mm）不在规范允许的 {allowed} 之内",
                message=(
                    "pageAspect",
                    {"ratio": f"{actual:.3f}", "w": f"{w:g}", "h": f"{h:g}", "allowed": allowed},
                ),
                detail={"aspect": _r2(actual), "closest": best[0]},
            )


def _check_panel_state(panel: dict, sink: _Sink) -> None:
    pid = panel.get("id", "")
    if panel.get("missing"):
        sink.add(
            "missing-asset",
            "面板引用的素材文件不在当前图库中，导出会失败或出空白",
            message=("missingAsset", {}),
            object_ids=[pid],
        )
    if panel.get("render_error"):
        sink.add(
            "render-error",
            "面板最近一次渲染失败，导出前请先修复",
            message=("renderError", {}),
            object_ids=[pid],
            detail={"error": str(panel["render_error"])[:200]},
        )
    if panel.get("stale"):
        sink.add(
            "stale-render",
            "面板的脚本已更新但尚未重建，导出的会是旧图",
            message=("staleRender", {}),
            object_ids=[pid],
        )
    n = int(panel.get("unapplied_overrides") or 0)
    if n > 0:
        sink.add(
            "unapplied-override",
            f"面板有 {n} 条图内修改尚未应用到渲染上，成图会与画布不一致",
            message=("unappliedOverride", {"count": n}),
            object_ids=[pid],
            detail={"count": n},
            worse=n,
        )
    if panel.get("bitmap_embed"):
        sink.add(
            "bitmap-embed",
            "翻转或半透明的面板在 PDF 里按导出 DPI 位图嵌入，矢量文字不保留",
            message=("bitmapEmbed", {}),
            object_ids=[pid],
        )


def _check_panel_raster(panel: dict, profile: dict, sink: _Sink) -> None:
    pid = panel.get("id", "")
    rect = panel.get("rect_mm") or [0, 0, 0, 0]
    w_mm = _num_or(rect[2], 0.0)
    px_w = panel.get("px_w")
    min_dpi = _num_or(profile.get("min_raster_dpi"), 300)
    if px_w and w_mm > 0:
        dpi = float(px_w) / (w_mm / 25.4)
        if dpi < min_dpi:
            sink.add(
                "raster-dpi",
                f"位图面板的等效分辨率 {dpi:.0f}dpi 低于规范的 {min_dpi:g}dpi",
                # JS 的 toFixed(0) 半数进位，Python 的 :.0f 半数取偶：
                # 190.5dpi 会一边说 191、一边说 190。params 必须逐字对齐，
                # 显示格式跟 JS 走（+0.5 截断 = 正数的半数进位）。
                message=("rasterDpi", {"dpi": str(int(dpi + 0.5)), "min": f"{min_dpi:g}"}),
                object_ids=[pid],
                detail={"dpi": _r2(dpi), "min_dpi": min_dpi},
                worse=-dpi,
            )
    # 外部位图内部的文字字号无法可靠判定：如实报 not_verifiable，绝不假装通过
    sink.add(
        "raster-text-not-verifiable",
        "位图面板内部的文字字号无法自动核验（没有矢量文字层），请人工确认其最终字号大于规范下限",
        message=("rasterTextNotVerifiable", {}),
        object_ids=[pid],
    )


def _iter_font_elements(manifest: dict):
    """(element, fontsize) —— manifest 里一切带字号的元素。"""
    for el in manifest.get("elements") or []:
        size = _num(_field(el, "fontsize"))
        if size is not None:
            yield el, size
        tick = _num(_field(el, "tick_fontsize"))  # colorbar 的刻度字号
        if tick is not None:
            yield el, tick
        title = _num(_field(el, "title_fontsize"))  # 图例标题
        if title is not None:
            yield el, title


def _check_panel_fonts(panel: dict, profile: dict, sink: _Sink) -> None:
    manifest = panel.get("manifest")
    if not isinstance(manifest, dict):
        return
    pid = panel.get("id", "")
    scale = _num_or(panel.get("scale"), 1.0)
    strict = _num_or(
        profile.get("min_effective_font_size_pt"), profiles_mod.FALLBACK_MIN_FONT_SIZE_PT
    )
    floor = _num_or(
        profile.get("absolute_min_font_size_pt"), profiles_mod.FALLBACK_MIN_FONT_SIZE_PT
    )
    biggest = _num_or(profile.get("max_font_size_pt"), 72.0)
    fam = profile.get("font_family") or {}
    accepted = {str(x).lower() for x in (fam.get("latin_accepted") or [])}
    flagged = {str(x).lower() for x in (fam.get("latin_substitutes_flagged") or [])}
    cjk = profile.get("cjk_fallback") or {}
    cjk_ok = {str(x).lower() for x in (cjk.get("accepted") or [])}
    weights = profile.get("text_weight_policy") or {}

    for el, size in _iter_font_elements(manifest):
        gid = el.get("gid", "")
        if _field(el, "visible") is False:
            continue
        eff = size * scale
        if eff <= floor:
            sink.add(
                "font-below-absolute-floor",
                f"图内文字的最终有效字号 {eff:.2f}pt 不大于绝对下限 {floor:g}pt",
                message=("fontBelowFloor", {"effective": f"{eff:.2f}", "floor": f"{floor:g}"}),
                object_ids=[pid],
                gids=[gid],
                detail={"effective_pt": _r2(eff), "floor_pt": floor},
                worse=-eff,
            )
        elif eff < strict:
            sink.add(
                "font-too-small",
                f"图内文字的最终有效字号 {eff:.2f}pt 低于规范下限 {strict:g}pt",
                message=("fontTooSmall", {"effective": f"{eff:.2f}", "min": f"{strict:g}"}),
                object_ids=[pid],
                gids=[gid],
                detail={"effective_pt": _r2(eff), "min_pt": strict},
                worse=-eff,
            )
        if eff > biggest:
            sink.add(
                "font-too-large",
                f"图内文字的最终有效字号 {eff:.2f}pt 超过 {biggest:g}pt，通常大于正文字号",
                message=("fontTooLarge", {"effective": f"{eff:.2f}", "max": f"{biggest:g}"}),
                object_ids=[pid],
                gids=[gid],
                detail={"effective_pt": _r2(eff), "max_pt": biggest},
                worse=eff,
            )

    for el in manifest.get("elements") or []:
        gid = el.get("gid", "")
        family = _field(el, "fontfamily")
        text = _field(el, "text")
        if isinstance(family, str) and family:
            low = family.lower()
            if accepted and low not in accepted:
                sink.add(
                    "font-family-substituted",
                    f"图内文字用的是 {family}，规范要求 "
                    f"{fam.get('latin', 'Times New Roman')}"
                    + ("（该字体是常见的替代品，说明目标字体没装上）" if low in flagged else ""),
                    message=(
                        (
                            "fontFamilySubstitutedKnown"
                            if low in flagged
                            else "fontFamilySubstituted"
                        ),
                        {"family": family, "want": fam.get("latin")},
                    ),
                    object_ids=[pid],
                    gids=[gid],
                    detail={"family": family},
                )
            if has_cjk(text) and cjk.get("required") and low not in cjk_ok:
                sink.add(
                    "cjk-fallback-missing",
                    f"含中日韩字符的文字用的是 {family}，没有声明中文 fallback，"
                    "导出 PDF 里会是方框",
                    message=("cjkFallbackMissing", {"family": family}),
                    object_ids=[pid],
                    gids=[gid],
                    detail={"family": family},
                )
        role = el.get("role", "")
        want = weights.get(role)
        if want in ("bold", "normal") and role in _TEXT_ROLES:
            got = _field(el, "weight")
            if isinstance(got, str) and got and got != want:
                sink.add(
                    "text-weight-policy",
                    f"规范建议 {role} 的字重为 {want}（当前 {got}）",
                    message=("textWeightPolicy", {"role": role, "want": want, "got": got}),
                    object_ids=[pid],
                    gids=[gid],
                    detail={"role": role, "want": want, "got": got},
                )


def _check_panel_axes(panel: dict, profile: dict, sink: _Sink) -> None:
    manifest = panel.get("manifest")
    if not isinstance(manifest, dict):
        return
    pid = panel.get("id", "")
    scale = _num_or(panel.get("scale"), 1.0)
    axis = profile.get("axis_policy") or {}
    legend = profile.get("legend_policy") or {}
    presets = [float(v) for v in (profile.get("line_widths_pt") or [])]
    tol = _num_or(profile.get("line_width_tolerance_pt"), 0.08)
    want_dir = axis.get("tick_direction")
    enclosed = bool(axis.get("enclosed_spines"))
    max_labels = int(axis.get("max_tick_labels") or 0)
    label_re = axis.get("label_format_regex")
    palette = profile.get("palette_policy") or {}
    bad_cmaps = {str(c).lower() for c in (palette.get("discouraged_colormaps") or [])}
    good_cmaps = {
        str(c).lower() for group in (palette.get("by_semantic") or {}).values() for c in group
    }

    tick_labels: dict[str, int] = {}
    lines_by_axes: dict[str, list[dict]] = {}
    roles_by_axes: dict[str, set[str]] = {}

    for el in manifest.get("elements") or []:
        gid, role = el.get("gid", ""), el.get("role", "")
        ax = _axes_of(gid)
        roles_by_axes.setdefault(ax, set()).add(role)

        if role == "legend":
            if legend.get("frame") is False and _field(el, "frameon") is True:
                sink.add(
                    "legend-frame",
                    "图例带边框，规范要求图例无框",
                    message=("legendFrame", {}),
                    object_ids=[pid],
                    gids=[gid],
                )
            size = _num(_field(el, "fontsize"))
            lo, hi = _num(legend.get("min_font_size_pt")), _num(legend.get("max_font_size_pt"))
            if size is not None and lo is not None and hi is not None:
                eff = size * scale
                if eff < lo - 1e-9 or eff > hi + 1e-9:
                    sink.add(
                        "legend-font-size",
                        f"图例字号最终有效值 {eff:.2f}pt 不在规范的 {lo:g}–{hi:g}pt 之间",
                        message=(
                            "legendFontSize",
                            {"effective": f"{eff:.2f}", "min": f"{lo:g}", "max": f"{hi:g}"},
                        ),
                        object_ids=[pid],
                        gids=[gid],
                        detail={"effective_pt": _r2(eff)},
                        # 越出区间越远越糟（两边都可能出界）
                        worse=max(lo - eff, eff - hi),
                    )

        if role == "ticks":
            got = _field(el, "direction")
            if want_dir and isinstance(got, str) and got != want_dir:
                sink.add(
                    "tick-direction",
                    f"刻度朝向为 {got}，规范要求 {want_dir}",
                    message=("tickDirection", {"got": got, "want": want_dir}),
                    object_ids=[pid],
                    gids=[gid],
                    detail={"direction": got, "want": want_dir},
                )

        if role == "ticklabel":
            tick_labels[gid.rsplit("_", 1)[0]] = tick_labels.get(gid.rsplit("_", 1)[0], 0) + 1

        if role in ("axes",):
            if enclosed:
                off = [
                    s
                    for s in ("top", "right", "bottom", "left")
                    if _field(el, f"spine_{s}") is False
                ]
                if off:
                    sink.add(
                        "spines-not-enclosed",
                        f"坐标轴未封闭（缺 {', '.join(off)} 边），规范要求封闭坐标轴",
                        message=("spinesNotEnclosed", {"sides": ", ".join(off)}),
                        object_ids=[pid],
                        gids=[gid],
                        detail={"missing": off},
                    )
            lw = _num(_field(el, "spine_linewidth"))
            frame_lw = [float(v) for v in (axis.get("frame_linewidth_pt") or [])]
            if lw is not None and frame_lw:
                eff = lw * scale
                if all(abs(eff - v) > tol for v in frame_lw):
                    sink.add(
                        "line-width-off-preset",
                        f"外框线宽最终有效值 {eff:.2f}pt 不在规范档位 "
                        f"{'/'.join(f'{v:g}' for v in frame_lw)}pt 上",
                        message=(
                            "frameWidthOffPreset",
                            {
                                "effective": f"{eff:.2f}",
                                "presets": "/".join(f"{v:g}" for v in frame_lw),
                            },
                        ),
                        object_ids=[pid],
                        gids=[gid],
                        detail={"effective_pt": _r2(eff)},
                        worse=min(abs(eff - v) for v in frame_lw),
                    )

        if role == "axis_label" and label_re:
            text = _field(el, "text")
            if isinstance(text, str) and text.strip() and not re.match(label_re, text.strip()):
                sink.add(
                    "axis-label-format",
                    f"坐标轴标题「{text.strip()[:30]}」不是规范的"
                    f"「{axis.get('label_format')}」形式",
                    message=(
                        "axisLabelFormat",
                        {"label": text.strip()[:30], "want": axis.get("label_format")},
                    ),
                    object_ids=[pid],
                    gids=[gid],
                    detail={"text": text.strip()[:60]},
                )

        if role in ("line", "scatter", "fill", "bar_series", "bar", "errorbar", "arrow_patch"):
            lw = _num(_field(el, "linewidth"))
            if lw is not None and presets and lw > 0:
                eff = lw * scale
                if all(abs(eff - v) > tol for v in presets):
                    sink.add(
                        "line-width-off-preset",
                        f"线宽最终有效值 {eff:.2f}pt 不在规范档位 "
                        f"{'/'.join(f'{v:g}' for v in presets)}pt 上",
                        message=(
                            "lineWidthOffPreset",
                            {
                                "effective": f"{eff:.2f}",
                                "presets": "/".join(f"{v:g}" for v in presets),
                            },
                        ),
                        object_ids=[pid],
                        gids=[gid],
                        detail={"effective_pt": _r2(eff)},
                        worse=min(abs(eff - v) for v in presets),
                    )
        if role == "line":
            lines_by_axes.setdefault(ax, []).append(el)

        if role in ("image", "colorbar"):
            cmap = _field(el, "cmap")
            if isinstance(cmap, str) and cmap:
                low = cmap.lower()
                if low in bad_cmaps:
                    sink.add(
                        "discouraged-colormap",
                        f"色谱 {cmap} 不是感知均匀的，规范推荐 "
                        f"{palette.get('recommended', 'Scientific colour maps')}",
                        message=(
                            "discouragedColormap",
                            {"cmap": cmap, "recommended": palette.get("recommended")},
                        ),
                        object_ids=[pid],
                        gids=[gid],
                        detail={"cmap": cmap},
                    )
                elif good_cmaps and low not in good_cmaps:
                    sink.add(
                        "palette-semantic",
                        f"色谱 {cmap} 不在推荐的科学色系里（按 sequential / "
                        f"diverging / categorical 语义选：{palette.get('url', '')}）",
                        message=("paletteSemantic", {"cmap": cmap, "url": palette.get("url")}),
                        object_ids=[pid],
                        gids=[gid],
                        detail={"cmap": cmap},
                    )

    for prefix, count in sorted(tick_labels.items()):
        if max_labels and count > max_labels:
            sink.add(
                "tick-label-count",
                f"{prefix} 有 {count} 个刻度标签，规范建议控制在 {max_labels} 个以内",
                message=("tickLabelCount", {"axis": prefix, "count": count, "max": max_labels}),
                object_ids=[pid],
                gids=[prefix],
                detail={"count": count},
                worse=count,
            )

    # --- 数据语义：只给建议，绝不替用户裁决 ---
    for ax, roles in sorted(roles_by_axes.items()):
        if "bar_series" in roles and "errorbar" not in roles:
            sink.add(
                "bar-without-errorbar",
                "柱状图没有误差棒——如果这些柱子是多次测量的均值，规范期望标出误差",
                message=("barWithoutErrorbar", {}),
                object_ids=[pid],
                gids=[ax],
            )
        lines = lines_by_axes.get(ax) or []
        if (
            any(_FIT_WORDS.search(str(_field(ln, "label") or "")) for ln in lines)
            and "fill" not in roles
        ):
            sink.add(
                "fit-without-ci",
                "有拟合曲线但没有置信区间填充带——投稿时通常要求给出拟合的不确定度",
                message=("fitWithoutCi", {}),
                object_ids=[pid],
                gids=[ax],
            )
        if len(lines) >= 2 and all(
            str(_field(ln, "marker") or "None") in ("None", "none", "") for ln in lines
        ):
            sink.add(
                "palette-line-markers",
                f"{len(lines)} 条曲线全部没有 marker，黑白打印或色觉障碍读者难以区分，"
                "可考虑点线图或不同线型",
                message=("paletteLineMarkers", {"count": len(lines)}),
                object_ids=[pid],
                gids=[ax],
                detail={"lines": len(lines)},
                worse=len(lines),
            )


def _rects(spec: dict) -> list[tuple[str, list[float], bool, str]]:
    out = []
    for o in spec.get("objects") or []:
        rect = [float(v) for v in (o.get("rect_mm") or [0, 0, 0, 0])]
        out.append((o.get("id", ""), rect, bool(o.get("hidden")), o.get("type", "")))
    return out


def _check_geometry(spec: dict, sink: _Sink) -> None:
    page = spec.get("page") or {}
    pw = _num_or(page.get("w_mm"), 0.0)
    ph = _num_or(page.get("h_mm"), 0.0)
    margin = _num_or(page.get("margin_mm"), 0.0)
    items = _rects(spec)
    visible = [(i, r, t) for i, r, hidden, t in items if not hidden]

    out = [
        i
        for i, r, _t in visible
        if r[0] < -EPS_MM
        or r[1] < -EPS_MM
        or r[0] + r[2] > pw + EPS_MM
        or r[1] + r[3] > ph + EPS_MM
    ]
    if out:
        sink.add(
            "out-of-page",
            "对象超出页面范围，超出部分不会出现在成图里",
            message=("outOfPage", {}),
            object_ids=out,
        )
    if margin > 0:
        inside = [i for i, r, _t in visible if i not in set(out)]
        near = [
            i
            for i, r, _t in visible
            if i in set(inside)
            and (
                r[0] < margin - EPS_MM
                or r[1] < margin - EPS_MM
                or r[0] + r[2] > pw - margin + EPS_MM
                or r[1] + r[3] > ph - margin + EPS_MM
            )
        ]
        if near:
            sink.add(
                "outside-margin",
                f"对象越过了 {margin:g}mm 安全区页边距",
                message=("outsideMargin", {"margin": f"{margin:g}"}),
                object_ids=near,
            )

    panels = [(i, r) for i, r, t in visible if t == "panel"]
    hit: list[str] = []
    for a in range(len(panels)):
        for b in range(a + 1, len(panels)):
            (ia, ra), (ib, rb) = panels[a], panels[b]
            w = min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0])
            h = min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1])
            if w > 0 and h > 0 and w * h > 1:
                for i in (ia, ib):
                    if i not in hit:
                        hit.append(i)
    if hit:
        sink.add(
            "overlap",
            "面板互相重叠，确认是有意的压盖再导出",
            message=("overlap", {}),
            object_ids=hit,
        )

    hidden = [i for i, _r, is_hidden, _t in items if is_hidden]
    if hidden:
        sink.add("hidden", "隐藏的对象不会出现在导出中", message=("hidden", {}), object_ids=hidden)


def _check_texts(spec: dict, profile: dict, sink: _Sink) -> None:
    strict = _num_or(
        profile.get("min_effective_font_size_pt"), profiles_mod.FALLBACK_MIN_FONT_SIZE_PT
    )
    floor = _num_or(
        profile.get("absolute_min_font_size_pt"), profiles_mod.FALLBACK_MIN_FONT_SIZE_PT
    )
    cjk = profile.get("cjk_fallback") or {}
    for t in spec.get("texts") or []:
        if t.get("hidden"):
            continue
        tid = t.get("id", "")
        size = _num_or(t.get("size_pt"), 0.0)
        # 画布标注的 size_pt 已经是页面上的绝对 pt：不再乘 scale
        if size <= floor:
            sink.add(
                "font-below-absolute-floor",
                f"画布文字 {size:g}pt 不大于绝对下限 {floor:g}pt",
                message=("textBelowFloor", {"size": f"{size:g}", "floor": f"{floor:g}"}),
                object_ids=[tid],
                detail={"effective_pt": _r2(size), "floor_pt": floor},
                worse=-size,
            )
        elif size < strict:
            sink.add(
                "font-too-small",
                f"画布文字 {size:g}pt 低于规范下限 {strict:g}pt",
                message=("textTooSmall", {"size": f"{size:g}", "min": f"{strict:g}"}),
                object_ids=[tid],
                detail={"effective_pt": _r2(size), "min_pt": strict},
                worse=-size,
            )
        if has_cjk(t.get("text")) and cjk.get("required") and not cjk.get("accepted"):
            sink.add(
                "cjk-fallback-missing",
                "画布中文文字没有可用的中文字体 fallback",
                message=("textCjkFallbackMissing", {}),
                object_ids=[tid],
            )


def _check_missing_manifest(panel: dict, sink: _Sink) -> None:
    sink.add(
        "panel-text-not-verifiable",
        "矢量面板还没有引擎 manifest（未渲染 / 非脚本产物），图内文字字号与字体无法自动核验",
        message=("panelTextNotVerifiable", {}),
        object_ids=[panel.get("id", "")],
    )


def run(spec: dict, profile: dict) -> list[dict]:
    """跑一遍预检，返回问题清单（顺序即报告顺序）。"""
    sink = _Sink(profile)
    _check_page(spec, profile, sink)
    for panel in spec.get("panels") or []:
        if panel.get("hidden"):
            continue
        _check_panel_state(panel, sink)
        if panel.get("kind") == "raster":
            _check_panel_raster(panel, profile, sink)
        elif not isinstance(panel.get("manifest"), dict):
            _check_missing_manifest(panel, sink)
        else:
            _check_panel_fonts(panel, profile, sink)
            _check_panel_axes(panel, profile, sink)
    _check_texts(spec, profile, sink)
    _check_geometry(spec, sink)
    return sink.result()


def summarize(issues: list[dict]) -> dict:
    """按等级分组 + 计数。MCP 与导出对话框都用它做「能不能导出」的判据。"""
    buckets: dict[str, list[dict]] = {s: [] for s in profiles_mod.SEVERITIES}
    for issue in issues:
        buckets.setdefault(issue["severity"], []).append(issue)
    return {
        "errors": buckets["error"],
        "warnings": buckets["warn"],
        "not_verifiable": buckets["not_verifiable"],
        "suggestions": buckets["suggestion"],
        "counts": {k: len(v) for k, v in buckets.items()},
        "blocking": len(buckets["error"]) > 0,
    }


def spec_from_manifest(
    manifest: dict,
    *,
    panel_id: str = "figure",
    kind: str = "pdf",
    scale: float = 1.0,
    page: dict | None = None,
) -> dict:
    """单张 figure（MCP 路径）→ figure spec。页面就是 figure 自己的尺寸。"""
    size = manifest.get("size_mm") or [0.0, 0.0]
    w, h = float(size[0]) * scale, float(size[1]) * scale
    rect = [0.0, 0.0, w, h]
    return {
        "page": {"w_mm": round(w, 4), "h_mm": round(h, 4), "margin_mm": 0.0},
        "panels": [
            {
                "id": panel_id,
                "name": manifest.get("stem", panel_id),
                "kind": kind,
                "rect_mm": rect,
                "scale": scale,
                "manifest": manifest,
                "px_w": None,
                "missing": False,
                "stale": False,
                "render_error": None,
                "unapplied_overrides": 0,
                "bitmap_embed": False,
                "hidden": False,
            }
        ],
        "texts": [],
        "objects": [{"id": panel_id, "type": "panel", "rect_mm": rect, "hidden": False}],
    }
