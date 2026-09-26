import { describe, expect, it } from 'vitest'
import { offscreen, placeCentered, placeCoachmark, shouldGlide, unionBoxes } from './position'

const vp = { w: 1000, h: 600 }
const card = { w: 300, h: 120 }

describe('placeCoachmark', () => {
  it('默认放在锚点下方、左对齐', () => {
    const p = placeCoachmark({ x: 100, y: 100, w: 80, h: 30 }, card, vp)
    expect(p).toEqual({ x: 100, y: 140, side: 'bottom' })
  })

  it('下方放不下 → 上方', () => {
    const p = placeCoachmark({ x: 100, y: 520, w: 80, h: 30 }, card, vp)
    expect(p.side).toBe('top')
    expect(p.y).toBe(520 - 10 - 120)
  })

  it('上下都放不下 → 右侧；再不行 → 左侧', () => {
    const short = { w: 1000, h: 150 }
    const r = placeCoachmark({ x: 100, y: 10, w: 80, h: 130 }, card, short)
    expect(r.side).toBe('right')
    expect(r.x).toBe(100 + 80 + 10)
    const l = placeCoachmark({ x: 650, y: 10, w: 80, h: 130 }, card, short)
    expect(l.side).toBe('left')
    expect(l.x).toBe(650 - 10 - 300)
  })

  it('四面都不够就夹进视口，绝不出界', () => {
    const tiny = { w: 320, h: 140 }
    const p = placeCoachmark({ x: 0, y: 0, w: 320, h: 140 }, card, tiny)
    expect(p.x).toBeGreaterThanOrEqual(8)
    expect(p.x + card.w).toBeLessThanOrEqual(tiny.w - 8 + 0.001)
    expect(p.y + card.h).toBeLessThanOrEqual(tiny.h - 8 + 0.001)
  })

  it('靠右边缘的锚点：卡片向左收，不出视口', () => {
    const p = placeCoachmark({ x: 950, y: 100, w: 40, h: 30 }, card, vp)
    expect(p.side).toBe('bottom')
    expect(p.x + card.w).toBeLessThanOrEqual(vp.w - 8)
  })
})

describe('其余纯函数', () => {
  it('placeCentered 居中且不小于边距', () => {
    expect(placeCentered(card, vp)).toEqual({ x: 350, y: 240 })
    expect(placeCentered(card, { w: 100, h: 100 })).toEqual({ x: 8, y: 8 })
  })

  it('offscreen 只在整块出界时为真', () => {
    expect(offscreen({ x: -50, y: 10, w: 40, h: 10 }, vp)).toBe(true)
    expect(offscreen({ x: -5, y: 10, w: 40, h: 10 }, vp)).toBe(false)
    expect(offscreen({ x: 10, y: 700, w: 40, h: 10 }, vp)).toBe(true)
  })

  it('unionBoxes 跳过零尺寸、并集其余', () => {
    expect(unionBoxes([])).toBeNull()
    expect(unionBoxes([{ x: 0, y: 0, w: 0, h: 0 }])).toBeNull()
    expect(
      unionBoxes([
        { x: 10, y: 10, w: 10, h: 5 },
        { x: 0, y: 0, w: 0, h: 0 },
        { x: 30, y: 12, w: 5, h: 20 },
      ]),
    ).toEqual({ x: 10, y: 10, w: 25, h: 22 })
  })

  it('shouldGlide：没落过位不滑；路上碰到锚点不滑；只有碰不到才滑（issue #581）', () => {
    const anchor = { x: 198, y: 184, w: 134, h: 128 }
    const below = { x: 198, y: 322, w: 300, h: 124 }
    // 刚挂载（屏幕外）→ 直接出现
    expect(shouldGlide(null, below, anchor)).toBe(false)
    // 从左上方飞来、要经过锚点 → 跳
    expect(shouldGlide({ x: 40, y: 150, w: 300, h: 124 }, below, anchor)).toBe(false)
    // 从画布中央挪到锚点下方：整段都在锚点下沿之下 → 滑
    expect(shouldGlide({ x: 550, y: 388, w: 300, h: 124 }, below, anchor)).toBe(true)
    // 贴边不算碰：卡片上沿正好等于锚点下沿
    expect(shouldGlide({ x: 198, y: 312, w: 300, h: 124 }, below, anchor)).toBe(true)
    // 没有锚点（居中的欢迎页 / 等待目标）→ 没有要护着的东西
    expect(shouldGlide(below, { x: 550, y: 388, w: 300, h: 124 }, null)).toBe(true)
  })
})
