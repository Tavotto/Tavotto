/**
 * SSE 事件 → 画布（Prompt 06 §六 / §七 / §九）。
 *
 * 这一份钉的是闭环里前端那一段：收到一条事件之后，素材清单、画布上已有面板
 * 的派生元数据、渲染缓存、图内编辑态、状态提示各自变成什么样。
 *
 * 直接驱动 `handleServerEvent`（与 `useEngineSync` 导出 `syncEngine` 同一条
 * 纪律）：经 EventSource 去测的话，测的是 jsdom 的 SSE 实现，而不是这里的判断。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn(),
  refreshProject: vi.fn().mockResolvedValue({}),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchRegistry: vi.fn().mockResolvedValue({
    source: '', scripts: {}, candidates: [], conflicts: {}, all_scripts: [],
  }),
}))
vi.mock('@/lib/session', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/session')>()),
  currentProjectId: vi.fn(() => project),
}))

import { fetchPanels, refreshProject, type PanelInfo, type ServerEvent } from '@/lib/api'
import type { CanvasData, PanelObject } from '@/types/document'
import { canvasToDoc } from '@/types/document'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import {
  recoverAfterReconnect,
  refreshProjectNow,
  resetReconnectThrottle,
  syncLoadedDocument,
} from '@/store/liveSync'
import { useProjectStore } from '@/store/projectStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { handleServerEvent } from './useServerEvents'

const mockPanels = vi.mocked(fetchPanels)
const mockRefresh = vi.mocked(refreshProject)

let project: string | null = 'p1'

const info = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace(/\.[^.]+$/, ''),
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...over,
})

const panels = (list: PanelInfo[]) => ({ figures_dir: '/figs', panels: list })

const panelObj = (id: string, fileId: string, over: Partial<PanelObject> = {}): PanelObject => ({
  id,
  type: 'panel',
  fileId,
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 10, y: 20, w: 40, h: 30,
  ...over,
})

function seed(objects: PanelObject[]): void {
  const canvas: CanvasData = {
    id: 'c1', name: 'Fig 1', page: { w: 150, h: 100 }, objects, guides: [],
  }
  useDocumentStore.setState({
    doc: canvasToDoc(canvas),
    canvases: [canvas],
    activeCanvasId: 'c1',
    openTabs: ['c1'],
    canvasSessions: {},
    past: [], future: [], txn: null,
    dirty: false, saveState: 'clean',
    derivedSeq: 0,
    loadSeq: useDocumentStore.getState().loadSeq + 1,
  })
}

const ev = (raw: unknown): ServerEvent => raw as ServerEvent
const tick = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve()
}
const statusKey = () => useUiStore.getState().status?.key

beforeEach(() => {
  project = 'p1'
  mockPanels.mockReset()
  mockRefresh.mockReset().mockResolvedValue({} as never)
  resetAssetLoadBookkeeping()
  resetReconnectThrottle()
  useAssetStore.setState({ panels: [], byId: {}, loading: false, loaded: false, error: null })
  useProjectStore.setState({
    phase: 'open',
    project: { open: true, id: 'p1' },
    recent: [],
    opened: [],
  })
  useRenderStore.getState().clear()
  useSelectionStore.getState().set([])
  useUiStore.setState({ status: null, statusTone: 'info', elementPanelId: null, selectedGids: [] })
  seed([])
})

describe('registry.changed', () => {
  it('刷新素材并把画布上已有的面板原地升级', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { script: 'fig1.py' })]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    expect(mockPanels).toHaveBeenCalledTimes(1)
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBe('fig1.py')
    expect(statusKey()).toBe('status.sourceLinked')
  })

  it('升级的面板转入引擎跟踪（下一轮同步会按新脚本重建）', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { script: 'fig1.py' })]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    expect(useRenderStore.getState().tracked['Fig1.pdf']).toBe(true)
  })

  it('无差异时不弹提示', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py' })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { script: 'fig1.py' })]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['other.py'] }))
    await tick()

    expect(statusKey()).toBeUndefined()
  })
})

describe('assets.changed', () => {
  it('刷新素材清单（mtime 换代 → 静态图片 URL 跟着换）', async () => {
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { mtime: 99 })]))

    handleServerEvent(
      ev({ kind: 'assets.changed', pj: 'p1', ids: ['Fig1.pdf'], added: [], removed: [], changed: ['Fig1.pdf'] }),
    )
    await tick()

    expect(useAssetStore.getState().byId['Fig1.pdf'].mtime).toBe(99)
  })

  it('删掉的素材不动文档对象', async () => {
    const o = panelObj('o1', 'Gone.pdf', { script: 'gone.py' })
    seed([o])
    mockPanels.mockResolvedValue(panels([]))

    handleServerEvent(
      ev({ kind: 'assets.changed', pj: 'p1', ids: ['Gone.pdf'], added: [], removed: ['Gone.pdf'], changed: [] }),
    )
    await tick()

    expect(useDocumentStore.getState().doc.objects[0]).toBe(o)
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBe('gone.py')
  })
})

describe('panel.file_changed', () => {
  it('既有行为不变：按 stem 找到面板、转入引擎跟踪、提示脚本已更新', async () => {
    seed([panelObj('o1', 'main_text_panels/Fig1.pdf', { script: 'fig1.py' })])
    mockPanels.mockResolvedValue(panels([info('main_text_panels/Fig1.pdf', { script: 'fig1.py' })]))

    handleServerEvent(
      ev({ kind: 'panel.file_changed', pj: 'p1', scripts: ['fig1.py'], stems: ['Fig1'] }),
    )

    // 重建**不等**素材刷新：markStale 是同步的
    expect(useRenderStore.getState().tracked['main_text_panels/Fig1.pdf']).toBe(true)
    expect(statusKey()).toBe('status.scriptChanged')
    await tick()
    expect(mockPanels).toHaveBeenCalledTimes(1)
  })

  it('当前文档里没有对应面板时照样刷新，不报错', async () => {
    mockPanels.mockResolvedValue(panels([]))
    handleServerEvent(ev({ kind: 'panel.file_changed', pj: 'p1', stems: ['Nobody'] }))
    await tick()
    expect(useUiStore.getState().statusTone).toBe('info')
    expect(mockPanels).toHaveBeenCalledTimes(1)
  })
})

describe('一批事件', () => {
  it('registry.changed + assets.changed 合并成一次请求、一条提示', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { script: 'fig1.py' })]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    handleServerEvent(
      ev({ kind: 'assets.changed', pj: 'p1', ids: ['Fig1.pdf'], added: [], removed: [], changed: ['Fig1.pdf'] }),
    )
    await tick()

    expect(mockPanels).toHaveBeenCalledTimes(1)
    expect(statusKey()).toBe('status.sourceLinked')
    // 第二条事件的同步是零差异，因此没有第二次写文档
    expect(useDocumentStore.getState().derivedSeq).toBe(1)
  })
})

describe('项目隔离', () => {
  it('pj 不是本标签页的项目：整条事件无视', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    handleServerEvent(ev({ kind: 'registry.changed', pj: 'other', scripts: ['fig1.py'] }))
    await tick()

    expect(mockPanels).not.toHaveBeenCalled()
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBeNull()
  })

  it('切项目途中到达的旧事件：响应不落进新项目', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    let release!: () => void
    mockPanels.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve(panels([info('Fig1.pdf', { script: 'fig1.py' })]))
      }),
    )

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    project = 'p2' // 用户切了图库
    release()
    await tick()

    expect(useAssetStore.getState().panels).toEqual([])
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBeNull()
  })
})

describe('降级', () => {
  const enterEditing = () => {
    useUiStore.setState({ elementPanelId: 'o1', selectedGids: ['axes_0'] })
    useSelectionStore.getState().set(['o1'])
  }

  it('正在图内编辑的面板失去脚本：退回画布，画布选择保留', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py', overrides: [{ gid: 'axes_0', prop: 'title', value: 'x' }] })])
    enterEditing()
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf')]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(useUiStore.getState().selectedGids).toEqual([])
    expect(useSelectionStore.getState().ids).toEqual(['o1'])
    expect(statusKey()).toBe('status.sourceLostEditing')
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('overrides 与排版一个都不删', async () => {
    const overrides = [{ gid: 'axes_0', prop: 'title', value: 'x' }]
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py', overrides, x: 11, y: 22 })])
    enterEditing()
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf')]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    const o = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(o.overrides).toEqual(overrides)
    expect([o.x, o.y]).toEqual([11, 22])
  })

  it('失效的 manifest / 渲染缓存跟着清掉——否则界面还显示"可编辑"', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py' })])
    useRenderStore.setState({
      tracked: { 'Fig1.pdf': true },
      latest: { 'Fig1.pdf': 'Fig1.pdf#0' },
    })
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf')]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    expect(useRenderStore.getState().tracked['Fig1.pdf']).toBeUndefined()
    expect(useRenderStore.getState().latest['Fig1.pdf']).toBeUndefined()
  })

  it('没在编辑那张图时用较轻的提示，不打断当前工作', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py' })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf')]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    expect(statusKey()).toBe('status.sourceLost')
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('同一批里既有升级又有降级时，说的是**失去**的那件事', async () => {
    // 得而复失里，用户必须知道的是"失"：得到的那份下次双击自然会发现，
    // 失去的那份不说就变成"点进去什么都没有"
    seed([
      panelObj('o1', 'A.pdf', { script: null }),
      panelObj('o2', 'B.pdf', { script: 'b.py' }),
    ])
    mockPanels.mockResolvedValue(panels([info('A.pdf', { script: 'a.py' }), info('B.pdf')]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['a.py', 'b.py'] }))
    await tick()

    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBe('a.py')
    expect((useDocumentStore.getState().doc.objects[1] as PanelObject).script).toBeNull()
    expect(statusKey()).toBe('status.sourceLost')
  })

  it('升级不会自动把用户拽进图内编辑', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { script: 'fig1.py' })]))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    expect(useUiStore.getState().elementPanelId).toBeNull()
  })
})

describe('刷新失败', () => {
  it('不拿上一轮的旧清单去同步文档，也不弹「已找到源脚本」', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    // 上一轮成功刷新留下的清单：它此刻已经不能代表磁盘了
    useAssetStore.setState({
      byId: { 'Fig1.pdf': info('Fig1.pdf', { script: 'fig1.py' }) },
      loaded: true,
    })
    mockPanels.mockRejectedValue(new Error('后端不可达'))

    handleServerEvent(ev({ kind: 'registry.changed', pj: 'p1', scripts: ['fig1.py'] }))
    await tick()

    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBeNull()
    expect(statusKey()).toBeUndefined()
    // 已有的素材数据原样留着，下一条事件还能恢复
    expect(useAssetStore.getState().byId['Fig1.pdf']).toBeDefined()
    expect(useAssetStore.getState().error).toBe('后端不可达')
  })
})

describe('project.error', () => {
  it('已知的 code 按当前语言显示，并且是常驻错误（不是模态框）', () => {
    handleServerEvent(
      ev({ kind: 'project.error', pj: 'p1', code: 'scan_failed', params: { reason: '坏了' } }),
    )
    expect(useUiStore.getState().status).toEqual({
      key: 'backend.scan_failed',
      ns: 'errors',
      values: { reason: '坏了' },
    })
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('本构建还不认识的 code 退回一句通用的可恢复说明', () => {
    handleServerEvent(ev({ kind: 'project.error', pj: 'p1', code: 'from_the_future' }))
    expect(statusKey()).toBe('status.projectBackgroundError')
  })
})

describe('项目打开时的对账', () => {
  it('文档装载完就按手里的清单原地升级——**不发请求**', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    useAssetStore.setState({
      byId: { 'Fig1.pdf': info('Fig1.pdf', { script: 'fig1.py' }) },
      loaded: true,
    })

    syncLoadedDocument()

    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBe('fig1.py')
    expect(mockPanels).not.toHaveBeenCalled()
  })

  it('清单还没取回来时是彻底的 no-op（不会把面板判成失去脚本）', () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: 'fig1.py' })])
    useAssetStore.setState({ byId: {}, loaded: false })

    syncLoadedDocument()

    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBe('fig1.py')
    expect(useDocumentStore.getState().derivedSeq).toBe(0)
  })
})

describe('SSE 重连恢复', () => {
  it('重新连上就补一次素材刷新与派生同步', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { script: 'fig1.py' })]))

    recoverAfterReconnect()
    await tick()

    expect(mockPanels).toHaveBeenCalledTimes(1)
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBe('fig1.py')
    // 恢复**不调**后端的静态刷新：一次网络抖动不该换来一次扫盘
    expect(mockRefresh).not.toHaveBeenCalled()
  })

  it('连着重连几次只补一次（节流）', async () => {
    mockPanels.mockResolvedValue(panels([]))
    recoverAfterReconnect()
    await tick()
    recoverAfterReconnect()
    recoverAfterReconnect()
    await tick()
    expect(mockPanels).toHaveBeenCalledTimes(1)
  })

  it('没有打开的项目时什么都不做', async () => {
    useProjectStore.setState({ phase: 'none', project: null })
    recoverAfterReconnect()
    await tick()
    expect(mockPanels).not.toHaveBeenCalled()
  })
})

describe('手动刷新', () => {
  it('走统一刷新端点，然后经同一个合并函数取素材', async () => {
    seed([panelObj('o1', 'Fig1.pdf', { script: null })])
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf', { script: 'fig1.py' })]))

    await refreshProjectNow()

    expect(mockRefresh).toHaveBeenCalledWith('manual')
    expect(mockPanels).toHaveBeenCalledTimes(1)
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).script).toBe('fig1.py')
  })

  it('后端刷新失败也照样取一次素材，并把错误抛给调用方去显示', async () => {
    mockRefresh.mockRejectedValue(new Error('扫描失败'))
    mockPanels.mockResolvedValue(panels([info('Fig1.pdf')]))

    await expect(refreshProjectNow()).rejects.toThrow('扫描失败')
    expect(mockPanels).toHaveBeenCalledTimes(1)
    expect(useAssetStore.getState().loaded).toBe(true)
  })
})
