import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCurrentProjectId } from '@/lib/session'
import { PROJECT_CONNECT_RETRY_DELAYS_MS } from '@/lib/startupProbe'
import { useProjectStore } from './projectStore'

/**
 * #1175：Windows 冻结 exe 刚起时开局 `GET /api/project` 连接层失败（fetch reject、无状态），
 * 以前一次失败就落到「选择项目」且不再重试。只对连接层失败有界重试；有 HTTP 状态的照旧。
 */
const realFetch = globalThis.fetch
let projectCalls = 0
let projectReply: () => Response | Error

beforeEach(() => {
  vi.useFakeTimers()
  setCurrentProjectId(null)
  projectCalls = 0
  useProjectStore.setState({ phase: 'loading', project: null })
  globalThis.fetch = (async (url: unknown) => {
    const u = String(url)
    if (u.includes('/api/project') && !u.includes('/api/projects') && !u.includes('/readiness')) {
      projectCalls += 1
      const r = projectReply()
      if (r instanceof Error) throw r
      return r
    }
    if (u.includes('/api/projects')) {
      return new Response(JSON.stringify({ recent: [], pinned: [], projects: [], default: null }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  }) as typeof fetch
})

afterEach(() => {
  vi.useRealTimers()
  globalThis.fetch = realFetch
  setCurrentProjectId(null)
})

const NONE = () => new Response(JSON.stringify({ open: false }), { status: 200 })

describe('开局读当前项目：连接层失败有界重试', () => {
  it('首次连接失败、第二次成功 → 照常进入（不停在 Picker）', async () => {
    projectReply = () =>
      projectCalls === 1 ? new TypeError('Failed to fetch') : NONE()
    const done = useProjectStore.getState().init()
    await vi.advanceTimersByTimeAsync(PROJECT_CONNECT_RETRY_DELAYS_MS[0])
    await done
    expect(projectCalls).toBe(2)
    expect(useProjectStore.getState().phase).toBe('none')
    expect(useProjectStore.getState().project).toEqual({ open: false })
  })

  it('一直连接失败 → 有界次数后停止并落到 Picker', async () => {
    projectReply = () => new TypeError('Failed to fetch')
    const done = useProjectStore.getState().init()
    await vi.advanceTimersByTimeAsync(60_000)
    await done
    expect(projectCalls).toBe(PROJECT_CONNECT_RETRY_DELAYS_MS.length + 1)
    expect(useProjectStore.getState().phase).toBe('none')
    expect(useProjectStore.getState().project).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('HTTP 500 有明确响应：不按连接失败重试', async () => {
    projectReply = () => new Response(JSON.stringify({ error: 'boom' }), { status: 500 })
    await useProjectStore.getState().init()
    expect(projectCalls).toBe(1)
    expect(useProjectStore.getState().phase).toBe('none')
  })

  it('等待中被取消（卸载）→ 不再请求、不落 phase、没有残留定时器', async () => {
    const { cancelStartupProbe } = await import('@/lib/startupProbe')
    projectReply = () => new TypeError('Failed to fetch')
    const done = useProjectStore.getState().init()
    await vi.advanceTimersByTimeAsync(0)
    cancelStartupProbe()
    await done
    await vi.advanceTimersByTimeAsync(60_000)
    expect(projectCalls).toBe(1)
    expect(useProjectStore.getState().phase).toBe('loading')
    expect(vi.getTimerCount()).toBe(0)
  })
})
