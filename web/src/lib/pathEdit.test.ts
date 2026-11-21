/**
 * 锚点编辑的纯函数（`lib/pathEdit`，ADR 0185 M1）。核心判据：**改了曲线范围、包围盒重算之后，画面上没动的锚点逐点不动**
 * （含旋转对象）；加锚点曲线不变；平滑点拖一侧手柄另一侧共线、⌥ 拆开。
 */
import { describe, expect, it } from 'vitest'
import type { PathObject } from '@/types/document'
import {
  addAnchors,
  anchorSnapLines,
  anchorsInRect,
  averageAnchors,
  closeSubpath,
  convertAnchors,
  cutAtAnchors,
  joinSubpaths,
  lock45,
  snapXY,
  deleteAnchors,
  insertAnchor,
  moveAnchors,
  moveHandle,
  pathFromMm,
  pathToMm,
  rotatePoint,
  type MmSubpath,
  type XY,
} from './pathEdit'
import { pathGeometry, segsExtent } from './pathGeom'

const path = (over: Partial<PathObject> = {}): PathObject => ({
  id: 'p',
  type: 'path',
  x: 10,
  y: 20,
  w: 40,
  h: 30,
  strokePt: 1,
  color: '#000',
  fill: null,
  subpaths: [
    {
      closed: false,
      nodes: [{ p: [0, 1] }, { p: [0.5, 0], in: [0.25, 0], out: [0.75, 0], smooth: true }, { p: [1, 1] }],
    },
  ],
  ...over,
})

/** 一个对象上每个锚点在页面上（已旋转）的位置 */
const pagePoints = (o: PathObject): XY[] => pathToMm(o).flatMap((sp) => sp.nodes.map((n) => rotatePoint(o, n.p)))
const close = (a: XY[], b: XY[], tol = 1e-9) => {
  expect(a.length).toBe(b.length)
  a.forEach((p, i) => {
    expect(Math.abs(p[0] - b[i][0]), `x[${i}]`).toBeLessThan(tol)
    expect(Math.abs(p[1] - b[i][1]), `y[${i}]`).toBeLessThan(tol)
  })
}

/** 沿曲线采样（对象自己的框里，mm）→ 页面坐标 */
function sampleCurve(o: PathObject, n = 60): XY[] {
  const g = pathGeometry(o, o.w, o.h, 0)
  const out: XY[] = []
  let cur: XY = [0, 0]
  for (const s of g.fill) {
    if (s[0] === 'M') cur = [s[1], s[2]]
    else if (s[0] === 'L') {
      for (let i = 0; i <= n; i++) out.push([cur[0] + ((s[1] - cur[0]) * i) / n, cur[1] + ((s[2] - cur[1]) * i) / n])
      cur = [s[1], s[2]]
    } else if (s[0] === 'C') {
      for (let i = 0; i <= n; i++) {
        const t = i / n
        const u = 1 - t
        out.push([
          u * u * u * cur[0] + 3 * u * u * t * s[1] + 3 * u * t * t * s[3] + t * t * t * s[5],
          u * u * u * cur[1] + 3 * u * u * t * s[2] + 3 * u * t * t * s[4] + t * t * t * s[6],
        ])
      }
      cur = [s[5], s[6]]
    }
  }
  return out.map((p) => rotatePoint(o, [o.x + p[0], o.y + p[1]]))
}
/** 点到折线（相邻采样点连成的线段）的最近距离：比的是曲线，不是两组离散点 */
function nearest(p: XY, line: XY[]): number {
  let best = Infinity
  for (let i = 1; i < line.length; i++) {
    const [ax, ay] = line[i - 1]
    const dx = line[i][0] - ax
    const dy = line[i][1] - ay
    const l2 = dx * dx + dy * dy
    const t = l2 ? Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2)) : 0
    best = Math.min(best, Math.hypot(p[0] - ax - dx * t, p[1] - ay - dy * t))
  }
  return best
}

describe('pathToMm / pathFromMm：往返不动', () => {
  it.each([0, 37, -120])('旋转 %s°：原样往返，页面上的锚点一个不动', (deg) => {
    const o = path({ rotationDeg: deg || undefined })
    const back = pathFromMm(o, pathToMm(o))!
    close(pagePoints(back), pagePoints(o))
  })
})

