import { describe, expect, it } from 'vitest'
import { guideBox, innerGid, manifestsPending, owningPanel, parseGuideGid } from './reviewGids'
import { renderKeyOf } from '@/store/renderStore'
import type { CanvasObject } from '@/types/document'

const panel = (id: string) => ({ id, type: 'panel', fileId: `${id}.pdf`, overrides: [] }) as unknown as CanvasObject
const render = { byKey: {}, latest: {} }

describe('排版里的元素 gid', () => {
  it('面板框 = 对象 id 原样；面板内元素 = <对象id>::<gid> 先按对象拆', () => {
    expect(innerGid('p1::legend')).toBe('legend')
    expect(innerGid('legend')).toBe('legend')
    expect(owningPanel([panel('p1'), panel('p2')], render, 'p2')?.id).toBe('p2')
    expect(owningPanel([panel('p1')], render, 'p9::legend')).toBeNull()
  })
})

describe('元素表还没到', () => {
  it('没有任何 manifest 的面板算「待定」：不能据此判元素不在了', () => {
    expect(manifestsPending([panel('p1'), panel('p2')], render).map((p) => p.id)).toEqual(['p1', 'p2'])
    expect(manifestsPending([], render)).toEqual([])
  })
})

describe('边框线 / 网格线 / 刻度线的合成 gid', () => {
  const line = (pts: [number, number][]) => ({ kind: 'multi_path', paths: [{ points: pts, closed: false }], fill: false, stroke: true }) as never
  const els = [
    {
      gid: 'axes_0',
      spines: { left: { visible: true, ticks: true, from: [0.1, 0.9], to: [0.1, 0.1] }, top: { visible: false, ticks: false, from: [0.1, 0.1], to: [0.9, 0.1] } },
      grid: { x: line([[0.3, 0.9], [0.3, 0.1], [0.6, 0.1]]) },
      tickmarks: { y: line([[0.1, 0.5], [0.09, 0.5]]) },
    },
  ] as never

  it('只认「子图 gid + 后缀」', () => {
    expect(parseGuideGid('axes_0.spine_left')).toEqual({ base: 'axes_0', kind: 'spine', key: 'left' })
    expect(parseGuideGid('axes_1.ygrid')).toEqual({ base: 'axes_1', kind: 'grid', key: 'y' })
    expect(parseGuideGid('axes_0.xtickmarks')).toEqual({ base: 'axes_0', kind: 'tickmarks', key: 'x' })
    expect(parseGuideGid('axes_0.xticks')).toBeNull()
    expect(parseGuideGid('legend')).toBeNull()
  })
  it('框来自子图那一项的当前数据；竖线给最小厚度', () => {
    const left = guideBox(els, 'axes_0.spine_left')!
    expect(left[0]).toBeCloseTo(0.099, 5)
    expect(left[2]).toBeCloseTo(0.002, 5)
    expect(left[3]).toBeCloseTo(0.8, 5)
    const grid = guideBox(els, 'axes_0.xgrid')!
    expect(grid.map((v) => Math.round(v * 1e4) / 1e4)).toEqual([0.3, 0.1, 0.3, 0.8])
    expect(guideBox(els, 'axes_0.ytickmarks')![0]).toBeCloseTo(0.09, 5)
  })
  it('数据没了 = null：隐藏的边、没开的网格、不存在的子图', () => {
    expect(guideBox(els, 'axes_0.spine_top')).toBeNull()
    expect(guideBox(els, 'axes_0.ygrid')).toBeNull()
    expect(guideBox(els, 'axes_0.xtickmarks')).toBeNull()
    expect(guideBox(els, 'axes_9.spine_left')).toBeNull()
  })
  it('owningPanel：子图还在且数据在才认；网格关了 = 找不到所属面板', () => {
    const p = panel('p1')
    const at = (m: unknown) => ({ byKey: { [renderKeyOf(p as never)]: { manifest: m } }, latest: {} }) as never
    const man = (e: unknown) => ({ stem: 'p1', size_mm: [40, 30], elements: [e] })
    expect(owningPanel([p], at(man(els[0])), 'p1::axes_0.xgrid')?.id).toBe('p1')
    expect(owningPanel([p], at(man(els[0])), 'p1::axes_0.spine_left')?.id).toBe('p1')
    expect(owningPanel([p], at(man({ ...(els[0] as object), grid: undefined })), 'p1::axes_0.xgrid')).toBeNull()
    expect(owningPanel([p], at(man(els[0])), 'p1::axes_0.spine_top')).toBeNull()
  })
})
