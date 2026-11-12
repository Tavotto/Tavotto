/**
 * 导出预览里的箭头 / 形状（QA-WIN-15 / 39 / 47 / 52）：按对象自己的端点、填色、线型、层级画，
 * 与画布 `ArrowView` / `ShapeView` 取同一套几何函数；缩略图（不传 `exportedShape`）一字不变。
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { CanvasThumb } from '@/components/CanvasThumb'
import { arrowGeometry, dashArray } from '@/lib/shapeGeometry'
import { buildPreset } from '@/lib/presets'
import { MM_PER_PT } from '@/lib/units'
import type { ArrowObject, CanvasObject, ShapeObject, TextObject } from '@/types/document'

const PAGE = { w: 150, h: 100 }
const C = { x: 75, y: 50 }
const asShape = (o: unknown) => {
  const t = (o as CanvasObject).type
  return t === 'arrow' || t === 'shape' ? (o as ArrowObject | ShapeObject) : null
}
const asText = (o: unknown) => ((o as CanvasObject).type === 'text' ? (o as TextObject) : null)

function draw(objects: CanvasObject[], on = true): Element {
  const html = renderToStaticMarkup(
    <CanvasThumb
      page={PAGE}
      objects={objects}
      flush
      exportedShape={on ? asShape : undefined}
      exportedText={asText}
    />,
  )
  const host = document.createElement('div')
  host.innerHTML = html
  return host.firstElementChild!
}
const shapes = (svg: Element) => [...svg.querySelectorAll('[data-thumb-shape]')]
/** 对象的位置在 `translate(x y)` 内层；取其内的第一个图元 */
const prim = (g: Element, sel: string) => g.querySelector(sel) as SVGElement
const n = (el: Element, a: string) => Number(el.getAttribute(a))

describe('exportedShape：箭头', () => {
  it('QA-WIN-15 反向水平箭头：端点按对象的 start/end 走，不是包围盒对角线；箭头帽在左端', () => {
    const [top, bottom] = buildPreset('reversible', C) as ArrowObject[]
    const svg = draw([top, bottom])
    const [gTop, gBot] = shapes(svg)
    for (const [g, o] of [[gTop, top], [gBot, bottom]] as const) {
      const sw = o.strokePt * MM_PER_PT
      const geo = arrowGeometry(o, o.w, o.h, sw)
      const line = prim(g, 'line')
      // 与画布同源函数的结果逐点相等
      expect(n(line, 'x1')).toBeCloseTo(geo.p1.x, 9)
      expect(n(line, 'y1')).toBeCloseTo(geo.p1.y, 9)
      expect(n(line, 'x2')).toBeCloseTo(geo.p2.x, 9)
      expect(n(line, 'y2')).toBeCloseTo(geo.p2.y, 9)
      // 水平：y 不变（旧画法是对角线 y1≠y2）
      expect(n(line, 'y1')).toBe(n(line, 'y2'))
    }
    // 下面那支向左：线从右（x1>x2），帽的尖在 x=0
    const line = prim(gBot, 'line')
    expect(n(line, 'x1')).toBeGreaterThan(n(line, 'x2'))
    const poly = prim(gBot, 'polygon')
    expect(poly.getAttribute('points')!.split(' ')[0].split(',')[0]).toBe('0')
    expect(poly.getAttribute('fill')).toBe(bottom.color)
  })

  it('QA-WIN-15 左下→右上的斜向箭头：斜率向上（y2<y1），不是 `\\`', () => {
    const a = buildPreset('callout', C)[0] as ArrowObject // start (0,1) → end (1,0)
    a.headEnd = 'open'
    const g = shapes(draw([a]))[0]
    const line = prim(g, 'line')
    expect(n(line, 'y2')).toBeLessThan(n(line, 'y1'))
    expect(n(line, 'x2')).toBeGreaterThan(n(line, 'x1'))
    expect(prim(g, 'path').getAttribute('d')).toContain('L')
  })

  it('线型：dashed 的箭头线带 stroke-dasharray，帽不带', () => {
    const a = buildPreset('crystal', C)[0] as ArrowObject
    a.dash = 'dashed'
    const g = shapes(draw([a]))[0]
    const sw = a.strokePt * MM_PER_PT
    expect(prim(g, 'line').getAttribute('stroke-dasharray')).toBe(dashArray('dashed', sw))
    expect(prim(g, 'polygon').getAttribute('stroke-dasharray')).toBeNull()
  })
})

