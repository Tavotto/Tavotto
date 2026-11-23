/** 路径文字的手动离线随缩放一起走（Codex 线程 4614422959） */
import { describe, expect, it } from 'vitest'
import { applyAnnotationScale, captureOrigins } from './annotationScale'
import { scaleObject } from './transform'
import type { CanvasObject, PathTextObject } from '@/types/document'

const pt = (over: Partial<PathTextObject> = {}): PathTextObject =>
  ({
    id: 'pt1', type: 'pathText', text: 'abc', x: 10, y: 10, w: 40, h: 10, sizePt: 10, bold: false, color: '#000',
    subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [1, 0] }] }], baselineOffsetPt: 12, ...over,
  }) as PathTextObject

describe('路径文字手动离线随缩放', () => {
  it('整组缩放（缩放描边和效果）：离线乘 √(kx·ky)，与字号、线宽同一个比例', () => {
    const o = scaleObject(pt(), 2, 2, [0, 0], { strokes: true, text: 'size' })
    expect(o.baselineOffsetPt).toBeCloseTo(24)
    expect(o.sizePt).toBeCloseTo(20)
  })
  it('负离线也按比例', () => {
    expect(scaleObject(pt({ baselineOffsetPt: -6 }), 0.5, 0.5, [0, 0], { strokes: true, text: 'size' }).baselineOffsetPt).toBeCloseTo(-3)
  })
  it('没写手动离线（自动）不凭空添一个', () => {
    const o = scaleObject(pt({ baselineOffsetPt: undefined }), 2, 2, [0, 0], { strokes: true, text: 'size' })
    expect('baselineOffsetPt' in o && o.baselineOffsetPt !== undefined).toBe(false)
  })
  it('面板缩放带着图内标注：离线随标注比例', () => {
    const objs: CanvasObject[] = [pt()]
    applyAnnotationScale(objs, captureOrigins(objs), { x: 0, y: 0 }, 1.5)
    expect((objs[0] as PathTextObject).baselineOffsetPt).toBeCloseTo(18)
  })
})
