/**
 * 数据系列类角色的属性面板（审计 P2 T11 / T15 / T16 / T19 / T20 / T21）：
 * 整张图、曲线、散点、柱形、误差棒、填充区域。字段形状与 engine/manifest.py
 * 各 `_*_fields` 同形；写入经真实的 documentStore，断言落在 override 上。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
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

export const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const num = (prop: string, value: number, extra: Record<string, unknown> = {}) =>
  f(prop, 'number', value, { min: 0, max: 8, step: 0.1, unit: 'pt', ...extra })
const alpha = (value: number) => f('alpha', 'number', value, { min: 0, max: 1, step: 0.05 })

/** 与 `_line_fields` 同形 */
export const lineFields = (
  over: { marker?: string; shape?: EditableField['marker_current'] } = {},
): EditableField[] => [
  f('label', 'text', 'Linear fit'),
  f('color', 'color', '#c0562a'),
  num('linewidth', 1.1, { min: 0.1 }),
  f('linestyle', 'enum', '-', { options: ['-', '--', ':', '-.'] }),
  alpha(0.75),
  f('visible', 'bool', true),
  f('marker', 'enum', over.marker ?? 'None', {
    options: ['None', 'o', 's', 'D', '^', 'v', '<', '>', 'x', '+', '*', '.'],
    group: '线条与标记',
    ...(over.shape ? { marker_current: over.shape } : {}),
  }),
  num('markersize', 6, { max: 20, step: 0.5, group: '线条与标记' }),
  f('markerfacecolor', 'color', '#c0562a', { group: '线条与标记' }),
  f('markeredgecolor', 'color', '#c0562a', { group: '线条与标记' }),
  f('zorder', 'number', 2, { min: -5, max: 50, step: 1, group: '排列' }),
]

/** 与 `_collection_fields(label=True)` 的散点形状同形 */
export const scatterFields = (
  over: {
    marker?: string
    hint?: string
    shape?: EditableField['marker_current']
    /** override 之前那个形状（引擎只在真的改过时才发它） */
    orig?: EditableField['marker_original']
  } = {},
): EditableField[] => [
  f('label', 'text', 'Observed'),
  f('facecolor', 'color', '#1b3a6b'),
  f('size', 'number', 12, { min: 1, max: 400, step: 1, unit: 'pt²' }),
  f('marker', 'enum', over.marker ?? 'original', {
    options: ['original', 'o', 's', 'D', '^', 'v', '<', '>', 'x', '+', '*', '.', 'p', 'h'],
    ...(over.hint ? { hint: over.hint } : {}),
    ...(over.shape ? { marker_current: over.shape } : {}),
    ...(over.orig ? { marker_original: over.orig } : {}),
  }),
  f('edgecolor', 'color', '#1b3a6b'),
  num('linewidth', 1.1),
  f('linestyle', 'enum', '-', { options: ['-', '--', '-.', ':'], group: '线条与填充' }),
  alpha(0.75),
  f('visible', 'bool', true),
  f('hatch', 'enum', '', { options: ['', '/', '\\\\', '|', '-', '+', 'x', 'o', 'O', '.', '*', '//', 'xx'], group: '线条与填充' }),
]

/** 与 `_bar_series_fields` 同形 */
export const barSeriesFields = (): EditableField[] => [
  f('label', 'text', 'Measurements'),
  f('facecolor', 'color', '#47749e'),
  f('edgecolor', 'color', '#000000'),
  num('linewidth', 1, { max: 5 }),
  f('bar_width', 'number', 0.8, { min: 0.01, max: 5, step: 0.02, unit: '数据单位' }),
  alpha(1),
  f('visible', 'bool', true),
  f('zorder', 'number', 2, { min: -5, max: 50, step: 1, group: '排列' }),
]

/** 与 `_errorbar_fields` 同形 */
export const errorbarFields = (): EditableField[] => [
  f('color', 'color', '#222222'),
  num('linewidth', 1.5, { min: 0.1, max: 5 }),
  num('capsize', 8, { max: 15, step: 0.5 }),
  num('cap_thickness', 1, { min: 0.1, max: 5 }),
  alpha(1),
  f('visible', 'bool', true),
]

