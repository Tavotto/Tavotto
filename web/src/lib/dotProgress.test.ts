import { describe, expect, it } from 'vitest'
import { dotLayout, dotsFor, filledCells, sweepCells, type DotLayout } from './dotProgress'

/** 画出来的点：按 [x, y] 建索引 */
const dotSet = (l: DotLayout, from: number, to: number) => new Set(dotsFor(l, from, to).map(([x, y]) => `${x},${y}`))

// 条在各缩放下的位图：CSS 336×12，物理像素 = round(CSS × 倍率)
const SCALES = [1, 1.25, 1.5, 1.75, 2, 3]
const layoutAt = (s: number) => dotLayout(Math.round(336 * s), Math.round(12 * s), 336)

describe('点阵进度条几何（ADR 0168）', () => {
  it.each(SCALES)('%sx：点 = 最接近倍率的整数个物理像素，间距 = 点 ×4 / ×2（与 Mac 2× 同比例）', (s) => {
    const l = layoutAt(s)
    expect(l.dot).toBe(Math.max(1, Math.round(s)))
    expect(l.emptyStep).toBe(4 * l.dot)
    expect(l.filledStep).toBe(2 * l.dot)
  })

  it.each(SCALES)('%sx：每个点都是整像素、互不重叠，且全部落在位图里', (s) => {
    const l = layoutAt(s)
    const devW = Math.round(336 * s)
    const devH = Math.round(12 * s)
    for (const [x, y] of dotsFor(l, 0, Math.round(l.cells / 2))) {
      expect(Number.isInteger(x) && Number.isInteger(y)).toBe(true)
      expect(x >= 0 && y >= 0 && x + l.dot <= devW && y + l.dot <= devH).toBe(true)
    }
  })

  it.each(SCALES)('%sx：两段共用一张网格——四边落在点上，已完成与未完成的顶行、底行同高', (s) => {
    const l = layoutAt(s)
    const k = Math.round(l.cells * 0.64)
    const dots = dotsFor(l, 0, k)
    const right = l.ox + l.gridW - l.dot
    const bottom = l.oy + l.gridH - l.dot
    const xs = dots.map(([x]) => x)
    const ys = dots.map(([, y]) => y)
    expect(Math.min(...xs)).toBe(l.ox)
    expect(Math.max(...xs)).toBe(right)
    // 已完成段（最左一列）与未完成段（最右一列）各自的顶行 / 底行
    const colYs = (x: number) => dots.filter(([dx]) => dx === x).map(([, y]) => y)
    for (const x of [l.ox, right]) {
      expect(Math.min(...colYs(x))).toBe(l.oy)
      expect(Math.max(...colYs(x))).toBe(bottom)
    }
    expect(Math.min(...ys)).toBe(l.oy)
    expect(Math.max(...ys)).toBe(bottom)
  })

  it('已完成段是密点（每 2 个点径一个），未完成段是稀点（每 4 个），点一样大', () => {
    const l = layoutAt(2)
    const k = 10
    const set = dotSet(l, 0, k)
    const split = k * l.period
    // 已完成段：每 filledStep 都有点
    for (let x = 0; x < split; x += l.filledStep)
      for (let y = 0; y + l.dot <= l.gridH; y += l.filledStep) expect(set.has(`${l.ox + x},${l.oy + y}`)).toBe(true)
    // 未完成段：只有 emptyStep 的倍数上有点
    for (let x = split; x + l.dot <= l.gridW; x += l.filledStep)
      for (let y = 0; y + l.dot <= l.gridH; y += l.filledStep)
        expect(set.has(`${l.ox + x},${l.oy + y}`)).toBe(x % l.emptyStep === 0 && y % l.emptyStep === 0)
  })

  it('纵向固定两格：各缩放下稀点都是 3 行、密点都是 5 行', () => {
    for (const s of SCALES) {
      const l = layoutAt(s)
      const ys = (from: number, to: number) => new Set(dotsFor(l, from, to).map(([, y]) => y)).size
      expect(ys(0, 0)).toBe(3)
      expect(ys(0, l.cells)).toBe(5)
    }
  })

  it('百分比换格数：夹在 0–100，非有限数按 0；不确定态那段至少一格', () => {
    const l = layoutAt(1)
    expect(filledCells(l, 0)).toBe(0)
    expect(filledCells(l, 100)).toBe(l.cells)
    expect(filledCells(l, 150)).toBe(l.cells)
    expect(filledCells(l, -5)).toBe(0)
    expect(filledCells(l, Number.NaN)).toBe(0)
    expect(sweepCells(l)).toBeGreaterThanOrEqual(1)
    expect(sweepCells(dotLayout(8, 12, 8))).toBe(1)
  })

  it('点径可以由外层（按屏幕 ppi）指定，比例照旧', () => {
    const l = dotLayout(420, 15, 336, 2)
    expect(l.dot).toBe(2)
    expect(l.emptyStep).toBe(8)
    expect(l.filledStep).toBe(4)
  })
})
