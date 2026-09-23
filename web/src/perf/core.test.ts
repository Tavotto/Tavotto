/**
 * 性能探针热路径（ADR 0075）的两条承诺：
 *
 * 1. **没在录制时零副作用**：每个入口都是直通，不记任何东西；
 * 2. **录制时记在对的片段上**：拖动开始 / 结束切片段，span 与计数落在当前
 *    片段里，帧行的各列取自那一帧自己的累加器。
 *
 * rAF 用手动队列驱动：jsdom 没有真实的帧，靠 setTimeout 模拟会让帧间隔
 * 成为调度噪声，断言就量不到「这一帧的累加器」这个主语了。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  perfActive,
  perfCount,
  perfInput,
  perfSegmentBegin,
  perfSegmentEnd,
  perfSpan,
  perfStart,
  perfStop,
} from './core'

let rafQueue: FrameRequestCallback[] = []
let clock = 0

function frame(advanceMs: number) {
  clock += advanceMs
  const q = rafQueue
  rafQueue = []
  for (const cb of q) cb(clock)
}

beforeEach(() => {
  rafQueue = []
  clock = 1000
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    rafQueue.push(cb)
    return rafQueue.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
})

afterEach(() => {
  perfStop()
  vi.unstubAllGlobals()
})

describe('没在录制时', () => {
  it('perfSpan 原样返回、perfInput 原样调用，什么都不记', () => {
    expect(perfActive()).toBe(false)
    expect(perfSpan('doc.txn_update', () => 42)).toBe(42)
    const fn = vi.fn()
    perfInput(fn)
    expect(fn).toHaveBeenCalledTimes(1)
    perfCount('render.X')
    perfSegmentBegin('move')
    perfSegmentEnd()
    expect(perfStop()).toBeNull()
  })

  it('不挂 rAF：关着的探针不许每帧都跑一次', () => {
    perfSpan('input.handler', () => undefined)
    expect(rafQueue).toHaveLength(0)
  })
})

describe('录制中', () => {
  it('拖动切片段，span / 计数落在当前片段，片段外的不算', async () => {
    expect(perfStart()).toBe(true)
    perfCount('render.Before') // 片段之外
    perfSegmentBegin('move')
    perfSpan('doc.txn_update', () => undefined)
    perfSpan('doc.txn_update', () => undefined)
    perfCount('render.PanelView')
    perfCount('render.PanelView')
    perfCount('render.PanelView')
    perfSegmentEnd()
    const raw = perfStop()!
    expect(raw.segments).toHaveLength(1)
    const seg = raw.segments[0]
    expect(seg.kind).toBe('move')
    expect(seg.source).toBe('user')
    expect(seg.spans['doc.txn_update']?.count).toBe(2)
    expect(seg.counts).toEqual({ 'render.PanelView': 3 })
    expect(seg.end).not.toBeNull()
  })

  it('松手后的计数与 span 记进尾巴，不混进拖动中的分子', () => {
    perfStart()
    perfSegmentBegin('element')
    perfCount('render.Rulers')
    perfSpan('doc.txn_update', () => undefined)
    perfSegmentEnd()
    // 松手后 600ms 内：悬停、commit、权威 SVG 换上来引起的渲染
    perfCount('render.Rulers')
    perfCount('render.Rulers')
    perfSpan('doc.txn_update', () => undefined)
    const seg = perfStop()!.segments[0]
    expect(seg.counts).toEqual({ 'render.Rulers': 1 })
    expect(seg.tailCounts).toEqual({ 'render.Rulers': 2 })
    expect(seg.spans['doc.txn_update']?.count).toBe(1)
    expect(seg.tailSpans['doc.txn_update']?.count).toBe(1)
  })

  it('perfInput 记 handler，并在紧随其后的微任务里记 react_flush', async () => {
    perfStart()
    perfSegmentBegin('element')
    perfInput(() => {
      // 模拟 zustand set 触发的 React flush：它排的微任务在我们的之前
      queueMicrotask(() => {
        const t = performance.now()
        while (performance.now() - t < 2) {
          /* 忙等 2ms */
        }
      })
    })
    await Promise.resolve()
    await Promise.resolve()
    const seg = perfStop()!.segments[0]
    expect(seg.spans['input.handler']?.count).toBe(1)
    expect(seg.spans['input.react_flush']?.count).toBe(1)
    expect(seg.spans['input.react_flush']!.total).toBeGreaterThanOrEqual(1.5)
  })

  it('帧行：间隔取 rAF 时间戳之差，handler 列只含那一帧的累加', () => {
    perfStart()
    frame(16) // 第一帧只建立基准，不出行
    perfSegmentBegin('move')
    perfSpan('input.handler', () => undefined)
    window.dispatchEvent(new MouseEvent('pointermove'))
    window.dispatchEvent(new MouseEvent('pointermove'))
    frame(33.4)
    frame(16.6)
    const seg = perfStop()!.segments[0]
    expect(seg.frames).toHaveLength(2)
    const [a, b] = seg.frames
    expect(a[0]).toBeCloseTo(33.4, 1)
    expect(a[5]).toBe(2) // 这一帧里的 pointermove 数
    expect(b[0]).toBeCloseTo(16.6, 1)
    expect(b[5]).toBe(0) // 下一帧的累加器是新的
    expect(b[2]).toBe(0)
    expect(seg.moves).toBe(2)
  })

  it('输入延迟记在 move 所在的那一行，没有 move 的帧不配延迟', async () => {
    perfStart()
    frame(16)
    perfSegmentBegin('element')
    frame(16.7) // 空帧
    await new Promise((r) => setTimeout(r, 0))
    window.dispatchEvent(new MouseEvent('pointermove'))
    frame(16.7) // 这一帧画出了那个 move
    await new Promise((r) => setTimeout(r, 0)) // 渲染结束后的任务
    frame(16.7) // 又一个空帧
    await new Promise((r) => setTimeout(r, 0))
    const rows = perfStop()!.segments[0].frames
    expect(rows.map((r) => r[5])).toEqual([0, 1, 0])
    expect(rows[0][6]).toBeNull()
    expect(typeof rows[1][6]).toBe('number')
    expect(rows[2][6]).toBeNull()
  })

  it('空闲帧只在第一次拖动之前采（它是这台机器的基线）', () => {
    perfStart()
    frame(16)
    frame(16.7)
    frame(16.7)
    perfSegmentBegin('move')
    frame(16.7)
    perfSegmentEnd()
    const raw = perfStop()!
    expect(raw.idle_frame_ms).toEqual([16.7, 16.7])
  })

  it('perfStop 之后回到直通', () => {
    perfStart()
    perfStop()
    expect(perfActive()).toBe(false)
    perfSegmentBegin('move')
    expect(perfStop()).toBeNull()
  })
})
