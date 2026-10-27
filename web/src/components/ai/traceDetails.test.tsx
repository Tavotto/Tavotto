/**
 * 「执行记录」折叠（ADR 0151 PR3）：会话结束后最后一次取快照失败时，不能拿运行中的旧快照继续往前画（Codex #1454）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ExecTraceDto } from '@/lib/api'

const fetchExecTrace = vi.fn()
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), fetchExecTrace: (...a: unknown[]) => fetchExecTrace(...a) }))

const toWaterfallCalls = vi.fn()
vi.mock('@/lib/execTrace', async (orig) => {
  const real = await orig<typeof import('@/lib/execTrace')>()
  return {
    ...real,
    toWaterfall: (...a: Parameters<typeof real.toWaterfall>) => {
      toWaterfallCalls()
      return real.toWaterfall(...a)
    },
  }
})

const { TraceDetails } = await import('./TraceDetails')

function snap(status: 'running' | 'ok'): ExecTraceDto {
  return {
    schema_version: 1,
    trace_id: 'a'.repeat(32),
    operation_kind: 'ai_task',
    started_at: Date.now() / 1000 - 2,
    ended_at: status === 'running' ? null : Date.now() / 1000,
    status,
    error_code: null,
    root_span_id: 'root',
    first_failure: null,
    truncated: false,
    dropped_count: 0,
    complete: status !== 'running',
    spans: [
      {
        span_id: 'root',
        parent_span_id: null,
        name: 'task',
        kind: 'task',
        start_offset_ms: 0,
        end_offset_ms: status === 'running' ? null : 2000,
        duration_ms: status === 'running' ? null : 2000,
        status,
        error_code: null,
        attempt: 1,
        timing: 'measured',
        attributes: {},
        usage: null,
      },
    ],
    events: [],
    last_seq: 0,
  }
}

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  fetchExecTrace.mockReset()
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

const flush = () => act(async () => {
  await Promise.resolve()
  await Promise.resolve()
})

describe('TraceDetails', () => {
  it('默认收起、不请求；展开才取', async () => {
    fetchExecTrace.mockResolvedValue({ available: true, trace: snap('ok') })
    await act(async () => root.render(<TraceDetails taskRef="s1" pj="pj" running={false} />))
    expect(fetchExecTrace).not.toHaveBeenCalled()
    await act(async () => host.querySelector<HTMLButtonElement>('[data-ai-trace-toggle]')!.click())
    await flush()
    expect(fetchExecTrace).toHaveBeenCalledWith('ai_task', 's1', 'pj')
    expect(host.querySelector('[data-ai-trace-state="ok"]')).not.toBeNull()
  })

  it('导出诊断只在任务结束后给；运行中的半份不交出去', async () => {
    fetchExecTrace.mockResolvedValue({ available: true, trace: snap('running') })
    await act(async () => root.render(<TraceDetails taskRef="s2" pj="pj" running />))
    await act(async () => host.querySelector<HTMLButtonElement>('[data-ai-trace-toggle]')!.click())
    await flush()
    expect(host.querySelector('[data-ai-trace-export]')).toBeNull()
    fetchExecTrace.mockResolvedValue({ available: true, trace: snap('ok') })
    await act(async () => root.render(<TraceDetails taskRef="s2" pj="pj" running={false} settledKey />))
    await flush()
    expect(host.querySelector('[data-ai-trace-export="idle"]')).not.toBeNull()
  })

  it('会话结束后最后一次取失败：明说过期、给重试，不当「运行中」往前走；重试拿到终局', async () => {
    fetchExecTrace.mockResolvedValue({ available: true, trace: snap('running') })
    await act(async () => root.render(<TraceDetails taskRef="s1" pj="pj" running settledKey={false} />))
    await act(async () => host.querySelector<HTMLButtonElement>('[data-ai-trace-toggle]')!.click())
    await flush()
    expect(host.querySelector('[data-ai-trace-state="running"]')).not.toBeNull()
    fetchExecTrace.mockRejectedValueOnce(new Error('offline'))
    await act(async () => root.render(<TraceDetails taskRef="s1" pj="pj" running={false} settledKey={true} />))
    await flush()
    expect(host.querySelector('[data-ai-trace-stale]')).not.toBeNull()
    fetchExecTrace.mockResolvedValue({ available: true, trace: snap('ok') })
    await act(async () => host.querySelector<HTMLButtonElement>('[data-ai-trace-stale] button')!.click())
    await flush()
    expect(host.querySelector('[data-ai-trace-stale]')).toBeNull()
    expect(host.querySelector('[data-ai-trace-state="ok"]')).not.toBeNull()
  })

  it('运行中再取到内容相同的快照：沿用同一份对象，不重建', async () => {
    const first = snap('running')
    fetchExecTrace.mockResolvedValue({ available: true, trace: first })
    await act(async () => root.render(<TraceDetails taskRef="s3" pj="pj" running />))
    await act(async () => host.querySelector<HTMLButtonElement>('[data-ai-trace-toggle]')!.click())
    await flush()
    const builds = toWaterfallCalls.mock.calls.length
    fetchExecTrace.mockResolvedValue({ available: true, trace: JSON.parse(JSON.stringify(first)) })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100))
    })
    await flush()
    expect(fetchExecTrace.mock.calls.length).toBeGreaterThan(1)
    // 内容相同的快照不重建展示模型（运行中的「此刻」只经 now 走，不触发 toWaterfall）
    expect(toWaterfallCalls.mock.calls.length).toBe(builds)
  })
})

