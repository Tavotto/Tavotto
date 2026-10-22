/**
 * AI 修改的提交可靠性（ADR 0132）。不变式：**任何操作都不得让同一条 AI 修改被无意执行两次**；
 * 不确定后端是否已执行时不得静默重发。
 *
 * 全部走真实的 `aiRun` / `fetchAiRequest`（只替掉全局 `fetch`），数的是**发往 /api/ai/run 的请求次数
 * 与它们带的 client_request_id**，不是内部函数调用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { handleServerEvent } from '@/hooks/useServerEvents'
import { AI_RUN_TIMEOUT_MS } from '@/lib/api'
import { createDraftBackend } from '@/test/aiDraftBackend'
import '@/lib/aiDraftLedger'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { AiLedgerError, ProjectChangedError, sameScript, snapshotIsFinal, useAiStore } from './aiStore'
import sameScriptCases from './sameScriptCases.json'
import { useUiStore } from './uiStore'

type Handler = (url: string, init: RequestInit | undefined) => Promise<Response> | Response
let handler: Handler
const runBodies: { client_request_id?: string; prompt: string }[] = []
const runCalls = () => runBodies.length

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** 默认：run 成功给 sid，别的 200 空 */
const defaultHandler: Handler = (url) => {
  if (url.includes('/api/ai/run')) return json({ session: 's-1', script: 'fig1.py' })
  return json({})
}

/** 永不回答的请求，但听从 abort（和真实 fetch 一样） */
const hang = (init?: RequestInit) =>
  new Promise<Response>((_res, rej) => {
    init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
  })

const draftBe = createDraftBackend()

const fetchLog: string[] = [] // 发往后端的请求（只数草稿以外的，用来验「一次收尾只读一次快照」）

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  if (!u.includes('/api/ai/drafts')) fetchLog.push(u)
  const drafted = draftBe.handle(u, init)
  if (drafted) return drafted
  if (u.includes('/api/ai/run') && init?.body) runBodies.push(JSON.parse(String(init.body)))
  const res = await handler(u, init)
  if (u.includes('/api/ai/run') && init?.body && !res.ok) {
    // 与后端一致：`/api/ai/run` **明确拒绝**（带稳定 code 的错误响应；`ai_request_unresolved` 除外）时由后端自己删这个 rid 的待确认行
    // （pytest 钉住）。没有 code 的（网关 502 / 读不出体）后端根本没见到，不删
    const code = ((await res.clone().json().catch(() => null)) as { code?: unknown } | null)?.code
    if (typeof code === 'string' && code !== 'ai_request_unresolved')
      draftBe.dropRejectedPending(String(JSON.parse(String(init.body)).client_request_id))
  }
  return res
}) as typeof fetch

const send = (prompt = '把图例移到左上角', draftStamp: number | null = null) =>
  useAiStore.getState().start({
    project: currentProjectId(),
    generation: useAiStore.getState().generation,
    draftStamp,
    prompt,
    fileId: 'Fig1.pdf',
    panelId: 'p1',
    gid: null,
    label: null,
    scope: 'figure',
    target: '整张图',
    overrides: [],
  })

const sendOn = (fileId: string, script: string | null) =>
  useAiStore.getState().start({
    project: currentProjectId(),
    generation: useAiStore.getState().generation,
    prompt: '把图例移到左上角',
    fileId,
    panelId: 'p-' + fileId,
    gid: null,
    label: null,
    scope: 'figure',
    target: '整张图',
    overrides: [],
    script,
  })

const subs = () => Object.values(useAiStore.getState().submissions)
const onlySub = () => {
  expect(subs()).toHaveLength(1)
  return subs()[0]
}
const pend = () => draftBe.pending('pa', 'Fig1.pdf')
const draftText = () => draftBe.draft('pa', 'Fig1.pdf')
const gone = () => {
  expect(draftText()).toBeUndefined()
  expect(pend()).toEqual([])
}
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve()
}

beforeEach(() => {
  runBodies.length = 0
  handler = defaultHandler
  draftBe.drafts.clear()
  draftBe.pendings.clear()
  draftBe.calls.length = 0
  setCurrentProjectId('pa')
  useAiStore.setState({
    sessions: [],
    submissions: {},
    agent: 'codex',
    caps: { agents: [{ id: 'codex', name: 'Codex', usable: true, models: [], efforts: [] }] } as never,
  })
  useUiStore.setState({ status: null })
})
afterEach(() => vi.useRealTimers())

