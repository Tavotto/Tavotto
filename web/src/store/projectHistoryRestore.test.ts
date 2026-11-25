/**
 * ADR 0211 第一期 1–3：打开项目时从项目自己的时间线找回排版（store 层）。
 *
 * 真实的 `documentStore` / `documentRestore` / `projectDocs` / API client，唯一换掉的是 `fetch`（有状态假后端
 * `test/fakeSessionBackend`，加了 `/api/layout-session/project-history`、`/claim` 与节点正文）。`mode` 由后端判
 * （它看得到旧项目路径在不在）；这里看护的是前端怎么用它：
 *  - 挪走（`slot` + `moved`）：先认领，再用本机槽位那份（含节点之间的修改），本机索引改到当前项目；
 *    localStorage 完好（索引说它属于旧项目）与空存储两条分支都要能找回；
 *  - 只有节点（`node`）：从最近一个非空节点恢复成同一个 docId 的工作副本，⌘Z 一步退回；
 *  - 复制（`copy`）：新 docId，旧槽位与旧归属一个字节不动；
 *  - 后端没有归属记录、本机索引却说是别的项目：不认领，按复制；认领被拒同样退成复制；
 *  - 多份候选不猜：交给现有「选一份」的恢复卡（`choose` + alternatives），选了才装。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'

const be = createFakeBackend()

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

/** 本机槽位里那份：节点之后又改过一笔（t_after） */
function slotDoc(): ProjectDocument {
  const pd = emptyProject()
  pd.project.name = '排版一'
  pd.canvases[0].id = 'c1'
  pd.activeCanvasId = 'c1'
  pd.canvases[0].objects = [text('t_node', '节点里的字'), text('t_after', '节点之后的修改')]
  pd.updatedAt = 100
  return pd
}

/** 最近一个非空节点（schema 2 单画布） */
const NODE = {
  schema: 2, name: 'Fig 1', page: { w: 150, h: 100 }, objects: [text('t_node', '节点里的字')], guides: [],
}

const row = (over: Record<string, unknown> = {}) => ({
  doc_id: 'd_move', name: '排版一', vid: 'v1-1', ts: 50, canvasId: 'c1', schema: 2,
  canvases: 1, objects: 1, mode: 'slot', owner: 'moved', revision: null, node_newer: false, ...over,
})

async function boot(pj = 'p_new') {
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
const indexOwner = (id: string) =>
  (JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]') as { id: string; projectId?: string }[]).find(
    (e) => e.id === id,
  )?.projectId

