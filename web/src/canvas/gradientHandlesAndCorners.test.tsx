/**
 * 画布上的渐变柄（ADR 0186 第二期）与实时圆角（ADR 0185 M3 第二批）：真的覆盖层组件、真的文档 store，
 * 指针事件从元素按下、在 window 上走（与 `interactions.trackPointer` 一致）。判据：文档里那个对象怎么变、
 * 一次拖动几条历史、什么时候不出现、选中的色标与属性栏共用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { liveCornerRecord } from '@/lib/liveCornerRecord'
import { roundCorners } from '@/lib/liveCorners'
import { reflectObject, rotateObjectAbout, scaleObject } from '@/lib/transform'
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

  /* -------- 圆过的角还能再调（ADR 0185 M7 方案 B：烤好的几何 + 圆角记录，对不上就丢） -------- */
  const cornerDot = (key: string) => host.querySelector(`[data-live-corner="${key}"]`)!
  const dotAt = (key: string): [number, number] => {
    const c = cornerDot(key)
    return [Number(c.getAttribute('cx')) / px(1), Number(c.getAttribute('cy')) / px(1)]
  }
  const roundAll = async (r: number) => {
    // 角 (50, 10)：往里拖到离两边各 r 的圆心——圆心离顶点 r·√2，与拖动方向的投影就是半径
    const from = dotAt('0:1')
    await drag(cornerDot('0:1'), from, [50 - r, 10 + r])
  }

  it('圆过之后仍是四个角、圆点落在圆心上；接着拖改的是同一个角的半径，几何从尖角重新圆（不是圆上再圆）', async () => {
    await seed([square()], ['p'])
    await roundAll(5)
    const p1 = objects()[0] as PathObject
    expect(pathToMm(p1)[0].nodes).toHaveLength(8)
    expect(liveCornerRecord(p1, 0)?.radii.map((r) => Math.round(r * 1e6) / 1e6)).toEqual([5, 5, 5, 5])
    expect(host.querySelectorAll('[data-live-corner]')).toHaveLength(4)
    const at = dotAt('0:1')
    expect(at[0]).toBeCloseTo(45, 6)
    expect(at[1]).toBeCloseTo(15, 6)
    await roundAll(2)
    const p2 = objects()[0] as PathObject
    expect(pathToMm(p2)[0].nodes).toHaveLength(8)
    expect(liveCornerRecord(p2, 0)?.radii[1]).toBeCloseTo(2, 6)
    // 与一次直接圆成 2 的结果逐点相同
    const direct = roundCorners(pathToMm(square()), [0, 1, 2, 3].map((i) => ({ sp: 0, i })), 2)[0].nodes
    pathToMm(p2)[0].nodes.forEach((n, i) => {
      expect(n.p[0]).toBeCloseTo(direct[i].p[0], 6)
      expect(n.p[1]).toBeCloseTo(direct[i].p[1], 6)
    })
    // 拖回 0：角又尖了，记录没了
    await drag(cornerDot('0:1'), dotAt('0:1'), [55, 5])
    const p3 = objects()[0] as PathObject
    expect(pathToMm(p3)[0].nodes).toHaveLength(4)
    expect(p3.subpaths[0].liveCorners).toBeUndefined()
  })

  it('移动、等比缩放、旋转、镜像、复制、阵列之后记录还在；非等比缩放之后圆弧变成椭圆，对不上，提交时就丢', async () => {
    await seed([square()], ['p'])
    await roundAll(5)
    const ok = (o: PathObject) => liveCornerRecord(o, 0) != null
    const base = objects()[0] as PathObject
    expect(ok({ ...base, x: base.x + 30, y: base.y - 7 })).toBe(true)
    expect(ok(scaleObject(base, 1.7, 1.7, [0, 0], { strokes: false, text: 'size' }))).toBe(true)
    expect(liveCornerRecord(scaleObject(base, 2, 2, [0, 0], { strokes: false, text: 'size' }), 0)?.radii[0]).toBeCloseTo(10, 6)
    expect(ok(rotateObjectAbout(base, 33, [0, 0]))).toBe(true)
    expect(ok(reflectObject(base, 'h', [0, 0]) as PathObject)).toBe(true)
    expect(ok(reflectObject(rotateObjectAbout(base, 20, [5, 5]), 'v', [0, 0]) as PathObject)).toBe(true)
    expect(ok(structuredClone(base))).toBe(true)
    expect(ok(scaleObject(base, 2, 1, [0, 0], { strokes: false, text: 'size' }))).toBe(false)
    // 通过文档改：非等比缩放的提交把记录删掉；移动的提交不碰
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      d.objects[0].x += 5
    })
    expect((objects()[0] as PathObject).subpaths[0].liveCorners).toBeDefined()
    useDocumentStore.getState().commit(literal('拉宽'), (d) => {
      d.objects[0].w *= 2
    })
    expect((objects()[0] as PathObject).subpaths[0].liveCorners).toBeUndefined()
  })

  it('旧版本打开再保存：原样带回记录 → 照常能调；改了几何却原样留着旧记录（比如旧版的镜像只翻锚点）→ 新版识别出来并丢掉', async () => {
    await seed([square()], ['p'])
    await roundAll(5)
    const saved = JSON.parse(JSON.stringify(objects()[0])) as PathObject
    // 1) 旧版本不认识这个字段、原样带回：记录照常有效
    expect(liveCornerRecord(saved, 0)).not.toBeNull()
    // 2) 旧版本的镜像：只翻 nodes（旧代码不知道 liveCorners），记录原样留着
    const flipOld = (o: PathObject): PathObject => ({
      ...o,
      subpaths: o.subpaths.map((sp) => ({
        ...sp,
        nodes: sp.nodes.map((n) => ({ ...n, p: [1 - n.p[0], n.p[1]] as [number, number], ...(n.in ? { in: [1 - n.in[0], n.in[1]] as [number, number] } : {}), ...(n.out ? { out: [1 - n.out[0], n.out[1]] as [number, number] } : {}) })),
      })),
    })
    // 半径改了而几何没改：对不上
    const radiiOff = JSON.parse(JSON.stringify(saved)) as PathObject
    radiiOff.subpaths[0].liveCorners!.radii = radiiOff.subpaths[0].liveCorners!.radii.map((r, i) => (i === 0 ? r * 0.5 : r))
    expect(liveCornerRecord(radiiOff, 0)).toBeNull()
    // 不对称的四边形圆过之后让旧版镜像：几何翻了、记录没翻
    const quad: PathObject = square({ subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [0.7, 1] }, { p: [0, 0.6] }] }] })
    await seed([quad], ['p'])
    await drag(cornerDot('0:1'), dotAt('0:1'), [46, 14])
    const rounded = JSON.parse(JSON.stringify(objects()[0])) as PathObject
    expect(liveCornerRecord(rounded, 0)).not.toBeNull()
    // 旧版删了最后一个锚点（前面的都没动）、记录原样留着：锚点个数对不上
    const dropped = JSON.parse(JSON.stringify(rounded)) as PathObject
    dropped.subpaths[0].nodes = dropped.subpaths[0].nodes.slice(0, -1)
    expect(liveCornerRecord(dropped, 0)).toBeNull()
    // 旧版把闭合路径断开成开放的、记录原样留着
    const opened = JSON.parse(JSON.stringify(rounded)) as PathObject
    opened.subpaths[0].closed = false
    expect(liveCornerRecord(opened, 0)).toBeNull()
    // 新版的镜像连记录里的尖角一起翻：仍对得上
    expect(liveCornerRecord(reflectObject(rounded, 'h', [0, 0]) as PathObject, 0)).not.toBeNull()
    const stale = flipOld(rounded)
    expect(stale.subpaths[0].liveCorners).toBeDefined()
    expect(liveCornerRecord(stale, 0)).toBeNull()
    // 3) 新版打开这份文档：照着圆点拖时不会从旧尖角重新圆（那会把旧版做的镜像抹掉）——圆点按现在的几何算，提交时记录被删
    await seed([stale], ['p'])
    expect(host.querySelectorAll('[data-live-corner]').length).toBe(0) // 圆过的角都是弧，没有直线夹角可以圆
    useDocumentStore.getState().commit(literal('碰一下'), (d) => {
      d.objects[0].x += 1
    })
    expect((objects()[0] as PathObject).subpaths[0].liveCorners).toBeUndefined()
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
