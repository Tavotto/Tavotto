/**
 * 拖分隔线时图本身跟手（ADR 0199 修订）：请求带 cols / rows / rows_mm 且与预览框一致；拖动中 DOM 上挂 translate / scale、
 * 不 commit；取消原样放回；松手后保持到每张图落位且新图挂上才放手；超时兜底；失败放回。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { QuickLayoutResponse } from '@/lib/api'
import { gutters } from '@/lib/quickLayoutGrid'

const quickLayoutPlan = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  quickLayoutPlan: (...a: unknown[]) => quickLayoutPlan(...a),
}))
// 面板有没有「已挂上的精确 manifest」：用例按 id 控制
const ready = new Set<string>()
vi.mock('@/store/mountedSvgStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/mountedSvgStore')>()),
  displayedExactManifest: (p: { id: string }) => (ready.has(p.id) ? ({ stem: p.id }) : null),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { useDocumentStore } from '@/store/documentStore'
import { cancelActivePointerGesture } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { useLicenseStore } from '@/store/licenseStore'
import { useMountedSvgStore } from '@/store/mountedSvgStore'
import { useQuickLayoutStore } from '@/store/quickLayoutActions'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { HOLD_CAP_MS } from './quickLayoutFollow'
import { startGutterDrag } from './gutterDrag'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()
const panel = (id: string, x: number, w: number): PanelObject =>
  ({
    id, type: 'panel', x, y: 10, w, h: 60, fileId: `f_${id}`, fileKind: 'pdf', nativeW: w, nativeH: 60,
    script: 'fig.py', overrides: [],
  }) as unknown as PanelObject

const licensed = (features: string[]) => ({
  enabled: true as const, licensed: features.length > 0, state: 'active' as const, features, exp: null, email_hint: '', needs_refresh: false,
})

const okPlan = (): QuickLayoutResponse => ({
  ok: true,
  panels: [
    { key: 'p1', patches: [], box: [10, 10, 60, 60] },
    { key: 'p2', patches: [], box: [73, 10, 110, 60] },
  ],
  labels: [],
  notes: [],
  spec_changes: [],
  page: { w: 193, h: 80 },
  grid: { rows: 1, cols: 2, cells: { p1: [0, 0], p2: [0, 1] }, col_ratios: [1, 2], row_ratios: [1] },
  replay_required: false,
})

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {}, preventDefault() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup' | 'pointercancel', clientX: number, clientY: number) =>
  act(() => void window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true })))
const PX_PER_MM = () => mmToWorld(1) * useViewportStore.getState().zoom
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  quickLayoutPlan.mockReset()
  localStorage.clear()
  await s().switchDocument(emptyProject(), 'd_gutter')
  s().commit(literal('放入'), (d) => {
    d.objects.push(panel('p1', 10, 85), panel('p2', 98, 85))
    d.quickLayout = {
      rows: 1, cols: 2, cells: { p1: [0, 0], p2: [0, 1] }, colRatios: [1, 1], rowRatios: [1],
      boxes: { p1: [10, 10, 85, 60], p2: [98, 10, 85, 60] },
      target: 'double', gapMm: 3, canvas: 'fit', fixSpec: true,
    }
  })
  useLicenseStore.setState({ status: licensed(['quick-layout']) })
  useUiStore.setState({ tool: 'select' })
  useViewportStore.setState({ zoom: 2, panX: 100, panY: 80, spaceDown: false })
  useQuickLayoutStore.setState({ busy: false, preview: null })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  cancelActivePointerGesture()
  act(() => root.unmount())
  host.remove()
  useInteractionStore.getState().end()
})

const gutter = () => gutters(s().doc.quickLayout!).find((g) => g.axis === 'col')!


const W = (mm: number) => mmToWorld(mm)
/** 像 ObjectView 那样摆面板节点：left/top 0、宽高、transform: translate(位置) */
const els: Record<string, HTMLElement> = {}
const place = (id: string) => {
  const o = s().doc.objects.find((x) => x.id === id) as PanelObject
  const el = els[id]
  el.style.width = `${W(o.w)}px`
  el.style.height = `${W(o.h)}px`
  el.style.transform = `translate(${W(o.x)}px, ${W(o.y)}px)`
}
const mountPanels = () => {
  for (const id of ['p1', 'p2']) {
    const el = document.createElement('div')
    el.setAttribute('data-object-id', id)
    host.appendChild(el)
    els[id] = el
    place(id)
  }
}
const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