describe('拖锚点：包围盒重算，没拖的锚点逐点不动', () => {
  it.each([0, 30, -75])('旋转 %s°：把中间那个往上拖出原来的框', (deg) => {
    const o = path({ rotationDeg: deg || undefined })
    const before = pagePoints(o)
    const next = pathFromMm(o, moveAnchors(pathToMm(o), [{ sp: 0, i: 1 }], [0, -12]))!
    // 框收到了新的曲线范围：比原来高
    expect(next.h).toBeGreaterThan(o.h)
    const ext = segsExtent(pathGeometry(next, 1, 1, 0).fill)!
    expect(ext.y0).toBeCloseTo(0, 9)
    expect(ext.y1).toBeCloseTo(1, 9)
    const after = pagePoints(next)
    close([after[0], after[2]], [before[0], before[2]])
  })

  it('拖动的那个跟着位移走（未旋转时就是页面位移）', () => {
    const o = path()
    const next = pathFromMm(o, moveAnchors(pathToMm(o), [{ sp: 0, i: 1 }], [3, -4]))!
    const [, mid] = pagePoints(next)
    close([mid], [[30 + 3, 20 - 4]])
  })
})

describe('手柄', () => {
  it('平滑点：拖一侧，另一侧共线、长度不变', () => {
    const subs = pathToMm(path())
    const n0 = subs[0].nodes[1]
    const lenIn = Math.hypot(n0.in[0] - n0.p[0], n0.in[1] - n0.p[1])
    const after = moveHandle(subs, { sp: 0, i: 1 }, 'out', [n0.p[0] + 5, n0.p[1] + 5], false)[0].nodes[1]
    const vin = [after.in[0] - after.p[0], after.in[1] - after.p[1]]
    expect(Math.hypot(vin[0], vin[1])).toBeCloseTo(lenIn, 9)
    // 共线且反向：in = -k·(out - p)
    const cross = vin[0] * 5 - vin[1] * 5
    expect(cross).toBeCloseTo(0, 9)
    expect(vin[0]).toBeLessThan(0)
    expect(after.smooth).toBe(true)
  })

  it('⌥：只动这一侧，另一侧不动、变尖角', () => {
    const subs = pathToMm(path())
    const before = subs[0].nodes[1]
    const after = moveHandle(subs, { sp: 0, i: 1 }, 'out', [before.p[0] + 5, before.p[1] + 5], true)[0].nodes[1]
    expect(after.in).toEqual(before.in)
    expect(after.smooth).toBe(false)
  })
})

describe('加 / 删 / 转换锚点', () => {
  it('在曲线段中间加点：曲线一点不变', () => {
    const o = path()
    const next = pathFromMm(o, insertAnchor(pathToMm(o), 0, 0, 0.37))!
    expect(next.subpaths[0].nodes).toHaveLength(4)
    const a = sampleCurve(o, 200)
    for (const p of sampleCurve(next, 50)) expect(nearest(p, a)).toBeLessThan(0.01)
  })

  it('直线段加点：无手柄的点，仍是直线', () => {
    const o = path({ subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [1, 1] }] }] })
    const subs = insertAnchor(pathToMm(o), 0, 0, 0.5)
    expect(subs[0].nodes[1]).toEqual({ p: [30, 35], in: [30, 35], out: [30, 35], smooth: false })
  })

  it('addAnchors：没选 = 每段中点；选了 = 选中锚点后面那一段；回新点的地址', () => {
    const subs = pathToMm(path())
    expect(addAnchors(subs, []).subs[0].nodes).toHaveLength(5)
    const r = addAnchors(subs, [{ sp: 0, i: 0 }])
    expect(r.subs[0].nodes).toHaveLength(4)
    expect(r.added).toEqual([{ sp: 0, i: 1 }])
    const all = addAnchors(subs, [])
    expect(all.added).toEqual([{ sp: 0, i: 1 }, { sp: 0, i: 3 }])
  })

  it('删锚点：剩不到两个点的子路径整条去掉；全删光 → null', () => {
    const o = path()
    const subs = deleteAnchors(pathToMm(o), [{ sp: 0, i: 1 }])
    expect(subs[0].nodes).toHaveLength(2)
    expect(pathFromMm(o, deleteAnchors(pathToMm(o), [{ sp: 0, i: 0 }, { sp: 0, i: 1 }]))).toBeNull()
  })

  it('闭合子路径删到只剩两个点：变成开放', () => {
    const subs: MmSubpath[] = [
      { closed: true, nodes: ([[0, 0], [1, 0], [1, 1]] as XY[]).map((p) => ({ p, in: p, out: p, smooth: false })) },
    ]
    expect(deleteAnchors(subs, [{ sp: 0, i: 2 }])[0].closed).toBe(false)
  })

  it('转换：尖角 → 平滑（手柄沿前后锚点连线、三分之一长）；全是平滑 → 尖角', () => {
    const subs = pathToMm(path())
    const smooth = convertAnchors(subs, [{ sp: 0, i: 0 }])[0].nodes[0]
    expect(smooth.smooth).toBe(true)
    expect(smooth.out).not.toEqual(smooth.p)
    const corner = convertAnchors(subs, [{ sp: 0, i: 1 }])[0].nodes[1]
    expect(corner).toMatchObject({ smooth: false, in: corner.p, out: corner.p })
  })

  it('框选锚点：编辑空间里落在框里的', () => {
    const subs = pathToMm(path())
    expect(anchorsInRect(subs, { x: 25, y: 15, w: 10, h: 10 })).toEqual([{ sp: 0, i: 1 }])
  })
})

