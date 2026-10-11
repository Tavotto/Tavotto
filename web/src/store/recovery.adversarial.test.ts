/**
 * 独立反例审查（QA 第二阶段）：每条都是一个具体操作序列，在集成后的树上跑。
 * 绿 = 该担心的路径验证过没问题（记入报告的「已验证」）；红 = 缺陷发现（记入「发现」）。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type ProjectDocument } from '@/types/document'
import {
  STATUS_B,
  be,
  boot,
  currentShape,
  failureSignal,
  localSnapshot,
  resetAll,
  savedProject,
  seedSaved,
  settle,
  settledWithin,
  shapeOf,
  text,
  until,
  type App,
  type Req,
} from './recovery.harness'

const cleanup: (() => void)[] = []
beforeEach(() => resetAll())
afterEach(async () => {
  cleanup.splice(0).forEach((stop) => stop())
  await settle()
})

const isSessionGet = (r: Req) => r.method === 'GET' && r.path === '/api/layout-session'
const isAutosavePut = (r: Req) => r.method === 'PUT' && r.path.startsWith('/api/autosave/')
const isLastPut = (r: Req) => r.method === 'PUT' && r.path === '/api/layout-session/last'
const blank = (name: string): ProjectDocument => ({ ...emptyProject(), project: { id: 'x', name } })

async function restore() {
  return await import('@/store/documentRestore')
}

/* ---------------- 问题 2：恢复未决期间还有哪些写入口没关 ---------------- */

describe('门关着时的写入口扫描', () => {
  it('restoring 期间调用每一个改文档的 store 动作：文档、画布、最近文档与后端都没有任何变化', async () => {
    seedSaved()
    const app = await boot()
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    cleanup.push(app.doc.startAutosave())
    await gate.reached
    const s = () => app.doc.useDocumentStore.getState()
    const before = { shape: currentShape(app), recent: JSON.stringify(s().recentDocs), local: localSnapshot() }
    const c1 = s().activeCanvasId
    const attempts: Record<string, () => unknown> = {
      commit: () => s().commit(literal('x'), (d) => void d.guides.push({ axis: 'x', pos: 1 })),
      amendLast: () => s().amendLast?.({} as never, () => undefined),
      beginTxn: () => s().beginTxn(literal('x')),
      txnUpdate: () => s().txnUpdate(() => undefined),
      undo: () => s().undo(),
      redo: () => s().redo(),
      silent: () => s().silent((d) => void (d.page.w = 321)),
      addCanvas: () => s().addCanvas('X'),
      renameCanvas: () => s().renameCanvas(c1, 'renamed'),
      duplicateCanvas: () => s().duplicateCanvas(c1),
      deleteCanvas: () => s().deleteCanvas(c1),
      reorderCanvases: () => s().reorderCanvases(0, 0),
      renameProject: () => s().renameProject('改名'),
      switchCanvas: () => s().switchCanvas(c1),
    }
    for (const [name, fn] of Object.entries(attempts)) {
      try {
        fn()
      } catch {
        /* 抛出也算拒绝 */
      }
      expect(currentShape(app), `${name} 改了占位文档`).toEqual(before.shape)
    }
    app.doc.flushAutosave()
    await settle()
    expect(be.count(isAutosavePut), '占位文档排队了自动保存').toBe(0)
    expect(be.count(isLastPut), '占位文档写了 last').toBe(0)
    expect(JSON.stringify(s().recentDocs)).toBe(before.recent)
    gate.release()
    await restoring
  })
})

/* ---------------- 问题 4：旧代 / 被取代的恢复 ---------------- */

