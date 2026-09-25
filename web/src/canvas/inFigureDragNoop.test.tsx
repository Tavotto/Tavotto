/**
 * 图内拖动的三条合同（QA 2026-09-24 geo 节，Refs #583）：
 *
 *   GEO-B3（规范 GEO-03）整组平移同时选中子图与它自己的孩子（axes_0 + axes_0.title）：
 *     孩子的 <g> 嵌在子图的 <g> 里，预览里只能走 Δ 一次（不是 2Δ）；提交时子图写
 *     position +Δ、孩子写 figure 锚定的 pos_frac = 锚点 +Δ，各一次。
 *   GEO-B5（规范 GEO-07）拖出去又拖回原处松手 = 终点净位移为零：不写 override、不进
 *     历史、不渲染（否则一条等于原值的绝对坐标把标题钉出 matplotlib 自动布局）。判据按
 *     **终点净位移**，不按途中是否越过拖动阈值——五个松手入口一个不落。
 *   GEO-B6 单条 setOverride 原地 upsert：给已有的 override 写新值不挪位置；同值写入
 *     不改数组、不进历史、变体键不变。
 *
 * 主语：B3 量的是拖动中那一帧、标题节点在 SVG 用户坐标里的**累计**平移（自己 + 所有
 * 祖先的 translate 前缀），B5 / B6 量的是松手后的文档 overrides、撤销栈与 engineRender。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { alignEntries, resolveGroup } from '@/lib/elementGeom'
import { setOverride } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import {
  startArrowDrag,
  startAxesDrag,
  startElementDrag,
  startElementGroupMove,
  startGroupResize,
} from './interactions'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

const AXES_POS: [number, number, number, number] = [0.12, 0.11, 0.8, 0.77]

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图',
  bbox: [0.1, 0.1, 0.8, 0.8],
  editable: [{ prop: 'position', type: 'rect', value: AXES_POS }],
  draggable: false,
  resizable: true,
}

// 第二个子图只给成组缩放用（缩放不做 SVG 预览，不需要 DOM 节点）
const axes2El: ManifestElement = {
  gid: 'axes_2',
  role: 'axes',
  label: '子图 2',
  bbox: [0.1, 0.05, 0.3, 0.02],
  editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.93, 0.3, 0.02] }],
  draggable: false,
  resizable: true,
}

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.2, 0.05, 0.4, 0.08],
  editable: [],
  draggable: true,
  anchor: [0.4, 0.09],
  drag_prop: 'pos_frac',
}

const arrowEl: ManifestElement = {
  gid: 'axes_0.arrows_3',
  role: 'arrow_patch',
  label: '箭头',
  bbox: [0.2, 0.2, 0.3, 0.2],
  editable: [],
  draggable: true,
  arrow_endpoints: [
    [0.2, 0.4],
    [0.5, 0.2],
  ],
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axesEl,
    axes2El,
    titleEl,
    arrowEl,
  ],
}

const panelOf = (overrides: PanelObject['overrides'] = []): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig.py',
    overrides,
  }) as unknown as PanelObject

const layout = { width: mmToWorld(101.6), height: mmToWorld(76.2) }
// MATPLOTLIB_SVG 的 viewBox 是 288 × 216
const VB: [number, number] = [288, 216]

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

const fire = (
  type: 'pointermove' | 'pointerup' | 'pointercancel',
  clientX: number,
  clientY: number,
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))

/** 依次移到这些点，每一步刷一帧预览 */
function path(points: [number, number][]) {
  for (const [x, y] of points) {
    fire('pointermove', x, y)
    flushPreviewFrame()
  }
}

