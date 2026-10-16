/**
 * 外部交接与文档恢复的时序（CONTRACT 2.5）：落图 / Figure 选择器 / runtime 落地之前等「这个项目的文档」恢复落定；
 * native 接收 / 确认**不等**文档。真实的 documentStore / projectStore / openRequest / assetStore，只换 fetch。
 *
 * 验收：A03（handoff 分别在 last GET / autosave GET 期间到达）、A22（交接终局）、A33（native 不堵）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import { emptyProject, type PanelObject, type ProjectDocument } from '@/types/document'

const be = createFakeBackend()

const oldPanel: PanelObject = {
  id: 'panel_saved', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf',
  nativeW: 80, nativeH: 60, x: 13, y: 24, w: 80, h: 60,
  script: 'fig1.py', overrides: [{ gid: 'figure', prop: 'facecolor', value: '#f00' }],
  figureFrame: 1,
}
const fig1Info = {
  id: 'Fig1.pdf', name: 'Fig1', folder: '.', kind: 'pdf',
  native_w_mm: 80, native_h_mm: 60, mtime: 1, script: 'fig1.py',
}

const fetchImpl = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://127.0.0.1')
  if (u.pathname === '/api/project') {
    return new Response(JSON.stringify({ open: true, id: 'p_a', figures_dir: '/figs/a', name: 'a' }), { status: 200 })
  }
  if (u.pathname === '/api/panels') {
    return new Response(JSON.stringify({ panels: [fig1Info], figures_dir: '/figs/a' }), { status: 200 })
  }
  if (u.pathname === '/api/runtime/assets') return new Response(JSON.stringify({ assets: [] }), { status: 200 })
  if (u.pathname.startsWith('/api/projects')) {
    return new Response(JSON.stringify({ recent: [], pinned: [], projects: [] }), { status: 200 })
  }
  return be.fetch(url as RequestInfo, init)
}) as typeof fetch

function seedSaved() {
  const pd: ProjectDocument = emptyProject()
  pd.project.name = '用户已修改的排版'
  pd.canvases[0].objects = [structuredClone(oldPanel)]
  pd.updatedAt = 100
  be.putSlot('d_saved', pd, 'p_a')
  be.setLast('p_a', 'd_saved', pd.project.name)
}

async function boot() {
  vi.resetModules()
  const session = await import('@/lib/session')
  session.setCurrentProjectId('p_a')
  const doc = await import('@/store/documentStore')
  const restore = await import('@/store/documentRestore')
  const proj = await import('@/store/projectStore')
  const handoff = await import('@/lib/openRequest')
  const native = await import('@/store/nativeSessionStore')
  const ui = await import('@/store/uiStore')
  const picker = await import('@/store/figurePickerStore')
  restore.setRestoreRetryDelays([0, 0])
  proj.useProjectStore.setState({
    phase: 'open', project: { open: true, id: 'p_a', figures_dir: '/figs/a' }, lastDocumentIssue: null,
  })
  return { doc, restore, proj, handoff, native, ui, picker }
}
type App = Awaited<ReturnType<typeof boot>>

const gate = () => {
  let release!: () => void
  const promise = new Promise<void>((r) => (release = r))
  return { promise, release }
}
const panelsOf = (app: Pick<App, 'doc'>) =>
  app.doc.useDocumentStore.getState().doc.objects.filter((o): o is PanelObject => o.type === 'panel')
const statusKey = (app: App) => (app.ui.useUiStore.getState().status as { key?: string } | null)?.key

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  be.reset()
  vi.stubGlobal('fetch', fetchImpl)
})
afterEach(() => vi.unstubAllGlobals())

describe('A03：交接落图等文档恢复', () => {
  for (const phase of ['session', 'autosaveGet'] as const) {
    it(`handoff 在 ${phase === 'session' ? 'last GET' : '正文 GET'} 期间到达：恢复完成后选中原面板，保留原 overrides，不重复 placed`, async () => {
      seedSaved()
      const app = await boot()
      const hold = gate()
      be.state.hold[phase] = hold.promise
      const restoring = app.doc.restoreSession()
      await vi.waitFor(() =>
        expect(be.state.calls).toContain(phase === 'session' ? 'GET session' : 'GET autosave d_saved'),
      )
      const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
      // 交接在等，没有抢走初始空白
      let settled = false
      void handoff.then(() => (settled = true))
      await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
      expect(settled).toBe(false)
      expect(panelsOf(app)).toEqual([])

      hold.release()
      expect(await restoring).toBe(true)
      expect(await handoff).toBe('selected')
      const st = app.doc.useDocumentStore.getState()
      expect(st.documentId).toBe('d_saved')
      expect(panelsOf(app)).toHaveLength(1)
      expect(panelsOf(app)[0]).toMatchObject({
        id: 'panel_saved', x: 13, y: 24,
        overrides: [{ gid: 'figure', prop: 'facecolor', value: '#f00' }],
      })
    })
  }

  it('恢复已落定后的交接（对照）：照常选中，不等', async () => {
    seedSaved()
    const app = await boot()
    await app.doc.restoreSession()
    expect(await app.handoff.applyOpenRequest({ stem: 'Fig1' })).toBe('selected')
  })

  it('没有历史的项目：恢复确认为空后，新图照常放进来（正常新图交接仍能完成）', async () => {
    const app = await boot()
    const restoring = app.doc.restoreSession()
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    await restoring
    expect(await handoff).toBe('placed')
    expect(panelsOf(app)).toHaveLength(1)
  })

  it('多 Figure 交接（pick）同样等恢复落定才打开选择器', async () => {
    seedSaved()
    const app = await boot()
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    const handoff = app.handoff.applyOpenRequest({ pick: 'plots/fig.py' })
    await Promise.resolve()
    expect(app.picker.useFigurePickerStore.getState().script).toBeNull()
    hold.release()
    await restoring
    expect(await handoff).toBe('picker')
    expect(app.picker.useFigurePickerStore.getState().script).toBe('plots/fig.py')
  })
})

describe('A22 / A33：终局与 native', () => {
  it('A33：native 接收 / 确认不等文档——恢复卡住时 receive 立刻被调；实际加图等恢复', async () => {
    seedSaved()
    const app = await boot()
    const receive = vi.fn(async () => {})
    app.native.useNativeSessionStore.setState({ receive } as never)
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1', native: 'n_1' })
    await vi.waitFor(() => expect(receive).toHaveBeenCalledWith('n_1'))
    expect(panelsOf(app)).toEqual([])
    hold.release()
    await restoring
    expect(await handoff).toBe('selected')
    expect(receive).toHaveBeenCalledTimes(1)
  })

  it('只有 native（stem 缺省）：不等文档，直接 native-pending', async () => {
    seedSaved()
    const app = await boot()
    app.native.useNativeSessionStore.setState({ receive: vi.fn(async () => {}) } as never)
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    expect(await app.handoff.applyOpenRequest({ native: 'n_1' })).toBe('native-pending')
    hold.release()
    await restoring
  })

  it('等待期间项目被切走 → 终局 deferred，说「项目已切换，这次交接没有放入」，不往新项目放图', async () => {
    seedSaved()
    const app = await boot()
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    // 切项目的认领自己的 last GET 也卡在同一个 gate 上：先让它开始（门换了代、换成 p_b），再放行
    const adopting = app.proj.useProjectStore
      .getState()
      .adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    await vi.waitFor(() => expect(app.doc.getRestoreGate().pj).toBe('p_b'))
    expect(await handoff).toBe('deferred')
    hold.release()
    await restoring
    await adopting
    expect(statusKey(app)).toBe('handoff.superseded')
    expect(panelsOf(app)).toEqual([])
  })

  it('上次的排版一直没处理（needs_user）→ 等满上限给终局 deferred，说一句「图在素材库里」；不永久阻塞', async () => {
    seedSaved()
    be.state.failGetDoc.set('d_saved', 500)
    const app = await boot()
    await app.doc.restoreSession()
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
      await vi.advanceTimersByTimeAsync(app.doc.DOCUMENT_READY_TIMEOUT_MS + 1)
      expect(await handoff).toBe('deferred')
      expect(statusKey(app)).toBe('handoff.restoreTimeout')
      expect(panelsOf(app)).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })

  it('URL 交接整个页面生命周期只认一次：StrictMode 双挂载不重放，也不丢', async () => {
    const app = await boot()
    window.history.replaceState(null, '', '/?open=Fig1&native=n_9')
    app.handoff.resetUrlOpenRequestClaimForTests()
    const first = app.handoff.claimUrlOpenRequest()
    const second = app.handoff.claimUrlOpenRequest()
    expect(first).toEqual({ stem: 'Fig1', native: 'n_9' })
    expect(second).toBeNull()
    expect(window.location.search).toBe('')
  })

  it('不按脚本名去重：同一个脚本的两次不同交接各自独立落地', async () => {
    const app = await boot()
    await app.doc.restoreSession()
    expect(await app.handoff.applyOpenRequest({ stem: 'Fig1' })).toBe('placed')
    expect(await app.handoff.applyOpenRequest({ stem: 'Fig1' })).toBe('selected')
    expect(panelsOf(app)).toHaveLength(1)
  })
})

describe('用户级入口在恢复中排队（素材卡双击 / 加入排版 / 拖放 / 粘贴）', () => {
  const setAssets = (app: App) =>
    import('@/store/assetStore').then(({ useAssetStore }) =>
      useAssetStore.setState({ panels: [fig1Info as never], byId: { 'Fig1.pdf': fig1Info as never }, loaded: true }),
    ).then(() => app)

  it('恢复卡在 last GET，用户双击素材卡 / 点加入排版：放行后面板进恢复出来的文档，恰好一次', async () => {
    seedSaved()
    const app = await setAssets(await boot())
    const ws = await import('@/store/workspace')
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    expect(ws.openFastEdit('Fig1.pdf')).toBe('deferred')
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    expect(panelsOf(app)).toEqual([])
    hold.release()
    await restoring
    await vi.waitFor(() => expect(app.doc.useDocumentStore.getState().documentId).toBe('d_saved'))
    await vi.waitFor(() => expect(app.ui.useUiStore.getState().status).not.toBeNull())
    // 文档里本来就有这张图：两个排队的动作都只是聚焦，没有叠出第二个
    expect(panelsOf(app)).toHaveLength(1)
    expect(panelsOf(app)[0].id).toBe('panel_saved')
  })

  it('新项目（恢复确认没有历史）：排队的加图放进新的空白文档，恰好一次', async () => {
    const app = await setAssets(await boot())
    const ws = await import('@/store/workspace')
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    hold.release()
    await restoring
    await vi.waitFor(() => expect(panelsOf(app)).toHaveLength(1))
    await new Promise((r) => setTimeout(r, 20))
    expect(panelsOf(app)).toHaveLength(1)
  })

  it('自动进入编辑传的守卫：恢复期间排队，落定时守卫不满足 → 入口不执行（不放图、不进编辑）', async () => {
    const app = await setAssets(await boot())
    const ws = await import('@/store/workspace')
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    let wanted = true
    expect(ws.openFastEdit('Fig1.pdf', { guard: () => wanted })).toBe('deferred')
    wanted = false // 用户在恢复期间把卡放到后台 / 切去别的卡
    hold.release()
    await restoring
    await vi.waitFor(() => expect(app.doc.getRestoreGate().state).toBe('ready'))
    await new Promise((r) => setTimeout(r, 30))
    expect(panelsOf(app)).toEqual([])
  })

  it('自动进入编辑（enterEdit）在恢复中排队、图还不在文档里：落定前切走 → 不插入面板、不进编辑、不记编辑', async () => {
    const app = await setAssets(await boot())
    const { enterEdit } = await import('@/store/prepEnterEdit')
    const desc = {
      asset_id: 'runtime:s.py#a', script: 's.py', entry: '__main__', stem: 'a', capture_source: 'savefig', execution_profile: 'safe',
      original_artifact: null, size_mm: [80, 60], source_fingerprint: 'fp', can_writeback_artifact: false, can_writeback_source: false,
    } as never
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    let wanted = true
    const noted: string[] = []
    const out = await enterEdit({ epoch: () => 0, note: (id) => noted.push(id), stillWanted: () => wanted }, desc)
    expect(out).toBe('deferred')
    wanted = false // 恢复期间用户把卡放到后台 / 切去别的卡
    hold.release()
    await restoring
    await vi.waitFor(() => expect(app.doc.getRestoreGate().state).toBe('ready'))
    await new Promise((r) => setTimeout(r, 30))
    expect(panelsOf(app)).toEqual([]) // 没插入面板（取景跟着插入走，也就没有）
    expect(noted).toEqual([]) // 没进编辑、没记编辑
  })

  it('拖放 / 选图对话框的加图（addPanelToCanvas）同样排队，返回 null', async () => {
    const app = await setAssets(await boot())
    const ws = await import('@/store/workspace')
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    expect(ws.addPanelToCanvas(fig1Info as never, 10, 10)).toBeNull()
    hold.release()
    await restoring
    await vi.waitFor(() => expect(panelsOf(app)).toHaveLength(1))
  })

  it('needs_user：直接拒掉并说一句，不排队——用户选了出口之后也不会补一个', async () => {
    seedSaved()
    be.state.failGetDoc.set('d_saved', 500)
    const app = await setAssets(await boot())
    const ws = await import('@/store/workspace')
    await app.doc.restoreSession()
    expect(app.doc.getRestoreGate().state).toBe('needs_user')
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('blocked')
    expect(ws.openFastEdit('Fig1.pdf')).toBe('blocked')
    expect(statusKey(app)).toBe('restore.blockedWrite')
    await app.restore.continueNew()
    await new Promise((r) => setTimeout(r, 20))
    expect(panelsOf(app)).toEqual([])
  })

  it('排队期间项目被切走：丢掉并说一句，不往新项目放', async () => {
    seedSaved()
    const app = await setAssets(await boot())
    const ws = await import('@/store/workspace')
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    const adopting = app.proj.useProjectStore.getState().adoptOpenedProject({ open: true, id: 'p_b', figures_dir: '/figs/b' })
    await vi.waitFor(() => expect(statusKey(app)).toBe('restore.entrySuperseded'))
    hold.release()
    await restoring
    await adopting
    expect(panelsOf(app)).toEqual([])
  })
})

describe('排队的入口只在**同一代**恢复落定时执行', () => {
  it.each([
    ['显式新建', async () => (await import('@/store/actions')).newBlankDocument()],
    ['继续/打开另一个排版', async (app: App) => { await app.doc.useDocumentStore.getState().switchDocument(emptyProject(), 'd_other') }],
  ])('排队后被%s取代：旧入口丢掉并说一句，不写进新文档', async (_n, intent) => {
    seedSaved()
    const app = await (async () => {
      const a = await boot()
      const { useAssetStore } = await import('@/store/assetStore')
      useAssetStore.setState({ panels: [fig1Info as never], byId: { 'Fig1.pdf': fig1Info as never }, loaded: true })
      return a
    })()
    const ws = await import('@/store/workspace')
    const hold = gate()
    be.state.hold.autosaveGet = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET autosave d_saved'))
    expect(ws.addFigureToLayout('Fig1.pdf')).toBe('deferred')
    const keys: string[] = []
    const unsub = app.ui.useUiStore.subscribe((st) => {
      const k = (st.status as { key?: string } | null)?.key
      if (k) keys.push(k)
    })
    await intent(app)
    await vi.waitFor(() => expect(keys).toContain('restore.entrySuperseded'))
    unsub()
    hold.release()
    await restoring
    expect(panelsOf(app)).toEqual([])
  })
})

describe('init 先关门再发布 phase:open（Codex P1）', () => {
  async function bootCold() {
    vi.resetModules()
    const session = await import('@/lib/session')
    session.setCurrentProjectId('p_a')
    const doc = await import('@/store/documentStore')
    const restore = await import('@/store/documentRestore')
    const proj = await import('@/store/projectStore')
    const handoff = await import('@/lib/openRequest')
    const assets = await import('@/store/assetStore')
    restore.setRestoreRetryDelays([0, 0])
    assets.useAssetStore.setState({ panels: [fig1Info as never], byId: { 'Fig1.pdf': fig1Info as never }, loaded: true })
    return { doc, restore, proj, handoff }
  }

  it('init() 一发布 phase:open 门就已经关着；restoreSession 前到的交接等着，落进恢复出来的文档，不落进空白', async () => {
    seedSaved()
    const app = await bootCold()
    await app.proj.useProjectStore.getState().init()
    expect(app.proj.useProjectStore.getState().phase).toBe('open')
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'restoring', pj: 'p_a' })
    const gen = app.doc.getRestoreGate().gen
    // 桌面 tavotto:open 在 Workspace 挂载（restoreSession）之前到达
    const handoff = app.handoff.applyOpenRequest({ stem: 'Fig1' })
    await Promise.resolve()
    expect(panelsOf(app)).toEqual([])
    // Workspace 挂载：接过 init 已经开始的那一轮，不开第二代
    expect(await app.doc.restoreSession()).toBe(true)
    expect(app.doc.getRestoreGate().gen).toBe(gen)
    expect(await handoff).toBe('selected')
    expect(app.doc.useDocumentStore.getState().documentId).toBe('d_saved')
    expect(panelsOf(app)).toHaveLength(1)
    expect(panelsOf(app)[0]).toMatchObject({ id: 'panel_saved', overrides: [{ gid: 'figure', prop: 'facecolor', value: '#f00' }] })
  })

  it('不经 init 的（playground / 嵌入 / 单测）门保持初值 ready', async () => {
    const app = await bootCold()
    expect(app.doc.getRestoreGate()).toMatchObject({ state: 'ready', reason: 'initial' })
    app.restore.beginStartupRestore(null)
    expect(app.doc.getRestoreGate().state).toBe('restoring')
    // 已经发生过恢复后不再重复关门
    await app.restore.restoreSession()
    const g = app.doc.getRestoreGate()
    app.restore.beginStartupRestore(null)
    expect(app.doc.getRestoreGate()).toEqual(g)
  })
})

describe('恢复期间落在画布上的素材：落点按占位页面算的，排队后不沿用', () => {
  it('占位的大页面上落在 (120, 130) mm；恢复出来的是 100×100 的页——面板按常规规则摆进页内，不落在页外', async () => {
    const pd: ProjectDocument = emptyProject()
    pd.project.name = '小页面'
    pd.canvases[0].page = { ...pd.canvases[0].page, w: 100, h: 100 }
    pd.canvases[0].objects = [{ id: 't', type: 'text', text: 'x', sizePt: 9, bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 10, h: 5 } as never]
    pd.updatedAt = 100
    be.putSlot('d_small', pd, 'p_a')
    be.setLast('p_a', 'd_small', '小页面')
    const app = await boot()
    const { useAssetStore } = await import('@/store/assetStore')
    useAssetStore.setState({ panels: [fig1Info as never], byId: { 'Fig1.pdf': fig1Info as never }, loaded: true })
    const ws = await import('@/store/workspace')
    const hold = gate()
    be.state.hold.session = hold.promise
    const restoring = app.doc.restoreSession()
    await vi.waitFor(() => expect(be.state.calls).toContain('GET session'))
    expect(app.doc.useDocumentStore.getState().doc.page.w).not.toBe(100) // 占位页比恢复出来的大
    // 落点按占位（较大的）页面算，对恢复出来的 100×100 页已经在页外
    expect(ws.addPanelToCanvas(fig1Info as never, 120, 130)).toBeNull()
    hold.release()
    await restoring
    await vi.waitFor(() => expect(panelsOf(app)).toHaveLength(1))
    const st = app.doc.useDocumentStore.getState()
    expect(st.doc.page).toMatchObject({ w: 100, h: 100 })
    const p = panelsOf(app)[0]
    expect(p.x).toBeGreaterThanOrEqual(0)
    expect(p.y).toBeGreaterThanOrEqual(0)
    expect(p.x + p.w).toBeLessThanOrEqual(100.001)
    expect(p.y + p.h).toBeLessThanOrEqual(100.001)
    // 没有沿用旧落点：按旧坐标会被夹到页面右下角，常规摆放不会
    expect(p.x + p.w > 99.9 && p.y + p.h > 99.9).toBe(false)
  })
})