describe('⇧ 锁 45° 与吸附', () => {
  it('lock45：投影到最近的 45° 倍数方向，轴向分量逐位归零', () => {
    expect(lock45([0, 0], [10, 1])).toEqual([10, 0])
    expect(lock45([0, 0], [1, 10])).toEqual([0, 10])
    const d = lock45([0, 0], [10, 9])
    expect(d[0]).toBeCloseTo(9.5, 9)
    expect(d[1]).toBeCloseTo(9.5, 9)
  })

  it('snapXY：x、y 各自吸到容差内最近的线', () => {
    const r = snapXY([10.3, 20.6], { xs: [0, 10, 11], ys: [20, 21] }, 0.5)
    expect(r.p).toEqual([10, 21])
    expect(r.xs).toEqual([10])
    expect(snapXY([10.3, 20.6], { xs: [12], ys: [25] }, 0.5).p).toEqual([10.3, 20.6])
  })

  it('anchorSnapLines：锚点在页面上的位置（已旋转），正在拖的不算', () => {
    const o = path()
    expect(anchorSnapLines(o, new Set(['0:1']))).toEqual({ xs: [10, 50], ys: [50, 50] })
  })
})

const nodesOf = (pts: XY[]): MmSubpath['nodes'] => pts.map((p) => ({ p, in: p, out: p, smooth: false }))

describe('剪刀', () => {
  it('开放子路径中间的锚点：断成两段，锚点一分为二', () => {
    const subs: MmSubpath[] = [{ closed: false, nodes: nodesOf([[0, 0], [1, 0], [2, 0], [3, 0]]) }]
    const [pieces] = cutAtAnchors(subs, [{ sp: 0, i: 1 }])
    expect(pieces.map((p) => p.nodes.map((n) => n.p))).toEqual([[[0, 0], [1, 0]], [[1, 0], [2, 0], [3, 0]]])
  })

  it('端点上剪：等于没剪', () => {
    const subs: MmSubpath[] = [{ closed: false, nodes: nodesOf([[0, 0], [1, 0], [2, 0]]) }]
    expect(cutAtAnchors(subs, [{ sp: 0, i: 0 }])[0]).toHaveLength(1)
  })

  it('相邻两个锚点都选了：剪掉它们之间那一段', () => {
    const subs: MmSubpath[] = [{ closed: false, nodes: nodesOf([[0, 0], [1, 0], [2, 0], [3, 0]]) }]
    const [pieces] = cutAtAnchors(subs, [{ sp: 0, i: 1 }, { sp: 0, i: 2 }])
    expect(pieces.map((p) => p.nodes.map((n) => n.p))).toEqual([[[0, 0], [1, 0]], [[2, 0], [3, 0]]])
  })

  it('闭合子路径剪一刀：从那个锚点起、到那个锚点止的开放路径，一段都不少', () => {
    const subs: MmSubpath[] = [{ closed: true, nodes: nodesOf([[0, 0], [1, 0], [1, 1], [0, 1]]) }]
    const [pieces] = cutAtAnchors(subs, [{ sp: 0, i: 2 }])
    expect(pieces).toHaveLength(1)
    expect(pieces[0].closed).toBe(false)
    expect(pieces[0].nodes.map((n) => n.p)).toEqual([[1, 1], [0, 1], [0, 0], [1, 0], [1, 1]])
  })

  it('闭合子路径剪掉一段：剩下的那几段连成一条', () => {
    const subs: MmSubpath[] = [{ closed: true, nodes: nodesOf([[0, 0], [1, 0], [1, 1], [0, 1]]) }]
    const [pieces] = cutAtAnchors(subs, [{ sp: 0, i: 0 }, { sp: 0, i: 1 }])
    expect(pieces.map((p) => p.nodes.map((n) => n.p))).toEqual([[[1, 0], [1, 1], [0, 1], [0, 0]]])
  })
})

