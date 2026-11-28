/**
 * N5 第 4 步的写入侧（/2 完整显示框）：图内几何手势带的是显示帧 F 的分数，
 * 落盘语义是源分数 S——`setOverride`/`setOverrides` 经唯一适配器
 * （`sourceFrameOverride`）换回 S 一次；缺精确配对帧时拒绝整批写入
 * （零正文 + 可见提示），绝不让 F 分数冒充 S 落盘。/1 面板与非帧相对 prop 原样通过。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { setOverride, setOverrides } from './actions'
import { useDocumentStore } from './documentStore'
import { useRenderStore } from './renderStore'
import { useUiStore } from './uiStore'

// /2 显示框：源 viewport 100×100 pt，框 rect_pt [-8,-6,116,112]（F 比 S 大、含负边距）
const FRAME = { version: 1 as const, renderProfile: 'native-manifest/2' as const, originPt: [-8, -6] as [number, number] }
const RENDER_FRAME = { version: 1 as const, source: 'computed-svg' as const,
  source_viewport_pt: [100, 100] as [number, number], rect_pt: [-8, -6, 116, 112] as [number, number, number, number],
  edit_basis: 'source-viewport-pt' as const }
// F 分数 → S 分数，按实现同一运算顺序（frame_mm = (-l,-t,sw,sh)，basis = F 的 pt 尺寸）
const toS = (fx: number, fy: number): [number, number] => {
  const f = { x: 8 / 116, y: 6 / 112, w: 100 / 116, h: 100 / 112 }
  return [(fx - f.x) / f.w, (fy - f.y) / f.h]
}
const seed = (p: PanelObject) =>
  seedExactRender(p, { stem: 'chart', size_mm: [40, 30], elements: [] }, { chartRevision: 'rev-2', renderFrame: RENDER_FRAME })

const panel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    fileId: 'chart.svg',
    fileKind: 'svg',
    nativeW: 40,
    nativeH: 30,
    overrides: [],
    ...over,
  }) as unknown as PanelObject

const doc = () => useDocumentStore.getState()
const overridesOf = () => (doc().doc.objects[0] as PanelObject).overrides
const putPanel = (p: PanelObject) => doc().commit(literal('fixture'), (d) => { d.objects = [p] })
const status = () => useUiStore.getState().status

beforeEach(async () => {
  useRenderStore.getState().clear()
  await doc().switchDocument(emptyProject(), 'frame-write-test')
  putPanel(panel())
})

describe('/2 写入侧 F→S 适配', () => {
  it('loc_frac 按显示框分数写，落盘是源分数', () => {
    const p = panel({ chartFrame: FRAME })
    putPanel(p)
    seed(p)
    expect(setOverride('p1', 'legend_1', 'loc_frac', [0.6, 0.4])).toBe(true)
    const written = overridesOf().find((o) => o.gid === 'legend_1' && o.prop === 'loc_frac')
    expect(written?.value).toEqual(toS(0.6, 0.4))
  })

  it('points_frac 变长点列整列换算', () => {
    const p = panel({ chartFrame: FRAME })
    putPanel(p)
    seed(p)
    expect(setOverride('p1', 'line_1', 'points_frac', [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])).toBe(true)
    const written = overridesOf().find((o) => o.gid === 'line_1' && o.prop === 'points_frac')
    expect(written?.value).toEqual([...toS(0.1, 0.2), ...toS(0.3, 0.4), ...toS(0.5, 0.6)])
  })

  it('批量写入里只换帧相对的那几条，其余原样', () => {
    const p = panel({ chartFrame: FRAME })
    putPanel(p)
    seed(p)
    expect(setOverrides('p1', literal('批'), [
      { gid: 'legend_1', prop: 'loc_frac', value: [0.6, 0.4] },
      { gid: 'axes_0.title', prop: 'text', value: 'A' },
    ])).toBe(true)
    const loc = overridesOf().find((o) => o.gid === 'legend_1' && o.prop === 'loc_frac')
    expect(loc?.value).toEqual(toS(0.6, 0.4))
    const text = overridesOf().find((o) => o.gid === 'axes_0.title' && o.prop === 'text')
    expect(text?.value).toBe('A')
  })

  it('缺精确配对帧：整批拒绝、零写入、可见提示', () => {
    putPanel(panel({ chartFrame: FRAME }))
    const before = overridesOf()
    expect(setOverride('p1', 'legend_1', 'loc_frac', [0.6, 0.4])).toBe(false)
    expect(overridesOf()).toBe(before)
    expect(status()?.key).toBe('status.chartFrameStale')
    expect(setOverrides('p1', literal('批'), [
      { gid: 'legend_1', prop: 'loc_frac', value: [0.6, 0.4] },
      { gid: 'axes_0.title', prop: 'text', value: 'A' },
    ])).toBe(false)
    expect(overridesOf()).toBe(before)
  })

  it('渲染在途（变体已漂移）同样拒绝', () => {
    const p = panel({ chartFrame: FRAME })
    putPanel(p)
    seed(p)
    // 上一笔写入让变体键漂了，新渲染还没回来：authority 就地失效
    expect(setOverride('p1', 'axes_0.title', 'fontsize', 12)).toBe(true)
    const before = overridesOf()
    expect(setOverride('p1', 'legend_1', 'loc_frac', [0.6, 0.4])).toBe(false)
    expect(overridesOf()).toBe(before)
    expect(status()?.key).toBe('status.chartFrameStale')
  })

  it('/1 面板与非帧相对 prop 原样通过', () => {
    expect(setOverride('p1', 'legend_1', 'loc_frac', [0.6, 0.4])).toBe(true)
    expect(overridesOf().find((o) => o.gid === 'legend_1')?.value).toEqual([0.6, 0.4])
    const p = panel({ chartFrame: FRAME })
    putPanel(p)
    seed(p)
    expect(setOverride('p1', 'axes_0.title', 'fontsize', 12)).toBe(true)
    expect(overridesOf().find((o) => o.gid === 'axes_0.title' && o.prop === 'fontsize')?.value).toBe(12)
  })

  it('换算后的值与落盘值相同 = no-op，不写第二条', () => {
    const s = toS(0.6, 0.4)
    const p = panel({ chartFrame: FRAME, overrides: [{ gid: 'legend_1', prop: 'loc_frac', value: s }] })
    putPanel(p)
    seed(p)
    const before = overridesOf()
    expect(setOverride('p1', 'legend_1', 'loc_frac', [0.6, 0.4])).toBe(true)
    // 没有新增条目（no-op 判据看的是换算后的落盘值，不是 F 输入）
    expect(overridesOf()).toBe(before)
  })
})