/** 节点自己 + 所有祖先上 `translate(a,b)` 前缀的累计值（预览只写这一种前缀） */
function cumulativeTranslate(gid: string): [number, number] {
  let n: Element | null = document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)
  if (!n) throw new Error(`SVG 里没有 ${gid}`)
  let x = 0
  let y = 0
  while (n && n.tagName.toLowerCase() !== 'svg') {
    const m = /^translate\(([^,\s]+)[,\s]+([^)]+)\)/.exec(n.getAttribute('transform') ?? '')
    if (m) {
      x += Number(m[1])
      y += Number(m[2])
    }
    n = n.parentElement
  }
  return [x, y]
}

async function setup(overrides: PanelObject['overrides'] = []) {
  engineRender.mockReset()
  engineRender.mockReturnValue(new Promise(() => {}))
  resetPreview()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    snapEnabled: false,
    elementPanelId: 'p1',
    selectedGids: [],
    dragAxesWithCompanions: true,
  })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_in_figure_noop')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(overrides))
  })
  seedExactRender(panelOf(overrides), manifest, { svg: MATPLOTLIB_SVG })
  document.body.innerHTML = `<div data-element-svg="p1">${MATPLOTLIB_SVG}</div>`
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(() => setup())

afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ===================== GEO-B3：整组平移里的父子不走两遍 ===================== */

describe('GEO-B3：整组平移同时选中子图与它自己的标题', () => {
  it('前提：标题的 <g> 确实嵌在 axes_0 的 <g> 里', () => {
    const title = document.querySelector('[data-element-svg="p1"] [id="axes_0.title"]')!
    expect(title.closest('[id="axes_0"]')).not.toBeNull()
  })

  it('预览里标题只走 Δ 一次；松手后子图与标题各写一次 Δ', () => {
    const entries = alignEntries(livePanel(), manifest, ['axes_0', 'axes_0.title'])
    expect(entries.map((e) => e.key).sort()).toEqual(['axes_0', 'axes_0.title'])

    startElementGroupMove(down(0, 0), livePanel(), entries, layout)
    const ds: [number, number] = [40, 20]
    path(Array.from({ length: 10 }, (_, i) => [(ds[0] * (i + 1)) / 10, (ds[1] * (i + 1)) / 10]))

    const once: [number, number] = [(ds[0] / layout.width) * VB[0], (ds[1] / layout.height) * VB[1]]
    const t = cumulativeTranslate('axes_0.title')
    expect(t[0]).toBeCloseTo(once[0], 6)
    expect(t[1]).toBeCloseTo(once[1], 6)
    // 子图自己照样走 Δ（跳过的只是嵌套的那个成员）
    const a = cumulativeTranslate('axes_0')
    expect(a[0]).toBeCloseTo(once[0], 6)
    expect(a[1]).toBeCloseTo(once[1], 6)

    fire('pointerup', ds[0], ds[1])
    const dfx = ds[0] / layout.width
    const dfy = ds[1] / layout.height
    const ov = livePanel().overrides
    const pos = ov.find((o) => o.gid === 'axes_0' && o.prop === 'position')?.value as number[]
    const frac = ov.find((o) => o.gid === 'axes_0.title' && o.prop === 'pos_frac')?.value as number[]
    // position 是 bottom-origin：屏幕向下 = y 变小
    expect(pos[0]).toBeCloseTo(AXES_POS[0] + dfx, 4)
    expect(pos[1]).toBeCloseTo(AXES_POS[1] - dfy, 4)
    // 标题 figure 锚定：锚点 + Δ 一次（不是 2Δ）
    expect(frac[0]).toBeCloseTo(titleEl.anchor![0] + dfx, 4)
    expect(frac[1]).toBeCloseTo(titleEl.anchor![1] + dfy, 4)
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
  })

  it('祖先不在选区里时，孩子照样自己平移（跳过只针对「祖先也被选中」）', () => {
    const entries = alignEntries(livePanel(), manifest, ['axes_0.title'])
    startElementGroupMove(down(0, 0), livePanel(), entries, layout)
    path([[20, 0], [40, 0]])
    const t = cumulativeTranslate('axes_0.title')
    expect(t[0]).toBeCloseTo((40 / layout.width) * VB[0], 6)
    fire('pointercancel', 40, 0)
  })
})