describe('被取代的恢复不影响新操作', () => {
  it('session 暂态失败的退避期间用户显式新建并有内容：晚到的恢复不覆盖它', async () => {
    seedSaved()
    const app = await boot()
    be.fail(isSessionGet, 500, 1)
    const restoring = app.doc.restoreSession()
    expect(await until(() => be.count(isSessionGet) >= 1, 3000)).toBe(true)
    // 退避 250 ms 的窗口里：用户明确新建，写了一笔
    await app.doc.useDocumentStore.getState().switchDocument(blank('用户新建'), 'd_user')
    app.doc.useDocumentStore.getState().commit(literal('用户的一笔'), (d) => {
      d.objects.push(text('t_user', 'mine'))
    })
    await restoring
    await new Promise((r) => setTimeout(r, 1300)) // 把后面的重试窗口也走完
    await settle()
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_user')
    expect(shape.canvases[0].objects.map((o) => o.id)).toEqual(['t_user'])
    expect(be.count((r) => r.method === 'GET' && r.docId === 'd_saved'), '被取代的恢复还在读旧文档').toBe(0)
  })

  it('needs_user 之后切到另一个没有历史的项目：旧项目的恢复问题与门不残留', async () => {
    seedSaved('d_a', 'p_a', { last: false })
    be.owners.set('d_b', 'p_a')
    be.setLast('p_a', 'd_b', 'Draft B') // B 缺失 → needs_user
    const app = await boot()
    await app.doc.restoreSession()
    expect(app.doc.useDocumentStore.getState().restoreGate.state).toBe('needs_user')
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_B)
    await settle()
    const st = app.doc.useDocumentStore.getState()
    expect(st.restoreGate.state).toBe('ready')
    expect(st.restoreIssue).toBeNull()
    expect(app.proj.useProjectStore.getState().lastDocumentIssue).toBeNull()
    // 新项目是可写的
    st.commit(literal('第一笔'), (d) => void d.objects.push(text('t1', 'x')))
    expect(currentShape(app).canvases[0].objects.map((o) => o.id)).toEqual(['t1'])
  })

  it('handoff 在 needs_user 期间等待，用户选了备选：交接落进用户选的那份文档', async () => {
    const savedA = seedSaved('d_a', 'p_a', { last: false })
    be.owners.set('d_b', 'p_a')
    be.setLast('p_a', 'd_b', 'Draft B')
    localStorage.setItem('tavotto.currentDoc', 'd_a')
    localStorage.setItem(
      'tavotto.docIndex',
      JSON.stringify([{ id: 'd_a', name: savedA.project.name, savedAt: 1, objects: 1, canvases: 2, projectId: 'p_a' }]),
    )
    const app = await boot()
    await app.doc.restoreSession()
    expect(app.doc.useDocumentStore.getState().restoreGate.state).toBe('needs_user')
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    const r = await restore()
    const out = await r.openAlternative('d_a')
    expect(out.kind).toBe('restored')
    expect(await settledWithin(handoff, 5000)).toBe('selected')
    expect(currentShape(app)).toEqual({ documentId: 'd_a', ...shapeOf(savedA) })
  })
})

/* ---------------- 问题 7：没有前端线索的 runtime 老文件 ---------------- */

describe('找回副本（无任何前端线索的 runtime 文档）', () => {
  async function setup() {
    const pd = savedProject('runtime 旧稿')
    pd.canvases[0].objects = [
      {
        id: 'panel_rt', type: 'panel', fileId: 'runtime:fig1.py#Fig1_PRISMA', fileKind: 'runtime',
        nativeW: 80, nativeH: 60, x: 5, y: 6, w: 80, h: 60, script: 'fig1.py', figureFrame: 1,
        overrides: [{ gid: 'figure', prop: 'facecolor', value: '#0f0' }],
      } as never,
    ]
    be.putSlot('d_old_rt', pd) // 没有 owner / last / 缓存 / currentDoc
    const app = await boot()
    const rc = await import('@/lib/recoveryCandidates')
    const actions = await import('@/store/actions')
    const api = await import('@/lib/api')
    return { pd, app, rc, actions, api }
  }

  it('确认恢复副本：新 docId、编辑内容完整、源槽位字节与归属不动、副本归本项目', async () => {
    const { pd, app, rc, actions, api } = await setup()
    const page = await api.fetchRecoveryCandidates('unknown', 'p_a')
    if (typeof page === 'string') throw new Error(page)
    const item = page.items.find((i) => i.doc_id === 'd_old_rt')!
    expect(item).toBeTruthy()
    const sourceBefore = be.slotBytes('d_old_rt')
    cleanup.push(app.doc.startAutosave())
    const res = await rc.recoverCopy(item, {
      pj: 'p_a',
      openExisting: async (id) => {
        await actions.openRecentDocument(id)
        return true
      },
      install: actions.openDocumentAs,
    })
    expect(res.kind).toBe('opened')
    await settle()
    const target = rc.recoveryCopyId('d_old_rt', item.revision, 'p_a')
    const shape = currentShape(app)
    expect(shape.documentId).toBe(target)
    expect(shape.canvases.map((c) => c.objects)).toEqual(shapeOf(pd).canvases.map((c) => c.objects))
    expect(be.slotBytes('d_old_rt')).toBe(sourceBefore)
    expect(be.owners.has('d_old_rt')).toBe(false)
    expect(be.slots.has(target), '副本没有落盘').toBe(true)
    expect(be.owners.get(target)).toBe('p_a')
  })

  it('双击确认：只有一个副本槽位，源不动', async () => {
    const { app, rc, actions, api } = await setup()
    const page = await api.fetchRecoveryCandidates('unknown', 'p_a')
    if (typeof page === 'string') throw new Error(page)
    const item = page.items.find((i) => i.doc_id === 'd_old_rt')!
    cleanup.push(app.doc.startAutosave())
    const deps = {
      pj: 'p_a',
      openExisting: async (id: string) => {
        await actions.openRecentDocument(id)
        return true
      },
      install: actions.openDocumentAs,
    }
    const [a, b] = await Promise.all([rc.recoverCopy(item, deps), rc.recoverCopy(item, deps)])
    await settle()
    expect(a).toEqual(b)
    expect([...be.slots.keys()].filter((k) => k.startsWith('d_rc_'))).toHaveLength(1)
  })

  it('预览之后源被改了：stale，什么都不装、不写', async () => {
    const { app, rc, actions, api } = await setup()
    const page = await api.fetchRecoveryCandidates('unknown', 'p_a')
    if (typeof page === 'string') throw new Error(page)
    const item = page.items.find((i) => i.doc_id === 'd_old_rt')!
    const changed = savedProject('被改过')
    be.putSlot('d_old_rt', changed)
    const before = app.doc.useDocumentStore.getState().documentId
    const res = await rc.recoverCopy(item, {
      pj: 'p_a',
      openExisting: async () => true,
      install: actions.openDocumentAs,
    })
    expect(res.kind).toBe('stale')
    expect(app.doc.useDocumentStore.getState().documentId).toBe(before)
    expect([...be.slots.keys()].filter((k) => k.startsWith('d_rc_'))).toHaveLength(0)
    expect(be.count(isAutosavePut)).toBe(0)
  })
})


