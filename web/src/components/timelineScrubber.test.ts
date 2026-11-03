import { describe, expect, it } from 'vitest'
import { contentBox, unionBox } from '@/lib/versionThumbBox'
import { restCenters } from './TimelineScrubber'

const page = { w: 150, h: 100 }

describe('浮卡缩略图对准页面上有内容的那块（contentBox）', () => {
  it('图只占页面右上一角（beta 0.19.3b2 反馈）：裁到那一块并留一点白', () => {
    const box = contentBox(page, [{ x: 77.7, y: 23.6, w: 74, h: 38.8 }])
    expect(box).not.toBeNull()
    // 伸出页面右边的部分不算：缩略图只拍到页面为止
    expect(box!.x + box!.w).toBeLessThanOrEqual(150)
    expect(box!.x).toBeLessThan(77.7)
    expect(box!.x).toBeGreaterThan(70)
    expect(box!.y).toBeLessThan(23.6)
    expect(box!.y + box!.h).toBeGreaterThan(23.6 + 38.8)
  })

  it('内容差不多占满页面：不裁', () => {
    expect(contentBox(page, [{ x: 2, y: 2, w: 146, h: 96 }])).toBeNull()
  })

  it('没有对象、只有隐藏对象、对象全在页面外：不裁', () => {
    expect(contentBox(page, [])).toBeNull()
    expect(contentBox(page, [{ x: 10, y: 10, w: 20, h: 20, hidden: true }])).toBeNull()
    expect(contentBox(page, [{ x: 200, y: 10, w: 20, h: 20 }])).toBeNull()
  })

  it('多个对象取并集', () => {
    const box = contentBox(page, [
      { x: 10, y: 10, w: 20, h: 10 },
      { x: 60, y: 40, w: 20, h: 10 },
    ])
    expect(box!.x).toBeLessThan(10)
    expect(box!.x + box!.w).toBeGreaterThan(80)
    expect(box!.y + box!.h).toBeGreaterThan(50)
  })
})

describe('对比卡片两格共用的框（unionBox）', () => {
  it('取并集；任一边不裁就都不裁', () => {
    expect(unionBox({ x: 10, y: 10, w: 20, h: 20 }, { x: 40, y: 5, w: 10, h: 10 })).toEqual({ x: 10, y: 5, w: 40, h: 25 })
    expect(unionBox(null, { x: 40, y: 5, w: 10, h: 10 })).toBeNull()
    expect(unionBox({ x: 40, y: 5, w: 10, h: 10 }, null)).toBeNull()
  })
})

describe('收起时竖线中心（restCenters）', () => {
  it('跨很多天也保持从左到右递增（Codex：80 个节点跨 40 天、296px 时中心倒序）', () => {
    const starts = Array.from({ length: 80 }, (_, i) => i > 0 && i % 2 === 0)
    const { xs } = restCenters(296, starts)
    for (let i = 1; i < xs.length; i++) expect(xs[i]).toBeGreaterThan(xs[i - 1])
    expect(xs[0]).toBeGreaterThanOrEqual(0)
    expect(xs[xs.length - 1]).toBeLessThanOrEqual(296)
  })

  it('天数少时日缝照旧是 8px', () => {
    const { xs, gap } = restCenters(296, [false, false, true, false])
    expect(gap).toBe(8)
    expect(xs[0]).toBe(8)
    expect(xs[3]).toBeCloseTo(288)
  })
})
