import { describe, expect, it } from 'vitest'
import type { PanelOverride } from '@/types/document'
import { overrideCounts } from './overrideCounts'

const ov = (gid: string, prop: string): PanelOverride => ({ gid, prop, value: 1 })

describe('overrideCounts：恢复动作的对象与数量', () => {
  const overrides = [
    ov('axes_0.title', 'fontsize'),
    ov('axes_0.title', 'color'),
    ov('axes_0.xlabel', 'fontsize'),
    ov('figure', 'size_mm'),
  ]

  it('元素数只算落在该 gid 上的，整图数是全部', () => {
    expect(overrideCounts(overrides, 'axes_0.title')).toEqual({ element: 2, figure: 4 })
    expect(overrideCounts(overrides, 'axes_0.xlabel')).toEqual({ element: 1, figure: 4 })
  })

  it('没有 gid（选中的是整张图）时元素数为 0，整图数不受影响', () => {
    expect(overrideCounts(overrides, null)).toEqual({ element: 0, figure: 4 })
    expect(overrideCounts(overrides, undefined)).toEqual({ element: 0, figure: 4 })
  })

  it('这个元素上一项都没有时元素数为 0，而不是把别的元素的算进来', () => {
    expect(overrideCounts(overrides, 'axes_0.ylabel')).toEqual({ element: 0, figure: 4 })
    expect(overrideCounts([], 'axes_0.title')).toEqual({ element: 0, figure: 0 })
  })
})
