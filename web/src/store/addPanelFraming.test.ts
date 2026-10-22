/**
 * 新加进画布的图怎么进视野，只由 `workspace.frameAddedPanel` 一处判（#706 评审 P2 ×4）：
 *
 * - 多图排版上：取景「页面 ∪ 这张图」并留在适应模式；拖放到一点、图整张已在视口里就不动；
 * - 单图编辑里（画布上恰好一张图；对话框在单图时加图，或 `openFastEdit` 打开一张还不在画布上的图）：
 *   这一张的加入让画布变成两张 = 切到多图排版（`startWorkspaceModeSync`），那一刻取景「页面 ∪ 所有图」
 *   并留在适应模式（不还原进单图之前的视口：用户说「取景页面 ∪ 图」）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CapturedFigureDescriptor, PanelInfo } from '@/lib/api'
import { BASE_PX_PER_MM } from '@/lib/units'
import { emptyProject } from '@/types/document'
import { setPageSize } from './actions'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'
import { startPageSizeFit } from './pageFit'
import { useViewportStore } from './viewportStore'
import {
  addFigureToLayout,
  addPanelToCanvas,
  addRuntimePanelToCanvas,
  openFastEdit,
  startWorkspaceModeSync,
  useWorkspaceStore,
} from './workspace'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const VIEW = { left: 0, top: 0, width: 800, height: 600 }
const vp = () => useViewportStore.getState()
const view = () => ({ zoom: vp().zoom, panX: vp().panX, panY: vp().panY })
const page = () => useDocumentStore.getState().doc.page
let stop: () => void = () => {}
let stopMode: () => void = () => {}

function info(id: string, w: number, h: number): PanelInfo {
  return { id, name: id, folder: '.', kind: 'pdf', native_w_mm: w, native_h_mm: h, mtime: 1 }
}

function descriptor(w: number, h: number): CapturedFigureDescriptor {
  return {
    asset_id: 'rt_big',
    script: 'fig.py',
    entry: 'fig.py',
    stem: 'big',
    capture_source: 'savefig',
    execution_profile: 'safe',
    original_artifact: null,
    size_mm: [w, h],
    source_fingerprint: 'f',
    can_writeback_artifact: false,
    can_writeback_source: false,
  }
}

/** 取景框按「页面 ∪ 对象」独立算（不借 `pageUnion`：那等于拿被测代码验自己） */
function unionOf(...os: { x: number; y: number; w: number; h: number }[]) {
  const { w, h } = page()
  const x = Math.min(0, ...os.map((o) => o.x))
  const y = Math.min(0, ...os.map((o) => o.y))
  return {
    x,
    y,
    w: Math.max(w, ...os.map((o) => o.x + o.w)) - x,
    h: Math.max(h, ...os.map((o) => o.y + o.h)) - y,
  }
}

const figures = () => useDocumentStore.getState().doc.objects.filter((o) => o.type === 'panel')

/** 画布上先放两张小图：≥2 张 = 多图排版（后面用例在它上面再加图） */
function seedLayout() {
  addFigureToLayout('small.pdf')
  addFigureToLayout('edit.pdf')
  expect(useWorkspaceStore.getState().mode).toBe('layout')
}

/** 按页面适配的落点，按公式独立算 */
function pageFitView() {
  const { w, h } = page()
  const k = BASE_PX_PER_MM
  const zoom = Math.min((VIEW.width - 72) / (w * k), (VIEW.height - 72) / (h * k))
  return { zoom, panX: (VIEW.width - w * k * zoom) / 2, panY: (VIEW.height - h * k * zoom) / 2 }
}

/**
 * 一片「整张页面都在舞台里、又不是适配落点」的自定义视口：缩放与适配相同、平移错开一点
 * （适配留白 36 px，错开 20 / 10 px 页面仍整张在里面）。缩放不放大余地：1.15 倍页面的
 * 超页图在较紧的那一维上必然出舞台，「超页图要重新取景」这条判据才不会恒等成立
 */
function customLayoutView() {
  const f = pageFitView()
  vp().setView({ zoom: f.zoom, panX: f.panX + 20, panY: f.panY + 10 })
  return view()
}

const bigInfo = () => info('big.pdf', page().w * 3, page().h * 3)

beforeEach(async () => {
  // reduced motion：补间同步落终态。r4129948729 说的就是这一侧：停放的本该是加图之前那一片
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('prefers-reduced-motion'),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }))
  localStorage.clear()
  useWorkspaceStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_addframe')
  const assets = [info('small.pdf', 10, 10), bigInfo(), info('edit.pdf', 10, 10)]
  useAssetStore.setState({ panels: assets, byId: Object.fromEntries(assets.map((a) => [a.id, a])) })
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, fitted: false })
  vp().setViewRect(VIEW)
  stop = startPageSizeFit()
  // 模式由图数推出：挂上真订阅，加图就和应用里一样切模式、动视口
  stopMode = startWorkspaceModeSync()
})

