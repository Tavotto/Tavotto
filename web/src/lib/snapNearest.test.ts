/**
 * 智能对齐内核（ADR 0128 §一）：`snapMoveNearest` 的挑法与「吸住后保持」，以及 manifest 元素 → 吸附类型。
 *
 * 钉住的事实：X / Y 各自独立；容差内先比档位、同档比距离；配对过滤能把线挡在外面；
 * 吸住的一对在 2 倍容差内不换、超过就放开重挑；两条一样近时不来回跳。
 */
import { describe, expect, it } from 'vitest'

import { snapKindOf } from './elementGeom'
import {
  createSnapHold,
  snapMoveNearest,
  type SnapKind,
  type SnapLine,
  type SnapLineCandidates,
} from './geometry'

const line = (v: number, kind: SnapKind = 'text', gid = `g${v}`): SnapLine => ({
  v,
  kind,
  edge: 0,
  gid,
})
const cands = (xs: SnapLine[], ys: SnapLine[] = []): SnapLineCandidates => ({ xs, ys })
// 10 宽 10 高的框，左边在 x
const box = (x: number, y = 0) => ({ x, y, w: 10, h: 10 })
const TOL = 2

describe('snapMoveNearest：挑法', () => {
  it('同档比距离：近的赢，与表里的顺序无关', () => {
    const a = line(100)
    const b = line(101.5)
    for (const xs of [[a, b], [b, a]]) {
      const r = snapMoveNearest(box(101), cands(xs), TOL)
      expect(r.x?.line).toBe(b)
      expect(r.x?.selfEdge).toBe(0)
      expect(r.dx).toBeCloseTo(0.5)
      expect(r.guideXs).toEqual([101.5])
    }
  })

  it('三条边里离线最近的那条胜出（中线）', () => {
    const r = snapMoveNearest(box(100), cands([line(105.5), line(101.9)]), TOL)
    expect(r.x?.selfEdge).toBe(1)
    expect(r.x?.line.v).toBe(105.5)
  })

  it('X / Y 各自独立：一轴吸上不影响另一轴', () => {
    const r = snapMoveNearest(box(100, 50), cands([line(100.5)], [line(80)]), TOL)
    expect(r.x).not.toBeNull()
    expect(r.y).toBeNull()
    expect(r.dy).toBe(0)
    expect(r.guideYs).toEqual([])
  })

  it('档位优先于距离：远一点但档位更高的赢', () => {
    const near = line(100.1, 'axes')
    const far = line(101.5, 'page')
    const rank = (_e: number, l: SnapLine) => (l.kind === 'page' ? 0 : 1)
    const r = snapMoveNearest(box(100), cands([near, far]), TOL, { rank })
    expect(r.x?.line).toBe(far)
    // 缺省全 0：回到只比距离
    expect(snapMoveNearest(box(100), cands([near, far]), TOL).x?.line).toBe(near)
  })

  it('配对过滤：只配同一条边', () => {
    const lines = [{ ...line(100.2), edge: 2 as const }, { ...line(101.2), edge: 0 as const }]
    const pair = (selfEdge: number, l: SnapLine) => l.edge === selfEdge
    const r = snapMoveNearest(box(100), cands(lines), TOL, { pair })
    // 更近的那条是右边线，配不上自身左边；只剩 edge=0 的
    expect(r.x?.line.v).toBe(101.2)
    expect(r.x?.selfEdge).toBe(0)
  })

  it('容差外不吸', () => {
    const r = snapMoveNearest(box(100), cands([line(103)]), TOL)
    expect(r.x).toBeNull()
    expect(r.dx).toBe(0)
  })
})

