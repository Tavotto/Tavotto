/**
 * 铅笔（ADR 0185 M8）的纯几何：记点、闭合、内核拟合（真 WASM）、⌥ 不简化、内核拿不到时 RDP 退路。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import type { MmSubpath, XY } from './pathEdit'
import { addPoint, isClosed, pencilSubpath, rdp, strokeLength, trimClosing } from './pencil'
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
const maxDev = (raw: readonly XY[], sp: MmSubpath) => {
  const pts = sample(sp)
  return Math.max(...raw.map((q) => Math.min(...pts.map((p) => Math.hypot(p[0] - q[0], p[1] - q[1])))))
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
