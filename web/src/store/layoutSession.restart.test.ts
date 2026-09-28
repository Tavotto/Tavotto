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
 *   5. 前端不再按本机 12 条 `docIndex` 发 DELETE 删磁盘槽位（换了 origin 的索引是空的）。
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
    if (u.pathname === '/api/layout-session' && method === 'GET') {
      return json({ last: lastByProject.get(pjOf(u, init)) ?? null })
    }
    if (u.pathname === '/api/layout-session/last' && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as { doc_id: string; name: string }
      const last = { doc_id: body.doc_id, name: body.name, at: 1 }
      lastByProject.set(pjOf(u, init), last)
      return json({ ok: true, last })
    }
    if (u.pathname === '/api/preferences/export-defaults') {
      if (method === 'PUT') {
        exportDefaults = JSON.parse(String(init?.body))
        return json({ ok: true, defaults: exportDefaults })
      }
      return json({ defaults: exportDefaults })
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

describe('磁盘槽位清理只由后端做', () => {
  it('本机索引放不下的排版，前端不再发 DELETE 删它的磁盘槽位', async () => {
    const app = await boot()
    for (let i = 0; i < 14; i++) await makeContentDoc(app, `d_${i}`, `Fig ${i}`)
    expect(deletes).toEqual([])
    expect(slots.has('d_0')).toBe(true)
  })
})