/* ---------------- deferUserEntry ---------------- */

describe('用户级加图入口在恢复期间排队（deferUserEntry）', () => {
  const textOnly = () => {
    const pd = savedProject('只有文字')
    pd.canvases[0].objects = [text('t_only', 'kept', 3, 4)]
    be.putSlot('d_text', pd)
    be.owners.set('d_text', 'p_a')
    be.setLast('p_a', 'd_text', pd.project.name)
    return pd
  }
  const figCount = (app: App) =>
    currentShape(app).canvases.flatMap((c) => c.objects).filter((o) => o.fileId === 'Fig1.pdf').length

  it('同一张素材卡在恢复期间连点两次：恢复后只有一个面板，且落进恢复出来的文档', async () => {
    textOnly()
    const app = await boot()
    await app.assets.useAssetStore.getState().load()
    const ws = await import('@/store/workspace')
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    expect(figCount(app), '占位文档上出现了面板').toBe(0)
    gate.release()
    await restoring
    await until(() => figCount(app) >= 1, 3000)
    await settle()
    expect(currentShape(app).documentId).toBe('d_text')
    expect(figCount(app)).toBe(1)
  })

  it('addPanelToCanvas 在恢复期间排队：落进恢复的文档，原对象保留', async () => {
    textOnly()
    const app = await boot()
    await app.assets.useAssetStore.getState().load()
    const ws = await import('@/store/workspace')
    const info = app.assets.useAssetStore.getState().panels[0]
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    expect(ws.addPanelToCanvas(info)).toBeNull()
    gate.release()
    await restoring
    await until(() => figCount(app) >= 1, 3000)
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_text')
    expect(shape.canvases[0].objects.map((o) => o.id)).toContain('t_only')
    expect(figCount(app)).toBe(1)
  })

  it('粘贴在恢复期间排队：负载被消费、恢复后落一次、落进恢复的文档', async () => {
    const pd = textOnly()
    // 先在一次正常会话里复制一个对象，拿到剪贴板文本
    let clip = ''
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (t: string) => void (clip = t), readText: async () => clip },
    })
    const first = await boot()
    expect(await first.doc.restoreSession()).toBe(true)
    const sel = await import('@/store/selectionStore')
    void sel
    first.doc.useDocumentStore.getState().commit(literal('加一个可复制的'), (d) => void d.objects.push(text('t_cp', 'copy me', 50, 50)))
    const selection = await import('@/store/selectionStore')
    selection.useSelectionStore.getState().set(['t_cp'])
    const clipboard = await import('@/lib/clipboard')
    expect(await clipboard.copySelectedObjects()).toBe(true)
    // 又一次启动：恢复卡在 last GET 上时粘贴
    be.slots.set('d_text', JSON.stringify(pd))
    const app = await boot()
    const cb = await import('@/lib/clipboard')
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const before = currentShape(app).canvases[0].objects.length
    void cb.pasteObjects()
    await new Promise((r) => setTimeout(r, 50))
    expect(currentShape(app).canvases[0].objects.length, '占位文档收到了粘贴').toBe(before)
    gate.release()
    await restoring
    await until(() => currentShape(app).canvases[0].objects.length > 1, 3000)
    await settle()
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_text')
    expect(shape.canvases[0].objects.filter((o) => o.text === 'copy me')).toHaveLength(1)
  })

  it('排队的加图 vs 切项目：不落进新项目的文档', async () => {
    textOnly()
    const savedB = seedSaved('d_b', 'p_b', { name: 'B 的排版' })
    const app = await boot()
    await app.assets.useAssetStore.getState().load()
    const ws = await import('@/store/workspace')
    const gate = be.hold((r) => isSessionGet(r) && r.pj === 'p_a')
    const restoring = app.doc.restoreSession()
    await gate.reached
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_B)
    gate.release()
    await restoring
    await settle()
    expect(currentShape(app)).toEqual({ documentId: 'd_b', ...shapeOf(savedB) })
  })

  it('排队的加图 vs 用户显式新建：不动被取代的旧稿，用户的新排版里一个都不落', async () => {
    textOnly()
    const original = be.slotBytes('d_text')
    const app = await boot()
    await app.assets.useAssetStore.getState().load()
    const ws = await import('@/store/workspace')
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    await app.doc.useDocumentStore.getState().switchDocument(blank('用户新建'), 'd_user')
    gate.release()
    await restoring
    await settle()
    await new Promise((r) => setTimeout(r, 50))
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_user')
    expect(figCount(app), '显式新建取代了恢复，排队的加图应被丢弃').toBe(0)
    expect(be.slotBytes('d_text')).toBe(original)
  })

  it('restoring 转 needs_user：已排的加图不落进占位文档；用户继续新建之后也一个都不落', async () => {
    seedSaved('d_a', 'p_a', { last: false })
    be.owners.set('d_b', 'p_a')
    be.setLast('p_a', 'd_b', 'Draft B') // B 缺失 → needs_user
    const app = await boot()
    await app.assets.useAssetStore.getState().load()
    const ws = await import('@/store/workspace')
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    gate.release()
    await restoring
    await settle()
    expect(app.doc.useDocumentStore.getState().restoreGate.state).toBe('needs_user')
    expect(figCount(app), '加图落进了 needs_user 期间的占位文档').toBe(0)
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('blocked')
    const r = await restore()
    await r.continueNew()
    await settle()
    await new Promise((r2) => setTimeout(r2, 50))
    expect(figCount(app), '继续新建之后，needs_user 之前排的加图应被丢弃').toBe(0)
  })

  it('恢复先到 needs_user、同项目重试成功：此前排队的加图仍执行一次', async () => {
    be.setLast('p_a', 'd_text', '暂时缺失')
    const app = await boot()
    await app.assets.useAssetStore.getState().load()
    const ws = await import('@/store/workspace')
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    gate.release()
    await restoring
    expect(app.doc.useDocumentStore.getState().restoreGate.state).toBe('needs_user')
    expect(figCount(app)).toBe(0)
    const body = savedProject('找回的文档')
    body.canvases[0].objects = [text('t_only', 'kept', 3, 4)]
    be.putSlot('d_text', body)
    be.owners.set('d_text', 'p_a')
    expect((await (await restore()).retryRestore()).kind).toBe('restored')
    await until(() => figCount(app) === 1, 3000)
    expect(currentShape(app).documentId).toBe('d_text')
    expect(currentShape(app).canvases[0].objects.map((o) => o.id)).toContain('t_only')
    expect(figCount(app)).toBe(1)
  })

  it('第一次重试期间排队、第二次重试成功：操作仍只落到恢复出的文档一次', async () => {
    be.setLast('p_a', 'd_text', '暂时缺失')
    const app = await boot()
    await app.assets.useAssetStore.getState().load()
    const ws = await import('@/store/workspace')
    expect(await app.doc.restoreSession()).toBe(false)
    const gate = be.hold(isSessionGet)
    const firstRetry = (await restore()).retryRestore()
    await gate.reached
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    gate.release()
    expect((await firstRetry).kind).toBe('needs_user')
    expect(figCount(app)).toBe(0)
    const body = savedProject('找回的文档')
    body.canvases[0].objects = [text('t_only', 'kept', 3, 4)]
    be.putSlot('d_text', body)
    be.owners.set('d_text', 'p_a')
    expect((await (await restore()).retryRestore()).kind).toBe('restored')
    await until(() => figCount(app) === 1, 3000)
    expect(currentShape(app).documentId).toBe('d_text')
    expect(currentShape(app).canvases[0].objects.map((o) => o.id)).toContain('t_only')
    expect(figCount(app)).toBe(1)
  })
})

