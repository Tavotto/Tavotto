/**
 * 跨文字角色的批量样式编辑。
 *
 * 要钉住的（修改前全部不成立，见
 * `docs/ux/img/ux-consistency-pass/before/zh-1440-title-plus-axis-labels.png`）：
 *   1. 图标题 + 轴标题（**角色不同**）能一起改公共样式；
 *   2. 只显示字段交集，内容（`text`）绝不出现在批量里；
 *   3. B/I 单选多选是同一个三态图标按钮，不退化成 `常规 / 加粗` 下拉；
 *   4. mixed 的字体 / 字号 / 颜色不显示成某个默认值；
 *   5. 一次点击 = 一条历史，撤销一次全组回滚，重做全部回来；
 *   6. 几何对齐工具出现时，公共文字样式入口仍在。
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
import { commonTextFields, isTextLikeSelection } from './textStyleModel'

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

const FAM = ['serif', 'sans-serif', 'monospace']

/** 一个 matplotlib Text 的能力表（与 engine/manifest.py `_text_fields` 同形） */
const textFields = (over: Record<string, unknown> = {}): EditableField[] => [
  f('text', 'text', over.text ?? 'Some text'),
  f('fontsize', 'number', over.fontsize ?? 9, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
  f('color', 'color', over.color ?? '#000000'),
  f('weight', 'enum', over.weight ?? 'normal', { options: ['normal', 'bold'] }),
  f('style', 'enum', over.style ?? 'normal', { options: ['normal', 'italic'] }),
  f('fontfamily', 'enum', over.fontfamily ?? 'serif', { options: (over.fam as string[]) ?? FAM }),
  f('rotation', 'number', over.rotation ?? 0, { min: -180, max: 180, step: 5, unit: '°' }),
  f('alpha', 'number', 1, { min: 0, max: 1, step: 0.05 }),
  f('visible', 'bool', true),
  f('ha', 'enum', 'center', { options: ['left', 'center', 'right'], group: '排版' }),
]

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题 “Reaction kinetics”',
  bbox: [0.2, 0.02, 0.5, 0.08],
  draggable: true,
  anchor: [0.45, 0.06],
  drag_prop: 'pos_frac',
  editable: textFields({ text: 'Reaction kinetics', fontsize: 11 }),
}

const xLabelEl: ManifestElement = {
  gid: 'axes_0.xaxis.label',
  role: 'axis_label',
  label: 'X 轴 “Reaction time”',
  bbox: [0.3, 0.9, 0.4, 0.06],
  draggable: true,
  anchor: [0.5, 0.93],
  drag_prop: 'pos_frac',
  editable: textFields({ text: 'Reaction time', fontsize: 9, weight: 'bold' }),
}

const yLabelEl: ManifestElement = {
  gid: 'axes_0.yaxis.label',
  role: 'axis_label',
  label: 'Y 轴 “Conversion”',
  bbox: [0.02, 0.3, 0.06, 0.4],
  draggable: true,
  anchor: [0.05, 0.5],
  drag_prop: 'pos_frac',
  editable: textFields({ text: 'Conversion', fontsize: 9 }),
}

const lineEl: ManifestElement = {
  gid: 'axes_0.lines_0',
  role: 'line',
  label: '曲线 sin',
  bbox: [0.1, 0.1, 0.8, 0.8],
  draggable: false,
  editable: [f('color', 'color', '#1f77b4'), f('linewidth', 'number', 1.5)],
}

const manifest: Manifest = {
  rev: 1,
  size_mm: [101.6, 76.2],
  elements: [titleEl, xLabelEl, yLabelEl, lineEl],
} as unknown as Manifest

const panelOf = (): PanelObject =>
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
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

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

async function mount(gids: string[]) {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: gids })
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
const byAria = (name: string) => buttons().filter((b) => b.getAttribute('aria-label') === name)
const boldBtn = () => byAria('加粗')[0] ?? byAria('加粗 · 多个值')[0]
const italicBtn = () => byAria('斜体')[0] ?? byAria('斜体 · 多个值')[0]
const textOf = () => host.textContent ?? ''
const inputs = () => Array.from(host.querySelectorAll('input'))
const sizeInput = () =>
  inputs().find((i) => i.getAttribute('data-inspector-prop') === 'fontsize') as HTMLInputElement
const colorInputs = () =>
  inputs().filter((i) => i.getAttribute('type') === 'color') as HTMLInputElement[]

function typeInto(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_text_batch')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(panelOf()) } })
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  resetPreview()
  useUiStore.setState({ selectedGids: [] })
})

/* --------------------------------- 模型 ---------------------------------- */

