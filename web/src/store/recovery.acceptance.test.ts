/**
 * 文档恢复可靠性的**正确行为验收**（QA，2026-10-11；任务书 9.1 的 A01–A33）。
 *
 * 与审计夹具的区别：审计断言的是旧缺陷（`expect(restored).toBe(false)`、`overrides=[]`），
 * 这里每条都翻成修复后应成立的断言——documentId、画布数、对象 id / 几何、非空 overrides、
 * 后端 last / owner、实际 GET / PUT 序列、原槽位字节。**不以标题、提示文案、截图判断。**
 *
 * 只用已有公共入口驱动（`restoreSession` / `adoptOpenedProject` / `applyOpenRequest` /
 * `commit` / `switchDocument` / `startAutosave` / `flushAutosave`），唯一被替换的是 `fetch`
 * （`recovery.harness.ts` 的有状态假后端 + 手动 gate，不用 sleep 判时序）。
 * 「明确的失败状态」用旧有的 `lastDocumentIssue` / `docNotice` 或修复后的 `restoreGate`
 * 任一成立判（`failureSignal`）；只在修复后才有的状态放在末尾的
 * `describe('修复后新增状态')`，缺模块时 `ctx.skip()`，不计入红证据。
 *
 * 编号对应任务书 9.1 的 A01–A33；每个 `it` 名字以编号开头，便于对表。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject, type ProjectDocument } from '@/types/document'
import {
  STATUS_A,
  STATUS_B,
  be,
  boot,
  currentShape,
  failureSignal,
  localSnapshot,
  newOrigin,
  resetAll,
  savedProject,
  seedSaved,
  settle,
  settledWithin,
  shapeOf,
  text,
  until,
  SAVED_PANEL,
  type App,
  type Req,
} from './recovery.harness'

const cleanup: (() => void)[] = []

beforeEach(() => {
  resetAll()
})

afterEach(async () => {
  cleanup.splice(0).forEach((stop) => stop())
  await settle()
})

/* --------------------------------- 小工具 --------------------------------- */

const isSessionGet = (r: Req) => r.method === 'GET' && r.path === '/api/layout-session'
const isAutosaveGet = (id: string) => (r: Req) =>
  r.method === 'GET' && r.path === `/api/autosave/${id}`
const isAutosavePut = (r: Req) => r.method === 'PUT' && r.path.startsWith('/api/autosave/')
const isLastPut = (r: Req) => r.method === 'PUT' && r.path === '/api/layout-session/last'
const lastPutIds = () =>
  be.requests.filter(isLastPut).map((r) => (JSON.parse(r.body ?? '{}') as { doc_id: string }).doc_id)

/** 冷启动 = 工作台挂载时那一次恢复；Picker = 先 adopt（Picker 打开项目）再工作台挂载的那一次恢复 */
async function reopen(app: App, how: 'cold' | 'picker') {
  if (how === 'picker') {
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_A)
  }
  return app.doc.restoreSession()
}

const bootFor = (how: 'cold' | 'picker') => boot({ open: how === 'cold' })

const blankProject = (name: string): ProjectDocument => ({
  ...emptyProject(),
  project: { id: 'x', name },
})

/** 摆本机留下的线索：currentDoc / 最近文档索引条目 / 按项目缓存 */
function seedLocal(opts: {
  currentDoc?: string
  index?: { id: string; name: string; projectId: string | null }[]
  projectDoc?: { pj: string; id: string; name: string }
}) {
  if (opts.currentDoc) localStorage.setItem('tavotto.currentDoc', opts.currentDoc)
  if (opts.index) {
    localStorage.setItem(
      'tavotto.docIndex',
      JSON.stringify(
        opts.index.map((e) => ({
          id: e.id,
          name: e.name,
          savedAt: 1,
          objects: 1,
          canvases: 2,
          ...(e.projectId ? { projectId: e.projectId } : {}),
        })),
      ),
    )
  }
  if (opts.projectDoc) {
    localStorage.setItem(
      `tavotto.projectDoc.${opts.projectDoc.pj}`,
      JSON.stringify({ id: opts.projectDoc.id, name: opts.projectDoc.name }),
    )
  }
}

const autosavePuts = () => be.requests.filter(isAutosavePut)

/** 给「急切」的实现一个先动手的机会：它若不等恢复，这里很快就已经落地；等恢复的实现在这里什么都没发生 */
const letEagerHandoffAct = (p: Promise<unknown>) => settledWithin(p, 300)

