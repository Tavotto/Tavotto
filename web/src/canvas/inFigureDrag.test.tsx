/**
 * 图内拖动的两件新本事（2026-09-25 用户反馈）：
 *
 * 1. **吸附**：拖图内文字 / 轴标题 / 图例 / 子图时，吸到别的元素的左中右、上中下
 *    （用户原话：拖「Vacuum」吸不到「Superconductor」，轴标题也不吸）。以前图内拖动
 *    一条候选线都没有，只有画布对象层会吸。
 * 2. **图例整体缩放**：拖图例的四个角，字号、标题字号与五个以字号为单位的间距同乘
 *    一个倍数，对角不动（`loc_frac` 钉住）。
 *
 * 钉住的事实：吸附只改位移不改写法（仍是一条 pos_frac / position）；⌘ / Ctrl、总开关、
 * 「吸附到对象」任一关掉就不吸；被拖的元素自己的后代不出线（否则一起动的东西会把
 * 自己吸走）；图例缩放一次 = 一条撤销 = 一次渲染，取消 = 零改动。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { startAxesDrag, startElementDrag, startLegendScale } from './interactions'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

// 版面：两个子图左右并排，左边那个里有上下两行左对齐的字（用户那张图 (a) 的样子）
const LEFT_POS: [number, number, number, number] = [0.1, 0.5, 0.35, 0.4]
const RIGHT_POS: [number, number, number, number] = [0.55, 0.1, 0.35, 0.3]

const leftAxes: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.35, 0.4],
  editable: [{ prop: 'position', type: 'rect', value: LEFT_POS }],
  draggable: false,
  resizable: true,
}

const rightAxes: ManifestElement = {
  gid: 'axes_1',
  role: 'axes',
  label: '子图 2',
  bbox: [0.55, 0.6, 0.35, 0.3],
  editable: [{ prop: 'position', type: 'rect', value: RIGHT_POS }],
  draggable: false,
  resizable: true,
}

const vacuum: ManifestElement = {
  gid: 'axes_0.texts_0',
  role: 'text',
  label: '文字 “Vacuum”',
  bbox: [0.2, 0.25, 0.1, 0.03],
  editable: [],
  draggable: true,
  anchor: [0.2, 0.28],
  drag_prop: 'pos_frac',
}

const superconductor: ManifestElement = {
  gid: 'axes_0.texts_1',
  role: 'text',
  label: '文字 “Superconductor”',
  bbox: [0.2, 0.32, 0.2, 0.03],
  editable: [],
  draggable: true,
  anchor: [0.2, 0.35],
  drag_prop: 'pos_frac',
}

// 子图 1 自己的标题：左边离子图左边只差 1px 多——拖子图时绝不能被它吸走
const ownTitle: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  // 宽度取 0.6：中线 / 右边都离两行字远远的，只有左边贴着子图
  bbox: [0.104, 0.05, 0.6, 0.03],
  editable: [],
  draggable: true,
  anchor: [0.104, 0.08],
  drag_prop: 'pos_frac',
}

const legendEditable = (title = '') => [
  { prop: 'fontsize', type: 'number' as const, value: 7, min: 3, max: 24 },
  { prop: 'title', type: 'text' as const, value: title },
  { prop: 'title_fontsize', type: 'number' as const, value: 8, min: 3, max: 24 },
  { prop: 'borderpad', type: 'number' as const, value: 0.4, min: 0, max: 3 },
  { prop: 'labelspacing', type: 'number' as const, value: 0.5, min: 0, max: 3 },
  { prop: 'handlelength', type: 'number' as const, value: 2, min: 0, max: 5 },
  { prop: 'handletextpad', type: 'number' as const, value: 0.8, min: 0, max: 3 },
  { prop: 'columnspacing', type: 'number' as const, value: 2, min: 0, max: 6 },
  { prop: 'ncol', type: 'number' as const, value: 1, min: 1, max: 6 },
]

const legendOf = (title = ''): ManifestElement => ({
  gid: 'axes_1.legend',
  role: 'legend',
  label: '图例',
  bbox: [0.6, 0.62, 0.2, 0.1],
  editable: legendEditable(title),
  draggable: true,
  // loc_frac 是图例框的左下角（top-origin：y = 框的下沿）
  anchor: [0.6, 0.72],
  drag_prop: 'loc_frac',
})

const manifestWith = (legend: ManifestElement): Manifest => ({
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    leftAxes,
    rightAxes,
    vacuum,
    superconductor,
    ownTitle,
    legend,
  ],
})

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

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value as
    | number[]
    | number
    | undefined

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

const fire = (
  type: 'pointermove' | 'pointerup' | 'pointercancel',
  clientX: number,
  clientY: number,
  mods: { metaKey?: boolean; ctrlKey?: boolean } = {},
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true, ...mods }))

function dragTo(x: number, y: number, mods: { metaKey?: boolean } = {}, steps = 10) {
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', (x * i) / steps, (y * i) / steps, mods)
    flushPreviewFrame()
  }
}

const tf = (gid: string) =>
  document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)?.getAttribute('transform') ?? null

/* -------------------------------- 环境搭建 -------------------------------- */

