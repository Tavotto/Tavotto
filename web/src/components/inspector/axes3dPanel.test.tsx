/**
 * 三维子图的属性页（审计 T24）。
 *
 * 钉住的合同：
 *   1. 关掉轴箭头时**只剩一行开启入口**——颜色 / 线宽 / 大小一并收起
 *      （连「更多」展开也数不到）；背景面板同理；
 *   2. 组标题「轴箭头」与那个开关不再同名（开关叫「显示箭头」）；
 *   3. 投影方式是透视 / 正交两个预览格，不是文字下拉；写入值仍是 proj_type；
 *   4. 方向示意随角度重画、没有动画，且**与 matplotlib 算出来的方向一致**
 *      （期望值取自 `proj3d._view_axes`，见 `lib/viewAngle.test.ts` 的出处）。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* -------------------------------- 测试数据 -------------------------------- */

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

/** 与 engine/manifest.py `_axes3d_fields` 同形 */
const fields3d = (over: { arrows?: boolean; panes?: boolean } = {}): EditableField[] => [
  f('position', 'rect', [0.12, 0.11, 0.75, 0.77]),
  f('visible', 'bool', true),
  f('elev', 'number', 30, { min: -90, max: 90, step: 5, unit: '°', group: '视角' }),
  f('azim', 'number', -60, { min: -180, max: 180, step: 5, unit: '°', group: '视角' }),
  f('roll', 'number', 0, { min: -180, max: 180, step: 5, unit: '°', group: '视角' }),
  f('axline_color', 'color', '#000000', { group: '坐标轴' }),
  f('axline_width', 'number', 0.8, { min: 0.1, max: 5, step: 0.1, unit: 'pt', group: '坐标轴' }),
  f('pane_visible', 'bool', over.panes ?? true, { group: '坐标轴' }),
  f('pane_color', 'color', '#F2F2F2', { group: '坐标轴' }),
  f('grid_visible', 'bool', true, { group: '坐标轴' }),
  f('proj_type', 'enum', 'persp', { options: ['persp', 'ortho'], group: '视角' }),
  f('axis_arrows', 'bool', over.arrows ?? false, { group: '轴箭头' }),
  f('arrow_color', 'color', '#000000', { group: '轴箭头' }),
  f('arrow_width', 'number', 0.8, { min: 0.1, max: 3, step: 0.1, unit: 'pt', group: '轴箭头' }),
  f('arrow_head', 'number', 6, { min: 2, max: 20, step: 0.5, group: '轴箭头' }),
]

const axes3dEl = (over: { arrows?: boolean; panes?: boolean } = {}): ManifestElement =>
  ({
    gid: 'axes_0',
    role: 'axes3d',
    label: '子图 1',
    bbox: [0.12, 0.11, 0.75, 0.77],
    draggable: true,
    resizable: true,
    editable: fields3d(over),
  }) as unknown as ManifestElement

const manifestOf = (el: ManifestElement = axes3dEl()): Manifest =>
  ({ rev: 1, size_mm: [101.6, 76.2], elements: [el] }) as unknown as Manifest

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
    script: 'fig3d.py',
    overrides,
  }) as unknown as PanelObject

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let host: HTMLDivElement

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