/** 与 `_collection_fields(label=False)` 的填充区域形状同形 */
export const fillFields = (): EditableField[] => [
  f('facecolor', 'color', '#1f77b4'),
  f('edgecolor', 'color', '#000000'),
  num('linewidth', 1),
  f('linestyle', 'enum', '-', { options: ['-', '--', '-.', ':'], group: '线条与填充' }),
  alpha(0.25),
  f('visible', 'bool', true),
  f('hatch', 'enum', '', { options: ['', '/', '\\\\', '|', '-', '+', 'x', 'o', 'O', '.', '*', '//', 'xx'], group: '线条与填充' }),
]

/** 与 `_patch_fields` 同形（脚本 add_patch 出的独立形状） */
export const patchFields = (over: { fill?: boolean } = {}): EditableField[] => [
  f('facecolor', 'color', '#1f77b4'),
  f('fill', 'bool', over.fill ?? true),
  f('edgecolor', 'color', '#000000'),
  num('linewidth', 1),
  f('linestyle', 'enum', '-', { options: ['-', '--', '-.', ':'] }),
  f('hatch', 'enum', '', { options: ['', '/', '\\\\', '|', '-', '+', 'x', 'o', 'O', '.', '*', '//', 'xx'] }),
  alpha(1),
  f('visible', 'bool', true),
  f('zorder', 'number', 2, { min: -5, max: 50, step: 1, group: '排列' }),
]

/** 与 `_fields_for(figure)` 同形 */
export const figureFields = (over: { transparent?: boolean } = {}): EditableField[] => [
  f('size_mm', 'pair', [80, 57.6], { unit: 'mm' }),
  f('facecolor', 'color', '#ffffff', { group: '背景' }),
  f('transparent', 'bool', over.transparent ?? false, { group: '背景' }),
]

export const elementOf = (
  gid: string,
  role: string,
  label: string,
  editable: EditableField[],
  extra: Partial<ManifestElement> = {},
): ManifestElement =>
  ({ gid, role, label, bbox: [0.2, 0.2, 0.6, 0.6], draggable: false, editable, ...extra }) as ManifestElement

export const makeManifest = (elements: ManifestElement[]): Manifest =>
  ({
    rev: 1,
    size_mm: [80, 57.6],
    elements: [
      { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: figureFields(), draggable: false },
      { gid: 'axes_0', role: 'axes', label: '子图 1', bbox: [0.1, 0.1, 0.8, 0.8], editable: [], draggable: true },
      ...elements,
    ],
  }) as unknown as Manifest

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 80,
    h: 57.6,
    fileId: 'Fig2.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 57.6,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
export const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let host: HTMLDivElement
let manifest: Manifest = makeManifest([])

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

export function seedRender(m: Manifest) {
  manifest = m
  engineRender.mockResolvedValue({ rev: 2, manifest: m, svg: MATPLOTLIB_SVG, warnings: [] })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig2.pdf',
    manifest: m,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig2.pdf': renderKeyOf(panelOf()) } })
}

export async function mount(gids: string[]) {
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

const textOf = () => host.textContent ?? ''
const buttons = () => Array.from(host.querySelectorAll('button'))
const byText = (text: string) => buttons().find((b) => b.textContent?.trim() === text)
const row = (prop: string) => host.querySelector<HTMLElement>(`[data-prop="${prop}"]`)
const inputIn = (prop: string) => row(prop)?.querySelector<HTMLInputElement>('input') ?? null

async function typeNumber(input: HTMLInputElement, text: string) {
  await act(async () => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  })
}

async function openMore() {
  const more = byText('更多')
  if (more && more.getAttribute('aria-expanded') !== 'true') {
    await act(async () => {
      more.click()
    })
  }
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_series')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  resetPreview()
  useUiStore.setState({ selectedGids: [] })
})

/* ------------------------------ 透明度百分比 ------------------------------ */

