/**
 * 意外错误的唯一汇聚点（PR A）：分类过滤、去重与风暴上界、递归保护、关着不登记、
 * trace 不含原文而发送环含原文、引擎 500 汇聚。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetDetailRings,
  buildSendPayload,
  installConsoleCapture,
  readErrorRing,
  NO_DETAIL_OPTIONS,
} from '@/diagnostics/detail'
import { readDiagnosticTrace } from '@/diagnostics'
import { __resetDiagnosticsForTests } from '@/diagnostics/store'
import { ApiError } from '@/lib/api'
import {
  MAX_FINGERPRINTS,
  MAX_PROMPTS_PER_SESSION,
  __resetUnexpectedErrors,
  __unexpectedErrorState,
  installUnexpectedErrorCapture,
  reportUnexpectedError,
  routeRule,
} from '@/lib/unexpectedErrors'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'

const SECRET = 'SECRET_PAPER_TITLE_12345'
let offConsole: (() => void) | null = null
let offReport: (() => void) | null = null

const card = () => useUnexpectedErrorStore.getState().card
const events = () => readDiagnosticTrace().filter((e) => e.type === 'ui.unexpected_error')
const throwWindow = (error: unknown, extra: Partial<ErrorEventInit> = {}) =>
  window.dispatchEvent(new ErrorEvent('error', { error, message: error instanceof Error ? error.message : String(error), ...extra }))
const reject = (reason: unknown) => {
  const ev = new Event('unhandledrejection') as Event & { reason: unknown }
  ev.reason = reason
  window.dispatchEvent(ev)
}

beforeEach(() => {
  __resetDiagnosticsForTests()
  __resetDetailRings()
  __resetUnexpectedErrors()
  useDiagSendStore.setState({ capability: { enabled: true }, open: false })
  offConsole = installConsoleCapture()
  offReport = installUnexpectedErrorCapture()
})

afterEach(() => {
  offReport?.()
  offConsole?.()
  __resetUnexpectedErrors()
  useDiagSendStore.setState({ capability: null, open: false })
  vi.unstubAllGlobals()
})

describe('常量', () => {
  it('上界集中定义', () => {
    expect(MAX_FINGERPRINTS).toBe(64)
    expect(MAX_PROMPTS_PER_SESSION).toBe(5)
  })
})

describe('分类过滤：不是意外的一律不记、不弹', () => {
  const cases: [string, () => void][] = [
    ['ResizeObserver loop', () => throwWindow(undefined, { message: 'ResizeObserver loop completed with undelivered notifications.' })],
    ['ResizeObserver loop limit', () => throwWindow(undefined, { message: 'ResizeObserver loop limit exceeded' })],
    ['跨域 Script error.', () => throwWindow(undefined, { message: 'Script error.' })],
    ['AbortError（DOMException）', () => reject(new DOMException('aborted', 'AbortError'))],
    ['ApiError（结构化失败，已有卡）', () => reject(new ApiError('boom', 400, { code: 'bad' }))],
    [
      '业务自己的错误类',
      () => {
        class PendingConflict extends Error {}
        reject(new PendingConflict('conflict'))
      },
    ],
    ['pyodide / worker 的 onerror', () => throwWindow(new TypeError('x'), { filename: 'http://h/assets/pyodide.worker-abc.js' })],
    ['非 Error 的拒绝（取消令牌）', () => reject('cancelled')],
    ['空拒绝', () => reject(undefined)],
  ]
  for (const [name, run] of cases) {
    it(name, () => {
      run()
      expect(events()).toHaveLength(0)
      expect(card()).toBeNull()
    })
  }

  it('对照：同一管线里一个真 TypeError 会弹（上面不是因为管线坏了才全空）', () => {
    throwWindow(new TypeError('cannot read x'))
    expect(events()).toHaveLength(1)
    expect(card()).not.toBeNull()
  })
})

describe('trace 与发送环的分工', () => {
  it('trace 只有闭集枚举 + hash，不含 message / stack 原文；发送环含原文', async () => {
    const err = new TypeError(`bad ${SECRET}`)
    throwWindow(err)
    const [ev] = events()
    expect(ev).toMatchObject({ source: 'window_error', error_name: 'TypeError' })
    expect(String(ev.message_hash)).toMatch(/^err:[0-9a-f]{12}$/)
    expect(JSON.stringify(readDiagnosticTrace())).not.toContain(SECRET)
    expect(Object.keys(ev).sort()).toEqual(['error_name', 'message_hash', 'seq', 'source', 'ts', 't_ms', 'type'].sort())
    // 原文在未捕获错误环里，随发送包走；导出路径（buildDiagnosticPayload）不带
    expect(readErrorRing().some((r) => r.message.includes(SECRET))).toBe(true)
    const sent = await buildSendPayload(NO_DETAIL_OPTIONS)
    expect(JSON.stringify(sent.detail.errors)).toContain(SECRET)
    expect(JSON.stringify(sent.interaction_trace)).not.toContain(SECRET)
    expect(JSON.stringify(sent.frontend_state)).not.toContain(SECRET)
  })

  it('开集错误名落到 other', () => {
    throwWindow('a thrown string', { error: 'a thrown string', message: 'a thrown string' })
    expect(events()[0]).toMatchObject({ error_name: 'other' })
  })
})

describe('去重与风暴上界', () => {
  it('同一指纹每会话只提示一次；用户关掉后不再提示', () => {
    throwWindow(new TypeError('same'))
    expect(card()).not.toBeNull()
    useUnexpectedErrorStore.getState().dismiss()
    throwWindow(new TypeError('same'))
    expect(card()).toBeNull()
    // 但每次都进 trace
    expect(events()).toHaveLength(2)
  })

  it('同时最多 1 张：轨上已有一张时，另一个指纹只记不弹', () => {
    expect(reportUnexpectedError({ source: 'window_error', error: new TypeError('a') })).toBe('prompted')
    const first = card()
    expect(reportUnexpectedError({ source: 'window_error', error: new RangeError('b') })).toBe('recorded')
    expect(card()).toBe(first)
  })

  it('每会话主动提示总数上限：第 6 个不同的错误只记环不弹', () => {
    const outcomes: string[] = []
    for (let i = 0; i < MAX_PROMPTS_PER_SESSION + 3; i++) {
      outcomes.push(reportUnexpectedError({ source: 'window_error', error: new TypeError(`distinct ${i}`) }))
      useUnexpectedErrorStore.getState().dismiss()
    }
    expect(outcomes.filter((o) => o === 'prompted')).toHaveLength(MAX_PROMPTS_PER_SESSION)
    expect(__unexpectedErrorState().prompts).toBe(MAX_PROMPTS_PER_SESSION)
    expect(events()).toHaveLength(MAX_PROMPTS_PER_SESSION + 3)
  })

  it('去重集合有上限（LRU）：风暴里指纹数不超过 MAX_FINGERPRINTS', () => {
    for (let i = 0; i < MAX_FINGERPRINTS * 3; i++) {
      reportUnexpectedError({ source: 'window_error', error: new TypeError(`storm ${i}`) })
    }
    expect(__unexpectedErrorState().fingerprints).toBe(MAX_FINGERPRINTS)
  })
})

describe('开关与递归保护', () => {
  it('发送功能关着：照记 trace 与环，不登记卡片', () => {
    useDiagSendStore.setState({ capability: null })
    throwWindow(new TypeError('closed'))
    expect(events()).toHaveLength(1)
    expect(readErrorRing()).toHaveLength(1)
    expect(card()).toBeNull()
    expect(__unexpectedErrorState()).toEqual({ fingerprints: 0, prompts: 0 })
  })

  it('发送对话框开着时抛的错：照记，不提示', () => {
    useDiagSendStore.setState({ open: true })
    throwWindow(new TypeError('inside dialog'))
    expect(events()).toHaveLength(1)
    expect(card()).toBeNull()
  })

  it('栈指回诊断模块 / 发送对话框：照记，不提示', () => {
    for (const where of ['http://h/src/diagnostics/snapshot.ts:1:1', 'DiagnosticsSendDialog.tsx:9:9']) {
      const e = new TypeError(`self ${where}`)
      e.stack = `TypeError: x\n    at f (${where})`
      throwWindow(e)
    }
    expect(events()).toHaveLength(2)
    expect(card()).toBeNull()
  })
})

describe('ErrorBoundary 来源', () => {
  it('记 trace（source=boundary）、补环（带 componentStack）、不登记卡片（App 树已死）', () => {
    const err = new Error(`render blew up ${SECRET}`)
    expect(reportUnexpectedError({ source: 'boundary', error: err, componentStack: '\n    at Foo' })).toBe('recorded')
    expect(events()[0]).toMatchObject({ source: 'boundary', error_name: 'Error' })
    const ring = readErrorRing()
    expect(ring).toHaveLength(1)
    expect(ring[0].level).toBe('boundary')
    expect(ring[0].stack).toContain('componentStack:')
    expect(ring[0].message).toContain(SECRET)
    expect(JSON.stringify(readDiagnosticTrace())).not.toContain(SECRET)
    expect(card()).toBeNull()
  })
})

describe('引擎 500 汇聚（jsonFetch）', () => {
  const stub = (status: number, body: unknown) =>
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })))

  it('只认兜底码 internal_error：记 trace + 环（方法与规整后的路由，不带 params）+ 弹卡', async () => {
    const { fetchPanels } = await import('@/lib/api')
    stub(500, { code: 'internal_error', error: `trace ${SECRET}`, params: { path: SECRET } })
    await expect(fetchPanels()).rejects.toBeInstanceOf(ApiError)
    expect(events()[0]).toMatchObject({ source: 'engine_500', error_name: 'other' })
    expect(readErrorRing().map((r) => [r.level, r.message])).toEqual([['engine_500', 'engine internal_error GET /api/panels']])
    expect(JSON.stringify([readErrorRing(), readDiagnosticTrace()])).not.toContain(SECRET)
    expect(card()).not.toBeNull()
  })

  it('具体业务码的 500、以及非 5xx 都不汇聚', async () => {
    const { fetchPanels } = await import('@/lib/api')
    stub(500, { code: 'render_failed', error: 'x' })
    await expect(fetchPanels()).rejects.toBeInstanceOf(ApiError)
    stub(409, { code: 'internal_error', error: 'x' })
    await expect(fetchPanels()).rejects.toBeInstanceOf(ApiError)
    expect(events()).toHaveLength(0)
    expect(card()).toBeNull()
  })

  it('抛出去的 ApiError 之后冒成 unhandledrejection 不重复计数', async () => {
    const { fetchPanels } = await import('@/lib/api')
    stub(500, { code: 'internal_error', error: 'x' })
    const err = await fetchPanels().catch((e) => e)
    reject(err)
    expect(events()).toHaveLength(1)
  })

  it('指纹 = 来源 + 路由规则：同一路由不同 id 只弹一次', () => {
    const r = (url: string) => reportUnexpectedError({ source: 'engine_500', request: { method: 'POST', url } })
    expect(r('/api/panels/abc123/render?x=1')).toBe('prompted')
    useUnexpectedErrorStore.getState().dismiss()
    expect(r('/api/panels/zzz999/render')).toBe('recorded')
    expect(routeRule('/api/panels/abc123/render?x=1')).toBe('/api/panels/:id/render')
  })
})
