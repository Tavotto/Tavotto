/**
 * 会话状态以后端为准（#715 PR-B）：**换了 origin 之后**还能回到上次的排版。
 *
 * 桌面版每次启动 sidecar 可能换端口，localStorage 按 origin 隔离——换了端口就是一份全新的
 * 空存储。这里用「有状态的假后端」跨两次启动存活，中间 `localStorage.clear()` +
 * `vi.resetModules()`（模块级缓存一并作废）模拟换 origin 的重启：
 *
 *   1. 启动恢复（`restoreSession`）回到上次那份排版；
 *   2. 切进项目（`adoptOpenedProject`）回到这个项目上次那份排版；
 *   3. 导出默认值（1200 ppi）不因换 origin 退回 600；
 *   4. 后端没有这组端点（404：playground、嵌入画布、旧后端）时退回旧逻辑（localStorage）；
 *   5. 前端不再按本机 12 条 `docIndex` 发 DELETE 删磁盘槽位（换了 origin 的索引是空的）；
 *   6. 推给后端失败（非 404）的写入不丢：同一个 origin 下次启动时本机那条更新就以它为准并重推。
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
/** pj → 上次开着的排版；'' = 没开项目 */
const lastByProject = new Map<string, { doc_id: string; name: string; at: number }>()
let exportDefaults: unknown = null
/** false = 这个后端没有 #715 的端点（旧后端 / playground），一律 404 */
let sessionEndpoints = true
/** true = 后端在，但写入回 500（sidecar 正在退出、瞬断） */
let failSessionPuts = false
/** 假后端的钟（毫秒）；个别用例把它拨到远超浏览器时钟，模拟连着远程实例、服务器钟快 */
let serverClock = 1
/** 按顺序给「上次开着的」PUT 排好结局（先挂在 hold 上，再按 'ok' / 'fail' 答）；空了按常规 */
const putPlan: { hold: Promise<void>; outcome: 'ok' | 'fail' }[] = []
/** 非空 = 「上次开着哪份」的 GET 挂在这里，等它 resolve 才答（切项目途中的那段 await） */
let holdSessionGet: Promise<void> | null = null
/** 同上，导出默认值的 GET */
let holdPrefsGet: Promise<void> | null = null
const deletes: string[] = []
let inFlight = 0

function pjOf(u: URL, init?: RequestInit): string {
  const h = (init?.headers ?? {}) as Record<string, string>
  return h['X-Tavotto-Project'] ?? u.searchParams.get('pj') ?? ''
}

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
      return json({ ok: true, saved_at: 1, revision: `r${slots.get(id)!.length}` })
    }
    if (method === 'DELETE') {
      deletes.push(id)
      slots.delete(id)
      return json({ ok: true })
    }
    const v = slots.get(id)
    return v
      ? new Response(v, { status: 200, headers: { 'X-Tavotto-Revision': `r${v.length}` } })
      : json({}, 404)
  }
  if (u.pathname.startsWith('/api/layout-session') || u.pathname.startsWith('/api/preferences/')) {
    if (!sessionEndpoints) return json({ error: 'not found' }, 404)
    if (failSessionPuts && method === 'PUT') return json({ error: 'boom' }, 500)
    if (u.pathname === '/api/layout-session' && method === 'GET') {
      // 先取值再挂起：挂起期间别处的写入不影响这份回包（模拟「在路上的旧回包」）
      const snap = lastByProject.get(pjOf(u, init)) ?? null
      if (holdSessionGet) await holdSessionGet
      return json({ last: snap })
    }
    if (u.pathname === '/api/layout-session/last' && method === 'PUT') {
      const plan = putPlan.shift()
      if (plan) {
        await plan.hold
        if (plan.outcome === 'fail') return json({ error: 'boom' }, 500)
      }
      const body = JSON.parse(String(init?.body)) as { doc_id: string; name: string }
      serverClock += 1 // 后端自己的钟：与浏览器那台机器的 Date.now() 无关
      const last = { doc_id: body.doc_id, name: body.name, at: serverClock }
      lastByProject.set(pjOf(u, init), last)
      return json({ ok: true, last })
    }
    if (u.pathname === '/api/preferences/export-defaults') {
      if (method === 'PUT') {
        exportDefaults = JSON.parse(String(init?.body))
        return json({ ok: true, defaults: exportDefaults })
      }
      const snap = exportDefaults
      if (holdPrefsGet) await holdPrefsGet
      return json({ defaults: snap })
    }
    return json({}, 404)
  }
  if (u.pathname.startsWith('/api/projects')) return json({ recent: [], pinned: [], projects: [] })
  return json({}, 404)
}) as typeof fetch

