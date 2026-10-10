/**
 * 「发送问题反馈」的本机遥控器（`lib/api.ts` 里 `*DiagSend*`）：只有 `startDiagSend` 引出远程请求，
 * 且请求体恒带 `confirm: true`；备包 / 查看 / 取消 / 丢弃都是本机 POST，不带 confirm。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cancelDiagSend,
  discardDiagSend,
  fetchDiagSendBundle,
  fetchDiagSendCapability,
  prepareDiagSend,
  startDiagSend,
} from './api'

const calls: { url: string; init?: RequestInit }[] = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
        blob: () => Promise.resolve(new Blob(['zip'])),
        headers: new Headers(),
      } as Response)
    }),
  )
})
afterEach(() => vi.unstubAllGlobals())

const bodyOf = (i: number) => JSON.parse(String(calls[i].init?.body ?? 'null'))

describe('diag send api', () => {
  it('startDiagSend 恒带 confirm:true，且打到 /send', async () => {
    await startDiagSend('abc', { category: 'crash', note: 'n' })
    expect(calls[0].url).toContain('/api/diagnostics/send/abc/send')
    expect(bodyOf(0)).toEqual({ confirm: true, category: 'crash', note: 'n' })
  })

  it('其余调用都不带 confirm（它们不出网）', async () => {
    await fetchDiagSendCapability()
    await prepareDiagSend({ frontend_state: null, interaction_trace: [] })
    await fetchDiagSendBundle('abc')
    await cancelDiagSend('abc')
    await discardDiagSend('abc')
    expect(calls.map((c) => c.url.replace(/^.*\/api/, '/api'))).toEqual([
      '/api/diagnostics/send',
      '/api/diagnostics/send/prepare',
      '/api/diagnostics/send/abc/bundle',
      '/api/diagnostics/send/abc/cancel',
      '/api/diagnostics/send/abc/discard',
    ])
    for (const c of calls) expect(String(c.init?.body ?? '')).not.toContain('confirm')
  })

  it('id 进路径前被编码（不能借 id 改写路径）', async () => {
    await cancelDiagSend('../x?y=1')
    expect(calls[0].url).toContain('/api/diagnostics/send/..%2Fx%3Fy%3D1/cancel')
  })
})
