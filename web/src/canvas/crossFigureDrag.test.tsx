/**
 * 图内编辑跨图对齐（ADR 0128 §二，PR2）：进图内编辑拖元素 / 子图缩放手柄时，除本图候选外
 * 还吸别的图里**同类元素的同一条边**与页面参考线。
 *
 * 钉住的事实：拖图例靠近另一张图的图例底边 → 吸上；靠近另一张图的**子图**底边 → 不跨类吸；
 * 快速编辑模式不跨图；多选整组不跨图；参考线（「吸附到参考线」）照吸；子图缩放手柄正在动的那条边
 * 吸别的图子图的同一条边，不动的边不吸，⌘ 临时不吸。吸附只改最终框，写法不变。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { alignEntries, panelFullRect } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { startAxesDrag, startElementDrag, startElementGroupMove } from './interactions'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

const W = 101.6
const H = 76.2
const layout = { width: mmToWorld(W), height: mmToWorld(H) }
/** 1 屏幕 px 在面板分数坐标里是多少（zoom 1） */
const PX_X = 1 / layout.width
const PX_Y = 1 / layout.height
const MM_PER_PX = 1 / mmToWorld(1)

// 编辑中的 p1：左子图（上沿 0.10、下沿 0.50、左 0.10、右 0.45）与右下角的图例（框底 0.72）
const axes0: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.35, 0.4],
  editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.5, 0.35, 0.4] }],
  draggable: false,
  resizable: true,
}
const legendP1: ManifestElement = {
  gid: 'axes_0.legend',
  role: 'legend',
  label: '图例',
  bbox: [0.6, 0.62, 0.2, 0.1],
  editable: [],
  draggable: true,
  anchor: [0.6, 0.72],
  drag_prop: 'loc_frac',
}
const figure: ManifestElement = {
  gid: 'figure',
  role: 'figure',
  label: '整图',
  bbox: [0, 0, 1, 1],
  editable: [],
  draggable: false,
}
const manifestP1: Manifest = {
  stem: 'Fig1',
  size_mm: [W, H],
  elements: [figure, axes0, legendP1],
}

const axesAt = (gid: string, pos: [number, number, number, number]): ManifestElement => ({
  gid,
  role: 'axes',
  label: gid,
  bbox: [pos[0], 1 - pos[1] - pos[3], pos[2], pos[3]],
  editable: [{ prop: 'position', type: 'rect', value: pos }],
  draggable: false,
  resizable: true,
})
const legendAt = (bottom: number): ManifestElement => ({
  ...legendP1,
  bbox: [0.6, bottom - 0.1, 0.2, 0.1],
  anchor: [0.6, bottom],
})

const P2_X = -45

const panelOf = (id: string, file: string, x: number, extra: Partial<PanelObject> = {}): PanelObject =>
  ({
    id,
    type: 'panel',
    x,
    y: 0,
    w: W,
    h: H,
    fileId: file,
    fileKind: 'pdf',
    nativeW: W,
    nativeH: H,
    script: 'fig.py',
    overrides: [],
    ...extra,
  }) as unknown as PanelObject

const livePanel = (id = 'p1'): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === id)
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value as number[] | undefined

const down = () =>
  ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (
  type: 'pointermove' | 'pointerup',
  clientX: number,
  clientY: number,
  mods: { metaKey?: boolean; ctrlKey?: boolean; shiftKey?: boolean } = {},
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true, ...mods }))

function dragTo(x: number, y: number, mods: { metaKey?: boolean } = {}, steps = 10) {
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', (x * i) / steps, (y * i) / steps, mods)
    flushPreviewFrame()
  }
  fire('pointerup', x, y, mods)
}

