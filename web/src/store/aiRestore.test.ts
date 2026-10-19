/**
 * 运行中会话的恢复（PR4b）：刷新 / SSE 重连 / A→B→A 回切之后，后端还在跑的会话接回来作只读占位，
 * 锁住同一脚本的发送；之后的 `ai.delta` / `ai.done` 认领到它上面；结束后转成普通终态。
 * 全部走真实的 `fetchAiHistory` / `fetchAiSession`（只替掉全局 `fetch`）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { handleServerEvent } from '@/hooks/useServerEvents'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { isOpenSession, isSessionOf, mergeTranscript, SAFETY_NET_MS, setAiSubmissionLedger, submissionBlocks, useAiStore } from './aiStore'
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
let history: Record<string, Row[]> = {} // pj → 「还没结束」清单（running + 已取消未收尾）
let requestsDown = false // `GET /api/ai/requests/<rid>` 回 500（读不到）
let requestsDb: Record<string, Row> = {} // rid → `GET /api/ai/requests/<rid>` 的回答（没有 = 后端没登记过）
let holds: Record<string, Promise<void>> = {} // pj → 扣住该项目的历史请求（「恢复在途」）
const historyCalls: string[] = [] // 发过历史请求的项目
let detail: Record<string, Row> = {}
let requestGate: Promise<void> | null = null // 扣住**下一个**请求快照查询（用一次即清）
let detailGate: Promise<void> | null = null // 扣住**下一个**会话详情读取（用一次即清）
const urls: string[] = []

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  urls.push(u)
  const pj = ((init?.headers ?? {}) as Record<string, string>)['X-Tavotto-Project'] ?? new URL(u, 'http://x').searchParams.get('pj') ?? ''
  if (u.includes('/api/ai/history')) {
    expect(u).toContain('status=open') // 还没结束的：running + 已取消但后端还没收尾
    historyCalls.push(pj)
    const gate = holds[pj] as Promise<void> | undefined
    if (gate) await gate
    const all = history[pj] ?? []
    // 同后端：`status=running` 一次返回全部，不受 limit / offset 约束
    return json({ total: all.length, sessions: all, truncated: false })
  }
  const rq = /\/api\/ai\/requests\/([^/?]+)$/.exec(u.split('?')[0])
  if (rq && requestGate) {
    const g = requestGate
    requestGate = null
    await g
  }
  if (rq && requestsDown) return json({ error: 'boom', code: 'internal_error' }, 500)
  if (rq) return requestsDb[rq[1]] ? json(requestsDb[rq[1]]) : json({ error: 'x', code: 'ai_request_not_found' }, 404)
  const m = /\/api\/ai\/sessions\/([^/?]+)$/.exec(u.split('?')[0])
  if (m) {
    const snapshot = detail[m[1]] // 读的是**请求发出那一刻**的详情（门闩放行得再晚，答复也是当时的）
    if (detailGate) {
      const g = detailGate
      detailGate = null
      await g
    }
    return snapshot ? json(snapshot) : json({ error: 'nf' }, 404)
  }
  if (u.includes('/cancel')) return json({ ok: true })
  return json({})
}) as typeof fetch

const panel = { id: 'p1', fileId: 'Fig1.pdf', script: 'fig1.py' }
const sessions = () => useAiStore.getState().sessions

beforeEach(() => {
  history = {}
  requestsDown = false
  requestGate = null
  detailGate = null
  requestsDb = {}
  holds = {}
  historyCalls.length = 0
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

  it('「还没结束」清单里的 interrupted（另一个服务进程 interrupt_all 了、pump 还没收尾）：接回成临时快照、锁发送，之后的对账拿到 final 才收尾', async () => {
    history.pa = [row({ status: 'interrupted' })]
    detail['s-run'] = { status: 'interrupted', final: false, changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    const [s] = sessions()
    expect(s).toMatchObject({ id: 's-run', restored: true, provisional: true })
    expect(s.finalized).toBeFalsy()
    expect(isOpenSession(s)).toBe(true) // 继续对账，面板的发送照旧锁着（finishingHere）
    history.pa = []
    detail['s-run'] = { status: 'interrupted', final: true, changed: true, diff: '@@ d', transcript: [{ kind: 'message', text: '改好了' }] }
    await useAiStore.getState().settleRunningSessions()
    const [t] = sessions()
    expect(t.finalized).toBe(true)
    expect(t.changed).toBe(true)
    expect(isOpenSession(t)).toBe(false)
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

  it('断线收尾读到「改成了但项目没刷新」：带上 refresh 给警告，不悄悄标成功', async () => {
    useAiStore.setState({
      sessions: [{
        id: 's-local', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py',
        panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
        status: 'running', changed: false, diff: '', startedAt: 1,
      }],
    })
    detail['s-local'] = { status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'failed', code: 'scan_failed' } }
    await useAiStore.getState().restoreRunning()
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChangedRefreshFailed')
  })

  it('后端说 interrupted（重启被中断）：按失败收尾，不再当成在跑', async () => {
    useAiStore.setState({
      sessions: [{
        id: 's-local', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py',
        panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
        status: 'running', changed: false, diff: '', startedAt: 1,
      }],
    })
    // 后端重启后：属主（上一个后端）已不在，详情给 `final: true`
    detail['s-local'] = { status: 'interrupted', final: true, changed: null, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0].status).toBe('failed')
  })

  it('后端说 interrupted 但还不是终态（另一个服务进程的 interrupt_all 刚写了 status，pump 还没收尾）：不收尾，继续当成在跑', async () => {
    useAiStore.setState({
      sessions: [{
        id: 's-local', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py',
        panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
        status: 'running', changed: false, diff: '', startedAt: 1,
      }],
    })
    detail['s-local'] = { status: 'interrupted', final: false, changed: null, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0].status).toBe('running')
    expect(sessions()[0].finalized).toBeFalsy()
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

describe('占位与「已发出未确认」「按脚本的发送锁」并存（PR4 × PR4b）', () => {
  const unconfirmedFor = async (fileId: string, script: string) => {
    const g = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
      String(url).includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : g(url as string, init)) as typeof fetch
    try {
      await useAiStore.getState().start({
        project: currentProjectId(), generation: useAiStore.getState().generation,
        prompt: '把线条加粗', fileId, panelId: 'p-' + fileId, gid: null, label: null, scope: 'figure',
        target: '整张图', overrides: [], script,
      })
    } finally {
      globalThis.fetch = g
    }
  }

  it('同脚本既有接回的运行中占位、又有未确认的提交：查询后只有一个会话（不重复），归属并到面板上，锁还在', async () => {
    history.pa = [row({ id: 's-run', script: 'fig1.py' })]
    await unconfirmedFor('Fig1.pdf', 'fig1.py')
    await useAiStore.getState().restoreRunning() // 刷新后：占位先到
    expect(sessions().map((s) => s.id)).toEqual(['s-run'])
    handleServerEvent({ kind: 'ai.delta', pj: 'pa', session: 's-run', text: '已经在改了', kindOf: 'message' })
    const rid = Object.keys(useAiStore.getState().submissions)[0]
    // 查询：后端说这条就是 s-run
    const g = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
      String(url).includes('/api/ai/requests/')
        ? json({ session: 's-run', status: 'running', changed: false, script: 'fig1.py' })
        : g(url as string, init)) as typeof fetch
    try {
      await useAiStore.getState().queryRequest(rid)
    } finally {
      globalThis.fetch = g
    }
    expect(sessions()).toHaveLength(1) // 对照：旧实现会 push 第二份同 id 会话
    expect(sessions()[0]).toMatchObject({ id: 's-run', status: 'running', restored: false, fileId: 'Fig1.pdf', panelId: 'p-Fig1.pdf' })
    expect(sessions()[0].entries.map((e) => e.text)).toEqual(['已经在改了']) // 占位上累积的输出还在
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([])
    expect(isSessionOf(sessions()[0], { id: 'p3', fileId: 'Fig3.pdf', script: 'fig1.py' })).toBe(true) // 同脚本的兄弟面板仍被锁
    handleServerEvent({ kind: 'ai.done', pj: 'pa', session: 's-run', status: 'done', changed: true, diff: 'D', script: 'fig1.py' })
    expect(sessions()[0]).toMatchObject({ status: 'done', changed: true, finalized: true })
  })

  it('未确认的提交在兄弟面板上、占位在另一个面板上：两个锁各自成立，互不吞掉', async () => {
    history.pa = [row({ id: 's-run', script: 'sub/fig1.py' })]
    await unconfirmedFor('Fig2.pdf', 'sub/fig1.py')
    await useAiStore.getState().restoreRunning()
    expect(Object.keys(useAiStore.getState().submissions)).toHaveLength(1)
    expect(sessions()).toHaveLength(1)
    const sub = Object.values(useAiStore.getState().submissions)[0]
    expect(submissionBlocks(sub, 'Fig9.pdf', 'sub/fig1.py')).toBe(true)
    expect(submissionBlocks(sub, 'Fig9.pdf', 'other/fig1.py')).toBe(false) // 完整路径比：同名不同目录的脚本不互锁
    expect(isSessionOf(sessions()[0], { id: 'p9', fileId: 'Fig9.pdf', script: 'sub/fig1.py' })).toBe(true)
    expect(isSessionOf(sessions()[0], { id: 'p9', fileId: 'Fig9.pdf', script: 'other/fig1.py' })).toBe(false)
  })

  it('占位先到、提交后到（reconcile 在 restoreRunning 之后）：请求 id 对上 = 已被接受，不再摆「未确认」，归属并到占位', async () => {
    history.pa = [row({ id: 's-run', script: 'fig1.py', client_request_id: 'rid-adopt-00001' })]
    await useAiStore.getState().restoreRunning()
    useAiStore.getState().restoreSubmission({
      rid: 'rid-adopt-00001', project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py',
      prompt: '把线条加粗', target: { scope: 'figure', gid: null, label: null, target: '整张图' },
      canvas: null, overrides: [],
    })
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([])
    expect(sessions()).toHaveLength(1)
    expect(sessions()[0]).toMatchObject({ id: 's-run', restored: false, fileId: 'Fig1.pdf', panelId: 'p1' })
  })

  it('提交先到、占位后到（restoreRunning 在 reconcile 之后）：同样直接并上去', async () => {
    useAiStore.getState().restoreSubmission({
      rid: 'rid-adopt-00002', project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py',
      prompt: '把线条加粗', target: { scope: 'figure', gid: null, label: null, target: '整张图' },
      canvas: null, overrides: [],
    })
    expect(Object.keys(useAiStore.getState().submissions)).toHaveLength(1) // 对照：此刻还是未确认
    history.pa = [row({ id: 's-run2', script: 'fig1.py', client_request_id: 'rid-adopt-00002' })]
    await useAiStore.getState().restoreRunning()
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([])
    expect(sessions()).toHaveLength(1)
    expect(sessions()[0]).toMatchObject({ id: 's-run2', restored: false, fileId: 'Fig1.pdf' })
  })

  it('请求 id 对不上的提交不被误并：照旧是未确认', async () => {
    history.pa = [row({ id: 's-run', client_request_id: 'rid-someone-else' })]
    await useAiStore.getState().restoreRunning()
    useAiStore.getState().restoreSubmission({
      rid: 'rid-mine-0000001', project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py',
      prompt: 'x', target: { scope: 'figure', gid: null, label: null, target: '整张图' }, canvas: null, overrides: [],
    })
    expect(Object.keys(useAiStore.getState().submissions)).toEqual(['rid-mine-0000001'])
  })
})

describe('恢复进行中到达的事件 / 并发恢复按项目分键（#1111 Codex P2）', () => {
  const hold = (pj: string) => {
    let release!: () => void
    holds[pj] = new Promise<void>((r) => (release = r))
    return () => {
      delete holds[pj]
      release()
    }
  }

  it('恢复在途（历史请求被扣住）时到达的 ai.done，带的 request id 此刻对不上任何提交（账本还没加载）——也要暂存；历史响应里仍是 running 记录时回放，收尾成终态，不卡 running', async () => {
    history.pa = [row({ id: 's-run', client_request_id: 'rid-not-loaded-yet' })]
    const release = hold('pa')
    const p = useAiStore.getState().restoreRunning()
    handleServerEvent({
      kind: 'ai.done', pj: 'pa', session: 's-run', client_request_id: 'rid-not-loaded-yet',
      status: 'done', changed: true, diff: 'D', script: 'fig1.py',
    })
    release()
    await p
    expect(sessions()).toHaveLength(1)
    expect(sessions()[0]).toMatchObject({ id: 's-run', status: 'done', changed: true, finalized: true }) // 对照：旧实现把它丢了，会话永远 running
    expect(sessions().some((x) => x.status === 'running')).toBe(false) // 不锁脚本
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('同样适用于 ai.delta：恢复期间的过程输出也被回放到接回的占位上', async () => {
    history.pa = [row({ id: 's-run', client_request_id: 'rid-x' })]
    const release = hold('pa')
    const p = useAiStore.getState().restoreRunning()
    handleServerEvent({ kind: 'ai.delta', pj: 'pa', session: 's-run', client_request_id: 'rid-x', text: '恢复期间的输出', kindOf: 'message' })
    release()
    await p
    expect(sessions()[0].entries.map((e) => e.text)).toEqual(['恢复期间的输出'])
    expect(sessions()[0].status).toBe('running')
  })

  it('没被任何返回会话认领的暂存事件在恢复结束后清掉：之后另一次恢复接回同名会话时不会误回放旧事件', async () => {
    const release = hold('pa')
    history.pa = [] // 这次恢复没有返回任何会话
    const p = useAiStore.getState().restoreRunning()
    handleServerEvent({ kind: 'ai.done', pj: 'pa', session: 's-run', client_request_id: 'rid-y', status: 'failed', changed: false, diff: '', script: 'fig1.py' })
    release()
    await p
    history.pa = [row({ id: 's-run' })] // 之后这个 id 又作为运行中记录出现
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0]).toMatchObject({ id: 's-run', status: 'running' }) // 旧的 failed 没被回放
  })

  it('A 的恢复还在途时切到 B：B 发起自己的请求并接回 B 的会话；A 的结果按代次丢弃，B 不受影响', async () => {
    history.pa = [row({ id: 's-a' })]
    history.pb = [row({ id: 's-b', script: 'figb.py' })]
    const releaseA = hold('pa')
    const pA = useAiStore.getState().restoreRunning() // A 在途
    setCurrentProjectId('pb')
    useAiStore.getState().clear() // 切到 B：代次前进
    await useAiStore.getState().restoreRunning() // B 自己的请求——不能拿到 A 的 promise
    expect(historyCalls).toEqual(['pa', 'pb']) // 对照：旧实现 B 复用 A 的 promise，historyCalls 里没有 pb
    expect(sessions().map((x) => x.id)).toEqual(['s-b'])
    releaseA()
    await pA
    expect(sessions().map((x) => x.id)).toEqual(['s-b']) // A 的结果被丢弃
  })

  it('项目认领已换成 B、clear 还没来得及调：B 的恢复也不复用 A 在途的 promise（键含项目 id，不只靠 clear 清）', async () => {
    history.pa = [row({ id: 's-a' })]
    history.pb = [row({ id: 's-b', script: 'figb.py' })]
    const releaseA = hold('pa')
    const pA = useAiStore.getState().restoreRunning()
    setCurrentProjectId('pb') // 只换认领，不 clear
    await useAiStore.getState().restoreRunning()
    expect(historyCalls).toEqual(['pa', 'pb'])
    releaseA()
    await pA
  })

  it('同一项目同一代次内并发调用仍共用一次请求（挂载 + 重连）', async () => {
    history.pa = [row({ id: 's-a' })]
    const release = hold('pa')
    const p1 = useAiStore.getState().restoreRunning()
    const p2 = useAiStore.getState().restoreRunning()
    release()
    await Promise.all([p1, p2])
    expect(historyCalls).toEqual(['pa'])
    expect(sessions()).toHaveLength(1)
  })
})

describe('「已被接受」不管从哪条路径进来，效果都一样（行为矩阵，不数源码）', () => {
  // 结构性不变式用行为验：每条路径都必须清账本（恰好一次）、把提交归属并到唯一的会话上、去掉未确认状态。
  // 盲点（写在明处）：新增第五条「已被接受」的入口，若不加进下面的矩阵，这里测不到它有没有绕开 `accept`。
  const removeAccepted = vi.fn(async () => {})
  const SUB = {
    rid: 'rid-matrix-0001', project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py', prompt: '把线条加粗',
    target: { scope: 'figure' as const, gid: null, label: null, target: '整张图' }, canvas: null, overrides: [],
  }
  const submit = () => useAiStore.getState().restoreSubmission(SUB)

  beforeEach(() => {
    removeAccepted.mockClear()
    setAiSubmissionLedger({ markSubmitted: async () => 'ok', removeAccepted })
  })
  afterEach(() => setAiSubmissionLedger(null))

  const entries: Record<string, () => Promise<void>> = {
    用户点查询: async () => {
      requestsDb[SUB.rid] = { session: 's-run', status: 'running', changed: false, script: 'fig1.py' }
      detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
      submit()
      await useAiStore.getState().queryRequest(SUB.rid)
    },
    占位先到_提交后到: async () => {
      history.pa = [row({ id: 's-run', client_request_id: SUB.rid })]
      await useAiStore.getState().restoreRunning()
      submit()
    },
    提交先到_占位后到: async () => {
      submit()
      history.pa = [row({ id: 's-run', client_request_id: SUB.rid })]
      await useAiStore.getState().restoreRunning()
    },
  }

  it.each(Object.keys(entries))('%s：账本恰好清一次，归属并到唯一的会话，不再是未确认', async (name) => {
    await entries[name]()
    expect(removeAccepted).toHaveBeenCalledTimes(1)
    expect(removeAccepted).toHaveBeenCalledWith('pa', 'Fig1.pdf', SUB.rid)
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([])
    expect(sessions()).toHaveLength(1)
    expect(sessions()[0]).toMatchObject({ id: 's-run', fileId: 'Fig1.pdf', panelId: 'p1', status: 'running' })
    expect(sessions()[0].restored).not.toBe(true)
  })

  it('断线收尾：每个本地 running 会话的详情只读一次（经 restoreRunning 或直接对账都一样）', async () => {
    const mk = (id: string) => ({
      id, project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: 'p', script: 'fig1.py', panelId: 'p1', fileId: 'Fig1.pdf',
      gid: null, scope: 'figure' as const, target: '整张图', entries: [], status: 'running' as const, changed: false, diff: '', startedAt: 1,
    })
    useAiStore.setState({ sessions: [mk('s-1'), mk('s-2')] })
    detail['s-1'] = { status: 'done', changed: false, diff: '', transcript: [] }
    detail['s-2'] = { status: 'done', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning() // 历史说没有在跑的 → 两个都走收尾
    const reads = (id: string) => urls.filter((u) => u.includes(`/api/ai/sessions/${id}`) && !u.includes('/cancel')).length
    expect([reads('s-1'), reads('s-2')]).toEqual([1, 1])
    expect(sessions().map((x) => x.status)).toEqual(['done', 'done'])
    urls.length = 0
    await useAiStore.getState().settleRunningSessions() // 已终态：不再读
    expect(urls.filter((u) => u.includes('/api/ai/sessions/'))).toEqual([])
  })
})

describe('还原出的未确认提交自动按快照收尾，不等用户点「查询结果」（#1111 Codex P2）', () => {
  const SUB = {
    rid: 'rid-auto-00001', project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py', prompt: '把线条加粗',
    target: { scope: 'figure' as const, gid: null, label: null, target: '整张图' }, canvas: null, overrides: [],
  }
  beforeEach(() => useAiStore.getState().clear()) // 自动收尾的次数记录按代次清零（rid 在用例间复用）
  const requestReads = () => urls.filter((u) => u.includes('/api/ai/requests/')).length
  const runPosts = () => urls.filter((u) => u.includes('/api/ai/run')).length

  it('刷新期间任务结束：运行中历史是空的、账本稍后才到——会话自动收尾成终态、有通知、脚本不锁，全程没点查询', async () => {
    history.pa = [] // 刷新时任务已经结束：运行中清单里没有它
    await useAiStore.getState().restoreRunning()
    expect(sessions()).toEqual([])
    requestsDb[SUB.rid] = { session: 's-end', status: 'done', changed: true, script: 'fig1.py' }
    detail['s-end'] = { agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'failed', code: 'scan_failed' } }
    useAiStore.getState().restoreSubmission(SUB) // 账本稍后到（reconcile 还原）
    await vi.waitFor(() => expect(sessions()).toHaveLength(1))
    expect(sessions()[0]).toMatchObject({ id: 's-end', status: 'done', changed: true, finalized: true }) // 对照：旧实现停在「未确认」等用户点
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([])
    expect(sessions().some((x) => x.status === 'running')).toBe(false) // 发送解锁
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChangedRefreshFailed') // 通知（含 refresh 警告）
    expect(runPosts()).toBe(0) // 只读：没有发出任何 run
  })

  it('同一个 rid 在途时只查一次（去重）：对账连着带来同一条也不重复请求', async () => {
    requestsDb[SUB.rid] = { session: 's-end', status: 'done', changed: false, script: 'fig1.py' }
    detail['s-end'] = { agent: 'codex', status: 'done', changed: false, diff: '', transcript: [] }
    const st = useAiStore.getState()
    st.restoreSubmission(SUB)
    st.restoreSubmission(SUB)
    st.restoreSubmission(SUB)
    await vi.waitFor(() => expect(sessions()).toHaveLength(1))
    expect(requestReads()).toBe(1)
  })

  it('有上限：读不到时每个 rid 最多自动查 5 次，之后不再自动查（手动「查询结果」仍可用）', async () => {
    requestsDown = true
    const st = useAiStore.getState()
    for (let i = 0; i < 8; i++) {
      st.restoreSubmission(SUB) // 每次对账都带来同一条
      await vi.waitFor(() => expect(urls.filter((u) => u.includes('/api/ai/requests/')).length).toBeGreaterThanOrEqual(Math.min(i + 1, 5)))
      await new Promise((r) => setTimeout(r, 5))
    }
    expect(requestReads()).toBe(5)
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([SUB.rid]) // 仍是未确认，等手动兜底
    requestsDown = false
    requestsDb[SUB.rid] = { session: 's-end', status: 'done', changed: false, script: 'fig1.py' }
    detail['s-end'] = { agent: 'codex', status: 'done', changed: false, diff: '', transcript: [] }
    await expect(useAiStore.getState().queryRequest(SUB.rid)).resolves.toBe('accepted') // 「查询结果」按钮的路径照常
  })

  it('绝不自动重发：后端说没登记过（404）只是进入 not_received，仍要用户点「重新发送」；全程没有 run 请求', async () => {
    useAiStore.getState().restoreSubmission(SUB) // requestsDb 里没有 = 后端的 404 ai_request_not_found
    await vi.waitFor(() => expect(useAiStore.getState().submissions[SUB.rid]?.phase).toBe('not_received'))
    expect(runPosts()).toBe(0)
    expect(sessions()).toEqual([])
  })

  it('A→B→A：上一代还在途的自动查询不挡新一代（去重键带项目代次 + 项目 id）——新一代的 A 照样发起查询并收尾', async () => {
    let openGate!: () => void
    requestGate = new Promise<void>((r) => (openGate = r)) // 上一代的查询一直在途
    useAiStore.getState().restoreSubmission(SUB)
    await vi.waitFor(() => expect(requestReads()).toBe(1))
    useAiStore.getState().clear() // A→B（换代）
    useAiStore.getState().clear() // B→A
    requestsDb[SUB.rid] = { session: 's-end', status: 'done', changed: false, script: 'fig1.py' }
    detail['s-end'] = { agent: 'codex', status: 'done', changed: false, diff: '', transcript: [] }
    useAiStore.getState().restoreSubmission(SUB) // 新一代的 A 还原出同一条
    await vi.waitFor(() => expect(sessions()).toHaveLength(1)) // 对照：旧实现被上一代的在途标记挡住，永远不查
    expect(requestReads()).toBe(2)
    expect(sessions()[0]).toMatchObject({ id: 's-end', status: 'done', finalized: true })
    openGate() // 上一代的响应迟到：代次对不上，丢弃，不影响新一代
    await new Promise((r) => setTimeout(r, 10))
    expect(sessions()).toHaveLength(1)
  })

  it('快照查询在途期间占位认领了这条提交：终态快照回来时按会话 id 把终态应用到已有会话（不停在 running，发送解锁）', async () => {
    requestsDb[SUB.rid] = { session: 's-run', status: 'done', changed: true, script: 'fig1.py' }
    detail['s-run'] = { agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [{ kind: 'message', text: '改好了' }] }
    let openDetail!: () => void
    detailGate = new Promise<void>((r) => (openDetail = r)) // 快照里的会话详情读取被扣住
    useAiStore.getState().restoreSubmission(SUB) // 自动收尾：请求快照已回（done），详情在途
    await vi.waitFor(() => expect(urls.some((u) => u.includes('/api/ai/sessions/s-run'))).toBe(true))
    history.pa = [row({ id: 's-run', script: 'fig1.py', client_request_id: SUB.rid })] // 此刻历史里它还是 running
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] } // 恢复里的那次详情读取看到的还在跑
    await useAiStore.getState().restoreRunning() // 占位被建起并认领这条提交（提交被删）
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([])
    expect(sessions()[0]).toMatchObject({ id: 's-run', status: 'running' })
    openDetail() // 终态快照迟到
    await vi.waitFor(() => expect(sessions()[0]).toMatchObject({ status: 'done', changed: true, finalized: true }))
    expect(sessions()).toHaveLength(1)
    expect(sessions().some((x) => x.status === 'running')).toBe(false) // 发送解锁
    expect(JSON.stringify(useUiStore.getState().status)).not.toBe('null') // 收尾提示
  })
})

describe('错过 ai.done 这一族的结构性兜底：建完占位一律读详情 + 低频安全网轮询（#1111 Codex P2）', () => {
  const detailReads = () => urls.filter((u) => /\/api\/ai\/sessions\/[^/?]+(\?|$)/.test(u)).length
  const setVisibility = (v: 'visible' | 'hidden') => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => v })
    document.dispatchEvent(new Event('visibilitychange'))
  }
  afterEach(() => {
    vi.useRealTimers()
    setVisibility('visible')
  })

  it('挂载时恢复先于事件流：历史返回 running、任务随后（事件流就绪前）已结束——建完占位就读详情收尾，没有 focus / 重连也行', async () => {
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [] } // 历史之后、事件流之前结束了
    await useAiStore.getState().restoreRunning()
    expect(sessions()).toHaveLength(1)
    expect(sessions()[0]).toMatchObject({ id: 's-run', status: 'done', changed: true, finalized: true }) // 对照：旧实现 live 里的跳过读取，永远停在 running
    expect(sessions().some((x) => x.status === 'running')).toBe(false) // 发送解锁
  })

  it('安全网：还有非终态会话时每 15 s 读一次快照；任务在两次对账之间结束、ai.done 也错过 → 最多 15 s 后收尾、锁解开、有提示', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0]).toMatchObject({ status: 'running' })
    const before = detailReads()
    detail['s-run'] = { agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [] } // 此后任务结束，事件没收到
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS - 1)
    expect(sessions()[0]).toMatchObject({ status: 'running' }) // 还没到点
    await vi.advanceTimersByTimeAsync(2)
    await vi.waitFor(() => expect(sessions()[0]).toMatchObject({ status: 'done', changed: true, finalized: true }))
    expect(detailReads()).toBeGreaterThan(before)
    expect(useUiStore.getState().status).not.toBeNull() // 收尾提示
  })

  it('没有非终态会话时不轮询；会话收尾后轮询停止', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS * 4)
    expect(detailReads()).toBe(0) // 空闲：一个请求都没有
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    handleServerEvent({ kind: 'ai.done', pj: 'pa', session: 's-run', status: 'done', changed: false, diff: '', script: 'fig1.py' })
    const after = detailReads()
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS * 4)
    expect(detailReads()).toBe(after) // 已无非终态会话：不再轮询
  })

  it('页面不可见时暂停；恢复可见时立即跑一次', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    const before = detailReads()
    setVisibility('hidden')
    detail['s-run'] = { agent: 'codex', status: 'done', changed: false, diff: '', transcript: [] }
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS * 3)
    expect(detailReads()).toBe(before) // 不可见：暂停
    expect(sessions()[0]).toMatchObject({ status: 'running' })
    setVisibility('visible') // 恢复可见：立即跑，不等下一个 15 s
    await vi.waitFor(() => expect(sessions()[0]).toMatchObject({ status: 'done', finalized: true }))
  })

  it('同一会话的详情读取在途时，对账与轮询同时到只读一次（按代次 + 项目 + id 去重）', async () => {
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    const before = detailReads()
    let open!: () => void
    detailGate = new Promise<void>((r) => (open = r))
    const a = useAiStore.getState().settleRunningSessions()
    const b = useAiStore.getState().settleRunningSessions()
    open()
    await Promise.all([a, b])
    expect(detailReads() - before).toBe(1)
  })
})

describe('取消后刷新：cancelled 且后端还没收尾的会话也要恢复（#1111 Codex P2 r4289299050）', () => {
  afterEach(() => vi.useRealTimers())

  it('用户先取消再刷新，_pump 还没写完最终结果（历史行 cancelled、ended_ms 为空）：恢复出占位，轮询到 final 后收尾并带上 diff', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    history.pa = [row({ status: 'cancelled', ended_ms: null })]
    detail['s-run'] = { agent: 'codex', status: 'cancelled', final: false, changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    expect(sessions()).toHaveLength(1) // 对照：只查 running 时这里是空的，没有占位、没有轮询
    expect(sessions()[0]).toMatchObject({ id: 's-run', status: 'cancelled', restored: true })
    expect(sessions()[0].finalized).toBeFalsy()
    detail['s-run'] = { agent: 'codex', status: 'cancelled', final: true, changed: true, diff: 'LATE-DIFF', transcript: [] }
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS + 100)
    await vi.waitFor(() => expect(sessions()[0]).toMatchObject({ status: 'cancelled', finalized: true, changed: true, diff: 'LATE-DIFF' }))
  })

  it('历史里已收尾的 cancelled（ended_ms 已写）不在「还没结束」清单里：后端过滤，前端也不会把非 running/cancelled 的行恢复成占位', async () => {
    history.pa = [row({ status: 'done', ended_ms: 5 })]
    await useAiStore.getState().restoreRunning()
    expect(sessions()).toEqual([])
  })
})

describe('取消之后与分页恢复（#1111 Codex P2）', () => {
  const detailReads = () => urls.filter((u) => /\/api\/ai\/sessions\/[^/?]+(\?|$)/.test(u)).length
  afterEach(() => vi.useRealTimers())

  it('cancel 成功后本地是 cancelled 但未 finalized，仍算非终态：SSE 断开、后端随后写入 changed / diff，15 s 轮询拿到并 finalize', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    await useAiStore.getState().cancel('s-run')
    expect(sessions()[0]).toMatchObject({ status: 'cancelled' })
    expect(sessions()[0].finalized).toBeFalsy() // 只是「中止请求被接受」，后端最终结局还没到
    detail['s-run'] = { agent: 'codex', status: 'cancelled', final: true, changed: true, diff: 'PARTIAL', transcript: [] } // 此后后端写入了改动
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS + 100)
    await vi.waitFor(() => expect(sessions()[0]).toMatchObject({ status: 'cancelled', changed: true, diff: 'PARTIAL', finalized: true })) // 对照：旧判据只看 running，永远等不到
    const reads = detailReads()
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS * 3)
    expect(detailReads()).toBe(reads) // finalize 之后不再轮询
  })

  it('cancel 之后后端仍说 running（中止还没生效）：继续对账，不提前 finalize', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    await useAiStore.getState().cancel('s-run')
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS + 100)
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS)
    expect(sessions()[0].finalized).toBeFalsy()
    expect(detailReads()).toBeGreaterThanOrEqual(2) // 一直在对账
  })

  it('运行中历史有 120 条 running：一次请求取全（不分页），都恢复成占位', async () => {
    history.pa = Array.from({ length: 120 }, (_, i) => row({ id: `s-${i}`, script: `f${i}.py` }))
    await useAiStore.getState().restoreRunning()
    expect(sessions()).toHaveLength(120)
    expect(new Set(sessions().map((x) => x.id)).size).toBe(120)
    expect(historyCalls).toHaveLength(1) // 对照：旧实现按 offset 翻页，翻页期间集合变化会漏掉仍在运行的
  })
})

describe('终态要后端明确说 final；终态转录以快照为准（#1111 Codex P2）', () => {
  afterEach(() => vi.useRealTimers())

  it('cancel 之后快照 status 是 cancelled 但 final:false（changed / diff 还没算出来）：不 finalize；轮询直到 final:true 才收尾，并带上 changed / diff', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    await useAiStore.getState().cancel('s-run')
    detail['s-run'] = { agent: 'codex', status: 'cancelled', final: false, changed: false, diff: '', transcript: [] }
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS + 100)
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS)
    expect(sessions()[0].finalized).toBeFalsy() // 对照：旧实现把 status !== 'running' 当终态，这里已经 finalize 成「没有改动」
    expect(sessions()[0].changed).toBe(false)
    detail['s-run'] = { agent: 'codex', status: 'cancelled', final: true, changed: true, diff: 'REAL', transcript: [] }
    await vi.advanceTimersByTimeAsync(SAFETY_NET_MS + 100)
    await vi.waitFor(() => expect(sessions()[0]).toMatchObject({ status: 'cancelled', finalized: true, changed: true, diff: 'REAL' }))
  })

  it('老后端没有 final 字段：退回按 status 判（非 running 即终态）', async () => {
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [] } // 没有 final
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0]).toMatchObject({ status: 'done', finalized: true, changed: true })
  })

  it('终态快照的转录是权威：本地 2 条，断线期间后端又产生 3 条，快照 5 条 → 收尾后显示 5 条，不重复', async () => {
    history.pa = [row()]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    handleServerEvent({ kind: 'ai.delta', pj: 'pa', session: 's-run', text: '第一条', kindOf: 'message' })
    handleServerEvent({ kind: 'ai.delta', pj: 'pa', session: 's-run', text: '第二条', kindOf: 'action' })
    expect(sessions()[0].entries.map((e) => e.text)).toEqual(['第一条', '第二条'])
    const full = ['第一条', '第二条', '第三条', '第四条', '第五条'].map((text, i) => ({ kind: i === 1 ? 'action' : 'message', text }))
    detail['s-run'] = { agent: 'codex', status: 'done', final: true, changed: true, diff: 'D', transcript: full }
    await useAiStore.getState().settleRunningSessions()
    expect(sessions()[0]).toMatchObject({ status: 'done', finalized: true })
    expect(sessions()[0].entries.map((e) => e.text)).toEqual(['第一条', '第二条', '第三条', '第四条', '第五条'])
  })

  it('mergeTranscript：本地独有的条目保留（按相对位置插回），流式半截被快照里的完整条目取代', () => {
    const local = [
      { kind: 'message' as const, text: 'A', at: 1 },
      { kind: 'action' as const, text: '只在前端的提示', at: 2 },
      { kind: 'message' as const, text: '半截…', streaming: true, at: 3 },
    ]
    const out = mergeTranscript(local, [
      { kind: 'message', text: 'A' },
      { kind: 'message', text: 'B' },
      { kind: 'message', text: '半截…完整了' },
    ])
    expect(out.map((e) => e.text)).toEqual(['A', '只在前端的提示', 'B', '半截…完整了'])
    expect(out.some((e) => e.streaming)).toBe(false)
    expect(out[0].at).toBe(1) // 本地已有的那条保留到达时刻
  })

  it('mergeTranscript：快照里没有对应完整条目的流式半截保留（只清 streaming），不让已看到的输出消失（#1111 Codex P2 r4299848602）', () => {
    const local = [
      { kind: 'message' as const, text: 'A', at: 1 },
      { kind: 'message' as const, text: '断线前看到的半截', streaming: true, at: 2 },
    ]
    // 后端不持久化 delta：断线后 agent 没再发完整 message，终态快照里就没有它
    const out = mergeTranscript(local, [{ kind: 'message', text: 'A' }])
    expect(out.map((e) => e.text)).toEqual(['A', '断线前看到的半截'])
    expect(out.some((e) => e.streaming)).toBe(false)
  })
})

describe('断线期间任务失败：恢复后失败提示带着后端给的原因（#1111 Codex P2 r4289419771）', () => {
  const FAILED = { agent: 'codex', status: 'failed', final: true, changed: false, diff: '', transcript: [], error: 'codex 退出码 2：找不到模块 foo' }

  it('占位被详情快照收尾（settleRunningSessions）：error 进会话', async () => {
    history.pa = [row()]
    detail['s-run'] = FAILED
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0]).toMatchObject({ status: 'failed', finalized: true, error: 'codex 退出码 2：找不到模块 foo' }) // 对照：以前是空白失败提示
  })

  it('占位被认领后快照回来（accept 找不到提交 → 按会话 id 收尾）：error 进会话', async () => {
    requestsDb['rid-err-000001'] = { session: 's-run', status: 'failed', changed: false, script: 'fig1.py' }
    detail['s-run'] = FAILED
    let open!: () => void
    detailGate = new Promise<void>((r) => (open = r))
    useAiStore.getState().restoreSubmission({
      rid: 'rid-err-000001', project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py', prompt: 'p',
      target: { scope: 'figure', gid: null, label: null, target: '整张图' }, canvas: null, overrides: [],
    })
    await vi.waitFor(() => expect(urls.some((u) => u.includes('/api/ai/sessions/s-run'))).toBe(true))
    history.pa = [row({ id: 's-run', client_request_id: 'rid-err-000001' })]
    detail['s-run'] = { agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    open()
    await vi.waitFor(() => expect(sessions()[0]).toMatchObject({ status: 'failed', finalized: true, error: 'codex 退出码 2：找不到模块 foo' }))
  })

  it('经查询请求新建会话（accept 主路径）：error 进会话', async () => {
    requestsDb['rid-err-000002'] = { session: 's-q', status: 'failed', changed: false, script: 'fig1.py' }
    detail['s-q'] = FAILED
    useAiStore.getState().restoreSubmission({
      rid: 'rid-err-000002', project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py', prompt: 'p',
      target: { scope: 'figure', gid: null, label: null, target: '整张图' }, canvas: null, overrides: [],
    })
    await vi.waitFor(() => expect(sessions()).toHaveLength(1))
    expect(sessions()[0]).toMatchObject({ id: 's-q', status: 'failed', finalized: true, error: 'codex 退出码 2：找不到模块 foo' })
  })
})

describe('认领占位保持它自己的非终态（#1111 Codex P2 r4290762243）', () => {
  const RID = 'rid-adopt-cancel-01'
  const SUBX = {
    rid: RID, project: 'pa', fileId: 'Fig1.pdf', panelId: 'p1', script: 'fig1.py', prompt: '把线条加粗',
    target: { scope: 'figure' as const, gid: null, label: null, target: '整张图' }, canvas: null, overrides: [],
  }

  it('已取消（cancelled、ended_ms 为空）的占位遇到同 rid 的未确认提交：认领之后仍是 cancelled，提交不再是未确认', async () => {
    history.pa = [row({ status: 'cancelled', ended_ms: null, client_request_id: RID })]
    detail['s-run'] = { agent: 'codex', status: 'cancelled', final: false, changed: false, diff: '', transcript: [] }
    await useAiStore.getState().restoreRunning()
    expect(sessions()[0]).toMatchObject({ status: 'cancelled', requestId: RID })
    useAiStore.getState().restoreSubmission(SUBX) // 账本这时才对账回来：同 rid
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([]) // 不再是未确认
    expect(sessions()).toHaveLength(1)
    expect(sessions()[0]).toMatchObject({ id: 's-run', status: 'cancelled' }) // 对照：以前被覆盖回 running，「中止」重新亮起、后端又拒绝第二次取消
    expect(sessions()[0].finalized).toBeFalsy() // 仍是非终态，继续对账
  })

  it('运行中的占位照旧：认领后仍是 running', async () => {
    history.pa = [row({ client_request_id: RID })]
    await useAiStore.getState().restoreRunning()
    useAiStore.getState().restoreSubmission(SUBX)
    expect(sessions()[0]).toMatchObject({ status: 'running' })
    expect(Object.keys(useAiStore.getState().submissions)).toEqual([])
  })
})