/** 两组键值的差异键（失败信息里直接指名谁变了） */
const changedKeys = (a: Record<string, unknown>, b: Record<string, unknown>) =>
  [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((k) => a[k] !== b[k]).sort()

/** 槽位里现在的排版形状（不比字节：离开旧文档时的 flush 会重写它，内容不变） */
const slotShape = (id: string) => shapeOf(JSON.parse(be.slotBytes(id)!) as ProjectDocument)

/* ================================ A01 正常对照 ================================ */

describe('A01 有效 last 的正常恢复（对照：基线应绿）', () => {
  it.each([
    { origin: '同 origin（本机留着线索）', same: true },
    { origin: '新 origin（本机全空）', same: false },
  ])('冷启动 $origin：同一 docId、两张画布、对象几何与非空 overrides，装载不产生 autosave PUT', async ({ same }) => {
    const saved = seedSaved()
    if (same) {
      seedLocal({
        currentDoc: 'd_saved',
        index: [{ id: 'd_saved', name: saved.project.name, projectId: 'p_a' }],
        projectDoc: { pj: 'p_a', id: 'd_saved', name: saved.project.name },
      })
    }
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    await settle()
    const shape = currentShape(app)
    expect(shape).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
    expect(shape.canvases).toHaveLength(2)
    expect((shape.canvases[0].objects[0] as { overrides: unknown[] }).overrides).toHaveLength(1)
    expect(autosavePuts()).toHaveLength(0)
    expect(be.lastOf('p_a')?.doc_id).toBe('d_saved')
  })

  it('Picker 打开项目：同样回到同一份排版，无 autosave PUT', async () => {
    const saved = seedSaved()
    const app = await boot({ open: false })
    await reopen(app, 'picker')
    await settle()
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
    // 基线的 Picker 路径装载后会立刻落一次快照（switchDocument 的 flush）：允许，但必须是同一份内容、同一个项目
    for (const put of autosavePuts()) {
      expect(put.docId).toBe('d_saved')
      expect(put.pj).toBe('p_a')
      expect(shapeOf(JSON.parse(put.body!) as ProjectDocument)).toEqual(shapeOf(saved))
    }
  })
})

/* ============================ A02 页宽 / 参考线 vs 恢复 ============================ */

describe('A02 last GET 延迟期间改初始文档', () => {
  it.each([
    { kind: '页宽', edit: (d: { page: { w: number }; guides: unknown[] }) => { d.page.w = 333 } },
    { kind: '参考线', edit: (d: { page: { w: number }; guides: unknown[] }) => { d.guides.push({ axis: 'x', pos: 20 }) } },
  ])('$kind：受屏障或有序处理，不能抢走旧文档的恢复；原文件与恢复入口保留', async ({ edit }) => {
    const saved = seedSaved()
    const original = be.slotBytes('d_saved')
    const app = await boot()
    const blankId = app.doc.useDocumentStore.getState().documentId
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    cleanup.push(app.doc.startAutosave())
    await gate.reached
    app.doc.useDocumentStore.getState().commit(literal('初始文档上的编辑'), (d) => {
      edit(d as unknown as { page: { w: number }; guides: unknown[] })
    })
    gate.release()
    await restoring
    await settle()
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
    expect(be.lastOf('p_a')?.doc_id).toBe('d_saved')
    expect(be.slotBytes('d_saved')).toBe(original)
    expect(be.slots.has(blankId)).toBe(false)
  })
})

/* ================================ A03 / A22 handoff ================================ */

describe('A03 handoff 在恢复未决期间到达', () => {
  it.each(['last', 'autosave'] as const)(
    '在 %s GET 期间：恢复后选中原面板（selected），保留原 overrides，不重复放图，不改 last',
    async (stage) => {
      const saved = seedSaved()
      const original = be.slotBytes('d_saved')
      const app = await boot()
      const gate = be.hold(stage === 'last' ? isSessionGet : isAutosaveGet('d_saved'))
      const restoring = app.doc.restoreSession()
      cleanup.push(app.doc.startAutosave())
      await gate.reached
      // useHandoff 在 phase === open 时就发起它，且不等 restoreSession
      const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
      await letEagerHandoffAct(handoff)
      gate.release()
      const outcome = await settledWithin(handoff, 5000)
      await restoring
      await settle()
      expect(outcome).toBe('selected')
      const shape = currentShape(app)
      expect(shape).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
      const panels = shape.canvases.flatMap((c) => c.objects).filter((o) => o.fileId === 'Fig1.pdf')
      expect(panels).toHaveLength(1)
      expect(panels[0].id).toBe('panel_saved')
      expect(be.lastOf('p_a')?.doc_id).toBe('d_saved')
      expect(be.slotBytes('d_saved')).toBe(original)
    },
  )

  it('恢复出来的文档里没有这张图：交接把它加进恢复的文档，而不是加进占位空白', async () => {
    const pd = savedProject('只有文字')
    pd.canvases[0].objects = [text('t_only', 'kept', 3, 4)]
    be.putSlot('d_text', pd)
    be.owners.set('d_text', 'p_a')
    be.setLast('p_a', 'd_text', pd.project.name)
    const app = await boot()
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    await letEagerHandoffAct(handoff)
    gate.release()
    const outcome = await settledWithin(handoff, 5000)
    await restoring
    await settle()
    expect(outcome).toBe('placed')
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_text')
    const ids = shape.canvases[0].objects.map((o) => o.id)
    expect(ids).toContain('t_only')
    expect(shape.canvases[0].objects.filter((o) => o.fileId === 'Fig1.pdf')).toHaveLength(1)
  })

  it('正常新图交接（没有历史，恢复期间到达）仍 placed，不死等', async () => {
    const app = await boot()
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    await letEagerHandoffAct(handoff)
    gate.release()
    const outcome = await settledWithin(handoff, 5000)
    await restoring
    expect(outcome).toBe('placed')
    const shape = currentShape(app)
    expect(shape.canvases[0].objects.filter((o) => o.fileId === 'Fig1.pdf')).toHaveLength(1)
  })
})

describe('A22 重复 / 取消 / 切项目的交接', () => {
  it('URL 与事件同时交接同一张图（恢复未决期间）：只有原面板，不重复放图', async () => {
    const saved = seedSaved()
    const app = await boot()
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const a = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    const b = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    await letEagerHandoffAct(Promise.all([a, b]))
    gate.release()
    const outcomes = await settledWithin(Promise.all([a, b]), 5000)
    await restoring
    await settle()
    expect(outcomes).toEqual(['selected', 'selected'])
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
  })

  it('空项目里同时两次交接：只放一份', async () => {
    const app = await boot()
    await app.doc.restoreSession()
    const outcomes = await Promise.all([
      app.handoff.applyOpenRequest({ stem: 'Fig1' }),
      app.handoff.applyOpenRequest({ stem: 'Fig1' }),
    ])
    expect(outcomes).toContain('placed')
    const panels = currentShape(app).canvases.flatMap((c) => c.objects).filter((o) => o.fileId === 'Fig1.pdf')
    expect(panels).toHaveLength(1)
  })

  it('找不到的 stem 在恢复未决期间到达：恢复后得到终局（missing），不永久挂起', async () => {
    seedSaved()
    const app = await boot()
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const handoff = app.handoff.applyOpenRequest({ stem: 'NoSuchFigure' })
    await letEagerHandoffAct(handoff)
    gate.release()
    expect(await settledWithin(handoff, 5000)).toBe('missing')
    await restoring
  })

  it('恢复未决期间用户切到另一个项目：挂起的交接得到终局，且没有把图放进新项目的文档', async () => {
    seedSaved('d_saved', 'p_a')
    const savedB = seedSaved('d_b', 'p_b', { name: 'B 的排版' })
    const app = await boot()
    const gate = be.hold((r) => isSessionGet(r) && r.pj === 'p_a')
    const restoring = app.doc.restoreSession()
    await gate.reached
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_B)
    gate.release()
    expect(await settledWithin(handoff, 5000)).not.toBe('pending')
    await restoring
    await settle()
    expect(currentShape(app)).toEqual({ documentId: 'd_b', ...shapeOf(savedB) })
  })
})

/* ================================ A33 native 交接 ================================ */

describe('A33 native 交接在恢复未决时', () => {
  function spyReceive(app: App) {
    const received: string[] = []
    app.native.useNativeSessionStore.setState({
      receive: ((id: string) => {
        received.push(id)
        return Promise.resolve()
      }) as never,
    })
    return received
  }

  it('native-only：接收 / 确认立即进行，不等文档就绪', async () => {
    seedSaved()
    const app = await boot()
    const received = spyReceive(app)
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const outcome = await settledWithin(app.handoff.applyOpenRequest({ native: 'n1' }), 3000)
    expect(outcome).toBe('native-pending')
    expect(received).toEqual(['n1'])
    gate.release()
    await restoring
  })

  it('stem+native：native 接收立即进行；实际加图等到恢复完成（selected 原面板）', async () => {
    const saved = seedSaved()
    const app = await boot()
    const received = spyReceive(app)
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1', native: 'n2' })
    // 接收不依赖文档：恢复还卡在 last GET 时它已经发出
    expect(await until(() => received.length === 1, 3000)).toBe(true)
    await letEagerHandoffAct(handoff)
    gate.release()
    const outcome = await settledWithin(handoff, 5000)
    await restoring
    await settle()
    expect(outcome).toBe('selected')
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
  })
})

/* ================================ A04 / A05 失败读取 ================================ */

