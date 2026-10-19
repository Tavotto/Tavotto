import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
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

  it('200 但响应体不是 JSON（代理回 HTML）：收到了响应，只请求 1 次不重试', async () => {
    projectReply = () => new Response('<html>proxy</html>', { status: 200 })
    await useProjectStore.getState().init()
    expect(projectCalls).toBe(1)
    expect(useProjectStore.getState().phase).toBe('none')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('记着旧项目 id、首次返回非 JSON → 清 id 后再请求默认项目', async () => {
    setCurrentProjectId('stale-id')
    projectReply = () =>
      projectCalls === 1 ? new Response('<html>proxy</html>', { status: 200 }) : NONE()
    await useProjectStore.getState().init()
    expect(projectCalls).toBe(2)
    expect(currentProjectId()).toBeNull()
    expect(useProjectStore.getState().phase).toBe('none')
    expect(useProjectStore.getState().project).toEqual({ open: false })
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

  it('请求在飞时被取消、随后请求成功 → init 不落 phase、不再拉列表', async () => {
    const { cancelStartupProbe } = await import('@/lib/startupProbe')
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    let listCalls = 0
    const inner = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('/api/projects')) listCalls += 1
      if (u.includes('/api/project') && !u.includes('/api/projects') && !u.includes('/readiness')) {
        projectCalls += 1
        await gate
        return NONE()
      }
      return inner(url as RequestInfo, init)
    }) as typeof fetch
    const done = useProjectStore.getState().init()
    await vi.advanceTimersByTimeAsync(0)
    expect(projectCalls).toBe(1)
    cancelStartupProbe()
    release()
    await done
    await vi.advanceTimersByTimeAsync(60_000)
    expect(projectCalls).toBe(1)
    expect(listCalls).toBe(0)
    expect(useProjectStore.getState().phase).toBe('loading')
    expect(useProjectStore.getState().project).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('项目请求已成功返回、列表请求在飞时被取消 → 列表返回后 phase 仍 loading、project 不写、不启动扫描', async () => {
    const { cancelStartupProbe } = await import('@/lib/startupProbe')
    const { useProjectScanStore } = await import('./projectScanStore')
    const startSpy = vi.spyOn(useProjectScanStore.getState(), 'start').mockResolvedValue(undefined as never)
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    let listCalls = 0
    const inner = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('/api/projects')) {
        listCalls += 1
        await gate
      }
      return inner(url as RequestInfo, init)
    }) as typeof fetch
    projectReply = NONE
    const done = useProjectStore.getState().init()
    await vi.advanceTimersByTimeAsync(0)
    expect(projectCalls).toBe(1)
    expect(listCalls).toBeGreaterThan(0)
    cancelStartupProbe()
    release()
    await done
    await vi.advanceTimersByTimeAsync(60_000)
    expect(useProjectStore.getState().phase).toBe('loading')
    expect(useProjectStore.getState().project).toBeNull()
    expect(startSpy).not.toHaveBeenCalled()
    startSpy.mockRestore()
  })
})