/** 搭环境：p1 在编辑；p2（可选）是另一张图，manifest 由调用方给 */
async function setup(opts: { p2?: Manifest | null; p2Extra?: Partial<PanelObject> } = {}) {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest: manifestP1, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    snapEnabled: true,
    snapToObjects: true,
    snapToGuides: true,
    elementPanelId: 'p1',
    selectedGids: [],
    dragAxesWithCompanions: true,
  })
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cross_figure_drag')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf('p1', 'Fig1.pdf', 0))
    if (opts.p2) d.objects.push(panelOf('p2', 'Fig2.pdf', P2_X, opts.p2Extra))
  })
  const latest: Record<string, ReturnType<typeof renderKeyOf>> = {}
  for (const [id, file, m] of [
    ['p1', 'Fig1.pdf', manifestP1],
    ['p2', 'Fig2.pdf', opts.p2 ?? null],
  ] as const) {
    if (!m) continue
    const key = renderKeyOf(livePanel(id))
    useRenderStore.getState().patch(key, {
      fileId: file,
      manifest: m,
      svg: MATPLOTLIB_SVG,
      rev: 1,
      status: 'ready',
      lastPatches: '[]',
    })
    latest[file] = key
  }
  useRenderStore.setState({ latest })
  document.body.innerHTML = `<div data-element-svg="p1">${MATPLOTLIB_SVG}</div>`
  const root = document.querySelector('[data-element-svg="p1"] svg')!
  for (const gid of ['axes_0.legend']) {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    g.setAttribute('id', gid)
    root.appendChild(g)
  }
  useDocumentStore.setState({ past: [], future: [] })
}

const manifestOf = (...els: ManifestElement[]): Manifest => ({
  stem: 'Fig2',
  size_mm: [W, H],
  elements: [figure, ...els],
})

beforeEach(() => resetPreview())
afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ============================ 拖图例：跨图同类 ============================ */

describe('图内拖动：跨图对齐同类元素', () => {
  // p2 的图例框底 0.725：p1 图例底 0.72 往下拖 2px = 0.72 + 2px，离 0.725 约 1.4px，在容差内
  const legendBottomMoved = 0.72 + 2 * PX_Y
  const dragLegend = () => {
    startElementDrag(down(), livePanel(), legendP1, layout)
    dragTo(0, 2)
  }

  it('拖图例靠近另一张图的图例底边：吸上（写进去的底边 = 对方底边）', async () => {
    await setup({ p2: manifestOf(axesAt('axes_0', [0.1, 0.5, 0.35, 0.4]), { ...legendAt(0.725), gid: 'axes_0.legend' }) })
    dragLegend()
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(0.725, 3)
  })

  it('页面 mm 对齐误差 < 0.1：两张图图例底边落在同一个页面 y', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    dragLegend()
    const y1 = overrideOf('axes_0.legend', 'loc_frac')![1] * H
    expect(Math.abs(y1 - 0.725 * H)).toBeLessThan(0.1)
  })

  it('靠近另一张图的子图底边（类型不同）：不跨图吸', async () => {
    // p2 子图下沿正好在 0.725
    await setup({ p2: manifestOf(axesAt('axes_0', [0.1, 0.275, 0.35, 0.4])) })
    dragLegend()
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(legendBottomMoved, 3)
  })

  it('边不同（拖图例的底边，对方只有顶边贴近）：不吸', async () => {
    // 对方图例框顶 0.725（底 0.825）；自己的底边 / 中线 / 顶边都够不着 0.725 以外的线
    await setup({ p2: manifestOf({ ...legendAt(0.825), gid: 'axes_0.legend' }) })
    dragLegend()
    // 自己的底边 0.72+2px 离对方顶边 0.725 很近，但底边只配底边——不吸
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(legendBottomMoved, 3)
  })

  it('快速编辑模式：不跨图', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    dragLegend()
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(legendBottomMoved, 3)
  })

  it('另一张图不在视口里：不跨图', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    // 视口只看页面右侧（x > 120 mm），p2 在 -45..56.6
    useViewportStore.setState({ panX: -mmToWorld(120) })
    dragLegend()
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(legendBottomMoved, 3)
  })

  it('另一张图旋转 / 隐藏：不跨图', async () => {
    for (const extra of [{ rotation: 90 }, { hidden: true }] as Partial<PanelObject>[]) {
      await setup({
        p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }),
        p2Extra: extra,
      })
      dragLegend()
      expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(legendBottomMoved, 3)
    }
  })

  it('按住 ⌘：不吸', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    startElementDrag(down(), livePanel(), legendP1, layout)
    dragTo(0, 2, { metaKey: true })
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(legendBottomMoved, 3)
  })

  it('多选整组平移：不跨图（只吸本图与参考线）', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    const entries = alignEntries(livePanel(), manifestP1, ['axes_0.legend', 'axes_0'])
    startElementGroupMove(down(), livePanel(), entries, layout)
    // 整组的包围框：往下 2px，离对方图例底边 0.725 很近——但整组不跨图，包围框底边是子图下沿 / 图例底 的并集
    dragTo(0, 2)
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(legendBottomMoved, 3)
  })
})

