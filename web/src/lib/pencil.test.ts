/**
 * 铅笔（ADR 0185 M8）的纯几何：记点、闭合、内核拟合（真 WASM）、⌥ 不简化、内核拿不到时 RDP 退路。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import type { MmSubpath, XY } from './pathEdit'
import { addPoint, fitWithKernel, isClosed, joinSegments, pencilSubpath, rdp, smoothStroke, strokeLength, tidyAnchors, trimClosing } from './pencil'
import { loadVecops, setVecopsSource, type VecopsKernel } from './vecops/kernel'

const WASM = path.resolve(process.cwd(), 'src/lib/vecops/vecops.wasm')
let kernel: VecopsKernel
beforeAll(async () => {
  setVecopsSource(async () => {
    const b = readFileSync(WASM)
    return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
  })
  kernel = (await loadVecops())!
})
afterAll(() => setVecopsSource(null))

const OPTS = { minStepMm: 0.1, closeTolMm: 1, tolMm: 0.3 }
/** 一段带抖动的波浪（mm） */
const wave = (n = 200, jitter = 0.05): XY[] =>
  Array.from({ length: n }, (_, i) => {
    const x = (i / (n - 1)) * 60
    return [x, 20 + 8 * Math.sin(x / 6) + (((i * 7919) % 13) / 13 - 0.5) * jitter] as XY
  })

/** 子路径上采样（每段三次贝塞尔 64 点） */
function sample(sp: MmSubpath): XY[] {
  const out: XY[] = []
  const n = sp.nodes.length
  for (let i = 0; i < (sp.closed ? n : n - 1); i++) {
    const a = sp.nodes[i]
    const b = sp.nodes[(i + 1) % n]
    for (let k = 0; k <= 64; k++) {
      const t = k / 64
      const u = 1 - t
      out.push([
        u * u * u * a.p[0] + 3 * u * u * t * a.out[0] + 3 * u * t * t * b.in[0] + t * t * t * b.p[0],
        u * u * u * a.p[1] + 3 * u * u * t * a.out[1] + 3 * u * t * t * b.in[1] + t * t * t * b.p[1],
      ])
    }
  }
  return out
}
/** 每个点到子路径（采样折线）的最近距离里最大的那个 */
const maxDev = (raw: readonly XY[], sp: MmSubpath) => {
  const pts = sample(sp)
  let worst = 0
  for (const [qx, qy] of raw) {
    let best = Infinity
    for (let k = 0; k + 1 < pts.length; k++) {
      const [ax, ay] = pts[k]
      const dx = pts[k + 1][0] - ax
      const dy = pts[k + 1][1] - ay
      const l2 = dx * dx + dy * dy
      const f = l2 > 0 ? Math.min(1, Math.max(0, ((qx - ax) * dx + (qy - ay) * dy) / l2)) : 0
      best = Math.min(best, Math.hypot(qx - ax - f * dx, qy - ay - f * dy))
    }
    worst = Math.max(worst, best)
  }
  return worst
}

describe('记点与闭合', () => {
  it('离上一个点太近的不要', () => {
    let pts: XY[] = [[0, 0]]
    pts = addPoint(pts, [0.05, 0], 0.1)
    expect(pts).toHaveLength(1)
    pts = addPoint(pts, [0.2, 0], 0.1)
    expect(pts).toHaveLength(2)
  })
  it('终点回到起点附近、而且够长才闭合；末尾落在闭合圈里的点丢掉', () => {
    const ring: XY[] = Array.from({ length: 41 }, (_, i) => [10 + 10 * Math.cos((i / 40) * 2 * Math.PI), 10 + 10 * Math.sin((i / 40) * 2 * Math.PI)])
    expect(isClosed(ring, 1)).toBe(true)
    expect(trimClosing(ring, 1).length).toBeLessThan(ring.length)
    expect(isClosed([[0, 0], [0.5, 0.5], [0.2, 0]], 1)).toBe(false) // 原地一个小钩
    expect(isClosed(wave(), 1)).toBe(false)
  })
})