describe('snapMoveNearest：吸住后保持', () => {
  it('2 倍容差内不换：另一条线更近了也继续吸原来那条', () => {
    const a = line(100)
    const b = line(104)
    const hold = createSnapHold()
    expect(snapMoveNearest(box(101), cands([a, b]), TOL, { hold }).x?.line).toBe(a)
    // 框移到 102.5：b 只差 1.5、a 差 2.5（已出容差但没到 2×）
    const r = snapMoveNearest(box(102.5), cands([a, b]), TOL, { hold })
    expect(r.x?.line).toBe(a)
    expect(r.dx).toBeCloseTo(-2.5)
  })

  it('超过 2 倍容差放开，重挑最近的', () => {
    const a = line(100)
    const b = line(104)
    const hold = createSnapHold()
    snapMoveNearest(box(101), cands([a, b]), TOL, { hold })
    const r = snapMoveNearest(box(104.2), cands([a, b]), TOL, { hold })
    expect(r.x?.line).toBe(b)
    expect(hold.x?.idx).toBe(1)
  })

  it('放开后没有可吸的线：hold 清空，再回来要重新进容差才吸', () => {
    const a = line(100)
    const hold = createSnapHold()
    snapMoveNearest(box(101), cands([a]), TOL, { hold })
    expect(snapMoveNearest(box(110), cands([a]), TOL, { hold }).x).toBeNull()
    expect(hold.x).toBeNull()
    // 回到 3（> 容差、< 2×）：已经放开过，不再吸
    expect(snapMoveNearest(box(103), cands([a]), TOL, { hold }).x).toBeNull()
  })

  it('两条一样近时不来回跳', () => {
    const a = line(100)
    const b = line(103)
    const hold = createSnapHold()
    const picks: SnapLine[] = []
    // 在两条线中点附近来回小幅移动
    for (const x of [101, 101.5, 102, 101.4, 101.6, 101.5, 101.2]) {
      const r = snapMoveNearest(box(x), cands([a, b]), TOL, { hold })
      picks.push(r.x!.line)
    }
    expect(new Set(picks).size).toBe(1)
    // 对照：不带 hold 会换
    const free = [101, 102, 101.4].map((x) => snapMoveNearest(box(x), cands([a, b]), TOL).x!.line)
    expect(new Set(free).size).toBe(2)
  })

  it('保持的是（自身边, 线）这一对，不是浮点值：同坐标的两条线是两条线', () => {
    const a = line(100, 'text', 'a')
    const b = line(100, 'axes', 'b')
    const hold = createSnapHold()
    expect(snapMoveNearest(box(100.5), cands([a, b]), TOL, { hold }).x?.line).toBe(a)
    expect(hold.x).toEqual({ selfEdge: 0, idx: 0 })
  })

  it('X / Y 各保各的', () => {
    const hold = createSnapHold()
    const c = cands([line(100)], [line(50)])
    snapMoveNearest(box(100.5, 50.5), c, TOL, { hold })
    const r = snapMoveNearest(box(103, 50.2), c, TOL, { hold })
    expect(r.x?.line.v).toBe(100)
    expect(r.y?.line.v).toBe(50)
  })

  it('保持的一对不再满足配对过滤时放开', () => {
    const a = { ...line(100), edge: 0 as const }
    const hold = createSnapHold()
    snapMoveNearest(box(100.5), cands([a]), TOL, { hold, pair: () => true })
    const r = snapMoveNearest(box(101), cands([a]), TOL, { hold, pair: () => false })
    expect(r.x).toBeNull()
  })
})

describe('snapKindOf：manifest 元素 → 吸附类型', () => {
  const k = (gid: string, role: string, is_colorbar?: boolean) => snapKindOf({ gid, role, is_colorbar })
  it('role 直接映射', () => {
    expect(k('axes_0', 'axes')).toBe('axes')
    expect(k('axes_0.legend', 'legend')).toBe('legend')
    expect(k('axes_0.title', 'title')).toBe('title')
    expect(k('axes_0.texts_0', 'text')).toBe('text')
    expect(k('figure', 'figure')).toBe('figure')
    expect(k('axes_0.lines_0', 'line')).toBe('other')
  })
  it('轴标题看 gid 后缀，不看 role', () => {
    expect(k('axes_0.xlabel', 'text')).toBe('xlabel')
    expect(k('axes_0.ylabel', 'text')).toBe('ylabel')
    expect(k('axes_3.xlabel', 'title')).toBe('xlabel')
  })
  it('色条看 is_colorbar（role 仍是 axes）', () => {
    expect(k('axes_1', 'axes', true)).toBe('colorbar')
    expect(k('axes_1', 'axes', false)).toBe('axes')
  })
})