describe('透明度按百分比显示与输入（T16 / T20）', () => {
  it('曲线的透明度显示 75 与 %，输入 40 写回 override 0.4', async () => {
    seedRender(makeManifest([elementOf('axes_0.lines_0', 'line', '曲线 “Linear fit”', lineFields())]))
    await mount(['axes_0.lines_0'])
    await openMore()
    const input = inputIn('alpha')!
    expect(input).toBeTruthy()
    expect(input.value).toBe('75')
    expect(row('alpha')!.textContent).toContain('%')
    // 界面上不出现 0.75 这种小数
    expect(row('alpha')!.textContent).not.toContain('0.75')
    await typeNumber(input, '40')
    expect(overrideOf('axes_0.lines_0', 'alpha')).toBe(0.4)
  })

  it('批量改两条曲线的透明度也是百分比，写回 0–1', async () => {
    seedRender(
      makeManifest([
        elementOf('axes_0.lines_0', 'line', '曲线 “a”', lineFields()),
        elementOf('axes_0.lines_1', 'line', '曲线 “b”', lineFields()),
      ]),
    )
    await mount(['axes_0.lines_0', 'axes_0.lines_1'])
    const input = host.querySelector<HTMLInputElement>('input[aria-label="透明度"]')!
    expect(input).toBeTruthy()
    expect(input.value).toBe('75')
    await typeNumber(input, '30')
    expect(overrideOf('axes_0.lines_0', 'alpha')).toBe(0.3)
    expect(overrideOf('axes_0.lines_1', 'alpha')).toBe(0.3)
  })
})

/* -------------------------------- 整张图 --------------------------------- */

describe('整张图：图幅带 W / H，背景在首屏（T11）', () => {
  it('图幅两个框有可见的 W / H 前缀，可达名仍是完整的宽 / 高', async () => {
    seedRender(makeManifest([]))
    await mount(['figure'])
    const size = row('size_mm')!
    expect(size).toBeTruthy()
    const inputs = Array.from(size.querySelectorAll('input'))
    expect(inputs.map((i) => i.getAttribute('aria-label'))).toEqual(['图幅 宽 (mm)', '图幅 高 (mm)'])
    const prefixes = Array.from(size.querySelectorAll('span'))
      .map((s) => s.textContent?.trim())
      .filter((t) => t === 'W' || t === 'H')
    expect(prefixes).toEqual(['W', 'H'])
  })

  it('背景色与透明背景不用打开「更多」就在；透明背景开着时背景色收起', async () => {
    seedRender(makeManifest([]))
    await mount(['figure'])
    expect(row('facecolor')).toBeTruthy()
    expect(row('transparent')).toBeTruthy()
    // 没有需要折叠的东西时不该出现「更多」
    expect(byText('更多')).toBeUndefined()

    const toggle = row('transparent')!.querySelector<HTMLElement>('[role="switch"], button, input')!
    await act(async () => {
      toggle.click()
    })
    expect(overrideOf('figure', 'transparent')).toBe(true)
    expect(row('facecolor')).toBeNull()
  })
})

/* --------------------------------- 曲线 ---------------------------------- */

describe('曲线：标记为无时不摆标记参数，选了标记才铺开（T15）', () => {
  it('marker = None：没有标记大小 / 标记填充 / 标记描边三行', async () => {
    seedRender(makeManifest([elementOf('axes_0.lines_0', 'line', '曲线 “Linear fit”', lineFields())]))
    await mount(['axes_0.lines_0'])
    await openMore()
    expect(row('marker')).toBeTruthy()
    expect(row('markersize')).toBeNull()
    expect(row('markerfacecolor')).toBeNull()
    expect(row('markeredgecolor')).toBeNull()
  })

  it('marker = o：三行就在首屏（不藏在「更多」里），颜色 / 线宽 / 线型仍排在前', async () => {
    seedRender(
      makeManifest([elementOf('axes_0.lines_0', 'line', '曲线 “Linear fit”', lineFields({ marker: 'o' }))]),
    )
    await mount(['axes_0.lines_0'])
    // 不点「更多」
    const props = Array.from(host.querySelectorAll<HTMLElement>('[data-prop]')).map((e) => e.dataset.prop)
    const idx = (p: string) => props.indexOf(p)
    for (const p of ['color', 'linewidth', 'linestyle', 'marker', 'markersize', 'markerfacecolor', 'markeredgecolor']) {
      expect(idx(p), p).toBeGreaterThanOrEqual(0)
    }
    expect(idx('color')).toBeLessThan(idx('marker'))
    expect(idx('marker')).toBeLessThan(idx('markersize'))
    expect(idx('markersize')).toBeLessThan(idx('markerfacecolor'))
    // 透明度仍在「更多」里
    expect(row('alpha')).toBeNull()
  })

  it('用户改过标记大小后再把标记设为无，那一行照样显示（改过的必须能看到）', async () => {
    seedRender(makeManifest([elementOf('axes_0.lines_0', 'line', '曲线 “Linear fit”', lineFields())]))
    useDocumentStore.getState().commit(literal('改标记大小'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 'axes_0.lines_0', prop: 'markersize', value: 9 })
    })
    await mount(['axes_0.lines_0'])
    expect(row('markersize')).toBeTruthy()
    expect(row('markerfacecolor')).toBeNull()
  })
})

