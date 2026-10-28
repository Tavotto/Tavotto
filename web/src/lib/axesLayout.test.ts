/**
 * 锁比例子图的等比缩放框（`lockedAxesResize`）：宽高同乘一个倍数、固定点在被拖手柄对侧、
 * 钳在整图里。比例一变 matplotlib 的 apply_aspect 就会在重渲时把框缩回去并居中，
 * 所以这里钉死的是「比例不变」与「固定的那条边不动」。
 */
import { describe, expect, it } from 'vitest'

import { axesAspectLocked, lockedAxesResize, type Rect4 } from './axesLayout'

// bottom-origin：左 0.1、下 0.5、宽 0.35、高 0.4
const START: Rect4 = [0.1, 0.5, 0.35, 0.4]
const ratio = (r: Rect4) => r[3] / r[2]

describe('axesAspectLocked', () => {
  it('aspect 字段是 auto 或缺省 = 不锁；equal / 数值 = 锁', () => {
    expect(axesAspectLocked({ editable: [] })).toBe(false)
    expect(axesAspectLocked({ editable: [{ prop: 'aspect', value: 'auto' }] })).toBe(false)
    expect(axesAspectLocked({ editable: [{ prop: 'aspect', value: 'equal' }] })).toBe(true)
    expect(axesAspectLocked({ editable: [{ prop: 'aspect', value: '1.0' }] })).toBe(true)
  })
})

describe('lockedAxesResize', () => {
  it('上柄往上拉：下边与左边不动，宽跟着同比放大', () => {
    const r = lockedAxesResize(START, 'n', 0, -0.04, 0.05)
    expect(r[1]).toBeCloseTo(0.5, 9)
    expect(r[0]).toBeCloseTo(0.1, 9)
    expect(r[1] + r[3]).toBeCloseTo(0.94, 9)
    expect(ratio(r)).toBeCloseTo(ratio(START), 9)
  })

  it('右柄：左边与上边不动', () => {
    const r = lockedAxesResize(START, 'e', 0.035, 0, 0.05)
    expect(r[0]).toBeCloseTo(0.1, 9)
    expect(r[2]).toBeCloseTo(0.385, 9)
    expect(r[1] + r[3]).toBeCloseTo(0.9, 9)
    expect(ratio(r)).toBeCloseTo(ratio(START), 9)
  })

  it('左下角柄：右上角不动，倍数取偏离更多的那一轴；by 可指定', () => {
    const r = lockedAxesResize(START, 'sw', -0.07, 0.01, 0.05)
    expect(r[0] + r[2]).toBeCloseTo(0.45, 9)
    expect(r[1] + r[3]).toBeCloseTo(0.9, 9)
    expect(r[2]).toBeCloseTo(0.42, 9) // 横向 ×1.2 胜出
    const byY = lockedAxesResize(START, 'sw', -0.07, 0.01, 0.05, 'y')
    expect(byY[3]).toBeCloseTo(0.41, 9)
    expect(ratio(byY)).toBeCloseTo(ratio(START), 9)
  })

  it('拉过头：钳在整图里，比例仍不变', () => {
    const r = lockedAxesResize(START, 'ne', 5, -5, 0.05)
    expect(r[1] + r[3]).toBeLessThanOrEqual(1 + 1e-12)
    expect(r[0] + r[2]).toBeLessThanOrEqual(1 + 1e-12)
    expect(r[1] + r[3]).toBeCloseTo(1, 9) // 先顶到的是上边
    expect(ratio(r)).toBeCloseTo(ratio(START), 9)
  })

  it('缩过头：两边都不小于最小尺寸', () => {
    const r = lockedAxesResize(START, 'e', -1, 0, 0.05)
    expect(Math.min(r[2], r[3])).toBeCloseTo(0.05, 9)
    expect(ratio(r)).toBeCloseTo(ratio(START), 9)
  })
})
