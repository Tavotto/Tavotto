/**
 * 直线标注的端点：数据模型兜底、创建时的真实斜率、端点手柄、渲染跟随。
 *
 * 背景：直线形状此前根本没有 start/end 字段，永远画成包围盒水平中线，
 * 竖直位移被 resizeRect 丢弃（掰不斜），侧边手柄还会把 0.01mm 的包围盒撑到
 * minH=2mm，凭空多出一个与线对不上的外框。见
 * docs/audit/2026-08-17-ux-audit.md 缺陷 1。
 *
 * jsdom 说明：这里断言的是**数值与结构**——端点比例、包围盒、SVG 属性、
 * 手柄方向表。真实指针命中由 hitTest.test.tsx 与审计的 Playwright 背书。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import {
  emptyProject,
  lineEndpoints,
  type ArrowObject,
  type ShapeObject,
} from '@/types/document'
import { dirsFor } from '@/lib/geometry'
import { startDraw, startEndpointDrag } from './interactions'
import { ShapeView } from './ShapeView'

/** 旧文档里的直线：没有 start/end，包围盒水平中线 */
const legacyLine = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's1',
  type: 'shape',
  shape: 'line',
  x: 10,
  y: 20,
  w: 40,
  h: 0.01,
  strokePt: 1,
  color: '#111111',
  fill: null,
  ...over,
})

const arrow: ArrowObject = {
  id: 'a1',
  type: 'arrow',
  x: 0,
  y: 0,
  w: 30,
  h: 10,
  start: { rx: 0, ry: 1 },
  end: { rx: 1, ry: 0 },
  strokePt: 1,
  color: '#111111',
  head: 'end',
}

/* ------------------------------ 指针事件桩 ------------------------------- */

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

/** jsdom 没有 PointerEvent 构造器，用同名 MouseEvent —— 监听器按事件名派发 */
const fire = (type: 'pointermove' | 'pointerup', clientX: number, clientY: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))

/** mm → client px（zoom=1 / pan=0 / origin=0 时 clientToMm 的逆） */
const px = (mm: number) => mmToWorld(mm)

const objects = () => useDocumentStore.getState().doc.objects
const lastShape = () => objects().at(-1) as ShapeObject

/* ------------------------------ 渲染骨架 --------------------------------- */

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
  // 关掉吸附：本用例要的是拖拽坐标 → 端点的原样换算，不掺吸附修正
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: null })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_line')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const render = (node: React.ReactNode) =>
  act(() => {
    root.render(node)
  })

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* --------------------------------- 用例 ---------------------------------- */

describe('lineEndpoints 兜底', () => {
  it('旧文档没有 start/end：兜底成包围盒水平中线', () => {
    expect(lineEndpoints(legacyLine())).toEqual({
      start: { rx: 0, ry: 0.5 },
      end: { rx: 1, ry: 0.5 },
    })
  })

  it('写了端点就原样返回（含 0 —— 不能被 ?? 之外的假值判断吞掉）', () => {
    const l = legacyLine({ start: { rx: 0, ry: 0 }, end: { rx: 1, ry: 1 } })
    expect(lineEndpoints(l)).toEqual({ start: { rx: 0, ry: 0 }, end: { rx: 1, ry: 1 } })
  })

  it('箭头走同一个读取口（两者字段同构）', () => {
    expect(lineEndpoints(arrow)).toEqual({ start: { rx: 0, ry: 1 }, end: { rx: 1, ry: 0 } })
  })
})

describe('dirsFor', () => {
  it('直线不再给侧边缩放柄（它走端点手柄；minH 撑大包围盒的老毛病随之消失）', () => {
    expect(dirsFor(legacyLine())).toEqual([])
  })

  it('箭头照旧无缩放柄，文字仍是左右两个，有面积的形状八个', () => {
    expect(dirsFor(arrow)).toEqual([])
    expect(dirsFor(legacyLine({ shape: 'rect', h: 20 }))).toHaveLength(8)
  })
})