describe('强度档位：明确无档位的型号不带 effort', () => {
  const caps = (known: boolean | undefined) =>
    ({
      agents: [
        {
          id: 'codex',
          name: 'Codex',
          usable: true,
          models: ['plain'],
          default_model: 'plain',
          efforts: ['low', 'high'],
          default_effort: 'high',
          model_details: [
            { id: 'plain', display_name: 'P', description: '', efforts: [], efforts_known: known, default_effort: null },
          ],
        },
      ],
    }) as never

  it('efforts_known 且为空：请求体 effort 为 null', async () => {
    useAiStore.setState({ caps: caps(true) })
    await send()
    expect((runBodies.at(-1) as { effort?: string | null } | undefined)?.effort ?? null).toBeNull()
  })

  it('未知（缺 efforts_known）：回落 Agent 级，照常带 effort', async () => {
    useAiStore.setState({ caps: caps(undefined) })
    await send()
    expect((runBodies.at(-1) as { effort?: string | null } | undefined)?.effort).toBe('high')
  })
})

describe('超时 → 「已发出未确认」，不是失败', () => {
  it('30 s 没有任何答复：start 回 unconfirmed（不抛）、会话没建、提交记录进 unconfirmed、草稿带着 rid', async () => {
    vi.useFakeTimers()
    handler = (url, init) => (url.includes('/api/ai/run') ? hang(init) : json({}))
    const p = send()
    await vi.advanceTimersByTimeAsync(AI_RUN_TIMEOUT_MS + 10)
    await expect(p).resolves.toBe('unconfirmed')
    expect(useAiStore.getState().sessions).toEqual([])
    const sub = onlySub()
    expect(sub.phase).toBe('unconfirmed')
    await flush()
    expect(pend()).toEqual([sub.rid])
  })

  it('29 s 时还在 sending（对照：超时尺子量得到）', async () => {
    vi.useFakeTimers()
    handler = (url, init) => (url.includes('/api/ai/run') ? hang(init) : json({}))
    void send()
    await vi.advanceTimersByTimeAsync(AI_RUN_TIMEOUT_MS - 1000)
    expect(onlySub().phase).toBe('sending')
    await vi.advanceTimersByTimeAsync(2000)
  })

  it('断网（fetch 抛 TypeError）、200 但读不出体、网关 502（无稳定 code）：都是未确认', async () => {
    for (const h of [
      (() => Promise.reject(new TypeError('Failed to fetch'))) as Handler,
      (() => new Response('<html>', { status: 200 })) as Handler,
      (() => new Response('bad gateway', { status: 502 })) as Handler,
    ]) {
      useAiStore.setState({ submissions: {}, sessions: [] })
      draftBe.pendings.clear() // 每一轮是独立的场景：上一轮「未确认」留在后端的账本行会（正确地）挡住这一轮
      handler = (url) => (url.includes('/api/ai/run') ? h(url, undefined) : json({}))
      await expect(send()).resolves.toBe('unconfirmed')
      expect(onlySub().phase).toBe('unconfirmed')
    }
  })
})

