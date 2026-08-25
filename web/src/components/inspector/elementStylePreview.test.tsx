/**
 * 属性页的 SVG 局部样式预览：**改的那一刻画面就变，matplotlib 慢慢跟上**。
 *
 * 要钉住的：
 *   1. 支持预览的字段（线条颜色/线宽、文字颜色、柱形填充、alpha、显隐）在
 *      改动那一刻就写进 SVG，整轮调整**一次后端都不发**；
 *   2. 不支持的字段（fontsize / linestyle / 字体…）原路走后端，SVG 一个字节不动；
 *   3. 一次连续调整 = 一条历史（gesture），granular 模式下每个变化各一条，
 *      但**两种模式下后端渲染都推迟到手势结束**；
 *   4. 两个独立控件的操作绝不被合并成一条历史。
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
import { flushPreviewFrame, resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
// Radix 的 Select 打开时会 scrollIntoView；jsdom 没有这个方法
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/* -------------------------------- 测试数据 -------------------------------- */

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const lineEl: ManifestElement = {
  gid: 'axes_0.lines_0',
  role: 'line',
  label: '曲线 sin',
  bbox: [0.1, 0.1, 0.8, 0.8],
  draggable: false,
  editable: [
    f('color', 'color', '#1f77b4'),
    f('linewidth', 'number', 1.5, { min: 0.1, max: 8, step: 0.1 }),
    f('linestyle', 'enum', '-', { options: ['-', '--', ':', '-.'] }),
    f('alpha', 'number', 1, { min: 0, max: 1, step: 0.05 }),
    f('visible', 'bool', true),
  ],
}

const barEl: ManifestElement = {
  gid: 'axes_0.barseries_0.bar_0',
  role: 'bar',
  label: '柱 1',
  bbox: [0.2, 0.2, 0.1, 0.5],
  draggable: false,
  editable: [f('facecolor', 'color', '#ff7f0e'), f('visible', 'bool', true)],
}

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.2, 0.02, 0.5, 0.08],
  draggable: true,
  anchor: [0.45, 0.06],
  drag_prop: 'pos_frac',
  editable: [
    f('text', 'text', 'Title here'),
    f('color', 'color', '#000000'),
    f('fontsize', 'number', 12, { min: 3, max: 36, step: 0.5 }),
  ],
}

/**
 * 误差棒是 manifest 的**伪元素**（SeriesGroup）：能力表里有 errorbar.color，
 * 但 matplotlib 给它的成员发的是自动 id，SVG 里根本没有这个 gid。
 * 它守的是「查不到就据实回退」，不是「装作预览成功」。
 */
const errorbarEl: ManifestElement = {
  gid: 'axes_0.errorbar_1',
  role: 'errorbar',
  label: '误差棒 1',
  bbox: [0.3, 0.3, 0.2, 0.2],
  draggable: false,
  editable: [f('color', 'color', '#9467bd'), f('linewidth', 'number', 1.2)],
}

/** 脚本 `ax.fill()` 出的独立形状：SVG 上 fill 与 stroke 各一条 */
const patchEl: ManifestElement = {
  gid: 'axes_0.patches_4',
  role: 'patch',
  label: '形状 1',
  bbox: [0.1, 0.6, 0.2, 0.2],
  draggable: false,
  editable: [
    f('facecolor', 'color', '#17becf'),
    f('fill', 'bool', true),
    f('edgecolor', 'color', '#5a3286'),
    f('linewidth', 'number', 1.2, { min: 0, max: 8, step: 0.1 }),
    f('visible', 'bool', true),
  ],
}

/**
 * `fill=False` 的 PathPatch：SVG 上写的是 `fill: none`。
 * 通用规则只改「本来就画着的叶子」，所以 facecolor 在它身上必须**预览不生效**
 * 并据实回退后端——而不是把一个空心形状凭空填实。
 */
