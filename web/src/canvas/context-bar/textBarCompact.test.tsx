/**
 * 文字的浮动栏（图内文字 = 审计 T14，画布文字 = 审计 T27，**同一条判据**）：
 *   1. 停靠的属性页开着 → 缩减档（只剩字号 / 加粗 / 斜体）；关着 → 完整档；
 *      narrow 断点下侧栏是覆盖层，那时浮动栏整个让位（既有规则），不在这里量；
 *   2. 落位避让同一张图里别的文字：贴上方会压住标题时不再压上去。
 *
 * jsdom 没有布局：矩形由用例按 gid / 容器直接给（`getBoundingClientRect` 打桩），
 * 工具条自身尺寸打桩到 offsetWidth / offsetHeight——规则本身在 `position.test.ts`
 * 里逐条量过，这里量的是 ContextBar 把障碍物与容器**真的喂进去了**。
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
import { ContextBar } from './ContextBar'
import { MARGIN, TOP_SAFE } from './position'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const textFields = () => [
  f('text', 'text', 'x'),
  f('fontsize', 'number', 9, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
  f('color', 'color', '#000000'),
  f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
  f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
  f('fontfamily', 'enum', 'serif', { options: ['serif', 'sans-serif', 'monospace'] }),
]

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.25, 0.02, 0.5, 0.08],
  draggable: true,
  editable: textFields(),
}
/** 紧贴在标题下方的图例项（`21-legend-entry` 的形状） */
const entryEl: ManifestElement = {
  gid: 'axes_0.legend.texts_0',
  role: 'legend_text',
  label: '图例项',
  bbox: [0.3, 0.16, 0.3, 0.05],
  draggable: false,
  editable: textFields(),
}
const nextEntryEl: ManifestElement = {
  gid: 'axes_0.legend.texts_1',
  role: 'legend_text',
  label: '图例项 2',
  bbox: [0.3, 0.23, 0.3, 0.05],
  draggable: false,
  editable: textFields(),
}

const manifest: Manifest = {
  stem: 'Fig2',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    titleEl,
    entryEl,
    nextEntryEl,
  ],
}

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig2.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig2.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

/* ------------------------------- 假布局 ---------------------------------- */

/** 图的 SVG 容器在窗口里的位置；元素矩形按 manifest 的 bbox 映上去 */
const HOST = { left: 100, top: 300, width: 400, height: 300 }
const BAR = { w: 220, h: 32 }
const rectOf = (el: Element): DOMRect => {
  let box: { left: number; top: number; width: number; height: number } | null = null
  if (el.hasAttribute('data-element-svg')) box = HOST
  else {
    const gid = el.getAttribute('id')
    const m = gid ? manifest.elements.find((e) => e.gid === gid) : null
    if (m) {
      const [bx, by, bw, bh] = m.bbox
      box = {
        left: HOST.left + bx * HOST.width,
        top: HOST.top + by * HOST.height,
        width: bw * HOST.width,
        height: bh * HOST.height,
      }
    }
  }
  const b = box ?? { left: 0, top: 0, width: 0, height: 0 }
  return {
    ...b,
    x: b.left,
    y: b.top,
    right: b.left + b.width,
    bottom: b.top + b.height,
    toJSON: () => b,
  } as DOMRect
}

const originalRect = Element.prototype.getBoundingClientRect
const originalW = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')
const originalH = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')

function stubLayout() {
  Element.prototype.getBoundingClientRect = function () {
    return rectOf(this)
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get() {
      return (this as HTMLElement).hasAttribute('data-context-bar') ? BAR.w : 0
    },
  })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get() {
      return (this as HTMLElement).hasAttribute('data-context-bar') ? BAR.h : 0
    },
  })
}
function restoreLayout() {
  Element.prototype.getBoundingClientRect = originalRect
  if (originalW) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', originalW)
  if (originalH) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalH)
}

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root

