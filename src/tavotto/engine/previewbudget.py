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

纯标准库；`figsession` 平铺 import 它，`tavotto.engine` 侧按包名 import。
"""

from __future__ import annotations

__all__ = [
    "EDITOR_SVG_HARD_LIMIT_BYTES",
    "EDITOR_SVG_SOFT_LIMIT_BYTES",
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
    "metadata",
    "mode_for_svg_bytes",
]

#: 普通科研图：行为与 #181 之前完全一致（内联 SVG，所见即所得）。
MODE_VECTOR = "vector"
#: 大型 mesh / collection 层临时 rasterize，文字 / 坐标轴 / 图例 / 标注 /
#: 普通曲线保持矢量。**Session 03 才产出**，这里先进类型与协议。
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

#: **hybrid 的触发线**。Session 03 之前它不改变任何行为——8～16 MiB 的图照旧
#: 内联 SVG。这个缺口是刻意留着的，`tests/test_preview_budget.py` 有一条用例
#: 钉住「今天软闸区间仍然透传」：hybrid 一落地它就红，逼着一起改。
#:
#: 选 8 MiB 的理由在数据里（docs/perf-baseline.md）：普通科研图的预览 SVG 是
#: 几十到几百 KB，含 imshow 的面板最坏 827 KB——比这条线低一个数量级以上，
#: 正常图一张都碰不到。
EDITOR_SVG_SOFT_LIMIT_BYTES = 8 * 1024 * 1024

#: **raster 的硬闸**：超过这个数就绝不 `read_text()`。#181 那张 126 MB 的图
#: 比它高 7.8 倍，阈值怎么微调都在闸外。
EDITOR_SVG_HARD_LIMIT_BYTES = 16 * 1024 * 1024

#: raster 档下位图预览的目标像素宽。**受控尺寸**是这条路径的前提——
#: MCP 那侧要把它 base64 塞进 JSON-RPC 响应里（ADR 0022 §6：绝不把 giant SVG
#: 转成 base64 塞回去，raster 走的是另一样东西）。7 英寸宽的图上 1200px
#: ≈ 171 dpi，屏幕上够看，payload 是 MB 级而不是百 MB 级。
RASTER_PREVIEW_WIDTH_PX = 1200


def mode_for_svg_bytes(svg_bytes: int) -> tuple[str, str]:
    """光看预览 SVG 的体积能得出的裁决：`(mode, reason)`。

    **只吃字节数**（`os.stat().st_size`），刻意不吃 SVG 内容——判定必须能在
    读取之前做完，见模块头。复杂度维度（primitive / vertex 估算）是 Session 02
    的事，那时这个函数会多一个入参，而不是多一个第二判定点。
    """
    if svg_bytes >= EDITOR_SVG_HARD_LIMIT_BYTES:
        return MODE_RASTER, REASON_SVG_HARD_LIMIT
    # 软闸区间（soft ≤ x < hard）今天仍然是 vector：hybrid 还没有实现，
    # 而「报一个我们其实做不到的 mode」比什么都不报更坏——前端会照着它
    # 去等一份永远不来的混合产物。
    return MODE_VECTOR, REASON_NORMAL


def metadata(
    *,
    svg_bytes: int,
    mode: str,
    reason: str,
    rasterized_artist_count: int = 0,
    estimated_primitives: int | None = None,
    estimated_vertices: int | None = None,
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
    return out