describe('未确认时不能盲目重发', () => {
  beforeEach(async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('Failed to fetch')) : json({}))
    await send()
  })

  it('resend 在 unconfirmed 相位是空操作：不发任何 /api/ai/run', async () => {
    const before = runCalls()
    await expect(useAiStore.getState().resend(onlySub().rid)).resolves.toBe('unconfirmed')
    expect(runCalls()).toBe(before)
  })

  it('查询到后端不存在 → not_received；这时用【同一个】rid 重发，且只多一次 run', async () => {
    const rid = onlySub().rid
    handler = (url) =>
      url.includes('/api/ai/requests/')
        ? json({ error: 'x', code: 'ai_request_not_found' }, 404)
        : json({ session: 's-9', script: 'fig1.py' })
    await expect(useAiStore.getState().queryRequest(rid)).resolves.toBe('not_received')
    expect(onlySub().phase).toBe('not_received')
    const before = runCalls()
    await expect(useAiStore.getState().resend(rid)).resolves.toBe('accepted')
    expect(runCalls()).toBe(before + 1)
    expect(runBodies.at(-1)?.client_request_id).toBe(rid)
    expect(runBodies[0].client_request_id).toBe(rid) // 第一次发的也是它
    expect(useAiStore.getState().sessions.map((s) => s.id)).toEqual(['s-9'])
    expect(subs()).toEqual([])
  })

  it('查询到后端已登记 → 直接进会话（不重发）、草稿删掉、转录与 diff 从后端补全', async () => {
    const rid = onlySub().rid
    expect(pend()).toEqual([rid])
    handler = (url) => {
      if (url.includes('/api/ai/requests/')) return json({ session: 's-5', status: 'done', changed: true, script: 'fig1.py' })
      if (url.includes('/api/ai/sessions/s-5'))
        return json({ agent: 'codex', status: 'done', changed: true, diff: '--- a\n+++ b', transcript: [{ kind: 'message', text: '好了' }] })
      return json({})
    }
    const before = runCalls()
    await expect(useAiStore.getState().queryRequest(rid)).resolves.toBe('accepted')
    expect(runCalls()).toBe(before) // 没有再发
    const [s] = useAiStore.getState().sessions
    expect(s).toMatchObject({ id: 's-5', status: 'done', changed: true, diff: '--- a\n+++ b', finalized: true })
    expect(s.entries[0].text).toBe('好了')
    expect(subs()).toEqual([])
    await flush()
    gone()
  })

  it('后端登记过但任务历史被用户删了（history_deleted）：不放开重发、不建会话、提交与草稿收掉', async () => {
    const rid = onlySub().rid
    handler = (url) =>
      url.includes('/api/ai/requests/')
        ? json({ session: 's-5', status: 'history_deleted', changed: false, script: null })
        : json({})
    const before = runCalls()
    await expect(useAiStore.getState().queryRequest(rid)).resolves.toBe('already_run')
    expect(runCalls()).toBe(before)
    expect(subs()).toEqual([])
    expect(useAiStore.getState().sessions).toEqual([])
    await flush()
    gone()
    // 提交记录没了，resend 也是空操作
    await expect(useAiStore.getState().resend(rid)).resolves.toBe('unconfirmed')
    expect(runCalls()).toBe(before)
  })

  it('请求状态说 running、会话详情已是终态（两次 GET 之间结束、SSE 又丢了）：以详情为准，不留一个永远 running 的会话', async () => {
    const rid = onlySub().rid
    handler = (url) => {
      if (url.includes('/api/ai/requests/')) return json({ session: 's-6', status: 'running', changed: false, script: 'fig1.py' })
      if (url.includes('/api/ai/sessions/s-6')) return json({ agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [] })
      return json({})
    }
    await useAiStore.getState().queryRequest(rid)
    expect(useAiStore.getState().sessions[0]).toMatchObject({ status: 'done', changed: true, diff: 'D', finalized: true })
  })

  it('查询到已结束的会话、且「改成了但项目没刷新」：和直达路径一样给警告，不悄悄标成功', async () => {
    const rid = onlySub().rid
    handler = (url) => {
      if (url.includes('/api/ai/requests/')) return json({ session: 's-7', status: 'done', changed: true, script: 'fig1.py' })
      if (url.includes('/api/ai/sessions/s-7'))
        return json({ agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'failed', code: 'scan_failed' } })
      return json({})
    }
    await useAiStore.getState().queryRequest(rid)
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChangedRefreshFailed')
  })

  it('查询到已结束的会话、刷新正常：给普通的完成提示（不再静默）', async () => {
    const rid = onlySub().rid
    handler = (url) => {
      if (url.includes('/api/ai/requests/')) return json({ session: 's-8', status: 'done', changed: true, script: 'fig1.py' })
      if (url.includes('/api/ai/sessions/s-8'))
        return json({ agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'ok' } })
      return json({})
    }
    await useAiStore.getState().queryRequest(rid)
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChanged')
    expect(JSON.stringify(useUiStore.getState().status)).not.toContain('RefreshFailed')
  })

  it('查询本身失败（断网 / 5xx / 老后端没有这个端点的普通 404）都不是结论：仍是 unconfirmed，不放开重发', async () => {
    const rid = onlySub().rid
    for (const h of [
      (() => Promise.reject(new TypeError('down'))) as Handler,
      (() => json({ error: 'boom' }, 500)) as Handler,
      (() => new Response('<html>404</html>', { status: 404 })) as Handler,
      (() => json({ error: 'nf', code: 'something_else' }, 404)) as Handler,
    ]) {
      handler = h
      await expect(useAiStore.getState().queryRequest(rid)).resolves.toBe('unreachable')
      expect(onlySub().phase).toBe('unconfirmed')
      await expect(useAiStore.getState().resend(rid)).resolves.toBe('unconfirmed')
    }
  })
})