/* ================= GEO-B5：拖出去又拖回原处 = 什么都没发生 ================== */

describe('GEO-B5：终点净位移为零时不写 override、不进历史、不渲染', () => {
  const expectNothingHappened = () => {
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    // 预览也收干净了：不留临时 transform
    expect(cumulativeTranslate('axes_0.title')).toEqual([0, 0])
  }

  it('单拖文字：拖出去 30px 再拖回原点松手', () => {
    startElementDrag(down(100, 100), livePanel(), titleEl, layout)
    path([[110, 100], [130, 100], [115, 100], [100, 100]])
    fire('pointerup', 100, 100)
    expectNothingHappened()
  })

  it('整组平移：拖出去再拖回原点松手', () => {
    const entries = alignEntries(livePanel(), manifest, ['axes_0', 'axes_0.title'])
    startElementGroupMove(down(100, 100), livePanel(), entries, layout)
    path([[130, 120], [100, 100]])
    fire('pointerup', 100, 100)
    expectNothingHappened()
  })

  it('子图整体移动：拖出去再拖回原点松手', () => {
    startAxesDrag(down(100, 100), livePanel(), axesEl, layout, 'move')
    path([[130, 120], [100, 100]])
    fire('pointerup', 100, 100)
    expectNothingHappened()
  })

  it('子图缩放：拖出去再拖回原点松手', () => {
    startAxesDrag(down(100, 100), livePanel(), axesEl, layout, 'se')
    path([[130, 120], [100, 100]])
    fire('pointerup', 100, 100)
    expectNothingHappened()
  })

  it('箭头整体平移：拖出去再拖回原点松手', () => {
    startArrowDrag(down(100, 100), livePanel(), arrowEl, layout, 'both')
    path([[130, 120], [100, 100]])
    fire('pointerup', 100, 100)
    expectNothingHappened()
  })

  it('成组缩放：拖出去再拖回原点松手', () => {
    const group = resolveGroup(livePanel(), manifest, ['axes_0', 'axes_2'])
    expect(group).not.toBeNull()
    startGroupResize(down(100, 100), livePanel(), group!, layout, 'se')
    path([[130, 120], [100, 100]])
    fire('pointerup', 100, 100)
    expectNothingHappened()
  })

  it('对照：真的挪了一段再松手照样写一条、一条历史、一次渲染', () => {
    startElementDrag(down(100, 100), livePanel(), titleEl, layout)
    path([[130, 100], [115, 100]])
    fire('pointerup', 115, 100)
    expect(livePanel().overrides).toHaveLength(1)
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
  })
})

/* ================= GEO-B6：单条 setOverride 原地 upsert ================== */

describe('GEO-B6：单条 setOverride 原地改值', () => {
  const seeded: PanelObject['overrides'] = [
    { gid: 'axes_0.title', prop: 'pos_frac', value: [0.2, 0.1] },
    { gid: 'axes_0.title', prop: 'color', value: '#ff0000' },
  ]

  it('给已有 override 写新值：原地改，数组顺序不变', async () => {
    await setup(seeded)
    setOverride('p1', 'axes_0.title', 'pos_frac', [0.3, 0.1], 'none')
    expect(livePanel().overrides.map((o) => o.prop)).toEqual(['pos_frac', 'color'])
    expect(livePanel().overrides[0].value).toEqual([0.3, 0.1])
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('同值写入：数组不变、不进历史、变体键不变、不渲染', async () => {
    await setup(seeded)
    const before = livePanel().overrides
    const keyBefore = renderKeyOf(livePanel())
    setOverride('p1', 'axes_0.title', 'pos_frac', [0.2, 0.1], true)
    expect(livePanel().overrides).toBe(before)
    expect(renderKeyOf(livePanel())).toBe(keyBefore)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
  })
})
