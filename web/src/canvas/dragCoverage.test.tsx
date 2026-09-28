/**
 * matplotlib 对象拖动全族排查（ADR 0100）的前端这一侧。
 *
 * 1. **按设计不能拖的元素，拖的时候说出为什么**：从前按下、拖、松手，画面一动不动、
 *    也没有一句话——用户说「拖不动」多半就是这一刻。点一下（没拖）照常只是选中、不出声；
 *    拖起来才说一次，不写项目、不进历史。
 * 2. **挪过的插图跟着宿主走**：插图的落位从前归定位器（跟着宿主），挪过之后钉在图幅上，
 *    拖宿主时要作为随行元素写同样的位移；没挪过的不平白多一条 override。
 * 3. 锚定框（`AnchoredText` 等，role `artist`、引擎宣称可拖）走的是同一份 `inFigureMoveOf`：
 *    前端不为它另写一条路，写的是引擎给的 `drag_prop`。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { alignEntries } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject } from '@/types/document'
import {
  inFigureImmovableReason,
  startAxesDrag,
  startElementGroupMove,
  startInFigureDrag,
} from './interactions'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

const AXES_POS: [number, number, number, number] = [0.12, 0.11, 0.6, 0.7]
const INSET_POS: [number, number, number, number] = [0.5, 0.5, 0.2, 0.2]

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.6, 0.7],
  editable: [{ prop: 'position', type: 'rect', value: AXES_POS }],
  draggable: false,
  resizable: true,
}

const insetEl: ManifestElement = {
  gid: 'axes_1',
  role: 'axes',
  label: '插图 1',
  bbox: [0.5, 0.3, 0.2, 0.2],
  editable: [{ prop: 'position', type: 'rect', value: INSET_POS }],
  draggable: false,
  resizable: true,
  inset_of: 'axes_0',
}

const lineEl: ManifestElement = {
  gid: 'axes_0.lines_0',
  role: 'line',
  label: '曲线 “sin”',
  bbox: [0.1, 0.2, 0.6, 0.5],
  editable: [],
  draggable: false,
}

const anchoredEl: ManifestElement = {
  gid: 'axes_0.artists_0',
  role: 'artist',
  label: 'AnchoredText 1',
  bbox: [0.14, 0.12, 0.1, 0.06],
  editable: [],
  draggable: true,
  anchor: [0.14, 0.18],
  drag_prop: 'pos_frac',
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axesEl,
    insetEl,
    lineEl,
    anchoredEl,
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

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value as
    | number[]
    | undefined

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

const fire = (type: 'pointermove' | 'pointerup', clientX: number, clientY: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))

function dragTo(x: number, y: number, steps = 10) {
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', (x * i) / steps, (y * i) / steps)
    flushPreviewFrame()
  }
}

const status = () => {
  const s = useUiStore.getState().status
  return s ? JSON.stringify(s) : ''
}
const past = () => useDocumentStore.getState().past

const tf = (gid: string) =>
  document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)?.getAttribute('transform') ?? null

async function setup(overrides: PanelObject['overrides'] = []) {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    snapEnabled: false,
    elementPanelId: 'p1',
    selectedGids: [],
    dragAxesWithCompanions: true,
    status: null,
  })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_drag_coverage')
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
  // 插图的 <g> 嵌在宿主子图的 <g> 里（matplotlib 的 child_axes 就是这么画的）
  const host = document.querySelector('[data-element-svg="p1"] svg [id="axes_0"]')!
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  g.setAttribute('id', 'axes_1')
  host.appendChild(g)
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(() => resetPreview())

afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ========================= 按设计不能拖：说出为什么 ========================= */

