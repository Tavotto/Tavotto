/**
 * 画布原生形状（椭圆 / 三角 / 菱形 / 多边形 / 大括号）的真实轮廓。
 *
 * 修之前：这些形状的选中框是包围盒矩形，命中区也是整个包围盒。一个三角形
 * 选中时显示成方框（用户认不出选的是哪一个），而点它三个空白角里的任何一个
 * 都会选中它、还挡住底下的面板。
 *
 * 现在：`shapeOutline` 是**唯一一份**轮廓几何，ShapeView 的显示、命中层、
 * 覆盖层的选中描示三处共用——三处一致才不会出现「看着没选中却点得中」。
 * 矩形不在此列（它的包围盒就是它自己），直线走端点那套。
 *
 * jsdom 说明：jsdom 不做 CSS 命中测试，所以这里断言结构与数值（渲染出来的
 * 是不是 polygon/ellipse/path、pointerEvents 取值、外层 div 有没有让位、
 * 覆盖层描示的顶点坐标）。真实浏览器里的命中由 e2e 背书。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { PATH_HIT_SHAPES, polygonPoints, shapeOutline } from '@/lib/shapeGeometry'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ShapeKind, type ShapeObject } from '@/types/document'
import { ObjectView } from './ObjectView'
import { OverlaySvg } from './OverlaySvg'

const shape = (kind: ShapeKind, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's1',
  type: 'shape',
  shape: kind,
  x: 10,
  y: 20,
  w: 40,
  h: 30,
  strokePt: 1,
  color: '#111111',
  fill: null,
  ...over,
})

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  useViewportStore.setState({
    zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700, spaceDown: false,
  })
  useUiStore.setState({ tool: 'select', editingTextId: null, cropTargetId: null,
                        elementPanelId: null })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const render = (node: React.ReactNode) => act(() => root.render(node))
const hitShape = () => container.querySelector('[data-hit-shape]') as SVGElement | null
const objectDiv = () => container.querySelector('[data-object-id]') as HTMLDivElement

/* --------------------------- 轮廓几何本身 --------------------------- */

describe('shapeOutline：三处共用的唯一一份轮廓', () => {
  it('三角形是三个顶点（上中 / 右下 / 左下）', () => {
    const o = shapeOutline('triangle', 40, 30, 0)
    expect(o).toEqual({ kind: 'poly', points: [[20, 0], [40, 30], [0, 30]] })
  })

  it('菱形是四个边中点', () => {
    const o = shapeOutline('diamond', 40, 30, 0)
    expect(o).toEqual({ kind: 'poly', points: [[20, 0], [40, 15], [20, 30], [0, 15]] })
  })

  it('多边形复用 polygonPoints（与后端 _polygon_points 同一公式）', () => {
    const o = shapeOutline('polygon', 40, 30, 1, 5)
    expect(o).toEqual({ kind: 'poly', points: polygonPoints(5, 40, 30, 1) })
  })

  it('椭圆给圆心与半径，大括号给贝塞尔 d 串', () => {
    expect(shapeOutline('ellipse', 40, 30, 1)).toMatchObject({ kind: 'ellipse', cx: 20, cy: 15 })
    expect(shapeOutline('brace', 40, 30, 1)).toMatchObject({ kind: 'path' })
  })

  it('矩形与直线不给轮廓：矩形的包围盒就是它自己，直线走端点那套', () => {
    expect(shapeOutline('rect', 40, 30, 1)).toBeNull()
    expect(shapeOutline('line', 40, 30, 1)).toBeNull()
    expect(PATH_HIT_SHAPES.has('rect')).toBe(false)
    expect(PATH_HIT_SHAPES.has('line')).toBe(false)
  })
})

/* ------------------------------ 命中层 ------------------------------ */