afterEach(() => {
  stop()
  stopMode()
  useWorkspaceStore.getState().clear()
  vi.unstubAllGlobals()
})

describe('多图排版上加图', () => {
  beforeEach(seedLayout)

  it('addPanelToCanvas（选图对话框 / 接入状态对话框）：超页图取景「页面 ∪ 图」，留在适应模式', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addPanelToCanvas(bigInfo())!
    expect(obj.w).toBeGreaterThan(page().w) // 软上限下确实伸出页面
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })

  it('addRuntimePanelToCanvas（脚本库 / 接入状态 / tavotto run 交接）同样取景', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addRuntimePanelToCanvas(descriptor(page().w * 3, page().h * 3))!
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })

  it('拖放到一点、图整张已在视口里：视口不动', () => {
    const before = customLayoutView()
    addPanelToCanvas(info('small.pdf', 10, 10), page().w / 2, page().h / 2)
    expect(vp()).toMatchObject({ ...before, fitted: false })
  })

  it('拖放到一点、图伸出视口：同样取景「页面 ∪ 这张图」', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addPanelToCanvas(bigInfo(), page().w / 2, page().h / 2)!
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })
})

describe('单图编辑里加图 = 第二张进来，切到多图排版', () => {
  it('对话框在单图时加超页图：切多图排版，取景「页面 ∪ 所有图」', () => {
    customLayoutView()
    openFastEdit('edit.pdf')
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    const obj = addPanelToCanvas(bigInfo())!
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(...figures()))
    expect(figures()).toContain(obj)
  })

  it('对话框在单图时加的小图：同样取景「页面 ∪ 所有图」，不还原进单图之前的那一片', () => {
    customLayoutView()
    openFastEdit('edit.pdf')
    addRuntimePanelToCanvas(descriptor(10, 10))
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(...figures()))
  })

  it('同一调用栈里不取两遍景：切换那一次已取了，`frameAddedPanel` 认账不再取', () => {
    customLayoutView()
    openFastEdit('edit.pdf')
    const fit = vi.spyOn(useViewportStore.getState(), 'fitRectAnimated')
    addPanelToCanvas(bigInfo())
    expect(fit).toHaveBeenCalledTimes(1)
  })
})

describe('addFigureToLayout 是分层链第 1 层（r4130522228）', () => {
  it('单图时点「添加到画布」一张新图：切多图排版、经第 2 层取景「页面 ∪ 图」', () => {
    customLayoutView()
    openFastEdit('edit.pdf')
    expect(addFigureToLayout('big.pdf')).toBe('added')
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(...figures()))
  })

  it('已在文档里：只聚焦，不新建第二张', () => {
    addFigureToLayout('small.pdf')
    const n = useDocumentStore.getState().doc.objects.length
    expect(addFigureToLayout('small.pdf')).toBe('focused')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(n)
  })
})

describe('openFastEdit 打开还不在画布上的图（r4129948729）', () => {
  it('空画布上打开：这一张就是单图编辑，取景它本身的矩形', () => {
    customLayoutView()
    openFastEdit('small.pdf')
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    const [only] = figures()
    // 整张图都在舞台里（取景的是它本身的矩形，不是页面）
    const k = BASE_PX_PER_MM * vp().zoom
    expect(only.x * k + vp().panX).toBeGreaterThanOrEqual(0)
    expect(only.y * k + vp().panY).toBeGreaterThanOrEqual(0)
    expect((only.x + only.w) * k + vp().panX).toBeLessThanOrEqual(VIEW.width)
    expect((only.y + only.h) * k + vp().panY).toBeLessThanOrEqual(VIEW.height)
    expect(vp().zoom).toBeGreaterThan(pageFitView().zoom) // 小图被放大到铺满舞台，不是页面那一档
  })

  it('已有一张时再打开一张：换看（新画布），原画布一个字节不动；取景那张新图本身', () => {
    customLayoutView()
    openFastEdit('small.pdf')
    const first = useDocumentStore.getState().activeCanvasId
    openFastEdit('edit.pdf')
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    expect(useDocumentStore.getState().activeCanvasId).not.toBe(first)
    expect(figures()).toHaveLength(1)
    expect(figures()[0]).toMatchObject({ fileId: 'edit.pdf' })
  })
})

describe('单图编辑里改页面尺寸（r4129948734）', () => {
  it('改了 W / H：第二张进来时按新页面取景「页面 ∪ 所有图」，不按旧页面还原', () => {
    customLayoutView()
    openFastEdit('edit.pdf')
    setPageSize(300, 200)
    addFigureToLayout('small.pdf')
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(...figures()))
    expect(page()).toMatchObject({ w: 300, h: 200 })
  })
})
