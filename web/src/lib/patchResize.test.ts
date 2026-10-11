import { describe, expect, it } from 'vitest'
import type { ManifestElement } from './api'
import { patchBoundsOf, patchMinSize, patchResizePatches, resizePatchBox } from './patchResize'
import type { Rect4 } from './axesLayout'
import type { ResizeDir } from './geometry'

const box: Rect4 = [0.2, 0.3, 0.4, 0.2]
const element: ManifestElement = {
  gid: 'axes_0.patches_0', role: 'patch', label: 'Box', bbox: box,
  shape_bounds: box, shape_min_size: [0.02, 0.03], anchor: [0.2, 0.5],
  draggable: true, drag_prop: 'pos_frac',
  editable: [{ prop: 'size_frac', type: 'pair', value: [0.4, 0.2] }],
}

describe('独立图框的几何能力', () => {
  it('只认 size_frac 与未裁剪外框，旧引擎 / 不支持的形状没有缩放入口', () => {
    expect(patchBoundsOf(element)).toEqual(box)
    expect(patchBoundsOf({ ...element, editable: [] })).toBeNull()
    expect(patchBoundsOf({ ...element, shape_bounds: undefined })).toBeNull()
    expect(patchBoundsOf({ ...element, resizable: true, editable: [] })).toBeNull()
  })

  it('bbox 的裁剪不改变未裁剪外框，只有权威 shape_bounds 决定手柄位置', () => {
    expect(patchBoundsOf({ ...element, bbox: [0.3, 0.3, 0.05, 0.1] })).toEqual(box)
  })

  it('非有限数与非正尺寸不能进入几何写操作', () => {
    expect(patchBoundsOf({ ...element, shape_bounds: [0.2, 0.3, NaN, 0.2] })).toBeNull()
    expect(patchBoundsOf({ ...element, shape_bounds: [0.2, 0.3, 0, 0.2] })).toBeNull()
    expect(patchResizePatches(element, box, [0.2, 0.3, Infinity, 0.2])).toEqual([])
  })
})

describe('八方向改框只动被拖的边', () => {
  // 独立预期值：+0.1 水平、+0.05 竖直，固定的对边 / 对角仍在原坐标。
  const cases: [ResizeDir, Rect4][] = [
    ['e', [0.2, 0.3, 0.5, 0.2]], ['w', [0.3, 0.3, 0.3, 0.2]],
    ['n', [0.2, 0.35, 0.4, 0.15]], ['s', [0.2, 0.3, 0.4, 0.25]],
    ['ne', [0.2, 0.35, 0.5, 0.15]], ['nw', [0.3, 0.35, 0.3, 0.15]],
    ['se', [0.2, 0.3, 0.5, 0.25]], ['sw', [0.3, 0.3, 0.3, 0.25]],
  ]
  it.each(cases)('%s 的对边 / 对角不动', (dir, expected) => {
    resizePatchBox(box, dir, 0.1, 0.05, [0.02, 0.03]).forEach((n, i) => expect(n).toBeCloseTo(expected[i]))
  })

  it('可以拖出图幅，但越过对边时停在引擎声明的最小尺寸', () => {
    expect(resizePatchBox(box, 'se', 1, 1, [0.02, 0.03])).toEqual([0.2, 0.3, 1.4, 1.2])
    const shrunk = resizePatchBox(box, 'nw', 10, 10, [0.02, 0.03])
    expect(shrunk[0] + shrunk[2]).toBeCloseTo(0.6)
    expect(shrunk[1] + shrunk[3]).toBeCloseTo(0.5)
    expect(shrunk[2]).toBeCloseTo(0.02)
    expect(shrunk[3]).toBeCloseTo(0.03)
  })

  it('圆角下限向上量化，提交不因四舍五入低于后端下限', () => {
    const rounded = { ...element, shape_min_size: [0.02001, 0.03101] as [number, number] }
    expect(patchMinSize(rounded)).toEqual([0.0201, 0.0311])
    expect(patchResizePatches(rounded, box, [0.2, 0.3, 0.02, 0.031])).toEqual([])
  })

  it('一个矩形只落尺寸与左下锚点，拖回起点没有补写', () => {
    expect(patchResizePatches(element, box, [0.1, 0.25, 0.5, 0.3])).toEqual([
      { gid: element.gid, prop: 'size_frac', value: [0.5, 0.3] },
      { gid: element.gid, prop: 'pos_frac', value: [0.1, 0.55] },
    ])
    expect(patchResizePatches(element, box, box)).toEqual([])
    expect(patchResizePatches(element, box, [0.2, 0.3, 0.400001, 0.2])).toEqual([])
  })
})

describe('没有位移的尺寸编辑不固定脚本定位', () => {
  const cases: [ResizeDir, number[], number[] | null][] = [
    ['e', [0.5, 0.2], null], ['w', [0.3, 0.2], [0.3, 0.5]],
    ['n', [0.4, 0.15], null], ['s', [0.4, 0.25], [0.2, 0.55]],
    ['ne', [0.5, 0.15], null], ['nw', [0.3, 0.15], [0.3, 0.5]],
    ['se', [0.5, 0.25], [0.2, 0.55]], ['sw', [0.3, 0.25], [0.3, 0.55]],
  ]
  it.each(cases)('%s 只在左下锚点变了时提交 pos_frac', (dir, size, position) => {
    const next = resizePatchBox(box, dir, 0.1, 0.05, [0.02, 0.03])
    const expected = [{ gid: element.gid, prop: 'size_frac', value: size }]
    if (position) expected.push({ gid: element.gid, prop: 'pos_frac', value: position })
    expect(patchResizePatches(element, box, next)).toEqual(expected)
  })

  it('宽高确实变了但锚点只差舍入噪声时，不凭噪声写位置', () => {
    expect(patchResizePatches(element, box, [0.200001, 0.350001, 0.5, 0.15])).toEqual([
      { gid: element.gid, prop: 'size_frac', value: [0.5, 0.15] },
    ])
  })

  it('比较左下锚点的和再舍入，而不是先舍入 top 与 height', () => {
    const start: Rect4 = [0.2, 0.30004, 0.20004, 0.20004]
    expect(patchResizePatches(element, start, [0.2, 0.35004, 0.3, 0.15004])).toEqual([
      { gid: element.gid, prop: 'size_frac', value: [0.3, 0.15] },
    ])
    expect(patchResizePatches(element, start, [0.2, 0.35008, 0.3, 0.15008])).toEqual([
      { gid: element.gid, prop: 'size_frac', value: [0.3, 0.1501] },
      { gid: element.gid, prop: 'pos_frac', value: [0.2, 0.5002] },
    ])
  })
})