describe('明确失败才是失败；新的发送换新 rid', () => {
  it('4xx 与带稳定 code 的 5xx：start 抛、提交记录删、草稿去掉 rid 留文本', async () => {
    for (const [status, code] of [
      [409, 'ai_script_busy'],
      [400, 'ai_agent_unknown'],
      [500, 'ai_start_failed'],
    ] as const) {
      useAiStore.setState({ submissions: {} })
      draftBe.put('pa', 'Fig1.pdf', '指令') // 输入框的防抖早已把这段文字存进后端（hook 的事）
      handler = (url) => (url.includes('/api/ai/run') ? json({ error: 'no', code }, status) : json({}))
      await expect(send('指令')).rejects.toMatchObject({ status })
      expect(subs()).toEqual([])
      await flush()
      expect(draftText()).toBe('指令')
      // 账本行的删除只发生在后端（`/api/ai/run` 明确拒绝时由后端删，pytest 钉住）：客户端一个 DELETE 都不发
      expect(draftBe.calls.filter((c) => c.method === 'DELETE' && c.path.includes('/pending'))).toEqual([])
    }
  })

  it('503 ai_request_unresolved（后端拒绝了、但清它的待确认行也失败）：不是明确拒绝——保持同一个 rid，进入「已发出未确认」', async () => {
    handler = (url) => (url.includes('/api/ai/run') ? json({ error: 'x', code: 'ai_request_unresolved' }, 503) : json({}))
    const out = await send('指令')
    expect(out).toBe('unconfirmed') // 对照：5xx 带 code 本来算明确失败
    const [sub] = subs()
    expect(sub?.phase).toBe('unconfirmed')
    expect(pend()).toHaveLength(1) // 账本行还在，查询 / 重发都用同一个 rid
  })

  it('失败后再发：请求 id 与上一次不同', async () => {
    handler = (url) => (url.includes('/api/ai/run') ? json({ error: 'no', code: 'ai_script_busy' }, 409) : json({}))
    await expect(send()).rejects.toBeTruthy()
    handler = defaultHandler
    await send()
    expect(runBodies).toHaveLength(2)
    expect(runBodies[0].client_request_id).not.toBe(runBodies[1].client_request_id)
    expect(runBodies.every((b) => /^[A-Za-z0-9_-]{8,64}$/.test(b.client_request_id ?? ''))).toBe(true)
  })
})

describe('接受之后才删草稿', () => {
  it('发出到响应之间草稿在（带 rid）；响应带回会话 id 的那一刻才删', async () => {
    let release: (r: Response) => void = () => {}
    handler = (url) =>
      url.includes('/api/ai/run') ? new Promise<Response>((r) => (release = r)) : json({})
    draftBe.putStamped('pa', 'Fig1.pdf', '指令', 1_000) // 输入框发送前已把草稿写成功（hook 的 draftSnapshot），戳 1000 记进账本
    const p = send('指令', 1_000)
    await flush()
    expect(draftText()).toBe('指令')
    expect(pend()).toEqual([onlySub().rid])
    release(json({ session: 's-2', script: 'fig1.py' }))
    await p
    await flush()
    gone()
  })

  it('发出后切走项目：会话不落到新项目，但这条已被接受，A 的草稿删掉', async () => {
    let release: (r: Response) => void = () => {}
    handler = (url) =>
      url.includes('/api/ai/run') ? new Promise<Response>((r) => (release = r)) : json({})
    const p = send('指令')
    await flush()
    useAiStore.getState().clear()
    setCurrentProjectId('pb')
    release(json({ session: 's-2', script: 'fig1.py' }))
    await p
    expect(useAiStore.getState().sessions).toEqual([])
    await flush()
    gone()
  })

  it('发出后切走项目且请求没有答复：A 的草稿留着 rid，切回来是未确认（不是可普通重发的草稿）', async () => {
    let failRun!: () => void
    handler = (url) =>
      url.includes('/api/ai/run') ? new Promise((_, rej) => (failRun = () => rej(new TypeError('down')))) : json({})
    const p = send('指令')
    await vi.waitFor(() => expect(runBodies).toHaveLength(1)) // run 已经发出去、还没有答复
    useAiStore.getState().clear() // 此刻切走项目
    failRun()
    await p
    await flush()
    expect(pend()).toHaveLength(1)
    expect(subs()).toEqual([])
  })

  it('账本写入期间切了项目（代次变了）：这次发送作废——不发 run，抛 ProjectChangedError，本地不留提交', async () => {
    const p = send('指令')
    useAiStore.getState().clear() // 切项目
    await expect(p).rejects.toBeInstanceOf(ProjectChangedError)
    await flush()
    expect(runBodies).toHaveLength(0) // 没有 run 发出去（既没发到 A，也没发到 B）
    expect(subs()).toEqual([])
  })
})