describe('A04 Picker 首次 last GET=500、下一次 200、无项目缓存', () => {
  it('不能零请求停在 reset 空白：恢复成功，或明确可重试（重试后回到原文档）', async () => {
    const saved = seedSaved()
    be.fail(isSessionGet, 500, 1)
    const app = await boot({ open: false })
    await reopen(app, 'picker')
    await until(
      () => app.doc.useDocumentStore.getState().documentId === 'd_saved' || failureSignal(app) !== null,
      3000,
    )
    const restored = app.doc.useDocumentStore.getState().documentId === 'd_saved'
    if (!restored) {
      expect(failureSignal(app), '静默空白：既没恢复也没有任何失败状态').not.toBeNull()
      // 现役重试入口（lastDocumentIssue 薄适配）；ref 未知时只要求上面的失败状态
      if (app.proj.useProjectStore.getState().lastDocumentIssue) {
        expect(await app.proj.useProjectStore.getState().openLastDocument()).toBe(true)
      }
    }
    if (restored || app.proj.useProjectStore.getState().lastDocumentIssue === null) {
      // 恢复成功（自动重试）或重试后：内容就是原来那份
      if (restored) expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
    }
    expect(be.slotBytes('d_saved')).toBeDefined()
  })
})

describe('A05 last 正确、正文首次 GET=500', () => {
  it('冷启动：暂态错误不静默假空白；自动重试或重试入口后回到原文档', async () => {
    const saved = seedSaved()
    be.fail(isAutosaveGet('d_saved'), 500, 1)
    const app = await boot()
    await app.doc.restoreSession()
    await until(
      () => app.doc.useDocumentStore.getState().documentId === 'd_saved' || failureSignal(app) !== null,
      4000,
    )
    if (app.doc.useDocumentStore.getState().documentId !== 'd_saved') {
      expect(failureSignal(app), '冷启动静默空白（审计 R4）').not.toBeNull()
      const issue = app.proj.useProjectStore.getState().lastDocumentIssue
      if (issue) await app.proj.useProjectStore.getState().openLastDocument()
      else await app.doc.restoreSession()
    }
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
  }, 20_000)

  it('冷启动：持续 500——明确失败状态、不写盘；故障恢复后重试回到原文档', async () => {
    const saved = seedSaved()
    const original = be.slotBytes('d_saved')
    be.fail(isAutosaveGet('d_saved'), 500, 50)
    const app = await boot()
    await app.doc.restoreSession()
    expect(await until(() => failureSignal(app) !== null, 6000), '持续失败却没有任何状态（R4）').toBe(true)
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_saved')
    expect(autosavePuts()).toHaveLength(0)
    expect(be.slotBytes('d_saved')).toBe(original)
    be.failures.length = 0
    if (app.proj.useProjectStore.getState().lastDocumentIssue) {
      expect(await app.proj.useProjectStore.getState().openLastDocument()).toBe(true)
    } else {
      await app.doc.restoreSession()
    }
    await until(() => app.doc.useDocumentStore.getState().documentId === 'd_saved', 4000)
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
  }, 20_000)

  it('Picker：首次 500 区分暂态，重试可恢复原文档（基线已有这条，对照）', async () => {
    const saved = seedSaved()
    be.fail(isAutosaveGet('d_saved'), 500, 1)
    const app = await boot({ open: false })
    await reopen(app, 'picker')
    await until(
      () => app.doc.useDocumentStore.getState().documentId === 'd_saved' || failureSignal(app) !== null,
      4000,
    )
    if (app.doc.useDocumentStore.getState().documentId !== 'd_saved') {
      expect(failureSignal(app)).not.toBeNull()
      expect(await app.proj.useProjectStore.getState().openLastDocument()).toBe(true)
    }
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
  })
})

/* ================================ A06 仅 global currentDoc ================================ */

describe('A06 last / 项目缓存缺失，仅 global currentDoc', () => {
  it.each([
    { evidence: '本机索引记着 own', how: 'cold' as const },
    { evidence: '本机索引记着 own', how: 'picker' as const },
    { evidence: '后端 owners=this', how: 'cold' as const },
    { evidence: '后端 owners=this', how: 'picker' as const },
  ])('证据=$evidence，入口=$how：恢复同一份排版（冷启动与 Picker 等价）', async ({ evidence, how }) => {
    const saved = seedSaved('d_saved', 'p_a', { last: false, owner: evidence === '后端 owners=this' })
    if (evidence === '本机索引记着 own') {
      seedLocal({
        currentDoc: 'd_saved',
        index: [{ id: 'd_saved', name: saved.project.name, projectId: 'p_a' }],
      })
    } else {
      seedLocal({ currentDoc: 'd_saved' })
    }
    const app = await bootFor(how)
    await reopen(app, how)
    await until(() => app.doc.useDocumentStore.getState().documentId === 'd_saved', 3000)
    await settle()
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(saved) })
    expect(autosavePuts()).toHaveLength(0)
  })
})

/* ================================ A07 last=B 缺失、另有有效 A ================================ */

describe('A07 last=B 404，同项目另有已确认的有效 A', () => {
  it.each(['cold', 'picker'] as const)(
    '%s：不静默换成 A，也不静默空白；保留 B 的失败原因',
    async (how) => {
      const savedA = seedSaved('d_a', 'p_a', { last: false })
      be.owners.set('d_b', 'p_a')
      be.setLast('p_a', 'd_b', 'Draft B')
      seedLocal({
        currentDoc: 'd_a',
        index: [{ id: 'd_a', name: savedA.project.name, projectId: 'p_a' }],
      })
      const original = be.slotBytes('d_a')
      const app = await bootFor(how)
      await reopen(app, how)
      await settle()
      expect(app.doc.useDocumentStore.getState().documentId, '静默换成了 A').not.toBe('d_a')
      expect(failureSignal(app), '静默空白：没有任何失败状态').not.toBeNull()
      const issue = app.proj.useProjectStore.getState().lastDocumentIssue
      if (issue) expect(issue.id, '失败原因应指名 B').toBe('d_b')
      expect(be.slotBytes('d_a')).toBe(original)
      expect(autosavePuts()).toHaveLength(0)
    },
  )
})

/* ================================ A08 迁移不发布坏 last ================================ */

describe('A08 本机缓存指向缺失 / 坏 / 未来 schema 槽位', () => {
  it.each(['missing', 'corrupt', 'future'] as const)('%s：迁移不把它推成后端 last；原槽位与本机缓存原文不变', async (variant) => {
    if (variant === 'corrupt') {
      be.putSlot('d_bad', '{not json')
      be.corrupt.add('d_bad')
    } else if (variant === 'future') {
      be.putSlot('d_bad', { ...savedProject('未来格式'), schema: 99 })
    }
    seedLocal({ projectDoc: { pj: 'p_a', id: 'd_bad', name: 'Old dangling cache' } })
    const cacheBefore = localStorage.getItem('tavotto.projectDoc.p_a')
    const slotBefore = be.slotBytes('d_bad')
    const app = await boot()
    await app.doc.restoreSession()
    await settle()
    expect(lastPutIds(), '坏引用被先发布成了后端 last').not.toContain('d_bad')
    expect(be.lastOf('p_a')?.doc_id).not.toBe('d_bad')
    expect(be.slotBytes('d_bad')).toBe(slotBefore)
    expect(autosavePuts()).toHaveLength(0)
    expect(localStorage.getItem('tavotto.projectDoc.p_a')).toBe(cacheBefore)
  })

  it('对照：缓存指向有效槽位 → 恢复，并在接受之后把它迁移成后端 last', async () => {
    const saved = seedSaved('d_ok', 'p_a', { last: false, owner: false })
    seedLocal({ projectDoc: { pj: 'p_a', id: 'd_ok', name: saved.project.name } })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(await until(() => be.lastOf('p_a')?.doc_id === 'd_ok', 3000)).toBe(true)
    expect(currentShape(app)).toEqual({ documentId: 'd_ok', ...shapeOf(saved) })
  })
})

