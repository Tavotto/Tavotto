/**
 * 画布上的渐变柄（ADR 0186 第二期）与实时圆角（ADR 0185 M3 第二批）：真的覆盖层组件、真的文档 store，
 * 指针事件从元素按下、在 window 上走（与 `interactions.trackPointer` 一致）。判据：文档里那个对象怎么变、
 * 一次拖动几条历史、什么时候不出现、选中的色标与属性栏共用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { pathToMm } from '@/lib/pathEdit'
import { useDocumentStore } from '@/store/documentStore'
import { useGradientEditStore } from '@/store/gradientEditStore'
import { editAnchors } from '@/store/pathActions'
import { usePathEditStore } from '@/store/pathEditStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type CanvasObject, type Gradient, type PathObject, type ShapeObject } from '@/types/document'
import { GradientHandles } from './GradientHandles'
import { LiveCorners } from './LiveCorners'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const px = (mm: number) => mmToWorld(mm)
const press = (el: Element, xMm: number, yMm: number, init: MouseEventInit = {}) =>
  el.dispatchEvent(new MouseEvent('pointerdown', { clientX: px(xMm), clientY: px(yMm), bubbles: true, cancelable: true, button: 0, ...init }))
const fire = (type: 'pointermove' | 'pointerup', xMm: number, yMm: number, init: MouseEventInit = {}) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: px(xMm), clientY: px(yMm), bubbles: true, ...init }))
const drag = async (el: Element, from: [number, number], to: [number, number], init: MouseEventInit = {}) => {
  await act(async () => press(el, ...from, init))
  await act(async () => fire('pointermove', ...to, init))
  await act(async () => fire('pointerup', ...to, init))
}

const LIN: Gradient = {
  kind: 'linear',
  stops: [
    { offset: 0, color: '#ff0000' },
    { offset: 0.5, color: '#00ff00' },
    { offset: 1, color: '#0000ff' },
  ],
  from: [0, 0.5],
  to: [1, 0.5],
}
const rect = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 'r',
  type: 'shape',
  shape: 'rect',
  x: 10,
  y: 20,
  w: 40,
  h: 20,
  strokePt: 1,
  color: '#000000',
  fill: '#ff0000',
  ...over,
})
const square = (over: Partial<PathObject> = {}): PathObject => ({
  id: 'p',
  type: 'path',
  x: 10,
  y: 10,
  w: 40,
  h: 40,
  strokePt: 1,
  color: '#000000',
  fill: '#cccccc',
  subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] }],
  ...over,
})

let root: Root
let host: HTMLDivElement
const t = { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }
const objects = () => useDocumentStore.getState().doc.objects
const past = () => useDocumentStore.getState().past.length

async function seed(items: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ghlc_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set(select)
  await act(async () =>
    root.render(
      <svg>
        <LiveCorners t={t} />
        <GradientHandles t={t} />
      </svg>,
    ),
  )
}

beforeEach(() => {
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  useUiStore.setState({ tool: 'select', snapEnabled: false })
  usePathEditStore.getState().exit()
  useSelectionStore.getState().clear()
  useGradientEditStore.setState({ objectId: null, target: 'fill', stop: 0 })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('渐变柄', () => {
  it('单选带渐变的对象就出现：两个端点 + 中间色标；端点代表位置 0 / 1 的色标（填它的颜色）', async () => {
    await seed([rect({ fillGradient: LIN })], ['r'])
    expect(host.querySelector('[data-gradient-handles="fill"]')).not.toBeNull()
    expect(host.querySelectorAll('[data-gradient-end]')).toHaveLength(2)
    expect(host.querySelectorAll('[data-gradient-handle-stop]')).toHaveLength(1)
    expect((host.querySelector('[data-gradient-end="a"]') as SVGElement).style.fill).toBe('rgb(255, 0, 0)')
  })

  it('拖终点：改 to（吸到框的 0 / ½ / 1）；一次拖动一条历史；色带用的降级色跟着首个色标', async () => {
    await seed([rect({ fillGradient: LIN })], ['r'])
    await drag(host.querySelector('[data-gradient-end="b"]')!, [50, 30], [30.1, 39.8])
    expect((objects()[0] as ShapeObject).fillGradient).toMatchObject({ to: [0.5, 1] })
    expect(past()).toBe(1)
  })

  it('⇧ 拖终点锁 45°', async () => {
    await seed([rect({ fillGradient: LIN })], ['r'])
    await drag(host.querySelector('[data-gradient-end="b"]')!, [50, 30], [41, 0], { shiftKey: true })
    const g = (objects()[0] as ShapeObject).fillGradient as Extract<Gradient, { kind: 'linear' }>
    const dx = (g.to[0] - g.from[0]) * 40
    const dy = (g.to[1] - g.from[1]) * 20
    expect(Math.abs(dy)).toBeGreaterThan(1) // 真锁到了斜线上，不是水平
    expect(Math.abs(Math.abs(dx) - Math.abs(dy))).toBeLessThan(1e-4)
  })

  it('拖中间色标：沿柄移动、不越过邻居；按下就选中它（与属性栏共用的编辑状态）', async () => {
    await seed([rect({ fillGradient: LIN })], ['r'])
    const stop = host.querySelector('[data-gradient-handle-stop="1"]')!
    await drag(stop, [30, 30], [20, 25])
    expect((objects()[0] as ShapeObject).fillGradient!.stops[1].offset).toBe(0.25)
    expect(useGradientEditStore.getState()).toMatchObject({ objectId: 'r', target: 'fill', stop: 1 })
    expect(host.querySelector('[data-gradient-handle-stop="1"]')!.getAttribute('data-selected')).toBe('true')
  })

  it('属性栏选了描边那一侧：柄换成描边渐变的；那一侧没有渐变就退回有的那一侧', async () => {
    await seed([rect({ fillGradient: LIN, strokeGradient: { ...LIN, to: [1, 0] } })], ['r'])
    await act(async () => useGradientEditStore.getState().set('r', { target: 'stroke', stop: 0 }))
    expect(host.querySelector('[data-gradient-handles="stroke"]')).not.toBeNull()
    await seed([rect({ fillGradient: LIN })], ['r'])
    await act(async () => useGradientEditStore.getState().set('r', { target: 'stroke', stop: 0 }))
    expect(host.querySelector('[data-gradient-handles="fill"]')).not.toBeNull()
  })

  it('径向：中心与半径柄；拖半径柄改 radius', async () => {
    await seed([rect({ fillGradient: { kind: 'radial', stops: LIN.stops, center: [0.5, 0.5], radius: 0.25 } })], ['r'])
    expect(host.querySelector('[data-gradient-kind="radial"]')).not.toBeNull()
    await drag(host.querySelector('[data-gradient-end="b"]')!, [40, 30], [46, 30])
    expect((objects()[0] as ShapeObject).fillGradient).toMatchObject({ radius: 0.4 })
  })

  it('不出现：多选、钢笔、编辑锚点、没有渐变', async () => {
    await seed([rect({ fillGradient: LIN }), rect({ id: 'r2', fillGradient: LIN })], ['r', 'r2'])
    expect(host.querySelector('[data-gradient-handles]')).toBeNull()
    await seed([square({ fillGradient: LIN })], ['p'])
    expect(host.querySelector('[data-gradient-handles]')).not.toBeNull()
    await act(async () => useUiStore.setState({ tool: 'pen' }))
    expect(host.querySelector('[data-gradient-handles]')).toBeNull()
    await act(async () => useUiStore.setState({ tool: 'select' }))
    await act(async () => editAnchors('p'))
    expect(host.querySelector('[data-gradient-handles]')).toBeNull()
    await seed([rect()], ['r'])
    expect(host.querySelector('[data-gradient-handles]')).toBeNull()
  })
})

describe('实时圆角', () => {
  it('矩形：四个圆点；往里拖改 cornerRadius（四个角一起），一次拖动一条历史', async () => {
    await seed([rect()], ['r'])
    const dots = host.querySelectorAll('[data-live-corner]')
    expect(dots).toHaveLength(4)
    await drag(host.querySelector('[data-live-corner="tl"]')!, [10.5, 20.5], [14, 24])
    expect((objects()[0] as ShapeObject).cornerRadius).toBe(4)
    expect(past()).toBe(1)
    await drag(host.querySelector('[data-live-corner="tl"]')!, [14, 24], [5, 15])
    expect('cornerRadius' in objects()[0]).toBe(false)
  })

  it('路径：没在编辑锚点时每个角都有圆点，拖一个就把全部角一起圆；结果仍是 path（每个角两个锚点）', async () => {
    await seed([square()], ['p'])
    expect(host.querySelectorAll('[data-live-corner]')).toHaveLength(4)
    await drag(host.querySelector('[data-live-corner="0:1"]')!, [49, 11], [45, 15])
    const p = objects()[0] as PathObject
    expect(p.type).toBe('path')
    expect(pathToMm(p)[0].nodes).toHaveLength(8)
    expect(past()).toBe(1)
  })

  it('编辑锚点时：只有选中的角有圆点，拖一个就圆选中的那几个；松手清掉锚点选择（下标变了）', async () => {
    await seed([square()], ['p'])
    await act(async () => {
      editAnchors('p')
      usePathEditStore.getState().setAnchors(['0:1', '0:2'])
    })
    expect([...host.querySelectorAll('[data-live-corner]')].map((e) => e.getAttribute('data-live-corner'))).toEqual(['0:1', '0:2'])
    await drag(host.querySelector('[data-live-corner="0:1"]')!, [49, 11], [45, 15])
    expect(pathToMm(objects()[0] as PathObject)[0].nodes).toHaveLength(6)
    expect(usePathEditStore.getState().anchors).toEqual([])
  })

  it('钢笔时、对象在屏幕上太小时不出现', async () => {
    await seed([square()], ['p'])
    await act(async () => useUiStore.setState({ tool: 'pen' }))
    expect(host.querySelector('[data-live-corners]')).toBeNull()
    await act(async () => useUiStore.setState({ tool: 'select' }))
    await act(async () => useViewportStore.setState({ zoom: 0.1 }))
    await act(async () =>
      root.render(
        <svg>
          <LiveCorners t={{ ...t, zoom: 0.1 }} />
        </svg>,
      ),
    )
    expect(host.querySelector('[data-live-corners]')).toBeNull()
  })
})

describe('拖柄 / 拖圆点时单选浮动栏让开（2026-11-15 用户拍板），松手就回来', () => {
  it.each([
    ['渐变柄', '[data-gradient-end="b"]', [50, 30], 'gradient'],
    ['圆角圆点', '[data-live-corner="tl"]', [10.5, 20.5], 'corner'],
  ] as const)('%s', async (_name, sel, from, kind) => {
    const { ContextBar } = await import('./context-bar/ContextBar')
    const { TooltipProvider } = await import('@/components/ui/Tooltip')
    const { useInteractionStore } = await import('@/store/interactionStore')
    await seed([rect({ fillGradient: LIN })], ['r'])
    const anchor = document.createElement('div')
    anchor.setAttribute('data-object-id', 'r')
    document.body.appendChild(anchor)
    const barHost = document.createElement('div')
    document.body.appendChild(barHost)
    const barRoot = createRoot(barHost)
    await act(async () =>
      barRoot.render(
        <TooltipProvider>
          <ContextBar />
        </TooltipProvider>,
      ),
    )
    const bar = () => document.querySelector('[data-context-bar]')
    expect(bar()).not.toBeNull()
    await act(async () => press(host.querySelector(sel)!, from[0], from[1]))
    await act(async () => fire('pointermove', from[0] + 3, from[1] + 3))
    expect(useInteractionStore.getState().kind).toBe(kind)
    expect(bar()).toBeNull()
    await act(async () => fire('pointerup', from[0] + 3, from[1] + 3))
    expect(useInteractionStore.getState().kind).toBe('none')
    expect(bar()).not.toBeNull()
    await act(async () => barRoot.unmount())
    barHost.remove()
    anchor.remove()
  })
})
