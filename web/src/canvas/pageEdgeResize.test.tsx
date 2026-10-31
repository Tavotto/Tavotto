/**
 * 多图排版里拖页面四条边改画布大小（渐进式画布改版 · 裁决 5）。
 *
 * 判据打在看得见的差别上：页面尺寸、对象在屏幕上的位置、撤销条数、标注是否出现。
 * 数学（吸附 / 夹取 / 平移量）在 `lib/pageEdgeResize.test.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { cancelActivePointerGesture } from '@/store/gestureCoordinator'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToViewX, mmToViewY, getTransform, mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { settleMode } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { CanvasStage } from './CanvasStage'
import { useProfileStore } from '@/store/profileStore'
import type { ProfileRecord } from '@/lib/api'
import { startPageSizeFit } from '@/store/pageFit'
import { startPageEdgeResize } from './pageEdgeDrag'
import { dimSpan } from './pageEdgeReadout'

/** 会滚的数字换成探针：只关心传下去的 duration（0 = 即时换，undefined = 默认时长会滚） */
vi.mock('@sfinterface/numbers', () => ({
  Numbers: ({ value, duration }: { value: number; duration?: number }) => (
    <span data-numbers data-value={value} data-duration={duration === undefined ? 'roll' : String(duration)} />
  ),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (id: string, x: number, y: number): PanelObject =>
  ({
    id,
    type: 'panel',
    x,
    y,
    w: 80,
    h: 55,
    fileId: `${id}.pdf`,
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 55,
    overrides: [],
  }) as unknown as PanelObject

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {}, preventDefault() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup' | 'pointercancel', clientX: number, clientY: number, altKey = false) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, altKey, bubbles: true }))

const page = () => useDocumentStore.getState().doc.page
const obj = (id: string) => useDocumentStore.getState().doc.objects.find((o) => o.id === id)!
/** 对象左上角此刻在屏幕（视口）上的位置 */
const screenPos = (id: string) => {
  const t = getTransform()
  const o = obj(id)
  return { x: mmToViewX(o.x, t), y: mmToViewY(o.y, t) }
}

/** zoom 2：1 mm = 2 × BASE_PX_PER_MM 屏幕像素 */
const PX_PER_MM = () => mmToWorld(1) * useViewportStore.getState().zoom

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  useWorkspaceStore.getState().clear()
  useUiStore.setState({ tool: 'select', snapEnabled: false })
  useViewportStore.setState({ zoom: 2, panX: 100, panY: 80, originX: 0, originY: 0, viewW: 1200, viewH: 800 })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_page_edge')
  useDocumentStore.getState().commit(literal('摆图'), (d) => {
    d.page.w = 180
    d.page.h = 120
    d.objects.push(panel('a', 7, 18), panel('b', 92, 18))
    d.guides.push({ axis: 'x', pos: 90 })
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  cancelActivePointerGesture()
  act(() => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
})

const mount = () =>
  act(() => {
    root.render(
      <TooltipProvider>
        <CanvasStage />
      </TooltipProvider>,
    )
  })
// jsdom 里舞台量不到尺寸（ResizeObserver 是空的，挂载后 viewW/viewH 回 0）：要看尺寸线的数字位置时补上可视区
const giveView = async () => {
  act(() => useViewportStore.setState({ viewW: 4000, viewH: 3000 }))
  // 舞台有了尺寸会起一次「适应画布」的取景补间（真实应用里也是）：等它走完再把视口钉回测试的基准，免得拖动中 px↔mm 漂移
  await act(async () => {
    await new Promise((r) => setTimeout(r, 600))
  })
  act(() => useViewportStore.setState({ zoom: 2, panX: 100, panY: 80 }))
}

describe('拖页面边（手势）', () => {
  it('拖右边改宽：对象不动；松手只记一步撤销，撤销回到原宽', () => {
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', 20 * PX_PER_MM(), 0) // +20 mm → 200
    expect(page().w).toBe(200)
    expect(obj('a').x).toBe(7)
    fire('pointermove', 30 * PX_PER_MM(), 0)
    fire('pointerup', 30 * PX_PER_MM(), 0)
    expect(page().w).toBe(210)
    expect(useInteractionStore.getState().kind).toBe('none')
    act(() => void useDocumentStore.getState().undo())
    expect(page().w).toBe(180)
    expect(page().h).toBe(120)
  })

  it('拖左边：宽度变，页面坐标反向补偿，对象在屏幕上的位置不变', () => {
    const before = { a: screenPos('a'), b: screenPos('b') }
    startPageEdgeResize(down(0, 0), 'l')
    fire('pointermove', -30 * PX_PER_MM(), 0) // 往左拖 30 mm → 宽 210
    expect(page().w).toBe(210)
    expect(obj('a').x).toBe(37)
    expect(obj('b').x).toBe(122)
    expect(useDocumentStore.getState().doc.guides[0].pos).toBe(120)
    // 视口平移跟着补偿：屏幕上没动
    expect(screenPos('a').x).toBeCloseTo(before.a.x, 6)
    expect(screenPos('b').x).toBeCloseTo(before.b.x, 6)
    expect(screenPos('a').y).toBeCloseTo(before.a.y, 6)
    fire('pointerup', -30 * PX_PER_MM(), 0)
    expect(screenPos('a').x).toBeCloseTo(before.a.x, 6)
  })

  it('拖上边同理（y 轴）', () => {
    const before = screenPos('a')
    startPageEdgeResize(down(0, 0), 't')
    fire('pointermove', 0, -10 * PX_PER_MM())
    expect(page().h).toBe(130)
    expect(obj('a').y).toBe(28)
    expect(screenPos('a').y).toBeCloseTo(before.y, 6)
    fire('pointerup', 0, -10 * PX_PER_MM())
  })

  it('宽度吸栏宽（±2 mm），标注上写栏宽名；⌥ 不吸', () => {
    startPageEdgeResize(down(0, 0), 'r')
    const dx = (mm: number) => mm * PX_PER_MM()
    fire('pointermove', dx(-28.5), 0) // 151.5 → 150（内置默认规范的双栏宽）
    expect(page().w).toBe(150)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeTruthy()
    fire('pointermove', dx(-28.5), 0, true)
    expect(page().w).toBe(151.5)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeNull()
    fire('pointerup', dx(-28.5), 0)
  })

  it('最小 30 mm，允许比图小（图超出页面不拦）', () => {
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', -500 * PX_PER_MM(), 0)
    expect(page().w).toBe(30)
    fire('pointerup', -500 * PX_PER_MM(), 0)
    expect(obj('b').x).toBe(92)
  })

  it('Esc（取消手势）回到原值，视口也回到原处，不留历史', () => {
    const pan = { x: useViewportStore.getState().panX, y: useViewportStore.getState().panY }
    startPageEdgeResize(down(0, 0), 'l')
    fire('pointermove', -30 * PX_PER_MM(), 0)
    expect(page().w).toBe(210)
    expect(cancelActivePointerGesture()).toBe(true)
    expect(page()).toMatchObject({ w: 180, h: 120 })
    expect(obj('a').x).toBe(7)
    expect(useViewportStore.getState().panX).toBe(pan.x)
    expect(useDocumentStore.getState().txn).toBeNull()
    expect(useInteractionStore.getState().kind).toBe('none')
  })

  it('没拖动（点一下）不改文档、不进历史', () => {
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointerup', 0, 0)
    expect(page().w).toBe(180)
    expect(useDocumentStore.getState().txn).toBeNull()
  })

  it('拖动期间不触发「页面尺寸变了就补间取景」', () => {
    const fit = vi.spyOn(useViewportStore.getState(), 'fitAnimated')
    const stop = startPageSizeFit()
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', 20 * PX_PER_MM(), 0)
    expect(fit).not.toHaveBeenCalled()
    fire('pointerup', 20 * PX_PER_MM(), 0)
    stop()
    fit.mockRestore()
  })
})

describe('适应画布（fitted）', () => {
  it('fitted 下拖右边：松手后退出适应模式，之后窗口 / 侧栏改变舞台尺寸页面不再被按旧取景挪走', () => {
    useViewportStore.getState().setViewRect({ left: 0, top: 0, width: 1200, height: 800 })
    useViewportStore.getState().fit(180, 120)
    expect(useViewportStore.getState().fitted).toBe(true)
    const before = screenPos('a')
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', 20 * PX_PER_MM(), 0)
    fire('pointerup', 20 * PX_PER_MM(), 0)
    expect(page().w).toBe(200)
    expect(useViewportStore.getState().fitted).toBe(false)
    expect(screenPos('a').x).toBeCloseTo(before.x, 6)
    // 侧栏开合 = 舞台尺寸变了：不在适应模式里就一个字不碰
    useViewportStore.getState().setViewRect({ left: 0, top: 0, width: 900, height: 800 })
    expect(screenPos('a').x).toBeCloseTo(before.x, 6)
    expect(screenPos('a').y).toBeCloseTo(before.y, 6)
  })

  it('Esc 取消：回到原值，适应模式一并恢复', () => {
    useViewportStore.getState().setViewRect({ left: 0, top: 0, width: 1200, height: 800 })
    useViewportStore.getState().fit(180, 120)
    const pan = { x: useViewportStore.getState().panX, y: useViewportStore.getState().panY }
    startPageEdgeResize(down(0, 0), 'l')
    fire('pointermove', -30 * PX_PER_MM(), 0)
    expect(useViewportStore.getState().fitted).toBe(false)
    expect(cancelActivePointerGesture()).toBe(true)
    expect(page().w).toBe(180)
    expect(useViewportStore.getState().fitted).toBe(true)
    expect(useViewportStore.getState().panX).toBe(pan.x)
  })
})

describe('取景补间进行中按下拖边', () => {
  it('拖几帧 → Esc：zoom / pan / fitted / lastFit 与按下那一刻定住后的视图一致，随后 resize 页面不跳', () => {
    const vp = () => useViewportStore.getState()
    vp().setViewRect({ left: 0, top: 0, width: 1200, height: 800 })
    // 探针：fit(180,120) 的终点视图
    vp().fit(180, 120)
    const target = { zoom: vp().zoom, panX: vp().panX, panY: vp().panY }
    // 回到起点后起一段补间（还没走一帧）：按下时补间在飞
    useViewportStore.setState({ zoom: 1, panX: 0, panY: 0 })
    vp().fitAnimated(180, 120)
    expect(vp().tweening).toBe(true)
    startPageEdgeResize(down(0, 0), 'l')
    // 按下那一刻视图已定在补间终点
    expect(vp().tweening).toBe(false)
    expect(vp().zoom).toBeCloseTo(target.zoom, 9)
    fire('pointermove', -30 * PX_PER_MM(), 0)
    fire('pointermove', -40 * PX_PER_MM(), 0)
    expect(cancelActivePointerGesture()).toBe(true)
    expect(page().w).toBe(180)
    expect(vp()).toMatchObject({ fitted: true, tweening: false })
    expect(vp().zoom).toBeCloseTo(target.zoom, 9)
    expect(vp().panX).toBeCloseTo(target.panX, 9)
    expect(vp().panY).toBeCloseTo(target.panY, 9)
    // lastFit 也回来了：舞台尺寸一变，按 180×120 重算，与直接 fit(180,120) 同一结果
    vp().setViewRect({ left: 0, top: 0, width: 900, height: 800 })
    const after = { zoom: vp().zoom, panX: vp().panX, panY: vp().panY }
    vp().fit(180, 120)
    expect(after.zoom).toBeCloseTo(vp().zoom, 9)
    expect(after.panX).toBeCloseTo(vp().panX, 9)
    expect(after.panY).toBeCloseTo(vp().panY, 9)
  })
})

describe('栏宽读自文档绑定的规范（目录没就绪不吸附）', () => {
  const record = (id: string, single: number, double: number): ProfileRecord => ({
    id,
    kind: 'spec',
    schema_version: 1,
    revision: 1,
    display_name: id,
    name_key: '',
    version: '1',
    created_at: 0,
    updated_at: 0,
    built_in: false,
    read_only: false,
    is_default: false,
    derived_from: '',
    warnings: [],
    data: { ...(useProfileStore.getState().specs[0]?.data ?? {}), widths_mm: { single, double, tolerance_mm: 0.5, allow_custom: true } },
  })
  const bindFollow = () =>
    useDocumentStore.getState().commit(literal('绑规范'), (d) => {
      d.profile = { id: 'mine', follow: true }
    })

  it('目录没加载 + 跟随自定义规范：拖到内置栏宽附近不吸附；目录加载后吸到自定义规范的栏宽', () => {
    bindFollow()
    useProfileStore.setState({ loaded: false, load: (async () => {}) as never })
    const dx = (mm: number) => mm * PX_PER_MM()
    startPageEdgeResize(down(0, 0), 'r')
    fire('pointermove', dx(-29), 0) // 151：内置默认双栏 150 附近
    expect(page().w).toBe(151)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeNull()
    // 目录到了：同一次手势下一帧起按自定义规范（双栏 140）吸
    useProfileStore.setState({
      loaded: true,
      specs: [...useProfileStore.getState().specs, record('mine', 70, 140)],
    })
    fire('pointermove', dx(-39), 0) // 141 → 140
    expect(page().w).toBe(140)
    expect(useInteractionStore.getState().pageEdge?.snapLabel).toBeTruthy()
    fire('pointerup', dx(-39), 0)
  })
})

describe('画布上的拖边命中区与标注', () => {
  it('多图排版里四条边各有一个命中区，命中区屏幕宽度恒定、不随倍率变', async () => {
    await mount()
    const edges = [...container.querySelectorAll('[data-page-edge]')]
    expect(edges.map((e) => e.getAttribute('data-page-edge')).sort()).toEqual(['b', 'l', 'r', 't'])
    const widthAt = (z: number) => {
      act(() => useViewportStore.setState({ zoom: z }))
      return (container.querySelector('[data-page-edge="l"]') as HTMLElement).style.width
    }
    expect(widthAt(1)).toBe(widthAt(4))
  })

  it('拖动中出现尺寸标注，松手后消失；标注不进文档', async () => {
    await mount()
    await giveView()
    expect(container.querySelector('[data-page-dims]')).toBeNull()
    const docBefore = JSON.stringify(useDocumentStore.getState().doc.objects)
    act(() => startPageEdgeResize(down(0, 0), 'r'))
    act(() => fire('pointermove', 20 * PX_PER_MM(), 0))
    const dims = container.querySelector('[data-page-dims]')
    expect(dims).not.toBeNull()
    expect(dims!.querySelector('[data-dim-readout="width"] [data-numbers]')?.getAttribute('data-value')).toBe('200')
    expect(dims!.querySelector('[data-dim-readout="height"] [data-numbers]')?.getAttribute('data-value')).toBe('120') // 两条线各写各的数
    expect(dims!.querySelectorAll('svg [data-dim]')).toHaveLength(2) // 画布内的两条蓝色箭头尺寸线
    act(() => fire('pointerup', 20 * PX_PER_MM(), 0))
    expect(container.querySelector('[data-page-dims]')).toBeNull()
    expect(JSON.stringify(useDocumentStore.getState().doc.objects)).toBe(docBefore)
  })

  it('标注读数：逐 mm 拖动即时换（duration 0），吸上栏宽 / ⌥ 跳变时才滚', async () => {
    await mount()
    await giveView()

    const dur = () =>
      [...container.querySelectorAll('[data-dim-readout="width"] [data-numbers]')].map((n) => n.getAttribute('data-duration'))
    const mm = (v: number) => v * PX_PER_MM()
    // 有舞台尺寸时页面一变视口会被重新取景（jsdom 里没有真实布局）：每一帧后把视口钉回基准，px ↔ mm 才对得上
    const step = (...a: Parameters<typeof fire>) => {
      fire(...a)
      useViewportStore.setState({ zoom: 2, panX: 100, panY: 80 })
    }
    act(() => startPageEdgeResize(down(0, 0), 'r'))
    act(() => step('pointermove', mm(-10), 0)) // 170：连续
    expect(dur()).toEqual(['0'])
    act(() => step('pointermove', mm(-11), 0)) // 169：连续
    expect(dur()).toEqual(['0'])
    act(() => step('pointermove', mm(-29), 0)) // 151 → 吸到 150：跳变
    expect(page().w).toBe(150)
    expect(dur()).toEqual(['roll'])
    act(() => step('pointermove', mm(-29.2), 0)) // 仍吸着：回到连续
    expect(dur()).toEqual(['0'])
    act(() => step('pointermove', mm(-29.2), 0, true)) // ⌥ 按下：跳变
    expect(dur()).toEqual(['roll'])
    act(() => step('pointerup', mm(-29.2), 0))
  })

  it('数字无框无底色，宽高两条尺寸线各有数字，数字在线的页面一侧、离线留间隙', async () => {
    await mount()
    await giveView()
    act(() => startPageEdgeResize(down(0, 0), 'b'))
    act(() => fire('pointermove', 0, 10 * PX_PER_MM()))
    expect(container.querySelector('[data-dim-chip]')).toBeNull()
    const readouts = [...container.querySelectorAll<HTMLElement>('[data-dim-readout]')]
    expect(readouts.map((r) => r.dataset.dimReadout).sort()).toEqual(['height', 'width'])
    for (const r of readouts) {
      expect(r.className).not.toMatch(/\bborder\b|\bbg-/)
      expect(r.className).toContain('type-number')
      expect(r.closest('[data-ruler-dim]')).toBeNull() // 不在标尺里
    }
    const v = useViewportStore.getState()
    const w = readouts.find((r) => r.dataset.dimReadout === 'width')!
    const h = readouts.find((r) => r.dataset.dimReadout === 'height')!
    // 宽度数字在横线之下（页面一侧）：横线 y = panY - 26，数字顶 = y + 3
    expect(parseFloat(w.style.top)).toBeCloseTo(Math.max(14, v.panY - 26) + 3, 3)
    expect(parseFloat(w.style.top)).toBeLessThan(v.panY)
    // 高度数字转 -90°，在竖线之右（页面一侧）
    expect(h.style.transform).toContain('rotate(-90deg)')
    expect(parseFloat(h.style.left)).toBeGreaterThan(Math.max(14, v.panX - 26))
    expect(parseFloat(h.style.left)).toBeLessThan(v.panX)
    act(() => fire('pointerup', 0, 10 * PX_PER_MM()))
  })

  it('数字居中于尺寸线可见段中点，逐帧只随页面边走', async () => {
    act(() => useViewportStore.setState({ zoom: 1 }))
    await mount()
    await giveView()
    const mm = (v: number) => v * PX_PER_MM()
    act(() => startPageEdgeResize(down(0, 0), 'r'))
    for (const dx of [1, 1.3, 7, 20, 31.4]) {
      act(() => fire('pointermove', mm(dx), 0))
      const vp = useViewportStore.getState()
      const left = vp.panX
      const right = vp.panX + page().w * PX_PER_MM()
      const w = container.querySelector<HTMLElement>('[data-dim-readout="width"]')!
      expect(parseFloat(w.style.left)).toBeCloseTo(dimSpan(left, right, 4000)!.center, 3)
    }
    act(() => fire('pointerup', mm(31.4), 0))
  })

  it('图内编辑子状态（排版里双击某张图进图内）没有拖边，退出后回来', async () => {
    await mount()
    expect(container.querySelectorAll('[data-page-edge]')).toHaveLength(4)
    act(() => useUiStore.getState().setElementPanel('a'))
    expect(container.querySelector('[data-page-edge]')).toBeNull()
    act(() => useUiStore.getState().setElementPanel(null))
    expect(container.querySelectorAll('[data-page-edge]')).toHaveLength(4)
  })

  it('单图编辑（fast_edit）没有拖边', async () => {
    // 模式由画布图数推出（只剩一张图 = 单图编辑）：删掉第二张再对一次模式
    useDocumentStore.getState().commit(literal('x'), (d) => {
      d.objects = d.objects.filter((o) => o.id !== 'b')
    })
    act(() => settleMode())
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    await mount()
    expect(container.querySelector('[data-page-edge]')).toBeNull()
  })
})
