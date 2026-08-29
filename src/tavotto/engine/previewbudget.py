"""编辑预览的表示法模型与复杂度预算——**唯一出处**（ADR 0022）。

## 这个模块回答一个问题

> 这一版预览该用什么画法交给前端：矢量、混合、还是位图？

答案在协议里是一等公民（`PreviewMode`），因为它决定的是**画布上挂的是
`<svg>` 还是 `<img>`**，而不是某个内部实现细节。

## 为什么阈值判定必须在这里，而不是散在调用点

issue #181 的教训是「**判定点在 `read_text()` 之后 = 这道闸什么都没挡住**」：
实测一张 126 MB 的预览 SVG，光是「读回来 → 解析 JSON → 再编码 JSON」这三步
就让 Flask 进程峰值 RSS 到 **1 245 MB**，而此时它一个字节都还没到浏览器。
所以判据吃的是 `os.stat().st_size`，不是已经读进内存的字符串——把它收在一个
模块里，是为了让「谁在什么时候判的」只有一个答案。

## 前端有一份镜像

`web/src/lib/previewBudget.ts`。那不是第二份权威，是**旧后端 / 异常后端的
二道闸**（后端不返回 `preview` 时前端要维持既有行为，而返回了一个超大 `svg`
时前端要能自己丢掉它）。两侧的数字由 `tests/test_preview_budget.py` 看住。

**复杂度预算那几个数没有镜像，这是有意的**：前端从不评估复杂度（artist 图只
在 worker 进程里），它手上只有裁决结果。凭空镜像过去就是造第二份权威。
`test_complexity_budgets_are_deliberately_not_mirrored` 是这个不对称的说明。

纯标准库；`figsession` 平铺 import 它，`tavotto.engine` 侧按包名 import。
"""

from __future__ import annotations

__all__ = [
    "COLLECTION_VERTEX_BUDGET",
    "EDITOR_SVG_HARD_LIMIT_BYTES",
    "EDITOR_SVG_SOFT_LIMIT_BYTES",
    "MESH_CELL_BUDGET",
    "MODES",
    "MODE_HYBRID",
    "MODE_RASTER",
    "MODE_VECTOR",
    "RASTER_PREVIEW_WIDTH_PX",
    "REASONS",
    "REASON_COMPLEXITY_BUDGET",
    "REASON_FALLBACK",
    "REASON_NORMAL",
    "REASON_SVG_HARD_LIMIT",
    "SCATTER_INSTANCE_BUDGET",
    "TOTAL_VECTOR_NODE_BUDGET",
    "TOTAL_VECTOR_PRIMITIVE_BUDGET",
    "metadata",
    "resolve_mode",
    "wants_hybrid_escalation",
]

#: 普通科研图：行为与 #181 之前完全一致（内联 SVG，所见即所得）。
MODE_VECTOR = "vector"
#: 大型 mesh / collection 层临时 rasterize，文字 / 坐标轴 / 图例 / 标注 /
#: 普通曲线保持矢量。名单由 `preview_complexity` 出，设/还原由
#: `preview_hybrid` 做——**只在 `savefig` 那一瞬**（ADR 0022 不变量 2）。
MODE_HYBRID = "hybrid"
#: 最后的安全降级：显示位图。**不是只读**——命中层与 exact manifest 照常在
#: （ADR 0022 不变量 4）。
MODE_RASTER = "raster"

MODES = (MODE_VECTOR, MODE_HYBRID, MODE_RASTER)

#: 没触发任何预算。
REASON_NORMAL = "normal"
#: 复杂度估算超预算（Session 02 的分析器产出）。
REASON_COMPLEXITY_BUDGET = "complexity_budget"
#: SVG 字节数超硬闸——**SVG 根本没被读**。
REASON_SVG_HARD_LIMIT = "svg_hard_limit"
#: 前端自己的二道闸（旧后端 / 异常后端给了一份超大 svg）。
REASON_FALLBACK = "fallback"

REASONS = (REASON_NORMAL, REASON_COMPLEXITY_BUDGET, REASON_SVG_HARD_LIMIT, REASON_FALLBACK)

#: **hybrid 的第二把尺子**（Session 03 起真的生效）：`savefig` 出来的字节数
#: 越过它、而可 rasterize 的数据层还没收满 ⇒ 全部收进来重画一遍
#: （`preview_hybrid.save_preview_svg`）。它量的是**产物**，复杂度那几个预算
#: 量的是**原料**——两把尺子独立，估算漏了的时候只有这一把看得见。
#:
#: 选 8 MiB 的理由在数据里（docs/perf-baseline.md）：普通科研图的预览 SVG 是
#: 几十到几百 KB，含 imshow 的面板最坏 827 KB——比这条线低一个数量级以上，
#: 正常图一张都碰不到，因此那第二遍 `savefig` 正常图一次都不会付。
EDITOR_SVG_SOFT_LIMIT_BYTES = 8 * 1024 * 1024