describe('文字家族与字段交集', () => {
  it('title + axis_label 被认成同一个文字家族', () => {
    expect(isTextLikeSelection([titleEl, xLabelEl])).toBe(true)
    expect(isTextLikeSelection([titleEl, xLabelEl, yLabelEl])).toBe(true)
    expect(isTextLikeSelection([yLabelEl, xLabelEl])).toBe(true)
  })

  it('曲线混进来就不是文字家族；单选也不算批量', () => {
    expect(isTextLikeSelection([titleEl, lineEl])).toBe(false)
    expect(isTextLikeSelection([titleEl])).toBe(false)
  })

  it('交集只留公共样式属性，内容 text / 对齐 ha 一律不进', () => {
    const fields = commonTextFields([titleEl, xLabelEl, yLabelEl])
    expect([...fields.keys()].sort()).toEqual(
      ['alpha', 'color', 'fontfamily', 'fontsize', 'rotation', 'style', 'weight'].sort(),
    )
    expect(fields.has('text')).toBe(false)
    expect(fields.has('ha')).toBe(false)
  })

  it('字体选项取交集：一个目标用了别人没有的字体时，只留公共项', () => {
    const custom: ManifestElement = {
      ...titleEl,
      editable: textFields({ fam: ['Comic Sans', 'serif', 'sans-serif'], fontfamily: 'Comic Sans' }),
    }
    const fields = commonTextFields([custom, xLabelEl])
    expect(fields.get('fontfamily')?.options).toEqual(['serif', 'sans-serif'])
  })

  it('某个目标不支持该属性时整条不给（不摆点了不生效的控件）', () => {
    const noWeight: ManifestElement = {
      ...yLabelEl,
      editable: textFields().filter((x) => x.prop !== 'weight'),
    }
    const fields = commonTextFields([titleEl, noWeight])
    expect(fields.has('weight')).toBe(false)
    expect(fields.has('fontsize')).toBe(true)
  })

  it('数值范围取最紧的那个：写出去的值要在每个目标各自区间内', () => {
    const narrow: ManifestElement = {
      ...xLabelEl,
      editable: [
        ...textFields().filter((x) => x.prop !== 'fontsize'),
        f('fontsize', 'number', 9, { min: 6, max: 20, step: 0.5, unit: 'pt' }),
      ],
    }
    const size = commonTextFields([titleEl, narrow]).get('fontsize')
    expect(size?.min).toBe(6)
    expect(size?.max).toBe(20)
  })
})

/* ------------------------------ 跨角色批量 UI ----------------------------- */

describe('图标题 + X/Y 轴标题的公共样式', () => {
  it('三个角色不同的文字一起选中，公共样式区出现且用 B/I 图标控件', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label', 'axes_0.yaxis.label'])
    expect(textOf()).toContain('个文字元素的公共样式')
    // B/I 是图标按钮（aria-pressed 三态），不是 `常规 / 加粗` 的枚举下拉
    expect(boldBtn()).toBeTruthy()
    expect(italicBtn()).toBeTruthy()
    // 通用枚举下拉的选项文字一个都不该出现
    expect(textOf()).not.toContain('常规')
    expect(textOf()).not.toContain('正体')
  })

  it('批量里不显示文字内容的可编辑控件', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label'])
    // 内容是 textarea / 文本框；批量里一个都不该有
    // （对齐区把选中元素的**名字**列出来是另一回事，那是「选了谁」不是「改内容」）
    expect(host.querySelector('textarea')).toBeNull()
    const editable = inputs().filter(
      (i) => i.value === 'Reaction kinetics' || i.value === 'Reaction time',
    )
    expect(editable).toHaveLength(0)
    // 单选时内容控件是在的——对照组，证明上面不是「本来就没有」
    await act(async () => {
      root.unmount()
    })
    document.body.innerHTML = ''
    await mount(['axes_0.title'])
    expect(host.querySelector('textarea, input[data-inspector-prop="text"]')).toBeTruthy()
  })

  it('字重不一致时 B 是 mixed 三态，不是「都没加粗」', async () => {
    // xLabel 是 bold，title 是 normal
    await mount(['axes_0.title', 'axes_0.xaxis.label'])
    expect(boldBtn().getAttribute('aria-pressed')).toBe('mixed')
    expect(boldBtn().getAttribute('aria-label')).toContain('多个值')
  })

  it('mixed 点一次 → 全部加粗；再点一次 → 全部恢复常规', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label'])
    await act(async () => {
      boldBtn().click()
    })
    expect(overrideOf('axes_0.title', 'weight')).toBe('bold')
    expect(overrideOf('axes_0.xaxis.label', 'weight')).toBe('bold')
    expect(boldBtn().getAttribute('aria-pressed')).toBe('true')

    await act(async () => {
      boldBtn().click()
    })
    expect(overrideOf('axes_0.title', 'weight')).toBe('normal')
    expect(overrideOf('axes_0.xaxis.label', 'weight')).toBe('normal')
    expect(boldBtn().getAttribute('aria-pressed')).toBe('false')
  })

  it('全关点一次 → 全开（斜体走同一套三态语义）', async () => {
    await mount(['axes_0.xaxis.label', 'axes_0.yaxis.label'])
    expect(italicBtn().getAttribute('aria-pressed')).toBe('false')
    await act(async () => {
      italicBtn().click()
    })
    expect(overrideOf('axes_0.xaxis.label', 'style')).toBe('italic')
    expect(overrideOf('axes_0.yaxis.label', 'style')).toBe('italic')
  })

  it('字号不一致时输入框留空 + 「多个值」占位，不谎报 9pt', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label']) // 11 vs 9
    const size = sizeInput()
    expect(size.value).toBe('')
    expect(size.getAttribute('placeholder')).toBe('多个值')
  })

  it('字号一致时显示真实数字', async () => {
    await mount(['axes_0.xaxis.label', 'axes_0.yaxis.label']) // 都是 9
    expect(sizeInput().value).toBe('9')
  })

  it('输入新字号写到全部目标', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label', 'axes_0.yaxis.label'])
    const size = sizeInput()
    await act(async () => {
      typeInto(size, '14')
      size.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    for (const gid of ['axes_0.title', 'axes_0.xaxis.label', 'axes_0.yaxis.label']) {
      expect(overrideOf(gid, 'fontsize')).toBe(14)
    }
  })

  it('颜色一致时不显示混合标记；改色写到全部目标', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label'])
    const swatch = colorInputs()[0]
    await act(async () => {
      typeInto(swatch, '#aa2233')
    })
    expect(overrideOf('axes_0.title', 'color')).toBe('#aa2233')
    expect(overrideOf('axes_0.xaxis.label', 'color')).toBe('#aa2233')
  })

  it('颜色不一致时旁边明写「多个值」', async () => {
    useDocumentStore.getState().commit(literal('先改一个'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 'axes_0.title', prop: 'color', value: '#ff0000' })
    })
    await mount(['axes_0.title', 'axes_0.xaxis.label'])
    expect(textOf()).toContain('多个值')
  })
})

