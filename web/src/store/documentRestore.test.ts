/**
 * 文档恢复协调（`documentRestore` + 恢复门，CONTRACT 2.3 / 2.4）。
 *
 * 真实的 `documentStore` / `projectStore` / `projectDocs` / API client / 迁移与恢复代码，唯一被换掉的是 `fetch`
 * （FE-P 的有状态假后端 `test/fakeSessionBackend`）。时序全由手动 gate 卡住——**没有 sleep 判时序**；
 * 有界重试的退避压到 0（`setRestoreRetryDelays`），不是真睡。
 *
 * 看护的不变量（任务书第三节）：
 *   1 占位文档零持久化副作用      2 项目 ready ≠ 文档 ready（同一个门供冷启动 / Picker / 交接）
 *   3 读取失败 ≠ 没有文档          4 候选探测只读（未选中零写入）
 *   5 每个异步提交有归属（令牌）   10 恢复问题与保存进度正交
 * 验收编号（A01…）标在用例名里；QA 的独立验收在 `recovery.acceptance.test.ts`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import {
  emptyProject,
  type PanelObject,
  type ProjectDocument,
  type TextObject,
} from '@/types/document'

const be = createFakeBackend()

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'panel_saved', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf',
  nativeW: 80, nativeH: 60, x: 13, y: 24, w: 80, h: 60,
  script: 'fig1.py', overrides: [{ gid: 'figure', prop: 'facecolor', value: '#f00' }],
  figureFrame: 1,
  ...over,
})

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

/** 一份有内容的已保存排版（带一个有非空 overrides 的面板） */
function savedProject(name: string, objects: PanelObject[] = [panel()]): ProjectDocument {
  const pd = emptyProject()
  pd.project.name = name
  pd.canvases[0].objects = structuredClone(objects)
  pd.updatedAt = 100
  return pd
}

/** 假后端之外的几个端点：素材清单（交接要找面板）、runtime 清单 */
const fig1Info = {
  id: 'Fig1.pdf', name: 'Fig1', folder: '.', kind: 'pdf',
  native_w_mm: 80, native_h_mm: 60, mtime: 1, script: 'fig1.py',
}
/** 非空 = 接管 /candidates：按游标出页（后端是先翻页、后过滤归属，所以一页可以是空的而 next_cursor 仍在） */
let candidatePages: ((cursor: string | null, n: number) => Response) | null = null
let candidateCalls = 0
const row = (id: string) => ({
  doc_id: id, name: id, updatedAt: 1, mtime: 1, revision: 'r', schema: 3, status: 'ok',
  canvases: 1, objects: 1, owner: 'this', evidence: 'owners',
})
const page = (items: string[], next: string | null) =>
  new Response(JSON.stringify({ items: items.map(row), next_cursor: next, index: 'ok' }), { status: 200 })

/** 非空 = autosave 的 PUT 卡在这里（模拟「排队的保存在切项目之后才完成」） */
let putHold: Promise<void> | null = null
let putsHeld = 0
/** true = autosave GET 的回包被剥掉 X-Tavotto-Revision（模拟代理吃头） */
let stripRevisionHeader = false
/** 非 undefined = /owner 回的 revision 强制成这个值（模拟「复核之后正文又被换了」） */
let ownerRevisionOverride: string | null | undefined

const fetchImpl = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://127.0.0.1')
  if (putHold && init?.method === 'PUT' && u.pathname.startsWith('/api/autosave/')) {
    putsHeld += 1
    await putHold
  }
  if (candidatePages && u.pathname === '/api/layout-session/candidates') {
    candidateCalls += 1
    return candidatePages(u.searchParams.get('cursor'), candidateCalls)
  }
  if (u.pathname === '/api/panels') {
    return new Response(JSON.stringify({ panels: [fig1Info], figures_dir: '/figs/a' }), { status: 200 })
  }
  if (u.pathname === '/api/runtime/assets') return new Response(JSON.stringify({ assets: [] }), { status: 200 })
  if (u.pathname.startsWith('/api/projects')) {
    return new Response(JSON.stringify({ recent: [], pinned: [], projects: [] }), { status: 200 })
  }
  if (stripRevisionHeader && (init?.method ?? 'GET') === 'GET' && /^\/api\/autosave\/[^/]+$/.test(u.pathname)) {
    const res = await be.fetch(url as RequestInfo, init)
    return new Response(await res.text(), { status: res.status })
  }
  // 真后端的 /owner 回「它算结论时槽位的修订号」；假后端不回，这里按此刻槽位补上（用例可用 ownerRevisionOverride 改写）
  if (u.pathname === '/api/layout-session/owner') {
    const res = await be.fetch(url as RequestInfo, init)
    if (!res.ok) return res
    const body = (await res.json()) as Record<string, unknown>
    const id = u.searchParams.get('doc_id') ?? ''
    const revision = ownerRevisionOverride !== undefined ? ownerRevisionOverride : (be.state.slots.get(id)?.revision ?? null)
    return new Response(JSON.stringify({ ...body, revision }), { status: 200 })
  }
  return be.fetch(url as RequestInfo, init)
}) as typeof fetch

const gate = () => {
  let release!: () => void
  const promise = new Promise<void>((r) => (release = r))
  return { promise, release }
}

async function boot(pj = 'p_a') {
  vi.resetModules()
  const session = await import('@/lib/session')
  session.setCurrentProjectId(pj)
  const doc = await import('@/store/documentStore')
  const restore = await import('@/store/documentRestore')
  const proj = await import('@/store/projectStore')
  restore.setRestoreRetryDelays([0, 0])
  proj.useProjectStore.setState({
    phase: 'open', project: { open: true, id: pj, figures_dir: `/figs/${pj.slice(2)}` }, lastDocumentIssue: null,
  })
  return { doc, restore, proj, session }
}
type App = Awaited<ReturnType<typeof boot>>

const ds = (app: App) => app.doc.useDocumentStore.getState()
const objectIds = (app: App) => ds(app).doc.objects.map((o) => o.id)
const autosavePuts = () => be.puts().filter((c) => c.startsWith('PUT autosave'))
const lastPuts = () => be.puts().filter((c) => c.startsWith('PUT last'))
const reached = (pred: () => boolean) => vi.waitFor(() => expect(pred()).toBe(true))

/** 种：d_saved 是 p_a 的 durable last，正文在盘上，owners 记着它属于 p_a */
function seedSavedLast(id = 'd_saved', name = 'Saved Fig') {
  be.putSlot(id, savedProject(name), 'p_a')
  be.setLast('p_a', id, name)
}

