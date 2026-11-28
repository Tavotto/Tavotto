import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { CanvasObject } from '@/types/document'
import { glyphKeyOf, LayerGlyph } from './LayerGlyph'

const obj = (over: Record<string, unknown>) =>
  ({ id: 'a', x: 0, y: 0, w: 10, h: 10, strokePt: 1, color: '#000000', fill: null, ...over }) as unknown as CanvasObject
const html = (o: CanvasObject, hidden = false) => renderToStaticMarkup(<LayerGlyph obj={o} hidden={hidden} />)

describe('图层行首的对象图标', () => {
  it('闭合且有填充的路径用闭合轮廓，开放曲线用 S 形，不混用', () => {
    const sub = (closed: boolean) => [{ closed, nodes: [{ p: [0, 0] }, { p: [1, 1] }] }]
    expect(glyphKeyOf(obj({ type: 'path', subpaths: sub(true), fill: '#8ecae6' }))).toBe('pathClosed')
    expect(glyphKeyOf(obj({ type: 'path', subpaths: sub(false) }))).toBe('path')
  })

  it('实心矩形：原色填充 + 外框，线宽 1.5（24 网格，16px 下 1px）', () => {
    const h = html(obj({ type: 'shape', shape: 'rect', fill: '#E69F00' }))
    expect(h).toContain('fill="#e69f00"')
    expect(h).toContain('stroke-width="1.5"')
    expect(h).toContain('width="16"')
  })

  it('只有描边的形状不填充；子图走默认灰', () => {
    expect(html(obj({ type: 'shape', shape: 'ellipse', color: '#0072B2' }))).toContain('fill="none"')
    expect(html(obj({ type: 'panel', fileId: 'f.pdf' }))).toContain('text-ink-3')
  })

  it('隐藏的对象去饱和：颜色与正常行不同', () => {
    const o = obj({ type: 'shape', shape: 'rect', fill: '#e76f51' })
    expect(html(o, true)).not.toBe(html(o, false))
  })

  it('不认识的类型：不画（调用方退回通用图标）', () => {
    expect(glyphKeyOf(obj({ type: 'from-the-future' }))).toBeNull()
  })
})
