/**
 * 同一份排版在两个窗口开着（ADR 0181）：只读、动作记住、接管协议（两个方向）、旧版标签页、存成副本。
 * 两个「窗口」在一个 JS 上下文里用一条假的 BroadcastChannel 对话：假频道把本窗口发的消息收进 `sent`，
 * 用例用 `deliver` 扮演对面窗口发来的消息，并让假对面按需回 ack。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import {
  __setPresenceChannelFactory,
  __setTakeoverTimeoutForTests,
  __tabId,
  type PresenceChannel,
} from '@/lib/docPresence'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type TextObject } from '@/types/document'
import { enterContested, saveCopyHere, startDocWindow, takeOverHere } from './docWindow'
import { deferUserEntry, resetHeldEntriesForTests } from './documentRestore'
import { flushAutosave, getWindowHold, startAutosave, useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'

const text = (id: string): TextObject => ({
  id, type: 'text', text: id, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})
const revisionOf = (body: string) => `r${body.length}-${[...body].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7)}`

const autosave = new Map<string, string>()
const puts: string[] = []
let putStatus: 200 | 500 = 200

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://x')
  const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
  if (auto) {
    const id = decodeURIComponent(auto[1])
    if (init?.method === 'PUT') {
      puts.push(id)
      if (putStatus === 500) return new Response('{"error":"disk full"}', { status: 500 })
      autosave.set(id, String(init.body))
      return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: revisionOf(String(init.body)) }))
    }
    if (init?.method === 'DELETE') return new Response('{"ok":true}')
    const v = autosave.get(id)
    return new Response(v ?? '{}', { status: v ? 200 : 404, headers: v ? { 'X-Tavotto-Revision': revisionOf(v) } : {} })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const s = () => useDocumentStore.getState()
const edit = (id: string) => s().commit(literal('加字'), (d) => { d.objects.push(text(id)) })
const ids = () => s().doc.objects.map((o) => o.id)
const diskIds = (id: string) => (JSON.parse(autosave.get(id)!).canvases[0].objects as TextObject[]).map((o) => o.id)

type Msg = { type: string; docId?: string; tabId?: string; reqId?: string; flushed?: boolean }
const sent: Msg[] = []
let listener: ((ev: { data: unknown }) => void) | null = null
/** 假对面：收到本窗口的 takeover-request 时怎么回（null = 不回，模拟已关 / 卡死 / 旧版标签页） */
let peer: null | { flushed: boolean } = null
const channel: PresenceChannel = {
  postMessage: (d) => {
    const m = d as Msg
    sent.push(m)
    if (m.type === 'takeover-request' && peer) {
      const reply = { type: 'takeover-ack', docId: m.docId, tabId: 'peer-tab', reqId: m.reqId, flushed: peer.flushed }
      setTimeout(() => listener?.({ data: reply }), 0)
    }
  },
  addEventListener: (_t, fn) => { listener = fn },
  close: () => {},
}
const deliver = (m: Msg) => listener?.({ data: { tabId: 'peer-tab', ...m } })

const flushMicrotasks = () => new Promise((r) => setTimeout(r, 30))

let stop: () => void
let stopWindow: () => void
beforeEach(async () => {
  // 先换走上一条用例留下的文档（换走时的冲刷会写盘），再清假后端
  await s().switchDocument(emptyProject(), 'd_reset')
  await flushMicrotasks()
  localStorage.clear()
  autosave.clear()
  puts.length = 0
  sent.length = 0
  putStatus = 200
  peer = null
  listener = null
  setCurrentProjectId(null)
  resetHeldEntriesForTests()
  useUiStore.setState({ status: null, statusAction: null })
  __setTakeoverTimeoutForTests(60)
  __setPresenceChannelFactory(() => channel)
  await s().switchDocument(emptyProject(), 'd_x')
  stop = startAutosave()
  stopWindow = startDocWindow()
})
afterEach(() => {
  stopWindow()
  stop()
  __setTakeoverTimeoutForTests(null)
  __setPresenceChannelFactory(null)
  resetHeldEntriesForTests()
})