/** 目标框的屏幕位置（世界坐标，不含平移缩放）：translate + scale 作用在 translate(X,Y) 上之后的左上角与宽 */
const shown = (id: string) => {
  const el = els[id]
  const [tx, ty] = (el.style.translate || '0px 0px').split(' ').map(parseFloat)
  const [sx, sy] = (el.style.scale || '1 1').split(' ').map(parseFloat)
  const [, X, Y] = /translate\(([-\d.]+)px, ([-\d.]+)px\)/.exec(el.style.transform)!.map(Number) as unknown as number[]
  return { x: tx + sx * X, y: ty + sy * Y, w: sx * parseFloat(el.style.width), h: sy * parseFloat(el.style.height) }
}
const expectBox = (id: string, b: number[]) => {
  const g = shown(id)
  expect(g.x).toBeCloseTo(W(b[0]), 1)
  expect(g.y).toBeCloseTo(W(b[1]), 1)
  expect(g.w).toBeCloseTo(W(b[2]), 1)
  expect(g.h).toBeCloseTo(W(b[3]), 1)
}

describe('拖分隔线：图跟手 + 请求与预览一致', () => {
  beforeEach(() => {
    ready.clear()
    vi.useRealTimers()
    mountPanels()
  })

  it('拖动中：面板节点按预览框摆（translate / scale），文档不动、不发请求、不重画淡色轮廓', () => {
    const depth = s().past.length
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', -20 * PX_PER_MM(), 0)
    const pv = useQuickLayoutStore.getState().preview!
    expectBox('p1', pv.p1)
    expectBox('p2', pv.p2)
    expect(els.p1.style.transformOrigin).toBe('0 0')
    fire('pointermove', -10 * PX_PER_MM(), 0)
    // 从 base 现算：再动一次仍等于这一次的预览框，不累加
    expectBox('p1', useQuickLayoutStore.getState().preview!.p1)
    expect(els.p1.style.transform).toBe(`translate(${W(10)}px, ${W(10)}px)`) // React 的几何一个字没改
    expect(s().past.length).toBe(depth)
    expect(quickLayoutPlan).not.toHaveBeenCalled()
    // 跟手的图不再画淡色轮廓
    expect(useQuickLayoutStore.getState().live).toEqual(['p1', 'p2'])
    act(() => void cancelActivePointerGesture())
  })

  it('标签跟着自己那张图的左上角走', () => {
    s().commit(literal('标签'), (d) => {
      d.objects.push({ id: 'lbl', type: 'text', x: 11, y: 11, w: 5, h: 5, text: '(b)', role: 'sub_label' } as never)
    })
    const lbl = document.createElement('div')
    lbl.setAttribute('data-object-id', 'lbl')
    host.appendChild(lbl)
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', -20 * PX_PER_MM(), 0)
    // p1 的左上角没动（只变窄）：标签不动
    expect(lbl.style.translate === '' || parseFloat(lbl.style.translate) === 0).toBe(true)
    act(() => void cancelActivePointerGesture())
  })

  it('Esc：translate / scale / transform-origin 原样放回', async () => {
    els.p1.style.translate = '1px 2px'
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', -20 * PX_PER_MM(), 0)
    expect(els.p1.style.scale).not.toBe('')
    act(() => void cancelActivePointerGesture())
    await tick()
    expect(els.p1.style.translate).toBe('1px 2px')
    expect(els.p1.style.scale).toBe('')
    expect(els.p1.style.transformOrigin).toBe('')
    expect(els.p2.style.translate).toBe('')
    expect(quickLayoutPlan).not.toHaveBeenCalled()
  })

  it('松手：请求带 cols / rows / rows_mm，行高 = 预览框里每行的高度（列拖与行拖都带）', async () => {
    quickLayoutPlan.mockResolvedValue(okPlan())
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', -25 * PX_PER_MM(), 0)
    const pv = useQuickLayoutStore.getState().preview!
    fire('pointerup', -25 * PX_PER_MM(), 0)
    await flush()
    const req = quickLayoutPlan.mock.calls[0][0]
    expect(req.ratios.rows_mm).toEqual([pv.p1[3]])
    expect(req.ratios.rows_mm[0]).toBeCloseTo(60)
    expect(req.ratios.cols[0]).toBeCloseTo((2 * 60) / 170)
  })

  it('行拖：rows_mm 是每行任意一格预览框的高度，列占比不变', async () => {
    await s().switchDocument(emptyProject(), 'd_rows')
    s().commit(literal('放入'), (d) => {
      d.objects.push(
        { ...panel('p1', 10, 60), y: 10, h: 40 } as PanelObject,
        { ...panel('p2', 10, 60), y: 53, h: 60 } as PanelObject,
      )
      d.quickLayout = {
        rows: 2, cols: 1, cells: { p1: [0, 0], p2: [1, 0] }, colRatios: [1], rowRatios: [1, 1],
        boxes: { p1: [10, 10, 60, 40], p2: [10, 53, 60, 60] },
        target: 'double', gapMm: 3, canvas: 'fit', fixSpec: true,
      }
    })
    quickLayoutPlan.mockResolvedValue({ ...okPlan(), panels: [{ key: 'p1', patches: [], box: [10, 10, 60, 50] }, { key: 'p2', patches: [], box: [10, 63, 60, 50] }] })
    const g = gutters(s().doc.quickLayout!).find((x) => x.axis === 'row')!
    startGutterDrag(down(0, 0), g)
    fire('pointermove', 0, 10 * PX_PER_MM())
    const pv = useQuickLayoutStore.getState().preview!
    fire('pointerup', 0, 10 * PX_PER_MM())
    await flush()
    const req = quickLayoutPlan.mock.calls[0][0]
    expect(req.ratios.rows_mm).toEqual([pv.p1[3], pv.p2[3]])
    expect(pv.p1[3] + pv.p2[3]).toBeCloseTo(100)
    expect(req.ratios.cols).toEqual([1])
  })

  it('松手后到落位之前一直钉在最终框上；图落位 + 新图挂上的同一刻才放手', async () => {
    quickLayoutPlan.mockResolvedValue(okPlan())
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', -25 * PX_PER_MM(), 0)
    fire('pointerup', -25 * PX_PER_MM(), 0)
    await flush()
    // commit 已发生：文档里 w 是「乘完正好是 box」之前的值（nativeW 还是旧的），节点是 React 的新几何
    // okPlan 没带 size_mm，所以文档 w / h 直接 = box；模拟「位置先到、尺寸还是旧的」
    s().silent((d) => { d.objects.find((o) => o.id === 'p1')!.w = 85 })
    act(() => { place('p1'); place('p2') })
    await tick()
    expectBox('p1', [10, 10, 60, 60])
    expectBox('p2', [73, 10, 110, 60])
    expect(els.p1.style.scale).not.toBe('')
    // 尺寸跟上了，但新图还没挂上：不放手
    s().silent((d) => { d.objects.find((o) => o.id === 'p1')!.w = 60 })
    act(() => { place('p1') })
    await tick()
    expect(els.p1.style.scale).not.toBe('')
    ready.add('p1'); ready.add('p2')
    act(() => useMountedSvgStore.getState().set('p1', '<svg/>'))
    await tick()
    expect(els.p1.style.scale).toBe('')
    expect(els.p1.style.translate).toBe('')
    expect(els.p2.style.scale).toBe('')
    expect(els.p1.style.transformOrigin).toBe('')
  })

  it('一直没落位：HOLD_CAP_MS 后无论如何放手', async () => {
    vi.useFakeTimers()
    quickLayoutPlan.mockResolvedValue(okPlan())
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', -25 * PX_PER_MM(), 0)
    fire('pointerup', -25 * PX_PER_MM(), 0)
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    expect(els.p1.style.scale).not.toBe('')
    await act(async () => { await vi.advanceTimersByTimeAsync(HOLD_CAP_MS - 100) })
    expect(els.p1.style.scale).not.toBe('')
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(els.p1.style.scale).toBe('')
    expect(els.p2.style.translate).toBe('')
    vi.useRealTimers()
  })

  it('请求失败 / 作废：不动画，节点原样放回', async () => {
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_converged' })
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', -25 * PX_PER_MM(), 0)
    fire('pointerup', -25 * PX_PER_MM(), 0)
    await flush()
    expect(els.p1.style.scale).toBe('')
    expect(els.p1.style.translate).toBe('')
    expect(els.p1.style.transformOrigin).toBe('')
  })
})