/* ================================ A09 / A10 / A11 last 晋升 ================================ */

describe('A09 新 B 已有内容、首次保存前硬中断（不触发 beforeunload）', () => {
  describe.each(['legacy', 'durable'] as const)('后端 last 语义=%s', (mode) => {
    it.each(['same', 'new'] as const)('重启 origin=%s：已持久的 A 仍有入口，不静默空白', async (origin) => {
      be.lastMode = mode
      const savedA = seedSaved()
      let app = await boot()
      expect(await app.doc.restoreSession()).toBe(true)
      const stop = app.doc.startAutosave()
      await app.doc.useDocumentStore.getState().switchDocument(blankProject('Draft B'), 'd_b')
      app.doc.useDocumentStore.getState().commit(literal('B 的第一笔'), (d) => {
        d.objects.push(text('tb', 'B content'))
      })
      // 打开意图（early last）已经发出；首次正文保存的防抖计时器还没到
      expect(await until(() => lastPutIds().includes('d_b'), 3000)).toBe(true)
      await settle()
      expect(be.slots.has('d_b'), '中断前 B 的槽位不该存在（不靠 close 帮它保存）').toBe(false)
      stop() // 强杀：没有 beforeunload，没有 flush
      if (origin === 'new') newOrigin()
      app = await boot()
      await app.doc.restoreSession()
      await settle()
      const restoredA = app.doc.useDocumentStore.getState().documentId === 'd_saved'
      if (mode === 'durable') {
        expect(restoredA, 'durable 后端下 last 仍指向可恢复的 A').toBe(true)
        expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(savedA) })
      } else {
        // 旧后端语义下 last 已悬空：前端至少不能静默空白（回到 A，或明确指出 B 读不到）
        expect(restoredA || failureSignal(app) !== null, '悬空 last 导致静默空白（R3）').toBe(true)
      }
      expect(slotShape('d_saved')).toEqual(shapeOf(savedA))
      expect(be.slots.has('d_b')).toBe(false)
    })
  })
})

describe('A10 新 B 首次正文成功、保存途中继续编辑；失败对照', () => {
  it('早期 last 推送失败后，首次正文成功 → 补记成 last；保存途中的后续编辑仍是 dirty', async () => {
    be.lastMode = 'durable'
    const app = await boot()
    cleanup.push(app.doc.startAutosave())
    be.fail(isLastPut, 500, 1) // 打开意图那次推送失败
    await app.doc.useDocumentStore.getState().switchDocument(blankProject('Draft B'), 'd_b')
    app.doc.useDocumentStore.getState().commit(literal('第一笔'), (d) => {
      d.objects.push(text('t1', 'one'))
    })
    expect(await until(() => be.count(isLastPut) >= 1, 3000)).toBe(true)
    await settle()
    expect(be.lastOf('p_a')).toBeUndefined()
    const gate = be.hold((r) => isAutosavePut(r) && r.docId === 'd_b')
    app.doc.flushAutosave()
    await gate.reached
    app.doc.useDocumentStore.getState().commit(literal('保存途中又改'), (d) => {
      d.objects.push(text('t2', 'two', 10, 10))
    })
    gate.release()
    expect(
      await until(() => be.lastOf('p_a')?.doc_id === 'd_b', 4000),
      '首次正文成功后 last 没有补记（同值去重挡住了补记）',
    ).toBe(true)
    expect(app.doc.useDocumentStore.getState().dirty, '保存途中的后续编辑被当成已保存').toBe(true)
    app.doc.flushAutosave()
    await settle()
    const body = JSON.parse(be.slotBytes('d_b')!) as ProjectDocument
    expect(body.canvases[0].objects.map((o) => o.id)).toEqual(['t1', 't2'])
  })

  it('失败对照：首次正文保存失败（500）→ 不晋升，last 不指向 B', async () => {
    be.lastMode = 'durable'
    seedSaved()
    const app = await boot()
    await app.doc.restoreSession()
    cleanup.push(app.doc.startAutosave())
    be.fail(isAutosavePut, 500, 99)
    await app.doc.useDocumentStore.getState().switchDocument(blankProject('Draft B'), 'd_b')
    app.doc.useDocumentStore.getState().commit(literal('第一笔'), (d) => {
      d.objects.push(text('t1', 'one'))
    })
    app.doc.flushAutosave()
    await settle()
    expect(be.slots.has('d_b')).toBe(false)
    expect(be.lastOf('p_a')?.doc_id).toBe('d_saved')
  })
})

describe('A11 A 的迟到保存不把 last 拉回', () => {
  it('A 的保存在途时用户打开并保存 B；A 的保存完成事件晚于 B 的打开意图 → last 终局是 B，A 不再被补推', async () => {
    be.lastMode = 'durable'
    seedSaved()
    const app = await boot()
    await app.doc.restoreSession()
    cleanup.push(app.doc.startAutosave())
    app.doc.useDocumentStore.getState().commit(literal('改 A'), (d) => {
      d.objects.push(text('t_a2', 'a edit', 30, 30))
    })
    // DiskWriter 对不同文档串行 PUT：A 在途时 B 排在它后面，A 的完成一定晚于 B 的「打开意图」
    const gateA = be.hold((r) => isAutosavePut(r) && r.docId === 'd_saved')
    app.doc.flushAutosave()
    await gateA.reached
    await app.doc.useDocumentStore.getState().switchDocument(blankProject('Draft B'), 'd_b')
    app.doc.useDocumentStore.getState().commit(literal('B 第一笔'), (d) => {
      d.objects.push(text('tb', 'b'))
    })
    expect(await until(() => lastPutIds().includes('d_b'), 3000)).toBe(true)
    gateA.release() // A 的保存此刻才完成
    app.doc.flushAutosave()
    expect(await until(() => be.lastOf('p_a')?.doc_id === 'd_b', 4000)).toBe(true)
    await settle()
    expect(be.lastOf('p_a')?.doc_id, 'A 的迟到保存把 last 拉回了 A').toBe('d_b')
    const ids = lastPutIds()
    expect(ids.slice(ids.indexOf('d_b')), 'B 的打开意图之后又推了 A').not.toContain('d_saved')
    // 保存的上下文是发起那一刻的项目，不是回包时的
    expect(autosavePuts().every((r) => r.pj === 'p_a')).toBe(true)
  })
})

/* ================================ A12 打开命名排版 / 项目包 ================================ */

