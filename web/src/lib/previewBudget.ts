/**
 * 编辑预览的表示法模型与复杂度预算——**前端这一半**（ADR 0022）。
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

/**
 * 这串文本的 UTF-8 字节数——**只在需要时才真的算**。
 *
 * 三段判据，`limit` 两侧各有一段是零成本的：
 *
 * * `length > limit` ⇒ 字节数一定 > limit（UTF-8 每个码元至少 1 字节）；
 * * `length <= limit / 3` ⇒ 字节数一定 <= limit（BMP 内每码元最多 3 字节；
 *   代理对是 2 码元 4 字节 = 2 字节/码元，所以 3 是安全上界）；
 * * 只有中间那一小段才 `TextEncoder` 编一次。
 *
 * 中间段最坏是一次 16 MiB 的编码——而走到那儿说明**后端那道闸已经漏了**
 * （正常路径上后端根本不会把超限的 SVG 交出来）。在异常路径上付这一次，
 * 换的是「126 MB 不进 DOM」。
 */
export function svgByteLength(text: string, limit: number): number {
  if (text.length > limit) return text.length // 下界已经超了，不必精确
  if (text.length * 3 <= limit) return text.length // 上界都没到，也不必
  return new TextEncoder().encode(text).length
}

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
 * 判据吃的是 **UTF-8 字节数**，与后端那道闸量的是同一个东西
 * （`stat().st_size`）。`svg.length` 是 UTF-16 码元数，拿它直接比是**低估**
 * ——一个 BMP 内的非 ASCII 字符占 1 个码元、却占 3 个 UTF-8 字节。560 万个
 * 中文字符的 SVG（图内中文标签的科研图，正是 Tavotto 的主场）`length` 只有
 * 五百多万、稳稳低于 16 MiB 阈值，实际 payload 却是 16.8 MiB——整份漏过去。
 *
 * 但也不能无脑 `TextEncoder().encode()`：为了量一个「太大了」的东西再复制它
 * 一遍，正是这道闸要防的那件事。所以分三段（`svgByteLength`）：两头零成本，
 * 只有**临近阈值**的那一小段才真的编码一次。
 */
export function resolvePreview(res: {
  svg?: string | null
  preview?: PreviewMetadata | null
}): { svg: string | null; preview: PreviewMetadata } {
  const declared = res.preview ?? VECTOR_PREVIEW
  const svg = res.svg ?? null
  if (svg != null) {
    const bytes = svgByteLength(svg, EDITOR_SVG_HARD_LIMIT_BYTES)
    if (bytes > EDITOR_SVG_HARD_LIMIT_BYTES) {
      return {
        svg: null,
        preview: {
          ...declared,
          mode: 'raster',
          reason: 'fallback',
          svg_bytes: declared.svg_bytes || bytes,
        },
      }
    }
  }
  // 后端说 raster 却仍然给了 svg：以**后端的裁决**为准把 svg 丢掉。
  // 这条不是防御性冗余——降档的理由可能是复杂度而不是体积（Session 02 的
  // 分析器），那时 svg 完全可能小于硬闸，而它照样不该进 DOM。
  if (declared.mode === 'raster') return { svg: null, preview: declared }
  return { svg, preview: declared }
}