describe('ai.done / ai.delta 早于 aiRun 响应', () => {
  const done = (over: Record<string, unknown> = {}) => ({
    kind: 'ai.done' as const,
    pj: 'pa',
    session: 's-7',
    client_request_id: undefined as string | undefined,
    status: 'done',
    changed: true,
    diff: '--- a\n+++ b',
    script: 'fig1.py',
    ...over,
  })

  it('响应还没回来，delta 与 done 先到：会话建好后回放，终态是 done（不是永久 running），状态栏也说了', async () => {
    let release: (r: Response) => void = () => {}
    handler = (url) => (url.includes('/api/ai/run') ? new Promise<Response>((r) => (release = r)) : json({}))
    const p = send()
    await flush()
    const rid = onlySub().rid
    handleServerEvent({ kind: 'ai.delta', pj: 'pa', session: 's-7', client_request_id: rid, text: '我来改', kindOf: 'message' })
    handleServerEvent(done({ client_request_id: rid }))
    expect(useAiStore.getState().sessions).toEqual([]) // 对照：此刻会话还没建
    release(json({ session: 's-7', script: 'fig1.py' }))
    await p
    const [s] = useAiStore.getState().sessions
    expect(s.status).toBe('done')
    expect(s.changed).toBe(true)
    expect(s.entries.map((e) => e.text)).toEqual(['我来改'])
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('不是任何在途提交的事件（别的 rid / 没有在途提交）不进缓冲，也不凭空造会话', async () => {
    handleServerEvent(done({ client_request_id: 'someone-elses-rid' }))
    let release: (r: Response) => void = () => {}
    handler = (url) => (url.includes('/api/ai/run') ? new Promise<Response>((r) => (release = r)) : json({}))
    const p = send()
    await flush()
    handleServerEvent(done({ client_request_id: 'someone-elses-rid' }))
    release(json({ session: 's-7', script: 'fig1.py' }))
    await p
    expect(useAiStore.getState().sessions[0].status).toBe('running') // 那两条没被误认领
  })

  it('重复的 ai.done 不改写终态，也不重复提示', async () => {
    await send()
    const first = useAiStore.getState().finish({ session: 's-1', status: 'done', changed: true, diff: 'D' })
    expect(first).toBe(true)
    const again = useAiStore.getState().finish({ session: 's-1', status: 'failed', changed: false, diff: '', error: 'late' })
    expect(again).toBe(false)
    expect(useAiStore.getState().sessions[0]).toMatchObject({ status: 'done', changed: true, diff: 'D' })
  })

  it('本地先标 cancelled 之后，真正的 ai.done 仍补上 diff / changed（本地 cancelled 不算终态已落）', async () => {
    await send()
    handler = (url) => (url.includes('/cancel') ? json({ ok: true }) : json({}))
    await useAiStore.getState().cancel('s-1')
    expect(useAiStore.getState().sessions[0].status).toBe('cancelled')
    expect(useAiStore.getState().finish({ session: 's-1', status: 'cancelled', changed: true, diff: 'D' })).toBe(true)
    expect(useAiStore.getState().sessions[0]).toMatchObject({ status: 'cancelled', changed: true, diff: 'D' })
  })
})

describe('cancel', () => {
  it('后端回 ok:false（会话已结束）：不标 cancelled，真实结局由 ai.done 给', async () => {
    await send()
    handler = (url) => (url.includes('/cancel') ? json({ ok: false }) : json({}))
    await useAiStore.getState().cancel('s-1')
    expect(useAiStore.getState().sessions[0].status).toBe('running')
    useAiStore.getState().finish({ session: 's-1', status: 'done', changed: false, diff: '' })
    expect(useAiStore.getState().sessions[0].status).toBe('done')
  })
})

describe('发送锁按脚本判，不按面板（Codex #1098 P1）', () => {
  it('A 面板有未落定的提交：同一脚本的 B 面板 start 被拒、一个 run 都不发；别的脚本照常', async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
    await expect(sendOn('Fig1.pdf', 'sub/fig1.py')).resolves.toBe('unconfirmed')
    const before = runCalls()
    await expect(sendOn('Fig2.pdf', './sub\\fig1.py')).rejects.toThrow('submission-pending') // 同一份脚本（路径写法不同）
    expect(runCalls()).toBe(before)
    handler = defaultHandler
    await expect(sendOn('Fig3.pdf', 'other.py')).resolves.toBe('accepted') // 对照：别的脚本不受影响
    // 同一面板同样被锁
    await expect(sendOn('Fig1.pdf', 'sub/fig1.py')).rejects.toThrow('submission-pending')
  })

  it('另一个项目的未落定提交不锁这个项目', async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
    await sendOn('Fig1.pdf', 'fig1.py')
    handler = defaultHandler
    setCurrentProjectId('pb')
    await expect(sendOn('Fig2.pdf', 'fig1.py')).resolves.toBe('accepted')
  })
})

