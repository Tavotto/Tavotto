/**
 * 方向键微调（ADR 0093）。2026-09-26 真浏览器实测的起点：
 *
 * - 快速编辑里选中图例 / 标题 / 子图按方向键：纹丝不动（键被吃掉）；
 * - 画布排版里进图内编辑、选中图例按方向键：**整张图**在版上挪了，图例没动；
 * - 画布对象每按一下一条撤销；焦点在素材卡上按方向键，素材卡换了焦点、画布上的面板也挪了。
 *
 * 这里钉住：步长（页面 mm，⇧ / ⌥ 两档）、一段连续按键 = 一条撤销（点按与按住都算，按住时
 * 系统连发的首延迟长于停顿阈值也不断段）、图内元素走与拖动同一套移动规则且这一段里零渲染、
 * 净位移为零不写、锁定与不可移动的元素不动并说出来、焦点在自己用方向键的控件里时不推画布、
 * 几何权威缺席时不拿旧几何写文档。
 */
import { createElement } from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { runUndoRedo, useKeyboard } from '@/hooks/useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject, type ShapeObject } from '@/types/document'
import { NUDGE_QUIET_MS, NUDGE_STEP_MM, nudgeActive, resetNudge } from './nudge'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

// 面板在页面上占 100 × 80 mm（原生 200 × 160，缩放 0.5）：步长换成分数时按**页面**大小算
const PAGE_W = 100
const PAGE_H = 80

const axes: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.6, 0.6],
  editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.3, 0.6, 0.6] }],
  draggable: false,
  resizable: true,
}

const title: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.3, 0.05, 0.2, 0.03],
  editable: [],
  draggable: true,
  anchor: [0.3, 0.08],
  drag_prop: 'pos_frac',
}

const legend: ManifestElement = {
  gid: 'axes_0.legend',
  role: 'legend',
  label: '图例',
  bbox: [0.5, 0.5, 0.15, 0.1],
  editable: [],
  draggable: true,
  anchor: [0.5, 0.6],
  drag_prop: 'loc_frac',
}

const line: ManifestElement = {
  gid: 'axes_0.lines_0',
  role: 'line',
  label: '曲线 1',
  bbox: [0.1, 0.1, 0.6, 0.6],
  editable: [],
  draggable: false,
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [200, 160],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axes,
    title,
    legend,
    line,
  ],
}

const panelOf = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 20,
    y: 30,
    w: PAGE_W,
    h: PAGE_H,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 200,
    nativeH: 160,
    script: 'fig.py',
    overrides: [],
    ...over,
  }) as unknown as PanelObject

const rect = (id: string, x: number, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x,
  y: 0,
  w: 10,
  h: 10,
  strokePt: 1,
  color: '#111111',
  fill: null,
  ...over,
})

const obj = (id: string) => useDocumentStore.getState().doc.objects.find((o) => o.id === id)!
const livePanel = () => obj('p1') as PanelObject
const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value as number[] | undefined
const past = () => useDocumentStore.getState().past
const tf = (gid: string) =>
  document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)?.getAttribute('transform') ?? null
const status = () => {
  const s = useUiStore.getState().status
  return s ? JSON.stringify(s) : ''
}

/* -------------------------------- 键盘桩 -------------------------------- */

let root: Root | null = null
function Harness() {
  useKeyboard()
  return null
}

type Mods = { shiftKey?: boolean; altKey?: boolean; metaKey?: boolean; repeat?: boolean }

function keydown(key: string, mods: Mods = {}, target: EventTarget = window) {
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...mods }))
  })
}
function keyup(key: string) {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }))
  })
}
/** 点按一下：按下、松开 */
function tap(key: string, mods: Mods = {}) {
  keydown(key, mods)
  keyup(key)
}
/** 停顿够了，这一段收尾 */
function settle() {
  act(() => {
    vi.advanceTimersByTime(NUDGE_QUIET_MS + 1)
  })
}

/* -------------------------------- 环境搭建 -------------------------------- */

