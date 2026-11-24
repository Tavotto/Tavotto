import { describe, expect, it } from 'vitest'
import type { CanvasObject } from '@/types/document'
import { CANVAS_STROKE_TYPES, canvasStrokePt } from './preflight'

/** ADR 0208 §四 第 7 条：画布上的箭头 / 形状 / 路径「都管」——这里只看「画得出的描边有多宽」这一步 */
const base = { id: 'o1', x: 0, y: 0, w: 10, h: 10 }
const obj = (o: Record<string, unknown>) => ({ ...base, ...o }) as unknown as CanvasObject

describe('canvasStrokePt', () => {
  it('箭头总有描边', () => {
    expect(canvasStrokePt(obj({ type: 'arrow', strokePt: 0.3, color: '#000' }))).toBe(0.3)
  })

  it('形状关了边框就没有描边；只有描边的形状（直线）关不掉', () => {
    expect(canvasStrokePt(obj({ type: 'shape', shape: 'rect', strokePt: 0.3, stroke: false }))).toBeUndefined()
    expect(canvasStrokePt(obj({ type: 'shape', shape: 'rect', strokePt: 0.3 }))).toBe(0.3)
    expect(canvasStrokePt(obj({ type: 'shape', shape: 'line', strokePt: 0.3, stroke: false }))).toBe(0.3)
  })

  it('路径按 readPath 的 strokeOn', () => {
    const p = { type: 'path', subpaths: [], strokePt: 0.2, color: '#000', fill: null }
    expect(canvasStrokePt(obj(p))).toBe(0.2)
    expect(canvasStrokePt(obj({ ...p, stroke: false }))).toBeUndefined()
  })

  it('描边全透明、线宽不是正数都当没有描边', () => {
    expect(canvasStrokePt(obj({ type: 'arrow', strokePt: 0.3, strokeOpacity: 0 }))).toBeUndefined()
    expect(canvasStrokePt(obj({ type: 'arrow', strokePt: 0 }))).toBeUndefined()
    expect(canvasStrokePt(obj({ type: 'arrow', strokePt: Number.NaN }))).toBeUndefined()
  })

  it('文字与面板不判', () => {
    expect(canvasStrokePt(obj({ type: 'text', text: 'a', sizePt: 8 }))).toBeUndefined()
    expect(CANVAS_STROKE_TYPES).toEqual(['arrow', 'shape', 'path'])
  })
})