describe('发送锁比完整的项目相对路径，不比文件名（Codex #1098 P2）', () => {
  it('a/plot.py 的未确认请求不锁 b/plot.py；同一路径（写法不同）才锁', async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
    await sendOn('FigA.pdf', 'a/plot.py')
    handler = defaultHandler
    await expect(sendOn('FigB.pdf', 'b/plot.py')).resolves.toBe('accepted') // 旧实现按文件名比，这里被误锁
    await expect(sendOn('FigC.pdf', 'a\\plot.py')).rejects.toThrow('submission-pending')
  })

  it('snapshotIsFinal：cancelled / interrupted 都要带 ended_ms 才是终态（interrupt_all 与 cancel 一样只先写 status）', () => {
    for (const status of ['cancelled', 'interrupted']) {
      expect(snapshotIsFinal({ status, ended_ms: null } as never, status)).toBe(false)
      expect(snapshotIsFinal({ status, ended_ms: 5 } as never, status)).toBe(true)
    }
    expect(snapshotIsFinal({ status: 'done', ended_ms: null } as never, 'done')).toBe(true)
    expect(snapshotIsFinal({ status: 'running', ended_ms: 5 } as never, 'running')).toBe(false)
  })

  it('sameScript ↔ 后端 ai_history.same_script 严格同源：两边跑同一张用例表（tests/test_ai_drafts.py 读同一份 JSON）', () => {
    expect(sameScriptCases.length).toBeGreaterThanOrEqual(8)
    for (const [a, b, want] of sameScriptCases as [string, string, boolean][]) {
      expect(sameScript(a, b), `${a} ↔ ${b}`).toBe(want)
      expect(sameScript(b, a), `${b} ↔ ${a}`).toBe(want)
    }
  })

  it('sameScript：分隔符与 ./ 不影响；绝对路径只与它的路径后缀相等', () => {
    expect(sameScript('a/plot.py', 'a/plot.py')).toBe(true)
    expect(sameScript('./a//plot.py', 'a\\plot.py')).toBe(true)
    expect(sameScript('a/plot.py', 'b/plot.py')).toBe(false)
    expect(sameScript('plot.py', 'a/plot.py')).toBe(false)
    expect(sameScript('/proj/a/plot.py', 'a/plot.py')).toBe(true)
    expect(sameScript('/proj/a/plot.py', 'b/plot.py')).toBe(false)
    expect(sameScript('/proj/xa/plot.py', 'a/plot.py')).toBe(false)
  })
})

describe('发送前必须先把账本写进后端（Codex #1098 P1）', () => {
  it('写账本失败（后端 5xx）：不发 run、提交记录删掉、抛 AiLedgerError', async () => {
    draftBe.setPendingWriteFails(true)
    try {
      await expect(send()).rejects.toBeInstanceOf(AiLedgerError)
    } finally {
      draftBe.setPendingWriteFails(false)
    }
    expect(runCalls()).toBe(0) // 对照：旧实现吞掉失败、照样发
    expect(subs()).toEqual([])
    expect(pend()).toEqual([])
  })

  it('后端整个不可用：同样不发', async () => {
    draftBe.setDown(true)
    try {
      await expect(send()).rejects.toBeInstanceOf(AiLedgerError)
    } finally {
      draftBe.setDown(false)
    }
    expect(runCalls()).toBe(0)
  })

  it('账本写成功后才发：run 发出的那一刻账本里已有这条', async () => {
    let seenAtRun: string[] = []
    handler = (url) => {
      if (url.includes('/api/ai/run')) seenAtRun = pend()
      return defaultHandler(url, undefined)
    }
    await send()
    expect(seenAtRun).toHaveLength(1)
  })

  it('失败后恢复：再发一次正常发出（新的请求 id）', async () => {
    draftBe.setPendingWriteFails(true)
    await expect(send()).rejects.toBeInstanceOf(AiLedgerError)
    draftBe.setPendingWriteFails(false)
    await expect(send()).resolves.toBe('accepted')
    expect(runCalls()).toBe(1)
  })
})