/* ---------------- 预防性审查：家族 1 / 2 / 3 / 5 ---------------- */

describe('家族 1：读与安装之间同一个 docId 被别的项目覆盖', () => {
  const gBody = () => {
    const g = savedProject('G 的排版')
    g.canvases[0].objects = [text('t_g', 'G only', 1, 1)]
    return g
  }
  const putAs = async (pj: string, id: string, body: ProjectDocument) => {
    const res = await fetch(`/api/autosave/${id}`, {
      method: 'PUT',
      headers: { 'X-Tavotto-Project': pj, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    expect(res.status).toBe(200)
  }
  const ids = (app: App) => currentShape(app).canvases.flatMap((c) => c.objects.map((o) => o.id))

  it('发现 → 探测：唯一候选的正文 GET 在路上时被 G 覆盖，F 不得自动采用 G 的正文', async () => {
    seedSaved('d_x', 'p_a', { last: false }) // 只有 owners，没有 last / 缓存 → 走发现
    const app = await boot()
    const gate = be.hold((r) => r.method === 'GET' && r.docId === 'd_x')
    const restoring = app.doc.restoreSession()
    await gate.reached
    await putAs('p_b', 'd_x', gBody())
    gate.release()
    await restoring
    await settle()
    expect(ids(app), 'F 自动采用了 G 的正文').not.toContain('t_g')
    expect(autosavePutsBy('p_a')).toHaveLength(0)
  })

  it('needs_user 的备选：用户点选之前同一个 docId 被 G 覆盖，openAlternative 不得装入 G 的正文', async () => {
    const savedA = seedSaved('d_a', 'p_a', { last: false })
    be.owners.set('d_b', 'p_a')
    be.setLast('p_a', 'd_b', 'Draft B') // B 缺失 → needs_user，备选里有 A
    localStorage.setItem('tavotto.currentDoc', 'd_a')
    localStorage.setItem(
      'tavotto.docIndex',
      JSON.stringify([{ id: 'd_a', name: savedA.project.name, savedAt: 1, objects: 1, canvases: 2, projectId: 'p_a' }]),
    )
    const app = await boot()
    await app.doc.restoreSession()
    expect(app.doc.useDocumentStore.getState().restoreGate.state).toBe('needs_user')
    // 用户盯着这个问题看了一会儿：期间项目 G 把 d_a 存成了自己的
    await putAs('p_b', 'd_a', gBody())
    expect(be.owners.get('d_a')).toBe('p_b')
    const r = await restore()
    await r.openAlternative('d_a')
    await settle()
    expect(ids(app), 'F 打开了 G 的正文').not.toContain('t_g')
    expect(autosavePutsBy('p_a')).toHaveLength(0)
  })
})

function autosavePutsBy(pj: string) {
  return be.requests.filter((r) => isAutosavePut(r) && r.pj === pj)
}

describe('家族 3：失败被折成「没有」', () => {
  it('没有 last / 缓存时，候选发现端点 500：不能当成空项目静默放行', async () => {
    seedSaved('d_one', 'p_a', { last: false })
    be.fail((r) => r.path === '/api/layout-session/candidates', 500, 99)
    const app = await boot()
    await app.doc.restoreSession()
    await settle()
    const ui = (await import('@/store/uiStore')).useUiStore.getState()
    const signalled = failureSignal(app) !== null || ui.status !== null
    expect(signalled, '发现失败被当成「没有历史」，用户既没看到提示也没有重试出口').toBe(true)
  })
})

describe('家族 5：弱证据', () => {
  it('只有 assets 同名证据（owners / last / 缓存都没有）：不自动采用成本项目的排版', async () => {
    seedSaved('d_w', 'p_a', { last: false, owner: false })
    be.assetsEvidence.add('p_a:d_w')
    const app = await boot()
    await app.doc.restoreSession()
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId, '凭素材同名自动采用并之后会按本项目保存').not.toBe('d_w')
  })
})