/** 对面窗口存过一版「theirs」到盘上，本窗口（后开的）内存里还是空白 */
async function diskHasTheirs() {
  edit('theirs')
  await import('./documentStore').then((m) => m.saveNow())
  const body = autosave.get('d_x')!
  await s().switchDocument(emptyProject(), 'd_x')
  autosave.set('d_x', body)
  puts.length = 0
}

describe('后开的窗口：选之前只读', () => {
  it('不能改、不写盘；写入口被拒', async () => {
    enterContested('d_x')
    expect(getWindowHold()).toEqual({ role: 'contested', docId: 'd_x' })
    edit('a')
    expect(ids(), '编辑被拒').toEqual([])
    expect(flushAutosave()).toBe('skipped')
    await flushMicrotasks()
    expect(puts).toEqual([])
  })

  it('别的排版的冲突通知与本窗口无关', () => {
    enterContested('d_other')
    expect(getWindowHold()).toBeNull()
  })
})

describe('只读期间动手的操作被记住，选完接着做', () => {
  it('「在这里编辑」之后执行一次，且落在载入的最新版里', async () => {
    await diskHasTheirs()
    enterContested('d_x')
    const ran: string[][] = []
    const gate = deferUserEntry(() => { ran.push(ids()) })
    expect(gate).toBe('deferred')
    expect(ran).toEqual([])
    peer = { flushed: true }
    expect(await takeOverHere()).toBe(true)
    expect(ran).toEqual([['theirs']])
    expect(deferUserEntry(() => {})).toBe('run') // 编辑权放开了
  })

  it('「存成副本」之后执行，落在副本（新 documentId）里', async () => {
    enterContested('d_x')
    let docWhenRun = ''
    deferUserEntry(() => { docWhenRun = s().documentId })
    expect(await saveCopyHere()).toBe(true)
    expect(s().documentId).not.toBe('d_x')
    expect(docWhenRun).toBe(s().documentId)
  })

  it('选之前换了排版：动作丢掉并说一句，不落进别的排版', async () => {
    enterContested('d_x')
    let ran = 0
    deferUserEntry(() => { ran += 1 })
    await s().switchDocument(emptyProject(), 'd_y')
    expect(ran).toBe(0)
    expect(useUiStore.getState().status).not.toBeNull()
  })
})

describe('接管协议：在这里编辑', () => {
  it('对面存好并回应：载入盘上最新版、放开编辑、不摆「未恢复的编辑」', async () => {
    await diskHasTheirs()
    enterContested('d_x')
    peer = { flushed: true }
    expect(await takeOverHere()).toBe(true)
    expect(sent.some((m) => m.type === 'takeover-request' && m.docId === 'd_x')).toBe(true)
    expect(getWindowHold()).toBeNull()
    expect(ids()).toEqual(['theirs'])
    expect(s().saveState).toBe('clean')
    expect(s().docNotice, '这个窗口没有自己的改动，不该把过时的内存版本当恢复副本摆出来').toBeNull()
    edit('mine')
    expect(ids()).toEqual(['theirs', 'mine'])
  })

  it('对面回了 ack：不等满限时就接管（ack 真的被认）', async () => {
    await diskHasTheirs()
    enterContested('d_x')
    __setTakeoverTimeoutForTests(2000)
    peer = { flushed: true }
    const t0 = Date.now()
    expect(await takeOverHere()).toBe(true)
    expect(Date.now() - t0).toBeLessThan(1000)
  })

  it('对面限时内没回应（已关 / 卡死 / 旧版标签页）：直接接管，载入最新版', async () => {
    await diskHasTheirs()
    enterContested('d_x')
    peer = null
    expect(await takeOverHere()).toBe(true)
    expect(getWindowHold()).toBeNull()
    expect(ids()).toEqual(['theirs'])
  })

  it('对面存不进去：照样接管；这边载入的是盘上现有的版本', async () => {
    await diskHasTheirs()
    enterContested('d_x')
    peer = { flushed: false }
    expect(await takeOverHere()).toBe(true)
    expect(ids()).toEqual(['theirs'])
  })

  it('读不回盘上的版本：保持只读、动作放回去、说一句并给出口', async () => {
    enterContested('d_x') // 盘上没有这份
    let ran = 0
    deferUserEntry(() => { ran += 1 })
    peer = { flushed: true }
    expect(await takeOverHere()).toBe(false)
    expect(getWindowHold()?.role).toBe('contested')
    expect(ran).toBe(0)
    expect(useUiStore.getState().statusTone).toBe('error')
    // 动作仍记着：之后接管成功就执行
    autosave.set('d_x', JSON.stringify(emptyProject()))
    expect(await takeOverHere()).toBe(true)
    expect(ran).toBe(1)
  })
})