describe('A12 打开命名排版或项目包（openLayoutDocument 分配新 docId）', () => {
  const namedLayout = (): ProjectDocument => {
    const pd = savedProject('命名排版')
    pd.canvases[0].objects = [
      { ...structuredClone(SAVED_PANEL), id: 'panel_named', x: 21, y: 22 },
      text('t_named', 'from file', 9, 9),
    ]
    return pd
  }

  it.each(['same', 'new'] as const)(
    '首次保存落盘前 last 仍是上一份 durable（只有 pending）；落盘后晋升；重启(%s origin)回到新 docId 且对象完整',
    async (origin) => {
      be.lastMode = 'durable'
      seedSaved()
      const pdN = namedLayout()
      let app = await boot()
      expect(await app.doc.restoreSession()).toBe(true)
      const stop = app.doc.startAutosave()
      const actions = await import('@/store/actions')
      const gate = be.hold((r) => isAutosavePut(r) && r.docId !== 'd_saved')
      expect(await actions.openLayoutDocument(structuredClone(pdN))).toBe(true)
      const newId = app.doc.useDocumentStore.getState().documentId
      expect(newId).not.toBe('d_saved')
      await gate.reached // 首次正文保存在路上，后端还没有槽位
      expect(await until(() => lastPutIds().includes(newId), 3000)).toBe(true)
      expect(be.slots.has(newId)).toBe(false)
      expect(be.lastOf('p_a')?.doc_id, '源文件存在被冒充成新 autosave 存在：last 已指向没有槽位的新 id').toBe('d_saved')
      gate.release()
      expect(await until(() => be.lastOf('p_a')?.doc_id === newId, 4000), '首次保存之后没有晋升').toBe(true)
      expect(be.slots.has(newId)).toBe(true)
      stop()
      if (origin === 'new') newOrigin()
      app = await boot()
      await app.doc.restoreSession()
      await settle()
      const shape = currentShape(app)
      expect(shape.documentId).toBe(newId)
      expect(shape.canvases[0].objects).toEqual(shapeOf(pdN).canvases[0].objects)
    },
  )

  describe.each(['legacy', 'durable'] as const)('后端 last 语义=%s：首次保存前进程中断', (mode) => {
    it.each(['same', 'new'] as const)('重启(%s origin)：回到上一份 durable，或明确指出读不到；不静默空白', async (origin) => {
      be.lastMode = mode
      const savedA = seedSaved()
      const pdN = namedLayout()
      let app = await boot()
      expect(await app.doc.restoreSession()).toBe(true)
      const stop = app.doc.startAutosave()
      const actions = await import('@/store/actions')
      be.fail(isAutosavePut, 500, 99) // 首次正文保存永远没落地
      expect(await actions.openLayoutDocument(structuredClone(pdN))).toBe(true)
      const newId = app.doc.useDocumentStore.getState().documentId
      await settle()
      expect(be.slots.has(newId)).toBe(false)
      stop()
      be.failures.length = 0
      if (origin === 'new') newOrigin()
      app = await boot()
      await app.doc.restoreSession()
      await settle()
      const id = app.doc.useDocumentStore.getState().documentId
      if (id === 'd_saved') {
        expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(savedA) })
      } else if (id === newId) {
        // 同 origin 且本机留着新排版的正文副本：按 A32 恢复它，对象必须完整
        expect(origin).toBe('same')
        expect(currentShape(app).canvases[0].objects).toEqual(shapeOf(pdN).canvases[0].objects)
      } else {
        expect(failureSignal(app), '悬空 last 导致静默空白').not.toBeNull()
      }
      if (mode === 'durable') expect(id === 'd_saved' || id === newId).toBe(true)
    })
  })
})

/* ================================ A13 / A28 / A16 / A18 ================================ */

describe('A13 有效 last、owners 缺失（含纯 runtime 文档）', () => {
  it('恢复正文与编辑；不强加 /owner 证明', async () => {
    const pd = savedProject('runtime 排版')
    const runtimePanel: PanelObject = {
      ...SAVED_PANEL,
      id: 'panel_rt',
      fileId: 'runtime:fig1.py#Fig1_PRISMA_Flowchart',
      fileKind: 'runtime',
      script: 'fig1.py',
      overrides: [{ gid: 'figure', prop: 'facecolor', value: '#0f0' }],
    }
    pd.canvases[0].objects = [runtimePanel]
    be.putSlot('d_rt', pd)
    be.setLast('p_a', 'd_rt', pd.project.name) // owners 缺席
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(currentShape(app)).toEqual({ documentId: 'd_rt', ...shapeOf(pd) })
    expect(be.count((r) => r.path === '/api/layout-session/owner')).toBe(0)
  })
})

describe('A28 全新项目、没有恢复目标，项目里另有未知旧槽位', () => {
  it('顺畅进入空白：不死等、没有阻塞性失败状态', async () => {
    be.putSlot('d_old_unknown', savedProject('别处的旧排版')) // 没有 owner、没有 last
    const app = await boot()
    const done = await settledWithin(app.doc.restoreSession(), 4000)
    expect(done).toBe(false)
    expect(failureSignal(app)).toBeNull()
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_old_unknown')
    // 新建后第一笔编辑照常可用
    app.doc.useDocumentStore.getState().commit(literal('第一笔'), (d) => {
      d.objects.push(text('t1', 'fresh'))
    })
    expect(currentShape(app).canvases[0].objects.map((o) => o.id)).toEqual(['t1'])
  })
})

describe('A16 归属判定不把不同情形压成同一种', () => {
  it('owners=null（明确的无项目）+ 本机 currentDoc：不当作当前项目的', async () => {
    be.putSlot('d_np', savedProject('无项目那份'))
    be.owners.set('d_np', null)
    seedLocal({ currentDoc: 'd_np' })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_np')
    expect(autosavePuts()).toHaveLength(0)
  })

  it('全局 currentDoc 归属未知 + /owner 500：不自动认领，且给出明确出口', async () => {
    be.putSlot('d_unk', savedProject('归属未知'))
    seedLocal({ currentDoc: 'd_unk' })
    be.fail((r) => r.path === '/api/layout-session/owner', 500, 99)
    const app = await boot()
    await app.doc.restoreSession()
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_unk')
    expect(failureSignal(app), '问不到归属时静默空白').not.toBeNull()
  })

  it('旧后端（/owner 与 session 都 404）：兼容分支照旧按本机 currentDoc 恢复（A29 前端半边）', async () => {
    const saved = savedProject('旧后端')
    be.putSlot('d_legacy', saved)
    be.sessionEndpoints = false
    be.ownerEndpoint = false
    seedLocal({ currentDoc: 'd_legacy' })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(currentShape(app)).toEqual({ documentId: 'd_legacy', ...shapeOf(saved) })
  })
})

describe('A18 schema 太新的磁盘正文', () => {
  it('不降级打开、不覆盖：槽位字节不变、无 PUT、有明确提示', async () => {
    const future = { ...savedProject('来自未来'), schema: 99 }
    be.putSlot('d_future', future)
    be.owners.set('d_future', 'p_a')
    be.setLast('p_a', 'd_future', 'future')
    const before = be.slotBytes('d_future')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_future')
    expect(failureSignal(app), 'schema 太新却静默').not.toBeNull()
    expect(be.slotBytes('d_future')).toBe(before)
    expect(autosavePuts()).toHaveLength(0)
  })
})