describe('按设计不能拖的元素：拖起来才说为什么，不写项目', () => {
  it('拖曲线：不写 override、不进历史，toast 说「位置由数据决定」并点名是哪条', async () => {
    await setup()
    const moved = startInFigureDrag(down(0, 0), livePanel(), manifest, lineEl, layout)
    expect(moved).toBe(false)
    dragTo(30, 10)
    fire('pointerup', 30, 10)
    expect(livePanel().overrides).toHaveLength(0)
    expect(past()).toHaveLength(0)
    expect(status()).toContain('dragNotMovable.series')
    expect(status()).toContain('sin')
    expect(useInteractionStore.getState().kind).toBe('none')
  })

  it('点一下（没拖过阈值）只是选中：不出声', async () => {
    await setup()
    startInFigureDrag(down(0, 0), livePanel(), manifest, lineEl, layout)
    fire('pointermove', 1, 0)
    fire('pointerup', 1, 0)
    expect(status()).toBe('')
  })

  it('一次手势只说一次（toast 不随每个 pointermove 重设、计时不被续命）', async () => {
    await setup()
    const setStatus = vi.spyOn(useUiStore.getState(), 'setStatus')
    startInFigureDrag(down(0, 0), livePanel(), manifest, lineEl, layout)
    dragTo(40, 0, 8)
    fire('pointerup', 40, 0)
    expect(setStatus).toHaveBeenCalledTimes(1)
    setStatus.mockRestore()
  })

  it.each([
    ['line', 'series'],
    ['scatter', 'series'],
    ['bar_series', 'series'],
    ['errorbar', 'series'],
    ['stem_series', 'series'],
    ['collection', 'series'],
    ['arrow_patch', 'unsupported'],
    ['legend_text', 'legendEntry'],
    ['ticklabel', 'ticks'],
    ['axis_label', 'axisLabel3d'],
    ['text', 'pixelCoords'],
    ['axes', 'hostPlaced'],
    ['artist', 'unsupported'],
  ])('%s → %s', (role, reason) => {
    expect(inFigureImmovableReason({ ...lineEl, role })).toBe(reason)
  })

  it('箭头：属于有字标注的（arrow_of）说「拖文字」，给不出端点的独立箭头只说暂不支持', () => {
    const arrow = { ...lineEl, role: 'arrow_patch' }
    expect(inFigureImmovableReason({ ...arrow, gid: 'axes_0.texts_1.arrow', arrow_of: 'axes_0.texts_1' })).toBe(
      'annotationArrow',
    )
    // `FancyArrowPatch(path=…)` 与坐标系逆算不回去的纯箭头注释：没有文字可拖，gid 形状不作数
    expect(inFigureImmovableReason({ ...arrow, gid: 'axes_0.arrows_2' })).toBe('unsupported')
    expect(inFigureImmovableReason({ ...arrow, gid: 'axes_0.texts_3.arrow' })).toBe('unsupported')
  })
})

/* ===================== 锚定框走同一份 inFigureMoveOf ===================== */

describe('锚定框（role artist、引擎宣称可拖）', () => {
  it('拖动写引擎给的 drag_prop：锚点 + 位移，一条撤销', async () => {
    await setup()
    const moved = startInFigureDrag(down(0, 0), livePanel(), manifest, anchoredEl, layout)
    expect(moved).toBe(true)
    dragTo(40, 20)
    // 预览跟手：平移的是它自己那个 <g>（引擎给锚定框开了 SVG 组，ADR 0100）
    fire('pointerup', 40, 20)
    const v = overrideOf(anchoredEl.gid, 'pos_frac')!
    expect(v[0]).toBeCloseTo(0.14 + 40 / layout.width, 4)
    expect(v[1]).toBeCloseTo(0.18 + 20 / layout.height, 4)
    expect(past()).toHaveLength(1)
    expect(status()).toBe('')
  })
})

/* ========================= 挪过的插图跟着宿主走 ========================= */