/* -------------------------------- 误差棒 ---------------------------------- */

describe('误差棒：示意图说清哪个数字改图上的哪一段（T20）', () => {
  const seed = async () => {
    seedRender(makeManifest([elementOf('axes_0.errorbar_0', 'errorbar', '误差棒 2', errorbarFields())]))
    await mount(['axes_0.errorbar_0'])
  }
  const diagram = () => host.querySelector<SVGElement>('[data-errorbar-diagram]')
  const segActive = (seg: string) =>
    diagram()!.querySelector(`[data-seg="${seg}"][data-active="true"]`) !== null

  it('术语保留（端帽长度 / 端帽线宽 / pt），旁边有一张示意图', async () => {
    await seed()
    expect(row('capsize')!.textContent).toContain('端帽长度')
    expect(row('capsize')!.textContent).toContain('pt')
    expect(row('cap_thickness')!.textContent).toContain('端帽线宽')
    expect(diagram()).toBeTruthy()
    // 无装饰、不吃焦点：它是图注，不是控件
    expect(diagram()!.getAttribute('aria-hidden')).toBe('true')
    expect(diagram()!.querySelector('[tabindex]')).toBeNull()
  })

  it('没聚焦时哪一段都不亮', async () => {
    await seed()
    expect(diagram()!.getAttribute('data-active')).toBe('')
    expect(segActive('linewidth')).toBe(false)
    expect(segActive('cap')).toBe(false)
  })

  const capStroke = () =>
    Number(diagram()!.querySelector('[data-seg="cap"]')!.getAttribute('stroke-width'))

  it('聚焦到端帽长度：端帽亮，上方出现水平量尺；线宽那一段不亮', async () => {
    await seed()
    await act(async () => {
      inputIn('capsize')!.focus()
    })
    expect(diagram()!.getAttribute('data-active')).toBe('capsize')
    expect(segActive('cap')).toBe(true)
    expect(segActive('linewidth')).toBe(false)
    expect(diagram()!.querySelector('[data-seg="capsize-measure"]')).toBeTruthy()
  })

  /**
   * 两个端帽档亮的是**同两条线**，区别必须在图上看得出来：长度档给量尺、
   * 线宽档把线画粗。真浏览器 72 px 实测过——竖向量尺只有 3 px 高，是噪点
   * 不是说明，所以线宽这一维靠粗细本身表达。
   */
  it('聚焦到端帽线宽：同两条端帽亮，但画得明显更粗，且不画水平量尺', async () => {
    await seed()
    await act(async () => {
      inputIn('capsize')!.focus()
    })
    const thin = capStroke()
    await act(async () => {
      inputIn('cap_thickness')!.focus()
    })
    expect(diagram()!.getAttribute('data-active')).toBe('cap_thickness')
    expect(segActive('cap')).toBe(true)
    expect(capStroke(), '两个端帽档在图上长得一样，用户分不出改的是哪一维').toBeGreaterThan(thin * 1.5)
    expect(diagram()!.querySelector('[data-seg="capsize-measure"]')).toBeNull()
  })

  it('聚焦到线宽：亮的是竖线，不是端帽', async () => {
    await seed()
    await act(async () => {
      inputIn('linewidth')!.focus()
    })
    expect(segActive('linewidth')).toBe(true)
    expect(segActive('cap')).toBe(false)
  })

  it('焦点离开后熄灭；颜色 / 透明度这类非几何字段不点亮任何一段', async () => {
    await seed()
    await act(async () => {
      inputIn('capsize')!.focus()
    })
    expect(diagram()!.getAttribute('data-active')).toBe('capsize')
    await act(async () => {
      inputIn('alpha')!.focus()
    })
    expect(diagram()!.getAttribute('data-active')).toBe('')
  })

  it('示意图不承接任何字段：三行照旧能改，写回的是 pt 原值', async () => {
    await seed()
    await typeNumber(inputIn('capsize')!, '10')
    expect(overrideOf('axes_0.errorbar_0', 'capsize')).toBe(10)
    await typeNumber(inputIn('cap_thickness')!, '2.5')
    expect(overrideOf('axes_0.errorbar_0', 'cap_thickness')).toBe(2.5)
  })
})