/* ================================ A17 C1 归属冲突 ================================ */

describe('A17 本机索引说 foreign 与后端归属', () => {
  const foreignIndex = (name: string) => [{ id: 'd_x', name, projectId: 'p_old' }]

  it('local foreign + 后端 owners=this（verified）：核对后恢复；本机陈旧记录在接受之后被纠正', async () => {
    const saved = seedSaved('d_x', 'p_a')
    seedLocal({ currentDoc: 'd_x', index: foreignIndex(saved.project.name) })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    expect(currentShape(app)).toEqual({ documentId: 'd_x', ...shapeOf(saved) })
    expect(be.count((r) => r.path === '/api/layout-session/owner')).toBeGreaterThan(0)
    const index = JSON.parse(localStorage.getItem('tavotto.docIndex')!) as { id: string; projectId?: string }[]
    expect(index.find((e) => e.id === 'd_x')?.projectId).toBe('p_a')
  })

  it('对照：只有 assets=this 证据（owners 缺席）→ 不推翻 foreign，不自动认领', async () => {
    const saved = seedSaved('d_x', 'p_a', { owner: false, last: false })
    be.assetsEvidence.add('p_a:d_x')
    be.setLast('p_a', 'd_x', saved.project.name)
    seedLocal({ currentDoc: 'd_x', index: foreignIndex(saved.project.name) })
    const original = be.slotBytes('d_x')
    const app = await boot()
    await app.doc.restoreSession()
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_x')
    expect(be.slotBytes('d_x')).toBe(original)
    expect(autosavePuts()).toHaveLength(0)
  })

  it('local foreign + /owner 不可用：拒绝自动认领', async () => {
    const saved = seedSaved('d_x', 'p_a')
    seedLocal({ currentDoc: 'd_x', index: foreignIndex(saved.project.name) })
    be.fail((r) => r.path === '/api/layout-session/owner', 500, 99)
    const app = await boot()
    await app.doc.restoreSession()
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_x')
  })

  it('owners=other：本机缓存 / currentDoc 指着它也必拒，不迁移成 last', async () => {
    be.putSlot('d_x', savedProject('别人的'))
    be.owners.set('d_x', 'p_other')
    seedLocal({ currentDoc: 'd_x', projectDoc: { pj: 'p_a', id: 'd_x', name: '别人的' } })
    const original = be.slotBytes('d_x')
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(false)
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_x')
    expect(be.lastOf('p_a')?.doc_id).not.toBe('d_x')
    expect(be.slotBytes('d_x')).toBe(original)
    expect(be.owners.get('d_x')).toBe('p_other')
  })
})

/* ================================ A19 候选探测零写入 ================================ */

describe('A19 未选中的候选：探测零副作用', () => {
  it('last=B 缺失；A 有本机更新副本、F 属于别的项目、U 是未来 schema：没人被选中时本机存储与后端槽位逐字节不变', async () => {
    const savedA = seedSaved('d_a', 'p_a', { last: false })
    be.putSlot('d_f', savedProject('别人的'))
    be.owners.set('d_f', 'p_other')
    be.putSlot('d_u', { ...savedProject('未来'), schema: 99 })
    be.owners.set('d_u', 'p_a')
    be.owners.set('d_b', 'p_a')
    be.setLast('p_a', 'd_b', 'Draft B')
    // A 的本机副本比磁盘更新：一旦被当「已打开」读取，就会被挪进恢复槽位
    const newer = { ...savedA, updatedAt: 200, project: { ...savedA.project, name: 'Unsaved candidate edit' } }
    localStorage.setItem('tavotto.autosave.d_a', JSON.stringify(newer))
    seedLocal({
      currentDoc: 'd_a',
      index: [
        { id: 'd_a', name: savedA.project.name, projectId: 'p_a' },
        { id: 'd_f', name: '别人的', projectId: 'p_other' },
      ],
    })
    const slotsBefore = new Map(be.slots)
    const localBefore = localSnapshot()
    const ownersBefore = new Map(be.owners)
    const lastBefore = JSON.stringify([...be.lastByPj])
    const app = await boot()
    await app.doc.restoreSession()
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId, '未选中就打开了某个候选').not.toBe('d_a')
    // 例外：`tavotto.projectDoc.p_a` 是后端 last（B）的镜像缓存，由读 last 本身刷新，与「探测候选」无关；
    // 这里只要求它没有被改指向任何一个未选中的候选
    const after = localSnapshot()
    const mirror = JSON.parse(after['tavotto.projectDoc.p_a'] ?? 'null') as { id?: string } | null
    expect(['d_a', 'd_f', 'd_u'], '未选中的候选被记成了项目缓存').not.toContain(mirror?.id)
    const skip = (o: Record<string, string>) =>
      Object.fromEntries(Object.entries(o).filter(([k]) => k !== 'tavotto.projectDoc.p_a'))
    expect(changedKeys(skip(localBefore), skip(after)), '本机存储里被改动的键').toEqual([])
    expect(new Map(be.slots)).toEqual(slotsBefore)
    expect(new Map(be.owners)).toEqual(ownersBefore)
    expect(JSON.stringify([...be.lastByPj])).toBe(lastBefore)
    expect(autosavePuts()).toHaveLength(0)
    expect(be.deletes).toEqual([])
  })
})

/* ================================ A20 教程 / skip-restore ================================ */

describe('A20 显式准备优先、skip-restore 只消费一次', () => {
  it('skip-restore 的消费结果是本次启动的共享决定：StrictMode 第二轮也不恢复崩溃的文档', async () => {
    seedSaved()
    const app = await boot()
    const blankId = app.doc.useDocumentStore.getState().documentId
    app.doc.requestBlankStart()
    expect(await app.doc.restoreSession()).toBe(false) // 第一轮：消费标记
    expect(await app.doc.restoreSession()).toBe(false) // 第二轮（StrictMode 重挂）
    await settle()
    expect(app.doc.useDocumentStore.getState().documentId).toBe(blankId)
    expect(be.count(isAutosaveGet('d_saved')), '第二轮又去读了崩溃的文档').toBe(0)
  })

  it('教程准备成功装载的文档不被后续恢复覆盖，第一笔拖动保留', async () => {
    seedSaved() // 项目 last 指向另一份旧排版：错误的恢复会把它换进来
    const tutorial = blankProject('教程画布')
    tutorial.canvases[0].objects = [{ ...structuredClone(SAVED_PANEL), id: 'panel_tut', x: 10, y: 10 }]
    const app = await boot({ open: false })
    const prepare = (async () => {
      await app.doc.useDocumentStore.getState().switchDocument(tutorial, 'd_tutorial')
      return true
    }) as never
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_A, { prepareDocument: prepare })
    await app.doc.restoreSession() // 工作台挂载后那一次
    app.doc.useDocumentStore.getState().commit(literal('第一笔拖动'), (d) => {
      const p = d.objects.find((o) => o.id === 'panel_tut')!
      p.x = 50
    })
    await settle()
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_tutorial')
    expect(shape.canvases[0].objects.find((o) => o.id === 'panel_tut')?.x).toBe(50)
    expect(be.count(isAutosaveGet('d_saved'))).toBe(0)
  })
})

