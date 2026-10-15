/**
 * 运行中会话的恢复（PR4b）：刷新 / SSE 重连 / A→B→A 回切之后，后端还在跑的会话接回来作只读占位，
 * 锁住同一脚本的发送；之后的 `ai.delta` / `ai.done` 认领到它上面；结束后转成普通终态。
 * 全部走真实的 `fetchAiHistory` / `fetchAiSession`（只替掉全局 `fetch`）。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { handleServerEvent } from '@/hooks/useServerEvents'
import { setCurrentProjectId } from '@/lib/session'
import { isSessionOf, useAiStore } from './aiStore'
import { useUiStore } from './uiStore'

const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })

type Row = Record<string, unknown>
const row = (over: Row = {}): Row => ({
  id: 's-run', provider: 'codex', model: null, effort: null, scope: 'figure', target: '整张图',
  script: 'fig1.py', prompt: '把线条加粗', diff: '', changed: false, status: 'running',
  error: null, started_ms: 1000, ended_ms: null, pinned: false, revert_available: true, transcript: [],
  ...over,
})
let history: Record<string, Row[]> = {} // pj → 运行中清单
let detail: Record<string, Row> = {}
const urls: string[] = []

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  urls.push(u)
  const pj = ((init?.headers ?? {}) as Record<string, string>)['X-Tavotto-Project'] ?? new URL(u, 'http://x').searchParams.get('pj') ?? ''
  if (u.includes('/api/ai/history')) {
    expect(u).toContain('status=running')
    return json({ total: 0, sessions: history[pj] ?? [] })
  }
  const m = /\/api\/ai\/sessions\/([^/?]+)$/.exec(u.split('?')[0])
  if (m) return detail[m[1]] ? json(detail[m[1]]) : json({ error: 'nf' }, 404)
  if (u.includes('/cancel')) return json({ ok: true })
  return json({})
}) as typeof fetch

const panel = { id: 'p1', fileId: 'Fig1.pdf', script: 'fig1.py' }
const sessions = () => useAiStore.getState().sessions

beforeEach(() => {
  history = {}
  detail = {}
  urls.length = 0
  setCurrentProjectId('pa')
  useAiStore.setState({
    sessions: [], submissions: {}, agent: 'codex',
    caps: { agents: [{ id: 'codex', name: 'Codex', usable: true, models: [], efforts: [] }] } as never,
  })
  useUiStore.setState({ status: null })
})

describe('接回运行中的会话', () => {
  it('后端有一个在跑的：占位出现（running、restored、记着开始时间 / 脚本 / 指令），并属于对应面板（锁发送靠它）', async () => {
    history.pa = [row()]
    await useAiStore.getState().restoreRunning()
    const [s] = sessions()
    expect(s).toMatchObject({ id: 's-run', status: 'running', restored: true, startedAt: 1000, script: 'fig1.py', prompt: '把线条加粗', project: 'pa' })
    expect(isSessionOf(s, panel)).toBe(true) // 同脚本的面板都看得到它 → runningHere 为真 → 发送被锁
    expect(isSessionOf(s, { id: 'p9', fileId: 'Other.pdf', script: 'other.py' })).toBe(false) // 别的脚本不受影响
  })

  it('重复调用（重连 + 面板挂载）不会造出第二个占位', async () => {
    history.pa = [row()]
    await Promise.all([useAiStore.getState().restoreRunning(), useAiStore.getState().restoreRunning()])
    await useAiStore.getState().restoreRunning()
    expect(sessions()).toHaveLength(1)
  })

  it('对照：后端没有在跑的，什么都不加', async () => {
    await useAiStore.getState().restoreRunning()
    expect(sessions()).toEqual([])
  })

  it('之后到的 ai.delta 追加到占位上；ai.done 收尾（终态、diff、changed）并提示，锁随 status 解开', async () => {
    history.pa = [row()]
    await useAiStore.getState().restoreRunning()
    handleServerEvent({ kind: 'ai.delta', pj: 'pa', session: 's-run', text: '改好了', kindOf: 'message' })
    expect(sessions()[0].entries.map((e) => e.text)).toEqual(['改好了'])
    handleServerEvent({ kind: 'ai.done', pj: 'pa', session: 's-run', status: 'done', changed: true, diff: 'D', script: 'fig1.py' })
    expect(sessions()[0]).toMatchObject({ status: 'done', changed: true, diff: 'D', finalized: true, restored: true })
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('迟到 / 重复的 ai.done 不倒退：终态后再来一条 failed 不改写', async () => {
    history.pa = [row()]
    await useAiStore.getState().restoreRunning()
    handleServerEvent({ kind: 'ai.done', pj: 'pa', session: 's-run', status: 'done', changed: false, diff: '', script: 'fig1.py' })
    handleServerEvent({ kind: 'ai.done', pj: 'pa', session: 's-run', status: 'failed', changed: false, diff: '', script: 'fig1.py', error: 'late' })
    expect(sessions()[0].status).toBe('done')
  })

  it('拉历史的途中到的事件不丢：占位建好后回放', async () => {
    history.pa = [row()]
    const p = useAiStore.getState().restoreRunning()
    handleServerEvent({ kind: 'ai.done', pj: 'pa', session: 's-run', status: 'done', changed: true, diff: 'D', script: 'fig1.py' })
    await p
    expect(sessions()[0]).toMatchObject({ status: 'done', changed: true })
  })

  it('中止：占位上的取消走 cancel（发往它所属的项目）', async () => {
    history.pa = [row()]
    await useAiStore.getState().restoreRunning()
    await useAiStore.getState().cancel('s-run')
    expect(urls.some((u) => u.includes('/api/ai/sessions/s-run/cancel'))).toBe(true)
    expect(sessions()[0].status).toBe('cancelled')
  })
})

describe('按项目隔离 / A→B→A', () => {
  it('切到 B：A 的占位不在（换代清空），B 只接回 B 自己的；切回 A 又接回 A 的', async () => {
    history.pa = [row({ id: 's-a' })]
    history.pb = [row({ id: 's-b', script: 'figb.py' })]
    await useAiStore.getState().restoreRunning()
    expect(sessions().map((s) => s.id)).toEqual(['s-a'])
    setCurrentProjectId('pb')
    useAiStore.getState().clear()
    expect(sessions()).toEqual([])
    await useAiStore.getState().restoreRunning()
    expect(sessions().map((s) => s.id)).toEqual(['s-b'])
    setCurrentProjectId('pa')
    useAiStore.getState().clear()
    await useAiStore.getState().restoreRunning()
    expect(sessions().map((s) => s.id)).toEqual(['s-a'])
  })

  it('拉历史的途中换了项目：结果丢弃，不落进新项目', async () => {
    history.pa = [row({ id: 's-a' })]
    const p = useAiStore.getState().restoreRunning()
    setCurrentProjectId('pb')
    useAiStore.getState().clear()
    await p
    expect(sessions()).toEqual([])
  })
})

describe('断线期间结束的会话收尾', () => {
  it('本地还是 running、后端的运行中清单里没有它：以后端为准收尾（补上 diff / 转录），并提示一次', async () => {
    useAiStore.setState({
      sessions: [{
        id: 's-local', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py',
        panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
        status: 'running', changed: false, diff: '', startedAt: 1,
      }],
    })
    detail['s-local'] = { status: 'done', changed: true, diff: 'DIFF', transcript: [{ kind: 'message', text: '好了' }] }
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0]).toMatchObject({ status: 'done', changed: true, diff: 'DIFF', finalized: true })
    expect(sessions()[0].entries[0].text).toBe('好了')
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('后端说 interrupted（重启被中断）：按失败收尾，不再当成在跑', async () => {
    useAiStore.setState({
      sessions: [{
        id: 's-local', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py',
        panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
        status: 'running', changed: false, diff: '', startedAt: 1,
      }],
    })
    detail['s-local'] = { status: 'interrupted', changed: null, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0].status).toBe('failed')
  })

  it('详情读不出状态（空体 / 老后端）：不当成结束', async () => {
    useAiStore.setState({
      sessions: [{
        id: 's-local', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py',
        panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
        status: 'running', changed: false, diff: '', startedAt: 1,
      }],
    })
    detail['s-local'] = {}
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0].status).toBe('running')
  })

  it('联系不上后端：本地会话一个字节不动（不把「查不到」当成结束）', async () => {
    useAiStore.setState({
      sessions: [{
        id: 's-local', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py',
        panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
        status: 'running', changed: false, diff: '', startedAt: 1,
      }],
    })
    const real = globalThis.fetch
    globalThis.fetch = (() => Promise.reject(new TypeError('down'))) as typeof fetch
    try {
      await useAiStore.getState().restoreRunning()
    } finally {
      globalThis.fetch = real
    }
    expect(sessions()[0].status).toBe('running')
  })
})