/* --------------------------------- 柱形 ---------------------------------- */

describe('柱形：单位不折行，纹理有一条能力提示与源对象入口（T19）', () => {
  it('柱宽的「数据单位」不折行——让位的是输入框', async () => {
    seedRender(
      makeManifest([elementOf('axes_0.bars_0', 'bar_series', '柱形系列 “Measurements”', barSeriesFields())]),
    )
    await mount(['axes_0.bars_0'])
    await openMore()
    const unit = Array.from(row('bar_width')!.querySelectorAll('span')).find(
      (e) => e.textContent === '数据单位',
    )!
    expect(unit, '柱宽那一行没有完整的单位').toBeTruthy()
    // jsdom 没有布局引擎，量不出折行（真浏览器截图 95 里它被挤成了两行）。
    // 能判的是规则本身：单位不收缩、不折行。
    expect(unit.className).toContain('whitespace-nowrap')
    expect(unit.className).toContain('shrink-0')
  })

  it('引擎没发纹理字段时说一句为什么，并给「到源文件里改」的入口', async () => {
    seedRender(
      makeManifest([elementOf('axes_0.bars_0', 'bar_series', '柱形系列 “Measurements”', barSeriesFields())]),
    )
    await mount(['axes_0.bars_0'])
    const note = host.querySelector('[data-absent-appearance="hatch"]')!
    expect(note, '柱形面板里连一句「纹理在哪儿改」都没有').toBeTruthy()
    expect(note.textContent).toContain('纹理')
    expect(note.textContent).toContain('来自脚本')
    // 不是摆一个点了没反应的控件
    expect(host.querySelector('[aria-label="纹理"]')).toBeNull()

    expect(useInspectorPrefs.getState().advancedOpen['bar_series']).toBeFalsy()
    await act(async () => {
      byText('到源文件里改')!.click()
    })
    expect(useInspectorPrefs.getState().advancedOpen['bar_series']).toBe(true)
  })

  it('引擎哪天真发了纹理字段，这条提示自己就没了——不用有人回来删', async () => {
    const withHatch = [
      ...barSeriesFields(),
      f('hatch', 'enum', '//', { options: ['', '/', '//', 'xx'] }),
    ]
    seedRender(
      makeManifest([elementOf('axes_0.bars_0', 'bar_series', '柱形系列 “Measurements”', withHatch)]),
    )
    await mount(['axes_0.bars_0'])
    expect(host.querySelector('[data-absent-appearance="hatch"]')).toBeNull()
    await openMore()
    expect(row('hatch')).toBeTruthy()
  })
})

/* ---------------------------- 填充区域与纹理 ------------------------------ */

describe('填充区域：填充一组、描边一组，纹理有名字（T21）', () => {
  it('中文界面上没有裸露的 hatch，那一行叫「纹理」', async () => {
    seedRender(makeManifest([elementOf('axes_0.collections_0', 'fill', '填充区域 1', fillFields())]))
    await mount(['axes_0.collections_0'])
    await openMore()
    expect(row('hatch')!.textContent).toContain('纹理')
    expect(textOf()).not.toContain('hatch')
    expect(host.querySelector('[aria-label="hatch"]')).toBeNull()
  })

  it('首屏顺序：填充色 · 纹理 → 描边色 · 线宽 · 线型 → 透明度', async () => {
    seedRender(makeManifest([elementOf('axes_0.collections_0', 'fill', '填充区域 1', fillFields())]))
    await mount(['axes_0.collections_0'])
    const props = Array.from(host.querySelectorAll<HTMLElement>('[data-prop]')).map((e) => e.dataset.prop)
    expect(props.slice(0, 6)).toEqual([
      'facecolor', 'hatch', 'edgecolor', 'linewidth', 'linestyle', 'alpha',
    ])
  })

  it('形状：关掉「填充」之后填充色与纹理收起——写了也不显形', async () => {
    seedRender(makeManifest([elementOf('axes_0.patches_0', 'patch', '形状 1', patchFields())]))
    await mount(['axes_0.patches_0'])
    expect(row('facecolor')).toBeTruthy()
    expect(row('hatch')).toBeTruthy()
    const toggle = row('fill')!.querySelector<HTMLElement>('[role="switch"]')!
    await act(async () => {
      toggle.click()
    })
    expect(overrideOf('axes_0.patches_0', 'fill')).toBe(false)
    expect(row('facecolor')).toBeNull()
    expect(row('hatch')).toBeNull()
    // 开关自己当然还在，否则就再也开不回来了
    expect(row('fill')).toBeTruthy()
    expect(row('edgecolor')).toBeTruthy()
  })
})