/* ================================ 参考线 ================================== */

describe('图内拖动：吸页面参考线', () => {
  const guideY = 0.725 * H

  it('「吸附到参考线」开着：图例底边吸上水平参考线', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'y', pos: guideY })
    })
    startElementDrag(down(), livePanel(), legendP1, layout)
    dragTo(0, 2)
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(0.725, 3)
  })

  it('「吸附到参考线」关掉：不吸', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'y', pos: guideY })
    })
    useUiStore.setState({ snapToGuides: false })
    startElementDrag(down(), livePanel(), legendP1, layout)
    dragTo(0, 2)
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(0.72 + 2 * PX_Y, 3)
  })
})

/* ============================ 子图缩放手柄吸附 ============================ */

describe('图内子图缩放手柄：吸别的图子图的同一条边', () => {
  // p2 子图：左边比 p1 的左边（页面 10.16）多 1px，右边比 p1 右边（45.72）多 2px
  const rightMm = 0.45 * W + 2 * MM_PER_PX
  const leftMm = 0.1 * W + 1 * MM_PER_PX
  const r2 = (rightMm - P2_X) / W
  const l2 = (leftMm - P2_X) / W
  // 上下沿也各比 p1 的偏 1px（贴近容差内），用来证明右柄不会去吸不动的上下沿
  const p2Axes = () => manifestOf(axesAt('axes_0', [l2, 0.5 + PX_Y, r2 - l2, 0.4]))

  it('右柄：右边吸到另一张图子图的右边；不动的左边不吸（哪怕对方左边也贴近）', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    dragTo(3, 0)
    const pos = overrideOf('axes_0', 'position')!
    expect(pos[0]).toBeCloseTo(0.1, 3)
    expect(pos[2]).toBeCloseTo(0.35 + 2 * PX_X, 3)
    // 页面 mm 对齐误差 < 0.1
    expect(Math.abs((pos[0] + pos[2]) * W - rightMm)).toBeLessThan(0.1)
  })

  it('右柄：不动的上下沿即使贴近对方的上下沿，也不出水平吸附线', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    for (let i = 1; i <= 10; i++) {
      fire('pointermove', (3 * i) / 10, 0)
      flushPreviewFrame()
    }
    expect(useInteractionStore.getState().snapXs.length).toBeGreaterThan(0)
    expect(useInteractionStore.getState().snapYs).toHaveLength(0)
    fire('pointerup', 3, 0)
  })

  it('左柄：左边吸到对方子图的左边', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'w')
    dragTo(2, 0)
    const pos = overrideOf('axes_0', 'position')!
    // 左边落在对方左边 = 0.1 + 1px
    expect(pos[0]).toBeCloseTo(0.1 + 1 * PX_X, 3)
  })

  it('对方是图例（类型不同）：不吸', async () => {
    await setup({
      p2: manifestOf({ ...legendAt(0.5), gid: 'axes_0.legend', bbox: [r2 - 0.2, 0.3, 0.2, 0.1] }),
    })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    dragTo(3, 0)
    expect(overrideOf('axes_0', 'position')![2]).toBeCloseTo(0.35 + 3 * PX_X, 3)
  })

  it('按住 ⌘：不吸', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    dragTo(3, 0, { metaKey: true })
    expect(overrideOf('axes_0', 'position')![2]).toBeCloseTo(0.35 + 3 * PX_X, 3)
  })

  it('吸上后手不动、按下 ⌘ 再松手：提交的是未吸附的框（松手事件的修饰键）', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    for (let i = 1; i <= 10; i++) {
      fire('pointermove', (3 * i) / 10, 0)
      flushPreviewFrame()
    }
    expect(useInteractionStore.getState().snapXs.length).toBeGreaterThan(0)
    fire('pointerup', 3, 0, { metaKey: true })
    expect(overrideOf('axes_0', 'position')![2]).toBeCloseTo(0.35 + 3 * PX_X, 3)
  })

  it('吸上后手不动、按下 ⌘：预览立刻退回未吸附；松开 ⌘ 又吸回', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    for (let i = 1; i <= 10; i++) {
      fire('pointermove', (3 * i) / 10, 0)
      flushPreviewFrame()
    }
    const previewW = () => useInteractionStore.getState().elementPreview?.boxes?.axes_0?.[2]
    const snapped = previewW()!
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Meta', metaKey: true }))
    const free = previewW()!
    expect(free - snapped).toBeCloseTo(PX_X, 3)
    expect(useInteractionStore.getState().snapXs).toHaveLength(0)
    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'Meta' }))
    expect(previewW()).toBeCloseTo(snapped, 6)
    fire('pointerup', 3, 0)
    expect(overrideOf('axes_0', 'position')![2]).toBeCloseTo(0.35 + 2 * PX_X, 3)
  })

  it('快速编辑：不吸别的图', async () => {
    await setup({ p2: p2Axes() })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    dragTo(3, 0)
    expect(overrideOf('axes_0', 'position')![2]).toBeCloseTo(0.35 + 3 * PX_X, 3)
  })

  it('下柄：下沿吸到对方子图的下沿（上沿不动）', async () => {
    // p1 下沿（top-origin）0.5；对方下沿 0.5 + 2px
    const b2 = 1 - (0.5 + 2 * PX_Y)
    await setup({ p2: manifestOf(axesAt('axes_0', [0.3, b2, 0.3, 0.3])) })
    startAxesDrag(down(), livePanel(), axes0, layout, 's')
    dragTo(0, 3)
    const pos = overrideOf('axes_0', 'position')!
    expect(pos[1]).toBeCloseTo(b2, 3)
    expect(pos[1] + pos[3]).toBeCloseTo(0.9, 3)
  })

  it('吸附只改最终框：写法不变（仍是一条 position，四个数）', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    dragTo(3, 0)
    const ov = livePanel().overrides.filter((o) => o.gid === 'axes_0')
    expect(ov.map((o) => o.prop)).toEqual(['position'])
    expect(ov[0].value as number[]).toHaveLength(4)
  })

  it('手势结束吸附线收掉', async () => {
    await setup({ p2: p2Axes() })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    for (let i = 1; i <= 10; i++) {
      fire('pointermove', (3 * i) / 10, 0)
      flushPreviewFrame()
    }
    expect(useInteractionStore.getState().snapXs.length).toBeGreaterThan(0)
    fire('pointerup', 3, 0)
    expect(useInteractionStore.getState().snapXs).toHaveLength(0)
  })
})