async function mount(opts: { manifest?: Manifest; overrides?: PanelObject['overrides'] } = {}) {
  const { manifest = manifestOf(), overrides = [] } = opts
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_axes3d')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(overrides))
  })
  const key = renderKeyOf(panelOf(overrides))
  useRenderStore.getState().patch(key, {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    lastPatches: JSON.stringify(overrides.map((o) => [o.gid, o.prop, o.value])),
    status: 'ready',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': key } })
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0'] })
  host = document.createElement('div')
  document.body.appendChild(host)
  const svgHost = document.createElement('div')
  svgHost.setAttribute('data-element-svg', 'p1')
  svgHost.innerHTML = MATPLOTLIB_SVG
  document.body.appendChild(svgHost)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
}

const buttons = () => Array.from(host.querySelectorAll('button'))
const byText = (text: string) => buttons().find((b) => b.textContent?.trim() === text)
const rowOf = (prop: string) => host.querySelector(`[data-prop="${prop}"]`) as HTMLElement | null
const openMore = async () => {
  const more = byText('更多')
  if (more?.getAttribute('aria-expanded') === 'false') await click(more)
}
const diagram = () => host.querySelector('[data-view-angle]') as SVGElement | null
const axisDir = (name: 'x' | 'y' | 'z'): [number, number] => {
  const g = diagram()!.querySelector(`[data-axis="${name}"]`)!
  return [Number(g.getAttribute('data-dx')), Number(g.getAttribute('data-dy'))]
}
const click = async (el: Element | null | undefined) => {
  if (!el) throw new Error('没有这个按钮')
  await act(async () => {
    const active = document.activeElement
    if (active instanceof HTMLElement && active !== el) active.blur()
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const typeInto = async (el: HTMLInputElement, text: string) => {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, text)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  })
}
const livePanel = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
const overrideOf = (prop: string) =>
  livePanel().overrides.find((o) => o.gid === 'axes_0' && o.prop === prop)?.value

beforeEach(() => {
  engineRender.mockReset()
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
})

/* --------------------------------- 用例 ---------------------------------- */

describe('三维子图的从属设置（审计 T24）', () => {
  it('关掉箭头：颜色 / 线宽 / 大小一并收起，只剩开启入口', async () => {
    await mount()
    await openMore()
    expect(rowOf('axis_arrows')).not.toBeNull()
    for (const prop of ['arrow_color', 'arrow_width', 'arrow_head']) {
      expect(rowOf(prop), prop).toBeNull()
    }
  })

  it('开着箭头：三条从属设置都在', async () => {
    await mount({ manifest: manifestOf(axes3dEl({ arrows: true })) })
    await openMore()
    for (const prop of ['arrow_color', 'arrow_width', 'arrow_head']) {
      expect(rowOf(prop), prop).not.toBeNull()
    }
  })

  it('背景面板关掉时面板颜色收起（三个开关各管各的）', async () => {
    await mount({ manifest: manifestOf(axes3dEl({ panes: false })) })
    await openMore()
    expect(rowOf('pane_visible')).not.toBeNull()
    expect(rowOf('pane_color')).toBeNull()
    // 网格是独立开关，不受背景面板影响
    expect(rowOf('grid_visible')).not.toBeNull()
  })

  it('组标题「轴箭头」与开关不再同名', async () => {
    await mount()
    await openMore()
    expect(host.textContent).toContain('轴箭头')
    expect(rowOf('axis_arrows')!.textContent).toContain('显示箭头')
    expect(rowOf('axis_arrows')!.textContent).not.toContain('轴箭头')
  })
})

describe('投影方式（审计 T24）', () => {
  it('两个预览格而不是文字下拉；点一下写 proj_type', async () => {
    await mount()
    const row = rowOf('proj_type')!
    expect(row.querySelector('select')).toBeNull()
    const cells = Array.from(row.querySelectorAll('[role="radio"]'))
    expect(cells.map((c) => c.getAttribute('data-value'))).toEqual(['persp', 'ortho'])
    expect(cells.map((c) => c.getAttribute('aria-label'))).toEqual(['透视', '正交'])
    expect(cells[0].getAttribute('aria-checked')).toBe('true')
    // 每格里有一个图形预览（小立方体）
    expect(cells[0].querySelector('svg')).not.toBeNull()
    await click(cells[1])
    expect(overrideOf('proj_type')).toBe('ortho')
  })
})

describe('方向示意（审计 T24）', () => {
  it('与 matplotlib 算出来的方向一致（默认视角 elev 30 / azim -60）', async () => {
    await mount()
    expect(diagram()).not.toBeNull()
    // 期望值来自 proj3d._view_axes（matplotlib 3.10.8），不是这份实现的自述
    const want = { x: [0.866025, -0.25], y: [0.5, 0.433013], z: [0, 0.866025] } as const
    for (const k of ['x', 'y', 'z'] as const) {
      expect(axisDir(k)[0], `${k} 右`).toBeCloseTo(want[k][0], 4)
      expect(axisDir(k)[1], `${k} 上`).toBeCloseTo(want[k][1], 4)
    }
  })

  it('改角度示意跟着变：azim 改成 -90 后 Y 轴正对视线', async () => {
    await mount()
    await typeInto(rowOf('azim')!.querySelector('input') as HTMLInputElement, '-90')
    expect(overrideOf('azim')).toBe(-90)
    // matplotlib（3.10.8）在 elev 30 / azim -90：x 完全指向屏幕右，
    // y 的左右分量归零、只剩往上的一半。**不是**「y 整个变成一个点」——
    // 那要 elev 也是 0；第一版的期望值是照直觉写的，被这条打回来了
    expect(axisDir('x')[0]).toBeCloseTo(1, 4)
    expect(axisDir('x')[1]).toBeCloseTo(0, 4)
    expect(axisDir('y')[0]).toBeCloseTo(0, 4)
    expect(axisDir('y')[1]).toBeCloseTo(0.5, 4)
    expect(axisDir('z')[1]).toBeCloseTo(0.866025, 4)
  })

  it('是一张有名字的静态图：role=img + aria-label，没有任何动画元素', async () => {
    await mount()
    const svg = diagram()!
    expect(svg.getAttribute('role')).toBe('img')
    expect(svg.getAttribute('aria-label')).toContain('俯仰 30')
    expect(svg.querySelectorAll('animate, animateTransform, animateMotion')).toHaveLength(0)
  })
})
