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
  page: { w: 150, h: 100 },
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
