/** 品牌竖线画的纯函数：亮度表采样、线宽分档、最亮区域选取、网格吸附、弹簧与噪声。 */
import { describe, expect, it } from 'vitest'
import {
  TIER_COUNT,
  brightestRegions,
  flowNoise,
  lumaAt,
  lumaFromRgba,
  repelTarget,
  snapToGrid,
  springStep,
  widthTier,
} from './brandLinesMath'

/** w×h 的 RGBA：alpha 由 fn(x, y) 给 */
const rgba = (w: number, h: number, fn: (x: number, y: number) => number) => {
  const d = new Uint8ClampedArray(w * h * 4)
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) d[(y * w + x) * 4 + 3] = fn(x, y)
  return d
}

describe('lumaFromRgba：按格取不透明度平均', () => {
  it('左半不透明、右半透明 → 左列格 1、右列格 0', () => {
    const l = lumaFromRgba(rgba(8, 8, (x) => (x < 4 ? 255 : 0)), 8, 8, 2, 2)
    expect([...l]).toEqual([1, 0, 1, 0])
  })
  it('一格里一半像素不透明 → 0.5', () => {
    const l = lumaFromRgba(rgba(4, 4, (x) => (x % 2 === 0 ? 255 : 0)), 4, 4, 1, 1)
    expect(l[0]).toBeCloseTo(0.5, 5)
  })
  it('格数比像素还多也不崩（每格至少取一个像素）', () => {
    const l = lumaFromRgba(rgba(2, 2, () => 255), 2, 2, 4, 4)
    expect(l.length).toBe(16)
    expect(l.every((v) => v === 1)).toBe(true)
  })
})

describe('lumaAt：查表', () => {
  const l = new Float32Array([0.1, 0.2, 0.3, 0.4])
  it('命中格与范围外', () => {
    expect(lumaAt(l, 2, 2, 0.1, 0.1)).toBeCloseTo(0.1)
    expect(lumaAt(l, 2, 2, 0.9, 0.9)).toBeCloseTo(0.4)
    expect(lumaAt(l, 2, 2, -0.01, 0.5)).toBe(0)
    expect(lumaAt(l, 2, 2, 0.5, 1)).toBe(0)
  })
})

describe('widthTier：线宽只分 4 档', () => {
  it('阈值两侧各归各档，且只有 0..3', () => {
    expect(TIER_COUNT).toBe(4)
    expect([0, 0.11, 0.12, 0.37, 0.38, 0.67, 0.68, 1].map(widthTier)).toEqual([0, 0, 1, 1, 2, 2, 3, 3])
    for (let v = 0; v <= 1; v += 0.01) expect([0, 1, 2, 3]).toContain(widthTier(v))
  })
  it('亮处线粗、暗处线细：单调不减', () => {
    let prev = 0
    for (let v = 0; v <= 1; v += 0.01) {
      const t = widthTier(v)
      expect(t).toBeGreaterThanOrEqual(prev)
      prev = t
    }
  })
})

describe('snapToGrid', () => {
  it('吸附到最近的格点', () => {
    expect(snapToGrid(13, 8)).toBe(16)
    expect(snapToGrid(11, 8)).toBe(8)
    expect(snapToGrid(-3, 8)).toBe(-0)
    expect(snapToGrid(20, 8)).toBe(24)
  })
})

describe('brightestRegions：最亮的几块', () => {
  /** 16×16 的表，在 (c,r) 起的 8×8 块里填 v */
  const table = (blocks: { c: number; r: number; v: number }[]) => {
    const l = new Float32Array(16 * 16)
    for (const b of blocks) for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) l[(b.r + y) * 16 + b.c + x] = b.v
    return l
  }
  it('按亮度从高到低挑，且不超过 n', () => {
    const l = table([{ c: 0, r: 0, v: 0.4 }, { c: 8, r: 0, v: 0.9 }, { c: 0, r: 8, v: 0.6 }])
    const r = brightestRegions(l, 16, 16, 2, 8, 8)
    expect(r.map((x) => [x.c, x.r])).toEqual([[8, 0], [0, 8]])
  })
  it('离得太近的块被跳过（minGap）', () => {
    const l = table([{ c: 0, r: 0, v: 0.9 }, { c: 8, r: 0, v: 0.8 }, { c: 0, r: 8, v: 0.7 }])
    const r = brightestRegions(l, 16, 16, 3, 8, 12)
    expect(r.map((x) => [x.c, x.r])).toEqual([[0, 0]])
  })
  it('全黑的表没有区域', () => {
    expect(brightestRegions(new Float32Array(256), 16, 16, 3, 8, 8)).toEqual([])
  })
})

describe('springStep / repelTarget', () => {
  it('弹簧阻尼：离开平衡位置后收敛回目标，不发散', () => {
    let p = 20
    let v = 0
    for (let i = 0; i < 600; i++) [p, v] = springStep(p, v, 0, 1 / 60)
    expect(Math.abs(p)).toBeLessThan(0.05)
    expect(Math.abs(v)).toBeLessThan(0.05)
  })
  it('鼠标左边的线往左、右边的往右，越近推得越远，超出半径为 0', () => {
    expect(repelTarget(90, 100, 70, 26)).toBeLessThan(0)
    expect(repelTarget(110, 100, 70, 26)).toBeGreaterThan(0)
    expect(Math.abs(repelTarget(105, 100, 70, 26))).toBeGreaterThan(Math.abs(repelTarget(150, 100, 70, 26)))
    expect(repelTarget(300, 100, 70, 26)).toBe(0)
  })
})

describe('flowNoise', () => {
  it('值域在 0..1 内、随时间变化（在流动）', () => {
    let lo = 1
    let hi = 0
    for (let i = 0; i < 400; i++) {
      const v = flowNoise(i * 0.37, i * 0.21, i * 0.05)
      lo = Math.min(lo, v)
      hi = Math.max(hi, v)
    }
    expect(lo).toBeGreaterThanOrEqual(0)
    expect(hi).toBeLessThanOrEqual(1)
    expect(flowNoise(1, 2, 0)).not.toBe(flowNoise(1, 2, 1))
  })
})