describe('拟合', () => {
  it('内核把一笔拟合成少量贝塞尔锚点，偏离原轨迹不超过容差', () => {
    const raw = wave()
    const sp = pencilSubpath(raw, OPTS, { raw: false, kernel })!
    expect(sp.closed).toBe(false)
    expect(sp.nodes.length).toBeLessThan(20)
    expect(sp.nodes.some((n) => n.out[0] !== n.p[0] || n.out[1] !== n.p[1])).toBe(true) // 有曲线手柄
    expect(maxDev(raw, sp)).toBeLessThanOrEqual(OPTS.tolMm * 1.25)
    // 容差小一档：锚点更多、贴得更紧
    const tight = pencilSubpath(raw, { ...OPTS, tolMm: 0.05 }, { raw: false, kernel })!
    expect(tight.nodes.length).toBeGreaterThan(sp.nodes.length)
  })
  it('闭合的一笔拟合成闭合子路径', () => {
    const ring: XY[] = Array.from({ length: 81 }, (_, i) => [30 + 15 * Math.cos((i / 80) * 2 * Math.PI), 30 + 10 * Math.sin((i / 80) * 2 * Math.PI)])
    const sp = pencilSubpath(ring, OPTS, { raw: false, kernel })!
    expect(sp.closed).toBe(true)
    expect(maxDev(ring.slice(0, -2), sp)).toBeLessThanOrEqual(OPTS.tolMm * 1.25)
  })
  it('⌥（raw）：原样的折线，一个点一个锚点', () => {
    const raw = wave(50)
    const sp = pencilSubpath(raw, OPTS, { raw: true, kernel })!
    expect(sp.nodes).toHaveLength(50)
    expect(sp.nodes.every((n) => n.in === n.p && n.out === n.p)).toBe(true)
  })
  it('内核拿不到：退回 RDP 折线，偏离不超过容差', () => {
    const raw = wave()
    const sp = pencilSubpath(raw, OPTS, { raw: false, kernel: null })!
    expect(sp.nodes.length).toBeLessThan(raw.length)
    expect(maxDev(raw, sp)).toBeLessThanOrEqual(OPTS.tolMm + 1e-9)
    expect(rdp([[0, 0], [1, 0.01], [2, 0]], 0.1)).toEqual([[0, 0], [2, 0]])
  })
  it('原地点一下（太短）不画东西', () => {
    expect(pencilSubpath([[0, 0], [0.1, 0]], OPTS, { raw: false, kernel })).toBeNull()
    expect(strokeLength([[0, 0], [3, 4]])).toBe(5)
  })
})

