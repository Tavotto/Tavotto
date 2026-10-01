/**
 * 一个项目的排版不许漏进另一个项目（#715 Windows 真机验收 P1，2026-10-01）。
 *
 * 稳定端口（#718）之后，同一台机器上先后打开的项目共用一个 origin、一份 localStorage。验收现场：
 * 项目 F 里排了一张图 → 关掉 → 打开新项目 G，G 一打开就是 F 的排版（图内编辑那张图），随后
 * `tavotto.projectDoc.<G>` 与后端 G.last 都记成 F 的 documentId，G 的目录里打出了同一个 doc_id 的
 * 时间线节点。根因：启动恢复（`restoreSession`）在「G 后端没记过」（`null`）时退回全局的
 * `tavotto.currentDoc`——那是 F 最后开着的那份——不问它属于哪个项目。
 *
 * 这里与 `layoutSession.restart.test.ts` 同一套做法（有状态的假后端跨「启动」存活、`vi.resetModules()`），
 * 但**不清 localStorage**：同一个 origin 才是这条缺陷的前提。每条用例在修复前的代码上都红。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

/* ------------------------- 有状态的假后端（跨启动存活） ------------------------- */

const slots = new Map<string, string>()
/** pj → 上次开着的排版 */
const lastByProject = new Map<string, { doc_id: string; name: string; at: number }>()
/** 槽位归属（`PUT /api/autosave` 按 pj 记，与 `engine/layoutsession.record_owner` 同形） */
const owners = new Map<string, string>()
/** 每一次自动保存 PUT：[doc_id, pj] */
const autosavePuts: [string, string][] = []
/** `${pj}:${doc_id}`：后端在这个项目里找得到这份排版的证据（时间线节点 / 素材全在） */
const evidenceHere = new Set<string>()
/** true = 后端做归属检查（`layout_foreign`，本 PR 的后端那一道）；false = 修复前的后端 */
let ownerChecks = false
let serverClock = 1
let inFlight = 0

function pjOf(u: URL, init?: RequestInit): string {
  const h = (init?.headers ?? {}) as Record<string, string>
  return h['X-Tavotto-Project'] ?? u.searchParams.get('pj') ?? ''
}

const foreign = (docId: string, pj: string) => owners.has(docId) && owners.get(docId) !== pj

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  inFlight += 1
  setTimeout(() => (inFlight -= 1), 0)
  const u = new URL(String(url), 'http://127.0.0.1')
  const method = init?.method ?? 'GET'
  const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status })
  const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
  if (auto) {
    const id = decodeURIComponent(auto[1])
    if (method === 'PUT') {
      slots.set(id, String(init?.body))
      owners.set(id, pjOf(u, init))
      autosavePuts.push([id, pjOf(u, init)])
      return json({ ok: true, saved_at: 1, revision: `r${slots.get(id)!.length}` })
    }
    const v = slots.get(id)
    return v
      ? new Response(v, { status: 200, headers: { 'X-Tavotto-Revision': `r${v.length}` } })
      : json({}, 404)
  }
  if (u.pathname === '/api/layout-session' && method === 'GET') {
    const pj = pjOf(u, init)
    const last = lastByProject.get(pj) ?? null
    return json({ last: last && ownerChecks && foreign(last.doc_id, pj) ? null : last })
  }
  if (u.pathname === '/api/layout-session/last' && method === 'PUT') {
    const body = JSON.parse(String(init?.body)) as { doc_id: string; name: string }
    const pj = pjOf(u, init)
    if (ownerChecks && foreign(body.doc_id, pj)) return json({ error: 'foreign', code: 'layout_foreign' }, 409)
    serverClock += 1
    const last = { doc_id: body.doc_id, name: body.name, at: serverClock }
    lastByProject.set(pj, last)
    return json({ ok: true, last })
  }
  if (u.pathname === '/api/layout-session/owner' && method === 'GET') {
    // 与 `app._layout_owner_evidence` 同形：owners 是定论；没有记录时看「当前项目里的证据」
    const id = u.searchParams.get('doc_id') ?? ''
    const pj = pjOf(u, init)
    if (owners.has(id)) return json({ owner: owners.get(id) === pj ? 'this' : 'other', evidence: 'owners' })
    return json(evidenceHere.has(`${pj}:${id}`) ? { owner: 'this', evidence: 'assets' } : { owner: 'unknown', evidence: null })
  }
  if (u.pathname.startsWith('/api/preferences/')) return json({ defaults: null })
  if (u.pathname.startsWith('/api/projects')) return json({ recent: [], pinned: [], projects: [] })
  return json({}, 404)
}) as typeof fetch

