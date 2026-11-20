/**
 * 一键排版之后拖行 / 列分隔线（ADR 0199 修订）：未解锁什么都不画；拖动中只画淡色轮廓（不 commit、不发请求）；
 * 松手一次请求 + 一次 commit（「调整排版比例」）；Esc / pointercancel 不发请求。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage, literal } from '@/i18n'
import type { QuickLayoutResponse } from '@/lib/api'
import { gutters } from '@/lib/quickLayoutGrid'

const quickLayoutPlan = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  quickLayoutPlan: (...a: unknown[]) => quickLayoutPlan(...a),
}))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: vi.fn(),
}))

import { useDocumentStore } from '@/store/documentStore'
import { cancelActivePointerGesture } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useLicenseStore } from '@/store/licenseStore'
import { useQuickLayoutStore } from '@/store/quickLayoutActions'
import { useUiStore } from '@/store/uiStore'
import { getTransform, mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { QuickLayoutGutters } from './QuickLayoutGutters'
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

const mount = () => act(() => root.render(<QuickLayoutGutters />))
const gutter = () => gutters(s().doc.quickLayout!).find((g) => g.axis === 'col')!

describe('分隔线命中区', () => {
  it('解锁 + 记忆有效：每条分隔线一个命中区，无文字', () => {
    mount()
    const strips = host.querySelectorAll('[data-quick-layout-gutter]')
    expect(strips).toHaveLength(1)
    expect(strips[0].textContent).toBe('')
  })

  it('没解锁（有入口、没权益）/ 入口没出现：什么都不画', () => {
    useLicenseStore.setState({ status: licensed([]) })
    mount()
    expect(host.querySelector('[data-quick-layout-gutters]')).toBeNull()
    useLicenseStore.setState({ status: null })
    expect(host.querySelector('[data-quick-layout-gutters]')).toBeNull()
  })

  it('记忆失效（挪过一张）：不画；非选择工具时不挂命中区', () => {
    mount()
    act(() => useUiStore.setState({ tool: 'rect' as never }))
    expect(host.querySelectorAll('[data-quick-layout-gutter]')).toHaveLength(0)
    act(() => useUiStore.setState({ tool: 'select' }))
    expect(host.querySelectorAll('[data-quick-layout-gutter]')).toHaveLength(1)
    act(() => s().commit(literal('挪'), (d) => { d.objects[0].x += 3 }))
    expect(host.querySelector('[data-quick-layout-gutters]')).toBeNull()
  })
})

describe('握柄与气泡', () => {
  const grips = () => [...host.querySelectorAll('[data-quick-layout-grip]')]
  const shown = () => grips().filter((g) => g.getAttribute('data-quick-layout-grip') === 'shown')
  const over = (x: number, y: number) => act(() => useInteractionStore.setState({ cursor: { x, y } }))
  const away = () => act(() => useInteractionStore.setState({ cursor: null }))
  beforeEach(() => {
    useInteractionStore.setState({ cursor: null })
    useSelectionStore.setState({ ids: [] })
  })

  it('命中区带原生气泡「拖动调整比例」', () => {
    mount()
    expect(host.querySelector('[data-quick-layout-gutter]')!.getAttribute('title')).toBe('拖动调整比例')
  })

  it('鼠标不在页面上、也没选这份排版的图：握柄是透明的；移到页面上才显出来', () => {
    mount()
    expect(grips()).toHaveLength(1)
    expect(shown()).toHaveLength(0)
    over(20, 20)
    expect(shown()).toHaveLength(1)
    over(-5, 20) // 出了页面
    expect(shown()).toHaveLength(0)
    away()
    expect(shown()).toHaveLength(0)
  })

  it('选中了这份排版里的图：鼠标不在页面上也显；选的是别的图不算', () => {
    mount()
    act(() => useSelectionStore.setState({ ids: ['p1'] }))
    expect(shown()).toHaveLength(1)
    act(() => useSelectionStore.setState({ ids: ['other'] }))
    expect(shown()).toHaveLength(0)
  })

  it('握柄不吃指针事件，只挂在命中区里面', () => {
    mount()
    over(20, 20)
    const grip = host.querySelector<HTMLElement>('[data-quick-layout-grip]')!
    expect(grip.className).toContain('pointer-events-none')
    expect(grip.closest('[data-quick-layout-gutter]')).not.toBeNull()
  })

  it('没解锁 / 记忆失效 / 别的手势在走 / 非选择工具 / 忙碌 / 图内编辑：一颗握柄都没有', () => {
    over(20, 20)
    mount()
    expect(grips()).toHaveLength(1)
    act(() => useInteractionStore.getState().begin('move' as never))
    expect(grips()).toHaveLength(0)
    act(() => useInteractionStore.getState().end())
    act(() => useQuickLayoutStore.setState({ busy: true }))
    expect(grips()).toHaveLength(0)
    act(() => useQuickLayoutStore.setState({ busy: false }))
    act(() => useUiStore.setState({ tool: 'rect' as never }))
    expect(grips()).toHaveLength(0)
    act(() => useUiStore.setState({ tool: 'select' }))
    act(() => useUiStore.setState({ elementPanelId: 'p1' }))
    expect(host.querySelector('[data-quick-layout-gutters]')).toBeNull()
    act(() => useUiStore.setState({ elementPanelId: null }))
    act(() => useLicenseStore.setState({ status: licensed([]) }))
    expect(host.querySelector('[data-quick-layout-gutters]')).toBeNull()
    act(() => useLicenseStore.setState({ status: licensed(['quick-layout']) }))
    act(() => s().commit(literal('挪'), (d) => { d.objects[0].x += 3 }))
    expect(host.querySelector('[data-quick-layout-gutters]')).toBeNull()
  })
})

describe('拖分隔线手势', () => {
  it('拖动中：只有预览轮廓，不 commit、不发请求；松手一次请求 + 一次 commit', async () => {
    mount()
    const depth = s().past.length
    quickLayoutPlan.mockResolvedValue(okPlan())
    startGutterDrag(down(0, 0), gutter())
    // 往右 -20 mm：左列变窄
    fire('pointermove', -20 * PX_PER_MM(), 0)
    const pv = useQuickLayoutStore.getState().preview!
    expect(pv.p1[2]).toBeCloseTo(65)
    expect(pv.p2[0]).toBeCloseTo(78)
    expect(host.querySelectorAll('[data-quick-layout-preview]')).toHaveLength(2)
    expect(quickLayoutPlan).not.toHaveBeenCalled()
    // 图在跟手（live）时淡蓝框照样画（维护者 2026-11-20）
    act(() => useQuickLayoutStore.setState({ live: ['p1', 'p2'] }))
    expect(host.querySelectorAll('[data-quick-layout-preview]')).toHaveLength(2)
    expect(s().past.length).toBe(depth)
    fire('pointermove', -25 * PX_PER_MM(), 0)
    fire('pointerup', -25 * PX_PER_MM(), 0)
    // 松手后等方案期间轮廓留着
    expect(useQuickLayoutStore.getState().preview).not.toBeNull()
    await flush()
    expect(quickLayoutPlan).toHaveBeenCalledTimes(1)
    const req = quickLayoutPlan.mock.calls[0][0]
    expect(req.fix_spec).toBe(false)
    expect(req.columns).toBe(2)
    // 只改被拖的这一对：权重合计 2，左列占 (85-25)/170
    expect(req.ratios.cols[0]).toBeCloseTo((2 * 60) / 170)
    expect(req.ratios.cols[0] + req.ratios.cols[1]).toBeCloseTo(2)
    expect(req.ratios.rows).toEqual([1])
    expect(s().past.length).toBe(depth + 1)
    expect(formatMessage(s().past.at(-1)!.label)).toBe('调整排版比例')
    expect(useQuickLayoutStore.getState().preview).toBeNull()
    expect(useInteractionStore.getState().kind).toBe('none')
  })

  it('Esc（取消手势）：收掉轮廓、不发请求、不进历史', async () => {
    const depth = s().past.length
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', 20 * PX_PER_MM(), 0)
    expect(useQuickLayoutStore.getState().preview).not.toBeNull()
    act(() => void cancelActivePointerGesture())
    await flush()
    expect(useQuickLayoutStore.getState().preview).toBeNull()
    expect(quickLayoutPlan).not.toHaveBeenCalled()
    expect(s().past.length).toBe(depth)
  })

  it('pointercancel 与松手分开：同样不发请求', async () => {
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', 20 * PX_PER_MM(), 0)
    fire('pointercancel', 20 * PX_PER_MM(), 0)
    await flush()
    expect(useQuickLayoutStore.getState().preview).toBeNull()
    expect(quickLayoutPlan).not.toHaveBeenCalled()
  })

  it('点一下没拖动：不发请求', async () => {
    startGutterDrag(down(0, 0), gutter())
    fire('pointerup', 0, 0)
    await flush()
    expect(quickLayoutPlan).not.toHaveBeenCalled()
    expect(getTransform().zoom).toBe(2)
  })

  it('失败：轮廓收回、不 commit', async () => {
    const depth = s().past.length
    quickLayoutPlan.mockResolvedValue({ ok: false, code: 'not_converged' })
    startGutterDrag(down(0, 0), gutter())
    fire('pointermove', 20 * PX_PER_MM(), 0)
    fire('pointerup', 20 * PX_PER_MM(), 0)
    await flush()
    expect(s().past.length).toBe(depth)
    expect(useQuickLayoutStore.getState().preview).toBeNull()
    expect(s().doc.quickLayout?.colRatios).toEqual([1, 1])
  })
})
