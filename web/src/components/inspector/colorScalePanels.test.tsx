/**
 * 热图 / 图像与颜色条的属性页（审计 T22 / T23）。
 *
 * 钉住的合同：
 *   1. 色条与它上色的图像**共用一份色阶**这件事说出口，并给「选中对方」的
 *      入口；判据只认 manifest 的 `mappable_gid`，不猜「cmap 名字相同」；
 *   2. 色阶上下限并排成一行，两条仍各写各的 override、各有各的恢复按钮；
 *   3. 透明度按百分比显示、写回 0–1（与 p2-elem-a 的 `PercentField` 同一份）；
 *   4. 图像的尺寸区不再有两段常驻说明：组标题写清作用对象（「子图尺寸 ·
 *      子图 1」），旁边一个来源入口，原理进那个按钮的悬停提示。
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
import { colorScalePartner } from './ColorScaleLink'

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

const CMAPS = ['viridis', 'plasma', 'Greys']

/** 与 engine/manifest.py `_axes_fields` 同形（只留这里用得到的几条） */
const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.12, 0.11, 0.75, 0.77],
  draggable: true,
  resizable: true,
  editable: [
    f('position', 'rect', [0.125, 0.11, 0.62, 0.77]),
    f('facecolor', 'color', '#ffffff'),
  ],
} as unknown as ManifestElement

/** 与 engine/manifest.py `_image_fields` 同形 */
const imageEl: ManifestElement = {
  gid: 'axes_0.images_0',
  role: 'image',
  label: '图像 1',
  bbox: [0.125, 0.11, 0.62, 0.77],
  draggable: true,
  resizable: true,
  // 位图的几何落点是宿主子图（引擎给的代理 gid）
  geom_gid: 'axes_0',
  editable: [
    f('cmap', 'enum', 'viridis', { options: CMAPS, group: '颜色映射' }),
    f('vmin', 'number', 0, { step: 0.35, group: '颜色映射' }),
    f('vmax', 'number', 35, { step: 0.35, group: '颜色映射' }),
    f('alpha', 'number', 1, { min: 0, max: 1, step: 0.05 }),
    f('interpolation', 'enum', 'nearest', { options: ['nearest', 'bilinear'] }),
    f('visible', 'bool', true),
  ],
} as unknown as ManifestElement

/** 与 engine/manifest.py `_colorbar_fields` 同形 */
const colorbarEl = (over: { mappable?: boolean; orientation?: string } = {}): ManifestElement =>
  ({
    gid: 'axes_1.colorbar',
    role: 'colorbar',
    label: '色条',
    bbox: [0.8, 0.11, 0.04, 0.77],
    draggable: false,
    colorbar_key: 'cb:axes_0.images_0',
    host_gid: 'axes_0',
    ...(over.mappable === false ? {} : { mappable_gid: 'axes_0.images_0' }),
    editable: [
      f('label', 'text', 'Intensity (a.u.)'),
      f('orientation', 'enum', over.orientation ?? 'vertical', {
        options: ['vertical', 'horizontal'],
      }),
      f('extend', 'enum', 'neither', { options: ['neither', 'min', 'max', 'both'] }),
      f('cmap', 'enum', 'viridis', { options: CMAPS, group: '颜色映射' }),
      f('vmin', 'number', 0, { step: 0.35, group: '颜色映射' }),
      f('vmax', 'number', 35, { step: 0.35, group: '颜色映射' }),
      f('tick_fontsize', 'number', 10, { min: 3, max: 24, step: 0.5, unit: 'pt', group: '刻度' }),
      f('tick_color', 'color', '#000000', { group: '刻度' }),
      f('visible', 'bool', true),
    ],
  }) as unknown as ManifestElement