beforeEach(() => {
  putHold = null
  putsHeld = 0
  stripRevisionHeader = false
  ownerRevisionOverride = undefined
  candidatePages = null
  candidateCalls = 0
  localStorage.clear()
  sessionStorage.clear()
  be.reset()
  vi.stubGlobal('fetch', fetchImpl)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('恢复门：占位文档零持久化副作用（不变量 1）', () => {
  it('A02 / R1：恢复在路上（last GET 被卡住）用户改页宽 / 加参考线 / 加画布 / 改名 → 全被门拒掉，恢复完成后原文档原样回来', async () => {
    seedSavedLast()
    const app = await boot()
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await reached(() => be.state.calls.includes('GET session'))
    expect(app.doc.getRestoreGate().state).toBe('restoring')

    const s = ds(app)
    const before = s.doc
    s.commit(literal('改页宽'), (d) => { d.page.w = 123 })
    s.commit(literal('加参考线'), (d) => { d.guides.push({ axis: 'x', pos: 10 }) })
    s.addCanvas('抢先画布')
    s.renameProject('抢先改名')
    expect(ds(app).doc).toBe(before)
    expect(ds(app).canvases).toHaveLength(1)
    expect(ds(app).dirty).toBe(false)

    hold.release()
    expect(await restoring).toBe(true)
    expect(ds(app).documentId).toBe('d_saved')
    expect(objectIds(app)).toEqual(['panel_saved'])
    const p = ds(app).doc.objects[0] as PanelObject
    expect(p.overrides).toEqual([{ gid: 'figure', prop: 'facecolor', value: '#f00' }])
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'restored', docId: 'd_saved' })
    // 装载本身不产生无理由的写（A01）：没有 autosave PUT，也没有往 last 再推一次
    expect(autosavePuts()).toEqual([])
    expect(lastPuts()).toEqual([])
  })

  it('占位文档不写 currentDoc / 最近文档 / 归属 / last / 自动保存队列（Picker 切项目，last GET 被卡住）', async () => {
    seedSavedLast()
    const app = await boot()
    // 先在 p_a 里有一份真文档，写进 currentDoc / 索引
    await ds(app).switchDocument(savedProject('旧项目的排版'), 'd_prev')
    expect(localStorage.getItem('tavotto.currentDoc')).toBe('d_prev')
    be.state.calls.length = 0

    const hold = gate()
    be.state.hold.session = hold.promise
    const adopting = app.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    await reached(() => be.state.calls.includes('GET session'))
    // 离开旧文档的那次冲刷（设门之前）已经做完；从这一刻起，占位期间什么都不许再写
    const currentBefore = localStorage.getItem('tavotto.currentDoc')
    const indexBefore = localStorage.getItem('tavotto.docIndex')
    expect(currentBefore).toBe('d_prev')
    await reached(() => be.state.slots.has('d_prev'))
    be.state.calls.length = 0

    // 占位已经换上（不是 d_prev），但它什么都没写
    expect(ds(app).documentId).not.toBe('d_prev')
    expect(app.doc.isDocumentPlaceholder()).toBe(true)
    expect(localStorage.getItem('tavotto.currentDoc')).toBe(currentBefore)
    expect(localStorage.getItem('tavotto.docIndex')).toBe(indexBefore)
    expect(app.doc.flushAutosave()).toBe('skipped')
    expect((await app.doc.saveNowWithResult()).wrote).toBe(false)
    // 想往占位里写东西（任何入口）都不成
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t1', 'x')) })
    ds(app).silent((d) => { d.page.w = 77 })
    app.doc.applyDerivedUpdate({ doc: { ...ds(app).doc, page: { ...ds(app).doc.page, w: 66 } } })
    expect(ds(app).doc.objects).toEqual([])
    expect(ds(app).doc.page.w).not.toBe(77)
    expect(ds(app).doc.page.w).not.toBe(66)
    expect(be.writes()).toEqual([])

    hold.release()
    await adopting
    expect(app.doc.isDocumentPlaceholder()).toBe(false)
    // 新项目没有历史：占位在确认之后转正，仍然没有为占位写过东西
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty', pj: 'p_b' })
    expect(localStorage.getItem('tavotto.currentDoc')).toBe(currentBefore)
  })

  it('离开旧文档的冲刷在设门之前：未保存的最后一笔照常落盘，DiskWriter 已排的队不清', async () => {
    seedSavedLast()
    const app = await boot()
    await ds(app).switchDocument(emptyProject(), 'd_prev')
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t_last', '最后一笔')) })
    // 还在防抖窗口里（没人调 flush）：切项目离开那一下必须把它写出去
    const hold = gate()
    be.state.hold.session = hold.promise
    const adopting = app.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    await reached(() => be.state.calls.includes('GET session'))
    await reached(() => be.state.slots.has('d_prev'))
    expect(JSON.parse(be.state.slots.get('d_prev')!.body).canvases[0].objects.map((o: { id: string }) => o.id)).toEqual([
      't_last',
    ])
    hold.release()
    await adopting
  })

  it('needs_user 期间用户的编辑被拒时，状态条说一句「上次的排版还没打开」（同一句节流，不刷屏）', async () => {
    seedSavedLast()
    be.state.fail.session = 500
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    const ui = (await import('@/store/uiStore')).useUiStore
    const spy: string[] = []
    const unsub = ui.subscribe((s) => {
      const key = (s.status as { key?: string } | null)?.key
      if (key) spy.push(key)
    })
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t1', 'x')) })
    ds(app).commit(literal('再加'), (d) => { d.objects.push(text('t2', 'y')) })
    unsub()
    expect(spy.filter((k) => k === 'restore.blockedWrite')).toHaveLength(1)
    expect(ds(app).doc.objects).toEqual([])
  })
})