/* --------------------------------- 散点 ---------------------------------- */

describe('散点：继承有小状态点，面积单位带一句短提示（T16）', () => {
  it('标记 = 脚本原始：画的是继承状态点，不是 ↺ 那种像按钮的字形', async () => {
    seedRender(makeManifest([elementOf('axes_0.collections_0', 'scatter', '散点 “Observed”', scatterFields())]))
    await mount(['axes_0.collections_0'])
    const marker = row('marker')!
    expect(marker.textContent).toContain('脚本原始')
    expect(marker.querySelector('[data-marker-inherited]')).toBeTruthy()
    expect(marker.textContent).not.toContain('↺')
  })

  it('标记 = 脚本原始 + 引擎给了形状：形状与继承状态点并列，不互相顶替（fu-marker）', async () => {
    seedRender(
      makeManifest([
        elementOf(
          'axes_0.collections_0',
          'scatter',
          '散点 “Observed”',
          scatterFields({ shape: { kind: 'named', name: 'o' } }),
        ),
      ]),
    )
    await mount(['axes_0.collections_0'])
    const marker = row('marker')!
    // 用户看得见图上是个圆
    expect(marker.querySelector('[data-marker-preview] circle')).toBeTruthy()
    // 「这是继承来的」没有因此消失 —— 四档值语义一档都不许压扁
    expect(marker.querySelector('[data-marker-inherited]')).toBeTruthy()
    expect(marker.textContent).toContain('脚本原始')
    expect(marker.textContent).toContain('圆点')
  })

  it('引擎认不出名字时照顶点画（曲线的元组标记也一样，fu-marker）', async () => {
    seedRender(
      makeManifest([
        elementOf(
          'axes_0.lines_0',
          'line',
          '曲线 “Linear fit”',
          lineFields({
            marker: '(5, 1, 0)',
            shape: {
              kind: 'path',
              vertices: [
                [0, 0.5],
                [0.5, -0.5],
                [-0.5, -0.5],
              ],
              codes: null,
            },
          }),
        ),
      ]),
    )
    await mount(['axes_0.lines_0'])
    const marker = row('marker')!
    expect(marker.querySelector('[data-marker-preview] path')).toBeTruthy()
    // 原始代码不丢失：认不出的取值仍旧原样摆在文字里
    expect(marker.textContent).toContain('(5, 1, 0)')
  })

  it('引擎没发事实（老引擎）：退回只有继承状态点，一个字节不变（fu-marker）', async () => {
    seedRender(makeManifest([elementOf('axes_0.collections_0', 'scatter', '散点 “Observed”', scatterFields())]))
    await mount(['axes_0.collections_0'])
    const marker = row('marker')!
    expect(marker.querySelector('[data-marker-preview]')).toBeNull()
    expect(marker.querySelector('[data-marker-inherited]')).toBeTruthy()
  })

  it('多选：两个散点的形状不一样就谁的都不画（fu-marker）', async () => {
    seedRender(
      makeManifest([
        elementOf(
          'axes_0.collections_0',
          'scatter',
          '散点 “Observed”',
          scatterFields({ shape: { kind: 'named', name: 'o' } }),
        ),
        elementOf(
          'axes_0.collections_1',
          'scatter',
          '散点 “Model”',
          scatterFields({ shape: { kind: 'named', name: 's' } }),
        ),
      ]),
    )
    await mount(['axes_0.collections_0', 'axes_0.collections_1'])
    // 多选的字段行不挂 `data-prop`（那是单元素定位服务的落点），按可达名找
    const marker = host.querySelector<HTMLElement>('button[aria-label="标记"]')!
    // 取值一致（都是「脚本原始」，这一行不显示「多个值」）不等于形状一致
    expect(marker.textContent).toContain('脚本原始')
    expect(marker.querySelector('[data-marker-preview]')).toBeNull()
    expect(marker.querySelector('[data-marker-inherited]')).toBeTruthy()
  })

  it('多选：形状一致时照画（否则上一条会恒真）（fu-marker）', async () => {
    seedRender(
      makeManifest([
        elementOf(
          'axes_0.collections_0',
          'scatter',
          '散点 “Observed”',
          scatterFields({ shape: { kind: 'named', name: 'o' } }),
        ),
        elementOf(
          'axes_0.collections_1',
          'scatter',
          '散点 “Model”',
          scatterFields({ shape: { kind: 'named', name: 'o' } }),
        ),
      ]),
    )
    await mount(['axes_0.collections_0', 'axes_0.collections_1'])
    const marker = host.querySelector<HTMLElement>('button[aria-label="标记"]')!
    expect(marker.querySelector('[data-marker-preview] circle')).toBeTruthy()
  })

  it('换过标记之后，「脚本原始」那一格仍画得出脚本原来那个形状（cap-marker-orig）', async () => {
    seedRender(
      makeManifest([
        elementOf(
          'axes_0.collections_0',
          'scatter',
          '散点 “Observed”',
          scatterFields({
            marker: '^',
            shape: { kind: 'named', name: '^' },
            orig: { kind: 'named', name: 'o' },
          }),
        ),
      ]),
    )
    await mount(['axes_0.collections_0'])
    const trigger = row('marker')!.querySelector<HTMLButtonElement>('button[aria-label="标记"]')!
    await act(async () => {
      trigger.click()
    })
    const orig = document.querySelector<HTMLElement>('[data-value="original"]')!
    // 「回到脚本原始」会变成什么形状，用户现在看得见
    expect(orig.querySelector('[data-marker-preview] circle')).toBeTruthy()
    expect(orig.getAttribute('aria-label')).toContain('圆点')
    // 触发按钮仍说当前那个（三角），两个事实各说各的那一半
    expect(trigger.querySelector('[data-marker-preview] path')).toBeTruthy()
    expect(trigger.querySelector('[data-marker-preview] circle')).toBeNull()
  })

  it('多选：一个改过一个没改，原样就谁的都不画（cap-marker-orig）', async () => {
    seedRender(
      makeManifest([
        elementOf(
          'axes_0.collections_0',
          'scatter',
          '散点 “Observed”',
          scatterFields({
            marker: '^',
            shape: { kind: 'named', name: '^' },
            orig: { kind: 'named', name: 'o' },
          }),
        ),
        // 这一个没改过 —— 引擎根本不发 `marker_original`
        elementOf(
          'axes_0.collections_1',
          'scatter',
          '散点 “Model”',
          scatterFields({ shape: { kind: 'named', name: '^' } }),
        ),
      ]),
    )
    await mount(['axes_0.collections_0', 'axes_0.collections_1'])
    const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="标记"]')!
    await act(async () => {
      trigger.click()
    })
    const orig = document.querySelector<HTMLElement>('[data-value="original"]')!
    // 「有的改过有的没改」也是一种不一致：拿改过那个的原样去画就是替另一个撒谎
    expect(orig.querySelector('[data-marker-preview]')).toBeNull()
    expect(orig.querySelector('[data-marker-inherited]')).toBeTruthy()
  })

  it('多选：两个都改过且原样一致时照画（否则上一条会恒真）（cap-marker-orig）', async () => {
    seedRender(
      makeManifest([
        elementOf(
          'axes_0.collections_0',
          'scatter',
          '散点 “Observed”',
          scatterFields({
            marker: '^',
            shape: { kind: 'named', name: '^' },
            orig: { kind: 'named', name: 'o' },
          }),
        ),
        elementOf(
          'axes_0.collections_1',
          'scatter',
          '散点 “Model”',
          scatterFields({
            marker: '^',
            shape: { kind: 'named', name: '^' },
            orig: { kind: 'named', name: 'o' },
          }),
        ),
      ]),
    )
    await mount(['axes_0.collections_0', 'axes_0.collections_1'])
    const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="标记"]')!
    await act(async () => {
      trigger.click()
    })
    const orig = document.querySelector<HTMLElement>('[data-value="original"]')!
    expect(orig.querySelector('[data-marker-preview] circle')).toBeTruthy()
  })

  it('标记 = o：画真实形状，没有继承状态点', async () => {
    seedRender(
      makeManifest([
        elementOf('axes_0.collections_0', 'scatter', '散点 “Observed”', scatterFields({ marker: 'o' })),
      ]),
    )
    await mount(['axes_0.collections_0'])
    const marker = row('marker')!
    expect(marker.querySelector('svg circle')).toBeTruthy()
    expect(marker.querySelector('[data-marker-inherited]')).toBeNull()
  })

  it('点大小保留 pt²，并带一句「这是面积」的短提示；线宽那种没歧义的不带', async () => {
    seedRender(makeManifest([elementOf('axes_0.collections_0', 'scatter', '散点 “Observed”', scatterFields())]))
    await mount(['axes_0.collections_0'])
    expect(row('size')!.textContent).toContain('pt²')
    const hinted = inputIn('size')!.closest('[title]')!.getAttribute('title')!
    expect(hinted).toContain('面积')
    expect(hinted).toContain('pt²')
    // 提示是给会被读错的那几条准备的，不是每一行都挂
    expect(inputIn('linewidth')!.closest('[title]')).toBeNull()
  })
})