/* ============================ 吸附反馈（PR4） ============================= */

describe('吸附反馈：自身所属框与跨图目标框', () => {
  /** 拖到 (x, y) 停住（不松手），返回此刻的反馈，再松手 */
  let heldXs: number[] = []
  let heldYs: number[] = []
  const feedbackAt = (x: number, y: number, mods: { metaKey?: boolean } = {}) => {
    for (let i = 1; i <= 10; i++) {
      fire('pointermove', (x * i) / 10, (y * i) / 10, mods)
      flushPreviewFrame()
    }
    const fb = useInteractionStore.getState().snapFeedback
    // 松手会清掉吸附线，所以在松手前取
    heldXs = [...useInteractionStore.getState().snapXs]
    heldYs = [...useInteractionStore.getState().snapYs]
    fire('pointerup', x, y, mods)
    return fb
  }
  const near = (a: number, b: number) => expect(Math.abs(a - b)).toBeLessThan(0.1)

  it('图例吸上本图的线：有 self（图例此刻的框）、没有 target', async () => {
    await setup()
    // 图例顶 0.62 → 子图下沿 0.5：往上约 0.12·H
    startElementDrag(down(), livePanel(), legendP1, layout)
    const fb = feedbackAt(0, -(0.12 / PX_Y) + 1)
    const y = fb.find((f) => f.axis === 'y')!
    expect(y).toBeTruthy()
    expect(y.target).toBeUndefined()
    near(y.self.y, 0.5 * H)
    near(y.self.x, 0.6 * W)
    near(y.self.w, 0.2 * W)
  })

  it('图例吸上另一张图的图例底边：target = 对方图例框', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    startElementDrag(down(), livePanel(), legendP1, layout)
    const fb = feedbackAt(0, 2)
    const y = fb.find((f) => f.axis === 'y')!
    expect(y).toBeTruthy()
    // 两个图例等高：顶对顶与底对底同时在容差内，谁胜出都对——线必须落在对方框的上沿或下沿，
    // 且自身框同一条边与它重合
    near(Math.min(Math.abs(y.v - 0.625 * H), Math.abs(y.v - 0.725 * H)), 0)
    near(Math.min(Math.abs(y.self.y - y.v), Math.abs(y.self.y + y.self.h - y.v)), 0)
    expect(y.target).toBeTruthy()
    near(y.target!.x, P2_X + 0.6 * W)
    near(y.target!.y, 0.625 * H)
    near(y.target!.w, 0.2 * W)
    near(y.target!.h, 0.1 * H)
  })

  it('快速编辑：吸上本图线照样有 self（没有跨图候选，所以没有 target）', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'p1' })
    startElementDrag(down(), livePanel(), legendP1, layout)
    const fb = feedbackAt(0, -(0.12 / PX_Y) + 1)
    const y = fb.find((f) => f.axis === 'y')!
    expect(y).toBeTruthy()
    near(y.self.y, 0.5 * H)
    expect(fb.every((f) => f.target === undefined)).toBe(true)
  })

  it('图例拖向面板外的相邻图同类图例（y 向）：照吸，有 target（图例提交的是未钳位的锚点，够得着）', async () => {
    // p2 下移 50 mm：它的图例底边在页面 y = 50 + 0.725·H ≈ 105 mm，落在 p1（0..H）之外
    await setup({
      p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }),
      p2Extra: { y: 50 },
    })
    const lineY = 50 + 0.725 * H
    startElementDrag(down(), livePanel(), legendP1, layout)
    const fb = feedbackAt(0, (lineY - 0.72 * H) / MM_PER_PX + 1)
    const y = fb.find((f) => f.axis === 'y')
    expect(y).toBeTruthy()
    expect(y!.target).toBeTruthy()
    expect(heldYs.length).toBeGreaterThan(0)
    expect(Math.abs(overrideOf('axes_0.legend', 'loc_frac')![1] * H - lineY)).toBeLessThan(0.2)
  })

  it('子图被钳在图内：面板外的线不参与，不遮住范围内的整图边', async () => {
    await setup()
    // 子图下沿被钳在 H；参考线在 H + 0.2（图外）。未过滤时它比整图下边（H）更近、吸上后因钳位够不着，整个吸附落空
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'y', pos: H + 0.2 })
    })
    startAxesDrag(down(), livePanel(), axes0, layout, 'move')
    const fb = feedbackAt(0, (0.5 * H + 0.3) / MM_PER_PX)
    expect(fb.find((f) => f.axis === 'y')).toBeTruthy()
    expect(heldYs).toEqual([H])
  })

  it('图例拖向面板外的相邻图同类图例（x 向）：照吸', async () => {
    await setup({
      p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }),
      p2Extra: { x: 150 },
    })
    const lineX = 150 + 0.6 * W // 对方图例左边，p1 之外
    startElementDrag(down(), livePanel(), legendP1, layout)
    const fb = feedbackAt((lineX - 0.6 * W) / MM_PER_PX + 1, 0)
    expect(fb.find((f) => f.axis === 'x')?.target).toBeTruthy()
    expect(heldXs.length).toBeGreaterThan(0)
  })

  it('恰在面板边界上的参考线（含边界）：照吸', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'y', pos: H })
    })
    startElementDrag(down(), livePanel(), legendP1, layout)
    const fb = feedbackAt(0, (H - 0.72 * H) / MM_PER_PX + 1)
    expect(fb.find((f) => f.axis === 'y')).toBeTruthy()
  })

  it('图例拖向面板外的参考线：照吸，有吸附线', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'y', pos: 1.5 * H })
    })
    startElementDrag(down(), livePanel(), legendP1, layout)
    const fb = feedbackAt(0, (1.5 * H - 0.72 * H) / MM_PER_PX + 1)
    expect(fb.find((f) => f.axis === 'y')).toBeTruthy()
    expect(heldYs).toEqual([1.5 * H])
  })

  it('子图被拖向图边被钳住、线在范围内却够不着：无吸附线、无反馈', async () => {
    await setup()
    // 参考线在 x = 0.2·W：子图右边吸它需要左边落到 −0.15（图外），实际被钳在 0，右边够不着
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'x', pos: 0.2 * W })
    })
    startAxesDrag(down(), livePanel(), axes0, layout, 'move')
    const fb = feedbackAt(-(0.25 / PX_X) + 1, 0)
    expect(fb.some((f) => f.axis === 'x')).toBe(false)
    expect(heldXs).toHaveLength(0)
    expect(overrideOf('axes_0', 'position')![0]).toBeCloseTo(0, 3)
  })

  it('子图被钳在图边、线恰是那条边：照常有反馈，self 是钳位后的框', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'x', pos: 0 })
    })
    startAxesDrag(down(), livePanel(), axes0, layout, 'move')
    const fb = feedbackAt(-(0.1 / PX_X) - 1, 0) // 左边越过图边 1 px：吸到 0，同时本该被钳在 0
    const x = fb.find((f) => f.axis === 'x')
    expect(x).toBeTruthy()
    near(x!.self.x, 0)
    near(x!.self.w, 0.35 * W)
  })

  it('松手后反馈消失', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    startElementDrag(down(), livePanel(), legendP1, layout)
    feedbackAt(0, 2)
    expect(useInteractionStore.getState().snapFeedback).toHaveLength(0)
  })

  it('按住 ⌘：没有反馈', async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    startElementDrag(down(), livePanel(), legendP1, layout)
    expect(feedbackAt(0, 2, { metaKey: true })).toHaveLength(0)
  })

  it('子图缩放手柄吸上对方子图右边：self 是缩放后的子图框，target 是对方子图框', async () => {
    const rightMm = 0.45 * W + 2 * MM_PER_PX
    const leftMm = 0.1 * W + 1 * MM_PER_PX
    const r2 = (rightMm - P2_X) / W
    const l2 = (leftMm - P2_X) / W
    await setup({ p2: manifestOf(axesAt('axes_0', [l2, 0.5 + PX_Y, r2 - l2, 0.4])) })
    startAxesDrag(down(), livePanel(), axes0, layout, 'e')
    const fb = feedbackAt(3, 0)
    const x = fb.find((f) => f.axis === 'x')!
    expect(x).toBeTruthy()
    near(x.self.x, 0.1 * W)
    near(x.self.x + x.self.w, rightMm) // 缩放后的右边，不是原来的 0.45
    near(x.self.y, 0.1 * H)
    near(x.self.h, 0.4 * H)
    expect(x.target).toBeTruthy()
    near(x.target!.x + x.target!.w, rightMm)
    // 不动的上下沿不出反馈
    expect(fb.some((f) => f.axis === 'y')).toBe(false)
  })
})