async function mount(ui: Partial<ReturnType<typeof useUiStore.getState>>) {
  useUiStore.setState({
    elementPanelId: 'p1',
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    tool: 'select',
    layout: 'wide',
    leftOpen: false,
    rightOpen: false,
    rightTab: 'properties',
    ...ui,
  })
  const svgHost = document.createElement('div')
  svgHost.setAttribute('data-element-svg', 'p1')
  svgHost.setAttribute('data-object-id', 'p1')
  svgHost.innerHTML = MATPLOTLIB_SVG
  document.body.appendChild(svgHost)
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ContextBar />
      </TooltipProvider>,
    )
  })
}

/**
 * 画布文字对象（审计 T27）：判据与图内文字共用一份（`ContextBar` 的
 * `textBarCompact`），所以这里量的是「同一条判据也管到了画布文字」。
 */
async function mountCanvasText(ui: Partial<ReturnType<typeof useUiStore.getState>>) {
  useDocumentStore.getState().commit(literal('加文字'), (d) => {
    d.objects.push({
      id: 't1',
      type: 'text',
      x: 10,
      y: 10,
      w: 40,
      h: 5,
      text: 'UI',
      sizePt: 10,
    } as never)
  })
  await mount({ elementPanelId: null, ...ui })
  const node = document.createElement('div')
  node.setAttribute('data-object-id', 't1')
  document.body.appendChild(node)
  await act(async () => {
    useSelectionStore.getState().set(['t1'])
  })
}

async function selectGid(gid: string) {
  await act(async () => {
    useUiStore.setState({ selectedGids: [gid] })
  })
}

const bar = () => document.querySelector<HTMLElement>('[data-context-bar]')
const byLabel = (label: string) => bar()?.querySelector(`[aria-label="${label}"]`) ?? null

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_text_bar_compact')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(livePanel()), {
    fileId: 'Fig2.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig2.pdf': renderKeyOf(livePanel()) } })
  useDocumentStore.setState({ past: [], future: [] })
  stubLayout()
})

afterEach(async () => {
  restoreLayout()
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
  resetPreview()
})

describe('属性页开着时浮动栏缩减', () => {
  it('右栏属性页开着（停靠）：只剩字号 / 加粗 / 斜体，字体与取色器让给右栏', async () => {
    await mount({ rightOpen: true, rightTab: 'properties' })
    await selectGid('axes_0.title')
    expect(bar()).not.toBeNull()
    expect(bar()!.hasAttribute('data-context-bar-compact')).toBe(true)
    expect(bar()!.querySelector('[data-text-quick="compact"]')).not.toBeNull()
    expect(byLabel('字号')).not.toBeNull()
    expect(byLabel('加粗')).not.toBeNull()
    expect(byLabel('斜体')).not.toBeNull()
    expect(byLabel('字体')).toBeNull()
    expect(bar()!.querySelector('input[type="color"], [data-color-field]')).toBeNull()
    // 到属性页的固定出口仍在
    expect(byLabel('全部属性')).not.toBeNull()
  })

  it('右栏关着：完整档，字体与颜色都在', async () => {
    await mount({ rightOpen: false })
    await selectGid('axes_0.title')
    expect(bar()!.hasAttribute('data-context-bar-compact')).toBe(false)
    expect(bar()!.querySelector('[data-text-quick="full"]')).not.toBeNull()
    expect(byLabel('字体')).not.toBeNull()
    expect(bar()!.querySelector('input[type="color"], [data-color-field]')).not.toBeNull()
  })

  it('右栏开着但停在别的页（画布 / 助手）：属性不在眼前，仍是完整档', async () => {
    await mount({ rightOpen: true, rightTab: 'canvas' })
    await selectGid('axes_0.title')
    expect(bar()!.hasAttribute('data-context-bar-compact')).toBe(false)
    expect(byLabel('字体')).not.toBeNull()
  })

  it('右栏开合切换时当场跟着变，不用重新选', async () => {
    await mount({ rightOpen: false })
    await selectGid('axes_0.title')
    expect(byLabel('字体')).not.toBeNull()
    await act(async () => {
      useUiStore.setState({ rightOpen: true, rightTab: 'properties' })
    })
    expect(byLabel('字体')).toBeNull()
    expect(byLabel('字号')).not.toBeNull()
  })
})