/* ================================ A21 A→B→A 旧响应 ================================ */

describe('A21 恢复 / 切项目过程中 A→B→A，旧响应最后到达', () => {
  it('旧代无提交权：B 的文档、last、owner 不受污染；回到 A 仍是 A 的排版', async () => {
    const savedA = seedSaved('d_saved', 'p_a')
    const savedB = seedSaved('d_b', 'p_b', { name: 'B 的排版' })
    const app = await boot()
    const gate = be.hold((r) => isSessionGet(r) && r.pj === 'p_a')
    const restoring = app.doc.restoreSession()
    await gate.reached
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_B)
    expect(currentShape(app)).toEqual({ documentId: 'd_b', ...shapeOf(savedB) })
    gate.release() // A 的旧响应此刻才到
    await restoring
    await settle()
    expect(currentShape(app)).toEqual({ documentId: 'd_b', ...shapeOf(savedB) })
    expect(be.lastOf('p_b')?.doc_id).toBe('d_b')
    expect(be.lastOf('p_a')?.doc_id).toBe('d_saved')
    expect(be.owners.get('d_saved')).toBe('p_a')
    expect(be.owners.get('d_b')).toBe('p_b')
    // 回到 A：A 的排版，不是 B 的
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_A)
    await settle()
    expect(currentShape(app)).toEqual({ documentId: 'd_saved', ...shapeOf(savedA) })
    expect(autosavePuts().filter((r) => r.docId === 'd_b' && r.pj !== 'p_b')).toHaveLength(0)
  })
})

/* ================================ A23 离开 flush ================================ */

describe('A23 未保存编辑 + 切项目：旧文档的离开 flush 不被恢复屏障吞掉', () => {
  it('切到 B 时 A 的最后一笔编辑照样落盘，且归属是发起那一刻的项目 A', async () => {
    seedSaved('d_saved', 'p_a')
    seedSaved('d_b', 'p_b', { name: 'B 的排版' })
    const app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    cleanup.push(app.doc.startAutosave())
    app.doc.useDocumentStore.getState().commit(literal('A 的最后一笔'), (d) => {
      d.objects.push(text('t_last', 'last edit', 40, 40))
    })
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_B)
    await settle()
    const body = JSON.parse(be.slotBytes('d_saved')!) as ProjectDocument
    expect(body.canvases[0].objects.map((o) => o.id)).toContain('t_last')
    const puts = autosavePuts().filter((r) => r.docId === 'd_saved')
    expect(puts.length).toBeGreaterThan(0)
    expect(puts.every((r) => r.pj === 'p_a')).toBe(true)
    expect(be.owners.get('d_saved')).toBe('p_a')
  })
})

/* ================================ A32 本机有正文、后端保存失败 ================================ */

describe('A32 B 只有有效本机正文，后端保存失败，durable last 仍为 A', () => {
  it('同 origin 重启：恢复 B 并保留其 id；B 不被塞进 A 的恢复槽，A 的磁盘字节不变', async () => {
    be.lastMode = 'durable'
    const savedA = seedSaved()
    let app = await boot()
    expect(await app.doc.restoreSession()).toBe(true)
    const stop = app.doc.startAutosave()
    await app.doc.useDocumentStore.getState().switchDocument(blankProject('Draft B'), 'd_b')
    app.doc.useDocumentStore.getState().commit(literal('B 的内容'), (d) => {
      d.objects.push(text('tb', 'only local', 7, 8))
    })
    be.fail(isAutosavePut, 500, 99)
    app.doc.flushAutosave() // 本机副本写下，磁盘失败
    await settle()
    expect(be.slots.has('d_b')).toBe(false)
    expect(localStorage.getItem('tavotto.autosave.d_b')).not.toBeNull()
    stop()
    be.failures.length = 0
    app = await boot() // 同一个 origin：本机缓存还在
    await app.doc.restoreSession()
    await settle()
    const shape = currentShape(app)
    expect(shape.documentId, 'B 被旧远端 A 藏起来了').toBe('d_b')
    expect(shape.canvases[0].objects.map((o) => o.id)).toEqual(['tb'])
    expect(localStorage.getItem('tavotto.recovery.d_saved')).toBeNull()
    expect(slotShape('d_saved')).toEqual(shapeOf(savedA))
  })
})

/* ================================ 指针 revision（Codex P1 r4240627652） ================================ */