describe('拖拽创建直线', () => {
  it('斜着拖：真实斜率落进 start/end，包围盒贴合线段', () => {
    useUiStore.setState({ tool: 'line' })
    startDraw(down(px(10), px(20)), 'line')
    fire('pointermove', px(50), px(60))
    fire('pointerup', px(50), px(60))

    const line = lastShape()
    expect(line.shape).toBe('line')
    expect(line.x).toBeCloseTo(10, 6)
    expect(line.y).toBeCloseTo(20, 6)
    expect(line.w).toBeCloseTo(40, 6)
    expect(line.h).toBeCloseTo(40, 6)
    expect(line.start!.rx).toBeCloseTo(0, 6)
    expect(line.start!.ry).toBeCloseTo(0, 6)
    expect(line.end!.rx).toBeCloseTo(1, 6)
    expect(line.end!.ry).toBeCloseTo(1, 6)
  })

  it('从右下往左上拖：端点跟着拖动方向，不是永远左上→右下', () => {
    useUiStore.setState({ tool: 'line' })
    startDraw(down(px(50), px(60)), 'line')
    fire('pointermove', px(10), px(20))
    fire('pointerup', px(10), px(20))

    const line = lastShape()
    expect(line.start!.rx).toBeCloseTo(1, 6)
    expect(line.start!.ry).toBeCloseTo(1, 6)
    expect(line.end!.rx).toBeCloseTo(0, 6)
    expect(line.end!.ry).toBeCloseTo(0, 6)
  })

  it('水平拖：h 钳到 0.01（包围盒不能零厚度，比例坐标要除它），与箭头同规则', () => {
    useUiStore.setState({ tool: 'line' })
    startDraw(down(px(10), px(20)), 'line')
    fire('pointermove', px(50), px(20))
    fire('pointerup', px(50), px(20))

    const line = lastShape()
    expect(line.w).toBeCloseTo(40, 6)
    expect(line.h).toBeCloseTo(0.01, 6)
    expect(line.start!.ry).toBe(line.end!.ry)
  })

  it('只点一下不拖：默认宽度的水平线（DEFAULT_DRAW）', () => {
    useUiStore.setState({ tool: 'line' })
    startDraw(down(px(10), px(20)), 'line')
    fire('pointerup', px(10), px(20))

    const line = lastShape()
    expect(line.w).toBeCloseTo(30, 6)
    expect(line.h).toBeCloseTo(0.01, 6)
    const ends = lineEndpoints(line)
    expect(ends.end.rx - ends.start.rx).toBeCloseTo(1, 6)
    expect(ends.end.ry).toBeCloseTo(ends.start.ry, 6)
  })
})

describe('端点拖拽把直线掰斜', () => {
  it('旧文档的水平线也能掰（读取走兜底），包围盒重新贴合线段', () => {
    useDocumentStore.getState().commit(literal('加线'), (d) => {
      d.objects.push(legacyLine())
    })
    // 端点绝对位置：start(10, 20.005) → end(50, 20.005)；把 end 往下拖 20mm
    startEndpointDrag(down(0, 0), 's1', 'end')
    fire('pointermove', 0, px(20))
    fire('pointerup', 0, px(20))

    const line = objects()[0] as ShapeObject
    expect(line.w).toBeCloseTo(40, 6)
    expect(line.h).toBeCloseTo(20, 6)
    expect(line.start!.ry).toBeCloseTo(0, 6)
    expect(line.end!.rx).toBeCloseTo(1, 6)
    expect(line.end!.ry).toBeCloseTo(1, 6)
  })

  it('是一条可撤销的历史（标签认直线，不是箭头）', () => {
    useDocumentStore.getState().commit(literal('加线'), (d) => {
      d.objects.push(legacyLine())
    })
    startEndpointDrag(down(0, 0), 's1', 'end')
    fire('pointermove', 0, px(20))
    fire('pointerup', 0, px(20))

    expect(useDocumentStore.getState().undo()).toBe('调整直线端点')
    const line = objects()[0] as ShapeObject
    expect(line.h).toBeCloseTo(0.01, 6)
    expect(line.start).toBeUndefined()
  })

  it('锁定的直线不响应端点拖拽', () => {
    useDocumentStore.getState().commit(literal('加线'), (d) => {
      d.objects.push(legacyLine({ locked: true }))
    })
    startEndpointDrag(down(0, 0), 's1', 'end')
    fire('pointermove', 0, px(20))
    fire('pointerup', 0, px(20))

    expect((objects()[0] as ShapeObject).h).toBeCloseTo(0.01, 6)
  })
})

describe('ShapeView 按真实端点画线', () => {
  const lines = () => [...container.querySelectorAll('line')]
  const coords = (el: SVGLineElement) =>
    ['x1', 'y1', 'x2', 'y2'].map((a) => Number(el.getAttribute(a)))

  it('旧文档（无 start/end）仍是包围盒水平中线，渲染不炸', () => {
    render(<ShapeView obj={legacyLine({ w: 40, h: 10 })} hit="stroke" />)
    const w = mmToWorld(40)
    const h = mmToWorld(10)
    for (const el of lines()) expect(coords(el)).toEqual([0, h / 2, w, h / 2])
  })

  it('斜线：可见线与命中线都落在真实端点上', () => {
    render(
      <ShapeView
        obj={legacyLine({ w: 40, h: 30, start: { rx: 0, ry: 1 }, end: { rx: 1, ry: 0 } })}
        hit="stroke"
      />,
    )
    const w = mmToWorld(40)
    const h = mmToWorld(30)
    expect(lines()).toHaveLength(2)
    for (const el of lines()) expect(coords(el)).toEqual([0, h, w, 0])
    const hitLine = container.querySelector('[data-hit-line]') as SVGLineElement
    expect(hitLine.style.pointerEvents).toBe('stroke')
  })
})
