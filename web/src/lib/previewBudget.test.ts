/**
 * 前端这道**二道闸**（ADR 0022）。
 *
 * 它不是第二份权威——后端的 pre-read 闸才是真正的第一道保护（那一侧连读都
 * 不读）。这里拦的是「字节已经进了浏览器进程」之后的那一步：126 MB 的字符串
 * 躺在 JS 堆里是一回事，展开成 66 万个 DOM 节点是另一回事。
 *
 * 判据只有一处（`resolvePreview`），所以只在这里测。
 */
import { describe, expect, it } from 'vitest'
import {
  EDITOR_SVG_HARD_LIMIT_BYTES,
  resolvePreview,
  VECTOR_PREVIEW,
  type PreviewMetadata,
} from './previewBudget'

const raster: PreviewMetadata = {
  mode: 'raster',
  reason: 'svg_hard_limit',
  svg_bytes: 126_132_735,
  rasterized_artist_count: 0,
}

describe('resolvePreview', () => {
  it('老后端不返回 preview：按 vector 解读，svg 原样透传', () => {
    const out = resolvePreview({ svg: '<svg/>' })
    expect(out.svg).toBe('<svg/>')
    expect(out.preview).toEqual(VECTOR_PREVIEW)
  })

  it('后端说 raster 且没给 svg：原样传达', () => {
    const out = resolvePreview({ preview: raster })
    expect(out.svg).toBeNull()
    expect(out.preview).toBe(raster)
  })

  it('后端说 raster 却仍然给了 svg：以后端的裁决为准丢掉它', () => {
    // 这条不是防御性冗余：降档的理由可能是**复杂度**而不是体积（Session 02
    // 的分析器），那时 svg 完全可能小于硬闸，而它照样不该进 DOM。
    const out = resolvePreview({ svg: '<svg/>', preview: raster })
    expect(out.svg).toBeNull()
  })

  it('后端说 vector 却给了一份超大 svg：前端自己丢掉，并说清是自己丢的', () => {
    const huge = 'x'.repeat(EDITOR_SVG_HARD_LIMIT_BYTES + 1)
    const out = resolvePreview({ svg: huge })
    expect(out.svg).toBeNull()
    expect(out.preview.mode).toBe('raster')
    // `fallback` 而不是 `svg_hard_limit`：**是谁拦的**要说得出口，否则排障时
    // 会以为后端那道闸生效了，而它其实漏了
    expect(out.preview.reason).toBe('fallback')
    expect(out.preview.svg_bytes).toBe(huge.length)
  })

  it('闸内的大图照旧透传（阈值是 >，不是 >=）', () => {
    const big = 'x'.repeat(EDITOR_SVG_HARD_LIMIT_BYTES)
    expect(resolvePreview({ svg: big }).svg).toBe(big)
  })
})