/* ------------------------------ 历史与撤销 -------------------------------- */

describe('批量写入的历史粒度', () => {
  it('一次点击 = 一条历史；撤销一次全组回滚，重做全部回来', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label', 'axes_0.yaxis.label'])
    const before = useDocumentStore.getState().past.length

    await act(async () => {
      boldBtn().click()
    })
    expect(useDocumentStore.getState().past.length).toBe(before + 1)

    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(overrideOf('axes_0.title', 'weight')).toBeUndefined()
    expect(overrideOf('axes_0.xaxis.label', 'weight')).toBeUndefined()
    expect(overrideOf('axes_0.yaxis.label', 'weight')).toBeUndefined()

    await act(async () => {
      useDocumentStore.getState().redo()
    })
    expect(overrideOf('axes_0.title', 'weight')).toBe('bold')
    expect(overrideOf('axes_0.xaxis.label', 'weight')).toBe('bold')
    expect(overrideOf('axes_0.yaxis.label', 'weight')).toBe('bold')
  })

  it('不会因为目标数量多压出多条历史', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label', 'axes_0.yaxis.label'])
    const before = useDocumentStore.getState().past.length
    await act(async () => {
      italicBtn().click()
    })
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
  })
})

/* ----------------------------- 与对齐并存 --------------------------------- */

describe('公共样式与几何对齐并存', () => {
  it('对齐工具条出现时，公共文字样式入口仍然可见', async () => {
    await mount(['axes_0.title', 'axes_0.xaxis.label', 'axes_0.yaxis.label'])
    // 对齐区在（多选三个文字必然有几何目标）
    expect(textOf()).toContain('已选 3 个元素')
    // 样式区也在
    expect(textOf()).toContain('个文字元素的公共样式')
    expect(boldBtn()).toBeTruthy()
  })

  it('同角色多选：公共样式在上，其余公共字段（旋转/透明度）仍在下面', async () => {
    await mount(['axes_0.xaxis.label', 'axes_0.yaxis.label'])
    expect(boldBtn()).toBeTruthy()
    expect(textOf()).toContain('旋转')
  })
})

/* ------------------------------- 单选一致性 ------------------------------- */

describe('单选与多选同一套控件', () => {
  it('单选文字用的是同一个 B/I 图标按钮', async () => {
    await mount(['axes_0.title'])
    expect(boldBtn()).toBeTruthy()
    expect(italicBtn()).toBeTruthy()
    expect(boldBtn().getAttribute('aria-pressed')).toBe('false')
  })

  it('单选点 B 只改这一个', async () => {
    await mount(['axes_0.title'])
    await act(async () => {
      boldBtn().click()
    })
    expect(overrideOf('axes_0.title', 'weight')).toBe('bold')
    expect(overrideOf('axes_0.xaxis.label', 'weight')).toBeUndefined()
  })

  it('单选仍然有对齐行（ha 只在单选时给）', async () => {
    await mount(['axes_0.title'])
    expect(textOf()).toContain('对齐')
  })
})