describe('统一恢复：冷启动 / Picker / 重试同一条（不变量 2、3）', () => {
  it('A04：Picker 打开项目，last GET 第一次 500、第二次 200、无项目缓存 → 有界重试后恢复，不停在 reset 空白', async () => {
    be.putSlot('d_saved', savedProject('Saved Fig'), 'p_b')
    be.setLast('p_b', 'd_saved', 'Saved Fig')
    be.state.failOnce.session = 500
    const app = await boot()
    await app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    expect(be.state.calls.filter((c) => c === 'GET session')).toHaveLength(2)
    expect(ds(app).documentId).toBe('d_saved')
    expect(objectIds(app)).toEqual(['panel_saved'])
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
    expect(ds(app).restoreIssue).toBeNull()
  })

  it('A04：last 一直读不到（500）且没有任何本机线索 → needs_user{session_unavailable}，不是空项目；后端好了点重试 → 恢复', async () => {
    be.putSlot('d_saved', savedProject('Saved Fig'), 'p_b')
    be.setLast('p_b', 'd_saved', 'Saved Fig')
    be.state.fail.session = 500
    const app = await boot()
    await app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    // 1 次 + 2 次重试，然后给出口
    expect(be.state.calls.filter((c) => c === 'GET session')).toHaveLength(3)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'session_unavailable', ref: null })
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    expect(app.doc.isDocumentPlaceholder()).toBe(true)
    // 薄适配：还不知道 docId 时没有 ref
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()

    delete be.state.fail.session
    const out = await app.restore.retryRestore()
    expect(out).toMatchObject({ kind: 'restored', id: 'd_saved' })
    expect(ds(app).documentId).toBe('d_saved')
    expect(ds(app).restoreIssue).toBeNull()
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'restored' })
  })

  it('A05：last 正确、正文第一次 GET=500 → 冷启动重试后恢复；持续 500 → needs_user{unavailable}+ref，重试可恢复，无假「已恢复」', async () => {
    seedSavedLast()
    be.state.failGetDoc.set('d_saved', 500)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(be.state.calls.filter((c) => c === 'GET autosave d_saved')).toHaveLength(3)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable', ref: { id: 'd_saved' } })
    // 横幅 / 芯片读的薄适配：有 ref 时同步
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toMatchObject({ id: 'd_saved' })
    expect(ds(app).documentId).not.toBe('d_saved')
    expect(ds(app).docNotice).toBeNull()

    be.state.failGetDoc.delete('d_saved')
    // 经薄适配的「打开上次文档」走同一条协调
    expect(await app.proj.useProjectStore.getState().openLastDocument()).toBe(true)
    expect(ds(app).documentId).toBe('d_saved')
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
  })

  it('A05：同一份证据，Picker 与冷启动给同一个结局（正文持续 500 → 同一个 RestoreIssue 形状）', async () => {
    seedSavedLast()
    be.state.failGetDoc.set('d_saved', 500)
    const cold = await boot()
    await cold.doc.restoreSession()
    const coldIssue = ds(cold).restoreIssue
    const picker = await boot()
    await picker.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    const pickerIssue = ds(picker).restoreIssue
    expect(pickerIssue).toMatchObject({ kind: coldIssue!.kind, ref: coldIssue!.ref, alternatives: coldIssue!.alternatives })
  })

  it('A06：没有 last / 项目缓存，只有全局 currentDoc——本机索引记着属于本项目：冷启动与 Picker 都恢复它', async () => {
    be.putSlot('d_cur', savedProject('Cur'))
    localStorage.setItem('tavotto.currentDoc', 'd_cur')
    localStorage.setItem(
      'tavotto.docIndex',
      JSON.stringify([{ id: 'd_cur', name: 'Cur', savedAt: 1, objects: 1, projectId: 'p_a' }]),
    )
    const cold = await boot()
    expect(await cold.doc.restoreSession()).toBe(true)
    expect(ds(cold).documentId).toBe('d_cur')

    // 同一份证据走 Picker（线索在 reset 之前捕获，reset 不毁候选）
    const picker = await boot()
    await picker.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(ds(picker).documentId).toBe('d_cur')
  })

  it('A06：只有 currentDoc 且本机不知道归属、后端 owners=this → 同样恢复；owners=other → 不恢复也不 needs_user', async () => {
    be.putSlot('d_cur', savedProject('Cur'), 'p_a')
    localStorage.setItem('tavotto.currentDoc', 'd_cur')
    const ok = await boot()
    expect(await ok.doc.restoreSession()).toBe(true)
    expect(ds(ok).documentId).toBe('d_cur')

    // 又一次全新启动（新 origin：本机什么都没有），后端说它是别的项目的
    localStorage.clear()
    be.state.sessions.clear() // 第一轮恢复把它记成了 p_a 的 last；真后端会按 owners 滤掉，假后端不滤，这里直接清
    localStorage.setItem('tavotto.currentDoc', 'd_cur')
    be.state.owners.set('d_cur', 'p_other')
    const no = await boot()
    expect(await no.doc.restoreSession()).toBe(false)
    expect(ds(no).documentId).not.toBe('d_cur')
    expect(no.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty' })
    expect(ds(no).restoreIssue).toBeNull()
  })

  it('切到空项目：旧项目全局 currentDoc 的归属接口暂不可用，仍进入空白', async () => {
    be.putSlot('d_old', savedProject('旧项目文档'), 'p_a')
    localStorage.setItem('tavotto.currentDoc', 'd_old')
    localStorage.setItem('tavotto.docIndex', JSON.stringify([
      { id: 'd_old', name: '旧项目文档', savedAt: 1, objects: 1, projectId: 'p_a' },
    ]))
    be.state.fail.owner = 500
    const app = await boot('p_b')
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty', pj: 'p_b' })
    expect(ds(app).restoreIssue).toBeNull()
    expect(ds(app).documentId).not.toBe('d_old')
    expect(be.writes()).toEqual([])
  })

  it('A07：last=B（B 404），同项目另有已验证的 A（prev）→ 保留 B 的失败原因，提供 A；不静默换旧稿；选 A 才打开 A，B/A 的正文原样', async () => {
    be.putSlot('d_a', savedProject('Older A'), 'p_a')
    const aBytes = be.state.slots.get('d_a')!.body
    be.setLast('p_a', 'd_b', 'Newest B')
    be.session('p_a').prev = { doc_id: 'd_a', name: 'Older A', at: 5 }
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    const issue = ds(app).restoreIssue!
    expect(issue).toMatchObject({ kind: 'missing', ref: { id: 'd_b', name: 'Newest B' } })
    expect(issue.alternatives.map((a) => a.id)).toEqual(['d_a'])
    expect(ds(app).documentId).not.toBe('d_a')
    expect(app.doc.isDocumentPlaceholder()).toBe(true)
    // 探测是只读的：没有任何写
    expect(be.writes()).toEqual([])
    expect(localStorage.getItem('tavotto.currentDoc')).toBeNull()

    const out = await app.restore.openAlternative('d_a')
    expect(out).toMatchObject({ kind: 'restored', id: 'd_a' })
    expect(ds(app).documentId).toBe('d_a')
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'explicit' })
    expect(be.state.slots.get('d_a')!.body).toBe(aBytes)
    expect(be.state.slots.has('d_b')).toBe(false)
  })

  it('A09 / R3：打开意图（pending）还没有正文——硬中断后重启，退回已持久的 durable last，不悬空；这不是故障', async () => {
    seedSavedLast('d_durable', 'Durable A')
    be.setPending('p_a', 'd_new_b', '新文档 B')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_durable')
    expect(ds(app).restoreIssue).toBeNull()
    expect(be.writes()).toEqual([])
  })

  it('A32：B 只有本机正文（后端保存失败），durable last 仍是 A → 同 origin 重启恢复 B，保留 B 的 id；A 的正文不被动', async () => {
    seedSavedLast('d_a', 'Durable A')
    const aBytes = be.state.slots.get('d_a')!.body
    const b = savedProject('Local only B', [panel({ id: 'panel_b' })])
    localStorage.setItem('tavotto.autosave.d_b', JSON.stringify(b))
    localStorage.setItem(
      'tavotto.projectDoc.p_a',
      // 待确认的打开意图：写下它时本机见过的后端最新记录时刻（pendingBase）不早于后端 last → 本机赢
      JSON.stringify({ id: 'd_b', name: 'Local only B', seenAt: 1, pendingBase: 999, pendingGen: 'g1' }),
    )
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_b')
    expect(objectIds(app)).toEqual(['panel_b'])
    // B 的本机副本在磁盘 404 时被认作文档本身并推上去；A 一个字节没动，B 没有塞进 A 的槽位
    await reached(() => be.state.slots.has('d_b'))
    expect(be.state.slots.get('d_a')!.body).toBe(aBytes)
  })

  it('探测零副作用：本机副本比磁盘新（recovery_slot 待裁决）——probe 不挪键，accept 才挪', async () => {
    be.putSlot('d_x', savedProject('main'), 'p_a')
    const newer = savedProject('crashed')
    newer.updatedAt = 500
    localStorage.setItem('tavotto.autosave.d_x', JSON.stringify(newer))
    const app = await boot()
    const probe = await app.doc.probeDocument('d_x', 'p_a')
    expect(probe).toMatchObject({ kind: 'found', from: 'disk', pendingRecovery: 'local_newer' })
    expect(probe.doc!.project.name).toBe('main')
    expect(localStorage.getItem('tavotto.autosave.d_x')).not.toBeNull()
    expect(localStorage.getItem('tavotto.recovery.d_x')).toBeNull()
    expect(be.writes()).toEqual([])

    const notice = app.doc.acceptProbe(probe, 'p_a')
    expect(notice).toMatchObject({ kind: 'recovery', docId: 'd_x' })
    expect(localStorage.getItem('tavotto.autosave.d_x')).toBeNull()
    expect(localStorage.getItem('tavotto.recovery.d_x')).not.toBeNull()
  })

  it('A18：磁盘 schema 比本构建新 → 不打开不覆盖，needs_user{too_new}+旧提示，磁盘字节不动、无 PUT；本机副本不被碰', async () => {
    const future = { ...savedProject('future'), schema: 99 }
    be.putSlot('d_future', future, 'p_a')
    be.setLast('p_a', 'd_future', 'future')
    const bytes = be.state.slots.get('d_future')!.body
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'too_new', ref: { id: 'd_future' } })
    expect(ds(app).docNotice).toMatchObject({ kind: 'schema_too_new', docId: 'd_future', schema: 99 })
    expect(be.state.slots.get('d_future')!.body).toBe(bytes)
    expect(be.writes()).toEqual([])
  })

  it('A14：last / 缓存 / currentDoc 全无，后端发现恰好 1 份本项目有效槽位 → 自动采用并说一句来源；≥2 份 → choose；0 份 → 空', async () => {
    be.putSlot('d_only', savedProject('唯一一份'), 'p_a')
    be.state.candidates = [{ doc_id: 'd_only', scope: 'project', name: '唯一一份' }]
    const one = await boot()
    expect(await one.doc.restoreSession()).toBe(true)
    expect(ds(one).documentId).toBe('d_only')
    const ui = (await import('@/store/uiStore')).useUiStore
    expect((ui.getState().status as { key?: string } | null)?.key).toBe('restore.autoAdopted')

    // 又一次全新启动（新 origin），后端里多了一份
    localStorage.clear()
    be.state.sessions.clear()
    be.putSlot('d_two', savedProject('第二份'), 'p_a')
    be.state.candidates = [
      { doc_id: 'd_only', scope: 'project', name: '唯一一份' },
      { doc_id: 'd_two', scope: 'project', name: '第二份' },
    ]
    const two = await boot()
    expect(await two.doc.restoreSession()).toBe(false)
    expect(ds(two).restoreIssue).toMatchObject({ kind: 'choose', ref: null })
    expect(ds(two).restoreIssue!.alternatives.map((a) => a.id).sort()).toEqual(['d_only', 'd_two'])
    expect(two.doc.isDocumentPlaceholder()).toBe(true)

    localStorage.clear()
    be.state.sessions.clear()
    be.state.candidates = []
    const none = await boot()
    expect(await none.doc.restoreSession()).toBe(false)
    expect(none.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty' })
  })

  it('A28：全新项目没有恢复目标，只有归属未知的旧槽位 → 顺畅进入空白，不弹、不等', async () => {
    be.putSlot('d_unknown', savedProject('归属未知'))
    be.state.candidates = [{ doc_id: 'd_unknown', scope: 'unknown', name: '归属未知' }]
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty' })
    expect(ds(app).restoreIssue).toBeNull()
    // 发现只问了本项目明确归属的那一档
    expect(be.state.calls.filter((c) => c.startsWith('GET candidates'))).toEqual(['GET candidates project'])
  })

  it('旧后端（会话端点 404）：不发现、按本机旧逻辑——没有线索就是空项目', async () => {
    be.state.fail.session = 404
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty' })
    expect(be.state.calls.some((c) => c.startsWith('GET candidates'))).toBe(false)
  })
})

