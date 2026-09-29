import { describe, expect, it } from 'vitest'
import { OVERSIZE_CAP, placePanelInPage, softCap } from './panelPlacement'

/**
 * 新加进画布的图：只缩不放、等比、没有跳变——比页面小的原样放，比页面大的平滑压到
 * 最多 1.15 倍页面（2026-09-28 用户反馈：大图加进来盖住整张页面；同日否掉 130% 阈值
 * 方案，因为阈值处必然跳变）。
 */
const PAGE = { w: 89, h: 60 }

describe('softCap', () => {
  it('上限就是用户拍板的 1.15', () => {
    expect(OVERSIZE_CAP).toBe(1.15)
  })

  it('比页面小的原样', () => {
    for (const r of [0.1, 0.5, 0.99, 1]) expect(softCap(r)).toBe(r)
  })

  it('单调递增、处处没有跳变、永远不超过上限', () => {
    let prev = softCap(0.5)
    // 步长 0.001 扫到 20 倍：任何一步的输出增量都不超过输入增量（斜率 ≤ 1 = 没有跳变）
    for (let i = 501; i <= 20000; i++) {
      const r = i / 1000
      const v = softCap(r)
      expect(v).toBeGreaterThanOrEqual(prev)
      expect(v - prev).toBeLessThanOrEqual(0.001 + 1e-12)
      expect(v).toBeLessThanOrEqual(OVERSIZE_CAP)
      prev = v
    }
  })

  it('稍大的图几乎不缩', () => {
    expect(softCap(1.05) / 1.05).toBeGreaterThan(0.99)
    expect(softCap(1.1) / 1.1).toBeGreaterThan(0.98)
  })

  it('很大的图逼近上限', () => {
    expect(softCap(2)).toBeGreaterThan(OVERSIZE_CAP - 0.01)
    expect(softCap(5)).toBeCloseTo(OVERSIZE_CAP, 6)
  })
})

describe('placePanelInPage', () => {
  it('装得下的图保持原始尺寸（不放大），页面居中', () => {
    const r = placePanelInPage(40, 30, PAGE)
    expect(r).toEqual({ x: (89 - 40) / 2, y: (60 - 30) / 2, w: 40, h: 30 })
  })

  it('很大的图等比缩到约 1.15 倍页面，伸出的部分在页面上居中', () => {
    const r = placePanelInPage(356, 178, PAGE) // 宽是瓶颈：356/89 = 4 倍
    expect(r.w / 89).toBeCloseTo(softCap(4))
    expect(r.w / r.h).toBeCloseTo(2)
    expect(r.x).toBeCloseTo((89 - r.w) / 2)
    expect(r.y).toBeCloseTo((60 - r.h) / 2)
  })

  it('按更吃紧的那一边缩', () => {
    const r = placePanelInPage(50, 240, PAGE) // 高 4 倍、宽 0.56 倍
    expect(r.h / 60).toBeCloseTo(softCap(4))
    expect(r.w / r.h).toBeCloseTo(50 / 240)
  })

  it('稍大一点的图：原图越大放上去越大，尺寸连续', () => {
    const a = placePanelInPage(100, 30, PAGE).w
    const b = placePanelInPage(101, 30, PAGE).w
    expect(b).toBeGreaterThan(a)
    expect(b - a).toBeLessThan(1)
    expect(a).toBeGreaterThan(95) // 100 mm（112%）几乎不缩
  })

  it('有安全边距时按扣掉边距后的可用区域算', () => {
    const r = placePanelInPage(356, 30, { ...PAGE, margin: 5 })
    expect(r.w / 79).toBeCloseTo(softCap(356 / 79))
  })

  it('边距挤没了可用区域时退回整页，不缩成 0', () => {
    const r = placePanelInPage(356, 30, { ...PAGE, margin: 50 })
    expect(r.w / 89).toBeCloseTo(softCap(4))
  })

  it('拖放落点靠边时整张钳进页面', () => {
    const r = placePanelInPage(40, 30, PAGE, { x: 88, y: -10 })
    expect(r.x).toBeCloseTo(89 - 40)
    expect(r.y).toBeCloseTo(0)
  })

  it('拖放落点在页面中间时以落点为中心', () => {
    const r = placePanelInPage(20, 10, PAGE, { x: 30, y: 25 })
    expect(r.x).toBeCloseTo(20)
    expect(r.y).toBeCloseTo(20)
  })

  it('比页面宽的那一维拖放时不跟落点跑，仍在页面上居中', () => {
    const r = placePanelInPage(100, 30, PAGE, { x: 80, y: 30 })
    expect(r.x).toBeCloseTo((89 - r.w) / 2)
    expect(r.y).toBeCloseTo(30 - r.h / 2) // 高度装得下：以落点为中心
  })
})