#: **raster 的硬闸**：超过这个数就绝不 `read_text()`。#181 那张 126 MB 的图
#: 比它高 7.8 倍，阈值怎么微调都在闸外。
EDITOR_SVG_HARD_LIMIT_BYTES = 16 * 1024 * 1024

#: raster 档下位图预览的目标像素宽。**受控尺寸**是这条路径的前提——
#: MCP 那侧要把它 base64 塞进 JSON-RPC 响应里（ADR 0022 §6：绝不把 giant SVG
#: 转成 base64 塞回去，raster 走的是另一样东西）。7 英寸宽的图上 1200px
#: ≈ 171 dpi，屏幕上够看，payload 是 MB 级而不是百 MB 级。
RASTER_PREVIEW_WIDTH_PX = 1200


# --------------------------- 复杂度预算（Session 02） -------------------------
# 上面两个数量的是**产物**（SVG 有多少字节），只有 `savefig` 跑完才知道——而
# #181 里那一步就是 11 789 ms 本身。下面这几个量的是**原料**（artist 图上有多少
# primitive），在 `savefig` 之前就答得出来。判据实现在
# `engine/preview_complexity.py`，阈值只在这里。
#
# 四个数是一组，选值的锚点是「换算回字节之后，它们要落在上面那两条闸的同一
# 量级、且略早一点触发」——早一点是**故意**的：字节闸要先付 12 秒才知道答案，
# 复杂度闸不必付。换算按 #181 实测的 126 132 735 字节 / 662 773 个 `<path>`
# ≈ **190 字节一个 primitive**。

#: 单块网格（`pcolormesh` / `pcolor` / `hist2d` / heatmap）的 cell 数上限。
#: 20 000 个 cell ≈ 3.6 MB SVG，在 8 MiB 软闸之下——普通科研图的网格是几千个
#: cell（Phase E 基线里含 imshow 的面板最坏 827 KB），一张都碰不到；而 #181
#: 那张是每格 220 900 个，高出 11 倍。
MESH_CELL_BUDGET = 20_000

#: 单个散点系列的实例数上限。比网格宽一倍有实测理由：scatter 的几何进
#: `<defs>` 只出现一次，每个点在 SVG 里只是一个约 60 字节的 `<use>`，而一个
#: mesh cell 是一个自带四组坐标的 `<path>`。50 000 个 `<use>` ≈ 3 MB，同一个
#: 量级。
SCATTER_INSTANCE_BUDGET = 50_000

#: 单个 collection 的顶点数上限——**给 contour 那一族用的**。等值线只有几十个
#: `<path>`（一层一个），节点数判据整族看不见它，可 300×300 网格 40 层实测
#: 232 679 个顶点、约 3 MB 文本。10 万个顶点 ≈ 1.4 MB，留出余量。
COLLECTION_VERTEX_BUDGET = 100_000

#: 整张图**留在矢量层**的 primitive 总数上限。逐族预算管不到「二十个各 4 万
#: cell 的面板」——每个都合规，合起来 80 万个节点照样把 DOM 打死。
#: 50 000 × 190 字节 ≈ 9.5 MB，正好落在 8 MiB 软闸旁边：两条闸说的是同一件事，
#: 一条在原料侧、一条在产物侧。
TOTAL_VECTOR_PRIMITIVE_BUDGET = 50_000

#: 整张图**留在矢量层**的 SVG 元素（≈ DOM 节点）总数上限。
#:
#: **它与上面那条量的是两个维度，不是同一条的两种写法。**
#: `TOTAL_VECTOR_PRIMITIVE_BUDGET` 的校准锚点是**字节**（50 000 × 190 B ≈ 9.5 MB，
#: 落在 8 MiB 软闸旁边）；这一条的锚点是**浏览器**。两者在 mesh 上重合
#: （一个 cell = 一个 `<path>`，1:1），在 `Line2D` 上分开——一条曲线是一个
#: `<path>` **外加一个 `<g>`**，实测 2.01 个元素/条（4 万条线 40 031 个 path、
#: 40 234 个 g）。40 000 条 `plot()` 因此只记 40 000 个 primitive、在字节侧
#: 只有 9.33 MB（软闸之上、硬闸之下），**三条闸一条都不响**，却往 DOM 里挂了
#: 201 977 个节点、330–360 MB。这条预算就是为那个缺口加的。
#:
#: 选 24 000 的依据是实测的挂载耗时（`docs/perf-baseline.md`「浏览器侧」一节）：
#:
#:     SVG 元素   DOM 节点    单面板 RSS    一次挂载
#:      20 470     51 650     147–155 MB     103 ms
#:      40 501    101 765     209–223 MB     205 ms
#:      80 501    201 977     333–362 MB     410 ms
#:
#: DOM 节点 ≈ 2.53 × SVG 元素（实测，线性）。24 000 个元素 ≈ 6 万个节点 ≈
#: 一次 120 ms 的挂载——**100 ms 是「瞬时」的人机边界**，再往上用户就看得见
#: 卡顿了。普通科研图的预览 SVG 是几百到几千个元素（#181 修好之后那张是
#: **818** 个），低两个数量级，一张都碰不到。
TOTAL_VECTOR_NODE_BUDGET = 24_000


