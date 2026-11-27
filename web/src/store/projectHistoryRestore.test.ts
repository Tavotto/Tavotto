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
import { fullCanvasNode, OPTIONAL_FIELDS } from '@/test/fullCanvasNode'

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

  it('认领被拒：重列一次，后端这次明说是复制才复制——新 docId，旧槽位与归属不动', async () => {
    const slot = seedMoved({ keepIndex: false })
    let listed = 0
    be.state.history.set('p_new', () =>
      // 第一次列：挪走（slot）；认领被拒之后重列：旧路径又出现了（复制）
      listed++ === 0 ? [row({ revision: slot.revision })] : [row({ mode: 'copy', owner: 'other' })],
    )
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(be.state.calls.filter((c) => c === 'POST claim d_move')).toHaveLength(1)
    expect(ds(app).documentId).not.toBe('d_move')
    expect(objectIds(app)).toEqual(['t_node'])
    expect(be.state.owners.get('d_move')).toBe('p_old')
    expect(be.state.slots.get('d_move')?.revision).toBe(slot.revision)
  })

  it('认领问不到（503 / 网络）：不另开一份、不写任何东西，给重试', async () => {
    const slot = seedMoved({ keepIndex: false })
    be.state.history.set('p_new', [row({ revision: slot.revision })])
    be.state.claimable.add('d_move')
    be.state.claimStatus = 503
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable', ref: { id: 'd_move' } })
    expect(be.writes()).toEqual([])
    expect(be.state.owners.get('d_move')).toBe('p_old')
    // 好了之后重试：照常认领并用槽位那份
    be.state.claimStatus = undefined
    const out = await app.restore.retryRestore()
    expect(out).toMatchObject({ kind: 'restored', id: 'd_move' })
    expect(objectIds(app)).toEqual(['t_node', 't_after'])
  })

  it('认领被拒、重列后仍是挪走（槽位刚被存过，revision 变了）：对着新 revision 再认领一次', async () => {
    const slot = seedMoved({ keepIndex: false })
    be.state.claimable.add('d_move')
    let listed = 0
    be.state.history.set('p_new', () => [row({ revision: listed++ === 0 ? 'stale' : slot.revision })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(be.state.calls.filter((c) => c === 'POST claim d_move')).toHaveLength(2)
    expect(ds(app).documentId).toBe('d_move')
    expect(be.state.owners.get('d_move')).toBe('p_new')
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

describe('槽位读不出、本机有副本（Codex #2235 P2）', () => {
  it('项目内候选判成 slot、自动保存 GET 一直失败：恢复卡给「打开本机副本」，选它就打开本机那份，不再回到同一张卡', async () => {
    const slot = be.putSlot('d_move', slotDoc(), 'p_new')
    be.state.history.set('p_new', [row({ owner: 'this', revision: slot.revision })])
    be.state.failGetDoc.set('d_move', 500)
    const local = slotDoc()
    local.canvases[0].objects.push(text('t_local', '本机副本里的'))
    localStorage.setItem('tavotto.autosave.d_move', JSON.stringify(local))
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    const issue = ds(app).restoreIssue!
    const alt = issue.alternatives.find((a) => a.localCopy)
    expect(alt?.id).toBe('d_move')
    // 本机副本不带项目内时间线的分派信息
    expect(alt?.history).toBeUndefined()
    const out = await app.restore.openAlternative('d_move')
    expect(out).toMatchObject({ kind: 'restored', id: 'd_move' })
    expect(objectIds(app)).toEqual(['t_node', 't_after', 't_local'])
    expect(ds(app).restoreIssue).toBeNull()
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
    // 记成当前项目「上次开着的」：下次打开走 last，不再靠项目历史（也不会每次加载都另开一份）
    await vi.waitFor(() => expect(be.session('p_new').last?.doc_id).toBe('d_node'))
    expect(ds(app).past).toHaveLength(1)
    ds(app).undo()
    expect(objectIds(app)).toEqual([])
  })

  it('节点存下的完整画布状态逐字恢复（留白 / 紧凑边 / 规范 / 样式 / 色键 / 一键排版 / 布局组），落盘的也是完整的（Codex #2235 P1）', async () => {
    const node = fullCanvasNode([text('t_node', '节点里的字')])
    be.state.nodes.set('d_full/v1-1', node)
    be.state.history.set('p_new', [row({ doc_id: 'd_full', mode: 'node', owner: 'none' })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    for (const key of OPTIONAL_FIELDS) expect(ds(app).doc[key], key).toEqual(node[key])
    expect(ds(app).doc.page).toEqual(node.page)
    expect(ds(app).doc.name).toBe(node.name)
    app.doc.flushAutosave()
    await vi.waitFor(() => expect(be.state.slots.has('d_full')).toBe(true))
    const saved = JSON.parse(be.state.slots.get('d_full')!.body) as ProjectDocument
    const canvas = saved.canvases.find((c) => c.id === 'c1')!
    for (const key of OPTIONAL_FIELDS) expect(canvas[key], `落盘的 ${key}`).toEqual(node[key])
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

describe('项目里的工作副本（ADR 0211 第二期 PR 2：自动保存写进项目）', () => {
  /** 工作副本：比任何时间线节点都新（节点之后又改了一笔），换电脑后本机槽位与绑定都没了 */
  const WORK_DOC = (() => {
    const pd = slotDoc()
    pd.updatedAt = 200
    return pd
  })()
  const workRow = (over: Record<string, unknown> = {}) =>
    row({
      doc_id: 'd_work', vid: '', mode: 'node', owner: 'none',
      work: { name: '排版一', revision: 'work-rev', ts: 200 }, ...over,
    })

  it('没有本机槽位：按清单 docId 指回工作副本（不读节点），同一个 docId、重新绑上项目文件，基线是读到的修订号', async () => {
    be.state.layouts.set('排版一', { doc: WORK_DOC, revision: 'work-rev' })
    be.state.history.set('p_new', [workRow()])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).toBe('d_work')
    expect(objectIds(app)).toEqual(['t_node', 't_after'])
    expect(be.state.calls).toContain('GET layout 排版一')
    expect(be.state.calls.some((c) => c.startsWith('GET version'))).toBe(false)
    expect(ds(app).projectFile).toMatchObject({ name: '排版一', file: 'tavottofile/排版一.json', revision: 'work-rev', dirty: false })
    // 装好之后当场落盘（没有可撤销的一步）：等它写完，别漏进下一条用例的假后端
    await vi.waitFor(() => expect(be.state.slots.has('d_work')).toBe(true))
  })

  it('复制（旧项目还在）：用工作副本内容、新 docId、不绑项目文件——两边互不影响', async () => {
    be.state.layouts.set('排版一', { doc: WORK_DOC, revision: 'work-rev' })
    be.state.history.set('p_new', [workRow({ mode: 'copy', owner: 'other' })])
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(ds(app).documentId).not.toBe('d_work')
    expect(objectIds(app)).toEqual(['t_node', 't_after'])
    expect(ds(app).projectFile).toBeNull()
    await vi.waitFor(() => expect(be.state.slots.has(ds(app).documentId)).toBe(true))
  })

  it('读不到工作副本（503 / 网络）：不另开一份、给重试', async () => {
    be.state.history.set('p_new', [workRow()]) // 假后端没有这份排版 → 404
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(ds(app).restoreIssue).toMatchObject({ kind: 'unavailable', ref: { id: 'd_work' } })
    expect(be.writes()).toEqual([])
    // 好了之后重试：按工作副本装
    be.state.layouts.set('排版一', { doc: WORK_DOC, revision: 'work-rev' })
    expect(await app.restore.retryRestore()).toMatchObject({ kind: 'restored', id: 'd_work' })
    await vi.waitFor(() => expect(be.state.slots.has('d_work')).toBe(true))
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
    await vi.waitFor(() => expect(be.session('p_new').last?.doc_id).toBe(id))
    expect(be.state.calls.some((c) => c.startsWith('POST claim'))).toBe(false)
    // 复制品另开这一份的时候请后端把复制源的改指表拷过来（后端重判、只拷改指表）
    expect(be.state.calls).toContain('POST copied d_copy')
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
    // 恢复卡区分同名候选用的右侧时间：节点时刻（后端给的）
    expect(issue?.alternatives.map((a) => a.at)).toEqual([50, 50])
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
