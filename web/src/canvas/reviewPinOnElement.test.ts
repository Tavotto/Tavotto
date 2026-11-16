import { describe, expect, it } from 'vitest'

import { pinOnElement } from './ReviewOverlay'

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
