/**
 * 多选时浮动栏上的快速排版（ADR 0089）：
 *
 *   1. 图内多选（两个及以上图内元素）出浮动栏：可对齐的目标 ≥ 2 时给六向对齐（落地是
 *      `alignSelectedPanelElements`，与属性页对齐区同一个函数）；同一文字家族时给字号 /
 *      加粗 / 斜体 / 颜色，写入走批量适配器——一次改动一条历史、撤销一次全组回去；
 *   2. 取色是连续动作：一轮拖动里多次 change 合成**一条**历史（图内与画布两条路都量）；
 *   3. 画布多选全是文字时，多选栏计数后面接同一份排版行；混着面板 / 标注时不给；
 *   4. 右栏属性页停靠时按单选那条判据缩减（字体下拉与取色器让给右栏）；
 *   5. `pointercancel` 也结束「按下即藏」——只听 pointerup 时工具条会一直藏着。
 *
 * jsdom 没有布局：落位在 `position.test.ts` / `textBarCompact.test.tsx` 量；这里量
 * 「出不出、给什么、写到哪、几条历史」。真实浏览器里的同一件事在 `e2e/multi-selection-bar.spec.ts`。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject, type TextObject } from '@/types/document'
import { ContextBar } from './ContextBar'

const engineRender = vi.fn()
const alignSpy = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

vi.mock('@/store/alignAction', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/store/alignAction')>()
  return {
    ...orig,
    alignSelectedPanelElements: (panelId: string, mode: string) => {
      alignSpy(panelId, mode)
      return orig.alignSelectedPanelElements(panelId, mode as never)
    },
  }
})

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const textFields = (size = 9, color = '#000000') => [
  f('text', 'text', 'x'),
  f('fontsize', 'number', size, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
  f('color', 'color', color),
  f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
  f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
  f('fontfamily', 'enum', 'serif', { options: ['serif', 'sans-serif', 'monospace'] }),
]

/** 可拖动、带锚点的文字（标题 / 轴标题的形状）：能参与对齐 */
const movable = (gid: string, role: string, bbox: ManifestElement['bbox'], size = 9): ManifestElement => ({
  gid,
  role,
  label: gid,
  bbox,
  draggable: true,
  anchor: [bbox[0], bbox[1]],
  drag_prop: 'position',
  editable: textFields(size),
})

const titleEl = movable('axes_0.title', 'title', [0.25, 0.02, 0.5, 0.08], 12)
const ylabelEl = movable('axes_0.ylabel', 'axis_label', [0.02, 0.3, 0.05, 0.4], 9)
/** 图例项：位置由图例决定，不可单独对齐 */
const entry = (i: number): ManifestElement => ({
  gid: `axes_0.legend.texts_${i}`,
  role: 'legend_text',
  label: `图例项 ${i}`,
  bbox: [0.3, 0.16 + i * 0.07, 0.3, 0.05],
  draggable: false,
  editable: textFields(8, i === 0 ? '#000000' : '#ff0000'),
})
const lineEl: ManifestElement = {
  gid: 'axes_0.lines_0',
  role: 'line',
  label: '曲线',
  bbox: [0.2, 0.3, 0.6, 0.4],
  draggable: false,
  editable: [f('color', 'color', '#1f77b4'), f('linewidth', 'number', 1.5, { min: 0.1, max: 12 })],
}