async function settle() {
  for (let quiet = 0; quiet < 3; ) {
    await new Promise((r) => setTimeout(r, 5))
    quiet = inFlight === 0 ? quiet + 1 : 0
  }
}

const statusOf = (pj: string) => ({ open: true, id: pj, figures_dir: `/figs/${pj}` })

/** 一次「启动」，开着项目 `pj`：全新的模块实例，**localStorage 原样留着**（稳定 origin） */
async function boot(pj: string) {
  vi.resetModules()
  sessionStorage.clear()
  const session = await import('@/lib/session')
  session.setCurrentProjectId(pj)
  const doc = await import('./documentStore')
  const proj = await import('./projectStore')
  const actions = await import('./actions')
  proj.useProjectStore.setState({ phase: 'open', project: statusOf(pj), lastDocumentIssue: null })
  return { doc, proj, session, actions }
}
type App = Awaited<ReturnType<typeof boot>>

/** 在当前项目里换上一份排版并放一段字（= 用户在这个项目里排了版），等落盘与「上次开着的」都推完 */
async function makeContentDoc(app: App, id: string, name: string, label: string) {
  const pd: ProjectDocument = { ...emptyProject(), project: { id: 'x', name } }
  await app.doc.useDocumentStore.getState().switchDocument(pd, id)
  editCurrent(app, label)
  app.doc.flushAutosave()
  await settle()
}

function editCurrent(app: App, label: string) {
  app.doc.useDocumentStore.getState().commit(literal('加一段字'), (d) => {
    d.objects.push(text(`t_${label}`, label))
  })
}

const texts = (app: App) =>
  app.doc.useDocumentStore.getState().doc.objects.map((o) => (o as TextObject).text)
const cached = (pj: string) => localStorage.getItem(`tavotto.projectDoc.${pj}`)

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  slots.clear()
  lastByProject.clear()
  owners.clear()
  evidenceHere.clear()
  autosavePuts.length = 0
  ownerChecks = false
  serverClock = 1
})

afterEach(async () => {
  await settle()
})

