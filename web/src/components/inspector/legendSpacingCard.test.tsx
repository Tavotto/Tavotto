/**
 * 图例的排版详情与位置参照（审计 T17）。
 *
 * 钉住的合同：
 *   1. 五条间距（示意线长度 / 线与文字间距 / 行距 / 列距 / 内边距）不在首屏
 *      平铺，收进默认折叠的「排版详情」；通用列表里一条都不再出现——
 *      同一属性不出两套控件；
 *   2. 折叠段里标签**独占一列、不定宽**：审计点名的「线与文字间…」正是被
 *      72px 定宽标签列截掉的。判据是「这一行的标签没有内联宽度」——那是
 *      截断的机制本身，不是它的外观（jsdom 量不出裁剪）；
 *   3. 数值带真实单位 `em`（matplotlib 这五条按字号的倍数计），小节顶上
 *      有一句说明；
 *   4. 用户改过任意一条时小节**自动展开**：override 不因折叠而不可发现；
 *   5. 列距只在多列时出现，与通用列表共用 `fieldVisible` 一条判据；
 *   6. 位置九宫格下面写出参照的容器（「相对子图 1」），容器认不出来时不写。
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

/** 与 engine/manifest.py `_legend_fields` 同形（间距五条都**不带** unit） */
const legendFields = (ncol = 1): EditableField[] => [
  f('loc', 'enum', 'best', { options: ['best', 'upper right', 'upper left', 'lower left'] }),
  f('frameon', 'bool', true),
  f('visible', 'bool', true),
  f('ncol', 'number', ncol, { min: 1, max: 6, step: 1, group: '布局' }),
  f('borderpad', 'number', 0.4, { min: 0, max: 3, step: 0.1, group: '布局' }),
  f('labelspacing', 'number', 0.5, { min: 0, max: 3, step: 0.1, group: '布局' }),
  f('handlelength', 'number', 2, { min: 0, max: 5, step: 0.1, group: '布局' }),
  f('handletextpad', 'number', 0.8, { min: 0, max: 3, step: 0.1, group: '布局' }),
  f('columnspacing', 'number', 2, { min: 0, max: 6, step: 0.1, group: '布局' }),
]

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.8, 0.8],
  draggable: true,
  editable: [f('facecolor', 'color', '#ffffff')],
} as unknown as ManifestElement

const legendOf = (ncol = 1): ManifestElement =>
  ({
    gid: 'axes_0.legend',
    role: 'legend',
    label: '图例',
    bbox: [0.6, 0.1, 0.3, 0.3],
    draggable: true,
    anchor: [0.6, 0.4],
    drag_prop: 'loc_frac',
    editable: legendFields(ncol),
  }) as unknown as ManifestElement

const manifestOf = (ncol = 1): Manifest =>
  ({
    rev: 1,
    size_mm: [101.6, 76.2],
    elements: [axesEl, legendOf(ncol)],
  }) as unknown as Manifest

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

