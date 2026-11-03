/**
 * 按版本对账（#1115）：刷新 / 切项目 / 断线的间隙里结束的任务，凭水位 `GET /api/ai/sessions/changes?since=` 补回面板。
 * 后端是一张带单调 rev 的假表（行为同 `ai_history.changes_since`：项目隔离、`rev > since`、先读 head）；
 * 其余全走真实的 `aiStore` / `handleServerEvent`，只替掉全局 `fetch`。模块内存的水位按项目分键，用例各用自己的项目 id。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { handleServerEvent } from '@/hooks/useServerEvents'
import { setCurrentProjectId } from '@/lib/session'
import { resetAiRevMemoryForTests, setAiSettledNotifier, useAiStore } from './aiStore'
import { useUiStore } from './uiStore'

const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })

type Row = {
  id: string
  project: string
  rev: number
  status: string
  final: boolean
  script: string | null
  changed: boolean
  diff: string
  prompt: string
}
let table: Row[] = []
let head = 0
let changesMode: 'ok' | 'missing' = 'ok'
let pageSize = Infinity // 假后端每页最多回几行（超过则 more）
let detailDown = false
const changesCalls: string[] = []

const writeRow = (project: string, id: string, over: Partial<Row> = {}): Row => {
  const prev = table.find((r) => r.id === id)
  const row: Row = {
    id,
    project,
    status: 'running',
    final: false,
    script: 'fig1.py',
    changed: false,
    diff: '',
    prompt: '把线条加粗',
    ...prev,
    ...over,
    rev: ++head,
  }
  table = table.filter((r) => r.id !== id).concat(row)
  return row
}
const finishOnBackend = (project: string, id: string, over: Partial<Row> = {}) =>
  writeRow(project, id, { status: 'done', final: true, changed: true, diff: '@@ d', ...over })

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  const pj = ((init?.headers ?? {}) as Record<string, string>)['X-Tavotto-Project'] ?? ''
  if (u.includes('/api/ai/sessions/changes')) {
    changesCalls.push(`${pj}|${u.split('?')[1] ?? ''}`)
    if (changesMode === 'missing') return json({ error: 'nf' }, 404)
    const raw = new URL(u, 'http://x').searchParams.get('since')
    if (raw === null) return json({ head, more: false, sessions: [] })
    const top = head
    const all = table
      .filter((r) => r.project === pj && r.rev > Number(raw) && r.rev <= top)
      .sort((a, b) => a.rev - b.rev)
    const more = all.length > pageSize
    const page = all.slice(0, pageSize)
    const sessions = page
      .map((r) => ({
        id: r.id, rev: r.rev, status: r.status, final: r.final, changed: r.changed, error: null,
        started_ms: 1000, ended_ms: r.final ? 2000 : null, client_request_id: null, provider: 'codex',
        scope: 'figure', target: '整张图', script: r.script, prompt: r.prompt, cancel_requested: false,
      }))
    return json({ head: more ? page[page.length - 1].rev : top, more, sessions })
  }
  if (u.includes('/api/ai/history')) {
    const open = table.filter((r) => r.project === pj && !r.final)
    return json({
      total: open.length,
      truncated: false,
      sessions: open.map((r) => ({
        id: r.id, provider: 'codex', model: null, effort: null, scope: 'figure', target: '整张图', script: r.script,
        prompt: r.prompt, diff: '', changed: false, status: r.status, error: null, started_ms: 1000, ended_ms: null,
        pinned: false, revert_available: true, transcript: [],
      })),
    })
  }
  const m = /\/api\/ai\/sessions\/([^/?]+)$/.exec(u.split('?')[0])
  if (m) {
    if (detailDown) return json({ error: 'boom', code: 'internal_error' }, 500)
    const r = table.find((x) => x.id === m[1])
    if (!r) return json({ error: 'nf' }, 404)
    return json({
      status: r.status, final: r.final, ended_ms: r.final ? 2000 : null, changed: r.changed, diff: r.diff,
      transcript: [{ kind: 'message', text: '改好了' }],
    })
  }
  return json({})
}) as typeof fetch

const store = () => useAiStore.getState()
const sessions = () => store().sessions
const notified: string[] = []

/** 把当前 pj 换成 `to` 并像 `projectStore` 那样 clear；回切时面板挂载 → restoreRunning */
async function visit(pj: string): Promise<void> {
  setCurrentProjectId(pj)
  store().clear()
  await store().restoreRunning()
}

