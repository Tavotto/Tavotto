/**
 * 拖动子图时的**联动**：与它一体的东西必须跟着走。
 *
 * 0.6.0 的回归：figure 锚定的 override（pos_frac / loc_frac / endpoints_frac）
 * 在几何变动后会被引擎重放（FigS3 事故的修法，见 overrides.py），于是**被用户
 * 手动摆过位置**的标题 / 轴标签 / 图例被钉死在原来的 figure 坐标上——子图走了
 * 它们不走。色条轴与 twinx 的孪生轴则从来就是平级的另一个 axes，更不会跟。
 *
 * 修法是把存着的锚点值本身加上同一个位移（而不是去掉那个重放——那会把
 * FigS3 放回来），所以要钉住的事实是：
 *   1. 被摆过的后代跟着走，位移与子图一致；
 *   2. 没被摆过的后代**不平白多出一条 override**（它们本来就跟着 Axes 走）；
 *   3. manifest 点名的随行 axes（色条 / 孪生轴）跟着走，且预览期就跟手；
 *   4. 子图被钳在画布内时，随行元素用**净位移**，一组东西不被拆散；
 *   5. 全部进同一次 commit：一条撤销、一次权威渲染；
 *   6. 设置关掉后只动子图本身。
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
import { startAxesDrag } from './interactions'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

const AXES_POS: [number, number, number, number] = [0.12, 0.11, 0.6, 0.7]
const CBAR_POS: [number, number, number, number] = [0.93, 0.12, 0.04, 0.76]

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.6, 0.7],
  editable: [{ prop: 'position', type: 'rect', value: AXES_POS }],
  draggable: false,
  resizable: true,
  follow_gids: ['axes_1'],
}

const cbarEl: ManifestElement = {
  gid: 'axes_1',
  role: 'axes',
  label: '色条轴',
  bbox: [0.93, 0.11, 0.04, 0.77],
  editable: [{ prop: 'position', type: 'rect', value: CBAR_POS }],
  draggable: false,
  resizable: true,
  is_colorbar: true,
  colorbar_gid: 'axes_1.colorbar',
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

const xlabelEl: ManifestElement = {
  gid: 'axes_0.xlabel',
  role: 'axis_label',
  label: 'X 轴标签',
  bbox: [0.4, 0.9, 0.2, 0.06],
  editable: [],
  draggable: true,
  anchor: [0.5, 0.94],
  drag_prop: 'pos_frac',
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axesEl,
    cbarEl,
    titleEl,
    xlabelEl,
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

const fire = (
  type: 'pointermove' | 'pointerup' | 'pointercancel',
  clientX: number,
  clientY: number,
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))

function dragTo(x: number, y: number, steps = 20) {
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', (x * i) / steps, (y * i) / steps)
    flushPreviewFrame()
  }
}

const tf = (gid: string) =>
  document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)?.getAttribute('transform') ?? null

/** 屏幕像素 → 内容分数（zoom = 1） */
const dfxOf = (px: number) => px / layout.width
const dfyOf = (px: number) => px / layout.height

/* -------------------------------- 环境搭建 -------------------------------- */

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
  })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_axes_companion')
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
  // fixture 是单子图的真实输出，没有色条轴那一组。预览平移要的只是一个带
  // gid 的 <g>，补一个空组足够验证「有没有跟手」；**必须补进同一棵 svg**
  // ——预览取的是面板容器里的第一个 <svg>，挂在外面的那棵找不到。
  const root = document.querySelector('[data-element-svg="p1"] svg')!
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  g.setAttribute('id', 'axes_1')
  root.appendChild(g)
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(() => {
  resetPreview()
})

afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ============================== 后代标签联动 ============================== */

describe('被手动摆过位置的后代跟着子图走', () => {
  it('标题的 pos_frac 加上同一个位移，图例式的 loc_frac 同理', async () => {
    await setup([
      { gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] },
      { gid: 'axes_0.legend', prop: 'loc_frac', value: [0.7, 0.2] },
    ])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    fire('pointerup', 40, 20)

    const [dfx, dfy] = [dfxOf(40), dfyOf(20)]
    // pos_frac / loc_frac 是 top-origin：位移直接加
    expect(overrideOf('axes_0.title', 'pos_frac')![0]).toBeCloseTo(0.4 + dfx, 4)
    expect(overrideOf('axes_0.title', 'pos_frac')![1]).toBeCloseTo(0.09 + dfy, 4)
    expect(overrideOf('axes_0.legend', 'loc_frac')![0]).toBeCloseTo(0.7 + dfx, 4)
    expect(overrideOf('axes_0.legend', 'loc_frac')![1]).toBeCloseTo(0.2 + dfy, 4)
    // position 是 bottom-origin：屏幕向下 = y 变小
    expect(overrideOf('axes_0', 'position')![0]).toBeCloseTo(AXES_POS[0] + dfx, 4)
    expect(overrideOf('axes_0', 'position')![1]).toBeCloseTo(AXES_POS[1] - dfy, 4)
  })

  it('独立箭头的 endpoints_frac 两个端点一起走', async () => {
    await setup([
      { gid: 'axes_0.arrows_3', prop: 'endpoints_frac', value: [0.2, 0.4, 0.5, 0.2] },
    ])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    fire('pointerup', 40, 20)

    const [dfx, dfy] = [dfxOf(40), dfyOf(20)]
    const v = overrideOf('axes_0.arrows_3', 'endpoints_frac')!
    expect(v[0]).toBeCloseTo(0.2 + dfx, 4)
    expect(v[1]).toBeCloseTo(0.4 + dfy, 4)
    expect(v[2]).toBeCloseTo(0.5 + dfx, 4)
    expect(v[3]).toBeCloseTo(0.2 + dfy, 4)
  })

  it('没被摆过的后代不平白多出一条 override（它们本来就跟着 Axes 走）', async () => {
    await setup([{ gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] }])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    fire('pointerup', 40, 20)

    expect(overrideOf('axes_0.xlabel', 'pos_frac')).toBeUndefined()
    // 只有：子图 position + 标题 pos_frac + 色条 position
    expect(livePanel().overrides).toHaveLength(3)
  })

  it('不属于这个子图的元素一动不动', async () => {
    await setup([
      { gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] },
      { gid: 'fig.texts_0', prop: 'pos_frac', value: [0.5, 0.5] },
      // 前缀像但不是后代：axes_00 不该被 axes_0 的拖动带走
      { gid: 'axes_00.title', prop: 'pos_frac', value: [0.3, 0.3] },
    ])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    fire('pointerup', 40, 20)

    expect(overrideOf('fig.texts_0', 'pos_frac')).toEqual([0.5, 0.5])
    expect(overrideOf('axes_00.title', 'pos_frac')).toEqual([0.3, 0.3])
  })
})