describe('取代与代次（不变量 5）', () => {
  it('A21：恢复在路上（last GET 被卡住）时切到另一个项目——旧恢复回来没有提交权，不改新项目的文档 / 提示', async () => {
    seedSavedLast()
    const app = await boot()
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await reached(() => be.state.calls.includes('GET session'))
    const oldGen = app.doc.getRestoreGate().gen

    // 切到 p_b（它的 session GET 也被卡在同一个 gate 上，先放行旧的之后新的才继续）
    const adopting = app.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    await reached(() => app.doc.getRestoreGate().pj === 'p_b')
    expect(app.doc.getRestoreGate().gen).toBeGreaterThan(oldGen)

    hold.release()
    expect(await restoring).toBe(false)
    await adopting
    expect(ds(app).documentId).not.toBe('d_saved')
    expect(app.doc.getRestoreGate()).toMatchObject({ pj: 'p_b', state: 'ready', reason: 'empty' })
    expect(ds(app).restoreIssue).toBeNull()
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
  })

  it('显式新建取代未落定的恢复：旧恢复晚到的响应不覆盖用户的新文档；等待者拿 ready', async () => {
    seedSavedLast()
    const app = await boot()
    const hold = gate()
    be.state.hold.autosaveGet = hold.promise
    const restoring = app.doc.restoreSession()
    await reached(() => be.state.calls.includes('GET autosave d_saved'))
    const waiting = app.doc.whenDocumentReady('p_a')

    const actions = await import('@/store/actions')
    await actions.newBlankDocument()
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'explicit', pj: 'p_a' })
    expect(await waiting).toBe('ready')
    const blankId = ds(app).documentId

    hold.release()
    expect(await restoring).toBe(false)
    expect(ds(app).documentId).toBe(blankId)
    expect(ds(app).documentId).not.toBe('d_saved')
    // 用户的新文档能正常写
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t1', 'x')) })
    expect(objectIds(app)).toEqual(['t1'])
  })

  it('A→B→A 是新的生命周期：回到 A 重新恢复，不因为「恢复过」而跳过', async () => {
    be.putSlot('d_a', savedProject('A'), 'p_a')
    be.setLast('p_a', 'd_a', 'A')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_a')
    const gen1 = app.doc.getRestoreGate().gen

    await app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    expect(ds(app).documentId).not.toBe('d_a')
    expect(app.doc.getRestoreGate().pj).toBe('p_b')

    await app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(ds(app).documentId).toBe('d_a')
    expect(app.doc.getRestoreGate()).toMatchObject({ pj: 'p_a', reason: 'restored' })
    expect(app.doc.getRestoreGate().gen).toBeGreaterThan(gen1)
  })

  it('restoreSession 不再按 currentDoc === documentId 短路：同 id 但这一代还没落定，照样读后端', async () => {
    seedSavedLast()
    localStorage.setItem('tavotto.currentDoc', 'd_saved')
    const app = await boot()
    // 内存里恰好是同一个 id 的空白文档（旧实现零请求退出）
    await ds(app).switchDocument(emptyProject(), 'd_saved')
    app.doc.resetRestoreGateForTests()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(be.state.calls).toContain('GET session')
    expect(objectIds(app)).toEqual(['panel_saved'])
  })

  it('已落定的恢复：工作台重挂（Picker 回来 / StrictMode 第二轮）直接回落定结果，不再读盘、不覆盖已改的文档', async () => {
    seedSavedLast()
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    ds(app).commit(literal('改一笔'), (d) => { d.objects.push(text('t_user', '用户的编辑')) })
    be.state.calls.length = 0
    expect(await app.doc.restoreSession()).toBe(false)
    expect(be.state.calls).toEqual([])
    expect(objectIds(app)).toEqual(['panel_saved', 't_user'])
  })

  it('StrictMode 双挂载：第二轮加入在路上的那次恢复，不开第二轮', async () => {
    seedSavedLast()
    const app = await boot()
    const hold = gate()
    be.state.hold.session = hold.promise
    const first = app.doc.restoreSession()
    await reached(() => be.state.calls.includes('GET session'))
    const second = app.doc.restoreSession()
    hold.release()
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(be.state.calls.filter((c) => c === 'GET session')).toHaveLength(1)
  })
})

describe('whenDocumentReady 的终局', () => {
  it('needs_user 期间继续等，超时给 timeout；换项目给 superseded；ready 给 ready', async () => {
    seedSavedLast()
    be.state.fail.session = 500
    const app = await boot()
    await app.doc.restoreSession()
    expect(app.doc.getRestoreGate().state).toBe('needs_user')

    expect(await app.doc.whenDocumentReady('p_a', { timeoutMs: 5 })).toBe('timeout')
    const waiting = app.doc.whenDocumentReady('p_a')
    delete be.state.fail.session
    await app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    expect(await waiting).toBe('superseded')
    expect(await app.doc.whenDocumentReady('p_b')).toBe('ready')
    // 问的是别的项目 → 已经不是它的门
    expect(await app.doc.whenDocumentReady('p_a')).toBe('superseded')
  })
})

describe('继续新建 / 打开另一份（出口）', () => {
  it('继续新建：占位转成正式的新文档，旧候选与旧正文一个字节不动，之后能写；晚到的旧响应无提交权', async () => {
    seedSavedLast()
    const bytes = be.state.slots.get('d_saved')!.body
    be.state.failGetDoc.set('d_saved', 500)
    const app = await boot()
    await app.doc.restoreSession()
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable' })

    expect(await app.restore.continueNew()).toBe(true)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'continued_new' })
    expect(ds(app).restoreIssue).toBeNull()
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t1', 'x')) })
    expect(objectIds(app)).toEqual(['t1'])
    expect(be.state.slots.get('d_saved')!.body).toBe(bytes)
    expect(app.doc.getRestoreGate().state).toBe('ready')
    // 没有需要处理的问题了：continueNew 再调是空操作
    expect(await app.restore.continueNew()).toBe(false)
  })

  it('「知道了」（dismissLastDocumentIssue）= 接受现状往下走：不会留一扇永远关着的门', async () => {
    seedSavedLast()
    be.state.failGetDoc.set('d_saved', 500)
    const app = await boot()
    await app.doc.restoreSession()
    app.proj.useProjectStore.getState().dismissLastDocumentIssue()
    await reached(() => app.doc.getRestoreGate().state === 'ready')
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t1', 'x')) })
    expect(objectIds(app)).toEqual(['t1'])
  })

  it('恢复问题与保存进度正交：saved → clean 清不掉未决的恢复问题', async () => {
    seedSavedLast()
    be.state.failGetDoc.set('d_saved', 500)
    const app = await boot()
    await app.doc.restoreSession()
    const issue = ds(app).restoreIssue
    expect(issue).not.toBeNull()
    app.doc.useDocumentStore.setState({ saveState: 'saved' })
    app.doc.useDocumentStore.setState({ saveState: 'clean' })
    expect(ds(app).restoreIssue).toBe(issue)
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
  })
})

describe('教程 prepare（A20）', () => {
  it('准备成功（返回 true 且文档真的装进来）→ prepared，第一笔编辑不丢', async () => {
    const app = await boot()
    await app.proj.useProjectStore.getState().adoptOpenedProject(
      { open: true, id: 'p_t', figures_dir: '/figs/t' },
      {
        prepareDocument: async () => {
          const ok = await ds(app).switchDocument(savedProject('教程', [panel({ id: 'panel_tut' })]), 'd_tutorial')
          return ok
        },
      },
    )
    expect(ds(app).documentId).toBe('d_tutorial')
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'prepared', pj: 'p_t' })
    ds(app).commit(literal('第一笔'), (d) => { d.objects.push(text('t_first', '第一笔')) })
    expect(objectIds(app)).toEqual(['panel_tut', 't_first'])
  })

  it('准备失败（回调吞掉装载失败、正常返回 false）→ needs_user{prepare_failed}，初始空白不标 ready，出口是继续新建', async () => {
    const app = await boot()
    await app.proj.useProjectStore.getState().adoptOpenedProject(
      { open: true, id: 'p_t', figures_dir: '/figs/t' },
      { prepareDocument: async () => false },
    )
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'prepare_failed', ref: null })
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t1', 'x')) })
    expect(ds(app).doc.objects).toEqual([])
    expect(await app.restore.continueNew()).toBe(true)
    ds(app).commit(literal('加字'), (d) => { d.objects.push(text('t1', 'x')) })
    expect(objectIds(app)).toEqual(['t1'])
  })

  it('准备「返回 true 却什么都没装」不算成功（不用 Promise resolve / id 相等冒充）', async () => {
    const app = await boot()
    await app.proj.useProjectStore.getState().adoptOpenedProject(
      { open: true, id: 'p_t', figures_dir: '/figs/t' },
      { prepareDocument: async () => true },
    )
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'prepare_failed' })
  })

  it('skip-restore 一次消费、共享决定：这次启动开空白，StrictMode 第二轮也尊重，不读后端、不恢复崩溃的文档', async () => {
    seedSavedLast()
    const app = await boot()
    app.doc.requestBlankStart()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'blank_requested' })
    expect(await app.doc.restoreSession()).toBe(false) // 第二轮
    expect(sessionStorage.getItem('tavotto:skip-restore')).toBeNull()
    expect(ds(app).documentId).not.toBe('d_saved')
    expect(be.state.calls).toEqual([])
    // 下一次（新项目 / 新生命周期）不再有这个标记，正常恢复
    await app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(ds(app).documentId).toBe('d_saved')
  })
})

