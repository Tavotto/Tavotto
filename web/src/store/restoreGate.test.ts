/**
 * 恢复门的写入口清单（CONTRACT 2.4）：门关着（restoring / needs_user）时，所有能写文档的入口一律拒，
 * 拒的方式是**不改 doc、不排队写盘、不写 currentDoc / 最近文档 / last**；门开了（ready）原样可写。
 *
 * 一条用例遍历整张入口表——新增一个写文档的入口而忘了看门，这里就会少一行（表和 `DocumentState` 的
 * 方法一一对应）。另外钉：时间线的自动节点 / 关键时刻、remember-last 订阅也看同一扇门。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'

const be = createFakeBackend()
const text = (id: string): TextObject => ({
  id, type: 'text', text: id, sizePt: 9, bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const fetchImpl = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://127.0.0.1')
  if (u.pathname.startsWith('/api/projects')) {
    return new Response(JSON.stringify({ recent: [], pinned: [], projects: [] }), { status: 200 })
  }
  return be.fetch(url as RequestInfo, init)
}) as typeof fetch

async function boot() {
  vi.resetModules()
  const session = await import('@/lib/session')
  session.setCurrentProjectId('p_a')
  const doc = await import('@/store/documentStore')
  const restore = await import('@/store/documentRestore')
  const proj = await import('@/store/projectStore')
  restore.setRestoreRetryDelays([0, 0])
  proj.useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p_a', figures_dir: '/figs/a' }, lastDocumentIssue: null })
  return { doc, restore, proj }
}

/** 造一份有内容、有两张画布的文档并把它装好（门开着） */
async function withContent(app: Awaited<ReturnType<typeof boot>>) {
  const pd: ProjectDocument = { ...emptyProject(), project: { id: 'x', name: 'N' } }
  await app.doc.useDocumentStore.getState().switchDocument(pd, 'd_real')
  const s = app.doc.useDocumentStore.getState()
  s.commit(literal('加字'), (d) => { d.objects.push(text('t1')) })
  s.addCanvas('第二张')
  app.doc.flushAutosave()
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  be.reset()
  vi.stubGlobal('fetch', fetchImpl)
})
afterEach(() => vi.unstubAllGlobals())

