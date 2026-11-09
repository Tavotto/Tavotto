import { describe, expect, it } from 'vitest'
import type { Manifest, ManifestElement } from '@/lib/api'
import { axisOverflowMm, planTextFit } from './edgeShrinkFit'

const el = (gid: string, role: string, bbox: [number, number, number, number], extra: Partial<ManifestElement> = {}) =>
  ({ gid, role, bbox, editable: [], ...extra }) as unknown as ManifestElement

const manifest = (size: [number, number], elements: ManifestElement[]) =>
  ({ size_mm: size, elements }) as unknown as Manifest

describe('axisOverflowMm：被拖那一维探出图幅多少（与预检「元素超出图幅」同一判据）', () => {
  it('两边各取最糟的一个再相加；容差内不算', () => {
    const m = manifest([100, 50], [
      el('figure', 'figure', [0, 0, 1, 1]),
      el('ylabel', 'axis_label', [-0.05, 0.2, 0.04, 0.6]), // 左探出 5 mm
      el('tick', 'ticklabel', [-0.02, 0.1, 0.03, 0.05]), // 左 2 mm，不如上一条糟
      el('legend', 'legend', [0.9, 0.1, 0.13, 0.2]), // 右 3 mm
      el('xlabel', 'axis_label', [0.4, 0.95, 0.2, 0.051]), // 下 0.05 mm：容差内
    ])
    expect(axisOverflowMm(m, true)).toBeCloseTo(8)
    expect(axisOverflowMm(m, false)).toBe(0)
  })

  it('裁剪框外的部分不算（曲线的未裁剪数据范围），隐藏的不算，manifest 缺席 = 0', () => {
    const m = manifest([100, 50], [
      el('line', 'line', [-2, 0.1, 5, 0.5], { clip_bbox: [0.1, 0.1, 0.8, 0.8] } as Partial<ManifestElement>),
      el('t', 'text', [-0.1, 0.1, 0.05, 0.05], {
        editable: [{ prop: 'visible', value: false }],
      } as unknown as Partial<ManifestElement>),
    ])
    expect(axisOverflowMm(m, true)).toBe(0)
    expect(axisOverflowMm(null, true)).toBe(0)
  })
})

describe('planTextFit：缩小后文字放不下 → 按刚好放得下的图幅出、整体等比缩进框里', () => {
  it('没有新增探出量（或在容差内）不提示', () => {
    expect(planTextFit({ side: 'r', before: [120, 80], overBefore: 2, now: [60, 80], overNow: 2.2 })).toBeNull()
  })

  it('刚好放得下 = 现在 + 新增探出量；另一维同比放大，页面框不变', () => {
    const p = planTextFit({ side: 'r', before: [120, 80], overBefore: 0, now: [60, 80], overNow: 15 })
    expect(p?.scale).toBeCloseTo(0.8)
    expect(p?.sizeMm).toEqual([75, 100])
  })

  it('不超过拖之前的长度（那时放得下）', () => {
    const p = planTextFit({ side: 'b', before: [100, 70], overBefore: 0, now: [100, 50], overNow: 40 })
    expect(p?.sizeMm).toEqual([140, 70])
    expect(p?.scale).toBeCloseTo(50 / 70)
  })

  it('按出来的图幅超过引擎上限就不提供', () => {
    expect(
      planTextFit({ side: 'r', before: [120, 300], overBefore: 0, now: [60, 300], overNow: 30, maxMm: 350 }),
    ).toBeNull()
  })
})