describe('连接 / 平均', () => {
  const a: MmSubpath = { closed: false, nodes: nodesOf([[0, 0], [1, 0]]) }
  const b: MmSubpath = { closed: false, nodes: nodesOf([[3, 0], [2, 0]]) }

  it('端点没重合：钢笔拖出来没用到的手柄收回锚点，接上的是真直线（Codex 线程 4614448163）', () => {
    const pa: MmSubpath = { closed: false, nodes: [{ p: [0, 0], in: [0, 0], out: [0, 0], smooth: false }, { p: [10, 0], in: [8, 3], out: [12, 3], smooth: true }] }
    const pb: MmSubpath = { closed: false, nodes: [{ p: [20, 0], in: [18, -3], out: [22, -3], smooth: true }, { p: [30, 0], in: [30, 0], out: [30, 0], smooth: false }] }
    const r = joinSubpaths(pa, 'end', pb, 'start').nodes
    expect(r[1].out).toEqual([10, 0])
    expect(r[2].in).toEqual([20, 0])
    expect(r[1].in).toEqual([8, 3]) // 原曲线用到的那一侧留着
    expect(r[2].out).toEqual([22, -3])
  })

  it('两条开放子路径：端点没重合补一段直线；方向随 a', () => {
    expect(joinSubpaths(a, 'end', b, 'end').nodes.map((n) => n.p)).toEqual([[0, 0], [1, 0], [2, 0], [3, 0]])
    expect(joinSubpaths(a, 'start', b, 'start').nodes.map((n) => n.p)).toEqual([[1, 0], [0, 0], [3, 0], [2, 0]])
  })

  it('端点重合：合成一个锚点', () => {
    const c: MmSubpath = { closed: false, nodes: nodesOf([[1, 0], [1, 1]]) }
    expect(joinSubpaths(a, 'end', c, 'start').nodes.map((n) => n.p)).toEqual([[0, 0], [1, 0], [1, 1]])
  })

  it('同一条的两端：闭合；首尾重合就合成一个锚点', () => {
    const open: MmSubpath = { closed: false, nodes: nodesOf([[0, 0], [1, 0], [1, 1], [0, 0]]) }
    const c = closeSubpath(open)
    expect(c.closed).toBe(true)
    expect(c.nodes).toHaveLength(3)
  })

  it('平均：选中的锚点挪到平均位置，手柄跟着走', () => {
    const subs: MmSubpath[] = [
      { closed: false, nodes: [{ p: [0, 0], in: [0, 0], out: [1, 1], smooth: false }, ...nodesOf([[4, 2], [9, 9]])] },
    ]
    const out = averageAnchors(subs, [{ sp: 0, i: 0 }, { sp: 0, i: 1 }])
    expect(out[0].nodes[0].p).toEqual([2, 1])
    expect(out[0].nodes[0].out).toEqual([3, 2])
    expect(out[0].nodes[1].p).toEqual([2, 1])
    expect(out[0].nodes[2].p).toEqual([9, 9])
  })
})