describe('保存成功 → 元数据（CONTRACT 2.6）', () => {
  it('正文成功、元数据失败：restoreMetaIssue 亮起，saveState 不受影响；下次成功清掉；监听器拿到的是排队那一刻的 pj', async () => {
    const app = await boot()
    const seen: [string, string | null, unknown][] = []
    app.doc.onDocumentPersisted((id, pj, metadata) => seen.push([id, pj, metadata]))
    be.state.metadata = 'failed'
    await ds(app).switchDocument(savedProject('M'), 'd_m')
    await app.doc.saveNow()
    expect(seen.at(-1)).toEqual(['d_m', 'p_a', 'failed'])
    expect(ds(app).restoreMetaIssue).toEqual({ docId: 'd_m', pj: 'p_a' })
    expect(ds(app).saveState).toBe('saved')

    be.state.metadata = 'ok'
    await app.doc.saveNow()
    expect(ds(app).restoreMetaIssue).toBeNull()
  })

  it('saved → clean 的定时器清不掉「恢复信息没更新」（两根轴互不覆盖）', async () => {
    const app = await boot()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      be.state.metadata = 'failed'
      await ds(app).switchDocument(savedProject('M'), 'd_m')
      await app.doc.saveNow()
      expect(ds(app).saveState).toBe('saved')
      await vi.advanceTimersByTimeAsync(5000)
      expect(ds(app).saveState).toBe('clean')
      expect(ds(app).restoreMetaIssue).toEqual({ docId: 'd_m', pj: 'p_a' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('A11 / 非当前文档的保存也落元数据那一半：A 的排队保存晚于 B 的打开完成——监听器收到 A，UI 那一半（lastPersisted / 提示）不动', async () => {
    const app = await boot()
    const seen: string[] = []
    app.doc.onDocumentPersisted((id) => seen.push(id))
    await ds(app).switchDocument(savedProject('A'), 'd_a')
    // A 的 PUT 排在队里（flush 是同步排队），马上换到 B
    await ds(app).switchDocument(savedProject('B'), 'd_b')
    const lastPersistedB = ds(app).lastPersisted
    await app.doc.saveNow()
    await reached(() => seen.includes('d_a') && seen.includes('d_b'))
    // 当前文档仍是 B；A 的成功没有改 B 的 UI 状态
    expect(ds(app).documentId).toBe('d_b')
    expect(ds(app).restoreMetaIssue).toBeNull()
    expect(lastPersistedB === null || typeof lastPersistedB === 'number').toBe(true)
  })
})

describe('Codex P1：durable 后备 / 发现翻页 / 发现不可用', () => {
  it('P1-a：纯意图正文缺失，durable last 也读不出来——不越过它悄悄装 prev；needs_user 指着 last，prev 留作出口', async () => {
    be.putSlot('d_prev', savedProject('Prev'), 'p_a')
    be.session('p_a').prev = { doc_id: 'd_prev', name: 'Prev', at: 3 }
    be.setLast('p_a', 'd_last', 'Durable last') // 槽位不在
    be.setPending('p_a', 'd_intent', '新打开的')   // 更晚的打开意图，也没有正文
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_prev')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'missing', ref: { id: 'd_last' } })
    expect(ds(app).restoreIssue!.alternatives.map((a) => a.id)).toEqual(['d_prev'])
    expect(be.writes()).toEqual([])
  })

  it('P1-a 对照：last 能读就装 last（不是 prev）', async () => {
    be.putSlot('d_prev', savedProject('Prev'), 'p_a')
    be.session('p_a').prev = { doc_id: 'd_prev', name: 'Prev', at: 3 }
    seedSavedLast('d_last', 'Durable last')
    be.setPending('p_a', 'd_intent', '新打开的')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_last')
  })

  it('P1-b：第一页是空的但 next_cursor 还在，后面才有唯一一份 → 翻页后自动采用（不是 empty）', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = (cursor) => (cursor === null ? page([], 'c1') : cursor === 'c1' ? page([], 'c2') : page(['d_only'], null))
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_only')
    expect(candidateCalls).toBe(3)
  })

  it('P1-b：第一页看到一份、后面还有一份 → 是 choose，不是自动采用', async () => {
    be.putSlot('d_one', savedProject('一'), 'p_a')
    be.putSlot('d_two', savedProject('二'), 'p_a')
    candidatePages = (cursor) => (cursor === null ? page(['d_one'], 'c1') : page(['d_two'], null))
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'choose' })
    expect(ds(app).restoreIssue!.alternatives.map((a) => a.id).sort()).toEqual(['d_one', 'd_two'])
    expect(ds(app).documentId).not.toBe('d_one')
  })

  it('P1-b：翻到上限游标还在 → 数不清：不自动采用、不当空；看到 ≥1 份给 choose，一份都没看到给重试出口', async () => {
    be.putSlot('d_seen', savedProject('见到的'), 'p_a')
    candidatePages = (_cursor, n) => page(n === 3 ? ['d_seen'] : [], `c${n}`) // 永远有下一页
    const some = await boot()
    expect(await some.doc.restoreSession()).toBe(false)
    expect(candidateCalls).toBe(10)
    expect(ds(some).restoreIssue).toMatchObject({ kind: 'choose' })
    expect(ds(some).documentId).not.toBe('d_seen')

    candidatePages = (_c, n) => page([], `c${n}`)
    candidateCalls = 0
    localStorage.clear()
    const none = await boot()
    expect(await none.doc.restoreSession()).toBe(false)
    expect(ds(none).restoreIssue).toMatchObject({ kind: 'index_degraded', ref: null })
    expect(none.doc.getRestoreGate().state).toBe('needs_user')
  })

  it('P1-c：发现端点 5xx 重试用尽 → needs_user（可重试），不是 empty；好了点重试 → 恢复', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = () => new Response('{}', { status: 503 })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(candidateCalls).toBe(3) // 1 次 + 2 次重试
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'session_unavailable', ref: null })
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    candidatePages = (_c) => page(['d_only'], null)
    expect(await app.restore.retryRestore()).toMatchObject({ kind: 'restored', id: 'd_only' })
  })

  it('P1-c：发现端点第一次 5xx、重试 200 → 照常采用', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = (_c, n) => (n === 1 ? new Response('{}', { status: 500 }) : page(['d_only'], null))
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_only')
  })

  it('P1-c 对照：发现端点 404（旧后端）才可以是 empty', async () => {
    candidatePages = () => new Response('{}', { status: 404 })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty' })
  })
})

describe('Codex P1 复查：被扣下的 durable last 不能被越过', () => {
  /** last=B 被计划扣下（本机索引说它是别的项目的，/owner 又问不到）；prev=A 可读 */
  function seedWithheldLast(withPending: boolean) {
    be.putSlot('d_a', savedProject('Older A'), 'p_a')
    be.session('p_a').prev = { doc_id: 'd_a', name: 'Older A', at: 3 }
    be.putSlot('d_b', savedProject('Newest B'), 'p_a')
    be.setLast('p_a', 'd_b', 'Newest B')
    if (withPending) be.setPending('p_a', 'd_c', '新打开的') // 纯打开意图，没有正文
    localStorage.setItem(
      'tavotto.docIndex',
      JSON.stringify([{ id: 'd_b', name: 'Newest B', savedAt: 1, objects: 1, projectId: 'p_other' }]),
    )
    be.state.fail.owner = 500
  }

  it.each([
    ['纯意图正文缺失', true],
    ['没有意图、计划的首选落到了 prev', false],
  ])('%s：last 被扣下 → needs_user 指着 last，prev 只作出口，绝不自动装 prev', async (_n, withPending) => {
    seedWithheldLast(withPending)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_a')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable', ref: { id: 'd_b' } })
    expect(ds(app).restoreIssue!.alternatives.map((a) => a.id)).toEqual(['d_a'])
    expect(app.doc.isDocumentPlaceholder()).toBe(true)
    expect(be.writes()).toEqual([])
    // 用户明确选 prev 才打开它
    expect(await app.restore.openAlternative('d_a')).toMatchObject({ kind: 'restored', id: 'd_a' })
  })

  it('对照：没有被扣下的 last 时，纯意图缺失照常静默退到可读的 durable', async () => {
    be.putSlot('d_a', savedProject('Older A'), 'p_a')
    be.setLast('p_a', 'd_a', 'Older A')
    be.setPending('p_a', 'd_c', '新打开的')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_a')
  })
})

