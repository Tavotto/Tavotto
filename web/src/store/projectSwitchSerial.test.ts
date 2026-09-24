/**
 * 切项目与改收藏都是串行的（Codex #550 P1 / P2）。
 *
 *  - 切项目：`adoptOpenedProject` 先改全局 pj 再 await 换代。两次交错的话，A 的换代
 *    请求会带着 B 的 pj 发出去、最后完成的那次把 `project` 写回 A。判据：连点 A、B 时，
 *    B 的 `/api/projects/open` 要等 A **整个换代完**才发；最终停在 B；`switching` 在
 *    排队期间一直亮着、全部结束才熄。
 *  - 改收藏：一次一个按路径描述的操作（后端对照最新列表执行）。判据：第一次还没回来
 *    时再收藏一个，第二次排队等第一次落地；回包按发出顺序落地，旧回包不盖新回包。
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

/** `/api/projects/open` 与收藏操作（POST pinned）挂起等用例放行；其余立刻答一个合法的空形状 */
let pending: Pending[] = []
/** 还没放行的全部请求（`take` 取走但断言失败没来得及放行的也在这里） */
const unreleased = new Set<Pending>()
const opened: string[] = []
/** 只有「刷新与收藏赛跑」那条用例要把 GET recent 也挂起 */
let holdRecent = false

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  const hold =
    u.includes('/api/projects/open') ||
    (u.includes('/api/projects/pinned') && init?.method === 'POST') ||
    (holdRecent && u.includes('/api/projects/recent'))
  if (hold) {
    if (u.includes('/api/projects/open')) opened.push(JSON.parse(String(init?.body)).path)
    return new Promise<Response>((resolve) => {
      const item: Pending = {
        url: u,
        body: String(init?.body ?? ''),
        release: (status, body) => {
          unreleased.delete(item)
          resolve(new Response(JSON.stringify(body), { status }))
        },
      }
      pending.push(item)
      unreleased.add(item)
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
  holdRecent = false
  useProjectStore.setState({
    phase: 'open',
    project: { open: true, id: 'p0', name: 'start', figures_dir: '/figs/start' },
    pinned: [entry('/old')],
    recent: [], // 不重置的话上一条用例写进去的会让下一条的「不含 X」断言替它背锅
    switching: false,
  })
  setCurrentProjectId('p0')
})

afterEach(async () => {
  // 队列是模块级的、跨用例共享：一条用例中途断言失败时，它挂着没放行的请求（包括
  // `take` 已经取走、断言失败没来得及放行的）会让后面
  // 所有用例排在一个永远不结束的 promise 后面，一条红变成一片红。这里一律以失败放行、
  // 把两条队列排空（反复几轮：放行一个，队列里下一个才发出来）
  for (let i = 0; i < 10 && unreleased.size; i++) {
    for (const p of [...unreleased]) p.release(599, { error: 'drain', code: 'internal', params: {} })
    await settle()
  }
  pending = []
  setCurrentProjectId(null)
})

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
  it('第一次操作未回时再收藏一个：第二次排队等第一次回来；两次各发自己的那个操作', async () => {
    const store = useProjectStore.getState()
    const first = store.togglePin('/A')
    const second = store.togglePin('/B')
    await settle()
    expect(pending.filter((p) => p.url.includes('pinned'))).toHaveLength(1) // 第二次还在排队

    const op1 = take('/api/projects/pinned')
    expect(JSON.parse(op1.body)).toEqual({ op: 'add', path: '/A' })
    op1.release(200, { pinned: ['/old', '/A'].map(entry) })
    await first
    await settle()

    const op2 = take('/api/projects/pinned')
    expect(JSON.parse(op2.body)).toEqual({ op: 'add', path: '/B' })
    op2.release(200, { pinned: ['/old', '/A', '/B'].map(entry) })
    await second
    expect(useProjectStore.getState().pinned.map((p) => p.path)).toEqual(['/old', '/A', '/B'])
  })
})

describe('收藏开关连点', () => {
  it('第一次未回时再点同一颗：第二次在轮到时按最新状态定为 remove（开了又关）', async () => {
    const store = useProjectStore.getState()
    const on = store.togglePin('/A')
    const off = store.togglePin('/A') // 此刻界面还显示「没收藏」
    await settle()
    const op1 = take('/api/projects/pinned')
    expect(JSON.parse(op1.body)).toEqual({ op: 'add', path: '/A' })
    op1.release(200, { pinned: ['/old', '/A'].map(entry) })
    await on
    await settle()
    const op2 = take('/api/projects/pinned')
    expect(JSON.parse(op2.body)).toEqual({ op: 'remove', path: '/A' })
    op2.release(200, { pinned: ['/old'].map(entry) })
    await off
    expect(useProjectStore.getState().pinned.map((p) => p.path)).toEqual(['/old'])
  })
})

describe('刷新不盖掉收藏', () => {
  it('收藏回包之后才回来的旧快照：只更新最近列表，收藏保持新的', async () => {
    holdRecent = true
    const store = useProjectStore.getState()
    const refresh = store.refreshRecent() // 发出时服务端还只有 /old
    const pin = store.togglePin('/A')
    await settle()

    take('/api/projects/pinned').release(200, { pinned: ['/old', '/A'].map(entry) })
    await pin
    expect(useProjectStore.getState().pinned.map((p) => p.path)).toEqual(['/old', '/A'])

    // 旧快照这时才回来
    take('/api/projects/recent').release(200, { recent: [entry('/r1')], pinned: [entry('/old')] })
    await refresh
    expect(useProjectStore.getState().pinned.map((p) => p.path)).toEqual(['/old', '/A'])
    expect(useProjectStore.getState().recent.map((p) => p.path)).toEqual(['/r1']) // 最近照常更新
  })

  it('期间没有收藏变化的刷新照常更新收藏', async () => {
    holdRecent = true
    const refresh = useProjectStore.getState().refreshRecent()
    await settle()
    take('/api/projects/recent').release(200, { recent: [], pinned: [entry('/srv')] })
    await refresh
    expect(useProjectStore.getState().pinned.map((p) => p.path)).toEqual(['/srv'])
  })
})

describe('列表刷新只认最新那一次', () => {
  it('先发的晚到：丢掉，不把旧项目标成当前', async () => {
    holdRecent = true
    const store = useProjectStore.getState()
    const older = store.refreshRecent() // 在项目 A 下发出
    const newer = store.refreshRecent()
    await settle()
    const [reqOld, reqNew] = pending.filter((p) => p.url.includes('/api/projects/recent'))
    reqNew.release(200, { recent: [{ ...entry('/figs/B'), current: true }], pinned: [] })
    await newer
    reqOld.release(200, { recent: [{ ...entry('/figs/A'), current: true }], pinned: [] })
    pending = pending.filter((p) => p !== reqOld && p !== reqNew)
    await older
    expect(useProjectStore.getState().recent.map((r) => r.path)).toEqual(['/figs/B'])
  })

  it('发出后换了项目：回来的那份属于旧项目，丢掉', async () => {
    holdRecent = true
    const refresh = useProjectStore.getState().refreshRecent()
    await settle()
    setCurrentProjectId('p-other') // 期间换了项目（换代本身会另发一次刷新）
    take('/api/projects/recent').release(200, { recent: [{ ...entry('/figs/A'), current: true }], pinned: [] })
    await refresh
    expect(useProjectStore.getState().recent.map((r) => r.path)).not.toContain('/figs/A')
  })
})
