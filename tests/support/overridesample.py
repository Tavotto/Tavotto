"""在 manifest 上采样一条「不同的值」、按使能项组 patch、枚举可编辑目标——
`tests/test_invariants_engine.py`（不变式 1 / 2）与 `tests/test_override_sequences.py`
（§4.2 操作序列）共用的一份采样器。2026-09-18 从前者逐字搬出：两套用例发的必须是
同一批 patch，分开写过一版，代价立刻显形（见 `_patch_for` 的抬头）。
"""

from __future__ import annotations


def _fields(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里"
    return {f["prop"]: f for f in hits[0]["editable"]}


def _sample_value(field):
    """按字段类型挑一个**不同**的值；挑不出来（结构化类型）返回 None。"""
    kind, cur = field.get("type"), field.get("value")
    if kind == "color":
        return "#123456" if str(cur).lower() != "#123456" else "#654321"
    if kind == "bool":
        return not bool(cur)
    if kind == "enum":
        opts = [o for o in (field.get("options") or []) if o != cur]
        return opts[0] if opts else None
    if kind == "text":
        return f"{cur}x" if cur is not None else "x"
    if kind == "number":
        if cur is None:
            return None
        lo, hi = field.get("min"), field.get("max")
        step = float(field.get("step") or 1.0)
        # **一个 step 往往看不出来**：`vmin` 的 step 是值域的 1%，挪 1% 之后
        # 256 级色图上多半还是同一个颜色，于是「改了没反应」被误判成假支持。
        # 挑一个明显的差（有界的按值域 1/10，无界的按 5 个 step），越界再退回
        # 一个 step——这是**采样**的取舍，不是断言的松动。
        delta = (
            (float(hi) - float(lo)) / 10.0 if (lo is not None and hi is not None) else step * 5.0
        )
        delta = max(abs(delta), abs(step))
        for cand in (float(cur) + delta, float(cur) - delta, float(cur) + step, float(cur) - step):
            if (lo is None or cand >= float(lo)) and (hi is None or cand <= float(hi)):
                return round(cand, 4)
        return None
    return None  # pair / rect / order / number_list：各有各的契约


def _same_value(field, got, want) -> bool:
    if field.get("type") == "number":
        tol = max(abs(float(field.get("step") or 1.0)) / 2.0, 1e-6)
        return got is not None and abs(float(got) - float(want)) <= tol
    if field.get("type") == "color":
        return str(got).lower() == str(want).lower()
    return got == want


def _patch_for(gid, field, advertised):
    """(使能项 patches, 完整 patch 列表) —— **能力真实与逐字还原共用同一份**。

    两条扫描必须发同一组 patch。分开写过一版，代价立刻显形：还原那条只发
    单条 prop，于是「先把背景框打开、再改背景色」这个组合永远轮不到它，而
    真正的还原缺陷（第一条 bbox_* 现建出来的框摘不掉）就藏在那个组合里——
    **另一道防线恰好挡住了它**，两条用例都绿。
    """
    prop = field["prop"]
    value = _SAMPLE_OVERRIDE.get(prop, _sample_value(field))
    if value is None:
        return None, None
    enablers = [
        {"gid": gid, "prop": ep, "value": ev}
        for ep, ev in _ENABLERS.get(prop, ())
        if ep != prop and ep in advertised[gid]
    ]
    return enablers, enablers + [{"gid": gid, "prop": prop, "value": value}]


def _editable_targets(man):
    """(gid, field) —— 跳过整块结构性的角色，它们各有各的专用用例。"""
    for el in man["elements"]:
        if el["role"] in ("figure", "axes", "axes3d", "ticks", "ticklabel"):
            continue
        for f in el["editable"]:
            yield el["gid"], f


# ---------------------------------------------------------------------------
# 不变式 1：能力真实（capability truthfulness）
# ---------------------------------------------------------------------------
#: **画面上看不出来是正常的**那些 prop —— 这张表必须显式、必须写清理由。
#:
#: 判据是「在这张图上，改它不动像素**是对的**」，不是「它改不动所以放过」。
#: 往里加一条之前先问：是这个 prop 天生不影响绘制，还是这张图恰好挡住了？
#: 后者要改图，不是加豁免。**没登记的一律按「必须动像素」处理**——忘了登记
#: 会让一个假支持悄悄溜过去，而那正是这条不变式要挡的东西。
_NON_VISUAL_PROPS = {
    "label": "只在图例里显形；元素自己的图上不画（图例项文字是另一个元素）",
    "zorder": "只改绘制次序；被它盖住/露出的是别的元素，本元素的像素可以不变",
    # 绑定是**状态**不是样式：follow → custom 是「从此不再跟随」，脱开那一刻
    # 示意线就是它此刻的样子（ADR 0034）；像素要等源对象下一次变才会分岔。
    # 反方向（custom → follow）会动像素，`test_legend_binding.py` 钉住它。
    "binding": "图例项脱开跟随的那一刻不改示意线；分岔发生在源对象下一次变化时",
}

#: **使能项**：这些 prop 要另一条 prop 开着才看得见，缺了它就是「改了没反应」。
#:
#: 这不是豁免——豁免是「它本来就不画」，使能是「它画在一个当前关着的通道上」。
#: 两者的区别在于**能不能被验**：加上使能项之后画面必须变，验不出来照样红。
#: 这也正是 matplotlib 自己的模型：`facecolor` 只在 `fill` 开着时显形，
#: 花纹画在边色上，`markersize` 要先有 marker——`Arc` 那条被误判成「画不出面」
#: 的旧结论，本质就是把「使能项没开」看成了「能力不存在」。
#:
#: 值是「(同一个元素上的 prop, 值)」，**只有该元素也宣称了那条 prop 时才附加**。
_ENABLERS: dict[str, tuple[tuple[str, object], ...]] = {
    # bbox 要**同时**有边或面才看得见：合成默认值是 lw=0 + 白底白面
    "bbox_facecolor": (("bbox_visible", True),),
    "bbox_edgecolor": (("bbox_visible", True), ("bbox_linewidth", 2.0)),
    "bbox_linewidth": (("bbox_visible", True), ("bbox_edgecolor", "#ff00ff")),
    "bbox_alpha": (("bbox_visible", True), ("bbox_facecolor", "#ff00ff")),
    "bbox_pad": (("bbox_visible", True), ("bbox_facecolor", "#ff00ff")),
    "bbox_rounded": (("bbox_visible", True), ("bbox_facecolor", "#ff00ff")),
    # `bbox_visible` 是这一组的**开关**（#412 之后现建的框不可见，样式不改显隐）：
    # 采样值钉成 True（`_SAMPLE_OVERRIDE`），使能项给它一个看得见的底色——合成
    # 默认值是 lw=0 + 白底，白纸上开了也量不到像素。
    "bbox_visible": (("bbox_facecolor", "#ff00ff"),),
    "stroke_color": (("stroke_enabled", True), ("stroke_width", 2.0)),
    "stroke_width": (("stroke_enabled", True), ("stroke_color", "#ff00ff")),
    # 花纹画在**边色**上；线宽 / 线型同样要先有一条看得见的边
    "hatch": (("edgecolor", "#ff00ff"), ("linewidth", 1.0)),
    "linewidth": (("edgecolor", "#ff00ff"),),
    "linestyle": (("edgecolor", "#ff00ff"), ("linewidth", 1.5)),
    "facecolor": (("fill", True),),
    "markersize": (("marker", "o"),),
    "handle_markersize": (("handle_marker", "o"),),
    # 列间距只在多列时有地方可摆——单列图例上它是死的（界面也只在 ncol>1 时给）
    "columnspacing": (("ncol", 2),),
    "markerfacecolor": (("marker", "o"), ("markersize", 8.0)),
    "markeredgecolor": (("marker", "o"), ("markersize", 8.0)),
    "title_fontsize": (("title", "T"),),
    "linespacing": (("text", "line one\nline two"),),
    "ha": (("text", "line one\nline two"),),
    "va": (("text", "line one\nline two"),),
}

#: 按 **(role, prop)** 划的豁免：同一条 prop 在不同容器里未必都画得出来。
#: `ha` / `va` 改的是文字相对**自身锚点**的对齐——标题、轴标签的锚点是自己的
#: 位置，改它文字就移动；而图例项的锚点由 HPacker 的布局定死，对齐改了也
#: 挪不动（实测 `axes_0.title.ha` 变、`legend.texts_j.ha` 不变）。这类差别
#: 只能按角色写，写成全局豁免就等于把标题那半也放过了。
#: 采样值不按 `_sample_value` 推、而是钉死的那几条（配合使能项才有意义）：
#: `bbox_visible` 是开关，量它得把它**打开**（脚本里没有框的文字上 False 是 no-op）。
_SAMPLE_OVERRIDE = {"bbox_visible": True}

_NON_VISUAL_BY_ROLE = {
    ("legend_text", "ha"): "图例项的位置由 HPacker 布局定，对齐挪不动它",
    ("legend_text", "va"): "同上",
}