describe('锚点不挤：拟合前平滑、拟合后在峰谷切锚点并去掉多余的', () => {
  // 屏幕像素 → mm（与画布 100% 缩放同量级）；参数与 pencilController 一致：容差 2 px、平滑 1 px / ±3 px、锚点至少隔 6 px
  const PX = 0.25
  const FULL = { minStepMm: 0.75 * PX, closeTolMm: 8 * PX, tolMm: 2 * PX, smoothStepMm: PX, smoothHalfMm: 3 * PX, minGapMm: 6 * PX }
  /** 手画的正弦：420 px 长、振幅 40 px、两个半周期多一点（5 个峰谷），带 0.6 px 的锯齿抖动 */
  const sine: XY[] = Array.from({ length: 140 }, (_, i) => [(i / 139) * 420 * PX, (40 * Math.sin(i / 9) + (i % 3) * 0.6) * PX])
  const peaks = [1, 3, 5, 7, 9].map((k) => sine[Math.round((k * Math.PI * 9) / 2)])
  const gaps = (sp: MmSubpath) => sp.nodes.slice(1).map((q, i) => Math.hypot(q.p[0] - sp.nodes[i].p[0], q.p[1] - sp.nodes[i].p[1]))

  it('同一条正弦：一个峰谷一个锚点（加两端不超过 9 个），相邻锚点至少隔 6 px，峰谷上都有锚点', () => {
    const sp = pencilSubpath(sine, FULL, { raw: false, kernel })!
    expect(sp.nodes.length).toBeLessThanOrEqual(9)
    expect(Math.min(...gaps(sp))).toBeGreaterThanOrEqual(6 * PX)
    for (const pk of peaks) expect(Math.min(...sp.nodes.map((q) => Math.hypot(q.p[0] - pk[0], q.p[1] - pk[1])))).toBeLessThan(6 * PX)
    // 偏离：对平滑后的轨迹不超过容差；对原始轨迹再加上抖动那 0.6 px
    expect(maxDev(smoothStroke(sine, false, FULL), sp)).toBeLessThanOrEqual(FULL.tolMm * 1.05)
    expect(maxDev(sine, sp)).toBeLessThanOrEqual(FULL.tolMm + 0.6 * PX)
  })
  it('不整理时同一笔锚点挤在峰谷两侧（对照：整理确实起作用）', () => {
    const plain = pencilSubpath(sine, { ...FULL, minGapMm: undefined, smoothStepMm: undefined }, { raw: false, kernel })!
    expect(plain.nodes.length).toBeGreaterThan(12)
  })
  it('整理只去锚点不走样：对整理前的曲线偏离不超过整理那份容差；并得越多也不会越并越歪', () => {
    const long: XY[] = Array.from({ length: 500 }, (_, i) => [(i / 499) * 500 * PX, (40 * Math.sin(i / 9) + 15 * Math.sin(i / 2.3)) * PX])
    const fit = fitWithKernel(kernel, smoothStroke(long, false, FULL), false, 1.2 * PX)!
    const tidy = tidyAnchors(fit, 6 * PX, 0.8 * PX)
    expect(tidy.nodes.length).toBeLessThan(fit.nodes.length)
    expect(maxDev(sample(fit), tidy)).toBeLessThanOrEqual(0.8 * PX * 1.05)
  })
  it('一串几乎共线的短折线（缓缓鼓起 3 倍容差）：并完对着原来的折线量，鼓包不会被一点点拉平', () => {
    const tol = 0.1
    const nodes = Array.from({ length: 121 }, (_, i) => {
      const p: XY = [i * 0.25, 3 * tol * Math.sin((i / 120) * Math.PI)]
      return { p, in: p, out: p, smooth: false }
    })
    const before: MmSubpath = { closed: false, nodes }
    const tidy = tidyAnchors(before, 0.01, tol)
    expect(tidy.nodes.length).toBeLessThan(nodes.length)
    expect(maxDev(nodes.map((q) => q.p), tidy)).toBeLessThanOrEqual(tol * 1.05)
  })
  it('整笔的偏离：拟合与整理合起来，对平滑后的轨迹不超过容差（容差是分着用的）', () => {
    const long: XY[] = Array.from({ length: 500 }, (_, i) => [(i / 499) * 500 * PX, (40 * Math.sin(i / 9) + 15 * Math.sin(i / 2.3)) * PX])
    const sp = pencilSubpath(long, FULL, { raw: false, kernel })!
    expect(maxDev(smoothStroke(long, false, FULL), sp)).toBeLessThanOrEqual(FULL.tolMm * 1.02)
  })
  it('闭合的一笔：整理后仍闭合，四个极值点上各有锚点', () => {
    const ring: XY[] = Array.from({ length: 120 }, (_, i) => [(90 * Math.cos((i / 118) * 2 * Math.PI)) * PX, (70 * Math.sin((i / 118) * 2 * Math.PI)) * PX])
    const sp = pencilSubpath(ring, FULL, { raw: false, kernel })!
    expect(sp.closed).toBe(true)
    expect(sp.nodes.length).toBeLessThanOrEqual(6)
    for (const pk of [[90, 0], [0, 70], [-90, 0], [0, -70]]) expect(Math.min(...sp.nodes.map((q) => Math.hypot(q.p[0] / PX - pk[0], q.p[1] / PX - pk[1])))).toBeLessThan(6)
  })
  it('两段并不成一段（中间是尖角）就不并', () => {
    const a = { p: [0, 0] as XY, in: [0, 0] as XY, out: [1, 0] as XY, smooth: false }
    const c = { p: [4, 0] as XY, in: [3, 0] as XY, out: [4, 0] as XY, smooth: false }
    const pts: XY[] = [[0, 0], [1, 1], [2, 2], [3, 1], [4, 0]]
    expect(joinSegments(a, c, pts, 0.1)).toBeNull()
    expect(joinSegments(a, c, [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]], 0.1)).not.toBeNull()
  })
})