describe('ShapeView：沿真实轮廓的命中层', () => {
  for (const kind of ['ellipse', 'triangle', 'diamond', 'polygon', 'brace'] as const) {
    it(`${kind}：出命中层，外层包围盒 div 让位`, () => {
      render(<ObjectView obj={shape(kind)} />)
      expect(hitShape(), `${kind} 应当有沿轮廓的命中层`).not.toBeNull()
      expect(objectDiv().style.pointerEvents).toBe('none')
    })
  }

  it('矩形照旧由外层 div 承担命中（不为了统一改掉它）', () => {
    render(<ObjectView obj={shape('rect')} />)
    expect(hitShape()).toBeNull()
    expect(objectDiv().style.pointerEvents).toBe('')
  })

  it('有填充 → 整块可点；空心 → 只在描边附近可点', () => {
    render(<ObjectView obj={shape('triangle', { fill: '#dddddd' })} />)
    expect(hitShape()!.style.pointerEvents).toBe('all')
    render(<ObjectView obj={shape('triangle')} />)
    expect(hitShape()!.style.pointerEvents).toBe('stroke')
  })

  it('大括号永远只描边（它本来就没有填充语义）', () => {
    render(<ObjectView obj={shape('brace', { fill: '#dddddd' })} />)
    expect(hitShape()!.style.pointerEvents).toBe('stroke')
  })

  it('锁定 / 绘制工具 / 空格平移时不出命中层（锁住的不得重新可点选）', () => {
    render(<ObjectView obj={shape('triangle', { locked: true })} />)
    expect(hitShape()).toBeNull()
    render(<ObjectView obj={shape('triangle')} />)
    expect(hitShape()).not.toBeNull()
    useUiStore.setState({ tool: 'rect' })
    render(<ObjectView obj={shape('triangle')} />)
    expect(hitShape()).toBeNull()
  })
})

/* ---------------------------- 覆盖层描示 ---------------------------- */

describe('OverlaySvg：选中沿真实轮廓描示', () => {
  const select = async (obj: ShapeObject) => {
    await useDocumentStore.getState().switchDocument(emptyProject(), `d_shape_${obj.shape}`)
    useDocumentStore.getState().commit(literal('加形状'), (d) => {
      d.objects.push(obj)
    })
    useSelectionStore.getState().set([obj.id])
    render(<OverlaySvg />)
  }

  it('三角形：画一个 polygon，不是矩形选择框', async () => {
    await select(shape('triangle'))
    const poly = container.querySelector('polygon')
    expect(poly).not.toBeNull()
    // 内容 40×30mm，面板在 (10,20)：轮廓算在局部坐标里，整组 translate 到左上角
    const g = poly!.parentElement as unknown as SVGGElement
    expect(g.getAttribute('transform')).toContain(`translate(${mmToWorld(10)},${mmToWorld(20)})`)
    const pts = poly!.getAttribute('points')!.split(' ').map((s) => s.split(',').map(Number))
    expect(pts.length).toBe(3)
    expect(pts[0][0]).toBeCloseTo(mmToWorld(40) / 2, 6)   // 顶点在上边中点
  })

  it('椭圆：画 ellipse；菱形 / 多边形：画 polygon；大括号：画 path', async () => {
    await select(shape('ellipse'))
    expect(container.querySelector('ellipse')).not.toBeNull()
    await select(shape('diamond'))
    expect(container.querySelector('polygon')!.getAttribute('points')!.split(' ').length).toBe(4)
    await select(shape('polygon', { sides: 6 }))
    expect(container.querySelector('polygon')!.getAttribute('points')!.split(' ').length).toBe(6)
    await select(shape('brace'))
    expect(container.querySelector('path[d]')).not.toBeNull()
  })

  it('矩形与文字仍是矩形选择框（回归对照）', async () => {
    await select(shape('rect'))
    expect(container.querySelector('polygon')).toBeNull()
    expect(container.querySelector('ellipse')).toBeNull()
    expect(container.querySelectorAll('rect').length).toBeGreaterThan(0)
  })

  it('形状带旋转时，轮廓与手柄绕同一个中心转过去', async () => {
    await select(shape('triangle', { rotationDeg: 30 }))
    const g = container.querySelector('polygon')!.parentElement as unknown as SVGGElement
    expect(g.getAttribute('transform')).toMatch(/^rotate\(30 /)
  })
})
