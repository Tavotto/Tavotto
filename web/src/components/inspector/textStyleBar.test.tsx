/**
 * 图内文字的样式工具条：**属性页与右键弹层是同一份**。
 *
 * 要钉住的：
 *   1. 工具条按 manifest 里真有的字段出控件——引擎不给就不画（不硬编清单）；
 *   2. 工具条覆盖掉的属性不在平铺列表里再出一遍（同一个属性两套控件最坏）；
 *   3. 加粗 / 字形是离散动作：一条历史、一次权威渲染；
 *   4. 弹层里的颜色照旧走局部预览——整轮不发后端，收尾定稿一次；
 *   5. 右键弹层拿到的是同一批控件（含只在弹层里露面的背景 / 描边 / 排版）；
 *   6. 只有 fontsize + color + weight 都在才算文字元素（图例、刻度标签不套）。
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
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { flushPreviewFrame, resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { propertyPathOf } from '@/lib/typography'
import { ElementInspector } from './ElementInspector'
import { hasTextStyleBar, TEXT_BAR_PROPS } from './TextStyleBar'
import { FIGURE_TEXT_SINGLE_PROPS } from './typographyAdapter'

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

/** 与 engine/manifest.py 的 _text_fields 同形（挑了工具条关心的那些） */
const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.3, 0.02, 0.3, 0.07],
  draggable: true,
  anchor: [0.45, 0.06],
  drag_prop: 'pos_frac',
  editable: [
    f('text', 'text', 'Title here'),
    f('fontsize', 'number', 12, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
    f('color', 'color', '#000000'),
    f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
    f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
    f('fontfamily', 'enum', 'serif', { options: ['serif', 'sans-serif', 'monospace'] }),
    f('rotation', 'number', 0, { min: -180, max: 180, step: 5, unit: '°' }),
    f('alpha', 'number', 1, { min: 0, max: 1, step: 0.05 }),
    f('visible', 'bool', true),
    f('ha', 'enum', 'center', { options: ['left', 'center', 'right'], group: '排版' }),
    f('va', 'enum', 'baseline', { options: ['top', 'center', 'bottom'], group: '排版' }),
    f('linespacing', 'number', 1.2, { min: 0.5, max: 3, step: 0.05, group: '排版' }),
    f('zorder', 'number', 3, { min: -5, max: 50, step: 1, group: '排版' }),
    f('bbox_visible', 'bool', false, { group: '背景' }),
    f('bbox_facecolor', 'color', '#FFFFFF', { group: '背景' }),
    f('stroke_enabled', 'bool', false, { group: '描边' }),
    f('stroke_color', 'color', '#FFFFFF', { group: '描边' }),
  ],
}