beforeEach(() => {
  table = []
  head = 100
  changesMode = 'ok'
  pageSize = Infinity
  detailDown = false
  changesCalls.length = 0
  notified.length = 0
  setAiSettledNotifier((p) => notified.push(p.session))
  useAiStore.setState({
    sessions: [], submissions: {}, agent: 'codex',
    caps: { agents: [{ id: 'codex', name: 'Codex', usable: true, models: [], efforts: [] }] } as never,
  })
  useUiStore.setState({ status: null })
})
afterEach(() => {
  setAiSettledNotifier(null)
  vi.restoreAllMocks()
})

describe('A→B→A：离开期间结束的任务回来时补上结果与完成提示', () => {
  it('任务在 A 里被接受后切走，在 B 期间结束：回到 A 出现已完成的轮次，提示恰好一次', async () => {
    await visit('rv-a1') // 起点
    writeRow('rv-a1', 's1') // 任务已被接受、正在跑（本页此刻持有它也一样：clear 会把它丢掉）
    await visit('rv-b1')
    finishOnBackend('rv-a1', 's1') // 离开期间结束；本页没收到 ai.done
    await visit('rv-a1')
    expect(sessions().map((x) => x.id)).toEqual(['s1'])
    expect(sessions()[0]).toMatchObject({ status: 'done', finalized: true, changed: true, diff: '@@ d', script: 'fig1.py' })
    expect(sessions()[0].entries.map((e) => e.text)).toEqual(['改好了'])
    expect(notified).toEqual(['s1'])
    // 再回来一次：水位已越过它，本页也呈现过它——不再补、不再提示
    await visit('rv-b1')
    await visit('rv-a1')
    expect(sessions()).toEqual([])
    expect(notified).toEqual(['s1'])
  })

  it('本页已经呈现过的终态（经 ai.done 收尾）在 clear 之后不会被对账再补一次、再提示一次', async () => {
    await visit('rv-a0')
    writeRow('rv-a0', 'seen')
    await store().restoreRunning()
    finishOnBackend('rv-a0', 'seen')
    // 老后端式的 ai.done（不带 rev → 不触发对账），水位还停在起点：presented 是唯一挡住重复的东西
    handleServerEvent({ kind: 'ai.done', pj: 'rv-a0', session: 'seen', status: 'done', changed: true, diff: '@@ d', script: 'fig1.py' })
    expect(sessions()[0].finalized).toBe(true)
    await visit('rv-b0')
    await visit('rv-a0')
    expect(sessions()).toEqual([])
    expect(notified).toEqual([])
  })

  it('别的项目的结束不会出现在这个项目的面板里', async () => {
    await visit('rv-a2')
    finishOnBackend('rv-other', 'x1')
    await visit('rv-b2')
    await visit('rv-a2')
    expect(sessions()).toEqual([])
    expect(notified).toEqual([])
  })

  it('没有脚本的会话（TAF 任务等）不进助手面板', async () => {
    await visit('rv-a3')
    finishOnBackend('rv-a3', 't1', { script: null })
    await visit('rv-b3')
    await visit('rv-a3')
    expect(sessions()).toEqual([])
  })

  it('A 的水位不被 B 的拉取推过去：B 期间 A 的任务结束，B 里反复对账也不吞掉它', async () => {
    await visit('rv-a4')
    writeRow('rv-a4', 's4')
    await visit('rv-b4')
    finishOnBackend('rv-a4', 's4')
    writeRow('rv-b4', 'b-run')
    await store().reconcileChanges() // B 的水位前进到全局 head（含 A 的写）
    await visit('rv-a4')
    expect(sessions().map((x) => x.id)).toEqual(['s4'])
  })
})

