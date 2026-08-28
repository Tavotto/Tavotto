/**
 * 编辑预览的表示法模型与复杂度预算——**前端这一半**（ADR 0021）。
 *
 * 权威在 `src/tavotto/engine/previewbudget.py`：用哪一档由引擎裁决，前端只
 * 消费。这里存在的理由只有一条：**二道闸**。
 *
 *   * 老后端不返回 `preview` → 前端必须维持今天的行为（内联 SVG），不能因为
 *     字段缺失就把画布刷成空白；
 *   * 后端异常地返回了一份超大 `svg`（老 worker、被人绕过的路径、将来某次
 *     回归）→ 前端**当场丢掉这份 payload**，绝不 `prepareSvg` + 存进 store +
 *     `dangerouslySetInnerHTML`。
 *
 * 后端的 pre-read 闸才是真正的第一道保护（那一侧连读都不读，见 issue #181
 * 的 1.2 GB 峰值 RSS）。这里拦的是「字节已经到了浏览器进程里」之后的那一步
 * ——它救不回内存，但能救回**渲染进程**：126 MB 的字符串在 JS 堆里放着是一回
 * 事，展开成 66 万个 DOM 节点是另一回事。
 *
 * 两侧的数字由 `tests/test_preview_budget.py` 逐个比对。
 */

export type PreviewMode = 'vector' | 'hybrid' | 'raster'

export type PreviewReason = 'normal' | 'complexity_budget' | 'svg_hard_limit' | 'fallback'

/** hybrid 的触发线（Session 03 落地前不改变任何行为）。 */
export const EDITOR_SVG_SOFT_LIMIT_BYTES = 8 * 1024 * 1024

/** raster 的硬闸。后端超过它就不读；前端超过它就不用。 */
export const EDITOR_SVG_HARD_LIMIT_BYTES = 16 * 1024 * 1024

/**
 * 这一版预览的元数据。**加字段协议**：老后端不返回它，字段整个不存在。
 */
export interface PreviewMetadata {
  mode: PreviewMode
  reason: PreviewReason
  svg_bytes: number
  estimated_primitives?: number
  estimated_vertices?: number
  rasterized_artist_count: number
}

/** 后端没给 `preview` 时的默认解读：就是今天的行为。 */
export const VECTOR_PREVIEW: PreviewMetadata = {
  mode: 'vector',
  reason: 'normal',
  svg_bytes: 0,
  rasterized_artist_count: 0,
}

/**
 * 一次渲染响应 → 「这一版该怎么显示」。
 *
 * 返回的 `svg` 是**允许进 DOM 的那一份**：二道闸拦下来时它是 `null`，
 * 且 `preview.mode` 被改写成 `raster` / `reason: 'fallback'`——调用方不必
 * 自己再判一次「这份 svg 是不是太大」，判据只有这一处。
 *
 * `svg.length` 是 UTF-16 码元数，不是字节数；对这道闸来说**够用且更保守**
 * （非 ASCII 字符的字节数只多不少），而真要精确到字节就得再全量编码一遍
 * ——为了量一个「太大了」的东西再复制它一遍，正是这道闸要防的那件事。
 */
export function resolvePreview(res: {
  svg?: string | null
  preview?: PreviewMetadata | null
}): { svg: string | null; preview: PreviewMetadata } {
  const declared = res.preview ?? VECTOR_PREVIEW
  const svg = res.svg ?? null
  if (svg != null && svg.length > EDITOR_SVG_HARD_LIMIT_BYTES) {
    return {
      svg: null,
      preview: {
        ...declared,
        mode: 'raster',
        reason: 'fallback',
        svg_bytes: declared.svg_bytes || svg.length,
      },
    }
  }
  // 后端说 raster 却仍然给了 svg：以**后端的裁决**为准把 svg 丢掉。
  // 这条不是防御性冗余——降档的理由可能是复杂度而不是体积（Session 02 的
  // 分析器），那时 svg 完全可能小于硬闸，而它照样不该进 DOM。
  if (declared.mode === 'raster') return { svg: null, preview: declared }
  return { svg, preview: declared }
}