const manifest: Manifest = {
  stem: 'Fig2',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    titleEl,
    ylabelEl,
    entry(0),
    entry(1),
    lineEl,
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

const textObj = (id: string, x: number, color = '#000000'): TextObject =>
  ({ id, type: 'text', x, y: 10, w: 30, h: 5, text: id, sizePt: 10, color }) as unknown as TextObject

let root: Root

async function mount(ui: Partial<ReturnType<typeof useUiStore.getState>> = {}) {
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

async function selectGids(...gids: string[]) {
  await act(async () => {
    useUiStore.setState({ selectedGids: gids })
  })
}

const bar = () => document.querySelector<HTMLElement>('[data-context-bar]')
const byLabel = (label: string) => bar()?.querySelector(`[aria-label="${label}"]`) ?? null
const overridesOf = (prop: string) =>
  livePanel()
    .overrides.filter((o) => o.prop === prop)
    .map((o) => [o.gid, o.value])

async function typeNumber(input: HTMLInputElement, value: string) {
  await act(async () => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  })
}

/** 模拟系统取色盘拖着走：一串 input 事件，最后失焦 */
async function dragColor(values: string[]) {
  const input = bar()!.querySelector<HTMLInputElement>('input[type="color"]')!
  for (const v of values) {
    await act(async () => {
      input.focus()
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, v)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  await act(async () => {
    input.blur()
  })
}

const undo = async () => {
  await act(async () => {
    useDocumentStore.getState().undo()
  })
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  alignSpy.mockReset()
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_element_multi_bar')
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
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
  resetPreview()
})

describe('图内多选出浮动栏', () => {
  it('两个可对齐的文字（标题 + 轴标题）：对齐 + 字号 / 加粗 / 斜体 / 颜色都在', async () => {
    await mount()
    await selectGids('axes_0.title', 'axes_0.ylabel')
    expect(bar()).not.toBeNull()
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('elements')
    expect(bar()!.querySelector('[data-selection-count="2"]')).not.toBeNull()
    const left = bar()!.querySelector<HTMLButtonElement>('[data-align-mode="left"]')
    expect(left).not.toBeNull()
    expect(left!.disabled).toBe(false)
    expect(byLabel('字号')).not.toBeNull()
    expect(byLabel('加粗')).not.toBeNull()
    expect(byLabel('斜体')).not.toBeNull()
    expect(byLabel('字体')).not.toBeNull()
    expect(bar()!.querySelector('input[type="color"]')).not.toBeNull()
  })

  it('字号两个值不一致：显示「多个值」而不是某一个的字号', async () => {
    await mount()
    await selectGids('axes_0.title', 'axes_0.ylabel')
    const size = byLabel('字号') as HTMLInputElement
    expect(size.value).toBe('')
  })

  it('对齐按钮只发意图：落地是 alignSelectedPanelElements，写成一条历史', async () => {
    await mount()
    await selectGids('axes_0.title', 'axes_0.ylabel')
    await act(async () => {
      bar()!.querySelector<HTMLButtonElement>('[data-align-mode="left"]')!.click()
    })
    expect(alignSpy).toHaveBeenCalledWith('p1', 'left')
    expect(useDocumentStore.getState().past.length).toBe(1)
    // 两个墨迹框的左沿被拉到同一条线上（选区边界的左沿 = 轴标题的 0.02）：
    // 左沿 = bbox 左 + (生效锚点 - 渲染时锚点)
    const leftEdge = (e: ManifestElement) => {
      const ov = livePanel().overrides.find((o) => o.gid === e.gid && o.prop === 'position')
      const ax = ov ? (ov.value as number[])[0] : e.anchor![0]
      return e.bbox[0] + ax - e.anchor![0]
    }
    expect(leftEdge(titleEl)).toBeCloseTo(0.02)
    expect(leftEdge(ylabelEl)).toBeCloseTo(0.02)
  })

  it('改字号：两个元素都写上、一条历史；撤销一次全组回去', async () => {
    await mount()
    await selectGids('axes_0.legend.texts_0', 'axes_0.legend.texts_1')
    await typeNumber(byLabel('字号') as HTMLInputElement, '11')
    expect(overridesOf('fontsize').sort()).toEqual([
      ['axes_0.legend.texts_0', 11],
      ['axes_0.legend.texts_1', 11],
    ])
    expect(useDocumentStore.getState().past.length).toBe(1)
    await undo()
    expect(overridesOf('fontsize')).toEqual([])
  })

  it('取色拖动一轮：多次 change 合成一条历史，撤销一次回到原色', async () => {
    await mount()
    await selectGids('axes_0.legend.texts_0', 'axes_0.legend.texts_1')
    await dragColor(['#110000', '#220000', '#330000', '#440000'])
    expect(overridesOf('color').sort()).toEqual([
      ['axes_0.legend.texts_0', '#440000'],
      ['axes_0.legend.texts_1', '#440000'],
    ])
    expect(useDocumentStore.getState().past.length).toBe(1)
    await undo()
    expect(overridesOf('color')).toEqual([])
  })

  it('只选图例项（位置由图例决定、不可单独对齐）：只给排版，不摆一排点了没用的对齐钮', async () => {
    await mount()
    await selectGids('axes_0.legend.texts_0', 'axes_0.legend.texts_1')
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('elements')
    expect(bar()!.querySelector('[data-align-mode]')).toBeNull()
    expect(byLabel('字号')).not.toBeNull()
  })

  it('异类混选（标题 + 曲线）：只给对齐，不给文字排版', async () => {
    const withLine: Manifest = {
      ...manifest,
      elements: manifest.elements.map((e) =>
        e.gid === lineEl.gid ? { ...lineEl, draggable: true, anchor: [0.2, 0.3], drag_prop: 'position' } : e,
      ),
    }
    useRenderStore.getState().patch(renderKeyOf(livePanel()), { manifest: withLine })
    await mount()
    await selectGids('axes_0.title', 'axes_0.lines_0')
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('elements')
    expect(bar()!.querySelector('[data-align-mode="left"]')).not.toBeNull()
    expect(bar()!.querySelector('[data-text-quick]')).toBeNull()
  })

  it('既不能对齐也没有公共文字属性（图例项 + 曲线）：不出浮动栏', async () => {
    await mount()
    await selectGids('axes_0.legend.texts_0', 'axes_0.lines_0')
    expect(bar()).toBeNull()
  })

  it('右栏属性页停靠：按单选那条判据缩减——字号 / 加粗 / 斜体留下，字体与取色器让给右栏', async () => {
    await mount({ rightOpen: true, rightTab: 'properties' })
    await selectGids('axes_0.title', 'axes_0.ylabel')
    expect(bar()!.hasAttribute('data-context-bar-compact')).toBe(true)
    expect(byLabel('字号')).not.toBeNull()
    expect(byLabel('加粗')).not.toBeNull()
    expect(byLabel('字体')).toBeNull()
    expect(bar()!.querySelector('input[type="color"]')).toBeNull()
    expect(bar()!.querySelector('[data-align-mode="left"]')).not.toBeNull()
  })

  it('单选照旧是单元素栏，不是多选栏', async () => {
    await mount()
    await selectGids('axes_0.title')
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('element')
  })
})

describe('画布多选全是文字：多选栏里的排版行', () => {
  async function mountTexts(ui: Partial<ReturnType<typeof useUiStore.getState>> = {}) {
    useDocumentStore.getState().commit(literal('加文字'), (d) => {
      d.objects.push(textObj('t1', 10), textObj('t2', 60, '#00ff00'))
    })
    useDocumentStore.setState({ past: [], future: [] })
    await mount({ elementPanelId: null, ...ui })
    for (const id of ['t1', 't2']) {
      const node = document.createElement('div')
      node.setAttribute('data-object-id', id)
      document.body.appendChild(node)
    }
    await act(async () => {
      useSelectionStore.getState().set(['t1', 't2'])
    })
  }
  const textOf = (id: string) =>
    useDocumentStore.getState().doc.objects.find((o) => o.id === id) as TextObject

  it('计数后接字号 / 加粗 / 斜体 / 颜色；改字号两个都变、一条历史，撤销一次回去', async () => {
    await mountTexts()
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('multi')
    expect(bar()!.querySelector('[data-align-mode="left"]')).not.toBeNull()
    await typeNumber(byLabel('字号') as HTMLInputElement, '14')
    expect([textOf('t1').sizePt, textOf('t2').sizePt]).toEqual([14, 14])
    // 数字框是连续型写入：一轮输入由安静计时 / 别处的离散动作收尾，这里直接喊收尾
    await act(async () => {
      finishActiveGesture()
    })
    expect(useDocumentStore.getState().past.length).toBe(1)
    await undo()
    expect([textOf('t1').sizePt, textOf('t2').sizePt]).toEqual([10, 10])
  })

  it('取色拖动一轮：一条历史，撤销一次两个都回原色', async () => {
    await mountTexts()
    await dragColor(['#010101', '#020202', '#030303'])
    expect([textOf('t1').color, textOf('t2').color]).toEqual(['#030303', '#030303'])
    expect(useDocumentStore.getState().past.length).toBe(1)
    await undo()
    expect([textOf('t1').color, textOf('t2').color]).toEqual(['#000000', '#00ff00'])
  })

  it('右栏停靠：多选栏照旧是收缩形态（T29），排版行按单选判据只留字号 / 加粗 / 斜体', async () => {
    await mountTexts({ rightOpen: true, rightTab: 'properties' })
    expect(bar()!.hasAttribute('data-multi-docked')).toBe(true)
    expect(bar()!.querySelector('[data-align-ref-picker]')).toBeNull()
    expect(byLabel('字号')).not.toBeNull()
    expect(byLabel('字体')).toBeNull()
    expect(bar()!.querySelector('input[type="color"]')).toBeNull()
  })

  it('混着面板：没有一组公共文字属性，不给排版行', async () => {
    useDocumentStore.getState().commit(literal('加文字'), (d) => {
      d.objects.push(textObj('t1', 10))
    })
    await mount({ elementPanelId: null })
    await act(async () => {
      useSelectionStore.getState().set(['t1', 'p1'])
    })
    expect(bar()!.getAttribute('data-context-bar-mode')).toBe('multi')
    expect(bar()!.querySelector('[data-text-quick]')).toBeNull()
  })
})

describe('按下即藏：pointercancel 也算松手', () => {
  it('pointerdown 藏起来，pointercancel 之后回来（不必等下一次 pointerup）', async () => {
    await mount()
    await selectGids('axes_0.title', 'axes_0.ylabel')
    expect(bar()).not.toBeNull()
    await act(async () => {
      document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    })
    expect(bar()).toBeNull()
    await act(async () => {
      window.dispatchEvent(new Event('pointercancel'))
    })
    expect(bar()).not.toBeNull()
  })
})
