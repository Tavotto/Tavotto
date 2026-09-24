/**
 * 切项目与改收藏都是串行的（Codex #550 P1 / P2）。
 *
 *  - 切项目：`adoptOpenedProject` 先改全局 pj 再 await 换代。两次交错的话，A 的换代
 *    请求会带着 B 的 pj 发出去、最后完成的那次把 `project` 写回 A。判据：连点 A、B 时，
 *    B 的 `/api/projects/open` 要等 A **整个换代完**才发；最终停在 B；`switching` 在
 *    排队期间一直亮着、全部结束才熄。
 *  - 改收藏：每次是整张替换。判据：第一次 PUT 还没回来时再收藏一个，第二次 PUT 的
 *    列表里**含第一次的结果**（不是两次都从同一份旧列表出发）。
 *
 * 后端的响应由用例手动放行（deferred），时序完全由用例决定，不靠睡眠碰运气。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { RecentProject } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useProjectStore } from './projectStore'

interface Pending {
  url: string
  body: string
  release: (status: number, body: unknown) => void
}

/** `/api/projects/open` 与 PUT pinned 挂起等用例放行；其余立刻答一个合法的空形状 */
let pending: Pending[] = []
const opened: string[] = []

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  const hold = u.includes('/api/projects/open') || (u.includes('/api/projects/pinned') && init?.method === 'PUT')
  if (hold) {
    if (u.includes('/api/projects/open')) opened.push(JSON.parse(String(init?.body)).path)
    return new Promise<Response>((resolve) => {
      pending.push({
        url: u,
        body: String(init?.body ?? ''),
        release: (status, body) => resolve(new Response(JSON.stringify(body), { status })),
      })
    })
  }
  if (u.includes('/api/projects/recent')) return new Response('{"recent":[]}', { status: 200 })
  if (u.includes('/api/projects')) return new Response('{"projects":[],"default":null}', { status: 200 })
  if (u.includes('/api/panels')) return new Response('{"figures_dir":"/x","panels":[]}', { status: 200 })
  return new Response('{}', { status: 200 })
}) as typeof fetch

/** 让挂起的 promise 链走完（换代里有一串 await，多给几轮） */
const settle = async () => {
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0))
}
const take = (part: string) => {
  const i = pending.findIndex((p) => p.url.includes(part))
  expect(i, `没有挂起的 ${part}`).toBeGreaterThanOrEqual(0)
  return pending.splice(i, 1)[0]
}
const entry = (path: string): RecentProject => ({
  path,
  name: path.split('/').pop()!,
  last_opened: 0,
  exists: true,
  current: false,
})

beforeEach(() => {
  localStorage.clear()
  pending = []
  opened.length = 0
  useProjectStore.setState({
    phase: 'open',
    project: { open: true, id: 'p0', name: 'start', figures_dir: '/figs/start' },
    pinned: [entry('/old')],
    switching: false,
  })
  setCurrentProjectId('p0')
})

afterEach(() => setCurrentProjectId(null))

describe('切项目串行', () => {
  it('连点 A、B：B 的打开请求等 A 换代完才发；最终停在 B；switching 全程亮着', async () => {
    const store = useProjectStore.getState()
    const a = store.open('/figs/A')
    const b = store.open('/figs/B')
    await settle()
    expect(opened).toEqual(['/figs/A']) // B 还在排队，一个请求都没发
    expect(useProjectStore.getState().switching).toBe(true)

    take('/api/projects/open').release(200, { open: true, id: 'pA', name: 'A', figures_dir: '/figs/A' })
    await a
    await settle()
    expect(useProjectStore.getState().project?.id).toBe('pA')
    expect(opened).toEqual(['/figs/A', '/figs/B']) // A 整个做完之后才轮到 B
    expect(useProjectStore.getState().switching).toBe(true) // B 还在跑

    take('/api/projects/open').release(200, { open: true, id: 'pB', name: 'B', figures_dir: '/figs/B' })
    await b
    expect(useProjectStore.getState().project?.id).toBe('pB')
    expect(useProjectStore.getState().switching).toBe(false)
  })

  it('前一次失败不堵住队列，也不让 switching 常亮', async () => {
    const store = useProjectStore.getState()
    const a = store.open('/figs/A')
    const b = store.open('/figs/B')
    await settle()
    take('/api/projects/open').release(500, { error: 'boom', code: 'internal', params: {} })
    await expect(a).rejects.toBeTruthy()
    await settle()
    take('/api/projects/open').release(200, { open: true, id: 'pB', name: 'B', figures_dir: '/figs/B' })
    await b
    expect(useProjectStore.getState().project?.id).toBe('pB')
    expect(useProjectStore.getState().switching).toBe(false)
  })
})

describe('改收藏串行', () => {
  it('第一次 PUT 未回时再收藏一个：第二次 PUT 的列表含第一次的结果', async () => {
    const store = useProjectStore.getState()
    const first = store.togglePin('/A')
    const second = store.togglePin('/B')
    await settle()
    expect(pending.filter((p) => p.url.includes('pinned'))).toHaveLength(1) // 第二次还在排队

    const put1 = take('/api/projects/pinned')
    expect(JSON.parse(put1.body).paths).toEqual(['/old', '/A'])
    put1.release(200, { pinned: ['/old', '/A'].map(entry) })
    await first
    await settle()

    const put2 = take('/api/projects/pinned')
    expect(JSON.parse(put2.body).paths).toEqual(['/old', '/A', '/B'])
    put2.release(200, { pinned: ['/old', '/A', '/B'].map(entry) })
    await second
    expect(useProjectStore.getState().pinned.map((p) => p.path)).toEqual(['/old', '/A', '/B'])
  })
})