def wants_hybrid_escalation(svg_bytes: int) -> bool:
    """产物越过软闸了吗——**「要不要再画一遍」，不是「这一版叫什么」**。

    软闸量的是 `savefig` 出来的字节数，复杂度预算量的是 artist 图上的
    primitive 数。两把尺子**相互独立**，这正是软闸留着的理由：复杂度模型估
    低了的时候，只有产物那一侧看得见。越过它且名单还没收满 ⇒ 把剩下的可
    rasterize 数据层一并收进来重画一次（策略在
    `engine/preview_hybrid.save_preview_svg`，那里也钉住了「最多第二遍」）。

    硬闸不在这里：它的答案是 `MODE_RASTER`，见 `resolve_mode`。
    """
    return svg_bytes >= EDITOR_SVG_SOFT_LIMIT_BYTES


def resolve_mode(
    *, svg_bytes: int, rasterized_artist_count: int = 0, plan_demands_raster: bool = False
) -> tuple[str, str]:
    """一次预览的最终裁决：`(mode, reason)`。**两个时刻合成一个答案。**

    * `rasterized_artist_count` 来自 `savefig` **之前**那一刻（分析器的名单）；
    * `svg_bytes` 来自 `savefig` **之后**那一刻，且**只吃字节数**
      （`os.stat().st_size` / 缓冲区的 `tell()`），刻意不吃 SVG 内容——判定必须
      能在 `read_text()` 之前做完，见模块头。

    `plan_demands_raster` 同样来自 `savefig` **之前**那一刻：分析器算出「收完
    之后矢量层还剩多少个 SVG 元素」，超 `TOTAL_VECTOR_NODE_BUDGET` 就要求降档。
    **它与字节无关**——4 万条 `plot()` 只有 9.33 MB，字节那两条闸一条都不响。

    顺序不能反：硬闸先答。一版 hybrid 产物**仍然可能**超过硬闸（收不动的层
    太多、或者矢量层本身就巨大），那时它照样不许被读进内存——不变量 3 不因为
    「我们已经尽力了」而放松。
    """
    if svg_bytes >= EDITOR_SVG_HARD_LIMIT_BYTES:
        return MODE_RASTER, REASON_SVG_HARD_LIMIT
    if plan_demands_raster:
        # 分析器说「收完之后矢量层仍然超 `TOTAL_VECTOR_NODE_BUDGET`」。
        # **这一档的产物完全可能很小**（4 万条线只有 9.33 MB，字节闸不会响）
        # ——它拦的是节点数，与字节无关。
        return MODE_RASTER, REASON_COMPLEXITY_BUDGET
    if rasterized_artist_count > 0:
        return MODE_HYBRID, REASON_COMPLEXITY_BUDGET
    return MODE_VECTOR, REASON_NORMAL


def metadata(
    *,
    svg_bytes: int,
    mode: str,
    reason: str,
    rasterized_artist_count: int = 0,
    estimated_primitives: int | None = None,
    estimated_vertices: int | None = None,
    estimated_nodes: int | None = None,
) -> dict:
    """一次渲染的 preview 元数据（响应里的 `preview` 字段）。

    形状是 additive 的（ADR 0003 §1）：老前端看不见它，新前端在老后端上
    收不到它——两边都必须照旧工作。可选的两个估算字段**没有就不出现**，
    别用 0 冒充「没估过」。
    """
    if mode not in MODES:
        raise ValueError(f"未知的 preview mode: {mode!r}")
    if reason not in REASONS:
        raise ValueError(f"未知的 preview reason: {reason!r}")
    out = {
        "mode": mode,
        "reason": reason,
        "svg_bytes": int(svg_bytes),
        "rasterized_artist_count": int(rasterized_artist_count),
    }
    if estimated_primitives is not None:
        out["estimated_primitives"] = int(estimated_primitives)
    if estimated_vertices is not None:
        out["estimated_vertices"] = int(estimated_vertices)
    if estimated_nodes is not None:
        out["estimated_nodes"] = int(estimated_nodes)
    return out
