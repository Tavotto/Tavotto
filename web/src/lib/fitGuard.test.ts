import { describe, expect, it } from 'vitest'
import { shouldFitOnDoubleClick, type FitGuardCtx } from './fitGuard'

const base: FitGuardCtx = {
  tool: 'select',
  spaceDown: false,
  editingText: false,
  cropping: false,
  interacting: false,
  onObject: false,
  point: { x: -20, y: 30 }, // 页面左侧灰色区域
  frame: { x: 0, y: 0, w: 150, h: 100 },
}

describe('双击回中触发判定', () => {
  it('页面外空白双击触发', () => {
    expect(shouldFitOnDoubleClick(base)).toBe(true)
    expect(shouldFitOnDoubleClick({ ...base, point: { x: 200, y: 50 } })).toBe(true)
    expect(shouldFitOnDoubleClick({ ...base, point: { x: 50, y: -5 } })).toBe(true)
  })

  it('页面内容双击不触发', () => {
    expect(shouldFitOnDoubleClick({ ...base, point: { x: 75, y: 50 } })).toBe(false)
    expect(shouldFitOnDoubleClick({ ...base, point: { x: 0, y: 0 } })).toBe(false)
    expect(shouldFitOnDoubleClick({ ...base, point: { x: 150, y: 100 } })).toBe(false)
  })

  it('取景框不在原点（快速编辑里那张图的矩形）：按框本身判内外，不是 (0,0) 到右下角', () => {
    const frame = { x: -15, y: -10, w: 40, h: 30 }
    expect(shouldFitOnDoubleClick({ ...base, frame, point: { x: -10, y: -5 } })).toBe(false)
    expect(shouldFitOnDoubleClick({ ...base, frame, point: { x: 30, y: 15 } })).toBe(true)
    const off = { x: 10, y: 20, w: 40, h: 30 }
    expect(shouldFitOnDoubleClick({ ...base, frame: off, point: { x: 5, y: 5 } })).toBe(true)
    expect(shouldFitOnDoubleClick({ ...base, frame: off, point: { x: 30, y: 40 } })).toBe(false)
  })

  it('对象上双击不触发（越界对象也一样）', () => {
    expect(shouldFitOnDoubleClick({ ...base, onObject: true })).toBe(false)
  })

  it('绘图 / 平移 / 裁剪 / 文字编辑 / 拖动中不误触', () => {
    expect(shouldFitOnDoubleClick({ ...base, tool: 'rect' })).toBe(false)
    expect(shouldFitOnDoubleClick({ ...base, spaceDown: true })).toBe(false)
    expect(shouldFitOnDoubleClick({ ...base, cropping: true })).toBe(false)
    expect(shouldFitOnDoubleClick({ ...base, editingText: true })).toBe(false)
    expect(shouldFitOnDoubleClick({ ...base, interacting: true })).toBe(false)
  })
})