/* ===================== 带裁剪的面板：按可见范围过滤（补评审 P2-2） ===================== */

describe('图内子图平移：参考线按面板可见（裁剪后）范围过滤', () => {
  it('裁掉右半的面板：落在整图矩形内、可见窗外的竖直参考线不参选，吸上的是可见窗右缘', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('裁剪'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.crop = { x: 0, y: 0, w: 0.5, h: 1 }
    })
    const full = panelFullRect(livePanel())
    const visibleRight = livePanel().x + livePanel().w
    // 整图宽是可见窗的两倍；参考线在可见窗外 0.3 mm、整图矩形内
    expect(full.w).toBeGreaterThan(livePanel().w * 1.9)
    const guideX = visibleRight + 0.3
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'x', pos: guideX })
    })
    // 子图右缘拖到参考线外 0.2 mm：未过滤时参考线（距 0.2）比可见窗右缘（距 0.7）近，
    // 吸上后因钳在可见窗内够不着，整个吸附落空；按可见窗过滤后吸上的是右缘
    const right = full.x + 0.45 * full.w
    startAxesDrag(down(), livePanel(), axes0, { width: mmToWorld(full.w), height: mmToWorld(full.h) }, 'move')
    const dx = (guideX + 0.2 - right) / MM_PER_PX
    // 一步到位：逐步走过去会先吸住可见窗右缘（吸住后保持），盖掉「候选里有没有那条参考线」的差别
    fire('pointermove', dx, 0)
    flushPreviewFrame()
    expect(useInteractionStore.getState().snapXs).toEqual([visibleRight])
    fire('pointerup', dx, 0)
  })
})