async function settle() {
  for (let quiet = 0; quiet < 3; ) {
    await new Promise((r) => setTimeout(r, 5))
    quiet = inFlight === 0 ? quiet + 1 : 0
  }
}

/** 一次「启动」：全新的模块实例（模块级缓存作废），本标签页认领 p_a */
async function boot() {
  vi.resetModules()
  const session = await import('@/lib/session')
  session.setCurrentProjectId('p_a')
  const doc = await import('./documentStore')
  const proj = await import('./projectStore')
  proj.useProjectStore.setState({
    phase: 'open',
    project: { open: true, id: 'p_a', figures_dir: '/figs/a' },
    lastDocumentIssue: null,
  })
  return { doc, proj, session }
}

/** 换 origin：这个 origin 的 localStorage / sessionStorage 全是空的 */
function newOrigin() {
  localStorage.clear()
  sessionStorage.clear()
}

async function makeContentDoc(app: Awaited<ReturnType<typeof boot>>, id: string, name: string) {
  const pd: ProjectDocument = { ...emptyProject(), project: { id: 'x', name } }
  await app.doc.useDocumentStore.getState().switchDocument(pd, id)
  app.doc.useDocumentStore.getState().commit(literal('加一段字'), (d) => {
    d.objects.push(text('t1', 'hello'))
  })
  app.doc.flushAutosave()
  await settle()
}

beforeEach(() => {
  newOrigin()
  slots.clear()
  lastByProject.clear()
  exportDefaults = null
  sessionEndpoints = true
  failSessionPuts = false
  serverClock = 1
  holdSessionGet = null
  holdPrefsGet = null
  putPlan.length = 0
  deletes.length = 0
})

afterEach(async () => {
  await settle()
})