/* ============================== 随行 axes 联动 ============================= */

describe('manifest 点名的随行 axes（色条 / 孪生轴）', () => {
  it('跟着走，且预览期就跟手（它是平级的另一个 <g>，得单独平移）', async () => {
    await setup()
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    // 宿主与色条都挂上了临时 transform
    expect(tf('axes_0')).toMatch(/^translate\(/)
    expect(tf('axes_1')).toMatch(/^translate\(/)
    fire('pointerup', 40, 20)

    const v = overrideOf('axes_1', 'position')!
    expect(v[0]).toBeCloseTo(CBAR_POS[0] + dfxOf(40), 4)
    expect(v[1]).toBeCloseTo(CBAR_POS[1] - dfyOf(20), 4)
    expect(v[2]).toBeCloseTo(CBAR_POS[2], 4) // 宽高不动
    expect(v[3]).toBeCloseTo(CBAR_POS[3], 4)
  })

  it('取消拖动：随行元素也一条都不落下', async () => {
    await setup([{ gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] }])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    fire('pointercancel', 40, 20)

    expect(overrideOf('axes_0', 'position')).toBeUndefined()
    expect(overrideOf('axes_1', 'position')).toBeUndefined()
    expect(overrideOf('axes_0.title', 'pos_frac')).toEqual([0.4, 0.09])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(tf('axes_1')).toBeNull()
  })
})

/* ============================== 钳位与整组刚性 ============================= */

describe('子图被钳在画布内时随行元素用净位移', () => {
  it('拖过头：子图停在 1-w，色条也只走那么多（一组东西不被拆散）', async () => {
    await setup([{ gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] }])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(layout.width, 0) // 想走满 1，实际只能走到 1 - 0.8
    fire('pointerup', layout.width, 0)

    const net = 1 - AXES_POS[2] - AXES_POS[0] // 0.08
    expect(overrideOf('axes_0', 'position')![0]).toBeCloseTo(AXES_POS[0] + net, 4)
    expect(overrideOf('axes_1', 'position')![0]).toBeCloseTo(CBAR_POS[0] + net, 4)
    expect(overrideOf('axes_0.title', 'pos_frac')![0]).toBeCloseTo(0.4 + net, 4)
  })
})

/* =============================== 历史与渲染 =============================== */

describe('一次拖动 = 一条撤销 = 一次权威渲染', () => {
  it('松手只发一次渲染，撤销一步把所有随行改动一起收回', async () => {
    await setup([{ gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] }])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    expect(engineRender).not.toHaveBeenCalled()
    fire('pointerup', 40, 20)

    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(useDocumentStore.getState().past).toHaveLength(1)

    useDocumentStore.getState().undo()
    expect(overrideOf('axes_0', 'position')).toBeUndefined()
    expect(overrideOf('axes_1', 'position')).toBeUndefined()
    expect(overrideOf('axes_0.title', 'pos_frac')).toEqual([0.4, 0.09])
  })
})

/* ================================ 开关关掉 ================================ */

describe('设置里关掉联动', () => {
  it('只动子图本身，随行元素一条都不写', async () => {
    await setup([{ gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] }])
    useUiStore.setState({ dragAxesWithCompanions: false })

    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20)
    expect(tf('axes_1')).toBeNull()
    fire('pointerup', 40, 20)

    expect(overrideOf('axes_0', 'position')).toBeDefined()
    expect(overrideOf('axes_1', 'position')).toBeUndefined()
    expect(overrideOf('axes_0.title', 'pos_frac')).toEqual([0.4, 0.09])
  })
})

/* ================================ 缩放不联动 =============================== */

describe('缩放子图不联动', () => {
  it('随行元素该缩到哪里没有可信答案，交给 matplotlib 重排', async () => {
    await setup([{ gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.09] }])
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'e')
    dragTo(40, 0)
    fire('pointerup', 40, 0)

    expect(overrideOf('axes_0', 'position')![2]).toBeGreaterThan(AXES_POS[2])
    expect(overrideOf('axes_1', 'position')).toBeUndefined()
    expect(overrideOf('axes_0.title', 'pos_frac')).toEqual([0.4, 0.09])
  })
})