async function setup(opts: { panel?: Partial<PanelObject>; extra?: (ShapeObject | PanelObject)[] } = {}) {
  engineRender.mockReset()
  // 权威渲染一律悬着：这里量的是「这一段里发没发、提交时发几次」，不需要它回来
  engineRender.mockReturnValue(new Promise(() => {}))
  resetNudge()
  resetPreview()
  resetGestureCoordinator()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    snapEnabled: true,
    snapToObjects: true,
    elementPanelId: null,
    selectedGids: [],
    status: null,
    dragAxesWithCompanions: true,
  })
  useWorkspaceStore.getState().clear()
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_arrow_nudge')
  useDocumentStore.getState().commit(literal('加对象'), (d) => {
    d.objects.push(panelOf(opts.panel), ...(opts.extra ?? []))
  })
  seedRender()
  document.body.innerHTML = `<div data-element-svg="p1">${MATPLOTLIB_SVG}</div>`
  const svg = document.querySelector('[data-element-svg="p1"] svg')!
  for (const gid of [title.gid, legend.gid]) {
    if (svg.querySelector(`[id="${gid}"]`)) continue
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    g.setAttribute('id', gid)
    svg.appendChild(g)
  }
  useDocumentStore.setState({ past: [], future: [] })
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  act(() => root!.render(createElement(Harness)))
}

/** 当前文档这一版的权威渲染到了（几何权威 = 文档的 overrides 与渲染对得上） */
function seedRender() {
  const p = livePanel()
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(p.overrides),
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(p) } })
}

function editFigure(gids: string[]) {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: gids })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  resetNudge()
  act(() => root?.unmount())
  root = null
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
  vi.useRealTimers()
})

/* ================================ 画布对象 ================================ */

describe('画布对象：步长与撤销', () => {
  it('普通 0.5 mm、⇧ 5 mm、⌥ 0.1 mm，单位是页面 mm', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    tap('ArrowRight')
    expect(obj('r').x).toBeCloseTo(NUDGE_STEP_MM.base, 9)
    tap('ArrowRight', { shiftKey: true })
    expect(obj('r').x).toBeCloseTo(NUDGE_STEP_MM.base + NUDGE_STEP_MM.large, 9)
    tap('ArrowLeft', { altKey: true })
    expect(obj('r').x).toBeCloseTo(0.5 + 5 - 0.1, 9)
    tap('ArrowDown', { altKey: true })
    expect(obj('r').y).toBeCloseTo(0.1, 9)
    expect(NUDGE_STEP_MM).toEqual({ base: 0.5, large: 5, fine: 0.1 })
  })

  it('连着点按 + 按住（连发首延迟长于停顿阈值）= 一条撤销，撤销一次回到原位', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    for (let i = 0; i < 3; i++) {
      tap('ArrowRight')
      act(() => vi.advanceTimersByTime(NUDGE_QUIET_MS - 100))
    }
    // 按住：第一下之后系统要等一会儿才开始连发——这段时间里这一段不能断
    keydown('ArrowRight')
    act(() => vi.advanceTimersByTime(NUDGE_QUIET_MS * 2))
    expect(nudgeActive()).toBe(true)
    for (let i = 0; i < 4; i++) keydown('ArrowRight', { repeat: true })
    keyup('ArrowRight')
    expect(past()).toHaveLength(0) // 还在这一段里
    settle()
    expect(nudgeActive()).toBe(false)
    expect(obj('r').x).toBeCloseTo(8 * 0.5, 9)
    expect(past()).toHaveLength(1)

    keydown('z', { metaKey: true })
    expect(obj('r').x).toBe(0)
  })

  it('停顿之后再按是新的一段：两条撤销', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    tap('ArrowRight')
    settle()
    tap('ArrowRight')
    settle()
    expect(past()).toHaveLength(2)
  })

  it('这一段里按 ⌘Z：先收掉这一段再撤销它（不被当成「拖动中」挡掉）', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    tap('ArrowRight')
    tap('ArrowRight')
    keydown('z', { metaKey: true })
    expect(obj('r').x).toBe(0)
    expect(nudgeActive()).toBe(false)
    expect(useDocumentStore.getState().future).toHaveLength(1)
  })

  it('这一段里点撤销按钮 / 菜单（不经键盘）：同样先收掉这一段再撤销它', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    tap('ArrowRight')
    act(() => runUndoRedo(false))
    expect(obj('r').x).toBe(0)
    expect(nudgeActive()).toBe(false)
  })

  it('这一段里按别的键（⌘D 复制）：先落定这一段，复制不并进移动那条撤销', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    tap('ArrowRight')
    keydown('d', { metaKey: true })
    expect(past().length).toBe(2)
  })

  it('一去一回净位移为零：不留历史', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    tap('ArrowRight')
    tap('ArrowLeft')
    settle()
    expect(obj('r').x).toBe(0)
    expect(past()).toHaveLength(0)
  })

  it('与鼠标拖动同一套可移动判据：锁定的、隐藏的不动', async () => {
    await setup({ extra: [rect('a', 0), rect('l', 20, { locked: true }), rect('h', 40, { hidden: true })] })
    useSelectionStore.getState().set(['a', 'l', 'h'])
    tap('ArrowRight', { shiftKey: true })
    settle()
    expect(obj('a').x).toBe(5)
    expect(obj('l').x).toBe(20)
    expect(obj('h').x).toBe(40)
  })

  it('快速编辑里没选图内元素：面板在版上的 x/y 不动，也不进历史', async () => {
    await setup()
    useSelectionStore.getState().set(['p1'])
    useWorkspaceStore.getState().enterFastEdit('p1')
    tap('ArrowRight')
    settle()
    expect(livePanel().x).toBe(20)
    expect(past()).toHaveLength(0)
  })
})

