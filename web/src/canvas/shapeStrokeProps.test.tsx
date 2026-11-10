/**
 * 形状 / 箭头的描边属性补齐（ADR 0174）：画布（ShapeView / ArrowView）、导出预览（ThumbShape）、
 * 导出载荷（toExportObjects）、属性栏（ShapeSection / ArrowSection）。
 *
 * 判据的主语：**画布上这个 SVG 元素此刻带不带某个描边属性、填充铺多大**——不是文档里的字段。
 * 无边框 = 一个 stroke 属性都不给，且填充铺满整个包围盒（后端 `geometry.shape_paths` 同一条）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { ThumbShape } from '@/components/ThumbShape'
import { ArrowSection, ShapeSection } from '@/components/inspector/StrokeSection'
import { toExportObjects } from '@/lib/exportPayload'
import { mmToWorld } from '@/store/viewportStore'
import { MM_PER_PT } from '@/lib/units'
import type { ArrowObject, ShapeKind, ShapeObject } from '@/types/document'
import { ArrowView } from './ArrowView'
import { ShapeView } from './ShapeView'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const shape = (kind: ShapeKind, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's1',
  type: 'shape',
  shape: kind,
  x: 10,
  y: 20,
  w: 40,
  h: 30,
  strokePt: 2,
  color: '#336699',
  fill: '#ffcc00',
  ...over,
})
const arrow = (over: Partial<ArrowObject> = {}): ArrowObject => ({
  id: 'a1',
  type: 'arrow',
  x: 10,
  y: 20,
  w: 40,
  h: 0,
  start: { rx: 0, ry: 0 },
  end: { rx: 1, ry: 0 },
  color: '#336699',
  strokePt: 2,
  head: 'end',
  ...over,
})

const dom = (node: React.ReactElement): Element => {
  const host = document.createElement('div')
  host.innerHTML = renderToStaticMarkup(node)
  return host
}
const attr = (el: Element | null, name: string) => el?.getAttribute(name) ?? null
const STROKE_ATTRS = ['stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'stroke-opacity']

describe('无边框（stroke: false）', () => {
  const CLOSED: ShapeKind[] = ['rect', 'ellipse', 'triangle', 'diamond', 'polygon']

  it.each(CLOSED)('%s：画布上不带任何描边属性', (kind) => {
    const body = dom(<ShapeView obj={shape(kind, { stroke: false })} />).querySelector(
      'rect,ellipse,polygon',
    )!
    for (const a of STROKE_ATTRS) expect(body.hasAttribute(a), a).toBe(false)
    expect(attr(body, 'fill')).toBe('#ffcc00')
  })

  it.each(CLOSED)('%s：有描边时（对照组）带描边属性', (kind) => {
    const body = dom(<ShapeView obj={shape(kind)} />).querySelector('rect,ellipse,polygon')!
    expect(attr(body, 'stroke')).toBe('#336699')
  })

  it('矩形填充铺满整个包围盒（不再内缩半个线宽）；有描边时仍内缩', () => {
    const w = mmToWorld(40)
    const h = mmToWorld(30)
    const off = dom(<ShapeView obj={shape('rect', { stroke: false })} />).querySelector('rect')!
    expect([attr(off, 'x'), attr(off, 'y')]).toEqual(['0', '0'])
    expect(Number(attr(off, 'width'))).toBeCloseTo(w, 6)
    expect(Number(attr(off, 'height'))).toBeCloseTo(h, 6)
    const on = dom(<ShapeView obj={shape('rect')} />).querySelector('rect')!
    const sw = mmToWorld(2 * MM_PER_PT)
    expect(Number(attr(on, 'x'))).toBeCloseTo(sw / 2, 6)
    expect(Number(attr(on, 'width'))).toBeCloseTo(w - sw, 6)
  })

  it('直线与大括号不看这个字段（只有描边）', () => {
    for (const kind of ['line', 'brace'] as const) {
      const body = dom(<ShapeView obj={shape(kind, { stroke: false, fill: null })} />).querySelector(
        'path:not([data-hit-shape]),line:not([data-hit-line])',
      )!
      expect(attr(body, 'stroke'), kind).toBe('#336699')
    }
  })

  it('导出预览（ThumbShape）同样不画描边，填充铺满', () => {
    const svg = dom(
      <svg>
        <ThumbShape obj={shape('rect', { stroke: false })} />
      </svg>,
    )
    const r = svg.querySelector('rect')!
    for (const a of STROKE_ATTRS) expect(r.hasAttribute(a), a).toBe(false)
    expect(Number(attr(r, 'width'))).toBeCloseTo(40, 6)
    expect(attr(r, 'x')).toBe('0')
  })

  it('导出载荷：只发 stroke:false；缺省 / true 不发；无边框时不再发描边不透明度', () => {
    const [off, on, line] = toExportObjects([
      shape('rect', { id: 'a', stroke: false, strokeOpacity: 0.5 }),
      shape('rect', { id: 'b', stroke: true }),
      shape('line', { id: 'c', stroke: false }),
    ]) as unknown as Record<string, unknown>[]
    expect(off.stroke).toBe(false)
    expect('stroke_opacity' in off && off.stroke_opacity !== undefined).toBe(false)
    expect(on.stroke).toBeUndefined()
    expect(line.stroke).toBeUndefined()
  })
})

describe('无边框又无填充的形状', () => {
  it('命中层整块可点（pointer-events: all），不是只有一圈看不见的轮廓', () => {
    for (const kind of ['ellipse', 'triangle', 'diamond', 'polygon'] as const) {
      const hit = dom(<ShapeView obj={shape(kind, { stroke: false, fill: null })} hit="stroke" />).querySelector(
        '[data-hit-shape]',
      ) as HTMLElement
      expect(hit.style.pointerEvents, kind).toBe('all')
    }
    const outlineOnly = dom(<ShapeView obj={shape('ellipse', { fill: null })} hit="stroke" />).querySelector(
      '[data-hit-shape]',
    ) as HTMLElement
    expect(outlineOnly.style.pointerEvents).toBe('stroke') // 对照：有描边的空心形状仍只在轮廓附近命中
  })
})

describe('描边不透明度', () => {
  it('形状：写在描边上，不碰填充；100% 不写属性', () => {
    const el = dom(<ShapeView obj={shape('rect', { strokeOpacity: 0.4 })} />).querySelector('rect')!
    expect(attr(el, 'stroke-opacity')).toBe('0.4')
    expect(attr(el, 'fill-opacity')).toBe('1') // 填充的不透明度还是它自己的（缺省 1）
    const full = dom(<ShapeView obj={shape('rect', { strokeOpacity: 1 })} />).querySelector('rect')!
    expect(full.hasAttribute('stroke-opacity')).toBe(false)
  })

  it('箭头：杆与每个实心端型（polygon / circle）同一个不透明度', () => {
    const svg = dom(
      <ArrowView obj={arrow({ headStart: 'dot', headEnd: 'stealth', strokeOpacity: 0.3 })} />,
    )
    expect(attr(svg.querySelector('line:not([data-hit-line])'), 'stroke-opacity')).toBe('0.3')
    expect(attr(svg.querySelector('circle'), 'fill-opacity')).toBe('0.3')
    expect(attr(svg.querySelector('polygon'), 'fill-opacity')).toBe('0.3')
  })

  it('导出载荷：缺省 / 100% 不发，其余原样', () => {
    const [a, b, c] = toExportObjects([
      arrow({ id: 'a' }),
      arrow({ id: 'b', strokeOpacity: 1 }),
      arrow({ id: 'c', strokeOpacity: 0.25 }),
    ]) as unknown as Record<string, unknown>[]
    expect(a.stroke_opacity).toBeUndefined()
    expect(b.stroke_opacity).toBeUndefined()
    expect(c.stroke_opacity).toBe(0.25)
  })
})

describe('线端 / 拐角 / 线型', () => {
  it('缺省保持现在的画法：圆头 + 圆角（封闭形状 / 箭头），直线与大括号尖角', () => {
    const rect = dom(<ShapeView obj={shape('rect')} />).querySelector('rect')!
    expect([attr(rect, 'stroke-linecap'), attr(rect, 'stroke-linejoin')]).toEqual(['round', 'round'])
    const brace = dom(<ShapeView obj={shape('brace', { fill: null })} />).querySelector(
      'path:not([data-hit-shape])',
    )!
    expect(attr(brace, 'stroke-linejoin')).toBe('miter')
    const shaft = dom(<ArrowView obj={arrow()} />).querySelector('line:not([data-hit-line])')!
    expect([attr(shaft, 'stroke-linecap'), attr(shaft, 'stroke-linejoin')]).toEqual(['round', 'round'])
  })

  it('斜接上限与 PDF 同一个数（SVG 缺省 4、PDF 缺省 10，不写的话尖三角在画布上被削）', () => {
    const rect = dom(<ShapeView obj={shape('triangle', { lineJoin: 'miter' })} />).querySelector('polygon')!
    expect(attr(rect, 'stroke-miterlimit')).toBe('10')
  })

  it('用户选的线端 / 拐角落到描边上', () => {
    const rect = dom(
      <ShapeView obj={shape('rect', { lineCap: 'butt', lineJoin: 'bevel', dash: 'dashed' })} />,
    ).querySelector('rect')!
    expect([attr(rect, 'stroke-linecap'), attr(rect, 'stroke-linejoin')]).toEqual(['butt', 'bevel'])
  })

  it('点线强制圆头：选了平头 / 方头点线仍然可见', () => {
    for (const cap of ['butt', 'square'] as const) {
      const rect = dom(<ShapeView obj={shape('rect', { dash: 'dotted', lineCap: cap })} />).querySelector(
        'rect',
      )!
      expect(attr(rect, 'stroke-linecap'), cap).toBe('round')
    }
  })

  it('箭头端型的线端 / 拐角跟随对象，且端型不画虚线', () => {
    const svg = dom(
      <ArrowView
        obj={arrow({ headEnd: 'open', dash: 'dashdot', lineCap: 'square', lineJoin: 'miter' })}
      />,
    )
    const head = svg.querySelector('path')!
    expect(head.hasAttribute('stroke-dasharray')).toBe(false)
    expect([attr(head, 'stroke-linecap'), attr(head, 'stroke-linejoin')]).toEqual(['square', 'miter'])
    expect(attr(svg.querySelector('line:not([data-hit-line])'), 'stroke-dasharray')).not.toBeNull()
  })

  it('新线型落成不同的 stroke-dasharray', () => {
    const arr = (dash: ShapeObject['dash']) =>
      attr(dom(<ShapeView obj={shape('rect', { dash })} />).querySelector('rect'), 'stroke-dasharray')
    const seen = new Set(['dashed', 'dotted', 'longdash', 'dashdot', 'longdashdot'].map((d) => arr(d as ShapeObject['dash'])))
    expect(seen.size).toBe(5)
    expect(arr('solid')).toBeNull()
  })

  it('导出载荷：缺省不发；线端缺省 = round、拐角缺省按形状族（等于缺省不发）', () => {
    const [plain, set, brace, braceMiter] = toExportObjects([
      shape('rect', { id: 'a' }),
      shape('rect', { id: 'b', lineCap: 'butt', lineJoin: 'bevel', dash: 'longdash' }),
      shape('brace', { id: 'c', fill: null, lineJoin: 'round' }),
      shape('brace', { id: 'd', fill: null, lineJoin: 'miter' }),
    ]) as unknown as Record<string, unknown>[]
    expect([plain.line_cap, plain.line_join, plain.dash]).toEqual([undefined, undefined, undefined])
    expect([set.line_cap, set.line_join, set.dash]).toEqual(['butt', 'bevel', 'longdash'])
    expect(brace.line_join).toBe('round')
    expect(braceMiter.line_join).toBeUndefined()
  })
})

describe('箭头端型', () => {
  const end = (head: ArrowObject['headEnd'], over: Partial<ArrowObject> = {}) =>
    dom(<ArrowView obj={arrow({ headStart: 'none', headEnd: head, ...over })} />)

  it('圆点是实心圆、菱形与燕尾是实心多边形', () => {
    expect(end('dot').querySelector('circle')).not.toBeNull()
    expect(end('diamond').querySelector('polygon')!.getAttribute('points')!.split(' ')).toHaveLength(4)
    expect(end('stealth').querySelector('polygon')!.getAttribute('points')!.split(' ')).toHaveLength(4)
  })

  it('端型大小三档：小 < 中 < 大，缺省 = 中', () => {
    const half = (size?: ArrowObject['headSizeEnd']) => {
      const pts = end('triangle', { headSizeEnd: size }).querySelector('polygon')!
        .getAttribute('points')!.split(' ').map((p) => p.split(',').map(Number))
      return Math.abs(pts[1][1] - pts[2][1]) / 2
    }
    expect(half('small')).toBeLessThan(half('medium'))
    expect(half('medium')).toBeLessThan(half('large'))
    expect(half(undefined)).toBeCloseTo(half('medium'), 9)
  })

  it('导出载荷：端型大小缺省 / 中不发', () => {
    const [a, b, c] = toExportObjects([
      arrow({ id: 'a' }),
      arrow({ id: 'b', headSizeEnd: 'medium' }),
      arrow({ id: 'c', headSizeEnd: 'large', headSizeStart: 'small', headStart: 'dot', headEnd: 'diamond' }),
    ]) as unknown as Record<string, unknown>[]
    expect([a.head_size_start, a.head_size_end]).toEqual([undefined, undefined])
    expect(b.head_size_end).toBeUndefined()
    expect([c.head_size_start, c.head_size_end, c.head_start, c.head_end]).toEqual(['small', 'large', 'dot', 'diamond'])
  })
})

describe('旧文档（不带新字段）的导出载荷逐字不变', () => {
  it('形状与箭头的载荷键集合与加属性之前相同', () => {
    const [s, a] = toExportObjects([shape('rect', { id: 's' }), arrow({ id: 'a' })]) as unknown as Record<string, unknown>[]
    const defined = (o: Record<string, unknown>) => Object.keys(o).filter((k) => o[k] !== undefined).sort()
    expect(defined(s)).toEqual(
      ['color', 'fill', 'h_mm', 'shape', 'stroke_pt', 'type', 'w_mm', 'x_mm', 'y_mm'],
    )
    expect(defined(a)).toEqual(['color', 'h_mm', 'head', 'start', 'end', 'stroke_pt', 'type', 'w_mm', 'x_mm', 'y_mm'].sort())
  })
})

/* ------------------------------- 属性栏 ------------------------------- */
let root: Root
let host: HTMLDivElement
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})
const render = (node: React.ReactNode) =>
  act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))