const hollowPatchEl: ManifestElement = {
  gid: 'axes_0.patches_5',
  role: 'patch',
  label: '形状 2',
  bbox: [0.4, 0.6, 0.2, 0.2],
  draggable: false,
  editable: [
    f('facecolor', 'color', '#000000'),
    f('edgecolor', 'color', '#7f7f0f'),
  ],
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    lineEl,
    barEl,
    titleEl,
    errorbarEl,
    patchEl,
    hollowPatchEl,
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

async function mount(gid: string) {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [gid] })
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

/** 属性页里那一行的控件（按 aria-label / 标签文字找） */
const rowByLabel = (text: string): HTMLElement | null => {
  for (const el of Array.from(host.querySelectorAll('span'))) {
    if (el.textContent?.trim() === text) return el.closest('div')!.parentElement
  }
  return null
}

const colorInput = (label: string): HTMLInputElement =>
  rowByLabel(label)!.querySelector('input[type="color"]')!
const textInput = (label: string): HTMLInputElement =>
  rowByLabel(label)!.querySelector('input[type="text"], input:not([type="color"])')!
const toggle = (label: string): HTMLElement =>
  rowByLabel(label)!.querySelector('button[role="switch"]')!

/** 展开「更多」折叠区（显隐等中频属性住在里面；IA 见 ADR 0010） */
async function openMore() {
  const btn = Array.from(host.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === '更多',
  )
  if (btn && btn.getAttribute('aria-expanded') !== 'true') {
    await act(async () => {
      btn.click()
    })
  }
}

/**
 * React 19 给受控 input 挂了 value tracker：直接 `el.value = x` 之后再发
 * input 事件，React 会认为值没变而**跳过 onChange**。必须经原生 setter 写值。
 */