/* ================================ 焦点分派 ================================ */

describe('焦点：方向键归谁', () => {
  const host = (html: string) => {
    const div = document.createElement('div')
    div.innerHTML = html
    document.body.appendChild(div)
    return div
  }

  it('焦点在自己用方向键的控件里（列表 / 树 / 单选组 / 菜单）：不推画布', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    const h = host(
      '<div role="listbox"><div role="option" tabindex="0" id="o"></div></div>' +
        '<ul role="tree"><li role="treeitem" tabindex="0" id="t"></li></ul>' +
        '<div role="radiogroup"><button role="radio" id="rg"></button></div>' +
        '<div role="menu"><div role="menuitem" tabindex="0" id="m"></div></div>',
    )
    for (const id of ['o', 't', 'rg', 'm']) keydown('ArrowRight', {}, h.querySelector(`#${id}`)!)
    settle()
    expect(obj('r').x).toBe(0)
  })

  it('控件已经处理过这一下（preventDefault，如素材卡换焦点）：不推画布', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    const h = host('<div tabindex="0" id="card"></div>')
    const card = h.querySelector('#card')!
    card.addEventListener('keydown', (e) => e.preventDefault())
    keydown('ArrowRight', {}, card)
    settle()
    expect(obj('r').x).toBe(0)
  })

  it('输入框里：归输入框', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    const h = host('<input id="i" />')
    keydown('ArrowRight', {}, h.querySelector('#i')!)
    settle()
    expect(obj('r').x).toBe(0)
  })

  it('对照：焦点在工具条按钮上（刚点完对齐）照样微调', async () => {
    await setup({ extra: [rect('r', 0)] })
    useSelectionStore.getState().set(['r'])
    const h = host('<div role="toolbar"><button id="b"></button></div>')
    keydown('ArrowRight', {}, h.querySelector('#b')!)
    settle()
    expect(obj('r').x).toBeCloseTo(0.5, 9)
  })
})

/* ================================ 图内元素 ================================ */