describe('属性栏', () => {
  it('封闭形状：描边行有开关；关着时线宽 / 线型 / 更多都收起，颜色换成「添加描边」', async () => {
    await render(<ShapeSection objs={[shape('rect', { stroke: false })]} />)
    expect(host.textContent).toContain('添加描边')
    expect(host.querySelector('[data-effect-add]')).not.toBeNull()
    expect(host.textContent).not.toContain('线宽')
    expect(host.textContent).not.toContain('线型')
    expect(host.querySelector('[data-fold="stroke-more"]')).toBeNull()
  })

  it('封闭形状：描边开着时有线宽 / 线型 / 更多', async () => {
    await render(<ShapeSection objs={[shape('rect')]} />)
    expect(host.textContent).toContain('线宽')
    expect(host.textContent).toContain('线型')
    expect(host.querySelector('[data-fold="stroke-more"]')).not.toBeNull()
    expect(host.querySelector('[data-effect-add]')).toBeNull() // 填充、描边都开着：没有任何「＋添加」
  })

  it('直线 / 大括号：没有描边开关，颜色常显', async () => {
    await render(<ShapeSection objs={[shape('line', { fill: null })]} />)
    expect(host.textContent).not.toContain('添加描边')
    expect(host.querySelector('input[type=color][aria-label="描边：取色盘"]')).not.toBeNull()
  })

  it('「更多」默认折起，点开才有线端 / 拐角 / 描边不透明度；箭头多出端型大小', async () => {
    await render(<ArrowSection objs={[arrow({ headEnd: 'triangle', headStart: 'none' })]} />)
    expect(host.querySelector('[data-prop="strokeOpacity"]')).toBeNull()
    const trigger = host.querySelector('[data-fold="stroke-more"] button') as HTMLButtonElement
    await act(async () => trigger.click())
    expect(host.querySelector('[data-prop="strokeOpacity"] input')).not.toBeNull()
    expect(host.textContent).toContain('线端')
    expect(host.textContent).toContain('拐角')
    expect(host.textContent).toContain('终点大小')
    expect(host.textContent).not.toContain('起点大小') // 起点没有端型就没有大小
  })

  it('点线：线端置灰并固定显示圆头', async () => {
    await render(<ShapeSection objs={[shape('rect', { dash: 'dotted' })]} />)
    await act(async () => (host.querySelector('[data-fold="stroke-more"] button') as HTMLButtonElement).click())
    const group = host.querySelector('[role=radiogroup][aria-label="线端"]')!
    const radios = [...group.querySelectorAll<HTMLElement>('[role=radio]')]
    expect(radios.every((r) => r.hasAttribute('disabled') || r.getAttribute('aria-disabled') === 'true')).toBe(true)
    expect(radios.find((r) => r.getAttribute('aria-checked') === 'true')?.textContent).toBe('圆头')
  })

  it('折叠行右值写改过几项；全是缺省时不写', async () => {
    await render(<ShapeSection objs={[shape('rect')]} />)
    expect(host.querySelector('[data-fold="stroke-more"] [data-summary-value]')).toBeNull()
    await render(<ShapeSection objs={[shape('rect', { strokeOpacity: 0.5, lineCap: 'butt' })]} />)
    expect(host.querySelector('[data-fold="stroke-more"] [data-summary-value]')!.textContent).toBe('2 项')
  })
})
