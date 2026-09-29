/**
 * 样式页示例图不裁东西（Codex #703）：刻度朝外伸到上限、线最粗、字最大时，画出来的每一笔
 * 都在 `viewBox` 里。量的是**渲染出的 DOM**（line / rect / polyline / text 的属性），
 * 不是 `sampleLayout` 自己报的外框——后者拿来比就是自己验自己。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { StyleSamplePreview } from './StyleSamplePreview'

// 文字外框的估法：字宽按字号倍数（粗体更宽），基线上 0.8、下 0.25 个字号
const GLYPH_W = 0.62
const GLYPH_W_BOLD = 0.68
const ASCENT = 0.8
const DESCENT = 0.25

let root: Root | null = null
let host: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

function render(data: Record<string, unknown>) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(<StyleSamplePreview data={data} />))
  return host.querySelector('svg')!
}

interface Box {
  what: string
  x0: number
  y0: number
  x1: number
  y1: number
}

const num = (el: Element, a: string) => Number(el.getAttribute(a) ?? 0)

/** 渲染出的每一笔的外框（线宽的一半算进去；文字按估法） */
function marks(svg: SVGSVGElement): Box[] {
  const out: Box[] = []
  for (const el of svg.querySelectorAll('rect')) {
    const s = num(el, 'stroke-width') / 2
    const x = num(el, 'x')
    const y = num(el, 'y')
    out.push({ what: 'rect', x0: x - s, y0: y - s, x1: x + num(el, 'width') + s, y1: y + num(el, 'height') + s })
  }
  for (const el of svg.querySelectorAll('line')) {
    const s = num(el, 'stroke-width') / 2
    const xs = [num(el, 'x1'), num(el, 'x2')]
    const ys = [num(el, 'y1'), num(el, 'y2')]
    out.push({ what: 'line', x0: Math.min(...xs) - s, y0: Math.min(...ys) - s, x1: Math.max(...xs) + s, y1: Math.max(...ys) + s })
  }
  for (const el of svg.querySelectorAll('polyline')) {
    const s = num(el, 'stroke-width') / 2
    const pts = el.getAttribute('points')!.split(' ').map((p) => p.split(',').map(Number))
    const xs = pts.map((p) => p[0])
    const ys = pts.map((p) => p[1])
    out.push({ what: 'polyline', x0: Math.min(...xs) - s, y0: Math.min(...ys) - s, x1: Math.max(...xs) + s, y1: Math.max(...ys) + s })
  }
  for (const el of svg.querySelectorAll('text')) {
    const pt = num(el, 'font-size')
    const w = (el.textContent ?? '').length * pt * (el.getAttribute('font-weight') === 'bold' ? GLYPH_W_BOLD : GLYPH_W)
    const anchor = el.getAttribute('text-anchor') ?? 'start'
    const along0 = anchor === 'middle' ? -w / 2 : anchor === 'end' ? -w : 0
    const tr = el.getAttribute('transform')
    const what = `text「${el.textContent}」`
    if (tr) {
      // 只有纵轴标题转了：translate(x y) rotate(-90)——字宽落在竖直方向，上伸朝左
      const m = /translate\(([-\d.e]+) ([-\d.e]+)\) rotate\(-90\)/.exec(tr)
      expect(m, tr).not.toBeNull()
      const [tx, ty] = [Number(m![1]), Number(m![2])]
      out.push({ what, x0: tx - ASCENT * pt, y0: ty - (along0 + w), x1: tx + DESCENT * pt, y1: ty - along0 })
    } else {
      const x = num(el, 'x')
      const y = num(el, 'y')
      out.push({ what, x0: x + along0, y0: y - ASCENT * pt, x1: x + along0 + w, y1: y + DESCENT * pt })
    }
  }
  return out
}

function outside(svg: SVGSVGElement): string[] {
  const [vx, vy, vw, vh] = svg.getAttribute('viewBox')!.split(' ').map(Number)
  return marks(svg)
    .filter((b) => b.x0 < vx || b.y0 < vy || b.x1 > vx + vw || b.y1 > vy + vh)
    .map((b) => `${b.what} [${b.x0.toFixed(1)},${b.y0.toFixed(1)} → ${b.x1.toFixed(1)},${b.y1.toFixed(1)}] vs viewBox ${vx.toFixed(1)},${vy.toFixed(1)} ${vw.toFixed(1)}×${vh.toFixed(1)}`)
}

// 样式页允许的上限（`StyleProfileFields.NUMBER_SPEC`）：刻度长 20、线宽类 10、字号 72
const bold = { weight: 'bold', style: 'italic' }
const EXTREMES: Array<[string, Record<string, unknown>]> = [
  ['默认', {}],
  ...(['out', 'inout', 'in'] as const).map((direction): [string, Record<string, unknown>] => [
    `刻度 ${direction} 长 20 · 刻度线宽 10`,
    { element: { ticks: { direction, length: 20, width: 10 } } },
  ]),
  ['边框 / 数据线宽 10', { element: { axes: { spine_linewidth: 10 }, line: { linewidth: 10 } } }],
  [
    '全部字号 72 且粗斜体 + 刻度朝外 20',
    {
      element: {
        title: { fontsize: 72, ...bold },
        axis_label: { fontsize: 72, ...bold },
        ticks: { fontsize: 72, direction: 'out', length: 20, width: 10 },
        legend: { fontsize: 72 },
        legend_text: bold,
        axes: { spine_linewidth: 10 },
      },
    },
  ],
  ['只有刻度字号大 + 刻度朝外 20', { element: { ticks: { fontsize: 14, direction: 'out', length: 20 } } }],
]

describe('样式示例图：每一笔都在画框里（Codex #703：长的朝外刻度把横轴标题裁掉）', () => {
  it.each(EXTREMES)('%s', (_name, data) => {
    const svg = render(data)
    expect(marks(svg).length).toBeGreaterThan(10)
    expect(outside(svg)).toEqual([])
  })

  it('外框比例固定：换一套刻度，设置页里示例图的外框不跳', () => {
    const ratio = (svg: SVGSVGElement) => {
      const [, , w, h] = svg.getAttribute('viewBox')!.split(' ').map(Number)
      return w / h
    }
    const a = ratio(render({}))
    act(() => root?.unmount())
    host?.remove()
    const b = ratio(render({ element: { ticks: { direction: 'out', length: 20 } } }))
    expect(b).toBeCloseTo(a, 6)
  })
})