describe('同一个 origin 先后打开两个项目', () => {
  it('A → B（新项目）：启动恢复不把 A 的排版装进 B，也不记到 B 名下', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    // 前提：全局 currentDoc 指着 F 的排版（验收现场的 `tavotto.currentDoc = "d_gtwuxyup"`）
    expect(localStorage.getItem('tavotto.currentDoc')).toBe('d_f')

    const g = await boot('p_g')
    expect(await g.doc.restoreSession()).toBe(false)
    const st = g.doc.useDocumentStore.getState()
    expect(st.documentId).not.toBe('d_f')
    expect(st.doc.objects).toEqual([])
    await settle()
    // 没绑：G 的「上次开着的」后端与本机缓存都没有 F 的排版；F 的槽位没以 G 的名义写过
    expect(lastByProject.get('p_g')).toBeUndefined()
    expect(cached('p_g')).toBeNull()
    expect(autosavePuts.filter(([, pj]) => pj === 'p_g')).toEqual([])
    // F 的记录原样
    expect(lastByProject.get('p_f')?.doc_id).toBe('d_f')
  })

  it('A → B（新项目，切项目入口）：修复前留下的被污染缓存 projectDoc.<B> → A 的排版，不认并作废', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    // 验收现场的本机状态：`tavotto.projectDoc.<G>` 指着 F 的排版，后端 G 没记过（同一个 origin 升级上来）
    localStorage.setItem('tavotto.projectDoc.p_g', JSON.stringify({ id: 'd_f', name: 'F 的排版' }))

    const g = await boot('p_g')
    await g.proj.useProjectStore.getState().adoptOpenedProject(statusOf('p_g'))
    expect(g.doc.useDocumentStore.getState().documentId).not.toBe('d_f')
    expect(g.doc.useDocumentStore.getState().doc.objects).toEqual([])
    expect(g.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
    await settle()
    expect(cached('p_g')).toBeNull()
    expect(lastByProject.get('p_g')).toBeUndefined()
  })

  it('A → B（B 有自己的排版，只记在本机缓存里）：回到 B 自己的，不是 A 的', async () => {
    // B 的排版在修复 #719 之前就有了：后端没记过、本机缓存记着（同一个 origin 升级上来）
    const g0 = await boot('p_g')
    await makeContentDoc(g0, 'd_g', 'G 的排版', 'G')
    lastByProject.delete('p_g')
    localStorage.setItem('tavotto.projectDoc.p_g', JSON.stringify({ id: 'd_g', name: 'G 的排版' }))
    // 然后在 F 里排了版，关掉（全局 currentDoc → F 的）
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    expect(localStorage.getItem('tavotto.currentDoc')).toBe('d_f')

    const g = await boot('p_g')
    expect(await g.doc.restoreSession()).toBe(true)
    expect(g.doc.useDocumentStore.getState().documentId).toBe('d_g')
    expect(texts(g)).toEqual(['G'])
    await settle()
    // 迁移推给了后端：B 的「上次开着的」是 B 自己的
    expect(lastByProject.get('p_g')?.doc_id).toBe('d_g')
  })

  it('A → B → A：B 里的编辑落在 B 自己的排版上，回到 A 时 A 的排版原封不动', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')

    const g = await boot('p_g')
    await g.doc.restoreSession()
    const gDoc = g.doc.useDocumentStore.getState().documentId
    // 在 B 里接着排（修复前这一笔落进的是 F 的排版）
    editCurrent(g, 'G')
    g.doc.flushAutosave()
    await settle()
    expect(gDoc).not.toBe('d_f')
    expect(lastByProject.get('p_g')?.doc_id).toBe(gDoc)

    const f2 = await boot('p_f')
    expect(await f2.doc.restoreSession()).toBe(true)
    expect(f2.doc.useDocumentStore.getState().documentId).toBe('d_f')
    expect(texts(f2)).toEqual(['F'])
    expect(JSON.parse(slots.get('d_f')!).canvases[0].objects.map((o: TextObject) => o.text)).toEqual(['F'])
  })
})

/** 升级前留下的状态：磁盘上一份槽位（没有归属记录）、本机 currentDoc 指着它、最近文档索引里没有归属 */
function legacyCurrentDoc(id: string, label: string, entry: 'none' | 'unlabelled') {
  const pd = { ...emptyProject(), project: { id: 'x', name: '升级前的排版' } }
  pd.canvases[0].objects.push(text(`t_${label}`, label))
  slots.set(id, JSON.stringify(pd))
  localStorage.setItem('tavotto.currentDoc', id)
  if (entry === 'unlabelled') {
    localStorage.setItem('tavotto.docIndex', JSON.stringify([{ id, name: '升级前的排版', savedAt: 1, objects: 1 }]))
  }
}
const indexOwner = (id: string) =>
  (JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]') as { id: string; projectId?: string }[]).find(
    (e) => e.id === id,
  )?.projectId

describe('升级上来、本机不知道归属的排版（#773）：问后端要证据', () => {
  it('当前项目里有证据（素材全在 / 时间线节点）：恢复，并当场把归属补进本机索引', async () => {
    legacyCurrentDoc('d_old', '旧', 'unlabelled')
    evidenceHere.add('p_g:d_old')
    const g = await boot('p_g')
    expect(await g.doc.restoreSession()).toBe(true)
    expect(g.doc.useDocumentStore.getState().documentId).toBe('d_old')
    expect(texts(g)).toEqual(['旧'])
    expect(indexOwner('d_old')).toBe('p_g')
  })

  it('索引里根本没有这条、后端的槽位归属记着当前项目：恢复并补一条带归属的索引', async () => {
    legacyCurrentDoc('d_old', '旧', 'none')
    owners.set('d_old', 'p_g')
    const g = await boot('p_g')
    expect(await g.doc.restoreSession()).toBe(true)
    expect(g.doc.useDocumentStore.getState().documentId).toBe('d_old')
    expect(indexOwner('d_old')).toBe('p_g')
  })

  it('只有别的项目有记录（projF → projG 的形状，本机索引已不知道归属）：不恢复', async () => {
    legacyCurrentDoc('d_f', 'F', 'unlabelled')
    owners.set('d_f', 'p_f')
    const g = await boot('p_g')
    expect(await g.doc.restoreSession()).toBe(false)
    expect(g.doc.useDocumentStore.getState().documentId).not.toBe('d_f')
    expect(g.doc.useDocumentStore.getState().doc.objects).toEqual([])
    expect(indexOwner('d_f')).toBeUndefined()
  })

  it('哪儿都没有证据：不恢复', async () => {
    legacyCurrentDoc('d_old', '旧', 'unlabelled')
    const g = await boot('p_g')
    expect(await g.doc.restoreSession()).toBe(false)
    expect(g.doc.useDocumentStore.getState().doc.objects).toEqual([])
  })
})

