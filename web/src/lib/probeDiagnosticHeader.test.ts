/**
 * 试运行被拒（非 2xx）时的诊断引用（T04，#813 Codex P2）：错误体不是对象时后端把引用放在
 * 响应头 `X-Tavotto-Diagnostic-Ref`；`ApiError` 必须带着它，否则 catch 路径无从取回。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, probeScript } from './api'

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

const reject = (body: string, headers: Record<string, string>) => {
  globalThis.fetch = vi.fn(async () => new Response(body, { status: 500, headers })) as typeof fetch
}

describe('probeScript 被拒时 ApiError 带诊断引用头', () => {
  it('非对象错误体 + 引用头 → ApiError.diagnosticRef', async () => {
    reject('<html>boom</html>', { 'X-Tavotto-Diagnostic-Ref': 'att-9' })
    const e = await probeScript('fig.py').catch((x: unknown) => x)
    expect(e).toBeInstanceOf(ApiError)
    expect((e as ApiError).diagnosticRef).toBe('att-9')
  })

  it('没有引用头（老后端）→ null', async () => {
    reject('{"error":"x"}', {})
    const e = await probeScript('fig.py').catch((x: unknown) => x)
    expect((e as ApiError).diagnosticRef).toBeNull()
  })
})
