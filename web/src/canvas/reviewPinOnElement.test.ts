import { describe, expect, it } from 'vitest'

import { intersectRect, pinOnElement } from './ReviewOverlay'

describe('元素意见的圆点落在被点的那一处', () => {
  it('按点击位置在当时元素框里的相对位置换算', () => {
    const [u, v] = pinOnElement([0.1, 0.2, 0.4, 0.2], [0.3, 0.25])!
    expect(u).toBeCloseTo(0.5)
    expect(v).toBeCloseTo(0.25)
  })
  it('框外的点夹回框边；框退化时退回角上', () => {
    expect(pinOnElement([0.1, 0.2, 0.4, 0.2], [0.9, 0.0])).toEqual([1, 0])
    expect(pinOnElement([0.1, 0.2, 0, 0.2], [0.1, 0.25])).toBeNull()
  })
})

describe('圆点按裁过的元素框定位', () => {
  it('未裁剪框与面板可见框求交后，相对位置与上传的（已夹进画面的）框一致', () => {
    const panel = { x: 100, y: 100, width: 200, height: 200 }
    // manifest 里未裁剪框 [-1,-1,3,3]：画面上 -100..500；上传的框是 [0,0,1,1]
    const unclipped = { x: -100, y: -100, width: 600, height: 600 }
    const visible = intersectRect(unclipped, panel)!
    expect(visible).toEqual(panel)
    const [u, v] = pinOnElement([0, 0, 1, 1], [0.25, 0.25])!
    expect(visible.x + u * visible.width).toBeCloseTo(150)
    expect(visible.y + v * visible.height).toBeCloseTo(150)
  })
  it('交集为空时 null（调用方退回面板框）', () => {
    const panel = { x: 0, y: 0, width: 10, height: 10 }
    expect(intersectRect({ x: 20, y: 20, width: 5, height: 5 }, panel)).toBeNull()
    expect(intersectRect({ x: 10, y: 0, width: 5, height: 5 }, panel)).toBeNull()
  })
})