describe('事件只是提示：丢了 ai.done / 本页没有这个会话，凭 rev 去对账', () => {
  it('另一个会话的 ai.done 带着比水位新的 rev：把此前丢掉的那个任务的结局一并补回', async () => {
    await visit('rv-a5')
    writeRow('rv-a5', 'lost')
    await store().restoreRunning() // 接回成占位
    expect(sessions()[0]).toMatchObject({ id: 'lost', status: 'running' })
    finishOnBackend('rv-a5', 'lost') // 它的 ai.done 丢了
    const next = finishOnBackend('rv-a5', 'next', { script: 'fig2.py' }) // 下一个任务的 ai.done 到了，rev 比水位新
    handleServerEvent({ kind: 'ai.done', pj: 'rv-a5', session: 'next', status: 'done', changed: true, diff: '@@ d', script: 'fig2.py', rev: next.rev })
    await vi.waitFor(() => expect(sessions().find((x) => x.id === 'lost')?.finalized).toBe(true))
    expect(sessions().find((x) => x.id === 'lost')).toMatchObject({ status: 'done', changed: true })
  })

  it('本页没有的会话的 ai.done（事件被丢弃）：rev 提示触发对账后补成已完成轮次', async () => {
    await visit('rv-a6')
    const row = finishOnBackend('rv-a6', 'orphan')
    handleServerEvent({ kind: 'ai.done', pj: 'rv-a6', session: 'orphan', status: 'done', changed: true, diff: '@@ d', script: 'fig1.py', rev: row.rev })
    await vi.waitFor(() => expect(sessions().map((x) => x.id)).toEqual(['orphan']))
    expect(sessions()[0].finalized).toBe(true)
    expect(notified).toEqual(['orphan'])
  })

  it('本页正常收到的 ai.done 不会因为对账被重复提示或改写', async () => {
    await visit('rv-a7')
    writeRow('rv-a7', 's7')
    await store().restoreRunning()
    const row = finishOnBackend('rv-a7', 's7')
    handleServerEvent({ kind: 'ai.done', pj: 'rv-a7', session: 's7', status: 'done', changed: true, diff: '@@ d', script: 'fig1.py', rev: row.rev })
    await vi.waitFor(() => expect(changesCalls.length).toBeGreaterThan(2))
    await store().reconcileChanges()
    expect(useUiStore.getState().status).not.toBeNull() // 事件直达路径已经提示过一次
    expect(notified).toEqual([]) // 对账的收尾路径没有再提示一次
    expect(sessions()).toHaveLength(1)
  })

  it('没有 rev 的 ai.done（老后端）不触发对账请求', async () => {
    await visit('rv-a8')
    const before = changesCalls.length
    handleServerEvent({ kind: 'ai.done', pj: 'rv-a8', session: 'zz', status: 'done', changed: false, diff: '', script: 'fig1.py' })
    await Promise.resolve()
    expect(changesCalls.length).toBe(before)
  })
})