describe('换了 origin 的重启', () => {
  it('启动恢复回到上次那份排版（后端记着，本机存储是空的）', async () => {
    const first = await boot()
    await makeContentDoc(first, 'd_keep', 'Fig keep')
    expect(first.doc.useDocumentStore.getState().documentId).toBe('d_keep')

    newOrigin()
    const second = await boot()
    expect(second.doc.useDocumentStore.getState().documentId).not.toBe('d_keep')
    expect(await second.doc.restoreSession()).toBe(true)
    const st = second.doc.useDocumentStore.getState()
    expect(st.documentId).toBe('d_keep')
    expect(st.doc.objects.map((o) => o.id)).toEqual(['t1'])
  })

  it('切进项目回到这个项目上次那份排版', async () => {
    const first = await boot()
    await makeContentDoc(first, 'd_proj', 'Fig proj')

    newOrigin()
    const second = await boot()
    await second.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(second.doc.useDocumentStore.getState().documentId).toBe('d_proj')
    expect(second.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
  })

  it('记录在、槽位没了：横幅照旧指名那份排版（lastDocumentIssue 机制不变）', async () => {
    const first = await boot()
    await makeContentDoc(first, 'd_gone', 'Fig gone')
    slots.delete('d_gone')

    newOrigin()
    const second = await boot()
    await second.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(second.proj.useProjectStore.getState().lastDocumentIssue).toEqual({
      id: 'd_gone',
      name: 'Fig gone',
    })
  })

  it('导出默认值不因换 origin 退回默认', async () => {
    vi.resetModules()
    const before = await import('@/lib/exportDefaults')
    before.writeExportDefaults({ dpi: '1200' })
    await settle()

    newOrigin()
    vi.resetModules()
    const after = (await import('@/lib/exportDefaults')) as typeof before & {
      hydrateExportDefaults?: () => Promise<unknown>
    }
    await after.hydrateExportDefaults?.()
    expect(after.readExportDefaults().dpi).toBe('1200')
  })
})

describe('后端没有这组端点（404）：退回旧逻辑', () => {
  beforeEach(() => {
    sessionEndpoints = false
  })

  it('同一个 origin 刷新：启动恢复照旧读本机 currentDoc', async () => {
    const first = await boot()
    await makeContentDoc(first, 'd_local', 'Fig local')
    const second = await boot()
    expect(await second.doc.restoreSession()).toBe(true)
    expect(second.doc.useDocumentStore.getState().documentId).toBe('d_local')
  })

  it('切进项目照旧按本机记录换回去', async () => {
    const first = await boot()
    await makeContentDoc(first, 'd_local', 'Fig local')
    const second = await boot()
    await second.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(second.doc.useDocumentStore.getState().documentId).toBe('d_local')
  })

  it('导出默认值照旧存在本机', async () => {
    vi.resetModules()
    const mod = (await import('@/lib/exportDefaults')) as typeof import('@/lib/exportDefaults') & {
      hydrateExportDefaults?: () => Promise<unknown>
    }
    mod.writeExportDefaults({ dpi: '900' })
    await mod.hydrateExportDefaults?.()
    expect(mod.readExportDefaults().dpi).toBe('900')
  })
})

describe('推给后端失败的写入不丢（#719 Codex P1 / P2）', () => {
  it('「上次开着的」推失败：同一个 origin 下次启动回到新的那份，并补推给后端', async () => {
    const first = await boot()
    await makeContentDoc(first, 'd_old', 'Fig old')
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_old')
    failSessionPuts = true
    await makeContentDoc(first, 'd_new', 'Fig new')
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_old') // 前提：后端那份还是旧的
    failSessionPuts = false

    const second = await boot() // 同一个 origin：本机缓存还在
    await second.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(second.doc.useDocumentStore.getState().documentId).toBe('d_new')
    await settle()
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_new')
  })

  it('推失败之后后端又记了更新的（别处写的）：后端为准', async () => {
    const first = await boot()
    await makeContentDoc(first, 'd_old', 'Fig old')
    failSessionPuts = true
    await makeContentDoc(first, 'd_mine', 'Fig mine')
    failSessionPuts = false
    lastByProject.set('p_a', { doc_id: 'd_old', name: 'Fig old', at: Date.now() + 60_000 })

    const second = await boot()
    await second.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_a', figures_dir: '/figs/a' })
    expect(second.doc.useDocumentStore.getState().documentId).toBe('d_old')
  })

  it('同一毫秒记两次：先到的确认不会把后一次当成自己摘掉（后一次推失败仍留待确认）', async () => {
    vi.resetModules()
    const docs = await import('@/lib/projectDocs')
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
    let release!: () => void
    putPlan.push({ hold: new Promise<void>((r) => (release = r)), outcome: 'ok' })
    putPlan.push({ hold: Promise.resolve(), outcome: 'fail' })
    try {
      docs.rememberProjectDocument('p_a', { id: 'd_first', name: 'first' })
      docs.rememberProjectDocument('p_a', { id: 'd_second', name: 'second' })
      release()
      await settle()
    } finally {
      now.mockRestore()
    }
    expect(docs.readProjectDocument('p_a')).toEqual({ id: 'd_second', name: 'second' })
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_first') // 前提：后一次确实没推上去
    // 下次读时本机这条待确认、比后端新：以它为准并补推
    expect(await docs.loadProjectDocument('p_a')).toEqual({ id: 'd_second', name: 'second' })
    await settle()
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_second')
  })

  it('待确认写入之后后端没收过别的写（后端那条就是写下时见过的那条）：本机赢并补推', async () => {
    vi.resetModules()
    const docs = await import('@/lib/projectDocs')
    docs.rememberProjectDocument('p_a', { id: 'd_old', name: 'old' })
    await settle()
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_old')
    failSessionPuts = true
    docs.rememberProjectDocument('p_a', { id: 'd_new', name: 'new' })
    await settle()
    failSessionPuts = false
    expect(await docs.loadProjectDocument('p_a')).toEqual({ id: 'd_new', name: 'new' })
    await settle()
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_new')
  })

  it('连着远程实例、服务器钟比浏览器快得多：确知没推上去的那次照样不丢（只比后端的钟）', async () => {
    serverClock = Date.now() + 10_000_000_000
    vi.resetModules()
    const docs = await import('@/lib/projectDocs')
    docs.rememberProjectDocument('p_a', { id: 'd_old', name: 'old' })
    await settle()
    failSessionPuts = true
    docs.rememberProjectDocument('p_a', { id: 'd_new', name: 'new' })
    await settle()
    failSessionPuts = false
    expect(await docs.loadProjectDocument('p_a')).toEqual({ id: 'd_new', name: 'new' })
    await settle()
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_new')
  })

  it('读在路上时本机又记了一次（且已确认）：旧回包作废，不回滚', async () => {
    vi.resetModules()
    const docs = await import('@/lib/projectDocs')
    lastByProject.set('p_a', { doc_id: 'd_old', name: 'old', at: 1 })
    let release!: () => void
    holdSessionGet = new Promise<void>((r) => (release = r))
    const reading = docs.loadProjectDocument('p_a')
    await new Promise((r) => setTimeout(r, 10))
    docs.rememberProjectDocument('p_a', { id: 'd_newer', name: 'newer' })
    await settle() // 这次 PUT 已确认、待确认标记已摘
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_newer')
    release()
    expect(await reading).toEqual({ id: 'd_newer', name: 'newer' })
    expect(docs.readProjectDocument('p_a')).toEqual({ id: 'd_newer', name: 'newer' })
  })

  it('导出默认值：取回在路上时用户改了设置（已确认）：旧回包不盖掉', async () => {
    exportDefaults = { dpi: '600', formats: ['pdf', 'png'], withProof: false, strictInspection: false }
    vi.resetModules()
    const mod = await import('@/lib/exportDefaults')
    let release!: () => void
    holdPrefsGet = new Promise<void>((r) => (release = r))
    const hydrating = mod.hydrateExportDefaults()
    await new Promise((r) => setTimeout(r, 10))
    mod.writeExportDefaults({ dpi: '1200' })
    release()
    await hydrating
    await settle()
    expect(mod.readExportDefaults().dpi).toBe('1200')
    expect((exportDefaults as { dpi: string }).dpi).toBe('1200')
  })

  it('升级迁移（后端没记过、本机有旧记录）推失败：留待确认，下次读补推', async () => {
    localStorage.setItem('tavotto.projectDoc.p_a', JSON.stringify({ id: 'd_legacy', name: 'legacy' }))
    vi.resetModules()
    const docs = await import('@/lib/projectDocs')
    failSessionPuts = true
    expect(await docs.loadProjectDocument('p_a')).toEqual({ id: 'd_legacy', name: 'legacy' })
    await settle()
    failSessionPuts = false
    expect(lastByProject.get('p_a')).toBeUndefined() // 前提：迁移那次没推上去
    expect(JSON.parse(localStorage.getItem('tavotto.projectDoc.p_a')!).pendingGen).toBeTruthy()
    vi.resetModules()
    const again = await import('@/lib/projectDocs')
    expect(await again.loadProjectDocument('p_a')).toEqual({ id: 'd_legacy', name: 'legacy' })
    await settle()
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_legacy')
  })

  it('导出默认值推失败：下次启动不被后端的旧值盖掉，并补推', async () => {
    vi.resetModules()
    const before = await import('@/lib/exportDefaults')
    before.writeExportDefaults({ dpi: '600' })
    await settle()
    expect((exportDefaults as { dpi: string }).dpi).toBe('600')
    failSessionPuts = true
    before.writeExportDefaults({ dpi: '1200' })
    await settle()
    failSessionPuts = false

    vi.resetModules()
    const after = await import('@/lib/exportDefaults')
    await after.hydrateExportDefaults()
    await settle()
    expect(after.readExportDefaults().dpi).toBe('1200')
    expect((exportDefaults as { dpi: string }).dpi).toBe('1200')
  })
})

describe('切项目途中不串项目（#719 Codex P1）', () => {
  it('问后端「B 上次开着哪份」的路上改了 A 的文档：不记到 B 名下', async () => {
    const app = await boot()
    await makeContentDoc(app, 'd_a', 'Fig A')
    expect(lastByProject.get('p_a')?.doc_id).toBe('d_a')
    let release!: () => void
    holdSessionGet = new Promise<void>((r) => (release = r))
    const adopting = app.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    await new Promise((r) => setTimeout(r, 10))
    // 此刻 pj 已是 p_b，内存里仍是 A 的 d_a：用户又改了一笔
    expect(app.doc.useDocumentStore.getState().documentId).toBe('d_a')
    app.doc.useDocumentStore.getState().commit(literal('再加一段'), (d) => {
      d.objects.push(text('t2', 'more'))
    })
    await settle()
    release()
    holdSessionGet = null
    await adopting
    await settle()
    expect(lastByProject.get('p_b')?.doc_id).not.toBe('d_a')
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_a')
  })
})

describe('磁盘槽位清理只由后端做', () => {
  it('本机索引放不下的排版，前端不再发 DELETE 删它的磁盘槽位', async () => {
    const app = await boot()
    for (let i = 0; i < 14; i++) await makeContentDoc(app, `d_${i}`, `Fig ${i}`)
    expect(deletes).toEqual([])
    expect(slots.has('d_0')).toBe(true)
  })
})