/** 图例只有 fontsize，没有 weight/color —— 不该套工具条 */
const legendEl: ManifestElement = {
  gid: 'axes_0.legend',
  role: 'legend',
  label: '图例',
  bbox: [0.6, 0.6, 0.3, 0.2],
  draggable: true,
  editable: [
    f('fontsize', 'number', 8),
    f('frameon', 'bool', true),
  ],
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    titleEl,
    legendEl,
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

const byLabel = (label: string) =>
  document.querySelector(`[aria-label="${label}"]`) as HTMLElement | null

/**
 * 点工具条按钮。真实鼠标会先把焦点从文字输入框拿走（内容那条编辑事务
 * 因此收尾），jsdom 的 .click() 不动焦点——不手动 blur 的话，按钮写的那条
 * 会被并进「编辑文字」的事务里，历史计数看起来凭空少一条。
 */
async function clickBar(label: string) {
  ;(document.activeElement as HTMLElement | null)?.blur()
  await act(async () => {
    byLabel(label)!.click()
  })
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
const css = (v: string) => {
  const probe = document.createElement('span')
  probe.style.setProperty('color', v)
  return probe.style.getPropertyValue('color') || v
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_text_bar')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(livePanel()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(livePanel()) } })
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
  resetPreview()
})

/* ============================== 该不该出工具条 ============================= */

describe('判据：fontsize + color + weight 三条都有才是文字元素', () => {
  it('标题算，图例不算', () => {
    expect(hasTextStyleBar(titleEl)).toBe(true)
    expect(hasTextStyleBar(legendEl)).toBe(false)
  })
})

/* ================================ 版面 ==================================== */

describe('高频样式是带可见标签的行', () => {
  it('「字体」「字号」「颜色」「对齐」是可见文字，不只是 aria-label', async () => {
    await mount('axes_0.title')
    const text = host.textContent ?? ''
    for (const label of ['字体', '字号', '颜色', '对齐']) {
      expect(text, label).toContain(label)
    }
    // 加粗 / 斜体图标按钮仍带 aria-label
    expect(byLabel('加粗')).not.toBeNull()
    expect(byLabel('斜体')).not.toBeNull()
    // 中频样式不再挤成弹层：行距 / 堆叠层级住进「更多」，默认收起
    expect(text).not.toContain('行距')
    expect(text).not.toContain('堆叠层级')
    // 文字内容仍在列表里（工具条不管它）
    expect(text).toContain('内容')
  })

  it('每一条属性都挂着定位锚点——工具条把它们从平铺列表拿走，锚点要一起带过来', async () => {
    await mount('axes_0.title')
    // 本轮修掉的缺陷：`TEXT_BAR_PROPS` 把这六条从平铺列表里摘走了
    // （平铺那一份是有 `data-prop` 的），工具条却没有补上锚点。表现是
    // 「问题面板上点定位，焦点没落到字段上」——而界面并不报错。
    for (const prop of FIGURE_TEXT_SINGLE_PROPS) {
      const path = propertyPathOf('figureText', prop)
      expect(path, prop).toBeTruthy()
      expect(host.querySelector(`[data-prop="${path}"]`), prop).not.toBeNull()
    }
  })

  it('工具条摘走的那几条 = 规范表算出来的那几条（不许手抄第二份）', () => {
    const derived = new Set(
      FIGURE_TEXT_SINGLE_PROPS.map((p) => propertyPathOf('figureText', p)).filter(Boolean),
    )
    expect(new Set(TEXT_BAR_PROPS)).toEqual(derived)
  })

  it('同一属性不出两套控件：颜色行只有一个取色器', async () => {
    await mount('axes_0.title')
    expect(host.querySelectorAll('input[type="color"]')).toHaveLength(1)
  })

  it('manifest 没给的字段不画控件', async () => {
    const slim: ManifestElement = {
      ...titleEl,
      editable: titleEl.editable.filter(
        (x) => !['style', 'fontfamily'].includes(x.prop),
      ),
    }
    useRenderStore.getState().patch(renderKeyOf(livePanel()), {
      manifest: { ...manifest, elements: [manifest.elements[0], slim] },
    })
    await mount('axes_0.title')
    expect(byLabel('加粗')).not.toBeNull()
    expect(byLabel('斜体')).toBeNull()
    expect(host.textContent).not.toContain('字体')
  })

  it('背景 / 描边 / 行距经「更多」可达（展示注册表接管，不再是齿轮弹层）', async () => {
    await mount('axes_0.title')
    const more = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === '更多',
    )!
    await act(async () => {
      more.click()
    })
    const text = host.textContent ?? ''
    for (const label of ['行距', '垂直对齐', '旋转']) {
      expect(text, label).toContain(label)
    }
  })
})

/* ============================== 离散动作 =================================== */

describe('加粗 / 字形：一次点击 = 一条历史 + 一次渲染', () => {
  it('点加粗写 weight=bold，再点回 normal', async () => {
    await mount('axes_0.title')
    await clickBar('加粗')
    expect(overrideOf('axes_0.title', 'weight')).toBe('bold')
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)

    await clickBar('加粗')
    expect(overrideOf('axes_0.title', 'weight')).toBe('normal')
    expect(useDocumentStore.getState().past).toHaveLength(2)
  })

  it('斜体按钮改 style，两个按钮各记各的账', async () => {
    await mount('axes_0.title')
    await clickBar('斜体')
    expect(overrideOf('axes_0.title', 'style')).toBe('italic')
    expect(overrideOf('axes_0.title', 'weight')).toBeUndefined()
  })
})

/* ============================ 行里的写入路径 =============================== */

describe('行里的控件与属性页同一条写入路径', () => {
  it('文字颜色照旧局部预览：整轮不发后端', async () => {
    await mount('axes_0.title')
    const input = host.querySelector('input[type="color"]') as HTMLInputElement
    await act(async () => {
      typeInto(input, '#ff0000')
    })
    flushPreviewFrame()
    const glyphs = svgNode('axes_0.title').querySelector('g[transform]') as SVGElement
    expect(glyphs.style.getPropertyValue('fill')).toBe(css('#ff0000'))
    expect(engineRender).not.toHaveBeenCalled()
    expect(overrideOf('axes_0.title', 'color')).toBe('#ff0000')
  })

  it('改过字号后：状态点 + 行尾恢复按钮，点恢复清掉 override', async () => {
    await mount('axes_0.title')
    useDocumentStore.getState().commit(literal('预置 override'), (d) => {
      const pl = d.objects.find((o) => o.id === 'p1')
      if (pl?.type === 'panel') {
        pl.overrides.push({ gid: 'axes_0.title', prop: 'fontsize', value: 14 })
      }
    })
    await act(async () => {})
    const reset = document.querySelector('[aria-label="恢复字号"]') as HTMLElement
    expect(reset).not.toBeNull()
    await act(async () => {
      reset.click()
    })
    expect(overrideOf('axes_0.title', 'fontsize')).toBeUndefined()
  })
})