/** 旧项目 p_old 里编辑过 d_move：槽位归它、节点在（挪走之后跟着文件夹到了 p_new 的 tavottofile/versions/） */
function seedMoved({ keepIndex }: { keepIndex: boolean }) {
  const slot = be.putSlot('d_move', slotDoc(), 'p_old')
  be.setLast('p_old', 'd_move', '排版一')
  be.state.nodes.set('d_move/v1-1', NODE)
  if (keepIndex) {
    localStorage.setItem('tavotto.currentDoc', 'd_move')
    localStorage.setItem(
      'tavotto.docIndex',
      JSON.stringify([{ id: 'd_move', name: '排版一', savedAt: 1, objects: 2, projectId: 'p_old' }]),
    )
  }
  return slot
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  be.reset()
  vi.stubGlobal('fetch', be.fetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('整夹挪走（旧路径不在）：用本机槽位那份，归属改到新项目', () => {
  it.each([
    ['本机存储完好（索引记着旧项目）', true],
    ['换了一份空的本机存储（换端口 / 换 origin）', false],
  ])('%s：打开新路径就是上次的排版，节点之后的修改还在', async (_name, keepIndex) => {
    const slot = seedMoved({ keepIndex })
    be.state.history.set('p_new', [row({ revision: slot.revision })])
    be.state.claimable.add('d_move')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_move')
    expect(objectIds(app)).toEqual(['t_node', 't_after'])
    expect(be.state.calls).toContain('POST claim d_move')
    expect(be.state.owners.get('d_move')).toBe('p_new')
    expect(indexOwner('d_move')).toBe('p_new')
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'restored', pj: 'p_new' })
    // 记成新项目「上次开着的」：下次打开直接走 last，不再靠项目历史
    await vi.waitFor(() => expect(be.session('p_new').last?.doc_id).toBe('d_move'))
  })

  it('认领被拒（此刻不再满足挪走的条件）：不抢归属，退成复制——新 docId，旧槽位不动', async () => {
    const slot = seedMoved({ keepIndex: false })
    be.state.history.set('p_new', [row({ revision: slot.revision })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).not.toBe('d_move')
    expect(objectIds(app)).toEqual(['t_node'])
    expect(be.state.owners.get('d_move')).toBe('p_old')
    expect(be.state.slots.get('d_move')?.revision).toBe(slot.revision)
  })

  it('节点比槽位新（历史在别处接着写过）：装槽位，再把节点作为一笔可撤销的编辑写上去', async () => {
    const slot = seedMoved({ keepIndex: false })
    be.state.history.set('p_new', [row({ revision: slot.revision, node_newer: true })])
    be.state.claimable.add('d_move')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    await vi.waitFor(() => expect(objectIds(app)).toEqual(['t_node']))
    expect(ds(app).past).toHaveLength(1)
    ds(app).undo()
    expect(objectIds(app)).toEqual(['t_node', 't_after'])
  })
})

describe('只有项目里的节点（换电脑 / 换数据目录）', () => {
  it('从最近一个非空节点恢复成同一个 docId 的工作副本，⌘Z 一步退回，随后落盘', async () => {
    be.state.nodes.set('d_node/v1-1', NODE)
    be.state.history.set('p_new', [row({ doc_id: 'd_node', mode: 'node', owner: 'none' })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_node')
    expect(objectIds(app)).toEqual(['t_node'])
    expect(ds(app).activeCanvasId).toBe('c1') // 画布身份沿用节点的：时间线按画布分组仍对得上
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', pj: 'p_new' })
    // 恢复是一笔普通编辑：自动保存把工作副本写进槽位，记在当前项目名下
    app.doc.flushAutosave()
    await vi.waitFor(() => expect(be.state.slots.has('d_node')).toBe(true))
    expect(be.state.owners.get('d_node')).toBe('p_new')
    expect(ds(app).past).toHaveLength(1)
    ds(app).undo()
    expect(objectIds(app)).toEqual([])
  })

  it('后端没有归属记录、本机索引却说是别的项目（可能就在旁边，是复制）：不沿用 docId', async () => {
    be.state.nodes.set('d_node/v1-1', NODE)
    be.state.history.set('p_new', [row({ doc_id: 'd_node', mode: 'node', owner: 'none' })])
    localStorage.setItem(
      'tavotto.docIndex',
      JSON.stringify([{ id: 'd_node', name: '排版一', savedAt: 1, objects: 1, projectId: 'p_old' }]),
    )
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).not.toBe('d_node')
    expect(objectIds(app)).toEqual(['t_node'])
    expect(indexOwner('d_node')).toBe('p_old')
  })
})

describe('复制（旧路径还在）', () => {
  it('当前项目用节点恢复出新 docId 的一份；旧项目的槽位、归属、last 一个字节不动', async () => {
    const slot = be.putSlot('d_copy', slotDoc(), 'p_old')
    be.setLast('p_old', 'd_copy', '排版一')
    be.state.nodes.set('d_copy/v1-1', NODE)
    be.state.history.set('p_new', [row({ doc_id: 'd_copy', mode: 'copy', owner: 'other' })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    const id = ds(app).documentId
    expect(id).not.toBe('d_copy')
    expect(objectIds(app)).toEqual(['t_node'])
    app.doc.flushAutosave()
    await vi.waitFor(() => expect(be.state.slots.has(id)).toBe(true))
    expect(be.state.owners.get(id)).toBe('p_new')
    expect(be.state.calls.some((c) => c.startsWith('POST claim'))).toBe(false)
    expect(be.state.slots.get('d_copy')?.revision).toBe(slot.revision)
    expect(be.state.owners.get('d_copy')).toBe('p_old')
    expect(be.session('p_old').last?.doc_id).toBe('d_copy')
  })
})

describe('多份候选不猜', () => {
  it('两份项目内候选：交给现有「选一份」的恢复卡，选了哪份才装哪份', async () => {
    be.state.nodes.set('d_a/v1-1', NODE)
    be.state.nodes.set('d_b/v1-1', { ...NODE, objects: [text('t_b', 'B')] })
    be.state.history.set('p_new', [
      row({ doc_id: 'd_a', name: 'A', mode: 'node', owner: 'none' }),
      row({ doc_id: 'd_b', name: 'B', mode: 'node', owner: 'none' }),
    ])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    const issue = ds(app).restoreIssue
    expect(issue?.kind).toBe('choose')
    expect(issue?.alternatives.map((a) => a.id)).toEqual(['d_a', 'd_b'])
    expect(be.writes()).toEqual([])
    const out = await app.restore.openAlternative('d_b')
    expect(out).toMatchObject({ kind: 'restored', id: 'd_b' })
    expect(objectIds(app)).toEqual(['t_b'])
  })

  it('一份槽位发现 + 一份项目内候选：同样交给用户选', async () => {
    be.putSlot('d_slot', slotDoc(), 'p_new')
    be.state.candidates = [{ doc_id: 'd_slot', scope: 'project' }]
    be.state.nodes.set('d_h/v1-1', NODE)
    be.state.history.set('p_new', [row({ doc_id: 'd_h', mode: 'node', owner: 'none' })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue?.alternatives.map((a) => a.id)).toEqual(['d_slot', 'd_h'])
  })

  it('同一个 docId 槽位发现已经列出：不重复算成两份', async () => {
    be.putSlot('d_same', slotDoc(), 'p_new')
    be.state.candidates = [{ doc_id: 'd_same', scope: 'project' }]
    be.state.history.set('p_new', [row({ doc_id: 'd_same', mode: 'slot', owner: 'this' })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_same')
  })
})

describe('没有项目内候选', () => {
  it('后端没有这个端点（旧后端 / playground）：照旧是空白，不报错', async () => {
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty' })
  })

  it('项目里没有有内容的时间线：空白排版', async () => {
    be.state.history.set('p_new', [])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'empty' })
  })
})