function typeInto(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

const svgNode = (gid: string) =>
  document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`) as SVGElement

/** 属性名 → 该节点上真正生效的样式值（颜色经 CSSOM 规范化，两边同样处理） */
const css = (v: string) => {
  const probe = document.createElement('span')
  probe.style.setProperty('color', v)
  return probe.style.getPropertyValue('color') || v
}
const styleOf = (gid: string, sel: string, prop: string) =>
  ((svgNode(gid).querySelector(sel) ?? svgNode(gid)) as SVGElement).style.getPropertyValue(prop)

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_style_preview')
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
  setHistoryMode('gesture')
  document.body.innerHTML = ''
})

/* ============================ 支持预览的字段 ============================== */

describe('线条颜色：改的那一刻 SVG 就变，后端等到手势结束', () => {
  it('取色过程中一次 engineRender 都没有，SVG 却已经是新颜色', async () => {
    await mount('axes_0.lines_0')
    const input = colorInput('颜色')
    for (const v of ['#ff0000', '#00ff00', '#0000ff']) {
      await act(async () => {
        typeInto(input, v)
      })
      flushPreviewFrame()
    }
    expect(engineRender).not.toHaveBeenCalled()
    expect(styleOf('axes_0.lines_0', 'path', 'stroke')).toBe(css('#0000ff'))
    // 文档里也已经是最后那个值（历史平面照常记账）
    expect(overrideOf('axes_0.lines_0', 'color')).toBe('#0000ff')
  })

  it('失焦定稿：一条历史 + 一次权威渲染，patch 是最后那个值', async () => {
    await mount('axes_0.lines_0')
    const input = colorInput('颜色')
    for (const v of ['#ff0000', '#00ff00', '#0000ff']) {
      await act(async () => {
        typeInto(input, v)
      })
    }
    await act(async () => {
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(engineRender.mock.calls[0][1]).toEqual([
      { gid: 'axes_0.lines_0', prop: 'color', value: '#0000ff' },
    ])
  })

  it('两个独立控件（先颜色后显隐）是两条历史，不会被并成一条', async () => {
    await mount('axes_0.lines_0')
    const input = colorInput('颜色')
    await act(async () => {
      typeInto(input, '#ff0000')
    })
    await act(async () => {
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    await openMore()
    await act(async () => {
      toggle('显示').click()
    })
    expect(useDocumentStore.getState().past).toHaveLength(2)
    expect(overrideOf('axes_0.lines_0', 'color')).toBe('#ff0000')
    expect(overrideOf('axes_0.lines_0', 'visible')).toBe(false)
  })
})

describe('显隐开关：一次动作 = 一条历史 + 一次渲染，画面立刻消失', () => {
  it('点一下就 display:none，且只发一次后端', async () => {
    await mount('axes_0.lines_0')
    await openMore()
    await act(async () => {
      toggle('显示').click()
    })
    flushPreviewFrame()
    expect(svgNode('axes_0.lines_0').style.display).toBe('none')
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
  })
})

describe('柱形填充色', () => {
  it('facecolor 只改 fill，SVG 当场变色', async () => {
    await mount('axes_0.barseries_0.bar_0')
    const input = colorInput('填充色')
    await act(async () => {
      typeInto(input, '#123456')
    })
    flushPreviewFrame()
    expect(styleOf('axes_0.barseries_0.bar_0', 'path', 'fill')).toBe(css('#123456'))
    // 边框色不该被顺手改掉
    expect(styleOf('axes_0.barseries_0.bar_0', 'path', 'stroke')).toBe(css('#333333'))
    expect(engineRender).not.toHaveBeenCalled()
  })
})

describe('独立形状（patch）', () => {
  it('facecolor 只改 fill，描边不动，整轮不发后端', async () => {
    await mount('axes_0.patches_4')
    const input = colorInput('填充色')
    await act(async () => {
      typeInto(input, '#123456')
    })
    flushPreviewFrame()
    expect(styleOf('axes_0.patches_4', 'path', 'fill')).toBe(css('#123456'))
    expect(styleOf('axes_0.patches_4', 'path', 'stroke')).toBe(css('#5a3286'))
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('edgecolor 只改 stroke，填充不动', async () => {
    await mount('axes_0.patches_4')
    await act(async () => {
      typeInto(colorInput('描边色'), '#aa0000')
    })
    flushPreviewFrame()
    expect(styleOf('axes_0.patches_4', 'path', 'stroke')).toBe(css('#aa0000'))
    expect(styleOf('axes_0.patches_4', 'path', 'fill')).toBe(css('#17becf'))
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('「填充」开关不在能力表里：不碰 SVG，直接走后端', async () => {
    await mount('axes_0.patches_4')
    const before = document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML
    await act(async () => {
      toggle('填充').click()
    })
    flushPreviewFrame()
    expect(document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML).toBe(before)
    expect(overrideOf('axes_0.patches_4', 'fill')).toBe(false)
    expect(engineRender).toHaveBeenCalled()
  })

  it('空心 patch 改 facecolor：SVG 一个字节不动，据实回退后端', async () => {
    await mount('axes_0.patches_5')
    const before = document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML
    await act(async () => {
      typeInto(colorInput('填充色'), '#00ff00')
    })
    flushPreviewFrame()
    // `fill: none` 不许被填实——预览没生效，就该说没生效
    expect(document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML).toBe(before)
    expect(overrideOf('axes_0.patches_5', 'facecolor')).toBe('#00ff00')
    expect(engineRender).toHaveBeenCalled()
  })

  it('空心 patch 的描边照样能抢先显示', async () => {
    await mount('axes_0.patches_5')
    await act(async () => {
      typeInto(colorInput('描边色'), '#0000aa')
    })
    flushPreviewFrame()
    expect(styleOf('axes_0.patches_5', 'path', 'stroke')).toBe(css('#0000aa'))
    expect(engineRender).not.toHaveBeenCalled()
  })
})

describe('文字颜色：默认黑色时字形组上本来没有 fill', () => {
  it('照样能抢先显示（新增属性），后端等定稿', async () => {
    await mount('axes_0.title')
    const input = colorInput('颜色')
    await act(async () => {
      typeInto(input, '#ff0000')
    })
    flushPreviewFrame()
    expect(styleOf('axes_0.title', 'g[transform]', 'fill')).toBe(css('#ff0000'))
    expect(engineRender).not.toHaveBeenCalled()
  })
})

/* =========================== 不支持预览的字段 ============================= */

describe('不支持局部预览的字段照旧走后端', () => {
  it('linestyle（枚举，会换 dash 图案）不碰 SVG，直接发渲染', async () => {
    await mount('axes_0.lines_0')
    const before = document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML
    const select = rowByLabel('线型')!.querySelector('button')!
    await act(async () => {
      select.click()
    })
    // 下拉项在 portal 里
    const option = Array.from(document.querySelectorAll('[role="option"]')).find(
      (o) => o.textContent?.trim() === '--',
    ) as HTMLElement | undefined
    if (option) {
      await act(async () => {
        option.click()
      })
      expect(overrideOf('axes_0.lines_0', 'linestyle')).toBe('--')
      expect(engineRender).toHaveBeenCalled()
    }
    // 不管下拉能不能在 jsdom 里点开，SVG 都不该被样式预览碰过
    flushPreviewFrame()
    expect(document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML).toBe(before)
  })

  it('fontsize（会重排）不碰 SVG', async () => {
    await mount('axes_0.title')
    const before = document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML
    const input = textInput('字号')
    await act(async () => {
      input.focus()
      typeInto(input, '20')
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    flushPreviewFrame()
    expect(overrideOf('axes_0.title', 'fontsize')).toBe(20)
    expect(document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML).toBe(before)
  })
})

describe('manifest 的伪元素（误差棒）：gid 在 SVG 里不存在', () => {
  it('据实回退到后端——绝不「预览成功」却什么都没变', async () => {
    await mount('axes_0.errorbar_1')
    const before = document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML
    const input = colorInput('颜色')
    await act(async () => {
      typeInto(input, '#00ff00')
    })
    flushPreviewFrame()
    // SVG 一个字节没变
    expect(document.querySelector(`[data-element-svg="p1"] svg`)!.outerHTML).toBe(before)
    // 但改动照旧生效：override 写了，后端也真的被请求了
    expect(overrideOf('axes_0.errorbar_1', 'color')).toBe('#00ff00')
    expect(engineRender).toHaveBeenCalled()
  })
})

/* ============================== 历史粒度 ================================= */

describe('historyMode', () => {
  it('gesture（默认）：一轮取色三次变化 → 一条历史', async () => {
    await mount('axes_0.lines_0')
    const input = colorInput('颜色')
    for (const v of ['#ff0000', '#00ff00', '#0000ff']) {
      await act(async () => {
        typeInto(input, v)
      })
    }
    await act(async () => {
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    expect(useDocumentStore.getState().past).toHaveLength(1)
    // 撤销一次直接回到改之前
    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(overrideOf('axes_0.lines_0', 'color')).toBeUndefined()
  })

  it('granular：每个语义变化各一条历史，但后端仍然只在结束时跑一次', async () => {
    setHistoryMode('granular')
    await mount('axes_0.lines_0')
    const input = colorInput('颜色')
    for (const v of ['#ff0000', '#00ff00', '#0000ff']) {
      await act(async () => {
        typeInto(input, v)
      })
    }
    expect(useDocumentStore.getState().past).toHaveLength(3)
    expect(engineRender).not.toHaveBeenCalled() // 渲染与历史彻底分开

    await act(async () => {
      input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    expect(engineRender).toHaveBeenCalledTimes(1)
    // 逐条可撤销
    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(overrideOf('axes_0.lines_0', 'color')).toBe('#00ff00')
    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(overrideOf('axes_0.lines_0', 'color')).toBe('#ff0000')
  })
})