async function mount(opts: { ncol?: number; overrides?: PanelObject['overrides'] } = {}) {
  const { ncol = 1, overrides = [] } = opts
  const manifest = manifestOf(ncol)
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_legend_spacing')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(overrides))
  })
  const key = renderKeyOf(panelOf(overrides))
  useRenderStore.getState().patch(key, {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(overrides.map((o) => [o.gid, o.prop, o.value])),
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': key } })
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.legend'] })
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

const card = () => host.querySelector('[data-legend-spacing]')
const cardToggle = () => card()?.querySelector('button') as HTMLButtonElement | undefined
/** 排版详情卡里某条属性的那一行 */
const cardRow = (prop: string) => card()?.querySelector(`[data-prop="${prop}"]`) ?? null
/** 整个属性页里挂着这条属性的所有行（用来证明没有第二套控件） */
const allRows = (prop: string) => Array.from(host.querySelectorAll(`[data-prop="${prop}"]`))
const click = async (el: Element | null | undefined) => {
  if (!el) throw new Error('没有这个按钮')
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

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

describe('图例的排版详情（审计 T17）', () => {
  it('默认折叠：五条间距一条都不在 DOM 里，只有一个「排版详情」入口', async () => {
    await mount()
    expect(card()).not.toBeNull()
    expect(cardToggle()?.getAttribute('aria-expanded')).toBe('false')
    for (const prop of ['handlelength', 'handletextpad', 'labelspacing', 'borderpad']) {
      expect(allRows(prop)).toHaveLength(0)
    }
  })

  it('展开后五条都在，且通用列表里没有第二套控件', async () => {
    await mount({ ncol: 2 })
    await click(cardToggle())
    for (const prop of ['handlelength', 'handletextpad', 'labelspacing', 'borderpad', 'columnspacing']) {
      // 恰好一行：卡里那一行。多于一行 = 首屏又铺了一遍
      expect(allRows(prop)).toHaveLength(1)
      expect(cardRow(prop)).not.toBeNull()
    }
  })

  it('标签独占一列、不定宽：审计点名的「线与文字间距」完整出现在行里', async () => {
    await mount()
    await click(cardToggle())
    const row = cardRow('handletextpad') as HTMLElement
    const labelSpan = row.querySelector('span') as HTMLElement
    // 通用列表的标签列是 `style="width: 72px"`（Row 的 labelWidth）——正是
    // 它把「线与文字间距」截成「线与文字间…」。卡里这一列不定宽
    expect(labelSpan.style.width).toBe('')
    expect(labelSpan.textContent).toContain('线与文字间距')
  })

  it('数值带真实单位 em，并且小节顶上说明它是相对字号', async () => {
    await mount()
    await click(cardToggle())
    const row = cardRow('labelspacing') as HTMLElement
    expect(row.textContent).toContain('em')
    expect(card()?.textContent).toContain('1 em')
  })

  it('改过一条时自动展开：override 不因折叠而不可发现', async () => {
    await mount({
      overrides: [{ gid: 'axes_0.legend', prop: 'handlelength', value: 3.2 }],
    })
    // 一次都没点过折叠按钮
    expect(cardToggle()?.getAttribute('aria-expanded')).toBe('true')
    expect(cardRow('handlelength')).not.toBeNull()
  })

  it('列距只在多列时出现（与通用列表共用 fieldVisible 一条判据）', async () => {
    await mount({ ncol: 1 })
    await click(cardToggle())
    expect(cardRow('columnspacing')).toBeNull()
    expect(cardRow('labelspacing')).not.toBeNull()
  })
})

describe('位置九宫格的参照容器（审计 T17）', () => {
  it('框下面写出九个档位参照的是谁', async () => {
    await mount()
    const grid = host.querySelector('[role="radiogroup"]') as HTMLElement
    expect(grid).not.toBeNull()
    const described = document.getElementById(grid.getAttribute('aria-describedby') ?? '')
    expect(described?.textContent).toBe('相对子图 1')
  })

  it('容器认不出来时不写一个猜的名字', async () => {
    // 宿主 axes 不在 manifest 里（fig.legend / 脚本自造的图例）
    const manifest = {
      rev: 1,
      size_mm: [101.6, 76.2],
      elements: [legendOf(1)],
    } as unknown as Manifest
    engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_legend_spacing_2')
    useDocumentStore.getState().commit(literal('加面板'), (d) => {
      d.objects.push(panelOf())
    })
    const key = renderKeyOf(panelOf())
    useRenderStore.getState().patch(key, {
      fileId: 'Fig1.pdf',
      manifest,
      svg: MATPLOTLIB_SVG,
      rev: 1,
      status: 'ready',
      lastPatches: '[]',
    })
    useRenderStore.setState({ latest: { 'Fig1.pdf': key } })
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.legend'] })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(<Harness />)
    })
    const grid = host.querySelector('[role="radiogroup"]') as HTMLElement
    expect(grid.getAttribute('aria-describedby')).toBeNull()
    expect(host.textContent).not.toContain('相对')
  })
})
