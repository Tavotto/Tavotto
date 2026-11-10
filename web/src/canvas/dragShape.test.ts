/**
 * 一次拖动的粗粒度形状（给提示引擎，ADR 0177）：只出枚举与布尔，判据按**手走的原始位移**算。
 */
import { describe, expect, it } from 'vitest'
import { dragShape } from './interactions'

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
