import { describe, expect, it } from 'vitest'
import { OVERSIZE_CAP, PLACE_GAP, placePanelInPage, softCap } from './panelPlacement'
import { visualBounds } from './geometry'
import type { CanvasObject } from '@/types/document'

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

/** 新图避开已有的图（设计稿 C9）：右边优先、一行放不下换行、放不下退回居中 */
describe('placePanelInPage 避开已有对象', () => {
  const overlaps = (a: Box, b: Box) =>
    a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
  type Box = { x: number; y: number; w: number; h: number }
  const P = { w: 200, h: 100 }

  it('空页面：仍居中', () => {
    expect(placePanelInPage(40, 30, P, undefined, [])).toEqual({ x: 80, y: 35, w: 40, h: 30 })
  })

  it('已有一张居中图：新图排到它右边，与它同顶', () => {
    const first = placePanelInPage(60, 40, P) // 居中：x 70..130, y 30..70
    const r = placePanelInPage(40, 30, P, undefined, [first])
    expect(r.x).toBeCloseTo(first.x + first.w + PLACE_GAP)
    expect(r.y).toBeCloseTo(first.y)
    expect(overlaps(r, first)).toBe(false)
  })

  it('一行放满：右边放不下就换到下方（贴左边距）', () => {
    const full = { x: 0, y: 0, w: 190, h: 40 }
    const r = placePanelInPage(40, 30, P, undefined, [full])
    expect(r.x).toBeCloseTo(0)
    expect(r.y).toBeCloseTo(40 + PLACE_GAP)
    expect(overlaps(r, full)).toBe(false)
  })

  it('连续加三张（居中一张 + 右 + 左）：互不重叠且都在页面内', () => {
    const placed: Box[] = []
    for (let i = 0; i < 3; i++) {
      const r = placePanelInPage(60, 40, P, undefined, placed)
      expect(placed.some((o) => overlaps(r, o))).toBe(false)
      expect(r.x).toBeGreaterThanOrEqual(0)
      expect(r.y).toBeGreaterThanOrEqual(0)
      expect(r.x + r.w).toBeLessThanOrEqual(P.w + 1e-9)
      expect(r.y + r.h).toBeLessThanOrEqual(P.h + 1e-9)
      placed.push(r)
    }
  })

  it('右边与下方都放不下时才退到同行左侧', () => {
    const mid = { x: 70, y: 30, w: 100, h: 40 } // 右边只剩 27，下方也放不下 40 高
    const r = placePanelInPage(60, 40, P, undefined, [mid])
    expect(overlaps(r, mid)).toBe(false)
    expect(r.x).toBeCloseTo(0)
  })

  it('页面放不下：退回居中（并保持页面内的钳位）', () => {
    const full = { x: 0, y: 0, w: 200, h: 100 }
    expect(placePanelInPage(40, 30, P, undefined, [full])).toEqual({ x: 80, y: 35, w: 40, h: 30 })
  })

  it('比页面大的图不找空位：仍居中、两边均匀伸出', () => {
    const r = placePanelInPage(800, 100, P, undefined, [{ x: 0, y: 0, w: 10, h: 10 }])
    expect(r.x).toBeCloseTo((200 - r.w) / 2)
  })

  it('有落点（拖放）：尊重落点，不避让', () => {
    const r = placePanelInPage(20, 10, P, { x: 100, y: 50 }, [{ x: 90, y: 45, w: 20, h: 10 }])
    expect(r).toEqual({ x: 90, y: 45, w: 20, h: 10 })
  })

  it('安全边距内才放', () => {
    const r = placePanelInPage(40, 30, { ...P, margin: 10 }, undefined, [
      { x: 10, y: 10, w: 175, h: 30 },
    ])
    expect(r.x).toBeGreaterThanOrEqual(10)
    expect(r.y).toBeGreaterThanOrEqual(10)
  })

  it('旋转 / 缩放后的包围盒当障碍：转了 90° 的宽图占的是竖条', () => {
    // 90° 面板：x/y/w/h 已是旋转后的盒（20×80）；居中后新图排它右边而不是按旧的 80×20 判
    const rotated = { x: 90, y: 10, w: 20, h: 80 }
    const r = placePanelInPage(40, 30, P, undefined, [rotated])
    expect(overlaps(r, rotated)).toBe(false)
    expect(r.x).toBeCloseTo(90 + 20 + PLACE_GAP)
  })
})

describe('visualBounds', () => {
  const o = (rotationDeg: number, type: 'shape' | 'panel' = 'shape') =>
    ({ type, x: 10, y: 10, w: 40, h: 10, rotationDeg }) as unknown as CanvasObject
  it('未旋转 = 自身盒', () => {
    expect(visualBounds(o(0))).toEqual({ x: 10, y: 10, w: 40, h: 10 })
  })
  it('任意角度绕中心转出外接矩形（90° 宽高互换、中心不动）', () => {
    const b = visualBounds(o(90))
    expect(b.w).toBeCloseTo(10)
    expect(b.h).toBeCloseTo(40)
    expect(b.x + b.w / 2).toBeCloseTo(30)
    expect(b.y + b.h / 2).toBeCloseTo(15)
  })
  it('面板的 rotationDeg 不参与（走 90° 步进，盒本身已转）', () => {
    expect(visualBounds(o(45, 'panel'))).toEqual({ x: 10, y: 10, w: 40, h: 10 })
  })
})
