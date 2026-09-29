/**
 * 新加进画布的图怎么进视野，只由 `workspace.frameAddedPanel` 一处判（#706 评审 P2 ×4）：
 *
 * - 排版上：取景「页面 ∪ 这张图」并留在适应模式；拖放到一点、图整张已在视口里就不动；
 * - 快速编辑里（对话框在快编时加图、`openFastEdit` 打开一张还不在画布上的图）：不动
 *   正在编辑的那一屏，记到停放的排版视口上，回排版时再判；
 * - 回排版：页面尺寸比的是最终值（改了又改回算没变），新图都整张在停放那一片里就原样还原。
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
  addPanelToCanvas,
  addRuntimePanelToCanvas,
  openFastEdit,
  returnToLayout,
  useWorkspaceStore,
} from './workspace'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const VIEW = { left: 0, top: 0, width: 800, height: 600 }
const vp = () => useViewportStore.getState()
const view = () => ({ zoom: vp().zoom, panX: vp().panX, panY: vp().panY })
const page = () => useDocumentStore.getState().doc.page
let stop: () => void = () => {}

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
function unionOf(o: { x: number; y: number; w: number; h: number }) {
  const { w, h } = page()
  const x = Math.min(0, o.x)
  const y = Math.min(0, o.y)
  return { x, y, w: Math.max(w, o.x + o.w) - x, h: Math.max(h, o.y + o.h) - y }
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
})

afterEach(() => {
  stop()
  useWorkspaceStore.getState().clear()
  vi.unstubAllGlobals()
})

describe('排版上加图', () => {
  it('addPanelToCanvas（选图对话框 / 接入状态对话框）：超页图取景「页面 ∪ 图」，留在适应模式', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addPanelToCanvas(bigInfo())
    expect(obj.w).toBeGreaterThan(page().w) // 软上限下确实伸出页面
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })

  it('addRuntimePanelToCanvas（脚本库 / 接入状态 / tavotto run 交接）同样取景', () => {
    vp().setView({ zoom: 3, panX: -500, panY: -400 })
    const obj = addRuntimePanelToCanvas(descriptor(page().w * 3, page().h * 3))
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
    const obj = addPanelToCanvas(bigInfo(), page().w / 2, page().h / 2)
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })
})

describe('快速编辑里加图（r4129948725）', () => {
  it('对话框在快编时加超页图：快编那一屏不动；回排版取景「页面 ∪ 图」', () => {
    customLayoutView()
    openFastEdit('edit.pdf')
    const editing = view()
    const obj = addPanelToCanvas(bigInfo())
    expect(view(), '正在编辑的那一屏不许被新图带着跳').toEqual(editing)
    returnToLayout()
    expect(vp().fitted).toBe(true)
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })

  it('对话框在快编时加的小图整张在停放那一片里：回排版原样还原', () => {
    const before = customLayoutView()
    openFastEdit('edit.pdf')
    addRuntimePanelToCanvas(descriptor(10, 10))
    returnToLayout()
    expect(view()).toEqual(before)
  })
})

describe('openFastEdit 打开还不在画布上的图（r4129948729）', () => {
  it('停放的是加图之前那一片：小图回排版原样还原', () => {
    const before = customLayoutView()
    openFastEdit('small.pdf')
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    returnToLayout()
    expect(view()).toEqual(before)
  })

  it('超页图：回排版取景「页面 ∪ 图」', () => {
    customLayoutView()
    openFastEdit('big.pdf')
    const obj = useDocumentStore
      .getState()
      .doc.objects.find((o) => o.type === 'panel' && o.fileId === 'big.pdf')!
    returnToLayout()
    expect(vp().fitFrame()).toMatchObject(unionOf(obj))
  })
})

describe('快速编辑里改页面尺寸（r4129948734）', () => {
  it('改了 W / H 又改回原值：比的是最终尺寸，回排版原样还原', () => {
    const { w, h } = page()
    const before = customLayoutView()
    openFastEdit('edit.pdf')
    setPageSize(300, 200)
    setPageSize(w, h)
    returnToLayout()
    expect(view()).toEqual(before)
  })

  it('改了 W / H 又撤销：同样原样还原', () => {
    const before = customLayoutView()
    openFastEdit('edit.pdf')
    setPageSize(300, 200)
    useDocumentStore.getState().undo()
    returnToLayout()
    expect(view()).toEqual(before)
  })
})