describe('失败与边界', () => {
  it('读详情失败：不越过水位也不补半截；下一次对账再补', async () => {
    await visit('rv-a9')
    writeRow('rv-a9', 's9')
    await visit('rv-b9')
    finishOnBackend('rv-a9', 's9')
    detailDown = true
    await visit('rv-a9')
    expect(sessions()).toEqual([])
    detailDown = false
    await store().reconcileChanges()
    expect(sessions().map((x) => x.id)).toEqual(['s9'])
    expect(notified).toEqual(['s9'])
  })

  it('老后端（没有 changes 端点）：恢复照常，不抛、不补', async () => {
    changesMode = 'missing'
    writeRow('rv-a10', 'run')
    await visit('rv-a10')
    expect(sessions().map((x) => x.id)).toEqual(['run'])
    await store().reconcileChanges()
    expect(sessions()).toHaveLength(1)
  })

  it('超过一轮页数上限（5 页）的积压：自动续轮，6 页全部补上', async () => {
    await visit('rv-a14')
    pageSize = 1
    for (let i = 0; i < 6; i++) finishOnBackend('rv-a14', `bulk${i}`)
    await store().reconcileChanges()
    expect(sessions().map((x) => x.id).sort()).toEqual(['bulk0', 'bulk1', 'bulk2', 'bulk3', 'bulk4', 'bulk5'])
    expect(sessions().every((x) => x.finalized)).toBe(true)
  })

  it('对完的行水位越过它：下一次对账从新水位起，不把旧行再拉一遍', async () => {
    await visit('rv-a13')
    const row = finishOnBackend('rv-a13', 'w1')
    await store().reconcileChanges()
    expect(sessions().map((x) => x.id)).toEqual(['w1'])
    changesCalls.length = 0
    await store().reconcileChanges()
    expect(changesCalls).toEqual([`rv-a13|since=${row.rev}&pj=rv-a13`])
  })

  it('同一代次内并发触发合并成一轮在途请求，期间再来的触发会补一轮（不丢）', async () => {
    await visit('rv-a11')
    const p1 = store().reconcileChanges()
    const p2 = store().reconcileChanges()
    finishOnBackend('rv-a11', 'late')
    await Promise.all([p1, p2])
    expect(sessions().map((x) => x.id)).toEqual(['late'])
  })

  it('新开的标签页（此项目没有水位）：只取起点，不补别的页面呈现过的历史', async () => {
    finishOnBackend('rv-a12', 'before-open')
    await visit('rv-a12')
    expect(sessions()).toEqual([])
    expect(notified).toEqual([])
  })
})

describe('跨刷新：水位落 sessionStorage（D1(b)）', () => {
  /** 整页刷新：页面内存没了、sessionStorage 还在、会话列表空 */
  const refresh = async (pj: string) => {
    resetAiRevMemoryForTests()
    useAiStore.setState({ sessions: [], submissions: {} })
    await visit(pj)
  }

  it('刷新间隙里结束的任务被补成只读轮次 + 恰好一次提示；再刷新一次不重复', async () => {
    await visit('rv-r1')
    writeRow('rv-r1', 'gap')
    finishOnBackend('rv-r1', 'gap') // 用户刷新的间隙里结束，没有任何事件送达
    await refresh('rv-r1')
    expect(sessions().map((x) => x.id)).toEqual(['gap'])
    expect(sessions()[0]).toMatchObject({ status: 'done', finalized: true })
    expect(notified).toEqual(['gap'])
    await refresh('rv-r1')
    expect(sessions()).toEqual([])
    expect(notified).toEqual(['gap'])
  })

  it('刷新前已经经 ai.done 呈现过的终态，刷新后不再补一次', async () => {
    await visit('rv-r2')
    writeRow('rv-r2', 'seen2')
    await store().restoreRunning()
    finishOnBackend('rv-r2', 'seen2')
    handleServerEvent({ kind: 'ai.done', pj: 'rv-r2', session: 'seen2', status: 'done', changed: true, diff: '@@ d', script: 'fig1.py' })
    await refresh('rv-r2')
    expect(sessions()).toEqual([])
    expect(notified).toEqual([])
  })

  it('sessionStorage 抛错：退回「只取起点」，不崩、不补', async () => {
    await visit('rv-r3')
    writeRow('rv-r3', 'x3')
    finishOnBackend('rv-r3', 'x3')
    const boom = () => {
      throw new Error('denied')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(boom)
    resetAiRevMemoryForTests()
    useAiStore.setState({ sessions: [] })
    await visit('rv-r3')
    expect(sessions()).toEqual([])
    expect(notified).toEqual([])
  })

  it('库被换掉（存的水位比后端 head 还大）：按 head 重置，之后的增量照收', async () => {
    await visit('rv-r4')
    sessionStorage.setItem('tavotto.ai.revMark.rv-r4', '999999')
    resetAiRevMemoryForTests()
    useAiStore.setState({ sessions: [] })
    await visit('rv-r4') // 水位 > head：重置
    finishOnBackend('rv-r4', 'after-reset')
    await store().reconcileChanges()
    expect(sessions().map((x) => x.id)).toEqual(['after-reset'])
  })
})
