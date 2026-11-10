/**
 * 一次拖动的粗粒度形状（给提示引擎，ADR 0177）：只出枚举与布尔，判据按**手走的原始位移**算。
 */
import { describe, expect, it } from 'vitest'
import { dragShape, drawShape } from './interactions'

const none = { shiftKey: false, metaKey: false, ctrlKey: false }

describe('dragShape', () => {
  it('一个方向走了 10 mm 以上、另一个方向偏了不到 1 mm = 直线', () => {
    expect(dragShape(24, 0.6, none, false).track).toBe('straight')
    expect(dragShape(-0.4, -15, none, false).track).toBe('straight')
  })
  it('走得不够远 / 偏得多了 = 不是直线', () => {
    expect(dragShape(8, 0.2, none, false).track).toBe('free')
    expect(dragShape(24, 3, none, false).track).toBe('free')
  })
  it('不到 2 mm = 很小的拖动', () => {
    expect(dragShape(1.2, 0.9, none, false).size).toBe('tiny')
    expect(dragShape(1.6, 1.6, none, false).size).toBe('normal')
  })
  it('修饰键：⇧、⌘ 或 Ctrl、两个都按', () => {
    expect(dragShape(5, 5, none, false).mods).toBe('none')
    expect(dragShape(5, 5, { ...none, shiftKey: true }, false).mods).toBe('shift')
    expect(dragShape(5, 5, { ...none, metaKey: true }, false).mods).toBe('meta')
    expect(dragShape(5, 5, { ...none, ctrlKey: true }, false).mods).toBe('meta')
    expect(dragShape(5, 5, { shiftKey: true, metaKey: false, ctrlKey: true }, true)).toMatchObject({ mods: 'both', snapped: true })
  })
  it('payload 里只有形状，没有位置', () => {
    expect(Object.keys(dragShape(12.34, 56.78, none, false)).sort()).toEqual(['kind', 'mods', 'size', 'snapped', 'track'])
  })
})

describe('drawShape', () => {
  it('矩形 / 椭圆两边差不到一成 = 差一点就是正的；正好相等或差得多都不算', () => {
    expect(drawShape('rect', { x: 30, y: 28 }, { w: 30, h: 28 }, false).near).toBe(true)
    expect(drawShape('ellipse', { x: 30, y: 30 }, { w: 30, h: 30 }, false).near).toBe(false)
    expect(drawShape('rect', { x: 30, y: 18 }, { w: 30, h: 18 }, false).near).toBe(false)
  })
  it('线 / 箭头离水平或竖直不到 4° = 差一点就是直的；正好水平不算', () => {
    expect(drawShape('line', { x: 40, y: 1.5 }, { w: 40, h: 1.5 }, false).near).toBe(true)
    expect(drawShape('arrow', { x: -1, y: 40 }, { w: 1, h: 40 }, false).near).toBe(true)
    expect(drawShape('line', { x: 40, y: 0 }, { w: 40, h: 0 }, false).near).toBe(false)
    expect(drawShape('line', { x: 40, y: 20 }, { w: 40, h: 20 }, false).near).toBe(false)
  })
  it('按着 ⇧ 画的：snapped，不再算「差一点」', () => {
    expect(drawShape('rect', { x: 30, y: 28 }, { w: 30, h: 28 }, true)).toEqual({ kind: 'shape.drawn', near: false, snapped: true })
  })
})