describe('纵深：两侧任何一道拦下都算数', () => {
  it('本机索引不知道归属、后端知道：迁移被后端拒收（layout_foreign）就不恢复，缓存作废、不重推', async () => {
    ownerChecks = true
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    // 本机「最近文档」索引里已没有这份（被 12 条上限挤掉 / 别的标签页清过）：前端那一道判不出
    localStorage.removeItem('tavotto.docIndex')
    localStorage.setItem('tavotto.projectDoc.p_g', JSON.stringify({ id: 'd_f', name: 'F 的排版' }))

    const g = await boot('p_g')
    await g.proj.useProjectStore.getState().adoptOpenedProject(statusOf('p_g'))
    expect(g.doc.useDocumentStore.getState().documentId).not.toBe('d_f')
    expect(g.doc.useDocumentStore.getState().doc.objects).toEqual([])
    await settle()
    expect(cached('p_g')).toBeNull()
    expect(lastByProject.get('p_g')).toBeUndefined()
  })

  it('后端里修复前记下的 B.last = A 的排版（后端还不知道归属）：前端索引认得出，不恢复也不写进缓存', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    // 验收现场的后端状态：G 的 last 指着 F 的排版；这里的后端是修复前的（不做归属检查）
    lastByProject.set('p_g', { doc_id: 'd_f', name: 'F 的排版', at: 99 })

    const g = await boot('p_g')
    expect(await g.doc.restoreSession()).toBe(false)
    expect(g.doc.useDocumentStore.getState().documentId).not.toBe('d_f')
    await g.proj.useProjectStore.getState().adoptOpenedProject(statusOf('p_g'))
    expect(g.doc.useDocumentStore.getState().documentId).not.toBe('d_f')
    expect(g.doc.useDocumentStore.getState().doc.objects).toEqual([])
    expect(cached('p_g')).toBeNull()
  })

  it('应用刚起、没换过文档就直接切项目：离开那一下的冲刷仍记在原项目名下', async () => {
    const f = await boot('p_f')
    const first = f.doc.useDocumentStore.getState().documentId
    editCurrent(f, 'F')
    f.doc.flushAutosave()
    await settle()
    expect(owners.get(first)).toBe('p_f')
    editCurrent(f, 'F2') // 离开之前还有一笔没落盘：换代时那次冲刷写它
    await f.proj.useProjectStore.getState().adoptOpenedProject(statusOf('p_g'))
    await settle()
    expect(autosavePuts.filter(([id, pj]) => id === first && pj === 'p_g')).toEqual([])
    expect(owners.get(first)).toBe('p_f')
  })

  it('在 B 里从「最近文档」看一眼 A 的排版、没改就回到 A（后端做归属检查）：A 照常恢复它', async () => {
    ownerChecks = true
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    const g = await boot('p_g')
    await g.doc.restoreSession()
    await g.actions.openRecentDocument('d_f')
    await settle()
    const f2 = await boot('p_f')
    expect(await f2.doc.restoreSession()).toBe(true)
    expect(f2.doc.useDocumentStore.getState().documentId).toBe('d_f')
  })

  it('看一眼时磁盘槽位不在、本机副本推回磁盘：仍记在原项目名下', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    // 磁盘那一份没了，本机兜底副本还在（readAutosaveDoc 会把它推回磁盘）
    slots.delete('d_f')
    owners.delete('d_f')
    localStorage.setItem('tavotto.autosave.d_f', JSON.stringify({ ...emptyProject(), project: { id: 'x', name: 'F 的排版' } }))
    const g = await boot('p_g')
    await g.doc.restoreSession()
    await g.actions.openRecentDocument('d_f')
    await settle()
    expect(slots.has('d_f')).toBe(true)
    expect(autosavePuts.filter(([id, pj]) => id === 'd_f' && pj === 'p_g')).toEqual([])
    expect(owners.get('d_f')).toBe('p_f')
  })

  it('看一眼之后按 ⌘S：手动保存是用户的动作，归到这个项目', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    const g = await boot('p_g')
    await g.doc.restoreSession()
    await g.actions.openRecentDocument('d_f')
    await settle()
    expect(owners.get('d_f')).toBe('p_f')
    await g.doc.saveNow()
    await settle()
    expect(owners.get('d_f')).toBe('p_g')
    expect(lastByProject.get('p_g')?.doc_id).toBe('d_f')
  })

  it('看一眼之后只改了名：改名是用户的编辑，归到这个项目并记成它的「上次开着的」', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    const g = await boot('p_g')
    await g.doc.restoreSession()
    await g.actions.openRecentDocument('d_f')
    await settle()
    g.doc.useDocumentStore.getState().renameProject('在 G 里改的名')
    await settle()
    expect(owners.get('d_f')).toBe('p_g')
    expect(lastByProject.get('p_g')).toMatchObject({ doc_id: 'd_f', name: '在 G 里改的名' })
  })

  it('应用里直接切项目 A → B → A（后端做归属检查）：离开 A 时那次冲刷记在 A 名下，回到 A 照常恢复', async () => {
    ownerChecks = true
    const a = await boot('p_f')
    await a.proj.useProjectStore.getState().adoptOpenedProject(statusOf('p_f'))
    await makeContentDoc(a, 'd_f', 'F 的排版', 'F')
    // 切项目先认领新项目、再冲刷旧文档：那一次 PUT 必须记在旧项目名下，否则后端把 F 的排版改记成 G 的
    await a.proj.useProjectStore.getState().adoptOpenedProject(statusOf('p_g'))
    await settle()
    expect(owners.get('d_f')).toBe('p_f')
    expect(autosavePuts.filter(([id, pj]) => id === 'd_f' && pj === 'p_g')).toEqual([])
    await a.proj.useProjectStore.getState().adoptOpenedProject(statusOf('p_f'))
    expect(a.doc.useDocumentStore.getState().documentId).toBe('d_f')
    expect(texts(a)).toEqual(['F'])
  })

  it('显式从「最近文档」打开别的项目的排版：打开那一下不记成这个项目「上次开着的」', async () => {
    const f = await boot('p_f')
    await makeContentDoc(f, 'd_f', 'F 的排版', 'F')
    const g = await boot('p_g')
    await g.doc.restoreSession()
    await g.actions.openRecentDocument('d_f')
    expect(g.doc.useDocumentStore.getState().documentId).toBe('d_f')
    await settle()
    expect(lastByProject.get('p_g')).toBeUndefined()
    expect(cached('p_g')).toBeNull()
    // 只是看一眼：归属不动（#773 Codex P2）——打开那一下的冲刷仍记在 F 名下，本机索引仍标 F
    expect(owners.get('d_f')).toBe('p_f')
    expect(autosavePuts.filter(([id, pj]) => id === 'd_f' && pj === 'p_g')).toEqual([])
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex')!).find((e: { id: string }) => e.id === 'd_f').projectId).toBe('p_f')
    // 在 G 里改过并落了盘 = 用户把它带进了 G（本机索引与后端归属都改记成 G）：之后的变化照常记。
    // 「改过」由自动保存的订阅认（工作台挂着时才有），这里挂上它
    // 只改**一笔**就离开：后面不会再有变化去触发记录（#773 Codex 复核 P2）
    const stop = g.doc.startAutosave()
    editCurrent(g, 'G')
    g.doc.flushAutosave()
    await settle()
    stop()
    expect(owners.get('d_f')).toBe('p_g')
    expect(lastByProject.get('p_g')?.doc_id).toBe('d_f')
  })
})