/* ------------------------------ 宽度的名字 -------------------------------- */

describe('全产品只有一个宽度名词「线宽」，限定词说哪条线（T16 / T20）', () => {
  it('散点的 linewidth 叫「描边线宽」，紧跟在描边色后面', async () => {
    seedRender(makeManifest([elementOf('axes_0.collections_0', 'scatter', '散点 “Observed”', scatterFields())]))
    await mount(['axes_0.collections_0'])
    expect(row('linewidth')!.textContent).toContain('描边线宽')
    const props = Array.from(host.querySelectorAll<HTMLElement>('[data-prop]')).map((e) => e.dataset.prop)
    expect(props.indexOf('linewidth') - props.indexOf('edgecolor')).toBe(1)
  })

  it('曲线与误差棒的 linewidth 是那条线本身，仍叫「线宽」', async () => {
    seedRender(makeManifest([elementOf('axes_0.lines_0', 'line', '曲线 “Linear fit”', lineFields())]))
    await mount(['axes_0.lines_0'])
    expect(row('linewidth')!.textContent).toContain('线宽')
    expect(row('linewidth')!.textContent).not.toContain('描边')
  })

  it('误差棒面板里没有「粗细」这个词——端帽那条也叫线宽', async () => {
    seedRender(makeManifest([elementOf('axes_0.errorbar_0', 'errorbar', '误差棒 2', errorbarFields())]))
    await mount(['axes_0.errorbar_0'])
    expect(row('cap_thickness')!.textContent).toContain('端帽线宽')
    expect(textOf()).not.toContain('粗细')
  })
})

/* ------------------------------- 可达名 ---------------------------------- */

describe('数值行的可达名：标签在视觉与辅助技术中一致（T11 验收）', () => {
  it('误差棒的四个数值框各有自己的名字，带单位', async () => {
    seedRender(makeManifest([elementOf('axes_0.errorbar_0', 'errorbar', '误差棒 2', errorbarFields())]))
    await mount(['axes_0.errorbar_0'])
    const named = (prop: string) => inputIn(prop)!.getAttribute('aria-label')
    expect(named('linewidth')).toBe('线宽 (pt)')
    expect(named('capsize')).toBe('端帽长度 (pt)')
    expect(named('cap_thickness')).toBe('端帽线宽 (pt)')
    // 百分比控件自己带名字（不带单位——单位就在框里那个 %）
    expect(named('alpha')).toBe('透明度')
  })
})

export { textOf, byText, row, inputIn, typeNumber, openMore, host as hostRef }
