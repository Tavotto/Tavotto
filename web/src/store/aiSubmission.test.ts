/**
 * AI 修改的提交可靠性（ADR 0131）。不变式：**任何操作都不得让同一条 AI 修改被无意执行两次**；
 * 不确定后端是否已执行时不得静默重发。
 *
 * 全部走真实的 `aiRun` / `fetchAiRequest`（只替掉全局 `fetch`），数的是**发往 /api/ai/run 的请求次数
 * 与它们带的 client_request_id**，不是内部函数调用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { handleServerEvent } from '@/hooks/useServerEvents'
import { AI_RUN_TIMEOUT_MS } from '@/lib/api'
import { loadDraft } from '@/lib/aiDraft'
import { setCurrentProjectId } from '@/lib/session'
import { useAiStore } from './aiStore'
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

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  if (u.includes('/api/ai/run') && init?.body) runBodies.push(JSON.parse(String(init.body)))
  return handler(u, init)
}) as typeof fetch

const send = (prompt = '把图例移到左上角') =>
  useAiStore.getState().start({
    prompt,
    fileId: 'Fig1.pdf',
    panelId: 'p1',
    gid: null,
    label: null,
    scope: 'figure',
    target: '整张图',
    overrides: [],
  })

const subs = () => Object.values(useAiStore.getState().submissions)
const onlySub = () => {
  expect(subs()).toHaveLength(1)
  return subs()[0]
}
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve()
}

beforeEach(() => {
  runBodies.length = 0
  handler = defaultHandler
  localStorage.clear()
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
    expect(loadDraft('pa', 'Fig1.pdf')?.rid).toBe(sub.rid)
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
    expect(loadDraft('pa', 'Fig1.pdf')?.rid).toBe(rid)
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
    expect(loadDraft('pa', 'Fig1.pdf')).toBeNull()
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
      handler = (url) => (url.includes('/api/ai/run') ? json({ error: 'no', code }, status) : json({}))
      await expect(send('指令')).rejects.toMatchObject({ status })
      expect(subs()).toEqual([])
      const d = loadDraft('pa', 'Fig1.pdf')
      expect(d?.text).toBe('指令')
      expect(d?.rid).toBeUndefined()
    }
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
    const p = send('指令')
    await flush()
    expect(loadDraft('pa', 'Fig1.pdf')).toMatchObject({ text: '指令', rid: onlySub().rid })
    release(json({ session: 's-2', script: 'fig1.py' }))
    await p
    expect(loadDraft('pa', 'Fig1.pdf')).toBeNull()
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
    expect(loadDraft('pa', 'Fig1.pdf')).toBeNull()
  })

  it('发出后切走项目且请求没有答复：A 的草稿留着 rid，切回来是未确认（不是可普通重发的草稿）', async () => {
    handler = (url) => (url.includes('/api/ai/run') ? Promise.reject(new TypeError('down')) : json({}))
    const p = send('指令')
    useAiStore.getState().clear()
    await p
    expect(loadDraft('pa', 'Fig1.pdf')?.rid).toBeTruthy()
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