describe('备选安装的 C1 归属纠正', () => {
  it('首选缺失后显式选择归属已核实的 prev，安装前纠正旧项目索引', async () => {
    be.setLast('p_a', 'd_missing', '首选缺失')
    be.putSlot('d_alt', savedProject('F 的旧稿'), 'p_a')
    be.session('p_a').prev = { doc_id: 'd_alt', name: 'F 的旧稿', at: 1 }
    localStorage.setItem('tavotto.docIndex', JSON.stringify([
      { id: 'd_alt', name: 'F 的旧稿', savedAt: 1, objects: 1, projectId: 'p_other', projectName: 'G' },
    ]))
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue?.alternatives[0]).toMatchObject({ id: 'd_alt', correctsIndex: true })
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]')[0].projectId).toBe('p_other')
    expect(await app.restore.openAlternative('d_alt')).toMatchObject({ kind: 'restored', id: 'd_alt' })
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]')[0].projectId).toBe('p_a')
    expect(be.state.owners.get('d_alt')).toBe('p_a')
    await reached(() => be.session('p_a').last?.doc_id === 'd_alt')
  })
})

describe('Codex P1：指针修订号与读到的正文对不上（共用 docId 的别的项目在两次读之间存过）', () => {
  /** 计划里首选带上读指针那一刻的正文修订号（FE-P 的 `expectedRevision`；这里在计划出口注入，验证 FE-R 这一半） */
  async function bootWithExpected(expected: string | null) {
    vi.resetModules()
    vi.doMock('@/lib/projectDocs', async (importOriginal) => {
      const orig = await importOriginal<typeof import('@/lib/projectDocs')>()
      return {
        ...orig,
        planRestore: async (...args: Parameters<typeof orig.planRestore>) => {
          const plan = await orig.planRestore(...args)
          if (plan.preferred) (plan.preferred as { expectedRevision?: string | null }).expectedRevision = expected
          return plan
        },
      }
    })
    return boot()
  }
  afterEach(() => vi.doUnmock('@/lib/projectDocs'))

  /** 读完 session 指针、读正文之前，p_other 把同一个 docId 存成了自己的正文 */
  async function raceThenRestore(app: App, ownerAnswer: 'other' | 'this' | 'this_unverified' | 'unknown' | 'fail') {
    const hold = gate()
    be.state.hold.autosaveGet = hold.promise
    if (ownerAnswer === 'fail') be.state.fail.owner = 500
    else if (ownerAnswer === 'this_unverified') be.state.ownerAnswers.set('p_a|d_saved', { owner: 'this', evidence: 'owners', verified: false })
    else be.state.ownerAnswers.set('p_a|d_saved', { owner: ownerAnswer, evidence: ownerAnswer === 'unknown' ? null : 'owners', verified: ownerAnswer === 'this' })
    const restoring = app.doc.restoreSession()
    await reached(() => be.state.calls.includes('GET autosave d_saved'))
    be.putSlot('d_saved', savedProject('别的项目的正文', [panel({ id: 'panel_other' })]), 'p_other')
    hold.release()
    return restoring
  }

  it.each([
    ['other', 'missing'],
    ['unknown', 'unavailable'],
    ['this_unverified', 'unavailable'],
    ['fail', 'unavailable'],
  ] as const)('归属复核 %s → needs_user{%s}，别的项目的正文不装进来', async (answer, kind) => {
    seedSavedLast()
    const app = await bootWithExpected('rev-when-pointer-read')
    expect(await raceThenRestore(app, answer)).toBe(false)
    expect(ds(app).documentId).not.toBe('d_saved')
    expect(objectIds(app)).toEqual([])
    expect(ds(app).restoreIssue).toMatchObject({ kind, ref: { id: 'd_saved' } })
    expect(be.writes()).toEqual([])
  })

  it('归属复核 {this, verified} → 照装（修订号变了只是自己又存了一次）', async () => {
    seedSavedLast()
    const app = await bootWithExpected('rev-when-pointer-read')
    expect(await raceThenRestore(app, 'this')).toBe(true)
    expect(ds(app).documentId).toBe('d_saved')
  })

  it('没有期望修订号（旧后端 / 本机线索）→ 照旧：不复核、直接装', async () => {
    seedSavedLast()
    const app = await bootWithExpected(null)
    be.state.ownerAnswers.set('p_a|d_saved', { owner: 'other', evidence: 'owners', verified: true })
    expect(await app.doc.restoreSession()).toBe(true)
    expect(be.state.calls.some((c) => c.startsWith('GET owner'))).toBe(false)
  })
})

describe('Codex P1/P2：发现的自动采用', () => {
  const rowWith = (id: string, evidence: 'owners' | 'versions' | 'assets', index = 'ok') =>
    new Response(JSON.stringify({ items: [{ ...row(id), evidence }], next_cursor: null, index }), { status: 200 })

  it('P1：索引 degraded 时发现的单独一行不自动采用 → choose（也不当空）', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = () => rowWith('d_only', 'owners', 'degraded')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_only')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'choose' })
    expect(ds(app).restoreIssue!.alternatives.map((a) => a.id)).toEqual(['d_only'])
  })

  it('P1：会话索引 degraded（session.index）同样不自动采用', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    be.session('p_a').index = 'degraded'
    candidatePages = () => rowWith('d_only', 'owners')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'index_degraded' })
  })

  it('P1：未来格式会话索引 degraded 即使包含旧 last，也不自动安装该指针', async () => {
    seedSavedLast()
    be.session('p_a').index = 'degraded'
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'index_degraded', ref: null })
    expect(ds(app).documentId).not.toBe('d_saved')
  })

  it.each(['versions', 'assets'] as const)('P1：单独一行只有 %s 弱证据 → choose，不自动采用', async (ev) => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = () => rowWith('d_only', ev)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_only')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'choose' })
  })

  it('P1 对照：owners 证据 + 索引正常 → 照旧自动采用', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = () => rowWith('d_only', 'owners')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
  })

  it('P2：degraded 且 0 行 → 保持恢复门关闭，等待重试或用户明确继续新建', async () => {
    candidatePages = () => new Response(JSON.stringify({ items: [], next_cursor: null, index: 'degraded' }), { status: 200 })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'needs_user' })
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'index_degraded', ref: null })
  })

  it.each([
    ['槽位 404（missing）', undefined, 'missing'],
    ['正文版本过新（too_new）', { ...savedProject('未来'), schema: 99 }, 'too_new'],
  ] as const)('P2：唯一候选探测 %s → needs_user，不是空', async (_n, body, kind) => {
    if (body) be.putSlot('d_only', body, 'p_a')
    candidatePages = () => rowWith('d_only', 'owners')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind, ref: { id: 'd_only' } })
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    if (kind === 'too_new') expect(ds(app).docNotice).toMatchObject({ kind: 'schema_too_new', docId: 'd_only' })
  })
})

describe('Codex P1：发现的行同样核对正文修订号', () => {
  const oneRow = (id: string, revision: string) =>
    new Response(JSON.stringify({ items: [{ ...row(id), revision }], next_cursor: null, index: 'ok' }), { status: 200 })

  it.each([
    ['other', 'missing'],
    ['unknown', 'unavailable'],
  ] as const)('扫描之后、读正文之前别的项目存了同一个 docId（复核 %s）→ needs_user{%s}，不装进来', async (answer, kind) => {
    be.putSlot('d_only', savedProject('F 的'), 'p_a')
    const hold = gate()
    candidatePages = () => oneRow('d_only', 'rev-at-scan')
    const app = await boot()
    be.state.hold.autosaveGet = hold.promise
    be.state.ownerAnswers.set('p_a|d_only', { owner: answer, evidence: answer === 'unknown' ? null : 'owners', verified: false })
    const restoring = app.doc.restoreSession()
    await reached(() => be.state.calls.includes('GET autosave d_only'))
    be.putSlot('d_only', savedProject('G 的', [panel({ id: 'panel_g' })]), 'p_other') // G 存了
    hold.release()
    expect(await restoring).toBe(false)
    expect(objectIds(app)).toEqual([])
    expect(ds(app).documentId).not.toBe('d_only')
    expect(ds(app).restoreIssue).toMatchObject({ kind, ref: { id: 'd_only' } })
    expect(be.writes()).toEqual([])
  })

  it('修订号对不上但复核仍是 {this, verified}（自己又存了一次）→ 照装；一致时不复核', async () => {
    be.putSlot('d_only', savedProject('F 的'), 'p_a')
    candidatePages = () => oneRow('d_only', 'stale')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(be.state.calls.some((c) => c.startsWith('GET owner'))).toBe(true)

    localStorage.clear()
    be.state.calls.length = 0
    candidatePages = () => oneRow('d_only', be.state.slots.get('d_only')!.revision)
    const again = await boot()
    expect(await again.doc.restoreSession()).toBe(true)
    expect(be.state.calls.some((c) => c.startsWith('GET owner'))).toBe(false)
  })

  it('choose 里选的候选（openAlternative）也带着发现时的修订号：被换了就不装', async () => {
    be.putSlot('d_one', savedProject('一'), 'p_a')
    be.putSlot('d_two', savedProject('二'), 'p_a')
    candidatePages = () =>
      new Response(JSON.stringify({ items: [row('d_one'), row('d_two')].map((r) => ({ ...r, revision: 'scan-rev' })), next_cursor: null, index: 'ok' }), { status: 200 })
    const app = await boot()
    await app.doc.restoreSession()
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'choose' })
    be.putSlot('d_one', savedProject('别人的'), 'p_other')
    be.state.ownerAnswers.set('p_a|d_one', { owner: 'other', evidence: 'owners', verified: true })
    expect(await app.restore.openAlternative('d_one')).toMatchObject({ kind: 'needs_user' })
    expect(ds(app).documentId).not.toBe('d_one')
  })
})