/* ============ 图内拖动：吸上后手不动、改变 ⌘ / Ctrl（补评审 P2-3） ============ */

describe('图内拖动：修饰键变化重算吸附', () => {
  const unsnapped = 0.72 + 2 * PX_Y
  const key = (type: 'keydown' | 'keyup', k: 'Control' | 'Meta') =>
    window.dispatchEvent(new KeyboardEvent(type, { key: k, ctrlKey: type === 'keydown' && k === 'Control' }))
  const snapUp = async () => {
    await setup({ p2: manifestOf({ ...legendAt(0.725), gid: 'axes_0.legend' }) })
    startElementDrag(down(), livePanel(), legendP1, layout)
    for (let i = 1; i <= 10; i++) {
      fire('pointermove', 0, (2 * i) / 10)
      flushPreviewFrame()
    }
    expect(useInteractionStore.getState().snapYs.length).toBeGreaterThan(0)
  }

  it('跨图吸上后不动指针、按住 Ctrl 再松手：提交的是未吸附位移', async () => {
    await snapUp()
    key('keydown', 'Control')
    expect(useInteractionStore.getState().snapYs).toHaveLength(0)
    fire('pointerup', 0, 2, { ctrlKey: true })
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(unsnapped, 3)
  })

  it('按住 Ctrl 后又松开再松手：恢复吸附，提交吸附位置', async () => {
    await snapUp()
    key('keydown', 'Control')
    key('keyup', 'Control')
    expect(useInteractionStore.getState().snapYs.length).toBeGreaterThan(0)
    fire('pointerup', 0, 2)
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(0.725, 3)
  })

  it('⇧ 锁向拖动、松手前先松 ⇧：松手不放开被锁的那一轴（沿用最后一次移动的 ⇧，Codex #1069）', async () => {
    await setup()
    // 竖直参考线在图例左边右侧 2 px：不锁向时 x 会吸过去；⇧ 锁成竖直拖动时 x 不动
    useDocumentStore.getState().commit(literal('加参考线'), (d) => {
      d.guides.push({ axis: 'x', pos: livePanel().x + 0.6 * W + 2 * MM_PER_PX })
    })
    startElementDrag(down(), livePanel(), legendP1, layout)
    for (let i = 1; i <= 10; i++) {
      fire('pointermove', (0.5 * i) / 10, (20 * i) / 10, { shiftKey: true })
      flushPreviewFrame()
    }
    expect(useInteractionStore.getState().snapXs).toHaveLength(0)
    fire('pointerup', 0.5, 20)
    expect(overrideOf('axes_0.legend', 'loc_frac')![0]).toBeCloseTo(0.6, 3)
  })

  it('松手事件带 Ctrl（中间没收到按键事件）：同样提交未吸附位移', async () => {
    await snapUp()
    fire('pointerup', 0, 2, { ctrlKey: true })
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(unsnapped, 3)
  })
})