describe('写入口一律看门', () => {
  for (const mode of ['restoring', 'needs_user'] as const) {
    it(`${mode}：commit / 事务 / undo / redo / silent / 画布增删改 / 改名 / 派生更新 都不改文档、不写盘`, async () => {
      const app = await boot()
      await withContent(app)
      const s = () => app.doc.useDocumentStore.getState()
      // 有一条可撤销的历史和一条可重做的历史，undo/redo 才有东西可退
      s().commit(literal('再加'), (d) => { d.objects.push(text('t2')) })
      s().undo()
      await vi.waitFor(() => expect(be.state.slots.has('d_real')).toBe(true))
      const snapshot = { doc: s().doc, canvases: s().canvases, active: s().activeCanvasId, past: s().past, future: s().future }
      const written = JSON.stringify([...be.state.slots.entries()])
      be.state.calls.length = 0

      // 关门：restoring 直接 beginRestore；needs_user 再 settle 成问题
      const token = app.doc.beginRestore('p_a')
      if (mode === 'needs_user') {
        app.doc.settleRestore(token, {
          state: 'needs_user',
          issue: { pj: 'p_a', gen: token.gen, kind: 'unavailable', ref: { id: 'd_x', name: 'X' }, alternatives: [] },
        })
      }
      expect(app.doc.isDocumentPlaceholder()).toBe(true)

      const other = s().canvases.find((c) => c.id !== s().activeCanvasId)!.id
      s().commit(literal('x'), (d) => { d.objects.push(text('t3')) })
      s().beginTxn(literal('拖'))
      s().txnUpdate((d) => { d.objects.push(text('t4')) })
      s().endTxn()
      s().undo()
      s().redo()
      s().silent((d) => { d.page.w = 11 })
      s().amendLast({ label: literal('a'), patches: [], inverse: [] }, () => {})
      s().switchCanvas(other)
      s().openCanvasTab(other)
      s().closeCanvasTab(other)
      s().reorderTabs(0, 0)
      expect(s().addCanvas('新')).toBe(snapshot.active)
      s().renameCanvas(other, '改名')
      expect(s().duplicateCanvas(other)).toBeNull()
      expect(s().deleteCanvas(other)).toBe(false)
      s().reorderCanvases(0, 1)
      s().renameProject('改了项目名')
      app.doc.applyDerivedUpdate({ doc: { ...s().doc, page: { ...s().doc.page, w: 22 } } })
      app.doc.flushAutosave()

      expect(s().doc).toBe(snapshot.doc)
      expect(s().canvases).toBe(snapshot.canvases)
      expect(s().activeCanvasId).toBe(snapshot.active)
      expect(s().past).toBe(snapshot.past)
      expect(s().future).toBe(snapshot.future)
      expect(s().projectMeta.name).toBe('N')
      expect(s().txn).toBeNull()
      expect(app.doc.flushAutosave()).toBe('skipped')
      expect(be.writes()).toEqual([])
      expect(JSON.stringify([...be.state.slots.entries()])).toBe(written)

      // 门开了（用户选了出口）：同一批入口恢复可用
      await app.restore.continueNew()
      s().commit(literal('x'), (d) => { d.objects.push(text('t3')) })
      expect(s().doc.objects.map((o) => o.id)).toEqual(['t3'])
    })
  }

  it('门关着时 startAutosave 的订阅也不为占位排队写盘（绕过入口直接 setState 也一样）', async () => {
    const app = await boot()
    const stop = app.doc.startAutosave()
    try {
      app.doc.beginRestore('p_a')
      // 绕过所有写入口：直接塞一个对象进占位文档（用例构造的最坏情况）
      app.doc.useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [text('sneak')] } }))
      expect(app.doc.useDocumentStore.getState().dirty).toBe(false)
      expect(app.doc.flushAutosave()).toBe('skipped')
      expect(be.writes()).toEqual([])
    } finally {
      stop()
    }
  })

  it('绕过门被改过的占位不被迟到的恢复覆盖：身份变了就让位，门放成 ready 保留内存里那份', async () => {
    be.putSlot('d_saved', { ...emptyProject(), project: { id: 'x', name: 'Saved' } }, 'p_a')
    const savedDoc = emptyProject()
    savedDoc.canvases[0].objects = [text('saved')]
    be.putSlot('d_saved', savedDoc, 'p_a')
    be.setLast('p_a', 'd_saved', 'Saved')
    const app = await boot()
    let release!: () => void
    be.state.hold.session = new Promise<void>((r) => (release = r))
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    app.doc.useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [text('sneak')] } }))
    release()
    expect(await restoring).toBe(false)
    expect(app.doc.useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['sneak'])
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'superseded' })
  })
})

describe('时间线与 remember-last 看同一扇门', () => {
  it('占位期间：自动节点不拍、关键时刻不打；门开了照常', async () => {
    const app = await boot()
    const cp = await import('@/lib/timelineCheckpoint')
    const hook = await import('@/hooks/useVersionCheckpoints')
    const taken = vi.spyOn(cp, 'takeCheckpoint').mockResolvedValue(null as never)
    const stop = hook.startVersionCheckpoints()
    try {
      const token = app.doc.beginRestore('p_a')
      await cp.markMoment('open')
      expect(taken).not.toHaveBeenCalled()
      app.doc.settleRestore(token, { state: 'ready', reason: 'empty' })
      await cp.markMoment('open')
      expect(taken).toHaveBeenCalledTimes(1)
    } finally {
      stop()
    }
  })

  it('占位期间 remember-last 订阅不记：就算占位被绕过门写进了内容，也不会被登记成「上次开着的」（缓存 / 后端 last 都不动）', async () => {
    const app = await boot()
    app.doc.beginRestore('p_a')
    app.doc.useDocumentStore.setState((st) => ({ doc: { ...st.doc, objects: [text('sneak')] } }))
    expect(localStorage.getItem('tavotto.projectDoc.p_a')).toBeNull()
    expect(be.puts().filter((c) => c.startsWith('PUT last'))).toEqual([])
  })

  it('令牌安装的真文档被登记成「上次开着的」（只记一次，缓存已校正就不再往后端推）', async () => {
    const saved = emptyProject()
    saved.project.name = 'Saved'
    saved.canvases[0].objects = [text('saved')]
    be.putSlot('d_saved', saved, 'p_a')
    be.setLast('p_a', 'd_saved', 'Saved')
    const app = await boot()
    await app.restore.restoreSession()
    expect(app.doc.useDocumentStore.getState().documentId).toBe('d_saved')
    expect(JSON.parse(localStorage.getItem('tavotto.projectDoc.p_a')!)).toMatchObject({ id: 'd_saved' })
    expect(be.puts().filter((c) => c.startsWith('PUT last'))).toEqual([])
  })
})
