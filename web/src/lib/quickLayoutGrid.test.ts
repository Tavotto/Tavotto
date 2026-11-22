/** 网格记忆的纯函数：有效性判据、分隔线位置、拖分隔线的比例算术（只动相邻两列 / 行，夹取 0.15） */
import { describe, expect, it } from 'vitest'
import { dragGutter, gutters, MIN_PAIR_SHARE, quickLayoutValid } from './quickLayoutGrid'
import type { CanvasObject, PanelObject, QuickLayoutMemo } from '@/types/document'

/** 2 行 × 3 列里放 5 张：a b c / d e（d、e 在前两列） */
const memo = (): QuickLayoutMemo => ({
  rows: 2,
  cols: 3,
  cells: { a: [0, 0], b: [0, 1], c: [0, 2], d: [1, 0], e: [1, 1] },
  colRatios: [1, 2, 1],
  rowRatios: [1, 1],
  boxes: {
    a: [10, 10, 40, 30],
    b: [53, 10, 80, 30],
    c: [136, 10, 40, 30],
    d: [10, 43, 40, 30],
    e: [53, 43, 80, 30],
  },
  target: 'double',
  gapMm: 3,
  canvas: 'fit',
  fixSpec: true,
})

const panels = (m: QuickLayoutMemo): CanvasObject[] =>
  Object.entries(m.boxes).map(
    ([id, [x, y, w, h]]) => ({ id, type: 'panel', x, y, w, h, fileId: id, fileKind: 'pdf', nativeW: w, nativeH: h, overrides: [] }) as unknown as PanelObject,
  )

describe('quickLayoutValid', () => {
  it('每张图都在且框一致 = 有效；0.05 mm 内的浮点误差不算动过', () => {
    const m = memo()
    expect(quickLayoutValid(m, panels(m))).toBe(true)
    const near = panels(m)
    near[0].x += 0.04
    expect(quickLayoutValid(m, near)).toBe(true)
  })

  it('挪过 / 缩过任何一张 = 无效', () => {
    const m = memo()
    const moved = panels(m)
    moved[1].x += 0.06
    expect(quickLayoutValid(m, moved)).toBe(false)
    const resized = panels(m)
    resized[2].h += 1
    expect(quickLayoutValid(m, resized)).toBe(false)
  })

  it('删了一张 / 变成不是面板 = 无效；没有记忆 / 比例数与行列数不符 = 无效', () => {
    const m = memo()
    expect(quickLayoutValid(m, panels(m).slice(1))).toBe(false)
    const notPanel = panels(m)
    notPanel[0] = { ...notPanel[0], type: 'text' } as unknown as CanvasObject
    expect(quickLayoutValid(m, notPanel)).toBe(false)
    expect(quickLayoutValid(undefined, panels(m))).toBe(false)
    expect(quickLayoutValid({ ...m, colRatios: [1, 1] }, panels(m))).toBe(false)
  })
})

describe('gutters', () => {
  it('列之间 2 条竖线、行之间 1 条横线；位置 = 相邻两列（行）空隙的中线', () => {
    const g = gutters(memo())
    const col = g.filter((x) => x.axis === 'col')
    const row = g.filter((x) => x.axis === 'row')
    expect(col.map((x) => [x.index, x.pos])).toEqual([[0, 51.5], [1, 134.5]])
    expect(row.map((x) => [x.index, x.pos])).toEqual([[0, 41.5]])
    expect([col[0].from, col[0].to]).toEqual([10, 73])
    expect([row[0].from, row[0].to]).toEqual([10, 176])
  })

  it('相邻两列互相压住（没有空隙）的那一条不给', () => {
    const m = memo()
    m.boxes.b = [45, 10, 80, 30]
    m.boxes.e = [45, 43, 80, 30]
    expect(gutters(m).filter((x) => x.axis === 'col').map((x) => x.index)).toEqual([1])
  })
})

describe('dragGutter', () => {
  it('只动相邻两列：它俩合计的权重不变，别的列原样；位移按列宽之比换算', () => {
    const r = dragGutter(memo(), 'col', 0, 12)!
    // 列 0 宽 40、列 1 宽 80（合计 120）→ 列 0 变 52 = 52/120；权重合计 1+2 = 3
    expect(r.delta).toBe(12)
    expect(r.ratios[0]).toBeCloseTo((3 * 52) / 120)
    expect(r.ratios[1]).toBeCloseTo((3 * 68) / 120)
    expect(r.ratios[0] + r.ratios[1]).toBeCloseTo(3)
    expect(r.ratios[2]).toBe(1)
  })

  it('夹取：任何一边都不低于两列合计的 0.15', () => {
    const left = dragGutter(memo(), 'col', 0, -500)!
    expect(left.ratios[0] / (left.ratios[0] + left.ratios[1])).toBeCloseTo(MIN_PAIR_SHARE)
    const right = dragGutter(memo(), 'col', 0, 500)!
    expect(right.ratios[1] / (right.ratios[0] + right.ratios[1])).toBeCloseTo(MIN_PAIR_SHARE)
    // 夹取后生效的位移跟着变，预览框不越过夹取点
    expect(right.delta).toBeCloseTo(0.85 * 120 - 40)
  })

  it('预览框：被拖那一对列的图伸缩、别的图纹丝不动', () => {
    const r = dragGutter(memo(), 'col', 0, 10)!
    expect(r.boxes.a).toEqual([10, 10, 50, 30])
    expect(r.boxes.d).toEqual([10, 43, 50, 30])
    expect(r.boxes.b).toEqual([63, 10, 70, 30])
    expect(r.boxes.e).toEqual([63, 43, 70, 30])
    expect(r.boxes.c).toEqual([136, 10, 40, 30])
  })

  it('行方向同理；越界 / 空列 = null', () => {
    const r = dragGutter(memo(), 'row', 0, 5)!
    expect(r.ratios[0] + r.ratios[1]).toBeCloseTo(2)
    expect(r.boxes.a[3]).toBe(35)
    expect(r.boxes.d[1]).toBe(48)
    // 第 2 行只有 d / e，第 3 列只有 c：行 0 / 1 之间有；列 2 之后没有
    expect(dragGutter(memo(), 'col', 2, 5)).toBeNull()
    expect(dragGutter(memo(), 'col', 0, Number.NaN)).toBeNull()
  })
})