describe('图内元素：与拖动同一套移动规则', () => {
  it('选中标题按 4 下 →：这一段里只动预览、零渲染；收尾一条 pos_frac、一条撤销、一次渲染，面板不动', async () => {
    await setup()
    useSelectionStore.getState().set(['p1']) // 图内编辑态里面板本身也在选区里：它不该被推
    editFigure([title.gid])
    for (let i = 0; i < 4; i++) tap('ArrowRight')
    act(() => flushPreviewFrame())

    expect(livePanel().x).toBe(20)
    expect(overrideOf(title.gid, 'pos_frac')).toBeUndefined()
    expect(tf(title.gid)).toMatch(/^translate\(/)
    expect(engineRender).not.toHaveBeenCalled()
    expect(useInteractionStore.getState().nudge).toEqual({ dx: 2, dy: 0 })

    settle()
    const v = overrideOf(title.gid, 'pos_frac')!
    // 4 × 0.5 mm，按面板在页面上的宽度（100 mm）折成分数
    expect(v[0]).toBeCloseTo(0.3 + 2 / PAGE_W, 9)
    expect(v[1]).toBeCloseTo(0.08, 9)
    expect(livePanel().x).toBe(20)
    expect(past()).toHaveLength(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(useInteractionStore.getState().nudge).toBeNull()

    keydown('z', { metaKey: true })
    expect(overrideOf(title.gid, 'pos_frac')).toBeUndefined()
  })

  it('↓ 是页面向下：内容分数 y 增大；子图（bottom-origin 的 position）y 减小', async () => {
    await setup()
    editFigure([title.gid])
    tap('ArrowDown', { shiftKey: true })
    settle()
    expect(overrideOf(title.gid, 'pos_frac')![1]).toBeCloseTo(0.08 + 5 / PAGE_H, 9)

    seedRender()
    editFigure([axes.gid])
    tap('ArrowDown', { shiftKey: true })
    settle()
    const pos = overrideOf(axes.gid, 'position')!
    expect(pos[0]).toBeCloseTo(0.1, 4)
    expect(pos[1]).toBeCloseTo(0.3 - 5 / PAGE_H, 4)
  })

  it('一去一回净位移为零：不写 override（与拖回原处同一条 GEO-07）', async () => {
    await setup()
    editFigure([legend.gid])
    tap('ArrowUp')
    tap('ArrowDown')
    settle()
    expect(livePanel().overrides).toHaveLength(0)
    expect(past()).toHaveLength(0)
    expect(tf(legend.gid)).toBeNull()
  })

  it('多选：整组平移，一条撤销', async () => {
    await setup()
    editFigure([title.gid, legend.gid])
    tap('ArrowLeft')
    tap('ArrowLeft')
    settle()
    expect(overrideOf(title.gid, 'pos_frac')![0]).toBeCloseTo(0.3 - 1 / PAGE_W, 9)
    expect(overrideOf(legend.gid, 'loc_frac')![0]).toBeCloseTo(0.5 - 1 / PAGE_W, 9)
    expect(past()).toHaveLength(1)
  })

  it('锁定的图内元素、不能拖的元素：不动，并说出来', async () => {
    await setup({ panel: { lockedGids: [title.gid] } as Partial<PanelObject> })
    editFigure([title.gid])
    tap('ArrowRight')
    settle()
    expect(livePanel().overrides).toHaveLength(0)
    expect(status()).toContain('nudgeNotMovable')

    useUiStore.setState({ status: null })
    editFigure([line.gid])
    tap('ArrowRight')
    settle()
    expect(livePanel().overrides).toHaveLength(0)
    expect(livePanel().x).toBe(20)
    expect(status()).toContain('nudgeNotMovable')
  })

  it('上一段的权威渲染还没回来：不拿旧几何写文档，先记位移，权威一到再移动并提交', async () => {
    await setup()
    editFigure([title.gid])
    tap('ArrowRight')
    settle()
    expect(overrideOf(title.gid, 'pos_frac')![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W, 9)

    // 第二段：文档已经带着第一段的 override，渲染还悬着 = 几何权威缺席
    tap('ArrowRight')
    tap('ArrowRight')
    expect(useInteractionStore.getState().nudge).toEqual({ dx: 1, dy: 0 })
    settle()
    expect(overrideOf(title.gid, 'pos_frac')![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W, 9)
    expect(past()).toHaveLength(1)

    // 这一版的权威到了：第二段接着第一段的落点走，并按停顿过的状态直接提交
    act(() => seedRender())
    expect(overrideOf(title.gid, 'pos_frac')![0]).toBeCloseTo(0.3 + 1.5 / PAGE_W, 9)
    expect(past()).toHaveLength(2)
    expect(nudgeActive()).toBe(false)
  })

  it('权威缺席时被要求立刻收尾（按了别的键）：放弃这一段，文档不变', async () => {
    await setup()
    editFigure([title.gid])
    tap('ArrowRight')
    settle()
    tap('ArrowRight')
    keydown('Escape')
    act(() => seedRender())
    expect(overrideOf(title.gid, 'pos_frac')![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W, 9)
    expect(past()).toHaveLength(1)
  })

  it('这一段里换了选中的元素：先按旧选区提交', async () => {
    await setup()
    editFigure([title.gid])
    tap('ArrowRight')
    act(() => useUiStore.getState().setSelectedGid(legend.gid))
    expect(overrideOf(title.gid, 'pos_frac')![0]).toBeCloseTo(0.3 + 0.5 / PAGE_W, 9)
    expect(nudgeActive()).toBe(false)
  })
})