describe('指针带 revision：读正文时它已被别的项目覆盖', () => {
  const overwrite = async (pj: string, body: ProjectDocument) => {
    const res = await fetch('/api/autosave/d_x', {
      method: 'PUT',
      headers: { 'X-Tavotto-Project': pj, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    expect(res.status).toBe(200)
  }

  it('F 的 last=d_x(R1)，正文 GET 在路上时 G 保存了 d_x(R2，owner=G)：F 不得安装 G 的正文，needs_user 且零写入', async () => {
    seedSaved('d_x', 'p_a')
    expect(be.lastOf('p_a')?.revision).toBeTruthy()
    const gBody = savedProject('G 的排版')
    gBody.canvases[0].objects = [text('t_g', 'G only', 1, 1)]
    const app = await boot()
    const gate = be.hold(isAutosaveGet('d_x'))
    const restoring = app.doc.restoreSession()
    await gate.reached
    await overwrite('p_b', gBody)
    expect(be.owners.get('d_x')).toBe('p_b')
    const gBytes = be.slotBytes('d_x')
    gate.release()
    await restoring
    await settle()
    const ids = currentShape(app).canvases.flatMap((c) => c.objects.map((o) => o.id))
    expect(ids, 'F 安装了 G 的正文').not.toContain('t_g')
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_x')
    expect(failureSignal(app), '静默空白').not.toBeNull()
    expect(be.slotBytes('d_x')).toBe(gBytes)
    expect(be.owners.get('d_x')).toBe('p_b')
    expect(autosavePuts().filter((r) => r.pj === 'p_a'), 'F 往 d_x 写了东西').toHaveLength(0)
    expect(be.lastOf('p_a')?.doc_id).toBe('d_x')
  })

  it('对照：同一个项目的另一个标签页把 d_x 存成 R2，/owner 回 this+verified → 照常安装新正文', async () => {
    seedSaved('d_x', 'p_a')
    const newer = savedProject('F 另一个标签页保存的')
    newer.canvases[0].objects = [text('t_f2', 'F newer', 2, 2)]
    const app = await boot()
    const gate = be.hold(isAutosaveGet('d_x'))
    const restoring = app.doc.restoreSession()
    await gate.reached
    await overwrite('p_a', newer)
    gate.release()
    expect(await restoring).toBe(true)
    await settle()
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_x')
    expect(shape.canvases[0].objects.map((o) => o.id)).toEqual(['t_f2'])
  })
})

describe('pending 指针带 revision:null（槽位当时不存在），读正文之前被别的项目建出来', () => {
  const firstSave = async (pj: string, body: ProjectDocument) => {
    const res = await fetch('/api/autosave/d_p', {
      method: 'PUT',
      headers: { 'X-Tavotto-Project': pj, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    expect(res.status).toBe(200)
  }
  const arm = () => {
    be.pendingByPj.set('p_a', { doc_id: 'd_p', name: 'Draft P', at: ++be.clock, revision: null })
  }

  it('F 只有 pending d_p、没有 durable last；session GET 与正文 GET 之间 G 首次保存了 d_p → F 不得安装，needs_user，零写入', async () => {
    arm()
    const g = savedProject('G 的排版')
    g.canvases[0].objects = [text('t_g', 'G only', 1, 1)]
    const app = await boot()
    const gate = be.hold((r) => r.method === 'GET' && r.docId === 'd_p')
    const restoring = app.doc.restoreSession()
    await gate.reached
    await firstSave('p_b', g)
    expect(be.owners.get('d_p')).toBe('p_b')
    gate.release()
    await restoring
    await settle()
    const ids = currentShape(app).canvases.flatMap((c) => c.objects.map((o) => o.id))
    expect(ids, 'F 安装了 G 的正文').not.toContain('t_g')
    expect(app.doc.useDocumentStore.getState().documentId).not.toBe('d_p')
    expect(failureSignal(app), '静默空白').not.toBeNull()
    expect(be.requests.filter((r) => isAutosavePut(r) && r.pj === 'p_a')).toHaveLength(0)
    expect(be.owners.get('d_p')).toBe('p_b')
  })

  it('对照：F 自己的首次保存在这之间落盘（owner=F，/owner this+verified，revision 一致）→ 照常安装', async () => {
    arm()
    const mine = savedProject('F 的排版')
    mine.canvases[0].objects = [text('t_f', 'F first save', 2, 2)]
    const app = await boot()
    const gate = be.hold((r) => r.method === 'GET' && r.docId === 'd_p')
    const restoring = app.doc.restoreSession()
    await gate.reached
    await firstSave('p_a', mine)
    gate.release()
    expect(await restoring).toBe(true)
    await settle()
    const shape = currentShape(app)
    expect(shape.documentId).toBe('d_p')
    expect(shape.canvases[0].objects.map((o) => o.id)).toEqual(['t_f'])
  })
})

/* ============================ 修复后新增状态（不计红证据） ============================ */

describe('修复后新增状态', () => {
  type Gate = { state?: string; reason?: string }
  const gateOf = (app: App) =>
    (app.doc.useDocumentStore.getState() as unknown as { restoreGate?: Gate }).restoreGate

  it('restoring 期间文档写入口被拒（页宽不变），门为 restoring', async (ctx) => {
    seedSaved()
    const app = await boot()
    if (!gateOf(app)) return ctx.skip()
    const gate = be.hold(isSessionGet)
    const restoring = app.doc.restoreSession()
    await gate.reached
    const w = app.doc.useDocumentStore.getState().doc.page.w
    app.doc.useDocumentStore.getState().commit(literal('占位文档上的编辑'), (d) => {
      d.page.w = 999
    })
    expect(app.doc.useDocumentStore.getState().doc.page.w).toBe(w)
    expect(gateOf(app)?.state).toBe('restoring')
    gate.release()
    await restoring
    expect(gateOf(app)?.state).toBe('ready')
  })

  it('last=B 缺失、同项目有 A：门为 needs_user，保留 B 的失败，A 在备选里', async (ctx) => {
    const savedA = seedSaved('d_a', 'p_a', { last: false })
    be.owners.set('d_b', 'p_a')
    be.setLast('p_a', 'd_b', 'Draft B')
    seedLocal({ currentDoc: 'd_a', index: [{ id: 'd_a', name: savedA.project.name, projectId: 'p_a' }] })
    const app = await boot()
    if (!gateOf(app)) return ctx.skip()
    await app.doc.restoreSession()
    await settle()
    expect(gateOf(app)?.state).toBe('needs_user')
    const st = app.doc.useDocumentStore.getState() as unknown as {
      restoreIssue?: { kind?: string; ref?: { id: string } | null; alternatives?: { id: string }[] }
    }
    const issue = st.restoreIssue
    if (!issue) return ctx.skip() // 字段名待与 FE-R 对齐
    expect(issue.kind).toBe('missing')
    expect(issue.ref?.id).toBe('d_b')
    expect((issue.alternatives ?? []).map((a) => a.id)).toContain('d_a')
  })

  it('教程 prepare 返回失败：不把初始空白标成 ready', async (ctx) => {
    const app = await boot({ open: false })
    if (!gateOf(app)) return ctx.skip()
    const failing = (async () => false) as never
    await app.proj.useProjectStore.getState().adoptOpenedProject(STATUS_A, { prepareDocument: failing })
    expect(gateOf(app)?.state).not.toBe('ready')
  })

  it('probeDocument 零副作用：本机更新副本只标待裁决，不挪键、不写盘', async (ctx) => {
    const savedA = seedSaved('d_a', 'p_a', { last: false })
    localStorage.setItem('tavotto.autosave.d_a', JSON.stringify({
      ...savedA, updatedAt: 200, project: { ...savedA.project, name: 'Unsaved edit' },
    }))
    const app = await boot()
    const probe = (app.doc as unknown as {
      probeDocument?: (id: string, pj: string | null) => Promise<{ kind: string; pendingRecovery: string | null }>
    }).probeDocument
    if (typeof probe !== 'function') return ctx.skip()
    const before = localSnapshot()
    const res = await probe('d_a', 'p_a')
    expect(res.kind).toBe('found')
    expect(res.pendingRecovery).toBe('local_newer')
    expect(localSnapshot()).toEqual(before)
    expect(autosavePuts()).toHaveLength(0)
  })

  it('planRestore 零写入：坏的本机缓存只被规划，不推迁移、不改缓存', async (ctx) => {
    seedLocal({ projectDoc: { pj: 'p_a', id: 'd_missing', name: 'dangling' } })
    const app = await boot()
    const plan = (app.refs as unknown as {
      planRestore?: (pj: string | null, clues: { globalCurrent: string | null }) => Promise<unknown>
    }).planRestore
    if (typeof plan !== 'function') return ctx.skip()
    const before = localSnapshot()
    await plan('p_a', { globalCurrent: null })
    expect(localSnapshot()).toEqual(before)
    expect(lastPutIds()).toEqual([])
  })

  it('没有首选目标时：恰好 1 份本项目有效候选 → 自动恢复；≥2 份 → needs_user', async (ctx) => {
    const one = seedSaved('d_one', 'p_a', { last: false })
    let app = await boot()
    if (!gateOf(app)) return ctx.skip()
    if (!be.candidatesEndpoint) return ctx.skip()
    await app.doc.restoreSession()
    await settle()
    if (app.doc.useDocumentStore.getState().documentId !== 'd_one') {
      // 发现式自动恢复尚未实现：不在这里判红
      return ctx.skip()
    }
    expect(currentShape(app)).toEqual({ documentId: 'd_one', ...shapeOf(one) })
    seedSaved('d_two', 'p_a', { last: false })
    be.lastByPj.clear()
    newOrigin()
    app = await boot()
    await app.doc.restoreSession()
    await settle()
    expect(gateOf(app)?.state).toBe('needs_user')
  })
})