describe('读会话详情暂时失败时不收尾（Codex #1098 P1）', () => {
  const found = (status = 'done') => json({ session: 's-9', status, changed: true, script: 'fig1.py' })
  beforeEach(async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
    await send()
  })

  it('请求状态是终态、详情读失败（网络 / 5xx）：保持未确认、提交记录与账本都在、可再查', async () => {
    const rid = onlySub().rid
    for (const detail of [
      (() => Promise.reject(new TypeError('down'))) as Handler,
      (() => json({ error: 'boom', code: 'internal_error' }, 500)) as Handler,
    ]) {
      handler = (url) => (url.includes('/api/ai/requests/') ? found() : detail(url, undefined))
      await expect(useAiStore.getState().queryRequest(rid)).resolves.toBe('unreachable')
      expect(useAiStore.getState().sessions).toEqual([])
      expect(onlySub().phase).toBe('unconfirmed')
      expect(pend()).toEqual([rid])
    }
    // 详情恢复后再查：带着 refresh 结局收尾，警告说得出口
    handler = (url) =>
      url.includes('/api/ai/requests/')
        ? found()
        : json({ agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'failed', code: 'scan_failed' } })
    await expect(useAiStore.getState().queryRequest(rid)).resolves.toBe('accepted')
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChangedRefreshFailed')
  })

  it('对照：后端明确说没有详情（404）才凭摘要往下走', async () => {
    const rid = onlySub().rid
    handler = (url) => (url.includes('/api/ai/requests/') ? found() : json({ error: 'nf' }, 404))
    await expect(useAiStore.getState().queryRequest(rid)).resolves.toBe('accepted')
    expect(useAiStore.getState().sessions[0]).toMatchObject({ id: 's-9', status: 'done', changed: true })
  })
})

describe('同 id 重发被去重（deduped）：拿快照、按真实状态收尾（Codex #1098 P2）', () => {
  /** 首个 POST 不确定 → 查询得 404（not_received）→ 之后后端其实把它执行完了 → 同 id 重发拿到 deduped */
  const toNotReceived = async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
    await send()
    const rid = onlySub().rid
    handler = (url) =>
      url.includes('/api/ai/requests/') ? json({ error: 'x', code: 'ai_request_not_found' }, 404) : json({})
    await useAiStore.getState().queryRequest(rid)
    expect(onlySub().phase).toBe('not_received')
    return rid
  }
  const dedupedHandler = (snapshot: Handler): Handler => (url, init) =>
    url.includes('/api/ai/run') ? json({ session: 's-9', script: 'fig1.py', deduped: true }) : snapshot(url, init)

  it('会话其实已经结束（SSE 又断着）：按快照收尾成终态，带 refresh 警告；不留 running 会话、不锁脚本', async () => {
    const rid = await toNotReceived()
    handler = dedupedHandler((url) => {
      if (url.includes('/api/ai/requests/')) return json({ session: 's-9', status: 'done', changed: true, script: 'fig1.py' })
      if (url.includes('/api/ai/sessions/s-9'))
        return json({ agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'failed', code: 'scan_failed' } })
      return json({})
    })
    await expect(useAiStore.getState().resend(rid)).resolves.toBe('accepted')
    const [s] = useAiStore.getState().sessions
    expect(s).toMatchObject({ id: 's-9', status: 'done', changed: true, finalized: true }) // 对照：旧实现是 running，永远等不到 ai.done
    expect(useAiStore.getState().sessions.some((x) => x.status === 'running')).toBe(false)
    expect(subs()).toEqual([])
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChangedRefreshFailed')
  })

  it('快照显示仍在运行：才进入 running，之后的 ai.done 照常收尾', async () => {
    const rid = await toNotReceived()
    handler = dedupedHandler((url) => {
      if (url.includes('/api/ai/requests/')) return json({ session: 's-9', status: 'running', changed: false, script: 'fig1.py' })
      if (url.includes('/api/ai/sessions/s-9')) return json({ agent: 'codex', status: 'running', changed: false, diff: '', transcript: [] })
      return json({})
    })
    await useAiStore.getState().resend(rid)
    expect(useAiStore.getState().sessions[0]).toMatchObject({ id: 's-9', status: 'running' })
    useAiStore.getState().finish({ session: 's-9', status: 'done', changed: false, diff: '' })
    expect(useAiStore.getState().sessions[0].status).toBe('done')
  })

  it('快照读不到：保持未确认（提交与账本都在），不凭 deduped 响应建会话', async () => {
    const rid = await toNotReceived()
    handler = dedupedHandler(() => Promise.reject(new TypeError('down')))
    await expect(useAiStore.getState().resend(rid)).resolves.toBe('unconfirmed')
    expect(useAiStore.getState().sessions).toEqual([])
    expect(onlySub().phase).toBe('unconfirmed')
    expect(pend()).toEqual([rid])
  })

  it('没有 deduped 的正常响应不受影响（对照）：仍是刚启动的 running 会话', async () => {
    const rid = await toNotReceived()
    handler = (url) => (url.includes('/api/ai/run') ? json({ session: 's-new', script: 'fig1.py' }) : json({}))
    await useAiStore.getState().resend(rid)
    expect(useAiStore.getState().sessions[0]).toMatchObject({ id: 's-new', status: 'running' })
  })
})