async function setup(legend = legendOf(), overrides: PanelObject['overrides'] = []) {
  const manifest = manifestWith(legend)
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    snapEnabled: true,
    snapToObjects: true,
    elementPanelId: 'p1',
    selectedGids: [],
    dragAxesWithCompanions: true,
  })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_in_figure_drag')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(overrides))
  })
  useRenderStore.getState().patch(renderKeyOf(livePanel()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(overrides),
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(livePanel()) } })
  document.body.innerHTML = `<div data-element-svg="p1">${MATPLOTLIB_SVG}</div>`
  // fixture 是单子图的真实输出：预览要的只是带 gid 的 <g>，补进同一棵 svg
  const root = document.querySelector('[data-element-svg="p1"] svg')!
  for (const gid of ['axes_0.texts_0', 'axes_1.legend']) {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    g.setAttribute('id', gid)
    root.appendChild(g)
  }
  useDocumentStore.setState({ past: [], future: [] })
  return manifest
}

beforeEach(() => resetPreview())

afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ================================== 吸附 ================================== */

describe('图内拖动吸附到别的元素', () => {
  it('拖「Vacuum」偏右 3px：左边吸回与「Superconductor」对齐，拖动中有参考线、松手收掉', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), vacuum, layout)
    dragTo(3, 0)
    expect(useInteractionStore.getState().snapXs).toHaveLength(1)
    fire('pointerup', 3, 0)

    const v = overrideOf('axes_0.texts_0', 'pos_frac') as number[]
    expect(v[0]).toBeCloseTo(0.2, 6)
    expect(useInteractionStore.getState().snapXs).toHaveLength(0)
  })

  it('按住 ⌘：不吸，落点就是光标的位移', async () => {
    await setup()
    startElementDrag(down(0, 0), livePanel(), vacuum, layout)
    dragTo(3, 0, { metaKey: true })
    fire('pointerup', 3, 0, { metaKey: true })

    const v = overrideOf('axes_0.texts_0', 'pos_frac') as number[]
    expect(v[0]).toBeCloseTo(0.2 + 3 / layout.width, 6)
  })

  it('吸附总开关或「吸附到对象」关掉：不吸', async () => {
    for (const off of [{ snapEnabled: false }, { snapToObjects: false }]) {
      await setup()
      useUiStore.setState(off)
      startElementDrag(down(0, 0), livePanel(), vacuum, layout)
      dragTo(3, 0)
      fire('pointerup', 3, 0)
      const v = overrideOf('axes_0.texts_0', 'pos_frac') as number[]
      expect(v[0]).toBeCloseTo(0.2 + 3 / layout.width, 6)
    }
  })

  it('拖子图：左边吸到另一个子图的左边；自己的标题（后代）不出线', async () => {
    await setup()
    // 子图 1 左边在 0.10，子图 2 左边在 0.55：往右拖到差 3px 的地方
    const px = (0.55 - 0.1) * layout.width - 3
    startAxesDrag(down(0, 0), livePanel(), leftAxes, layout, 'move')
    dragTo(px, 0)
    fire('pointerup', px, 0)
    expect((overrideOf('axes_0', 'position') as number[])[0]).toBeCloseTo(0.55, 4)
  })

  it('拖子图一小步：不会被只差 1px 的自己的标题吸走', async () => {
    await setup()
    startAxesDrag(down(0, 0), livePanel(), leftAxes, layout, 'move')
    dragTo(0, 12)
    fire('pointerup', 0, 12)
    // 水平方向一点都没动：若标题出线，左边会被它拽过去 1px 多
    expect((overrideOf('axes_0', 'position') as number[])[0]).toBeCloseTo(LEFT_POS[0], 4)
  })
})

/* ================================ 图例缩放 ================================ */