describe('needs_user 的备选记着提供时读到的正文修订号', () => {
  it('用户坐在面板上时同一个 docId 被别的项目覆盖——点选时复核归属，不装别人的正文', async () => {
    be.putSlot('d_a', savedProject('Older A'), 'p_a')
    be.setLast('p_a', 'd_b', 'Newest B') // B 缺失
    be.session('p_a').prev = { doc_id: 'd_a', name: 'Older A', at: 5 }
    const app = await boot()
    await app.doc.restoreSession()
    expect(ds(app).restoreIssue!.alternatives.map((a) => a.id)).toEqual(['d_a'])
    expect(ds(app).restoreIssue!.alternatives[0].expectedRevision).toBeTruthy()
    be.putSlot('d_a', savedProject('G 的', [panel({ id: 'panel_g' })]), 'p_other')
    be.state.ownerAnswers.set('p_a|d_a', { owner: 'other', evidence: 'owners', verified: true })
    expect(await app.restore.openAlternative('d_a')).toMatchObject({ kind: 'needs_user' })
    expect(ds(app).documentId).not.toBe('d_a')
    expect(objectIds(app)).toEqual([])
  })

  it('没被覆盖：点选照装，不复核', async () => {
    be.putSlot('d_a', savedProject('Older A'), 'p_a')
    be.setLast('p_a', 'd_b', 'Newest B')
    be.session('p_a').prev = { doc_id: 'd_a', name: 'Older A', at: 5 }
    const app = await boot()
    await app.doc.restoreSession()
    be.state.calls.length = 0
    expect(await app.restore.openAlternative('d_a')).toMatchObject({ kind: 'restored', id: 'd_a' })
    expect(be.state.calls.some((c) => c.startsWith('GET owner'))).toBe(false)
  })
})

describe('Codex P1：磁盘读失败不被本机副本掩盖 / 会话不可用不自动兜底', () => {
  const localCopyOf = (name: string) => {
    const pd = savedProject(name, [panel({ id: 'panel_local' })])
    pd.updatedAt = 50
    return pd
  }

  it.each([
    ['unavailable（5xx）', 500, 'unavailable'],
    ['invalid（400）', 400, 'invalid'],
  ] as const)('(a) 正文读失败 %s + 有本机副本：不当成「读到了」自动恢复；needs_user，副本只作「打开本机副本」备选', async (_n, status, kind) => {
    seedSavedLast()
    localStorage.setItem('tavotto.autosave.d_saved', JSON.stringify(localCopyOf('本机副本')))
    be.state.failGetDoc.set('d_saved', status)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_saved')
    expect(objectIds(app)).toEqual([])
    const issue = ds(app).restoreIssue!
    expect(issue).toMatchObject({ kind, ref: { id: 'd_saved' } })
    expect(issue.alternatives[0]).toMatchObject({ id: 'd_saved', localCopy: true })
    expect(be.writes()).toEqual([]) // 读失败时不把副本推回磁盘
    // 用户明确选「打开本机副本」才装它，仍不推回磁盘（不知道磁盘上有什么）
    expect(await app.restore.openAlternative('d_saved')).toMatchObject({ kind: 'restored', id: 'd_saved' })
    expect(objectIds(app)).toEqual(['panel_local'])
    expect(autosavePuts()).toEqual([])
  })

  it('(a) probeDocument：磁盘 unavailable 时 kind 照实、doc 为 null，副本单放 local；404 才把副本当文档本身', async () => {
    const app = await boot()
    localStorage.setItem('tavotto.autosave.d_x', JSON.stringify(localCopyOf('副本')))
    be.state.failGetDoc.set('d_x', 500)
    const bad = await app.doc.probeDocument('d_x', 'p_a')
    expect(bad).toMatchObject({ kind: 'unavailable', doc: null })
    expect(bad.local?.project.name).toBe('副本')
    be.state.failGetDoc.delete('d_x')
    const miss = await app.doc.probeDocument('d_x', 'p_a')
    expect(miss).toMatchObject({ kind: 'found', from: 'local' })
  })

  it('(a) 显式打开最近文档（readAutosaveDoc）保持老行为：读失败时直接用本机副本', async () => {
    const app = await boot()
    localStorage.setItem('tavotto.autosave.d_x', JSON.stringify(localCopyOf('副本')))
    be.state.failGetDoc.set('d_x', 500)
    const r = await app.doc.readAutosaveDoc('d_x')
    expect(r.doc?.project.name).toBe('副本')
  })

  function seedLocalCache() {
    be.putSlot('d_c', savedProject('本机缓存的'), 'p_a')
    localStorage.setItem('tavotto.projectDoc.p_a', JSON.stringify({ id: 'd_c', name: '本机缓存的' }))
  }

  it('(b) 会话 last 一直读不到、有本机缓存线索：不自动装它；needs_user{session_unavailable}，线索作已验证备选', async () => {
    seedLocalCache()
    be.state.fail.session = 500
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_c')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'session_unavailable', ref: null })
    expect(ds(app).restoreIssue!.alternatives.map((a) => a.id)).toEqual(['d_c'])
    expect(app.doc.isDocumentPlaceholder()).toBe(true)
    // 用户选了才打开
    expect(await app.restore.openAlternative('d_c')).toMatchObject({ kind: 'restored', id: 'd_c' })
  })

  it('(b) 会话端点 404（unsupported，旧后端）才按本机线索自动恢复', async () => {
    seedLocalCache()
    be.state.fail.session = 404
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_c')
  })
})

describe('Codex P2：restoreMetaIssue 绑定保存载荷的 pj', () => {
  it('A 项目的保存（元数据失败）在界面切到 B 并恢复出同一个 documentId 之后才完成——A 的失败不会挂到 B 头上', async () => {
    const app = await boot()
    // B 项目的 last 指着同一个 documentId（比如两个项目里都有的固定 id 的示例排版）
    be.state.metadata = 'failed'
    const hold = gate()
    putHold = hold.promise
    await ds(app).switchDocument(savedProject('A 的'), 'd_shared') // A 的保存排队（pj = p_a），卡住
    await reached(() => putsHeld >= 1)
    be.putSlot('d_shared', savedProject('B 的'), 'p_b')
    be.setLast('p_b', 'd_shared', 'B 的')
    await app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    expect(ds(app).documentId).toBe('d_shared')
    expect(app.doc.getRestoreGate()).toMatchObject({ pj: 'p_b', reason: 'restored' })
    const seen: (string | null)[] = []
    const persisted: (string | null)[] = []
    app.doc.useDocumentStore.subscribe((st) => {
      if (st.restoreMetaIssue) seen.push(st.restoreMetaIssue.pj)
    })
    app.doc.onDocumentPersisted((_id, pj) => persisted.push(pj))
    putHold = null
    hold.release()
    await reached(() => persisted.includes('p_a'))
    // A 的迟到回包到过（监听器收到，pj=p_a），但界面轴上没有 A 的失败
    expect(seen).not.toContain('p_a')
    expect(ds(app).restoreMetaIssue).toBeNull()
  })
})