describe('画布文字：同一条缩减判据（审计 T27）', () => {
  it('右栏属性页开着：只剩字号 / 加粗 / 斜体，字体下拉与取色器让给右栏', async () => {
    await mountCanvasText({ rightOpen: true, rightTab: 'properties' })
    expect(bar()).not.toBeNull()
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('object')
    expect(bar()!.hasAttribute('data-context-bar-compact')).toBe(true)
    expect(bar()!.querySelector('[data-text-quick="compact"]')).not.toBeNull()
    expect(byLabel('字号')).not.toBeNull()
    expect(byLabel('加粗')).not.toBeNull()
    expect(byLabel('斜体')).not.toBeNull()
    expect(byLabel('字体')).toBeNull()
    expect(bar()!.querySelector('input[type="color"], [data-color-field]')).toBeNull()
    expect(byLabel('全部属性')).not.toBeNull()
  })

  it('右栏关着：完整档，字体与颜色都在', async () => {
    await mountCanvasText({ rightOpen: false })
    expect(bar()!.hasAttribute('data-context-bar-compact')).toBe(false)
    expect(bar()!.querySelector('[data-text-quick="full"]')).not.toBeNull()
    expect(byLabel('字体')).not.toBeNull()
    expect(bar()!.querySelector('input[type="color"], [data-color-field]')).not.toBeNull()
  })

  it('标注（箭头 / 形状）不吃这条判据：它的右栏没有铺同一批文字控件', async () => {
    useDocumentStore.getState().commit(literal('加箭头'), (d) => {
      d.objects.push({
        id: 'a1',
        type: 'arrow',
        x: 10,
        y: 40,
        w: 30,
        h: 10,
        start: { x: 10, y: 40 },
        end: { x: 40, y: 50 },
        color: '#1B1B18',
        strokePt: 1,
      } as never)
    })
    await mount({ elementPanelId: null, rightOpen: true, rightTab: 'properties' })
    const node = document.createElement('div')
    node.setAttribute('data-object-id', 'a1')
    document.body.appendChild(node)
    await act(async () => {
      useSelectionStore.getState().set(['a1'])
    })
    expect(bar()!.hasAttribute('data-context-bar-compact')).toBe(false)
  })
})

describe('落位避让同一张图里别的文字', () => {
  it('选中标题：上方没有别的文字，照旧贴在它上方', async () => {
    await mount({ rightOpen: false })
    await selectGid('axes_0.title')
    const el = bar()!
    expect(el.getAttribute('data-placement')).toBe('above')
    const anchorTop = HOST.top + 0.02 * HOST.height
    expect(parseFloat(el.style.top)).toBeCloseTo(anchorTop - BAR.h - MARGIN)
  })

  it('选中标题正下方的图例项：贴上方会压住标题、贴下方会压住下一项 → 退到图的上方', async () => {
    await mount({ rightOpen: false })
    await selectGid('axes_0.legend.texts_0')
    const el = bar()!
    expect(el.getAttribute('data-placement')).toBe('above')
    expect(parseFloat(el.style.top)).toBeCloseTo(HOST.top - BAR.h - MARGIN)
    // 与标题的矩形不相交
    const titleTop = HOST.top + 0.02 * HOST.height
    expect(parseFloat(el.style.top) + BAR.h).toBeLessThanOrEqual(titleTop)
  })

  it('图的上方进了顶部安全区时退到图的下方（不再硬压标题）', async () => {
    // 把整张图挪到窗口顶部：HOST.top 只比安全区高出一点
    const saved = { ...HOST }
    HOST.top = TOP_SAFE + 10
    try {
      await mount({ rightOpen: false })
      await selectGid('axes_0.legend.texts_0')
      const el = bar()!
      expect(el.getAttribute('data-placement')).toBe('below')
      expect(parseFloat(el.style.top)).toBeCloseTo(HOST.top + HOST.height + MARGIN)
    } finally {
      Object.assign(HOST, saved)
    }
  })
})
