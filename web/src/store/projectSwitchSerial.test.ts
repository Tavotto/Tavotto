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
import { armNoProjectRecovery, type RecentProject } from '@/lib/api'
import { startTutorial, useTutorialStore } from '@/lib/onboarding/tutorial'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
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
/** 发出过的请求 URL（判「某个请求根本没发」用） */
const sent: string[] = []
/** 只有「刷新与收藏赛跑」那条用例要把 GET recent 也挂起 */
let holdRecent = false
/** 「教程请求在路上时点别的项目」那条用例把 /api/tutorial/open 挂起 */
let holdTutorial = false

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  sent.push(u)
  const hold =
    u.includes('/api/projects/open') ||
    (u.includes('/api/projects/pinned') && init?.method === 'POST') ||
    (holdRecent && u.includes('/api/projects/recent')) ||
    (holdTutorial && u.includes('/api/tutorial/open'))
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
  sent.length = 0
  holdRecent = false
  holdTutorial = false
  // 上一条用例里故意喂坏形状的教程请求会在「busy 已置、清 busy 之前」抛出，busy 留在
  // 'open'——不重置的话下一条的 startTutorial 被防重入直接挡回，替上一条背锅
  useTutorialStore.setState({ busy: null, failure: null })
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

describe('切换进行中的「去 Picker / 返回当前」', () => {
  it('切换未完成时两者都不动 phase；完成后照常', async () => {
    const store = useProjectStore.getState()
    const sw = store.open('/figs/A')
    await settle()
    expect(useProjectStore.getState().switching).toBe(true)
    useProjectStore.getState().showPicker()
    expect(useProjectStore.getState().phase).toBe('open') // 被拦下
    useProjectStore.setState({ phase: 'none' }) // 假设用户此刻在 Picker 上
    useProjectStore.getState().returnToCurrent()
    expect(useProjectStore.getState().phase).toBe('none') // 被拦下：旧界面 + 新 pj 的那一刻

    take('/api/projects/open').release(200, { open: true, id: 'pA', name: 'A', figures_dir: '/figs/A' })
    await sw
    expect(useProjectStore.getState().switching).toBe(false)
    useProjectStore.getState().showPicker()
    expect(useProjectStore.getState().phase).toBe('none')
    useProjectStore.getState().returnToCurrent()
    expect(useProjectStore.getState().phase).toBe('open')
  })
})

describe('切换进行中点教程', () => {
  it('不发 /api/tutorial/open、不排第二次切换；切换结束后照常可开', async () => {
    const sw = useProjectStore.getState().open('/figs/A')
    await settle()
    const out = await startTutorial('help')
    expect(out.ok).toBe(false)
    expect(sent.some((u) => u.includes('/api/tutorial/open'))).toBe(false)
    take('/api/projects/open').release(200, { open: true, id: 'pA', name: 'A', figures_dir: '/figs/A' })
    await sw
    expect(useProjectStore.getState().switching).toBe(false)
    // 反证落点：不在切换中时它确实会去开（请求发得出去即可，结果不在本条的主语里）
    // mock 回的是空形状，后面的认领会失败——不关心，只等它跑完别漏到下一条用例里
    await startTutorial('help').catch(() => undefined)
    expect(sent.some((u) => u.includes('/api/tutorial/open'))).toBe(true)
  })
})

describe('失效会话里排着的收藏操作', () => {
  it('前一个撞上 409 no_project（pj 被清）：后面排着的作废、一个请求都不发', async () => {
    armNoProjectRecovery()
    const store = useProjectStore.getState()
    const a = store.togglePin('/A')
    const b = store.togglePin('/B')
    await settle()
    take('/api/projects/pinned').release(409, { error: '尚未打开项目', code: 'no_project' })
    await a
    await b
    await settle()
    expect(currentProjectId()).toBeNull() // 恢复出口确实跑了
    expect(sent.filter((u) => u.includes('/api/projects/pinned'))).toHaveLength(1)
  })

  it('入队后换了项目：同样作废', async () => {
    const op = useProjectStore.getState().togglePin('/A')
    // 同步地换 pj（排队的函数还没轮到）
    setCurrentProjectId('p-other')
    await op
    expect(sent.filter((u) => u.includes('/api/projects/pinned'))).toHaveLength(0)
  })
})

describe('教程与别的打开按点击顺序落地', () => {
  it('教程请求在路上时 switching 已亮；此时点的项目排在教程之后，最后停在它上面', async () => {
    holdTutorial = true
    const tut = startTutorial('help')
    await settle()
    expect(useProjectStore.getState().switching).toBe(true) // 请求在路上就算切换
    const b = useProjectStore.getState().open('/figs/B')
    await settle()
    expect(opened).toEqual([]) // B 排在教程后面，一个请求都没发

    take('/api/tutorial/open').release(200, {
      project: { open: true, id: 'pT', name: 'Tutorial', figures_dir: '/data/tutorial/x' },
      tutorial: { document_id: 'd_tut', document_name: 'tutorial', version: 1 },
      created: false,
    })
    await tut.catch(() => undefined) // 画布装不装得上不是本条的主语
    await settle()
    expect(opened).toEqual(['/figs/B']) // 教程整个结束之后才轮到 B
    take('/api/projects/open').release(200, { open: true, id: 'pB', name: 'B', figures_dir: '/figs/B' })
    await b
    expect(useProjectStore.getState().project?.id).toBe('pB')
    expect(useProjectStore.getState().switching).toBe(false)
  })
})