describe('拖图例的角 = 整体缩放', () => {
  // 沿对角线拖：宽高各放大到 1.5 倍
  const diag = (s: number) => [0.2 * layout.width * (s - 1), 0.1 * layout.height * (s - 1)] as const

  it('右下角：字号与五个间距同乘一个倍数，左上角不动（loc_frac = 新框左下角）', async () => {
    await setup()
    const [dx, dy] = diag(1.5)
    startLegendScale(down(0, 0), livePanel(), legendOf(), layout, 'se')
    dragTo(dx, dy)
    const m = /^matrix\(([^,]+),0,0,([^,]+),/.exec(tf('axes_1.legend') ?? '')
    expect(Number(m?.[1])).toBeCloseTo(1.5, 6)
    expect(Number(m?.[2])).toBeCloseTo(1.5, 6)
    fire('pointerup', dx, dy)

    expect(overrideOf('axes_1.legend', 'fontsize')).toBeCloseTo(10.5, 6)
    expect(overrideOf('axes_1.legend', 'borderpad')).toBeCloseTo(0.6, 6)
    expect(overrideOf('axes_1.legend', 'labelspacing')).toBeCloseTo(0.75, 6)
    expect(overrideOf('axes_1.legend', 'handlelength')).toBeCloseTo(3, 6)
    expect(overrideOf('axes_1.legend', 'handletextpad')).toBeCloseTo(1.2, 6)
    expect(overrideOf('axes_1.legend', 'columnspacing')).toBeCloseTo(3, 6)
    // 不是尺寸的不碰；没有标题就不写标题字号
    expect(overrideOf('axes_1.legend', 'ncol')).toBeUndefined()
    expect(overrideOf('axes_1.legend', 'title_fontsize')).toBeUndefined()
    const loc = overrideOf('axes_1.legend', 'loc_frac') as number[]
    expect(loc[0]).toBeCloseTo(0.6, 4)
    expect(loc[1]).toBeCloseTo(0.62 + 0.1 * 1.5, 4)
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
  })

  it('右上角：左下角不动，loc_frac 原值不变', async () => {
    await setup()
    const [dx, dy] = diag(1.5)
    startLegendScale(down(0, 0), livePanel(), legendOf(), layout, 'ne')
    dragTo(dx, -dy)
    fire('pointerup', dx, -dy)
    const loc = overrideOf('axes_1.legend', 'loc_frac') as number[]
    expect(loc[0]).toBeCloseTo(0.6, 4)
    expect(loc[1]).toBeCloseTo(0.72, 4)
    expect(overrideOf('axes_1.legend', 'fontsize')).toBeCloseTo(10.5, 6)
  })

  it('有标题时标题字号一起缩', async () => {
    await setup(legendOf('Legend'))
    const [dx, dy] = diag(1.5)
    startLegendScale(down(0, 0), livePanel(), legendOf('Legend'), layout, 'se')
    dragTo(dx, dy)
    fire('pointerup', dx, dy)
    expect(overrideOf('axes_1.legend', 'title_fontsize')).toBeCloseTo(12, 6)
  })

  it('基准取文档里已写下的值（上一次缩放尚未渲染回来时连着再缩）', async () => {
    await setup(legendOf(), [{ gid: 'axes_1.legend', prop: 'fontsize', value: 10 }])
    const [dx, dy] = diag(1.2)
    startLegendScale(down(0, 0), livePanel(), legendOf(), layout, 'se')
    dragTo(dx, dy)
    fire('pointerup', dx, dy)
    expect(overrideOf('axes_1.legend', 'fontsize')).toBeCloseTo(12, 6)
  })

  it('拖过头：倍数夹在属性的取值范围里（间距 borderpad 最大 3 → 最多 7.5 倍，再被 4 倍的理智上限截住）', async () => {
    await setup()
    const [dx, dy] = diag(20)
    startLegendScale(down(0, 0), livePanel(), legendOf(), layout, 'se')
    dragTo(dx, dy)
    fire('pointerup', dx, dy)
    // handlelength 2 最大 5 → 2.5 倍是最紧的那一条
    expect(overrideOf('axes_1.legend', 'handlelength')).toBeCloseTo(5, 6)
    expect(overrideOf('axes_1.legend', 'fontsize')).toBeCloseTo(17.5, 6)
  })

  it('取消：DOM 还原，文档零改动，不渲染', async () => {
    await setup()
    const [dx, dy] = diag(1.5)
    startLegendScale(down(0, 0), livePanel(), legendOf(), layout, 'se')
    dragTo(dx, dy)
    fire('pointercancel', dx, dy)
    expect(tf('axes_1.legend')).toBeNull()
    expect(livePanel().overrides).toHaveLength(0)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
  })
})