describe('被接管的窗口（持有方）', () => {
  it('收到接管请求：先把没存的改动存好，再回 ack(flushed) 并变成只读', async () => {
    edit('mine')
    puts.length = 0
    deliver({ type: 'takeover-request', docId: 'd_x', reqId: 'r1' })
    await flushMicrotasks()
    expect(diskIds('d_x')).toEqual(['mine'])
    const ack = sent.find((m) => m.type === 'takeover-ack')
    expect(ack).toMatchObject({ docId: 'd_x', reqId: 'r1', flushed: true, tabId: __tabId() })
    expect(getWindowHold()).toEqual({ role: 'yielded', docId: 'd_x' })
    // 存盘先于 ack
    expect(sent.indexOf(ack!)).toBeGreaterThanOrEqual(0)
    edit('more')
    expect(ids()).toEqual(['mine'])
    await flushMicrotasks()
    expect(puts.length, '被接管后不再写盘').toBe(1)
  })

  it('存不进去：仍让出（ack flushed:false），改动留在内存里，「存成副本」把它们存成新排版', async () => {
    putStatus = 500
    edit('mine')
    deliver({ type: 'takeover-request', docId: 'd_x', reqId: 'r2' })
    await flushMicrotasks()
    expect(sent.find((m) => m.type === 'takeover-ack')).toMatchObject({ reqId: 'r2', flushed: false })
    expect(getWindowHold()?.role).toBe('yielded')
    expect(ids()).toEqual(['mine'])

    putStatus = 200
    expect(await saveCopyHere()).toBe(true)
    expect(s().documentId).not.toBe('d_x')
    await flushMicrotasks()
    expect(diskIds(s().documentId)).toEqual(['mine'])
    expect(autosave.has('d_x')).toBe(false)
  })

  it('反向「拿回来编辑」：同一个协议，对方让出，这边载入最新版', async () => {
    edit('mine')
    deliver({ type: 'takeover-request', docId: 'd_x', reqId: 'r3' })
    await flushMicrotasks()
    expect(getWindowHold()?.role).toBe('yielded')
    // 对面接管后又改了一版并存盘
    const next = emptyProject()
    next.canvases[0].objects = [text('theirs2')]
    autosave.set('d_x', JSON.stringify(next))
    peer = { flushed: true }
    expect(await takeOverHere()).toBe(true)
    expect(sent.some((m) => m.type === 'takeover-request')).toBe(true)
    expect(getWindowHold()).toBeNull()
    expect(ids()).toEqual(['theirs2'])
    expect(s().saveState).toBe('clean')
    expect(s().docNotice, '让出时已经存好了，没有要保护的改动').toBeNull()
  })

  it('已经只读的窗口收到接管请求：直接回 ack，不再存盘', async () => {
    enterContested('d_x')
    puts.length = 0
    deliver({ type: 'takeover-request', docId: 'd_x', reqId: 'r4' })
    await flushMicrotasks()
    expect(sent.find((m) => m.type === 'takeover-ack')).toMatchObject({ reqId: 'r4', flushed: true })
    expect(puts).toEqual([])
    expect(getWindowHold()?.role).toBe('contested')
  })
})

describe('旧版标签页', () => {
  it('不认识的消息被忽略；没登记处理方时不回 ack（请求方按超时接管）', async () => {
    stopWindow()
    deliver({ type: 'takeover-request', docId: 'd_x', reqId: 'r5' })
    deliver({ type: 'something-new', docId: 'd_x' })
    await flushMicrotasks()
    expect(sent.some((m) => m.type === 'takeover-ack')).toBe(false)
    expect(getWindowHold()).toBeNull()
    stopWindow = startDocWindow()
  })
})