describe('exportedShape：形状', () => {
  const rect = (over: Partial<ShapeObject>): ShapeObject => ({
    ...(buildPreset('magnifier', C)[2] as ShapeObject),
    ...over,
  })

  it('QA-WIN-47 实心形状：填色 / 描边色 / 线宽取对象自己的，按对象顺序画（后者在上）', () => {
    const blue = rect({ id: 'r', fill: '#0000ff', color: '#000080', strokePt: 2 })
    const black = rect({ id: 'e', shape: 'ellipse', fill: '#000000', color: '#000000', x: blue.x + 5, y: blue.y + 5 })
    const g1 = shapes(draw([black, blue]))
    // 数组顺序 = 层级：先 black 后 blue → DOM 里 blue 在后（压在上面）
    expect(g1[0].getAttribute('data-thumb-shape')).toBe('ellipse')
    expect(g1[1].getAttribute('data-thumb-shape')).toBe('rect')
    const r = prim(g1[1], 'rect')
    expect(r.getAttribute('fill')).toBe('#0000ff')
    expect(r.getAttribute('stroke')).toBe('#000080')
    expect(n(r, 'stroke-width')).toBeCloseTo(2 * MM_PER_PT, 9)
    expect(prim(g1[0], 'ellipse').getAttribute('fill')).toBe('#000000')
    // 反向：椭圆置顶 → DOM 顺序跟着变
    const g2 = shapes(draw([blue, black]))
    expect(g2[1].getAttribute('data-thumb-shape')).toBe('ellipse')
  })

  it('无填充的形状 fill=none；fillOpacity 透传', () => {
    expect(prim(shapes(draw([rect({ fill: null })]))[0], 'rect').getAttribute('fill')).toBe('none')
    expect(prim(shapes(draw([rect({ fill: '#f00', fillOpacity: 0.4 })]))[0], 'rect').getAttribute('fill-opacity')).toBe('0.4')
  })

  it('QA-WIN-52 放大框预设：小框与连线是虚线，大框是实线', () => {
    const [small, line, big] = buildPreset('magnifier', C)
    const [gs, gl, gb] = shapes(draw([small, line, big]))
    const sw = (o: ShapeObject) => o.strokePt * MM_PER_PT
    expect(prim(gs, 'rect').getAttribute('stroke-dasharray')).toBe(dashArray('dashed', sw(small as ShapeObject)))
    expect(prim(gl, 'line').getAttribute('stroke-dasharray')).toBe(dashArray('dashed', sw(line as ShapeObject)))
    expect(prim(gb, 'rect').getAttribute('stroke-dasharray')).toBeNull()
  })

  it('旋转绕包围盒中心', () => {
    const cap = buildPreset('dimension', C)[1] as ShapeObject // rotationDeg 90
    const g = shapes(draw([cap]))[0]
    expect(g.getAttribute('transform')).toBe(`rotate(90 ${cap.x + cap.w / 2} ${cap.y + cap.h / 2})`)
  })
})

describe('QA-WIN-39 比例尺：线是水平的，在文字上方', () => {
  it('线按自己的端点（盒内水平中线）画，不是 h=2mm 的对角线；文字在线下方', () => {
    const [bar, label] = buildPreset('scalebar', C) as [ShapeObject, TextObject]
    const svg = draw([bar, label])
    const line = prim(shapes(svg)[0], 'line')
    expect(n(line, 'y1')).toBe(n(line, 'y2'))
    expect(n(line, 'x2') - n(line, 'x1')).toBeCloseTo(bar.w, 9)
    // 线的绝对 y = 对象 y + 中线；文字框顶 ≥ 线 y
    const lineY = bar.y + n(line, 'y1')
    expect(label.y).toBeGreaterThan(lineY)
  })
})

describe('不传 exportedShape：缩略图示意画法一字不变', () => {
  it('没有 data-thumb-shape，仍是灰色外框线', () => {
    const objs = buildPreset('reversible', C)
    const svg = draw(objs, false)
    expect(svg.querySelector('[data-thumb-shape]')).toBeNull()
    const line = svg.querySelector('line')!
    expect(n(line, 'y1')).not.toBe(n(line, 'y2'))
  })
})