describe('一次收尾只读一次快照：各条收尾路径，同样的服务器状态得到同样的结果（行为，不数源码）', () => {
  const requestUrl = (u: string) => u.includes('/api/ai/requests/')
  const detailUrl = (u: string) => u.includes('/api/ai/sessions/s-9')
  const snapshotServer: Handler = (url) => {
    if (requestUrl(url)) return json({ session: 's-9', status: 'done', changed: true, script: 'fig1.py' })
    if (detailUrl(url))
      return json({ agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'failed', code: 'scan_failed' } })
    return json({})
  }
  const toNotReceived = async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
    await send()
    const rid = onlySub().rid
    handler = (url) => (requestUrl(url) ? json({ error: 'x', code: 'ai_request_not_found' }, 404) : json({}))
    await useAiStore.getState().queryRequest(rid)
    return rid
  }

  const paths: Record<string, { run: () => Promise<void>; requestReads: number }> = {
    用户点查询: {
      requestReads: 1,
      run: async () => {
        handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
        await send()
        const rid = onlySub().rid
        handler = snapshotServer
        fetchLog.length = 0
        await useAiStore.getState().queryRequest(rid)
      },
    },
    同id重发被去重: {
      requestReads: 1,
      run: async () => {
        const rid = await toNotReceived()
        handler = (url, init) =>
          url.includes('/api/ai/run') ? json({ session: 's-9', script: 'fig1.py', deduped: true }) : snapshotServer(url, init)
        fetchLog.length = 0
        await useAiStore.getState().resend(rid)
      },
    },
    本地running会话的对账收尾: {
      requestReads: 0,
      run: async () => {
        handler = (url) => (url.includes('/api/ai/run') ? json({ session: 's-9', script: 'fig1.py' }) : json({}))
        await send()
        expect(useAiStore.getState().sessions[0]).toMatchObject({ id: 's-9', status: 'running' })
        handler = snapshotServer
        fetchLog.length = 0
        await useAiStore.getState().settleRunningSessions()
      },
    },
  }

  it.each(Object.keys(paths))('%s：快照各读一次（会话详情恰好一次），收尾结果与其余路径相同', async (name) => {
    await paths[name].run()
    const reads = (pred: (u: string) => boolean) => fetchLog.filter(pred).length
    expect(reads(detailUrl)).toBe(1) // 一次收尾只读一次会话详情——任何路径重复读 / 绕开共用读法都会改变这个数
    expect(reads(requestUrl)).toBe(paths[name].requestReads)
    expect(useAiStore.getState().sessions).toHaveLength(1)
    expect(useAiStore.getState().sessions[0]).toMatchObject({ id: 's-9', status: 'done', changed: true, diff: 'D', finalized: true })
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChangedRefreshFailed') // 终态走同一条完成路径，refresh 警告都在
    expect(useAiStore.getState().sessions.some((x) => x.status === 'running')).toBe(false)
    expect(subs()).toEqual([])
  })
})