const manifestOf = (cb: ManifestElement = colorbarEl()): Manifest =>
  ({
    rev: 1,
    size_mm: [101.6, 76.2],
    elements: [axesEl, imageEl, cb],
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

async function mount(
  gid: string,
  opts: { overrides?: PanelObject['overrides']; manifest?: Manifest } = {},
) {
  const { overrides = [], manifest = manifestOf() } = opts
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_color_scale')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(overrides))
  })
  const panel = panelOf(overrides)
  const key = renderKeyOf(panel)
  useRenderStore.getState().patch(key, {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    // 几何权威要求 lastPatches 与当前 overrides 逐字相等（ADR 0017）
    lastPatches: JSON.stringify(overrides.map((o) => [o.gid, o.prop, o.value])),
    status: 'ready',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': key } })
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

const buttons = () => Array.from(host.querySelectorAll('button'))
const byAria = (name: string) => buttons().find((b) => b.getAttribute('aria-label') === name)
const byText = (text: string) => buttons().find((b) => b.textContent?.trim() === text)
const input = (prop: string) =>
  host.querySelector(`[data-prop="${prop}"] input`) as HTMLInputElement | null
const rowOf = (prop: string) => host.querySelector(`[data-prop="${prop}"]`) as HTMLElement | null
const livePanel = () => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p as PanelObject
}
const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value
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

/* ------------------------------ 共用的那份色阶 ----------------------------- */

describe('色阶共用关系（审计 T22 / T23）', () => {
  it('两个方向都认得出对家，判据是 mappable_gid', () => {
    const m = manifestOf()
    expect(colorScalePartner(m, colorbarEl())?.gid).toBe('axes_0.images_0')
    expect(colorScalePartner(m, imageEl)?.gid).toBe('axes_1.colorbar')
  })

  it('引擎没给 mappable_gid 时不摆一个指向空处的链接', () => {
    const cb = colorbarEl({ mappable: false })
    const m = manifestOf(cb)
    expect(colorScalePartner(m, cb)).toBeNull()
    expect(colorScalePartner(m, imageEl)).toBeNull()
  })

  it('图像页写出「与色条共用色阶」，点入口选中色条', async () => {
    await mount('axes_0.images_0')
    const link = host.querySelector('[data-color-scale-link]')
    expect(link?.getAttribute('data-color-scale-link')).toBe('axes_1.colorbar')
    expect(link?.textContent).toContain('与色条共用色阶')
    await click(byAria('选中色条'))
    expect(useUiStore.getState().selectedGids).toEqual(['axes_1.colorbar'])
  })

  it('没有对家时这一行整个不出现', async () => {
    const cb = colorbarEl({ mappable: false })
    await mount('axes_0.images_0', { manifest: manifestOf(cb) })
    expect(host.querySelector('[data-color-scale-link]')).toBeNull()
  })
})

/* ------------------------------ 色阶上下限并排 ----------------------------- */

describe('色阶上下限并排（审计 T22）', () => {
  it('两条在同一行，各写各的 override', async () => {
    await mount('axes_0.images_0')
    // 「同一行」认那个并排行的锚点：**不能拿「父节点相同」当判据**——
    // 分开画的两行也共用同一个列表容器，那条断言恒真（第一版踩过）
    const pair = host.querySelector('[data-pair-row="vmin|vmax"]') as HTMLElement | null
    expect(pair).not.toBeNull()
    expect(Array.from(pair!.querySelectorAll('[data-prop]')).map((n) => n.getAttribute('data-prop'))).toEqual([
      'vmin',
      'vmax',
    ])
    expect(pair!.textContent).toContain('色阶范围')
    // 两个数字框各有自己的无障碍名（图形之外必须有文字名）
    expect(input('vmin')!.getAttribute('aria-label')).toBe('色阶下限')
    expect(input('vmax')!.getAttribute('aria-label')).toBe('色阶上限')

    await typeInto(input('vmin')!, '5')
    expect(overrideOf('axes_0.images_0', 'vmin')).toBe(5)
    expect(overrideOf('axes_0.images_0', 'vmax')).toBeUndefined()
    // **两格都要写对自己那条**：只测第一格的话，「两格写同一个 prop」这种
    // 实现照样绿（第二格的值会落进第一条，而第一条本来就该有值）
    await typeInto(input('vmax')!, '40')
    expect(overrideOf('axes_0.images_0', 'vmax')).toBe(40)
    expect(overrideOf('axes_0.images_0', 'vmin')).toBe(5)
  })

  it('只改了一条时，恢复按钮只出现在那一条上', async () => {
    await mount('axes_0.images_0', {
      overrides: [{ gid: 'axes_0.images_0', prop: 'vmax', value: 20 }],
    })
    expect(rowOf('vmin')!.querySelector('button')).toBeNull()
    expect(rowOf('vmax')!.querySelector('button')).not.toBeNull()
  })
})

/* --------------------------------- 透明度 --------------------------------- */

describe('透明度按百分比（审计 T22，与 T16 / T20 共用控件）', () => {
  it('显示 100%，输入 75 写回 0.75', async () => {
    await mount('axes_0.images_0')
    const alpha = input('alpha')!
    expect(alpha.value).toBe('100')
    await typeInto(alpha, '75')
    expect(overrideOf('axes_0.images_0', 'alpha')).toBe(0.75)
  })
})

/* -------------------------------- 尺寸区 ---------------------------------- */

describe('图像的尺寸区（审计 T22）', () => {
  it('两段常驻说明换成一个组标题 + 来源入口', async () => {
    await mount('axes_0.images_0')
    // 原来的两句话都不再常驻
    expect(host.textContent).not.toContain('位置和大小属于宿主子图')
    expect(host.textContent).not.toContain('位置与大小作用于宿主子图')
    // 作用对象写在组标题上
    expect(host.textContent).toContain('子图尺寸 · 子图 1')
    // 来源入口点得动：选中那个子图
    await click(byAria('选中子图 1'))
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0'])
  })

  it('选中子图本身时没有这个组标题（它就是自己的尺寸）', async () => {
    await mount('axes_0')
    expect(host.textContent).not.toContain('子图尺寸 · ')
    expect(byText('选中')).toBeUndefined()
  })
})

/* -------------------------------- 颜色条 ---------------------------------- */

describe('颜色条（审计 T23）', () => {
  it('名称在主区直接可编辑，不再收在「更多」里', async () => {
    await mount('axes_1.colorbar')
    const nameRow = rowOf('label')
    expect(nameRow).not.toBeNull()
    // 「更多」还折叠着的时候它就在——说明它在首屏，不是在折叠区里
    const more = buttons().find((b) => b.textContent?.trim() === '更多')
    expect(more?.getAttribute('aria-expanded')).toBe('false')
    const box = nameRow!.querySelector('textarea') as HTMLTextAreaElement
    expect(box.value).toBe('Intensity (a.u.)')
  })

  it('方向是一组带小色条预览的选项，不是文字下拉；点一下写 matplotlib 的值', async () => {
    await mount('axes_1.colorbar')
    const row = rowOf('orientation')!
    expect(row.querySelector('select')).toBeNull()
    const group = row.querySelector('[role="radiogroup"]')
    expect(group).not.toBeNull()
    const cells = Array.from(group!.querySelectorAll('[role="radio"]'))
    expect(cells.map((c) => c.getAttribute('data-value'))).toEqual(['vertical', 'horizontal'])
    // 图形之外必须有文字名（无障碍名 + 可见的档位名）
    expect(cells.map((c) => c.getAttribute('aria-label'))).toEqual(['竖直', '水平'])
    // 预览用的是当前色图
    const preview = cells[0].querySelector('[data-cb-preview]') as HTMLElement
    expect(preview.getAttribute('data-cb-preview')).toBe('vertical:neither')
    // jsdom 把 hex 规范成 rgb()；viridis 的第一个停靠点是 #440154
    expect(preview.style.background).toContain('rgb(68, 1, 84)')
    expect(preview.style.background).toContain('to top')

    await click(cells[1])
    expect(overrideOf('axes_1.colorbar', 'orientation')).toBe('horizontal')
  })

  it('两端延伸的预览跟着色条此刻的方向画', async () => {
    await mount('axes_1.colorbar', { manifest: manifestOf(colorbarEl({ orientation: 'horizontal' })) })
    const cells = Array.from(
      rowOf('extend')!.querySelectorAll('[role="radio"]'),
    ) as HTMLElement[]
    expect(cells.map((c) => c.querySelector('[data-cb-preview]')?.getAttribute('data-cb-preview'))).toEqual([
      'horizontal:neither',
      'horizontal:min',
      'horizontal:max',
      'horizontal:both',
    ])
    await click(cells[3])
    expect(overrideOf('axes_1.colorbar', 'extend')).toBe('both')
  })

  it('多宿主色条不宣称方向：延伸预览从 bbox 反推，不摆一个方向控件', async () => {
    // 引擎的 guard（issue #69）：`orientation` 整条不发
    const cb = colorbarEl()
    const noOrientation = {
      ...cb,
      editable: cb.editable.filter((x) => x.prop !== 'orientation'),
    } as ManifestElement
    await mount('axes_1.colorbar', { manifest: manifestOf(noOrientation) })
    expect(rowOf('orientation')).toBeNull()
    const first = rowOf('extend')!.querySelector('[data-cb-preview]')
    // bbox 是 0.04 宽 × 0.77 高 → 竖直
    expect(first?.getAttribute('data-cb-preview')).toBe('vertical:neither')
  })

  it('色条页也写出与图像共用色阶，并且上下限并排', async () => {
    await mount('axes_1.colorbar')
    expect(host.querySelector('[data-color-scale-link]')?.getAttribute('data-color-scale-link')).toBe(
      'axes_0.images_0',
    )
    expect(host.querySelector('[data-color-scale-link]')?.textContent).toContain('与图像 1共用色阶')
    expect(host.querySelector('[data-pair-row="vmin|vmax"]')).not.toBeNull()
    await click(byAria('选中图像 1'))
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.images_0'])
  })
})

/* ------------------------------ 脚本自定义的色图 ---------------------------- */

const CUSTOM_STOPS = ['#256fa8', '#ebeef1', '#cf6a2c']
const CUSTOM_FACTS = { name: 'from_list', custom: true, stops: CUSTOM_STOPS, discrete: true }

/** 图 A 那种 `imshow(..., cmap=ListedColormap([...]))`：名字 `from_list`、不在可写选项里 */
const customImage = (over: Partial<EditableField> = {}): ManifestElement =>
  ({
    ...imageEl,
    editable: imageEl.editable.map((x) =>
      x.prop === 'cmap'
        ? f('cmap', 'enum', 'from_list', { options: CMAPS, group: '颜色映射', cmap_current: CUSTOM_FACTS, ...over })
        : x,
    ),
  }) as ManifestElement

/** 换成 viridis 之后引擎发来的样子：值是 viridis，原样是那张自定义的 */
const switchedImage = (): ManifestElement =>
  ({
    ...imageEl,
    editable: imageEl.editable.map((x) =>
      x.prop === 'cmap'
        ? f('cmap', 'enum', 'viridis', {
            options: CMAPS,
            group: '颜色映射',
            cmap_original: CUSTOM_FACTS,
          })
        : x,
    ),
  }) as ManifestElement

const customColorbar = (): ManifestElement => {
  const cb = colorbarEl()
  return {
    ...cb,
    editable: cb.editable.map((x) =>
      x.prop === 'cmap'
        ? f('cmap', 'enum', 'from_list', { options: CMAPS, group: '颜色映射', cmap_current: CUSTOM_FACTS })
        : x,
    ),
  } as ManifestElement
}

const withElements = (...els: ManifestElement[]): Manifest =>
  ({ rev: 1, size_mm: [101.6, 76.2], elements: [axesEl, ...els] }) as unknown as Manifest

const cmapTrigger = () => rowOf('cmap')!.querySelector('button[aria-label="色图"]') as HTMLButtonElement
const cmapEntries = () =>
  Array.from(document.querySelectorAll('[role="radiogroup"][aria-label="色图"] [role="radio"]')) as HTMLElement[]

describe('脚本自定义的色图（2026-09-13 用户反馈：图 A 显示 from_list）', () => {
  it('触发按钮说「自定义」而不是 from_list，渐变条按引擎给的三格硬边画', async () => {
    await mount('axes_0.images_0', { manifest: withElements(customImage(), colorbarEl()) })
    const trigger = cmapTrigger()
    expect(trigger.textContent).toContain('自定义')
    expect(trigger.textContent).not.toContain('from_list')
    expect(trigger.getAttribute('data-cmap-custom')).toBe('true')
    // 原文留在悬停提示里，用户要查脚本时认得出
    expect(trigger.querySelector('[title="from_list"]')).not.toBeNull()
    const bar = trigger.querySelector('[data-cmap-gradient]') as HTMLElement
    expect(bar.getAttribute('data-cmap-gradient')).toBe('discrete')
    // jsdom 把 hex 规范成 rgb()；三格色块 = 每个颜色出现两次（起止各一次）
    expect(bar.style.background).toContain('rgb(37, 111, 168)')
    expect(bar.style.background.split('rgb(37, 111, 168)').length - 1).toBe(2)
  })

  it('列表里当前那一格是「自定义」且选中；点它**不写 override**（from_list 写不进去）', async () => {
    await mount('axes_0.images_0', { manifest: withElements(customImage(), colorbarEl()) })
    await click(cmapTrigger())
    const entries = cmapEntries()
    expect(entries[0].getAttribute('data-cmap-entry')).toBe('keep')
    expect(entries[0].getAttribute('aria-checked')).toBe('true')
    expect(entries[0].textContent).toContain('自定义')
    expect(entries[0].getAttribute('aria-label')).toContain('from_list')
    // 后面才是白名单，一个 from_list 都没有
    expect(entries.slice(1).map((e) => e.textContent?.trim())).toEqual(CMAPS)
    await click(entries[0])
    expect(overrideOf('axes_0.images_0', 'cmap')).toBeUndefined()
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('自定义色图顶着白名单里的名字（`ListedColormap([...], name="viridis")`）：自定义与否只认引擎的 `custom`，不拿名字判', async () => {
    // 引擎：value 是 viridis、viridis 也在选项表里，但事实说它是自定义的三格
    const facts = { ...CUSTOM_FACTS, name: 'viridis' }
    await mount('axes_0.images_0', {
      manifest: withElements(customImage({ value: 'viridis', cmap_current: facts } as Partial<EditableField>), colorbarEl()),
    })
    const trigger = cmapTrigger()
    expect(trigger.textContent).toContain('自定义')
    expect(trigger.getAttribute('data-cmap-custom')).toBe('true')
    expect(trigger.querySelector('[data-cmap-gradient]')?.getAttribute('data-cmap-gradient')).toBe('discrete')
    await click(trigger)
    const entries = cmapEntries()
    // 顶上「自定义」那一格选中、点它不写；同名的 viridis 一格照常列着但**不选中**，
    // 渐变条是真 viridis——选它 = 换成注册表里真正的那张
    expect(entries[0].getAttribute('data-cmap-entry')).toBe('keep')
    expect(entries[0].getAttribute('aria-checked')).toBe('true')
    expect(entries[0].textContent).toContain('自定义')
    expect(entries.slice(1).map((e) => e.textContent?.trim())).toEqual(CMAPS)
    const viridis = entries[1]
    expect(viridis.getAttribute('data-cmap-entry')).toBe('write')
    expect(viridis.getAttribute('aria-checked')).toBe('false')
    expect(viridis.textContent).not.toContain('自定义')
    expect(viridis.querySelector('[data-cmap-gradient]')?.getAttribute('data-cmap-gradient')).toBe('smooth')
    await click(entries[0])
    expect(overrideOf('axes_0.images_0', 'cmap')).toBeUndefined()
    expect(engineRender).not.toHaveBeenCalled()
    await click(cmapTrigger())
    await click(cmapEntries()[1])
    expect(overrideOf('axes_0.images_0', 'cmap')).toBe('viridis')
  })

  it('换走之后多一格「脚本原样」，选它清掉图像与色条两边的 override', async () => {
    await mount('axes_0.images_0', {
      manifest: withElements(switchedImage(), colorbarEl()),
      overrides: [
        { gid: 'axes_0.images_0', prop: 'cmap', value: 'viridis' },
        // 用户之前也从色条那边改过一次：两条 override 指同一份色图状态
        { gid: 'axes_1.colorbar', prop: 'cmap', value: 'viridis' },
        // 无关的 override 不许被顺手清掉
        { gid: 'axes_0.images_0', prop: 'vmax', value: 20 },
      ],
    })
    expect(cmapTrigger().textContent).toContain('viridis')
    await click(cmapTrigger())
    const entries = cmapEntries()
    expect(entries[0].getAttribute('data-cmap-entry')).toBe('restore')
    expect(entries[0].getAttribute('aria-checked')).toBe('false')
    expect(entries[0].textContent).toContain('自定义')
    expect(entries[0].textContent).toContain('脚本原样')
    const bar = entries[0].querySelector('[data-cmap-gradient]') as HTMLElement
    expect(bar.getAttribute('data-cmap-gradient')).toBe('discrete')
    expect(bar.style.background).toContain('rgb(207, 106, 44)')
    await click(entries[0])
    expect(overrideOf('axes_0.images_0', 'cmap')).toBeUndefined()
    expect(overrideOf('axes_1.colorbar', 'cmap')).toBeUndefined()
    expect(overrideOf('axes_0.images_0', 'vmax')).toBe(20)
  })

  it('事实只对得上名字才作数：value 已经是 viridis、事实还是上一张的，不把 viridis 叫成「自定义」', async () => {
    // 渲染还没回来的那一拍：字段的 value 已被 override 换成 viridis，manifest 里的
    // `cmap_current` 仍描述 from_list
    await mount('axes_0.images_0', {
      manifest: withElements(customImage({ value: 'viridis' } as Partial<EditableField>), colorbarEl()),
    })
    const trigger = cmapTrigger()
    expect(trigger.textContent).toContain('viridis')
    expect(trigger.textContent).not.toContain('自定义')
    expect(trigger.getAttribute('data-cmap-custom')).toBeNull()
    const bar = trigger.querySelector('[data-cmap-gradient]') as HTMLElement
    expect(bar.getAttribute('data-cmap-gradient')).toBe('smooth')
    // viridis 的第一个停靠点 #440154，不是自定义那张的 #256fa8
    expect(bar.style.background).toContain('rgb(68, 1, 84)')
    expect(bar.style.background).not.toContain('rgb(37, 111, 168)')
  })

  it('没有原样事实（还没换走 / 老引擎）时不出「脚本原样」那一格', async () => {
    await mount('axes_0.images_0')
    await click(cmapTrigger())
    expect(cmapEntries().map((e) => e.getAttribute('data-cmap-entry'))).not.toContain('restore')
  })

  it('色条页的方向 / 延伸预览用自定义色图的色标上色，不再退回灰阶', async () => {
    await mount('axes_1.colorbar', { manifest: withElements(customImage(), customColorbar()) })
    const preview = rowOf('orientation')!.querySelector('[data-cb-preview]') as HTMLElement
    expect(preview.style.background).toContain('rgb(37, 111, 168)')
    expect(preview.style.background).not.toContain('rgb(217, 217, 217)')
    const extend = rowOf('extend')!.querySelector('[data-cb-preview]') as HTMLElement
    expect(extend.style.background).toContain('rgb(207, 106, 44)')
  })
})
