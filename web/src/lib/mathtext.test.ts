import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { __resetMathForTests, mathBoxOf, mathPathData, subscribeMath } from './mathtext'

const BOX = { ok: true, width: 2, ascent: 0.8, descent: 0.2, segments: [['M', 0, -0.2], ['L', 2, 0.8], ['Z']] }

describe('公式排版结果的前端缓存（ADR 0152 PR 4）', () => {
  let calls: unknown[] = []
  beforeEach(() => {
    calls = []
    __resetMathForTests()
    vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body))
      calls.push(body)
      return new Response(
        JSON.stringify({ ok: true, results: body.items.map((it: { src: string }) => (it.src === 'bad' ? { ok: false, error: 'E' } : BOX)) }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('同一拍里要的合成一次请求；结果到达时通知、之后直接命中', async () => {
    const seen: number[] = []
    const off = subscribeMath(() => seen.push(1))
    expect(mathBoxOf('x', 'serif')).toBeUndefined()
    expect(mathBoxOf('bad', 'serif')).toBeUndefined()
    expect(mathBoxOf('x', 'serif')).toBeUndefined() // 在途：不重复排
    await vi.waitFor(() => expect(seen.length).toBeGreaterThan(0))
    off()
    expect(calls).toEqual([{ items: [{ src: 'x', family: 'serif' }, { src: 'bad', family: 'serif' }] }])
    expect(mathBoxOf('x', 'serif')).toEqual(BOX)
    expect(mathBoxOf('bad', 'serif')).toEqual({ ok: false, error: 'E' })
    expect(mathBoxOf('x', 'sans-serif')).toBeUndefined() // 族不同是另一条
  })

  it('轮廓段 → SVG d：y 翻成向下', () => {
    expect(mathPathData(BOX)).toBe('M 0 0.2 L 2 -0.8 Z')
  })
})