describe('复核归属绑定到要装的那个修订号', () => {
  async function bootExpected() {
    vi.resetModules()
    vi.doMock('@/lib/projectDocs', async (importOriginal) => {
      const orig = await importOriginal<typeof import('@/lib/projectDocs')>()
      return {
        ...orig,
        planRestore: async (...args: Parameters<typeof orig.planRestore>) => {
          const plan = await orig.planRestore(...args)
          if (plan.preferred) plan.preferred.expectedRevision = 'rev-at-pointer'
          return plan
        },
      }
    })
    return boot()
  }
  afterEach(() => vi.doUnmock('@/lib/projectDocs'))

  it('复核说 this+verified，但它针对的是 R2、我们读到的是 R3 → 不装，needs_user{unavailable}', async () => {
    seedSavedLast()
    ownerRevisionOverride = 'R2-not-what-we-read'
    const app = await bootExpected()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_saved')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable', ref: { id: 'd_saved' } })
  })

  it('复核没带 revision（null）同样不装', async () => {
    seedSavedLast()
    ownerRevisionOverride = null
    const app = await bootExpected()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable' })
  })

  it('复核的修订号就是读到的 → 装', async () => {
    seedSavedLast()
    const app = await bootExpected()
    expect(await app.doc.restoreSession()).toBe(true)
  })
})

describe('Codex P1：期望修订号在、读到的修订号是空', () => {
  it('代理吃了 X-Tavotto-Revision → 绑定不上，不装；needs_user{unavailable}，可重试', async () => {
    seedSavedLast()
    stripRevisionHeader = true
    vi.resetModules()
    vi.doMock('@/lib/projectDocs', async (importOriginal) => {
      const orig = await importOriginal<typeof import('@/lib/projectDocs')>()
      return {
        ...orig,
        planRestore: async (...a: Parameters<typeof orig.planRestore>) => {
          const plan = await orig.planRestore(...a)
          if (plan.preferred) plan.preferred.expectedRevision = 'rev-at-pointer'
          return plan
        },
      }
    })
    try {
      const app = await boot()
      expect(await app.doc.restoreSession()).toBe(false)
      expect(ds(app).documentId).not.toBe('d_saved')
      expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable', ref: { id: 'd_saved' } })
      // 头回来了、修订号对上了 → 重试可装
      stripRevisionHeader = false
      // 修订号读得到了，与期望不同 → 复核归属（/owner 针对的就是读到的这份）→ this+verified 才装
      expect(await app.restore.retryRestore()).toMatchObject({ kind: 'restored', id: 'd_saved' })
    } finally {
      vi.doUnmock('@/lib/projectDocs')
    }
  })

  it('没有期望修订号（旧后端）：读到空修订号也照旧装', async () => {
    seedSavedLast()
    stripRevisionHeader = true
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
  })
})

describe('发现被后端截断（truncated）', () => {
  const truncatedPage = (items: string[], next: string | null, truncated: boolean) =>
    new Response(JSON.stringify({ items: items.map(row), next_cursor: next, index: 'ok', truncated }), { status: 200 })

  it('唯一一行可见 + truncated → 不自动采用，给 choose', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = () => truncatedPage(['d_only'], null, true)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).documentId).not.toBe('d_only')
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'choose' })
    expect(ds(app).restoreIssue!.alternatives.map((a) => a.id)).toEqual(['d_only'])
  })

  it('truncated 是粘性的：第一页截断、后面的页再干净也不翻案', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = (cursor) => (cursor === null ? truncatedPage([], 'c1', true) : truncatedPage(['d_only'], null, false))
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'choose' })
  })

  it('0 行 + truncated → 重试出口（index_degraded），不是空', async () => {
    candidatePages = () => truncatedPage([], null, true)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'index_degraded', ref: null })
  })

  it('对照：没截断的唯一一行照旧自动采用', async () => {
    be.putSlot('d_only', savedProject('唯一'), 'p_a')
    candidatePages = () => truncatedPage(['d_only'], null, false)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
  })
})

describe('Codex P1：指针是在槽位还没有正文时验过的（expectedAbsent）', () => {
  async function bootAbsent() {
    vi.resetModules()
    vi.doMock('@/lib/projectDocs', async (importOriginal) => {
      const orig = await importOriginal<typeof import('@/lib/projectDocs')>()
      return {
        ...orig,
        planRestore: async (...a: Parameters<typeof orig.planRestore>) => {
          const plan = await orig.planRestore(...a)
          if (plan.preferred) (plan.preferred as { expectedAbsent?: true }).expectedAbsent = true
          return plan
        },
      }
    })
    return boot()
  }
  afterEach(() => vi.doUnmock('@/lib/projectDocs'))

  it.each([
    ['other', 'missing'],
    ['unknown', 'unavailable'],
  ] as const)('指针 pending、读正文时冒出了别的项目存的正文（复核 %s）→ needs_user{%s}，不装', async (answer, kind) => {
    be.putSlot('d_c', savedProject('G 的', [panel({ id: 'panel_g' })]), 'p_other')
    be.setLast('p_a', 'd_c', '指针说的')
    be.state.ownerAnswers.set('p_a|d_c', { owner: answer, evidence: answer === 'unknown' ? null : 'owners', verified: false })
    const app = await bootAbsent()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(objectIds(app)).toEqual([])
    expect(ds(app).restoreIssue).toMatchObject({ kind, ref: { id: 'd_c' } })
  })

  it('冒出的正文经复核就是自己的（this+verified，修订号对得上）→ 装', async () => {
    be.putSlot('d_c', savedProject('自己的'), 'p_a')
    be.setLast('p_a', 'd_c', '自己的')
    const app = await bootAbsent()
    expect(await app.doc.restoreSession()).toBe(true)
  })

  it('只有本机副本（磁盘 404）的路径不受影响（A32）', async () => {
    be.setLast('p_a', 'd_c', '本机的')
    localStorage.setItem('tavotto.autosave.d_c', JSON.stringify(savedProject('本机的', [panel({ id: 'panel_l' })])))
    const app = await bootAbsent()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(objectIds(app)).toEqual(['panel_l'])
  })

  it('缺席证据的备选在展示前冒出别人的正文，点选仍要复核归属', async () => {
    be.setLast('p_a', 'd_missing', '首选缺失')
    be.session('p_a').prev = { doc_id: 'd_c', name: '曾经缺席', at: 1, revision: null }
    vi.resetModules()
    vi.doMock('@/lib/projectDocs', async (importOriginal) => {
      const orig = await importOriginal<typeof import('@/lib/projectDocs')>()
      return {
        ...orig,
        planRestore: async (...a: Parameters<typeof orig.planRestore>) => {
          const plan = await orig.planRestore(...a)
          be.putSlot('d_c', savedProject('G 的', [panel({ id: 'panel_g' })]), 'p_other')
          return plan
        },
      }
    })
    try {
      const app = await boot()
      expect(await app.doc.restoreSession()).toBe(false)
      expect(ds(app).restoreIssue?.alternatives[0]).toMatchObject({ id: 'd_c', expectedAbsent: true })
      expect(await app.restore.openAlternative('d_c')).toMatchObject({ kind: 'needs_user' })
      expect(objectIds(app)).toEqual([])
      expect(be.state.calls).toContain('GET owner d_c')
    } finally {
      vi.doUnmock('@/lib/projectDocs')
    }
  })

  it('本机索引误记为 G，后端证明缺席槽位归 F：本机副本首次写盘归 F', async () => {
    localStorage.setItem('tavotto.docIndex', JSON.stringify([{ id: 'd_c', projectId: 'p_other', projectName: 'G' }]))
    localStorage.setItem('tavotto.autosave.d_c', JSON.stringify(savedProject('F 的本机副本')))
    be.state.owners.set('d_c', 'p_a')
    be.setLast('p_a', 'd_c', 'F 的', null)
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    await reached(() => autosavePuts().includes('PUT autosave d_c'))
    expect(be.state.owners.get('d_c')).toBe('p_a')
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]')[0].projectId).toBe('p_a')
  })
})

describe('Codex P2：owner_unknown 与 failed 一样点亮「恢复信息没更新」', () => {
  it('metadata=owner_unknown → restoreMetaIssue 亮起；下次 ok 清掉', async () => {
    const app = await boot()
    be.state.metadata = 'owner_unknown'
    await ds(app).switchDocument(savedProject('M'), 'd_m')
    await app.doc.saveNow()
    expect(ds(app).restoreMetaIssue).toEqual({ docId: 'd_m', pj: 'p_a' })
    expect(ds(app).saveState).toBe('saved')
    be.state.metadata = 'ok'
    await app.doc.saveNow()
    expect(ds(app).restoreMetaIssue).toBeNull()
  })
})