describe('插图：挪过的跟着宿主走，没挪过的由定位器带着走', () => {
  it('拖宿主：挪过的插图写同样的位移（bottom-origin），一条撤销', async () => {
    await setup([{ gid: 'axes_1', prop: 'position', value: [...INSET_POS] }])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    // 插图嵌在宿主的 <g> 里：宿主一平移它已经跟着动了，不单独预览（不然是 2Δ）
    expect(tf('axes_1')).toBeNull()
    expect(tf('axes_0')).not.toBeNull()
    fire('pointerup', 40, 20)
    const [dfx, dfy] = [40 / layout.width, 20 / layout.height]
    const v = overrideOf('axes_1', 'position')!
    expect(v[0]).toBeCloseTo(INSET_POS[0] + dfx, 4)
    expect(v[1]).toBeCloseTo(INSET_POS[1] - dfy, 4)
    expect(v[2]).toBeCloseTo(INSET_POS[2], 9)
    expect(past()).toHaveLength(1)
  })

  it('插图套插图：外层没挪过（跟定位器走），挪过的内层照样写同样的位移；插图里挪过的标题也跟', async () => {
    const inner: ManifestElement = {
      ...insetEl,
      gid: 'axes_2',
      label: '插图 2',
      bbox: [0.55, 0.32, 0.08, 0.08],
      editable: [{ prop: 'position', type: 'rect', value: [0.55, 0.6, 0.08, 0.08] }],
      inset_of: 'axes_1',
    }
    manifest.elements.push(inner)
    try {
      await setup([
        { gid: 'axes_2', prop: 'position', value: [0.55, 0.6, 0.08, 0.08] },
        { gid: 'axes_1.title', prop: 'pos_frac', value: [0.6, 0.3] },
      ])
      startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
      dragTo(40, 20)
      fire('pointerup', 40, 20)
      const [dfx, dfy] = [40 / layout.width, 20 / layout.height]
      expect(overrideOf('axes_1', 'position'), '没挪过的外层不多写').toBeUndefined()
      expect(overrideOf('axes_2', 'position')![0]).toBeCloseTo(0.55 + dfx, 4)
      expect(overrideOf('axes_2', 'position')![1]).toBeCloseTo(0.6 - dfy, 4)
      expect(overrideOf('axes_1.title', 'pos_frac')![0]).toBeCloseTo(0.6 + dfx, 4)
      expect(past()).toHaveLength(1)
    } finally {
      manifest.elements.pop()
    }
  })

  it('插图自己的随行色条轴（平级、固定落位）跟着宿主走，且单独预览', async () => {
    const cbax: ManifestElement = {
      ...axesEl,
      gid: 'axes_5',
      label: '色条轴',
      bbox: [0.72, 0.3, 0.02, 0.2],
      editable: [{ prop: 'position', type: 'rect', value: [0.72, 0.5, 0.02, 0.2] }],
      follow_gids: undefined,
    }
    const insetWithCbar = { ...insetEl, follow_gids: ['axes_5'] }
    const i = manifest.elements.indexOf(insetEl)
    manifest.elements.splice(i, 1, insetWithCbar)
    manifest.elements.push(cbax)
    try {
      await setup()
      const svg = document.querySelector('[data-element-svg="p1"] svg')!
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
      g.setAttribute('id', 'axes_5')
      svg.appendChild(g)
      startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
      dragTo(40, 20)
      expect(tf('axes_5')).not.toBeNull()
      fire('pointerup', 40, 20)
      expect(overrideOf('axes_5', 'position')![0]).toBeCloseTo(0.72 + 40 / layout.width, 4)
    } finally {
      manifest.elements.pop()
      manifest.elements.splice(i, 1, insetEl)
    }
  })

  it('没挪过的插图不平白多一条 override', async () => {
    await setup()
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    fire('pointerup', 40, 20)
    expect(overrideOf('axes_1', 'position')).toBeUndefined()
    expect(livePanel().overrides).toHaveLength(1)
  })

  it('多选整组平移（宿主 + 锚定框）同样带上挪过的插图', async () => {
    await setup([{ gid: 'axes_1', prop: 'position', value: [...INSET_POS] }])
    const entries = alignEntries(livePanel(), manifest, ['axes_0', anchoredEl.gid])
    expect(entries).toHaveLength(2)
    startElementGroupMove(down(0, 0), livePanel(), entries, layout)
    dragTo(40, 20)
    fire('pointerup', 40, 20)
    const v = overrideOf('axes_1', 'position')!
    expect(v[0]).toBeCloseTo(INSET_POS[0] + 40 / layout.width, 4)
    expect(past()).toHaveLength(1)
  })
})
